"""Small building blocks shared across the interface."""

from __future__ import annotations

import math
import random
from functools import lru_cache

import shiboken6
from PIL import Image, ImageChops, ImageFilter
from PySide6.QtCore import (QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QIcon,
    QIconEngine,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRegion,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from ..core.i18n import tr
from ..core.platforms import native_keys
from ..icons import hue_colour, icon
from ..theme import Look, Theme, bar_radius, outline, qcolor, themes, tone_sheet
from . import lens, motion

GLASS_BLEED = 24  # logical px of backdrop sampled beyond the bar: the blur reaches its edges from what is there
CAPSULE_MAX = 48  # px: a floating bar this tall or less is a capsule, its ends half circles
BLUR_STEP = 2  # logical px per texel of the blurred backdrop, which the painter stretches smoothly
BLUR_SIGMA = 6.0  # logical px: lines of text behind melt into an even wash, never strokes to read
# The Liquid glass is one clear lens over the whole bar: a veil just strong enough to tell glass from a hole,
# under a dark bar and under a light one (whose dark text needs a little more over a dark page), and a rim as wide
# as half the bar, so the page bends across all of it.
LIQUID_VEIL = (0.06, 0.10)
SMOKED = 0.6  # a look whose clear glass is tinted more than this is smoked glass by design
LIQUID_BAND = 24  # logical px: the widest rim, so a tall panel keeps a flat middle to read through
# With nothing laid over the page, text and icons on a Liquid bar stand on a soft halo of their own: the shape of
# the bar's content, spread HALO_SPREAD and blurred HALO_SIGMA logical px, as dense as HALO_GAIN makes it.
HALO_SPREAD = 2
HALO_SIGMA = 3.0
HALO_GAIN = 2.6
HALO_NEAR = 0.85  # the least the halo is next to a glyph: what coloured icons are made readable on
HALO_RECHECK_MS = 250  # after the last repaint that kept the halo, look once whether the content changed meanwhile
# 0-255: the faint fills under a bar's buttons (hover, pressed, checked: 15 to 56 in every theme, and one over
# another) stay under this; a child shown in the other ink leaves them out away from its glyphs, as they are
# painted once already.
INK_FILL_MOST = 80
STATS_STEP = 18  # logical px per sample when judging how strong the tint must be
SHADOW_BLUR = 14  # logical px the shadow fades over, all round the bar
SHADOW_OFFSET = 6  # the shadow falls this far below the bar
BAR_BUTTON = 36  # icon buttons on the glass bars are squares this big
TEXT_CONTRAST = 7.0  # text and icons on glass, over plain parts of what shows through: a page, the canvas
BUSY_CONTRAST = 4.5  # and at least this over the worst spot of a busy backdrop, such as a bright patch of a photo
# The Clear glass lets the page show, as Apple's Liquid Glass does: the tint rises only as far as WCAG AA asks,
# 4.5:1 for text over plain parts and 3:1, as for icons, over a busy spot. Balanced keeps 7:1 and 4.5:1.
CLEAR_TEXT_CONTRAST = 4.5
CLEAR_BUSY_CONTRAST = 3.0
PLAIN_SPREAD = 1.25  # samples this close to their neighbours (a contrast ratio) are a plain backdrop
BUSY_SPREAD = 2.0  # and this far from them, a busy one; in between the contrast needed slides from 7 to 4.5
CONTRAST_MARGIN = 0.15  # aimed above both, for the rounding of 8-bit colours and of the tint's opacity
MOST_ALPHA = 0.92  # the glass never covers what is behind it completely
ALPHA_STEP = 0.02
GLASS_STYLES = ("liquid", "clear", "balanced", "solid")
# Colours behind the glass, lifted so the tint never turns them grey.
SATURATION = {"liquid": 1.3, "clear": 1.3, "balanced": 1.2}
NOISE_ALPHA = 4  # 0-255: the faint grain on the glass, which keeps its smooth gradients from banding
NOISE_SIZE = 64  # device px of the grain's tile
FADE_MS = motion.NORMAL  # floating bars and chips take this long to come, and less to go
# A bar in the dark theme turns light where what is behind it is brighter than this (relative luminance), and
# dark again below the second; in the light theme it turns dark under the third and light above the fourth. The
# gap between each pair keeps a bar from flickering at the edge of a page.
TONE_LIGHT_OVER, TONE_DARK_UNDER = 0.45, 0.22
TONE_DARK_IN_LIGHT, TONE_LIGHT_IN_LIGHT = 0.06, 0.16
# A popup draws its own rounded shape: Windows leaves the corners clear only for a frameless window, and its
# drop shadow would be a square round them.
POPUP_FLAGS = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint


class _Glass:
    """How the floating bars show what is behind them: the "Glass" setting.

    "clear" blurs the pages behind with a light tint, "balanced" with a stronger one, "solid"
    paints the bars opaque. "liquid" makes each bar one piece of clear glass that bends the pages
    behind it as a lens does, its content on a halo of its own, where the lens can run (lens.py);
    where it cannot, it is the clear glass itself.
    """

    style = "clear"
    adaptive = True  # the bars take the light or the dark theme's colours, whichever suits what is behind them

    @property
    def enabled(self) -> bool:
        return self.style != "solid" and themes.current.look.glass  # some styles draw every bar solid

    @property
    def clear(self) -> bool:
        """Whether the glass reads as the Clear one. The Liquid glass does: the two share the contrast text
        needs, the tint's least opacity, the lift of the colours and the light along the bottom."""
        return self.style in ("liquid", "clear")

    @property
    def liquid(self) -> bool:
        """Whether the bars are drawn through the lens now."""
        return self.style == "liquid" and self.enabled and lens.available()


glass = _Glass()


def glass_style(value) -> str:
    """A "glass" setting as one of GLASS_STYLES. Older versions stored on (True) or off (False)."""
    if value is True or value == "true":
        return "clear"
    if value is False or value == "false":
        return "solid"
    if value == "liquid" and not lens.supported():
        return "clear"  # what a Liquid bar is drawn as where the lens cannot run at all
    return value if value in GLASS_STYLES else "clear"


def set_glass_adaptive(on: bool) -> None:
    """Let the bars change tone with what is behind them, or keep the theme's: at once, on every bar."""
    glass.adaptive = bool(on)
    for widget in QApplication.allWidgets():
        if isinstance(widget, FloatingBar):
            widget.refresh_backdrop()
            widget.update()


def set_glass(value) -> None:
    """Show every floating bar in another glass style (or the old on and off), at once."""
    global _lens_asked
    glass.style = glass_style(value)
    if glass.style == "liquid":
        # Asked for now, not at a bar's first sample: at startup this runs before the window shows, so the
        # device is made behind the splash, also when the app opens at Home.
        _ask_for_lens()
    else:
        lens.shutdown()  # the device and its textures hold some 60 MB that no other style needs
        _lens_asked = False  # so that choosing Liquid again asks again
    for widget in QApplication.allWidgets():
        if isinstance(widget, FloatingBar):
            widget.refresh_backdrop()
            widget.update()


_lens_asked = False  # the lens's device was asked for: once, or every sample would queue another try


def _ask_for_lens() -> None:
    """Queue the making of the lens's device, once, when the bars are Liquid glass on a look with glass. Until
    it is up a Liquid bar is drawn as the Clear one."""
    global _lens_asked
    if glass.style == "liquid" and glass.enabled and not _lens_asked and not lens.available():
        _lens_asked = True
        QTimer.singleShot(0, _start_lens)


def _start_lens() -> None:
    """Make the lens's device, from the event loop and never in a paint (it takes 0.1 to 0.2 s). Once it is up
    every bar samples again and is drawn through it."""
    # Another style chosen in the meantime has no use for the device.
    if glass.style == "liquid" and lens.start():
        set_glass(glass.style)


# Contrast ---------------------------------------------------------------------------


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(color: QColor) -> float:
    """WCAG relative luminance, 0 for black to 1 for white."""
    return 0.2126 * _linear(color.redF()) + 0.7152 * _linear(color.greenF()) + 0.0722 * _linear(color.blueF())


def contrast(first: float, second: float) -> float:
    """WCAG contrast ratio of two luminances, from 1 to 21."""
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _over(top: QColor, alpha: float, bottom: QColor) -> QColor:
    """`top` at `alpha` laid over `bottom`, as the painter composes them."""
    return QColor.fromRgbF(*(alpha * a + (1 - alpha) * b for a, b in (
        (top.redF(), bottom.redF()), (top.greenF(), bottom.greenF()), (top.blueF(), bottom.blueF()))))


def contrast_targets() -> tuple[float, float]:
    """The contrast text and icons on the glass need, over plain parts and over a busy spot, in the style in use."""
    if glass.clear:
        return CLEAR_TEXT_CONTRAST, CLEAR_BUSY_CONTRAST
    return TEXT_CONTRAST, BUSY_CONTRAST


def least_alpha(theme: Theme) -> float:
    """The tint's least opacity in the glass style in use: the clear glass shows more of what is behind it."""
    return theme.glass_clear_alpha if glass.clear else theme.glass_alpha


def _grained(color: QColor, light_text: bool) -> QColor:
    """`color` under the strongest speck of the glass's grain that works against the text: a white one under
    light text, a black one under dark text."""
    share = NOISE_ALPHA / 255
    channels = (color.redF(), color.greenF(), color.blueF())
    if light_text:
        return QColor.fromRgbF(*(c + (1 - c) * share for c in channels))
    return QColor.fromRgbF(*(c * (1 - share) for c in channels))


def glass_alpha(behind: QColor, theme: Theme, need: float | None = None, least: float | None = None) -> float:
    """The least opacity of the glass over `behind` that gives text and icons `need`:1 on every pixel, the grain's
    included (the style's contrast over plain parts by default), from `least` up (the style's own least by
    default) and never above MOST_ALPHA."""
    need = contrast_targets()[0] if need is None else need
    text = luminance(QColor(theme.text))
    light_text = text > 0.5
    alpha = least_alpha(theme) if least is None else least
    while alpha < MOST_ALPHA:
        mixed = _grained(_over(theme.glass_base, alpha, behind), light_text)
        if contrast(luminance(mixed), text) >= need + CONTRAST_MARGIN:
            break
        alpha = min(MOST_ALPHA, alpha + ALPHA_STEP)
    return alpha


def column_needs(luminances: list[float]) -> list[float]:
    """The contrast text needs over each column of what is behind the glass, left to right.

    The style's plain contrast where the backdrop is plain on at least one side of the column, over its two
    neighbours there: a page's margin, the canvas, a large patch of one colour, also right up to its edge.
    Sliding to its busy contrast where the column stands out from both sides, as a bright spot of a photo does.
    """
    plain_need, busy_need = contrast_targets()
    shifted = [value + 0.05 for value in luminances]
    needs = []
    for index, value in enumerate(shifted):
        sides = [side for side in (shifted[max(0, index - 2):index], shifted[index + 1:index + 3]) if side]
        # The largest contrast with a neighbour, on the side where the backdrop is evener.
        spread = min((max(max(side) / value, value / min(side)) for side in sides), default=1.0)
        plain = min(max((BUSY_SPREAD - spread) / (BUSY_SPREAD - PLAIN_SPREAD), 0.0), 1.0)
        needs.append(busy_need + (plain_need - busy_need) * plain)
    return needs


_LINEAR8 = [_linear(value / 255) for value in range(256)]
_ALPHAS: dict[tuple, float] = {}  # glass_alpha on a coarse grid of colours, by theme, need and least opacity
GRID = 17  # the grid's step, in 8-bit channel values: 16 levels, 0 to 255
NEED_LEVELS = 4  # the needs between the busy and the plain contrast are rounded up to one of these steps


def alpha_profile(columns: list[tuple[int, int, int]], theme: Theme, least: float | None = None) -> list[float]:
    """The tint's opacity column by column, left to right: each as strong as reading over that column needs
    (see column_needs), never less than `least`. `columns` holds the colour hardest to read on in each column,
    in 8-bit channels.

    The opacity comes from glass_alpha on a coarse grid of colours, kept once worked out. A column's colour
    goes to the grid point that is harder to read on (brighter under light text, darker under dark text) and
    its need is rounded up, so the grid never asks for less than the exact answer. Each column's opacity
    then also covers the two columns either side of it, and the profile is smoothed over three: the tint
    changes gently along the bar, and every point between two columns keeps what either of them needs.
    """
    least = least_alpha(theme) if least is None else least
    if not columns:
        return []
    linear = _LINEAR8
    needs = column_needs([0.2126 * linear[r] + 0.7152 * linear[g] + 0.0722 * linear[b] for r, g, b in columns])
    up = luminance(QColor(theme.text)) > 0.5  # light text: brighter is harder to read on
    plain_need, busy_need = contrast_targets()
    step = (plain_need - busy_need) / NEED_LEVELS
    known = _ALPHAS
    if len(known) > 50_000:
        known.clear()
    theme_key = (theme.text, theme.glass_base.rgba(), round(least, 4), plain_need, busy_need)
    alphas = []
    for (r, g, b), need in zip(columns, needs):
        if up:
            point = (min(255, -(-r // GRID) * GRID), min(255, -(-g // GRID) * GRID), min(255, -(-b // GRID) * GRID))
        else:
            point = (r // GRID * GRID, g // GRID * GRID, b // GRID * GRID)
        level = min(NEED_LEVELS, max(0, math.ceil((need - busy_need) / step - 1e-9)))
        key = (theme_key, point, level)
        alpha = known.get(key)
        if alpha is None:
            alpha = known[key] = glass_alpha(QColor(*point), theme, busy_need + level * step, least)
        alphas.append(alpha)
    count = len(alphas)
    grown = [max(alphas[max(0, i - 2):i + 3]) for i in range(count)]
    return [sum(grown[max(0, i - 1):i + 2]) / len(grown[max(0, i - 1):i + 2]) for i in range(count)]


def needed_alpha(columns: list[QColor], theme: Theme, least: float | None = None) -> float:
    """The opacity that keeps text readable over every one of `columns` (the colour hardest to read on in
    each column, left to right): the most any column of alpha_profile needs."""
    profile = alpha_profile([(c.red(), c.green(), c.blue()) for c in columns], theme, least)
    return max(profile) if profile else (least_alpha(theme) if least is None else least)


# Blur -------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _saturation_matrix(amount: float) -> tuple:
    """Lifts the saturation of RGB colours by `amount` (1 keeps them), keeping their luma."""
    weights = (0.2126, 0.7152, 0.0722)
    keep = 1 - amount
    rows = []
    for channel in range(3):
        rows += [weights[i] * keep + (amount if i == channel else 0.0) for i in range(3)] + [0.0]
    return tuple(rows)


_GAUSSIAN = ImageFilter.GaussianBlur(BLUR_SIGMA / BLUR_STEP)
_BGRX_FORMATS = (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied)


def _softened(image: QImage, size: QSize) -> Image.Image:
    """`image`, a grab of `size` logical px, frosted: one texel every BLUR_STEP logical px, blurred with a
    gaussian BLUR_SIGMA px wide (Pillow runs it as three box passes), its colours lifted for the glass style.

    The gaussian leaves no blocks, and the picture moves smoothly when what is behind it scrolls by a pixel.
    Beyond its edges the blur repeats the edge, so nothing dark creeps in at the borders.
    """
    width, height = max(1, round(size.width() / BLUR_STEP)), max(1, round(size.height() / BLUR_STEP))
    small = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)  # each texel the mean of its pixels
    if small.format() not in _BGRX_FORMATS:
        small = small.convertToFormat(QImage.Format.Format_RGB32)
    # Read as blue, green, red and a byte left out, the order of these formats in memory: no conversion.
    picture = Image.frombuffer("RGB", (width, height), bytes(small.constBits()), "raw", "BGRX", small.bytesPerLine(), 1)
    picture = picture.filter(_GAUSSIAN)
    # Through the lens the page shows as it is: its colours, lifted, would mislead the tone and the surface.
    lift = 1.0 if glass.liquid else SATURATION.get(glass.style, 1.0)
    return picture.convert("RGB", _saturation_matrix(lift)) if lift != 1.0 else picture


def _qimage(picture: Image.Image) -> QImage:
    """An RGB picture of Pillow's as a QImage of its own."""
    data = picture.tobytes("raw", "RGBX")
    return QImage(data, picture.width, picture.height, 4 * picture.width, QImage.Format.Format_RGBX8888).copy()


def frosted(image: QImage, ratio: float | None = None) -> QImage:
    """`image` as the glass shows it, blurred and lifted: one texel every BLUR_STEP logical px.

    The result is smaller than `image`; draw it stretched over the same rectangle.
    """
    ratio = image.devicePixelRatio() if ratio is None else ratio
    return _qimage(_softened(image, QSize(round(image.width() / ratio), round(image.height() / ratio))))


def _behind(picture: Image.Image, inner: QRectF, light_text: bool):
    """What the glass covers in `picture` (texels; `inner` is the bar, in its coordinates).

    Returns, for every column STATS_STEP px wide across the whole picture, the colour hardest to read on
    in the rows of the bar and one sample more above and below it (what a scroll brings under the bar
    before the next sample): the brightest under light text, the darkest under dark text, channel by
    channel, so never kinder than the truth. Then which of those columns lie under the bar, and the
    mean colour there.
    """
    step = max(1, STATS_STEP // BLUR_STEP)
    stats = picture.reduce(step) if step > 1 and min(picture.size) >= step else picture
    scale_x, scale_y = stats.width / picture.width, stats.height / picture.height
    top = min(max(0, math.floor(inner.top() * scale_y) - 1), stats.height - 1)
    bottom = max(top + 1, min(stats.height, math.ceil(inner.bottom() * scale_y) + 1))
    band = stats.crop((0, top, stats.width, bottom))
    data, stride = band.tobytes(), band.width * 3
    rows = [data[row * stride:(row + 1) * stride] for row in range(band.height)]
    worst = bytes(map(max if light_text else min, zip(*rows)))  # channel by channel, down each column
    columns = list(zip(worst[0::3], worst[1::3], worst[2::3]))
    first = min(max(0, math.floor(inner.left() * scale_x)), stats.width - 1)
    last = max(first + 1, min(stats.width, math.ceil(inner.right() * scale_x)))
    mean = band.crop((first, 0, last, band.height)).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    return columns, (first, last), QColor(*mean[:3])


@lru_cache(maxsize=1)
def _noise_tile() -> QPixmap:
    """A tile of faint grain, white and black specks a few percent strong, the same on every run."""
    rng = random.Random(7)
    image = QImage(NOISE_SIZE, NOISE_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(NOISE_SIZE):
        for x in range(NOISE_SIZE):
            alpha = rng.randrange(NOISE_ALPHA + 1)
            value = alpha if rng.random() < 0.5 else 0  # premultiplied white, or black
            image.setPixel(x, y, (alpha << 24) | (value << 16) | (value << 8) | value)
    return QPixmap.fromImage(image)


def _snapped(value: float) -> int:
    """The device pixel edge Qt fills a widget's rect to: half a pixel rounds up."""
    return math.floor(value + 0.5)


# Icons ------------------------------------------------------------------------------


class _CachedIcon(QIconEngine):
    """Keeps every picture of an icon it has drawn, by size, mode, state, screen scale and theme.

    The bars repaint whenever the pages scroll under them; drawing the vector glyphs
    again each time cost more than the scrolling itself.
    """

    def __init__(self, source: QIcon):
        super().__init__()
        self._source = source
        self._pixmaps: dict[tuple, QPixmap] = {}

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        key = (size.width(), size.height(), mode, state, round(scale * 100), themes.current.name)
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = self._pixmaps[key] = self._source.pixmap(size, scale, mode, state)
        return pixmap

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        device = painter.device()
        scale = device.devicePixelRatioF() if device is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))

    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        return size

    def clone(self) -> QIconEngine:
        return _CachedIcon(self._source)


def cached_icon(name: str, color: str | None = None) -> QIcon:
    """icon(), drawn once per size and screen instead of on every paint."""
    return QIcon(_CachedIcon(icon(name, color)))


def icon_pixmap(name: str, size: int, color: str | None = None) -> QPixmap:
    """An icon as a picture for a QLabel, drawn for the sharpest screen so it never looks soft."""
    app = QApplication.instance()
    return icon(name, color).pixmap(QSize(size, size), app.devicePixelRatio() if app is not None else 1.0)


_CLEAR = QColor(0, 0, 0, 0)


class IconButton(QToolButton):
    """A flat tool button whose icon follows the active theme.

    On a glass bar an icon-only button is a BAR_BUTTON square. A plain one there (no name of its own) paints its
    hover and pressed fills itself, so they fade in and out where the style sheet would snap them.
    """

    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        checkable: bool = False,
        icon_size: int = 20,
        parent: QWidget | None = None,
        text: str | None = None,
        color: str | None = None,
        help: str | None = None,
    ):
        super().__init__(parent)
        self._icon_name = icon_name
        self._color = color
        self._fill = motion.Fill(self)
        self._fill.changed.connect(self.update)
        self.setProperty("fade", True)  # the sheet (theme.py) leaves its hover and pressed fills to paintEvent
        self.setToolTip(tr(tooltip))
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if text:
            self.setText(tr(text))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._refresh()
        themes.changed.connect(self._refresh)
        if help:
            from .help import attach_help  # help builds on this module

            attach_help(self, help)

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._refresh()

    def _refresh(self, *_):
        filled = self.objectName() == "RoundAccent" and themes.current.look.sheet is None  # on the accent fill
        on_accent = themes.current.accent_text if filled else None
        self.setIcon(cached_icon(self._icon_name, self._color or on_accent or self._tone_text()))

    def _tone_text(self) -> str | None:
        """The icon's colour on the glass bar the button is on, when that bar changed tone (its hue, or the text
        colour, in the bar's theme); else None (the app theme's)."""
        widget = self.parentWidget()
        while widget is not None and not widget.isWindow():
            if isinstance(widget, FloatingBar):
                if not widget.tone:
                    return None
                theme = widget.theme()
                return hue_colour(self._icon_name, theme.name) or theme.text
            widget = widget.parentWidget()
        return None

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and self._on_glass():
            return hint.expandedTo(QSize(BAR_BUTTON, BAR_BUTTON))
        return hint

    def _on_glass(self) -> bool:
        bar = self._bar()
        return bar is not None and not bar.solid

    def _bar(self, through_windows: bool = False) -> FloatingBar | None:
        """The floating bar the button is on, if any. With `through_windows` also one that owns the window the
        button is in (a popup it opened), which is as far as the style sheet's rules for a bar's buttons reach."""
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, FloatingBar):
                return widget
            if widget.isWindow() and not through_windows:
                return None
            widget = widget.parentWidget()
        return None

    def setObjectName(self, name: str) -> None:
        super().setObjectName(name)
        # A named button has fills of its own in the sheet (a danger button's red, a chip's); it keeps them.
        fades = not name
        if bool(self.property("fade")) != fades:
            self.setProperty("fade", fades)
            if self.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):  # the sheet read it already
                self.style().unpolish(self)
                self.style().polish(self)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        fill = self._fill
        # Idle, which is almost always (a scroll repaints every bar's buttons): nothing to look up or paint.
        if self.property("fade") and (self.underMouse() or self.isDown() or fill.running or fill.target.alpha()):
            bar = self._bar(through_windows=True)
            if bar is not None:
                self._paint_fill(bar)
        super().paintEvent(event)

    def _paint_fill(self, bar: FloatingBar) -> None:
        """The fill under the pointer or the press, under the icon: in the bar's own colours (light over a bright
        page) and the shape of its buttons (cut at two corners where the style chamfers). A checked button keeps
        the sheet's fill."""
        t = bar.theme()
        pressed = self.isDown()
        hot = self.isEnabled() and not self.isChecked() and (pressed or self.underMouse())
        fill = qcolor(t.glass_pressed if pressed else t.glass_hover) if hot else _CLEAR
        self._fill.go(fill, motion.FAST if hot else motion.NORMAL)  # it lingers a little as the pointer leaves
        colour = self._fill.colour
        if colour.alpha() == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        radius = min(bar_radius(t.look), self.width() / 2, self.height() / 2)
        painter.drawPath(outline(QRectF(self.rect()), radius, t.look))
        painter.end()


# Shadows ----------------------------------------------------------------------------


def _blurred(image: QImage, radius: float) -> QImage:
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    scene.render(painter, QRectF(out.rect()), QRectF(0, 0, image.width(), image.height()))
    painter.end()
    return out


@lru_cache(maxsize=32)
def _shadow_picture(radius: int, scale: int, rgba: int, look: Look) -> QPixmap:
    """A blurred square in the shape of `look`'s surfaces: the nine slices of every shadow with this corner radius.

    `scale` is the screen ratio in hundredths, so the cache keeps one picture per screen.
    """
    ratio = scale / 100
    side = 2 * (SHADOW_BLUR + radius) + 2
    image = QImage(round(side * ratio), round(side * ratio), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor.fromRgba(rgba))
    painter.drawPath(outline(QRectF(SHADOW_BLUR, SHADOW_BLUR, side - 2 * SHADOW_BLUR, side - 2 * SHADOW_BLUR),
                             radius, look))
    painter.end()
    pixmap = QPixmap.fromImage(_blurred(image, SHADOW_BLUR * ratio))
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _nine_slice(painter: QPainter, target: QRect, pixmap: QPixmap, edge: int) -> None:
    """Draw `pixmap` stretched over `target`, keeping its corners `edge` logical px square."""
    ratio = pixmap.devicePixelRatio()
    width, height = pixmap.width() / ratio, pixmap.height() / ratio
    xs = (0.0, edge, width - edge, width)
    ys = (0.0, edge, height - edge, height)
    tx = (target.left(), target.left() + edge, target.right() + 1 - edge, target.right() + 1)
    ty = (target.top(), target.top() + edge, target.bottom() + 1 - edge, target.bottom() + 1)
    for row in range(3):
        for column in range(3):
            into = QRectF(tx[column], ty[row], tx[column + 1] - tx[column], ty[row + 1] - ty[row])
            if into.width() <= 0 or into.height() <= 0:
                continue
            source = QRectF(xs[column] * ratio, ys[row] * ratio, (xs[column + 1] - xs[column]) * ratio,
                            (ys[row + 1] - ys[row]) * ratio)
            painter.drawPixmap(into, pixmap, source)


def shadow_colour(theme: Theme) -> QColor:
    """What floating bars cast: the theme's shadow, or in a style that glows, the accent."""
    if theme.look.shadow != "glow":
        return theme.shadow
    glow = QColor(theme.accent)
    glow.setAlpha(theme.shadow.alpha())  # the palette's shadow says how strong the glow is
    return glow


class _BarShadow(QWidget):
    """The soft shadow of a floating bar: a sibling stacked just under it.

    A cached picture costs nothing to draw, where a QGraphicsDropShadowEffect blurred
    the bar again on every frame of scrolling. The nine slices are put together once
    per size, so a frame copies one picture.
    """

    def __init__(self, bar: FloatingBar):
        super().__init__(bar.parentWidget())
        self._bar = bar
        self._key: tuple | None = None
        self._picture: QPixmap | None = None
        self.strength = 1.0  # less while its bar fades in or out
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        themes.changed.connect(self._restyle)

    def _restyle(self, *_) -> None:
        if self._bar._cast_shadow() is self:
            self.follow()  # a style may drop the shadow, or make it a glow
        self.update()

    def follow(self) -> None:
        bar = self._bar
        drop = 0 if themes.current.look.shadow == "glow" else SHADOW_OFFSET  # a glow spreads evenly all round
        self.setGeometry(bar.geometry().adjusted(-SHADOW_BLUR, drop - SHADOW_BLUR, SHADOW_BLUR, drop + SHADOW_BLUR))
        self.setVisible(bar.isVisibleTo(bar.parentWidget()) and themes.current.look.shadow != "none")

    def _composed(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        radius = round(self._bar.radius())
        look = themes.current.look
        key = (self.width(), self.height(), round(ratio * 100), radius, shadow_colour(themes.current).rgba(),
               look.chamfer)
        if key != self._key or self._picture is None:
            picture = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
            picture.setDevicePixelRatio(ratio)
            picture.fill(Qt.GlobalColor.transparent)
            painter = QPainter(picture)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            _nine_slice(painter, self.rect(), _shadow_picture(radius, key[2], key[4], look), SHADOW_BLUR + radius)
            painter.end()
            self._key, self._picture = key, picture
        return self._picture

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setOpacity(self.strength)
        painter.drawPixmap(0, 0, self._composed())


class Fade(QObject):
    """Shows or hides a widget with a short change of opacity; a floating bar's shadow fades with it.

    Only while it runs does the widget paint through a QGraphicsOpacityEffect: once over, the effect
    goes, so a bar that stays costs nothing more on every frame of a scroll. It comes in over `ms`
    (motion.NORMAL) and goes in less (motion.FAST), easing out and in; a bar fading out stays shown until it has
    gone. With motion off it shows and hides at once.
    """

    def __init__(self, widget: QWidget, ms: int | None = None):
        super().__init__(widget)
        self._widget = widget
        self._ms = ms
        self._showing = True
        self._animation = QVariantAnimation(self)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._finish)

    @property
    def showing(self) -> bool:
        """Whether the widget is shown or on its way in."""
        return self._showing and self._widget.isVisible()

    def fade_in(self) -> None:
        widget = self._widget
        running = self._animation.state() == QVariantAnimation.State.Running
        if widget.isVisible() and self._showing and not running:
            return  # shown already
        start = self._level() if running and widget.isVisible() else 0.0
        self._showing = True
        self._run(start, 1.0)
        widget.show()

    def fade_out(self) -> None:
        if not self._widget.isVisible():
            return
        running = self._animation.state() == QVariantAnimation.State.Running
        if not running or self._showing:
            self._showing = False
            self._run(self._level() if running else 1.0, 0.0)

    def stop(self) -> None:
        """At once: the widget as it should end up, with no effect left."""
        self._animation.stop()
        self._finish()

    def _level(self) -> float:
        effect = self._widget.graphicsEffect()
        return effect.opacity() if isinstance(effect, QGraphicsOpacityEffect) else 1.0

    def _run(self, start: float, end: float) -> None:
        self._animation.stop()
        if not motion.enabled():
            self._finish()
            return
        effect = self._widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._widget)
            self._widget.setGraphicsEffect(effect)
        entering = end > start
        self._animation.blockSignals(True)  # a stopped animation reports the values of its old time as its ends change
        self._animation.setDuration(self._ms or (motion.NORMAL if entering else motion.FAST))
        self._animation.setEasingCurve(motion.EASE_OUT if entering else motion.EASE_IN)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.blockSignals(False)
        self._step(start)
        self._animation.start()

    def _step(self, value) -> None:
        effect = self._widget.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(float(value))
        self._shade(float(value))

    def _shade(self, value: float) -> None:
        shadow = self._widget._cast_shadow() if isinstance(self._widget, FloatingBar) else None
        if shadow is not None:
            shadow.strength = value
            shadow.update()

    def _finish(self) -> None:
        if not shiboken6.isValid(self._widget):
            return
        if not self._showing:
            self._widget.hide()
        self._widget.setGraphicsEffect(None)
        self._shade(1.0)


