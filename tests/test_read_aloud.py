"""Reading aloud, with Qt's mock voice: sentences in order, page after page, the one being read lit on the page.
Nothing is played on the speakers."""

import pytest
import pypdfium2 as pdfium
from PySide6.QtTest import QTest

from conftest import _add_text, wait_for
from zenith_pdf.core import writing
from zenith_pdf.core.document import Document
from zenith_pdf.ui import read_aloud

pytest.importorskip("PySide6.QtTextToSpeech")
from PySide6.QtTextToSpeech import QTextToSpeech  # noqa: E402

MOCK = "mock" in QTextToSpeech.availableEngines()
needs_mock = pytest.mark.skipif(not MOCK, reason="Qt's mock voice is not installed here")


@pytest.fixture
def two_pages(tmp_path):
    path = tmp_path / "speech.pdf"
    pdf = pdfium.PdfDocument.new()
    for lines in (["The sun gives light. Cells turn it", "into current. 1"], ["Batteries keep it for the night."]):
        page = pdf.new_page(612, 792)
        for number, line in enumerate(lines):
            _add_text(pdf, page, line, 72, 700 - 30 * number)
        page.gen_content()
        page.close()
    pdf.save(str(path))
    pdf.close()
    document = Document(path)
    yield document
    document.close()


def test_sentences_keep_their_place_on_the_page(two_pages):
    sentences = read_aloud.page_sentences(two_pages, 0)
    assert [s.text for s in sentences] == ["The sun gives light.", "Cells turn it into current."]
    assert all(s.page == 0 and s.rects for s in sentences)  # the lone page number is not read
    first, second = sentences[0].rects[0], sentences[1].rects[0]
    assert first[1] == pytest.approx(second[1], abs=2)  # the second starts on the same line as the first
    assert writing.sentence_spans("  Ab cd.  Ef gh.\n") == [(2, 8), (10, 16)]
    assert read_aloud.rate_for(1.0) == 0 and read_aloud.rate_for(2.0) == 1 and read_aloud.rate_for(0.5) == -1


@needs_mock
def test_the_reader_goes_through_the_pages_in_order(qt_app, two_pages):
    reader = read_aloud.Reader(two_pages, None, engine="mock")
    said, states = [], []
    reader.speaking.connect(lambda s: said.append(None if s is None else s.text))
    reader.state_changed.connect(states.append)
    reader.read_from_page(0)
    assert states == ["playing"]
    assert wait_for(lambda: reader.state == "stopped")
    assert said == ["The sun gives light.", "Cells turn it into current.", "Batteries keep it for the night.", None]


@needs_mock
def test_pause_skip_and_stop(qt_app, two_pages):
    reader = read_aloud.Reader(two_pages, None, engine="mock")
    said = []
    reader.speaking.connect(lambda s: said.append(None if s is None else s.text))
    reader.read_text("One here. Two there. Three at last.")
    reader.toggle()
    assert reader.state == "paused"
    reader.toggle()
    assert reader.state == "playing"
    reader.step(2)
    assert said[-1] == "Three at last."
    reader.stop()
    assert reader.state == "stopped" and said[-1] is None
    reader.set_speed(1.5)
    assert reader.speed == 1.5


@needs_mock
def test_the_workspace_reads_the_selection_and_lights_it(qt_app, two_pages, monkeypatch):
    from PySide6.QtCore import QSettings

    from zenith_pdf.app import create_app
    from zenith_pdf.settings import Settings

    settings = Settings(QSettings(str(two_pages.path.with_suffix(".ini")), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    monkeypatch.setattr(read_aloud, "available", lambda: True)
    original = read_aloud.ReadAloud.__init__
    monkeypatch.setattr(read_aloud.ReadAloud, "__init__", lambda self, ws, engine="": original(self, ws, "mock"))
    try:
        window.open_file(two_pages.path)
        workspace = window.current_workspace()
        QTest.qWait(50)
        workspace.read_aloud()
        speaker = workspace._read_aloud
        assert speaker.bar.isVisible() and speaker._lit  # the first sentence is lit on its page
        assert wait_for(lambda: speaker.reader.state == "stopped")
        assert not speaker.bar.isVisible() and not speaker._lit
    finally:
        for index in reversed(range(len(window.workspaces))):
            window.close_tab(index, confirm=False)
        window.close()
        window.renderer.shutdown()
