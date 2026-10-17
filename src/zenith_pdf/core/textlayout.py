"""Line breaking for text boxes.

Text boxes are written to the PDF in one of the fonts every viewer has built in: Helvetica, Times or Courier,
each upright or slanted, regular or bold. Arial, Times New Roman and Courier New have the same glyph widths, so
measuring with them through Qt gives line breaks that match what other viewers will show.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontMetricsF

from .annotations import LINE_SPACING, TEXT_ASCENT, TEXT_PADDING

REFERENCE_PIXELS = 100  # measure at a large size, then scale: exact at any font size

Face = tuple[str, bool, bool]  # (font, bold, italic): FreeText.face()
DEFAULT_FACE: Face = ("Helvetica", False, False)
# The PDF's built-in fonts, by the font a text box names, and the system fonts with their widths.
FAMILIES = {"Helvetica": "Arial", "Times": "Times New Roman", "Courier": "Courier New"}
ALIGNS = ("left", "center", "right")


@lru_cache(maxsize=16)
def reference_font(face: Face = DEFAULT_FACE) -> QFont:
    family, bold, italic = face
    font = QFont(FAMILIES.get(family, "Arial"))
    font.setPixelSize(REFERENCE_PIXELS)
    font.setBold(bold)
    font.setItalic(italic)
    return font


def text_width(text: str, font_size: float, face: Face = DEFAULT_FACE) -> float:
    return QFontMetricsF(reference_font(face)).horizontalAdvance(text) * font_size / REFERENCE_PIXELS


def wrap(text: str, font_size: float, max_width: float, face: Face = DEFAULT_FACE) -> list[str]:
    """Break text into lines no wider than max_width, honouring explicit newlines."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if current and text_width(candidate, font_size, face) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


@lru_cache(maxsize=1024)
def layout(text: str, font_size: float, box_width: float, face: Face = DEFAULT_FACE,
           align: str = "left") -> tuple[tuple[str, float, float], ...]:
    """(line, x, baseline) relative to the box's top-left corner, each line placed as `align` says. Kept, as
    every repaint of a box asks again."""
    inner = max(box_width - 2 * TEXT_PADDING, font_size)
    step = font_size * LINE_SPACING
    first = TEXT_PADDING + font_size * TEXT_ASCENT
    placed = []
    for i, line in enumerate(wrap(text, font_size, inner, face)):
        room = inner - text_width(line, font_size, face) if align != "left" else 0.0
        x = TEXT_PADDING + (room / 2 if align == "center" else room if align == "right" else 0.0)
        placed.append((line, x, first + i * step))
    return tuple(placed)


def fitted_height(text: str, font_size: float, box_width: float, face: Face = DEFAULT_FACE) -> float:
    """Height a text box needs to show all of its text."""
    lines = max(len(wrap(text, font_size, max(box_width - 2 * TEXT_PADDING, font_size), face)), 1)
    return 2 * TEXT_PADDING + font_size * (TEXT_ASCENT + 0.25) + (lines - 1) * font_size * LINE_SPACING


def underline(font_size: float) -> tuple[float, float]:
    """(offset below the baseline, thickness) of an underline, as the PDF draws it and the screen too."""
    return font_size * 0.12, max(font_size * 0.06, 0.5)
