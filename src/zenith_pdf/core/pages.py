"""Page operations on the pikepdf document.

These functions only edit the PDF. Document.edit_pages wraps them: it writes the
current annotations into the PDF first, so annotations travel with their pages,
and reloads everything afterwards.
"""

from __future__ import annotations

import io
import math
import zlib

import pikepdf
from pikepdf import Array, Dictionary, Name

from . import pdf_annots
from .annotations import new_id
from .appearance import display_matrix, num, pdf_string
from .geometry import PageGeometry

MM = 72 / 25.4
TEMPLATES = {"blank": "Blank page", "lined": "Lined page", "grid": "Grid page", "dotted": "Dotted page"}
PAPER_SIZES = {"A4": (595.28, 841.89), "A3": (841.89, 1190.55), "A5": (419.53, 595.28),
               "Letter": (612.0, 792.0), "Legal": (612.0, 1008.0)}  # points, portrait
LINE_COLOUR = "0.72 0.79 0.89"


class SaveBuffer(io.RawIOBase):
    """Bytes a PDF is saved into, chunk by chunk; getvalue() gives them all.

    pikepdf holds the GIL for the whole of a save, so a large file written in a worker
    thread (Compress) would stop the window for most of a second. A write() in Python
    lets the interpreter hand the GIL over between chunks, and the chunks are kept as
    they come: a BytesIO would copy everything it holds each time it grows, in one go.
    """

    def __init__(self):
        super().__init__()
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, chunk) -> int:
        self._chunks.append(bytes(chunk))  # pikepdf reuses the memory it lends
        return len(chunk)

    def getvalue(self) -> bytes:
        return b"".join(self._chunks)


def template_content(kind: str, width: float, height: float) -> bytes:
    """Drawing operators for ruled paper, in PDF space (origin bottom-left)."""
    ops = []
    if kind == "lined":
        spacing = 8 * MM
        ops.append(f"{LINE_COLOUR} RG 0.5 w")
        y = height - 25 * MM
        while y > 12 * MM:
            ops.append(f"{num(12 * MM)} {num(y)} m {num(width - 12 * MM)} {num(y)} l S")
            y -= spacing
        ops.append(f"0.93 0.55 0.55 RG 0.6 w {num(25 * MM)} 0 m {num(25 * MM)} {num(height)} l S")
    elif kind == "grid":
        spacing = 5 * MM
        ops.append(f"{LINE_COLOUR} RG 0.35 w")
        x = spacing
        while x < width:
            ops.append(f"{num(x)} 0 m {num(x)} {num(height)} l S")
            x += spacing
        y = spacing
        while y < height:
            ops.append(f"0 {num(y)} m {num(width)} {num(y)} l S")
            y += spacing
    elif kind == "dotted":
        spacing = 5 * MM
        ops.append(f"{LINE_COLOUR} RG 1.3 w 1 J")
        y = spacing
        while y < height:
            x = spacing
            while x < width:
                ops.append(f"{num(x)} {num(y)} m {num(x)} {num(y)} l S")
                x += spacing
            y += spacing
    return "\n".join(ops).encode("ascii")


def new_page(pdf: pikepdf.Pdf, size: tuple[float, float], kind: str = "blank") -> pikepdf.Page:
    width, height = size
    page = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, float(width), float(height)]),
        Resources=Dictionary(),
        Contents=pdf.make_stream(template_content(kind, width, height)),
    )
    return pikepdf.Page(pdf.make_indirect(page))


