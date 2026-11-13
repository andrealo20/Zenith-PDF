"""The Liquid glass: a fourth style of the bars, in Settings beside the other three, drawn through the lens where
it runs and as the Clear one wherever it cannot."""

import math
import sys
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

import test_glass
from conftest import settle, wait_for
from test_glass import idle, record_samples
from test_lens import bend, read, rgb, smoothstep
from zenith_pdf import settings as stored
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.i18n import LANGUAGES, set_language
from zenith_pdf.settings import Settings
from zenith_pdf.styles import neon
from zenith_pdf.theme import DARK, LIGHT, themes
from zenith_pdf.ui import lens, settings_page, widgets
from zenith_pdf.ui.settings_page import CONTROL_WIDTH, GLASS_CHOICES, SettingsWindow

STYLES = ("liquid", "clear", "balanced", "solid")
window, workspace = test_glass.window, test_glass.workspace  # the glass tests' fixtures, for these too
needs_lens = pytest.mark.skipif(not lens.supported(), reason="the lens draws through Direct3D: Windows alone")
BAR = QRect(50, 60, 300, 44)  # a capsule, in the window of its own that floating() makes
WHITE, BLACK, INK = QColor(255, 255, 255), QColor(0, 0, 0), QColor(20, 20, 30)
LINE = 2  # px: the dark lines across a Page
GRAIN = widgets._noise_tile  # the glass's grain, which floating() takes away


def test_liquid_is_a_glass_style_and_clear_stays_the_default():
    assert widgets.GLASS_STYLES == stored.CHOICES["glass"] == STYLES
    assert widgets.glass_style("liquid") == "liquid" and widgets.glass_style("nonsense") == "clear"
    assert widgets.glass_style(True) == "clear" and widgets.glass_style(False) == "solid"  # the old switch
    assert stored.DEFAULTS["glass"] == "clear"


def test_liquid_reads_like_clear(qt_app, monkeypatch):
    before = widgets.glass.style
    try:
        widgets.set_glass("liquid")
        theme = themes.current
        assert widgets.glass.clear is True
        assert widgets.contrast_targets() == (widgets.CLEAR_TEXT_CONTRAST, widgets.CLEAR_BUSY_CONTRAST)
        assert widgets.least_alpha(theme) == theme.glass_clear_alpha
        widgets.set_glass("balanced")
        assert widgets.glass.clear is False
        assert widgets.contrast_targets() == (widgets.TEXT_CONTRAST, widgets.BUSY_CONTRAST)
    finally:
        widgets.set_glass(before)
    # Through the lens only in the Liquid style, where the lens can run and the look has glass bars at all.
    for style in STYLES:
        monkeypatch.setattr(widgets.glass, "style", style)  # the names alone: no bar is drawn meanwhile
        for can in (True, False):
            monkeypatch.setattr(lens, "available", lambda: can)
            for bars in (True, False):  # a look with glass bars, and one that draws them solid
                monkeypatch.setattr(themes, "current", replace(theme, look=replace(theme.look, glass=bars)))
                assert widgets.glass.liquid is (style == "liquid" and can and bars), (style, can, bars)


def test_without_a_lens_a_liquid_bar_is_painted_as_a_clear_one(window, workspace, monkeypatch):
    monkeypatch.setattr(lens, "available", lambda: False)
    view, bar = workspace.view, workspace.toolbar
    # The first page under the toolbar, with a patch of colour behind the bar's left, a dark one behind its right
    # and plain paper between them: the lift of the colours, the tint as strong as each column needs and the
    # light on the rim all show in the picture.
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 200)
    idle(window, view)
    page = view._pages[0]
    top = page.mapFromScene(view.mapToScene(QPoint(0, bar.geometry().top()))).y() - 40
    width = page.rect.width()
    assert top > 0  # the page reaches well above the bar
    view.add_annotations([Shape(page=0, rect=(0, top, width / 2, 240), fill=(0.95, 0.35, 0.1)),
                          Shape(page=0, rect=(width * 0.6, top, width * 0.4, 240), fill=(0.05, 0.05, 0.08))],
                         "Patches")

    before = window.settings.get("glass")
    try:
        clear = toolbar_picture(window, workspace, "clear")
        assert bar._backdrop is not None and bar.covers()  # glass over the page, not a flat colour
        liquid = toolbar_picture(window, workspace, "liquid")
        assert widgets.glass.style == "liquid" and widgets.glass.liquid is False
        assert bar._backdrop is not None and bar.covers()
        assert liquid == clear  # pixel for pixel
        assert toolbar_picture(window, workspace, "balanced") != clear  # and the picture tells one glass from another
    finally:
        window.settings.set("glass", before)


