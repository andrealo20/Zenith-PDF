"""Pictures placed on pages: encoding, decoding, and the PDF image objects behind them.

An image annotation keeps its picture as PNG or JPEG bytes. A JPEG goes into
the PDF as it is (PDF readers decode JPEG natively); anything else becomes
deflated RGB rows with a separate soft mask for transparency.
"""

from __future__ import annotations

import io
import zlib
from collections import OrderedDict

import pikepdf
from pikepdf import Name
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader

MAX_SIDE = 8000  # pixels; bigger pictures are scaled down when placed
PHOTO_PIXELS = 500_000  # below this, PNG is always fine
DECODED_CACHE = 24  # pictures kept decoded for painting
SCALED_CACHE = 64  # downscaled copies kept for painting at small sizes


def is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def jpeg_components(data: bytes) -> int:
    """Colour components of a JPEG (1 grey, 3 colour, 4 CMYK); 0 if it can't tell."""
    position = 2
    while position + 9 < len(data):
        if data[position] != 0xFF:
            return 0
        marker = data[position + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
            position += 2
            continue
        length = int.from_bytes(data[position + 2:position + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # a start of frame
            return data[position + 9]
        position += 2 + length
    return 0


def encode(image: QImage) -> bytes:
    """A picture as file bytes: PNG for screenshots and drawings, JPEG for photographs.

    A photograph compresses badly as PNG, so when PNG comes out more than twice
    the size of a good JPEG, and the picture is large and has no transparency,
    JPEG wins. Text and flat colours stay lossless.
    """
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    png = _save(image, "PNG")
    if image.hasAlphaChannel() and not _opaque(image):
        return png
    if image.width() * image.height() < PHOTO_PIXELS:
        return png
    jpeg = _save(image.convertToFormat(QImage.Format.Format_RGB888), "JPG", 90)
    return jpeg if len(png) > 2 * len(jpeg) else png


def _save(image: QImage, kind: str, quality: int = -1) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, kind, quality)
    buffer.close()
    return bytes(data)


def _opaque(image: QImage) -> bool:
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    rows = (bytes(alpha.constScanLine(y))[: alpha.width()] for y in range(alpha.height()))
    return all(row.count(255) == len(row) for row in rows)


def from_file(path: str) -> bytes | None:
    """The bytes to keep for an image file: JPEG and PNG as they are when possible, else re-encoded.

    A photo's EXIF orientation is applied, since PDF has no such thing. None if unreadable.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    rotated = reader.transformation() != QImageIOHandler.Transformation.TransformationNone
    size = reader.size()
    small_enough = size.isValid() and max(size.width(), size.height()) <= MAX_SIDE
    if small_enough and not rotated:
        if is_jpeg(data) and jpeg_components(data) in (1, 3):
            return data
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
    image = reader.read()
    return None if image.isNull() else encode(image)


def size(data: bytes) -> tuple[int, int]:
    image = decoded(data)
    return image.width(), image.height()


_decoded: OrderedDict[int, QImage] = OrderedDict()
_scaled: OrderedDict[tuple[int, int], QImage] = OrderedDict()


def decoded(data: bytes) -> QImage:
    """The picture, decoded once and kept for a while (keyed by the bytes object itself)."""
    key = hash(data)
    image = _decoded.get(key)
    if image is None:
        image = QImage.fromData(data)
        _decoded[key] = image
        while len(_decoded) > DECODED_CACHE:
            _decoded.popitem(last=False)
    else:
        _decoded.move_to_end(key)
    return image


def for_painting(data: bytes, width_px: float) -> QImage:
    """The picture at about `width_px` wide: a halved copy while that is still sharp enough.

    Painting a 4000-pixel photo into a 300-pixel box every frame is slow; a
    prepared copy at the next power-of-two step above the target is fast and
    still smooth.
    """
    image = decoded(data)
    full = image.width()
    if full <= 0 or width_px * 2 >= full:
        return image
    step = full
    while step / 2 >= width_px * 1.5 and step > 64:
        step //= 2
    key = (hash(data), step)
    scaled = _scaled.get(key)
    if scaled is None:
        scaled = image.scaledToWidth(step, Qt.TransformationMode.SmoothTransformation)
        _scaled[key] = scaled
        while len(_scaled) > SCALED_CACHE:
            _scaled.popitem(last=False)
    return scaled


# PDF ------------------------------------------------------------------


def xobject(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """A PDF image object showing `data`."""
    if is_jpeg(data) and jpeg_components(data) in (1, 3):
        image = decoded(data)
        stream = pikepdf.Stream(pdf, data)
        stream.Filter = Name.DCTDecode
        stream.ColorSpace = Name.DeviceGray if jpeg_components(data) == 1 else Name.DeviceRGB
        stream.Width, stream.Height = image.width(), image.height()
    else:
        image = decoded(data)
        rgb = image.convertToFormat(QImage.Format.Format_RGB888)
        width, height = rgb.width(), rgb.height()
        rows = b"".join(bytes(rgb.constScanLine(y))[: width * 3] for y in range(height))
        stream = pikepdf.Stream(pdf, zlib.compress(rows, 6))
        stream.Filter = Name.FlateDecode
        stream.ColorSpace = Name.DeviceRGB
        stream.Width, stream.Height = width, height
        if image.hasAlphaChannel() and not _opaque(image):
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            mask_rows = b"".join(bytes(alpha.constScanLine(y))[:width] for y in range(height))
            mask = pikepdf.Stream(pdf, zlib.compress(mask_rows, 6))
            mask.Type, mask.Subtype = Name.XObject, Name.Image
            mask.Filter = Name.FlateDecode
            mask.ColorSpace = Name.DeviceGray
            mask.BitsPerComponent = 8
            mask.Width, mask.Height = width, height
            stream.SMask = mask
    stream.Type, stream.Subtype = Name.XObject, Name.Image
    stream.BitsPerComponent = 8
    return stream


def _rows(stream: pikepdf.Stream, channels: int) -> bytes | None:
    """The decoded rows of an 8-bit image with `channels` components and no /Decode, else None."""
    try:
        width, height = int(stream.Width), int(stream.Height)
        if int(stream.get("/BitsPerComponent", 8)) != 8 or "/Decode" in stream or width <= 0 or height <= 0:
            return None
        data = stream.read_bytes()
    except Exception:
        return None
    size = width * height * channels
    return data[:size] if len(data) >= size else None


def _plain_rows(stream: pikepdf.Stream) -> QImage | None:
    """An RGB or grey image made of plain rows (the kind xobject() writes), with its soft mask, as a QImage."""
    gray = stream.get("/ColorSpace") == Name.DeviceGray
    rows = _rows(stream, 1 if gray else 3)
    if rows is None:
        return None
    width, height = int(stream.Width), int(stream.Height)
    image = QImage(rows, width, height, width * (1 if gray else 3),
                   QImage.Format.Format_Grayscale8 if gray else QImage.Format.Format_RGB888)
    mask = stream.get("/SMask")
    if mask is None:
        return image.convertToFormat(QImage.Format.Format_RGB32)
    if not isinstance(mask, pikepdf.Stream) or mask.get("/ColorSpace") != Name.DeviceGray or (
            int(mask.get("/Width", 0)), int(mask.get("/Height", 0))) != (width, height):
        return None
    alpha = _rows(mask, 1)
    if alpha is None:
        return None
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    image.setAlphaChannel(QImage(alpha, width, height, width, QImage.Format.Format_Alpha8))
    return image


def from_xobject(stream: pikepdf.Stream) -> bytes | None:
    """The picture in a PDF image object, as PNG or JPEG bytes. None for kinds it can't read."""
    if stream.get("/ImageMask", False) or "/Mask" in stream:
        return None
    filters = stream.get("/Filter")
    single = filters if isinstance(filters, Name) else filters[0] if isinstance(filters, pikepdf.Array) and len(filters) == 1 else None
    space = stream.get("/ColorSpace")
    plain_space = space in (Name.DeviceRGB, Name.DeviceGray)
    if single == Name.DCTDecode and "/SMask" not in stream and plain_space and "/Decode" not in stream:
        data = stream.read_raw_bytes()
        if jpeg_components(data) in (1, 3):
            return data
    if single in (None, Name.FlateDecode) and plain_space:
        image = _plain_rows(stream)
        if image is not None:  # the PNG encode() makes: a picture saved and read back keeps its bytes
            return _save(image, "PNG")
    try:
        picture = pikepdf.PdfImage(stream).as_pil_image()
        if "/SMask" in stream:
            mask = pikepdf.PdfImage(stream.SMask).as_pil_image().convert("L")
            if mask.size != picture.size:
                mask = mask.resize(picture.size)
            picture = picture.convert("RGBA")
            picture.putalpha(mask)
        elif picture.mode not in ("RGB", "L", "RGBA"):
            picture = picture.convert("RGB")
        buffer = io.BytesIO()
        picture.save(buffer, "PNG")
        return buffer.getvalue()
    except Exception:
        return None
