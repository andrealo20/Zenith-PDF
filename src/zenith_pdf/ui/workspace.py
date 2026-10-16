"""Everything shown inside one document tab.

The canvas is framed like a drawing app: tools across the top, the page counter
top right, zoom and view options bottom right, undo and redo bottom left.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QElapsedTimer, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QWidget,
)

from ..core.annotations import Redaction, TextMarkup, union
from ..core.document import Document
from ..core.render import RenderPool
from ..core.store import AnnotationStore
from ..settings import Settings
from ..theme import themes
from . import messages
from .annotations_panel import AnnotationsPanel
from .commands import ChangePages
from .dialogs import ApplyToPagesDialog, PropertiesDialog
from .library import Library, LibraryPanel
from .selection_bar import SelectionBar
from .document_view import LAYOUTS, TINTS, DocumentView
from .help import attach_help
from .link_preview import LinkPreview
from .outline import OutlinePanel
from .page_actions import PageActions
from .page_manager import PageManager
from .search import SearchPanel
from .side_panel import SidePanel
from .text_bar import TextSelectionBar
from .thumbnails import ThumbnailPanel
from .tips import ToolTips
from .toolbar import AnnotationToolbar
from .tools import ToolSettings
from .widgets import GLASS_BLEED, Fade, FloatingBar, IconButton, Switch, icon_pixmap, muted_label, separator

ZOOM_PRESETS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
GAP = 14  # pixels between the floating bars and the edges
TOAST_MS = 3500
CARD_OFFSET = QPoint(14, 22)  # a card by the pointer starts this far right of it and below it, clear of the cursor
TEXT_BAR_GAP = 10  # pixels between selected text and the bar over it
SCROLLBAR = 14  # pixels the vertical scrollbar takes at the right of the canvas


class PageField(QLineEdit):
    """The page box. Esc, or leaving it, puts back the page shown: a number not taken does not stay."""

    cancelled = Signal()  # Esc
    left = Signal()  # the focus went elsewhere

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.cancelled.emit()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not self.hasAcceptableInput():
            event.accept()  # no such page (or nothing typed): the number shown again, selected to type over
            self.left.emit()
            self.selectAll()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:  # its own context menu: still typing
            self.left.emit()


class PageCounter(FloatingBar):
    """Top right: the page you're on, Back after a jump, the arrows to the pages either side, and the page manager."""

    def __init__(self, view: DocumentView, open_manager, parent=None):
        super().__init__(parent)
        self._view = view
        self.field = PageField()
        self.field.setObjectName("PageField")
        self.field.setFixedWidth(46)
        self.field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.field.setToolTip("Go to page (Ctrl+G)")
        self.field.returnPressed.connect(self._on_entered)
        self.field.cancelled.connect(self._view.setFocus)  # and leaving puts the number back
        self.field.left.connect(lambda: self.field.setText(str(self._view.current_page + 1)))
        self.total = QLabel()  # full contrast: muted text gets lost on glass over a white page
        # A chip shown after a jump (a link, a bookmark, Ctrl+G, Home or End), with the page Back returns to.
        self.back_button = IconButton("arrow-bend-up-left", "Back", icon_size=16, text="1", help="nav_back")
        self.back_button.setObjectName("BackChip")
        self.back_button.clicked.connect(view.back)
        previous = IconButton("caret-up", "Previous page", icon_size=18, help="previous_page")
        previous.clicked.connect(view.previous_page)
        following = IconButton("caret-down", "Next page", icon_size=18, help="next_page")
        following.clicked.connect(view.next_page)
        grid = IconButton("squares-four", "Page manager: reorder, rotate, delete pages", icon_size=18,
                          help="page_manager")
        grid.clicked.connect(open_manager)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        layout.setSpacing(4)
        for widget in (self.field, self.total, self.back_button, previous, following, separator(), grid):
            layout.addWidget(widget)
        self.refresh()

    def refresh(self) -> None:
        count = self._view.document.page_count
        self.field.setValidator(QIntValidator(1, count, self))
        self.total.setText(f" of {count} ")
        self.set_page(self._view.current_page)
        spot = self._view.history.peek_back()
        self.back_button.setVisible(spot is not None)
        if spot is not None:
            self.back_button.setText(str(spot.page + 1))
            self.back_button.setToolTip(f"Back to page {spot.page + 1} (Alt+Left)")
            self.back_button.setProperty("help_detail", f"Back to page {spot.page + 1}")
        self.adjustSize()

    def set_page(self, index: int) -> None:
        if not self.field.hasFocus():
            self.field.setText(str(index + 1))

    def focus(self) -> None:
        self.field.setFocus()
        self.field.selectAll()

    def _on_entered(self) -> None:
        if self.field.text():
            self._view.jump(int(self.field.text()) - 1)
        self._view.setFocus()


