"""How the interface moves: the timings, the easing and the small helpers every animation is built from.

Motion is only what is painted. A widget's value, its visibility, its geometry and its signals are at their end
state from the first moment; what is drawn catches up with them. With motion off (enabled() is False) every helper
jumps to the end state at once, so the code around them needs no second path. (The exception is Fade, older than
this module: a bar that fades out stays shown until it has gone, and widgets.py says so.)

Enter with EASE_OUT, leave with EASE_IN, move with EASE_IN_OUT. FAST answers the pointer (a hover, a press), NORMAL
is for what comes and goes and what moves between places, SLOW for a panel crossing the window. Nothing here belongs
on the hot paths of scrolling or zooming the pages.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterable

import shiboken6
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsEffect, QGraphicsOpacityEffect, QWidget

FAST = 120  # ms: what answers the pointer, a hover or a press
NORMAL = 180  # ms: what comes and goes, and what moves between places
SLOW = 260  # ms: a panel crossing the window
EASE_OUT = QEasingCurve(QEasingCurve.Type.OutCubic)  # entering: quick off the mark, then settling
EASE_IN = QEasingCurve(QEasingCurve.Type.InCubic)  # leaving: slow to start, then away
EASE_IN_OUT = QEasingCurve(QEasingCurve.Type.InOutCubic)  # moving from one place to another
RISE = 8  # px a popup climbs as it appears
SLIDE_WINDOW = 48  # px a window slides in from: nothing clips it, so it comes from near its place
SPI_GETCLIENTAREAANIMATION = 0x1042

_reduced = False  # the "reduce_motion" setting, kept up to date by Settings
_forced: bool | None = None  # tests: True animates whatever the setting says, False never


def system_animations() -> bool:
    """Whether the system lets windows animate: Windows' "Animation effects". Elsewhere there is no switch to read."""
    if sys.platform != "win32":
        return True
    state = ctypes.c_int(1)
    asked = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(state), 0)
    return bool(state.value) if asked else True


def enabled() -> bool:
    """Whether to animate. False when the setting "reduce_motion" is on, when Windows' "Animation effects" is off,
    or when the environment variable ZENITH_NO_MOTION is set (the tests do); then every helper jumps to the end."""
    if _forced is not None:
        return _forced
    if _reduced or os.environ.get("ZENITH_NO_MOTION", "0") not in ("", "0"):
        return False
    return system_animations()


def set_reduced(on: bool) -> None:
    """The setting "reduce_motion", as Settings keeps it: enabled() follows it from now on."""
    global _reduced
    _reduced = bool(on)


def force(on: bool | None) -> None:
    """For tests: True runs the animations whatever the setting, the system and the environment say, False stops
    them all, None goes back to asking them."""
    global _forced
    _forced = on


def _animates(owner: object) -> bool:
    """Whether a change of `owner`'s is worth animating: motion is on and, for a widget, it is on screen. Nobody
    sees what moves on a page that is not shown yet, so a value set up before then simply lands."""
    return enabled() and (not isinstance(owner, QWidget) or owner.isVisible())


def mixed(first: QColor, second: QColor, amount: float) -> QColor:
    """`first` blended toward `second` by `amount` (0 to 1), alpha included, with premultiplied alpha: a colour
    that fades in from clear keeps its own hue all the way, where a plain blend with clear black would pass through
    a muddy grey."""
    amount = min(max(amount, 0.0), 1.0)
    alpha_a, alpha_b = first.alphaF() * (1.0 - amount), second.alphaF() * amount
    alpha = alpha_a + alpha_b
    if alpha <= 0.0:
        return QColor(0, 0, 0, 0)
    return QColor.fromRgbF(
        (first.redF() * alpha_a + second.redF() * alpha_b) / alpha,
        (first.greenF() * alpha_a + second.greenF() * alpha_b) / alpha,
        (first.blueF() * alpha_a + second.blueF() * alpha_b) / alpha,
        alpha,
    )


