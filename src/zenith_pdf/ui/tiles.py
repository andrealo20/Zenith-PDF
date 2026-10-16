"""Tiled page rendering on the canvas.

At the current zoom a page is cut into TILE x TILE device-pixel tiles, and only
the visible ones are rendered. Tiles at the exact screen scale are copied 1:1
onto device pixels, as sharp as a native rendering. While they arrive, tiles of
other zoom levels and a low-resolution whole-page base fill in.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QPainter, QPixmap, QTransform

from ..core.render_worker import full_size

CACHE_BYTES = 384 * 2**20
BLEED_POINTS = 14.0  # about the widest glyph of body text


def scale_key(scale: float) -> int:
    """Rendering scales as integers, so float noise can't split one zoom level in two."""
    return round(scale * 4096)


def tile_size(scale: float) -> int:
    """Device pixels per tile side: larger when zoomed in, so the bleed costs less."""
    return 512 if scale < 4 else 1024


def bleed(scale: float) -> int:
    """Pixels rendered around each tile and then thrown away.

    PDFium places a glyph by truncating its origin towards zero, so a letter that
    starts left of (or above) the bitmap lands one pixel off and shows a seam
    where two tiles meet. With this margin, letters crossing a tile edge start
    inside the bitmap.
    """
    return max(16, min(192, math.ceil(scale * BLEED_POINTS)))


def tile_grid(page_size: tuple[float, float], scale: float, area: QRectF) -> Iterator[tuple[int, int, QRect]]:
    """Tiles covering `area` (page points) at `scale`: (column, row, pixel rect in the whole page)."""
    full_w, full_h = full_size(page_size, scale)
    if area.isEmpty() or full_w <= 0 or full_h <= 0:
        return
    size = tile_size(scale)
    first_col = max(0, int(area.left() * scale) // size)
    last_col = min((full_w - 1) // size, int(area.right() * scale) // size)
    first_row = max(0, int(area.top() * scale) // size)
    last_row = min((full_h - 1) // size, int(area.bottom() * scale) // size)
    for row in range(first_row, last_row + 1):
        for col in range(first_col, last_col + 1):
            x, y = col * size, row * size
            yield col, row, QRect(x, y, min(size, full_w - x), min(size, full_h - y))


def cell_rect(page_size: tuple[float, float], scale: float, col: int, row: int) -> QRect:
    full_w, full_h = full_size(page_size, scale)
    size = tile_size(scale)
    x, y = col * size, row * size
    return QRect(x, y, min(size, full_w - x), min(size, full_h - y))


def render_area(page_size: tuple[float, float], scale: float, cell: QRect) -> QRect:
    """The region actually rendered for a tile: the cell plus the bleed, inside the page."""
    full_w, full_h = full_size(page_size, scale)
    margin = bleed(scale)
    left, top = max(0, cell.left() - margin), max(0, cell.top() - margin)
    right, bottom = min(full_w, cell.right() + 1 + margin), min(full_h, cell.bottom() + 1 + margin)
    return QRect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class Tile:
    pixmap: QPixmap  # the rendered area, bleed included
    scale: float
    rect: QRect  # the cell, in pixels of the whole page rendered at `scale`
    source: QRect  # where the cell sits inside the pixmap

    @property
    def nbytes(self) -> int:
        return self.pixmap.width() * self.pixmap.height() * 4


class TileCache:
    """Rendered tiles of one document, least recently painted evicted first."""

    def __init__(self, budget: int = CACHE_BYTES):
        self._budget = budget
        self._bytes = 0
        self._lru: OrderedDict[tuple, Tile] = OrderedDict()  # (page, scale key, col, row)
        self._levels: dict[int, dict[int, dict[tuple[int, int], Tile]]] = {}  # page -> key -> cells

    def set_budget(self, budget: int) -> None:
        self._budget = budget
        self._evict()

    def has(self, page: int, key: int, col: int, row: int) -> bool:
        return (page, key, col, row) in self._lru

    def level(self, page: int, key: int) -> dict[tuple[int, int], Tile]:
        return self._levels.get(page, {}).get(key, {})

    def levels(self, page: int) -> dict[int, dict[tuple[int, int], Tile]]:
        return self._levels.get(page, {})

    def touch(self, page: int, key: int, col: int, row: int) -> None:
        self._lru.move_to_end((page, key, col, row))

    def put(self, page: int, key: int, col: int, row: int, tile: Tile) -> None:
        full = (page, key, col, row)
        if full in self._lru:
            self._bytes -= self._lru[full].nbytes
        self._lru[full] = tile
        self._lru.move_to_end(full)
        self._levels.setdefault(page, {}).setdefault(key, {})[(col, row)] = tile
        self._bytes += tile.nbytes
        self._evict()

    def _evict(self) -> None:
        while self._bytes > self._budget and len(self._lru) > 1:
            (old_page, old_key, old_col, old_row), old = self._lru.popitem(last=False)
            self._bytes -= old.nbytes
            cells = self._levels[old_page][old_key]
            del cells[(old_col, old_row)]
            if not cells:
                del self._levels[old_page][old_key]

    def clear(self) -> None:
        self._lru.clear()
        self._levels.clear()
        self._bytes = 0


def paint_page(
    painter: QPainter,
    page: int,
    page_rect: QRectF,
    exposed: QRectF,
    cache: TileCache,
    base: QPixmap | None,
) -> None:
    """Paint a page's content: exact tiles if complete, else fallbacks under them."""
    device = painter.deviceTransform()  # page points -> device pixels, display ratio included
    scale = device.m11()
    key = scale_key(scale)
    size = (page_rect.width(), page_rect.height())
    needed = list(tile_grid(size, scale, exposed))
    exact = cache.level(page, key)

    if not all((col, row) in exact for col, row, _ in needed):
        if base is not None:
            painter.drawPixmap(page_rect, base, QRectF(base.rect()))
        # Other zoom levels, coarsest first so the sharpest end up on top.
        for other_key in sorted(cache.levels(page)):
            if other_key == key:
                continue
            for tile in cache.level(page, other_key).values():
                r, s = tile.rect, tile.scale
                target = QRectF(r.x() / s, r.y() / s, r.width() / s, r.height() / s)
                if target.intersects(exposed):
                    painter.drawPixmap(target, tile.pixmap, QRectF(tile.source))

    if not exact:
        return
    # Exact tiles: drop to device pixels and copy without resampling.
    # deviceTransform() also contains where this widget sits in the window's backing
    # store, which can be a fractional device pixel at 125% or 175% scaling. Round the
    # page origin in absolute device pixels, then express it relative to the widget.
    ratio = painter.device().devicePixelRatioF()
    origin = device.map(QPointF(0, 0))
    offset = origin - painter.worldTransform().map(QPointF(0, 0)) * ratio
    left, top = round(origin.x()) - offset.x(), round(origin.y()) - offset.y()
    painter.save()
    painter.setWorldTransform(QTransform.fromScale(1 / ratio, 1 / ratio))
    for col, row, rect in needed:
        tile = exact.get((col, row))
        if tile is not None:
            cache.touch(page, key, col, row)
            painter.drawPixmap(QPointF(left + rect.x(), top + rect.y()), tile.pixmap, QRectF(tile.source))
    painter.restore()
