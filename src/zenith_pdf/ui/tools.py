"""Canvas tools. Each turns press / drag / release on a page into an annotation."""

from __future__ import annotations

import getpass
import math
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QPainterPath
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem

from ..core import textlayout
from ..core.annotations import (
    DASHES,
    ENDINGS,
    NOTE_ICONS,
    NOTE_SIZE,
    UNITS,
    Color,
    FreeText,
    Line,
    Note,
    Redaction,
    Shape,
    TextMarkup,
    constrain_angle,
    normalized,
)
from . import messages
from .annotation_items import AnnotationItem, qcolor
from .tool_catalog import BY_KEY, DUPLICABLE, MAX_INSTANCES, base_of, instance_number, is_valid_key

if TYPE_CHECKING:
    from .document_view import DocumentView, PageItem

MIN_DRAG = 3.0  # points; shorter drags count as clicks
TEXT_BOX_WIDTH = 200.0
MIN_TEXT_BOX = 80.0  # points: a text box started this close to the right edge moves left to be this wide
MAX_SUBJECT = 200  # characters

# The values the choice fields of ToolStyle take.
PEN_TYPES = ("basic", "pro", "calligraphy")
ERASE_MODES = ("whole", "partial")  # whole strokes, or only what the eraser passes over
AUDIO_SOURCES = ("ask", "record", "file")
TAPE_STARTS = ("covered", "peeled")
LASER_SIZES = {"thin": 3.0, "medium": 5.0, "thick": 8.0}  # screen pixels
CLEAN_KINDS = ("highlight", "underline", "squiggly", "strikeout")  # the markup tools whose marks the cleaner removes
IMAGE_SIZES = {"actual": 0.0, "quarter": 0.25, "half": 0.5, "full": 1.0}  # share of the page width; 0: as copied
SNAPSHOT_SCALES = (1, 2, 3)
LASSO_PICKS = ("touching", "inside", "centre")
TEXT_FONTS = ("Helvetica", "Times", "Courier")  # the PDF's built-in fonts a text box is written in
TEXT_ALIGNS = ("left", "center", "right")


@dataclass(frozen=True)
class ToolStyle:
    color: Color
    width: float = 2.0
    opacity: float = 1.0
    font_size: float = 12.0
    fill: Color | None = None  # None: no fill
    border: bool = True  # shapes: False draws the fill alone
    dash: str = "solid"  # DASHES
    head_start: str = "None"  # ENDINGS
    head_end: str = "None"
    highlight: bool = False  # multiply blend: darkens, never covers the text
    pen: str = "basic"  # PEN_TYPES
    nib_angle: float = 45.0  # calligraphy, degrees
    ink_to_line: bool = False
    ink_to_shape: bool = False
    erase: str = "whole"  # ERASE_MODES
    tape_start: str = "covered"  # TAPE_STARTS
    laser_size: str = "medium"  # LASER_SIZES
    laser_fade: float = 0.7  # seconds
    clean_kinds: tuple[str, ...] = CLEAN_KINDS
    note_icon: str = "Comment"  # NOTE_ICONS
    image_size: str = "actual"  # IMAGE_SIZES
    link_border: bool = False
    snapshot_scale: int = 3  # SNAPSHOT_SCALES
    snapshot_annotations: bool = True
    lasso_pick: str = "centre"  # LASSO_PICKS
    audio_source: str = "ask"  # AUDIO_SOURCES
    signature_width: float = 150.0  # points, of a newly placed signature
    font: str = "Helvetica"  # TEXT_FONTS
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"  # TEXT_ALIGNS

    def face(self) -> tuple[str, bool, bool]:
        """(font, bold, italic), as a text box made with this style measures its text."""
        return self.font, self.bold, self.italic

    def text_format(self) -> dict:
        """The FreeText fields a text box made with this style takes from it, besides its size and colours."""
        return dict(font=self.font, bold=self.bold, italic=self.italic, underline=self.underline, align=self.align)


@dataclass(frozen=True)
class ToolConfig:
    """One tool's settings: its style, the subject its annotations get, and its lock."""

    style: ToolStyle
    subject: str = ""  # "" means the tool's title
    locked: bool | None = None  # None means the tool's own default, LOCK_DEFAULTS


