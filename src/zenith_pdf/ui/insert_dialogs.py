"""Dialogs of the insert and measure tools: links, measuring scale, equations, signatures, camera."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QPointF, QRectF, QSize, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import images, mathtext
from ..core.annotations import REAL_UNITS, UNITS
from ..icons import icon
from ..theme import themes
from .page_actions import IMAGE_FILES
from .widgets import Segmented, Switch, muted_label

DIALOG_WIDTH = 440  # the narrowest any dialog opens
PAPER_TEXT = "#6b6c75"  # hints on the white previews, in both themes
PAPER_DANGER = "#d93a2f"  # the light theme's danger red: the previews are always white
SCRIPT_FONTS = ("Segoe Script", "Ink Free", "Lucida Handwriting", "Brush Script MT", "Mistral", "Freestyle Script",
                "Edwardian Script ITC", "Vladimir Script", "Segoe Print", "Bradley Hand ITC", "Gabriola")
INK_COLOURS = {"Black": (0.05, 0.05, 0.08), "Blue": (0.07, 0.2, 0.55), "Dark grey": (0.25, 0.25, 0.3)}
SCALES = (("Life size", (1, "cm", 1, "cm")), ("1:50", (1, "cm", 50, "cm")), ("1:100", (1, "cm", 1, "m")),
          ("1:500", (1, "cm", 5, "m")), ("1:1000", (1, "cm", 10, "m")), ("1:25 000 map", (1, "cm", 250, "m")))
# (name, what the button shows, what it inserts)
EXAMPLES = [
    ("Fraction", "a/b", r"\frac{a}{b}"), ("Power", "x²", r"x^{2}"), ("Index", "xᵢ", r"x_{i}"),
    ("Root", "√x", r"\sqrt{x}"), ("Sum", "Σ", r"\sum_{i=1}^{n} x_i"), ("Integral", "∫", r"\int_{a}^{b} f(x)\,dx"),
    ("Limit", "lim", r"\lim_{x \to 0}"), ("Brackets", "( )", r"\left( x \right)"), ("Vector", "v⃗", r"\vec{v}"),
    ("Greek", "αβγ", r"\alpha \beta \gamma \pi \theta"), ("Less or equal", "≤", r"\leq"),
    ("Approximately", "≈", r"\approx"), ("Infinity", "∞", r"\infty"), ("Text", "Tt", r"\text{ if }"),
]


# The house look: a heading, a muted intro, rows 12 apart, and the main verb and Cancel on the right.


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("DialogTitle")
    return label


def _intro(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("DialogBody")
    label.setWordWrap(True)
    return label


def _layout(dialog: QDialog, title: str, intro: str = "") -> QVBoxLayout:
    """The dialog's column, with its heading and intro already in it."""
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(DIALOG_WIDTH)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(12)
    top = QVBoxLayout()
    top.setSpacing(8)  # the intro belongs to the heading
    top.addWidget(_heading(title))
    if intro:
        top.addWidget(_intro(intro))
    layout.addLayout(top)
    layout.addSpacing(4)
    return layout


def _paper_label(text: str = "") -> QLabel:
    """A preview on white paper, as it will be on the page; its own text is grey whatever the theme."""
    label = QLabel(text)
    label.setObjectName("EquationPreview")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(f"color: {PAPER_TEXT};")
    return label


def _footer(dialog: QDialog, layout: QVBoxLayout, action: str, *left: QWidget) -> QPushButton:
    """The button row: `left` widgets, then the main button (Enter presses it) and Cancel. Returns the main one."""
    row = QHBoxLayout()
    row.setSpacing(8)
    for widget in left:
        row.addWidget(widget)
    row.addStretch()
    ok = QPushButton(action)
    ok.setObjectName("Primary")
    ok.setAutoDefault(False)
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    cancel = QPushButton("Cancel")
    cancel.setAutoDefault(False)
    cancel.clicked.connect(dialog.reject)
    row.addWidget(ok)
    row.addWidget(cancel)
    layout.addSpacing(8)  # 20 above the buttons
    layout.addLayout(row)
    return ok


