"""The app's own message and prompt dialogs, and replacing the pages with a finished background job."""

import shutil
from io import BytesIO

import pikepdf
import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
)

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import messages
from zenith_pdf.ui.messages import MessageDialog


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 800)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def answer(action, seen: list | None = None):
    """Run `action(dialog)` on the next message dialog once it shows; give up (Esc) after two seconds."""
    tries = [0]

    def poll():
        shown = [w for w in QApplication.topLevelWidgets() if isinstance(w, MessageDialog) and w.isVisible()]
        if shown:
            dialog = shown[-1]
            if seen is not None:
                seen.append(dialog)
            try:
                action(dialog)
            finally:
                if dialog.isVisible():  # the action failed or left it open: never hang the run
                    dialog.reject()
        elif tries[0] < 200:
            tries[0] += 1
            QTimer.singleShot(10, poll)

    QTimer.singleShot(0, poll)


def key(name):
    return lambda dialog: QTest.keyClick(QApplication.focusWidget() or dialog, name)


def test_ask_returns_the_button_key(window):
    buttons = [("save", "Save", "primary"), ("discard", "Discard", "danger"), ("cancel", "Cancel", "")]
    seen = []
    answer(lambda d: d.button("discard").click(), seen)
    assert messages.ask(window, "Unsaved changes", "Save the changes?", buttons) == "discard"
    dialog = seen[0]
    assert dialog.minimumWidth() >= 440
    assert dialog.button("save").objectName() == "Primary" and dialog.button("save").isDefault()
    assert dialog.button("discard").objectName() == "DangerText"  # red text beside the accent button
    assert dialog.findChild(QLabel, "DialogTitle").text() == "Unsaved changes"

    answer(lambda d: d.button("cancel").click())
    assert messages.ask(window, "Unsaved changes", "Save?", buttons) is None  # the cancel key reads as None

    answer(key(Qt.Key.Key_Escape))
    assert messages.ask(window, "Unsaved changes", "Save?", buttons) is None

    answer(key(Qt.Key.Key_Return))
    assert messages.ask(window, "Unsaved changes", "Save?", buttons) == "save"  # Enter is the main button


def test_the_dialog_looks_like_the_app(window):
    seen = []
    answer(lambda d: d.reject(), seen)
    messages.ask(window, "Delete pages", "Delete 3 pages?", [("delete", "Delete", "danger"), ("cancel", "Cancel", "")],
                 icon="trash", detail="Undo brings them back.")
    dialog = seen[0]
    names = {label.objectName() for label in dialog.findChildren(QLabel)}
    assert {"DialogTitle", "DialogBody", "DialogDetail", "DialogIcon"} <= names
    assert dialog.button("delete").objectName() == "Danger" and dialog.button("delete").isDefault()
    margins = dialog.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (24, 20, 24, 20)
    heights = {dialog.button(k).sizeHint().height() for k in ("delete", "cancel")}
    assert len(heights) == 1  # the red button is as tall as the plain one
    picture = dialog.grab()
    assert not picture.isNull() and picture.width() >= 440


def test_labels_keep_their_ampersands(window):
    seen = []
    answer(lambda d: d.reject(), seen)
    messages.ask(window, "Merge", "Merge?", [("ok", "Save & close", "primary")])
    assert seen[0].button("ok").text() == "Save && close"


def test_ask_once_remembers_the_main_answer(window):
    class Asked:
        def __init__(self):
            self.stopped = set()

        def asked(self, question):
            return question in self.stopped

        def stop_asking(self, question):
            self.stopped.add(question)

    settings = Asked()

    def tick_and_cancel(dialog):
        dialog.check_box.setChecked(True)
        dialog.button("cancel").click()

    answer(tick_and_cancel)
    assert not messages.confirm_once(window, settings, "flatten", "Flatten", "Flatten?", "Flatten")
    assert not settings.stopped  # "don't ask again" with Cancel would mean never doing it

    def tick_and_accept(dialog):
        dialog.check_box.setChecked(True)
        dialog.button("ok").click()

    answer(tick_and_accept)
    assert messages.confirm_once(window, settings, "flatten", "Flatten", "Flatten?", "Flatten")
    assert settings.stopped == {"flatten"}
    assert messages.confirm_once(window, settings, "flatten", "Flatten", "Flatten?", "Flatten")  # not asked again
    assert messages.confirm_once(window, None, "flatten", "Flatten", "Flatten?")  # no settings, no question