def detach_annotations(pdf: pikepdf.Pdf, index: int, keep=None, forms: bool = False) -> None:
    """Give a copied page annotations of its own: fresh dictionaries (a page copied
    twice may otherwise share them), no /NM (new ids are assigned on import), no
    popups, and /P pointing at the new page. With `forms`, copy_form_fields gave
    the page widgets of its own, listed in /AcroForm: they stay as they are.

    `keep(position, copy)` may change each copy (`position` is where the original
    sat among the dictionaries in /Annots, see link_targets) and says whether it stays.
    """
    page = pdf.pages[index].obj
    annots = page.get("/Annots")
    if not isinstance(annots, Array):
        return
    kept = []
    for position, annot in enumerate(_entries(annots)):
        if str(annot.get("/Subtype", "")) == "/Popup":
            continue
        if forms and annot.get("/Subtype") == Name.Widget:
            copy = annot
        else:
            copy = Dictionary({key: annot[key] for key in annot.keys() if key not in ("/NM", "/Popup", "/IRT", "/P")})
        copy.P = page
        if keep is None or keep(position, copy):
            kept.append(copy if copy.is_indirect else pdf.make_indirect(copy))
    page.Annots = Array(kept)


def _entries(annots: Array) -> list[Dictionary]:
    """The annotations in /Annots, without the nulls and other junk qpdf drops when it copies a form."""
    return [annot for annot in annots if isinstance(annot, Dictionary)]


def copy_form_fields(pdf: pikepdf.Pdf, first: int, other: pikepdf.Pdf, copied: list) -> bool:
    """Make the form fields on pages just copied from `other` fields of pdf's form, renamed where a
    name is taken: pdf.pages[first + n] is the copy of copied[n]. Says whether `other` has a form.

    Without this the copied widgets are reachable from no /AcroForm, so filling one fills the original.
    """
    form = other.acroform
    if not form.exists:
        return False
    for offset, page in enumerate(copied):
        pdf.acroform.fix_copied_annotations(pdf.pages[first + offset], page, form)
    return True


def name_annotations(pdf: pikepdf.Pdf, subtypes) -> None:
    """Give every annotation of these subtypes an /NM of its own, where it has none or
    shares one: annotation ids then survive saving and loading again."""
    seen = set()
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not isinstance(annots, Array):
            continue
        for position in range(len(annots)):
            annot = annots[position]
            if not isinstance(annot, Dictionary) or str(annot.get("/Subtype", "")) not in subtypes:
                continue
            name = str(annot.get("/NM", ""))
            if name and name not in seen:
                seen.add(name)
                continue
            if not annot.is_indirect:
                annot = pdf.make_indirect(annot)
                annots[position] = annot
            name = new_id()
            annot.NM = pikepdf.String(name)
            seen.add(name)


# Links to pages. A page copied into another PDF brings its links along, but the
# pages they lead to stay behind (pikepdf leaves a null in their place). So the
# targets are read before copying and aimed again at the copies afterwards.


def _destination(link: Dictionary):
    """(holder, key) of a link's explicit destination [page /XYZ ...], or None."""
    if link.get("/Subtype") != Name.Link:
        return None
    action = link.get("/A")
    if isinstance(action, Dictionary):
        return (action, "/D") if action.get("/S") == Name.GoTo and isinstance(action.get("/D"), Array) else None
    return (link, "/Dest") if isinstance(link.get("/Dest"), Array) else None


def link_targets(page: Dictionary, numbers: dict) -> dict[int, int]:
    """Position among the dictionaries in /Annots -> index of the page the link there leads to,
    for links to pages. `numbers` maps page objects (objgen) to their indices."""
    targets = {}
    annots = page.get("/Annots")
    if not isinstance(annots, Array):
        return targets
    for position, annot in enumerate(_entries(annots)):
        found = _destination(annot)
        if found is None:
            continue
        holder, key = found
        destination = holder[key]
        if len(destination) and isinstance(destination[0], Dictionary) and destination[0].objgen in numbers:
            targets[position] = numbers[destination[0].objgen]
    return targets


def aim_link(link: Dictionary, page: Dictionary) -> None:
    """Point a link (a copy of its own, see detach_annotations) at `page`. The destination
    array and the action are replaced, not changed: the original may still share them."""
    holder, key = _destination(link)
    destination = Array(list(holder[key]))
    destination[0] = page
    if holder is link:
        link.Dest = destination
    else:
        action = Dictionary({k: holder[k] for k in holder.keys()})
        action.D = destination
        link.A = action


