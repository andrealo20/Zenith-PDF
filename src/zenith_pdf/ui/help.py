"""In-app help: point at a button for a moment and a card says what it does and how to use it.

attach_help(button, key) ties a button to TOPICS[key]; the card takes the place
of the button's tooltip. If the user asks for them in the settings, each button
also gets a small "?" badge that opens the card at once. The badge is a sibling
of the button, not a child, so it still works while the button is disabled.
Menus get the same text as tooltips (explain_menu).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget
from shiboken6 import isValid

from ..core.i18n import source, tr
from ..core.platforms import SYSTEM_NAME, WINDOWS, native_keys
from ..theme import themes
from .widgets import FloatingBar

SHOW_DELAY_MS = 280
HIDE_DELAY_MS = 160
CARD_WIDTH = 300


@dataclass(frozen=True)
class Topic:
    title: str
    what: str
    how: str = ""
    keys: str = ""


TOPICS: dict[str, Topic] = {
    # Top bar
    "home": Topic("Home", "Shows your recent files and pinned folders.",
                  "Click a recent file to open it. Open documents stay in their tabs."),
    "new": Topic("Open and new", "Opens files, creates new documents from templates and starts the Doc Builder.",
                 "Click and choose. Templates you save appear in the same list.", "Ctrl+O opens a file"),
    "tools": Topic("PDF tools", "Page numbers, cropping, splitting, compression, passwords, metadata, images and more.",
                   "Choose a tool: each one shows its options before it changes anything. Changes to the document "
                   "can be undone like any other edit."),
    "save": Topic("Save", "Writes your changes into the PDF file.",
                  "Annotations are stored as standard PDF annotations, so other viewers show them too. "
                  "A new document asks where to go the first time.", "Ctrl+S"),
    "save_as": Topic("Save as", "Saves a copy under another name or in another folder, then keeps working on the copy.",
                     keys="Ctrl+Shift+S"),
    "print": Topic("Print", "Prints the document with its annotations.",
                   "Pick the printer and the pages in the dialog. Each page is fitted to the paper.", "Ctrl+P"),
    "full_screen": Topic("Full screen", "Gives the whole screen to the pages.",
                         "Press the button or F11 again to go back.", "F11"),
    "more": Topic("More", "Settings, keyboard shortcuts, theme, and information about Zenith PDF."),
    "assistant": Topic("Assistant", "Finds any tool, command, setting, shortcut or guide chapter from a few words "
                       "or a question, in English, Italian, French, Spanish, German or Portuguese. It also checks "
                       "spelling, picks the key points of a text and turns highlights into study notes.",
                       "Type what you want to do, such as \"rotate page\" or \"come si firma\", and press Enter. It "
                       "works on this computer, without the internet or a language model.", "Ctrl+K"),
    # Annotation toolbar
    "tool_select": Topic("Select", "Selects annotations and text.",
                         "Click an annotation to select it; drag over empty space to select several. Drag a selection "
                         "to move it, or its handles to resize it. Drag across text, or double-click a word, to "
                         "select it: a bar over it marks, copies or searches for it.", "V"),
    "tool_hand": Topic("Pan", "Moves the pages around without selecting anything.",
                       "Drag the page. With any tool, holding Space or the middle mouse button pans too.", "H"),
    "tool_highlight": Topic("Highlight", "Marks text with a see-through colour, like a highlighter pen.",
                            "Drag across the text. Click the tool again for its menu: colour and opacity."),
    "tool_underline": Topic("Underline", "Draws a line under text.", "Drag across the text."),
    "tool_strikeout": Topic("Strikethrough", "Draws a line through text, to mark it as removed.",
                            "Drag across the text."),
    "tool_rectangle": Topic("Rectangle", "Draws a rectangle.",
                            "Drag on the page. Hold Shift for a square. Click the tool again for its menu: border, "
                            "fill, thickness and line style."),
    "tool_ellipse": Topic("Ellipse", "Draws an ellipse.", "Drag on the page. Hold Shift for a circle."),
    "tool_line": Topic("Line", "Draws a straight line.",
                       "Drag from one end to the other. Shift snaps to 45°. Its menu sets the line style and the ends."),
    "tool_arrow": Topic("Arrow", "Draws a line with an arrowhead at the end.",
                        "Drag from the tail to the tip. Shift snaps to 45°. Its menu sets the line style and both "
                        "ends, such as a second head."),
    "tool_text": Topic("Text box", "Types text anywhere on the page.",
                       "Click where the text should start and type; click outside the box to finish. "
                       "Double-click a text box to edit it again."),
    "tool_note": Topic("Note", "Adds a sticky note: a small icon that holds a comment.",
                       "Click on the page and write the note. Double-click the icon later to read or edit it. "
                       "Its menu picks the colour and the icon."),
    "tool_redact": Topic("Redact", "Marks areas to black out for good, such as names or account numbers.",
                         "Drag over what must go; the marks can still be moved or deleted. When ready, open "
                         "PDF tools and choose Apply redactions: the marked areas turn black and the text under "
                         "them is removed from the file. Find and redact marks every match of a word."),
    "tool_pen": Topic("Pen", "Draws freehand, like a pen on paper.",
                      "Drag on the page. Each stroke is its own drawing: select it to move, recolour or delete it. "
                      "Hold Shift for a straight stroke. Its menu has three pens (Basic; Pro, thinner when "
                      "you go fast; Calligraphy, with an angled nib) and can turn strokes into lines and shapes."),
    "tool_marker": Topic("Highlighter", "A freehand highlighter, for marking pictures, formulas and handwriting.",
                         "Drag over anything. It darkens what is under it instead of covering it. Shift draws a "
                         "straight stroke, handy for lines of text."),
    "tool_eraser": Topic("Eraser", "Rubs out pen and highlighter strokes.",
                         "Drag over the strokes to remove. Other annotations are not touched. Its menu sets the "
                         "size, and whether it takes whole strokes or only the part it passes over."),
    "tool_tape": Topic("Tape", "Strips of tape that hide part of the page, for testing yourself while studying.",
                       "Drag to stick tape over an answer or a word. Click the tape to peel it back and look, "
                       "click again to cover it. With Select, double-click a strip."),
    "tool_laser": Topic("Laser pointer", "A glowing trail that fades away, to point at things during a call or a "
                        "presentation.", "Drag on the page. Nothing is added to the document. Its menu sets the colour, "
                        "the size and how long the trail stays."),
    "tool_polyline": Topic("Polyline", "Joined straight lines, point by point.",
                           "Click for each corner, double-click or press Enter to finish. Backspace removes the last "
                           "point, Esc gives up. Shift keeps lines at 45°."),
    "tool_polygon": Topic("Polygon", "A closed shape with straight sides.",
                          "Click for each corner, double-click or press Enter to close it. Its menu can fill it."),
    "tool_cloud": Topic("Cloud", "A rectangle with a scalloped edge, the way changes are circled on drawings.",
                        "Drag over what you want to point out."),
    "tool_squiggly": Topic("Squiggle", "A wavy line under text, often used for a typo or a doubtful word.",
                           "Drag across the text."),
    "tool_cleaner": Topic("Text cleaner", "Takes highlights, underlines and strikethroughs off text.",
                          "Drag across the text to clean. Only the part you drag over is cleaned. Its menu picks "
                          "which kinds of marks go."),
    "tool_callout": Topic("Callout", "A text box with an arrow pointing at something on the page.",
                          "Press where the arrow should point, drag to where the text goes, let go and type. "
                          "Drag the arrow's tip later to point elsewhere."),
    "tool_distance": Topic("Distance", "Measures the length of a line, at the drawing's scale.",
                           "Drag from one point to the other; Shift keeps it straight. Set the scale (such as "
                           "1 cm = 1 m) in the tool's menu."),
    "tool_perimeter": Topic("Perimeter", "Measures the length of a path with several sides.",
                            "Click for each point, double-click or Enter to finish."),
    "tool_area": Topic("Area", "Measures the surface inside a shape, at the drawing's scale.",
                       "Click for each corner, double-click or Enter to close the shape."),
    "tool_bookmark": Topic("Bookmark", "Adds a bookmark to this page, to come back to it from the Bookmarks panel.",
                           "Click, then give it a name. Rename or delete it with a right click in the panel."),
    "tool_signature": Topic("Signature", "Signs the document with a signature you typed, drew or took from a "
                            "picture.", "Click where the signature goes and pick one, or make a new one. They are "
                            "kept for every document, and the tool's menu lists them. This is a picture of a signature, "
                            "not a certificate."),
    "tool_camera": Topic("Image from camera", "Takes a photo with the Windows Camera app and puts it on the page.",
                         "Click, take the photo in the Camera app, and it appears on the page.") if WINDOWS else
    Topic("Image from camera", "Takes a photo with the computer's camera and puts it on the page.",
          "Click, frame the shot in the camera window and take it: it appears on the page."),
    "tool_image": Topic("Image from file", "Places a picture file on the page.",
                        "Choose the file. Ctrl+V pastes a copied picture and you can drag files in too. Corners "
                        "resize it keeping its proportions; hold Shift to stretch it."),
    "tool_hyperlink": Topic("Hyperlink", "Makes part of the page clickable: to a web page or to another page.",
                            "Drag over the area, then type the address or the page. While this tool is on, links "
                            "show as blue boxes: click one to change or remove it."),
    "tool_audio": Topic("Audio note", "Records a voice note from the microphone, or attaches a sound file, shown "
                        "as a speaker on the page.",
                        "Click where the note goes, then record (up to 10 minutes) or pick a file; the tool's menu "
                        "sets whether it asks, records or attaches. Double-click the speaker to play it right by "
                        "the note."),
    "tool_equation": Topic("Equation", "Writes a formula, typed the way LaTeX writes it.",
                           "Click where it goes, type the formula and watch the preview. Double-click it later to "
                           "edit it."),
    "tool_snapshot": Topic("Snapshot", "Copies part of a page as a picture, annotations included.",
                           "Drag over the area. Then paste it here, in a chat, an e-mail or a slide. Its menu sets "
                           "the resolution and whether annotations come along."),
    "tool_lasso": Topic("Lasso select", "Selects annotations by drawing a loop round them.",
                        "Draw a loop; whatever has its middle inside is selected, ready to move or change. Its "
                        "menu can take what the loop touches, or only what is wholly inside, instead."),
    "add_tools": Topic("Add tools", "Opens the list of every tool: pens, shapes, measuring, signatures and more.",
                       "Click a tool to use it, or its + to keep it on the toolbar. You can also drag tools onto "
                       "the toolbar. Click the tool that is on, or right-click any tool, for its menu: style, "
                       "lock, move, duplicate or remove."),
    "snapping": Topic("Snapping", "Lines annotations up with each other and with the page's text and edges.",
                      "Turn it on here. While you move, resize or draw, edges and centres snap together and a "
                      "guide line shows what they lined up with. Hold Alt to place freely for a moment."),
    # Tool menu
    "menu_lock": Topic("Lock", "Keeps the tool on after each use, to draw several in a row.",
                       "Unlocked, the tool goes back to Select after one use (or to Pan, in Settings > "
                       "Annotation). Each tool, and each copy of one, has its own lock."),
    "menu_remove": Topic("Remove from the toolbar", "Takes the tool off the toolbar. A copy of a tool is forgotten "
                         "with its style.", "The + at the end of the toolbar brings any tool back."),
    "menu_more": Topic("More", "Duplicate the tool with its style, reset it, move it along the toolbar, or open "
                       "its help and every tool's settings."),
    "menu_pin": Topic("Pin the menu", "Keeps the menu open while you draw, to change the style between strokes.",
                      "Pinned, it follows the tools you turn on, and stays with an unlocked tool that hands over "
                      "to Pan or Select. Click the pin again to let it close."),
    "menu_subject": Topic("Tool subject", "The subject every annotation of this tool gets, such as Important or "
                          "Question. Other PDF apps show it and can sort by it.",
                          "Click the pencil, type, and press Enter. Leave it empty for the tool's own name."),
    # Page counter
    "nav_back": Topic("Back", "Returns to where you were before following a link or a bookmark, or going to "
                      "a page with Ctrl+G, Home or End.",
                      "It appears after such a jump. Alt+Right, or the mouse's forward button, goes where you "
                      "came back from.", "Alt+Left, or the mouse's back button"),
    "previous_page": Topic("Previous page", "Goes back one page.", keys="Page Up, or Left when the page fits"),
    "next_page": Topic("Next page", "Goes forward one page.", keys="Page Down, or Right when the page fits"),
    "page_manager": Topic("Page manager", "Every page as a thumbnail, to reorder, rotate, duplicate, extract or delete.",
                          "Drag thumbnails to reorder them; Ctrl or Shift selects several. Esc goes back."),
    # Zoom bar
    "view_options": Topic("More view options", "Page layout, page colours and snapping, gathered here while the "
                          "window is too narrow for their own buttons; in a very narrow one, Fit width and Fit page "
                          "too.", "Widen the window and they come back to the bar."),
    "layout": Topic("Page layout", "One page at a time, two side by side, or two with the cover alone, like a book."),
    "tint": Topic("Page colours", "Normal pages, inverted pages for reading in the dark, or warm sepia.",
                  "Only the screen changes: the file stays as it is."),
    "zoom_out": Topic("Zoom out", "Makes the pages smaller.", "Ctrl and the mouse wheel zoom around the pointer.",
                      "Ctrl+-"),
    "zoom_in": Topic("Zoom in", "Makes the pages larger.", "Ctrl and the mouse wheel zoom around the pointer.",
                     "Ctrl+="),
    "zoom_menu": Topic("Zoom level", "The current zoom. Click it for preset levels.", keys="Ctrl+1 is actual size"),
    "fit_width": Topic("Fit width", "Zooms so the page fills the width of the window.", keys="Ctrl+2"),
    "fit_page": Topic("Fit page", "Zooms so the whole page fits in the window.", keys="Ctrl+0"),
    "reading_mode": Topic("Reading mode", "Shows only the pages: the bars, the side panel and the tabs go away "
                          "until you come back, and nothing you were doing is lost.",
                          "Drag, scroll or use the keys to move through the pages; links work with a click. Move "
                          "the pointer to the top edge for a button that brings everything back. Settings > View & "
                          "Layout can make it fill the screen too.", "Ctrl+H, Esc to leave"),
    # Undo bar
    "undo": Topic("Undo", "Takes back the last change: annotations, page edits and tools alike.", keys="Ctrl+Z"),
    "redo": Topic("Redo", "Puts back what Undo took away.", keys="Ctrl+Y"),
    # Side rail
    "panel_pages": Topic("Pages", "Thumbnails of every page.", "Click a thumbnail to go to that page."),
    "panel_outline": Topic("Bookmarks", "The document's table of contents, when it has one.",
                           "Click an entry to jump to it."),
    "panel_search": Topic("Search", "Finds words anywhere in the document.",
                          "Type and press Enter. F3 and Shift+F3 move between the results.", "Ctrl+F"),
    "panel_annotations": Topic("Annotations", "Every annotation, page by page, with its text.",
                               "Click one to jump to it. The export button saves the list as CSV or plain text."),
    "panel_library": Topic("Markup library", "Annotations kept for reuse, such as stamps and standard comments.",
                           "Select annotations and choose Add to library in the selection bar. Click an item here "
                           "to place it on the current page."),
    "shortcuts": Topic("Keyboard shortcuts", "The keys for every command."),
    "guide": Topic("Guide", "A short guide to everything in Zenith PDF: reading, tools, pages, PDF tools, "
                   "settings and shortcuts.", "Pick a chapter, or search the whole guide.", "F1"),
    "settings": Topic("Settings", "Theme, accent colour, opening zoom, tool styles, autosave, help and more.",
                      "Changes apply at once. The search box finds a setting by name. Under Help you can change "
                      "how long to point at a button before its card opens."),
    "export_annotations": Topic("Export annotations", "Saves the list of annotations as a CSV table or plain text."),
    # Selection bar
    "sel_style": Topic("Style", "Colour, fill, thickness, opacity, line style and ends of the selection: the "
                       "options of the tools' menus.", "Every change applies at once, and Undo takes it back."),
    "sel_duplicate": Topic("Duplicate", "Makes a copy of the selection, just below and to the right."),
    "sel_arrange": Topic("Arrange", "Changes which annotation is drawn on top where they overlap."),
    "sel_flatten": Topic("Flatten", "Makes the selection part of the page. It looks the same, but nobody can move "
                         "or edit it any more, in any app.",
                         "Use it before sharing a file whose markup must not change. Undo works until you close it."),
    "sel_lock": Topic("Lock", "Stops the selection from being moved, resized or deleted by accident.",
                      "Click again to unlock."),
    "sel_library": Topic("Add to library", "Keeps a copy of the selection in the markup library, for any document."),
    "sel_apply": Topic("Apply to pages", "Copies the selection to the same place on other pages: all, odd, even or a range."),
    "sel_copy": Topic("Copy/Paste as", "Copy, cut, copy as an image for other apps, or paste."),
    "sel_more": Topic("More", "Properties, flatten, add to the markup library, apply to other pages, copy, cut, "
                      "copy as an image and paste; select everything on the page, or deselect."),
    "sel_delete": Topic("Delete", "Deletes the selection. Locked annotations stay.", keys="Del"),
    # Bar over selected text
    "text_highlight": Topic("Highlight", "Highlights the selected text in the highlight tool's colour, the one "
                            "the dot beside it shows.",
                            "Select text with Select: drag across it, or double-click a word. Undo takes the "
                            "highlight back."),
    "text_colour": Topic("Highlight colour", "Highlights the selected text in the colour you pick. New highlights "
                         "take that colour too.", "The dot shows the colour Highlight uses now."),
    "text_underline": Topic("Underline", "Draws a line under the selected text, in the underline tool's colour."),
    "text_strike": Topic("Strikethrough", "Draws a line through the selected text, to mark it as removed."),
    "text_squiggle": Topic("Squiggle", "Draws a wavy line under the selected text, to point out a mistake."),
    "text_read_aloud": Topic("Read aloud", "Reads the selected text with the system's voices.",
                             "A bar at the bottom pauses, goes back or on a sentence, changes the speed and the voice, "
                             "and stops."),
    "text_copy": Topic("Copy", "Copies the selected text, to paste it into any other app.", keys="Ctrl+C"),
    "text_note": Topic("Note", "Highlights the selected text with a note you write on it.",
                       "Point at the highlight to read the note; other PDF apps show it too. Properties, in the "
                       "selection bar's More menu, changes it."),
    "text_search": Topic("Search", "Finds every place the selected words occur in the document.",
                         "The results open in the Search panel; F3 goes to the next one."),
    # Page manager
    "pm_rotate_left": Topic("Rotate left", "Turns the selected pages a quarter turn counter-clockwise."),
    "pm_rotate_right": Topic("Rotate right", "Turns the selected pages a quarter turn clockwise."),
    "pm_duplicate": Topic("Duplicate", "Copies the last selected page, with its annotations, right after it."),
    "pm_extract": Topic("Save pages as", "Saves the selected pages, with their annotations, as a new PDF file.",
                        "The document itself does not change."),
    "pm_delete": Topic("Delete pages", "Removes the selected pages from the document.", keys="Del"),
    "pm_add_blank": Topic("Add blank page", "Adds an empty page after the selection, as large as its neighbour."),
    "pm_insert_pdf": Topic("Insert from PDF", "Adds every page of another PDF after the selection."),
    "pm_close": Topic("Back to the document", "Closes the page manager.", keys="Esc"),
    # Doc Builder
    "db_open": Topic("Open file", "Adds PDF files to the build.", "You can also drag files in from Explorer."),
    "db_sort": Topic("Sort A-Z", "Puts the files in alphabetical order, and their pages with them."),
    "db_clear": Topic("Clear", "Removes every file and starts over."),
    "db_undo": Topic("Undo", "Takes back the last change to the build.", keys="Ctrl+Z"),
    "db_redo": Topic("Redo", "Puts back what Undo took away.", keys="Ctrl+Y"),
    "db_extract": Topic("Extract", "Saves the selected pages as a new PDF, leaving the build as it is."),
    "db_rotate_left": Topic("Rotate left", "Turns the selected pages a quarter turn counter-clockwise."),
    "db_rotate_right": Topic("Rotate right", "Turns the selected pages a quarter turn clockwise."),
    "db_delete": Topic("Delete", "Takes the selected pages out of the build. The original files are not touched.",
                       keys="Del"),
    "db_duplicate": Topic("Duplicate", "Repeats the selected pages right after themselves."),
    "db_smaller": Topic("Smaller thumbnails", "Shows more pages at once."),
    "db_larger": Topic("Larger thumbnails", "Shows pages bigger, to read them."),
    "db_selection": Topic("Selection", "Select all pages, none, or swap the selection."),
    "db_build": Topic("Build", "Joins the pages in the order shown into one new PDF and opens it.",
                      "Name the result in the title above the pages."),
    "db_insert": Topic("Insert page", "Adds a blank page after the selected one."),
    # Home
    "home_open": Topic("Open file", "Opens one or more PDF files, each in its own tab.",
                       "You can also drag PDF files onto the window.", "Ctrl+O"),
}

# Tooltips for menu entries, by their text (without the shortcut after the tab).
MENU_TIPS = {
    "Edit text": "Change the text of this text box.",
    "Edit note": "Read or change the text of this note.",
    "Highlight": "Highlight the selected text.",
    "Underline": "Underline the selected text.",
    "Strikethrough": "Strike through the selected text.",
    "Squiggle": "Draw a wavy line under the selected text.",
    "Copy/Paste as": "Copy text or the page as an image, or paste text or an image in a new form.",
    "Copy selected text": "Copy the text you selected, for any other app.",
    "Copy page as image": "Copy the whole page with its annotations as a picture.",
    "Paste text as text box": "Turn the copied text into a text box here.",
    "Paste image here": "Place the copied picture on the page, where you clicked.",
    "Paste image as new page": "Add a page that shows the copied picture.",
    "Copy page": "Copy this page with its annotations; paste it into this or another document.",
    "Duplicate page": "Add a copy of this page right after it.",
    "Rotate page (Clockwise)": "Turn this page a quarter turn clockwise.",
    "Rotate page (Counter-clockwise)": "Turn this page a quarter turn counter-clockwise.",
    "Extend margin": "Add white space on the right of the page, for notes.",
    "Watermark…": "Write a large, faint text across the page, such as DRAFT.",
    "Run OCR (text recognition)": "Find the text in a scanned page, so it can be searched and selected.",
    "Insert": "Add pages, a picture, a text box or a note.",
    "Pages from PDF file…": "Add every page of another PDF after this page.",
    "Image as page…": "Add a page that shows a picture file.",
    "Image here…": "Place a picture file on the page, where you clicked.",
    "Equation here…": "Write a formula and place it where you clicked.",
    "Signature here…": "Sign where you clicked, with a saved signature or a new one.",
    "Guide": "A short guide to everything in Zenith PDF.",
    "Paste copied pages": "Insert the pages you copied after this page.",
    "Text box here": "Start typing a text box where you clicked.",
    "Note here": "Add a sticky note where you clicked.",
    "Select all annotations": "Select every annotation on this page.",
    "Select all text": "Select all the text on this page, to copy it.",
    "Bring to front": "Draw the selection above everything else.",
    "Bring forward": "Draw the selection one step higher.",
    "Send backward": "Draw the selection one step lower.",
    "Send to back": "Draw the selection below everything else.",
    "Copy as image": "Copy the selection as a picture, for other apps.",
    "Properties…": "Text, author, colour and the other details of the selection.",
    "Flatten": "Make the selection part of the page: it looks the same, but can't be edited any more.",
    "Add to library": "Keep a copy in the markup library, to place it in any document.",
    "Apply to pages": "Copy the selection to the same place on other pages.",
    "Copy": "Copy the selection, to paste it here or in another document.",
    "Cut": "Copy the selection and take it off the page.",
    "Paste": "Paste the annotations you copied.",
    "Select all on this page": "Select every annotation on this page.",
    "Deselect": "Select nothing.",
    "Duplicate": "A copy of this tool with the same style, right after it on the toolbar.",
    "Reset to default": "The tool's own colour, size, subject and lock again.",
    "Move left": "Move the tool one place to the left on the toolbar.",
    "Move right": "Move the tool one place to the right on the toolbar.",
    "Add tools…": "Every tool, to use or to keep on the toolbar.",
    "Reset toolbar": "The tools the toolbar started with. Copies on it are forgotten.",
    "Help": "The guide's chapter on the tools.",
    "All tool settings…": "Settings > Annotation: every tool's style, the locks, what tools go back to.",
    "Settings…": "Theme, tool styles, navigation, files, help and more.",
    "Keyboard shortcuts": "The keys for every command.",
    "About Zenith PDF": "Version and the open source parts Zenith PDF is built on.",
    "Play audio note": "Play the recording right here, in a small player by the note.",
    "Open attachment": f"Open the attached file with the app {SYSTEM_NAME} uses for it.",
    "Save attachment as…": "Save a copy of the attached file or recording.",
    "Peel tape": "Uncover what the tape hides.",
    "Put tape back": "Cover the text with the tape again.",
    "Edit equation…": "Change the formula or its size; it keeps its colour and place.",
    "Edit link…": "Change where the link leads, or remove it.",
    "Audio note here…": "Record a voice note or attach a sound file where you clicked.",
    "New signature…": "Type, draw or pick a picture of a signature, keep it and place it.",
    "Delete a signature": "Forget one of the saved signatures.",
    "Open…": "Open a PDF file.",
    "Close tab": "Close this document; you are asked about unsaved changes.",
    "Theme": f"Dark, light, or as {SYSTEM_NAME} is set.",
    "Reading mode": "Only the pages: the bars, the side panel and the tabs go away. Esc brings them back.",
    "Assistant…": "Find any tool, command, setting or shortcut by asking, in your language.",
    "New window": "Another Zenith PDF window, with the same tools and styles. Drag a tab out of the strip to move "
                  "a document into a window of its own.",
    "Move to new window": "Open this document in a window of its own. Dragging the tab out of the strip does the same.",
    "Move to window": "Move this document to another open window.",
    "Close other tabs": "Close every tab of this window but this one; you are asked about unsaved changes.",
    "Check spelling…": "Typos, doubled words, spacing and punctuation in this text, fixed in one step you can undo.",
    "Key points of the selection…": "The sentences that carry the selected text, to read or keep as a note.",
    "Full screen": "Give the whole screen to the window. F11 again goes back.",
    "Read aloud": "Read the selected text, or the pages from this one on, with the system's voices.",
    "Compare with another version…": "Two versions of a PDF side by side, with what was added, removed or changed "
                                     "lit on both.",
}


def explain_menu(menu: QMenu) -> QMenu:
    """Show a short explanation when hovering the entries of `menu` and its submenus."""
    menu.setToolTipsVisible(True)
    for action in menu.actions():
        text = source(action.text().split("\t")[0].replace("&", ""))
        if action.menu() is not None:
            explain_menu(action.menu())
        if text in MENU_TIPS:
            action.setToolTip(tr(MENU_TIPS[text]))
        elif text.startswith("Add page"):
            action.setToolTip(tr("Add a blank page after this one."))
        elif text.startswith("Delete page"):
            action.setToolTip(tr("Remove this page from the document."))
        elif text.startswith("Page "):
            action.setToolTip(tr("Add, copy, turn or remove this page."))
        elif text.startswith("Bookmark page"):
            action.setToolTip(tr("Add this page to the Bookmarks panel."))
    return menu


class HelpBadges(QObject):
    """How help shows up.

    "hover": point at a button and keep still for `delay` ms, and the card opens
    in place of the usual tooltip. "badges": a small ? on every button as well.
    "never": only the short tooltips.
    """

    mode_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.mode = "hover"
        self.delay = 1000

    def set_mode(self, mode: str) -> None:
        mode = {"always": "badges"}.get(mode, mode)  # the first name of the badges mode
        if mode != self.mode:
            self.mode = mode
            self.mode_changed.emit(mode)


badges = HelpBadges()


def attach_help(widget: QWidget, key: str) -> None:
    """Explain `widget` with TOPICS[key]: a card when pointing at it, a ? badge if the user wants one."""
    if key not in TOPICS:
        raise KeyError(f"no help topic {key!r}")
    widget.setProperty("help_topic", key)
    hook = getattr(widget, "_help_hook", None)
    if hook is None:
        widget._help_hook = _Hook(widget, key)
    else:
        hook.key = key


class _Hook(QObject):
    """Watches one button: opens its card when pointed at, and keeps its badge (if any) beside it."""

    PLACE = {QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.ParentChange}

    def __init__(self, target: QWidget, key: str):
        super().__init__(target)
        self.target = target
        self.key = key
        self.badge: HelpBadge | None = None
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._open)
        target.installEventFilter(self)
        badges.mode_changed.connect(self.sync)

    @property
    def topic(self) -> Topic:
        return TOPICS[self.key]

    def eventFilter(self, obj, event) -> bool:
        if not isValid(self._show_timer):
            return False  # the app is closing and Qt deleted the timer before the button
        kind = event.type()
        if kind == QEvent.Type.Enter and badges.mode == "hover":
            self._show_timer.start(badges.delay)
        elif kind in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._show_timer.stop()
            card = _live_card()
            if card is not None:
                card.leave(self)
        elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
            self._show_timer.stop()  # busy using the button: no card now
            card = _live_card()
            if card is not None and card.showing(self) and not card.pinned:
                card.close_card()
        elif kind == QEvent.Type.ToolTip and badges.mode == "hover":
            return True  # the card takes the tooltip's place
        if kind in self.PLACE:
            self.sync()
        return False

    def _open(self) -> None:
        if self.target.isVisible() and self.target.underMouse() and QApplication.activePopupWidget() is None:
            help_card().show_for(self, pinned=False)

    def sync(self, *_) -> None:
        """Place the badge on the button's top-right corner, in badges mode only."""
        target = self.target
        parent = target.parentWidget()
        wanted = badges.mode == "badges" and parent is not None and target.isVisibleTo(parent)
        if not wanted:
            if self.badge is not None:
                self.badge.hide()
            return
        if self.badge is None:
            self.badge = HelpBadge(self, parent)
        elif self.badge.parentWidget() is not parent:
            self.badge.setParent(parent)
        size = HelpBadge.SIZE
        x = min(target.x() + target.width() - size + 3, parent.width() - size)
        y = max(target.y() - 3, 0)
        self.badge.setGeometry(x, y, size, size)
        self.badge.show()
        self.badge.raise_()