RED = (0.89, 0.18, 0.18)
BLUE = (0.1, 0.45, 0.95)
BLACK = (0.0, 0.0, 0.0)
DEFAULT_STYLES = {
    "pen": ToolStyle(color=(0.12, 0.12, 0.14), width=2.0),
    "marker": ToolStyle(color=(1.0, 0.86, 0.0), width=14.0, highlight=True),
    "eraser": ToolStyle(color=(0.5, 0.5, 0.5), width=14.0),
    "tape": ToolStyle(color=(0.98, 0.78, 0.36), width=18.0),
    "laser": ToolStyle(color=(1.0, 0.15, 0.2)),
    "line": ToolStyle(color=RED),
    "polyline": ToolStyle(color=(0.55, 0.3, 0.9)),
    "arrow": ToolStyle(color=RED, head_end="ClosedArrow"),
    "rectangle": ToolStyle(color=RED),
    "ellipse": ToolStyle(color=RED),
    "cloud": ToolStyle(color=RED),
    "polygon": ToolStyle(color=(0.55, 0.3, 0.9)),
    "highlight": ToolStyle(color=(1.0, 0.86, 0.0)),
    "underline": ToolStyle(color=(0.13, 0.55, 0.95)),
    "squiggly": ToolStyle(color=(0.13, 0.55, 0.95)),
    "strikeout": ToolStyle(color=RED),
    "cleaner": ToolStyle(color=RED),
    "text": ToolStyle(color=BLACK),
    "callout": ToolStyle(color=BLACK, fill=(1.0, 1.0, 1.0)),
    "redact": ToolStyle(color=(0.86, 0.12, 0.12), fill=BLACK),
    "distance": ToolStyle(color=BLUE, width=1.5),
    "perimeter": ToolStyle(color=BLUE, width=1.5),
    "area": ToolStyle(color=BLUE, width=1.5),
    "note": ToolStyle(color=(1.0, 0.82, 0.2)),
    "bookmark": ToolStyle(color=BLUE),
    "signature": ToolStyle(color=BLACK),
    "camera": ToolStyle(color=BLACK),
    "image": ToolStyle(color=BLACK),
    "hyperlink": ToolStyle(color=BLUE),
    "audio": ToolStyle(color=(0.96, 0.45, 0.33)),
    "equation": ToolStyle(color=BLACK, font_size=16.0),
    "snapshot": ToolStyle(color=(0.94, 0.7, 0.0)),
    "select": ToolStyle(color=BLUE),
    "lasso": ToolStyle(color=BLUE),
    "hand": ToolStyle(color=BLUE),
}

