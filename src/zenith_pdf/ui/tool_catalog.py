"""Every tool there is, grouped as in the tool chooser. The toolbar shows the ones the user picked."""

from __future__ import annotations

from dataclasses import dataclass

TOOL_MIME = "application/x-zenith-pdf-tool"
GROUPS = ["Free-form", "Shapes", "Review", "Measure", "Insert", "Select"]


@dataclass(frozen=True)
class ToolSpec:
    key: str
    title: str
    icon: str
    group: str
    color: str  # the icon's colour in the chooser; "" is the theme's text colour, for tools with no hue
    action: bool = False  # does something once (insert a picture), instead of staying active

    @property
    def help(self) -> str:
        return f"tool_{self.key}"


CATALOG = [
    ToolSpec("pen", "Pen", "pen-nib", "Free-form", "#ff6b6b"),
    ToolSpec("marker", "Highlighter", "marker-circle", "Free-form", "#f5c400"),
    ToolSpec("eraser", "Eraser", "eraser", "Free-form", ""),
    ToolSpec("tape", "Tape", "z-tape", "Free-form", "#b48cff"),
    ToolSpec("laser", "Laser pointer", "z-laser", "Free-form", "#ff4d4d"),
    ToolSpec("line", "Line", "line-segment", "Shapes", "#ff5a5a"),
    ToolSpec("polyline", "Polyline", "line-segments", "Shapes", "#9b6bff"),
    ToolSpec("arrow", "Arrow", "arrow-up-right", "Shapes", "#22c55e"),
    ToolSpec("rectangle", "Rectangle", "square", "Shapes", "#4f8dff"),
    ToolSpec("ellipse", "Ellipse", "circle", "Shapes", "#22c55e"),
    ToolSpec("cloud", "Cloud", "cloud", "Shapes", "#16a34a"),
    ToolSpec("polygon", "Polygon", "hexagon", "Shapes", "#8b5cf6"),
    ToolSpec("highlight", "Text highlight", "z-text-highlight", "Review", "#22c55e"),
    ToolSpec("underline", "Underline", "text-underline", "Review", "#ff5a5a"),
    ToolSpec("squiggly", "Squiggle", "z-squiggle", "Review", "#3b82f6"),
    ToolSpec("strikeout", "Strikethrough", "text-strikethrough", "Review", "#a855f7"),
    ToolSpec("cleaner", "Text cleaner", "z-cleaner", "Review", "#ff6b6b"),
    ToolSpec("text", "Text", "text-t", "Review", "#3b82f6"),
    ToolSpec("callout", "Callout", "chat-teardrop-text", "Review", "#f59e0b"),
    ToolSpec("redact", "Redaction", "eye-slash", "Review", ""),
    ToolSpec("distance", "Distance", "ruler", "Measure", "#06b6d4"),
    ToolSpec("perimeter", "Perimeter", "path", "Measure", "#14b8a6"),
    ToolSpec("area", "Area", "bounding-box", "Measure", "#0ea5e9"),
    ToolSpec("note", "Note", "note", "Insert", "#facc15"),
    ToolSpec("stamp", "Stamp", "circle-wavy-check", "Insert", "#ef4444"),
    ToolSpec("bookmark", "Bookmark", "bookmark-simple", "Insert", "#f59e0b", action=True),
    ToolSpec("signature", "Signature", "z-signature", "Insert", "#3b82f6"),
    ToolSpec("camera", "Image from camera", "camera", "Insert", "#14b8a6", action=True),
    ToolSpec("image", "Image from file", "image", "Insert", "#22c55e", action=True),
    ToolSpec("hyperlink", "Hyperlink", "link", "Insert", "#38bdf8"),
    ToolSpec("audio", "Audio note", "microphone", "Insert", "#ef4444"),
    ToolSpec("equation", "Equation", "z-equation", "Insert", "#8b5cf6"),
    ToolSpec("snapshot", "Snapshot", "z-snapshot", "Insert", "#facc15"),
    ToolSpec("select", "Select", "cursor", "Select", "#60a5fa"),
    ToolSpec("lasso", "Lasso select", "z-lasso", "Select", "#60a5fa"),
    ToolSpec("hand", "Pan", "hand", "Select", "#60a5fa"),
]
BY_KEY = {spec.key: spec for spec in CATALOG}
DEFAULT_TOOLBAR = ["select", "hand", "highlight", "underline", "strikeout", "rectangle", "ellipse", "line", "arrow",
                   "text", "note", "redact", "image"]
ALWAYS = {"select"}  # can't be taken off the toolbar
# Tools the toolbar can hold more than once, each copy with its own style: "pen", "pen#2", "pen#3".
DUPLICABLE = frozenset(spec.key for spec in CATALOG if not spec.action) - {
    "select", "hand", "lasso", "laser", "snapshot", "cleaner", "hyperlink"}
MAX_INSTANCES = 99


def base_of(key: str) -> str:
    """The tool an instance key belongs to: "pen#2" -> "pen"."""
    return key.partition("#")[0]


def instance_number(key: str) -> int:
    """1 for the tool itself, n for its copy "base#n"."""
    _, sep, number = key.partition("#")
    return int(number) if sep and number.isascii() and number.isdigit() else 1


def instance_title(key: str) -> str:
    """The name shown for an instance: "Pen", "Pen 2"."""
    spec = BY_KEY.get(base_of(key))
    if spec is None:
        return key
    number = instance_number(key)
    return spec.title if number == 1 else f"{spec.title} {number}"


def is_valid_key(key) -> bool:
    """A tool of the catalog, or a copy "base#n" of a duplicable one with 2 <= n <= MAX_INSTANCES."""
    if not isinstance(key, str):
        return False
    base, sep, number = key.partition("#")
    if not sep:
        return base in BY_KEY
    return (base in DUPLICABLE and number.isascii() and number.isdigit() and number == str(int(number))
            and 2 <= int(number) <= MAX_INSTANCES)


def toolbar_keys(stored: list) -> list[str]:
    """The user's toolbar, cleaned up: valid tool and instance keys only, each once, Select always there."""
    keys = [k for k in dict.fromkeys(k for k in stored if isinstance(k, str)) if is_valid_key(k)] if stored \
        else list(DEFAULT_TOOLBAR)
    for key in ALWAYS:
        if key not in keys:
            keys.insert(0, key)
    return keys
