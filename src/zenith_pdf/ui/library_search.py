"""Searching inside every PDF opened: the app's indexer, and the results on the home page.

The indexer reads files in a thread of its own: those on the home page a little after the app starts, and each
one as it is opened or saved. The home page's search box asks the index too, and lists the pages that hold
every word typed, each with the words around them; a click opens the file at that page with the words lit.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
)

from ..core.library_index import Hit, LibraryIndex, query_terms
from ..settings import Settings
from ..theme import qcolor, themes
from .widgets import muted_label

RESULTS = 40  # pages listed at most
ROW = 54  # px: the file and page, then the words around the match


def index_path() -> Path:
    from .home import cache_folder

    return cache_folder().parent / "search-index.sqlite3"


class Indexer(QObject):
    """The app's one indexer: a queue of files and the thread that reads them."""

    indexed = Signal(str)  # a file is in the index, or changed in it

    def __init__(self, settings: Settings, parent: QObject | None = None, index: LibraryIndex | None = None):
        super().__init__(parent)
        self._settings = settings
        self.index = index or LibraryIndex(index_path())
        self._queue: list[Path] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = False

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("search_index"))

    def add(self, paths) -> None:
        if not self.enabled:
            return
        with self._lock:
            for path in paths:
                path = Path(path)
                if path not in self._queue and path.suffix.lower() == ".pdf":
                    self._queue.append(path)
            running = self._thread is not None and self._thread.is_alive()
            if not running and self._queue:
                self._stop = False
                self._thread = threading.Thread(target=self._run, daemon=True, name="index")
                self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            with self._lock:
                if not self._queue:
                    break
                path = self._queue.pop(0)
            try:
                if self.index.add(path, stop=lambda: self._stop) >= 0:
                    self.indexed.emit(str(path))
            except Exception:  # a file the index can't take is skipped; the others go on
                continue
        self.index.close()

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop = True
        if self._thread is not None:
            self._thread.join(5)

    def search(self, text: str) -> list[Hit]:
        return self.index.search(text, RESULTS) if self.enabled else []

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
        self.index.clear()


class _HitDelegate(QStyledItemDelegate):
    """A result: the file's name and the page, then the words around the match, the match in bold."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        hit: Hit = index.data(Qt.ItemDataRole.UserRole)
        t = themes.current
        painter.save()
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, qcolor(t.hover))
        rect = option.rect.adjusted(10, 6, -10, -6)
        painter.setPen(qcolor(t.text))
        bold = painter.font()
        bold.setWeight(bold.Weight.DemiBold)
        painter.setFont(bold)
        painter.drawText(rect.adjusted(0, 0, 0, -rect.height() // 2), Qt.AlignmentFlag.AlignLeft,
                         f"{hit.path.stem}   ·   p. {hit.page + 1}")
        painter.restore()
        painter.save()
        words = hit.snippet.replace("&", "&amp;").replace("<", "&lt;").replace("[", "<b>").replace("]", "</b>")
        text = QTextDocument()
        text.setDefaultFont(option.font)
        text.setHtml(f'<span style="color:{t.text_muted}">{words}</span>')
        text.setTextWidth(rect.width())
        painter.translate(rect.left() - 4, rect.top() + rect.height() // 2 - 4)
        text.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW)


class InsideResults(QFrame):
    """Under the home page's files: the pages of every PDF that hold the words searched for."""

    page_requested = Signal(Path, int, str)  # file, page from 0, the words

    def __init__(self, indexer: Indexer | None = None, parent=None):
        super().__init__(parent)
        self.indexer = indexer
        self.query = ""
        title = QLabel("Inside the documents")
        title.setObjectName("HomeSection")
        self.count = muted_label()
        row = QHBoxLayout()
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        row.addSpacing(10)
        row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        self.list = QListWidget()
        self.list.setObjectName("InsideList")
        self.list.setItemDelegate(_HitDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._open)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(row)
        layout.addWidget(self.list, 1)
        self.hide()

    def show_for(self, text: str) -> None:
        """Search the index for `text`; hidden while there is nothing to show."""
        self.query = text.strip()
        self.list.clear()
        if self.indexer is None or not query_terms(self.query) or len(self.query) < 3:
            self.hide()
            return
        hits = self.indexer.search(self.query)
        for hit in hits:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, hit)
            item.setToolTip(str(hit.path))
            self.list.addItem(item)
        files = len({hit.path for hit in hits})
        self.count.setText(f"{len(hits)} page{'s' if len(hits) != 1 else ''} in {files} file{'s' if files != 1 else ''}"
                           if hits else "No page holds all these words")
        self.show()

    def _open(self, item: QListWidgetItem) -> None:
        hit: Hit = item.data(Qt.ItemDataRole.UserRole)
        self.page_requested.emit(hit.path, hit.page, self.query)
