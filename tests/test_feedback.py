"""Report a problem: GitHub's page for a new issue, filled in; nothing is sent from the app itself."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QSettings

from zenith_pdf import __version__
from zenith_pdf.app import create_app
from zenith_pdf.core import feedback
from zenith_pdf.settings import Settings
from zenith_pdf.ui import main_window, messages


def test_the_issue_is_filled_in_with_what_happened_and_this_copy():
    about = feedback.details()
    assert about.startswith(f"Zenith PDF {__version__}") and str(Path.home()) not in about
    url = feedback.issue_url("The page is blank\nwhen I compare.", about)
    query = parse_qs(urlparse(url).query)
    assert url.startswith(feedback.NEW_ISSUE)
    assert query["title"] == ["The page is blank"]
    assert query["body"][0].startswith("The page is blank\nwhen I compare.") and about in query["body"][0]
    assert len(feedback.issue_url("x" * 50_000, about)) < 8000  # what every browser takes


def test_the_menu_opens_the_issue_in_the_browser(qt_app, tmp_path, monkeypatch):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    _app, window = create_app(["zenith-pdf"], settings)
    opened = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(messages, "get_multiline_text", lambda *args, **kwargs: "It crashed when printing.")
    report = next(a for a in window.menu_button.menu().actions() if a.text().startswith("Report a problem"))
    report.trigger()
    assert len(opened) == 1 and opened[0].startswith(feedback.NEW_ISSUE) and "printing" in opened[0]
    monkeypatch.setattr(messages, "get_multiline_text", lambda *args, **kwargs: None)  # cancelled
    report.trigger()
    assert len(opened) == 1
    window.close()
    window.renderer.shutdown()