def _field_label(text: str, width: int) -> QLabel:
    """A form label of a set width, so fields line up across the pages of a dialog."""
    label = QLabel(text)
    label.setFixedWidth(width)
    return label


# The segmented control that switches a dialog between ways of doing one thing (a link to a web
# address or to a page; a signature typed, drawn or from a picture) is the app's one, in widgets.
Segments = Segmented


# Links ------------------------------------------------------------------------------


class LinkDialog(QDialog):
    """Where a link goes: a web address or a page of this document."""

    def __init__(self, page_count: int, uri: str = "", target: int = -1, existing: bool = False, parent=None):
        super().__init__(parent)
        self.removed = False
        layout = _layout(self, "Edit link" if existing else "Link",
                         "Clicking the area opens a web page or goes to a page.")
        self.kind = Segments(["Web address", "Page of this document"])
        self.web, self.page = self.kind.buttons
        self.address = QLineEdit(uri)
        self.address.setPlaceholderText("example.org or name@example.org")
        self.number = QSpinBox()
        self.number.setRange(1, max(1, page_count))
        self.number.setValue(target + 1 if target >= 0 else 1)
        self.number.setMinimumWidth(96)
        self.ensurePolished()  # measure with the style sheet's font
        width = max(self.fontMetrics().horizontalAdvance(text) for text in ("Address", "Page")) + 4
        web = QWidget()
        web_row = QHBoxLayout(web)
        web_row.setContentsMargins(0, 0, 0, 0)
        web_row.setSpacing(8)
        web_row.addWidget(_field_label("Address", width))
        web_row.addWidget(self.address, 1)
        page = QWidget()
        page_row = QHBoxLayout(page)
        page_row.setContentsMargins(0, 0, 0, 0)
        page_row.setSpacing(8)
        page_row.addWidget(_field_label("Page", width))
        page_row.addWidget(self.number)
        page_row.addWidget(muted_label(f"of {max(1, page_count)}"))
        page_row.addStretch()
        self.fields = QStackedWidget()
        self.fields.addWidget(web)
        self.fields.addWidget(page)
        (self.page if target >= 0 and not uri else self.web).setChecked(True)
        layout.addWidget(self.kind)
        layout.addWidget(self.fields)
        left = []
        if existing:
            remove = QPushButton("Remove link")
            remove.setObjectName("DangerText")
            remove.setAutoDefault(False)
            remove.clicked.connect(self._remove)
            left.append(remove)
        self.ok_button = _footer(self, layout, "Save" if existing else "Add link", *left)
        self.kind.changed.connect(self._sync)
        self.address.textChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_) -> None:
        on_page = self.page.isChecked()
        self.fields.setCurrentIndex(1 if on_page else 0)
        self.address.setEnabled(not on_page)
        self.number.setEnabled(on_page)
        self.ok_button.setEnabled(on_page or bool(self.address.text().strip()))
        (self.number if on_page else self.address).setFocus()

    def _remove(self) -> None:
        self.removed = True
        self.accept()

    def result_link(self) -> tuple[str, int]:
        """(address, -1) or ("", page index)."""
        if self.page.isChecked():
            return "", self.number.value() - 1
        address = self.address.text().strip()
        if address and "@" in address and "://" not in address and not address.startswith("mailto:"):
            address = "mailto:" + address
        elif address and "://" not in address and not address.startswith("mailto:"):
            address = "https://" + address
        return address, -1


# Measuring scale --------------------------------------------------------------------


