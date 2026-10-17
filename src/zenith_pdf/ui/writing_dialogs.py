"""The writing helpers on the page: spelling and punctuation for text boxes, callouts and notes; the key points
of selected text or a page; study notes from a document's highlights. All of it runs on this computer, without a
language model: see core/writing.py and core/spelling.py.
"""

from __future__ import annotations

import html
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QTextEdit,
    QWidget,
)

from ..core import spelling, textlayout, writing
from ..core.annotations import NOTE_SIZE, FreeText, Note, TextMarkup
from ..core.platforms import LINUX, SYSTEM_NAME, settings_place
from ..theme import themes
from . import messages
from .commands import ChangeAnnotations
from .widgets import Segmented, muted_label

if TYPE_CHECKING:
    from .document_view import DocumentView

MAX_ISSUES = 60  # rows the spelling dialog lists; a text with more is better fixed in parts
CONTEXT = 10  # characters of text shown either side of a change
SPELLING_WIDTH = 700
PREVIEW_HEIGHT = 120
LIST_HEIGHT = 220
LENGTHS = (("short", "Short"), ("medium", "Medium"), ("long", "Long"))
SENTENCES = {"short": 2, "medium": 4, "long": 7}


def user_language() -> str:
    """The language the system shows its menus in, if it is one the helpers know; English otherwise."""
    code = QLocale.system().name().split("_")[0].lower()
    return code if code in writing.LANGUAGES else "en"


# Spelling ---------------------------------------------------------------------------------


class Change:
    """One proposed change to the text: a word the spell checker flags, or a spacing or punctuation fix."""

    def __init__(self, start: int, end: int, options: tuple[str, ...], reason: str, spelling_issue: bool):
        self.start, self.end = start, end
        self.options = options
        self.reason = reason
        self.spelling = spelling_issue
        self.replacement = options[0] if options else ""


def proposed_changes(text: str, language: str) -> list[Change]:
    """Spelling (when the system has the language) and tidy fixes, in text order, never two on the same characters."""
    changes: list[Change] = []
    for issue in spelling.check(text, language, limit=MAX_ISSUES):
        start, end = issue.start, issue.start + issue.length
        if issue.action == "delete":
            # A doubled word goes with the space before it: "the the" becomes "the".
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            changes.append(Change(start, end, ("",), "Repeated word", True))
        elif issue.suggestions:
            changes.append(Change(start, end, issue.suggestions, "Spelling", True))
        else:
            changes.append(Change(start, end, (), "Not in the dictionary", True))
    taken = [(c.start, c.end) for c in changes]
    for fix in writing.tidy(text, language):
        if not any(fix.start < end and start < fix.end or fix.start == start for start, end in taken):
            changes.append(Change(fix.start, fix.end, (fix.replacement,), fix.reason, False))
    changes.sort(key=lambda c: c.start)
    return changes[:MAX_ISSUES]


