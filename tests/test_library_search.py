"""Searching inside every PDF opened: the index, its thread, and the results on the home page."""

import os
import time

import pypdfium2 as pdfium
import pytest
from PySide6.QtCore import QSettings

from conftest import _add_text, wait_for
from zenith_pdf.core.library_index import MATCH_END, MATCH_START, LibraryIndex, query_terms
from zenith_pdf.settings import Settings
from zenith_pdf.ui.library_search import Indexer, InsideResults


def make_pdf(path, pages):
    pdf = pdfium.PdfDocument.new()
    for lines in pages:
        page = pdf.new_page(612, 792)
        for number, line in enumerate(lines):
            _add_text(pdf, page, line, 72, 700 - 30 * number)
        page.gen_content()
        page.close()
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.fixture
def library(tmp_path):
    lecture = make_pdf(tmp_path / "Lecture 4.pdf", [["Solar cells and the sun."],
                                                     ["The maximum power point moves with temperature."]])
    lab = make_pdf(tmp_path / "Lab report.pdf", [["Inverter efficiency peaks near 40 percent load."],
                                                 ["Caffè e perché: accents fold."]])
    return lecture, lab


def test_files_moved_away_leave_the_index_and_their_places_in_the_results(tmp_path):
    first = make_pdf(tmp_path / "first.pdf", [["Solar cells, solar panels, solar farms."]])
    second = make_pdf(tmp_path / "second.pdf", [["Solar panels on the roof."]])
    index = LibraryIndex(tmp_path / "cache" / "index.sqlite3")
    index.add(first)
    index.add(second)
    first.rename(tmp_path / "moved.pdf")  # its rows still name the old path, and rank first
    assert [h.path for h in index.search("solar", limit=1)] == [second] and index.files() == 1


def test_the_index_finds_every_word_on_one_page(tmp_path, library):
    lecture, lab = library
    index = LibraryIndex(tmp_path / "cache" / "index.sqlite3")
    assert index.add(lecture) == 2 and index.add(lab) == 2
    assert index.add(lecture) == -1  # unchanged: not read again
    hits = index.search("maximum power temperature")
    assert [(h.path.name, h.page) for h in hits] == [("Lecture 4.pdf", 1)]
    assert f"{MATCH_START}maximum{MATCH_END}" in hits[0].snippet.lower()
    assert [h.path.name for h in index.search("effic")] == ["Lab report.pdf"]  # the start of a word is enough
    assert [h.page for h in index.search("CAFFE perche")] == [1]  # no case, no accents
    assert index.search("sun inverter") == []  # never on the same page
    assert query_terms("a, b... power!") == ["power"]
    time.sleep(0.01)
    make_pdf(lab, [["Now it is about batteries."]])  # changed: read again, the old words gone
    os.utime(lab, (time.time() + 5, time.time() + 5))
    assert index.add(lab) == 1
    assert index.search("inverter") == [] and len(index.search("batteries")) == 1
    index.forget(lecture)
    assert index.search("solar") == [] and index.files() == 1
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 not really")
    assert index.add(broken) == 0  # remembered as empty, never tried again until it changes
    assert index.add(broken) == -1
    index.close()


def test_the_indexer_reads_in_its_own_thread_and_the_home_page_lists_the_pages(qt_app, tmp_path, library):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    indexer = Indexer(settings, index=LibraryIndex(tmp_path / "index.sqlite3"))
    done = []
    indexer.indexed.connect(done.append)
    indexer.add(list(library) + [tmp_path / "notes.txt"])
    assert wait_for(lambda: len(done) == 2 and not indexer.busy())
    results = InsideResults(indexer)
    asked = []
    results.page_requested.connect(lambda *args: asked.append(args))
    results.show_for("power point")
    assert results.isVisibleTo(results.parentWidget() or results) and results.list.count() == 1
    results._open(results.list.item(0))
    assert asked == [(library[0].resolve(), 1, "power point")]
    results.show_for("po")  # too short to search all documents for
    assert not results.isVisibleTo(results.parentWidget() or results)
    settings.set("search_index", False)
    assert indexer.search("power") == []
    indexer.add(list(library))
    assert not indexer.busy()  # off: nothing is read
    indexer.stop()
    indexer.index.close()


def test_a_page_found_opens_with_its_words_lit(qt_app, tmp_path, library, monkeypatch):
    from zenith_pdf.app import create_app
    from zenith_pdf.ui.main_window import MainWindow

    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    monkeypatch.setattr(MainWindow, "indexer", None)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    try:
        window.open_at_page(library[0], 1, "maximum power")
        workspace = window.current_workspace()
        assert wait_for(lambda: workspace.view.current_page == 1 and workspace.search.query == "maximum power")
        window.command_search_all()
        assert window.stack.currentWidget() is window.home
    finally:
        for index in reversed(range(len(window.workspaces))):
            window.close_tab(index, confirm=False)
        window.close()
        window.renderer.shutdown()
