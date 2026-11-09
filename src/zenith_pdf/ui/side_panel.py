"""Icon rail on the left edge plus the panel it opens, which its right edge widens or narrows.

Also what the panels and the bars of the window's chrome share: a button whose fills ease in and out, the title row
every panel starts with, the fills of list rows that ease, pictures that fade in as they arrive, and what a list
says when it is empty.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ..theme import Look, outline, qcolor, themes
from . import motion
from .widgets import IconButton, icon_pixmap, muted_label, panel_title

RAIL_WIDTH = 48
PANEL_WIDTH = 264  # at first; dragging the panel's right edge changes it, for every tab
PANEL_MIN, PANEL_MAX = 200, 640
GRIP = 5  # px of the panel's right edge that take the drag: its border and the scroll bar's margin, no more
PANEL_SLIDE = 32  # px a panel comes from as it opens: out from under the rail
TITLE_HEIGHT = 44  # the row every panel starts with
PAD = 16  # from the panel's left edge to its title, and to what lines up with it
HANG = 8  # px a row's fill reaches out beyond its text on the left, so the text lines up with the title
CLEAR = QColor(0, 0, 0, 0)


def rail_radius(look: Look) -> float:
    """The corners of a rail button: the style's item corners, and nearly round where it has pill buttons."""
    return 16 if look.pill_buttons else look.item