def toolbar_picture(window, workspace, style):
    """The toolbar's grab in the glass `style`, every bar sampled again where the pages are."""
    view, canvas = workspace.view, workspace.canvas
    window.settings.set("glass", style)
    idle(window, view)
    canvas._glass_timer.stop()
    canvas._refresh_glass()
    canvas._glass_timer.stop()
    settle()
    return workspace.toolbar.grab().toImage()


def glass_row(page):
    """The Glass row of a settings window, its control, and whether the control sits beside the row's words
    (else under them)."""
    row = next(row for row in page.sections["view"].rows if row.title == "Glass")
    control = row.findChild(widgets.Segmented)
    return row, control, control.geometry().top() < row.findChild(QLabel, "Muted").geometry().bottom()


def cut_short(control) -> list[str]:
    """The labels of a segmented control that don't show in full. A segment draws its label in bold once it is
    picked, and cuts it short unless 6 px stay clear on each side of it."""
    cut = []
    for button in control.buttons:
        bold = QFont(button.font())
        bold.setWeight(QFont.Weight.DemiBold)
        if QFontMetrics(bold).horizontalAdvance(button.text()) > button.width() - 12:
            cut.append(button.text())
    return cut


def test_the_glass_row_offers_liquid_first_and_every_label_fits_in_every_language(window, monkeypatch):
    assert GLASS_CHOICES == (("liquid", "Liquid"), ("clear", "Clear"), ("balanced", "Balanced"), ("solid", "Solid"))
    style = window.settings.get("style")
    try:
        for code in (*LANGUAGES, "en"):  # English last: its window stays, for what follows
            set_language(code)
            page = SettingsWindow(window.settings, window.tool_settings, window)
            page.show()
            settle()
            row, glass, beside = glass_row(page)
            assert [value for value, _ in glass.items] == list(STYLES), code
            assert glass.buttons[0].text() == "Liquid", code  # a name, the same in every language
            assert glass.value == window.settings.get("glass") == "clear", code
            assert not cut_short(glass), code
            # Beside the row's words it is as wide as the other controls. Labels too long for that take it
            # under the words, where it has the row's width and no more.
            if beside:
                assert glass.width() == CONTROL_WIDTH, code
            else:
                assert CONTROL_WIDTH < glass.width() <= row.width(), code
                glass.setFixedWidth(CONTROL_WIDTH)  # there for want of room alone: this narrow, a label is cut short
                settle()
                assert cut_short(glass), code
            assert beside or code != "en"  # English keeps the place and the width it always had
            if code != "en":
                page.close()
                page.deleteLater()
        # A style brings its own font, so the row looks again at what fits whenever the style changes. With
        # room for no four labels the control goes under the words; with room again, back beside them.
        monkeypatch.setattr(settings_page, "CONTROL_WIDTH", 100)
        window.settings.set("style", "neon")
        settle()
        row, glass, beside = glass_row(page)
        assert not beside and CONTROL_WIDTH < glass.width() <= row.width() and not cut_short(glass)
        monkeypatch.setattr(settings_page, "CONTROL_WIDTH", CONTROL_WIDTH)
        window.settings.set("style", style)
        settle()
        row, glass, beside = glass_row(page)
        assert beside and glass.width() == CONTROL_WIDTH and not cut_short(glass)
        page.close()
        page.deleteLater()
    finally:
        window.settings.set("style", style)
        set_language("en")


