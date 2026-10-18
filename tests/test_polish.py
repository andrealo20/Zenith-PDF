"""Keyboard use of the tool menus, the one segmented control, popups on the real platform, and the
layouts of the record dialog, the player and the home page's columns.

Offscreen, a press goes straight to the widget under it even while a popup is open. On
Windows the popup takes it, closes, and Qt hands the press on to what is under the pointer
unless the popup set WA_NoMouseReplay. windows_press() plays that part, so the tests follow
the sequence a real click goes through.
"""

import base64
import shutil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSlider, QWidget

from conftest import settle
from zenith_pdf.app import create_app
from zenith_pdf.core import images
from zenith_pdf.core.audio import Player, Recorder, wav_bytes
from zenith_pdf.settings import Settings
from zenith_pdf.theme import DARK, LIGHT, themes
from zenith_pdf.ui import audio_dialog, insert_dialogs, messages, tool_menu
from zenith_pdf.ui import toolbar as toolbar_module
from zenith_pdf.ui.home import AUTHOR, COLUMN_WIDTHS, LEAST_WIDTHS, MORE, NAME, NAME_WIDTH, OPTIONAL_COLUMNS, STAR, \
    HomePage
from zenith_pdf.ui.tool_catalog import DEFAULT_TOOLBAR
from zenith_pdf.ui.tool_menu import ColourRow, OptionCards, OptionGrid, Popover, StyleEditor
from zenith_pdf.ui.tools import DEFAULT_STYLES
from zenith_pdf.ui.widgets import Segmented, Switch, contrast, keyboard_focus, luminance, moved_cursor, \
    segment_colours

TONE = (b"\x00\x10" + b"\x00\xf0") * 551  # 50 ms of a quiet tone at 22050 Hz


def centre(widget: QWidget) -> QPoint:
    return widget.mapToGlobal(widget.rect().center())


def windows_press(popup: Popover, point: QPoint, button=Qt.MouseButton.LeftButton) -> bool:
    """A press at `point` (global) while `popup` is open, routed as on Windows. True if Qt would hand it on.

    The popup gets the press; if it closed, and the press was outside its window, and it
    did not set WA_NoMouseReplay, Qt replays the press to the widget under the pointer.
    """
    local = QPointF(popup.mapFromGlobal(point))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, local, QPointF(point), button, button,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(popup, press)
    return (not popup.isVisible() and not popup.geometry().contains(point)
            and not popup.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay))


def wheel_at(target: QWidget, point: QPoint, notches: int = -1) -> None:
    event = QWheelEvent(QPointF(target.mapFromGlobal(point)), QPointF(point), QPoint(), QPoint(0, 120 * notches),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, event)


def tab(times: int = 1) -> QWidget:
    for _ in range(times):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
    return QApplication.focusWidget()


def key(name) -> None:
    QTest.keyClick(QApplication.focusWidget(), name)


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 860)
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
    window.resize(1300, 860)
    window.open_file(path)
    workspace = window.current_workspace()
    settle(80)
    yield workspace
    menu = workspace.toolbar.tool_menu
    if menu is not None:
        window.settings.set("tool_menu_pinned", False)
        menu.close_menu()
    tools = workspace.tool_settings
    tools.set_tool("select")
    for name in DEFAULT_STYLES:
        tools.reset_style(name)
        tools.set_locked(name, None)
    window.settings.set("toolbar_tools", list(DEFAULT_TOOLBAR))
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def open_menu(workspace, name):
    toolbar = workspace.toolbar
    if name not in toolbar.keys():
        toolbar.add_tool(name)
        settle(40)
    workspace.tool_settings.set_tool(name)
    toolbar.open_menu(name)
    settle(40)
    menu = toolbar.tool_menu
    assert menu.isVisible() and QApplication.focusWidget() is menu
    return menu


# The segmented control ---------------------------------------------------------------------------------


