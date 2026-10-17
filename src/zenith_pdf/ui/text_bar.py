"""The bar over selected text: mark it, copy it, write a note on it or search for it.

It appears when text is selected with the Select tool, by dragging across it or by double-clicking a word,
and floats above the selection (below it when there is no room above), never on it. Icons only, like the
selection bar: the four markups have their tools' icons, in the colours those tools draw with, and each
button says what it does when pointed at. Beside Highlight, a dot in its colour opens the colours to
highlight with; the one picked marks the text at once and becomes the highlight tool's colour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QToolButton

from ..icons import icon, readable
from ..theme import themes
from . import messages
from .help import attach_help
from .tool_catalog import BY_KEY
from .tool_menu import HIGHLIGHTER, nearest
from .widgets import BAR_BUTTON, FloatingBar, IconButton, separator

if TYPE_CHECKING:
    from .workspace import Workspace

# The markups, in the order of the bar: tool, tooltip, help topic.
MARKUPS = (("highlight", "Highlight", "text_highlight"), ("underline", "Underline", "text_underline"),
           ("strikeout", "Strikethrough", "text_strike"), ("squiggly", "Squiggle", "text_squiggle"))
COLOUR_NAMES = ("Yellow", "Green", "Blue", "Pink", "Orange", "Violet", "Red", "Grey")  # HIGHLIGHTER's, in order
CHIP = QSize(24, 16)  # the colour dot and its caret, beside Highlight
DOT = 16  # a colour of the menu (a menu's icon size), with room for the ring round the chosen one
SEARCH_CHARS = 120  # a longer selection is searched for by its start
ICON = 18


def colour_name(color) -> str:
    """The name of a highlighter colour, or its hex code for any other."""
    index = nearest(HIGHLIGHTER, color)
    return COLOUR_NAMES[index] if index >= 0 else QColor.fromRgbF(*color).name().upper()


def _dot(color, chosen: bool = False) -> QIcon:
    """A round swatch of `color`, for the colour menu; the chosen one has a ring in the accent colour, as the
    swatches of the tools' menus do."""
    size = DOT
    ratio = _ratio()
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    centre = QPointF(size / 2, size / 2)
    if chosen:
        painter.setPen(QPen(QColor(themes.current.accent), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, size / 2 - 0.75, size / 2 - 0.75)
    painter.setPen(QPen(QColor(128, 128, 128, 140), 1.0))
    painter.setBrush(QColor.fromRgbF(*color))
    painter.drawEllipse(centre, size / 2 - 2.5, size / 2 - 2.5)
    painter.end()
    return QIcon(pixmap)


def _chip(color) -> QIcon:
    """The dot of the highlight's colour and a small caret: the button that opens the colours."""
    t = themes.current
    ratio = _ratio()
    pixmap = QPixmap(round(CHIP.width() * ratio), round(CHIP.height() * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(128, 128, 128, 140), 1.0))
    painter.setBrush(QColor.fromRgbF(*color))
    painter.drawEllipse(QPointF(7, 8), 6, 6)
    icon("caret-down-bold", t.text).paint(painter, QRect(15, 4, 8, 8))
    painter.end()
    return QIcon(pixmap)


def _ratio() -> float:
    app = QApplication.instance()
    return app.devicePixelRatio() if app is not None else 1.0


class TextSelectionBar(FloatingBar):
    """Highlight (with its colours), Underline, Strikethrough and Squiggle, then Copy, Note and Search."""

    def __init__(self, workspace: Workspace, parent=None):
        super().__init__(parent)
        self._workspace = workspace
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the keys stay with the pages: Ctrl+C, Esc
        self.markups: dict[str, IconButton] = {}
        for key, tip, help_key in MARKUPS:
            button = IconButton(BY_KEY[key].icon, tip, icon_size=ICON, help=help_key)
            button.clicked.connect(lambda _=False, k=key: self.mark(k))
            self.markups[key] = button
        self.colour = QToolButton()
        self.colour.setObjectName("ColourChip")
        self.colour.setAutoRaise(True)
        self.colour.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.colour.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.colour.setIconSize(CHIP)
        self.colour.setFixedSize(CHIP.width() + 8, BAR_BUTTON)
        self.colour.setToolTip("Highlight colour")
        self.colour_menu = QMenu(self)
        self.colour_menu.setToolTipsVisible(True)
        for name, color in zip(COLOUR_NAMES, HIGHLIGHTER):
            action = self.colour_menu.addAction(_dot(color), name)
            action.setData(color)
            action.setToolTip(f"Highlight in {name.lower()}; new highlights take this colour too.")
            action.triggered.connect(lambda _=False, c=color: self.mark("highlight", c))
        self.colour_menu.aboutToShow.connect(self._tick_colour)
        self.colour.setMenu(self.colour_menu)
        attach_help(self.colour, "text_colour")
        self.copy = IconButton("copy", "Copy (Ctrl+C)", icon_size=ICON, help="text_copy")
        self.copy.clicked.connect(self.copy_text)
        self.note = IconButton("note-pencil", "Note on the text", icon_size=ICON, help="text_note")
        self.note.clicked.connect(self.add_note)
        self.search = IconButton("magnifying-glass", "Search for this text", icon_size=ICON, help="text_search")
        self.search.clicked.connect(self.search_text)
        self.speak = IconButton("speaker-high", "Read aloud", icon_size=ICON, help="text_read_aloud")
        self.speak.clicked.connect(workspace.read_aloud)

        highlight = QHBoxLayout()  # Highlight and its colour read as one split button
        highlight.setContentsMargins(0, 0, 0, 0)
        highlight.setSpacing(0)
        highlight.addWidget(self.markups["highlight"])
        highlight.addWidget(self.colour)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        layout.addLayout(highlight)
        for widget in (self.markups["underline"], self.markups["strikeout"], self.markups["squiggly"], separator(),
                       self.copy, self.note, self.search, self.speak):
            layout.addWidget(widget)

        workspace.tool_settings.style_changed.connect(self._on_style)
        themes.changed.connect(self._recolour)
        self._recolour()
        self.hide()

    # The colours of the buttons -----------------------------------------------------------

    def _on_style(self, key: str, _style=None) -> None:
        if key in self.markups:
            self._recolour()

    def _recolour(self, *_) -> None:
        """Each markup's icon in its tool's colour, made readable on the bar, as the toolbar shows them."""
        t = themes.current
        settings = self._workspace.tool_settings
        for key, button in self.markups.items():
            button._color = readable(settings.style(key).color, t.raised, t.text)
            button._refresh()
        color = settings.style("highlight").color
        self.colour.setIcon(_chip(color))
        name = colour_name(color)
        self.markups["highlight"].setProperty("help_detail", f"Highlights in {name.lower()}.")
        self.markups["highlight"].setToolTip(f"Highlight ({name.lower()})")

    def chosen_colour(self) -> int:
        """Which colour of the menu Highlight uses now, or -1 for one of its own."""
        return nearest(HIGHLIGHTER, self._workspace.tool_settings.style("highlight").color)

    def _tick_colour(self) -> None:
        chosen = self.chosen_colour()
        for position, action in enumerate(self.colour_menu.actions()):
            action.setIcon(_dot(action.data(), position == chosen))

    # What the buttons do ------------------------------------------------------------------

    def sync(self, read_only: bool) -> None:
        """Markups and notes change the document: on one that can only be viewed, only Copy and Search work."""
        for button in (*self.markups.values(), self.colour, self.note):
            button.setEnabled(not read_only)
        self.adjustSize()

    def mark(self, tool: str, color=None) -> None:
        self._workspace.view.mark_text_selection(tool, color)

    def copy_text(self) -> None:
        view = self._workspace.view
        if view.has_text_selection():
            view.copy_selection()
            self._workspace.notify("Copied.")

    def add_note(self) -> None:
        """A highlight that carries a note: other PDF apps show the note with it, and it shows when pointed at."""
        view = self._workspace.view
        if not view.has_text_selection():
            return
        text = messages.get_multiline_text(self._workspace, "Note on the text",
                                           "The text is highlighted, and the highlight carries the note.",
                                           action="Add note", required=True)
        if text is not None and text.strip():
            view.mark_text_selection("highlight", contents=text.strip())

    def search_text(self) -> None:
        """Every place the selected words occur, in the Search panel."""
        workspace = self._workspace
        text = " ".join(workspace.view.selected_text().split())[:SEARCH_CHARS].strip()
        if not text:
            return
        workspace.view.clear_text_selection()  # the results take over, and the bar goes with the selection
        workspace.search.field.setText(text)
        workspace.show_search()
        workspace.search.start()
