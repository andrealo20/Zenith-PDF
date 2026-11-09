"""The document's bookmarks (PDF outline) as a tree."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core.document import Document
from ..theme import outline, qcolor, themes
from .side_panel import CLEAR, RowFades, empty_state, panel_header

EXPAND_ALL_BELOW = 40  # entries; longer outlines start collapsed
SPOT_ROLE = Qt.ItemDataRole.UserRole + 1  # the spot on the page a bookmark shows, in its PDF space
# The sheet's own fills for a chosen or pointed-at row are switched off (a widget's sheet outranks the app's): the
# tree paints them, as one shape across the whole row.
NO_SHEET_FILLS = "QTreeView::item:selected, QTreeView::item:hover { background: transparent; }"


class _Tree(QTreeWidget):
    """The bookmarks. The fill of a row, the pointer's or the choice's, is one rounded shape from the row's left edge
    to its right, indent included, and eases in and out. The style sheet paints it in two pieces, the indent apart
    from the text, and snaps it."""

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setStyleSheet(NO_SHEET_FILLS)
        self._fades = RowFades(self)
        self._hover = 0  # the id of the row under the pointer
        self.verticalScrollBar().valueChanged.connect(self._follow_scroll)

    def rebuilt(self) -> None:
        """The rows are about to be made again: a new one may stand where the pointed-at one was."""
        self._hover = 0
        self._fades.reset()

    def _point_at(self, point: QPoint) -> None:
        under = self.indexAt(point)
        hover = under.internalId() if under.isValid() else 0
        if hover != self._hover:
            self._hover = hover
            self.viewport().update()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self._point_at(event.position().toPoint())

    def _follow_scroll(self) -> None:
        """The rows moved under a pointer that did not: the row it is on now is the one that lights."""
        if self.viewport().underMouse():
            self._point_at(self.viewport().mapFromGlobal(QCursor.pos()))

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.Leave and self._hover:  # also onto the scroll bar, which is not the tree's rows
            self._hover = 0
            self.viewport().update()
        return super().viewportEvent(event)

    def drawRow(self, painter: QPainter, option, index) -> None:
        t = themes.current
        row = index.internalId()
        if self.selectionModel().isSelected(index):
            want = qcolor(t.selection)
        elif row == self._hover:
            want = qcolor(t.hover)
        else:
            want = CLEAR
        shade = self._fades.colour(row, want)
        if shade.alpha():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            area = QRectF(3, option.rect.y() + 1, self.viewport().width() - 6, option.rect.height() - 2)
            painter.drawPath(outline(area, t.look.item, t.look))
            painter.restore()
        super().drawRow(painter, option, index)


class OutlinePanel(QWidget):
    page_activated = Signal(int)
    spot_activated = Signal(int, object)  # the page, and the spot on it (PDF space) or None for its top

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self._document = document
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel_header("Bookmarks"))

        self.empty = empty_state("bookmark-simple", "This document has no bookmarks.")
        self.tree = _Tree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)  # its top level reads at the title's own left edge
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.empty, 1)  # with no bookmarks it takes the room: the title stays at the top
        layout.addWidget(self.tree, 1)
        self.reload()

    def reload(self) -> None:
        self.tree.rebuilt()
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
