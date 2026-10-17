"""The assistant (Ctrl+K, the sparkle in the top bar): ask in a few words or a whole question, in English,
Italian, French, Spanish, German or Portuguese, and it finds the tool, command, setting, shortcut or guide
chapter, and runs it with Enter. It also offers the writing helpers that fit what is selected.

It needs no language model and nothing leaves the PC: the search is core/finder.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import FreeText, Note, TextMarkup
from ..core.finder import Entry, Finder
from ..core.platforms import native_keys
from ..theme import THEME_NAMES, themes
from .dialogs import SHORTCUTS
from .help import TOPICS
from .tool_catalog import BY_KEY, CATALOG
from .widgets import FloatingBar, IconButton, cached_icon, icon_pixmap, muted_label

if TYPE_CHECKING:
    from .main_window import MainWindow

PANEL_WIDTH = 640
MAX_HEIGHT = 520
ROW_HEIGHT = 48
HEADER_HEIGHT = 30
RESULTS = 24
RECENT = 8  # picks remembered, to come first next time
KINDS = {"tool": "Tool", "command": "Command", "setting": "Setting", "shortcut": "Shortcut", "guide": "Guide",
         "writing": "Writing", "info": "Not available"}
VIEW_ONLY = {"select", "hand", "laser", "snapshot", "lasso"}  # tools that work on a document that can only be viewed
NO_DOCUMENT = "Open a document first."
READ_ONLY = "This document can only be viewed."
HINT = "↑ ↓ to choose, Enter to run, Esc to close."
MODEL_NOTE = ("Translating, explaining and rewriting text need a language model. Zenith PDF works without one, "
              "on this computer only, so these are not available.")


@dataclass
class Found:
    entry: Entry
    run: Callable[[], None] | None


class AccentButton(IconButton):
    """The assistant's button: an icon in the accent colour, so it can be found at a glance."""

    def _refresh(self, *_):
        self.setIcon(cached_icon(self._icon_name, themes.current.accent))


# What there is to find ----------------------------------------------------------------------


def _entries(window: MainWindow) -> list[Found]:
    workspace = window.current_workspace()
    found: list[Found] = []
    found += _writing(window, workspace)
    found += _tools(window, workspace)
    found += _commands(window, workspace)
    found += _settings(window)
    found += _shortcuts(window)
    found += _guide(window)
    return found


def _tools(window, workspace) -> list[Found]:
    found = []
    read_only = workspace is not None and workspace.document.read_only
    for spec in CATALOG:
        topic = TOPICS.get(spec.help)
        note = NO_DOCUMENT if workspace is None else (READ_ONLY if read_only and spec.key not in VIEW_ONLY else "")
        keys = {"select": "V", "hand": "H"}.get(spec.key, "")
        entry = Entry(f"tool:{spec.key}", "tool", spec.title, f"Tool · {spec.group}", "tool",
                      f"{topic.what} {topic.how}" if topic else "", keys, spec.icon, spec.color, not note, note)

        def run(key=spec.key):
            ws = window.current_workspace()
            if ws is None:
                return
            if not BY_KEY[key].action and ws.tool_settings.tool == key:
                ws.toolbar.open_menu(key)  # already on: its menu, with its options
            else:
                ws.toolbar.use(key)

        found.append(Found(entry, run))
    return found


def _command(key, title, keywords, icon, keys, run, note="", subtitle="Command") -> Found:
    return Found(Entry(f"cmd:{key}", "command", title, subtitle, keywords, "", keys, icon, "", not note, note), run)


