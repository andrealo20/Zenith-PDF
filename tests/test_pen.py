"""A pen on a tablet or a Surface: pressure for the width, the eraser end, and a hand resting on the screen.

Tablet events are made up here as Qt would send them; Qt turns them into mouse events, which the tests send
too."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QInputDevice, QMouseEvent, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core import inking
from zenith_pdf.core.annotations import Ink
from zenith_pdf.settings import Settings

PEN = QPointingDevice("Test pen", 9001, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                      QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
ERASER = QPointingDevice("Test pen eraser", 9002, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Eraser,
                         QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
TOUCH = QPointingDevice("Test screen", 9003, QInputDevice.DeviceType.TouchScreen, QPointingDevice.PointerType.Finger,
                        QInputDevice.Capability.Position, 10, 0)
LEFT, NONE = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton


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
def view(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    QTest.qWait(30)
    yield workspace.view
    workspace.tool_settings.set_tool("select")
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def on_page(view, x, y) -> QPointF:
    return QPointF(view.mapFromScene(view._pages[0].mapToScene(QPointF(x, y))))


def tablet(view, kind, device, at: QPointF, pressure: float, buttons=LEFT) -> None:
    event = QTabletEvent(kind, device, at, QPointF(view.viewport().mapToGlobal(at.toPoint())), pressure, 0.0, 0.0,
                         0.0, 0.0, 0.0, Qt.KeyboardModifier.NoModifier,
                         LEFT if kind != QEvent.Type.TabletMove else NONE, buttons)
    QApplication.sendEvent(view.viewport(), event)


def pen_stroke(view, points, pressures, device=PEN) -> None:
    """A stroke as a pen makes it: each tablet event, then the mouse event Qt makes of it."""
    viewport = view.viewport()
    first = on_page(view, *points[0])
    tablet(view, QEvent.Type.TabletPress, device, first, pressures[0])
    QTest.mousePress(viewport, LEFT, Qt.KeyboardModifier.NoModifier, first.toPoint())
    for point, pressure in zip(points[1:], pressures[1:]):
        at = on_page(view, *point)
        tablet(view, QEvent.Type.TabletMove, device, at, pressure)
        QTest.mouseMove(viewport, at.toPoint())
    last = on_page(view, *points[-1])
    tablet(view, QEvent.Type.TabletRelease, device, last, 0.0, NONE)
    QTest.mouseRelease(viewport, LEFT, Qt.KeyboardModifier.NoModifier, last.toPoint())


def test_pressure_makes_widths():
    light, middle, firm = inking.pressure_widths([0.05], 2.0)[0], inking.pressure_widths([0.5], 2.0)[0], \
        inking.pressure_widths([1.0], 2.0)[0]
    assert light < 1.0 < middle < 2.5 < firm <= 3.2
    smoothed = inking.pressure_widths([0.2, 1.0], 2.0)
    assert smoothed[1] < inking.pressure_widths([1.0], 2.0)[0]  # a jump in pressure eases in


def test_the_line_follows_the_pressure(view):
    view.tool_settings.set_tool("pen")
    points = [(100 + 12 * n, 200 + 3 * n) for n in range(20)]
    pressures = [0.1 + 0.045 * n for n in range(20)]  # pressing harder and harder
    pen_stroke(view, points, pressures)
    [ink] = [a for a in view.store.all() if isinstance(a, Ink)]
    widths = ink.stroke_widths()[0]
    assert len(set(widths)) > 3 and widths[-1] > 2 * widths[0]  # thin to thick
    assert ink.width == max(widths)


def test_a_mouse_keeps_one_width_and_the_highlighter_ignores_pressure(view):
    view.tool_settings.set_tool("pen")
    viewport = view.viewport()
    QTest.mousePress(viewport, LEFT, Qt.KeyboardModifier.NoModifier, on_page(view, 100, 300).toPoint())
    for n in range(1, 8):
        QTest.mouseMove(viewport, on_page(view, 100 + 15 * n, 300).toPoint())
    QTest.mouseRelease(viewport, LEFT, Qt.KeyboardModifier.NoModifier, on_page(view, 205, 300).toPoint())
    assert view.pen_pressure() is None
    [ink] = [a for a in view.store.all() if isinstance(a, Ink)]
    assert not ink.widths  # a basic pen with a mouse: one width
    view.tool_settings.set_tool("marker")
    pen_stroke(view, [(100, 230), (200, 230), (300, 230)], [0.1, 0.9, 0.5])
    marker = [a for a in view.store.all() if isinstance(a, Ink) and a.highlighter]
    assert marker and not marker[0].widths


def test_the_pens_eraser_rubs_out(view):
    view.tool_settings.set_tool("pen")
    pen_stroke(view, [(100, 250), (200, 250), (300, 250)], [0.5, 0.5, 0.5])
    assert [a for a in view.store.all() if isinstance(a, Ink)]
    pen_stroke(view, [(200, 220), (200, 250), (200, 280)], [0.5, 0.5, 0.5], device=ERASER)
    left = [a for a in view.store.all() if isinstance(a, Ink)]
    assert all(len(ink.strokes) == 2 for ink in left) or not left  # cut in two, or gone
    assert view.tool_settings.tool == "pen"  # the pen draws again afterwards


def test_a_hand_resting_on_the_screen_draws_nothing(view):
    view.tool_settings.set_tool("pen")
    tablet(view, QEvent.Type.TabletMove, PEN, on_page(view, 50, 50), 0.0, NONE)  # the pen hovers near
    viewport = view.viewport()
    for kind, x, buttons in ((QEvent.Type.MouseButtonPress, 100, LEFT), (QEvent.Type.MouseMove, 200, LEFT),
                             (QEvent.Type.MouseButtonRelease, 300, NONE)):
        at = on_page(view, x, 260)
        event = QMouseEvent(kind, at, QPointF(viewport.mapToGlobal(at.toPoint())), LEFT, buttons,
                            Qt.KeyboardModifier.NoModifier, TOUCH)
        QApplication.sendEvent(viewport, event)
    assert not [a for a in view.store.all() if isinstance(a, Ink)]