def test_one_segmented_control_for_menus_and_dialogs(qt_app):
    assert tool_menu.Segmented is Segmented and insert_dialogs.Segments is Segmented
    holder = QWidget()
    control = Segmented([("whole", "Whole strokes"), ("partial", "Partial")], {"partial": "Only what it passes"},
                        holder)
    holder.resize(300, 40)
    control.resize(288, 32)
    holder.show()
    holder.activateWindow()
    QTest.qWaitForWindowActive(holder)
    chosen, changed = [], []
    control.chosen.connect(chosen.append)
    control.changed.connect(changed.append)
    assert control.value == "whole" and control.buttons[1].toolTip() == "Only what it passes"

    control.set_value("partial")  # showing a value is no choice
    assert control.current() == 1 and changed == [1] and chosen == []
    control.set_value("whole")
    QTest.keyClick(holder, Qt.Key.Key_Tab)  # one stop for the whole control
    assert QApplication.focusWidget() is control and keyboard_focus(control)
    assert control.focusPolicy() == Qt.FocusPolicy.TabFocus
    unfocused = control.buttons[1].grab().toImage()
    QTest.keyClick(control, Qt.Key.Key_Right)  # the ring moves; nothing is chosen yet
    assert control.value == "whole" and chosen == []
    assert control.ring_on(control.buttons[1]) and control.buttons[1].grab().toImage() != unfocused
    QTest.keyClick(control, Qt.Key.Key_Return)
    assert control.value == "partial" and chosen == ["partial"]
    QTest.keyClick(control, Qt.Key.Key_Left)
    QTest.keyClick(control, Qt.Key.Key_Space)
    assert control.value == "whole" and chosen == ["partial", "whole"]
    QTest.keyClick(control, Qt.Key.Key_Space)  # the one already chosen: no news
    assert chosen == ["partial", "whole"]

    QTest.mouseClick(control.buttons[1], Qt.MouseButton.LeftButton)
    assert chosen[-1] == "partial" and control.current() == 1
    QTest.mouseClick(control.buttons[1], Qt.MouseButton.LeftButton)
    assert chosen == ["partial", "whole", "partial"]
    control.set_value("nothing of the kind")
    assert control.current() == -1 and not any(button.isChecked() for button in control.buttons)

    labels = Segmented(["Portrait", "Landscape"])  # plain labels: their positions are the values
    assert labels.items == [(0, "Portrait"), (1, "Landscape")] and labels.current() == 0
    holder.close()
    holder.deleteLater()
    labels.deleteLater()


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=["dark", "light"])
def test_the_chosen_segment_reads_clearly(qt_app, theme):
    """designer.md section 2: a filled segment with a 1 px border, apart from the track at a glance."""
    track, fill, edge = segment_colours(theme)
    raised = QColor(theme.raised)
    alpha = track.alphaF()
    behind = QColor.fromRgbF(*(alpha * t + (1 - alpha) * r for t, r in (
        (track.redF(), raised.redF()), (track.greenF(), raised.greenF()), (track.blueF(), raised.blueF()))))
    assert contrast(luminance(edge), luminance(behind)) >= 1.25
    if theme is DARK:  # the theme's own segment colour was nearly the track's (Border | Fill, round 2)
        assert contrast(luminance(fill), luminance(behind)) >= 1.5
        assert contrast(luminance(QColor(theme.segment)), luminance(behind)) < 1.5
    assert contrast(luminance(QColor(theme.text)), luminance(fill)) >= 7

    current = themes.current
    themes.apply(theme)
    try:
        control = Segmented([("color", "Border"), ("fill", "Fill")])
        control.resize(288, 32)
        control.show()
        settle(40)
        picture = control.grab().toImage()
        chosen = control.buttons[0].geometry()
        sample = picture.pixelColor(chosen.left() + 6, chosen.center().y())
        assert abs(sample.red() - fill.red()) <= 3 and abs(sample.green() - fill.green()) <= 3
        control.close()
        control.deleteLater()
    finally:
        themes.apply(current)


def test_the_cursor_moves_by_rows_and_stops_at_the_ends():
    assert moved_cursor(0, 10, 5, Qt.Key.Key_Left) == 0
    assert moved_cursor(4, 10, 5, Qt.Key.Key_Right) == 5  # on into the next row
    assert moved_cursor(7, 10, 5, Qt.Key.Key_Up) == 2 and moved_cursor(2, 10, 5, Qt.Key.Key_Up) == 2
    assert moved_cursor(2, 10, 5, Qt.Key.Key_Down) == 7 and moved_cursor(9, 10, 5, Qt.Key.Key_End) == 9
    assert moved_cursor(1, 3, 3, Qt.Key.Key_Down) == 2  # one row: up and down go along it
    assert moved_cursor(1, 3, 3, Qt.Key.Key_A) is None


