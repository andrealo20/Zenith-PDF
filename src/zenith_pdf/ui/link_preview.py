"""A card that shows where a link goes: point at "[3]", "Figure 2" or "Section III" for a moment and the spot
it leads to appears by the pointer, so checking a citation costs no jump. Clicking still follows the link.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core.annotations import Rect
from ..core.document import DocumentClosed, PdfiumError
from ..theme import themes
from .widgets import FloatingBar

DELAY_MS = 450  # pointing this long at a link shows its card
CACHE = 16  # pictures kept, for going back and forth over the links of a page
COLUMN = 300.0  # points: one column of a two-column page
HEIGHT = 190.0  # points shown
ABOVE = 30.0  # points above the spot, so the line it points at is not the first thing cut
CAPTION_BELOW = 40.0  # for a caption, the card ends this far below it: the figure is above its caption
CAPTIONS = ("Figure", "Fig.", "Table")
COLUMN_INSET = 12.0  # points left of the spot: a column's text starts a little before it
LINE_CLEARANCE = 1.5  # points kept between an edge of the area and a line of text
MOST_SCALE = 1.3  # logical pixels a point, so small print stays readable without a huge card
MOST_WIDTH = 560  # logical pixels of the picture, at most: a whole page's width, for a figure, still reads
LEAST_WIDTH = 160  # and at least, however narrow the window
MARGIN = 10  # pixels of glass round the picture
EDGE = 14  # pixels the canvas keeps between its cards and its edges
MARK = QSize(3, 16)  # the accent mark at the spot, on the picture's left edge


def preview_area(page_size: tuple[float, float], point: tuple[float, float] | None, caption_above: bool) -> Rect:
    """The part of a page the card shows, in display points, for the spot `point` (display points; None for the
    top of the page).

    A spot with an x of its own (a column of a two-column page) shows one column from just left of it; x 0
    shows the whole width. The area starts a little above the spot, or, when the spot is a caption
    (`caption_above`: the figure sits above it), ends a little below it. It always lies on the page.
    """
    page_w, page_h = page_size
    x, y = point if point is not None else (0.0, 0.0)
    if x > 0:
        width = min(COLUMN, page_w)
        left = min(max(0.0, x - COLUMN_INSET), page_w - width)
    else:
        left, width = 0.0, page_w
    height = min(HEIGHT, page_h)
    top = y + CAPTION_BELOW - height if caption_above else y - ABOVE
    top = min(max(top, 0.0), page_h - height)
    return left, top, width, height


def whole_lines(area: Rect, lines) -> Rect:
    """`area` with its top and bottom edges off the lines of text (display rects) they would cut through:
    the top moves up to take in the whole line, the bottom moves up to leave it out. Lines beside the area,
    in another column, don't count."""
    left, top, width, height = area
    bottom = top + height
    new_top, new_bottom = top, bottom
    for x, y, w, h in lines:
        if x >= left + width or x + w <= left:
            continue
        if y < top < y + h:
            new_top = min(new_top, y - LINE_CLEARANCE)
        if y < bottom < y + h:
            new_bottom = min(new_bottom, y - LINE_CLEARANCE)
    new_top = max(new_top, 0.0)
    if new_bottom - new_top < height / 2:  # a page of huge print: cut it rather than show a sliver
        return area
    return left, new_top, width, new_bottom - new_top


