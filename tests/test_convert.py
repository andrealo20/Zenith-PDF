"""Opening text, Markdown, web and office files: each becomes a PDF, a new document beside the file."""

import threading
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core import convert
from zenith_pdf.settings import Settings

pytestmark = pytest.mark.usefixtures("qt_app")


def text_of(data: bytes) -> str:
    pdf = pdfium.PdfDocument(data)
    try:
        return " ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()


def test_text_markdown_and_web_pages_are_typeset(tmp_path):
    (tmp_path / "plain.txt").write_text("name   size\nalpha  10\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Plans\n\nSome *words* and a list:\n\n- first\n- second\n", encoding="utf-8")
    (tmp_path / "page.html").write_text("<h1>Hello</h1><p>From <b>HTML</b>, città.</p>", encoding="utf-8")
    (tmp_path / "old.txt").write_bytes("caffè".encode("cp1252"))  # not UTF-8: read as Windows text
    assert "alpha" in text_of(convert.convert(tmp_path / "plain.txt"))
    words = text_of(convert.convert(tmp_path / "notes.md"))
    assert "Plans" in words and "second" in words and "#" not in words
    assert "città" in text_of(convert.convert(tmp_path / "page.html"))
    assert "caffè" in text_of(convert.convert(tmp_path / "old.txt"))


def test_office_files_need_an_office_suite(tmp_path, monkeypatch):
    path = tmp_path / "letter.docx"
    path.write_bytes(b"PK")
    monkeypatch.setattr(convert, "libreoffice", lambda: None)
    monkeypatch.setattr(convert, "office_app", lambda suffix: None)
    with pytest.raises(convert.ConversionError, match="LibreOffice"):
        convert.convert(path)
    with pytest.raises(convert.ConversionError):
        convert.convert(tmp_path / "picture.bmp")


def test_libreoffice_converts_with_a_profile_of_its_own(tmp_path, monkeypatch, sample_pdf):
    path = tmp_path / "sheet.xlsx"
    path.write_bytes(b"PK")
    seen = []

    def run(command, cancel, env=None):
        seen.append(command)
        out = Path(command[command.index("--outdir") + 1])
        (out / "sheet.pdf").write_bytes(Path(sample_pdf).read_bytes())
        return None

    monkeypatch.setattr(convert, "libreoffice", lambda: "soffice")
    monkeypatch.setattr(convert, "_run", run)
    data = convert.convert(path)
    assert data == Path(sample_pdf).read_bytes()
    [command] = seen
    assert command[0] == "soffice" and "--headless" in command and command[-1] == str(path)
    assert any(part.startswith("-env:UserInstallation=file:") for part in command)


def test_microsoft_office_gets_the_paths_outside_its_script(tmp_path, monkeypatch, sample_pdf):
    path = tmp_path / "deck; rm -rf.pptx"  # a name that must never become part of a command
    path.write_bytes(b"PK")
    seen = []

    def run(command, cancel, env=None):
        seen.append((command, env))
        Path(env["ZENITH_OUT"]).write_bytes(Path(sample_pdf).read_bytes())
        return None

    monkeypatch.setattr(convert, "libreoffice", lambda: None)
    monkeypatch.setattr(convert, "office_app", lambda suffix: "PowerPoint")
    monkeypatch.setattr(convert, "_run", run)
    assert convert.convert(path)[:5] == b"%PDF-"
    [(command, env)] = seen
    assert env["ZENITH_IN"] == str(path.resolve()) and not any(path.name in part for part in command)
    assert "PowerPoint.Application" in command[-1]


def test_a_converter_can_be_cancelled(monkeypatch):
    import sys

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(convert.Cancelled):
        convert._run([sys.executable, "-c", "import time; time.sleep(30)"], cancel)


@pytest.fixture
def window(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 800)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def test_opening_a_text_file_opens_a_new_pdf_beside_it(window, tmp_path):
    path = tmp_path / "todo.md"
    path.write_text("# To do\n\n- write\n- check\n", encoding="utf-8")
    window.open_file(path)
    workspace = window.current_workspace()
    document = workspace.document
    assert document.path == (tmp_path / "todo.pdf").resolve() and document.unsaved and document.page_count == 1
    assert "check" in document.page_text(0)
    window.open_file(path)  # again: the same tab, not a second conversion
    assert len(window.workspaces) == 1
    assert path.read_text(encoding="utf-8").startswith("# To do")  # the file itself is left as it was
