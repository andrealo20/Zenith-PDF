"""Edge cases of the page canvas: resizing in fit width, spreads, zoom glides, read only and text box editing."""

import shutil

import pikepdf
import pytest
from pikepdf import Dictionary, Name
from PySide6.QtCore import QPointF, QRect, QSettings, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import FreeText, Ink, Note, Shape
from zenith_pdf.core.document import SearchHit
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import clipboard, messages
from zenith_pdf.ui.document_view import ZOOM_GLIDE_MS


def settle(ms=60):
    QTest.qWait(ms)
    QApplication.processEvents()


def on_viewport(view, page, x, y):
    """Where a display-space point of `page` is in viewport pixels."""
    return view.mapFromScene(view._pages[page].mapToScene(QPointF(x, y)))


@pytest.fixture(scope="module")
def eight_pages(tmp_path_factory):
    """Eight Letter pages, each with lines of text reading "Page N needle line K"."""
    path = tmp_path_factory.mktemp("eight") / "eight.pdf"
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    for number in range(1, 9):
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj.Resources = Dictionary(Font=Dictionary(F1=font))
        lines = b"".join(f"BT /F1 14 Tf 72 {692 - 40 * k} Td (Page {number} needle line {k}) Tj ET\n".encode()
                         for k in range(16))
        page.obj.Contents = pdf.make_stream(lines)
    pdf.save(path)
    return path


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
def workspace(window, eight_pages, tmp_path):
    path = tmp_path / "eight.pdf"
    shutil.copy(eight_pages, path)
    window.resize(1300, 900)
    window.open_file(path)
    workspace = window.current_workspace()
    settle()
    yield workspace
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_a_resize_in_fit_width_keeps_the_line_being_read(window, workspace):
    view = workspace.view
    assert view._fit == "width"
    view.go_to(4, 474)
    settle()
    before = view.spot()
    window.resize(1100, 900)
    settle(150)
    after = view.spot()
    assert view._fit == "width" and after.page == before.page == 4
    assert abs(after.y - before.y) < 3


def test_the_left_page_of_a_spread_at_the_top_of_the_window_is_rendered(workspace):
    view = workspace.view
    view.set_layout("double")
    view.set_zoom(3.0)
    view.jump(4, 400)  # into the middle of the spread 5-6: the top of the window is inside the row
    settle()
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    on_screen = [page.index for page in view._pages if page.sceneBoundingRect().intersects(visible)]
    assert 4 in on_screen
    assert [page.index for page in view._pages_in(visible)] == on_screen


@pytest.mark.parametrize("layout", ["double", "cover"])
def test_next_and_previous_page_move_a_whole_spread(workspace, layout):
    view = workspace.view
    view.set_layout(layout)
    bar = view.verticalScrollBar()
    view.goto_page(0)
    top = bar.value()
    view.goto_page(1)
    if layout == "double":
        assert bar.value() == top  # pages 1 and 2 share the first row
    view.goto_page(0)
    seen = [bar.value()]
    for _ in range(3):
        view.next_page()
        seen.append(bar.value())
    assert seen == sorted(set(seen))  # every press moved on
    for _ in range(3):
        view.previous_page()
        seen.append(bar.value())
    assert seen[-1] == top and len(set(seen[3:])) == 4


def test_search_hits_survive_a_layout_change(workspace):
    view = workspace.view
    hits = [SearchHit(page, ((72.0, 300.0, 120.0, 14.0),), "needle") for page in (1, 6)]
    for hit in hits:
        view.add_search_hit(hit)
    view.focus_search_hit(hits[1])
    view.set_layout("double")
    assert set(view._hit_items) == set(hits)
    assert view._hit_items[hits[1]][0].brush().color() == themes.current.search_current
    view.focus_search_hit(hits[0])  # picking another result in the list still goes there
    visible = view.mapToScene(view.viewport().rect()).boundingRect()
    assert view.current_page == 1 and visible.intersects(view._hit_items[hits[0]][0].sceneBoundingRect())


