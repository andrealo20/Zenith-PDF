"""Annotations on the canvas: painting, hit areas, selection and resize handles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem

from ..core import images, inking, textlayout
from ..core.annotations import (
    NOTE_SIZE,
    Annotation,
    Attachment,
    Ending,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Link,
    Measurement,
    Note,
    Poly,
    Redaction,
    Shape,
    Tape,
    TextMarkup,
    arrow_head,
    cloud_bumps,
    constrain_angle,
    drawn_dashes,
    ellipse_curves,
    markup_stroke,
    normalized,
    note_ink,
    note_marks,
    quarter_turn,
    round_dash_caps,
    squiggle,
)
from ..icons import icon
from ..theme import themes

HIT_SLOP = 5.0  # points of tolerance around thin strokes
Z_ANNOTATION = 2
Z_HANDLE = 10
MAX_TIP = 500  # characters of a comment shown when pointing at marked text


class _LinkDisplay:
    """Links are invisible on the page; the hyperlink tool shows where they are."""

    visible = False


links = _LinkDisplay()


def cloud_path(rect) -> QPainterPath:
    curves = cloud_bumps(*rect)
    path = QPainterPath(QPointF(*curves[0][0]))
    for _, c1, c2, end in curves:
        path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*end))
    path.closeSubpath()
    return path


@lru_cache(maxsize=256)
def _cloud_border(w: float, h: float, width: float, dash: str) -> QPainterPath:
    """The outline of a cloud's border, for a cloud at (0, 0): stroking all those curves takes
    longer than filling an outline made once. (Simplifying it would paint faster still, but a
    resize, which needs a new one at every step, would lag.)"""
    stroker = QPainterPathStroker(dashed_pen(QColor(0, 0, 0), width, dash, Qt.PenCapStyle.RoundCap,
                                             Qt.PenJoinStyle.RoundJoin))
    return stroker.createStroke(cloud_path((0.0, 0.0, w, h)))


def paint_cloud(painter: QPainter, a: Shape) -> None:
    """A cloud: its fill, then its border, both from paths kept for its size."""
    x, y, w, h = a.rect
    painter.save()
    painter.translate(x, y)
    if a.fill is not None:
        painter.fillPath(cloud_path((0.0, 0.0, w, h)), qcolor(a.fill))
    if a.border and a.width > 0:
        painter.fillPath(_cloud_border(round(w, 3), round(h, 3), a.width, a.dash), qcolor(a.color))
    painter.restore()


def qpolygon(points) -> QPolygonF:
    return QPolygonF([QPointF(*p) for p in points])


def paint_label(painter: QPainter, a: Measurement) -> None:
    """The measured value, in a white box with the measurement's colour."""
    x, y, w, h = a.label_rect()
    painter.setPen(pen(qcolor(a.color), 0.6))
    painter.setBrush(QColor(255, 255, 255))
    painter.drawRoundedRect(QRectF(x, y, w, h), 2, 2)
    painter.setPen(qcolor(a.color))
    painter.setFont(textlayout.reference_font())
    factor = a.label_size / textlayout.REFERENCE_PIXELS
    painter.save()
    painter.translate(x + 4, y + h * 0.7)
    painter.scale(factor, factor)
    painter.drawText(QPointF(0, 0), a.text())
    painter.restore()


def qcolor(color, alpha: float = 1.0) -> QColor:
    return QColor.fromRgbF(color[0], color[1], color[2], alpha)


def pen(
    color: QColor,
    width: float,
    cap: Qt.PenCapStyle = Qt.PenCapStyle.SquareCap,
    join: Qt.PenJoinStyle = Qt.PenJoinStyle.BevelJoin,
) -> QPen:
    # PySide6's QPen constructor takes cap and join only positionally, after a style.
    result = QPen(color, width)
    result.setCapStyle(cap)
    result.setJoinStyle(join)
    return result


