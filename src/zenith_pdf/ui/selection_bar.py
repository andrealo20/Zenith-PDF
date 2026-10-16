"""The bar that appears over selected annotations, with what can be done to them.

Icons only, so it stays small over the page: how many are selected (from two
on), Style (the same editor as the tools' menus), Duplicate, Arrange, Lock, More
and Delete. Each button says what it does when pointed at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton

from ..core.annotations import TextMarkup
from ..icons import icon
from ..theme import themes
from . import clipboard
from .help import explain_menu
from .tool_menu import SelectionMenu, selection_options
from .widgets import FloatingBar, IconButton, separator

if TYPE_CHECKING:
    from .workspace import Workspace


class SelectionBar(FloatingBar):
    def __init__(self, workspace: Workspace, parent=None):
        super().__init__(parent)
        self._workspace = workspace
        view = workspace.view
        self._style_menu: SelectionMenu | None = None

        self.count = QLabel()
        self.count.setObjectName("SelectionCount")
        self.count.setToolTip("Annotations selected")
        self.count_line = separator()
        self.style_button = self._button("palette", "Style: colour, thickness, line style", self.open_style,
                                         help="sel_style")
        self.duplicate = self._button("copy-simple", "Duplicate", view.duplicate_selected, help="sel_duplicate")
        self.arrange = self._button("stack", "Arrange", None, help="sel_arrange")
        self.arrange.setMenu(self._menu([
            ("arrow-line-up", "Bring to front", lambda: view.arrange("front")),
            ("caret-up", "Bring forward", lambda: view.arrange("forward")),
            ("caret-down", "Send backward", lambda: view.arrange("backward")),
            ("arrow-line-down", "Send to back", lambda: view.arrange("back")),
        ]))
        self.lock = self._button("lock-simple", "Lock", view.toggle_lock, help="sel_lock")
        self.lock.setText("Lock")  # the icon shows; the text names it for the tooltip and screen readers
        self.more = self._button("dots-three", "More", None, help="sel_more")
        self._more = self._menu([
            ("sliders-horizontal", "Properties…", workspace.edit_properties),
            ("stack-simple", "Flatten", workspace.flatten_selected),
            ("books", "Add to library", workspace.add_to_library),
            ("files", "Apply to pages", workspace.apply_to_pages),
            None,
            ("copy", "Copy\tCtrl+C", view.copy_annotations),
            ("scissors", "Cut\tCtrl+X", view.cut_annotations),
            ("image", "Copy as image", view.copy_selection_as_image),
            ("clipboard-text", "Paste\tCtrl+V", lambda: view.paste_annotations()),
            None,
            ("selection-all", "Select all on this page", lambda: view.select_all_annotations(self._page())),
            ("x", "Deselect\tEsc", view.scene().clearSelection),
        ])
        self.more.setMenu(self._more)
        # What the clipboard holds can change with the menu's own Copy, while the selection stays the same.
        self._more.aboutToShow.connect(self._sync_paste)
        self.delete = self._button("trash", "Delete (Del)", view.delete_selected, help="sel_delete")
        self.delete.setObjectName("DangerButton")
        themes.changed.connect(self._recolour)
        self._recolour()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        for widget in (self.count, self.count_line, self.style_button, self.duplicate, self.arrange, self.lock,
                       self.more, separator(), self.delete):
            layout.addWidget(widget)

    def _button(self, icon_name: str, tip: str, slot, help: str | None = None) -> IconButton:
        button = IconButton(icon_name, tip, icon_size=18, help=help)
        button.setFixedSize(36, 36)
        if slot is None:
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        else:
            button.clicked.connect(slot)
        return button

    def _menu(self, entries) -> QMenu:
        menu = QMenu(self)
        for entry in entries:
            if entry is None:
                menu.addSeparator()
            else:
                icon_name, text, slot = entry
                menu.addAction(icon(icon_name), text, slot)
        return explain_menu(menu)

    def _recolour(self, *_) -> None:
        self.delete._color = themes.current.danger
        self.delete._refresh()

    def _page(self) -> int:
        selected = self._workspace.view.selected_annotations()
        return selected[0].page if selected else self._workspace.view.current_page

    def _action(self, text: str):
        return next(a for a in self._more.actions() if a.text().split("\t")[0] == text)

    def open_style(self) -> None:
        """The style editor for the selection, beside it if there is room."""
        selected = self._workspace.view.selected_annotations()
        if not selected:
            return
        if self._style_menu is None:
            self._style_menu = SelectionMenu(self._workspace.view, self)
        view = self._workspace.view
        area = view.selection_rect()
        beside = QRect(view.viewport().mapToGlobal(area.topLeft()), area.size()) if area is not None else None
        self._style_menu.show_for(selected, self.style_button, beside)

    @property
    def style_menu(self) -> SelectionMenu | None:
        return self._style_menu

    def sync(self, selected) -> None:
        """Match the buttons to the current selection."""
        read_only = self._workspace.document.read_only
        count = len(selected)
        self.count.setText(str(count))
        self.count.setVisible(count > 1)
        self.count_line.setVisible(count > 1)
        locked = bool(selected) and all(a.locked for a in selected)
        self.lock.setText("Unlock" if locked else "Lock")
        self.lock.setToolTip("Unlock" if locked else "Lock: no moving, resizing or deleting by accident")
        self.lock.set_icon_name("lock-simple-open" if locked else "lock-simple")
        movable = [a for a in selected if not isinstance(a, TextMarkup)]
        self.style_button.setEnabled(not read_only and bool(selection_options(selected)))
        for button in (self.arrange, self.lock):
            button.setEnabled(not read_only)
        self.duplicate.setEnabled(bool(movable) and not read_only)
        for text in ("Add to library", "Apply to pages"):
            self._action(text).setEnabled(bool(movable) and not read_only)
        for text in ("Properties…", "Flatten", "Cut"):
            self._action(text).setEnabled(not read_only)
        self._sync_paste()
        self.delete.setEnabled(not read_only and not locked)
        menu = self._style_menu
        if menu is not None and menu.isVisible():
            menu.refresh(selected)
        self.adjustSize()

    def _sync_paste(self) -> None:
        self._action("Paste").setEnabled(clipboard.has_annotations() and not self._workspace.document.read_only)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._style_menu is not None and self._style_menu.isVisible() and not self._style_menu.pinned:
            self._style_menu.close_menu()
