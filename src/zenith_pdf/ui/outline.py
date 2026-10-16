"""The document's bookmarks (PDF outline) as a tree."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core.document import Document
from .widgets import muted_label, panel_title

EXPAND_ALL_BELOW = 40  # entries; longer outlines start collapsed
SPOT_ROLE = Qt.ItemDataRole.UserRole + 1  # the spot on the page a bookmark shows, in its PDF space


class OutlinePanel(QWidget):
    page_activated = Signal(int)
    spot_activated = Signal(int, object)  # the page, and the spot on it (PDF space) or None for its top

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self._document = document
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 0)
        layout.setSpacing(6)
        title = panel_title("Bookmarks")
        title.setContentsMargins(8, 0, 8, 0)
        layout.addWidget(title)

        self.empty = muted_label("This document has no bookmarks.")
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(8, 8, 8, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.empty)
        layout.addWidget(self.tree, 1)
        self.reload()

    def reload(self) -> None:
        self.tree.clear()
        entries = self._document.outline()
        parents: list[QTreeWidgetItem] = []
        for entry in entries:
            del parents[entry.level :]
            item = QTreeWidgetItem(parents[-1] if parents else self.tree, [entry.title])
            item.setToolTip(0, entry.title)
            item.setData(0, Qt.ItemDataRole.UserRole, entry.page)
            item.setData(0, SPOT_ROLE, entry.point)
            parents.append(item)
        if len(entries) < EXPAND_ALL_BELOW:
            self.tree.expandAll()
        self.empty.setVisible(not entries)
        self.tree.setVisible(bool(entries))

    def _on_clicked(self, item: QTreeWidgetItem) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.page_activated.emit(page)
            point = item.data(0, SPOT_ROLE)
            self.spot_activated.emit(page, tuple(point) if point is not None else None)
