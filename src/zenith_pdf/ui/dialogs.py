"""Dialogs: watermark, apply to pages, annotation properties, new documents and templates.

They share the house look of messages.MessageDialog: a heading, a muted intro,
a form with its labels on the left, and the main verb with Cancel on the right.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

from ..core.annotations import ENDINGS
from ..core.i18n import tr
from ..core.pages import PAPER_SIZES, TEMPLATES
from ..theme import qcolor, themes
from . import messages
from .widgets import muted_label

FORM_WIDTH = 480  # as the PDF tools' forms
NEW_DOCUMENT_WIDTH = 520  # four paper cards side by side
SWATCH = 16  # logical px of the colour dot on a ColorButton
CARD = QSize(112, 128)  # a paper card of the New document dialog: four and their gaps fill its 472 px
SPIN_INSET = 12  # px before a spin box's text, as in a line edit
RULING = QColor(184, 201, 227)  # the blue of the ruled templates (pages.LINE_COLOUR)
MARGIN_RED = QColor(237, 140, 140)

SHORTCUTS = [
    ("Files", [("Ctrl+O", "Open"), ("Ctrl+S", "Save"), ("Ctrl+Shift+S", "Save as"), ("Ctrl+P", "Print"),
               ("Ctrl+W", "Close tab"), ("Ctrl+Tab", "Next tab"), ("Ctrl+Shift+N", "New window"),
               ("Drag a tab out", "Open the document in a window of its own, or move it to another window")]),
    ("Viewing", [("Ctrl + wheel", "Zoom"), ("Ctrl+= / Ctrl+-", "Zoom in / out"), ("Ctrl+1", "Actual size"),
                 ("Ctrl+0 / Ctrl+2", "Fit page / fit width"), ("Ctrl+G", "Go to page"), ("Home / End", "First / last page"),
                 ("Alt+Left / Alt+Right", "Back / forward after a link, a bookmark or Ctrl+G"),
                 ("F4", "Side panel"), ("F11", "Full screen"),
                 ("Ctrl+H", "Reading mode: only the pages (Esc or Ctrl+H leaves)")]),
    ("Editing", [("V / H", "Select / pan tool"),
                 ("F6", "Go to the toolbar: arrows move, Enter turns a tool on or opens its menu"),
                 ("Space, middle button", "Pan with any tool"),
                 ("Ctrl+Z / Ctrl+Y", "Undo / redo"), ("Ctrl+C / Ctrl+X", "Copy / cut"),
                 ("Ctrl+V", "Paste annotations, pages, a picture or text"),
                 ("Del", "Delete selection"), ("Shift while drawing", "Square, circle, 45° lines"),
                 ("Esc", "Cancel, deselect, back to select tool"),
                 ("Double-click text", "Select a word, with the bar over selected text")]),
    ("Search", [("Ctrl+F", "Search"), ("F3 / Shift+F3", "Next / previous result")]),
    ("Drawing", [("Enter, double-click", "Finish a polyline, polygon, perimeter or area"),
                 ("Backspace", "Take back the last point"), ("Alt while moving", "Place freely, without snapping")]),
    ("Help", [("F1", "Guide"), ("Ctrl+K", "Assistant: find any tool, command, setting or shortcut")]),
]


class _Mixed:
    """The value of a field on which the selected annotations differ."""

    def __repr__(self) -> str:
        return "MIXED"


MIXED = _Mixed()


def common_value(annotations, name: str):
    """The value all `annotations` share for field `name`, or MIXED."""
    values = [getattr(a, name) for a in annotations]
    return values[0] if all(v == values[0] for v in values[1:]) else MIXED


# The base of every dialog here ---------------------------------------------------------


class FormDialog(messages.MessageDialog):
    """Heading, intro and a form whose rows add_row() appends; `action` names the main button.

    Callers read the values after exec(), so the dialog must outlive it: no
    WA_DeleteOnClose (exec() would delete it on return). Call deleteLater() after.
    """

    def __init__(self, parent, title: str, intro: str, action: str, width: int = FORM_WIDTH):
        super().__init__(parent, title, intro, [("ok", action, "primary"), (messages.CANCEL, "Cancel", "")])
        self.setMinimumWidth(width)
        self._box = QWidget()
        self.form = QFormLayout(self._box)
        self.form.setContentsMargins(0, 6, 0, 0)
        self.form.setHorizontalSpacing(18)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.add_field(self._box)

    def add_row(self, label: str, field):
        """A labelled row (a widget or a layout); an empty label lines the field up with the others."""
        self.form.addRow(tr(label), field)
        return field


class PointBox(QDoubleSpinBox):
    """A size in points, in steps of 0.5, written without trailing zeros: "1.5 pt", "12 pt".

    With `mixed`, its lowest value reads "Mixed": the annotations differ, and
    nothing changes until the user picks a size.
    """

    def __init__(self, value, low: float, high: float, parent=None):
        super().__init__(parent)
        mixed = value is MIXED
        _inset(self)
        self.setDecimals(2)
        self.setSingleStep(0.5)
        self.setSuffix(" pt")
        self.setRange(low - 0.5 if mixed else low, high)
        if mixed:
            self.setSpecialValueText("Mixed")
            self.setValue(self.minimum())
        else:
            self.setValue(float(value))
        self._original = value
        self._shown = self.value()  # a value out of range shows clamped, but stays as it is untouched

    def textFromValue(self, value: float) -> str:
        return f"{value:g}"

    def current(self):
        """The size the user set; until they set one, the value it started with (or MIXED)."""
        return self._original if self.value() == self._shown else self.value()


def _inset(box: QAbstractSpinBox) -> QAbstractSpinBox:
    """Start a spin box's text where a line edit's starts. The style sheet's padding doesn't
    reach the editor inside a spin box, so its text would touch the border."""
    box.lineEdit().setTextMargins(SPIN_INSET, 0, 0, 0)
    return box


def _value_row(slider: QSlider) -> tuple[QWidget, QLabel]:
    """A slider with a label beside it for its value, which the dialog writes.

    Signals go to the dialog's own methods, never to lambdas holding the
    dialog: those would keep it alive after it is gone, until Qt shuts down.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    label = QLabel()
    label.setObjectName("Muted")
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setMinimumWidth(label.fontMetrics().horizontalAdvance("Mixed") + 8)
    layout.addWidget(slider, 1)
    layout.addWidget(label)
    return row, label