def _commands(window, workspace) -> list[Found]:
    from . import pdf_tools
    from .document_view import LAYOUTS, TINTS

    has_doc = workspace is not None
    none = "" if has_doc else NO_DOCUMENT
    editable = "" if not has_doc or not workspace.document.read_only else READ_ONLY
    edit_note = none or editable
    found = [
        _command("open", "Open a PDF", "file document browse", "folder-open", "Ctrl+O", lambda: window.open_dialog()),
        _command("new", "New document", "blank create lined grid dotted paper templates", "file-plus", "",
                 window.new_document_dialog),
        _command("builder", "Doc Builder: combine PDF files", "merge join combine files pages builder", "squares-four",
                 "", window.open_builder),
        _command("home", "Home: recent files", "recent files start", "house", "", window.show_home),
        _command("save", "Save", "", "floppy-disk", "Ctrl+S", window.save_current, edit_note),
        _command("save_as", "Save as", "copy rename", "export", "Ctrl+Shift+S",
                 lambda: window.save_current(save_as=True), edit_note),
        _command("print", "Print", "printer paper", "printer", "Ctrl+P", window.print_current, none),
        _command("close_tab", "Close tab", "document", "x", "Ctrl+W", window.close_current_tab,
                 "" if window.stack.currentWidget() in window.workspaces else "No tab is open."),
        _command("reading", "Reading mode: only the pages", "reading focus hide bars distractions", "z-reading",
                 "Ctrl+H", window.reading_mode.toggle, none),
        _command("full_screen", "Full screen", "full screen", "corners-out", "F11", window._toggle_full_screen),
        _command("settings", "Settings", "settings preferences options", "gear-six", "", lambda: window.open_settings()),
        _command("guide", "Guide", "guide help manual", "graduation-cap", "F1", lambda: window.open_guide("")),
        _command("shortcuts", "Keyboard shortcuts", "shortcut keys keyboard", "keyboard", "", window.show_shortcuts),
        _command("about", "About Zenith PDF", "version about", "info", "", lambda: window.open_settings("about")),
        _command("tips_again", "Show tool tips again", "tips hints first time tools", "lightbulb", "",
                 lambda: _tips_again(window)),
        _command("tools_panel", "PDF tools", "tools pdf", "wrench", "", window.show_tools_panel),
        _command("compare", "Compare with another version", "compare versions difference diff changes revision",
                 "git-diff", "", window.command_compare),
        _command("search_all", "Search inside every PDF", "search all documents full text inside files library",
                 "magnifying-glass", "", window.command_search_all),
        _command("whats_new", "What's new in this version", "whats new changes release update tour",
                 "sparkle", "", lambda: window.show_whats_new()),
        _command("check_updates", "Check for updates", "update new version upgrade download",
                 "arrow-circle-up", "", window.check_for_updates),
        _command("new_window", "New window", "window another second side by side", "app-window", "Ctrl+Shift+N",
                 window.new_window),
    ]
    if has_doc and len(window.workspaces) > 1:
        found.append(_command("tab_window", "Move this document to a new window", "window side by side tab move",
                              "app-window", "", lambda ws=workspace: window.tear_off(ws)))
    icons = {"dark": "moon", "light": "sun", "system": "circle-half"}
    for name, label in THEME_NAMES.items():
        found.append(_command(f"theme_{name}", f"Theme: {label}", "theme colours appearance", icons.get(name, "palette"),
                              "", lambda n=name: window.set_theme(n)))
    for key, tool in ((t.key, t) for t in pdf_tools.TOOLS):
        note = "" if has_doc or not tool.needs_document else NO_DOCUMENT
        found.append(Found(Entry(f"pdf:{key}", "command", tool.title, f"PDF tools · {tool.group}", "pdf tools",
                                 tool.description, "", tool.icon, "", not note, note),
                           lambda t=tool: t.run(window)))
    if not has_doc:
        return found
    ws, view = workspace, workspace.view
    page = view.current_page
    actions = ws.actions
    snapping = bool(ws.settings and (ws.settings.get("snap_annotations") or ws.settings.get("snap_content")))

    def snap(on: bool) -> None:
        if ws.settings is not None:
            ws.settings.set("snap_annotations", on)
            ws.settings.set("snap_content", on)

    found += [
        _command("search", "Search in the document", "search find word text", "magnifying-glass", "Ctrl+F",
                 ws.show_search),
        _command("go_to_page", "Go to page…", "go page number jump", "hash", "Ctrl+G", ws.page_counter.focus),
        _command("read_aloud", "Read aloud", "read aloud speak voice listen speech audio", "speaker-high", "",
                 ws.read_aloud),
        _command("first_page", "First page", "go start beginning", "arrow-line-up", "Home", lambda: view.jump(0)),
        _command("last_page", "Last page", "go end", "arrow-line-down", "End",
                 lambda: view.jump(ws.document.page_count - 1)),
        _command("back", "Back to where you were", "back previous link", "arrow-bend-up-left", "Alt+Left", view.back),
        _command("forward", "Forward again", "forward next", "arrow-bend-up-right", "Alt+Right", view.forward),
        _command("page_manager", "Page manager: reorder, rotate, delete pages", "page manager organize thumbnails",
                 "squares-four", "", ws.toggle_page_manager),
        _command("zoom_in", "Zoom in", "zoom bigger larger", "magnifying-glass-plus", "Ctrl+=", view.zoom_in),
        _command("zoom_out", "Zoom out", "zoom smaller", "magnifying-glass-minus", "Ctrl+-", view.zoom_out),
        _command("fit_width", "Fit width", "zoom fit width", "arrows-out-line-horizontal", "Ctrl+2", view.fit_width),
        _command("fit_page", "Fit page", "zoom fit whole page", "corners-in", "Ctrl+0", view.fit_page),
        _command("actual_size", "Actual size", "zoom 100", "frame-corners", "Ctrl+1", lambda: view.set_zoom(1.0)),
        _command("snapping", "Turn snapping off" if snapping else "Turn snapping on", "snapping align guides magnet",
                 "magnet", "", lambda: snap(not snapping)),
        _command("undo", "Undo", "undo", "arrow-counter-clockwise", "Ctrl+Z",
                 lambda: (view.end_interaction(), ws.undo_stack.undo())),
        _command("redo", "Redo", "redo", "arrow-clockwise", "Ctrl+Y",
                 lambda: (view.end_interaction(), ws.undo_stack.redo())),
        _command("add_tools", "Add tools to the toolbar", "add tools toolbar chooser customise", "plus-circle", "",
                 ws.show_tool_chooser),
        _command("tool_styles", "Tool styles and locks", "tools colour thickness lock settings", "pencil-line", "",
                 lambda: window.open_settings("annotation")),
        _command("select_text", "Select all text on this page", "select text copy", "text-aa", "",
                 lambda: view.select_all_text(view.current_page)),
        _command("select_annotations", "Select all annotations on this page", "select annotations", "selection-all",
                 "", lambda: view.select_all_annotations(view.current_page)),
        _command("rotate_cw", f"Rotate page {page + 1} clockwise", "rotate page turn right", "arrow-clockwise", "",
                 lambda: actions.rotate(page, 90), editable, "Command · This page"),
        _command("rotate_ccw", f"Rotate page {page + 1} counter-clockwise", "rotate page turn left",
                 "arrow-counter-clockwise", "", lambda: actions.rotate(page, -90), editable, "Command · This page"),
        _command("add_page", f"Add a blank page after page {page + 1}", "add new blank page insert", "file-plus", "",
                 lambda: actions.add_page(page, "blank"), editable, "Command · This page"),
        _command("duplicate_page", f"Duplicate page {page + 1}", "duplicate copy page", "copy-simple", "",
                 lambda: actions.duplicate(page), editable, "Command · This page"),
        _command("delete_page", f"Delete page {page + 1}", "delete remove page", "file-minus", "",
                 lambda: actions.delete(page),
                 editable or ("" if ws.document.page_count > 1 else "The only page can't be deleted."),
                 "Command · This page"),
        _command("copy_page_image", f"Copy page {page + 1} as an image", "copy page image picture screenshot", "image",
                 "", lambda: actions.copy_page_image(page), "", "Command · This page"),
        _command("bookmark_page", f"Bookmark page {page + 1}", "bookmark add page", "bookmark-simple", "",
                 lambda: actions.add_bookmark(page), editable, "Command · This page"),
        _command("watermark", f"Watermark page {page + 1}", "watermark text draft", "drop-half", "",
                 lambda: actions.watermark(page), editable, "Command · This page"),
        _command("extend_margin", f"Extend the margin of page {page + 1}", "margin space notes", "arrows-out-line-horizontal",
                 "", lambda: actions.extend_margin(page), editable, "Command · This page"),
        _command("insert_pdf", f"Insert pages from a PDF after page {page + 1}", "insert pages pdf file merge",
                 "file-pdf", "", lambda: actions.insert_pdf(page), editable, "Command · This page"),
    ]
    if actions.ocr_available():
        found.append(_command("ocr", f"Recognise the text of page {page + 1} (OCR)", "ocr scan recognise text",
                              "magnifying-glass-plus", "", lambda: actions.run_ocr(page), editable,
                              "Command · This page"))
    for key, label in LAYOUTS.items():
        found.append(_command(f"layout_{key}", f"Page layout: {label}", "layout pages two book single", "book-open", "",
                              lambda k=key: view.set_layout(k)))
    for key, label in TINTS.items():
        found.append(_command(f"tint_{key}", f"Page colours: {label}", "colour night invert sepia reading", "drop-half",
                              "", lambda k=key: view.set_page_tint(k)))
    for key, title, icon_name in (("pages", "Pages panel", "files"), ("outline", "Bookmarks panel", "bookmark-simple"),
                                  ("annotations", "Annotations list", "chat-centered-text"),
                                  ("library", "Markup library", "books")):
        if key in ws.side.keys():
            found.append(_command(f"panel_{key}", title, "panel thumbnails sidebar", icon_name, "",
                                  lambda k=key: ws.side.show_panel(k)))
    return found


