"""The tools added through the tool chooser: free-form, more shapes, review, measure, insert, select.

Like the tools in tools.py, each one turns mouse input on a page into
annotations. Click-by-click tools (polyline, polygon, perimeter, area) keep
their points between presses and finish on a double-click or Enter.
"""

from __future__ import annotations

import time
from dataclasses import replace

import shiboken6
from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF, QUndoCommand
from PySide6.QtWidgets import QApplication, QGraphicsEllipseItem, QGraphicsPathItem

from ..core import inking, textlayout
from ..core.annotations import (
    FreeText,
    Ink,
    Line,
    Link,
    Measurement,
    Poly,
    Shape,
    Tape,
    TextMarkup,
    constrain_angle,
    normalized,
)
from .annotation_items import AnnotationItem, hit_path, qcolor
from .commands import ChangeAnnotations, RemoveAnnotations
from .tools import LASER_SIZES, MIN_DRAG, TextSpanTool, Tool, alive, remove_item

Z_PREVIEW = 6
CALLOUT_WIDTH = 160.0
PREVIEW_BUDGET = 0.008  # seconds a stroke of varying width may take to paint while drawn; slower ones show plainly
PRO_MEMORY = 32  # points back the Pro pen looks to size the newest one while drawing (its average forgets the rest)
MARKUP_TOOLS = {"Highlight": "highlight", "Underline": "underline", "Squiggly": "squiggly", "StrikeOut": "strikeout"}

clock = time.perf_counter  # when each point of a stroke is drawn, for the Pro pen; tests replace it


def _point(pos: QPointF) -> tuple[float, float]:
    return pos.x(), pos.y()


def _near(a, b, distance: float) -> bool:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= distance * distance


class _Preview:
    """A dashed outline that follows the mouse while a tool is in use."""

    def __init__(self, page, color: QColor, fill: QColor | None = None):
        self.item = QGraphicsPathItem(page)
        self.item.setZValue(Z_PREVIEW)
        outline = QPen(color, 1.2, Qt.PenStyle.DashLine)
        outline.setCosmetic(True)
        self.item.setPen(outline)
        self.item.setBrush(QBrush(fill) if fill is not None else Qt.BrushStyle.NoBrush)

    def set_path(self, path: QPainterPath) -> None:
        if alive(self.item):
            self.item.setPath(path)

    def remove(self) -> None:
        remove_item(self.item)


# Free-form ------------------------------------------------------------------------


class _Trail(QGraphicsPathItem):
    """A stroke of varying width while it is drawn: each move adds the piece of its newest segment."""

    def __init__(self, page, color, opacity: float, multiply: bool):
        super().__init__(page)
        self.multiply = multiply
        self.cost = 0.0  # seconds the last paint took
        self.setZValue(Z_PREVIEW)
        self.setPen(Qt.PenStyle.NoPen)
        self.setBrush(qcolor(color))
        self.setOpacity(opacity)
        self._path = QPainterPath()
        self._path.setFillRule(Qt.FillRule.WindingFill)  # the pieces overlap: filled as one, no darker seams

    def add(self, pieces) -> None:
        for piece in pieces:
            self._path.addPolygon(QPolygonF([QPointF(*p) for p in piece]))
            self._path.closeSubpath()
        self.setPath(self._path)

    def reset(self, pieces) -> None:
        self._path = QPainterPath()
        self._path.setFillRule(Qt.FillRule.WindingFill)
        self.add(pieces)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        started = time.perf_counter()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.multiply:  # like the finished highlighter: darkens, never covers the text
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        super().paint(painter, option, widget)
        self.cost = time.perf_counter() - started


