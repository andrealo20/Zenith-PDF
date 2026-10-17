"""Appearance streams: the drawing any PDF viewer shows for an annotation.

Every stream starts with a `cm` that maps display space (top-left origin,
y down, page rotation applied) onto PDF space. The drawing code then works in
the same coordinates as the on-screen painter, whatever the page's /Rotate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from . import inking, textlayout
from .annotations import (
    NOTE_SIZE,
    Annotation,
    Attachment,
    Caret,
    Ending,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Link,
    Measurement,
    Note,
    Poly,
    Rect,
    Redaction,
    Tape,
    arrow_head,
    cloud_bumps,
    drawn_dashes,
    ellipse_curves,
    note_ink,
    note_marks,
    quarter_turn,
    round_dash_caps,
    squiggle,
    Shape,
    TextMarkup,
    markup_stroke,
)
from .geometry import PageGeometry

BOUNDS_PAD = 2.0  # points added around the drawing so antialiased edges aren't clipped
# The PDF's built-in fonts, by (font, bold, italic): the name a stream calls each by, and the font it is.
STANDARD_FONTS = {
    ("Helvetica", False, False): ("Helv", "Helvetica"),
    ("Helvetica", True, False): ("HeBo", "Helvetica-Bold"),
    ("Helvetica", False, True): ("HeOb", "Helvetica-Oblique"),
    ("Helvetica", True, True): ("HeBO", "Helvetica-BoldOblique"),
    ("Times", False, False): ("TiRo", "Times-Roman"),
    ("Times", True, False): ("TiBo", "Times-Bold"),
    ("Times", False, True): ("TiIt", "Times-Italic"),
    ("Times", True, True): ("TiBI", "Times-BoldItalic"),
    ("Courier", False, False): ("Cour", "Courier"),
    ("Courier", True, False): ("CoBo", "Courier-Bold"),
    ("Courier", False, True): ("CoOb", "Courier-Oblique"),
    ("Courier", True, True): ("CoBO", "Courier-BoldOblique"),
}


def standard_font(face: textlayout.Face) -> tuple[str, str]:
    """(resource name, BaseFont) of the built-in font for `face`; Helvetica for a face that isn't one."""
    return STANDARD_FONTS.get(tuple(face), STANDARD_FONTS[textlayout.DEFAULT_FACE])


@dataclass(frozen=True)
class Appearance:
    content: bytes
    bbox: tuple[float, float, float, float]  # PDF space: left, bottom, right, top
    opacity: float
    blend: str | None  # PDF blend mode name, e.g. "Multiply"
    uses_font: bool
    uses_image: bool = False  # draws the picture /Im0
    face: tuple[str, bool, bool] = textlayout.DEFAULT_FACE  # the font uses_font means, as standard_font names it
    soft_fill: bool = False  # uses /GS1, a see-through fill (the inside of a measured area)


