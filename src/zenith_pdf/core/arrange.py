"""Lining annotations up, spacing them evenly, and flipping them.

A group moves as one: it is lined up and spaced by its own bounds, never pulled apart. Text markup stays with
the text it marks, and locked annotations stay where they are.
"""

from __future__ import annotations

from dataclasses import replace

from . import images
from .annotations import (
    NOTE_SIZE,
    Annotation,
    Attachment,
    Caret,
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
    Shape,
    Tape,
    TextMarkup,
    TextStamp,
    union,
)

ALIGNS = ("left", "centre", "right", "top", "middle", "bottom")


def movable(a: Annotation) -> bool:
    return not a.locked and not isinstance(a, TextMarkup)


def units(annotations: list[Annotation]) -> list[list[Annotation]]:
    """The movable annotations, a group's together, in the order given."""
    found: dict[str, list[Annotation]] = {}
    out: list[list[Annotation]] = []
    for a in annotations:
        if not movable(a):
            continue
        if a.group and a.group in found:
            found[a.group].append(a)
            continue
        unit = [a]
        out.append(unit)
        if a.group:
            found[a.group] = unit
    return out


def _bounds(unit: list[Annotation]) -> Rect:
    return union(a.bounds() for a in unit)


def aligned(annotations: list[Annotation], how: str) -> list[Annotation]:
    """Each annotation lined up with the others' common edge or middle, `how` one of ALIGNS. Annotations on
    other pages than the first one's are left alone: lining up is within a page."""
    parts = [u for u in units(annotations) if u[0].page == annotations[0].page] if annotations else []
    if len(parts) < 2:
        return []
    left, top, width, height = union(_bounds(u) for u in parts)
    moved = []
    for unit in parts:
        x, y, w, h = _bounds(unit)
        dx = {"left": left - x, "centre": left + width / 2 - (x + w / 2), "right": left + width - (x + w)}.get(how, 0.0)
        dy = {"top": top - y, "middle": top + height / 2 - (y + h / 2), "bottom": top + height - (y + h)}.get(how, 0.0)
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            moved += [a.translated(dx, dy) for a in unit]
    return moved


def distributed(annotations: list[Annotation], axis: str) -> list[Annotation]:
    """Three or more spread out so the gaps between them are the same, left to right ("h") or top to bottom
    ("v"); the first and the last stay put."""
    parts = [u for u in units(annotations) if u[0].page == annotations[0].page] if annotations else []
    if len(parts) < 3:
        return []
    along = 0 if axis == "h" else 1
    parts.sort(key=lambda u: _bounds(u)[along] + _bounds(u)[along + 2] / 2)
    first, last = _bounds(parts[0]), _bounds(parts[-1])
    span = last[along] + last[along + 2] - first[along]
    sizes = sum(_bounds(u)[along + 2] for u in parts)
    gap = (span - sizes) / (len(parts) - 1)
    moved, at = [], first[along] + first[along + 2] + gap
    for unit in parts[1:-1]:
        start = _bounds(unit)[along]
        shift = at - start
        if abs(shift) > 1e-6:
            moved += [a.translated(shift, 0.0) if along == 0 else a.translated(0.0, shift) for a in unit]
        at += _bounds(unit)[along + 2] + gap
    return moved


def flipped(annotations: list[Annotation], axis: str) -> list[Annotation]:
    """Mirrored left to right ("h") or top to bottom ("v") about the middle of what is flipped, each page's
    about its own. Text stays readable: a text box or an equation moves to its mirrored place, unmirrored."""
    moved = []
    for page in sorted({a.page for a in annotations}):
        parts = [a for unit in units(annotations) for a in unit if a.page == page]
        if not parts:
            continue
        x, y, w, h = union(a.bounds() for a in parts)
        centre = x + w / 2 if axis == "h" else y + h / 2
        moved += [mirrored(a, axis, centre) for a in parts]
    return moved


def mirrored(a: Annotation, axis: str, centre: float) -> Annotation:
    """`a` flipped about the line x = centre ("h") or y = centre ("v")."""
    def point(p):
        return (2 * centre - p[0], p[1]) if axis == "h" else (p[0], 2 * centre - p[1])

    def points(ps):
        return tuple(point(p) for p in ps)

    def rect(r):
        rx, ry, rw, rh = r
        return (2 * centre - rx - rw, ry, rw, rh) if axis == "h" else (rx, 2 * centre - ry - rh, rw, rh)

    if isinstance(a, Ink):
        return replace(a, strokes=tuple(points(s) for s in a.strokes))
    if isinstance(a, Line):
        return replace(a, start=point(a.start), end=point(a.end))
    if isinstance(a, (Poly, Measurement)):
        return replace(a, points=points(a.points))
    if isinstance(a, Tape):
        return replace(a, start=point(a.start), end=point(a.end))
    if isinstance(a, FreeText):
        return replace(a, rect=rect(a.rect), callout=points(a.callout))
    if isinstance(a, ImageStamp):
        if a.equation or not a.data:
            return replace(a, rect=rect(a.rect))
        picture = images.decoded(a.data)
        if picture.isNull():
            return replace(a, rect=rect(a.rect))
        # Flipping a turned picture flips it along the same axis and turns it back the other way.
        if hasattr(picture, "flipped"):  # Qt 6.9 on
            from PySide6.QtCore import Qt

            picture = picture.flipped(Qt.Orientation.Horizontal if axis == "h" else Qt.Orientation.Vertical)
        else:
            picture = picture.mirrored(axis == "h", axis == "v")
        return replace(a, rect=rect(a.rect), data=images.encode(picture), rotation=(4 - a.rotation) % 4)
    if isinstance(a, Redaction):
        return replace(a, rects=tuple(rect(r) for r in a.rects))
    if isinstance(a, (Shape, Link, Caret, TextStamp)):
        return replace(a, rect=rect(a.rect))
    if isinstance(a, (Note, Attachment)):
        px, py = a.position
        return replace(a, position=rect((px, py, NOTE_SIZE, NOTE_SIZE))[:2])
    return a

