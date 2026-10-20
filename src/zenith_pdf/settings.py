"""Persistent user preferences.

Every preference has a default in DEFAULTS and is read with get() and written
with set(). Writing emits `changed(key)`, so open windows apply a preference
the moment it changes instead of after a restart.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal

DEFAULT_TEMPLATES = (
    {"name": "Study notes", "paper": "A4", "orientation": "portrait", "kind": "lined", "pages": 5},
    {"name": "Lecture notes", "paper": "A4", "orientation": "portrait", "kind": "dotted", "pages": 5},
)

DEFAULTS: dict[str, object] = {
    # View and layout
    "theme": "dark",  # dark, light or system
    "language": "auto",  # "auto" for the system's, else a key of core.i18n.LANGUAGES
    "accent": "blue",  # a key of theme.ACCENTS
    "glass": "clear",  # the floating bars: "clear" or "balanced" frosted glass, or "solid"
    "icon_colours": False,  # icons coloured by what they do, as Acrobat's tools are; off: the text colour
    "glass_adaptive": True,  # the bars turn light over a bright page and dark over dark content
    "reading_full_screen": False,  # reading mode also fills the screen
    "default_zoom": "actual",  # how every document opens: actual (100%), width or page
    "default_layout": "single",  # a key of document_view.LAYOUTS
    "default_tint": "normal",  # a key of document_view.TINTS
    "side_panel": "auto",  # auto (pages panel for multi-page documents), always or never
    "side_panel_width": 264,  # px: the panel's right edge drags it wider or narrower, in every tab
    "zoom_bar_compact": False,  # the bar at the bottom right shows the zoom alone, in every tab
    "link_preview": True,  # pointing at a link to a page shows a card of the spot it goes to
    # Help
    "help_badges": "hover",  # hover (a card after pointing for a moment), badges (a ? on buttons too) or never
    "help_delay": 1000,  # milliseconds of pointing before the card opens
    "asked": [],  # questions answered with "don't ask again"
    "tool_tips": True,  # a card with tips the first time each tool is used
    "tips_seen": [],  # the tools whose tips have shown
    "assistant_recent": [],  # what was last run from the assistant (Ctrl+K), the newest first
    "whats_new_after_update": True,  # after an update, a tour of what changed
    "seen_version": "",  # the version whose tour was shown last; "" before the first start
    "obsidian_vault": "",  # the vault study notes go to; "" asks the first time
    "obsidian_folder": "Zenith PDF",  # the folder in the vault, "" for its top
    "obsidian_open": True,  # show the note in Obsidian once it is sent
    "read_speed": 1.0,  # reading aloud: times the voice's own pace
    "read_voice": "",  # the voice picked last; "" for the system's
    "update_check": True,  # ask GitHub once a day whether a new version is out
    "update_auto": False,  # download new versions in the background and install them when the app closes
    "update_skipped": "",  # a version the user chose to skip
    "update_last_check": 0.0,  # when GitHub was last asked (seconds since 1970)
    "default_app_asked": False,  # whether Zenith PDF should open PDF files is asked no more
    "default_app_next": 0,  # the start from which it is asked again, after Later
    "default_app_times": 0,  # how often it was asked
    "starts": 0,  # how many times Zenith PDF started
    # Annotation
    "author": "",
    "remember_styles": True,
    "tool_styles": {},  # tool or copy key ("pen#2") -> {"style", "subject", "locked"}, and "measure"
    "toolbar_tools": [],  # the toolbar's tools and tool copies in order; empty means the default set
    "after_drawing": "select",  # where unlocked tools go after one use: "select" or "pan"
    "after_drawing_moved": False,  # 0.5.0 moved the old default, Pan, to Select once
    "tool_menu_pinned": False,  # the tool menu stays open while drawing
    "tool_menu_sections": {},  # "tool:section title" -> whether that section of the tool menu is open
    "recent_colours": [],  # colours picked last in the tool menus, as [r, g, b], the newest first
    "snap_annotations": False,  # line up with other annotations while moving, resizing, drawing
    "snap_content": False,  # line up with the page's text, pictures and edges
    "select_new": False,  # select an annotation right after making it
    "pen_pressure": True,  # with a pen that feels pressure, the line follows how hard it presses
    "touch_mode": False,  # a finger pans the pages and the pen draws; handles are bigger, for fingertips
    # Canvas navigation
    "wheel": "scroll",  # scroll (Ctrl zooms) or zoom (Ctrl scrolls)
    "scroll_speed": 100,  # percent
    "prefetch_zoom": True,
    # Files
    "autosave_minutes": 0,
    "on_start": "restore",  # restore: reopen last time's documents where they were; home: start with none
    "session": {},  # {"tabs": [view states], "active": index}, written when the app closes
    "remember_position": True,  # reopen each file at the page it was left at (the zoom is default_zoom's)
    "file_state": {},  # path -> view state, the most recent last
    "recent_limit": 40,
    "recent": [],
    "recent_times": {},  # path -> when it was last opened (seconds since 1970)
    "starred": [],
    "file_tags": {},  # path -> tag names
    "tag_colors": {},  # tag name -> colour
    "file_info": {},  # path -> {"mtime", "size", "pages", "author", "title"}, for the home page
    "search_index": True,  # keep an index of every PDF opened, for searching inside all of them
    "new_folder": "",  # "" means the last folder used
    "last_directory": str(Path.home() / "Documents"),
    # Performance
    "render_processes": "auto",
    "tile_cache_mb": 384,
    # Text recognition
    "ocr_language": "",  # "" means the system's languages
    "ocr_dpi": 200,
    # Stored data
    "markup_library": [],
    "signatures": [],  # {"name", "png" (base64)}
    "templates": list(DEFAULT_TEMPLATES),
    "pinned_folders": [],
}
JSON_KEYS = {"asked", "tool_styles", "session", "file_state", "recent", "markup_library", "templates",
             "pinned_folders", "signatures", "toolbar_tools", "recent_times", "starred", "file_tags", "tag_colors",
             "file_info", "tool_menu_sections", "recent_colours", "tips_seen", "assistant_recent"}
FILE_STATES = 300  # documents whose last position is remembered
RECENT_KEEP = 200  # recent files stored; "recent_limit" only says how many are shown, so lowering it loses none
# Stored values of older versions, and what they mean now. Anything else not in CHOICES reads as the default.
MIGRATIONS = {"after_drawing": {"keep": "pan"}, "glass": {"true": "clear", "false": "solid"}}
CHOICES = {"after_drawing": ("select", "pan"), "glass": ("clear", "balanced", "solid")}


class Settings(QObject):
    changed = Signal(str)  # the key that changed

    def __init__(self, store: QSettings | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._store = store or QSettings()
        self._cache: dict[str, object] = {}  # QSettings on Windows is the registry: read each key once

    # Generic access -----------------------------------------------------

    def get(self, key: str):
        if key in self._cache:
            value = self._cache[key]
        else:
            value = self._read(key)
            self._cache[key] = value
        # Lists and dicts are handed out as copies, so callers can't change the cache by accident.
        return json.loads(json.dumps(value)) if key in JSON_KEYS else value

    def set(self, key: str, value) -> None:
        if key in JSON_KEYS:
            self._store.setValue(key, json.dumps(value))
        else:
            self._store.setValue(key, value)
        self._cache[key] = json.loads(json.dumps(value)) if key in JSON_KEYS else value
        self.changed.emit(key)

    def reset(self, key: str) -> None:
        self._store.remove(key)
        self._cache.pop(key, None)
        self.changed.emit(key)

    def upgrade(self) -> None:
        """Once, what an older version's default would leave behind: unlocked tools went back to Pan before
        0.5.0, and now go back to Select."""
        if not self.get("after_drawing_moved"):
            if self.get("after_drawing") == "pan":
                self.set("after_drawing", "select")
            self.set("after_drawing_moved", True)

    def stored(self, key: str) -> bool:
        """Whether `key` was ever set, as opposed to still having its default."""
        return self._store.contains(key)

    def _read(self, key: str):
        default = DEFAULTS[key]
        raw = self._store.value(key, None)
        if raw is None:
            return json.loads(json.dumps(default)) if key in JSON_KEYS else default
        if key in JSON_KEYS:
            if key == "recent" and not isinstance(raw, str):  # a plain list, from older versions
                return [str(p) for p in raw or []]
            try:
                value = json.loads(str(raw))
            except ValueError:
                if key == "recent":  # one path, from the days it wasn't JSON
                    return [str(raw)]
                return json.loads(json.dumps(default))
            return value if isinstance(value, type(default)) else json.loads(json.dumps(default))
        if isinstance(default, bool):
            return raw if isinstance(raw, bool) else str(raw).lower() in ("true", "1", "yes")
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        value = MIGRATIONS.get(key, {}).get(str(raw).lower(), str(raw))  # a bool reads back as True or "true"
        return default if key in CHOICES and value not in CHOICES[key] else value

    # Recent files -------------------------------------------------------

    def recent_files(self) -> list[Path]:
        """The recent files to show, most recent first: as many as "recent_limit" says."""
        return [Path(p) for p in self._recent()[: max(int(self.get("recent_limit")), 0)]]

    def _recent(self) -> list[str]:
        """Every stored recent file, also those beyond the limit, so raising it again brings them back."""
        return [str(p) for p in self.get("recent") if isinstance(p, str)]

    def add_recent(self, path: Path) -> None:
        paths = [str(path)] + [p for p in self._recent() if Path(p) != Path(path)]
        times = self.get("recent_times")
        times[str(path)] = time.time()
        kept = paths[:RECENT_KEEP]
        self.set("recent_times", {p: t for p, t in times.items() if p in kept or p in self.get("starred")})
        self.set("recent", kept)

    def remove_recent(self, path: Path) -> None:
        self.set("recent", [p for p in self._recent() if Path(p) != Path(path)])

    def file_state(self, path: Path) -> dict | None:
        return self.get("file_state").get(str(path)) if self.get("remember_position") else None

    def remember_file_state(self, path: Path, state: dict) -> None:
        if not self.get("remember_position"):
            return
        states = self.get("file_state")
        states.pop(str(path), None)
        states[str(path)] = state  # dicts keep insertion order: the oldest go first
        while len(states) > FILE_STATES:
            states.pop(next(iter(states)))
        self.set("file_state", states)

    def asked(self, question: str) -> bool:
        """True if the user said "don't ask again" to this question."""
        return question in self.get("asked")

    def stop_asking(self, question: str) -> None:
        self.set("asked", sorted(set(self.get("asked")) | {question}))

    # Named preferences, kept for readability at the call sites ---------

    @property
    def theme(self) -> str:
        return self.get("theme")

    @theme.setter
    def theme(self, name: str) -> None:
        self.set("theme", name)

    @property
    def author(self) -> str:
        return self.get("author")

    @author.setter
    def author(self, name: str) -> None:
        self.set("author", name)

    @property
    def default_zoom(self) -> str:
        """How documents open: "width", "page" or "actual"."""
        return self.get("default_zoom")

    @default_zoom.setter
    def default_zoom(self, value: str) -> None:
        self.set("default_zoom", value)

    @property
    def render_processes(self) -> int | None:
        """None means automatic."""
        value = self.get("render_processes")
        return None if value in (None, "auto", "") else int(value)

    @render_processes.setter
    def render_processes(self, value: int | None) -> None:
        self.set("render_processes", "auto" if value is None else str(value))

    @property
    def markup_library(self) -> list[dict]:
        """Saved annotation groups: {"name": str, "records": [annotation dicts]}."""
        return self.get("markup_library")

    @markup_library.setter
    def markup_library(self, entries: list[dict]) -> None:
        self.set("markup_library", entries)

    @property
    def templates(self) -> list[dict]:
        """New-document templates: {"name", "paper", "orientation", "kind", "pages"}."""
        return self.get("templates")

    @templates.setter
    def templates(self, entries: list[dict]) -> None:
        self.set("templates", entries)

    @property
    def pinned_folders(self) -> list[str]:
        return self.get("pinned_folders")

    @pinned_folders.setter
    def pinned_folders(self, folders: list[str]) -> None:
        self.set("pinned_folders", folders)

    @property
    def last_directory(self) -> str:
        return self.get("last_directory")

    @last_directory.setter
    def last_directory(self, path: str) -> None:
        self.set("last_directory", path)

    @property
    def new_document_folder(self) -> str:
        """Where new documents go until their first save."""
        return self.get("new_folder") or self.last_directory

    @property
    def window_geometry(self) -> QByteArray | None:
        return self._store.value("window_geometry")

    @window_geometry.setter
    def window_geometry(self, data: QByteArray) -> None:
        self._store.setValue("window_geometry", data)