def test_choosing_liquid_in_settings_reaches_every_bar(window, workspace, tmp_path):
    before = window.settings.get("glass")
    bars = [bar for bar in workspace.canvas._glass() if bar.isVisible()]
    scrollbar = workspace.view.verticalScrollBar()
    samples = [record_samples(bar, scrollbar) for bar in bars]
    window.open_settings("view")
    dialog = window._settings_window
    try:
        glass = glass_row(dialog)[1]
        glass.choose("liquid")  # what a click on the segment does
        assert window.settings.get("glass") == "liquid" and widgets.glass.style == "liquid"
        assert bars and all(samples) and all(bar._backdrop is not None for bar in bars)  # glass, sampled at once
    finally:
        window.settings.set("glass", before)
        dialog.close()
    assert widgets.glass.style == before and glass.value == before
    # Kept as it is from one start to the next: a value the settings don't know would read as the default.
    path = str(tmp_path / "settings.ini")
    Settings(QSettings(path, QSettings.Format.IniFormat)).set("glass", "liquid")
    assert Settings(QSettings(path, QSettings.Format.IniFormat)).get("glass") == "liquid"


# Through the lens -----------------------------------------------------------------------


class Page(QWidget):
    """What a bar floats over: one colour, with dark lines LINE px thick across it at `lines` from its top."""

    def __init__(self, colour: QColor, lines=()):
        super().__init__()
        self.colour, self.lines = colour, list(lines)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.colour)
        for top in self.lines:
            painter.fillRect(0, top, self.width(), LINE, INK)
        painter.end()


@pytest.fixture
def liquid(qt_app, monkeypatch):
    """The bars are Liquid glass with the lens up (on Windows the software device is always there), and the lens
    is up again after a test that broke it."""
    assert lens.start()
    monkeypatch.setattr(widgets.glass, "style", "liquid")
    yield
    lens._failed = False
    assert lens.start()


@pytest.fixture
def floating(qt_app, monkeypatch):
    """Makes a glass bar at BAR over a Page, in a window of its own on no screen, sampled once: the dark theme,
    which the bar keeps whatever lies behind it, and no grain, so pictures can be compared to the level."""
    monkeypatch.setattr(themes, "current", DARK)
    monkeypatch.setattr(widgets.glass, "adaptive", False)
    clear = QPixmap(8, 8)
    clear.fill(Qt.GlobalColor.transparent)
    monkeypatch.setattr(widgets, "_noise_tile", lambda: clear)
    made = []

    def make(page: Page) -> widgets.FloatingBar:
        host = QWidget()
        host.resize(400, 200)
        page.setParent(host)
        page.setGeometry(host.rect())
        bar = widgets.FloatingBar(host)
        bar.setGeometry(BAR)
        bar.set_backdrop(page)
        host.show()
        settle()
        bar.refresh_backdrop()
        made.append(host)
        return bar

    yield make
    for host in made:
        host.close()
        host.deleteLater()
    settle()


def renders(monkeypatch) -> list:
    """lens.render, counted: each call's Spec and the picture it gave."""
    render, asked = lens.render, []

    def counted(sharp, middle, spec):
        picture = render(sharp, middle, spec)
        asked.append((spec, picture))
        return picture

    monkeypatch.setattr(lens, "render", counted)
    return asked


def raised(monkeypatch) -> list:
    """What a paint event lets out: Qt hands it to sys.excepthook rather than to the caller."""
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    return errors


def apart(first, second, points) -> int:
    """The largest difference between two pictures over `points`, in levels of any channel."""
    return max(max(abs(a - b) for a, b in zip(rgb(first, x, y), rgb(second, x, y))) for x, y in points)


def mix(first, second, share):
    return [a + (b - a) * share for a, b in zip(first, second)]


def veiled(page, spec) -> list[float]:
    """What a Liquid bar shows of a pixel of the page away from its edge: the page under the faint veil."""
    return mix(page, spec.tint, spec.rim_veil)


