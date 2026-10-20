"""Documents open at 100%: new ones, reopened ones and the tabs the app reopens at start, each at its page."""

import pytest
from PySide6.QtCore import QSettings

from conftest import APP_DEFAULT_ZOOM, wait_for
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings


def test_the_app_opens_documents_at_100_percent():
    assert APP_DEFAULT_ZOOM == "actual"


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("default_zoom", APP_DEFAULT_ZOOM)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def test_a_document_reopens_at_its_page_but_at_100_percent(window, sample_pdf):
    window.settings.remember_file_state(sample_pdf, {"path": str(sample_pdf), "page": 2, "zoom": 2.5, "fit": "width"})
    window.open_file(sample_pdf)
    view = window.current_workspace().view
    assert wait_for(lambda: view.current_page == 2, timeout=5)
    assert view.zoom == pytest.approx(1.0)  # not the 2.5 it was left at


def test_the_tabs_reopened_at_start_open_at_100_percent(window, sample_pdf):
    window.settings.set("session", {"tabs": [{"path": str(sample_pdf), "page": 1, "zoom": 3.0, "fit": None}],
                                    "active": 0})
    window.restore_session()
    view = window.current_workspace().view
    assert wait_for(lambda: view.current_page == 1, timeout=5)
    assert view.zoom == pytest.approx(1.0)
