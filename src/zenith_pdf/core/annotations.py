"""Annotation model.

Annotations are immutable values in display space: points, origin at the
top-left corner of the page as the user sees it, page rotation applied.
Editing replaces an annotation with a modified copy (dataclasses.replace),
which turns undo into swapping one value for another.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field, replace

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x, y, width, height
Color = tuple[float, float, float]  # RGB in 0..1

NOTE_SIZE = 20.0  # points, side of the note icon
TEXT_PADDING = 4.0  # points between a text box border and its text
LINE_SPACING = 1.2  # line height as a multiple of the font size
TEXT_ASCENT = 0.8  # baseline offset of the first line, as a multiple of the font size
KAPPA = 0.5522847498  # Bezier handle length of a quarter circle, as a fraction of its radius

DASHES = ("solid", "dotted", "dashed", "dashdot")
# Line endings, by their PDF /LE names.
ENDINGS = ("None", "Square", "Circle", "Diamond", "OpenArrow", "ClosedArrow",
           "Butt", "ROpenArrow", "RClosedArrow", "Slash")
NOTE_ICONS = ("Comment", "Note", "Help", "Key", "Insert", "Paragraph")  # a note's PDF /Name


def new_id() -> str:
    return f"zenith-{uuid.uuid4()}"


def union(rects) -> Rect:
    rects = list(rects)
    if not rects:
        return 0.0, 0.0, 0.0, 0.0
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return left, top, right - left, bottom - top


def points_bounds(points, pad: float = 0.0) -> Rect:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad


def normalized(x1: float, y1: float, x2: float, y2: float) -> Rect:
    """Rect spanned by two corners given in any order."""
    return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)


@dataclass(frozen=True, kw_only=True)
class Annotation:
    page: int
    color: Color = (0.0, 0.0, 0.0)
    opacity: float = 1.0
    contents: str = ""
    author: str = ""
    subject: str = ""  # the PDF /Subj: what the mark is about, e.g. the tool's name or "Important"
    locked: bool = False  # can't be moved, resized or deleted until unlocked
    group: str = ""  # annotations with the same group are selected, moved and deleted as one
    id: str = field(default_factory=new_id)

    @property
    def kind(self) -> str:
        """The PDF /Subtype, without the slash."""
        raise NotImplementedError

    @property
    def label(self) -> str:
        return self.kind

    def bounds(self) -> Rect:
        raise NotImplementedError

    def translated(self, dx: float, dy: float) -> Annotation:
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class TextMarkup(Annotation):
    style: str = "Highlight"  # Highlight, Underline, Squiggly or StrikeOut
    quads: tuple[Rect, ...] = ()  # one rectangle per line of marked text
    # Quarter turns clockwise of the marked text on screen, 0 to 3: 1 runs top to bottom, as the
    # text of a page turned 90 degrees does. Underlines and strikethroughs follow it.
    rotation: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation", int(self.rotation) % 4)

    @property
    def kind(self) -> str:
        return self.style

    @property
    def label(self) -> str:
        return {"StrikeOut": "Strikethrough", "Squiggly": "Squiggle"}.get(self.style, self.style)

    def bounds(self) -> Rect:
        return union(self.quads)

    def translated(self, dx: float, dy: float) -> TextMarkup:
        return self  # markup belongs to the text under it


@dataclass(frozen=True, kw_only=True)
class Shape(Annotation):
    shape: str = "Square"  # Square or Circle
    rect: Rect = (0.0, 0.0, 0.0, 0.0)
    width: float = 2.0
    fill: Color | None = None
    cloudy: bool = False  # a scalloped border, the "cloud" used to circle a change on a drawing
    dash: str = "solid"  # one of DASHES
    border: bool = True  # False: only the fill is drawn
    highlight: bool = False  # drawn like a highlighter: darkens, never covers the text

    @property
    def kind(self) -> str:
        return self.shape

    @property
    def label(self) -> str:
        if self.cloudy:
            return "Cloud"
        return "Rectangle" if self.shape == "Square" else "Ellipse"

    def bounds(self) -> Rect:
        """The rect; a cloud's bumps and border reach out of it."""
        if not self.cloudy:
            return self.rect
        x, y, w, h = self.rect
        out = cloud_reach(w, h) + (self.width / 2 if self.border else 0.0)
        return x - out, y - out, w + 2 * out, h + 2 * out

    def translated(self, dx: float, dy: float) -> Shape:
        x, y, w, h = self.rect
        return replace(self, rect=(x + dx, y + dy, w, h))


