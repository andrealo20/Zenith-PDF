"""Render the iPad app icon from the logo.

    python ipad/tools/make_icon.py

Reads src/zenith_pdf/assets/zenith.svg and writes a 1024 x 1024 opaque PNG to the
asset catalog, plus a copy in the unit test fixtures. CI does not run this; both
PNGs are committed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

IPAD = Path(__file__).resolve().parents[1]
LOGO = IPAD.parent / "src" / "zenith_pdf" / "assets" / "zenith.svg"
ICON = IPAD / "ZenithPDF" / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon.png"
CHECK = IPAD / "ZenithPDFTests" / "Fixtures" / "AppIcon-check.png"
SIZE = 1024
INSET = 88
BACKGROUND = "#E8EBF0"  # Folio canvas, light: the page on the desk


def render() -> QImage:
    renderer = QSvgRenderer(str(LOGO))
    if not renderer.isValid():
        raise SystemExit(f"the logo could not be read: {LOGO}")
    image = QImage(SIZE, SIZE, QImage.Format.Format_RGB32)
    image.fill(QColor(BACKGROUND))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(INSET, INSET, SIZE - 2 * INSET, SIZE - 2 * INSET))
    painter.end()
    # RGB888 has no alpha channel, so the saved PNG is opaque, as the App Store icon must be.
    return image.convertToFormat(QImage.Format.Format_RGB888)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ICON.parent.mkdir(parents=True, exist_ok=True)
    CHECK.parent.mkdir(parents=True, exist_ok=True)
    if not render().save(str(ICON), "PNG"):
        raise SystemExit(f"could not write {ICON}")
    saved = QImage(str(ICON))
    assert saved.width() == SIZE and saved.height() == SIZE, "wrong icon size"
    assert not saved.hasAlphaChannel(), "the icon has an alpha channel"
    shutil.copyfile(ICON, CHECK)
    print(f"wrote {ICON} and {CHECK}")
    app.quit()


if __name__ == "__main__":
    main()