# The options each tool's menu shows, in order. Most edit ToolStyle fields (OPTION_FIELDS),
# a few edit preferences in Settings (SETTING_OPTIONS), and "scale" edits ToolSettings.measure.
_LINES = ("color", "width", "opacity", "dash", "endings", "highlight")
_SHAPES = ("color", "fill", "width", "opacity", "dash", "highlight")
_MARKUP = ("color", "opacity")
_MEASURE = ("color", "width", "scale")
_TEXT = ("color", "font_size", "text_format", "fill")
_PICTURE = ("image_size",)
OPTIONS: dict[str, tuple[str, ...]] = {
    "pen": ("pen_type", "color", "width", "opacity", "ink_to_line", "ink_to_shape"),
    "marker": ("color", "width", "opacity", "highlight", "ink_to_line", "ink_to_shape"),
    "eraser": ("erase", "width"),
    "tape": ("color", "width", "tape_start"),
    "laser": ("color", "laser_size", "laser_fade"),
    "line": _LINES,
    "polyline": _LINES,
    "arrow": _LINES,
    "rectangle": _SHAPES,
    "ellipse": _SHAPES,
    "cloud": ("color", "fill", "width", "opacity", "highlight"),
    "polygon": _SHAPES,
    "highlight": _MARKUP,
    "underline": _MARKUP,
    "squiggly": _MARKUP,
    "strikeout": _MARKUP,
    "cleaner": ("clean_kinds",),
    "text": _TEXT,
    "callout": _TEXT,
    "redact": ("fill",),
    "distance": _MEASURE,
    "perimeter": _MEASURE,
    "area": _MEASURE,
    "note": ("color", "note_icon"),
    "bookmark": (),
    "signature": ("signatures",),
    "camera": _PICTURE,
    "image": _PICTURE,
    "hyperlink": ("color", "link_border"),
    "audio": ("audio_source", "color"),
    "equation": ("color", "font_size"),
    "snapshot": ("snapshot_scale", "snapshot_annotations"),
    "select": ("select_new", "snap_annotations", "snap_content"),
    "lasso": ("lasso_pick",),
    "hand": ("wheel", "scroll_speed"),
}
OPTION_FIELDS: dict[str, tuple[str, ...]] = {
    "pen_type": ("pen", "nib_angle"),  # nib_angle only matters for "calligraphy"
    "color": ("color", "border"),  # border only for BORDER_TOOLS: their "No colour" is border=False
    "endings": ("head_start", "head_end"),
    "signatures": ("signature_width",),  # plus the saved signatures, in Settings
    "scale": (),
    "text_format": ("font", "bold", "italic", "underline", "align"),
} | {name: (name,) for name in (
    "fill", "width", "opacity", "font_size", "dash", "highlight", "ink_to_line", "ink_to_shape", "erase",
    "tape_start", "laser_size", "laser_fade", "clean_kinds", "note_icon", "image_size", "link_border",
    "snapshot_scale", "snapshot_annotations", "lasso_pick", "audio_source")}
SETTING_OPTIONS = {name: name for name in ("select_new", "snap_annotations", "snap_content", "wheel", "scroll_speed",
                                           "signatures")}  # option -> Settings key
BORDER_TOOLS = frozenset({"rectangle", "ellipse", "cloud", "polygon"})  # colour titled Border, with No colour
# The controls of the older style popup and settings table, for each tool that has any.
_BASIC = {"color", "width", "opacity", "font_size", "scale"}
STYLE_CONTROLS = {tool: set(names) & _BASIC for tool, names in OPTIONS.items() if set(names) & _BASIC}
WIDE_TOOLS = {"marker", "eraser", "tape"}  # thickness chips in bigger steps
MEASURE_TOOLS = {"distance", "perimeter", "area"}

# Whether a tool stays on after one use. Repetitive tools are locked by default; tools that place
# one object are not, and go back to the "after_drawing" tool. Tools missing here never switch away:
# select, hand and laser; lasso, which goes to Select by itself; and the action tools.
LOCK_DEFAULTS = {key: True for key in (
    "pen", "marker", "eraser", "tape", "highlight", "underline", "squiggly", "strikeout", "cleaner", "redact",
    "distance", "perimeter", "area")} | {key: False for key in (
    "line", "arrow", "polyline", "rectangle", "ellipse", "cloud", "polygon", "text", "callout", "note", "signature",
    "hyperlink", "audio", "equation", "snapshot")}
LOCKABLE = frozenset(LOCK_DEFAULTS)
AFTER_DRAWING = ("pan", "select")


# Checks for each ToolStyle field. Each returns the clean value or raises ValueError / TypeError.

def _number(low: float, high: float):
    def check(value) -> float:
        if isinstance(value, bool):
            raise ValueError(value)
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(value)
        return min(max(number, low), high)

    return check


def _one_of(choices):
    choices = tuple(choices)

    def check(value):
        if isinstance(value, bool) or value not in choices:
            raise ValueError(value)
        return choices[choices.index(value)]  # 2.0 -> 2

    return check


