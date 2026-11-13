"""The home page: every file you have opened, with what you need to start working.

Down the left: all files, starred ones, pinned folders and tags. On top: search
and the quick actions. In the middle: the files, with a thumbnail, where they
are, who wrote them, their pages and when you last opened them; click a column
to sort. On the right, a way into the guide.

Facts about the files (pages, author, a thumbnail of the first page) are read
in the background and kept, so the list shows at once next time.

The page fits a narrow window: the guide column goes first, then the quick
actions lose their captions and their names, and the file list drops the
columns it can do without, so the name always has room.

A row of the list lights as a whole under the pointer and fades as the pointer
leaves; the picture of a page that has just been read fades in over its place;
a list with no files, and a search that matches none, say so with a picture.
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import shiboken6
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    QStandardPaths,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.document import before_exit
from ..core.i18n import tr
from ..core.platforms import native_keys
from ..icons import icon
from ..settings import Settings
from ..theme import outline, qcolor, themes
from .library_search import InsideResults
from . import messages, motion
from .help import attach_help
from .side_panel import FadingButton
from .widgets import IconButton, focus_ring, icon_pixmap, keyboard_focus, muted_label

INFO_LIMIT = 400  # files whose page count and author are remembered
ROW_HEIGHT = 56
THUMB_WIDTH = 34  # the widest a page's picture goes in the name column
# Tag colours, given in this order as tags are made. No red: it reads as an error.
TAG_COLOURS = ["#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899", "#64748b"]
TIPS = [
    "Hold Space and drag to move around the page with any tool.",
    "Ctrl+V pastes a picture you copied straight onto the page.",
    "Right-click a toolbar button to move it or take it off the toolbar.",
    "The + at the end of the toolbar opens every tool, from pens to measuring.",
    "Drag PDF files onto the window to open them, or image files onto a page to place them.",
    "Point at any button for a moment to see what it does.",
    "Turn on snapping, the magnet at the bottom right, to line annotations up.",
    "The Doc Builder joins several PDFs and lets you pick their pages.",
    "Tape hides answers while you study: click it to peek.",
    "Find and redact blacks out every place a name appears, for good.",
]
COLUMNS = ["", "Name", "Location", "Author", "Pages", "Last opened", "Tags", ""]
STAR, NAME, LOCATION, AUTHOR, PAGES, OPENED, TAGS, MORE = range(8)
COLUMN_WIDTHS = {STAR: 36, LOCATION: 160, AUTHOR: 140, PAGES: 60, OPENED: 120, TAGS: 150, MORE: 36}
LEAST_WIDTHS = {LOCATION: 110, AUTHOR: 100, PAGES: 56, OPENED: 100, TAGS: 100}  # narrower, a column goes
NAME_WIDTH = 260  # the least the name column gets; other columns shrink, then go, before it does
OPTIONAL_COLUMNS = (OPENED, TAGS, LOCATION, PAGES, AUTHOR)  # kept in this order while there is room
ASIDE_FROM = 1280  # page width from which the guide column shows
NAV_FROM = 760  # page width from which the left column stays; narrower, the menu button opens it over the files
NAV_WIDTH = 220
BUTTON_HEIGHT = 36
FULL_CARD = 150  # quick action width that fits its caption
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_ROLE = Qt.ItemDataRole.UserRole + 2
LOGO = Path(__file__).resolve().parents[1] / "assets" / "zenith.svg"
THUMBNAIL_AGE = 3600  # seconds an unlisted thumbnail is kept, in case its file is being read right now


def ago(moment: float | None) -> str:
    """ "3 minutes ago", "yesterday", "12/09/2026" """
    if not moment:
        return ""
    seconds = time.time() - moment
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 2 * 86400:
        return "Yesterday"
    return time.strftime("%d/%m/%Y", time.localtime(moment))


def short_folder(path: Path) -> str:
    parts = path.parent.parts
    return str(path.parent) if len(parts) <= 2 else "../" + "/".join(parts[-2:])


def logo_pixmap(size: int, ratio: float) -> QPixmap:
    """The logo drawn from its vector file at `size` logical pixels, sharp at `ratio`."""
    side = max(1, round(size * ratio))
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(str(LOGO))
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def cache_folder() -> Path:
    """Where thumbnails go: ZENITH_CACHE_DIR if set (tests, portable use), else the system cache."""
    override = os.environ.get("ZENITH_CACHE_DIR")
    if override:
        return Path(override) / "thumbnails"
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(folder or Path.home() / ".zenith-pdf") / "thumbnails"


@dataclass
class FileEntry:
    path: Path
    opened: float | None = None
    pages: int | None = None
    author: str = ""
    title: str = ""
    size: int = 0
    starred: bool = False
    tags: list = field(default_factory=list)
    thumbnail: QPixmap | None = None


# Every file-info thread with its loader's stop flag. Kept here, not with the loader: a home page
# deleted before the program ends leaves its thread working, and that one must be waited for too.
_THREADS: list[tuple[threading.Thread, threading.Event]] = []


def _stop_loaders(timeout: float = 5.0) -> None:
    """PDFium must be idle when Python takes it down: let file-info threads finish first."""
    for _, stop in _THREADS:
        stop.set()
    end = time.monotonic() + timeout
    for thread, _ in _THREADS:
        if thread is not threading.current_thread():
            thread.join(max(0.0, end - time.monotonic()))


before_exit(_stop_loaders)  # ahead of the finalizers that close pypdfium2's documents under a thread


class InfoLoader(QObject):
    """Reads page counts, authors and first-page thumbnails off the GUI thread."""

    loaded = Signal(str, dict, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder = cache_folder()
        self.folder.mkdir(parents=True, exist_ok=True)
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        stop = self._stop
        self.destroyed.connect(lambda *_: stop.set())  # no emitting into a deleted object
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def thumbnail_file(self, path: Path, mtime: float) -> Path:
        name = hashlib.sha1(f"{path}|{mtime}".encode("utf-8")).hexdigest()
        return self.folder / f"{name}.png"

    def request(self, paths: list[Path]) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            wanted = [p for p in paths if str(p) not in self._pending]
            self._pending.update(str(p) for p in wanted)
            self._threads = [t for t in self._threads if t.is_alive()]
        if wanted:
            thread = threading.Thread(target=self._work, args=(wanted,), name="file-info", daemon=True)
            self._threads.append(thread)
            _THREADS[:] = [(t, s) for t, s in _THREADS if t.is_alive()] + [(thread, self._stop)]
            thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop after the file being read, and wait for that (a thumbnail takes a moment)."""
        self._stop.set()
        end = time.monotonic() + timeout
        for thread in list(self._threads):
            if thread is not threading.current_thread():
                thread.join(max(0.0, end - time.monotonic()))

    def forget(self, names: set[str]) -> None:
        """Delete these thumbnails (file names), for files no longer remembered."""
        for name in names:
            (self.folder / name).unlink(missing_ok=True)

    def prune(self, keep: set[str]) -> None:
        """Delete thumbnails nothing lists any more, except fresh ones a thread may be about to list."""
        cutoff = time.time() - THUMBNAIL_AGE
        try:
            for file in self.folder.glob("*.png"):
                if file.name not in keep and file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
        except OSError:
            pass  # the cache is only a cache

    def _work(self, paths: list[Path]) -> None:
        import pypdfium2 as pdfium

        from ..core.document import PDFIUM_LOCK
        from ..core.render_worker import render_region

        for path in paths:
            if self._stop.is_set():
                return
            info, image = {}, QImage()
            try:
                stat = path.stat()
                info = {"mtime": stat.st_mtime, "size": stat.st_size}
                pdf_bytes = path.read_bytes()  # PDFium holding the file open would make saving it fail on Windows
                with PDFIUM_LOCK:
                    pdf = pdfium.PdfDocument(pdf_bytes)
                    try:
                        info["pages"] = len(pdf)
                        # Metadata can hold line breaks and runs of spaces: keep one line.
                        info["author"] = " ".join(pdf.get_metadata_value("Author").split())
                        info["title"] = " ".join(pdf.get_metadata_value("Title").split())
                        page = pdf[0]
                        try:
                            size = page.get_size()
                            scale = 96 / max(size)
                            data, w, h, stride = render_region(page, None, size, scale, None)
                            image = QImage(data, w, h, stride, QImage.Format.Format_RGB32).copy()
                        finally:
                            page.close()
                    finally:
                        pdf.close()
                image.save(str(self.thumbnail_file(path, stat.st_mtime)))
            except Exception:
                info.setdefault("pages", 0)  # protected or damaged: show the file without the extras
            with self._lock:
                self._pending.discard(str(path))
            if self._stop.is_set():
                return
            try:
                self.loaded.emit(str(path), info, image)
            except RuntimeError:
                return  # the home page went away meanwhile


class FileModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[FileEntry] = []

    def set_entries(self, entries: list[FileEntry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(COLUMNS[section])
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        entry = self.entries[index.row()]
        column = index.column()
        if role == FILE_ROLE:
            return entry
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == AUTHOR:
                return entry.author or None  # it may be cut short
            return str(entry.path) if column in (NAME, LOCATION) else None
        if role == SORT_ROLE:
            return {STAR: int(entry.starred), NAME: entry.path.name.lower(), LOCATION: str(entry.path.parent).lower(),
                    AUTHOR: entry.author.lower(), PAGES: entry.pages or 0, OPENED: entry.opened or 0.0,
                    TAGS: ",".join(entry.tags)}.get(column, "")
        if role == Qt.ItemDataRole.DisplayRole:
            return {NAME: entry.path.name, LOCATION: short_folder(entry.path), AUTHOR: entry.author,
                    PAGES: str(entry.pages) if entry.pages else "", OPENED: tr(ago(entry.opened))}.get(column, "")
        return None

    def changed(self, path: str) -> None:
        for row, entry in enumerate(self.entries):
            if str(entry.path) == path:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))


class FileFilter(QSortFilterProxyModel):
    """Search over name, folder, author, title and tags."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.words: list[str] = []
        self.setSortRole(SORT_ROLE)

    def set_text(self, text: str) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.9 and later
            self.beginFilterChange()
            self.words = text.lower().split()
            self.endFilterChange()
        else:
            self.words = text.lower().split()
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        if not self.words:
            return True
        entry = self.sourceModel().entries[row]
        text = f"{entry.path.name} {entry.path.parent} {entry.author} {entry.title} {' '.join(entry.tags)}".lower()
        return all(word in text for word in self.words)


class FileDelegate(QStyledItemDelegate):
    """Paints the thumbnail and name, the tag chips, the star and the menu button."""

    def __init__(self, home: HomePage):
        super().__init__(home)
        self._home = home

    def sizeHint(self, option, index) -> QSize:
        return QSize(100, ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        t = themes.current
        entry: FileEntry = index.data(FILE_ROLE)
        rect = option.rect
        over = bool(option.state & QStyle.StateFlag.State_MouseOver)  # the pointer is on this cell
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lit = self._home.row_light(entry.path)
        if lit > 0.0:  # the pointer is on the row, or has just left it: every cell of it, above the line
            fill = qcolor(t.pressed if self._home.row_pressed(entry.path) else t.hover)
            fill.setAlpha(round(fill.alpha() * lit))
            painter.fillRect(rect.adjusted(0, 0, 0, -1), fill)
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        column = index.column()
        middle = QPoint(rect.x() + rect.width() // 2, rect.y() + (rect.height() - 1) // 2)
        if column in (STAR, MORE):
            if over:  # a button: under the pointer it shows its shape
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(t.pressed))
                painter.drawPath(outline(QRectF(middle.x() - 14, middle.y() - 14, 28, 28), t.look.item, t.look))
            if column == STAR:
                glyph = icon("star-fill" if entry.starred else "star", "#f5b301" if entry.starred else t.text_muted)
                size = self._home.star_size(entry.path)
                painter.translate(middle)
                painter.scale(size, size)
                painter.translate(-middle)
            else:
                glyph = icon("dots-three-vertical", t.text if over else t.text_muted)
            glyph.paint(painter, QRect(middle.x() - 9, middle.y() - 9, 18, 18))
        elif column == NAME:
            self._paint_name(painter, option, entry)
        elif column == TAGS:
            self._paint_tags(painter, option, entry, over)
        else:
            painter.setFont(option.font)
            painter.setPen(QColor(t.text_muted if column != AUTHOR else t.text))
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""
            metrics = painter.fontMetrics()
            area = rect.adjusted(6, 0, -6, -1)
            # A folder keeps both ends, a name its start.
            mode = Qt.TextElideMode.ElideRight if column == AUTHOR else Qt.TextElideMode.ElideMiddle
            painter.drawText(area, Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(text, mode, area.width()))
        painter.restore()

    def _paint_name(self, painter: QPainter, option, entry: FileEntry) -> None:
        """The picture of the first page, and the name: its ending, ".pdf", quieter than the rest."""
        t = themes.current
        look = t.look
        rect = option.rect
        box = QRect(rect.x() + 4, rect.y() + 6, THUMB_WIDTH, rect.height() - 13)
        corner = min(look.chip, 3)
        shown = self._home.thumb_shown(entry.path)
        if entry.thumbnail is None or shown < 1.0:  # no picture yet, or one on its way in over this
            painter.save()
            painter.setOpacity(1.0 - shown if entry.thumbnail is not None else 1.0)
            paper = QRectF(0, 0, box.height() * 0.72, box.height())
            paper.moveCenter(QRectF(box).center())
            painter.setPen(QPen(QColor(t.border), 1))
            painter.setBrush(qcolor(t.hover))
            painter.drawPath(outline(paper.adjusted(0.5, 0.5, -0.5, -0.5), corner, look))
            icon("file-pdf", t.text_muted).paint(painter, QRect(round(paper.center().x()) - 8,
                                                                round(paper.center().y()) - 8, 16, 16))
            painter.restore()
        if entry.thumbnail is not None:
            pixmap = entry.thumbnail
            ratio = pixmap.devicePixelRatio()
            fitted = pixmap.size().scaled(box.size() * ratio, Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(0, 0, fitted.width() / ratio, fitted.height() / ratio)
            target.moveCenter(QRectF(box).center())
            page = outline(target, corner, look)
            painter.save()
            painter.setOpacity(shown)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 80 if t.name == "dark" else 38))
            painter.drawPath(page.translated(0, 1))  # a thin shadow: the page rests on the row
            painter.setClipPath(page)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            painter.setClipping(False)
            painter.setPen(QPen(QColor(t.border), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline(target.adjusted(0.5, 0.5, -0.5, -0.5), corner, look))
            painter.restore()
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        area = rect.adjusted(4 + THUMB_WIDTH + 10, 0, -6, -1)
        ending = entry.path.suffix
        name = metrics.elidedText(entry.path.stem, Qt.TextElideMode.ElideMiddle,
                                  max(area.width() - metrics.horizontalAdvance(ending), 0))
        painter.setPen(QColor(t.text))
        painter.drawText(area, Qt.AlignmentFlag.AlignVCenter, name)
        painter.setPen(QColor(t.text_muted))
        painter.drawText(area.adjusted(metrics.horizontalAdvance(name), 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, ending)

    def _paint_tags(self, painter: QPainter, option, entry: FileEntry, over: bool) -> None:
        """The tag chips as far as they fit, then how many more there are; with none, "Add tag" under the pointer."""
        t = themes.current
        look = t.look
        rect = option.rect
        painter.setFont(option.font)
        metrics = painter.fontMetrics()
        round_ = 11 if look.chip >= 5 else look.chip  # pills, while the style rounds chips as Zenith does
        x, right = rect.x() + 4, rect.right() - 4
        for position, tag in enumerate(entry.tags):
            more = len(entry.tags) - position - 1  # the tags after this one
            reserve = metrics.horizontalAdvance(f"+{more}") + 8 if more else 0
            text, width = tag, metrics.horizontalAdvance(tag) + 16
            if x + width + reserve > right:
                if position:  # no room for this one: the rest is a count
                    painter.setPen(QColor(t.text_muted))
                    painter.drawText(QRect(x, rect.y(), right - x, rect.height() - 1), Qt.AlignmentFlag.AlignVCenter,
                                     f"+{more + 1}")
                    return
                width = max(right - x - reserve, 32)  # a first chip too long for the column is cut to fit
                text = metrics.elidedText(tag, Qt.TextElideMode.ElideRight, width - 16)
            chip = QRectF(x, rect.y() + (rect.height() - 1) / 2 - 11, width, 22)
            colour = QColor(self._home.tag_colour(tag))
            fill = QColor(colour)
            fill.setAlpha(46)
            painter.setPen(QPen(colour, 1))
            painter.setBrush(fill)
            painter.drawPath(outline(chip, round_, look))
            painter.setPen(QColor(t.text))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
            x += width + 4
        if not entry.tags and over:  # an extra: only under the pointer
            painter.setPen(QColor(t.text_muted))
            middle = rect.y() + (rect.height() - 1) // 2
            icon("tag", t.text_muted).paint(painter, QRect(x, middle - 8, 16, 16))
            area = QRect(x + 22, rect.y(), rect.right() - 4 - (x + 22), rect.height() - 1)
            painter.drawText(area, Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(tr("Add tag"), Qt.TextElideMode.ElideRight, area.width()))


class Tile(QLabel):
    """An icon, in the accent, on a rounded square tinted as the style tints what is chosen."""

    def __init__(self, icon_name: str, size: int, glyph: int, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self._glyph = glyph
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        themes.changed.connect(self.recolour)
        self.recolour()

    def set_icon(self, icon_name: str) -> None:
        self.icon_name = icon_name
        self.recolour()

    def recolour(self, *_) -> None:
        self.setPixmap(icon_pixmap(self.icon_name, self._glyph, themes.current.accent))

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(t.selection))
        painter.drawPath(outline(QRectF(self.rect()), min(t.look.card, self.width() / 3), t.look))
        painter.end()
        super().paintEvent(event)


class EmptyState(QWidget):
    """What a list says when it has nothing to show: a picture, what is missing, and what to do about it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.glyph = Tile("files", 64, 28)
        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = muted_label()
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 0, 40, 0)
        layout.setSpacing(6)
        layout.addStretch(2)
        layout.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.text)
        layout.addStretch(3)

    def show_state(self, icon_name: str, title: str, text: str = "") -> None:
        """The state, in words already in the language in use."""
        self.glyph.set_icon(icon_name)
        self.title.setText(title)
        self.text.setText(native_keys(text))
        self.text.setVisible(bool(text))