@needs_lens
def test_a_liquid_bar_bends_the_page_across_its_whole_height(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))  # a line every 7 px: some line near every row of the bar
    asked = renders(monkeypatch)
    image = bar.grab().toImage()
    spec = asked[-1][0]
    # One lens over all of the bar: a round rim as wide as half of it that never gives way to a middle.
    assert (spec.band, spec.profile, spec.ease, spec.saturation) == (BAR.height() / 2, "circle", 1.0, 1.0)
    sharp, area = bar._sharp, bar._backdrop_area
    x, height, bent = BAR.width() // 2, BAR.height(), set()
    lit = 0.84856  # how squarely the top edge faces the light; the bottom one faces away as much
    for y in range(3, height - 3):  # inside the light and the edge along the top and the bottom
        depth = min(y + 0.5, height - y - 0.5)
        inwards = 1.0 if y < height / 2 else -1.0
        by = bend(depth, spec.band, spec.thick, spec.most, spec.profile)
        at = (x + 0.5 + BAR.left() - area.left(), y + 0.5 + BAR.top() - area.top())
        page = read(sharp, at[0], at[1] + inwards * by)
        wanted = veiled(page, spec)
        if inwards < 0:  # the faint shadow on the side away from the light
            wanted = [part * (1 - spec.shade * lit * (1 - depth / spec.band) ** 2) for part in wanted]
        # Green, read at the bend itself: red and blue part from it by the fringe.
        assert abs(rgb(image, x, y)[1] - wanted[1]) <= 3, (y, rgb(image, x, y), wanted)
        if abs(read(sharp, *at)[1] - page[1]) > 60:  # the page read where it lies would be far from it
            bent.add(inwards)
    assert bent == {1.0, -1.0}  # the bend shows in both halves


@needs_lens
def test_the_middle_of_a_liquid_bar_shows_the_page_sharp_under_a_faint_veil(liquid, floating, monkeypatch):
    asked = renders(monkeypatch)
    page = (150, 120, 90)
    bar = floating(Page(QColor(*page)))
    image = bar.grab().toImage()
    spec = asked[-1][0]
    assert spec.rim_veil == widgets.LIQUID_VEIL[0]  # a dark bar: the dark theme's, kept whatever is behind
    middle = (BAR.width() // 2, BAR.height() // 2)
    assert max(abs(a - b) for a, b in zip(rgb(image, *middle), veiled(page, spec))) <= 2
    # Over fine lines the lines keep their contrast: nothing is blurred or laid over them.
    bar = floating(Page(WHITE, range(0, 200, 7)))
    image = bar.grab().toImage()
    greens = [rgb(image, middle[0], y)[1] for y in range(middle[1] - 7, middle[1] + 7)]
    assert max(greens) - min(greens) >= 0.85 * (255 - INK.green()) * (1 - spec.rim_veil), greens
    monkeypatch.setattr(widgets.glass, "style", "clear")
    bar.refresh_backdrop()
    image = bar.grab().toImage()
    clear = [rgb(image, middle[0], y)[1] for y in range(middle[1] - 7, middle[1] + 7)]
    assert max(clear) - min(clear) < 20  # where the Clear glass melts them into an even wash


def labelled(floating, theme, page, text="Page 12 of 340") -> tuple[widgets.FloatingBar, QLabel]:
    """A bar in `theme` over `page`, with a line of text in the theme's text colour."""
    bar = floating(page)
    label = QLabel(text, bar)
    label.setStyleSheet(f"color: {theme.text}; font-size: 13px;")
    label.setGeometry(40, 10, 220, 24)
    label.show()
    settle()
    bar.refresh_backdrop()
    return bar, label


@needs_lens
def test_next_to_a_glyph_the_halo_keeps_text_readable(liquid, floating, monkeypatch):
    for theme, behind in ((DARK, WHITE), (LIGHT, BLACK)):  # light text over white, dark text over black
        monkeypatch.setattr(themes, "current", theme)
        monkeypatch.setattr(widgets, "_noise_tile", GRAIN)  # with the grain, as the bar is drawn
        asked = renders(monkeypatch)
        bar, _ = labelled(floating, theme, Page(behind))
        image = bar.grab().toImage()
        assert asked and asked[-1][1] is not None and bar._halo is not None, theme.name  # through the lens
        alpha = bar._content_alpha(1.0)  # the text drawn alone: what is glyph and what lies beside it
        glyph = {(x, y) for y in range(alpha.height()) for x in range(alpha.width())
                 if alpha.pixelColor(x, y).alpha() >= 128}
        beside = {(x + dx, y + dy) for x, y in glyph for dx in range(-2, 3) for dy in range(-2, 3)}
        beside = {(x, y) for x, y in beside if 0 <= x < alpha.width() and 0 <= y < alpha.height()
                  and alpha.pixelColor(x, y).alpha() == 0}
        assert len(glyph) > 100 and len(beside) > 100, theme.name
        text = widgets.luminance(QColor(theme.text))
        worst = min(widgets.contrast(widgets.luminance(QColor(*rgb(image, x, y))), text) for x, y in beside)
        assert worst >= 4.5, (theme.name, worst)


def halo_builds(monkeypatch) -> list:
    """FloatingBar._build_halo, counted."""
    build, built = widgets.FloatingBar._build_halo, []

    def counted(self, alpha, ratio, *colour):
        built.append(self)
        return build(self, alpha, ratio, *colour)

    monkeypatch.setattr(widgets.FloatingBar, "_build_halo", counted)
    return built


class Sign(QWidget):
    """A child of fixed size that paints its `text`: it changes with no new layout, as a page number does."""

    def __init__(self, parent):
        super().__init__(parent)
        self.text = "12"
        self.setGeometry(40, 10, 60, 24)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QColor(DARK.text))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
        painter.end()