def _flag(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError(value)
    return value


def _color(value) -> Color:
    if isinstance(value, str) or len(value) != 3 or any(isinstance(c, bool) for c in value):
        raise ValueError(value)
    color = tuple(float(c) for c in value)
    if not all(math.isfinite(c) for c in color):
        raise ValueError(value)
    return tuple(min(max(c, 0.0), 1.0) for c in color)


def _fill(value) -> Color | None:
    return None if value is None else _color(value)


def _kinds(value) -> tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(value)
    chosen = set(value)
    if not chosen or not chosen <= set(CLEAN_KINDS):  # an empty set would make a cleaner that cleans nothing
        raise ValueError(value)
    return tuple(kind for kind in CLEAN_KINDS if kind in chosen)


FIELD_CHECKS = {
    "color": _color,
    "width": _number(0.1, 100.0),
    "opacity": _number(0.05, 1.0),
    "font_size": _number(1.0, 400.0),
    "fill": _fill,
    "border": _flag,
    "dash": _one_of(DASHES),
    "head_start": _one_of(ENDINGS),
    "head_end": _one_of(ENDINGS),
    "highlight": _flag,
    "pen": _one_of(PEN_TYPES),
    "nib_angle": _number(0.0, 180.0),
    "ink_to_line": _flag,
    "ink_to_shape": _flag,
    "erase": _one_of(ERASE_MODES),
    "tape_start": _one_of(TAPE_STARTS),
    "laser_size": _one_of(LASER_SIZES),
    "laser_fade": _number(0.3, 3.0),
    "clean_kinds": _kinds,
    "note_icon": _one_of(NOTE_ICONS),
    "image_size": _one_of(IMAGE_SIZES),
    "link_border": _flag,
    "snapshot_scale": _one_of(SNAPSHOT_SCALES),
    "snapshot_annotations": _flag,
    "lasso_pick": _one_of(LASSO_PICKS),
    "audio_source": _one_of(AUDIO_SOURCES),
    "signature_width": _number(40.0, 600.0),
    "font": _one_of(TEXT_FONTS),
    "bold": _flag,
    "italic": _flag,
    "underline": _flag,
    "align": _one_of(TEXT_ALIGNS),
}


def checked_fields(values: dict) -> dict:
    """The valid ToolStyle fields among `values`, cleaned; unknown names and bad values are left out."""
    clean = {}
    for name, value in values.items():
        check = FIELD_CHECKS.get(name)
        if check is None:
            continue
        try:
            clean[name] = check(value)
        except (TypeError, ValueError):
            continue
    return clean


def _consistent(style: ToolStyle) -> ToolStyle:
    """A shape needs a border or a fill: with neither, the border comes back."""
    return style if style.border or style.fill is not None else replace(style, border=True)


class ToolSettings(QObject):
    """The active tool and each tool's style, subject and lock, shared by every open document.

    Keys are the catalog's tool keys ("pen") or copies of them ("pen#2"), as in tool_catalog.
    """

    tool_changed = Signal(str)  # the active key
    style_changed = Signal(str, object)  # key, ToolStyle; also after a subject or lock change; ("measure", None)
    instances_changed = Signal()  # a copy of a tool was made, loaded or forgotten

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.tool = "select"
        self._configs: dict[str, ToolConfig] = {key: ToolConfig(style) for key, style in DEFAULT_STYLES.items()}
        self.author = getpass.getuser()
        self.after_drawing = "select"  # where an unlocked tool goes after one use: "select" or "pan"
        self.select_new = False  # select each annotation as soon as it is made
        # True while an unlocked tool hands over to Pan or Select after one use, not by the user's choice.
        self.handing_over = False
        # The drawing's scale for measurements: paper value and unit, standing for a real value and unit.
        self.measure = (1.0, "cm", 1.0, "cm")

    @property
    def return_to_select(self) -> bool:
        """Unlocked tools go back to Select (else to Pan)."""
        return self.after_drawing == "select"

    @return_to_select.setter
    def return_to_select(self, value: bool) -> None:
        self.after_drawing = "select" if value else "pan"

    @property
    def styles(self) -> dict[str, ToolStyle]:
        return {key: config.style for key, config in self._configs.items()}

    def set_tool(self, key: str) -> None:
        """Turn a tool on. Unknown keys are ignored, and so are the action tools: they act once, never stay on."""
        if key != self.tool and is_valid_key(key) and not BY_KEY[base_of(key)].action:
            self.tool = key
            self.tool_changed.emit(key)

    # One tool's settings ---------------------------------------------------

    def config(self, key: str | None = None) -> ToolConfig:
        key = key or self.tool
        config = self._configs.get(key)
        return config if config is not None else ToolConfig(DEFAULT_STYLES.get(base_of(key), ToolStyle(color=RED)))

    def style(self, key: str | None = None) -> ToolStyle:
        return self.config(key).style

    def style_for(self, base: str) -> ToolStyle:
        """The style of the active copy of `base` if one is active, else of `base` itself."""
        return self.style(self._active_copy(base))

    def subject(self, key: str | None = None) -> str:
        """The subject the tool's annotations get: the user's, else the tool's title."""
        key = key or self.tool
        spec = BY_KEY.get(base_of(key))
        return self.config(key).subject or (spec.title if spec is not None else "")

    def subject_for(self, base: str) -> str:
        return self.subject(self._active_copy(base))

    def set_subject(self, key: str, text: str) -> None:
        """An empty text, or the tool's title, means the default subject."""
        text = str(text).strip()[:MAX_SUBJECT]
        spec = BY_KEY.get(base_of(key))
        if spec is not None and text == spec.title:
            text = ""
        self._put(key, replace(self.config(key), subject=text))

    def is_locked(self, key: str | None = None) -> bool:
        """True if the tool stays on after one use. Tools that can't be locked never switch away."""
        key = key or self.tool
        base = base_of(key)
        if base not in LOCKABLE:
            return True
        locked = self.config(key).locked
        return LOCK_DEFAULTS[base] if locked is None else locked

    def set_locked(self, key: str, locked: bool | None) -> None:
        """None goes back to the tool's default. Ignored for tools that can't be locked."""
        if base_of(key) in LOCKABLE and (locked is None or isinstance(locked, bool)):
            self._put(key, replace(self.config(key), locked=locked))

    def update_style(self, key: str, **changes) -> None:
        """Change some style fields. Unknown fields and bad values are ignored, numbers kept in range."""
        config = self.config(key)
        style = _consistent(replace(config.style, **checked_fields(changes)))
        self._put(key, replace(config, style=style))

    def reset_style(self, key: str) -> None:
        """The tool's default style. Its subject and lock stay."""
        self._put(key, replace(self.config(key), style=DEFAULT_STYLES.get(base_of(key), ToolStyle(color=RED))))

    def _active_copy(self, base: str) -> str:
        return self.tool if base_of(self.tool) == base else base

    def _put(self, key: str, config: ToolConfig) -> None:
        if not is_valid_key(key) or self._configs.get(key) == config:
            return
        self._configs[key] = config
        self.style_changed.emit(key, config.style)

    # Copies of a tool ------------------------------------------------------

    def instances(self, base: str) -> list[str]:
        """`base` and its known copies, in number order."""
        copies = [key for key in self._configs if "#" in key and base_of(key) == base]
        return [base] + sorted(copies, key=instance_number)

    def duplicate(self, key: str, taken=()) -> str:
        """A copy of the tool with the same style, subject and lock, under the smallest free "base#n".

        `taken` names keys in use elsewhere, such as the toolbar's, so a copy never reuses one of them.
        """
        base = base_of(key)
        if base not in DUPLICABLE:
            raise ValueError(f"{base} has no copies")
        used = set(self._configs) | set(taken)
        new = next((f"{base}#{n}" for n in range(2, MAX_INSTANCES + 1) if f"{base}#{n}" not in used), None)
        if new is None:
            raise ValueError(f"{base} has too many copies")
        self._configs[new] = self.config(key)
        self.instances_changed.emit()
        return new

    def forget(self, key: str) -> None:
        """Drop a copy ("base#n"), as when it leaves the toolbar. If it was the active tool, Select takes over."""
        if "#" not in key:
            return
        if self.tool == key:
            self.set_tool("select")
        if self._configs.pop(key, None) is not None:
            self.instances_changed.emit()

    # Measurements ----------------------------------------------------------

    def measure_scale(self) -> tuple[float, str]:
        """Real units per point on the page, and the unit's name."""
        paper, paper_unit, real, real_unit = self.measure
        points = paper * UNITS.get(paper_unit, 1.0)
        return (real / points if points > 0 else 1.0), real_unit

    def set_measure(self, paper: float, paper_unit: str, real: float, real_unit: str) -> None:
        self.measure = (paper, paper_unit, real, real_unit)
        self.style_changed.emit("measure", None)

    # Stored form -----------------------------------------------------------

    def styles_data(self) -> dict:
        """Every tool's settings as plain values, to store in the settings."""
        data = {key: {"style": asdict(config.style), "subject": config.subject, "locked": config.locked}
                for key, config in self._configs.items()}
        return data | {"measure": list(self.measure)}

    def load_styles(self, data: dict) -> None:
        """Settings stored by styles_data, or by older versions (the style fields alone).

        Unknown keys are skipped; each bad field falls back to its default and the others are kept.
        """
        if not isinstance(data, dict):
            return
        measure = data.get("measure")
        if isinstance(measure, list) and len(measure) == 4:
            try:
                self.measure = (float(measure[0]), str(measure[1]), float(measure[2]), str(measure[3]))
            except (TypeError, ValueError):
                pass
        new_copies = False
        for key, values in data.items():
            if not is_valid_key(key) or not isinstance(values, dict):
                continue
            if "style" in values:
                style_values = values["style"] if isinstance(values["style"], dict) else {}
                subject, locked = values.get("subject", ""), values.get("locked")
            else:
                style_values, subject, locked = values, "", None
            base = base_of(key)
            style = _consistent(replace(DEFAULT_STYLES[base], **checked_fields(style_values)))
            subject = subject.strip()[:MAX_SUBJECT] if isinstance(subject, str) else ""
            if not (isinstance(locked, bool) and base in LOCKABLE):
                locked = None
            new_copies = new_copies or ("#" in key and key not in self._configs)
            self._configs[key] = ToolConfig(style, subject, locked)
            self.style_changed.emit(key, style)
        if new_copies:
            self.instances_changed.emit()


def remove_item(item: QGraphicsItem | None) -> None:
    """Take a tool's helper item off the canvas. A page edit may have deleted it already, with the scene."""
    if item is not None and shiboken6.isValid(item) and item.scene() is not None:
        item.scene().removeItem(item)


def alive(item: QGraphicsItem | None) -> bool:
    """Whether a helper item is still on the canvas."""
    return item is not None and shiboken6.isValid(item) and item.scene() is not None


class Tool:
    cursor = Qt.CursorShape.CrossCursor
    wants_hover = False  # receives hover() while the mouse moves with no button down

    def __init__(self, view: DocumentView, name: str):
        self.view = view
        self.name = name  # the base tool; copies of it ("pen#2") share this object

    @property
    def key(self) -> str:
        """The copy of this tool in use: the active key if it is one of ours, else the tool itself."""
        active = self.view.tool_settings.tool
        return active if base_of(active) == self.name else self.name

    @property
    def busy(self) -> bool:
        """In the middle of something that spans several clicks."""
        return False

    def hover(self, page: PageItem, pos: QPointF) -> None:
        pass

    def double_click(self, page: PageItem, pos: QPointF) -> bool:
        return False

    def key_press(self, event) -> bool:
        return False

    @property
    def style(self) -> ToolStyle:
        return self.view.tool_settings.style(self.key)

    def common(self) -> dict:
        """Fields every annotation this tool makes gets: the author and the tool's subject."""
        settings = self.view.tool_settings
        return dict(author=settings.author, subject=settings.subject(self.key))

    def press(self, page: PageItem, pos: QPointF, event) -> bool:
        return False

    def move(self, page: PageItem, pos: QPointF, event) -> None:
        pass

    def release(self, page: PageItem, pos: QPointF, event) -> None:
        pass

    def cancel(self) -> None:
        pass


class TextSpanTool(Tool):
    """Drag across text. Base of text selection and of the markup tools."""

    cursor = Qt.CursorShape.IBeamCursor

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.anchor = -1
        self.rects = ()
        self.text = ""
        self.preview: QGraphicsPathItem | None = None

    def press(self, page, pos, event) -> bool:
        index = self.view.document.char_at(page.index, (pos.x(), pos.y()), tolerance=6)
        if index < 0:
            return False
        self.cancel()
        self.page, self.anchor = page, index
        self.preview = QGraphicsPathItem(page)
        self.preview.setZValue(3)
        self.preview.setPen(Qt.PenStyle.NoPen)
        self.preview.setBrush(QBrush(self.preview_color()))
        self._extend(index)
        return True

    def move(self, page, pos, event) -> None:
        if self.page is None or not alive(self.preview):
            return
        index = self.view.document.char_at(self.page.index, (pos.x(), pos.y()), tolerance=40)
        if index >= 0:
            self._extend(index)

    def _extend(self, index: int) -> None:
        self.rects, self.text = self.view.document.text_span(self.page.index, self.anchor, index)
        path = QPainterPath()
        for rect in self.rects:
            path.addRect(QRectF(*rect))
        self.preview.setPath(path)

    def preview_color(self):
        return qcolor(self.style.color, 0.45)

    def cancel(self) -> None:
        remove_item(self.preview)
        self.preview = None
        self.page = None
        self.rects, self.text = (), ""


class MarkupTool(TextSpanTool):
    STYLES = {"highlight": "Highlight", "underline": "Underline", "squiggly": "Squiggly", "strikeout": "StrikeOut"}

    def release(self, page, pos, event) -> None:
        rects, index, anchor = self.rects, self.page.index if self.page is not None else -1, self.anchor
        self.cancel()
        if rects and index >= 0:
            s = self.style
            turns = self.view.document.text_turns(index, anchor)  # along vertical text, too
            markup = TextMarkup(page=index, style=self.STYLES[self.name], quads=rects, color=s.color, opacity=s.opacity,
                                rotation=turns, **self.common())
            self.view.add_annotations([markup], markup.label)
            self.view.created([markup])


class SelectTool(TextSpanTool):
    """Selects annotations (handled by Qt) and text (handled here). A click on text selects nothing."""

    cursor = Qt.CursorShape.ArrowCursor

    def __init__(self, view, name):
        super().__init__(view, name)
        self.start = QPointF()
        self.dragged = False

    def preview_color(self):
        return qcolor((0.2, 0.45, 1.0), 0.3)

    def press(self, page, pos, event) -> bool:
        self.view.clear_text_selection()
        self.start, self.dragged = QPointF(pos), False
        if not super().press(page, pos, event):
            return False
        self.view.scene().clearSelection()  # text it is, then: the annotations let go
        return True

    def move(self, page, pos, event) -> None:
        if not self.dragged and math.hypot(pos.x() - self.start.x(), pos.y() - self.start.y()) >= MIN_DRAG:
            self.dragged = True
        super().move(page, pos, event)

    def release(self, page, pos, event) -> None:
        if self.rects and self.dragged and alive(self.preview):
            self.view.set_text_selection(self.page.index, self.rects, self.text, self.preview)
            self.preview = None  # now owned by the view
            self.page, self.rects, self.text = None, (), ""
        else:
            self.cancel()


class DragShapeTool(Tool):
    """Rectangle, ellipse, line and arrow: press, drag, release."""

    def __init__(self, view, name):
        super().__init__(view, name)
        self.page = None
        self.anchor = QPointF()
        self.item: AnnotationItem | None = None

    def build(self, start: QPointF, end: QPointF, constrain: bool):
        s = self.style
        a, b = (start.x(), start.y()), (end.x(), end.y())
        if self.name == "redact":
            return Redaction(page=self.page.index, rects=(normalized(*a, *b),), color=s.color,
                             fill=s.fill if s.fill is not None else BLACK, **self.common())
        common = dict(page=self.page.index, color=s.color, opacity=s.opacity, width=s.width, highlight=s.highlight,
                      **self.common())
        if self.name in ("line", "arrow"):
            if constrain:
                b = constrain_angle(a, b)
            return Line(start=a, end=b, head_start=s.head_start, head_end=s.head_end, dash=s.dash, **common)
        if constrain:  # square or circle
            side = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            b = (a[0] + side * (1 if b[0] >= a[0] else -1), a[1] + side * (1 if b[1] >= a[1] else -1))
        shape = "Circle" if self.name == "ellipse" else "Square"
        cloud = self.name == "cloud"
        return Shape(shape=shape, rect=normalized(*a, *b), cloudy=cloud, fill=s.fill, border=s.border,
                     dash="solid" if cloud else s.dash, **common)

    def press(self, page, pos, event) -> bool:
        self.cancel()
        pos = self.view.snapped(page, pos)
        self.page, self.anchor = page, pos
        self.item = AnnotationItem(self.build(pos, pos, False), page, interactive=False)
        return True

    def move(self, page, pos, event) -> None:
        if not alive(self.item):
            return
        constrain = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if not constrain:
            pos = self.view.snapped(page, pos)
        self.item.set_annotation(self.build(self.anchor, pos, constrain))

    def release(self, page, pos, event) -> None:
        if not alive(self.item):
            self.cancel()
            return
        annotation = self.item.annotation
        self.cancel()
        # A cloud's bounds take in its bumps: its own rect says whether the drag was a real one.
        x, y, w, h = annotation.rect if isinstance(annotation, Shape) else (
            annotation.bounds() if not isinstance(annotation, Line) else (0, 0, 0, 0))
        if isinstance(annotation, Line):
            dx = annotation.end[0] - annotation.start[0]
            dy = annotation.end[1] - annotation.start[1]
            big_enough = abs(dx) >= MIN_DRAG or abs(dy) >= MIN_DRAG
        else:
            big_enough = w >= MIN_DRAG and h >= MIN_DRAG
        if big_enough:
            self.view.add_annotations([annotation], annotation.label)
            self.view.created([annotation])

    def cancel(self) -> None:
        remove_item(self.item)
        self.item = None
        self.page = None


class TextBoxTool(Tool):
    cursor = Qt.CursorShape.IBeamCursor

    def press(self, page, pos, event) -> bool:
        s = self.style
        page_width, page_height = page.rect.width(), page.rect.height()
        # As wide as the room right of the click; with too little room there, it ends at the page's edge instead.
        width = min(TEXT_BOX_WIDTH, page_width, max(page_width - pos.x(), MIN_TEXT_BOX))
        height = textlayout.fitted_height("", s.font_size, width, s.face())
        x = max(min(pos.x(), page_width - width), 0.0)
        y = max(min(pos.y(), page_height - height), 0.0)
        box = FreeText(
            page=page.index,
            rect=(x, y, width, height),
            font_size=s.font_size,
            color=s.color,
            fill=s.fill,
            **s.text_format(),
            **self.common(),
        )
        self.view.edit_text_box(box, is_new=True, tool=self.name)
        return True


class NoteTool(Tool):
    def press(self, page, pos, event) -> bool:
        text = messages.get_multiline_text(self.view, "New note", "What the note says.", action="Add note",
                                           required=True)
        if text is not None and text.strip():
            # Centred on the click, and on the page whole, even from a click at its edge.
            x = min(max(pos.x() - NOTE_SIZE / 2, 0.0), max(page.rect.width() - NOTE_SIZE, 0.0))
            y = min(max(pos.y() - NOTE_SIZE / 2, 0.0), max(page.rect.height() - NOTE_SIZE, 0.0))
            note = Note(
                page=page.index,
                position=(x, y),
                contents=text,
                color=self.style.color,
                icon=self.style.note_icon,
                **self.common(),
            )
            self.view.add_annotations([note], note.label)
            self.view.created([note])
        return False  # the dialog took the mouse: no release will follow


def make_tools(view: DocumentView) -> dict[str, Tool]:
    tools: dict[str, Tool] = {"select": SelectTool(view, "select")}
    for name in MarkupTool.STYLES:
        tools[name] = MarkupTool(view, name)
    for name in ("rectangle", "ellipse", "cloud", "line", "arrow", "redact"):
        tools[name] = DragShapeTool(view, name)
    tools["text"] = TextBoxTool(view, "text")
    tools["note"] = NoteTool(view, "note")
    from .drawing_tools import make_more_tools  # they build on the classes above

    tools.update(make_more_tools(view))
    return tools
