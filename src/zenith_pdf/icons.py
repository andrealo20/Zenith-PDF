"""Theme-aware icons: Phosphor (MIT licensed, bundled with qtawesome), plus a few drawn here.

Icons have a colour by what they do, as in Acrobat's tool list: blue for files, purple for pens and
signing, pink for editing text, red for making and taking away, green for pages and searching, orange for
comments, teal for help (HUES). Little controls (a caret, a close cross, zoom) keep the text colour. Every
icon has some depth: lit from above, its colour runs from a lighter shade at the top to a deeper one at the
bottom, over a soft shadow (_DepthIcon).

Phosphor 1.3 has no squiggle, tape, lasso, laser pointer, signature or equation
glyph, so those are painted with the same thin stroke, on a 24-unit grid. So are
the pictures of the tool menu's choices: line styles, line endings, pen types and
note icons, drawn from the same geometry as the annotations themselves.
"""

from __future__ import annotations

import math

import qtawesome as qta
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QIconEngine,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from .core.annotations import DASHES, ENDINGS, PATTERNS, dash_array, line_ending, round_dash_caps
from .core.inking import calligraphy_widths, stroke_polygons
from .core.platforms import math_font, ui_font
from .theme import luminance, themes


def _squiggle(p: QPainter) -> None:
    p.drawLine(QPointF(7, 4.5), QPointF(17, 4.5))
    p.drawLine(QPointF(12, 4.5), QPointF(12, 14))
    wave = QPainterPath(QPointF(3, 19))
    for i in range(6):
        x = 3 + i * 3
        wave.quadTo(QPointF(x + 1.5, 16.5 if i % 2 == 0 else 21.5), QPointF(x + 3, 19))
    p.drawPath(wave)


def _tape(p: QPainter) -> None:
    p.drawEllipse(QPointF(9, 11), 6, 6)
    p.drawEllipse(QPointF(9, 11), 2.2, 2.2)
    strip = QPainterPath(QPointF(9, 17))
    strip.lineTo(QPointF(20, 17))
    strip.lineTo(QPointF(21.5, 15.5))
    strip.lineTo(QPointF(20, 14))
    strip.lineTo(QPointF(21.5, 12.5))
    strip.lineTo(QPointF(20, 11))
    strip.lineTo(QPointF(15, 11))
    p.drawPath(strip)


def _lasso(p: QPainter) -> None:
    p.drawEllipse(QRectF(3.5, 3.5, 17, 11))
    rope = QPainterPath(QPointF(8, 13.5))
    rope.cubicTo(QPointF(5, 17), QPointF(10, 19), QPointF(8, 21))
    p.drawPath(rope)
    p.drawEllipse(QPointF(8, 13.8), 1.2, 1.2)


def _laser(p: QPainter) -> None:
    p.drawLine(QPointF(3.5, 20.5), QPointF(13, 11))
    p.drawLine(QPointF(5.5, 21.5), QPointF(14.5, 12.5))
    p.save()
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(17, 7), 2.2, 2.2)
    p.restore()
    for angle in (-90, -45, 0, 45, 135):
        a = math.radians(angle)
        p.drawLine(QPointF(17 + 4 * math.cos(a), 7 + 4 * math.sin(a)), QPointF(17 + 6 * math.cos(a), 7 + 6 * math.sin(a)))


def _signature(p: QPainter) -> None:
    curve = QPainterPath(QPointF(3, 15))
    curve.cubicTo(QPointF(6, 3), QPointF(9, 4), QPointF(8, 12))
    curve.cubicTo(QPointF(7.5, 17), QPointF(10.5, 9), QPointF(12.5, 12))
    curve.cubicTo(QPointF(14, 14.5), QPointF(16, 9), QPointF(18, 11))
    curve.lineTo(QPointF(21, 10))
    p.drawPath(curve)
    p.drawLine(QPointF(3, 19.5), QPointF(21, 19.5))


def _glyph(text: str, italic: bool = False, size: float = 15):
    def draw(p: QPainter) -> None:
        font = QFont(math_font())
        font.setPixelSize(round(size * 4 / 3))  # pixels, not points: as large on every system
        font.setItalic(italic)
        p.setFont(font)
        p.drawText(QRectF(0, 0, 24, 24), Qt.AlignmentFlag.AlignCenter, text)
    return draw


