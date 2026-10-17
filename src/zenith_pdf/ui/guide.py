"""The guide to the whole editor: chapters down the left, search, and the text with the real icons.

The chapters on tools and PDF tools are written from the same data as the
help cards and the tool chooser, so the guide can't drift from the app.
"""

from __future__ import annotations

import html
import platform

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.platforms import MAC, SYSTEM_NAME, WINDOWS, mono_font, native_keys, settings_place, ui_font
from ..core.releases import EARLIER, RELEASES
from ..icons import icon
from ..theme import themes
from .dialogs import SHORTCUTS
from .help import TOPICS
from .tool_catalog import CATALOG, GROUPS
from .widgets import say_copied

GROUP_INTROS = {
    "Free-form": "Draw by hand with the mouse, a pen or a finger.",
    "Shapes": "Lines and shapes that stay sharp at any zoom. Hold Shift for straight lines, squares and circles.",
    "Review": "Mark up text and add comments, the way a reviewer would.",
    "Measure": "Lengths, perimeters and areas at the scale of the drawing.",
    "Insert": "Notes, bookmarks, pictures, signatures, links, sound and formulas.",
    "Select": "Pick annotations to move, change or delete.",
}
if WINDOWS:
    SPELLER = ("the spell checker built into Windows, for every language Windows has a dictionary for "
               f"({settings_place('languages')} adds more)")
    CAMERA = "Image from camera opens the Windows Camera app; the photo you take appears on the page."
elif MAC:
    SPELLER = "the spell checker built into macOS, for every language it knows"
    CAMERA = "Image from camera shows what the camera sees; take the photo and it appears on the page."
else:
    SPELLER = ("Hunspell, through Enchant, with the dictionaries your system has (install more, such as "
               "hunspell-it, with your package manager)")
    CAMERA = "Image from camera shows what the camera sees; take the photo and it appears on the page."


def _whats_new() -> str:
    """Every version's highlights and smaller changes, the newest first, from core/releases.py."""
    parts = ["<h1>What's new</h1>"]
    for release in RELEASES:
        parts.append(f"<h2>Version {release.version}</h2><ul>")
        parts += [f"<li><b>{html.escape(h.title)}</b>. {html.escape(native_keys(h.text))}</li>" for h in release.highlights]
        parts += [f"<li>{html.escape(native_keys(line))}</li>" for line in release.more]
        parts.append("</ul>")
    parts.append("<h2>Earlier</h2><ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in EARLIER) + "</ul>")
    parts.append("<p>After each update a short tour shows what changed. Settings &gt; Help turns it off, and "
                 "opens it again.</p>")
    return "".join(parts)


def _keys(text: str) -> str:
    return f'<span class="key">{html.escape(native_keys(text))}</span>'


def _tool_list(document: QTextDocument, specs) -> str:
    rows = []
    for spec in specs:
        name = f"icon:{spec.key}"
        document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name),
                             icon(spec.icon, spec.color).pixmap(QSize(26, 26)).toImage())
        topic = TOPICS.get(spec.help)
        what = html.escape(topic.what) if topic else ""
        how = html.escape(topic.how) if topic and topic.how else ""
        rows.append(f'<tr><td class="glyph"><img src="{name}" width="26" height="26"></td>'
                    f'<td><b>{html.escape(spec.title)}</b><br>{what}'
                    f'{"<br><span class=muted>" + how + "</span>" if how else ""}</td></tr>')
    return "<table cellspacing='0' cellpadding='6'>" + "".join(rows) + "</table>"


def _pdf_tools() -> str:
    from .pdf_tools import GROUPS as TOOL_GROUPS, TOOLS

    parts = []
    for group in TOOL_GROUPS:
        items = "".join(f"<li><b>{html.escape(t.title)}</b>: {html.escape(t.description)}.</li>"
                        for t in TOOLS if t.group == group)
        parts.append(f"<h3>{group}</h3><ul>{items}</ul>")
    return "".join(parts)


def _shortcuts() -> str:
    parts = []
    for group, entries in SHORTCUTS:
        rows = "".join(f"<tr><td width='220'>{_keys(keys)}</td><td>{html.escape(action)}</td></tr>"
                       for keys, action in entries)
        parts.append(f"<h3>{group}</h3><table cellpadding='4'>{rows}</table>")
    return "".join(parts)


