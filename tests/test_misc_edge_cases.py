"""Edge cases of the tool menu's header, the selection bar's place, the cleaner on turned text, flipping a turned
picture, and the updater after a click, a cancel or an unexpected error. GitHub is played by fakes."""

import http.client
import io
import shutil
import time

import pytest
from PySide6.QtCore import QPointF, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget

from conftest import settle, wait_for
from zenith_pdf.app import create_app
from zenith_pdf.core import arrange, images, updates
from zenith_pdf.core.annotations import ImageStamp, Shape, TextMarkup
from zenith_pdf.core.i18n import set_language
from zenith_pdf.core.updates import Update, UpdateError
from zenith_pdf.settings import Settings
from zenith_pdf.ui.annotation_items import paint_annotation
from zenith_pdf.ui.updates import UpdateCard, Updater


class Mouse:
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier

    def globalPosition(self):
        return QPointF(400, 300)


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.resize(1300, 900)
    window.open_file(path)
    workspace = window.current_workspace()
    settle(60)
    yield workspace
    menu = workspace.toolbar.tool_menu
    if menu is not None:
        menu.close_menu()
    workspace.tool_settings.set_tool("select")
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


@pytest.fixture
def italian():
    set_language("it")
    yield
    set_language("en")


def open_menu(workspace, key):
    workspace.tool_settings.set_tool(key)
    workspace.toolbar.open_menu(key)
    settle(60)
    return workspace.toolbar.tool_menu


def test_a_long_tool_name_is_cut_short_with_the_whole_name_in_its_tip(workspace, italian):
    title = open_menu(workspace, "highlight").title
    assert title.toolTip() == "Evidenziazione del testo" and title.text() != title.toolTip()
    assert title.fontMetrics().horizontalAdvance(title.text()) <= title.contentsRect().width()


def test_the_tool_subject_line_is_translated(workspace, italian):
    menu = open_menu(workspace, "pen")
    subject = workspace.tool_settings.subject("pen")
    assert menu.subject_caption.text() == f"Oggetto dello strumento: {subject}"  # the subject itself as stored


def test_the_selection_bar_keeps_off_the_page_counter(window, workspace):
    window.resize(700, 800)
    settle(150)
    view, canvas = workspace.view, workspace.selection_bar.parentWidget()
    shape = Shape(page=0, rect=(250, 300, 120, 60), color=(0.9, 0.2, 0.2))
    view.add_annotations([shape], "Rectangle")
    view.select([shape])
    settle(60)
    counter = canvas.bars["counter"]
    assert counter.isVisible()
    spot = counter.geometry()
    for top in range(spot.bottom() - 20, spot.bottom() + 80, 10):  # its spot above lands on the counter
        canvas.place_selection(QRect(spot.center().x() - 60, top, 120, 60))
        assert not workspace.selection_bar.geometry().intersects(spot), top


def test_the_cleaner_cuts_turned_text_along_its_length(workspace):
    view, document, store = workspace.view, workspace.document, workspace.store
    count = document.char_count(1)  # page 2 is turned a quarter: its line runs down the page
    whole, text = document.text_span(1, 0, count - 1)
    start = text.index("needle")
    needle, _ = document.text_span(1, start, count - 1)
    mark = TextMarkup(page=1, style="Highlight", quads=whole, rotation=document.text_turns(1, 0))
    assert mark.rotation % 2 == 1
    view.add_annotations([mark], "Highlight")
    view.tool_settings.set_tool("cleaner")
    tool, item = view._tools["cleaner"], view._pages[1]
    x, y, w, h = needle[0]
    tool.press(item, QPointF(x + w / 2, y + 1), Mouse())
    tool.move(item, QPointF(x + w / 2, y + h - 1), Mouse())
    tool.release(item, QPointF(x + w / 2, y + h - 1), Mouse())
    [(lx, ly, lw, lh)] = store.get(mark.id).quads  # "Page 2 " keeps its highlight, all across the line
    wx, wy, ww, _ = whole[0]
    assert (lx, ly, lw) == pytest.approx((wx, wy, ww)) and lh == pytest.approx(y - wy, abs=1)


