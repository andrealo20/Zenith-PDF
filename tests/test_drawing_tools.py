"""The tools of the tool chooser, driven with synthetic mouse input, and the customisable toolbar.

Also what goes wrong when a page edit, a lost mouse release or a drag across pages
meets a tool, and the window's own questions and menus.
"""

import shutil
import sys
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsView, QMenu, QWidgetAction

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import (
    Attachment,
    FreeText,
    ImageStamp,
    Ink,
    Link,
    Measurement,
    Poly,
    Shape,
    Tape,
    TextMarkup,
)
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui import context_menu, insert_dialogs, messages
from zenith_pdf.ui.context_menu import build_context_menu
from zenith_pdf.ui.tool_catalog import CATALOG, DEFAULT_TOOLBAR
from zenith_pdf.ui.tools import DEFAULT_STYLES


class Mouse:
    """Just enough of a mouse event for the tools."""

    def __init__(self, modifiers=Qt.KeyboardModifier.NoModifier):
        self._modifiers = modifiers

    def modifiers(self):
        return self._modifiers

    def globalPosition(self):
        return QPointF(400, 300)


def drag(view, tool_name, points, page=0, modifiers=Qt.KeyboardModifier.NoModifier):
    view.tool_settings.set_tool(tool_name)
    tool = view._tools[tool_name]
    item = view._pages[page]
    event = Mouse(modifiers)
    tool.press(item, QPointF(*points[0]), event)
    for point in points[1:]:
        tool.move(item, QPointF(*point), event)
    tool.release(item, QPointF(*points[-1]), event)
    return tool


def clicks(view, tool_name, points, page=0):
    view.tool_settings.set_tool(tool_name)
    tool = view._tools[tool_name]
    item = view._pages[page]
    for point in points:
        tool.press(item, QPointF(*point), Mouse())
        tool.release(item, QPointF(*point), Mouse())
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
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_pen_marker_and_eraser(workspace):
    view, store = workspace.view, workspace.store
    drag(view, "pen", [(100, 100), (120, 110), (140, 100), (160, 115)])
    drag(view, "marker", [(100, 200), (300, 200)], modifiers=Qt.KeyboardModifier.ShiftModifier)
    pen, marker = of(store, Ink)
    assert not pen.highlighter and len(pen.strokes[0]) >= 3
    assert marker.highlighter and marker.strokes[0] == ((100, 200), (300, 200))  # Shift: straight
    drag(view, "eraser", [(130, 90), (130, 125)])
    assert of(store, Ink) == [marker]
    workspace.undo_stack.undo()
    assert len(of(store, Ink)) == 2


def test_tape_peels_back_and_sticks_again(workspace):
    view, store = workspace.view, workspace.store
    drag(view, "tape", [(80, 300), (260, 300)])
    [tape] = of(store, Tape)
    assert not tape.revealed
    clicks(view, "tape", [(170, 300)])
    assert of(store, Tape)[0].revealed
    clicks(view, "tape", [(170, 300)])
    assert not of(store, Tape)[0].revealed


def test_point_by_point_shapes_and_measurements(workspace):
    view, store = workspace.view, workspace.store
    tool = clicks(view, "polyline", [(50, 400), (100, 380), (150, 420)])
    assert tool.busy
    assert tool.double_click(view._pages[0], QPointF(150, 420))
    [line] = of(store, Poly)
    assert line.points == ((50, 400), (100, 380), (150, 420)) and not line.closed
    tool = clicks(view, "area", [(200, 400), (272, 400), (272, 472), (200, 472)])
    tool.finish()
    [area] = of(store, Measurement)
    assert area.mode == "area" and area.text() == "6.45 cm²"  # 1 inch square
    view.tool_settings.set_measure(1, "cm", 1, "m")
    drag(view, "distance", [(300, 600), (300 + 72 / 2.54 * 3, 600)])
    distance = [m for m in of(store, Measurement) if m.mode == "distance"][0]
    assert distance.text() == "3.00 m"
    view.tool_settings.set_measure(1, "cm", 1, "cm")
    clicks(view, "polygon", [(10, 10), (40, 10)]).finish()  # two points can't make a polygon
    assert len(of(store, Poly)) == 1


def test_cloud_squiggle_and_cleaner(workspace):
    view, store = workspace.view, workspace.store
    drag(view, "cloud", [(300, 300), (420, 360)])
    [cloud] = of(store, Shape)
    assert cloud.cloudy and cloud.label == "Cloud"
    hit = next(workspace.document.iter_search("needle", False, False))
    x, y, w, h = hit.rects[0]
    drag(view, "squiggly", [(x + 1, y + h / 2), (x + w - 1, y + h / 2)], page=hit.page)
    drag(view, "highlight", [(x + 1, y + h / 2), (x + w - 1, y + h / 2)], page=hit.page)
    assert {m.style for m in of(store, TextMarkup)} == {"Squiggly", "Highlight"}
    drag(view, "cleaner", [(x + 1, y + h / 2), (x + w - 1, y + h / 2)], page=hit.page)
    assert of(store, TextMarkup) == []


