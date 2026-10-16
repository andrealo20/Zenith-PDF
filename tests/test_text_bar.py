"""The bar over selected text, and selecting a word with a double click."""

import shutil
import sys

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core import textclean
from zenith_pdf.core.annotations import Shape, TextMarkup
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import messages
from zenith_pdf.ui.text_bar import COLOUR_NAMES
from zenith_pdf.ui.tool_menu import HIGHLIGHTER
from zenith_pdf.ui.tools import DEFAULT_STYLES


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
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    workspace.side.hide_panel()
    workspace.tool_settings.set_tool("select")
    settle()
    yield workspace
    tools = workspace.tool_settings
    tools.set_tool("select")
    for key in DEFAULT_STYLES:
        tools.reset_style(key)
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


@pytest.fixture
def errors(monkeypatch):
    """Exceptions raised in Qt event handlers, which PySide reports instead of raising."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: seen.append(f"{kind.__name__}: {value}"))
    return seen


def needle(document, page=0):
    """The display rect of "needle" on `page`: "Page 1 needle" sits near display y 83 to 100 on the first."""
    return next(h for h in document.iter_search("needle", False, False) if h.page == page).rects[0]


def at(view, page, x, y) -> QPoint:
    return view.mapFromScene(view._pages[page].mapToScene(QPointF(x, y)))


def real_drag(view, start, end, steps=6):
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, steps + 1):
        QTest.mouseMove(viewport, start + (end - start) * step / steps)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


def drag_over_needle(workspace):
    view = workspace.view
    x, y, w, h = needle(workspace.document)
    real_drag(view, at(view, 0, x + 1, y + h / 2), at(view, 0, x + w - 1, y + h / 2))
    settle()


def double_click(view, pos: QPoint):
    """A press and release, then the second press as Windows sends it: a double-click event."""
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    settle()


def double_click_needle(workspace, word="needle"):
    view = workspace.view
    x, y, w, h = next(h for h in workspace.document.iter_search(word, False, False) if h.page == 0).rects[0]
    double_click(view, at(view, 0, x + w / 2, y + h / 2))


def selection_on_canvas(workspace) -> QRect:
    view = workspace.view
    area = view.text_selection_rect()
    return QRect(view.viewport().mapTo(workspace.canvas, area.topLeft()), area.size())


def markups(store):
    return [a for a in store.all() if isinstance(a, TextMarkup)]


def test_words_run_over_letters_digits_and_ligatures(sample_pdf):
    for char in "a7_-'’\x1cﬁ￾\x17":
        assert textclean.in_word(char), repr(char)
    for char in " ,.;:()\r\n\t\x15":
        assert not textclean.in_word(char), repr(char)
    document = Document(sample_pdf)
    try:
        x, y, w, h = needle(document)
        first, last = document.word_at(0, (x + w / 2, y + h / 2))
        assert document.text_span(0, first, last)[1] == "needle"
        first, last = document.word_at(0, (x + 2, y + h / 2))  # its first letter gives the whole word too
        assert document.text_span(0, first, last)[1] == "needle"
        assert document.word_at(0, (300, 600)) is None  # nothing there
    finally:
        document.close()


def test_one_click_on_text_selects_nothing_and_shows_no_bar(workspace):
    view = workspace.view
    x, y, w, h = needle(workspace.document)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     at(view, 0, x + w / 2, y + h / 2))
    settle()
    assert not view.has_text_selection() and not workspace.text_bar.isVisible()


def test_the_bar_sits_above_the_text_and_highlights_it(workspace, errors):
    view, bar, store = workspace.view, workspace.text_bar, workspace.store
    drag_over_needle(workspace)
    assert view.selected_text() == "needle"
    assert bar.isVisible()
    text = selection_on_canvas(workspace)
    assert bar.geometry().bottom() < text.top() and not bar.geometry().intersects(text)  # above, never on it
    assert workspace.canvas.rect().contains(bar.geometry())
    assert bar.height() <= 48 and bar.width() < 400  # compact: icons, not labels
    rects = view._text_selection[1]
    first = themes.current.name
    for theme in ("light", "dark", first):  # a theme change while it is up
        workspace.window().set_theme(theme)
        settle()
    assert bar.isVisible() and not errors

    QTest.mouseClick(bar.markups["highlight"], Qt.MouseButton.LeftButton)
    settle()
    [mark] = markups(store)
    assert mark.style == "Highlight" and mark.quads == rects
    assert mark.color == DEFAULT_STYLES["highlight"].color
    assert not view.has_text_selection() and not bar.isVisible()
    workspace.undo_stack.undo()
    assert markups(store) == []


@pytest.mark.parametrize("tool, style", [("underline", "Underline"), ("strikeout", "StrikeOut"),
                                         ("squiggly", "Squiggly")])
def test_the_other_markups(workspace, tool, style):
    drag_over_needle(workspace)
    QTest.mouseClick(workspace.text_bar.markups[tool], Qt.MouseButton.LeftButton)
    settle()
    [mark] = markups(workspace.store)
    assert mark.style == style and mark.color == workspace.tool_settings.style(tool).color


def test_a_colour_from_the_menu_marks_the_text_and_stays(workspace):
    view, bar, settings = workspace.view, workspace.text_bar, workspace.tool_settings
    drag_over_needle(workspace)
    assert bar.chosen_colour() == 0  # yellow, the highlight tool's own
    green = next(a for a in bar.colour_menu.actions() if a.text() == "Green")
    green.trigger()
    settle()
    [mark] = markups(workspace.store)
    assert mark.style == "Highlight" and mark.color == HIGHLIGHTER[COLOUR_NAMES.index("Green")]
    assert settings.style("highlight").color == mark.color  # the tool draws in it from now on
    assert bar.chosen_colour() == 1 and "green" in bar.markups["highlight"].toolTip()
    assert not view.has_text_selection()
    double_click_needle(workspace, "Page")  # needle is marked now: a double click there selects the mark
    assert view.selected_text() == "Page"
    QTest.mouseClick(bar.markups["highlight"], Qt.MouseButton.LeftButton)
    settle()
    assert [m.color for m in markups(workspace.store)] == [mark.color, mark.color]


def test_a_note_on_the_text(workspace, monkeypatch):
    asked = []
    monkeypatch.setattr(messages, "get_multiline_text", lambda *args, **kwargs: asked.append(args) or "Check this")
    drag_over_needle(workspace)
    QTest.mouseClick(workspace.text_bar.note, Qt.MouseButton.LeftButton)
    settle()
    [mark] = markups(workspace.store)
    assert asked and mark.style == "Highlight" and mark.contents == "Check this"
    assert workspace.view._items[mark.id].toolTip() == "Check this"  # pointing at it shows the note


def test_a_double_click_selects_a_word(workspace):
    view, bar = workspace.view, workspace.text_bar
    double_click_needle(workspace)
    assert view.selected_text() == "needle" and bar.isVisible()
    assert not bar.geometry().intersects(selection_on_canvas(workspace))
    workspace.tool_settings.set_tool("hand")  # Pan: a double click pans, it selects nothing
    double_click_needle(workspace)
    assert not view.has_text_selection()


def test_search_finds_every_place(workspace):
    view, search = workspace.view, workspace.search
    double_click_needle(workspace)
    QTest.mouseClick(workspace.text_bar.search, Qt.MouseButton.LeftButton)
    for _ in range(200):
        settle(10)
        if search.status.text() == "4 results":
            break
    assert workspace.side.current == "search" and search.field.text() == "needle"
    assert search.results.count() == 4  # one on each page
    assert not view.has_text_selection() and not workspace.text_bar.isVisible()


def test_view_only_documents_copy_but_do_not_mark(workspace, monkeypatch):
    view, bar = workspace.view, workspace.text_bar
    monkeypatch.setattr(Document, "read_only", property(lambda self: True))
    copied = []
    monkeypatch.setattr(view, "copy_selection", lambda: copied.append(view.selected_text()))  # no real clipboard
    double_click_needle(workspace)
    assert bar.isVisible()
    for button in (*bar.markups.values(), bar.colour, bar.note):
        assert not button.isEnabled()
    assert bar.copy.isEnabled() and bar.search.isEnabled()
    QTest.mouseClick(bar.copy, Qt.MouseButton.LeftButton)
    assert copied == ["needle"] and workspace.toast.text() == "Copied."
    assert view.mark_text_selection("highlight") is None and markups(workspace.store) == []


def test_another_tool_lets_the_text_go(workspace):
    view, bar = workspace.view, workspace.text_bar
    drag_over_needle(workspace)
    assert bar.isVisible()
    workspace.tool_settings.set_tool("rectangle")
    assert not bar.isVisible() and not view.has_text_selection()


def test_selected_annotations_and_text_take_turns(workspace):
    view, bar = workspace.view, workspace.text_bar
    box = Shape(page=0, rect=(300, 300, 80, 60))
    view.add_annotations([box], "Rectangle")
    drag_over_needle(workspace)
    assert bar.isVisible()
    view.select([box])
    settle()
    assert not view.has_text_selection() and not bar.isVisible() and workspace.selection_bar.isVisible()


def test_the_bar_follows_the_text_and_waits_while_it_is_away(workspace):
    view, bar = workspace.view, workspace.text_bar
    drag_over_needle(workspace)
    gap = selection_on_canvas(workspace).top() - bar.geometry().bottom()
    scrollbar = view.verticalScrollBar()
    scrollbar.setValue(scrollbar.value() + 30)
    settle()
    assert bar.isVisible() and selection_on_canvas(workspace).top() - bar.geometry().bottom() == gap
    scrollbar.setValue(scrollbar.value() + 2000)  # the text is off screen
    settle()
    assert view.has_text_selection() and not bar.isVisible()
    view.goto_page(0)
    settle()
    assert bar.isVisible()
    workspace.toggle_page_manager()  # other pages: the selection goes, and no bar comes back with it
    settle()
    assert not view.has_text_selection() and not bar.isVisible()
    workspace.toggle_page_manager()
    settle()
    assert not bar.isVisible()


def test_the_bar_moves_with_the_pages_when_the_canvas_changes_size(workspace):
    view, bar = workspace.view, workspace.text_bar
    view.set_zoom(0.5)  # narrower than the view: the pages sit in the middle, and move when it changes width
    settle()
    double_click_needle(workspace)
    offset = bar.geometry().center().x() - selection_on_canvas(workspace).center().x()
    canvas = workspace.canvas
    canvas.resize(canvas.width() - 200, canvas.height())  # as the side panel opening does; the pages move
    assert bar.isVisible() and bar.geometry().center().x() - selection_on_canvas(workspace).center().x() == offset
    assert not bar.geometry().intersects(selection_on_canvas(workspace))
    workspace.side.show_panel("pages")
    settle()
    assert bar.geometry().center().x() - selection_on_canvas(workspace).center().x() == offset
    workspace.side.hide_panel()


def test_the_bar_keeps_clear_of_the_page_counter(workspace):
    canvas, bar = workspace.canvas, workspace.text_bar
    counter = canvas.bars["counter"].geometry()
    drag_over_needle(workspace)  # a selection to show the bar for
    viewport = workspace.view.viewport()
    # Text at the right, just under the toolbar: no room above it, and below it the counter is in the way.
    on_canvas = QRect(counter.center().x() - 40, counter.top() + 6, 80, 16)
    canvas.place_over_text(bar, QRect(viewport.mapFrom(canvas, on_canvas.topLeft()), on_canvas.size()))
    assert bar.isVisible() and not bar.geometry().intersects(counter)
    assert not bar.geometry().intersects(on_canvas) and bar.geometry().top() > on_canvas.bottom()


def test_the_second_click_after_a_link_selects_nothing(window, links_pdf, tmp_path):
    path = tmp_path / "links.pdf"
    shutil.copy(links_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    try:
        settle()
        view = workspace.view
        pos = at(view, 0, 90, 132)  # on the link to the middle of page 2
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        assert view.current_page == 1 and view.history.peek_back() is not None
        words = at(view, 1, 90, 792 - 600 - 4)  # "Second page", whose baseline is at PDF y 600
        scrollbar = view.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + words.y() - pos.y())  # as if the jump had put them under the pointer
        page, point = view._page_at(QPointF(pos))
        assert page.index == 1 and workspace.document.word_at(1, (point.x(), point.y())) is not None
        QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        settle()
        assert not view.has_text_selection()  # the second click of the pair did nothing there
    finally:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_the_bar_colours_follow_the_theme_and_the_tools(workspace):
    bar, settings = workspace.text_bar, workspace.tool_settings
    before = bar.colour.icon().pixmap(bar.colour.iconSize()).toImage()
    settings.update_style("highlight", color=HIGHLIGHTER[3])
    assert bar.colour.icon().pixmap(bar.colour.iconSize()).toImage() != before  # the dot shows the new colour
    assert "pink" in bar.markups["highlight"].property("help_detail")
    pink = bar.markups["highlight"]._color
    first = themes.current.name
    workspace.window().set_theme("light" if first == "dark" else "dark")
    settle()
    try:
        assert bar.markups["highlight"]._color != pink  # made readable on the other theme
    finally:
        workspace.window().set_theme(first)