class FreehandTool(Tool):
    """Pen and highlighter: draw with the mouse. Shift draws a straight stroke.

    Basic keeps one width, Pro gets thinner the faster the hand moves, Calligraphy
    follows a slanted nib. On release the stroke is smoothed, and Ink to Shape or
    Ink to Line turn it into the rectangle, ellipse, polygon or line it looks like.
    """

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.points: list = []
        self.times: list[float] = []
        self.pressures: list[float] | None = None  # with a pen that feels pressure: one per point
        self.straight = False  # Shift is held: the stroke is its first and last point
        self.item: AnnotationItem | None = None  # the stroke so far, at one width
        self.trail: _Trail | None = None  # or with its widths, for Pro and Calligraphy
        self._live: list[float] = []  # the widths the trail shows

    def _scale(self) -> float:
        """Screen pixels per point: the speed of the Pro pen and the sizes Smart ink looks for are on screen."""
        return max(self.view._view_scale(), 1e-6)

    def _plain(self, points) -> Ink:
        s = self.style
        return Ink(page=self.page.index, strokes=(tuple(points),), width=s.width, color=s.color, opacity=s.opacity,
                   highlighter=s.highlight, **self.common())

    def press(self, page, pos, event) -> bool:
        self.cancel()
        self.page, self.points, self.times = page, [_point(pos)], [clock()]
        s = self.style
        pressure = None if s.highlight else self.view.pen_pressure()  # a highlighter keeps its width
        self.pressures = None if pressure is None else [pressure]
        if s.pen == "basic" and self.pressures is None:
            self.item = AnnotationItem(self._plain(self.points), page, interactive=False)
        else:
            self.trail = _Trail(page, s.color, s.opacity, s.highlight)
            self._live = [self._newest_width()]
        return True

    def move(self, page, pos, event) -> None:
        if self.page is None or not (alive(self.item) or alive(self.trail)):
            return
        point = _point(pos)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        pressure = self.view.pen_pressure() if self.pressures is not None else None
        if shift:
            self.points, self.times = [self.points[0], point], [self.times[0], clock()]
            if self.pressures is not None:
                self.pressures = [self.pressures[0], self.pressures[0]]
        elif not _near(self.points[-1], point, 0.4 / max(self.view.zoom, 0.1)):
            self.points.append(point)
            self.times.append(clock())
            if self.pressures is not None:
                self.pressures.append(self.pressures[-1] if pressure is None else pressure)
        else:
            return
        redraw = shift or self.straight  # the stroke changed as a whole, not just at its end
        self.straight = shift
        if self.trail is not None and self.trail.cost > PREVIEW_BUDGET:
            self._show_plainly()
        if self.item is not None:
            self.item.set_annotation(self._plain(self.points))
        elif redraw:
            self._live = self._widths(self.points, self.times, self.straight, live=True)
            self.trail.reset(inking.stroke_polygons(self.points, self._live))
        else:
            self._live.append(self._newest_width())
            if len(self.points) == 2 and self.style.pen == "calligraphy":
                self._live[0] = self._live[1]  # the first point had no direction yet
            self.trail.add(inking.stroke_polygons(self.points[-2:], self._live[-2:]))

    def _newest_width(self) -> float:
        """The width the last point gets while drawing. Pro tapers the end only on release."""
        s = self.style
        if self.pressures is not None:
            return self._pressed(self.points[-PRO_MEMORY:], self.pressures[-PRO_MEMORY:])[-1]
        if s.pen == "calligraphy":
            return inking.calligraphy_widths(self.points[-2:], s.width, s.nib_angle)[-1]
        points, times = self.points[-PRO_MEMORY:], self.times[-PRO_MEMORY:]
        return self._widths(points, times, False, live=True)[-1]

    def _pressed(self, points, pressures) -> list[float]:
        """Widths from the pen's pressure: every pen follows it, the calligraphy nib still turns with the line."""
        s = self.style
        widths = inking.pressure_widths(pressures, s.width)
        if s.pen == "calligraphy":
            nib = inking.calligraphy_widths(points, s.width, s.nib_angle)
            widths = [w * n / max(s.width, 1e-6) for w, n in zip(widths, nib)]
        return widths

    def _widths(self, points, times, straight: bool, live: bool = False) -> list[float] | None:
        """A width for each point, or None when the stroke keeps one width."""
        s = self.style
        if self.pressures is not None and len(self.pressures) == len(points):
            return self._pressed(points, self.pressures)
        if s.pen == "calligraphy":
            return inking.calligraphy_widths(points, s.width, s.nib_angle)
        if s.pen != "pro" or (straight and len(points) == 2):  # a ruled line has no speed to show
            return None if not live else [s.width] * len(points)
        if live:  # the stroke goes on: its end is not tapered yet
            last = len(points)
            return inking.pro_widths(points + points[-1:] * 4, times + times[-1:] * 4, s.width, self._scale())[:last]
        return inking.pro_widths(points, times, s.width, self._scale())

    def _show_plainly(self) -> None:
        """Painting the widths got too slow for this stroke: it shows at one width until the release."""
        remove_item(self.trail)
        self.trail = None
        self.item = AnnotationItem(self._plain(self.points), self.page, interactive=False)

    def release(self, page, pos, event) -> None:
        if self.page is None:
            self.cancel()
            return
        index, points, times, straight = self.page.index, self.points, self.times, self.straight
        pressures = self.pressures
        self.cancel()
        self.pressures = pressures  # _finished reads them; the next press sets them again
        made = self._finished(index, points, times, straight)
        self.pressures = None
        self.view.add_annotations([made], made.label)
        self.view.created([made])

    def _finished(self, page: int, points, times, straight: bool):
        """What the stroke becomes: a shape or a line if Smart ink recognises one, else ink."""
        s = self.style
        unit = 1 / self._scale()
        common = dict(page=page, color=s.color, opacity=s.opacity, width=s.width, highlight=s.highlight,
                      **self.common())
        if s.ink_to_shape and not straight:
            found = inking.as_shape(points, unit)
            if found is not None:
                kind, value = found
                if kind == "polygon":
                    return Poly(points=value, closed=True, **common)
                x, y, w, h = value  # a shape's border is drawn inside its box: grow it to sit on the stroke
                rect = (x - s.width / 2, y - s.width / 2, w + s.width, h + s.width)
                return Shape(shape="Square" if kind == "rectangle" else "Circle", rect=rect, **common)
        if s.ink_to_line:
            ends = inking.as_line(points, unit)
            if ends is not None:
                return Line(start=ends[0], end=ends[1], **common)
        widths = self._widths(points, times, straight)
        tolerance = (0.25 if self.name == "pen" else 0.6) / max(self.view.zoom, 0.25)
        keep = inking.simplify_indices(points, tolerance)
        stroke = tuple(points[i] for i in keep)
        common.pop("highlight")
        if widths is None:
            return Ink(strokes=(stroke,), highlighter=s.highlight, **common)
        kept = tuple(round(widths[i], 2) for i in keep)  # as the PDF keeps them
        common["width"] = max(kept)
        return Ink(strokes=(stroke,), widths=(kept,), highlighter=s.highlight, **common)

    def cancel(self) -> None:
        remove_item(self.item)
        remove_item(self.trail)
        self.item = self.trail = None
        self.page, self.points, self.times, self.straight, self._live = None, [], [], False, []