def chapters(document: QTextDocument) -> list[tuple[str, str, str, str]]:
    """(key, title, icon, html) for every chapter."""
    tools = "".join(f"<h3>{group}</h3><p class='muted'>{GROUP_INTROS[group]}</p>"
                    + _tool_list(document, [s for s in CATALOG if s.group == group]) for group in GROUPS)
    return [
        ("start", "Getting started", "rocket-launch", f"""
<h1>Getting started</h1>
<p>Zenith PDF reads, marks up and reorganises PDF documents. Everything you change can be undone with
{_keys('Ctrl+Z')}, and everything is saved as standard PDF, so other apps see your work too.</p>
<h2>Opening documents</h2>
<p>Open files from the home page, with {_keys('Ctrl+O')}, or by dropping PDF files onto the window. Each
document opens in its own tab. The <b>+</b> next to the tabs makes new documents, from templates too, and starts
the Doc Builder.</p>
<p>To read two documents side by side, drag a tab out of the strip: it opens in a window of its own. Drop a tab on
another Zenith PDF window to move it there, or right-click a tab for Move to new window. {_keys('Ctrl+Shift+N')}
opens an empty window. Every window shares the same tools and styles.</p>
<h2>The screen</h2>
<ul>
<li><b>Toolbar</b>, at the top: the tools you picked. Click the tool that is on for its menu: colour, size and
the rest of its options.</li>
<li><b>Page counter</b>, top right: where you are; the grid button opens the page manager.</li>
<li><b>Zoom bar</b>, bottom right: page layout, page colours, snapping, zoom, and reading mode.</li>
<li><b>Undo and redo</b>, bottom left.</li>
<li><b>Side panel</b>, on the left: pages, bookmarks, search, the list of annotations and the markup library.</li>
</ul>
<p>Point at any button and keep still for a moment: a card says what it does and how to use it.</p>
<h2>Saving</h2>
<p>{_keys('Ctrl+S')} saves into the file; {_keys('Ctrl+Shift+S')} saves a copy. A dot on the tab means there are
changes to save. Settings &gt; Files &amp; Saving can save automatically every few minutes.</p>"""),
        ("assistant", "Assistant and writing help", "sparkle", f"""
<h1>Assistant and writing help</h1>
<p>Press {_keys('Ctrl+K')}, or click the sparkle at the top right, and type what you want to do. Press
{_keys('Enter')} to run the best match, or move with the arrow keys first.</p>
<h2>Finding anything</h2>
<ul>
<li><b>Tools</b>: Enter turns the tool on; if it is on already, Enter opens its menu.</li>
<li><b>Commands</b>: save, print, rotate or duplicate this page, reading mode, page layout and colours, zoom,
every PDF tool.</li>
<li><b>Settings</b>: Enter opens Settings on that setting, lit for a moment so it is easy to spot.</li>
<li><b>Keyboard shortcuts</b> and the chapters of this guide.</li>
</ul>
<p>Ask in English, Italian, French, Spanish, German or Portuguese, in a few words or a whole question:
"ruota pagina", "Seite drehen", "pivoter la page", "come si firma un pdf". Typing mistakes are forgiven, and what
you ran last comes first. With nothing typed, it suggests the writing helpers that fit what is selected.</p>
<h2>Writing help</h2>
<ul>
<li><b>Check spelling and punctuation</b>: select a text box, a callout or a note, or right-click it. Zenith PDF
uses {html.escape(SPELLER)}. Spacing, punctuation and capitals are checked in every language. Untick what should
stay; the rest changes in one step that Undo takes back.</li>
<li><b>Key points</b> of the selected text (right-click it) or of a page: the sentences that carry it, Short,
Medium or Long. Keep them as a note on the page, or copy them.</li>
<li><b>Study notes from highlights</b>: every highlight, underline, squiggle and strikethrough with the text under
it and its comment, by page or by colour, ready to save as Markdown.</li>
</ul>
<p>All of this works on this computer, without the internet. Translating, explaining and rewriting text would need a
language model, which Zenith PDF does not use.</p>
<h2>Tips for new tools</h2>
<p>The first time you pick a tool, a small card under it gives two or three ways to use it well. It goes as soon as
you use the tool, and never comes back for that tool. Settings &gt; Help turns the cards off, or shows them
again.</p>"""),
        ("reading", "Reading and moving around", "book-open", f"""
<h1>Reading and moving around</h1>
<ul>
<li>Scroll with the mouse wheel; hold {_keys('Space')} or the middle button and drag to pan with any tool.</li>
<li>Zoom with {_keys('Ctrl')} and the wheel, round the pointer; {_keys('Ctrl+=')} and {_keys('Ctrl+-')} step
in and out. Pages stay sharp at every zoom.</li>
<li>{_keys('Ctrl+2')} fits the width, {_keys('Ctrl+0')} the whole page, {_keys('Ctrl+1')} shows actual size.</li>
<li>The zoom bar switches between one page, two pages side by side, and a book with the cover alone, and shows
pages inverted for the dark or in sepia.</li>
<li>Type a page number in the page counter, or press {_keys('Ctrl+G')}.</li>
<li>Search all the text with {_keys('Ctrl+F')}; {_keys('F3')} goes to the next result.</li>
<li>Click links in the document to follow them. Web addresses ask first. Links and bookmarks land on the line
they point at, just below the toolbar.</li>
<li>Point at a link to another page and keep still for a moment: a card shows what is there, such as the
reference behind [3] or the figure above its caption, with a mark at the line the link points at. Clicking still
follows the link. Settings &gt; View &amp; Layout &gt; Link previews turns the cards off.</li>
<li>After following a link or a bookmark, or going to a page with {_keys('Ctrl+G')}, {_keys('Home')} or
{_keys('End')}, a Back chip appears in the page counter with the page you came from.
{_keys('Alt+Left')} or the mouse's back button returns there, exactly where you were;
{_keys('Alt+Right')} or the forward button goes back to where you jumped.</li>
</ul>
<h2>Selected text</h2>
<p>With Select {_keys('V')}, drag across text, or double-click a word. A small bar appears just above it, or
below it when the toolbar leaves no room above:</p>
<ul>
<li><b>Highlight</b>, <b>Underline</b>, <b>Strikethrough</b> and <b>Squiggle</b> mark the text in their tools'
colours, which their icons show. The dot beside Highlight opens its colours: the one you pick marks the text and
stays for the next highlights.</li>
<li><b>Copy</b> copies the text, as {_keys('Ctrl+C')} does. <b>Note</b> highlights it with a note you write;
point at the highlight to read it. <b>Search</b> finds every place the words occur.</li>
</ul>
<p>{_keys('Esc')}, a click elsewhere or another tool lets the text go.</p>
<h2>Reading mode</h2>
<p>{_keys('Ctrl+H')}, the glasses at the end of the zoom bar, or Reading mode in the More menu (the three dots
at the top right) show only the pages: the bars, the side panel and the tabs go away, and the line you are
reading stays where it is.</p>
<ul>
<li>Drag the pages, scroll, zoom and use {_keys('Page Up')}, {_keys('Page Down')}, {_keys('Home')} and
{_keys('End')} as always. A click on a link follows it; {_keys('Alt+Left')} comes back.</li>
<li>{_keys('Esc')} or {_keys('Ctrl+H')} again brings everything back as it was: the same panel, the same tool,
what was selected. Moving the pointer to the top edge shows a small button that does the same, and goes away
again when the pointer moves on.</li>
<li>Search ({_keys('Ctrl+F')}) and the page box ({_keys('Ctrl+G')}) leave reading mode, as they need the
bars.</li>
<li>Settings &gt; View &amp; Layout can make reading mode fill the whole screen too.</li>
</ul>
<p>When you close a document, Zenith PDF remembers the page and zoom. When you close the app, it can reopen
every tab where it was (Settings &gt; Files &amp; Saving &gt; When Zenith PDF starts).</p>"""),
        ("tools", "Tools", "pen-nib", f"""
<h1>Tools</h1>
<p>The toolbar holds the tools you use most. The <b>+</b> at its end opens every tool, grouped as below: click
one to use it, click its <b>+</b> to keep it on the toolbar, or drag it onto the toolbar where you want it.
Drag a toolbar button along the toolbar to move it. On a narrow window, the last tools move into a menu at the
end of the toolbar.</p>
<p>From the keyboard, {_keys('F6')} (or {_keys('Tab')}) goes to the toolbar, on the tool that is on. The arrow
keys move along it, {_keys('Enter')} turns a tool on, or opens the menu of the one that is on, and the
{_keys('Menu')} key opens any tool's menu. {_keys('Esc')} goes back to the pages.</p>
<h2>Each tool's menu</h2>
<p>Click a tool to turn it on. Click it again, click its small arrow, right-click it, or hold it down for half a
second, and its menu opens. Every change applies at once and the menu stays open; {_keys('Esc')} or a click
elsewhere closes it.</p>
<ul>
<li>At the top, the tool's name and four buttons: <b>lock</b>, <b>remove</b> from the toolbar, <b>more</b>
(duplicate, reset, move left or right, help, every tool's settings) and <b>pin</b>, which keeps the menu open
while you draw. A pinned menu follows the tools you turn on, and stays with an unlocked tool when it hands over
to Pan or Select, ready for the next one.</li>
<li><b>Tool subject</b>: the subject its annotations get, such as Important or Question. Other PDF apps show it
and can sort by it. Click the pencil to change it.</li>
<li>A preview, drawn the way the page draws, then the options: colour, thickness, opacity, line style and the
others the tool has.</li>
</ul>
<h2>Lock, or back to Pan</h2>
<p>A locked tool stays on, to draw one thing after another. An unlocked tool is used once, then the pages go
back to Pan, ready to scroll; Settings &gt; Annotation can send them back to Select instead, with what you made
selected. The pens, the text markups and the measuring tools start locked; shapes, text boxes and notes start
unlocked. A small padlock on the tool that is on shows that it is locked.</p>
<h2>Copies of a tool</h2>
<p><b>Duplicate</b>, in the menu's more button, puts a copy of the tool right after it, with its own style and
subject: a green and a yellow highlighter side by side, say. Each copy shows its icon in its own colour.</p>
<h2>Pens and the eraser</h2>
<ul>
<li>The pen has three kinds: <b>Basic</b> keeps one width; <b>Pro</b> gets thinner when you move fast, like ink;
<b>Calligraphy</b> draws with an angled nib, thick one way and thin the other.</li>
<li><b>Smart ink</b> in the pen's and highlighter's menus: <b>Ink to line</b> straightens a stroke that is almost
straight, <b>Ink to shape</b> turns a drawn rectangle, ellipse or polygon into a real one.</li>
<li>The eraser takes <b>whole strokes</b>, or with <b>Partial</b> only the part it passes over.</li>
</ul>
<h2>Lines and shapes</h2>
<p>Lines, arrows, polylines and shapes can be solid, dotted, dashed or dash and dot. Each end of a line can be
plain, an open or closed arrow, a reversed arrow, a square, a circle, a diamond, a bar or a slash. Shapes have a
border and a fill colour; either can be none, not both. <b>Highlight mode</b> darkens what is under the shape
instead of covering it, like a highlighter.</p>
{tools}"""),
        ("editing", "Changing annotations", "cursor", f"""
<h1>Changing annotations</h1>
<p>With the Select tool {_keys('V')}, click an annotation to select it, or drag over empty space to select
several. The lasso selects by drawing a loop.</p>
<ul>
<li>Drag a selection to move it, or its handles to resize it. Lines, polylines and measurements have a handle
on every point; a callout has one on its arrow's tip.</li>
<li>A small bar appears over the selection: <b>Style</b> opens the same options as the tools' menus, for
everything selected at once; then duplicate, drawing order, lock, and a menu with properties, flatten, add to
the markup library, apply to other pages, copy as a picture and paste; delete is last.</li>
<li>Double-click a text box or note to edit it, a strip of tape to peel it back, an equation to change it,
an audio note to play it.</li>
<li>{_keys('Ctrl+C')}, {_keys('Ctrl+X')} and {_keys('Ctrl+V')} copy, cut and paste, also between documents.</li>
</ul>
<h2>Snapping</h2>
<p>The magnet in the zoom bar turns on snapping: to other annotations, to the page's content, or both. While
you move, resize or draw, edges and centres line up and a dashed guide shows with what. Hold {_keys('Alt')} to
place freely for a moment.</p>"""),
        ("pictures", "Pictures, sound, signatures", "image", f"""
<h1>Pictures, sound, signatures and equations</h1>
<ul>
<li><b>Audio notes</b>: record one from the microphone (up to 10 minutes) or attach a sound file. The audio
tool's menu chooses whether a click on the page asks, starts recording or asks for a file. Double-click the note
to play it in a small player beside it.</li>
<li><b>Paste</b> a copied picture with {_keys('Ctrl+V')}: it lands where the pointer is. Drag image files onto a
page, or use Image from file. Corners keep the proportions; {_keys('Shift')} stretches.</li>
<li><b>Camera</b>: {html.escape(CAMERA)}</li>
<li><b>Signatures</b>: type your name in a handwriting style, draw it, or take it from a photo of your
signature. They are kept for every document. This is a picture of a signature; for a certificate-based digital
signature you need a dedicated app.</li>
<li><b>Equations</b> are written like LaTeX: {_keys('x^2')}, {_keys('a_i')}, {_keys(r'\frac{a}{b}')},
{_keys(r'\sqrt{x}')}, {_keys(r'\sum_{i=1}^n')}, {_keys(r'\int_a^b')}, Greek letters by name such as
{_keys(r'\alpha')}. The preview shows mistakes as you type.</li>
</ul>"""),
        ("pages", "Pages", "files", """
<h1>Pages</h1>
<p>Right-click a page for page commands: add a blank, lined, grid or dotted page, copy, duplicate, delete or
rotate a page, insert pages from another PDF or a picture, extend the margin for notes, add a watermark, or run
text recognition (OCR) on a scan.</p>
<p>The page manager (the grid button by the page counter) shows every page: drag to reorder, select several to
rotate, extract or delete them.</p>
<p>New documents come from the <b>+</b> next to the tabs: paper size, orientation, ruling and length. Keep the
ones you use as templates.</p>"""),
        ("builder", "Doc Builder", "squares-four", """
<h1>Doc Builder</h1>
<p>Joins several PDFs into one. Add files on the left, and they appear as coloured cards; open a card to see its
pages. Drag files and pages into order, rotate, duplicate or delete pages, add blank ones, then press
<b>Build</b> to make the new PDF. The original files are never changed.</p>"""),
        ("pdf-tools", "PDF tools", "wrench", f"""
<h1>PDF tools</h1>
<p>The wrench at the top right opens tools for the whole document. Each one shows its options first; changes to
the document can be undone like any other edit.</p>
{_pdf_tools()}"""),
        ("security", "Redaction and security", "shield-check", """
<h1>Redaction and security</h1>
<h2>Redaction</h2>
<p>Mark what must go with the Redaction tool, or every place a word appears with Find and redact. The marks can
still be moved or deleted. Apply redactions blacks the areas out for good: those pages become pictures of
themselves, so the text under the marks is gone from the file, not just covered. Text recognition can make the
rest searchable again.</p>
<h2>Passwords and permissions</h2>
<p>Password and permissions encrypts the file with AES-256 when you save: a password to open it, and limits on
printing, copying and editing. Keep the password safe: without it nobody can open the file.</p>
<h2>Sanitize</h2>
<p>Removes what a PDF can carry besides its pages: scripts, embedded files, author details, links to websites.</p>"""),
        ("settings", "Settings", "gear-six", """
<h1>Settings</h1>
<p>Settings (the gear at the foot of the side panel, or the menu at the top right) apply as soon as you change
them. The search box finds a setting by name.</p>
<ul>
<li><b>View &amp; Layout</b>: theme, accent colour, the glass of the bars (Clear, Balanced or Solid), how
documents open, whether reading mode fills the screen.</li>
<li><b>Templates</b>: new-document templates, and where new documents go.</li>
<li><b>Annotation</b>: your name, where unlocked tools go back to, locks, snapping, and every tool's menu.</li>
<li><b>Canvas Navigation</b>: what the mouse wheel does and how fast it scrolls.</li>
<li><b>Files &amp; Saving</b>: autosave, reopening documents, the home page's list.</li>
<li><b>Performance</b>, <b>Text Recognition</b>, <b>Keyboard Shortcuts</b>, <b>Help</b>, <b>About</b>.</li>
</ul>"""),
        ("shortcuts", "Keyboard shortcuts", "keyboard", f"<h1>Keyboard shortcuts</h1>{_shortcuts()}"),
        ("whats-new", "What's new", "sparkle", _whats_new()),
        ("problem", "Report a problem", "first-aid-kit", """
<h1>Report a problem</h1>
<p>If something doesn't work, write down what you did, what you expected and what happened instead. The button
below copies the version of Zenith PDF and of the parts it is built on, which helps finding the cause.</p>
<p>A file that doesn't open may be damaged: PDF tools &gt; Repair a PDF can often rebuild it.</p>"""),
    ]


class GuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsWindow")
        self.setWindowTitle("Guide")
        self.resize(1040, 740)
        nav = QFrame()
        nav.setObjectName("SettingsNav")
        nav.setFixedWidth(300)
        title = QLabel("Guide")
        title.setObjectName("SettingsTitle")
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText("Search the guide")
        self.search.setClearButtonEnabled(True)
        self._search_icon = self.search.addAction(icon("magnifying-glass"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setObjectName("SettingsNavList")
        self.list.setIconSize(QSize(22, 22))
        self.list.currentItemChanged.connect(self._show)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(24, 34, 16, 20)
        nav_layout.setSpacing(18)
        nav_layout.addWidget(title)
        nav_layout.addWidget(self.search)
        nav_layout.addWidget(self.list, 1)
        self.text = QTextBrowser()
        self.text.setObjectName("GuideText")
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        self.text.setOpenExternalLinks(True)
        self.text.document().setDocumentMargin(40)
        self.copy = QPushButton("Copy version details")
        self.copy.clicked.connect(self._copy_details)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.text, 1)
        self.bottom = QWidget()  # only under "Report a problem"; elsewhere the text reaches the bottom
        bottom = QHBoxLayout(self.bottom)
        bottom.setContentsMargins(40, 0, 40, 20)
        bottom.addWidget(self.copy)
        bottom.addStretch()
        right.addWidget(self.bottom)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(nav)
        layout.addLayout(right, 1)
        themes.changed.connect(self._build)
        self._build()

    def _style(self) -> str:
        t = themes.current
        # Pixels, not points: a Mac counts 72 points to the inch and the text would come out smaller there.
        return (f"body {{ color: {t.text}; font-family: '{ui_font()}'; font-size: 14px; line-height: 135%; }}"
                f"h1 {{ font-size: 27px; font-weight: 600; margin-bottom: 12px; }}"
                f"h2 {{ font-size: 18px; font-weight: 600; margin-top: 22px; }}"
                f"h3 {{ font-size: 15px; font-weight: 600; margin-top: 18px; color: {t.accent_ink}; }}"
                f".muted {{ color: {t.text_muted}; }}"
                f".key {{ background-color: {t.raised}; color: {t.text}; font-family: '{mono_font()}'; }}"
                f"td {{ vertical-align: top; }}"
                f"li {{ margin-bottom: 4px; }}")

    def _build(self, *_) -> None:
        current = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else "start"
        t = themes.current
        self._search_icon.setIcon(icon("magnifying-glass"))
        self.text.setStyleSheet(f"QTextBrowser#GuideText {{ background: {t.window}; border: none; }}")
        self.text.document().setDefaultStyleSheet(self._style())
        self.chapters = chapters(self.text.document())
        self.list.blockSignals(True)
        self.list.clear()
        for key, title, icon_name, _ in self.chapters:
            item = QListWidgetItem(QIcon(icon(icon_name)), title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(240, 44))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.show_chapter(current)
        if self.search.text().strip():
            self._filter(self.search.text())  # the new theme keeps the search

    def show_chapter(self, key: str) -> None:
        key = key or "start"
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(row)
                self._show(self.list.item(row))
                return

    def _show(self, item, _previous=None) -> None:
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        page = next(text for k, _, _, text in self.chapters if k == key)
        self.text.setHtml(f"<body>{page}</body>")
        self.bottom.setVisible(key == "problem")
        self.copy.setText("Copy version details")  # a chapter shown again starts with nothing copied
        words = self.search.text().strip()
        if words:
            self.text.find(words.split()[0])

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        first = None
        for row in range(self.list.count()):
            item = self.list.item(row)
            key = item.data(Qt.ItemDataRole.UserRole)
            body = next(t for k, title, _, t in self.chapters if k == key)
            plain = QTextDocument()
            plain.setHtml(body)
            visible = all(word in plain.toPlainText().lower() for word in words)
            item.setHidden(not visible)
            if visible and first is None:
                first = item
        if first is None:  # nothing matches: say so, instead of a chapter that doesn't mention it
            self.list.blockSignals(True)
            self.list.setCurrentItem(None)
            self.list.blockSignals(False)
            self.bottom.hide()
            self.text.setHtml(f"<body><p class='muted'>No chapter mentions “{html.escape(text.strip())}”. "
                              "Try another word, or fewer words.</p></body>")
        elif self.list.currentItem() is None or self.list.currentItem().isHidden():
            self.list.setCurrentItem(first)
        else:
            self._show(self.list.currentItem())

    def _copy_details(self) -> None:
        from .settings_page import version_details

        QApplication.clipboard().setText(version_details() + f"\n{SYSTEM_NAME}: {platform.version()}")
        say_copied(self.copy)