@needs_lens
def test_the_halo_is_built_once_while_the_pages_scroll_and_again_when_a_child_changes(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))
    sign = Sign(bar)
    sign.show()
    settle()
    built = halo_builds(monkeypatch)
    bar.repaint()
    assert len(built) <= 1 and bar._halo is not None
    built.clear()
    for _ in range(6):
        bar.shift_backdrop(0, -1)  # the pages move: every frame repaints the whole bar
        bar.repaint()
    bar.refresh_backdrop()  # and a new sample
    bar.repaint()
    assert not built
    sign.text = "13"
    sign.repaint()  # the child alone: a paint of part of the bar
    assert len(built) == 1
    # A child that changes within a repaint of the whole bar, as when the pages move in the same frame: the halo
    # is looked at again once they stop.
    sign.text = "14"
    bar.repaint()
    assert len(built) == 1
    assert wait_for(lambda: len(built) == 2, timeout=5)
    settle(2 * widgets.HALO_RECHECK_MS)  # and once it matches, nothing more
    assert len(built) == 2


@needs_lens
def test_clear_draws_no_halo(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "style", "clear")
    built = halo_builds(monkeypatch)
    bar, label = labelled(floating, DARK, Page(WHITE, range(0, 200, 7)))
    bar.grab()
    label.repaint()
    settle(2 * widgets.HALO_RECHECK_MS)
    assert not built and bar._halo is None


# Each child in the ink its own backdrop needs ------------------------------------------------


CANVAS = QColor(DARK.canvas)


class Halves(QWidget):
    """A white page on the left and the dark canvas from `edge` on, as a bar sees them at the side of a page."""

    def __init__(self, edge: int, right: QColor = CANVAS):
        super().__init__()
        self.edge, self.right = edge, right

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), WHITE)
        painter.fillRect(self.edge, 0, self.width() - self.edge, self.height(), self.right)
        painter.end()


class Mark(QWidget):
    """A child that paints "85%" in the ink of the bar it is on, as a label in the bar's colours does."""

    def __init__(self, bar: widgets.FloatingBar, left: int):
        super().__init__(bar)
        self.bar = bar
        self.setGeometry(left, 10, 40, 24)
        self.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        font = painter.font()
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self.bar.theme().text))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "85%")
        painter.end()


def across(floating, edge: int) -> tuple[widgets.FloatingBar, Mark, Mark]:
    """A bar across the side of a page at `edge` (in the window), with a mark over the page and one over the canvas
    (at 248 in the bar, past the side of the page in every test here)."""
    bar = floating(Halves(edge))
    marks = Mark(bar, 30), Mark(bar, 248)
    settle()
    bar.refresh_backdrop()
    return bar, *marks


def glyph(bar, mark, at_least=200) -> list[tuple[int, int]]:
    """The pixels of the bar where `mark` draws its text, at least `at_least` opaque."""
    alpha = bar._content_alpha(1.0)
    box = mark.geometry()
    return [(x, y) for y in range(box.top(), box.bottom() + 1) for x in range(box.left(), box.right() + 1)
            if alpha.pixelColor(x, y).alpha() >= at_least]


def lightness(image, points) -> float:
    return sum(widgets.luminance(QColor(*rgb(image, x, y))) for x, y in points) / len(points)


