"""Snapping: annotations line up with each other and with the page's content while you work.

When it is on, an edge or centre of what you move, resize or draw jumps to a
nearby edge or centre of another annotation ("snap to annotations") or of the
page's text lines, pictures, drawings and the page itself ("snap to PDF
content"). A guide line shows what it lined up with. Alt places freely.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QLineF, QPointF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QApplication, QGraphicsLineItem

from ..theme import themes

REACH_PX = 6  # how close, in screen pixels, an edge must come to snap
Z_GUIDE = 20


class Snapper:
    def __init__(self, view):
        self.view = view
        self._content: dict[int, tuple[list, list]] = {}  # page -> (xs, ys) of its content, worked out once
        self._guides: list[QGraphicsLineItem] = []

    # What is on ---------------------------------------------------------

    def _pref(self, key: str) -> bool:
        return bool(self.view._pref(key))

    @property
    def active(self) -> bool:
        """Snapping is on, and Alt isn't held to place freely."""
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier:
            return False
        return self._pref("snap_annotations") or self._pref("snap_content")

    def forget(self) -> None:
        """The pages changed: their content is measured again when needed."""
        self._content.clear()

    # Candidates ----------------------------------------------------------

    def _content_lines(self, page: int) -> tuple[list, list]:
        if page not in self._content:
            width, height = self.view.document.page_size(page)
            xs, ys = [0.0, width / 2, width], [0.0, height / 2, height]
            try:
                boxes = self.view.document.content_boxes(page)
            except Exception:
                boxes = []
            for x, y, w, h in boxes:
                xs += [x, x + w]
                ys += [y, y + h]
            self._content[page] = (sorted(set(round(v, 2) for v in xs)), sorted(set(round(v, 2) for v in ys)))
        return self._content[page]

    def _targets(self, page: int, exclude: set[str]) -> tuple[list, list]:
        xs, ys = [], []
        if self._pref("snap_content"):
            content_x, content_y = self._content_lines(page)
            xs += content_x
            ys += content_y
        if self._pref("snap_annotations"):
            for annotation in self.view.store.all():
                if annotation.page != page or annotation.id in exclude:
                    continue
                x, y, w, h = annotation.bounds()
                xs += [x, x + w / 2, x + w]
                ys += [y, y + h / 2, y + h]
        return xs, ys

    def _reach(self) -> float:
        return REACH_PX / max(self.view._view_scale(), 1e-6)

    @staticmethod
    def _nearest(values: list[float], targets: list[float], reach: float) -> tuple[float, float] | None:
        """(shift, target) bringing one of `values` onto the closest target within reach."""
        best = None
        for value in values:
            for target in targets:
                shift = target - value
                if abs(shift) <= reach and (best is None or abs(shift) < abs(best[0])):
                    best = (shift, target)
        return best

    # Snapping -------------------------------------------------------------

    def snap_rect(self, page: int, rect, exclude: set[str]) -> tuple[float, float]:
        """How far to shift `rect` (x, y, w, h) so its edges or centre meet the nearest targets."""
        if not self.active:
            self.hide()
            return 0.0, 0.0
        x, y, w, h = rect
        xs, ys = self._targets(page, exclude)
        reach = self._reach()
        across = self._nearest([x, x + w / 2, x + w], xs, reach)
        down = self._nearest([y, y + h / 2, y + h], ys, reach)
        self._show(page, across[1] if across else None, down[1] if down else None)
        return (across[0] if across else 0.0), (down[0] if down else 0.0)

    def snap_point(self, page: int, point: QPointF, exclude: set[str] = frozenset()) -> QPointF:
        if not self.active:
            self.hide()
            return point
        xs, ys = self._targets(page, exclude)
        reach = self._reach()
        across = self._nearest([point.x()], xs, reach)
        down = self._nearest([point.y()], ys, reach)
        self._show(page, across[1] if across else None, down[1] if down else None)
        return QPointF(point.x() + (across[0] if across else 0.0), point.y() + (down[0] if down else 0.0))

    # Guides ----------------------------------------------------------------

    def _show(self, page: int, x: float | None, y: float | None) -> None:
        self.hide()
        if not 0 <= page < len(self.view._pages):
            return
        item = self.view._pages[page]
        width, height = item.rect.width(), item.rect.height()
        for line in ([QLineF(x, 0, x, height)] if x is not None else []) + (
                [QLineF(0, y, width, y)] if y is not None else []):
            guide = QGraphicsLineItem(line, item)
            pen = QPen(QColor(themes.current.accent), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            guide.setPen(pen)
            guide.setZValue(Z_GUIDE)
            self._guides.append(guide)

    def hide(self) -> None:
        for guide in self._guides:
            if shiboken6.isValid(guide) and guide.scene() is not None:  # a reload may have deleted it
                guide.scene().removeItem(guide)
        self._guides = []
