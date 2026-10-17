"""The scrolling, zoomable page canvas.

Scene units are PDF points. Each page is a PageItem placed in a vertical
column; the view transform turns points into screen pixels, so zooming never
moves an item, it only changes the transform and asks for sharper bitmaps.
Annotations are child items of their page, in the page's display space.
"""

from __future__ import annotations

import base64
import bisect
import subprocess
import sys
import tempfile
import time
from dataclasses import fields, replace
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QDesktopServices,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
    QTransform,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
    QToolTip,
)

from ..core import images, textlayout
from ..core.annotations import (
    AUDIO_SUFFIXES,
    NOTE_SIZE,
    Annotation,
    Attachment,
    FreeText,
    ImageStamp,
    Ink,
    Link,
    Note,
    Tape,
    TextMarkup,
    new_id,
    overlaps,
    union,
)
from ..core.document import Document, SearchHit
from ..core.render import RenderPool, render_scale
from ..core.store import AnnotationStore
from ..icons import icon
from ..settings import DEFAULTS, Settings
from ..theme import themes
from . import clipboard
from . import annotation_items
from . import messages
from .annotation_items import Z_ANNOTATION, AnnotationItem, Handle, hit_path, paint_annotation
from .commands import AddAnnotations, ChangeAnnotations, RemoveAnnotations, ReorderAnnotations
from .history import NEAR, History, Spot
from .text_editor import TextBoxEditor
from .tiles import Tile, TileCache, cell_rect, paint_page, render_area, scale_key, tile_grid
from .tool_catalog import base_of, toolbar_keys
from .tools import IMAGE_SIZES, MarkupTool, Tool, ToolSettings, alive, make_tools, remove_item

PAGE_GAP = 14.0  # points between pages
MARGIN = 28.0  # points around the column of pages
SHADOW = 8.0  # points reserved around a page for its shadow
# Mouse-wheel zoom moves along these levels, so the next level up and down is
# known in advance and can be rendered before the user asks for it.
ZOOM_LADDER = (
    0.1, 0.125, 0.15, 0.2, 0.25, 0.33, 0.4, 0.5, 0.6, 0.67, 0.75, 0.85, 1.0, 1.15, 1.3,
    1.5, 1.75, 2.0, 2.4, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0,
)
ZOOM_MIN, ZOOM_MAX = ZOOM_LADDER[0], ZOOM_LADDER[-1]
BASE_ZOOM = 0.5  # quality of the whole-page fallback shown while tiles arrive
SPECULATIVE_TILES = 96  # cap on tiles rendered ahead for the neighbouring zoom levels
ZOOM_SETTLE_MS = 30  # continuous (touchpad) zoom renders once it pauses this long
FOLLOWUP_MS = 30  # quiet time after results before rendering ahead of the user
VIEW_ONLY_TOOLS = ("select", "hand", "lasso", "laser", "snapshot")  # they change nothing: on for view-only files
Z_STEP = 1e-4  # z-value between annotations, in drawing order
LANDING = 48  # pixels below the toolbar band where the spot a link or bookmark goes to lands
HOLD_MS = 600  # how long hold() keeps a point in place: a window going full screen settles within it
# Attachments that run as programs when opened, or carry macros: someone else's PDF could carry one, so opening
# asks first, with a warning.
RISKY_SUFFIXES = (
    ".exe", ".com", ".bat", ".cmd", ".msi", ".msp", ".mst", ".scr", ".pif", ".lnk", ".url", ".js", ".jse", ".vbs",
    ".vbe", ".vb", ".wsf", ".wsh", ".wsc", ".ws", ".ps1", ".psm1", ".psd1", ".psc1", ".hta", ".cpl", ".reg", ".jar",
    ".msc", ".scf", ".sct", ".shb", ".shs", ".inf", ".ins", ".isp", ".chm", ".dll", ".sys", ".ocx", ".gadget",
    ".application", ".appref-ms", ".appx", ".appxbundle", ".msix", ".settingcontent-ms", ".library-ms", ".search-ms",
    ".diagcab", ".iso", ".img", ".vhd", ".vhdx", ".xll", ".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm",
    ".ppam",
    # macOS and Linux
    ".app", ".command", ".tool", ".pkg", ".mpkg", ".dmg", ".workflow", ".scpt", ".applescript", ".terminal",
    ".sh", ".bash", ".zsh", ".csh", ".ksh", ".fish", ".run", ".bin", ".appimage", ".desktop", ".deb", ".rpm",
    ".py", ".pyw", ".pl", ".rb", ".php",
)
# Documents and media: these open without a question. Any other kind asks first, plainly.
SAFE_SUFFIXES = (".pdf", ".txt", ".rtf", ".csv", ".md", ".log", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif",
                 ".tiff", ".webp", ".heic", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".mp4",
                 ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(10)), *(f"LPT{n}" for n in range(10))}


