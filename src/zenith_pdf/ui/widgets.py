"""Small building blocks shared across the interface."""

from __future__ import annotations

import math
import random
from functools import lru_cache

import shiboken6
from PIL import Image, ImageFilter
from PySide6.QtCore import (QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QIconEngine,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
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

from ..core.platforms import native_keys
from ..icons import icon
from ..theme import Theme, qcolor, themes, tone_sheet

GLASS_BLEED = 24  # logical px of backdrop sampled beyond the bar: the blur reaches its edges from what is there
GLASS_RADIUS = 14  # corner radius of tall glass panels, as the tool menu's; bars are capsules
CAPSULE_MAX = 48  # px: a floating bar this tall or less is a capsule, its ends half circles
BLUR_STEP = 2  # logical px per texel of the blurred backdrop, which the painter stretches smoothly
BLUR_SIGMA = 3.5  # logical px: lines of text show through as soft strokes, too soft to read
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
GLASS_STYLES = ("clear", "balanced", "solid")
SATURATION = {"clear": 1.6, "balanced": 1.2}  # colours behind the glass, lifted so the tint never turns them grey
NOISE_ALPHA = 4  # 0-255: the faint grain on the glass, which keeps its smooth gradients from banding
NOISE_SIZE = 64  # device px of the grain's tile
FADE_MS = 150  # floating bars and chips come and go this fast
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
    paints the bars opaque.
    """

    style = "clear"
    adaptive = True  # the bars take the light or the dark theme's colours, whichever suits what is behind them

    @property
    def enabled(self) -> bool:
        return self.style != "solid"


glass = _Glass()


def glass_style(value) -> str:
    """A "glass" setting as one of GLASS_STYLES. Older versions stored on (True) or off (False)."""
    if value is True or value == "true":
        return "clear"
    if value is False or value == "false":
        return "solid"
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
    glass.style = glass_style(value)
    for widget in QApplication.allWidgets():
        if isinstance(widget, FloatingBar):
            widget.refresh_backdrop()
            widget.update()


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
    if glass.style == "clear":
        return CLEAR_TEXT_CONTRAST, CLEAR_BUSY_CONTRAST
    return TEXT_CONTRAST, BUSY_CONTRAST


def least_alpha(theme: Theme) -> float:
    """The tint's least opacity in the glass style in use: the clear glass shows more of what is behind it."""
    return theme.glass_clear_alpha if glass.style == "clear" else theme.glass_alpha


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
    lift = SATURATION.get(glass.style, 1.0)
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
    """Keeps every picture of an icon it has drawn, by size, mode, state and screen scale.

    The bars repaint whenever the pages scroll under them; drawing the vector glyphs
    again each time cost more than the scrolling itself.
    """

    def __init__(self, source: QIcon):
        super().__init__()
        self._source = source
        self._pixmaps: dict[tuple, QPixmap] = {}

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        key = (size.width(), size.height(), mode, state, round(scale * 100))
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = self._source.pixmap(size, scale, mode, state)
            self._pixmaps[key] = pixmap
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


class IconButton(QToolButton):
    """A flat tool button whose icon follows the active theme.

    On a glass bar an icon-only button is a BAR_BUTTON square.
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
        self.setToolTip(tooltip)
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if text:
            self.setText(text)
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
        self.setIcon(cached_icon(self._icon_name, self._color or self._tone_text()))

    def _tone_text(self) -> str | None:
        """The text colour of the glass bar the button is on, when that bar changed tone; else None (the theme's)."""
        widget = self.parentWidget()
        while widget is not None and not widget.isWindow():
            if isinstance(widget, FloatingBar):
                return widget.theme().text if widget.tone else None
            widget = widget.parentWidget()
        return None

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and self._on_glass():
            return hint.expandedTo(QSize(BAR_BUTTON, BAR_BUTTON))
        return hint

    def _on_glass(self) -> bool:
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, FloatingBar):
                return not widget.solid
            if widget.isWindow():
                return False
            widget = widget.parentWidget()
        return False


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
def _shadow_picture(radius: int, scale: int, rgba: int) -> QPixmap:
    """A blurred rounded square: the nine slices of every shadow with this corner radius.

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
    painter.drawRoundedRect(QRectF(SHADOW_BLUR, SHADOW_BLUR, side - 2 * SHADOW_BLUR, side - 2 * SHADOW_BLUR),
                            radius, radius)
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
        themes.changed.connect(self.update)

    def follow(self) -> None:
        bar = self._bar
        self.setGeometry(bar.geometry().adjusted(-SHADOW_BLUR, SHADOW_OFFSET - SHADOW_BLUR, SHADOW_BLUR,
                                                 SHADOW_OFFSET + SHADOW_BLUR))
        self.setVisible(bar.isVisibleTo(bar.parentWidget()))

    def _composed(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        radius = round(self._bar.radius())
        key = (self.width(), self.height(), round(ratio * 100), radius, themes.current.shadow.rgba())
        if key != self._key or self._picture is None:
            picture = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
            picture.setDevicePixelRatio(ratio)
            picture.fill(Qt.GlobalColor.transparent)
            painter = QPainter(picture)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            _nine_slice(painter, self.rect(), _shadow_picture(radius, key[2], key[4]), SHADOW_BLUR + radius)
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
    goes, so a bar that stays costs nothing more on every frame of a scroll.
    """

    def __init__(self, widget: QWidget, ms: int = FADE_MS):
        super().__init__(widget)
        self._widget = widget
        self._showing = True
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(ms)
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
        effect = self._widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._widget)
            self._widget.setGraphicsEffect(effect)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
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
        level = luminance(behind)
        own = themes.current.name
        if own == "dark":
            if self.tone != "light" and level > TONE_LIGHT_OVER:
                return "light"
            if self.tone == "light" and level < TONE_DARK_UNDER:
                return ""
        else:
            if self.tone != "dark" and level < TONE_DARK_IN_LIGHT:
                return "dark"
            if self.tone == "dark" and level > TONE_LIGHT_IN_LIGHT:
                return ""
        return self.tone

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

    def radius(self) -> float:
        return (self.height() - 1) / 2 if self.height() <= CAPSULE_MAX else GLASS_RADIUS

    def surface(self) -> QColor:
        """The colour coloured icons stand on: the glass over the part of what is behind it hardest to read on
        (the brightest under light text, the darkest under dark text), or the bar itself when it is solid."""
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
        if self._solid or source is None or not glass.enabled or not self.isVisible() or not source.isVisible():
            self._backdrop = self._frosted = None
            return
        area = self.backdrop_area().intersected(source.rect())
        if area.isEmpty():
            self._backdrop = self._frosted = None
            self.update()
            return
        blurred = _softened(source.grab(area).toImage(), area.size())
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
        self._flat = _over(t.glass_base, self.tint_alpha, self._mean)

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

    def _on_theme(self, *_) -> None:
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
        return super().event(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if (shadow := self._cast_shadow()) is not None:
            shadow.follow()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
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

    # Painting -------------------------------------------------------------------

    def paintEvent(self, event) -> None:
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
        radius = rect.height() / 2 if self.height() <= CAPSULE_MAX else GLASS_RADIUS * ratio
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        textured = False
        if self._solid:
            painter.setBrush(QColor(t.raised))
        elif self._backdrop is not None and self._source is not None:
            origin = self._origin()
            textured = self.covers(origin)
            if textured:
                painter.setBrush(self._backdrop_brush(ratio, origin))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            else:  # the content jumped past the picture: its mean colour until the new sample, moments away
                painter.setBrush(self._flat)
        else:
            painter.setBrush(t.glass_solid)
        painter.drawPath(shape)
        painter.drawPixmap(rect.topLeft(), self._edges(rect.size().toSize(), radius, ratio, textured))

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

    def _edges(self, size: QSize, radius: float, ratio: float, grain: bool) -> QPixmap:
        """The faint grain, the line of light inside the top edge and the fine edge all round, in device pixels:
        put together once per size and theme. The edge and the light are each a whole number of device
        pixels wide (one at 100% and 125%, two from 150%), and their straight runs fill whole pixels."""
        t = self.theme()
        width = max(1, int(ratio + 0.5))
        colours = (t.raised, t.border) if self._solid else (t.glass_highlight.rgba(), t.glass_border.rgba())
        key = (size.width(), size.height(), radius, width, grain, self._solid, colours, glass.style)
        if self._overlay is not None and self._overlay[0] == key:
            return self._overlay[1]
        picture = QPixmap(size)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, size.width(), size.height())
        if grain:
            shape = QPainterPath()
            shape.addRoundedRect(rect, radius, radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(_noise_tile()))
            painter.drawPath(shape)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if not self._solid:
            # Light along the top edge, fading down the corners: the second ring of pixels in. The Clear glass
            # catches some along the bottom too, as the rim of a lens does.
            light = QLinearGradient(QPointF(0, width), QPointF(0, rect.height() - width))
            fade = min(0.5, (radius + width) / max(rect.height(), 1.0))
            clear = QColor(255, 255, 255, 0)
            light.setColorAt(0.0, t.glass_highlight)
            light.setColorAt(fade, clear)
            if glass.style == "clear":
                rim = QColor(t.glass_highlight)
                rim.setAlpha(rim.alpha() // 2)
                light.setColorAt(1.0 - fade, clear)
                light.setColorAt(1.0, rim)
            else:
                light.setColorAt(1.0, clear)
            inset = width * 1.5
            painter.setPen(QPen(QBrush(light), width))
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(radius - inset, 0),
                                    max(radius - inset, 0))
        inset = width / 2
        painter.setPen(QPen(QColor(t.border) if self._solid else t.glass_border, width))
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(radius - inset, 0),
                                max(radius - inset, 0))
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
        self._position = 1.0 if checked else 0.0  # knob: 0 left, 1 right
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.valueChanged.connect(self._move_knob)
        self.toggled.connect(self._on_toggled)
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _move_knob(self, value) -> None:
        self._position = float(value)
        self.update()

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
        running = self._animation.state() == QVariantAnimation.State.Running
        mix = self._position if running else (1.0 if self.isChecked() else 0.0)
        self._position = mix
        faded = 1.0 if self.isEnabled() else 0.45
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
        white = QColor("#ffffff")
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


