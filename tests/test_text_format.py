"""Text boxes in a font of their own: Helvetica, Times or Courier, bold, italic, underlined, lined up left,
centred or right; kept in the PDF the way other viewers read them."""

import shutil
from dataclasses import replace

import pikepdf
import pypdfium2 as pdfium
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core import appearance, textlayout
from zenith_pdf.core.annotations import TEXT_PADDING, FreeText
from zenith_pdf.core.document import Document
from zenith_pdf.core.geometry import PageGeometry
from zenith_pdf.settings import Settings
from zenith_pdf.ui.tool_menu import OptionGrid, Segmented

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def test_lines_line_up_left_centred_or_right():
    face = ("Times", True, False)
    for align in ("left", "center", "right"):
        [(line, x, _)] = textlayout.layout("Short", 12.0, 200.0, face, align)
        width = textlayout.text_width(line, 12.0, face)
        left, right = x - TEXT_PADDING, 200.0 - TEXT_PADDING - (x + width)
        if align == "left":
            assert left == pytest.approx(0.0)
        elif align == "right":
            assert right == pytest.approx(0.0, abs=1e-6)
        else:
            assert left == pytest.approx(right, abs=1e-6)
    # Bold is wider than regular, Courier's letters all as wide.
    assert textlayout.text_width("Wide", 12, ("Helvetica", True, False)) > textlayout.text_width("Wide", 12)
    assert textlayout.text_width("iiii", 12, ("Courier", False, False)) == pytest.approx(
        textlayout.text_width("MMMM", 12, ("Courier", False, False)))


def test_the_appearance_names_the_font_and_draws_the_underline():
    box = FreeText(page=0, rect=(50, 50, 200, 40), contents="Hello", font="Times", bold=True, italic=True,
                   underline=True, align="center")
    look = appearance.build(box, PageGeometry((0, 0, 612, 792), 0))
    content = look.content.decode()
    assert "/TiBI 12 Tf" in content and look.face == ("Times", True, True)
    assert content.count(" re f") >= 1  # the underline, a thin filled bar
    assert appearance.standard_font(("Courier", False, True)) == ("CoOb", "Courier-Oblique")
    assert appearance.standard_font(("Comic", False, False)) == ("Helv", "Helvetica")


def test_a_formatted_box_survives_saving_and_other_viewers_show_it(pdf_copy):
    document = Document(pdf_copy)
    box = FreeText(page=0, rect=(100, 300, 300, 40), contents="Right side", font_size=20, font="Courier",
                   bold=True, underline=True, align="right")
    document.save([box])
    document.close()

    with pikepdf.open(pdf_copy) as pdf:
        [annot] = [a for a in pdf.pages[0].obj.Annots if a.Subtype == "/FreeText"]
        assert str(annot.DA).startswith("/CoBo 20 Tf") and int(annot.Q) == 2
        assert "font-weight:bold" in str(annot.DS) and "text-decoration:underline" in str(annot.DS)
        fonts = annot.AP.N.Resources.Font
        assert str(fonts.CoBo.BaseFont) == "/Courier-Bold"

    reopened = Document(pdf_copy)
    [loaded] = [a for a in reopened.annotations if isinstance(a, FreeText)]
    assert (loaded.font, loaded.bold, loaded.italic, loaded.underline, loaded.align) == \
        ("Courier", True, False, True, "right")
    reopened.close()

    # Another viewer draws the text at the right of the box: ink in its right half, none in its left.
    pdf = pdfium.PdfDocument(str(pdf_copy))
    image = pdf[0].render(scale=1, draw_annots=True).to_pil().convert("L")
    pdf.close()
    x, y, w, h = box.rect
    dark = lambda x0, x1: sum(image.getpixel((px, py)) < 128 for px in range(int(x0), int(x1))
                               for py in range(int(y), int(y + h)))
    assert dark(x + w / 2, x + w) > 20 and dark(x, x + w / 3) == 0


