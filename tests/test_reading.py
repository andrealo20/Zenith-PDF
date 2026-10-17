"""Reading mode: only the pages show, everything comes back as it was, and reading goes on as usual."""

import shutil
import sys

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.platforms import key
from zenith_pdf.settings import Settings
from zenith_pdf.ui import reading
from zenith_pdf.ui.document_view import HOLD_MS
from zenith_pdf.ui.widgets import FADE_MS


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


def faded():
    settle(FADE_MS + 120)


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    window.activateWindow()
    yield window
    window.reading_mode.leave()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, sample_pdf, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    QCursor.setPos(-2000, -2000)  # the pointer is off the pages: the middle of what is read is kept in place
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    settle()
    yield workspace
    window.reading_mode.leave()
    faded()
    assert not errors, errors
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)
    window.tool_settings.set_tool("select")


def chrome(window, workspace):
    """Everything reading mode hides, by name."""
    canvas = workspace.canvas
    return {"toolbar": workspace.toolbar, "counter": canvas.bars["counter"], "zoom": canvas.bars["zoom"],
            "undo": canvas.bars["undo"], "side panel": workspace.side, "tabs": window.top_bar}


def test_only_the_pages_show_and_everything_comes_back(window, workspace):
    view = workspace.view
    workspace.side.show_panel("outline")
    settle()
    shown = {name for name, widget in chrome(window, workspace).items() if widget.isVisible()}
    assert shown == set(chrome(window, workspace))
    window.setFocus()
    QTest.keySequence(window, QKeySequence(key("Ctrl+H")))
    faded()
    assert window.reading_mode.active
    assert not any(widget.isVisible() for widget in chrome(window, workspace).values())
    assert view.isVisible() and view.hasFocus()
    assert "Esc" in workspace.toast.text()  # once, saying how to leave; it fades by itself
    assert not window.isFullScreen()

    QTest.keyClick(view, Qt.Key.Key_Escape)
    faded()
    assert not window.reading_mode.active
    assert all(widget.isVisible() for widget in chrome(window, workspace).values())
    assert workspace.side.current == "outline"  # the same panel, open as it was
    assert not window.isFullScreen()

    workspace.side.hide_panel()
    window.reading_mode.toggle()
    window.reading_mode.toggle()  # Ctrl+H again leaves, even before the bars have faded
    faded()
    assert workspace.side.current is None and workspace.side.isVisible()
    assert all(bar.isVisible() and bar.graphicsEffect() is None for bar in (workspace.toolbar, workspace.zoom_bar))


def test_one_shortcut_and_three_ways_in(window, workspace):
    bound = [a for a in window.actions() if a.shortcut() == QKeySequence(key("Ctrl+H"))]
    assert len(bound) == 1  # nothing else answers to Ctrl+H
    workspace.zoom_bar.reading_button.click()
    assert window.reading_mode.active
    window.reading_mode.leave()
    window.menu_button.menu().aboutToShow.emit()
    assert window.reading_action.isEnabled() and not window.reading_action.isChecked()
    window.reading_action.trigger()
    assert window.reading_mode.active
    window.reading_mode.leave()
    faded()
    window.show_home()
    window.menu_button.menu().aboutToShow.emit()
    assert not window.reading_action.isEnabled()  # no pages to read on Home
    assert not window.reading_mode.enter()
    window._show_tab(window.workspaces.index(workspace))


def test_the_tool_and_the_selection_come_back(window, workspace):
    view, tools = workspace.view, window.tool_settings
    shape = Shape(page=0, rect=(100, 300, 120, 80))
    view.add_annotations([shape], "Shape")
    tools.set_tool("select")
    view.reveal_annotation(shape.id)
    settle()
    item = view._items[shape.id]
    assert workspace.selection_bar.isVisible() and item._handles and all(h.isVisible() for h in item._handles)
    window.reading_mode.enter()
    faded()
    assert view.selected_annotations() == [shape]  # still selected, only unmarked
    assert not workspace.selection_bar.isVisible() and not any(h.isVisible() for h in item._handles)
    assert view._tool_key() == "hand" and tools.tool == "select"
    assert view.dragMode() == view.DragMode.NoDrag
    QTest.keyClick(view, Qt.Key.Key_Delete)  # nothing shows as selected: Delete removes nothing unseen
    assert shape.id in workspace.store
    window.reading_mode.leave()
    faded()
    assert view.selected_annotations() == [shape] and workspace.selection_bar.isVisible()
    assert all(h.isVisible() for h in item._handles) and view.dragMode() == view.DragMode.RubberBandDrag

    tools.set_tool("pen")
    window.reading_mode.enter()
    assert view._tool_key() == "hand" and view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    window.reading_mode.leave()
    assert tools.tool == "pen" and view._tool_key() == "pen"


