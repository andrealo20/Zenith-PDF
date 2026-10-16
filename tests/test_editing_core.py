"""Lock, drawing order, flattening, new documents and the Doc Builder, below the UI."""

import shutil

import pikepdf
import pypdfium2 as pdfium
import pytest

from zenith_pdf.core import pages
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.builder import Entry, Source, build
from zenith_pdf.core.document import Document

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def test_lock_survives_saving(pdf_copy):
    document = Document(pdf_copy)
    document.save([Shape(page=0, rect=(10, 10, 50, 50), locked=True)])
    document.close()
    reopened = Document(pdf_copy)
    assert [a.locked for a in reopened.annotations] == [True]
    reopened.close()


def test_drawing_order_is_saved(pdf_copy):
    shapes = [Shape(page=0, rect=(10 * i, 10, 50, 50)) for i in range(3)]
    document = Document(pdf_copy)
    document.save(shapes)
    document.save([shapes[1], shapes[2], shapes[0]])  # first one brought to the front
    document.close()
    reopened = Document(pdf_copy)
    assert [a.id for a in reopened.annotations] == [shapes[1].id, shapes[2].id, shapes[0].id]
    reopened.close()


def test_flatten_burns_annotations_into_the_page(pdf_copy):
    document = Document(pdf_copy)
    blue = Shape(page=1, rect=(50, 60, 120, 80), fill=(0.0, 0.0, 1.0))
    kept = Shape(page=1, rect=(300, 300, 40, 40))
    document.flatten([blue, kept], {blue.id})
    assert [a.id for a in document.annotations] == [kept.id]
    document.save(document.annotations)
    document.close()

    pdf = pdfium.PdfDocument(str(pdf_copy))
    image = pdf[1].render(scale=1, draw_annots=False).to_pil().convert("RGB")  # content only
    x, y, w, h = blue.rect
    assert image.getpixel((int(x + w / 2), int(y + h / 2))) == (0, 0, 255)
    pdf.close()


def test_new_document_saves_where_asked(tmp_path):
    target = tmp_path / "notes.pdf"
    document = Document(target, data=pages.new_document((595, 842), "lined", 3))
    assert document.unsaved and document.page_count == 3 and not target.exists()
    document.save([Shape(page=2, rect=(10, 10, 30, 30))])
    assert target.exists() and not document.unsaved
    document.close()


def test_builder_combines_rotates_and_unshares_pages(pdf_copy, tmp_path):
    document = Document(pdf_copy)
    document.save([Shape(page=0, rect=(10, 10, 30, 30))])
    document.close()
    other = tmp_path / "other.pdf"
    shutil.copy(pdf_copy, other)

    sources = {"a": Source("a", pdf_copy), "b": Source("b", other)}
    entries = [Entry("b", 3), Entry("a", 0), Entry(None, size=(300, 400)), Entry("a", 0, rotation=90)]
    result = tmp_path / "combined.pdf"
    result.write_bytes(build(sources, entries))

    combined = Document(result)
    assert combined.page_count == 4
    assert [" ".join(combined.text_span(i, 0, 12)[1].split()) for i in (0, 1)] == ["Page 4 needle", "Page 1 needle"]
    assert combined.page_size(2) == pytest.approx((300, 400))
    assert combined.geometry(3).rotation == 90
    assert sorted(a.page for a in combined.annotations) == [1, 3]
    assert len({a.id for a in combined.annotations}) == 2  # two annotations, not one shared
    combined.close()
    with pikepdf.open(result) as pdf:
        first, second = pdf.pages[1].obj.Annots[0], pdf.pages[3].obj.Annots[0]
        assert first.objgen != second.objgen
