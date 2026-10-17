"""Tool copies ("pen#2"), each tool's style, subject and lock, how they are stored, and what a tool does after
one use."""

import shutil
from dataclasses import fields

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Attachment, Ink, Note, Shape, TextMarkup
from zenith_pdf.settings import Settings
from zenith_pdf.ui import audio_dialog, messages, tool_catalog
from zenith_pdf.ui.tool_catalog import (
    BY_KEY,
    CATALOG,
    base_of,
    instance_number,
    instance_title,
    is_valid_key,
    toolbar_keys,
)
from zenith_pdf.ui.tools import (
    DEFAULT_STYLES,
    FIELD_CHECKS,
    LOCK_DEFAULTS,
    LOCKABLE,
    OPTION_FIELDS,
    OPTIONS,
    SETTING_OPTIONS,
    STYLE_CONTROLS,
    ToolSettings,
    ToolStyle,
)


def test_instance_keys():
    assert base_of("pen#2") == "pen" and base_of("pen") == "pen"
    assert instance_number("pen") == 1 and instance_number("pen#3") == 3
    assert instance_title("highlight#2") == "Text highlight 2" and instance_title("pen") == "Pen"
    assert is_valid_key("pen") and is_valid_key("pen#2") and is_valid_key("highlight#99")
    for bad in ("pen#1", "pen#0", "pen#02", "pen#x", "pen#", "pen#100", "select#2", "hand#2", "image#2", "nope",
                "nope#2", 3, None):
        assert not is_valid_key(bad), bad
    assert toolbar_keys(["pen", "pen#2", "pen#2", "pen#x", "nope", 7, "hand"]) == ["select", "pen", "pen#2", "hand"]
    assert toolbar_keys([]) == list(tool_catalog.DEFAULT_TOOLBAR)


def test_every_tool_has_a_style_options_and_checks():
    assert set(DEFAULT_STYLES) == set(BY_KEY) == set(OPTIONS)
    assert set(FIELD_CHECKS) == {f.name for f in fields(ToolStyle)}
    known = set(OPTION_FIELDS) | set(SETTING_OPTIONS)
    for base, names in OPTIONS.items():
        assert set(names) <= known, base
    for names in OPTION_FIELDS.values():
        assert set(names) <= set(FIELD_CHECKS)
    # The older style popup and settings table still find what they used to.
    assert STYLE_CONTROLS["pen"] == {"color", "width", "opacity"}
    assert STYLE_CONTROLS["eraser"] == {"width"} and STYLE_CONTROLS["distance"] == {"color", "width", "scale"}
    assert not LOCKABLE & {"select", "hand", "laser", "lasso", "bookmark", "camera", "image"}
    assert LOCKABLE == {spec.key for spec in CATALOG if not spec.action} - {"select", "hand", "laser", "lasso"}


def test_copies_have_their_own_style_and_subject(qt_app):
    tools = ToolSettings()
    seen = []
    tools.instances_changed.connect(lambda: seen.append(True))
    key = tools.duplicate("highlight")
    assert key == "highlight#2" and seen
    tools.update_style(key, color=(0.2, 0.8, 0.3))
    assert tools.style("highlight").color == DEFAULT_STYLES["highlight"].color
    assert tools.style(key).color == (0.2, 0.8, 0.3)
    assert tools.duplicate("highlight", taken=["highlight#3"]) == "highlight#4"
    assert tools.instances("highlight") == ["highlight", "highlight#2", "highlight#4"]
    tools.set_subject(key, "  Important ")
    assert tools.subject(key) == "Important" and tools.subject("highlight") == "Text highlight"
    tools.set_subject(key, "Text highlight")  # the title is the default
    assert tools.config(key).subject == ""
    tools.set_tool(key)
    assert tools.style_for("highlight").color == (0.2, 0.8, 0.3)
    assert tools.style_for("pen") == tools.style("pen")
    tools.forget(key)
    assert tools.tool == "select" and key not in tools.instances("highlight")
    tools.forget("highlight")  # the tool itself stays
    assert tools.config("highlight").style == DEFAULT_STYLES["highlight"]


