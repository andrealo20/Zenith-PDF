"""Whole-document tools: page numbers, cropping, paper size, pages per sheet, cleaning,
compression and more. The features follow the list of the open source Stirling-PDF
project; the code is Zenith PDF's own, on top of pikepdf (qpdf).

Functions that take a `pdf` change it in place and are run through
Document.edit_pages, so they can be undone. The others build new files.
compress_data works on bytes and a copy of its own, so it can run in a
background thread while the window stays responsive.
"""

from __future__ import annotations

import io
import secrets
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name

from . import images
from .appearance import display_matrix, num, pdf_rect, pdf_string
from .geometry import PageGeometry, multiply
from .pages import PAPER_SIZES, SaveBuffer, flatten

POSITIONS = {
    "top-left": "Top left", "top-center": "Top centre", "top-right": "Top right",
    "bottom-left": "Bottom left", "bottom-center": "Bottom centre", "bottom-right": "Bottom right",
}
HELVETICA = dict(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding)
CAP_HEIGHT = 0.72  # of the font size, for Helvetica


def _wrap(pdf: pikepdf.Pdf, page: pikepdf.Page, content: str) -> None:
    """Draw `content` over the page, with the old content's graphics state sealed off."""
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
    page.contents_add(pdf.make_stream(("Q\n" + content).encode("ascii")))


# Page numbers -----------------------------------------------------------


def page_number_text(template: str, number: int, total: int) -> str:
    """ "Page {n} of {total}" with the numbers in. Only these two names are replaced."""
    return template.replace("{n}", str(number)).replace("{total}", str(total))


def add_page_numbers(pdf: pikepdf.Pdf, items, position: str, font_size: float, margin: float,
                     color: tuple[float, float, float]) -> None:
    """`items`: (page index, its PageGeometry, text, text width at font_size), one per numbered page."""
    vertical, horizontal = position.split("-")
    r, g, b = color
    for index, geometry, text, width in items:
        page = pdf.pages[index]
        font = page.add_resource(Dictionary(**HELVETICA), Name.Font, prefix="ZenithNum")
        page_w, page_h = geometry.size
        x = {"left": margin, "center": (page_w - width) / 2, "right": page_w - margin - width}[horizontal]
        y = margin + font_size * CAP_HEIGHT if vertical == "top" else page_h - margin
        _wrap(pdf, page, "\n".join([
            "q " + " ".join(num(v) for v in display_matrix(geometry)) + " cm",
            f"{num(r)} {num(g)} {num(b)} rg",
            f"BT {font} {num(font_size)} Tf 1 0 0 -1 {num(x)} {num(y)} Tm {pdf_string(text)} Tj ET Q",
        ]))


# Crop -------------------------------------------------------------------


def crop(pdf: pikepdf.Pdf, index: int, geometry: PageGeometry, rect) -> None:
    """Show only `rect` (display space, x y w h) of the page, by setting its CropBox.

    Nothing is deleted: a crop can be widened again, and other apps may still
    print the hidden part if they ignore the CropBox.
    """
    left, bottom, right, top = pdf_rect(geometry, rect)
    box = geometry.box
    left, bottom = max(left, box[0]), max(bottom, box[1])
    right, top = min(right, box[2]), min(top, box[3])
    if right - left < 1 or top - bottom < 1:
        raise ValueError("The crop leaves nothing of the page.")
    pdf.pages[index].obj.CropBox = Array([left, bottom, right, top])


# Placing whole pages ------------------------------------------------------


def _flatten_marks(pdf: pikepdf.Pdf, index: int) -> None:
    """Burn in every annotation that has an appearance, form fields included; links stay."""
    annots = pdf.pages[index].obj.get("/Annots")
    if not isinstance(annots, Array):
        return
    drawn = [a for a in annots if isinstance(a, Dictionary) and "/AP" in a
             and a.get("/Subtype") not in (Name.Link, Name.Popup)]
    if drawn:
        flatten(pdf, index, drawn)