# Colour --------------------------------------------------------------------------------


def swatch(color, ratio: float = 1.0, size: int = SWATCH) -> QPixmap:
    """A colour dot: a ring round the colour, a red slash for none, two halves for MIXED."""
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    dot = QRectF(1, 1, size - 2, size - 2)
    painter.setPen(Qt.PenStyle.NoPen)
    if color is MIXED:
        painter.setBrush(QColor(120, 120, 128))
        painter.drawPie(dot, 90 * 16, 180 * 16)
        painter.setBrush(QColor(214, 214, 220))
        painter.drawPie(dot, 270 * 16, 180 * 16)
    elif color is None:
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(dot)
        painter.setPen(QPen(QColor(themes.current.danger), 1.5))
        painter.drawLine(QPointF(size * 0.25, size * 0.75), QPointF(size * 0.75, size * 0.25))
    else:
        painter.setBrush(QColor.fromRgbF(*color))
        painter.drawEllipse(dot)
    # A ring that shows dark colours on the dark field, and light ones on the light field.
    painter.setPen(QPen(QColor(255, 255, 255, 90) if themes.current.name == "dark" else QColor(0, 0, 0, 60), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(dot)
    painter.end()
    return pixmap


class ColorButton(QPushButton):
    """Shows a colour and lets the user pick another. None means no colour (see clear).

    With `none_label` (e.g. "No fill") the button opens a short menu: pick a
    colour, or none. `color` may also start as MIXED, for a selection whose
    annotations differ.
    """

    changed = Signal()

    def __init__(self, color, parent=None, none_label: str = ""):
        super().__init__(parent)
        self._color = color
        self._none_label = none_label
        self.setStyleSheet("text-align: left; padding-left: 10px;")  # the dot first, as in a field
        self.clicked.connect(self._pressed)
        self._show()
        themes.changed.connect(self._show)

    @property
    def color(self):
        return self._color

    def set_color(self, color) -> None:
        self._color = color
        self._show()
        self.changed.emit()

    def clear(self) -> None:
        self.set_color(None)

    def _show(self, *_) -> None:
        self.setIcon(QIcon(swatch(self._color, self.devicePixelRatioF())))
        self.setIconSize(QSize(SWATCH, SWATCH))
        if self._color is MIXED:
            self.setText(tr("Mixed"))
        elif self._color is None:
            self.setText(tr(self._none_label or "None"))
        else:
            self.setText(QColor.fromRgbF(*self._color).name().upper())

    def _pressed(self) -> None:
        if not self._none_label:
            self.pick()
            return
        menu = QMenu(self)
        menu.addAction(tr("Choose a colour…"), self.pick)
        menu.addAction(tr(self._none_label), self.clear)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        menu.deleteLater()

    def pick(self) -> None:
        start = QColor.fromRgbF(*self._color) if isinstance(self._color, tuple) else QColor("white")
        color = QColorDialog.getColor(start, self, "Choose colour")
        if color.isValid():
            self.set_color((color.redF(), color.greenF(), color.blueF()))


# Watermark -----------------------------------------------------------------------------


class WatermarkPreview(QWidget):
    """The watermark on a small white page, as it will look. The dialog sets `options`."""

    def __init__(self, options: dict):
        super().__init__()
        self.options = options
        self.setFixedHeight(156)
        self.setMinimumWidth(160)

    def paintEvent(self, event) -> None:
        t = themes.current
        options = self.options
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        height = self.height() - 2
        page = QRectF((self.width() - height * 0.75) / 2, 1, height * 0.75, height)  # about A4 upright
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(QColor(t.paper))
        painter.drawRoundedRect(page, 4, 4)
        painter.setPen(QPen(QColor(200, 200, 206), 1.0))
        for i in range(9):  # a few grey lines of text
            y = page.top() + 16 + i * 14
            painter.drawLine(QPointF(page.left() + 10, y), QPointF(page.right() - 10 - (i % 3) * 8, y))
        scale = page.width() / 595  # the dialog's sizes are in points on an A4 page
        font = QFont("Helvetica")
        font.setPixelSize(max(1, round(options["font_size"] * scale)))
        painter.setFont(font)
        color = QColor.fromRgbF(*options["color"])
        color.setAlphaF(options["opacity"])
        painter.setPen(color)
        painter.setClipRect(page)
        painter.translate(page.center())
        painter.rotate(-options["angle"])  # counter-clockwise as the reader sees it
        width = painter.fontMetrics().horizontalAdvance(options["text"])
        painter.drawText(QPointF(-width / 2, painter.fontMetrics().capHeight() / 2), options["text"])


class WatermarkDialog(FormDialog):
    def __init__(self, parent=None):
        super().__init__(parent, "Watermark", "Text across the middle of the page, over what is there. It becomes "
                         "part of the page and can only be undone, not moved.", "Add")
        self._color = (140 / 255, 140 / 255, 150 / 255)
        self.text_field = QLineEdit("DRAFT")
        self.size_box = _inset(QSpinBox())
        self.size_box.setRange(12, 300)
        self.size_box.setValue(72)
        self.size_box.setSuffix(" pt")
        self.angle_box = _inset(QSpinBox())
        self.angle_box.setRange(-90, 90)
        self.angle_box.setValue(45)
        self.angle_box.setSuffix("°")
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(22)
        self.color_button = ColorButton(self._color)
        self.all_pages = QCheckBox(tr("Put it on every page"))
        self.preview = WatermarkPreview(self.options())
        opacity, self.opacity_label = _value_row(self.opacity_slider)

        self.add_row("", self.preview)
        self.add_row("Text", self.text_field)
        self.add_row("Size", self.size_box)
        self.add_row("Angle", self.angle_box)
        self.add_row("Opacity", opacity)
        self.add_row("Colour", self.color_button)
        self.add_row("", self.all_pages)
        for signal in (self.text_field.textChanged, self.size_box.valueChanged, self.angle_box.valueChanged,
                       self.opacity_slider.valueChanged, self.color_button.changed):
            signal.connect(self._changed)
        self._changed()
        self.text_field.setFocus()
        self.text_field.selectAll()

    def _changed(self, *_) -> None:
        self.opacity_label.setText(tr(f"{self.opacity_slider.value()}%"))
        self.preview.options = self.options()
        self.preview.update()

    def options(self) -> dict:
        color = self.color_button.color
        return {
            "text": self.text_field.text().strip() or "DRAFT",
            "font_size": float(self.size_box.value()),
            "angle": float(self.angle_box.value()),
            "opacity": self.opacity_slider.value() / 100,
            "color": color if isinstance(color, tuple) else self._color,
            "all_pages": self.all_pages.isChecked(),
        }


# Apply to pages ------------------------------------------------------------------------


def parse_pages(text: str, count: int) -> list[int]:
    """ "1-3, 7" -> [0, 1, 2, 6], clipped to the document. Raises ValueError on nonsense."""
    pages: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        start, end = int(first), int(last) if last else int(first)
        if start > end:
            start, end = end, start
        pages.extend(p - 1 for p in range(start, end + 1) if 1 <= p <= count)
    return sorted(set(pages))


class ApplyToPagesDialog(FormDialog):
    def __init__(self, count: int, current: int, parent=None):
        super().__init__(parent, "Apply to pages", "Copies of the selection go to the same place on each page. "
                         "Text markup stays with its text.", "Copy")
        self._count = count
        self._current = current
        # Not "choice": MessageDialog keeps the button pressed under that name.
        self.pages_box = QComboBox()
        for key, label in (("all", "All pages"), ("odd", "Odd pages"), ("even", "Even pages"), ("range", "Pages…")):
            self.pages_box.addItem(tr(label), key)
        self.range = QLineEdit()
        self.range.setPlaceholderText(tr(f"e.g. 1-3, 7 (of {count})"))
        self.range.setEnabled(False)
        self.problem = muted_label("")
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color: {themes.current.danger};")
        self.problem.hide()
        self.pages_box.currentIndexChanged.connect(self._on_choice)
        self.range.textChanged.connect(self._typed)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.pages_box)
        row.addWidget(self.range, 1)
        self.add_row("Copy to", row)
        self.add_row("", self.problem)
        self.pages_box.setFocus()

    def _typed(self, *_) -> None:
        self.problem.hide()

    def _on_choice(self, *_) -> None:
        picking = self.pages_box.currentData() == "range"
        self.range.setEnabled(picking)
        self.problem.hide()
        if picking:
            self.range.setFocus()

    def pages(self) -> list[int]:
        key = self.pages_box.currentData()
        if key == "odd":
            return list(range(0, self._count, 2))
        if key == "even":
            return list(range(1, self._count, 2))
        if key == "range":
            return parse_pages(self.range.text(), self._count)
        return list(range(self._count))

    def accept(self) -> None:
        try:
            chosen = self.pages()
        except ValueError:
            chosen = []
        if not chosen:  # say so here, and let the user fix it, instead of closing on nonsense
            self.problem.setText(tr(f"No such pages: the document has {self._count}. Write them like 1-3, 7."))
            self.problem.show()
            self.range.setFocus()
            return
        super().accept()


# Annotation properties -----------------------------------------------------------------


ENDING_NAMES = {"None": "None", "Square": "Square", "Circle": "Circle", "Diamond": "Diamond",
                "OpenArrow": "Open arrow", "ClosedArrow": "Closed arrow", "Butt": "Bar across",
                "ROpenArrow": "Open arrow, reversed", "RClosedArrow": "Closed arrow, reversed", "Slash": "Slash"}
DASH_NAMES = {"solid": "Solid", "dotted": "Dotted", "dashed": "Dashed", "dashdot": "Dash and dot"}


FONT_LABELS = {"Helvetica": "Sans serif", "Times": "Serif", "Courier": "Monospace"}
ALIGN_LABELS = {"left": "Left", "center": "Centre", "right": "Right"}


class PropertiesDialog(FormDialog):
    """Edit what the selected annotations have in common.

    Only the fields the user changes are applied (see changes()), so a field
    shown as "Mixed" keeps each annotation's own value, and opening and
    confirming the dialog changes nothing.
    """

    def __init__(self, annotations, parent=None):
        from ..core.annotations import FreeText, ImageStamp, Ink, Line, Measurement, Poly, Redaction, Shape, Tape

        first = annotations[0]
        single = len(annotations) == 1

        def has(name: str) -> bool:
            return all(hasattr(a, name) for a in annotations)

        if single:
            intro = f"{first.label} on page {first.page + 1}."
        else:
            intro = (f"{len(annotations)} annotations. Fields marked Mixed keep each one's own value "
                     "unless you change them.")
        super().__init__(parent, "Properties", intro, "Apply")
        self._initial: dict[str, object] = {}  # field -> the value it started with (or MIXED)
        self._readers: dict[str, object] = {}  # field -> what its control holds now

        if single:
            contents = first.contents.replace("\r\n", "\n").replace("\r", "\n")  # as the text field gives it back
            self.contents_field = QPlainTextEdit(contents)
            self.contents_field.setTabChangesFocus(True)
            self.contents_field.setFixedHeight(self.contents_field.fontMetrics().lineSpacing() * 3 + 18)
            self._watch("contents", contents, self.contents_field.toPlainText)
            self.add_row("Text" if isinstance(first, FreeText) else "Comment", self.contents_field)
            self.author_field = QLineEdit(first.author)
            self._watch("author", first.author, self.author_field.text)
            self.add_row("Author", self.author_field)
            self.subject_field = QLineEdit(first.subject)
            self.subject_field.setPlaceholderText(tr(first.label))
            self._watch("subject", first.subject.strip(), lambda: self.subject_field.text().strip())
            self.add_row("Subject", self.subject_field)

        self.color_button = None
        if not all(isinstance(a, ImageStamp) for a in annotations):  # a picture has its own colours
            color = common_value(annotations, "color")
            self.color_button = ColorButton(color)
            self._watch("color", color, lambda: self.color_button.color)
            if all(isinstance(a, FreeText) for a in annotations):
                label = "Text colour"
            elif all(isinstance(a, Shape) or (isinstance(a, Poly) and a.closed) for a in annotations):
                label = "Border"
            else:
                label = "Colour"
            self.add_row(label, self.color_button)

        self.fill_button = None
        if has("fill"):
            fill = common_value(annotations, "fill")
            # A redaction's fill is what replaces the hidden content: it can't be none.
            optional = not any(isinstance(a, Redaction) for a in annotations)
            self.fill_button = ColorButton(fill, none_label="No fill" if optional else "")
            self._watch("fill", fill, lambda: self.fill_button.color)
            self.add_row("Fill", self.fill_button)

        self._opacity = common_value(annotations, "opacity")
        self._opacity_moved = False
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(100 if self._opacity is MIXED else round(self._opacity * 100))
        self.opacity_slider.valueChanged.connect(self._opacity_moved_to)
        row, self.opacity_label = _value_row(self.opacity_slider)
        self._watch("opacity", self._opacity, self._opacity_now)
        self.add_row("Opacity", row)
        self._show_opacity()

        self.width_box = None
        if all(isinstance(a, (Shape, Line, Ink, Poly, Measurement, Tape)) for a in annotations):
            width = common_value(annotations, "width")
            self.width_box = PointBox(width, 0.5, 100)
            self._watch("width", width, self.width_box.current)
            self.add_row("Thickness", self.width_box)

        self.dash_box = None
        if has("dash") and not any(getattr(a, "cloudy", False) for a in annotations):  # a cloud has its own edge
            self.dash_box = self._choices(common_value(annotations, "dash"), DASH_NAMES)
            self._watch("dash", common_value(annotations, "dash"), self.dash_box.currentData)
            self.add_row("Line style", self.dash_box)

        self.start_box = self.end_box = None
        if all(isinstance(a, Line) or (isinstance(a, Poly) and not a.closed) for a in annotations):
            names = {key: ENDING_NAMES[key] for key in ENDINGS}
            for name, label in (("head_start", "Start"), ("head_end", "End")):
                value = common_value(annotations, name)
                box = self._choices(value, names)
                self._watch(name, value, box.currentData)
                self.add_row(label, box)
                if name == "head_start":
                    self.start_box = box
                else:
                    self.end_box = box

        self.font_box = None
        if has("font_size"):
            size = common_value(annotations, "font_size")
            self.font_box = PointBox(size, 4, 400)
            self._watch("font_size", size, self.font_box.current)
            self.add_row("Font size", self.font_box)

        self.face_box = self.align_box = None
        self.style_boxes: dict[str, QCheckBox] = {}
        if all(isinstance(a, FreeText) for a in annotations):
            font = common_value(annotations, "font")
            self.face_box = self._choices(font, FONT_LABELS)
            self._watch("font", font, self.face_box.currentData)
            self.add_row("Font", self.face_box)
            row = QHBoxLayout()
            row.setSpacing(14)
            for name, label in (("bold", "Bold"), ("italic", "Italic"), ("underline", "Underline")):
                self.style_boxes[name] = box = self._check(name, label, common_value(annotations, name))
                row.addWidget(box)
            row.addStretch()
            self.add_row("Style", row)
            align = common_value(annotations, "align")
            self.align_box = self._choices(align, ALIGN_LABELS)
            self._watch("align", align, self.align_box.currentData)
            self.add_row("Alignment", self.align_box)

        self.locked_box = self._check("locked", "Locked: can't be moved, resized or deleted",
                                      common_value(annotations, "locked"))
        self.add_row("", self.locked_box)

    def _check(self, name: str, label: str, value) -> QCheckBox:
        """A tick for a yes-or-no field; for a mixed one, half ticked until clicked, then on or off for all."""
        box = QCheckBox(tr(label))
        if value is MIXED:
            box.setTristate(True)
            box.setCheckState(Qt.CheckState.PartiallyChecked)
            box.clicked.connect(lambda *_: box.setTristate(False))
        else:
            box.setChecked(value)

        def now():
            state = box.checkState()
            return MIXED if state == Qt.CheckState.PartiallyChecked else state == Qt.CheckState.Checked

        self._watch(name, value, now)
        return box

    def _opacity_now(self):
        # Untouched, it keeps the exact value it showed rounded (0.333 is not 33%).
        return self.opacity_slider.value() / 100 if self._opacity_moved else self._opacity

    def _opacity_moved_to(self, *_) -> None:
        self._opacity_moved = True
        self._show_opacity()

    def _show_opacity(self) -> None:
        mixed = self._opacity_now() is MIXED
        self.opacity_label.setText(tr("Mixed" if mixed else f"{self.opacity_slider.value()}%"))

    def _choices(self, value, names: dict[str, str]) -> QComboBox:
        box = QComboBox()
        if value is MIXED:
            box.addItem(tr("Mixed"), MIXED)
        for key, label in names.items():
            box.addItem(tr(label), key)
        if value is not MIXED and value not in names:  # from another app: shown as it is, kept unless changed
            box.addItem(str(value), value)
        box.setCurrentIndex(0 if value is MIXED else max(box.findData(value), 0))
        return box

    def _watch(self, name: str, initial, now) -> None:
        self._initial[name] = initial
        self._readers[name] = now

    def changes(self) -> dict:
        """The fields the user changed, with their new values; each annotation takes the ones it has."""
        values = {}
        for name, now in self._readers.items():
            value = now()
            if value is MIXED or value == self._initial[name]:
                continue
            values[name] = value
        return values


# New documents and templates -----------------------------------------------------------


def paper_lines(painter: QPainter, kind: str, page: QRectF) -> None:
    """Ruling of a template drawn small, as a picture of it rather than to scale."""
    painter.save()
    painter.setClipRect(page)
    if kind == "lined":
        painter.setPen(QPen(RULING, 1.0))
        y = page.top() + page.height() * 0.16
        while y < page.bottom() - 4:
            painter.drawLine(QPointF(page.left() + 4, y), QPointF(page.right() - 4, y))
            y += 7
        painter.setPen(QPen(MARGIN_RED, 1.0))
        x = page.left() + page.width() * 0.2
        painter.drawLine(QPointF(x, page.top()), QPointF(x, page.bottom()))
    elif kind == "grid":
        painter.setPen(QPen(RULING, 0.8))
        step = 6.0
        x = page.left() + step
        while x < page.right():
            painter.drawLine(QPointF(x, page.top()), QPointF(x, page.bottom()))
            x += step
        y = page.top() + step
        while y < page.bottom():
            painter.drawLine(QPointF(page.left(), y), QPointF(page.right(), y))
            y += step
    elif kind == "dotted":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(RULING.darker(115))
        step = 6.0
        y = page.top() + step
        while y < page.bottom():
            x = page.left() + step
            while x < page.right():
                painter.drawEllipse(QPointF(x, y), 0.9, 0.9)
                x += step
            y += step
    painter.restore()


class PaperCard(QAbstractButton):
    """One kind of paper in the New document dialog: a picture of the page and its name."""

    def __init__(self, kind: str, title: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setText(tr(title))
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tr(TEMPLATES[kind]))
        self.landscape = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return CARD

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            fill = QColor(t.accent)
            fill.setAlphaF(0.16)
            painter.setPen(QPen(QColor(t.accent), 2.0))
            painter.setBrush(fill)
        else:
            painter.setPen(QPen(QColor(t.border), 1.0))
            painter.setBrush(qcolor(t.hover) if self.underMouse() else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, 10, 10)

        label_height = self.fontMetrics().height() + 12
        room = frame.adjusted(14, 12, -14, -label_height)
        ratio = math.sqrt(2) if not self.landscape else 1 / math.sqrt(2)  # height / width, as A paper
        width = min(room.width(), room.height() / ratio)
        page = QRectF(room.center().x() - width / 2, room.center().y() - width * ratio / 2, width, width * ratio)
        painter.setPen(QPen(QColor(0, 0, 0, 40), 1.0))
        painter.setBrush(QColor(t.paper))
        painter.drawRect(page)
        paper_lines(painter, self.kind, page.adjusted(1, 1, -1, -1))

        font = QFont(self.font())
        if self.isChecked():
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(t.text if self.isChecked() else t.text_muted))
        painter.drawText(QRectF(frame.left(), frame.bottom() - label_height, frame.width(), label_height - 4),
                         Qt.AlignmentFlag.AlignCenter, self.text())


