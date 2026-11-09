"""The chrome of the page canvas: the shadow and edge of a page, what marks a selection, a hover and a guide, the colour
of selected text, a page's content fading in, and the lit fields of a form. All of it reads the theme."""

import itertools
import math
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsScene

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import FreeText, Shape
from zenith_pdf.core.document import SearchHit
from zenith_pdf.settings import Settings
from zenith_pdf.theme import DARK, LIGHT, luminance, themes
from zenith_pdf.ui.annotation_items import SEPIA, Handle, page_accent
from zenith_pdf.ui.commands import ChangeAnnotations
from zenith_pdf.ui.document_view import PageItem, frame_picture
from zenith_pdf.ui.form_layer import FieldItem
from zenith_pdf.ui.tiles import Tile, TileCache, scale_key

from conftest import settle, wait_for

pytestmark = pytest.mark.usefixtures("qt_app")


def contrast(a: QColor, b: QColor) -> float:
    first, second = luminance(a), luminance(b)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


@pytest.fixture
def theme():
    """A theme set for one test, put back after it: theme(a_theme), or theme("neon", "dark", "pink") to choose."""
    before = themes.current, themes.style, themes._choice

    def use(chosen, scheme="dark", accent="blue"):
        if isinstance(chosen, str):
            themes.choose(scheme, accent, chosen)
        else:
            themes.apply(chosen)

    yield use
    themes.style, themes._choice = before[1], before[2]
    themes.apply(before[0])


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


# The accent as it reads on a page -----------------------------------------------------------------------------------


def test_the_accent_on_a_page_is_the_palettes_made_for_it_and_holds_its_contrast(theme):
    white, black = QColor("white"), QColor("black")
    for scheme in ("dark", "light"):
        theme("zenith", scheme)  # a page is white in either: marked alike, in the light palette's blue
        assert page_accent().name() == LIGHT.accent and page_accent(inverted=True).name() == DARK.accent
    theme("material", "dark")  # whose dark accent is a pastel for dark chrome, and would fade on white
    assert contrast(QColor(themes.current.accent), white) < 3 <= contrast(page_accent(), white)
    assert page_accent().name() == themes.tone("light").accent
    theme("neon", "dark")  # which has no light palette: its cyan is darkened until it holds against white
    cyan = QColor(themes.current.accent)
    assert contrast(cyan, white) < 3 <= contrast(page_accent(), white) and abs(page_accent().hue() - cyan.hue()) <= 3
    assert page_accent(inverted=True).name() == cyan.name() and contrast(cyan, black) >= 3  # on black it is as it is
    theme(replace(themes.current, accent="#102a55"))  # one too dark for an inverted page is lightened
    assert contrast(page_accent(inverted=True), black) >= 3


def test_the_accent_on_a_sepia_page_holds_its_contrast_against_that_paper(theme):
    for style in ("zenith", "dot", "neon", "material"):
        for accent in ("blue", "salmon", "green"):
            theme(style, "dark", accent)
            assert contrast(page_accent(sepia=True), SEPIA) >= 3, (style, accent)
            assert contrast(page_accent(), QColor("white")) >= 3  # and the white page keeps what it had
    theme("neon", "dark")  # whose cyan only just holds on white: sepia is the less bright paper
    assert luminance(page_accent(sepia=True)) < luminance(page_accent())


# The shadow and the edge of a page ----------------------------------------------------------------------------------


def paint_alone(page: PageItem, margin: int = 24) -> QImage:
    """The page drawn on its own at 100%, on a canvas of one colour."""
    scene = QGraphicsScene()
    scene.addItem(page)
    box = QRectF(-margin, -margin, page.rect.width() + 2 * margin, page.rect.height() + 2 * margin)
    image = QImage(int(box.width()), int(box.height()), QImage.Format.Format_RGB32)
    image.fill(QColor(220, 221, 226))
    painter = QPainter(image)
    scene.render(painter, QRectF(image.rect()), box)
    painter.end()
    scene.removeItem(page)  # the scene would delete it: it is drawn again
    return image


