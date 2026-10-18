"""Reading and writing annotation dictionaries with pikepdf.

Existing annotations are updated in place: only the keys Zenith PDF manages are
rewritten, so replies, popups and other applications' private keys survive.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime, timezone

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from . import appearance, images
from .annotations import (
    AUDIO_SUFFIXES,
    CARET_BLUE,
    DASHES,
    ENDINGS,
    NOTE_ICONS,
    PATTERNS,
    STAMP_NAMES,
    STAMPS,
    Annotation,
    Attachment,
    Caret,
    Color,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Link,
    Measurement,
    Note,
    Point,
    Poly,
    Redaction,
    Shape,
    Tape,
    TextMarkup,
    TextStamp,
    drawn_dashes,
    new_id,
    points_bounds,
    quarter_turn,
)
from .geometry import PageGeometry, multiply, visible_box

log = logging.getLogger(__name__)

MARKUPS = {"/Highlight", "/Underline", "/Squiggly", "/StrikeOut"}
SUPPORTED = MARKUPS | {"/Square", "/Circle", "/Line", "/Ink", "/FreeText", "/Text", "/Stamp", "/Redact", "/PolyLine",
                       "/Polygon", "/Link", "/FileAttachment", "/Caret"}
DIMENSIONS = {"/LineDimension": "distance", "/PolyLineDimension": "perimeter", "/PolygonDimension": "area",
              "/ZenithAngle": "angle", "/ZenithVolume": "volume"}  # the last two are Zenith PDF's own
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# Keys rewritten from scratch on every write: an annotation that no longer needs one loses it.
MANAGED_KEYS = ("/RD", "/IC", "/LE", "/AS", "/IT", "/CL", "/BE", "/Measure", "/Vertices", "/Subj", "/Name",
                "/ZenithHighlighter", "/ZenithWidths", "/ZenithBorderColor", "/ZenithBorderWidth", "/ZenithRotation",
                "/ZenithEquationSize", "/ZenithEquationColor", "/ZenithGroup", "/ZenithStampText",
                "/ZenithStampDetail", "/ZenithStampName", "/ZenithPattern", "/ZenithDepth")
ALIGNS = ("left", "center", "right")  # a text box's /Q: 0, 1 and 2
FLAG_HIDDEN = 2
FLAG_PRINT = 4
FLAG_LOCKED = 128


def pdf_date(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.strftime("D:%Y%m%d%H%M%SZ")


# Reading ------------------------------------------------------------------


def _color(value) -> Color | None:
    if value is None:
        return None
    components = [float(v) for v in value]
    if len(components) == 1:
        return components[0], components[0], components[0]
    if len(components) == 3:
        return components[0], components[1], components[2]
    if len(components) == 4:  # CMYK, naive conversion
        c, m, y, k = components
        return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    return None


def _text(value) -> str:
    return str(value) if value is not None else ""


def _border_width(obj, default: float = 1.0) -> float:
    style = obj.get("/BS")
    if style is not None and "/W" in style:
        return float(style.W)
    border = obj.get("/Border")
    if border is not None and len(border) >= 3:
        return float(border[2])
    return default


def _dash(obj) -> str:
    """The border's dash style, one of DASHES: from /BS /S /D and its /D, or a fourth /Border element.

    The pattern itself is not kept: a dot comes first in a dotted pattern, four
    or more entries make dashes and dots, anything else is dashed.
    """
    style = obj.get("/BS")
    pattern = None
    if isinstance(style, Dictionary) and style.get("/S") == Name.D:
        pattern = style.get("/D", Array([3]))
    else:
        border = obj.get("/Border")
        if isinstance(border, Array) and len(border) >= 4 and isinstance(border[3], Array):
            pattern = border[3]
    try:
        values = [float(v) for v in pattern] if isinstance(pattern, Array) else []
    except (TypeError, ValueError):
        values = []
    if not values or not any(values):
        return "solid"
    if abs(values[0]) <= 0.15:  # no length, or DOT
        return "dotted"
    return "dashdot" if len(values) >= 4 else "dashed"


def _endings(obj) -> tuple[str, str]:
    """The /LE line endings (start, end), as names in ENDINGS; "None" for unknown ones."""
    value = obj.get("/LE")
    names = [value] if isinstance(value, Name) else list(value) if isinstance(value, Array) else []
    ends = [str(n)[1:] if str(n)[1:] in ENDINGS else "None" for n in names] + ["None", "None"]
    return ends[0], ends[1]


def _has_border(obj) -> bool:
    """False when a shape draws no border: zero width, an empty /C, or a fill with no /C at all."""
    color = obj.get("/C")
    if isinstance(color, Array) and len(color) == 0:
        return False
    if color is None and "/IC" in obj:
        return False
    return _border_width(obj) > 0


def _border_extras(obj, color: Color | None, fallback: Color) -> dict:
    """color, width and border for a shape or polygon, the colour and width kept even with no border."""
    border = _has_border(obj)
    width = _border_width(obj)
    if not border:
        width = _number(obj.get("/ZenithBorderWidth"), width if width > 0 else 2.0)
        color = _color(obj.get("/ZenithBorderColor")) or color
    return dict(border=border, width=width, color=color or fallback)


def _turns(vector) -> int:
    """Quarter turns clockwise on screen from "to the right" to `vector` (y grows downwards)."""
    dx, dy = vector
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 2
    return 1 if dy > 0 else 3


def _pdf_box(obj) -> tuple[float, float, float, float]:
    left, bottom, right, top = (float(v) for v in obj.Rect)
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def _inner_box(obj) -> tuple[float, float, float, float]:
    """Rect minus /RD, the margins between the annotation's Rect and what it draws."""
    left, bottom, right, top = _pdf_box(obj)
    if "/RD" in obj:
        dl, db, dr, dt = (float(v) for v in obj.RD)
        left, bottom, right, top = left + dl, bottom + db, right - dr, top - dt
    return left, bottom, right, top


