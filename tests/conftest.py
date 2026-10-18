import ctypes
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ZENITH_LANGUAGE", "en")  # the tests read the English texts, whatever the system speaks
if sys.platform == "win32":  # the offscreen platform finds no fonts of its own there
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"))
os.environ.setdefault("ZENITH_RENDER_PROCESSES", "0")  # test_render.py covers the processes
# The home page's thumbnails go to a folder of the run's own, never the user's cache.
CACHE = tempfile.mkdtemp(prefix="zenith-tests-cache-")
os.environ["ZENITH_CACHE_DIR"] = CACHE

import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole run: Qt allows a single instance per process."""
    return QApplication.instance() or QApplication(["zenith-tests"])


@pytest.fixture(scope="session", autouse=True)
def private_cache():
    yield CACHE
    shutil.rmtree(CACHE, ignore_errors=True)


def settle(ms=30):
    """Let Qt run its timers and events for `ms` milliseconds."""
    QTest.qWait(ms)
    QApplication.processEvents()


def wait_for(condition, timeout=20.0) -> bool:
    """Whether `condition()` comes true within `timeout` seconds, with Qt running meanwhile."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        QTest.qWait(10)
    return condition()

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


@pytest.fixture(scope="session")
def form_pdf(tmp_path_factory):
    """One 612 x 792 page with a form: a text field "name" (y 700), a multiline "notes" (y 560 to 640), a check
    box "agree" (on state /Yes), a radio group "size" with S and L, and a combo box "colour" (Red, Green, Blue).
    Display y = 792 - PDF y."""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    path = tmp_path_factory.mktemp("forms") / "form.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    zapf = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.ZapfDingbats))

    def stream(text: bytes, w: float, h: float):
        return pdf.make_stream(text, Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, w, h],
                               Resources=Dictionary(Font=Dictionary(ZaDb=zapf)))

    def widget(rect, **more):
        return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, Rect=rect, P=page.obj, F=4, **more))

    name = widget([100, 690, 300, 710], FT=Name.Tx, T=String("name"), V=String(""), DA=String("/Helv 12 Tf 0 g"))
    notes = widget([100, 560, 400, 640], FT=Name.Tx, T=String("notes"), Ff=4096, V=String(""),
                   DA=String("/Helv 10 Tf 0 g"))
    on = stream(b"q BT /ZaDb 12 Tf 2 3 Td (4) Tj ET Q", 16, 16)
    off = stream(b"", 16, 16)
    agree = widget([100, 500, 116, 516], FT=Name.Btn, T=String("agree"), V=Name.Off, AS=Name.Off,
                   AP=Dictionary(N=Dictionary(Yes=on, Off=off)))
    size = pdf.make_indirect(Dictionary(FT=Name.Btn, T=String("size"), Ff=49152, V=Name.Off, Kids=Array([])))
    for state, x in (("S", 100), ("L", 140)):
        dot = stream(b"q BT /ZaDb 12 Tf 2 3 Td (l) Tj ET Q", 16, 16)
        kid = widget([x, 450, x + 16, 466], Parent=size, AS=Name.Off,
                     AP=Dictionary(N=Dictionary({f"/{state}": dot, "/Off": stream(b"", 16, 16)})))
        size.Kids.append(kid)
    colour = widget([100, 400, 250, 420], FT=Name.Ch, T=String("colour"), Ff=131072,
                    Opt=Array([String("Red"), String("Green"), String("Blue")]), V=String("Red"),
                    DA=String("/Helv 12 Tf 0 g"))
    page.obj.Annots = Array([name, notes, agree, *size.Kids, colour])
    pdf.Root.AcroForm = Dictionary(Fields=Array([name, notes, agree, size, colour]), DA=String("/Helv 0 Tf 0 g"),
                                   DR=Dictionary(Font=Dictionary(Helv=font, ZaDb=zapf)))
    pdf.save(path)
    return path