def _placement(form: pikepdf.Stream, rect: tuple[float, float, float, float]) -> tuple[float, ...]:
    """The matrix that fits a page's form XObject inside `rect` (left, bottom, right, top), centred.

    The form's own /Matrix (rotation, user unit) is applied by Do; this only scales and moves.
    """
    x0, y0, x1, y1 = (float(v) for v in form.BBox)
    a, b, c, d, e, f = (float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    corners = [(a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    bx0, by0 = min(p[0] for p in corners), min(p[1] for p in corners)
    bx1, by1 = max(p[0] for p in corners), max(p[1] for p in corners)
    left, bottom, right, top = rect
    scale = min((right - left) / (bx1 - bx0), (top - bottom) / (by1 - by0))
    tx = left + ((right - left) - (bx1 - bx0) * scale) / 2 - bx0 * scale
    ty = bottom + ((top - bottom) - (by1 - by0) * scale) / 2 - by0 * scale
    return scale, 0.0, 0.0, scale, tx, ty


def _transform(point, matrix) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return a * x + c * y + e, b * x + d * y + f


def _move_links(links, matrix) -> list:
    """Links of a placed page, their Rect carried along to where the page now is."""
    moved = []
    for link in links:
        x0, y0, x1, y1 = (float(v) for v in link.Rect)
        corners = [_transform(p, matrix) for p in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        link.Rect = Array([min(p[0] for p in corners), min(p[1] for p in corners),
                           max(p[0] for p in corners), max(p[1] for p in corners)])
        for key in ("/QuadPoints", "/P"):
            if key in link:
                del link[key]
        moved.append(link)
    return moved


def _page_form(pdf: pikepdf.Pdf, page: pikepdf.Page) -> pikepdf.Object:
    return pdf.make_indirect(page.as_form_xobject(handle_transformations=True))


def resize_pages(pdf: pikepdf.Pdf, indices: list[int], paper: str, orientation: str, margin: float) -> None:
    """Put each page on paper of another size, its content scaled to fit and centred.

    `orientation` is "portrait", "landscape" or "auto" (the page's own). Annotations
    become part of the page first, since they could not follow the scaling.
    """
    width, height = PAPER_SIZES[paper]
    for index in indices:
        _flatten_marks(pdf, index)
        old = pdf.pages[index]
        form = _page_form(pdf, old)
        landscape = orientation == "landscape"
        if orientation == "auto":
            x0, y0, x1, y1 = (float(v) for v in form.BBox)
            a, b = (float(v) for v in form.get("/Matrix", [1, 0])[:2])
            wide, tall = (x1 - x0, y1 - y0) if abs(a) >= abs(b) else (y1 - y0, x1 - x0)
            landscape = wide > tall
        page_w, page_h = (max(width, height), min(width, height)) if landscape else (width, height)
        matrix = _placement(form, (margin, margin, page_w - margin, page_h - margin))
        links = [a for a in old.obj.get("/Annots", Array()) if isinstance(a, Dictionary) and a.get("/Subtype") == Name.Link]
        full = multiply(tuple(float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0])), matrix)
        new = pikepdf.Page(pdf.make_indirect(Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, page_w, page_h]),
            Resources=Dictionary(XObject=Dictionary(ZenithPage=form)),
            Contents=pdf.make_stream(f"q {' '.join(num(v) for v in matrix)} cm /ZenithPage Do Q".encode("ascii")),
        )))
        if links:
            new.obj.Annots = Array(_move_links(links, full))
        pdf.pages.insert(index, new)
        del pdf.pages[index + 1]


SHEET_GRIDS = {2: (2, 1, True), 4: (2, 2, False), 6: (3, 2, True), 8: (2, 4, False), 9: (3, 3, False),
               16: (4, 4, False)}  # pages per sheet: columns, rows, landscape sheet