class NewDocumentDialog(FormDialog):
    """Paper, orientation, ruling and length of a new document; optionally kept as a template."""

    def __init__(self, template: dict | None = None, editing_template: bool = False, parent=None):
        from .insert_dialogs import Segments

        template = template or {}
        if editing_template:
            title, intro, action = "Edit template", "A template starts new documents with these pages.", "Save"
        else:
            title, intro, action = ("New document", "Pick the paper. To start the same way another time, keep it "
                                    "as a template.", "Create")
        super().__init__(parent, title, intro, action, NEW_DOCUMENT_WIDTH)
        self.name_field = QLineEdit(template.get("name", tr("Untitled")))

        self.cards = QButtonGroup(self)
        cards = QHBoxLayout()
        cards.setSpacing(8)
        for kind, label in TEMPLATES.items():
            card = PaperCard(kind, label.replace(" page", ""))
            self.cards.addButton(card)
            cards.addWidget(card)
        chosen = template.get("kind", "blank")
        for card in self.cards.buttons():
            card.setChecked(card.kind == chosen)
        if self.cards.checkedButton() is None:
            self.cards.buttons()[0].setChecked(True)

        self.paper = QComboBox()
        for name in PAPER_SIZES:
            self.paper.addItem(tr(name), name)  # kept by its key: page_size knows "Letter", not "Lettre US"
        self.paper.setCurrentIndex(max(self.paper.findData(template.get("paper", "A4")), 0))
        self.orientation = Segments(["Portrait", "Landscape"])
        self.orientation.set_current(1 if template.get("orientation") == "landscape" else 0)
        self.orientation.changed.connect(self._turn_cards)
        self.count = _inset(QSpinBox())
        self.count.setRange(1, 500)
        self.count.setValue(min(max(int(template.get("pages", 1)), 1), 500))
        self.keep = QCheckBox(tr("Save as a template"), self)  # owned even when the form leaves it out
        self.keep.setChecked(editing_template)
        self.keep.setVisible(not editing_template)

        self.form.addRow(cards)  # the whole width: four cards don't fit beside the labels
        self.add_row("Name", self.name_field)
        self.add_row("Paper", self.paper)
        self.add_row("Orientation", self.orientation)
        self.add_row("Pages", self.count)
        if not editing_template:
            self.add_row("", self.keep)
        self._turn_cards()
        self.name_field.setFocus()
        self.name_field.selectAll()

    def _turn_cards(self, *_) -> None:
        for card in self.cards.buttons():
            card.landscape = self.orientation.current() == 1
            card.update()

    def kind(self) -> str:
        return self.cards.checkedButton().kind

    def spec(self) -> dict:
        return {
            "name": self.name_field.text().strip() or tr("Untitled"),
            "paper": self.paper.currentData(),
            "orientation": "landscape" if self.orientation.current() == 1 else "portrait",
            "kind": self.kind(),
            "pages": self.count.value(),
        }


