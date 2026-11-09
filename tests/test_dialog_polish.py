"""How the dialogs come in, where the keyboard's focus shows, and the pieces made for the settings, flashcards and tour."""

import gc

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QLineEdit, QPushButton, QWidget

from conftest import settle, wait_for
from test_print import NoPrinters
from zenith_pdf.app import create_app
from zenith_pdf.core.audio import Recorder
from zenith_pdf.core.flashcards import Card
from zenith_pdf.core.i18n import set_language
from zenith_pdf.core.releases import RELEASES
from zenith_pdf.core.writing import Marked
from zenith_pdf.settings import Settings
from zenith_pdf.ui import audio_dialog, messages, pdf_tools, print_dialog
from zenith_pdf.ui.audio_dialog import RecordDialog
from zenith_pdf.ui.flashcards import FlashcardsDialog, StudyDialog
from zenith_pdf.ui.insert_dialogs import SignatureDialog
from zenith_pdf.ui.messages import FocusRing, MessageDialog
from zenith_pdf.ui.print_dialog import PrintDialog
from zenith_pdf.ui.settings_page import AccentPicker, Row, Section
from zenith_pdf.ui.updates import CARD_WIDTH, UpdateCard, Updater
from zenith_pdf.ui.whats_new import WhatsNewDialog
from zenith_pdf.ui.widgets import Switch

BUTTONS = [("ok", "Flatten", "primary"), (messages.CANCEL, "Cancel", "")]


def shown(dialog: MessageDialog) -> MessageDialog:
    dialog.show()
    dialog.activateWindow()
    QTest.qWaitForWindowActive(dialog)
    return dialog


def ring_of(dialog) -> FocusRing:
    return dialog.findChild(FocusRing)


# Coming in ----------------------------------------------------------------------------------------------------------


def test_a_dialog_comes_in_and_lands_whole(qt_app, motion_on):
    dialog = MessageDialog(None, "Delete pages", "Delete 3 pages?", [("delete", "Delete", "danger")])
    dialog.show()
    assert dialog.windowOpacity() < 1.0  # it fades in as it is shown...
    assert wait_for(lambda: dialog.windowOpacity() == 1.0)  # ...and ends as any window does
    dialog.close()
    dialog.deleteLater()


def test_with_motion_off_a_dialog_is_there_at_once(qt_app):
    dialog = MessageDialog(None, "Delete pages", "Delete 3 pages?", [("delete", "Delete", "danger")])
    dialog.show()
    assert dialog.windowOpacity() == 1.0
    dialog.close()
    dialog.deleteLater()


def test_a_dialog_shown_again_centres_on_its_parent_again(qt_app, motion_on):
    parent = QWidget()
    parent.resize(500, 320)
    parent.show()
    dialog = MessageDialog(parent, "Delete pages", "Delete 3 pages?", [("delete", "Delete", "danger")])
    for corner, early in ((20, False), (260, False), (140, True), (20, False)):
        parent.move(corner, corner)  # it has moved since the dialog was last there; its entrance moved the dialog too
        dialog.show()
        if early:  # gone before its entrance is over: that must not go on moving it
            dialog.hide()
            settle(300)
            parent.move(corner + 60, corner + 40)
            dialog.show()
        assert wait_for(lambda: dialog.windowOpacity() == 1.0)
        assert (dialog.geometry().center() - parent.geometry().center()).manhattanLength() <= 2
        dialog.hide()
    dialog.deleteLater()
    parent.deleteLater()


# Focus --------------------------------------------------------------------------------------------------------------


def test_the_main_button_has_the_focus_even_beside_a_tick_box(qt_app):
    dialog = shown(MessageDialog(None, "Flatten", "Make them part of the page?", BUTTONS, check="Don't ask again"))
    assert dialog.focusWidget() is dialog.button("ok")  # the tick box comes first in the tab chain, not in Enter's
    assert not ring_of(dialog).isVisible()  # put there by the dialog, not by the keyboard
    dialog.close()
    dialog.deleteLater()


