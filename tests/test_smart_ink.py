"""Pen types, Smart ink, the eraser's two modes, and the options of the line and shape tools."""

import itertools
import math
import shutil

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Ink, Line, Poly, Shape
from zenith_pdf.settings import Settings
from zenith_pdf.ui import drawing_tools
from zenith_pdf.ui.tools import DEFAULT_STYLES


class Mouse:
    def __init__(self, modifiers=Qt.KeyboardModifier.NoModifier):
        self._modifiers = modifiers

    def modifiers(self):
        return self._modifiers

    def globalPosition(self):
        return QPointF(400, 300)


def stroke(view, key, points, page=0, modifiers=Qt.KeyboardModifier.NoModifier):
    """Draw with the tool `key` (a copy like "pen#2" too), as the canvas would."""
    view.tool_settings.set_tool(key)
    tool = view._tool()
    item = view._pages[page]
    tool.press(item, QPointF(*points[0]), Mouse(modifiers))
    for point in points[1:]:
        tool.move(item, QPointF(*point), Mouse(modifiers))
    tool.release(item, QPointF(*points[-1]), Mouse(modifiers))
    return tool


def sampled(corners, step=1.5):
    out = []
    for p, q in zip(corners, corners[1:]):
        n = max(1, int(math.dist(p, q) / step))
        out += [(p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n) for i in range(n)]
    return out + [corners[-1]]


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
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_pro_pen_gets_thinner_when_the_hand_is_faster(workspace, monkeypatch):
    view, tools = workspace.view, workspace.tool_settings
    ticks = itertools.chain(itertools.accumulate([0.0] + [0.05] * 30), itertools.count(1.5, 0.002))
    monkeypatch.setattr(drawing_tools, "clock", lambda: next(ticks))  # slow for 30 points, then fast
    tools.update_style("pen", pen="pro", width=6.0)
    points = [(60 + i * 4.0, 300 + 30 * math.sin(i / 3)) for i in range(60)]
    stroke(view, "pen", points)
    [ink] = of(workspace.store, Ink)
    [widths] = ink.widths
    assert len(widths) == len(ink.strokes[0]) and ink.width == max(widths) <= 6.0
    slow = [w for p, w in zip(ink.strokes[0], widths) if 80 < p[0] < 160]
    fast = [w for p, w in zip(ink.strokes[0], widths) if 220 < p[0] < 280]
    assert slow and fast and sum(slow) / len(slow) > 1.5 * sum(fast) / len(fast)
    assert widths[0] < max(widths) and widths[-1] < max(widths)  # both ends taper


def test_basic_and_calligraphy_pens(workspace):
    view, tools = workspace.view, workspace.tool_settings
    stroke(view, "pen", [(50, 100), (90, 120), (130, 100), (170, 125)])
    [basic] = of(workspace.store, Ink)
    assert basic.widths == () and basic.width == 2.0
    tools.update_style("pen", pen="calligraphy", width=8.0, nib_angle=45.0)
    stroke(view, "pen", sampled([(50, 300), (250, 300), (350, 400)], 6))  # across the nib, then along it
    calligraphy = [ink for ink in of(workspace.store, Ink) if ink.widths][0]
    widths = dict(zip(calligraphy.strokes[0], calligraphy.widths[0]))
    across = widths[(50.0, 300.0)]
    along = [w for (x, y), w in widths.items() if x > 300]
    assert across == pytest.approx(8 * math.sin(math.radians(45)), abs=0.01)
    assert along and max(along) < 0.3 * across


def test_shift_draws_a_ruled_pro_line_of_one_width(workspace):
    view, tools = workspace.view, workspace.tool_settings
    tools.update_style("pen", pen="pro")
    stroke(view, "pen", [(100, 200), (150, 210), (300, 200)], modifiers=Qt.KeyboardModifier.ShiftModifier)
    [ink] = of(workspace.store, Ink)
    assert ink.strokes == (((100, 200), (300, 200)),) and ink.widths == ()


def test_ink_to_shape_and_ink_to_line(workspace):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    rectangle = sampled([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)])
    line = sampled([(80, 400), (400, 430)])
    stroke(view, "pen", rectangle)
    stroke(view, "pen", line)
    assert len(of(store, Ink)) == 2 and not of(store, Shape) and not of(store, Line)  # the options are off

    tools.update_style("pen", ink_to_shape=True, ink_to_line=True, color=(0.9, 0.1, 0.1))
    tools.set_subject("pen", "Sketch")
    stroke(view, "pen", rectangle)
    stroke(view, "pen", line)
    stroke(view, "pen", sampled([(100, 600), (220, 600), (160, 500), (103, 596)]))
    [shape] = of(store, Shape)
    assert shape.shape == "Square" and shape.color == (0.9, 0.1, 0.1) and shape.subject == "Sketch"
    assert shape.rect == pytest.approx((99, 99, 162, 102))  # the border sits on the stroke
    [made_line] = of(store, Line)
    assert made_line.start == (80, 400) and made_line.end == pytest.approx((400, 430))
    [triangle] = of(store, Poly)
    assert triangle.closed and len(triangle.points) == 3
    workspace.undo_stack.undo()
    assert not of(store, Poly)

    tools.update_style("marker", ink_to_shape=True)
    stroke(view, "marker", sampled([(300, 100), (460, 100), (460, 200), (300, 200), (300, 104)]))
    marked = [s for s in of(store, Shape) if s.rect[0] > 280]
    assert marked and marked[0].highlight and marked[0].width == DEFAULT_STYLES["marker"].width


