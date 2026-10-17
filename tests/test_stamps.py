"""Stamps: a ready word or one's own, with who and when under it, kept so other viewers show them."""

import shutil

import pikepdf
import pypdfium2 as pdfium
import pytest
from PySide6.QtCore import QDate, QDateTime, QPointF, QSettings, QTime
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core import textlayout
from zenith_pdf.core.annotations import TextStamp
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui import messages
from zenith_pdf.ui.tool_menu import OptionGrid, preview_annotation
from zenith_pdf.ui.tools import DEFAULT_STYLES, stamp_words

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def test_the_words_of_a_stamp():
    when = QDateTime(QDate(2026, 10, 17), QTime(15, 30))
    text, detail, name = stamp_words(DEFAULT_STYLES["stamp"], "Andrea", when=when)
    assert text == "APPROVED" and name == "Approved" and detail.startswith("Andrea, ") and "15" in detail
    own = DEFAULT_STYLES["stamp"].__class__(color=(0, 0, 0), stamp="Checked by {author} on page {page}",
                                            stamp_detail=False)
    assert stamp_words(own, "Bo", page=2, when=when) == ("Checked by Bo on page 3", "", "")


def test_a_stamp_survives_saving_and_other_viewers_draw_it(pdf_copy):
    document = Document(pdf_copy)
    width, height = textlayout.stamp_size("APPROVED", "Andrea, 17/10/2026")
    stamp = TextStamp(page=0, rect=(150, 300, width, height), text="APPROVED", detail="Andrea, 17/10/2026",
                      name="Approved", color=(0.0, 0.5, 0.0))
    own = TextStamp(page=0, rect=(150, 450, 160, 40), text="Ελέγχθηκε")  # Greek: drawn as shapes
    document.save([stamp, own])
    document.close()

    with pikepdf.open(pdf_copy) as pdf:
        annots = {str(a.NM): a for a in pdf.pages[0].obj.Annots if "/NM" in a}
        assert str(annots[stamp.id].Name) == "/Approved" and str(annots[own.id].Name) == "/ZenithStamp"
        assert str(annots[stamp.id].AP.N.Resources.Font.HeBo.BaseFont) == "/Helvetica-Bold"

    reopened = Document(pdf_copy)
    loaded = {a.id: a for a in reopened.annotations}
    assert isinstance(loaded[stamp.id], TextStamp)
    assert (loaded[stamp.id].text, loaded[stamp.id].detail, loaded[stamp.id].name) == \
        ("APPROVED", "Andrea, 17/10/2026", "Approved")
    assert loaded[stamp.id].rect == pytest.approx(stamp.rect, abs=0.01) and loaded[own.id].text == "Ελέγχθηκε"
    reopened.close()

    pdf = pdfium.PdfDocument(str(pdf_copy))
    image = pdf[0].render(scale=1, draw_annots=True).to_pil().convert("RGB")
    pdf.close()
    x, y, w, h = stamp.rect
    green = sum(1 for px in range(int(x), int(x + w)) for py in range(int(y), int(y + h))
                if image.getpixel((px, py))[1] > 90 and image.getpixel((px, py))[0] < 60)
    assert green > 200  # the frame and the word
    x, y, w, h = own.rect
    red = sum(1 for px in range(int(x), int(x + w)) for py in range(int(y), int(y + h))
              if image.getpixel((px, py))[0] > 150 and image.getpixel((px, py))[1] < 80)
    assert red > 200


@pytest.fixture
def workspace(qt_app, tmp_path, sample_pdf):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    QTest.qWait(60)
    yield window.current_workspace()
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


class Mouse:
    def modifiers(self):
        from PySide6.QtCore import Qt

        return Qt.KeyboardModifier.NoModifier


def test_the_stamp_tool_stamps_what_its_menu_says(workspace, monkeypatch):
    toolbar, tools, view = workspace.toolbar, workspace.tool_settings, workspace.view
    toolbar.add_tool("stamp")
    tools.set_tool("stamp")
    toolbar.open_menu("stamp")
    QTest.qWait(40)
    block = toolbar.tool_menu.editor.blocks["stamp_text"]
    block.findChild(OptionGrid).choose("Draft")
    assert tools.style("stamp").stamp == "Draft"
    toolbar.tool_menu.editor.blocks["stamp_detail"].switch.click()
    QTest.qWait(10)
    assert preview_annotation("stamp", tools.style("stamp")).text == "DRAFT"
    toolbar.tool_menu.close_menu()

    view._tool().press(view._pages[0], QPointF(300, 400), Mouse())
    [stamp] = [a for a in workspace.store.all() if isinstance(a, TextStamp)]
    x, y, w, h = stamp.rect
    assert stamp.text == "DRAFT" and stamp.detail == "" and (x + w / 2, y + h / 2) == pytest.approx((300, 400))

    monkeypatch.setattr(messages, "get_text", lambda *args, **kwargs: "SEEN")
    view.edit_stamp(stamp)
    [edited] = [a for a in workspace.store.all() if isinstance(a, TextStamp)]
    assert edited.text == "SEEN" and edited.name == ""
    tools.reset_style("stamp")
