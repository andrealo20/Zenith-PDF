"""Measuring angles and volumes."""

import math
import shutil

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Measurement, angle_at
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui import messages

pytestmark = pytest.mark.usefixtures("qt_app")


def test_an_angle_in_degrees_and_its_arc():
    right = Measurement(page=0, points=((100, 0), (0, 0), (0, 100)), mode="angle")
    assert right.value() == pytest.approx(90) and right.text() == "90.0°"
    assert angle_at(((10, 0), (0, 0), (-10, 0.0001))) == pytest.approx(180, abs=0.01)
    arc = right.arc()
    assert all(math.hypot(x, y) == pytest.approx(18) for x, y in arc)
    assert arc[0] == pytest.approx((18, 0)) and arc[-1] == pytest.approx((0, 18), abs=1e-9)
    x, y, w, h = right.label_rect()
    assert x + w / 2 > 18 and y + h / 2 > 18  # outside the arc, in the angle's middle


def test_a_volume_is_an_area_times_its_depth():
    square = ((0, 0), (72, 0), (72, 72), (0, 72))  # an inch a side
    volume = Measurement(page=0, points=square, mode="volume", scale=2.54 / 72, unit="cm", depth=10)
    assert volume.value() == pytest.approx(2.54 ** 2 * 10) and volume.text() == "64.5 cm³"
    assert volume.closed and volume.kind == "Polygon"


def test_angles_and_volumes_survive_saving(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    angle = Measurement(page=0, points=((300, 300), (200, 300), (250, 220)), mode="angle")
    volume = Measurement(page=0, points=((100, 400), (200, 400), (200, 480)), mode="volume", depth=2.5, unit="m",
                         scale=0.01)
    document = Document(path)
    document.save([angle, volume])
    document.close()
    reopened = Document(path)
    loaded = {a.id: a for a in reopened.annotations}
    assert loaded[angle.id].mode == "angle" and loaded[angle.id].text() == angle.text()
    assert loaded[volume.id].mode == "volume" and loaded[volume.id].depth == 2.5
    assert loaded[volume.id].text() == volume.text() and loaded[volume.id].contents == ""
    reopened.close()


@pytest.fixture
def workspace(qt_app, tmp_path, sample_pdf):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    QTest.qWait(60)
    yield window.current_workspace()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


class Mouse:
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier


def test_the_angle_tool_is_done_at_the_third_click_and_volume_asks_the_depth(workspace, monkeypatch):
    view, tools, store = workspace.view, workspace.tool_settings, workspace.store
    tools.set_tool("angle")
    tool, page = view._tool(), view._pages[0]
    for point in ((300, 300), (200, 300), (200, 200)):
        tool.press(page, QPointF(*point), Mouse())
    [angle] = [a for a in store.all() if isinstance(a, Measurement)]
    assert angle.mode == "angle" and angle.value() == pytest.approx(90) and not tool.busy

    asked = []
    monkeypatch.setattr(messages, "get_double", lambda *args, **kwargs: asked.append(args) or 3.0)
    tools.set_tool("volume")
    tool = view._tool()
    for point in ((100, 400), (200, 400), (200, 480)):
        tool.press(page, QPointF(*point), Mouse())
    tool.finish()
    [volume] = [a for a in store.all() if isinstance(a, Measurement) and a.mode == "volume"]
    assert asked and volume.depth == 3.0 and tools.style("volume").depth == 3.0

    monkeypatch.setattr(messages, "get_double", lambda *args, **kwargs: None)  # cancelled: nothing is added
    for point in ((300, 500), (400, 500), (400, 580)):
        tool.press(page, QPointF(*point), Mouse())
    tool.finish()
    assert len([a for a in store.all() if isinstance(a, Measurement)]) == 2
    tools.reset_style("volume")
