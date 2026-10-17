"""Text recognition with the engine each system has.

- Windows 10 and 11: the OCR engine built into Windows, through the WinRT bindings.
- macOS: the Vision framework, through pyobjc.
- Linux: Tesseract, run as a program, when the tesseract-ocr package is installed.

All of them work offline. The recognised words become an invisible text layer
(pages.add_text_layer), so a scanned page can be searched, selected and
highlighted like any other.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtGui import QImage

from .document import Document

OCR_DPI = 200  # enough for body text; Windows rejects images over 10,000 px a side
MAX_SIDE = 9999  # the largest picture handed to any engine, in pixels
TESSERACT = {  # Tesseract's language codes: (two-letter code, name)
    "eng": ("en", "English"), "ita": ("it", "Italian"), "fra": ("fr", "French"), "spa": ("es", "Spanish"),
    "deu": ("de", "German"), "por": ("pt", "Portuguese"), "nld": ("nl", "Dutch"), "pol": ("pl", "Polish"),
    "rus": ("ru", "Russian"), "ukr": ("uk", "Ukrainian"), "ell": ("el", "Greek"), "tur": ("tr", "Turkish"),
    "swe": ("sv", "Swedish"), "dan": ("da", "Danish"), "nor": ("nb", "Norwegian"), "fin": ("fi", "Finnish"),
    "ces": ("cs", "Czech"), "ron": ("ro", "Romanian"), "hun": ("hu", "Hungarian"), "jpn": ("ja", "Japanese"),
    "chi_sim": ("zh", "Chinese (simplified)"), "chi_tra": ("zh", "Chinese (traditional)"),
    "kor": ("ko", "Korean"), "ara": ("ar", "Arabic"), "heb": ("he", "Hebrew"), "hin": ("hi", "Hindi"),
    "cat": ("ca", "Catalan"), "slk": ("sk", "Slovak"),
}


def engine() -> str:
    """Which engine this system uses: "windows", "vision", "tesseract", or "" when there is none."""
    if sys.platform == "win32":
        try:
            from winrt.windows.media.ocr import OcrEngine
        except ImportError:
            return ""
        return "windows" if OcrEngine.try_create_from_user_profile_languages() is not None else ""
    if sys.platform == "darwin":
        return "vision" if importlib.util.find_spec("Vision") is not None else ""
    return "tesseract" if shutil.which("tesseract") else ""


def available() -> bool:
    return bool(engine())


def languages() -> list[tuple[str, str]]:
    """The languages the engine can read: (tag, name), e.g. ("it-IT", "Italian (Italy)") or ("ita", "Italian")."""
    if sys.platform == "win32":
        try:
            from winrt.windows.media.ocr import OcrEngine
        except ImportError:
            return []
        return [(lang.language_tag, lang.display_name) for lang in OcrEngine.available_recognizer_languages]
    kind = engine()
    if kind == "vision":
        return [(tag, _locale_name(tag)) for tag in _vision_languages()]
    if kind == "tesseract":
        return [(code, TESSERACT.get(code, ("", code))[1]) for code in _tesseract_languages()]
    return []


def default_name() -> str:
    """What the empty language choice means here."""
    return {"win32": "Windows display language", "darwin": "Languages of this Mac"}.get(sys.platform,
                                                                                       "Language of the system")


def language_hint() -> str:
    """Where more recognition languages come from on this system."""
    if sys.platform == "win32":
        return "Windows can add more languages in Settings > Time & language > Language."
    if sys.platform == "darwin":
        return "macOS reads the languages its text recognition supports."
    return ("Zenith PDF reads scans with Tesseract: install the tesseract-ocr package, and one tesseract-ocr-xxx "
            "package per language (for example tesseract-ocr-ita).")


def _system_languages() -> list[str]:
    """The user's languages, most preferred first, as tags like "it-IT"."""
    from PySide6.QtCore import QLocale

    return [tag.replace("_", "-") for tag in QLocale.system().uiLanguages()] or ["en-US"]


def _locale_name(tag: str) -> str:
    from PySide6.QtCore import QLocale

    locale = QLocale(tag.replace("-", "_"))
    name = QLocale.languageToString(locale.language())
    return name if name and name != "C" else tag


def _picture(document: Document, index: int, dpi: float, limit: int = MAX_SIDE) -> tuple[QImage, float]:
    """The page as a grey picture for an engine, and its pixels per point."""
    width, height = document.page_size(index)
    scale = min(dpi / 72, limit / max(width, height))
    full_w, full_h = int(width * scale) // 4 * 4, int(height * scale)  # whole 4-byte grey rows
    data, w, h, stride = document.render_region(index, scale, (0, 0, full_w, full_h))
    grey = QImage(data, w, h, stride, QImage.Format.Format_RGB32).convertToFormat(QImage.Format.Format_Grayscale8)
    return grey, scale


