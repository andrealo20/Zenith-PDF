"""Dragging a document's tab out of its window: dropped on another Zenith PDF window it moves there, dropped
anywhere else it opens in a new window of its own.

Along the strip the tab bar moves tabs itself. Once the pointer is THRESHOLD pixels
above or below the strip the tab comes loose: the tab bar's own move ends, and a
picture of the document follows the pointer until the button is let go. Over another
window of the app a line in its tab strip shows where the tab would go.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ..theme import outline, themes
from . import motion
from .widgets import SHADOW_BLUR, SHADOW_OFFSET, _nine_slice, _shadow_picture, shadow_colour

if TYPE_CHECKING:
    from .main_window import MainWindow

THRESHOLD = 32  # px above or below the tab strip before a tab comes loose
GHOST_WIDTH = 260
GHOST_OFFSET = QPoint(14, 14)  # the card hangs below and right of the pointer
GHOST_MARGIN = SHADOW_BLUR + SHADOW_OFFSET  # px of the ghost's window round its card, for the shadow
MARK_WIDTH = 3  # px: the line that shows where a tab would drop
NEW_WINDOW_OFFSET = QPoint(120, 18)  # a new window opens with its tab strip under the pointer


class TabGhost(QWidget):
    """The picture of the document that follows the pointer while its tab is dragged, on a card with a shadow."""

    def __init__(self, picture: QPixmap, title: str):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ratio = self.devicePixelRatioF()
        self.picture = picture.scaledToWidth(int(GHOST_WIDTH * ratio), Qt.TransformationMode.SmoothTransformation)
        self.picture.setDevicePixelRatio(ratio)
        self.title = title
        self.setFixedSize(GHOST_WIDTH + 12 + 2 * GHOST_MARGIN,
                          int(self.picture.height() / ratio) + 40 + 2 * GHOST_MARGIN)

    def place(self, pointer: QPoint) -> None:
        """Hang the card below and right of `pointer`: the window is larger than it by the shadow's margin."""
        self.move(pointer + GHOST_OFFSET - QPoint(GHOST_MARGIN, GHOST_MARGIN))

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(GHOST_MARGIN, GHOST_MARGIN, -GHOST_MARGIN, -GHOST_MARGIN)
        radius = max(t.look.card - 2, 0)
        if t.look.shadow != "none":  # as the floating bars cast theirs: below, or all round in a glow
            drop = 0 if t.look.shadow == "glow" else SHADOW_OFFSET
            reach = card.toRect().adjusted(-SHADOW_BLUR, drop - SHADOW_BLUR, SHADOW_BLUR, drop + SHADOW_BLUR)
            cast = _shadow_picture(round(radius), round(self.devicePixelRatioF() * 100), shadow_colour(t).rgba(),
                                   t.look)
            _nine_slice(painter, reach, cast, SHADOW_BLUR + round(radius))
        painter.translate(card.topLeft())
        frame = QRectF(0, 0, card.width(), card.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = outline(frame, radius, t.look)
        painter.fillPath(path, QColor(t.raised))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawPath(path)
        painter.setPen(QColor(t.text))
        text = QRectF(12, 6, card.width() - 24, 24)
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(text.width())))
        target = QRectF(6, 34, card.width() - 12, card.height() - 40)
        painter.setClipPath(outline(target, t.look.item, t.look))
        painter.drawPixmap(target.topLeft(), self.picture)


class DropMark(QWidget):
    """A line of the accent in the tab strip of the window a tab is over: where the tab would go if it were dropped."""

    def __init__(self):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def place(self, window: MainWindow, pointer: QPoint) -> None:
        """Show it in `window`'s strip, before the tab under `pointer` or after the last one."""
        strip, tabs = window.top_bar, window.tabs
        index = window.tab_index_at(pointer)
        if tabs.isVisible() and tabs.count():
            rect = tabs.tabRect(index if index is not None else tabs.count() - 1)
            edge = tabs.mapTo(strip, QPoint(rect.left() if index is not None else rect.right() + 1, rect.top()))
            left, top, height = edge.x(), edge.y(), rect.height()
        else:  # no tabs yet: where the first would stand, by the new tab button
            button = window.open_button.geometry()
            left, top, height = button.left() - MARK_WIDTH, button.top(), button.height()
        if self.parentWidget() is not strip:
            self.setParent(strip)
        self.setGeometry(left - MARK_WIDTH // 2, top + 4, MARK_WIDTH, max(height - 8, 4))
        self.show()
        self.raise_()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.focus or t.accent))
        painter.drawRoundedRect(QRectF(self.rect()), MARK_WIDTH / 2, MARK_WIDTH / 2)


