"""Page edits and page geometry on files other apps made: nulls in /Annots, links and bookmarks to a deleted page,
grouped annotations and form fields on a copied page, null heights in destinations, layers shown again before
saving, and boxes PDFium reads its own way."""

import shutil
import warnings

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Link, Shape
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings

pytestmark = pytest.mark.usefixtures("qt_app")


def text_pdf(texts, size=(612, 792)):
    """One page per text, each written at (72, 700); `size` is the MediaBox's width and height."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    for text in texts:
        page = pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, *size],
                                       Resources=Dictionary(Font=Dictionary(F1=font))))
        page.obj.Contents = pdf.make_stream(f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1"))
        pdf.pages.append(page)
    return pdf


def test_flatten_passes_over_a_null_in_annots(tmp_path):
    path = tmp_path / "doc.pdf"
    text_pdf(["One"]).save(path)
    document = Document(path)
    document.save([Shape(page=0, rect=(100, 100, 200, 150))])  # drawn by Zenith PDF: it has an appearance
    document.close()
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Annots = Array([None, *pdf.pages[0].obj.Annots])
        pdf.save()

    document = Document(path)
    [shape] = document.annotations
    document.flatten(document.annotations, {shape.id})
    assert document.annotations == []
    document.save([])
    document.close()
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0].obj
        assert list(page.get("/Annots", [])) == []
        assert any(str(name).startswith("/ZenithFlat") for name in page.Resources.XObject.keys())


def test_deleting_a_page_leaves_nothing_that_leads_to_it_in_the_file(tmp_path):
    path = tmp_path / "doc.pdf"
    pdf = text_pdf(["Page one", "SECRET SALARY TABLE", "Page three"])
    _, second, third = (page.obj for page in pdf.pages)

    def link(y, **where):
        return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[72, y, 200, y + 20],
                                            Border=[0, 0, 0], **where))

    pdf.pages[0].obj.Annots = Array([link(600, Dest=Array([second, Name.Fit])), link(550, Dest=String("salaries")),
                                     link(500, Dest=Array([third, Name.Fit]))])
    pdf.Root.Names = Dictionary(Dests=Dictionary(Names=Array([String("salaries"), Array([second, Name.Fit])])))
    pdf.Root.OpenAction = Array([second, Name.Fit])
    with pdf.open_outline() as outline:
        part = pikepdf.OutlineItem("Part two", 1)
        part.children.append(pikepdf.OutlineItem("Page three", 2))
        outline.root.extend([pikepdf.OutlineItem("Salaries", 1), part])
    pdf.save(path)

    document = Document(path)
    document.delete(document.annotations, [1])
    assert [(type(a), a.target) for a in document.annotations] == [(Link, 1)]  # only the link to page three
    # The bookmark with one under it stays, leading nowhere.
    assert [(e.level, e.title, e.page) for e in document.outline()] == [(0, "Part two", None), (1, "Page three", 1)]
    document.save(document.annotations)
    document.close()
    with pikepdf.open(path) as saved:
        assert len(saved.pages) == 2
        assert not any(b"SECRET" in obj.read_bytes() for obj in saved.objects if isinstance(obj, pikepdf.Stream))
        assert "/OpenAction" not in saved.Root and "salaries" not in pikepdf.NameTree(saved.Root.Names.Dests)


def test_a_duplicated_page_groups_its_copies_apart_from_the_originals(tmp_path):
    path = tmp_path / "doc.pdf"
    text_pdf(["One"]).save(path)
    document = Document(path)
    grouped = [Shape(page=0, rect=(100, 100, 50, 50), group="g"), Shape(page=0, rect=(200, 100, 50, 50), group="g")]
    document.duplicate(grouped, 0)
    groups = {page: {a.group for a in document.annotations if a.page == page} for page in (0, 1)}
    document.close()
    assert groups[0] == {"g"}
    assert len(groups[1]) == 1 and groups[1] != {"g"}  # still one group, a new one


def test_a_duplicated_page_has_form_fields_of_its_own(form_pdf, tmp_path):
    path = tmp_path / "form.pdf"
    shutil.copy(form_pdf, path)
    document = Document(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        document.duplicate(document.annotations, 0)
    assert not [w for w in caught if issubclass(w.category, pikepdf.PageCopyWarning)]  # every widget is in /AcroForm
    fields = document.form_fields()
    assert all(len({box.page for box in field.boxes}) == 1 for field in fields)
    original = next(f for f in fields if f.name == "name")
    copy = next(f for f in fields if f.kind == "text" and not f.multiline and f.boxes[0].page == 1)
    document.fill_fields(document.annotations, {copy.name: "Ada"})
    values = {f.name: f.value for f in document.form_fields()}
    document.close()
    assert values[copy.name] == "Ada" and values[original.name] == ""


def test_a_destination_with_a_null_height_shows_the_top_of_its_page(tmp_path):
    path = tmp_path / "doc.pdf"
    pdf = text_pdf(["One", "Two"])
    second = pdf.pages[1].obj
    with pdf.open_outline() as outline:
        outline.root.extend([pikepdf.OutlineItem("FitH null", Array([second, Name.FitH, None])),
                             pikepdf.OutlineItem("FitBH null", Array([second, Name.FitBH, None])),
                             pikepdf.OutlineItem("FitH 500", Array([second, Name.FitH, 500]))])
    pdf.save(path)
    document = Document(path)
    entries = [(e.title, e.page, e.point) for e in document.outline()]
    document.close()
    assert entries == [("FitH null", 1, None), ("FitBH null", 1, None), ("FitH 500", 1, (0.0, 500.0))]


def test_a_layer_hidden_and_shown_again_saves_the_page_whole(tmp_path):
    path = tmp_path / "layered.pdf"
    pdf = pikepdf.new()
    group = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("Notes")))
    page = pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 300, 300],
                                   Resources=Dictionary(Properties=Dictionary(oc1=group))))
    page.obj.Contents = pdf.make_stream(b"/OC /oc1 BDC 1 0 0 rg 50 50 200 200 re f EMC")
    pdf.pages.append(page)
    pdf.Root.OCProperties = Dictionary(OCGs=[group], D=Dictionary(Order=[group], ON=[group]))
    pdf.save(path)

    document = Document(path)
    document.set_layer_shown(0, False)
    document.set_layer_shown(0, True)
    document.save([])
    document.close()
    again = Document(path)
    middle = again.render(0, 1.0).pixelColor(150, 150)
    again.close()
    assert middle.red() > 200 and middle.green() < 80  # the red square is still there


def test_a_page_whose_cropbox_misses_its_mediabox_still_opens(tmp_path):
    path = tmp_path / "crop.pdf"
    pdf = text_pdf(["One", "Two"])
    pdf.pages[0].obj.CropBox = Array([1000, 1000, 1200, 1200])  # nowhere on the page: PDFium sizes it 0 x 0
    pdf.save(path)
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1000, 800)
    window.show()
    QTest.qWaitForWindowExposed(window)
    try:
        workspace = window.open_file(path)
        QTest.qWait(60)
        assert workspace is not None and len(window.workspaces) == 1
        assert workspace.document.geometry(0).size == workspace.document.page_size(0)
    finally:
        for index in reversed(range(len(window.workspaces))):
            window.close_tab(index, confirm=False)
        window.close()
        window.renderer.shutdown()


def test_an_empty_mediabox_puts_search_hits_on_the_page(tmp_path):
    path = tmp_path / "empty.pdf"
    text_pdf(["needle"], size=(0, 0)).save(path)  # PDFium shows a Letter page instead
    document = Document(path)
    size = document.page_size(0)
    geometry = document.geometry(0)
    [hit] = list(document.iter_search("needle"))
    document.close()
    assert geometry.size == size == (612, 792)
    x, y, width, height = hit.rects[0]
    assert 0 <= y and y + height <= 792 and y == pytest.approx(792 - 700 - height, abs=6)


def test_the_doc_builder_gives_copied_form_fields_a_form_of_their_own(form_pdf, tmp_path):
    from zenith_pdf.core import builder

    sources = {"form": builder.Source("form", form_pdf)}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        data = builder.build(sources, [builder.Entry("form", 0), builder.Entry("form", 0)])
    assert not [w for w in caught if issubclass(w.category, pikepdf.PageCopyWarning)]  # every widget is in /AcroForm
    path = tmp_path / "built.pdf"
    path.write_bytes(data)
    document = Document(path)
    fields = document.form_fields()
    document.close()
    assert fields and all(len({box.page for box in field.boxes}) == 1 for field in fields)