def _cleaner(p: QPainter) -> None:
    p.drawLine(QPointF(4, 5), QPointF(14, 5))
    p.drawLine(QPointF(9, 5), QPointF(9, 18))
    p.drawLine(QPointF(14.5, 13.5), QPointF(20.5, 19.5))
    p.drawLine(QPointF(20.5, 13.5), QPointF(14.5, 19.5))


def _angle(p: QPainter) -> None:
    p.drawLine(QPointF(3.5, 19.5), QPointF(20.5, 19.5))
    p.drawLine(QPointF(3.5, 19.5), QPointF(16, 5))
    arc = QPainterPath(QPointF(12, 19.5))
    arc.arcTo(QRectF(-5, 11, 17, 17), 0, 48)
    p.drawPath(arc)


def _snapshot(p: QPainter) -> None:
    pen = QPen(p.pen())
    pen.setDashPattern([2.0, 1.6])
    p.save()
    p.setPen(pen)
    p.drawRect(QRectF(3.5, 3.5, 13, 11))
    p.restore()
    p.drawEllipse(QPointF(15.5, 18.5), 2.3, 2.3)
    p.drawEllipse(QPointF(20, 15), 2.3, 2.3)
    p.drawLine(QPointF(17, 17), QPointF(11, 11))
    p.drawLine(QPointF(18.3, 13.5), QPointF(11.5, 18))


def _dash(name: str):
    """A line in one of the DASHES, as the tool menu offers them."""
    def draw(p: QPainter) -> None:
        pen = QPen(p.pen())
        pen.setWidthF(2.0)
        pattern = dash_array(name, 2.0)
        if pattern:
            # Qt measures dashes in pen widths; a zero-length dash with a round cap is a dot.
            pen.setDashPattern([max(value / 2.0, 0.001) for value in pattern])
            pen.setCapStyle(Qt.PenCapStyle.RoundCap if round_dash_caps(name) else Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(3, 12), QPointF(21, 12))
    return draw


def _fill(name: str):
    """A square filled one of the ways a shape's fill can be drawn (PATTERNS)."""
    def draw(p: QPainter) -> None:
        box = QRectF(4, 4, 16, 16)
        area = QPainterPath()
        area.addRoundedRect(box, 2, 2)
        p.save()
        p.setClipPath(area)
        color = p.pen().color()
        if name == "solid":
            p.fillRect(box, color)
        elif name == "dots":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            for row in range(4):
                for column in range(4):
                    p.drawEllipse(QPointF(6 + column * 4 + (2 if row % 2 else 0), 6 + row * 4), 0.9, 0.9)
        else:
            thin = QPen(color, 1.2)
            p.setPen(thin)
            for i in range(-4, 9):
                p.drawLine(QPointF(4 + i * 4, 20), QPointF(20 + i * 4, 4))
                if name == "cross":
                    p.drawLine(QPointF(4 + i * 4, 4), QPointF(20 + i * 4, 20))
        p.restore()
        p.drawRoundedRect(box, 2, 2)
    return draw


def _ending(name: str, start: bool):
    """A short line ending in `name` (one of ENDINGS): on the right, or on the left for the start of a line."""
    def draw(p: QPainter) -> None:
        if start:
            p.translate(24, 0)
            p.scale(-1, 1)
        reverse = name.startswith("R")  # its wings reach past the end
        tip, tail = (15.5 if reverse else 18.0, 12.0), (3.0, 12.0)
        p.drawLine(QPointF(*tail), QPointF(*tip))
        ending = line_ending(name, tip, tail, 1.5)
        if ending is None:
            return
        p.save()
        if ending.filled:
            p.setBrush(p.pen().color())
        if ending.circle is not None:
            centre, radius = ending.circle
            p.drawEllipse(QPointF(*centre), radius, radius)
        elif ending.closed:
            p.drawPolygon(QPolygonF([QPointF(*q) for q in ending.points]))
        else:
            p.drawPolyline(QPolygonF([QPointF(*q) for q in ending.points]))
        p.restore()
    return draw


def _wave(steps: int = 24) -> list[tuple[float, float]]:
    return [(4 + 16 * i / steps, 12 - 5 * math.sin(i / steps * 2 * math.pi)) for i in range(steps + 1)]