def dashed_pen(color: QColor, width: float, dash: str, cap: Qt.PenCapStyle, join: Qt.PenJoinStyle) -> QPen:
    """A pen for a border or a line in one of DASHES, with the saved appearance's pattern and caps.

    `cap` is for a solid line; dots get round caps and dashes flat ones, as in the PDF.
    """
    result = pen(color, width, cap, join)
    pattern = drawn_dashes(dash, width)  # dots have a length: Qt would draw nothing for none
    if pattern:
        result.setDashPattern([v / (width if width > 0 else 1.0) for v in pattern])  # Qt counts in pen widths
        result.setCapStyle(Qt.PenCapStyle.RoundCap if round_dash_caps(dash) else Qt.PenCapStyle.FlatCap)
    return result


def ellipse_path(x: float, y: float, w: float, h: float) -> QPainterPath:
    """The ellipse as the saved appearance draws it, so its dashes start at the same point."""
    start, curves = ellipse_curves(x, y, w, h)
    path = QPainterPath(QPointF(*start))
    for c1, c2, end in curves:
        path.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*end))
    path.closeSubpath()
    return path


def paint_endings(painter: QPainter, endings: list[Ending], color: QColor, width: float) -> None:
    """Line endings (and note symbols), always with a solid pen: closed ones filled when `filled`."""
    painter.setPen(pen(color, width, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    for ending in endings:
        painter.setBrush(color if ending.filled else Qt.BrushStyle.NoBrush)
        if ending.circle is not None:
            (cx, cy), r = ending.circle
            painter.drawPath(ellipse_path(cx - r, cy - r, 2 * r, 2 * r))
        elif ending.closed:
            painter.drawPolygon(qpolygon(ending.points))
        else:
            painter.drawPolyline(qpolygon(ending.points))
    painter.setBrush(Qt.BrushStyle.NoBrush)


@lru_cache(maxsize=512)
def _ink_outline(stroke: tuple, widths: tuple) -> QPainterPath:
    """The outline of one stroke of varying width, its first point at (0, 0): built once, painted often."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for piece in inking.stroke_polygons(stroke, widths):
        path.addPolygon(qpolygon(piece))
        path.closeSubpath()
    return path.simplified()  # one outline: faster to fill, and overlapping pieces don't darken


def paint_ink_outline(painter: QPainter, stroke, widths) -> None:
    """Fill one stroke of varying width with the current brush. Moving the drawing keeps the cache."""
    x0, y0 = stroke[0]
    relative = tuple((round(x - x0, 4), round(y - y0, 4)) for x, y in stroke)
    painter.save()
    painter.translate(x0, y0)
    painter.drawPath(_ink_outline(relative, tuple(widths)))
    painter.restore()


def paint_annotation(painter: QPainter, a: Annotation) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(painter.opacity() * a.opacity)
    if getattr(a, "highlight", False):  # highlight mode, for shapes and lines: darkens, never covers the text
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    if isinstance(a, TextMarkup):
        if a.style == "Highlight":
            # Multiply darkens like ink on paper: the text under it stays black.
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            for x, y, w, h in a.quads:
                painter.fillRect(QRectF(x, y, w, h), qcolor(a.color))
        elif a.style == "Squiggly":
            for quad in a.quads:
                points, thickness = squiggle(quad, a.rotation)
                painter.setPen(pen(qcolor(a.color), thickness, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPolyline(qpolygon(points))
        else:
            for quad in a.quads:
                start, end, thickness = markup_stroke(a.style, quad, a.rotation)
                painter.setPen(pen(qcolor(a.color), thickness, Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(*start), QPointF(*end))
    elif isinstance(a, Shape) and a.cloudy:
        paint_cloud(painter, a)
    elif isinstance(a, Shape):
        x, y, w, h = a.rect
        if a.border:  # the border lies inside the rect
            inset = a.width / 2
            x, y, w, h = x + inset, y + inset, max(w - a.width, 0), max(h - a.width, 0)
            painter.setPen(dashed_pen(qcolor(a.color), a.width, a.dash, Qt.PenCapStyle.SquareCap,
                                      Qt.PenJoinStyle.MiterJoin))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(a.fill) if a.fill is not None else Qt.BrushStyle.NoBrush)
        if a.shape == "Circle":
            painter.drawPath(ellipse_path(x, y, w, h))
        else:
            painter.drawRect(QRectF(x, y, w, h))
    elif isinstance(a, Line):
        color = qcolor(a.color)
        painter.setPen(dashed_pen(color, a.width, a.dash, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(*a.start), QPointF(*a.end))
        paint_endings(painter, a.endings(), color, a.width)
    elif isinstance(a, Ink):
        if a.highlighter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        if a.widths:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(a.color))
            for stroke, widths in zip(a.strokes, a.stroke_widths()):
                if stroke:
                    paint_ink_outline(painter, stroke, widths)
        else:
            painter.setPen(pen(qcolor(a.color), a.width, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for stroke in a.strokes:
                if len(stroke) == 1:
                    painter.drawPoint(QPointF(*stroke[0]))
                painter.drawPolyline(QPolygonF([QPointF(*p) for p in stroke]))
    elif isinstance(a, FreeText):
        line = a.callout_line()
        if line:
            painter.setPen(pen(qcolor(a.color), 1.0, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline(qpolygon(line))
            painter.setBrush(qcolor(a.color))
            painter.drawPolygon(qpolygon(arrow_head(line[0], line[1], 1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        paint_text_box(painter, a)
    elif isinstance(a, Poly):
        color = qcolor(a.color)
        border = dashed_pen(color, a.width, a.dash, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if a.closed:
            painter.setPen(border if a.border else QPen(Qt.PenStyle.NoPen))
            painter.setBrush(qcolor(a.fill) if a.fill is not None else Qt.BrushStyle.NoBrush)
            painter.drawPolygon(qpolygon(a.points))
        else:
            painter.setPen(border)
            painter.drawPolyline(qpolygon(a.points))
            paint_endings(painter, a.endings(), color, a.width)
    elif isinstance(a, Tape):
        shape = qpolygon(a.outline())
        r, g, b = a.color
        if a.revealed:
            edge = pen(qcolor(a.color), 1.0)
            edge.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(edge)
            painter.setBrush(qcolor(a.color, 0.12))
        else:
            painter.setPen(pen(qcolor((r * 0.8, g * 0.8, b * 0.8)), 0.6))
            painter.setBrush(qcolor(a.color))
        painter.drawPolygon(shape)
    elif isinstance(a, Measurement):
        painter.setPen(pen(qcolor(a.color), a.width, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if a.mode == "area" and len(a.points) >= 3:
            painter.setBrush(qcolor(a.color, 0.16))
            painter.drawPolygon(qpolygon(a.points))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif len(a.points) >= 2:
            painter.drawPolyline(qpolygon(a.points))
            for end, neighbour in ((a.points[0], a.points[1]), (a.points[-1], a.points[-2])):
                dx, dy = end[0] - neighbour[0], end[1] - neighbour[1]
                length = max((dx * dx + dy * dy) ** 0.5, 1e-6)
                nx, ny = -dy / length * 4, dx / length * 4
                painter.drawLine(QPointF(end[0] + nx, end[1] + ny), QPointF(end[0] - nx, end[1] - ny))
        if a.points:
            paint_label(painter, a)
    elif isinstance(a, Link):
        if a.border > 0:
            painter.setPen(pen(qcolor(a.color), a.border))
            painter.drawRect(QRectF(*a.rect))
    elif isinstance(a, Attachment):
        x, y = a.position
        s = NOTE_SIZE
        painter.setPen(QPen(QColor(77, 77, 77), 0.8))
        painter.setBrush(qcolor(a.color))
        painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, s - 1, s - 1), 3, 3)
        painter.setPen(pen(QColor("white"), 1.2, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor("white"))
        if a.audio:
            painter.drawPolygon(qpolygon(((x + s * .2, y + s * .4), (x + s * .35, y + s * .4), (x + s * .55, y + s * .22),
                                         (x + s * .55, y + s * .78), (x + s * .35, y + s * .6), (x + s * .2, y + s * .6))))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(x + s * .52, y + s * .32, s * .26, s * .36), -60 * 16, 120 * 16)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(qpolygon(((x + s * .38, y + s * .75), (x + s * .38, y + s * .3),
                                          (x + s * .62, y + s * .3), (x + s * .62, y + s * .7))))
    elif isinstance(a, Redaction):
        # Until applied, a hatched red box: clearly a mark, and the text under it still readable.
        outline = pen(qcolor(a.color), 1.5)
        outline.setCosmetic(True)
        painter.setPen(outline)
        hatch = QBrush(qcolor(a.color, 0.55), Qt.BrushStyle.BDiagPattern)
        hatch.setTransform(painter.worldTransform().inverted()[0])  # hatching stays the same size at any zoom
        for x, y, w, h in a.rects:
            painter.fillRect(QRectF(x, y, w, h), qcolor(a.color, 0.10))
            painter.fillRect(QRectF(x, y, w, h), hatch)
            painter.drawRect(QRectF(x, y, w, h))
    elif isinstance(a, ImageStamp):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(*a.rect)
        if a.rotation:  # drawn upright, then turned into its rect, as in the saved appearance
            painter.setTransform(QTransform(*quarter_turn(a.rect, a.rotation)), True)
            target = QRectF(0, 0, *a.upright_size())
        scale = painter.deviceTransform()
        width_px = target.width() * max(abs(scale.m11()) + abs(scale.m12()), 1e-6)
        painter.drawImage(target, images.for_painting(a.data, width_px))
    elif isinstance(a, Note):
        x, y = a.position
        painter.setPen(QPen(QColor(90, 90, 90), 1))
        painter.setBrush(qcolor(a.color))
        painter.drawRect(QRectF(x + 0.5, y + 0.5, NOTE_SIZE - 1, NOTE_SIZE - 1))
        paint_endings(painter, note_marks(a.icon, a.position), qcolor(note_ink(a.color)), 1.2)
    painter.restore()


def paint_text_box(painter: QPainter, a: FreeText, with_text: bool = True) -> None:
    painter.save()
    if a.rotation:  # the box is drawn upright, then turned into its rect, as in the saved appearance
        painter.setTransform(QTransform(*quarter_turn(a.rect, a.rotation)), True)
        rect = QRectF(0, 0, *a.upright_size())
    else:
        rect = QRectF(*a.rect)
    if a.fill is not None:
        painter.fillRect(rect, qcolor(a.fill))
    if a.border_width > 0:
        inset = a.border_width / 2
        painter.setPen(QPen(qcolor(a.border_color), a.border_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(inset, inset, -inset, -inset))
    if with_text:
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.setPen(qcolor(a.color))
        painter.setFont(textlayout.reference_font())
        factor = a.font_size / textlayout.REFERENCE_PIXELS
        for line, dx, baseline in textlayout.layout(a.contents, a.font_size, rect.width()):
            painter.save()
            painter.translate(rect.x() + dx, rect.y() + baseline)
            painter.scale(factor, factor)
            painter.drawText(QPointF(0, 0), line)
            painter.restore()
    painter.restore()


def hit_path(a: Annotation) -> QPainterPath:
    path = QPainterPath()
    stroker = QPainterPathStroker()
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    if isinstance(a, TextMarkup):
        for x, y, w, h in a.quads:
            path.addRect(QRectF(x, y, w, h))
        return path
    if isinstance(a, Redaction):
        for x, y, w, h in a.rects:
            path.addRect(QRectF(x, y, w, h))
        return path
    if isinstance(a, Tape):
        path.addPolygon(qpolygon(a.outline()))
        path.closeSubpath()
        return path
    if isinstance(a, Poly) and a.closed and a.fill is not None:
        path.addPolygon(qpolygon(a.points))
        path.closeSubpath()
        return path
    if isinstance(a, (Poly, Measurement)):
        path.addPolygon(qpolygon(a.points + ((a.points[0],) if getattr(a, "closed", False) or
                                             getattr(a, "mode", "") == "area" else ())))
        stroker.setWidth(a.width + 2 * HIT_SLOP)
        outline = stroker.createStroke(path)
        if isinstance(a, Measurement):
            outline.addRect(QRectF(*a.label_rect()))
            if a.mode == "area":
                outline.addPolygon(qpolygon(a.points))
        else:
            outline = _with_endings(outline, a.endings())
        return outline
    if isinstance(a, FreeText) and a.callout:
        path.addPolygon(qpolygon(a.callout_line()))
        stroker.setWidth(1 + 2 * HIT_SLOP)
        outline = stroker.createStroke(path)
        outline.addRect(QRectF(*a.rect))
        return outline
    if isinstance(a, Shape):
        rect = QRectF(*a.rect)
        if a.cloudy:
            path = cloud_path(a.rect)
        elif a.shape == "Circle":
            path.addEllipse(rect)
        else:
            path.addRect(rect)
        if a.fill is not None:
            return path
        stroker.setWidth(a.width + 2 * HIT_SLOP)
        return stroker.createStroke(path)
    if isinstance(a, Line):
        path.moveTo(*a.start)
        path.lineTo(*a.end)
        stroker.setWidth(a.width + 2 * HIT_SLOP)
        return _with_endings(stroker.createStroke(path), a.endings())
    if isinstance(a, Ink):
        for stroke in a.strokes:
            path.moveTo(*stroke[0])
            for point in stroke[1:]:
                path.lineTo(*point)
        stroker.setWidth(a.width + 2 * HIT_SLOP)
        return stroker.createStroke(path)
    path.addRect(QRectF(*a.bounds()))
    return path


def _with_endings(outline: QPainterPath, endings: list[Ending]) -> QPainterPath:
    """`outline` and the line's endings, so a click on an ending selects the line too."""
    extra = QPainterPath()
    for ending in endings:
        if ending.circle is not None:
            (cx, cy), r = ending.circle
            extra.addEllipse(QPointF(cx, cy), r + HIT_SLOP / 2, r + HIT_SLOP / 2)
        elif len(ending.points) >= 3:
            extra.addPolygon(qpolygon(ending.points))
            extra.closeSubpath()
    # A union, not added subpaths: those could turn the other way and cancel out where they overlap.
    return outline.united(extra) if not extra.isEmpty() else outline


def _stretch(old: Annotation, new: Annotation | None) -> QTransform | None:
    """The scaling that turns `old` into `new` when a handle stretches an Ink of varying width, else None.

    Its outline takes a few milliseconds a stroke to build: stretching the one
    already built keeps the drag smooth. The new outline is built once, on release.
    """
    if not (isinstance(old, Ink) and isinstance(new, Ink) and old.widths and len(old.strokes) == len(new.strokes)):
        return None
    extent = lambda ink: [f(p[i] for s in ink.strokes for p in s) for i in (0, 1) for f in (min, max)]
    x0, x1, y0, y1 = extent(old)
    u0, u1, v0, v1 = extent(new)
    sx = (u1 - u0) / (x1 - x0) if x1 - x0 > 1e-6 else 1.0
    sy = (v1 - v0) / (y1 - y0) if y1 - y0 > 1e-6 else 1.0
    return QTransform(sx, 0, 0, sy, u0 - x0 * sx, v0 - y0 * sy)


def resizable(a: Annotation) -> bool:
    return isinstance(a, (Shape, FreeText, Line, ImageStamp, Poly, Measurement, Tape, Link, Ink)) and not a.locked


def selection_rect(a: Annotation) -> tuple[float, float, float, float]:
    """The box a selection frames and resizes: a text box's own rect (a callout's line has handles of its own),
    else the bounds, which for a cloud take in its bumps."""
    return a.rect if isinstance(a, FreeText) else a.bounds()


def handle_positions(a: Annotation) -> dict[str, QPointF]:
    if isinstance(a, (Line, Tape)):
        return {"start": QPointF(*a.start), "end": QPointF(*a.end)}
    if isinstance(a, (Poly, Measurement)):
        return {f"v{i}": QPointF(*p) for i, p in enumerate(a.points)}
    extra = {}
    if isinstance(a, FreeText):
        extra = {("tip" if i == 0 else f"bend{i}"): QPointF(*p) for i, p in enumerate(a.callout)}
    x, y, w, h = selection_rect(a)
    return extra | {
        "tl": QPointF(x, y), "t": QPointF(x + w / 2, y), "tr": QPointF(x + w, y),
        "l": QPointF(x, y + h / 2), "r": QPointF(x + w, y + h / 2),
        "bl": QPointF(x, y + h), "b": QPointF(x + w / 2, y + h), "br": QPointF(x + w, y + h),
    }


def resized(a: Annotation, role: str, point: QPointF, constrain: bool) -> Annotation:
    """`a` with the handle `role` dragged to `point` (page coordinates)."""
    moved = (point.x(), point.y())
    if isinstance(a, (Line, Tape)):
        fixed = a.end if role == "start" else a.start
        if constrain:
            moved = constrain_angle(fixed, moved)
        return replace(a, start=moved) if role == "start" else replace(a, end=moved)
    if isinstance(a, (Poly, Measurement)) and role.startswith("v"):
        points = list(a.points)
        index = int(role[1:])
        if constrain and len(points) > 1:
            moved = constrain_angle(points[index - 1 if index else 1], moved)
        points[index] = moved
        return replace(a, points=tuple(points))
    if isinstance(a, FreeText) and (role == "tip" or role.startswith("bend")):
        callout = list(a.callout)
        callout[0 if role == "tip" else int(role[4:])] = moved
        return replace(a, callout=tuple(callout))
    x, y, w, h = selection_rect(a)
    left, top, right, bottom = x, y, x + w, y + h
    if isinstance(a, ImageStamp) and len(role) == 2 and not constrain and w > 0 and h > 0:
        # A corner keeps the picture's proportions (Shift stretches it): scale from the opposite corner.
        anchor_x = right if "l" in role else left
        anchor_y = bottom if "t" in role else top
        factor = max(abs(point.x() - anchor_x) / w, abs(point.y() - anchor_y) / h, 8.0 / min(w, h))
        new_w, new_h = w * factor, h * factor
        new_x = anchor_x - new_w if "l" in role else anchor_x
        new_y = anchor_y - new_h if "t" in role else anchor_y
        return replace(a, rect=(new_x, new_y, new_w, new_h))
    if "l" in role:
        left = point.x()
    if "r" in role:
        right = point.x()
    if "t" in role:
        top = point.y()
    if "b" in role:
        bottom = point.y()
    rect = normalized(left, top, right, bottom)
    rect = (rect[0], rect[1], max(rect[2], 4.0), max(rect[3], 4.0))
    if isinstance(a, Shape) and a.cloudy:  # the handles frame the bumps; the cloud's own rect lies inside them
        out = (w - a.rect[2]) / 2
        rect = (rect[0] + out, rect[1] + out, max(rect[2] - 2 * out, 4.0), max(rect[3] - 2 * out, 4.0))
    if isinstance(a, Ink):  # stretch the drawing into the new box
        sx, sy = rect[2] / max(w, 1e-6), rect[3] / max(h, 1e-6)
        strokes = tuple(tuple((rect[0] + (px - x) * sx, rect[1] + (py - y) * sy) for px, py in stroke)
                        for stroke in a.strokes)
        return replace(a, strokes=strokes)
    return replace(a, rect=rect)


CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
}


class AnnotationItem(QGraphicsItem):
    """One annotation on its page. `preview` is shown instead while a handle is dragged."""

    def __init__(self, annotation: Annotation, page_item: QGraphicsItem, interactive: bool = True):
        super().__init__(page_item)
        self._bounds: tuple[Annotation, QRectF] | None = None  # boundingRect() of the annotation shown
        self.annotation = annotation
        self.preview: Annotation | None = None
        self.on_resized: Callable[[Annotation, Annotation], None] | None = None
        self.setZValue(Z_ANNOTATION)
        self._interactive = interactive
        if interactive:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)  # for snapping while moving
        self._handles: list[Handle] = []
        self._badge: LockBadge | None = None
        self._marks = True  # the outline, handles and padlock of a selection show; reading mode hides them
        self._sync_flags()

    def show_marks(self, shown: bool) -> None:
        """Show or hide what marks the item as selected, keeping it selected: reading mode shows only the pages."""
        if shown != self._marks:
            self._marks = shown
            for handle in self._handles:
                handle.setVisible(shown)
            if self._badge is not None:
                self._badge.setVisible(shown)
            self.update()

    def _sync_flags(self) -> None:
        # Text markup belongs to the text under it; a locked annotation stays put.
        movable = self._interactive and not isinstance(self.annotation, TextMarkup) and not self.annotation.locked
        if isinstance(self.annotation, Link):  # links are followed; only the hyperlink tool edits them
            movable = movable and links.visible
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, self._interactive and links.visible)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, movable)
        # Marked text with a comment (the Note of the bar over selected text) shows it when pointed at.
        markup = isinstance(self.annotation, TextMarkup) and self._interactive
        self.setToolTip(self.annotation.contents[:MAX_TIP] if markup else "")

    def refresh_flags(self) -> None:
        if not self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable and self.isSelected():
            self.setSelected(False)
        self._sync_flags()
        self.update()

    @property
    def shown(self) -> Annotation:
        return self.preview or self.annotation

    def set_annotation(self, annotation: Annotation) -> None:
        self.prepareGeometryChange()
        self.annotation = annotation
        self.preview = None
        self.setPos(0, 0)
        self._sync_flags()
        self.show_handles(self.isSelected() and resizable(annotation))
        self._sync_badge()
        self.update()

    def set_preview(self, annotation: Annotation | None) -> None:
        self.prepareGeometryChange()
        self.preview = annotation
        self._place_handles()
        self.update()

    def boundingRect(self) -> QRectF:
        # Qt asks for it many times a frame while the pages scroll. Annotations are values that never change,
        # so the rect of the one shown is worked out once.
        shown = self.shown
        if self._bounds is None or self._bounds[0] is not shown:
            x, y, w, h = shown.bounds()
            pad = HIT_SLOP + 2
            self._bounds = (shown, QRectF(x - pad, y - pad, w + 2 * pad, h + 2 * pad))
        return self._bounds[1]

    def shape(self) -> QPainterPath:
        return hit_path(self.shown)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        stretch = _stretch(self.annotation, self.preview)
        if stretch is not None:  # resizing a drawing of varying width: stretch the outline already built
            painter.save()
            painter.setTransform(stretch, True)
            paint_annotation(painter, self.annotation)
            painter.restore()
        else:
            paint_annotation(painter, self.shown)
        if isinstance(self.shown, Link) and (links.visible or self.isSelected()) and self._marks:
            outline = QPen(QColor(themes.current.accent), 1, Qt.PenStyle.DashLine)
            outline.setCosmetic(True)
            painter.setPen(outline)
            painter.setBrush(QColor(42, 140, 255, 30))
            painter.drawRect(QRectF(*self.shown.rect))
        if self.isSelected() and self._marks:
            self._paint_selection(painter)

    def _paint_selection(self, painter: QPainter) -> None:
        """A solid 1 px accent outline: through the handles when they frame the annotation, a little
        outside it when there are none. A line's two end handles show its selection on their own."""
        roles = {handle.role for handle in self._handles}
        if "start" in roles:
            return
        pen = QPen(QColor(themes.current.accent), 1)  # a hairline, at any zoom
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(*selection_rect(self.shown))
        if "tl" not in roles:
            t = painter.worldTransform()
            pad = min(3 / max(abs(t.m11()) + abs(t.m12()), 1e-6), HIT_SLOP)  # 3 px, inside boundingRect
            rect.adjust(-pad, -pad, pad, pad)
        painter.drawRect(rect)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() is not None:
            views = self.scene().views()
            snapper = getattr(views[0], "snapper", None) if views else None
            if snapper is not None and snapper.active and self.isSelected():
                # Every selected item moves by the same amount: snap their union, then all follow alike.
                moving = [i for i in self.scene().selectedItems() if isinstance(i, AnnotationItem)
                          and i.annotation.page == self.annotation.page]
                x0, y0, x1, y1 = None, None, None, None
                for item in moving:
                    x, y, w, h = item.annotation.bounds()
                    x0 = x if x0 is None else min(x0, x)
                    y0 = y if y0 is None else min(y0, y)
                    x1 = x + w if x1 is None else max(x1, x + w)
                    y1 = y + h if y1 is None else max(y1, y + h)
                if moving:
                    dx, dy = snapper.snap_rect(self.annotation.page, (x0 + value.x(), y0 + value.y(), x1 - x0, y1 - y0),
                                               {i.annotation.id for i in moving})
                    return QPointF(value.x() + dx, value.y() + dy)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.show_handles(bool(value) and resizable(self.annotation))
            self._sync_badge()
        return super().itemChange(change, value)

    def _sync_badge(self) -> None:
        """The padlock on a selected, locked annotation."""
        wanted = self.isSelected() and self.annotation.locked
        if wanted and self._badge is None:
            self._badge = LockBadge(self)
        elif not wanted and self._badge is not None:
            self._badge.setParentItem(None)
            if self._badge.scene() is not None:
                self._badge.scene().removeItem(self._badge)
            self._badge = None
        if self._badge is not None:
            x, y, w, _ = selection_rect(self.shown)
            self._badge.setPos(x + w, y)
            self._badge.setVisible(self._marks)

    def show_handles(self, visible: bool) -> None:
        for handle in self._handles:
            handle.setParentItem(None)
            if handle.scene() is not None:
                handle.scene().removeItem(handle)
        self._handles = []
        if visible:
            self._handles = [Handle(self, role) for role in handle_positions(self.annotation)]
            self._place_handles()
            for handle in self._handles:
                handle.setVisible(self._marks)

    def _place_handles(self) -> None:
        positions = handle_positions(self.shown)
        for handle in self._handles:
            handle.setPos(positions[handle.role])

    def finish_resize(self) -> None:
        if self.preview is not None and self.preview != self.annotation and self.on_resized:
            self.on_resized(self.annotation, self.preview)
        else:
            self.set_preview(None)