class SpellingDialog(messages.MessageDialog):
    """The proposed changes to a text, each with a tick box and, for a misspelt word, its alternatives."""

    def __init__(self, parent, text: str, language: str | None = None):
        super().__init__(parent, "Check spelling and punctuation", "", [
            ("apply", "Apply changes", "primary"), (messages.CANCEL, "Cancel", "")])
        self.setMinimumWidth(SPELLING_WIDTH)
        self.text = text
        self.intro = muted_label()
        self.intro.setWordWrap(True)
        self.add_field(self.intro)
        self.language = QComboBox()
        detected = writing.detect_language(text, user_language()) or "en"
        for code in writing.LANGUAGES:
            name = spelling.NAMES[code] + ("" if spelling.available(code) else " (spacing and punctuation only)")
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(self.language.findData(detected), 0))
        self.language.currentIndexChanged.connect(self._refresh)
        self.add_field(self.language, full_width=False)
        self.preview = QTextEdit()
        self.preview.setObjectName("SpellingPreview")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(PREVIEW_HEIGHT)
        self.add_field(self.preview)
        self._rows = QWidget()
        self._grid = QGridLayout(self._rows)
        self._grid.setContentsMargins(0, 0, 4, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(2, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMaximumHeight(LIST_HEIGHT)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self._rows)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.add_field(self.scroll)
        self.changes: list[Change] = []
        self.boxes: list[QCheckBox] = []
        self.choices: list[QComboBox | None] = []
        self._refresh()

    @property
    def code(self) -> str:
        return self.language.currentData() or "en"

    def _refresh(self, *_) -> None:
        self.changes = proposed_changes(self.text, self.code)
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.boxes, self.choices = [], []
        for row, change in enumerate(self.changes):
            box = QCheckBox()
            box.setChecked(bool(change.options))
            box.setEnabled(bool(change.options))
            box.toggled.connect(self._update)
            found = QLabel(self._in_context(change, None))
            found.setTextFormat(Qt.TextFormat.RichText)
            if len(change.options) > 1:
                choice = QComboBox()
                choice.setEditable(True)
                choice.addItems(list(change.options))
                choice.currentTextChanged.connect(self._update)
                self.choices.append(choice)
                target: QWidget = choice
            else:
                self.choices.append(None)
                target = QLabel(self._in_context(change, change.replacement) if change.options else "no suggestion")
                target.setTextFormat(Qt.TextFormat.RichText)
                if not change.options:
                    target.setObjectName("Muted")
            arrow = muted_label("→")
            reason = muted_label(change.reason)
            for column, widget in enumerate((box, found, arrow, target, reason)):
                self._grid.addWidget(widget, row, column)
            self.boxes.append(box)
        self._grid.setColumnStretch(2, 0)
        self._grid.setColumnStretch(3, 1)
        self._mark_preview()
        self._update()

    def _in_context(self, change: Change, replacement: str | None) -> str:
        """The change with a few words round it, as rich text: what changes is marked, spaces made visible.
        `replacement` None shows the text as it is now."""
        text = self.text
        start, end = max(0, change.start - CONTEXT), min(len(text), change.end + CONTEXT)
        while start > 0 and not text[start - 1].isspace() and start < change.start:
            start += 1  # whole words only
        while end < len(text) and not text[end].isspace() and end > change.end:
            end -= 1
        piece = text[change.start:change.end] if replacement is None else replacement
        shown = html.escape(piece).replace(" ", "·") or "·"
        if replacement == "":
            shown = "(gone)"
        colour = themes.current.danger if replacement is None else themes.current.accent_ink
        marked = f'<span style="color:{colour}; font-weight:600">{shown}</span>'
        before = ("…" if start > 0 else "") + html.escape(text[start:change.start])
        after = html.escape(text[change.end:end]) + ("…" if end < len(text) else "")
        return f"{before}{marked}{after}".replace("\n", " ")

    def _mark_preview(self) -> None:
        t = themes.current
        self.preview.setPlainText(self.text)
        cursor = QTextCursor(self.preview.document())
        for change in self.changes:
            cursor.setPosition(change.start)
            cursor.setPosition(max(change.end, change.start + 1), QTextCursor.MoveMode.KeepAnchor)
            mark = QTextCharFormat()
            if change.spelling:
                mark.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
                mark.setUnderlineColor(QColor(t.danger))
            else:
                mark.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
                mark.setUnderlineColor(QColor(t.accent))
            cursor.mergeCharFormat(mark)

    def _update(self, *_) -> None:
        chosen = len(self.chosen())
        spelling_on = spelling.available(self.code)
        name = spelling.NAMES[self.code]
        if not self.changes:
            text = f"Nothing to change in {name}."
        else:
            text = f"{len(self.changes)} to look at in {name}. Untick what should stay as it is."
        if not spelling_on:
            text += f" {SYSTEM_NAME} has no {name} dictionary here, so only spacing, punctuation and capitals are checked."
            if LINUX:
                text += (f" Install the {name} Hunspell dictionary (hunspell-{self.code}, or a similar package) "
                         "to check its spelling too.")
            else:
                text += f" Add {name} in {settings_place('languages')} to check its spelling too."
        self.intro.setText(text)
        button = self.button("apply")
        if button is not None:
            button.setEnabled(chosen > 0)
            button.setText(f"Apply {chosen} change{'s' if chosen != 1 else ''}" if chosen else "Apply changes")

    def chosen(self) -> list[writing.Fix]:
        fixes = []
        for change, box, choice in zip(self.changes, self.boxes, self.choices):
            if box.isChecked() and change.options:
                replacement = choice.currentText() if choice is not None else change.replacement
                fixes.append(writing.Fix(change.start, change.end, replacement, change.reason))
        return fixes

    def result(self) -> str:
        return writing.apply_fixes(self.text, self.chosen())


def text_target(view: DocumentView):
    """The text box, callout or note the spelling check would work on: the one selected, or being typed in."""
    view.finish_text_edit()
    selected = [a for a in view.selected_annotations() if isinstance(a, (FreeText, Note))]
    return selected[0] if len(selected) == 1 else None


def check_spelling(view: DocumentView, annotation=None) -> bool:
    """Check a text box, callout or note and apply what the user keeps, as one undo step. True if it changed."""
    annotation = annotation or text_target(view)
    if annotation is None or view.document.read_only:
        return False
    annotation = view.store.get(annotation.id) or annotation  # as it is now, after any edit
    text = annotation.contents
    if not text.strip():
        view.message.emit("This one has no text to check yet.")
        return False
    dialog = SpellingDialog(view, text)
    if not dialog.changes and spelling.available(dialog.code):
        dialog.deleteLater()
        view.message.emit(f"No mistakes found ({spelling.NAMES[dialog.code]}).")
        return False
    accepted = dialog.exec() == SpellingDialog.DialogCode.Accepted and dialog.choice == "apply"
    new_text = dialog.result() if accepted else text
    dialog.deleteLater()
    if new_text == text:
        return False
    updated = replace(annotation, contents=new_text)
    if isinstance(annotation, FreeText):
        width, height = annotation.upright_size()
        updated = replace(annotation.with_height(max(height, textlayout.fitted_height(new_text, annotation.font_size,
                                                                                       width))), contents=new_text)
    view.undo_stack.push(ChangeAnnotations(view.store, [annotation], [updated], "Fix spelling"))
    return True


# Key points -------------------------------------------------------------------------------


class SummaryDialog(messages.MessageDialog):
    """The key sentences of a text, as many as Short, Medium or Long asks for."""

    def __init__(self, parent, text: str, where: str):
        super().__init__(parent, "Key points", "", [
            ("note", "Add as a note", "primary"), ("copy", "Copy", ""), (messages.CANCEL, "Close", "")])
        self.setMinimumWidth(560)
        self.text = text
        self.language = writing.detect_language(text, user_language()) or "en"
        intro = muted_label(f"The sentences of {where} that carry most of it, picked by how often their words "
                            "come up. Worked out on this computer.")
        intro.setWordWrap(True)
        self.add_field(intro)
        self.length = Segmented(LENGTHS)
        self.length.set_value("medium")
        self.length.chosen.connect(self._refresh)
        self.add_field(self.length, full_width=False)
        self.view = QTextBrowser()
        self.view.setObjectName("SummaryText")
        self.view.setMinimumHeight(200)
        self.add_field(self.view)
        self.points: list[str] = []
        self._refresh()

    def _refresh(self, *_) -> None:
        self.points = writing.key_sentences(self.text, SENTENCES[self.length.value], self.language)
        self.view.setHtml("".join(f'<p style="margin: 0 0 8px 0;">•&nbsp;&nbsp;{html.escape(point)}</p>'
                                  for point in self.points))

    def as_text(self) -> str:
        return "\n".join(f"• {point}" for point in self.points)


def summarise(view: DocumentView, text: str, page: int, at: tuple[float, float], where: str) -> str | None:
    """Show the key points of `text`; keep them as a note at `at` on `page`, or copy them. The choice made."""
    if len(writing.sentences(text)) < 2:
        view.message.emit("There is not enough text here to pick key points from: select a longer passage.")
        return None
    dialog = SummaryDialog(view, text, where)
    dialog.exec()
    choice, points = dialog.choice, dialog.as_text()
    dialog.deleteLater()
    if choice == "copy":
        QApplication.clipboard().setText(points)
        view.message.emit("Key points copied.")
    elif choice == "note" and not view.document.read_only:
        style = view.tool_settings.style_for("note")
        width, height = view.document.page_size(page)
        x = min(max(at[0], 0.0), width - NOTE_SIZE)
        y = min(max(at[1], 0.0), height - NOTE_SIZE)
        note = Note(page=page, position=(x, y), contents=f"Key points\n{points}", color=style.color,
                    icon=style.note_icon, **view.common("note"))
        view.add_annotations([note], "Note")
        view.select([note])
        view.message.emit("Added a note with the key points. Double-click it to read them.")
    return choice


def summarise_selection(view: DocumentView) -> str | None:
    if not view.has_text_selection():
        return None
    page, rects, text, _ = view._text_selection
    x, y, w, _h = rects[0]
    return summarise(view, text, page, (x + w + 4, y - NOTE_SIZE / 2), "the selected text")


def summarise_page(view: DocumentView, page: int | None = None) -> str | None:
    page = view.current_page if page is None else page
    text = view.document.page_text(page)
    return summarise(view, text, page, (12.0, 12.0), f"page {page + 1}")


# Study notes ------------------------------------------------------------------------------


def marks_of(view: DocumentView) -> list[writing.Marked]:
    """Every text markup of the document, with the text under it."""
    found = []
    for annotation in view.store.all():
        if isinstance(annotation, TextMarkup) and annotation.quads:
            text = view.document.text_in(annotation.page, annotation.quads)
            top = min(q[1] for q in annotation.quads)
            found.append(writing.Marked(annotation.page, annotation.style, tuple(annotation.color), text,
                                        annotation.contents, top))
    return found


class StudyNotesDialog(messages.MessageDialog):
    def __init__(self, parent, marks: list[writing.Marked], title: str):
        super().__init__(parent, "Study notes", "", [
            ("save", "Save as…", "primary"), ("copy", "Copy", ""), (messages.CANCEL, "Close", "")])
        self.setMinimumWidth(620)
        self.marks, self.title = marks, title
        intro = muted_label(f"{len(marks)} marked passage{'s' if len(marks) != 1 else ''}, each with the text under "
                            "it and its comment, ready to revise from. Save them as Markdown, which most note apps "
                            "read.")
        intro.setWordWrap(True)
        self.add_field(intro)
        self.order = Segmented((("page", "By page"), ("colour", "By colour")))
        self.order.chosen.connect(self._refresh)
        self.add_field(self.order, full_width=False)
        self.view = QTextBrowser()
        self.view.setObjectName("SummaryText")
        self.view.setMinimumHeight(320)
        self.add_field(self.view)
        self.markdown = ""
        self._refresh()

    def _refresh(self, *_) -> None:
        self.markdown = writing.study_notes(self.marks, self.title, self.order.value)
        self.view.setMarkdown(self.markdown.split("\n", 1)[1])  # the dialog's heading names it already


def study_notes(view: DocumentView) -> str | None:
    marks = marks_of(view)
    if not marks:
        view.message.emit("No highlights or underlines yet: mark some text first.")
        return None
    path = view.document.path
    dialog = StudyNotesDialog(view, marks, f"Study notes: {path.stem}")
    dialog.exec()
    choice, notes = dialog.choice, dialog.markdown
    dialog.deleteLater()
    if choice == "copy":
        QApplication.clipboard().setText(notes)
        view.message.emit("Study notes copied.")
    elif choice == "save":
        target, _ = QFileDialog.getSaveFileName(view, "Save study notes", str(path.with_name(f"{path.stem} notes.md")),
                                                "Markdown (*.md);;Text (*.txt)")
        if target:
            Path(target).write_text(notes, encoding="utf-8")
            view.message.emit(f"Saved {Path(target).name}.")
    return choice
