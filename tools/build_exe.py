"""Build Zenith-PDF.exe, the whole app in one file for Windows, with PyInstaller.

    pip install pyinstaller
    python tools/build_exe.py

The executable lands in dist/. It unpacks itself into a temporary folder each time it
starts, so the first window takes a few seconds longer than running from source.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zenith_pdf import __version__  # noqa: E402

BUILD = ROOT / "build"
VERSION_FILE = BUILD / "version_info.txt"


def version_info() -> Path:
    """The Windows file properties (Details tab) of the executable."""
    numbers = tuple(int(part) for part in __version__.split(".")[:3]) + (0,)
    BUILD.mkdir(exist_ok=True)
    VERSION_FILE.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Andrea Loroni'),
      StringStruct('FileDescription', 'Zenith PDF'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'Zenith-PDF'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Andrea Loroni. GPL-3.0-or-later.'),
      StringStruct('OriginalFilename', 'Zenith-PDF.exe'),
      StringStruct('ProductName', 'Zenith PDF'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return VERSION_FILE


def main() -> int:
    assets = ROOT / "src" / "zenith_pdf" / "assets"
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Zenith-PDF",
        "--icon", str(assets / "zenith.ico"),
        "--version-file", str(version_info()),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{assets}{os.pathsep}zenith_pdf/assets",
        "--collect-all", "pypdfium2",
        "--collect-all", "pypdfium2_raw",
        "--collect-data", "qtawesome",
        "--collect-all", "winrt",  # text recognition with the engine built into Windows
        "--exclude-module", "tkinter",
        "--specpath", str(BUILD),
        "--workpath", str(BUILD / "pyinstaller"),
        "--distpath", str(ROOT / "dist"),
        str(ROOT / "tools" / "launcher.py"),
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