class TabDragger(QObject):
    """Watches a window's tab strip for a tab pulled out of it."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window = window
        self.index = -1
        self.dragging = False
        self.ghost: TabGhost | None = None
        self.mark: DropMark | None = None
        self._passing = False  # an event this sends the tab bar itself
        window.tabs.installEventFilter(self)

    @property
    def tabs(self):
        return self.window.tabs

    def eventFilter(self, obj, event) -> bool:
        if self._passing or obj is not self.tabs:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.index = self.tabs.tabAt(event.position().toPoint())
            self.dragging = False
        elif kind == QEvent.Type.MouseMove and self.index >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            pointer = event.globalPosition().toPoint()
            if self.dragging:
                self.follow(pointer)
                return True
            local = self.tabs.mapFromGlobal(pointer)
            if (local.y() < -THRESHOLD or local.y() > self.tabs.height() + THRESHOLD) and self.can_drag(self.index):
                self.start(pointer, event)
                return True
        elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.finish(event.globalPosition().toPoint())
                return True
            self.index = -1
        return False

    def can_drag(self, index: int) -> bool:
        from .workspace import Workspace

        return 0 <= index < len(self.window.workspaces) and isinstance(self.window.workspaces[index], Workspace)

    def start(self, pointer: QPoint, event) -> None:
        """The tab comes loose: the tab bar's own move ends where it began, and the picture shows."""
        self._passing = True
        try:
            release = QMouseEvent(QEvent.Type.MouseButtonRelease, event.position(), event.globalPosition(),
                                  Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, event.modifiers())
            QApplication.sendEvent(self.tabs, release)
        finally:
            self._passing = False
        self.dragging = True
        workspace = self.window.workspaces[self.index]
        self.ghost = TabGhost(workspace.grab(), self.tabs.tabText(self.index))
        self.mark = DropMark()
        self.follow(pointer)
        self.ghost.show()
        motion.appear(self.ghost, 0, motion.FAST)  # it fades in where it is, as the pointer takes it

    def follow(self, pointer: QPoint) -> None:
        if self.ghost is not None:
            self.ghost.place(pointer)
        if self.mark is not None:
            target = window_at(pointer)
            if target is not None and target is not self.window:
                self.mark.place(target, pointer)
            else:
                self.mark.hide()

    def finish(self, pointer: QPoint) -> None:
        """Let go: on another window the tab moves there, anywhere else it gets a window of its own."""
        self.dragging = False
        if self.ghost is not None:
            self.ghost.hide()
            self.ghost.deleteLater()
            self.ghost = None
        if self.mark is not None:
            self.mark.hide()
            self.mark.deleteLater()
            self.mark = None
        index, self.index = self.index, -1
        if not self.can_drag(index):
            return
        workspace = self.window.workspaces[index]
        target = window_at(pointer)
        if target is self.window:
            return  # back where it came from
        if target is not None:
            self.window.move_tab(workspace, target, target.tab_index_at(pointer))
        else:
            self.window.tear_off(workspace, pointer - NEW_WINDOW_OFFSET)


def window_at(pointer: QPoint):
    """The Zenith PDF window under `pointer`, if any."""
    from .main_window import MainWindow

    widget = QApplication.topLevelAt(pointer)
    while widget is not None and not isinstance(widget, MainWindow):
        widget = widget.parentWidget()
    if widget is not None:
        return widget
    for window in reversed(MainWindow.open_windows):  # the newest first: it is likelier on top
        if window.isVisible() and window.frameGeometry().contains(pointer):
            return window
    return None