def _filled_stroke(points, widths):
    """A stroke of varying width, filled the way the pen tools fill theirs."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    for piece in stroke_polygons(points, widths):
        path.addPolygon(QPolygonF([QPointF(*q) for q in piece]))
        path.closeSubpath()

    def draw(p: QPainter) -> None:
        p.save()
        p.setBrush(p.pen().color())
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        p.restore()
    return draw


def _pen_basic(p: QPainter) -> None:
    curve = QPainterPath(QPointF(*_wave()[0]))
    for point in _wave()[1:]:
        curve.lineTo(QPointF(*point))
    p.drawPath(curve)


def _pen_pro():
    points = _wave()
    widths = [0.6 + 2.6 * math.sin(i / (len(points) - 1) * math.pi) for i in range(len(points))]
    return _filled_stroke(points, widths)


def _pen_calligraphy():
    points = _wave()
    return _filled_stroke(points, calligraphy_widths(points, 3.4, 45.0, 0.2))


def _lock_badge(p: QPainter) -> None:
    """A padlock, filled, to sit small on a toolbar button."""
    p.save()
    pen = QPen(p.pen())
    pen.setWidthF(2.6)
    p.setPen(pen)
    p.drawArc(QRectF(7.5, 3.5, 9, 11), 0, 180 * 16)
    p.drawLine(QPointF(7.5, 9), QPointF(7.5, 11))
    p.drawLine(QPointF(16.5, 9), QPointF(16.5, 11))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(pen.color())
    p.drawRoundedRect(QRectF(4.5, 10.5, 15, 11), 2.2, 2.2)
    p.restore()


def _reading(p: QPainter) -> None:
    """Reading glasses: two round lenses, the bridge between them, and the arms."""
    p.drawEllipse(QPointF(7, 14.5), 4, 4)
    p.drawEllipse(QPointF(17, 14.5), 4, 4)
    bridge = QPainterPath(QPointF(10.6, 13))
    bridge.quadTo(QPointF(12, 11.4), QPointF(13.4, 13))
    p.drawPath(bridge)
    p.drawLine(QPointF(3.2, 13.4), QPointF(5, 7))
    p.drawLine(QPointF(20.8, 13.4), QPointF(19, 7))


def _text_highlight(p: QPainter) -> None:
    """Letters on a band of highlight: the text highlight, told apart from the freehand highlighter's nib."""
    colour = p.pen().color()
    band = QColor(colour)
    band.setAlphaF(0.45)
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(band)
    p.drawRoundedRect(QRectF(2, 12, 20, 8), 1.5, 1.5)
    p.restore()
    font = QFont(ui_font())
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.DemiBold)
    p.setFont(font)
    p.setPen(colour)
    width = p.fontMetrics().horizontalAdvance("Ab")
    p.drawText(QPointF(12 - width / 2, 17), "Ab")


def _note_insert(p: QPainter) -> None:
    """The caret proofreaders write where text goes in."""
    p.drawLine(QPointF(5, 18), QPointF(12, 7))
    p.drawLine(QPointF(12, 7), QPointF(19, 18))
    p.drawLine(QPointF(3.5, 20.5), QPointF(20.5, 20.5))


