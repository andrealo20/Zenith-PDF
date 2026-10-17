"""Each tool's own menu, in the style of Drawboard's.

At the top, the tool's icon in its colour and its name, then four buttons: lock
(stay on after each use, or go back to Select), remove from the toolbar, more, and
pin (keep the menu open while drawing). Under them the subject the tool's
annotations get, with a pencil to change it. Below, a preview painted the way the
page paints, then the tool's options in the order tools.OPTIONS gives. Every
change applies at once and the menu stays open.

StyleEditor is the part below the header. The selection bar's Style button shows
it for the selected annotations (SelectionMenu), with only the options the whole
selection has.
"""

from __future__ import annotations

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass, replace

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ..core import inking, textlayout
from ..core.i18n import tr
from ..core.platforms import WINDOWS, math_font, native_keys, ui_font
from ..core.annotations import (
    NOTE_SIZE,
    UNITS,
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
    Redaction,
    Shape,
    STAMPS,
    Tape,
    TextMarkup,
    TextStamp,
)
from ..icons import icon, readable
from ..settings import DEFAULTS
from ..theme import qcolor as token_color, themes
from .annotation_items import paint_annotation
from .help import explain_menu
from .tool_catalog import ALWAYS, BY_KEY, DUPLICABLE, base_of, instance_title
from .tools import (
    BORDER_TOOLS,
    LASER_SIZES,
    LOCK_DEFAULTS,
    LOCKABLE,
    OPTIONS,
    SETTING_OPTIONS,
    ToolSettings,
    ToolStyle,
    checked_fields,
)
from .widgets import (
    PICK_KEYS,
    POPUP_FLAGS,
    IconButton,
    Segmented,
    Switch,
    focus_ring,
    keyboard_focus,
    moved_cursor,
    ring_on_keyboard_focus,
)

MENU_WIDTH = 320
PAD = 16
BODY_WIDTH = MENU_WIDTH - 2 * PAD  # 288: the options' width
GUTTER = 12  # room for the body's scroll bar, inside the right padding
ROW_GAP = 12 if WINDOWS else 11  # between the options; other systems' fonts set taller lines
SECTION_GAP = 4  # between one open section's last option and the next section's heading, which has air of its own
SLIDER_ROW = 40  # a slider with its value box, and the tick values tucked under the slider
RADIUS = 14
SHADOW = (24, 16, 24, 32)  # transparent margin left, top, right, bottom: the shadow falls 8 px down
# A pinned menu lets clicks through to the page and the toolbar round it, but its window still takes
# those on its transparent margin: it keeps the margin narrower than GAP, and its shadow tight.
PINNED_SHADOW = (6, 4, 6, 8)
GAP = 8  # between the button and the menu
EDGE = 12  # kept clear of the window's sides
FLOOR = 16  # kept clear of the window's bottom
THROTTLE_MS = 30  # a dragged slider applies at most this often
SETTLE_MS = 150  # a selection changed by keys waits this long, so one change is one undo step
SLIDE_MS = 120  # a pinned menu sliding under the next tool
PREVIEW = QSize(BODY_WIDTH, 72)
GLYPH = 22  # the tool's icon before its name
TITLE_INDENT = 30  # where the name starts, and the subject under it

# Colours, as RGB floats. Each tool's defaults are in its own row, so they show as chosen.
COLOURS = [(0.0, 0.0, 0.0), (0.89, 0.18, 0.18), (1.0, 0.55, 0.0), (1.0, 0.86, 0.0), (0.18, 0.7, 0.27),
           (0.1, 0.45, 0.95), (0.48, 0.23, 0.88), (1.0, 1.0, 1.0)]
HIGHLIGHTER = [(1.0, 0.86, 0.0), (0.49, 0.89, 0.42), (0.24, 0.84, 1.0), (1.0, 0.44, 0.68), (1.0, 0.62, 0.1),
               (0.65, 0.48, 1.0), (1.0, 0.35, 0.35), (0.6, 0.6, 0.64)]
TAPE_COLOURS = [(0.98, 0.78, 0.36), (0.55, 0.86, 0.7), (0.53, 0.75, 0.98), (0.75, 0.65, 0.95), (0.98, 0.6, 0.7),
                (0.7, 0.7, 0.74)]
NOTE_COLOURS = [(1.0, 0.82, 0.2), (1.0, 0.62, 0.25), (0.45, 0.82, 0.45), (0.45, 0.7, 1.0), (1.0, 0.55, 0.7),
                (0.7, 0.55, 0.95)]
LASER_COLOURS = [(1.0, 0.15, 0.2), (0.2, 0.85, 0.35), (0.2, 0.55, 1.0), (1.0, 0.85, 0.1), (1.0, 1.0, 1.0)]
PALETTES = {"marker": HIGHLIGHTER, "highlight": HIGHLIGHTER, "tape": TAPE_COLOURS, "note": NOTE_COLOURS,
            "laser": LASER_COLOURS}
PALETTE = [  # the palette popover's twelve
    (0.0, 0.0, 0.0), (0.45, 0.45, 0.47), (1.0, 1.0, 1.0), (0.89, 0.18, 0.18),
    (1.0, 0.55, 0.0), (1.0, 0.86, 0.0), (0.2, 0.7, 0.3), (0.1, 0.65, 0.65),
    (0.13, 0.55, 0.95), (0.35, 0.3, 0.9), (0.75, 0.3, 0.85), (0.95, 0.4, 0.65),
]
MATCH = 0.15  # a colour this close to a swatch, on every channel, shows that swatch as chosen
RECENT = 8
NO_COLOUR_RED = "#e5322d"

ENDING_NAMES = {"None": "None", "Square": "Square", "Circle": "Circle", "Diamond": "Diamond",
                "OpenArrow": "Open arrow", "ClosedArrow": "Closed arrow", "Butt": "Bar",
                "ROpenArrow": "Reversed open arrow", "RClosedArrow": "Reversed closed arrow", "Slash": "Slash"}
DASH_NAMES = {"solid": "Solid", "dotted": "Dotted", "dashed": "Dashed", "dashdot": "Dash and dot"}
PATTERN_NAMES = {"solid": "Solid fill", "diagonal": "Hatched", "cross": "Cross-hatched", "dots": "Dotted fill"}
NOTE_ICON_GLYPHS = {"Comment": ("chat-centered-text", "Comment"), "Note": ("note", "Note"), "Help": ("question", "Help"),
                    "Key": ("key", "Key"), "Insert": ("z-note-insert", "Insert"), "Paragraph": ("z-paragraph", "Paragraph")}
CLEAN_GLYPHS = {"highlight": ("z-text-highlight", "Highlight"), "underline": ("text-underline", "Underline"),
                "squiggly": ("z-squiggle", "Squiggle"), "strikeout": ("text-strikethrough", "Strikethrough")}
MARKUP_STYLES = {"highlight": "Highlight", "underline": "Underline", "squiggly": "Squiggly", "strikeout": "StrikeOut"}
# Tools whose annotations get a subject; the others make none, so their menu has no subject row.
NO_SUBJECT = {"eraser", "laser", "snapshot", "cleaner", "select", "lasso", "hand", "bookmark"}
# Options that open a collapsible section, by the section's title; consecutive ones share it.
GROUPED = {"dash": "Line style", "endings": "Line endings", "highlight": "Advanced", "ink_to_line": "Smart ink",
           "ink_to_shape": "Smart ink"}
OPEN_AT_FIRST = {"Line style": True, "Line endings": True, "Smart ink": True, "Advanced": False}
SELECTION_ORDER = ("color", "fill", "width", "opacity", "font_size", "text_format", "note_icon", "pattern", "dash",
                   "endings", "highlight")
FONT_NAMES = {"Helvetica": "Sans serif", "Times": "Serif", "Courier": "Monospace"}
TEXT_STYLES = {"bold": ("text-bolder", "Bold"), "italic": ("text-italic", "Italic"),
               "underline": ("text-underline", "Underline")}
ALIGN_GLYPHS = {"left": ("text-align-left", "Align left"), "center": ("text-align-center", "Centre"),
                "right": ("text-align-right", "Align right")}

_memory: dict[str, object] = {"recent_colours": [], "tool_menu_sections": {}}  # when Settings has no such key
# The eyedropper: waits for a click on the open document and gives the colour there, or None. The window sets it.
colour_picker = None


def scale_text(measure: tuple) -> str:
    paper, paper_unit, real, real_unit = measure
    return f"{paper:g} {paper_unit} = {real:g} {real_unit}"


def _stored(prefs, key: str):
    """A value kept between sessions when Settings knows the key, else only while the app runs."""
    if prefs is not None and key in DEFAULTS:
        return prefs.get(key)
    return _memory[key]


def _store(prefs, key: str, value) -> None:
    if prefs is not None and key in DEFAULTS:
        prefs.set(key, value)
    else:
        _memory[key] = value


def nearest(colours, color) -> int:
    """Index of the swatch `color` matches, or -1."""
    if color is None:
        return -1
    best, index = MATCH, -1
    for i, swatch in enumerate(colours):
        difference = max(abs(a - b) for a, b in zip(swatch, color))
        if difference <= best:
            best, index = difference, i
    return index


def hex_of(color) -> str:
    return QColor.fromRgbF(*color).name().upper()


def settle_layouts(root: QWidget) -> None:
    """Bring every layout under `root` up to date now, innermost first.

    Qt does it on its next pass through the event loop: too late to measure a menu whose
    options were just rebuilt, or a section that just opened.
    """
    def depth(widget: QWidget) -> int:
        count = 0
        while widget is not None and widget is not root:
            widget = widget.parentWidget()
            count += 1
        return count

    holders = [w for w in root.findChildren(QWidget) if w.layout() is not None]
    for widget in sorted(holders, key=depth, reverse=True) + [root]:
        layout = widget.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()


def forward_wheel(target: QWidget, event) -> None:
    """Hand a wheel event on to `target`, then up its parents until one takes it, as Qt does with a real one."""
    point = event.globalPosition()
    widget = target
    while widget is not None and isValid(widget):
        wheel = QWheelEvent(QPointF(widget.mapFromGlobal(point.toPoint())), point, event.pixelDelta(),
                            event.angleDelta(), event.buttons(), event.modifiers(), event.phase(), event.inverted())
        QApplication.sendEvent(widget, wheel)
        if wheel.isAccepted() or widget.isWindow():
            return
        widget = widget.parentWidget()


def forward_press(target: QWidget, event) -> None:
    """Hand a mouse press on to `target`; Qt passes it up the parents if `target` lets it go."""
    point = event.globalPosition()
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(target.mapFromGlobal(point.toPoint())), point,
                        event.button(), event.buttons(), event.modifiers())
    QApplication.sendEvent(target, press)


