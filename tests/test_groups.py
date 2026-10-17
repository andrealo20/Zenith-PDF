"""Groups of annotations: picked, moved, copied and deleted as one; kept in the PDF the way other apps keep
them. And the caret that marks text to put in."""

import shutil

import pikepdf
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Caret, Note, Shape, TextMarkup, regrouped
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def test_a_group_and_a_caret_survive_saving(pdf_copy):
    document = Document(pdf_copy)
    strike = TextMarkup(page=0, style="StrikeOut", quads=((80, 83, 60, 17),), group="g1")
    caret = Caret(page=0, rect=(137, 97, 8, 8), contents="pin", group="g1")
    alone = Shape(page=0, rect=(200, 300, 50, 40))
    document.save([strike, caret, alone])
    document.close()

    with pikepdf.open(pdf_copy) as pdf:
        annots = {str(a.NM): a for a in pdf.pages[0].obj.Annots if "/NM" in a}
        assert str(annots[caret.id].Subtype) == "/Caret" and str(annots[caret.id].ZenithGroup) == "g1"
        assert annots[caret.id].RT == pikepdf.Name.Group and annots[caret.id].IRT.objgen == annots[strike.id].objgen
        assert "/IRT" not in annots[strike.id] and "/ZenithGroup" not in annots[alone.id]

    reopened = Document(pdf_copy)
    loaded = {a.id: a for a in reopened.annotations}
    assert loaded[caret.id].group == loaded[strike.id].group == "g1" and loaded[alone.id].group == ""
    assert loaded[caret.id].rect == pytest.approx(caret.rect, abs=0.01) and loaded[caret.id].contents == "pin"
    # Ungrouped and saved again: the link between them goes.
    reopened.save([a if a.id != caret.id else Caret(page=0, rect=caret.rect, contents="pin", id=caret.id)
                   for a in reopened.annotations])
    reopened.close()
    with pikepdf.open(pdf_copy) as pdf:
        annots = {str(a.NM): a for a in pdf.pages[0].obj.Annots if "/NM" in a}
        assert "/IRT" not in annots[caret.id] and "/RT" not in annots[caret.id]


def test_a_group_another_app_made_is_one_here(pdf_copy):
    with pikepdf.open(pdf_copy, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0].obj
        first = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Square,
                                                     Rect=[100, 100, 150, 150], NM=pikepdf.String("a")))
        second = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Circle,
                                                      Rect=[200, 100, 250, 150], NM=pikepdf.String("b"),
                                                      IRT=first, RT=pikepdf.Name.Group))
        page.Annots = pikepdf.Array([first, second])
        pdf.save(pdf_copy)
    document = Document(pdf_copy)
    groups = {a.id: a.group for a in document.annotations}
    assert groups == {"a": "a", "b": "a"}
    document.close()


def test_copies_keep_their_groups_under_new_names():
    a, b, c = (Note(page=0, group="g"), Note(page=0, group="g"), Note(page=0))
    copies = regrouped([a, b, c])
    assert copies[0].group == copies[1].group != "g" and copies[2].group == ""


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


def test_grouped_annotations_are_picked_together_and_come_apart(workspace):
    view, store = workspace.view, workspace.store
    one, two, other = (Shape(page=0, rect=(100, 100, 40, 40)), Shape(page=0, rect=(200, 100, 40, 40)),
                       Shape(page=0, rect=(300, 100, 40, 40)))
    view.add_annotations([one, two, other], "Shapes")
    view.select([one, two])
    assert view.can_group()
    arrange = {action.text(): action for action in workspace.selection_bar.arrange.menu().actions()}
    QApplication.processEvents()
    assert arrange["Group"].isEnabled() and not arrange["Ungroup"].isEnabled()
    view.group_selected()
    assert not view.can_group()
    view.select([next(a for a in store.all() if a.id == one.id)])  # one of them: the whole group
    assert {a.id for a in view.selected_annotations()} == {one.id, two.id}

    view.duplicate_selected()
    copies = [a for a in store.all() if a.id not in {one.id, two.id, other.id}]
    groups = {a.group for a in store.all() if a.id in {one.id, two.id}}
    assert len(copies) == 2 and len({a.group for a in copies}) == 1 and not {a.group for a in copies} & groups

    view.select([next(a for a in store.all() if a.id == one.id)])
    view.ungroup_selected()
    view.select([next(a for a in store.all() if a.id == one.id)])
    assert [a.id for a in view.selected_annotations()] == [one.id]
    workspace.undo_stack.undo()  # grouped again
    view.select([next(a for a in store.all() if a.id == two.id)])
    assert {a.id for a in view.selected_annotations()} == {one.id, two.id}
