"""The toolbar, the tool menu, the tool chooser and the tips card as they move and answer the pointer: what is painted
catches up with a state that is final at once. The suite runs with motion off (conftest); a test of an animation asks
for it with the motion_on fixture."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QHelpEvent, QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolTip, QVBoxLayout, QWidget

from conftest import settle, wait_for
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.styles import neon
from zenith_pdf.theme import qcolor, themes
from zenith_pdf.ui import motion, tool_menu
from zenith_pdf.ui import toolbar as toolbar_module
from zenith_pdf.ui.tips import _Bullet
from zenith_pdf.ui.tool_catalog import DEFAULT_TOOLBAR
from zenith_pdf.ui.tool_chooser import ToolChooser, _Empty, _shape
from zenith_pdf.ui.toolbar import ToolButton
from zenith_pdf.ui.tools import DEFAULT_STYLES


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
    window.open_file(path)
    workspace = window.current_workspace()
    settle(40)
    yield workspace
    menu = workspace.toolbar.tool_menu
    if menu is not None:
        menu.pin_button.setChecked(False)
        window.settings.set("tool_menu_pinned", False)
        menu.close_menu()
    tools = workspace.tool_settings
    tools.set_tool("select")
    for key in DEFAULT_STYLES:
        tools.reset_style(key)
        tools.set_locked(key, None)
    window.settings.set("toolbar_tools", list(DEFAULT_TOOLBAR))
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def hover(widget, on: bool) -> None:
    widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, on)  # as the pointer does, without a screen to point at


def left_edge(widget) -> tuple[int, int, int, int]:
    """The colour at the left of the widget, clear of its icon, in a picture of it: its fill, as drawn."""
    return widget.grab().toImage().pixelColor(3, widget.height() // 2).getRgb()


def near(first, second) -> bool:
    return all(abs(a - b) <= 2 for a, b in zip(first, second))


# The toolbar ---------------------------------------------------------------------------------------------


def test_a_tool_switch_is_final_at_once_and_what_is_painted_catches_up(workspace, motion_on):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("select")
    assert wait_for(lambda: toolbar.graphicsEffect() is None and not toolbar._gliding, 3)
    pen, hand, select = (toolbar._buttons[k] for k in ("rectangle", "hand", "select"))
    hand_at = hand.x()
    tools.set_tool("rectangle")
    # The state and the geometry are the new ones from the first moment (the menu, the keys and the clicks read them) ...
    assert pen.isChecked() and pen.size() == ToolButton.ACTIVE and select.size() == ToolButton.INACTIVE
    assert hand.x() == hand_at - (ToolButton.ACTIVE.width() - ToolButton.INACTIVE.width())
    # ... and the picture is on its way: the tools the layout moved are painted where they were.
    assert isinstance(hand.graphicsEffect(), toolbar_module._Glide) and pen._lit.running
    assert 0.0 <= pen._lit.value < 1.0
    assert wait_for(lambda: hand.graphicsEffect() is None and not toolbar._gliding, 3)
    assert pen._lit.value == 1.0 and not pen._lit.running


def test_switching_again_while_the_tools_glide_goes_on_from_where_they_are_seen(workspace, motion_on):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("select")
    assert wait_for(lambda: not toolbar._gliding, 3)
    tools.set_tool("rectangle")
    hand = toolbar._buttons["hand"]
    toolbar._glide_by.set(0.5)  # halfway
    seen = hand.pos() + hand.graphicsEffect().shift
    assert seen != hand.pos()
    tools.set_tool("select")  # back, before it has arrived
    effect = hand.graphicsEffect()
    assert isinstance(effect, toolbar_module._Glide)
    assert hand.pos() + effect.shift == seen  # from where it was seen, so no jump
    assert wait_for(lambda: not toolbar._gliding and hand.graphicsEffect() is None, 3)


def test_the_tools_close_the_gap_of_one_taken_off_and_one_that_comes_fades_in(workspace, motion_on):
    toolbar = workspace.toolbar
    toolbar.fit(toolbar._max_width)
    assert wait_for(lambda: not toolbar._gliding, 3)
    after, add = toolbar._buttons["strikeout"], toolbar.add_button
    where = {key: button.x() for key, button in toolbar._buttons.items()}
    toolbar.remove_tool("underline")
    # Gone at once, and the ones that were after it are in their new places from the first moment, painted on the way.
    assert "underline" not in toolbar._buttons and toolbar._buttons["strikeout"].x() < where["strikeout"]
    assert isinstance(toolbar._buttons["strikeout"].graphicsEffect(), toolbar_module._Glide)
    assert isinstance(add.graphicsEffect(), toolbar_module._Glide)
    assert wait_for(lambda: not toolbar._gliding and add.graphicsEffect() is None, 3)
    toolbar.add_tool("underline", 3)
    new = toolbar._buttons["underline"]
    assert new.isVisible() and new.graphicsEffect() is not None  # it fades in
    assert isinstance(toolbar._buttons["strikeout"].graphicsEffect(), toolbar_module._Glide)  # making room
    assert wait_for(lambda: new.graphicsEffect() is None and not toolbar._gliding, 3)
    assert {key: button.x() for key, button in toolbar._buttons.items()} == where
    assert after is not toolbar._buttons["strikeout"]  # they were made again: the glide is of what is painted


def test_nothing_glides_with_motion_off(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("select")
    settle(40)
    tools.set_tool("rectangle")
    assert not toolbar._gliding
    assert all(button.graphicsEffect() is None for button in toolbar._buttons.values())
    pen = toolbar._buttons["rectangle"]
    assert pen._lit.value == 1.0 and not pen._lit.running and not pen._fill.running  # the state lands at once


def test_the_fills_of_the_tools_come_and_go_with_the_pointer(workspace, motion_on):
    toolbar = workspace.toolbar
    toolbar.fit(toolbar._max_width)
    button, t = toolbar._buttons["ellipse"], toolbar.theme()
    assert left_edge(button)[3] == 0
    hover(button, True)
    button.grab()
    assert button._fill.running and button._fill.target.alpha() == qcolor(t.glass_hover).alpha()
    assert wait_for(lambda: not button._fill.running, 3) and near(left_edge(button), qcolor(t.glass_hover).getRgb())
    hover(button, False)
    button.grab()
    assert button._fill.running and button._fill.colour.alpha() > 0  # it lingers as the pointer leaves
    assert wait_for(lambda: not button._fill.running, 3) and left_edge(button)[3] == 0


def test_the_tool_that_is_on_has_its_fill_and_takes_a_little_more_under_the_pointer(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("rectangle")
    settle(40)
    button, t = toolbar._buttons["rectangle"], toolbar.theme()
    checked = qcolor(t.glass_checked)
    assert near(left_edge(button), checked.getRgb()) and button._fill.target.alpha() == checked.alpha()
    hover(button, True)
    assert left_edge(button)[3] > checked.alpha()
    button.setDown(True)
    assert left_edge(button)[3] > qcolor(t.glass_checked).alpha() + 18
    button.setDown(False)
    hover(button, False)


def test_a_disabled_tool_has_no_hover_fill(workspace):
    toolbar = workspace.toolbar
    button = toolbar._buttons["ellipse"]
    button.setEnabled(False)
    hover(button, True)
    assert left_edge(button)[3] == 0
    hover(button, False)
    button.setEnabled(True)


def test_the_caret_and_the_padlock_come_with_the_tool_that_is_on(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("rectangle")
    tools.set_locked("rectangle", True)
    settle(40)
    pen = toolbar._buttons["rectangle"]
    assert pen.active and pen.locked and pen._lit.value == 1.0 and pen._padlock.value == 1.0
    tools.set_locked("rectangle", False)
    settle(40)
    assert not pen.locked and pen._padlock.value == 0.0 and pen._lit.value == 1.0
    tools.set_tool("select")
    settle(40)
    assert pen._lit.value == 0.0 and not pen.active


def test_the_grip_answers_the_pointer(workspace, motion_on):
    grip = workspace.toolbar.grip
    grip.grab()
    assert grip._hot.value == 0.0
    hover(grip, True)
    grip.grab()
    assert grip._hot.target == 1.0
    assert wait_for(lambda: grip._hot.value == 1.0, 3)
    hover(grip, False)
    grip.grab()
    assert wait_for(lambda: grip._hot.value == 0.0, 3)


def test_the_mark_of_a_drop_glides_from_place_to_place(workspace, motion_on):
    mark = workspace.toolbar._mark
    first, second = QRect(20, 8, 3, 20), QRect(200, 8, 3, 20)
    mark.aim(first)
    assert mark.isVisible() and mark.geometry() == first  # it shows where it is first needed, at once
    mark.aim(second)
    assert first.x() <= mark.geometry().x() < second.x()  # and from there it goes
    assert wait_for(lambda: mark.geometry() == second, 3)
    mark.aim(second)  # the same place again changes nothing
    assert mark.geometry() == second
    mark.hide()


def test_a_tool_that_is_dragged_stays_as_a_ghost_of_where_it_came_from(workspace, monkeypatch):
    toolbar = workspace.toolbar
    seen = []

    class Drag:
        def __init__(self, source):
            pass

        def setMimeData(self, mime):
            pass

        def setPixmap(self, pixmap):
            pass

        def exec(self, action):
            seen.append(toolbar._buttons["rectangle"].lifted)
            return Qt.DropAction.IgnoreAction

    monkeypatch.setattr(toolbar_module, "QDrag", Drag)
    button = toolbar._buttons["rectangle"]
    toolbar.start_drag("rectangle", button)
    assert seen == [True] and not button.lifted
    button.lifted = True
    faint = button.grab().toImage()
    button.lifted = False
    full = button.grab().toImage()
    icon = [(x, y) for x in range(8, 28) for y in range(8, 28)]  # where the picture of the tool is
    assert max(faint.pixelColor(x, y).alpha() for x, y in icon) < max(full.pixelColor(x, y).alpha() for x, y in icon)


# The tool menu ---------------------------------------------------------------------------------------------


def open_menu(workspace, key="rectangle"):
    workspace.tool_settings.set_tool(key)
    workspace.toolbar.open_menu(key)
    settle(40)
    return workspace.toolbar.tool_menu


def test_the_menu_comes_out_of_its_button_and_stays_where_it_was_put(workspace, motion_on):
    toolbar = workspace.toolbar
    workspace.tool_settings.set_tool("rectangle")
    toolbar.open_menu("rectangle")
    menu = toolbar.tool_menu
    assert menu.isVisible() and menu.windowOpacity() < 1.0  # on its way in
    place = menu.pos()
    assert wait_for(lambda: menu.windowOpacity() == 1.0, 3)
    assert menu.pos() == place + QPoint(0, 0) or abs(menu.pos().y() - place.y()) <= motion.RISE
    menu.close_menu()


def test_the_menu_does_not_fade_again_when_it_is_pinned_or_unpinned(workspace, motion_on):
    menu = open_menu(workspace)
    assert wait_for(lambda: menu.windowOpacity() == 1.0, 3)
    menu.pin_button.setChecked(True)
    menu.pin_button.click()  # as a click does: the menu goes to its window flags and shows again
    menu.pin_button.click()
    assert menu.windowOpacity() == 1.0
    menu.pin_button.setChecked(False)
    menu.close_menu()


def test_a_menu_moves_under_the_next_tool_with_the_tokens_and_not_with_motion_off(workspace):
    menu = open_menu(workspace)
    target = menu.pos() + QPoint(40, 0)
    menu.place_panel(target + menu.panel_origin(), animate=True)
    assert menu.pos() == target and menu._slide is None or not menu._slide.state()  # motion off: at once
    menu.close_menu()


def test_a_menu_slides_between_tools_when_motion_runs(workspace, motion_on):
    menu = open_menu(workspace)
    assert wait_for(lambda: menu.windowOpacity() == 1.0, 3)
    start = menu.pos()
    menu.place_panel(start + menu.panel_origin() + QPoint(60, 0), animate=True)
    assert menu._slide.duration() == motion.NORMAL and menu._slide.easingCurve() == motion.EASE_IN_OUT
    assert menu.pos() == start  # it goes from here
    assert wait_for(lambda: menu.pos() == start + QPoint(60, 0), 3)
    menu.close_menu()


def test_the_buttons_of_the_menu_paint_their_own_fills_once(workspace):
    menu = open_menu(workspace)
    t = themes.current
    button = menu.more_button
    assert left_edge(button)[3] == 0
    hover(button, True)
    assert near(left_edge(button), qcolor(t.hover).getRgb())  # once, not on top of the sheet's own
    button.setDown(True)
    assert near(left_edge(button), qcolor(t.pressed).getRgb())
    button.setDown(False)
    hover(button, False)
    assert left_edge(button)[3] == 0
    hover(menu.lock_button, True)
    menu.lock_button.setChecked(True)
    assert left_edge(menu.lock_button)[3] > qcolor(t.selection).alpha()  # a choice, and the pointer on it
    hover(menu.lock_button, False)
    assert near(left_edge(menu.lock_button), qcolor(t.selection).getRgb())
    menu.lock_button.setChecked(False)
    menu.close_menu()


def test_the_remove_button_warms_to_red_and_the_pencil_has_its_corners(workspace):
    menu = open_menu(workspace, "ellipse")
    t = themes.current
    hover(menu.remove_button, True)
    red = QColor(t.danger)
    painted = left_edge(menu.remove_button)
    assert painted[3] > 0 and painted[0] > painted[2] and abs(painted[0] - red.red()) <= 3
    hover(menu.remove_button, False)
    hover(menu.subject_pencil, True)
    assert near(left_edge(menu.subject_pencil), qcolor(t.hover).getRgb()) or left_edge(menu.subject_pencil)[3] > 0
    hover(menu.subject_pencil, False)
    menu.close_menu()


def test_the_delete_button_of_a_saved_signature_keeps_its_red_fill_under_the_pointer(qt_app):
    host = QWidget()
    host.setStyleSheet(tool_menu.menu_style(themes.current))  # the sheet the menu's own window carries
    picture = QPixmap(120, 40)
    picture.fill(Qt.GlobalColor.white)
    row = tool_menu._SignatureRow(QIcon(picture), "Initials")
    QVBoxLayout(host).addWidget(row)
    host.resize(300, 80)
    host.show()
    settle(20)
    row.remove.setVisible(True)
    assert left_edge(row.remove)[3] == 0
    hover(row.remove, True)
    painted, red = left_edge(row.remove), QColor(themes.current.danger)
    assert painted[3] > 0 and painted[0] > painted[2] and abs(painted[0] - red.red()) <= 3
    assert row.remove.focusPolicy() == Qt.FocusPolicy.NoFocus  # the row has the focus, as before
    hover(row.remove, False)
    host.close()
    host.deleteLater()


def test_the_slides_of_a_menu_do_not_pile_up_under_it(workspace, motion_on):
    menu = open_menu(workspace)
    assert wait_for(lambda: menu.windowOpacity() == 1.0, 3)
    for step in range(6):
        menu.place_panel(menu.pos() + menu.panel_origin() + QPoint(10 * (step + 1), 0), animate=True)
    settle(40)  # what is let go of is deleted once the loop turns
    assert len(menu.findChildren(QPropertyAnimation)) == 1
    menu.close_menu()


def test_the_ring_of_a_colour_settles_in_and_widens_away(workspace, motion_on):
    row = tool_menu.ColourRow(tool_menu.COLOURS)
    row.resize(288, 32)
    row.set_value(tool_menu.COLOURS[0])
    row.show()
    settle(20)
    row.grab()
    assert row._ring.value(0) == 1.0 and row._ring.value(2) == 0.0  # seen first as it is: nothing animates
    row.set_value(tool_menu.COLOURS[2])
    row.grab()
    assert row._ring._by[0].target == 0.0 and row._ring._by[2].target == 1.0
    assert wait_for(lambda: row._ring.value(0) == 0.0 and row._ring.value(2) == 1.0, 3)
    row.close()
    row.deleteLater()


def test_the_disc_under_the_pointer_fades_and_the_chosen_colour_has_none(workspace, motion_on):
    row = tool_menu.ColourRow(tool_menu.COLOURS)
    row.resize(288, 32)
    row.set_value(tool_menu.COLOURS[0])
    row.show()
    settle(20)
    row.grab()
    row._hover = 3
    row.grab()
    assert row._hot._by[3].target == 1.0
    assert wait_for(lambda: row._hot.value(3) == 1.0, 3)
    row._hover = 0  # the chosen one: its ring is there instead
    row.grab()
    assert row._hot._by[3].target == 0.0
    assert wait_for(lambda: row._hot.value(3) == 0.0, 3)
    row._hover = -1
    row.close()
    row.deleteLater()


def test_a_press_dips_a_colour_and_deepens_a_cell(workspace, motion_on):
    row = tool_menu.ColourRow(tool_menu.COLOURS)
    grid = tool_menu.OptionGrid([(name, None, name, name) for name in ("a", "b", "c")], 3)
    for control, pressed in ((row, 3), (grid, 1)):
        control.resize(288, control.height())
        control.show()
        settle(20)
        control.grab()
        QTest.mousePress(control, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         control.slot_rect(pressed).center().toPoint() if control is row else
                         control.cell_rect(pressed).center().toPoint())
        assert control._pressed == pressed
        control.grab()
        assert control._down._by[pressed].target == 1.0
        QTest.mouseRelease(control, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           control.slot_rect(pressed).center().toPoint() if control is row else
                           control.cell_rect(pressed).center().toPoint())
        assert control._pressed == -1
        control.grab()
        assert wait_for(lambda: control._down.value(pressed) == 0.0, 3)
        control.close()
        control.deleteLater()


def test_a_grid_cell_turns_on_and_off_gently_and_a_card_takes_the_accent(workspace, motion_on):
    grid = tool_menu.OptionGrid([(name, None, name, name) for name in ("a", "b", "c")], 3)
    cards = tool_menu.OptionCards([(name, "z-pen-basic", name, "caption") for name in ("a", "b", "c")])
    for control in (grid, cards):
        control.resize(288, control.height())
        control.set_value("a")
        control.show()
        settle(20)
        control.grab()
        assert control._lit.value(0) == 1.0 and control._lit.value(1) == 0.0
        control.choose("b")
        control.grab()
        assert control._lit._by[0].target == 0.0 and control._lit._by[1].target == 1.0
        assert wait_for(lambda: control._lit.value(0) == 0.0 and control._lit.value(1) == 1.0, 3)
        control.close()
        control.deleteLater()


def test_a_section_turns_its_caret_and_what_opens_drops_in(workspace, motion_on):
    section = tool_menu.Section("Smart ink", open_=False)
    section.resize(288, 200)
    section.show()
    settle(20)
    assert section.header._turn.value == 0.0 and not section.content.isVisible()
    section.set_open(True, emit=True)
    assert section.is_open() and section.content.isVisible()  # open at once
    assert section.header._turn.running and section.content.graphicsEffect() is not None  # and shown on its way in
    assert wait_for(lambda: section.content.graphicsEffect() is None and section.header._turn.value == 1.0, 3)
    section.set_open(False, emit=True)
    assert not section.content.isVisible() and section.header._turn.target == 0.0
    section.close()
    section.deleteLater()


# The tool chooser ------------------------------------------------------------------------------------------


@pytest.fixture
def chooser(window, workspace):
    chooser = ToolChooser(workspace.toolbar, window)
    yield chooser
    chooser.close()


def test_the_chooser_comes_down_from_the_toolbar(chooser, workspace, motion_on):
    chooser.show_under(workspace.toolbar)
    assert chooser.isVisible() and chooser.windowOpacity() < 1.0
    assert wait_for(lambda: chooser.windowOpacity() == 1.0, 3)


def test_a_group_slides_in_from_the_side_its_tab_lies_on(chooser, workspace, motion_on):
    chooser.show_under(workspace.toolbar)
    assert wait_for(lambda: chooser.windowOpacity() == 1.0, 3)
    chooser.show_group(2)
    assert chooser.pages.currentIndex() == 2 and chooser.pages.currentWidget().graphicsEffect() is not None
    assert wait_for(lambda: chooser.pages.currentWidget().graphicsEffect() is None, 3)
    chooser.search.setText("pen")  # typing swaps the page for the results: that does not slide
    assert chooser.pages.currentWidget() is chooser.results and chooser.results.graphicsEffect() is None


def test_a_tile_answers_the_pointer_and_the_press(chooser, workspace, motion_on):
    chooser.show_under(workspace.toolbar)
    tile = chooser._tiles[0]
    t = themes.current
    resting = tile._well(t).name()
    hover(tile, True)
    tile.grab()
    assert tile._hot.target == 1.0
    assert wait_for(lambda: tile._hot.value == 1.0, 3) and tile._well(t).name() != resting
    tile._press = QPoint(10, 70)  # a press, on the well and not the badge
    tile.grab()
    assert tile._down.target == 1.0
    tile._press = None
    hover(tile, False)
    tile.grab()
    assert wait_for(lambda: tile._hot.value == 0.0 and tile._down.value == 0.0, 3)
    assert tile._well(t).name() == resting


def test_the_badge_changes_as_the_tool_comes_and_goes_and_says_what_it_does(chooser, workspace, motion_on):
    chooser.show_under(workspace.toolbar)
    toolbar = workspace.toolbar
    tile = next(t for t in chooser._tiles if t.spec.key == "tape")
    assert not toolbar.has_tool("tape")
    tile.grab()
    assert tile._listed.value == 0.0
    toolbar.add_tool("tape")
    tile.grab()
    assert tile._listed.target == 1.0
    assert wait_for(lambda: tile._listed.value == 1.0, 3)
    badge = tile._badge().center().toPoint()
    tile.event(QHelpEvent(QEvent.Type.ToolTip, badge, tile.mapToGlobal(badge)))
    assert QToolTip.text() == "Remove from the toolbar"
    toolbar.remove_tool("tape")
    tile.event(QHelpEvent(QEvent.Type.ToolTip, badge, tile.mapToGlobal(badge)))
    assert QToolTip.text() == "Add it to the toolbar"
    QToolTip.hideText()


def test_no_match_shows_a_mark_and_a_line_in_the_middle_of_the_page(chooser, workspace):
    chooser.show_under(workspace.toolbar)
    chooser.search.setText("zzzz")
    settle(20)
    empty = chooser.results.widget().findChildren(_Empty)
    assert len(empty) == 1 and empty[0].isVisible()
    assert empty[0].accessibleName() == "No tool matches."  # painted, so a screen reader is told by its name
    assert wait_for(lambda: empty[0].width() > 500, 3)  # across the page, not in a corner of it
    chooser.search.setText("pen")
    settle(20)
    assert not chooser.results.widget().findChildren(_Empty)


def test_the_wells_and_badges_are_round_but_cut_in_a_style_that_chamfers(qt_app):
    before = themes.current.name, themes.style.key
    try:
        themes.choose("dark", "blue", "zenith")
        round_ = _shape(QRectF(0, 0, 78, 78), 39)
        assert not round_.contains(QPointF(76.0, 2.0))  # the corner of a circle is empty
        themes.choose("dark", "blue", "neon")
        cut = _shape(QRectF(0, 0, 78, 78), 39)
        assert neon.LOOK.chamfer and cut.contains(QPointF(76.0, 2.0)) and not cut.contains(QPointF(2.0, 2.0))
    finally:
        themes.choose(before[0], "blue", before[1])


def test_the_dots_swell_to_the_group_that_is_open(chooser, workspace, motion_on):
    chooser.show_under(workspace.toolbar)
    chooser.dots.grab()
    assert chooser.dots._lit.value(0) == 1.0
    chooser.show_group(3)
    chooser.dots.grab()
    assert chooser.dots._lit._by[0].target == 0.0 and chooser.dots._lit._by[3].target == 1.0
    assert wait_for(lambda: chooser.dots._lit.value(3) == 1.0 and chooser.dots._lit.value(0) == 0.0, 3)


# The tips card -------------------------------------------------------------------------------------------------


def test_a_tip_that_wraps_hangs_from_its_bullet_and_the_card_comes_down_from_the_toolbar(window, workspace, motion_on):
    settings = window.settings
    settings.set("tool_tips", True)
    settings.set("tips_seen", [])
    try:
        workspace.tool_settings.set_tool("rectangle")
        card = workspace.tool_tips.card
        assert card.isVisible() and card.graphicsEffect() is not None  # on its way in
        assert wait_for(lambda: card.graphicsEffect() is None, 3)
        assert len(card.lines) >= 3 and not any("•" in label.text() for label in card.lines)
        bullets = card.findChildren(_Bullet)
        assert len(bullets) == len(card.lines)
        # The words start to the right of the dots, all at the same place: a line that wraps starts under the first.
        assert len({label.mapTo(card, QPoint(0, 0)).x() for label in card.lines}) == 1
        assert all(label.x() > bullet.width() for label, bullet in zip(card.lines, bullets))
        assert card.height() >= sum(label.height() for label in card.lines)  # all the lines fit the card
    finally:
        workspace.tool_tips.card.hide()
        settings.set("tool_tips", False)