# Slider scales ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    """How a slider's positions map to values: in straight steps, or piecewise between stops.

    Piecewise stops give thin lines room at the start of the track: 1 to 2 pt takes as
    much of it as 10 to 50 pt.
    """

    stops: tuple[float, ...]
    ticks: tuple[float, ...] = ()
    steps: int = 100  # positions between two stops
    step: float = 0.0  # values are rounded to this; 0: finer at the thin end

    @property
    def maximum(self) -> int:
        return self.steps * (len(self.stops) - 1)

    @property
    def low(self) -> float:
        return self.stops[0]

    @property
    def high(self) -> float:
        return self.stops[-1]

    def position(self, value: float) -> int:
        stops = self.stops
        if value <= stops[0]:
            return 0
        for i in range(len(stops) - 1):
            low, high = stops[i], stops[i + 1]
            if value <= high:
                return round((i + (value - low) / (high - low)) * self.steps)
        return self.maximum

    def value(self, position: int) -> float:
        i, part = divmod(min(max(position, 0), self.maximum), self.steps)
        if i >= len(self.stops) - 1:
            return self.rounded(self.stops[-1])
        low, high = self.stops[i], self.stops[i + 1]
        return self.rounded(low + (high - low) * part / self.steps)

    def rounded(self, value: float) -> float:
        step = self.step or (0.1 if value < 3 else 0.5 if value < 10 else 1.0)
        return round(round(value / step) * step, 2)


THICKNESS = Scale((0.5, 1, 2, 3, 6, 10, 50), ticks=(1, 2, 3, 6, 10, 50))
WIDE = Scale((2, 6, 10, 14, 20, 28, 40), ticks=(6, 10, 14, 20, 28, 40))
TAPE_WIDTHS = Scale((6, 10, 14, 18, 24, 32, 48), ticks=(10, 14, 18, 24, 32, 48))
ERASER_SIZES = Scale((1, 2, 3, 6, 10, 20, 50), ticks=(1, 3, 6, 10, 20, 50))
OPACITY = Scale((10, 100), ticks=(25, 50, 75), steps=90, step=1)
FADE = Scale((0.3, 3.0), ticks=(0.5, 1, 2, 3), steps=27, step=0.1)
NIB = Scale((0, 180), ticks=(0, 45, 90, 135, 180), steps=36, step=5)
SPEED = Scale((50, 300), ticks=(100, 200, 300), steps=25, step=10)
SIGNATURE = Scale((40, 600), ticks=(100, 200, 300, 400, 500), steps=56, step=10)
FONT_SIZES = (8.0, 10.0, 12.0, 14.0, 18.0, 24.0)
FONT_PRESETS = (8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 16.0, 18.0, 20.0, 24.0, 28.0, 36.0, 48.0, 72.0)


def width_scale(base: str, wide: bool = False) -> Scale:
    if base == "tape":
        return TAPE_WIDTHS
    if base == "eraser":
        return ERASER_SIZES
    return WIDE if wide or base == "marker" else THICKNESS


def _points(value: float) -> str:
    return f"{value:g} pt"


def _percent(value: float) -> str:
    return f"{value:g}%"


def _number(text: str) -> float | None:
    """The first number in what someone typed: "2.5pt", "2,5", "80 %"."""
    found = re.search(r"\d+(?:[.,]\d*)?|[.,]\d+", text)
    if found is None:
        return None
    return float(found.group().replace(",", "."))


# Style sheet and shadow ----------------------------------------------------------------------


def menu_style(t) -> str:
    """The menu's own rules, on top of the app's style sheet."""
    accent = QColor(t.accent)
    danger = QColor(t.danger)
    return f"""
    #ToolMenuPanel {{ background: transparent; }}
    #ToolMenuPanel QLabel {{ background: transparent; }}
    #MenuTitle {{ font-size: 12pt; font-weight: 600; }}
    #MenuSection {{ font-size: 10pt; font-weight: 600; }}
    #MenuLabel {{ font-size: 10pt; }}
    #MenuCaption {{ color: {t.text_muted}; font-size: 9pt; }}
    #MenuCaption:disabled, #MenuLabel:disabled {{ color: {t.text_muted}; }}
    QToolButton#MenuButton, QToolButton#MenuRemove {{
        border: 2px solid transparent; border-radius: 8px; padding: 0; background: transparent;
    }}
    QToolButton#MenuButton:hover {{ background: {t.hover}; }}
    QToolButton#MenuButton:pressed {{ background: {t.pressed}; }}
    QToolButton#MenuButton:checked {{ background: {t.selection}; }}
    QToolButton#MenuRemove:hover {{ background: rgba({danger.red()}, {danger.green()}, {danger.blue()}, 34); }}
    QToolButton#MenuPencil {{ border: 2px solid transparent; border-radius: 6px; padding: 0; background: transparent; }}
    QToolButton#MenuPencil:hover {{ background: {t.hover}; }}
    QToolButton#MenuButton:focus, QToolButton#MenuRemove:focus, QToolButton#MenuPencil:focus {{
        border-color: {t.accent};
    }}
    QLineEdit#SubjectEdit {{ background: {t.field}; border-radius: 6px; padding: 2px 6px; }}
    QComboBox#ValueBox {{ background: {t.field}; border-radius: 8px; padding: 0 2px 0 8px; min-height: 30px; }}
    QComboBox#ValueBox::drop-down {{ width: 18px; }}
    QPushButton#MenuButtonWide {{ background: {t.field}; border-radius: 8px; padding: 0 12px; min-height: 30px;
                                  text-align: left; }}
    QPushButton#MenuButtonWide:focus {{ border: 2px solid {t.accent}; padding: 0 11px; }}
    QPushButton#Primary:focus {{ border: 2px solid {t.text}; padding: 6px 15px 5px 15px; }}  /* the accent is its fill */
    QSlider {{ background: transparent; min-height: 22px; }}
    QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {t.border}; }}
    QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {t.accent}; }}
    QSlider::handle:horizontal {{
        width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
        background: #ffffff; border: 1px solid rgba(0, 0, 0, 60);
    }}
    QSlider::handle:horizontal:hover {{ border: 1px solid rgba({accent.red()}, {accent.green()}, {accent.blue()}, 200); }}
    QSlider[ring="true"]::handle:horizontal {{ width: 14px; height: 14px; border: 2px solid {t.accent}; }}
    QScrollArea#MenuBody, QWidget#MenuBodyInner {{ background: transparent; border: none; }}
    QScrollArea#MenuBody QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0 2px 2px; }}
    QScrollArea#MenuBody QScrollBar::handle:vertical {{
        background: {t.scrollbar}; border-radius: 3px; min-height: 32px; margin: 0;
    }}
    QScrollArea#MenuBody QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
    QScrollArea#MenuBody QScrollBar::add-line, QScrollArea#MenuBody QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollArea#MenuBody QScrollBar::add-page, QScrollArea#MenuBody QScrollBar::sub-page {{ background: transparent; }}
    """


def _shadow_image(size: QSize, ratio: float, color: QColor, margins: tuple = SHADOW) -> QImage:
    """A soft shadow under a rounded panel of `size`: drawn sharp, then blurred by shrinking and growing.

    With the narrow margins of a pinned menu the shadow is tighter and falls less far.
    """
    left, top, right, bottom = margins
    width, height = size.width() + left + right, size.height() + top + bottom
    image = QImage(max(1, round(width * ratio)), max(1, round(height * ratio)), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    wide = margins == SHADOW
    drop = 8 if wide else 3
    painter.drawRoundedRect(QRectF(left + 2, top + drop, size.width() - 4, size.height() - 6 if wide else size.height()),
                            RADIUS, RADIUS)
    painter.end()
    full = (image.width(), image.height())
    for factor in ((6, 10) if wide else (3, 4)):
        small = image.scaled(max(1, full[0] // factor), max(1, full[1] // factor),
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        image = small.scaled(*full, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(ratio)
    return image


PINNED_FLAGS = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint


class Popover(QWidget):
    """A solid rounded panel in a window of its own, with a soft shadow. It pops up, or stays (pinned).

    Solid, not glass: it is full of small text and controls.
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, POPUP_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._pinned = False
        self._quiet = 0  # hides that are not a close: switching to pinned, a dialog running
        self._shadow: tuple | None = None
        self._slide: QPropertyAnimation | None = None
        self.closed_by_anchor = False  # the last close came from a press on the anchor_widget()
        self.closing_button = Qt.MouseButton.NoButton  # and the button of that press
        self.panel = QFrame(self)
        self.panel.setObjectName("ToolMenuPanel")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*SHADOW)
        self._outer.setSpacing(0)
        self._outer.addWidget(self.panel)
        themes.changed.connect(self._restyle)
        self._restyle()

    @property
    def pinned(self) -> bool:
        return self._pinned

    def margins(self) -> tuple[int, int, int, int]:
        """The transparent margin round the panel, where its shadow falls."""
        return PINNED_SHADOW if self._pinned else SHADOW

    def set_pinned(self, pinned: bool) -> None:
        """Pinned, the menu is a tool window: clicks on the page go through, and it stays open."""
        if pinned == self._pinned:
            return
        visible, panel_at = self.isVisible(), self.pos() + self.panel_origin()
        self._pinned = pinned
        self._outer.setContentsMargins(*self.margins())
        self._shadow = None
        self._quiet += 1
        try:
            self.setWindowFlags(PINNED_FLAGS if pinned else POPUP_FLAGS)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        finally:
            self._quiet -= 1
        self.adjustSize()
        if visible:
            self.move(panel_at - self.panel_origin())  # the panel stays put; only the margin round it changes
            self.show()

    def owner_window(self) -> QWidget | None:
        """The window the menu belongs to. Its dialogs go over that one, not over the menu, which hides meanwhile."""
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    @contextmanager
    def suspended(self):
        """Out of the way while a dialog runs: a popup would keep the mouse from reaching the dialog."""
        if not self.isVisible() or self._pinned:
            yield
            return
        position = self.pos()
        self._quiet += 1
        try:
            self.hide()
        finally:
            self._quiet -= 1
        try:
            yield
        finally:
            if isValid(self):
                self.move(position)
                self.show()
                self.raise_()

    def close_menu(self) -> None:
        self.hide()

    def anchor_widget(self) -> QWidget | None:
        """What opened the menu: a press on it closes the menu and goes no further, so it can't open it again."""
        return None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.closed_by_anchor = False
        self.closing_button = Qt.MouseButton.NoButton
        if not self._pinned:
            # Keys go to the menu itself (Esc closes it), not to the control that had them last
            # time: arrow keys would change a thickness nobody is looking at.
            self.setFocus(Qt.FocusReason.PopupFocusReason)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if not self._quiet and not event.spontaneous():
            self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_menu()
        else:
            super().keyPressEvent(event)

    def _on_anchor(self, point: QPoint) -> bool:
        anchor = self.anchor_widget()
        return (anchor is not None and isValid(anchor) and anchor.isVisible()
                and QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size()).contains(point))

    def mousePressEvent(self, event) -> None:
        """A press outside the panel closes an open menu, and goes on to what is under the pointer.

        A popup gets every press while it is open. One outside its window: QWidget closes it,
        and Windows hands the press on, so one click on another tool closes the menu and
        switches tools. One on the transparent margin round the panel: the menu closes
        and passes it on itself, as Qt only hands on presses outside the popup's window.
        One on the button that opened the menu only closes it: handed on, it would open
        the menu again (QMenu's noReplayFor does the same).
        """
        position, point = event.position().toPoint(), event.globalPosition().toPoint()
        if self._pinned or self.panel.geometry().contains(position):
            super().mousePressEvent(event)
            return
        on_anchor = self._on_anchor(point)
        if on_anchor:
            # QApplication clears this before every mouse event, so it only holds for this press.
            self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.closed_by_anchor = on_anchor
        self.closing_button = event.button()
        if not self.rect().contains(position):
            super().mousePressEvent(event)
            return
        event.accept()
        self.close_menu()
        target = None if on_anchor else self._under(point)
        if target is not None:
            forward_press(target, event)

    def _under(self, point: QPoint) -> QWidget | None:
        """The widget of the owner window under `point` (global), once no popup holds the mouse any more."""
        owner = self.owner_window()
        if owner is None or not isValid(owner) or not owner.isVisible() or QApplication.activePopupWidget() is not None:
            return None
        local = owner.mapFromGlobal(point)
        if not owner.rect().contains(local):
            return None
        return owner.childAt(local) or owner

    def wheelEvent(self, event) -> None:
        """The wheel outside the panel scrolls what is under the pointer; an open menu closes first.

        While a popup is open Qt drops wheel events meant for other windows, so the menu
        hands this one on itself, as a press outside goes on to what is under it.
        """
        if self.panel.geometry().contains(event.position().toPoint()):
            super().wheelEvent(event)
            return
        event.accept()
        if not self._pinned:
            self.close_menu()
        target = self._under(event.globalPosition().toPoint())
        if target is not None:
            forward_wheel(target, event)

    def _restyle(self, *_) -> None:
        self.setStyleSheet(menu_style(themes.current))
        self._shadow = None
        self.update()

    def panel_origin(self) -> QPoint:
        left, top, _, _ = self.margins()
        return QPoint(left, top)

    def place_panel(self, top_left: QPoint, animate: bool = False) -> None:
        """Move so the panel's top-left corner lands on `top_left` (global)."""
        target = top_left - self.panel_origin()
        if self._slide is not None:
            self._slide.stop()
        if animate and self.isVisible() and target != self.pos():
            self._slide = QPropertyAnimation(self, b"pos", self)
            self._slide.setDuration(SLIDE_MS)
            self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._slide.setEndValue(target)
            self._slide.start()
        else:
            self.move(target)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = QRectF(self.panel.geometry())
        ratio = self.devicePixelRatioF()
        margins = self.margins()
        key = (self.panel.size().width(), self.panel.size().height(), ratio, t.name, t.shadow.rgba(), margins)
        if self._shadow is None or self._shadow[0] != key:
            self._shadow = (key, _shadow_image(self.panel.size(), ratio, t.shadow, margins))
        painter.drawImage(QPointF(panel.left() - margins[0], panel.top() - margins[1]), self._shadow[1])
        shape = panel.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(QColor(t.raised))
        painter.drawRoundedRect(shape, RADIUS, RADIUS)


# Small parts ----------------------------------------------------------------------------------


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(tr(text))
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if wrap:
        label.setWordWrap(True)
    return label


class IconPushButton(QPushButton):
    """A push button with an icon that follows the theme: icons are drawn in its colours when made."""

    def __init__(self, icon_name: str, text: str, parent: QWidget | None = None):
        super().__init__(tr(text), parent)
        self._icon_name = icon_name
        self._refresh()
        themes.changed.connect(self._refresh)

    def _refresh(self, *_) -> None:
        self.setIcon(icon(self._icon_name))


