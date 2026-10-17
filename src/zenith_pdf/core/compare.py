"""Comparing two versions of a PDF: the words of each, where they are, and what was added, removed or changed.

Words come from PDFium's characters, with the box of each word on its page (display space). The two lists are
lined up with difflib over the whole document, so text that moved to another page still lines up. Compare
words as they read: case and punctuation count, spacing and line breaks don't.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .document import PDFIUM_LOCK, Document

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Word:
    text: str
    page: int  # from 0
    rect: Rect  # display space: x, y, width, height


@dataclass
class Change:
    kind: str  # "added", "removed" or "changed"
    old: list[Word] = field(default_factory=list)
    new: list[Word] = field(default_factory=list)

    @property
    def page(self) -> int:
        """The page it is on in the newer version (or the older one, for what was removed)."""
        return (self.new or self.old)[0].page

    def summary(self, limit: int = 60) -> str:
        def text(words) -> str:
            joined = " ".join(w.text for w in words)
            return joined if len(joined) <= limit else joined[: limit - 1] + "…"

        if self.kind == "added":
            return text(self.new)
        if self.kind == "removed":
            return text(self.old)
        return f"{text(self.old)}  →  {text(self.new)}"


def words(document: Document, stop=None) -> list[Word]:
    """Every word of `document`, page by page in reading order, with its box. `stop()` True ends early."""
    found: list[Word] = []
    for index in range(document.page_count):
        if stop is not None and stop():
            break
        with PDFIUM_LOCK:
            geometry = document.geometry(index)
            textpage = document._textpage(index)
            count = textpage.count_chars()
            text = textpage.get_text_range(0, count) if count else ""
            boxes = [textpage.get_charbox(i) for i in range(count)] if len(text) == count else []
        if not boxes:  # characters outside the Basic Multilingual Plane: words without boxes, rather than wrong ones
            found += [Word(w, index, (0.0, 0.0, 0.0, 0.0)) for w in text.split()]
            continue
        start = None
        for i in range(count + 1):
            letter = i < count and not text[i].isspace()
            if letter and start is None:
                start = i
            elif not letter and start is not None:
                left = min(boxes[j][0] for j in range(start, i))
                bottom = min(boxes[j][1] for j in range(start, i))
                right = max(boxes[j][2] for j in range(start, i))
                top = max(boxes[j][3] for j in range(start, i))
                found.append(Word(text[start:i], index, geometry.rect_to_display((left, bottom, right, top))))
                start = None
    return found


def compare(old: list[Word], new: list[Word]) -> list[Change]:
    """What changed from `old` to `new`, in the order of the newer version."""
    matcher = difflib.SequenceMatcher(None, [w.text for w in old], [w.text for w in new], autojunk=False)
    changes = []
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        kind = {"insert": "added", "delete": "removed", "replace": "changed"}[tag]
        changes.append(Change(kind, old[a1:a2], new[b1:b2]))
    return changes


def counts(changes: list[Change]) -> dict[str, int]:
    found = {"added": 0, "removed": 0, "changed": 0}
    for change in changes:
        found[change.kind] += 1
    return found