def _tips_again(window) -> None:
    window.settings.set("tips_seen", [])
    window.settings.set("tool_tips", True)
    workspace = window.current_workspace()
    if workspace is not None:
        workspace.notify("Each tool shows its tips again the next time you pick it.")


def _writing(window, workspace) -> list[Found]:
    from . import writing_dialogs as helpers

    view = workspace.view if workspace is not None else None
    found = []
    target = None
    if view is not None:
        selected = [a for a in view.selected_annotations() if isinstance(a, (FreeText, Note))]
        target = selected[0] if len(selected) == 1 else None
    if view is None:
        note = NO_DOCUMENT
    elif workspace.document.read_only:
        note = READ_ONLY
    elif target is None and not view.is_editing_text():
        note = "Select a text box, a callout or a note first."
    else:
        note = ""
    what = f" of this {target.label.lower()}" if target is not None else ""
    found.append(Found(Entry("write:spelling", "writing", f"Check spelling and punctuation{what}", "Writing",
                             "spelling punctuation typo mistakes correct", "", "", "check-circle", "", not note, note),
                       lambda: helpers.check_spelling(view)))
    has_text = view is not None and view.has_text_selection()
    found.append(Found(Entry("write:summary_selection", "writing", "Key points of the selected text", "Writing",
                             "summary key points selected text", "", "", "list-bullets", "", has_text,
                             "" if has_text else "Select some text first, with Select: drag across it."),
                       lambda: helpers.summarise_selection(view)))
    found.append(Found(Entry("write:summary_page", "writing",
                             f"Key points of page {view.current_page + 1}" if view is not None else "Key points of a page",
                             "Writing", "summary key points page", "", "", "article", "", view is not None,
                             "" if view is not None else NO_DOCUMENT),
                       lambda: helpers.summarise_page(view)))
    marked = view is not None and any(isinstance(a, TextMarkup) for a in view.store.all())
    found.append(Found(Entry("write:study_notes", "writing", "Study notes from highlights", "Writing",
                             "study notes highlights underlines export summary revise", "", "", "notebook", "", marked,
                             "" if marked else (NO_DOCUMENT if view is None else "No highlights or underlines yet.")),
                       lambda: helpers.study_notes(view)))
    found.append(Found(Entry("write:flashcards", "writing", "Flashcards from highlights", "Writing",
                             "flashcards cards anki study revise quiz memorize test highlights", "", "", "cards", "",
                             marked, "" if marked else (NO_DOCUMENT if view is None else
                                                        "No highlights or underlines yet.")),
                       lambda: _flashcards(view)))
    found.append(Found(Entry("write:model", "info", "Translate, explain or rewrite text", "Writing",
                             "translate explain rewrite paraphrase", "", "", "translate", "", False, MODEL_NOTE), None))
    return found


