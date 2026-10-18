"""Each tool's menu on the toolbar, the selection's style menu, and the tool chooser."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import settle
from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Ink, Line, Shape
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import tool_menu
from zenith_pdf.ui.tool_catalog import BY_KEY, CATALOG, DEFAULT_TOOLBAR, base_of
from zenith_pdf.ui.tool_chooser import ToolChooser, ToolTile
from zenith_pdf.ui.tool_menu import (
    EDGE,
    FLOOR,
    OptionCards,
    OptionGrid,
    Segmented,
    SliderRow,
    StyleEditor,
    THICKNESS,
    selection_options,
)
from zenith_pdf.ui.toolbar import ToolButton
from zenith_pdf.ui.tools import DEFAULT_STYLES, OPTIONS


def full_title(menu) -> str:
    """The tool's name in the menu's header: elided where it doesn't fit, whole in the tooltip then."""
    return menu.title.toolTip() or menu.title.text()


class Mouse:
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier

    def globalPosition(self):
        return QPointF(400, 300)


def drag(view, key, points, page=0):
    view.tool_settings.set_tool(key)
    tool = view._tool()
    item = view._pages[page]
    tool.press(item, QPointF(*points[0]), Mouse())
    for point in points[1:]:
        tool.move(item, QPointF(*point), Mouse())
    tool.release(item, QPointF(*points[-1]), Mouse())


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
        tools.set_subject(key, "")
    for base in DEFAULT_STYLES:
        for key in tools.instances(base)[1:]:
            tools.forget(key)
    window.settings.set("toolbar_tools", list(DEFAULT_TOOLBAR))
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def open_menu(workspace, key):
    toolbar = workspace.toolbar
    if key not in toolbar.keys():
        toolbar.add_tool(key)
    if not BY_KEY[base_of(key)].action:
        workspace.tool_settings.set_tool(key)
    toolbar.open_menu(key)
    settle(40)
    return toolbar.tool_menu


def press_key(widget, key):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


# Opening and closing ---------------------------------------------------------------------------------


def test_a_click_turns_a_tool_on_and_another_opens_its_menu(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    button = toolbar._buttons["rectangle"]
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle(40)
    assert tools.tool == "rectangle" and (toolbar.tool_menu is None or not toolbar.tool_menu.isVisible())
    assert button.isChecked() and button.size() == ToolButton.ACTIVE  # wider, with its caret
    assert toolbar._buttons["ellipse"].size() == ToolButton.INACTIVE

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)  # the tool that is on: its menu
    settle(150)
    menu = toolbar.tool_menu
    assert menu.isVisible() and menu.key == "rectangle" and button.isChecked() and tools.tool == "rectangle"
    assert full_title(menu) == "Rectangle"
    toolbar.toggle_menu("rectangle")  # the same again closes it
    assert not menu.isVisible()

    other = toolbar._buttons["ellipse"]
    QApplication.sendEvent(other, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                                    other.mapToGlobal(QPoint(5, 5))))
    settle(40)
    assert menu.isVisible() and menu.key == "ellipse" and tools.tool == "rectangle"  # a right click only opens
    press_key(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    toolbar.open_menu("ellipse")
    tools.set_tool("line")  # another tool turned on: a menu about an earlier one goes
    assert not menu.isVisible()


def test_a_long_press_opens_the_menu_without_a_click(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    tools.set_tool("select")
    button = toolbar._buttons["line"]
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    settle(650)
    menu = toolbar.tool_menu
    assert menu is not None and menu.isVisible() and menu.key == "line"
    button.mouseReleaseEvent(_release(button))
    settle(40)
    assert tools.tool == "select" and menu.isVisible()  # the release after a long press is no click


def _release(button):
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), button.mapToGlobal(QPointF(5, 5)),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)