def test_a_switch_shows_its_ring_for_the_keyboard_only(qt_app):
    holder = QWidget()
    switch = Switch(False, holder)
    switch.resize(40, 22)
    holder.show()
    holder.activateWindow()
    QTest.qWaitForWindowActive(holder)
    plain = switch.grab().toImage()
    switch.setFocus(Qt.FocusReason.MouseFocusReason)
    assert switch.hasFocus() and switch.grab().toImage() == plain  # a click gives no ring
    switch.clearFocus()
    QTest.keyClick(holder, Qt.Key.Key_Tab)
    assert switch.hasFocus() and switch.grab().toImage() != plain
    QTest.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked()
    holder.close()
    holder.deleteLater()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_an_off_switch_reads_and_accent_text_reads_on_tinted_fills(qt_app, theme):
    """Off: an outline and a knob in the muted text colour, 3:1 or more on any panel (a track in the border colour
    was 1.44:1 on light). Accent text on a tinted fill (a selected item, a chosen chip) takes accent_ink: 4.5:1."""
    from zenith_pdf.theme import ACCENTS, THEMES, with_accent

    previous = themes.current.name

    def ratio(a: QColor, b: QColor) -> float:
        return contrast(luminance(a), luminance(b))

    themes.choose(theme, "blue")
    try:
        t = themes.current
        for back in (t.raised, t.surface, t.window):
            assert ratio(QColor(t.text_muted), QColor(back)) >= 3.0
        switch = Switch(False)
        switch.resize(40, 22)
        image = switch.grab().toImage()
        edge = image.pixelColor(1, 11)  # the left end of the outline
        assert edge.alpha() > 0 and abs(edge.lightness() - QColor(t.text_muted).lightness()) < 40
        switch.deleteLater()
        base = THEMES[theme]
        for accent in ACCENTS:
            tinted = with_accent(base, accent)
            fill = QColor(tinted.accent)
            fill.setAlpha(41)
            for back in (tinted.raised, tinted.surface, tinted.window):
                under = QColor(back)
                mixed = QColor.fromRgbF(*(u + (f - u) * fill.alphaF() for u, f in
                                          zip((under.redF(), under.greenF(), under.blueF()),
                                              (fill.redF(), fill.greenF(), fill.blueF()))))
                assert ratio(QColor(tinted.accent_ink), mixed) >= 4.5, (theme, accent, back)
    finally:
        themes.choose(previous, "blue")


# The tool menus from the keyboard --------------------------------------------------------------------


def test_tab_goes_down_the_line_menu_and_keys_pick(workspace):
    tools = workspace.tool_settings
    menu = open_menu(workspace, "line")
    editor = menu.editor
    stops, rings = [], []
    for _ in range(16):
        stops.append(tab())
        rings.append(keyboard_focus(stops[-1]))
    expected = [menu.lock_button, menu.remove_button, menu.more_button, menu.pin_button, menu.subject_pencil,
                editor.blocks["color"].findChild(ColourRow)]
    assert stops[:6] == expected
    kinds = [type(w).__name__ for w in stops[6:]]
    assert kinds == ["QSlider", "ValueBox", "QSlider", "ValueBox", "_SectionHeader", "OptionGrid", "_SectionHeader",
                     "Segmented", "OptionGrid", "_SectionHeader"]
    assert all(rings)

    colours = tab(6)  # round the menu once more: the header's four, the pencil, then the colours
    assert colours is expected[5] and colours._cursor == colours.chosen_slot()
    QTest.keyClick(colours, Qt.Key.Key_Right)
    QTest.keyClick(colours, Qt.Key.Key_Right)
    assert tools.style("line").color == DEFAULT_STYLES["line"].color  # moving the ring chooses nothing
    QTest.keyClick(colours, Qt.Key.Key_Return)
    assert tools.style("line").color == tool_menu.COLOURS[colours._cursor]
    assert menu.isVisible()

    dashes = tab(6)  # past the two sliders and their boxes, and the Line style heading
    assert isinstance(dashes, OptionGrid) and dashes is editor.blocks["dash"]
    QTest.keyClick(dashes, Qt.Key.Key_Right)
    QTest.keyClick(dashes, Qt.Key.Key_Space)
    assert tools.style("line").dash == "dotted"

    ends = tab(2)
    assert ends is editor.endings_switch and ends.value == "head_end"
    QTest.keyClick(ends, Qt.Key.Key_Left)
    assert ends.value == "head_end"
    QTest.keyClick(ends, Qt.Key.Key_Return)
    assert ends.value == "head_start"
    grid = tab()
    QTest.keyClick(grid, Qt.Key.Key_Down)  # the second row of endings, same column
    QTest.keyClick(grid, Qt.Key.Key_Return)
    assert tools.style("line").head_start == list(tool_menu.ENDING_NAMES)[5]

    advanced = tab()
    assert advanced is editor.sections["Advanced"].header and not editor.sections["Advanced"].is_open()
    QTest.keyClick(advanced, Qt.Key.Key_Space)
    settle(40)
    assert editor.sections["Advanced"].is_open()
    switch = tab()
    assert isinstance(switch, Switch)
    QTest.keyClick(switch, Qt.Key.Key_Space)
    assert tools.style("line").highlight is True
    QTest.keyClick(advanced, Qt.Key.Key_Return)  # and shut again
    settle(40)


