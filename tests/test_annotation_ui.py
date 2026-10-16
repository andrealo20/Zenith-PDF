"""Annotation tools driven through real mouse and keyboard events."""

import shutil

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import FreeText, ImageStamp, Ink, Line, Note, Poly, Shape, TextMarkup
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 900)
    window.show()
    window.activateWindow()
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
    QTest.qWait(50)
    yield workspace
    window.tool_settings.set_tool("select")
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def at(view, page: int, x: float, y: float) -> QPoint:
    """Viewport pixel position of a point on a page, in points."""
    page_item = view._pages[page]
    return view.mapFromScene(page_item.mapToScene(QPointF(x, y)))


def drag(view, start: QPoint, end: QPoint, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    viewport = view.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, modifiers, start)
    for step in range(1, 6):
        QTest.mouseMove(viewport, start + (end - start) * step / 5)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, modifiers, end)


def test_rectangle_tool_with_undo_and_redo(window, workspace):
    view = workspace.view
    window.tool_settings.set_tool("rectangle")
    drag(view, at(view, 0, 100, 300), at(view, 0, 250, 400))

    [shape] = workspace.store.all()
    assert isinstance(shape, Shape)
    assert shape.rect == pytest.approx((100, 300, 150, 100), abs=2)
    assert workspace.dirty
    workspace.undo_stack.undo()
    assert len(workspace.store) == 0
    workspace.undo_stack.redo()
    assert len(workspace.store) == 1


def test_highlight_tool_marks_the_text_and_saves(window, workspace):
    view = workspace.view
    window.tool_settings.set_tool("highlight")
    drag(view, at(view, 0, 76, 95), at(view, 0, 170, 95))

    [markup] = workspace.store.all()
    assert isinstance(markup, TextMarkup)
    assert "Page 1 need" in workspace.annotations.marked_text(markup)

    workspace.save()
    assert not workspace.dirty
    reopened = Document(workspace.document.path)
    assert [a.id for a in reopened.annotations] == [markup.id]
    reopened.close()


def test_select_move_and_delete(window, workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100), color=(1, 0, 0))
    view.add_annotations([shape], "Rectangle")
    window.tool_settings.set_tool("select")

    drag(view, at(view, 0, 100, 350), at(view, 0, 150, 370))  # grab the left edge
    [moved] = workspace.store.all()
    assert moved.rect == pytest.approx((150, 320, 150, 100), abs=2)

    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert len(workspace.store) == 0


def test_every_kind_paints_without_errors(window, workspace, capfd):
    # PySide prints exceptions raised inside paint() instead of raising them, so check stderr.
    view = workspace.view
    first = workspace.document.char_at(0, (80, 95))
    rects, _ = workspace.document.text_span(0, first, first + 5)
    annotations = [
        TextMarkup(page=0, style=style, quads=rects, color=(1, 0.8, 0)) for style in ("Highlight", "Underline", "StrikeOut")
    ] + [
        Shape(page=0, rect=(100, 300, 150, 100), fill=(0, 0, 1)),
        Shape(page=0, shape="Circle", rect=(300, 300, 100, 100)),
        Line(page=0, start=(50, 450), end=(250, 520), head_start="OpenArrow", head_end="ClosedArrow"),
        Ink(page=0, strokes=(((60, 600), (90, 640), (130, 610)),)),
        FreeText(page=0, rect=(300, 450, 200, 60), contents="Testo di prova", fill=(1, 1, 0.8), border_width=1),
        Note(page=0, position=(500, 100), contents="nota"),
        # The styles added later: dashes, endings, highlight mode, no border, varying widths, turned boxes.
        Shape(page=0, rect=(60, 680, 80, 50), dash="dotted", highlight=True, fill=(1, 1, 0)),
        Shape(page=0, rect=(160, 680, 80, 50), border=False, fill=(0.2, 0.6, 1.0)),
        Line(page=0, start=(260, 700), end=(400, 720), head_start="Circle", head_end="RClosedArrow", dash="dashdot"),
        Poly(page=0, points=((420, 680), (470, 700), (520, 690)), head_end="Diamond", dash="dashed"),
        Ink(page=0, strokes=(((60, 750), (90, 760), (130, 745)),), width=4, widths=((1.0, 4.0, 2.0),)),
        FreeText(page=0, rect=(540, 300, 30, 160), contents="Δt → ≤ 3 Ω", rotation=1, border_width=1),
        ImageStamp(page=0, rect=(540, 480, 30, 60), data=_picture(), rotation=3),
        Note(page=0, position=(530, 100), icon="Help"),
        TextMarkup(page=0, style="Squiggly", quads=((20, 300, 12, 60),), rotation=1),
    ]
    view.add_annotations(annotations, "All kinds")
    window.tool_settings.set_tool("select")
    view.reveal_annotation(annotations[5].id)  # selected: also paints handles
    window.grab()
    for annotation in annotations[9:]:
        view.reveal_annotation(annotation.id)
        window.grab()
    assert "Traceback" not in capfd.readouterr().err


