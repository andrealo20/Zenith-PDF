"""Selection bar, glass bars, the new-file panel and the Doc Builder, end to end."""

import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core import builder as core
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui.doc_builder import DocBuilder
from zenith_pdf.ui.new_file import NewFilePanel


def settle(ms=60):
    QTest.qWait(ms)
    QApplication.processEvents()


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_selection_bar_follows_the_selection(workspace):
    view = workspace.view
    shapes = [Shape(page=0, rect=(100, 300 + 10 * i, 120, 80)) for i in range(2)]
    view.add_annotations(shapes, "Shapes")
    view.reveal_annotation(shapes[0].id)
    settle()
    bar = workspace.selection_bar
    assert bar.isVisible() and bar.count.text() == "1"
    shape_on_screen = view.selection_rect()
    assert bar.geometry().bottom() < shape_on_screen.top() or bar.geometry().top() > shape_on_screen.bottom()

    view.arrange("front")
    assert workspace.store.order()[-1] == shapes[0].id
    view.arrange("back")
    assert workspace.store.order()[0] == shapes[0].id

    view.toggle_lock()
    assert workspace.store.get(shapes[0].id).locked and bar.lock.text() == "Unlock"
    view.delete_selected()  # locked: stays
    assert shapes[0].id in workspace.store
    view.toggle_lock()

    view.duplicate_selected()
    assert len(workspace.store) == 3
    assert view.apply_to_pages([0, 1, 2]) == 2  # the duplicate, copied to pages 2 and 3
    workspace.undo_stack.undo()
    workspace.undo_stack.undo()
    view.scene().clearSelection()
    settle()
    assert not bar.isVisible()


def test_flatten_and_library(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(200, 200, 60, 60), fill=(0, 0, 1))
    view.add_annotations([shape], "Shape")
    view.reveal_annotation(shape.id)
    workspace.library.add("Blue square", [shape])
    workspace.settings.stop_asking("flatten")  # no one to answer the question in a test
    workspace.flatten_selected()
    assert shape.id not in workspace.store
    assert workspace.library_panel.list.count() >= 1

    workspace.place_from_library(workspace.library.entries()[-1]["records"])
    placed = [a for a in workspace.store.all() if isinstance(a, Shape)]
    assert len(placed) == 1 and placed[0].fill == (0, 0, 1) and placed[0].id != shape.id
    workspace.undo_stack.undo()  # the placement
    workspace.undo_stack.undo()  # the flattening: the original is back
    assert shape.id in workspace.store


def test_glass_bars_sample_what_is_behind(workspace):
    workspace.settings.set("glass", "clear")  # whatever Windows' transparency effects say on this machine
    settle(150)
    workspace.view.verticalScrollBar().setValue(workspace.view.verticalScrollBar().value() + 40)
    settle(150)
    for bar in (workspace.toolbar, workspace.page_counter, workspace.zoom_bar):
        assert bar._backdrop is not None and not bar._backdrop.isNull()
        assert bar.graphicsEffect() is None and bar._shadow is not None and bar._shadow.isVisible()
    workspace.settings.set("glass", "solid")
    assert workspace.toolbar._backdrop is None  # solid bars, sampled no more
    workspace.settings.set("glass", "clear")
    assert workspace.toolbar._backdrop is not None


def test_new_file_panel_and_documents_from_templates(window, tmp_path):
    window._settings.last_directory = str(tmp_path)
    from PySide6.QtWidgets import QLabel

    panel = NewFilePanel(window._settings, window)
    texts = {label.text() for label in panel.findChildren(QLabel)}
    for expected in ("Open from device", "New document", "Study notes", "Lecture notes", "New template",
                     "Doc Builder", "Pin a folder…"):
        assert expected in texts
    template = window._settings.templates[0]
    window.create_document(template)
    workspace = window.current_workspace()
    assert workspace.document.unsaved and workspace.dirty
    assert workspace.document.page_count == template["pages"]
    assert workspace.document.path.parent == tmp_path and not workspace.document.path.exists()
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_doc_builder_combines_and_builds(window, sample_pdf, tmp_path):
    other = tmp_path / "other.pdf"
    shutil.copy(sample_pdf, other)
    window.open_builder()
    builder = window.stack.currentWidget()
    assert isinstance(builder, DocBuilder)
    builder.add_files([str(sample_pdf), str(other)])
    assert len(builder.entries) == 8 and len(builder.model.rows) == 2  # one card per file

    builder.move_rows([1], 0)  # second file first
    assert builder.entries[0].source == builder.order[1]
    builder.toggle_group(builder.model.rows[0])
    assert len(builder.model.rows) == 5  # first file expanded into four pages
    assert len(builder.selected_entries()) == 4  # moved pages stay selected, through the expansion
    selection = builder.grid.selectionModel()
    selection.select(builder.model.index(0), selection.SelectionFlag.ClearAndSelect)
    builder.rotate_selected(90)
    assert builder.selected_entries() == [0]  # still selected after the rotation
    builder.delete_selected()
    assert len(builder.entries) == 7
    builder.insert_blank()
    assert builder.entries[-1].source is None
    builder.undo_stack.undo()
    builder.undo_stack.undo()
    assert len(builder.entries) == 8 and builder.entries[0].rotation == 90

    result = tmp_path / "combined.pdf"
    result.write_bytes(core.build(builder._sources(), builder.entries))
    combined = Document(result)
    assert combined.page_count == 8 and combined.geometry(0).rotation == 90
    combined.close()
    window.close_tab(window.workspaces.index(builder))