def test_callout_points_at_what_it_explains(workspace):
    view, store = workspace.view, workspace.store
    drag(view, "callout", [(100, 500), (200, 450)])
    assert view.is_editing_text()
    view._editor[0].setPlainText("Look")
    view.finish_text_edit()
    [callout] = of(store, FreeText)
    assert callout.callout == ((100, 500),) and callout.contents == "Look"
    assert callout.label == "Callout"


def test_boxes_notes_and_pastes_by_the_edge_stay_on_the_page(workspace, monkeypatch):
    """Started at the right or bottom edge of page 1 (612 x 792), each lands on the page whole."""
    from zenith_pdf.ui import clipboard

    view, store = workspace.view, workspace.store

    def on_page(annotation):
        x, y, w, h = annotation.bounds()
        return x >= -0.01 and y >= -0.01 and x + w <= 612.01 and y + h <= 792.01

    for tool, points in (("text", [(609, 300)]), ("callout", [(500, 700), (611, 791)])):
        drag(view, tool, points)
        view._editor[0].setPlainText("Edge")
        view.finish_text_edit()
    boxes = of(store, FreeText)
    assert len(boxes) == 2 and all(on_page(box) for box in boxes)
    assert boxes[0].rect[2] >= 80  # moved left, not squeezed to nothing
    assert boxes[1].callout == ((500, 700),)  # the arrow still points where it was pressed
    monkeypatch.setattr(messages, "get_multiline_text", lambda *a, **k: "A note")
    clicks(view, "note", [(611, 791)])
    shape = Shape(page=0, rect=(100, 100, 100, 60))
    clipboard.copy_annotations([shape])
    view.paste_annotations(0, (592.0, 772.0))
    assert all(on_page(a) for a in store.all()) and len(store.all()) == 4


def test_links_are_followed_and_edited(workspace, monkeypatch):
    view, store = workspace.view, workspace.store

    class Answer(insert_dialogs.LinkDialog):
        def exec(self):
            self.page.setChecked(True)
            self.number.setValue(3)
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(insert_dialogs, "LinkDialog", Answer)
    drag(view, "hyperlink", [(100, 100), (220, 130)])
    [link] = of(store, Link)
    assert link.target == 2 and link.uri == ""
    view.tool_settings.set_tool("select")
    target = view._link_target(QPointF(view.mapFromScene(view._pages[0].mapToScene(QPointF(150, 115)))))
    assert target == ("page", 2, None)
    view.follow_link(*target)
    assert view.current_page == 2


def test_snapshot_and_lasso(workspace):
    view = workspace.view
    QApplication.clipboard().clear()
    drag(view, "snapshot", [(50, 50), (250, 150)])
    image = QApplication.clipboard().image()
    assert not image.isNull() and image.width() == 600  # 200 points at 3x
    shapes = [Shape(page=0, rect=(100 + 60 * i, 300, 40, 40)) for i in range(3)]
    view.add_annotations(shapes, "Shapes")
    drag(view, "lasso", [(80, 280), (220, 280), (220, 360), (80, 360)])
    assert view.tool_settings.tool == "select"
    assert {a.id for a in view.selected_annotations()} == {shapes[0].id, shapes[1].id}


def test_equation_is_placed_and_saved(workspace, monkeypatch):
    view, store = workspace.view, workspace.store

    class Answer(insert_dialogs.EquationDialog):
        def exec(self):
            self.source.setPlainText(r"\frac{a}{b} + \sqrt{x}")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(insert_dialogs, "EquationDialog", Answer)
    clicks(view, "equation", [(200, 200)])
    [equation] = of(store, ImageStamp)
    assert equation.equation == r"\frac{a}{b} + \sqrt{x}" and equation.label == "Equation"
    workspace.save()
    reopened = Document(workspace.document.path)
    assert [a.equation for a in reopened.annotations if isinstance(a, ImageStamp)] == [equation.equation]
    reopened.close()


def test_toolbar_can_be_customised(window, workspace):
    toolbar = workspace.toolbar
    assert toolbar.keys() == DEFAULT_TOOLBAR
    toolbar.add_tool("pen", 2)
    assert toolbar.keys()[2] == "pen" and "pen" in toolbar._buttons
    toolbar.move_tool("pen", 0)
    assert toolbar.keys()[0] == "pen"
    toolbar.remove_tool("pen")
    toolbar.remove_tool("select")  # Select always stays
    assert "pen" not in toolbar.keys() and "select" in toolbar.keys()
    toolbar.fit(300)
    assert toolbar._hidden and toolbar.overflow.isVisible()
    toolbar.fit(None)
    assert not toolbar._hidden

    workspace.show_tool_chooser()
    chooser = workspace._chooser
    QTest.qWait(30)
    shown = [t for t in chooser._tiles if t.isVisibleTo(chooser)]
    assert chooser.isVisible() and len(shown) == sum(1 for s in CATALOG if s.group == "Free-form")
    chooser._toggle("laser")
    assert "laser" in toolbar.keys()
    chooser.search.setText("measure")
    assert {t.spec.key for t in chooser._tiles if t.isVisibleTo(chooser)} >= {"distance", "area"}
    chooser._use("distance")
    assert workspace.tool_settings.tool == "distance" and not chooser.isVisible()
    toolbar._store(list(DEFAULT_TOOLBAR))