# Keyboard focus ----------------------------------------------------------------------

PICK_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space, Qt.Key.Key_Select)
KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                    Qt.FocusReason.ShortcutFocusReason)


def keyboard_focus(widget: QWidget) -> bool:
    """Whether `widget` has the focus and the keyboard put it there: then, and only then, a ring shows."""
    return widget.hasFocus() and widget.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)


def focus_ring(painter: QPainter, rect: QRectF, radius: float) -> None:
    """The keyboard focus ring: two pixels of the accent just inside `rect`, the same in both themes."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(themes.current.accent), 2.0))
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
    if luminance(QColor(t.raised)) < 0.2:
        return (QColor(0, 0, 0, 56), _mixed(QColor(t.segment), QColor(t.text), 0.10),
                _mixed(QColor(t.segment), QColor(t.text), 0.22))
    return qcolor(t.track), QColor(t.segment), QColor(t.border)


class _Segment(QAbstractButton):
    """One choice of a Segmented control. It takes clicks; the control takes the keys."""

    def __init__(self, text: str, control: Segmented):
        super().__init__(control)
        self._control = control
        self.setText(text)
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
        _, fill, edge = segment_colours(t)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        font = QFont(self.font())
        hot = self.underMouse() and self.isEnabled()
        if self.isChecked():
            painter.setPen(QPen(edge, 1.0))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            font.setWeight(QFont.Weight.DemiBold)
            color = QColor(t.text)
        else:
            if hot:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(t.hover))
                painter.drawRoundedRect(rect, 6, 6)
            color = QColor(t.text if hot else t.text_muted)
        if not self.isEnabled():
            color = QColor(t.text_muted)
            color.setAlpha(140)
        if self._control.ring_on(self):
            focus_ring(painter, rect, 6)
        painter.setFont(font)
        painter.setPen(color)
        text = QFontMetrics(font).elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 12)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

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
        self._group = QButtonGroup(self)
        self.buttons: list[_Segment] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        for index, (value, label) in enumerate(self.items):
            button = _Segment(label, self)
            if self.tips.get(value):
                button.setToolTip(self.tips[value])
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
            self.value = self.items[index][0]
            self._cursor = index
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

    def paintEvent(self, event) -> None:
        track, _, _ = segment_colours(themes.current)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)


def separator(vertical: bool = True) -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    if vertical:
        line.setFixedSize(1, 20)
    else:
        line.setFixedHeight(1)
    return line


def muted_label(text: str = "") -> QLabel:
    label = QLabel(native_keys(text))
    label.setObjectName("Muted")
    return label


COPIED_MS = 1500  # how long a copy button says it copied


def say_copied(button) -> None:
    """A copy button says "Copied" for a moment, then its own name again."""
    name = button.property("label_before_copied") or button.text()
    button.setProperty("label_before_copied", name)
    button.setText("Copied")

    def back() -> None:
        if shiboken6.isValid(button):
            button.setText(name)

    QTimer.singleShot(COPIED_MS, button, back)


def panel_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PanelTitle")
    return label
