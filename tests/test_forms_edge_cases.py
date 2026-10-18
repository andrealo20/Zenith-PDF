"""Forms and dialogs in another language: paper sizes, row labels, the tools' search, file dialogs, settings rows."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QFormLayout, QLabel, QWidget

from zenith_pdf.app import create_app
from zenith_pdf.core.i18n import set_language, tr
from zenith_pdf.settings import Settings
from zenith_pdf.ui import pdf_tools
from zenith_pdf.ui.dialogs import NewDocumentDialog, WatermarkDialog, page_size
from zenith_pdf.ui.pdf_tools import TOOLS, ToolForm, ToolTile
from zenith_pdf.ui.settings_page import SettingsWindow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def language():
    """Switch to a language for one test, and back to English after it."""
    yield set_language
    set_language("en")


def _labels(form: QFormLayout) -> list[str]:
    found = []
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        if item is not None and isinstance(item.widget(), QLabel) and item.widget().text():
            found.append(item.widget().text())
    return found


def test_new_document_keeps_letter_when_its_name_is_translated(qt_app, language):
    language("fr")
    dialog = NewDocumentDialog()
    assert dialog.paper.itemText(dialog.paper.findData("Letter")) == tr("Letter") != "Letter"
    dialog.paper.setCurrentIndex(dialog.paper.findData("Letter"))
    assert dialog.spec()["paper"] == "Letter" and page_size(dialog.spec()) == (612.0, 792.0)
    dialog.deleteLater()
    editing = NewDocumentDialog({"name": "Notes", "paper": "Letter", "orientation": "landscape"}, editing_template=True)
    assert editing.paper.currentData() == "Letter"
    assert editing.spec()["paper"] == "Letter" and page_size(editing.spec()) == (792.0, 612.0)
    editing.deleteLater()


def test_form_rows_are_labelled_in_the_language_shown(qt_app, language):
    language("it")
    host = QWidget()
    form = ToolForm("Add page numbers", "", host)
    form.choose("position", "Position", {"a": "Bottom"})
    form.number("margin", "From the edge", 3, 60, 12)
    form.text("custom", "Or your own")
    form.color("color", "Colour", (0.2, 0.2, 0.2))
    form.pages("pages", 5, 0)
    assert _labels(form._form) == ["Posizione", "Dal bordo", "O il tuo", "Colore", "Pagine"]
    watermark = WatermarkDialog(host)
    assert _labels(watermark.form) == ["Testo", "Dimensione", "Angolo", "Opacità", "Colore"]
    new = NewDocumentDialog(parent=host)
    assert _labels(new.form) == ["Nome", "Carta", "Orientamento", "Pagine"]
    assert new.name_field.text() == "Senza titolo"
    host.deleteLater()


def test_tools_search_finds_tools_by_the_names_shown(qt_app, language):
    language("it")
    tiles = [ToolTile(tool, True, lambda tool: None) for tool in TOOLS]

    def found(query: str) -> list[str]:
        return [tile.tool.key for tile in tiles if tile.matches(query.lower().split())]

    assert tr("Crop pages").lower().startswith("ritaglia")
    assert found("ritaglia") == ["crop"]
    assert "compress" in found(tr("Compress"))
    assert found("crop") == ["crop"]  # the English still finds it
    for tile in tiles:
        tile.deleteLater()


def test_file_dialog_captions_and_filters_are_translated(qt_app, language, monkeypatch, tmp_path):
    language("it")
    asked = []

    def picker(name, answer):
        def ask(parent, caption="", folder="", filter="", *rest, **options):
            asked.append((caption, filter))
            return answer
        monkeypatch.setattr(QFileDialog, name, staticmethod(ask))

    picker("getOpenFileName", ("", ""))
    picker("getOpenFileNames", ([], ""))
    picker("getExistingDirectory", "")
    window = SimpleNamespace(settings=SimpleNamespace(last_directory=str(tmp_path)))
    pdf_tools.repair(window)
    pdf_tools.images_to_pdf(window)
    pdf_tools._folder(window, "Save the files in", tmp_path)
    SettingsWindow._choose_new_folder(SimpleNamespace(_settings=SimpleNamespace(new_document_folder=str(tmp_path))))
    assert asked == [("Ripara un PDF", "Documenti PDF (*.pdf)"),
                     ("Immagini per il nuovo PDF", "Immagini (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"),
                     ("Salva i file in", ""), ("Cartella per i nuovi documenti", "")]

    # tools/i18n.py wrap catches the captions of new file dialogs, and both texts of a progress dialog.
    spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    calls = ast.parse('QFileDialog.getSaveFileName(self, "Save as", folder, "PDF documents (*.pdf)")\n'
                      'QFileDialog.getExistingDirectory(self, tr("Pin a folder"), folder)\n'
                      'QProgressDialog("Printing…", "Cancel", 0, 3, self)\n')
    targets = [[node.value for node in tool._targets(line.value)] for line in calls.body]
    assert targets == [["Save as"], [], ["Printing…", "Cancel"]]


def test_long_setting_titles_wrap_instead_of_pushing_the_controls_out(qt_app, language, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("language", "it")  # the longest titles: "Apri il documento in una finestra tutta sua, ..."
    _app, window = create_app(["zenith-pdf"], Settings(store))
    try:
        window.open_settings("shortcuts")
        dialog = window._settings_window
        QTest.qWaitForWindowExposed(dialog)
        assert dialog.width() == 1000  # as it opens
        QTest.qWait(30)
        QApplication.processEvents()
        assert dialog.scroll.horizontalScrollBar().maximum() == 0  # every key chip in sight, no sideways scrolling
    finally:
        if window._settings_window is not None:
            window._settings_window.close()
        window.close()
        window.renderer.shutdown()