def test_only_real_tools_turn_on(qt_app):
    tools = ToolSettings()
    seen = []
    tools.tool_changed.connect(seen.append)
    for key in ("image", "camera", "bookmark", "pen#x", "nope", "select"):  # actions act once; select is on
        tools.set_tool(key)
    assert tools.tool == "select" and seen == []
    tools.set_tool("pen")
    tools.set_tool("pen")  # a second time changes nothing
    assert seen == ["pen"]


def test_style_values_are_checked(qt_app):
    tools = ToolSettings()
    tools.update_style("line", dash="dotted", head_end="Diamond", width=500, opacity="half", nonsense=1)
    style = tools.style("line")
    assert (style.dash, style.head_end, style.width, style.opacity) == ("dotted", "Diamond", 100.0, 1.0)
    tools.update_style("line", dash="wavy", head_start="Star", highlight="yes")
    assert tools.style("line") == style
    tools.update_style("rectangle", border=False)  # with no fill, a shape keeps its border
    assert tools.style("rectangle").border
    tools.update_style("rectangle", fill=(1, 1, 1), border=False)
    assert tools.style("rectangle").fill == (1.0, 1.0, 1.0) and not tools.style("rectangle").border
    tools.update_style("cleaner", clean_kinds=["strikeout", "highlight"])
    assert tools.style("cleaner").clean_kinds == ("highlight", "strikeout")
    tools.update_style("cleaner", clean_kinds=[])  # a cleaner that cleans nothing is refused
    assert tools.style("cleaner").clean_kinds == ("highlight", "strikeout")
    tools.update_style("snapshot", snapshot_scale=2.0)
    assert tools.style("snapshot").snapshot_scale == 2
    tools.reset_style("line")
    assert tools.style("line") == DEFAULT_STYLES["line"]
    assert tools.style("arrow").head_end == "ClosedArrow"


def test_locks(qt_app):
    tools = ToolSettings()
    assert tools.is_locked("pen") and not tools.is_locked("rectangle")
    assert tools.is_locked("select") and tools.is_locked("hand") and tools.is_locked("laser")
    assert all(tools.is_locked(key) == locked for key, locked in LOCK_DEFAULTS.items())
    tools.set_locked("rectangle", True)
    assert tools.is_locked("rectangle")
    copy = tools.duplicate("rectangle")
    assert tools.is_locked(copy)  # a copy keeps the lock
    tools.set_locked("rectangle", None)
    assert not tools.is_locked("rectangle") and tools.is_locked(copy)
    tools.set_locked("hand", False)  # can't be unlocked
    assert tools.is_locked("hand")


def test_stored_and_loaded(qt_app):
    tools = ToolSettings()
    copy = tools.duplicate("pen")
    tools.update_style(copy, color=(1.0, 0.0, 0.0), pen="pro", ink_to_shape=True)
    tools.set_subject(copy, "Sketch")
    tools.set_locked("line", True)
    tools.set_measure(1, "cm", 2, "m")
    fresh = ToolSettings()
    seen = []
    fresh.instances_changed.connect(lambda: seen.append(True))
    fresh.load_styles(tools.styles_data())
    assert fresh.config(copy) == tools.config(copy) and fresh.is_locked("line") and seen
    assert fresh.measure == (1.0, "cm", 2.0, "m")
    assert fresh.styles_data() == tools.styles_data()


def test_older_and_damaged_data_loads(qt_app):
    tools = ToolSettings()
    tools.load_styles({
        "pen": {"color": [1, 0, 0], "width": 3},  # the format before tool copies
        "line": {"style": {"color": [0, 0, 1], "dash": "wavy", "width": "thick"}, "subject": 5, "locked": "yes"},
        "pen#x": {"style": {"color": [0, 1, 0]}},
        "select#2": {"style": {}},
        "hand": {"style": {}, "locked": False},
        "measure": "nonsense",
    })
    assert tools.style("pen").color == (1.0, 0.0, 0.0) and tools.style("pen").width == 3.0
    line = tools.config("line")
    assert line.style.color == (0.0, 0.0, 1.0) and line.style.dash == "solid" and line.style.width == 2.0
    assert line.subject == "" and line.locked is None
    assert tools.instances("pen") == ["pen"] and tools.config("hand").locked is None


