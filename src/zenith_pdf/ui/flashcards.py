"""Flashcards from the highlights of a document: pick the colours, look through the cards, study them here
or save them for Anki."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import flashcards
from ..core.flashcards import Card
from ..core.i18n import tr
from ..core.writing import colour_name
from . import messages
from .widgets import muted_label
from .writing_dialogs import marks_of


class FlashcardsDialog(messages.MessageDialog):
    """The cards of a document's marks, by colour: study them here, or save them for Anki."""

    def __init__(self, parent, marks, page_texts: dict[int, str], title: str):
        super().__init__(parent, "Flashcards", "", [("study", "Study here", "primary"), ("anki", "Save for Anki…", ""),
                                                    (messages.CANCEL, "Close", "")])
        self.setMinimumWidth(620)
        self.marks, self.page_texts, self.title = marks, page_texts, title
        intro = muted_label("A mark with a comment asks the comment and answers with the marked text. One without "
                            "hides the marked words in their sentence. Strikethroughs are left out.")
        intro.setWordWrap(True)
        self.add_field(intro)
        colours = sorted({colour_name(mark.color) for mark in marks if mark.style != "StrikeOut"})
        row = QHBoxLayout()
        row.setSpacing(12)
        self.colour_boxes: dict[str, QCheckBox] = {}
        for name in colours:
            box = QCheckBox(tr(name))
            box.setChecked(True)
            box.toggled.connect(self._refresh)
            self.colour_boxes[name] = box
            row.addWidget(box)
        row.addStretch()
        holder = QWidget()
        holder.setLayout(row)
        self.add_field(holder)
        self.preview = QListWidget()
        self.preview.setObjectName("CardList")
        self.preview.setMinimumHeight(280)
        self.preview.setWordWrap(True)
        self.add_field(self.preview)
        self.count = muted_label()
        self.add_field(self.count, full_width=False)
        self.cards: list[Card] = []
        self._refresh()

    def chosen_colours(self) -> set[str]:
        return {name for name, box in self.colour_boxes.items() if box.isChecked()}

    def _refresh(self, *_) -> None:
        self.cards = flashcards.make_cards(self.marks, self.page_texts, self.chosen_colours())
        self.preview.clear()
        for card in self.cards:
            self.preview.addItem(f"{card.front}\n→ {card.back}   ({tr(f'p. {card.page + 1}')})")
        self.count.setText(tr(f"{len(self.cards)} card{'s' if len(self.cards) != 1 else ''}"))
        for key in ("study", "anki"):
            button = self._buttons.get(key)
            if button is not None:
                button.setEnabled(bool(self.cards))


class StudyDialog(QDialog):
    """One card at a time: the question, then its answer; a card answered wrong comes back later."""

    def __init__(self, cards: list[Card], parent=None, shuffle: bool = True):
        super().__init__(parent)
        self.setObjectName("MessageDialog")
        self.setWindowTitle(tr("Study"))
        self.setMinimumSize(560, 360)
        self.queue = list(cards)
        if shuffle:
            random.shuffle(self.queue)
        self.total, self.right, self.tries = len(self.queue), 0, 0
        self._missed: set[Card] = set()  # answered wrong once: knowing them later doesn't count as first time
        self.progress = muted_label()
        self.front = QLabel()
        self.front.setObjectName("CardFront")
        self.front.setWordWrap(True)
        self.front.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.back = QLabel()
        self.back.setObjectName("CardBack")
        self.back.setWordWrap(True)
        self.back.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.show_button = QPushButton(tr("Show the answer"))
        self.show_button.setObjectName("Primary")
        self.show_button.clicked.connect(self.reveal)
        self.again = QPushButton(tr("Again"))
        self.again.clicked.connect(lambda: self.answer(False))
        self.knew = QPushButton(tr("I knew it"))
        self.knew.setObjectName("Primary")
        self.knew.clicked.connect(lambda: self.answer(True))
        buttons = QHBoxLayout()
        buttons.addWidget(self.progress)
        buttons.addStretch()
        for button in (self.show_button, self.again, self.knew):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addStretch()
        layout.addWidget(self.front)
        layout.addWidget(self.back)
        layout.addStretch()
        layout.addLayout(buttons)
        self._next()

    def _next(self) -> None:
        if not self.queue:
            self.front.setText(tr(f"Done: {self.right} of {self.total} at the first try."))
            self.back.hide()
            self.show_button.hide()
            self.again.hide()
            self.knew.setText(tr("Close"))
            self.knew.clicked.disconnect()
            self.knew.clicked.connect(self.accept)
            self.knew.show()
            self.progress.setText("")
            return
        card = self.queue[0]
        self.front.setText(card.front)
        self.back.setText(tr(f"{card.back}\n\np. {card.page + 1}"))
        self.back.hide()
        self.show_button.show()
        self.again.hide()
        self.knew.hide()
        self.show_button.setFocus()
        self.progress.setText(tr(f"{self.total - len(self.queue) + 1} of {self.total}"))

    def reveal(self) -> None:
        self.back.show()
        self.show_button.hide()
        self.again.show()
        self.knew.show()
        self.knew.setFocus()

    def answer(self, knew: bool) -> None:
        card = self.queue.pop(0)
        self.tries += 1
        if knew:
            self.right += card not in self._missed
        else:
            self._missed.add(card)
            self.queue.insert(min(len(self.queue), 3), card)  # back in a few cards
        self._next()


def flashcards_for(view) -> str | None:
    """The Flashcards dialog for the document in `view`; what was chosen, or None."""
    marks = marks_of(view)
    if not marks:
        view.message.emit("No highlights or underlines yet: mark some text first.")
        return None
    path = view.document.path
    pages = sorted({mark.page for mark in marks})
    page_texts = {page: view.document.page_text(page) for page in pages}
    dialog = FlashcardsDialog(view, marks, page_texts, path.stem)
    dialog.exec()
    choice, cards = dialog.choice, dialog.cards
    dialog.deleteLater()
    if choice == "study" and cards:
        study = StudyDialog(cards, view)
        study.exec()
        study.deleteLater()
    elif choice == "anki" and cards:
        target, _ = QFileDialog.getSaveFileName(view, "Save flashcards for Anki",
                                                str(path.with_name(f"{path.stem} flashcards.txt")),
                                                "Anki text (*.txt)")
        if target:
            Path(target).write_text(flashcards.anki_text(cards, f"Zenith PDF::{path.stem}", path.stem),
                                    encoding="utf-8")
            view.message.emit(f"Saved {len(cards)} cards. In Anki: File > Import, then choose {Path(target).name}.")
    return choice
