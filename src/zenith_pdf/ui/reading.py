"""Reading mode: only the pages show, on the canvas.

The floating bars fade away, and the side panel, the rail and the tab strip go,
so the pages have the whole window (and the whole screen, if the setting says
so). The pages answer as to Pan: a drag moves them, a click follows a link, the
wheel, the zoom keys, Page Up and Down, Home and End, Back and Forward work as
always. Nothing is lost: leaving puts back the panel, the bars, the tool, the
selection and the window as they were, with the line being read where it was.

It is a state of the window, not of a tab: another tab shows in reading mode
too, and Home or the Doc Builder end it. Esc, Ctrl+H again, or the small chip
that shows when the pointer reaches the top edge, bring everything back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget
from shiboken6 import isValid

from .help import close_help_card
from .widgets import Fade, FloatingBar, IconButton

if TYPE_CHECKING:
    from .main_window import MainWindow
    from .workspace import Workspace

TOP_EDGE = 8  # px from the top of the pages where the pointer calls the exit chip
NEAR = 40  # px below the chip where moving the pointer keeps it showing
CHIP_MS = 1500  # the chip fades this long after the pointer last moved by it
SHORTCUT = "Ctrl+H"


class ExitChip(FloatingBar):
    """The small chip at the top of the pages that leaves reading mode."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button = IconButton("z-reading", f"Leave reading mode (Esc or {SHORTCUT})", icon_size=18,
                                 text=" Exit reading mode", help="reading_mode")  # a space after the icon
        self.button.clicked.connect(self.clicked)
        key = QLabel("Esc")
        key.setObjectName("HelpKeys")  # the key as the help cards show keys
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(6)
        layout.addWidget(self.button)
        layout.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)  # as tall as its text, not the chip
        self.fade = Fade(self)
        self.hide()


class ReadingMode(QObject):
    """Reading mode for one window. `changed` says when it begins (True) and ends (False)."""

    changed = Signal(bool)

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self._window = window
        self.active = False
        self._workspace: Workspace | None = None  # the tab shown in reading mode
        self._entry = None  # where reading began: a point of the pages and its place on the screen
        self._window_state = None  # the window's state before reading mode made it full screen
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._chip_away)

    @property
    def workspace(self) -> Workspace | None:
        return self._workspace

    def toggle(self) -> None:
        self.leave() if self.active else self.enter()

    def enter(self) -> bool:
        """Show only the pages of the current tab. False when there are none to show (Home, the Doc Builder)."""
        window = self._window
        workspace = window.current_workspace()
        if self.active or workspace is None:
            return False
        close_help_card()  # a card of a button that is going away
        self.active = True
        workspace.close_page_manager_for_reading()  # first: the page picked there is the one read
        self._entry = workspace.view.anchor()
        self._show(workspace)
        window.top_bar.hide()
        if window.settings.get("reading_full_screen") and not window.isFullScreen():
            self._window_state = window.windowState()
            window.set_full_screen(True)
        workspace.view.hold(self._entry)  # the panel and the tabs go: the line read stays where it is
        workspace.view.setFocus()
        workspace.notify(f"Reading mode. Esc or {SHORTCUT} brings the bars back.")
        self.changed.emit(True)
        return True

    def leave(self) -> None:
        """Bring back everything reading mode hid, as it was."""
        if not self.active:
            return
        self.active = False
        window = self._window
        workspace = self._workspace
        anchor = None
        if workspace is not None and isValid(workspace):
            # Unmoved since reading began: back exactly where it began. Else the line read now stays in place.
            view = workspace.view
            anchor = self._entry if view.still_at(self._entry) else view.anchor()
            self._hide(workspace)
        self._workspace = None
        window.top_bar.show()
        if self._window_state is not None and window.isFullScreen():
            window.set_full_screen(False, self._window_state)
        self._window_state = None
        if anchor is not None:
            workspace.view.hold(anchor)
            if workspace.page_manager is not None:  # reading began there, and it is back
                workspace.page_manager.list.setFocus()
            else:
                workspace.view.setFocus()
        self.changed.emit(False)

    def fill_screen(self, on: bool) -> None:
        """The setting changed while reading: fill the screen now, or give it back as it was."""
        window = self._window
        if not self.active:
            return
        if on and not window.isFullScreen():
            self._window_state = window.windowState()
            window.set_full_screen(True)
        elif not on and self._window_state is not None:
            if window.isFullScreen():
                window.set_full_screen(False, self._window_state)
            self._window_state = None

    def follow(self, widget) -> None:
        """Another tab shows: it is read the same way. Home and the Doc Builder end reading mode."""
        from .workspace import Workspace

        if not self.active or widget is self._workspace:
            return
        if not isinstance(widget, Workspace):
            self.leave()
            return
        if self._workspace is not None and isValid(self._workspace):
            self._hide(self._workspace)
        self._show(widget)
        self._entry = widget.view.anchor()
        widget.view.setFocus()

    def forget(self, workspace) -> None:
        """A tab is closing: let go of it, so nothing waits on its widgets."""
        if workspace is self._workspace:
            self._unwatch(workspace)
            self._workspace = None

    # One tab -------------------------------------------------------------

    def _show(self, workspace: Workspace) -> None:
        self._workspace = workspace
        workspace.set_reading(True)
        workspace.view.viewport().installEventFilter(self)
        workspace.exit_chip().installEventFilter(self)

    def _hide(self, workspace: Workspace) -> None:
        self._unwatch(workspace)
        workspace.set_reading(False)

    def _unwatch(self, workspace: Workspace) -> None:
        self._timer.stop()
        if not isValid(workspace):
            return
        workspace.view.viewport().removeEventFilter(self)
        chip = workspace.canvas.exit_chip
        if chip is not None and isValid(chip):
            chip.removeEventFilter(self)
            chip.fade.stop()
            chip.hide()

    # The exit chip -------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:
        workspace = self._workspace
        if not self.active or workspace is None or not isValid(workspace):
            return False
        kind = event.type()
        chip = workspace.exit_chip()
        if kind == QEvent.Type.MouseMove and watched is workspace.view.viewport():
            self.pointer_at(watched.mapTo(workspace.canvas, event.position().toPoint()))
        elif watched is chip and kind == QEvent.Type.Enter:
            self._timer.stop()  # the pointer is on it: it stays
        elif watched is chip and kind == QEvent.Type.Leave:
            self._timer.start(CHIP_MS)
        return False

    def pointer_at(self, point: QPoint) -> None:
        """The pointer moved to `point` on the canvas: at the top edge the chip shows; moving by it keeps it."""
        chip = self._workspace.exit_chip()
        if point.y() <= TOP_EDGE:
            self._workspace.canvas.place_exit_chip()
            chip.fade.fade_in()
            chip.raise_()
            self._timer.start(CHIP_MS)
        elif chip.fade.showing and point.y() <= chip.geometry().bottom() + NEAR:
            self._timer.start(CHIP_MS)

    def _chip_away(self) -> None:
        workspace = self._workspace
        if workspace is not None and isValid(workspace):
            chip = workspace.exit_chip()
            if not chip.underMouse():
                chip.fade.fade_out()