class ScaleDialog(QDialog):
    """The scale of a drawing, for measurements: 1 cm on paper is 1 m in reality, and so on."""

    def __init__(self, measure: tuple, parent=None):
        super().__init__(parent)
        layout = _layout(self, "Measuring scale", "New measurements use this scale.")
        paper, paper_unit, real, real_unit = measure
        self.paper = QDoubleSpinBox()
        self.paper.setRange(0.001, 100000)
        self.paper.setDecimals(3)
        self.paper.setValue(paper)
        self.paper_unit = QComboBox()
        self.paper_unit.addItems(list(UNITS))
        self.paper_unit.setCurrentText(paper_unit)
        self.real = QDoubleSpinBox()
        self.real.setRange(0.001, 10**9)
        self.real.setDecimals(3)
        self.real.setValue(real)
        self.real_unit = QComboBox()
        self.real_unit.addItems(list(REAL_UNITS))
        self.real_unit.setCurrentText(real_unit)
        self.presets = QComboBox()
        for label, values in SCALES:
            self.presets.addItem(label, values)
        self.presets.addItem("Custom", None)
        self.presets.activated.connect(self._preset)  # only the user's own picks, not _match's
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Scale"), 0, 0)
        grid.addWidget(self.presets, 0, 1, 1, 2)
        grid.addWidget(QLabel("On the page"), 1, 0)
        grid.addWidget(self.paper, 1, 1)
        grid.addWidget(self.paper_unit, 1, 2)
        grid.addWidget(QLabel("In reality"), 2, 0)
        grid.addWidget(self.real, 2, 1)
        grid.addWidget(self.real_unit, 2, 2)
        layout.addLayout(grid)
        _footer(self, layout, "Use this scale")
        for box in (self.paper, self.real):
            box.valueChanged.connect(self._match)
        for box in (self.paper_unit, self.real_unit):
            box.currentTextChanged.connect(self._match)
        self._match()

    def _preset(self, index: int) -> None:
        values = self.presets.itemData(index)
        if values:
            paper, paper_unit, real, real_unit = values
            self.paper.setValue(paper)
            self.paper_unit.setCurrentText(paper_unit)
            self.real.setValue(real)
            self.real_unit.setCurrentText(real_unit)

    def _match(self, *_) -> None:
        """Show the common scale the values make, or Custom."""
        paper, paper_unit, real, real_unit = self.measure()
        index = self.presets.count() - 1
        for number, (_, (p, pu, r, ru)) in enumerate(SCALES):
            if (pu, ru) == (paper_unit, real_unit) and abs(p - paper) < 1e-9 and abs(r - real) < 1e-9:
                index = number
        self.presets.setCurrentIndex(index)

    def measure(self) -> tuple:
        return self.paper.value(), self.paper_unit.currentText(), self.real.value(), self.real_unit.currentText()


# Equations --------------------------------------------------------------------------


