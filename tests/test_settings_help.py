"""Settings that apply at once, the settings window, and the help badges."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QHelpEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QSpinBox, QWidget

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import widgets
from zenith_pdf.ui.help import TOPICS, badges, help_card
from zenith_pdf.ui.tools import DEFAULT_STYLES, ToolSettings


def settle(ms=50):
    QTest.qWait(ms)
    QApplication.processEvents()


def test_settings_keep_types_and_old_values(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store.setValue("recent", ["C:/a.pdf", "C:/b.pdf"])  # how older versions stored it
    store.setValue("render_processes", 4)
    settings = Settings(store)
    assert [p.name for p in settings.recent_files()] == ["a.pdf", "b.pdf"]
    assert settings.render_processes == 4
    assert settings.get("glass") == "clear" and settings.get("scroll_speed") == 100
    seen = []
    settings.changed.connect(seen.append)
    settings.set("glass", "balanced")
    settings.set("autosave_minutes", 5)
    again = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert again.get("glass") == "balanced" and again.get("autosave_minutes") == 5
    assert seen == ["glass", "autosave_minutes"]
    for old, style in ((True, "clear"), (False, "solid"), ("frosted", "clear")):  # the switch of older versions
        store = QSettings(str(tmp_path / f"old-{style}-{old}.ini"), QSettings.Format.IniFormat)
        store.setValue("glass", old)
        assert Settings(store).get("glass") == style
    settings.set("recent_limit", 1)
    settings.add_recent(tmp_path / "c.pdf")
    assert [p.name for p in settings.recent_files()] == ["c.pdf"]


def test_tool_styles_are_stored_and_loaded(qt_app):
    tools = ToolSettings()
    tools.update_style("highlight", color=(0.1, 0.8, 0.3), opacity=0.5)
    data = tools.styles_data()
    fresh = ToolSettings()
    fresh.load_styles(data | {"nonsense": {"color": [1, 1, 1]}, "rectangle": {"width": "thick"}})
    assert fresh.style("highlight").color == (0.1, 0.8, 0.3)
    assert fresh.style("highlight").opacity == 0.5
    assert fresh.style("rectangle").width == 2.0  # the bad value was skipped


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 850)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    if window._settings_window is not None:
        window._settings_window.close()
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    settle()
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_settings_apply_at_once(window, workspace):
    settings = window.settings
    settings.set("theme", "light")
    assert themes.current.name == "light"
    settings.set("accent", "green")
    assert themes.current.accent != "#0a6cf0"
    settings.set("accent", "blue")
    settings.set("theme", "dark")

    settings.set("glass", "solid")
    workspace.toolbar.refresh_backdrop()
    assert workspace.toolbar._backdrop is None
    settings.set("glass", "balanced")
    assert widgets.glass.enabled and widgets.glass.style == "balanced"
    settings.set("glass", "clear")
    assert widgets.glass.style == "clear"

    settings.set("author", "Ada")
    assert window.tool_settings.author == "Ada"
    settings.set("after_drawing", "select")
    assert window.tool_settings.return_to_select and window.tool_settings.after_drawing == "select"
    settings.set("after_drawing", "pan")
    assert window.tool_settings.after_drawing == "pan"


def test_wheel_can_zoom_without_ctrl(window, workspace):
    view = workspace.view
    window.settings.set("wheel", "zoom")
    before = view.zoom
    centre = QPointF(view.viewport().rect().center())
    event = QWheelEvent(centre, view.viewport().mapToGlobal(centre), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)
    assert view.zoom_target() > before  # gliding there
    window.settings.set("wheel", "scroll")


def test_settings_window_search_and_controls(window):
    window.open_settings()
    dialog = window._settings_window
    settle()
    assert dialog.sections["view"].isVisible() and not dialog.sections["files"].isVisible()
    dialog.search.setText("autosave")
    settle()
    assert dialog.sections["files"].isVisible() and not dialog.sections["view"].isVisible()
    visible = [row for row in dialog.sections["files"].rows if row.isVisible()]
    assert len(visible) == 1
    dialog.search.setText("no such setting anywhere")
    assert dialog.empty.isVisible()
    dialog.search.clear()
    glass = next(w for w in dialog.findChildren(widgets.Segmented) if [v for v, _ in w.items][0] == "clear")
    assert glass.value == "clear"
    window.settings.set("glass", "solid")  # the window follows changes made elsewhere
    assert glass.value == "solid"
    glass.choose("balanced")  # and a click on the control changes the setting, at once
    assert window.settings.get("glass") == "balanced" and widgets.glass.style == "balanced"
    window.settings.set("glass", "clear")
    dialog.show_category("about")
    settle()
    assert dialog.sections["about"].isVisible()
    dialog.close()


def test_help_card_when_pointing_and_optional_badges(window, workspace):
    button = workspace.toolbar._buttons["highlight"]
    hook = button._help_hook
    assert badges.mode == "hover" and (hook.badge is None or not hook.badge.isVisible())  # no ? by default
    tooltip = QHelpEvent(QEvent.Type.ToolTip, QPoint(5, 5), button.mapToGlobal(QPoint(5, 5)))
    assert hook.eventFilter(button, tooltip)  # the card replaces the plain tooltip

    card = help_card()
    card.show_for(hook, pinned=False)
    settle()
    assert card.isVisible() and card.title.text() == TOPICS["tool_highlight"].title
    hook.eventFilter(button, QEvent(QEvent.Type.Leave))
    settle(300)
    assert not card.isVisible()  # pointing elsewhere closes it

    badges.set_mode("badges")
    assert hook.badge is not None and hook.badge.isVisible()
    assert not hook.eventFilter(button, tooltip)  # with badges, tooltips stay as they were
    badges.set_mode("never")
    assert not hook.badge.isVisible()
    badges.set_mode("hover")

    undo = workspace.undo_stack
    workspace.view.add_annotations([], "Rectangle")
    detail = workspace.canvas.bars["undo"].findChildren(widgets.IconButton)[0].property("help_detail")
    assert detail == "Undo add rectangle"
    undo.undo()


def test_every_help_key_in_use_has_a_topic(window, workspace):
    keys = {w.property("help_topic") for w in window.findChildren(object) if hasattr(w, "property")
            and w.property("help_topic")}
    assert keys and keys <= set(TOPICS)


def wheel_over(widget):
    centre = QPointF(widget.rect().center())
    return QWheelEvent(centre, widget.mapToGlobal(centre), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)


def test_the_wheel_scrolls_the_settings_and_changes_nothing(window, tmp_path):
    window.open_settings("view")
    dialog = window._settings_window
    settle()
    theme = window.settings.get("theme")
    combo = next(c for c in dialog.findChildren(QComboBox) if c.currentData() == theme)
    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus
    QApplication.sendEvent(combo, wheel_over(combo))
    assert window.settings.get("theme") == theme  # pointing at it while scrolling changes nothing

    for number in range(5):
        window.settings.add_recent(tmp_path / f"{number}.pdf")
    dialog.show_category("files")
    settle()
    limit = next(s for s in dialog.findChildren(QSpinBox) if s.value() == window.settings.get("recent_limit"))
    before = window.settings.get("recent_limit")
    limit.setValue(1)  # typing 10 starts with a 1
    assert len(window.settings.get("recent")) >= 5 and len(window.settings.recent_files()) == 1
    limit.setValue(before)
    assert len(window.settings.recent_files()) >= 5
    dialog.close()


def test_a_category_click_leaves_the_search(window):
    window.open_settings()
    dialog = window._settings_window
    dialog.search.setText("zoom")
    settle()
    assert dialog.sections["navigation"].isVisible() and dialog.sections["view"].isVisible()
    item = next(dialog.nav.item(r) for r in range(dialog.nav.count())
                if dialog.nav.item(r).data(Qt.ItemDataRole.UserRole) == "performance")
    dialog.nav.itemClicked.emit(item)
    dialog.nav.setCurrentItem(item)
    settle()
    assert dialog.search.text() == "" and dialog.sections["performance"].isVisible()
    assert not dialog.sections["view"].isVisible()
    dialog.close()


def test_tool_styles_open_each_tools_menu(window):
    from zenith_pdf.ui.tool_menu import ColourRow

    tools = window.tool_settings
    tools.set_tool("select")
    window.open_settings("annotation")
    dialog = window._settings_window
    settle()
    rows = dialog.tool_list.rows
    assert "rectangle" in rows and "select" not in rows and "bookmark" not in rows
    rows["rectangle"].clicked.emit("rectangle")
    settle()
    menu = dialog.tool_list.menu
    assert menu.isVisible() and menu.key == "rectangle" and menu.toolbar is None
    assert menu.lock_button.isVisible() and not menu.remove_button.isVisible() and not menu.pin_button.isVisible()
    texts = [a.text() for a in menu.more_menu().actions() if a.text()]
    assert texts == ["Reset to default", "Help"]  # nothing that needs a toolbar
    row = menu.editor.blocks["color"].findChild(ColourRow)
    row.activate(next(i for i, (kind, value) in enumerate(row.slots()) if kind == "colour" and value != tools.style(
        "rectangle").color))
    changed = tools.style("rectangle").color
    assert changed != DEFAULT_STYLES["rectangle"].color and tools.tool != "rectangle"  # the settings choose no tool
    menu.lock_button.click()
    assert tools.is_locked("rectangle") and rows["rectangle"].summary.text().startswith("Locked")
    menu.close_menu()

    copy = tools.duplicate("pen")
    settle()
    assert copy in dialog.tool_list.rows and dialog.tool_list.rows[copy].name.text() == "Pen 2"
    tools.forget(copy)
    settle()
    assert copy not in dialog.tool_list.rows

    dialog._lock_all(False)
    assert not any(tools.is_locked(k) for k in ("pen", "highlight", "rectangle", "distance"))
    dialog._lock_all(True)
    assert all(tools.is_locked(k) for k in ("pen", "highlight", "rectangle", "distance"))
    for key in ("pen", "highlight", "rectangle", "distance"):
        tools.set_locked(key, None)
    tools.reset_style("rectangle")
    combo = next(c for c in dialog.findChildren(QComboBox) if c.findData("select") >= 0 and c.findData("pan") >= 0)
    combo.setCurrentIndex(combo.findData("select"))
    assert window.settings.get("after_drawing") == "select" and tools.after_drawing == "select"
    combo.setCurrentIndex(combo.findData("pan"))
    dialog.close()


def test_about_names_the_app_once_with_real_versions(window):
    from zenith_pdf.ui.settings_page import version_details

    window.open_settings("about")
    dialog = window._settings_window
    settle()
    about = dialog.sections["about"]
    names = [label for label in about.findChildren(QLabel) if label.text() == "Zenith PDF"]
    assert len(names) == 1
    assert dialog.logo.pixmap() is not None and not dialog.logo.pixmap().isNull()
    assert "PDFium, through pypdfium2: ?" not in version_details() and "/ ?" not in version_details()
    dialog.close()


def test_help_cards_fit_their_text(window, workspace):
    card = help_card()
    hooks = {w.property("help_topic"): w._help_hook for w in window.findChildren(QWidget)
             if w.property("help_topic") and hasattr(w, "_help_hook")}
    shown = 0
    for key in ("new", "tools", "full_screen", "tool_rectangle", "add_tools", "tool_highlight", "undo"):
        if key in hooks:  # one after the other: each card sized for its own text, not the last one's
            card.show_for(hooks[key], pinned=False)
            layout = card.glass.layout()
            assert card.glass.height() >= layout.totalHeightForWidth(card.glass.width()), key
            shown += 1
    card.close_card()
    assert shown >= 5


def test_the_guide_says_when_nothing_matches(window):
    from zenith_pdf.ui.guide import GuideWindow

    guide = GuideWindow(window)
    guide.show()
    settle()
    assert guide.text.frameShape() == guide.text.frameShape().NoFrame
    guide.show_chapter("tools")
    assert "lock" in guide.text.toPlainText().lower() and "Pan" in guide.text.toPlainText()
    assert not guide.bottom.isVisible()
    guide.show_chapter("problem")
    assert guide.bottom.isVisible()
    guide.search.setText("xyzzy")
    settle()
    assert "No chapter mentions" in guide.text.toPlainText() and guide.list.currentItem() is None
    assert not guide.bottom.isVisible()
    guide.search.clear()
    settle()
    assert guide.list.currentItem() is not None and "No chapter" not in guide.text.toPlainText()
    guide.close()


def test_settings_icons_follow_the_theme(window):
    window.open_settings("view")
    dialog = window._settings_window
    theme = window.settings.get("theme")
    before = dialog.nav.item(0).icon().pixmap(22, 22).toImage()
    window.settings.set("theme", "light" if themes.current.name == "dark" else "dark")
    try:
        assert dialog.nav.item(0).icon().pixmap(22, 22).toImage() != before  # not drawn for the other theme
    finally:
        window.settings.set("theme", theme)
        dialog.close()


def test_a_pinned_help_card_goes_with_its_tab(window, workspace, sample_pdf, tmp_path):
    other = tmp_path / "other.pdf"
    shutil.copy(sample_pdf, other)
    hook = workspace.toolbar._buttons["rectangle"]._help_hook
    card = help_card()
    card.show_for(hook, pinned=True)
    settle()
    assert card.isVisible()
    window.open_file(other)  # another tab shows, as Ctrl+Tab would
    settle()
    assert not card.isVisible()
    window.close_tab(window.workspaces.index(window.current_workspace()), confirm=False)


def test_copy_buttons_say_copied_for_a_moment(window, monkeypatch):
    from zenith_pdf.ui.guide import GuideWindow

    monkeypatch.setattr(widgets, "COPIED_MS", 30)
    guide = GuideWindow(window)
    guide.show_chapter("problem")
    guide.copy.click()
    assert guide.copy.text() == "Copied"
    settle(120)
    assert guide.copy.text() == "Copy version details"
    guide.copy.click()
    guide.show_chapter("problem")  # shown again: nothing copied yet
    assert guide.copy.text() == "Copy version details"
    guide.close()
    window.open_settings("about")
    dialog = window._settings_window
    copy = next(b for b in dialog.sections["about"].findChildren(QPushButton) if b.text() == "Copy details")
    copy.click()
    assert copy.text() == "Copied"
    settle(120)
    assert copy.text() == "Copy details"
    dialog.close()