@dataclass(frozen=True, kw_only=True)
class Line(Annotation):
    start: Point = (0.0, 0.0)
    end: Point = (0.0, 0.0)
    width: float = 2.0
    head_start: str = "None"  # one of ENDINGS
    head_end: str = "None"
    dash: str = "solid"  # one of DASHES; the endings are always solid
    highlight: bool = False

    @property
    def kind(self) -> str:
        return "Line"

    @property
    def label(self) -> str:
        return "Arrow" if "Arrow" in self.head_start + self.head_end else "Line"

    def heads(self) -> list[tuple[str, tuple[Point, Point, Point]]]:
        """(style, (wing, tip, wing)) for each end with an OpenArrow or ClosedArrow; see endings()."""
        result = []
        if self.head_end in ("OpenArrow", "ClosedArrow"):
            result.append((self.head_end, arrow_head(self.end, self.start, self.width)))
        if self.head_start in ("OpenArrow", "ClosedArrow"):
            result.append((self.head_start, arrow_head(self.start, self.end, self.width)))
        return result

    def endings(self) -> list[Ending]:
        """The shapes drawn at both ends, start first; an end without one is left out."""
        ends = (line_ending(self.head_start, self.start, self.end, self.width),
                line_ending(self.head_end, self.end, self.start, self.width))
        return [e for e in ends if e is not None]

    def bounds(self) -> Rect:
        return points_bounds([self.start, self.end] + ending_points(self.endings()), self.width)

    def translated(self, dx: float, dy: float) -> Line:
        return replace(
            self,
            start=(self.start[0] + dx, self.start[1] + dy),
            end=(self.end[0] + dx, self.end[1] + dy),
        )