def test_every_tool_has_a_menu_that_stays_open_inside_the_window(window, workspace):
    toolbar = workspace.toolbar
    bounds = window.geometry()
    for spec in CATALOG:
        menu = open_menu(workspace, spec.key)
        settle(60)  # nothing it builds may open a window of its own and close it
        assert menu.isVisible(), spec.key
        assert menu.key == spec.key and full_title(menu) == spec.title
        panel = menu.panel.geometry().translated(menu.pos())
        assert panel.left() >= bounds.left() + EDGE - 1 and panel.right() <= bounds.right() - EDGE + 1, spec.key
        assert panel.bottom() <= bounds.bottom() - FLOOR + 1, spec.key
        body = menu._body_height()  # measured for this tool, not the last one
        assert menu.scroll.height() == body or menu.scroll.verticalScrollBar().maximum() > 0, spec.key
        assert menu.subject_row.isVisible() == (spec.key not in tool_menu.NO_SUBJECT)
        assert menu.lock_button.isVisible() == (spec.key in tool_menu.LOCKABLE)
        assert menu.remove_button.isVisible() == (spec.key != "select")
        shown = set(menu.editor.blocks)
        assert shown <= set(OPTIONS[spec.key]) | {"color"}, spec.key
        menu.close_menu()
    assert toolbar.tool_menu.bookmark_button.isHidden()  # only the bookmark tool's menu has it


def test_a_short_window_scrolls_the_menu_body(window, workspace):
    size = window.size()
    window.resize(1300, 560)
    settle(80)
    try:
        menu = open_menu(workspace, "polyline")
        assert menu.scroll.height() < menu._body_height()
        assert menu.panel.geometry().translated(menu.pos()).bottom() <= window.geometry().bottom() - FLOOR + 1
        # One tool's menu after another's: the scrolled body takes the height this tool's options need, neither
        # squeezed to the last one's (options on top of each other) nor left longer (an empty tail).
        for key in ("rectangle", "line", "polygon", "marker"):
            menu = open_menu(workspace, key)
            settle(40)
            assert menu.body.height() == menu._body_height(), key
        menu.close_menu()
    finally:
        window.resize(size)
        settle(80)


# The header --------------------------------------------------------------------------------------------


