"""How long does zooming take to look fully sharp?

    python tools/bench_zoom.py file.pdf [--processes N] [--page P]
    python tools/bench_zoom.py --synthetic heavy.pdf    (writes a dense A1 drawing first)

Opens the real window, waits for the first render and the work done ahead of
the user, then zooms in with simulated mouse-wheel notches and reports the
milliseconds until every tile on screen is rendered at the exact zoom.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


def make_synthetic(path: Path, lines: int = 40_000) -> None:
    """An A1 sheet packed with short vector strokes and small text, like a CAD export."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    random.seed(1)
    pdf = pdfium.PdfDocument.new()
    width, height = 2384, 1684
    page = pdf.new_page(width, height)
    for _ in range(lines):
        x, y = random.uniform(20, width - 20), random.uniform(20, height - 20)
        path_obj = raw.FPDFPageObj_CreateNewPath(x, y)
        raw.FPDFPath_LineTo(path_obj, x + random.uniform(-30, 30), y + random.uniform(-30, 30))
        raw.FPDFPageObj_SetStrokeColor(path_obj, 0, 0, 0, 255)
        raw.FPDFPageObj_SetStrokeWidth(path_obj, 0.3)
        raw.FPDFPath_SetDrawMode(path_obj, 0, 1)
        raw.FPDFPage_InsertObject(page, path_obj)
    page.gen_content()
    page.close()
    pdf.save(str(path))
    pdf.close()


def wait(app: QApplication, condition, timeout: float = 60.0) -> float:
    start = time.perf_counter()
    while not condition():
        app.processEvents()
        if time.perf_counter() - start > timeout:
            raise TimeoutError
        time.sleep(0.001)
    return (time.perf_counter() - start) * 1000


def settled(view, pool):
    """True once nothing is queued and the view has no follow-up pending: work ahead is done."""
    return lambda: pool.is_idle() and not view._render_timer.isActive() and not view._followup_timer.isActive()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", nargs="?")
    parser.add_argument("--synthetic")
    parser.add_argument("--processes", type=int)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--notches", type=int, default=5)
    args = parser.parse_args()
    if args.synthetic:
        args.pdf = args.synthetic
        make_synthetic(Path(args.synthetic))
    if args.processes is not None:
        os.environ["ZENITH_RENDER_PROCESSES"] = str(args.processes)

    from zenith_pdf.app import create_app
    from zenith_pdf.settings import Settings

    store = QSettings(str(Path(tempfile.mkdtemp()) / "settings.ini"), QSettings.Format.IniFormat)
    app, window = create_app([sys.argv[0]], Settings(store))
    window.resize(1400, 900)
    window.show()
    pool = window.renderer
    wait(app, lambda: all(w.ready for w in pool._workers), 10)
    print(f"{Path(args.pdf).name}: {pool.worker_count} workers ({pool.worker_count - 1} processes + 1 thread)")

    window.open_file(args.pdf)
    view = window.current_workspace().view
    view.goto_page(args.page - 1)
    print(f"first screen sharp after   {wait(app, lambda: view.missing_tiles() == 0):7.0f} ms")
    print(f"work ahead finished after  {wait(app, settled(view, pool)):7.0f} ms more")

    for notch in range(args.notches):
        view.set_zoom(view.ladder_step(view.zoom, 1))
        sharp = wait(app, lambda: view.missing_tiles() == 0)
        ahead = wait(app, settled(view, pool))
        print(f"wheel notch to {view.zoom:5.0%}: sharp after {sharp:6.0f} ms   (next levels ready {ahead:5.0f} ms later)")

    for _ in range(3):
        view.set_zoom(view.ladder_step(view.zoom, -1))
    print(f"three quick notches back:   sharp after {wait(app, lambda: view.missing_tiles() == 0):6.0f} ms (cached)")
    view.set_zoom(view.zoom * 1.37)  # off the ladder, nothing prepared: pure render speed
    print(f"jump to {view.zoom:5.0%} (unprepared):  sharp after {wait(app, lambda: view.missing_tiles() == 0):6.0f} ms")
    window.close()
    pool.shutdown()


if __name__ == "__main__":
    main()
