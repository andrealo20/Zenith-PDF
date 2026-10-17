"""Floating annotation toolbar, and the toolbar's own customising.

The toolbar shows the tools listed in the "toolbar_tools" setting: tools of the
catalog and copies of them ("pen#2"), each copy with its own style. A click on a
tool turns it on. A click on the tool that is on, a right click, or a long press
opens the tool's menu (tool_menu.ToolMenu): its style, its lock, and moving,
duplicating or removing it. Tools are added from the tool chooser (its + buttons,
or by dragging one onto the toolbar) and reordered by dragging along the toolbar.
When the window is too narrow, the last tools move into a menu.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QDrag, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QMenu, QToolButton, QWidget
from shiboken6 import isValid

from ..icons import icon, readable
from ..theme import qcolor as token_color, themes
from .document_view import DocumentView
from .help import attach_help
from .tool_catalog import ALWAYS, BY_KEY, DEFAULT_TOOLBAR, TOOL_MIME, base_of, instance_title, is_valid_key, toolbar_keys
from .tool_menu import ToolMenu
from .tools import LOCKABLE, OPTIONS, ToolSettings
from .widgets import FloatingBar, IconButton, cached_icon, focus_ring, keyboard_focus, separator

SHORTCUTS = {"select": "V", "hand": "H, or hold Space"}
LONG_PRESS_MS = 500
REPLAY_MS = 400  # what follows a press that closed a menu on its own button comes this soon after it
VIEW_TOOLS = {"select", "hand", "lasso", "laser", "snapshot"}  # for documents that can only be viewed


def tool_colour(settings: ToolSettings, key: str) -> str | None:
    """The colour a tool's icon takes: its own, made readable on the bar. None for tools without a colour."""
    if "color" not in OPTIONS.get(base_of(key), ()):
        return None
    t = themes.current
    return readable(settings.style(key).color, t.raised, t.text)


class ToolButton(QToolButton):
    """One tool on the toolbar. The tool that is on is wider, with a caret for its menu and, if locked, a padlock."""

    INACTIVE = QSize(36, 36)
    ACTIVE = QSize(52, 36)
    ICON = 20

    def __init__(self, key: str, toolbar: AnnotationToolbar):
        super().__init__(toolbar)
        self.key = key
        self.spec = BY_KEY[base_of(key)]
        self._toolbar = toolbar
        self.active = False
        self.locked = False
        self.tint: str | None = None
        self._press: QPoint | None = None
        self._long = False
        self._icons: dict[tuple, QIcon] = {}
        self.setCheckable(not self.spec.action)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedSize(self.INACTIVE)
        self.setProperty("tool_key", key)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LONG_PRESS_MS)
        self._timer.timeout.connect(self._long_press)
        attach_help(self, self.spec.help)
        themes.changed.connect(self._theme)

    def nextCheckState(self) -> None:
        pass  # the toolbar says which tool is on: a click must not untick the one that is

    def set_state(self, active: bool, locked: bool, tint: str | None) -> bool:
        """Show the tool on or off, locked or not, in its colour. True if its size changed."""
        resized = active != self.active
        self.active, self.locked, self.tint = active, locked, tint
        self.setChecked(active)
        if resized:
            self.setFixedSize(self.ACTIVE if active else self.INACTIVE)
        self.update()
        return resized

    def _theme(self, *_) -> None:
        self._icons.clear()
        self.update()

    def _glass(self) -> FloatingBar | None:
        """The glass bar this button stands on, if it is glass."""
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, FloatingBar):
            parent = parent.parentWidget()
        return parent if parent is not None and not parent.solid else None

    def _icon(self, name: str, color: str) -> QIcon:
        cached = self._icons.get((name, color))
        if cached is None:
            cached = self._icons[(name, color)] = cached_icon(name, color)  # drawn once per size and screen
        return cached

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = None
        if self.active:
            fill = t.glass_checked
        elif self.isDown():
            fill = t.glass_pressed
        elif self.underMouse() and self.isEnabled():
            fill = t.glass_hover  # the tokens of the other glass bars' buttons
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(token_color(fill))
            painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        color = self.tint or (t.accent if self.active else t.text)
        if self.tint and (bar := self._glass()) is not None:
            # On glass the tool's colour is made readable on what shows through, as on the bar it was made for.
            color = readable(QColor(self.tint).getRgbF()[:3], bar.surface().name(), t.text)
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        self._icon(self.spec.icon, color).paint(painter, QRect(8, 8, self.ICON, self.ICON),
                                                Qt.AlignmentFlag.AlignCenter, mode)
        if self.active:
            self._icon("caret-down-bold", t.accent).paint(painter, QRect(35, 13, 10, 10))
            if self.locked:
                self._icon("z-lock-badge", t.accent).paint(painter, QRect(28, 4, 9, 9))
        if keyboard_focus(self):
            focus_ring(painter, QRectF(self.rect()), 10)

    def keyPressEvent(self, event) -> None:
        if not self._toolbar.key_on(self, event):
            super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            self._long = False
            self._timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._press = None
                self._timer.stop()
                self.setDown(False)
                self._toolbar.start_drag(self.key, self)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._timer.stop()
        self._press = None
        if self._long:  # the long press opened the menu: no click as well
            self._long = False
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _long_press(self) -> None:
        if self.isDown():
            self._long = True
            self.setDown(False)
            self._toolbar.toggle_menu(self.key)

    def contextMenuEvent(self, event) -> None:
        event.accept()
        self._toolbar.toggle_menu(self.key)