class Smooth(QObject):
    """A number that eases to where it is sent, for a widget to paint with: a selection indicator that moves between
    items, the level of a glow, how far a section has opened.

    go() animates and set() jumps; `changed` carries the value after every step, so connecting it to the widget's
    update() repaints as it moves. go() also jumps when motion is off, or while the widget it belongs to (its
    parent, when that is a widget) is not on screen. `value` is where it is now, `target` where it is going.
    """

    changed = Signal(float)

    def __init__(self, parent: QObject | None = None, value: float = 0.0):
        super().__init__(parent)
        self._value = float(value)
        self._animation = QVariantAnimation(self)
        self._animation.valueChanged.connect(self._step)

    @property
    def value(self) -> float:
        return self._value

    @property
    def running(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    @property
    def target(self) -> float:
        return float(self._animation.endValue()) if self.running else self._value

    def go(self, target: float, ms: int = NORMAL, easing: QEasingCurve = EASE_OUT) -> None:
        """Ease to `target` from where the value is now, in `ms`; a move already on its way there is left alone."""
        target = float(target)
        if target == self.target:
            return
        if target == self._value or not _animates(self.parent()):
            self.set(target)
            return
        self._animation.blockSignals(True)  # a stopped animation reports the values of its old time as its ends change
        self._animation.stop()
        self._animation.setDuration(ms)
        self._animation.setEasingCurve(easing)
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(target)
        self._animation.blockSignals(False)
        self._animation.start()

    def set(self, value: float) -> None:
        """At once, with nothing left on its way."""
        self._animation.stop()
        self._step(value)

    def _step(self, value) -> None:
        value = float(value)
        if value != self._value:
            self._value = value
            self.changed.emit(value)


class Fill(QObject):
    """A colour that eases to where it is sent, for a widget to paint with: the fill under the pointer that fades
    in and out where it would snap. Paint with `colour`; `changed` says when it moved.

    The colours are blended with premultiplied alpha (see mixed()), so a fill that comes from clear is the same
    tint at every step, only fainter. go() jumps, like Smooth.go(), when motion is off or the widget is not shown.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None, colour: QColor | None = None):
        super().__init__(parent)
        self._from = self._to = QColor(colour) if colour is not None else QColor(0, 0, 0, 0)
        self._mix = 1.0  # how far from `_from` to `_to` the colour has come
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._step)

    @property
    def colour(self) -> QColor:
        """The colour now."""
        return mixed(self._from, self._to, self._mix)

    @property
    def target(self) -> QColor:
        return QColor(self._to)

    @property
    def running(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def go(self, colour: QColor, ms: int = FAST, easing: QEasingCurve = EASE_OUT) -> None:
        """Ease to `colour` from the colour now, in `ms`; one already on its way there is left alone."""
        if colour == self._to or (colour.alpha() == 0 and self._to.alpha() == 0):
            return
        if not _animates(self.parent()):
            self.set(colour)
            return
        self._from, self._to, self._mix = self.colour, QColor(colour), 0.0
        self._animation.blockSignals(True)  # as in Smooth.go
        self._animation.stop()
        self._animation.setDuration(ms)
        self._animation.setEasingCurve(easing)
        self._animation.blockSignals(False)
        self._animation.start()

    def set(self, colour: QColor) -> None:
        """At once, with nothing left on its way."""
        self._animation.stop()
        self._from = self._to = QColor(colour)
        self._step(1.0)

    def _step(self, mix) -> None:
        self._mix = float(mix)
        self.changed.emit()


class _Veil(QGraphicsOpacityEffect):
    """Paints its widget faded and shifted, which is all appear() and slide_in() change: the widget itself never
    moves, so a layout, a hit test or a line of code that reads its place finds the end state from the first frame."""

    def __init__(self, widget: QWidget, start: QPoint):
        super().__init__(widget)
        self._reach = QPoint(start)  # its bounding rect takes in the whole way, so each frame clears the last
        self._shift = QPoint(start)

    def shift(self, offset: QPoint) -> None:
        self._shift = offset
        self.update()

    def boundingRectFor(self, rect):
        return rect.united(rect.translated(self._reach.x(), self._reach.y()))

    def draw(self, painter) -> None:
        if self._shift.isNull():
            super().draw(painter)
            return
        if self.opacity() <= 0.0:
            return
        offset = QPoint()
        picture = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates, offset, QGraphicsEffect.PixmapPadMode.NoPad)
        painter.save()
        painter.setOpacity(self.opacity())
        painter.drawPixmap(offset + self._shift, picture)
        painter.restore()


class _Entrance(QObject):
    """One appear() or slide_in() on its way: a progress from 0 to 1 that fades the widget in and brings it from
    `start` px away to its place. A window fades through its window opacity and is moved; any other widget is
    painted through a _Veil, which goes when the entrance is over (as Fade's effect does)."""

    def __init__(self, widget: QWidget, start: QPoint, ms: int, along: Iterable[QWidget]):
        super().__init__(widget)
        self._widget = widget
        self._start = start
        self._window = widget.isWindow()
        self._base = self._placed = widget.pos()  # a window's end place, and where it was last put
        self._veils = [] if self._window else [_Veil(w, start) for w in (widget, *along)]
        self._done = False
        for veil in self._veils:
            veil.parent().setGraphicsEffect(veil)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(ms)
        self._animation.setEasingCurve(EASE_OUT)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.finish)
        placed_by_someone = widget.testAttribute(Qt.WidgetAttribute.WA_Moved)
        self._step(0.0)
        # A dialog centres itself in its own showEvent, after this, but only if nobody moved it: our first move
        # must not count, or every dialog would open at its parent's corner. _step() then follows its new place.
        widget.setAttribute(Qt.WidgetAttribute.WA_Moved, placed_by_someone)
        self._animation.start()

    def _step(self, progress) -> None:
        progress = float(progress)
        left = 1.0 - progress
        shift = QPoint(round(self._start.x() * left), round(self._start.y() * left))
        if self._window:
            if self._widget.pos() != self._placed:  # something placed the window meanwhile: that is its place now
                self._base = self._widget.pos()
            self._widget.setWindowOpacity(progress)
            self._placed = self._base + shift
            self._widget.move(self._placed)
            return
        for veil in self._veils:
            if shiboken6.isValid(veil):  # an effect set by someone else has replaced it
                veil.setOpacity(progress)
                veil.shift(shift)

    def finish(self) -> None:
        """At once: the widget as it should end up, nothing left on it."""
        if self._done:
            return
        self._done = True
        self._animation.stop()
        self._step(1.0)
        for veil in self._veils:
            if shiboken6.isValid(veil):
                veil.parent().setGraphicsEffect(None)
        self.deleteLater()


def _enter(widget: QWidget, start: QPoint, ms: int, along: Iterable[QWidget]) -> None:
    for earlier in widget.findChildren(_Entrance, options=Qt.FindChildOption.FindDirectChildrenOnly):
        earlier.finish()  # one at a time: the last call decides
    if _animates(widget):
        _Entrance(widget, start, ms, tuple(along))


def appear(widget: QWidget, rise: int = RISE, ms: int = NORMAL, along: Iterable[QWidget] = ()) -> None:
    """A popup or a panel that has just been shown fades in as it rises `rise` px into its place (a negative
    `rise` makes it drop in, for a popup that hangs from its button).

    Call it from showEvent or right after show(): the widget is shown, placed and sized as always, and only its
    picture is faded and lifted until it lands. A window fades through its window opacity and is moved; a widget
    inside one is painted through a graphics effect that is removed when done, so give a widget one of these or
    a Fade, not both, and keep it to popups and panels: each frame paints the whole widget again into a picture.
    `along` are widgets that go with it, such as a floating bar's shadow (FloatingBar.appear() passes it).
    Calling it again finishes the one on its way first.
    """
    _enter(widget, QPoint(0, rise), ms, along)


def slide_in(widget: QWidget, edge: Qt.Edge = Qt.Edge.LeftEdge, distance: int | None = None, ms: int = SLOW,
             along: Iterable[QWidget] = ()) -> None:
    """A panel that opens from an `edge` of the place it floats in slides in from there, fading as it comes.

    `distance` px away by default its own width or height, so it comes from beyond the edge, where the widget it
    sits in clips it; a window, which nothing clips, comes SLIDE_WINDOW px. Call it once the panel is shown, placed
    and sized, as appear().
    """
    sideways = edge in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge)
    if distance is None:
        distance = SLIDE_WINDOW if widget.isWindow() else widget.width() if sideways else widget.height()
    sign = -1 if edge in (Qt.Edge.LeftEdge, Qt.Edge.TopEdge) else 1
    _enter(widget, QPoint(sign * distance, 0) if sideways else QPoint(0, sign * distance), ms, along)


class _OnShow(QObject):
    """Calls appear() whenever its widget is shown, but not when it comes back from being minimised."""

    def __init__(self, widget: QWidget, rise: int, ms: int):
        super().__init__(widget)
        self._rise, self._ms = rise, ms
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and not event.spontaneous():
            appear(watched, self._rise, self._ms)
        return False


def appear_on_show(widget: QWidget, rise: int = RISE, ms: int = NORMAL) -> None:
    """appear() every time `widget` is shown: for a menu, a dialog or a popup made elsewhere, in one line. Doing
    it twice changes nothing."""
    if widget.findChild(_OnShow, options=Qt.FindChildOption.FindDirectChildrenOnly) is None:
        _OnShow(widget, rise, ms)