class HelpBadge(QWidget):
    """The optional ? on a button. Pointing at it opens the card at once; a click keeps it open."""

    SIZE = 13

    def __init__(self, hook: _Hook, parent: QWidget):
        super().__init__(parent)
        self._hook = hook
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(SHOW_DELAY_MS)
        self._show_timer.timeout.connect(lambda: help_card().show_for(self._hook, pinned=False))
        themes.changed.connect(self.update)

    def enterEvent(self, event) -> None:
        self._show_timer.start()
        self.update()

    def leaveEvent(self, event) -> None:
        self._show_timer.stop()
        card = _live_card()
        if card is not None:
            card.leave(self._hook)
        self.update()

    def mousePressEvent(self, event) -> None:
        event.accept()  # never reaches the button underneath

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_timer.stop()
            card = help_card()
            if card.showing(self._hook) and card.pinned:
                card.close_card()
            else:
                card.show_for(self._hook, pinned=True)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        card = _live_card()
        active = self.underMouse() or (card is not None and card.showing(self._hook))
        if active:
            painter.setBrush(QColor(t.accent))
            painter.setPen(Qt.PenStyle.NoPen)
            text = QColor(t.accent_text)
        else:
            fill = QColor(t.raised)
            fill.setAlpha(235)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(t.border), 1))
            text = QColor(t.text_muted)
        painter.drawEllipse(rect)
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text)
        painter.drawText(self.rect().adjusted(0, 0, 0, -1), Qt.AlignmentFlag.AlignCenter, "?")


