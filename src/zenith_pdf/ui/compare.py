"""Two versions of a PDF side by side, with what was added (green), removed (red) or changed (amber) lit on both,
a list of the changes, and the two sides scrolling together page by page."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import compare
from ..core.annotations import Annotation
from ..core.compare import Change, Word
from ..core.document import Document
from ..core.i18n import tr
from ..theme import themes
from .annotation_items import paint_annotation
from .widgets import muted_label

GAP = 16  # px between pages
COLOURS = {"added": QColor(34, 197, 94, 90), "removed": QColor(239, 68, 68, 90), "changed": QColor(245, 158, 11, 95)}
NAMES = {"added": "Added", "removed": "Removed", "changed": "Changed"}


class PageColumn(QGraphicsView):
    """One version's pages, one under the other at the column's width, each drawn when it comes into sight with
    its annotations on it: PDFium draws the page alone, as the canvas paints annotations itself."""

    def __init__(self, document: Document, annotations: list[Annotation] | None = None, parent=None):
        super().__init__(parent)
        self.document = document
        self.annotations = list(document.annotations if annotations is None else annotations)
        self.setScene(QGraphicsScene(self))
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(themes.current.canvas))
        self.scale_ = 1.0
        self.tops: list[float] = []
        self._pages: list[QGraphicsPixmapItem] = []
        self._drawn: set[int] = set()
        self._marks: list[tuple[Word, str]] = []
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._draw_visible())

    def lay_out(self) -> None:
        """Fit the pages to the column's width (again, after a resize), and light the changes on them."""
        width = max(self.viewport().width() - 2 * GAP, 100)
        widest = max((self.document.page_size(i)[0] for i in range(self.document.page_count)), default=612.0)
        self.scale_ = width / widest
        scene = self.scene()
        scene.clear()
        self._pages, self._drawn, self.tops = [], set(), []
        y = GAP
        for index in range(self.document.page_count):
            w, h = self.document.page_size(index)
            blank = QPixmap(max(1, round(w * self.scale_)), max(1, round(h * self.scale_)))
            blank.fill(QColor("white"))
            item = QGraphicsPixmapItem(blank)
            item.setPos((width - w * self.scale_) / 2 + GAP, y)
            scene.addItem(item)
            self._pages.append(item)
            self.tops.append(y)
            y += h * self.scale_ + GAP
        scene.setSceneRect(QRectF(0, 0, width + 2 * GAP, y))
        for word, kind in self._marks:
            self._mark(word, kind)
        QTimer.singleShot(0, self._draw_visible)

    def mark(self, words: list[Word], kind: str) -> None:
        for word in words:
            self._marks.append((word, kind))
            self._mark(word, kind)

    def _mark(self, word: Word, kind: str) -> None:
        if word.page >= len(self._pages) or not any(word.rect[2:]):
            return
        x, y, w, h = word.rect
        rect = QGraphicsRectItem(x * self.scale_ - 1, y * self.scale_ - 1, w * self.scale_ + 2, h * self.scale_ + 2,
                                 self._pages[word.page])
        rect.setPen(Qt.PenStyle.NoPen)
        rect.setBrush(QBrush(COLOURS[kind]))

    def _draw_visible(self) -> None:
        if self.document.closed:  # the window closed with a drawing still to come
            return
        area = self.mapToScene(self.viewport().rect()).boundingRect()
        ratio = self.devicePixelRatioF()
        for index, item in enumerate(self._pages):
            if index in self._drawn or not item.sceneBoundingRect().intersects(area.adjusted(0, -400, 0, 400)):
                continue
            picture = self.document.render(index, self.scale_ * ratio)
            on_page = [a for a in self.annotations if a.page == index]
            if on_page:
                painter = QPainter(picture)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.scale(self.scale_ * ratio, self.scale_ * ratio)
                for annotation in on_page:
                    paint_annotation(painter, annotation)
                painter.end()
            pixmap = QPixmap.fromImage(picture)
            pixmap.setDevicePixelRatio(ratio)
            item.setPixmap(pixmap)
            self._drawn.add(index)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self.lay_out)

    def spot(self) -> tuple[int, float]:
        """The page at the top of the column, and how far down it the top is (0 to 1)."""
        y = self.mapToScene(0, 0).y() + 0.5  # the scroll bar moves by whole pixels, page tops fall between them
        index = max(0, max((i for i, top in enumerate(self.tops) if top <= y), default=0))
        height = self._pages[index].boundingRect().height() if self._pages else 1
        return index, max(0.0, y - 0.5 - self.tops[index]) / max(height, 1) if self.tops else 0.0

    def go(self, page: int, fraction: float) -> None:
        if not self.tops:
            return
        page = min(page, len(self.tops) - 1)
        height = self._pages[page].boundingRect().height()
        self.verticalScrollBar().setValue(round(self.tops[page] + fraction * height))

    def show_word(self, word: Word) -> None:
        if word.page < len(self._pages):
            x, y, w, h = word.rect
            area = self._pages[word.page].mapRectToScene(QRectF(x * self.scale_, y * self.scale_, w * self.scale_,
                                                                h * self.scale_))
            self.centerOn(area.center())


