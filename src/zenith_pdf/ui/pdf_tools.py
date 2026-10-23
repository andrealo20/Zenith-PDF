"""The PDF tools panel and what each tool asks before it runs.

Tools that change the document go through Workspace.edit_structure, so Undo
takes them back like any other edit. Tools that make files (split, export) leave
the document alone. Compress works on a copy in a background thread and swaps
the result in with Workspace.replace_structure.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEventLoop, QObject, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPainter, QUndoCommand
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import images, pdf_tools as T
from ..core.annotations import Redaction
from ..core.document import Document
from ..core.i18n import tr
from ..core.pages import PAPER_SIZES
from ..icons import icon
from ..theme import themes
from . import messages
from .commands import AddAnnotations, RemoveAnnotations
from .dialogs import ColorButton, parse_pages
from .page_actions import IMAGE_FILES
from .widgets import FloatingBar, IconButton, muted_label

if TYPE_CHECKING:
    from .main_window import MainWindow
    from .workspace import Workspace

PANEL_WIDTH = 620
TILE_WIDTH = 284  # two columns of tiles inside the panel, with room for the scroll bar
JPEG_QUALITY = 92
FORM_WIDTH = 480
PROPERTIES_WIDTH = 560
PROPERTIES_CAPTION = 48  # px the window's caption and a little room take above and below the Properties dialog
PROPERTIES_LEAST = 120  # px of facts the Properties dialog keeps on the shortest screen
SHOW_AFTER = 0.3  # seconds a job runs before its progress dialog appears
REPORT_EVERY = 0.05  # seconds between progress reports from a worker thread


# A small form builder: each tool lists its options, the dialog lays them out -------------


class ToolForm(messages.MessageDialog):
    """Options for one tool. Each add_* method adds a row; run() returns the values or None.

    `validate`, if set, gets the values when the main button is pressed and returns
    what is wrong with them ("" if nothing), which keeps the form open. (A list is
    choose(), since MessageDialog keeps the button pressed in `choice`.)
    """

    def __init__(self, title: str, intro: str, parent: QWidget, action: str = "Apply"):
        super().__init__(parent, title, intro, [("ok", action, "primary"), (messages.CANCEL, "Cancel", "")])
        self.setMinimumWidth(FORM_WIDTH)
        self.validate: Callable[[dict], str] | None = None
        self._getters: dict[str, Callable] = {}
        self._values: dict | None = None
        self._box = QWidget()
        self._form = QFormLayout(self._box)
        self._form.setContentsMargins(0, 6, 0, 0)
        self._form.setHorizontalSpacing(18)
        self._form.setVerticalSpacing(10)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.add_field(self._box)

    def choose(self, key: str, label: str, options: dict, default=None) -> QComboBox:
        box = QComboBox()
        for value, text in options.items():
            box.addItem(tr(text), value)
        if default is not None:
            box.setCurrentIndex(max(box.findData(default), 0))
        self._form.addRow(tr(label), box)
        self._getters[key] = box.currentData
        return box

    def number(self, key: str, label: str, low: float, high: float, default: float, suffix: str = "",
               decimals: int = 0) -> QWidget:
        box = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            box.setDecimals(decimals)
        box.setRange(low, high)
        box.setValue(default)
        box.setSuffix(suffix)
        self._form.addRow(tr(label), box)
        self._getters[key] = box.value
        return box

    def text(self, key: str, label: str, default: str = "", placeholder: str = "", password: bool = False) -> QLineEdit:
        edit = QLineEdit(default)
        edit.setPlaceholderText(tr(placeholder))
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._form.addRow(tr(label), edit)
        self._getters[key] = lambda: edit.text()
        return edit

    def check(self, key: str, label: str, default: bool = False) -> QCheckBox:
        box = QCheckBox(tr(label))
        box.setChecked(default)
        self._form.addRow("", box)
        self._getters[key] = box.isChecked
        return box

    def color(self, key: str, label: str, default) -> ColorButton:
        button = ColorButton(default)
        self._form.addRow(tr(label), button)
        self._getters[key] = lambda: button.color
        return button

    def pages(self, key: str, count: int, current: int, label: str = "Pages") -> None:
        box = QComboBox()
        for value, text in (("all", "All pages"), ("current", f"This page ({current + 1})"), ("odd", "Odd pages"),
                            ("even", "Even pages"), ("range", "Pages…")):
            box.addItem(tr(text), value)
        pick = QLineEdit()
        pick.setPlaceholderText(tr(f"e.g. 1-3, 7 (of {count})"))
        pick.setEnabled(False)
        box.currentIndexChanged.connect(lambda: pick.setEnabled(box.currentData() == "range"))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(box)
        layout.addWidget(pick, 1)
        self._form.addRow(tr(label), row)

        def chosen() -> list[int]:
            kind = box.currentData()
            if kind == "current":
                return [current]
            if kind == "odd":
                return list(range(0, count, 2))
            if kind == "even":
                return list(range(1, count, 2))
            if kind == "range":
                result = parse_pages(pick.text(), count)
                if not result:
                    raise ValueError("Write pages like 1-3, 7.")
                return result
            return list(range(count))

        self._getters[key] = chosen

    def note(self, text: str) -> None:
        label = muted_label(text)
        label.setWordWrap(True)
        self._form.addRow("", label)

    def _check(self) -> bool:
        """Read every field. False, after saying why, if something isn't right."""
        try:
            values = {key: get() for key, get in self._getters.items()}
        except ValueError as error:
            messages.warn(self, self.windowTitle(), str(error) or "Write pages like 1-3, 7.")
            return False
        problem = self.validate(values) if self.validate is not None else ""
        if problem:
            messages.warn(self, self.windowTitle(), problem)
            return False
        self._values = values
        return True

    def accept(self) -> None:
        if self._check():
            super().accept()

    def run(self) -> dict | None:
        focused = self.focusWidget()
        if focused is None or not self._box.isAncestorOf(focused):
            fields = [w for w in self._box.findChildren(QWidget)
                      if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.isEnabled()]
            if fields:
                fields[0].setFocus()
        accepted = self.exec() == QDialog.DialogCode.Accepted
        self.deleteLater()
        return self._values if accepted else None


