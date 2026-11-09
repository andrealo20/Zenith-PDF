"""The window's chrome: the rail's marker, the buttons that fade, the titles and lists of the panels, the dot of a
tab with unsaved changes, and the ghost and the mark of a tab dragged out. The suite runs with motion off; a test of
an animation asks for it with motion_on."""

import sys
import threading

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QEnterEvent, QImage, QMouseEvent, QPixmap, QResizeEvent, QUndoCommand
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QTabBar,
    QTreeWidgetItem,
    QWidget,
)

from conftest import settle, wait_for
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.theme import luminance, qcolor, themes
from zenith_pdf.ui import motion, outline, tab_drag
from zenith_pdf.ui.page_manager import TINT, _chosen_label
from zenith_pdf.ui.side_panel import CLEAR, PAD, Arrivals, FadingButton, RowFades, panel_header
from zenith_pdf.ui.tab_drag import GHOST_MARGIN, GHOST_OFFSET, GHOST_WIDTH, DropMark, TabGhost
from zenith_pdf.ui.thumbnails import ARRIVAL_ROLE
from zenith_pdf.ui.widgets import FloatingBar
from zenith_pdf.ui.workspace import SnappingPopup


@pytest.fixture(autouse=True)
def no_errors_in_handlers(monkeypatch):
    """Qt calls the paint and event handlers, and prints what they raise: here it fails the test."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: seen.append(f"{kind.__name__}: {value}"))
    yield
    assert not seen


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    settings.set("side_panel", "always")
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.move(40, 40)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for other in [w for w in window.open_windows if w is not window]:
        for index in reversed(range(len(other.workspaces))):
            other.close_tab(index, confirm=False)
        other.close()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()
    settle()


@pytest.fixture
def workspace(window, sample_pdf):
    window.open_file(sample_pdf)
    settle(60)
    return window.current_workspace()


def pixel(widget, x: int, y: int) -> tuple[int, int, int, int]:
    return widget.grab().toImage().pixelColor(x, y).getRgb()


def near(first, second, by: int = 2) -> bool:
    return all(abs(a - b) <= by for a, b in zip(first, second))


# The rail ---------------------------------------------------------------------------------------------------


def titles(workspace) -> dict[str, QPoint]:
    """Where each panel's title stands, in the side panel's own coordinates."""
    side, where = workspace.side, {}
    for key in ("pages", "outline", "search", "annotations"):
        side.show_panel(key)
        settle()
        label = next(w for w in side._panels[key].findChildren(QLabel) if w.objectName() == "PanelTitle")
        where[key] = label.mapTo(side, QPoint(0, 0))
    return where


def test_the_titles_of_the_panels_stand_in_one_place(workspace):
    where = titles(workspace)
    assert len(set((p.x(), p.y() // 2) for p in where.values())) == 1  # one column, in one row (a font may differ)
    assert min(p.x() for p in where.values()) == 48 + PAD  # the rail's width, then the title's margin


def test_the_rail_marker_is_on_the_button_of_the_open_panel(workspace):
    side = workspace.side
    side.show_panel("search")
    settle()
    assert side._rail._rect() == QRectF(side._buttons["search"].geometry()) and not side._rail._slide.running
    side.show_panel("annotations")  # motion off: it is there at once
    assert side._rail._rect() == QRectF(side._buttons["annotations"].geometry()) and not side._rail._slide.running


def test_the_rail_marker_slides_from_one_button_to_the_next(workspace, motion_on):
    side, rail = workspace.side, workspace.side._rail
    side.show_panel("pages")
    settle(300)
    first, second = (side._buttons[key].geometry() for key in ("pages", "search"))
    side.show_panel("search")
    assert rail._slide.running and rail._rect().top() == first.top()  # it starts where it was
    rail._slide._animation.setCurrentTime(90)
    assert first.top() < rail._rect().top() < second.top()
    assert wait_for(lambda: not rail._slide.running, 5)
    assert rail._rect() == QRectF(second)


def test_the_marker_appears_where_a_panel_opens_and_fades_when_it_closes(workspace, motion_on):
    side, rail = workspace.side, workspace.side._rail
    side.hide_panel()
    assert wait_for(lambda: rail._level.value == 0.0, 5)
    side.show_panel("annotations")
    assert rail._rect() == QRectF(side._buttons["annotations"].geometry())  # no slide from the one before
    assert wait_for(lambda: rail._level.value == 1.0, 5)
    side.hide_panel()
    assert rail._level.running and rail._level.target == 0.0
    assert wait_for(lambda: rail._level.value == 0.0, 5)


def test_a_panel_opens_at_its_size_and_only_its_picture_comes(workspace, motion_on):
    side = workspace.side
    side.hide_panel()
    settle()
    side.show_panel("pages")
    assert side.current == "pages" and side._stack.isVisible() and side._stack.width() == side.panel_width()
    assert isinstance(side._stack.graphicsEffect(), QGraphicsOpacityEffect)  # a veil, while it comes
    assert wait_for(lambda: side._stack.graphicsEffect() is None, 5)


def test_a_panel_switched_to_fades_in_and_nothing_else_does(workspace, motion_on):
    side = workspace.side
    side.show_panel("pages")
    settle(300)
    side.show_panel("search")
    assert isinstance(workspace.search.graphicsEffect(), QGraphicsOpacityEffect)
    assert side._stack.graphicsEffect() is None and workspace.thumbnails.graphicsEffect() is None
    assert wait_for(lambda: workspace.search.graphicsEffect() is None, 5)


def test_with_motion_off_a_panel_comes_with_nothing_on_it(workspace):
    side = workspace.side
    side.hide_panel()
    side.show_panel("pages")
    side.show_panel("search")
    assert side._stack.graphicsEffect() is None and workspace.search.graphicsEffect() is None
    assert not side._rail._level.running and not side._rail._slide.running


def test_the_edge_of_the_panel_lights_under_the_pointer(workspace, motion_on):
    grip = workspace.side._grip
    assert grip.isVisible() and grip._glow.colour.alpha() == 0
    grip.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert grip._glow.target.alpha() > 0
    assert wait_for(lambda: grip._glow.colour == grip._glow.target, 5)
    grip.leaveEvent(None)
    assert grip._glow.target.alpha() == 0
    assert wait_for(lambda: grip._glow.colour.alpha() == 0, 5)


# Buttons that fade ------------------------------------------------------------------------------------------


@pytest.fixture
def button(qt_app):
    """A fading button on the window's own colour, in Zenith's dark theme with its style sheet on, as in the app."""
    before = themes.current.name, themes.style.key
    themes.choose("dark", "blue", "zenith")
    page = QWidget()
    page.resize(120, 80)
    one = FadingButton("plus", "Add", checkable=True, parent=page)
    one.move(20, 20)
    page.show()
    settle()
    yield one
    page.close()
    page.deleteLater()
    themes.choose(before[0], "blue", before[1])


def hover(button, on: bool) -> None:
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, on)  # as the pointer does, without a screen to point at


def test_a_fading_buttons_fills_are_drawn_once_in_the_themes_own_colours(button):
    t, middle = themes.current, button.height() // 2
    assert pixel(button, 3, middle)[3] == 0  # nothing at rest
    hover(button, True)
    assert near(pixel(button, 3, middle), qcolor(t.hover).getRgb())  # its alpha, not twice it: the sheet's is off
    hover(button, False)
    button.setChecked(True)
    assert near(pixel(button, 3, middle), qcolor(t.selection).getRgb())
    button.setChecked(False)
    assert pixel(button, 3, middle)[3] == 0


def test_a_fading_buttons_fill_eases_in_and_out(button, motion_on):
    t = themes.current
    hover(button, True)
    button.grab()
    assert button._shade.running and button._shade.colour.alpha() < qcolor(t.hover).alpha()
    assert wait_for(lambda: not button._shade.running, 5) and button._shade.colour == qcolor(t.hover)
    hover(button, False)
    button.grab()
    assert button._shade.running and button._shade.colour.alpha() > 0  # it lingers a moment as the pointer goes
    assert wait_for(lambda: not button._shade.running, 5) and button._shade.colour.alpha() == 0


def test_a_disabled_fading_button_has_no_fill(button):
    hover(button, True)
    button.setEnabled(False)
    rest = pixel(button, 3, button.height() // 2)
    button.setEnabled(True)
    button.setEnabled(False)
    assert near(pixel(button, 3, button.height() // 2), rest) and button._shade.colour.alpha() == 0


def test_a_fading_button_on_a_floating_bar_leaves_the_fills_to_the_bar(qt_app):
    page = QWidget()
    page.resize(200, 100)
    bar = FloatingBar(page)
    layout = QHBoxLayout(bar)
    one = FadingButton("plus", "Add", checkable=True)
    layout.addWidget(one)  # put on the bar after it was made
    bar.move(10, 10)
    bar.adjustSize()
    page.show()
    settle()
    assert not one._owns and one.styleSheet() == ""
    one.setChecked(True)
    assert pixel(one, 3, one.height() // 2)[3] > 0  # the bar's sheet draws the chosen fill, not transparent
    other = FadingButton("plus", "Add", parent=page)
    assert other._owns and other.styleSheet() == FadingButton.SHEET
    page.close()
    page.deleteLater()


# The fills and pictures of lists ----------------------------------------------------------------------------


@pytest.fixture
def listing(qt_app):
    view = QListWidget()
    view.resize(120, 120)
    view.show()
    settle()
    yield view
    view.close()
    view.deleteLater()


def test_a_rows_fill_lands_at_once_with_motion_off(listing):
    fades, want = RowFades(listing), QColor(10, 120, 240, 60)
    assert fades.colour(2, want) == want
    assert fades.colour(2, CLEAR).alpha() == 0


def test_a_rows_fill_eases_in_and_out_and_is_forgotten(listing, motion_on):
    fades, want = RowFades(listing), QColor(10, 120, 240, 60)
    assert fades.colour(2, CLEAR).alpha() == 0 and not fades._fills  # nothing shown, nothing kept
    assert fades.colour(2, want).alpha() < 60 and len(fades._fills) == 1
    assert wait_for(lambda: fades.colour(2, want) == want, 5)
    fades.colour(2, CLEAR)
    assert wait_for(lambda: not fades._fills[2].running, 5) and fades.colour(2, CLEAR).alpha() == 0
    for row in range(fades.KEPT + 1):  # enough rows that the finished ones are let go
        fades.colour(100 + row, want)
    assert 2 not in fades._fills


def test_rows_that_stay_chosen_are_not_looked_over_at_every_paint(listing):
    fades, want, looks = RowFades(listing), QColor(10, 120, 240, 60), []
    for row in range(600):  # every page of a long document chosen, and scrolled past
        fades.colour(row, want)
    original = fades._forget_done
    fades._forget_done = lambda: (looks.append(1), original())
    for _ in range(100):  # the cells of a paint
        fades.colour(599, want)
    assert not looks and len(fades._fills) == 600


def test_the_fills_of_rows_that_were_made_again_are_dropped(listing, motion_on):
    fades = RowFades(listing)
    fades.colour(0, QColor(10, 120, 240, 60))
    fades.reset()
    assert not fades._fills


def test_a_picture_fades_in_as_it_arrives(listing, motion_on):
    arrivals, rows = Arrivals(listing), []  # owned, as in the app: a bare QObject with a lambda on it dies in a GC
    arrivals.changed.connect(rows.append)
    assert arrivals.level(4) == 1.0
    arrivals.arrive(4)
    assert arrivals.level(4) < 1.0
    assert wait_for(lambda: arrivals.level(4) == 1.0, 5) and 4 in rows and not arrivals._fades


def test_a_picture_arrives_at_once_with_motion_off(listing):
    arrivals = Arrivals(listing)
    arrivals.arrive(4)
    assert arrivals.level(4) == 1.0 and not arrivals._fades


def test_a_page_drawn_for_the_first_time_fades_in_and_one_drawn_again_does_not(workspace, motion_on):
    model = workspace.thumbnails.model
    model.reload()
    image = QImage(60, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    index = model.index(1)
    model._on_rendered((model._token, 1), 1.0, image)
    assert model.data(index, ARRIVAL_ROLE) < 1.0
    assert wait_for(lambda: model.data(index, ARRIVAL_ROLE) == 1.0, 5)
    model._on_rendered((model._token, 1), 1.0, image)  # at another size: the old one was there
    assert model.data(index, ARRIVAL_ROLE) == 1.0


def test_a_page_that_fades_in_is_painted_again_and_not_announced_as_changed(workspace, motion_on):
    model = workspace.thumbnails.model
    model.reload()
    image = QImage(60, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    announced, steps = [], []
    model.dataChanged.connect(lambda first, last, roles: announced.extend(roles))
    model.fading.connect(steps.append)
    model._on_rendered((model._token, 1), 1.0, image)
    assert wait_for(lambda: model.data(model.index(1), ARRIVAL_ROLE) == 1.0, 5)
    # A list of rows of different heights measures every one of them again for each change it is told of, which on
    # a long document is a frame lost at every step of every fade.
    assert steps and ARRIVAL_ROLE not in announced


def test_the_bookmark_that_is_chosen_has_one_fill_across_its_whole_row(window, links_pdf):
    window.open_file(links_pdf)
    settle(60)
    workspace = window.current_workspace()
    workspace.side.show_panel("outline")
    tree = workspace.outline.tree
    item = tree.topLevelItem(0)
    tree.setCurrentItem(item)
    settle()
    y = tree.visualItemRect(item).center().y()
    rest = pixel(tree.viewport(), 1, y)  # the text starts 16 px in: these are all in the indent, or past the text
    fill = [pixel(tree.viewport(), x, y) for x in (4, 8, 10, 11, 12, 13, 14, tree.viewport().width() - 5)]
    assert all(near(colour, fill[0]) for colour in fill) and not near(fill[0], rest)  # no seam between the pieces


def test_the_bookmark_under_the_pointer_is_lit_and_let_go_when_it_leaves(window, links_pdf):
    window.open_file(links_pdf)
    settle(60)
    workspace = window.current_workspace()
    workspace.side.show_panel("outline")
    tree = workspace.outline.tree
    item = tree.topLevelItem(0)
    settle()
    y = tree.visualItemRect(item).center().y()
    assert pixel(tree.viewport(), 6, y)[3] == 0
    point = QPointF(40, y)
    move = QMouseEvent(QEvent.Type.MouseMove, point, QPointF(tree.viewport().mapToGlobal(point.toPoint())),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(tree.viewport(), move)
    assert tree._hover == tree.indexFromItem(item).internalId()
    assert near(pixel(tree.viewport(), 6, y), qcolor(themes.current.hover).getRgb())
    QApplication.sendEvent(tree.viewport(), QEvent(QEvent.Type.Leave))  # onto the scroll bar, say
    assert tree._hover == 0 and pixel(tree.viewport(), 6, y)[3] == 0


def test_the_bookmark_under_a_resting_pointer_changes_as_the_rows_scroll_by(window, links_pdf, monkeypatch):
    window.open_file(links_pdf)
    settle(60)
    workspace = window.current_workspace()
    workspace.side.show_panel("outline")
    tree = workspace.outline.tree
    for number in range(80):
        QTreeWidgetItem(tree, [f"Chapter {number}"])
    settle()
    point = QPoint(40, 60)
    cursor = type("Cursor", (), {"pos": staticmethod(lambda: tree.viewport().mapToGlobal(point))})
    monkeypatch.setattr(outline, "QCursor", cursor)  # a pointer that rests where it is, whatever scrolls
    tree.viewport().setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    bar = tree.verticalScrollBar()
    before = tree.indexAt(point).internalId()
    bar.setValue(bar.maximum() // 2)
    assert tree.indexAt(point).internalId() != before and tree._hover == tree.indexAt(point).internalId()
    tree.viewport().setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    lit = tree._hover
    bar.setValue(0)
    assert tree._hover == lit  # a pointer that is elsewhere does not light what scrolls under where it was


def test_a_list_with_nothing_in_it_says_so(window, workspace):
    side = workspace.side
    for key, empty in (("outline", workspace.outline.empty), ("annotations", workspace.annotations.empty)):
        side.show_panel(key)
        settle()
        assert empty.isVisible() and empty.width() > 100  # in the room the list would have, not a line at the top


def test_a_header_has_its_title_and_an_action(qt_app):
    action = QWidget()
    row = panel_header("Pages", action)
    assert row.height() == 44 and action.parentWidget() is row
    assert row.layout().contentsMargins().left() == PAD


# The search's line of light ---------------------------------------------------------------------------------


def test_a_line_of_light_runs_while_a_search_does(workspace, motion_on):
    search = workspace.search
    workspace.side.show_panel("search")
    search.field.setText("needle")
    search.start()
    assert search.results.busy.isVisible()
    search.results.busy.grab()  # the light, painted
    assert wait_for(lambda: search.status.text() == "4 results", 20)
    assert not search.results.busy.isVisible()
    search.field.setText("")
    search.start()
    assert not search.results.busy.isVisible()


def test_with_motion_off_the_status_line_alone_says_the_search_runs(workspace):
    search = workspace.search
    workspace.side.show_panel("search")
    search.field.setText("needle")
    search.start()
    assert not search.results.busy.isVisible()
    assert wait_for(lambda: search.status.text() == "4 results", 20)


def test_a_search_that_fails_still_ends_its_line_of_light(workspace, motion_on, monkeypatch):
    def broken(*_args):
        raise RuntimeError("a page that cannot be read")
        yield

    monkeypatch.setattr(workspace.document, "iter_search", broken)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the worker's own error is not what is tested
    search = workspace.search
    workspace.side.show_panel("search")
    search.field.setText("needle")
    search.start()
    assert wait_for(lambda: search.status.text() == "No results", 20)
    assert not search.results.busy.isVisible() and not search.results.busy._tick.isActive()


def test_the_line_of_light_ticks_only_while_it_is_on_screen(workspace, motion_on):
    search, busy = workspace.search, workspace.search.results.busy
    workspace.side.show_panel("search")
    search.field.setText("needle")
    search.start()
    assert busy.isVisible() and busy._tick.isActive()
    workspace.side.show_panel("pages")  # switched away while the search runs: nobody sees a frame of it
    assert not busy._tick.isActive()
    workspace.side.show_panel("search")
    assert busy._tick.isActive()
    assert wait_for(lambda: not busy.isVisible() and not busy._tick.isActive(), 20)


def test_the_search_options_stand_one_over_the_other_when_their_labels_do_not_fit_a_row(workspace):
    search = workspace.search
    workspace.side.show_panel("search")
    short = "Aa"  # fits beside the other with any system font; English does not on macOS
    search.match_case.setText(short)
    QApplication.sendEvent(search, QResizeEvent(search.size(), search.size()))
    settle()
    assert search.match_case.y() == search.whole_word.y()
    search.match_case.setText("A label as long as some languages have for it, which no row of the panel holds")
    QApplication.sendEvent(search, QResizeEvent(search.size(), search.size()))
    settle()
    assert search.whole_word.y() > search.match_case.y() and search.whole_word.x() == search.match_case.x()
    search.match_case.setText(short)
    QApplication.sendEvent(search, QResizeEvent(search.size(), search.size()))
    settle()
    assert search.match_case.y() == search.whole_word.y()


# The page manager --------------------------------------------------------------------------------------------


def test_the_number_of_a_chosen_page_reads_on_its_card_in_every_style(qt_app):
    before = themes.current.name, themes.style.key
    try:
        for style in ("zenith", "dot", "neon", "material"):
            for scheme in ("dark", "light"):
                themes.choose(scheme, "blue", style)
                t = themes.current
                ring = t.focus or t.accent
                card = motion.mixed(QColor(t.canvas), QColor(ring), TINT)
                label = QColor(_chosen_label(t.canvas, ring, t.accent, t.text))
                light, dark = sorted((luminance(label), luminance(card)), reverse=True)
                assert (light + 0.05) / (dark + 0.05) >= 4.4, (style, scheme)  # 4.5 by the icons' own measure
    finally:
        themes.choose(before[0], "blue", before[1])


def test_the_manager_comes_in_as_it_opens_and_not_again_when_its_tab_is_shown(window, sample_pdf, tmp_path, motion_on):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    window.open_file(copy)
    settle(60)
    workspace = window.current_workspace()
    workspace.toggle_page_manager()
    manager = workspace.page_manager
    assert manager.isVisible() and isinstance(manager.graphicsEffect(), QGraphicsOpacityEffect)
    assert wait_for(lambda: manager.graphicsEffect() is None, 5)
    window.tabs.setCurrentIndex(0)
    settle()
    window.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert manager.isVisible() and manager.graphicsEffect() is None  # shown again, but nothing opens
    workspace.toggle_page_manager()


# The page counter's chip ------------------------------------------------------------------------------------


def test_the_back_chip_fades_in_as_the_bar_widens(workspace, motion_on):
    counter = workspace.page_counter
    assert not counter.back_button.isVisible()
    workspace.view.jump(2)
    assert counter.back_button.isVisible() and isinstance(counter.back_button.graphicsEffect(), QGraphicsOpacityEffect)
    assert wait_for(lambda: counter.back_button.graphicsEffect() is None, 5)
    workspace.view.jump(3)  # the chip is there already: nothing new comes
    assert counter.back_button.graphicsEffect() is None


def test_unfolding_the_zoom_bar_fades_in_the_controls_that_come(workspace, motion_on):
    bar = workspace.zoom_bar
    bar.set_compact(True)
    settle(300)
    assert not bar.view_button.isVisible()
    bar.set_compact(False)
    assert bar.view_button.isVisible() and isinstance(bar.view_button.graphicsEffect(), QGraphicsOpacityEffect)
    assert bar.zoom_button.graphicsEffect() is None  # the zoom was there already
    assert wait_for(lambda: bar.view_button.graphicsEffect() is None, 5)


def test_the_zoom_bars_popups_rise_out_of_it(workspace, motion_on):
    workspace.zoom_bar._show_snapping()
    popup = workspace.zoom_bar.findChild(SnappingPopup)
    assert popup.isVisible() and popup.windowOpacity() < 1.0
    assert wait_for(lambda: popup.windowOpacity() == 1.0, 5)
    popup.close()


# Tabs --------------------------------------------------------------------------------------------------------


def close_button(window, index: int):
    return window.tabs.tabButton(index, QTabBar.ButtonPosition.RightSide)


def test_a_tab_with_unsaved_changes_shows_a_dot_where_its_cross_is(window, workspace):
    button = close_button(window, 0)
    assert not button._dirty and window.tabs.tabText(0) == workspace.document.path.stem
    workspace.undo_stack.push(QUndoCommand("edit"))
    assert button._dirty and window.tabs.tabText(0) == workspace.document.path.stem  # the mark is not in the title
    assert window.windowTitle().startswith(f"{workspace.document.path.name} •")  # the window's still is
    middle = button.rect().center()
    dot = pixel(button, middle.x(), middle.y())
    hover(button, True)
    cross = button.grab().toImage()
    hover(button, False)
    assert near(dot, QColor(themes.current.text).getRgb(), 4)  # the dot, in the text colour, at its centre
    assert cross != button.grab().toImage()  # the pointer brings the cross
    workspace.undo_stack.setClean()
    assert not button._dirty and pixel(button, middle.x(), middle.y()) != dot


def test_every_tab_has_its_own_dot(window, sample_pdf, tmp_path):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    window.open_file(copy)
    settle(60)
    window.workspaces[1].undo_stack.push(QUndoCommand("edit"))
    assert [close_button(window, i)._dirty for i in (0, 1)] == [False, True]


# The dragged tab ---------------------------------------------------------------------------------------------


def test_the_ghost_hangs_below_and_right_of_the_pointer_with_room_for_its_shadow(qt_app):
    ghost = TabGhost(QPixmap(300, 200), "A document")
    assert ghost.width() == GHOST_WIDTH + 12 + 2 * GHOST_MARGIN
    ghost.place(QPoint(100, 100))
    card = ghost.pos() + QPoint(GHOST_MARGIN, GHOST_MARGIN)
    assert card == QPoint(100, 100) + GHOST_OFFSET  # the card is where it always was; the shadow is round it
    assert ghost.grab().toImage().pixelColor(2, 2).alpha() == 0  # clear of the card, in the margin
    ghost.deleteLater()


def test_a_tab_over_another_window_shows_where_it_would_drop(window, sample_pdf, tmp_path):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    other = window.new_window()
    other.open_file(sample_pdf)
    other.open_file(copy)
    settle(60)
    mark = DropMark()
    first = other.tabs.tabRect(0)
    on_first = other.tabs.mapToGlobal(first.center())
    mark.place(other, on_first)
    assert mark.isVisible() and mark.parentWidget() is other.top_bar
    assert mark.x() + tab_drag.MARK_WIDTH // 2 == other.tabs.mapTo(other.top_bar, first.topLeft()).x()  # before it
    after_all = other.tabs.mapToGlobal(QPoint(other.tabs.width() + 400, first.center().y()))
    mark.place(other, after_all)
    last = other.tabs.tabRect(1)
    assert mark.x() + tab_drag.MARK_WIDTH // 2 == other.tabs.mapTo(other.top_bar, QPoint(last.right() + 1, 0)).x()
    mark.deleteLater()


def test_the_mark_follows_the_drag_and_goes_when_it_ends(window, sample_pdf, tmp_path, monkeypatch):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    window.open_file(copy)
    other = window.new_window()
    other.open_file(sample_pdf.with_name("sample.pdf"))
    settle(60)
    dragger = window._dragger
    dragger.index, dragger.dragging = 1, True
    dragger.ghost, dragger.mark = TabGhost(window.workspaces[1].grab(), "copy"), DropMark()
    monkeypatch.setattr(tab_drag, "window_at", lambda pointer: other)
    dragger.follow(other.tabs.mapToGlobal(other.tabs.tabRect(0).center()))
    assert dragger.mark.isVisible()
    monkeypatch.setattr(tab_drag, "window_at", lambda pointer: window)  # back over its own window: nothing to mark
    dragger.follow(window.tabs.mapToGlobal(QPoint(5, 5)))
    assert not dragger.mark.isVisible()
    mark = dragger.mark
    monkeypatch.setattr(tab_drag, "window_at", lambda pointer: window)
    dragger.finish(window.tabs.mapToGlobal(QPoint(5, 5)))
    assert dragger.mark is None and dragger.ghost is None and not mark.isVisible()
    assert window.workspaces and len(window.workspaces) == 2  # dropped on its own window: nothing moved


def test_the_ghost_fades_in_where_it_is_with_motion_on(window, sample_pdf, tmp_path, motion_on):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    window.open_file(copy)
    settle(60)
    dragger = window._dragger
    dragger.index = 1
    start = QPoint(300, 300)
    event = type("Event", (), {"position": lambda s: QPointF(0, 0), "globalPosition": lambda s: QPointF(start),
                               "modifiers": lambda s: Qt.KeyboardModifier.NoModifier})()
    dragger.start(start, event)
    ghost = dragger.ghost
    assert ghost.isVisible() and ghost.windowOpacity() < 1.0
    assert wait_for(lambda: ghost.windowOpacity() == 1.0, 5) and ghost.pos() == start + GHOST_OFFSET - QPoint(
        GHOST_MARGIN, GHOST_MARGIN)
    dragger.finish(start)
    QApplication.processEvents()