def recognize_page(document: Document, index: int, language: str = "", dpi: int = OCR_DPI
                   ) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Words on a page with their boxes in display space (points). Slow: call off the GUI thread.

    `language` is a tag of languages(); empty means the system's languages.
    """
    kind = engine()
    if kind == "windows":
        return _windows(document, index, language, dpi)
    if kind == "vision":
        return _vision(document, index, language, dpi)
    if kind == "tesseract":
        return _tesseract(document, index, language, dpi)
    raise RuntimeError("Text recognition is not available on this computer.")


# Windows --------------------------------------------------------------------------------


def _windows(document: Document, index: int, language: str, dpi: int):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine_ = None
    if language:
        from winrt.windows.globalization import Language

        engine_ = OcrEngine.try_create_from_language(Language(language))
    engine_ = engine_ or OcrEngine.try_create_from_user_profile_languages()
    if engine_ is None:
        raise RuntimeError("Windows has no text recognition language installed.")
    grey, scale = _picture(document, index, dpi, OcrEngine.max_image_dimension - 1)
    h = grey.height()
    writer = DataWriter()
    writer.write_bytes(bytes(grey.constBits())[: grey.bytesPerLine() * h])
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.GRAY8,
                                                    grey.bytesPerLine(), h)

    async def run():
        return await engine_.recognize_async(bitmap)

    result = asyncio.run(run())
    words = []
    for line in result.lines:
        for word in line.words:
            box = word.bounding_rect
            words.append((word.text, (box.x / scale, box.y / scale, box.width / scale, box.height / scale)))
    return words


# macOS ----------------------------------------------------------------------------------


def _vision_languages() -> list[str]:
    import Vision

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    found, _error = request.supportedRecognitionLanguagesAndReturnError_(None)
    return [str(tag) for tag in (found or [])]


def _png(image: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def _vision(document: Document, index: int, language: str, dpi: int):
    import Quartz
    import Vision
    from Foundation import NSData, NSMakeRange

    grey, scale = _picture(document, index, dpi)
    png = _png(grey)
    source = Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(png, len(png)), None)
    picture = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    supported = _vision_languages()
    if language in supported:
        request.setRecognitionLanguages_([language])
    else:  # the user's own languages that Vision reads, else its default (English)
        mine = [tag for wanted in _system_languages() for tag in supported
                if tag.lower() == wanted.lower() or tag.split("-")[0].lower() == wanted.split("-")[0].lower()]
        if mine:
            request.setRecognitionLanguages_(list(dict.fromkeys(mine)))
        if hasattr(request, "setAutomaticallyDetectsLanguage_"):  # macOS 13 and later
            request.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(picture, None)
    done, error = handler.performRequests_error_([request], None)
    if not done:
        raise RuntimeError(str(error) if error is not None else "Vision could not read the page.")
    width, height = grey.width() / scale, grey.height() / scale  # in points
    words = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        line = str(candidate.string())
        start = 0
        for word in line.split():
            start = line.index(word, start)
            box, _error = candidate.boundingBoxForRange_error_(NSMakeRange(start, len(word)), None)
            start += len(word)
            if box is None:
                continue
            frame = box.boundingBox()  # 0 to 1, from the bottom left
            words.append((word, (frame.origin.x * width, (1 - frame.origin.y - frame.size.height) * height,
                                 frame.size.width * width, frame.size.height * height)))
    return words


# Linux ----------------------------------------------------------------------------------


def _tesseract_languages() -> list[str]:
    try:
        listed = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = [line.strip() for line in listed.stdout.splitlines()]
    return [line for line in lines[1:] if line and line != "osd"]  # the first line is a heading


def _tesseract_choice(language: str) -> str:
    """The -l argument: the chosen language, else the system's languages that are installed, plus English."""
    installed = _tesseract_languages()
    if language in installed:
        return language
    wanted = [code for tag in _system_languages() for code, (short, _name) in TESSERACT.items()
              if code in installed and short == tag.split("-")[0].lower()]
    if "eng" in installed:
        wanted.append("eng")
    return "+".join(dict.fromkeys(wanted))


def _tesseract(document: Document, index: int, language: str, dpi: int):
    choice = _tesseract_choice(language)
    grey, scale = _picture(document, index, dpi)
    with tempfile.TemporaryDirectory(prefix="zenith-ocr-") as folder:
        picture = Path(folder) / "page.png"
        grey.save(str(picture), "PNG")
        command = ["tesseract", str(picture), "stdout", "--dpi", str(round(scale * 72)), "tsv"]
        if choice:
            command[3:3] = ["-l", choice]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Tesseract did not run: {error}") from None
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "Tesseract failed.")
    words = []
    for row in csv.DictReader(io.StringIO(done.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            left, top, w, h = (float(row[k]) for k in ("left", "top", "width", "height"))
        except (KeyError, TypeError, ValueError):
            continue
        words.append((text, (left / scale, top / scale, w / scale, h / scale)))
    return words
