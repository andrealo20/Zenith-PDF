"""Page commands shared by the context menu, the page manager and the keyboard."""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QProgressDialog

from ..core import ocr, textlayout
from ..core.annotations import FreeText
from ..core.pages import TEMPLATES
from . import clipboard, messages
from .dialogs import WatermarkDialog

if TYPE_CHECKING:
    from .workspace import Workspace

IMAGE_FILES = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"
COPY_IMAGE_SCALE = 2.0  # 144 dpi


def rgb_bytes(image: QImage) -> tuple[bytes, int, int]:
    """Tightly packed RGB rows, as a PDF image wants them (QImage pads rows to 4 bytes)."""
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = image.width(), image.height()
    rows = (bytes(image.constScanLine(y))[: width * 3] for y in range(height))
    return b"".join(rows), width, height


class PageActions(QObject):
    def __init__(self, workspace: Workspace):
        super().__init__(workspace)
        self._workspace = workspace
        self._ocr_available: bool | None = None

    @property
    def _document(self):
        return self._workspace.document

    @property
    def _view(self):
        return self._workspace.view

    def _edit(self, label: str, edit, then_page: int | None = None) -> bool:
        done = self._workspace.edit_structure(label, edit)
        if done and then_page is not None:
            self._view.goto_page(min(then_page, self._document.page_count - 1))
        return done

    # Forms --------------------------------------------------------------

    def fill_field(self, name: str, value) -> bool:
        return self._workspace.edit_structure("Fill in form", lambda a: self._document.fill_fields(a, {name: value}),
                                              "Can't fill in the form")

    # Pages --------------------------------------------------------------

    def add_page(self, after: int, kind: str = "blank") -> None:
        label = f"Add {TEMPLATES[kind].lower()}"
        self._edit(label, lambda a: self._document.insert_template(a, after + 1, kind), after + 1)

    def duplicate(self, page: int) -> None:
        self._edit("Duplicate page", lambda a: self._document.duplicate(a, page), page + 1)

    def delete(self, page: int) -> None:
        self.delete_pages([page])

    def delete_pages(self, pages: list[int]) -> None:
        label = "Delete page" if len(pages) == 1 else f"Delete {len(pages)} pages"
        self._edit(label, lambda a: self._document.delete(a, pages), min(pages))

    def rotate(self, page: int, degrees: int) -> None:
        self.rotate_pages([page], degrees)

    def rotate_pages(self, pages: list[int], degrees: int) -> None:
        self._edit("Rotate page" if len(pages) == 1 else "Rotate pages", lambda a: self._document.rotate(a, pages, degrees))

    def reorder(self, order: list[int]) -> None:
        self._edit("Reorder pages", lambda a: self._document.reorder(a, order))

    def extend_margin(self, page: int) -> None:
        self._edit("Extend margin", lambda a: self._document.extend_margin(a, page))

    def copy_page(self, page: int) -> None:
        # copy_pages, not extract_pages: pasted back into this document, the copy keeps its links to other pages.
        clipboard.copy_pages(self._document.copy_pages(self._workspace.store.all(), [page]))

    def paste_pages(self, after: int) -> None:
        data = clipboard.pasted_pages()
        if data:
            self._edit("Paste pages", lambda a: self._document.insert_pdf(a, after + 1, data), after + 1)

    def extract_pages(self, pages: list[int]) -> None:
        """Save copies of these pages, with their annotations, as a new PDF."""
        default = self._document.path.with_name(f"{self._document.path.stem} (pages).pdf")
        target, _ = QFileDialog.getSaveFileName(self._view, "Save pages as", str(default), "PDF documents (*.pdf)")
        if not target:
            return
        try:
            Path(target).write_bytes(self._document.extract_pages(self._workspace.store.all(), sorted(pages)))
        except OSError as error:  # a read-only folder, or the file open in another program
            messages.warn(self._view, "Can't save the pages", f"{Path(target).name} was not written.",
                          detail=error.strerror or str(error))

    def insert_pdf(self, after: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self._view, "Insert pages from", str(self._document.path.parent),
                                              "PDF documents (*.pdf)")
        if not path:
            return
        name = Path(path).name
        try:
            data = Path(path).read_bytes()
        except OSError as error:
            messages.warn(self._view, "Can't insert the pages", f"{name} could not be read.",
                          detail=error.strerror or str(error))
            return
        password, tried = "", False
        while True:  # ask for the password until it opens or the user gives up
            try:
                with pikepdf.open(io.BytesIO(data), password=password):
                    break
            except pikepdf.PasswordError:
                prompt = ("That password is not right. Try again." if tried
                          else f"{name} is protected. Type its password to insert its pages.")
                password = messages.get_text(self._view, "Password required", prompt, password=True,
                                             action="Insert", required=True)
                if password is None:
                    return
                tried = True
            except Exception as error:  # not a PDF, or damaged beyond repair
                messages.warn(self._view, "Can't insert the pages", f"{name} could not be opened as a PDF.",
                              detail=str(error))
                return
        self._edit("Insert pages", lambda a: self._document.insert_pdf(a, after + 1, data, password), after + 1)

    def insert_image(self, after: int) -> None:
        path, _ = QFileDialog.getOpenFileName(self._view, "Insert image", str(self._document.path.parent), IMAGE_FILES)
        if path:
            self._insert_image(after, QImage(path))

    def paste_image_page(self, after: int) -> None:
        image = clipboard.image()
        if image is not None:
            self._insert_image(after, image)

    def _insert_image(self, after: int, image: QImage) -> None:
        if image.isNull():
            messages.warn(self._view, "Can't insert the image", "The picture could not be read.")
            return
        rgb, width, height = rgb_bytes(image)
        self._edit("Insert image", lambda a: self._document.insert_image(a, after + 1, rgb, width, height), after + 1)

    # Bookmarks ----------------------------------------------------------

    def add_bookmark(self, page: int) -> None:
        title = messages.get_text(self._view, f"Bookmark page {page + 1}", "Name the bookmark, to find the page "
                                  "again in the Bookmarks panel.", self._title_of(page), action="Add", required=True)
        if title is not None and title.strip():
            if self._edit("Add bookmark", lambda a: self._document.add_bookmark(a, title.strip(), page)):
                self._workspace.notify(f"Bookmark \"{title.strip()}\" added. It is in the Bookmarks panel.")

    def _title_of(self, page: int) -> str:
        """A first guess at a bookmark's name: the page's first line of text, else its number."""
        text = self._document.page_text(page).strip().splitlines()
        first = next((line.strip() for line in text if len(line.strip()) > 2), "")
        return first[:60] or f"Page {page + 1}"

    def rename_bookmark(self, number: int, title: str) -> None:
        self._edit("Rename bookmark", lambda a: self._document.rename_bookmark(a, number, title))

    def delete_bookmark(self, number: int) -> None:
        self._edit("Delete bookmark", lambda a: self._document.delete_bookmark(a, number))

    def watermark(self, page: int) -> None:
        dialog = WatermarkDialog(self._view)
        accepted = dialog.exec() == WatermarkDialog.DialogCode.Accepted
        options = dialog.options()
        dialog.deleteLater()
        if not accepted:
            return
        pages = list(range(self._document.page_count)) if options.pop("all_pages") else [page]
        self._edit("Add watermark", lambda a: self._document.watermark(a, pages, **options))

    # Clipboard ----------------------------------------------------------

    def copy_page_image(self, page: int) -> None:
        QApplication.clipboard().setImage(self._view.page_image(page, COPY_IMAGE_SCALE))

    def paste_text_box(self, page: int, at) -> None:
        text = clipboard.text()
        if not text:
            return
        style = self._workspace.tool_settings.style_for("text")
        x, y = at or (72.0, 72.0)
        width = max(min(260.0, self._view.page_width(page) - x), 60.0)
        box = FreeText(
            page=page,
            rect=(x, y, width, textlayout.fitted_height(text, style.font_size, width, style.face())),
            contents=text,
            font_size=style.font_size,
            color=style.color,
            fill=style.fill,
            **style.text_format(),
            **self._view.common("text"),
        )
        self._view.add_annotations([box], box.label)

    # Text recognition ---------------------------------------------------

    def ocr_available(self) -> bool:
        if self._ocr_available is None:
            self._ocr_available = ocr.available()
        return self._ocr_available

    def run_ocr(self, page: int) -> None:
        document = self._document
        answer = messages.ask(
            self._view, "Recognise text", "Make the words of scanned pages searchable and selectable. Pages that "
            "already have text are left as they are when you choose all pages.",
            [("page", "This page", "primary"), ("all", "All pages", ""), (messages.CANCEL, "Cancel", "")],
            icon="magnifying-glass-plus")
        if answer is None:
            return
        if answer == "page":
            if document.char_count(page) > 0 and not messages.confirm(
                    self._view, "This page already has text", "Recognise it anyway? The recognised words are added "
                    "as a second, invisible layer.", action="Recognise"):
                return
            pages = [page]
        else:
            pages = [i for i in range(document.page_count) if document.char_count(i) == 0]
            if not pages:
                messages.inform(self._view, "Nothing to recognise", "Every page already contains text.")
                return
        self.recognize(pages)

    def recognize(self, pages: list[int]) -> None:
        """Recognise the text of these pages and add it as an invisible, searchable layer."""
        document = self._document
        settings = self._workspace.settings
        language = settings.get("ocr_language") if settings is not None else ""
        dpi = settings.get("ocr_dpi") if settings is not None else ocr.OCR_DPI
        progress = QProgressDialog("Recognising text…", "Cancel", 0, len(pages), self._view)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        results: dict[int, list] = {}
        failure: list[Exception] = []
        cancelled = threading.Event()

        def work():
            try:
                for index in pages:
                    if cancelled.is_set():
                        return
                    results[index] = ocr.recognize_page(document, index, language, dpi)
            except Exception as error:  # reported on the GUI thread below
                failure.append(error)

        worker = threading.Thread(target=work, name="ocr", daemon=True)
        worker.start()
        while worker.is_alive():
            progress.setValue(len(results))
            QApplication.processEvents()
            if progress.wasCanceled():
                cancelled.set()
            worker.join(0.05)
        progress.close()
        progress.deleteLater()
        if failure:
            messages.warn(self._view, "Text recognition failed", "Nothing was added to the pages.",
                          detail=str(failure[0]))
            return
        if cancelled.is_set() or not any(results.values()):
            if not cancelled.is_set():
                messages.inform(self._view, "No text was found", "The pages look empty to the text recognition.")
            return
        words = sum(len(w) for w in results.values())
        if self._edit("Recognise text", lambda a: document.add_text_layers(a, results)):
            QTimer.singleShot(0, lambda: self._workspace.notify(f"Recognised {words} words on {len(results)} page(s)."))
