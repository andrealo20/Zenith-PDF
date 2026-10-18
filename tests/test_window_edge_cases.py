"""The main window at its edges: one file in two windows, closing tabs from Home, dialogs in another language,
printing a poster page, and a second file for Compare that will not open."""

import shutil

import pikepdf
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPdfWriter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QTabBar

from zenith_pdf.app import create_app
from zenith_pdf.core.i18n import set_language, tr
from zenith_pdf.settings import Settings
from zenith_pdf.ui import main_window, messages
from zenith_pdf.ui.main_window import MainWindow


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for other in [w for w in MainWindow.open_windows if w is not window]:
        for index in reversed(range(len(other.workspaces))):
            other.close_tab(index, confirm=False)
        other.close()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()
    settle()


def copies(sample_pdf, tmp_path, count):
    paths = []
    for number in range(count):
        path = tmp_path / f"doc{number}.pdf"
        shutil.copy(sample_pdf, path)
        paths.append(path)
    return paths


def test_a_file_open_in_one_window_is_shown_there_when_another_opens_it(window, sample_pdf, tmp_path):
    (path,) = copies(sample_pdf, tmp_path, 1)
    first = window.open_file(path)
    other = window.new_window()
    settle()
    assert other.open_file(path) is first
    # A double click in the file manager goes to the newest window, as app.py sends it.
    MainWindow.open_windows[-1].open_targets([str(path)])
    settle()
    assert other.workspaces == [] and window.workspaces == [first]
    assert window.current_workspace() is first


def test_closing_a_tab_from_home_stays_on_home(window, sample_pdf, tmp_path):
    for path in copies(sample_pdf, tmp_path, 3):
        window.open_file(path)
    settle()
    window.show_home()
    window.tabs.tabButton(0, QTabBar.ButtonPosition.RightSide).click()
    settle()
    assert window.stack.currentWidget() is window.home and window.home_button.isChecked()
    window._close_others(window.workspaces[0])
    settle()
    assert window.tabs.count() == 1
    assert window.stack.currentWidget() is window.home and window.home_button.isChecked()


def test_file_dialogs_speak_the_language_in_use(window, sample_pdf, tmp_path, monkeypatch):
    (path,) = copies(sample_pdf, tmp_path, 1)
    window.open_file(path)
    shown = []
    older = [str(path)]  # Compare gets its older version; every other dialog is cancelled

    def files(parent, caption, folder="", filters=""):
        shown.append((caption, filters))
        return [], ""

    def file(parent, caption, folder="", filters=""):
        shown.append((caption, filters))
        return (older.pop() if older and caption == tr("Compare with which version?") else ""), ""

    def folder(parent, caption, start=""):
        shown.append((caption, ""))
        return ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(files))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(file))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(file))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(folder))
    set_language("it")
    try:
        window.open_dialog()
        window.pin_folder()
        window.save_current(save_as=True)
        window.show_home()  # no document shown: Compare asks for both versions
        window.command_compare()
        english = ["Open", "Pin a folder", "Save as", "Compare with which version?", "And the newer version?"]
        assert [caption for caption, _ in shown] == [tr(text) for text in english]
        assert all(tr(text) != text for text in english + ["PDF documents (*.pdf)"])
        assert [filters for _, filters in shown[2:]] == [tr("PDF documents (*.pdf)")] * 3
    finally:
        set_language("en")


def test_printing_a_poster_page_renders_for_the_paper_and_reports_a_failure(window, tmp_path, monkeypatch):
    path = tmp_path / "poster.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(14400, 14400))  # 200 x 200 inches
    pdf.save(path)
    output = tmp_path / "printed.pdf"

    class FilePrinter(QPdfWriter):
        """The printer, as print_current uses it: pages go to a PDF file, and the system's printers are never asked."""
        PrinterMode = QPrinter.PrinterMode

        def __init__(self, mode):
            super().__init__(str(output))
            self.setResolution(1200)

        def setDocName(self, name):
            pass

        def setFromTo(self, first, last):
            pass

        def fromPage(self):
            return 0  # no range picked: every page

        def toPage(self):
            return 0

    class Accept:
        DialogCode = QPrintDialog.DialogCode
        PrintDialogOption = QPrintDialog.PrintDialogOption

        def __init__(self, *args):
            pass

        def setOption(self, *args):
            pass

        def exec(self):
            return QPrintDialog.DialogCode.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_window, "QPrinter", FilePrinter)
    monkeypatch.setattr(main_window, "QPrintDialog", Accept)
    warned = []
    monkeypatch.setattr(messages, "warn", lambda parent, title, *args, **kwargs: warned.append(title))
    window.open_file(path)
    view = window.current_workspace().view
    render = view.page_image
    scales = []
    monkeypatch.setattr(view, "page_image", lambda index, scale: scales.append(scale) or render(index, scale))
    window.print_current()
    assert not warned and output.stat().st_size > 0
    # 300 dpi on paper about 8 inches wide; at 300 dpi of the page itself the bitmap would be 60000 pixels a side.
    assert len(scales) == 1 and scales[0] * 14400 <= 9 * 300

    def out_of_memory(index, scale):
        raise MemoryError("PDFium could not allocate a 60001x60001 bitmap")

    monkeypatch.setattr(view, "page_image", out_of_memory)
    window.print_current()  # the app goes on, with a message instead of aborting
    assert warned == ["Can't print"]


def test_compare_says_why_the_newer_file_does_not_open(window, tmp_path, monkeypatch):
    good, protected, broken = tmp_path / "good.pdf", tmp_path / "protected.pdf", tmp_path / "broken.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(good)
    pdf.save(protected, encryption=pikepdf.Encryption(user="u", owner="o", R=6))
    broken.write_bytes(good.read_bytes()[:200])
    picks = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args, **kwargs: (str(picks.pop(0)), "")))
    warned, asked = [], []
    monkeypatch.setattr(messages, "warn", lambda parent, title, *args, **kwargs: warned.append(title))
    monkeypatch.setattr(messages, "get_text", lambda parent, title, *args, **kwargs: asked.append(title))
    picks[:] = [good, broken]
    window.command_compare()
    assert warned == ["Can't open the file"]
    picks[:] = [good, protected]
    window.command_compare()  # its password is asked for; cancelling the prompt ends it quietly
    assert asked == ["Password required"] and warned == ["Can't open the file"]