def test_the_tool_chooser_stays_over_a_narrow_window():
    from PySide6.QtCore import QRect

    from zenith_pdf.ui.tool_chooser import centred_x

    screen = QRect(0, 0, 2560, 1400)
    assert centred_x(636, 820, QRect(0, 0, 960, 800), screen) == 140  # not past the window's right edge at 1046
    assert centred_x(400, 820, QRect(100, 0, 1300, 800), screen) == 100
    assert centred_x(350, 820, QRect(0, 0, 720, 800), screen) == 0  # narrower than the chooser: the screen holds it
    assert centred_x(2500, 820, QRect(1800, 0, 1300, 800), screen) == 2560 - 820


def test_every_catalogued_tool_has_an_implementation(workspace):
    view = workspace.view
    for spec in CATALOG:
        assert spec.action or spec.key in view._tools or spec.key == "hand", spec.key


# When things go wrong: page edits, lost releases, drags across pages ------------------------------


def at(view, page, x, y) -> QPoint:
    """The viewport pixel of a point on a page."""
    return view.mapFromScene(view._pages[page].mapToScene(QPointF(x, y)))


def real_drag(view, start, end, steps=6):
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, steps + 1):
        QTest.mouseMove(viewport, start + (end - start) * step / steps)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


@pytest.fixture
def errors(monkeypatch):
    """Exceptions raised in Qt event handlers, which PySide reports instead of raising."""
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: seen.append(f"{kind.__name__}: {value}"))
    return seen


def test_tools_let_go_of_their_items_when_the_pages_change(workspace, errors):
    view, viewport, tools = workspace.view, workspace.view.viewport(), workspace.tool_settings
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    tools.set_tool("eraser")
    QTest.mouseMove(viewport, at(view, 0, 200, 200))
    workspace.actions.rotate(0, 90)
    QTest.mouseMove(viewport, at(view, 0, 210, 210))
    QTest.mouseMove(viewport, at(view, 0, 220, 220))
    tools.set_tool("select")
    assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor

    tools.set_tool("polyline")
    for x in (100, 150, 200):
        QTest.mouseClick(viewport, left, none, at(view, 0, x, 300))
    QTest.mouseMove(viewport, at(view, 0, 230, 320))
    workspace.undo_stack.undo()  # the rotation, while the polyline is half drawn
    QTest.mouseMove(viewport, at(view, 0, 240, 330))
    QTest.mouseClick(viewport, left, none, at(view, 0, 250, 330))
    assert view._tools["polyline"].points == [pytest.approx((250, 330), abs=1)]

    tools.update_style("laser", laser_fade=0.3)
    tools.set_tool("laser")
    start = at(view, 0, 100, 500)
    QTest.mousePress(viewport, left, none, start)
    QTest.mouseMove(viewport, start + QPoint(40, 10))
    QTest.mouseRelease(viewport, left, none, start + QPoint(40, 10))
    view.set_layout("double")  # while the trail fades
    QTest.qWait(450)
    view.set_layout("single")
    assert errors == []


def test_typing_survives_an_undone_page_edit(workspace, errors):
    view = workspace.view
    workspace.actions.rotate(0, 90)
    view.new_text_box(0, (100.0, 100.0))
    view._editor[0].setPlainText("Kept")
    workspace.undo_stack.undo()  # as the undo button does: it takes no focus from the editor
    QTest.qWait(20)
    assert not view.is_editing_text() and errors == []
    assert [a.contents for a in workspace.store.all() if isinstance(a, FreeText)] == ["Kept"]


def test_clicking_a_link_with_the_hyperlink_tool_edits_it_once(workspace, monkeypatch, errors):
    view, store, tools = workspace.view, workspace.store, workspace.tool_settings
    opened = []

    class Answer(insert_dialogs.LinkDialog):
        def exec(self):
            opened.append(self.address.text())
            self.web.setChecked(True)
            self.address.setText("example.org/" + str(len(opened)))
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(insert_dialogs, "LinkDialog", Answer)
    view.add_annotations([Link(page=0, rect=(100, 100, 120, 30), uri="https://example.org")], "Link")
    tools.set_tool("hyperlink")
    point = at(view, 0, 150, 115)
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseMove(viewport, point + QPoint(3, 2))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point + QPoint(3, 2))
    [link] = of(store, Link)
    assert opened == ["https://example.org"] and link.uri == "https://example.org/1" and errors == []
    assert tools.tool == "hyperlink"  # changing a link is not a use of the tool
    real_drag(view, at(view, 0, 300, 400), at(view, 0, 420, 430))
    assert len(of(store, Link)) == 2 and opened[-1] == "" and tools.tool == "hand"