@needs_lens
def test_over_the_page_and_the_canvas_each_child_takes_the_ink_its_backdrop_needs(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "adaptive", True)
    tones = set()
    # Half the bar over the page, the bar stays dark; most of it over the page, it turns light. Either way the
    # mark over the white page is in dark ink and the one over the canvas in light ink.
    for edge in (200, 290):
        bar, page, canvas = across(floating, edge)
        tones.add(bar.tone)
        image = bar.grab().toImage()
        assert len(glyph(bar, page, 250)) > 20 and len(glyph(bar, canvas, 250)) > 20
        assert lightness(image, glyph(bar, page, 250)) < 0.1, (edge, bar.tone)
        assert lightness(image, glyph(bar, canvas, 250)) > 0.5, (edge, bar.tone)
    assert tones == {"", "light"}


@needs_lens
def test_away_from_a_glyph_the_halo_cannot_be_told_from_a_plain_page_or_canvas(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "adaptive", True)
    for edge in (200, 290):
        bar, *marks = across(floating, edge)
        image = bar.grab().toImage()
        points = []
        for mark in marks:
            drawn = glyph(bar, mark, at_least=1)
            left, right = min(x for x, _ in drawn), max(x for x, _ in drawn)
            middle = (min(y for _, y in drawn) + max(y for _, y in drawn)) // 2
            points += [(x, y) for x in (left - 4, right + 4) for y in range(middle - 3, middle + 4)]
        for mark in marks:
            mark.hide()
        settle()
        seen = bar.grab().toImage()  # the backdrop through the lens, with nothing on it
        assert apart(image, seen, points) <= 12, (edge, bar.tone)


@needs_lens
def test_a_child_keeps_its_side_while_its_backdrop_stays_between_the_two_thresholds(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "adaptive", True)
    # Greys whose luminance lies between each theme's two thresholds: 0.33 between 0.22 and 0.45, 0.10 between
    # 0.06 and 0.16.
    for theme, grey in ((DARK, QColor(156, 156, 156)), (LIGHT, QColor(89, 89, 89))):
        monkeypatch.setattr(themes, "current", theme)
        assert widgets.TONE_DARK_UNDER < widgets.luminance(QColor(156, 156, 156)) < widgets.TONE_LIGHT_OVER
        assert widgets.TONE_DARK_IN_LIGHT < widgets.luminance(QColor(89, 89, 89)) < widgets.TONE_LIGHT_IN_LIGHT
        page = Halves(200, WHITE)
        bar = floating(page)
        mark = Mark(bar, 248)
        sides = []
        for right in (WHITE, grey, BLACK, grey, WHITE):
            page.right = right
            page.repaint()
            bar.refresh_backdrop()
            sides.append(bar._sides[mark])
        light, dark = ("light", "") if theme is DARK else ("", "dark")
        assert sides == [light, light, dark, dark, light], theme.name


