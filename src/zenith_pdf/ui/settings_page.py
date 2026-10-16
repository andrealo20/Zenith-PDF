"""The settings window: categories down the left, a search box, and settings that apply at once."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal, qVersion
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .. import __version__
from ..core import ocr
from ..icons import icon, readable
from ..settings import DEFAULTS, Settings
from ..theme import ACCENTS, THEME_NAMES, themes
from . import messages
from .dialogs import SHORTCUTS, delete_template, edit_template, template_summary
from .document_view import LAYOUTS, TINTS
from .tool_catalog import BY_KEY, CATALOG, base_of, instance_title
from .tool_menu import IconPushButton
from .tools import LOCKABLE, OPTIONS, SETTING_OPTIONS, ToolSettings
from .widgets import IconButton, Segmented, Switch, glass_style, muted_label, say_copied

ASSETS = Path(__file__).resolve().parent.parent / "assets"
GLASS_CHOICES = (("clear", "Clear"), ("balanced", "Balanced"), ("solid", "Solid"))
GLASS_TIPS = {"clear": "The pages show through, blurred, with a light tint.",
              "balanced": "A stronger tint: the pages show through less.",
              "solid": "Opaque bars, as when Windows' transparency effects are off."}
LOGO = ASSETS / "zenith.svg"  # drawn at every size, so it stays sharp on any screen
CONTROL_WIDTH = 280  # combos and fields on the right of a setting
FLASH_MS = 1600  # how long a setting the assistant opened stays lit
CATEGORIES = [
    ("view", "View & Layout", "layout"),
    ("templates", "Templates", "note-blank"),
    ("annotation", "Annotation", "pencil-line"),
    ("navigation", "Canvas Navigation", "mouse-simple"),
    ("files", "Files & Saving", "floppy-disk"),
    ("performance", "Performance", "gauge"),
    ("ocr", "Text Recognition", "text-t"),
    ("shortcuts", "Keyboard Shortcuts", "keyboard"),
    ("help", "Help", "question"),
    ("about", "About Zenith PDF", None),  # the app's own logo
]
STORED_DATA = {"recent", "session", "file_state", "markup_library", "templates", "pinned_folders", "last_directory",
               "signatures", "tool_styles", "toolbar_tools", "recent_times", "starred", "file_tags", "tag_colors",
               "file_info", "recent_colours", "assistant_recent"}
CREDITS = [
    ("Qt and PySide6", "LGPL-3.0", lambda: f"{qVersion()} / {PYSIDE_VERSION}"),
    ("PDFium, through pypdfium2", "BSD-3-Clause, Apache-2.0", lambda: _pdfium_version()),
    ("qpdf, through pikepdf", "Apache-2.0, MPL-2.0", lambda: _pikepdf_version()),
    ("QtAwesome with Phosphor icons", "MIT", lambda: _module_version("qtawesome")),
    ("Pillow", "MIT-CMU", lambda: _module_version("PIL")),
]
# Controls the mouse wheel changes only once clicked: scrolling the page over them must not change them.
WHEEL_GUARDED = (QComboBox, QAbstractSpinBox, QSlider)


def _module_version(name: str) -> str:
    try:
        return __import__(name).__version__
    except Exception:
        return "?"


def _pdfium_version() -> str:
    try:
        import pypdfium2.version as version

        return f"{version.PDFIUM_INFO.build} / {version.PYPDFIUM_INFO.version}"
    except Exception:
        return _module_version("pypdfium2")


def _pikepdf_version() -> str:
    try:
        import pikepdf

        return f"{pikepdf.__libqpdf_version__} / {pikepdf.__version__}"
    except Exception:
        return "?"


def version_details() -> str:
    lines = [f"Zenith PDF {__version__}", f"Python {platform.python_version()} on {platform.platform()}"]
    lines += [f"{name}: {version()}" for name, _, version in CREDITS]
    return "\n".join(lines)


def logo_icon() -> QIcon:
    return QIcon(str(LOGO)) if LOGO.exists() else icon("info")


class Row(QFrame):
    """One setting: a title, a line of explanation and its control (on the right, or below it).

    With no title, the control takes the whole row.
    """

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, keywords: str = "",
                 below: bool = False):
        super().__init__()
        self.setObjectName("SettingRow")
        self.title, self.description, self.keywords = title, description, keywords  # the assistant finds rows by these
        self.search_text = f"{title} {description} {keywords}".lower()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 12, 0, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(3)
        rows = 0
        if title:
            heading = QLabel(title)
            heading.setObjectName("SettingTitle")
            grid.addWidget(heading, rows, 0)
            rows += 1
        if description:
            text = muted_label(description)
            text.setWordWrap(True)
            grid.addWidget(text, rows, 0)
            rows += 1
        if control is not None:
            if below or not title:
                grid.addWidget(control, rows, 0, 1, 2)
            else:
                grid.addWidget(control, 0, 1, rows, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(0, 1)

    def matches(self, words: list[str]) -> bool:
        return all(word in self.search_text for word in words)


class Section(QWidget):
    def __init__(self, key: str, title: str):
        super().__init__()
        self.key = key
        self.title = title
        self.rows: list[Row] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 28)
        layout.setSpacing(0)
        heading = QLabel(title)
        heading.setObjectName("SettingsSection")
        heading.setIndent(0)  # its style gives it a frame, and a frame an indent: it would start 5 px in
        layout.addWidget(heading)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(0)
        layout.addLayout(self._rows)

    def add(self, row: Row) -> Row:
        row.search_text += f" {self.title.lower()}"
        if self.rows:
            line = QFrame()
            line.setObjectName("SettingLine")
            line.setFixedHeight(1)
            self._rows.addWidget(line)
            row.line = line
        self.rows.append(row)
        self._rows.addWidget(row)
        return row

    def filter(self, words: list[str]) -> bool:
        """Show only the rows matching every word. True if any does."""
        shown = False
        for row in self.rows:
            visible = not words or row.matches(words)
            row.setVisible(visible)
            if hasattr(row, "line"):
                row.line.setVisible(visible and shown)
            shown = shown or visible
        return shown


class AccentPicker(QWidget):
    """A row of round colour buttons; the chosen one has a ring."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._buttons = {}
        for key, (label, dark, light) in ACCENTS.items():
            button = QToolButton()
            button.setFixedSize(26, 26)
            button.setToolTip(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: settings.set("accent", k))
            layout.addWidget(button)
            self._buttons[key] = (button, dark, light)
        settings.changed.connect(self._refresh)
        themes.changed.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_) -> None:
        chosen = self._settings.get("accent")
        dark = themes.current.name == "dark"
        for key, (button, on_dark, on_light) in self._buttons.items():
            size = 26
            ratio = 2
            pixmap = QPixmap(size * ratio, size * ratio)
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(on_dark if dark else on_light))
            painter.drawEllipse(5, 5, size - 10, size - 10)
            if key == chosen:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QColor(themes.current.text))
                painter.drawEllipse(1, 1, size - 2, size - 2)
            painter.end()
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(size, size))


