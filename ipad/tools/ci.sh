#!/usr/bin/env bash
# CI steps of the iPad app. Run from anywhere: ipad/tools/ci.sh <step>
#   setup | fixtures | strings-check | generate | build | test-package <branch> | test |
#   desktop-check | desktop-roundtrip | screenshots | summary | ipa
# Steps whose tools or tests other packages have not written yet are skipped with a warning,
# unless ZENITH_STRICT=1 (the release build), where a missing piece fails the step.
# Written for the macOS /bin/bash 3.2: no associative arrays, no empty array expansion under set -u.
set -euo pipefail
IPAD="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$IPAD/.." && pwd)"
BUILD="$IPAD/build"
BUNDLE_ID="io.github.andrealo20.zenithpdf"
XCODEGEN_VERSION="2.46.0"
VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$REPO/pyproject.toml" | head -1)"
BUILD_NUMBER="${GITHUB_RUN_NUMBER:-1}"
STRICT="${ZENITH_STRICT:-0}"
if [ -d /Applications/Xcode_26.6.app ]; then
  export DEVELOPER_DIR=/Applications/Xcode_26.6.app/Contents/Developer
fi
VERSIONS=(MARKETING_VERSION="$VERSION" CURRENT_PROJECT_VERSION="$BUILD_NUMBER")
PROJECT=(-project "$IPAD/ZenithPDF.xcodeproj" -scheme ZenithPDF)
export QT_QPA_PLATFORM=offscreen

# missing <what>: a warning and false, or with ZENITH_STRICT=1 an error that ends the step
missing() {
  if [ "$STRICT" = 1 ]; then echo "::error::missing: $1"; exit 1; fi
  echo "::warning::skipped, not written yet: $1"
  return 1
}
need() { [ -e "$1" ] || missing "$2 (${1#"$REPO"/})"; }   # need <path> <what>, used as: need ... || return 0