def _points(values, geometry: PageGeometry) -> tuple:
    values = [float(v) for v in values]
    return tuple(geometry.to_display(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2))


def _flat(points, geometry: PageGeometry) -> Array:
    return _array([c for p in points for c in geometry.to_pdf(*p)])


def _measure_scale(obj) -> tuple[float, str] | None:
    """Units per point and their name, from a /Measure dictionary's distance format."""
    measure = obj.get("/Measure")
    formats = measure.get("/X") if isinstance(measure, Dictionary) else None
    if isinstance(formats, Array) and len(formats) and isinstance(formats[0], Dictionary):
        first = formats[0]
        return float(first.get("/C", 1.0)), str(first.get("/U", "pt"))
    return None


def read_annotation(obj, page: int, geometry: PageGeometry, pages: dict | None = None) -> Annotation | None:
    """The model value for an annotation dictionary, or None if Zenith PDF leaves it alone.

    `pages` maps page objects (objgen) to indices, for links that go to a page.
    """
    subtype = str(obj.get("/Subtype", ""))
    if subtype not in SUPPORTED or int(obj.get("/F", 0)) & FLAG_HIDDEN:
        return None
    common = dict(
        page=page,
        opacity=float(obj.get("/CA", 1.0)),
        contents=_text(obj.get("/Contents")),
        author=_text(obj.get("/T")),
        subject=_text(obj.get("/Subj")),
        locked=bool(int(obj.get("/F", 0)) & FLAG_LOCKED),
        group=_text(obj.get("/ZenithGroup")),
        id=_text(obj.get("/NM")) or new_id(),
    )
    color = _color(obj.get("/C"))
    highlight = bool(obj.get("/ZenithHighlighter", False))

    if subtype in MARKUPS:
        values = [float(v) for v in obj.QuadPoints]
        quads, turns = [], []
        for i in range(0, len(values) - 7, 8):
            corners = [geometry.to_display(values[j], values[j + 1]) for j in range(i, i + 8, 2)]
            quads.append(points_bounds(corners))
            # The corners go along the text first (top-left, top-right, ...): that gives its direction.
            turns.append(_turns((corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])))
        if not quads:
            return None
        return TextMarkup(style=subtype[1:], quads=tuple(quads), rotation=max(set(turns), key=turns.count),
                          color=color or (1.0, 0.85, 0.0), **common)

    if subtype in ("/Square", "/Circle"):
        effect = obj.get("/BE")
        return Shape(
            shape=subtype[1:],
            rect=geometry.rect_to_display(_inner_box(obj)),
            fill=_color(obj.get("/IC")),
            cloudy=subtype == "/Square" and isinstance(effect, Dictionary) and effect.get("/S") == Name.C,
            dash=_dash(obj),
            highlight=highlight,
            pattern=_pattern(obj),
            **_border_extras(obj, color, (0.0, 0.0, 0.0)),
            **common,
        )

    mode = DIMENSIONS.get(str(obj.get("/IT", "")))
    if mode is not None and subtype in ("/Line", "/PolyLine", "/Polygon"):
        points = _points(obj.L, geometry) if subtype == "/Line" else _points(obj.get("/Vertices", []), geometry)
        scale = _measure_scale(obj)
        extra = dict(scale=scale[0], unit=scale[1]) if scale else {}
        if len(points) < 2:
            return None
        if (mode == "angle" and len(points) < 3) or (mode == "volume" and len(points) < 3):
            return None
        measured = Measurement(points=points, mode=mode, width=_border_width(obj, 1.5),
                               color=color or (0.1, 0.45, 0.95), depth=_number(obj.get("/ZenithDepth"), 0.0),
                               **extra, **common)
        if measured.contents == measured.text():  # the label we wrote, not a comment of the user's
            measured = replace(measured, contents="")
        return measured

    if subtype in ("/PolyLine", "/Polygon"):
        if "/ZenithTapeLine" in obj:
            start, end = _points(obj.ZenithTapeLine, geometry)[:2]
            return Tape(start=start, end=end, width=float(obj.get("/ZenithTapeWidth", 18.0)),
                        revealed=bool(obj.get("/ZenithRevealed", False)), color=_color(obj.get("/IC")) or (0.98, 0.78, 0.36),
                        **common)
        points = _points(obj.get("/Vertices", []), geometry)
        if len(points) < 2:
            return None
        closed = subtype == "/Polygon"
        head_start, head_end = ("None", "None") if closed else _endings(obj)
        style = _border_extras(obj, color, (0.0, 0.0, 0.0)) if closed else dict(
            width=_border_width(obj), color=color or (0.0, 0.0, 0.0))
        return Poly(points=points, closed=closed, fill=_color(obj.get("/IC")) if closed else None, dash=_dash(obj),
                    highlight=highlight, head_start=head_start, head_end=head_end, pattern=_pattern(obj), **style,
                    **common)

    if subtype == "/Link":
        return _read_link(obj, geometry, common, color, pages or {})

    if subtype == "/FileAttachment":
        spec = obj.get("/FS")
        if not isinstance(spec, Dictionary):
            return None
        name, data = _embedded_file(spec)
        if data is None:
            return None
        x, y, _, _ = geometry.rect_to_display(_pdf_box(obj))
        audio = bool(obj.get("/ZenithAudio", False)) or name.lower().endswith(AUDIO_SUFFIXES)
        return Attachment(position=(x, y), file_name=name, data=data, audio=audio,
                          color=color or (0.96, 0.45, 0.33), **common)

    if subtype == "/Line":
        x1, y1, x2, y2 = (float(v) for v in obj.L)
        head_start, head_end = _endings(obj)
        return Line(
            start=geometry.to_display(x1, y1),
            end=geometry.to_display(x2, y2),
            width=_border_width(obj),
            head_start=head_start,
            head_end=head_end,
            dash=_dash(obj),
            highlight=highlight,
            color=color or (0.0, 0.0, 0.0),
            **common,
        )

    if subtype == "/Ink":
        strokes, given = [], obj.get("/ZenithWidths")
        given = list(given) if isinstance(given, Array) else []
        widths = []
        for index, path in enumerate(obj.InkList):
            values = [float(v) for v in path]
            stroke = tuple(geometry.to_display(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2))
            if stroke:
                strokes.append(stroke)
                ws = given[index] if index < len(given) and isinstance(given[index], Array) else ()
                widths.append(tuple(float(w) for w in ws))
        if not strokes:
            return None
        # Widths count only if every stroke has one per point; else the stroke is as wide throughout.
        if not all(len(ws) == len(s) for ws, s in zip(widths, strokes)):
            widths = []
        return Ink(strokes=tuple(strokes), width=_border_width(obj), color=color or (0.0, 0.0, 0.0),
                   highlighter=highlight, widths=tuple(widths), **common)

    if subtype == "/FreeText":
        appearance_string = _text(obj.get("/DA"))
        size = re.search(r"([\d.]+)\s+Tf", appearance_string)
        font, bold, italic, underline = _read_face(appearance_string, _text(obj.get("/DS")))
        quadding = obj.get("/Q", 0)
        align = ALIGNS[int(quadding)] if isinstance(quadding, int) and 0 <= int(quadding) < 3 else "left"
        rgb = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", appearance_string)
        gray = re.search(r"([\d.]+)\s+g\b", appearance_string)
        text_color = (
            tuple(float(v) for v in rgb.groups()) if rgb else (float(gray.group(1)),) * 3 if gray else (0.0, 0.0, 0.0)
        )
        width = _border_width(obj, default=0.0)
        callout = ()
        if str(obj.get("/IT", "")) == "/FreeTextCallout" and "/CL" in obj:
            line = _points(obj.CL, geometry)
            callout = tuple(line[:-1]) if len(line) >= 2 else ()
        return FreeText(
            rect=geometry.rect_to_display(_inner_box(obj)),
            font_size=float(size.group(1)) if size and float(size.group(1)) > 0 else 12.0,
            fill=color,
            border_width=width,
            # Files from before the colour was kept: a callout's border took its text colour.
            border_color=_color(obj.get("/ZenithBorderColor")) or (text_color if callout else (0.0, 0.0, 0.0)),
            color=text_color,
            callout=callout,
            rotation=_text_turns(obj, geometry),
            font=font,
            bold=bold,
            italic=italic,
            underline=underline,
            align=align,
            **common,
        )

    if subtype == "/Caret":
        return Caret(rect=geometry.rect_to_display(_inner_box(obj)), color=color or CARET_BLUE, **common)

    if subtype == "/Text":
        x, y, _, _ = geometry.rect_to_display(_pdf_box(obj))
        icon = str(obj.get("/Name", "/Comment"))[1:]
        return Note(position=(x, y), color=color or (1.0, 0.85, 0.2), icon=icon if icon in NOTE_ICONS else "Comment",
                    **common)

    if subtype == "/Stamp" and "/ZenithStampText" in obj:
        name = _text(obj.get("/ZenithStampName"))
        return TextStamp(rect=geometry.rect_to_display(_inner_box(obj)), text=_text(obj.ZenithStampText),
                         detail=_text(obj.get("/ZenithStampDetail")), name=name if name in STAMPS else "",
                         rotation=_text_turns(obj, geometry), color=color or (0.85, 0.15, 0.15), **common)
    if subtype == "/Stamp":
        return _read_picture(obj, geometry, dict(common, color=color or (0.0, 0.0, 0.0)))

    if subtype == "/Redact":
        values = [float(v) for v in obj.get("/QuadPoints", [])]
        rects = []
        for i in range(0, len(values) - 7, 8):
            corners = [geometry.to_display(values[j], values[j + 1]) for j in range(i, i + 8, 2)]
            rects.append(points_bounds(corners))
        if not rects:
            rects = [geometry.rect_to_display(_pdf_box(obj))]
        return Redaction(rects=tuple(rects), fill=_color(obj.get("/IC")) or (0.0, 0.0, 0.0),
                         color=color or (0.86, 0.12, 0.12), **common)
    return None