def test_an_erase_whose_release_was_lost_stays_its_own_undo_step(workspace, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent

    view, stack, viewport = workspace.view, workspace.undo_stack, workspace.view.viewport()
    strokes = [Ink(page=0, strokes=(((100, 100 + 40 * i), (300, 100 + 40 * i)),), width=3) for i in range(4)]
    view.add_annotations(strokes, "Strokes")

    class Dismissed(QMenu):
        def exec(self, *args):
            return None

    monkeypatch.setattr(context_menu, "QMenu", Dismissed)
    workspace.tool_settings.set_tool("eraser")
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mousePress(viewport, left, none, at(view, 0, 200, 90))
    QTest.mouseMove(viewport, at(view, 0, 200, 110))  # rubs out the first stroke
    where = at(view, 0, 200, 110)
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, where, viewport.mapToGlobal(where)))
    QTest.mousePress(viewport, left, none, at(view, 0, 200, 130))
    QTest.mouseMove(viewport, at(view, 0, 200, 150))
    QTest.mouseRelease(viewport, left, none, at(view, 0, 200, 150))
    workspace.tool_settings.set_tool("select")
    view.add_annotations([Shape(page=0, rect=(400, 400, 50, 50))], "After")
    assert len(of(workspace.store, Ink)) == 2 and stack.undoText() == "Add after"
    stack.undo()
    assert not of(workspace.store, Shape)
    stack.undo()
    assert len(of(workspace.store, Ink)) == 3
    stack.undo()
    assert len(of(workspace.store, Ink)) == 4 and stack.undoText() == "Add strokes"


def test_undoing_an_erase_keeps_the_drawing_order(workspace):
    view, store = workspace.view, workspace.store
    strokes = [Ink(page=0, strokes=(((100, 100 + 40 * i), (300, 100 + 40 * i)),), width=3) for i in range(3)]
    view.add_annotations(strokes, "Strokes")
    order = store.order()
    drag(view, "eraser", [(200, 90), (200, 110)])  # the bottom stroke, the first one drawn
    workspace.view.tool_settings.update_style("eraser", erase="partial")
    drag(view, "eraser", [(150, 170), (150, 190)])  # and part of the top one
    assert len(of(store, Ink)) == 2
    workspace.undo_stack.undo()
    workspace.undo_stack.undo()
    assert store.order() == order and of(store, Ink) == strokes
    z = [view._items[key].zValue() for key in order]
    assert z == sorted(z)