def test_partial_erase_cuts_a_stroke_in_one_undo_step(workspace):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    stroke(view, "pen", [(100, 300), (200, 305), (300, 300)])
    [ink] = of(store, Ink)
    steps = workspace.undo_stack.count()
    tools.update_style("eraser", erase="partial", width=10.0)
    stroke(view, "eraser", [(200, 250), (199, 280), (201, 320), (200, 350)])
    [cut] = of(store, Ink)
    assert cut.id == ink.id and len(cut.strokes) == 2
    left, right = cut.strokes
    assert left[0] == (100, 300) and left[-1][0] < 195 and right[0][0] > 205 and right[-1] == (300, 300)
    assert workspace.undo_stack.count() == steps + 1 and workspace.undo_stack.undoText() == "Erase"
    workspace.undo_stack.undo()
    assert of(store, Ink) == [ink]
    workspace.undo_stack.redo()
    assert len(of(store, Ink)[0].strokes) == 2
    assert tools.tool == "eraser"  # locked by default


def test_whole_erase_keeps_widths_with_their_strokes(workspace):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    strokes = tuple(((100.0, y), (300.0, y)) for y in (100.0, 150.0, 200.0))
    ink = Ink(page=0, strokes=strokes, width=4.0, widths=((1.0, 2.0), (3.0, 4.0), (1.5, 2.5)))
    view.add_annotations([ink], "Drawing")
    stroke(view, "eraser", [(200, 140), (200, 160)])
    [left] = of(store, Ink)
    assert left.strokes == (strokes[0], strokes[2]) and left.widths == ((1.0, 2.0), (1.5, 2.5))
    assert left.width == 2.5
    tools.update_style("eraser", erase="partial")
    stroke(view, "eraser", [(150, 90), (150, 110)])
    [partly] = of(store, Ink)
    assert len(partly.strokes) == 3 and all(len(s) == len(w) for s, w in zip(partly.strokes, partly.widths))


def test_line_and_shape_tools_use_their_options(workspace):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    tools.update_style("line", dash="dashed", head_start="Circle", head_end="Diamond", highlight=True, opacity=0.5)
    tools.set_subject("line", "Dimension")
    stroke(view, "line", [(50, 50), (250, 50)])
    [line] = of(store, Line)
    assert (line.dash, line.head_start, line.head_end, line.highlight, line.opacity) == (
        "dashed", "Circle", "Diamond", True, 0.5)
    assert line.subject == "Dimension" and line.label == "Line"
    stroke(view, "arrow", [(50, 100), (250, 100)])
    assert [a.head_end for a in of(store, Line) if a.start == (50, 100)] == ["ClosedArrow"]

    tools.update_style("rectangle", fill=(1.0, 1.0, 0.0), border=False, dash="dotted")
    stroke(view, "rectangle", [(50, 200), (150, 260)])
    [shape] = of(store, Shape)
    assert shape.fill == (1.0, 1.0, 0.0) and not shape.border and shape.dash == "dotted" and shape.subject == "Rectangle"

    tools.update_style("polyline", head_end="OpenArrow", dash="dashdot")
    tools.update_style("polygon", fill=(0.0, 0.5, 1.0))
    for key, points in (("polyline", [(50, 400), (100, 380), (150, 420)]),
                        ("polygon", [(300, 400), (350, 380), (330, 450)])):
        tools.set_tool(key)
        tool = view._tool()
        for point in points:
            tool.press(view._pages[0], QPointF(*point), Mouse())
        tool.finish()
    polyline, polygon = sorted(of(store, Poly), key=lambda p: p.closed)
    assert polyline.head_end == "OpenArrow" and polyline.dash == "dashdot" and not polyline.closed
    assert polygon.closed and polygon.fill == (0.0, 0.5, 1.0) and polygon.head_end == "None"


def test_live_preview_of_a_varying_stroke(workspace):
    view, tools = workspace.view, workspace.tool_settings
    tools.update_style("pen", pen="pro", width=6.0)
    tools.set_tool("pen")
    tool, page = view._tools["pen"], view._pages[0]
    points = [(60 + i * 1.5, 300 + 60 * math.sin(i / 20)) for i in range(200)]
    tool.press(page, QPointF(*points[0]), Mouse())
    for point in points[1:100]:
        tool.move(page, QPointF(*point), Mouse())
    assert tool.trail is not None and tool.item is None
    pieces = tool.trail.path().elementCount()
    view.viewport().repaint()
    assert 0 < tool.trail.cost < drawing_tools.PREVIEW_BUDGET
    tool.trail.cost = 1.0  # as if painting it took too long
    for point in points[100:]:
        tool.move(page, QPointF(*point), Mouse())
    assert tool.trail is None and tool.item is not None and pieces > 0  # shown plainly until the release
    tool.release(page, QPointF(*points[-1]), Mouse())
    [ink] = of(workspace.store, Ink)
    assert ink.widths and tool.item is None