class _ToolRow(QFrame):
    """One tool in the list: its icon in its colour, its name, its lock and subject. A click opens its menu."""

    clicked = Signal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("PanelRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 8, 7)
        layout.setSpacing(10)
        self.glyph = QLabel()
        self.glyph.setFixedSize(22, 22)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.name = QLabel(instance_title(key))
        self.name.setObjectName("PanelRowText")
        self.summary = muted_label()
        text.addWidget(self.name)
        text.addWidget(self.summary)
        self.caret = QLabel()
        self.caret.setFixedSize(16, 16)
        layout.addWidget(self.glyph)
        layout.addLayout(text, 1)
        layout.addWidget(self.caret)

    def show_state(self, tools: ToolSettings) -> None:
        t = themes.current
        base = base_of(self.key)
        color = readable(tools.style(self.key).color, t.window, t.text) if "color" in OPTIONS.get(base, ()) else t.text
        ratio = self.devicePixelRatioF()
        self.glyph.setPixmap(icon(BY_KEY[base].icon, color).pixmap(QSize(22, 22), ratio))
        self.caret.setPixmap(icon("caret-right", t.text_muted).pixmap(QSize(16, 16), ratio))
        parts = []
        if base in LOCKABLE:
            parts.append("Locked" if tools.is_locked(self.key) else "Unlocked")
        subject = tools.subject(self.key)
        if subject != BY_KEY[base].title:
            parts.append(f"subject “{subject}”")
        self.summary.setText(", ".join(parts) or "Options")
        self.setToolTip(f"{instance_title(self.key)}: colour, size and the other options")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.key)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.key)
        else:
            super().keyPressEvent(event)


