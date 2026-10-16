import shutil
from dataclasses import replace

import pikepdf
import pypdfium2 as pdfium
import pytest

from zenith_pdf.core.annotations import FreeText, Ink, Line, Link, Note, Shape, TextMarkup, merge_line_rects
from zenith_pdf.core.document import Document

# Text boxes measure their lines with Qt fonts, which needs an application object.
pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def _sample_annotations(document):
    first = document.char_at(0, (80, 95))
    assert first >= 0
    rects, text = document.text_span(0, first, first + len("Page 1 needle") - 1)
    assert text == "Page 1 needle"
    return [
        TextMarkup(page=0, style="Highlight", quads=rects, color=(1.0, 0.85, 0.0)),
        FreeText(page=0, rect=(100, 200, 220, 40), contents="Ciao, perché (prova)", font_size=14),
        Shape(page=1, rect=(50, 60, 120, 80), width=3, fill=(0.0, 0.0, 1.0), color=(1.0, 0.0, 0.0)),
        Note(page=1, position=(300, 300), contents="una nota"),
        Line(page=2, start=(30, 40), end=(200, 150), width=2, head_end="ClosedArrow", opacity=0.5),
        Ink(page=3, strokes=(((10, 10), (50, 40), (90, 20)),), width=1.5),
    ]


def _assert_same(loaded, original):
    assert type(loaded) is type(original)
    assert loaded.page == original.page
    assert loaded.contents == original.contents
    assert loaded.opacity == pytest.approx(original.opacity, abs=0.01)
    for name in ("rect", "start", "end", "position"):
        if hasattr(original, name):
            assert getattr(loaded, name) == pytest.approx(getattr(original, name), abs=0.01), name
    if isinstance(original, TextMarkup):
        for a, b in zip(loaded.quads, original.quads, strict=True):
            assert a == pytest.approx(b, abs=0.01)
    if isinstance(original, Ink):
        assert [p for s in loaded.strokes for c in s for p in c] == pytest.approx(
            [p for s in original.strokes for c in s for p in c], abs=0.01
        )


def test_round_trip_on_rotated_and_cropped_pages(pdf_copy):
    document = Document(pdf_copy)
    assert document.annotations == []
    made = _sample_annotations(document)
    assert "needle" in document.text_in(0, made[0].quads)
    document.save(made)
    document.close()

    reopened = Document(pdf_copy)
    loaded = {a.id: a for a in reopened.annotations}
    assert set(loaded) == {a.id for a in made}
    for original in made:
        _assert_same(loaded[original.id], original)
    reopened.close()


def test_other_viewers_see_annotations_in_place(pdf_copy):
    document = Document(pdf_copy)
    shape = _sample_annotations(document)[2]  # filled blue rectangle on the page rotated 90 degrees
    document.save([shape])
    document.close()

    pdf = pdfium.PdfDocument(str(pdf_copy))
    image = pdf[1].render(scale=1, draw_annots=True).to_pil().convert("RGB")
    x, y, w, h = shape.rect
    assert image.getpixel((int(x + w / 2), int(y + h / 2))) == (0, 0, 255)
    pdf.close()


def test_deleting_keeps_other_annotations_untouched(pdf_copy):
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:
        link = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 20, 20]))
        pdf.pages[1].obj.Annots = pikepdf.Array([link])
        pdf.save(pdf_copy)

    document = Document(pdf_copy)
    made = _sample_annotations(document)
    document.save(made)
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        stamps = {str(a.NM): str(a.M) for a in pdf.pages[1].obj.Annots if "/NM" in a}

    document = Document(pdf_copy)
    note = next(a for a in document.annotations if isinstance(a, Note))
    document.save([a for a in document.annotations if a is not note])
    document.close()

    with pikepdf.open(pdf_copy) as pdf:
        annots = pdf.pages[1].obj.Annots
        subtypes = sorted(str(a.Subtype) for a in annots)
        assert subtypes == ["/Link", "/Square"]
        square = next(a for a in annots if a.Subtype == "/Square")
        assert str(square.M) == stamps[str(square.NM)]  # unchanged, so not rewritten


def test_merge_line_rects_joins_the_words_of_a_line():
    words = ((10, 100, 30, 12), (44, 100.5, 60, 11.5), (108, 100, 40, 12))
    next_line = ((10, 116, 50, 12),)
    merged = merge_line_rects(words + next_line)
    assert len(merged) == 2
    assert merged[0] == pytest.approx((10, 100, 138, 12), abs=0.6)
    assert merged[1] == pytest.approx(next_line[0])


def test_merge_line_rects_keeps_columns_apart():
    left, right = (50, 100, 200, 12), (320, 100, 200, 12)  # two columns sharing a baseline
    assert len(merge_line_rects((left, right))) == 2


def test_encryption_survives_saving(pdf_copy):
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:
        pdf.save(pdf_copy, encryption=pikepdf.Encryption(owner="owner", user="secret"))

    document = Document(pdf_copy, password="secret")
    document.save(_sample_annotations(document)[:1])
    document.close()
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(pdf_copy)
    with pikepdf.open(pdf_copy, password="secret") as pdf:
        assert len(pdf.pages[0].obj.Annots) == 1


def test_a_moved_link_keeps_the_spot_it_goes_to(pdf_copy):
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:
        target = pdf.pages[2].obj
        link = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[60, 60, 160, 80],
            Dest=pikepdf.Array([target, pikepdf.Name.XYZ, 0, 693, 0])))
        pdf.pages[0].obj.Annots = pikepdf.Array([link])
        pdf.save(pdf_copy)

    document = Document(pdf_copy)
    [read] = document.annotations
    assert isinstance(read, Link) and read.target == 2
    document.save([read.translated(1, 0)])
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        [saved] = pdf.pages[0].obj.Annots
        assert saved.Dest[0].objgen == pdf.pages[2].obj.objgen
        assert [str(v) for v in list(saved.Dest)[1:]] == ["/XYZ", "0", "693", "0"]  # not turned into /Fit

    document = Document(pdf_copy)
    [read] = document.annotations
    document.save([replace(read, target=3)])  # a new page: its view isn't known
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        [saved] = pdf.pages[0].obj.Annots
        assert saved.Dest[0].objgen == pdf.pages[3].obj.objgen and saved.Dest[1] == pikepdf.Name.Fit