def _render(annotation) -> QImage:
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    paint_annotation(painter, annotation)
    painter.end()
    return image


@pytest.mark.parametrize("rotation", [1, 3])
def test_flipping_a_turned_picture_mirrors_it_the_way_asked(qt_app, rotation):
    picture = QImage(40, 20, QImage.Format.Format_RGB32)
    picture.fill(QColor("white"))
    painter = QPainter(picture)
    painter.fillRect(0, 0, 20, 10, QColor("red"))  # a corner of its own, so a mirror shows which way it went
    painter.end()
    stamp = ImageStamp(page=0, rect=(40, 30, 20, 40), data=images.encode(picture), rotation=rotation)
    before = _render(stamp)
    for axis in "hv":
        (flipped,) = arrange.flipped([stamp], axis)
        after = _render(flipped)
        mirror = (lambda px, py: (99 - px, py)) if axis == "h" else (lambda px, py: (px, 99 - py))
        wrong = sum(after.pixel(px, py) != before.pixel(*mirror(px, py)) for px in range(100) for py in range(100))
        assert wrong <= 20, (rotation, axis, wrong)


@pytest.fixture
def settings(tmp_path):
    return Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def _labels(card) -> list[str]:
    found = (card.buttons.itemAt(i).widget() for i in range(card.buttons.count()))
    return [widget.text() for widget in found if widget is not None]


def test_update_starts_the_download_and_cancel_keeps_the_card_closed(qt_app, settings):
    update = Update(version="9.0.0", notes="* Pressure for pens", page="https://example.invalid",
                    name="Zenith-PDF-Setup.exe", url="https://example.invalid/x", size=1, sha256="a" * 64)
    started = []

    def fetch_file(update, folder, progress=None, cancelled=None):
        started.append(update)
        deadline = time.monotonic() + 5
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise UpdateError("The download was stopped.")

    updater = Updater(settings, fetch=lambda: update, fetch_file=fetch_file, kind="installer")
    updater.update = update
    host = QWidget()
    host.resize(900, 600)
    card = UpdateCard(updater, host)
    updater.found.emit(update)
    [button] = [b for b in card.findChildren(QPushButton) if b.text() == "Update"]
    button.click()
    assert _labels(card) == ["Cancel"] and wait_for(lambda: started)
    card._cancel()
    assert wait_for(lambda: not updater.busy)
    settle(100)
    assert not card.isVisibleTo(host)  # cancelled is not a failure to report
    updater.check(manual=True)  # and the card works again, its buttons swapped once more
    assert wait_for(lambda: card.isVisibleTo(host)) and "Update" in _labels(card)
    card.deleteLater()
    host.deleteLater()


def test_the_updater_is_free_again_after_any_error(qt_app, settings, tmp_path, monkeypatch):
    asked = []

    class CutShort(io.BytesIO):
        def read(self, *_):
            raise http.client.IncompleteRead(b"{", 100)

    def opener(request, timeout):
        asked.append(request.full_url)
        return CutShort()

    updater = Updater(settings, fetch=lambda: updates.latest(opener), kind="installer")
    failed = []
    updater.failed.connect(failed.append)
    updater.check(manual=True)
    assert wait_for(lambda: failed) and not updater.busy
    updater.check(manual=True)  # asks again rather than waiting for a check that died
    assert wait_for(lambda: len(failed) == 2) and len(asked) == 2

    blocker = tmp_path / "zenith-pdf-update"
    blocker.write_text("a file where the downloads go")
    monkeypatch.setattr(updates, "downloads_folder", lambda: blocker)

    def refuse(request, timeout):
        raise AssertionError("nothing should be asked for")

    updater = Updater(settings, fetch_file=lambda update, folder, **kw: updates.download(update, folder,
                                                                                         opener=refuse, **kw),
                      kind="installer")
    updater.failed.connect(failed.append)
    updater.update = Update(version="9.0.0", notes="", page="", name="Zenith-PDF-Setup.exe",
                            url="https://example.invalid/x", size=1, sha256="a" * 64)
    updater.download()
    assert wait_for(lambda: len(failed) == 3) and not updater.busy
    assert failed[-1].startswith("The new version could not be downloaded")
