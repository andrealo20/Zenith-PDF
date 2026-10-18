"""Parallel page rendering.

PDFium is not thread-safe, not even across documents, so parallelism comes
from processes: each worker process opens the document from one shared-memory
copy of the file. One more worker is a thread in this process. It is ready the
moment the app starts, while the processes are still importing, and it keeps
rendering if the processes can't start at all.

Jobs wait in a priority queue here and go to a worker only when it is idle, so
a job nobody needs any more (the user scrolled on) can still be dropped. A new
request with the same key replaces the waiting one.
"""

from __future__ import annotations

import itertools
import logging
import math
import multiprocessing
import os
import queue
import threading
import weakref
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from multiprocessing import shared_memory

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .document import Document, DocumentClosed, before_exit
from .render_worker import attach, worker_main

log = logging.getLogger(__name__)

# Upper bound on the pixels of a whole-page bitmap (RGB32, 4 bytes each: 64 MB).
# Tiles have no such limit: they cover only what is on screen.
MAX_RENDER_PIXELS = 16_000_000
MAX_RESTARTS = 3  # per worker, so a PDF that crashes PDFium can't loop forever
AFFINITY = 4  # recent pages remembered per worker


def render_scale(page_size: tuple[float, float], view_scale: float, device_pixel_ratio: float) -> float:
    """Bitmap pixels per PDF point for a whole page shown at `view_scale` screen pixels per point.

    The sharpest answer is view_scale * device_pixel_ratio: one bitmap pixel per
    physical screen pixel. On big pages at high zoom that bitmap explodes: an A0
    drawing at 800% on a 200% display would be about 50,000 x 72,000 pixels.
    """
    scale = view_scale * device_pixel_ratio
    width, height = page_size
    if width * height * scale * scale <= MAX_RENDER_PIXELS:
        return scale
    # Pixels grow with the square of the scale, so shrink by the square root of the excess,
    # then nudge down until rounding each side up to whole pixels fits too.
    scale = math.sqrt(MAX_RENDER_PIXELS / (width * height))
    while math.ceil(width * scale) * math.ceil(height * scale) > MAX_RENDER_PIXELS:
        scale *= 0.999
    return scale


