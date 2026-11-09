"""Filling in PDF forms on the page: the fields lit, and edited where they are.

With Select or Pan, a click on a text field types in place (Enter or a click away keeps it, Esc leaves it, Tab
goes to the next field); a check box flips; a radio button is chosen; a list opens its choices. Each value goes
into the PDF as one step Undo takes back, and PDFium draws it from there, as every other viewer will.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject, QGraphicsProxyWidget, QLineEdit, QMenu, QPlainTextEdit

from shiboken6 import isValid

from ..core.forms import Box, Field
from ..core.platforms import ui_font
from ..theme import outline, themes
from . import motion
from .annotation_items import accent_on, page_accent

# 0-255: the light on a field, so a form shows what can be filled in, at rest and under the pointer; the hairline that
# comes round it there, and the ring (RING screen pixels wide) round the field being typed in. A stroked outline costs
# several times what a fill does and a form paints a hundred fields at a time: so only the field in use has one.
FILL_ALPHA, HOVER_ALPHA, HOVER_EDGE = 34, 64, 170
RING_ALPHA, RING = 90, 3
GLOW_STEPS = 8  # the pointer's light is told apart in this many steps
TEXT_SIZE = 11  # points, for a field that leaves its size to the viewer


def corner(look) -> float:
    """How far a field's light is rounded (cut, in a style with chamfered corners), in points: a little of the
    style's own, and it goes with the page's scale, as the field does. The text editor over it takes the same."""
    return min(look.chip, 3) * 0.75


@lru_cache(maxsize=64)
def lit(accent: int, glow: int) -> tuple[QPen, QColor]:
    """The hairline (none at rest) and the light of a field in `accent` (an rgba), `glow` steps of GLOW_STEPS from
    rest to the pointer's. Worked out once, not at every paint of every field."""
    share = glow / GLOW_STEPS
    fill, edge = QColor.fromRgba(accent), QColor.fromRgba(accent)
    fill.setAlpha(round(FILL_ALPHA + (HOVER_ALPHA - FILL_ALPHA) * share))
    edge.setAlpha(round(HOVER_EDGE * share))
    hairline = QPen(edge, 1) if glow else QPen(Qt.PenStyle.NoPen)
    hairline.setCosmetic(True)
    return hairline, fill


class FieldItem(QGraphicsObject):
    """One box of a field on its page: lit with the accent, more under the pointer, and ringed while it is typed in.

    A form may have a hundred of these on a page, painted at every frame of a zoom: all that can be worked out once is
    kept (the shape, the pen and the brush), until the theme or the page's tint changes (recolour)."""

    def __init__(self, field: Field, box: Box, rect: tuple, page_item):
        super().__init__(page_item)
        self.field, self.box = field, box
        self.editing = False  # its text is being typed in, in an editor laid over it
        self._rect = QRectF(*rect)
        self._reach = self._rect.adjusted(-12, -12, 12, 12)  # room for the ring and the hairline at any zoom
        self._hit = QPainterPath()
        self._hit.addRect(self._rect)  # only the field itself is hit, not the room round it
        self._lit: tuple[QPen, QColor, QPainterPath] | None = None  # hairline, light and shape, made at the first paint
        self._glow: motion.Smooth | None = None  # how much of the pointer's light shows, made when first needed
        self.setZValue(0.5)  # over the page, under the annotations
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.IBeamCursor if field.kind == "text" else Qt.CursorShape.PointingHandCursor)
        self.setToolTip(field.name)

    def rect(self) -> QRectF:
        return self._rect

    def boundingRect(self) -> QRectF:
        return self._reach

    def shape(self) -> QPainterPath:
        return self._hit

    def recolour(self) -> None:
        self._lit = None  # in the theme's accent, as the page's tint wants it, and the style's corners
        self.update()

    def set_editing(self, editing: bool) -> None:
        self.editing = editing
        self.update()

    def hoverEnterEvent(self, event) -> None:
        self._light(1.0, motion.FAST)

    def hoverLeaveEvent(self, event) -> None:
        self._light(0.0, motion.NORMAL)

    def _light(self, target: float, ms: int) -> None:
        if self._glow is None:
            self._glow = motion.Smooth(self)
            self._glow.changed.connect(lambda _: self.update())
        self._glow.go(target, ms)

    def _accent(self) -> QColor:
        return accent_on(self.parentItem())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._lit is None:
            look = themes.current.look
            shape = QPainterPath()
            if self.field.kind == "radio":  # round, as the button it lights is
                shape.addEllipse(self._rect)
            else:
                shape = outline(self._rect, corner(look), look)
            self._lit = (*lit(self._accent().rgba(), 0), shape)
        hairline, fill, shape = self._lit
        if self._glow is not None and self._glow.value:  # the pointer's light is on its way in or out
            hairline, fill = lit(self._accent().rgba(), round(self._glow.value * GLOW_STEPS))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # the scene gives every item a painter of its own
        painter.setPen(hairline)
        painter.setBrush(fill)
        painter.drawPath(shape)
        if self.editing:
            ring = self._accent()
            ring.setAlpha(RING_ALPHA)
            halo = QPen(ring, RING)
            halo.setCosmetic(True)
            painter.setPen(halo)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            out = (RING + 1) / 2 / max(painter.worldTransform().m11(), 1e-6)  # in points
            look = themes.current.look
            painter.drawPath(outline(self._rect.adjusted(-out, -out, out, out), corner(look) + out, look))


class _LineEditor(QLineEdit):
    def __init__(self, layer: FormLayer):
        super().__init__()
        self.layer = layer

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.layer.cancel()
            return
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.layer.commit(then=1 if event.key() == Qt.Key.Key_Tab else -1)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if event.reason() not in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            self.layer.commit()