def test_the_shadow_of_a_page_falls_off_smoothly_and_a_little_lower(theme):
    theme(LIGHT)
    image = paint_alone(PageItem(0, 100, 140, TileCache(2**20)))
    canvas, left = image.pixelColor(2, 90).lightness(), 24  # the page starts at column 24 and row 24
    row = [image.pixelColor(x, 24 + 70).lightness() for x in range(left - 1, left - 9, -1)]
    assert row[0] < canvas and row == sorted(row) and row[-1] == canvas  # darkest at the edge, none beyond its reach
    assert len({*row}) >= 5  # many steps, not a hard line
    above, below = image.pixelColor(24 + 50, 24 - 1).lightness(), image.pixelColor(24 + 50, 24 + 140).lightness()
    assert below < above < canvas  # lighter above the page than below it
    assert image.pixelColor(24 + 50, 24 + 70).name() == "#ffffff"  # and under the page nothing shows


def test_a_style_without_a_shadow_has_its_hairline_and_one_that_glows_glows_in_its_accent(theme):
    from zenith_pdf.styles import STYLES

    canvas = QColor(220, 221, 226)
    for key in ("dot", "neon"):
        theme(STYLES[key].palette("dark"))
        image = paint_alone(PageItem(0, 100, 140, TileCache(2**20)))
        edge, past, far = (image.pixelColor(24 - x, 24 + 70) for x in (1, 3, 9))
        assert edge != canvas and far == canvas  # a hairline round the page, whichever it is
        if key == "dot":
            assert past == canvas  # and nothing more
        else:
            assert past != canvas and past.blue() > past.red()  # the cyan of Neon's accent over the grey


def test_the_frame_of_a_page_is_one_picture_cut_in_nine_and_made_once_for_a_size():
    first = frame_picture(6, 1, "soft", 0x29000000, 0)
    assert first is frame_picture(6, 1, "soft", 0x29000000, 0)
    picture, lift, margin = first
    assert picture.width() == picture.height() == 13 and lift == 1 and margin == 6
    assert frame_picture(6, 1, "glow", 0x5a2ec4ff, 0)[1] == 0  # a glow spreads evenly all round
    assert frame_picture(3, 1, "soft", 0x29000000, 0)[1] == 0  # a page seen from far has nothing to fall
    assert frame_picture(0, 2, "", 0, 0x28ffffff)[2] == 2  # only a hairline, at 200%: two pixels