# Glass ------------------------------------------------------------------------------


def _tone_pair() -> tuple[float, float]:
    """The luminances a bar in the app's theme turns dark under and light over (see TONE_LIGHT_OVER)."""
    if themes.current.name == "dark":
        return TONE_DARK_UNDER, TONE_LIGHT_OVER
    return TONE_DARK_IN_LIGHT, TONE_LIGHT_IN_LIGHT


def _tone_for(level: float, tone: str) -> str:
    """The tone for a backdrop of luminance `level`, `tone` being the one taken so far: it changes only past the
    far threshold of the pair, so the edge of a page doesn't flicker."""
    dark_under, light_over = _tone_pair()
    if themes.current.name == "dark":
        if tone != "light" and level > light_over:
            return "light"
        if tone == "light" and level < dark_under:
            return ""
    else:
        if tone != "dark" and level < dark_under:
            return "dark"
        if tone == "dark" and level > light_over:
            return ""
    return tone


def _larger_part(picture: Image.Image, box: tuple[int, int, int, int]) -> float:
    """The mean luminance of the larger part of `box` in `picture`, its light texels or its dark ones (split
    halfway between the tone's two thresholds): a child across the edge of a page takes the side most of it is on."""
    counts = picture.crop(box).convert("L").histogram()
    middle = sum(_tone_pair()) / 2
    split = next(level for level, linear in enumerate(_LINEAR8) if linear > middle)
    dark, light = sum(counts[:split]), sum(counts[split:])
    part = slice(split, 256) if light > dark else slice(0, split)
    return sum(count * linear for count, linear in zip(counts[part], _LINEAR8[part])) / max(light, dark)


