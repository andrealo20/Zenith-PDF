"""Line styles, endings, highlight mode, variable-width ink, note icons, turned boxes and text in any script:
saved with the PDF, read back, and drawn alike by the canvas and by other viewers (PDFium)."""

import dataclasses
import math
import shutil
import time

import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image, ImageChops
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from zenith_pdf.core import images, inking
from zenith_pdf.core.annotations import (
    DASHES,
    ENDINGS,
    NOTE_ICONS,
    Annotation,
    Attachment,
    FreeText,
    ImageStamp,
    Ink,
    Line,
    Measurement,
    Note,
    Poly,
    Shape,
    TextMarkup,
    merge_line_rects,
    quarter_turn,
)
from zenith_pdf.core.builder import Entry, Source, build
from zenith_pdf.core.document import Document
from zenith_pdf.core.pdf_annots import page_geometry, redraw_turned
from zenith_pdf.ui.annotation_items import paint_annotation

pytestmark = pytest.mark.usefixtures("qt_app")

GREEK = "Δt = 5 µs → ≤ 3 Ω, Привет, 中文"


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


@pytest.fixture
def blank_pdf(tmp_path):
    """One empty 300 x 240 pt page, nothing on it to get in the way of the pixels."""
    path = tmp_path / "blank.pdf"
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 240).close()
    pdf.save(str(path))
    pdf.close()
    return path


