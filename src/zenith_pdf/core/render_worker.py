"""Page rasterisation shared by the in-process renderer and the worker processes.

This module must not import Qt: worker processes import it at start-up and
should be ready in a few tens of milliseconds.
"""

from __future__ import annotations

import ctypes
import gc
import math
import multiprocessing
import queue
import traceback
from collections import OrderedDict
from multiprocessing import shared_memory

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

PAGE_CACHE = 6  # parsed pages kept per document: tiles of one page reuse the parse
PARENT_CHECK_SECONDS = 2.0


def full_size(page_size: tuple[float, float], scale: float) -> tuple[int, int]:
    """Pixel size of a whole page rendered at `scale`, as PDFium and the canvas both compute it."""
    return math.ceil(page_size[0] * scale), math.ceil(page_size[1] * scale)


def render_region(page, formenv, page_size, scale: float, region) -> tuple[bytes, int, int, int]:
    """Rasterise part of a page: `region` is (x, y, w, h) in pixels of the whole page
    rendered at `scale`, or None for all of it. Returns BGRA bytes, width, height, stride.

    Rendering a region is how tiles work: PDFium draws the full page geometry
    offset by (-x, -y) into a bitmap the size of the region, and skips the rest.
    """
    full_w, full_h = full_size(page_size, scale)
    x, y, w, h = region if region is not None else (0, 0, full_w, full_h)
    bitmap = pdfium_c.FPDFBitmap_Create(w, h, 1)
    if not bitmap:
        raise MemoryError(f"PDFium could not allocate a {w}x{h} bitmap")
    try:
        pdfium_c.FPDFBitmap_FillRect(bitmap, 0, 0, w, h, 0xFFFFFFFF)
        flags = pdfium_c.FPDF_ANNOT
        pdfium_c.FPDF_RenderPageBitmap(bitmap, page, -x, -y, full_w, full_h, 0, flags)
        if formenv is not None:
            pdfium_c.FPDF_FFLDraw(formenv, bitmap, page, -x, -y, full_w, full_h, 0, flags)
        stride = pdfium_c.FPDFBitmap_GetStride(bitmap)
        data = ctypes.string_at(pdfium_c.FPDFBitmap_GetBuffer(bitmap), stride * h)
    finally:
        pdfium_c.FPDFBitmap_Destroy(bitmap)
    return data, w, h, stride


def hide_annotations(pdf, hidden: dict[int, list[int]]) -> None:
    """Set the Hidden flag on annotations the canvas draws itself (index = position in /Annots)."""
    for index, positions in hidden.items():
        page = pdf[index]
        try:
            for position in positions:
                handle = pdfium_c.FPDFPage_GetAnnot(page, position)
                if handle:
                    flags = pdfium_c.FPDFAnnot_GetFlags(handle)
                    pdfium_c.FPDFAnnot_SetFlags(handle, flags | pdfium_c.FPDF_ANNOT_FLAG_HIDDEN)
                    pdfium_c.FPDFPage_CloseAnnot(handle)
        finally:
            page.close()


class _OpenDocument:
    """A document inside a worker, read straight from the shared memory block."""

    def __init__(self, shm_name: str, size: int, password, hidden):
        self.shm = shared_memory.SharedMemory(name=shm_name)
        self.buffer = (ctypes.c_ubyte * size).from_buffer(self.shm.buf)
        self.pdf = pdfium.PdfDocument(self.buffer, password=password)
        self.pdf.init_forms()
        hide_annotations(self.pdf, hidden)
        self.pages: OrderedDict[int, object] = OrderedDict()

    def page(self, index: int):
        if index in self.pages:
            self.pages.move_to_end(index)
        else:
            if len(self.pages) >= PAGE_CACHE:
                self.pages.popitem(last=False)[1].close()
            self.pages[index] = self.pdf[index]
        return self.pages[index]

    def render(self, index: int, scale: float, region):
        page = self.page(index)
        formenv = self.pdf.formenv.raw if self.pdf.formenv else None
        return render_region(page, formenv, (page.get_width(), page.get_height()), scale, region)

    def close(self) -> None:
        for page in self.pages.values():
            page.close()
        self.pages.clear()
        self.pdf.close()
        self.pdf = None
        self.buffer = None
        gc.collect()  # the ctypes view must be gone before the block can close
        try:
            self.shm.close()
        except BufferError:
            pass


class _OutputBuffer:
    """Shared memory the worker writes pixels into. Only a short message crosses the pipe:
    pushing megabytes of pixels through it was slower than rendering them."""

    def __init__(self):
        self.memory: shared_memory.SharedMemory | None = None

    def write(self, data: bytes) -> str:
        if self.memory is None or self.memory.size < len(data):
            if self.memory is not None:
                self.memory.close()
                self.memory.unlink()
            self.memory = shared_memory.SharedMemory(create=True, size=max(len(data), 8 * 2**20))
        self.memory.buf[: len(data)] = data
        return self.memory.name

    def close(self) -> None:
        if self.memory is not None:
            self.memory.close()
            self.memory.unlink()


def worker_main(index: int, inbox, outbox) -> None:
    """Entry point of a render process. Messages in:

    ("hello",)
    ("open", doc_key, shm_name, size, password, hidden)
    ("close", doc_key)
    ("render", job_id, doc_key, page, scale, region)
    ("quit",)

    Out: ("ready", index), ("done", index, job_id, width, height, stride, shm_name),
    ("error", index, job_id, message). The pool copies the pixels out of shm_name
    before it sends this worker another job, so one buffer per worker is enough.
    """
    documents: dict[str, _OpenDocument] = {}
    output = _OutputBuffer()
    parent = multiprocessing.parent_process()
    while True:
        try:
            message = inbox.get(timeout=PARENT_CHECK_SECONDS)
        except queue.Empty:
            # If the app was killed it never sends "quit": don't linger as an orphan.
            if parent is not None and not parent.is_alive():
                break
            continue
        kind = message[0]
        if kind == "quit":
            break
        if kind == "hello":  # first message: answering it means imports are done
            outbox.put(("ready", index))
        elif kind == "open":
            _, key, shm_name, size, password, hidden = message
            try:
                documents[key] = _OpenDocument(shm_name, size, password, hidden)
            except Exception:
                traceback.print_exc()
        elif kind == "close":
            document = documents.pop(message[1], None)
            if document is not None:
                document.close()
        elif kind == "render":
            _, job_id, key, page, scale, region = message
            try:
                data, width, height, stride = documents[key].render(page, scale, region)
                outbox.put(("done", index, job_id, width, height, stride, output.write(data)))
            except Exception as error:
                outbox.put(("error", index, job_id, repr(error)))
    for document in documents.values():
        document.close()
    output.close()