def pages_per_sheet(data: bytes, per_sheet: int, paper: str, margin: float, gap: float, border: bool,
                    down_first: bool = False) -> bytes:
    """A new PDF with `per_sheet` pages of `data` on each sheet, in reading order (or down the columns)."""
    columns, rows, landscape = SHEET_GRIDS[per_sheet]
    width, height = PAPER_SIZES[paper]
    sheet_w, sheet_h = (max(width, height), min(width, height)) if landscape else (width, height)
    cell_w = (sheet_w - 2 * margin - gap * (columns - 1)) / columns
    cell_h = (sheet_h - 2 * margin - gap * (rows - 1)) / rows
    with pikepdf.open(io.BytesIO(data)) as pdf:
        count = len(pdf.pages)
        for index in range(count):
            _flatten_marks(pdf, index)
        forms = [_page_form(pdf, pdf.pages[i]) for i in range(count)]
        for first in range(0, count, per_sheet):
            names, ops = Dictionary(), []
            for slot, form in enumerate(forms[first:first + per_sheet]):
                column, row = (slot // rows, slot % rows) if down_first else (slot % columns, slot // columns)
                left = margin + column * (cell_w + gap)
                top = sheet_h - margin - row * (cell_h + gap)
                matrix = _placement(form, (left, top - cell_h, left + cell_w, top))
                name = f"P{slot}"
                names[f"/{name}"] = form
                ops.append(f"q {' '.join(num(v) for v in matrix)} cm /{name} Do Q")
                if border:
                    x0, y0, x1, y1 = (float(v) for v in form.BBox)
                    m = multiply(tuple(float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0])), matrix)
                    corners = [_transform(p, m) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
                    ops.append(f"q 0.6 g 0.6 G 0.5 w {num(min(xs))} {num(min(ys))} {num(max(xs) - min(xs))} "
                               f"{num(max(ys) - min(ys))} re S Q")
            pdf.pages.append(pikepdf.Page(pdf.make_indirect(Dictionary(
                Type=Name.Page,
                MediaBox=Array([0, 0, sheet_w, sheet_h]),
                Resources=Dictionary(XObject=names),
                Contents=pdf.make_stream("\n".join(ops).encode("ascii")),
            ))))
        for _ in range(count):
            del pdf.pages[0]
        buffer = io.BytesIO()
        pdf.save(buffer)
        return buffer.getvalue()


# New documents from pictures --------------------------------------------


def images_to_pdf(pictures: list[bytes], paper: str | None, margin: float, dpi: float = 150) -> bytes:
    """One page per picture. With `paper`, each picture is fitted on that paper, turned to suit
    the picture; without, the page is the picture's own size at `dpi`."""
    pdf = pikepdf.new()
    for data in pictures:
        pixels_w, pixels_h = images.size(data)
        if pixels_w <= 0 or pixels_h <= 0:
            continue
        if paper is None:
            page_w, page_h = pixels_w * 72 / dpi + 2 * margin, pixels_h * 72 / dpi + 2 * margin
        else:
            width, height = PAPER_SIZES[paper]
            landscape = pixels_w > pixels_h
            page_w, page_h = (max(width, height), min(width, height)) if landscape else (width, height)
        scale = min((page_w - 2 * margin) / pixels_w, (page_h - 2 * margin) / pixels_h)
        w, h = pixels_w * scale, pixels_h * scale
        x, y = (page_w - w) / 2, (page_h - h) / 2
        picture = pdf.make_indirect(images.xobject(pdf, data))
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, page_w, page_h]),
            Resources=Dictionary(XObject=Dictionary(Im0=picture)),
            Contents=pdf.make_stream(f"q {num(w)} 0 0 {num(h)} {num(x)} {num(y)} cm /Im0 Do Q".encode("ascii")),
        ))))
    if not len(pdf.pages):
        raise ValueError("None of the pictures could be read.")
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


# Cleaning ---------------------------------------------------------------


def _strip_actions(obj, found: list) -> None:
    """Remove JavaScript from an object's /A and /AA, wherever it appears."""
    if not isinstance(obj, Dictionary):
        return
    if "/AA" in obj:
        del obj["/AA"]
        found.append("AA")
    action = obj.get("/A")
    if isinstance(action, Dictionary) and action.get("/S") == Name.JavaScript:
        del obj["/A"]
        found.append("A")