def page_size(spec: dict) -> tuple[float, float]:
    width, height = PAPER_SIZES.get(spec.get("paper", "A4"), PAPER_SIZES["A4"])
    return (height, width) if spec.get("orientation") == "landscape" else (width, height)


def template_summary(template: dict) -> str:
    """ "A4 portrait, lined, 5 pages" """
    kind = TEMPLATES.get(template.get("kind", "blank"), "Blank page").replace(" page", "").lower()
    pages = int(template.get("pages", 1))
    return f"{template.get('paper', 'A4')} {template.get('orientation', 'portrait')}, {kind}, {pages} page{'s' if pages != 1 else ''}"


def edit_template(settings, position: int | None, parent=None) -> None:
    """Edit the template at `position`, or make a new one when it's None."""
    templates = settings.templates
    editing = isinstance(position, int) and 0 <= position < len(templates)
    start = templates[position] if editing else {"name": "New template"}
    dialog = NewDocumentDialog(start, editing_template=True, parent=parent)
    accepted = dialog.exec() == NewDocumentDialog.DialogCode.Accepted
    spec = dialog.spec()
    dialog.deleteLater()
    if not accepted:
        return
    if editing:
        templates[position] = spec
    else:
        templates.append(spec)
    settings.templates = templates


def delete_template(settings, position: int) -> None:
    templates = settings.templates
    if 0 <= position < len(templates):
        del templates[position]
        settings.templates = templates