class _Erase(QUndoCommand):
    """One drag of the eraser, undone in one go. The strokes went as it passed, so the first redo does nothing."""

    def __init__(self, store, before: dict, after: dict, order: list[str]):
        super().__init__("Erase")
        self._store = store
        self._before = before  # id -> the ink as it was
        self._after = after  # id -> what is left of it, None if nothing is
        self._order = order  # the drawing order before the drag: undo puts strokes back where they were
        self._done = True

    def redo(self) -> None:
        if self._done:
            self._done = False
            return
        for key, ink in self._after.items():
            if ink is None:
                self._store.remove(key)
            else:
                self._store.replace(ink)

    def undo(self) -> None:
        positions = {key: i for i, key in enumerate(self._order)}
        gone = []
        for key, ink in self._before.items():
            if self._after[key] is None:
                gone.append((positions.get(key, len(positions)), ink))
            else:
                self._store.replace(ink)
        self._store.put_back(gone)


class EraserTool(Tool):
    """Rubs out hand-drawn strokes: pen and highlighter. Other annotations stay.

    Whole takes away each stroke it touches; Partial takes only what it passes
    over, so a stroke can be cut in two. A whole drag is one step to undo.
    """

    cursor = Qt.CursorShape.BlankCursor
    wants_hover = True

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.last = None
        self.ring: QGraphicsEllipseItem | None = None
        self._before: dict[str, Ink] = {}
        self._after: dict[str, Ink | None] = {}
        self._order: list[str] = []  # the drawing order as the drag found it
        self._boxes: dict[str, tuple[Ink, tuple]] = {}  # each ink's bounds, worked out once per drag
        view.store.reset.connect(self.drop)  # a page edit replaced every annotation

    @property
    def erasing(self) -> bool:
        """Something was rubbed out in this drag and is not on the undo stack yet."""
        return bool(self._before)

    @property
    def radius(self) -> float:
        return self.style.width / 2

    def _show_ring(self, page, pos) -> None:
        if not alive(self.ring) or self.ring.parentItem() is not page:
            remove_item(self.ring)
            self.ring = QGraphicsEllipseItem(page)
            self.ring.setZValue(Z_PREVIEW)
            outline = QPen(QColor(90, 90, 100), 1.0)
            outline.setCosmetic(True)
            self.ring.setPen(outline)
            self.ring.setBrush(QColor(255, 255, 255, 90))
        r = self.radius
        self.ring.setRect(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r))

    def hover(self, page, pos) -> None:
        self._show_ring(page, pos)

    def press(self, page, pos, event) -> bool:
        self.commit()  # a drag whose release never came (a menu took it) stays as it was
        self._boxes = {}
        self.page, self.last = page, _point(pos)
        self._show_ring(page, pos)
        self._erase(self.last, self.last)
        return True

    def move(self, page, pos, event) -> None:
        if self.page is None:
            return
        point = _point(pos)
        self._show_ring(page, pos)
        self._erase(self.last, point)
        self.last = point

    def release(self, page, pos, event) -> None:
        erased = self.commit()
        self.page, self._boxes = None, {}
        if erased:
            self.view.tool_used()

    def commit(self) -> bool:
        """Put what this drag erased on the undo stack, as one step. False if it erased nothing."""
        if not self._before:
            return False
        before, after, order = self._before, self._after, self._order
        self._before, self._after, self._order = {}, {}, []
        self.view.undo_stack.push(_Erase(self.view.store, before, after, order))
        return True

    def drop(self) -> None:
        """Forget what this drag erased, with no undo step: the pages were replaced, and that is in their snapshot."""
        self._before, self._after, self._order, self._boxes = {}, {}, [], {}

    def _erase(self, a, b) -> None:
        style = self.style
        radius, partial = style.width / 2, style.erase == "partial"
        left, right = min(a[0], b[0]) - radius, max(a[0], b[0]) + radius
        top, bottom = min(a[1], b[1]) - radius, max(a[1], b[1]) + radius
        store = self.view.store
        for ink in [x for x in store.all() if isinstance(x, Ink) and x.page == self.page.index and not x.locked]:
            known = self._boxes.get(ink.id)
            if known is None or known[0] is not ink:
                known = self._boxes[ink.id] = (ink, ink.bounds())  # already grown by the ink's width
            x, y, w, h = known[1]
            if x > right or x + w < left or y > bottom or y + h < top:
                continue  # far from the eraser: most strokes on a full page
            left_over = self._rubbed(ink, a, b, radius, partial)
            if left_over is ink:
                continue
            if not self._before:  # the drag's first change
                self._order = store.order()
            self._before.setdefault(ink.id, ink)
            self._after[ink.id] = left_over
            if left_over is None:
                store.remove(ink.id)
            else:
                store.replace(left_over)

    @staticmethod
    def _rubbed(ink: Ink, a, b, radius: float, partial: bool) -> Ink | None:
        """`ink` after the eraser went from a to b: the same object if untouched, None if nothing is left."""
        strokes, widths, touched = [], [], False
        for stroke, stroke_widths in zip(ink.strokes, ink.stroke_widths()):
            if partial:
                pieces = inking.erase_stroke(stroke, stroke_widths, a, b, radius)
                if pieces is not None:
                    touched = True
                    for points, piece_widths in pieces:
                        strokes.append(tuple(points))
                        widths.append(tuple(piece_widths))
                    continue
            elif inking.touches(stroke, stroke_widths, a, b, radius):
                touched = True
                continue
            strokes.append(stroke)
            widths.append(stroke_widths)
        if not touched:
            return ink
        if not strokes:
            return None
        if not ink.widths:  # one width throughout: the pieces keep it
            return replace(ink, strokes=tuple(strokes))
        return replace(ink, strokes=tuple(strokes), widths=tuple(widths), width=max(max(w) for w in widths))

    def cancel(self) -> None:
        remove_item(self.ring)
        self.ring = None
        self.commit()
        self.page = None
        self._boxes = {}