@dataclass(frozen=True, kw_only=True)
class Ink(Annotation):
    strokes: tuple[tuple[Point, ...], ...] = ()
    width: float = 2.0  # with `widths`, the largest of them (bounds, hit area, /BS W)
    highlighter: bool = False  # drawn like a highlighter pen: darkens, never covers the text
    # () draws every point at `width`. Else one tuple per stroke, a width for each of its points.
    widths: tuple[tuple[float, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.widths:
            object.__setattr__(self, "widths", tuple(tuple(float(w) for w in ws) for ws in self.widths))

    @property
    def kind(self) -> str:
        return "Ink"

    @property
    def label(self) -> str:
        return "Highlighter" if self.highlighter else "Drawing"

    def stroke_widths(self) -> tuple[tuple[float, ...], ...]:
        """A width for every point of every stroke: `width` where `widths` has none, or doesn't fit."""
        result = []
        for i, stroke in enumerate(self.strokes):
            given = self.widths[i] if i < len(self.widths) else ()
            result.append(given if len(given) == len(stroke) else (self.width,) * len(stroke))
        return tuple(result)

    def bounds(self) -> Rect:
        return points_bounds([p for stroke in self.strokes for p in stroke], self.width)

    def translated(self, dx: float, dy: float) -> Ink:
        return replace(self, strokes=tuple(tuple((x + dx, y + dy) for x, y in s) for s in self.strokes))


@dataclass(frozen=True, kw_only=True)
class FreeText(Annotation):
    """A text box. `contents` is the text, `color` the text colour."""

    rect: Rect = (0.0, 0.0, 0.0, 0.0)
    font_size: float = 12.0
    fill: Color | None = None
    border_width: float = 0.0
    border_color: Color = (0.0, 0.0, 0.0)
    callout: tuple[Point, ...] = ()  # a callout's line: the arrow's tip first, then where it bends
    # Quarter turns clockwise of the text in its box, 0 to 3: a box keeps its look when its page turns.
    rotation: int = 0
    font: str = "Helvetica"  # Helvetica, Times or Courier: the PDF's built-in fonts (textlayout.FAMILIES)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"  # left, center or right

    def __post_init__(self) -> None:
        object.__setattr__(self, "rotation", int(self.rotation) % 4)

    def face(self) -> tuple[str, bool, bool]:
        """(font, bold, italic): how textlayout measures the text and the appearance names its font."""
        return self.font, self.bold, self.italic

    @property
    def kind(self) -> str:
        return "FreeText"

    @property
    def label(self) -> str:
        return "Callout" if self.callout else "Text box"

    def upright_size(self) -> tuple[float, float]:
        """The box as its text sees it, (width, height): the lines are laid out in this width."""
        return upright_size(self.rect, self.rotation)

    def with_height(self, height: float) -> FreeText:
        """This box made `height` tall as its text sees it, the top of its text staying where it is."""
        x, y, w, h = self.rect
        grow = height - self.upright_size()[1]
        rect = {0: (x, y, w, h + grow), 1: (x - grow, y, w + grow, h), 2: (x, y - grow, w, h + grow),
                3: (x, y, w + grow, h)}[self.rotation]
        return replace(self, rect=rect)

    def callout_line(self) -> tuple[Point, ...]:
        """The whole callout line, ending where it meets the box."""
        if not self.callout:
            return ()
        x, y, w, h = self.rect
        bend = self.callout[-1]
        end = (x if bend[0] < x + w / 2 else x + w, min(max(bend[1], y), y + h))
        return (*self.callout, end)

    def bounds(self) -> Rect:
        if not self.callout:
            return self.rect
        tip = arrow_head(self.callout[0], self.callout_line()[1], 1.0)
        return union([self.rect, points_bounds(self.callout_line() + tip, 1.0)])

    def translated(self, dx: float, dy: float) -> FreeText:
        x, y, w, h = self.rect
        return replace(self, rect=(x + dx, y + dy, w, h), callout=tuple((px + dx, py + dy) for px, py in self.callout))


@dataclass(frozen=True, kw_only=True)
class Note(Annotation):
    """A sticky note: an icon on the page, `contents` holds its text."""

    position: Point = (0.0, 0.0)  # top-left corner of the icon
    icon: str = "Comment"  # one of NOTE_ICONS

    @property
    def kind(self) -> str:
        return "Text"

    @property
    def label(self) -> str:
        return "Note"

    def bounds(self) -> Rect:
        return self.position[0], self.position[1], NOTE_SIZE, NOTE_SIZE

    def translated(self, dx: float, dy: float) -> Note:
        return replace(self, position=(self.position[0] + dx, self.position[1] + dy))


@dataclass(frozen=True, kw_only=True)
class Caret(Annotation):
    """A proofreader's mark for text to put in: a caret where it goes, `contents` the text."""

    rect: Rect = (0.0, 0.0, 0.0, 0.0)  # the caret's tip at the top middle, its feet on the bottom edge

    @property
    def kind(self) -> str:
        return "Caret"

    @property
    def label(self) -> str:
        return "Inserted text"

    def bounds(self) -> Rect:
        return self.rect

    def translated(self, dx: float, dy: float) -> Caret:
        x, y, w, h = self.rect
        return replace(self, rect=(x + dx, y + dy, w, h))

    def outline(self) -> tuple[Point, ...]:
        """The caret's shape: two strokes meeting at the top, hollowed at the bottom."""
        x, y, w, h = self.rect
        return (x, y + h), (x + w / 2, y), (x + w, y + h), (x + w / 2, y + h * 0.62)


CARET_BLUE = (0.13, 0.4, 0.95)  # a caret for text to put in, as proofreaders mark it


def caret_at(line: Rect) -> Rect:
    """Where a caret goes for text put in after the last letter of `line` (a line of text's rectangle): under
    the line's end, its tip at the baseline, as wide as a letter."""
    x, y, w, h = line
    size = max(min(h * 0.55, 14.0), 4.0)
    return x + w - size / 2, y + h * 0.82, size, size


def regrouped(annotations: list) -> list:
    """Copies keep their groups among themselves, under new group names: a copy is not one with what it came
    from."""
    names: dict[str, str] = {}
    return [replace(a, group=names.setdefault(a.group, new_id())) if a.group else a for a in annotations]


@dataclass(frozen=True, kw_only=True)
class ImageStamp(Annotation):
    """A picture on the page. `data` is the picture as PNG or JPEG bytes.

    Two images are equal when their pictures' bytes are: `digest` stands for
    `data` in comparisons, so undo and saving never compare megabytes.
    """

    rect: Rect = (0.0, 0.0, 0.0, 0.0)
    data: bytes = field(default=b"", repr=False, compare=False)
    equation: str = ""  # for an equation, its source (LaTeX), to edit it again
    equation_size: float = 0.0  # the font size it was typeset at; 0 = unknown
    equation_color: Color | None = None  # the colour it was typeset in; None = unknown
    # Quarter turns clockwise of the picture in `rect`, 0 to 3: it stays editable when its page turns.
    rotation: int = 0
    digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "digest", hashlib.blake2b(self.data, digest_size=16).hexdigest())
        object.__setattr__(self, "rotation", int(self.rotation) % 4)

    @property
    def kind(self) -> str:
        return "Stamp"

    @property
    def label(self) -> str:
        return "Equation" if self.equation else "Image"

    def upright_size(self) -> tuple[float, float]:
        """The picture's own (width, height) on the page, before its quarter turns."""
        return upright_size(self.rect, self.rotation)

    def bounds(self) -> Rect:
        return self.rect

    def translated(self, dx: float, dy: float) -> ImageStamp:
        x, y, w, h = self.rect
        return replace(self, rect=(x + dx, y + dy, w, h))


@dataclass(frozen=True, kw_only=True)
class Redaction(Annotation):
    """An area marked for redaction: still a mark, until the redactions are applied.

    Applying replaces the page with a picture of itself in which these areas are
    filled with `fill`, so the text and images under them are gone for good.
    """

    rects: tuple[Rect, ...] = ()
    fill: Color = (0.0, 0.0, 0.0)
    color: Color = (0.86, 0.12, 0.12)  # the outline shown until it's applied

    @property
    def kind(self) -> str:
        return "Redact"

    @property
    def label(self) -> str:
        return "Redaction"

    def bounds(self) -> Rect:
        return union(self.rects)

    def translated(self, dx: float, dy: float) -> Redaction:
        return replace(self, rects=tuple((x + dx, y + dy, w, h) for x, y, w, h in self.rects))


@dataclass(frozen=True, kw_only=True)
class Poly(Annotation):
    """A polyline, or a polygon when closed."""

    points: tuple[Point, ...] = ()
    closed: bool = False
    width: float = 2.0
    fill: Color | None = None
    dash: str = "solid"  # one of DASHES
    border: bool = True  # False: only the fill is drawn
    highlight: bool = False
    head_start: str = "None"  # one of ENDINGS, drawn only when the polyline is open
    head_end: str = "None"

    @property
    def kind(self) -> str:
        return "Polygon" if self.closed else "PolyLine"

    @property
    def label(self) -> str:
        return "Polygon" if self.closed else "Polyline"

    def endings(self) -> list[Ending]:
        """The shapes drawn at both ends of an open polyline, start first; none for a polygon."""
        if self.closed or len(self.points) < 2:
            return []
        first, last = self.points[0], self.points[-1]
        toward_first = next((p for p in self.points[1:] if p != first), self.points[1])
        toward_last = next((p for p in reversed(self.points[:-1]) if p != last), self.points[-2])
        ends = (line_ending(self.head_start, first, toward_first, self.width),
                line_ending(self.head_end, last, toward_last, self.width))
        return [e for e in ends if e is not None]

    def bounds(self) -> Rect:
        return points_bounds(list(self.points) + ending_points(self.endings()), self.width)

    def translated(self, dx: float, dy: float) -> Poly:
        return replace(self, points=tuple((x + dx, y + dy) for x, y in self.points))


@dataclass(frozen=True, kw_only=True)
class Tape(Annotation):
    """A strip of tape over the page, for studying: it hides what is under it until revealed."""

    start: Point = (0.0, 0.0)
    end: Point = (0.0, 0.0)
    width: float = 18.0
    revealed: bool = False
    color: Color = (0.98, 0.78, 0.36)

    @property
    def kind(self) -> str:
        return "Polygon"

    @property
    def label(self) -> str:
        return "Tape"

    def outline(self) -> tuple[Point, ...]:
        return tape_outline(self.start, self.end, self.width)

    def bounds(self) -> Rect:
        return points_bounds(self.outline())

    def translated(self, dx: float, dy: float) -> Tape:
        return replace(self, start=(self.start[0] + dx, self.start[1] + dy), end=(self.end[0] + dx, self.end[1] + dy))


UNITS = {"pt": 1.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0}  # points in one unit, on paper
REAL_UNITS = ("mm", "cm", "m", "km", "in", "ft", "yd", "mi", "pt")


@dataclass(frozen=True, kw_only=True)
class Measurement(Annotation):
    """A measured distance, perimeter or area, labelled with its value at the drawing's scale.

    `scale` turns points on the page into `unit`s in reality: 1 cm on paper
    standing for 1 m makes scale = 1 / (72 / 2.54) m per point.
    """

    points: tuple[Point, ...] = ()
    mode: str = "distance"  # distance, perimeter or area
    scale: float = 2.54 / 72  # cm per point: life size
    unit: str = "cm"
    width: float = 1.5
    color: Color = (0.1, 0.45, 0.95)
    label_size: float = 9.0

    @property
    def kind(self) -> str:
        return {"distance": "Line", "perimeter": "PolyLine"}.get(self.mode, "Polygon")

    @property
    def label(self) -> str:
        return self.mode.capitalize()

    def value(self) -> float:
        if self.mode == "area":
            return polygon_area(self.points) * self.scale ** 2
        return path_length(self.points) * self.scale

    def text(self) -> str:
        value = self.value()
        digits = 0 if value >= 100 else 1 if value >= 10 else 2
        return f"{value:.{digits}f} {self.unit}{'²' if self.mode == 'area' else ''}"

    def label_rect(self) -> Rect:
        from .textlayout import text_width

        width = text_width(self.text(), self.label_size) + 8
        height = self.label_size * 1.6
        if self.mode == "area" and len(self.points) >= 3:
            cx, cy = centroid(self.points)
            return cx - width / 2, cy - height / 2, width, height
        if self.mode == "distance" and len(self.points) >= 2:
            (x1, y1), (x2, y2) = self.points[0], self.points[-1]
            return (x1 + x2) / 2 - width / 2, (y1 + y2) / 2 - height - 3, width, height
        x, y = self.points[-1] if self.points else (0.0, 0.0)
        return x + 6, y - height - 3, width, height

    def bounds(self) -> Rect:
        if not self.points:
            return 0.0, 0.0, 0.0, 0.0
        return union([points_bounds(self.points, self.width + 4), self.label_rect()])

    def translated(self, dx: float, dy: float) -> Measurement:
        return replace(self, points=tuple((x + dx, y + dy) for x, y in self.points))


@dataclass(frozen=True, kw_only=True)
class Link(Annotation):
    """A clickable area: to a web address, or to a page of this document."""

    rect: Rect = (0.0, 0.0, 0.0, 0.0)
    uri: str = ""
    target: int = -1  # page index, when not a web address
    # The spot on the target page, in its PDF user space as in [page /XYZ x y zoom]: no need for
    # that page's geometry to read or write it. None goes to the whole page (/Fit).
    target_point: Point | None = None
    border: float = 0.0
    color: Color = (0.1, 0.45, 0.95)

    @property
    def kind(self) -> str:
        return "Link"

    @property
    def label(self) -> str:
        return "Link"

    def bounds(self) -> Rect:
        return self.rect

    def translated(self, dx: float, dy: float) -> Link:
        x, y, w, h = self.rect
        return replace(self, rect=(x + dx, y + dy, w, h))


@dataclass(frozen=True, kw_only=True)
class Attachment(Annotation):
    """A file carried by the page, shown as an icon: an audio note, or any file."""

    position: Point = (0.0, 0.0)
    file_name: str = "attachment"
    data: bytes = field(default=b"", repr=False, compare=False)
    audio: bool = False
    color: Color = (0.96, 0.45, 0.33)
    digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "digest", hashlib.blake2b(self.data, digest_size=16).hexdigest())

    @property
    def kind(self) -> str:
        return "FileAttachment"

    @property
    def label(self) -> str:
        return "Audio note" if self.audio else "Attachment"

    def bounds(self) -> Rect:
        return self.position[0], self.position[1], NOTE_SIZE, NOTE_SIZE

    def translated(self, dx: float, dy: float) -> Attachment:
        return replace(self, position=(self.position[0] + dx, self.position[1] + dy))


AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".wma", ".flac", ".opus")


def overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


# Shared geometry: used both to paint on screen and to write appearance streams,
# so an annotation looks the same in Zenith PDF and in any other PDF viewer.


def merge_line_rects(rects: tuple[Rect, ...], turns: int = 0) -> tuple[Rect, ...]:
    """Join the rectangles PDFium returns for a text span into one rectangle per line.

    PDFium reports one rectangle per run of characters, so selecting
    "Wide Supply-Voltage Range" yields three boxes with gaps at the spaces.
    The rects arrive in reading order, in display space (x, y, width, height).
    `turns` is the text's quarter turns on screen (TextMarkup.rotation): with an
    odd number the lines run vertically, and the test looks along them.
    """
    merged: list[Rect] = []
    for rect in rects:
        if merged:
            mx, my, mw, mh = _upright(merged[-1], turns)
            x, y, w, h = _upright(rect, turns)
            # Same line: they share at least half of the shorter one's height
            # (tolerates superscripts). Neighbours: the gap is under one line height,
            # so words join but side-by-side columns stay apart.
            overlap = min(my + mh, y + h) - max(my, y)
            gap = x - (mx + mw)
            if overlap >= 0.5 * min(mh, h) and -max(mh, h) < gap < max(mh, h):
                merged[-1] = union([merged[-1], rect])
                continue
        merged.append(rect)
    return tuple(merged)