def test_the_eraser_is_quick_on_a_page_full_of_handwriting(workspace):
    view = workspace.view
    strokes = [Ink(page=0, strokes=(tuple((20 + (i % 20) * 28 + k * 0.25, 20 + (i // 20) * 30 + (k % 7))
                                          for k in range(100)),)) for i in range(400)]
    view.add_annotations(strokes, "Handwriting")
    workspace.tool_settings.set_tool("eraser")
    tool, page = view._tools["eraser"], view._pages[0]
    tool.press(page, QPointF(600, 780), Mouse())  # far from every stroke
    started = time.process_time()
    for k in range(20):
        tool.move(page, QPointF(600 - k * 0.5, 780), Mouse())
    per_move = (time.process_time() - started) / 20
    tool.release(page, QPointF(590, 780), Mouse())
    assert per_move < 0.01  # 55 ms before strokes far from the eraser were skipped
    assert len(of(workspace.store, Ink)) == 400


def test_dragging_onto_the_next_page_moves_the_annotation_there(workspace):
    view, store = workspace.view, workspace.store
    view.set_zoom(0.5)
    shape = Shape(page=0, rect=(250, 700, 80, 60), fill=(0, 0, 1))
    view.add_annotations([shape], "Rectangle")
    workspace.tool_settings.set_tool("select")
    view.goto_page(0)
    QApplication.processEvents()
    real_drag(view, at(view, 0, 290, 730), at(view, 1, 200, 150), steps=10)
    moved = store.get(shape.id)
    x, y, w, h = moved.rect
    width, height = workspace.document.page_size(1)
    assert moved.page == 1 and 0 <= x <= x + w <= width and 0 <= y <= y + h <= height
    assert (x + w / 2, y + h / 2) == pytest.approx((200, 150), abs=3)
    assert view._items[shape.id].parentItem() is view._pages[1]
    workspace.undo_stack.undo()
    assert store.get(shape.id) == shape and view._items[shape.id].parentItem() is view._pages[0]
    off_page = Shape(page=0, rect=(560, 300, 40, 40))
    view.add_annotations([off_page], "Rectangle")
    real_drag(view, at(view, 0, 580, 320), at(view, 0, 680, 320))  # dropped beside the page
    x, y, w, h = store.get(off_page.id).rect
    assert store.get(off_page.id).page == 0 and x + w <= workspace.document.page_size(0)[0]


def test_cut_and_delete_leave_locked_annotations(workspace):
    from zenith_pdf.ui.widgets import IconButton

    view, store = workspace.view, workspace.store
    locked = Shape(page=0, rect=(50, 50, 60, 40), locked=True)
    loose = Shape(page=0, rect=(150, 50, 60, 40))
    view.add_annotations([locked, loose], "Shapes")
    workspace.tool_settings.set_tool("select")
    view.select([locked])
    menu = build_context_menu(view, 0, QPointF(80, 70), locked)
    row = menu.actions()[0]
    assert isinstance(row, QWidgetAction)
    buttons = {b.toolTip(): b.isEnabled() for b in row.defaultWidget().findChildren(IconButton)}
    assert not buttons["Cut (Ctrl+X)"] and not buttons["Delete (Del)"] and buttons["Copy (Ctrl+C)"]
    view.select([locked, loose])
    view.cut_annotations()
    assert locked.id in store and loose.id not in store
    QApplication.clipboard().clear()


def test_apply_to_pages_keeps_the_copies_on_smaller_pages(workspace):
    view, store = workspace.view, workspace.store
    corner = Shape(page=0, rect=(480, 650, 100, 100))
    view.add_annotations([corner], "Rectangle")
    view.select([corner])
    assert view.apply_to_pages([3, 9]) == 1  # there is no page 10
    [copy] = [a for a in store.all() if a.page == 3]
    width, height = workspace.document.page_size(3)
    x, y, w, h = copy.rect
    assert (w, h) == (100, 100) and 0 <= x and x + w <= width and 0 <= y and y + h <= height


def test_one_click_on_text_selects_nothing(workspace):
    view = workspace.view
    workspace.tool_settings.set_tool("select")
    hit = next(workspace.document.iter_search("needle", False, False))
    x, y, w, h = hit.rects[0]
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     at(view, hit.page, x + w / 2, y + h / 2))
    assert not view.has_text_selection()
    real_drag(view, at(view, hit.page, x + 1, y + h / 2), at(view, hit.page, x + w - 1, y + h / 2))
    assert view.has_text_selection() and "needle" in view._text_selection[2]


def test_snapshot_scale_and_annotations(workspace):
    from PySide6.QtGui import QColor

    view, tools = workspace.view, workspace.tool_settings
    view.add_annotations([Shape(page=0, rect=(60, 60, 100, 60), color=(0, 0, 0), fill=(0, 0, 0))], "Rectangle")
    QApplication.clipboard().clear()
    tools.update_style("snapshot", snapshot_scale=1, snapshot_annotations=False)
    drag(view, "snapshot", [(50, 50), (250, 150)])
    image = QApplication.clipboard().image()
    assert (image.width(), image.height()) == (200, 100)
    assert QColor(image.pixel(60, 30)).lightness() > 200  # under the rectangle, left out
    assert tools.tool == "hand"  # the snapshot tool is unlocked by default
    tools.update_style("snapshot", snapshot_scale=2, snapshot_annotations=True)
    drag(view, "snapshot", [(50, 50), (250, 150)])
    image = QApplication.clipboard().image()
    assert image.width() == 400 and QColor(image.pixel(120, 60)).lightness() < 40
    QApplication.clipboard().clear()


def test_editing_an_equation_keeps_its_colour_size_and_scale(workspace, monkeypatch):
    from zenith_pdf.ui.commands import ChangeAnnotations

    view, store, tools = workspace.view, workspace.store, workspace.tool_settings

    class Same(insert_dialogs.EquationDialog):
        def exec(self):
            if not self.source.toPlainText():
                self.source.setPlainText(r"x^2 + y^2")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(insert_dialogs, "EquationDialog", Same)
    tools.update_style("equation", color=(0.8, 0.1, 0.1), font_size=20.0)
    clicks(view, "equation", [(300, 300)])
    [equation] = of(store, ImageStamp)
    assert equation.equation_size == 20 and equation.equation_color == (0.8, 0.1, 0.1)
    x, y, w, h = equation.rect
    bigger = replace(equation, rect=(x, y, w * 2, h * 2))
    workspace.undo_stack.push(ChangeAnnotations(store, [equation], [bigger], "Resize"))
    tools.update_style("equation", color=(0.0, 0.0, 1.0), font_size=30.0)  # for the next one
    view.edit_equation(store.get(equation.id))  # opened and saved without a change
    edited = store.get(equation.id)
    assert edited.rect == pytest.approx(bigger.rect) and edited.digest == equation.digest
    assert edited.equation_color == (0.8, 0.1, 0.1) and edited.equation_size == 20


def test_lasso_picks_and_what_the_cleaner_cleans(workspace):
    view, store, tools = workspace.view, workspace.store, workspace.tool_settings
    inside = Shape(page=0, rect=(100, 300, 40, 40))
    edge = Shape(page=0, rect=(200, 300, 60, 40))  # crosses the loop, its centre outside
    centre = Shape(page=0, rect=(170, 340, 60, 30))  # its centre inside, its right edge outside
    view.add_annotations([inside, edge, centre], "Shapes")
    loop = [(80, 280), (220, 280), (220, 380), (80, 380)]
    for pick, expected in (("centre", {inside.id, centre.id}), ("inside", {inside.id}),
                           ("touching", {inside.id, edge.id, centre.id})):
        tools.update_style("lasso", lasso_pick=pick)
        drag(view, "lasso", loop)
        assert {a.id for a in view.selected_annotations()} == expected, pick

    hit = next(workspace.document.iter_search("needle", False, False))
    x, y, w, h = hit.rects[0]
    across = [(x + 1, y + h / 2), (x + w - 1, y + h / 2)]
    drag(view, "highlight", across, page=hit.page)
    drag(view, "underline", across, page=hit.page)
    tools.update_style("cleaner", clean_kinds=("underline",))
    drag(view, "cleaner", across, page=hit.page)
    assert [m.style for m in of(store, TextMarkup)] == ["Highlight"]


def test_tape_and_laser_options(workspace):
    view, tools = workspace.view, workspace.tool_settings
    tools.update_style("tape", tape_start="peeled")
    drag(view, "tape", [(80, 300), (260, 300)])
    assert of(workspace.store, Tape)[0].revealed
    tools.update_style("laser", laser_size="thick", laser_fade=0.3)
    tools.set_tool("laser")
    laser, page = view._tools["laser"], view._pages[0]
    laser.press(page, QPointF(100, 100), Mouse())
    assert laser.item.pen().widthF() == 8.0
    laser.release(page, QPointF(100, 100), Mouse())
    assert [fade.duration() for fade, _ in laser._fades] == [300]
    laser.cancel()
    assert laser._fades == []


def test_pictures_and_signatures_take_their_size_from_the_tool(workspace, monkeypatch):
    import base64

    from PySide6.QtGui import QImage

    from zenith_pdf.core import images
    from zenith_pdf.ui import document_view

    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    picture = QImage(300, 100, QImage.Format.Format_RGB32)
    picture.fill(0x336699)
    data = images.encode(picture)
    tools.update_style("image", image_size="half")
    tools.set_subject("image", "Figure")
    view.place_images([data], 0, None, "Insert image", "image")
    [stamp] = of(store, ImageStamp)
    assert stamp.rect[2] == pytest.approx(workspace.document.page_size(0)[0] / 2) and stamp.subject == "Figure"
    tools.set_subject("image", "")

    class First(QMenu):
        def exec(self, *args):
            return self.actions()[0]

    monkeypatch.setattr(document_view, "QMenu", First)
    workspace.settings.set("signatures", [{"name": "Me", "png": base64.b64encode(data).decode("ascii")}])
    tools.update_style("signature", signature_width=200.0)
    clicks(view, "signature", [(300, 400)])
    signature = [a for a in of(store, ImageStamp) if a.id != stamp.id][0]
    assert signature.rect[2:] == pytest.approx((200.0, 200.0 / 3)) and signature.subject == "Signature"
    workspace.settings.set("signatures", [])


def test_a_program_in_an_attachment_is_opened_only_when_confirmed(workspace, monkeypatch, tmp_path):
    from zenith_pdf.ui import document_view

    opened, asked = [], []

    class Desktop:
        @staticmethod
        def openUrl(url):
            opened.append(url.toLocalFile())

    monkeypatch.setattr(document_view, "QDesktopServices", Desktop)  # nothing really opens
    monkeypatch.setattr(document_view.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(messages, "confirm", lambda *a, **k: asked.append(a[1]) or False)
    view = workspace.view
    program = Attachment(page=0, position=(100, 100), file_name="setup.exe", data=b"MZ")
    view.open_attachment(program)
    assert asked == ["Open a program?"] and opened == []
    monkeypatch.setattr(messages, "confirm", lambda *a, **k: True)
    view.open_attachment(program)
    assert len(opened) == 1 and opened[0].endswith("setup.exe")
    view.open_attachment(Attachment(page=0, position=(100, 100), file_name="notes.pdf", data=b"%PDF"))
    assert len(asked) == 1 and opened[-1].endswith("notes.pdf")  # a document opens without asking


def test_an_attachment_name_cannot_hide_a_program(workspace, monkeypatch, tmp_path):
    """Windows drops dots and spaces at the end of a name: "invoice.exe." is written, and runs, as invoice.exe."""
    from zenith_pdf.ui import document_view
    from zenith_pdf.ui.document_view import attachment_file_name

    assert attachment_file_name("invoice.exe. ") == "invoice.exe"
    assert attachment_file_name(r"C:\temp/../a<b>.txt") == "a_b_.txt"
    assert attachment_file_name("report.pdf:hidden.exe") == "report.pdf_hidden.exe"
    assert attachment_file_name("CON.txt") == "_CON.txt" and attachment_file_name(" . ") == "attachment"
    opened, asked = [], []

    class Desktop:
        @staticmethod
        def openUrl(url):
            opened.append(url.toLocalFile())

    monkeypatch.setattr(document_view, "QDesktopServices", Desktop)  # nothing really opens
    monkeypatch.setattr(document_view.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(messages, "confirm", lambda *a, **k: asked.append(a[1]) or False)
    view = workspace.view
    for name in ("invoice.exe.", "invoice.exe ", "invoice.bat. ", "macros.xlsm"):
        view.open_attachment(Attachment(page=0, position=(100, 100), file_name=name, data=b"harmless"))
    assert asked == ["Open a program?"] * 4 and opened == []
    view.open_attachment(Attachment(page=0, position=(100, 100), file_name="archive.zip", data=b"PK"))
    assert asked[-1] == "Open the attachment?" and opened == []  # a kind that is not a document or media
    monkeypatch.setattr(messages, "confirm", lambda *a, **k: True)
    view.open_attachment(Attachment(page=0, position=(100, 100), file_name="invoice.exe.", data=b"harmless"))
    assert opened[-1].endswith("invoice.exe")  # the name Windows would have made of it, checked as such


def double_click(view, point):
    """What Windows sends for a double click: a click, then a double click event in place of the second press."""
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mouseClick(view.viewport(), left, none, point)
    QTest.mouseDClick(view.viewport(), left, none, point)
    QTest.mouseRelease(view.viewport(), left, none, point)


def test_double_clicks_play_notes_in_pan_and_draw_two_dots(workspace, monkeypatch):
    from zenith_pdf.ui import audio_dialog

    view, store, tools = workspace.view, workspace.store, workspace.tool_settings
    note = Attachment(page=0, position=(300, 300), file_name="a.wav", data=b"RIFF", audio=True)
    view.add_annotations([note], "Audio note")
    played = []
    monkeypatch.setattr(audio_dialog, "play_audio", lambda parent, name, *a, **k: played.append(name))
    tools.set_tool("hand")  # where unlocked tools go after one use
    double_click(view, at(view, 0, 308, 308))
    assert played == ["a.wav"] and store.get(note.id) == note  # played, not moved or selected
    assert not view.selected_annotations()
    tools.set_tool("pen")
    double_click(view, at(view, 0, 150, 500))
    assert len(of(store, Ink)) == 2  # the second click of a quick pair is a dot too


def test_clicks_inside_the_text_being_typed_move_the_caret(workspace):
    view = workspace.view
    workspace.tool_settings.set_tool("select")
    view.new_text_box(0, (100.0, 100.0))
    editor = view._editor[0]
    editor.setPlainText("Hello world")
    box = editor.sceneBoundingRect()
    inside = view.mapFromScene(box.left() + 2, box.center().y())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, inside)
    assert view.is_editing_text() and editor.textCursor().position() < len("Hello world")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     at(view, 0, 400, 600))
    QTest.qWait(10)
    assert not view.is_editing_text()
    assert [a.contents for a in of(workspace.store, FreeText)] == ["Hello world"]


def test_the_first_signature_is_made_without_a_menu(workspace, monkeypatch):
    import base64

    from PySide6.QtGui import QImage

    from zenith_pdf.core import images
    from zenith_pdf.ui import document_view

    picture = QImage(90, 30, QImage.Format.Format_RGB32)
    picture.fill(0x000000)
    record = {"name": "Me", "png": base64.b64encode(images.encode(picture)).decode("ascii")}
    menus = []

    class Watched(QMenu):
        def exec(self, *args):
            menus.append(self)
            return None

    class Signed(insert_dialogs.SignatureDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

        def signature(self):
            return record

    monkeypatch.setattr(document_view, "QMenu", Watched)
    monkeypatch.setattr(insert_dialogs, "SignatureDialog", Signed)
    workspace.settings.set("signatures", [])
    clicks(workspace.view, "signature", [(300, 400)])
    assert menus == [] and len(of(workspace.store, ImageStamp)) == 1
    assert workspace.settings.get("signatures") == [record]
    workspace.settings.set("signatures", [])


def test_context_menu_keeps_page_commands_together(workspace):
    view = workspace.view
    menu = build_context_menu(view, 1, QPointF(50, 50), None)
    top = [a.text() for a in menu.actions()]
    pages = next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == "Page 2")
    inside = [a.text() for a in pages.actions() if not a.isSeparator()]
    for expected in ("Add page (after page 2)", "Copy page", "Duplicate page", "Delete page 2",
                     "Rotate page (Clockwise)", "Rotate page (Counter-clockwise)", "Extend margin", "Watermark…"):
        assert expected in inside and expected not in top
    audio = Attachment(page=0, position=(100, 100), file_name="a.wav", data=b"RIFF", audio=True)
    view.add_annotations([audio], "Audio note")
    texts = [a.text() for a in build_context_menu(view, 0, QPointF(105, 105), audio).actions()]
    assert "Play audio note" in texts and "Save attachment as…" in texts


# The window: tabs, questions and stored preferences -----------------------------------------------


def test_the_tool_chooser_closes_with_its_tab(window, workspace, sample_pdf, tmp_path):
    import shiboken6

    def chooser_shown(ws):
        chooser = ws._chooser
        return chooser is not None and shiboken6.isValid(chooser) and chooser.isVisible()

    workspace.show_tool_chooser()
    QTest.qWait(30)
    assert chooser_shown(workspace)
    window.show_home()
    assert not chooser_shown(workspace) and window.tabs.property("home") is True
    window._show_tab(window.workspaces.index(workspace))
    assert window.tabs.property("home") is False
    workspace.show_tool_chooser()
    QTest.qWait(30)
    other = tmp_path / "other.pdf"
    shutil.copy(sample_pdf, other)
    window.open_file(other)
    assert not chooser_shown(workspace)
    window.close_tab(window.workspaces.index(window.current_workspace()), confirm=False)


def test_the_window_can_be_narrow(window):
    assert window.stack.minimumWidth() == 720 and window.minimumSizeHint().width() <= 960


def test_unsaved_changes_are_asked_about_with_verbs(window, workspace, monkeypatch):
    workspace.view.add_annotations([Shape(page=0, rect=(10, 10, 20, 20))], "Rectangle")
    asked = []
    monkeypatch.setattr(messages, "save_discard_cancel", lambda parent, title, *a, **k: asked.append(title))
    window.close_current_tab()
    assert workspace in window.workspaces and asked == ["Save the changes to doc.pdf?"]  # Cancel keeps it
    monkeypatch.setattr(messages, "save_discard_cancel", lambda *a, **k: "discard")
    window.close_current_tab()
    assert workspace not in window.workspaces


def test_a_new_document_from_the_dialog(window, monkeypatch, tmp_path):
    from zenith_pdf.ui import main_window
    from zenith_pdf.ui.dialogs import NewDocumentDialog

    answers = [QDialog.DialogCode.Rejected, QDialog.DialogCode.Accepted]

    class Answer(NewDocumentDialog):
        def exec(self):
            self.name_field.setText("Lecture 3")
            return answers.pop(0)

    monkeypatch.setattr(main_window, "NewDocumentDialog", Answer)
    window.settings.set("new_folder", str(tmp_path))
    tabs = len(window.workspaces)
    window.new_document_dialog()  # cancelled
    assert len(window.workspaces) == tabs
    window.new_document_dialog()
    made = window.current_workspace()
    assert len(window.workspaces) == tabs + 1 and made.document.path.name == "Lecture 3.pdf"
    window.close_tab(window.workspaces.index(made), confirm=False)
    window.settings.set("new_folder", "")


def test_ocr_asks_about_this_page_or_all_pages(workspace, monkeypatch):
    seen = []

    def ask(parent, title, text, buttons=None, *args, **kwargs):
        seen.append([label for _, label, _ in buttons])
        return None

    monkeypatch.setattr(messages, "ask", ask)
    workspace.actions.run_ocr(0)
    assert seen == [["This page", "All pages", "Cancel"]]


def test_tool_styles_are_written_once_the_changes_stop(window):
    tools = window.tool_settings
    before = window.settings.get("tool_styles")
    for width in (3.0, 4.0, 5.0, 6.0):  # a slider being dragged
        tools.update_style("pen", width=width)
    assert window.settings.get("tool_styles") == before
    QTest.qWait(450)
    assert window.settings.get("tool_styles")["pen"]["style"]["width"] == 6.0
    tools.reset_style("pen")


def test_autosave_and_closing_wait_for_a_busy_document(window, workspace):
    from PySide6.QtCore import QTimer

    workspace.view.add_annotations([Shape(page=0, rect=(10, 10, 20, 20))], "Rectangle")
    workspace.busy = True  # a compression works on it in the background
    window.autosave()
    assert workspace.dirty
    workspace.busy = False
    window.autosave()
    assert not workspace.dirty
    workspace.busy = True
    QTimer.singleShot(150, lambda: setattr(workspace, "busy", False))
    window.close_tab(window.workspaces.index(workspace), confirm=False)
    assert workspace not in window.workspaces


def test_lowering_the_recent_limit_loses_no_files(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    for number in range(5):
        settings.add_recent(tmp_path / f"{number}.pdf")
    settings.set("recent_limit", 2)
    assert [p.name for p in settings.recent_files()] == ["4.pdf", "3.pdf"]
    settings.remove_recent(tmp_path / "4.pdf")
    settings.set("recent_limit", 10)
    assert [p.name for p in settings.recent_files()] == ["3.pdf", "2.pdf", "1.pdf", "0.pdf"]