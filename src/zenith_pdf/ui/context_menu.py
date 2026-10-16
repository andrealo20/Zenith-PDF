"""The right-click menu of the page canvas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QMenu, QWidget, QWidgetAction

from ..core.annotations import Annotation, Attachment, FreeText, ImageStamp, Link, Note, Tape
from ..core.pages import TEMPLATES
from ..icons import icon
from . import clipboard
from .help import explain_menu
from .widgets import IconButton

if TYPE_CHECKING:
    from .document_view import DocumentView

MARKUPS = (("highlight", "Highlight", "z-text-highlight"), ("underline", "Underline", "text-underline"),
           ("strikeout", "Strikethrough", "text-strikethrough"), ("squiggly", "Squiggle", "z-squiggle"))


def _action(menu: QMenu, icon_name: str | None, text: str, slot, enabled: bool = True):
    action = menu.addAction(icon(icon_name), text, slot) if icon_name else menu.addAction(text, slot)
    action.setEnabled(enabled)
    return action


def _quick_row(menu: QMenu, buttons) -> None:
    """A row of icon buttons across the top of the menu: copy, cut, paste, delete."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(4)
    for icon_name, tip, slot, enabled in buttons:
        button = IconButton(icon_name, tip)
        button.setEnabled(enabled)
        button.clicked.connect(lambda _=False, s=slot: (menu.close(), s()))
        layout.addWidget(button)
    layout.addStretch()
    action = QWidgetAction(menu)
    action.setDefaultWidget(row)
    menu.addAction(action)


def _check_spelling(view: DocumentView, annotation) -> None:
    from .writing_dialogs import check_spelling

    check_spelling(view, annotation)


def _summarise_selection(view: DocumentView) -> None:
    from .writing_dialogs import summarise_selection

    summarise_selection(view)


def _annotation_actions(menu: QMenu, view: DocumentView, annotation: Annotation | None, editable: bool) -> bool:
    """What can be done to the annotation under the pointer, besides the clipboard. False if nothing."""
    if isinstance(annotation, FreeText):
        _action(menu, "pencil-simple", "Edit text", lambda: view.edit_text_box(annotation, is_new=False), editable)
        _action(menu, "check-circle", "Check spelling…", lambda: _check_spelling(view, annotation),
                editable and bool(annotation.contents.strip()))
    elif isinstance(annotation, Note):
        _action(menu, "pencil-simple", "Edit note", lambda: view.edit_note(annotation), editable)
        _action(menu, "check-circle", "Check spelling…", lambda: _check_spelling(view, annotation),
                editable and bool(annotation.contents.strip()))
    elif isinstance(annotation, Attachment):
        if annotation.audio:
            _action(menu, "play", "Play audio note", lambda: view.open_attachment(annotation, QCursor.pos()))
        else:
            _action(menu, "arrow-square-out", "Open attachment", lambda: view.open_attachment(annotation))
        _action(menu, "download-simple", "Save attachment as…", lambda: view.save_attachment(annotation))
    elif isinstance(annotation, Tape):
        from .drawing_tools import toggle_tape

        _action(menu, "eye" if not annotation.revealed else "eye-closed",
                "Put tape back" if annotation.revealed else "Peel tape", lambda: toggle_tape(view, annotation), editable)
    elif isinstance(annotation, ImageStamp) and annotation.equation:
        _action(menu, "pencil-simple", "Edit equation…", lambda: view.edit_equation(annotation), editable)
    elif isinstance(annotation, Link):
        _action(menu, "link", "Edit link…", lambda: view.edit_link(annotation), editable)
    else:
        return False
    return True


