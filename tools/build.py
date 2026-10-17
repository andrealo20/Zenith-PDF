"""Build the release of this system with PyInstaller, into dist/release/.

    pip install pyinstaller
    python tools/build.py

- Windows: Zenith-PDF-Setup.exe, an installer made with NSIS (makensis on the PATH,
  or in Program Files). It asks for the folder and a desktop shortcut, adds Zenith PDF
  to the Start menu, to "Open with" for PDF files and to Installed apps.
- macOS: Zenith-PDF-macOS.dmg, with Zenith PDF.app to drag into Applications.
- Linux: Zenith-PDF-Linux-x86_64.AppImage, one file to make executable and run
  (appimagetool on the PATH, or APPIMAGETOOL naming it).

Signing on Windows: with WINDOWS_CERTIFICATE (a .pfx file) and
WINDOWS_CERTIFICATE_PASSWORD set, the app, the installer and its uninstaller are
signed with signtool and time-stamped. Without them the files are left unsigned.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zenith_pdf import __version__  # noqa: E402

ASSETS = ROOT / "src" / "zenith_pdf" / "assets"
PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = DIST / "release"
NAME = "Zenith-PDF"  # the program's file name
TITLE = "Zenith PDF"  # what people see
BUNDLE_ID = "io.github.andrealo20.zenithpdf"
TIMESTAMP = "http://timestamp.digicert.com"


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
        "--collect-all", "pypdfium2",
        "--collect-all", "pypdfium2_raw",
        "--collect-data", "qtawesome",
        "--exclude-module", "tkinter",
        "--specpath", BUILD,
        "--workpath", BUILD / "pyinstaller",
        "--distpath", DIST,
        *extra,
        ROOT / "tools" / "launcher.py",
    ]
    run(command, cwd=ROOT)
    return DIST / (f"{name}.app" if sys.platform == "darwin" else name)


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


def windows() -> Path:
    app = pyinstaller(NAME, ["--icon", ASSETS / "zenith.ico", "--version-file", version_info(),
                             "--collect-all", "winrt"])  # text recognition with the engine built into Windows
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
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "PDF document",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
        }],
    })
    plist_path.write_bytes(plistlib.dumps(info))
    # The edit undoes PyInstaller's ad-hoc signature, and an Apple silicon Mac runs nothing unsigned.
    run(["codesign", "--force", "--deep", "--sign", "-", app])
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
    build = {"win32": windows, "darwin": mac}.get(sys.platform, linux)
    target = build()
    print(f"Built {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