def test_the_ring_follows_the_keyboard_and_goes_with_a_click_elsewhere(qt_app):
    dialog = MessageDialog(None, "Rename", "Name:", BUTTONS, check="Don't ask again")
    field = QLineEdit("Lecture")
    dialog.add_field(field)
    shown(dialog)
    ring = ring_of(dialog)
    assert dialog.focusWidget() is field and not ring.isVisible()
    before = dialog.grab().toImage()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)  # on from the field, round to the tick box
    assert QApplication.focusWidget() is dialog.check_box and ring.isVisible() and dialog.grab().toImage() != before
    assert ring.width() <= dialog.check_box.sizeHint().width() + 3 * messages.RING_GAP  # as wide as its words, with air
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    target = QApplication.focusWidget()
    assert target is dialog.button("ok") and isinstance(target, QPushButton) and ring.isVisible()
    assert ring.geometry().contains(target.geometry()) and ring.width() == target.width() + 2 * messages.RING_GAP
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert not ring.isVisible()  # a click puts the focus back with the mouse, which draws no ring
    dialog.close()
    dialog.deleteLater()


# The settings -------------------------------------------------------------------------------------------------------


def test_captions_head_their_rows_and_a_search_takes_them_away(qt_app):
    section = Section("view", "View & Layout")
    section.caption("Appearance")
    first, second = section.add(Row("Alpha one", "x")), section.add(Row("Beta two", "y"))
    section.caption("Pages")
    third = section.add(Row("Gamma three", "z"))
    section.show()
    assert section.filter([])
    assert all(label.isVisibleTo(section) for label in section._captions)
    assert not first.after_caption and second.line.isVisibleTo(section)
    assert third.after_caption and not third.line.isVisibleTo(section)  # the caption stands where the line would
    assert section.filter(["gamma"])
    assert not any(label.isVisibleTo(section) for label in section._captions)  # a search lists rows, without groups
    assert not first.isVisibleTo(section) and third.isVisibleTo(section) and not third.line.isVisibleTo(section)
    assert section.filter(["alpha", "one"]) and not section.filter(["nothing", "like", "it"])
    section.deleteLater()


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 850)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    if window._settings_window is not None:
        window._settings_window.close()
    window.close()
    window.renderer.shutdown()


def test_reduce_motion_is_a_switch_in_view_and_layout(window):
    window.open_settings("view")
    dialog = window._settings_window
    row = next(r for r in dialog.sections["view"].rows if r.title == "Reduce motion")
    switch = row.findChild(Switch)
    assert not switch.isChecked() and not window.settings.get("reduce_motion")
    switch.click()
    assert window.settings.get("reduce_motion") is True
    switch.click()
    assert window.settings.get("reduce_motion") is False
    dialog.search.setText("animation")  # found by what people call it
    settle(50)
    assert row.isVisible() and dialog.sections["view"].isVisible()
    dialog.search.clear()
    dialog.close()


def test_the_accent_swatches_follow_the_setting(window):
    picker = AccentPicker(window.settings)
    window.settings.set("accent", "green")
    assert picker._buttons["green"].chosen and not picker._buttons["blue"].chosen
    window.settings.set("accent", "blue")
    assert picker._buttons["blue"].chosen and not picker._buttons["green"].chosen
    picker.deleteLater()


# The PDF tools ------------------------------------------------------------------------------------------------------


def test_a_tool_tile_takes_the_keyboard(qt_app):
    tool = pdf_tools.TOOLS[0]
    asked = []
    tile = pdf_tools.ToolTile(tool, True, asked.append)
    off = pdf_tools.ToolTile(tool, False, asked.append)
    for widget in (tile, off):
        widget.show()
    assert tile.focusPolicy() == Qt.FocusPolicy.StrongFocus and off.focusPolicy() == Qt.FocusPolicy.NoFocus
    QTest.keyClick(tile, Qt.Key.Key_Return)
    QTest.keyClick(tile, Qt.Key.Key_Space)
    QTest.keyClick(off, Qt.Key.Key_Return)
    assert asked == [tool, tool]  # the tile that cannot be used says nothing
    tile.deleteLater()
    off.deleteLater()