def paint_tiled(scale: float, ratio: float, tint: str, place: float) -> QImage:
    """A page of 100 x 140 points and the one tile it has at `scale`, as the tiles are cut (a pixel more than the
    page where the scale rounds up), on a canvas of one colour. `place` is how far past a whole pixel it sits."""
    device = scale * ratio
    width, height = math.ceil(100 * device), math.ceil(140 * device)
    picture = QPixmap(width, height)
    picture.fill(Qt.GlobalColor.white)
    cache = TileCache(2**24)
    cell = QRect(0, 0, width, height)
    cache.put(0, scale_key(device), 0, 0, Tile(picture, device, cell, cell))
    page = PageItem(0, 100, 140, cache, tint)
    image = QImage(width + 60, height + 60, QImage.Format.Format_RGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(QColor(220, 221, 226))
    painter = QPainter(image)
    painter.setWorldTransform(QTransform.fromScale(scale, scale) * QTransform.fromTranslate((30 + place) / ratio,
                                                                                            (30 + place) / ratio))
    page.paint(painter, SimpleNamespace(exposedRect=page.rect))
    painter.end()
    return image


@pytest.mark.parametrize("ratio", (1.0, 1.25))
def test_the_edge_of_a_page_hugs_its_paper_at_any_scale_and_tint(theme, ratio):
    from zenith_pdf.styles import STYLES

    theme(STYLES["dot"].palette("light"))  # a hairline and no shadow: a gap shows as the canvas, and a sliver as white
    canvas = QColor(220, 221, 226).rgb()
    for tint, paper in (("normal", 0xFFFFFFFF), ("dark", 0xFF000000), ("sepia", SEPIA.rgb())):
        for scale in (1.0, 1.0733, 1.2111, 1.3333, 1.5067, 2.2):
            for place in (0.0, 0.5):  # and half a pixel: where Qt rounds up and round() goes to even
                image = paint_tiled(scale, ratio, tint, place)
                row = [image.pixel(x, image.height() // 2) for x in range(image.width())]
                column = [image.pixel(image.width() // 2, y) for y in range(image.height())]
                for line in (row, column):
                    first = last = len(line) // 2
                    assert line[first] == paper
                    while line[first - 1] == paper:
                        first -= 1
                    while line[last + 1] == paper:
                        last += 1
                    for outside in (line[first - 1], line[last + 1]):  # the hairline, right against the paper
                        assert outside not in (canvas, 0xFFFFFFFF), (tint, scale, place, hex(outside))


# Selection, hover and handles ---------------------------------------------------------------------------------------


def test_hovering_marks_what_a_click_would_pick_and_only_one_at_a_time(workspace):
    view = workspace.view
    one, two = Shape(page=0, rect=(100, 300, 80, 60)), FreeText(page=0, rect=(300, 300, 120, 40), contents="x")
    view.add_annotations([one, two], "Two")
    workspace.tool_settings.set_tool("select")
    first, second = view._items[one.id], view._items[two.id]
    view._mark(first)
    assert first.hover == 1.0 and second.hover == 0.0  # motion is off in the suite: the mark is there at once
    view._mark(second)
    assert first.hover == 0.0 and second.hover == 1.0
    view._mark(None)
    assert second.hover == 0.0 and view._hot is None
    point = view.mapFromScene(view._pages[0].mapToScene(QPointF(140, 300)))  # on its border: it has no fill
    view._update_hover_cursor(QPointF(point))  # what the cursor logic says it is on
    assert view._hot is first
    workspace.tool_settings.set_tool("pen")  # another tool: nothing is picked by a click
    assert view._hot is None and first.hover == 0.0


def test_a_hover_mark_eases_in_when_motion_is_on(workspace, motion_on):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 80, 60))
    view.add_annotations([shape], "Shape")
    item = view._items[shape.id]
    view._mark(item)
    assert 0.0 <= item.hover < 1.0  # on its way
    assert wait_for(lambda: item.hover == 1.0, timeout=2)
    view._mark(None)
    assert item.hover == 0.0  # and gone at once


def test_a_hover_mark_goes_when_the_pages_move_under_the_pointer(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 80, 60))
    view.add_annotations([shape], "Shape")
    item = view._items[shape.id]
    bar = view.verticalScrollBar()
    for move in (lambda: bar.setValue(bar.value() + 40), lambda: view.set_zoom(view.zoom * 1.5)):
        view._mark(item)
        assert view._hot is item and item.hover == 1.0
        move()  # a wheel notch or a zoom step: what the pointer is on is not known until it moves again
        assert view._hot is None and item.hover == 0.0


def test_a_hover_mark_goes_when_the_annotation_leaves_a_pointer_that_rests(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 80, 60))
    view.add_annotations([shape], "Shape")
    item = view._items[shape.id]
    view._mark(item)
    view.undo_stack.push(ChangeAnnotations(view.store, [shape], [replace(shape, rect=(100, 450, 80, 60))], "Move"))
    assert view._hot is None and item.hover == 0.0  # the pointer did not move: what it is on is not known


def test_hover_marks_and_handles_paint_without_errors_in_every_state(workspace, capfd, theme):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100))
    view.add_annotations([shape], "Rectangle")
    workspace.tool_settings.set_tool("select")
    item = view._items[shape.id]
    view._mark(item)
    view.viewport().grab()
    view.select([shape])
    handle = next(i for i in item.childItems() if isinstance(i, Handle))
    for state in ((False, False), (True, False), (True, True)):
        handle._under, handle._pressed = state
        handle.recolour()
        view.viewport().grab()
    style = replace(themes.current, look=replace(themes.current.look, chamfer=True))
    theme(style)  # a style with chamfered corners cuts them from the handle
    handle._under = handle._pressed = False
    view.select([])
    view.select([shape])  # new handles in a new style
    view.viewport().grab()
    assert "Traceback" not in capfd.readouterr().err


def paint_handle(handle: Handle) -> QImage:
    image = QImage(40, 40, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.translate(20, 20)
    handle.paint(painter, None)
    painter.end()
    return image


def test_a_handle_swells_under_the_pointer_and_fills_with_the_accent_when_pressed(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100))
    view.add_annotations([shape], "Rectangle")
    workspace.tool_settings.set_tool("select")
    view.select([shape])
    handle = next(i for i in view._items[shape.id].childItems() if isinstance(i, Handle))
    accent = view._items[shape.id].accent()
    at_rest = paint_handle(handle)
    assert at_rest.pixelColor(20, 20) == QColor("white") and at_rest.pixelColor(20, 20 + 6).alpha() == 0
    handle.hoverEnterEvent(None)
    hovered = paint_handle(handle)
    assert hovered.pixelColor(20, 20) not in (QColor("white"), accent)  # a tint of the accent
    assert hovered.pixelColor(20, 20 + 6).alpha() > 0  # and larger than at rest
    handle.hoverLeaveEvent(None)
    assert paint_handle(handle) == at_rest
    handle._pressed = True
    handle.recolour()
    assert paint_handle(handle).pixelColor(20, 20) == accent


