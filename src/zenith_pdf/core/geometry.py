"""Conversion between PDF user space and page display space.

PDF user space has its origin at the bottom-left of the media box, with y
growing upwards. Display space is the page as the user sees it: origin at the
top-left corner of the visible box, y growing downwards, /Rotate applied.
Both use points (1/72 inch).
"""

from __future__ import annotations

from dataclasses import dataclass

Box = tuple[float, float, float, float]  # left, bottom, right, top


def multiply(m, n) -> tuple[float, ...]:
    """The PDF matrix that applies `m`, then `n`."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F


def visible_box(mediabox: Box, cropbox: Box) -> Box:
    """The area PDFium displays: the crop box clipped to the media box."""
    left = max(mediabox[0], cropbox[0])
    bottom = max(mediabox[1], cropbox[1])
    right = min(mediabox[2], cropbox[2])
    top = min(mediabox[3], cropbox[3])
    if right <= left or top <= bottom:
        return mediabox
    return left, bottom, right, top


@dataclass(frozen=True)
class PageGeometry:
    box: Box
    rotation: int  # clockwise, one of 0, 90, 180, 270

    @property
    def size(self) -> tuple[float, float]:
        left, bottom, right, top = self.box
        width, height = right - left, top - bottom
        if self.rotation in (90, 270):
            return height, width
        return width, height

    def to_display(self, x: float, y: float) -> tuple[float, float]:
        left, bottom, right, top = self.box
        if self.rotation == 0:
            return x - left, top - y
        if self.rotation == 90:
            return y - bottom, x - left
        if self.rotation == 180:
            return right - x, y - bottom
        return top - y, right - x

    def to_pdf(self, u: float, v: float) -> tuple[float, float]:
        left, bottom, right, top = self.box
        if self.rotation == 0:
            return u + left, top - v
        if self.rotation == 90:
            return v + left, u + bottom
        if self.rotation == 180:
            return right - u, v + bottom
        return right - v, top - u

    def display_turns(self, dx: float, dy: float) -> int:
        """Quarter turns clockwise on screen, 0 to 3, of the PDF-space direction (dx, dy).

        0 reads left to right; 1 runs top to bottom, as horizontal text does on a page
        turned 90 degrees. Use it with a character's text matrix (a, b) for TextMarkup.rotation.
        """
        x0, y0 = self.to_display(0.0, 0.0)
        x1, y1 = self.to_display(dx, dy)
        u, v = x1 - x0, y1 - y0
        if abs(u) >= abs(v):
            return 0 if u >= 0 else 2
        return 1 if v > 0 else 3

    def rect_to_display(self, rect: Box) -> tuple[float, float, float, float]:
        """Map a PDF rectangle to (x, y, width, height) in display space."""
        u1, v1 = self.to_display(rect[0], rect[1])
        u2, v2 = self.to_display(rect[2], rect[3])
        return min(u1, u2), min(v1, v2), abs(u2 - u1), abs(v2 - v1)