def test_an_empty_list_of_attachments_says_so_in_its_middle(qt_app):
    empty = pdf_tools._AttachmentList()
    empty.resize(300, 160)
    empty.show()
    picture = empty.grab().toImage()
    assert len({picture.pixel(x, y) for x in range(60, 240, 2) for y in range(40, 120)}) > 3  # the paperclip, the words
    empty.close()
    empty.deleteLater()


# Flashcards and the tour ---------------------------------------------------------------------------------------------

PAGE = ("A solar cell turns sunlight into electricity. The open-circuit voltage falls as the cell warms up. "
        "Batteries store the energy for the night.")


def test_the_cards_to_be_made_list_themselves_and_say_why_when_there_are_none(qt_app):
    marks = [Marked(0, "Highlight", (1.0, 0.85, 0.0), "sunlight into electricity", top=10),
             Marked(0, "Highlight", (0.2, 0.75, 0.3), "open-circuit voltage", "What falls as the cell warms?", top=20)]
    dialog = FlashcardsDialog(None, marks, {0: PAGE}, "Lecture")
    assert dialog.preview.count() == 2 and dialog.preview.item(0).data(Qt.ItemDataRole.UserRole) in dialog.cards
    for box in dialog.colour_boxes.values():
        box.setChecked(False)
    assert dialog.preview.count() == 0 and "colour" in dialog.preview.empty_text
    assert not dialog.button("study").isEnabled()
    dialog.show()
    assert not dialog.preview.grab().isNull()
    dialog.close()
    dialog.deleteLater()


def test_a_study_card_keeps_its_place_while_its_answer_comes(qt_app, motion_on):
    cards = [Card(f"Question {n}", f"Answer {n}", n, "Yellow") for n in range(2)]
    dialog = StudyDialog(cards, shuffle=False)
    dialog.show()
    assert wait_for(lambda: dialog.windowOpacity() == 1.0 and dialog.face.graphicsEffect() is None)
    place = dialog.front.mapTo(dialog, QPoint(0, 0))
    assert not dialog.solution.isVisibleTo(dialog) and dialog.where.text() == "p. 1" and dialog.bar.value() == 0
    dialog.reveal()
    assert dialog.back.text() == "Answer 0" and dialog.solution.graphicsEffect() is not None  # it comes in
    assert wait_for(lambda: dialog.solution.graphicsEffect() is None)
    assert dialog.front.mapTo(dialog, QPoint(0, 0)) == place  # and the question has not moved for it
    dialog.answer(True)
    assert dialog.bar.value() == 1 and dialog.front.text() == "Question 1" and dialog.face.graphicsEffect() is not None
    dialog.reveal()
    dialog.answer(True)
    assert dialog.bar.value() == 2 and dialog.mark.isVisibleTo(dialog) and "2 of 2" in dialog.front.text()
    dialog.close()
    dialog.deleteLater()


def test_the_tour_slides_from_card_to_card(qt_app, motion_on):
    dialog = WhatsNewDialog(list(RELEASES[:2]))
    dialog.show()
    assert wait_for(lambda: dialog.windowOpacity() == 1.0)
    dialog.go(1)
    page = dialog.pages.currentWidget()
    assert page.graphicsEffect() is not None
    assert wait_for(lambda: page.graphicsEffect() is None) and dialog.back.isVisibleTo(dialog)
    dialog.close()
    dialog.deleteLater()


# The update card, and texts longer than English ------------------------------------------------------------------


def test_the_update_card_keeps_room_for_its_buttons_as_its_state_changes(window):
    updater = Updater(window.settings, window, fetch=lambda: None)
    card = UpdateCard(updater, window.centralWidget())
    card._set("Zenith PDF 9.0.0 is out", "Notes.", [("Later", card.hide, False), ("Update", card.hide, True)])
    card._set("Downloading the update…", "", [("Cancel", card.hide, False)], progress=True)  # on screen already
    settle(60)  # the layout would have shown the new button only now: the card must not be sized without it
    assert card.height() >= card.layout().minimumSize().height()
    assert all(button.height() >= button.sizeHint().height() for button in card.findChildren(QPushButton))
    card.deleteLater()