def test_lock_subject_duplicate_and_remove(workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    menu = open_menu(workspace, "highlight")
    assert menu.lock_button.isChecked() and tools.is_locked("highlight")  # a text marker starts locked
    menu.lock_button.click()
    assert not tools.is_locked("highlight") and not menu.lock_button.isChecked()
    menu.lock_button.click()
    assert tools.is_locked("highlight") and tools.config("highlight").locked is None  # back to the default

    assert menu.subject_caption.text() == "Tool subject: Text highlight"
    menu.subject_pencil.click()
    assert menu.subject_edit.isVisible() and not menu.subject_caption.isVisible()
    menu.subject_edit.setText("Important")
    press_key(menu.subject_edit, Qt.Key.Key_Return)
    settle(40)
    assert tools.subject("highlight") == "Important" and menu.subject_caption.text() == "Tool subject: Important"
    menu.subject_pencil.click()
    menu.subject_edit.setText("Nothing")
    press_key(menu.subject_edit, Qt.Key.Key_Escape)  # gives up the change; the menu stays
    assert tools.subject("highlight") == "Important" and menu.isVisible() and not menu.subject_edit.isVisible()

    actions = {a.text(): a for a in menu.more_menu().actions() if a.text()}
    assert {"Duplicate", "Reset to default", "Move left", "Move right", "Help", "All tool settings…"} <= set(actions)
    actions["Duplicate"].trigger()
    settle(40)
    keys = toolbar.keys()
    assert keys[keys.index("highlight") + 1] == "highlight#2" and tools.tool == "highlight#2"
    assert menu.isVisible() and menu.key == "highlight#2" and full_title(menu) == "Text highlight 2"
    assert tools.subject("highlight#2") == "Important"  # the copy keeps the subject
    menu.editor.blocks["color"].findChild(tool_menu.ColourRow).activate(1)  # the second highlighter colour
    copy = toolbar._buttons["highlight#2"]
    assert copy.tint is not None and copy.tint != toolbar._buttons["highlight"].tint  # told apart by colour

    menu.remove_button.click()
    settle(40)
    assert "highlight#2" not in toolbar.keys() and tools.instances("highlight") == ["highlight"]
    assert not menu.isVisible() and tools.tool == "select"

    menu = open_menu(workspace, "highlight")
    {a.text(): a for a in menu.more_menu().actions()}["Reset to default"].trigger()
    assert tools.subject("highlight") == "Text highlight"


def test_moving_a_tool_from_its_menu(workspace):
    toolbar = workspace.toolbar
    menu = open_menu(workspace, "ellipse")
    position = toolbar.keys().index("ellipse")
    {a.text(): a for a in menu.more_menu().actions()}["Move left"].trigger()
    assert toolbar.keys().index("ellipse") == position - 1
    {a.text(): a for a in menu.more_menu().actions()}["Move right"].trigger()
    assert toolbar.keys().index("ellipse") == position
    select = open_menu(workspace, "select")
    assert not {a.text(): a for a in select.more_menu().actions()}["Move left"].isEnabled()


def test_a_pinned_menu_stays_while_drawing_and_follows_the_tool(window, workspace):
    tools, view = workspace.tool_settings, workspace.view
    menu = open_menu(workspace, "pen")
    menu.pin_button.click()
    settle(40)
    assert menu.pinned and menu.isVisible() and window.settings.get("tool_menu_pinned") is True
    assert menu.windowFlags() & Qt.WindowType.Tool == Qt.WindowType.Tool  # clicks on the page go through
    drag(view, "pen", [(100, 100), (150, 130), (200, 110)])
    settle(40)
    assert menu.isVisible() and [a for a in workspace.store.all() if isinstance(a, Ink)]
    tools.set_tool("rectangle")
    settle(200)
    assert menu.isVisible() and menu.key == "rectangle" and full_title(menu) == "Rectangle"
    menu.pin_button.click()
    assert not menu.pinned and window.settings.get("tool_menu_pinned") is False


# The options -------------------------------------------------------------------------------------------


def test_each_tool_has_its_own_options(workspace):
    pen = open_menu(workspace, "pen")
    cards = pen.editor.blocks["pen_type"].findChild(OptionCards)
    assert [v for v, *_ in cards.items] == ["basic", "pro", "calligraphy"]
    assert {"ink_to_line", "ink_to_shape"} <= set(pen.editor.blocks) and "Smart ink" in pen.editor.sections
    cards.choose("calligraphy")
    settle(40)
    assert workspace.tool_settings.style("pen").pen == "calligraphy"
    nib = pen.editor.blocks["pen_type"].findChildren(SliderRow)[0]
    assert nib.isVisibleTo(pen.editor)  # the nib angle shows for calligraphy only
    cards.choose("basic")
    settle(40)
    assert not nib.isVisibleTo(pen.editor)

    eraser = open_menu(workspace, "eraser")
    whole_or_partial = eraser.editor.blocks["erase"].findChild(Segmented)
    assert [v for v, _ in whole_or_partial.items] == ["whole", "partial"]
    whole_or_partial.choose("partial")
    assert workspace.tool_settings.style("eraser").erase == "partial"
    assert not eraser.subject_row.isVisible()  # the eraser makes no annotations

    line = open_menu(workspace, "line")
    assert {"dash", "endings", "highlight"} <= set(line.editor.blocks)
    line.editor.blocks["dash"].choose("dashed")
    line.editor.endings_switch.choose("head_start")
    line.editor.blocks["endings"].findChild(OptionGrid).choose("Circle")
    style = workspace.tool_settings.style("line")
    assert style.dash == "dashed" and style.head_start == "Circle" and style.head_end == "None"
    assert workspace.tool_settings.style("arrow").head_end == "ClosedArrow"  # another tool keeps its own


def test_colour_fill_and_border_rules(workspace):
    tools = workspace.tool_settings
    menu = open_menu(workspace, "rectangle")
    editor = menu.editor
    before = editor.preview.grab().toImage()
    row = editor.blocks["color"].findChild(tool_menu.ColourRow)
    blue = next(i for i, (kind, value) in enumerate(row.slots()) if value == tool_menu.COLOURS[5])
    row.activate(blue)
    assert tools.style("rectangle").color == tool_menu.COLOURS[5]
    assert editor.preview.grab().toImage() != before  # the preview follows
    assert not row.none_allowed  # no border is refused while there is no fill
    editor.colour_switch.choose("fill")
    assert row.none and row.none_allowed and row.current is None
    row.activate(blue)
    assert tools.style("rectangle").fill == tool_menu.COLOURS[5]
    editor.colour_switch.choose("color")
    assert row.none_allowed
    row.activate(0)  # "no colour": no border
    assert tools.style("rectangle").border is False and row.current is None
    editor.colour_switch.choose("fill")
    assert not row.none_allowed  # and now the fill has to stay


def test_sliders_and_values(workspace):
    tools = workspace.tool_settings
    menu = open_menu(workspace, "rectangle")
    width = menu.editor.blocks["width"].findChild(SliderRow)
    width.box.setEditText("3.5 pt")
    width.box.lineEdit().editingFinished.emit()
    assert tools.style("rectangle").width == 3.5 and width.slider.value() == THICKNESS.position(3.5)
    width.box.setEditText("nonsense")
    width.box.lineEdit().editingFinished.emit()
    assert tools.style("rectangle").width == 3.5 and width.box.currentText() == "3.5 pt"
    opacity = menu.editor.blocks["opacity"].findChild(SliderRow)
    opacity.slider.setValue(opacity.scale.position(40))  # a click on the track or the arrow keys
    settle(80)
    assert tools.style("rectangle").opacity == pytest.approx(0.4)  # applied without letting go of anything
    assert THICKNESS.value(THICKNESS.position(1.5)) == 1.5 and THICKNESS.value(0) == 0.5


def test_settings_backed_options_and_the_scale(window, workspace, monkeypatch):
    select = open_menu(workspace, "select")
    row = select.editor.blocks["select_new"]
    row.switch.click()
    assert window.settings.get("select_new") is True
    row.switch.click()
    hand = open_menu(workspace, "hand")
    hand.editor.blocks["wheel"].findChild(Segmented).choose("zoom")
    assert window.settings.get("wheel") == "zoom"
    window.settings.set("wheel", "scroll")
    assert hand.editor.blocks["wheel"].findChild(Segmented).value == "scroll"  # follows changes made elsewhere

    from zenith_pdf.ui import insert_dialogs

    class Scale:
        DialogCode = insert_dialogs.ScaleDialog.DialogCode

        def __init__(self, measure, parent=None):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def measure(self):
            return 1.0, "cm", 2.0, "m"

        def deleteLater(self):
            pass

    monkeypatch.setattr(insert_dialogs, "ScaleDialog", Scale)
    distance = open_menu(workspace, "distance")
    distance.editor.scale_button.click()
    settle(40)
    assert workspace.tool_settings.measure == (1.0, "cm", 2.0, "m")
    assert "1 cm = 2 m" in distance.editor.scale_button.text() and distance.isVisible()
    workspace.tool_settings.set_measure(1.0, "cm", 1.0, "cm")


# The toolbar ---------------------------------------------------------------------------------------------


def test_copies_survive_a_rebuild_and_the_chooser_takes_them_all_off(window, workspace):
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    toolbar.add_tool("pen")
    copy = toolbar.duplicate_tool("pen")
    settle(40)
    assert copy == "pen#2" and tools.tool == "pen#2"
    toolbar.rebuild()
    assert "pen#2" in toolbar._buttons and toolbar._buttons["pen#2"].active
    assert toolbar._buttons["pen#2"].toolTip().startswith("Pen 2")
    assert toolbar.has_tool("pen") and toolbar.has_tool("pen#2")
    chooser = ToolChooser(toolbar, window)
    try:
        chooser._toggle("pen")
        assert not toolbar.has_tool("pen") and tools.instances("pen") == ["pen"]
        chooser._toggle("pen")
        assert toolbar.keys()[-1] == "pen"
    finally:
        chooser.close()


def test_the_chooser_scrolls_and_acts_on_the_tab_on_screen(window, workspace, sample_pdf, tmp_path):
    chooser = ToolChooser(workspace.toolbar, window)
    try:
        chooser.show_under(workspace.toolbar)
        chooser.search.setText("e")
        settle(40)
        tiles = chooser.results.findChildren(ToolTile)
        rows = sorted({t.pos().y() for t in tiles})
        assert len(tiles) > 10 and all(b - a >= tiles[0].height() for a, b in zip(rows, rows[1:]))  # no overlap
        assert chooser.results.widget().height() > chooser.results.viewport().height()  # it scrolls instead
        chooser.search.clear()
        other = tmp_path / "other.pdf"
        shutil.copy(sample_pdf, other)
        window.open_file(other)
        settle(40)
        assert chooser.toolbar() is window.current_workspace().toolbar
        window.close_tab(window.workspaces.index(window.current_workspace()), confirm=False)
    finally:
        chooser.close()


# Selections ------------------------------------------------------------------------------------------------


def test_the_selection_style_menu_changes_dash_and_endings(workspace):
    view, store = workspace.view, workspace.store
    line = Line(page=0, start=(100, 100), end=(300, 160))
    shape = Shape(page=0, rect=(100, 300, 80, 60), fill=(1.0, 1.0, 0.0))
    view.add_annotations([line, shape], "Shapes")
    view.select([line])
    settle(40)
    bar = workspace.selection_bar
    assert bar.isVisible() and bar.width() < 400  # icons only, small over the page
    bar.open_style()
    settle(40)
    menu = bar.style_menu
    assert menu.isVisible() and full_title(menu) == "Line"
    assert {"dash", "endings", "color", "width"} <= set(menu.editor.blocks)
    menu.editor.blocks["dash"].choose("dotted")
    menu.editor.endings_switch.choose("head_start")
    menu.editor.blocks["endings"].findChild(OptionGrid).choose("Circle")
    [changed] = [a for a in store.all() if isinstance(a, Line)]
    assert changed.dash == "dotted" and changed.head_start == "Circle" and changed.head_end == "None"
    workspace.undo_stack.undo()
    [changed] = [a for a in store.all() if isinstance(a, Line)]
    assert changed.dash == "dotted" and changed.head_start == "None"  # one change, one undo step

    view.select([a for a in store.all() if isinstance(a, (Line, Shape))])
    settle(40)
    bar.open_style()
    settle(40)
    assert menu.isVisible() and full_title(menu) == "2 annotations"
    assert "endings" not in menu.editor.blocks and "fill" not in menu.editor.blocks
    menu.close_menu()
    assert selection_options([shape]) == ("color", "fill", "width", "opacity", "pattern", "dash", "highlight")


def test_the_editor_alone_reports_only_what_changed():
    editor = StyleEditor()
    seen = []
    editor.changed.connect(seen.append)
    editor.set_options(("color", "width"), DEFAULT_STYLES["line"], base="line")
    editor.blocks["width"].findChild(SliderRow).box.setEditText("4")
    editor.blocks["width"].findChild(SliderRow).box.lineEdit().editingFinished.emit()
    assert seen == [{"width": 4.0}]
    editor.deleteLater()


def test_menus_follow_the_theme(window, workspace):
    menu = open_menu(workspace, "rectangle")
    dark = menu.grab().toImage()
    theme = window.settings.get("theme")
    window.settings.set("theme", "light" if themes.current.name == "dark" else "dark")
    settle(40)
    try:
        assert menu.grab().toImage() != dark
    finally:
        window.settings.set("theme", theme)