def num(value: float, digits: int = 3) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def encodable(text: str) -> bool:
    """Whether Helvetica in WinAnsi encoding can show `text`, so it can be written as text."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def pdf_string(text: str) -> str:
    """A PDF literal string in WinAnsi encoding, written in plain ASCII."""
    out = []
    for byte in text.encode("cp1252", errors="replace"):
        char = chr(byte)
        if char in "()\\":
            out.append("\\" + char)
        elif 32 <= byte < 127:
            out.append(char)
        else:
            out.append(f"\\{byte:03o}")
    return "(" + "".join(out) + ")"


def pdf_rect(geometry: PageGeometry, rect: Rect) -> tuple[float, float, float, float]:
    """Display-space rect to a PDF-space (left, bottom, right, top)."""
    x, y, w, h = rect
    corners = [geometry.to_pdf(u, v) for u, v in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def display_matrix(geometry: PageGeometry) -> tuple[float, ...]:
    e, f = geometry.to_pdf(0, 0)
    ax, ay = geometry.to_pdf(1, 0)
    cx, cy = geometry.to_pdf(0, 1)
    return ax - e, ay - f, cx - e, cy - f, e, f


def _rgb(color, stroke: bool) -> str:
    return " ".join(num(c) for c in color) + (" RG" if stroke else " rg")


def _matrix(values) -> str:
    return " ".join(num(v) for v in values) + " cm"


def _ellipse(x: float, y: float, w: float, h: float) -> list[str]:
    start, curves = ellipse_curves(x, y, w, h)
    p = lambda *points: " ".join(num(c) for point in points for c in point)
    return [f"{p(start)} m"] + [f"{p(c1, c2, end)} c" for c1, c2, end in curves]


def _stroke_style(dash: str, width: float, solid_cap: int) -> str:
    """Pen width, dash pattern and line cap: dots need round caps to show, dashes have square-cut ends."""
    pattern = drawn_dashes(dash, width)
    if not pattern:
        return f"{num(width)} w {solid_cap} J"
    return f"{num(width)} w [{' '.join(num(v) for v in pattern)}] 0 d {1 if round_dash_caps(dash) else 0} J"


def _ending(ending: Ending) -> str:
    """One line ending (or note symbol), in the current colours and pen."""
    paint = " B" if ending.filled else " S"
    if ending.circle is not None:
        (cx, cy), r = ending.circle
        return " ".join(_ellipse(cx - r, cy - r, 2 * r, 2 * r)) + paint
    return _polyline(ending.points) + (" h" if ending.closed else "") + paint


def _endings(endings: list[Ending], dashed: bool) -> list[str]:
    if not endings:
        return []
    return (["[] 0 d 1 J"] if dashed else []) + [_ending(e) for e in endings]  # endings are always solid


@lru_cache(maxsize=512)
def glyph_path(text: str, face: textlayout.Face = textlayout.DEFAULT_FACE) -> str:
    """`text` as glyph outlines (PDF path operators): the face's system font at the reference size, baseline at
    y = 0, y down.

    For characters Helvetica can't show (Greek, arrows, maths, Cyrillic, CJK): drawn as
    shapes, they look the same in every viewer without embedding a font. Qt finds
    each glyph in a fallback font when Arial has none, as it does on screen.
    """
    path = QPainterPath()
    path.addText(QPointF(0, 0), textlayout.reference_font(face), text)
    ops, i, count = [], 0, path.elementCount()
    p = lambda *elements: " ".join(f"{num(e.x, 2)} {num(e.y, 2)}" for e in elements)
    while i < count:
        element = path.elementAt(i)
        if element.type == QPainterPath.ElementType.MoveToElement:
            if ops:
                ops.append("h")
            ops.append(f"{p(element)} m")
        elif element.type == QPainterPath.ElementType.LineToElement:
            ops.append(f"{p(element)} l")
        elif element.type == QPainterPath.ElementType.CurveToElement and i + 2 < count:
            ops.append(f"{p(element, path.elementAt(i + 1), path.elementAt(i + 2))} c")
            i += 2
        i += 1
    if ops:
        ops.append("h")
    return " ".join(ops)


def _runs(line: str) -> list[tuple[int, str, bool]]:
    """(start, run, plain) for the stretches of `line` that Helvetica can show (plain) or not."""
    runs: list[tuple[int, str, bool]] = []
    for i, char in enumerate(line):
        plain = encodable(char)
        if runs and runs[-1][2] == plain:
            start, run, _ = runs[-1]
            runs[-1] = (start, run + char, plain)
        else:
            runs.append((i, char, plain))
    return runs


def _text_lines(text: str, font_size: float, x: float, y: float, width: float, color,
                face: textlayout.Face = textlayout.DEFAULT_FACE, align: str = "left",
                underline: bool = False) -> list[str]:
    """A text box's lines at (x, y), laid out `width` wide in `face`: the built-in font where it can, glyph
    outlines elsewhere, each line placed as `align` says and underlined when asked."""
    words, shapes, lines = [], [], []
    scale = font_size / textlayout.REFERENCE_PIXELS
    below, thickness = textlayout.underline(font_size)
    for line, dx, baseline in textlayout.layout(text, font_size, width, face, align):
        for start, run, plain in _runs(line):
            left = x + dx + (textlayout.text_width(line[:start], font_size, face) if start else 0.0)
            if plain:
                # y runs downwards here, so flip the text matrix to keep glyphs upright.
                words.append(f"1 0 0 -1 {num(left)} {num(y + baseline)} Tm {pdf_string(run)} Tj")
            elif glyph_path(run, face):
                shapes.append(f"q {num(scale, 5)} 0 0 {num(scale, 5)} {num(left)} {num(y + baseline)} cm "
                              f"{glyph_path(run, face)} f Q")
        drawn = line.rstrip(" ")
        if underline and drawn:
            lines.append(f"{num(x + dx)} {num(y + baseline + below)} "
                         f"{num(textlayout.text_width(drawn, font_size, face))} {num(thickness)} re f")
    ops = []
    if words:
        ops += [f"BT /{standard_font(face)[0]} {num(font_size)} Tf {_rgb(color, stroke=False)}"] + words + ["ET"]
    if shapes or lines:
        ops += [_rgb(color, stroke=False)] + shapes + lines
    return ops


def _draw(annotation: Annotation) -> tuple[list[str], bool]:
    """Drawing operators in display space, and whether the Helvetica resource is needed."""
    ops: list[str] = []
    a = annotation
    if isinstance(a, TextMarkup):
        if a.style == "Highlight":
            ops.append(_rgb(a.color, stroke=False))
            ops += [f"{num(x)} {num(y)} {num(w)} {num(h)} re f" for x, y, w, h in a.quads]
        elif a.style == "Squiggly":
            ops += ["1 J 1 j", _rgb(a.color, stroke=True)]
            for quad in a.quads:
                points, thickness = squiggle(quad, a.rotation)
                ops.append(f"{num(thickness)} w " + _polyline(points) + " S")
        else:
            ops.append(_rgb(a.color, stroke=True))
            for quad in a.quads:
                (x1, y1), (x2, y2), thickness = markup_stroke(a.style, quad, a.rotation)
                ops.append(f"{num(thickness)} w {num(x1)} {num(y1)} m {num(x2)} {num(y2)} l S")
    elif isinstance(a, Shape):
        x, y, w, h = a.rect
        if a.border:  # the border lies inside the rect
            inset = a.width / 2
            x, y, w, h = x + inset, y + inset, max(w - 2 * inset, 0), max(h - 2 * inset, 0)
            ops += [_stroke_style(a.dash, a.width, 0), _rgb(a.color, stroke=True)]
        if a.fill is not None:
            ops.append(_rgb(a.fill, stroke=False))
        # With no border and no fill there is nothing to draw.
        paint = ("B" if a.fill is not None else "S") if a.border else ("f" if a.fill is not None else "")
        if paint and a.cloudy:
            curves = cloud_bumps(*a.rect)
            path = [f"{num(curves[0][0][0])} {num(curves[0][0][1])} m"]
            path += [f"{num(c1[0])} {num(c1[1])} {num(c2[0])} {num(c2[1])} {num(p1[0])} {num(p1[1])} c"
                     for _, c1, c2, p1 in curves]
            ops += ["1 j"] + path + ["h " + paint]
        elif paint and a.shape == "Circle":
            ops += _ellipse(x, y, w, h) + [paint]
        elif paint:
            ops.append(f"{num(x)} {num(y)} {num(w)} {num(h)} re {paint}")
    elif isinstance(a, Line):
        ops += ["1 j", _stroke_style(a.dash, a.width, 1), _rgb(a.color, stroke=True), _rgb(a.color, stroke=False)]
        ops.append(f"{num(a.start[0])} {num(a.start[1])} m {num(a.end[0])} {num(a.end[1])} l S")
        ops += _endings(a.endings(), a.dash != "solid")
    elif isinstance(a, Ink):
        if a.widths:  # a varying width: each stroke's outline pieces, filled at once so their overlaps don't darken
            ops.append(_rgb(a.color, stroke=False))
            for stroke, widths in zip(a.strokes, a.stroke_widths()):
                pieces = inking.stroke_polygons(stroke, widths)
                if pieces:
                    ops.append(" ".join(_polyline(piece, 2) + " h" for piece in pieces) + " f")
        else:
            ops += ["1 J 1 j", f"{num(a.width)} w", _rgb(a.color, stroke=True)]
            for stroke in a.strokes:
                points = list(stroke) if len(stroke) > 1 else [stroke[0], (stroke[0][0] + 0.01, stroke[0][1])]
                path = [f"{num(points[0][0])} {num(points[0][1])} m"]
                path += [f"{num(x)} {num(y)} l" for x, y in points[1:]]
                ops.append(" ".join(path) + " S")
    elif isinstance(a, FreeText):
        line = a.callout_line()
        if line:
            ops += ["1 J 1 j 1 w", _rgb(a.color, stroke=True), _rgb(a.color, stroke=False), _polyline(line) + " S"]
            left, tip, right = arrow_head(line[0], line[1], 1.0)
            ops.append(f"{_polyline((left, tip, right))} h B")
        # The box is drawn upright at (x, y, w, h); a turned one is moved into its rect by a cm.
        x, y, _, _ = a.rect
        w, h = a.upright_size()
        if a.rotation:
            ops.append("q " + _matrix(quarter_turn(a.rect, a.rotation)))
            x, y = 0.0, 0.0
        if a.fill is not None:
            ops.append(f"{_rgb(a.fill, stroke=False)} {num(x)} {num(y)} {num(w)} {num(h)} re f")
        if a.border_width > 0:
            inset = a.border_width / 2
            ops.append(
                f"{num(a.border_width)} w {_rgb(a.border_color, stroke=True)} "
                f"{num(x + inset)} {num(y + inset)} {num(w - 2 * inset)} {num(h - 2 * inset)} re S"
            )
        ops.append(f"{num(x)} {num(y)} {num(w)} {num(h)} re W n")  # clip overflow to the box
        ops += _text_lines(a.contents, a.font_size, x, y, w, a.color, a.face(), a.align, a.underline)
        if a.rotation:
            ops.append("Q")
        return ops, True
    elif isinstance(a, Poly):
        ops += ["1 j", _stroke_style(a.dash, a.width, 1), _rgb(a.color, stroke=True)]
        if a.closed:
            if a.fill is not None:
                ops.append(_rgb(a.fill, stroke=False))
            paint = ("B" if a.fill is not None else "S") if a.border else ("f" if a.fill is not None else "")
            if paint:
                ops.append(_polyline(a.points) + " h " + paint)
        else:
            ops += [_rgb(a.color, stroke=False), _polyline(a.points) + " S"]
            ops += _endings(a.endings(), a.dash != "solid")
    elif isinstance(a, Tape):
        outline = _polyline(a.outline()) + " h"
        if a.revealed:  # only a dashed edge, so you know it's there
            ops += ["0.8 w [3 2] 0 d", _rgb(a.color, stroke=True), outline + " S"]
        else:
            r, g, b = a.color
            ops += ["0.6 w", _rgb(a.color, stroke=False), _rgb((r * 0.8, g * 0.8, b * 0.8), stroke=True), outline + " B"]
    elif isinstance(a, Measurement):
        ops += ["1 J 1 j", f"{num(a.width)} w", _rgb(a.color, stroke=True)]
        if a.mode == "area" and len(a.points) >= 3:
            ops += ["q /GS1 gs", _rgb(a.color, stroke=False), _polyline(a.points) + " h f Q"]
            ops.append(_polyline(a.points) + " h S")
        elif len(a.points) >= 2:
            ops.append(_polyline(a.points) + " S")
            for end, neighbour in ((a.points[0], a.points[1]), (a.points[-1], a.points[-2])):
                (x1, y1), (x2, y2) = _tick(end, neighbour, 4.0)
                ops.append(f"{num(x1)} {num(y1)} m {num(x2)} {num(y2)} l S")
        lx, ly, lw, lh = a.label_rect()
        ops += ["1 1 1 rg", _rgb(a.color, stroke=True), "0.6 w", f"{num(lx)} {num(ly)} {num(lw)} {num(lh)} re B"]
        ops += [f"BT /Helv {num(a.label_size)} Tf {_rgb(a.color, stroke=False)}",
                f"1 0 0 -1 {num(lx + 4)} {num(ly + lh * 0.7)} Tm {pdf_string(a.text())} Tj ET"]
        return ops, True
    elif isinstance(a, Link):
        if a.border > 0:
            x, y, w, h = a.rect
            ops += [f"{num(a.border)} w", _rgb(a.color, stroke=True), f"{num(x)} {num(y)} {num(w)} {num(h)} re S"]
    elif isinstance(a, Attachment):
        x, y = a.position
        s = NOTE_SIZE
        ops += ["1 j 1 J", _rgb(a.color, stroke=False), "0.3 0.3 0.3 RG 0.8 w",
                f"{num(x + 0.5)} {num(y + 0.5)} {num(s - 1)} {num(s - 1)} re B", "1 1 1 rg 1 1 1 RG 1.2 w"]
        if a.audio:  # a speaker
            ops.append(_polyline(((x + s * .2, y + s * .4), (x + s * .35, y + s * .4), (x + s * .55, y + s * .22),
                                  (x + s * .55, y + s * .78), (x + s * .35, y + s * .6), (x + s * .2, y + s * .6))) + " h f")
            ops.append(f"{num(x + s * .68)} {num(y + s * .36)} m {num(x + s * .76)} {num(y + s * .5)} "
                       f"{num(x + s * .68)} {num(y + s * .64)} v S")
        else:  # a paperclip-like loop
            ops.append(_polyline(((x + s * .38, y + s * .75), (x + s * .38, y + s * .3), (x + s * .62, y + s * .3),
                                  (x + s * .62, y + s * .7))) + " S")
    elif isinstance(a, Caret):
        ops += [_rgb(a.color, stroke=False), _polyline(a.outline()) + " h f"]
    elif isinstance(a, Redaction):
        ops += ["1 w", _rgb(a.color, stroke=True)]
        ops += [f"{num(x + 0.5)} {num(y + 0.5)} {num(w - 1)} {num(h - 1)} re S" for x, y, w, h in a.rects]
    elif isinstance(a, ImageStamp):
        # An image fills the unit square, its first row at the top: flip it into the y-down space.
        x, y, w, h = a.rect
        if a.rotation:  # drawn upright, then turned into its rect
            w, h = a.upright_size()
            ops.append(f"q {_matrix(quarter_turn(a.rect, a.rotation))} {num(w)} 0 0 {num(-h)} 0 {num(h)} cm /Im0 Do Q")
        else:
            ops.append(f"{num(w)} 0 0 {num(-h)} {num(x)} {num(y + h)} cm /Im0 Do")
    elif isinstance(a, Note):
        x, y = a.position
        s = NOTE_SIZE
        ops += [
            "1 j 1 w",
            _rgb(a.color, stroke=False),
            "0.35 0.35 0.35 RG",
            f"{num(x + 0.5)} {num(y + 0.5)} {num(s - 1)} {num(s - 1)} re B",
            f"{_rgb(note_ink(a.color), stroke=True)} {_rgb(note_ink(a.color), stroke=False)} 1.2 w 1 J",
        ]
        ops += [_ending(mark) for mark in note_marks(a.icon, a.position)]
    return ops, False


def _polyline(points, digits: int = 3) -> str:
    first, *rest = points
    return " ".join([f"{num(first[0], digits)} {num(first[1], digits)} m"]
                    + [f"{num(x, digits)} {num(y, digits)} l" for x, y in rest])


def _tick(end, neighbour, half: float):
    """A short line across a measured line at `end`, as on a drawing's dimension."""
    dx, dy = end[0] - neighbour[0], end[1] - neighbour[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * half, dx / length * half
    return (end[0] + nx, end[1] + ny), (end[0] - nx, end[1] - ny)


def build(annotation: Annotation, geometry: PageGeometry) -> Appearance:
    drawing, uses_font = _draw(annotation)
    # Highlighters, and shapes and lines in highlight mode, darken like ink on paper (Multiply).
    is_highlight = (isinstance(annotation, TextMarkup) and annotation.style == "Highlight") or (
        isinstance(annotation, Ink) and annotation.highlighter) or bool(getattr(annotation, "highlight", False))
    needs_state = is_highlight or annotation.opacity < 1
    ops = ["q", " ".join(num(v) for v in display_matrix(geometry)) + " cm"]
    if needs_state:
        ops.append("/GS0 gs")
    ops += drawing
    ops.append("Q")
    x, y, w, h = annotation.bounds()
    # A note's Rect is its icon, by convention; a picture's, a link's and an attachment's are themselves.
    pad = 0.0 if isinstance(annotation, (Note, ImageStamp, Link, Attachment)) else BOUNDS_PAD
    bbox = pdf_rect(geometry, (x - pad, y - pad, w + 2 * pad, h + 2 * pad))
    return Appearance(
        content="\n".join(ops).encode("ascii"),
        bbox=bbox,
        opacity=annotation.opacity,
        blend="Multiply" if is_highlight else ("Normal" if needs_state else None),
        uses_font=uses_font,
        uses_image=isinstance(annotation, ImageStamp),
        face=annotation.face() if isinstance(annotation, FreeText) else textlayout.DEFAULT_FACE,
        soft_fill=isinstance(annotation, Measurement) and annotation.mode == "area",
    )