class ZoomBar(FloatingBar):
    """Bottom right: page layout, page colour, zoom, and reading mode."""

    reading_requested = Signal()

    def __init__(self, view: DocumentView, parent=None):
        super().__init__(parent)
        self._view = view
        layout_button = self._menu_button("book-open", "Page layout", LAYOUTS, view.set_layout, lambda: view.layout_mode,
                                          "layout")
        tint_button = self._menu_button("circle-half", "Page colours", TINTS, view.set_page_tint, lambda: view.page_tint,
                                        "tint")
        zoom_out = IconButton("minus", "Zoom out (Ctrl+-)", icon_size=18, help="zoom_out")
        zoom_out.clicked.connect(view.zoom_out)
        zoom_in = IconButton("plus", "Zoom in (Ctrl+=)", icon_size=18, help="zoom_in")
        zoom_in.clicked.connect(view.zoom_in)
        self.zoom_button = QToolButton()
        self.zoom_button.setObjectName("ZoomButton")
        self.zoom_button.setFixedWidth(58)
        self.zoom_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.zoom_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu = QMenu(self.zoom_button)
        for zoom in ZOOM_PRESETS:
            menu.addAction(f"{round(zoom * 100)}%", lambda z=zoom: view.set_zoom(z))
        menu.addSeparator()
        menu.addAction("Fit width", view.fit_width)
        menu.addAction("Fit page", view.fit_page)
        self.zoom_button.setMenu(menu)
        attach_help(self.zoom_button, "zoom_menu")
        fit_width = IconButton("arrows-out-line-horizontal", "Fit width (Ctrl+2)", icon_size=18, help="fit_width")
        fit_width.clicked.connect(view.fit_width)
        fit_page = IconButton("frame-corners", "Fit page (Ctrl+0)", icon_size=18, help="fit_page")
        self.snap_button = IconButton("magnet", "Snapping", icon_size=18, checkable=True, help="snapping")
        self.snap_button.clicked.connect(self._show_snapping)
        if view.settings is not None:
            view.settings.changed.connect(self._sync_snapping)
        self._sync_snapping()
        fit_page.clicked.connect(view.fit_page)
        self.reading_button = IconButton("z-reading", "Reading mode: only the pages (Ctrl+H)", icon_size=18,
                                         help="reading_mode")
        self.reading_button.clicked.connect(self.reading_requested)
        # In a narrow window the view options fold into one menu, then Fit width and Fit page too (see fit).
        self.more_button = IconButton("dots-three-vertical", "Page layout, page colours, snapping", icon_size=18,
                                      help="view_options")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.more_button)
        more.aboutToShow.connect(lambda: self._fill_more(more))
        self.more_button.setMenu(more)
        self.more_button.hide()
        self._folded = 0
        view_separator, fit_separator = separator(), separator()
        self._folds = [  # what goes first, then next
            [layout_button, tint_button, self.snap_button, view_separator],
            [fit_width, fit_page, fit_separator],
        ]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        for widget in (self.more_button, layout_button, tint_button, self.snap_button, view_separator, zoom_out,
                       self.zoom_button, zoom_in, fit_separator, fit_width, fit_page, separator(),
                       self.reading_button):
            layout.addWidget(widget)

    def fit(self, max_width: int) -> None:
        """Fold the view options, then Fit width and Fit page, into the More button's menu until the bar is at
        most `max_width` pixels wide (or nothing more can fold)."""
        folded = 0
        while True:
            for number, group in enumerate(self._folds):
                for widget in group:
                    widget.setVisible(number >= folded)
            self.more_button.setVisible(folded > 0)
            self.layout().invalidate()
            if self.sizeHint().width() <= max_width or folded == len(self._folds):
                break
            folded += 1
        self._folded = folded
        self.adjustSize()

    def _fill_more(self, menu: QMenu) -> None:
        """The More button's menu: what fit() folded away, as menu entries."""
        menu.clear()
        view = self._view
        for title, choices, apply, current in (("Page layout", LAYOUTS, view.set_layout, view.layout_mode),
                                               ("Page colours", TINTS, view.set_page_tint, view.page_tint)):
            sub = menu.addMenu(title)
            for key, label in choices.items():
                action = sub.addAction(label, lambda k=key, a=apply: a(k))
                action.setCheckable(True)
                action.setChecked(key == current)
        if view.settings is not None:
            menu.addSeparator()
            for key, text in (("snap_annotations", "Snap to annotations"), ("snap_content", "Snap to PDF content")):
                action = menu.addAction(text, lambda k=key: view.settings.set(k, not view.settings.get(k)))
                action.setCheckable(True)
                action.setChecked(bool(view.settings.get(key)))
        if self._folded > 1:
            menu.addSeparator()
            menu.addAction("Fit width\tCtrl+2", view.fit_width)
            menu.addAction("Fit page\tCtrl+0", view.fit_page)

    def _menu_button(self, icon_name: str, tip: str, choices: dict, apply, current, help: str) -> IconButton:
        button = IconButton(icon_name, tip, icon_size=18, help=help)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for key, label in choices.items():
            action = menu.addAction(label, lambda k=key: apply(k))
            action.setCheckable(True)
            action.setData(key)
        menu.aboutToShow.connect(lambda: [a.setChecked(a.data() == current()) for a in menu.actions()])
        button.setMenu(menu)
        return button

    def set_zoom(self, zoom: float) -> None:
        self.zoom_button.setText(f"{round(zoom * 100)}%")

    def _sync_snapping(self, key: str = "") -> None:
        if key in ("", "snap_annotations", "snap_content"):
            on = bool(self._view._pref("snap_annotations") or self._view._pref("snap_content"))
            self.snap_button.setChecked(on)

    def _show_snapping(self) -> None:
        self._sync_snapping()
        if self._view.settings is None:
            return
        popup = SnappingPopup(self._view.settings, self)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        corner = self.snap_button.mapToGlobal(QPoint(self.snap_button.width() // 2, 0))
        popup.adjustSize()
        popup.move(corner.x() - popup.width() // 2, corner.y() - popup.height() - 10)
        popup.show()


class SnappingPopup(FloatingBar):
    """Two switches: snap to other annotations, snap to the page's own content."""

    def __init__(self, settings, parent=None):
        super().__init__(parent, popup=True)
        self._settings = settings
        title = QLabel("Annotation snapping")
        title.setObjectName("SectionTitle")
        gear = IconButton("gear-six", "Snapping settings", icon_size=16)
        gear.clicked.connect(self._open_settings)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(gear)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        layout.addLayout(header)
        for key, icon_name, text in (("snap_annotations", "squares-four", "Snap to annotations"),
                                     ("snap_content", "article", "Snap to PDF content")):
            row = QHBoxLayout()
            glyph = QLabel()
            glyph.setPixmap(icon_pixmap(icon_name, 20))
            switch = Switch(bool(settings.get(key)))
            switch.toggled.connect(lambda on, k=key: settings.set(k, on))
            row.addWidget(glyph)
            row.addSpacing(8)
            row.addWidget(QLabel(text), 1)
            row.addSpacing(24)
            row.addWidget(switch)
            layout.addLayout(row)
        layout.addWidget(muted_label("Hold Alt to place freely for a moment."))

    def _open_settings(self) -> None:
        self.close()
        window = self.parent().window() if self.parent() is not None else None
        if hasattr(window, "open_settings"):
            window.open_settings("annotation")


class UndoBar(FloatingBar):
    """Bottom left: undo and redo, with what they would undo in the tooltip."""

    def __init__(self, undo_stack: QUndoStack, end_interaction=None, parent=None):
        super().__init__(parent)
        # Typing and drags end first, as with Ctrl+Z: an undo must never land in the middle of one.
        finish = end_interaction or (lambda: None)
        undo = IconButton("arrow-u-up-left", "Undo (Ctrl+Z)", icon_size=18, help="undo")
        undo.clicked.connect(lambda: (finish(), undo_stack.undo()))
        redo = IconButton("arrow-u-up-right", "Redo (Ctrl+Y)", icon_size=18, help="redo")
        redo.clicked.connect(lambda: (finish(), undo_stack.redo()))
        for button, can, text, verb in (
            (undo, undo_stack.canUndoChanged, undo_stack.undoTextChanged, "Undo"),
            (redo, undo_stack.canRedoChanged, undo_stack.redoTextChanged, "Redo"),
        ):
            button.setEnabled(False)
            can.connect(button.setEnabled)
            text.connect(lambda t, b=button, v=verb: self._describe(b, v, t))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(undo)
        layout.addWidget(redo)

    @staticmethod
    def _describe(button: IconButton, verb: str, text: str) -> None:
        """What the button would do now, in its tooltip and its help card: "Undo add rectangle"."""
        what = f"{verb} {text.lower()}" if text else ""
        button.setToolTip(what or verb)
        button.setProperty("help_detail", what)


class Toast(FloatingBar):
    """A short message floating over the bottom of the canvas, on the same glass as the bars. It fades in, and
    fades away by itself."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label = QLabel()
        self.label.setObjectName("ToastText")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 9, 18, 9)
        layout.addWidget(self.label)
        self.hide()
        self.fade = Fade(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.fade.fade_out)

    def text(self) -> str:
        return self.label.text()

    def show_message(self, text: str) -> None:
        self.label.setText(text)
        self.adjustSize()
        self.fade.fade_in()
        self.raise_()
        self._timer.start(TOAST_MS)


class _Canvas(QWidget):
    """The page view (or the page manager), with glass bars floating over the pages.

    The glass follows the pages without lag: a scroll moves each bar's picture of
    its backdrop at once, and a fresh sample follows at most once a frame
    (throttled, never put off while the scroll goes on). A zoom resamples at once;
    a change in the scene resamples only the bars it reaches.
    """

    GLASS_REFRESH_MS = 12  # least time between samples: under a frame, so a late timer still lands in it
    MANY_REGIONS = 32  # a scene change in more pieces than this counts as a change everywhere
    # The bars were placed again (a new size, a wider counter): bars over a selection go back over it, as the
    # pages may have moved in the view without a scroll.
    relaid = Signal()

    def __init__(self, view: DocumentView, toolbar: FloatingBar, bars: dict, selection: FloatingBar, toast: Toast,
                 parent=None):
        super().__init__(parent)
        self.view = view
        self.toolbar = toolbar
        self.bars = bars
        self.selection = selection
        self.toast = toast
        self.manager: QWidget | None = None
        self._floating: list[QWidget] = []  # cards added with add_floating
        self._band = 0
        self.reading = False  # reading mode: the bars are away, and nothing floats up over the pages
        self.exit_chip: FloatingBar | None = None  # reading mode's way back, at the top
        self._fades: dict[QWidget, Fade] = {}
        for child in (view, toolbar, selection, toast, *bars.values()):
            child.setParent(self)
        for bar in self._glass():
            bar.set_backdrop(view.viewport())
        selection.hide()
        self._stale: set[FloatingBar] = set()
        self._since = QElapsedTimer()  # since the last sample
        self._since.start()
        self._glass_timer = QTimer(self)
        self._glass_timer.setSingleShot(True)
        self._glass_timer.setTimerType(Qt.TimerType.PreciseTimer)  # a coarse timer stretches a frame to 20 ms
        self._glass_timer.timeout.connect(self._refresh_glass)
        self._scroll = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        view.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        view.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        view.zoom_changed.connect(self._on_zoomed)
        view.scene().changed.connect(self._on_scene_changed)
        themes.changed.connect(self._on_theme)

    def _glass(self) -> list[FloatingBar]:
        self._floating = [w for w in self._floating if shiboken6.isValid(w)]
        floating = [w for w in self._floating if isinstance(w, FloatingBar)]
        chip = [self.exit_chip] if self.exit_chip is not None and shiboken6.isValid(self.exit_chip) else []
        return [self.toolbar, self.selection, *self.bars.values(), self.toast, *floating, *chip]

    def set_exit_chip(self, chip: FloatingBar) -> None:
        """The chip that leaves reading mode: on the glass, over the pages, placed at the top."""
        chip.setParent(self)
        chip.set_backdrop(self.view.viewport())
        self.exit_chip = chip
        self.place_exit_chip()

    def place_exit_chip(self) -> None:
        chip = self.exit_chip
        if chip is not None and shiboken6.isValid(chip):
            hint = chip.sizeHint()
            chip.setGeometry((self.width() - hint.width()) // 2, GAP, hint.width(), hint.height())

    def set_reading(self, on: bool) -> None:
        """Reading mode: the toolbar and the bars fade away, and nothing floats up over the pages until it ends.
        A pinned tool menu goes with the toolbar and comes back with it."""
        if on == self.reading:
            return
        self.reading = on
        chrome = [self.toolbar, *self.bars.values()]
        if on:
            self.toolbar.put_menu_away()
            self.selection.hide()
            for card in self._floating:
                if shiboken6.isValid(card):
                    card.hide()
            for bar in chrome:
                self._fade(bar).fade_out()  # they stay where they are while they fade
        else:
            self.resizeEvent(None)  # in place for the canvas as it is now
            for bar in chrome:
                self._fade(bar).fade_in()

    def _fade(self, bar: QWidget) -> Fade:
        fade = self._fades.get(bar)
        if fade is None:
            fade = self._fades[bar] = Fade(bar)
        return fade

    def add_floating(self, card: QWidget) -> None:
        """Float a card or bar over the pages (a link preview, a bar for selected text).

        A FloatingBar gets the glass. Cards stay above the pages and hide while the page manager is open.
        """
        card.setParent(self)
        if card not in self._floating:
            self._floating.append(card)
        if isinstance(card, FloatingBar):
            card.set_backdrop(self.view.viewport())
        card.raise_()

    def schedule_glass(self, bars=None) -> None:
        """Resample these bars (all by default): now if a frame has passed since the last sample, else then."""
        self._stale.update(self._glass() if bars is None else bars)
        wait = self.GLASS_REFRESH_MS - self._since.elapsed()
        if wait <= 0:
            self._glass_timer.stop()
            self._refresh_glass()
        elif not self._glass_timer.isActive():
            self._glass_timer.start(wait)

    def _refresh_glass(self) -> None:
        self._since.restart()
        bars, self._stale = self._stale, set()
        for bar in bars:
            if shiboken6.isValid(bar) and bar.isVisible():
                bar.refresh_backdrop()

    def _on_scrolled(self, _value=None) -> None:
        x, y = self.view.horizontalScrollBar().value(), self.view.verticalScrollBar().value()
        dx, dy = x - self._scroll[0], y - self._scroll[1]
        self._scroll = (x, y)
        if dx or dy:
            bars = self._glass()
            for bar in bars:
                bar.shift_backdrop(-dx, -dy)  # the pages moved the other way
            # A jump past the sampled picture (a page turn, a fast flick) would show a flat colour for a frame:
            # those bars sample again now, the others when a frame has passed.
            far = [bar for bar in bars if bar.isVisible() and not bar.covers()]
            for bar in far:
                bar.refresh_backdrop()
            if far:
                self._stale.difference_update(far)
                self._since.restart()  # a sample this frame: tiles landing under the bars wait for the next one
            self.schedule_glass([bar for bar in bars if bar not in far])

    def _on_zoomed(self, _zoom=None) -> None:
        self._stale.update(self._glass())
        self._glass_timer.stop()
        self._refresh_glass()

    def _on_scene_changed(self, regions) -> None:
        bars = [bar for bar in self._glass() if bar.isVisible()]
        if not bars:
            return
        if not regions or len(regions) > self.MANY_REGIONS:
            self.schedule_glass(bars)
            return
        viewport = self.view.viewport()
        changed = [self.view.mapFromScene(region).boundingRect() for region in regions]
        reached = []
        for bar in bars:
            area = QRect(viewport.mapFrom(self, bar.pos()), bar.size()).adjusted(
                -GLASS_BLEED, -GLASS_BLEED, GLASS_BLEED, GLASS_BLEED)
            if any(area.intersects(rect) for rect in changed):
                reached.append(bar)
        if reached:
            self.schedule_glass(reached)

    def _on_theme(self, *_) -> None:
        self.update()
        self.schedule_glass()

    def paintEvent(self, event) -> None:
        QPainter(self).fillRect(self.rect(), QColor(themes.current.canvas))

    def show_manager(self, manager: QWidget | None) -> None:
        self.manager = manager
        if manager is not None:
            manager.setParent(self)
            manager.show()
        self.view.setVisible(manager is None)
        self.toolbar.setVisible(manager is None)
        for bar in self.bars.values():
            bar.setVisible(manager is None)
        if manager is not None:
            self.selection.hide()
            self.toast.hide()
            for card in self._floating:
                if shiboken6.isValid(card):
                    card.hide()
        self.resizeEvent(None)

    def place_selection(self, area) -> None:
        """Float the selection bar above the selection, or below it if there's no room."""
        if area is None or self.manager is not None or self.reading:
            self.selection.hide()
            return
        hint = self.selection.sizeHint()
        x = min(max(area.center().x() - hint.width() // 2, GAP), self.width() - hint.width() - GAP)
        y = area.top() - hint.height() - 12
        if y < self._band:
            y = area.bottom() + 12
        if y + hint.height() > self.height() - GAP:
            y = self._band
        self.selection.setGeometry(x, y, hint.width(), hint.height())
        self.selection.show()
        self.selection.raise_()
        self.schedule_glass([self.selection])

    def place_over_text(self, bar: QWidget, area: QRect | None) -> None:
        """Float `bar` (added with add_floating) just above `area`, the selected text in viewport pixels, or just
        below it where the toolbar leaves no room above: never on the text, and beside the page counter and the
        other bars rather than under them. A selection taller than the reading area leaves no room either side,
        and the bar waits at its top. Hidden while the text is off screen, and in reading mode."""
        if area is None or self.manager is not None or self.reading:
            bar.hide()
            return
        area = QRect(self.view.viewport().mapTo(self, area.topLeft()), area.size())
        reading = QRect(0, self._band, self.width() - SCROLLBAR, self.height() - self._band)
        if not area.intersects(reading):
            bar.hide()
            return
        size = bar.sizeHint()
        centred = area.center().x() - size.width() // 2
        spot = None
        for y in (area.top() - TEXT_BAR_GAP - size.height(), area.bottom() + TEXT_BAR_GAP):
            if self._band <= y <= self.height() - GAP - size.height():
                x = self._clear_x(centred, y, size)
                if x is not None:
                    spot = QPoint(x, y)
                    break
        if spot is None:
            x = self._clear_x(centred, self._band, size)
            spot = QPoint(x if x is not None else self._clamped_x(centred, size), self._band)
        bar.setGeometry(QRect(spot, size))
        if not bar.isVisible():  # it follows every scroll: raised once, as it shows
            bar.show()
            bar.raise_()
        self.schedule_glass([bar])

    def _clamped_x(self, x: int, size: QSize) -> int:
        return max(min(x, self.width() - SCROLLBAR - size.width() - GAP), GAP)

    def _clear_x(self, x: int, y: int, size: QSize) -> int | None:
        """An x near `x` where a bar of `size` at height `y` stays on the canvas and off the page counter, the zoom
        bar and the undo bar: moved to one side of a bar it would cover. None if no place is clear."""
        x = self._clamped_x(x, size)
        for _ in range(len(self.bars) + 1):
            rect = QRect(QPoint(x, y), size)
            hit = next((b.geometry() for b in self.bars.values() if b.isVisible() and b.geometry().intersects(rect)),
                       None)
            if hit is None:
                return x
            beside = hit.left() - GAP - size.width() if hit.center().x() >= rect.center().x() else hit.right() + GAP
            if beside != self._clamped_x(beside, size):
                return None  # no room on that side of it
            x = beside
        return None

    def place_card(self, card: QWidget, anchor: QPoint) -> None:
        """Put a card added with add_floating below and to the right of `anchor`, the pointer in viewport pixels:
        on the other side where there is no room, and always on the canvas, below the toolbar."""
        if self.reading:
            card.hide()
            return
        point = self.view.viewport().mapTo(self, anchor)
        hint = card.sizeHint()
        x, y = point.x() + CARD_OFFSET.x(), point.y() + CARD_OFFSET.y()
        if x + hint.width() > self.width() - GAP:
            x = point.x() - CARD_OFFSET.x() - hint.width()
        if y + hint.height() > self.height() - GAP:
            y = point.y() - 10 - hint.height()  # above: the cursor hangs below the point
        x = min(max(x, GAP), self.width() - hint.width() - GAP)
        y = min(max(y, self._band), self.height() - hint.height() - GAP)
        card.setGeometry(x, y, hint.width(), hint.height())
        card.raise_()

    def resizeEvent(self, event) -> None:
        if event is not None:
            super().resizeEvent(event)
        width, height = self.width(), self.height()
        hint = self.toolbar.sizeHint()
        # The pages run under the toolbar; the view only keeps room above the first one.
        self._band = hint.height() + 2 * GAP
        self.view.setGeometry(self.rect())
        self.view.set_top_inset(self._band)
        if self.manager is not None:
            self.manager.setGeometry(self.rect())
        if not self.reading:  # in reading mode the bars stay put while they fade, then wait hidden
            # The toolbar stays centred, clear of the scrollbar. The page counter sits below its band.
            self.toolbar.fit(max(width - 2 * (GAP + 14), 220))
            hint = self.toolbar.sizeHint()
            self.toolbar.setGeometry((width - hint.width()) // 2, GAP, hint.width(), hint.height())
            menu = getattr(self.toolbar, "tool_menu", None)
            if menu is not None and menu.isVisible():
                menu.reposition()  # under its button still, which moved with the canvas (the side panel opened)
            right = width - GAP - 14  # clear of the scrollbar
            undo = self.bars["undo"].sizeHint()
            zoom_bar = self.bars["zoom"]
            if hasattr(zoom_bar, "fit"):  # beside the undo bar if it can be, folding what it must
                zoom_bar.fit(right - 2 * GAP - undo.width())
            counter, zoom = (self.bars[k].sizeHint() for k in ("counter", "zoom"))
            self.bars["counter"].setGeometry(right - counter.width(), self._band, counter.width(), counter.height())
            zoom_x, zoom_y = max(right - zoom.width(), GAP), height - zoom.height() - GAP
            zoom_bar.setGeometry(zoom_x, zoom_y, zoom.width(), zoom.height())
            undo_y = height - undo.height() - GAP
            if GAP + undo.width() + GAP > zoom_x:  # no room beside it even so: above it
                undo_y = zoom_y - GAP // 2 - undo.height()
            self.bars["undo"].setGeometry(GAP, undo_y, undo.width(), undo.height())
        self.place_exit_chip()
        toast = self.toast.sizeHint()
        self.toast.setGeometry((width - toast.width()) // 2, height - toast.height() - GAP - 50, toast.width(), toast.height())
        floating = (self.toolbar, self.selection, *self.bars.values(), *self._floating, self.toast, self.exit_chip)
        for widget in floating:
            if widget is not None and shiboken6.isValid(widget):
                widget.raise_()
        self.schedule_glass()
        self.relaid.emit()


class Workspace(QWidget):
    settings_requested = Signal()
    shortcuts_requested = Signal()
    guide_requested = Signal()
    reading_requested = Signal()  # the zoom bar's reading mode button
    reading_exit_requested = Signal()  # the chip at the top, in reading mode

    def __init__(self, document: Document, renderer: RenderPool, tool_settings: ToolSettings,
                 settings: Settings | None = None, library: Library | None = None, parent=None):
        super().__init__(parent)
        self.document = document
        self.tool_settings = tool_settings
        self.settings = settings
        self.library = library
        self.busy = False  # set while a background job works on the document: autosave and closing wait
        self._renderer = renderer
        self.store = AnnotationStore(document.annotations, self)
        self.undo_stack = QUndoStack(self)
        self.view = DocumentView(document, renderer, self.store, self.undo_stack, tool_settings, settings=settings)
        self.actions = PageActions(self)
        self.view.actions = self.actions
        self.toolbar = AnnotationToolbar(tool_settings, self.view, document.read_only)
        self.toolbar.chooser_requested.connect(self.show_tool_chooser)
        self._chooser = None
        self.page_counter = PageCounter(self.view, self.toggle_page_manager)
        self.zoom_bar = ZoomBar(self.view)
        self.zoom_bar.reading_requested.connect(self.reading_requested)
        self._style_menu_back = False  # reading mode closed a pinned style menu of the selection: it comes back
        self._manager_back = False  # reading mode closed the page manager: it comes back
        self.toast = Toast()
        bars = {"counter": self.page_counter, "zoom": self.zoom_bar,
                "undo": UndoBar(self.undo_stack, self.view.end_interaction)}

        # The highest ratio of any screen, so thumbnails stay sharp wherever the window goes.
        self._ratio = QApplication.instance().devicePixelRatio()
        self.annotations = AnnotationsPanel(document, self.store)
        self.thumbnails = ThumbnailPanel(document, renderer, self._ratio, self.store)
        self.outline = OutlinePanel(document)
        self.search = SearchPanel(document)
        self.page_manager: PageManager | None = None
        self._panel_before_manager: str | None = None

        self.side = SidePanel()
        self.side.add_panel("pages", "files", "Pages", self.thumbnails)
        self.side.add_panel("outline", "bookmark-simple", "Bookmarks", self.outline)
        self.side.add_panel("search", "magnifying-glass", "Search (Ctrl+F)", self.search)
        self.side.add_panel("annotations", "chat-centered-text", "Annotations", self.annotations)
        if library is not None:
            self.library_panel = LibraryPanel(library, self._ratio)
            self.library_panel.chosen.connect(self.place_from_library)
            self.side.add_panel("library", "books", "Markup library", self.library_panel)
        self.side.add_bottom_button("graduation-cap", "Guide (F1)", self.guide_requested.emit, "guide")
        self.side.add_bottom_button("keyboard", "Keyboard shortcuts", self.shortcuts_requested.emit, "shortcuts")
        self.side.add_bottom_button("gear-six", "Settings", self.settings_requested.emit, "settings")
        side = self.view._pref("side_panel")
        if side == "always" or (side == "auto" and document.page_count > 1):
            self.side.show_panel("pages")

        self.selection_bar = SelectionBar(self)
        self.canvas = _Canvas(self.view, self.toolbar, bars, self.selection_bar, self.toast)
        self.link_preview = LinkPreview(self.view, self.canvas.place_card)
        self.canvas.add_floating(self.link_preview)
        self.text_bar = TextSelectionBar(self)
        self.canvas.add_floating(self.text_bar)
        self.tool_tips = ToolTips(self)  # a tool's tips, the first time it is picked
        self.view.text_selection_changed.connect(self._show_text_bar)
        self.view.history_changed.connect(self._on_history_changed)
        self.view.selection_changed.connect(self._update_selection_bar)
        self.view.message.connect(self.notify)
        self.view.content_changed.connect(self._follow_selection)
        self.canvas.relaid.connect(self._follow_selection)
        self.store.changed.connect(lambda *_: self._update_selection_bar(self.view.selected_annotations()))
        tool_settings.tool_changed.connect(self._on_tool_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.side)
        layout.addWidget(self.canvas, 1)

        self.annotations.annotation_activated.connect(self.view.reveal_annotation)
        self.view.current_page_changed.connect(self.page_counter.set_page)
        self.view.current_page_changed.connect(self.thumbnails.set_current_page)
        self.view.zoom_changed.connect(self.zoom_bar.set_zoom)
        # Thumbnails also follow the scrolling, so going to one is no jump: it would flood Back.
        self.thumbnails.page_activated.connect(self.view.goto_page)
        self.outline.spot_activated.connect(self._go_to_bookmark)
        self.search.hit_found.connect(self.view.add_search_hit)
        self.search.hit_selected.connect(self.view.focus_search_hit)
        self.search.cleared.connect(self.view.clear_search_hits)
        self.page_counter.set_page(0)
        self.zoom_bar.set_zoom(self.view.zoom)
        if self.view._pref("default_layout") != "single":
            self.view.set_layout(self.view._pref("default_layout"))
        if self.view._pref("default_tint") != "normal":
            self.view.set_page_tint(self.view._pref("default_tint"))
        default_zoom = self.view._pref("default_zoom")
        if default_zoom == "page":
            QTimer.singleShot(0, self.view.fit_page)
        elif default_zoom == "actual":
            QTimer.singleShot(0, lambda: self.view.set_zoom(1.0))

    def view_state(self) -> dict:
        """Where the reader is: page, zoom and the view options, to come back to later."""
        view = self.view
        return {"path": str(self.document.path), "page": view.current_page, "zoom": round(view.zoom, 4),
                "fit": view._fit, "layout": view.layout_mode, "tint": view.page_tint,
                "panel": self.side.current}

    def apply_view_state(self, state: dict) -> None:
        """Go back to a view_state(). Runs once the view has its size, so fitting works."""
        def apply():
            view = self.view
            if state.get("layout") in LAYOUTS:
                view.set_layout(state["layout"])
            if state.get("tint") in TINTS:
                view.set_page_tint(state["tint"])
            if state.get("fit") == "width":
                view.fit_width()
            elif state.get("fit") == "page":
                view.fit_page()
            elif isinstance(state.get("zoom"), (int, float)):
                view.set_zoom(float(state["zoom"]))
            page = state.get("page")
            if isinstance(page, int) and 0 <= page < self.document.page_count:
                view.goto_page(page)
            panel = state.get("panel", "")
            if panel is None:
                self.side.hide_panel()
            elif panel in self.side.keys():
                self.side.show_panel(panel)

        QTimer.singleShot(0, apply)

    def show_tool_chooser(self) -> None:
        from .tool_chooser import ToolChooser

        if self._chooser is not None:
            self._chooser.close()
        chooser = self._chooser = ToolChooser(self.toolbar, self.window())
        # Only this one: the chooser closed just now goes later, and must not make the new one be forgotten,
        # or it would outlive its tab.
        chooser.destroyed.connect(lambda _=None: self._chooser_gone(chooser))
        chooser.show_under(self.toolbar)

    def _chooser_gone(self, chooser) -> None:
        if self._chooser is chooser:
            self._chooser = None

    def show_search(self) -> None:
        self.side.show_panel("search")
        self.search.focus_field()

    def _go_to_bookmark(self, page: int, point) -> None:
        self.view.jump(page, self.document.display_y(page, point))

    def _on_history_changed(self) -> None:
        self.page_counter.refresh()  # the Back button shows or hides: the counter changes width
        self.canvas.resizeEvent(None)

    # Selection bar ------------------------------------------------------

    def _update_selection_bar(self, selected) -> None:
        if selected and self.tool_settings.tool == "select":
            self.selection_bar.sync(selected)
            self.canvas.place_selection(self.view.selection_rect())
        else:
            self.selection_bar.hide()

    def _follow_selection(self) -> None:
        if self.selection_bar.isVisible():
            self.canvas.place_selection(self.view.selection_rect())
        if self.view.has_text_selection():  # also when scrolled off screen: it comes back with the text
            self.canvas.place_over_text(self.text_bar, self.view.text_selection_rect())

    def _on_tool_changed(self, _tool) -> None:
        self._update_selection_bar(self.view.selected_annotations())
        if self.tool_settings.tool != "select":
            self.text_bar.hide()

    # Bar over selected text ---------------------------------------------

    def _show_text_bar(self, area) -> None:
        if area is None:
            self.text_bar.hide()
            return
        self.text_bar.sync(self.document.read_only)
        self.canvas.place_over_text(self.text_bar, area)

    def edit_properties(self) -> None:
        selected = self.view.selected_annotations()
        if not selected:
            return
        dialog = PropertiesDialog(selected, self)
        changes = dialog.changes() if dialog.exec() == PropertiesDialog.DialogCode.Accepted else None
        dialog.deleteLater()  # read first: the dialog does not delete itself
        if changes is not None:
            self.view.apply_style(**changes)

    def add_to_library(self) -> None:
        selected = [a for a in self.view.selected_annotations() if not isinstance(a, TextMarkup)]
        if not selected or self.library is None:
            return
        default = selected[0].label if len(selected) == 1 else f"{len(selected)} annotations"
        name = messages.get_text(self, "Add to library", "Name it, to find it again in the markup library.",
                                 default, action="Add", required=True)
        if name and name.strip():
            self.library.add(name.strip(), selected)
            self.notify(f"\"{name.strip()}\" is in the markup library.")

    def place_from_library(self, records: list) -> None:
        """Put a library item in the middle of what's on screen."""
        page = self.view.current_page
        width, height = self.document.page_size(page)
        from .clipboard import from_records, left_out, unreachable

        made = from_records(records, page, (0.0, 0.0))
        lost = {a.id for a in unreachable(made, self.document.page_count)}
        if lost:
            made = [a for a in made if a.id not in lost]
            self.notify(left_out(len(lost)))
        if not made:
            return
        _, _, w, h = union(a.bounds() for a in made)
        centre = self.view.visible_centre(page)
        x = min(max(centre[0] - w / 2, 0.0), max(width - w, 0.0))
        y = min(max(centre[1] - h / 2, 0.0), max(height - h, 0.0))
        placed = [a.translated(x, y) for a in made]
        self.view.add_annotations(placed, "Library item")
        self.tool_settings.set_tool("select")
        self.view.select(placed)

    def apply_to_pages(self) -> None:
        selected = self.view.selected_annotations()
        if not selected:
            return
        dialog = ApplyToPagesDialog(self.document.page_count, selected[0].page, self)
        accepted = dialog.exec() == ApplyToPagesDialog.DialogCode.Accepted
        try:
            pages = dialog.pages() if accepted else None
        except ValueError:
            pages = ValueError
        dialog.deleteLater()  # read first: the dialog does not delete itself
        if pages is ValueError:
            messages.warn(self, "Apply to pages", "Write pages like 1-3, 7.")
            return
        if pages is None:
            return
        count = self.view.apply_to_pages(pages)
        self.notify(f"Added {count} annotation{'s' if count != 1 else ''}.")

    def flatten_selected(self) -> None:
        selected = self.view.selected_annotations()
        # A redaction mark burnt into the page would hide nothing: the text under it would stay.
        ids = {a.id for a in selected if not isinstance(a, Redaction)}
        kept = len(selected) - len(ids)
        if not ids:
            if kept:
                self.notify("Redaction marks can't be flattened. Apply the redactions to remove what they cover.")
            return
        if self.confirm("flatten", "Flatten", "Make the selection part of the page? It will look the same, "
                        "but it can't be moved or edited any more, in Zenith PDF or any other app.",
                        action="Flatten"):
            if self.edit_structure("Flatten", lambda annotations: self.document.flatten(annotations, ids)) and kept:
                self.notify("Redaction marks were left as they are. Apply the redactions to remove what they cover.")

    def confirm(self, question: str, title: str, text: str, action: str = "OK", danger: bool = False) -> bool:
        """Ask a yes/no question the user can turn off with "Don't ask again"."""
        return messages.confirm_once(self, self.settings, question, title, text, action, danger)

    def notify(self, text: str) -> None:
        self.toast.show_message(text)
        self.canvas.resizeEvent(None)

    # Page manager -------------------------------------------------------

    def toggle_page_manager(self) -> None:
        if self.page_manager is None:
            self.view.finish_text_edit()
            self.view.clear_text_selection()  # its bar would come back over other pages
            # The manager shows every page itself: the Pages panel beside it would show them twice.
            self._panel_before_manager = self.side.current
            self.side.hide_panel()
            self.page_manager = PageManager(self, self._renderer, self._ratio)
            self.page_manager.closed.connect(self._close_page_manager)
            self.page_manager.reload(select=self.view.current_page)
            self.canvas.show_manager(self.page_manager)
            self.page_manager.list.setFocus()
        else:
            self._close_page_manager(self.page_manager.current_page())

    def _close_page_manager(self, page: int) -> None:
        if self.page_manager is None:
            return
        manager, self.page_manager = self.page_manager, None
        self.canvas.show_manager(None)
        manager.release()
        manager.hide()
        manager.setParent(None)
        manager.deleteLater()
        panel = self._panel_before_manager
        if panel in self.side.keys() and self.side.current is None:
            self.side.show_panel(panel)
        self.view.goto_page(page)
        self.thumbnails.set_current_page(self.view.current_page)
        self.view.setFocus()

    # Reading mode -------------------------------------------------------

    def set_reading(self, on: bool) -> None:
        """Reading mode for this tab (see reading.py): only the pages show. The side panel and the rail go as a
        whole, so the panel that was open comes back with them; the bars fade; the selection stays, unmarked."""
        if on == self.canvas.reading:
            return
        if on:
            self.close_page_manager_for_reading()
            if self._chooser is not None:
                self._chooser.close()
            self.link_preview.leave()
            menu = self.selection_bar.style_menu
            self._style_menu_back = menu is not None and menu.isVisible() and menu.pinned
            if menu is not None and menu.isVisible():
                menu.close_menu()
            self.text_bar.hide()
        self.side.setVisible(not on)
        self.view.set_reading(on)
        self.canvas.set_reading(on)
        if not on:
            self._update_selection_bar(self.view.selected_annotations())
            if self.view.has_text_selection():
                self._show_text_bar(self.view.text_selection_rect())
            if self._style_menu_back and self.selection_bar.isVisible():
                self.selection_bar.open_style()
            self._style_menu_back = False
            if self._manager_back:  # reading began in the page manager: back to it, on the page read
                self._manager_back = False
                self.toggle_page_manager()

    def close_page_manager_for_reading(self) -> None:
        """Reading mode shows the page picked in the page manager, and the manager comes back after it."""
        if self.page_manager is not None:
            self._manager_back = True
            self._close_page_manager(self.page_manager.current_page())

    def exit_chip(self) -> FloatingBar:
        """The chip that leaves reading mode, made the first time it is needed."""
        if self.canvas.exit_chip is None:
            from .reading import ExitChip

            chip = ExitChip()
            chip.clicked.connect(self.reading_exit_requested)
            self.canvas.set_exit_chip(chip)
        return self.canvas.exit_chip

    # Page structure -----------------------------------------------------

    def edit_structure(self, label: str, edit, error_title: str = "Can't change the pages") -> bool:
        """Run a page edit as one undoable step. `edit(annotations)` changes the document."""
        if self.document.read_only:
            return False
        self.view.finish_text_edit()
        annotations = self.store.all()
        try:
            before = self.document.snapshot(annotations)
            self._renderer.forget(self.document)
            edit(annotations)
        except Exception as error:
            self._after_restore()  # the document put itself back; resynchronise the views
            messages.warn(self, error_title, "Nothing was changed.", detail=str(error))
            return False
        self._after_restore()
        self.undo_stack.push(ChangePages(label, before, self.document.data, self._restore))
        return True

    def replace_structure(self, title: str, before: bytes, after: bytes) -> bool:
        """Swap in a document made elsewhere (a Compress run in the background) as one undoable step.

        `before` is the snapshot the job started from, so Undo brings it back.
        """
        if self.document.read_only:
            return False
        self.view.finish_text_edit()
        try:
            self._restore(after)
        except Exception as error:
            self._restore(before)
            messages.warn(self, title, "The result could not be opened, so nothing was changed.", detail=str(error))
            return False
        self.undo_stack.push(ChangePages(title, before, after, self._restore))
        return True

    def _restore(self, data: bytes) -> None:
        self._renderer.forget(self.document)
        self.document.restore(data)
        self._after_restore()

    def _after_restore(self) -> None:
        """Bring every view in line with the document after its pages changed."""
        self.store.replace_all(self.document.annotations)
        self.view.snapper.forget()
        # The lists first: the view's reload says which page is current, and a list still holding rows of pages
        # that went would be asked about them.
        self.thumbnails.reload()
        self.view.reload()
        self.thumbnails.set_current_page(self.view.current_page)  # the reload forgot which page is current
        self.outline.reload()
        self.search.reset()
        self.page_counter.refresh()
        self.text_bar.hide()  # the reload let the selected text go
        if self.page_manager is not None:
            self.page_manager.reload(select=self.view.current_page)
        self.canvas.resizeEvent(None)

    # Saving and closing -------------------------------------------------

    @property
    def dirty(self) -> bool:
        return not self.undo_stack.isClean() or self.document.unsaved

    def save(self, path: str | None = None) -> None:
        self.view.finish_text_edit()
        self.document.save(self.store.all(), path)
        self.undo_stack.setClean()

    def close_document(self) -> None:
        if self._chooser is not None:
            self._chooser.close()
        if self.page_manager is not None:
            self.page_manager.release()
        self.link_preview.leave()  # no card drawn from a closed document
        self.search.shutdown()
        self.view.release()
        self.thumbnails.release()
        self._renderer.forget(self.document)
        self.document.close()