CUSTOM = {
    "z-squiggle": _squiggle,
    "z-tape": _tape,
    "z-lasso": _lasso,
    "z-laser": _laser,
    "z-signature": _signature,
    "z-equation": _glyph("π", italic=True, size=17),
    "z-cleaner": _cleaner,
    "z-snapshot": _snapshot,
    "z-angle": _angle,
    "z-pen-basic": _pen_basic,
    "z-pen-pro": _pen_pro(),
    "z-pen-calligraphy": _pen_calligraphy(),
    "z-lock-badge": _lock_badge,
    "z-note-insert": _note_insert,
    "z-reading": _reading,
    "z-paragraph": _glyph("¶", size=15),
    "z-text-highlight": _text_highlight,
}
CUSTOM |= {f"z-dash-{name}": _dash(name) for name in DASHES}
CUSTOM |= {f"z-fill-{name}": _fill(name) for name in PATTERNS}
CUSTOM |= {f"z-end-{name}": _ending(name, start=False) for name in ENDINGS}
CUSTOM |= {f"z-start-{name}": _ending(name, start=True) for name in ENDINGS}


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours, 1 to 21."""
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable(color, background: str, text: str, ratio: float = 3.0) -> str:
    """`color` (RGB floats) as an icon colour, mixed towards `text` until it stands out on `background`.

    A yellow highlighter turns to a deeper yellow on white. The hue stays, so copies stay
    told apart. A black pen keeps its black on a light toolbar and takes the text colour on
    a dark one: a grey part of the way there would look like a disabled button.
    """
    base = QColor.fromRgbF(*color)
    back, towards = QColor(background), QColor(text)
    if base.hsvSaturationF() < 0.15 and contrast(base, back) < ratio:
        return towards.name()
    mixed = base
    for step in range(11):
        share = step / 10
        mixed = QColor.fromRgbF(base.redF() + (towards.redF() - base.redF()) * share,
                                base.greenF() + (towards.greenF() - base.greenF()) * share,
                                base.blueF() + (towards.blueF() - base.blueF()) * share)
        if contrast(mixed, back) >= ratio:
            break
    return mixed.name()


class _DrawnIcon(QIconEngine):
    """An icon drawn with QPainter, coloured like the Phosphor ones in each state."""

    def __init__(self, draw, color: str, on: str, disabled: str):
        super().__init__()
        self._draw = draw
        self._colors = (color, on, disabled)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        color, on, disabled = self._colors
        if mode == QIcon.Mode.Disabled:
            color = disabled
        elif state == QIcon.State.On:
            color = on
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.topLeft())
        painter.scale(rect.width() / 24, rect.height() / 24)
        pen = QPen(QColor(color), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._draw(painter)
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        # Sharp on 125% to 200% screens: draw at device pixels, not a stretched logical pixmap.
        pixmap = QPixmap(round(size.width() * scale), round(size.height() * scale))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, pixmap.width(), pixmap.height()), mode, state)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def clone(self) -> QIconEngine:
        return _DrawnIcon(self._draw, *self._colors)


# The colours of HUES, as (on a light theme, on a dark theme): deep enough on white, light enough on black.
PALETTE = {
    "blue": ("#2d63e8", "#6f9bff"),
    "purple": ("#8a4ff0", "#b18cff"),
    "pink": ("#d63c91", "#f17cbd"),
    "red": ("#e0373f", "#ff7b7f"),
    "green": ("#22924a", "#5bd389"),
    "orange": ("#e07a14", "#ffad5c"),
    "teal": ("#11908f", "#4fd1cf"),
}
_HUE_NAMES = {
    "blue": "cursor hand folder-open folder-simple files file file-pdf export floppy-disk download-simple "
            "arrow-square-out article "
            "copy copy-simple printer app-window browsers house shield-check lock-simple lock-simple-fill "
            "clipboard clipboard-text paperclip link arrows-clockwise arrow-circle-up desktop",
    "purple": "pen-nib pencil-simple pencil-line z-signature z-pen-basic z-pen-pro z-pen-calligraphy "
              "check-square-offset user translate z-equation polygon hexagon line-segments z-tape",
    "pink": "eraser note-pencil text-t text-aa z-paragraph z-note-insert z-text-highlight marker-circle "
            "highlighter-circle",
    "red": "file-plus trash eye-slash warning-circle warning microphone microphone-slash stop "
           "arrows-in z-laser z-cleaner record x-circle lock-key file-search stack-simple",
    "green": "plus-circle squares-four image image-square camera magnifying-glass books bounding-box path ruler "
             "line-segment arrow-up-right square circle cloud z-lasso z-snapshot crop frame-corners list-numbers "
             "arrows-down-up file-x scissors",
    "orange": "chat-centered-text chat-teardrop-text note note-blank bookmark-simple bookmarks tag tag-simple "
              "star star-fill lightbulb lightbulb-filament notebook cards z-squiggle text-underline "
              "text-strikethrough",
    "teal": "git-diff check-circle info graduation-cap question keyboard speaker-high gauge sparkle wrench "
            "first-aid-kit rocket-launch book-open z-reading magnet",
}
HUES = {name: hue for hue, names in _HUE_NAMES.items() for name in names.split()}


class _Style:
    colourful = True  # Settings > View & Layout > Coloured icons


style = _Style()


def hue_colour(name: str, theme: str | None = None) -> str | None:
    """The colour `name` gets by what it does, for the light or the dark theme (the app's by default); None for
    the little controls that keep the text colour, or when coloured icons are off."""
    hue = HUES.get(name) if style.colourful else None
    if hue is None:
        return None
    light, dark = PALETTE[hue]
    return dark if (theme or themes.current.name) == "dark" else light


def _mix(color: QColor, toward: QColor, share: float) -> QColor:
    return QColor.fromRgbF(*(c + (t - c) * share for c, t in (
        (color.redF(), toward.redF()), (color.greenF(), toward.greenF()), (color.blueF(), toward.blueF()))))


class CachedIconEngine(QIconEngine):
    """Draws each picture of an icon once (by size, mode, state, screen scale and theme) with _render."""

    def __init__(self):
        super().__init__()
        self._pixmaps: dict[tuple, QPixmap] = {}

    def _render(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        raise NotImplementedError

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        key = (size.width(), size.height(), mode, state, round(scale * 100), themes.current.name)
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = self._pixmaps[key] = self._render(size, mode, state, scale)
        return pixmap

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        device = painter.device()
        scale = device.devicePixelRatioF() if device is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        return size


class _DepthIcon(CachedIconEngine):
    """An icon with depth: lit from above, its colour runs from a lighter shade at the top to a deeper one at the
    bottom, over a soft shadow a little below it. A disabled icon, or one smaller than DEPTH_LEAST, stays flat."""

    def __init__(self, flat: QIcon, color: str, on: str):
        super().__init__()
        self._flat = flat
        self._colors = (color, on)

    def _render(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        flat = self._flat.pixmap(size, scale, mode, state)
        if mode == QIcon.Mode.Disabled or min(size.width(), size.height()) < DEPTH_LEAST:
            return flat
        mask = flat.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        # Work in device pixels: a layer that kept the screen's ratio would be drawn at its logical size on the
        # others, the icon shrunk by the display scaling (half its size at 200%).
        mask.setDevicePixelRatio(1.0)
        width, height = mask.width(), mask.height()
        colour = QColor(self._colors[1] if state == QIcon.State.On else self._colors[0])
        # The icon's own lightness says what it stands on: a light icon on a dark bar takes a deeper shadow
        # and a dark one on a light bar a faint one, whatever the app's theme (a bar can change tone).
        level = luminance(colour)
        on_dark = level > 0.4

        def filled(brush) -> QImage:
            layer = QImage(mask)
            painter = QPainter(layer)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(layer.rect(), brush)
            painter.end()
            return layer

        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0.0, _mix(colour, QColor("white"), 0.16 if level < 0.06 else 0.32))
        gradient.setColorAt(0.55, colour)
        gradient.setColorAt(1.0, _mix(colour, QColor("black"), 0.12 if level < 0.06 else 0.22))
        body = filled(gradient)
        shadow = filled(QColor(0, 0, 0, 150 if on_dark else 46))
        out = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        drop = max(1.0, height * 0.05)
        for dx, dy, opacity in ((0, drop, 0.55), (-0.6, drop * 1.4, 0.25), (0.6, drop * 1.4, 0.25)):
            painter.setOpacity(opacity)  # three soft copies: a blurred shadow, cheaply
            painter.drawImage(QPointF(dx, dy), shadow)
        painter.setOpacity(1.0)
        painter.drawImage(0, 0, body)
        painter.end()
        pixmap = QPixmap.fromImage(out)
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def clone(self) -> QIconEngine:
        return _DepthIcon(self._flat, *self._colors)


DEPTH_LEAST = 12  # logical px: smaller icons (badges, carets) stay flat, where a shadow would smudge them


def icon(name: str, color: str | None = None) -> QIcon:
    """The icon `name` (Phosphor, or one drawn here), in `color`, else the colour of what it does (hue_colour),
    else the theme's text colour; with depth."""
    t = themes.current
    color = color or hue_colour(name) or t.text
    if name in CUSTOM:
        flat = QIcon(_DrawnIcon(CUSTOM[name], color, t.accent, t.text_muted))
    else:
        # qtawesome derives the hover colour from the checked one unless both are given.
        flat = qta.icon(
            name if "." in name else f"ph.{name}",
            color=color,
            color_active=color,
            color_on=t.accent,
            color_on_active=t.accent,
            color_disabled=t.text_muted,
        )
    return QIcon(_DepthIcon(flat, color, t.accent))