setup() {          # the XcodeGen release binary, pinned, and the desktop with its own dependencies
  local tmp="${RUNNER_TEMP:-$BUILD/tmp}"
  mkdir -p "$tmp"
  curl -fsSL -o "$tmp/xcodegen.zip" \
    "https://github.com/yonaskolb/XcodeGen/releases/download/$XCODEGEN_VERSION/xcodegen.zip"
  unzip -q -o "$tmp/xcodegen.zip" -d "$tmp"
  echo "$tmp/xcodegen/bin" >> "${GITHUB_PATH:-/dev/null}"
  python3 -m pip install --quiet -e "$REPO"
}
fixtures() {       # written by the desktop code of this very commit
  need "$IPAD/tools/make_fixtures.py" "the fixture writer" || return 0
  mkdir -p "$IPAD/ZenithPDF/Resources/Demo" "$IPAD/ZenithPDFTests/Fixtures"
  python3 "$IPAD/tools/make_fixtures.py" "$IPAD/ZenithPDF/Resources/Demo/desktop-ink.pdf" \
    "$IPAD/ZenithPDFTests/Fixtures/desktop-ink.json"
}
strings_check() {
  need "$IPAD/tools/make_strings.py" "the String Catalog writer" || return 0
  python3 "$IPAD/tools/make_strings.py" --check
}
generate() {
  if need "$IPAD/tools/make_strings.py" "the String Catalog writer"; then
    python3 "$IPAD/tools/make_strings.py"
  fi
  (cd "$IPAD" && xcodegen generate --spec project.yml)
}
simulator() { python3 "$IPAD/tools/pick_simulator.py"; }   # prints one UDID
boot() {
  xcrun simctl boot "$1" 2>/dev/null || true
  xcrun simctl bootstatus "$1" -b
}
build_step() {
  local udid; udid="$(simulator)"
  echo "simulator: $udid"
  xcodebuild build-for-testing "${PROJECT[@]}" -destination "platform=iOS Simulator,id=$udid" \
    -derivedDataPath "$BUILD/sim" CODE_SIGNING_ALLOWED=NO "${VERSIONS[@]}"
}
# run_tests <result name> [-only-testing:X ...]: records the screen while the tests run
run_tests() {
  local name="$1" udid rec status=0; shift
  udid="$(simulator)"; boot "$udid"
  mkdir -p "$BUILD/exports" "$BUILD/screenshots"
  rm -rf "$BUILD/$name.xcresult"
  xcrun simctl io "$udid" recordVideo --codec h264 --force "$BUILD/screenshots/$name.mp4" &
  rec=$!
  TEST_RUNNER_ZENITH_EXPORT_DIR="$BUILD/exports" \
  xcodebuild test-without-building "${PROJECT[@]}" -destination "platform=iOS Simulator,id=$udid" \
    -derivedDataPath "$BUILD/sim" -resultBundlePath "$BUILD/$name.xcresult" "$@" || status=$?
  kill -INT "$rec" 2>/dev/null || true
  wait "$rec" 2>/dev/null || true
  return "$status"
}
test_package() {   # the test classes of the package a branch named wp<N>-... belongs to
  local branch="${1:-}" classes="" c
  case "$branch" in
    wp1-*) classes="ZenithPDFTests/CoreTests" ;;
    wp2-*) classes="ZenithPDFTests/FormatTests ZenithPDFTests/DesktopCompatibilityTests" ;;
    wp3-*) classes="ZenithPDFTests/StorageTests" ;;
    wp4-*) classes="ZenithPDFTests/TemplateTests ZenithPDFTests/PageOperationTests" ;;
    wp5-*) classes="ZenithPDFTests/EditorTests" ;;
    wp6-*) classes="ZenithPDFTests/ContentTests" ;;
    wp7-*) classes="ZenithPDFTests/ShapeTests" ;;
    wp8-*) classes="ZenithPDFTests/AudioTests" ;;
    wp9-*) classes="ZenithPDFTests/SearchTests" ;;
    wp10-*) classes="ZenithPDFTests/LibraryModelTests" ;;
    wp11-*) classes="ZenithPDFTests/AppTests ZenithPDFUITests" ;;
    wp12-*) classes="ZenithPDFTests/LocalizationTests" ;;
    *) echo "no package tests for branch '$branch'"; return 0 ;;
  esac
  set --
  for c in $classes; do
    # a class lives in <target>/<Class>.swift; a whole target is a folder
    if [ -f "$IPAD/$c.swift" ] || { [ "${c#*/}" = "$c" ] && [ -d "$IPAD/$c" ]; }; then
      set -- "$@" -only-testing:"$c"
    else
      echo "::warning::$c is not written yet"
    fi
  done
  if [ "$#" -eq 0 ]; then echo "no package tests written yet for '$branch'"; return 0; fi
  run_tests Package "$@"
}
test_step() { run_tests Tests; }
desktop_check() {  # the desktop reads what the iPad wrote, renders it, then edits it for the round trip
  need "$IPAD/tools/check_desktop_reads.py" "the desktop reader check" || return 0
  mkdir -p "$BUILD/exports" "$BUILD/screenshots"
  python3 "$IPAD/tools/check_desktop_reads.py" "$BUILD/exports" --png "$BUILD/screenshots"
  need "$IPAD/tools/desktop_edit.py" "the desktop edit script" || return 0
  need "$BUILD/exports/ipad-ink.pdf" "the iPad export of testExportForDesktop" || return 0
  python3 "$IPAD/tools/desktop_edit.py" "$BUILD/exports/ipad-ink.pdf" \
    "$BUILD/exports/ipad-ink-desktop.pdf" "$BUILD/exports/ipad-ink-desktop.json"
}
desktop_roundtrip() {
  need "$BUILD/exports/ipad-ink-desktop.pdf" "the desktop edit of the iPad note" || return 0
  grep -q "func testReadsDesktopEdits" "$IPAD/ZenithPDFTests/DesktopCompatibilityTests.swift" 2>/dev/null ||
    missing "DesktopCompatibilityTests.testReadsDesktopEdits" || return 0
  run_tests Roundtrip -only-testing:ZenithPDFTests/DesktopCompatibilityTests/testReadsDesktopEdits
}
container() { xcrun simctl get_app_container "$1" "$BUNDLE_ID" data 2>/dev/null || true; }
WAIT=60
wait_ready() {     # wait_ready <udid> <name>: up to $WAIT s for Library/Caches/ready-<name>
  local udid="$1" name="$2" data i
  for i in $(seq 1 "$WAIT"); do
    data="$(container "$udid")"
    if [ -n "$data" ] && [ -f "$data/Library/Caches/ready-$name" ]; then sleep 1; return 0; fi
    sleep 1
  done
  xcrun simctl spawn "$udid" launchctl list | grep -q "$BUNDLE_ID" || echo "::warning::the app is not running"
  # One scene without its marker usually means the app has none yet: do not wait a full minute per scene.
  WAIT=5
  return 1
}
shoot() {          # shoot <udid> <name> <file> [launch arguments...]: false when the scene never got ready
  local udid="$1" name="$2" file="$3" data ready=0; shift 3
  data="$(container "$udid")"
  if [ -n "$data" ]; then rm -f "$data/Library/Caches/ready-$name"; fi
  xcrun simctl launch --terminate-running-process "$udid" "$BUNDLE_ID" "$@"
  wait_ready "$udid" "$name" || ready=1
  xcrun simctl io "$udid" screenshot "$file"
  return "$ready"
}
screenshots() {
  local udid app shots scene look logger entry missed=""
  udid="$(simulator)"; app="$BUILD/sim/Build/Products/Debug-iphonesimulator/ZenithPDF.app"
  shots="$BUILD/screenshots"; mkdir -p "$shots"
  boot "$udid"
  xcrun simctl status_bar "$udid" override --time "9:41" --batteryState charged --batteryLevel 100 || true
  xcrun simctl spawn "$udid" log stream --level debug --predicate "subsystem == \"$BUNDLE_ID\"" > "$shots/app.log" 2>&1 &
  logger=$!
  xcrun simctl uninstall "$udid" "$BUNDLE_ID" 2>/dev/null || true
  xcrun simctl install "$udid" "$app"
  xcrun simctl ui "$udid" appearance light
  shoot "$udid" launch "$shots/firstLaunch-light.png" || missed="$missed launch"   # a fresh install, no arguments
  for entry in library:light library:dark note:light note:dark darkPaper:light zoomed:light pages:light \
               templates:light search:light noteSearch:light trash:light audio:light settings:dark desktopNote:light; do
    scene="${entry%%:*}"; look="${entry##*:}"
    xcrun simctl ui "$udid" appearance "$look"
    shoot "$udid" "$scene" "$shots/$scene-$look.png" -ZenithDemo "$scene" || missed="$missed $scene"
  done
  kill "$logger" 2>/dev/null || true
  cp ~/Library/Logs/DiagnosticReports/ZenithPDF* "$shots/" 2>/dev/null || true
  if [ -n "$missed" ]; then
    if [ "$STRICT" = 1 ]; then echo "::error::no ready marker for:$missed"; return 1; fi
    echo "::warning::no ready marker for:$missed (screenshots taken anyway)"
  fi
}
summary() {        # failing tests as plain text, readable without Xcode
  local result out="$BUILD/test-summary.txt"
  mkdir -p "$BUILD/attachments"
  : > "$out"
  for result in "$BUILD"/*.xcresult; do
    [ -d "$result" ] || continue
    echo "## $(basename "$result" .xcresult)" >> "$out"
    xcrun xcresulttool get test-results tests --path "$result" > "${result%.xcresult}.json" || true
    python3 "$IPAD/tools/test_summary.py" "${result%.xcresult}.json" >> "$out" || true
    xcrun xcresulttool export attachments --path "$result" \
      --output-path "$BUILD/attachments/$(basename "$result" .xcresult)" || true
  done
  [ -s "$out" ] || echo "no test results" > "$out"
  cp ~/Library/Logs/DiagnosticReports/ZenithPDF* "$BUILD/attachments/" 2>/dev/null || true
  cat "$out"
  cat "$out" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
}
ipa() {
  rm -rf "$BUILD/device" "$BUILD/Payload" "$BUILD/Zenith-PDF-iPad.ipa"
  xcodebuild build "${PROJECT[@]}" -configuration Release \
    -sdk iphoneos -destination "generic/platform=iOS" -derivedDataPath "$BUILD/device" \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" "${VERSIONS[@]}"
  mkdir -p "$BUILD/Payload"
  cp -R "$BUILD/device/Build/Products/Release-iphoneos/ZenithPDF.app" "$BUILD/Payload/"
  (cd "$BUILD" && zip -qry Zenith-PDF-iPad.ipa Payload)
  test -s "$BUILD/Zenith-PDF-iPad.ipa"
}
case "${1:-}" in
  setup) setup ;; fixtures) fixtures ;; strings-check) strings_check ;; generate) generate ;;
  build) build_step ;; test-package) test_package "${2:-}" ;; test) test_step ;;
  desktop-check) desktop_check ;; desktop-roundtrip) desktop_roundtrip ;;
  screenshots) screenshots ;; summary) summary ;; ipa) ipa ;;
  *) echo "usage: ci.sh setup|fixtures|strings-check|generate|build|test-package <branch>|test|desktop-check|desktop-roundtrip|screenshots|summary|ipa" >&2; exit 2 ;;
esac