def test_a_zoom_glide_renders_only_where_it_lands(workspace, monkeypatch):
    view = workspace.view
    view.set_zoom(1.0)
    settle()
    renderer = view._renderer
    asked_mid_glide = []
    request_many = renderer.request_many

    def spy(jobs):
        jobs = list(jobs)
        if view.glide.state() == QVariantAnimation.State.Running:
            asked_mid_glide.extend(jobs)
        return request_many(jobs)

    monkeypatch.setattr(renderer, "request_many", spy)
    view.zoom_in()
    QTest.qWait(ZOOM_GLIDE_MS)
    for _ in range(100):  # a slow machine's frames run late
        if view.glide.state() != QVariantAnimation.State.Running:
            break
        settle(20)
    assert view.glide.state() != QVariantAnimation.State.Running and not asked_mid_glide
    for _ in range(100):  # what it landed on still gets sharp
        if view.missing_tiles() == 0:
            break
        settle(30)
    assert view.missing_tiles() == 0


def test_closing_a_tab_mid_glide_leaves_the_closed_document_alone(window, workspace):
    view, document = workspace.view, workspace.document
    view.zoom_in()
    QTest.qWait(20)
    assert view.glide.state() == QVariantAnimation.State.Running
    window.close_tab(window.workspaces.index(workspace), confirm=False)
    assert view.glide.state() != QVariantAnimation.State.Running  # checked before the view is deleted
    QTest.qWait(ZOOM_GLIDE_MS + 100)
    assert document.key not in window.renderer._documents


def test_delete_and_cut_change_nothing_when_only_reading(workspace, monkeypatch):
    copied = []
    monkeypatch.setattr(clipboard, "copy_annotations", lambda annotations: copied.append(list(annotations)))
    view = workspace.view
    shape = Shape(page=0, rect=(100, 100, 50, 50))
    view.add_annotations([shape], "Rectangle")
    workspace.undo_stack.setClean()
    workspace.set_view_only(True)
    view.select([shape])  # Select works when only reading
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    view.cut_annotations()
    assert shape.id in workspace.store and workspace.undo_stack.isClean()
    assert copied  # Cut still copies, as Ctrl+C does
    workspace.set_view_only(False)


def colours_in(view, page, x, y, w, h):
    """The colours shown over the display-space area (x, y, w, h) of `page`, as (red, green, blue) 0 to 255."""
    image = view.viewport().grab(QRect(on_viewport(view, page, x, y), on_viewport(view, page, x + w, y + h))).toImage()
    return [image.pixelColor(i, j).getRgb()[:3] for i in range(image.width()) for j in range(image.height())]


def test_a_callout_keeps_its_fill_and_line_while_typed_in(workspace):
    view = workspace.view
    view.set_zoom(1.0)
    view.goto_page(0)
    box = FreeText(page=0, rect=(200, 150, 200, 40), contents="Dark on yellow", color=(0.8, 0, 0), fill=(1, 1, 0),
                   font_size=14, border_width=1.0, border_color=(0.8, 0, 0), callout=((100, 300),))
    view.add_annotations([box], "Callout")
    view.edit_text_box(workspace.store.get(box.id), is_new=False)
    settle()
    assert view.is_editing_text()
    fill = colours_in(view, 0, 300, 175, 90, 12)  # below the text, inside the box
    assert sum(1 for r, g, b in fill if r > 200 and g > 200 and b < 80) > 0.8 * len(fill)
    tip, bend = box.callout_line()[:2]
    middle = ((tip[0] + bend[0]) / 2, (tip[1] + bend[1]) / 2)
    line = colours_in(view, 0, middle[0] - 3, middle[1] - 3, 6, 6)
    assert any(r > 150 and g < 100 and b < 100 for r, g, b in line)
    view.finish_text_edit()


