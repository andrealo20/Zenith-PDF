import math

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from zenith_pdf.core import textclean
from zenith_pdf.core.document import Document, DocumentClosed
from zenith_pdf.core.render import MAX_RENDER_PIXELS, render_scale
from conftest import PAGES

# A TeX page in the T1 (Cork) encoding, set in a Type 3 font with no Unicode map:
# PDFium reads its ligatures, quotes and dashes as raw control codes.
T1_LINES = [
    b"Quali\x1cers \x10o\x1b\x11 \x15 e\x1ecient",
    b"a hyphen-",
    b"ated word",
]


def t1_pdf(path) -> None:
    pdf = pikepdf.new()
    codes = sorted({c for line in T1_LINES for c in line})
    procs = Dictionary({f"/g{c}": pdf.make_stream(b"500 0 0 0 400 600 d1 0 0 400 600 re f") for c in codes})
    differences = Array([value for c in codes for value in (c, Name(f"/g{c}"))])
    font = Dictionary(Type=Name.Font, Subtype=Name.Type3, FontBBox=[0, 0, 500, 700],
                      FontMatrix=[0.001, 0, 0, 0.001, 0, 0], CharProcs=procs, Resources=Dictionary(),
                      Encoding=Dictionary(Type=Name.Encoding, Differences=differences),
                      FirstChar=0, LastChar=255, Widths=Array([500] * 256))
    ops = [b"BT /F1 12 Tf 72 700 Td"]
    for line in T1_LINES:
        escaped = line.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        ops.append(b"(" + escaped + b") Tj 0 -14 Td")
    ops.append(b"ET")
    page = Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792], Resources=Dictionary(Font=Dictionary(F1=font)),
                      Contents=pdf.make_stream(b"\n".join(ops)))
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(path)


def test_page_sizes_include_rotation(sample_pdf):
    document = Document(sample_pdf)
    assert document.page_count == len(PAGES)
    assert document.page_size(0) == pytest.approx((612, 792))
    assert document.page_size(1) == pytest.approx((595, 842))  # rotated 90
    assert document.page_size(2) == pytest.approx((450, 740))  # cropped
    assert document.page_size(3) == pytest.approx((280, 360))  # cropped and rotated 270
    document.close()


def test_render_matches_requested_scale(sample_pdf):
    document = Document(sample_pdf)
    image = document.render(1, 0.5)
    assert (image.width(), image.height()) == (math.ceil(595 * 0.5), math.ceil(842 * 0.5))
    document.close()
    with pytest.raises(DocumentClosed):
        document.render(0, 1.0)


def test_search_finds_text_inside_each_page(sample_pdf):
    document = Document(sample_pdf)
    hits = list(document.iter_search("needle"))
    assert [hit.page for hit in hits] == list(range(len(PAGES)))
    for hit in hits:
        width, height = document.page_size(hit.page)
        assert "needle" in hit.context
        for x, y, w, h in hit.rects:
            assert 0 <= x and x + w <= width + 0.5
            assert 0 <= y and y + h <= height + 0.5
    document.close()


def test_a_search_stops_when_the_pages_change_under_it(sample_pdf, tmp_path):
    """The search panel's thread walks the pages while an edit may take them away: it stops instead of asking
    PDFium for a page that is gone."""
    path = tmp_path / "doc.pdf"
    path.write_bytes(sample_pdf.read_bytes())
    document = Document(path)
    search = document.iter_search("needle")
    assert next(search).page == 0
    document.delete([], [1, 2, 3])
    assert list(search) == []
    document.close()


def test_t1_codes_are_cleaned_for_copy_and_search(tmp_path):
    path = tmp_path / "t1.pdf"
    t1_pdf(path)
    document = Document(path)
    raw = document._textpage(0).get_text_range()
    assert "\x1c" in raw  # the fixture shows the problem: PDFium returns the codes as they are
    text = document.page_text(0)
    assert text.startswith("Qualifiers “off” \u2013 efficient")  # code 0x15 is the en dash
    assert "hyphenated" in text and not any(ord(c) < 32 and c not in "\r\n\t" for c in text)
    assert document.text_span(0, 0, 8)[1] == "Qualifiers"  # 9 characters on the page, one of them "fi"
    assert document.text_in(0, [(0, 0, *document.page_size(0))]).startswith("Qualifiers")

    for query, case, word in (("Qualifiers", False, False), ("QUALIFIERS", False, True), ("efficient", True, False),
                              ("hyphenated", False, False), ("“off”", False, False)):
        [hit] = list(document.iter_search(query, case, word))
        assert query.lower() in hit.context.lower() and hit.rects
    assert list(document.iter_search("QUALIFIERS", match_case=True)) == []
    assert list(document.iter_search("Qualifier", whole_word=True)) == []
    [hit] = list(document.iter_search("Qualifiers"))
    x, _, w, _ = hit.rects[0]
    assert w == pytest.approx(8 * 6 + 4.8, abs=0.5)  # 9 glyphs as drawn, the ligature once: 8 advances and a box
    document.close()


def test_textclean_keeps_layout_and_maps_positions():
    assert textclean.clean("a\r\nb\tc") == "a\r\nb\tc"
    assert textclean.clean("ﬁne ﬂy") == "fine fly"
    text, origin = textclean.cleaned("x\U0001d44e\x1cy")  # a character beyond U+FFFF counts twice
    assert text == "x\U0001d44efiy" and origin[2] == origin[3] == (3, 4) and origin[1] == (1, 3)
    assert textclean.find("Quali\x1cers and more", "qualifiers   and") == [(0, 13)]
    assert textclean.find("word\r\nnext", "word next") == [(0, 10)]


def test_render_scale_is_exact_for_ordinary_pages():
    # A4 at 100% on a 150% display.
    assert render_scale((595, 842), 96 / 72, 1.5) == pytest.approx(2.0)


@pytest.mark.parametrize("page_size", [(2384, 3370), (14400, 14400)])  # A0, maximum PDF page
def test_render_scale_respects_pixel_budget(page_size):
    scale = render_scale(page_size, 8 * 96 / 72, 2.0)
    width, height = page_size
    assert 0 < scale
    assert math.ceil(width * scale) * math.ceil(height * scale) <= MAX_RENDER_PIXELS
