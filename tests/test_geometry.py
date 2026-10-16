import ctypes

import pypdfium2.raw as raw
import pytest

from zenith_pdf.core.document import PDFIUM_LOCK, Document
from conftest import PAGES

# FPDF_PageToDevice works in whole device pixels, so compare at 1000 pixels per point.
SCALE = 1000


def _pdfium_to_display(document, index, x, y):
    width, height = document.page_size(index)
    page = document._pdf[index]
    try:
        dx, dy = ctypes.c_int(), ctypes.c_int()
        raw.FPDF_PageToDevice(
            page, 0, 0, round(width * SCALE), round(height * SCALE), 0, x, y, dx, dy
        )
    finally:
        page.close()
    return dx.value / SCALE, dy.value / SCALE


@pytest.mark.parametrize("index", range(len(PAGES)))
def test_display_mapping_matches_pdfium(sample_pdf, index):
    document = Document(sample_pdf)
    geometry = document.geometry(index)
    assert geometry.size == pytest.approx(document.page_size(index))

    left, bottom, right, top = geometry.box
    for x, y in [(left, bottom), (right, top), (left + 30, top - 70), ((left + right) / 2, bottom + 5)]:
        with PDFIUM_LOCK:
            expected = _pdfium_to_display(document, index, x, y)
        assert geometry.to_display(x, y) == pytest.approx(expected, abs=0.01)
        assert geometry.to_pdf(*geometry.to_display(x, y)) == pytest.approx((x, y))
    document.close()


@pytest.mark.parametrize("index", range(len(PAGES)))
def test_text_direction_on_screen(sample_pdf, index):
    """The sample's text runs left to right in PDF space: on screen it turns with the page."""
    document = Document(sample_pdf)
    with PDFIUM_LOCK:
        matrix = raw.FS_MATRIX()
        assert raw.FPDFText_GetMatrix(document._textpage(index).raw, 0, matrix)
    geometry = document.geometry(index)
    assert geometry.display_turns(matrix.a, matrix.b) == geometry.rotation // 90
    # Text running up the PDF page reads bottom to top on an unturned one.
    assert geometry.display_turns(0, 1) == (3 + geometry.rotation // 90) % 4
    document.close()