def test_prompts_return_the_value_or_none(window):
    def type_name(dialog):
        field = dialog.findChild(QLineEdit)
        assert not dialog.button("ok").isEnabled()  # required: nothing to add yet
        QTest.keyClicks(field, "Stamp")
        assert dialog.button("ok").isEnabled()
        QTest.keyClick(field, Qt.Key.Key_Return)

    answer(type_name)
    assert messages.get_text(window, "Add to library", "Name:", required=True, action="Add") == "Stamp"

    answer(key(Qt.Key.Key_Escape))
    assert messages.get_text(window, "Rename", "Name:", "Old") is None

    def password(dialog):
        field = dialog.findChild(QLineEdit)
        assert field.echoMode() == QLineEdit.EchoMode.Password
        field.setText("secret")
        dialog.button("ok").click()

    answer(password)
    assert messages.get_text(window, "Password required", "Password:", password=True) == "secret"

    def note(dialog):
        field = dialog.findChild(QPlainTextEdit)
        QTest.keyClicks(field, "one")
        QTest.keyClick(field, Qt.Key.Key_Return)  # a new line, not OK
        QTest.keyClicks(field, "two")
        assert dialog.isVisible()
        QTest.keyClick(field, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

    answer(note)
    assert messages.get_multiline_text(window, "New note", "Note text:") == "one\ntwo"

    def pick(dialog):
        dialog.findChild(QComboBox).setCurrentIndex(2)
        dialog.button("ok").click()

    answer(pick)
    assert messages.get_item(window, "Add tag", "Tag:", ["a", "b", "c"]) == "c"

    def number(dialog):
        box = dialog.findChild(QSpinBox)
        box.setValue(500)  # clamped to the range
        dialog.button("ok").click()

    answer(number)
    assert messages.get_int(window, "Go to page", "Page:", 3, 1, 12) == 12

    def decimal(dialog):
        dialog.findChild(QDoubleSpinBox).setValue(2.5)
        dialog.button("ok").click()

    answer(decimal)
    assert messages.get_double(window, "Width", "Points:", 1.0, 0.1, 10.0, decimals=1) == 2.5

    answer(lambda d: d.button("ok").click())
    assert messages.inform(window, "Done", "Saved.") is None
    answer(lambda d: d.button("ok").click())
    assert messages.warn(window, "Can't save", "Saving failed.", detail="disk full") is None


def test_replace_structure_is_one_undoable_step(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    try:
        assert workspace.busy is False
        before = workspace.document.snapshot(workspace.store.all())
        with pikepdf.open(BytesIO(before)) as pdf:  # what a background job would hand back
            del pdf.pages[1:]
            out = BytesIO()
            pdf.save(out)
        assert workspace.replace_structure("Compress", before, out.getvalue())
        assert workspace.document.page_count == 1
        assert workspace.page_counter.total.text().strip() == "of 1"
        assert workspace.undo_stack.undoText() == "Compress"
        workspace.undo_stack.undo()
        assert workspace.document.page_count == 4
        workspace.undo_stack.redo()
        assert workspace.document.page_count == 1

        answer(lambda d: d.button("ok").click())
        count = workspace.undo_stack.count()
        assert not workspace.replace_structure("Compress", workspace.document.data, b"not a pdf")
        assert workspace.document.page_count == 1 and workspace.undo_stack.count() == count
    finally:
        window.close_tab(window.workspaces.index(workspace), confirm=False)
