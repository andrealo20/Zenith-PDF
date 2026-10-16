"""Save a PNG of Zenith PDF's window, for documentation and visual checks.

    python tools/snapshot.py out.png [file.pdf] [--theme light] [--size 1400x900]
                             [--page N] [--zoom 1.5] [--search TEXT] [--panel outline]

Uses a throwaway settings file, so it never touches the real recent-files list.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("pdf", nargs="?")
    parser.add_argument("--theme", default="dark")
    parser.add_argument("--size", default="1400x900")
    parser.add_argument("--page", type=int)
    parser.add_argument("--zoom", type=float)
    parser.add_argument("--search")
    parser.add_argument("--panel")
    parser.add_argument("--recent", nargs="*", default=[], help="files to list on the home page")
    args = parser.parse_args()

    store = QSettings(str(Path(tempfile.mkdtemp()) / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.theme = args.theme
    for path in reversed(args.recent):
        settings.add_recent(Path(path).resolve())

    app, window = create_app([sys.argv[0]], settings)
    width, height = (int(n) for n in args.size.split("x"))
    window.resize(width, height)
    window.show()
    if args.pdf:
        window.open_file(args.pdf)
    else:
        window.show_home()

    def arrange() -> None:
        workspace = window.current_workspace()
        if workspace is None:
            return
        if args.zoom:
            workspace.view.set_zoom(args.zoom)
        if args.page:
            workspace.view.goto_page(args.page - 1)
        if args.panel:
            workspace.side.show_panel(args.panel)
        if args.search:
            workspace.show_search()
            workspace.search.field.setText(args.search)
            workspace.search.start()

    deadline = time.monotonic() + 20
    idle_checks = 0

    def capture_when_idle() -> None:
        nonlocal idle_checks
        idle_checks = idle_checks + 1 if window.renderer.is_idle() else 0
        if idle_checks >= 8 or time.monotonic() > deadline:
            window.grab().save(args.out)
            app.quit()
        else:
            QTimer.singleShot(100, capture_when_idle)

    QTimer.singleShot(300, arrange)
    QTimer.singleShot(900, capture_when_idle)
    app.exec()


if __name__ == "__main__":
    main()
