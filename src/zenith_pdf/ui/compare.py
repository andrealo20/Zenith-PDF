"""Two versions of a PDF side by side, with what was added (green), removed (red) or changed (amber) lit on both,
a list of the changes, and the two sides scrolling together page by page."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
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
    QSplitterHandle,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core import compare
from ..core.annotations import Annotation
from ..core.compare import Change, Word
from ..core.document import Document
from ..core.i18n import tr
from ..icons import icon, readable
from ..theme import outline, qcolor, themes
from . import motion
from .annotation_items import paint_annotation

GAP = 16  # px between pages
ROW = 56  # px: a change in the list
COLOURS = {"added": QColor(34, 197, 94, 90), "removed": QColor(239, 68, 68, 90), "changed": QColor(245, 158, 11, 95)}
NAMES = {"added": "Added", "removed": "Removed", "changed": "Changed"}
LIT = 170  # alpha of the words of the change picked in the list


def _ring() -> QColor:
    """The accent, deepened until it shows on the white of a page, which is white in every theme."""
    accent = QColor(themes.current.accent)
    return QColor(readable((accent.redF(), accent.greenF(), accent.blueF()), "white", "black", 3.0))


def _ink(kind: str, ground: QColor) -> QColor:
    """The word for a kind of change: its colour, deepened until it reads on `ground`, the row it stands on."""
    base = QColor(COLOURS[kind])
    return QColor(readable((base.redF(), base.greenF(), base.blueF()), ground.name(), themes.current.text, 4.5))


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
        self._marks: list[tuple[Word, str, int | None]] = []
        self._lit: dict[int, list[tuple[QGraphicsRectItem, str]]] = {}  # the marks of each change, to light them
        self._focus: int | None = None  # the change lit now
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._draw_visible())

    def restyle(self) -> None:
        """The theme changed: the canvas behind the pages, and the outline of the change that is lit."""
        self.setBackgroundBrush(QColor(themes.current.canvas))
        self.focus(self._focus)

    def lay_out(self) -> None:
        """Fit the pages to the column's width (again, after a resize), and light the changes on them."""
        width = max(self.viewport().width() - 2 * GAP, 100)
        widest = max((self.document.page_size(i)[0] for i in range(self.document.page_count)), default=612.0)
        self.scale_ = width / widest
        scene = self.scene()
        scene.clear()
        self._pages, self._drawn, self.tops, self._lit = [], set(), [], {}
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
        for word, kind, change in self._marks:
            self._mark(word, kind, change)
        QTimer.singleShot(0, self, self._draw_visible)  # with the view as context: dropped if it goes first

    def mark(self, words: list[Word], kind: str, change: int | None = None) -> None:
        """Tint `words`; `change` is the number the list gives their change, to light them when it is picked."""
        for word in words:
            self._marks.append((word, kind, change))
            self._mark(word, kind, change)

    def _mark(self, word: Word, kind: str, change: int | None) -> None:
        if word.page >= len(self._pages) or not any(word.rect[2:]):
            return
        x, y, w, h = word.rect
        rect = QGraphicsRectItem(x * self.scale_ - 1, y * self.scale_ - 1, w * self.scale_ + 2, h * self.scale_ + 2,
                                 self._pages[word.page])
        rect.setPen(Qt.PenStyle.NoPen)
        rect.setBrush(QBrush(COLOURS[kind]))
        if change is not None:
            self._lit.setdefault(change, []).append((rect, kind))
            if change == self._focus:
                self._light(rect, kind, True)

    @staticmethod
    def _light(rect: QGraphicsRectItem, kind: str, on: bool) -> None:
        """The words of the picked change: a stronger tint, in an outline in the accent colour."""
        tint = QColor(COLOURS[kind])
        if on:
            tint.setAlpha(LIT)
        rect.setBrush(QBrush(tint))
        rect.setPen(QPen(_ring(), 1.5) if on else Qt.PenStyle.NoPen)

    def focus(self, change: int | None) -> None:
        """Light the words of change number `change` (None: of none)."""
        for number, on in ((self._focus, False), (change, True)):
            for rect, kind in self._lit.get(number, ()):
                self._light(rect, kind, on)
        self._focus = change

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
        QTimer.singleShot(0, self, self.lay_out)

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
    ended = Signal()  # the thread is over, with its changes or without: a page it could not read