def mark_downloaded(path: Path) -> None:
    """Mark a file as coming from elsewhere, as a download is: Windows and macOS then check a program before it
    runs, and Office opens a document in Protected View. Linux has no such mark."""
    try:
        if sys.platform == "win32":
            Path(f"{path}:Zone.Identifier").write_text("[ZoneTransfer]\r\nZoneId=3\r\n")
        elif sys.platform == "darwin":
            flag = f"0081;{int(time.time()):x};Zenith PDF;"
            subprocess.run(["xattr", "-w", "com.apple.quarantine", flag, str(path)], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass  # a drive without that (FAT): the question before opening still stands


def attachment_file_name(file_name: str) -> str:
    """The name an attachment is written out under, as Windows would store it: no folder, none of the characters
    it refuses, no dots or spaces at the end (it drops them, so "invoice.exe." is a program) and no device name.
    The kind checked before opening is then the kind Windows opens."""
    name = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join("_" if c in '<>:"|?*' or ord(c) < 32 else c for c in name).rstrip(" .")
    if name.split(".", 1)[0].strip().upper() in DEVICE_NAMES:
        name = "_" + name
    return name or "attachment"


def ladder_step(zoom: float, steps: int) -> float:
    """The zoom `steps` levels up (or down, if negative) the ladder from `zoom`."""
    for _ in range(abs(steps)):
        if steps > 0:
            zoom = next((z for z in ZOOM_LADDER if z > zoom * 1.001), ZOOM_MAX)
        else:
            zoom = next((z for z in reversed(ZOOM_LADDER) if z < zoom * 0.999), ZOOM_MIN)
    return zoom


def levels_to_prepare(zoom: float, last_direction: int) -> list[float]:
    """Zoom levels to render ahead of the user, most likely first.

    `last_direction` is +1 after zooming in, -1 after zooming out, 0 before any zoom.
    The tile budget (SPECULATIVE_TILES) is shared equally among the levels returned.
    """
    # TODO(human): choose the levels, using the direction the user has been zooming in.
    return [ladder_step(zoom, 1), ladder_step(zoom, -1)]


LAYOUTS = {"single": "Single page", "double": "Two pages", "cover": "Two pages, cover alone"}
TINTS = {"normal": "Normal", "dark": "Dark (inverted)", "sepia": "Sepia"}
SEPIA = QColor(244, 233, 210)
TEXT_SELECTION = QColor(51, 115, 255, 77)  # over selected text, as a drag with Select shows it


class PageItem(QGraphicsItem):
    def __init__(self, index: int, width: float, height: float, cache: TileCache, tint: str = "normal"):
        super().__init__()
        self.index = index
        self.rect = QRectF(0, 0, width, height)
        self.cache = cache
        self.tint = tint
        self.base: QPixmap | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW)

    def set_base(self, pixmap: QPixmap | None) -> None:
        self.base = pixmap
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        # One device pixel in points, capped so the shadow stays inside boundingRect.
        step = min(1.0 / max(painter.worldTransform().m11(), 1e-6), SHADOW / 4)
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((3, 14), (2, 24), (1, 40)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            d = spread * step
            painter.drawRect(self.rect.adjusted(-d, -d + step, d, d + step))
        painter.fillRect(self.rect, Qt.GlobalColor.white)
        exposed = option.exposedRect.intersected(self.rect)
        if exposed.isEmpty():
            return
        paint_page(painter, self.index, self.rect, exposed, self.cache, self.base)
        # Page tints are applied on screen, so switching them never re-renders.
        # Annotations are child items, painted after this, so they keep their colours.
        if self.tint == "dark":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
            painter.fillRect(exposed, Qt.GlobalColor.white)
        elif self.tint == "sepia":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            painter.fillRect(exposed, SEPIA)


class DocumentView(QGraphicsView):
    current_page_changed = Signal(int)
    zoom_changed = Signal(float)
    selection_changed = Signal(list)  # selected Annotations
    content_changed = Signal()  # what is on screen changed: glass bars resample their backdrop
    message = Signal(str)  # a short note for the user, shown as a toast
    history_changed = Signal()  # Back or Forward became possible, or stopped being
    # The pointer is on a link to a page: that page, the spot it shows (PDF space, or None), and where
    # the pointer is in viewport pixels. link_left: it left, or the pages moved under it.
    link_hovered = Signal(int, object, QPoint)
    link_left = Signal()
    text_selection_changed = Signal(object)  # where the selected text is, in viewport pixels (QRect), or None

    def __init__(
        self,
        document: Document,
        renderer: RenderPool,
        store: AnnotationStore,
        undo_stack: QUndoStack,
        tool_settings: ToolSettings,
        parent=None,
        settings: Settings | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.document = document
        self.store = store
        self.undo_stack = undo_stack
        self.tool_settings = tool_settings
        self._renderer = renderer
        self._token = object()  # identifies this view's jobs in the shared render queue
        self._zoom = 1.0
        self._zoom_direction = 0  # +1 after zooming in, -1 after zooming out
        self._fit: str | None = "width"
        self._current = 0
        self._hit_items: dict[SearchHit, list[QGraphicsRectItem]] = {}
        self._focused_hit: SearchHit | None = None
        self._items: dict[str, AnnotationItem] = {}
        self._tools = make_tools(self)
        from .snapping import Snapper

        self.snapper = Snapper(self)
        self._active: Tool | None = None
        self._active_page: PageItem | None = None
        self._pan_from: QPoint | None = None
        self._space_down = False
        self._text_selection = None  # (page, rects, text, preview item)
        self._editor: tuple | None = None  # (TextBoxEditor, hidden AnnotationItem or None, tool or None)
        self._spent_editors: list[TextBoxEditor] = []  # finished, still to be taken off the canvas
        self._cache = TileCache(int(self._pref("tile_cache_mb")) * 2**20)
        self._mouse: QPointF | None = None  # last cursor position over the viewport
        self.layout_mode = "single"
        self.top_inset = 0  # pixels at the top covered by the floating toolbar
        self.page_tint = "normal"
        self.actions = None  # PageActions, set by the workspace; drives the context menu
        self.history = History()  # places jumped from, for Back and Forward
        self._history_key = document.key  # the version of the document whose page numbers it holds
        self._on_link = False  # the pointer is on a link to a page, and link_hovered said so
        self._followed = False  # the last press followed a link: a second click of the pair does nothing more
        self.reading = False  # reading mode: the pages answer as to Pan, and nothing marks a selection
        self._press_link = None  # in reading mode, the link a press began on: a click without a drag follows it
        self._press_at = QPoint()
        self._held: tuple[QPointF, QPoint] | None = None  # a point of the pages kept where it is on the screen
        self._settled: tuple | None = None  # the held point and where the pages were when the hold ended
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._hold_done)

        self.setScene(QGraphicsScene(self))
        self._pages: list[PageItem] = []
        self._tops: list[float] = []
        self._layout_pages()

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing)
        self.setMouseTracking(True)
        self._apply_theme(themes.current)
        themes.changed.connect(self._apply_theme)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._update_renders)
        # After results stop arriving, look again: that is when work ahead of the user starts.
        self._followup_timer = QTimer(self)
        self._followup_timer.setSingleShot(True)
        self._followup_timer.setInterval(FOLLOWUP_MS)
        self._followup_timer.timeout.connect(self._update_renders)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(lambda: self._schedule_renders(0))
        self.horizontalScrollBar().valueChanged.connect(self.content_changed)
        renderer.rendered.connect(self._on_rendered)

        for annotation in store.all():
            self._add_item(annotation)
        store.added.connect(self._add_item)
        store.removed.connect(self._remove_item)
        store.changed.connect(self._change_item)
        store.reordered.connect(self._restack)
        self.scene().selectionChanged.connect(self._on_selection_changed)
        self.scene().changed.connect(self.content_changed)
        tool_settings.tool_changed.connect(self._apply_tool)
        self._apply_tool(tool_settings.tool)

    def _pref(self, key: str):
        """A user preference; the defaults when the view has no settings (tests, tools)."""
        return self.settings.get(key) if self.settings is not None else DEFAULTS[key]

    def set_cache_budget(self, megabytes: int) -> None:
        self._cache.set_budget(int(megabytes) * 2**20)

    # Layout -------------------------------------------------------------

    def _rows(self, count: int) -> list[list[int]]:
        """Which pages sit side by side, for the current layout."""
        if self.layout_mode == "double":
            return [list(range(i, min(i + 2, count))) for i in range(0, count, 2)]
        if self.layout_mode == "cover":
            return [[0]] + [list(range(i, min(i + 2, count))) for i in range(1, count, 2)] if count else []
        return [[i] for i in range(count)]

    def _layout_pages(self) -> None:
        sizes = [self.document.page_size(i) for i in range(self.document.page_count)]
        self._row_of: dict[int, list[int]] = {}
        rows = self._rows(len(sizes))
        widths = [sum(sizes[i][0] for i in row) + PAGE_GAP * (len(row) - 1) for row in rows]
        column = max(widths, default=612.0)
        centre = column / 2
        y = MARGIN
        self._tops = [0.0] * len(sizes)
        self._pages = [None] * len(sizes)
        for row in rows:
            height = max(sizes[i][1] for i in row)
            if len(row) == 1:
                xs = [centre - sizes[row[0]][0] / 2]
            else:  # a spread: the left page ends and the right page starts at the gutter
                xs = [centre - PAGE_GAP / 2 - sizes[row[0]][0], centre + PAGE_GAP / 2]
            for index, x in zip(row, xs):
                width, page_height = sizes[index]
                item = PageItem(index, width, page_height, self._cache, self.page_tint)
                item.setPos(x, y + (height - page_height) / 2)
                self.scene().addItem(item)
                self._pages[index] = item
                self._tops[index] = y
                self._row_of[index] = row
            y += height + PAGE_GAP
        self._column = column
        self._content_rect = QRectF(-MARGIN, 0, column + 2 * MARGIN, y - PAGE_GAP + MARGIN)
        self._update_scene_rect()

    def _update_scene_rect(self) -> None:
        """Room above the first page for the toolbar floating over it, whatever the zoom."""
        inset = self.top_inset / max(self._view_scale(), 1e-6)
        self.scene().setSceneRect(self._content_rect.adjusted(0, -inset, 0, 0))

    def set_top_inset(self, pixels: int) -> None:
        if pixels != self.top_inset:
            self.top_inset = pixels
            self._update_scene_rect()

    def _apply_theme(self, theme) -> None:
        self.setBackgroundBrush(QColor(theme.canvas))

    def _page_at(self, view_pos: QPointF) -> tuple[PageItem | None, QPointF | None]:
        scene_pos = self.mapToScene(view_pos.toPoint())
        index = bisect.bisect_right(self._tops, scene_pos.y()) - 1
        if 0 <= index < len(self._pages):
            for candidate in self._row_of[index]:
                page = self._pages[candidate]
                local = page.mapFromScene(scene_pos)
                if page.rect.contains(local):
                    return page, local
        return None, None

    def reload(self) -> None:
        """Rebuild the canvas after the pages changed, keeping zoom and position."""
        self.finish_text_edit()
        self._dispose_editors()
        self.clear_text_selection()
        self._leave_link()
        self.release_hold()  # a point held in place belongs to the pages before
        self.stop_tools()  # nothing may hold on to an item of the scene about to be cleared
        self.snapper.forget()  # the pages' content is measured again for snapping
        if self.document.key != self._history_key:  # the pages changed: an old page number may mean another page
            self._history_key = self.document.key
            self.history.clear()
            self.history_changed.emit()
        token = self._token
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._token = object()  # results still on their way belong to the old pages
        current, fit, zoom = self._current, self._fit, self._zoom
        self._hit_items.clear()
        self._focused_hit = None
        self._items.clear()
        self.scene().clear()
        self._cache.clear()
        self._layout_pages()
        for annotation in self.store.all():
            self._add_item(annotation)
        self._current = -1
        if fit == "width":
            self.fit_width()
        elif fit == "page":
            self.fit_page()
        else:
            self.set_zoom(zoom)
        self.goto_page(min(current, len(self._pages) - 1))
        self._schedule_renders(0)

    def set_layout(self, mode: str) -> None:
        if mode != self.layout_mode:
            self.layout_mode = mode
            self.reload()

    def set_page_tint(self, tint: str) -> None:
        self.page_tint = tint
        for page in self._pages:
            page.tint = tint
            page.update()

    # Zoom ---------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    def _view_scale(self) -> float:
        """Screen pixels per point. At 100% a page shows at its physical size."""
        return self._zoom * self.logicalDpiX() / 72.0

    def set_zoom(self, zoom: float, anchor: QPointF | None = None, fit: str | None = None) -> None:
        self._fit = fit
        self._leave_link()
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        if abs(zoom - self._zoom) > 1e-6:
            self._zoom_direction = 1 if zoom > self._zoom else -1
        anchor_px = (anchor or QPointF(self.viewport().rect().center())).toPoint()
        anchor_scene = self.mapToScene(anchor_px)
        self._zoom = zoom
        scale = self._view_scale()
        self.setTransform(QTransform.fromScale(scale, scale))
        self._update_scene_rect()
        # Keep the scene point under the anchor where it was.
        drift = self.mapFromScene(anchor_scene) - anchor_px
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + drift.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + drift.y())
        self.zoom_changed.emit(zoom)
        self.content_changed.emit()
        self._schedule_renders(ZOOM_SETTLE_MS)

    ladder_step = staticmethod(ladder_step)

    def zoom_in(self) -> None:
        self.set_zoom(self.ladder_step(self._zoom, 1), anchor=self._mouse)

    def zoom_out(self) -> None:
        self.set_zoom(self.ladder_step(self._zoom, -1), anchor=self._mouse)

    def fit_width(self) -> None:
        points_to_px = self.logicalDpiX() / 72.0
        # Two pixels of slack: an exact fit can round one pixel over and summon the scrollbar.
        zoom = (self.viewport().width() - 2) / ((self._column + 2 * MARGIN) * points_to_px)
        self._refit(zoom, "width")

    def fit_page(self) -> None:
        points_to_px = self.logicalDpiX() / 72.0
        row = self._row_of.get(self._current, [self._current])  # a spread fits as a whole
        width = sum(self._pages[i].rect.width() for i in row) + PAGE_GAP * (len(row) - 1)
        height = max(self._pages[i].rect.height() for i in row)
        zoom = min(
            self.viewport().width() / ((width + 2 * MARGIN) * points_to_px),
            (self.viewport().height() - self.top_inset) / ((height + 2 * PAGE_GAP) * points_to_px),
        )
        self._refit(zoom, "page")

    def _refit(self, zoom: float, fit: str) -> None:
        page = self._current
        self.set_zoom(zoom, fit=fit)
        self.goto_page(page)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit == "width":
            self.fit_width()
        elif self._fit == "page":
            self.fit_page()
        if self._held is not None:
            self._apply_hold()
        self._schedule_renders(0)

    # Keeping a point in place while the window around the pages changes ---

    def anchor(self) -> tuple[QPointF, QPoint]:
        """A point of the pages and where it is on the screen: under the pointer when it is over the pages,
        else in the middle of the reading area."""
        viewport = self.viewport()
        pos = viewport.mapFromGlobal(QCursor.pos())
        if not viewport.rect().contains(pos):
            pos = QPoint(viewport.width() // 2, (self.top_inset + viewport.height()) // 2)
        return QPointF(self.mapToScene(pos)), viewport.mapToGlobal(pos)

    def hold(self, anchor: tuple[QPointF, QPoint], ms: int = HOLD_MS) -> None:
        """Keep `anchor` (from anchor()) where it is on the screen while the view is resized in the next `ms`:
        as side panels and bars come and go, or the window goes full screen, the line read stays put. The
        pages refit as they would, around it. The user's first scroll, zoom or click lets go."""
        self._held = anchor
        self._settled = None
        self._apply_hold()
        self._hold_timer.start(ms)

    def release_hold(self) -> None:
        """The reader moved (or the pages changed): nothing is held any more."""
        self._held = self._settled = None
        self._hold_timer.stop()

    def _hold_done(self) -> None:
        """The time is up: remember where the held point left the pages, to tell later whether they moved."""
        if self._held is not None:
            self._settled = (self._held, self._view_state())
        self._held = None

    def _view_state(self) -> tuple:
        return self.horizontalScrollBar().value(), self.verticalScrollBar().value(), self._zoom

    def still_at(self, anchor) -> bool:
        """Whether the pages are where hold(anchor) put them: the reader has not scrolled or zoomed since."""
        if self._held is anchor:
            return True
        return self._settled is not None and self._settled[0] is anchor and self._settled[1] == self._view_state()

    def _apply_hold(self) -> None:
        scene_point, global_point = self._held
        want = self.viewport().mapFromGlobal(global_point)
        now = self.mapFromScene(scene_point)
        horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
        horizontal.setValue(horizontal.value() + now.x() - want.x())
        vertical.setValue(vertical.value() + now.y() - want.y())

    def wheelEvent(self, event) -> None:
        self._leave_link()
        self.release_hold()  # the reader moves on: no point is held in place any more
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl != (self._pref("wheel") == "zoom"):  # Ctrl swaps scrolling and zooming
            delta = event.angleDelta().y()
            if delta and delta % 120 == 0:
                # A mouse wheel notch: move along the ladder, whose levels are pre-rendered.
                zoom = self.ladder_step(self._zoom, delta // 120)
            else:
                zoom = self._zoom * 1.2 ** (delta / 120)  # touchpad pinch: continuous
            self.set_zoom(zoom, anchor=event.position())
            event.accept()
            return
        speed = self._pref("scroll_speed") / 100
        if abs(speed - 1) < 0.01:
            super().wheelEvent(event)
            return
        self._scroll_by_wheel(event, speed)

    def _scroll_by_wheel(self, event, speed: float) -> None:
        """Scroll like Qt would, times `speed`: whole lines for a mouse wheel, pixels for a touchpad."""
        pixels = event.pixelDelta()
        if not pixels.isNull():
            dx, dy = pixels.x(), pixels.y()
        else:
            angle = event.angleDelta()
            lines = QApplication.wheelScrollLines()
            dx = angle.x() / 120 * lines * self.horizontalScrollBar().singleStep()
            dy = angle.y() / 120 * lines * self.verticalScrollBar().singleStep()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = dy, dx
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(dx * speed))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(dy * speed))
        event.accept()

    # Navigation ---------------------------------------------------------

    @property
    def current_page(self) -> int:
        return self._current

    def goto_page(self, index: int) -> None:
        index = min(max(index, 0), len(self._pages) - 1)
        top = self._tops[index] - (MARGIN if index == 0 else PAGE_GAP / 2) - self.top_inset / self._view_scale()
        current_top = self.mapToScene(QPoint(0, 0)).y()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + round((top - current_top) * self._view_scale()))
        self._set_current(index)

    def next_page(self) -> None:
        self.goto_page(self._current + 1)

    def previous_page(self) -> None:
        self.goto_page(self._current - 1)

    def spot(self) -> Spot:
        """Where the reader is: the page and the height on it at the top of the reading area, under the toolbar."""
        scene_y = self.mapToScene(QPoint(0, self.top_inset)).y()
        # The gap above a page counts as that page's: Home or Ctrl+G leave the top line there.
        index = min(max(bisect.bisect_right(self._tops, scene_y + PAGE_GAP) - 1, 0), len(self._pages) - 1)
        return Spot(index, scene_y - self._pages[index].y())

    def _scroll_to(self, scene_y: float, row: int) -> None:
        """Scroll so the scene height `scene_y` sits on the viewport's pixel row `row`."""
        bar = self.verticalScrollBar()
        here = self.mapToScene(QPoint(0, row)).y()
        bar.setValue(bar.value() + round((scene_y - here) * self._view_scale()))

    def go_to(self, page: int, y: float | None = None) -> None:
        """Show `page`: from its top, or with `y` (display points down the page) with that height a little
        below the toolbar, so the line a link or bookmark points at is the first one read."""
        if y is None:
            self.goto_page(page)
            return
        page = min(max(page, 0), len(self._pages) - 1)
        item = self._pages[page]
        self._scroll_to(item.y() + min(max(y, 0.0), item.rect.height()), self.top_inset + LANDING)
        self._set_current(page)

    def jump(self, page: int, y: float | None = None) -> None:
        """go_to(), remembering where from: Back (Alt+Left) comes back here. A jump that didn't move adds nothing."""
        here = self.spot()
        self.go_to(page, y)
        there = self.spot()
        if there.page != here.page or abs(there.y - here.y) >= NEAR:
            self.history.push(here)
            self.history_changed.emit()

    def back(self) -> None:
        """Back to where the last jump started, exactly as it was on screen."""
        spot = self.history.back(self.spot())
        if spot is not None:
            self._show_spot(spot)
            self.history_changed.emit()

    def forward(self) -> None:
        """Forward again to where Back came from."""
        spot = self.history.forward(self.spot())
        if spot is not None:
            self._show_spot(spot)
            self.history_changed.emit()

    def _show_spot(self, spot: Spot) -> None:
        page = min(max(spot.page, 0), len(self._pages) - 1)
        self._scroll_to(self._pages[page].y() + spot.y, self.top_inset)
        self._on_scrolled()  # the current page, as scrolling there sets it

    def _on_scrolled(self) -> None:
        self._leave_link()
        self._schedule_renders(0)
        self.content_changed.emit()
        bar = self.verticalScrollBar()
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            self._set_current(len(self._pages) - 1)
            return
        # The current page is the one crossing a line a third of the way down the viewport.
        probe = self.mapToScene(QPoint(0, self.top_inset + (self.viewport().height() - self.top_inset) // 3)).y()
        self._set_current(max(0, bisect.bisect_right(self._tops, probe) - 1))

    def _set_current(self, index: int) -> None:
        if index != self._current:
            self._current = index
            self.current_page_changed.emit(index)

    # Tools and mouse ----------------------------------------------------

    def _tool_key(self) -> str:
        """The tool the pages answer to: Pan in reading mode, else the one turned on."""
        return "hand" if self.reading else self.tool_settings.tool

    def _tool(self) -> Tool | None:
        name = base_of(self._tool_key())  # copies of a tool ("pen#2") share its Tool
        if self.document.read_only and name not in VIEW_ONLY_TOOLS:
            return None
        return self._tools.get(name)

    def stop_tools(self) -> None:
        """Every tool drops what it is in the middle of, with its helper items, and the snapping guides go."""
        self._active = self._active_page = None
        for tool in self._tools.values():
            tool.cancel()
        self.snapper.hide()

    def end_interaction(self) -> None:
        """Finish typing and any drag still going, before something else changes the document (an undo, a page
        edit): an erase in progress goes on the undo stack first, as a step of its own. A polyline being
        clicked out stays."""
        self.finish_text_edit()
        if self._active is not None:  # keys and timers work while the mouse is down
            tool, self._active, self._active_page = self._active, None, None
            tool.cancel()

    def _apply_tool(self, key: str) -> None:
        name = base_of(key)
        self._leave_link()
        if self._active is not None:
            self._active.cancel()
            self._active = self._active_page = None
        for tool in self._tools.values():
            if tool.busy or tool.wants_hover:
                tool.cancel()
        if name != "select":
            self.scene().clearSelection()
            self.clear_text_selection()  # text is selected with Select; other tools let it go, as annotations
        # Links are invisible and simply followed, except with the hyperlink tool, which edits them.
        annotation_items.links.visible = name == "hyperlink"
        for item in self._items.values():
            if isinstance(item.annotation, Link):
                item.refresh_flags()
        self.viewport().update()
        rubber_band = name == "select" and not self.reading
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if rubber_band else QGraphicsView.DragMode.NoDrag)
        self._set_cursor()

    def set_reading(self, on: bool) -> None:
        """Reading mode: the pages answer as to Pan whatever tool is on (a drag moves them, a click follows
        a link), and what marks a selection hides. Nothing is lost: on leaving, the tool and the selection
        are as they were."""
        if on == self.reading:
            return
        self._leave_link()
        if on:
            self.finish_text_edit()  # typed text is kept, not left half done
            if self._active is not None:
                self._active.cancel()
                self._active = self._active_page = None
            self.snapper.hide()
        self.reading = on
        self._press_link = None
        for item in self._items.values():
            item.show_marks(not on)
        if self._text_selection is not None and alive(self._text_selection[3]):
            self._text_selection[3].setVisible(not on)
        rubber_band = base_of(self.tool_settings.tool) == "select" and not on
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if rubber_band else QGraphicsView.DragMode.NoDrag)
        QToolTip.hideText()
        self._set_cursor()

    def _set_cursor(self, shape: Qt.CursorShape | None = None) -> None:
        if shape is None:
            if self._space_down or self._tool_key() == "hand":
                shape = Qt.CursorShape.OpenHandCursor
            else:
                tool = self._tool()
                shape = tool.cursor if tool is not None else Qt.CursorShape.ArrowCursor
        self.viewport().setCursor(shape)

    def _annotation_item_at(self, view_pos: QPointF) -> AnnotationItem | None:
        for item in self.items(view_pos.toPoint()):
            if isinstance(item, Handle):
                return item.owner
            if isinstance(item, AnnotationItem) and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                return item
        return None

    def mousePressEvent(self, event) -> None:
        self._leave_link()
        self.release_hold()
        self._followed = False
        if event.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):  # the mouse's side buttons
            self.back() if event.button() == Qt.MouseButton.BackButton else self.forward()
            event.accept()
            return
        left = event.button() == Qt.MouseButton.LeftButton
        if left and not self._space_down and self._press_on_editor(event):
            return
        panning = event.button() == Qt.MouseButton.MiddleButton or (
            left and (self._space_down or self._tool_key() == "hand")
        )
        if panning:
            # Reading: a press on a link pans like any other, and follows the link if it ends without a drag.
            reading_click = left and self.reading and not self._space_down
            self._press_link = self._link_target(event.position()) if reading_click else None
            self._press_at = event.position().toPoint()
            self._pan_from = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if not left:
            super().mousePressEvent(event)
            return
        self.finish_text_edit()
        tool = self._tool()
        if tool is None:
            return
        if tool.name == "select" and not event.modifiers():
            target = self._link_target(event.position())
            if target is not None:
                self.follow_link(*target)
                self._followed = True
                event.accept()
                return
        if tool.name == "select" and self._annotation_item_at(event.position()) is not None:
            self.clear_text_selection()
            super().mousePressEvent(event)  # Qt selects, moves, and drives the handles
            return
        page, pos = self._page_at(event.position())
        if page is not None and tool.press(page, pos, event):
            self._active, self._active_page = tool, page
            event.accept()
            return
        if tool.name == "select":
            self.clear_text_selection()
            super().mousePressEvent(event)  # rubber-band selection

    def _over_editor(self, view_pos: QPointF) -> tuple[bool, bool]:
        """Whether the pointer is on the text being typed in, and whether it is inside that text box at all."""
        if self._editor is None:
            return False, False
        editor = self._editor[0]
        scene_pos = self.mapToScene(view_pos.toPoint())
        on_text = alive(editor) and editor.sceneBoundingRect().contains(scene_pos)
        in_box = on_text or (alive(editor.frame) and editor.frame.sceneBoundingRect().contains(scene_pos))
        return on_text, in_box

    def _press_on_editor(self, event, double: bool = False) -> bool:
        """A press inside the text box being typed in stays there: on the text it moves the caret (a double click
        selects a word), below the last line the typing simply goes on. False if the press is elsewhere."""
        on_text, in_box = self._over_editor(event.position())
        if on_text:
            if double:
                super().mouseDoubleClickEvent(event)
            else:
                super().mousePressEvent(event)
        elif in_box:
            self._editor[0].setFocus()
            event.accept()
        return in_box

    def leaveEvent(self, event) -> None:
        self._mouse = None
        self._leave_link()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._leave_link()  # another tab, or the page manager: no card waits to come back with the pages
        super().hideEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._mouse = event.position()
        if self._active is None and self._pan_from is None and event.buttons() == Qt.MouseButton.NoButton:
            tool = self._tool()
            if tool is not None and tool.wants_hover:
                page, pos = self._page_at(event.position())
                if page is not None:
                    tool.hover(page, pos)
        if self._pan_from is not None:
            delta = event.position().toPoint() - self._pan_from
            self._pan_from = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self._active is not None:
            page = self._active_page
            local = page.mapFromScene(self.mapToScene(event.position().toPoint()))
            local = QPointF(min(max(local.x(), 0), page.rect.width()), min(max(local.y(), 0), page.rect.height()))
            self._active.move(page, local, event)
            return
        super().mouseMoveEvent(event)
        if event.buttons() == Qt.MouseButton.NoButton:
            self._update_hover_cursor(event.position())

    def snapped(self, page, pos: QPointF, exclude: set[str] = frozenset()) -> QPointF:
        """`pos` on `page` (a PageItem), moved onto the nearest snapping line if snapping is on."""
        return self.snapper.snap_point(page.index, pos, exclude)

    def mouseReleaseEvent(self, event) -> None:
        self.snapper.hide()
        if self._pan_from is not None:
            self._pan_from = None
            self._set_cursor()
            link, self._press_link = self._press_link, None
            moved = (event.position().toPoint() - self._press_at).manhattanLength()
            if link is not None and moved < QApplication.startDragDistance():  # a click on a link, not a drag
                self.follow_link(*link)
                self._followed = True
            return
        if self._active is not None:
            tool, page = self._active, self._active_page
            self._active = None
            local = page.mapFromScene(self.mapToScene(event.position().toPoint()))
            tool.release(page, local, event)
            return
        super().mouseReleaseEvent(event)
        self._commit_moves()

    def _update_hover_cursor(self, view_pos: QPointF) -> None:
        if self.reading:
            self._reading_hover(view_pos)
            return
        if self.tool_settings.tool != "select" or self._space_down:
            self._leave_link()
            return
        if self._over_editor(view_pos)[1]:
            self._leave_link()
            self._set_cursor(Qt.CursorShape.IBeamCursor)
            return
        target = self._link_target(view_pos)
        if target is not None:
            self._set_cursor(Qt.CursorShape.PointingHandCursor)
            kind, value, point = target
            if kind == "page" and self._pref("link_preview"):  # a card shows the spot instead of a tooltip
                QToolTip.hideText()
                self._on_link = True
                self.link_hovered.emit(int(value), point, view_pos.toPoint())
                return
            self._leave_link()
            tip = value if kind == "uri" else f"Go to page {value + 1}"
            if QToolTip.text() != tip:
                QToolTip.showText(self.viewport().mapToGlobal(view_pos.toPoint()), tip, self.viewport())
            return
        self._leave_link()
        QToolTip.hideText()
        item = self._annotation_item_at(view_pos)
        if item is not None:
            movable = item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            self._set_cursor(Qt.CursorShape.SizeAllCursor if movable else Qt.CursorShape.PointingHandCursor)
            return
        page, pos = self._page_at(view_pos)
        over_text = page is not None and self.document.char_at(page.index, (pos.x(), pos.y())) >= 0
        self._set_cursor(Qt.CursorShape.IBeamCursor if over_text else Qt.CursorShape.ArrowCursor)

    def _reading_hover(self, view_pos: QPointF) -> None:
        """Reading mode: the open hand, or a pointing hand over a link with a tooltip of where it goes. No card
        floats over the pages, so nothing hides what is read."""
        target = None if self._space_down else self._link_target(view_pos)
        if target is None:
            QToolTip.hideText()
            self._set_cursor()
            return
        self._set_cursor(Qt.CursorShape.PointingHandCursor)
        kind, value, _point = target
        tip = value if kind == "uri" else f"Go to page {value + 1}"
        if QToolTip.text() != tip:
            QToolTip.showText(self.viewport().mapToGlobal(view_pos.toPoint()), tip, self.viewport())

    def _leave_link(self) -> None:
        """The pointer is no longer on the link link_hovered named, or the pages moved under it."""
        if self._on_link:
            self._on_link = False
            self.link_left.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
                self.mousePressEvent(event)  # the second press pans, or goes back or forward once more, too
            else:
                super().mouseDoubleClickEvent(event)
            return
        if not self._space_down and self._press_on_editor(event, double=True):
            return
        if self._followed:  # the first click followed a link: the second lands on another page, and does nothing
            self._followed = False
            event.accept()
            return
        active = self._tool_key()
        tool = self._tool()
        if tool is not None and tool.name != "select" and not self._space_down:
            page, pos = self._page_at(event.position())
            if page is not None and tool.double_click(page, pos):
                event.accept()
                return
        # Select edits what is double-clicked. Pan, where unlocked tools go after one use, only plays or opens
        # attachments: listening to a note is reading, like following a link.
        item = self._annotation_item_at(event.position()) if active in ("select", "hand") else None
        annotation = item.annotation if item is not None else None
        if isinstance(annotation, Attachment) and not self._space_down:
            self.open_attachment(annotation, event.globalPosition().toPoint())
        elif active != "select" or self._space_down:
            self.mousePressEvent(event)  # the second click of a quick pair is a press of its own: a dot, a pan
        elif annotation is None and self._select_word_at(event.position()):
            event.accept()  # on text: the word, for the bar over selected text
        elif self.document.read_only:
            super().mouseDoubleClickEvent(event)
        elif isinstance(annotation, FreeText):
            self.edit_text_box(annotation, is_new=False)
        elif isinstance(annotation, Note):
            self.edit_note(annotation)
        elif isinstance(annotation, Tape):
            from .drawing_tools import toggle_tape

            toggle_tape(self, annotation)
        elif isinstance(annotation, ImageStamp) and annotation.equation:
            self.edit_equation(annotation)
        else:
            super().mouseDoubleClickEvent(event)

    def _select_word_at(self, view_pos: QPointF) -> bool:
        page, pos = self._page_at(view_pos)
        return page is not None and self.select_word(page.index, (pos.x(), pos.y()))

    def keyPressEvent(self, event) -> None:
        if self._editor is not None:
            super().keyPressEvent(event)  # typing goes to the text box editor
            return
        tool = self._tool()
        if tool is not None and tool.busy and tool.key_press(event):
            return
        self._leave_link()
        self.release_hold()
        key = event.key()
        plain = not event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self._set_cursor()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_annotations():
            if not self.reading:  # nothing shows as selected while reading: Delete must not remove it unseen
                self.delete_selected()
        elif key == Qt.Key.Key_Escape:
            if not self.reading:  # in reading mode the window takes Esc, to leave it
                self._escape()
        elif key == Qt.Key.Key_Home:
            self.jump(0)
        elif key == Qt.Key.Key_End:
            self.jump(len(self._pages) - 1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Left) and plain and not self.horizontalScrollBar().maximum():
            self.next_page() if key == Qt.Key.Key_Right else self.previous_page()  # Alt+Left and Right go back
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            self._set_cursor()
        super().keyReleaseEvent(event)

    def _escape(self) -> None:
        tool = self._tool()
        if tool is not None and tool.busy:
            tool.cancel()
        elif self._active is not None:
            self._active.cancel()
            self._active = None
        elif self._text_selection is not None:
            self.clear_text_selection()
        elif self.scene().selectedItems():
            self.scene().clearSelection()
        else:
            self.tool_settings.set_tool("select")

    def contextMenuEvent(self, event) -> None:
        from .context_menu import build_context_menu

        if self._active is not None:  # a right click in the middle of a drag: the menu will take the release
            tool, self._active, self._active_page = self._active, None, None
            tool.cancel()
        view_pos = QPointF(event.pos())
        item = self._annotation_item_at(view_pos)
        if item is not None and not item.isSelected():
            self.scene().clearSelection()
            item.setSelected(True)
        page, pos = self._page_at(view_pos)
        if page is None:
            page = self._pages[self._current]
            pos = None
        menu = build_context_menu(self, page.index, pos, item.annotation if item is not None else None)
        menu.exec(event.globalPos())
        menu.deleteLater()

    # Clipboard and select all -------------------------------------------

    def copy_annotations(self) -> bool:
        selected = self.selected_annotations()
        if selected:
            clipboard.copy_annotations(selected)
        return bool(selected)

    def cut_annotations(self) -> None:
        """Copy the selection, then take away what isn't locked: a locked annotation stays, as with Delete."""
        if self.copy_annotations():
            removable = [a for a in self.selected_annotations() if not a.locked]
            if removable:
                self.undo_stack.push(RemoveAnnotations(self.store, removable, "Cut"))

    def can_cut(self) -> bool:
        return any(not a.locked for a in self.selected_annotations())

    def paste_annotations(self, page: int | None = None, at: tuple[float, float] | None = None) -> None:
        pasted = clipboard.paste_annotations(self._current if page is None else page, at)
        lost = {a.id for a in clipboard.unreachable(pasted, self.document.page_count)}
        if lost:
            pasted = [a for a in pasted if a.id not in lost]
            self.message.emit(clipboard.left_out(len(lost)))
        if pasted:
            # On the page whole, as a group: pasted by the edge, or onto a smaller page, it moves in.
            x, y, w, h = union(a.bounds() for a in pasted)
            width, height = self.document.page_size(pasted[0].page)
            dx = min(max(x, 0.0), max(width - w, 0.0)) - x
            dy = min(max(y, 0.0), max(height - h, 0.0)) - y
            if dx or dy:
                pasted = [a.translated(dx, dy) for a in pasted]
            self.undo_stack.push(AddAnnotations(self.store, pasted, "Paste"))
            self.scene().clearSelection()
            for annotation in pasted:
                self._items[annotation.id].setSelected(True)

    def can_paste(self) -> bool:
        mime = QApplication.clipboard().mimeData()
        return mime is not None and (mime.hasFormat(clipboard.ANNOTATIONS_MIME) or mime.hasFormat(clipboard.PAGES_MIME)
                                     or clipboard.has_image() or mime.hasText())

    def paste(self, page: int | None = None, at: tuple[float, float] | None = None) -> None:
        """Ctrl+V: whatever the clipboard holds, in the most useful form.

        Annotations paste as annotations, copied pages go in after the current
        page, a picture lands on the page as an image, and text becomes a text box.
        Without a place, things go where the pointer is, or in the middle of the screen.
        """
        if self.document.read_only:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return
        if page is None:
            page, at = self._paste_target()
        if mime.hasFormat(clipboard.ANNOTATIONS_MIME):
            self.paste_annotations(page, at)
        elif mime.hasFormat(clipboard.PAGES_MIME) and self.actions is not None:
            self.actions.paste_pages(page)
        elif clipboard.has_image():
            self.paste_image(page, at)
        elif mime.hasText() and mime.text().strip() and self.actions is not None:
            self.actions.paste_text_box(page, at)

    def _paste_target(self) -> tuple[int, tuple[float, float] | None]:
        """The page and point under the pointer, else the current page and no point."""
        if self._mouse is not None and self.viewport().rect().contains(self._mouse.toPoint()):
            page, pos = self._page_at(self._mouse)
            if page is not None:
                return page.index, (pos.x(), pos.y())
        return self._current, None

    def paste_image(self, page: int, at: tuple[float, float] | None = None) -> None:
        mime = QApplication.clipboard().mimeData()
        files = clipboard.image_files(mime)
        if files:
            self.place_image_files(files, page, at)
            return
        picture = clipboard.image()
        if picture is not None and not picture.isNull():
            self.place_images([images.encode(picture)], page, at, "Paste image")

    def insert_image_file(self, page: int | None = None, at: tuple[float, float] | None = None) -> None:
        from .page_actions import IMAGE_FILES

        paths, _ = QFileDialog.getOpenFileNames(self, "Insert image", str(self.document.path.parent), IMAGE_FILES)
        if paths:
            if page is None:
                page, at = self._current, None
            self.place_image_files(paths, page, at, "image")

    def place_image_files(self, paths: list[str], page: int, at: tuple[float, float] | None,
                          tool: str | None = None) -> None:
        read = [(path, images.from_file(path)) for path in paths]
        pictures = [data for _, data in read if data]
        failed = [Path(path).name for path, data in read if not data]
        if failed:
            messages.warn(self, "Some files were left out", "They could not be read as pictures.",
                          detail="\n".join(failed))
        self.place_images(pictures, page, at, "Insert image", tool)

    def place_images(self, pictures: list[bytes], page: int, at: tuple[float, float] | None, label: str,
                     tool: str | None = None) -> None:
        """Put pictures on `page`, centred on `at` (else on the middle of the screen), a little apart.

        `tool` is "image" or "camera" when one of those tools brought them: its size option and subject
        apply. Pasted and dropped pictures keep the size they had where they came from.
        """
        if not pictures or self.document.read_only:
            return
        width, height = self.document.page_size(page)
        centre = at or self.visible_centre(page)
        size = self.tool_settings.style_for(tool).image_size if tool else "actual"
        common = self.common(tool) if tool else dict(author=self.tool_settings.author)
        made = []
        for number, data in enumerate(pictures):
            w, h = self.picture_size(data, page, size)
            x = min(max(centre[0] - w / 2 + 16 * number, 0.0), max(width - w, 0.0))
            y = min(max(centre[1] - h / 2 + 16 * number, 0.0), max(height - h, 0.0))
            made.append(ImageStamp(page=page, rect=(x, y, w, h), data=data, **common))
        self.undo_stack.push(AddAnnotations(self.store, made, label))
        self.tool_settings.set_tool("select")
        self.select(made)

    def picture_size(self, data: bytes, page: int, size: str = "actual") -> tuple[float, float]:
        """How big a picture lands. With "actual": one picture pixel per screen pixel at the current zoom,
        so it looks as it did where it was copied, but never wider or taller than 80% of the page.
        Otherwise a share of the page's width (IMAGE_SIZES), and never taller than the page."""
        pixels_w, pixels_h = images.size(data)
        if pixels_w <= 0 or pixels_h <= 0:
            return 100.0, 100.0
        share = IMAGE_SIZES.get(size, 0.0)
        if share > 0:
            page_w, page_h = self.document.page_size(page)
            w = page_w * share
            h = w * pixels_h / pixels_w
            fit = min(1.0, page_h / h)
            return w * fit, h * fit
        per_point = self._view_scale() * self.viewport().devicePixelRatioF()
        w, h = pixels_w / per_point, pixels_h / per_point
        page_w, page_h = self.document.page_size(page)
        fit = min(1.0, 0.8 * page_w / w, 0.8 * page_h / h)
        grow = max(1.0, 24.0 / min(w * fit, h * fit))  # tiny icons still get a usable size
        return w * fit * grow, h * fit * grow

    def dragEnterEvent(self, event) -> None:
        if not self.document.read_only and (clipboard.image_files(event.mimeData()) or event.mimeData().hasImage()):
            event.acceptProposedAction()
        else:
            event.ignore()  # PDFs and the rest: the window opens them

    def dragMoveEvent(self, event) -> None:
        if not self.document.read_only and (clipboard.image_files(event.mimeData()) or event.mimeData().hasImage()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        page, pos = self._page_at(event.position())
        if page is None:
            event.ignore()
            return
        at = (pos.x(), pos.y())
        files = clipboard.image_files(mime)
        if files:
            self.place_image_files(files, page.index, at)
        elif mime.hasImage():
            self.place_images([images.encode(mime.imageData())], page.index, at, "Drop image")
        event.acceptProposedAction()

    # Links ----------------------------------------------------------------

    def _link_target(self, view_pos: QPointF):
        """Where a link under the pointer leads: ("uri", address, None), or ("page", index, spot) with the spot
        on that page in its PDF space, or None for the whole page. None if there is no link."""
        if annotation_items.links.visible:
            return None  # the hyperlink tool edits links instead of following them
        page, pos = self._page_at(view_pos)
        if page is None:
            return None
        for item in self._items.values():
            link = item.annotation
            if isinstance(link, Link) and link.page == page.index and QRectF(*link.rect).contains(pos):
                if link.uri:
                    return "uri", link.uri, None
                # A page this document has: a link made in a longer one goes nowhere here.
                return ("page", link.target, link.target_point) if 0 <= link.target < self.document.page_count \
                    else None
        try:
            return self.document.link_at(page.index, (pos.x(), pos.y()))
        except Exception:
            return None

    def follow_link(self, kind: str, value, point=None) -> None:
        """Go where a link leads. A page is a jump, which Back undoes, to the spot `point` (PDF space) if given."""
        if kind == "page":
            page = int(value)
            if 0 <= page < self.document.page_count:
                self.jump(page, self.document.display_y(page, point))
            return
        address = str(value)
        if messages.confirm_once(self, self.settings, "open_links", "Open link", f"Open this address?\n\n{address}",
                                 action="Open", icon="link"):
            QDesktopServices.openUrl(QUrl(address))

    def common(self, tool: str) -> dict:
        """The author and subject an annotation made outside a Tool (menus, dialogs) gets from `tool`."""
        return dict(author=self.tool_settings.author, subject=self.tool_settings.subject_for(tool))

    def new_link(self, page: int, rect) -> Link | None:
        """Ask where a link over `rect` goes and add it, in the hyperlink tool's style. None if cancelled."""
        from .insert_dialogs import LinkDialog

        dialog = LinkDialog(self.document.page_count, parent=self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        uri, target = dialog.result_link() if accepted else ("", -1)
        dialog.deleteLater()
        if not (uri or target >= 0):
            return None
        style = self.tool_settings.style_for("hyperlink")
        link = Link(page=page, rect=rect, uri=uri, target=target, color=style.color,
                    border=1.0 if style.link_border else 0.0, **self.common("hyperlink"))
        self.add_annotations([link], "Link")
        return link

    def edit_link(self, link: Link) -> None:
        from .insert_dialogs import LinkDialog

        dialog = LinkDialog(self.document.page_count, link.uri, link.target, existing=True, parent=self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            return
        if dialog.removed:
            self.undo_stack.push(RemoveAnnotations(self.store, [link], "Remove link"))
            return
        uri, target = dialog.result_link()
        changed = replace(link, uri=uri, target=target)
        if changed.uri or changed.target != link.target:
            changed = replace(changed, target_point=None)  # the spot belonged to the old page
        if changed != link:
            self.undo_stack.push(ChangeAnnotations(self.store, [link], [changed], "Change link"))

    # Insert tools -----------------------------------------------------------

    def run_action(self, key: str) -> None:
        """Toolbar tools that act once instead of staying on: image, camera, bookmark."""
        if self.document.read_only:
            return
        key = base_of(key)
        if key == "image":
            self.insert_image_file()
        elif key == "camera":
            from .insert_dialogs import camera_dialog

            dialog = camera_dialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.photo:
                self.place_image_files([dialog.photo], self._current, None, "camera")
                if getattr(dialog, "temporary", False):  # the picture is in the PDF now
                    Path(dialog.photo).unlink(missing_ok=True)
            dialog.deleteLater()
        elif key == "bookmark" and self.actions is not None:
            self.actions.add_bookmark(self._current)

    def item_contains(self, annotation: Annotation, pos: QPointF) -> bool:
        return hit_path(annotation).contains(pos)

    def area_image(self, page: int, rect, scale: float, annotations: bool = True, tint: str = "normal") -> QImage:
        """Part of a page, `rect` in points, as an image at `scale` pixels a point, with the annotations on it
        unless told not to. Only that part is rendered, so a small area of a huge page stays cheap.
        A `tint` (TINTS) colours the page as the canvas does, under the annotations."""
        width, height = self.document.page_size(page)
        x, y, w, h = rect
        left, top = max(x, 0.0), max(y, 0.0)
        right, bottom = min(x + w, width), min(y + h, height)
        region = (round(left * scale), round(top * scale), max(1, round((right - left) * scale)),
                  max(1, round((bottom - top) * scale)))
        data, pixels_w, pixels_h, stride = self.document.render_region(page, scale, region)
        image = QImage(data, pixels_w, pixels_h, stride, QImage.Format.Format_RGB32).copy()
        if tint == "dark":
            image.invertPixels()
        elif tint == "sepia":
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            painter.fillRect(image.rect(), SEPIA)
            painter.end()
        if annotations:
            area = (left, top, right - left, bottom - top)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(-region[0], -region[1])
            painter.scale(scale, scale)
            for annotation in self.store.all():
                if annotation.page == page and overlaps(annotation.bounds(), area):
                    paint_annotation(painter, annotation)
            painter.end()
        return image

    def copy_area_as_image(self, page: int, rect, scale: float = 3.0, annotations: bool = True) -> None:
        QApplication.clipboard().setImage(self.area_image(page, rect, scale, annotations))
        self.message.emit("Snapshot copied. Paste it here or in any app.")

    def insert_at(self, tool: str, page: int, point, global_pos) -> None:
        """Signature, audio note and equation tools: place one where the user clicked."""
        if tool == "signature":
            self._signature_menu(page, point, global_pos)
        elif tool == "audio":
            self._audio_note(page, point, global_pos)
        elif tool == "equation":
            self._new_equation(page, point)

    def _place_stamp(self, stamp: ImageStamp, label: str, tool: str) -> None:
        width, height = self.document.page_size(stamp.page)
        x, y, w, h = stamp.rect
        x, y = min(max(x, 0.0), max(width - w, 0.0)), min(max(y, 0.0), max(height - h, 0.0))
        placed = replace(stamp, rect=(x, y, w, h))
        self.add_annotations([placed], label)
        self.created([placed], tool)

    def _new_equation(self, page: int, point) -> None:
        from .insert_dialogs import EquationDialog, equation_picture

        style = self.tool_settings.style_for("equation")
        dialog = EquationDialog(size=style.font_size, color=style.color, parent=self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        source, size = dialog.equation()
        dialog.deleteLater()
        if not accepted:
            return
        data, w, h = equation_picture(source, size, style.color)
        self._place_stamp(ImageStamp(page=page, rect=(point[0], point[1] - h / 2, w, h), data=data, equation=source,
                                     equation_size=size, equation_color=style.color, **self.common("equation")),
                          "Equation", "equation")

    def edit_equation(self, stamp: ImageStamp) -> None:
        """Change an equation's source or size. It keeps its colour, and the scale the user gave it on the page."""
        from .insert_dialogs import EquationDialog, equation_picture

        size = stamp.equation_size or self.tool_settings.style_for("equation").font_size
        color = stamp.equation_color if stamp.equation_color is not None else (0.0, 0.0, 0.0)
        dialog = EquationDialog(stamp.equation, size, color, parent=self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        source, new_size = dialog.equation()
        dialog.deleteLater()
        if not accepted or (source == stamp.equation and new_size == size):
            return  # nothing to change: the picture stays exactly as it was drawn
        # As big as before, relative to its natural size: a picture the user made larger stays larger.
        _, natural_w, natural_h = equation_picture(stamp.equation, size, color)
        data, fresh_w, fresh_h = equation_picture(source, new_size, color)
        shown_w, shown_h = stamp.upright_size()
        new_w = shown_w * (fresh_w / natural_w) if natural_w > 0 else fresh_w
        new_h = shown_h * (fresh_h / natural_h) if natural_h > 0 else fresh_h
        x, y, _, _ = stamp.rect
        rect = (x, y, new_h, new_w) if stamp.rotation % 2 else (x, y, new_w, new_h)
        changed = replace(stamp, data=data, equation=source, equation_size=new_size, equation_color=color, rect=rect)
        if changed != stamp:
            self.undo_stack.push(ChangeAnnotations(self.store, [stamp], [changed], "Edit equation"))

    def _audio_note(self, page: int, point, global_pos=None) -> None:
        """Record a note or attach a sound file, as the audio tool is set to, and put it where the user clicked."""
        from . import audio_dialog

        style = self.tool_settings.style_for("audio")
        chosen = audio_dialog.ask_audio(self, style.audio_source, str(self.document.path.parent), global_pos)
        if chosen is None:
            return
        name, data = chosen
        width, height = self.document.page_size(page)
        x = min(max(point[0] - NOTE_SIZE / 2, 0.0), max(width - NOTE_SIZE, 0.0))
        y = min(max(point[1] - NOTE_SIZE / 2, 0.0), max(height - NOTE_SIZE, 0.0))
        note = Attachment(page=page, position=(x, y), file_name=name, data=data,
                          audio=name.lower().endswith(AUDIO_SUFFIXES), color=style.color, **self.common("audio"))
        self.add_annotations([note], note.label)
        self.created([note], "audio")
        if note.audio:
            self.message.emit("Double-click the note to play it.")

    def open_attachment(self, attachment: Attachment, global_pos=None) -> None:
        """Play an audio note right here, by the note; open any other file with the app Windows uses for it."""
        if attachment.audio:
            from . import audio_dialog

            if global_pos is None:
                x, y = attachment.position
                global_pos = self.viewport().mapToGlobal(
                    self.mapFromScene(self._pages[attachment.page].mapToScene(QPointF(x + NOTE_SIZE, y + NOTE_SIZE))))
            audio_dialog.play_audio(self, attachment.file_name, attachment.data, global_pos)
            return
        name = attachment_file_name(attachment.file_name)
        suffix = Path(name).suffix.lower()
        if suffix in RISKY_SUFFIXES:
            if not messages.confirm(self, "Open a program?", f"{name} is a program, a script or a file with macros. "
                                    "Open it only if you trust whoever sent this PDF.", "Open", danger=True,
                                    icon="warning-circle"):
                return
        elif suffix not in SAFE_SUFFIXES and not messages.confirm(
                self, "Open the attachment?", f"{name} came inside this PDF. Open it only if you trust whoever sent "
                "the PDF.", "Open", icon="paperclip"):
            return
        target = Path(tempfile.gettempdir()) / "zenith-pdf" / attachment.digest / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(attachment.data)
        except OSError as error:
            messages.warn(self, "Can't open the attachment", f"{name} could not be copied out of the PDF.",
                          detail=error.strerror or str(error))
            return
        mark_downloaded(target)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def save_attachment(self, attachment: Attachment) -> None:
        """Save the file an attachment or audio note carries, where the user says."""
        start = self.document.path.parent / attachment_file_name(attachment.file_name)
        target, _ = QFileDialog.getSaveFileName(self, "Save attachment as", str(start))
        if not target:
            return
        try:
            Path(target).write_bytes(attachment.data)
        except OSError as error:
            messages.warn(self, "The file could not be saved", f"{Path(target).name} was not written.",
                          detail=error.strerror or str(error))

    def _signature_menu(self, page: int, point, global_pos) -> None:
        from .insert_dialogs import SignatureDialog, signature_icon

        saved = self.settings.get("signatures") if self.settings is not None else []
        record = None
        if saved:  # pick one; with none saved yet, the dialog for a new one opens at once
            menu = QMenu(self)
            for record in saved:
                menu.addAction(signature_icon(record), record["name"]).setData(record)
            menu.addSeparator()
            new = menu.addAction(icon("plus"), "New signature…")
            remove = menu.addMenu(icon("trash"), "Delete a signature")
            for position, record in enumerate(saved):
                remove.addAction(record["name"]).setData(position)
            from .help import explain_menu

            chosen = explain_menu(menu).exec(global_pos)
            menu.deleteLater()
            if chosen is None:
                return
            if chosen.parent() is remove:
                del saved[chosen.data()]
                self.settings.set("signatures", saved)
                return
            record = None if chosen is new else chosen.data()
        if record is None:
            dialog = SignatureDialog(self.tool_settings.author, self)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            record = dialog.signature() if accepted else None
            dialog.deleteLater()
            if record is None:
                return
            if self.settings is not None:
                self.settings.set("signatures", saved + [record])
        data = base64.b64decode(record["png"])
        pixels_w, pixels_h = images.size(data)
        width = self.tool_settings.style_for("signature").signature_width
        height = width * pixels_h / max(pixels_w, 1)
        if height > width * 0.4:  # tall signatures: limit the height instead
            width, height = width * width * 0.4 / height, width * 0.4
        self._place_stamp(ImageStamp(page=page, rect=(point[0] - width / 2, point[1] - height / 2, width, height),
                                     data=data, **self.common("signature")), "Signature", "signature")

    def copy(self) -> None:
        """Ctrl+C: the selected annotations if any, else the selected text."""
        if not self.copy_annotations():
            self.copy_selection()

    def has_text_selection(self) -> bool:
        return self._text_selection is not None

    def text_selection(self) -> tuple[int, list, str] | None:
        """The selected text: (page, its display-space rectangles, the text), or None."""
        if self._text_selection is None:
            return None
        page, rects, text, _preview = self._text_selection
        return page, list(rects), text

    def page_width(self, page: int) -> float:
        return self._pages[page].rect.width()

    def new_text_box(self, page: int, at: tuple[float, float] | None) -> None:
        style = self.tool_settings.style_for("text")
        x, y = at or (72.0, 72.0)
        width = max(min(200.0, self._pages[page].rect.width() - x), 40.0)
        box = FreeText(
            page=page,
            rect=(x, y, width, textlayout.fitted_height("", style.font_size, width)),
            font_size=style.font_size,
            color=style.color,
            fill=style.fill,
            **self.common("text"),
        )
        self.edit_text_box(box, is_new=True, tool="text")

    def new_note(self, page: int, at: tuple[float, float] | None) -> None:
        text = messages.get_multiline_text(self, "New note", "What the note says.", action="Add note", required=True)
        if text is not None and text.strip():
            style = self.tool_settings.style_for("note")
            x, y = at or (36.0, 36.0)
            note = Note(page=page, position=(x - NOTE_SIZE / 2, y - NOTE_SIZE / 2), contents=text, color=style.color,
                        icon=style.note_icon, **self.common("note"))
            self.add_annotations([note], note.label)

    def select_all_annotations(self, page: int) -> None:
        self.tool_settings.set_tool("select")
        self.clear_text_selection()
        self.scene().clearSelection()
        for item in self._items.values():
            if item.annotation.page == page and item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                item.setSelected(True)

    def select_all_text(self, page: int) -> None:
        count = self.document.char_count(page)
        if count <= 0:
            return
        self.tool_settings.set_tool("select")
        self.scene().clearSelection()
        self._select_text(page, 0, count - 1)

    def page_image(self, index: int, scale: float) -> QImage:
        """A page with its annotations, as an image: for printing and copying."""
        image = self.document.render(index, scale)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(scale, scale)
        for annotation in self.store.all():
            if annotation.page == index:
                paint_annotation(painter, annotation)
        painter.end()
        return image

    # Annotation items ---------------------------------------------------

    def _add_item(self, annotation: Annotation) -> None:
        item = AnnotationItem(annotation, self._pages[annotation.page])
        item.on_resized = self._on_item_resized
        item.setZValue(Z_ANNOTATION + len(self._items) * Z_STEP)  # new ones go on top
        item.show_marks(not self.reading)
        self._items[annotation.id] = item

    def _restack(self) -> None:
        """Stack items in the store's drawing order."""
        for position, annotation_id in enumerate(self.store.order()):
            item = self._items.get(annotation_id)
            if item is not None:
                item.setZValue(Z_ANNOTATION + position * Z_STEP)

    def _remove_item(self, annotation: Annotation) -> None:
        item = self._items.pop(annotation.id)
        item.setSelected(False)
        self.scene().removeItem(item)

    def _change_item(self, old: Annotation, new: Annotation) -> None:
        item = self._items[new.id]
        if old.page != new.page and 0 <= new.page < len(self._pages):  # moved to another page, or back
            item.setParentItem(self._pages[new.page])
        item.set_annotation(new)

    def _on_item_resized(self, old: Annotation, new: Annotation) -> None:
        self.undo_stack.push(ChangeAnnotations(self.store, [old], [new], f"Resize {new.label.lower()}"))

    def _commit_moves(self) -> None:
        old, new = [], []
        for item in self.scene().selectedItems():
            if isinstance(item, AnnotationItem) and item.pos() != QPointF(0, 0):
                old.append(item.annotation)
                new.append(self._landed(item.annotation.translated(item.pos().x(), item.pos().y())))
        if new:
            self.undo_stack.push(ChangeAnnotations(self.store, old, new, "Move"))

    def _landed(self, annotation: Annotation) -> Annotation:
        """Where a moved annotation belongs. Dropped with its centre on another page, it goes to that page;
        dropped off every page, it is pulled back onto its own."""
        source = self._pages[annotation.page]
        x, y, w, h = annotation.bounds()
        centre = source.mapToScene(QPointF(x + w / 2, y + h / 2))
        if source.rect.contains(source.mapFromScene(centre)):
            return annotation
        target = next((page for page in self._pages if page.rect.contains(page.mapFromScene(centre))), None)
        if target is None:
            return self._clamped(annotation, annotation.page)
        shift = source.pos() - target.pos()  # pages are only placed side by side and one under another
        moved = replace(annotation.translated(shift.x(), shift.y()), page=target.index)
        return self._clamped(moved, target.index)

    def _clamped(self, annotation: Annotation, page: int) -> Annotation:
        """`annotation` moved just enough to lie on `page` (its top left corner, if it is bigger than the page)."""
        width, height = self.document.page_size(page)
        x, y, w, h = annotation.bounds()
        dx = min(max(x, 0.0), max(width - w, 0.0)) - x
        dy = min(max(y, 0.0), max(height - h, 0.0)) - y
        return annotation.translated(dx, dy) if dx or dy else annotation

    def _on_selection_changed(self) -> None:
        selected = self.selected_annotations()
        if selected:
            self.clear_text_selection()  # one selection at a time, with one bar over it
        self.selection_changed.emit(selected)

    def selected_annotations(self) -> list[Annotation]:
        return [i.annotation for i in self.scene().selectedItems() if isinstance(i, AnnotationItem)]

    def add_annotations(self, annotations: list[Annotation], label: str) -> None:
        self.undo_stack.push(AddAnnotations(self.store, annotations, f"Add {label.lower()}"))

    def created(self, annotations: list[Annotation], tool: str | None = None) -> None:
        """A tool just made these: select them if the user wants that, then count it as one use of the tool.

        `tool` is the base tool they came from when that may not be the active one (a menu can add an
        equation while the pen is on): a use only counts for the active tool.
        """
        if self.tool_settings.select_new:
            self.select(annotations)
        self.tool_used(annotations, tool)

    def tool_used(self, made=(), tool: str | None = None) -> None:
        """The active tool was used once. A locked tool stays on; an unlocked one hands over to the tool the
        "after drawing" setting names, Pan or Select. With "select new annotations" on, Select takes over
        with what was just made selected."""
        settings = self.tool_settings
        if (tool is not None and base_of(settings.tool) != tool) or settings.is_locked():
            return
        made = list(made)
        target = "select" if settings.after_drawing == "select" or (settings.select_new and made) else "hand"
        if target == "hand" and self.settings is not None and "hand" not in toolbar_keys(
                self.settings.get("toolbar_tools")):
            target = "select"  # Pan is off the toolbar: nothing there would show it is on
        settings.handing_over = True  # a pinned menu stays with the tool that handed over
        try:
            settings.set_tool(target)
        finally:
            settings.handing_over = False
        if settings.tool == "select" and settings.select_new:
            self.select(made)

    def delete_selected(self) -> None:
        deletable = [a for a in self.selected_annotations() if not a.locked]
        if deletable:
            self.undo_stack.push(RemoveAnnotations(self.store, deletable, "Delete"))

    def select(self, annotations: list[Annotation]) -> None:
        self.scene().clearSelection()
        for annotation in annotations:
            item = self._items.get(annotation.id)
            if item is not None:
                item.setSelected(True)

    def duplicate_selected(self) -> None:
        copies = [replace(a, id=new_id(), locked=False).translated(12, 12)
                  for a in self.selected_annotations() if not isinstance(a, TextMarkup)]
        if copies:
            self.undo_stack.push(AddAnnotations(self.store, copies, "Duplicate"))
            self.select(copies)

    def toggle_lock(self) -> None:
        selected = self.selected_annotations()
        if not selected:
            return
        lock = not all(a.locked for a in selected)
        self.undo_stack.push(ChangeAnnotations(self.store, selected, [replace(a, locked=lock) for a in selected],
                                               "Lock" if lock else "Unlock"))

    def arrange(self, how: str) -> None:
        """Change drawing order: "front", "forward", "backward" or "back"."""
        old = self.store.order()
        chosen = {a.id for a in self.selected_annotations()}
        if not chosen:
            return
        if how == "front":
            new = [i for i in old if i not in chosen] + [i for i in old if i in chosen]
        elif how == "back":
            new = [i for i in old if i in chosen] + [i for i in old if i not in chosen]
        else:
            # One step past the next annotation on the same page, in the chosen direction.
            new = list(old) if how == "forward" else list(reversed(old))
            page_of = {a.id: a.page for a in self.store.all()}
            for index in reversed(range(len(new))):
                current = new[index]
                if current not in chosen:
                    continue
                later = [j for j in range(index + 1, len(new)) if page_of[new[j]] == page_of[current]]
                if later and new[later[0]] not in chosen:
                    new.insert(later[0], new.pop(index))
            if how == "backward":
                new.reverse()
        if new != old:
            labels = {"front": "Bring to front", "forward": "Bring forward", "backward": "Send backward", "back": "Send to back"}
            self.undo_stack.push(ReorderAnnotations(self.store, old, new, labels[how]))

    def apply_to_pages(self, pages: list[int]) -> int:
        """Copy the selected annotations to the same place on other pages. Returns how many."""
        sources = [a for a in self.selected_annotations() if not isinstance(a, TextMarkup)]
        copies = [self._clamped(replace(a, page=p, id=new_id()), p) for p in pages for a in sources
                  if p != a.page and 0 <= p < self.document.page_count]  # a smaller page: kept on it
        if copies:
            self.undo_stack.push(AddAnnotations(self.store, copies, "Apply to pages"))
        return len(copies)

    def visible_centre(self, page: int) -> tuple[float, float]:
        """The middle of what is on screen (below the toolbar), in the page's points."""
        middle = QPoint(self.viewport().width() // 2, (self.top_inset + self.viewport().height()) // 2)
        local = self._pages[page].mapFromScene(self.mapToScene(middle))
        rect = self._pages[page].rect
        return min(max(local.x(), 0.0), rect.width()), min(max(local.y(), 0.0), rect.height())

    def selection_rect(self) -> QRect | None:
        """Where the selection is on screen, in viewport pixels."""
        area = QRectF()
        for item in self.scene().selectedItems():
            if isinstance(item, AnnotationItem):
                area = area.united(item.sceneBoundingRect())
        return self.mapFromScene(area).boundingRect() if not area.isEmpty() else None

    def copy_selection_as_image(self) -> None:
        selected = self.selected_annotations()
        if not selected:
            return
        page = selected[0].page
        x, y, w, h = union(a.bounds() for a in selected if a.page == page)
        QApplication.clipboard().setImage(self.area_image(page, (x - 4, y - 4, w + 8, h + 8), 3.0))

    def apply_style(self, **changes) -> bool:
        """Apply style changes to the selected annotations. False if nothing was selected."""
        old, new = [], []
        for annotation in self.selected_annotations():
            names = {f.name for f in fields(annotation)}
            valid = {k: v for k, v in changes.items() if k in names}
            if "color" in valid and "border" in names and "border" not in changes and annotation.border is False:
                # A colour picked for a mixed selection is a border colour: a fill-only shape would not show it.
                valid["border"] = True
            updated = replace(annotation, **valid)
            if isinstance(annotation, Ink) and annotation.widths and "width" in valid and annotation.width > 0:
                factor = valid["width"] / annotation.width  # a pen with widths of its own keeps its shape, scaled
                widths = tuple(tuple(w * factor for w in stroke) for stroke in annotation.widths)
                updated = replace(updated, widths=widths)
            if getattr(updated, "border", True) is False and getattr(updated, "fill", None) is None:
                updated = replace(updated, border=True)  # with neither, a shape would vanish
            if updated != annotation:
                old.append(annotation)
                new.append(updated)
        if new:
            self.undo_stack.push(ChangeAnnotations(self.store, old, new, "Change style"))
        return bool(self.selected_annotations())

    def reveal_annotation(self, annotation_id: str) -> None:
        item = self._items.get(annotation_id)
        if item is None:
            return
        self.tool_settings.set_tool("select")
        self.scene().clearSelection()
        item.setSelected(True)
        self.ensureVisible(item.sceneBoundingRect(), 60, 100)
        self._set_current(item.annotation.page)
        self.setFocus()

    # Text selection -----------------------------------------------------

    def set_text_selection(self, page: int, rects, text: str, preview) -> None:
        if self._text_selection is not None:
            remove_item(self._text_selection[3])  # replaced: no "nothing selected" in between
        self._text_selection = (page, rects, text, preview)
        self.text_selection_changed.emit(self.text_selection_rect())

    def clear_text_selection(self) -> None:
        if self._text_selection is not None:
            remove_item(self._text_selection[3])
            self._text_selection = None
            self.text_selection_changed.emit(None)

    def text_selection_rect(self) -> QRect | None:
        """Where the selected text is on screen, in viewport pixels; None when no text is selected."""
        if self._text_selection is None:
            return None
        page, rects, _, _ = self._text_selection
        if not rects or not 0 <= page < len(self._pages):
            return None
        area = self._pages[page].mapRectToScene(QRectF(*union(rects)))
        return self.mapFromScene(area).boundingRect()

    def selected_text(self) -> str:
        return self._text_selection[2] if self._text_selection is not None else ""

    def _select_text(self, page: int, first: int, last: int) -> None:
        """Select the characters first..last of `page`, as a drag over them would."""
        rects, text = self.document.text_span(page, first, last)
        preview = QGraphicsPathItem(self._pages[page])
        preview.setZValue(3)
        preview.setPen(Qt.PenStyle.NoPen)
        preview.setBrush(QBrush(TEXT_SELECTION))
        path = QPainterPath()
        for rect in rects:
            path.addRect(QRectF(*rect))
        preview.setPath(path)
        self.set_text_selection(page, rects, text, preview)

    def select_word(self, page: int, point) -> bool:
        """Select the word at `point` (display points on `page`), as a double click does. False if none is there."""
        span = self.document.word_at(page, point)
        if span is None:
            return False
        self.scene().clearSelection()
        self._select_text(page, *span)
        return True

    def copy_selection(self) -> None:
        if self._text_selection is not None:
            QApplication.clipboard().setText(self._text_selection[2])

    def mark_text_selection(self, tool: str, color=None, contents: str = "") -> TextMarkup | None:
        """Mark the selected text with a markup tool ("highlight", "underline", "strikeout" or "squiggly") in its
        style, then let the selection go. A `color` is used and becomes the tool's colour; `contents` is a
        comment the mark carries. Returns the markup, or None if nothing could be marked."""
        if self._text_selection is None or self.document.read_only:
            return None
        page, rects, _, _ = self._text_selection
        if not rects:
            return None
        if color is not None:  # text is selected with Select on: the tool's own style, not a copy's
            self.tool_settings.update_style(base_of(tool), color=color)
        style = self.tool_settings.style_for(tool)
        x, y, w, h = rects[0]
        first = self.document.char_at(page, (x + w / 2, y + h / 2), tolerance=6)
        markup = TextMarkup(
            page=page,
            style=MarkupTool.STYLES[base_of(tool)],
            quads=rects,
            color=style.color,
            opacity=style.opacity,
            contents=contents,
            rotation=self.document.text_turns(page, first),  # along vertical text, too
            **self.common(tool),
        )
        self.clear_text_selection()
        self.add_annotations([markup], markup.label)
        return markup

    # Editing text -------------------------------------------------------

    def edit_text_box(self, box: FreeText, is_new: bool, tool: str | None = None) -> None:
        """Type into a text box on the page. `tool` is the tool a new box came from, "text" or "callout"."""
        self.finish_text_edit()
        item = self._items.get(box.id)
        if item is not None:
            item.setSelected(False)
            item.hide()
        editor = TextBoxEditor(box, self._pages[box.page], lambda text: self._end_text_edit(box, is_new, text))
        self._editor = (editor, item, tool)
        self.setFocus()
        editor.setFocus()
        self.ensureVisible(editor.frame.sceneBoundingRect(), 40, 60)

    def is_editing_text(self) -> bool:
        return self._editor is not None

    def finish_text_edit(self) -> None:
        if self._editor is not None:
            self._editor[0].finish()

    def _end_text_edit(self, box: FreeText, is_new: bool, text: str) -> None:
        editor, item, tool = self._editor
        self._editor = None
        # This runs inside the editor's own event handler: it goes once that returns, or before a reload.
        self._spent_editors.append(editor)
        QTimer.singleShot(0, self._dispose_editors)
        if item is not None:
            item.show()
        upright_w, upright_h = box.upright_size()  # the text runs across the box as the user reads it
        needed = textlayout.fitted_height(text, box.font_size, upright_w)
        # A turned box grows away from its first line, which stays where it was typed.
        updated = replace(box.with_height(needed if is_new else max(upright_h, needed)), contents=text)
        if is_new:
            if text.strip():
                self.add_annotations([updated], updated.label)
                self.created([updated], tool)
        elif not text.strip():
            self.undo_stack.push(RemoveAnnotations(self.store, [box], "Delete text box"))
        elif updated != box:
            self.undo_stack.push(ChangeAnnotations(self.store, [box], [updated], "Edit text"))

    def _dispose_editors(self) -> None:
        """Take finished text editors off the canvas. Safe when a reload has deleted them already."""
        editors, self._spent_editors = self._spent_editors, []
        for editor in editors:
            remove_item(editor.frame)
            remove_item(editor)
            remove_item(editor.holder)

    def edit_note(self, note: Note) -> None:
        text = messages.get_multiline_text(self, "Note", "What the note says.", note.contents, action="Save")
        if text is not None and text != note.contents:
            self.undo_stack.push(ChangeAnnotations(self.store, [note], [replace(note, contents=text)], "Edit note"))

    # Rendering ----------------------------------------------------------

    def _schedule_renders(self, delay_ms: int) -> None:
        self._render_timer.start(delay_ms)

    def _pages_in(self, rect: QRectF):
        first = max(0, bisect.bisect_right(self._tops, rect.top()) - 1)
        for page in self._pages[first:]:
            if page.y() > rect.bottom():
                break
            yield page

    def _want_tiles(self, wanted: dict, rect: QRectF, scale: float, tier: int, limit: int | None = None) -> None:
        """Add the missing tiles covering `rect` (scene) at `scale`, nearest the centre first."""
        key = scale_key(scale)
        centre = rect.center()
        found = []
        for page in self._pages_in(rect):
            area = page.mapRectFromScene(rect).intersected(page.rect)
            size = (page.rect.width(), page.rect.height())
            for col, row, cell in tile_grid(size, scale, area):
                if self._cache.has(page.index, key, col, row):
                    continue
                middle = page.mapToScene(QPointF((cell.x() + cell.width() / 2) / scale, (cell.y() + cell.height() / 2) / scale))
                distance = (middle - centre).manhattanLength()
                area = render_area(size, scale, cell)
                region = (area.x(), area.y(), area.width(), area.height())
                found.append((distance, (self._token, "tile", page.index, key, col, row), page.index, scale, region))
        found.sort(key=lambda entry: entry[0])
        for distance, job_key, page_index, job_scale, region in found[:limit]:
            wanted.setdefault(job_key, ((tier, distance), page_index, job_scale, region))

    def missing_tiles(self) -> int:
        """On-screen tiles not yet rendered at the exact zoom: 0 means the view is fully sharp."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        scale = self._view_scale() * self.viewport().devicePixelRatioF()
        key = scale_key(scale)
        missing = 0
        for page in self._pages_in(visible):
            area = page.mapRectFromScene(visible).intersected(page.rect)
            size = (page.rect.width(), page.rect.height())
            missing += sum(1 for col, row, _ in tile_grid(size, scale, area) if not self._cache.has(page.index, key, col, row))
        return missing

    def _neighbour_view(self, zoom: float) -> QRectF:
        """Where the view would land after zooming to `zoom` around the cursor (or the centre)."""
        anchor = self._mouse or QPointF(self.viewport().rect().center())
        anchor_scene = self.mapToScene(anchor.toPoint())
        scale = zoom * self.logicalDpiX() / 72.0
        size = self.viewport().size()
        return QRectF(
            anchor_scene.x() - anchor.x() / scale,
            anchor_scene.y() - anchor.y() / scale,
            size.width() / scale,
            size.height() / scale,
        )

    def _update_renders(self) -> None:
        """Decide what should be rendered now, in order of urgency:

        0  tiles on screen at the exact zoom
        1  low-resolution bases of on-screen pages, shown while tiles arrive
        2  tiles just beyond the edges, for scrolling
        3  bases of nearby pages
        4  tiles for the next zoom level up and down, so a wheel notch is instant
        """
        if not self._pages:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        width, height = visible.width(), visible.height()
        ratio = self.viewport().devicePixelRatioF()
        scale = self._view_scale() * ratio
        wanted: dict = {}

        self._want_tiles(wanted, visible, scale, 0)
        base_scale_view = BASE_ZOOM * self.logicalDpiX() / 72.0
        for page in self._pages_in(visible.adjusted(0, -2 * height, 0, 2 * height)):
            if page.base is None:
                on_screen = page.sceneBoundingRect().intersects(visible)
                distance = abs(page.sceneBoundingRect().center().y() - visible.center().y())
                size = (page.rect.width(), page.rect.height())
                base_scale = render_scale(size, base_scale_view, ratio)
                wanted[(self._token, "base", page.index)] = ((1 if on_screen else 3, distance), page.index, base_scale, None)
        self._want_tiles(wanted, visible.adjusted(-width / 4, -height / 2, width / 4, height / 2), scale, 2)
        if not any(entry[0][0] == 0 for entry in wanted.values()) and self._pref("prefetch_zoom"):
            levels = [z for z in levels_to_prepare(self._zoom, self._zoom_direction) if abs(z - self._zoom) > 1e-6]
            for rank, zoom in enumerate(levels):
                zoom_scale = zoom * self.logicalDpiX() / 72.0 * ratio
                # Tier 4 plus rank: the most likely level is rendered first.
                self._want_tiles(wanted, self._neighbour_view(zoom), zoom_scale, 4 + rank, SPECULATIVE_TILES // len(levels))

        self._renderer.request_many(
            (key, self.document, page, job_scale, priority, region)
            for key, (priority, page, job_scale, region) in wanted.items()
        )
        token = self._token
        self._renderer.discard(lambda key: key[0] is token and key not in wanted)

        keep = visible.adjusted(0, -4 * height, 0, 4 * height)
        for page in self._pages:
            if page.base is not None and not keep.intersects(page.sceneBoundingRect()):
                page.set_base(None)

    def _on_rendered(self, key, scale: float, image) -> None:
        if not isinstance(key, tuple) or key[0] is not self._token:
            return
        if key[1] == "base":
            self._pages[key[2]].set_base(QPixmap.fromImage(image))
        elif key[1] == "tile":
            _, _, page, level, col, row = key
            item = self._pages[page]
            size = (item.rect.width(), item.rect.height())
            cell = cell_rect(size, scale, col, row)
            area = render_area(size, scale, cell)
            source = QRect(cell.x() - area.x(), cell.y() - area.y(), cell.width(), cell.height())
            self._cache.put(page, level, col, row, Tile(QPixmap.fromImage(image), scale, cell, source))
            area = QRectF(cell.x() / scale, cell.y() / scale, cell.width() / scale, cell.height() / scale)
            if not self._sharp_under(page, area, level):
                item.update(area)
        self._followup_timer.start()

    def _sharp_under(self, page: int, area: QRectF, level: int) -> bool:
        """Whether a tile of another zoom level (`level`) landing on `area` of `page` changes nothing on screen:
        the tiles of the zoom shown cover all of it, and they are painted over every other level. Rendering ahead
        of the user (the zoom levels either side) then repaints nothing, and the glass need not resample."""
        scale = self._view_scale() * self.viewport().devicePixelRatioF()
        exact = scale_key(scale)
        if level == exact:
            return False
        item = self._pages[page]
        size = (item.rect.width(), item.rect.height())
        return all(self._cache.has(page, exact, col, row) for col, row, _ in tile_grid(size, scale, area))

    # Search highlights --------------------------------------------------

    def add_search_hit(self, hit: SearchHit) -> None:
        page = self._pages[hit.page]
        items = []
        for x, y, w, h in hit.rects:
            item = QGraphicsRectItem(x - 1, y - 1, w + 2, h + 2, page)
            item.setPen(Qt.PenStyle.NoPen)
            item.setBrush(QBrush(themes.current.search_hit))
            item.setZValue(1)
            items.append(item)
        self._hit_items[hit] = items

    def focus_search_hit(self, hit: SearchHit) -> None:
        for previous in self._hit_items.get(self._focused_hit, []):
            previous.setBrush(QBrush(themes.current.search_hit))
        self._focused_hit = hit
        items = self._hit_items.get(hit, [])
        if not items:
            return
        area = QRectF()
        for item in items:
            item.setBrush(QBrush(themes.current.search_current))
            area = area.united(item.sceneBoundingRect())
        self.ensureVisible(area, 80, 120)
        self._set_current(hit.page)

    def clear_search_hits(self) -> None:
        for items in self._hit_items.values():
            for item in items:
                self.scene().removeItem(item)
        self._hit_items.clear()
        self._focused_hit = None

    # Lifetime -----------------------------------------------------------

    def release(self) -> None:
        """Stop receiving renders, and let the tools and the text editor go; call before the document is closed."""
        if self._editor is not None:  # closing without saving: what was typed goes with the document
            self._spent_editors.append(self._editor[0])
            self._editor = None
        self._dispose_editors()
        self.stop_tools()
        token = self._token
        self._render_timer.stop()
        self._followup_timer.stop()
        self._renderer.discard(lambda key: isinstance(key, tuple) and key[0] is token)
        self._renderer.rendered.disconnect(self._on_rendered)
        self.tool_settings.tool_changed.disconnect(self._apply_tool)
        self._cache.clear()
