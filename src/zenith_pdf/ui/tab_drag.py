"""Dragging a document's tab out of its window: dropped on another Zenith PDF window it moves there, dropped
anywhere else it opens in a new window of its own.

Along the strip the tab bar moves tabs itself. Once the pointer is THRESHOLD pixels
above or below the strip the tab comes loose: the tab bar's own move ends, and a
picture of the document follows the pointer until the button is let go.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ..theme import themes

if TYPE_CHECKING:
    from .main_window import MainWindow

THRESHOLD = 32  # px above or below the tab strip before a tab comes loose
GHOST_WIDTH = 260
GHOST_OFFSET = QPoint(14, 14)  # the picture hangs below and right of the pointer
NEW_WINDOW_OFFSET = QPoint(120, 18)  # a new window opens with its tab strip under the pointer


class TabGhost(QWidget):
    """The picture of the document that follows the pointer while its tab is dragged."""

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
        self.setFixedSize(GHOST_WIDTH + 12, int(self.picture.height() / ratio) + 40)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(frame, 10, 10)
        painter.fillPath(path, QColor(t.raised))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.drawPath(path)
        painter.setPen(QColor(t.text))
        text = QRectF(12, 6, self.width() - 24, 24)
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(text.width())))
        clip = QPainterPath()
        target = QRectF(6, 34, self.width() - 12, self.height() - 40)
        clip.addRoundedRect(target, 6, 6)
        painter.setClipPath(clip)
        painter.drawPixmap(target.topLeft(), self.picture)


class TabDragger(QObject):
    """Watches a window's tab strip for a tab pulled out of it."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window = window
        self.index = -1
        self.dragging = False
        self.ghost: TabGhost | None = None
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
        self.follow(pointer)
        self.ghost.show()

    def follow(self, pointer: QPoint) -> None:
        if self.ghost is not None:
            self.ghost.move(pointer + GHOST_OFFSET)

    def finish(self, pointer: QPoint) -> None:
        """Let go: on another window the tab moves there, anywhere else it gets a window of its own."""
        self.dragging = False
        if self.ghost is not None:
            self.ghost.hide()
            self.ghost.deleteLater()
            self.ghost = None
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
