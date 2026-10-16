"""Annotations and pages on the system clipboard, so they paste across documents."""

from __future__ import annotations

import atexit
import base64
import json
from dataclasses import asdict, fields, replace

from PySide6.QtCore import QByteArray, QMimeData
from PySide6.QtWidgets import QApplication

from ..core.annotations import (
    Annotation,
    Attachment,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Link,
    Measurement,
    Note,
    Poly,
    Redaction,
    Shape,
    Tape,
    TextMarkup,
    new_id,
    union,
)

ANNOTATIONS_MIME = "application/x-zenith-pdf-annotations"
PAGES_MIME = "application/x-zenith-pdf-pages"
KINDS = {cls.__name__: cls for cls in (TextMarkup, Shape, Line, Ink, FreeText, Note, ImageStamp, Redaction, Poly,
                                       Tape, Measurement, Link, Attachment)}
BINARY = (ImageStamp, Attachment)  # their bytes travel as base64, and their digest is worked out again
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


def _tuples(value):
    """JSON turns tuples into lists; the annotation dataclasses want tuples back."""
    if isinstance(value, list):
        return tuple(_tuples(v) for v in value)
    return value


def to_records(annotations: list[Annotation]) -> list[dict]:
    """Annotations as JSON-ready dictionaries (clipboard, markup library). Pictures and files go in as base64."""
    records = []
    for a in annotations:
        record = {"kind": type(a).__name__, **asdict(a)}
        if isinstance(a, BINARY):
            record["data"] = base64.b64encode(a.data).decode("ascii")
            del record["digest"]
        records.append(record)
    return records


def from_records(records: list[dict], page: int, at: tuple[float, float] | None) -> list[Annotation]:
    """Fresh copies on `page`, their group's top-left moved to `at` (else nudged 12 pt).

    Text markup is left out: it belongs to the text under it. A record this
    version can't read (a kind or a value it doesn't know) is left out too;
    fields it doesn't know are ignored.
    """
    made = []
    for record in records:
        record = dict(record)
        cls = KINDS.get(record.pop("kind", None))
        if cls is None or cls is TextMarkup:
            continue
        known = {f.name for f in fields(cls) if f.init}
        values = {k: _tuples(v) for k, v in record.items() if k in known}
        try:
            if cls in BINARY:
                values["data"] = base64.b64decode(values.get("data", ""))
            made.append(replace(cls(**values), page=page, id=new_id(), locked=False))
        except (TypeError, ValueError):
            continue
    if not made:
        return []
    left, top, _, _ = union(a.bounds() for a in made)
    dx, dy = (at[0] - left, at[1] - top) if at else (12.0, 12.0)
    return [a.translated(dx, dy) for a in made]


def unreachable(annotations: list[Annotation], page_count: int) -> list[Annotation]:
    """Links to pages a document of `page_count` pages doesn't have: copied from a longer one, they would go
    nowhere, and saving would drop them."""
    return [a for a in annotations if isinstance(a, Link) and not a.uri and not 0 <= a.target < page_count]


def left_out(count: int) -> str:
    """What a paste says when it left links out (see unreachable)."""
    if count == 1:
        return "Left out a link to a page this document doesn't have."
    return f"Left out {count} links to pages this document doesn't have."


_clear_at_exit = False


def _set(mime: QMimeData) -> None:
    global _clear_at_exit
    QApplication.clipboard().setMimeData(mime)
    if not _clear_at_exit and QApplication.platformName() == "offscreen":
        # The offscreen clipboard (tests, scripts) crashes the process at exit while it
        # holds data set from Python; emptying it first avoids that.
        _clear_at_exit = True
        atexit.register(lambda: QApplication.instance() is not None and QApplication.clipboard().clear())


def copy_annotations(annotations: list[Annotation]) -> None:
    payload = to_records(annotations)
    mime = QMimeData()
    mime.setData(ANNOTATIONS_MIME, QByteArray(json.dumps(payload).encode("utf-8")))
    text = "\n".join(a.contents for a in annotations if a.contents)
    if text:
        mime.setText(text)
    _set(mime)


def has_annotations() -> bool:
    mime = QApplication.clipboard().mimeData()
    return mime is not None and mime.hasFormat(ANNOTATIONS_MIME)


def paste_annotations(page: int, at: tuple[float, float] | None) -> list[Annotation]:
    """Copies of the clipboard's annotations on `page`, their group's top-left moved to `at`.

    Text markup is left out: it belongs to the text under it.
    """
    mime = QApplication.clipboard().mimeData()
    if mime is None or not mime.hasFormat(ANNOTATIONS_MIME):
        return []
    try:
        records = json.loads(bytes(mime.data(ANNOTATIONS_MIME)).decode("utf-8"))
    except ValueError:  # not ours after all, or cut short
        return []
    return from_records(records, page, at) if isinstance(records, list) else []


def copy_pages(pdf: bytes) -> None:
    mime = QMimeData()
    mime.setData(PAGES_MIME, QByteArray(pdf))
    _set(mime)


def pasted_pages() -> bytes | None:
    mime = QApplication.clipboard().mimeData()
    if mime is None or not mime.hasFormat(PAGES_MIME):
        return None
    return bytes(mime.data(PAGES_MIME))


def text() -> str:
    mime = QApplication.clipboard().mimeData()
    return mime.text() if mime is not None and mime.hasText() else ""


def image():
    mime = QApplication.clipboard().mimeData()
    if mime is not None and mime.hasImage():
        return QApplication.clipboard().image()
    return None


def image_files(mime) -> list[str]:
    """Local image files named in a clipboard or drag-and-drop payload."""
    if mime is None or not mime.hasUrls():
        return []
    paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
    return [p for p in paths if p.lower().endswith(IMAGE_SUFFIXES)]


def has_image() -> bool:
    mime = QApplication.clipboard().mimeData()
    return mime is not None and (mime.hasImage() or bool(image_files(mime)))