class ToolStyleList(QWidget):
    """Every tool with options, and every copy of one: a click opens the same menu as on the toolbar."""

    COLUMNS = 2

    def __init__(self, tools: ToolSettings, settings: Settings, parent=None):
        super().__init__(parent)
        self._tools = tools
        self._settings = settings
        self.rows: dict[str, _ToolRow] = {}
        self.menu = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        tools.style_changed.connect(self._restate)
        tools.instances_changed.connect(self._build)
        themes.changed.connect(self._restate)
        self._build()

    def keys(self) -> list[str]:
        """The tools listed: those with options of their own (not only app settings), copies after each."""
        keys = []
        for spec in CATALOG:
            options = [o for o in OPTIONS.get(spec.key, ()) if o not in SETTING_OPTIONS or o == "signatures"]
            if options:
                keys += self._tools.instances(spec.key)
        return keys

    def _build(self, *_) -> None:
        while self._grid.count():
            widget = self._grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.rows = {}
        for index, key in enumerate(self.keys()):
            row = _ToolRow(key)
            row.clicked.connect(self.open_menu)
            self._grid.addWidget(row, index // self.COLUMNS, index % self.COLUMNS)
            self.rows[key] = row
        for column in range(self.COLUMNS):
            self._grid.setColumnStretch(column, 1)
        self._restate()

    def _restate(self, *_) -> None:
        for row in self.rows.values():
            row.show_state(self._tools)

    def open_menu(self, key: str) -> None:
        from .tool_menu import ToolMenu

        if self.menu is None:
            self.menu = ToolMenu(self._tools, toolbar=None, prefs=self._settings, parent=self.window())
        row = self.rows.get(key)
        if row is not None:
            self.menu.show_for(key, row.caret)


class Templates(QWidget):
    """The new-document templates, with edit and delete."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 0)
        self._layout.setSpacing(6)
        self._list = QVBoxLayout()
        self._list.setSpacing(6)
        self._layout.addLayout(self._list)
        add = IconPushButton("plus", " New template")
        add.clicked.connect(lambda: edit_template(self._settings, None, self))
        self._layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        settings.changed.connect(self._on_changed)
        themes.changed.connect(self._build)
        self._build()

    def _on_changed(self, key: str) -> None:
        if key == "templates":
            self._build()

    def _build(self, *_) -> None:
        while self._list.count():
            widget = self._list.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        templates = self._settings.templates
        if not templates:
            self._list.addWidget(muted_label("No templates yet."))
        for position, template in enumerate(templates):
            row = QFrame()
            row.setObjectName("PanelRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(12, 6, 6, 6)
            glyph = QLabel()
            glyph.setPixmap(icon("note-blank", themes.current.accent).pixmap(20, 20))
            text = QVBoxLayout()
            text.setSpacing(0)
            name = QLabel(template.get("name", "Untitled"))
            name.setObjectName("PanelRowText")
            text.addWidget(name)
            text.addWidget(muted_label(template_summary(template)))
            edit = IconButton("pencil-simple", "Edit template", icon_size=16)
            edit.clicked.connect(lambda _=False, p=position: edit_template(self._settings, p, self))
            remove = IconButton("trash", "Delete template", icon_size=16)
            remove.clicked.connect(lambda _=False, p=position: delete_template(self._settings, p))
            layout.addWidget(glyph)
            layout.addLayout(text, 1)
            layout.addWidget(edit)
            layout.addWidget(remove)
            self._list.addWidget(row)


class SettingsWindow(QDialog):
    def __init__(self, settings: Settings, tool_settings: ToolSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsWindow")
        self.setWindowTitle("Settings")
        self.resize(1000, 720)
        self._settings = settings
        self._tools = tool_settings
        self._watchers: dict[str, list] = {}
        settings.changed.connect(self._on_changed)

        nav = QFrame()
        nav.setObjectName("SettingsNav")
        nav.setFixedWidth(300)
        title = QLabel("Settings")
        title.setObjectName("SettingsTitle")
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self._search_icon = self.search.addAction(icon("magnifying-glass"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNavList")
        self.nav.setIconSize(QSize(22, 22))
        for key, label, icon_name in CATEGORIES:
            item = QListWidgetItem(icon(icon_name) if icon_name else logo_icon(), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(240, 46))
            self.nav.addItem(item)
            if key == "help":
                self._add_nav_separator()
        self.nav.currentItemChanged.connect(self._on_nav)
        self.nav.itemClicked.connect(self._on_nav)  # also the category already chosen, to leave a search
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(24, 34, 16, 20)
        nav_layout.setSpacing(18)
        nav_layout.addWidget(title)
        nav_layout.addWidget(self.search)
        nav_layout.addWidget(self.nav, 1)

        self.container = QWidget()
        self.container.setObjectName("SettingsContent")
        self._content = QVBoxLayout(self.container)
        self._content.setContentsMargins(56, 40, 56, 40)
        self._content.setSpacing(0)
        self.sections: dict[str, Section] = {}
        self._build_view()
        self._build_templates()
        self._build_annotation()
        self._build_navigation()
        self._build_files()
        self._build_performance()
        self._build_ocr()
        self._build_shortcuts()
        self._build_help()
        self._build_about()
        self.empty = muted_label("No setting matches your search.")
        self._content.addWidget(self.empty)
        self._content.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setObjectName("SettingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.container)
        for control in self.container.findChildren(QWidget):
            if isinstance(control, WHEEL_GUARDED):
                control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                control.installEventFilter(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(nav)
        layout.addWidget(self.scroll, 1)
        self.nav.setCurrentRow(0)
        themes.changed.connect(self._retheme)

    def _retheme(self, *_) -> None:
        """Icons are drawn in the theme's colours when made: draw them again."""
        names = {key: name for key, _, name in CATEGORIES}
        for row in range(self.nav.count()):
            key = self.nav.item(row).data(Qt.ItemDataRole.UserRole)
            if key in names:
                self.nav.item(row).setIcon(icon(names[key]) if names[key] else logo_icon())
        self._search_icon.setIcon(icon("magnifying-glass"))

    def _add_nav_separator(self) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(QSize(240, 17))
        holder = QWidget()
        layout = QVBoxLayout(holder)
        # The item's padding and margin leave the holder 7 px of its 17: the line in the middle of those.
        layout.setContentsMargins(8, 3, 8, 3)
        line = QFrame()
        line.setObjectName("SettingLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.nav.addItem(item)
        self.nav.setItemWidget(item, holder)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(obj, WHEEL_GUARDED) and not obj.hasFocus():
            QApplication.sendEvent(self.scroll.viewport(), event)  # the page scrolls; the setting stays
            return True
        return super().eventFilter(obj, event)

    # Navigation and search ----------------------------------------------

    def show_category(self, key: str) -> None:
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentRow(row)
                self._on_nav(self.nav.item(row))
                return

    def reveal(self, key: str, row: Row) -> None:
        """Show category `key` scrolled to `row`, lit for a moment so the eye finds it (the assistant's way in)."""
        self.search.clear()
        self.show_category(key)
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(row, 0, 80))
        row.setProperty("flash", True)
        row.style().unpolish(row)
        row.style().polish(row)

        def calm():
            if isValid(row):
                row.setProperty("flash", False)
                row.style().unpolish(row)
                row.style().polish(row)

        QTimer.singleShot(FLASH_MS, calm)

    def _on_nav(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        if self.search.text():
            self.search.blockSignals(True)  # a category clicked during a search leaves the search
            self.search.clear()
            self.search.blockSignals(False)
        key = item.data(Qt.ItemDataRole.UserRole)
        for name, section in self.sections.items():
            section.filter([])
            section.setVisible(name == key)
        self.empty.hide()
        self.scroll.verticalScrollBar().setValue(0)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        if not words:
            self._on_nav(self.nav.currentItem())
            return
        found = False
        for section in self.sections.values():
            visible = section.filter(words)
            section.setVisible(visible)
            found = found or visible
        self.empty.setVisible(not found)
        self.scroll.verticalScrollBar().setValue(0)

    # Controls bound to settings ------------------------------------------

    def _on_changed(self, key: str) -> None:
        for load in self._watchers.get(key, []):
            load()

    def _watch(self, key: str, widget: QWidget, load) -> None:
        def guarded():
            widget.blockSignals(True)
            load()
            widget.blockSignals(False)

        guarded()
        self._watchers.setdefault(key, []).append(guarded)

    def _combo(self, key: str, options: dict) -> QComboBox:
        box = QComboBox()
        box.setFixedWidth(CONTROL_WIDTH)
        for value, label in options.items():
            box.addItem(label, value)
        self._watch(key, box, lambda: box.setCurrentIndex(max(box.findData(self._settings.get(key)), 0)))
        box.currentIndexChanged.connect(lambda: self._settings.set(key, box.currentData()))
        return box

    def _switch(self, key: str) -> Switch:
        switch = Switch()
        self._watch(key, switch, lambda: switch.setChecked(bool(self._settings.get(key))))
        switch.toggled.connect(lambda on: self._settings.set(key, on))
        return switch

    def _slider(self, key: str, low: int, high: int, step: int, suffix: str) -> QWidget:
        holder = QWidget()
        holder.setFixedWidth(CONTROL_WIDTH)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setSingleStep(step)
        slider.setPageStep(step)
        value = QLabel()
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.ensurePolished()  # measured in the font the style sheet gives it
        widest = max(value.fontMetrics().horizontalAdvance(f"{n}{suffix}") for n in (low, high))
        value.setFixedWidth(widest + 10)

        def load():
            slider.setValue(int(self._settings.get(key)))
            value.setText(f"{slider.value()}{suffix}")

        self._watch(key, slider, load)
        slider.valueChanged.connect(lambda v: value.setText(f"{v}{suffix}"))
        slider.valueChanged.connect(lambda v: self._settings.set(key, int(round(v / step) * step)))
        layout.addWidget(slider, 1)
        layout.addWidget(value)
        return holder

    def _section(self, key: str) -> Section:
        title = next(label for k, label, _ in CATEGORIES if k == key)
        section = Section(key, title)
        self.sections[key] = section
        self._content.addWidget(section)
        return section

    # The sections -------------------------------------------------------

    def _build_view(self) -> None:
        s = self._section("view")
        s.add(Row("Theme", "Dark, light, or the same as Windows.", self._combo("theme", THEME_NAMES),
                  "dark mode light mode colours appearance"))
        s.add(Row("Accent colour", "Used for selections, the active tool and buttons.", AccentPicker(self._settings),
                  "colour color highlight"))
        glass = Segmented(GLASS_CHOICES, GLASS_TIPS)
        glass.setFixedWidth(CONTROL_WIDTH)
        self._watch("glass", glass, lambda: glass.set_value(glass_style(self._settings.get("glass"))))
        glass.chosen.connect(lambda value: self._settings.set("glass", value))
        s.add(Row("Glass", "Clear lets the pages show through the floating bars, blurred; Balanced tints them more; "
                  "Solid makes them opaque. Their text stays readable over any page.",
                  glass, "transparency effects transparent blur frosted acrylic liquid clear balanced solid opaque"))
        s.add(Row("Open documents at", "The zoom a document opens with.",
                  self._combo("default_zoom", {"width": "Fit width", "page": "Fit page", "actual": "Actual size"}),
                  "zoom"))
        s.add(Row("Page layout", "How new tabs arrange the pages. Each tab can change it in the zoom bar.",
                  self._combo("default_layout", LAYOUTS), "two pages book spread single"))
        s.add(Row("Page colours", "How new tabs colour the pages. The file itself never changes.",
                  self._combo("default_tint", TINTS), "dark sepia night invert"))
        s.add(Row("Side panel", "Whether documents open with the page thumbnails showing.",
                  self._combo("side_panel", {"auto": "Pages, for documents with several",
                                             "always": "Always pages", "never": "Closed"}),
                  "thumbnails sidebar"))
        s.add(Row("Link previews", "Point at a link to another page for a moment: a card shows the spot it goes to, "
                  "such as a figure or a reference. Off, a tooltip names the page.",
                  self._switch("link_preview"), "hover card citation reference figure tooltip"))
        s.add(Row("Reading mode also goes full screen", "Reading mode (Ctrl+H) hides the bars, the side panel and the "
                  "tabs, so only the pages show. On, it also fills the whole screen.",
                  self._switch("reading_full_screen"), "reading focus distraction immersive hide bars"))

    def _build_templates(self) -> None:
        s = self._section("templates")
        s.add(Row("Document templates",
                  "Paper, orientation, ruling and length for new documents. They appear under the + button.",
                  Templates(self._settings), "new document lined dotted grid paper", below=True))
        folder = QWidget()
        layout = QHBoxLayout(folder)
        layout.setContentsMargins(0, 0, 0, 0)
        path = QLineEdit()
        path.setReadOnly(True)
        path.setMinimumWidth(260)
        path.setPlaceholderText("The last folder you used")
        choose = QPushButton("Choose…")
        reset = QPushButton("Reset")
        choose.clicked.connect(self._choose_new_folder)
        reset.clicked.connect(lambda: self._settings.set("new_folder", ""))
        layout.addWidget(path)
        layout.addWidget(choose)
        layout.addWidget(reset)
        self._watch("new_folder", path, lambda: path.setText(self._settings.get("new_folder")))
        s.add(Row("Folder for new documents", "Where a new document is saved unless you choose another place.",
                  folder, "location directory", below=True))

    def _choose_new_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder for new documents", self._settings.new_document_folder)
        if folder:
            self._settings.set("new_folder", folder)

    def _build_annotation(self) -> None:
        s = self._section("annotation")
        author = QLineEdit()
        author.setFixedWidth(CONTROL_WIDTH)
        author.setPlaceholderText("Your name")
        self._watch("author", author, lambda: author.setText(self._settings.get("author")))
        author.editingFinished.connect(lambda: self._settings.set("author", author.text().strip()))
        s.add(Row("Author name", "Written into each annotation you make, so others can see who added it.", author,
                  "name account user"))
        s.add(Row("Remember tool styles", "Keep each tool's colour and thickness the next time Zenith PDF starts.",
                  self._switch("remember_styles"), "colours save"))
        s.add(Row("Unlocked tools go back to", "After one use, a tool that is not locked switches to Pan, or to "
                  "Select with what it made selected. Each tool's lock is in its menu: click the tool that is on.",
                  self._combo("after_drawing", {"pan": "Pan", "select": "Select"}),
                  "after drawing sticky tool lock unlock pan select return"))
        locks = QWidget()
        row = QHBoxLayout(locks)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for text, locked in (("Lock all", True), ("Unlock all", False)):
            button = IconPushButton("lock-simple" if locked else "lock-simple-open", f" {text}")
            button.clicked.connect(lambda _=False, on=locked: self._lock_all(on))
            row.addWidget(button)
        s.add(Row("Tool locks", "A locked tool stays on to draw again; an unlocked one goes back after one use. "
                  "The pens, the text markups and the measuring tools start locked; shapes, text boxes and notes "
                  "start unlocked.", locks, "lock unlock sticky keep tool"))
        s.add(Row("Select new annotations", "Select an annotation as soon as it is made, ready to move or style.",
                  self._switch("select_new")))
        s.add(Row("Snap to annotations", "While moving, resizing or drawing, edges and centres line up with other "
                  "annotations. Hold Alt to place freely.", self._switch("snap_annotations"), "snapping align guides"))
        s.add(Row("Snap to PDF content", "Line up with the page's text, pictures, drawings and edges.",
                  self._switch("snap_content"), "snapping align guides margins"))
        ask = Switch()
        self._watch("asked", ask, lambda: ask.setChecked(not self._settings.asked("flatten")))
        ask.toggled.connect(self._set_ask_flatten)
        s.add(Row("Ask before flattening", "Flattening makes annotations part of the page, so it asks first.", ask,
                  "confirm question"))
        self.tool_list = ToolStyleList(self._tools, self._settings)
        reset = QPushButton("Reset every tool")
        reset.clicked.connect(self._reset_tools)
        styles = QWidget()
        column = QVBoxLayout(styles)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(12)
        column.addWidget(self.tool_list)
        column.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        s.add(Row("Tool styles", "What each tool draws with. Click one for its menu, the same as on the toolbar.",
                  styles, "colour thickness width opacity font size highlight pen subject menu", below=True))

    def _lock_all(self, locked: bool) -> None:
        for base in sorted(LOCKABLE):
            for key in self._tools.instances(base):
                self._tools.set_locked(key, locked)

    def _reset_tools(self) -> None:
        if not messages.confirm(self, "Reset every tool", "Put every tool's colour, size, subject and lock back "
                                "to how Zenith PDF started?", action="Reset", danger=True):
            return
        for key in list(self._tools.styles):
            if key in BY_KEY or "#" in key:
                self._tools.reset_style(key)
                self._tools.set_subject(key, "")
                self._tools.set_locked(key, None)

    def _set_ask_flatten(self, ask: bool) -> None:
        asked = set(self._settings.get("asked"))
        asked.discard("flatten") if ask else asked.add("flatten")
        self._settings.set("asked", sorted(asked))

    def _build_navigation(self) -> None:
        s = self._section("navigation")
        s.add(Row("Mouse wheel", "Scroll the pages and zoom with Ctrl, or the other way round.",
                  self._combo("wheel", {"scroll": "Scrolls (Ctrl zooms)", "zoom": "Zooms (Ctrl scrolls)"}),
                  "scroll zoom ctrl"))
        s.add(Row("Scrolling speed", "How far one notch of the mouse wheel moves the pages.",
                  self._slider("scroll_speed", 50, 300, 10, "%"), "wheel fast slow"))
        s.add(Row("Panning", "Hold Space or the middle mouse button and drag to move the pages with any tool.",
                  None, "hand pan drag move"))

    def _build_files(self) -> None:
        s = self._section("files")
        s.add(Row("Save automatically", "Saves open documents that have changes. New documents wait for their "
                  "first save, so they never land somewhere unexpected.",
                  self._combo("autosave_minutes", {0: "Off", 1: "Every minute", 2: "Every 2 minutes",
                                                   5: "Every 5 minutes", 10: "Every 10 minutes"}),
                  "autosave backup"))
        s.add(Row("When Zenith PDF starts", "Pick up where you stopped, with each document where it was, or begin "
                  "with nothing open.",
                  self._combo("on_start", {"restore": "Reopen last time's documents",
                                           "home": "Start on the home page"}),
                  "restore session tabs startup reopen close windows"))
        s.add(Row("Remember where I was", "Open each document at the page and zoom it had when you closed it.",
                  self._switch("remember_position"), "last page position zoom resume"))
        recent = QWidget()
        recent.setFixedWidth(CONTROL_WIDTH)
        layout = QHBoxLayout(recent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        limit = QSpinBox()
        limit.setRange(0, 200)
        self._watch("recent_limit", limit, lambda: limit.setValue(self._settings.get("recent_limit")))
        limit.valueChanged.connect(self._set_recent_limit)
        clear = QPushButton("Clear list")
        clear.clicked.connect(lambda: self._settings.set("recent", []))
        layout.addWidget(limit, 1)
        layout.addWidget(clear)
        s.add(Row("Files on the home page", "How many recently opened files the home page lists.", recent,
                  "history home recent"))

    def _set_recent_limit(self, value: int) -> None:
        # Only how many show: the list itself stays, so typing 50 (a 5 first) or a smaller number loses nothing.
        self._settings.set("recent_limit", value)

    def _build_performance(self) -> None:
        s = self._section("performance")
        processes = {"auto": "Automatic"} | {str(n): ("None, one thread" if n == 0 else str(n)) for n in range(13)}
        s.add(Row("Render processes", "Pages are drawn by several processes at once. Takes effect at the next start.",
                  self._combo("render_processes", processes), "speed cpu cores threads"))
        s.add(Row("Memory for page images", "Sharp page tiles kept in memory, per document. More memory means "
                  "fewer pages drawn twice.",
                  self._combo("tile_cache_mb", {128: "128 MB", 256: "256 MB", 384: "384 MB", 512: "512 MB",
                                                768: "768 MB", 1024: "1 GB"}),
                  "cache ram tiles"))
        s.add(Row("Prepare zoom levels", "While idle, draw the next zoom level in and out, so the mouse wheel finds "
                  "it ready and sharp.", self._switch("prefetch_zoom"), "prefetch speculative render"))

    def _build_ocr(self) -> None:
        s = self._section("ocr")
        languages = {"": "Windows display language"} | dict(ocr.languages())
        s.add(Row("Language", "The language of the scanned text. Windows can add more languages in "
                  "Settings > Time & language > Language.", self._combo("ocr_language", languages),
                  "ocr scan recognise"))
        s.add(Row("Resolution", "Higher finds smaller print, but takes longer.",
                  self._combo("ocr_dpi", {150: "150 dpi, faster", 200: "200 dpi", 300: "300 dpi, small print"}),
                  "ocr dpi quality"))

    def _build_shortcuts(self) -> None:
        s = self._section("shortcuts")
        for group, entries in SHORTCUTS:
            for keys, action in entries:
                chip = QLabel(keys)
                chip.setObjectName("KeyChip")
                s.add(Row(action, "", chip, f"{group} keyboard shortcut {keys}"))

    def _build_help(self) -> None:
        s = self._section("help")
        s.add(Row("Help on buttons", "Point at a button and keep still: a card says what it does and how to "
                  "use it.", self._combo("help_badges", {"hover": "After pointing for a moment",
                                                         "badges": "Also show a ? on every button",
                                                         "never": "Off, short tooltips only"}),
                  "question mark tips explain tooltip guide"))
        s.add(Row("Wait before showing help", "How long to point at a button before its card opens.",
                  self._slider("help_delay", 300, 3000, 100, " ms"), "delay time seconds hover"))
        s.add(Row("Tips for new tools", "The first time you pick a tool, a small card under it gives two or three "
                  "ways to use it well. Each tool's card shows once.", self._switch("tool_tips"),
                  "tips hints first time tool card advice"))
        again = QPushButton("Show tips again")
        again.clicked.connect(self._tips_again)
        s.add(Row("Tips already seen", "Show every tool's tips again, the next time you pick it.", again,
                  "tips hints reset again"))
        assistant = QPushButton("Open the assistant")
        assistant.clicked.connect(self._open_assistant)
        s.add(Row("Assistant", "Ctrl+K, or the sparkle at the top right, finds any tool, command, setting, shortcut "
                  "or guide chapter from a few words or a question, in English, Italian, French, Spanish, German or "
                  "Portuguese. It also checks spelling, picks the key points of a text and turns highlights into "
                  "study notes, all on this PC.", assistant,
                  "assistant ai search command palette find ask spelling summary study notes"))
        guide = QPushButton("Open the guide")
        guide.clicked.connect(self._open_guide)
        s.add(Row("Guide", "A short guide to every part of Zenith PDF. F1 opens it from anywhere.", guide,
                  "manual help learn tutorial"))
        questions = QPushButton("Ask again")
        questions.clicked.connect(lambda: self._settings.set("asked", []))
        s.add(Row("Questions you turned off", "Questions answered with \"don't ask again\" will be asked again.",
                  questions, "confirm reset"))
        reset = QPushButton("Reset settings")
        reset.clicked.connect(self._reset_all)
        s.add(Row("Reset all settings", "Back to how Zenith PDF started. Recent files, templates, the markup "
                  "library and tool styles stay.", reset, "defaults factory"))

    def _open_guide(self) -> None:
        window = self.parent()
        if hasattr(window, "open_guide"):
            window.open_guide("")

    def _tips_again(self) -> None:
        self._settings.set("tips_seen", [])
        self._settings.set("tool_tips", True)
        self.sender().setText("Tips will show again")

    def _open_assistant(self) -> None:
        window = self.parent()
        if hasattr(window, "show_assistant"):
            self.hide()
            window.show_assistant()

    def _reset_all(self) -> None:
        if messages.confirm(self, "Reset all settings", "Put every setting back to its default? Recent files, "
                            "templates, the markup library and tool styles stay.", action="Reset", danger=True):
            for key in DEFAULTS:
                if key not in STORED_DATA:
                    self._settings.reset(key)

    def _build_about(self) -> None:
        s = self._section("about")
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        self.logo = QLabel()
        self.logo.setFixedSize(72, 72)
        self._draw_logo()
        themes.changed.connect(self._draw_logo)
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel("Zenith PDF")
        name.setObjectName("AboutName")
        text.addStretch()
        text.addWidget(name)
        text.addWidget(muted_label(f"Version {__version__}"))
        text.addWidget(muted_label("A PDF reader and markup app for Windows."))
        text.addStretch()
        layout.addWidget(self.logo)
        layout.addLayout(text, 1)
        s.add(Row("", "", header, "zenith pdf version about logo"))
        for title, licence, version in CREDITS:
            s.add(Row(title, f"{licence} licence, version {version()}", None, "credits licence open source"))
        copy = QPushButton("Copy details")
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(version_details()), say_copied(copy)))
        s.add(Row("Version details", f"Python {platform.python_version()}, {sys.platform}. Useful when reporting "
                  "a problem.", copy, "debug report"))

    def _draw_logo(self, *_) -> None:
        ratio = self.devicePixelRatioF()
        self.logo.setPixmap(logo_icon().pixmap(QSize(72, 72), ratio))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._draw_logo()  # the screen may have another scale than when it was built