def _picture(size=(80, 40)) -> bytes:
    """Green, with a blue right quarter: which way it is turned shows."""
    image = QImage(*size, QImage.Format.Format_RGB32)
    image.fill(QColor(20, 160, 60))
    for x in range(size[0] * 3 // 4, size[0]):
        for y in range(size[1]):
            image.setPixelColor(x, y, QColor(30, 60, 220))
    return images.encode(image)


def _same(loaded: Annotation, original: Annotation) -> None:
    """Every field equal, numbers to a hundredth of a point."""
    assert type(loaded) is type(original)
    for field in dataclasses.fields(original):
        if field.name in ("data", "digest"):
            continue
        got, want = getattr(loaded, field.name), getattr(original, field.name)
        flat = lambda v: [float(x) for x in _flatten(v)]
        if isinstance(want, (float, tuple)) and want and all(isinstance(x, (int, float)) for x in _flatten(want)):
            assert flat(got) == pytest.approx(flat(want), abs=0.011), (original.label, field.name)
        else:
            assert got == want, (original.label, field.name)


def _flatten(value):
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


def _every_new_field(page: int) -> list[Annotation]:
    """One of every kind, set away from its defaults, with a subject each."""
    points = [(60 + i * 4.0, 300 + 20 * math.sin(i / 4)) for i in range(30)]
    widths = inking.pro_widths(points, [i * 0.012 for i in range(30)], 4.0)
    made = [
        Shape(page=page, rect=(40, 40, 90, 50), dash="dashed", border=False, fill=(0.2, 0.4, 1.0), highlight=True,
              color=(0.9, 0.1, 0.1), width=3.5),
        Shape(page=page, shape="Circle", rect=(150, 40, 60, 60), dash="dotted", width=2),
        Shape(page=page, rect=(230, 40, 60, 40), cloudy=True, dash="dashdot", highlight=True),
        Poly(page=page, points=((40, 120), (90, 140), (70, 180)), closed=True, border=False, fill=(1, 1, 0),
             dash="dashed"),
        Poly(page=page, points=((120, 120), (170, 150), (200, 120), (200, 120)), head_start="Slash",
             head_end="RClosedArrow", dash="dashdot", highlight=True, width=1.5),
        Ink(page=page, strokes=(tuple(points), ((60, 360), (90, 380))), width=max(widths),
            widths=(tuple(widths), (1.0, 2.5))),
        FreeText(page=page, rect=(220, 120, 40, 150), contents=GREEK, rotation=1, border_width=1.5,
                 border_color=(0.9, 0.1, 0.1), color=(0.1, 0.1, 0.8), font_size=10),
        FreeText(page=page, rect=(40, 420, 160, 40), contents="Callout", callout=((30, 400),), border_width=1,
                 border_color=(0.1, 0.6, 0.2), color=(0.1, 0.6, 0.2), fill=(1, 1, 1)),
        ImageStamp(page=page, rect=(250, 300, 40, 80), data=_picture(), equation="x^2", equation_size=18.0,
                   equation_color=(0.8, 0.1, 0.1), rotation=3),
        TextMarkup(page=page, style="Underline", quads=((300, 120, 12, 60),), rotation=1, color=(0, 0, 1)),
        TextMarkup(page=page, style="StrikeOut", quads=((330, 120, 12, 60),), rotation=3, color=(0, 0, 1)),
    ]
    made += [Note(page=page, position=(40 + 26 * i, 220), icon=icon) for i, icon in enumerate(NOTE_ICONS)]
    for i, (start, end) in enumerate(zip(ENDINGS, ENDINGS[3:] + ENDINGS[:3])):
        made.append(Line(page=page, start=(40, 480 + 12 * i), end=(200, 480 + 12 * i + 5), head_start=start,
                         head_end=end, dash=DASHES[i % 4], highlight=i % 3 == 0, width=1 + i % 3))
        made.append(Poly(page=page, points=((220, 480 + 12 * i), (260, 478 + 12 * i), (300, 485 + 12 * i)),
                         head_start=end, head_end=start))
    return [dataclasses.replace(a, subject=f"Subject {i} of {a.label}") for i, a in enumerate(made)]


def test_new_fields_survive_saving_on_every_rotation(pdf_copy):
    document = Document(pdf_copy)
    made = [a for page in range(4) for a in _every_new_field(page)]
    document.save(made)
    document.close()

    reopened = Document(pdf_copy)
    loaded = {a.id: a for a in reopened.annotations}
    assert set(loaded) == {a.id for a in made}
    for original in made:
        _same(loaded[original.id], original)
    reopened.close()


def test_pictures_read_back_as_the_same_bytes(pdf_copy):
    """Saving and reopening gives the very picture back, so nothing counts as changed afterwards."""
    from zenith_pdf.core import mathtext

    formula = images.encode(mathtext.render(r"\frac{a}{b}", 16, (0.8, 0.1, 0.1)))  # see-through background
    made = [ImageStamp(page=page, rect=(100, 100, 80, 40), data=_picture(), color=(0.2, 0.5, 0.8)) for page in range(4)]
    made.append(ImageStamp(page=0, rect=(200, 100, 60, 40), data=formula, equation=r"\frac{a}{b}"))
    _save(pdf_copy, made)
    reopened = Document(pdf_copy)
    loaded = {a.id: a for a in reopened.annotations}
    reopened.close()
    for original in made:
        assert loaded[original.id].data == original.data and loaded[original.id] == original


def test_the_pdf_keys_other_viewers_read(pdf_copy):
    dashed = Shape(page=0, rect=(40, 40, 90, 50), dash="dashed", width=2, subject="Important")
    arrow = Line(page=0, start=(10, 300), end=(200, 300), head_start="Diamond", head_end="ROpenArrow")
    open_poly = Poly(page=0, points=((10, 400), (80, 420), (90, 480)), head_end="Circle")
    note = Note(page=0, position=(300, 300), icon="Key")
    ink = Ink(page=0, strokes=(((10, 10), (20, 20), (30, 10)),), width=3, widths=((1.0, 3.0, 2.004),))
    no_border = Shape(page=0, rect=(300, 40, 50, 50), border=False, fill=(0, 0, 1), width=4)
    dotted = Line(page=0, start=(10, 500), end=(200, 500), width=2, dash="dotted")
    document = Document(pdf_copy)
    document.save([dashed, arrow, open_poly, note, ink, no_border, dotted])
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        by_id = {str(a.NM): a for a in pdf.pages[0].obj.Annots}
        square = by_id[dashed.id]
        assert square.BS.S == pikepdf.Name.D and [float(v) for v in square.BS.D] == [6, 4]
        assert [float(v) for v in by_id[dotted.id].BS.D] == pytest.approx([0.1, 3.9])  # a dot is drawn 0.1 long
        assert str(square.Subj) == "Important" and "/Subj" not in by_id[arrow.id]
        assert [str(n) for n in by_id[arrow.id].LE] == ["/Diamond", "/ROpenArrow"]
        assert [str(n) for n in by_id[open_poly.id].LE] == ["/None", "/Circle"]
        assert by_id[note.id].Name == pikepdf.Name.Key
        assert [[float(w) for w in ws] for ws in by_id[ink.id].ZenithWidths] == [[1.0, 3.0, 2.0]]
        assert float(by_id[no_border.id].BS.W) == 0  # other programs draw no border


def test_an_imported_line_keeps_its_endings_through_an_edit(pdf_copy):
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:
        line = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Line, Rect=[50, 50, 250, 250], L=[60, 60, 240, 240],
            LE=pikepdf.Array([pikepdf.Name.Circle, pikepdf.Name.Square]), C=[0, 0, 1],
            BS=pikepdf.Dictionary(W=2, S=pikepdf.Name.D, D=pikepdf.Array([0, 4])), Subj=pikepdf.String("Imported")))
        pdf.pages[0].obj.Annots = pikepdf.Array([line])
        pdf.save(pdf_copy)

    document = Document(pdf_copy)
    [imported] = document.annotations
    assert (imported.head_start, imported.head_end, imported.dash, imported.subject) == (
        "Circle", "Square", "dotted", "Imported")
    document.save([imported.translated(10, 0)])
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        [saved] = pdf.pages[0].obj.Annots
        assert [str(n) for n in saved.LE] == ["/Circle", "/Square"]