def _font(widget: QWidget, points: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(widget.font())
    font.setPointSizeF(points)
    font.setWeight(weight)
    return font


class Swatches(QWidget):
    """Round colour buttons in rows. The chosen one has a ring in the accent colour.

    Tab reaches the row once; the arrow keys move a square focus ring over the colours,
    and Enter or Space picks the one in it.
    """

    chosen = Signal(object)  # an RGB tuple, or None for "no colour"
    SLOT = 32
    CIRCLE = 11.0  # radius

    def __init__(self, colours, columns: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.colours = [tuple(c) for c in colours]
        self.columns = columns
        self.current = None
        self.spread = False  # the slots share the whole width, rather than sitting 32 px apart from the left
        self._hover = -1
        self._cursor = 0  # the slot the keys point at
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        themes.changed.connect(self.update)

    # The slots: ("colour", value), and in a ColourRow also ("none", None) and ("more", None).
    def slots(self) -> list[tuple[str, object]]:
        return [("colour", c) for c in self.colours]

    def _columns(self) -> int:
        return self.columns or max(len(self.slots()), 1)

    def _rows(self) -> int:
        return max(1, math.ceil(len(self.slots()) / self._columns()))

    def sizeHint(self) -> QSize:
        return QSize(BODY_WIDTH, self._rows() * self.SLOT)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._columns() * 24, self._rows() * self.SLOT)

    def set_colours(self, colours) -> None:
        self.colours = [tuple(c) for c in colours]
        self.setFixedHeight(self._rows() * self.SLOT)
        self.update()

    def slot_width(self) -> float:
        share = self.width() / self._columns()
        return share if self.spread else min(float(self.SLOT), share)

    def slot_rect(self, index: int) -> QRectF:
        width = self.slot_width()
        row, column = divmod(index, self._columns())
        return QRectF(column * width, row * self.SLOT, width, self.SLOT)

    def _at(self, pos: QPointF) -> int:
        return next((i for i in range(len(self.slots())) if self.slot_rect(i).contains(pos)), -1)

    def selected_index(self) -> int:
        colours = [value for kind, value in self.slots() if kind == "colour"]
        return nearest(colours, self.current)

    def chosen_slot(self) -> int:
        """The slot that shows as chosen, or -1."""
        return self.selected_index()

    def set_value(self, color) -> None:
        self.current = None if color is None else tuple(color)
        self.update()

    def radius(self) -> float:
        return min(self.CIRCLE, self.slot_width() / 2 - 4)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chosen = self.selected_index()
        index_of_colour = 0
        for i, (kind, value) in enumerate(self.slots()):
            centre = self.slot_rect(i).center()
            selected = False
            if kind == "colour":
                selected = index_of_colour == chosen
                index_of_colour += 1
            self.paint_slot(painter, i, kind, value, centre, selected)
        if keyboard_focus(self) and 0 <= self._cursor < len(self.slots()):
            focus_ring(painter, self.slot_rect(self._cursor), 8)

    def paint_slot(self, painter: QPainter, index: int, kind: str, value, centre: QPointF, selected: bool) -> None:
        t = themes.current
        radius = self.radius()
        if index == self._hover and not selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(token_color(t.hover))
            painter.drawEllipse(centre, radius + 3.5, radius + 3.5)
        if selected:
            painter.setPen(QPen(QColor(t.accent), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius + 3, radius + 3)
        painter.setPen(QPen(QColor(128, 128, 128, 110), 1.0))
        painter.setBrush(QColor.fromRgbF(*value))
        painter.drawEllipse(centre, radius, radius)

    def mouseMoveEvent(self, event) -> None:
        index = self._at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        index = self._at(event.position())
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.activate(index)

    def focusInEvent(self, event) -> None:
        chosen = self.chosen_slot()
        self._cursor = chosen if chosen >= 0 else 0
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:
        moved = moved_cursor(self._cursor, len(self.slots()), self._columns(), event.key())
        if moved is not None:
            self._cursor = moved
            self.update()
        elif event.key() in PICK_KEYS and 0 <= self._cursor < len(self.slots()):
            self.activate(self._cursor)
        else:
            super().keyPressEvent(event)

    def activate(self, index: int) -> None:
        kind, value = self.slots()[index]
        if kind == "colour":
            self.current = value
            self.update()
            self.chosen.emit(value)

    def tip(self, index: int) -> str:
        kind, value = self.slots()[index]
        return hex_of(value) if kind == "colour" else ""

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self._at(QPointF(event.pos()))
            text = self.tip(index) if index >= 0 else ""
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class ColourRow(Swatches):
    """A tool's colours in one row: "no colour" first where it means something, "..." last for any colour."""

    def __init__(self, colours, none: bool = False, prefs=None, parent: QWidget | None = None):
        super().__init__(colours, 0, parent)
        self.none = none  # the slot is there
        self.none_allowed = True  # and can be picked
        self.none_tip = "No colour"
        self.prefs = prefs
        self.host = None  # what to keep out of the way while the colour dialog runs
        self.popover: PalettePopover | None = None
        self.setFixedHeight(self.SLOT)

    def slots(self) -> list[tuple[str, object]]:
        return ([("none", None)] if self.none else []) + [("colour", c) for c in self.colours] + [("more", None)]

    def set_none(self, shown: bool, allowed: bool = True, tip: str = "No colour") -> None:
        self.none, self.none_allowed, self.none_tip = shown, allowed, tip
        self.update()

    def is_custom(self) -> bool:
        return self.current is not None and self.selected_index() < 0

    def chosen_slot(self) -> int:
        slots = self.slots()
        if self.current is None:
            return 0 if self.none else -1
        if self.is_custom():
            return len(slots) - 1  # "...", which shows the custom colour
        return self.selected_index() + (1 if self.none else 0)

    def paint_slot(self, painter, index, kind, value, centre, selected) -> None:
        t = themes.current
        radius = self.radius()
        if kind == "colour":
            super().paint_slot(painter, index, kind, value, centre, selected)
            return
        chosen = (kind == "none" and self.current is None) or (kind == "more" and self.is_custom())
        if index == self._hover and not chosen and (kind != "none" or self.none_allowed):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(token_color(t.hover))
            painter.drawEllipse(centre, radius + 3.5, radius + 3.5)
        if chosen:
            painter.setPen(QPen(QColor(t.accent), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius + 3, radius + 3)
        if kind == "none":
            offset = radius * 0.68
            if self.none_allowed:
                painter.setPen(QPen(QColor(128, 128, 128, 110), 1.0))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(centre, radius, radius)
                painter.setPen(QPen(QColor(NO_COLOUR_RED), 2.0))
            else:  # an empty outline: a faded white disc read as a grey colour on the dark theme
                painter.setPen(QPen(QColor(t.border), 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, radius, radius)
                painter.setPen(QPen(QColor(t.text_muted), 1.5))
            painter.drawLine(centre + QPointF(-offset, offset), centre + QPointF(offset, -offset))
        elif self.is_custom():
            painter.setPen(QPen(QColor(128, 128, 128, 110), 1.0))
            painter.setBrush(QColor.fromRgbF(*self.current))
            painter.drawEllipse(centre, radius, radius)
        else:
            painter.setPen(QPen(QColor(t.border), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius, radius)
            size = 16
            icon("dots-three").paint(painter, QRect(round(centre.x() - size / 2), round(centre.y() - size / 2), size, size))

    def activate(self, index: int) -> None:
        kind, value = self.slots()[index]
        if kind == "colour":
            super().activate(index)
        elif kind == "none":
            if self.none_allowed:
                self.current = None
                self.update()
                self.chosen.emit(None)
        else:
            self.open_palette(index)

    def tip(self, index: int) -> str:
        kind, _ = self.slots()[index]
        if kind == "none":
            return self.none_tip
        if kind == "more":
            return f"Custom colour {hex_of(self.current)}. More colours" if self.is_custom() else "More colours"
        return super().tip(index)

    def open_palette(self, index: int) -> None:
        if self.popover is not None and isValid(self.popover):
            self.popover.close_menu()
        self.popover = PalettePopover(self.current, self.prefs, self)
        self.popover.picked.connect(self._picked)
        self.popover.dialog_requested.connect(self._dialog)
        self.popover.dropper_requested.connect(self._dropper)
        rect = self.slot_rect(index)
        anchor = self.mapToGlobal(QPoint(round(rect.center().x()), round(rect.bottom()) + 6))
        self.popover.show_at(anchor)

    def _picked(self, color) -> None:
        remember_colour(self.prefs, color)
        self.current = tuple(color)
        self.update()
        self.chosen.emit(self.current)

    def _dropper(self) -> None:
        """The eyedropper: the menu steps aside, the next click on a page gives the colour there."""
        if colour_picker is None:
            return
        host = self.host if self.host is not None and isValid(self.host) else None
        with host.suspended() if host is not None else _nothing():
            color = colour_picker()
        if color is not None and isValid(self):
            self._picked(color)

    def _dialog(self) -> None:
        start = QColor.fromRgbF(*self.current) if self.current is not None else QColor("#e5322d")
        host = self.host if self.host is not None and isValid(self.host) else None
        with host.suspended() if host is not None else _nothing():
            parent = host.owner_window() if host is not None else self.window()
            color = QColorDialog.getColor(start, parent, "Choose a colour")
        if color.isValid() and isValid(self):
            self._picked((color.redF(), color.greenF(), color.blueF()))


@contextmanager
def _nothing():
    yield


def remember_colour(prefs, color) -> None:
    color = [round(c, 4) for c in color]
    recent = [c for c in _stored(prefs, "recent_colours") if isinstance(c, list) and c != color]
    _store(prefs, "recent_colours", ([color] + recent)[:RECENT])


def recent_colours(prefs) -> list[tuple]:
    colours = []
    for value in _stored(prefs, "recent_colours"):
        if isinstance(value, list) and len(value) == 3 and all(isinstance(c, (int, float)) for c in value):
            colours.append(tuple(min(max(float(c), 0.0), 1.0) for c in value))
    return colours[:RECENT]


class PalettePopover(Popover):
    """Any colour: twelve to pick from, the last ones used, a hex code, or the full colour dialog."""

    picked = Signal(object)
    dialog_requested = Signal()
    dropper_requested = Signal()

    def __init__(self, current, prefs=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.panel.setFixedWidth(256)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(PAD, 14, PAD, PAD)
        layout.setSpacing(8)
        layout.addWidget(_label("Colours", "MenuSection"))
        self.grid = Swatches(PALETTE, 6)
        self.grid.spread = True  # as wide as the hex field and More under it
        self.grid.setFixedHeight(2 * Swatches.SLOT)
        self.grid.set_value(current)
        self.grid.chosen.connect(self._pick)
        layout.addWidget(self.grid)
        recent = recent_colours(prefs)
        self.recent = Swatches(recent, RECENT)
        self.recent.set_value(current)
        self.recent.chosen.connect(self._pick)
        if recent:
            layout.addWidget(_label("Recent", "MenuCaption"))
            layout.addWidget(self.recent)
        else:
            self.recent.hide()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.hex = QLineEdit(hex_of(current) if current is not None else "")
        self.hex.setObjectName("SubjectEdit")
        self.hex.setPlaceholderText(tr("#RRGGBB"))
        self.hex.setMaxLength(7)
        self.hex.setFixedHeight(32)
        self.hex.returnPressed.connect(self._typed)
        more = QPushButton(tr("More…"))
        more.setFixedHeight(32)
        more.clicked.connect(self._more)
        row.addWidget(self.hex, 1)
        self.dropper = None
        if colour_picker is not None:
            self.dropper = QToolButton()
            self.dropper.setIcon(icon("eyedropper"))
            self.dropper.setIconSize(QSize(18, 18))
            self.dropper.setFixedSize(32, 32)
            self.dropper.setAutoRaise(True)
            self.dropper.setToolTip(tr("Pick a colour from the page"))
            self.dropper.clicked.connect(self._dropper)
            row.addWidget(self.dropper)
        row.addWidget(more)
        layout.addLayout(row)
        self.adjustSize()

    def show_at(self, anchor: QPoint) -> None:
        """Centred under `anchor` (global), kept on the screen."""
        self.adjustSize()
        width = self.panel.sizeHint().width()
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(anchor.x() - width // 2, area.left() + EDGE), area.right() - width - EDGE)
        y = min(anchor.y(), area.bottom() - self.panel.sizeHint().height() - EDGE)
        self.place_panel(QPoint(x, y))
        self.show()

    def close_menu(self) -> None:
        self.close()  # and deleted: a new one opens each time

    def _pick(self, color) -> None:
        self.close_menu()
        self.picked.emit(color)

    def _dropper(self) -> None:
        self.close_menu()
        self.dropper_requested.emit()

    def _typed(self) -> None:
        text = self.hex.text().strip()
        color = QColor(text if text.startswith("#") else f"#{text}")
        if color.isValid() and len(text.lstrip("#")) in (3, 6):
            self._pick((color.redF(), color.greenF(), color.blueF()))

    def _more(self) -> None:
        self.close_menu()
        self.dialog_requested.emit()


class OptionGrid(QWidget):
    """Choices as a grid of cells, each a drawn icon, a word, or both; one chosen, or several.

    Tab reaches the grid once; the arrow keys move a focus ring over the cells, and
    Enter or Space chooses the one in it (or, for several, turns it on or off).
    """

    chosen = Signal(object)  # the value, or for several the chosen values as a tuple

    def __init__(self, items, columns: int, multi: bool = False, cell: int = 32, parent: QWidget | None = None,
                 empty: bool = False):
        super().__init__(parent)
        self.items = list(items)  # (value, icon name or None, text or None, tip)
        self.columns = columns
        self.multi = multi
        self.empty = empty  # several: whether all may be off
        self.cell = cell
        self.selected: set = set()
        self._hover = -1
        self._cursor = 0  # the cell the keys point at
        rows = max(1, math.ceil(len(self.items) / columns))
        self.setFixedHeight(rows * cell + (rows - 1) * 6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(BODY_WIDTH, self.height())

    def set_value(self, value) -> None:
        self.selected = set(value) if self.multi else {value}
        self.update()

    def set_icons(self, names: dict) -> None:
        self.items = [(v, names.get(v, glyph), text, tip) for v, glyph, text, tip in self.items]
        self.update()

    def cell_rect(self, index: int) -> QRectF:
        width = (self.width() - 6 * (self.columns - 1)) / self.columns
        row, column = divmod(index, self.columns)
        return QRectF(column * (width + 6), row * (self.cell + 6), width, self.cell)

    def _at(self, pos: QPointF) -> int:
        return next((i for i in range(len(self.items)) if self.cell_rect(i).contains(pos)), -1)

    def choose(self, value) -> None:
        """What a click on the cell with `value` does."""
        if self.multi:
            chosen = set(self.selected) ^ {value}
            if not chosen and not self.empty:
                return  # one at least stays chosen
            self.selected = chosen
            self.update()
            self.chosen.emit(tuple(v for v, *_ in self.items if v in chosen))
        elif {value} != self.selected:
            self.selected = {value}
            self.update()
            self.chosen.emit(value)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(t.accent)
        for i, (value, glyph, text, _tip) in enumerate(self.items):
            rect = self.cell_rect(i)
            on = value in self.selected
            if on:
                fill = QColor(accent)
                fill.setAlpha(41)
                painter.setPen(QPen(accent, 2.0))
                painter.setBrush(fill)
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
            else:
                painter.setPen(QPen(QColor(t.border), 1.0))
                painter.setBrush(token_color(t.hover) if i == self._hover else Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
            color = t.accent_ink if on else t.text  # on the tinted fill: the ring keeps the accent itself
            font = _font(self, 9.5, QFont.Weight.DemiBold if on else QFont.Weight.Normal)
            metrics = QFontMetricsF(font)
            words = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - (40 if glyph else 10)) \
                if text else ""
            size = 20
            content = (size if glyph else 0) + (6 if glyph and words else 0) + (metrics.horizontalAdvance(words) if words else 0)
            x = rect.center().x() - content / 2
            if glyph:
                icon(glyph, color).paint(painter, QRect(round(x), round(rect.center().y() - size / 2), size, size))
                x += size + 6
            if words:
                painter.setFont(font)
                painter.setPen(QColor(color))
                painter.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, tr(words))
        self.paint_focus(painter, 8)

    def paint_focus(self, painter: QPainter, radius: float) -> None:
        """The keyboard's ring, inside the cell's own border, so it reads apart from the chosen look."""
        if keyboard_focus(self) and 0 <= self._cursor < len(self.items):
            focus_ring(painter, self.cell_rect(self._cursor).adjusted(3, 3, -3, -3), radius - 3)

    def mouseMoveEvent(self, event) -> None:
        index = self._at(event.position())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        index = self._at(event.position())
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.choose(self.items[index][0])

    def focusInEvent(self, event) -> None:
        self._cursor = next((i for i, (value, *_) in enumerate(self.items) if value in self.selected), 0)
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:
        moved = moved_cursor(self._cursor, len(self.items), self.columns, event.key())
        if moved is not None:
            self._cursor = moved
            self.update()
        elif event.key() in PICK_KEYS and 0 <= self._cursor < len(self.items):
            self.choose(self.items[self._cursor][0])
        else:
            super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self._at(QPointF(event.pos()))
            tip = self.items[index][3] if index >= 0 else ""
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class OptionCards(OptionGrid):
    """Big choices with a picture, a name and a line of explanation: the pen types."""

    HEIGHT = 84

    def __init__(self, items, parent: QWidget | None = None):
        # items: (value, icon name, title, caption)
        super().__init__([(v, glyph, title, caption) for v, glyph, title, caption in items], len(items),
                         cell=self.HEIGHT, parent=parent)
        self.setFixedHeight(self.HEIGHT)

    def cell_rect(self, index: int) -> QRectF:
        width = (self.width() - 8 * (self.columns - 1)) / self.columns
        return QRectF(index * (width + 8), 0, width, self.HEIGHT)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(t.accent)
        for i, (value, glyph, title, caption) in enumerate(self.items):
            rect = self.cell_rect(i)
            on = value in self.selected
            if on:
                fill = QColor(accent)
                fill.setAlpha(41)
                painter.setPen(QPen(accent, 2.0))
                painter.setBrush(fill)
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
            else:
                painter.setPen(QPen(QColor(t.border), 1.0))
                painter.setBrush(token_color(t.hover) if i == self._hover else Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
            icon(glyph, t.accent if on else t.text).paint(painter, QRect(round(rect.center().x() - 12), 10, 24, 24))
            font = _font(self, 9.5, QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(t.text))
            painter.drawText(QRectF(rect.left() + 4, 38, rect.width() - 8, 20), Qt.AlignmentFlag.AlignCenter,
                             QFontMetricsF(font).elidedText(tr(title), Qt.TextElideMode.ElideRight, rect.width() - 8))
            font = _font(self, 8.0)
            painter.setFont(font)
            painter.setPen(QColor(t.text_muted))
            painter.drawText(QRectF(rect.left() + 3, 57, rect.width() - 6, 18), Qt.AlignmentFlag.AlignCenter,
                             QFontMetricsF(font).elidedText(tr(caption), Qt.TextElideMode.ElideRight, rect.width() - 6))
        self.paint_focus(painter, 10)


class ValueBox(QComboBox):
    """An editable value with presets: "8 pt", "100%". Typing a number and Enter applies it."""

    picked = Signal(float)
    HEIGHT = 32

    def __init__(self, fmt, presets=(), limits: tuple[float, float] = (0.0, 1e9), parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ValueBox")
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setFixedSize(76, self.HEIGHT)
        self.fmt = fmt
        self.limits = limits
        for value in presets:
            self.addItem(fmt(value), float(value))
        self.activated.connect(self._chosen)
        self.lineEdit().editingFinished.connect(self._typed)
        self._value: float | None = None

    def set_value(self, value: float) -> None:
        self._value = value
        self.blockSignals(True)
        index = next((i for i in range(self.count()) if abs(self.itemData(i) - value) < 1e-6), -1)
        self.setCurrentIndex(index)
        self.setEditText(self.fmt(value))
        self.blockSignals(False)

    def _chosen(self, index: int) -> None:
        if index >= 0:
            self._apply(float(self.itemData(index)))

    def _typed(self) -> None:
        number = _number(self.currentText())
        if number is None:
            self.set_value(self._value if self._value is not None else 0.0)
        else:
            self._apply(number)

    def _apply(self, value: float) -> None:
        value = min(max(value, self.limits[0]), self.limits[1])
        changed = self._value is None or abs(value - self._value) > 1e-9
        self.set_value(value)
        if changed:
            self.picked.emit(value)


class Ticks(QWidget):
    """The values under a slider, each centred under its own place on the track."""

    HANDLE = 18  # the slider's handle, border included: its centre runs from HANDLE / 2 to the width less that
    HEIGHT = 15

    def __init__(self, scale: Scale, fmt=lambda v: f"{v:g}", parent: QWidget | None = None):
        super().__init__(parent)
        self.scale = scale
        self.fmt = fmt
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # it overlaps the slider a little
        themes.changed.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        font = _font(self, 8.5)
        painter.setFont(font)
        painter.setPen(QColor(themes.current.text_muted))
        metrics = QFontMetricsF(font)
        usable = self.width() - self.HANDLE
        for tick in self.scale.ticks:
            text = self.fmt(tick)
            width = metrics.horizontalAdvance(text) + 2
            x = self.HANDLE / 2 + self.scale.position(tick) / self.scale.maximum * usable
            left = min(max(x - width / 2, 0.0), self.width() - width)  # the end ones stay whole
            painter.drawText(QRectF(left, 0, width, self.HEIGHT), Qt.AlignmentFlag.AlignCenter, tr(text))


class SliderRow(QWidget):
    """A slider with its value in an editable box beside it, and tick values under it.

    The ticks sit right under the handle, beside the lower part of the box, rather than
    on a line of their own below both: SLIDER_ROW tall in all.

    `live`: a drag applies as it goes (at most every THROTTLE_MS). Otherwise only once the
    handle is let go, so editing a selection makes one undo step per drag.
    """

    committed = Signal(float)

    def __init__(self, scale: Scale, fmt, presets=(), limits: tuple[float, float] | None = None, live: bool = True,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.scale = scale
        self.fmt = fmt
        self.live = live
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, scale.maximum)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(max(1, scale.steps // 2))
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        ring_on_keyboard_focus(self.slider)
        self.box = ValueBox(fmt, presets, limits or (scale.low, scale.high))
        self.box.picked.connect(self._typed)
        row = QHBoxLayout(self)
        row.setSpacing(12)
        row.addWidget(self.slider, 1)
        row.addWidget(self.box)
        self.ticks = Ticks(scale, parent=self) if scale.ticks else None
        tall = SLIDER_ROW if self.ticks is not None else ValueBox.HEIGHT
        row.setContentsMargins(0, 0, 0, tall - ValueBox.HEIGHT)
        self.setFixedHeight(tall)
        self._value = scale.low
        self._sent: float | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._send)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(self._released)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.ticks is not None:  # the layout has placed the slider by now
            slider = self.slider.geometry()
            self.ticks.setGeometry(slider.x(), self.height() - Ticks.HEIGHT, slider.width(), Ticks.HEIGHT)

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        if self.slider.isSliderDown():
            return  # the user is dragging it
        self._value = self._sent = value
        self.slider.blockSignals(True)
        self.slider.setValue(self.scale.position(value))
        self.slider.blockSignals(False)
        self.box.set_value(value)

    def _moved(self, position: int) -> None:
        self._value = self.scale.value(position)
        self.box.set_value(self._value)
        if self.live:
            if not self._timer.isActive():
                self._timer.start(THROTTLE_MS)
        elif not self.slider.isSliderDown():
            self._timer.start(SETTLE_MS)  # keys or a click on the track: wait for the last step

    def _released(self) -> None:
        self._timer.stop()
        self._send()

    def _typed(self, value: float) -> None:
        self._timer.stop()
        self._value = value
        self.slider.blockSignals(True)
        self.slider.setValue(self.scale.position(value))
        self.slider.blockSignals(False)
        self._send()

    def _send(self) -> None:
        if self._sent is None or abs(self._value - self._sent) > 1e-9:
            self._sent = self._value
            self.committed.emit(self._value)


class SwitchRow(QWidget):
    """An on/off option: its name, a line saying what it does, and a switch on the right."""

    toggled = Signal(bool)

    def __init__(self, title: str, caption: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.title = _label(title, "MenuLabel")
        text.addWidget(self.title)
        self.caption = _label(caption, "MenuCaption", wrap=True)
        if not caption:
            self.caption.hide()  # never show() a widget with no parent yet: it would open as a window
        text.addWidget(self.caption)
        layout.addLayout(text, 1)
        self.switch = Switch()
        self.switch.setFixedSize(40, 22)
        self.switch.toggled.connect(self.toggled.emit)
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_checked(self, on: bool) -> None:
        self.switch.blockSignals(True)
        self.switch.setChecked(on)
        self.switch.blockSignals(False)
        self.switch.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.switch.toggle()  # the name works as the switch too


class _SectionHeader(QWidget):
    """A section's heading: its title and a caret. A click, or Enter or Space, opens or closes it.

    The first section of a menu draws the divider above the sections along its top edge.
    """

    clicked = Signal()
    HEIGHT = 32

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = title
        self.open = True
        self.divider = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        themes.changed.connect(self.update)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.divider:
            painter.fillRect(QRectF(0, 0, self.width(), 1), QColor(t.border))
        painter.setFont(_font(self, 10, QFont.Weight.DemiBold))
        painter.setPen(QColor(t.text))
        painter.drawText(QRectF(0, 0, self.width() - 24, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, tr(self.title))
        glyph = "caret-down" if self.open else "caret-right"
        icon(glyph, t.text_muted).paint(painter, QRect(self.width() - 16, (self.height() - 14) // 2, 14, 14))
        if keyboard_focus(self):  # round the caret: the title lines up with the other titles, so no room left of it
            focus_ring(painter, QRectF(self.width() - 19, (self.height() - 22) / 2, 19, 22), 6)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() in PICK_KEYS:
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class Section(QWidget):
    """Options under a heading that opens and closes."""

    toggled = Signal(bool)

    def __init__(self, title: str, open_: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  # the heading's own height leaves air under its title
        self.header = _SectionHeader(title)
        self.header.clicked.connect(self._toggle)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(ROW_GAP)
        layout.addWidget(self.header)
        layout.addWidget(self.content)
        self.set_open(open_)

    @property
    def title(self) -> str:
        return self.header.title

    def is_open(self) -> bool:
        return self.header.open

    def _toggle(self) -> None:
        self.set_open(not self.header.open, emit=True)

    def set_open(self, open_: bool, emit: bool = False) -> None:
        self.header.open = open_
        self.header.update()
        self.content.setVisible(open_)
        if emit:
            self.toggled.emit(open_)


class SignatureList(QWidget):
    """The saved signatures, each with a delete button that shows on hover, and New signature."""

    def __init__(self, editor: StyleEditor, parent: QWidget | None = None):
        super().__init__(parent)
        self.editor = editor
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.rows: list[QWidget] = []
        self.new = IconPushButton("plus", " New signature…")
        self.new.setObjectName("MenuButtonWide")
        self.new.clicked.connect(self._new)
        self.reload()

    def _saved(self) -> list[dict]:
        prefs = self.editor.prefs
        saved = prefs.get("signatures") if prefs is not None else []
        return [s for s in saved if isinstance(s, dict) and "png" in s and "name" in s]

    def reload(self) -> None:
        from .insert_dialogs import signature_icon

        while self._layout.count():
            widget = self._layout.takeAt(0).widget()
            if widget is not None and widget is not self.new:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.rows = []
        saved = self._saved()
        if not saved:
            empty = _label("No signatures yet. Make one, and it is kept for every document.", "MenuCaption", wrap=True)
            self._layout.addWidget(empty)
            empty.show()  # now, not on the next pass: the menu measures itself first
        for index, record in enumerate(saved):
            row = _SignatureRow(signature_icon(record), record["name"])
            row.remove.clicked.connect(lambda _=False, i=index: self._delete(i))
            self._layout.addWidget(row)
            row.show()
            self.rows.append(row)
        self._layout.addWidget(self.new)

    def _new(self) -> None:
        from .insert_dialogs import SignatureDialog

        prefs = self.editor.prefs
        if prefs is None:
            return
        with self.editor.suspended():
            dialog = SignatureDialog(self.editor.author, self.editor.dialog_parent())
            accepted = dialog.exec() == SignatureDialog.DialogCode.Accepted
            record = dialog.signature() if accepted else None
            dialog.deleteLater()
        if record is not None:
            prefs.set("signatures", self._saved() + [record])

    def _delete(self, index: int) -> None:
        from . import messages

        prefs = self.editor.prefs
        saved = self._saved()
        if prefs is None or not 0 <= index < len(saved):
            return
        with self.editor.suspended():
            sure = messages.confirm(self.editor.dialog_parent(), "Delete signature",
                                    f"Delete \"{saved[index]['name']}\"? Signatures already on pages stay.",
                                    action="Delete", danger=True)
        if sure:
            prefs.set("signatures", saved[:index] + saved[index + 1:])


class _SignatureRow(QFrame):
    """A saved signature. Its delete button shows under the pointer, or when Tab reaches the row,
    where the Delete key does the same."""

    def __init__(self, picture, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tr(f"{name}. Delete removes it from the saved signatures."))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        image = QLabel()
        image.setFixedSize(120, 40)
        image.setPixmap(picture.pixmap(QSize(120, 40)))
        image.setStyleSheet(f"background: {themes.current.paper}; border-radius: 6px;")
        layout.addWidget(image)
        layout.addWidget(_label(name, "MenuLabel"), 1)
        self.remove = IconButton("trash", "Delete this signature", icon_size=16)
        self.remove.setObjectName("MenuRemove")
        self.remove.setFixedSize(28, 28)
        self.remove.setVisible(False)
        layout.addWidget(self.remove)

    def _show_remove(self) -> None:
        self.remove.setVisible(self.underMouse() or self.hasFocus())

    def enterEvent(self, event) -> None:
        self.remove.setVisible(True)

    def leaveEvent(self, event) -> None:
        self._show_remove()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._show_remove()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._show_remove()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if keyboard_focus(self):
            painter = QPainter(self)
            focus_ring(painter, QRectF(self.rect()), 8)


# The preview ------------------------------------------------------------------------------------

TEXT_GREY = QColor(213, 214, 220)
INK = QColor(28, 29, 33)
SAMPLE = "Mark up this text"
REDACT_MARKED = "Mark up"  # the words the Redaction preview marks; the rest shows filled in
PREVIEWED = {"pen", "marker", "eraser", "tape", "laser", "line", "polyline", "arrow", "rectangle", "ellipse", "cloud",
             "polygon", "highlight", "underline", "squiggly", "strikeout", "text", "callout", "redact", "distance",
             "perimeter", "area", "note", "hyperlink", "audio", "equation", "stamp", "angle", "volume"}
UNDER_TEXT = {"marker", "tape", "rectangle", "ellipse", "cloud", "polygon", "line", "arrow", "polyline"}


def _wave(x0: float, x1: float, middle: float, height: float, count: int = 40) -> list[tuple[float, float]]:
    return [(x0 + (x1 - x0) * i / count, middle - height * math.sin(i / count * 2 * math.pi) * (0.6 + 0.4 * i / count))
            for i in range(count + 1)]


def _sample_font() -> QFont:
    font = QFont(ui_font())
    font.setPixelSize(16)
    return font


def _sample_rect() -> QRectF:
    metrics = QFontMetricsF(_sample_font())
    width, height = metrics.horizontalAdvance(SAMPLE), metrics.height()
    return QRectF((PREVIEW.width() - width) / 2, (PREVIEW.height() - height) / 2, width, height)


def preview_annotation(base: str, style: ToolStyle, measure: tuple | None = None) -> Annotation | None:
    """The sample the preview paints for `base` drawn with `style`, in the preview's own points."""
    w, h = PREVIEW.width(), PREVIEW.height()
    s = style
    common = dict(page=0, color=s.color, opacity=s.opacity)
    if base in ("pen", "marker"):
        points = _wave(36, w - 36, h / 2, 13 if base == "pen" else 4)
        widths: tuple = ()
        if base == "pen" and s.pen == "pro":
            times = [0.0]
            for i in range(1, len(points)):  # slow at the ends, fast in the middle
                times.append(times[-1] + 0.004 + 0.03 * abs(math.cos(i / len(points) * math.pi)))
            widths = (tuple(inking.pro_widths(points, times, s.width)),)
        elif base == "pen" and s.pen == "calligraphy":
            widths = (tuple(inking.calligraphy_widths(points, s.width, s.nib_angle)),)
        width = max(widths[0]) if widths else s.width
        return Ink(strokes=(tuple(points),), width=min(width, 40.0), widths=widths, highlighter=s.highlight, **common)
    if base == "eraser":
        return Ink(page=0, strokes=(tuple(_wave(36, w - 36, h / 2, 10)),), width=2.5, color=(0.11, 0.11, 0.13))
    if base == "tape":
        return Tape(page=0, start=(48, h / 2), end=(w - 48, h / 2), width=min(s.width, 44.0), color=s.color,
                    revealed=s.tape_start == "peeled")
    if base in ("line", "arrow"):
        return Line(start=(44, h - 22), end=(w - 44, 22), width=min(s.width, 24.0), head_start=s.head_start,
                    head_end=s.head_end, dash=s.dash, highlight=s.highlight, **common)
    if base == "polyline":
        return Poly(points=((40, h - 18), (104, 20), (184, h - 18), (w - 40, 22)), closed=False,
                    width=min(s.width, 24.0), dash=s.dash, highlight=s.highlight, head_start=s.head_start,
                    head_end=s.head_end, **common)
    if base in ("rectangle", "ellipse", "cloud"):
        rect = (w / 2 - 50, 12, 100, h - 24) if base == "cloud" else (w / 2 - 44, 12, 88, h - 24)
        return Shape(shape="Circle" if base == "ellipse" else "Square", rect=rect, width=min(s.width, 16.0),
                     fill=s.fill, cloudy=base == "cloud", dash=s.dash, border=s.border, highlight=s.highlight,
                     pattern=s.pattern, **common)
    if base == "polygon":
        cx, cy, r = w / 2, h / 2 + 2, 28
        corners = tuple((cx + r * 1.25 * math.cos(math.radians(-90 + 72 * i)), cy + r * math.sin(math.radians(-90 + 72 * i)))
                        for i in range(5))
        return Poly(points=corners, closed=True, width=min(s.width, 16.0), fill=s.fill, dash=s.dash, border=s.border,
                    highlight=s.highlight, pattern=s.pattern, **common)
    if base in MARKUP_STYLES:
        rect = _sample_rect()
        return TextMarkup(style=MARKUP_STYLES[base], quads=((rect.x() - 2, rect.y(), rect.width() + 4, rect.height()),),
                          **common)
    if base in ("text", "callout"):
        size = min(s.font_size, 40.0)
        contents = "Text box" if base == "text" else "Look here"
        left = 24.0 if base == "text" else 100.0
        width = min(textlayout.text_width(contents, size, s.face()) + 12, w - left - 12)
        height = textlayout.fitted_height(contents, size, width, s.face())
        box = FreeText(rect=(left, (h - height) / 2, width, height), contents=contents, font_size=size, fill=s.fill,
                       **s.text_format(), **common)
        if base == "callout":
            box = replace(box, callout=((30.0, h - 14), (70.0, h / 2)))
        return box
    if base == "redact":
        rect = _sample_rect()  # the mark covers whole words, "Mark up"
        marked = QFontMetricsF(_sample_font()).horizontalAdvance(REDACT_MARKED) + 6
        return Redaction(page=0, rects=((rect.x() - 3, rect.y(), marked, rect.height()),),
                         fill=s.fill or (0.0, 0.0, 0.0))
    if base == "angle":
        return Measurement(page=0, points=((w / 2 + 90, h - 16), (w / 2 - 70, h - 16), (w / 2 + 40, 12)),
                           mode="angle", width=min(s.width, 12.0), color=s.color)
    if base in ("distance", "perimeter", "area", "volume"):
        paper, paper_unit, real, real_unit = measure or (1.0, "cm", 1.0, "cm")
        points_per_paper = paper * UNITS.get(paper_unit, 1.0)
        scale = real / points_per_paper if points_per_paper > 0 else 1.0
        shapes = {"distance": ((44, h - 20), (w - 44, h - 20)),
                  "perimeter": ((44, h - 14), (104, 18), (184, h - 14), (w - 44, 18)),
                  "area": ((w / 2 - 70, 12), (w / 2 + 70, 12), (w / 2 + 70, h - 12), (w / 2 - 70, h - 12))}
        shapes["volume"] = shapes["area"]
        return Measurement(page=0, points=shapes[base], mode=base, scale=scale, unit=real_unit,
                           width=min(s.width, 12.0), color=s.color, depth=s.depth)
    if base == "note":
        return Note(page=0, position=(w / 2 - NOTE_SIZE / 2, h / 2 - NOTE_SIZE / 2), color=s.color, icon=s.note_icon)
    if base == "stamp":
        from .tools import stamp_words

        text, detail, name = stamp_words(s, "")
        width, height = textlayout.stamp_size(text, detail)
        scale = min(1.0, (h - 12) / height, (w - 24) / width)
        width, height = width * scale, height * scale
        return TextStamp(page=0, rect=((w - width) / 2, (h - height) / 2, width, height), text=text, detail=detail,
                         name=name, color=s.color, opacity=s.opacity)
    if base == "audio":
        return Attachment(page=0, position=(w / 2 - NOTE_SIZE / 2, h / 2 - NOTE_SIZE / 2), audio=True, color=s.color,
                          file_name="Audio note.wav")
    if base == "hyperlink":
        rect = _sample_rect()
        return Link(page=0, rect=(rect.x() - 4, rect.y() - 2, rect.width() + 8, rect.height() + 4),
                    border=1.0 if s.link_border else 0.0, color=s.color)
    return None


class ToolPreview(QWidget):
    """A sample of what the tool draws, on white like a page, painted by the page's own painter."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(PREVIEW)
        self.base = ""
        self.tool_style: ToolStyle | None = None  # not "style": that is QWidget's
        self.measure: tuple | None = None
        themes.changed.connect(self.update)

    @staticmethod
    def shows(base: str) -> bool:
        return base in PREVIEWED

    def set_style(self, base: str, style: ToolStyle, measure: tuple | None = None) -> None:
        self.base, self.tool_style, self.measure = base, style, measure
        self.update()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(frame, 10, 10)
        painter.fillPath(shape, QColor(t.paper))
        if self.tool_style is not None:
            painter.save()
            painter.setClipPath(shape)
            self._paint_sample(painter)
            painter.restore()
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)

    def _paint_sample(self, painter: QPainter) -> None:
        base, s = self.base, self.tool_style
        w, h = self.width(), self.height()
        if base in UNDER_TEXT and (base in ("marker", "tape") or s.highlight):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(TEXT_GREY)
            for y, length in ((h / 2 - 15, 0.82), (h / 2 - 3, 0.9), (h / 2 + 9, 0.7)):
                painter.drawRoundedRect(QRectF(28, y, (w - 56) * length, 6), 3, 3)
        if base in MARKUP_STYLES or base in ("redact", "hyperlink"):
            painter.setFont(_sample_font())
            painter.setPen(INK)
            painter.drawText(_sample_rect(), Qt.AlignmentFlag.AlignCenter, SAMPLE)
        if base == "laser":
            size = LASER_SIZES.get(s.laser_size, LASER_SIZES["medium"])
            path = QPainterPath(QPointF(*_wave(36, w - 36, h / 2, 12)[0]))
            for point in _wave(36, w - 36, h / 2, 12)[1:]:
                path.lineTo(QPointF(*point))
            glow = QColor.fromRgbF(*s.color)
            for factor, alpha in ((3.2, 50), (1.9, 110), (1.0, 255)):
                glow.setAlpha(alpha)
                pen = QPen(glow, size * factor)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(path)
            return
        if base == "equation":
            font = QFont(math_font())
            font.setPixelSize(max(8, round(min(s.font_size, 44.0) * 1.15)))
            font.setItalic(True)
            painter.setFont(font)
            painter.setPen(QColor.fromRgbF(*s.color))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, tr("a² + b² = c²"))
            return
        annotation = preview_annotation(base, s, self.measure)
        if annotation is None:
            return
        if base == "eraser":
            self._paint_eraser(painter, annotation)
            return
        if isinstance(annotation, (Note, Attachment)):
            painter.translate(w / 2, h / 2)
            painter.scale(1.8, 1.8)
            painter.translate(-w / 2, -h / 2)
        paint_annotation(painter, annotation)
        if base == "redact":  # what Apply redactions leaves: the area filled in, from the next word on
            rect = _sample_rect()
            start = rect.x() + QFontMetricsF(_sample_font()).horizontalAdvance(REDACT_MARKED + " ") - 1
            painter.fillRect(QRectF(start, rect.y(), rect.right() + 3 - start, rect.height()),
                             QColor.fromRgbF(*(s.fill or (0.0, 0.0, 0.0))))

    def _paint_eraser(self, painter: QPainter, stroke: Ink) -> None:
        s = self.tool_style
        w, h = self.width(), self.height()
        radius = min(max(s.width / 2, 4.0), h / 2 - 6)
        centre = QPointF(w / 2, h / 2)
        painter.save()
        if s.erase == "partial":  # only what the eraser passes over goes
            gap = QPainterPath()
            gap.addRect(QRectF(0, 0, w, h))
            hole = QPainterPath()
            hole.addRect(QRectF(centre.x() - radius, 0, 2 * radius, h))
            painter.setClipPath(gap.subtracted(hole), Qt.ClipOperation.IntersectClip)
            paint_annotation(painter, stroke)
        else:  # the whole stroke goes: shown faded
            painter.setOpacity(0.25)
            paint_annotation(painter, stroke)
        painter.restore()
        painter.setPen(QPen(QColor(90, 90, 100), 1.2))
        painter.setBrush(QColor(255, 255, 255, 150))
        painter.drawEllipse(centre, radius, radius)


# The editor ---------------------------------------------------------------------------------


class StyleEditor(QWidget):
    """A tool's options, or a selection's: what the menu shows under its header.

    set_options() builds the controls for some of tools.OPTIONS; set_style() shows
    another style in them without rebuilding. `changed` carries ToolStyle fields;
    options kept in Settings (select_new, wheel...) are written there directly.
    """

    changed = Signal(dict)
    scale_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MenuBodyInner")
        self.setFixedWidth(BODY_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(ROW_GAP)
        self.preview = ToolPreview()
        self.blocks: dict[str, QWidget] = {}  # option -> the widget holding its control
        self.sections: dict[str, Section] = {}
        self._loaders: list[tuple[str, object]] = []
        self._style = ToolStyle(color=(0.0, 0.0, 0.0))
        self._options: tuple[str, ...] = ()
        self._base = ""
        self._key = None  # what the controls were built for
        self._measure: tuple | None = None
        self._source: str | None = None  # the option sending a change: it shows the change already
        self.prefs = None
        self.host: Popover | None = None
        self.author = ""
        self.live = True
        self.wide = False
        self.rules: dict | None = None  # for a selection: {"border_none", "fill_none", "border_title"}
        self._watching = None

    # Building -------------------------------------------------------------

    def set_options(self, options, style: ToolStyle, wide: bool = False, measure: tuple | None = None, *, base: str = "",
                    prefs=None, live: bool = True, rules: dict | None = None, author: str = "",
                    preview: bool = True) -> None:
        options = tuple(o for o in options if o not in SETTING_OPTIONS or prefs is not None)
        key = (options, base, wide, live, preview, None if rules is None else tuple(sorted(rules.items())))
        self.prefs, self.author, self.live, self.wide, self.rules = prefs, author, live, wide, rules
        self._measure = measure
        if prefs is not self._watching:
            if self._watching is not None:
                self._watching.changed.disconnect(self._on_pref)
            if prefs is not None:
                prefs.changed.connect(self._on_pref)
            self._watching = prefs
        if key != self._key:
            self._key, self._options, self._base = key, options, base
            self._rebuild(options, base, preview)
        self.set_style(style)

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                if widget is not self.preview:
                    widget.deleteLater()
        self.blocks, self.sections, self._loaders = {}, {}, []

    def _add(self, widget: QWidget) -> None:
        """Add a row. Shown at once: a widget added to a visible one would only show on the next pass,
        and the menu measures itself before that."""
        self._layout.addWidget(widget)
        widget.show()

    def _rebuild(self, options: tuple[str, ...], base: str, preview: bool) -> None:
        self._clear()
        if preview and ToolPreview.shows(base):
            self._add(self.preview)
        section: Section | None = None
        sections: QVBoxLayout | None = None  # consecutive sections sit closer than the options above them
        for option in options:
            if option == "fill" and "color" in options:
                continue  # shares the colour row: Border | Fill
            widget = self._build(option, base)
            if widget is None:
                continue
            self.blocks[option] = widget
            group = GROUPED.get(option)
            if group is None:
                section = sections = None
                self._add(widget)
                continue
            if section is None or section.title != group:
                section = Section(group, self._section_open(base, group))
                section.toggled.connect(lambda on, title=group: self._remember_section(base, title, on))
                if sections is None:
                    holder = QWidget()
                    sections = QVBoxLayout(holder)
                    sections.setContentsMargins(0, 0, 0, 0)
                    sections.setSpacing(SECTION_GAP)
                    section.header.divider = not self.sections  # one line above the first sections only
                    self._add(holder)
                self.sections[group] = section
                sections.addWidget(section)
                section.show()
            section.content_layout.addWidget(widget)
            widget.show()
        if base == "hand" and self.prefs is not None:
            self._add(_label("Space or the middle mouse button pans with any tool.", "MenuCaption", True))

    def _section_open(self, base: str, title: str) -> bool:
        stored = _stored(self.prefs, "tool_menu_sections")
        value = stored.get(f"{base}:{title}") if isinstance(stored, dict) else None
        return value if isinstance(value, bool) else OPEN_AT_FIRST.get(title, True)

    def _remember_section(self, base: str, title: str, open_: bool) -> None:
        stored = _stored(self.prefs, "tool_menu_sections")
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored[f"{base}:{title}"] = open_
        _store(self.prefs, "tool_menu_sections", stored)
        if self.host is not None:
            self.host.refit()

    def _block(self, title: str | None, *widgets, caption: QLabel | None = None) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if title:
            layout.addWidget(_label(title, "MenuSection"))
        for widget in widgets:
            if isinstance(widget, QWidget):
                layout.addWidget(widget)
            else:
                layout.addLayout(widget)
        if caption is not None:
            layout.addWidget(caption)
        return block

    def _load(self, option: str, function) -> None:
        self._loaders.append((option, function))

    def _build(self, option: str, base: str) -> QWidget | None:
        build = getattr(self, f"_build_{option}", None)
        return build(base) if build is not None else None

    # Showing a style ----------------------------------------------------------

    def current_style(self) -> ToolStyle:
        return self._style

    def set_style(self, style: ToolStyle) -> None:
        self._style = style
        for option, load in self._loaders:
            if option != self._source:
                load(style)
        if self.preview.parent() is self:
            self.preview.set_style(self._base, style, self._measure)

    def set_measure(self, measure: tuple) -> None:
        self._measure = measure
        self.set_style(self._style)

    def _emit(self, option: str, **changes) -> None:
        clean = checked_fields(changes)
        if not clean:
            return
        self._style = replace(self._style, **clean)
        self._source = option
        try:
            self.changed.emit(clean)
        finally:
            self._source = None
        self.set_style(self._style)  # the options that depend on this one

    def _on_pref(self, key: str) -> None:
        if key in SETTING_OPTIONS.values() and isValid(self):
            self.set_style(self._style)

    @contextmanager
    def suspended(self):
        host = self.host if self.host is not None and isValid(self.host) else None
        with host.suspended() if host is not None else _nothing():
            yield

    def dialog_parent(self) -> QWidget:
        host = self.host if self.host is not None and isValid(self.host) else None
        return (host.owner_window() if host is not None else None) or self.window()

    # One builder per option ---------------------------------------------------

    def _border_rules(self, style: ToolStyle) -> tuple[bool, bool]:
        """Whether "no colour" may be picked for the border, and for the fill: a shape needs one of them."""
        if self.rules is not None:
            return self.rules.get("border_none", False), self.rules.get("fill_none", True)
        border_tool = self._base in BORDER_TOOLS
        return border_tool and style.fill is not None, (not border_tool) or style.border

    def _build_color(self, base: str) -> QWidget:
        palette = PALETTES.get(base, COLOURS)
        row = ColourRow(palette, prefs=self.prefs)
        row.host = self.host
        if "fill" not in self._options:
            row.chosen.connect(lambda c: self._emit("color", color=c))
            self._load("color", lambda s: row.set_value(s.color))
            title = {"audio": "Icon colour", "note": "Colour", "hyperlink": "Border colour",
                     "equation": "Colour"}.get(base, "Colour")
            return self._block(title, row)
        # Border | Fill (or Text | Fill): one row of colours, for whichever the switch shows.
        border_tool = base in BORDER_TOOLS or (self.rules or {}).get("border_title") == "Border"
        first = "Border" if border_tool else (self.rules or {}).get("border_title") or "Text"
        if base in ("text", "callout"):
            first = "Text"
        which = Segmented([("color", first), ("fill", "Fill")])
        state = {"target": "color"}

        def load(s: ToolStyle) -> None:
            border_none, fill_none = self._border_rules(s)
            if state["target"] == "color":
                row.set_none(border_tool, border_none, "No border" if border_none else
                             "A shape with no fill needs its border")
                row.set_value(s.color if s.border or not border_tool else None)
            else:
                row.set_none(True, fill_none, "No fill" if fill_none else "A shape with no border needs its fill")
                row.set_value(s.fill)

        def chosen(color) -> None:
            if state["target"] == "color":
                if color is None:
                    self._emit("color", border=False)
                elif border_tool:
                    self._emit("color", color=color, border=True)
                else:
                    self._emit("color", color=color)
            else:
                self._emit("color", fill=color)

        def switch(target) -> None:
            state["target"] = target
            load(self._style)

        which.chosen.connect(switch)
        row.chosen.connect(chosen)
        self._load("color", load)
        self.colour_switch = which
        return self._block(None, which, row)

    def _build_fill(self, base: str) -> QWidget:
        row = ColourRow(COLOURS, prefs=self.prefs)
        row.host = self.host
        row.chosen.connect(lambda c: self._emit("fill", fill=c))
        self._load("fill", lambda s: row.set_value(s.fill or (0.0, 0.0, 0.0)))
        return self._block("Fill after applying" if base == "redact" else "Fill", row)

    def _build_width(self, base: str) -> QWidget:
        scale = width_scale(base, self.wide)
        slider = SliderRow(scale, _points, scale.ticks, limits=(0.1, 100.0), live=self.live)
        slider.committed.connect(lambda v: self._emit("width", width=v))
        self._load("width", lambda s: slider.set_value(s.width))
        title = {"eraser": "Size", "tape": "Width"}.get(base, "Thickness")
        return self._block(title, slider)

    def _build_opacity(self, base: str) -> QWidget:
        slider = SliderRow(OPACITY, _percent, (25, 50, 75, 100), limits=(5, 100), live=self.live)
        slider.committed.connect(lambda v: self._emit("opacity", opacity=v / 100))
        self._load("opacity", lambda s: slider.set_value(round(s.opacity * 100)))
        return self._block("Opacity", slider)

    def _build_font_size(self, base: str) -> QWidget:
        chips = OptionGrid([(size, None, f"{size:g}", f"{size:g} pt") for size in FONT_SIZES], len(FONT_SIZES))
        box = ValueBox(_points, FONT_PRESETS, (1.0, 400.0))
        chips.chosen.connect(lambda v: self._emit("font_size", font_size=float(v)))
        box.picked.connect(lambda v: self._emit("font_size", font_size=v))

        def load(s: ToolStyle) -> None:
            chips.set_value(next((size for size in FONT_SIZES if abs(size - s.font_size) < 1e-6), None))
            box.set_value(s.font_size)

        self._load("font_size", load)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(chips, 1)
        row.addWidget(box)
        return self._block("Size" if base == "equation" else "Font size", row)

    def _build_text_format(self, base: str) -> QWidget:
        """The font, bold, italic and underline, and how the lines line up."""
        fonts = Segmented(list(FONT_NAMES.items()))
        styles = OptionGrid([(name, glyph, None, label) for name, (glyph, label) in TEXT_STYLES.items()], 3,
                            multi=True, cell=36, empty=True)
        aligns = OptionGrid([(name, glyph, None, label) for name, (glyph, label) in ALIGN_GLYPHS.items()], 3, cell=36)
        fonts.chosen.connect(lambda v: self._emit("text_format", font=v))
        styles.chosen.connect(lambda chosen: self._emit("text_format", **{name: name in chosen for name in TEXT_STYLES}))
        aligns.chosen.connect(lambda v: self._emit("text_format", align=v))

        def load(s: ToolStyle) -> None:
            fonts.set_value(s.font)
            styles.set_value(tuple(name for name in TEXT_STYLES if getattr(s, name)))
            aligns.set_value(s.align)

        self._load("text_format", load)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(styles)
        row.addStretch()
        row.addWidget(aligns)
        return self._block("Font", fonts, row)

    def _build_stamp_text(self, base: str) -> QWidget:
        """The ready stamps, and a field for words of one's own."""
        grid = OptionGrid([(name, None, tr(name).upper(), tr(name)) for name in STAMPS], 2, cell=30)
        own = QLineEdit()
        own.setObjectName("SubjectEdit")
        own.setFixedHeight(32)
        own.setPlaceholderText(tr("Your own words"))
        own.setToolTip(tr("{date}, {time}, {author} and {page} fill in when you stamp."))
        grid.chosen.connect(lambda v: (own.clear(), self._emit("stamp_text", stamp=v)))
        own.editingFinished.connect(lambda: own.text().strip() and self._emit("stamp_text", stamp=own.text()))

        def load(s: ToolStyle) -> None:
            grid.set_value(s.stamp if s.stamp in STAMPS else None)
            if s.stamp not in STAMPS and own.text() != s.stamp:
                own.setText(s.stamp)

        self._load("stamp_text", load)
        return self._block("Stamp", grid, own)

    def _build_stamp_detail(self, base: str) -> QWidget:
        row = SwitchRow("Name and date", "A line under the stamp: who stamped it, and when")
        row.toggled.connect(lambda on: self._emit("stamp_detail", stamp_detail=on))
        self._load("stamp_detail", lambda s: row.set_checked(s.stamp_detail))
        return row

    def _build_pattern(self, base: str) -> QWidget:
        grid = OptionGrid([(name, f"z-fill-{name}", None, label) for name, label in PATTERN_NAMES.items()], 4)
        grid.chosen.connect(lambda v: self._emit("pattern", pattern=v))
        block = self._block("Fill pattern", grid)

        def load(s: ToolStyle) -> None:
            grid.set_value(s.pattern)
            shown = s.fill is not None  # with no fill there is nothing to hatch
            if block.isHidden() == shown:
                block.setVisible(shown)
                if self.host is not None:
                    QTimer.singleShot(0, self.host.refit)

        self._load("pattern", load)
        return block

    def _build_dash(self, base: str) -> QWidget:
        grid = OptionGrid([(name, f"z-dash-{name}", None, label) for name, label in DASH_NAMES.items()], 4)
        grid.chosen.connect(lambda v: self._emit("dash", dash=v))
        self._load("dash", lambda s: grid.set_value(s.dash))
        return grid

    def _build_endings(self, base: str) -> QWidget:
        which = Segmented([("head_start", "Start"), ("head_end", "End")])
        which.set_value("head_end")
        grid = OptionGrid([(name, f"z-end-{name}", None, label) for name, label in ENDING_NAMES.items()], 5)
        state = {"end": "head_end"}

        def load(s: ToolStyle) -> None:
            grid.set_value(getattr(s, state["end"]))

        def switch(end) -> None:
            state["end"] = end
            side = "start" if end == "head_start" else "end"
            grid.set_icons({name: f"z-{side}-{name}" for name in ENDING_NAMES})
            load(self._style)

        which.chosen.connect(switch)
        grid.chosen.connect(lambda v: self._emit("endings", **{state["end"]: v}))
        self._load("endings", load)
        self.endings_switch = which
        return self._block(None, which, grid)

    def _build_highlight(self, base: str) -> QWidget:
        caption = "Darkens what is under it, like a highlighter: text shows through"
        if base in ("rectangle", "ellipse", "cloud", "polygon"):
            caption = "Border and fill darken what is under them: text shows through"
        row = SwitchRow("Highlight mode", caption)
        row.toggled.connect(lambda on: self._emit("highlight", highlight=on))
        self._load("highlight", lambda s: row.set_checked(s.highlight))
        return row

    def _build_ink_to_line(self, base: str) -> QWidget:
        row = SwitchRow("Ink to line", "A straight stroke becomes a line")
        row.toggled.connect(lambda on: self._emit("ink_to_line", ink_to_line=on))
        self._load("ink_to_line", lambda s: row.set_checked(s.ink_to_line))
        return row

    def _build_ink_to_shape(self, base: str) -> QWidget:
        row = SwitchRow("Ink to shape", "A drawn rectangle, ellipse or polygon becomes that shape")
        row.toggled.connect(lambda on: self._emit("ink_to_shape", ink_to_shape=on))
        self._load("ink_to_shape", lambda s: row.set_checked(s.ink_to_shape))
        return row

    def _build_pen_type(self, base: str) -> QWidget:
        cards = OptionCards([("basic", "z-pen-basic", "Basic", "Fixed width"),
                             ("pro", "z-pen-pro", "Pro", "Thins when fast"),
                             ("calligraphy", "z-pen-calligraphy", "Calligraphy", "Angled nib")])
        nib = SliderRow(NIB, lambda v: f"{v:g}°", (0, 30, 45, 60, 90, 135), limits=(0, 180), live=self.live)
        nib_block = self._block("Nib angle", nib)
        cards.chosen.connect(lambda v: self._emit("pen_type", pen=v))
        nib.committed.connect(lambda v: self._emit("pen_type", nib_angle=v))

        def load(s: ToolStyle) -> None:
            cards.set_value(s.pen)
            nib.set_value(s.nib_angle)
            visible = s.pen == "calligraphy"
            if nib_block.isVisibleTo(block) != visible:
                nib_block.setVisible(visible)
                if self.host is not None:
                    QTimer.singleShot(0, self.host.refit)

        block = self._block(None, cards, nib_block)
        self._load("pen_type", load)
        return block

    def _choice(self, option: str, title: str | None, items, captions: dict, field: str | None = None,
                prefs_key: str | None = None) -> QWidget:
        """A Segmented choice with a line under it explaining the chosen value."""
        segments = Segmented(items)
        caption = _label("", "MenuCaption", wrap=True)

        def load(s: ToolStyle) -> None:
            value = self.prefs.get(prefs_key) if prefs_key else getattr(s, field or option)
            segments.set_value(value)
            caption.setText(tr(captions.get(value, "")))

        if prefs_key:
            segments.chosen.connect(lambda v: (self.prefs.set(prefs_key, v), load(self._style)))
        else:
            segments.chosen.connect(lambda v: self._emit(option, **{field or option: v}))
        self._load(option, load)
        return self._block(title, segments, caption=caption if captions else None)

    def _build_erase(self, base: str) -> QWidget:
        return self._choice("erase", None, [("whole", "Whole strokes"), ("partial", "Partial")],
                            {"whole": "Removes every stroke it touches.",
                             "partial": "Removes only the part it passes over; the rest stays."})

    def _build_tape_start(self, base: str) -> QWidget:
        return self._choice("tape_start", "New tape starts", [("covered", "Covered"), ("peeled", "Peeled")],
                            {"covered": "Hides what is under it until you click it.",
                             "peeled": "Shows what is under it until you click it."})

    def _build_laser_size(self, base: str) -> QWidget:
        return self._choice("laser_size", "Size", [("thin", "Thin"), ("medium", "Medium"), ("thick", "Thick")], {})

    def _build_laser_fade(self, base: str) -> QWidget:
        slider = SliderRow(FADE, lambda v: f"{v:g} s", (0.5, 0.7, 1.0, 1.5, 2.0, 3.0), live=self.live)
        slider.committed.connect(lambda v: self._emit("laser_fade", laser_fade=v))
        self._load("laser_fade", lambda s: slider.set_value(s.laser_fade))
        return self._block("Fades after", slider)

    def _build_clean_kinds(self, base: str) -> QWidget:
        grid = OptionGrid([(kind, glyph, text, f"Remove {text.lower()} marks") for kind, (glyph, text)
                           in CLEAN_GLYPHS.items()], 2, multi=True, cell=36)
        grid.chosen.connect(lambda kinds: self._emit("clean_kinds", clean_kinds=kinds))
        self._load("clean_kinds", lambda s: grid.set_value(s.clean_kinds))
        return self._block("Removes", grid, caption=_label("One kind at least stays on.", "MenuCaption", True))

    def _build_note_icon(self, base: str) -> QWidget:
        grid = OptionGrid([(name, glyph, None, label) for name, (glyph, label) in NOTE_ICON_GLYPHS.items()], 6)
        grid.chosen.connect(lambda v: self._emit("note_icon", note_icon=v))
        self._load("note_icon", lambda s: grid.set_value(s.note_icon))
        return self._block("Icon", grid)

    def _build_image_size(self, base: str) -> QWidget:
        return self._choice("image_size", "Size on the page",
                            [("actual", "Actual"), ("quarter", "25%"), ("half", "50%"), ("full", "Full")],
                            {"actual": "The picture's own size, kept inside the page.",
                             "quarter": "A quarter of the page's width.", "half": "Half the page's width.",
                             "full": "As wide as the page."})

    def _build_link_border(self, base: str) -> QWidget:
        row = SwitchRow("Show a border", "Other viewers draw a frame round the link")
        row.toggled.connect(lambda on: self._emit("link_border", link_border=on))
        self._load("link_border", lambda s: row.set_checked(s.link_border))
        return row

    def _build_audio_source(self, base: str) -> QWidget:
        return self._choice("audio_source", "When you click the page",
                            [("ask", "Ask"), ("record", "Record"), ("file", "Attach file")],
                            {"ask": "Choose each time: record from the microphone, or attach a file.",
                             "record": "Starts the recorder at once.",
                             "file": "Asks for an audio file at once."})

    def _build_snapshot_scale(self, base: str) -> QWidget:
        return self._choice("snapshot_scale", "Resolution", [(1, "1×"), (2, "2×"), (3, "3×")],
                            {1: "72 dots per inch, the page's own size.", 2: "144 dots per inch.",
                             3: "216 dots per inch, sharp when enlarged."})

    def _build_snapshot_annotations(self, base: str) -> QWidget:
        row = SwitchRow("Include annotations", "Off copies the page alone")
        row.toggled.connect(lambda on: self._emit("snapshot_annotations", snapshot_annotations=on))
        self._load("snapshot_annotations", lambda s: row.set_checked(s.snapshot_annotations))
        return row

    def _build_lasso_pick(self, base: str) -> QWidget:
        return self._choice("lasso_pick", "Selects", [("touching", "Touching"), ("inside", "Inside"),
                                                     ("centre", "Centre")],
                            {"touching": "Everything the loop touches.", "inside": "Only what is wholly inside.",
                             "centre": "Everything with its middle inside the loop."})

    def _pref_switch(self, option: str, title: str, caption: str) -> QWidget:
        row = SwitchRow(title, caption)
        row.toggled.connect(lambda on: self.prefs.set(option, on))
        self._load(option, lambda s: row.set_checked(bool(self.prefs.get(option))))
        return row

    def _build_select_new(self, base: str) -> QWidget:
        return self._pref_switch("select_new", "Select new annotations", "Select each annotation as soon as it is made")

    def _build_snap_annotations(self, base: str) -> QWidget:
        return self._pref_switch("snap_annotations", "Snap to annotations",
                                 "Edges and centres line up with other annotations")

    def _build_snap_content(self, base: str) -> QWidget:
        return self._pref_switch("snap_content", "Snap to PDF content", "Line up with the page's text, pictures and edges")

    def _build_wheel(self, base: str) -> QWidget:
        return self._choice("wheel", "Mouse wheel", [("scroll", "Scrolls"), ("zoom", "Zooms")],
                            {"scroll": native_keys("Ctrl and the wheel zoom."),
                             "zoom": native_keys("Ctrl and the wheel scroll.")},
                            prefs_key="wheel")

    def _build_scroll_speed(self, base: str) -> QWidget:
        slider = SliderRow(SPEED, _percent, (50, 100, 150, 200, 300), live=self.live)
        slider.committed.connect(lambda v: self.prefs.set("scroll_speed", int(v)))
        self._load("scroll_speed", lambda s: slider.set_value(float(self.prefs.get("scroll_speed"))))
        return self._block("Scrolling speed", slider)

    def _build_signatures(self, base: str) -> QWidget:
        signatures = SignatureList(self)
        self.signature_list = signatures
        width = SliderRow(SIGNATURE, _points, (80, 120, 150, 200, 300), live=self.live)
        width.committed.connect(lambda v: self._emit("signatures", signature_width=v))

        def load(s: ToolStyle) -> None:
            width.set_value(s.signature_width)

        self._load("signatures", load)
        holder = QWidget()  # two blocks, a row apart, under one option
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(ROW_GAP)
        column.addWidget(self._block("Saved signatures", signatures))
        column.addWidget(self._block("Width of a new signature", width))
        return holder

    def _build_scale(self, base: str) -> QWidget:
        button = IconPushButton("ruler", "")
        button.setObjectName("MenuButtonWide")
        button.setToolTip(tr("Change the scale of the drawing"))
        button.clicked.connect(self.scale_requested.emit)
        self.scale_button = button
        self._load("scale", lambda s: button.setText(tr(f"  {scale_text(self._measure or (1.0, 'cm', 1.0, 'cm'))}")))
        return self._block("Scale of the drawing", button)


# Selections ------------------------------------------------------------------------------------


def _options_of(a: Annotation) -> set[str]:
    if isinstance(a, TextMarkup):
        return {"color", "opacity"}
    if isinstance(a, Shape):
        return {"color", "fill", "width", "opacity", "highlight", "pattern"} | (set() if a.cloudy else {"dash"})
    if isinstance(a, Line):
        return {"color", "width", "opacity", "dash", "endings", "highlight"}
    if isinstance(a, Poly):
        return {"color", "width", "opacity", "dash", "highlight"} | ({"fill", "pattern"} if a.closed else {"endings"})
    if isinstance(a, (Ink, Measurement, Tape)):
        return {"color", "width"} | ({"opacity"} if isinstance(a, Ink) else set())
    if isinstance(a, FreeText):
        return {"color", "font_size", "text_format", "fill"}
    if isinstance(a, ImageStamp):
        return {"opacity"}
    if isinstance(a, Note):
        return {"color", "note_icon"}
    if isinstance(a, (Attachment, Caret)):
        return {"color"}
    if isinstance(a, TextStamp):
        return {"color", "opacity"}
    if isinstance(a, Redaction):
        return {"fill"}
    return set()


def selection_options(annotations) -> tuple[str, ...]:
    """The options every one of `annotations` has, in the menu's order."""
    sets = [_options_of(a) for a in annotations]
    common = set.intersection(*sets) if sets else set()
    return tuple(option for option in SELECTION_ORDER if option in common)


def selection_style(a: Annotation) -> ToolStyle:
    """An annotation's look, as the editor shows a tool's."""
    border = getattr(a, "border", True)
    return ToolStyle(
        color=tuple(a.color),
        width=float(getattr(a, "width", 2.0)),
        opacity=a.opacity,
        font_size=float(getattr(a, "font_size", 12.0)),
        fill=getattr(a, "fill", None),
        border=border if isinstance(border, bool) else True,
        dash=getattr(a, "dash", "solid"),
        head_start=getattr(a, "head_start", "None"),
        head_end=getattr(a, "head_end", "None"),
        highlight=bool(getattr(a, "highlight", False)),
        pattern=getattr(a, "pattern", "solid"),
        note_icon=a.icon if isinstance(a, Note) else "Comment",
        **({"font": a.font, "bold": a.bold, "italic": a.italic, "underline": a.underline, "align": a.align}
           if isinstance(a, FreeText) else {}),
    )


def selection_changes(changes: dict) -> dict:
    """Editor changes as annotation fields: a note's icon is its own field."""
    return {"icon" if name == "note_icon" else name: value for name, value in changes.items()}


def selection_title(annotations) -> str:
    if len(annotations) == 1:
        return annotations[0].label
    labels = {a.label for a in annotations}
    return f"{len(annotations)} {next(iter(labels)).lower()}s" if len(labels) == 1 and \
        next(iter(labels))[-1] not in "sx" else f"{len(annotations)} annotations"


def _selection_palette(annotations) -> str:
    if all(isinstance(a, TextMarkup) and a.style == "Highlight" for a in annotations):
        return "highlight"
    if all(isinstance(a, Ink) and a.highlighter for a in annotations):
        return "marker"
    if all(isinstance(a, Tape) for a in annotations):
        return "tape"
    if all(isinstance(a, Note) for a in annotations):
        return "note"
    return ""


def _selection_rules(annotations) -> dict:
    shapes = all(isinstance(a, (Shape, Poly)) for a in annotations)
    texts = all(isinstance(a, FreeText) for a in annotations)
    return {
        "border_none": shapes and all(getattr(a, "fill", None) is not None for a in annotations),
        "fill_none": all(getattr(a, "border", True) is not False for a in annotations) or not shapes,
        "border_title": "Border" if shapes else "Text" if texts else "Colour",
    }


# The menus -------------------------------------------------------------------------------------


class _MenuButton(IconButton):
    """A 32 px square button of the menu's header. Tab reaches it, and Enter or Space presses it."""

    def __init__(self, icon_name: str, tip: str, help: str | None = None, checkable: bool = False,
                 danger: bool = False, size: int = 32, icon_size: int = 18):
        super().__init__(icon_name, tip, checkable=checkable, icon_size=icon_size, help=help)
        self.setObjectName("MenuRemove" if danger else "MenuButton")
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # not on a click: the ring is for the keyboard
        self._danger = danger

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.animateClick()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self._danger and self.isEnabled():
            self._color = themes.current.danger
            self._refresh()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._danger:
            self._color = None
            self._refresh()


class _Menu(Popover):
    """A header that stays put and a body that scrolls, placed under (or over) what opened it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.panel.setFixedWidth(MENU_WIDTH)
        self.column = QVBoxLayout(self.panel)
        self.column.setContentsMargins(PAD, 14, PAD - GUTTER, PAD)
        self.column.setSpacing(ROW_GAP)
        self.header = QWidget()
        self.header_layout = QVBoxLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, GUTTER, 0)
        self.header_layout.setSpacing(0)
        self.column.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName("MenuBodyInner")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)  # a hint under the options belongs to them
        self.editor = StyleEditor()
        self.editor.host = self
        self.body_layout.addWidget(self.editor)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("MenuBody")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFixedWidth(BODY_WIDTH + GUTTER)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Tab goes to the options, not to the frame round them
        self.scroll.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body.setFixedWidth(BODY_WIDTH)
        self.scroll.setWidget(self.body)
        self.scroll.viewport().setAutoFillBackground(False)
        self.column.addWidget(self.scroll)
        self._anchor_rect: QRect | None = None
        self._bounds: QRect | None = None
        self._beside: QRect | None = None
        self._above = False
        QApplication.instance().focusChanged.connect(self._focus_moved)

    def _focus_moved(self, _old, new) -> None:
        """Tab onto an option below the fold scrolls it into view."""
        if new is not None and isValid(self) and self.isVisible() and self.body.isAncestorOf(new):
            self.scroll.ensureWidgetVisible(new, 0, ROW_GAP)

    def chain_tabs(self) -> None:
        """Tab goes down the menu in the order it reads: the header's buttons, then the options one by one.

        Qt's own order follows when each control joined the window, and options are made
        again for every tool. A slider and its value box share a line: middles decide.
        """
        def place(widget: QWidget) -> tuple[int, int]:
            middle = widget.mapTo(self.panel, QPoint(0, widget.height() // 2))
            return middle.y(), middle.x()

        stops = [w for w in self.panel.findChildren(QWidget)
                 if w.focusPolicy() & Qt.FocusPolicy.TabFocus and w.focusProxy() is None
                 and w.isVisibleTo(self.panel) and w.isEnabled()]
        stops.sort(key=place)
        for first, second in zip(stops, stops[1:]):
            QWidget.setTabOrder(first, second)

    def focusNextPrevChild(self, next_: bool) -> bool:
        self.chain_tabs()  # in the layout as it is now, sections opened or closed
        return super().focusNextPrevChild(next_)

    def _body_height(self) -> int:
        settle_layouts(self.body)
        layout = self.body.layout()
        if layout.hasHeightForWidth():
            return layout.totalHeightForWidth(BODY_WIDTH)
        return layout.totalSizeHint().height()

    def _chrome(self) -> int:
        settle_layouts(self.header)
        header = self.header.sizeHint().height() if self.header.isVisibleTo(self.panel) else 0
        margins = self.column.contentsMargins()
        return margins.top() + margins.bottom() + header + (self.column.spacing() if header else 0)

    def place(self, anchor: QRect, bounds: QRect, animate: bool = False, prefer_above: bool = False,
              beside: QRect | None = None) -> None:
        """Hang the panel under `anchor` (global), centred, kept `EDGE` inside `bounds` (the window, global).

        With `beside` (a global rectangle, such as the selection), it goes to its right or left
        instead when there is room, so it covers nothing it edits.
        """
        self._anchor_rect, self._bounds, self._beside = QRect(anchor), QRect(bounds), beside
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        bounds = bounds.intersected(screen.availableGeometry()) if screen is not None else bounds
        body = self._body_height()
        # The body is exactly as tall as this tool's options need: in a scroll it would keep the last tool's height,
        # squeezing the options of a longer menu on top of each other, or leaving an empty tail after a shorter one.
        self.body.setFixedHeight(body)
        chrome = self._chrome()
        if beside is not None:
            visible = min(body, max(bounds.height() - EDGE - FLOOR - chrome, 120))
            height = chrome + visible
            right, left = beside.right() + 16, beside.left() - 16 - MENU_WIDTH
            x = right if right + MENU_WIDTH <= bounds.right() - EDGE else left if left >= bounds.left() + EDGE else None
            if x is not None:
                self.scroll.setFixedHeight(visible)
                self.panel.setFixedHeight(height)
                self.adjustSize()
                y = min(max(beside.top(), bounds.top() + EDGE), bounds.bottom() - FLOOR - height)
                self.place_panel(QPoint(x, y), animate=animate)
                return
        room_below = bounds.bottom() - FLOOR - (anchor.bottom() + GAP) - chrome
        room_above = anchor.top() - GAP - (bounds.top() + EDGE) - chrome
        above = (prefer_above and room_above >= min(body, 240)) or (room_below < min(body, 240) and room_above > room_below)
        room = max(room_above if above else room_below, 120)
        self.scroll.setFixedHeight(min(body, room))
        self.panel.setFixedHeight(chrome + self.scroll.height())
        self.adjustSize()
        width, height = MENU_WIDTH, self.panel.height()
        x = min(max(anchor.center().x() - width // 2, bounds.left() + EDGE), bounds.right() - EDGE - width)
        y = anchor.top() - GAP - height if above else anchor.bottom() + GAP
        self._above = above
        self.place_panel(QPoint(x, y), animate=animate)

    def refit(self) -> None:
        """Size and place again after the body grew or shrank (a section opened, an option appeared)."""
        if self._anchor_rect is not None and self.isVisible() and isValid(self):
            self.place(self._anchor_rect, self._bounds, prefer_above=self._above, beside=self._beside)

    @staticmethod
    def rects_of(anchor: QWidget) -> tuple[QRect, QRect]:
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        window = anchor.window()
        return QRect(top_left, anchor.size()), QRect(window.mapToGlobal(QPoint(0, 0)), window.size())


class ToolMenu(_Menu):
    """One tool's menu. With a toolbar, it also removes, moves, duplicates and pins the tool.

    The settings window shows it without a toolbar, to change any tool's style.
    """

    def __init__(self, settings: ToolSettings, toolbar=None, prefs=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.toolbar = toolbar
        self.prefs = prefs
        self.key = ""
        self.anchor_for = None  # key -> the widget to hang under; the toolbar's buttons come and go
        self._anchor: QWidget | None = None
        self._editing = False

        top = QHBoxLayout()
        top.setSpacing(4)
        top.setContentsMargins(0, 0, 0, 0)
        self.glyph = QLabel()
        self.glyph.setFixedSize(GLYPH, GLYPH)
        self.title = _label("", "MenuTitle")
        self.lock_button = _MenuButton("lock-simple", "Lock", help="menu_lock", checkable=True)
        self.lock_button.clicked.connect(self._toggle_lock)
        self.remove_button = _MenuButton("trash", "Remove from the toolbar", help="menu_remove", danger=True)
        self.remove_button.clicked.connect(self._remove)
        self.more_button = _MenuButton("dots-three", "More", help="menu_more")
        self.more_button.clicked.connect(self._show_more)
        self.pin_button = _MenuButton("push-pin", "Pin the menu open", help="menu_pin", checkable=True)
        self.pin_button.clicked.connect(self._toggle_pin)
        top.addWidget(self.glyph)
        top.addSpacing(TITLE_INDENT - GLYPH - top.spacing())
        top.addWidget(self.title, 1)
        for button in (self.lock_button, self.remove_button, self.more_button, self.pin_button):
            top.addWidget(button)
        self.header_layout.addLayout(top)

        self.subject_row = QWidget()
        row = QHBoxLayout(self.subject_row)
        row.setContentsMargins(TITLE_INDENT, 0, 0, 0)  # under the title, not under the icon
        row.setSpacing(2)
        self.subject_caption = _label("", "MenuCaption")
        self.subject_pencil = _MenuButton("pencil-simple", "Change the subject", help="menu_subject", size=22,
                                          icon_size=14)
        self.subject_pencil.setObjectName("MenuPencil")
        self.subject_pencil.clicked.connect(self.edit_subject)
        self.subject_edit = QLineEdit()
        self.subject_edit.setObjectName("SubjectEdit")
        self.subject_edit.setMaxLength(200)
        self.subject_edit.setFixedHeight(26)
        self.subject_edit.hide()
        self.subject_edit.editingFinished.connect(self._commit_subject)
        self.subject_edit.installEventFilter(self)
        row.addWidget(self.subject_caption)
        row.addWidget(self.subject_pencil)
        row.addWidget(self.subject_edit, 1)
        row.addStretch()
        self.header_layout.addWidget(self.subject_row)

        self.bookmark_button = QPushButton(tr(" Add a bookmark for this page"))
        self.bookmark_button.setObjectName("Primary")
        self.bookmark_button.setFixedHeight(32)
        self.bookmark_button.clicked.connect(self._add_bookmark)
        self.body_layout.addWidget(self.bookmark_button)
        self.hint = _label("", "MenuCaption", wrap=True)
        self.body_layout.addWidget(self.hint)

        self.editor.changed.connect(self._edited)
        self.editor.scale_requested.connect(self._ask_scale)
        settings.style_changed.connect(self._on_style_changed)
        settings.instances_changed.connect(self._on_instances)
        if prefs is not None:
            prefs.changed.connect(self._on_pref)
        themes.changed.connect(self._fill_header)

    # Opening ----------------------------------------------------------------

    def show_for(self, key: str, anchor: QWidget | None = None) -> None:
        """Open (or move) the menu for tool `key`, under `anchor`."""
        if self._editing:
            self._end_subject_edit()
        changed_key = key != self.key
        self.key = key
        if anchor is not None:
            self._anchor = anchor
        base = base_of(key)
        self.set_pinned(self._wants_pin())
        self._fill_header()
        self.editor.set_options(OPTIONS.get(base, ()), self.settings.style(key), measure=self.settings.measure,
                                base=base, prefs=self.prefs, author=self.settings.author)
        self.bookmark_button.setVisible(base == "bookmark" and self.toolbar is not None)
        self.hint.setText(native_keys(tr(HINTS.get(base, ""))))
        self.hint.setVisible(base in HINTS)
        was_visible = self.isVisible()
        self.reposition(animate=was_visible and changed_key)
        if not was_visible:
            self.show()
        self.raise_()

    def anchor_widget(self) -> QWidget | None:
        widget = self.anchor_for(self.key) if self.anchor_for is not None and self.key else self._anchor
        return widget if widget is not None and isValid(widget) else None

    def reposition(self, animate: bool = False) -> None:
        widget = self.anchor_for(self.key) if self.anchor_for is not None else self._anchor
        if widget is None or not isValid(widget) or not widget.isVisible():
            widget = self._anchor if self._anchor is not None and isValid(self._anchor) else None
        if widget is None or not widget.isVisible():
            return
        anchor, bounds = self.rects_of(widget)
        self.place(anchor, bounds, animate=animate)

    def _wants_pin(self) -> bool:
        if self.toolbar is None:
            return False
        if self.prefs is not None and "tool_menu_pinned" in DEFAULTS:
            return bool(self.prefs.get("tool_menu_pinned"))
        return self.pinned

    # The header -------------------------------------------------------------

    def _fill_header(self, *_) -> None:
        if not self.key:
            return
        t = themes.current
        key, base = self.key, base_of(self.key)
        spec = BY_KEY[base]
        style = self.settings.style(key)
        tinted = "color" in OPTIONS.get(base, ())
        color = readable(style.color, t.raised, t.text) if tinted else t.text
        self.glyph.setPixmap(icon(spec.icon, color).pixmap(QSize(20, 20), self.devicePixelRatioF()))
        self.title.setText(tr(instance_title(key)))
        self.bookmark_button.setIcon(icon("bookmark-simple", t.accent_text))
        on_toolbar = self.toolbar is not None and key in self.toolbar.keys()
        self.lock_button.setVisible(base in LOCKABLE)
        locked = self.settings.is_locked(key)
        self.lock_button.setChecked(locked)
        self.lock_button.set_icon_name("lock-simple" if locked else "lock-simple-open")
        back = "Select" if (self.settings.after_drawing == "select" or self.settings.select_new
                            or (self.toolbar is not None and "hand" not in self.toolbar.keys())) else "Pan"
        self.lock_button.setToolTip(tr("Locked: stays on after each use. Click to unlock." if locked else
                                    f"Unlocked: goes back to {back} after one use. Click to lock."))
        self.remove_button.setVisible(on_toolbar and key not in ALWAYS)
        self.more_button.setVisible(True)
        self.pin_button.setVisible(self.toolbar is not None)
        self.pin_button.setChecked(self.pinned)
        self.pin_button.set_icon_name("push-pin-fill" if self.pinned else "push-pin")
        self.pin_button.setToolTip(tr("Unpin: close the menu when you click elsewhere" if self.pinned else
                                   "Pin the menu open while you draw"))
        self.subject_row.setVisible(base not in NO_SUBJECT)
        subject = self.settings.subject(key)
        shown = QFontMetricsF(self.subject_caption.font()).elidedText(
            f"Tool subject: {subject}", Qt.TextElideMode.ElideRight, BODY_WIDTH - 60)
        self.subject_caption.setText(shown)
        self.subject_caption.setToolTip(subject if shown.endswith("…") else "")

    def _toggle_lock(self) -> None:
        base = base_of(self.key)
        locked = self.lock_button.isChecked()
        self.settings.set_locked(self.key, None if locked == LOCK_DEFAULTS.get(base) else locked)

    def _toggle_pin(self) -> None:
        pinned = self.pin_button.isChecked()
        if self.prefs is not None and "tool_menu_pinned" in DEFAULTS:
            self.prefs.set("tool_menu_pinned", pinned)  # _on_pref pins the menus
        self.set_pinned(pinned)
        self._fill_header()

    def _remove(self) -> None:
        key = self.key
        self.close_menu()
        if self.toolbar is not None:
            self.toolbar.remove_tool(key)

    # Subject ----------------------------------------------------------------

    def edit_subject(self) -> None:
        self._editing = True
        self.subject_edit.setText(self.settings.subject(self.key))
        self.subject_edit.setPlaceholderText(tr(BY_KEY[base_of(self.key)].title))
        self.subject_caption.hide()
        self.subject_pencil.hide()
        self.subject_edit.show()
        self.subject_edit.setFocus()
        self.subject_edit.selectAll()

    def _commit_subject(self) -> None:
        if not self._editing:
            return
        text = self.subject_edit.text()
        self._end_subject_edit()
        self.settings.set_subject(self.key, text)
        self._fill_header()

    def _end_subject_edit(self) -> None:
        self._editing = False
        self.subject_edit.hide()
        self.subject_caption.show()
        self.subject_pencil.show()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.subject_edit and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._end_subject_edit()  # Esc gives up the change; the menu stays
            return True
        return super().eventFilter(obj, event)

    # More -------------------------------------------------------------------

    def more_menu(self) -> QMenu:
        """What the ... button offers. Built fresh each time, so the positions are current."""
        menu = QMenu(self)
        key, base = self.key, base_of(self.key)
        toolbar = self.toolbar
        if toolbar is not None and base in DUPLICABLE:
            menu.addAction(icon("copy-simple"), tr("Duplicate"), self._duplicate)
        menu.addAction(icon("arrow-counter-clockwise"), tr("Reset to default"), self._reset)
        if toolbar is not None and key in toolbar.keys():
            keys = toolbar.keys()
            position = keys.index(key)
            menu.addSeparator()
            menu.addAction(icon("caret-left"), tr("Move left"),
                           lambda: toolbar.move_tool(key, position - 1)).setEnabled(position > 0)
            menu.addAction(icon("caret-right"), tr("Move right"),
                           lambda: toolbar.move_tool(key, position + 1)).setEnabled(position < len(keys) - 1)
        if toolbar is not None:
            menu.addSeparator()
            menu.addAction(icon("plus-circle"), tr("Add tools…"), self._add_tools)
            menu.addAction(icon("arrows-counter-clockwise"), tr("Reset toolbar"), self._reset_toolbar)
        menu.addSeparator()
        menu.addAction(icon("question"), tr("Help"), self._help)
        if toolbar is not None:
            menu.addAction(icon("gear-six"), tr("All tool settings…"), self._all_settings)
        return explain_menu(menu)

    def _show_more(self) -> None:
        menu = self.more_menu()
        menu.exec(self.more_button.mapToGlobal(QPoint(0, self.more_button.height() + 4)))
        menu.deleteLater()

    def _duplicate(self) -> None:
        if self.toolbar is not None:
            self.toolbar.duplicate_tool(self.key)

    def _reset(self) -> None:
        key = self.key
        self.settings.reset_style(key)
        self.settings.set_subject(key, "")
        self.settings.set_locked(key, None)

    def _reset_toolbar(self) -> None:
        self.close_menu()
        self.toolbar.reset()

    def _add_tools(self) -> None:
        self.close_menu()
        self.toolbar.chooser_requested.emit()

    def _window(self):
        """The main window: the toolbar's, or the one behind the settings window that opened this menu."""
        owner = self.toolbar if self.toolbar is not None else self.parentWidget()
        window = owner.window() if owner is not None else None
        while window is not None and not hasattr(window, "open_guide") and window.parentWidget() is not None:
            window = window.parentWidget().window()
        return window

    def _help(self) -> None:
        window = self._window()
        self.close_menu()
        if hasattr(window, "open_guide"):
            window.open_guide("tools")

    def _all_settings(self) -> None:
        window = self._window()
        self.close_menu()
        if hasattr(window, "open_settings"):
            window.open_settings("annotation")

    def _add_bookmark(self) -> None:
        self.close_menu()
        if self.toolbar is not None:
            self.toolbar.use("bookmark")

    # Changes ------------------------------------------------------------------

    def _edited(self, changes: dict) -> None:
        self.settings.update_style(self.key, **changes)
        if self.toolbar is not None and not BY_KEY[base_of(self.key)].action and self.settings.tool != self.key:
            self.settings.set_tool(self.key)  # styling a tool from its menu is choosing it to draw with

    def _ask_scale(self) -> None:
        from .insert_dialogs import ScaleDialog

        with self.suspended():
            dialog = ScaleDialog(self.settings.measure, self.owner_window())
            if dialog.exec() == ScaleDialog.DialogCode.Accepted:
                self.settings.set_measure(*dialog.measure())
            dialog.deleteLater()

    def _on_style_changed(self, key: str, style) -> None:
        # Also while hidden: a dialog it opened may be running, and it shows again afterwards.
        if key == self.key:
            self.editor.set_style(self.settings.style(key))
            self._fill_header()
        elif key == "measure":
            self.editor.set_measure(self.settings.measure)

    def _on_instances(self) -> None:
        key = self.key
        if key and "#" in key and key not in self.settings.instances(base_of(key)):
            self.close_menu()

    def _on_pref(self, name: str) -> None:
        if name == "tool_menu_pinned" and self.toolbar is not None:
            self.set_pinned(bool(self.prefs.get(name)))
            if self.isVisible():
                self._fill_header()
        elif name == "signatures" and hasattr(self.editor, "signature_list") and isValid(self.editor.signature_list):
            self.editor.signature_list.reload()
            QTimer.singleShot(0, self.refit)
        elif name in ("after_drawing", "select_new") and self.isVisible():
            self._fill_header()


HINTS = {
    "angle": "Click an arm's end, the corner, then the other arm's end.",
    "volume": "Click for each corner, then say how deep it goes.",
    "stamp": "Click where the stamp goes.",
    "polyline": "Click for each corner; double-click or Enter finishes.",
    "polygon": "Click for each corner; double-click or Enter closes the shape.",
    "perimeter": "Click for each point; double-click or Enter finishes.",
    "area": "Click for each corner; double-click or Enter closes the shape.",
    "camera": "Click the tool to take a photo with the Camera app." if WINDOWS else
              "Click the tool to take a photo with the computer's camera.",
    "image": "Click the tool to choose a picture file. Ctrl+V pastes a copied one.",
    "lasso": "Draw a loop round annotations. Afterwards Select takes over, with them selected.",
}


class SelectionMenu(_Menu):
    """The style of the selected annotations, from the selection bar's Style button."""

    def __init__(self, view, parent: QWidget | None = None):
        super().__init__(parent)
        self.view = view
        self.title = _label("", "MenuTitle")
        self.header_layout.addWidget(self.title)
        self.empty = _label("Nothing in this selection has a style to change.", "MenuCaption", wrap=True)
        self.body_layout.addWidget(self.empty)
        self._anchor: QWidget | None = None
        self._annotations: list[Annotation] = []
        self.editor.changed.connect(self._edited)

    def anchor_widget(self) -> QWidget | None:
        return self._anchor if self._anchor is not None and isValid(self._anchor) else None

    def show_for(self, annotations, anchor: QWidget, beside: QRect | None = None) -> None:
        """Open for `annotations` under `anchor`, or beside `beside` (the selection, global) if there is room."""
        self._anchor = anchor
        self.refresh(annotations)
        if not self._annotations:
            return
        anchor_rect, bounds = self.rects_of(anchor)
        self.place(anchor_rect, bounds, beside=beside)
        self.show()
        self.raise_()

    def refresh(self, annotations) -> None:
        """Show the selection as it is now: after a change, or when it changed."""
        annotations = list(annotations)
        if not annotations:
            self.close_menu()
            return
        self._annotations = annotations
        options = selection_options(annotations)
        palette = _selection_palette(annotations)
        wide = any(isinstance(a, Tape) or (isinstance(a, Ink) and a.highlighter) for a in annotations)
        self.title.setText(tr(selection_title(annotations)))
        self.editor.set_options(options, selection_style(annotations[0]), wide=wide, base=palette, live=False,
                                rules=_selection_rules(annotations), preview=False)
        self.empty.setVisible(not options)
        if self.isVisible():
            self.refit()

    def _edited(self, changes: dict) -> None:
        self.view.apply_style(**selection_changes(changes))
