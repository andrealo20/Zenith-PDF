"""The side panel's right edge drags it wider or narrower: in every tab, remembered, thumbnails growing with it."""

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest

from conftest import settle
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import side_panel


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("side_panel", "always")
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1400, 900)
    window.show()
    yield window
    window.close()
    window.renderer.shutdown()


def test_dragging_the_panels_edge_resizes_it_in_every_tab(window, sample_pdf, tmp_path):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    first = window.current_workspace()
    window.open_file(copy)
    second = window.current_workspace()
    settle()
    side = second.side
    assert side.panel_width() == side_panel.PANEL_WIDTH
    thumbs_before = second.thumbnails.model.thumb_width

    grip = side._grip
    assert grip.isVisible() and grip.cursor().shape() == Qt.CursorShape.SplitHCursor
    start = QPoint(grip.width() // 2, 200)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(grip, start + QPoint(120, 0))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(120, 0))
    settle()

    wider = side_panel.PANEL_WIDTH + 120
    assert side.panel_width() == wider
    assert window.settings.get("side_panel_width") == wider  # remembered for next time
    assert first.side.panel_width() == wider  # the other tab's panel follows
    assert second.thumbnails.model.thumb_width > thumbs_before  # bigger pages to pick from

    side.set_panel_width(10_000)
    assert side.panel_width() == side_panel.PANEL_MAX
    side.set_panel_width(0)
    assert side.panel_width() == side_panel.PANEL_MIN

    side.hide_panel()
    assert not grip.isVisible()  # no edge to drag without a panel