class _Handle(QSplitterHandle):
    """The grip between two columns: a hairline down its middle, which takes the text colour under the pointer."""

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._heat = motion.Smooth(self)
        self._heat.changed.connect(self.update)

    def enterEvent(self, event) -> None:
        self._heat.go(1.0, motion.FAST)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._heat.go(0.0, motion.NORMAL)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setPen(QPen(motion.mixed(QColor(t.border), QColor(t.text_muted), self._heat.value), 1.0))
        x = self.width() // 2
        painter.drawLine(x, 4, x, self.height() - 4)


class _Splitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:
        return _Handle(self.orientation(), self)


class _Busy(QWidget):
    """A thin bar with a light running along it, for as long as the comparison is not done. With motion off it is
    not shown at all: the words "Comparing…" say it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._run = QVariantAnimation(self)
        self._run.setStartValue(0.0)
        self._run.setEndValue(1.0)
        self._run.setDuration(1100)
        self._run.setLoopCount(-1)
        self._run.setEasingCurve(motion.EASE_IN_OUT)
        self._run.valueChanged.connect(self.update)

    def start(self) -> None:
        self.setVisible(motion.enabled())
        if motion.enabled():
            self._run.start()

    def stop(self) -> None:
        self._run.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(t.track))
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        length = self.width() * 0.3
        left = (self.width() + length) * float(self._run.currentValue() or 0.0) - length
        painter.setClipRect(self.rect())
        painter.setBrush(QColor(t.accent))
        painter.drawRoundedRect(QRectF(left, 0, length, self.height()), 1.5, 1.5)


class _ChangeRows(QStyledItemDelegate):
    """A change in the list: a bar in its colour, what kind of change and where, and what it says."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW)

    def paint(self, painter, option, index) -> None:
        change: Change = index.data(Qt.ItemDataRole.UserRole)
        t = themes.current
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = QRectF(option.rect).adjusted(2, 1, -2, -1)
        fill = (qcolor(t.selection) if option.state & QStyle.StateFlag.State_Selected
                else qcolor(t.hover) if option.state & QStyle.StateFlag.State_MouseOver else None)
        painter.setPen(Qt.PenStyle.NoPen)
        ground = QColor(t.window)
        if fill is not None:
            painter.setBrush(fill)
            painter.drawPath(outline(row, t.look.item, t.look))
            ground = motion.mixed(ground, QColor(fill.rgb()), fill.alphaF())  # what the words are read on
        colour = QColor(COLOURS[change.kind])
        colour.setAlpha(255)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(row.left() + 8, row.top() + 10, 3, row.height() - 20), 1.5, 1.5)
        left, right = int(row.left()) + 22, int(row.right()) - 10
        small = QFont(option.font)
        small.setPointSizeF(small.pointSizeF() * 0.9)
        small.setBold(True)
        painter.setFont(small)
        kind = tr(NAMES[change.kind])
        painter.setPen(_ink(change.kind, ground))
        top = int(row.top()) + 9
        painter.drawText(left, top, right - left, 16, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, kind)
        after = left + painter.fontMetrics().horizontalAdvance(kind) + 8
        small.setBold(False)
        painter.setFont(small)
        painter.setPen(QColor(t.text_muted))
        painter.drawText(after, top, right - after, 16, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         tr(f"p. {change.page + 1}"))
        painter.setFont(option.font)
        painter.setPen(QColor(t.text))
        mode = Qt.TextElideMode.ElideMiddle if change.kind == "changed" else Qt.TextElideMode.ElideRight
        text = painter.fontMetrics().elidedText(change.summary(), mode, right - left)
        painter.drawText(left, top + 17, right - left, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         text)
        painter.restore()