DISPLAY_TURNS = {(1, 0, 0, -1): 0, (0, 1, 1, 0): 1, (-1, 0, 0, 1): 2, (0, -1, -1, 0): 3}  # display_matrix, by /Rotate


def _pattern(obj) -> str:
    name = str(obj.get("/ZenithPattern", "/solid"))[1:]
    return name if name in PATTERNS else "solid"


def _text_turns(obj, geometry: PageGeometry) -> int:
    """FreeText.rotation for a text box: how its text is turned on screen.

    /ZenithRotation holds the text's quarter turns on the page as stored, before
    /Rotate, which turning the page leaves alone. Boxes written before it existed
    had their text upright on screen; the first `cm` of their appearance (the
    display matrix) tells how the page was turned then. Anything else is upright.
    """
    page_turns = geometry.rotation // 90
    if "/ZenithRotation" in obj:
        return (int(_number(obj.get("/ZenithRotation"), 0)) + page_turns) % 4
    written = _drawn_turns(obj)
    return 0 if written is None else (page_turns - written) % 4


def _style_string(a: FreeText) -> str:
    """A text box's /DS: its font as the CSS a viewer that redraws it reads."""
    family = {"Helvetica": "Helvetica,sans-serif", "Times": "Times,serif", "Courier": "Courier,monospace"}
    style = [f"font: {family.get(a.font, family['Helvetica'])} {appearance.num(a.font_size)}pt",
             f"text-align:{a.align}", "color:#" + "".join(f"{round(c * 255):02X}" for c in a.color)]
    if a.bold:
        style.append("font-weight:bold")
    if a.italic:
        style.append("font-style:italic")
    if a.underline:
        style.append("text-decoration:underline")
    return "; ".join(style)


