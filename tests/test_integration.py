"""Where the parts meet: text direction from the document to the markup tools, page turns and the
appearances other viewers draw, the text box editor on a turned page, pinned menus and hand-overs,
and the dialogs and widgets every part shares."""

import gc
import io
import shutil
import weakref

import pikepdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSettings
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QSpinBox

from conftest import settle
from zenith_pdf.app import SPIN_INSET, create_app
from zenith_pdf.core.annotations import Attachment, FreeText, Ink, Note, Shape, TextMarkup
from zenith_pdf.core.builder import Entry, Source, build
from zenith_pdf.core.document import Document
from zenith_pdf.core.pdf_annots import redraw_turned
from zenith_pdf.icons import readable
from zenith_pdf.settings import Settings
from zenith_pdf.theme import DARK, LIGHT
from zenith_pdf.ui import dialogs, messages
from zenith_pdf.ui.context_menu import build_context_menu
from zenith_pdf.ui.tool_catalog import DEFAULT_TOOLBAR
from zenith_pdf.ui.tools import DEFAULT_STYLES


class Mouse:
    def modifiers(self):
        from PySide6.QtCore import Qt

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
    settle()
    yield workspace
    tools = workspace.tool_settings
    tools.set_tool("select")
    for key in DEFAULT_STYLES:
        tools.reset_style(key)
        tools.set_locked(key, None)
    menu = workspace.toolbar.tool_menu
    if menu is not None and menu.isVisible():
        if menu.pinned:
            menu.pin_button.click()
        menu.close_menu()
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def found(document, page, text="needle"):
    return next(h for h in document.iter_search(text, False, False) if h.page == page).rects[0]


# Text direction ---------------------------------------------------------------------------------


def test_markups_run_along_vertical_text(workspace):
    """On a page turned 90 degrees the text runs down the screen: underlines run along it, not across."""
    view, store, document = workspace.view, workspace.store, workspace.document
    x, y, w, h = found(document, 0)
    assert document.text_turns(0, document.char_at(0, (x + w / 2, y + h / 2))) == 0
    x, y, w, h = found(document, 1, "Page 2")
    end_x, end_y, end_w, end_h = found(document, 1)
    assert h > w and end_y > y  # down the screen
    start, end = (x + w / 2, y + 1), (end_x + end_w / 2, end_y + end_h - 1)
    drag(view, "underline", [start, end], page=1)
    drag(view, "highlight", [start, end], page=1)
    marks = [a for a in store.all() if isinstance(a, TextMarkup)]
    assert len(marks) == 2 and {a.rotation for a in marks} == {1}
    assert all(len(a.quads) == 1 for a in marks)  # the words of the line join along it
    view.tool_settings.set_tool("select")
    drag(view, "select", [start, end], page=1)
    assert view.has_text_selection()
    view.mark_text_selection("strikeout")
    struck = [a for a in store.all() if isinstance(a, TextMarkup) and a.style == "StrikeOut"]
    assert len(struck) == 1 and struck[0].rotation == 1


# Page turns and other viewers ---------------------------------------------------------------------


def test_turned_pages_have_notes_drawn_for_their_turn(sample_pdf, tmp_path):
    """Rotate page and the Doc Builder redraw notes, so other viewers show them upright as the canvas does.

    The builder's second copy of a page keeps its own turn when the first copy is turned.
    """
    path = tmp_path / "turn.pdf"
    shutil.copy(sample_pdf, path)
    document = Document(path)
    notes = [Note(page=0, position=(60, 60)), Attachment(page=0, position=(120, 60), file_name="a.wav",
                                                          data=b"RIFF", audio=True)]
    document.rotate(notes, [0], 90)
    with pikepdf.open(io.BytesIO(document.data)) as pdf:
        assert redraw_turned(pdf, 0) == 0  # already drawn for the page as it is now
    document.save(document.annotations)
    document.close()
    result = build({"a": Source("a", path)}, [Entry("a", 0, rotation=90), Entry("a", 0)])
    with pikepdf.open(io.BytesIO(result)) as pdf:
        assert [page.obj.Rotate for page in pdf.pages] == [180, 90]
        assert redraw_turned(pdf, 0) == 0 and redraw_turned(pdf, 1) == 0


