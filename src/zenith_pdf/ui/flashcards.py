"""Flashcards from the highlights of a document: pick the colours, look through the cards, study them here
or save them for Anki."""

from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core import flashcards
from ..core.flashcards import Card
from ..core.i18n import tr
from ..core.writing import colour_name
from ..icons import icon
from ..theme import outline, themes
from . import messages, motion
from .widgets import muted_label, separator
from .writing_dialogs import marks_of

ROW_PAD = 10  # px round a card in the preview
RISE = 6  # px a new card, or an answer, climbs as it comes


class _CardRows(QStyledItemDelegate):
    """A card of the preview: the question as it will be asked, the answer under it in the accent with the page at
    the end of its line, and a hairline before the next card. Each is as tall as its words need at the list's width."""

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self._view = view

    def _sizes(self, option, card: Card) -> tuple[int, int, int]:
        """The width of the words, and the height of the question and of the answer, wrapped to it."""
        width = self._view.viewport().width() - 2 * ROW_PAD
        flags = int(Qt.TextFlag.TextWordWrap)
        question = option.fontMetrics.boundingRect(0, 0, width, 10_000, flags, card.front).height()
        page = option.fontMetrics.horizontalAdvance(tr(f"p. {card.page + 1}")) + 12
        answer = option.fontMetrics.boundingRect(0, 0, width - page, 10_000, flags, f"→ {card.back}").height()
        return width, question, answer

    def sizeHint(self, option, index) -> QSize:
        card = index.data(Qt.ItemDataRole.UserRole)
        _, question, answer = self._sizes(option, card)
        return QSize(self._view.viewport().width(), 2 * ROW_PAD + question + 4 + answer + 1)

    def paint(self, painter, option, index) -> None:
        t = themes.current
        card = index.data(Qt.ItemDataRole.UserRole)
        width, question, answer = self._sizes(option, card)
        flags = int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        rect = option.rect
        left, top = rect.left() + ROW_PAD, rect.top() + ROW_PAD
        page = tr(f"p. {card.page + 1}")
        page_width = option.fontMetrics.horizontalAdvance(page)
        painter.save()
        painter.setFont(option.font)
        painter.setPen(QColor(t.text))
        painter.drawText(QRect(left, top, width, question), flags, card.front)
        painter.setPen(QColor(t.accent_ink))
        painter.drawText(QRect(left, top + question + 4, width - page_width - 12, answer), flags, f"→ {card.back}")
        painter.setPen(QColor(t.text_muted))
        painter.drawText(QRect(left + width - page_width, top + question + 4, page_width, option.fontMetrics.height()),
                         int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), page)
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawLine(rect.left() + ROW_PAD, rect.bottom(), rect.right() - ROW_PAD, rect.bottom())
        painter.restore()


class _CardList(QListWidget):
    """The cards to be made; with none, its middle says why."""

    def __init__(self):
        super().__init__()
        self.empty_text = ""
        self.setObjectName("CardList")
        self.setItemDelegate(_CardRows(self))
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setResizeMode(QListView.ResizeMode.Adjust)  # the words wrap again when the dialog is resized
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the rows are as wide as the list
        self.setMinimumHeight(280)
        self._restyle()
        themes.changed.connect(self._restyle)

    def _restyle(self, *_) -> None:
        t = themes.current
        self.setStyleSheet(f"QListWidget#CardList {{ background: {t.field}; border: 1px solid {t.border}; "
                           f"border-radius: {t.look.control}px; padding: 2px 0; }}")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() or not self.empty_text:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(themes.current.text_muted))
        painter.drawText(QRectF(self.viewport().rect()).adjusted(24, 0, -24, 0),
                         int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap), tr(self.empty_text))


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
        self.preview = _CardList()
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
            item = QListWidgetItem(f"{card.front}\n→ {card.back}   ({tr(f'p. {card.page + 1}')})")  # as it is read out
            item.setData(Qt.ItemDataRole.UserRole, card)
            self.preview.addItem(item)
        self.preview.empty_text = ("Tick a colour above to see its cards." if self.colour_boxes else
                                   "Only highlights and underlines make cards.")
        self.count.setText(tr(f"{len(self.cards)} card{'s' if len(self.cards) != 1 else ''}"))
        for key in ("study", "anki"):
            button = self._buttons.get(key)
            if button is not None:
                button.setEnabled(bool(self.cards))


class _Face(QFrame):
    """The face of a flashcard: a raised surface with the style's corners, holding the question and its answer."""

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t.border), 1))
        painter.setBrush(QColor(t.raised))
        painter.drawPath(outline(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), t.look.card, t.look))


