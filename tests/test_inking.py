import math
import random
import time

import pytest

from zenith_pdf.core import inking
from zenith_pdf.core.annotations import (
    DASHES,
    ENDINGS,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Poly,
    arrow_head,
    dash_array,
    line_ending,
    quarter_turn,
    round_dash_caps,
)


def _jitter(points, amount, rng):
    """Hand tremor: noise that drifts smoothly, like a real mouse or pen (a moving average of white noise)."""
    raw = [(rng.gauss(0, amount * 2.5), rng.gauss(0, amount * 2.5)) for _ in points]
    out = []
    for i, (px, py) in enumerate(points):
        window = raw[max(0, i - 4): i + 5]
        out.append((px + sum(n[0] for n in window) / len(window), py + sum(n[1] for n in window) / len(window)))
    return out


def _sample(polyline, step=1.5):
    out = []
    for p, q in zip(polyline, polyline[1:]):
        n = max(1, int(math.dist(p, q) / step))
        out += [(p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n) for i in range(n)]
    out.append(polyline[-1])
    return out


def _ellipse(cx, cy, a, b, n=120, start=0.3, overlap=0.05):
    return [(cx + a * math.cos(start + t), cy + b * math.sin(start + t))
            for t in [2 * math.pi * (1 + overlap) * i / n for i in range(n + 1)]]


CASES = {
    "rectangle": (lambda: _sample([(100, 100), (260, 102), (258, 200), (101, 198), (104, 106)]), "rectangle", 40),
    "ellipse": (lambda: _ellipse(200, 200, 90, 50), "ellipse", 40),
    "circle": (lambda: _ellipse(200, 200, 60, 60), "ellipse", 40),
    "triangle": (lambda: _sample([(100, 300), (220, 300), (160, 200), (103, 296)]), "polygon", 40),
    "pentagon": (lambda: _sample([(200 + 80 * math.cos(math.radians(-90 + 72 * k)),
                                   200 + 80 * math.sin(math.radians(-90 + 72 * k))) for k in range(6)]), "polygon", 25),
    "rotated square": (lambda: _sample([(200, 100), (300, 200), (200, 300), (100, 200), (198, 103)]), "polygon", 40),
    "line": (lambda: _sample([(50, 50), (400, 80)]), "line", 40),
    "scribble": (lambda: [(100 + 40 * math.sin(i / 3) + i, 100 + 30 * math.cos(i / 5)) for i in range(200)], "ink", 40),
    "slight curve": (lambda: [(50 + i * 3, 100 + 0.0009 * (i * 3 - 150) ** 2) for i in range(101)], "ink", 40),
    "zigzag back": (lambda: _sample([(50, 50), (300, 52), (150, 51)]), "ink", 40),
    "check mark": (lambda: _sample([(50, 100), (80, 130), (160, 40)]), "ink", 40),
    "open arc": (lambda: _ellipse(200, 200, 90, 50, start=0, overlap=-0.4), "ink", 40),
}


def test_smart_ink_recognises_what_was_meant_and_nothing_else():
    rng = random.Random(4)
    for name, (make, expected, at_least) in CASES.items():
        results = {}
        for _ in range(40):
            points = _jitter(make(), 1.2, rng)
            kind = "line" if inking.as_line(points, 1.0) else (inking.as_shape(points, 1.0) or ("ink",))[0]
            results[kind] = results.get(kind, 0) + 1
        assert results.get(expected, 0) >= at_least, (name, results)
        assert set(results) <= {expected, "ink"}, (name, results)  # never the wrong shape


def test_recognised_shapes_have_the_drawn_size():
    rect = inking.as_shape(_sample([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)]), 1.0)
    assert rect[0] == "rectangle" and rect[1] == pytest.approx((100, 100, 160, 100))
    kind, corners = inking.as_shape(_sample([(100, 300), (220, 300), (160, 200), (101, 299)]), 1.0)
    assert kind == "polygon" and len(corners) == 3 and isinstance(corners, tuple)
    assert inking.as_line([(0, 0), (10, 0.5), (40, 0)], 1.0) == ((0, 0), (40, 0))
    assert inking.as_line([(0, 0), (10, 0)], 1.0) is None  # too short at this zoom
    assert inking.as_line([(0, 0), (10, 0)], 0.25) is not None  # zoomed in, 40 px on screen