@needs_lens
def test_a_click_on_a_child_in_the_other_ink_reaches_it(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "adaptive", True)
    bar = floating(Halves(290))
    button = widgets.IconButton("magnifying-glass-minus", "Smaller", parent=bar)
    button.setGeometry(246, 4, 36, 36)
    button.show()
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    settle()
    bar.refresh_backdrop()
    bar.grab()
    assert bar.tone == "light" and bar._sides[button] == ""  # a light bar, the button over the dark canvas
    host = bar.window()
    at = bar.mapTo(host, button.geometry().center())
    assert host.childAt(at) is button
    QTest.mouseClick(host.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    settle()
    assert clicks == [True]


def pictures_made(monkeypatch) -> list:
    """FloatingBar._pictures, counted: each time the children are drawn again."""
    make, made = widgets.FloatingBar._pictures, []

    def counted(self, ratio):
        made.append(self)
        return make(self, ratio)

    monkeypatch.setattr(widgets.FloatingBar, "_pictures", counted)
    return made


@needs_lens
def test_the_pictures_are_not_made_again_while_the_pages_only_scroll(liquid, floating, monkeypatch):
    monkeypatch.setattr(widgets.glass, "adaptive", True)
    bar, left, _ = across(floating, 200)
    bar.repaint()
    assert bar.tone == "" and bar._sides[left] == "light"  # a dark bar, the mark on the left in dark ink
    made = pictures_made(monkeypatch)
    behind = bar._source
    for edge in range(190, 40, -10):  # the side of the page passes under the mark on the left
        behind.edge = edge
        behind.repaint()
        bar.shift_backdrop(-10, 0)
        bar.repaint()
        bar.refresh_backdrop()
        bar.repaint()
    assert bar.tone == "" and bar._sides[left] == ""  # over the canvas now, in the bar's own light ink
    assert not made  # with the pictures it had
    assert lightness(bar.grab().toImage(), glyph(bar, left, 250)) > 0.5


@needs_lens
def test_the_lens_stays_with_the_bar_while_the_pages_move(liquid, floating):
    page = Page(WHITE, range(0, 200, 7))
    bar = floating(page)
    still = bar.grab().toImage()
    bar.shift_backdrop(0, -5)  # the pages moved 5 px up, and no new sample yet
    moved = bar.grab().toImage()
    page.lines = [top - 5 for top in page.lines]
    page.repaint()
    bar.refresh_backdrop()
    sampled = bar.grab().toImage()
    # The outer part of the rim along the top and the bottom, between the round ends.
    rim = [(x, y) for x in range(30, BAR.width() - 30) for y in (*range(4), *range(BAR.height() - 4, BAR.height()))]
    assert apart(still, moved, rim) > 60  # the lines did move under it
    assert apart(moved, sampled, rim) <= 2


@needs_lens
def test_a_repaint_with_nothing_new_renders_nothing(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))
    asked = renders(monkeypatch)
    bar.refresh_backdrop()
    bar.repaint()
    bar.repaint()  # a hover on a button, say
    assert len(asked) == 1
    bar.shift_backdrop(0, -3)
    bar.repaint()
    assert len(asked) == 2


@needs_lens
def test_the_light_and_the_edge_are_drawn_once(liquid, floating, monkeypatch):
    page = (150, 120, 90)
    bar = floating(Page(QColor(*page)))
    asked = renders(monkeypatch)
    image = bar.grab().toImage()
    spec = asked[-1][0]
    t = bar.theme()
    # As the lens has it at 100%: the page as it is under the faint veil, a light that fades over 1.5 px in from
    # the edge, strongest where the edge faces the top left, and the 1 px edge.
    assert spec.saturation == 1.0
    under = veiled(page, spec)
    facing = 0.84856  # the top edge, towards the light
    light, border = t.glass_highlight, t.glass_border
    lit = (light.red(), light.green(), light.blue())
    edge = mix(mix(under, lit, light.alphaF() * facing), (border.red(), border.green(), border.blue()),
               border.alphaF())
    inside = mix(under, lit, light.alphaF() * facing * (1 - smoothstep(1.0, 2.5, 1.5)))
    x = BAR.width() // 2
    for y, wanted in ((0, edge), (1, inside), (4, under)):
        assert max(abs(a - b) for a, b in zip(rgb(image, x, y), wanted)) <= 3, (y, rgb(image, x, y), wanted)


@needs_lens
def test_a_lens_that_fails_leaves_clear_bars_and_no_exception(liquid, window, workspace, monkeypatch):
    bar = workspace.toolbar
    errors = raised(monkeypatch)
    before = window.settings.get("glass")
    try:
        clear = toolbar_picture(window, workspace, "clear")
        lensed = toolbar_picture(window, workspace, "liquid")
        assert widgets.glass.liquid and bar._lensed is not None and lensed != clear
        monkeypatch.setattr(lens._lens, "begin", lambda *_: 3)  # a frame that does not start: a driver reset
        bar.shift_backdrop(0, -3)  # a new picture is due
        bar.repaint()
        assert not errors and not lens.available() and not widgets.glass.liquid
        settle()  # the events run: every bar samples again, as the Clear glass
        assert bar.grab().toImage() == clear
    finally:
        window.settings.set("glass", before)
    assert not errors


