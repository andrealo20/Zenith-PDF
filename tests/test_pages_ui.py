"""Page commands, the context menu, the page manager and view options, end to end."""

import shutil
import sys

import pytest
from PySide6.QtCore import QEvent, QPointF, QSettings
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidgetAction

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Shape
from zenith_pdf.settings import Settings
from zenith_pdf.ui.context_menu import build_context_menu


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()
    # Deleted now, not at exit: Qt destroying windows after the application has gone can crash the run.
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def page_texts(workspace):
    document = workspace.document
    return [" ".join(document.text_span(i, 0, 12)[1].split()) for i in range(document.page_count)]


def menu_texts(menu):
    texts = []
    for action in menu.actions():
        if action.menu():
            texts.append(action.text())
            texts += [f"  {t}" for t in menu_texts(action.menu())]
        elif not isinstance(action, QWidgetAction) and not action.isSeparator():
            texts.append(action.text())
    return texts


def test_context_menu_offers_every_page_command(workspace):
    menu = build_context_menu(workspace.view, 1, QPointF(50, 50), None)
    texts = menu_texts(menu)
    expected = ["Copy/Paste as", "Page 2", "  Add page (after page 2)", "  Copy page", "  Duplicate page",
                "  Delete page 2", "  Rotate page (Clockwise)", "  Rotate page (Counter-clockwise)", "  Extend margin",
                "  Watermark…", "Insert", "  Grid page", "  Pages from PDF file…", "Select all annotations",
                "Select all text"]
    if workspace.actions.ocr_available():  # offered only where Windows can recognise text
        expected.append("  Run OCR (text recognition)")
    for text in expected:
        assert text in texts
    assert isinstance(menu.actions()[0], QWidgetAction)  # the copy / cut / paste / delete row


def test_page_commands_are_undoable_and_keep_annotations(workspace):
    view, actions = workspace.view, workspace.actions
    shape = Shape(page=1, rect=(40, 40, 80, 60))
    view.add_annotations([shape], "Rectangle")

    actions.duplicate(1)
    assert page_texts(workspace)[1:3] == ["Page 2 needle"] * 2
    assert sorted(a.page for a in workspace.store.all()) == [1, 2]
    assert workspace.thumbnails.model.rowCount() == 5

    actions.rotate(0, 90)
    actions.delete(3)
    actions.add_page(0, "lined")
    assert workspace.document.page_count == 5
    assert workspace.page_counter.total.text().strip() == "of 5"

    for _ in range(4):
        workspace.undo_stack.undo()
    assert page_texts(workspace) == ["Page 1 needle", "Page 2 needle", "Page 3 needle", "Page 4 needle"]
    assert [(a.id, a.page) for a in workspace.store.all()] == [(shape.id, 1)]
    workspace.undo_stack.redo()
    assert workspace.document.page_count == 5


def test_removing_the_page_on_screen_with_the_pages_panel_open(workspace, monkeypatch):
    """The view says which page is current while the Pages list could still hold rows of pages that went."""
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: errors.append(f"{kind.__name__}: {value}"))
    workspace.side.show_panel("pages")
    view, actions, thumbnails = workspace.view, workspace.actions, workspace.thumbnails
    view.goto_page(3)
    QApplication.processEvents()
    actions.delete(3)  # the last page, the one on screen
    QApplication.processEvents()
    assert thumbnails.model.rowCount() == 3 and view.current_page == 2
    actions.duplicate(2)  # goes to the copy, then Undo takes it away
    QApplication.processEvents()
    workspace.undo_stack.undo()
    actions.add_page(2)
    QApplication.processEvents()
    workspace.undo_stack.undo()
    QApplication.processEvents()
    assert errors == []
    assert thumbnails.model.data(thumbnails.model.createIndex(7, 0)) is None  # a row past the end: nothing
    thumbnails.list.setCurrentIndex(thumbnails.model.index(0))  # a click on a thumbnail still goes there
    assert view.current_page == 0


def test_copied_pages_keep_their_links_to_other_pages(workspace):
    """The page manager's Duplicate of several pages, and Copy page then Paste pages, as Duplicate page does."""
    from zenith_pdf.core.annotations import Link

    workspace.view.add_annotations([Link(page=0, rect=(72, 200, 150, 20), target=3)], "Link")

    def leads():
        texts = page_texts(workspace)
        return sorted((texts[a.page], texts[a.target]) for a in workspace.store.all() if isinstance(a, Link))

    workspace.toggle_page_manager()
    workspace.page_manager.select_rows([0, 1])
    workspace.page_manager.duplicate_selected()
    workspace.toggle_page_manager()
    assert leads() == [("Page 1 needle", "Page 4 needle")] * 2
    workspace.actions.copy_page(0)
    workspace.actions.paste_pages(workspace.document.page_count - 1)
    assert leads() == [("Page 1 needle", "Page 4 needle")] * 3


