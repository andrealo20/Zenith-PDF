"""Back and forward through jumps, and landing on the spot a link or a bookmark points at."""

import shutil
from dataclasses import replace

import pikepdf
import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Link, new_id
from zenith_pdf.core.document import Document
from zenith_pdf.core.platforms import native_keys
from zenith_pdf.settings import Settings
from zenith_pdf.ui.commands import ChangeAnnotations
from zenith_pdf.ui.history import LIMIT, History, Spot


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, links_pdf, tmp_path):
    path = tmp_path / "links.pdf"
    shutil.copy(links_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    settle()
    yield workspace
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def on_viewport(view, page, x, y):
    """Where a display-space point of `page` is in viewport pixels."""
    return view.mapFromScene(view._pages[page].mapToScene(QPointF(x, y)))


def readable(view, page, y) -> bool:
    """Whether the height `y` of `page` is on screen, below the toolbar band."""
    row = on_viewport(view, page, 100, y).y()
    return view.top_inset <= row <= view.viewport().height()


def test_history_goes_back_and_forward_in_order():
    history = History()
    assert not history.can_back and history.back(Spot(0, 0)) is None and history.forward(Spot(0, 0)) is None
    history.push(Spot(0, 10))
    history.push(Spot(3, 400))
    assert history.peek_back() == Spot(3, 400)
    assert history.back(Spot(7, 50)) == Spot(3, 400)
    assert history.back(Spot(3, 400)) == Spot(0, 10)
    assert not history.can_back and history.can_forward
    assert history.forward(Spot(0, 10)) == Spot(3, 400)
    assert history.forward(Spot(3, 400)) == Spot(7, 50)
    assert not history.can_forward

    history.back(Spot(7, 50))
    history.push(Spot(2, 0))  # a new jump: the way forward is gone
    assert not history.can_forward

    history.clear()
    history.push(Spot(1, 100))
    history.push(Spot(1, 110))  # the same place, give or take a line: kept once
    history.push(Spot(2, 110))
    assert history.back(Spot(5, 0)) == Spot(2, 110) and history.back(Spot(2, 110)) == Spot(1, 100)
    assert not history.can_back

    history.clear()
    for page in range(LIMIT + 10):
        history.push(Spot(page, 0))
    places = []
    while history.can_back:
        places.append(history.back(Spot(999, 0)).page)
    assert len(places) == LIMIT and places[-1] == 10  # the oldest went


def test_destinations_keep_their_spot(links_pdf):
    document = Document(links_pdf)
    try:
        [explicit] = [a for a in document.annotations if isinstance(a, Link) and not a.uri]
        assert (explicit.target, explicit.target_point) == (2, (0.0, 200.0))
        assert document.link_at(0, (100, 132)) == ("page", 1, (0.0, 300.0))  # the named one, answered by PDFium
        assert document.outline()[0].point == (0.0, 150.0)
        assert document.display_y(2, (0.0, 200.0)) == 592 and document.display_y(2, None) is None
    finally:
        document.close()


def test_a_link_lands_on_its_spot_and_back_returns_exactly(workspace):
    view, counter = workspace.view, workspace.page_counter
    assert not counter.back_button.isVisible()
    start = view.verticalScrollBar().value()
    target = view._link_target(QPointF(on_viewport(view, 0, 136, 82)))
    assert target == ("page", 2, (0.0, 200.0))
    view.follow_link(*target)
    settle()
    assert view.current_page == 2 and readable(view, 2, 592)
    assert counter.back_button.isVisible() and "page 1" in counter.back_button.toolTip()
    assert native_keys("Alt+Left") in counter.back_button.toolTip()

    view.back()
    settle()
    assert view.current_page == 0 and abs(view.verticalScrollBar().value() - start) <= 1
    assert not counter.back_button.isVisible()
    view.forward()
    settle()
    assert view.current_page == 2 and readable(view, 2, 592)


def test_a_bookmark_lands_on_its_spot(workspace):
    view, tree = workspace.view, workspace.outline.tree
    item = tree.topLevelItem(0)
    assert item.text(0) == "Low on page 2"
    tree.itemClicked.emit(item, 0)
    settle()
    assert view.current_page == 1 and readable(view, 1, 642)
    assert view.history.can_back


def test_page_jumps_go_back_too(workspace):
    view, counter = workspace.view, workspace.page_counter
    counter.focus()
    counter.field.setText("3")
    counter._on_entered()  # Ctrl+G, then Enter
    assert view.current_page == 2 and view.history.can_back
    QTest.keyClick(view, Qt.Key.Key_Home)
    assert view.current_page == 0
    QTest.keyClick(view, Qt.Key.Key_Home)  # already there: nothing to come back to
    assert view.history.back(view.spot()).page == 2


def test_the_spot_survives_a_move_and_a_copy(workspace, tmp_path):
    view, store = workspace.view, workspace.store
    [link] = [a for a in store.all() if isinstance(a, Link) and not a.uri]
    copy = replace(link, id=new_id()).translated(0, 40)  # a new PDF object: the spot is written from the value
    workspace.undo_stack.push(ChangeAnnotations(store, [link], [link.translated(5, 0)], "Move"))
    view.add_annotations([copy], "Link")
    workspace.save()
    with pikepdf.open(workspace.document.path) as pdf:
        destinations = [annot.Dest for annot in pdf.pages[0].obj.Annots if "/Dest" in annot
                        and isinstance(annot.Dest, pikepdf.Array)]
        assert len(destinations) == 2
        for destination in destinations:
            assert destination[0].objgen == pdf.pages[2].obj.objgen
            assert destination[1] == pikepdf.Name.XYZ and destination[3] == 200


def test_the_mouse_back_button_and_alt_left_go_back(workspace, window):
    view = workspace.view
    view.follow_link("page", 2, (0.0, 200.0))
    settle()
    centre = view.viewport().rect().center()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.BackButton, Qt.KeyboardModifier.NoModifier, centre)
    assert view.current_page == 0
    QTest.mouseClick(view.viewport(), Qt.MouseButton.ForwardButton, Qt.KeyboardModifier.NoModifier, centre)
    assert view.current_page == 2
    view.setFocus()
    QTest.keyClick(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert view.current_page == 0
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert view.current_page == 2


def test_deleting_a_page_forgets_the_way_back(workspace):
    view, document = workspace.view, workspace.document
    view.follow_link("page", 2, (0.0, 200.0))
    settle()
    assert view.history.can_back and workspace.page_counter.back_button.isVisible()
    view.set_layout("double")  # the same pages: Back still knows them
    assert view.history.can_back
    view.set_layout("single")
    assert workspace.edit_structure("Delete page", lambda annotations: document.delete(annotations, [1]))
    assert not view.history.can_back and not workspace.page_counter.back_button.isVisible()