class HelpCard(QWidget):
    """The explanation, on frosted glass under the button. One for the whole app."""

    MARGIN = 14  # room for the glass's drop shadow

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.pinned = False
        self._owner: _Hook | None = None
        self.glass = FloatingBar(self, solid=True)
        self.title = QLabel()
        self.title.setObjectName("HelpTitle")
        self.detail = QLabel()  # what the button would do right now, e.g. "Undo add rectangle"
        self.detail.setObjectName("HelpHeading")
        self.what = QLabel()
        self.how_title = QLabel(tr("How to use"))
        self.how_title.setObjectName("HelpHeading")
        self.how = QLabel()
        self.keys = QLabel()
        self.keys.setObjectName("HelpKeys")
        for label in (self.what, self.how):
            label.setWordWrap(True)
        inner = QVBoxLayout(self.glass)
        inner.setContentsMargins(16, 12, 16, 14)
        inner.setSpacing(6)
        inner.addWidget(self.title)
        inner.addWidget(self.detail)
        inner.addWidget(self.what)
        inner.addWidget(self.how_title)
        inner.addWidget(self.how)
        keys_row = QHBoxLayout()
        keys_row.addWidget(self.keys)
        keys_row.addStretch()
        inner.addLayout(keys_row)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.MARGIN, self.MARGIN - 6, self.MARGIN, self.MARGIN + 6)
        outer.addWidget(self.glass)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self.close_card)

    def showing(self, owner: _Hook) -> bool:
        return self.isVisible() and self._owner is owner

    def show_for(self, owner: _Hook, pinned: bool) -> None:
        self._hide_timer.stop()
        previous = self._owner
        self._owner = owner
        self.pinned = pinned
        topic = owner.topic
        detail = owner.target.property("help_detail") or ""
        self.detail.setText(native_keys(tr(detail)))
        self.detail.setVisible(bool(detail))
        self.title.setText(native_keys(tr(topic.title)))
        self.what.setText(native_keys(tr(topic.what)))
        self.how.setText(native_keys(tr(topic.how)))
        self.how.setVisible(bool(topic.how))
        self.how_title.setVisible(bool(topic.how))
        self.keys.setText(native_keys(tr(topic.keys)))
        self.keys.setVisible(bool(topic.keys))
        for label in (self.what, self.how):
            label.setFixedWidth(CARD_WIDTH - 32)
        self.glass.setFixedWidth(CARD_WIDTH)
        # adjustSize() alone sizes for the last topic: wrapped labels need their height for this width.
        layout = self.glass.layout()
        layout.invalidate()
        layout.activate()
        self.glass.setFixedHeight(layout.totalHeightForWidth(CARD_WIDTH))
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()
        self.move(self._position(owner.target))
        self.glass.set_backdrop(owner.target.window())
        self.show()
        self.raise_()
        self.glass.refresh_backdrop()
        if pinned:
            QApplication.instance().installEventFilter(self)
        for hook in (previous, owner):
            if hook is not None and hook.badge is not None:
                hook.badge.update()

    def _position(self, button: QWidget) -> QPoint:
        """Below the button if it fits on the screen, else above it; kept inside the screen."""
        top = button.mapToGlobal(QPoint(0, 0))
        screen = button.screen().availableGeometry()
        size = self.size()
        glass_top = self.MARGIN - 6  # where the glass starts inside the card window
        glass_bottom = self.MARGIN + 6
        x = top.x() + button.width() // 2 - size.width() // 2
        y = top.y() + button.height() + 8 - glass_top
        if y + size.height() > screen.bottom():
            y = top.y() - 8 - size.height() + glass_bottom
        x = min(max(x, screen.left()), screen.right() - size.width())
        return QPoint(x, y)

    def leave(self, owner: _Hook) -> None:
        if self._owner is owner and not self.pinned:
            self._hide_timer.start()

    def close_card(self) -> None:
        self.hide()
        self.pinned = False
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        owner, self._owner = self._owner, None
        if owner is not None and isValid(owner) and owner.badge is not None and isValid(owner.badge):
            owner.badge.update()

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and not isinstance(obj, HelpBadge):
            self.close_card()
        elif kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_card()
            return True
        return False


_card: HelpCard | None = None


def help_card() -> HelpCard:
    """The one card of the app, made on first use: it is a window, so the application must exist."""
    global _card
    if _card is None or not isValid(_card):
        _card = HelpCard()
    return _card


def close_help_card() -> None:
    """Close the card if one is open, without making one."""
    card = _live_card()
    if card is not None and card.isVisible():
        card.close_card()


def _live_card() -> HelpCard | None:
    """The card if it exists and Qt still has it: at exit, events reach buttons after the card is gone."""
    card = _card
    if card is None or not isValid(card) or not isValid(card._hide_timer):
        return None
    return card
