"""The tool chooser: every tool by group, to use at once or to add to the toolbar.

Click a tool to use it. Its + adds it to the toolbar (a tick shows it is there;
clicking the tick takes it off, with any copies of it), or drag it onto the
toolbar to put it exactly where you want it. It always acts on the document on
screen, even if another tab opened it.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..icons import icon
from ..theme import outline, themes
from . import motion
from .help import TOPICS
from .tool_catalog import ALWAYS, CATALOG, GROUPS, TOOL_MIME, ToolSpec, base_of
from .tool_menu import Levels
from .toolbar import tool_colour
from .widgets import FloatingBar, IconButton

COLUMNS = 5
TILE = QSize(124, 138)
CIRCLE = 78
BADGE = 26


def _shape(rect: QRectF, round_: float):
    """A round shape (a well, a badge) as the style draws its surfaces: round, or a square with two corners cut,
    each cut about a third of the radius."""
    look = themes.current.look
    return outline(rect, round_ * 0.36 if look.chamfer else round_, look)


class ToolTile(QWidget):
    used = Signal(str)
    toggled = Signal(str)

    def __init__(self, spec: ToolSpec, on_toolbar, colour=None, parent=None):
        super().__init__(parent)
        self.spec = spec
        self._on_toolbar = on_toolbar  # callable: is it on the toolbar now?
        self._colour = colour  # callable: the colour of a tool's icon; the catalog's without it
        self._press: QPoint | None = None
        # What is painted of the state: how far the pointer is on the tile, how hard it is pressed, and how far the
        # tool is on the toolbar (the badge).
        self._hot = motion.Smooth(self)
        self._down = motion.Smooth(self)
        self._listed: motion.Smooth | None = None
        for animated in (self._hot, self._down):
            animated.changed.connect(self.update)
        self.setFixedSize(TILE)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        topic = TOPICS.get(spec.help)
        self.setToolTip(tr(topic.what if topic else spec.title))
        themes.changed.connect(self.update)

    def colour(self) -> str:
        return (self._colour(self.spec.key) if self._colour is not None else "") or self.spec.color

    def _circle(self) -> QRectF:
        return QRectF((self.width() - CIRCLE) / 2, 8, CIRCLE, CIRCLE)

    def _badge(self) -> QRectF:
        circle = self._circle()
        return QRectF(circle.right() - BADGE * 0.75, circle.top() - BADGE * 0.2, BADGE, BADGE)

    def _over_badge(self) -> bool:
        return self.spec.key not in ALWAYS and self.underMouse() and \
            self._badge().contains(self.mapFromGlobal(self.cursor().pos()).toPointF())

    def _well(self, t) -> QColor:
        """The disc the icon stands on: soft, lighter (or darker) under the pointer, and again under a press."""
        well = QColor(t.canvas)
        if t.name != "dark":  # halfway between the panel and the canvas: a soft grey that dark icons stand out on
            panel = QColor(t.raised)
            well = QColor((well.red() + panel.red()) // 2, (well.green() + panel.green()) // 2,
                          (well.blue() + panel.blue()) // 2)
        ink = QColor(t.text)  # toward the text colour, to a step you see but short of the panel's own tone
        over = motion.mixed(well, ink, 0.055)
        pressed = motion.mixed(well, ink, 0.09)
        return motion.mixed(motion.mixed(well, over, self._hot.value), pressed, self._down.value)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = self._circle()
        hovered = self.underMouse()
        pressed = self._press is not None and not self._badge().contains(self._press.toPointF())
        self._hot.go(1.0 if hovered else 0.0, motion.FAST if hovered else motion.NORMAL,
                     motion.EASE_OUT if hovered else motion.EASE_IN)
        self._down.go(1.0 if pressed else 0.0, motion.FAST)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._well(t))
        painter.drawPath(_shape(circle, CIRCLE / 2))
        size = 38
        painter.save()  # the icon comes up to meet the pointer, and gives under a press
        painter.translate(circle.center())
        grow = 1.0 + 0.07 * self._hot.value - 0.1 * self._down.value
        painter.scale(grow, grow)
        icon(self.spec.icon, self.colour()).paint(painter, QRect(-size // 2, -size // 2, size, size))
        painter.restore()
        if self.spec.key not in ALWAYS:  # these are always on the toolbar: no badge
            self._paint_badge(painter, t)
        painter.setPen(QColor(t.text))
        font = QFont(self.font())
        painter.setFont(font)
        painter.drawText(QRectF(0, circle.bottom() + 8, self.width(), 40),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                         tr(self.spec.title))

    def _paint_badge(self, painter: QPainter, t) -> None:
        """The + that adds the tool to the toolbar, or the tick that says it is there (and, under the pointer, the
        minus that takes it off); the ring and the mark change colour as the tool comes and goes."""
        on = bool(self._on_toolbar(self.spec.key))
        if self._listed is None:
            self._listed = motion.Smooth(self, float(on))
            self._listed.changed.connect(self.update)
        self._listed.go(1.0 if on else 0.0, motion.NORMAL, motion.EASE_OUT)
        mark = motion.mixed(QColor(t.text_muted), QColor(t.accent), self._listed.value)
        glyph = "plus"
        if on and self._over_badge():
            glyph, mark = "minus", QColor(t.danger)
        elif on and self._listed.value > 0.5:
            glyph = "check"
        badge = self._badge()
        painter.setBrush(QColor(t.raised))
        painter.setPen(QPen(mark, 1.2))
        painter.drawPath(_shape(badge, BADGE / 2))
        icon(glyph, mark.name()).paint(painter, badge.adjusted(6, 6, -6, -6).toRect())

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.spec.key not in ALWAYS and \
                self._badge().contains(event.pos().toPointF()):
            # The badge says what it does: the tile's own tip says what the tool is.
            QToolTip.showText(event.globalPos(), tr("Remove from the toolbar" if self._on_toolbar(self.spec.key)
                                                    else "Add it to the toolbar"), self)
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:
        self.update()
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._press = None
                self._drag()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = None
        if self.spec.key not in ALWAYS and self._badge().contains(event.position()):
            self.toggled.emit(self.spec.key)
        elif self.rect().contains(event.position().toPoint()):
            self.used.emit(self.spec.key)
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()

    def _drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TOOL_MIME, QByteArray(self.spec.key.encode("ascii")))
        drag.setMimeData(mime)
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(CIRCLE * ratio), round(CIRCLE * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(themes.current.raised))
        painter.drawPath(_shape(QRectF(0, 0, CIRCLE, CIRCLE), CIRCLE / 2))
        icon(self.spec.icon, self.colour()).paint(painter, QRectF(20, 20, CIRCLE - 40, CIRCLE - 40).toRect())
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(CIRCLE // 2, CIRCLE // 2))
        drag.exec(Qt.DropAction.CopyAction)


class _Dots(QWidget):
    """One dot per group; the current one lit. Clicking a dot opens its group."""

    clicked = Signal(int)

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count = count
        self.current = 0
        self._lit = Levels(self, motion.FAST)  # how far each dot has come to being the current one
        self.setFixedSize(count * 20, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_current(self, index: int) -> None:
        self.current = index
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        t = themes.current
        for i in range(self._count):
            self._lit.go(i, i == self.current)
            lit = self._lit.value(i)
            painter.setBrush(motion.mixed(QColor(t.border), QColor(t.accent), lit))
            radius = 4 + lit  # the current one swells a little
            painter.drawEllipse(QRectF(i * 20 + 10 - radius, 8 - radius, 2 * radius, 2 * radius))

    def mouseReleaseEvent(self, event) -> None:
        index = int(event.position().x() // 20)
        if 0 <= index < self._count:
            self.clicked.emit(index)


class _Empty(QWidget):
    """What a search with no match leaves on the page: a mark and a line, in the middle of it."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(TILE.height())
        self.setAccessibleName(tr("No tool matches."))  # painted, not a label: a screen reader reads it from here
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(COLUMNS * TILE.width(), TILE.height())  # as wide as the tiles it stands in for

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        icon("magnifying-glass", t.text_muted).paint(painter, QRect((self.width() - 32) // 2, 22, 32, 32))
        painter.setPen(QColor(t.text_muted))
        painter.drawText(QRectF(0, 64, self.width(), 28), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         self.accessibleName())


def centred_x(centre: int, width: int, window: QRect, screen: QRect) -> int:
    """Where a chooser `width` wide goes to be centred on `centre`: on the screen, and over the window as well
    when the window is wide enough (the side panel pushes the toolbar right of the window's middle)."""
    area = window.intersected(screen)
    if area.width() < width:
        area = screen
    return min(max(centre - width // 2, area.left()), area.right() + 1 - width)


class ToolChooser(QWidget):
    """A window of its own, so its tools can be dragged onto the toolbar behind it."""

    def __init__(self, toolbar, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._toolbar = toolbar
        self._tiles: list[ToolTile] = []
        panel = FloatingBar(self, solid=True)
        panel.setObjectName("ToolChooser")

        title = QLabel(tr("Choose a tool"))
        title.setObjectName("ChooserTitle")
        close = IconButton("x", "Close (Esc)", icon_size=20)
        close.clicked.connect(self.close)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)

        self.tabs: list[QToolButton] = []
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        for index, group in enumerate(GROUPS):
            tab = QToolButton()
            tab.setText(tr(group))
            tab.setObjectName("ChooserTab")
            tab.setCheckable(True)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _=False, i=index: self.show_group(i))
            tabs.addWidget(tab)
            self.tabs.append(tab)
        tabs.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText(tr("Search tools"))
        self.search.setFixedWidth(200)
        self._search_icon = self.search.addAction(icon("magnifying-glass"), QLineEdit.ActionPosition.TrailingPosition)
        self.search.textChanged.connect(self._search)
        tabs.addWidget(self.search)

        self.pages = QStackedWidget()
        for group in GROUPS:
            self.pages.addWidget(self._page([spec for spec in CATALOG if spec.group == group]))
        self.results = self._page([])
        self.pages.addWidget(self.results)
        self.dots = _Dots(len(GROUPS))
        self.dots.clicked.connect(self.show_group)

        footer = QWidget()
        footer.setObjectName("ChooserFooter")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 14, 24, 14)
        hint = QLabel(tr("<b>Drag and drop</b>&nbsp;&nbsp;Add a tool straight to your toolbar, where you want it. "
                      "Right-click a toolbar button for its menu: style, lock, move or remove."))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        footer_layout.addWidget(hint, 1)
        self.hand = QLabel()
        footer_layout.addWidget(self.hand)

        inner = QVBoxLayout(panel)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        body = QVBoxLayout()
        body.setContentsMargins(28, 18, 22, 10)
        body.setSpacing(14)
        body.addLayout(top)
        body.addLayout(tabs)
        body.addWidget(self.pages, 1)
        body.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addLayout(body, 1)
        inner.addWidget(footer)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 18)  # room for the shadow
        outer.addWidget(panel)
        self.setFixedSize(COLUMNS * TILE.width() + 200, 560)
        settings = toolbar._view.settings
        if settings is not None:
            settings.changed.connect(self._on_setting)
        themes.changed.connect(self._retheme)
        self._retheme()
        self.show_group(0)

    def _retheme(self, *_) -> None:
        self.hand.setPixmap(icon("hand-pointing", themes.current.accent).pixmap(QSize(26, 26),
                                                                                  self.devicePixelRatioF()))
        self._search_icon.setIcon(icon("magnifying-glass"))

    def _page(self, specs) -> QScrollArea:
        """A grid of tiles that scrolls when there are more rows than room."""
        page = QWidget()
        page.setObjectName("ChooserPage")
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._fill(grid, specs)
        scroll = QScrollArea()
        scroll.setObjectName("ChooserScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("#ChooserScroll, #ChooserPage { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(page)
        scroll.grid = grid
        return scroll

    def _fill(self, grid: QGridLayout, specs) -> None:
        while grid.count():
            widget = grid.takeAt(0).widget()
            if widget is not None:
                if widget in self._tiles:
                    self._tiles.remove(widget)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for position, spec in enumerate(specs):
            tile = ToolTile(spec, lambda key: self.toolbar().has_tool(key), self._colour)
            tile.used.connect(self._use)
            tile.toggled.connect(self._toggle)
            grid.addWidget(tile, position // COLUMNS, position % COLUMNS)
            self._tiles.append(tile)
        # The tiles stand at the left of the page; the line that says there are none is in the middle of it.
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | (Qt.AlignmentFlag.AlignLeft if specs else Qt.AlignmentFlag(0)))
        if not specs:
            grid.addWidget(_Empty(), 0, 0, 1, COLUMNS)

    def show_group(self, index: int) -> None:
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        before = self.pages.currentIndex()
        self.pages.setCurrentIndex(index)
        for i, tab in enumerate(self.tabs):
            tab.setChecked(i == index)
        self.dots.set_current(index)
        self.dots.show()
        if index != before and before < len(GROUPS):  # a later group comes from the right, an earlier from the left
            motion.slide_in(self.pages.currentWidget(), Qt.Edge.RightEdge if index > before else Qt.Edge.LeftEdge, 24)

    def _search(self, text: str) -> None:
        words = text.lower().split()
        if not words:
            self.show_group(self.dots.current)
            return
        def text(spec: ToolSpec) -> str:
            topic = TOPICS.get(spec.help)
            return f"{spec.title} {spec.group} {topic.what if topic else ''}".lower()

        found = [spec for spec in CATALOG if all(word in text(spec) for word in words)]
        self._fill(self.results.grid, found)
        self.results.verticalScrollBar().setValue(0)
        self.pages.setCurrentWidget(self.results)
        for tab in self.tabs:
            tab.setChecked(False)
        self.dots.hide()

    def _colour(self, key: str) -> str | None:
        """A tool's icon in its ink, made readable, as the toolbar shows it: None for tools without one."""
        return tool_colour(self.toolbar()._settings, key)

    def toolbar(self):
        """The toolbar of the document on screen now: the tab that opened the chooser may not be it any more."""
        window = self.parentWidget()
        current = getattr(window, "current_workspace", None)
        workspace = current() if callable(current) else None
        toolbar = getattr(workspace, "toolbar", None)
        return toolbar if toolbar is not None else self._toolbar

    def _use(self, key: str) -> None:
        toolbar = self.toolbar()
        self.close()
        toolbar.use(key)

    def _toggle(self, key: str) -> None:
        toolbar = self.toolbar()
        if toolbar.has_tool(key):
            toolbar.remove_tools([k for k in toolbar.keys() if base_of(k) == key])  # the tool and its copies
        else:
            toolbar.add_tool(key)

    def _on_setting(self, key: str) -> None:
        if key == "toolbar_tools":
            for tile in self._tiles:
                tile.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not self.search.hasFocus():
            step = 1 if event.key() == Qt.Key.Key_Right else -1
            self.show_group((self.dots.current + step) % len(GROUPS))
        else:
            super().keyPressEvent(event)

    def show_under(self, anchor: QWidget) -> None:
        """On the pages' side of the toolbar (under it, at the top), so the toolbar stays in view for dragging."""
        bar = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
        screen = anchor.screen().availableGeometry()
        side = getattr(anchor, "side", "top")
        if side in ("top", "bottom"):
            x = centred_x(bar.center().x(), self.width(), anchor.window().frameGeometry(), screen)
            y = bar.bottom() + 4 if side == "top" else bar.top() - 4 - self.height()
        else:
            x = bar.right() + 4 if side == "left" else bar.left() - 4 - self.width()
            y = bar.center().y() - self.height() // 2
        x = min(max(x, screen.left()), screen.right() - self.width())
        y = min(max(y, screen.top()), screen.bottom() - self.height())
        self.move(x, y)
        self.show()
        motion.appear(self, motion.RISE if side == "bottom" else -motion.RISE)  # from the toolbar, away from it
        self.raise_()
        self.activateWindow()