def test_text_boxes_from_other_apps_keep_their_font():
    from zenith_pdf.core.pdf_annots import _read_face

    assert _read_face("/Arial,BoldItalic 11 Tf 0 g", "") == ("Helvetica", True, True, False)
    assert _read_face("/Helv 12 Tf 0 g", "font: Times New Roman 12pt; font-weight:bold") == \
        ("Times", True, False, False)
    assert _read_face("/F1 9 Tf", "font: 9pt Courier,monospace; text-decoration:underline") == \
        ("Courier", False, False, True)
    assert _read_face("/F2 10 Tf", "font: Helvetica,sans-serif 10pt") == ("Helvetica", False, False, False)


@pytest.fixture
def window(qt_app, tmp_path, sample_pdf):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    QTest.qWait(60)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def test_the_text_tool_menu_sets_the_font_and_new_boxes_take_it(window):
    workspace = window.current_workspace()
    toolbar, tools = workspace.toolbar, workspace.tool_settings
    if "text" not in toolbar.keys():
        toolbar.add_tool("text")
    tools.set_tool("text")
    toolbar.open_menu("text")
    QTest.qWait(40)
    block = toolbar.tool_menu.editor.blocks["text_format"]
    fonts = block.findChild(Segmented)
    styles, aligns = block.findChildren(OptionGrid)
    assert [v for v, _ in fonts.items] == ["Helvetica", "Times", "Courier"]
    fonts.choose("Times")
    styles.choose("bold")
    styles.choose("italic")
    styles.choose("italic")  # off again: none at all may be on
    aligns.choose("center")
    style = tools.style("text")
    assert (style.font, style.bold, style.italic, style.align) == ("Times", True, False, "center")
    toolbar.tool_menu.close_menu()

    view = workspace.view
    view.new_text_box(0, (80.0, 80.0))
    editor = view._editor[0]
    assert editor.font().family() == "Times New Roman" and editor.font().bold()
    editor.setPlainText("A centred title")
    editor.finish()
    QApplication.processEvents()
    [box] = [a for a in workspace.store.all() if isinstance(a, FreeText)]
    assert (box.font, box.bold, box.align) == ("Times", True, "center")
    tools.reset_style("text")


def test_the_selection_menu_makes_a_box_bold_and_the_box_grows_to_fit(window):
    workspace = window.current_workspace()
    view, store = workspace.view, workspace.store
    text = "Words that only just fit on a line"
    width = textlayout.text_width(text, 12.0) + 2 * TEXT_PADDING + 1
    height = textlayout.fitted_height(text, 12.0, width)
    box = FreeText(page=0, rect=(80, 120, width, height), contents=text)
    view.add_annotations([box], "Text box")
    view.select([box])
    QTest.qWait(40)
    bar = workspace.selection_bar
    bar.open_style()
    QTest.qWait(40)
    styles, _ = bar.style_menu.editor.blocks["text_format"].findChildren(OptionGrid)
    styles.choose("bold")
    [changed] = [a for a in store.all() if isinstance(a, FreeText)]
    assert changed.bold and changed.rect[3] > height  # bold is wider: two lines now, all of them shown
    assert replace(changed, bold=False, rect=box.rect) == box
    bar.style_menu.close_menu()


def test_properties_change_the_font_of_several_boxes_and_leave_mixed_ones_alone(qt_app):
    from zenith_pdf.ui.dialogs import PropertiesDialog

    boxes = [FreeText(page=0, rect=(0, 0, 100, 30), contents="One", bold=True),
             FreeText(page=0, rect=(0, 40, 100, 30), contents="Two", font="Times")]
    dialog = PropertiesDialog(boxes)
    assert dialog.face_box.currentText() == "Mixed" and dialog.style_boxes["bold"].isTristate()
    assert dialog.changes() == {}  # opened and confirmed: nothing changes
    dialog.style_boxes["italic"].setChecked(True)
    dialog.align_box.setCurrentIndex(dialog.align_box.findData("right"))
    assert dialog.changes() == {"italic": True, "align": "right"}
    dialog.deleteLater()