def test_the_line_read_stays_in_place(window, workspace):
    view = workspace.view
    workspace.side.show_panel("pages")
    settle()
    view.fit_width()
    view.goto_page(1)
    settle(60)
    before = view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.zoom
    middle = QPoint(view.viewport().width() // 2, (view.top_inset + view.viewport().height()) // 2)
    point, place = view.mapToScene(middle), view.viewport().mapToGlobal(middle)
    window.reading_mode.enter()
    faded()
    assert view.zoom > before[2]  # fitted to the width, now wider: the pages grow round the line read,
    now = view.viewport().mapToGlobal(view.mapFromScene(point))
    assert abs(now.y() - place.y()) <= 1  # which stays at its height on the screen
    page = view.mapFromScene(view._pages[1].sceneBoundingRect()).boundingRect()
    assert abs(page.center().x() - view.viewport().width() / 2) <= 8  # and the column of pages in the middle
    settle(HOLD_MS + 100)  # long after the pages settled
    window.reading_mode.leave()
    faded()
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value(), view.zoom) == before

    # Scrolled while reading: on leaving, the line read then stays where it is.
    window.reading_mode.enter()
    faded()
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 150)
    view.release_hold()  # as the wheel does
    middle = QPoint(view.viewport().width() // 2, (view.top_inset + view.viewport().height()) // 2)
    point, place = view.mapToScene(middle), view.viewport().mapToGlobal(middle)
    window.reading_mode.leave()
    faded()
    assert abs(view.viewport().mapToGlobal(view.mapFromScene(point)).y() - place.y()) <= 1


def test_links_and_keys_keep_working(window, links_pdf, tmp_path):
    path = tmp_path / "links.pdf"
    shutil.copy(links_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    view = workspace.view
    settle()
    window.reading_mode.enter()
    faded()
    link = view.mapFromScene(view._pages[0].mapToScene(QPointF(100, 792 - 710)))  # the explicit link to page 3
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, link)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, link)
    assert view.current_page == 2
    QTest.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert view.current_page == 0
    scrollbar = view.verticalScrollBar()
    start = scrollbar.value()
    QTest.keyClick(view, Qt.Key.Key_PageDown)
    assert scrollbar.value() > start
    QTest.keyClick(view, Qt.Key.Key_End)
    assert view.current_page == 2
    QTest.keyClick(view, Qt.Key.Key_Home)
    assert view.current_page == 0
    # A drag that starts on the link moves the pages and follows nothing.
    start = scrollbar.value()
    away = link + QPoint(0, -60)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, link)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(away), QPointF(view.viewport().mapToGlobal(away)),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), move)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, away)
    assert view.current_page == 0 and scrollbar.value() == start + 60
    window.reading_mode.leave()
    faded()
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_the_exit_chip_shows_at_the_top_edge_and_hides_again(window, workspace, monkeypatch):
    monkeypatch.setattr(reading, "CHIP_MS", 200)
    view, canvas = workspace.view, workspace.canvas
    window.reading_mode.enter()
    faded()
    chip = workspace.exit_chip()
    assert not chip.isVisible()  # nothing on screen until the pointer asks for it

    def move_to(y):
        point = QPointF(view.viewport().width() / 2, y)
        event = QMouseEvent(QEvent.Type.MouseMove, point, QPointF(view.viewport().mapToGlobal(point.toPoint())),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)

    move_to(300)
    settle()
    assert not chip.isVisible()
    move_to(2)  # the top edge
    faded()
    assert chip.isVisible() and chip.geometry().top() < 40
    assert abs(chip.geometry().center().x() - canvas.width() // 2) <= 1
    move_to(300)  # away from it, then still
    settle(200 + FADE_MS + 150)
    assert not chip.isVisible()
    move_to(2)
    faded()
    chip.button.click()
    assert not window.reading_mode.active
    faded()
    assert not chip.isVisible()


def test_it_can_go_full_screen_and_back(window, workspace):
    window.settings.set("reading_full_screen", True)
    try:
        window.reading_mode.enter()
        settle(60)
        assert window.isFullScreen()
        window.reading_mode.leave()
        settle(60)
        assert not window.isFullScreen()
        window.set_full_screen(True)  # already full screen: reading mode leaves it so
        window.reading_mode.enter()
        window.reading_mode.leave()
        assert window.isFullScreen()
        window.set_full_screen(False)
    finally:
        window.settings.set("reading_full_screen", False)
    settle(60)


def test_a_pinned_tool_menu_waits_and_comes_back(window, workspace):
    toolbar = workspace.toolbar
    window.tool_settings.set_tool("rectangle")
    toolbar.open_menu("rectangle")
    menu = toolbar.tool_menu
    menu.pin_button.click()
    settle()
    assert menu.isVisible() and menu.pinned
    try:
        window.reading_mode.enter()
        assert not menu.isVisible()  # at once, not after the bars have faded
        faded()
        window.reading_mode.leave()
        faded()
        assert menu.isVisible() and menu.key == "rectangle"
    finally:
        menu.pin_button.click()
        menu.close_menu()
        window.tool_settings.set_tool("select")


def test_a_pinned_menu_comes_back_for_the_tool_on_now_and_on_other_tabs(window, workspace, sample_pdf, tmp_path):
    """While it is away (reading, another tab), a pinned menu follows the tools turned on, as it does in sight;
    another tab's toolbar brings one up of its own; and it stays under its button when the side panel opens."""
    tools, toolbar = window.tool_settings, workspace.toolbar
    tools.set_tool("rectangle")
    toolbar.open_menu("rectangle")
    menu = toolbar.tool_menu
    menu.pin_button.click()
    other = tmp_path / "other.pdf"
    shutil.copy(sample_pdf, other)
    try:
        window.reading_mode.enter()
        faded()
        tools.set_tool("select")  # V, while reading
        window.reading_mode.leave()
        faded()
        assert menu.isVisible() and menu.key == "select"
        tools.set_tool("rectangle")
        window.open_file(other)
        second = window.current_workspace()
        settle()
        assert second.toolbar.tool_menu is not None and second.toolbar.tool_menu.isVisible()
        assert second.toolbar.tool_menu.key == "rectangle" and not menu.isVisible()
        tools.set_tool("ellipse")
        window.close_tab(window.workspaces.index(second), confirm=False)
        settle()
        assert menu.isVisible() and menu.key == "ellipse"
        placed = []
        real = menu.reposition
        menu.reposition = lambda *a, **k: (placed.append(True), real(*a, **k))
        try:
            for panel in (None, "pages", None):  # the canvas, and the toolbar on it, move: the menu goes with them
                placed.clear()
                workspace.side.show_panel(panel) if panel else workspace.side.hide_panel()
                settle()
                assert placed
        finally:
            del menu.reposition
    finally:
        menu.pin_button.click()
        menu.close_menu()
        tools.set_tool("select")


def test_reading_from_the_page_manager_reads_the_page_picked_and_goes_back_to_it(window, workspace):
    workspace.toggle_page_manager()
    workspace.page_manager.select_rows([2])
    window.reading_mode.enter()
    assert workspace.page_manager is None and workspace.view.current_page == 2
    faded()
    window.reading_mode.leave()
    faded()
    assert workspace.page_manager is not None and workspace.page_manager.current_page() == 2
    assert not workspace.toolbar.isVisible() and workspace.side.current is None  # as it was before reading
    workspace.toggle_page_manager()
    assert workspace.view.isVisible() and workspace.side.current == "pages"


def test_the_full_screen_setting_applies_while_reading(window, workspace):
    window.reading_mode.enter()
    window.settings.set("reading_full_screen", True)
    assert window.isFullScreen()
    window.settings.set("reading_full_screen", False)
    assert not window.isFullScreen() and window.reading_mode.active
    window.reading_mode.leave()


def test_another_tab_is_read_too_and_home_ends_it(window, workspace, sample_pdf, tmp_path):
    other = tmp_path / "other.pdf"
    shutil.copy(sample_pdf, other)
    window.reading_mode.enter()
    faded()
    window.open_file(other)
    second = window.current_workspace()
    faded()
    assert second is not workspace and window.reading_mode.workspace is second
    assert not second.side.isVisible() and not second.toolbar.isVisible() and not window.top_bar.isVisible()
    assert workspace.side.isVisibleTo(workspace) and not workspace.view.reading  # the first tab is as it was
    window.show_home()
    assert not window.reading_mode.active and window.top_bar.isVisible()
    window.close_tab(window.workspaces.index(second), confirm=False)
    window._show_tab(window.workspaces.index(workspace))
    faded()
    assert workspace.toolbar.isVisible()


def test_search_and_the_page_box_bring_the_bars_back(window, workspace):
    window.reading_mode.enter()
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert not window.reading_mode.active and workspace.side.current == "search"
    faded()
