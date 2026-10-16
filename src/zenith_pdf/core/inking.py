"""Geometry for hand drawing: pen widths, variable-width outlines, partial erasing and smart ink.

Pure Python, no Qt. Points are in display space (points on the page); `unit`
is the size of one screen pixel in points (1 / zoom), so the thresholds that
decide what a stroke looks like hold at any zoom.
"""

from __future__ import annotations

import math

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x, y, width, height


def _dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _lerp(p: Point, q: Point, t: float) -> Point:
    return p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    """Distance from `p` to the segment a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def path_length(points) -> float:
    return sum(_dist(p, q) for p, q in zip(points, points[1:]))


# Simplifying ----------------------------------------------------------------------


def simplify_indices(points, tolerance: float) -> list[int]:
    """The indices Ramer-Douglas-Peucker keeps, none of the dropped points further than `tolerance`.

    Indices rather than points, so the widths and times of the kept points follow.
    """
    if len(points) < 3:
        return list(range(len(points)))
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        length = math.hypot(bx - ax, by - ay)
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if length == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
            if d > worst:
                worst, index = d, i
        if index > 0 and worst > tolerance:
            keep.add(index)
            stack += [(first, index), (index, last)]
    return sorted(keep)


# Pen widths -----------------------------------------------------------------------


def pro_widths(points, times, base: float, zoom: float = 1.0) -> list[float]:
    """The "Pro" pen, without pressure: the faster the hand, the thinner the line; both ends taper.

    `times` are in seconds, one per point. Speed is measured in screen pixels per second.
    """
    widths, smooth = [], None
    for i, p in enumerate(points):
        if i == 0 or i >= len(times):
            speed = 0.0
        else:
            dt = max(times[i] - times[i - 1], 1e-3)
            speed = _dist(points[i - 1], p) * zoom / dt
        factor = 0.35 + 0.65 * math.exp(-speed / 900.0)
        smooth = factor if smooth is None else smooth + 0.35 * (factor - smooth)  # moving average
        widths.append(base * smooth)
    n = len(widths)
    if n > 1:
        for i in range(n):
            reach = min(i, n - 1 - i)
            if reach < 4:
                widths[i] *= 0.45 + 0.55 * reach / 4
    return widths


def calligraphy_widths(points, base: float, angle_degrees: float = 45.0, thinnest: float = 0.18) -> list[float]:
    """A flat nib held at `angle_degrees`: the mark is as wide as the nib seen across the direction of travel."""
    nib = math.radians(angle_degrees)
    widths: list[float | None] = []
    for i in range(len(points)):
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, len(points) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        moving = dx != 0 or dy != 0
        widths.append(base * max(thinnest, abs(math.sin(math.atan2(dy, dx) - nib))) if moving else None)
    # Where the pen stands still it keeps the width it had (or is about to have).
    known = next((w for w in widths if w is not None), base)
    for i, w in enumerate(widths):
        known = w if w is not None else known
        widths[i] = known
    return widths


# Variable-width outline ---------------------------------------------------------------


def _sides(radius: float, sides: int) -> int:
    """Corners for a round end of `radius`: at least `sides`, more for thick strokes so the facets stay small."""
    if radius <= 0.15:
        return sides
    step = 2 * math.acos(1 - 0.15 / radius)
    return max(sides, min(48, math.ceil(2 * math.pi / step)))


def _circle(centre: Point, radius: float, sides: int) -> tuple[Point, ...]:
    n = _sides(radius, sides)
    x, y = centre
    return tuple((x + radius * math.cos(2 * math.pi * k / n), y + radius * math.sin(2 * math.pi * k / n))
                 for k in range(n))


def _capsule(p: Point, rp: float, q: Point, rq: float, sides: int) -> tuple[Point, ...]:
    """The convex hull of two circles, as a polygon: one segment of a variable-width stroke."""
    d = _dist(p, q)
    if d <= abs(rp - rq) + 1e-9:  # one circle holds the other
        return _circle(p, rp, sides) if rp >= rq else _circle(q, rq, sides)
    heading = math.atan2(q[1] - p[1], q[0] - p[0])
    alpha = math.acos(max(-1.0, min(1.0, (rp - rq) / d)))  # where the outer tangents touch, from the heading
    n = _sides(max(rp, rq), sides)
    front = max(1, round(n * alpha / math.pi))  # q's arc spans 2 alpha
    back = max(1, n - front)  # p's arc spans 2 pi - 2 alpha
    points = []
    for k in range(front + 1):
        angle = heading - alpha + 2 * alpha * k / front
        points.append((q[0] + rq * math.cos(angle), q[1] + rq * math.sin(angle)))
    for k in range(back + 1):
        angle = heading + alpha + (2 * math.pi - 2 * alpha) * k / back
        points.append((p[0] + rp * math.cos(angle), p[1] + rp * math.sin(angle)))
    return tuple(points)


def stroke_polygons(points, widths, sides: int = 12) -> list[tuple[Point, ...]]:
    """A variable-width stroke as convex pieces, all turning the same way.

    Filled together with the nonzero winding rule, they give the stroke with round
    ends and joins: one piece per segment (the hull of its two end circles).
    """
    points, widths = list(points), list(widths)
    if not points:
        return []
    widths += [widths[-1] if widths else 1.0] * (len(points) - len(widths))
    radii = [max(w / 2, 0.01) for w in widths]
    pieces = []
    for i in range(len(points) - 1):
        if _dist(points[i], points[i + 1]) > 1e-9:
            pieces.append(_capsule(points[i], radii[i], points[i + 1], radii[i + 1], sides))
    if not pieces:  # a dot
        pieces.append(_circle(points[0], max(radii), sides))
    return pieces


# Erasing ------------------------------------------------------------------------------


def _far(p: Point, q: Point, a: Point, b: Point, reach: float) -> bool:
    """Whether the boxes round p-q and a-b, the second grown by `reach`, miss each other."""
    return (min(p[0], q[0]) > max(a[0], b[0]) + reach or max(p[0], q[0]) < min(a[0], b[0]) - reach
            or min(p[1], q[1]) > max(a[1], b[1]) + reach or max(p[1], q[1]) < min(a[1], b[1]) - reach)


GOLDEN = (math.sqrt(5) - 1) / 2


def _sweep_box(a: Point, b: Point, reach: float) -> tuple[float, float, float, float]:
    """left, top, right, bottom of the box round a-b grown by `reach`."""
    return min(a[0], b[0]) - reach, min(a[1], b[1]) - reach, max(a[0], b[0]) + reach, max(a[1], b[1]) + reach


def _misses(p: Point, q: Point, box: tuple[float, float, float, float]) -> bool:
    """_far against a box from _sweep_box, without working the box out again for every segment."""
    left, top, right, bottom = box
    return ((p[0] > right and q[0] > right) or (p[0] < left and q[0] < left)
            or (p[1] > bottom and q[1] > bottom) or (p[1] < top and q[1] < top))


def _hit_interval(p: Point, q: Point, wp: float, wq: float, a: Point, b: Point, radius: float):
    """The part [t0, t1] of segment p-q the eraser's sweep a-b touches, or None.

    The ink reaches half its width round the segment, so a point at t is touched when its
    distance to a-b is under radius + w(t) / 2. That distance minus a linear term is
    convex along the segment, so the touched part is a single interval.
    """
    gap = lambda t: _segment_distance(_lerp(p, q, t), a, b) - radius - (wp + (wq - wp) * t) / 2
    lo, hi = 0.0, 1.0
    m1, m2 = hi - GOLDEN * (hi - lo), lo + GOLDEN * (hi - lo)
    g1, g2 = gap(m1), gap(m2)
    for _ in range(34):  # golden-section search for the closest point: one new distance per step
        if g1 < g2:
            hi, m2, g2 = m2, m1, g1
            m1 = hi - GOLDEN * (hi - lo)
            g1 = gap(m1)
        else:
            lo, m1, g1 = m1, m2, g2
            m2 = lo + GOLDEN * (hi - lo)
            g2 = gap(m2)
    best = (lo + hi) / 2
    if gap(best) > 0:
        return None

    def edge(inside: float, outside: float) -> float:
        for _ in range(30):
            middle = (inside + outside) / 2
            if gap(middle) <= 0:
                inside = middle
            else:
                outside = middle
        return inside

    t0 = 0.0 if gap(0.0) <= 0 else edge(best, 0.0)
    t1 = 1.0 if gap(1.0) <= 0 else edge(best, 1.0)
    return t0, t1


def touches(points, widths, a: Point, b: Point, radius: float) -> bool:
    """Whether an eraser of `radius` swept from `a` to `b` touches the stroke (its width included)."""
    if not points:
        return False
    widest = max(widths) / 2 if widths else 0.0
    if len(points) == 1:
        return _segment_distance(points[0], a, b) <= radius + widest
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if _far((min(xs), min(ys)), (max(xs), max(ys)), a, b, radius + widest):
        return False
    box = _sweep_box(a, b, radius + widest)
    for i in range(len(points) - 1):
        p, q = points[i], points[i + 1]
        if not _misses(p, q, box) and _hit_interval(p, q, widths[i], widths[i + 1], a, b, radius):
            return True
    return False


def erase_stroke(points, widths, a: Point, b: Point, radius: float
                 ) -> list[tuple[tuple[Point, ...], tuple[float, ...]]] | None:
    """What is left of one stroke after an eraser of `radius` swept from `a` to `b`.

    `widths` has one width per point; half of it counts towards the reach, as the
    eraser only has to touch the ink. Returns None if the stroke is untouched, else
    the pieces left as (points, widths), [] when it is all gone. Pieces shorter than
    half a point are dropped.
    """
    points, widths = list(points), list(widths)
    if not points:
        return None
    if len(points) == 1:
        if _segment_distance(points[0], a, b) <= radius + widths[0] / 2:
            return []
        return None
    widest = max(widths) / 2
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if _far((min(xs), min(ys)), (max(xs), max(ys)), a, b, radius + widest):
        return None
    pieces, current, current_widths = [], [], []
    touched = False
    box = _sweep_box(a, b, radius + widest)
    for i in range(len(points) - 1):
        p, q, wp, wq = points[i], points[i + 1], widths[i], widths[i + 1]
        hit = None if _misses(p, q, box) else _hit_interval(p, q, wp, wq, a, b, radius)
        if hit is None:
            if not current:
                current, current_widths = [p], [wp]
            current.append(q)
            current_widths.append(wq)
            continue
        touched = True
        t0, t1 = hit
        if t0 > 0:
            if not current:
                current, current_widths = [p], [wp]
            current.append(_lerp(p, q, t0))
            current_widths.append(wp + (wq - wp) * t0)
        if len(current) >= 2:
            pieces.append((current, current_widths))
        current, current_widths = [], []
        if t1 < 1:
            current, current_widths = [_lerp(p, q, t1), q], [wp + (wq - wp) * t1, wq]
    if len(current) >= 2:
        pieces.append((current, current_widths))
    if not touched:
        return None
    return [(tuple(pts), tuple(ws)) for pts, ws in pieces if _reaches(pts, 0.5)]


def _reaches(points, length: float) -> bool:
    """Whether the path through `points` is at least `length` long; stops as soon as it is."""
    total = 0.0
    for p, q in zip(points, points[1:]):
        total += _dist(p, q)
        if total >= length:
            return True
    return False


# Smart ink ----------------------------------------------------------------------------


def as_line(points, unit: float) -> tuple[Point, Point] | None:
    """(start, end) when the stroke is nearly straight and never doubles back; else None."""
    points = list(points)
    if len(points) < 2:
        return None
    a, b = points[0], points[-1]
    chord = _dist(a, b)
    if chord < 24 * unit:
        return None
    if max(_segment_distance(p, a, b) for p in points) > max(0.04 * chord, 3 * unit):
        return None
    ux, uy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
    furthest = 0.0
    for px, py in points:  # along the chord, the hand only moves forward (with a little slack)
        t = (px - a[0]) * ux + (py - a[1]) * uy
        if t < furthest - 4 * unit:
            return None
        furthest = max(furthest, t)
    return a, b


def _angle(p: Point, q: Point, r: Point) -> float:
    """The angle at `q`, in degrees."""
    v1, v2 = (p[0] - q[0], p[1] - q[1]), (r[0] - q[0], r[1] - q[1])
    lengths = math.hypot(*v1) * math.hypot(*v2) or 1.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / lengths))))


def as_shape(points, unit: float) -> tuple[str, object] | None:
    """What a closed stroke was meant to be: ("rectangle", rect), ("ellipse", rect) or ("polygon", corners).

    None when unsure: better a stroke left as drawn than the wrong shape. Rotated
    ellipses stay strokes.
    """
    points = list(points)
    if len(points) < 8:
        return None
    perimeter = path_length(points)
    gap = _dist(points[0], points[-1])
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x, y, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if perimeter < 60 * unit or min(w, h) < 10 * unit or gap > max(0.12 * perimeter, 14 * unit):
        return None
    # Corners: start the loop at the point farthest from the centre, close it and simplify.
    cx, cy = x + w / 2, y + h / 2
    start = max(range(len(points)), key=lambda i: _dist(points[i], (cx, cy)))
    loop = points[start:] + points[:start] + [points[start]]
    tolerance = max(0.06 * max(w, h), 2 * unit)
    corners = []
    for corner in [loop[i] for i in simplify_indices(loop, tolerance)][:-1]:
        if not corners or _dist(corner, corners[-1]) > 0.08 * perimeter:  # merge corners close together
            corners.append(corner)
    if len(corners) > 2 and _dist(corners[0], corners[-1]) <= 0.08 * perimeter:
        corners.pop()
    n = len(corners)
    edges = list(zip(corners, corners[1:] + corners[:1]))
    # How far the stroke is from the ellipse in its bounding box, and from the corners' polygon.
    error = sum(abs(math.hypot((px - cx) / (w / 2), (py - cy) / (h / 2)) - 1) for px, py in points) / len(points)
    off = [min(_segment_distance(p, e0, e1) for e0, e1 in edges) for p in points] if n >= 3 else []
    polygon_error = sum(off) / len(points) / (max(w, h) / 2) if off else 1.0
    if error < 0.07 and (n > 8 or error < polygon_error):
        return "ellipse", (x, y, w, h)
    if 3 <= n <= 8 and max(off) <= max(0.08 * max(w, h), 3 * unit):
        if n == 4:
            angles = [_angle(corners[i - 1], corners[i], corners[(i + 1) % 4]) for i in range(4)]
            tilts = [abs(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))) % 90 for p, q in edges]
            if all(abs(angle - 90) < 18 for angle in angles) and all(t < 12 or t > 78 for t in tilts):
                return "rectangle", (x, y, w, h)
        return "polygon", tuple(corners)
    return None
