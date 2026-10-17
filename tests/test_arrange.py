"""Lining annotations up, spacing them out and flipping them, from the selection bar's Arrange menu."""

import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core import arrange, images
from zenith_pdf.core.annotations import FreeText, ImageStamp, Ink, Line, Shape, TextMarkup, union
from zenith_pdf.settings import Settings

pytestmark = pytest.mark.usefixtures("qt_app")


def boxes(*rects, **fields):
    return [Shape(page=0, rect=rect, **fields) for rect in rects]


def test_lining_up_by_edges_and_middles():
    a, b, c = boxes((10, 10, 20, 20), (50, 40, 40, 10), (30, 80, 10, 30))
    by_id = lambda moved: {m.id: m.rect for m in moved}
    assert by_id(arrange.aligned([a, b, c], "left")) == {b.id: (10, 40, 40, 10), c.id: (10, 80, 10, 30)}
    assert by_id(arrange.aligned([a, b, c], "right"))[a.id] == (70, 10, 20, 20)
    assert by_id(arrange.aligned([a, b, c], "middle"))[a.id] == (10, 50, 20, 20)  # the middle of 10 to 110
    assert arrange.aligned([a], "left") == []  # one alone has nothing to line up with


def test_a_group_lines_up_as_one_and_locked_ones_stay():
    a, b = boxes((10, 10, 20, 20), (40, 10, 20, 20), group="g")
    c, = boxes((100, 50, 20, 20))
    locked, = boxes((0, 200, 10, 10), locked=True)
    moved = {m.id: m.rect for m in arrange.aligned([a, b, c, locked], "right")}
    assert moved == {a.id: (70, 10, 20, 20), b.id: (100, 10, 20, 20)}  # the group's right edge, not each one's


def test_spacing_three_or_more_evenly():
    a, b, c, d = boxes((0, 0, 10, 10), (15, 0, 30, 10), (70, 0, 10, 10), (100, 0, 20, 10))
    moved = {m.id: m.rect for m in arrange.distributed([d, b, a, c], "h")}
    # 120 wide, 70 of boxes: three gaps of 50 / 3.
    gap = 50 / 3
    assert moved[b.id][0] == pytest.approx(10 + gap) and moved[c.id][0] == pytest.approx(10 + gap + 30 + gap)
    assert a.id not in moved and d.id not in moved
    assert arrange.distributed([a, b], "h") == []


def test_flipping_mirrors_shapes_but_keeps_text_readable():
    ink = Ink(page=0, strokes=(((10, 10), (30, 20)),))
    line = Line(page=0, start=(10, 50), end=(50, 60))
    text = FreeText(page=0, rect=(40, 0, 20, 10), contents="Hi")
    mark = TextMarkup(page=0, quads=((0, 0, 50, 10),))
    picture = QImage(2, 1, QImage.Format.Format_RGB32)
    picture.setPixelColor(0, 0, QColor("red"))
    picture.setPixelColor(1, 0, QColor("blue"))
    image = ImageStamp(page=0, rect=(10, 70, 20, 10), data=images.encode(picture))
    flipped = {m.id: m for m in arrange.flipped([ink, line, text, mark, image], "h")}
    x, _, w, _ = union(a.bounds() for a in (ink, line, text, image))
    twice = lambda value: 2 * (x + w / 2) - value  # mirrored about the middle of all of them
    assert flipped[ink.id].strokes == (((twice(10), 10), (twice(30), 20)),)
    assert flipped[line.id].start == (twice(10), 50) and flipped[line.id].end == (twice(50), 60)
    assert flipped[text.id].rect == (twice(60), 0, 20, 10) and flipped[text.id].contents == "Hi"
    assert mark.id not in flipped  # it belongs to the text under it
    mirror = images.decoded(flipped[image.id].data)
    assert mirror.pixelColor(0, 0) == QColor("blue") and flipped[image.id].rect == (twice(30), 70, 20, 10)
    back = {m.id: m for m in arrange.flipped(list(flipped.values()), "h")}
    assert [c for p in back[ink.id].strokes[0] for c in p] == pytest.approx([10, 10, 30, 20])


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


def test_the_arrange_menu_aligns_and_undo_puts_them_back(workspace):
    view, store = workspace.view, workspace.store
    shapes = boxes((100, 100, 40, 40), (200, 150, 40, 40), (300, 120, 20, 20))
    view.add_annotations(shapes, "Shapes")
    view.select(shapes)
    QTest.qWait(30)
    menu = {a.text(): a for a in workspace.selection_bar.arrange.menu().actions()}
    assert menu["Align"].isEnabled() and menu["Distribute"].isEnabled() and menu["Flip"].isEnabled()
    top = next(a for a in menu["Align"].menu().actions() if a.text() == "Top")
    top.trigger()
    assert {a.rect[1] for a in store.all() if isinstance(a, Shape)} == {100}
    workspace.undo_stack.undo()
    assert sorted(a.rect[1] for a in store.all() if isinstance(a, Shape)) == [100, 120, 150]

    view.select(shapes[:2])
    QTest.qWait(30)
    assert not menu["Distribute"].isEnabled()  # two have no gap between them to even out


def test_hatched_fills_are_drawn_alike_on_screen_and_in_the_pdf(tmp_path, sample_pdf):
    import pypdfium2 as pdfium
    from PySide6.QtGui import QPainter

    from zenith_pdf.core.annotations import Poly, pattern_marks
    from zenith_pdf.core.document import Document
    from zenith_pdf.ui.annotation_items import paint_annotation

    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    square = Shape(page=0, rect=(100, 300, 120, 90), fill=(0.0, 0.0, 1.0), color=(1.0, 0.0, 0.0), pattern="diagonal")
    star = Poly(page=0, points=((300, 300), (420, 300), (360, 400)), closed=True, fill=(0.0, 0.6, 0.0),
                pattern="dots")
    lines, dots = pattern_marks(square.rect, "cross")
    assert len(lines) > 2 * (120 + 90) / 6 and not dots
    document = Document(path)
    document.save([square, star])
    document.close()
    reopened = Document(path)
    assert {a.pattern for a in reopened.annotations} == {"diagonal", "dots"}
    reopened.close()

    pdf = pdfium.PdfDocument(str(path))
    page = pdf[0]
    rendered = page.render(scale=1, draw_annots=True).to_pil().convert("RGB")
    pdf.close()
    screen = QImage(rendered.width, rendered.height, QImage.Format.Format_RGB32)
    screen.fill(QColor("white"))
    painter = QPainter(screen)
    paint_annotation(painter, square)
    painter.end()

    def blue(image_pixel) -> bool:
        r, g, b = image_pixel
        return b > 150 and r < 120 and g < 120

    inside = [(x, y) for x in range(110, 210) for y in range(310, 380)]
    pdf_blue = {p for p in inside if blue(rendered.getpixel(p))}
    screen_blue = {p for p in inside if blue(screen.pixelColor(*p).getRgb()[:3])}
    assert 0.1 * len(inside) < len(pdf_blue) < 0.6 * len(inside)  # hatched: lines with white between
    assert len(pdf_blue ^ screen_blue) < 0.12 * len(inside)  # the same lines in both