def _picture() -> bytes:
    from PySide6.QtGui import QColor, QImage

    from zenith_pdf.core import images

    image = QImage(20, 40, QImage.Format.Format_RGB32)
    image.fill(QColor(20, 160, 60))
    return images.encode(image)


def test_selection_shows_round_handles(window, workspace):
    from PySide6.QtWidgets import QGraphicsEllipseItem

    from zenith_pdf.ui.annotation_items import Handle

    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100))
    view.add_annotations([shape], "Rectangle")
    window.tool_settings.set_tool("select")
    view.reveal_annotation(shape.id)
    handles = [item for item in view.scene().items() if isinstance(item, Handle)]
    assert len(handles) == 8 and all(isinstance(h, QGraphicsEllipseItem) for h in handles)
    ring = handles[0].pen().widthF()
    assert handles[0].rect().width() + ring == pytest.approx(Handle.SIZE)  # 10 px across, ring included


def test_text_box_typing(window, workspace):
    view = workspace.view
    window.tool_settings.set_tool("text")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(view, 0, 300, 500))
    QTest.keyClicks(view, "Ciao")
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.qWait(10)

    [box] = workspace.store.all()
    assert isinstance(box, FreeText)
    assert box.contents == "Ciao"
    assert box.rect[:2] == pytest.approx((300, 500), abs=2)


def test_a_locked_selection_shows_a_padlock(window, workspace):
    from zenith_pdf.ui.annotation_items import Handle, LockBadge

    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 150, 100), locked=True)
    view.add_annotations([shape], "Rectangle")
    window.tool_settings.set_tool("select")
    view.reveal_annotation(shape.id)
    kinds = lambda kind: [item for item in view.scene().items() if isinstance(item, kind)]
    assert len(kinds(LockBadge)) == 1 and kinds(Handle) == []  # no handles: it can't be resized
    view.scene().clearSelection()
    assert kinds(LockBadge) == []


def test_the_undo_bar_ends_typing_first(window, workspace):
    """As Ctrl+Z does: the text being typed counts as done, then Undo takes that back, not the step before."""
    view = workspace.view
    box = FreeText(page=0, rect=(100, 300, 200, 40), contents="Ciao")
    view.add_annotations([box], "Text box")
    view.edit_text_box(box, is_new=False)
    view._editor[0].setPlainText("Ciao, typed")
    undo = next(b for b in workspace.canvas.bars["undo"].findChildren(QToolButton) if b.toolTip().startswith("Undo"))
    undo.click()
    assert not view.is_editing_text()
    assert [a.contents for a in workspace.store.all()] == ["Ciao"]


def test_the_page_box_puts_the_page_back_when_left(window, workspace):
    counter, view = workspace.page_counter, workspace.view
    field = counter.field
    counter.focus()
    QTest.keyClicks(field, "99")  # no such page in four
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert field.text() == "1" and field.hasFocus() and view.current_page == 0
    QTest.keyClicks(field, "3")
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert field.text() == "1" and not field.hasFocus() and view.current_page == 0
    counter.focus()
    field.clear()
    view.setFocus()
    assert field.text() == "1"


def test_paste_in_the_selection_bar_follows_its_own_copy(window, workspace):
    view = workspace.view
    shape = Shape(page=0, rect=(100, 300, 80, 60))
    view.add_annotations([shape], "Rectangle")
    view.reveal_annotation(shape.id)
    QTest.qWait(10)
    bar = workspace.selection_bar
    QApplication.clipboard().clear()
    bar.sync(view.selected_annotations())
    assert not bar._action("Paste").isEnabled()
    bar._action("Copy").trigger()
    bar.more.menu().aboutToShow.emit()  # the same selection: the menu looks again as it opens
    assert bar._action("Paste").isEnabled()


def test_a_colour_for_a_mixed_selection_shows_on_a_fill_only_shape(window, workspace):
    """The colour row of a mixed selection sets the border colour: a shape without a border gets one, as a fill-only
    rectangle alone does with Border picked."""
    view = workspace.view
    filled = Shape(page=0, rect=(100, 300, 80, 60), fill=(0, 0, 1), border=False)
    line = Line(page=0, start=(100, 400), end=(200, 420))
    view.add_annotations([filled, line], "Shapes")
    for annotation in (filled, line):
        view._items[annotation.id].setSelected(True)
    view.apply_style(color=(1.0, 0.0, 0.0))
    shape = next(a for a in workspace.store.all() if isinstance(a, Shape))
    assert shape.border and shape.color == (1.0, 0.0, 0.0) and shape.fill == (0, 0, 1)
    view.apply_style(width=4.0)  # anything else leaves the border as it is
    view.apply_style(border=False, fill=(0, 1, 0))
    shape = next(a for a in workspace.store.all() if isinstance(a, Shape))
    assert not shape.border
