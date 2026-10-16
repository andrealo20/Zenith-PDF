"""Icon rail on the left edge plus the panel it opens."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .widgets import IconButton

RAIL_WIDTH = 48
PANEL_WIDTH = 264


class SidePanel(QWidget):
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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._rail)
        layout.addWidget(self._stack)

        self._buttons: dict[str, IconButton] = {}
        self._panels: dict[str, QWidget] = {}

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

    def hide_panel(self) -> None:
        self._stack.hide()
        for button in self._buttons.values():
            button.setChecked(False)

    def toggle(self, key: str | None = None) -> None:
        key = key or next(iter(self._panels))
        if self.current == key:
            self.hide_panel()
        else:
            self.show_panel(key)
