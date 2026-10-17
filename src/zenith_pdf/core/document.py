"""An open PDF: PDFium for display and text, pikepdf for saving.

The file is read into memory once and neither library keeps it open, so it
can be overwritten on save (Windows refuses to replace a file that is open).
Annotations Zenith PDF can edit are hidden in PDFium's copy and drawn by the
canvas instead; everything else (links, form fields, stamps) PDFium renders.
"""

from __future__ import annotations

import atexit
import io
import logging
import os
import re
import secrets
import threading
import uuid
import warnings
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from PySide6.QtGui import QImage

from . import forms, pages, textclean
from .annotations import Annotation, ImageStamp, Point, Rect, merge_line_rects, new_id
from .appearance import pdf_rect
from .geometry import Box, PageGeometry, visible_box
from .pdf_annots import SUPPORTED, groups_of, picture_parts, read_annotation, write_page
from .render_worker import PAGE_CACHE, hide_annotations, render_region

log = logging.getLogger(__name__)

# PDFium is not thread-safe: every call into it, from any thread, holds this lock.
PDFIUM_LOCK = threading.RLock()


_BEFORE_EXIT: list[Callable[[], None]] = []


def _run_before_exit() -> None:
    for callback in reversed(_BEFORE_EXIT):
        try:
            callback()
        except Exception:
            log.warning("a step on the way out failed", exc_info=True)


def before_exit(callback: Callable[[], None]) -> None:
    """Run `callback` as soon as the interpreter starts to exit, before the atexit callbacks.

    pypdfium2 closes the documents still open from weakref finalizers, which run among
    the atexit callbacks: a thread still reading a PDF then crashes the process. Threads
    that use PDFium stop here, first. (threading's own exit hook runs before atexit;
    atexit itself is the fallback.)
    """
    if not _BEFORE_EXIT:
        register = getattr(threading, "_register_atexit", None) or atexit.register
        register(_run_before_exit)
    _BEFORE_EXIT.append(callback)

PdfiumError = pdfium.PdfiumError
WORD_REACH = 200  # characters a word may run on either side of the one clicked


class DocumentClosed(RuntimeError):
    pass


class ReadOnlyDocument(RuntimeError):
    pass


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    page: int | None
    point: Point | None = None  # the spot on the page, in its PDF space (see Document.display_y)


@dataclass(frozen=True)
class SearchHit:
    page: int
    rects: tuple[tuple[float, float, float, float], ...]  # display space x, y, w, h
    context: str


