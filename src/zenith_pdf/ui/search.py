"""Full-text search panel. The search itself runs on a worker thread."""

from __future__ import annotations

import threading

from PySide6.QtCore import QElapsedTimer, QObject, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
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
from . import motion
from .side_panel import CLEAR, HANG, PAD, RowFades, panel_header
from .widgets import muted_label

HIT_ROLE = Qt.ItemDataRole.UserRole + 1


class _Relay(QObject):
    """Carries results from the worker thread to the GUI thread."""

    hit = Signal(int, object)  # generation, SearchHit
    done = Signal(int)


class _Busy(QWidget):
    """A thin line along the top of the results while a search runs, with a short bar of light running along it.
    With motion off it shows nothing: the status line says the search is on."""

    PERIOD = 1300  # ms for the light to cross

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self.update)
        self.hide()

    def run(self, on: bool) -> None:
        self.setVisible(on and motion.enabled())
        self.raise_()

    def showEvent(self, event) -> None:
        self._clock.start()  # it ticks only while it is on screen: a panel switched away from needs no frames
        self._tick.start()

    def hideEvent(self, event) -> None:
        self._tick.stop()

    def paintEvent(self, event) -> None:
        t = themes.current
        share = motion.EASE_IN_OUT.valueForProgress((self._clock.elapsed() % self.PERIOD) / self.PERIOD)
        length = max(self.width() * 0.3, 24.0)
        left = -length + (self.width() + length) * share
        light = QColor(t.focus or t.accent)
        clear = QColor(light)
        clear.setAlpha(0)
        gradient = QLinearGradient(left, 0, left + length, 0)
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, light)
        gradient.setColorAt(1.0, clear)
        QPainter(self).fillRect(QRectF(self.rect()), gradient)


class _Results(QListWidget):
    """The results, with the line that says a search is running along their top."""

    def __init__(self):
        super().__init__()
        self.busy = _Busy(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.busy.setGeometry(0, 0, self.width(), 2)


class ResultDelegate(QStyledItemDelegate):
    def __init__(self, panel: SearchPanel, view: QListWidget):
        super().__init__(panel)
        self._panel = panel
        self.fades = RowFades(view)  # the fill under the pointer or the choice comes and goes gently

    def sizeHint(self, option, index) -> QSize:
        return QSize(200, 2 * option.fontMetrics.height() + 14)

    def paint(self, painter: QPainter, option, index) -> None:
        t = themes.current
        hit: SearchHit = index.data(HIT_ROLE)
        rect = option.rect.adjusted(HANG, 2, -4, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            want = qcolor(t.selection)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            want = qcolor(t.hover)
        else:
            want = CLEAR
        shade = self.fades.colour(index.row(), want)
        if shade.alpha():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawRoundedRect(rect, t.look.item, t.look.item)

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
        self._glass = self.field.addAction(icon("magnifying-glass", themes.current.text_muted),
                                           QLineEdit.ActionPosition.LeadingPosition)
        themes.changed.connect(self._repaint_glass)
        self.field.returnPressed.connect(self._on_return)
        self.field.textChanged.connect(self._on_text_changed)

        self.match_case = QCheckBox(tr("Match case"))
        self.whole_word = QCheckBox(tr("Whole words"))
        for box in (self.match_case, self.whole_word):
            box.setObjectName("Muted")
            box.toggled.connect(self.start)
        self._options = options = QGridLayout()
        options.setContentsMargins(2, 0, 0, 0)
        options.addWidget(self.match_case, 0, 0)
        options.addWidget(self.whole_word, 0, 1)
        options.setColumnStretch(2, 1)  # what is left of the row
        self._stacked = False  # the two stand one over the other, when their labels are too long for a row

        self.status = muted_label()
        self.results = _Results()
        self._delegate = ResultDelegate(self, self.results)
        self.results.setItemDelegate(self._delegate)
        self.results.setMouseTracking(True)
        self.results.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.currentItemChanged.connect(self._on_current_changed)

        self._controls = controls = QVBoxLayout()
        controls.setContentsMargins(PAD, 0, PAD - 4, 8)
        controls.setSpacing(8)
        controls.addWidget(self.field)
        controls.addLayout(options)
        controls.addWidget(self.status)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(panel_header("Search"))
        layout.addLayout(controls)
        layout.addWidget(self.results)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        margins = self._controls.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        need = (self.match_case.sizeHint().width() + self.whole_word.sizeHint().width() + 2
                + max(self._options.horizontalSpacing(), 0))
        stacked = need > room  # labels as long as German's or Russian's would be cut off in a row
        if stacked != self._stacked:
            self._stacked = stacked
            self._options.removeWidget(self.whole_word)
            self._options.addWidget(self.whole_word, int(stacked), int(not stacked))

    def _repaint_glass(self, *_) -> None:
        """The magnifier in the field, in the muted colour of the theme now: it was made in the one before."""
        self._glass.setIcon(icon("magnifying-glass", themes.current.text_muted))

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
        self._clear_results()
        self.cleared.emit()
        self.query = self.field.text().strip()
        if not self.query:
            self.status.setText("")
            self.results.busy.run(False)
            return
        self.status.setText(tr("Searching…"))
        self.results.busy.run(True)
        args = (self._generation, self.query, self.match_case.isChecked(), self.whole_word.isChecked(), self._cancel)
        threading.Thread(target=self._worker, args=args, daemon=True, name="search").start()

    def _worker(self, generation, query, match_case, whole_word, cancelled) -> None:
        try:
            for hit in self._document.iter_search(query, match_case, whole_word, cancelled):
                self._relay.hit.emit(generation, hit)
        finally:  # a search that failed is over all the same: its line of light must stop
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
        self.results.busy.run(False)
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
            self._clear_results()
            self.results.busy.run(False)

    def _clear_results(self) -> None:
        self._delegate.fades.reset()  # the highlight of a row that is gone is not the next one's
        self.results.clear()

    def shutdown(self) -> None:
        self._cancel.set()
        self.results.busy.run(False)