def test_a_selection_stands_in_a_halo_of_the_accent(workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100), color=(1.0, 1.0, 1.0))  # white on white: only the selection shows
    view.add_annotations([shape], "Rectangle")
    workspace.tool_settings.set_tool("select")
    view.reveal_annotation(shape.id)
    settle(100)
    image = view.viewport().grab().toImage()
    edge = view.mapFromScene(view._pages[0].mapToScene(QPointF(140, 300)))  # on the top edge, clear of the handles
    column = [image.pixelColor(edge.x(), edge.y() + dy) for dy in range(-3, 4)]
    line = min(column, key=lambda c: c.lightness())
    assert abs(line.red() - page_accent().red()) < 30 and abs(line.blue() - page_accent().blue()) < 30  # the hairline
    halo = [c for c in column if c != line and c != QColor("white")]
    assert len(halo) >= 2 and all(line.lightness() < c.lightness() < 255 for c in halo)  # and a faint band round it


@pytest.mark.parametrize("ratio", (1.25, 1.5, 1.75))
def test_the_four_sides_of_a_selection_are_as_thick_at_any_display_scale(workspace, ratio):
    view = workspace.view
    shape = Shape(page=0, rect=(10, 10, 60, 40))
    view.add_annotations([shape], "Rectangle")
    view.select([shape])
    item = view._items[shape.id]
    accent = item.accent().rgb() & 0xFFFFFF
    for scale, place in itertools.product((1.31, 1.7, 2.13), (0.0, 0.3, 0.6, 0.85)):  # zoom, and where the page sits
        image = QImage(int(200 * ratio), int(140 * ratio), QImage.Format.Format_RGB32)  # 200 x 140 logical pixels
        image.setDevicePixelRatio(ratio)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setWorldTransform(QTransform.fromScale(scale, scale) * QTransform.fromTranslate(place, place))
        item._paint_selection(painter)
        painter.end()
        middle = (int(35 * scale * ratio), int(30 * scale * ratio))  # the middle of the rect, in device pixels
        column = [image.pixel(middle[0], y) & 0xFFFFFF == accent for y in range(image.height())]
        row = [image.pixel(x, middle[1]) & 0xFFFFFF == accent for x in range(image.width())]
        sides = [sum(column[:middle[1]]), sum(column[middle[1]:]), sum(row[:middle[0]]), sum(row[middle[0]:])]
        assert sides[0] >= 1 and len(set(sides)) == 1, (ratio, scale, place, sides)


# The colour of selected text ----------------------------------------------------------------------------------------


def test_selected_text_takes_the_accent_as_it_reads_on_the_page(workspace, theme):
    view = workspace.view
    document = workspace.document
    first = document.char_at(0, (80, 95))
    view._select_text(0, first, first + 5)
    preview = view._text_selection[3]
    colour = preview.brush().color()
    assert (colour.red(), colour.green(), colour.blue()) == page_accent().getRgb()[:3] and colour.alpha() == 77
    view.set_page_tint("dark")  # an inverted page: the accent for a dark ground, and more of it
    assert preview.brush().color().alpha() == 100 and preview.brush().color().name() == page_accent(True).name()
    view.set_page_tint("normal")
    theme("zenith", "dark", "pink")  # a new accent: the selection already there follows it
    assert preview.brush().color().name() == page_accent().name() != colour.name()


def test_text_being_dragged_over_has_the_colour_the_selection_keeps(workspace):
    view = workspace.view
    tool, page = view._tools["select"], view._pages[0]
    tool.page, tool.preview = page, QGraphicsPathItem(page)  # a drag with Select that has just begun
    view._active = tool
    try:
        view._tint_text_selection()
        assert tool.preview.brush().color() == view.selection_colour()
    finally:
        view._active = None
        tool.cancel()


# A page's content fading in -----------------------------------------------------------------------------------------


def test_the_first_content_of_a_page_fades_in_and_a_second_picture_does_not(workspace, motion_on):
    view = workspace.view
    page = view._pages[2]
    view._entered.discard(2)
    page.reveal = 1.0
    view._bring_in(page)
    assert page.reveal < 0.5  # started from the white of the paper
    assert wait_for(lambda: page.reveal == 1.0, timeout=2)
    view._bring_in(page)
    assert page.reveal == 1.0  # the picture of a page already shown, drawn again: no fade, which would be a flash


