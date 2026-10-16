"""Build the application icon from the logo.

    python tools/make_icon.py

Reads src/zenith_pdf/assets/zenith.svg and writes zenith.ico (one image per size,
each rendered directly from the vector logo) and a large zenith.png.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parents[1] / "src" / "zenith_pdf" / "assets"
SIZES = (256, 128, 64, 48, 32, 24, 16)
# The drawing occupies the middle of its 512 x 512 canvas (x 121-391, y 72-452): frame a square
# around it so the icon isn't mostly empty space at small sizes.
FRAME = QRectF(61, 67, 390, 390)


def draw(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer.setViewBox(FRAME)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def dib_bytes(image: QImage) -> bytes:
    """A 32-bit BMP entry: header, bottom-up BGRA rows, then the 1-bit AND mask.

    Explorer draws shortcut icons from these; small PNG entries can come out blank."""
    side = image.width()
    rows = image.convertToFormat(QImage.Format.Format_ARGB32).flipped(Qt.Orientation.Vertical)
    stride = (side + 31) // 32 * 4
    pixels = bytearray()
    mask = bytearray()
    for y in range(side):
        line = bytes(rows.constScanLine(y))[: side * 4]  # B, G, R, A per pixel
        pixels += line
        bits = bytearray(stride)
        for x in range(side):
            if line[x * 4 + 3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    header = struct.pack("<IiiHHIIiiII", 40, side, side * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0)
    return header + bytes(pixels) + bytes(mask)


def write_ico(path: Path, images: list[QImage]) -> None:
    """An .ico with a PNG entry at 256 px and BMP entries below, the layout the Windows shell expects."""
    blobs = [png_bytes(image) if image.width() >= 256 else dib_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for image, blob in zip(images, blobs):
        side = image.width() if image.width() < 256 else 0  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    renderer = QSvgRenderer(str(ASSETS / "zenith.svg"))
    if not renderer.isValid():
        raise SystemExit("zenith.svg could not be read")
    write_ico(ASSETS / "zenith.ico", [draw(renderer, size) for size in SIZES])
    draw(renderer, 1024).save(str(ASSETS / "zenith.png"))
    print(f"wrote {ASSETS / 'zenith.ico'}")
    app.quit()


if __name__ == "__main__":
    main()