def _turned(image: QImage) -> QImage:
    """`image` with its lightness turned over and its hue and saturation kept: in HSL that moves every channel by
    255 minus the largest and the smallest, so dark ink turns light and a coloured icon keeps its colour, lighter
    or darker. The faint fills are left out (INK_FILL_MOST), all but the pixel round each glyph: there the soft
    edge of the glyph lies, and without it the edge in the bar's own ink would ring the glyph."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    picture = Image.frombuffer("RGBA", (width, height), bytes(rgba.constBits()), "raw", "RGBA", rgba.bytesPerLine(), 1)
    red, green, blue, alpha = picture.split()
    most = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    room = ImageChops.invert(ImageChops.darker(ImageChops.darker(red, green), blue))  # 255 minus the smallest
    turned = [ImageChops.subtract(room, ImageChops.subtract(most, channel)) for channel in (red, green, blue)]
    glyphs = alpha.point([255 if level > INK_FILL_MOST else 0 for level in range(256)]).filter(ImageFilter.MaxFilter(3))
    alpha = ImageChops.darker(alpha, glyphs)
    out = Image.merge("RGBA", (*turned, alpha))
    return QImage(out.tobytes(), width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()


class _Inks(QWidget):
    """Over a Liquid bar, the children whose backdrop wants the other ink, drawn again in it: each one's own
    picture with its lightness turned over. It lies above every other child and lets the mouse through, so a
    click or a hover reaches the child under it."""

    def __init__(self, bar: FloatingBar):
        super().__init__(bar)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(bar.rect())

    def paintEvent(self, event) -> None:
        halo = self.parentWidget()._halo  # the bar paints first, in the same pass: its pictures are current
        if halo is None or not halo[5]:
            return
        painter = QPainter(self)
        device = painter.deviceTransform()  # in device pixels from the bar's (0, 0), as the bar lays its halo
        painter.setWorldTransform(QTransform.fromTranslate(device.dx(), device.dy()) * device.inverted()[0]
                                  * painter.worldTransform())
        for place, picture in halo[5]:
            painter.drawImage(place, picture)
        painter.end()


class FloatingBar(QFrame):
    """A floating bar of frosted glass, like Windows' acrylic.

    Qt widgets can't blur what lies behind them, so the bar samples its backdrop
    itself: set_backdrop(widget) names what it floats over, refresh_backdrop()
    grabs that area and the bleed round it, blurs it with a gaussian and lifts
    its colours, and the bar paints it under a tint just opaque enough for its
    text and icons to stay readable over what is there (see needed_alpha). Between
    samples the owner moves the picture with the content (shift_backdrop), so it
    never lags a scroll; covers() says when it has moved too far and needs a new
    sample at once. The edge, the line of light inside the top and the faint grain
    are painted in device pixels, so they stay sharp at any screen scale. With no
    backdrop the bar paints a solid version. Bars inside a window cast a soft
    shadow; popups are solid and cast none.
    """

    _inks: _Inks | None = None  # set in __init__, after the events of the children made before it

    def __init__(self, parent: QWidget | None = None, popup: bool = False, solid: bool | None = None):
        super().__init__(parent, POPUP_FLAGS if popup else Qt.WindowType.Widget)
        self.setObjectName("FloatingBar")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._popup = popup
        # Menus and panels that pop up are solid: only the bars over the pages are glass.
        self._solid = popup if solid is None else solid
        self.setProperty("glass", not self._solid)  # the style sheet gives muted text the full colour on glass
        self.min_tint_alpha = 0  # 0-255: panels full of text raise it, so busy pages behind don't hurt reading
        self._source: QWidget | None = None
        self._frosted: QImage | None = None  # the backdrop blurred and lifted, before the tint
        self._backdrop: QPixmap | None = None  # the same under the tint: what shows
        # In the Liquid glass, the grab as it is, which the lens bends; then the lens's picture of the bar with what
        # it was made from, so a repaint for a hover renders nothing.
        self._sharp: QImage | None = None
        self._lensed: tuple[tuple, QImage] | None = None
        # The content of a Liquid bar and its halo: (what it was drawn for, the content's alpha, each child drawn
        # alone, the side each was on, the halo, the children in the other ink). The children are drawn again only
        # when they can have changed, never because the pages moved; a child changing side recolours the rest.
        self._halo: tuple | None = None
        self._sides: dict[QWidget, str] = {}  # the tone each child's own backdrop asks for, at the last sample
        self._haloing = False  # the content is being drawn alone, for the halo: the bar's own paint draws nothing
        self._halo_check = QTimer(self)
        self._halo_check.setSingleShot(True)
        self._halo_check.setInterval(HALO_RECHECK_MS)
        self._halo_check.timeout.connect(self._check_halo)
        self._backdrop_area = QRect()  # where the backdrop was sampled, in the source's coordinates
        self._shift = QPoint()  # how far the content has moved since
        self._columns: list[tuple[int, int, int]] = []  # the colour hardest to read on, column by column (_behind)
        self._under = (0, 0)  # which of those columns lie under the bar
        self._tint_key: tuple | None = None  # (what it was made from, the most opaque under the bar, the gradient)
        self._mean = QColor(themes.current.glass_solid)
        self._flat = QColor(themes.current.glass_solid)  # the mean under the tint, where the picture runs out
        self._alpha = least_alpha(themes.current)
        self._overlay: tuple[tuple, QPixmap] | None = None  # the grain, the light and the edge, for one size
        self._shadow: _BarShadow | None = None
        self.tone = ""  # "light" or "dark" when the bar took that theme's colours; "" for the theme's own
        self._inks = None if self._solid else _Inks(self)
        self._attach_shadow()
        themes.changed.connect(self._on_theme)

    @property
    def solid(self) -> bool:
        return self._solid

    def theme(self) -> Theme:
        """The theme the bar is painted in: the app's, or the other one's when the bar changed tone."""
        return themes.tone(self.tone) if self.tone else themes.current

    def _chosen_tone(self, behind: QColor) -> str:
        """The tone for what is behind the bar, with some give either way so the edge of a page doesn't flicker."""
        if self._solid or not glass.adaptive or not glass.enabled:
            return ""
        return _tone_for(luminance(behind), self.tone)

    def set_tone(self, tone: str) -> None:
        """Paint the bar, its text and its icons in the light or the dark theme's colours ("" for the app's)."""
        if tone == self.tone:
            return
        self.tone = tone
        self.setProperty("tone", tone)
        self.setStyleSheet(tone_sheet(self.theme()) if tone else "")
        for button in self.findChildren(IconButton):
            button._refresh()
        self._overlay = None
        self.tone_changed()
        self.update()

    def tone_changed(self) -> None:
        """For bars with more to recolour when the tone changes (the toolbar's tools)."""
        for child in self.findChildren(QWidget):
            child.update()

    @property
    def tint_alpha(self) -> float:
        """The opacity of the tint over the backdrop, from 0 to 1."""
        return max(self._alpha, self.min_tint_alpha / 255)

    def veil(self) -> float:
        """How much of the tint lies over the page through the lens: LIQUID_VEIL under a dark or a light bar, more
        in a look of smoked glass, and more where the bar asks for it (min_tint_alpha)."""
        t = self.theme()
        light_bar = luminance(QColor(t.text)) < 0.5
        # Smoked glass keeps half its smoke: clear, its light icons would stand on black blots over a white page.
        smoke = t.glass_clear_alpha / 2 if t.glass_clear_alpha > SMOKED else 0.0
        return max(LIQUID_VEIL[light_bar], smoke, self.min_tint_alpha / 255)

    def radius(self) -> float:
        look = themes.current.look
        return (self.height() - 1) / 2 if look.capsules and self.height() <= CAPSULE_MAX else look.panel

    def surface(self) -> QColor:
        """The colour coloured icons stand on: the glass over the part of what is behind it hardest to read on
        (the brightest under light text, the darkest under dark text), or the bar itself when it is solid. Through
        the lens: the halo next to the icons, over the faint veil."""
        t = self.theme()
        if self._solid:
            return QColor(t.raised)
        first, last = self._under
        columns = self._columns[first:last]
        if self._backdrop is None or not columns:
            return QColor(t.glass_solid)
        light_text = luminance(QColor(t.text)) > 0.5
        weight = (lambda c: 0.2126 * _LINEAR8[c[0]] + 0.7152 * _LINEAR8[c[1]] + 0.0722 * _LINEAR8[c[2]])
        worst = (max if light_text else min)(columns, key=weight)
        if glass.liquid:  # icons stand on their halo, at least HALO_NEAR dense next to them, over the faint veil
            return _over(t.glass_base, HALO_NEAR, _over(t.glass_base, self.veil(), QColor(*worst)))
        return _over(t.glass_base, self.tint_alpha, QColor(*worst))

    def set_backdrop(self, source: QWidget | None) -> None:
        self._source = source
        self.refresh_backdrop()

    def backdrop_area(self) -> QRect:
        """What the glass shows, in the source's coordinates: the bar and the bleed round it."""
        if self._source is None:
            return QRect()
        origin = self._source.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))
        return QRect(origin, self.size()).adjusted(-GLASS_BLEED, -GLASS_BLEED, GLASS_BLEED, GLASS_BLEED)

    def refresh_backdrop(self) -> None:
        source = self._source
        self._sharp = self._lensed = None
        if self._solid or source is None or not glass.enabled or not self.isVisible() or not source.isVisible():
            self._backdrop = self._frosted = None
            return
        area = self.backdrop_area().intersected(source.rect())
        if area.isEmpty():
            self._backdrop = self._frosted = None
            self.update()
            return
        _ask_for_lens()  # also here: a look with glass can come after the Liquid style was chosen
        grab = source.grab(area).toImage()
        blurred = _softened(grab, area.size())
        self._sharp = grab if glass.liquid else None
        self._backdrop_area = area
        self._shift = QPoint()
        # The part of the picture right behind the bar decides how strong the tint must be.
        origin = self._origin()
        scale_x, scale_y = blurred.width / area.width(), blurred.height / area.height()
        inner = QRectF((origin.x() - area.x()) * scale_x, (origin.y() - area.y()) * scale_y,
                       self.width() * scale_x, self.height() * scale_y)
        light_text = luminance(QColor(self.theme().text)) > 0.5
        self._columns, self._under, self._mean = _behind(blurred, inner, light_text)
        tone = self._chosen_tone(self._mean)
        if tone != self.tone:  # the other theme's colours: what is hardest to read on changes with them
            self.set_tone(tone)
            light_text = luminance(QColor(self.theme().text)) > 0.5
            self._columns, self._under, self._mean = _behind(blurred, inner, light_text)
        # Each child's own side only where its ink can follow it: through the lens, in a bar that changes tone.
        sided = glass.liquid and glass.adaptive
        self._sides = self._child_sides(blurred, origin - area.topLeft(), (scale_x, scale_y)) if sided else {}
        self._frosted = _qimage(blurred)
        self._tint()
        self.update()

    def _origin(self) -> QPoint:
        """Where the bar is, in the source's coordinates."""
        return self._source.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))

    def _tint(self) -> None:
        """Lay the theme's tint over the blurred backdrop, column by column as strong as reading there needs:
        clearer over the dark parts of a page under light text, or the light parts under dark text."""
        t = self.theme()
        least = max(least_alpha(t), self.min_tint_alpha / 255)
        frosted = self._frosted
        # Scrolling over a plain page or the canvas often brings the same columns again: keep their tint.
        key = (self._columns, self._under, frosted.width(), least, t.text, t.glass_base.rgba())
        if self._tint_key is None or self._tint_key[0] != key:
            profile = alpha_profile(self._columns, t, least)
            first, last = self._under
            # Left to right through the middle of each column, as strong as the profile; the painter blends
            # from one column to the next.
            gradient = QLinearGradient(0, 0, frosted.width(), 0)
            for index, alpha in enumerate(profile):
                colour = QColor(t.glass_base)
                colour.setAlpha(min(255, math.ceil(alpha * 255)))
                gradient.setColorAt((index + 0.5) / len(profile), colour)
            self._tint_key = (key, max(profile[first:last] or profile), gradient)
        _, self._alpha, gradient = self._tint_key
        tinted = frosted.copy()
        painter = QPainter(tinted)
        painter.fillRect(tinted.rect(), gradient)
        painter.end()
        self._backdrop = QPixmap.fromImage(tinted)
        self._flat = _over(t.glass_base, self.veil() if glass.liquid else self.tint_alpha, self._mean)

    def shift_backdrop(self, dx: int, dy: int) -> None:
        """The content behind moved by (dx, dy) pixels: move the picture with it until the next sample."""
        if self._backdrop is not None and (dx or dy):
            self._shift += QPoint(dx, dy)
            self.update()

    def covers(self, origin: QPoint | None = None) -> bool:
        """Whether the sampled picture, moved with the content, still lies under all of the bar that is over the
        source, with a texel to spare all round for its smooth stretch. False after a long jump (a page turn,
        a fast scroll): resample then, not a frame later. `origin` is the bar's place in the source, when the
        caller knows it already."""
        if self._backdrop is None or self._source is None:
            return True
        bar = QRect(self._origin() if origin is None else origin, self.size()).intersected(self._source.rect())
        spare = bar.adjusted(-BLUR_STEP, -BLUR_STEP, BLUR_STEP, BLUR_STEP).intersected(self._source.rect())
        return bar.isEmpty() or self._backdrop_area.translated(self._shift).contains(spare)

    def _child_sides(self, blurred: Image.Image, corner: QPoint, scale: tuple[float, float]) -> dict[QWidget, str]:
        """The tone the backdrop of each child asks for, in the blurred sample whose texels are `scale` per logical
        px, the bar's corner at `corner` in it: through the clear lens each child is read against what lies behind
        it alone, which can be the white page beside the dark canvas."""
        sides = {}
        for child, place in self._content():
            left, top = (corner.x() + place.x()) * scale[0], (corner.y() + place.y()) * scale[1]
            right, bottom = left + child.width() * scale[0], top + child.height() * scale[1]
            box = (max(0, int(left)), max(0, int(top)), min(blurred.width, math.ceil(right)),
                   min(blurred.height, math.ceil(bottom)))
            if box[0] < box[2] and box[1] < box[3]:
                sides[child] = _tone_for(_larger_part(blurred, box), self._sides.get(child, self.tone))
        return sides

    def _on_theme(self, *_) -> None:
        self._sides = {}  # a side is named after the theme's tones: the next sample finds them again
        self.set_tone("")  # the new theme's own colours, until the next sample says otherwise
        if self._frosted is not None:
            self._tint()  # in the new theme's colours; the owner resamples soon after
        self.update()

    # Shadow ---------------------------------------------------------------------

    def _cast_shadow(self) -> _BarShadow | None:
        """The shadow, when the bar has one: bars inside a window do, popups and windows don't."""
        shadow = self._shadow
        if shadow is None or not shiboken6.isValid(shadow) or self.isWindow():
            return None
        return shadow

    def _attach_shadow(self) -> None:
        parent = self.parentWidget()
        if self._shadow is not None and not shiboken6.isValid(self._shadow):
            self._shadow = None  # went with an earlier parent
        if self._popup or parent is None or self.isWindow():
            if self._shadow is not None:
                self._shadow.hide()
            return
        if self._shadow is None:
            self._shadow = _BarShadow(self)
            self.destroyed.connect(self._shadow.deleteLater)
        elif self._shadow.parentWidget() is not parent:
            self._shadow.setParent(parent)
        self._shadow.follow()
        self._shadow.stackUnder(self)

    def event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ParentChange:
            self._attach_shadow()
        elif kind == QEvent.Type.ZOrderChange and (shadow := self._cast_shadow()) is not None:
            shadow.stackUnder(self)
        elif kind in (QEvent.Type.ChildAdded, QEvent.Type.ChildRemoved, QEvent.Type.LayoutRequest):
            if kind == QEvent.Type.ChildAdded and self._inks is not None and event.child().isWidgetType():
                self._inks.raise_()  # above every child, the new one too
            if self._halo is not None:
                self._halo = None  # other content, or the same laid out elsewhere: the halo shown is out of date
                self.update()
        return super().event(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if (shadow := self._cast_shadow()) is not None:
            shadow.follow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._inks is not None:
            self._inks.setGeometry(self.rect())
        if (shadow := self._cast_shadow()) is not None:
            shadow.follow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (shadow := self._cast_shadow()) is not None:
            shadow.follow()
            shadow.stackUnder(self)
        self.refresh_backdrop()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if (shadow := self._cast_shadow()) is not None:
            shadow.hide()

    def appear(self, rise: int = motion.RISE, ms: int = motion.NORMAL) -> None:
        """motion.appear() for a bar just shown: its shadow, a sibling, rises and fades with it."""
        shadow = self._cast_shadow()
        motion.appear(self, rise, ms, along=[shadow] if shadow is not None else [])

    def slide_in(self, edge: Qt.Edge = Qt.Edge.LeftEdge, distance: int | None = None, ms: int = motion.SLOW) -> None:
        """motion.slide_in() for a bar just shown: its shadow comes with it."""
        shadow = self._cast_shadow()
        motion.slide_in(self, edge, distance, ms, along=[shadow] if shadow is not None else [])

    # Painting -------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        if self._haloing:
            return
        t = self.theme()
        painter = QPainter(self)
        # Everything is laid out in device pixels, from the device pixels Qt gives the bar's rect: at 125% or
        # 175% the bar can start a fraction of a pixel in, and a 1 px edge drawn in logical pixels would blur.
        device = painter.deviceTransform()
        ratio = device.m11() or 1.0  # device pixels per logical pixel: the screen's ratio, or a grab's
        x, y = device.dx(), device.dy()
        left, top = _snapped(x), _snapped(y)
        right, bottom = _snapped(x + self.width() * ratio), _snapped(y + self.height() * ratio)
        if right <= left or bottom <= top:
            return
        # From here one unit is one device pixel, counted from where the bar's (0, 0) lands: also when a grab or
        # render() moves the bar with the painter's own transform rather than the backing store's offset.
        painter.setWorldTransform(QTransform.fromTranslate(x, y) * device.inverted()[0] * painter.worldTransform())
        rect = QRectF(left - x, top - y, right - left, bottom - top)
        look = self.theme().look
        radius = rect.height() / 2 if look.capsules and self.height() <= CAPSULE_MAX else look.panel * ratio
        shape = outline(rect, radius, look)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        textured = lensed = False
        if self._solid:
            painter.setBrush(QColor(t.raised))
        elif self._backdrop is not None and self._source is not None:
            origin = self._origin()
            textured = self.covers(origin)
            picture = self._through_lens(rect, radius, ratio, origin) if textured and self._sharp is not None else None
            if picture is not None:
                lensed = True
                brush = QBrush(picture)  # its pixels on the device's: no stretch
                brush.setTransform(QTransform.fromTranslate(rect.left(), rect.top()))
                painter.setBrush(brush)
            elif textured:
                painter.setBrush(self._backdrop_brush(ratio, origin))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            else:  # the content jumped past the picture: its mean colour until the new sample, moments away
                painter.setBrush(self._flat)
        else:
            painter.setBrush(t.glass_solid)
        painter.drawPath(shape)
        painter.drawPixmap(rect.topLeft(), self._edges(rect.size().toSize(), radius, ratio, textured, lensed))
        if lensed:
            # A paint of part of the bar is a child repainting itself: what it shows may have changed.
            painter.setBrush(QBrush(self._halo_picture(ratio, event.rect() != self.rect())))
            painter.drawPath(shape)
        else:  # no halo off the lens; what the content shows meanwhile is not followed, so none is kept either
            self._halo = None
            self._halo_check.stop()

    def _halo_picture(self, ratio: float, changed: bool) -> QImage:
        """The halo under the bar's content, in device pixels from the bar's (0, 0): the one kept, unless the
        content `changed` or the bar's size, scale, tone or theme did, or a child changed side."""
        t = self.theme()
        key = (self.size(), ratio, self.tone, t.name, t.look.key, t.glass_base.rgb())
        if changed or self._halo is None or self._halo[0] != key:
            pictures = self._pictures(ratio)
            self._halo = (key, self._content_alpha(ratio, pictures), pictures, None, None, ())
        key, alpha, pictures, sides, halo, turned = self._halo
        now = tuple(self._sides.get(child, self.tone) for child, _, _ in pictures)
        if now != sides:
            halo, turned = self._halos(alpha, pictures, now, ratio)
            self._halo = (key, alpha, pictures, now, halo, turned)
        # The pages moving can hide a child's repaint inside one of the whole bar: look again once they stop.
        self._halo_check.start()
        return halo

    def _content(self) -> list[tuple[QWidget, QPoint]]:
        """The bar's children and theirs, with where each lies from the bar's (0, 0). The separators are left out:
        a line between groups carries nothing to read, and a halo would make it a dark or a light bar."""
        found, walk = [], [(self, QPoint())]
        while walk:
            parent, offset = walk.pop()
            for child in parent.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if (child.isVisible() and not child.isWindow() and not child.property("separator")
                        and child is not self._inks):
                    found.append((child, offset + child.pos()))
                    walk.append((child, offset + child.pos()))
        return found

    def _pictures(self, ratio: float) -> list[tuple[QWidget, QRect, QImage]]:
        """Each child drawn alone (its own children are drawn in turn), with the device pixels of the bar it
        covers. Drawn where it lies in a picture of the whole bar, its glyphs fall on the pixels they fall on in
        the bar."""
        image = QImage(math.ceil(self.width() * ratio), math.ceil(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        alone = QWidget.RenderFlag(0)
        pictures = []
        self._haloing = True
        try:
            for child, place in self._content():
                left, top = math.floor(place.x() * ratio), math.floor(place.y() * ratio)
                box = QRect(left, top, math.ceil((place.x() + child.width()) * ratio) - left,
                            math.ceil((place.y() + child.height()) * ratio) - top).intersected(image.rect())
                if box.isEmpty():
                    continue
                image.fill(Qt.GlobalColor.transparent)
                child.render(image, place, QRegion(), alone)
                picture = image.copy(box)
                picture.setDevicePixelRatio(1.0)
                pictures.append((child, box, picture))
        finally:
            self._haloing = False
        return pictures

    def _content_alpha(self, ratio: float, pictures: list | None = None) -> QImage:
        """The bar's children drawn alone (or those of `pictures`), as an 8-bit alpha picture in device pixels."""
        image = QImage(math.ceil(self.width() * ratio), math.ceil(self.height() * ratio),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        for _, box, picture in self._pictures(ratio) if pictures is None else pictures:
            painter.drawImage(box.topLeft(), picture)
        painter.end()
        return image.convertToFormat(QImage.Format.Format_Alpha8)

    def _halos(self, alpha: QImage, pictures: list, sides: tuple, ratio: float) -> tuple[QImage, tuple]:
        """The halo of the children on their `sides`, each in the glass of its ink (the light glass under dark ink,
        the dark under light), so over a plain page or the plain canvas it melts into what the lens shows; and the
        children whose ink is not the bar's, as (where, picture in the other ink)."""
        bar_dark = luminance(QColor(self.theme().text)) < 0.5
        inks = [(side or themes.current.name) == "light" for side in sides]  # dark ink over a light backdrop
        turned = tuple((box.topLeft(), _turned(picture))
                       for (_, box, picture), dark in zip(pictures, inks) if dark != bar_dark)
        if not turned:  # every child in the bar's own ink: one halo, in its glass
            return self._build_halo(alpha, ratio, self.theme().glass_base), ()
        halo = QImage(alpha.size(), QImage.Format.Format_ARGB32_Premultiplied)
        halo.fill(Qt.GlobalColor.transparent)
        painter = QPainter(halo)
        for dark in (True, False):
            group = [entry for entry, ink in zip(pictures, inks) if ink == dark]
            if group:
                painter.drawImage(0, 0, self._build_halo(self._content_alpha(ratio, group), ratio, self._glass(dark)))
        painter.end()
        return halo, turned

    def _glass(self, dark_ink: bool) -> QColor:
        """The glass ink of this lightness stands on: the bar's own, or the other tone's. A style with one palette
        has no other tone; there it is the page its ink is for, white or black, under the bar's veil."""
        t = self.theme()
        if (luminance(QColor(t.text)) < 0.5) == dark_ink:
            return t.glass_base
        other = themes.tone("light" if dark_ink else "dark")
        if (luminance(QColor(other.text)) < 0.5) == dark_ink:
            return other.glass_base
        return _over(t.glass_base, self.veil(), QColor(255, 255, 255) if dark_ink else QColor(0, 0, 0))

    def _build_halo(self, alpha: QImage, ratio: float, base: QColor) -> QImage:
        """The halo of the content whose `alpha` this is: that shape spread and blurred (one gaussian, as
        _softened's), made dense next to the glyphs, in the colour `base`."""
        width, height = alpha.width(), alpha.height()
        mask = Image.frombuffer("L", (width, height), bytes(alpha.constBits()), "raw", "L", alpha.bytesPerLine(), 1)
        spread = 2 * max(1, round(HALO_SPREAD * ratio)) + 1
        mask = mask.filter(ImageFilter.MaxFilter(spread)).filter(ImageFilter.GaussianBlur(HALO_SIGMA * ratio))
        mask = mask.point([min(255, round(level * HALO_GAIN)) for level in range(256)])
        halo = Image.new("RGBA", (width, height), (base.red(), base.green(), base.blue(), 0))
        halo.putalpha(mask)
        picture = QImage(halo.tobytes(), width, height, 4 * width, QImage.Format.Format_RGBA8888).copy()
        return picture

    def _check_halo(self) -> None:
        """Once the pages stop: build the halo again if the content changed under repaints of the whole bar."""
        if self._halo is not None and glass.liquid and self.isVisible():
            if self._content_alpha(self._halo[0][1]) != self._halo[1]:  # pixel by pixel, not the rows' padding
                self._halo = None
                self.update()

    def _through_lens(self, rect: QRectF, radius: float, ratio: float, origin: QPoint) -> QImage | None:
        """The bar through the lens, in the device pixels of `rect`: the picture kept from the last paint while the
        sample, the shift, the size, the ratio and the tone stay as they were. None when the lens draws nothing;
        when that is because the lens went off, every bar samples again as the Clear glass, after this paint."""
        if not glass.liquid or self._sharp is None:
            return None
        t = self.theme()
        # As _backdrop_brush() lays the picture, but moved by whole device pixels: at 125% or 175% a move of whole
        # logical px would put the sharp picture between texels, and the rim would smear.
        corner = self._backdrop_area.topLeft() - origin
        place = (corner.x() * ratio + round(self._shift.x() * ratio) - rect.left(),
                 corner.y() * ratio + round(self._shift.y() * ratio) - rect.top())
        area = self._backdrop_area.size()
        base, light, edge = t.glass_base, t.glass_highlight, t.glass_border
        spec = lens.Spec(
            int(rect.width()), int(rect.height()), ratio, radius, t.look.chamfer,
            place,
            (area.width() * ratio, area.height() * ratio),
            (base.red(), base.green(), base.blue()),
            # The same faint veil whatever is behind: the halo, not the glass, keeps the content readable.
            self.veil(),
            (light.red(), light.green(), light.blue(), light.alpha()),
            (edge.red(), edge.green(), edge.blue(), edge.alpha()),
            # The rim reaches the middle of the bar: one lens over all of it, with no flat middle.
            band=min(LIQUID_BAND, min(rect.width(), rect.height()) / ratio / 2))
        key = (self._sharp.cacheKey(), spec)
        if self._lensed is None or self._lensed[0] != key:
            # The sharp page is both pictures: a rim that never gives way shows no middle.
            picture = lens.render(self._sharp, self._sharp, spec)
            self._lensed = None if picture is None else (key, picture)
            if picture is None and not lens.available():
                QTimer.singleShot(0, lambda: set_glass(glass.style))  # never from inside a paint
        return None if self._lensed is None else self._lensed[1]

    def _backdrop_brush(self, ratio: float, origin: QPoint) -> QBrush:
        """The blurred picture as a brush, in device pixels from the bar's origin (`origin` in the source):
        where the backdrop was sampled, moved as far as the content has moved since, each texel stretched
        smoothly over its pixels."""
        place = self._backdrop_area.topLeft() + self._shift - origin
        sx = self._backdrop_area.width() * ratio / self._backdrop.width()
        sy = self._backdrop_area.height() * ratio / self._backdrop.height()
        brush = QBrush(self._backdrop)
        brush.setTransform(QTransform(sx, 0, 0, sy, place.x() * ratio, place.y() * ratio))
        return brush

    def _edges(self, size: QSize, radius: float, ratio: float, grain: bool, lensed: bool = False) -> QPixmap:
        """The faint grain, the line of light inside the top edge and the fine edge all round, in device pixels:
        put together once per size and theme. The edge and the light are each a whole number of device
        pixels wide (one at 100% and 125%, two from 150%), and their straight runs fill whole pixels. Over the
        lens's picture, the grain alone: the light and the edge are in the picture."""
        t = self.theme()
        width = max(1, int(ratio + 0.5))
        colours = (t.raised, t.border) if self._solid else (t.glass_highlight.rgba(), t.glass_border.rgba())
        lit = not self._solid or t.look.rail  # a style with a lit rail keeps it on solid bars too
        colours += (t.glass_highlight.rgba(),) if lit else ()
        key = (size.width(), size.height(), radius, width, grain, lensed, self._solid, colours, glass.style,
               t.look.key)
        if self._overlay is not None and self._overlay[0] == key:
            return self._overlay[1]
        picture = QPixmap(size)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, size.width(), size.height())
        look = t.look
        if grain:
            shape = outline(rect, radius, look)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_noise_tile()))
            painter.drawPath(shape)
        if lensed:
            painter.end()
            self._overlay = (key, picture)
            return picture
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if lit:
            # Light along the top edge, fading down the corners: the second ring of pixels in. The Clear glass
            # catches some along the bottom too, as the rim of a lens does.
            light = QLinearGradient(QPointF(0, width), QPointF(0, rect.height() - width))
            fade = min(0.5, (radius + width) / max(rect.height(), 1.0))
            clear = QColor(255, 255, 255, 0)
            light.setColorAt(0.0, t.glass_highlight)
            light.setColorAt(fade, clear)
            if glass.clear:
                rim = QColor(t.glass_highlight)
                rim.setAlpha(rim.alpha() // 2)
                light.setColorAt(1.0 - fade, clear)
                light.setColorAt(1.0, rim)
            else:
                light.setColorAt(1.0, clear)
            inset = width * 1.5
            painter.setPen(QPen(QBrush(light), width))
            painter.drawPath(outline(rect.adjusted(inset, inset, -inset, -inset), max(radius - inset, 0), look))
        inset = width / 2
        painter.setPen(QPen(QColor(t.border) if self._solid else t.glass_border, width))
        painter.drawPath(outline(rect.adjusted(inset, inset, -inset, -inset), max(radius - inset, 0), look))
        painter.end()
        self._overlay = (key, picture)
        return picture


class Switch(QAbstractButton):
    """An on/off switch, for settings that are either on or off."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._travel = motion.Smooth(self, 1.0 if checked else 0.0)  # 0 left, 1 right
        self._travel.changed.connect(self.update)
        self.toggled.connect(self._on_toggled)
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def _on_toggled(self, checked: bool) -> None:
        if not self._travel.running:
            self._travel.set(0.0 if checked else 1.0)  # it starts from the other end, also after a silent setChecked()
        self._travel.go(1.0 if checked else 0.0, motion.FAST, motion.EASE_IN_OUT)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, (self.height() - 20) / 2, 38, 20)
        if keyboard_focus(self):
            # The ring runs round the switch's outline, and the switch draws in a little inside it.
            focus_ring(painter, track.adjusted(-1, -1, 1, 1), 11)
            track = track.adjusted(3, 3, -3, -3)
        # Mid-animation the knob is where the animation put it; otherwise where the state says,
        # which also covers setChecked() with signals blocked.
        mix = self._travel.value if self._travel.running else (1.0 if self.isChecked() else 0.0)
        faded = 1.0 if self.isEnabled() else 0.45
        draw = {"material": self._material, "dot": self._dot, "neon": self._neon}.get(t.look.switch)
        if draw is not None:
            painter.setOpacity(faded)
            draw(painter, t, track, mix)
            return
        # Off, as Windows 11 draws it: an outline and a small knob in the muted text colour, which read on
        # any panel (a track in the border colour was 1.44:1 on light). On: the accent, with a white knob.
        radius = track.height() / 2
        muted, on = QColor(t.text_muted), QColor(t.accent)
        if mix < 1.0:
            outline = QColor(muted)
            outline.setAlphaF((1.0 - mix) * faded)
            painter.setPen(QPen(outline, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        if mix > 0.0:
            on.setAlphaF(mix * faded)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(on)
            painter.drawRoundedRect(track, radius, radius)
        white = QColor(t.accent_text)  # white on Zenith's accents; dark on a light accent
        knob_colour = QColor.fromRgbF(
            muted.redF() + (white.redF() - muted.redF()) * mix,
            muted.greenF() + (white.greenF() - muted.greenF()) * mix,
            muted.blueF() + (white.blueF() - muted.blueF()) * mix,
        )
        knob_colour.setAlphaF(faded + (1.0 - faded) * mix)  # a white knob stays white on a faded track
        knob = QPointF(track.left() + radius + mix * (track.width() - 2 * radius), track.center().y())
        size = radius - 4 + 2 * mix
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_colour)
        painter.drawEllipse(knob, size, size)

    @staticmethod
    def _knob(painter: QPainter, track: QRectF, mix: float, colour: QColor, size: float) -> QPointF:
        """A round knob `size` px in radius, from the left end of `track` (off) to the right one (on); its centre."""
        radius = track.height() / 2
        centre = QPointF(track.left() + radius + mix * (track.width() - 2 * radius), track.center().y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(centre, size, size)
        return centre

    def _material(self, painter: QPainter, t: Theme, track: QRectF, mix: float) -> None:
        """Material 3: off, a 2 px outline and a small knob in the muted text colour; on, the accent, with a knob
        grown to 16 px in the colour of text on the accent and a check on it, as Android's settings show."""
        radius = track.height() / 2
        muted = QColor(t.text_muted)
        if mix < 1.0:
            edge = QColor(muted)
            edge.setAlphaF(1.0 - mix)
            painter.setPen(QPen(edge, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        if mix > 0.0:
            fill = QColor(t.accent)
            fill.setAlphaF(mix)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(track, radius, radius)
        small = min(5.0, radius - 3)
        size = small + (radius - 2 - small) * mix
        centre = self._knob(painter, track, mix, _mixed(muted, QColor(t.accent_text), mix), size)
        if mix > 0.0:
            tick = QColor(t.accent)
            tick.setAlphaF(mix)
            pen = QPen(tick, t.look.stroke)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([centre + QPointF(-0.5 * size, 0.05 * size),
                                  centre + QPointF(-0.15 * size, 0.4 * size),
                                  centre + QPointF(0.5 * size, -0.35 * size)])

    def _neon(self, painter: QPainter, t: Theme, track: QRectF, mix: float) -> None:
        """Neon's: a cut track and a square knob; off in the muted text colour, on lit with the accent."""
        muted, accent = QColor(t.text_muted), QColor(t.accent)
        edge = _mixed(muted, accent, mix)
        glow = QColor(accent)
        glow.setAlpha(round(46 * mix))
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(glow)
        painter.drawPath(outline(track.adjusted(0.5, 0.5, -0.5, -0.5), 4, t.look))
        side = track.height() - 8
        left = track.left() + 4 + mix * (track.width() - 8 - side)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(edge)
        painter.drawRect(QRectF(left, track.top() + 4, side, side))

    def _dot(self, painter: QPainter, t: Theme, track: QRectF, mix: float) -> None:
        """Nothing's, in the text colour alone: off, a 1 px outline and a dot at the left; on, the track filled
        and the dot in the window colour at the right."""
        radius = track.height() / 2
        ink = QColor(t.text)
        painter.setPen(QPen(ink, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        if mix > 0.0:
            fill = QColor(ink)
            fill.setAlphaF(mix)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(track, radius, radius)
        # The dot turns at half way: blended, it would match the half-filled track and vanish mid-slide.
        self._knob(painter, track, mix, QColor(t.window) if mix >= 0.5 else ink, min(5.0, radius - 3))


# Keyboard focus ----------------------------------------------------------------------

PICK_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_Select)
KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                    Qt.FocusReason.ShortcutFocusReason)


def keyboard_focus(widget: QWidget) -> bool:
    """Whether `widget` has the focus and the keyboard put it there: then, and only then, a ring shows."""
    return widget.hasFocus() and widget.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)


def focus_ring(painter: QPainter, rect: QRectF, radius: float) -> None:
    """The keyboard focus ring: two pixels of the style's focus colour (the accent) just inside `rect`."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(themes.current.focus or themes.current.accent), 2.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), max(radius - 1, 0.0), max(radius - 1, 0.0))
    painter.restore()


def moved_cursor(index: int, count: int, columns: int, key) -> int | None:
    """Where an arrow, Home or End key takes a cursor at `index` over `count` cells in rows of `columns`.

    None for any other key. Left and Right run on from one row into the next; Up and Down
    keep the column. In a single row Up and Down act as Left and Right. The cursor stops
    at the first and last cells.
    """
    if count <= 0:
        return None
    columns = max(1, min(columns, count))
    single_row = columns >= count
    if key == Qt.Key.Key_Left or (single_row and key == Qt.Key.Key_Up):
        return max(index - 1, 0)
    if key == Qt.Key.Key_Right or (single_row and key == Qt.Key.Key_Down):
        return min(index + 1, count - 1)
    if key == Qt.Key.Key_Up:
        return index - columns if index - columns >= 0 else index
    if key == Qt.Key.Key_Down:
        return index + columns if index + columns < count else index
    if key == Qt.Key.Key_Home:
        return 0
    if key == Qt.Key.Key_End:
        return count - 1
    return None


class _RingWatcher(QObject):
    """Keeps the "ring" property of a style-sheet control true while the keyboard gave it the focus.

    A style sheet's :focus also matches after a click; [ring="true"] only after Tab, so
    a slider dragged with the mouse shows no ring and one reached with Tab does.
    """

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            ring = kind == QEvent.Type.FocusIn and event.reason() in KEYBOARD_REASONS
            if bool(watched.property("ring")) != ring:
                watched.setProperty("ring", ring)
                watched.style().unpolish(watched)
                watched.style().polish(watched)
                watched.update()
        return False


_ring_watcher: _RingWatcher | None = None


def ring_on_keyboard_focus(widget: QWidget) -> None:
    """Let a style sheet draw `widget`'s focus ring with [ring="true"]: only when Tab brought the focus."""
    global _ring_watcher
    if _ring_watcher is None:
        _ring_watcher = _RingWatcher(QApplication.instance())
    widget.setProperty("ring", False)
    widget.installEventFilter(_ring_watcher)


# Segmented control -------------------------------------------------------------------


def _mixed(base: QColor, other: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(a + (b - a) * amount for a, b in (
        (base.redF(), other.redF()), (base.greenF(), other.greenF()), (base.blueF(), other.blueF()))))


def segment_colours(t: Theme) -> tuple[QColor, QColor, QColor]:
    """The track of a segmented control, its picked segment, and that segment's 1 px border.

    On a dark surface the theme's segment colour is hardly lighter than the track, so the
    track sinks a little and the picked segment rises, until it reads at a glance.
    """
    if t.look.key == "zenith" and luminance(QColor(t.raised)) < 0.2:  # the other styles pick their own
        return (QColor(0, 0, 0, 56), _mixed(QColor(t.segment), QColor(t.text), 0.10),
                _mixed(QColor(t.segment), QColor(t.text), 0.22))
    return qcolor(t.track), QColor(t.segment), QColor(t.border)


class _Segment(QAbstractButton):
    """One choice of a Segmented control. It takes clicks; the control takes the keys."""

    def __init__(self, text: str, control: Segmented):
        super().__init__(control)
        self._control = control
        self._hover = motion.Fill(self)
        self._hover.changed.connect(self.update)
        self.setText(tr(text))
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # one tab stop for the whole control

    def sizeHint(self) -> QSize:
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)  # as wide as it gets once picked
        return QSize(QFontMetrics(bold).horizontalAdvance(self.text()) + 28, 28)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, 28)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = t.look.item
        font = QFont(self.font())
        chosen = QColor(t.segment_text or t.text)
        # The fill of the chosen segment is the control's (Segmented.paintEvent): it slides here, and the label
        # takes its colours and weight as the fill comes under it.
        lit = self._control.lit(self)
        hot = self.underMouse() and self.isEnabled() and lit < 0.5
        self._hover.go(qcolor(t.hover) if hot else _CLEAR, motion.FAST if hot else motion.NORMAL)
        if self._hover.colour.alpha():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._hover.colour)
            painter.drawRoundedRect(rect, radius, radius)
        if lit >= 0.5:
            font.setWeight(QFont.Weight.DemiBold)
        color = motion.mixed(QColor(t.text if hot else t.text_muted), chosen, lit)
        if not self.isEnabled():
            color = chosen if lit >= 0.5 else QColor(t.text_muted)
            color.setAlpha(140)  # on the chosen segment's fill, which may be the text colour itself
        if self._control.ring_on(self):
            focus_ring(painter, rect, radius)
        painter.setFont(font)
        painter.setPen(color)
        text = QFontMetrics(font).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 12)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr(text))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class Segmented(QWidget):
    """A segmented control: a few choices side by side on a track, one of them picked.

    `items` are (value, label) pairs, or plain labels whose values are their positions.
    `chosen` carries a value the user picks (a click, the keys, or choose()), only when
    it differs from the one picked before. `changed` carries the position of the picked
    segment whenever it changes, also through set_value(), set_current() or setChecked()
    on one of `buttons`, which are checkable and exclusive like radio buttons.

    Tab reaches the control once. The arrow keys move a ring between the segments, and
    Enter or Space picks the one with the ring.
    """

    chosen = Signal(object)
    changed = Signal(int)

    def __init__(self, items, tips: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.items = [item if isinstance(item, tuple) else (index, item) for index, item in enumerate(items)]
        self.tips = dict(tips or {})
        self.value = self.items[0][0] if self.items else None
        self._before = self.value  # the value when a segment was pressed
        self._cursor = 0  # the segment the keys point at
        self._thumb = motion.Smooth(self)  # where the chosen fill is, in segments from the first: it slides
        self._thumb.changed.connect(self._repaint)
        self._group = QButtonGroup(self)
        self.buttons: list[_Segment] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        for index, (value, label) in enumerate(self.items):
            button = _Segment(label, self)
            if self.tips.get(value):
                button.setToolTip(tr(self.tips[value]))
            self._group.addButton(button, index)
            button.pressed.connect(self._remember)
            button.clicked.connect(self._clicked)
            row.addWidget(button, 1)
            self.buttons.append(button)
        if self.buttons:
            self.buttons[0].setChecked(True)
        self._group.idToggled.connect(self._toggled)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        themes.changed.connect(self._repaint)

    # Values ---------------------------------------------------------------------

    def _index(self, value) -> int:
        return next((i for i, (v, _) in enumerate(self.items) if v == value), -1)

    def current(self) -> int:
        """The position of the picked segment, -1 for none."""
        return self._group.checkedId()

    def set_current(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def set_value(self, value) -> None:
        """Show `value` as picked, without `chosen`; a value that is no segment's leaves none picked."""
        index = self._index(value)
        self._before = value
        if index >= 0:
            self.buttons[index].setChecked(True)
        else:
            self._group.setExclusive(False)
            for button in self.buttons:
                button.setChecked(False)
            self._group.setExclusive(True)
        self.value = value
        self._repaint()

    def choose(self, value) -> None:
        """What a click on the segment with `value` does."""
        index = self._index(value)
        if index < 0 or value == self.value:
            return
        self.buttons[index].setChecked(True)
        self._before = value
        self.chosen.emit(value)

    def _toggled(self, index: int, checked: bool) -> None:
        if checked:
            slide = self._index(self.value) >= 0  # from one segment to another; from none, the fill just appears
            self.value = self.items[index][0]
            self._cursor = index
            if slide:
                self._thumb.go(index, motion.NORMAL, motion.EASE_IN_OUT)
            else:
                self._thumb.set(index)
            self._repaint()
            self.changed.emit(index)

    def _remember(self) -> None:
        self._before = self.value

    def _clicked(self) -> None:
        if self.value != self._before:
            self._before = self.value
            self.chosen.emit(self.value)

    # Keys -----------------------------------------------------------------------

    def ring_on(self, button: _Segment) -> bool:
        """Whether `button` shows the keyboard's ring."""
        return keyboard_focus(self) and 0 <= self._cursor < len(self.buttons) and self.buttons[self._cursor] is button

    def focusInEvent(self, event) -> None:
        index = self.current()
        self._cursor = index if index >= 0 else 0
        self._repaint()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._repaint()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        moved = moved_cursor(self._cursor, len(self.items), len(self.items), event.key())
        if moved is not None and not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            self._cursor = moved
            self._repaint()
        elif event.key() in PICK_KEYS and self.items and self.buttons[self._cursor].isEnabled():
            self.choose(self.items[self._cursor][0])
        else:
            super().keyPressEvent(event)

    # Painting -------------------------------------------------------------------

    def _repaint(self, *_) -> None:
        self.update()
        for button in self.buttons:
            button.update()

    def lit(self, button: _Segment) -> float:
        """How much of the chosen fill is under `button`: 1 once it is there, 0 when the fill is a segment or more
        away, and between while it slides."""
        if self.current() < 0:
            return 0.0
        return max(0.0, 1.0 - abs(self._thumb.value - self.buttons.index(button)))

    def _thumb_rect(self) -> QRectF | None:
        """Where the chosen fill is now: over the chosen segment, or between two while it slides."""
        if self.current() < 0:
            return None
        place = min(max(self._thumb.value, 0.0), len(self.buttons) - 1)
        low = int(place)
        first = QRectF(self.buttons[low].geometry())
        second = QRectF(self.buttons[min(low + 1, len(self.buttons) - 1)].geometry())
        share = place - low
        return QRectF(first.x() + (second.x() - first.x()) * share, first.y() + (second.y() - first.y()) * share,
                      first.width() + (second.width() - first.width()) * share,
                      first.height() + (second.height() - first.height()) * share)

    def paintEvent(self, event) -> None:
        t = themes.current
        track, fill, edge = segment_colours(t)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(self.rect()), t.look.control, t.look.control)
        thumb = self._thumb_rect()
        if thumb is not None:
            painter.setPen(QPen(edge, 1.0))
            painter.setBrush(fill)
            painter.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), t.look.item, t.look.item)