class ProgressDialog(messages.MessageDialog):
    """How far a long job has got, with Cancel. Window-modal, in the style of the other dialogs.

    It appears once the job has run SHOW_AFTER seconds, so quick jobs don't flash it;
    until then the window ignores clicks and keys (they wait in the queue). Cancel,
    Esc and the close button only ask the job to stop: finish() closes the dialog.
    """

    def __init__(self, parent: QWidget, title: str, text: str, maximum: int = 0):
        super().__init__(parent, title, "", [(messages.CANCEL, "Cancel", "")])
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(messages.MIN_WIDTH)
        self.cancelled = False
        self.on_cancel: Callable[[], None] | None = None
        self.label = QLabel(tr(text))
        self.label.setObjectName("DialogBody")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, maximum)
        self._style_bar()
        themes.changed.connect(self._style_bar)
        self.add_field(self.label)
        self.add_field(self.bar)
        cancel = self.button(messages.CANCEL)
        cancel.setDefault(False)  # Enter, pressed once too often on the form before, must not stop the job
        cancel.setFocus()
        self._started = time.monotonic()

    def _style_bar(self, *_) -> None:
        t = themes.current
        self.bar.setStyleSheet(f"QProgressBar {{ background: {t.border}; border: none; border-radius: 3px; }}"
                               f"QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}")

    def set_progress(self, value: int, maximum: int | None = None, text: str | None = None) -> None:
        if maximum is not None and maximum != self.bar.maximum():
            self.bar.setRange(0, maximum)
        self.bar.setValue(value)
        if text is not None and not self.cancelled:
            self.label.setText(tr(text))

    def due(self) -> bool:
        """Whether the job has run long enough for the dialog to show."""
        return time.monotonic() - self._started >= SHOW_AFTER

    def step(self, value: int, text: str | None = None) -> bool:
        """Progress of a loop on the GUI thread; lets the window repaint. False once cancelled."""
        self.set_progress(value, text=text)
        if not self.isVisible() and self.due():
            self.show()
        flags = QEventLoop.ProcessEventsFlag.AllEvents
        if not self.isVisible():
            flags = QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
        QApplication.processEvents(flags)
        return not self.cancelled

    def reject(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.label.setText(tr("Stopping…"))
        self.button(messages.CANCEL).setEnabled(False)
        if self.on_cancel is not None:
            self.on_cancel()

    def finish(self) -> None:
        self.done(QDialog.DialogCode.Rejected)  # hides it; reject() would only ask to stop
        self.deleteLater()


# The tools ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolInfo:
    key: str
    title: str
    icon: str
    group: str
    description: str
    run: Callable[[MainWindow], None]
    needs_document: bool = True


def _workspace(window: MainWindow) -> Workspace | None:
    workspace = window.current_workspace()
    if workspace is None:
        messages.inform(window, "PDF tools", "Open a document first.")
    elif workspace.document.read_only:
        messages.inform(window, "PDF tools", "This document could not be fully read, so it can only be viewed.")
        return None
    return workspace


def _edit(workspace: Workspace, label: str, operation) -> bool:
    """Run `operation(pdf)` on the document as one undoable step."""
    document = workspace.document
    return workspace.edit_structure(label, lambda annotations: document.edit_pages(annotations, operation))


def _folder(window, title: str, start: Path) -> Path | None:
    folder = QFileDialog.getExistingDirectory(window, tr(title), str(start))
    return Path(folder) if folder else None


def _show_folder(folder: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _new_document(window: MainWindow, name: str, data: bytes) -> None:
    folder = Path(window.settings.new_document_folder)
    path = folder / f"{name}.pdf"
    number = 2
    while path.exists():
        path = folder / f"{name} ({number}).pdf"
        number += 1
    window.open_document(Document(path, data=data))


# Pages ---------------------------------------------------------------------------------


def page_numbers(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Add page numbers", "The numbers become part of the pages. In the format, {n} is the number "
                    "and {total} the last number.", window, "Add numbers")
    form.choose("position", "Position", T.POSITIONS, "bottom-center")
    # The formats are written on the pages, so "Page" is in the language shown, as the examples are.
    form.choose("format", "Format", {"{n}": "1, 2, 3", tr("Page {n}"): "Page 1",
                                     tr("Page {n} of {total}"): "Page 1 of 9", "{n} / {total}": "1 / 9",
                                     "- {n} -": "- 1 -"})
    custom = form.text("custom", "Or your own", placeholder="e.g. Chapter 2, page {n}")
    form.number("start", "First number", 0, 99999, 1)
    form.number("size", "Font size", 5, 48, 10, " pt")
    form.number("margin", "From the edge", 3, 60, 12, " mm")
    form.color("color", "Colour", (0.2, 0.2, 0.2))
    form.pages("pages", document.page_count, ws.view.current_page)
    custom.setFocus()
    values = form.run()
    if values is None:
        return
    template = values["custom"].strip() or values["format"]
    chosen, start = values["pages"], int(values["start"])
    last = start + len(chosen) - 1
    items = []
    for rank, index in enumerate(chosen):
        text = T.page_number_text(template, start + rank, last)
        items.append((index, document.geometry(index), text, document.text_width(text, values["size"])))
    _edit(ws, "Add page numbers", lambda pdf: T.add_page_numbers(pdf, items, values["position"], values["size"],
                                                                 T.mm(values["margin"]), values["color"]))


def _content_box(document: Document, index: int) -> tuple[float, float, float, float] | None:
    """Where the page has ink, in display space; None for an empty page."""
    scale = 0.5
    image = document.render(index, scale).convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = image.width(), image.height()
    ink = re.compile(rb"[\x00-\xe6]")  # darker than near-white
    rows = [bytes(image.constScanLine(y))[:width] for y in range(height)]
    inked = [y for y, row in enumerate(rows) if ink.search(row)]
    if not inked:
        return None
    left, right = width, 0
    for y in inked:
        row = rows[y]
        left = min(left, ink.search(row).start())
        right = max(right, width - ink.search(row[::-1]).start())
    return left / scale, inked[0] / scale, (right - left) / scale, (inked[-1] + 1 - inked[0]) / scale


def crop(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Crop pages", "Hides the edges of the pages. Nothing is deleted, so a crop can be undone.",
                    window, "Crop")
    auto = form.check("auto", "Trim the white margins automatically", True)
    boxes = [form.number(side, f"{side.capitalize()}", 0, 200, 10, " mm") for side in ("top", "bottom", "left", "right")]
    for box in boxes:
        box.setEnabled(False)
    auto.toggled.connect(lambda on: [b.setEnabled(not on) for b in boxes])
    form.number("padding", "Keep around the content", 0, 30, 4, " mm")
    form.pages("pages", document.page_count, ws.view.current_page)
    values = form.run()
    if values is None:
        return
    rects = {}
    for index in values["pages"]:
        width, height = document.page_size(index)
        if values["auto"]:
            box = _content_box(document, index)
            if box is None:
                continue
            pad = T.mm(values["padding"])
            x, y, w, h = box
            rects[index] = (max(x - pad, 0), max(y - pad, 0), min(w + 2 * pad, width), min(h + 2 * pad, height))
        else:
            left, right, top, bottom = (T.mm(values[k]) for k in ("left", "right", "top", "bottom"))
            rects[index] = (left, top, width - left - right, height - top - bottom)
    geometries = {i: document.geometry(i) for i in rects}
    if rects:
        _edit(ws, "Crop pages", lambda pdf: [T.crop(pdf, i, geometries[i], r) for i, r in rects.items()])


PAPERS = {name: name for name in PAPER_SIZES}


def resize(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    form = ToolForm("Page size", "Puts the pages on paper of another size, the content scaled to fit. Annotations "
                    "and form fields on these pages become part of the page.", window, "Resize")
    form.choose("paper", "Paper", PAPERS, "A4")
    form.choose("orientation", "Orientation", {"auto": "Same as each page", "portrait": "Portrait",
                                               "landscape": "Landscape"})
    form.number("margin", "Margin", 0, 50, 0, " mm")
    form.pages("pages", ws.document.page_count, ws.view.current_page)
    values = form.run()
    if values is not None:
        _edit(ws, "Change page size", lambda pdf: T.resize_pages(pdf, values["pages"], values["paper"],
                                                                 values["orientation"], T.mm(values["margin"])))


def pages_per_sheet(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    form = ToolForm("Pages per sheet", "Makes a new document with several pages on each sheet, for printing "
                    "handouts or saving paper. This document stays as it is.", window, "Create")
    form.choose("count", "Pages per sheet", {n: str(n) for n in T.SHEET_GRIDS}, 4)
    form.choose("paper", "Paper", PAPERS, "A4")
    form.choose("order", "Order", {"across": "Across, then down", "down": "Down, then across"})
    form.number("margin", "Margin", 0, 40, 8, " mm")
    form.number("gap", "Space between pages", 0, 30, 4, " mm")
    form.check("border", "A thin line around each page", True)
    values = form.run()
    if values is None:
        return
    document = ws.document
    data = document.extract_pages(ws.store.all(), list(range(document.page_count)))
    result = T.pages_per_sheet(data, values["count"], values["paper"], T.mm(values["margin"]), T.mm(values["gap"]),
                               values["border"], values["order"] == "down")
    _new_document(window, f"{document.path.stem} ({values['count']} per sheet)", result)


def split(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Split into files", "Saves parts of this document as separate PDF files, with their "
                    "annotations. The document itself stays whole.", window, "Choose folder…")
    mode = form.choose("mode", "Split", {"every": "Every few pages", "at": "Before chosen pages",
                                         "bookmarks": "At each top-level bookmark", "single": "Into single pages"})
    every = form.number("every", "Pages in each file", 1, max(document.page_count, 1), 2)
    at = form.text("at", "Start new files at", placeholder="e.g. 5, 12, 20")
    at.setEnabled(False)
    mode.currentIndexChanged.connect(lambda: (every.setEnabled(mode.currentData() == "every"),
                                              at.setEnabled(mode.currentData() == "at")))
    form.check("open", "Show the folder afterwards", True)
    values = form.run()
    if values is None:
        return
    count = document.page_count
    titles: list[str] = []
    if values["mode"] == "every":
        parts = T.chunks(count, int(values["every"]))
    elif values["mode"] == "single":
        parts = T.chunks(count, 1)
    elif values["mode"] == "at":
        try:
            starts = [int(p) - 1 for p in values["at"].replace(" ", "").split(",") if p]
        except ValueError:
            messages.warn(window, "Split into files", "Write page numbers like 5, 12, 20.")
            return
        parts = T.page_ranges(starts, count)
    else:
        marks = [(e.page, e.title) for e in document.outline() if e.level == 0 and e.page is not None]
        if not marks:
            messages.inform(window, "Split into files", "This document has no bookmarks to split at.")
            return
        parts = T.page_ranges([page for page, _ in marks], count)
        names = dict(marks)
        titles = [names.get(part.start, f"part {n}") for n, part in enumerate(parts, start=1)]
    folder = _folder(window, "Save the files in", document.path.parent)
    if folder is None:
        return
    annotations = ws.store.all()
    digits = len(str(len(parts)))
    progress = ProgressDialog(window, "Split into files", "Saving the files…", len(parts))
    saved, failure = 0, None
    for number, part in enumerate(parts, start=1):
        if not progress.step(number - 1, f"Saving file {number} of {len(parts)}"):
            break
        title = T.safe_file_name(titles[number - 1]) if titles else f"part {number:0{digits}d}"
        target = folder / f"{document.path.stem} - {title}.pdf"
        try:
            target.write_bytes(document.extract_pages(annotations, list(part)))
        except OSError as error:
            failure = error
            break
        saved += 1
    progress.finish()
    if failure is not None:
        messages.warn(window, "Split into files", f"Only {_plural(saved, 'file')} could be saved.", detail=str(failure))
        return
    ws.notify(f"Saved {_plural(saved, 'file')}.")
    if values["open"] and saved:
        _show_folder(folder)


def remove_blank(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Remove blank pages", "Finds pages with almost no ink, such as the empty backs of a scan. "
                    "You see the list before anything is deleted.", window, "Find")
    form.choose("threshold", "A page is blank with less ink than",
                {0.0005: "0.05% (strict)", 0.003: "0.3%", 0.01: "1% (scans with dust and noise)"}, 0.003)
    form.check("marked", "Keep pages that have annotations", True)
    values = form.run()
    if values is None:
        return
    with_annotations = {a.page for a in ws.store.all()} if values["marked"] else set()
    found = []
    progress = ProgressDialog(window, "Remove blank pages", "Looking at the pages…", document.page_count)
    for index in range(document.page_count):
        if not progress.step(index, f"Looking at page {index + 1} of {document.page_count}"):
            progress.finish()
            return
        if index in with_annotations:
            continue
        gray = document.render(index, 0.3).convertToFormat(QImage.Format.Format_Grayscale8)
        pixels = b"".join(bytes(gray.constScanLine(y))[: gray.width()] for y in range(gray.height()))
        if T.blank(pixels, values["threshold"]):
            found.append(index)
    progress.finish()
    if not found:
        messages.inform(window, "Remove blank pages", "No blank pages found.")
        return
    if len(found) == document.page_count:
        messages.inform(window, "Remove blank pages", "Every page looks blank, so nothing was removed.")
        return
    listed = ", ".join(str(i + 1) for i in found)
    pages = _plural(len(found), "page")
    if messages.confirm(window, "Remove blank pages", f"These pages look blank: {listed}. Undo brings them back.",
                        f"Delete {pages}", danger=True):
        ws.actions.delete_pages(found)


def reverse(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is not None and ws.document.page_count > 1:
        ws.actions.reorder(list(reversed(range(ws.document.page_count))))
        ws.notify("Page order reversed.")


# Convert ------------------------------------------------------------------------------


def export_images(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Export pages as images", "Saves pages as picture files, one per page.", window,
                    "Choose folder…")
    form.choose("format", "Format", {"png": "PNG, sharp text", "jpg": "JPEG, smaller photos"})
    form.choose("dpi", "Resolution", {72: "72 dpi", 96: "96 dpi (screen)", 150: "150 dpi", 200: "200 dpi",
                                      300: "300 dpi (print)", 600: "600 dpi"}, 150)
    form.pages("pages", document.page_count, ws.view.current_page)
    form.check("annotations", "Include annotations", True)
    form.check("open", "Show the folder afterwards", True)
    values = form.run()
    if values is None:
        return
    folder = _folder(window, "Save the images in", document.path.parent)
    if folder is None:
        return
    scale = values["dpi"] / 72
    chosen = values["pages"]
    progress = ProgressDialog(window, "Export pages as images", "Saving the images…", len(chosen))
    digits = len(str(document.page_count))
    saved, failed = 0, None
    for done, index in enumerate(chosen):
        if not progress.step(done, f"Saving page {index + 1} ({done + 1} of {len(chosen)})"):
            break
        image = ws.view.page_image(index, scale) if values["annotations"] else document.render(index, scale)
        target = folder / f"{document.path.stem} - page {index + 1:0{digits}d}.{values['format']}"
        if not image.save(str(target), "JPG" if values["format"] == "jpg" else "PNG",
                          JPEG_QUALITY if values["format"] == "jpg" else -1):
            failed = target
            break
        saved += 1
    progress.finish()
    if failed is not None:
        messages.warn(window, "Export pages as images", f"{failed.name} could not be saved there.",
                      detail=str(failed.parent))
        return
    ws.notify(f"Saved {_plural(saved, 'image')}.")
    if values["open"] and saved:
        _show_folder(folder)


def export_text(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Export text", "Saves the text of the document as a plain text file. Scanned pages need "
                    "text recognition (OCR) first.", window, "Save as…")
    form.pages("pages", document.page_count, ws.view.current_page)
    form.check("markers", "Mark where each page starts", True)
    values = form.run()
    if values is None:
        return
    target, _ = QFileDialog.getSaveFileName(window, tr("Save text as"), str(document.path.with_suffix(".txt")),
                                            tr("Text files (*.txt)"))
    if not target:
        return
    parts = []
    for index in values["pages"]:
        text = document.page_text(index).replace("\r\n", "\n").strip()
        parts.append(f"--- Page {index + 1} ---\n{text}" if values["markers"] else text)
    try:
        Path(target).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    except OSError as error:
        messages.warn(window, "Export text", "The text file could not be saved.", detail=str(error))
        return
    ws.notify(f"Saved the text of {_plural(len(parts), 'page')}.")


def images_to_pdf(window: MainWindow) -> None:
    paths, _ = QFileDialog.getOpenFileNames(window, tr("Pictures for the new PDF"), window.settings.last_directory,
                                            tr(IMAGE_FILES))
    if not paths:
        return
    form = ToolForm("Images to PDF", f"Makes a new document with one page for each of the {len(paths)} "
                    "pictures, in the order chosen.", window, "Create")
    form.choose("paper", "Page size", {None: "Same as each picture", **PAPERS}, "A4")
    form.number("margin", "Margin", 0, 50, 10, " mm")
    values = form.run()
    if values is None:
        return
    pictures = [data for data in (images.from_file(p) for p in sorted(paths, key=str.lower)) if data]
    try:
        data = T.images_to_pdf(pictures, values["paper"], T.mm(values["margin"]))
    except ValueError as error:
        messages.warn(window, "Images to PDF", str(error))
        return
    _new_document(window, Path(paths[0]).stem if len(paths) == 1 else "Pictures", data)


def extract_images(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    folder = _folder(window, "Save the pictures in", ws.document.path.parent)
    if folder is None:
        return
    count = ws.document.extract_images(folder)
    if count:
        ws.notify(f"Saved {_plural(count, 'picture')}.")
        _show_folder(folder)
    else:
        messages.inform(window, "Extract images", "The document has no pictures to save.")


# Security ------------------------------------------------------------------------------


class ChangeSecurity(QUndoCommand):
    def __init__(self, document: Document, security, password: str, text: str):
        super().__init__(text)
        self._document = document
        self._before = document.security
        self._after = (security, password)

    def redo(self) -> None:
        self._document.set_security(*self._after)

    def undo(self) -> None:
        self._document.set_security(self._before)


def protect(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Password and permissions", "Encrypts the file with AES-256 when you save it. Leave the "
                    "password empty to let anyone open it, with the permissions below.", window, "Apply")
    remove = form.check("remove", "No password and no restrictions", False)
    password = form.text("password", "Password to open", password=True)
    again = form.text("again", "Password again", password=True)
    owner = form.text("owner", "Password to change permissions", placeholder="Empty: a random one", password=True)
    checks = [form.check("print", "Allow printing", True), form.check("copy", "Allow copying text and images", True),
              form.check("edit", "Allow editing", True), form.check("annotate", "Allow comments and filling in forms", True),
              form.check("assemble", "Allow adding, removing and turning pages", True)]
    remove.toggled.connect(lambda on: [w.setEnabled(not on) for w in (password, again, owner, *checks)])
    form.note("Keep the password somewhere safe: without it, nobody can open the file, not even Zenith PDF.")
    form.validate = lambda v: "" if v["remove"] or v["password"] == v["again"] else "The two passwords are different."
    values = form.run()
    if values is None:
        return
    if values["remove"]:
        ws.undo_stack.push(ChangeSecurity(document, False, "", "Remove password"))
        ws.notify("The password goes when you save.")
        return
    allow = {key: values[key] for key in ("print", "copy", "edit", "annotate", "assemble")}
    security = T.encryption(values["password"], values["owner"], allow)
    ws.undo_stack.push(ChangeSecurity(document, security, values["password"], "Set password"))
    ws.notify("Save the document to apply the password." if values["password"] else
              "Save the document to apply the permissions.")


def sanitize(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    form = ToolForm("Sanitize", "Removes things a PDF can carry besides its pages: scripts that run when it "
                    "opens, files hidden inside it, details about its author, links to websites.", window, "Remove")
    form.check("javascript", "Scripts (JavaScript)", True)
    form.check("files", "Embedded files", True)
    form.check("metadata", "Document details: title, author, software", False)
    form.check("links", "Links to websites and other files", False)
    values = form.run()
    if values is None:
        return
    counts = {}
    if _edit(ws, "Sanitize", lambda pdf: counts.update(T.sanitize(pdf, values["javascript"], values["files"],
                                                                  values["metadata"], values["links"]))):
        found = [f"{n} {label}" for label, n in (("scripts", counts.get("javascript", 0)),
                                                 ("files", counts.get("files", 0)),
                                                 ("metadata entries", counts.get("metadata", 0)),
                                                 ("links", counts.get("links", 0))) if n]
        ws.notify("Removed " + ", ".join(found) + "." if found else "Nothing to remove: the document was clean.")


def flatten(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    form = ToolForm("Flatten", "Makes markup part of the pages. Everything looks the same, but nothing can be "
                    "moved or edited any more, in any app.", window, "Flatten")
    form.choose("what", "Flatten", {"annotations": "Annotations", "forms": "Annotations and form fields"})
    values = form.run()
    if values is None:
        return
    document = ws.document
    if values["what"] == "forms":
        _edit(ws, "Flatten", T.flatten_forms)
    else:
        ids = {a.id for a in ws.store.all() if not isinstance(a, Redaction)}
        if ids:
            ws.edit_structure("Flatten", lambda annotations: document.flatten(annotations, ids))


def remove_annotations(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    found = [a for a in ws.store.all() if not isinstance(a, Redaction)]
    if not found:
        messages.inform(window, "Remove annotations", "The document has no annotations.")
        return
    if messages.confirm(window, "Remove annotations", f"Remove all {len(found)} annotations? Links and form fields "
                        "stay. Undo brings them back.", "Remove", danger=True):
        ws.undo_stack.push(RemoveAnnotations(ws.store, found, "Remove annotations"))


def find_and_redact(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    form = ToolForm("Find and redact", "Marks every place a word or phrase appears. Check the marks, then use "
                    "Apply redactions to black them out for good.", window, "Mark")
    form.text("text", "Text to find", placeholder="e.g. a name or an account number")
    form.check("case", "Match case")
    form.check("word", "Whole words only")
    values = form.run()
    if values is None or not values["text"].strip():
        return
    marks = [Redaction(page=hit.page, rects=hit.rects, author=ws.tool_settings.author)
             for hit in ws.document.iter_search(values["text"].strip(), values["case"], values["word"])]
    if not marks:
        messages.inform(window, "Find and redact", "No matches found.")
        return
    ws.undo_stack.push(AddAnnotations(ws.store, marks, "Mark for redaction"))
    ws.notify(f"Marked {len(marks)} match{'es' if len(marks) != 1 else ''}. Apply redactions to black them out.")


def apply_redactions(window: MainWindow, marks: list[Redaction] | None = None) -> None:
    """Black out `marks`, or every redaction mark of the document."""
    ws = _workspace(window)
    if ws is None:
        return
    marks = marks or [a for a in ws.store.all() if isinstance(a, Redaction)]
    if not marks:
        messages.inform(window, "Apply redactions", "There is nothing marked yet. Use the Redact tool "
                        "in the toolbar, or Find and redact.")
        return
    pages = sorted({a.page for a in marks})
    form = ToolForm("Apply redactions", f"Blacks out {len(marks)} marked area{'s' if len(marks) != 1 else ''} on "
                    f"{_plural(len(pages), 'page')}. What lies under the marks is removed from the file, not just "
                    "covered; the rest stays as it was.", window, "Redact")
    form.color("fill", "Fill colour", (0.0, 0.0, 0.0))
    form.choose("dpi", "Quality of the redacted pages", {150: "150 dpi, smaller file", 200: "200 dpi",
                                                         300: "300 dpi, sharper"}, 300)
    values = form.run()
    if values is None:
        return
    pictured = set()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        done = ws.edit_structure("Apply redactions", lambda annotations: pictured.update(
            ws.document.apply_redactions(annotations, marks, values["fill"], values["dpi"] / 72)))
    finally:
        QApplication.restoreOverrideCursor()
    if done:
        if pictured and ws.actions.ocr_available():  # those pages lost their text: it comes back searchable
            ws.actions.recognize(sorted(pictured))
        ws.notify(f"Redacted {_plural(len(pages), 'page')}.")


# Document --------------------------------------------------------------------------------


class ElidedLabel(QLabel):
    """One line of text, cut in the middle when it doesn't fit; the whole of it is the tooltip."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setToolTip(tr(text))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, rect.width())
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tr(text))


def font_summary(fonts: list[str]) -> str:
    """ "9 fonts, 3 not embedded", from describe()'s list."""
    if not fonts:
        return "None"
    missing = sum(1 for name in fonts if name.endswith(T.NOT_EMBEDDED))
    if missing:
        return f"{_plural(len(fonts), 'font')}, {missing} not embedded"
    return f"{_plural(len(fonts), 'font')}, {'all embedded' if len(fonts) > 1 else 'embedded'}"


class _Facts(QScrollArea):
    """The Properties dialog's facts: as tall as they are (`wanted`), and down to a few lines on a short screen,
    where they scroll."""

    wanted = 0

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.wanted)

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), min(self.wanted, PROPERTIES_LEAST))


class PropertiesDialog(messages.MessageDialog):
    """What the file is (read only) and its title, author, subject and keywords (editable)."""

    def __init__(self, document: Document, parent=None):
        super().__init__(parent, document.path.name, "", [("save", "Save", "primary"), (messages.CANCEL, "Cancel", "")])
        self.setWindowTitle(tr("Properties"))
        self.setFixedWidth(PROPERTIES_WIDTH)
        meta = document.metadata()
        rows = [("Location", str(document.path.parent))] + document.info()
        for key, label in (("creator", "Made with"), ("producer", "Written by"), ("created", "Created"),
                           ("modified", "Modified")):
            if meta.get(key):
                rows.append((label, _pdf_date(meta[key]) if key in ("created", "modified") else meta[key]))
        # The facts scroll on a short screen; the fields to edit, and the buttons, stay in sight under them.
        facts = QWidget()
        grid = QGridLayout(facts)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        labels = [label for label, _ in rows] + ["Keywords"]
        label_width = max(QLabel(tr(text)).sizeHint().width() for text in labels)
        grid.setColumnMinimumWidth(0, label_width)  # the values line up with the fields below
        self.font_list: QWidget | None = None
        line = 0
        for label, value in rows:
            with_button = label == "Fonts" and value != "None"
            # Beside the taller Details button the label is centred, so it sits on the summary's line.
            grid.addWidget(muted_label(label), line, 0,
                           Qt.AlignmentFlag.AlignVCenter if with_button else Qt.AlignmentFlag.AlignTop)
            if label == "Location":
                self.location = ElidedLabel(value)
                grid.addWidget(self.location, line, 1)
            elif with_button:
                grid.addWidget(self._fonts_row(value.split("\n")), line, 1)
                line += 1
                grid.addWidget(self.font_list, line, 1)
            else:
                text = QLabel(value)
                text.setWordWrap(True)
                text.setTextFormat(Qt.TextFormat.PlainText)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(text, line, 1)
            line += 1
        self._facts_box = facts
        self.facts = _Facts()
        self.facts.setFrameShape(QFrame.Shape.NoFrame)
        self.facts.setWidgetResizable(True)
        self.facts.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.facts.setWidget(facts)
        self.facts.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.add_field(self.facts)
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, label_width)
        rule = QFrame()
        rule.setObjectName("SettingLine")
        rule.setFixedHeight(1)
        grid.addWidget(rule, 0, 0, 1, 2)
        grid.setRowMinimumHeight(0, 13)
        self.fields = {}
        for line, (key, label) in enumerate((("title", "Title"), ("author", "Author"), ("subject", "Subject"),
                                             ("keywords", "Keywords")), start=1):
            self.fields[key] = QLineEdit(meta.get(key, ""))
            grid.addWidget(QLabel(tr(label)), line, 0)
            grid.addWidget(self.fields[key], line, 1)
        note = muted_label("Other apps show these in the file's properties and search results.")
        note.setWordWrap(True)
        grid.addWidget(note, 5, 1)
        self.add_field(box)
        self.fields["title"].setFocus()
        self._original = {key: meta.get(key, "") for key in self.fields}
        self._fit_screen()

    def _fit_screen(self) -> None:
        """As tall as the facts need, unless the screen is shorter (1080p at 150% leaves 672 px above the
        taskbar): then the facts scroll, and Save and Cancel stay on the screen."""
        self.ensurePolished()  # measured in the fonts the style sheet gives, not the defaults
        width = PROPERTIES_WIDTH - 48  # the dialog's margins
        needed = self._facts_box.heightForWidth(width)
        if needed < 0:
            needed = self._facts_box.sizeHint().height()
        self.facts.wanted = needed
        self.facts.setMaximumHeight(needed)
        self.facts.updateGeometry()
        self.layout().activate()
        screen = self.screen()  # its parent's, until it shows
        room = screen.availableGeometry().height() - PROPERTIES_CAPTION if screen is not None else 10**6
        excess = self.sizeHint().height() - room
        if excess > 0:
            self.facts.wanted = max(needed - excess, PROPERTIES_LEAST)
            self.facts.updateGeometry()
            self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())  # its width is fixed, so Qt would not size it itself

    def _fonts_row(self, fonts: list[str]) -> QWidget:
        """The count of fonts, and a button that shows each of them under it."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel(tr(font_summary(fonts))))
        self.fonts_button = IconButton("caret-down", "Show every font", icon_size=14, text="Details")
        self.fonts_button.setCheckable(True)
        self.fonts_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # Tab reaches it, Space opens the list
        # No taller than a line of text, so the rows keep their spacing.
        self.fonts_button.setStyleSheet("QToolButton { padding: 0 6px; }")
        self.fonts_button.setFixedHeight(20)
        self.fonts_button.toggled.connect(self._show_fonts)
        layout.addWidget(self.fonts_button)
        layout.addStretch()
        listing = QLabel(tr("\n".join(fonts)))
        listing.setObjectName("Muted")
        listing.setTextFormat(Qt.TextFormat.PlainText)
        listing.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        listing.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(listing)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setFixedHeight(min(listing.sizeHint().height() + 4, 160))
        scroll.hide()
        self.font_list = scroll
        return row

    def _show_fonts(self, shown: bool) -> None:
        self.font_list.setVisible(shown)
        self.fonts_button.set_icon_name("caret-up" if shown else "caret-down")
        self.fonts_button.setToolTip(tr("Hide the fonts" if shown else "Show every font"))
        self._fit_screen()
        if shown:
            self.facts.ensureWidgetVisible(self.font_list)

    def changes(self) -> dict | None:
        values = {key: field.text().strip() for key, field in self.fields.items()}
        return values if values != self._original else None


def _pdf_date(value: str) -> str:
    """D:20160115120000Z -> 2016-01-15 12:00"""
    match = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value)
    if not match:
        return value
    year, month, day, hour, minute = match.groups()
    text = "-".join(p for p in (year, month, day) if p)
    return f"{text} {hour}:{minute}" if hour and minute else text


def properties(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    dialog = PropertiesDialog(ws.document, window)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    values = dialog.changes() if accepted else None
    dialog.deleteLater()
    if values is not None:
        _edit(ws, "Change properties", lambda pdf: T.write_metadata(pdf, values))


class AttachmentsDialog(messages.MessageDialog):
    def __init__(self, workspace: Workspace, parent=None):
        super().__init__(parent, "Attachments", "Files carried inside the PDF. Adding or removing one is undoable.",
                         [("close", "Close", "")])
        self.setMinimumWidth(FORM_WIDTH)
        self._workspace = workspace
        self.list = QListWidget()
        self.list.setObjectName("AttachmentList")  # a framed field, so an empty list still reads as one
        self.list.setMinimumHeight(180)
        self.list.itemSelectionChanged.connect(self._sync)
        add = QPushButton(icon("plus", themes.current.accent_text), " Add files…")
        add.setObjectName("Primary")  # the dialog's one verb
        add.clicked.connect(self.add)
        self.save = QPushButton(icon("download-simple"), " Save as…")
        self.save.clicked.connect(self.save_selected)
        self.remove = QPushButton(icon("trash"), " Remove")
        self.remove.clicked.connect(self.remove_selected)
        row = QWidget()
        buttons = QHBoxLayout(row)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        for button in (add, self.save, self.remove):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        buttons.addStretch()
        self.add_field(self.list)
        self.add_field(row)
        self.list.setFocus()
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for name, size, description in self._workspace.document.attachments():
            item = QListWidgetItem(icon("paperclip"), f"{name}    {T.human_size(size)}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(tr(description or name))
            self.list.addItem(item)
        if not self.list.count():
            placeholder = QListWidgetItem(tr("No attachments"))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
        self._sync()

    def _selected(self) -> list[str]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems() if i.data(Qt.ItemDataRole.UserRole)]

    def _sync(self) -> None:
        self.save.setEnabled(len(self._selected()) == 1)
        self.remove.setEnabled(bool(self._selected()))

    def add(self) -> None:
        import pikepdf

        paths, _ = QFileDialog.getOpenFileNames(self, tr("Attach files"), str(self._workspace.document.path.parent))
        if not paths:
            return

        def operation(pdf):
            for path in paths:
                pdf.attachments[Path(path).name] = pikepdf.AttachedFileSpec.from_filepath(pdf, Path(path))

        _edit(self._workspace, "Attach files", operation)
        self.reload()

    def save_selected(self) -> None:
        names = self._selected()
        if len(names) != 1:
            return
        target, _ = QFileDialog.getSaveFileName(self, tr("Save attachment"),
                                                str(self._workspace.document.path.parent / names[0]))
        if not target:
            return
        try:
            Path(target).write_bytes(self._workspace.document.attachment_data(names[0]))
        except OSError as error:
            messages.warn(self, "Attachments", f"{names[0]} could not be saved there.", detail=str(error))

    def remove_selected(self) -> None:
        names = self._selected()
        if names:
            _edit(self._workspace, "Remove attachments", lambda pdf: [pdf.attachments.__delitem__(n) for n in names])
            self.reload()


def attachments(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is not None:
        dialog = AttachmentsDialog(ws, window)
        dialog.exec()
        dialog.deleteLater()


COMPRESSION = {
    "lossless": ("Lossless: pack the file, pictures untouched", None, None),
    "balanced": ("Balanced: pictures up to 2000 pixels, good quality", 2000, 80),
    "small": ("Small: pictures up to 1400 pixels", 1400, 65),
    "smallest": ("Smallest: pictures up to 1000 pixels, for e-mail", 1000, 50),
}


class _Relay(QObject):
    """Carries a worker thread's news to the GUI thread: signals sent across threads are queued."""

    progressed = Signal(int, int)
    finished = Signal(object, object)  # result, error


def compress_summary(before: int, after: int, left: int = 0) -> str:
    """The toast after Compress: old and new size, and how many large pictures stayed as they were."""
    if after < before:
        saving = 100 * (before - after) / before
        if saving > 99:
            share = f"now {100 * after / before:.1f}% of the original size"
        else:
            share = f"{saving:.1f}% smaller" if saving < 1 else f"{saving:.0f}% smaller"
        text = f"{T.human_size(before)} → {T.human_size(after)} ({share}) once saved."
    else:
        text = "The file was already as small as these settings make it."
    if left:
        text += f" {_plural(left, 'large picture')} of a kind Compress can't rewrite stayed as {'it was' if left == 1 else 'they were'}."
    return text


def _compress_text(done: int, total: int) -> str:
    return f"Compressing pictures, {done + 1} of {total}" if done < total else "Writing the smaller file…"


def run_compress(window: MainWindow, ws: Workspace, side: int | None, quality: int | None) -> bool:
    """Compress the document in a worker thread, with a progress dialog, as one undoable step.

    The GUI thread only writes the changed annotations into the PDF and swaps in the
    result. The worker writes the whole file out for the undo step, which takes most of
    a second on a large scan, then compresses it: the window keeps painting and Cancel
    works. True if the document changed.
    """
    if ws.busy:
        return False
    document = ws.document
    ws.view.finish_text_edit()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        document.write_annotations(ws.store.all())
    except Exception as error:
        QApplication.restoreOverrideCursor()
        messages.warn(window, "Compress", "The file could not be compressed, so nothing was changed.", detail=str(error))
        return False
    QApplication.changeOverrideCursor(Qt.CursorShape.BusyCursor)
    stop = threading.Event()
    relay = _Relay()
    outcome: dict = {}
    taken: dict = {}  # the document as it was, written out by the worker: Undo brings it back
    loop = QEventLoop()
    dialog = ProgressDialog(window, "Compress", "Reading the document…")
    dialog.on_cancel = stop.set
    # Whatever waits in the queue (the form just closed, the widgets above to polish) is done now, not in the loop
    # below: while the worker writes the file out, every call into Python there waits its turn for the GIL.
    dialog.ensurePolished()
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    reported = [0.0]

    def progress(done: int, total: int) -> None:  # in the worker thread
        now = time.monotonic()
        if done in (0, total) or now - reported[0] >= REPORT_EVERY:
            reported[0] = now
            relay.progressed.emit(done, total)

    def work() -> None:
        try:
            # The tab is busy and takes no input, so nothing else touches the PDF in memory meanwhile.
            before = taken["before"] = document.written()
            result, error = T.compress_data(before, document.password, side, quality, progress, stop), None
        except Exception as exc:  # reported on the GUI thread
            result, error = None, exc
        try:
            relay.finished.emit(result, error)
        except RuntimeError:
            pass  # the window went away meanwhile: nobody is waiting

    def finished(result, error) -> None:
        outcome.update(result=result, error=error)
        loop.quit()

    relay.progressed.connect(lambda done, total: dialog.set_progress(done, total + 1 if total else 0,
                                                                     _compress_text(done, total)))
    relay.finished.connect(finished)
    ws.busy = True
    worker = threading.Thread(target=work, name="compress", daemon=True)
    worker.start()
    try:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(round(SHOW_AFTER * 1000))
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)  # quick jobs end here, unseen
        timer.stop()
        if not outcome:
            dialog.show()
            if not outcome:
                loop.exec()
    finally:
        ws.busy = False
        dialog.finish()
        QApplication.restoreOverrideCursor()
    if not outcome:  # the application is quitting: the job stops after the picture in hand
        stop.set()
        worker.join(10)
        return False
    # The thread has sent its result and is ending: wait for that, so the relay and the loop,
    # which belong to this thread, are let go here and not over there.
    worker.join()
    result, error = outcome["result"], outcome["error"]
    if error is not None:
        messages.warn(window, "Compress", "The file could not be compressed, so nothing was changed.", detail=str(error))
        return False
    if result is None or ws not in window.workspaces:
        if result is None:
            ws.notify("Compress stopped. Nothing was changed.")
        return False
    before = taken["before"]
    if not ws.replace_structure("Compress", before, result.data):
        return False
    ws.notify(compress_summary(len(before), len(result.data), result.left))
    return True


def compress(window: MainWindow) -> None:
    ws = _workspace(window)
    if ws is None:
        return
    document = ws.document
    form = ToolForm("Compress", f"Makes the file smaller. It is {T.human_size(len(document.data))} now. Text and "
                    "drawings stay sharp; only large pictures lose detail.", window, "Compress")
    form.choose("level", "Level", {key: label for key, (label, _, _) in COMPRESSION.items()}, "balanced")
    values = form.run()
    if values is None:
        return
    _, side, quality = COMPRESSION[values["level"]]
    run_compress(window, ws, side, quality)


def repair(window: MainWindow) -> None:
    path, _ = QFileDialog.getOpenFileName(window, tr("Repair a PDF"), window.settings.last_directory,
                                          tr("PDF documents (*.pdf)"))
    if not path:
        return
    source = Path(path)
    target = source.with_name(f"{source.stem} (repaired).pdf")
    try:
        T.repair(source, target)
    except Exception as error:
        messages.warn(window, "Repair a PDF", f"{source.name} is too damaged to repair.", detail=str(error))
        return
    window.open_file(target)


TOOLS = [
    ToolInfo("page_numbers", "Add page numbers", "list-numbers", "Pages", "Number pages in a corner or the middle", page_numbers),
    ToolInfo("crop", "Crop pages", "crop", "Pages", "Trim white margins or cut the edges", crop),
    ToolInfo("resize", "Page size", "frame-corners", "Pages", "Put pages on A4, Letter or other paper", resize),
    ToolInfo("per_sheet", "Pages per sheet", "squares-four", "Pages", "2, 4 or more pages on one sheet", pages_per_sheet),
    ToolInfo("split", "Split into files", "scissors", "Pages", "Every few pages, at chosen pages or bookmarks", split),
    ToolInfo("blank", "Remove blank pages", "file-x", "Pages", "Find and drop empty pages", remove_blank),
    ToolInfo("reverse", "Reverse page order", "arrows-down-up", "Pages", "Last page first", reverse),
    ToolInfo("export_images", "Export as images", "image", "Convert", "Pages as PNG or JPEG files", export_images),
    ToolInfo("export_text", "Export text", "article", "Convert", "All the text in a .txt file", export_text),
    ToolInfo("images_to_pdf", "Images to PDF", "image-square", "Convert", "A new PDF from picture files",
             images_to_pdf, needs_document=False),
    ToolInfo("extract_images", "Extract images", "download-simple", "Convert", "Save the pictures inside", extract_images),
    ToolInfo("protect", "Password and permissions", "lock-key", "Security", "Encrypt, restrict, or unlock", protect),
    ToolInfo("redact_find", "Find and redact", "file-search", "Security", "Mark every match of a word", find_and_redact),
    ToolInfo("redact_apply", "Apply redactions", "eye-slash", "Security", "Black out marked areas for good",
             apply_redactions),
    ToolInfo("sanitize", "Sanitize", "shield-check", "Security", "Remove scripts, hidden files, metadata", sanitize),
    ToolInfo("flatten", "Flatten", "stack-simple", "Security", "Make markup and forms part of the page", flatten),
    ToolInfo("remove_annotations", "Remove annotations", "trash", "Security", "Delete all markup at once",
             remove_annotations),
    ToolInfo("properties", "Properties", "info", "Document", "Title, author, fonts, version", properties),
    ToolInfo("attachments", "Attachments", "paperclip", "Document", "Add, save or remove embedded files", attachments),
    ToolInfo("compress", "Compress", "arrows-in", "Document", "A smaller file for sharing", compress),
    ToolInfo("repair", "Repair a PDF", "first-aid-kit", "Document", "Rebuild a damaged file", repair,
             needs_document=False),
]
GROUPS = ["Pages", "Convert", "Security", "Document"]


def _caption(label: QLabel) -> QLabel:
    """9 pt muted text, as for captions everywhere."""
    label.setObjectName("Muted")
    label.setStyleSheet("font-size: 9pt;")
    return label


class ToolTile(QFrame):
    """One tool in the panel: icon, name and a line about it. All tiles share one height,
    with the text from the top, so the names in a row line up."""

    def __init__(self, tool: ToolInfo, enabled: bool, on_click):
        super().__init__()
        self.tool = tool
        self.setObjectName("PanelRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setEnabled(enabled)
        self.setToolTip(tr(tool.description if enabled else f"{tool.description}. Open a document first."))
        self._on_click = on_click
        t = themes.current
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(12)
        glyph = QLabel()
        glyph.setFixedSize(22, 22)
        glyph.setPixmap(icon(tool.icon, t.accent if enabled else t.text_muted).pixmap(22, 22))
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        name = QLabel(tr(tool.title))
        name.setObjectName("PanelRowText")
        detail = _caption(QLabel(tr(tool.description)))
        detail.setWordWrap(True)
        if not enabled:
            name.setStyleSheet(f"color: {t.text_muted};")
            self.setStyleSheet(f"#PanelRow {{ background: transparent; border-color: {t.border}; }}")
        text.addWidget(name)
        text.addWidget(detail)
        text.addStretch()
        layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        # Room for the name and two lines of caption, whatever the font (the style sheet sets it on polish).
        name.ensurePolished()
        detail.ensurePolished()
        # Two wrapped lines need 2 px more than two line spacings: a caption cut short would start higher.
        lines = name.fontMetrics().height() + 2 + 2 * detail.fontMetrics().lineSpacing() + 2
        self.setFixedSize(TILE_WIDTH, max(64, lines + 20))

    def matches(self, words: list[str]) -> bool:
        # In English and in the language shown, as the user may type either (as Settings' search does).
        t = self.tool
        text = f"{t.title} {t.description} {t.group} {tr(t.title)} {tr(t.description)} {tr(t.group)}".lower()
        return all(word in text for word in words)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self._on_click(self.tool)


class ToolsPanel(FloatingBar):
    """The list of PDF tools, under the tools button. It deletes itself when it closes."""

    def __init__(self, window: MainWindow):
        super().__init__(window, popup=True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._window = window
        self.setFixedWidth(PANEL_WIDTH)
        has_document = window.current_workspace() is not None
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText(tr("Search tools"))
        self.search.addAction(icon("magnifying-glass"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        grid = QVBoxLayout(body)
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setSpacing(6)
        self.hint = muted_label("Open a document to use these tools. Images to PDF and Repair a PDF work "
                                "without one.")
        self.hint.setWordWrap(True)
        self.hint.setVisible(not has_document)
        grid.addWidget(self.hint)
        self._groups: list[tuple[QLabel, list[ToolTile]]] = []
        for number, group in enumerate(GROUPS):
            title = QLabel(tr(group))
            title.setObjectName("SectionTitle")
            title.setContentsMargins(0, 0 if number == 0 and has_document else 12, 0, 2)
            grid.addWidget(title)
            tiles = QGridLayout()
            tiles.setSpacing(6)
            tiles.setAlignment(Qt.AlignmentFlag.AlignLeft)
            members = []
            for position, tool in enumerate(t for t in TOOLS if t.group == group):
                tile = ToolTile(tool, has_document or not tool.needs_document, self._run)
                tiles.addWidget(tile, position // 2, position % 2)
                members.append(tile)
            grid.addLayout(tiles)
            self._groups.append((title, members))
        self.empty = muted_label("No tool matches.")
        self.empty.hide()
        grid.addWidget(self.empty)
        grid.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self._scroll = scroll
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 12, 14)
        layout.setSpacing(12)
        layout.addWidget(self.search)
        layout.addWidget(scroll, 1)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        any_shown = False
        for title, tiles in self._groups:
            shown = [tile for tile in tiles if tile.matches(words)]
            for tile in tiles:
                tile.setVisible(tile in shown)
            title.setVisible(bool(shown))
            any_shown = any_shown or bool(shown)
        self.empty.setVisible(not any_shown)

    def _run(self, tool: ToolInfo) -> None:
        window = self._window
        self.close()
        tool.run(window)

    def show_below(self, anchor: QWidget) -> None:
        window = anchor.window()
        height = min(720, window.height() - 80)
        self.setFixedHeight(height)
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
        screen = anchor.screen().availableGeometry()
        self.move(max(screen.left(), min(corner.x() - PANEL_WIDTH, screen.right() - PANEL_WIDTH)), corner.y())
        self.set_backdrop(window)
        self.show()
        self.search.setFocus()


def run_tool(window: MainWindow, key: str) -> None:
    """Run a tool by key, as if picked from the panel (tests, shortcuts)."""
    next(t for t in TOOLS if t.key == key).run(window)
