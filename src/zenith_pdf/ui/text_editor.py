"""In-place editing of text boxes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen, QTextCursor, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem

from ..core import textlayout
from ..core.annotations import TEXT_ASCENT, TEXT_PADDING, FreeText, quarter_turn
from ..theme import themes
from .annotation_items import paint_annotation, qcolor

Z_EDITOR = 5


class Backdrop(QGraphicsItem):
    """The box being typed in without its text: its fill, frame and callout line, as they will be saved."""

    def __init__(self, page: QGraphicsItem):
        super().__init__(page)
        self.box: FreeText | None = None

    def set_box(self, box: FreeText) -> None:
        self.prepareGeometryChange()
        self.box = box

    def boundingRect(self) -> QRectF:
        if self.box is None:
            return QRectF()
        x, y, w, h = self.box.bounds()
        return QRectF(x - 2, y - 2, w + 4, h + 4)

    def paint(self, painter, option, widget=None) -> None:
        if self.box is not None:
            paint_annotation(painter, self.box)


class TextBoxEditor(QGraphicsTextItem):
    """Edits a FreeText's contents with the same font and scale as the final rendering,
    so lines break while typing exactly where they will in the saved PDF.

    The editor and its frame sit in `holder`, an item turned as the box is (FreeText.rotation),
    so the text of a box on a turned page is typed the way it is shown.
    """

    def __init__(self, box: FreeText, page: QGraphicsItem, on_finish: Callable[[str], None]):
        # The box's own item hides while it is typed in: its fill (light text may need it), frame and callout stay.
        self.backdrop = Backdrop(page)
        self.backdrop.setZValue(Z_EDITOR)
        self.holder = QGraphicsRectItem(page)
        self.holder.setPen(QPen(Qt.PenStyle.NoPen))
        self.holder.setTransform(QTransform(*quarter_turn(box.rect, box.rotation)))
        self.holder.setZValue(Z_EDITOR)
        super().__init__(self.holder)
        self.box = box
        self._on_finish = on_finish
        self._finished = False
        factor = box.font_size / textlayout.REFERENCE_PIXELS
        w, _ = box.upright_size()

        self.document().setDocumentMargin(0)
        font = QFont(textlayout.reference_font(box.face()))
        font.setUnderline(box.underline)
        self.setFont(font)
        option = self.document().defaultTextOption()
        option.setAlignment({"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}.get(
            box.align, Qt.AlignmentFlag.AlignLeft))
        self.document().setDefaultTextOption(option)
        self.setDefaultTextColor(qcolor(box.color))
        self.setScale(factor)
        self.setTextWidth((w - 2 * TEXT_PADDING) / factor)
        ascent = QFontMetricsF(textlayout.reference_font(box.face())).ascent() / textlayout.REFERENCE_PIXELS
        self.setPos(TEXT_PADDING, TEXT_PADDING + (TEXT_ASCENT - ascent) * box.font_size)
        self.setPlainText(box.contents)
        self.setZValue(Z_EDITOR)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

        self.frame = QGraphicsRectItem(self.holder)
        pen = QPen(QColor(themes.current.accent), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.frame.setPen(pen)
        self.frame.setZValue(Z_EDITOR)
        self.document().contentsChanged.connect(self._fit_frame)
        self._fit_frame()

    def _fit_frame(self) -> None:
        w, h = self.box.upright_size()
        needed = textlayout.fitted_height(self.toPlainText(), self.box.font_size, w, self.box.face())
        self.frame.setRect(QRectF(0, 0, w, max(h, needed)))
        self.backdrop.set_box(replace(self.box, contents="").with_height(max(h, needed)))

    def keyPressEvent(self, event) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if event.key() == Qt.Key.Key_Escape or (enter and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.finish()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # Its own context menu takes the focus as a popup, or on macOS as another window: still typing.
        if event.reason() not in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            self.finish()

    def finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._on_finish(self.toPlainText())