def test_erase_stroke_cuts_misses_clears_and_trims():
    stroke = [(float(x), 100.0) for x in range(0, 201, 10)]
    widths = [2.0] * len(stroke)
    left, right = inking.erase_stroke(stroke, widths, (100, 50), (100, 150), 4)
    assert left[0][0] == (0.0, 100.0) and left[0][-1][0] == pytest.approx(95.0, abs=0.01)
    assert right[0][0][0] == pytest.approx(105.0, abs=0.01) and right[0][-1] == (200.0, 100.0)
    assert len(left[0]) == len(left[1]) and len(right[0]) == len(right[1])
    assert inking.erase_stroke(stroke, widths, (100, 150), (100, 250), 4) is None
    assert inking.erase_stroke([(0, 0), (3, 0)], [2, 2], (1, -5), (1, 5), 6) == []
    [(kept, kept_widths)] = inking.erase_stroke(stroke, widths, (200, 50), (200, 150), 4)
    assert kept[0] == (0.0, 100.0) and kept[-1][0] == pytest.approx(195.0, abs=0.01)
    assert isinstance(kept, tuple) and isinstance(kept_widths, tuple)


def test_erase_reaches_the_edge_of_thick_ink_and_keeps_widths_in_step():
    stroke = [(0.0, 0.0), (100.0, 0.0)]
    assert inking.erase_stroke(stroke, [1.0, 1.0], (50, 6), (50, 20), 4) is None
    assert inking.erase_stroke(stroke, [5.0, 5.0], (50, 6), (50, 20), 4) is not None  # touches its edge
    assert inking.touches(stroke, [5.0, 5.0], (50, 6), (50, 20), 4)
    assert not inking.touches(stroke, [1.0, 1.0], (50, 6), (50, 20), 4)
    [(left, left_widths), (right, right_widths)] = inking.erase_stroke(stroke, [2.0, 4.0], (50, -20), (50, 20), 2)
    assert left_widths[0] == 2.0 and left_widths[-1] == pytest.approx(2.0 + 2.0 * left[-1][0] / 100)
    assert right_widths[0] == pytest.approx(2.0 + 2.0 * right[0][0] / 100) and right_widths[-1] == 4.0
    assert left[-1][0] < 50 - 2 < 50 + 2 < right[0][0]


def test_partial_erase_is_fast_on_long_strokes():
    long = [(i * 0.5, 100 + 10 * math.sin(i / 20)) for i in range(2000)]
    pieces = [(long, [0.2] * len(long))]  # a fine pen: the swipes leave pieces between them
    started = time.process_time()  # CPU time: other work on the machine doesn't count
    for k in range(300):
        a, b = (k * 3.3, 60.0), (k * 3.3 + 3.3, 140.0)
        left = []
        for points, widths in pieces:
            result = inking.erase_stroke(points, widths, a, b, 0.6)
            left += [(points, widths)] if result is None else result
        pieces = left
    assert time.process_time() - started < 1.0
    assert len(pieces) > 100


def _area(polygon):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1])) / 2


def test_outline_pieces_turn_one_way_and_cover_the_stroke():
    points = [(i * 1.0, 100 + 20 * math.sin(i / 15)) for i in range(400)]
    widths = inking.pro_widths(points, [i * 0.01 for i in range(400)], 3.0)
    pieces = inking.stroke_polygons(points, widths)
    assert len(pieces) == 399 and all(_area(list(p)) > 0 for p in pieces)
    assert inking.stroke_polygons([(5, 5)], [4.0]) and inking.stroke_polygons([(5, 5), (5, 5)], [4.0, 4.0])
    assert inking.stroke_polygons([], []) == []


def test_thick_strokes_get_round_ends_without_visible_facets():
    for width in (2.0, 12.0, 40.0):
        [dot] = inking.stroke_polygons([(0, 0)], [width])
        corners, radius = len(dot), width / 2
        assert corners >= 12 and (radius * (1 - math.cos(math.pi / corners)) < 0.15 or corners == 48)
    assert len(inking.stroke_polygons([(0, 0), (10, 0)], [2.0])) == 1  # one piece per segment


def test_constant_width_outline_has_the_area_of_a_capsule():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QPainterPath, QPolygonF

    r, length = 3.0, 100.0
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for piece in inking.stroke_polygons([(0, 0), (40, 0), (length, 0)], [2 * r] * 3):
        path.addPolygon(QPolygonF([QPointF(*p) for p in piece]))
        path.closeSubpath()
    union = path.simplified()
    area = abs(_area([(union.elementAt(i).x, union.elementAt(i).y) for i in range(union.elementCount())]))
    assert area == pytest.approx(2 * r * length + math.pi * r * r, rel=0.03)


def test_pro_pen_is_thinner_when_faster_and_tapers():
    points = [(i * 2.0, 0.0) for i in range(30)]
    slow = inking.pro_widths(points, [i * 0.05 for i in range(30)], 4.0)
    fast = inking.pro_widths(points, [i * 0.001 for i in range(30)], 4.0)
    assert sum(fast) < sum(slow) and max(slow) <= 4.0
    assert slow[0] < slow[15] and slow[-1] < slow[15]  # tapered ends
    assert inking.pro_widths([(0, 0)], [0.0], 4.0) == [4.0]  # a dot keeps its width


