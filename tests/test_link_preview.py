"""The card that shows where a link to a page goes, while pointing at it."""

import shutil
import sys
import traceback

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui.link_preview import CAPTION_BELOW, COLUMN, HEIGHT, LINE_CLEARANCE, preview_area, whole_lines


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


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
def workspace(window, links_pdf, tmp_path):
    path = tmp_path / "links.pdf"
    shutil.copy(links_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    workspace.side.hide_panel()  # the whole width for the pages
    settle()
    yield workspace
    window.settings.set("link_preview", True)
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def point_at(view, x, y):
    """Move the mouse to a display-space point of page 1, from somewhere else: a move to where the mouse
    already is never reaches the view. Returns where that is in viewport pixels."""
    pos = view.mapFromScene(view._pages[0].mapToScene(QPointF(x, y)))
    QTest.mouseMove(view.viewport(), pos + QPoint(0, 300))
    QTest.mouseMove(view.viewport(), pos)
    return pos


NAMED = (136, 132)  # middle of the link to "second-middle", page 2 at PDF height 300
EXPLICIT = (136, 82)  # middle of the link to page 3, at PDF height 200, where a caption starts
WEB = (136, 182)


def test_the_area_shown_round_a_spot():
    assert preview_area((612, 792), (312, 400), False) == (300, 370, COLUMN, HEIGHT)  # one column from just left
    assert preview_area((612, 792), (0, 400), False)[::2] == (0, 612)  # no x: the whole width
    x, y, w, h = preview_area((612, 792), (0, 400), True)
    assert y + h == 400 + CAPTION_BELOW  # a caption: the figure above it shows
    assert preview_area((612, 792), (0, 780), False)[1] == 792 - HEIGHT  # kept on the page
    assert preview_area((612, 792), (600, 0), False)[0] == 612 - COLUMN
    assert preview_area((612, 792), None, False) == (0, 0, 612, HEIGHT)
    assert preview_area((300, 100), (50, 50), False) == (0, 0, 300, 100)  # a small page fits whole


def test_the_card_never_cuts_a_line_in_half():
    area = (300, 370, 300, 190)  # 370 to 560
    lines = [(310, 362, 280, 12), (310, 552, 280, 12), (20, 365, 250, 12)]  # the last is in the other column
    left, top, width, height = whole_lines(area, lines)
    assert (left, width) == (300, 300)
    assert top == 362 - LINE_CLEARANCE  # the first line comes in whole
    assert top + height == 552 - LINE_CLEARANCE  # the last one stays out
    assert whole_lines(area, []) == area


def test_pointing_at_a_link_shows_its_spot(workspace):
    view, card = workspace.view, workspace.link_preview
    pos = point_at(view, *NAMED)
    settle(200)
    assert not card.isVisible()  # not before the delay
    QTest.mouseMove(view.viewport(), pos + QPoint(20, 0))  # along the same link: the wait goes on
    settle(400)
    assert card.isVisible() and card.title.text() == "Page 2"
    assert not card.picture.pixmap().isNull()
    assert workspace.canvas.rect().contains(card.geometry())
    assert card.area[1] == pytest.approx(792 - 300 - 30)  # a little above the spot

    point_at(view, 400, 400)
    settle()
    assert not card.isVisible()  # pointing away hides it

    point_at(view, *NAMED)
    settle(600)
    assert card.isVisible()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    settle()
    assert not card.isVisible() and view.current_page == 1  # a click still follows the link


def test_a_caption_shows_the_figure_above_it(workspace):
    view, card = workspace.view, workspace.link_preview
    point_at(view, *EXPLICIT)
    settle(600)
    assert card.isVisible() and card.title.text() == "Page 3"
    x, y, w, h = card.area
    assert y + h == pytest.approx(592 + CAPTION_BELOW)


def test_the_card_survives_a_theme_change_and_hides_for_the_page_manager(workspace, window, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append("".join(traceback.format_exception(*exc))))
    view, card = workspace.view, workspace.link_preview
    point_at(view, *NAMED)
    settle(600)
    assert card.isVisible()
    theme = window.settings.get("theme")
    window.settings.set("theme", "light" if theme == "dark" else "dark")
    settle()
    window.settings.set("theme", theme)
    settle()
    assert not errors
    workspace.toggle_page_manager()
    settle()
    assert not card.isVisible()
    workspace.toggle_page_manager()
    settle()
    assert not errors


def test_web_links_and_the_setting_show_no_card(workspace, window):
    view, card = workspace.view, workspace.link_preview
    point_at(view, *WEB)
    settle(600)
    assert not card.isVisible()  # a web address keeps its tooltip
    window.settings.set("link_preview", False)
    point_at(view, *NAMED)
    settle(600)
    assert not card.isVisible() and QToolTip.text() == "Go to page 2"  # turned off: the tooltip of before
    QToolTip.hideText()  # the tooltips named the address and the page; no window of theirs stays for the next test


def test_scrolling_and_zooming_hide_the_card(workspace):
    view, card = workspace.view, workspace.link_preview
    point_at(view, *NAMED)
    settle(600)
    assert card.isVisible()
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 40)
    assert not card.isVisible()
    point_at(view, *NAMED)
    settle(600)
    assert card.isVisible()
    view.zoom_in()
    assert not card.isVisible()


def test_a_link_to_a_page_this_document_lacks(workspace, monkeypatch):
    """Copied from a longer document, such a link is left out of a paste, which says so. One that is here anyway
    goes nowhere: no card, no jump and no error when pointed at or clicked."""
    from zenith_pdf.core.annotations import Link
    from zenith_pdf.ui import clipboard

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append("".join(traceback.format_exception(*exc))))
    view, card = workspace.view, workspace.link_preview
    links = len([a for a in workspace.store.all() if isinstance(a, Link)])
    clipboard.copy_annotations([Link(page=0, rect=(300, 300, 100, 20), target=15),
                                Link(page=0, rect=(300, 330, 100, 20), target=1, target_point=(0, 300))])
    view.paste_annotations(0, (300.0, 400.0))
    pasted = [a for a in workspace.store.all() if isinstance(a, Link)][links:]
    assert [a.target for a in pasted] == [1]
    assert workspace.toast.text() == "Left out a link to a page this document doesn't have."
    view.add_annotations([Link(page=0, rect=(300, 500, 120, 30), target=15),
                          Link(page=0, rect=(300, 560, 120, 30), target=15, target_point=(0, 400))], "Links")
    for y in (515, 575):
        pos = point_at(view, 360, y)
        settle(600)
        assert not card.isVisible()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        settle()
        assert view.current_page == 0
    view.follow_link("page", 15, (0, 400))
    assert view.current_page == 0 and not errors