# What other viewers draw ------------------------------------------------------------------


def _pdfium_image(path, page: int, scale: float, annotations: bool = True) -> Image.Image:
    """The page as PDFium (any other viewer) draws it; without `annotations`, what the canvas paints them over."""
    pdf = pdfium.PdfDocument(str(path))
    image = pdf[page].render(scale=scale, draw_annots=annotations).to_pil().convert("RGB")
    pdf.close()
    return image


def _painted_image(annotations, size, scale: float, background: Image.Image | None = None) -> Image.Image:
    """The annotations as the canvas paints them, over `background` (else white)."""
    image = QImage(round(size[0] * scale), round(size[1] * scale), QImage.Format.Format_RGB888)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    if background is not None:
        painter.drawImage(0, 0, QImage(background.tobytes(), background.width, background.height,
                                       background.width * 3, QImage.Format.Format_RGB888))
    painter.scale(scale, scale)
    for annotation in annotations:
        paint_annotation(painter, annotation)
    painter.end()
    return Image.frombytes("RGB", (image.width(), image.height()), bytes(image.constBits()), "raw", "RGB",
                           image.bytesPerLine())


def _mask(image: Image.Image, red=(0, 255), green=(0, 255), blue=(0, 255), light=(0, 255)) -> Image.Image:
    """255 where every channel, and the lightness (max + min) / 2, lies in its range; else 0."""
    inside = lambda band, low, high: band.point(lambda v: 255 if low <= v <= high else 0)
    r, g, b = image.split()
    lightness = ImageChops.add(ImageChops.lighter(ImageChops.lighter(r, g), b),
                               ImageChops.darker(ImageChops.darker(r, g), b), scale=2.0)
    mask = inside(lightness, *light)
    for band, (low, high) in ((r, red), (g, green), (b, blue)):
        if (low, high) != (0, 255):
            mask = ImageChops.multiply(mask, inside(band, low, high))
    return mask


def _inked(image: Image.Image) -> Image.Image:
    return _mask(image, light=(0, 169))


def _count(mask: Image.Image) -> int:
    return mask.histogram()[255]


def _iou(a: Image.Image, b: Image.Image) -> float:
    return _count(ImageChops.multiply(a, b)) / max(_count(ImageChops.lighter(a, b)), 1)


def _save(path, annotations) -> None:
    document = Document(path)
    document.save(annotations)
    document.close()


def test_a_dotted_line_shows_its_dots(blank_pdf):
    line = Line(page=0, start=(20, 120), end=(280, 120), width=3, dash="dotted")
    _save(blank_pdf, [line])
    image = _pdfium_image(blank_pdf, 0, 2)
    row = [sum(image.getpixel((x, 240))) / 3 for x in range(600)]
    dots = sum(1 for x in range(1, 600) if row[x] < 128 <= row[x - 1])
    assert dots >= 35  # 260 pt at one dot every 6 pt: about 43


