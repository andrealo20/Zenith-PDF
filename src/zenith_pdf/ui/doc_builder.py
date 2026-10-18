"""Doc Builder: combine pages from several PDFs, reorder, rotate, drop and add pages, then build.

The result is a list of entries, each a page of a source file or a blank page.
Each source has a colour; its consecutive pages show as one card until expanded.
Undo and redo keep snapshots of the (small, immutable) entry list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import builder as core
from ..core.document import Document, PdfiumError
from ..core.i18n import tr
from ..core.render import RenderPool
from ..icons import icon
from ..theme import themes
from . import messages
from .help import attach_help
from .widgets import IconButton, icon_pixmap, muted_label, separator

COLOURS = ["#8b7cf6", "#22b8cf", "#f59f43", "#ef6f8f", "#45bf7f", "#e3b532", "#5b8cff", "#c06bdc"]
ROWS_MIME = "application/x-zenith-pdf-builder-rows"
SOURCE_ROLE = Qt.ItemDataRole.UserRole + 1


@dataclass
class SourceFile:
    key: str
    path: Path
    document: Document
    colour: str
    added: datetime


@dataclass(frozen=True)
class Row:
    """One card in the grid: a single page, or a collapsed run of one source's pages."""

    entries: tuple[int, ...]  # indices into the entry list
    group: bool


class Snapshot(QUndoCommand):
    """Undo by restoring the entry list (and source order) as they were."""

    def __init__(self, builder: DocBuilder, before, after, text: str):
        super().__init__(text)
        self._builder, self._before, self._after = builder, before, after
        self._first = True

    def redo(self) -> None:
        if self._first:
            self._first = False  # the change is already made when pushed
        else:
            self._builder._restore(self._after)

    def undo(self) -> None:
        self._builder._restore(self._before)


class BuilderModel(QAbstractListModel):
    def __init__(self, builder: DocBuilder):
        super().__init__(builder)
        self._builder = builder
        self.rows: list[Row] = []

    def refresh(self) -> None:
        self.beginResetModel()
        self.rows = self._builder.rows()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()]
        return None

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
        mime.setData(ROWS_MIME, QByteArray(",".join(str(i.row()) for i in sorted(indexes, key=QModelIndex.row)).encode()))
        return mime

    def dropMimeData(self, data, action, row, column, parent) -> bool:
        if data.hasFormat(ROWS_MIME):
            rows = [int(r) for r in bytes(data.data(ROWS_MIME)).decode().split(",") if r]
            before = row if row >= 0 else (parent.row() if parent.isValid() else len(self.rows))
            self._builder.move_rows(rows, before)
        return False  # the builder rebuilds the rows itself