def test_calligraphy_follows_the_nib():
    across = inking.calligraphy_widths([(0, 0), (10, 0), (20, 0)], 6)
    along = inking.calligraphy_widths([(0, 0), (7, 7), (14, 14)], 6)
    assert across == pytest.approx([6 * math.sin(math.radians(45))] * 3)
    assert along == pytest.approx([6 * 0.18] * 3)
    assert inking.calligraphy_widths([(0, 0), (0, 0), (10, 0)], 6)[0] == pytest.approx(across[0])


def test_simplify_keeps_the_ends_and_the_corners():
    assert inking.simplify_indices([], 1.0) == [] and inking.simplify_indices([(0, 0)], 1.0) == [0]
    points = [(float(i), 0.0) for i in range(11)] + [(10.0, float(i)) for i in range(1, 11)]
    assert inking.simplify_indices(points, 0.5) == [0, 10, 20]


def test_every_ending_has_its_shape():
    for style in ENDINGS:
        ending = line_ending(style, (100, 50), (0, 50), 2.0)
        assert (ending is None) == (style == "None"), style
    assert line_ending("ClosedArrow", (100, 50), (0, 50), 2.0).points == arrow_head((100, 50), (0, 50), 2.0)
    reverse = line_ending("ROpenArrow", (100, 50), (0, 50), 2.0)
    assert reverse.points[1] == (100, 50) and all(p[0] > 100 for p in (reverse.points[0], reverse.points[2]))
    circle = line_ending("Circle", (100, 50), (0, 50), 2.0)
    assert circle.circle == ((100, 50), 3.5) and circle.filled
    square = line_ending("Square", (100, 50), (0, 50), 2.0)
    assert square.closed and square.filled and len(square.points) == 4
    slash = line_ending("Slash", (100, 50), (0, 50), 2.0)
    (x1, y1), (x2, y2) = slash.points
    assert math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180 == pytest.approx(120)  # "/" on screen
    assert line_ending("Bogus", (0, 0), (1, 1), 1.0) is None
    assert line_ending("Butt", (0, 0), (0, 0), 1.0) is not None  # a line of no length


def test_line_and_polyline_bounds_hold_their_endings():
    plain = Line(page=0, start=(0, 0), end=(100, 0), width=2)
    assert plain.endings() == [] and plain.label == "Line"
    arrow = Line(page=0, start=(0, 0), end=(100, 0), width=2, head_end="ClosedArrow")
    assert arrow.label == "Arrow" and arrow.heads()[0][1] == arrow.endings()[0].points
    dotted = Line(page=0, start=(0, 0), end=(100, 0), width=2, head_start="Circle", head_end="Square")
    x, y, w, h = dotted.bounds()
    assert x <= -3.5 and x + w >= 103.5 and dotted.label == "Line" and dotted.heads() == []
    open_poly = Poly(page=0, points=((0, 0), (50, 0), (50, 50)), head_end="Diamond")
    closed = Poly(page=0, points=((0, 0), (50, 0), (50, 50)), closed=True, head_end="Diamond")
    assert len(open_poly.endings()) == 1 and closed.endings() == []
    assert open_poly.bounds()[3] > closed.bounds()[3]


def test_dash_arrays():
    assert set(DASHES) == {"solid", "dotted", "dashed", "dashdot"}
    assert dash_array("solid", 2) == () and dash_array("dotted", 2) == (0.0, 4)
    assert dash_array("dashed", 0.5) == (3.0, 2.0) and dash_array("dashdot", 2) == (6, 4, 0.0, 4)
    assert round_dash_caps("dotted") and round_dash_caps("dashdot") and not round_dash_caps("dashed")


def test_new_fields_default_to_todays_look():
    ink = Ink(page=0, strokes=(((0, 0), (1, 1)), ((2, 2),)), width=3, widths=([1, 2],))
    assert ink.widths == ((1.0, 2.0),) and ink.stroke_widths() == ((1.0, 2.0), (3.0,))
    assert Ink(page=0, strokes=(((0, 0), (1, 1)),)).stroke_widths() == ((2.0, 2.0),)
    box = FreeText(page=0, rect=(10, 20, 30, 200), rotation=5)
    assert box.rotation == 1 and box.upright_size() == (200, 30)
    stamp = ImageStamp(page=0, rect=(0, 0, 40, 10))
    assert stamp.rotation == 0 and stamp.equation_size == 0 and stamp.equation_color is None
    for turns in range(4):
        a, b, c, d, e, f = quarter_turn((10, 20, 30, 40), turns)
        uw, uh = (40, 30) if turns % 2 else (30, 40)
        corners = [(a * u + c * v + e, b * u + d * v + f) for u, v in ((0, 0), (uw, 0), (0, uh), (uw, uh))]
        assert sorted(corners) == sorted([(10, 20), (40, 20), (10, 60), (40, 60)]), turns