def page_numbers(pdf: pikepdf.Pdf) -> dict:
    return {page.obj.objgen: index for index, page in enumerate(pdf.pages)}


def forget_pages(pdf: pikepdf.Pdf, gone: set) -> None:
    """Take out what still leads to pages taken out of `pdf` (`gone` holds their objgens): links,
    bookmarks, named destinations and the opening view. Any of them would keep such a page, content
    and all, in the saved file. A bookmark with others under it stays, leading nowhere."""
    names = set()  # the named destinations that went with the pages

    def dead(destination) -> bool:
        """Whether a destination, or a GoTo action holding one, leads to a page that went."""
        if isinstance(destination, Dictionary):
            if destination.get("/S") != Name.GoTo:
                return False
            destination = destination.get("/D")
        if isinstance(destination, (pikepdf.String, Name)):
            return str(destination) in names
        return (isinstance(destination, Array) and len(destination) > 0
                and isinstance(destination[0], Dictionary) and destination[0].objgen in gone)

    def goal(obj: Dictionary):  # what a link or a bookmark leads to
        return obj.get("/A") if "/A" in obj else obj.get("/Dest")

    holder = pdf.Root.get("/Names")
    if isinstance(holder, Dictionary) and isinstance(holder.get("/Dests"), Dictionary):
        tree = pikepdf.NameTree(holder.Dests)
        for name in [name for name, value in tree.items()
                     if dead(value.get("/D") if isinstance(value, Dictionary) else value)]:
            del tree[name]
            names.add(name)
    old = pdf.Root.get("/Dests")  # named destinations as PDF 1.1 kept them
    if isinstance(old, Dictionary):
        for key in [key for key in old.keys()
                    if dead(old[key].get("/D") if isinstance(old[key], Dictionary) else old[key])]:
            del old[key]
            names.add(key)
    if dead(pdf.Root.get("/OpenAction")):
        del pdf.Root["/OpenAction"]
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not isinstance(annots, Array):
            continue
        kept = [a for a in annots
                if not (isinstance(a, Dictionary) and a.get("/Subtype") == Name.Link and dead(goal(a)))]
        if len(kept) < len(annots):
            page.obj.Annots = Array(kept)

    def prune(items: list) -> None:
        for position in reversed(range(len(items))):
            item = items[position]
            prune(item.children)
            if item.obj is None or not dead(goal(item.obj)):
                continue
            if item.children:
                item.destination = item.action = None
                for key in ("/Dest", "/A"):
                    if key in item.obj:
                        del item.obj[key]
            else:
                del items[position]

    if "/Outlines" in pdf.Root:
        with pdf.open_outline() as outline:
            prune(outline.root)


