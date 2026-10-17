"""How the pages show: hiding annotations, lines over the pages, layers, locked zoom, fit height, read only and
touch mode."""

import shutil

import pikepdf
import pytest
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui.document_view import ZOOM_GLIDE_MS

pytestmark = pytest.mark.usefixtures("qt_app")


def layered_pdf(path):
    """One page with a red square in a layer called "Notes", shown by default."""
    pdf = pikepdf.new()
    group = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String("Notes")))
    page = pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 300, 300],
                                           Resources=pikepdf.Dictionary(Properties=pikepdf.Dictionary(oc1=group))))
    page.obj.Contents = pdf.make_stream(b"/OC /oc1 BDC 1 0 0 rg 50 50 200 200 re f EMC")
    pdf.pages.append(page)
    pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=[group], D=pikepdf.Dictionary(Order=[group], ON=[group]))
    pdf.save(path)


def red_share(image) -> float:
    reds = sum(1 for x in range(0, image.width(), 5) for y in range(0, image.height(), 5)
               if image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 80)
    return reds / ((image.width() // 5 + 1) * (image.height() // 5 + 1))


def test_layers_show_and_hide_on_screen_and_the_file_keeps_its_own(tmp_path):
    path = tmp_path / "layered.pdf"
    layered_pdf(path)
    document = Document(path)
    assert document.layers() == [("Notes", True)]
    assert red_share(document.render(0, 1.0)) > 0.3
    key = document.key
    document.set_layer_shown(0, False)
    assert document.layers() == [("Notes", False)] and document.key != key
    assert red_share(document.render(0, 1.0)) == 0
    document.save([])  # saving keeps the file's own visibility
    document.close()
    with pikepdf.open(path) as pdf:
        assert list(pdf.Root.OCProperties.D.get("/OFF", [])) == []
    again = Document(path)
    assert again.layers() == [("Notes", True)]
    again.close()


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


def test_hiding_annotations_and_showing_them_again(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 100, 50, 50))
    view.add_annotations([shape], "Rectangle")
    view.select([shape])
    view.show_annotations(False)
    assert not view._items[shape.id].isVisible() and not view.selected_annotations()
    workspace.tool_settings.set_tool("hand")  # a tool that changes nothing: still hidden
    assert not view._items[shape.id].isVisible()
    workspace.tool_settings.set_tool("rectangle")  # one that draws: they show again
    assert view._items[shape.id].isVisible() and view.annotations_shown
    workspace.tool_settings.set_tool("select")


def test_lines_over_the_pages_are_on_screen_only(workspace):
    view = workspace.view
    menu = workspace.zoom_bar.view_menu
    workspace.zoom_bar.fill_view_menu(menu)
    lines = next(a for a in menu.actions() if a.text() == "Lines over the pages").menu()
    next(a for a in lines.actions() if a.text() == "Grid").trigger()
    assert view.overlay == "grid" and all(page.overlay == "grid" for page in view._pages)
    view.reload()  # kept when the pages are laid out again
    assert all(page.overlay == "grid" for page in view._pages)
    image = view.area_image(0, (0, 0, 100, 100), 2.0, annotations=True)
    assert all(image.pixelColor(x, 40).blue() > 240 for x in range(0, 200, 7))  # the PDF's own picture: no lines
    view.set_overlay("none")


def test_a_locked_zoom_stays_and_fit_height_fits_the_page(workspace):
    view = workspace.view
    view.set_zoom(1.0)
    view.lock_zoom(True)
    told = []
    view.message.connect(told.append)
    view.zoom_in()
    view.fit_width()
    assert view.zoom == pytest.approx(1.0) and len(told) == 1  # told once, not at every try
    view.lock_zoom(False)
    view.fit_height()
    page = view._pages[view.current_page]
    shown = page.mapRectToScene(page.rect)
    height_px = view.mapFromScene(shown).boundingRect().height()
    assert height_px <= view.viewport().height() - view.top_inset and height_px > 0.8 * view.viewport().height() - \
        view.top_inset


def test_a_zoom_step_glides_and_keeps_the_point_under_the_pointer(workspace):
    view = workspace.view
    view.set_zoom(1.0)
    anchor = QPointF(view.viewport().width() / 3, view.viewport().height() / 3)
    under = view.mapToScene(anchor.toPoint())
    view._mouse = anchor
    view.zoom_in()
    view.zoom_in()  # while it glides: on from the level it was heading for
    assert view.zoom < 1.3 and view.zoom_target() == pytest.approx(1.3)
    QTest.qWait(ZOOM_GLIDE_MS + 150)
    assert view.zoom == pytest.approx(1.3)
    # Across, a page narrower than the window stays centred; down, the line under the pointer stays.
    assert abs((view.mapFromScene(under) - anchor.toPoint()).y()) <= 2


def test_read_only_turns_the_tools_that_change_things_off(workspace):
    view, document = workspace.view, workspace.document
    workspace.tool_settings.set_tool("rectangle")
    workspace.set_view_only(True)
    assert document.read_only and document.can_save and workspace.tool_settings.tool == "select"
    assert not workspace.toolbar._buttons["rectangle"].isEnabled()
    workspace.tool_settings.set_tool("rectangle")
    assert view._tool() is None  # a tool that would draw does nothing
    shape = Shape(page=0, rect=(100, 100, 50, 50))
    workspace.store.add(shape)
    item = view._items[shape.id]
    item.setSelected(True)
    item.setPos(30, 30)  # dragged...
    view._commit_moves()
    assert item.pos().x() == 0 and workspace.store.get(shape.id).rect == shape.rect  # ...and back
    workspace.set_view_only(False)
    assert not document.read_only and workspace.toolbar._buttons["rectangle"].isEnabled()


def test_touch_mode_makes_handles_bigger(workspace):
    from zenith_pdf.ui.annotation_items import Handle

    view = workspace.view
    shape = Shape(page=0, rect=(100, 100, 50, 50))
    view.add_annotations([shape], "Rectangle")
    view.settings.set("touch_mode", True)
    view.select([shape])
    handle = next(i for i in view._items[shape.id].childItems() if isinstance(i, Handle))
    assert handle.rect().width() > Handle.SIZE
    view.settings.set("touch_mode", False)
    assert not Handle.touch