def test_cards_rings_and_the_header_from_the_keyboard(workspace):
    tools = workspace.tool_settings
    menu = open_menu(workspace, "pen")
    cards = menu.editor.blocks["pen_type"].findChild(OptionCards)
    plain = cards.grab().toImage()
    assert tab(6) is cards  # the header's four buttons and the pencil come first
    assert cards.grab().toImage() != plain  # the ring
    QTest.keyClick(cards, Qt.Key.Key_Right)
    QTest.keyClick(cards, Qt.Key.Key_Enter)
    assert tools.style("pen").pen == "pro"
    slider = menu.editor.blocks["width"].findChild(QSlider)
    slider.setFocus(Qt.FocusReason.MouseFocusReason)
    assert slider.property("ring") is False  # a slider clicked shows no ring
    slider.clearFocus()
    slider.setFocus(Qt.FocusReason.ShortcutFocusReason)  # as Tab does
    assert slider.property("ring") is True
    QTest.keyClick(slider, Qt.Key.Key_PageUp)  # a single step is finer than the values are rounded to
    settle(200)
    assert tools.style("pen").width > DEFAULT_STYLES["pen"].width
    locked = tools.is_locked("pen")
    menu.lock_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
    QTest.keyClick(menu.lock_button, Qt.Key.Key_Return)
    settle(250)  # Enter presses it as a click would, with the button seen going down
    assert tools.is_locked("pen") is not locked and menu.isVisible()


def test_tab_scrolls_a_short_menu_to_the_option(window, workspace):
    window.resize(1300, 560)
    settle(80)
    try:
        menu = open_menu(workspace, "polyline")
        bar = menu.scroll.verticalScrollBar()
        assert bar.maximum() > 0 and bar.value() == 0
        for _ in range(30):
            widget = tab()
            if widget is menu.editor.sections["Advanced"].header:
                break
        assert bar.value() > 0  # the option that has the focus is in view
        top = widget.mapTo(menu.scroll.viewport(), QPoint(0, 0)).y()
        assert 0 <= top and top + widget.height() <= menu.scroll.viewport().height()
    finally:
        window.resize(1300, 860)
        settle(80)


def test_a_signature_is_deleted_from_the_keyboard(workspace, monkeypatch):
    settings = workspace.view.settings
    picture = insert_dialogs.typed_signature("AL", "Segoe UI", (0, 0, 0))
    record = {"name": "Initials", "png": base64.b64encode(images.encode(picture)).decode("ascii")}
    settings.set("signatures", [record])
    try:
        menu = open_menu(workspace, "signature")
        row = menu.editor.signature_list.rows[0]
        assert not row.remove.isVisible()
        row.setFocus(Qt.FocusReason.ShortcutFocusReason)
        assert row.remove.isVisible()  # where a mouse user sees it on hover
        asked = []
        monkeypatch.setattr(messages, "confirm", lambda *a, **k: asked.append(a[2]) or True)
        QTest.keyClick(row, Qt.Key.Key_Delete)
        assert asked and "Initials" in asked[0] and settings.get("signatures") == []
    finally:
        settings.set("signatures", [])


# Popups and the toolbar, as on Windows -------------------------------------------------------------------