class _Name(QLabel):
    """A file's name that gives way to the room there is, with an ellipsis; pointed at, it says all of it."""

    def __init__(self, name: str):
        super().__init__(name)
        self.setObjectName("SectionTitle")
        self.setToolTip(name)
        self.setMinimumWidth(40)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        room = self.contentsRect()
        painter.drawText(room, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, room.width()))


class _Pane(QWidget):
    """One version: whose it is and its name over its pages."""

    def __init__(self, side: str, name: str, column: PageColumn):
        super().__init__()
        role = QLabel(tr(side))
        role.setObjectName("HomeNavHeading")
        title = _Name(name)
        heading = QHBoxLayout()
        heading.setContentsMargins(2, 0, 2, 0)
        heading.setSpacing(10)
        heading.addWidget(role)
        heading.addWidget(title, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(heading)
        layout.addWidget(column, 1)


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
        self.status = QLabel(tr("Comparing…"))
        self.status.setMinimumHeight(24)
        self.busy = _Busy()
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setMinimumWidth(300)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(_ChangeRows(self.list))
        self.list.currentRowChanged.connect(self._go)
        self.previous, self.following = QPushButton(tr("Previous")), QPushButton(tr("Next"))
        self.previous.clicked.connect(lambda: self.step(-1))
        self.following.clicked.connect(lambda: self.step(1))
        for button in (self.previous, self.following):
            button.setEnabled(False)  # until there are changes to go through
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.previous)
        buttons.addWidget(self.following)
        side = QWidget()
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addWidget(self.status)
        column.addWidget(self.busy)
        column.addWidget(self.list, 1)
        column.addLayout(buttons)
        split = _Splitter()
        split.setHandleWidth(16)  # room to take hold of, round a line
        split.setChildrenCollapsible(False)
        split.addWidget(_Pane("Older", old.path.name, self.left))
        split.addWidget(_Pane("Newer", new.path.name, self.right))
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 1)
        split.setSizes([560, 560, 280])  # the two versions share the room, the list takes what is left
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.addWidget(split, 1)
        self._syncing = False
        self.left.verticalScrollBar().valueChanged.connect(lambda _v: self._sync(self.left, self.right))
        self.right.verticalScrollBar().valueChanged.connect(lambda _v: self._sync(self.right, self.left))
        themes.changed.connect(self._restyle)
        self._restyle()
        self._worker = _Worker()
        self._worker.done.connect(self._show)
        self._worker.ended.connect(self.busy.stop)
        self._stop = False
        self._thread = threading.Thread(target=self._compare, daemon=True, name="compare")
        self._thread.start()
        self.busy.start()

    def _restyle(self, *_) -> None:
        self.previous.setIcon(icon("caret-up"))
        self.following.setIcon(icon("caret-down"))
        for column in (self.left, self.right):
            column.restyle()
        self.list.viewport().update()

    def _compare(self) -> None:
        try:
            old = compare.words(self.old, stop=lambda: self._stop)
            new = compare.words(self.new, stop=lambda: self._stop)
            if not self._stop:
                self._worker.done.emit(compare.compare(old, new), bool(old or new))
        finally:
            self._worker.ended.emit()  # so the busy bar does not run on for ever behind a thread that failed

    def _show(self, changes: list[Change], has_text: bool = True) -> None:
        if self._stop:  # closed before the comparison was done: the window may be gone already
            return
        self.busy.stop()
        self.changes = changes
        for button in (self.previous, self.following):
            button.setEnabled(bool(changes))
        for number, change in enumerate(changes):
            self.left.mark(change.old, change.kind, number)
            self.right.mark(change.new, change.kind, number)
            # Each part is translated on its own: the whole line, with the change's words in it, is no text the
            # translations know.
            item = QListWidgetItem(f"{tr(NAMES[change.kind])} · {tr(f'p. {change.page + 1}')}\n{change.summary()}")
            item.setData(Qt.ItemDataRole.UserRole, change)
            item.setToolTip(change.summary(400))
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
            self.left.focus(row)
            self.right.focus(row)
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
        self.busy.stop()
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