def separator(vertical: bool = True) -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setProperty("separator", True)  # a Liquid bar leaves these out of its halo
    if vertical:
        line.setFixedSize(1, 20)
    else:
        line.setFixedHeight(1)
    return line


def muted_label(text: str = "") -> QLabel:
    label = QLabel(native_keys(tr(text)))
    label.setObjectName("Muted")
    return label


COPIED_MS = 1500  # how long a copy button says it copied


def say_copied(button) -> None:
    """A copy button says "Copied" for a moment, then its own name again."""
    name = button.property("label_before_copied") or button.text()
    button.setProperty("label_before_copied", name)
    button.setText(tr("Copied"))

    def back() -> None:
        if shiboken6.isValid(button):
            button.setText(tr(name))

    QTimer.singleShot(COPIED_MS, button, back)


def panel_title(text: str) -> QLabel:
    label = QLabel(tr(text))
    label.setObjectName("PanelTitle")
    return label


# Dot-matrix titles -------------------------------------------------------------------

DOT_TITLES = {"SettingsTitle", "ChooserTitle", "HomeSection", "SettingsSection", "AboutName", "HomeBrand"}
DOT_ROWS = 8  # a capital letter is this many dots tall


@lru_cache(maxsize=64)
def _dot_picture(text: str, font: QFont, width: int, height: int, rgba: int, flags: int, scale: int) -> QPixmap:
    """`text` as a dot matrix over a `width` x `height` box: drawn small, one pixel a cell, and a dot in each cell
    the glyphs cover more than half of. `scale` is the screen ratio in hundredths."""
    cell = max(QFontMetricsF(font).capHeight() / DOT_ROWS, 1.0)
    columns, rows = max(1, math.ceil(width / cell)), max(1, math.ceil(height / cell))
    small = QImage(columns, rows, QImage.Format.Format_ARGB32_Premultiplied)
    small.fill(Qt.GlobalColor.transparent)
    painter = QPainter(small)
    painter.scale(1 / cell, 1 / cell)
    painter.setFont(font)
    painter.setPen(QColor(0, 0, 0))
    painter.drawText(QRectF(0, 0, width, height), flags, text)
    painter.end()
    ratio = scale / 100
    picture = QPixmap(max(1, round(width * ratio)), max(1, round(height * ratio)))
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor.fromRgba(rgba))
    dot = 0.35 * cell  # 70% of the cell wide
    for y in range(rows):
        for x in range(columns):
            if small.pixelColor(x, y).alpha() > 127:
                painter.drawEllipse(QPointF((x + 0.5) * cell, (y + 0.5) * cell), dot, dot)
    painter.end()
    return picture


