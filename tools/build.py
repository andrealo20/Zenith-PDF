"""Build the release of this system with PyInstaller, into dist/release/.

    pip install pyinstaller
    python tools/build.py

- Windows: Zenith-PDF-Setup.exe, an installer made with NSIS (makensis on the PATH,
  or in Program Files). It asks for the folder and a desktop shortcut, adds Zenith PDF
  to the Start menu, to "Open with" for PDF files and to Installed apps.
- macOS: Zenith-PDF-macOS.dmg, with Zenith PDF.app to drag into Applications.
- Linux: Zenith-PDF-Linux-x86_64.AppImage, one file to make executable and run
  (appimagetool on the PATH, or APPIMAGETOOL naming it).

    python tools/build.py --store

builds Zenith-PDF.msix instead, the package to upload to the Microsoft Store, which
signs it. It needs makeappx and makepri from the Windows SDK. To try it before
uploading, with Windows' Developer Mode on: Add-AppxPackage -Register
build/msix/AppxManifest.xml.

Signing on Windows: with WINDOWS_CERTIFICATE (a .pfx file) and
WINDOWS_CERTIFICATE_PASSWORD set, the app, the installer and its uninstaller are
signed with signtool and time-stamped. Without them the files are left unsigned.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # make_icon, also when this file is loaded by path

from zenith_pdf import __version__  # noqa: E402

ASSETS = ROOT / "src" / "zenith_pdf" / "assets"
LOCALES = ROOT / "src" / "zenith_pdf" / "locales"
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = DIST / "release"
NAME = "Zenith-PDF"  # the program's file name
TITLE = "Zenith PDF"  # what people see
BUNDLE_ID = "io.github.andrealo20.zenithpdf"
TIMESTAMP = "http://timestamp.digicert.com"
# The Store package's identity, from Partner Center > Product identity (public: every copy of the package has it).
STORE_IDENTITY = {
    "name": os.environ.get("STORE_IDENTITY_NAME", "andrealo.ZenithPDF"),
    "publisher": os.environ.get("STORE_PUBLISHER", "CN=6324E174-874E-4AF4-BEFF-97AD455C0AFD"),
    "publisher_name": os.environ.get("STORE_PUBLISHER_NAME", "andrealo"),
}
# The languages the Store package says it speaks, as Windows names them: the app's own (core.i18n.LANGUAGES).
STORE_LANGUAGES = {"en": "en-US", "it": "it-IT", "fr": "fr-FR", "es": "es-ES", "de": "de-DE", "pt": "pt-BR",
                   "nl": "nl-NL", "pl": "pl-PL", "cs": "cs-CZ", "tr": "tr-TR", "ru": "ru-RU", "uk": "uk-UA",
                   "zh": "zh-CN", "ja": "ja-JP", "ko": "ko-KR"}
# The Store package's logos: name, size in pixels, share of it the logo takes. The unplated ones are the taskbar's
# and the Start menu's, drawn without a coloured square behind.
STORE_LOGOS = [("StoreLogo.scale-100.png", 50, 1.0), ("StoreLogo.scale-200.png", 100, 1.0),
               ("Square150x150Logo.scale-100.png", 150, 0.66), ("Square150x150Logo.scale-200.png", 300, 0.66),
               ("Square44x44Logo.scale-100.png", 44, 1.0), ("Square44x44Logo.scale-200.png", 88, 1.0)] + [
    (f"Square44x44Logo.targetsize-{size}{plate}.png", size, 1.0)
    for size in (16, 24, 32, 48, 256) for plate in ("", "_altform-unplated")]
LANGUAGES = ("en", "it", "fr", "es", "de", "pt")  # Qt's own translations kept: those the app speaks
# Python modules nothing in Zenith PDF uses, which PyInstaller would otherwise follow from PySide6 and Pillow.
UNUSED = ("tkinter", "PySide6.QtDataVisualization", "PySide6.QtCharts", "PySide6.QtGraphs", "PySide6.QtQml",
          "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
          "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtSql",
          "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.Qt3DCore",
          "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
          "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
          "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtWebSockets", "PIL._avif",
          "PIL.AvifImagePlugin")
# Qt files left behind by plugins Zenith PDF never loads: the virtual keyboard (which brings Qt Quick), the PDF
# image format (Qt's own PDF module), the TLS backends (Python does HTTPS itself), network information.
PRUNE = (r"platforminputcontexts/.*virtualkeyboard", r"imageformats/.*qpdf", r"plugins/tls/",
         r"plugins/networkinformation/", r"Qt6?VirtualKeyboard", r"Qt6?Qml", r"Qt6?Quick", r"Qt6?Pdf",
         r"Qt6?DataVisualization")
# On Windows sound goes through winmm and the camera through the Camera app: no FFmpeg, no software OpenGL.
PRUNE_WINDOWS = (r"opengl32sw\.dll", r"ffmpegmediaplugin", r"/(avcodec|avformat|avutil|swresample|swscale)-")


def run(command: list[str], **options) -> None:
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def pyinstaller(name: str, extra: list[str]) -> Path:
    """The app as a folder (one-folder mode starts faster than one file), in dist/."""
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", name,
        "--paths", ROOT / "src",
        "--add-data", f"{ASSETS}{os.pathsep}zenith_pdf/assets",
        "--add-data", f"{LOCALES}{os.pathsep}zenith_pdf/locales",
        "--collect-all", "pypdfium2",
        "--collect-all", "pypdfium2_raw",
        "--collect-data", "qtawesome",
        *(part for module in UNUSED for part in ("--exclude-module", module)),
        "--specpath", BUILD,
        "--workpath", BUILD / "pyinstaller",
        "--distpath", DIST,
        *extra,
        ROOT / "tools" / "launcher.py",
    ]
    run(command, cwd=ROOT)
    app = DIST / (f"{name}.app" if sys.platform == "darwin" else name)
    prune(app)
    return app


def prune(app: Path) -> None:
    """Delete what the app never loads (PRUNE), and Qt's translations into languages it doesn't speak."""
    patterns = [re.compile(p, re.IGNORECASE) for p in PRUNE + (PRUNE_WINDOWS if sys.platform == "win32" else ())]
    keep = re.compile(r"_(%s)(_[a-z]{2})?\.qm$" % "|".join(LANGUAGES), re.IGNORECASE)
    freed = 0
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() or not path.exists():
            continue
        text = path.as_posix()
        unwanted = any(p.search(text) for p in patterns) or (
            path.suffix == ".qm" and "/translations/" in text and not keep.search(path.name))
        if not unwanted:
            continue
        if path.is_dir():
            freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
            shutil.rmtree(path)
        else:
            freed += path.stat().st_size
            path.unlink()
    print(f"Pruned {freed / 1e6:.1f} MB")


def self_test(app: Path) -> None:
    """Start the built program in its test mode: every module imports, and Qt finds what it needs."""
    if sys.platform == "darwin":
        program = app / "Contents" / "MacOS" / app.stem
    else:
        program = app / (f"{NAME}.exe" if sys.platform == "win32" else NAME)
    log = BUILD / "self-test.log"
    log.unlink(missing_ok=True)
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "ZENITH_SELF_TEST_LOG": str(log)}
    done = subprocess.run([str(program), "--self-test"], env=environment, timeout=300)
    report = log.read_text(encoding="utf-8") if log.exists() else "(no report)"
    print(report)
    if done.returncode != 0:
        raise SystemExit(f"The built program failed its self-test ({done.returncode}).")


# Windows ----------------------------------------------------------------------------------


def version_info() -> Path:
    """The Windows file properties (Details tab) of the program."""
    numbers = tuple(int(part) for part in __version__.split(".")[:3]) + (0,)
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "version_info.txt"
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Andrea Loroni'),
      StringStruct('FileDescription', '{TITLE}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Andrea Loroni. GPL-3.0-or-later.'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{TITLE}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return path


def _found(names: list[str], places: list[Path]) -> Path | None:
    for name in names:
        if found := shutil.which(name):
            return Path(found)
    return next((place for place in places if place.is_file()), None)


def signtool() -> Path | None:
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    places = sorted(kits.glob("*/x64/signtool.exe"), reverse=True) if kits.is_dir() else []
    return _found(["signtool"], places)


def signing_script() -> Path | None:
    """A small script that signs the file it is given, when a certificate is set; None otherwise."""
    certificate, password = os.environ.get("WINDOWS_CERTIFICATE"), os.environ.get("WINDOWS_CERTIFICATE_PASSWORD")
    if not certificate:
        print("No WINDOWS_CERTIFICATE: the files stay unsigned.")
        return None
    tool = signtool()
    if tool is None:
        raise SystemExit("WINDOWS_CERTIFICATE is set, but signtool was not found (it comes with the Windows SDK).")
    script = BUILD / "sign.cmd"
    # The password stays in the environment: the script names the variable, never its value.
    script.write_text(f'@"{tool}" sign /fd sha256 /td sha256 /tr {TIMESTAMP} /d "{TITLE}" '
                      f'/f "{certificate}"{" /p %WINDOWS_CERTIFICATE_PASSWORD%" if password else ""} %1\r\n',
                      encoding="utf-8")
    return script


def installer_pictures() -> tuple[Path, Path]:
    """The logo at the right of the installer's header, and the picture down the side of its first and
    last pages, as the bitmaps NSIS takes. Twice the size NSIS lays out, so they stay sharp at 200%."""
    from PIL import Image

    logo = Image.open(ASSETS / "zenith.png").convert("RGBA")
    header = Image.new("RGB", (300, 114), "white")
    mark = logo.resize((92, 92), Image.Resampling.LANCZOS)
    header.paste(mark, (300 - 92 - 12, 11), mark)
    side = Image.new("RGB", (328, 628), (16, 24, 40))
    mark = logo.resize((176, 176), Image.Resampling.LANCZOS)
    side.paste(mark, ((328 - 176) // 2, 150), mark)
    paths = BUILD / "installer-header.bmp", BUILD / "installer-side.bmp"
    header.save(paths[0])
    side.save(paths[1])
    return paths


def uninstall_list(folder: Path) -> Path:
    """The files the installer puts down, for the uninstaller to delete one by one: removing the folder
    whole could take files of the user's with it, if they installed into a folder of their own."""
    lines = []
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        name = str(path.relative_to(folder)).replace("$", "$$")  # NSIS reads $ as a variable
        lines.append(f'  {"RMDir" if path.is_dir() else "Delete"} "$INSTDIR\\{name}"')
    target = BUILD / "uninstall-files.nsh"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def windows_app() -> Path:
    app = pyinstaller(NAME, ["--icon", ASSETS / "zenith.ico", "--version-file", version_info(),
                             "--collect-all", "winrt"])  # text recognition with the engine built into Windows
    self_test(app)
    return app