def default_worker_count(cpu_count: int | None) -> int:
    """Render processes to start. Each costs about 40 MB plus the pages it has parsed."""
    return max(1, min((cpu_count or 2) // 2, 6))


@dataclass
class _Job:
    document: Document
    page: int
    scale: float
    priority: tuple
    region: tuple[int, int, int, int] | None
    version: str = ""  # the document's key when the job went to a worker


def _image(pixels, width: int, height: int, stride: int) -> QImage:
    """Copy BGRA pixels (bytes or a shared-memory view) into a new image, once."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    size = stride * height
    if image.bytesPerLine() == stride:
        image.bits()[:size] = pixels[:size]
        return image
    return QImage(bytes(pixels[:size]), width, height, stride, QImage.Format.Format_RGB32).copy()


class _ThreadWorker:
    """Renders with this process's own PDFium, under the document lock.
    Its results skip the inter-process queue and go straight to the pool."""

    def __init__(self, index: int, deliver: Callable, documents: dict):
        self.index = index
        self.ready = True
        self.restarts = 0
        self._deliver = deliver
        self._documents = documents
        self._inbox: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="render-local", daemon=True)
        self._thread.start()
        _LOCAL_WORKERS.add(self)  # stopped as the interpreter exits, even without a shutdown()

    def send(self, message) -> None:
        if message[0] in ("render", "quit"):
            self._inbox.put(message)

    def alive(self) -> bool:
        return True

    def stop(self) -> None:
        self._inbox.put(("quit",))
        # Wait for the page in hand: PDFium still at work as the interpreter exits can bring the process down.
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while True:
            message = self._inbox.get()
            if message[0] == "quit":
                return
            _, job_id, key, page, scale, region = message
            entry = self._documents.get(key)
            try:
                if entry is None:  # forgotten while the job waited: its pages may be gone
                    raise DocumentClosed(key)
                data, width, height, stride = entry[0].render_region(page, scale, region, key)
                result = ("done", self.index, job_id, width, height, stride, data)
            except DocumentClosed:
                result = ("error", self.index, job_id, "closed")
            except Exception as error:
                result = ("error", self.index, job_id, repr(error))
            try:
                self._deliver(result)
            except Exception:
                log.warning("a rendered page could not be delivered", exc_info=True)


_LOCAL_WORKERS: weakref.WeakSet[_ThreadWorker] = weakref.WeakSet()


def _stop_local_workers() -> None:
    """A script or a test that ends without RenderPool.shutdown() leaves the render thread at a page: it must be
    done with PDFium before the documents are closed under it."""
    for worker in list(_LOCAL_WORKERS):
        worker.stop()


before_exit(_stop_local_workers)


class _ProcessWorker:
    def __init__(self, index: int, context, results):
        self.index = index
        self.ready = False  # set when the process reports in
        self.restarts = 0
        self._context = context
        self._results = results
        self._start()

    def _start(self) -> None:
        self.inbox = self._context.Queue()
        self.process = self._context.Process(
            target=worker_main, args=(self.index, self.inbox, self._results), name=f"zenith-render-{self.index}", daemon=True
        )
        self.process.start()
        self.inbox.put(("hello",))

    def restart(self) -> None:
        self.restarts += 1
        self.ready = False
        self._start()

    def send(self, message) -> None:
        self.inbox.put(message)

    def alive(self) -> bool:
        return self.process.is_alive()

    def stop(self) -> None:
        try:
            self.inbox.put(("quit",))
            self.process.join(timeout=1)
        finally:
            if self.process.is_alive():
                self.process.terminate()


class RenderPool(QObject):
    # key, scale, image
    rendered = Signal(object, float, QImage)

    def __init__(self, processes: int | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._jobs: dict[Hashable, _Job] = {}
        self._inflight: dict[int, tuple[int, Hashable, _Job]] = {}  # worker -> (job id, key, job)
        self._recent: dict[int, deque] = {}
        self._ids = itertools.count()
        self._documents: dict[str, tuple[Document, shared_memory.SharedMemory | None]] = {}
        self._stopping = False
        self._outputs: dict[int, shared_memory.SharedMemory] = {}  # each process's pixel buffer
        self._context = multiprocessing.get_context("spawn")
        self._results = self._context.Queue()
        self._workers = [_ThreadWorker(0, self._handle, self._documents)]
        count = default_worker_count(os.cpu_count()) if processes is None else processes
        for index in range(1, count + 1):
            try:
                self._workers.append(_ProcessWorker(index, self._context, self._results))
            except Exception:
                log.warning("render process %d did not start; rendering continues in-process", index, exc_info=True)
                break
        for worker in self._workers:
            self._recent[worker.index] = deque(maxlen=AFFINITY)
        self._listener = threading.Thread(target=self._listen, name="render-results", daemon=True)
        self._listener.start()

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    # Documents ----------------------------------------------------------

    def _register(self, document: Document) -> None:
        """Share the file bytes with the processes once; they open the PDF from there."""
        memory = None
        if len(self._workers) > 1:
            data = document.display_data
            memory = shared_memory.SharedMemory(create=True, size=max(len(data), 1))
            memory.buf[: len(data)] = data
            message = ("open", document.key, memory.name, len(data), document.password, document.hidden)
            for worker in self._workers[1:]:
                worker.send(message)
        self._documents[document.key] = (document, memory)

    def forget(self, document: Document) -> None:
        """Release a closed document's shared memory and drop its jobs."""
        with self._lock:
            entry = self._documents.pop(document.key, None)
            for key in [k for k, job in self._jobs.items() if job.document is document]:
                del self._jobs[key]
        if entry is None:
            return
        for worker in self._workers[1:]:
            worker.send(("close", document.key))
        if entry[1] is not None:
            entry[1].close()
            entry[1].unlink()

    # Jobs ---------------------------------------------------------------

    def request(self, key: Hashable, document: Document, page: int, scale: float, priority: tuple, region=None) -> None:
        self.request_many([(key, document, page, scale, priority, region)])

    def request_many(self, jobs) -> None:
        """Queue (key, document, page, scale, priority, region) jobs.

        A key already rendering is skipped; a waiting one gets the new priority.
        """
        with self._lock:
            if self._stopping:
                return  # a late repaint after shutdown: the workers are gone
            running = {key for _, key, _ in self._inflight.values()}
            for key, document, page, scale, priority, region in jobs:
                if key in running:
                    continue
                if document.key not in self._documents:
                    self._register(document)
                self._jobs[key] = _Job(document, page, scale, priority, region)
        self._dispatch()

    def discard(self, predicate: Callable[[Hashable], bool]) -> list[Hashable]:
        """Drop waiting jobs whose key matches and return their keys. Running jobs still finish."""
        with self._lock:
            dropped = [k for k in self._jobs if predicate(k)]
            for key in dropped:
                del self._jobs[key]
        return dropped

    def is_idle(self) -> bool:
        with self._lock:
            return not self._jobs and not self._inflight

    def pending(self) -> int:
        with self._lock:
            return len(self._jobs) + len(self._inflight)

    def _pick(self, worker_index: int) -> Hashable:
        """The most urgent job, preferring a page this worker parsed recently."""
        best = min(self._jobs, key=lambda k: self._jobs[k].priority)
        tier = self._jobs[best].priority[0]
        recent = self._recent[worker_index]
        warm = [
            k for k, job in self._jobs.items()
            if job.priority[0] == tier and (job.document.key, job.page) in recent
        ]
        return min(warm, key=lambda k: self._jobs[k].priority) if warm else best

    def _dispatch(self) -> None:
        with self._lock:
            # A process that died for good stays "ready": alive() keeps the thread at work then.
            processes_ready = any(worker.ready and worker.alive() for worker in self._workers[1:])
            for worker in self._workers:
                if not self._jobs:
                    return
                if not worker.ready or worker.index in self._inflight:
                    continue
                if worker.index == 0 and processes_ready:
                    continue  # its tiles hold PDFIUM_LOCK, which the GUI thread waits on at every mouse move
                key = self._pick(worker.index)
                job = self._jobs.pop(key)
                job_id = next(self._ids)
                job.version = job.document.key
                if job.version not in self._documents:  # reloaded since it was asked for
                    self._register(job.document)
                self._inflight[worker.index] = (job_id, key, job)
                self._recent[worker.index].append((job.version, job.page))
                worker.send(("render", job_id, job.version, job.page, job.scale, job.region))

    def _listen(self) -> None:
        """Receive the processes' messages until shutdown."""
        while True:
            try:
                message = self._results.get(timeout=0.5)
            except queue.Empty:
                self._check_workers()
                continue
            except (OSError, EOFError):
                return  # the queue was closed under us at exit
            if message is None:
                return
            try:
                self._handle(message)
            except Exception:  # one bad message must not stop every render that follows
                log.warning("a render result could not be handled", exc_info=True)

    def _pixels(self, index: int, name: str):
        """A view of worker `index`'s output buffer, attaching again if it grew."""
        memory = self._outputs.get(index)
        if memory is None or memory.name != name:
            if memory is not None:
                memory.close()
            memory = self._outputs[index] = attach(name)  # the worker made it, and removes it
        return memory.buf

    def _handle(self, message) -> None:
        """A worker's message, from the listener thread or the in-process worker's thread."""
        kind, index = message[0], message[1]
        if kind == "ready":
            self._workers[index].ready = True
            self._dispatch()
            return
        job_id = message[2]
        with self._lock:
            entry = self._inflight.get(index)
            if entry is None or entry[0] != job_id:
                return
        _, key, job = entry
        image = None
        try:
            if kind == "done":
                width, height, stride, payload = message[3:]
                pixels = self._pixels(index, payload) if isinstance(payload, str) else payload
                image = _image(pixels, width, height, stride)  # copied out before the worker is reused
            elif message[3] != "closed" and job.version in self._documents:  # else: forgotten, nobody waits
                log.warning("rendering page %d failed: %s", job.page + 1, message[3])
        finally:
            # Whatever happened, the worker is free again: a job left in flight would idle it for good.
            with self._lock:
                self._inflight.pop(index, None)
        if image is not None:
            self.rendered.emit(key, job.scale, image)
        self._dispatch()

    def _check_workers(self) -> None:
        """Replace a worker process that died, most likely on a PDF that crashed PDFium."""
        for worker in self._workers[1:]:
            # While shutting down, workers die on purpose: starting new ones then fails.
            if self._stopping or worker.alive() or worker.restarts >= MAX_RESTARTS:
                continue
            with self._lock:
                lost = self._inflight.pop(worker.index, None)
            if lost is not None:
                log.warning("render process crashed on page %d", lost[2].page + 1)
            worker.restart()
            for key, (document, memory) in list(self._documents.items()):
                if memory is not None:
                    worker.send(("open", key, memory.name, len(document.display_data), document.password,
                                 document.hidden))

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            self._jobs.clear()
        for worker in self._workers:
            worker.stop()
        self._results.put(None)
        self._listener.join(timeout=2)
        for memory in self._outputs.values():
            memory.close()
        self._outputs.clear()
        for document, _ in list(self._documents.values()):
            self.forget(document)