def _flashcards(view) -> None:
    from .flashcards import flashcards_for

    flashcards_for(view)


def _settings(window) -> list[Found]:
    settings_window = window.settings_window()
    found = []
    for key, section in settings_window.sections.items():
        if key in ("shortcuts", "about"):
            continue
        for row in section.rows:
            if not row.title:
                continue
            entry = Entry(f"set:{key}:{row.title}", "setting", row.title, f"Setting · {section.title}",
                          row.keywords, row.description, icon="gear-six")
            found.append(Found(entry, lambda k=key, r=row: window.reveal_setting(k, r)))
    return found


def _shortcuts(window) -> list[Found]:
    found = []
    for group, entries in SHORTCUTS:
        for keys, action in entries:
            entry = Entry(f"key:{keys}", "shortcut", action, f"Keyboard shortcut · {group}", "shortcut keys keyboard",
                          "", keys, "keyboard")
            found.append(Found(entry, window.show_shortcuts))
    return found


def _guide(window) -> list[Found]:
    from .guide import chapters

    found = []
    document = QTextDocument()
    for key, title, icon_name, html in chapters(document):
        plain = QTextDocument()
        plain.setHtml(html)
        entry = Entry(f"guide:{key}", "guide", f"Guide: {title}", "Guide chapter", "guide help",
                      plain.toPlainText()[:4000], "", icon_name)
        found.append(Found(entry, lambda k=key: window.open_guide(k)))
    return found