def test_a_text_box_on_a_turned_page_is_edited_as_it_is_shown(workspace):
    view, store = workspace.view, workspace.store
    box = FreeText(page=1, rect=(200, 100, 30, 220), rotation=1, contents="Turned", font_size=12)
    view.add_annotations([box], "Text box")
    view.edit_text_box(box, is_new=False)
    editor = view._editor[0]
    page = view._pages[1]
    shown = page.mapRectToScene(QRectF(*box.rect))
    frame = editor.frame.mapRectToScene(editor.frame.rect())  # the box, without the pen round it
    assert abs(frame.width() - shown.width()) < 1 and abs(frame.height() - shown.height()) < 1
    assert abs(frame.left() - shown.left()) < 1 and abs(frame.top() - shown.top()) < 1
    editor.setPlainText("Turned\nand a second line\nand a third")
    view.finish_text_edit()
    grown = store.get(box.id)
    x, y, w, h = grown.rect
    assert grown.rotation == 1 and w > 30 and abs((x + w) - (200 + 30)) < 1e-6  # grew away from its first line
    settle()
    assert not view.is_editing_text()


def test_a_cloud_needs_a_real_drag(workspace):
    view, store = workspace.view, workspace.store
    drag(view, "cloud", [(100, 100), (101, 101)])
    assert not [a for a in store.all() if isinstance(a, Shape)]  # its bumps don't make a click big enough
    drag(view, "cloud", [(100, 100), (160, 150)])
    assert len([a for a in store.all() if isinstance(a, Shape) and a.cloudy]) == 1


def test_a_pro_stroke_keeps_its_shape_when_its_thickness_changes(workspace):
    view, store = workspace.view, workspace.store
    ink = Ink(page=0, strokes=(((100, 100), (150, 120), (200, 100)),), width=4.0, widths=((1.0, 4.0, 2.0),))
    view.add_annotations([ink], "Ink")
    view.tool_settings.set_tool("select")
    view.select([ink])
    view.apply_style(width=8.0)
    changed = store.get(ink.id)
    assert changed.width == 8.0 and changed.widths == ((2.0, 8.0, 4.0),)


# Pinned menus and hand-overs -------------------------------------------------------------------------


def test_a_pinned_menu_stays_with_an_unlocked_tool_after_its_use(workspace):
    tools, view, toolbar = workspace.tool_settings, workspace.view, workspace.toolbar
    tools.set_tool("rectangle")
    toolbar.open_menu("rectangle")
    settle()
    menu = toolbar.tool_menu
    menu.pin_button.click()
    settle()
    tools.set_locked("rectangle", False)
    drag(view, "rectangle", [(100, 100), (200, 180)])
    settle()
    assert tools.tool == "select"  # it went back to Select after one use
    assert menu.isVisible() and menu.key == "rectangle"  # its pinned menu stayed, to style the next one
    assert "Select" in menu.lock_button.toolTip()
    tools.set_tool("ellipse")  # a tool the user turns on takes the pinned menu, as before
    settle(200)
    assert menu.isVisible() and menu.key == "ellipse"


def test_the_lock_tip_names_where_the_tool_goes(window, workspace):
    toolbar = workspace.toolbar
    workspace.tool_settings.set_locked("rectangle", False)
    toolbar.open_menu("rectangle")
    settle()
    menu = toolbar.tool_menu
    assert "Select" in menu.lock_button.toolTip()
    window.settings.set("after_drawing", "pan")
    try:
        assert "Pan" in menu.lock_button.toolTip()
        window.settings.set("select_new", True)
        assert "Select" in menu.lock_button.toolTip()  # what was made gets selected: Select it is
    finally:
        window.settings.set("select_new", False)
        window.settings.set("after_drawing", "select")
    menu.close_menu()


def test_the_default_toolbar_fits_a_1300_px_window(window, workspace):
    window.resize(1300, 900)
    settle(60)
    toolbar = workspace.toolbar
    assert [key for key in DEFAULT_TOOLBAR if key in toolbar.keys()] == list(DEFAULT_TOOLBAR)
    assert toolbar._hidden == []  # none in the overflow menu: the page counter sits below the toolbar


