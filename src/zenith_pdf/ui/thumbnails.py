"""Page thumbnails, rendered lazily as they scroll into view."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget

from ..core.document import Document
from ..core.render import RenderPool
from ..theme import themes
from .annotation_items import paint_annotation
from .widgets import panel_title

THUMB_WIDTH = 120
THUMB_MAX_HEIGHT = 180
PADDING = 10
LABEL_HEIGHT = 22
PAGE_SIZE_ROLE = Qt.ItemDataRole.UserRole + 1


def _thumb_size(page_size: tuple[float, float]) -> tuple[float, float]:
    width, height = (max(side, 1.0) for side in page_size)  # 0 x 0: a CropBox outside the MediaBox
    scale = min(THUMB_WIDTH / width, THUMB_MAX_HEIGHT / height)
    return width * scale, height * scale


class ThumbnailModel(QAbstractListModel):
    """Page thumbnails with the annotations drawn on, kept up to date as annotations change.

    PDFium renders the page alone (the canvas draws annotations itself), so each
    thumbnail is that rendering with the page's annotations painted over it.
    """

    def __init__(self, document: Document, renderer: RenderPool, device_pixel_ratio: float, store=None, parent=None):
        super().__init__(parent)
        self._document = document
        self._renderer = renderer
        self._ratio = device_pixel_ratio
        self._store = store
        self._token = object()
        self._pixmaps: dict[int, QPixmap] = {}
        self._bases: dict[int, QImage] = {}  # the page as rendered, before annotations
        self._pending: set[int] = set()
        self._labels: dict[int, str] = {}
        self._dirty: set[int] = set()
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(150)
        self._redraw.timeout.connect(self._recompose)
        renderer.rendered.connect(self._on_rendered)
        if store is not None:
            store.added.connect(lambda a: self._touch(a.page))
            store.removed.connect(lambda a: self._touch(a.page))
            store.changed.connect(lambda old, new: (self._touch(old.page), self._touch(new.page)))
            store.reordered.connect(lambda: self._touch(*self._bases))
            store.reset.connect(lambda: self._touch(*self._bases))

    def _touch(self, *pages: int) -> None:
        self._dirty.update(pages)
        self._redraw.start()

    def _compose(self, row: int) -> QPixmap:
        image = self._bases[row].copy()
        if self._store is not None:
            annotations = [a for a in self._store.all() if a.page == row]
            if annotations:
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                scale = image.width() / max(self._document.page_size(row)[0], 1.0)
                painter.scale(scale, scale)
                for annotation in annotations:
                    paint_annotation(painter, annotation)
                painter.end()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(self._ratio)
        return pixmap

    def _recompose(self) -> None:
        for row in sorted(self._dirty):
            if row in self._bases and row < self.rowCount():
                self._pixmaps[row] = self._compose(row)
                index = self.index(row)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        self._dirty.clear()

    def reload(self) -> None:
        """Start over after the pages changed."""
        self.beginResetModel()
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._token = object()  # thumbnails still on their way show old pages
        self._pixmaps.clear()
        self._bases.clear()
        self._pending.clear()
        self._labels.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._document.page_count

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        row = index.row()
        if not 0 <= row < self._document.page_count:
            return None  # a row the list still has from before the pages changed, until reload() resets it
        if role == Qt.ItemDataRole.DisplayRole:
            if row not in self._labels:
                self._labels[row] = self._document.page_label(row)
            return self._labels[row]
        if role == Qt.ItemDataRole.DecorationRole:
            # data() is only asked for rows being painted, which makes this the lazy-load hook.
            if row not in self._pixmaps and row not in self._pending:
                self._pending.add(row)
                width, _ = _thumb_size(self._document.page_size(row))
                scale = width * self._ratio / max(self._document.page_size(row)[0], 1.0)
                self._renderer.request((self._token, row), self._document, row, scale, (2, row))
            return self._pixmaps.get(row)
        if role == PAGE_SIZE_ROLE:
            return self._document.page_size(row)
        return None

    def _on_rendered(self, key, scale, image) -> None:
        if not isinstance(key, tuple) or key[0] is not self._token:
            return
        row = key[1]
        self._bases[row] = image
        self._pixmaps[row] = self._compose(row)
        self._pending.discard(row)
        index = self.index(row)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def release(self) -> None:
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._renderer.rendered.disconnect(self._on_rendered)


class ThumbnailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        size = index.data(PAGE_SIZE_ROLE)
        height = _thumb_size(size)[1] if size is not None else THUMB_MAX_HEIGHT
        return QSize(THUMB_WIDTH + 2 * PADDING, round(height) + LABEL_HEIGHT + 2 * PADDING)

    def paint(self, painter: QPainter, option, index) -> None:
        if index.data(PAGE_SIZE_ROLE) is None:
            return  # a page that just went: the list is about to be reset
        t = themes.current
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        width, height = _thumb_size(index.data(PAGE_SIZE_ROLE))
        rect = option.rect
        thumb = QRectF(rect.x() + (rect.width() - width) / 2, rect.y() + PADDING, width, height)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(QPen(QColor(t.accent if selected else t.text_muted), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(thumb.adjusted(-4, -4, 4, 4), 5, 5)

        painter.fillRect(thumb, Qt.GlobalColor.white)
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if pixmap is not None:
            painter.drawPixmap(thumb, pixmap, QRectF(pixmap.rect()))

        label_rect = QRect(rect.x(), round(thumb.bottom()) + 4, rect.width(), LABEL_HEIGHT)
        painter.setPen(QColor(t.accent if selected else t.text_muted))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, index.data(Qt.ItemDataRole.DisplayRole))
        painter.restore()


class ThumbnailPanel(QWidget):
    page_activated = Signal(int)

    def __init__(self, document: Document, renderer: RenderPool, device_pixel_ratio: float, store=None, parent=None):
        super().__init__(parent)
        self._syncing = False
        self.model = ThumbnailModel(document, renderer, device_pixel_ratio, store, self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(ThumbnailDelegate(self.list))
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.selectionModel().currentChanged.connect(self._on_current_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(6)
        title = panel_title("Pages")
        title.setContentsMargins(16, 0, 16, 0)
        layout.addWidget(title)
        layout.addWidget(self.list)
        self.set_current_page(0)

    def reload(self) -> None:
        current = self.list.currentIndex().row()
        self.model.reload()  # a reset forgets the current row, and with it the highlight
        if current >= 0 and self.model.rowCount():
            self.set_current_page(min(current, self.model.rowCount() - 1))

    def set_current_page(self, page: int) -> None:
        self._syncing = True
        try:
            index = self.model.index(page)
            self.list.setCurrentIndex(index)
            self.list.scrollTo(index, QListView.ScrollHint.EnsureVisible)
        finally:
            self._syncing = False  # else a later click on a thumbnail would go nowhere

    def _on_current_changed(self, current: QModelIndex, _previous) -> None:
        if not self._syncing and current.isValid():
            self.page_activated.emit(current.row())

    def release(self) -> None:
        self.model.release()
