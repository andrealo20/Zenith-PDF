"""The glass bars, the theme's shared looks, and the window shell around the pages."""

import math
import shutil
import sys
import time

import pikepdf
import pytest
import shiboken6
from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import FreeText, Redaction, Shape
from zenith_pdf.settings import Settings
from zenith_pdf.theme import DARK, LIGHT, themes
from zenith_pdf.ui import messages, widgets
from zenith_pdf.ui.new_file import NewFilePanel
from zenith_pdf.ui.widgets import BAR_BUTTON, MOST_ALPHA, FloatingBar, IconButton, contrast, glass_alpha, luminance
from zenith_pdf.ui.workspace import SnappingPopup


def settle(ms=60):
    QTest.qWait(ms)
    QApplication.processEvents()


def idle(window, view, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if window.renderer.is_idle() and view.missing_tiles() == 0:
            break
        time.sleep(0.01)
    settle(40)


def flush_deletes():
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    settings.set("glass", "clear")
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
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    workspace.side.hide_panel()
    idle(window, workspace.view)
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def record_samples(bar, scrollbar):
    """Wrap the bar's resampling: a list of (time, scroll value) for each sample."""
    samples = []
    original = bar.refresh_backdrop

    def sampled():
        samples.append((time.perf_counter(), scrollbar.value()))
        original()

    bar.refresh_backdrop = sampled
    return samples


# Glass ----------------------------------------------------------------------------------


def test_scrolling_moves_the_glass_at_once_and_resamples_while_it_goes(workspace):
    view, bar = workspace.view, workspace.toolbar
    view.set_zoom(2.0)
    idle(workspace.window(), view)
    scrollbar = view.verticalScrollBar()
    scrollbar.setValue(0)
    settle()
    samples = record_samples(bar, scrollbar)
    misplaced = []
    steps = []
    ticker = QTimer()
    ticker.setInterval(8)
    begin = time.perf_counter()

    def tick():
        if time.perf_counter() - begin > 0.4:
            ticker.stop()
            return
        scrollbar.setValue(scrollbar.value() + 6)
        steps.append(scrollbar.value())
        last = samples[-1][1] if samples else 0
        # The picture behind the glass moved with the pages: it is where they are now.
        if bar._backdrop is not None and bar._shift.y() != last - scrollbar.value():
            misplaced.append((scrollbar.value(), last, bar._shift.y()))

    ticker.timeout.connect(tick)
    ticker.start()
    deadline = time.monotonic() + 5
    while ticker.isActive() and time.monotonic() < deadline:
        QApplication.processEvents()
    end = time.perf_counter()
    during = [t for t, _ in samples if begin <= t <= end]
    assert len(steps) >= 10
    assert not misplaced
    # Throttled, not debounced: fresh samples keep coming while the scroll goes on.
    assert len(during) >= max(3, len(steps) // 6)


def test_a_zoom_resamples_at_once_and_a_scene_change_only_where_it_reaches(workspace):
    view, canvas = workspace.view, workspace.canvas
    toolbar, zoom_bar = workspace.toolbar, workspace.zoom_bar
    scrollbar = view.verticalScrollBar()
    toolbar_samples = record_samples(toolbar, scrollbar)
    zoom_samples = record_samples(zoom_bar, scrollbar)
    view.set_zoom(view.zoom * 1.25)
    assert toolbar_samples and zoom_samples  # before any event is processed

    idle(workspace.window(), view)  # the new tiles have landed
    deadline = time.monotonic() + 2
    while canvas._glass_timer.isActive() or canvas._since.elapsed() < canvas.GLASS_REFRESH_MS:
        QTest.qWait(5)  # a frame since the last sample, so the next one is not put off
        assert time.monotonic() < deadline
    toolbar_samples.clear()
    zoom_samples.clear()
    viewport = view.viewport()
    under = QRect(viewport.mapFrom(canvas, zoom_bar.pos()), zoom_bar.size())
    canvas._on_scene_changed([view.mapToScene(under).boundingRect()])
    assert zoom_samples and not toolbar_samples  # a frame has passed: sampled at once, and only there


def test_a_jump_past_the_picture_samples_again_at_once(workspace):
    view, bar, canvas = workspace.view, workspace.toolbar, workspace.canvas
    view.set_zoom(1.5)
    idle(workspace.window(), view)
    deadline = time.monotonic() + 2
    while canvas._glass_timer.isActive() or canvas._since.elapsed() < canvas.GLASS_REFRESH_MS:
        QTest.qWait(5)
        assert time.monotonic() < deadline
    scrollbar = view.verticalScrollBar()
    samples = record_samples(bar, scrollbar)
    canvas._refresh_glass()  # a sample now: the next one waits for a frame
    samples.clear()
    scrollbar.setValue(scrollbar.value() + 3)  # a small step: the picture moves with the pages, no new sample yet
    assert not samples and bar._shift.y() == -3 and bar.covers()
    view.next_page()  # a page turn, within the same frame: far past the bleed sampled round the bar
    assert samples, "sampled again at once, not a frame later"
    assert bar._shift.isNull() and bar.covers()
    canvas._glass_timer.stop()


def test_tiles_rendered_ahead_repaint_nothing_that_shows(workspace):
    from zenith_pdf.ui.tiles import cell_rect, render_area, scale_key

    view = workspace.view
    idle(workspace.window(), view)
    assert view.missing_tiles() == 0
    changes = []
    view.scene().changed.connect(changes.append)
    exact = view._view_scale() * view.viewport().devicePixelRatioF()
    item = view._pages[0]
    size = (item.rect.width(), item.rect.height())
    for scale, shows in ((exact * 1.25, False), (exact, True)):  # a zoom level ahead, then the one shown
        cell = cell_rect(size, scale, 0, 0)
        area = render_area(size, scale, cell)
        image = QImage(area.size(), QImage.Format.Format_RGB32)
        image.fill(QColor(255, 255, 255))
        changes.clear()
        view._on_rendered((view._token, "tile", 0, scale_key(scale), 0, 0), scale, image)
        settle(20)
        assert bool(changes) == shows, scale
    view.scene().changed.disconnect(changes.append)


def test_the_picture_stays_with_the_pages_between_samples(workspace):
    view, bar, canvas = workspace.view, workspace.toolbar, workspace.canvas
    view.set_zoom(1.0)
    scrollbar = view.verticalScrollBar()
    scrollbar.setValue(scrollbar.value() + 200)
    idle(workspace.window(), view)
    # A black band across the page whose top edge runs under the middle of the toolbar: an edge to follow.
    page = view._pages[0]
    edge = page.mapFromScene(view.mapToScene(QPoint(0, bar.geometry().center().y()))).y()
    view.add_annotations([Shape(page=0, rect=(0, edge, page.rect.width(), 300), fill=(0, 0, 0))], "Band")
    idle(workspace.window(), view)
    canvas._refresh_glass()
    children = [w for w in bar.findChildren(QWidget) if w.parentWidget() is bar and w.isVisible()]
    for child in children:
        child.hide()
    try:
        canvas._glass_timer.stop()
        bar.shift_backdrop(0, 0)
        scrollbar.setValue(scrollbar.value() + 9)  # moved, not sampled again
        canvas._glass_timer.stop()
        assert bar._shift.y() == -9
        moved = bar.grab().toImage()
        bar.refresh_backdrop()
        fresh = bar.grab().toImage()
    finally:
        for child in children:
            child.show()
    # The shifted picture is where a fresh sample puts it: the two differ by no more than rounding.
    worst = 0
    for y in range(4, moved.height() - 4, 3):
        for x in range(bar.height(), moved.width() - bar.height(), 7):
            a, b = moved.pixelColor(x, y), fresh.pixelColor(x, y)
            worst = max(worst, abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue()))
    assert worst <= 6


def test_the_edge_and_the_light_fill_whole_device_pixels(window):
    holder = QWidget()
    holder.resize(300, 100)
    bar = FloatingBar(holder)  # nothing to sample behind it: the even glass colour, so rows compare exactly
    bar.setGeometry(37, 11, 200, 44)  # at 125% and 175% its top lands a fraction of a device pixel in

    def near(a, b, by=2):
        return all(abs(p - q) <= by for p, q in zip(a.getRgb(), b.getRgb()))

    for ratio in (1.0, 1.25, 1.5, 1.75, 2.0):
        image = QImage(QSize(round(300 * ratio), round(100 * ratio)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        holder.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        painter.end()
        x = round(137 * ratio)
        top = math.floor(11 * ratio + 0.5)  # the device pixel row Qt starts the bar's rect on
        width = max(1, int(ratio + 0.5))  # one device pixel at 100% and 125%, two from 150%
        edge, light, fill = (image.pixelColor(x, top), image.pixelColor(x, top + width),
                             image.pixelColor(x, top + 2 * width + 1))
        # Nothing half covered above the edge (only the soft shadow there), then `width` whole pixels of edge,
        # as many of light, then the glass.
        assert image.pixelColor(x, top - 1).alpha() < 40 and edge.alpha() == 255, ratio
        assert near(image.pixelColor(x, top + width - 1), edge, 0), ratio
        assert near(image.pixelColor(x, top + 2 * width - 1), light), ratio
        assert not near(light, edge) and not near(light, fill, 8), ratio
    bar.hide()
    bar.setParent(None)
    holder.deleteLater()
    bar.deleteLater()


def test_glass_tint_keeps_text_readable_over_any_page(qt_app):
    behind = [QColor(255, 255, 255), QColor(0, 0, 0), QColor(128, 128, 128), QColor(255, 220, 0),
              QColor(0, 120, 255), QColor(220, 20, 60), QColor(30, 30, 34), QColor(240, 240, 240)]
    try:
        for style in ("clear", "balanced"):
            widgets.set_glass(style)
            for theme in (DARK, LIGHT):
                least = widgets.least_alpha(theme)
                plain_need, busy_need = widgets.contrast_targets()
                text = luminance(QColor(theme.text))
                for color in behind:
                    alpha = glass_alpha(color, theme)
                    assert least <= alpha <= MOST_ALPHA
                    mixed = luminance(widgets._over(theme.glass_base, alpha, color))
                    assert contrast(mixed, text) >= plain_need, (style, theme.name, color.name())
                    busy = glass_alpha(color, theme, need=busy_need)
                    assert busy <= alpha  # a bright spot in a photo needs less than a plain page
                    assert contrast(luminance(widgets._over(theme.glass_base, busy, color)), text) >= busy_need
                # Over content like the glass itself nothing needs covering: the glass stays as clear as it gets.
                assert glass_alpha(theme.glass_base, theme) == least
        assert DARK.glass_clear_alpha < DARK.glass_alpha and LIGHT.glass_clear_alpha < LIGHT.glass_alpha
        widgets.set_glass("balanced")
        assert widgets.contrast_targets() == (7.0, 4.5)  # Balanced reads at AAA
        widgets.set_glass("clear")
        assert widgets.contrast_targets() == (4.5, 3.0)  # Clear shows more of the page, at AA
    finally:
        widgets.set_glass("clear")


def test_the_tint_rises_only_where_reading_needs_it(qt_app):
    white, canvas, spot = (255, 255, 255), (19, 20, 23), (230, 220, 160)
    # A plain page needs the style's plain contrast right up to its edge; a lone bright spot of a photo the busy one.
    plain_need, busy_need = widgets.contrast_targets()
    assert widgets.column_needs([1.0] * 4 + [0.007] * 4) == [plain_need] * 8
    needs = widgets.column_needs([0.01, 0.01, 0.01, 0.7, 0.01, 0.01, 0.01])
    assert needs[3] == busy_need and needs[0] == plain_need
    # The bar is clearer over the canvas than over the page beside it, and each column keeps what it needs.
    columns = [white] * 6 + [canvas] * 8
    profile = widgets.alpha_profile(columns, DARK)
    assert profile[0] > 0.5 and profile[-1] == pytest.approx(widgets.least_alpha(DARK))
    text = luminance(QColor(DARK.text))
    for colour, alpha, need in zip(columns, profile, widgets.column_needs(
            [luminance(QColor(*c)) for c in columns])):
        assert contrast(luminance(widgets._over(DARK.glass_base, alpha, QColor(*colour))), text) >= need
    assert widgets.needed_alpha([QColor(*spot)], DARK) >= widgets.needed_alpha([QColor(*canvas)], DARK)


def test_glass_over_a_white_page_reads_as_the_style_asks(window, workspace):
    view, counter = workspace.view, workspace.page_counter
    for name in ("dark", "light"):
        window.set_theme(name)
        view.set_zoom(2.5)
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum())
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 200)
        idle(window, view)
        counter.refresh_backdrop()
        children = [w for w in counter.findChildren(QWidget) if w.parentWidget() is counter and w.isVisible()]
        for child in children:
            child.hide()
        image = workspace.canvas.grab().toImage()
        for child in children:
            child.show()
        ratio = image.devicePixelRatio()
        geometry = counter.geometry()
        y = round(geometry.center().y() * ratio)
        surface = [luminance(image.pixelColor(round(x * ratio), y))
                   for x in range(geometry.left() + 20, geometry.right() - 20, 3)]
        t = counter.theme()  # over a white page the dark theme's bar takes the light theme's colours
        worst = max(surface) if t.name == "dark" else min(surface)
        need = widgets.contrast_targets()[0]
        assert contrast(worst, luminance(QColor(t.text))) >= need, name  # on every pixel, the grain's too
        # Muted text on the glass over the pages takes the full text colour.
        note = widgets.muted_label("muted")
        counter.layout().addWidget(note)
        note.ensurePolished()
        assert note.palette().color(note.foregroundRole()) == QColor(t.text), name
        note.hide()
        note.setParent(None)
        note.deleteLater()
    window.set_theme("dark")


def test_bars_cast_a_cached_shadow_instead_of_an_effect(workspace):
    counter = workspace.page_counter
    shadow = counter._shadow
    assert counter.graphicsEffect() is None
    assert shadow is not None and shadow.parentWidget() is counter.parentWidget()
    assert shadow.geometry().contains(counter.geometry())
    siblings = counter.parentWidget().children()
    assert siblings.index(shadow) == siblings.index(counter) - 1  # just under its bar
    counter.raise_()
    siblings = counter.parentWidget().children()
    assert siblings.index(shadow) == siblings.index(counter) - 1
    counter.hide()
    assert not shadow.isVisible()
    counter.show()
    assert shadow.isVisible()
    assert not shadow.grab().toImage().isNull()
    picture = shadow._picture
    shadow.grab()
    assert shadow._picture is picture  # put together once, then copied on every frame
    window = counter.window()
    window.set_theme("light")
    shadow.grab()
    assert shadow._picture is not picture  # a lighter shadow in the light theme
    window.set_theme("dark")
    popup = FloatingBar(workspace, popup=True)
    assert popup._shadow is None
    popup.deleteLater()


def test_bar_buttons_are_squares_with_cached_icons(window, workspace):
    for bar in (workspace.page_counter, workspace.zoom_bar, workspace.canvas.bars["undo"]):
        assert bar.height() == BAR_BUTTON + 8
        for button in bar.findChildren(IconButton):
            # Not a chip with text, as Back, nor the zoom bar's More, shown only in a narrow window.
            if button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly and button.isVisibleTo(bar):
                assert button.size() == QSize(BAR_BUTTON, BAR_BUTTON)
    cached = widgets.cached_icon("pencil-simple")
    first = cached.pixmap(QSize(20, 20), 1.0)
    assert cached.pixmap(QSize(20, 20), 1.0).cacheKey() == first.cacheKey()  # drawn once, then reused
    button = workspace.zoom_bar.snap_button
    widgets.set_glass_adaptive(False)  # the theme's own colours on every bar, wherever it is
    try:
        before = button.icon().pixmap(QSize(18, 18), 1.0).toImage()
        window.set_theme("light")
        after = button.icon().pixmap(QSize(18, 18), 1.0).toImage()
        window.set_theme("dark")
    finally:
        widgets.set_glass_adaptive(True)
    assert before != after  # a new theme draws the icons again in its colours


def test_the_toast_is_glass_like_the_bars(workspace):
    toast = workspace.toast
    workspace.notify("Copied 2 annotations.")
    settle(30)
    assert toast.isVisible() and toast.text() == "Copied 2 annotations."
    assert toast._backdrop is not None and toast._shadow is not None and toast._shadow.isVisible()
    canvas = workspace.canvas
    assert toast.geometry().bottom() < canvas.bars["zoom"].geometry().top()
    toast.hide()
    assert not toast._shadow.isVisible()


def test_page_counter_reads_in_full_contrast(workspace):
    counter = workspace.page_counter
    assert counter.total.objectName() != "Muted"
    assert counter.total.text().strip() == "of 4"
    layout = counter.layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order[:2] == [counter.field, counter.total]  # the arrows sit together after "1 of 4"


# Theme ----------------------------------------------------------------------------------


def brightest(widget) -> float:
    image = widget.grab().toImage()
    return max(luminance(image.pixelColor(x, y)) for y in range(image.height()) for x in range(image.width()))


def test_disabled_widgets_look_disabled(window):
    window.set_theme("dark")
    for make in (lambda: QPushButton("Save as"), lambda: QLabel("Location"), lambda: QCheckBox("Ink only"),
                 lambda: QLineEdit("name.pdf")):
        box = QDialog()  # its own background, as in the app
        widget = make()
        layout = QVBoxLayout(box)
        layout.addWidget(widget)
        box.resize(box.sizeHint())
        enabled = brightest(box)
        widget.setEnabled(False)
        disabled = brightest(box)
        assert disabled < enabled * 0.75, type(widget).__name__
        box.deleteLater()


def test_menus_have_round_corners(window):
    menu = QMenu(window)
    menu.addAction("Rotate")
    menu.addAction("Delete")
    menu.popup(window.mapToGlobal(QPoint(200, 200)))
    settle(30)
    image = menu.grab().toImage()
    menu.hide()
    assert image.pixelColor(0, 0).alpha() == 0  # outside the rounded border
    assert image.pixelColor(image.width() // 2, 3).alpha() == 255
    menu.deleteLater()


def test_the_violet_accent_is_the_logo_violet(window):
    window.settings.set("accent", "violet")
    assert themes.current.accent == "#9a5cff"
    assert themes.current.glass_checked == "rgba(154, 92, 255, 56)"
    window.settings.set("accent", "blue")


# Workspace ------------------------------------------------------------------------------


def test_flatten_never_burns_redaction_marks(workspace):
    view = workspace.view
    mark = Redaction(page=0, rects=((60, 60, 120, 20),))
    shape = Shape(page=0, rect=(200, 200, 60, 60))
    view.add_annotations([mark, shape], "Marks")
    view.select([mark, shape])
    workspace.settings.stop_asking("flatten")
    workspace.flatten_selected()
    assert mark.id in workspace.store and shape.id not in workspace.store
    assert "Redaction" in workspace.toast.text()

    pages = workspace.undo_stack.count()
    view.select([workspace.store.get(mark.id)])
    workspace.flatten_selected()  # only marks: nothing to do
    assert workspace.undo_stack.count() == pages and mark.id in workspace.store


def test_page_manager_duplicates_the_selection_in_one_step(workspace):
    workspace.side.show_panel("pages")
    workspace.toggle_page_manager()
    manager = workspace.page_manager
    assert workspace.side.current is None  # one grid of pages at a time
    workspace.toast.show_message("Copied")
    workspace.toggle_page_manager()
    assert workspace.side.current == "pages"

    workspace.toggle_page_manager()
    manager = workspace.page_manager
    assert not workspace.toast.isVisible() and not workspace.toolbar.isVisible()
    manager.select_rows([0, 2])
    count = workspace.undo_stack.count()
    manager.duplicate_selected()
    assert workspace.document.page_count == 6
    assert workspace.undo_stack.count() == count + 1
    assert workspace.page_manager.selected() == [3, 4]
    workspace.undo_stack.undo()
    assert workspace.document.page_count == 4
    workspace.toggle_page_manager()
    flush_deletes()
    assert not shiboken6.isValid(manager)


def test_the_thumbnail_highlight_survives_a_page_edit(workspace):
    workspace.side.show_panel("pages")
    workspace.view.goto_page(2)
    workspace.actions.rotate_pages([0], 90)
    thumbnails = workspace.thumbnails.list
    assert thumbnails.currentIndex().row() == workspace.view.current_page
    assert thumbnails.selectionModel().isSelected(thumbnails.currentIndex())
    workspace.side.show_panel("annotations")
    workspace.side.show_panel("pages")
    assert thumbnails.selectionModel().isSelected(thumbnails.currentIndex())


def test_popups_are_deleted_when_they_close(window, workspace):
    workspace.zoom_bar._show_snapping()
    settle(20)
    popup = next(w for w in QApplication.topLevelWidgets() if isinstance(w, SnappingPopup) and w.isVisible())
    popup.close()
    flush_deletes()
    assert not shiboken6.isValid(popup)

    panel = NewFilePanel(window.settings, window)
    panel.show_below(window.home_button)
    panel.close()
    flush_deletes()
    assert not shiboken6.isValid(panel)


def test_a_reload_while_editing_text_raises_nothing(workspace, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    view = workspace.view
    view.edit_text_box(FreeText(page=0, rect=(80, 140, 200, 30), contents="Typing", font_size=12), is_new=True)
    view.reload()  # an undone page edit clears the scene under the editor
    settle(40)
    assert not errors


def test_floating_cards_hide_with_the_page_manager(workspace):
    card = FloatingBar()
    card.resize(120, 44)
    workspace.canvas.add_floating(card)
    card.show()
    assert card.parentWidget() is workspace.canvas and card._source is workspace.view.viewport()
    workspace.toggle_page_manager()
    assert not card.isVisible()
    workspace.toggle_page_manager()
    card.hide()
    card.setParent(None)
    card.deleteLater()
    flush_deletes()
    workspace.canvas.resizeEvent(None)  # a deleted card is forgotten, not raised


# Doc Builder ----------------------------------------------------------------------------


def test_doc_builder_asks_in_the_app_style(window, sample_pdf, tmp_path, monkeypatch):
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(sample_pdf) as pdf:
        pdf.save(locked, encryption=pikepdf.Encryption(owner="secret", user="secret"))
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    prompts, warnings = [], []
    answers = iter(["wrong", "secret"])

    def get_text(parent, title, label, text="", **options):
        prompts.append((title, label, options))
        return next(answers)

    monkeypatch.setattr(messages, "get_text", get_text)
    monkeypatch.setattr(messages, "warn", lambda parent, title, text, detail="", **_: warnings.append(title))
    window.open_builder()
    builder = window.stack.currentWidget()
    builder.add_files([str(locked), str(broken)])
    assert len(prompts) == 2 and prompts[0][2]["password"] and "Try again" in prompts[1][1]
    assert warnings == ["Can't add file"]
    assert len(builder.entries) == 4
    assert builder.selection_button.objectName() == "MenuButton"
    assert builder.selection_button.text() == "0 selected"
    window.close_tab(window.workspaces.index(builder))


def test_bars_turn_light_over_a_bright_page_and_dark_again_over_the_canvas(window, workspace):
    """Liquid Glass's way: in the dark theme a bar over a white page takes the light theme's colours, so its
    tint can stay thin; back over the dark canvas it is dark again. With the setting off it never changes."""
    view, counter = workspace.view, workspace.page_counter
    window.set_theme("dark")
    view.set_zoom(2.5)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum() // 2)  # the page fills the width
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 300)
    idle(window, view)
    counter.refresh_backdrop()
    assert luminance(counter._mean) > widgets.TONE_LIGHT_OVER  # white paper behind it
    assert counter.tone == "light" and counter.theme().name == "light"
    button = next(b for b in counter.findChildren(IconButton) if b.isVisibleTo(counter))
    assert button._tone_text() == counter.theme().text  # its icon in the light theme's ink
    assert counter.styleSheet()  # labels and the page field in the light theme's colours
    assert counter._chosen_tone(QColor(19, 20, 23)) == ""  # the canvas: the dark theme's own again
    assert counter._chosen_tone(QColor(150, 150, 150)) == "light"  # in between: it stays as it is
    widgets.set_glass_adaptive(False)
    try:
        assert counter.tone == "" and not counter.styleSheet()
    finally:
        widgets.set_glass_adaptive(True)
    window.set_theme("light")
    counter.refresh_backdrop()
    assert counter.tone == ""  # the light theme over a white page: its own colours
    assert counter._chosen_tone(QColor(5, 5, 8)) == "dark"  # a dark picture behind: the dark theme's
    window.set_theme("dark")