def test_highlight_mode_keeps_the_text_under_it_black(pdf_copy):
    document = Document(pdf_copy)
    first = document.char_at(0, (80, 95))
    rects, _ = document.text_span(0, first, first + 5)
    x, y, w, h = rects[0]
    marker = Shape(page=0, rect=(x - 4, y - 4, w + 8, h + 8), fill=(1.0, 0.9, 0.0), border=False, highlight=True)
    document.save([marker])
    document.close()
    image = _pdfium_image(pdf_copy, 0, 2)
    words = image.crop((int(2 * x), int(2 * y), int(2 * (x + w)), int(2 * (y + h)))).convert("L")
    assert words.getextrema()[0] < 60  # the glyphs are still black under the yellow
    red, green, blue = image.getpixel((int(2 * x - 4), int(2 * (y + h / 2))))
    assert red > 240 and 215 < green < 240 and blue < 30  # and the yellow is there


def test_a_shape_without_border_shows_no_outline(blank_pdf):
    shape = Shape(page=0, rect=(50, 50, 120, 80), fill=(0.0, 0.0, 1.0), border=False, color=(1, 0, 0), width=8)
    _save(blank_pdf, [shape])
    image = _pdfium_image(blank_pdf, 0, 1)
    assert _count(_mask(image, red=(151, 255), blue=(0, 99))) == 0
    assert image.getpixel((52, 52)) == (0, 0, 255)  # filled right to the edge


PARITY = [
    Shape(page=0, rect=(40, 40, 200, 140), dash="dashed", width=4, color=(0.8, 0.1, 0.1)),
    Line(page=0, start=(40, 200), end=(260, 60), width=3, head_end="ClosedArrow", head_start="Diamond"),
    Line(page=0, start=(30, 120), end=(260, 140), width=2.5, head_start="Circle", head_end="Square", dash="dashdot"),
    Poly(page=0, points=((30, 30), (150, 200), (270, 40)), width=3, dash="dotted", head_end="OpenArrow"),
    Shape(page=0, rect=(60, 50, 180, 120), cloudy=True, width=2.5, fill=(0.7, 0.85, 1.0), color=(0.1, 0.2, 0.7)),
]


@pytest.mark.parametrize("annotation", PARITY + ["pro ink"], ids=["dashed rect", "arrow and diamond", "circle, square",
                                                                   "dotted polyline", "cloud", "pro ink"])