def _upright(rect: Rect, turns: int) -> Rect:
    """`rect` in a frame where text turned `turns` times on screen reads left to right."""
    x, y, w, h = rect
    return {0: (x, y, w, h), 1: (y, x, h, w), 2: (-x - w, y, w, h), 3: (-y - h, x, h, w)}[turns % 4]


def markup_stroke(style: str, quad: Rect, turns: int = 0) -> tuple[Point, Point, float] | None:
    """The line drawn for an underline or strikeout: (start, end, thickness).

    `turns` is TextMarkup.rotation: the line runs along the text and an underline
    sits on the text's own bottom edge, whichever way the text runs on screen.
    """
    w, h = upright_size(quad, turns)
    thickness = max(h / 14, 0.5)
    if style == "Underline":
        level = h - thickness
    elif style == "StrikeOut":
        level = h * 0.55
    else:
        return None
    start, end = _turned(quad, turns, [(0.0, level), (w, level)])
    return start, end, thickness


def _turned(quad: Rect, turns: int, points) -> list[Point]:
    """Points given upright in (0, 0, *upright_size(quad, turns)), placed in `quad` turned `turns` times."""
    a, b, c, d, e, f = quarter_turn(quad, turns)
    return [(a * u + c * v + e, b * u + d * v + f) for u, v in points]