# The panel ----------------------------------------------------------------------------------


class ResultRow(QWidget):
    """One result: the icon, the name and where it lives, and its keys or what kind of thing it is."""

    def __init__(self, entry: Entry):
        super().__init__()
        self.entry = entry
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        glyph = QLabel()
        glyph.setFixedSize(22, 22)
        glyph.setPixmap(icon_pixmap(entry.icon or "circle", 20, entry.color or None))
        title = QLabel(native_keys(entry.title))
        title.setObjectName("AssistantTitle")
        # A short reason it can't run shows in place of where it lives; a long one waits in the footer.
        short = not entry.enabled and entry.note and entry.kind != "info"
        detail = muted_label(entry.note if short else entry.subtitle)
        detail.setObjectName("Muted")
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        text.addWidget(title)
        text.addWidget(detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 12, 4)
        row.setSpacing(12)
        row.addWidget(glyph)
        row.addLayout(text, 1)
        if entry.keys:
            chip = QLabel(native_keys(entry.keys))
            chip.setObjectName("KeyChip")
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            kind = QLabel(KINDS.get(entry.kind, ""))
            kind.setObjectName("AssistantKind")
            row.addWidget(kind, 0, Qt.AlignmentFlag.AlignVCenter)
        if not entry.enabled:
            for label in (title, glyph):
                label.setEnabled(False)
        self.setToolTip(entry.note if not entry.enabled else "")