class _TextEditor(QPlainTextEdit):
    """A multi-line field: Enter starts a line, Ctrl+Enter or a click away keeps the text."""

    def __init__(self, layer: FormLayer):
        super().__init__()
        self.layer = layer

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.layer.cancel()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.layer.commit()
            return
        if event.key() == Qt.Key.Key_Tab:
            self.layer.commit(then=1)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if event.reason() not in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            self.layer.commit()


class FormLayer:
    """The fields of a view's document: lit on the pages, and filled in on a click."""

    def __init__(self, view):
        self.view = view
        self.items: list[FieldItem] = []
        self._editing: tuple[FieldItem, QGraphicsProxyWidget, object] | None = None

    @property
    def fields(self) -> list[Field]:
        return self.view.document.form_fields()

    def build(self) -> None:
        """Light every field on the pages; after the pages were laid out (again)."""
        self._editing = None  # its proxy went with the old scene
        self.items = []
        document = self.view.document
        if document.read_only:
            return
        for field in self.fields:
            if field.read_only:
                continue
            for box in field.boxes:
                if box.page >= len(self.view._pages) or self.view._pages[box.page] is None:
                    continue
                rect = document.geometry(box.page).rect_to_display(box.rect)
                self.items.append(FieldItem(field, box, rect, self.view._pages[box.page]))

    def recolour(self) -> None:
        """The fields in the new theme's accent; the view calls it when the theme changes."""
        for item in self.items:
            if isValid(item) and item.scene() is not None:
                item.recolour()

    def item_at(self, view_pos: QPointF) -> FieldItem | None:
        for item in self.view.items(view_pos.toPoint()):
            if isinstance(item, FieldItem):
                return item
        return None

    # A click ----------------------------------------------------------------------------------

    def press(self, view_pos: QPointF) -> bool:
        """A press with Select or Pan on a field fills it in. True if it was on one."""
        item = self.item_at(view_pos)
        if item is None:
            if self._editing is not None:
                self.commit()
            return False
        field = item.field
        if field.kind == "text":
            self.edit(item)
        elif field.kind == "checkbox":
            self.view.fill_field(field.name, not field.value)
        elif field.kind == "radio":
            self.view.fill_field(field.name, "" if field.value == item.box.state else item.box.state)
        elif field.kind == "choice":
            self._choose(item)
        return True

    def _choose(self, item: FieldItem) -> None:
        menu = QMenu(self.view)
        motion.appear_on_show(menu)
        for option in item.field.options:
            action = menu.addAction(option)
            action.setCheckable(True)
            action.setChecked(option == item.field.value)
        rect = item.mapRectToScene(item.rect())
        where = self.view.viewport().mapToGlobal(self.view.mapFromScene(rect.bottomLeft()))
        chosen = self.run_menu(menu, where)
        menu.deleteLater()
        if chosen is not None and chosen.text() != item.field.value:
            self.view.fill_field(item.field.name, chosen.text())

    @staticmethod
    def run_menu(menu: QMenu, where):
        """Show a field's choices and wait for one (tests answer for the user here)."""
        return menu.exec(where)

    # Typing in a field ------------------------------------------------------------------------

    def edit(self, item: FieldItem) -> None:
        if self._editing is not None:
            self.commit()
        field = item.field
        editor = _TextEditor(self) if field.multiline else _LineEditor(self)
        rect = item.rect()
        font = QFont(ui_font())
        size = TEXT_SIZE if field.multiline else max(6.0, min(TEXT_SIZE, rect.height() * 0.62))
        font.setPixelSize(max(6, round(size)))  # the scene counts a point of the page as a pixel
        editor.setFont(font)
        if isinstance(editor, QLineEdit):
            editor.setFrame(False)
        else:
            editor.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        radius = 0 if themes.current.look.chamfer else corner(themes.current.look)
        editor.setStyleSheet(f"background: #ffffff; color: #111111; border: 1px solid {page_accent().name()};"
                             f"border-radius: {radius:g}px; padding: 0px;")
        if isinstance(editor, QLineEdit):
            editor.setText(str(field.value))
            if field.max_length:
                editor.setMaxLength(field.max_length)
            editor.returnPressed.connect(self.commit)
        else:
            editor.setPlainText(str(field.value))
        proxy = QGraphicsProxyWidget(item.parentItem())
        proxy.setWidget(editor)
        proxy.setGeometry(QRectF(rect.x(), rect.y(), rect.width(), rect.height()))
        proxy.setZValue(5)
        item.set_editing(True)
        self._editing = (item, proxy, editor)
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        if isinstance(editor, QLineEdit):
            editor.selectAll()

    def editing(self) -> bool:
        return self._editing is not None

    def commit(self, then: int = 0) -> None:
        """Keep what was typed (as one undoable step), and with `then` go on to the next or previous field."""
        if self._editing is None:
            return
        item, proxy, editor = self._editing
        self._editing = None
        text = editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
        name, before = item.field.name, item.field.value
        order = [i.field.name for i in self.items if i.field.kind == "text"]
        self._drop(proxy, item)
        if text != before:
            self.view.fill_field(name, text)  # the pages are laid out again: the items are new ones
        if then and name in order:
            following = order[(order.index(name) + then) % len(order)]
            target = next((i for i in self.items if i.field.name == following), None)
            if target is not None:
                self.edit(target)

    def cancel(self) -> None:
        if self._editing is not None:
            item, proxy, _editor = self._editing
            self._editing = None
            self._drop(proxy, item)

    @staticmethod
    def _drop(proxy: QGraphicsProxyWidget, item: FieldItem) -> None:
        if proxy.scene() is not None:
            proxy.scene().removeItem(proxy)
        proxy.deleteLater()
        if isValid(item):  # filling the field in lays the pages out again, and its item goes with them
            item.set_editing(False)

