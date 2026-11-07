"""Printing: the preview shows each sheet as the printer will print it, laid out by the same code."""

import pikepdf
from conftest import settle
from PySide6.QtCore import QSettings, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPdfWriter
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from test_integration import window  # noqa: F401  (the fixture)

from zenith_pdf.settings import Settings
from zenith_pdf.ui import print_dialog
from zenith_pdf.ui.print_dialog import PrintDialog, PrintOptions, cells, placement, print_pages, sheet_orientation


class NoPrinters:
    """The system's printers, never asked: the tests print to files."""

    @staticmethod
    def availablePrinterNames():
        return []

    @staticmethod
    def defaultPrinterName():
        return ""


def test_a_page_fits_the_paper_keeps_its_size_or_turns_to_fill_it():
    area = QSizeF(500, 700)
    wide, turned = placement((800, 400), area, "fit")
    assert not turned and (wide.width(), wide.height(), wide.top()) == (500, 250, 225)  # as wide as the paper
    own, _ = placement((300, 200), area, "actual")
    assert (own.left(), own.top(), own.width(), own.height()) == (100, 250, 300, 200)
    small, _ = placement((300, 200), area, "shrink")
    assert small.width() == 300  # shrunk only when too large
    half, _ = placement((300, 200), area, "custom", 50)
    assert half.width() == 150
    across, turned = placement((800, 400), area, "fit", turn=True)
    assert turned and (across.width(), across.height()) == (350, 700)  # turned a quarter: the paper's full height


def test_several_pages_share_a_sheet_along_its_long_side():
    assert [c.width() for c in cells(QSizeF(800, 500), 2)] == [396, 396]  # side by side across a landscape sheet
    assert len(cells(QSizeF(500, 800), 6)) == 6 and cells(QSizeF(500, 800), 6)[1].left() > 0
    assert sheet_orientation((595, 842), 2) == QPageLayout.Orientation.Landscape
    assert sheet_orientation((595, 842), 4) == QPageLayout.Orientation.Portrait
    options = PrintOptions(tuple(range(5)), per_sheet=2, reverse=True)
    assert options.sheets() == [(4,), (2, 3), (0, 1)]


def test_the_preview_follows_the_choices_and_remembers_them(window, tmp_path, monkeypatch):
    monkeypatch.setattr(print_dialog, "QPrinterInfo", NoPrinters)
    path = tmp_path / "mixed.pdf"
    pdf = pikepdf.new()
    for size in ((842, 595), (595, 842), (595, 842)):  # a landscape page, then two upright ones
        page = pdf.add_blank_page(page_size=size)
        page.Contents = pdf.make_stream(b"0 0 0 rg 0 0 300 300 re f")  # a black square, to see where it goes
    pdf.save(path)
    window.open_file(path)
    view = window.current_workspace().view
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    dialog = PrintDialog(printer, view, 1, window, settings)
    try:
        dialog.show()
        settle()
        assert dialog.options().pages == (0, 1, 2) and dialog.shown_sheet() == (0,)
        assert not dialog.button("ok").isEnabled()  # no printer here
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape  # auto: as the first page
        first = dialog.preview.grab().toImage()
        QTest.keyClick(dialog, Qt.Key.Key_PageDown)
        assert dialog.shown_sheet() == (1,) and dialog.preview.grab().toImage() != first
        dialog._page_box.setCurrentIndex(dialog._page_box.findData("range"))
        dialog._page_pick.setText("2-3")
        assert dialog.options().pages == (1, 2)
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Portrait
        dialog._per_sheet.setCurrentIndex(dialog._per_sheet.findData(2))
        assert dialog.options().sheets() == [(1, 2)] and dialog.shown_sheet() == (1, 2)
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape  # two upright pages across
        assert not dialog._scaling.isEnabled() and dialog._borders.isEnabled()
        dialog._page_pick.setText("9")  # nothing of the document: Print says what to write
        assert dialog.options().pages == () and dialog.shown_sheet() == ()
        dialog._page_box.setCurrentIndex(dialog._page_box.findData("all"))
        dialog._gray.setChecked(True)
        dialog._reverse.setChecked(True)
        assert dialog.options().gray and printer.colorMode() == QPrinter.ColorMode.GrayScale
        assert dialog.options().sheets() == [(2,), (0, 1)]
        dialog._remember()
        saved = settings.get("print_options")
        assert saved["per_sheet"] == 2 and saved["gray"] and saved["reverse"] and "pages" not in saved
    finally:
        dialog.reject()
        dialog.deleteLater()
    again = PrintDialog(printer, view, 0, window, settings)
    try:
        assert again.options().per_sheet == 2 and again.options().gray and again.options().reverse
    finally:
        again.reject()
        again.deleteLater()


def test_several_pages_print_on_one_sheet(window, tmp_path):
    path = tmp_path / "four.pdf"
    pdf = pikepdf.new()
    for _ in range(4):
        pdf.add_blank_page(page_size=(595, 842))
    pdf.save(path)
    window.open_file(path)
    output = tmp_path / "printed.pdf"
    printer = QPdfWriter(str(output))  # a file, never a printer
    print_pages(window, printer, window.current_workspace().view, PrintOptions((0, 1, 2, 3), per_sheet=4,
                                                                               borders=True))
    assert len(pikepdf.open(output).pages) == 1