def test_a_click_on_another_tool_switches_in_one_click(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    menu = open_menu(workspace, "rectangle")
    other = toolbar._buttons["ellipse"]
    if windows_press(menu, centre(other)):
        QTest.mousePress(other, Qt.MouseButton.LeftButton)  # Qt hands the press on
    QTest.mouseRelease(other, Qt.MouseButton.LeftButton)  # the release goes to the button under the pointer
    settle(40)
    assert not menu.isVisible() and tools.tool == "ellipse" and not menu.closed_by_anchor


def test_a_click_on_the_menus_own_button_closes_it_for_good(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    menu = open_menu(workspace, "rectangle")
    button = toolbar._buttons["rectangle"]
    handed_on = windows_press(menu, centre(button))
    assert not handed_on and not menu.isVisible() and menu.closed_by_anchor
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    settle(100)
    assert not menu.isVisible() and tools.tool == "rectangle"
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)  # the next click opens it again, at once
    settle(40)
    assert menu.isVisible()

    # A right click: the press closes the menu, and the context menu event of its release is swallowed.
    assert not windows_press(menu, centre(button), Qt.MouseButton.RightButton)
    QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), centre(button)))
    settle(40)
    assert not menu.isVisible()
    QTest.qWait(toolbar_module.REPLAY_MS + 50)
    QApplication.sendEvent(button, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), centre(button)))
    settle(40)
    assert menu.isVisible() and menu.key == "rectangle"


def test_where_the_press_reaches_the_button_itself_the_click_is_swallowed(workspace, monkeypatch):
    """Platforms that close a popup without routing the press through it: the button's click follows."""
    toolbar = workspace.toolbar
    menu = open_menu(workspace, "rectangle")
    button = toolbar._buttons["rectangle"]
    point = centre(button)

    class Pointer:
        @staticmethod
        def pos():
            return point

    class Buttons:
        startDragDistance = staticmethod(QApplication.startDragDistance)

        @staticmethod
        def mouseButtons():
            return Qt.MouseButton.LeftButton

    monkeypatch.setattr(toolbar_module, "QCursor", Pointer)
    monkeypatch.setattr(toolbar_module, "QApplication", Buttons)
    menu.close_menu()  # the platform closed it as the button was pressed
    monkeypatch.undo()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(40)
    assert not menu.isVisible()


def test_a_press_on_the_menus_shadow_goes_on_to_the_toolbar(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    menu = open_menu(workspace, "rectangle")
    panel = menu.panel.geometry().translated(menu.pos())
    window_rect = menu.geometry()
    target = next(b for k, b in toolbar._buttons.items() if k != "rectangle" and b.isVisible()
                  and panel.left() < centre(b).x() < panel.right())
    point = QPoint(centre(target).x(), target.mapToGlobal(QPoint(0, target.height() - 3)).y())
    assert window_rect.contains(point) and not panel.contains(point)  # the margin round the panel, over the button
    assert not windows_press(menu, point)  # inside the popup's window: Qt would not hand it on, the menu does
    assert not menu.isVisible() and target.isDown()
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=target.mapFromGlobal(point))
    settle(40)
    assert tools.tool == target.key


