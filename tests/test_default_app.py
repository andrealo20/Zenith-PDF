"""Zenith PDF as the app that opens PDF files: the Linux desktop entry, the Windows names, the question asked
the first time and the row in Settings. Nothing here touches the system's own choice."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core import default_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import messages


def test_linux_gets_a_desktop_entry_of_its_own_and_xdg_mime_is_told(tmp_path):
    ran = []
    answers = {"query": "zenith-pdf.desktop"}

    def run(command):
        ran.append(command)
        return answers["query"] if command[:3] == ["xdg-mime", "query", "default"] else ""

    command = ["/home/me/Apps/Zenith PDF.AppImage"]
    assert default_app._make_default_linux(command, run, tmp_path) == default_app.DONE
    entry = (tmp_path / "applications" / "zenith-pdf.desktop").read_text(encoding="utf-8")
    assert 'Exec="/home/me/Apps/Zenith PDF.AppImage" %U' in entry  # a path with a space, quoted
    assert "MimeType=application/pdf;x-scheme-handler/zenith-pdf;" in entry and "Icon=zenith-pdf" in entry
    assert (tmp_path / "icons" / "hicolor" / "256x256" / "apps" / "zenith-pdf.png").stat().st_size > 0
    assert ["xdg-mime", "default", "zenith-pdf.desktop", "application/pdf"] in ran
    answers["query"] = "org.gnome.Evince.desktop"  # the system kept another choice
    assert default_app._make_default_linux(command, run, tmp_path) == default_app.FAILED


def test_an_appimage_is_started_as_itself(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/me/Zenith-PDF.AppImage")
    assert default_app.linux_command(["/tmp/.mount_x/Zenith-PDF"]) == ["/home/me/Zenith-PDF.AppImage"]
    monkeypatch.delenv("APPIMAGE")
    assert default_app.linux_command(["python", "-m", "zenith_pdf"]) == ["python", "-m", "zenith_pdf"]


def test_the_windows_names():
    assert default_app.is_ours("ZenithPDF.Document") and default_app.is_ours(r"Applications\Zenith-PDF.exe")
    assert default_app.is_ours("ZenithPDF.Source")
    assert not default_app.is_ours("AcroExch.Document.DC") and not default_app.is_ours("")
    assert default_app.windows_page(True) == "ms-settings:defaultapps?registeredAppMachine=ZenithPDF"
    assert default_app.windows_page(False, "ZenithPDFSource").endswith("registeredAppUser=ZenithPDFSource")


def test_windows_lists_it_by_name_and_logo_and_a_source_copy_apart():
    source, installed = default_app.Identity(source=True), default_app.Identity(source=False)
    assert source.prog_id != installed.prog_id and source.registered != installed.registered
    assert installed.name == "Zenith PDF" and source.name == "Zenith PDF (source)"
    icon = str(default_app.ICON)
    entries = default_app.registry_entries([r"C:\venv\Scripts\pythonw.exe", "-m", "zenith_pdf"], source, icon)
    classes = rf"Software\Classes\{source.prog_id}"
    assert entries[classes + r"\DefaultIcon"][""] == icon and icon.endswith("zenith.ico")  # not Python's logo
    assert entries[classes + r"\shell\open"]["FriendlyAppName"] == "Zenith PDF (source)"
    assert entries[classes + r"\Application"]["ApplicationName"] == "Zenith PDF (source)"
    assert entries[classes + r"\shell\open\command"][""] == r'"C:\venv\Scripts\pythonw.exe" "-m" "zenith_pdf" "%1"'
    assert source.prog_id in entries[r"Software\Classes\.pdf\OpenWithProgids"]  # among the suggested apps
    assert entries[r"Software\RegisteredApplications"] == {source.registered: source.capabilities}
    assert default_app.ICON.exists()


@pytest.fixture
def window(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    yield window
    if window._settings_window is not None:
        window._settings_window.close()
    window.close()
    window.renderer.shutdown()


def test_the_first_start_asks_once(window, monkeypatch):
    asked, made = [], []
    monkeypatch.setattr(default_app, "can_set", lambda: True)
    monkeypatch.setattr(default_app, "is_default", lambda: False)
    monkeypatch.setattr(default_app, "installer_asked", lambda: False)
    monkeypatch.setattr(default_app, "make_default", lambda command: made.append(command) or default_app.DONE)
    monkeypatch.setattr(messages, "ask", lambda *args, **kwargs: asked.append(args[1]) or "make")
    assert window.offer_default_app()
    assert asked == ["Open PDF files with Zenith PDF?"] and len(made) == 1
    assert window.settings.get("default_app_asked")
    assert not window.offer_default_app() and len(asked) == 1  # once only


@pytest.mark.parametrize("already, by_installer", [(True, False), (None, False), (False, True)])
def test_no_question_when_it_already_opens_them_or_the_installer_asked(window, monkeypatch, already, by_installer):
    monkeypatch.setattr(default_app, "can_set", lambda: True)
    monkeypatch.setattr(default_app, "is_default", lambda: already)
    monkeypatch.setattr(default_app, "installer_asked", lambda: by_installer)
    monkeypatch.setattr(messages, "ask", lambda *args, **kwargs: pytest.fail("asked"))
    assert not window.offer_default_app()


def test_settings_say_whether_it_opens_pdf_files(window, monkeypatch):
    state = {"default": False}
    made = []
    monkeypatch.setattr(default_app, "can_set", lambda: True)
    monkeypatch.setattr(default_app, "is_default", lambda: state["default"])

    def make(command):
        made.append(command)
        state["default"] = True
        return default_app.DONE

    monkeypatch.setattr(default_app, "make_default", make)
    window.open_settings("files")
    QApplication.processEvents()
    button = window._settings_window._default_app
    assert button.text() == "Make it the default" and button.isEnabled()
    button.click()
    assert made and button.text() == "Zenith PDF opens them" and not button.isEnabled()


def test_a_failure_says_how_to_do_it_by_hand(window, monkeypatch):
    warned = []
    monkeypatch.setattr(default_app, "make_default", lambda command: default_app.FAILED)
    monkeypatch.setattr(messages, "warn", lambda parent, title, text, *args, **kwargs: warned.append(text))
    assert window.make_default_app() == default_app.FAILED
    assert warned and ("Open With" in warned[0] or "Get Info" in warned[0])


def test_the_installer_records_the_question(tmp_path):
    script = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "zenith-pdf.nsi").read_text("utf-8")
    assert '"DefaultAsked"' in script and "RegisteredApplications" in script and "${DEFAULT_APPS}" in script