def windows() -> Path:
    app = windows_app()
    sign = signing_script()
    if sign is not None:
        run([sign, app / f"{NAME}.exe"])
    makensis = _found(["makensis"], [Path(os.environ.get(var, "")) / "NSIS" / "makensis.exe"
                                     for var in ("ProgramFiles(x86)", "ProgramFiles")])
    if makensis is None:
        raise SystemExit("makensis was not found: install NSIS 3 (https://nsis.sourceforge.io).")
    header, side = installer_pictures()
    target = RELEASE / f"{NAME}-Setup.exe"
    defines = {"VERSION": __version__, "SOURCE": app, "OUTFILE": target, "ICON": ASSETS / "zenith.ico",
               "HEADER": header, "SIDE": side, "LICENSE": ROOT / "LICENSE", "FILES": uninstall_list(app)}
    if sign is not None:
        defines["SIGN"] = sign
    run([makensis, "/V2", "/INPUTCHARSET", "UTF8", *(f"/D{key}={value}" for key, value in defines.items()),
         PACKAGING / "windows" / "zenith-pdf.nsi"])
    return target


def store_manifest() -> str:
    """The Store package's AppxManifest.xml: packaging/windows/AppxManifest.xml with this build's values."""
    from zenith_pdf.core.default_app import STORE_APP_ID

    numbers = (re.findall(r"\d+", __version__) + ["0", "0", "0"])[:3]
    languages = "\n".join(f'    <Resource Language="{tag}"/>' for tag in STORE_LANGUAGES.values())
    template = (PACKAGING / "windows" / "AppxManifest.xml").read_text(encoding="utf-8")
    return template.format(**STORE_IDENTITY, version=".".join(numbers) + ".0", languages=languages,
                           app_id=STORE_APP_ID)  # the Store wants the fourth number 0