def _family(text: str) -> str | None:
    """The built-in font closest to a font's name or CSS family, if the text names one we know."""
    lower = text.lower()
    if "times" in lower or "georgia" in lower or re.search(r"(?<!sans-)serif", lower):
        return "Times"
    if "cour" in lower or "mono" in lower or "consol" in lower:
        return "Courier"
    if "helv" in lower or "arial" in lower or "sans" in lower:
        return "Helvetica"
    return None


def _read_face(appearance_string: str, style: str) -> tuple[str, bool, bool, bool]:
    """(font, bold, italic, underline) of a text box, from the font its /DA names and its /DS style string,
    whichever app wrote them: our resource names, or a font's own name such as /Times-Bold or /Arial,Italic.
    A family the style string names wins, as apps that redraw a box read that one."""
    named = re.search(r"/([^\s/]+)\s+[\d.]+\s+Tf", appearance_string)
    name = named.group(1) if named else ""
    ours = {resource: face for face, (resource, _) in appearance.STANDARD_FONTS.items()}
    if name in ours:
        font, bold, italic = ours[name]
    else:
        font = _family(name) or "Helvetica"
        bold = "bold" in name.lower()
        italic = "italic" in name.lower() or "oblique" in name.lower()
    family = re.search(r"font(?:-family)?\s*:\s*([^;]+)", style, re.IGNORECASE)
    font = (_family(family.group(1)) if family else None) or font
    css = style.lower().replace(" ", "")
    bold = bold or "font-weight:bold" in css or re.search(r"font-weight:[6-9]00", css) is not None
    italic = italic or "font-style:italic" in css or "font-style:oblique" in css
    return font, bold, italic, "text-decoration:underline" in css or "text-decoration:word" in css


def _read_link(obj, geometry: PageGeometry, common: dict, color, pages: dict) -> Link | None:
    """A link to a web address or to a page here; any other kind is left to PDFium."""
    rect = geometry.rect_to_display(_pdf_box(obj))
    border = 0.0
    if "/BS" in obj or "/Border" in obj:
        border = _border_width(obj, 0.0)
    extra = dict(rect=rect, border=border, color=color or (0.1, 0.45, 0.95), **common)
    action = obj.get("/A")
    if isinstance(action, Dictionary):
        if action.get("/S") == Name.URI and "/URI" in action:
            return Link(uri=str(action.URI), **extra)
        if action.get("/S") != Name.GoTo:
            return None
        target, point = _destination(action.get("/D"), pages)
    else:
        target, point = _destination(obj.get("/Dest"), pages)
    return Link(target=target, target_point=point, **extra) if target is not None else None


def _destination(destination, pages: dict) -> tuple[int | None, Point | None]:
    """The page index of an explicit destination ([page /XYZ x y zoom]) and the spot it shows, in that
    page's PDF space: (x, y) for /XYZ (a null x is 0), (0, y) for /FitH and /FitBH, else None.
    Named destinations are left alone: (None, None)."""
    if not (isinstance(destination, Array) and len(destination) and isinstance(destination[0], Dictionary)):
        return None, None
    index = pages.get(destination[0].objgen)
    mode = destination[1] if len(destination) > 1 else None
    values = list(destination)[2:]

    def number(position: int) -> float | None:  # None for null, missing or not a number
        value = values[position] if position < len(values) else None
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    point = None
    if mode == Name.XYZ and number(1) is not None:
        point = (number(0) or 0.0, number(1))
    elif mode in (Name.FitH, Name.FitBH) and number(0) is not None:
        point = (0.0, number(0))
    return index, point


def _view_point(destination) -> Point | None:
    """The spot an explicit destination shows, as _destination reads it."""
    return _destination(destination, {})[1]


def _embedded_file(spec) -> tuple[str, bytes | None]:
    name = str(spec.get("/UF", spec.get("/F", "attachment")))
    files = spec.get("/EF")
    stream = files.get("/F") if isinstance(files, Dictionary) else None
    if not isinstance(stream, pikepdf.Stream):
        return name, None
    try:
        return name, stream.read_bytes()
    except Exception:
        return name, None


