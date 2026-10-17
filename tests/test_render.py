"""The multi-process render pool."""

import time

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from zenith_pdf.core.document import Document
from zenith_pdf.core.render import RenderPool
from zenith_pdf.core.render_worker import full_size
from zenith_pdf.ui.tiles import render_area, tile_grid

SCALE = 3.1  # not a round number, so tiles at the edges are partial


def wait_for(condition, timeout=20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def pool(qt_app):
    pool = RenderPool(processes=2)
    assert wait_for(lambda: all(w.ready for w in pool._workers)), "render processes did not start"
    yield pool
    pool.shutdown()


@pytest.fixture
def document(sample_pdf):
    document = Document(sample_pdf)
    yield document
    document.close()


def _collect(pool):
    results = {}
    pool.rendered.connect(lambda key, scale, image: results.__setitem__(key, image))
    return results


def test_processes_render_the_same_pixels_as_this_process(pool, document):
    results = _collect(pool)
    region = (512, 0, 512, 512)
    # Low priority for the in-process worker's view: lets a process take the job.
    for worker in range(3):
        pool.request(("tile", worker), document, 1, SCALE, (0, worker), region)
    assert wait_for(lambda: len(results) == 3)
    data, width, height, stride = document.render_region(1, SCALE, region)
    expected = QImage(data, width, height, stride, QImage.Format.Format_RGB32)
    for image in results.values():
        assert image == expected
    pool.forget(document)


def _max_difference(a: QImage, b: QImage) -> int:
    worst = 0
    for y in range(a.height()):
        for x in range(a.width()):
            p, q = a.pixel(x, y), b.pixel(x, y)
            if p != q:
                worst = max(worst, *(abs(((p >> s) & 255) - ((q >> s) & 255)) for s in (0, 8, 16)))
    return worst


@pytest.mark.parametrize("page", [0, 3])  # upright, and cropped and rotated 270 degrees
def test_tiles_stitch_into_the_whole_page_without_seams(pool, document, page):
    results = _collect(pool)
    size = document.page_size(page)
    cells = list(tile_grid(size, SCALE, QRectF(0, 0, *size)))
    assert len(cells) > 1
    for col, row, cell in cells:
        area = render_area(size, SCALE, cell)
        pool.request(("cell", col, row), document, page, SCALE, (0, col + row), (area.x(), area.y(), area.width(), area.height()))
    assert wait_for(lambda: len(results) == len(cells))

    width, height = full_size(size, SCALE)
    stitched = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(stitched)
    for col, row, cell in cells:
        area = render_area(size, SCALE, cell)
        painter.drawImage(cell.topLeft(), results[("cell", col, row)], cell.translated(-area.topLeft()))
    painter.end()
    # Antialiasing may differ by a level or two; a glyph split by a seam differs by ~130.
    assert _max_difference(stitched, document.render(page, SCALE)) <= 2
    pool.forget(document)


def test_a_page_edit_while_pages_render_is_quiet(qt_app, document, caplog):
    """forget() and a reload while a job is in flight: no "rendering page N failed", and rendering goes on."""
    from zenith_pdf.core.document import PDFIUM_LOCK

    pool = RenderPool(processes=0)
    try:
        results = _collect(pool)
        with PDFIUM_LOCK:  # the render thread takes its first job and waits here
            for i in range(8):
                pool.request(("old", i), document, i % 4, 1.0, (0, i), None)
            pool.forget(document)
            document.restore(document.data)  # what a page edit does: the same document, a new version
        assert wait_for(pool.is_idle)
        for i in range(4):
            pool.request(("new", i), document, i, 1.0, (0, i), None)
        assert wait_for(lambda: len([k for k in results if k[0] == "new"]) == 4)
        assert "failed" not in caplog.text and not [k for k in results if k[0] == "old"]
    finally:
        pool.shutdown()


def test_a_crashed_process_is_replaced(pool, document):
    victim = pool._workers[1]
    victim.process.terminate()
    victim.process.join()
    assert wait_for(lambda: victim.alive() and victim.ready)

    results = _collect(pool)
    for i in range(6):
        pool.request(("after-crash", i), document, 0, 1.0, (0, i), None)
    assert wait_for(lambda: len(results) == 6)
    pool.forget(document)


def test_threads_using_pdfium_stop_before_the_finalizers_at_exit(qt_app, monkeypatch):
    """pypdfium2 closes the documents still open from weakref finalizers, among the atexit callbacks. Home's
    file-info threads and the render thread stop before those, even when a script ends without a shutdown: else
    PDFium can crash the process on its way out."""
    import os
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    from zenith_pdf.core import document, render
    from zenith_pdf.ui import home

    assert home._stop_loaders in document._BEFORE_EXIT and render._stop_local_workers in document._BEFORE_EXIT
    script = textwrap.dedent("""
        import weakref
        from zenith_pdf.core.document import before_exit

        class Thing:
            pass

        thing = Thing()
        before_exit(lambda: print("threads stopped", flush=True))
        weakref.finalize(thing, print, "documents closed", flush=True)
    """)
    source = Path(document.__file__).parents[2]
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(source)})
    assert out.stdout.split()[:4] == ["threads", "stopped", "documents", "closed"], out.stderr
    pool = RenderPool(processes=0)
    worker = pool._workers[0]
    assert worker in render._LOCAL_WORKERS
    monkeypatch.setattr(render, "_LOCAL_WORKERS", [worker])  # this pool's only: the module's pool goes on
    render._stop_local_workers()
    assert not worker._thread.is_alive()
    pool.shutdown()


def test_a_zoom_between_levels_paints_from_the_sharpest_level_that_covers_the_page(qt_app):
    from PySide6.QtGui import QPixmap, QTransform

    from zenith_pdf.ui.tiles import Tile, TileCache, paint_page, scale_key

    class Counting(QPainter):
        drawn = 0

        def drawPixmap(self, *args):
            Counting.drawn += 1
            super().drawPixmap(*args)

    size = (100.0, 100.0)
    cache = TileCache()
    for scale in (1.0, 2.0):  # two levels, each covering the whole page
        for col, row, rect in tile_grid(size, scale, QRectF(0, 0, *size)):
            cache.put(0, scale_key(scale), col, row, Tile(QPixmap(rect.size()), scale, rect, rect.translated(-rect.topLeft())))
    image = QImage(300, 300, QImage.Format.Format_ARGB32)
    painter = Counting(image)
    painter.setTransform(QTransform.fromScale(1.5, 1.5))  # between the two
    paint_page(painter, 0, QRectF(0, 0, *size), QRectF(0, 0, *size), cache, QPixmap(10, 10))
    painter.end()
    assert Counting.drawn == len(cache.level(0, scale_key(2.0)))  # neither the base nor the coarser level