def arrow_head(tip: Point, tail: Point, line_width: float) -> tuple[Point, Point, Point]:
    """Arrowhead at `tip` for a line coming from `tail`: (left wing, tip, right wing)."""
    length = max(line_width * 4.5, 6.0)
    angle = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
    spread = math.radians(28)
    left = (tip[0] - length * math.cos(angle - spread), tip[1] - length * math.sin(angle - spread))
    right = (tip[0] - length * math.cos(angle + spread), tip[1] - length * math.sin(angle + spread))
    return left, tip, right


@dataclass(frozen=True)
class Ending:
    """The shape at one end of a line, in display space, always drawn with a solid pen. A note's symbol
    is made of these too (note_marks)."""

    points: tuple[Point, ...]  # a polyline, or a polygon when closed; empty for a circle
    closed: bool
    filled: bool  # filled with the line's colour
    circle: tuple[Point, float] | None = None  # a Circle ending: centre and radius


def line_ending(style: str, tip: Point, toward: Point, width: float) -> Ending | None:
    """The ending `style` (one of ENDINGS) at `tip`, for a line that comes from `toward`; None for "None"."""
    length = math.hypot(tip[0] - toward[0], tip[1] - toward[1])
    ux, uy = ((tip[0] - toward[0]) / length, (tip[1] - toward[1]) / length) if length > 1e-9 else (1.0, 0.0)
    nx, ny = -uy, ux
    side = max(3.5 * width, 5.0)
    at = lambda along, across: (tip[0] + ux * along + nx * across, tip[1] + uy * along + ny * across)
    if style in ("OpenArrow", "ClosedArrow"):
        closed = style == "ClosedArrow"
        return Ending(arrow_head(tip, toward, width), closed, closed)
    if style in ("ROpenArrow", "RClosedArrow"):  # the point at the end, the wings opening outwards
        closed = style == "RClosedArrow"
        return Ending(arrow_head(tip, (tip[0] + ux, tip[1] + uy), width), closed, closed)
    half = side / 2
    if style == "Square":
        return Ending((at(-half, -half), at(half, -half), at(half, half), at(-half, half)), True, True)
    if style == "Diamond":
        return Ending((at(-half, 0), at(0, -half), at(half, 0), at(0, half)), True, True)
    if style == "Circle":
        return Ending((), True, True, (tip, half))
    if style == "Butt":
        return Ending((at(0, -half), at(0, half)), False, False)
    if style == "Slash":  # 30 degrees clockwise from across the line; y grows downwards here
        turn = math.radians(30)
        along, across = half * math.sin(turn), half * math.cos(turn)
        return Ending((at(along, -across), at(-along, across)), False, False)
    return None


def ending_points(endings) -> list[Point]:
    """Every point the endings reach, a circle by its bounding box: for bounds."""
    points = []
    for ending in endings:
        points += ending.points
        if ending.circle is not None:
            (cx, cy), r = ending.circle
            points += [(cx - r, cy - r), (cx + r, cy + r)]
    return points


def dash_array(dash: str, width: float) -> tuple[float, ...]:
    """The PDF dash array, in points, for a stroke `width` wide; () for solid."""
    w = width if width > 0 else 1.0
    if dash == "dotted":
        return 0.0, 2 * w
    if dash == "dashed":
        return max(3 * w, 3.0), max(2 * w, 2.0)
    if dash == "dashdot":
        return max(3 * w, 3.0), 2 * w, 0.0, 2 * w
    return ()


