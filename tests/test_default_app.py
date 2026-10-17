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


@pytest.fixture
def question(monkeypatch):
    """The start-up question with the system left out: PDF files open elsewhere, the installer asked nothing."""
    monkeypatch.setattr(default_app, "can_set", lambda: True)
    monkeypatch.setattr(default_app, "is_default", lambda: False)
    monkeypatch.setattr(default_app, "installer_choice", lambda: None)
    answers, asked = [], []

    def run(dialog):
        asked.append(dialog.windowTitle())
        dialog.deleteLater()
        return answers.pop(0)

    monkeypatch.setattr(messages, "run", run)
    return answers, asked


def test_later_asks_again_some_starts_on_and_three_times_at_most(window, monkeypatch, question):
    from zenith_pdf.ui import main_window

    answers, asked = question
    made = []
    monkeypatch.setattr(window, "make_default_app", lambda: made.append(1))
    answers += [(messages.CANCEL, False)] * 3
    assert window.offer_default_app() and asked == ["Open PDF files with Zenith PDF?"]
    for _ in range(main_window.DEFAULT_APP_AGAIN - 1):
        assert not window.offer_default_app()  # not every start
    assert window.offer_default_app() and len(asked) == 2
    window.settings.set("starts", window.settings.get("default_app_next"))
    assert window.offer_default_app() and len(asked) == 3
    assert window.settings.get("default_app_asked")  # three times, then no more
    window.settings.set("starts", 10_000)
    assert not window.offer_default_app() and not made


@pytest.mark.parametrize("answer, made_it", [(("make", False), True), ((messages.CANCEL, True), False)])
def test_yes_or_dont_ask_again_ends_the_questions(window, monkeypatch, question, answer, made_it):
    answers, asked = question
    made = []
    monkeypatch.setattr(window, "make_default_app", lambda: made.append(1))
    answers.append(answer)
    assert window.offer_default_app()
    assert bool(made) == made_it and window.settings.get("default_app_asked")
    window.settings.set("starts", 10_000)
    assert not window.offer_default_app() and len(asked) == 1


@pytest.mark.parametrize("wanted", [True, False])
def test_the_installers_answer_stands(window, monkeypatch, question, wanted):
    answers, asked = question
    made = []
    monkeypatch.setattr(default_app, "installer_choice", lambda: wanted)
    monkeypatch.setattr(window, "make_default_app", lambda: made.append(1))
    window.offer_default_app()
    assert not asked and bool(made) == wanted and window.settings.get("default_app_asked")


@pytest.mark.parametrize("already", [True, None])
def test_no_question_when_it_opens_them_or_cant_tell(window, monkeypatch, question, already):
    answers, asked = question
    monkeypatch.setattr(default_app, "is_default", lambda: already)
    assert not window.offer_default_app() and not asked


@pytest.fixture
def windows_list(monkeypatch):
    """Windows' list of apps, played: each pick in turn, and whether PDF files open here after it."""
    from zenith_pdf.ui import main_window

    monkeypatch.setattr(main_window.sys, "platform", "win32")
    monkeypatch.setattr(default_app, "register_for_user", lambda command: True)
    monkeypatch.setattr(default_app, "current_app_name", lambda: "Microsoft Edge")
    picks, after = [], []
    monkeypatch.setattr(default_app, "choose_in_windows", lambda handle: picks.pop(0))
    monkeypatch.setattr(default_app, "is_default", lambda: after.pop(0))
    said = []
    monkeypatch.setattr(messages, "inform", lambda parent, title, *args, **kwargs: said.append(title))
    return picks, after, said


def test_windows_list_then_a_check_and_a_hint_until_it_is_chosen(window, monkeypatch, windows_list):
    picks, after, said = windows_list
    hints = []
    monkeypatch.setattr(messages, "ask", lambda parent, title, text, *args, **kwargs: hints.append(text) or "again")
    picks += [default_app.CHOSEN, default_app.CHOSEN]
    after += [False, True]  # "Always" missed the first time
    assert window.make_default_app() == default_app.DONE
    assert len(hints) == 1 and "Microsoft Edge" in hints[0] and "Always" in hints[0]
    assert said == ["Zenith PDF opens your PDF files"]


def test_a_cancelled_list_is_left_alone_and_a_missing_one_falls_back(window, monkeypatch, windows_list):
    picks, after, said = windows_list
    pages = []
    monkeypatch.setattr(default_app, "make_default", lambda command: pages.append(1) or default_app.CONFIRM)
    picks.append(default_app.CANCELLED)
    assert window.make_default_app() == default_app.CANCELLED and not pages and not said
    picks.append(default_app.FAILED)
    assert window.make_default_app() == default_app.CONFIRM and pages == [1]  # Default apps settings instead


def test_settings_say_whether_it_opens_pdf_files(window, monkeypatch):
    state = {"default": False}
    monkeypatch.setattr(default_app, "can_set", lambda: True)
    monkeypatch.setattr(default_app, "is_default", lambda: state["default"])
    monkeypatch.setattr(window, "make_default_app", lambda: state.update(default=True))
    window.open_settings("files")
    QApplication.processEvents()
    button = window._settings_window._default_app
    assert button.text() == "Make it the default" and button.isEnabled()
    button.click()
    assert button.text() == "Zenith PDF opens them" and not button.isEnabled()


def test_a_failure_elsewhere_says_how_to_do_it_by_hand(window, monkeypatch):
    from zenith_pdf.ui import main_window

    warned = []
    monkeypatch.setattr(main_window.sys, "platform", "linux")
    monkeypatch.setattr(default_app, "make_default", lambda command: default_app.FAILED)
    monkeypatch.setattr(messages, "warn", lambda parent, title, text, *args, **kwargs: warned.append(text))
    assert window.make_default_app() == default_app.FAILED
    assert warned and "Open With" in warned[0]


def test_the_list_of_apps_has_a_pdf_to_be_about():
    sample = default_app.sample_pdf()
    assert sample.suffix == ".pdf" and sample.read_bytes().startswith(b"%PDF-")


def test_the_installer_records_its_answer_for_the_first_start():
    script = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "zenith-pdf.nsi").read_text("utf-8")
    assert '"DefaultAsked"' in script and '"DefaultPdf"' in script and "RegisteredApplications" in script
    assert "ms-settings" not in script  # Windows' list opens from the app, as the user, not from the installer
