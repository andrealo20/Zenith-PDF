"""Filling in PDF forms: reading the fields, setting them (undoably), and clicking them on the page."""

import shutil

import pikepdf
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from zenith_pdf.app import create_app
from zenith_pdf.core import forms
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui.form_layer import FormLayer


def values(document) -> dict:
    return {field.name: field.value for field in document.form_fields()}


def test_the_fields_and_their_boxes(form_pdf):
    with pikepdf.open(form_pdf) as pdf:
        fields = {f.name: f for f in forms.read_fields(pdf)}
    assert list(fields) == ["name", "notes", "agree", "size", "colour"]
    assert fields["notes"].multiline and not fields["name"].multiline
    assert fields["agree"].kind == "checkbox" and fields["agree"].boxes[0].state == "Yes"
    assert fields["size"].kind == "radio" and [b.state for b in fields["size"].boxes] == ["S", "L"]
    assert fields["colour"].options == ("Red", "Green", "Blue") and fields["colour"].value == "Red"
    assert fields["name"].boxes[0].rect == (100.0, 690.0, 300.0, 710.0)
    with pikepdf.new() as blank:
        assert forms.read_fields(blank) == []


def test_filling_in_is_an_edit_of_the_document(tmp_path, form_pdf):
    path = tmp_path / "form.pdf"
    shutil.copy(form_pdf, path)
    document = Document(path)
    document.fill_fields([], {"name": "Andrea", "agree": True, "size": "L", "colour": "Blue", "ghost": "x"})
    assert values(document) == {"name": "Andrea", "notes": "", "agree": True, "size": "L", "colour": "Blue"}
    picture = document.render(0, 1.0)
    dark = sum(picture.pixelColor(x, 792 - 700).lightness() < 128 for x in range(100, 300))
    assert dark > 3  # PDFium draws the name typed in
    document.save([])
    document.close()
    again = Document(path)
    assert values(again)["name"] == "Andrea" and values(again)["size"] == "L"  # in the file, for every viewer
    again.close()


@pytest.fixture
def window(qt_app, tmp_path):
    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 1000)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def click_field(view, name, which=0):
    item = [i for i in view.forms.items if i.field.name == name][which]
    centre = item.mapToScene(item.rect().center())
    view.ensureVisible(item.mapRectToScene(item.rect()), 40, 40)
    QApplication.processEvents()
    at = view.mapFromScene(centre)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    QApplication.processEvents()


def test_clicking_the_fields_fills_them_in(window, form_pdf, tmp_path, monkeypatch):
    path = tmp_path / "form.pdf"
    shutil.copy(form_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    view = workspace.view
    QTest.qWait(50)
    workspace.tool_settings.set_tool("select")
    assert len(view.forms.items) == 6  # two radio buttons
    click_field(view, "agree")
    assert values(workspace.document)["agree"] is True
    click_field(view, "size", 1)
    assert values(workspace.document)["size"] == "L"
    monkeypatch.setattr(FormLayer, "run_menu",
                        staticmethod(lambda menu, where: next(x for x in menu.actions() if x.text() == "Green")))
    click_field(view, "colour")
    assert values(workspace.document)["colour"] == "Green"

    click_field(view, "name")
    editor = view.forms._editing[2]
    assert isinstance(editor, QLineEdit)
    editor.setText("Ada Lovelace")
    QTest.keyClick(editor, Qt.Key.Key_Tab)  # kept, and on to the next text field
    assert values(workspace.document)["name"] == "Ada Lovelace"
    notes = view.forms._editing[2]
    assert isinstance(notes, QPlainTextEdit)
    notes.setPlainText("Two\nlines")
    QTest.keyClick(notes, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert values(workspace.document)["notes"] == "Two\nlines" and not view.forms.editing()

    click_field(view, "name")
    view.forms._editing[2].setText("Not this")
    QTest.keyClick(view.forms._editing[2], Qt.Key.Key_Escape)
    assert values(workspace.document)["name"] == "Ada Lovelace"  # Esc leaves it as it was

    workspace.undo_stack.undo()
    assert values(workspace.document)["notes"] == ""  # each value one step
    workspace.undo_stack.undo()
    assert values(workspace.document)["name"] == ""

    workspace.tool_settings.set_tool("pen")
    count = len(workspace.store.all())
    click_field(view, "agree")
    assert values(workspace.document)["agree"] is True  # the pen draws on fields; it doesn't flip them
    assert len(workspace.store.all()) >= count
