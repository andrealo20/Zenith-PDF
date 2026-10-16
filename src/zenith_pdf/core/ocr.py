"""Text recognition with the OCR engine built into Windows 10 and 11.

It works offline and needs no extra install beyond the WinRT bindings. The
recognised words become an invisible text layer (pages.add_text_layer), so a
scanned page can be searched, selected and highlighted like any other.
"""

from __future__ import annotations

import asyncio

from PySide6.QtGui import QImage

from .document import Document

OCR_DPI = 200  # enough for body text; Windows rejects images over 10,000 px a side


def available() -> bool:
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return False
    return OcrEngine.try_create_from_user_profile_languages() is not None


def languages() -> list[tuple[str, str]]:
    """The recognition languages installed in Windows: (tag, name), e.g. ("it-IT", "Italian (Italy)")."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return []
    return [(language.language_tag, language.display_name) for language in OcrEngine.available_recognizer_languages]


def _engine(language: str):
    from winrt.windows.media.ocr import OcrEngine

    if language:
        from winrt.windows.globalization import Language

        engine = OcrEngine.try_create_from_language(Language(language))
        if engine is not None:
            return engine
    return OcrEngine.try_create_from_user_profile_languages()


def recognize_page(document: Document, index: int, language: str = "", dpi: int = OCR_DPI
                   ) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Words on a page with their boxes in display space (points). Slow: call off the GUI thread.

    `language` is a tag such as "it-IT"; empty means the languages of the Windows user.
    """
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = _engine(language)
    if engine is None:
        raise RuntimeError("Windows has no text recognition language installed.")
    width, height = document.page_size(index)
    scale = min(dpi / 72, (OcrEngine.max_image_dimension - 1) / max(width, height))
    full_w, full_h = int(width * scale) // 4 * 4, int(height * scale)  # whole 4-byte grey rows
    data, w, h, stride = document.render_region(index, scale, (0, 0, full_w, full_h))
    grey = QImage(data, w, h, stride, QImage.Format.Format_RGB32).convertToFormat(QImage.Format.Format_Grayscale8)
    writer = DataWriter()
    writer.write_bytes(bytes(grey.constBits())[: grey.bytesPerLine() * h])
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.GRAY8, grey.bytesPerLine(), h)

    async def run():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(run())
    words = []
    for line in result.lines:
        for word in line.words:
            box = word.bounding_rect
            words.append((word.text, (box.x / scale, box.y / scale, box.width / scale, box.height / scale)))
    return words
