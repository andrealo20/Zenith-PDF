import pytest
from PySide6.QtCore import QSettings

from conftest import wait_for
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)  # each test opens files fresh, at the default zoom and page
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    yield window
    window.close()
    window.renderer.shutdown()


def test_open_navigate_and_close_tabs(window, sample_pdf, tmp_path):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    window.open_file(copy)
    window.open_file(sample_pdf)  # already open: switches instead of duplicating
    assert window.tabs.count() == 2
    assert window.tabs.currentIndex() == 0

    view = window.current_workspace().view
    view.zoom_in()
    view.goto_page(3)
    assert view.current_page == 3
    page = view._pages[3]
    assert wait_for(lambda: window.renderer.is_idle() and page.base is not None and view._cache.levels(3))

    window.close_current_tab()
    window.close_tab(0)
    assert window.tabs.count() == 0
    assert window.stack.currentWidget() is window.home
    assert window.tabs.isHidden()


def test_tiles_land_on_screen_exactly_where_the_page_is(window, sample_pdf):
    """Grab the window and compare the page area with a direct rendering at the same scale."""
    window.open_file(sample_pdf)
    window.current_workspace().side.hide_panel()
    view = window.current_workspace().view
    view.set_zoom(2.5)
    view.goto_page(0)
    view.horizontalScrollBar().setValue(0)  # the page is wider than the view: show its left edge
    assert wait_for(lambda: window.renderer.is_idle() and view.missing_tiles() == 0)

    grab = window.grab().toImage()
    ratio = grab.devicePixelRatio()
    page = view._pages[0]
    origin = view.viewport().mapTo(window, view.mapFromScene(page.mapToScene(0, 0)))
    scale = view._view_scale() * ratio
    # The glyphs of "Page 1 needle", about (70, 80) to (200, 105) in page points.
    x, y = round(origin.x() * ratio + 70 * scale), round(origin.y() * ratio + 80 * scale)
    w, h = round(130 * scale), round(25 * scale)
    shown = grab.copy(x, y, w, h).convertToFormat(grab.format())
    expected = window.current_workspace().document.render(0, scale)
    expected = expected.copy(round(70 * scale), round(80 * scale), w, h).convertToFormat(grab.format())
    differing = sum(1 for j in range(h) for i in range(w) if shown.pixel(i, j) != expected.pixel(i, j))
    assert differing < 0.01 * w * h
    window.close_current_tab()


def test_search_highlights_every_hit(window, sample_pdf):
    window.open_file(sample_pdf)
    workspace = window.current_workspace()
    workspace.show_search()
    workspace.search.field.setText("needle")
    workspace.search.start()
    assert wait_for(lambda: workspace.search.status.text() == "4 results")
    assert len(workspace.view._hit_items) == 4
    workspace.search.step(1)
    assert workspace.view.current_page == 1
    window.close_current_tab()


def test_theme_switch_round_trip(window):
    window.set_theme("light")
    window.set_theme("dark")
    assert window.palette().window().color().name() == "#1b1c1f"