class NavButton(QPushButton):
    """A row of the left column: icon, label, and a count on the right.

    The fill under the pointer is painted here, to fade it (the page's style sheet leaves it to the button); the
    chosen row's fill stays the sheet's, so each style keeps its own way of marking it.
    """

    def __init__(self, icon_name: str, text: str, key: str, colour: str | None = None, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon_name
        self.colour = colour
        self.count = ""
        self.label = text
        self.setObjectName("HomeNav")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setToolTip(tr(text))
        self.setAccessibleName(tr(text))  # its words are painted, not set
        self._fill = motion.Fill(self)
        self._fill.changed.connect(self.update)
        self.toggled.connect(lambda _: self._settle())
        self.pressed.connect(self._settle)
        self.released.connect(self._settle)

    def _settle(self, hover: bool | None = None) -> None:
        """Send the fill where it belongs: under the pointer or pressed, else clear."""
        t = themes.current
        hot = (self.underMouse() if hover is None else hover) and self.isEnabled() and not self.isChecked()
        self._fill.go(qcolor(t.pressed if self.isDown() else t.hover) if hot else QColor(0, 0, 0, 0),
                      motion.FAST if hot else motion.NORMAL)

    def enterEvent(self, event) -> None:
        self._settle(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._settle(False)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        t = themes.current
        radius = min(18 if t.look.pill_buttons else t.look.control, self.height() / 2)  # as the sheet shapes it
        if self._fill.colour.alpha():
            under = QPainter(self)
            under.setRenderHint(QPainter.RenderHint.Antialiasing)
            under.setPen(Qt.PenStyle.NoPen)
            under.setBrush(self._fill.colour)
            under.drawPath(outline(QRectF(self.rect()), radius, t.look))
            under.end()
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        if self.colour:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.colour))
            painter.drawEllipse(QRectF(14, rect.center().y() - 5, 10, 10))
        # On the accent's tint, the accent; a style that selects with a neutral fill keeps the text colour.
        tinted = t.look.tint[0 if t.name == "dark" else 1] and t.look.checked_icon == "accent"
        if not self.colour:
            icon(self.icon_name, t.accent if self.isChecked() and tinted else t.text).paint(
                painter, QRect(10, rect.center().y() - 9, 18, 18))
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(t.accent_ink if self.isChecked() and tinted else t.text))
        metrics = painter.fontMetrics()
        room = rect.width() - (80 if self.count else 44)  # a row with no count lends its space to the name
        painter.drawText(QRect(38, 0, room, rect.height()), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(tr(self.label), Qt.TextElideMode.ElideRight, room))
        if self.count:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(QRect(rect.width() - 44, 0, 34, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.count)
        if keyboard_focus(self):
            focus_ring(painter, QRectF(rect), radius)


class QuickCard(QFrame):
    """A large button among the quick actions.

    In a narrow window it drops its caption ("compact"), then its name too ("icon"),
    and the tooltip says what it does. The style sheet draws its fill and edge, and what they do under the
    pointer; the card adds a press, an arrow that comes in at the corner as the pointer does, and the keyboard.
    """

    clicked = Signal()

    def __init__(self, icon_name: str, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setObjectName("QuickCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Tab reaches it; Enter and Space press it
        self.setAccessibleName(tr(title))
        self.setAccessibleDescription(tr(detail))
        self.icon_name = icon_name
        self.title = title
        self.detail_text = detail
        self.mode = "full"
        self._down = False
        self._press = motion.Fill(self)  # the fill of a press, fading in and out
        self._press.changed.connect(self.update)
        self._reach = motion.Smooth(self)  # how far the pointer or the keyboard has come: the arrow
        self._reach.changed.connect(self.update)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self.glyph = Tile(icon_name, 36, 20)
        self.name = QLabel(tr(title))
        self.name.setObjectName("PanelRowText")
        self.caption = muted_label(detail)
        self.caption.setWordWrap(True)
        layout.addWidget(self.glyph)
        layout.addSpacing(8)
        layout.addWidget(self.name)
        layout.addWidget(self.caption)
        layout.addStretch()

    def needs(self) -> int:
        """The width that shows the name without the caption."""
        self.name.ensurePolished()
        return self.name.fontMetrics().horizontalAdvance(self.title) + 30

    def set_mode(self, mode: str) -> None:
        """ "full", "compact" (no caption) or "icon" (only the icon)."""
        if mode == self.mode:
            return
        self.mode = mode
        self.caption.setVisible(mode == "full")
        self.name.setVisible(mode != "icon")
        self.layout().setAlignment(self.glyph, Qt.AlignmentFlag.AlignHCenter if mode == "icon" else Qt.AlignmentFlag(0))
        self.setToolTip("" if mode == "full" else f"{tr(self.title)}: {tr(self.detail_text)}")
        self.update()

    def minimumSizeHint(self) -> QSize:
        return QSize(64, super().minimumSizeHint().height())  # the tile and the margins round it

    def _settle(self, hover: bool | None = None) -> None:
        """Send the press and the arrow where they belong."""
        t = themes.current
        near = (self.underMouse() if hover is None else hover) or keyboard_focus(self)
        self._press.go(qcolor(t.pressed) if self._down else QColor(0, 0, 0, 0),
                       motion.FAST if self._down else motion.NORMAL)
        self._reach.go(1.0 if near else 0.0, motion.FAST if near else motion.NORMAL)

    def enterEvent(self, event) -> None:
        self._settle(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._settle(False)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._settle()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._settle()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._down = True
            self._settle()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._down = False
        self._settle()
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.clicked.emit()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the sheet's fill and edge
        t = themes.current
        radius = 2 if t.look.chamfer else t.look.card  # as the sheet shapes it
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._press.colour.alpha():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._press.colour)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        reach = self._reach.value
        if reach > 0.0 and self.mode != "icon":
            tinted = t.look.tint[0 if t.name == "dark" else 1] and t.look.checked_icon == "accent"
            painter.setOpacity(reach)
            nudge = round(4 * (1.0 - reach))  # it comes from the corner it points away from
            icon("arrow-up-right", t.accent_ink if tinted else t.text).paint(
                painter, QRect(self.width() - 14 - 16 - nudge, 22 + nudge, 16, 16))
            painter.setOpacity(1.0)
        if keyboard_focus(self):
            focus_ring(painter, QRectF(self.rect()), radius)


class HomePage(QWidget):
    open_requested = Signal()
    file_requested = Signal(Path)
    page_requested = Signal(Path, int, str)  # a page found inside a document: file, page from 0, the words
    new_requested = Signal()
    builder_requested = Signal()
    tool_requested = Signal(str)  # a PDF tool of the list, by key
    compare_requested = Signal()
    images_requested = Signal()
    guide_requested = Signal(str)  # chapter, "" for the start
    settings_requested = Signal()
    pin_requested = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setObjectName("Home")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._settings = settings
        self._tag_colours: dict[str, str] = settings.get("tag_colors")
        self._quiet = False  # while the page stores tag colours itself
        self.scope = "all"
        self._pointed: str | None = None  # the file whose row the pointer is on
        self._pressed: str | None = None  # and the one it is pressing
        self._lit: dict[str, motion.Smooth] = {}  # how lit each row that was under the pointer is, by path
        self._shown: dict[str, motion.Smooth] = {}  # how far a page picture that just came in has faded in
        self._grown: dict[str, motion.Smooth] = {}  # how far a star that was just clicked has grown
        self._source_rows: dict[str, int] = {}  # where each listed file stands in the model
        self.loader = InfoLoader(self)
        self.loader.loaded.connect(self._on_loaded)

        # Left column
        self.nav = nav = QFrame()
        nav.setObjectName("HomeSide")
        nav.setFixedWidth(NAV_WIDTH)
        # The rows paint their hover and press fills themselves (NavButton), to fade them: the sheet leaves them.
        nav.setStyleSheet("QPushButton#HomeNav:hover:!checked, QPushButton#HomeNav:pressed:!checked "
                          "{ background: transparent; }")
        self._nav = QVBoxLayout(nav)
        self._nav.setContentsMargins(14, 22, 14, 14)
        self._nav.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(30, 30)
        self.logo.setPixmap(logo_pixmap(30, QApplication.instance().devicePixelRatio()))
        name = QLabel(tr("Zenith PDF"))
        name.setObjectName("HomeBrand")
        brand.addWidget(self.logo)
        brand.addWidget(name)
        brand.addStretch()
        self._nav.addLayout(brand)
        self._nav.addSpacing(16)
        self._nav_buttons: list[NavButton] = []
        # Files, folders, tags and the tools: a column that scrolls when the window is short.
        middle = QWidget()
        self._dynamic = QVBoxLayout(middle)
        self._dynamic.setContentsMargins(0, 0, 0, 0)
        self._dynamic.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidget(middle)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self._nav.addWidget(scroll, 1)
        self._nav.addSpacing(8)
        guide = NavButton("graduation-cap", "Guide", "guide")
        guide.setCheckable(False)
        guide.clicked.connect(lambda: (self.close_nav(), self.guide_requested.emit("")))
        gear = NavButton("gear-six", "Settings", "settings")
        gear.setCheckable(False)
        gear.clicked.connect(lambda: (self.close_nav(), self.settings_requested.emit()))
        self._nav.addWidget(guide)
        self._nav.addWidget(gear)

        # Search, open, new
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self._files_placeholder = "Search your files or tags"
        self.search.setPlaceholderText(tr(self._files_placeholder))
        self._search_icon = self.search.addAction(icon("magnifying-glass"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(BUTTON_HEIGHT)
        self.open_button = QPushButton(tr("  Open"))
        self.open_button.setObjectName("Primary")
        self.open_button.setFixedHeight(BUTTON_HEIGHT)
        self.open_button.clicked.connect(self.open_requested)
        attach_help(self.open_button, "home_open")
        self.new_button = IconButton("plus", "New document", icon_size=20, help="new")
        self.new_button.setObjectName("RoundAccent")
        self.new_button._refresh()  # its icon in the colour of text on the accent
        self.new_button.setFixedSize(BUTTON_HEIGHT, BUTTON_HEIGHT)
        self.new_button.clicked.connect(self.new_requested.emit)
        top = QHBoxLayout()
        top.setSpacing(10)
        # On a narrow page the left column is out of the row: this opens it over the files.
        self.menu_button = FadingButton("list", "Files, folders, tags and tools", icon_size=20)
        self.menu_button.setFixedSize(BUTTON_HEIGHT, BUTTON_HEIGHT)
        self.menu_button.clicked.connect(self.toggle_nav)
        self.menu_button.hide()
        top.addWidget(self.menu_button)
        top.addWidget(self.search, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.new_button)

        self.quick = QWidget()
        quick = QHBoxLayout(self.quick)
        quick.setContentsMargins(0, 0, 0, 0)
        quick.setSpacing(10)
        self.cards: list[QuickCard] = []
        for icon_name, title, detail, signal in (
            ("file-plus", "New document", "Blank, lined, grid or from a template", self.new_requested),
            ("folder-open", "Open a file", "Or drop PDFs on the window", self.open_requested),
            ("squares-four", "Doc Builder", "Join PDFs, pick their pages", self.builder_requested),
            ("image-square", "Images to PDF", "Photos and scans into one PDF", self.images_requested),
        ):
            card = QuickCard(icon_name, title, detail)
            card.clicked.connect(signal.emit)
            quick.addWidget(card)
            self.cards.append(card)
        self.quick.installEventFilter(self)

        self.title = QLabel(tr("All files"))
        self.title.setObjectName("HomeSection")
        self.summary = muted_label()
        # The count stands on the title's baseline: bottoms aligned, then lifted by what the big
        # type hangs below its baseline more than the small one.
        self.title.ensurePolished()
        self.summary.ensurePolished()
        lift = self.title.fontMetrics().descent() - self.summary.fontMetrics().descent()
        self.summary.setContentsMargins(0, 0, 0, max(lift, 0))
        title_row = QHBoxLayout()
        title_row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignBottom)
        title_row.addSpacing(10)
        title_row.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignBottom)
        title_row.addStretch()

        self.model = FileModel(self)
        self.proxy = FileFilter(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self._search)
        self.table = QTableView()
        self.table.setObjectName("FileTable")
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(FileDelegate(self))
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(OPENED, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setStyleSheet("QHeaderView::section:hover { color: palette(window-text); }")  # it is a button
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column, width in COLUMN_WIDTHS.items():
            header.resizeSection(column, width)
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(30)
        self.table.clicked.connect(self._on_clicked)
        self.table.verticalScrollBar().valueChanged.connect(self._repoint)
        self._viewport = self.table.viewport()
        self._viewport.installEventFilter(self)  # its width is what the columns share, its pointer the lit row
        self.empty = EmptyState()
        self.empty.setMinimumHeight(140)
        self.nothing = EmptyState(self.table.viewport())  # a search that finds no file, in the table's own place
        self.nothing.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.nothing.show_state("magnifying-glass", tr("No file matches"))
        self.nothing.hide()

        main = QVBoxLayout()
        main.setContentsMargins(26, 22, 26, 18)
        main.setSpacing(16)
        main.addLayout(top)
        main.addWidget(self.quick)
        main.addSpacing(12)  # the files are a group of their own, and the title belongs to them
        files = QVBoxLayout()
        files.setSpacing(4)
        files.addLayout(title_row)
        files.addWidget(self.empty, 1)  # with no files, the space below the cards is the message's
        files.addWidget(self.table, 1)
        self.inside = InsideResults()
        self.inside.page_requested.connect(self.page_requested)
        files.addWidget(self.inside, 1)
        main.addLayout(files, 1)

        # Right column: the way into the guide
        self.aside = QFrame()
        self.aside.setObjectName("HomeAside")
        self.aside.setFixedWidth(270)
        aside = QVBoxLayout(self.aside)
        aside.setContentsMargins(22, 30, 22, 22)
        aside.setSpacing(10)
        heading = QLabel(tr("Get to know your tools"))
        heading.setObjectName("SectionTitle")
        heading.setWordWrap(True)
        pitch = muted_label("A short guide to everything in Zenith PDF: reading, marking up, pages, tools and "
                            "shortcuts.")
        pitch.setWordWrap(True)
        open_guide = QPushButton(tr("Open the guide"))  # secondary: Open, at the top, is the page's main action
        open_guide.setFixedHeight(BUTTON_HEIGHT)
        open_guide.clicked.connect(lambda: self.guide_requested.emit(""))
        whats_new = QPushButton(tr("What's new"))
        whats_new.setFixedHeight(BUTTON_HEIGHT)
        whats_new.clicked.connect(lambda: self.guide_requested.emit("whats-new"))
        self._tip = random.choice(TIPS)
        self.tip = QLabel()
        self.tip.setObjectName("TipCard")
        self.tip.setWordWrap(True)
        aside.addWidget(heading)
        aside.addWidget(pitch)
        aside.addSpacing(6)
        aside.addWidget(open_guide)
        aside.addWidget(whats_new)
        aside.addSpacing(18)
        aside.addWidget(muted_label("Tip"))
        aside.addWidget(self.tip)
        aside.addStretch()

        self._outer = outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(nav)
        self._narrow = False
        self.scrim = QPushButton(self)  # behind the open column: a tap on the files closes it
        self.scrim.setStyleSheet("QPushButton { background: rgba(0, 0, 0, 110); border: none; }")
        self.scrim.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scrim.clicked.connect(self.close_nav)
        self.scrim.hide()
        outer.addLayout(main, 1)
        outer.addWidget(self.aside)

        self._entries_by_path: dict[str, FileEntry] = {}
        settings.changed.connect(self._on_setting)
        themes.changed.connect(self._refresh_icons)
        self._refresh_icons()
        self.refresh()
        self._prune_thumbnails()

    # Data -----------------------------------------------------------------

    def tag_colour(self, tag: str) -> str:
        return self._tag_colours.get(tag) or TAG_COLOURS[sum(map(ord, tag)) % len(TAG_COLOURS)]

    def _colour_tags(self, names) -> None:
        """Give each tag without a colour the first one no other tag has (stored, so it stays)."""
        colours = self._settings.get("tag_colors")
        missing = [name for name in names if name not in colours]
        if not missing:
            return
        in_use = {colours[name] for name in names if name in colours}
        for name in missing:
            free = [c for c in TAG_COLOURS if c not in in_use]
            colours[name] = free[0] if free else TAG_COLOURS[len(in_use) % len(TAG_COLOURS)]
            in_use.add(colours[name])
        self._quiet = True
        try:
            self._settings.set("tag_colors", colours)
        finally:
            self._quiet = False
        self._tag_colours = colours

    def _files(self) -> list[Path]:
        recent = [p for p in self._settings.recent_files() if p.exists()]
        if self.scope == "starred":
            return [Path(p) for p in self._settings.get("starred") if Path(p).exists()]
        if self.scope.startswith("tag:"):
            tag = self.scope[4:]
            tags = self._settings.get("file_tags")
            return [Path(p) for p, names in tags.items() if tag in names and Path(p).exists()]
        if self.scope.startswith("folder:"):
            folder = Path(self.scope[7:])
            try:
                return sorted((p for p in folder.iterdir() if p.suffix.lower() == ".pdf" and p.is_file()),
                              key=lambda p: p.name.lower())
            except OSError:
                return []
        return recent

    def refresh(self) -> None:
        self._tag_colours = self._settings.get("tag_colors")
        self._colour_tags(sorted({name for names in self._settings.get("file_tags").values() for name in names}))
        self._build_nav()
        self._list()

    def _list(self) -> None:
        """The files of the scope, with their title, their count and what is said when there are none."""
        times = self._settings.get("recent_times")
        infos = self._settings.get("file_info")
        starred = set(self._settings.get("starred"))
        tags = self._settings.get("file_tags")
        entries, missing = [], []
        for path in self._files():
            entry = FileEntry(path=path, opened=times.get(str(path)), starred=str(path) in starred,
                              tags=list(tags.get(str(path), [])))
            info = infos.get(str(path))
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if info and abs(info.get("mtime", 0) - mtime) < 1:
                entry.pages, entry.size = info.get("pages"), info.get("size", 0)
                entry.author = " ".join(str(info.get("author", "")).split())
                entry.title = " ".join(str(info.get("title", "")).split())
                thumb = self.loader.thumbnail_file(path, mtime)
                if thumb.exists():
                    pixmap = QPixmap(str(thumb))
                    entry.thumbnail = pixmap
            else:
                missing.append(path)
            entries.append(entry)
        self._entries_by_path = {str(e.path): e for e in entries}
        self._source_rows = {str(e.path): row for row, e in enumerate(entries)}
        self.model.set_entries(entries)
        for glows in (self._lit, self._shown, self._grown):  # nothing kept for files no longer listed
            for gone in [path for path in glows if path not in self._entries_by_path]:
                glows.pop(gone).deleteLater()
        if self._pointed not in self._entries_by_path:
            self._point(None)  # its row is gone, and the hand with it
        self._repoint()
        self.loader.request(missing)
        titles = {"all": "All files", "starred": "Starred"}
        if self.scope.startswith("folder:"):
            self.title.setText(Path(self.scope[7:]).name or self.scope[7:])
        elif self.scope.startswith("tag:"):
            # A one-word template only takes numbers: the tag's name goes after the translated word.
            self.title.setText(f"{tr('Tagged')} {self.scope[4:]}")
        else:
            self.title.setText(tr(titles.get(self.scope, "All files")))
        count = len(entries)
        self.summary.setText(tr(f"{count} file{'s' if count != 1 else ''}"))
        self.empty.setVisible(not entries)
        self.table.setVisible(bool(entries))
        if not entries:
            self.empty.show_state(*self._nothing_to_show())
        self.tip.setText(native_keys(tr(self._tip)))
        self.search.setPlaceholderText(tr(self._files_placeholder))
        self.inside.show_for(self.search.text())
        self._sync_nothing()

    def _on_loaded(self, path: str, info: dict, image: QImage) -> None:
        if info:
            infos = self._settings.get("file_info")
            stale = set()
            old = infos.pop(path, None)
            if old and old.get("mtime") != info.get("mtime"):
                stale.add(self.loader.thumbnail_file(Path(path), old.get("mtime", 0)).name)
            infos[path] = {k: v for k, v in info.items() if k in ("mtime", "size", "pages", "author", "title")}
            while len(infos) > INFO_LIMIT:
                gone, facts = next(iter(infos.items()))
                infos.pop(gone)
                stale.add(self.loader.thumbnail_file(Path(gone), facts.get("mtime", 0)).name)
            self._settings.set("file_info", infos)
            self.loader.forget(stale)
        entry = self._entries_by_path.get(path)
        if entry is None:
            return
        entry.pages = info.get("pages") or entry.pages
        entry.author = info.get("author", entry.author)
        entry.title = info.get("title", entry.title)
        if not image.isNull():
            entry.thumbnail = QPixmap.fromImage(image)
            if path in self._shown:
                self._shown.pop(path).deleteLater()
            shown = self._shown[path] = motion.Smooth(self.table.viewport())  # the picture fades in over its place
            shown.changed.connect(lambda _, path=path: self._repaint_row(path))
            shown.go(1.0, motion.NORMAL)
        self.model.changed(path)

    def _prune_thumbnails(self) -> None:
        """Thumbnails of files no longer remembered go, so the cache doesn't grow for ever."""
        infos = self._settings.get("file_info")
        keep = {self.loader.thumbnail_file(Path(p), facts.get("mtime", 0)).name for p, facts in infos.items()}
        threading.Thread(target=self.loader.prune, args=(keep,), name="thumbnail-prune", daemon=True).start()

    def _on_setting(self, key: str) -> None:
        if self._quiet:
            return
        if key == "tag_colors":
            self._tag_colours = self._settings.get("tag_colors")
        if key in ("recent", "starred", "file_tags", "pinned_folders", "tag_colors") and self.isVisible():
            self.refresh()

    def _search(self, text: str) -> None:
        self.proxy.set_text(text)
        self._repoint()  # the rows moved under a pointer that rests on the list
        self.inside.show_for(text)
        self._sync_nothing()

    def _nothing_to_show(self) -> tuple[str, str, str]:
        """The picture, the title and the words of a list with no files, by what the list is."""
        if self.scope == "starred":
            return "star", tr("Nothing here yet."), tr("Click the star beside a file to keep it here.")
        if self.scope.startswith("tag:"):
            return "tag", tr("Nothing here yet."), tr("Tag a file from its row, and it is listed here.")
        if self.scope.startswith("folder:"):
            return "folder-simple", tr("Nothing here yet."), tr("This folder has no PDF files.")
        return "files", tr("No files yet"), tr("Files you open will appear here. Open one, or drop PDFs on the window.")

    def _sync_nothing(self) -> None:
        """Say so when files are listed but the search matches none of them."""
        self.nothing.glyph.setVisible(not self.inside.isVisibleTo(self))  # those results are the answer: no picture
        self.nothing.setGeometry(self.table.viewport().rect())
        self.nothing.setVisible(bool(self.model.entries) and not self.proxy.rowCount())

    def set_indexer(self, indexer) -> None:
        """Search inside the documents too, with the app's index."""
        self.inside.indexer = indexer
        self._files_placeholder = "Search your files, their tags, or the words inside them"
        self.search.setPlaceholderText(tr(self._files_placeholder))
        indexer.indexed.connect(self._on_indexed)

    def _on_indexed(self, _path: str) -> None:
        if self.inside.query and self.isVisible():
            self.inside.show_for(self.search.text())  # a file just read may hold the words

    # Under the pointer --------------------------------------------------------

    def row_light(self, path: Path) -> float:
        """How lit the row of `path` is, 0 to 1: it comes up as the pointer reaches it and goes down as it leaves."""
        glow = self._lit.get(str(path))
        return glow.value if glow is not None else 0.0

    def row_pressed(self, path: Path) -> bool:
        """Whether the row of `path` is under a pressed mouse button."""
        return self._pressed == str(path)

    def star_size(self, path: Path) -> float:
        """How big the star of `path` is, as a share of its size: a star just clicked grows into its new look."""
        grown = self._grown.get(str(path))
        return grown.value if grown is not None else 1.0

    def thumb_shown(self, path: Path) -> float:
        """How far the picture of `path`'s first page has come in over its placeholder, 0 to 1."""
        shown = self._shown.get(str(path))
        return shown.value if shown is not None else 1.0

    def _repaint_rows(self, *_) -> None:
        self.table.viewport().update()

    def _repaint_row(self, path: str) -> None:
        """Paint again only the row of `path`, as its light, its star or its picture changes: a list of many rows
        is not painted whole on every step."""
        source = self._source_rows.get(path)
        row = self.proxy.mapFromSource(self.model.index(source, 0)).row() if source is not None else -1
        if row < 0:
            self._repaint_rows()
            return
        viewport = self.table.viewport()
        viewport.update(0, self.table.rowViewportPosition(row), viewport.width(), ROW_HEIGHT)

    def _point(self, at: QPoint | None) -> None:
        """The row at `at` lights up and the one before it fades; a row is a thing to click, so it shows a hand."""
        row = self.table.rowAt(at.y()) if at is not None else -1
        path = str(self._entry(self.proxy.index(row, NAME)).path) if row >= 0 else None
        if path == self._pointed:
            return
        before, self._pointed = self._pointed, path
        if bool(before) != bool(path):
            self.table.viewport().setCursor(Qt.CursorShape.PointingHandCursor if path else Qt.CursorShape.ArrowCursor)
        if before in self._lit:
            self._lit[before].go(0.0, motion.NORMAL)  # it lingers a little as the pointer goes
        if path is not None:
            if path not in self._lit:
                self._lit[path] = motion.Smooth(self.table.viewport())
                self._lit[path].changed.connect(lambda _, path=path: self._repaint_row(path))
            self._lit[path].go(1.0, motion.FAST)

    def _repoint(self, *_) -> None:
        """Find the row under the pointer again, for when the rows moved under it: a scroll, a new list."""
        viewport = self.table.viewport()
        self._point(viewport.mapFromGlobal(QCursor.pos()) if viewport.underMouse() else None)

    # Left column ------------------------------------------------------------

    def _build_nav(self) -> None:
        while self._dynamic.count():
            item = self._dynamic.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # deleteLater alone leaves it on screen until the event loop runs
                widget.setParent(None)
                widget.deleteLater()
        self._nav_buttons = []
        recent = [p for p in self._settings.recent_files() if p.exists()]
        starred = [p for p in self._settings.get("starred") if Path(p).exists()]
        self._nav_add(NavButton("files", "All files", "all"), str(len(recent)))
        self._nav_add(NavButton("star", "Starred", "starred"), str(len(starred)))
        self._dynamic.addSpacing(14)
        self._dynamic.addWidget(self._heading("Folders", "push-pin", "Pin a folder", self.pin_requested.emit))
        for folder in self._settings.pinned_folders:
            button = NavButton("folder-simple", Path(folder).name or folder, f"folder:{folder}")
            button.setToolTip(folder)
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, f=folder: self._folder_menu(f))
            self._nav_add(button, "")
        if not self._settings.pinned_folders:
            self._dynamic.addWidget(self._hint("Pin folders you use often."))
        self._dynamic.addSpacing(14)
        tags = self._settings.get("file_tags")
        counts: dict[str, int] = {}
        for names in tags.values():
            for name in names:
                counts[name] = counts.get(name, 0) + 1
        self._dynamic.addWidget(self._heading("Tags"))
        for name in sorted(counts):
            self._nav_add(NavButton("tag", name, f"tag:{name}", colour=self.tag_colour(name)), str(counts[name]))
        if not counts:
            self._dynamic.addWidget(self._hint("Tag files from the list."))
        self._dynamic.addSpacing(14)
        self._dynamic.addWidget(self._heading("Tools"))
        from .pdf_tools import TOOLS  # here: pdf_tools imports the window, which imports this page

        rows = [("compare", "git-diff", "Compare two versions", "What was added, removed or changed")]
        rows += [(tool.key, tool.icon, tool.title, tool.description) for tool in TOOLS]
        for key, icon_name, title, detail in rows:
            button = NavButton(icon_name, title, f"tool:{key}")
            button.setCheckable(False)
            button.setMinimumHeight(32)
            button.setToolTip(tr(detail))
            button.clicked.connect(lambda _=False, key=key: self._tool(key))
            self._dynamic.addWidget(button)
        self._dynamic.addStretch()

    def _tool(self, key: str) -> None:
        self.close_nav()
        if key == "compare":
            self.compare_requested.emit()
        else:
            self.tool_requested.emit(key)

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = muted_label(text)
        label.setWordWrap(True)
        label.setContentsMargins(10, 2, 4, 2)  # in line with the headings
        return label

    def _heading(self, text: str, icon_name: str | None = None, tip: str = "", slot=None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 4, 2)
        label = QLabel(tr(text))
        label.setObjectName("HomeNavHeading")  # in capitals by the style sheet, where the style wants them
        layout.addWidget(label)
        layout.addStretch()
        if icon_name:
            button = FadingButton(icon_name, tip, icon_size=14)
            button.clicked.connect(slot)
            layout.addWidget(button)
        return row

    def _nav_add(self, button: NavButton, count: str) -> None:
        button.count = count
        button.setChecked(button.key == self.scope)
        button.clicked.connect(lambda _=False, k=button.key: self.show_scope(k))
        self._dynamic.addWidget(button)
        self._nav_buttons.append(button)

    def show_scope(self, key: str) -> None:
        self.close_nav()
        changed = key != self.scope
        self.scope = key
        # The rows stay as they are, so the keyboard keeps its place; a click on the chosen one had unchecked it.
        for button in self._nav_buttons:
            button.setChecked(button.key == key)
        self._list()
        if changed:  # a new list arrives rather than swaps
            motion.appear(self.table if self.table.isVisible() else self.empty, motion.RISE // 2, motion.NORMAL)

    def _folder_menu(self, folder: str) -> None:
        menu = QMenu(self)
        motion.appear_on_show(menu, -motion.RISE)  # it drops out of the pointer
        menu.addAction(icon("folder-open"), tr("Show in Explorer"),
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        menu.addAction(icon("x"), tr("Unpin"), lambda: self._unpin(folder))
        menu.exec(self.cursor().pos())

    def _unpin(self, folder: str) -> None:
        self._settings.pinned_folders = [f for f in self._settings.pinned_folders if f != folder]
        if self.scope == f"folder:{folder}":
            self.scope = "all"
        self.refresh()

    # The table ---------------------------------------------------------------

    def _entry(self, index: QModelIndex) -> FileEntry:
        return self.proxy.data(index, FILE_ROLE)

    def _on_clicked(self, index: QModelIndex) -> None:
        entry = self._entry(index)
        if index.column() == STAR:
            self.toggle_star(entry.path)
        elif index.column() == TAGS:
            self.add_tag(entry.path)
        elif index.column() == MORE:
            self._file_menu(entry)
        else:
            self.file_requested.emit(entry.path)

    def toggle_star(self, path: Path) -> None:
        starred = self._settings.get("starred")
        if str(path) in starred:
            starred.remove(str(path))
        else:
            starred.append(str(path))
        self._settings.set("starred", starred)
        if str(path) in self._grown:
            self._grown.pop(str(path)).deleteLater()
        grown = self._grown[str(path)] = motion.Smooth(self.table.viewport(), 0.5)
        grown.changed.connect(lambda _, path=str(path): self._repaint_row(path))
        grown.go(1.0, motion.NORMAL)

    def add_tag(self, path: Path) -> None:
        tags = self._settings.get("file_tags")
        known = sorted({name for names in tags.values() for name in names})
        current = tags.get(str(path), [])
        name = messages.get_item(self, "Add tag", f"Pick a tag for {path.name}, or type a new one.",
                                 [n for n in known if n not in current], 0, editable=True, action="Add", required=True)
        name = (name or "").strip()
        if name and name not in current:
            self._colour_tags([*known, name])
            tags[str(path)] = current + [name]
            self._settings.set("file_tags", tags)

    def remove_tag(self, path: Path, name: str) -> None:
        tags = self._settings.get("file_tags")
        remaining = [n for n in tags.get(str(path), []) if n != name]
        if remaining:
            tags[str(path)] = remaining
        else:
            tags.pop(str(path), None)
        self._settings.set("file_tags", tags)

    def _file_menu(self, entry: FileEntry) -> None:
        menu = QMenu(self)
        motion.appear_on_show(menu, -motion.RISE)
        menu.addAction(icon("file-pdf"), tr("Open"), lambda: self.file_requested.emit(entry.path))
        menu.addAction(icon("folder-open"), tr("Show in Explorer"),
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.path.parent))))
        menu.addAction(icon("copy"), tr("Copy the path"), lambda: QApplication.clipboard().setText(str(entry.path)))
        menu.addSeparator()
        menu.addAction(icon("star"), tr("Remove the star" if entry.starred else "Star"), lambda: self.toggle_star(entry.path))
        menu.addAction(icon("tag"), tr("Add tag…"), lambda: self.add_tag(entry.path))
        if entry.tags:
            remove = menu.addMenu(icon("tag-simple"), tr("Remove tag"))
            motion.appear_on_show(remove, -motion.RISE, motion.FAST)  # pointing at its entry opens it: it comes faster
            for name in entry.tags:
                remove.addAction(tr(name), lambda n=name: self.remove_tag(entry.path, n))
        menu.addSeparator()
        menu.addAction(icon("x"), tr("Remove from the list"), lambda: self._settings.remove_recent(entry.path))
        menu.exec(self.cursor().pos())

    # Fitting the window ---------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if watched is self._viewport:
            if not shiboken6.isValid(self.table):  # the page is going: events still come through
                return False
            if kind == QEvent.Type.Resize:
                self._fit_columns()
                self.nothing.setGeometry(self._viewport.rect())
            elif kind == QEvent.Type.MouseMove:
                self._point(event.position().toPoint())
            elif kind == QEvent.Type.MouseButtonPress:
                self._point(event.position().toPoint())
                # Only the left button clicks a row, so only it presses one.
                self._pressed = self._pointed if event.button() == Qt.MouseButton.LeftButton else None
                self._repaint_rows()
            elif kind == QEvent.Type.MouseButtonRelease:
                self._pressed = None
                self._repaint_rows()
            elif kind == QEvent.Type.Leave:
                self._point(None)
        elif kind == QEvent.Type.Resize and watched is self.quick:
            self._fit_cards()
        return super().eventFilter(watched, event)

    def _fit_columns(self) -> None:
        """Show the optional columns, most useful first, while the name keeps NAME_WIDTH.

        Short of room, the shown columns give up the same share of their extra width,
        down to LEAST_WIDTHS; a column that doesn't fit even then is hidden, with the
        ones after it. Only the room counts, never the window's width as such.
        """
        room = self.table.viewport().width() - COLUMN_WIDTHS[STAR] - COLUMN_WIDTHS[MORE] - NAME_WIDTH
        shown: list[int] = []
        for column in OPTIONAL_COLUMNS:
            if sum(LEAST_WIDTHS[c] for c in shown) + LEAST_WIDTHS[column] > room:
                break
            shown.append(column)
        least = sum(LEAST_WIDTHS[c] for c in shown)
        extra = sum(COLUMN_WIDTHS[c] - LEAST_WIDTHS[c] for c in shown)
        share = 1.0 if extra <= 0 else min(1.0, max(0.0, room - least) / extra)
        header = self.table.horizontalHeader()
        for column in OPTIONAL_COLUMNS:
            show = column in shown
            if self.table.isColumnHidden(column) == show:
                self.table.setColumnHidden(column, not show)
            if show:
                width = int(LEAST_WIDTHS[column] + (COLUMN_WIDTHS[column] - LEAST_WIDTHS[column]) * share)
                if header.sectionSize(column) != width:
                    header.resizeSection(column, width)

    def _fit_cards(self) -> None:
        spacing = self.quick.layout().spacing()
        each = (self.quick.width() - spacing * (len(self.cards) - 1)) / len(self.cards)
        if each >= FULL_CARD:
            mode = "full"
        elif each >= max(card.needs() for card in self.cards):
            mode = "compact"
        else:
            mode = "icon"
        for card in self.cards:
            card.set_mode(mode)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tip = random.choice(TIPS)
        self.tip.setText(native_keys(tr(self._tip)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.aside.setVisible(self.width() >= ASIDE_FROM)
        self._set_narrow(self.width() < NAV_FROM)
        if self.nav.isVisible() and self._narrow:
            self._place_nav()
        self._fit_columns()

    # A narrow page: the left column over the files -------------------------------------

    def _set_narrow(self, narrow: bool) -> None:
        if narrow == self._narrow:
            return
        self._narrow = narrow
        self.menu_button.setVisible(narrow)
        self.scrim.hide()
        if narrow:
            self._outer.removeWidget(self.nav)
            self.nav.hide()
        else:
            self._outer.insertWidget(0, self.nav)
            self.nav.show()

    def _place_nav(self) -> None:
        self.scrim.setGeometry(self.rect())
        self.nav.setGeometry(0, 0, NAV_WIDTH, self.height())

    def toggle_nav(self) -> None:
        if self.nav.isVisible():
            self.close_nav()
            return
        self._place_nav()
        self.scrim.show()
        self.scrim.raise_()
        self.nav.show()
        self.nav.raise_()
        motion.appear(self.scrim, 0, motion.NORMAL)
        motion.slide_in(self.nav, Qt.Edge.LeftEdge)

    def close_nav(self) -> None:
        """Close the left column when it is open over the files; in a wide page it stays."""
        if self._narrow:
            self.nav.hide()
            self.scrim.hide()

    def _refresh_icons(self, *_):
        self.open_button.setIcon(icon("folder-open", themes.current.accent_text))
        self._search_icon.setIcon(icon("magnifying-glass"))
        self.update()
