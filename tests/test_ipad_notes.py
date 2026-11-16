"""Pictures the iPad app places: the desktop reads them from /ZenithImageData when their appearance holds more
than a plain picture (PDFKit's appearance streams clip), and keeps them editable through a save."""

import base64

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String
from PySide6.QtGui import QColor, QImage

from zenith_pdf.core import images
from zenith_pdf.core.annotations import ImageStamp
from zenith_pdf.core.document import Document
from zenith_pdf.core.pdf_annots import page_geometry, read_annotation

RECT = (100.0, 500.0, 300.0, 650.0)  # PDF space: left, bottom, right, top


@pytest.fixture
def png(qt_app):
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 40, 60))
    return images._save(image, "PNG")


def _stamp(pdf, data, clip, carried):
    """A stamp drawn the way PDFKit draws one: a clip around the picture, so picture_parts refuses it."""
    left, bottom, right, top = RECT
    width, height = right - left, top - bottom
    resources = Dictionary()
    content = b""
    if data is not None:
        resources.XObject = Dictionary(Im0=pdf.make_indirect(images.xobject(pdf, data)))
        clipping = f"0 0 {width} {height} re W n ".encode() if clip else b""
        content = b"q " + clipping + f"{width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    form = pikepdf.Stream(pdf, content)
    form.Type, form.Subtype = Name.XObject, Name.Form
    form.BBox = Array([0, 0, width, height])
    form.Resources = resources
    stamp = Dictionary(Type=Name.Annot, Subtype=Name.Stamp, Rect=Array(list(RECT)), NM=String("zenith-test"),
                       F=4, AP=Dictionary(N=form))
    if carried is not None:
        stamp.ZenithImageData = String(carried)
    return pdf.make_indirect(stamp)


def _note(tmp_path, data, clip=True, carried=None, rotate: int = 0):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    page = pdf.pages[0].obj
    if rotate:
        page.Rotate = rotate
    page.Annots = Array([_stamp(pdf, data, clip, carried)])
    path = tmp_path / "ipad.pdf"
    pdf.save(path)
    return path


def test_a_clipped_picture_with_carried_bytes_reads_as_a_picture(tmp_path, png):
    path = _note(tmp_path, png, carried=base64.b64encode(png))
    document = Document(path)
    pictures = [a for a in document.annotations if isinstance(a, ImageStamp)]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.data == png
    assert picture.rotation == 0
    assert picture.rect == pytest.approx((100.0, 842 - 650.0, 200.0, 150.0))
    document.close()


def test_the_picture_follows_the_page_rotation(tmp_path, png):
    path = _note(tmp_path, png, carried=base64.b64encode(png), rotate=90)
    document = Document(path)
    (picture,) = [a for a in document.annotations if isinstance(a, ImageStamp)]
    assert picture.rect == pytest.approx(document.geometry(0).rect_to_display(RECT))
    document.close()


def test_without_carried_bytes_a_clipped_stamp_is_left_alone(tmp_path, png):
    path = _note(tmp_path, png)
    document = Document(path)
    assert not [a for a in document.annotations if isinstance(a, ImageStamp)]
    document.close()


@pytest.mark.parametrize("carried", [b"not base64 at all!", base64.b64encode(b"GIF89a not a png")])
def test_carried_bytes_that_are_no_picture_are_ignored(tmp_path, png, carried):
    path = _note(tmp_path, png, carried=carried)
    document = Document(path)
    assert not [a for a in document.annotations if isinstance(a, ImageStamp)]
    document.close()


def test_a_plain_appearance_still_wins_over_the_carried_bytes(tmp_path, png, qt_app):
    other = QImage(8, 8, QImage.Format.Format_RGB32)
    other.fill(QColor(0, 0, 255))
    path = _note(tmp_path, png, clip=False, carried=base64.b64encode(images._save(other, "PNG")))
    with pikepdf.open(path) as pdf:
        obj = pdf.pages[0].Annots[0]
        picture = read_annotation(obj, 0, page_geometry(pdf.pages[0].obj))
    assert isinstance(picture, ImageStamp)
    assert images.size(picture.data) == (40, 30)  # the picture the appearance draws, not the carried one


def test_the_picture_survives_a_desktop_save_and_keeps_its_bytes(tmp_path, png):
    carried = base64.b64encode(png)
    path = _note(tmp_path, png, carried=carried)
    document = Document(path)
    (picture,) = [a for a in document.annotations if isinstance(a, ImageStamp)]
    x, y, w, h = picture.rect
    moved = [a.translated(10, 20) if a is picture else a for a in document.annotations]
    out = tmp_path / "moved.pdf"
    document.save(moved, out)
    document.close()

    with pikepdf.open(out) as pdf:
        assert bytes(pdf.pages[0].Annots[0].ZenithImageData) == carried  # not a key the desktop manages
    reopened = Document(out)
    (again,) = [a for a in reopened.annotations if isinstance(a, ImageStamp)]
    assert again.rect == pytest.approx((x + 10, y + 20, w, h))
    assert images.size(again.data) == (40, 30)
    reopened.close()
