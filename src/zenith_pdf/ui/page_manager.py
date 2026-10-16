"""Page manager: every page as a large thumbnail, to reorder by dragging and edit in bulk."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListView, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from ..core.render import RenderPool
from ..theme import themes
from .widgets import IconButton, muted_label, separator

if TYPE_CHECKING:
    from .workspace import Workspace

THUMB = 170  # longest side, in logical pixels
CELL = QSize(THUMB + 40, THUMB + 48)
ROWS_MIME = "application/x-zenith-pdf-page-rows"


class PageManagerModel(QAbstractListModel):
    reordered = Signal(list)  # the new page order

    def __init__(self, workspace: Workspace, renderer: RenderPool, ratio: float, parent=None):
        super().__init__(parent)
        self._workspace = workspace
        self._renderer = renderer
        self._ratio = ratio
        self._token = object()
        self._pixmaps: dict[int, QPixmap] = {}
        renderer.rendered.connect(self._on_rendered)

    @property
    def _document(self):
        return self._workspace.document

    def reload(self) -> None:
        self.beginResetModel()
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._token = object()
        self._pixmaps.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._document.page_count

    def thumb_size(self, row: int) -> tuple[float, float]:
        if not 0 <= row < self._document.page_count:
            return THUMB, THUMB  # a row of a page that just went, until reload() resets the list
        width, height = self._document.page_size(row)
        scale = THUMB / max(width, height)
        return width * scale, height * scale

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = index.row()
        if not 0 <= row < self._document.page_count:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row + 1)
        if role == Qt.ItemDataRole.DecorationRole:
            if row not in self._pixmaps:
                width, _ = self.thumb_size(row)
                scale = width * self._ratio / self._document.page_size(row)[0]
                self._renderer.request((self._token, row), self._document, row, scale, (3, row))
            return self._pixmaps.get(row)
        return None

    def _on_rendered(self, key, scale, image) -> None:
        if isinstance(key, tuple) and key[0] is self._token:
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self._ratio)
            self._pixmaps[key[1]] = pixmap
            index = self.index(key[1])
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    # Drag and drop: the model doesn't move rows itself; it reports the new order and
    # the workspace rebuilds the document (and with it this model) in that order.

    def flags(self, index):
        flags = super().flags(index) | Qt.ItemFlag.ItemIsDropEnabled
        if index.isValid():
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self):
        return [ROWS_MIME]

    def mimeData(self, indexes):
        mime = QMimeData()
        rows = sorted({i.row() for i in indexes})
        mime.setData(ROWS_MIME, QByteArray(",".join(map(str, rows)).encode()))
        return mime

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        if not data.hasFormat(ROWS_MIME):
            return False
        moving = [int(r) for r in bytes(data.data(ROWS_MIME)).decode().split(",") if r]
        before = row if row >= 0 else (parent.row() if parent.isValid() else self.rowCount())
        order = [i for i in range(self.rowCount()) if i not in moving]
        position = sum(1 for i in order if i < before)
        order[position:position] = moving
        if order != list(range(self.rowCount())):
            self.reordered.emit(order)
        return False  # nothing for the view to remove: the reload redraws everything

    def release(self) -> None:
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._renderer.rendered.disconnect(self._on_rendered)


class PageCellDelegate(QStyledItemDelegate):
    def __init__(self, model: PageManagerModel, parent=None):
        super().__init__(parent)
        self._model = model

    def sizeHint(self, option, index) -> QSize:
        return CELL

    def paint(self, painter: QPainter, option, index) -> None:
        if index.data(Qt.ItemDataRole.DisplayRole) is None:
            return  # a page that just went: the list is about to be reset
        t = themes.current
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        width, height = self._model.thumb_size(index.row())
        cell = option.rect
        thumb = QRectF(cell.x() + (cell.width() - width) / 2, cell.y() + 12 + (THUMB - height) / 2, width, height)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(QPen(QColor(t.accent if selected else t.text_muted), 2.5 if selected else 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(thumb.adjusted(-5, -5, 5, 5), 6, 6)
        painter.fillRect(thumb, Qt.GlobalColor.white)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap is not None:
            painter.drawPixmap(thumb, pixmap, QRectF(pixmap.rect()))
        painter.setPen(QColor(t.accent if selected else t.text_muted))
        label = QRect(cell.x(), cell.y() + 12 + THUMB + 6, cell.width(), 20)
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, index.data(Qt.ItemDataRole.DisplayRole))
        painter.restore()


class PageManager(QWidget):
    """Shown in place of the page view; double-click a page to go back to it."""

    closed = Signal(int)  # page to show

    def __init__(self, workspace: Workspace, renderer: RenderPool, ratio: float, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("PageManager")
        self._workspace = workspace
        self.model = PageManagerModel(workspace, renderer, ratio, self)
        self.model.reordered.connect(workspace.actions.reorder)

        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(PageCellDelegate(self.model, self.list))
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Snap)
        self.list.setGridSize(CELL)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(4)
        self.list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QListView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.doubleClicked.connect(lambda index: self.closed.emit(index.row()))
        self.list.selectionModel().selectionChanged.connect(self._sync_buttons)

        title = QLabel("Page manager")
        title.setObjectName("PanelTitle")
        self.count = muted_label()
        actions = workspace.actions
        self._needs_selection = []
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 6)
        bar.addWidget(title)
        bar.addSpacing(8)
        bar.addWidget(self.count)
        bar.addStretch()
        for icon_name, tip, slot, needs, help in (
            ("arrow-counter-clockwise", "Rotate counter-clockwise", lambda: actions.rotate_pages(self.selected(), -90),
             True, "pm_rotate_left"),
            ("arrow-clockwise", "Rotate clockwise", lambda: actions.rotate_pages(self.selected(), 90), True,
             "pm_rotate_right"),
            ("copy-simple", "Duplicate", self.duplicate_selected, True, "pm_duplicate"),
            ("export", "Save selected pages as a new PDF", lambda: actions.extract_pages(self.selected()), True,
             "pm_extract"),
            ("trash", "Delete", lambda: actions.delete_pages(self.selected()), True, "pm_delete"),
            (None, None, None, False, None),
            ("file-plus", "Add a blank page after the selection",
             lambda: actions.add_page(self.selected()[-1] if self.selected() else workspace.document.page_count - 1),
             False, "pm_add_blank"),
            ("file-pdf", "Insert pages from a PDF file",
             lambda: actions.insert_pdf(self.selected()[-1] if self.selected() else workspace.document.page_count - 1),
             False, "pm_insert_pdf"),
        ):
            if icon_name is None:
                bar.addWidget(separator())
                continue
            button = IconButton(icon_name, tip, help=help)
            button.clicked.connect(slot)
            bar.addWidget(button)
            if needs:
                self._needs_selection.append(button)
        bar.addWidget(separator())
        done = IconButton("x", "Back to the document (Esc)", help="pm_close")
        done.clicked.connect(lambda: self.closed.emit(self.current_page()))
        bar.addWidget(done)

        hint = muted_label("Drag pages to reorder them. Ctrl or Shift selects several.")
        hint.setContentsMargins(16, 0, 16, 8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(hint)
        layout.addWidget(self.list, 1)
        self.reload()

    def selected(self) -> list[int]:
        return sorted(i.row() for i in self.list.selectionModel().selectedIndexes())

    def duplicate_selected(self) -> None:
        """Copy every selected page, in one step: the copies follow the last of them, in order."""
        pages = self.selected()
        if not pages:
            return
        workspace = self._workspace
        if len(pages) == 1:
            workspace.actions.duplicate(pages[0])
            return
        document, after = workspace.document, pages[-1]
        done = workspace.edit_structure(
            f"Duplicate {len(pages)} pages",
            # copy_pages keeps the copies' links to the pages left behind, as Duplicate page does for one.
            lambda annotations: document.insert_pdf(annotations, after + 1, document.copy_pages(annotations, pages)))
        if done:
            workspace.view.goto_page(after + 1)
            self.select_rows(range(after + 1, after + 1 + len(pages)))

    def select_rows(self, rows) -> None:
        selection = QItemSelection()
        for row in rows:
            index = self.model.index(row)
            selection.select(index, index)
        model = self.list.selectionModel()
        model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        rows = list(rows)
        if rows:
            model.setCurrentIndex(self.model.index(rows[0]), QItemSelectionModel.SelectionFlag.NoUpdate)

    def current_page(self) -> int:
        selected = self.selected()
        return selected[0] if selected else self._workspace.view.current_page

    def reload(self, select: int | None = None) -> None:
        self.model.reload()
        count = self._workspace.document.page_count
        self.count.setText(f"{count} page{'s' if count != 1 else ''}")
        if select is not None:
            self.list.setCurrentIndex(self.model.index(min(select, count - 1)))
        self._sync_buttons()

    def _sync_buttons(self, *_) -> None:
        has = bool(self.selected())
        for button in self._needs_selection:
            button.setEnabled(has and not self._workspace.document.read_only)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit(self.current_page())
        elif event.key() == Qt.Key.Key_Delete and self.selected():
            self._workspace.actions.delete_pages(self.selected())
        else:
            super().keyPressEvent(event)

    def release(self) -> None:
        self.model.release()