def build_context_menu(view: DocumentView, page: int, pos, annotation: Annotation | None) -> QMenu:
    menu = QMenu(view)
    actions = view.actions
    editable = actions is not None and not view.document.read_only
    selected = bool(view.selected_annotations())
    has_text = view.has_text_selection()
    at = (pos.x(), pos.y()) if pos is not None else None
    number = page + 1

    unlocked = view.can_cut()  # locked annotations stay put: Cut and Delete leave them
    _quick_row(menu, [
        ("copy", "Copy (Ctrl+C)", view.copy, selected or has_text),
        ("scissors", "Cut (Ctrl+X)", view.cut_annotations, unlocked and editable),
        ("clipboard-text", "Paste (Ctrl+V)", lambda: view.paste(page, at), view.can_paste() and editable),
        ("trash", "Delete (Del)", view.delete_selected, unlocked and editable),
    ])
    menu.addSeparator()

    shown = _annotation_actions(menu, view, annotation, editable)
    if has_text and editable:
        for name, label, icon_name in MARKUPS:
            _action(menu, icon_name, label, lambda n=name: view.mark_text_selection(n))
    if has_text:
        _action(menu, "list-bullets", "Key points of the selection…", lambda: _summarise_selection(view))
    if shown or has_text:
        menu.addSeparator()

    special = menu.addMenu(icon("clipboard"), "Copy/Paste as")
    _action(special, "text-aa", "Copy selected text", view.copy_selection, has_text)
    _action(special, "image", "Copy page as image", lambda: actions.copy_page_image(page), actions is not None)
    special.addSeparator()
    _action(special, "text-t", "Paste text as text box", lambda: actions.paste_text_box(page, at),
            bool(clipboard.text()) and editable)
    _action(special, "image", "Paste image here", lambda: view.paste_image(page, at),
            clipboard.has_image() and editable)
    _action(special, "image", "Paste image as new page", lambda: actions.paste_image_page(page),
            clipboard.image() is not None and editable)
    menu.addSeparator()

    pages = menu.addMenu(icon("file"), f"Page {number}")
    _action(pages, "file-plus", f"Add page (after page {number})", lambda: actions.add_page(page, "blank"), editable)
    _action(pages, "files", "Copy page", lambda: actions.copy_page(page), actions is not None and not view.document.read_only)
    _action(pages, "copy-simple", "Duplicate page", lambda: actions.duplicate(page), editable)
    _action(pages, "file-minus", f"Delete page {number}", lambda: actions.delete(page),
            editable and view.document.page_count > 1)
    pages.addSeparator()
    _action(pages, "arrow-clockwise", "Rotate page (Clockwise)", lambda: actions.rotate(page, 90), editable)
    _action(pages, "arrow-counter-clockwise", "Rotate page (Counter-clockwise)", lambda: actions.rotate(page, -90),
            editable)
    pages.addSeparator()
    _action(pages, "arrows-out-line-horizontal", "Extend margin", lambda: actions.extend_margin(page), editable)
    _action(pages, "drop-half", "Watermark…", lambda: actions.watermark(page), editable)
    if actions is not None and actions.ocr_available():  # shown only where Windows can recognise text
        _action(pages, "magnifying-glass-plus", "Run OCR (text recognition)", lambda: actions.run_ocr(page), editable)

    insert = menu.addMenu(icon("plus-circle"), "Insert")
    for kind, label in TEMPLATES.items():
        _action(insert, "file-plus", label, lambda k=kind: actions.add_page(page, k), editable)
    insert.addSeparator()
    _action(insert, "file-pdf", "Pages from PDF file…", lambda: actions.insert_pdf(page), editable)
    _action(insert, "image", "Image as page…", lambda: actions.insert_image(page), editable)
    _action(insert, "clipboard-text", "Paste copied pages", lambda: actions.paste_pages(page),
            clipboard.pasted_pages() is not None and editable)
    insert.addSeparator()
    _action(insert, "image", "Image here…", lambda: view.insert_image_file(page, at), editable)
    _action(insert, "text-t", "Text box here", lambda: view.new_text_box(page, at), editable)
    _action(insert, "note", "Note here", lambda: view.new_note(page, at), editable)
    _action(insert, "z-equation", "Equation here…", lambda: view.insert_at("equation", page, at or (72.0, 72.0), None),
            editable)
    _action(insert, "z-signature", "Signature here…",
            lambda: view.insert_at("signature", page, at or (72.0, 72.0), QCursor.pos()), editable)
    _action(insert, "microphone", "Audio note here…",
            lambda: view.insert_at("audio", page, at or (72.0, 72.0), QCursor.pos()), editable)
    insert.addSeparator()
    _action(insert, "bookmark-simple", f"Bookmark page {number}…", lambda: actions.add_bookmark(page), editable)
    menu.addSeparator()

    _action(menu, "selection-all", "Select all annotations", lambda: view.select_all_annotations(page))
    _action(menu, "text-aa", "Select all text", lambda: view.select_all_text(page))
    return explain_menu(menu)