class Document:
    def __init__(self, path: str | Path, password: str | None = None, data: bytes | None = None):
        """Open the PDF at `path`, or, given `data`, a document not yet saved there."""
        self.path = Path(path)
        self.password = password  # the one that opens self.data; render processes need it too
        self.unsaved = data is not None  # a new document: the first save asks where
        # How saving encrypts: None keeps what the file had when opened, False removes
        # encryption, a pikepdf.Encryption sets new passwords (see set_security). Only
        # save() applies it: snapshots for undo never take it (see _serialize).
        self.security: pikepdf.Encryption | bool | None = None
        self._passwords = [password] if password else []  # every password a snapshot to restore may need
        self._closed = False
        self._pdf = None
        self._source = None
        # Layers shown or hidden here unlike the file says (index -> shown): only the pages on screen change,
        # never what is saved.
        self.layer_changes: dict[int, bool] = {}
        self.view_only = False  # the reader chose to only read: nothing can be changed until they choose again
        self._load(data if data is not None else self.path.read_bytes())
        self._opened_encrypted = self._source is not None and self._source.is_encrypted
        # The file's encryption as settings, for a save from bytes that lost it (see _encryption).
        self._own_encryption = _rebuilt_encryption(self._source, self.data, password) if self._opened_encrypted else None

    def _load(self, data: bytes) -> None:
        """(Re)build everything from the bytes of a PDF: display copy, editable copy, annotations."""
        self.data = data
        self._display: bytes | None = None  # the bytes the pages are drawn from, when layers differ from data's
        self.key = uuid.uuid4().hex  # names this version to the render processes
        # Bytes that load back into exactly the editable PDF, annotation ids included, while
        # nothing has changed: snapshot() hands them out instead of serializing again.
        self._clean: bytes | None = data
        self._geometry: dict[int, PageGeometry] = {}
        self._pages: OrderedDict[int, object] = OrderedDict()  # parsed pages, most recent last
        self._textpages: dict[int, object] = {}
        self._raw_texts: dict[int, str | None] = {}  # page -> its text when it needs cleaning (see _raw_text)
        self.hidden: dict[int, list[int]] = {}  # page -> /Annots positions the canvas draws itself
        self._fields: list | None = None  # the form's fields, read the first time they are asked for
        with PDFIUM_LOCK:
            self._pdf = pdfium.PdfDocument(data, password=self.password)
            # Forms must be initialised before any page is loaded, or they don't render.
            self._pdf.init_forms()
            self._sizes = [self._pdf.get_page_size(i) for i in range(len(self._pdf))]
            title = self._pdf.get_metadata_value("Title").strip()
        self.title = title or self.path.stem

        self.annotations: list[Annotation] = []
        self._sources: dict[str, pikepdf.Dictionary] = {}  # annotation id -> its PDF dictionary
        self._owned: dict[int, list[str]] = {}  # page -> ids of managed annotations, in /Annots order
        self._saved: dict[str, Annotation] = {}  # last value written for each id
        self._pictures: dict[str, pikepdf.Stream] = {}  # image digest -> its image object in the PDF
        try:
            with warnings.catch_warnings():  # bytes without encryption need no password: pikepdf warns
                warnings.simplefilter("ignore", UserWarning)
                self._source = pikepdf.open(io.BytesIO(data), password=self.password or "")
        except pikepdf.PdfError as error:
            log.warning("%s opens read-only: %s", self.path, error)
            self._source = None
        if self._source is not None and len(self._source.pages) != self.page_count:
            log.warning("%s opens read-only: page counts disagree", self.path)
            self._source = None
        if self._source is not None:
            self._import_annotations()
        if self.layer_changes:
            self._draw_layers()

    @property
    def display_data(self) -> bytes:
        """The bytes the render processes draw: the document's own, with the layers as shown here."""
        return self._display if self._display is not None else self.data

    def _layer_config(self):
        """The groups of the PDF's layers, and the file's default visibility of each."""
        if self._source is None:
            return [], []
        properties = self._source.Root.get("/OCProperties")
        if not isinstance(properties, pikepdf.Dictionary):
            return [], []
        groups = [g for g in properties.get("/OCGs", pikepdf.Array()) if isinstance(g, pikepdf.Dictionary)]
        config = properties.get("/D", pikepdf.Dictionary())
        off = {g.objgen for g in config.get("/OFF", pikepdf.Array()) if isinstance(g, pikepdf.Dictionary)}
        on = {g.objgen for g in config.get("/ON", pikepdf.Array()) if isinstance(g, pikepdf.Dictionary)}
        hidden_first = str(config.get("/BaseState", "/ON")) == "/OFF"
        defaults = [(g.objgen in on) if hidden_first else (g.objgen not in off) for g in groups]
        return groups, defaults

    def layers(self) -> list[tuple[str, bool]]:
        """The PDF's layers (optional content), each (name, shown here)."""
        groups, defaults = self._layer_config()
        return [(str(group.get("/Name", f"Layer {i + 1}")), self.layer_changes.get(i, defaults[i]))
                for i, group in enumerate(groups)]

    def set_layer_shown(self, index: int, shown: bool) -> None:
        """Show or hide a layer on screen. The file keeps its own setting: saving does not change it."""
        _, defaults = self._layer_config()
        if not 0 <= index < len(defaults):
            return
        if shown == defaults[index]:
            self.layer_changes.pop(index, None)
        else:
            self.layer_changes[index] = shown
        with PDFIUM_LOCK:
            self._draw_layers()

    def _draw_layers(self) -> None:
        """Reopen the display copy with the layers as shown here. The caller holds PDFIUM_LOCK (or is _load)."""
        display = None
        if self.layer_changes:
            with pikepdf.open(io.BytesIO(self.data), password=self.password or "") as copy:
                properties = copy.Root.get("/OCProperties")
                groups = [g for g in properties.get("/OCGs", pikepdf.Array()) if isinstance(g, pikepdf.Dictionary)]
                shown = [shown for _, shown in self.layers()]
                config = properties.get("/D", pikepdf.Dictionary())
                config.BaseState = pikepdf.Name.ON
                config.ON = pikepdf.Array([g for g, on in zip(groups, shown) if on])
                config.OFF = pikepdf.Array([g for g, on in zip(groups, shown) if not on])
                if "/AS" in config:
                    del config["/AS"]  # its automatic states (for print, say) would override the choice
                properties.D = config
                out = io.BytesIO()
                copy.save(out)
                display = out.getvalue()
        if display == self._display:
            return
        self._display = display
        self._unload()
        self._pages.clear()
        if self._pdf is not None:
            self._pdf.close()
        self._pdf = pdfium.PdfDocument(self.display_data, password=self.password)
        self._pdf.init_forms()
        self.key = uuid.uuid4().hex  # the render processes open this version
        hide_annotations(self._pdf, self.hidden)

    def _unload(self) -> None:
        with PDFIUM_LOCK:
            for textpage in self._textpages.values():
                textpage.close()
            for page in self._pages.values():
                page.close()
            self._textpages.clear()
            self._pages.clear()
            if self._pdf is not None:
                self._pdf.close()
        if self._source is not None:
            self._source.close()

    @property
    def read_only(self) -> bool:
        """Nothing can be changed: the file could not be fully parsed, or the reader chose to only read it."""
        return self._source is None or self.view_only

    @property
    def can_save(self) -> bool:
        return self._source is not None

    @property
    def page_count(self) -> int:
        return len(self._sizes)

    def page_size(self, index: int) -> tuple[float, float]:
        """Displayed page size in points, rotation included."""
        return self._sizes[index]

    def page_label(self, index: int) -> str:
        with PDFIUM_LOCK:
            label = self._pdf.get_page_label(index)
        return label or str(index + 1)

    def _page(self, index: int):
        """A parsed PDFium page from a small cache. The caller must hold PDFIUM_LOCK.

        Parsing a page's content is a large part of rendering it, and tiles render
        the same page many times, so pages stay loaded for a while.
        """
        if self._closed:
            raise DocumentClosed(str(self.path))
        if index in self._pages:
            self._pages.move_to_end(index)
        else:
            if len(self._pages) >= PAGE_CACHE:
                oldest, page = self._pages.popitem(last=False)
                textpage = self._textpages.pop(oldest, None)
                if textpage is not None:
                    textpage.close()
                page.close()
            self._pages[index] = self._pdf[index]
        return self._pages[index]

    def geometry(self, index: int) -> PageGeometry:
        if index not in self._geometry:
            with PDFIUM_LOCK:
                page = self._page(index)
                box = visible_box(_box(page.get_mediabox()), _box(page.get_cropbox()))
                self._geometry[index] = PageGeometry(box, page.get_rotation())
        return self._geometry[index]

    # Annotations --------------------------------------------------------

    def _join_groups(self, read: list) -> None:
        """Annotations another app grouped are one group here too."""
        groups = groups_of([(obj, a) for obj, a in read if not a.group])
        for position, annotation in enumerate(self.annotations):
            if annotation.id in groups:
                joined = replace(annotation, group=groups[annotation.id])
                self.annotations[position] = self._saved[annotation.id] = joined

    def _import_annotations(self) -> None:
        page_numbers = {page.obj.objgen: index for index, page in enumerate(self._source.pages)}
        for index, page in enumerate(self._source.pages):
            annots = page.obj.get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                continue
            geometry = self.geometry(index)
            hidden = []
            read = []  # (dictionary, annotation): for the groups other apps made
            for position in range(len(annots)):
                obj = annots[position]
                if not isinstance(obj, pikepdf.Dictionary):
                    continue
                try:
                    annotation = read_annotation(obj, index, geometry, page_numbers)
                except Exception:
                    log.warning("skipping unreadable annotation on page %d", index + 1, exc_info=True)
                    continue
                if annotation is None:
                    continue
                if annotation.id in self._sources:  # duplicate /NM: ids must be unique
                    annotation = replace(annotation, id=new_id())
                if not obj.is_indirect:
                    obj = self._source.make_indirect(obj)
                    annots[position] = obj
                if str(obj.get("/NM", "")) != annotation.id:
                    self._clean = None  # a new id: the bytes loaded would give another one next time
                obj.NM = pikepdf.String(annotation.id)
                if isinstance(annotation, ImageStamp):
                    # Moving a picture later reuses this image object, untouched, instead of re-encoding it.
                    self._pictures.setdefault(annotation.digest, picture_parts(obj)[0])
                self._sources[annotation.id] = obj
                self._saved[annotation.id] = annotation
                self._owned.setdefault(index, []).append(annotation.id)
                self.annotations.append(annotation)
                read.append((obj, annotation))
                hidden.append(position)
            self._join_groups(read)
            if hidden:
                self.hidden[index] = hidden
        # PDFium's annotation index is the position in /Annots, the order pikepdf sees too.
        # The render processes apply the same list to their own copies.
        with PDFIUM_LOCK:
            hide_annotations(self._pdf, self.hidden)

    def _sync(self, annotations: list[Annotation]) -> None:
        """Write the annotations that changed into the editable PDF (in memory)."""
        if self._source is None:
            raise ReadOnlyDocument(str(self.path))
        by_page: dict[int, list[Annotation]] = {}
        for annotation in annotations:
            by_page.setdefault(annotation.page, []).append(annotation)
        for index in sorted(set(by_page) | set(self._owned)):
            current = by_page.get(index, [])
            # Order counts: it is the drawing order, changed by bring to front / send to back.
            ids = [a.id for a in current]
            unchanged = ids == self._owned.get(index, []) and all(self._saved.get(a.id) == a for a in current)
            if unchanged:
                continue
            self._clean = None
            self._owned[index] = write_page(
                self._source,
                index,
                self.geometry(index),
                current,
                self._sources,
                self._owned.get(index, []),
                {a.id for a in current if self._saved.get(a.id) == a},
                self._pictures,
            )
        self._saved = {a.id: a for a in annotations}

    def _encryption(self):
        """How save() encrypts, as pikepdf takes it: new settings, False for none, or True to copy
        the encryption of the PDF in memory, which is always the file's own."""
        if self.security is not None:
            return self.security
        if not self._opened_encrypted:
            return False
        if self._source.is_encrypted:
            return True  # copied exactly, an owner password nobody typed included
        return self._own_encryption  # the PDF in memory came from bytes without it (e.g. another program's)

    def _serialize(self) -> bytes:
        """The editable PDF as bytes, for undo and page edits.

        It keeps the encryption the PDF in memory has, which is the file's own or
        none, and never one set with set_security: Undo of "Set password" or
        "Remove password" must give back a document that saves as before. Object
        streams and recompressed data are left to the file being saved: here they
        would only cost time.
        """
        buffer = pages.SaveBuffer()  # a worker thread may write it (see written)
        self._source.save(buffer, encryption=self._source.is_encrypted)
        return buffer.getvalue()

    def set_security(self, security: pikepdf.Encryption | bool | None, user_password: str = "") -> None:
        """Change how the next save encrypts the file. The file on disk changes on save."""
        self.security = security
        if user_password and user_password not in self._passwords:
            self._passwords.append(user_password)

    def info(self) -> list[tuple[str, str]]:
        """Facts about the file for the properties dialog."""
        from .pdf_tools import describe

        if self._source is None:
            return [("Pages", str(self.page_count))]
        return describe(self._source, len(self.data))

    def metadata(self) -> dict[str, str]:
        from .pdf_tools import read_metadata

        return read_metadata(self._source) if self._source is not None else {}

    def attachments(self) -> list[tuple[str, int, str]]:
        """Embedded files: (name, size in bytes, description)."""
        if self._source is None:
            return []
        result = []
        for name, spec in self._source.attachments.items():
            try:
                size = len(spec.get_file().read_bytes())
            except Exception:
                size = 0
            result.append((name, size, str(spec.description or "")))
        return result

    def extract_images(self, folder: Path) -> int:
        """Save the document's pictures in `folder`. Returns how many."""
        from .pdf_tools import extract_images

        return extract_images(self._source, folder, self.path.stem) if self._source is not None else 0

    def attachment_data(self, name: str) -> bytes:
        return self._source.attachments[name].get_file().read_bytes()

    def modified(self, annotation_id: str) -> datetime | None:
        """When an annotation was last changed, as its PDF dictionary says (/M, else /CreationDate),
        in local time; None if it doesn't say, or the annotation isn't in the PDF yet."""
        obj = self._sources.get(annotation_id)
        if obj is None:
            return None
        for key in ("/M", "/CreationDate"):
            moment = pdf_time(str(obj.get(key, "")))
            if moment is not None:
                return moment
        return None

    @property
    def encrypted(self) -> bool:
        """Whether the next save writes an encrypted file."""
        return bool(self._encryption())

    def save(self, annotations: list[Annotation], path: str | Path | None = None) -> None:
        """Write the annotations and save, replacing the file atomically."""
        self._sync(annotations)
        target = Path(path) if path else self.path
        temp = target.with_name(f"~{target.name}.tmp")
        try:
            self._source.save(temp, encryption=self._encryption())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        self.path = target
        self.unsaved = False

    # Page structure -----------------------------------------------------
    #
    # Every structural edit writes the current annotations into the PDF, changes
    # the pages with pikepdf, then reloads everything from the resulting bytes, so
    # annotations move, rotate and get copied together with their pages.

    def snapshot(self, annotations: list[Annotation]) -> bytes:
        """The whole document, annotations included, as PDF bytes.

        While nothing changes, the same bytes come back, those of the last load or
        snapshot: undo steps in a row then share one copy of the file.
        """
        self.write_annotations(annotations)
        return self.written()

    def write_annotations(self, annotations: list[Annotation]) -> None:
        """The first half of snapshot(): write the annotations that changed into the PDF in memory. Quick."""
        self._sync(annotations)

    def written(self) -> bytes:
        """The second half of snapshot(): the PDF in memory as bytes, annotations as last written.

        Writing a large file out takes a while, so a worker thread may call this, as long as
        nothing else touches the document meanwhile (Compress keeps the tab busy).
        """
        if self._clean is None:
            self._clean = self._serialize()
        return self._clean

    def restore(self, data: bytes) -> None:
        """Replace the document's content with a snapshot. `annotations` is re-read from it.

        Snapshots open with the password that opened the file, or need none; bytes
        from elsewhere may need another one used in this session, so each is tried.
        The render thread waits meanwhile: half-replaced, the document has no pages.
        """
        with PDFIUM_LOCK:
            self._unload()
            opening = self.password
            candidates = [opening] + [p for p in self._passwords if p != opening] + [None]
            for number, password in enumerate(candidates):
                try:
                    self.password = password
                    self._load(data)
                    return
                except PdfiumError as error:
                    if "password" not in str(error).lower() or number == len(candidates) - 1:
                        self.password = opening
                        raise

    def edit_pages(self, annotations: list[Annotation], operation: Callable[[pikepdf.Pdf], None],
                   before: bytes | None = None) -> None:
        """Apply `operation` to the editable PDF and reload. On failure nothing changes.

        `before` is snapshot(annotations), when the caller has just taken it.
        """
        if before is None:
            before = self.snapshot(annotations)
        else:
            self._sync(annotations)
        self._clean = None
        try:
            operation(self._source)
            if len(self._source.pages) == 0:
                raise ValueError("A PDF needs at least one page.")
            # Copied pages come without ids: name their annotations now, so the bytes
            # kept for redo give them the same ids each time they load.
            pages.name_annotations(self._source, SUPPORTED)
            after = self._serialize()
        except Exception:
            self.restore(before)
            raise
        self.restore(after)

    def form_fields(self) -> list:
        """The fields of the document's form that can be filled in (core/forms.py), in page order."""
        if self._fields is None:
            try:
                self._fields = forms.read_fields(self._source) if self._source is not None else []
            except Exception as error:  # a damaged form: the document still reads, its fields just don't fill
                log.warning("%s: the form could not be read: %s", self.path, error)
                self._fields = []
        return self._fields

    def fill_fields(self, annotations: list[Annotation], values: dict[str, object]) -> None:
        """Set form fields by name, with their appearances drawn again, as an edit of the document."""
        self.edit_pages(annotations, lambda pdf: forms.fill(pdf, values))

    def extract_pages(self, annotations: list[Annotation], indices: list[int]) -> bytes:
        """A standalone PDF with copies of these pages and their annotations, for a file.
        Links to pages that are not among them are left out."""
        self._sync(annotations)
        return pages.extract(self._source, indices)

    def copy_pages(self, annotations: list[Annotation], indices: list[int]) -> bytes:
        """Copies of these pages, as extract_pages, for the clipboard: pasted back into this
        document before its pages change, their links to other pages work again."""
        self._sync(annotations)
        return pages.extract(self._source, indices, origin=self.key)

    def insert_template(self, annotations, at: int, kind: str = "blank", size=None) -> None:
        """A new page before position `at`, as big as its neighbour unless `size` is given."""
        size = size or self.page_size(min(max(at - 1, 0), self.page_count - 1))
        self.edit_pages(annotations, lambda pdf: pdf.pages.insert(at, pages.new_page(pdf, size, kind)))

    def insert_pdf(self, annotations, at: int, data: bytes, password: str = "") -> None:
        origin = self.key  # this version's name: pages copied from it (copy_pages) find their links again
        self.edit_pages(annotations, lambda pdf: pages.insert_pdf(pdf, at, data, password, origin))

    def insert_image(self, annotations, at: int, rgb: bytes, width: int, height: int) -> None:
        self.edit_pages(annotations, lambda pdf: pdf.pages.insert(at, pages.image_page(pdf, rgb, width, height)))

    def duplicate(self, annotations, index: int) -> None:
        copy = self.copy_pages(annotations, [index])
        self.insert_pdf(annotations, index + 1, copy)

    def delete(self, annotations, indices: list[int]) -> None:
        if len(set(indices)) >= self.page_count:
            raise ValueError("A PDF needs at least one page.")

        def operation(pdf):
            for index in sorted(set(indices), reverse=True):
                del pdf.pages[index]

        self.edit_pages(annotations, operation)

    def rotate(self, annotations, indices: list[int], degrees: int) -> None:
        rotations = {i: self.geometry(i).rotation for i in indices}  # effective, inherited included
        self.edit_pages(annotations, lambda pdf: [pages.rotate(pdf, i, rotations[i], degrees) for i in indices])

    def move(self, annotations, indices: list[int], before: int) -> None:
        """Move pages so they sit, in their current order, before page `before` (old numbering)."""
        moving = sorted(set(indices))
        order = [i for i in range(self.page_count) if i not in moving]
        position = sum(1 for i in order if i < before)
        order[position:position] = moving
        self.reorder(annotations, order)

    def reorder(self, annotations, order: list[int]) -> None:
        """Put the pages in `order` (a permutation of the current indices)."""
        if sorted(order) != list(range(self.page_count)):
            raise ValueError("not a permutation of the pages")
        if order == list(range(self.page_count)):
            return

        def operation(pdf):
            ordered = [pdf.pages[i] for i in order]
            for index in reversed(range(len(pdf.pages))):
                del pdf.pages[index]
            for page in ordered:
                pdf.pages.append(page)

        self.edit_pages(annotations, operation)

    def add_text_layers(self, annotations, words_by_page: dict) -> None:
        """Invisible recognised text on several pages, in a single edit."""
        geometries = {i: self.geometry(i) for i in words_by_page}

        def operation(pdf):
            for index, words in words_by_page.items():
                if words:
                    pages.add_text_layer(pdf, index, geometries[index], words, self.text_width)

        self.edit_pages(annotations, operation)

    def char_count(self, index: int) -> int:
        with PDFIUM_LOCK:
            return self._textpage(index).count_chars()

    def extend_margin(self, annotations, index: int, fraction: float = 0.5) -> None:
        geometry = self.geometry(index)
        self.edit_pages(annotations, lambda pdf: pages.extend_margin(pdf, index, geometry, fraction))

    def watermark(self, annotations, indices: list[int], text: str, font_size: float = 72,
                  color=(0.55, 0.55, 0.6), opacity: float = 0.22, angle: float = 45) -> None:
        width = self.text_width(text, font_size)
        geometries = {i: self.geometry(i) for i in indices}

        def operation(pdf):
            for i in indices:
                pages.watermark(pdf, i, geometries[i], text, width, font_size, color, opacity, angle)

        self.edit_pages(annotations, operation)

    def flatten(self, annotations, ids: set[str]) -> None:
        """Make these annotations part of their pages' content."""
        by_page: dict[int, list[str]] = {}
        for annotation in annotations:
            if annotation.id in ids:
                by_page.setdefault(annotation.page, []).append(annotation.id)

        def operation(pdf):
            for index, page_ids in by_page.items():
                pages.flatten(pdf, index, [self._sources[i] for i in page_ids])

        self.edit_pages(annotations, operation)

    def apply_redactions(self, annotations, pictures: dict[int, bytes]) -> None:
        """Replace pages by pictures of themselves, with the redacted areas already filled in.

        `pictures` maps page indices to PNG or JPEG bytes. The redaction marks on
        those pages go, and so does any annotation or link touching a redacted area.
        """
        from .annotations import Redaction, overlaps
        from .images import xobject

        areas = {page: [r for a in annotations if isinstance(a, Redaction) and a.page == page for r in a.rects]
                 for page in pictures}
        kept = [a for a in annotations if a.page not in pictures
                or (not isinstance(a, Redaction) and not any(overlaps(a.bounds(), r) for r in areas[a.page]))]
        kept_ids = {a.id for a in kept}
        geometries = {i: self.geometry(i) for i in pictures}

        def keep(annot, page):
            if str(annot.get("/NM", "")) in kept_ids:
                return True
            if annot.get("/Subtype") != pikepdf.Name.Link or "/Rect" not in annot:
                return False  # form fields and the rest were drawn into the picture
            x0, y0, x1, y1 = (float(v) for v in annot.Rect)
            box = geometries[page].rect_to_display((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            return not any(overlaps(box, r) for r in areas[page])

        def operation(pdf):
            for index, data in pictures.items():
                pages.replace_with_picture(pdf, index, geometries[index], xobject(pdf, data),
                                           lambda annot, i=index: keep(annot, i))

        self.edit_pages(kept, operation)

    def add_text_layer(self, annotations, index: int, words) -> None:
        self.add_text_layers(annotations, {index: words})

    def text_width(self, text: str, size: float) -> float:
        from .textlayout import text_width

        return text_width(text, size)

    # Rendering ----------------------------------------------------------

    def render_region(self, index: int, scale: float, region=None, key: str | None = None) -> tuple[bytes, int, int, int]:
        """BGRA pixels of a page region (see render_worker.render_region). Thread-safe.

        With a `key`, only that version of the document renders: after a page edit
        the old page numbers mean other pages, so DocumentClosed is raised instead.
        """
        with PDFIUM_LOCK:
            if key is not None and key != self.key:
                raise DocumentClosed(f"{self.path} has changed")
            page = self._page(index)
            formenv = self._pdf.formenv.raw if self._pdf.formenv else None
            return render_region(page, formenv, self.page_size(index), scale, region)

    def render(self, index: int, scale: float) -> QImage:
        """The whole page as an image."""
        data, width, height, stride = self.render_region(index, scale)
        return QImage(data, width, height, stride, QImage.Format.Format_RGB32).copy()

    # Text ---------------------------------------------------------------

    def _textpage(self, index: int):
        """The PDFium text page of a cached page. The caller must hold PDFIUM_LOCK."""
        page = self._page(index)
        if index not in self._textpages:
            self._textpages[index] = page.get_textpage()
        return self._textpages[index]

    def char_at(self, index: int, point: Point, tolerance: float = 4.0) -> int:
        """Index of the character at a display-space point, or -1."""
        with PDFIUM_LOCK:
            x, y = self.geometry(index).to_pdf(*point)
            found = self._textpage(index).get_index(x, y, tolerance, tolerance)
        return -1 if found is None or found < 0 else found  # pypdfium2 returns None for a miss

    def word_at(self, index: int, point: Point, tolerance: float = 4.0) -> tuple[int, int] | None:
        """The first and last character of the word at a display-space point, or None if no word is there.

        A word is a run of letters and digits; textclean.WORD_MARKS inside it keep it whole, but are left off
        its ends, so "'needle'," gives needle.
        """
        import pypdfium2.raw as raw

        char = self.char_at(index, point, tolerance)
        if char < 0:
            return None
        with PDFIUM_LOCK:
            textpage = self._textpage(index).raw
            count = raw.FPDFText_CountChars(textpage)

            def letter(i: int) -> bool:
                return 0 <= i < count and textclean.in_word(chr(raw.FPDFText_GetUnicode(textpage, i)))

            def mark(i: int) -> bool:
                code = raw.FPDFText_GetUnicode(textpage, i)
                return chr(code) in textclean.WORD_MARKS or not textclean.TABLE.get(code, chr(code))

            if not letter(char):
                return None
            first = last = char
            while first > max(char - WORD_REACH, 0) and letter(first - 1):
                first -= 1
            while last < char + WORD_REACH and letter(last + 1):
                last += 1
            while first < last and mark(first):
                first += 1
            while last > first and mark(last):
                last -= 1
            if mark(first):
                return None  # a lone hyphen or apostrophe is no word
        return first, last

    def text_span(self, index: int, first: int, last: int) -> tuple[tuple[Rect, ...], str]:
        """Line rectangles (display space) and text of the characters first..last."""
        start, end = sorted((first, last))
        count = end - start + 1
        with PDFIUM_LOCK:
            geometry = self.geometry(index)
            textpage = self._textpage(index)
            rects = tuple(
                geometry.rect_to_display(textpage.get_rect(i)) for i in range(textpage.count_rects(start, count))
            )
            text = textpage.get_text_range(start, count)
            turns = self.text_turns(index, start)  # vertical lines join along their own direction
        return merge_line_rects(rects, turns), textclean.clean(text)

    def text_turns(self, index: int, char: int) -> int:
        """Quarter turns clockwise on screen of the text at character `char`, 0 to 3 (TextMarkup.rotation).

        The page's own turn when `char` is -1 or PDFium has no matrix for it.
        """
        import pypdfium2.raw as raw

        with PDFIUM_LOCK:
            geometry = self.geometry(index)
            matrix = raw.FS_MATRIX()
            if char < 0 or not raw.FPDFText_GetMatrix(self._textpage(index).raw, char, matrix):
                return geometry.rotation // 90
            return geometry.display_turns(matrix.a, matrix.b)

    def content_boxes(self, index: int, limit: int = 3000) -> list[Rect]:
        """Where the page has content, in display space: lines of text, pictures and drawings."""
        import pypdfium2.raw as raw

        boxes = []
        with PDFIUM_LOCK:
            geometry = self.geometry(index)
            page = self._page(index)
            textpage = self._textpage(index)
            count = textpage.count_chars()
        if count > 0:
            boxes += list(self.text_span(index, 0, count - 1)[0])
        with PDFIUM_LOCK:
            kinds = (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_FORM)
            for obj in page.get_objects(max_depth=1):
                if obj.type not in kinds:
                    continue
                left, bottom, right, top = obj.get_pos()
                if right - left < 6 and top - bottom < 6:
                    continue  # specks and hairlines: nothing to line up with
                boxes.append(geometry.rect_to_display((left, bottom, right, top)))
                if len(boxes) >= limit:
                    break
        return boxes

    def page_text(self, index: int) -> str:
        """All the text of a page, in reading order as PDFium sees it."""
        with PDFIUM_LOCK:
            return textclean.clean(self._textpage(index).get_text_range())

    def page_characters(self, index: int) -> tuple[str, bool]:
        """The page's text with one string position per PDFium character, and whether that holds: a
        character outside the Basic Multilingual Plane takes two positions, and then it doesn't."""
        with PDFIUM_LOCK:
            textpage = self._textpage(index)
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
        return text, textclean.units(text) == count == len(text)

    def _raw_text(self, index: int) -> str | None:
        """The page's text as PDFium has it, if cleaning would change it; else None.
        The caller must hold PDFIUM_LOCK."""
        if index not in self._raw_texts:
            textpage = self._textpage(index)
            text = textpage.get_text_range()
            # Positions in the text must be PDFium's character indices, or hits would land elsewhere.
            usable = textclean.needs_cleaning(text) and textclean.units(text) == textpage.count_chars()
            self._raw_texts[index] = text if usable else None
        return self._raw_texts[index]

    def text_in(self, index: int, rects) -> str:
        """Text inside display-space rectangles, e.g. the words under a highlight.

        Every character whose middle lies in one of the rectangles counts, once, in reading order, so a PDF that
        places each letter on its own (one rectangle per letter) or rectangles that overlap read as plain words.
        """
        with PDFIUM_LOCK:
            geometry = self.geometry(index)
            textpage = self._textpage(index)
            areas = [pdf_rect(geometry, rect) for rect in rects]
            inside = []
            for char in range(textpage.count_chars()):
                left, bottom, right, top = textpage.get_charbox(char)
                if right <= left and top <= bottom:
                    continue  # a space or line break PDFium made up: it has no box
                x, y = (left + right) / 2, (bottom + top) / 2
                if any(a <= x <= c and b <= y <= d for a, b, c, d in areas):
                    inside.append(char)
            runs = []  # unbroken runs of characters, each read as PDFium spells it
            for char in inside:
                if runs and char == runs[-1][1] + 1:
                    runs[-1][1] = char
                else:
                    runs.append([char, char])
            parts = [textpage.get_text_range(first, last - first + 1) for first, last in runs]
        return " ".join(" ".join(textclean.clean(part).split()) for part in parts if part.strip())

    def display_y(self, index: int, point: Point | None) -> float | None:
        """How far down page `index` a destination's spot is, in display points; None stays None."""
        if point is None:
            return None
        return self.geometry(index).to_display(*point)[1]

    def link_at(self, index: int, point: Point) -> tuple[str, object, Point | None] | None:
        """The PDF's own link at a display-space point: ("uri", address, None), or ("page", index, spot)
        with the spot it shows on that page in its PDF space (None for the whole page).

        Links Zenith PDF manages are hidden in PDFium's copy and answered by the
        canvas instead, so hidden ones are skipped here.
        """
        import ctypes

        import pypdfium2.raw as raw

        with PDFIUM_LOCK:
            page = self._page(index)
            x, y = self.geometry(index).to_pdf(*point)
            link = raw.FPDFLink_GetLinkAtPoint(page.raw, x, y)
            if not link:
                return None
            annot = raw.FPDFLink_GetAnnot(page.raw, link)
            if annot and raw.FPDFAnnot_GetFlags(annot) & 2:  # hidden: one of ours
                raw.FPDFPage_CloseAnnot(annot)
                return None
            if annot:
                raw.FPDFPage_CloseAnnot(annot)
            document = self._pdf.raw
            destination = raw.FPDFLink_GetDest(document, link)
            if not destination:
                action = raw.FPDFLink_GetAction(link)
                if not action:
                    return None
                kind = raw.FPDFAction_GetType(action)
                if kind == raw.PDFACTION_URI:
                    size = raw.FPDFAction_GetURIPath(document, action, None, 0)
                    buffer = ctypes.create_string_buffer(size)
                    raw.FPDFAction_GetURIPath(document, action, buffer, size)
                    return "uri", buffer.value.decode("utf-8", "replace"), None
                if kind != raw.PDFACTION_GOTO:
                    return None
                destination = raw.FPDFAction_GetDest(document, action)
            target = raw.FPDFDest_GetDestPageIndex(document, destination) if destination else -1
            spot = _view_point(destination) if destination else None
        return ("page", target, spot) if 0 <= target < self.page_count else None

    # Bookmarks ----------------------------------------------------------
    #
    # Bookmarks are numbered in the order outline() lists them (depth first),
    # which is also the order qpdf walks the outline in.

    def _walk_outline(self, items, found: list) -> None:
        for position, item in enumerate(items):
            found.append((items, position, item))
            self._walk_outline(item.children, found)

    def add_bookmark(self, annotations, title: str, page: int) -> None:
        """A top-level bookmark to `page`, placed among the others in page order."""
        def page_of(pdf, item, numbers) -> int:
            destination = item.destination
            if isinstance(destination, pikepdf.Array) and len(destination):
                return numbers.get(destination[0].objgen, 10**9)
            return 10**9

        def operation(pdf):
            numbers = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
            with pdf.open_outline() as outline:
                position = next((i for i, item in enumerate(outline.root) if page_of(pdf, item, numbers) > page),
                                len(outline.root))
                outline.root.insert(position, pikepdf.OutlineItem(title, page))

        self.edit_pages(annotations, operation)

    def rename_bookmark(self, annotations, number: int, title: str) -> None:
        def operation(pdf):
            with pdf.open_outline() as outline:
                found: list = []
                self._walk_outline(outline.root, found)
                found[number][2].title = title

        self.edit_pages(annotations, operation)

    def delete_bookmark(self, annotations, number: int) -> None:
        """Remove a bookmark; its children move up to take its place."""
        def operation(pdf):
            with pdf.open_outline() as outline:
                found: list = []
                self._walk_outline(outline.root, found)
                items, position, item = found[number]
                items[position:position + 1] = list(item.children)

        self.edit_pages(annotations, operation)

    def outline(self) -> list[OutlineEntry]:
        entries = []
        with PDFIUM_LOCK:
            for bookmark in self._pdf.get_toc():
                dest = bookmark.get_dest()
                page = dest.get_index() if dest is not None else None
                point = _view_point(dest.raw) if dest is not None else None
                entries.append(OutlineEntry(bookmark.level, bookmark.get_title(), page, point))
        return entries

    def iter_search(
        self,
        text: str,
        match_case: bool = False,
        whole_word: bool = False,
        cancelled: threading.Event | None = None,
    ) -> Iterator[SearchHit]:
        """Yield every occurrence of `text`, page by page.

        The lock is released between pages so rendering can interleave with a long search.
        It stops when the pages change meanwhile: the numbers it walks would mean other pages.
        """
        key = self.key
        for index in range(self.page_count):
            if cancelled is not None and cancelled.is_set():
                return
            with PDFIUM_LOCK:
                if self._closed or self.key != key:
                    return
                geometry = self.geometry(index)
                hits = list(self._search_page(index, text, match_case, whole_word, geometry))
            yield from hits

    def _search_page(self, index, text, match_case, whole_word, geometry) -> list[SearchHit]:
        textpage = self._textpage(index)
        found = []
        searcher = textpage.search(text, match_case=match_case, match_whole_word=whole_word)
        try:
            while (occurrence := searcher.get_next()) is not None:
                found.append(occurrence)
        finally:
            searcher.close()
        raw = self._raw_text(index)
        if raw is not None:
            # PDFium compares raw codes: "Qualifiers" misses "Quali\x1cers". Search the
            # cleaned text too, and keep what PDFium did not find already.
            for start, count in textclean.find(raw, text, match_case, whole_word):
                if not any(s < start + count and start < s + c for s, c in found):
                    found.append((start, count))
            found.sort()
        hits = []
        for start, count in found:
            rects = tuple(
                geometry.rect_to_display(textpage.get_rect(i))
                for i in range(textpage.count_rects(start, count))
            )
            before = max(0, start - 40)
            context = textclean.clean(textpage.get_text_range(before, start - before + count + 40))
            hits.append(SearchHit(index, rects, " ".join(context.split())))
        return hits

    def close(self) -> None:
        with PDFIUM_LOCK:
            if self._closed:
                return
            self._closed = True
        self._unload()


def _box(values) -> Box:
    left, bottom, right, top = values
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _view_point(destination) -> Point | None:
    """The spot a PDFium destination shows, in its page's PDF space: (x, y) for /XYZ (a null x is 0) and
    (0, y) for /FitH and /FitBH. None for the other views and a null height: the whole page.
    The caller holds PDFIUM_LOCK."""
    import ctypes

    import pypdfium2.raw as raw

    count = ctypes.c_ulong()
    values = (raw.FS_FLOAT * 4)()
    mode = raw.FPDFDest_GetView(destination, count, values)
    if mode == raw.PDFDEST_VIEW_XYZ:
        has_x, has_y, has_zoom = raw.FPDF_BOOL(), raw.FPDF_BOOL(), raw.FPDF_BOOL()
        x, y, zoom = raw.FS_FLOAT(), raw.FS_FLOAT(), raw.FS_FLOAT()
        if not raw.FPDFDest_GetLocationInPage(destination, has_x, has_y, has_zoom, x, y, zoom) or not has_y.value:
            return None
        return (float(x.value) if has_x.value else 0.0), float(y.value)
    if mode in (raw.PDFDEST_VIEW_FITH, raw.PDFDEST_VIEW_FITBH) and count.value >= 1:
        return 0.0, float(values[0])
    return None


_PDF_DATE = re.compile(r"(?:D:)?(\d{4})(\d\d)?(\d\d)?(\d\d)?(\d\d)?(\d\d)?\s*(?:([Zz+-])(\d\d)?'?(\d\d)?'?)?")


def pdf_time(text: str) -> datetime | None:
    """A PDF date ("D:20261016140512+02'00'", parts after the year optional) in local time, or None.
    Without a time zone it is taken as local."""
    match = _PDF_DATE.match(text.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second, sign, zone_hours, zone_minutes = match.groups()
    try:
        moment = datetime(int(year), int(month or 1), int(day or 1), int(hour or 0), int(minute or 0),
                          int(second or 0))
        if sign in ("Z", "z"):
            moment = moment.replace(tzinfo=timezone.utc)
        elif sign:
            offset = timedelta(hours=int(zone_hours or 0), minutes=int(zone_minutes or 0))
            moment = moment.replace(tzinfo=timezone(offset if sign == "+" else -offset))
        return moment.astimezone()
    except (ValueError, OverflowError):
        return None


def _opens_without_password(data: bytes) -> bool:
    try:
        with pikepdf.open(io.BytesIO(data), password=""):
            return True
    except pikepdf.PasswordError:
        return False


def _rebuilt_encryption(pdf: pikepdf.Pdf, data: bytes, password: str | None) -> pikepdf.Encryption:
    """The encryption of an opened file as settings, to save it from a PDF that no longer has it.

    Only the passwords someone typed are known. An owner password nobody typed
    becomes a new random one, which keeps the permissions. A user password that
    can't be worked out becomes the password that opened the file, so the file
    never ends up opening without one.
    """
    info = pdf.encryption
    typed = password or ""
    if pdf.user_password_matched:
        user = typed
    elif info.user_password:  # up to revision 4, the owner password gives it away
        user = info.user_password.decode("latin-1")
    else:
        user = "" if _opens_without_password(data) else typed
    owner = typed if pdf.owner_password_matched and typed else secrets.token_urlsafe(24)
    revision = 6 if info.R >= 5 else max(int(info.R), 2)
    aes = revision == 6 or (revision == 4 and info.stream_method.name != "rc4")
    # Revisions 2 and 3 know only RC4 and have no switch for the metadata: pikepdf refuses either setting for them.
    metadata = revision >= 4
    return pikepdf.Encryption(owner=owner, user=user, R=revision, allow=pdf.allow, aes=aes, metadata=metadata)