@needs_lens
def test_the_device_is_never_made_in_a_paint_event(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))
    lens.shutdown()  # down, as when the program starts
    monkeypatch.setattr(widgets, "_lens_asked", False)
    painting, started = [], []
    start, paint = lens.start, widgets.FloatingBar.paintEvent

    def painted(self, event) -> None:
        painting.append(self)
        try:
            paint(self, event)
        finally:
            painting.pop()

    monkeypatch.setattr(widgets.FloatingBar, "paintEvent", painted)
    monkeypatch.setattr(lens, "start", lambda: started.append(bool(painting)) or start())
    asked = renders(monkeypatch)
    monkeypatch.setattr(widgets.glass, "style", "clear")
    bar.refresh_backdrop()
    clear = bar.grab().toImage()
    monkeypatch.setattr(widgets.glass, "style", "liquid")
    bar.refresh_backdrop()  # a first sample in the Liquid style asks for the device, for later
    assert not started and not lens.available()
    assert bar.grab().toImage() == clear and not asked  # meanwhile the bar is the Clear one
    assert wait_for(lambda: bar._lensed is not None, timeout=5)  # then the lens's, with nobody asking
    assert started == [False] and lens.available() and asked


@needs_lens
def test_a_grab_at_another_scale_gets_a_lens_picture_of_that_scale(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))
    errors = raised(monkeypatch)
    asked = renders(monkeypatch)
    assert bar.devicePixelRatioF() == 1
    for ratio in (2.0, 1.25):
        picture = QPixmap(QSize(math.ceil(bar.width() * ratio), math.ceil(bar.height() * ratio)))
        picture.setDevicePixelRatio(ratio)
        picture.fill(Qt.GlobalColor.transparent)
        bar.render(picture)
        spec, image = asked[-1]
        assert (spec.width, spec.height, spec.ratio) == (round(BAR.width() * ratio), round(BAR.height() * ratio), ratio)
        assert type(spec.width) is int and type(spec.height) is int  # whole device pixels
        assert image is not None and image.size() == QSize(spec.width, spec.height)
    assert not errors


@needs_lens
def test_the_pictures_move_under_the_lens_by_whole_device_pixels(liquid, floating, monkeypatch):
    bar = floating(Page(WHITE, range(0, 200, 7)))
    asked = renders(monkeypatch)
    # Five logical px are 6.25 device px at 125% and 8.75 at 175%: read between its texels, the sharp page smears.
    for ratio, moved in ((1.0, 5), (1.25, 6), (1.75, 9)):
        bar.refresh_backdrop()  # a sample of its own for each ratio, not yet moved
        places = []
        for _ in range(2):
            picture = QPixmap(QSize(math.ceil(bar.width() * ratio), math.ceil(bar.height() * ratio)))
            picture.setDevicePixelRatio(ratio)
            picture.fill(Qt.GlobalColor.transparent)
            bar.render(picture)
            places.append(asked[-1][0].place)
            bar.shift_backdrop(0, -5)  # the pages moved 5 px up, and no new sample yet
        (x, y), (moved_x, moved_y) = places
        assert moved_x == x and y - moved_y == pytest.approx(moved, abs=1e-9), (ratio, places)


@needs_lens
def test_choosing_liquid_asks_for_the_device_before_any_bar_samples(liquid, monkeypatch):
    monkeypatch.setattr(widgets.FloatingBar, "refresh_backdrop", lambda self: None)  # no bar samples, as at Home
    lens.shutdown()  # down, as when the program starts at Home
    monkeypatch.setattr(widgets, "_lens_asked", False)
    widgets.set_glass("liquid")
    assert not lens.available()  # never made in the call: it takes 0.1 to 0.2 s
    assert wait_for(lens.available, timeout=5)


@needs_lens
def test_leaving_liquid_frees_the_device_and_choosing_it_again_makes_another(liquid, monkeypatch):
    widgets.set_glass("liquid")
    assert lens.available()
    device = lens._lens
    widgets.set_glass("clear")
    assert not lens.available() and device.rhi is None and not device.owned and not device.textures
    widgets.set_glass("liquid")
    assert wait_for(lens.available, timeout=5)


@needs_lens
def test_a_chamfered_bar_gets_a_chamfered_lens(liquid, floating, monkeypatch):
    monkeypatch.setattr(themes, "current", replace(DARK, look=neon.LOOK))
    bar = floating(Page(WHITE, range(0, 200, 7)))
    asked = renders(monkeypatch)
    bar.grab()
    spec, image = asked[-1]
    assert spec.chamfer is True and spec.radius == neon.LOOK.panel and image is not None