class EquationDialog(QDialog):
    """Type an equation in LaTeX and see it as it will look."""

    def __init__(self, source: str = "", size: float = 16.0, color=(0.0, 0.0, 0.0), parent=None):
        super().__init__(parent)
        layout = _layout(self, "Equation", "Written like LaTeX: ^ and _ for powers and indices, braces to group, "
                                           "\\frac, \\sqrt, \\sum, \\int, Greek letters by name.")
        self.setMinimumWidth(620)
        self._color = color
        self.source = QPlainTextEdit(source)
        self.source.setPlaceholderText(r"e.g. x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}")
        self.source.setStyleSheet('font-family: "Cascadia Mono", Consolas, monospace; font-size: 11pt;')
        self.source.setTabChangesFocus(True)
        self.source.setFixedHeight(84)
        self.source.installEventFilter(self)
        self.size_box = QSpinBox()  # not self.size: that would hide QWidget.size()
        self.size_box.setRange(6, 96)
        self.size_box.setSuffix(" pt")
        self.size_box.setValue(round(size))
        self.size_box.setMinimumWidth(96)
        self.preview = _paper_label()
        self.preview.setMinimumHeight(150)
        self.preview.setWordWrap(True)
        self.problem = ""  # why the source can't be drawn, shown in the preview's place
        snippets = QWidget()
        # Symbols read better in the maths font, and short labels never clip; the name is in the tooltip.
        snippets.setStyleSheet('QPushButton { font-family: "Cambria Math", "Segoe UI"; font-size: 12pt; '
                               'padding: 3px 4px; }')
        grid = QGridLayout(snippets)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for number, (name, label, text) in enumerate(EXAMPLES):
            button = QPushButton(label)
            button.setToolTip(f"{name}: {text}")
            button.setAccessibleName(name)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _=False, t=text: self._insert(t))
            grid.addWidget(button, number // 7, number % 7)
        options = QHBoxLayout()
        options.setSpacing(8)
        options.addWidget(QLabel("Size"))
        options.addWidget(self.size_box)
        options.addStretch()
        layout.addWidget(self.source)
        layout.addWidget(snippets)
        layout.addLayout(options)
        layout.addWidget(self.preview, 1)
        self.ok_button = _footer(self, layout, "Place on the page" if not source else "Save")
        self.ok_button.setToolTip("Ctrl+Enter")
        self.source.textChanged.connect(self._render)
        self.size_box.valueChanged.connect(self._render)
        self._render()
        self.source.setFocus()

    def eventFilter(self, watched, event) -> bool:
        # In the source Enter starts a line, so Ctrl+Enter is the way to confirm from the keyboard.
        if (watched is self.source and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if self.ok_button.isEnabled():
                self.accept()
            return True
        return super().eventFilter(watched, event)

    def _insert(self, text: str) -> None:
        self.source.insertPlainText(text)
        self.source.setFocus()

    def _render(self) -> None:
        text = self.source.toPlainText().strip()
        image = None
        try:
            image = mathtext.render(text, self.size_box.value(), self._color, scale=2.0) if text else None
            problem = ""
        except mathtext.MathError as error:
            problem = str(error)
        if problem != self.problem:  # the message takes the preview's place, in red, so nothing jumps
            self.problem = problem
            self.preview.setStyleSheet(f"color: {PAPER_DANGER if problem else PAPER_TEXT};")
        self.ok_button.setEnabled(image is not None)
        if image is None:
            self.preview.setText(problem or "The equation appears here.")
            return
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(1.5)  # rendered at 2 pixels a point: about the size it will print
        self.preview.setPixmap(pixmap)

    def equation(self) -> tuple[str, float]:
        return self.source.toPlainText().strip(), float(self.size_box.value())


def equation_picture(source: str, size: float, color) -> tuple[bytes, float, float]:
    """PNG bytes of an equation and its size on the page, in points."""
    scale = 4.0  # sharp enough to print and to zoom in on
    image = mathtext.render(source, size, color, scale)
    return images.encode(image), image.width() / scale, image.height() / scale


# Signatures -------------------------------------------------------------------------


class _DrawPad(QWidget):
    """Sign with the mouse (or a pen, or a finger)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(392, 150)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.strokes: list[list[QPointF]] = []
        self.color = QColor.fromRgbF(*INK_COLOURS["Black"])
        themes.changed.connect(self.update)

    def clear(self) -> None:
        self.strokes = []
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.strokes.append([event.position()])
            self.update()
            self.changed.emit()

    def mouseMoveEvent(self, event) -> None:
        if self.strokes and event.buttons() & Qt.MouseButton.LeftButton:
            self.strokes[-1].append(event.position())
            self.update()

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        for stroke in self.strokes:
            path.moveTo(stroke[0])
            for point in stroke[1:]:
                path.lineTo(point)
            if len(stroke) == 1:
                path.lineTo(stroke[0] + QPointF(0.1, 0))
        return path

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(QColor(t.paper))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.setPen(QPen(QColor(200, 200, 205), 1, Qt.PenStyle.DashLine))
        painter.drawLine(20, self.height() - 40, self.width() - 20, self.height() - 40)
        if not self.strokes:
            painter.setPen(QColor(150, 150, 158))
            painter.drawText(QRectF(20, self.height() - 36, self.width() - 40, 24),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "Sign above the line")
        painter.setPen(QPen(self.color, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path())

    def picture(self, scale: float = 3.0) -> QImage | None:
        path = self._path()
        if path.isEmpty():
            return None
        box = path.boundingRect().adjusted(-4, -4, 4, 4)
        image = QImage(round(box.width() * scale), round(box.height() * scale), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        painter.translate(-box.topLeft())
        painter.setPen(QPen(self.color, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(path)
        painter.end()
        return image


def typed_signature(name: str, family: str, color) -> QImage:
    font = QFont(family)
    font.setPixelSize(120)
    probe = QImage(1, 1, QImage.Format.Format_ARGB32)
    painter = QPainter(probe)
    painter.setFont(font)
    bounds = painter.boundingRect(QRectF(0, 0, 4000, 400), Qt.AlignmentFlag.AlignLeft, name)
    painter.end()
    image = QImage(round(bounds.width() + 40), round(bounds.height() + 20), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(QColor.fromRgbF(*color))
    painter.drawText(QRectF(20, 10, bounds.width(), bounds.height()), Qt.AlignmentFlag.AlignLeft, name)
    painter.end()
    return image


def without_background(image: QImage, threshold: int = 225, soft: int = 60) -> QImage:
    """A scanned or photographed signature on white paper: the paper turns transparent.

    Lighter than `threshold` is paper; the `soft` levels below it fade in, so the
    ink keeps a smooth edge. Done with a lookup table over the grey bytes, not
    pixel by pixel in Python.
    """
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    table = bytes(0 if g >= threshold else 255 if g <= threshold - soft else round(255 * (threshold - g) / soft)
                  for g in range(256))
    alpha = QImage(bytes(gray.constBits()).translate(table), gray.width(), gray.height(), gray.bytesPerLine(),
                   QImage.Format.Format_Grayscale8).copy()
    result = image.convertToFormat(QImage.Format.Format_ARGB32)
    result.setAlphaChannel(alpha)
    return result


class SignatureDialog(QDialog):
    """Make a signature: type it in a handwriting font, draw it, or take it from a picture."""

    KINDS = ("Typed", "Drawn", "Picture")

    def __init__(self, default_name: str = "", parent=None):
        super().__init__(parent)
        layout = _layout(self, "New signature", "It is kept on this computer, ready for any document.")
        self.setMinimumWidth(520)
        self._picture: QImage | None = None
        fonts = [f for f in SCRIPT_FONTS if f in QFontDatabase.families()] or ["Segoe UI"]
        self.ensurePolished()  # measure with the style sheet's font
        width = max(self.fontMetrics().horizontalAdvance(text) for text in ("Name", "Style", "Ink", "Save as")) + 4
        self.mode = Segments(["Type", "Draw", "Picture"])
        self.pages = QStackedWidget()

        typed = QWidget()
        self.name = QLineEdit(default_name)
        self.name.setPlaceholderText("Your name")
        self.style_box = QComboBox()  # not self.font: that would hide QWidget.font()
        for family in fonts:
            self.style_box.addItem(family, family)
            self.style_box.setItemData(self.style_box.count() - 1, QFont(family, 12), Qt.ItemDataRole.FontRole)
        self.typed_preview = _paper_label()
        self.typed_preview.setMinimumHeight(110)
        form = QFormLayout(typed)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.setHorizontalSpacing(8)
        form.addRow(_field_label("Name", width), self.name)
        form.addRow(_field_label("Style", width), self.style_box)
        form.addRow(self.typed_preview)

        drawn = QWidget()
        self.pad = _DrawPad()
        clear = QPushButton("Clear")
        clear.setAutoDefault(False)
        clear.setIcon(icon("eraser"))
        clear.clicked.connect(self.pad.clear)
        drawn_layout = QVBoxLayout(drawn)
        drawn_layout.setContentsMargins(0, 0, 0, 0)
        drawn_layout.setSpacing(8)
        drawn_layout.addWidget(self.pad, 1)
        drawn_row = QHBoxLayout()
        drawn_row.addWidget(muted_label("Use the mouse, a pen or a finger."))
        drawn_row.addStretch()
        drawn_row.addWidget(clear)
        drawn_layout.addLayout(drawn_row)

        picture = QWidget()
        self.picture_path = ""
        choose = QPushButton("Choose a picture…")
        choose.setAutoDefault(False)
        choose.setIcon(icon("image"))
        choose.clicked.connect(self._choose_picture)
        self.clean = Switch(True)
        self.clean.setAccessibleName("Make the paper transparent")
        self.clean.toggled.connect(self._show_picture)
        self.picture_preview = _paper_label("A photo or scan of your signature on white paper.")
        self.picture_preview.setWordWrap(True)
        self.picture_preview.setMinimumHeight(110)
        picture_row = QHBoxLayout()
        picture_row.setSpacing(8)
        picture_row.addWidget(choose)
        picture_row.addStretch()
        picture_row.addWidget(QLabel("Make the paper transparent"))
        picture_row.addWidget(self.clean)
        picture_layout = QVBoxLayout(picture)
        picture_layout.setContentsMargins(0, 0, 0, 0)
        picture_layout.setSpacing(12)
        picture_layout.addLayout(picture_row)
        picture_layout.addWidget(self.picture_preview, 1)

        for page in (typed, drawn, picture):
            self.pages.addWidget(page)
        self.colour = QComboBox()
        for label, color in INK_COLOURS.items():
            self.colour.addItem(label, color)
        self.colour.currentIndexChanged.connect(self._colour_changed)
        self.label = QLineEdit()
        self.label.setPlaceholderText("Name of this signature, e.g. Full signature")
        bottom = QFormLayout()
        bottom.setSpacing(12)
        bottom.setHorizontalSpacing(8)
        bottom.addRow(_field_label("Ink", width), self.colour)
        bottom.addRow(_field_label("Save as", width), self.label)
        layout.addWidget(self.mode)
        layout.addWidget(self.pages, 1)
        layout.addLayout(bottom)
        footnote = muted_label("This is a picture of a signature, not a digital (certificate) signature.")
        footnote.setWordWrap(True)
        layout.addWidget(footnote)
        self.ok_button = _footer(self, layout, "Save signature")
        self.mode.changed.connect(self.pages.setCurrentIndex)
        self.mode.changed.connect(self._refresh_ok)
        self.name.textChanged.connect(self._show_typed)
        self.style_box.currentIndexChanged.connect(self._show_typed)
        self.pad.changed.connect(self._refresh_ok)
        self._show_typed()
        self.name.setFocus()

    def _ink(self):
        return self.colour.currentData()

    def _colour_changed(self) -> None:
        self.pad.color = QColor.fromRgbF(*self._ink())
        self.pad.update()
        self._show_typed()

    def _refresh_ok(self, *_) -> None:
        page = self.pages.currentIndex()
        ready = (bool(self.name.text().strip()), bool(self.pad.strokes), self._picture is not None)[page]
        self.ok_button.setEnabled(ready)

    def _show_typed(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.typed_preview.setText("Type your name")
        else:
            image = typed_signature(name, self.style_box.currentData(), self._ink())
            self.typed_preview.setPixmap(QPixmap.fromImage(image).scaledToHeight(
                90, Qt.TransformationMode.SmoothTransformation))
        self._refresh_ok()

    def _choose_picture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Signature picture", str(Path.home() / "Pictures"), IMAGE_FILES)
        if path:
            self.picture_path = path
            self._show_picture()

    def _show_picture(self, *_) -> None:
        if not self.picture_path:
            return
        image = QImage(self.picture_path)
        if image.isNull():
            self.picture_preview.setText("That file is not a picture. Choose another one.")
            self._picture = None
            self._refresh_ok()
            return
        if max(image.width(), image.height()) > 1600:
            image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._picture = without_background(image) if self.clean.isChecked() else image
        self.picture_preview.setPixmap(QPixmap.fromImage(self._picture).scaled(
            QSize(460, 110), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self._refresh_ok()

    def signature(self) -> dict | None:
        """{"name", "png"} with the picture as base64 PNG, or None if nothing was made."""
        page = self.pages.currentIndex()
        if page == 0:
            if not self.name.text().strip():
                return None
            image = typed_signature(self.name.text().strip(), self.style_box.currentData(), self._ink())
        elif page == 1:
            image = self.pad.picture()
        else:
            image = self._picture
        if image is None or image.isNull():
            return None
        label = self.label.text().strip() or self.KINDS[page] + " signature"
        return {"name": label, "png": base64.b64encode(images.encode(image)).decode("ascii")}


def signature_icon(record: dict) -> QIcon:
    data = base64.b64decode(record["png"])
    pixmap = QPixmap()
    pixmap.loadFromData(data)
    return QIcon(pixmap.scaled(QSize(120, 40), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


# Camera -----------------------------------------------------------------------------


class CameraDialog(QDialog):
    """Take a photo with the Windows Camera app; the newest photo it saves is picked up here."""

    PICTURES = (".jpg", ".jpeg", ".png", ".heic")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.photo: str = ""
        self._started = time.time() - 1
        pictures = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation))
        self._folders = [p for p in (pictures / "Camera Roll", pictures, Path.home() / "OneDrive" / "Pictures" / "Camera Roll")
                         if p.is_dir()]
        if self._folders:
            self._watcher = QFileSystemWatcher([str(p) for p in self._folders], self)
            self._watcher.directoryChanged.connect(self._look)
        self._poll = QTimer(self)  # some folders (OneDrive) don't always notify
        self._poll.timeout.connect(self._look)
        if self._folders:
            self._poll.start(1500)
        layout = _layout(self, "Image from camera",
                         "The Camera app is opening. Take a photo and it goes on the page."
                         if self._folders else
                         "No Pictures folder was found, so photos can't be picked up. Choose a file instead.")
        reopen = QPushButton("Open the Camera app again")
        reopen.setAutoDefault(False)
        reopen.setIcon(icon("camera"))
        reopen.clicked.connect(self.open_camera)
        reopen.setVisible(bool(self._folders))
        file_button = QPushButton("Choose a photo file…")
        file_button.setAutoDefault(False)
        file_button.setIcon(icon("image"))
        file_button.clicked.connect(self._choose)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(reopen)
        row.addWidget(file_button)
        row.addStretch()
        row.addWidget(cancel)
        layout.addSpacing(8)
        layout.addLayout(row)
        if self._folders:
            QTimer.singleShot(0, self.open_camera)

    def open_camera(self) -> None:
        QDesktopServices.openUrl(QUrl("microsoft.windows.camera:"))

    def _look(self, *_) -> None:
        newest, newest_time = None, self._started
        for folder in self._folders:
            try:
                entries = list(os.scandir(folder))
            except OSError:  # the folder went away, or OneDrive is busy with it
                continue
            for entry in entries:
                try:
                    if (os.path.splitext(entry.name)[1].lower() in self.PICTURES and entry.is_file()
                            and entry.stat().st_mtime > newest_time):
                        newest, newest_time = Path(entry.path), entry.stat().st_mtime
                except OSError:
                    continue
        if newest is not None and _finished_writing(newest):
            self._poll.stop()
            self.photo = str(newest)
            self.accept()

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Photo", str(self._folders[0] if self._folders else Path.home()),
                                              IMAGE_FILES)
        if path:
            self.photo = path
            self.accept()


def _finished_writing(path: Path) -> bool:
    """The Camera app writes the file in steps: wait until its size stays put."""
    try:
        size = path.stat().st_size
        time.sleep(0.15)
        return size > 0 and path.stat().st_size == size
    except OSError:
        return False