class CardDelegate(QStyledItemDelegate):
    """A card: page thumbnail on its source's colour, a selection circle, the page range,
    and for collapsed groups the file name and a button to expand."""

    def __init__(self, builder: DocBuilder):
        super().__init__(builder)
        self._builder = builder

    def sizeHint(self, option, index) -> QSize:
        size = self._builder.thumb_size
        return QSize(size + 40, round(size * 1.3) + 46)

    def _toggle_rect(self, rect: QRect) -> QRect:
        return QRect(rect.right() - 26, rect.center().y() - 13, 26, 26)

    def paint(self, painter: QPainter, option, index) -> None:
        builder = self._builder
        row: Row = index.data(Qt.ItemDataRole.UserRole)
        entry = builder.entries[row.entries[0]]
        source = builder.sources.get(entry.source) if entry.source else None
        colour = QColor(source.colour if source else themes.current.text_muted)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        card = option.rect.adjusted(6, 6, -6, -6)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        background = QColor(colour)
        background.setAlpha(70 if selected else 42)
        painter.setPen(QPen(colour, 2.5) if selected else Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(card, 10, 10)

        # The page, kept upright within the card and turned by the entry's rotation.
        width, height = builder.page_size(entry)
        if entry.rotation % 180:
            width, height = height, width
        room = QRectF(card.adjusted(16, 30, -16, -34 if row.group else -14))
        scale = min(room.width() / width, room.height() / height)
        page = QRectF(0, 0, width * scale, height * scale)
        page.moveCenter(room.center())
        painter.fillRect(page, Qt.GlobalColor.white)
        pixmap = builder.thumbnail(entry)
        if pixmap is not None:
            painter.save()
            painter.translate(page.center())
            painter.rotate(entry.rotation)
            target = QRectF(0, 0, page.height(), page.width()) if entry.rotation % 180 else QRectF(0, 0, page.width(), page.height())
            target.moveCenter(QPointF(0, 0))
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            painter.restore()

        # Selection circle, top left
        circle = QRectF(card.x() + 10, card.y() + 8, 18, 18)
        painter.setPen(QPen(QColor("white"), 1.5))
        painter.setBrush(colour if selected else QColor(0, 0, 0, 60))
        painter.drawEllipse(circle)
        if selected:
            icon("check", "white").paint(painter, circle.adjusted(3, 3, -3, -3).toRect())

        # Page or range, top right
        label = builder.row_label(row)
        font = QFont(option.font)
        font.setPointSizeF(font.pointSizeF() * 0.85)
        font.setBold(True)
        painter.setFont(font)
        text_width = painter.fontMetrics().horizontalAdvance(label) + 14
        pill = QRectF(card.right() - text_width - 8, card.y() + 8, text_width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawRoundedRect(pill, 9, 9)
        painter.setPen(colour.darker(150))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, tr(label))

        if row.group and source is not None:
            strip = QRectF(card.x(), card.bottom() - 28, card.width(), 28)
            path = QPainterPath()
            path.addRoundedRect(strip, 10, 10)
            painter.fillPath(path, QColor(20, 20, 24, 200))
            painter.setPen(QColor("white"))
            painter.setFont(option.font)
            name = painter.fontMetrics().elidedText(source.path.name, Qt.TextElideMode.ElideMiddle, round(strip.width()) - 16)
            painter.drawText(strip.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, tr(name))
        if entry.source is not None and builder.can_toggle(row):
            toggle = self._toggle_rect(card)
            painter.setPen(QPen(QColor("white"), 1.5))
            painter.setBrush(colour)
            painter.drawEllipse(toggle)
            icon("arrows-out-line-horizontal" if row.group else "arrows-in-line-horizontal", "white").paint(
                painter, toggle.adjusted(5, 5, -5, -5))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            row: Row = index.data(Qt.ItemDataRole.UserRole)
            card = option.rect.adjusted(6, 6, -6, -6)
            if self._toggle_rect(card).contains(event.position().toPoint()) and self._builder.can_toggle(row):
                self._builder.toggle_group(row)
                return True
        return super().editorEvent(event, model, option, index)


class DocBuilder(QWidget):
    open_requested = Signal(str)  # a built file to open in a tab

    def __init__(self, renderer: RenderPool, ratio: float, parent=None):
        super().__init__(parent)
        self.setObjectName("Builder")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAcceptDrops(True)
        self._renderer = renderer
        self._ratio = ratio
        self._token = object()
        self._thumbs: dict[tuple, QPixmap] = {}
        self._pending: set[tuple] = set()
        self.sources: dict[str, SourceFile] = {}
        self.order: list[str] = []  # source keys, as listed on the left
        self.entries: list[core.Entry] = []
        self.expanded: set[str] = set()
        self.thumb_size = 150
        self.undo_stack = QUndoStack(self)
        renderer.rendered.connect(self._on_rendered)
        self._build_ui()
        self.refresh()

    # Interface ----------------------------------------------------------

    def _build_ui(self) -> None:
        # Left: the source files
        open_button = QPushButton(icon("plus"), " Open file")
        open_button.clicked.connect(self.open_dialog)
        sort_button = QPushButton(icon("sort-ascending"), " Sort A-Z")
        sort_button.clicked.connect(self.sort_sources)
        clear_button = QPushButton(tr("Clear"))
        clear_button.clicked.connect(self.clear_all)
        for button, key in ((open_button, "db_open"), (sort_button, "db_sort"), (clear_button, "db_clear")):
            attach_help(button, key)
        top = QHBoxLayout()
        for button in (open_button, sort_button):
            top.addWidget(button)
        top.addStretch()
        top.addWidget(clear_button)
        self.source_list = QListWidget()
        self.source_list.setObjectName("SourceList")
        self.source_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.source_list.setSpacing(2)
        self.source_list.model().rowsMoved.connect(self._on_sources_moved)
        left = QWidget()
        left.setObjectName("Panel")
        left.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        left.setFixedWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.addLayout(top)
        left_layout.addWidget(self.source_list, 1)
        left_layout.addWidget(muted_label("Drag files to change their order."))

        # Right: tools, the result's name, the cards
        tools = QHBoxLayout()
        tools.setSpacing(4)
        undo = IconButton("arrow-u-up-left", "Undo (Ctrl+Z)", help="db_undo")
        undo.clicked.connect(self.undo_stack.undo)
        redo = IconButton("arrow-u-up-right", "Redo (Ctrl+Y)", help="db_redo")
        redo.clicked.connect(self.undo_stack.redo)
        for button, signal in ((undo, self.undo_stack.canUndoChanged), (redo, self.undo_stack.canRedoChanged)):
            button.setEnabled(False)
            signal.connect(button.setEnabled)
        self.selection_tools = []
        def tool(icon_name, text, slot, help, needs_selection=True):
            button = IconButton(icon_name, text, icon_size=18, text=text if text in ("Extract",) else None, help=help)
            button.clicked.connect(slot)
            if needs_selection:
                self.selection_tools.append(button)
            return button
        extract = tool("export", "Extract", self.extract_selected, "db_extract")
        rotate_left = tool("arrow-counter-clockwise", "Rotate counter-clockwise", lambda: self.rotate_selected(-90),
                           "db_rotate_left")
        rotate_right = tool("arrow-clockwise", "Rotate clockwise", lambda: self.rotate_selected(90), "db_rotate_right")
        delete = tool("trash", "Delete", self.delete_selected, "db_delete")
        duplicate = tool("copy-simple", "Duplicate", self.duplicate_selected, "db_duplicate")
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(100, 280)
        self.zoom.setValue(self.thumb_size)
        self.zoom.setFixedWidth(140)
        self.zoom.valueChanged.connect(self._set_thumb_size)
        self.selection_button = QToolButton()
        self.selection_button.setObjectName("MenuButton")  # a button with a caret: the choices are in its menu
        self.selection_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.selection_button)
        menu.addAction(tr("Select all"), lambda: self.grid.selectAll())
        menu.addAction(tr("Select none"), lambda: self.grid.clearSelection())
        menu.addAction(tr("Invert selection"), self._invert_selection)
        self.selection_button.setMenu(menu)
        attach_help(self.selection_button, "db_selection")
        self.build_button = QPushButton(tr("Build"))
        self.build_button.setObjectName("Primary")
        self.build_button.clicked.connect(self.build)
        attach_help(self.build_button, "db_build")
        for widget in (undo, redo, separator(), extract, separator(), rotate_left, rotate_right, delete, duplicate,
                       separator(), IconButton("magnifying-glass-minus", "Smaller", icon_size=16, help="db_smaller"),
                       self.zoom, IconButton("magnifying-glass-plus", "Larger", icon_size=16, help="db_larger"),
                       separator(), self.selection_button):
            tools.addWidget(widget)
        tools.addStretch()
        tools.addWidget(self.build_button)

        self.name = QLineEdit("Combined - 01")
        self.name.setObjectName("BuilderName")
        self.name.textChanged.connect(self._fit_name)
        self._fit_name()
        self.count = muted_label()
        insert = QToolButton()
        insert.setText(tr(" Insert page"))
        insert.setIcon(icon("plus"))
        insert.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        insert.clicked.connect(self.insert_blank)
        attach_help(insert, "db_insert")
        title = QHBoxLayout()
        title.addWidget(self.name)
        title.addWidget(self.count)
        title.addStretch()
        title.addWidget(insert)

        self.model = BuilderModel(self)
        self.grid = QListView()
        self.grid.setObjectName("BuilderGrid")
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(CardDelegate(self))
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Snap)
        self.grid.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.grid.setDragDropMode(QListView.DragDropMode.InternalMove)
        self.grid.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.grid.setSpacing(4)
        self.grid.setUniformItemSizes(True)
        self.empty = QLabel(tr("Open PDF files, or drop them here, to start building."))
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 12, 16, 12)
        right_layout.setSpacing(10)
        right_layout.addLayout(tools)
        right_layout.addLayout(title)
        right_layout.addWidget(self.empty, 1)
        right_layout.addWidget(self.grid, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(left)
        layout.addWidget(right, 1)
        self.grid.selectionModel().selectionChanged.connect(self._sync_selection)

    def refresh(self, select: set[int] | None = None) -> None:
        """Rebuild the cards. `select` names the entries to select afterwards; by default
        the current selection is kept (the entry list must then be unchanged)."""
        self._rebuild_grid(select)
        count = len(self.entries)
        self.count.setText(tr(f"({count} page{'s' if count != 1 else ''})"))
        self.empty.setVisible(not self.entries)
        self.grid.setVisible(bool(self.entries))
        self.build_button.setEnabled(bool(self.entries))
        self._refresh_sources()
        self._sync_selection()

    def _refresh_sources(self) -> None:
        self.source_list.blockSignals(True)
        self.source_list.clear()
        for key in self.order:
            source = self.sources[key]
            pages = sum(1 for e in self.entries if e.source == key)
            item = QListWidgetItem()
            item.setData(SOURCE_ROLE, key)
            item.setSizeHint(QSize(260, 56))
            self.source_list.addItem(item)
            self.source_list.setItemWidget(item, self._source_row(source, pages))
        self.source_list.blockSignals(False)

    def _source_row(self, source: SourceFile, pages: int) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 4, 4, 4)
        handle = QLabel()
        handle.setPixmap(icon_pixmap("dots-six-vertical", 16, themes.current.text_muted))
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(tr(source.path.name))
        name.setToolTip(str(source.path))
        detail = muted_label(f"{source.document.page_count} pages · {pages} in use · added {source.added:%H:%M}")
        text.addWidget(name)
        text.addWidget(detail)
        dot = QLabel()
        swatch = QPixmap(14, 14)
        swatch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(swatch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(source.colour))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 14, 14)
        painter.end()
        dot.setPixmap(swatch)
        more = IconButton("dots-three-vertical", "More", icon_size=16)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(tr("Add all its pages again"), lambda: self.add_pages_of(source.key))
        menu.addAction(tr("Remove from the build"), lambda: self.remove_source(source.key))
        more.setMenu(menu)
        layout.addWidget(handle)
        layout.addLayout(text, 1)
        layout.addWidget(dot)
        layout.addWidget(more)
        return row

    def _fit_name(self, *_) -> None:
        """The name field as wide as its text, so the page count sits right after it."""
        self.name.ensurePolished()  # measure with the style sheet's large bold font, not the default
        self.name.setFixedWidth(min(self.name.fontMetrics().horizontalAdvance(self.name.text()) + 28, 480))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_name()

    def _sync_selection(self, *_) -> None:
        count = len(self.selected_entries())
        self.selection_button.setText(tr(f"{count} selected"))
        for button in self.selection_tools:
            button.setEnabled(count > 0)

    def _rebuild_grid(self, select: set[int] | None = None) -> None:
        # Resetting the model drops the view's selection: carry it over by entry index.
        keep = set(self.selected_entries()) if select is None else select
        self.model.refresh()
        chosen = QItemSelection()
        for row, card in enumerate(self.model.rows):
            if keep.intersection(card.entries):
                chosen.select(self.model.index(row), self.model.index(row))
        self.grid.selectionModel().select(chosen, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _set_thumb_size(self, size: int) -> None:
        self.thumb_size = size
        self._rebuild_grid()

    def _invert_selection(self) -> None:
        selection = self.grid.selectionModel()
        for row in range(self.model.rowCount()):
            selection.select(self.model.index(row), selection.SelectionFlag.Toggle)

    # Rows ---------------------------------------------------------------

    def rows(self) -> list[Row]:
        """Cards to show: runs of a collapsed source become one card."""
        rows: list[Row] = []
        run: list[int] = []
        for index, entry in enumerate(self.entries):
            collapsible = entry.source is not None and entry.source not in self.expanded
            if run and collapsible and self.entries[run[-1]].source == entry.source:
                run.append(index)
                continue
            if run:
                rows.append(Row(tuple(run), len(run) > 1 or self._is_whole(run)))
                run = []
            if collapsible:
                run = [index]
            else:
                rows.append(Row((index,), False))
        if run:
            rows.append(Row(tuple(run), len(run) > 1 or self._is_whole(run)))
        return rows

    def _is_whole(self, run: list[int]) -> bool:
        entry = self.entries[run[0]]
        return self.sources[entry.source].document.page_count == 1

    def can_toggle(self, row: Row) -> bool:
        entry = self.entries[row.entries[0]]
        if entry.source is None:
            return False
        if row.group:
            return True
        return entry.source in self.expanded and self._first_of_run(row.entries[0])

    def _first_of_run(self, index: int) -> bool:
        source = self.entries[index].source
        return index == 0 or self.entries[index - 1].source != source

    def toggle_group(self, row: Row) -> None:
        source = self.entries[row.entries[0]].source
        self.expanded.symmetric_difference_update({source})
        self._rebuild_grid()

    def row_label(self, row: Row) -> str:
        entries = [self.entries[i] for i in row.entries]
        if entries[0].source is None:
            return "blank"
        numbers = [e.page + 1 for e in entries]
        if len(numbers) == 1:
            return str(numbers[0])
        if numbers == list(range(numbers[0], numbers[-1] + 1)):
            return f"{numbers[0]}-{numbers[-1]}"
        return f"{len(numbers)} pages"

    def page_size(self, entry: core.Entry) -> tuple[float, float]:
        if entry.source is None:
            return entry.size
        return self.sources[entry.source].document.page_size(entry.page)

    def thumbnail(self, entry: core.Entry) -> QPixmap | None:
        if entry.source is None:
            return None
        key = (entry.source, entry.page)
        if key not in self._thumbs and key not in self._pending:
            self._pending.add(key)
            document = self.sources[entry.source].document
            width, height = document.page_size(entry.page)
            scale = 280 * self._ratio / max(width, height)  # sharp up to the largest card size
            self._renderer.request((self._token, *key), document, entry.page, scale, (3, len(self._pending)))
        return self._thumbs.get(key)

    def _on_rendered(self, key, scale, image) -> None:
        if isinstance(key, tuple) and key[0] is self._token:
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self._ratio)
            self._thumbs[key[1:]] = pixmap
            self._pending.discard(key[1:])
            self.grid.viewport().update()

    def selected_entries(self) -> list[int]:
        indices = []
        for index in self.grid.selectionModel().selectedIndexes():
            indices.extend(self.model.rows[index.row()].entries)
        return sorted(indices)

    # Editing ------------------------------------------------------------

    def _state(self):
        return tuple(self.order), tuple(self.entries)

    def _restore(self, state) -> None:
        order, entries = state
        self.order, self.entries = list(order), list(entries)
        self.refresh(select=set())

    def _change(self, text: str, order: list[str], entries: list[core.Entry], select=()) -> None:
        """Make an undoable change; `select` lists the entries to select afterwards."""
        before = self._state()
        self.order, self.entries = order, entries
        self.undo_stack.push(Snapshot(self, before, self._state(), text))
        self.refresh(select=set(select))

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add PDF files"), str(Path.home() / "Documents"),
                                                tr("PDF documents (*.pdf)"))
        self.add_files(paths)

    def add_files(self, paths) -> None:
        order, entries = list(self.order), list(self.entries)
        for path in paths:
            source = self._open_source(Path(path))
            if source is None:
                continue
            order.append(source.key)
            entries += [core.Entry(source.key, page) for page in range(source.document.page_count)]
        if len(order) != len(self.order):
            self._change("Add files", order, entries)

    def _open_source(self, path: Path) -> SourceFile | None:
        password = None
        while True:
            try:
                document = Document(path, password)
                break
            except PdfiumError as error:
                if "password" not in str(error).lower():
                    messages.warn(self, "Can't add file", f"{path.name} could not be opened.", detail=str(error))
                    return None
                wrong = password is not None
                password = messages.get_text(
                    self, "Password required",
                    f"That password didn't open {path.name}. Try again." if wrong else f"{path.name} is protected.",
                    password=True, placeholder="Password", action="Open", required=True)
                if password is None:
                    return None
        key = uuid.uuid4().hex
        colour = COLOURS[len(self.sources) % len(COLOURS)]
        source = SourceFile(key, path, document, colour, datetime.now())
        self.sources[key] = source
        return source

    def add_pages_of(self, key: str) -> None:
        pages = [core.Entry(key, p) for p in range(self.sources[key].document.page_count)]
        self._change("Add pages", list(self.order), self.entries + pages)

    def remove_source(self, key: str) -> None:
        self._change("Remove file", [k for k in self.order if k != key], [e for e in self.entries if e.source != key])

    def clear_all(self) -> None:
        if self.entries or self.order:
            self._change("Clear all", [], [])

    def sort_sources(self) -> None:
        order = sorted(self.order, key=lambda k: self.sources[k].path.name.lower())
        self._change("Sort files", order, self._grouped(order))

    def _grouped(self, order: list[str]) -> list[core.Entry]:
        """Entries regrouped to follow the order of the files; blank pages stay at the end."""
        rank = {key: i for i, key in enumerate(order)}
        return sorted(self.entries, key=lambda e: rank.get(e.source, len(rank)))

    def _on_sources_moved(self, *_) -> None:
        order = [self.source_list.item(i).data(SOURCE_ROLE) for i in range(self.source_list.count())]
        if order != self.order:
            self._change("Reorder files", order, self._grouped(order))

    def move_rows(self, rows: list[int], before: int) -> None:
        moving = [i for r in sorted(rows) for i in self.model.rows[r].entries]
        target = len(self.entries) if before >= len(self.model.rows) else self.model.rows[before].entries[0]
        kept = [i for i in range(len(self.entries)) if i not in set(moving)]
        position = sum(1 for i in kept if i < target)
        order = kept[:position] + moving + kept[position:]
        if order != list(range(len(self.entries))):
            moved = range(position, position + len(moving))
            self._change("Move pages", list(self.order), [self.entries[i] for i in order], moved)

    def delete_selected(self) -> None:
        chosen = set(self.selected_entries())
        if chosen:
            self._change("Delete pages", list(self.order), [e for i, e in enumerate(self.entries) if i not in chosen])

    def rotate_selected(self, degrees: int) -> None:
        chosen = set(self.selected_entries())
        if chosen:
            entries = [replace(e, rotation=(e.rotation + degrees) % 360) if i in chosen else e
                       for i, e in enumerate(self.entries)]
            self._change("Rotate pages", list(self.order), entries, chosen)

    def duplicate_selected(self) -> None:
        chosen = self.selected_entries()
        if chosen:
            last = chosen[-1]
            copies = [self.entries[i] for i in chosen]
            entries = self.entries[: last + 1] + copies + self.entries[last + 1 :]
            self._change("Duplicate pages", list(self.order), entries, range(last + 1, last + 1 + len(copies)))

    def insert_blank(self) -> None:
        chosen = self.selected_entries()
        at = chosen[-1] + 1 if chosen else len(self.entries)
        size = self.page_size(self.entries[at - 1]) if at > 0 else (595.28, 841.89)
        blank = core.Entry(None, size=tuple(size))
        self._change("Insert page", list(self.order), self.entries[:at] + [blank] + self.entries[at:], {at})

    # Output -------------------------------------------------------------

    def _sources(self) -> dict[str, core.Source]:
        return {key: core.Source(key, s.path, s.document.password or "") for key, s in self.sources.items()}

    def _save(self, entries: list[core.Entry], default_name: str) -> Path | None:
        folder = self.sources[self.order[0]].path.parent if self.order else Path.home() / "Documents"
        target, _ = QFileDialog.getSaveFileName(self, tr("Save PDF"), str(folder / f"{default_name}.pdf"),
                                                tr("PDF documents (*.pdf)"))
        if not target:
            return None
        try:
            Path(target).write_bytes(core.build(self._sources(), entries))
        except Exception as error:
            messages.warn(self, "Can't build", "The PDF could not be built.", detail=str(error))
            return None
        return Path(target)

    def build(self) -> None:
        target = self._save(self.entries, self.name.text().strip() or "Combined")
        if target is not None:
            self.open_requested.emit(str(target))

    def extract_selected(self) -> None:
        chosen = self.selected_entries()
        if chosen:
            target = self._save([self.entries[i] for i in chosen], f"{self.name.text().strip() or 'Combined'} (extract)")
            if target is not None:
                self.open_requested.emit(str(target))

    # Files dropped on the builder ---------------------------------------

    def dragEnterEvent(self, event) -> None:
        if any(url.toLocalFile().lower().endswith(".pdf") for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")])

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    def close_builder(self) -> None:
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._renderer.rendered.disconnect(self._on_rendered)
        for source in self.sources.values():
            self._renderer.forget(source.document)
            source.document.close()