def test_with_motion_off_a_page_has_its_content_at_once(workspace):
    view = workspace.view
    page = view._pages[3]
    view._entered.discard(3)
    view._bring_in(page)
    assert page.reveal == 1.0 and 3 in view._entered


def test_a_page_paints_its_content_as_far_as_it_has_come_out_of_the_white(theme):
    theme(LIGHT)
    page = PageItem(0, 100, 140, TileCache(2**20))
    black = QPixmap(10, 10)
    black.fill(QColor("black"))
    page.set_base(black)
    spots = {}
    for reveal in (0.0, 0.5, 1.0):
        page.reveal = reveal
        spots[reveal] = paint_alone(page).pixelColor(24 + 50, 24 + 70).lightness()
    assert spots[0.0] == 255 and 110 < spots[0.5] < 145 and spots[1.0] == 0


# Snapping guides ----------------------------------------------------------------------------------------------------


def test_guides_are_two_lines_moved_about_and_they_ease_out(workspace, motion_on):
    view, snapper = workspace.view, workspace.view.snapper
    anchor = Shape(page=0, rect=(100, 100, 80, 60))
    view.add_annotations([anchor], "Shape")
    view.settings.set("snap_annotations", True)
    snapper.snap_rect(0, (103, 300, 40, 40), set())
    line = snapper._lines["x"]
    assert snapper._guides == [line] and line.isVisible() and line.opacity() == 1.0
    assert line.pen().color().name() == page_accent().name() and line.pen().isCosmetic()
    snapper.snap_rect(0, (102, 310, 40, 40), set())
    assert snapper._lines["x"] is line  # not built again at every step of a drag
    snapper.hide()
    assert snapper._guides == []
    assert line.isVisible() and 0.0 < line.opacity() <= 1.0  # on its way out
    assert wait_for(lambda: not line.isVisible(), timeout=2)
    snapper.snap_rect(0, (103, 300, 40, 40), set())  # wanted again: back at full strength, the same line
    assert snapper._lines["x"] is line and line.isVisible() and line.opacity() == 1.0
    snapper.hide()
    view.settings.set("snap_annotations", False)


def test_guides_go_at_once_when_motion_is_off(workspace):
    view, snapper = workspace.view, workspace.view.snapper
    view.add_annotations([Shape(page=0, rect=(100, 100, 80, 60))], "Shape")
    view.settings.set("snap_annotations", True)
    snapper.snap_rect(0, (103, 300, 40, 40), set())
    line = snapper._lines["x"]
    snapper.hide()
    assert not line.isVisible()
    view.settings.set("snap_annotations", False)


# A search hit -------------------------------------------------------------------------------------------------------


def test_a_search_hit_is_one_rounded_shape_whose_pieces_do_not_darken_each_other(workspace):
    view = workspace.view
    hit = SearchHit(page=0, rects=((100, 100, 30, 12), (120, 100, 30, 12), (100, 120, 30, 12)), context="")
    view.add_search_hit(hit)
    [item] = view._hit_items[hit]  # the pieces overlap by 10 points: one shape, not three that darken each other
    assert isinstance(item, QGraphicsPathItem) and item.brush().color() == themes.current.search_hit
    assert item.path().contains(QPointF(125, 106)) and item.path().contains(QPointF(110, 126))  # all of it is in
    assert not item.path().contains(QPointF(140, 126))  # and nothing else
    view.focus_search_hit(hit)
    assert item.brush().color() == themes.current.search_current
    view.clear_search_hits()


# The zoom glide is motion -------------------------------------------------------------------------------------------


def test_with_motion_off_a_zoom_step_lands_at_once(workspace):
    view = workspace.view
    view.set_zoom(1.0)
    view.zoom_in()
    assert view.zoom > 1.0 and view.zoom == view.zoom_target()
    assert view.glide.state() != view.glide.State.Running


# The lit fields of a form -------------------------------------------------------------------------------------------


@pytest.fixture
def form(qt_app, tmp_path, form_pdf):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 1000)
    window.show()
    QTest.qWaitForWindowExposed(window)
    path = tmp_path / "form.pdf"
    shutil.copy(form_pdf, path)
    window.open_file(path)
    QTest.qWait(60)
    workspace = window.current_workspace()
    workspace.tool_settings.set_tool("select")
    yield workspace
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def field(view, name, which=0) -> FieldItem:
    return [i for i in view.forms.items if i.field.name == name][which]


