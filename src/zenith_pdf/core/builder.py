"""Building one PDF out of pages from several, for the Doc Builder."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from . import pages, pdf_annots


@dataclass(frozen=True)
class Source:
    key: str
    path: Path
    password: str = ""


@dataclass(frozen=True)
class Entry:
    """One page of the result: a page of a source, or a new blank page (source None)."""

    source: str | None
    page: int = 0
    rotation: int = 0  # extra clockwise turn, in degrees
    size: tuple[float, float] = (595.0, 842.0)  # for blank pages


def build(sources: dict[str, Source], entries: list[Entry]) -> bytes:
    """The combined PDF. Annotations on the copied pages come along, as separate copies.

    A link to another page of the same source leads to that page's first copy in
    the result; if the result has no copy of it, the link is left out.
    """
    if not entries:
        raise ValueError("There are no pages to build.")
    out = pikepdf.new()
    opened: dict[str, pikepdf.Pdf] = {}
    numbers: dict[str, dict] = {}  # source -> its page objects' indices
    placed: dict[tuple[str, int], int] = {}  # (source, page) -> its first copy in the result
    copied: list[tuple[int, str, dict[int, int], pikepdf.Page]] = []  # result page, source, link targets, original
    turned: dict[int, int] = {}  # result page -> its extra turn, in degrees
    try:
        for entry in entries:
            if entry.source is None:
                out.pages.append(pages.new_page(out, entry.size, "blank"))
                continue
            if entry.source not in opened:
                source = sources[entry.source]
                opened[entry.source] = pikepdf.open(io.BytesIO(source.path.read_bytes()), password=source.password)
                numbers[entry.source] = pages.page_numbers(opened[entry.source])
            page = opened[entry.source].pages[entry.page]
            targets = pages.link_targets(page.obj, numbers[entry.source])
            out.pages.append(page)
            index = len(out.pages) - 1
            placed.setdefault((entry.source, entry.page), index)
            copied.append((index, entry.source, targets, page))
            if entry.rotation % 360:
                turned[index] = entry.rotation
        # Turned only now: a page copied again is a copy of its first copy, so a turn given to
        # the first before the second was added would turn both.
        for index, rotation in turned.items():
            out.pages[index].rotate(rotation, relative=True)

        def keep(source: str, targets: dict[int, int], position: int, link) -> bool:
            if position not in targets:
                return True
            found = placed.get((source, targets[position]))
            if found is not None:
                pages.aim_link(link, out.pages[found].obj)
            return found is not None

        for index, source, targets, original in copied:
            forms = pages.copy_form_fields(out, index, opened[source], [original])  # fields of the result's own form
            pages.detach_annotations(out, index, lambda position, link, s=source, t=targets: keep(s, t, position, link),
                                     forms=forms)
            if index in turned:  # only now: before detaching, copies of one page share their annotations
                pdf_annots.redraw_turned(out, index)
        buffer = io.BytesIO()
        out.save(buffer)
        return buffer.getvalue()
    finally:
        for pdf in opened.values():
            pdf.close()