def sanitize(pdf: pikepdf.Pdf, javascript: bool, files: bool, metadata: bool, links: bool) -> dict[str, int]:
    """Remove what could act on its own or carry hidden data. Returns how many of each went."""
    removed = {"javascript": 0, "files": 0, "metadata": 0, "links": 0}
    root = pdf.Root
    names = root.get("/Names")
    if javascript:
        found: list = []
        if isinstance(names, Dictionary) and "/JavaScript" in names:
            del names["/JavaScript"]
            found.append("names")
        opening = root.get("/OpenAction")
        if isinstance(opening, Dictionary) and opening.get("/S") == Name.JavaScript:
            del root["/OpenAction"]
            found.append("open")
        _strip_actions(root, found)
        for page in pdf.pages:
            _strip_actions(page.obj, found)
            for annot in page.obj.get("/Annots", Array()):
                _strip_actions(annot, found)
        form = root.get("/AcroForm")
        stack = list(form.get("/Fields", Array())) if isinstance(form, Dictionary) else []
        while stack:  # form fields, their kids and their kids
            field = stack.pop()
            _strip_actions(field, found)
            if isinstance(field, Dictionary):
                stack.extend(field.get("/Kids", Array()))
        removed["javascript"] = len(found)
    if files:
        if isinstance(names, Dictionary) and "/EmbeddedFiles" in names:
            removed["files"] += len(pdf.attachments)
            del names["/EmbeddedFiles"]
        for page in pdf.pages:
            annots = page.obj.get("/Annots")
            if isinstance(annots, Array):
                kept = [a for a in annots if not (isinstance(a, Dictionary) and a.get("/Subtype") == Name.FileAttachment)]
                removed["files"] += len(annots) - len(kept)
                page.obj.Annots = Array(kept)
    if metadata:
        if "/Info" in pdf.trailer:
            removed["metadata"] += len(pdf.trailer.Info.keys())
            del pdf.trailer["/Info"]
        if "/Metadata" in root:
            del root["/Metadata"]
            removed["metadata"] += 1
    if links:
        for page in pdf.pages:
            annots = page.obj.get("/Annots")
            if not isinstance(annots, Array):
                continue
            kept = []
            for annot in annots:
                action = annot.get("/A") if isinstance(annot, Dictionary) else None
                outside = isinstance(action, Dictionary) and action.get("/S") in (Name.URI, Name.Launch, Name.GoToR,
                                                                                    Name.SubmitForm, Name.ImportData)
                if annot.get("/Subtype") == Name.Link and outside:
                    removed["links"] += 1
                else:
                    kept.append(annot)
            page.obj.Annots = Array(kept)
    return removed


def flatten_forms(pdf: pikepdf.Pdf) -> None:
    """Form fields and every annotation with an appearance become part of the pages."""
    if "/AcroForm" in pdf.Root:
        pdf.generate_appearance_streams()
    pdf.flatten_annotations("all")
    if "/AcroForm" in pdf.Root:
        del pdf.Root["/AcroForm"]


# Document properties ----------------------------------------------------

METADATA_FIELDS = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
NOT_EMBEDDED = " (not embedded)"


def read_metadata(pdf: pikepdf.Pdf) -> dict[str, str]:
    info = pdf.docinfo
    values = {key: str(info.get(name, "")) for key, name in METADATA_FIELDS.items()}
    for key, name in (("creator", "/Creator"), ("producer", "/Producer"), ("created", "/CreationDate"),
                      ("modified", "/ModDate")):
        values[key] = str(info.get(name, ""))
    return values


def write_metadata(pdf: pikepdf.Pdf, values: dict[str, str]) -> None:
    """Title, author, subject and keywords, in both the Info dictionary and the XMP metadata."""
    xmp_names = {"title": "dc:title", "author": "dc:creator", "subject": "dc:description", "keywords": "pdf:Keywords"}
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        for key, xmp in xmp_names.items():
            value = values.get(key, "").strip()
            if xmp == "dc:creator":
                meta[xmp] = [value] if value else []
            elif value:
                meta[xmp] = value
            elif xmp in meta:
                del meta[xmp]
    info = pdf.docinfo
    for key, name in METADATA_FIELDS.items():
        value = values.get(key, "").strip()
        if value:
            info[name] = pikepdf.String(value)
        elif name in info:
            del info[name]