class StudyDialog(QDialog):
    """One card at a time: the question, then its answer; a card answered wrong comes back later."""

    def __init__(self, cards: list[Card], parent=None, shuffle: bool = True):
        super().__init__(parent)
        self.setObjectName("MessageDialog")
        messages.house(self)
        self.setWindowTitle(tr("Study"))
        self.setMinimumSize(560, 400)
        self.queue = list(cards)
        if shuffle:
            random.shuffle(self.queue)
        self.total, self.right, self.tries = len(self.queue), 0, 0
        self._missed: set[Card] = set()  # answered wrong once: knowing them later doesn't count as first time
        self.progress = muted_label()
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, max(self.total, 1))
        self._restyle()
        themes.changed.connect(self._restyle)
        header = QVBoxLayout()
        header.setSpacing(6)
        header.addWidget(self.progress)
        header.addWidget(self.bar)

        self.mark = QLabel()  # the check that closes a session, there while the questions are not
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.front = QLabel()
        self.front.setObjectName("CardFront")
        self.front.setWordWrap(True)
        self.front.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.back = QLabel()
        self.back.setObjectName("CardBack")
        self.back.setWordWrap(True)
        self.back.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.where = muted_label()
        self.where.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rule = separator(vertical=False)
        rule.setFixedWidth(40)
        # The answer keeps its room while it is hidden, so the question stays where it is when the answer comes.
        self.solution = QWidget()
        below = QVBoxLayout(self.solution)
        below.setContentsMargins(0, 0, 0, 0)
        below.setSpacing(10)
        below.addWidget(rule, 0, Qt.AlignmentFlag.AlignHCenter)
        below.addWidget(self.back)
        below.addWidget(self.where)
        self._keep_room(True)
        self.face = _Face()
        inside = QVBoxLayout(self.face)
        inside.setContentsMargins(32, 24, 32, 24)
        inside.setSpacing(16)
        inside.addStretch()
        inside.addWidget(self.mark)
        inside.addWidget(self.front)
        inside.addWidget(self.solution)
        inside.addStretch()

        self.show_button = QPushButton(tr("Show the answer"))
        self.show_button.setObjectName("Primary")
        self.show_button.clicked.connect(self.reveal)
        self.again = QPushButton(tr("Again"))
        self.again.clicked.connect(lambda: self.answer(False))
        self.knew = QPushButton(tr("I knew it"))
        self.knew.setObjectName("Primary")
        self.knew.clicked.connect(lambda: self.answer(True))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        for button in (self.show_button, self.again, self.knew):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(self.face, 1)
        layout.addSpacing(4)  # 20 above the buttons, as in the other dialogs
        layout.addLayout(buttons)
        self._next()

    def _restyle(self, *_) -> None:
        messages.style_progress(self.bar)

    def _keep_room(self, keep: bool) -> None:
        policy = self.solution.sizePolicy()
        policy.setRetainSizeWhenHidden(keep)
        self.solution.setSizePolicy(policy)

    def _next(self) -> None:
        self.bar.setValue(self.total - len(self.queue))
        if not self.queue:
            self.mark.setPixmap(icon("check-circle", themes.current.accent).pixmap(QSize(36, 36),
                                                                                    self.devicePixelRatioF()))
            self.mark.show()
            self.front.setText(tr(f"Done: {self.right} of {self.total} at the first try."))
            self._keep_room(False)
            self.solution.hide()
            self.show_button.hide()
            self.again.hide()
            self.knew.setText(tr("Close"))
            self.knew.clicked.disconnect()
            self.knew.clicked.connect(self.accept)
            self.knew.show()
            self.knew.setFocus()
            self.progress.setText("")
            motion.appear(self.face, RISE)
            return
        card = self.queue[0]
        self.mark.hide()
        self.front.setText(card.front)
        self.back.setText(card.back)
        self.where.setText(tr(f"p. {card.page + 1}"))
        self._keep_room(True)
        self.solution.hide()
        self.show_button.show()
        self.again.hide()
        self.knew.hide()
        self.show_button.setFocus()
        self.progress.setText(tr(f"{self.total - len(self.queue) + 1} of {self.total}"))
        motion.appear(self.face, RISE)

    def reveal(self) -> None:
        self.solution.show()
        self.show_button.hide()
        self.again.show()
        self.knew.show()
        self.knew.setFocus()
        motion.appear(self.solution, RISE)

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
        target, _ = QFileDialog.getSaveFileName(view, tr("Save flashcards for Anki"),
                                                str(path.with_name(f"{path.stem} flashcards.txt")),
                                                "Anki text (*.txt)")
        if target:
            Path(target).write_text(flashcards.anki_text(cards, f"Zenith PDF::{path.stem}", path.stem),
                                    encoding="utf-8")
            view.message.emit(f"Saved {len(cards)} cards. In Anki: File > Import, then choose {Path(target).name}.")
    return choice
