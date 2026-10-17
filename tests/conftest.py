import ctypes
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # the offscreen platform finds no fonts of its own there
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"))
os.environ.setdefault("ZENITH_RENDER_PROCESSES", "0")  # test_render.py covers the processes
# The home page's thumbnails go to a folder of the run's own, never the user's cache.
CACHE = tempfile.mkdtemp(prefix="zenith-tests-cache-")
os.environ["ZENITH_CACHE_DIR"] = CACHE

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole run: Qt allows a single instance per process."""
    return QApplication.instance() or QApplication(["zenith-tests"])


@pytest.fixture(scope="session", autouse=True)
def private_cache():
    yield CACHE
    shutil.rmtree(CACHE, ignore_errors=True)

# (width, height, rotation, cropbox or None)
PAGES = [
    (612, 792, 0, None),
    (842, 595, 90, None),
    (595, 842, 180, (50, 60, 500, 800)),
    (400, 300, 270, (20, 10, 380, 290)),
]


def _add_text(pdf, page, text, x, y):
    obj = raw.FPDFPageObj_NewTextObj(pdf, b"Helvetica", 18)
    encoded = (text + "\x00").encode("utf-16-le")
    buffer = ctypes.create_string_buffer(encoded, len(encoded))
    raw.FPDFText_SetText(obj, ctypes.cast(buffer, ctypes.POINTER(raw.FPDF_WCHAR)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page, obj)


@pytest.fixture(scope="session")
def links_pdf(tmp_path_factory):
    """Three 612 x 792 pages. Page 1 has an explicit link to [page 3 /XYZ 0 200 0], where a caption starts,
    a link to the named destination "second-middle", [page 2 /XYZ 0 300 0], and a web link. A bookmark goes to
    [page 2 /XYZ 0 150 0]. Display y = 792 - PDF y on these pages."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    path = tmp_path_factory.mktemp("links") / "links.pdf"
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    for text, y in (("See Figure 2 and the middle of page 2.", 708), ("Second page", 600),
                    ("Figure 2. A caption under its figure", 188)):
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj.Resources = Dictionary(Font=Dictionary(F1=font))
        page.obj.Contents = pdf.make_stream(f"BT /F1 12 Tf 72 {y} Td ({text}) Tj ET".encode("latin-1"))
    third, second = pdf.pages[2].obj, pdf.pages[1].obj

    def link(rect, **where):
        return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=rect, Border=[0, 0, 0], **where))

    pdf.pages[0].obj.Annots = Array([
        link([72, 700, 200, 720], Dest=Array([third, Name.XYZ, 0, 200, 0])),
        link([72, 650, 200, 670], Dest=String("second-middle")),
        link([72, 600, 200, 620], A=Dictionary(S=Name.URI, URI=String("https://example.org/"))),
    ])
    pdf.Root.Names = Dictionary(Dests=Dictionary(Names=Array([String("second-middle"),
                                                              Array([second, Name.XYZ, 0, 300, 0])])))
    with pdf.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem("Low on page 2", 1, pikepdf.PageLocation.XYZ, top=150))
    pdf.save(path)
    return path


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    pdf = pdfium.PdfDocument.new()
    for number, (width, height, rotation, cropbox) in enumerate(PAGES, start=1):
        page = pdf.new_page(width, height)
        _add_text(pdf, page, f"Page {number} needle", 72, height - 100)
        page.gen_content()
        if cropbox:
            page.set_cropbox(*cropbox)
        page.set_rotation(rotation)
        page.close()
    pdf.save(str(path))
    pdf.close()
    return path
