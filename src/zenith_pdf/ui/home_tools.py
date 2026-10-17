"""The home page's list of tools: what Zenith PDF can make and do to a PDF, by group, each with its own colour."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..core.i18n import tr
from ..icons import icon
from ..theme import themes
from .widgets import muted_label

# Not PDF tools, but where making a document starts: the home page's own actions.
CREATE = [
    ("new", "file-plus", "New document", "Blank, lined, grid or from a template"),
    ("builder", "squares-four", "Doc Builder", "Join PDFs, pick their pages"),
    ("compare", "git-diff", "Compare two versions", "What was added, removed or changed"),
]
TILE_WIDTH = 280  # the narrowest a column gets before the list takes one column less


class HomeTool(QFrame):
    """One tool of the list: its icon in the colour of what it does, its name and a line about it."""

    clicked = Signal(str)

    def __init__(self, key: str, icon_name: str, title: str, detail: str, group: str, parent=None):
        super().__init__(parent)
        self.key, self.icon_name, self.title, self.detail, self.group = key, icon_name, title, detail, group
        self.setObjectName("QuickCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr(detail))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        self.glyph = QLabel()
        self.glyph.setFixedSize(26, 26)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name = QLabel(tr(title))
        self.name.setObjectName("PanelRowText")
        caption = muted_label(detail)
        caption.setWordWrap(True)
        text.addWidget(self.name)
        text.addWidget(caption)
        text.addStretch()
        layout.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        self.recolour()

    def recolour(self) -> None:
        ratio = self.devicePixelRatioF()
        self.glyph.setPixmap(icon(self.icon_name).pixmap(QSize(26, 26), ratio))  # the colour of what it does

    def matches(self, words: list[str]) -> bool:
        text = f"{self.title} {self.detail} {self.group} {tr(self.title)} {tr(self.detail)} {tr(self.group)}".lower()
        return all(word in text for word in words)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.key)


class ToolList(QScrollArea):
    """Every tool, grouped: creating, then the PDF tools by their groups. As many columns as fit."""

    tool_requested = Signal(str)  # a PDF tool's key, or "new", "builder" or "compare"

    def __init__(self, parent=None):
        super().__init__(parent)
        from .pdf_tools import GROUPS, TOOLS

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        body = QWidget()
        self._column = QVBoxLayout(body)
        self._column.setContentsMargins(0, 0, 6, 0)
        self._column.setSpacing(8)
        self.groups: list[tuple[QLabel, QGridLayout, list[HomeTool]]] = []
        listed = [("Create", CREATE)] + [(group, [(t.key, t.icon, t.title, t.description) for t in TOOLS
                                                   if t.group == group]) for group in GROUPS]
        for number, (group, tools) in enumerate(listed):
            title = QLabel(tr(group))
            title.setObjectName("SectionTitle")
            title.setContentsMargins(0, 0 if number == 0 else 10, 0, 2)
            grid = QGridLayout()
            grid.setSpacing(10)
            tiles = []
            for key, icon_name, name, detail in tools:
                tile = HomeTool(key, icon_name, name, detail, group)
                tile.clicked.connect(self.tool_requested)
                tiles.append(tile)
            self._column.addWidget(title)
            self._column.addLayout(grid)
            self.groups.append((title, grid, tiles))
        self.empty = muted_label("No tool matches.")
        self.empty.hide()
        self._column.addWidget(self.empty)
        self._column.addStretch()
        self.setWidget(body)
        self._columns = 0
        self._lay_out(3)
        themes.changed.connect(self._recolour)

    def tiles(self) -> list[HomeTool]:
        return [tile for _, _, tiles in self.groups for tile in tiles]

    def count(self) -> int:
        return len(self.tiles())

    def filter(self, text: str) -> int:
        """Show the tools whose name, line or group has every word; how many show."""
        words = text.lower().split()
        shown = 0
        for title, _, tiles in self.groups:
            here = [tile for tile in tiles if tile.matches(words)]
            for tile in tiles:
                tile.setVisible(tile in here)
            title.setVisible(bool(here))
            shown += len(here)
        self._columns = 0  # hidden tiles leave no gaps: lay the rest out again
        self._lay_out(self._fitting())
        self.empty.setVisible(not shown)
        return shown

    def _fitting(self) -> int:
        return max(1, min(3, self.viewport().width() // TILE_WIDTH))

    def _lay_out(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for _, grid, tiles in self.groups:
            for tile in tiles:
                grid.removeWidget(tile)
            for place, tile in enumerate(t for t in tiles if not t.isHidden()):
                grid.addWidget(tile, place // columns, place % columns)
            for column in range(3):
                grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._lay_out(self._fitting())

    def _recolour(self, *_) -> None:
        for tile in self.tiles():
            tile.recolour()