def describe(pdf: pikepdf.Pdf, file_size: int) -> list[tuple[str, str]]:
    """Facts about a PDF, for the properties dialog: (label, value).

    "Fonts" lists one font per line, "(not embedded)" after those the file doesn't carry.
    """
    sizes: dict[str, int] = {}
    fonts: dict[str, bool] = {}
    pictures = set()
    for page in pdf.pages:
        box = [float(v) for v in page.mediabox]
        w, h = abs(box[2] - box[0]), abs(box[3] - box[1])
        label = _paper_name(w, h)
        sizes[label] = sizes.get(label, 0) + 1
        resources = page.obj.get("/Resources")
        font_dict = resources.get("/Font") if isinstance(resources, Dictionary) else None
        if isinstance(font_dict, Dictionary):
            for font in font_dict.values():
                if isinstance(font, Dictionary):
                    name = " ".join(str(font.get("/BaseFont", "/(unnamed)"))[1:].split("+")[-1].split())
                    descriptor = font.get("/FontDescriptor")
                    if "/DescendantFonts" in font:
                        descriptor = font.DescendantFonts[0].get("/FontDescriptor")
                    embedded = isinstance(descriptor, Dictionary) and any(
                        k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
                    fonts[name] = fonts.get(name, False) or embedded
        for image in page.get_images().values():
            pictures.add(image.objgen)
    encryption = "None"
    if pdf.is_encrypted:
        method = pdf.encryption
        encryption = f"Yes, {method.stream.name if hasattr(method.stream, 'name') else method.stream}"
    allowed = pdf.allow
    restrictions = [label for label, flag in (("printing", allowed.print_highres), ("copying", allowed.extract),
                                              ("editing", allowed.modify_other), ("annotating", allowed.modify_annotation))
                    if not flag]
    form = pdf.Root.get("/AcroForm")
    return [
        ("File size", _size(file_size)),
        ("PDF version", pdf.pdf_version),
        ("Pages", str(len(pdf.pages))),
        ("Page sizes", ", ".join(f"{label} ({count})" if len(sizes) > 1 else label for label, count in sizes.items())),
        ("Fonts", "\n".join(f"{name}{'' if embedded else NOT_EMBEDDED}" for name, embedded in sorted(fonts.items()))
         or "None"),
        ("Pictures", str(len(pictures))),
        ("Form fields", "Yes" if isinstance(form, Dictionary) and len(form.get("/Fields", Array())) else "No"),
        ("Attachments", str(len(pdf.attachments))),
        ("Encrypted", encryption),
        ("Restrictions", ", ".join(restrictions) if restrictions else "None"),
    ]


def _paper_name(width: float, height: float) -> str:
    for name, (w, h) in PAPER_SIZES.items():
        if (abs(width - w) < 3 and abs(height - h) < 3) or (abs(width - h) < 3 and abs(height - w) < 3):
            return f"{name} {'landscape' if width > height else 'portrait'}"
    return f"{width / 72 * 25.4:.0f} × {height / 72 * 25.4:.0f} mm"


def _size(count: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "bytes" else f"{count:.1f} {unit}"
        count /= 1024
    return str(count)


human_size = _size


# Pictures ---------------------------------------------------------------


COMPONENTS = {"/DeviceGray": 1, "/DeviceRGB": 3, "/DeviceCMYK": 4}
MODES = {1: "L", 3: "RGB", 4: "CMYK"}  # Pillow's names for 1, 3 and 4 components of 8 bits
LOSSLESS_FILTERS = {"/FlateDecode", "/LZWDecode", "/ASCII85Decode", "/ASCIIHexDecode", "/RunLengthDecode"}
JPEG_GAIN = 0.9  # a JPEG that keeps its size is replaced only by one at most this big, against generation loss


class Cancelled(Exception):
    """The user stopped a long job: nothing it made is used."""


@dataclass(frozen=True)
class Compressed:
    data: bytes  # the whole PDF, with the encryption of the input: every password that opened it still does
    changed: int  # pictures stored smaller
    left: int  # large pictures of a kind Compress can't rewrite, kept as they were


def _filters(obj) -> list[str]:
    value = obj.get("/Filter")
    if value is None:
        return []
    return [str(f) for f in value] if isinstance(value, Array) else [str(value)]


def _components(space) -> int | None:
    """Colour components of a space whose samples can be resized and stored as JPEG as they are.

    Grey, RGB and CMYK, also with an ICC profile (kept) or calibrated. Palettes, Lab,
    spot colours and the rest stay untouched.
    """
    if isinstance(space, Name):
        return COMPONENTS.get(str(space))
    if isinstance(space, Array) and len(space) == 2:
        kind = str(space[0])
        if kind == "/ICCBased" and isinstance(space[1], pikepdf.Stream):
            count = int(space[1].get("/N", 0))
            return count if count in MODES else None
        return {"/CalGray": 1, "/CalRGB": 3}.get(kind)
    return None


def _samples(obj) -> bytes:
    """The decoded bytes of a losslessly stored picture.

    Plain Flate is inflated with Python's zlib, which lets other threads run while
    it works; qpdf, used for the rest, keeps them waiting.
    """
    parms = obj.get("/DecodeParms")
    if isinstance(parms, Array):
        parms = parms[0] if len(parms) == 1 else None
    plain = parms is None or (isinstance(parms, Dictionary) and int(parms.get("/Predictor", 1)) <= 1)
    if _filters(obj) == ["/FlateDecode"] and plain:
        try:
            return zlib.decompress(obj.read_raw_bytes())
        except zlib.error:
            pass  # damaged or padded data: qpdf is more forgiving
    return obj.read_bytes()


def _decode(obj, target: tuple[int, int] | None):
    """(Pillow image, stored as JPEG) for a picture Compress can rewrite, or None.

    `target` is the size the picture will be shrunk to, if any: a JPEG then decodes
    straight at a smaller scale, which is much faster.
    """
    from PIL import Image

    if obj.get("/ImageMask", False) or isinstance(obj.get("/Mask"), Array):
        return None  # stencils, and colour keys that lossy data would break
    soft = obj.get("/SMask")
    if isinstance(soft, pikepdf.Stream) and "/Matte" in soft:
        return None  # blended with its mask beforehand: the two must keep matching samples
    bits = int(obj.get("/BitsPerComponent", 0))
    count = _components(obj.get("/ColorSpace"))
    width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
    if count is None or bits not in (8, 16) or width <= 0 or height <= 0:
        return None
    filters = _filters(obj)
    try:
        if filters == ["/DCTDecode"]:
            parms = obj.get("/DecodeParms")
            if bits != 8 or (isinstance(parms, Dictionary) and "/ColorTransform" in parms):
                return None
            picture = Image.open(io.BytesIO(obj.read_raw_bytes()))
            if target is not None:
                picture.draft(picture.mode, target)
            picture.load()
            if picture.mode != MODES[count]:
                return None
            return picture, True
        if not set(filters) <= LOSSLESS_FILTERS:
            return None  # JPEG 2000, JBIG2, fax and the like
        data = _samples(obj)
        if bits == 16:
            data = data[0::2]  # the high byte of each big-endian sample
        size = width * height * count
        if len(data) < size:
            return None
        return Image.frombytes(MODES[count], (width, height), data[:size]), False
    except Exception:
        return None


def _jpeg(picture, quality: int) -> bytes:
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _shrink_mask(mask: pikepdf.Stream, size: tuple[int, int]) -> None:
    """Bring a soft mask down to its picture's new size, still lossless. Left alone if unreadable."""
    from PIL import Image

    width, height = int(mask.get("/Width", 0)), int(mask.get("/Height", 0))
    if int(mask.get("/BitsPerComponent", 0)) != 8 or (width <= size[0] and height <= size[1]):
        return
    if not set(_filters(mask)) <= LOSSLESS_FILTERS:
        return
    try:
        data = _samples(mask)
        if len(data) < width * height:
            return
        alpha = Image.frombytes("L", (width, height), data[:width * height]).resize(size, Image.LANCZOS)
    except Exception:
        return  # a mask may differ in size from its picture: the old one still fits
    mask.write(zlib.compress(alpha.tobytes(), 6), filter=Name.FlateDecode)
    mask.Width, mask.Height = alpha.size
    if "/DecodeParms" in mask:
        del mask["/DecodeParms"]


def _flip_decode(obj, count: int) -> None:
    """Pillow stores CMYK JPEGs inverted, as Adobe does: swap each /Decode pair to undo it."""
    values = [float(v) for v in obj.get("/Decode", [0, 1] * count)]
    flipped = [v for low, high in zip(values[0::2], values[1::2]) for v in (high, low)]
    if flipped == [0.0, 1.0] * count:
        if "/Decode" in obj:
            del obj["/Decode"]
    else:
        obj.Decode = Array(flipped)


def _compress_picture(obj: pikepdf.Stream, max_side: int, quality: int, shrunk_masks: set) -> str:
    """Rewrite one picture if that makes it smaller: "changed", "kept" or "left" (a kind we can't rewrite)."""
    from PIL import Image

    width, height = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
    too_big = max(width, height) > max_side
    target = None
    if too_big:
        factor = max_side / max(width, height)
        target = (max(1, round(width * factor)), max(1, round(height * factor)))
    decoded = _decode(obj, target)
    if decoded is None:
        return "left" if too_big else "kept"
    picture, was_jpeg = decoded
    sixteen = int(obj.get("/BitsPerComponent", 8)) == 16
    if not too_big and not was_jpeg and not sixteen:
        return "kept"  # lossless pictures within the limit: likely drawings or text, leave them sharp
    original = len(obj.read_raw_bytes())
    if target is not None and picture.size != target:
        picture = picture.resize(target, Image.LANCZOS)
    candidates = []
    if too_big or was_jpeg:
        candidates.append(("jpeg", _jpeg(picture, quality)))
    if not was_jpeg and (not candidates or 2 * len(candidates[0][1]) > original):
        # A drawing or a screenshot: lossless at the new size may beat JPEG, and stays sharp.
        candidates.append(("flate", zlib.compress(picture.tobytes(), 6)))
    kind, data = min(candidates, key=lambda c: len(c[1]))
    limit = original * (JPEG_GAIN if was_jpeg and not too_big else 1)
    if len(data) >= limit:
        return "kept"
    count = len(picture.getbands())
    obj.write(data, filter=Name.DCTDecode if kind == "jpeg" else Name.FlateDecode)
    if "/DecodeParms" in obj:
        del obj["/DecodeParms"]
    obj.Width, obj.Height = picture.size
    obj.BitsPerComponent = 8  # the colour space stays, ICC profile included
    if count == 4 and kind == "jpeg" and not was_jpeg:
        _flip_decode(obj, count)
    soft = obj.get("/SMask")
    if too_big and isinstance(soft, pikepdf.Stream) and soft.objgen not in shrunk_masks:
        shrunk_masks.add(soft.objgen)
        _shrink_mask(soft, picture.size)
    return "changed"


def compress_images(pdf: pikepdf.Pdf, max_side: int, quality: int,
                    progress: Callable[[int, int], None] | None = None, cancelled=None) -> tuple[int, int]:
    """Shrink pictures larger than `max_side` pixels and store photographs as JPEG at `quality`.

    A picture is only replaced when the result is smaller, and a JPEG that keeps its
    size only when that saves at least a tenth, so running Compress again doesn't wear
    pictures down. Soft masks are never stored lossy: they shrink with their picture.
    ICC profiles stay. 16-bit pictures become 8-bit; CMYK stays CMYK.

    `progress(done, total)` is called before each picture and once at the end;
    `cancelled` is a threading.Event, checked between pictures (raises Cancelled).
    Returns (pictures changed, large pictures of a kind left as they were).
    """
    pictures, masks = [], set()
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == Name.Image:
            pictures.append(obj)
            for key in ("/SMask", "/Mask"):
                mask = obj.get(key)
                if isinstance(mask, pikepdf.Stream):
                    masks.add(mask.objgen)
    pictures = [obj for obj in pictures if obj.objgen not in masks]  # masks change only with their picture
    changed = left = 0
    shrunk_masks: set = set()
    for number, obj in enumerate(pictures):
        if cancelled is not None and cancelled.is_set():
            raise Cancelled()
        if progress is not None:
            progress(number, len(pictures))
        outcome = _compress_picture(obj, max_side, quality, shrunk_masks)
        changed += outcome == "changed"
        left += outcome == "left"
    if progress is not None:
        progress(len(pictures), len(pictures))
    return changed, left


def compress_data(data: bytes, password: str | None, side: int | None, quality: int | None,
                  progress: Callable[[int, int], None] | None = None, cancelled=None) -> Compressed | None:
    """Compress a whole PDF given as bytes, on a copy of its own: safe in a worker thread.

    With `side` (and `quality`), pictures go through compress_images; without, only
    the rest is packed. Everything is written once, with object streams, which later
    saves keep. The input may be encrypted (`password` opens it); the result keeps that
    encryption exactly, user and owner passwords included, whichever of them was typed.
    Returns None if `cancelled` was set before the end.
    """
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError:
        pdf = pikepdf.open(io.BytesIO(data), password=password or "")
    with pdf:
        changed = left = 0
        try:
            if side:
                changed, left = compress_images(pdf, side, quality or 80, progress, cancelled)
            elif progress is not None:
                progress(0, 0)
        except Cancelled:
            return None
        if cancelled is not None and cancelled.is_set():
            return None
        buffer = SaveBuffer()  # in a worker thread: the window gets its turn between chunks
        # Rebuilt from settings, the encryption would lose the password nobody typed (and RC4 files would not
        # save at all): copied as it is, it stays the file's own.
        pdf.save(buffer, object_stream_mode=pikepdf.ObjectStreamMode.generate, encryption=pdf.is_encrypted)
    if cancelled is not None and cancelled.is_set():
        return None
    return Compressed(buffer.getvalue(), changed, left)


def extract_images(pdf: pikepdf.Pdf, folder: Path, stem: str, min_side: int = 16) -> int:
    """Save every picture of the document into `folder` once. Returns how many were written."""
    seen, written = set(), 0
    for number, page in enumerate(pdf.pages, start=1):
        for name, raw in page.get_images().items():
            if raw.objgen in seen:
                continue
            seen.add(raw.objgen)
            try:
                image = pikepdf.PdfImage(raw)
                if min(image.width, image.height) < min_side:
                    continue
                written += 1
                image.extract_to(fileprefix=str(folder / f"{stem} - page {number} - {written}"))
            except Exception:
                continue
    return written


# Files ------------------------------------------------------------------


def repair(source: Path, target: Path) -> None:
    """Rewrite a damaged PDF: qpdf rebuilds what it can of the cross-reference table and streams."""
    with pikepdf.open(source, attempt_recovery=True) as pdf:
        pdf.save(target, object_stream_mode=pikepdf.ObjectStreamMode.generate)


def new_owner_password() -> str:
    """A long random owner password, so the permissions can't be lifted with an empty one."""
    return secrets.token_urlsafe(24)


def encryption(user: str, owner: str, allow: dict[str, bool]) -> pikepdf.Encryption:
    """AES-256 encryption with these passwords and permissions."""
    permissions = pikepdf.Permissions(
        print_lowres=allow.get("print", True),
        print_highres=allow.get("print", True),
        extract=allow.get("copy", True),
        accessibility=True,
        modify_other=allow.get("edit", True),
        modify_assembly=allow.get("assemble", True),
        modify_annotation=allow.get("annotate", True),
        modify_form=allow.get("annotate", True),
    )
    return pikepdf.Encryption(owner=owner or new_owner_password(), user=user, R=6, allow=permissions)


def blank(gray: bytes, threshold: float) -> bool:
    """Whether a page, as grey pixels, has less than `threshold` (a fraction) of dark ones."""
    if not gray:
        return True
    dark = bytes(range(0, 225))
    ink = len(gray) - len(gray.translate(None, dark))
    return ink / len(gray) < threshold


def safe_file_name(text: str, fallback: str = "part") -> str:
    """A name Windows accepts for a file, from a bookmark title or similar."""
    cleaned = "".join(c if c not in '<>:"/\\|?*' and ord(c) >= 32 else " " for c in text).strip(" .")
    return " ".join(cleaned.split())[:80] or fallback


def page_ranges(starts: list[int], count: int) -> list[range]:
    """Consecutive ranges that begin at each of `starts` (0-based) and cover pages up to `count`."""
    points = sorted({s for s in starts if 0 < s < count} | {0})
    return [range(a, b) for a, b in zip(points, points[1:] + [count])]


def chunks(count: int, size: int) -> list[range]:
    return [range(a, min(a + size, count)) for a in range(0, count, max(size, 1))]


def mm(value: float) -> float:
    """Millimetres to points."""
    return value * 72 / 25.4

