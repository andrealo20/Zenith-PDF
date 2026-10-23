"""Redaction that keeps the page: only what a marked area touches leaves the file.

Each thing the page's content draws (a run of text, a line or shape, a picture, a form) that a marked area touches is
taken out of the content; everything else stays as it was, word for word, its text still text. A picture of the page
as it was, with the marked areas filled in, takes the place of what went, so the rest of a redacted line still shows.
Then the marked areas are filled in. The pictures the page no longer draws leave its resources, and so the file.

PDFium says what each instruction draws and where: a copy of the content with each drawing instruction in marked
content of its own (/ZenithUnit <</Z n>> BDC ... EMC) is read back, and each object it finds carries its number.
When a run of text goes, the text after it on the same line keeps its place: the run becomes a move as long as it was.

Two checks make it safe. No character of the page's text and no object but the filling may be left in a marked area,
and the page must draw as before outside the parts that changed. A page that fails either, or whose content can't be
followed, becomes a picture of itself, filled in, as every redacted page used to.
"""

from __future__ import annotations

import ctypes
import io
import math
from dataclasses import dataclass, field

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageChops, ImageDraw
from pikepdf import Operator
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter

from . import pages
from .appearance import display_matrix
from .geometry import Box, PageGeometry
from .images import encode, xobject

MARK = "ZenithRedaction"  # the marked content around what redaction adds: the pictures and the filling
UNIT = "ZenithUnit"  # the marked content that numbers each drawing instruction, in the copy PDFium reads
MARGIN = 0.5  # points: an object this close to a marked area goes too
PATCH_MARGIN = 1.0  # points around a removed object that its picture also covers: the edges of its glyphs
CHECK_SCALE = 1.5  # the page before and after is compared at 108 dpi...
CHECK_LEVEL = 40  # ...and a pixel that changed by more than this...
CHECK_PIXELS = 12  # ...in more than this many places outside the redacted parts means it draws differently

CONSTRUCT = {"m", "l", "c", "v", "y", "h", "re"}
PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
SHOW = {"Tj", "TJ", "'", '"'}
PLACE = {"Td", "TD", "Tm", "T*", "'", '"', "ET"}  # after these, the text no longer follows on from the run before

Rect = tuple[float, float, float, float]  # (x, y, width, height) on the page as shown


class _Unfollowed(Exception):
    """The page's content can't be redacted in place: the page becomes a picture."""


@dataclass
class _Found:
    box: Box
    matrix: tuple[float, ...] = ()  # a run of text: where it starts, and its text matrix
    size: float = 0.0  # a run of text: its font size


@dataclass
class _Plan:
    content: list | None = None  # the page's instructions without what the marks touch; None: all stay
    patches: list[tuple[Rect, bytes]] = field(default_factory=list)  # pictures of what was removed, as PNG
    gone: set[str] = field(default_factory=set)  # the XObjects that were drawn by what went


def redact(pdf: pikepdf.Pdf, areas: dict[int, list[Rect]], geometries: dict[int, PageGeometry],
           fill: tuple[float, float, float], scale: float, render, keep) -> set[int]:
    """Black out `areas` ({page: [rects as shown]}) in `pdf` for good. The caller holds PDFium's lock.

    `scale` is the resolution of the pictures, in pixels per point. `render(index, scale)` draws a page as the reader
    sees it, a QImage, for a page that has to become a picture; `keep(annot, index, pictured)` says which of a
    page's annotations stay. Returns the pages that became pictures.
    """
    data = _bytes(pdf)
    instructions = {index: list(pikepdf.parse_content_stream(pdf.pages[index])) for index in areas}
    units = {index: _units(ops) for index, ops in instructions.items()}
    original, numbered = pdfium.PdfDocument(data), pdfium.PdfDocument(_numbered(data, instructions, units))
    pictured, plans = set(), {}
    try:
        for index, rects in areas.items():
            try:
                plans[index] = _plan(original[index], numbered[index], geometries[index], rects, instructions[index],
                                     units[index], fill, scale)
            except _Unfollowed:
                pictured.add(index)
        for index, plan in plans.items():
            _apply(pdf, index, plan, geometries[index], areas[index], fill)
        pictured |= set(_failing(pdf, original, {i: areas[i] for i in plans}, plans, geometries))
    finally:
        numbered.close()
        original.close()
    for index in areas:
        if index in pictured:
            _picture(pdf, index, geometries[index], areas[index], fill, scale, render, keep)
        else:
            pages.keep_annotations(pdf.pages[index].obj, lambda annot, i=index: keep(annot, i, False))
    return pictured