class _DotTitles(QObject):
    """Paints the text of the large titles (DOT_TITLES) as a dot matrix, in the label's own font, colour and
    place, and keeps the label from painting it again. It watches the whole application, so it is installed
    only while the style asks for it."""

    def eventFilter(self, watched, event) -> bool:
        if (event.type() != QEvent.Type.Paint or not isinstance(watched, QLabel)
                or watched.objectName() not in DOT_TITLES or not watched.text()):
            return False
        margin = watched.margin()
        box = watched.contentsRect().adjusted(margin, margin, -margin, -margin)
        align = watched.alignment()
        indent = watched.indent()
        if indent < 0 and watched.frameWidth():  # as QLabel does: half an "x" in from a frame
            indent = watched.fontMetrics().horizontalAdvance("x") // 2 - margin
        if indent > 0:
            box.adjust(indent if align & Qt.AlignmentFlag.AlignLeft else 0,
                       indent if align & Qt.AlignmentFlag.AlignTop else 0,
                       -indent if align & Qt.AlignmentFlag.AlignRight else 0,
                       -indent if align & Qt.AlignmentFlag.AlignBottom else 0)
        flags = align.value | (Qt.TextFlag.TextWordWrap.value if watched.wordWrap() else 0)
        colour = watched.palette().color(watched.foregroundRole())
        picture = _dot_picture(watched.text(), watched.font(), box.width(), box.height(), colour.rgba(), flags,
                               round(watched.devicePixelRatioF() * 100))
        painter = QPainter(watched)
        painter.drawPixmap(box.topLeft(), picture)
        painter.end()
        return True


_dot_titles: _DotTitles | None = None


def _follow_dot_titles(theme: Theme) -> None:
    """Watch the titles while the style draws them as dots (Look.dot_titles), and only then."""
    global _dot_titles
    app = QApplication.instance()
    if _dot_titles is None:
        _dot_titles = _DotTitles(app)
    if theme.look.dot_titles:
        app.installEventFilter(_dot_titles)  # once: installing it again only moves it to the front
    else:
        app.removeEventFilter(_dot_titles)


themes.changed.connect(_follow_dot_titles)
