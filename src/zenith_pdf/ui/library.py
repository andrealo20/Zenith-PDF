"""Markup library: annotations saved once, reused on any page of any document."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import Annotation, union
from ..core.i18n import tr
from ..settings import Settings
from ..theme import outline, qcolor, themes
from . import messages, motion
from .annotation_items import paint_annotation
from .clipboard import from_records, to_records
from .side_panel import CLEAR, HANG, PAD, RowFades, empty_state, panel_header
from .widgets import muted_label

PREVIEW = 52  # logical pixels
THUMB = 48  # the preview as the list shows it, in a rounded frame
INDEX_ROLE = Qt.ItemDataRole.UserRole + 1


class Library(QObject):
    """The saved groups, shared by every open document and kept in the settings."""

    changed = Signal()

    def __init__(self, settings: Settings, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = settings

    def entries(self) -> list[dict]:
        """The saved groups; anything in the settings that isn't one (a hand-edited file) is left out."""
        return [entry for entry in self._settings.markup_library
                if isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("records"), list)]

    def add(self, name: str, annotations: list[Annotation]) -> None:
        entries = self.entries() + [{"name": name, "records": to_records(annotations)}]
        self._settings.markup_library = entries
        self.changed.emit()

    def rename(self, index: int, name: str) -> None:
        entries = self.entries()
        entries[index]["name"] = name
        self._settings.markup_library = entries
        self.changed.emit()

    def remove(self, index: int) -> None:
        entries = self.entries()
        del entries[index]
        self._settings.markup_library = entries
        self.changed.emit()


def preview(records: list[dict], ratio: float) -> QPixmap:
    """The saved annotations drawn small, fitted into a square."""
    annotations = from_records(records, 0, (0.0, 0.0))
    pixmap = QPixmap(round(PREVIEW * ratio), round(PREVIEW * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor("white"))
    if not annotations:
        return pixmap
    _, _, width, height = union(a.bounds() for a in annotations)
    scale = (PREVIEW - 8) / max(width, height, 1.0)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate((PREVIEW - width * scale) / 2, (PREVIEW - height * scale) / 2)
    painter.scale(scale, scale)
    for annotation in annotations:
        paint_annotation(painter, annotation)
    painter.end()
    return pixmap


class LibraryDelegate(QStyledItemDelegate):
    def __init__(self, view: QListWidget):
        super().__init__(view)
        self._fades = RowFades(view)  # the fill under the pointer or the choice comes and goes gently

    def sizeHint(self, option, index) -> QSize:
        return QSize(200, PREVIEW + 12)

    def paint(self, painter: QPainter, option, index) -> None:
        t = themes.current
        rect = option.rect.adjusted(HANG, 2, -4, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            want = qcolor(t.selection)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            want = qcolor(t.hover)
        else:
            want = CLEAR
        shade = self._fades.colour(index.row(), want)
        if shade.alpha():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawPath(outline(QRectF(rect), t.look.item, t.look))
        thumb = index.data(Qt.ItemDataRole.DecorationRole)
        side = THUMB
        target = QRectF(rect.x() + PAD - HANG, rect.y() + (rect.height() - side) / 2, side, side)
        frame = outline(target, t.look.item, t.look)
        painter.save()
        painter.setClipPath(frame)
        painter.drawPixmap(target, thumb, QRectF(thumb.rect()))
        painter.restore()
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(frame)
        painter.setPen(QColor(t.text))
        text = QRect(round(target.right()) + 12, rect.y(), rect.right() - round(target.right()) - 18, rect.height())
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter, option.fontMetrics.elidedText(
            index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight, text.width()))
        painter.restore()


class LibraryPanel(QWidget):
    chosen = Signal(list)  # records to place on the page

    def __init__(self, library: Library, ratio: float, parent=None):
        super().__init__(parent)
        self._library = library
        self._ratio = ratio
        self.list = QListWidget()
        self.list.setItemDelegate(LibraryDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.empty = empty_state("books", "Select annotations on a page and choose Add to library to keep them here.")
        self.hint = muted_label("Click an item to place it on the page.")
        self.hint.setWordWrap(True)  # in most languages it is longer than the panel is wide
        self.hint.setContentsMargins(PAD, 0, PAD, 8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel_header("Markup library"))
        layout.addWidget(self.hint)
        layout.addWidget(self.empty, 1)  # with nothing saved it takes the room: the title stays at the top
        layout.addWidget(self.list, 1)
        library.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        entries = self._library.entries()
        for position, entry in enumerate(entries):
            item = QListWidgetItem(entry["name"])
            item.setData(INDEX_ROLE, position)
            item.setData(Qt.ItemDataRole.DecorationRole, preview(entry["records"], self._ratio))
            self.list.addItem(item)
        self.empty.setVisible(not entries)
        self.hint.setVisible(bool(entries))  # nothing to click yet
        self.list.setVisible(bool(entries))

    def _clicked(self, item: QListWidgetItem) -> None:
        entries = self._library.entries()
        position = item.data(INDEX_ROLE)
        if 0 <= position < len(entries):
            self.chosen.emit(entries[position]["records"])

    def _menu(self, point) -> None:
        item = self.list.itemAt(point)
        if item is None:
            return
        position = item.data(INDEX_ROLE)
        menu = QMenu(self)
        motion.appear_on_show(menu, -motion.RISE)  # it drops out of the pointer
        menu.addAction(tr("Rename…"), lambda: self._rename(position, item.text()))
        menu.addAction(tr("Remove from library"), lambda: self._library.remove(position))
        menu.exec(self.list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def _rename(self, position: int, current: str) -> None:
        name = messages.get_text(self, "Rename", "The name it has in the markup library.", current,
                                 action="Rename", required=True)
        if name and name.strip():
            self._library.rename(position, name.strip())
