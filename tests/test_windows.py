"""More than one window: a tab dragged out of the strip, moved to another window, and what windows share."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui.main_window import MainWindow
from zenith_pdf.ui.tab_drag import THRESHOLD


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.move(40, 40)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for other in [w for w in MainWindow.open_windows if w is not window]:
        for index in reversed(range(len(other.workspaces))):
            other.close_tab(index, confirm=False)
        other.close()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()
    settle()


def open_copies(window, sample_pdf, tmp_path, count):
    for number in range(count):
        path = tmp_path / f"doc{number}.pdf"
        shutil.copy(sample_pdf, path)
        window.open_file(path)
    settle()
    return list(window.workspaces)


def test_a_new_window_shares_tools_and_library(window):
    other = window.new_window()
    settle()
    assert other in MainWindow.open_windows and other.isVisible()
    assert other.tool_settings is window.tool_settings and other.library is window.library
    window.tool_settings.set_tool("rectangle")
    assert other.tool_settings.tool == "rectangle"
    other.close()
    settle()
    assert other not in MainWindow.open_windows


def test_move_to_new_window_keeps_the_document_working(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    new = window.tear_off(second, QPoint(300, 200))
    settle(60)
    assert new is not window and new.isVisible()
    assert window.workspaces == [first] and new.workspaces == [second]
    assert new.current_workspace() is second and second.window() is new
    assert new.tabs.tabText(0).startswith("doc1")
    # Its buttons now speak to the new window, not the old one.
    second.settings_requested.emit()
    settle()
    assert new._settings_window is not None and new._settings_window.isVisible()
    assert window._settings_window is None or not window._settings_window.isVisible()
    new._settings_window.hide()
    # And it still edits and undoes as before.
    second.tool_settings.set_tool("select")
    second.actions.rotate(0, 90)
    assert second.undo_stack.canUndo()
    second.undo_stack.undo()
    assert second.document.page_count == 4


def test_the_only_tab_moves_its_window_instead(window, sample_pdf, tmp_path):
    (only,) = open_copies(window, sample_pdf, tmp_path, 1)
    assert window.tear_off(only, QPoint(200, 150)) is window
    assert window.workspaces == [only]
    assert len([w for w in MainWindow.open_windows if w.isVisible()]) == 1


def test_a_tab_moves_to_another_window_and_an_empty_window_closes(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    other = window.tear_off(second, QPoint(400, 300))
    settle()
    window.move_tab(first, other, 0)  # before its tab
    settle(60)
    assert other.workspaces == [first, second]
    assert not window.isVisible()  # left with nothing, it closed
    assert window not in MainWindow.open_windows
    assert other.current_workspace() is first


def test_closing_one_of_two_windows_keeps_the_session_of_the_last(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    window.settings.set("session", {"marker": True})
    other = window.tear_off(second, QPoint(400, 300))
    settle()
    other.close()  # another window stays open: the session is not this one's to write
    settle()
    assert window.settings.get("session") == {"marker": True}
    assert second not in window.workspaces


def send(widget, kind, local, buttons):
    button = Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, QPointF(local), QPointF(widget.mapToGlobal(local)), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def test_dragging_a_tab_out_of_the_strip_opens_a_window(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    tabs = window.tabs
    start = tabs.tabRect(1).center()
    left = Qt.MouseButton.LeftButton
    send(tabs, QEvent.Type.MouseButtonPress, start, left)
    send(tabs, QEvent.Type.MouseMove, start + QPoint(10, 0), left)  # along the strip: the tab bar's own move
    assert not window._dragger.dragging
    below = QPoint(start.x() + 40, tabs.height() + THRESHOLD + 30)
    send(tabs, QEvent.Type.MouseMove, below, left)
    assert window._dragger.dragging and window._dragger.ghost is not None and window._dragger.ghost.isVisible()
    outside = tabs.mapFromGlobal(window.frameGeometry().bottomRight() + QPoint(80, 80))
    send(tabs, QEvent.Type.MouseMove, outside, left)
    send(tabs, QEvent.Type.MouseButtonRelease, outside, Qt.MouseButton.NoButton)
    settle(80)
    windows = [w for w in MainWindow.open_windows if w.isVisible()]
    assert len(windows) == 2
    new = next(w for w in windows if w is not window)
    assert new.workspaces == [second] and window.workspaces == [first]
    assert window._dragger.ghost is None


def test_a_drag_dropped_back_on_its_window_changes_nothing(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    tabs = window.tabs
    start = tabs.tabRect(1).center()
    left = Qt.MouseButton.LeftButton
    send(tabs, QEvent.Type.MouseButtonPress, start, left)
    below = QPoint(start.x(), tabs.height() + THRESHOLD + 60)  # still over the window
    send(tabs, QEvent.Type.MouseMove, below, left)
    send(tabs, QEvent.Type.MouseButtonRelease, below, Qt.MouseButton.NoButton)
    settle()
    assert window.workspaces == [first, second]
    assert len([w for w in MainWindow.open_windows if w.isVisible()]) == 1


def test_the_tab_menu_offers_a_new_window(window, sample_pdf, tmp_path):
    first, second = open_copies(window, sample_pdf, tmp_path, 2)
    menu = window.tab_menu(0)
    actions = {a.text(): a for a in menu.actions()}
    assert actions["Move to new window"].isEnabled() and actions["Close other tabs"].isEnabled()
    assert actions["Move to new window"].toolTip()  # explained, like every menu
    actions["Move to new window"].trigger()
    settle()
    assert first not in window.workspaces and window.workspaces == [second]
    menu.deleteLater()
    assert window.tab_menu(5) is None