def test_the_print_rows_that_fade_in_stay_held_while_they_do(window, sample_pdf, monkeypatch, motion_on):
    monkeypatch.setattr(print_dialog, "QPrinterInfo", NoPrinters)
    window.open_file(sample_pdf)
    view = window.current_workspace().view
    dialog = PrintDialog(QPrinter(QPrinter.PrinterMode.HighResolution), view, 1, window, window.settings)
    dialog.show()
    dialog._show_more(True)
    gc.collect()  # the form made these labels itself: nothing but the dialog holds the wrappers the fades paint with
    items = [dialog._form.itemAt(row, QFormLayout.ItemRole.LabelRole) for row in dialog._more_rows]
    veils = [item.widget().graphicsEffect() for item in items if item is not None and item.widget() is not None]
    assert len(veils) >= 4 and all(hasattr(veil, "_reach") for veil in veils)  # whole, not emptied by the collector
    dialog.reject()
    dialog.deleteLater()


class Mic:
    def open(self, rate, channels, bits):
        pass

    def read(self):
        return [b"\x00\x10" * 1102]

    def close(self):
        return []


def squeezed(dialog) -> list[str]:
    """The buttons and one-line labels the layout gave less room than their words need."""
    found = []
    for widget in dialog.findChildren(QWidget):
        words = isinstance(widget, QPushButton) or (isinstance(widget, QLabel) and not widget.wordWrap())
        if words and widget.text() and widget.isVisibleTo(dialog) and widget.width() < widget.sizeHint().width():
            found.append(widget.text())
    return found


def test_the_record_dialog_is_as_wide_as_its_buttons_need_in_a_longer_language(qt_app):
    english = RecordDialog(None, recorder=Recorder(backend=Mic()))
    assert english.width() == audio_dialog.DIALOG_WIDTH  # English fits the width it always had
    english.deleteLater()
    set_language("de")
    try:
        dialog = RecordDialog(None, recorder=Recorder(backend=Mic()))
        dialog.show()
        settle(30)
        assert dialog.width() > audio_dialog.DIALOG_WIDTH and not squeezed(dialog)
        dialog._set_state("recorded")  # Record again, Add note, Cancel: the other row
        settle(30)
        assert not squeezed(dialog)
        dialog.close()
        dialog.deleteLater()
    finally:
        set_language("en")


def test_the_update_card_is_as_wide_as_its_buttons_need_in_a_longer_language(window):
    set_language("de")
    try:
        card = UpdateCard(Updater(window.settings, window, fetch=lambda: None), window.centralWidget())
        card._set("Zenith PDF 9.0.0 is out", "Notes.", [("Skip this version", card.hide, False),
                                                        ("Later", card.hide, False), ("Update", card.hide, True)])
        settle(30)
        assert card.width() > CARD_WIDTH and not squeezed(card)  # "Diese Version überspringen" was cut short
        card._set("The update didn't work", "No network.", [("Close", card.hide, False)])
        settle(30)
        assert card.width() == CARD_WIDTH  # one button needs no more than it ever had
        card.deleteLater()
    finally:
        set_language("en")


def test_the_signature_labels_hold_their_words_in_a_longer_language(qt_app):
    set_language("de")
    try:
        dialog = SignatureDialog("Andrea Loroni")
        dialog.show()
        settle(30)
        assert not squeezed(dialog)  # "Speichern unter" was cut to "Speicher": its width was measured in English
        dialog.close()
        dialog.deleteLater()
    finally:
        set_language("en")


def test_the_tour_opens_tall_enough_for_its_wrapped_text_in_a_longer_language(qt_app):
    set_language("fr")
    try:
        dialog = WhatsNewDialog(list(RELEASES[:2]))
        dialog.show()
        settle(30)
        wrapped = [label for label in dialog.findChildren(QLabel) if label.wordWrap() and label.isVisibleTo(dialog)]
        assert wrapped and all(label.height() >= label.heightForWidth(label.width()) for label in wrapped)
        dialog.close()
        dialog.deleteLater()
    finally:
        set_language("en")