def test_after_drawing_is_migrated(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store.setValue("after_drawing", "keep")
    store.sync()
    assert Settings(store).get("after_drawing") == "pan"
    store.setValue("after_drawing", "select")
    assert Settings(store).get("after_drawing") == "select"
    store.setValue("after_drawing", "sideways")
    assert Settings(store).get("after_drawing") == "select"
    store.setValue("after_drawing", "pan")  # the default before 0.5.0, moved to Select once
    settings = Settings(store)
    settings.upgrade()
    assert settings.get("after_drawing") == "select"
    settings.set("after_drawing", "pan")  # chosen after that, it stays
    settings.upgrade()
    assert settings.get("after_drawing") == "pan"
    assert Settings(store).get("tool_menu_pinned") is False


# In a window: what the tools draw, and where they go after one use -------------------------------


class Mouse:
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier

    def globalPosition(self):
        return QPointF(400, 300)


def drag(view, key, points, page=0):
    view.tool_settings.set_tool(key)
    tool = view._tool()  # a copy shares its tool's Tool
    item = view._pages[page]
    tool.press(item, QPointF(*points[0]), Mouse())
    for point in points[1:]:
        tool.move(item, QPointF(*point), Mouse())
    tool.release(item, QPointF(*points[-1]), Mouse())
    return tool


def of(store, kind):
    return [a for a in store.all() if isinstance(a, kind)]


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
    QTest.qWait(30)
    yield workspace
    tools = workspace.tool_settings
    tools.set_tool("select")
    for key in DEFAULT_STYLES:
        tools.reset_style(key)
        tools.set_locked(key, None)
        tools.set_subject(key, "")
    for base in DEFAULT_STYLES:
        for key in tools.instances(base)[1:]:
            tools.forget(key)
    window.settings.set("after_drawing", "pan")
    window.settings.set("select_new", False)
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_a_copy_draws_with_its_own_style_and_subject(workspace):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    key = tools.duplicate("highlight")
    tools.update_style(key, color=(0.2, 0.8, 0.3))
    tools.set_subject(key, "Important")
    hit = next(workspace.document.iter_search("needle", False, False))
    x, y, w, h = hit.rects[0]
    tool = drag(view, key, [(x + 1, y + h / 2), (x + w - 1, y + h / 2)], page=hit.page)
    assert tool.key == key and tools.tool == key  # the highlighter is locked: it stays on
    [markup] = of(store, TextMarkup)
    assert markup.color == (0.2, 0.8, 0.3) and markup.subject == "Important" and markup.style == "Highlight"
    drag(view, "highlight", [(x + 1, y + h / 2), (x + w - 1, y + h / 2)], page=hit.page)
    assert {m.subject for m in of(store, TextMarkup)} == {"Important", "Text highlight"}

    pen = tools.duplicate("pen")
    tools.update_style(pen, color=(1.0, 0.0, 0.0), width=5.0)
    drag(view, pen, [(100, 300), (150, 320), (200, 300)])
    [ink] = of(store, Ink)
    assert ink.color == (1.0, 0.0, 0.0) and ink.width == 5.0 and ink.subject == "Pen"


def test_unlocked_tools_go_back_to_pan_and_locked_ones_stay(workspace):
    view, tools, settings = workspace.view, workspace.tool_settings, workspace.settings
    drag(view, "pen", [(100, 100), (150, 120), (200, 100)])
    assert tools.tool == "pen"
    tools.set_locked("pen", False)
    drag(view, "pen", [(100, 200), (150, 220), (200, 200)])
    assert tools.tool == "hand"
    drag(view, "rectangle", [(300, 300), (360, 340)])
    assert tools.tool == "hand" and not view.selected_annotations()
    tools.set_locked("rectangle", True)
    drag(view, "rectangle", [(300, 400), (360, 440)])
    assert tools.tool == "rectangle"
    drag(view, "eraser", [(500, 600), (510, 610)])  # erases nothing: no use to count
    assert tools.tool == "eraser"

    settings.set("after_drawing", "select")
    assert tools.after_drawing == "select"
    drag(view, "ellipse", [(300, 500), (360, 540)])
    assert tools.tool == "select" and not view.selected_annotations()  # selecting is "select new annotations"

    settings.set("after_drawing", "pan")
    settings.set("select_new", True)
    drag(view, "ellipse", [(100, 500), (160, 540)])
    [newest] = view.selected_annotations()
    assert tools.tool == "select" and isinstance(newest, Shape) and newest.rect[0] == 100


def test_the_tool_that_is_on_always_shows_on_the_toolbar(workspace):
    """Taken off the toolbar it goes over to Select, and without Pan there an unlocked tool hands over to Select;
    one turned on from the tool chooser without being added shows in the overflow button, which anchors its menu."""
    view, tools, toolbar = workspace.view, workspace.tool_settings, workspace.toolbar
    settings = workspace.settings
    keys = list(toolbar.keys())
    try:
        tools.set_tool("ellipse")
        toolbar.remove_tool("ellipse")
        assert tools.tool == "select"
        toolbar.remove_tool("hand")
        drag(view, "rectangle", [(300, 300), (360, 340)])
        assert tools.tool == "select"  # not Pan, which is no longer on the toolbar
        toolbar.use("pen")  # as the chooser's tile does
        QTest.qWait(10)
        assert tools.tool == "pen" and not toolbar.has_tool("pen")
        assert toolbar.overflow.isVisible() and toolbar.anchor_for("pen") is toolbar.overflow
        assert toolbar.overflow.menu().actions()[0].isChecked()
        toolbar.reset()
        assert tools.tool == "select"
        assert not any(a.text() == "Pen" for a in toolbar.overflow.menu().actions())
    finally:
        settings.set("toolbar_tools", keys)


def test_the_toolbar_from_the_keyboard(window, workspace):
    """F6 goes to the tool that is on; the arrows move along, Enter turns a tool on, the Menu key opens its menu,
    Esc goes back to the pages. The toolbar is one Tab stop."""
    from PySide6.QtWidgets import QApplication

    toolbar, tools, view = workspace.toolbar, workspace.tool_settings, workspace.view
    window.activateWindow()
    view.setFocus()
    QTest.keyClick(window, Qt.Key.Key_F6)
    assert QApplication.focusWidget() is toolbar._buttons["select"]
    assert [b.key for b in toolbar._buttons.values() if b.focusPolicy() != Qt.FocusPolicy.NoFocus] == ["select"]
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Right)
    assert QApplication.focusWidget() is toolbar._buttons["hand"]
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)
    assert tools.tool == "hand"
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Menu)
    menu = toolbar.tool_menu
    assert menu is not None and menu.isVisible() and menu.key == "hand"
    menu.close_menu()
    toolbar._buttons["hand"].setFocus()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_End)
    assert QApplication.focusWidget() is toolbar.add_button
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert QApplication.focusWidget() is view