class AssistantPanel(FloatingBar):
    """The search field and the results, over the top of the window. It deletes itself when it closes."""

    def __init__(self, window: MainWindow):
        super().__init__(window, popup=True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(PANEL_WIDTH)
        self._window = window
        self._found = _entries(window)
        self._runs = {f.entry.key: f.run for f in self._found}
        self._entries = {f.entry.key: f.entry for f in self._found}
        self.finder = Finder(f.entry for f in self._found)

        self.glyph = QLabel()
        self.glyph.setFixedSize(22, 22)
        self.glyph.setPixmap(icon_pixmap("sparkle", 20, themes.current.accent))
        self.search = QLineEdit()
        self.search.setObjectName("AssistantSearch")
        self.search.setPlaceholderText("Ask in your language: a tool, a command, a setting…")
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 4, 0)
        top.setSpacing(10)
        top.addWidget(self.glyph)
        top.addWidget(self.search, 1)
        self.list = QListWidget()
        self.list.setObjectName("AssistantList")
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the keys stay in the field
        self.list.itemClicked.connect(self._clicked)
        self.list.currentRowChanged.connect(self._on_row)
        self.footer = muted_label(HINT)
        self.footer.setWordWrap(True)
        self.footer.setContentsMargins(10, 0, 10, 0)
        self.footer.ensurePolished()
        # Room for two lines always: a longer note (why something can't run) never pushes into the list.
        self.footer.setFixedHeight(self.footer.fontMetrics().lineSpacing() * 2 + 4)
        self.footer.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.footer)
        self._refresh("")

    # Results ---------------------------------------------------------------------------

    def recent(self) -> list[str]:
        settings = self._window.settings
        return [k for k in settings.get("assistant_recent") if k in self._entries] if settings is not None else []

    def _boost(self) -> dict[str, float]:
        recent = self.recent()
        return {key: 0.15 - 0.012 * number for number, key in enumerate(recent)}

    def results(self, text: str) -> list[tuple[str, list[Entry]]]:
        """The sections to show for `text`: (heading, entries)."""
        if not text.strip():
            writing = [e for key, e in self._entries.items() if e.kind == "writing" and e.enabled]
            recent = [self._entries[k] for k in self.recent()]
            staples = [self._entries[k] for k in ("cmd:reading", "tool:highlight", "pdf:compress", "cmd:settings",
                                                  "cmd:guide") if k in self._entries]
            sections = [("Writing help", writing), ("Recent", recent),
                        ("Try", [e for e in staples if e not in recent])]
            return [(title, members) for title, members in sections if members]
        found = [entry for entry, _ in self.finder.search(text, RESULTS, self._boost())]
        if not found:
            return []
        return [("Best match", found[:1]), ("More", found[1:])] if len(found) > 1 else [("Best match", found)]

    def _refresh(self, text: str = "") -> None:
        self.list.clear()
        sections = self.results(text)
        height = 0
        for heading, members in sections:
            if not members:
                continue
            head = QListWidgetItem()
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setSizeHint(QSize(0, HEADER_HEIGHT))
            self.list.addItem(head)
            label = QLabel(heading)
            label.setObjectName("AssistantSection")
            self.list.setItemWidget(head, label)
            height += HEADER_HEIGHT
            for entry in members:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, entry.key)
                item.setSizeHint(QSize(0, ROW_HEIGHT))
                self.list.addItem(item)
                self.list.setItemWidget(item, ResultRow(entry))
                height += ROW_HEIGHT
        if not sections:
            empty = QListWidgetItem()
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setSizeHint(QSize(0, 64))
            self.list.addItem(empty)
            words = text.strip()
            note = muted_label(f"Nothing matches “{words}”. Try other words, or fewer.")
            note.setWordWrap(True)
            note.setContentsMargins(10, 0, 10, 0)
            self.list.setItemWidget(empty, note)
            height = 64
        self.list.setFixedHeight(min(max(height, 64) + 4, MAX_HEIGHT))
        self._select(self._first_row())
        self.adjustSize()

    def _first_row(self) -> int:
        return next((row for row in range(self.list.count())
                     if self.list.item(row).flags() & Qt.ItemFlag.ItemIsSelectable), -1)

    def _select(self, row: int) -> None:
        if row >= 0:
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row))

    def _on_row(self, row: int) -> None:
        item = self.list.item(row) if row >= 0 else None
        entry = self._entries.get(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
        self.footer.setText(entry.note if entry is not None and not entry.enabled and entry.note else HINT)

    def _move(self, step: int) -> None:
        rows = [row for row in range(self.list.count()) if self.list.item(row).flags() & Qt.ItemFlag.ItemIsSelectable]
        if not rows:
            return
        current = self.list.currentRow()
        if current not in rows:
            self._select(rows[0])
            return
        position = rows.index(current) + step
        self._select(rows[max(0, min(position, len(rows) - 1))])

    # Running ---------------------------------------------------------------------------

    def current_key(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _clicked(self, item: QListWidgetItem) -> None:
        self.run(item.data(Qt.ItemDataRole.UserRole))

    def run(self, key: str | None) -> bool:
        """Run what `key` names: the panel closes first, so the command opens on the window. False if it can't."""
        entry = self._entries.get(key) if key else None
        run = self._runs.get(key) if key else None
        if entry is None:
            return False
        if not entry.enabled or run is None:
            self.footer.setText(entry.note or "Not available here.")
            return False
        settings = self._window.settings
        if settings is not None:
            settings.set("assistant_recent", ([key] + [k for k in settings.get("assistant_recent") if k != key])[:RECENT])
        self.close()
        QTimer.singleShot(0, run)
        return True

    def eventFilter(self, obj, event) -> bool:
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._move(6 if key == Qt.Key.Key_PageDown else -6)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run(self.current_key())
                return True
            if key == Qt.Key.Key_Escape or (key == Qt.Key.Key_K and
                                            event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.close()  # Ctrl+K again puts it away, as it brought it
                return True
        return super().eventFilter(obj, event)

    def show_over(self, window: QWidget) -> None:
        """Centred over the window, a little below its top bar."""
        corner = window.mapToGlobal(QPoint((window.width() - PANEL_WIDTH) // 2, 56))
        screen = window.screen().availableGeometry()
        self.move(max(screen.left(), min(corner.x(), screen.right() - PANEL_WIDTH)), corner.y())
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
