"""Line breaking for text boxes.

Text boxes are written to the PDF in Helvetica, one of the fonts every viewer
has built in. Arial has the same glyph widths, so measuring with Arial through
Qt gives line breaks that match what other viewers will show.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontMetricsF

from .annotations import LINE_SPACING, TEXT_ASCENT, TEXT_PADDING

REFERENCE_PIXELS = 100  # measure at a large size, then scale: exact at any font size


@lru_cache(maxsize=1)
def reference_font() -> QFont:
    font = QFont("Arial")
    font.setPixelSize(REFERENCE_PIXELS)
    return font


def text_width(text: str, font_size: float) -> float:
    return QFontMetricsF(reference_font()).horizontalAdvance(text) * font_size / REFERENCE_PIXELS


def wrap(text: str, font_size: float, max_width: float) -> list[str]:
    """Break text into lines no wider than max_width, honouring explicit newlines."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if current and text_width(candidate, font_size) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


@lru_cache(maxsize=1024)
def layout(text: str, font_size: float, box_width: float) -> tuple[tuple[str, float, float], ...]:
    """(line, x, baseline) relative to the box's top-left corner. Kept, as every repaint of a box asks again."""
    inner = max(box_width - 2 * TEXT_PADDING, font_size)
    step = font_size * LINE_SPACING
    first = TEXT_PADDING + font_size * TEXT_ASCENT
    return tuple((line, TEXT_PADDING, first + i * step) for i, line in enumerate(wrap(text, font_size, inner)))


def fitted_height(text: str, font_size: float, box_width: float) -> float:
    """Height a text box needs to show all of its text."""
    lines = max(len(wrap(text, font_size, max(box_width - 2 * TEXT_PADDING, font_size))), 1)
    return 2 * TEXT_PADDING + font_size * (TEXT_ASCENT + 0.25) + (lines - 1) * font_size * LINE_SPACING