def picture_parts(obj) -> tuple[pikepdf.Stream, tuple[float, ...]] | None:
    """For a stamp whose appearance only shows one picture: (the image, image space to page space).

    Anything else in the appearance (text, vector drawing) means None: the stamp
    is left to PDFium, so nothing it draws is lost.
    """
    from .pages import form_placement

    look = obj.get("/AP")
    form = look.get("/N") if isinstance(look, Dictionary) else None
    if not isinstance(form, pikepdf.Stream) or "/BBox" not in form:
        return None
    resources = form.get("/Resources")
    xobjects = resources.get("/XObject") if isinstance(resources, Dictionary) else None
    if not isinstance(xobjects, Dictionary) or len(xobjects.keys()) != 1:
        return None
    name = next(iter(xobjects.keys()))
    image = xobjects[name]
    if not isinstance(image, pikepdf.Stream) or image.get("/Subtype") != Name.Image:
        return None
    ctm, stack, drawn = IDENTITY, [], None
    for operands, operator in pikepdf.parse_content_stream(form):
        op = str(operator)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else IDENTITY
        elif op == "cm":
            ctm = multiply(tuple(float(v) for v in operands), ctm)
        elif op == "Do" and drawn is None and str(operands[0]) == name:
            drawn = ctm
        elif op != "gs":
            return None
    if drawn is None:
        return None
    form_matrix = tuple(float(v) for v in form.get("/Matrix", IDENTITY))
    placement = tuple(form_placement(form, [float(v) for v in obj.Rect]))
    return image, multiply(multiply(drawn, form_matrix), placement)


def _read_picture(obj, geometry: PageGeometry, common: dict) -> ImageStamp | None:
    parts = picture_parts(obj)
    if parts is None:
        return None
    image, (a, b, c, d, e, f) = parts
    corner = lambda u, v: geometry.to_display(a * u + c * v + e, b * u + d * v + f)
    # The picture's top-left, top-right and bottom-left corners on screen (its first row is at v = 1).
    top_left, top_right, bottom_left = corner(0, 1), corner(1, 1), corner(0, 0)
    across = (top_right[0] - top_left[0], top_right[1] - top_left[1])
    down = (bottom_left[0] - top_left[0], bottom_left[1] - top_left[1])
    turns = _turns(across)
    # Turned by quarter turns and not mirrored: "down" is "across" turned clockwise, both on the axes.
    upright = (abs(across[0]) < 0.5 or abs(across[1]) < 0.5) and _turns(down) == (turns + 1) % 4 and (
        abs(down[0]) < 0.5 or abs(down[1]) < 0.5)
    width, height = abs(across[0]) + abs(across[1]), abs(down[0]) + abs(down[1])
    if not upright or width < 0.5 or height < 0.5:
        return None  # turned at an angle, mirrored or degenerate: PDFium draws it as it is
    data = images.from_xobject(image)
    if data is None:
        return None
    corners = (top_left, top_right, bottom_left, corner(1, 0))
    return ImageStamp(rect=points_bounds(corners), data=data, equation=str(obj.get("/ZenithEquation", "")),
                      equation_size=max(_number(obj.get("/ZenithEquationSize"), 0.0), 0.0),
                      equation_color=_color(obj.get("/ZenithEquationColor")), rotation=turns, **common)