def test_switching_tools_or_reading_puts_the_format_painter_down(workspace):
    view, tools = workspace.view, workspace.tool_settings
    view.set_zoom(1.0)
    view.goto_page(0)
    red = Shape(page=0, rect=(100, 100, 50, 50), color=(1, 0, 0), width=5)
    view.add_annotations([red], "Rectangle")
    tools.set_tool("select")
    view.select([red])
    view.start_format_painter()
    assert view.painting
    tools.set_tool("pen")
    assert not view.painting
    viewport = view.viewport()
    points = [on_viewport(view, 0, x, y) for x, y in ((100, 400), (150, 420), (200, 440))]
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, points[0])
    for point in points[1:]:
        QTest.mouseMove(viewport, point)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, points[-1])
    assert any(isinstance(a, Ink) for a in workspace.store.all())  # the first stroke is drawn
    tools.set_tool("select")
    view.select([red])
    view.start_format_painter()
    view.set_reading(True)
    assert not view.painting
    view.set_reading(False)


def test_right_clicking_the_text_being_typed_keeps_typing(workspace):
    view = workspace.view
    view.set_zoom(1.0)
    view.goto_page(0)
    workspace.tool_settings.set_tool("text")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     on_viewport(view, 0, 100, 200))
    QTest.keyClicks(view, "Hello wor")
    assert view.is_editing_text()
    seen = {}

    def close_menu():
        menu = QApplication.activePopupWidget()
        seen["menu"] = [action.text() for action in menu.actions()] if menu is not None else []
        if menu is not None:
            menu.close()

    QTimer.singleShot(100, close_menu)
    pos = on_viewport(view, 0, 110, 205)
    QApplication.sendEvent(view.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                                             view.viewport().mapToGlobal(pos)))
    settle(300)
    assert "&Copy" in seen["menu"] and "Page 1" not in seen["menu"]  # the editor's own menu, not the page's
    assert view.is_editing_text()
    QTest.keyClicks(view, "ld")
    view.finish_text_edit()
    assert [a.contents for a in workspace.store.all() if isinstance(a, FreeText)] == ["Hello world"]


def test_text_boxes_and_notes_made_at_the_edge_stay_on_the_page(workspace, monkeypatch):
    view = workspace.view
    width, height = workspace.document.page_size(0)
    monkeypatch.setattr(messages, "get_multiline_text", lambda *args, **kwargs: "A note")
    view.new_note(0, (2.0, 2.0))  # Note here, from the context menu, at the top left corner
    (note,) = [a for a in workspace.store.all() if isinstance(a, Note)]
    assert note.position == (0.0, 0.0)
    view.new_text_box(0, (width - 10, height - 5))  # Text box here, at the bottom right corner
    QTest.keyClicks(view, "a few words that wrap onto several lines")
    view.finish_text_edit()
    (box,) = [a for a in workspace.store.all() if isinstance(a, FreeText)]
    x, y, w, h = box.rect
    assert x >= 0 and y >= 0 and x + w <= width + 1e-6 and y + h <= height + 1e-6
    assert h > 40  # it grew while typed in, and came back onto the page


def test_recolouring_a_callout_recolours_the_frame_drawn_in_its_colour(workspace):
    view, store = workspace.view, workspace.store
    red = (0.89, 0.18, 0.18)
    callout = FreeText(page=0, rect=(100, 300, 120, 30), contents="Words", color=(0.0, 0.0, 0.0),
                       callout=((60.0, 260.0),), border_width=1.0, border_color=(0.0, 0.0, 0.0))
    framed = FreeText(page=0, rect=(300, 300, 120, 30), contents="Blue frame", color=(0.0, 0.0, 0.0),
                      border_width=1.0, border_color=(0.0, 0.0, 1.0))
    view.add_annotations([callout, framed], "Text boxes")
    view.select([callout, framed])
    view.apply_style(color=red)
    assert store.get(callout.id).border_color == red
    assert store.get(framed.id).border_color == (0.0, 0.0, 1.0)  # a frame of its own colour stays