class _DropMark(QFrame):
    """Where a dragged tool would land on the toolbar."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedWidth(3)
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(themes.current.accent))
        painter.drawRoundedRect(QRectF(self.rect()), 1.5, 1.5)


class AnnotationToolbar(FloatingBar):
    chooser_requested = Signal()
    action_used = Signal(str)  # an action tool (an image, a bookmark) was used from here
    menu_opened = Signal(str)  # the key of the tool whose menu just opened
    # Whether a pinned tool menu was up when its toolbar last went away (another tab, Home): the toolbar shown next
    # brings up a pinned menu of its own, for its tool. Every tab has its own toolbar and menu; the pin is one.
    pinned_menu_up = False

    def __init__(self, settings: ToolSettings, view: DocumentView, read_only: bool, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._view = view
        self._read_only = read_only
        self._buttons: dict[str, ToolButton] = {}
        self._order: list[str] = []
        self._separators: list[tuple[QFrame, str]] = []  # each with the tool it comes before
        self._hidden: list[str] = []
        self._visiting: str | None = None  # the tool that is on without being on the toolbar (see fit)
        self._max_width: int | None = None
        self._unsaved_keys: list[str] | None = None  # the toolbar's tools when there are no settings to keep them
        self._menu: ToolMenu | None = None
        self._swallow: str | None = None  # the tool whose menu a click on its button just closed
        self._swallow_timer = QTimer(self)
        self._swallow_timer.setSingleShot(True)
        self._swallow_timer.setInterval(REPLAY_MS)
        self._swallow_timer.timeout.connect(self._forget_swallow)
        self._reopen: str | None = None  # a pinned menu to show again with the toolbar
        self._putting_away = False  # the menu closes with the toolbar, not because the user closed it
        self._watched: QWidget | None = None
        self.setAcceptDrops(True)
        self._mark = _DropMark(self)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(8, 4, 8, 4)
        self._row.setSpacing(4)
        self.overflow = IconButton("caret-double-right", "More tools")
        self.overflow.setFixedSize(36, 36)
        self.overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.overflow.setMenu(QMenu(self.overflow))
        self.add_button = IconButton("plus-circle", "Add tools", help="add_tools")
        self.add_button.setFixedSize(36, 36)
        self.add_button.clicked.connect(self.chooser_requested.emit)
        for button in (self.overflow, self.add_button):
            button.installEventFilter(self)  # the arrow keys and Enter, as on the tools (see key_on)
        self.rebuild()

        # Bound methods, not lambdas: Qt drops these connections when the toolbar is
        # destroyed, while a lambda on these long-lived signals would outlive it.
        settings.tool_changed.connect(self._on_tool_changed)
        settings.style_changed.connect(self._sync)
        settings.instances_changed.connect(self._sync)
        themes.changed.connect(self._sync)
        if view.settings is not None:
            view.settings.changed.connect(self._on_setting)

    @property
    def tool_menu(self) -> ToolMenu | None:
        """The tools' menu, once it has been opened."""
        return self._menu

    # Building ------------------------------------------------------------

    def keys(self) -> list[str]:
        if self._view.settings is None:
            return toolbar_keys(self._unsaved_keys or [])
        return toolbar_keys(self._view.settings.get("toolbar_tools"))

    def _store(self, keys: list[str]) -> None:
        if self._view.settings is not None:
            self._view.settings.set("toolbar_tools", keys)  # every open toolbar rebuilds itself
        else:
            self._unsaved_keys = keys
            self.rebuild()

    def _on_setting(self, key: str) -> None:
        if key == "toolbar_tools":
            self.rebuild()

    def rebuild(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self.overflow, self.add_button):
                widget.hide()  # off screen now: deleteLater only acts once the event loop gets to it
                widget.setParent(None)
                widget.deleteLater()
        self._buttons.clear()
        self._separators = []
        self._order = self.keys()
        previous_group = None
        for key in self._order:
            spec = BY_KEY[base_of(key)]
            if previous_group is not None and spec.group != previous_group:
                line = separator()
                self._row.addWidget(line)
                self._separators.append((line, key))
            previous_group = spec.group
            button = ToolButton(key, self)
            button.clicked.connect(lambda _=False, k=key: self._clicked(k))
            if self._read_only and spec.key not in VIEW_TOOLS:
                button.setEnabled(False)
            self._row.addWidget(button)
            self._buttons[key] = button
        for widget in (self.overflow, self.add_button):
            self._row.addWidget(widget)
        self._sync(relayout=False)
        self._relayout()

    def _details(self, key: str) -> list[str]:
        """What a tool's tooltip adds to its name: its lock, and its subject if it is the user's."""
        base = base_of(key)
        if self._read_only and base not in VIEW_TOOLS:
            return ["this document can only be viewed"]
        parts = []
        if base in LOCKABLE:
            parts.append("locked" if self._settings.is_locked(key) else "unlocked")
        subject = self._settings.subject(key)
        if subject != BY_KEY[base].title:
            parts.append(f"subject “{subject}”")
        return parts

    def _tip(self, key: str) -> str:
        """What pointing at a tool says: its name and shortcut, its lock, and its subject if it is the user's."""
        tip = instance_title(key) + (f" ({SHORTCUTS[key]})" if key in SHORTCUTS else "")
        if self._read_only and base_of(key) not in VIEW_TOOLS:
            return f"{tip}: this document can only be viewed"
        return ", ".join([tip] + self._details(key))

    def use(self, key: str) -> None:
        """What choosing a tool does: switch to it, or for an action tool, do it now."""
        spec = BY_KEY[base_of(key)]
        if spec.action:
            self._view.run_action(spec.key)
            self._sync()
            self.action_used.emit(spec.key)
        else:
            self._settings.set_tool(key)

    def _clicked(self, key: str) -> None:
        if BY_KEY[base_of(key)].action or self._settings.tool != key:
            self.use(key)
        else:
            self.toggle_menu(key)  # a second click on the tool that is on

    # Width -----------------------------------------------------------------

    def fit(self, max_width: int | None) -> None:
        """Hide the last tools into the overflow menu until the toolbar fits in `max_width` pixels."""
        self._max_width = max_width
        for key, button in self._buttons.items():
            button.show()
        for line, _key in self._separators:
            line.show()
        self._hidden = []
        # A tool turned on from the tool chooser without being added: the overflow button shows it is on.
        active = self._settings.tool
        self._visiting = active if active not in self._order and is_valid_key(active) else None
        self.overflow.setVisible(self._visiting is not None)
        menu = self.overflow.menu()
        menu.clear()
        if self._visiting is not None:
            spec = BY_KEY[base_of(self._visiting)]
            action = menu.addAction(icon(spec.icon, tool_colour(self._settings, self._visiting)),
                                    instance_title(self._visiting), lambda k=self._visiting: self._from_overflow(k))
            action.setCheckable(True)
            action.setChecked(True)
            menu.addAction("Add it to the toolbar", lambda k=self._visiting: self.add_tool(k))
        if max_width is None:
            self._sync_overflow()
            self.adjustSize()
            return
        self._row.invalidate()
        visible = [k for k in self._order]
        while self._row.sizeHint().width() > max_width and len(visible) > 2:
            key = visible.pop()
            self._buttons[key].hide()
            self._hidden.insert(0, key)
            for line, before in self._separators:
                if before == key:
                    line.hide()  # it leads a group that has gone into the menu
            self.overflow.show()
            self._row.invalidate()
        if self._hidden and self._visiting is not None:
            menu.addSeparator()
        for key in self._hidden:
            spec = BY_KEY[base_of(key)]
            action = menu.addAction(icon(spec.icon, tool_colour(self._settings, key)), instance_title(key),
                                    lambda k=key: self._from_overflow(k))
            action.setCheckable(not spec.action)
            action.setChecked(key == self._settings.tool)
        self._sync_overflow()
        self._roving()
        self.adjustSize()

    def _from_overflow(self, key: str) -> None:
        if not BY_KEY[base_of(key)].action and self._settings.tool == key:
            self.open_menu(key)
        else:
            self.use(key)

    def _relayout(self) -> None:
        """Fit again and stay centred where the canvas put the toolbar: the active tool is wider."""
        self.fit(self._max_width)
        parent = self.parentWidget()
        if parent is not None and self._max_width is not None:
            hint = self.sizeHint()
            self.setGeometry((parent.width() - hint.width()) // 2, self.y(), hint.width(), hint.height())
            self.refresh_backdrop()
        if self._menu is not None and self._menu.isVisible():
            self._menu.reposition()

    # State -----------------------------------------------------------------

    def _sync(self, *_, relayout: bool = True) -> None:
        active = self._settings.tool
        resized = False
        for key, button in self._buttons.items():
            on = key == active and not button.spec.action
            locked = on and base_of(key) in LOCKABLE and self._settings.is_locked(key)
            resized = button.set_state(on, locked, tool_colour(self._settings, key)) or resized
            button.setToolTip(self._tip(key))
            detail = ", ".join(self._details(key))  # the help card has the name already
            button.setProperty("help_detail", detail[:1].upper() + detail[1:])
        self._sync_overflow()
        if resized and relayout:
            self._relayout()
        self._roving()

    # Keyboard ---------------------------------------------------------------
    #
    # The toolbar is one Tab stop, and F6 goes to it: the tool that is on takes the focus. The arrow keys, Home and
    # End move along the tools, then More tools and Add tools. Enter or Space turns a tool on, or opens the menu of
    # the one that is on; the Menu key (or Shift+F10) opens any tool's menu. Esc goes back to the pages.

    def _stops(self) -> list[QWidget]:
        stops: list[QWidget] = [self._buttons[k] for k in self._order
                                if k not in self._hidden and self._buttons[k].isEnabled()]
        return stops + [w for w in (self.overflow, self.add_button) if not w.isHidden()]

    def _roving(self) -> None:
        """Only one button takes Tab: the one with the focus, else the tool that is on, else the first."""
        stops = self._stops()
        focused = next((w for w in stops if w.hasFocus()), None)
        active = self._buttons.get(self._settings.tool)
        target = focused or (active if active in stops else (stops[0] if stops else None))
        for widget in [*self._buttons.values(), self.overflow, self.add_button]:
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus if widget is target else Qt.FocusPolicy.NoFocus)

    def focus_tools(self) -> None:
        """F6: the keyboard goes to the toolbar, on the tool that is on."""
        self._roving()
        target = next((w for w in self._stops() if w.focusPolicy() != Qt.FocusPolicy.NoFocus), None)
        if target is not None:
            self.window().setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, True)  # its ring shows
            target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def key_on(self, widget: QWidget, event) -> bool:
        """A key pressed on one of the toolbar's buttons. False if it is not one the toolbar answers."""
        stops = self._stops()
        if widget not in stops:
            return False
        key, index = event.key(), stops.index(widget)
        moves = {Qt.Key.Key_Left: index - 1, Qt.Key.Key_Right: index + 1, Qt.Key.Key_Home: 0,
                 Qt.Key.Key_End: len(stops) - 1}
        if key in moves:
            target = stops[max(0, min(moves[key], len(stops) - 1))]
            for other in stops:
                other.setFocusPolicy(Qt.FocusPolicy.TabFocus if other is target else Qt.FocusPolicy.NoFocus)
            target.setFocus(Qt.FocusReason.TabFocusReason)
            return True
        menu_key = key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and
                                              event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        pressed = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
        if key == Qt.Key.Key_Escape:
            self._view.setFocus()
        elif isinstance(widget, ToolButton) and (pressed or menu_key):
            if menu_key or (not widget.spec.action and self._settings.tool == widget.key):
                self.toggle_menu(widget.key)
            else:
                self.use(widget.key)
        elif pressed and widget is self.overflow:
            self.overflow.showMenu()
        elif pressed:
            widget.click()
        else:
            return False
        return True

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.overflow or obj is self.add_button) and event.type() == QEvent.Type.KeyPress:
            return self.key_on(obj, event)
        if obj is self._watched and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            menu = self._menu
            if menu is not None and menu.isVisible() and menu.pinned:
                menu.reposition()
        return False

    def _sync_overflow(self) -> None:
        active = self._settings.tool
        if active in self._hidden or active == self._visiting:
            self.overflow.setIcon(icon(BY_KEY[base_of(active)].icon, themes.current.accent))
        else:
            self.overflow.set_icon_name("caret-double-right")

    def _on_tool_changed(self, key: str) -> None:
        self._sync()
        if key not in self._order or self._visiting is not None:
            self._relayout()  # a tool from the chooser comes, or goes, in the overflow button
        if self._reopen is not None and not self._settings.handing_over:
            self._reopen = key  # away with the toolbar (reading mode), it follows the tools turned on meanwhile
        menu = self._menu
        if menu is not None and menu.isVisible() and menu.key != key:
            if menu.pinned:
                # A pinned menu follows the tool the user turns on. It stays with an unlocked tool that
                # just went back to Pan or Select by itself, to style the next one.
                if not self._settings.handing_over:
                    menu.show_for(key, self.anchor_for(key))
            else:
                menu.close_menu()  # another tool is on: this menu was about the one before

    # The tools' menu -------------------------------------------------------

    def anchor_for(self, key: str) -> QWidget:
        """What a tool's menu hangs under: its button, or the overflow button, or the toolbar."""
        button = self._buttons.get(key)
        if button is not None and button.isVisible():
            return button
        if (key in self._hidden or key == self._visiting) and self.overflow.isVisible():
            return self.overflow
        return self

    def open_menu(self, key: str) -> None:
        if not is_valid_key(key):
            return
        if self._menu is None:
            self._menu = ToolMenu(self._settings, toolbar=self, prefs=self._view.settings, parent=self)
            self._menu.anchor_for = self.anchor_for
            self._menu.closed.connect(self._menu_closed)
        window = self.window()
        if window is not self._watched and window is not self:
            if self._watched is not None and isValid(self._watched):
                self._watched.removeEventFilter(self)
            window.installEventFilter(self)
            self._watched = window
        self._swallow = None
        self._menu.show_for(key, self.anchor_for(key))
        if self._menu.pinned:
            AnnotationToolbar.pinned_menu_up = True
        self.menu_opened.emit(key)

    def toggle_menu(self, key: str) -> None:
        """Open the tool's menu, or close it if it is open: a second click, the caret, a right click, a long press."""
        menu = self._menu
        if menu is not None and menu.isVisible() and menu.key == key:
            menu.close_menu()
            return
        if self._swallow == key:  # what follows the press that closed this menu on its own button
            self._swallow = None
            return
        self.open_menu(key)

    def _menu_closed(self) -> None:
        """A press on the button of the open menu closes it; what that press does next must not open it again.

        The menu keeps the press itself from going on to the button (see Popover.mousePressEvent).
        A right press still brings a context menu event when the button is let go, and where
        a platform gives the press to the button anyway, its click follows: both are swallowed
        if they come within REPLAY_MS.
        """
        menu = self._menu
        if menu is None or not isValid(self):
            return
        if not self._putting_away:
            AnnotationToolbar.pinned_menu_up = False  # closed by the user: other tabs don't bring it up either
        if menu.closed_by_anchor:
            follows = menu.closing_button == Qt.MouseButton.RightButton
        else:
            anchor = menu.anchor_widget()
            follows = (anchor is not None and QApplication.mouseButtons() != Qt.MouseButton.NoButton
                       and anchor.rect().contains(anchor.mapFromGlobal(QCursor.pos())))
        if follows:
            self._swallow = menu.key
            self._swallow_timer.start()

    def _forget_swallow(self) -> None:
        self._swallow = None

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.put_menu_away()

    def put_menu_away(self) -> None:
        """Close the tools' menu along with the toolbar: a pinned one opens again when the toolbar shows."""
        menu = self._menu
        if menu is not None and menu.isVisible():
            self._reopen = menu.key if menu.pinned else None
            self._putting_away = True
            try:
                menu.close_menu()
            finally:
                self._putting_away = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._reopen is not None or self._pin_wanted():
            QTimer.singleShot(0, self._show_again)

    def _pin_wanted(self) -> bool:
        """A pinned menu was up on another tab's toolbar, and this one has none up yet."""
        prefs = self._view.settings
        return (AnnotationToolbar.pinned_menu_up and prefs is not None and bool(prefs.get("tool_menu_pinned"))
                and not (self._menu is not None and self._menu.isVisible()))

    def _show_again(self) -> None:
        key, self._reopen = self._reopen, None
        if not self.isVisible() or not isValid(self):
            return
        if key is not None and self._menu is not None and self._menu.pinned:
            self.open_menu(key)
        elif self._pin_wanted() and is_valid_key(self._settings.tool):
            self.open_menu(self._settings.tool)  # the tool on now: a pinned menu follows the tools

    # Customising -----------------------------------------------------------

    def add_tool(self, key: str, position: int | None = None) -> None:
        keys = [k for k in self._order if k != key]
        keys.insert(len(keys) if position is None else max(0, min(position, len(keys))), key)
        self._store(keys)

    def remove_tool(self, key: str) -> None:
        self.remove_tools([key])

    def remove_tools(self, keys) -> None:
        """Take tools off the toolbar. Copies ("pen#2") are forgotten with their style."""
        gone = [k for k in keys if k not in ALWAYS and k in self._order]
        if not gone:
            return
        self._store([k for k in self._order if k not in gone])
        for key in gone:
            if "#" in key:
                self._settings.forget(key)
        if self._settings.tool in gone:
            self._settings.set_tool("select")  # nothing on the toolbar would show it is on

    def move_tool(self, key: str, position: int) -> None:
        self.add_tool(key, position)

    def has_tool(self, key: str) -> bool:
        """True if the tool is on the toolbar; for a catalog tool, any copy of it counts."""
        if "#" in key:
            return key in self._order
        return any(base_of(k) == key for k in self._order)

    def duplicate_tool(self, key: str) -> str:
        """A copy of the tool, with its style, right after it on the toolbar; it is turned on and its menu opens."""
        new = self._settings.duplicate(key, taken=self.keys())
        keys = list(self._order)
        keys.insert(keys.index(key) + 1 if key in keys else len(keys), new)
        self._store(keys)
        self._settings.set_tool(new)
        self.open_menu(new)
        return new

    def reset(self) -> None:
        """The toolbar the app starts with. Copies on it are forgotten."""
        copies = [k for k in self._order if "#" in k]
        self._store(list(DEFAULT_TOOLBAR))
        for key in copies:
            self._settings.forget(key)
        if self._settings.tool not in DEFAULT_TOOLBAR:
            self._settings.set_tool("select")  # it left the toolbar

    # Dragging tools on, and along, the toolbar -------------------------------

    def start_drag(self, key: str, button: QWidget) -> None:
        drag = QDrag(button)
        mime = QMimeData()
        mime.setData(TOOL_MIME, QByteArray(key.encode("ascii")))
        drag.setMimeData(mime)
        drag.setPixmap(button.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def _drop_position(self, x: float) -> int:
        """Where among the visible tool buttons a drop at `x` goes."""
        visible = [k for k in self._order if k not in self._hidden]
        for key in visible:
            button = self._buttons[key]
            if x < button.geometry().center().x():
                return self._order.index(key)
        return len(visible)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(TOOL_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat(TOOL_MIME):
            return
        event.acceptProposedAction()
        position = self._drop_position(event.position().x())
        visible = [k for k in self._order if k not in self._hidden]
        if position < len(visible):
            x = self._buttons[visible[position]].geometry().left() - 3
        else:
            last = self._buttons[visible[-1]] if visible else self.add_button
            x = last.geometry().right() + 1
        self._mark.setGeometry(x, 8, 3, self.height() - 16)
        self._mark.show()
        self._mark.raise_()

    def dragLeaveEvent(self, event) -> None:
        self._mark.hide()

    def dropEvent(self, event) -> None:
        self._mark.hide()
        key = bytes(event.mimeData().data(TOOL_MIME)).decode("ascii", "replace")
        if not is_valid_key(key):
            return
        position = self._drop_position(event.position().x())
        if key in self._order and self._order.index(key) < position:
            position -= 1  # it leaves its old place first
        self.add_tool(key, position)
        event.acceptProposedAction()