def test_the_toolbar_goes_to_any_side_and_can_be_fixed_to_it(window, workspace):
    window.resize(1300, 900)
    settle(60)
    canvas, toolbar, prefs = workspace.canvas, workspace.toolbar, window.settings
    try:
        prefs.set("toolbar_side", "left")
        settle(20)
        bar = toolbar.geometry()
        assert bar.height() > bar.width() and bar.left() < 30  # a column down the left
        assert canvas.bars["undo"].geometry().left() > bar.right()  # the other bars keep clear of it
        prefs.set("toolbar_side", "bottom")
        settle(20)
        bar = toolbar.geometry()
        assert bar.width() > bar.height() and bar.bottom() > canvas.height() - 30
        assert canvas.bars["zoom"].geometry().bottom() < bar.top()
        prefs.set("toolbar_docked", True)
        settle(20)
        bar = toolbar.geometry()
        assert (bar.width(), bar.bottom()) == (canvas.width(), canvas.height() - 1)  # the whole edge
        assert workspace.view.geometry().bottom() < bar.top()  # the pages beside it, not under it
        toolbar.drag_to(canvas.mapToGlobal(QPoint(canvas.width() - 5, canvas.height() // 2)))  # the handle
        settle(20)
        assert prefs.get("toolbar_side") == "right"
        bar = toolbar.geometry()
        assert (bar.right(), bar.height()) == (canvas.width() - 1, canvas.height())
        assert workspace.view.geometry().right() < bar.left()
        toolbar.open_menu("highlight")
        settle(20)
        panel, button = toolbar.tool_menu.panel, toolbar.anchor_for("highlight")
        # beside its button, on the pages' side
        assert panel.mapToGlobal(QPoint(panel.width(), 0)).x() <= button.mapToGlobal(QPoint(0, 0)).x()
    finally:
        if toolbar.tool_menu is not None:
            toolbar.tool_menu.close_menu()
        prefs.set("toolbar_side", "top")
        prefs.set("toolbar_docked", False)
    settle(20)
    assert toolbar.geometry().top() < 30 and workspace.view.geometry() == canvas.rect()


# Shared widgets and dialogs ----------------------------------------------------------------------


def test_neutral_tool_colours_never_look_disabled():
    """A black pen on a dark bar takes the text colour; a grey would look like a disabled button."""
    assert QColor(readable((0.0, 0.0, 0.0), DARK.raised, DARK.text)) == QColor(DARK.text)
    assert readable((0.0, 0.0, 0.0), LIGHT.raised, LIGHT.text) == "#000000"
    red = QColor(readable((0.9, 0.1, 0.1), DARK.raised, DARK.text))
    assert red.red() > 2 * red.green()  # colours keep their hue


def test_spin_boxes_start_their_text_where_line_edits_do(window):
    box = QSpinBox(window)
    box.ensurePolished()
    assert box.lineEdit().textMargins().left() == SPIN_INSET
    box.deleteLater()


def test_message_dialogs_are_freed_when_let_go(qt_app):
    dialog = messages.MessageDialog(None, "Title", "Text")
    gone = weakref.ref(dialog)
    del dialog
    gc.collect()
    assert gone() is None  # nothing connected to its buttons keeps it alive


def test_workspace_dialogs_are_deleted_once_read(workspace, monkeypatch):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 100, 80, 40))
    view.add_annotations([shape], "Rectangle")
    view.tool_settings.set_tool("select")
    view.select([shape])
    monkeypatch.setattr(dialogs.PropertiesDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(dialogs.ApplyToPagesDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    workspace.edit_properties()
    workspace.apply_to_pages()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert not workspace.findChildren(dialogs.PropertiesDialog)
    assert not workspace.findChildren(dialogs.ApplyToPagesDialog)


def test_context_menu_entries_explain_themselves(workspace):
    view = workspace.view
    note = Attachment(page=0, position=(300, 300), file_name="a.wav", data=b"RIFF", audio=True)
    view.add_annotations([note], "Audio note")
    menu = build_context_menu(view, 0, QPointF(310, 310), note)
    tips = {}

    def walk(m):
        for action in m.actions():
            if action.text():
                tips[action.text().split("\t")[0]] = action.toolTip()
            if action.menu() is not None:
                walk(action.menu())

    walk(menu)
    for entry in ("Play audio note", "Save attachment as…", "Page 1", "Audio note here…", "Bookmark page 1…"):
        assert tips.get(entry) and tips[entry] != entry.replace("&", ""), entry
    menu.deleteLater()


def test_file_info_threads_of_a_deleted_home_page_are_waited_for_at_exit(qt_app, sample_pdf, tmp_path):
    """PDFium must be idle when Python takes it down, even for a thread whose home page is gone."""
    from zenith_pdf.ui import home

    copies = []
    for number in range(6):
        copies.append(tmp_path / f"copy{number}.pdf")
        shutil.copy(sample_pdf, copies[-1])
    loader = home.InfoLoader()
    loader.request(copies)
    threads = list(loader._threads)
    loader.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del loader
    gc.collect()
    home._stop_loaders()
    assert threads and not any(thread.is_alive() for thread in threads)