def _bytes(pdf: pikepdf.Pdf) -> bytes:
    buffer = io.BytesIO()
    pdf.save(buffer, encryption=False)  # read by PDFium only: no password needed
    return buffer.getvalue()


def _to_pdf(geometry: PageGeometry, rect: Rect) -> Box:
    x, y, w, h = rect
    (x0, y0), (x1, y1) = geometry.to_pdf(x, y), geometry.to_pdf(x + w, y + h)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _touches(a: Box, b: Box, margin: float = 0.0) -> bool:
    return a[0] < b[2] + margin and b[0] < a[2] + margin and a[1] < b[3] + margin and b[1] < a[3] + margin


def _merged(boxes: list[Box]) -> list[Box]:
    """The boxes, those that overlap made one."""
    boxes = list(boxes)
    joined = True
    while joined:
        joined = False
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                if _touches(a, b):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    joined = True
                    break
            if joined:
                break
    return boxes


def _pixels(rect: Rect, scale: float, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The pixels a rect as shown covers in a picture of the page at `scale`, whole pixels outwards."""
    x, y, w, h = rect
    return (max(0, math.floor(x * scale)), max(0, math.floor(y * scale)),
            min(size[0], math.ceil((x + w) * scale)), min(size[1], math.ceil((y + h) * scale)))


def _picture_of(page, scale: float) -> Image.Image:
    """The page's own content, without annotations or form fields: those stay annotations."""
    return page.render(scale=scale, draw_annots=False, may_draw_forms=False).to_pil().convert("RGB")


# What the content draws -----------------------------------------------------------------------------------------


def _units(ops) -> list[tuple[int, int]]:
    """The instructions that draw something, as (first, last) indices: a path from its first point to its painting,
    a run of text, a picture or form (Do), an inline picture, a shading."""
    units, start = [], None
    for index, (_, operator) in enumerate(ops):
        name = str(operator)
        if name in CONSTRUCT:
            start = index if start is None else start
        elif name in PAINT:
            if start is not None and name != "n":  # n only clips: it draws nothing
                units.append((start, index))
            start = None
        elif name in SHOW or name in ("Do", "sh", "INLINE IMAGE"):
            units.append((index, index))
    return units


def _numbered(data: bytes, instructions: dict, units: dict) -> bytes:
    """`data` with each drawing instruction of the redacted pages in marked content that carries its number."""
    with pikepdf.open(io.BytesIO(data)) as copy:
        for index, ops in instructions.items():
            first = {start: number for number, (start, _) in enumerate(units[index])}
            last = {end for _, end in units[index]}
            numbered = []
            for position, instruction in enumerate(ops):
                if position in first:
                    numbered.append(([pikepdf.Name(f"/{UNIT}"), pikepdf.Dictionary(Z=first[position])],
                                     Operator("BDC")))
                numbered.append(instruction)
                if position in last:
                    numbered.append(([], Operator("EMC")))
            copy.pages[index].obj.Contents = copy.make_stream(pikepdf.unparse_content_stream(numbered))
        return _bytes(copy)


def _marks(obj) -> list:
    """The marked content an object of the page is in."""
    return [pdfium_c.FPDFPageObj_GetMark(obj.raw, n) for n in range(pdfium_c.FPDFPageObj_CountMarks(obj.raw))]


def _mark_name(mark) -> str:
    length = ctypes.c_ulong()
    pdfium_c.FPDFPageObjMark_GetName(mark, None, 0, ctypes.byref(length))
    buffer = (ctypes.c_ushort * max(1, length.value // 2))()
    pdfium_c.FPDFPageObjMark_GetName(mark, buffer, length, ctypes.byref(length))
    return bytes(buffer)[:max(0, length.value - 2)].decode("utf-16-le")


def _unit(obj) -> int | None:
    """The number of the instruction an object came from, in the numbered copy."""
    for mark in _marks(obj):
        value = ctypes.c_int()
        if _mark_name(mark) == UNIT and pdfium_c.FPDFPageObjMark_GetParamIntValue(mark, b"Z", ctypes.byref(value)):
            return value.value
    return None


def _ours(obj) -> bool:
    """The object is one redaction added: it is inside its marked content."""
    return any(_mark_name(mark) == MARK for mark in _marks(obj))


def _found(page) -> tuple[dict[int, list[_Found]], list[Box]]:
    """What each numbered instruction of `page` draws, and the boxes of what no instruction claims."""
    found: dict[int, list[_Found]] = {}
    strays = []
    for obj in page.get_objects(max_depth=0):
        number, box = _unit(obj), obj.get_bounds()
        if number is None:
            strays.append(box)
            continue
        item = _Found(box)
        if obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
            size = ctypes.c_float()
            pdfium_c.FPDFTextObj_GetFontSize(obj.raw, ctypes.byref(size))
            item.matrix, item.size = tuple(obj.get_matrix().get()), size.value
        found.setdefault(number, []).append(item)
    return found, strays


# Taking out what the marks touch --------------------------------------------------------------------------------


def _plan(before, numbered, geometry: PageGeometry, rects: list[Rect], ops: list, units: list, fill,
          scale: float) -> _Plan:
    """What to take out of the page's instructions `ops`, read from `numbered`; its pictures come from `before`."""
    marks = [_to_pdf(geometry, r) for r in rects]
    found, strays = _found(numbered)
    if any(_touches(box, m, MARGIN) for box in strays for m in marks):
        raise _Unfollowed("something the content draws has no instruction of its own")
    removed = sorted(n for n, items in found.items() if any(_touches(i.box, m, MARGIN) for i in items for m in marks))
    if not removed:
        return _Plan()
    plan = _Plan()
    content = [[instruction] for instruction in ops]  # what each instruction becomes
    unit_at = {start: number for number, (start, _) in enumerate(units)}
    for number in removed:
        first, last = units[number]
        name = str(ops[first][1])
        if name in SHOW:
            content[first] = _moved_instead(ops, first, number, found, unit_at)
        elif first != last:  # a path: what it clips stays clipped, nothing is painted
            clips = any(str(ops[i][1]) in ("W", "W*") for i in range(first, last))
            for i in range(first, last + 1):
                content[i] = [ops[i]] if clips and i < last else []
            if clips:
                content[last] = [([], Operator("n"))]
        else:
            if name == "Do":
                plan.gone.add(str(ops[first][0][0]))
            content[first] = []
    plan.content = [instruction for replaced in content for instruction in replaced]
    plan.patches = _patches(before, geometry, rects, [i.box for n in removed for i in found[n]], fill, scale)
    return plan


def _kerning(instruction) -> float:
    """How far a TJ array moves before its first glyph, in thousandths of the font size (to the left)."""
    operands, operator = instruction
    total = 0.0
    if str(operator) == "TJ" and operands and isinstance(operands[0], pikepdf.Array):
        for item in operands[0]:
            if isinstance(item, pikepdf.String):
                break
            total += float(item)
    return total


def _moved_instead(ops: list, at: int, number: int, found: dict, unit_at: dict) -> list:
    """What a run of text that goes becomes: nothing, or the move it made when more text follows on from it."""
    operands, operator = ops[at]
    name = str(operator)
    instead = []
    if name == "'":
        instead.append(([], Operator("T*")))
    elif name == '"':
        instead += [([operands[0]], Operator("Tw")), ([operands[1]], Operator("Tc")), ([], Operator("T*"))]
    after = next((i for i in range(at + 1, len(ops)) if str(ops[i][1]) in SHOW | PLACE), None)
    if after is None or str(ops[after][1]) in PLACE:
        return instead
    mine, theirs = found[number][0], found.get(unit_at.get(after), [None])[0]
    if not mine.matrix or theirs is None or not theirs.matrix or not mine.size:
        raise _Unfollowed("the text after a removed run can't be placed")
    a, b, c, d, e, f = mine.matrix
    if any(abs(x - y) > 1e-4 * max(1.0, abs(x)) for x, y in zip(mine.matrix[:4], theirs.matrix[:4])):
        raise _Unfollowed("the text after a removed run is scaled or turned differently")
    det = a * d - b * c
    if abs(det) < 1e-9:
        raise _Unfollowed("a run of text has no width")
    dx, dy = theirs.matrix[4] - e, theirs.matrix[5] - f
    along = (d * dx - c * dy) / det  # how far the next run starts from this one, along the line, in its text space
    along += (_kerning(ops[after]) * theirs.size - _kerning(ops[at]) * mine.size) / 1000
    instead.append(([pikepdf.Array([round(-1000 * along / mine.size, 3)])], Operator("TJ")))
    return instead


def _patches(before, geometry: PageGeometry, rects: list[Rect], removed: list[Box], fill,
             scale: float) -> list[tuple[Rect, bytes]]:
    """Pictures of the page as it was where things were removed, the marked areas in them filled in."""
    picture = _picture_of(before, scale)
    draw = ImageDraw.Draw(picture)
    for rect in rects:
        x0, y0, x1, y1 = _pixels(rect, scale, picture.size)
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=tuple(round(c * 255) for c in fill))
    patches = []
    grown = [(x0 - PATCH_MARGIN, y0 - PATCH_MARGIN, x1 + PATCH_MARGIN, y1 + PATCH_MARGIN) for x0, y0, x1, y1 in removed]
    for box in _merged(grown):
        x0, y0, x1, y1 = _pixels(geometry.rect_to_display(box), scale, picture.size)
        if x1 > x0 and y1 > y0:
            png = io.BytesIO()
            picture.crop((x0, y0, x1, y1)).save(png, "PNG")
            patches.append(((x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale), png.getvalue()))
    return patches


def _resources(page_obj: pikepdf.Dictionary) -> pikepdf.Dictionary:
    """The page's resources, inherited from the page tree when it has none of its own."""
    node = page_obj
    while isinstance(node, pikepdf.Dictionary):
        if isinstance(node.get("/Resources"), pikepdf.Dictionary):
            return node.Resources
        node = node.get("/Parent")
    return pikepdf.Dictionary()


def _balanced(ops: list) -> list:
    """`ops` with the graphics states it leaves open closed, so what comes after draws on the page as it is."""
    depth = 0
    for _, operator in ops:
        if str(operator) == "q":
            depth += 1
        elif str(operator) == "Q":
            depth = max(0, depth - 1)
    return [([], Operator("q"))] + list(ops) + [([], Operator("Q"))] * (depth + 1)


def _apply(pdf: pikepdf.Pdf, index: int, plan: _Plan, geometry: PageGeometry, rects: list[Rect], fill) -> None:
    """Give the page its instructions without what went, the pictures of it, and the filling on top."""
    page = pdf.pages[index]
    ops = plan.content if plan.content is not None else list(pikepdf.parse_content_stream(page))
    resources = pikepdf.Dictionary({key: value for key, value in _resources(page.obj).items()})
    xobjects = resources.get("/XObject")
    xobjects = pikepdf.Dictionary({k: v for k, v in xobjects.items()}) if isinstance(xobjects, pikepdf.Dictionary) \
        else pikepdf.Dictionary()
    drawn = {str(operands[0]) for operands, operator in ops if str(operator) == "Do" and operands}
    for name in plan.gone - drawn:  # what the page no longer draws leaves the file with it
        if name in xobjects:
            del xobjects[name]
    added = [([pikepdf.Name(f"/{MARK}")], Operator("BMC")), ([], Operator("q")),
             ([float(v) for v in display_matrix(geometry)], Operator("cm"))]
    for number, ((x, y, w, h), png) in enumerate(plan.patches):
        name = f"/{MARK}{number}"
        while name in xobjects:
            name += "x"
        xobjects[name] = pdf.make_indirect(xobject(pdf, png))
        added += [([], Operator("q")), ([w, 0, 0, -h, x, y + h], Operator("cm")),
                  ([pikepdf.Name(name)], Operator("Do")), ([], Operator("Q"))]
    added.append(([float(c) for c in fill], Operator("rg")))
    added += [([x, y, w, h], Operator("re")) for x, y, w, h in rects] + [([], Operator("f"))]
    added += [([], Operator("Q")), ([], Operator("EMC"))]
    if len(xobjects):
        resources.XObject = xobjects
    elif "/XObject" in resources:
        del resources["/XObject"]
    page.obj.Resources = resources
    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(_balanced(ops) + added))
    if "/Thumb" in page.obj:
        del page.obj["/Thumb"]


# Checking ---------------------------------------------------------------------------------------------------------


def _left_in(page, marks: list[Box]) -> bool:
    """Something of the page's own is still in a marked area: an object, or a character of its text."""
    if any(_touches(obj.get_bounds(), m) and not _ours(obj) for obj in page.get_objects(max_depth=0) for m in marks):
        return True
    text = page.get_textpage()
    try:
        for number in range(text.count_chars()):
            if pdfium_c.FPDFText_IsGenerated(text.raw, number) == 1:
                continue
            if any(_touches(text.get_charbox(number), m) for m in marks):
                return True
    finally:
        text.close()
    return False


def _draws_as_before(before, after, rects: list[Rect], plan: _Plan) -> bool:
    """The page draws as it did, outside the marked areas and the pictures of what was removed."""
    a, b = _picture_of(before, CHECK_SCALE), _picture_of(after, CHECK_SCALE)
    if a.size != b.size:
        return False
    for picture in (a, b):
        draw = ImageDraw.Draw(picture)
        for rect in rects + [r for r, _ in plan.patches]:
            x0, y0, x1, y1 = _pixels(rect, CHECK_SCALE, picture.size)
            draw.rectangle((x0 - 2, y0 - 2, x1 + 1, y1 + 1), fill=(0, 0, 0))
    changed = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > CHECK_LEVEL else 0)
    return changed.histogram()[255] <= CHECK_PIXELS


def _failing(pdf: pikepdf.Pdf, original, areas, plans, geometries) -> list[int]:
    """The redacted pages that fail a check, read back from what `pdf` now holds."""
    final = pdfium.PdfDocument(_bytes(pdf))
    try:
        failing = []
        for index, rects in areas.items():
            page = final[index]
            marks = [_to_pdf(geometries[index], r) for r in rects]
            if _left_in(page, marks) or not _draws_as_before(original[index], page, rects, plans[index]):
                failing.append(index)
        return failing
    finally:
        final.close()


def _picture(pdf: pikepdf.Pdf, index: int, geometry: PageGeometry, rects: list[Rect], fill, scale: float, render,
             keep) -> None:
    """The page as a picture of itself, the marked areas filled in: nothing of what was under them is left."""
    image = render(index, scale)
    painter = QPainter(image)
    for x, y, w, h in rects:
        painter.fillRect(QRect(int(x * scale) - 1, int(y * scale) - 1, int(w * scale) + 3, int(h * scale) + 3),
                         QColor.fromRgbF(*fill))
    painter.end()
    pages.replace_with_picture(pdf, index, geometry, xobject(pdf, encode(image)),
                               lambda annot: keep(annot, index, True))