class _Picture(QWidget):
    """The picture of the spot, with rounded corners and a fine edge, so a white page stands out on light glass,
    and an accent mark on its left edge at the height the link points at."""

    RADIUS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pixmap = QPixmap()
        self._mark: float | None = None

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def set_pixmap(self, pixmap: QPixmap, mark: float | None = None) -> None:
        """Show `pixmap`, with the mark `mark` logical pixels down it (None: no mark)."""
        self._pixmap = pixmap
        self._mark = mark
        ratio = pixmap.devicePixelRatio() or 1.0
        self.setFixedSize(QSize(round(pixmap.width() / ratio), round(pixmap.height() / ratio)))
        self.update()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        painter.save()
        painter.setClipPath(shape)
        painter.drawPixmap(self.rect(), self._pixmap)
        if self._mark is not None:
            top = min(max(self._mark, self.RADIUS), self.height() - self.RADIUS - MARK.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(QRectF(0, top, MARK.width(), MARK.height()), 1.5, 1.5)
        painter.restore()
        painter.setPen(QPen(QColor(t.border), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shape)


class LinkPreview(FloatingBar):
    """The card, on the glass of the bars. It belongs to the canvas, not a popup window, so it never takes the
    focus, and the pointer passes through it to the page. `place(card, anchor)` puts it by the pointer."""

    def __init__(self, view, place, parent=None):
        super().__init__(parent)
        self._view = view
        self._place = place
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.title = QLabel()
        font = QFont(self.title.font())
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.title.setContentsMargins(4, 0, 0, 0)  # in line with the text of the page, not its corner
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.picture = _Picture()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN, 8, MARGIN, MARGIN)
        layout.setSpacing(6)
        layout.addWidget(self.title)
        layout.addWidget(self.picture)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DELAY_MS)
        self._timer.timeout.connect(self._appear)
        self._target: tuple[int, tuple[float, float] | None] | None = None  # the page and spot pointed at
        self._anchor = QPoint()
        self.area: Rect | None = None  # the part of the page shown last, in display points
        # (document version, page, spot, pixels a point, tint) -> (picture, area, mark): the newest last
        self._pictures: OrderedDict[tuple, tuple[QPixmap, Rect, float | None]] = OrderedDict()
        store = view.store
        for signal in (store.added, store.removed, store.changed, store.reordered):
            signal.connect(self._forget)  # annotations are in the pictures
        view.link_hovered.connect(self.hover)
        view.link_left.connect(self.leave)
        self.hide()

    def hover(self, page: int, point, anchor: QPoint) -> None:
        """The pointer is on a link to `page`, at `anchor`. Moving along the same link changes nothing; another
        link shows its card after the delay, or at once if a card is up already."""
        target = (page, tuple(point) if point is not None else None)
        if target == self._target and (self.isVisible() or self._timer.isActive()):
            if self._timer.isActive():
                self._anchor = anchor  # the card goes where the pointer stops
            return
        self._target, self._anchor = target, anchor
        if self.isVisible():
            self._appear()  # reading along a list of references: no wait between them
        else:
            self._timer.start()

    def leave(self) -> None:
        self._timer.stop()
        self._target = None
        self.hide()

    def _forget(self, *_) -> None:
        self._pictures.clear()

    def _appear(self) -> None:
        if self._target is None or not self._view.isVisible():
            return
        page, point = self._target
        if not 0 <= page < self._view.document.page_count:
            self.leave()  # no such page here (the pages changed under the pointer): nothing to show
            return
        try:
            pixmap, self.area, mark = self._picture_of(page, point)
            label = self._view.document.page_label(page)
        except (DocumentClosed, PdfiumError):
            self.leave()  # the document is closing or changing: there is nothing to show
            return
        number = str(page + 1)
        self.title.setText(f"Page {number}" if label == number else f"Page {label} ({number})")
        self.picture.set_pixmap(pixmap, mark)
        self.layout().activate()
        self.adjustSize()
        self._place(self, self._anchor)
        self.show()
        self.raise_()
        self.refresh_backdrop()

    def _picture_of(self, page: int, point) -> tuple[QPixmap, Rect, float | None]:
        """The spot `point` (PDF space) on `page` and what is round it, with its annotations, as the page shows;
        the area shown, in display points; and where the spot is down the picture, in logical pixels."""
        view = self._view
        document = view.document
        canvas = self.parentWidget()
        room = canvas.width() - 2 * (EDGE + MARGIN) if canvas is not None else MOST_WIDTH  # a narrow window
        ratio = self.devicePixelRatioF()
        key = (document.key, page, point, room, round(ratio * 100), view.page_tint)
        if key in self._pictures:
            self._pictures.move_to_end(key)
            return self._pictures[key]
        spot = document.geometry(page).to_display(*point) if point is not None else None
        if spot is not None and point[0] <= 0:
            spot = (0.0, spot[1])  # no x: the destination names a height only
        caption = spot is not None and self._is_caption(page, spot)
        area = whole_lines(preview_area(document.page_size(page), spot, caption), self._lines(page))
        scale = min(MOST_SCALE, max(min(MOST_WIDTH, room), LEAST_WIDTH) / area[2])
        image = view.area_image(page, area, scale * ratio, tint=view.page_tint)
        image.setDevicePixelRatio(ratio)
        mark = (spot[1] - area[1]) * scale if spot is not None else None
        self._pictures[key] = entry = (QPixmap.fromImage(image), area, mark)
        while len(self._pictures) > CACHE:
            self._pictures.popitem(last=False)
        return entry

    def _lines(self, page: int):
        """The lines of text on `page`, in display space, one rect per line and column."""
        document = self._view.document
        count = document.char_count(page)
        return document.text_span(page, 0, count - 1)[0] if count > 0 else ()

    def _is_caption(self, page: int, spot: tuple[float, float]) -> bool:
        """Whether the line at the spot starts like a caption, "Figure 2." or "Table 3", below its figure."""
        width = self._view.document.page_size(page)[0]
        x, y = spot
        line = self._view.document.text_in(page, [(max(x - COLUMN_INSET, 0.0), y - 2, width, 18)]).lstrip()
        return line.startswith(CAPTIONS)
