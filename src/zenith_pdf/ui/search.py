"""Full-text search panel. The search itself runs on a worker thread."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.document import Document, SearchHit
from ..core.i18n import tr
from ..icons import icon
from ..theme import qcolor, themes
from .widgets import muted_label, panel_title

HIT_ROLE = Qt.ItemDataRole.UserRole + 1


class _Relay(QObject):
    """Carries results from the worker thread to the GUI thread."""

    hit = Signal(int, object)  # generation, SearchHit
    done = Signal(int)


class ResultDelegate(QStyledItemDelegate):
    def __init__(self, panel: SearchPanel):
        super().__init__(panel)
        self._panel = panel

    def sizeHint(self, option, index) -> QSize:
        return QSize(200, 2 * option.fontMetrics.height() + 14)

    def paint(self, painter: QPainter, option, index) -> None:
        t = themes.current
        hit: SearchHit = index.data(HIT_ROLE)
        rect = option.rect.adjusted(6, 2, -6, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.selection))
            painter.drawRoundedRect(rect, 6, 6)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.hover))
            painter.drawRoundedRect(rect, 6, 6)

        metrics = option.fontMetrics
        inner = rect.adjusted(8, 5, -8, -5)
        painter.setPen(QColor(t.text_muted))
        painter.drawText(QRect(inner.x(), inner.y(), inner.width(), metrics.height()), 0, tr(self._panel.page_label(hit.page)))

        # Context line with the match in bold, trimmed so the match stays visible.
        before, match, after = _split_context(hit.context, self._panel.query)
        bold = QFont(option.font)
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        room = inner.width() - bold_metrics.horizontalAdvance(match)
        before = metrics.elidedText(before, Qt.TextElideMode.ElideLeft, max(room // 3, 0)) if before else ""
        x, y = inner.x(), inner.y() + metrics.height()
        line = QRect(x, y, inner.width(), metrics.height())
        painter.setPen(QColor(t.text))
        painter.setClipRect(inner)
        painter.drawText(line, 0, before)
        x += metrics.horizontalAdvance(before)
        painter.setFont(bold)
        painter.drawText(QRect(x, y, inner.width(), metrics.height()), 0, match)
        x += bold_metrics.horizontalAdvance(match)
        painter.setFont(option.font)
        remaining = inner.right() - x
        painter.drawText(QRect(x, y, remaining, metrics.height()), 0, metrics.elidedText(after, Qt.TextElideMode.ElideRight, remaining))
        painter.restore()


def _split_context(context: str, query: str) -> tuple[str, str, str]:
    position = context.lower().find(query.lower()) if query else -1
    if position < 0:
        return "", "", context
    end = position + len(query)
    return context[:position], context[position:end], context[end:]


class SearchPanel(QWidget):
    hit_found = Signal(object)  # SearchHit
    hit_selected = Signal(object)  # SearchHit
    cleared = Signal()

    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self._document = document
        self._generation = 0
        self._cancel = threading.Event()
        self._labels: dict[int, str] = {}
        self.query = ""
        self._relay = _Relay(self)
        self._relay.hit.connect(self._on_hit)
        self._relay.done.connect(self._on_done)

        self.field = QLineEdit()
        self.field.setPlaceholderText(tr("Search in document"))
        self.field.setClearButtonEnabled(True)
        self.field.addAction(icon("magnifying-glass", themes.current.text_muted), QLineEdit.ActionPosition.LeadingPosition)
        self.field.returnPressed.connect(self._on_return)
        self.field.textChanged.connect(self._on_text_changed)

        self.match_case = QCheckBox(tr("Match case"))
        self.whole_word = QCheckBox(tr("Whole words"))
        for box in (self.match_case, self.whole_word):
            box.setObjectName("Muted")
            box.toggled.connect(self.start)
        options = QHBoxLayout()
        options.setContentsMargins(2, 0, 0, 0)
        options.addWidget(self.match_case)
        options.addWidget(self.whole_word)
        options.addStretch()

        self.status = muted_label()
        self.results = QListWidget()
        self.results.setItemDelegate(ResultDelegate(self))
        self.results.setMouseTracking(True)
        self.results.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.currentItemChanged.connect(self._on_current_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 6, 0)
        layout.setSpacing(8)
        layout.addWidget(panel_title("Search"))
        layout.addWidget(self.field)
        layout.addLayout(options)
        layout.addWidget(self.status)
        layout.addWidget(self.results)

    def page_label(self, page: int) -> str:
        if page not in self._labels:
            self._labels[page] = f"Page {self._document.page_label(page)}"
        return self._labels[page]

    def focus_field(self) -> None:
        self.field.setFocus()
        self.field.selectAll()

    def start(self) -> None:
        self._cancel.set()
        self._cancel = threading.Event()
        self._generation += 1
        self.results.clear()
        self.cleared.emit()
        self.query = self.field.text().strip()
        if not self.query:
            self.status.setText("")
            return
        self.status.setText(tr("Searching…"))
        args = (self._generation, self.query, self.match_case.isChecked(), self.whole_word.isChecked(), self._cancel)
        threading.Thread(target=self._worker, args=args, daemon=True, name="search").start()

    def _worker(self, generation, query, match_case, whole_word, cancelled) -> None:
        for hit in self._document.iter_search(query, match_case, whole_word, cancelled):
            self._relay.hit.emit(generation, hit)
        self._relay.done.emit(generation)

    def _on_hit(self, generation: int, hit: SearchHit) -> None:
        if generation != self._generation:
            return
        item = QListWidgetItem()
        item.setData(HIT_ROLE, hit)
        self.results.addItem(item)
        self.hit_found.emit(hit)
        if self.results.count() == 1:
            self.results.setCurrentRow(0)
        self.status.setText(tr(f"{self.results.count()} results so far…"))

    def _on_done(self, generation: int) -> None:
        if generation != self._generation:
            return
        count = self.results.count()
        self.status.setText(tr("No results" if count == 0 else "1 result" if count == 1 else f"{count} results"))

    def _on_text_changed(self, text: str) -> None:
        if not text:
            self.start()  # the clear button empties the field: drop old highlights too

    def _on_return(self) -> None:
        if self.field.text().strip() != self.query:
            self.start()
        else:
            self.step(1)

    def step(self, delta: int) -> None:
        count = self.results.count()
        if count:
            self.results.setCurrentRow((self.results.currentRow() + delta) % count)

    def _on_current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self.hit_selected.emit(current.data(HIT_ROLE))

    def reset(self) -> None:
        """The pages changed: page labels are stale and results may have moved; search again."""
        self._labels.clear()
        if self.query:
            self.start()
        else:
            self._cancel.set()
            self.results.clear()

    def shutdown(self) -> None:
        self._cancel.set()