def test_the_wheel_over_the_page_scrolls_it(window, workspace):
    view = workspace.view
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0
    menu = open_menu(workspace, "rectangle")
    panel = menu.panel.geometry().translated(menu.pos())
    wheel_at(menu, menu.mapToGlobal(menu.panel.geometry().center()))  # over the menu: it stays
    assert menu.isVisible()
    before = bar.value()
    over_page = view.viewport().mapToGlobal(QPoint(40, view.viewport().height() // 2))
    assert not panel.contains(over_page)
    wheel_at(menu, over_page)  # a popup gets the wheel, wherever the pointer is
    settle(40)
    assert not menu.isVisible() and bar.value() > before

    menu = open_menu(workspace, "rectangle")
    menu.pin_button.click()
    settle(40)
    assert menu.pinned and menu.margins() == tool_menu.PINNED_SHADOW
    left, top, _, _ = menu.margins()
    in_margin = menu.mapToGlobal(QPoint(left - 2, top + 40))  # a pinned menu's window still has a little margin
    before = bar.value()
    wheel_at(menu, in_margin)
    settle(40)
    assert menu.isVisible() and bar.value() > before
    menu.pin_button.click()


# Room ----------------------------------------------------------------------------------------------


def test_the_common_menus_fit_a_window_860_tall(window, workspace):
    """Line, arrow and polyline had the Advanced section below the fold."""
    for name in ("line", "arrow", "polyline", "pen", "marker", "rectangle", "polygon"):
        menu = open_menu(workspace, name)
        needed = menu._chrome() + menu._body_height()
        assert needed <= 680, name  # room under the toolbar, even on an 800 px offscreen screen
        assert menu.scroll.verticalScrollBar().maximum() == 0 and menu.scroll.height() == menu._body_height(), name
        menu.close_menu()
    editor = StyleEditor()
    editor.set_options(("width",), DEFAULT_STYLES["pen"], base="pen")
    row = editor.blocks["width"].findChild(tool_menu.SliderRow)
    assert row.height() == tool_menu.SLIDER_ROW and row.ticks is not None
    editor.deleteLater()


# Dialogs --------------------------------------------------------------------------------------------


class QuietMic:
    def __init__(self, chunk=TONE):
        self.chunk = chunk

    def open(self, rate, channels, bits):
        pass

    def read(self):
        return [self.chunk]

    def close(self):
        return [self.chunk[:200]]


def test_the_record_dialog_keeps_its_size_and_centres_a_short_message(qt_app):
    dialog = audio_dialog.RecordDialog(recorder=Recorder(backend=QuietMic()))
    dialog.show()
    settle(40)
    height = dialog.height()
    box = dialog.status.parentWidget()

    def balanced() -> bool:
        settle(40)
        label = dialog.status.geometry()
        return abs(label.center().y() - box.rect().center().y()) <= 1

    assert balanced()  # one line in the room of two: in the middle, not over an empty line
    for text, link in ((audio_dialog.NO_SOUND, audio_dialog.PRIVACY_SETTINGS),
                       (audio_dialog.NO_DEVICE, audio_dialog.SOUND_SETTINGS)):
        dialog._say(text, problem=True, link=link)
        settle(40)
        assert dialog.status.height() <= box.height() and dialog.height() == height, text
    dialog._say(audio_dialog.RECORDED)
    assert balanced() and dialog.height() == height
    gap_above = box.y() - (dialog.record_button.geometry().bottom() + 1)
    buttons = dialog.add_button.geometry().top() - (box.geometry().bottom() + 1)
    assert gap_above == 12 and buttons == 20
    dialog.reject()
    dialog.deleteLater()


def test_the_player_lines_its_parts_up(qt_app):
    class Mci:
        def __call__(self, command):
            words = command.split()
            return {"length": "7000", "mode": "stopped", "position": "0"}[words[2]] if words[0] == "status" else ""

    player = Player(wav_bytes(TONE), "Audio note 2026-10-16 10.42.wav", send=Mci())
    popover = audio_dialog.AudioPopover(player, "Audio note 2026-10-16 10.42.wav")
    popover.show_at(QPoint(100, 100))
    settle(40)
    assert popover.title.geometry().left() == popover.bar.geometry().left()  # the name over the bar
    assert popover.close_button.geometry().right() == popover.time_label.geometry().right()
    play = popover.play_button.geometry()
    assert play.top() > 0 and abs(play.center().y() - popover.rect().center().y()) <= 1
    popover.close()
    settle(40)


def test_the_author_column_shows_whenever_the_name_keeps_its_room(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.settings.add_recent(path)
    home = HomePage(window.settings)
    try:
        home.show()
        for width in (960, 1100, 1180, 1279, 1290, 1400):
            home.resize(width, 760)
            settle(40)
            room = home.table.viewport().width() - COLUMN_WIDTHS[STAR] - COLUMN_WIDTHS[MORE] - NAME_WIDTH
            fits = sum(LEAST_WIDTHS[c] for c in OPTIONAL_COLUMNS) <= room
            assert home.table.isColumnHidden(AUTHOR) is not fits, width
            assert home.table.columnWidth(NAME) >= NAME_WIDTH, width
        home.resize(1180, 760)
        settle(40)
        assert not home.table.isColumnHidden(AUTHOR)  # below 1300 px, where it used to stay hidden
    finally:
        home.close()
        home.deleteLater()


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_icons_keep_their_size_at_any_display_scaling(qt_app, scale):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    from zenith_pdf.icons import icon

    for name in ("pen", "z-lasso"):  # a Phosphor icon and one drawn here
        picture = icon(name).pixmap(QSize(24, 24), scale).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        inked = [(x, y) for y in range(picture.height()) for x in range(picture.width())
                 if picture.pixelColor(x, y).alpha() > 40]
        right = max(x for x, _ in inked)
        bottom = max(y for _, y in inked)
        # The drawing reaches past two thirds of the pixmap, as at 100%: not shrunk into its corner.
        assert right > picture.width() * 0.66 and bottom > picture.height() * 0.66, (name, scale, right, bottom)
