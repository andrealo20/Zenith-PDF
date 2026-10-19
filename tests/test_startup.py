"""Starting: the card in the middle of the screen, and the window out of sight until its pages are drawn."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QWidget

from conftest import wait_for
from zenith_pdf import app
from zenith_pdf.settings import Settings
from zenith_pdf.splash import HEIGHT, WIDTH, card, show_splash
from zenith_pdf.theme import LIGHT


def test_the_splash_is_a_rounded_card_in_the_theme(qt_app):
    image = card(LIGHT, 2.0).toImage()
    assert (image.width(), image.height()) == (WIDTH * 2, HEIGHT * 2)  # sharp on a 200% screen
    assert image.pixelColor(0, 0).alpha() == 0  # a rounded corner
    assert image.pixelColor(20, image.height() - 20).name() == QColor(LIGHT.raised).name()
    splash = show_splash("dark")
    assert splash.isVisible()
    splash.close()


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    _app, window = app.create_app(["zenith-pdf"], settings)
    window.resize(1000, 700)
    yield window
    window.close()
    window.renderer.shutdown()


def start(window):
    """As main() starts: the splash up, the window shown out of sight."""
    splash, revealed = QWidget(), []
    splash.show()
    window.setWindowOpacity(0.0)
    window.show()

    def done():  # how many tiles were still to draw when it came into sight
        workspace = window.current_workspace()
        revealed.append(workspace.view.missing_tiles() if workspace else None)

    app._Reveal(window, splash, done)
    return splash, revealed


def test_the_window_comes_into_sight_once_its_pages_are_drawn(window, sample_pdf, monkeypatch):
    monkeypatch.setattr(app, "REVEAL_WAIT_S", 60.0)  # a slow machine still waits for the pages
    splash, revealed = start(window)
    window.open_file(sample_pdf)  # as the documents open behind the splash
    assert wait_for(lambda: revealed)
    assert revealed == [0]  # no page left to draw when it came into sight
    assert window.windowOpacity() == 1.0 and not splash.isVisible()


def test_a_dialog_at_start_brings_the_window_into_sight_at_once(window):
    splash, revealed = start(window)
    dialog = QDialog(window)
    dialog.show()  # a password to type, a file that won't open
    assert revealed and window.windowOpacity() == 1.0 and not splash.isVisible()
    dialog.close()


def test_the_settings_read_at_start_are_the_apps_own(qt_app, monkeypatch):
    """Read before the app had its name, the settings were an empty store: nothing that was open came back."""
    names = []

    def settings():
        names.append((qt_app.organizationName(), qt_app.applicationName()))
        return SimpleNamespace(theme="dark")

    def stop(theme_name):
        raise SystemExit(0)

    before = qt_app.organizationName(), qt_app.applicationName()
    qt_app.setOrganizationName("")
    qt_app.setApplicationName("")
    monkeypatch.setattr(app, "forward", lambda targets: False)
    monkeypatch.setattr(app, "Settings", settings)  # never the user's own
    monkeypatch.setattr(app, "show_splash", stop)
    try:
        with pytest.raises(SystemExit):
            app.main(["zenith-pdf"])
    finally:
        qt_app.setOrganizationName(before[0])
        qt_app.setApplicationName(before[1])
    assert names == [("Zenith PDF", "Zenith PDF")]