def test_the_canvas_draws_what_other_viewers_draw(blank_pdf, annotation):
    if annotation == "pro ink":
        points = [(30 + i * 2.4, 120 + 70 * math.sin(i / 14)) for i in range(100)]
        widths = inking.pro_widths(points, [i * 0.004 * (1 + (i // 30) % 2) for i in range(100)], 9.0)
        keep = inking.simplify_indices(points, 0.25)
        annotation = Ink(page=0, strokes=(tuple(points[i] for i in keep),), width=max(widths),
                         widths=(tuple(widths[i] for i in keep),))
    _save(blank_pdf, [annotation])
    theirs = _inked(_pdfium_image(blank_pdf, 0, 3))
    ours = _inked(_painted_image([annotation], (300, 240), 3))
    assert _iou(theirs, ours) > 0.9


def test_turned_pages_draw_the_same_too(pdf_copy):
    """The page turned 90 degrees: a dashed shape and an arrow, on the canvas and in PDFium."""
    made = [dataclasses.replace(a, page=1, id=f"p{i}") for i, a in enumerate(PARITY[:2])]
    _save(pdf_copy, made)
    document = Document(pdf_copy)
    size = document.page_size(1)
    document.close()
    theirs = _pdfium_image(pdf_copy, 1, 2)
    ours = _painted_image(made, size, 2, background=_pdfium_image(pdf_copy, 1, 2, annotations=False))
    assert _iou(_inked(theirs), _inked(ours)) > 0.9


# Text in any script ----------------------------------------------------------------------


def test_text_outside_windows_1252_shows_in_other_viewers(blank_pdf):
    box = FreeText(page=0, rect=(10, 40, 280, 80), contents=GREEK + "\nПривет мир", font_size=16, color=(0, 0, 0))
    _save(blank_pdf, [box])
    with pikepdf.open(blank_pdf) as pdf:
        stream = pdf.pages[0].obj.Annots[0].AP.N.read_bytes()
    assert b"?" not in stream and b"\\077" not in stream  # nothing written as a question mark
    theirs = _inked(_pdfium_image(blank_pdf, 0, 3))
    ours = _inked(_painted_image([box], (300, 240), 3))
    assert _count(theirs) > 1500 and _iou(theirs, ours) > 0.6  # the same letters in the same places
    # Each word lands where the canvas puts it: compare the ink in thin columns across the box.
    columns = lambda mask: {x for x in range(0, 900, 24) if mask.crop((x, 0, x + 24, 720)).getbbox()}
    assert columns(theirs) == columns(ours)


# Turned pages ----------------------------------------------------------------------------


def _red(image: Image.Image) -> tuple | None:
    """Where the red text is: (left, top, right, bottom) or None."""
    return _mask(image, red=(151, 255), green=(0, 109), blue=(0, 109)).getbbox()


def test_pictures_and_text_boxes_stay_editable_when_the_page_turns(pdf_copy):
    box = FreeText(page=0, rect=(100, 300, 260, 26), contents="Hello world from Zenith PDF", font_size=14,
                   color=(0.9, 0.0, 0.0), border_width=1, border_color=(0.1, 0.1, 0.9))
    stamp = ImageStamp(page=0, rect=(100, 400, 80, 40), data=_picture(), equation="a+b", equation_size=14,
                       equation_color=(0, 0, 0))
    document = Document(pdf_copy)
    for turn in (90, 90, 180, 270):
        document.rotate(document.annotations or [box, stamp], [0], turn)
        after = {a.id: a for a in document.annotations}
        assert set(after) == {box.id, stamp.id}  # both still annotations Zenith PDF edits
        turns = document.geometry(0).rotation // 90
        assert after[box.id].rotation == turns and after[stamp.id].rotation == turns
        assert after[box.id].upright_size() == pytest.approx((260, 26), abs=0.01)
        assert after[box.id].border_color == pytest.approx((0.1, 0.1, 0.9))
        assert after[stamp.id].equation_size == 14 and after[stamp.id].upright_size() == pytest.approx((80, 40))
    turned = document.annotations
    document.save(turned)
    size = document.page_size(0)
    document.close()

    # The canvas shows the text box and the picture where PDFium does.
    theirs = _pdfium_image(pdf_copy, 0, 1)
    ours = _painted_image(turned, size, 1, background=_pdfium_image(pdf_copy, 0, 1, annotations=False))
    assert _red(theirs) is not None
    assert all(abs(p - q) <= 2 for p, q in zip(_red(theirs), _red(ours))), (_red(theirs), _red(ours))
    green = lambda image: _mask(image, red=(0, 79), green=(121, 255))
    assert _iou(green(theirs), green(ours)) > 0.9


def test_icons_and_labels_stay_upright_when_the_page_turns(blank_pdf):
    """Turning a page redraws notes, audio notes and measurements, as the canvas shows them upright."""
    made = [Note(page=0, position=(40, 40), icon="Help", color=(1, 0.85, 0.2)),
            Attachment(page=0, position=(100, 40), file_name="a.wav", data=b"RIFF", audio=True),
            Measurement(page=0, points=((40, 150), (200, 150)), mode="distance"),
            ImageStamp(page=0, rect=(150, 60, 80, 40), data=_picture())]
    _save(blank_pdf, made)
    for turn in (90, 180, 270):
        with pikepdf.open(blank_pdf, allow_overwriting_input=True) as pdf:
            pdf.pages[0].obj.Rotate = turn
            assert redraw_turned(pdf, 0) == 3  # the picture keeps its own turn
            assert redraw_turned(pdf, 0) == 0  # drawn for this turn now
            pdf.save(blank_pdf)
        document = Document(blank_pdf)
        turned, size = document.annotations, document.page_size(0)
        document.close()
        assert {a.id for a in turned} == {a.id for a in made}
        theirs = _inked(_pdfium_image(blank_pdf, 0, 2))
        ours = _inked(_painted_image(turned, size, 2))
        assert _iou(theirs, ours) > 0.9, turn


def test_page_geometry_from_the_page_dictionary(pdf_copy):
    document = Document(pdf_copy)
    expected = [document.geometry(i) for i in range(4)]
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        assert [page_geometry(page.obj) for page in pdf.pages] == expected


def test_text_boxes_saved_before_rotation_was_kept_turn_with_the_page(pdf_copy):
    box = FreeText(page=1, rect=(100, 300, 260, 26), contents="Old box", color=(0.9, 0, 0))
    _save(pdf_copy, [box])
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:  # as an older version wrote it
        for annot in pdf.pages[1].obj.Annots:
            del annot["/ZenithRotation"]
        pdf.save(pdf_copy)
    document = Document(pdf_copy)
    [read] = document.annotations
    assert read.rotation == 0 and read.rect == pytest.approx(box.rect, abs=0.01)
    document.rotate(document.annotations, [1], 90)
    [turned] = document.annotations
    assert turned.rotation == 1 and turned.upright_size() == pytest.approx((260, 26), abs=0.01)
    document.close()


def test_a_turned_text_box_grows_away_from_its_first_line():
    for turns in range(4):
        box = FreeText(page=0, rect=(100, 100, 40, 160) if turns % 2 else (100, 100, 160, 40), rotation=turns)
        grown = box.with_height(70)
        assert grown.upright_size() == pytest.approx((160, 70))
        assert quarter_turn(grown.rect, turns)[4:] == pytest.approx(quarter_turn(box.rect, turns)[4:]), turns


def test_doc_builder_turns_keep_pictures_and_text_boxes(pdf_copy, tmp_path):
    box = FreeText(page=0, rect=(100, 300, 200, 30), contents="Turned in the builder", color=(0.9, 0, 0))
    stamp = ImageStamp(page=0, rect=(100, 400, 80, 40), data=_picture())
    _save(pdf_copy, [box, stamp])
    result = tmp_path / "built.pdf"
    other = tmp_path / "other.pdf"
    shutil.copy(pdf_copy, other)
    sources = {"a": Source("a", pdf_copy), "b": Source("b", other)}
    result.write_bytes(build(sources, [Entry("a", 0, rotation=90), Entry("b", 0, rotation=270)]))
    combined = Document(result)
    kinds = sorted((a.page, a.label, a.rotation) for a in combined.annotations)
    assert kinds == [(0, "Image", 1), (0, "Text box", 1), (1, "Image", 3), (1, "Text box", 3)]
    combined.close()


# Markup along vertical text --------------------------------------------------------------


def test_underline_runs_along_vertical_text(pdf_copy):
    document = Document(pdf_copy)
    hit = next(h for h in document.iter_search("needle", False, False) if h.page == 1)
    x, y, w, h = hit.rects[0]
    assert h > 3 * w  # the page is turned: the word runs down the screen
    turns = document.geometry(1).rotation // 90
    marks = [TextMarkup(page=1, style=style, quads=(hit.rects[0],), rotation=turns, color=(0, 0, 1))
             for style in ("Underline", "StrikeOut", "Squiggly")]
    for mark in marks:
        document.save([mark])
        reopened = Document(pdf_copy)
        assert [a.rotation for a in reopened.annotations] == [turns]
        reopened.close()
        theirs = _pdfium_image(pdf_copy, 1, 2)
        ours = _painted_image([mark], document.page_size(1), 2, background=_pdfium_image(pdf_copy, 1, 2, False))
        blue = lambda image: _mask(image, red=(0, 119), green=(0, 119), blue=(151, 255))
        tb, ob = blue(theirs), blue(ours)
        (tl, tt, tr, tbottom), (ol, ot, oright, obottom) = tb.getbbox(), ob.getbbox()
        assert tbottom - tt > 1.6 * h and obottom - ot > 1.6 * h, mark.style  # along the whole word (2 px a point)
        assert oright - ol < w and tr - tl < w, mark.style  # and not across it
        assert _iou(tb, ob) > 0.6, mark.style
    document.close()


def test_foreign_underlines_on_turned_pages_follow_their_text(pdf_copy):
    pdf = pdfium.PdfDocument(str(pdf_copy))
    for index in range(4):
        page = pdf[index]
        left, bottom, _, _ = page.get_mediabox()
        annot = raw.FPDFPage_CreateAnnot(page.raw, raw.FPDF_ANNOT_UNDERLINE)
        x0, y0 = left + 80, bottom + 80
        raw.FPDFAnnot_SetRect(annot, raw.FS_RECTF(x0, y0 + 30, x0 + 60, y0))
        raw.FPDFAnnot_AppendAttachmentPoints(annot, raw.FS_QUADPOINTSF(x0, y0 + 20, x0 + 60, y0 + 20, x0, y0,
                                                                      x0 + 60, y0))
        raw.FPDFPage_CloseAnnot(annot)
        page.close()
    target = pdf_copy.with_name("foreign.pdf")
    pdf.save(str(target))
    pdf.close()
    document = Document(target)
    turns = {a.page: a.rotation for a in document.annotations}
    assert turns == {i: document.geometry(i).rotation // 90 for i in range(4)}
    document.close()


def test_merging_word_boxes_follows_the_text():
    down = ((100, 10, 12, 40), (100, 55, 12, 30), (118, 10, 12, 50))  # two words of one line, then the next line
    assert merge_line_rects(down, 1) == ((100, 10, 12, 75), (118, 10, 12, 50))
    assert len(merge_line_rects(down)) == 3  # read as horizontal text, nothing joins


# Notes, painting, speed ------------------------------------------------------------------


def test_every_note_icon_looks_different(blank_pdf):
    notes = [Note(page=0, position=(20 + 40 * i, 100), icon=icon, color=(1, 0.85, 0.2)) for i, icon in enumerate(NOTE_ICONS)]
    _save(blank_pdf, notes)
    symbols = []
    for image in (_pdfium_image(blank_pdf, 0, 3), _painted_image(notes, (300, 240), 3)):
        dark = _mask(image, light=(0, 109))
        found = [dark.crop((round(3 * x) + 6, round(3 * y) + 6, round(3 * x) + 54, round(3 * y) + 54))
                 for x, y in (note.position for note in notes)]
        assert len({symbol.tobytes() for symbol in found}) == len(NOTE_ICONS)
        symbols.append(found)
    for theirs, ours in zip(*symbols):
        assert _iou(theirs, ours) > 0.5


def test_new_fields_paint_without_errors(capfd):
    image = QImage(400, 700, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    for annotation in _every_new_field(0):
        paint_annotation(painter, annotation)
    painter.end()
    assert "Traceback" not in capfd.readouterr().err


def test_variable_width_ink_paints_fast():
    points = [(10 + i * 2.0, 100 + 40 * math.sin(i / 9)) for i in range(400)]
    widths = inking.pro_widths(points, [i * 0.006 for i in range(400)], 6.0)
    keep = inking.simplify_indices(points, 0.25)
    ink = Ink(page=0, strokes=(tuple(points[i] for i in keep),), width=6, widths=(tuple(widths[i] for i in keep),))
    image = QImage(1000, 500, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.scale(1.2, 1.2)
    paint_annotation(painter, ink)  # builds the outline once
    started = time.perf_counter()
    for _ in range(20):
        paint_annotation(painter, ink.translated(0.5, 0))  # moved: the outline is reused
    painter.end()
    assert (time.perf_counter() - started) / 20 < 0.008


def test_a_cloud_is_framed_by_its_bumps_and_resized_by_them():
    from PySide6.QtCore import QPointF

    from zenith_pdf.ui.annotation_items import resized, selection_rect

    cloud = Shape(page=0, rect=(100, 100, 150, 90), cloudy=True, width=2)
    x, y, w, h = cloud.bounds()
    assert x < 100 - 6 and w > 150 + 12  # the bumps and the border reach out of the rect
    assert selection_rect(cloud) == cloud.bounds()
    assert resized(cloud, "br", QPointF(x + w, y + h), False).rect == pytest.approx(cloud.rect)
    assert resized(cloud, "r", QPointF(x + w + 30, y + h / 2), False).rect == pytest.approx((100, 100, 180, 90))
    flat = Shape(page=0, rect=(100, 100, 150, 90), width=2)
    assert flat.bounds() == flat.rect