def test_copy_cut_paste_annotations(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 100, 50, 40))
    view.add_annotations([shape], "Rectangle")
    view.reveal_annotation(shape.id)
    view.copy()
    view.paste_annotations(2, (10.0, 20.0))
    pasted = [a for a in workspace.store.all() if a.page == 2]
    assert len(pasted) == 1 and pasted[0].id != shape.id
    assert pasted[0].rect == pytest.approx((10, 20, 50, 40))

    view.reveal_annotation(shape.id)
    view.cut_annotations()
    assert shape.id not in workspace.store


def test_undo_of_a_delete_keeps_the_drawing_order_on_screen_and_in_the_file(workspace):
    from zenith_pdf.ui.commands import RemoveAnnotations

    view = workspace.view
    red, green, blue = (Shape(page=0, rect=(100 + 20 * i, 100, 60, 60), fill=c) for i, c in
                        enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1))))
    view.add_annotations([red, green, blue], "Shapes")
    workspace.undo_stack.push(RemoveAnnotations(workspace.store, [red], "Delete"))
    workspace.undo_stack.undo()
    z = [view._items[a.id].zValue() for a in (red, green, blue)]
    assert z == sorted(z)  # red under green under blue, as before
    workspace.save()
    reopened = [a.id for a in workspace.document.__class__(workspace.document.path).annotations]
    assert reopened == [red.id, green.id, blue.id]


def test_select_all_on_a_page(workspace):
    view = workspace.view
    view.add_annotations([Shape(page=1, rect=(10, 10, 20, 20)), Shape(page=2, rect=(10, 10, 20, 20))], "Shapes")
    view.select_all_annotations(1)
    assert [a.page for a in view.selected_annotations()] == [1]
    view.select_all_text(0)
    view.copy_selection()
    assert "Page 1 needle" in QApplication.clipboard().text()


def test_page_manager_reorders_by_drag_result(workspace):
    workspace.toggle_page_manager()
    manager = workspace.page_manager
    assert manager is not None and manager.isVisible() and not workspace.view.isVisible()
    manager.model.reordered.emit([3, 0, 1, 2])
    assert page_texts(workspace)[0] == "Page 4 needle"
    assert manager.model.rowCount() == 4
    manager.closed.emit(0)
    assert workspace.page_manager is None and workspace.view.isVisible()


def test_two_page_layout_and_tints_paint_cleanly(workspace, window, capfd):
    view = workspace.view
    view.add_annotations([Shape(page=1, rect=(10, 10, 100, 100), fill=(0, 0, 1))], "Shape")
    view.set_layout("double")
    page0, page1 = view._pages[0], view._pages[1]
    # Side by side (pages of different heights are centred on the row, so compare rows).
    assert view._row_of[0] == [0, 1] and page0.x() < page1.x()
    point = view.mapFromScene(page1.mapToScene(QPointF(20, 20)))
    assert view._page_at(QPointF(point))[0] is page1
    for tint in ("dark", "sepia", "normal"):
        view.set_page_tint(tint)
        window.grab()
    view.set_layout("cover")
    assert view._row_of[0] == [0] and view._row_of[1] == [1, 2]
    window.grab()
    image = view.page_image(1, 1.0)
    assert image.pixelColor(60, 60) == QColor(0, 0, 255)  # annotations are part of the image
    assert "Traceback" not in capfd.readouterr().err


def test_the_bottom_bars_fit_a_narrow_window(window, workspace):
    """At the window's least width, with the Pages panel open, the zoom bar folds what it must into a menu and
    neither bar covers the other or leaves the canvas."""
    canvas, zoom, undo = workspace.canvas, workspace.zoom_bar, workspace.canvas.bars["undo"]
    workspace.side.show_panel("pages")
    try:
        for width in (960, 820, 720):
            window.resize(width, 800)
            QApplication.processEvents()
            assert not zoom.geometry().intersects(undo.geometry()), width
            assert canvas.rect().contains(zoom.geometry()) and canvas.rect().contains(undo.geometry()), width
        assert zoom.more_button.isVisible()
        assert zoom.more_button.size() == zoom.reading_button.size()  # a square like the others on the bar
        menu = zoom.more_button.menu()
        menu.aboutToShow.emit()
        texts = menu_texts(menu)
        assert texts[:2] == ["Page layout", "  Single page"] and "Page colours" in texts
        assert "Snap to annotations" in texts
    finally:
        window.resize(1200, 900)
        QApplication.processEvents()
    assert not zoom.more_button.isVisible() and zoom.snap_button.isVisible()  # wide again: back on the bar