class _Worker(QObject):
    done = Signal(list, bool)  # the changes, and whether either version has text at all


class CompareWindow(QWidget):
    """The older version on the left, the newer on the right, the changes in a list beside them."""

    def __init__(self, old: Document, new: Document, parent=None, new_annotations: list[Annotation] | None = None):
        """`new_annotations`: the newer version's annotations when they are not all in its file yet (an open
        document's unsaved ones); else those its file has."""
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(tr(f"Compare: {old.path.name} and {new.path.name}"))
        self.resize(1400, 900)
        self.old, self.new = old, new
        self.changes: list[Change] = []
        self.left, self.right = PageColumn(old), PageColumn(new, new_annotations)
        heading = QHBoxLayout()
        for name, side in ((old.path.name, "Older"), (new.path.name, "Newer")):
            label = QLabel(f"{tr(side)}: {name}")
            label.setObjectName("HomeSection")
            heading.addWidget(label, 1)
        self.status = muted_label("Comparing…")
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.currentRowChanged.connect(self._go)
        previous, following = QPushButton(tr("Previous")), QPushButton(tr("Next"))
        previous.clicked.connect(lambda: self.step(-1))
        following.clicked.connect(lambda: self.step(1))
        buttons = QHBoxLayout()
        buttons.addWidget(previous)
        buttons.addWidget(following)
        side = QWidget()
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.status)
        column.addWidget(self.list, 1)
        column.addLayout(buttons)
        split = QSplitter()
        split.addWidget(self.left)
        split.addWidget(self.right)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 1)
        split.setSizes([560, 560, 280])  # the two versions share the room, the list takes what is left
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.addLayout(heading)
        layout.addWidget(split, 1)
        self._syncing = False
        self.left.verticalScrollBar().valueChanged.connect(lambda _v: self._sync(self.left, self.right))
        self.right.verticalScrollBar().valueChanged.connect(lambda _v: self._sync(self.right, self.left))
        self._worker = _Worker()
        self._worker.done.connect(self._show)
        self._stop = False
        self._thread = threading.Thread(target=self._compare, daemon=True, name="compare")
        self._thread.start()

    def _compare(self) -> None:
        old = compare.words(self.old, stop=lambda: self._stop)
        new = compare.words(self.new, stop=lambda: self._stop)
        if not self._stop:
            self._worker.done.emit(compare.compare(old, new), bool(old or new))

    def _show(self, changes: list[Change], has_text: bool = True) -> None:
        if self._stop:  # closed before the comparison was done: the window may be gone already
            return
        self.changes = changes
        for change in changes:
            self.left.mark(change.old, change.kind)
            self.right.mark(change.new, change.kind)
            item = QListWidgetItem(tr(f"{NAMES[change.kind]} · p. {change.page + 1}\n{change.summary()}"))
            item.setForeground(QBrush(COLOURS[change.kind].darker(150) if themes.current.name == "light"
                                      else COLOURS[change.kind].lighter(160)))
            self.list.addItem(item)
        found = compare.counts(changes)
        if not has_text:  # scans and pictures: nothing to read, which is not reading the same
            self.status.setText(tr("Neither version has text to compare, only pictures or scans: look at them side "
                                   "by side."))
        else:
            self.status.setText(tr("The two versions read the same." if not changes else
                                f"{found['added']} added, {found['removed']} removed, {found['changed']} changed"))
        self.status.setWordWrap(True)

    def step(self, by: int) -> None:
        if self.changes:
            self.list.setCurrentRow((self.list.currentRow() + by) % len(self.changes))

    def _go(self, row: int) -> None:
        if 0 <= row < len(self.changes):
            change = self.changes[row]
            self._syncing = True
            if change.old:
                self.left.show_word(change.old[0])
            if change.new:
                self.right.show_word(change.new[0])
            self._syncing = False

    def _sync(self, source: PageColumn, target: PageColumn) -> None:
        if self._syncing:
            return
        self._syncing = True
        target.go(*source.spot())
        self._syncing = False

    def closeEvent(self, event) -> None:
        self._stop = True
        self._worker.done.disconnect(self._show)  # nothing more to show, whenever the comparison ends
        self._thread.join(5)  # it stops between pages: then the documents can go
        super().closeEvent(event)
        for document in (self.old, self.new):
            document.close()


def compare_files(parent, older: Path, newer: Document, annotations: list[Annotation] | None = None) -> CompareWindow:
    """Open the comparison of `older` (a file) with `newer` (a copy of an open document's current bytes, with
    `annotations` as it has them now)."""
    window = CompareWindow(Document(older), newer, parent, annotations)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()
    return window
