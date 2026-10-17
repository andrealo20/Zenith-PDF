"""Top-level window: tab strip, home page and one workspace per open document."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QProgressDialog,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .. import __version__
from ..core import convert
from ..core.document import Document, PdfiumError
from ..core.i18n import tr
from ..core.platforms import key as platform_key
from ..core.releases import RELEASES, since
from ..core.render import RenderPool
from ..icons import icon, style as icon_style
from ..settings import Settings
from ..theme import THEME_NAMES, themes
from ..core import pages
from . import messages
from .assistant import AccentButton, AssistantPanel
from .dialogs import NewDocumentDialog, delete_template, edit_template, page_size
from .doc_builder import DocBuilder
from .help import badges, close_help_card, explain_menu
from .instance import own_command, parse_target
from .library import Library
from .new_file import NewFilePanel
from . import pdf_tools
from . import tool_menu
from .pdf_tools import ToolsPanel
from .home import HomePage
from .reading import SHORTCUT as READING_SHORTCUT
from .reading import ReadingMode
from .settings_page import SettingsWindow
from .tab_drag import TabDragger
from .tools import ToolSettings
from .widgets import IconButton, set_glass, set_glass_adaptive
from .workspace import Workspace

APP_NAME = "Zenith PDF"
PRINT_DPI = 300  # enough for print; more only makes huge spool files
MIN_CONTENT_WIDTH = 720  # pixels: the window can be snapped to half of a 1280 pixel wide screen
REMEMBER_STYLES_MS = 300  # tool styles are written this long after the last change: a slider sends many


DEFAULT_APP_QUESTION = {
    "win32": "Double-clicking a PDF would open it here. Windows shows its list of apps: pick Zenith PDF, then "
             "Always. Settings > Files & Saving can do it later too.",
    "": "Double-clicking a PDF would open it here. Settings > Files & Saving can do it later too.",
}
DEFAULT_APP_AGAIN = 10  # starts before "Later" asks again
DEFAULT_APP_TIMES = 3  # questions at most, "Later" included
DEFAULT_APP_BY_HAND = {
    "darwin": "Choose it in the Finder: select a PDF, File > Get Info, pick Zenith PDF under Open with, then "
              "Change All.",
    "": "Choose it in your file manager: right-click a PDF, Open With, and make Zenith PDF the default there.",
}


class MainWindow(QMainWindow):
    open_windows: list[MainWindow] = []  # every window of the app that is open, the oldest first
    updater = None  # the app's Updater (ui/updates.py), made by main() or the first check by hand
    indexer = None  # the app's Indexer (ui/library_search.py): the text of every PDF opened, made by main()

    def __init__(self, renderer: RenderPool, settings: Settings, tool_settings: ToolSettings | None = None,
                 library: Library | None = None):
        """The first window makes the tool settings and the markup library; windows opened from it share them,
        so a tool styled in one draws the same in every window."""
        super().__init__()
        self._renderer = renderer
        self._settings = settings
        first = tool_settings is None
        self.tool_settings = tool_settings or ToolSettings(self)
        self._default_author = self.tool_settings.author
        if first and settings.get("remember_styles"):
            self.tool_settings.load_styles(settings.get("tool_styles"))
        self._styles_timer = QTimer(self)
        self._styles_timer.setSingleShot(True)
        self._styles_timer.setInterval(REMEMBER_STYLES_MS)
        self._styles_timer.timeout.connect(self._remember_styles)
        self.tool_settings.style_changed.connect(lambda *_: self._styles_timer.start())
        self.tool_settings.instances_changed.connect(lambda: self._styles_timer.start())
        self._settings_window: SettingsWindow | None = None
        self._before_full_screen = Qt.WindowState.WindowNoState  # the state F11 goes back to
        self.reading_mode = ReadingMode(self)
        self._autosave = QTimer(self)
        self._autosave.timeout.connect(self.autosave)
        self.library = library or Library(settings, self)
        self.workspaces: list[Workspace] = []
        self._links: dict[QWidget, list] = {}  # each tab's (signal, slot) to this window, undone when it moves away
        self.setWindowTitle(tr(APP_NAME))
        self.setAcceptDrops(True)
        self.resize(1320, 860)
        if first and settings.window_geometry is not None:
            self.restoreGeometry(settings.window_geometry)

        self.home_button = IconButton("house", "Home", checkable=True, help="home")
        self.home_button.clicked.connect(self.show_home)
        self.tabs = QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.currentChanged.connect(self._show_tab)
        self.tabs.tabBarClicked.connect(self._show_tab)
        self.tabs.tabMoved.connect(self._on_tab_moved)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)
        self._dragger = TabDragger(self)  # a tab pulled out of the strip opens in a window of its own
        self.open_button = open_button = IconButton("plus", "Open, new document, templates, Doc Builder", help="new")
        open_button.clicked.connect(self.show_new_file_panel)
        self.menu_button = IconButton("dots-three", "More", help="more")
        self.menu_button.setPopupMode(IconButton.ToolButtonPopupMode.InstantPopup)
        self.menu_button.setMenu(self._build_menu())
        self.tools_button = IconButton("wrench", "PDF tools", help="tools")
        self.tools_button.clicked.connect(self.show_tools_panel)
        self._assistant: AssistantPanel | None = None
        self.assistant_button = AccentButton("sparkle", "Assistant (Ctrl+K)", help="assistant")
        self.assistant_button.clicked.connect(self.show_assistant)
        # Document commands on the right of the tab strip, enabled while a document is shown.
        self.document_buttons = []
        right = [self.assistant_button, self.tools_button]
        for icon_name, tip, slot, help in (
            ("floppy-disk", "Save (Ctrl+S)", self.save_current, "save"),
            ("export", "Save as (Ctrl+Shift+S)", lambda: self.save_current(save_as=True), "save_as"),
            ("printer", "Print (Ctrl+P)", self.print_current, "print"),
        ):
            button = IconButton(icon_name, tip, help=help)
            button.clicked.connect(slot)
            self.document_buttons.append(button)
            right.append(button)
        self.full_screen_button = IconButton("corners-out", "Full screen (F11)", help="full_screen")
        self.full_screen_button.clicked.connect(self._toggle_full_screen)
        right += [self.full_screen_button, self.menu_button]

        self.top_bar = top = QFrame()
        top.setObjectName("TopBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(8, 0, 8, 0)
        top_layout.setSpacing(4)
        top_layout.addWidget(self.home_button, 0, Qt.AlignmentFlag.AlignVCenter)
        top_layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignBottom)
        top_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
        top_layout.addStretch()
        for button in right:
            top_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.home = HomePage(settings)
        self.home.open_requested.connect(self.open_dialog)
        self.home.file_requested.connect(self.open_file)
        self.home.page_requested.connect(self.open_at_page)
        if MainWindow.indexer is not None:
            self.home.set_indexer(MainWindow.indexer)
        self.home.new_requested.connect(self.new_document_dialog)
        self.home.builder_requested.connect(self.open_builder)
        self.home.tool_requested.connect(self.run_tool_from_home)
        tool_menu.colour_picker = self.pick_colour
        from .annotation_items import Handle

        Handle.touch = bool(self._settings.get("touch_mode"))
        self._settings.changed.connect(
            lambda key: setattr(Handle, "touch", bool(self._settings.get("touch_mode"))) if key == "touch_mode" else None)
        self.home.compare_requested.connect(self.command_compare)
        self.home.images_requested.connect(lambda: pdf_tools.images_to_pdf(self))
        self.home.guide_requested.connect(self.open_guide)
        self.home.settings_requested.connect(self.open_settings)
        self.home.pin_requested.connect(self.pin_folder)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(MIN_CONTENT_WIDTH)  # its pages' own minimums would add up to far more
        self.stack.addWidget(self.home)

        central = QWidget()
        central.setObjectName("Central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(top)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._add_shortcuts()
        settings.changed.connect(self._on_setting_changed)
        for key in ("author", "after_drawing", "select_new", "autosave_minutes"):
            self._on_setting_changed(key)
        self.show_home()
        MainWindow.open_windows.append(self)
        self.update_card = None
        if MainWindow.updater is not None:
            self._attach_update_card()

    # Menu and shortcuts -------------------------------------------------

    MENU = (  # icon, text (with the shortcut the window owns, after "\t"), what it does; "theme" is the submenu
        ("folder-open", "Open…\tCtrl+O", "open_dialog"),
        ("floppy-disk", "Save\tCtrl+S", "save_current"),
        ("export", "Save as…\tCtrl+Shift+S", "save_as"),
        ("printer", "Print…\tCtrl+P", "print_current"),
        ("x", "Close tab\tCtrl+W", "close_current_tab"),
        ("app-window", "New window\tCtrl+Shift+N", "new_window"),
        None,  # View
        ("z-reading", f"Reading mode\t{READING_SHORTCUT}", "toggle_reading_mode"),
        ("speaker-high", "Read aloud", "command_read_aloud"),
        ("git-diff", "Compare with another version…", "command_compare"),
        ("corners-out", "Full screen\tF11", "toggle_full_screen"),
        "theme",
        None,
        ("sparkle", "Assistant…\tCtrl+K", "show_assistant"),
        ("gear-six", "Settings…", "open_settings"),
        ("graduation-cap", "Guide\tF1", "open_guide"),
        ("keyboard", "Keyboard shortcuts", "show_shortcuts"),
    )

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        # The window owns the real shortcuts; "\t" only shows the key in the menu.
        # A second action bound to the same key would make both ambiguous and dead.
        slots = {"save_as": lambda: self.save_current(save_as=True), "open_guide": lambda: self.open_guide(""),
                 "open_dialog": lambda: self.open_dialog(), "open_settings": lambda: self.open_settings(),
                 "toggle_reading_mode": self.reading_mode.toggle, "toggle_full_screen": self._toggle_full_screen}
        self._menu_icons: list[tuple[QAction | QMenu, str]] = []
        self._document_actions: dict[str, QAction] = {}  # greyed out where they would do nothing (see _sync_menu)
        for entry in self.MENU:
            if entry is None:
                menu.addSeparator()
            elif entry == "theme":
                self._add_theme_menu(menu)
            else:
                icon_name, text, slot = entry
                action = menu.addAction(tr(text), slots.get(slot) or getattr(self, slot))
                self._menu_icons.append((action, icon_name))
                if slot == "toggle_reading_mode":
                    self.reading_action = action
                    action.setCheckable(True)
                elif slot in ("save_current", "save_as", "print_current", "close_current_tab"):
                    self._document_actions[slot] = action
        menu.aboutToShow.connect(self._sync_menu)
        menu.addSeparator()
        about = menu.addAction(tr("About Zenith PDF"), lambda: self.open_settings("about"))
        self._menu_icons.append((about, "info"))
        self._paint_menu_icons()
        themes.changed.connect(self._paint_menu_icons)
        return explain_menu(menu)

    def _add_theme_menu(self, menu: QMenu) -> None:
        theme_menu = menu.addMenu(tr("Theme"))
        self._menu_icons.append((theme_menu, "circle-half"))
        group = QActionGroup(self)
        for name, label in THEME_NAMES.items():
            action = theme_menu.addAction(tr(label), lambda n=name: self.set_theme(n))
            action.setCheckable(True)
            action.setData(name)
            group.addAction(action)
        theme_menu.aboutToShow.connect(
            lambda: [a.setChecked(a.data() == self._settings.theme) for a in theme_menu.actions()])

    def _sync_menu(self) -> None:
        """Reading mode, saving and printing need a document; Close tab, a tab (the Doc Builder's too)."""
        is_document = self.current_workspace() is not None
        self.reading_action.setEnabled(is_document)
        self.reading_action.setChecked(self.reading_mode.active)
        for slot in ("save_current", "save_as", "print_current"):
            self._document_actions[slot].setEnabled(is_document)
        self._document_actions["close_current_tab"].setEnabled(self.stack.currentWidget() in self.workspaces)

    def _paint_menu_icons(self, *_) -> None:
        """Icons in the main menu, as in the context menus, in the theme's colours."""
        for entry, icon_name in self._menu_icons:
            entry.setIcon(icon(icon_name))

    def _add_shortcuts(self) -> None:
        def ws(method):
            return lambda: (w := self.current_workspace()) and method(w)

        def chrome(method):
            """A command that shows part of what reading mode hides (search, the page box, the side panel)."""
            return ws(lambda w: (self.reading_mode.leave(), method(w)))

        bindings = [
            ("Ctrl+O", self.open_dialog),
            ("Ctrl+S", self.save_current),
            ("Ctrl+Shift+S", lambda: self.save_current(save_as=True)),
            ("Ctrl+W", self.close_current_tab),
            # Typing and drags end first, so an undo never lands in the middle of one.
            ("Ctrl+Z", ws(lambda w: (w.view.end_interaction(), w.undo_stack.undo()))),
            ("Ctrl+Y", ws(lambda w: (w.view.end_interaction(), w.undo_stack.redo()))),
            ("Ctrl+Shift+Z", ws(lambda w: (w.view.end_interaction(), w.undo_stack.redo()))),
            ("Ctrl+C", ws(lambda w: w.view.copy())),
            ("Ctrl+X", ws(lambda w: w.view.cut_annotations())),
            ("Ctrl+V", ws(lambda w: w.view.paste())),
            ("Ctrl+P", self.print_current),
            ("V", lambda: self.tool_settings.set_tool("select")),
            ("H", lambda: self.tool_settings.set_tool("hand")),
            ("F6", chrome(lambda w: w.toolbar.focus_tools())),
            ("Ctrl+Tab", lambda: self._cycle_tabs(1)),
            ("Ctrl+Shift+Tab", lambda: self._cycle_tabs(-1)),
            ("Ctrl+F", chrome(lambda w: w.show_search())),
            ("F3", ws(lambda w: w.search.step(1))),
            ("Shift+F3", ws(lambda w: w.search.step(-1))),
            ("Ctrl+=", ws(lambda w: w.view.zoom_in())),
            ("Ctrl++", ws(lambda w: w.view.zoom_in())),
            ("Ctrl+-", ws(lambda w: w.view.zoom_out())),
            ("Ctrl+1", ws(lambda w: w.view.set_zoom(1.0))),
            ("Ctrl+0", ws(lambda w: w.view.fit_page())),
            ("Ctrl+2", ws(lambda w: w.view.fit_width())),
            ("Ctrl+G", chrome(lambda w: w.page_counter.focus())),
            ("Alt+Left", ws(lambda w: w.view.back())),
            ("Alt+Right", ws(lambda w: w.view.forward())),
            ("F4", chrome(lambda w: w.side.toggle(w.side.current))),
            ("F11", self._toggle_full_screen),
            (READING_SHORTCUT, self.reading_mode.toggle),
            ("F1", lambda: self.open_guide("")),
            ("Ctrl+K", self.toggle_assistant),
            ("Ctrl+Shift+N", self.new_window),
        ]
        for keys, slot in bindings:
            action = QAction(self)
            action.setShortcut(QKeySequence(platform_key(keys)))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
        # Esc leaves reading mode. Only then is the key the window's: otherwise it cancels, deselects and so on.
        self.leave_reading_action = QAction(self)
        self.leave_reading_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self.leave_reading_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.leave_reading_action.setEnabled(False)
        self.leave_reading_action.triggered.connect(self.reading_mode.leave)
        self.addAction(self.leave_reading_action)
        self.reading_mode.changed.connect(self.leave_reading_action.setEnabled)

    # Tabs ---------------------------------------------------------------

    @property
    def renderer(self) -> RenderPool:
        return self._renderer

    @property
    def settings(self) -> Settings:
        return self._settings

    def current_workspace(self) -> Workspace | None:
        widget = self.stack.currentWidget()
        return widget if isinstance(widget, Workspace) else None

    def show_home(self) -> None:
        self.reading_mode.leave()  # Home has no pages to read
        self._close_chooser()
        close_help_card()
        # An empty QTabBar still reserves width; hide it so "+" sits next to Home.
        self.tabs.setVisible(self.tabs.count() > 0)
        self._mark_home(True)
        self.home.refresh()
        self.stack.setCurrentWidget(self.home)
        self.home_button.setChecked(True)
        self.setWindowTitle(tr(APP_NAME))
        for button in self.document_buttons:
            button.setEnabled(False)

    def _mark_home(self, home: bool) -> None:
        """The tab strip's "home" property: while Home shows, no tab is drawn as the current one."""
        if self.tabs.property("home") != home:
            self.tabs.setProperty("home", home)
            self.tabs.style().unpolish(self.tabs)
            self.tabs.style().polish(self.tabs)
            self.tabs.update()

    def _close_chooser(self) -> None:
        """A tool chooser left open belongs to the tab that opened it: it goes when another tab or Home shows."""
        for widget in self.workspaces:
            chooser = getattr(widget, "_chooser", None)
            if chooser is not None:
                chooser.close()

    def _show_tab(self, index: int) -> None:
        if not 0 <= index < len(self.workspaces):
            return
        widget = self.workspaces[index]
        if widget is not self.stack.currentWidget():
            self._close_chooser()
            close_help_card()  # a pinned card points at a button of the tab going away
        self.stack.setCurrentWidget(widget)
        self.reading_mode.follow(widget)  # reading mode belongs to the window: the next tab is read the same way
        self._mark_home(False)
        self.home_button.setChecked(False)
        self._update_titles(widget)
        is_document = isinstance(widget, Workspace)
        for button in self.document_buttons:
            button.setEnabled(is_document)
        if is_document:
            widget.view.setFocus()

    def _update_titles(self, widget) -> None:
        """Tab text, tooltip and window title, with a dot while there are unsaved changes."""
        if widget not in self.workspaces or not isValid(widget):
            return
        if isinstance(widget, Workspace) and not isValid(widget.undo_stack):  # being torn down, as the app quits
            return
        index = self.workspaces.index(widget)
        if isinstance(widget, DocBuilder):
            title, tip, window = "Doc Builder", "Combine PDF files", "Doc Builder"
        else:
            path = widget.document.path
            marker = " •" if widget.dirty else ""
            title, tip, window = path.stem + marker, str(path), f"{path.name}{marker}"
        self.tabs.setTabText(index, title)
        self.tabs.setTabToolTip(index, tip)
        if self.stack.currentWidget() is widget:
            self.setWindowTitle(tr(f"{window} - {APP_NAME}"))

    def _on_tab_moved(self, source: int, target: int) -> None:
        self.workspaces.insert(target, self.workspaces.pop(source))

    def _cycle_tabs(self, delta: int) -> None:
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % self.tabs.count())
            self._show_tab(self.tabs.currentIndex())

    def close_current_tab(self) -> None:
        widget = self.stack.currentWidget()
        if widget in self.workspaces:
            self.close_tab(self.workspaces.index(widget))

    def _confirm_close(self, workspace) -> bool:
        """Offer to save unsaved changes. False means the user cancelled."""
        if not isinstance(workspace, Workspace):
            return True
        if not self._wait_until_idle(workspace):
            return False
        workspace.view.end_interaction()
        if not workspace.dirty:
            return True
        self.tabs.setCurrentIndex(self.workspaces.index(workspace))
        self._show_tab(self.tabs.currentIndex())
        answer = messages.save_discard_cancel(self, f"Save the changes to {workspace.document.path.name}?",
                                              "If you don't save, the changes since the last save are lost.",
                                              icon="floppy-disk")
        if answer == "save":
            return self.save_current()
        return answer == "discard"

    def _wait_until_idle(self, workspace) -> bool:
        """While a background job (a compression) works on the document, wait for it. False if the user would
        rather not wait: the tab then stays open, and the job goes on."""
        if not getattr(workspace, "busy", False):
            return True
        name = workspace.document.path.name
        progress = QProgressDialog(f"Finishing the work on {name}…", "Don't close", 0, 0, self)
        progress.setWindowTitle(tr("Please wait"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        loop = QEventLoop()
        check = QTimer()
        check.setInterval(50)
        check.timeout.connect(lambda: (not workspace.busy or progress.wasCanceled()) and loop.quit())
        check.start()
        progress.show()
        loop.exec()
        check.stop()
        waited = not workspace.busy
        progress.close()
        progress.deleteLater()
        return waited

    def close_tab(self, index: int, confirm: bool = True) -> None:
        if confirm and not self._confirm_close(self.workspaces[index]):
            return
        if not confirm and not self._wait_until_idle(self.workspaces[index]):
            return
        workspace = self.workspaces.pop(index)
        self._links.pop(workspace, None)
        self.reading_mode.forget(workspace)
        if isinstance(workspace, Workspace) and not workspace.document.unsaved:
            self._settings.remember_file_state(workspace.document.path, workspace.view_state())
        self.tabs.removeTab(index)
        self.stack.removeWidget(workspace)
        if isinstance(workspace, DocBuilder):
            workspace.close_builder()
        else:
            workspace.close_document()
        workspace.deleteLater()
        if self.workspaces:
            self._show_tab(self.tabs.currentIndex())
        else:
            self.show_home()

    # Opening files ------------------------------------------------------

    def open_dialog(self, folder: str | None = None) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open", folder or self._settings.last_directory,
            f"{tr('Documents')} ({convert.FILTER});;{tr('PDF documents')} (*.pdf);;{tr('All files')} (*)"
        )
        if paths:
            self._settings.last_directory = str(Path(paths[0]).parent)
        for path in paths:
            self.open_file(path)

    def pick_colour(self):
        """The eyedropper of the colour menus: a colour from the open document's pages, or None."""
        workspace = self.current_workspace()
        return workspace.view.pick_colour() if workspace is not None else None

    def run_tool_from_home(self, key: str) -> None:
        """A tool picked from the home page's list. One that works on a document asks for the PDF first, opens
        it, then goes to work on it, as a tool picked with the document open would."""
        tool = next(t for t in pdf_tools.TOOLS if t.key == key)
        if tool.needs_document:
            path, _ = QFileDialog.getOpenFileName(self, tr(tool.title), self._settings.last_directory,
                                                  tr("PDF documents (*.pdf);;All files (*)"))
            if not path:
                return
            self._settings.last_directory = str(Path(path).parent)
            self.open_file(path)
            workspace = self.current_workspace()
            if workspace is None or workspace.document.path != Path(path).resolve():
                return  # it didn't open, and opening said why
        tool.run(self)

    def open_files(self, paths) -> None:
        for path in paths:
            self.open_file(path)

    def open_targets(self, targets: list[str]) -> None:
        """Open files and zenith-pdf links (a file at a page), as the command line or a later start gives them."""
        for target in targets:
            parsed = parse_target(target)
            if parsed is None:
                continue
            path, page = parsed
            self.open_file(path)
            workspace = self.current_workspace()
            if page is not None and workspace is not None and workspace.document.path == Path(path).resolve():
                # After the file's own last position, which applies once the view has its size.
                QTimer.singleShot(0, lambda view=workspace.view, index=page - 1: view.jump(index))

    def open_file(self, path) -> None:
        path = Path(path).resolve()
        converted = path.suffix.lower() != ".pdf" and convert.can_convert(path)
        target = path.with_suffix(".pdf") if converted else path  # a converted file is a new PDF beside it
        for index, workspace in enumerate(self.workspaces):
            if isinstance(workspace, Workspace) and workspace.document.path == target and                     (not converted or workspace.document.unsaved):
                self.tabs.setCurrentIndex(index)
                self._show_tab(index)
                return
        if converted:
            self.open_converted(path)
            return
        document = self._load(path)
        if document is not None:
            workspace = self.open_document(document)
            self._settings.add_recent(path)
            if MainWindow.indexer is not None:
                MainWindow.indexer.add([path])
            state = self._settings.file_state(path)
            if state:
                workspace.apply_view_state(state)

    def open_converted(self, path: Path) -> Workspace | None:
        """A text, web or office file, made a PDF and opened as a new document: saving asks where the PDF goes,
        and the file itself is left as it was."""
        data = self._convert(path)
        if data is None:
            return None
        try:
            document = Document(path.with_suffix(".pdf"), data=data)
        except Exception as error:
            self._cannot_open(path, error)
            return None
        workspace = self.open_document(document)
        QTimer.singleShot(0, lambda: workspace.notify(f"{path.name} is a PDF now. Save it to keep it."))
        return workspace

    def _convert(self, path: Path) -> bytes | None:
        """The PDF the file makes, worked out in the background while a dialog waits; None if it failed or
        was cancelled, after saying why."""
        progress = QProgressDialog(tr(f"Making {path.name} a PDF…"), tr("Cancel"), 0, 0, self)
        progress.setWindowTitle(tr("Opening"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)
        cancel = threading.Event()
        result: list = []
        failure: list[Exception] = []

        def work():
            try:
                result.append(convert.convert(path, cancel))
            except Exception as error:  # reported on the GUI thread below
                failure.append(error)

        worker = threading.Thread(target=work, name="convert", daemon=True)
        worker.start()
        while worker.is_alive():
            QApplication.processEvents()
            if progress.wasCanceled():
                cancel.set()
            worker.join(0.05)
        progress.close()
        progress.deleteLater()
        if failure and not isinstance(failure[0], convert.Cancelled):
            error = failure[0]
            if isinstance(error, convert.ConversionError):
                messages.warn(self, "Can't open the file", str(error))
            else:
                self._cannot_open(path, error)
        return result[0] if result else None

    def open_document(self, document: Document) -> Workspace:
        workspace = Workspace(document, self._renderer, self.tool_settings, self._settings, self.library)
        self.adopt(workspace)
        return workspace

    def adopt(self, workspace: Workspace, index: int | None = None) -> None:
        """Give a document's tab a place in this window: a new one, or one moved from another window."""
        links = [
            (workspace.settings_requested, lambda: self.open_settings()),
            (workspace.shortcuts_requested, self.show_shortcuts),
            (workspace.guide_requested, lambda: self.open_guide("")),
            (workspace.reading_requested, self.reading_mode.enter),
            (workspace.reading_exit_requested, self.reading_mode.leave),
            (workspace.undo_stack.cleanChanged, lambda _clean: self._update_titles(workspace)),
        ]
        for signal, slot in links:
            signal.connect(slot)
        self._links[workspace] = links
        self._add_tab(workspace, index)

    def take(self, workspace: Workspace) -> Workspace:
        """Take a document's tab out of this window, its document still open, to move it to another one."""
        index = self.workspaces.index(workspace)
        self.reading_mode.forget(workspace)
        self._close_chooser()
        close_help_card()
        for signal, slot in self._links.pop(workspace, []):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # already gone
        menu = workspace.toolbar.tool_menu
        if menu is not None and menu.isVisible():
            workspace.toolbar.put_menu_away()
        self.workspaces.pop(index)
        self.tabs.removeTab(index)
        self.stack.removeWidget(workspace)
        workspace.setParent(None)
        if self.workspaces:
            self._show_tab(self.tabs.currentIndex())
        else:
            self.show_home()
        return workspace

    def _add_tab(self, widget, index: int | None = None) -> None:
        index = len(self.workspaces) if index is None else max(0, min(index, len(self.workspaces)))
        self.workspaces.insert(index, widget)
        self.stack.addWidget(widget)
        index = self.tabs.insertTab(index, "")
        close = IconButton("x", "Close (Ctrl+W)", icon_size=12)
        close.clicked.connect(lambda: self.close_tab(self.workspaces.index(widget)))
        self.tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        self.tabs.show()
        self.tabs.setCurrentIndex(index)
        self._show_tab(index)
        self._update_titles(widget)

    # More windows ---------------------------------------------------------

    def new_window(self) -> MainWindow:
        """Another window, sharing the tools, their styles and the markup library with this one."""
        window = MainWindow(self._renderer, self._settings, self.tool_settings, self.library)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # unlike the first, it goes when closed
        geometry = self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        window.resize(geometry.size())
        window.move(self.pos() + QPoint(36, 36))
        window.show()
        return window

    def tear_off(self, workspace: Workspace, top_left: QPoint | None = None) -> MainWindow:
        """Open a tab in a window of its own, its top left at `top_left`. The only tab of a window stays: the
        window itself moves there."""
        if self.workspaces == [workspace]:
            if top_left is not None:
                self.move(top_left)
            return self
        window = self.new_window()
        window.adopt(self.take(workspace))
        if top_left is not None:
            window.move(top_left)
        window.raise_()
        window.activateWindow()
        return window

    def move_tab(self, workspace: Workspace, target: MainWindow, index: int | None = None) -> None:
        """Move a tab to another window, before its tab `index` (at the end if None). A window left with no tab
        closes, when another window stays open."""
        if target is self:
            return
        target.adopt(self.take(workspace), index)
        target.raise_()
        target.activateWindow()
        if not self.workspaces and len([w for w in MainWindow.open_windows if w.isVisible()]) > 1:
            self.close()

    def tab_index_at(self, pointer: QPoint) -> int | None:
        """Where a tab dropped at `pointer` (screen coordinates) goes: before the tab under it, else at the end."""
        local = self.tabs.mapFromGlobal(pointer)
        index = self.tabs.tabAt(local) if self.tabs.isVisible() and self.tabs.rect().contains(local) else -1
        return index if index >= 0 else None

    def _tab_menu(self, position: QPoint) -> None:
        menu = self.tab_menu(self.tabs.tabAt(position))
        if menu is not None:
            menu.exec(self.tabs.mapToGlobal(position))
            menu.deleteLater()

    def tab_menu(self, index: int) -> QMenu | None:
        """The right-click menu of tab `index`: a window of its own, another window, closing."""
        if not 0 <= index < len(self.workspaces):
            return None
        widget = self.workspaces[index]
        menu = QMenu(self)
        move = menu.addAction(icon("app-window"), tr("Move to new window"), lambda: self.tear_off(widget))
        move.setEnabled(isinstance(widget, Workspace) and len(self.workspaces) > 1)
        others = [w for w in MainWindow.open_windows if w is not self and w.isVisible()]
        if others and isinstance(widget, Workspace):
            to = menu.addMenu(icon("arrow-square-out"), tr("Move to window"))
            for number, window in enumerate(others, start=1):
                to.addAction(window.windowTitle() or tr(f"Window {number}"),
                             lambda w=window: self.move_tab(widget, w))
        menu.addSeparator()
        menu.addAction(icon("x"), tr("Close tab"), lambda: self.close_tab(self.workspaces.index(widget)))
        close_others = menu.addAction(tr("Close other tabs"), lambda: self._close_others(widget))
        close_others.setEnabled(len(self.workspaces) > 1)
        return explain_menu(menu)

    def _close_others(self, keep) -> None:
        for widget in [w for w in self.workspaces if w is not keep]:
            if widget in self.workspaces:
                self.close_tab(self.workspaces.index(widget))

    # New files, templates, Doc Builder ----------------------------------

    def show_new_file_panel(self) -> None:
        panel = NewFilePanel(self._settings, self)
        panel.open_requested.connect(self.open_dialog)
        panel.open_in_folder.connect(self.open_dialog)
        panel.new_document.connect(self.new_document_dialog)
        panel.from_template.connect(self.create_document)
        panel.edit_template.connect(self.edit_template)
        panel.delete_template.connect(self.delete_template)
        panel.new_template.connect(self.edit_template)
        panel.builder_requested.connect(self.open_builder)
        panel.pin_folder.connect(self.pin_folder)
        panel.unpin_folder.connect(self.unpin_folder)
        panel.show_below(self.open_button)

    def show_tools_panel(self) -> None:
        ToolsPanel(self).show_below(self.tools_button)

    def new_document_dialog(self) -> None:
        dialog = NewDocumentDialog(parent=self)
        accepted = dialog.exec() == NewDocumentDialog.DialogCode.Accepted
        spec, keep = (dialog.spec(), dialog.keep.isChecked()) if accepted else (None, False)
        dialog.deleteLater()
        if spec is None:
            return
        if keep:
            self._settings.templates = self._settings.templates + [spec]
        self.create_document(spec)

    def create_document(self, spec: dict) -> None:
        """A new document from a spec; it lives in memory until the first save."""
        folder = Path(self._settings.new_document_folder)
        path = folder / f"{spec['name']}.pdf"
        number = 2
        while path.exists() or any(isinstance(w, Workspace) and w.document.path == path for w in self.workspaces):
            path = folder / f"{spec['name']} ({number}).pdf"
            number += 1
        data = pages.new_document(page_size(spec), spec["kind"], spec["pages"])
        self.open_document(Document(path, data=data))

    def edit_template(self, position: int | None = None) -> None:
        edit_template(self._settings, position, self)

    def delete_template(self, position: int) -> None:
        delete_template(self._settings, position)

    def pin_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Pin a folder", self._settings.last_directory)
        if folder and folder not in self._settings.pinned_folders:
            self._settings.pinned_folders = self._settings.pinned_folders + [folder]

    def unpin_folder(self, position: int) -> None:
        folders = self._settings.pinned_folders
        if 0 <= position < len(folders):
            del folders[position]
            self._settings.pinned_folders = folders

    def open_builder(self) -> None:
        for index, widget in enumerate(self.workspaces):
            if isinstance(widget, DocBuilder):
                self.tabs.setCurrentIndex(index)
                self._show_tab(index)
                return
        builder = DocBuilder(self._renderer, QApplication.instance().devicePixelRatio())
        builder.open_requested.connect(self.open_file)
        self._add_tab(builder)

    def _load(self, path: Path) -> Document | None:
        password = None
        while True:
            try:
                return Document(path, password)
            except PdfiumError as error:
                if "password" not in str(error).lower():
                    self._cannot_open(path, error)
                    return None
                prompt = ("That password is not right. Try again." if password is not None
                          else f"{path.name} is protected. Type its password to open it.")
                password = messages.get_text(self, "Password required", prompt, password=True, action="Open",
                                             required=True)
                if password is None:
                    return None
            except OSError as error:
                self._cannot_open(path, error)
                return None

    def _cannot_open(self, path: Path, error: Exception) -> None:
        messages.warn(self, "Can't open the file", f"{path.name} could not be opened.", detail=str(error))

    # Saving -------------------------------------------------------------

    def save_current(self, save_as: bool = False) -> bool:
        """Save the current tab. False if nothing was saved."""
        workspace = self.current_workspace()
        if workspace is None:
            return False
        document = workspace.document
        if not document.can_save:
            messages.inform(self, "View only",
                            f"{document.path.name} could not be fully parsed, so it can be viewed but not saved.")
            return False
        target = None
        if save_as or document.unsaved:  # a new document has nowhere to go yet
            target, _ = QFileDialog.getSaveFileName(self, "Save as", str(document.path), "PDF documents (*.pdf)")
            if not target:
                return False
        try:
            workspace.save(target)
        except PermissionError:
            messages.warn(self, "Can't save",
                          f"{document.path.name} is read-only or open in another program. Use Save as to write a copy.")
            return False
        except Exception as error:
            messages.warn(self, "Can't save", f"Saving {document.path.name} failed.", detail=str(error))
            return False
        self._settings.add_recent(document.path)
        self._update_titles(workspace)
        if MainWindow.indexer is not None:
            MainWindow.indexer.add([document.path])  # what it says now is what a search finds
        return True

    # Drag and drop ------------------------------------------------------

    @staticmethod
    def _openable(url) -> bool:
        name = url.toLocalFile().lower()
        return name.endswith(".pdf") or convert.can_convert(name)

    def dragEnterEvent(self, event) -> None:
        if any(self._openable(url) for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if self._openable(url):
                self.open_file(url.toLocalFile())

    # Misc ---------------------------------------------------------------

    def set_theme(self, name: str) -> None:
        self._settings.theme = name

    # Settings -----------------------------------------------------------

    def _on_setting_changed(self, key: str) -> None:
        """Apply a preference to everything open, the moment it changes."""
        settings = self._settings
        if key in ("theme", "accent"):
            themes.choose(settings.theme, settings.get("accent"))
        elif key == "glass":
            set_glass(settings.get("glass"))
        elif key == "glass_adaptive":
            set_glass_adaptive(settings.get("glass_adaptive"))
        elif key == "icon_colours":
            icon_style.colourful = bool(settings.get("icon_colours"))
            themes.apply(themes.current)  # every icon is drawn again
        elif key == "reading_full_screen":
            self.reading_mode.fill_screen(settings.get("reading_full_screen"))
        elif key == "help_badges":
            badges.set_mode(settings.get("help_badges"))
        elif key == "help_delay":
            badges.delay = settings.get("help_delay")
        elif key == "author":
            self.tool_settings.author = settings.author or self._default_author
        elif key == "after_drawing":
            self.tool_settings.after_drawing = "select" if settings.get("after_drawing") == "select" else "pan"
        elif key == "select_new":
            self.tool_settings.select_new = settings.get("select_new")
        elif key == "autosave_minutes":
            minutes = settings.get("autosave_minutes")
            if minutes > 0:
                self._autosave.start(minutes * 60_000)
            else:
                self._autosave.stop()
        elif key == "tile_cache_mb":
            for widget in self.workspaces:
                if isinstance(widget, Workspace):
                    widget.view.set_cache_budget(settings.get("tile_cache_mb"))
        elif key == "remember_styles" and settings.get("remember_styles"):
            self._remember_styles()

    def _remember_styles(self, *_) -> None:
        self._styles_timer.stop()
        if self._settings.get("remember_styles"):
            self._settings.set("tool_styles", self.tool_settings.styles_data())

    def autosave(self) -> None:
        """Save every document with changes that already has a file of its own. One a background job is
        working on (a compression) waits for the next round."""
        for workspace in self.workspaces:
            if not isinstance(workspace, Workspace) or getattr(workspace, "busy", False):
                continue
            document = workspace.document
            if not workspace.dirty or document.unsaved or document.read_only or workspace.view.is_editing_text():
                continue
            try:
                workspace.save()
            except Exception as error:  # read-only file, file open elsewhere: say so, try again next time
                workspace.notify(f"Couldn't save automatically: {error}")
                continue
            self._update_titles(workspace)
            if workspace is self.current_workspace():
                workspace.notify("Saved")

    def restore_session(self) -> None:
        """Reopen the documents that were open when the app last closed, each where it was."""
        session = self._settings.get("session")
        opened = []
        for state in session.get("tabs", []):
            path = Path(state.get("path", ""))
            if not path.is_file():
                continue
            document = self._load(path)
            if document is None:
                continue
            workspace = self.open_document(document)
            workspace.apply_view_state(state)
            opened.append(workspace)
        active = session.get("active", -1)
        if isinstance(active, int) and 0 <= active < len(opened):
            index = self.workspaces.index(opened[active])
            self.tabs.setCurrentIndex(index)
            self._show_tab(index)
        elif session.get("home") and opened:
            self.show_home()

    def _toggle_full_screen(self) -> None:
        self.set_full_screen(not self.isFullScreen())

    def set_full_screen(self, on: bool, back_to: Qt.WindowState | None = None) -> None:
        """Full screen, or back to the window as it was before (maximized stays maximized), or to `back_to`."""
        if on and not self.isFullScreen():
            self._before_full_screen = self.windowState() & ~Qt.WindowState.WindowFullScreen
            self.showFullScreen()
        elif not on and self.isFullScreen():
            state = self._before_full_screen if back_to is None else back_to
            self.setWindowState(state & ~Qt.WindowState.WindowFullScreen)
        self.full_screen_button.set_icon_name("corners-in" if self.isFullScreen() else "corners-out")

    def show_assistant(self) -> None:
        """The assistant (Ctrl+K): ask for a tool, a command, a setting or a shortcut, in a few words."""
        if self.assistant_open():
            return
        close_help_card()
        self._assistant = AssistantPanel(self)
        self._assistant.show_over(self)

    def assistant_open(self) -> bool:
        return self._assistant is not None and isValid(self._assistant) and self._assistant.isVisible()

    def toggle_assistant(self) -> None:
        if self.assistant_open():
            self._assistant.close()
        else:
            self.show_assistant()

    def settings_window(self) -> SettingsWindow:
        """The settings window, made the first time it is needed (the assistant reads its rows)."""
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self._settings, self.tool_settings, self)
        return self._settings_window

    def reveal_setting(self, category: str, row) -> None:
        window = self.settings_window()
        window.show()
        window.raise_()
        window.activateWindow()
        window.reveal(category, row)

    def open_settings(self, category: str | None = None) -> None:
        window = self.settings_window()
        if category:
            window.search.clear()
            window.show_category(category)
        window.show()
        window.raise_()
        window.activateWindow()

    def show_shortcuts(self) -> None:
        self.open_settings("shortcuts")

    # What's new ---------------------------------------------------------

    def after_update(self) -> bool:
        """Once per new version, the tour of what changed; never on a fresh install. True if it showed."""
        seen = self._settings.get("seen_version")
        if not seen:  # 0.4.0 and earlier did not record it: files opened mean an update, none a new install
            seen = "0.4.0" if self._settings.get("recent") or self._settings.get("file_state") else __version__
        self._settings.set("seen_version", __version__)
        releases = since(seen)
        if not releases or not self._settings.get("whats_new_after_update"):
            return False
        self.show_whats_new(releases)
        return True

    def offer_default_app(self) -> bool:
        """At a start: should Zenith PDF open PDF files? The first time, and after Later a few starts on, three
        times at most; never when it opens them already. When the installer asked, its answer stands: Windows'
        list of apps opens at the first start if it was yes. True if it asked or acted."""
        from ..core import default_app

        starts = self._settings.get("starts") + 1
        self._settings.set("starts", starts)
        if self._settings.get("default_app_asked") or not default_app.can_set():
            return False
        if starts < self._settings.get("default_app_next"):
            return False
        if default_app.is_default() is not False:
            self._settings.set("default_app_asked", True)
            return False
        wanted = default_app.installer_choice()
        if wanted is not None:  # asked in the installer: its answer, at once
            self._settings.set("default_app_asked", True)
            if wanted:
                self.make_default_app()
            return wanted
        times = self._settings.get("default_app_times") + 1
        self._settings.set("default_app_times", times)
        text = DEFAULT_APP_QUESTION.get(sys.platform, DEFAULT_APP_QUESTION[""])
        dialog = messages.MessageDialog(self, "Open PDF files with Zenith PDF?", text,
                                        [("make", "Make it the default", "primary"), (messages.CANCEL, "Later", "")],
                                        icon="file-pdf", check="Don't ask again")
        choice, never = messages.run(dialog)
        if choice == "make" or never or times >= DEFAULT_APP_TIMES:
            self._settings.set("default_app_asked", True)
        else:
            self._settings.set("default_app_next", starts + DEFAULT_APP_AGAIN)
        if choice == "make":
            self.make_default_app()
        return True

    def make_default_app(self) -> str:
        """Make Zenith PDF open PDF files. Windows lets only the user choose: its own list of apps opens, with
        Zenith PDF among the suggested ones, and afterwards Zenith PDF looks whether the choice was made."""
        from ..core import default_app

        if sys.platform != "win32":
            result = default_app.make_default(own_command())
            if result == default_app.FAILED:
                messages.warn(self, "Zenith PDF could not be made the default",
                              DEFAULT_APP_BY_HAND.get(sys.platform, DEFAULT_APP_BY_HAND[""]))
            return result
        default_app.register_for_user(own_command())  # a copy run from source: listed by its name and logo
        while True:
            picked = default_app.choose_in_windows(int(self.winId()))
            if picked == default_app.CANCELLED:
                return picked
            if picked == default_app.FAILED:  # no list of apps: Windows' Default apps page instead
                return default_app.make_default(own_command())
            if default_app.is_default():
                messages.inform(self, "Zenith PDF opens your PDF files", "Double-click a PDF and it opens here.",
                                icon="check-circle")
                return default_app.DONE
            now = default_app.current_app_name() or tr("another app")
            again = messages.ask(self, "PDF files still open elsewhere", tr(
                f"They open with {now}. In Windows' list, pick Zenith PDF, then Always: on Windows 10, tick "
                f"Always use this app to open .pdf files before OK."),
                [("again", "Try again", "primary"), ("settings", "Default apps settings", ""),
                 (messages.CANCEL, "Close", "")], icon="info")
            if again == "settings":
                return default_app.make_default(own_command())
            if again != "again":
                return picked

    def show_whats_new(self, releases=None) -> None:
        from .whats_new import WhatsNewDialog

        dialog = WhatsNewDialog(releases or [RELEASES[0]], self)
        dialog.show_me.connect(self.show_me)
        dialog.exec()
        dialog.deleteLater()

    def show_me(self, action: str) -> None:
        """Take the user to what a highlight of the tour is about: a tool, a setting, a guide chapter, a command."""
        kind, _, what = action.partition(":")
        if kind == "setting":
            self.open_settings(what)
        elif kind == "guide":
            self.open_guide(what)
        elif kind == "tool":
            if self.current_workspace() is None:
                self.open_guide("tools")  # no document to draw on: the chapter about tools
            else:
                self.tool_settings.set_tool(what)
        elif kind == "command":
            command = getattr(self, f"command_{what}", None)
            if command is not None:
                command()

    def command_assistant(self) -> None:
        self.show_assistant()

    def command_compare(self) -> None:
        """Compare the open document with another version of it, or two files when none is open."""
        from .compare import compare_files

        workspace = self.current_workspace()
        start = str(workspace.document.path.parent) if workspace is not None else self._settings.get("last_directory")
        older, _ = QFileDialog.getOpenFileName(self, "Compare with which version?", start, "PDF documents (*.pdf)")
        if not older:
            return
        if workspace is None:
            newer, _ = QFileDialog.getOpenFileName(self, "And the newer version?", start, "PDF documents (*.pdf)")
            if not newer:
                return
            document = Document(Path(newer))
        else:
            current = workspace.document
            document = Document(current.path, password=current.password, data=current.data)  # as it is now
        try:
            self._compare_window = compare_files(self, Path(older), document)
        except Exception as error:
            document.close()
            messages.warn(self, "Can't compare", f"{Path(older).name} could not be opened.", detail=str(error))

    def command_search_all(self) -> None:
        """Search inside every PDF: the home page's search box asks the index too."""
        self.show_home()
        self.home.search.setFocus()
        self.home.search.selectAll()

    def open_at_page(self, path, page: int, query: str = "") -> None:
        """Open `path` at `page` (from 0), with the words of a search inside all documents lit on it."""
        self.open_file(path)
        workspace = self.current_workspace()
        if workspace is None or workspace.document.path != Path(path).resolve():
            return

        def land() -> None:
            workspace.view.jump(page)
            if query:
                workspace.side.show_panel("search")
                workspace.search.field.setText(query)
                workspace.search.start()

        QTimer.singleShot(0, land)  # after the file's own last position, which applies once the view has a size

    @classmethod
    def make_indexer(cls, settings: Settings):
        """The app's one Indexer, shared by the home pages of every window."""
        from PySide6.QtWidgets import QApplication

        from .library_search import Indexer

        if cls.indexer is None:
            cls.indexer = Indexer(settings, QApplication.instance())
            QApplication.instance().aboutToQuit.connect(cls.indexer.stop)
            for window in cls.open_windows:
                window.home.set_indexer(cls.indexer)
        return cls.indexer

    def command_study_notes(self) -> None:
        workspace = self.current_workspace()
        if workspace is None:
            self.open_guide("assistant")
        else:
            from . import writing_dialogs

            writing_dialogs.study_notes(workspace.view)

    def command_flashcards(self) -> None:
        workspace = self.current_workspace()
        if workspace is None:
            self.open_guide("assistant")
        else:
            from .flashcards import flashcards_for

            flashcards_for(workspace.view)

    def command_read_aloud(self) -> None:
        workspace = self.current_workspace()
        if workspace is None:
            self.open_guide("reading")
        else:
            workspace.read_aloud()

    # Updates ------------------------------------------------------------

    @classmethod
    def make_updater(cls, settings: Settings):
        """The app's one Updater: installs at quit what it downloaded, and gives every window its card."""
        from PySide6.QtWidgets import QApplication

        from .updates import Updater

        if cls.updater is None:
            app = QApplication.instance()
            cls.updater = Updater(settings, app)
            app.aboutToQuit.connect(cls.updater.install_at_quit)
            for window in cls.open_windows:
                window._attach_update_card()
        return cls.updater

    def _attach_update_card(self) -> None:
        from .updates import UpdateCard

        if self.update_card is None:
            self.update_card = UpdateCard(MainWindow.updater, self.centralWidget())

    def check_for_updates(self) -> None:
        """Asked for by hand, from Settings: the card says what it found, also that nothing is newer."""
        MainWindow.make_updater(self._settings).check(manual=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.update_card is not None and self.update_card.isVisible():
            self.update_card.place()

    def open_guide(self, chapter: str = "") -> None:
        from .guide import GuideWindow

        if getattr(self, "_guide", None) is None:
            self._guide = GuideWindow(self)
            self._guide.destroyed.connect(lambda: setattr(self, "_guide", None))
        self._guide.show_chapter(chapter)
        self._guide.show()
        self._guide.raise_()
        self._guide.activateWindow()

    def print_current(self) -> None:
        """Print with annotations, each page fitted to the paper and kept upright."""
        workspace = self.current_workspace()
        if workspace is None:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(workspace.document.path.stem)
        printer.setFromTo(1, workspace.document.page_count)
        dialog = QPrintDialog(printer, self)
        dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
        accepted = dialog.exec() == QPrintDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            return
        first, last = printer.fromPage(), printer.toPage()
        pages = range((first or 1) - 1, (last or workspace.document.page_count))
        scale = min(printer.resolution(), PRINT_DPI) / 72
        progress = QProgressDialog("Printing…", "Cancel", 0, len(pages), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        painter = QPainter()
        if not painter.begin(printer):
            progress.close()
            progress.deleteLater()
            messages.warn(self, "Can't print", "The printer could not be started.")
            return
        for count, index in enumerate(pages):
            if progress.wasCanceled():
                break
            progress.setValue(count)
            if count:
                printer.newPage()
            image = workspace.view.page_image(index, scale)
            area = painter.viewport()
            fitted = image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = area.x() + (area.width() - fitted.width()) // 2
            y = area.y() + (area.height() - fitted.height()) // 2
            painter.drawImage(QRect(x, y, fitted.width(), fitted.height()), image)
        painter.end()
        progress.close()
        progress.deleteLater()

    def closeEvent(self, event) -> None:
        for workspace in list(self.workspaces):
            if not self._confirm_close(workspace):
                event.ignore()
                return
        if self._styles_timer.isActive():  # a style changed moments ago: keep it
            self._remember_styles()
        self.reading_mode.leave()  # the window as it is outside reading mode is what opens next time
        documents = [w for w in self.workspaces if isinstance(w, Workspace) and not w.document.unsaved]
        last = not any(w is not self and w.isVisible() for w in MainWindow.open_windows)
        if last:  # the app closes with this window: what it shows opens next time
            self._settings.window_geometry = self.saveGeometry()
            current = self.stack.currentWidget()
            self._settings.set("session", {
                "tabs": [w.view_state() for w in documents],
                "active": documents.index(current) if current in documents else -1,
                "home": current is self.home,
            })
        for workspace in documents:
            self._settings.remember_file_state(workspace.document.path, workspace.view_state())
        for workspace in self.workspaces:
            if isinstance(workspace, DocBuilder):
                workspace.close_builder()
            else:
                workspace.close_document()
            self.stack.removeWidget(workspace)
            workspace.deleteLater()
        self.workspaces.clear()
        self._links.clear()
        while self.tabs.count():  # tabs and documents stay in step, even if the window comes back
            self.tabs.removeTab(0)
        self.show_home()
        if self in MainWindow.open_windows:
            MainWindow.open_windows.remove(self)
        super().closeEvent(event)