def paint_field(item: FieldItem) -> QImage:
    rect = item.rect()
    image = QImage(int(rect.width()) + 40, int(rect.height()) + 40, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.translate(20 - rect.x(), 20 - rect.y())
    item.paint(painter, None)
    painter.end()
    return image


def test_a_field_is_lit_brighter_under_the_pointer_and_ringed_while_typed_in(form, motion_on, capfd):
    view = form.view
    item = field(view, "name")
    inside = lambda image: image.pixelColor(30 + 100, 30 + 8)
    rest = inside(paint_field(item))
    assert rest != QColor("white")  # lit
    item.hoverEnterEvent(None)
    assert wait_for(lambda: item._glow is not None and item._glow.value == 1.0, timeout=2)
    assert inside(paint_field(item)).lightness() < rest.lightness()  # brighter light, darker tint
    item.hoverLeaveEvent(None)
    assert wait_for(lambda: item._glow.value == 0.0, timeout=2)
    assert inside(paint_field(item)) == rest
    outside = lambda image: image.pixelColor(20 - 2, 20 + 8)  # two pixels left of the field
    before = outside(paint_field(item))
    view.forms.edit(item)
    assert item.editing and outside(paint_field(item)) != before  # the ring
    view.forms.cancel()
    assert not item.editing and outside(paint_field(item)) == before
    assert "Traceback" not in capfd.readouterr().err


def test_only_the_field_is_hit_not_the_room_its_ring_takes(form):
    view = form.view
    item = field(view, "name")
    rect = item.rect()
    beside = QPointF(rect.left() - 3, rect.center().y())
    assert item.shape().contains(rect.center()) and not item.shape().contains(beside)
    inside = view.mapFromScene(item.mapToScene(rect.center()))
    assert view.forms.item_at(QPointF(inside)) is item
    just_out = view.mapFromScene(item.mapToScene(QPointF(rect.left() - 4, rect.center().y())))
    assert view.forms.item_at(QPointF(just_out)) is None


def test_an_annotation_over_a_field_is_not_marked_as_what_a_press_picks(form):
    view = form.view
    rect = field(view, "name").rect()
    box = (rect.x() - 10, rect.y() - 6, rect.width() + 20, rect.height() + 12)
    cover = Shape(page=0, rect=box, fill=(1.0, 0.95, 0.4))
    view.add_annotations([cover], "Shape")
    over = lambda x, y: QPointF(view.mapFromScene(view._pages[0].mapToScene(QPointF(x, y))))
    view._update_hover_cursor(over(rect.center().x(), rect.center().y()))  # a press here fills the field in
    assert view._hot is None
    view._update_hover_cursor(over(rect.x() - 5, rect.center().y()))  # beside the field, on what covers it
    assert view._hot is view._items[cover.id]


def test_a_round_field_is_lit_round_and_the_editor_takes_the_corners_of_the_style(form):
    view = form.view
    radio = field(view, "size", 0)
    image = paint_field(radio)
    middle = QPointF(radio.rect().width() / 2, radio.rect().height() / 2).toPoint()
    assert image.pixelColor(20 + 1, 20 + 1) == QColor("white")  # the corner of the box: round, so not lit
    assert image.pixelColor(20 + middle.x(), 20 + middle.y()) != QColor("white")
    view.forms.edit(field(view, "name"))
    editor = view.forms._editing[2]
    assert "border-radius" in editor.styleSheet() and page_accent().name() in editor.styleSheet()
    view.forms.cancel()


def test_a_form_still_works_as_before(form, monkeypatch):
    from zenith_pdf.ui.form_layer import FormLayer

    view = form.view
    item = field(view, "agree")
    centre = item.mapToScene(item.rect().center())
    view.ensureVisible(item.mapRectToScene(item.rect()), 40, 40)
    settle(50)
    at = view.mapFromScene(centre)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    assert {f.name: f.value for f in form.document.form_fields()}["agree"] is True
    blue = staticmethod(lambda menu, where: next(a for a in menu.actions() if a.text() == "Blue"))
    monkeypatch.setattr(FormLayer, "run_menu", blue)
    view.forms._choose(field(view, "colour"))
    assert {f.name: f.value for f in form.document.form_fields()}["colour"] == "Blue"
