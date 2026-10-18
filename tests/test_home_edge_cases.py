"""The home page and its search inside documents at the edges: other languages, files being saved while they are
read, a damaged index, brackets in the text, and the render thread beside the render processes."""

import os
import shutil
import threading
import time
from collections import deque

import pytest
from PySide6.QtCore import QRect, QSettings, Qt
from PySide6.QtGui import QImage, QPainter, QTextDocument
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QStyle, QStyleOptionViewItem

from conftest import _add_text
from zenith_pdf.core import i18n
from zenith_pdf.core.document import Document
from zenith_pdf.core.i18n import set_language
from zenith_pdf.core.library_index import MATCH_END, MATCH_START, LibraryIndex
from zenith_pdf.core.render import MAX_RESTARTS, RenderPool
from zenith_pdf.settings import Settings
from zenith_pdf.ui import library_search
from zenith_pdf.ui.home import NAME, OPENED, ROW_HEIGHT, TAGS, FileEntry, HomePage


@pytest.fixture
def home(qt_app, tmp_path):
    page = HomePage(Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)))
    yield page
    page.loader.shutdown()
    page.close()
    page.deleteLater()


@pytest.fixture
def language():
    yield set_language
    set_language("en")


def make_pdf(path, lines):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    for line in lines:
        page = pdf.new_page(612, 792)
        _add_text(pdf, page, line, 72, 700)
        page.gen_content()
        page.close()
    pdf.save(str(path))
    pdf.close()
    return path


def test_the_file_list_is_in_the_language_in_use(home, language, sample_pdf, tmp_path, monkeypatch):
    language("it")
    path = tmp_path / "a.pdf"
    shutil.copy(sample_pdf, path)
    home._settings.add_recent(path)
    home._settings.set("file_tags", {str(path): ["Esame"]})
    home.refresh()
    model = home.table.model()
    assert [model.headerData(c, Qt.Orientation.Horizontal) for c in (NAME, OPENED, TAGS)] == \
        ["Nome", "Ultima apertura", "Tag"]
    assert model.index(0, OPENED).data() == "Proprio ora"
    card = home.cards[0]
    card.set_mode("icon")
    assert card.toolTip() == "Nuovo documento: Bianco, a righe, a quadretti o da un modello"
    monkeypatch.setitem(i18n._current.exact, "Tagged", "Con tag")  # the key is not in the locales yet
    home.show_scope("tag:Esame")
    assert home.title.text() == "Con tag Esame"


def test_add_tag_shows_only_under_the_pointer_and_stays_in_its_column(home, language, tmp_path):
    language("fr")  # "Ajouter une étiquette": wider than the column below
    home.model.set_entries([FileEntry(path=tmp_path / "a.pdf")])
    index = home.proxy.index(0, TAGS)
    width = 90

    def paint(hover: bool) -> QImage:
        image = QImage(width + 100, ROW_HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, width, ROW_HEIGHT)
        option.font = home.table.font()
        if hover:
            option.state |= QStyle.StateFlag.State_MouseOver
        painter = QPainter(image)
        home.table.itemDelegate().paint(painter, option, index)
        painter.end()
        return image

    def colours(image, xs, ys) -> set:
        return {image.pixel(x, y) for x in xs for y in ys}

    above_the_border = range(ROW_HEIGHT - 4)
    assert colours(paint(False), range(width), above_the_border) == {0}  # nothing on a row the pointer isn't on
    hovered = paint(True)
    assert len(colours(hovered, range(4, width - 4), above_the_border)) > 1  # the icon and the words
    assert colours(hovered, range(width + 2, width + 100), above_the_border) == {0}  # cut short, not past the edge


def test_saving_a_file_while_the_index_reads_it(tmp_path):
    path = make_pdf(tmp_path / "doc.pdf", ["one", "two", "three"])
    index = LibraryIndex(tmp_path / "index.sqlite3")
    reading, go = threading.Event(), threading.Event()

    def stop():
        reading.set()
        go.wait(10)
        return False

    reader = threading.Thread(target=index.add, args=(path, stop))
    reader.start()
    try:
        assert reading.wait(10)
        saved = tmp_path / "~doc.pdf.tmp"
        shutil.copy(path, saved)
        os.replace(saved, path)  # what Document.save does: on Windows it fails while PDFium has the file open
    finally:
        go.set()
        reader.join(10)
        index.close()


def test_a_damaged_index_starts_again_empty(tmp_path):
    path = tmp_path / "search-index.sqlite3"
    path.write_bytes(b"\0" * 4096)
    (tmp_path / "search-index.sqlite3-wal").write_bytes(b"\0" * 100)
    index = LibraryIndex(path)
    try:
        assert index.files() == 0
        assert index.add(make_pdf(tmp_path / "doc.pdf", ["Solar cells"])) == 1
        assert len(index.search("solar")) == 1
    finally:
        index.close()


def test_brackets_in_the_text_stay_and_only_the_match_is_bold(qt_app, tmp_path, monkeypatch):
    index = LibraryIndex(tmp_path / "index.sqlite3")
    try:
        index.add(make_pdf(tmp_path / "paper.pdf", ["as shown in [12] the transformer model beats [3]"]))
        hit = index.search("transformer")[0]
    finally:
        index.close()
    assert "[12]" in hit.snippet and f"{MATCH_START}transformer{MATCH_END}" in hit.snippet
    shown = []

    class Recording(QTextDocument):
        def setHtml(self, text):
            shown.append(text)
            super().setHtml(text)

    monkeypatch.setattr(library_search, "QTextDocument", Recording)
    view = QListWidget()
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, hit)
    view.addItem(item)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 600, library_search.ROW)
    option.font = view.font()
    image = QImage(600, library_search.ROW, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    library_search._HitDelegate().paint(painter, option, view.model().index(0, 0))
    painter.end()
    assert "[12]" in shown[0] and "[3]" in shown[0] and "<b>transformer</b>" in shown[0]
    assert "<b>12</b>" not in shown[0]


class _Process:
    """A render process that is up, takes jobs and never answers: where each job went is all that counts."""

    def __init__(self, index):
        self.index, self.ready, self.restarts, self.living = index, True, MAX_RESTARTS, True
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def alive(self):
        return self.living

    def stop(self):
        pass


def test_the_render_thread_stands_aside_while_a_process_is_up(qt_app, sample_pdf):
    pool = RenderPool(processes=0)
    process = _Process(1)
    pool._workers.append(process)
    pool._recent[1] = deque(maxlen=4)
    document = Document(sample_pdf)
    done = []
    pool.rendered.connect(lambda key, scale, image: done.append(key))
    try:
        pool.request_many([("a", document, 0, 1.0, (0, 0), None), ("b", document, 1, 1.0, (0, 1), None)])
        assert [m[0] for m in process.sent].count("render") == 1
        assert list(pool._jobs) == ["b"]  # waiting for the process, not taken by the thread in this one
        process.living = False  # gone for good: the thread renders again
        pool._dispatch()
        for _ in range(500):
            QApplication.processEvents()
            if done:
                break
            time.sleep(0.01)
        assert done == ["b"]
    finally:
        pool.forget(document)
        pool.shutdown()
        document.close()