class TapeTool(Tool):
    """Drag to stick a strip of tape over the page; click a strip to peel it back or put it down again."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.start = None
        self.item: AnnotationItem | None = None

    def press(self, page, pos, event) -> bool:
        self.cancel()
        point = _point(self.view.snapped(page, pos))
        for tape in self.view.store.all():
            if isinstance(tape, Tape) and tape.page == page.index and self.view.item_contains(tape, pos):
                toggle_tape(self.view, tape)
                return True
        s = self.style
        self.page, self.start = page, point
        self.item = AnnotationItem(Tape(page=page.index, start=point, end=point, width=s.width, color=s.color,
                                        revealed=s.tape_start == "peeled", **self.common()), page, interactive=False)
        return True

    def move(self, page, pos, event) -> None:
        if not alive(self.item):
            return
        end = _point(self.view.snapped(page, pos))
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = constrain_angle(self.start, end)
        self.item.set_annotation(replace(self.item.annotation, end=end))

    def release(self, page, pos, event) -> None:
        if not alive(self.item):
            self.cancel()
            return
        tape = self.item.annotation
        self.cancel()
        if not _near(tape.start, tape.end, MIN_DRAG * 2):
            self.view.add_annotations([tape], tape.label)
            self.view.created([tape])

    def cancel(self) -> None:
        remove_item(self.item)
        self.item = None
        self.page = None


def toggle_tape(view, tape: Tape) -> None:
    label = "Put tape back" if tape.revealed else "Peel tape"
    view.undo_stack.push(ChangeAnnotations(view.store, [tape], [replace(tape, revealed=not tape.revealed)], label))


class LaserTool(Tool):
    """A glowing trail that fades away, for pointing things out while presenting or sharing the screen."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.item: QGraphicsPathItem | None = None
        self.path = QPainterPath()
        self._fades: list[tuple[QVariantAnimation, QGraphicsPathItem]] = []

    def press(self, page, pos, event) -> bool:
        self._fade_out()
        s = self.style
        self.path = QPainterPath(pos)
        self.item = QGraphicsPathItem(page)
        self.item.setZValue(Z_PREVIEW + 1)
        glow = QPen(qcolor(s.color), LASER_SIZES.get(s.laser_size, LASER_SIZES["medium"]), Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        glow.setCosmetic(True)  # the same size on screen at any zoom
        self.item.setPen(glow)
        self.item.setPath(self.path)
        return True

    def move(self, page, pos, event) -> None:
        if alive(self.item):
            self.path.lineTo(pos)
            self.item.setPath(self.path)

    def release(self, page, pos, event) -> None:
        self._fade_out()

    def _fade_out(self) -> None:
        item, self.item = self.item, None
        if not alive(item):
            return
        fade = QVariantAnimation(self.view)
        fade.setDuration(round(self.style.laser_fade * 1000))
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        entry = (fade, item)
        self._fades.append(entry)
        fade.valueChanged.connect(lambda value: alive(item) and item.setOpacity(float(value)))
        fade.finished.connect(lambda: (remove_item(item), entry in self._fades and self._fades.remove(entry)))
        fade.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def cancel(self) -> None:
        """Every trail goes at once, fading or not: the pages may be about to be rebuilt."""
        remove_item(self.item)
        self.item = None
        fades, self._fades = self._fades, []
        for fade, item in fades:
            remove_item(item)
            if shiboken6.isValid(fade):
                fade.stop()  # deletes itself: DeleteWhenStopped


# Shapes and measurements drawn point by point --------------------------------------


class PointsTool(Tool):
    """Polyline, polygon, perimeter and area: click for each point, double-click or Enter to finish.

    Backspace takes back the last point, Esc gives up, Shift keeps lines at 45°.
    """

    wants_hover = True
    CLOSED = {"polygon", "area"}

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.points: list = []
        self.item: AnnotationItem | None = None

    @property
    def busy(self) -> bool:
        return self.page is not None

    def _build(self, points) -> Poly | Measurement:
        s = self.style
        common = dict(page=self.page.index, color=s.color, width=s.width, **self.common())
        if self.name in ("perimeter", "area"):
            scale, unit = self.view.tool_settings.measure_scale()
            return Measurement(points=tuple(points), mode=self.name, scale=scale, unit=unit, **common)
        closed = self.name == "polygon"
        if closed:
            ends = dict(fill=s.fill, border=s.border)
        else:
            ends = dict(head_start=s.head_start, head_end=s.head_end)
        return Poly(points=tuple(points), closed=closed, opacity=s.opacity, dash=s.dash, highlight=s.highlight,
                    **ends, **common)

    def _place(self, pos, event):
        point = _point(self.view.snapped(self.page, pos))
        if self.points and event is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            point = constrain_angle(self.points[-1], point)
        return point

    def press(self, page, pos, event) -> bool:
        if self.page is not None and page is not self.page:
            self.finish()
        if self.page is None:
            self.page, self.points = page, []
        point = self._place(pos, event)
        if not self.points or not _near(self.points[-1], point, 1.0):
            self.points.append(point)
        self._show(self.points)
        return True

    def hover(self, page, pos) -> None:
        if self.page is not None and page is self.page:
            modifiers = QApplication.keyboardModifiers()
            point = _point(pos)
            if self.points and modifiers & Qt.KeyboardModifier.ShiftModifier:
                point = constrain_angle(self.points[-1], point)
            self._show(self.points + [point])

    def _show(self, points) -> None:
        if len(points) < 2:
            return
        annotation = self._build(points)
        if not alive(self.item):
            self.item = AnnotationItem(annotation, self.page, interactive=False)
        else:
            self.item.set_annotation(annotation)

    def double_click(self, page, pos) -> bool:
        if self.page is None:
            return False
        self.finish()
        return True

    def key_press(self, event) -> bool:
        if self.page is None:
            return False
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish()
        elif event.key() == Qt.Key.Key_Backspace:
            if len(self.points) > 1:
                self.points.pop()
                self._show(self.points)
            else:
                self.cancel()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel()
        else:
            return False
        return True

    def finish(self) -> None:
        points, page = self.points, self.page
        self.cancel()
        needed = 3 if self.name in self.CLOSED else 2
        if page is None or len(points) < needed:
            return
        self.page = page
        annotation = self._build(points)
        self.page = None
        self.view.add_annotations([annotation], annotation.label)
        self.view.created([annotation])

    def cancel(self) -> None:
        remove_item(self.item)
        self.item = None
        self.page, self.points = None, []


class MeasureLineTool(Tool):
    """Distance: drag from one point to the other; the length shows as you go."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.start = None
        self.item: AnnotationItem | None = None

    def _build(self, end) -> Measurement:
        s = self.style
        scale, unit = self.view.tool_settings.measure_scale()
        return Measurement(page=self.page.index, points=(self.start, end), mode="distance", scale=scale, unit=unit,
                           color=s.color, width=s.width, **self.common())

    def press(self, page, pos, event) -> bool:
        self.cancel()
        self.page, self.start = page, _point(self.view.snapped(page, pos))
        self.item = AnnotationItem(self._build(self.start), page, interactive=False)
        return True

    def move(self, page, pos, event) -> None:
        if not alive(self.item):
            return
        end = _point(self.view.snapped(page, pos))
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = constrain_angle(self.start, end)
        self.item.set_annotation(self._build(end))

    def release(self, page, pos, event) -> None:
        if not alive(self.item):
            self.cancel()
            return
        measured = self.item.annotation
        self.cancel()
        if not _near(measured.points[0], measured.points[1], MIN_DRAG):
            self.view.add_annotations([measured], measured.label)
            self.view.created([measured])

    def cancel(self) -> None:
        remove_item(self.item)
        self.item = None
        self.page = None


# Review -----------------------------------------------------------------------------


def _minus(quad, cut) -> list:
    """What is left of a line of markup once the span `cut` (on the same line) is cleaned off it."""
    x, y, w, h = quad
    cx, cy, cw, ch = cut
    overlap = min(y + h, cy + ch) - max(y, cy)
    if overlap < 0.5 * min(h, ch) or cx >= x + w or cx + cw <= x:
        return [quad]
    pieces = []
    if cx > x + 1:
        pieces.append((x, y, cx - x, h))
    if cx + cw < x + w - 1:
        pieces.append((cx + cw, y, x + w - cx - cw, h))
    return pieces


class CleanerTool(TextSpanTool):
    """Drag across text to clean the highlights, underlines, squiggles and strikethroughs it is set to off it."""

    def release(self, page, pos, event) -> None:
        cuts = self.rects
        index = self.page.index if self.page is not None else -1
        self.cancel()
        kinds = set(self.style.clean_kinds)
        old, new, gone = [], [], []
        for markup in self.view.store.all():
            if (not isinstance(markup, TextMarkup) or markup.page != index or markup.locked
                    or MARKUP_TOOLS.get(markup.style) not in kinds):
                continue
            quads = list(markup.quads)
            for cut in cuts:
                quads = [piece for quad in quads for piece in _minus(quad, cut)]
            if tuple(quads) == markup.quads:
                continue
            if quads:
                old.append(markup)
                new.append(replace(markup, quads=tuple(quads)))
            else:
                gone.append(markup)
        if not (old or gone):
            return
        stack = self.view.undo_stack
        stack.beginMacro("Clean text")
        if old:
            stack.push(ChangeAnnotations(self.view.store, old, new, "Clean text"))
        if gone:
            stack.push(RemoveAnnotations(self.view.store, gone, "Clean text"))
        stack.endMacro()
        self.view.tool_used()


class CalloutTool(Tool):
    """Press where the arrow should point, drag to where the text goes, then type."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.tip = None
        self.preview: _Preview | None = None

    def press(self, page, pos, event) -> bool:
        self.cancel()
        self.page, self.tip = page, _point(pos)
        self.preview = _Preview(page, qcolor(self.style.color))
        return True

    def move(self, page, pos, event) -> None:
        if self.preview is None:
            return
        path = QPainterPath(QPointF(*self.tip))
        path.lineTo(pos)
        self.preview.set_path(path)

    def release(self, page, pos, event) -> None:
        tip, page_item = self.tip, self.page
        self.cancel()
        if page_item is None:
            return
        end = _point(pos)
        if _near(tip, end, MIN_DRAG * 3):
            end = (tip[0] + 40, tip[1] - 30)  # a click: put the box up and to the right
        s = self.style
        page_width, page_height = page_item.rect.width(), page_item.rect.height()
        width = min(CALLOUT_WIDTH, max(page_width - end[0], 60.0)) if end[0] >= tip[0] else CALLOUT_WIDTH
        width = min(width, page_width)
        height = textlayout.fitted_height("", s.font_size, width, s.face())
        x = end[0] if end[0] >= tip[0] else end[0] - width
        # The box stays on the page whole, wherever the drag ended; the arrow still points where it was pressed.
        x = max(min(x, page_width - width), 0.0)
        y = max(min(end[1] - height / 2, page_height - height), 0.0)
        box = FreeText(page=page_item.index, rect=(x, y, width, height), font_size=s.font_size,
                       color=s.color, callout=(tip,), border_width=1.0, border_color=s.color, fill=s.fill,
                       **s.text_format(), **self.common())
        self.view.edit_text_box(box, is_new=True, tool=self.name)

    def cancel(self) -> None:
        if self.preview is not None:
            self.preview.remove()
        self.preview = None
        self.page = self.tip = None


# Insert and select ------------------------------------------------------------------


class AreaTool(Tool):
    """Drag a rectangle; subclasses decide what it is for (links, snapshots)."""

    color = QColor(42, 140, 255)

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.start = None
        self.preview: _Preview | None = None

    def press(self, page, pos, event) -> bool:
        self.cancel()
        self.page, self.start = page, _point(self.view.snapped(page, pos))
        fill = QColor(self.color)
        fill.setAlpha(36)
        self.preview = _Preview(page, self.color, fill)
        return True

    def move(self, page, pos, event) -> None:
        if self.start is None or self.preview is None:
            return
        pos = self.view.snapped(page, pos)
        path = QPainterPath()
        path.addRect(QRectF(*normalized(*self.start, pos.x(), pos.y())))
        self.preview.set_path(path)

    def release(self, page, pos, event) -> None:
        if self.start is None or self.page is None:
            self.cancel()
            return
        pos = self.view.snapped(page, pos)
        rect = normalized(*self.start, pos.x(), pos.y())
        page_item = self.page
        self.cancel()
        self.finished(page_item, rect, rect[2] >= MIN_DRAG and rect[3] >= MIN_DRAG)

    def finished(self, page, rect, dragged: bool) -> None:
        raise NotImplementedError

    def cancel(self) -> None:
        if self.preview is not None:
            self.preview.remove()
        self.preview = None
        self.page = self.start = None


class HyperlinkTool(AreaTool):
    """Drag over what should be clickable, then say where it leads. Click a link to change it."""

    def press(self, page, pos, event) -> bool:
        for link in self.view.store.all():
            if isinstance(link, Link) and link.page == page.index and QRectF(*link.rect).contains(pos):
                self.cancel()
                self.view.edit_link(link)
                return False  # the dialog had the click: no drag follows
        return super().press(page, pos, event)

    def finished(self, page, rect, dragged: bool) -> None:
        if dragged and self.view.new_link(page.index, rect) is not None:
            self.view.tool_used()


class SnapshotTool(AreaTool):
    """Drag over part of a page to copy it as a picture, annotations included unless the tool says not."""

    color = QColor(240, 180, 0)

    def finished(self, page, rect, dragged: bool) -> None:
        if dragged:
            s = self.style
            self.view.copy_area_as_image(page.index, rect, s.snapshot_scale, s.snapshot_annotations)
            self.view.tool_used()


class LassoTool(Tool):
    """Draw a loop round annotations to select them: those touching it, inside it, or with their centre inside."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.path = QPainterPath()
        self.preview: _Preview | None = None

    def press(self, page, pos, event) -> bool:
        self.cancel()
        self.page, self.path = page, QPainterPath(pos)
        fill = QColor(42, 140, 255, 24)
        self.preview = _Preview(page, QColor(42, 140, 255), fill)
        return True

    def move(self, page, pos, event) -> None:
        if self.preview is None:
            return
        self.path.lineTo(pos)
        self.preview.set_path(self.path)

    def release(self, page, pos, event) -> None:
        if self.page is None:
            self.cancel()
            return
        loop, index = QPainterPath(self.path), self.page.index
        loop.closeSubpath()
        self.cancel()
        pick = self.style.lasso_pick
        chosen = [a for a in self.view.store.all() if a.page == index and self._picked(loop, a, pick)]
        self.view.tool_settings.set_tool("select")
        self.view.select(chosen)

    @staticmethod
    def _picked(loop: QPainterPath, annotation, pick: str) -> bool:
        x, y, w, h = annotation.bounds()
        if pick == "inside":
            return loop.contains(QRectF(x, y, w, h))
        if pick == "touching":
            return loop.intersects(hit_path(annotation))
        return loop.contains(QPointF(x + w / 2, y + h / 2))

    def cancel(self) -> None:
        if self.preview is not None:
            self.preview.remove()
        self.preview = None
        self.page = None


class ClickTool(Tool):
    """Tools that act where you click: signature, audio note, equation."""

    cursor = Qt.CursorShape.CrossCursor

    def press(self, page, pos, event) -> bool:
        self.view.insert_at(self.name, page.index, _point(pos), event.globalPosition().toPoint())
        return False  # a dialog or a menu had the click: no drag follows


def make_more_tools(view) -> dict:
    tools = {}
    for name in ("pen", "marker"):
        tools[name] = FreehandTool(view, name)
    tools["eraser"] = EraserTool(view, "eraser")
    tools["tape"] = TapeTool(view, "tape")
    tools["laser"] = LaserTool(view, "laser")
    for name in ("polyline", "polygon", "perimeter", "area"):
        tools[name] = PointsTool(view, name)
    tools["distance"] = MeasureLineTool(view, "distance")
    tools["cleaner"] = CleanerTool(view, "cleaner")
    tools["callout"] = CalloutTool(view, "callout")
    tools["hyperlink"] = HyperlinkTool(view, "hyperlink")
    tools["snapshot"] = SnapshotTool(view, "snapshot")
    tools["lasso"] = LassoTool(view, "lasso")
    for name in ("signature", "audio", "equation"):
        tools[name] = ClickTool(view, name)
    return tools