def store_logos(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, size, share in STORE_LOGOS:
        store_logo(size, share).save(folder / name)


def store_logo(size: int, share: float):
    """The logo drawn from zenith.svg at its final size, in zenith.png's frame and on whole pixels: 4 times larger,
    then each pixel the mean of its 4 x 4 block in premultiplied alpha, so edges get no dark fringe."""
    from make_icon import FRAME
    from PIL import Image
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    svg = (ASSETS / "zenith.svg").read_text(encoding="utf-8")
    base = '<rect x="166" y="360" width="180" height="92"'
    edited = ('fill="url(#drop)"', '<path d="M249 96', '<path d="M263 96', '<path d="M249 185', base, " 336 ", "329.1")
    if not all(part in svg for part in edited):
        raise SystemExit("zenith.svg has changed: update the small logo in store_logo")
    side = round(size * share)
    # the ink drop solid: its fade to transparent shows as a grey neck on a dark taskbar or Start menu
    svg = svg.replace('fill="url(#drop)"', 'fill="#C2AEFF"')
    # the flat edges on whole pixels (the base's top and bottom, the nib's bottom), so none leaves a faint row
    unit = side / FRAME.height()

    def snap(y: float) -> float:
        return FRAME.y() + round((y - FRAME.y()) * unit) / unit

    top, bottom, nib = snap(360), snap(452), snap(336)
    svg = svg.replace(base, f'<rect x="166" y="{top:.4f}" width="180" height="{bottom - top:.4f}"')
    svg = re.sub(r"(?<![\d.])336(?![\d.])", f"{nib:.4f}", svg).replace("329.1", f"{329.1 + nib - 336:.4f}")
    if side <= 32:  # the slit, under 1.2 px wide, draws as a soft line: each half drawn again 7 units in closes it
        svg = (svg.replace("<svg ", '<svg xmlns:xlink="http://www.w3.org/1999/xlink" ')
               .replace('<path d="M249 96', '<path id="left" d="M249 96')
               .replace('<path d="M263 96', '<path id="right" d="M263 96')
               .replace('<path d="M249 185',
                        '<use xlink:href="#left" x="7"/><use xlink:href="#right" x="-7"/><path d="M249 185'))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    renderer.setViewBox(FRAME)
    big = QImage(size * 4, size * 4, QImage.Format.Format_RGBA8888_Premultiplied)
    big.fill(Qt.GlobalColor.transparent)
    painter = QPainter(big)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    offset = (size - side) // 2 * 4
    renderer.render(painter, QRectF(offset, offset, side * 4, side * 4))
    painter.end()
    picture = Image.frombuffer("RGBa", (size * 4, size * 4), bytes(big.constBits()), "raw", "RGBa",
                               big.bytesPerLine(), 1)
    return picture.reduce(4).convert("RGBA")


def windows_kit(tool: str) -> Path:
    """A tool of the Windows SDK, from its newest version."""
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found = sorted(kits.glob(f"10.*/x64/{tool}"), key=lambda p: [int(n) for n in p.parts[-3].split(".")])
    if not found:
        raise SystemExit(f"{tool} was not found: install the Windows SDK.")
    return found[-1]


def store() -> Path:
    """Zenith-PDF.msix, for the Microsoft Store: the Store signs it, hosts it and keeps it up to date."""
    return store_package(windows_app())


def store_package(app: Path) -> Path:
    layout, resources = BUILD / "msix", BUILD / "msix-resources"
    for folder in (layout, resources):
        shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(app, layout)
    store_logos(resources / "Assets")
    manifest = store_manifest()
    for folder in (layout, resources):
        (folder / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
    shutil.copytree(resources / "Assets", layout / "Assets")
    # The index of the logos' sizes, made from the logos alone: indexed whole, the app's thousands of files
    # would go in too.
    makepri, config = windows_kit("makepri.exe"), BUILD / "priconfig.xml"
    run([makepri, "createconfig", "/cf", config, "/dq", "en-US", "/o"])
    # One package: the logos of every scale in its own index, none split off into resource packages.
    config.write_text(re.sub(r"\s*<packaging>.*?</packaging>", "", config.read_text(encoding="utf-8"), flags=re.S),
                      encoding="utf-8")
    run([makepri, "new", "/pr", resources, "/cf", config, "/mn", resources / "AppxManifest.xml",
         "/of", layout / "resources.pri", "/o"])
    target = RELEASE / f"{NAME}.msix"
    run([windows_kit("makeappx.exe"), "pack", "/d", layout, "/p", target, "/o"])
    return target


# macOS ------------------------------------------------------------------------------------


def mac() -> Path:
    app = pyinstaller(TITLE, ["--icon", ASSETS / "zenith.png", "--osx-bundle-identifier", BUNDLE_ID])
    plist_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    info.update({
        "CFBundleDisplayName": TITLE,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHumanReadableCopyright": "Copyright (C) 2026 Andrea Loroni. GPL-3.0-or-later.",
        "NSMicrophoneUsageDescription": "Zenith PDF records the audio notes you add to a page.",
        "NSCameraUsageDescription": "Zenith PDF takes the photos you put on a page.",
        "CFBundleURLTypes": [{"CFBundleURLName": "Zenith PDF link", "CFBundleURLSchemes": ["zenith-pdf"]}],
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "PDF document",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
        }],
    })
    plist_path.write_bytes(plistlib.dumps(info))
    # The edits undo PyInstaller's ad-hoc signature, and an Apple silicon Mac runs nothing unsigned.
    run(["codesign", "--force", "--deep", "--sign", "-", app])
    self_test(app)
    staging = BUILD / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    run(["ditto", app, staging / app.name])
    (staging / "Applications").symlink_to("/Applications")
    target = RELEASE / f"{NAME}-macOS.dmg"
    target.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", TITLE, "-srcfolder", staging, "-ov", "-format", "UDZO", target])
    return target


# Linux ------------------------------------------------------------------------------------


def linux() -> Path:
    app = pyinstaller(NAME, [])
    self_test(app)
    appdir = BUILD / "AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app, appdir / "usr" / "lib" / "zenith-pdf", symlinks=True)
    shutil.copy(PACKAGING / "linux" / "zenith-pdf.desktop", appdir / "zenith-pdf.desktop")
    shutil.copy(ASSETS / "zenith.png", appdir / "zenith-pdf.png")
    shutil.copy(ASSETS / "zenith.png", appdir / ".DirIcon")
    run_script = appdir / "AppRun"
    run_script.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                          f'exec "$HERE/usr/lib/zenith-pdf/{NAME}" "$@"\n', encoding="utf-8")
    run_script.chmod(0o755)
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        raise SystemExit("appimagetool was not found: set APPIMAGETOOL to its AppImage.")
    target = RELEASE / f"{NAME}-Linux-x86_64.AppImage"
    run([tool, appdir, target], env={**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})
    return target


def main() -> int:
    RELEASE.mkdir(parents=True, exist_ok=True)
    build = {"win32": store if "--store" in sys.argv else windows, "darwin": mac}.get(sys.platform, linux)
    target = build()
    print(f"Built {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