DOT = 0.1  # points: how long a dot is drawn. Viewers disagree on dashes of no length (PDFium makes them 0.1).


def drawn_dashes(dash: str, width: float) -> tuple[float, ...]:
    """dash_array as written to the PDF and painted on screen: each dot DOT long, taken off the gap after it,
    so every viewer spaces the dots alike."""
    pattern = list(dash_array(dash, width))
    for i in range(0, len(pattern) - 1, 2):
        if pattern[i] < DOT:
            pattern[i], pattern[i + 1] = DOT, max(pattern[i + 1] - DOT + pattern[i], 0.01)
    return tuple(pattern)


def round_dash_caps(dash: str) -> bool:
    """Whether the dashes need round caps: dots are dashes of no length, round caps make them show."""
    return dash in ("dotted", "dashdot")


def upright_size(rect: Rect, turns: int) -> tuple[float, float]:
    """(width, height) of what `rect` holds, before it was turned `turns` quarter turns."""
    return (rect[3], rect[2]) if turns % 2 else (rect[2], rect[3])


def quarter_turn(rect: Rect, turns: int) -> tuple[float, float, float, float, float, float]:
    """The matrix (a, b, c, d, e, f) that puts content drawn upright at (0, 0, *upright_size) into `rect`,
    turned clockwise by `turns` quarter turns: x' = a u + c v + e, y' = b u + d v + f."""
    x, y, w, h = rect
    return {
        0: (1.0, 0.0, 0.0, 1.0, x, y),
        1: (0.0, 1.0, -1.0, 0.0, x + w, y),
        2: (-1.0, 0.0, 0.0, -1.0, x + w, y + h),
        3: (0.0, -1.0, 1.0, 0.0, x, y + h),
    }[turns % 4]


def squiggle(quad: Rect, turns: int = 0) -> tuple[tuple[Point, ...], float]:
    """The wavy line under a squiggly markup: zigzag points along the text's bottom, and its thickness.

    `turns` is TextMarkup.rotation, as for markup_stroke.
    """
    w, h = upright_size(quad, turns)
    amplitude = max(h / 14, 0.6)
    step = max(h / 6, 1.5)
    level = h - amplitude * 1.5
    points, i = [], 0
    while i * step <= w + 0.01:
        points.append((i * step, level + (amplitude if i % 2 else -amplitude)))
        i += 1
    if len(points) < 2:
        points = [(0.0, level), (w, level)]
    return tuple(_turned(quad, turns, points)), max(h / 18, 0.5)


def ellipse_curves(x: float, y: float, w: float, h: float) -> tuple[Point, list[tuple[Point, Point, Point]]]:
    """The ellipse in a rect as a start point and four cubic curves (control, control, end).

    It starts on the right and goes clockwise on screen, in the painter and in the
    PDF alike, so dashes fall in the same places.
    """
    rx, ry = w / 2, h / 2
    cx, cy = x + rx, y + ry
    kx, ky = rx * KAPPA, ry * KAPPA
    return (cx + rx, cy), [
        ((cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry)),
        ((cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy)),
        ((cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry)),
        ((cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy)),
    ]


def note_ink(color: Color) -> Color:
    """The colour of the symbol on a note: dark grey, or white on a dark note so it still shows."""
    r, g, b = color
    return (1.0, 1.0, 1.0) if 0.299 * r + 0.587 * g + 0.114 * b < 0.45 else (0.3, 0.3, 0.3)


def note_marks(icon: str, position: Point) -> list[Ending]:
    """The symbol drawn on a note's square, one per NOTE_ICONS name ("Comment" for unknown ones).

    Drawn in note_ink with a 1.2 pt round pen: open marks are stroked, closed ones
    stroked and filled when `filled`.
    """
    x, y = position
    s = NOTE_SIZE
    at = lambda *uv: tuple((x + s * u, y + s * v) for u, v in zip(uv[::2], uv[1::2]))
    line = lambda *uv: Ending(at(*uv), False, False)
    if icon == "Note":  # a sheet with a folded corner and two lines of writing
        return [Ending(at(.28, .2, .6, .2, .74, .34, .74, .8, .28, .8), True, False), line(.6, .2, .6, .34, .74, .34),
                line(.37, .5, .65, .5), line(.37, .65, .65, .65)]
    if icon == "Help":  # a question mark
        hook = [(.5 + .15 * math.cos(math.radians(a)), .36 + .15 * math.sin(math.radians(a)))
                for a in range(180, 405, 15)]
        return [line(*[c for p in hook + [(.5, .6)] for c in p]), Ending((), True, True, (at(.5, .76)[0], s * .05))]
    if icon == "Key":  # a ring, a shaft and two teeth
        return [Ending((), True, False, (at(.33, .5)[0], s * .13)), line(.46, .5, .8, .5), line(.7, .5, .7, .64),
                line(.8, .5, .8, .6)]
    if icon == "Insert":  # a caret, the proofreader's mark for "insert here"
        return [line(.24, .72, .5, .3, .76, .72)]
    if icon == "Paragraph":  # a pilcrow
        bowl = [(.48 + .15 * math.cos(math.radians(a)), .37 + .13 * math.sin(math.radians(a))) for a in range(90, 285, 15)]
        return [Ending(at(*[c for p in bowl for c in p]), True, True), line(.48, .24, .48, .8), line(.63, .24, .63, .8),
                line(.48, .24, .72, .24)]
    return [line(.22, .32 + i * .18, .78, .32 + i * .18) for i in range(3)]  # Comment: lines of text