def test_a_menu_entry_does_not_count_as_a_use_of_another_tool(workspace, monkeypatch):
    view, tools = workspace.view, workspace.tool_settings
    monkeypatch.setattr(messages, "get_multiline_text", lambda *a, **k: "From the menu")
    tools.set_tool("rectangle")
    view.new_note(0, (100.0, 100.0))
    assert tools.tool == "rectangle"
    tools.update_style("note", note_icon="Key", color=(0.3, 0.6, 1.0))
    drag(view, "note", [(200, 200)])
    notes = sorted(of(workspace.store, Note), key=lambda n: n.position)
    assert [n.contents for n in notes] == ["From the menu"] * 2 and notes[1].icon == "Key"
    assert notes[1].color == (0.3, 0.6, 1.0) and notes[1].subject == "Note"
    assert tools.tool == "hand"  # the note tool is unlocked by default


def test_the_audio_tool_records_or_attaches_where_clicked(workspace, monkeypatch):
    view, tools = workspace.view, workspace.tool_settings
    asked = []

    def answer(parent, source, start_dir, global_pos=None):
        asked.append((source, global_pos))
        return "Audio note 2026-10-16 10.00.wav", b"RIFF....WAVEfmt "

    monkeypatch.setattr(audio_dialog, "ask_audio", answer)
    tools.update_style("audio", audio_source="record")
    drag(view, "audio", [(200, 200)])
    [note] = of(workspace.store, Attachment)
    assert note.audio and note.file_name.endswith(".wav") and note.data == b"RIFF....WAVEfmt "
    assert asked == [("record", QPoint(400, 300))] and note.subject == "Audio note"
    assert tools.tool == "hand"

    played = []
    monkeypatch.setattr(audio_dialog, "play_audio", lambda *a, **k: played.append(a[1:3]))
    view.open_attachment(note)
    assert played == [(note.file_name, note.data)]  # played in place, not handed to another app
