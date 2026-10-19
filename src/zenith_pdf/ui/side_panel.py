"""Icon rail on the left edge plus the panel it opens, which its right edge widens or narrows."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .widgets import IconButton

RAIL_WIDTH = 48
PANEL_WIDTH = 264  # at first; dragging the panel's right edge changes it, for every tab
PANEL_MIN, PANEL_MAX = 200, 640
GRIP = 5  # px of the panel's right edge that take the drag: its border and the scroll bar's margin, no more


class _Grip(QWidget):
    """The panel's right edge: dragged, it makes the panel wider or narrower."""

    def __init__(self, side: SidePanel):
        super().__init__(side)
        self._side = side
        self._start: tuple[float, int] | None = None  # where the drag began, and the width then
        self.setCursor(Qt.CursorShape.SplitHCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = (event.globalPosition().x(), self._side.panel_width())

    def mouseMoveEvent(self, event) -> None:
        if self._start is not None:
            x, width = self._start
            self._side.set_panel_width(round(width + event.globalPosition().x() - x))

    def mouseReleaseEvent(self, event) -> None:
        if self._start is not None:
            self._start = None
            self._side.resized.emit(self._side.panel_width())


class SidePanel(QWidget):
    resized = Signal(int)  # the panel's width, once a drag of its edge ends

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rail = QFrame()
        self._rail.setObjectName("Rail")
        self._rail.setFixedWidth(RAIL_WIDTH)
        self._rail_layout = QVBoxLayout(self._rail)
        self._rail_layout.setContentsMargins(6, 8, 6, 8)
        self._rail_layout.setSpacing(4)
        self._rail_layout.addStretch()

        self._stack = QStackedWidget()
        self._stack.setObjectName("Panel")
        self._stack.setFixedWidth(PANEL_WIDTH)
        self._stack.hide()
        self._grip = _Grip(self)
        self._grip.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._rail)
        layout.addWidget(self._stack)

        self._buttons: dict[str, IconButton] = {}
        self._panels: dict[str, QWidget] = {}

    def panel_width(self) -> int:
        return self._stack.minimumWidth()  # its fixed width, shown or not

    def set_panel_width(self, width: int) -> None:
        """The panel this wide, within PANEL_MIN and PANEL_MAX."""
        self._stack.setFixedWidth(max(PANEL_MIN, min(PANEL_MAX, int(width))))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_grip()

    def _place_grip(self) -> None:
        self._grip.setVisible(self._stack.isVisibleTo(self))
        if self._grip.isVisible():
            self._grip.setGeometry(RAIL_WIDTH + self._stack.minimumWidth() - GRIP, 0, GRIP, self.height())
            self._grip.raise_()

    def add_panel(self, key: str, icon_name: str, tooltip: str, widget: QWidget) -> None:
        button = IconButton(icon_name, tooltip, checkable=True, help=f"panel_{key}")
        button.clicked.connect(lambda: self.toggle(key))
        self._rail_layout.insertWidget(len(self._buttons), button)
        self._stack.addWidget(widget)
        self._buttons[key] = button
        self._panels[key] = widget

    def add_bottom_button(self, icon_name: str, tooltip: str, slot, help: str | None = None) -> IconButton:
        """A plain button at the foot of the rail, below the stretch."""
        button = IconButton(icon_name, tooltip, help=help)
        button.clicked.connect(slot)
        self._rail_layout.addWidget(button)
        return button

    def keys(self) -> list[str]:
        return list(self._panels)

    @property
    def current(self) -> str | None:
        if self._stack.isHidden():
            return None
        return next(key for key, widget in self._panels.items() if widget is self._stack.currentWidget())

    def show_panel(self, key: str) -> None:
        self._stack.setCurrentWidget(self._panels[key])
        self._stack.show()
        for name, button in self._buttons.items():
            button.setChecked(name == key)
        self._place_grip()

    def hide_panel(self) -> None:
        self._stack.hide()
        for button in self._buttons.values():
            button.setChecked(False)
        self._place_grip()

    def toggle(self, key: str | None = None) -> None:
        key = key or next(iter(self._panels))
        if self.current == key:
            self.hide_panel()
        else:
            self.show_panel(key)