def _number(value, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# Writing ------------------------------------------------------------------


def _array(values) -> Array:
    return Array([float(v) for v in values])


def _set_border(obj, width: float, dash: str = "solid", border: bool = True) -> None:
    """/BS: the border's width and style. With no border, W is 0 and the width is kept aside."""
    if dash in DASHES and dash != "solid":
        obj.BS = Dictionary(Type=Name.Border, W=float(width), S=Name.D,
                            D=Array([round(v, 3) for v in drawn_dashes(dash, width)]))
    else:
        obj.BS = Dictionary(Type=Name.Border, W=float(width), S=Name.S)
    if not border:
        obj.BS.W = 0
        obj.ZenithBorderWidth = float(width)
    if "/Border" in obj:
        del obj["/Border"]


def _ending_names(start: str, end: str) -> Array:
    return Array([Name("/" + (start if start in ENDINGS else "None")), Name("/" + (end if end in ENDINGS else "None"))])


def _explicit_destination(obj) -> Array | None:
    """A copy of the link's explicit destination ([page /XYZ ...], from /Dest or a GoTo action), if it has one."""
    action = obj.get("/A")
    destination = action.get("/D") if isinstance(action, Dictionary) and action.get("/S") == Name.GoTo else obj.get("/Dest")
    if isinstance(destination, Array) and len(destination) >= 2 and isinstance(destination[0], Dictionary):
        return Array(list(destination))
    return None


def _link_destination(a: Link, page, written: Array | None) -> Array:
    """Where a link to `page` goes, given the destination it had (`written`).

    One that still shows the same spot is kept as it was, zoom included. The spot is written as
    [page /XYZ x y null] when it belongs to this page: a link read with a spot on another page,
    then pointed at this one, shows the whole page, as that page's view isn't known.
    """
    same_page = written is not None and written[0].objgen == page.objgen
    if same_page and _view_point(written) == a.target_point:
        return written
    if a.target_point is not None and (written is None or same_page):
        x, y = a.target_point
        return Array([page, Name.XYZ, float(x), float(y), None])
    return Array([page, Name.Fit])


def _quad_points(rects, geometry: PageGeometry, turns: int = 0) -> Array:
    """/QuadPoints: four corners per rect, in the order of the text in it (top-left, top-right,
    bottom-left, bottom-right), so other viewers know which way it runs."""
    values = []
    for rect in rects:
        a, b, c, d, e, f = quarter_turn(rect, turns)
        w, h = (rect[3], rect[2]) if turns % 2 else (rect[2], rect[3])
        for u, v in ((0, 0), (w, 0), (0, h), (w, h)):
            values.extend(geometry.to_pdf(a * u + c * v + e, b * u + d * v + f))
    return _array(values)


def write_annotation(pdf: pikepdf.Pdf, obj: Dictionary, annotation: Annotation, geometry: PageGeometry,
                     pictures: dict | None = None) -> None:
    """Write `annotation` into the dictionary `obj`, creating its appearance stream.

    `pictures` maps image digests to image objects already in `pdf`, so a picture
    used many times is stored once; new ones are added to it.
    """
    a = annotation
    look = appearance.build(a, geometry)
    obj.Type = Name.Annot
    obj.Subtype = Name("/" + a.kind)
    obj.Rect = _array(look.bbox)
    obj.NM = String(a.id)
    obj.M = String(pdf_date())
    obj.F = (int(obj.get("/F", 0)) & ~FLAG_HIDDEN & ~FLAG_LOCKED) | FLAG_PRINT | (FLAG_LOCKED if a.locked else 0)
    if "/RC" in obj and _text(obj.get("/Contents")) != a.contents:
        del obj["/RC"]  # another app's rich text, which viewers show instead: stale once the text changes
    obj.Contents = String(a.contents)
    if a.author:
        obj.T = String(a.author)
    elif "/T" in obj:
        del obj["/T"]
    if "/CreationDate" not in obj:
        obj.CreationDate = String(pdf_date())
    if a.opacity < 1:
        obj.CA = float(a.opacity)
    elif "/CA" in obj:
        del obj["/CA"]
    for key in MANAGED_KEYS:
        if key in obj:
            del obj[key]
    if obj.get("/RT") == Name.Group:  # its group's link, written again by write_page if it is still in one
        for key in ("/IRT", "/RT"):
            del obj[key]
    if a.group:
        obj.ZenithGroup = String(a.group)
    if a.subject:
        obj.Subj = String(a.subject)
    if getattr(a, "highlight", False) or getattr(a, "highlighter", False):
        obj.ZenithHighlighter = True

    if isinstance(a, TextMarkup):
        obj.C = _array(a.color)
        obj.QuadPoints = _quad_points(a.quads, geometry, a.rotation)
    elif isinstance(a, Shape):
        obj.C = _array(a.color)
        _set_border(obj, a.width, a.dash, a.border)
        if a.fill is not None:
            obj.IC = _array(a.fill)
            if a.pattern != "solid":
                obj.ZenithPattern = Name("/" + a.pattern)
        if a.cloudy:
            obj.BE = Dictionary(S=Name.C, I=1)
        # Rect carries padding for antialiasing; RD says where the shape itself is.
        left, bottom, right, top = look.bbox
        sl, sb, sr, st = appearance.pdf_rect(geometry, a.rect)
        obj.RD = _array((sl - left, sb - bottom, right - sr, top - st))
    elif isinstance(a, Line):
        obj.C = _array(a.color)
        obj.IC = _array(a.color)  # fills closed endings
        _set_border(obj, a.width, a.dash)
        obj.L = _array((*geometry.to_pdf(*a.start), *geometry.to_pdf(*a.end)))
        obj.LE = _ending_names(a.head_start, a.head_end)
    elif isinstance(a, Ink):
        obj.C = _array(a.color)
        _set_border(obj, a.width)
        obj.InkList = Array([_array([c for p in stroke for c in geometry.to_pdf(*p)]) for stroke in a.strokes])
        if a.widths:
            obj.ZenithWidths = Array([Array([round(w, 2) for w in ws]) for ws in a.stroke_widths()])
    elif isinstance(a, FreeText):
        r, g, b = a.color
        name, _ = appearance.standard_font(a.face())
        obj.DA = String(f"/{name} {appearance.num(a.font_size)} Tf {appearance.num(r)} {appearance.num(g)} {appearance.num(b)} rg")
        # How a viewer that redraws the box sets its text: alignment, and the font as a style string.
        obj.Q = ALIGNS.index(a.align) if a.align in ALIGNS else 0
        obj.DS = String(_style_string(a))
        _set_border(obj, a.border_width)
        obj.ZenithBorderColor = _array(a.border_color)
        # The text's turns on the page as stored, which survive the page being turned.
        obj.ZenithRotation = (a.rotation - geometry.rotation // 90) % 4
        if a.fill is not None:
            obj.C = _array(a.fill)
        elif "/C" in obj:
            del obj["/C"]
        left, bottom, right, top = look.bbox
        sl, sb, sr, st = appearance.pdf_rect(geometry, a.rect)
        obj.RD = _array((sl - left, sb - bottom, right - sr, top - st))
        if a.callout:
            obj.IT = Name.FreeTextCallout
            obj.CL = _flat(a.callout_line(), geometry)
            obj.LE = Name.OpenArrow
    elif isinstance(a, Measurement):
        obj.C = _array(a.color)
        _set_border(obj, a.width)
        if a.mode == "distance":
            obj.L = _array((*geometry.to_pdf(*a.points[0]), *geometry.to_pdf(*a.points[-1])))
        else:
            obj.Vertices = _flat(a.points, geometry)
        obj.IT = Name({"distance": "/LineDimension", "perimeter": "/PolyLineDimension", "angle": "/ZenithAngle",
                       "volume": "/ZenithVolume"}.get(a.mode, "/PolygonDimension"))
        if a.mode == "volume":
            obj.ZenithDepth = float(a.depth)
        unit = pikepdf.String(a.unit)
        number = lambda factor, label: Dictionary(Type=Name.NumberFormat, U=label, C=float(factor), D=100)
        obj.Measure = Dictionary(
            Type=Name.Measure, Subtype=Name.RL, R=pikepdf.String(f"1 pt = {appearance.num(a.scale)} {a.unit}"),
            X=Array([number(a.scale, unit)]), D=Array([number(1, unit)]),
            A=Array([number(a.scale, pikepdf.String(a.unit + "²"))]),
        )
        obj.Contents = String(a.contents or a.text())
    elif isinstance(a, Poly):
        obj.C = _array(a.color)
        _set_border(obj, a.width, a.dash, a.border or not a.closed)
        obj.Vertices = _flat(a.points, geometry)
        if a.closed and a.fill is not None:
            obj.IC = _array(a.fill)
            if a.pattern != "solid":
                obj.ZenithPattern = Name("/" + a.pattern)
        elif not a.closed:
            obj.LE = _ending_names(a.head_start, a.head_end)
            obj.IC = _array(a.color)  # fills closed endings
    elif isinstance(a, Tape):
        r, g, b = a.color
        obj.C = _array((r * 0.8, g * 0.8, b * 0.8))
        obj.IC = _array(a.color)
        _set_border(obj, 0.6)
        obj.Vertices = _flat(a.outline(), geometry)
        obj.ZenithTapeLine = _flat((a.start, a.end), geometry)
        obj.ZenithTapeWidth = float(a.width)
        obj.ZenithRevealed = bool(a.revealed)
    elif isinstance(a, Link):
        obj.C = _array(a.color)
        obj.Border = Array([0, 0, float(a.border)])
        if "/BS" in obj:
            del obj["/BS"]
        obj.H = Name.I
        written = _explicit_destination(obj)  # read before the stale keys go
        for stale in ("/A", "/Dest"):
            if stale in obj:
                del obj[stale]
        if a.uri:
            obj.A = Dictionary(S=Name.URI, URI=String(a.uri))
        elif 0 <= a.target < len(pdf.pages):
            obj.Dest = _link_destination(a, pdf.pages[a.target].obj, written)
    elif isinstance(a, Attachment):
        obj.C = _array(a.color)
        obj.Name = Name.Paperclip
        obj.ZenithAudio = bool(a.audio)
        if str(obj.get("/ZenithDigest", "")) != a.digest or "/FS" not in obj:
            obj.FS = pikepdf.AttachedFileSpec(pdf, a.data, filename=a.file_name).obj
            obj.ZenithDigest = String(a.digest)
    elif isinstance(a, Note):
        obj.C = _array(a.color)
        obj.Name = Name("/" + (a.icon if a.icon in NOTE_ICONS else "Comment"))
        obj.Open = False
    elif isinstance(a, Caret):
        obj.C = _array(a.color)
        obj.Sy = Name("/None")
        left, bottom, right, top = look.bbox
        sl, sb, sr, st = appearance.pdf_rect(geometry, a.rect)
        obj.RD = _array((sl - left, sb - bottom, right - sr, top - st))
    elif isinstance(a, Redaction):
        obj.C = _array(a.color)
        obj.IC = _array(a.fill)
        obj.QuadPoints = _quad_points(a.rects, geometry)
    elif isinstance(a, TextStamp):
        obj.C = _array(a.color)
        # The PDF's own name for a ready stamp, so other apps list it as that; words of one's own get ours.
        obj.Name = Name("/" + STAMP_NAMES.get(a.name, "ZenithStamp"))
        obj.ZenithStampText = String(a.text)
        if a.detail:
            obj.ZenithStampDetail = String(a.detail)
        if a.name:
            obj.ZenithStampName = String(a.name)
        obj.ZenithRotation = (a.rotation - geometry.rotation // 90) % 4
        left, bottom, right, top = look.bbox
        sl, sb, sr, st = appearance.pdf_rect(geometry, a.rect)
        obj.RD = _array((sl - left, sb - bottom, right - sr, top - st))
    elif isinstance(a, ImageStamp):
        obj.C = _array(a.color)  # not drawn, but kept, so the annotation reads back as it was
        if "/ZenithEquation" in obj:
            del obj["/ZenithEquation"]
        if a.equation:
            obj.ZenithEquation = String(a.equation)  # the source, to edit the equation again
            if a.equation_size > 0:  # and how it was typeset, so editing keeps its look
                obj.ZenithEquationSize = float(a.equation_size)
            if a.equation_color is not None:
                obj.ZenithEquationColor = _array(a.equation_color)

    resources = Dictionary()
    states = Dictionary()
    if look.blend is not None:
        states.GS0 = Dictionary(Type=Name.ExtGState, CA=float(look.opacity), ca=float(look.opacity),
                                BM=Name("/" + look.blend))
    if look.soft_fill:
        states.GS1 = Dictionary(Type=Name.ExtGState, ca=0.16)
    if len(states.keys()):
        resources.ExtGState = states
    if look.uses_font:
        name, base = appearance.standard_font(look.face)
        resources.Font = Dictionary({"/" + name: Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                                            BaseFont=Name("/" + base), Encoding=Name.WinAnsiEncoding)})
    if look.uses_image:
        pictures = {} if pictures is None else pictures
        picture = pictures.get(a.digest)
        if picture is None:
            picture = pdf.make_indirect(images.xobject(pdf, a.data))
            pictures[a.digest] = picture
        resources.XObject = Dictionary(Im0=picture)
    stream = pikepdf.Stream(pdf, look.content)
    stream.Type = Name.XObject
    stream.Subtype = Name.Form
    stream.BBox = _array(look.bbox)
    stream.Resources = resources
    obj.AP = Dictionary(N=stream)


def key(obj) -> tuple:
    """Identity of a PDF object that survives saving: its object and generation numbers."""
    return obj.objgen if obj.is_indirect else ("direct", id(obj))


def write_page(
    pdf: pikepdf.Pdf,
    page_index: int,
    geometry: PageGeometry,
    annotations: list[Annotation],
    sources: dict[str, Dictionary],
    owned,
    unchanged: set[str] = frozenset(),
    pictures: dict | None = None,
) -> list[str]:
    """Bring one page's /Annots in line with `annotations`.

    `sources` maps annotation ids to their dictionaries and gains entries for new
    annotations. `owned` holds the ids currently in this page's /Annots that
    Zenith PDF manages. Annotations in `unchanged` keep their dictionary as is, so
    their modification date stays put.

    /Annots order is drawing order: annotations Zenith PDF leaves alone (links,
    form fields) keep theirs, then the managed ones follow in the order given.
    Returns the managed ids in that order.
    """
    page = pdf.pages[page_index].obj
    # Nulls and references to missing objects (pikepdf gives None for both) are dropped.
    old = [obj for obj in page.get("/Annots", Array()) if isinstance(obj, Dictionary)]
    current = {a.id for a in annotations}
    owned_keys = {key(sources[i]): i for i in owned if i in sources}
    deleted = {k for k, i in owned_keys.items() if i not in current}

    result = []
    for obj in old:
        if key(obj) in owned_keys:
            continue  # managed: re-added below in order, unless deleted
        parent = obj.get("/Parent")
        if parent is not None and str(obj.get("/Subtype", "")) == "/Popup" and key(parent) in deleted:
            continue  # the popup of a deleted annotation
        result.append(obj)

    for annotation in annotations:
        obj = sources.get(annotation.id)
        if obj is None:
            obj = pdf.make_indirect(Dictionary())
            sources[annotation.id] = obj
        if annotation.id not in unchanged or key(obj) not in owned_keys:
            write_annotation(pdf, obj, annotation, geometry, pictures)
            obj.P = page
        result.append(obj)
    _link_groups(annotations, sources)

    if result:
        page.Annots = Array(result)
    elif "/Annots" in page:
        del page["/Annots"]
    return [a.id for a in annotations]


def _link_groups(annotations: list[Annotation], sources: dict[str, Dictionary]) -> None:
    """Each group's other members point at its first one (/IRT, /RT /Group), as other PDF apps keep a group;
    /ZenithGroup holds it for Zenith PDF."""
    heads: dict[str, Dictionary] = {}
    for annotation in annotations:
        if not annotation.group:
            continue
        obj = sources[annotation.id]
        head = heads.setdefault(annotation.group, obj)
        if key(head) != key(obj):
            obj.IRT = head
            obj.RT = Name.Group
        elif obj.get("/RT") == Name.Group:  # a new head: its link to a removed one would keep that in the file
            for k in ("/IRT", "/RT"):
                if k in obj:
                    del obj[k]


def groups_of(objects: list[tuple[Dictionary, Annotation]]) -> dict[str, str]:
    """Group names for annotations another app grouped (/RT /Group pointing at the group's first one), by id:
    the id of that first one."""
    ids = {key(obj): annotation.id for obj, annotation in objects}
    found = {}
    for obj, annotation in objects:
        target = obj.get("/IRT")
        if obj.get("/RT") == Name.Group and isinstance(target, Dictionary) and key(target) in ids:
            head = ids[key(target)]
            found[annotation.id] = found.setdefault(head, head)
    return found


def _inherited(page: Dictionary, key: str):
    """A page attribute, from the page or the nearest /Parent that has it."""
    node, seen = page, 0
    while isinstance(node, Dictionary) and seen < 64:
        if key in node:
            return node[key]
        node, seen = node.get("/Parent"), seen + 1
    return None


def page_geometry(page: Dictionary) -> PageGeometry:
    """The geometry of a page, read from its dictionary: what Document.geometry gets from PDFium."""
    def box(key: str, fallback):
        value = _inherited(page, key)
        if not isinstance(value, Array) or len(value) != 4:
            return fallback
        left, bottom, right, top = (float(v) for v in value)
        return min(left, right), min(bottom, top), max(left, right), max(bottom, top)

    media = box("/MediaBox", (0.0, 0.0, 612.0, 792.0))
    crop = box("/CropBox", media)
    turn = round(_number(_inherited(page, "/Rotate"), 0) / 90) * 90 % 360
    return PageGeometry(visible_box(media, crop), turn)


def _drawn_turns(obj) -> int | None:
    """The page turns an appearance Zenith PDF wrote was drawn for (its first cm, the display matrix);
    None for an appearance from another program."""
    look = obj.get("/AP")
    form = look.get("/N") if isinstance(look, Dictionary) else None
    if not isinstance(form, pikepdf.Stream) or "/Matrix" in form:
        return None
    try:
        found = re.match(r"q\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+cm\b", form.read_bytes()[:160].decode("latin-1"))
        values = [float(v) for v in found.groups()] if found else []
    except Exception:
        return None
    if len(values) != 4 or any(abs(v - round(v)) > 1e-3 for v in values):
        return None
    return DISPLAY_TURNS.get(tuple(round(v) for v in values))


def redraw_turned(pdf: pikepdf.Pdf, index: int) -> int:
    """Redraw Zenith PDF's annotations on page `index` after the page was turned (its /Rotate changed).

    Their appearances were drawn for the page as it was, so other viewers would turn
    a note's icon, an audio note's speaker and a measurement's label with the page,
    while the canvas keeps them upright. Each is read in the page's new geometry and
    written again. Pictures keep their own turn, and annotations from other programs
    are left alone. Returns how many were redrawn.
    """
    page = pdf.pages[index].obj
    geometry = page_geometry(page)
    turns = geometry.rotation // 90
    count = 0
    for obj in list(page.get("/Annots", Array())):
        if not isinstance(obj, Dictionary) or str(obj.get("/Subtype", "")) in ("/Stamp", "/Link", "/Popup"):
            continue
        drawn = _drawn_turns(obj)
        if drawn is None or drawn == turns:
            continue
        annotation = read_annotation(obj, index, geometry)
        if annotation is not None:
            write_annotation(pdf, obj, annotation, geometry)
            count += 1
    return count
