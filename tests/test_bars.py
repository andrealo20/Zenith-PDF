"""The zoom bar folded to the zoom alone, and the assistant's button as a long field to type in."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import settle
from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import main_window


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1400, 900)
    window.show()
    yield window
    window.close()
    window.renderer.shutdown()


def shown(bar):
    return [w for w in bar._widgets if w.isVisible()]


def test_the_zoom_bar_folds_to_the_zoom_alone_in_every_tab(window, sample_pdf, tmp_path):
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(sample_pdf.read_bytes())
    window.open_file(sample_pdf)
    first = window.current_workspace().zoom_bar
    window.open_file(copy)
    second = window.current_workspace().zoom_bar
    settle()
    everything = shown(second)
    wide = second.width()

    second.compact_button.click()
    settle()
    assert set(shown(second)) == second._zoom_only  # the zoom, and the arrow to open the bar again
    assert second.width() < wide
    assert window.settings.get("zoom_bar_compact") is True  # kept for next time
    assert first.compact  # the other tab's bar folded too
    second.compact_button.click()
    settle()
    assert shown(second) == everything and not window.settings.get("zoom_bar_compact")


def test_the_assistant_button_is_a_long_field_that_takes_typing_at_once(window):
    button = window.assistant_button
    window.resize(main_window.WIDE_ASSISTANT + 100, 800)
    settle()
    assert button.wide and button.width() == button.WIDE
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    settle()
    assert QApplication.focusWidget() is window._assistant.search
    QTest.keyClicks(QApplication.focusWidget(), "zoom")
    assert window._assistant.search.text() == "zoom"
    window._assistant.close()
    window.resize(main_window.WIDE_ASSISTANT - 200, 800)
    settle()
    assert not button.wide and button.width() < button.WIDE  # a narrow window: the sparkle alone