def _bump_size(w: float, h: float) -> float:
    """About how wide the bumps of a cloud round a w x h rect are."""
    return max(min(min(w, h) / 3, 12.0), 4.0)


def cloud_reach(w: float, h: float) -> float:
    """How far the bumps of cloud_bumps reach out of their rect: the middle of each curve lies 3/4 of the
    way out to its control points, which stand out 0.75 times the bump's width."""
    size = _bump_size(w, h)
    return max(0.5625 * length / max(1, round(length / size)) for length in (w, h))


def cloud_bumps(x: float, y: float, w: float, h: float) -> list[tuple[Point, Point, Point, Point]]:
    """A cloud border round a rectangle, as cubic curves (start, control, control, end), clockwise.

    Each edge is split into arcs about as wide as they are tall, bulging outwards.
    """
    size = _bump_size(w, h)
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    curves = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        length = math.hypot(bx - ax, by - ay) or 1e-9
        count = max(1, round(length / size))
        ux, uy = (bx - ax) / length, (by - ay) / length
        nx, ny = uy, -ux  # outward, as the corners go clockwise on screen
        for i in range(count):
            p0 = (ax + (bx - ax) * i / count, ay + (by - ay) * i / count)
            p1 = (ax + (bx - ax) * (i + 1) / count, ay + (by - ay) * (i + 1) / count)
            bulge = 0.75 * length / count
            curves.append((p0, (p0[0] + nx * bulge, p0[1] + ny * bulge), (p1[0] + nx * bulge, p1[1] + ny * bulge), p1))
    return curves


def tape_outline(start: Point, end: Point, width: float) -> tuple[Point, ...]:
    """A strip of tape from `start` to `end`, with torn, zigzag ends."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    half, tooth = width / 2, min(width / 6, length / 4)
    teeth = 4

    def edge(base: Point, outward: float, reverse: bool) -> list[Point]:
        points = []
        for i in range(teeth + 1):
            t = -half + width * i / teeth
            depth = tooth * (i % 2) * outward
            points.append((base[0] + nx * t + ux * depth, base[1] + ny * t + uy * depth))
        return points[::-1] if reverse else points

    return tuple(edge(end, 1.0, False) + edge(start, -1.0, True))


def path_length(points) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def polygon_area(points) -> float:
    """Shoelace formula; the absolute area."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, list(points[1:]) + list(points[:1])):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def centroid(points) -> Point:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def simplify(points: list[Point], tolerance: float) -> list[Point]:
    """Fewer points for a hand-drawn stroke, none further than `tolerance` from the original
    (Ramer-Douglas-Peucker)."""
    if len(points) < 3:
        return list(points)
    (ax, ay), (bx, by) = points[0], points[-1]
    length = math.hypot(bx - ax, by - ay)
    worst, index = 0.0, 0
    for i, (px, py) in enumerate(points[1:-1], start=1):
        if length == 0:
            distance = math.hypot(px - ax, py - ay)
        else:
            distance = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
        if distance > worst:
            worst, index = distance, i
    if worst <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def constrain_angle(anchor: Point, point: Point, step_degrees: float = 45.0) -> Point:
    """Snap the direction anchor -> point to the nearest multiple of `step_degrees`."""
    dx, dy = point[0] - anchor[0], point[1] - anchor[1]
    length = math.hypot(dx, dy)
    step = math.radians(step_degrees)
    angle = round(math.atan2(dy, dx) / step) * step
    return anchor[0] + length * math.cos(angle), anchor[1] + length * math.sin(angle)
