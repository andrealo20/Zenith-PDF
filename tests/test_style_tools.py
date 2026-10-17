"""Giving one annotation's look to others, and picking a colour from the page."""

import shutil

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt, QTimer
from PySide6.QtTest import QTest

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import FreeText, Line, Shape
from zenith_pdf.settings import Settings
from zenith_pdf.ui import tool_menu


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


def on_screen(view, annotation):
    x, y, w, h = annotation.bounds()
    return view.mapFromScene(view._pages[annotation.page].mapToScene(QPointF(x + w / 2, y + h / 2)))


def test_the_format_painter_gives_a_look_to_the_next_one_clicked(workspace):
    view, store = workspace.view, workspace.store
    source = Shape(page=0, rect=(100, 100, 60, 40), color=(0.0, 0.6, 0.2), width=5.0, dash="dashed",
                   fill=(1.0, 1.0, 0.0))
    target = Line(page=0, start=(300, 120), end=(400, 160), color=(1.0, 0.0, 0.0), width=1.0)
    box = FreeText(page=0, rect=(100, 300, 120, 30), contents="Words", font="Times", bold=True)
    view.add_annotations([source, target, box], "Marks")
    view.ensureVisible(view._pages[0].mapRectToScene(view._pages[0].rect), 0, 0)
    view.select([source])
    QTest.qWait(30)
    QTest.mouseClick(workspace.selection_bar.painter, Qt.MouseButton.LeftButton)
    assert view.painting
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=on_screen(view, target))
    assert not view.painting  # one use
    [line] = [a for a in store.all() if isinstance(a, Line)]
    assert (line.color, line.width, line.dash, line.start) == ((0.0, 0.6, 0.2), 5.0, "dashed", (300, 120))
    workspace.undo_stack.undo()
    assert next(a for a in store.all() if isinstance(a, Line)).color == (1.0, 0.0, 0.0)

    view.select([box])  # a text box's font goes to another with Copy and Paste style
    view.copy_style()
    other = FreeText(page=0, rect=(300, 300, 120, 30), contents="More")
    view.add_annotations([other], "Text box")
    view.select([other])
    assert view.paste_style()
    pasted = next(a for a in store.all() if a.id == other.id)
    assert (pasted.font, pasted.bold, pasted.contents) == ("Times", True, "More")

    view.select([source])
    view.start_format_painter()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not view.painting


def test_the_eyedropper_picks_the_colour_under_the_click(workspace):
    view = workspace.view
    mark = Shape(page=0, rect=(150, 400, 80, 60), fill=(0.2, 0.4, 0.8), color=(0.2, 0.4, 0.8))
    view.add_annotations([mark], "Rectangle")
    view.ensureVisible(view._pages[0].mapRectToScene(view._pages[0].rect), 0, 0)
    QTest.qWait(30)
    assert tool_menu.colour_picker is not None
    QTimer.singleShot(50, lambda: QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                                   pos=on_screen(view, mark)))
    color = tool_menu.colour_picker()
    assert color == pytest.approx((0.2, 0.4, 0.8), abs=0.02)
    QTimer.singleShot(50, lambda: QTest.keyClick(view, Qt.Key.Key_Escape))
    assert tool_menu.colour_picker() is None  # Esc: no colour