class FadingButton(IconButton):
    """An icon button on the window's own chrome (the rail, the top bar, a panel's header) whose hover, pressed and
    chosen fills ease in and out, where the style sheet's would snap.

    The button's own sheet switches the sheet's fills off, and it paints them in the shape the style gives its
    buttons: `radius(look)` for one that rounds more, as the rail does. `marked`: the chosen fill is somebody else's
    to paint (the rail's marker, which slides from one button to the next). On a floating bar it does none of this:
    IconButton and the bar's own sheet draw the fills there.
    """

    SHEET = "QToolButton { background: transparent; }"  # a widget's own sheet outranks the app's
    _owns = False  # whether it paints its own fills: not while it is on a floating bar (also while it is being made)

    def __init__(self, *args, radius: Callable[[Look], float] | None = None, marked: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._radius = radius
        self._marked = marked
        self._shade = motion.Fill(self)
        self._shade.changed.connect(self.update)
        self._settle()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ParentChange:
            self._settle()
        return super().event(event)

    def _settle(self) -> None:
        owns = self._bar(through_windows=True) is None
        if owns != self._owns:
            self._owns = owns
            self.setStyleSheet(self.SHEET if owns else "")

    def _paint_shade(self) -> None:
        if not self._owns:
            return
        t = themes.current
        if not self.isEnabled():
            want = CLEAR
        elif self.isChecked():
            want = CLEAR if self._marked else qcolor(t.selection)
        elif self.isDown():
            want = qcolor(t.pressed)
        elif self.underMouse():
            want = qcolor(t.hover)
        else:
            want = CLEAR
        self._shade.go(want, motion.FAST if want.alpha() else motion.NORMAL)  # it lingers a little as the pointer goes
        shade = self._shade.colour
        if shade.alpha() == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        radius = t.look.item if self._radius is None else self._radius(t.look)
        painter.drawPath(outline(QRectF(self.rect()), min(radius, self.width() / 2, self.height() / 2), t.look))
        painter.end()

    def paintEvent(self, event) -> None:
        self._paint_shade()
        super().paintEvent(event)


class RowFades(QObject):
    """The fill under the pointer or the choice in a list, eased row by row. A delegate asks colour() as it paints a
    row, with the colour the row should have now, and gets the one it has got to; the list is painted again as it
    moves. A row whose fill has gone is forgotten."""

    KEPT = 48  # rows remembered before the ones that are done are let go

    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        self._fills: dict = {}
        self._limit = self.KEPT  # fills held before the finished ones are looked for

    def colour(self, key, want: QColor) -> QColor:
        fill = self._fills.get(key)
        if fill is None:
            if want.alpha() == 0:
                return want  # nothing shown, nothing to ease
            fill = self._fills[key] = motion.Fill(self._view)  # a child of the view: not eased while it is hidden
            fill.changed.connect(self._view.viewport().update)
        fill.go(want, motion.FAST if want.alpha() else motion.NORMAL)
        shown = fill.colour
        if len(self._fills) > self._limit:
            self._forget_done()
            self._limit = max(self.KEPT, 2 * len(self._fills))  # rows that stay chosen are kept: not a look at every paint
        return shown

    def reset(self) -> None:
        """The rows are made again: what the old ones had on them is not the new ones'."""
        for fill in self._fills.values():
            fill.deleteLater()
        self._fills.clear()
        self._limit = self.KEPT

    def _forget_done(self) -> None:
        for key, fill in list(self._fills.items()):
            if not fill.running and fill.target.alpha() == 0:
                del self._fills[key]
                fill.deleteLater()


class Arrivals(QObject):
    """Pictures that fade in as they come: a list's thumbnails, drawn a moment after their rows were asked for.
    arrive(row) starts a row's fade, level(row) is how far it has got (1 for any other row), and changed(row) says
    that row is to be painted again."""

    changed = Signal(int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._fades: dict[int, motion.Smooth] = {}

    def arrive(self, row: int) -> None:
        if row in self._fades:
            return
        fade = self._fades[row] = motion.Smooth(self)
        fade.changed.connect(lambda value, row=row: self._moved(row, value))
        fade.go(1.0, motion.NORMAL)  # with motion off it lands at once, and the fade is forgotten at once

    def level(self, row: int) -> float:
        fade = self._fades.get(row)
        return 1.0 if fade is None else fade.value

    def clear(self) -> None:
        for fade in self._fades.values():
            fade.deleteLater()
        self._fades.clear()

    def _moved(self, row: int, value: float) -> None:
        self.changed.emit(row)
        if value >= 1.0:
            fade = self._fades.pop(row, None)
            if fade is not None:
                fade.deleteLater()


class _EmptyState(QWidget):
    """A quiet glyph and a line, in the middle of the room a list would fill."""

    def __init__(self, icon_name: str, text: str):
        super().__init__()
        self._icon_name = icon_name
        self._glyph = QLabel()
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line = muted_label(text)
        line.setWordWrap(True)
        line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAD, 0, PAD, 56)  # above the middle, where the eye rests
        layout.setSpacing(10)
        layout.addStretch(1)
        layout.addWidget(self._glyph)
        layout.addWidget(line)
        layout.addStretch(1)
        self._paint()
        themes.changed.connect(self._paint)

    def _paint(self, *_) -> None:
        self._glyph.setPixmap(icon_pixmap(self._icon_name, 28, themes.current.text_disabled))


def empty_state(icon_name: str, text: str) -> QWidget:
    """What a list says when it has nothing in it: the glyph `icon_name` over `text`, centred."""
    return _EmptyState(icon_name, text)


def panel_header(title: str, action: QWidget | None = None) -> QWidget:
    """The row every panel starts with: its title PAD from the left, so the titles of the panels stand in one place,
    and an action at the right when it has one."""
    row = QWidget()
    row.setFixedHeight(TITLE_HEIGHT)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(PAD, 0, 8 if action is not None else PAD, 0)
    layout.addWidget(panel_title(title))
    layout.addStretch()
    if action is not None:
        layout.addWidget(action)
    return row


class _Grip(QWidget):
    """The panel's right edge: dragged, it makes the panel wider or narrower. It lights up under the pointer, so
    the edge can be found."""

    def __init__(self, side: SidePanel):
        super().__init__(side)
        self._side = side
        self._start: tuple[float, int] | None = None  # where the drag began, and the width then
        self._glow = motion.Fill(self)
        self._glow.changed.connect(self.update)
        self.setCursor(Qt.CursorShape.SplitHCursor)

    def _light(self, on: bool) -> None:
        t = themes.current
        self._glow.go(QColor(t.focus or t.accent) if on else CLEAR, motion.FAST if on else motion.NORMAL)

    def enterEvent(self, event) -> None:
        self._light(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._start is None:  # a drag keeps it lit, wherever the pointer goes
            self._light(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = (event.globalPosition().x(), self._side.panel_width())

    def mouseMoveEvent(self, event) -> None:
        if self._start is not None:
            x, width = self._start
            self._side.set_panel_width(round(width + event.globalPosition().x() - x))

    def mouseReleaseEvent(self, event) -> None:
        if self._start is not None:
            self._start = None
            self._light(self.underMouse())
            self._side.resized.emit(self._side.panel_width())

    def paintEvent(self, event) -> None:
        glow = self._glow.colour
        if glow.alpha():
            QPainter(self).fillRect(QRectF(self.width() - 2, 0, 2, self.height()), glow)


class _Rail(QFrame):
    """The strip of buttons. A marker sits under the button whose panel is open, slides to the next one when another
    panel opens, and fades away when the panel closes."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Rail")
        self._on: QWidget | None = None  # the button the marker is on, or was when it began to fade
        self._from = QRectF()  # where it began its slide
        self._slide = motion.Smooth(self, 1.0)  # 0 at `_from`, 1 at the button
        self._level = motion.Smooth(self)  # 1 shown, 0 gone
        self._slide.changed.connect(self.update)
        self._level.changed.connect(self.update)
        themes.changed.connect(self.update)

    def mark(self, button: QWidget | None) -> None:
        """The panel of `button` is open, or None: none is."""
        if button is None:
            self._level.go(0.0, motion.FAST, motion.EASE_IN)
            return
        if button is not self._on:
            if self._on is not None and self._level.target > 0.0:  # on its way from one button to another
                self._from = self._rect()
                self._slide.set(0.0)
                self._slide.go(1.0, motion.NORMAL, motion.EASE_IN_OUT)
            else:  # none was open: it appears where it goes
                self._from = QRectF(button.geometry())
                self._slide.set(1.0)
            self._on = button
        self._level.go(1.0, motion.FAST)

    def _rect(self) -> QRectF:
        """Where the marker is now: its button, or between that and where it came from."""
        goal, share = QRectF(self._on.geometry()), self._slide.value
        start = self._from
        return QRectF(start.x() + (goal.x() - start.x()) * share, start.y() + (goal.y() - start.y()) * share,
                      start.width() + (goal.width() - start.width()) * share,
                      start.height() + (goal.height() - start.height()) * share)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        level = self._level.value
        if self._on is None or level <= 0.0:
            return
        t = themes.current
        rect = self._rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        fill = qcolor(t.selection)
        fill.setAlphaF(fill.alphaF() * level)
        painter.setBrush(fill)
        painter.drawPath(outline(rect, min(rail_radius(t.look), rect.width() / 2, rect.height() / 2), t.look))
        if t.look.on_mark:  # the style's own sign of what is on, here at the rail's edge
            sign = QColor(t.accent)
            sign.setAlphaF(level)
            painter.setBrush(sign)
            if t.look.on_mark == "dot":
                painter.drawEllipse(QPointF(3, rect.center().y()), 2, 2)
            else:
                painter.drawRect(QRectF(0, rect.top() + 8, 2, rect.height() - 16))
        painter.end()


class SidePanel(QWidget):
    resized = Signal(int)  # the panel's width, once a drag of its edge ends

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rail = _Rail()
        self._rail.setFixedWidth(RAIL_WIDTH)
        self._rail_layout = QVBoxLayout(self._rail)
        self._rail_layout.setContentsMargins(6, 8, 6, 8)
        self._rail_layout.setSpacing(4)
        self._rail_layout.addStretch()

        self._stack = QStackedWidget()
        self._stack.setObjectName("Panel")
        self._stack.setFixedWidth(PANEL_WIDTH)
        self._stack.hide()
        self._grip = _Grip(self)
        self._grip.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._rail)
        layout.addWidget(self._stack)
        self._stack.stackUnder(self._rail)  # a panel opening comes out from under the rail

        self._buttons: dict[str, IconButton] = {}
        self._panels: dict[str, QWidget] = {}

    def panel_width(self) -> int:
        return self._stack.minimumWidth()  # its fixed width, shown or not

    def set_panel_width(self, width: int) -> None:
        """The panel this wide, within PANEL_MIN and PANEL_MAX."""
        self._stack.setFixedWidth(max(PANEL_MIN, min(PANEL_MAX, int(width))))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_grip()

    def _place_grip(self) -> None:
        self._grip.setVisible(self._stack.isVisibleTo(self))
        if self._grip.isVisible():
            self._grip.setGeometry(RAIL_WIDTH + self._stack.minimumWidth() - GRIP, 0, GRIP, self.height())
            self._grip.raise_()

    def add_panel(self, key: str, icon_name: str, tooltip: str, widget: QWidget) -> None:
        button = FadingButton(icon_name, tooltip, checkable=True, help=f"panel_{key}", radius=rail_radius, marked=True)
        button.clicked.connect(lambda: self.toggle(key))
        self._rail_layout.insertWidget(len(self._buttons), button)
        self._stack.addWidget(widget)
        self._buttons[key] = button
        self._panels[key] = widget

    def add_bottom_button(self, icon_name: str, tooltip: str, slot, help: str | None = None) -> IconButton:
        """A plain button at the foot of the rail, below the stretch."""
        button = FadingButton(icon_name, tooltip, help=help, radius=rail_radius)
        button.clicked.connect(slot)
        self._rail_layout.addWidget(button)
        return button

    def keys(self) -> list[str]:
        return list(self._panels)

    @property
    def current(self) -> str | None:
        if self._stack.isHidden():
            return None
        return next(key for key, widget in self._panels.items() if widget is self._stack.currentWidget())

    def show_panel(self, key: str) -> None:
        widget = self._panels[key]
        opening = self._stack.isHidden()
        switching = not opening and self._stack.currentWidget() is not widget
        self._stack.setCurrentWidget(widget)
        self._stack.show()
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self._rail.mark(self._buttons[key])
        self._place_grip()
        # Only what is painted moves: the panel and the pages beside it have their final size from the start.
        if opening:
            motion.slide_in(self._stack, Qt.Edge.LeftEdge, PANEL_SLIDE, motion.NORMAL)
        elif switching:
            motion.appear(widget, motion.RISE, motion.FAST)

    def hide_panel(self) -> None:
        self._stack.hide()
        for button in self._buttons.values():
            button.setChecked(False)
        self._rail.mark(None)
        self._place_grip()

    def toggle(self, key: str | None = None) -> None:
        key = key or next(iter(self._panels))
        if self.current == key:
            self.hide_panel()
        else:
            self.show_panel(key)
