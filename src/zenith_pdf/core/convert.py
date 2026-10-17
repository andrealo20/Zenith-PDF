"""Opening documents that are not PDFs: text, Markdown, web pages and office files become a PDF first.

Text, Markdown and HTML are typeset here, with Qt. Word, Excel, PowerPoint, OpenDocument and RTF files need an
office suite to lay them out as they were made: LibreOffice where it is installed, on any system, else
Microsoft Office on Windows. Nothing is downloaded or installed, and the file itself is only read.
"""

from __future__ import annotations

import codecs
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TEXT = {".txt", ".text", ".log", ".md", ".markdown", ".html", ".htm", ".xhtml"}
WORD = {".docx", ".doc", ".docm", ".odt", ".rtf"}
EXCEL = {".xlsx", ".xls", ".xlsm", ".ods"}
POWERPOINT = {".pptx", ".ppt", ".pptm", ".odp"}
OFFICE = WORD | EXCEL | POWERPOINT
SUFFIXES = TEXT | OFFICE
TIMEOUT = 300  # seconds an office suite gets to convert one file
FILTER = "*.pdf " + " ".join(f"*{suffix}" for suffix in sorted(SUFFIXES))  # for file dialogs

# Microsoft Office, driven through PowerShell: the paths come in environment variables, never in the script.
_OFFICE_SCRIPTS = {
    "Word": """
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$app.DisplayAlerts = 0
try {
    $doc = $app.Documents.Open($env:ZENITH_IN, $false, $true, $false)
    $doc.ExportAsFixedFormat($env:ZENITH_OUT, 17)
    $doc.Close(0)
} finally { $app.Quit() }
""",
    "Excel": """
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
try {
    $book = $app.Workbooks.Open($env:ZENITH_IN, 0, $true)
    $book.ExportAsFixedFormat(0, $env:ZENITH_OUT)
    $book.Close($false)
} finally { $app.Quit() }
""",
    "PowerPoint": """
$app = New-Object -ComObject PowerPoint.Application
try {
    $deck = $app.Presentations.Open($env:ZENITH_IN, -1, 0, 0)
    $deck.SaveAs($env:ZENITH_OUT, 32)
    $deck.Close()
} finally { $app.Quit() }
""",
}


class ConversionError(Exception):
    """The file could not become a PDF; the message says why, for the reader."""


class Cancelled(Exception):
    pass


def can_convert(path) -> bool:
    return Path(path).suffix.lower() in SUFFIXES


def convert(path, cancel: threading.Event | None = None) -> bytes:
    """The PDF a text, web or office file makes. Raises ConversionError, or Cancelled once `cancel` is set."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in TEXT:
        return typeset(path)
    if suffix not in OFFICE:
        raise ConversionError(f"Zenith PDF can't open {suffix} files.")
    soffice = libreoffice()
    if soffice is not None:
        return _with_libreoffice(soffice, path, cancel)
    app = office_app(suffix)
    if app is not None:
        return _with_office(app, path, cancel)
    raise ConversionError(f"To open {suffix} files, install LibreOffice, which is free, or Microsoft Office.")


def _decoded(raw: bytes) -> str:
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def typeset(path: Path) -> bytes:
    """Text, Markdown or HTML laid out on A4 pages, as Qt's rich text engine sets them."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QUrl
    from PySide6.QtGui import QFontDatabase, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    text = _decoded(path.read_bytes())
    suffix = path.suffix.lower()
    document = QTextDocument()
    document.setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))  # pictures next to a web page show
    if suffix in (".md", ".markdown"):
        document.setMarkdown(text)
    elif suffix in (".html", ".htm", ".xhtml"):
        document.setHtml(text)
    else:  # plain text keeps its columns: one width for every letter
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSizeF(10)
        document.setDefaultFont(font)
        document.setPlainText(text)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setTitle(path.stem)
    writer.setCreator("Zenith PDF")
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(20, 20, 20, 20), QPageLayout.Unit.Millimeter)
    document.print_(writer)
    del writer  # finishes the file
    buffer.close()
    return bytes(data)


def libreoffice() -> str | None:
    """LibreOffice's soffice, if it is installed."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    places = []
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                places.append(Path(root) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        places.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    return next((str(place) for place in places if place.is_file()), None)


def office_app(suffix: str) -> str | None:
    """The Microsoft Office program for a kind of file, on Windows, if it is installed."""
    if sys.platform != "win32":
        return None
    app = "Word" if suffix in WORD else "Excel" if suffix in EXCEL else "PowerPoint" if suffix in POWERPOINT else None
    if app is None:
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{app}.Application\\CLSID"):
            return app
    except OSError:
        return None


def _run(command: list[str], cancel: threading.Event | None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a converter, giving up when `cancel` is set or it takes longer than TIMEOUT."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=flags)
    started = time.monotonic()
    while process.poll() is None:
        if (cancel is not None and cancel.is_set()) or time.monotonic() - started > TIMEOUT:
            process.kill()
            process.communicate()
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raise ConversionError("The conversion took too long and was stopped.")
        time.sleep(0.1)
    out, err = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def _with_libreoffice(soffice: str, path: Path, cancel: threading.Event | None) -> bytes:
    with tempfile.TemporaryDirectory(prefix="zenith-convert-") as folder:
        out = Path(folder)
        # A profile of its own: it works while LibreOffice is open, and leaves the user's settings alone.
        profile = (out / "profile").as_uri()
        result = _run([soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--convert-to",
                       "pdf", "--outdir", str(out), str(path)], cancel)
        made = out / f"{path.stem}.pdf"
        if not made.is_file():
            detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip()
            raise ConversionError(f"LibreOffice could not convert {path.name}." + (f" {detail}" if detail else ""))
        return made.read_bytes()


def _with_office(app: str, path: Path, cancel: threading.Event | None) -> bytes:
    with tempfile.TemporaryDirectory(prefix="zenith-convert-") as folder:
        made = Path(folder) / f"{path.stem}.pdf"
        env = dict(os.environ, ZENITH_IN=str(path.resolve()), ZENITH_OUT=str(made))
        result = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                       "$ErrorActionPreference = 'Stop'\n" + _OFFICE_SCRIPTS[app]], cancel, env)
        if not made.is_file():
            detail = (result.stderr or b"").decode(errors="replace").strip().splitlines()
            raise ConversionError(f"Microsoft {app} could not convert {path.name}." +
                                  (f" {detail[0]}" if detail else ""))
        return made.read_bytes()
