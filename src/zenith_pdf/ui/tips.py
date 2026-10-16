"""Tips the first time a tool is used: a small card under the tool with two or three ways to use it well.

Each tool's card shows once, ever (Settings > Help can show them again or turn them
off). It never takes the focus, and it goes as soon as the tool is used on the page,
another tool is picked, or the user closes it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import themes
from .tool_catalog import BY_KEY, base_of
from .widgets import FloatingBar, IconButton, icon_pixmap

CARD_WIDTH = 320
GAP = 10  # between the toolbar and the card
FIRST_TIP = "Ctrl+K opens the assistant: it finds any tool, command, setting or shortcut, in your language."

TOOL_TIPS: dict[str, tuple[str, ...]] = {
    "select": ("Double-click a word to select it: a bar over it highlights, copies or searches for it.",
               "Drag over empty space to select several annotations; Del deletes them, locked ones stay.",
               "Hold Alt while moving to place freely, without snapping."),
    "hand": ("Space or the middle mouse button pans with any tool, without switching to this one.",
             "H turns Pan on from the keyboard, V goes back to Select."),
    "pen": ("Hold Shift for a straight stroke.",
            "Click the pen again for its menu: Pro thins the line when you go fast, Calligraphy has an angled nib.",
            "Ink to shape, in the menu, turns a rough rectangle or circle into a clean one."),
    "marker": ("It darkens what is under it instead of covering it, like a real highlighter.",
               "Shift draws a straight stroke, handy along a line of text."),
    "eraser": ("It only rubs out pen and highlighter strokes; other annotations are safe.",
               "Its menu switches between whole strokes and only the part you pass over."),
    "tape": ("Stick tape over answers or definitions, then test yourself.",
             "Click a strip to peel it back and check; click again to cover it."),
    "laser": ("Nothing is added to the document: the trail fades by itself.",
              "Its menu sets the colour, the size and how long the trail stays."),
    "line": ("Shift snaps the line to 45°.",
             "Click the tool again for dashes and ten kinds of line ends.",
             "Unlocked, it goes back to Pan after one line: lock it in its menu to draw several."),
    "polyline": ("Click for each corner; double-click or Enter finishes.",
                 "Backspace takes back the last point, Esc gives up."),
    "arrow": ("Shift snaps the arrow to 45°.",
              "Its menu can put a head on both ends, or change the head's style."),
    "rectangle": ("Hold Shift for a square.",
                  "Click the tool again for border, fill, line style and a highlight mode that never hides the text.",
                  "Unlocked, it goes back to Pan after one shape: lock it in its menu to draw several."),
    "ellipse": ("Hold Shift for a circle.",
                "Its menu has border, fill and opacity, and a lock to draw several in a row."),
    "cloud": ("Drag over what changed, the way drawings mark revisions.",
              "Its menu sets the border, the fill and the opacity."),
    "polygon": ("Click for each corner; double-click or Enter closes the shape.",
                "Its menu can fill it; Backspace takes back the last corner."),
    "highlight": ("Drag across the text; the mark follows the lines.",
                  "With Select, double-click a word and use the bar over it: no need to switch tools.",
                  "Ctrl+K > Study notes gathers every highlight into notes you can save."),
    "underline": ("Drag across the text; the line follows each line of it.",
                  "With Select, the bar over selected text underlines without switching tools."),
    "squiggly": ("A wavy line, often used for a typo or a doubtful word.",
                 "With Select, the bar over selected text draws it without switching tools."),
    "strikeout": ("Drag across the text to mark it as removed.",
                  "The Text cleaner takes marks off again, only where you drag."),
    "cleaner": ("Only the part you drag over is cleaned.",
                "Its menu picks which kinds of marks go: highlights, underlines, squiggles, strikethroughs."),
    "text": ("Click and type; click outside the box to finish.",
             "Double-click a text box to edit it again.",
             "Right-click a text box and choose Check spelling to catch typos."),
    "callout": ("Press where the arrow points, drag to where the text goes, then type.",
                "Drag the arrow's tip later to point somewhere else."),
    "redact": ("The marks can still move until you apply them.",
               "PDF tools > Apply redactions blacks them out for good and removes the text under them.",
               "PDF tools > Find and redact marks every match of a word at once."),
    "distance": ("Set the drawing's scale first, in the tool's menu: for example 1 cm = 1 m.",
                 "Shift keeps the line straight."),
    "perimeter": ("Click for each point; double-click or Enter finishes.",
                  "The scale in the tool's menu turns points into metres, feet or any unit."),
    "area": ("Click for each corner; double-click or Enter closes the shape.",
             "The scale in the tool's menu turns the area into square metres or any unit."),
    "note": ("Click on the page and write; double-click the icon later to read or edit it.",
             "Its menu picks the colour and the icon.",
             "Right-click a note and choose Check spelling to catch typos."),
    "bookmark": ("The Bookmarks panel on the left lists them; click one to jump there.",
                 "Right-click a bookmark in the panel to rename or delete it."),
    "signature": ("The signatures you make are kept for every document.",
                  "It places a picture of your signature, not a digital certificate."),
    "camera": ("The photo comes from the Windows Camera app and lands on the page.",
               "Corners resize it keeping its proportions; hold Shift to stretch it."),
    "image": ("Ctrl+V pastes a copied picture, and you can drag picture files onto the page.",
              "Corners resize it keeping its proportions; hold Shift to stretch it."),
    "hyperlink": ("Drag over the area, then type a web address or a page number.",
                  "While this tool is on, links show as boxes: click one to change or remove it."),
    "audio": ("Click where the note goes, then record or pick a sound file.",
              "Its menu chooses whether it asks, records or attaches.",
              "Double-click the speaker to play it right there."),
    "equation": ("Type LaTeX, such as \\frac{a}{b} or x^2, and watch the preview.",
                 "Double-click the formula later to edit it."),
    "snapshot": ("Drag over an area, then paste it in a chat, an e-mail or a slide.",
                 "Its menu sets the resolution and whether annotations come along."),
    "lasso": ("Draw a loop round annotations: whatever has its middle inside is selected.",
              "Its menu can take what the loop touches, or only what is wholly inside."),
}


class TipCard(FloatingBar):
    """The card itself: the tool's tips, Got it, and a way to turn tips off."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, solid=True)
        self.setFixedWidth(CARD_WIDTH)
        self.key = ""
        self.glyph = QLabel()
        self.glyph.setFixedSize(20, 20)
        self.title = QLabel()
        self.title.setObjectName("HelpTitle")
        self.close_button = IconButton("x", "Close", icon_size=14)
        self.close_button.clicked.connect(self.dismiss)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.glyph)
        header.addWidget(self.title, 1)
        header.addWidget(self.close_button)
        self.lines: list[QLabel] = []
        self._body = QVBoxLayout()
        self._body.setSpacing(6)
        self.off_button = QPushButton("Don't show tips")
        self.off_button.setObjectName("Link")
        self.off_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.got_button = QPushButton("Got it")
        self.got_button.setObjectName("Primary")
        self.got_button.clicked.connect(self.dismiss)
        footer = QHBoxLayout()
        footer.addWidget(self.off_button)
        footer.addStretch()
        footer.addWidget(self.got_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 14)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addLayout(self._body)
        layout.addLayout(footer)
        for button in (self.off_button, self.got_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the card never takes the keyboard from the pages
        themes.changed.connect(self._paint_glyph)
        self._paint_glyph()
        self.hide()

    def _paint_glyph(self, *_) -> None:
        self.glyph.setPixmap(icon_pixmap("lightbulb-filament", 20, themes.current.accent))

    def fill(self, key: str, lines: tuple[str, ...]) -> None:
        self.key = key
        spec = BY_KEY.get(base_of(key))
        self.title.setText(f"Tips: {spec.title if spec else key}")
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().setParent(None)
                item.widget().deleteLater()
        self.lines = []
        for text in lines:
            row = QLabel(f"•  {text}")
            row.setObjectName("TipText")
            row.setWordWrap(True)
            row.setTextFormat(Qt.TextFormat.PlainText)
            self._body.addWidget(row)
            self.lines.append(row)
        self.layout().invalidate()
        self.layout().activate()
        self.setFixedHeight(self.layout().totalHeightForWidth(CARD_WIDTH))

    def dismiss(self) -> None:
        self.hide()


class ToolTips(QObject):
    """Shows a tool's tips on the canvas of one workspace, the first time the user turns that tool on."""

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.card = TipCard()
        self.card.off_button.clicked.connect(self.turn_off)
        workspace.canvas.add_floating(self.card)
        workspace.tool_settings.tool_changed.connect(self._on_tool)
        workspace.toolbar.action_used.connect(self._on_action)
        workspace.view.viewport().installEventFilter(self)  # using the tool on the page puts its card away
        workspace.canvas.relaid.connect(self._place)
        workspace.toolbar.menu_opened.connect(lambda _key: self.card.hide())  # the tool's own menu says more

    @property
    def settings(self):
        return self.workspace.settings

    def wanted(self, key: str) -> bool:
        settings = self.settings
        base = base_of(key)
        return (settings is not None and bool(settings.get("tool_tips")) and base in TOOL_TIPS
                and base not in settings.get("tips_seen"))

    def _on_tool(self, key: str) -> None:
        if self.workspace.tool_settings.handing_over:
            return  # the tool went back to Pan or Select by itself: no tip for a tool nobody picked
        self.card.hide()
        self.show_for(key)

    def _on_action(self, key: str) -> None:
        self.card.hide()
        self.show_for(key)

    def show_for(self, key: str) -> bool:
        """Show `key`'s tips if they are due and there is room for them. True if they show."""
        workspace = self.workspace
        canvas = workspace.canvas
        if (not self.wanted(key) or not workspace.isVisible() or canvas.reading or canvas.manager is not None):
            return False
        settings = self.settings
        seen = settings.get("tips_seen")
        lines = TOOL_TIPS[base_of(key)]
        if not seen:
            lines = lines + (FIRST_TIP,)  # the very first card also says where to ask
        settings.set("tips_seen", seen + [base_of(key)])  # once ever, read or not
        self.card.fill(key, lines)
        self._place()
        self.card.show()
        self.card.raise_()
        return True

    def _place(self) -> None:
        card = self.card
        if not card.isVisible() and not card.key:
            return
        canvas = self.workspace.canvas
        toolbar = self.workspace.toolbar
        anchor = toolbar.anchor_for(card.key) if card.key else toolbar
        corner = anchor.mapTo(canvas, QPoint(anchor.width() // 2, 0))
        x = corner.x() - card.width() // 2
        y = toolbar.geometry().bottom() + GAP
        x = min(max(x, GAP), max(canvas.width() - card.width() - GAP, GAP))
        counter = self.workspace.page_counter
        if counter.isVisible() and counter.y() < y + card.height() and x + card.width() > counter.x() - GAP:
            x = max(counter.x() - GAP - card.width(), GAP)  # beside the page counter, not over it
        card.setGeometry(QRect(x, y, card.width(), card.height()))
        menu = toolbar.tool_menu
        if menu is not None and menu.isVisible():
            card.hide()  # the tool's own menu hangs there: it says more
        elif card.isVisible():
            card.raise_()

    def turn_off(self) -> None:
        self.card.hide()
        if self.settings is not None:
            self.settings.set("tool_tips", False)
            self.workspace.notify("Tips are off. Settings > Help can turn them back on.")

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and self.card.isVisible():
            self.card.hide()
        return False