class Handle(QGraphicsEllipseItem):
    """A resize handle: a white dot with an accent ring, the same size at any zoom."""

    SIZE = 10.0  # screen pixels, ring included

    def __init__(self, owner: AnnotationItem, role: str):
        ring = 1.5
        half = (self.SIZE - ring) / 2  # the ring is centred on the edge: the whole dot is SIZE wide
        super().__init__(-half, -half, 2 * half, 2 * half, owner)
        self.owner = owner
        self.role = role
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(Z_HANDLE)
        self.setPen(QPen(QColor(themes.current.accent), ring))
        self.setBrush(QBrush(Qt.GlobalColor.white))
        self.setCursor(CURSORS.get(role, Qt.CursorShape.CrossCursor))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # a round dot, not a jagged one
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawEllipse(self.rect())
        painter.restore()

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        point = self.owner.mapFromScene(event.scenePos())
        views = self.scene().views() if self.scene() is not None else []
        snapper = getattr(views[0], "snapper", None) if views else None
        if snapper is not None:
            point = snapper.snap_point(self.owner.annotation.page, point, {self.owner.annotation.id})
        constrain = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.owner.set_preview(resized(self.owner.annotation, self.role, point, constrain))

    def mouseReleaseEvent(self, event) -> None:
        views = self.scene().views() if self.scene() is not None else []
        if views and hasattr(views[0], "snapper"):
            views[0].snapper.hide()
        self.owner.finish_resize()


class LockBadge(QGraphicsItem):
    """A padlock on the top-right corner of a locked annotation's selection, the same size at any zoom."""

    SIZE = 16.0  # screen pixels
    OFFSET = 3.0  # the selection outline of a locked annotation sits this far out

    def __init__(self, owner: AnnotationItem):
        super().__init__(owner)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(Z_HANDLE)

    def boundingRect(self) -> QRectF:
        half = self.SIZE / 2 + 1
        return QRectF(self.OFFSET - half, -self.OFFSET - half, 2 * half, 2 * half)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QColor(themes.current.accent))
        disc = QRectF(self.OFFSET - self.SIZE / 2, -self.OFFSET - self.SIZE / 2, self.SIZE, self.SIZE)
        painter.drawEllipse(disc)
        glyph = disc.adjusted(3.5, 3.5, -3.5, -3.5)
        icon("lock-simple-fill", "#ffffff").paint(painter, glyph.toRect())
        painter.restore()


def line_length(a: Line) -> float:
    return QLineF(QPointF(*a.start), QPointF(*a.end)).length()