def new_document(size: tuple[float, float], kind: str = "blank", count: int = 1) -> bytes:
    pdf = pikepdf.new()
    for _ in range(max(count, 1)):
        pdf.pages.append(new_page(pdf, size, kind))
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def form_placement(form: pikepdf.Stream, rect: list[float]) -> list[float]:
    """The matrix that puts an appearance form onto its annotation Rect (PDF 32000, 12.5.5):
    the BBox, transformed by the form's Matrix, is scaled and moved onto Rect."""
    x0, y0, x1, y1 = (float(v) for v in form.BBox)
    a, b, c, d, e, f = (float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    corners = [(a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    bx0, by0 = min(p[0] for p in corners), min(p[1] for p in corners)
    bx1, by1 = max(p[0] for p in corners), max(p[1] for p in corners)
    left, bottom, right, top = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
    sx = (right - left) / (bx1 - bx0) if bx1 > bx0 else 1.0
    sy = (top - bottom) / (by1 - by0) if by1 > by0 else 1.0
    return [sx, 0.0, 0.0, sy, left - sx * bx0, bottom - sy * by0]


def flatten(pdf: pikepdf.Pdf, index: int, annots: list[Dictionary]) -> None:
    """Burn annotations into the page content: they look the same but are no longer editable."""
    page = pdf.pages[index]
    targets = {annot.objgen for annot in annots}
    ops = []
    for annot in annots:
        appearance = annot.get("/AP")
        form = appearance.get("/N") if isinstance(appearance, Dictionary) else None
        if isinstance(form, Dictionary) and not isinstance(form, pikepdf.Stream):  # states, e.g. /On /Off
            form = form.get(annot.get("/AS", Name.Off))
        if not isinstance(form, pikepdf.Stream) or "/BBox" not in form:
            continue
        form.Type, form.Subtype = Name.XObject, Name.Form
        name = page.add_resource(form, Name.XObject, prefix="ZenithFlat")
        state = ""
        opacity = float(annot.get("/CA", 1.0))
        if opacity < 1:
            gs = page.add_resource(Dictionary(Type=Name.ExtGState, CA=opacity, ca=opacity), Name.ExtGState,
                                   prefix="ZenithFlatGs")
            state = f"{gs} gs "
        matrix = " ".join(num(v) for v in form_placement(form, [float(v) for v in annot.Rect]))
        ops.append(f"q {state}{matrix} cm {name} Do Q")
    if ops:
        page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
        page.contents_add(pdf.make_stream(("Q\n" + "\n".join(ops)).encode("ascii")))
    kept = []
    for annot in page.obj.get("/Annots", Array()):
        if not isinstance(annot, Dictionary):
            continue  # a null or a reference to a missing object (pikepdf gives None for both)
        parent = annot.get("/Parent")
        if annot.objgen in targets or (parent is not None and parent.objgen in targets):
            continue  # flattened, or the popup of one that was
        kept.append(annot)
    page.obj.Annots = Array(kept)


def extract(pdf: pikepdf.Pdf, indices: list[int], origin: str = "") -> bytes:
    """A new PDF holding deep copies of the given pages.

    A link to one of these pages goes to its copy. A link to a page left behind
    would lead nowhere and is dropped; with an `origin` (a name for this version
    of `pdf`) it stays instead, holding its page's index in /ZenithTarget, so that
    insert_pdf with the same origin can aim it at that page again.
    """
    numbers = page_numbers(pdf)
    targets = [link_targets(pdf.pages[i].obj, numbers) for i in indices]
    out = pikepdf.new()
    for index in indices:
        out.pages.append(pdf.pages[index])
    forms = copy_form_fields(out, 0, pdf, [pdf.pages[i] for i in indices])
    copies: dict[int, int] = {}
    for position, index in enumerate(indices):
        copies.setdefault(index, position)

    def keep(page: int, position: int, link: Dictionary) -> bool:
        target = targets[page].get(position)
        if target is None:
            return True
        if target in copies:
            aim_link(link, out.pages[copies[target]].obj)
            return True
        link.ZenithTarget = target
        return bool(origin)

    for page in range(len(out.pages)):
        detach_annotations(out, page, lambda position, link, page=page: keep(page, position, link), forms)
    if origin:
        out.Root.ZenithOrigin = pikepdf.String(origin)
    buffer = io.BytesIO()
    out.save(buffer)
    return buffer.getvalue()


def insert_pdf(pdf: pikepdf.Pdf, at: int, data: bytes, password: str = "", origin: str = "") -> int:
    """Insert every page of another PDF before position `at`. Returns how many.

    Links between the inserted pages keep leading to each other. A link that
    extract() left a /ZenithTarget in leads to that page of `pdf` if the pages
    were extracted from it with the same `origin`; anywhere else it is dropped.
    """
    with pikepdf.open(io.BytesIO(data), password=password) as other:
        numbers = page_numbers(other)
        targets = [link_targets(page.obj, numbers) for page in other.pages]
        back = bool(origin) and str(other.Root.get("/ZenithOrigin", "")) == origin
        count, before = len(other.pages), len(pdf.pages)
        for offset, page in enumerate(other.pages):
            pdf.pages.insert(at + offset, page)
        forms = copy_form_fields(pdf, at, other, list(other.pages))
    groups: dict[str, str] = {}

    def keep(offset: int, position: int, link: Dictionary) -> bool:
        if "/ZenithGroup" in link:  # copies group among themselves, never with the originals
            link.ZenithGroup = pikepdf.String(groups.setdefault(str(link.ZenithGroup), new_id()))
        target = targets[offset].get(position)
        if target is not None:
            aim_link(link, pdf.pages[at + target].obj)
            return True
        if "/ZenithTarget" not in link:
            return True
        target = int(link.ZenithTarget)
        del link["/ZenithTarget"]
        if not back or not 0 <= target < before:
            return False
        aim_link(link, pdf.pages[target if target < at else target + count].obj)
        return True

    for offset in range(count):
        detach_annotations(pdf, at + offset, lambda position, link, offset=offset: keep(offset, position, link), forms)
    return count


def image_page(pdf: pikepdf.Pdf, rgb: bytes, width: int, height: int, size: tuple[float, float] | None = None) -> pikepdf.Page:
    """A page showing an RGB image, by default at 150 dpi; else fitted inside `size`."""
    image = pikepdf.Stream(pdf, zlib.compress(rgb))
    image.Type = Name.XObject
    image.Subtype = Name.Image
    image.Width, image.Height = width, height
    image.ColorSpace = Name.DeviceRGB
    image.BitsPerComponent = 8
    image.Filter = Name.FlateDecode
    if size is None:
        page_w, page_h = width * 72 / 150, height * 72 / 150
        scale, x, y = 72 / 150, 0.0, 0.0
    else:
        page_w, page_h = size
        scale = min(page_w / width, page_h / height)
        x, y = (page_w - width * scale) / 2, (page_h - height * scale) / 2
    content = f"q {num(width * scale)} 0 0 {num(height * scale)} {num(x)} {num(y)} cm /Im0 Do Q".encode("ascii")
    page = Dictionary(
        Type=Name.Page,
        MediaBox=Array([0, 0, page_w, page_h]),
        Resources=Dictionary(XObject=Dictionary(Im0=image)),
        Contents=pdf.make_stream(content),
    )
    return pikepdf.Page(pdf.make_indirect(page))


def replace_with_picture(pdf: pikepdf.Pdf, index: int, geometry: PageGeometry, picture: pikepdf.Stream,
                         keep_annots) -> None:
    """Make the page show only `picture`, covering what the reader sees of it.

    The old content and resources go, text included: this is what makes a
    redaction final. `keep_annots(annot)` says which annotations stay.
    """
    page = pdf.pages[index].obj
    width, height = geometry.size
    page.Resources = Dictionary(XObject=Dictionary(ZenithPage=pdf.make_indirect(picture)))
    matrix = " ".join(num(v) for v in display_matrix(geometry))
    # The picture's first row is the top of the page as the reader sees it.
    content = f"q {matrix} cm {num(width)} 0 0 {num(-height)} 0 {num(height)} cm /ZenithPage Do Q"
    page.Contents = pdf.make_stream(content.encode("ascii"))
    for key in ("/Group", "/Thumb", "/PieceInfo", "/StructParents"):
        if key in page:
            del page[key]
    keep_annotations(page, keep_annots)


def keep_annotations(page: Dictionary, keep) -> None:
    """Leave on the page only the annotations `keep(annot)` says stay."""
    annots = page.get("/Annots")
    if isinstance(annots, Array):
        kept = [a for a in annots if isinstance(a, Dictionary) and keep(a)]
        if kept:
            page.Annots = Array(kept)
        else:
            del page["/Annots"]


def rotate(pdf: pikepdf.Pdf, index: int, current: int, degrees: int) -> None:
    pdf.pages[index].obj.Rotate = (current + degrees) % 360
    pdf_annots.redraw_turned(pdf, index)  # appearances drawn for the new turn, as the canvas shows them


def extend_margin(pdf: pikepdf.Pdf, index: int, geometry: PageGeometry, fraction: float) -> None:
    """Add blank space on the right of the page as displayed, for notes."""
    left, bottom, right, top = geometry.box
    extra = geometry.size[0] * fraction
    if geometry.rotation == 0:
        right += extra
    elif geometry.rotation == 90:
        top += extra
    elif geometry.rotation == 180:
        left -= extra
    else:
        bottom -= extra
    page = pdf.pages[index].obj
    page.MediaBox = Array([left, bottom, right, top])
    for key in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
        if key in page:
            del page[key]


def watermark(
    pdf: pikepdf.Pdf,
    index: int,
    geometry: PageGeometry,
    text: str,
    text_width: float,
    font_size: float,
    color: tuple[float, float, float],
    opacity: float,
    angle: float,
) -> None:
    """Draw `text` across the middle of the page, rotated by `angle` degrees (counter-clockwise
    as the reader sees it), over the existing content. `text_width` is measured at `font_size`."""
    page = pdf.pages[index]
    page.add_resource(
        Dictionary(Type=Name.ExtGState, CA=float(opacity), ca=float(opacity)), Name.ExtGState, Name("/ZenithWmGs")
    )
    page.add_resource(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding),
        Name.Font,
        Name("/ZenithWmFont"),
    )
    width, height = geometry.size
    theta = math.radians(angle)
    cos, sin = math.cos(theta), math.sin(theta)
    cap = font_size * 0.72
    # Display space runs y downwards: the baseline goes (cos, -sin), "up" for glyphs is (-sin, -cos).
    x = width / 2 - text_width / 2 * cos + cap / 2 * sin
    y = height / 2 + text_width / 2 * sin + cap / 2 * cos
    r, g, b = color
    content = "\n".join([
        "Q q",
        " ".join(num(v) for v in display_matrix(geometry)) + " cm",
        "/ZenithWmGs gs",
        f"{num(r)} {num(g)} {num(b)} rg",
        f"BT /ZenithWmFont {num(font_size)} Tf",
        f"{num(cos)} {num(-sin)} {num(-sin)} {num(-cos)} {num(x)} {num(y)} Tm",
        f"{pdf_string(text)} Tj ET Q",
    ]).encode("ascii")
    # Wrap the old content in q/Q so its graphics state can't leak into the watermark.
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
    page.contents_add(pdf.make_stream(content))


def add_text_layer(pdf: pikepdf.Pdf, index: int, geometry: PageGeometry, words, measure) -> None:
    """Invisible, selectable text over scanned words: (text, (x, y, w, h) display rect).

    Text render mode 3 draws nothing but is found by search and selection.
    `measure(text, size)` gives the Helvetica width, used to stretch each word to its box.
    """
    page = pdf.pages[index]
    page.add_resource(
        Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding),
        Name.Font,
        Name("/ZenithOcr"),
    )
    ops = ["Q q", " ".join(num(v) for v in display_matrix(geometry)) + " cm", "BT 3 Tr"]
    for text, (x, y, w, h) in words:
        size = max(h * 0.9, 1.0)
        natural = measure(text, size) or 1.0
        stretch = 100 * w / natural
        ops.append(f"/ZenithOcr {num(size)} Tf {num(stretch)} Tz")
        ops.append(f"1 0 0 -1 {num(x)} {num(y + h * 0.82)} Tm {pdf_string(text)} Tj")
    ops.append("ET Q")
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
    page.contents_add(pdf.make_stream("\n".join(ops).encode("ascii")))
