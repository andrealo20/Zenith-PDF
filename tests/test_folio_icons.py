"""A style's own icons: names it draws instead, the pale bodies of its tool icons, and a tool's ink shown only while
it is on, with a short line of that ink under it."""

from dataclasses import replace

import pytest
from conftest import settle
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QWidget

from zenith_pdf import icons
from zenith_pdf.icons import PALETTE, icon, readable
from zenith_pdf.theme import themes
from zenith_pdf.ui.toolbar import ToolButton

FOLIO_ICONS = {"pen-nib": "z-folio-pen", "marker-circle": "z-folio-marker", "eraser": "z-folio-eraser",
               "hand": "hand-pointing"}


def restyled(**look):
    """The theme in use with some of its Look changed."""
    return replace(themes.current, look=replace(themes.current.look, **look))


@pytest.fixture
def theme_kept(qt_app):
    before, colourful = themes.current, icons.style.colourful
    yield
    icons.style.colourful = colourful
    themes.apply(before)


def picture(name: str, size: int, color: str | None = None) -> QImage:
    return icon(name, color).pixmap(size, size).toImage()


def test_a_style_can_give_its_own_icons(theme_kept):
    hand = picture("hand", 24)
    themes.apply(restyled(icons={"hand": "hand-pointing"}))
    assert picture("hand", 24) == picture("hand-pointing", 24)
    assert picture("hand", 24) != hand
    themes.apply(restyled(icons={}))
    assert picture("hand", 24) == hand


def test_duotone_fills_the_body(theme_kept):
    def alphas() -> tuple[int, int]:
        eraser = picture("z-folio-eraser", 48, "#000000")  # the middle of its long body, on the diagonal
        square = picture("square", 48, "#000000")
        return eraser.pixelColor(30, 18).alpha(), square.pixelColor(24, 24).alpha()

    themes.apply(restyled(duotone=0.16))
    for alpha in alphas():
        assert 20 <= alpha <= 60
    themes.apply(restyled(duotone=0.0))
    assert alphas() == (0, 0)


def test_hue_follows_the_name_asked_for(theme_kept):
    icons.style.colourful = True
    themes.apply(restyled(icons=FOLIO_ICONS, duotone=0.16, icon_tone="muted"))
    t = themes.current
    # The hand is drawn as hand-pointing, which has no hue of its own: only the name asked for gives it blue.
    for name, hue in (("pen-nib", "purple"), ("hand", "blue")):
        colour = QColor(PALETTE[hue][1 if t.name == "dark" else 0])
        image = picture(name, 48)
        pixels = [image.pixelColor(x, y) for x in range(48) for y in range(48)]
        solid = {pixel.rgb() for pixel in pixels if pixel.alpha() == 255}
        assert colour.rgb() in solid
        assert QColor(t.text).rgb() not in solid and QColor(t.text_muted).rgb() not in solid


def test_folio_tool_shows_its_ink_only_when_on(theme_kept):
    themes.apply(restyled(idle_ink=False, icon_tone="muted", on_mark="ink"))
    t = themes.current
    holder = QWidget()  # never shown: the button is grabbed off screen
    button = ToolButton("pen", holder)
    ink = readable(QColor("#ae00cd").getRgbF()[:3], t.raised, t.text)
    button.set_state(False, False, "#ae00cd")
    assert button._inks(t, None) == (t.text_muted, ink)

    button.set_state(True, False, "#ae00cd")
    settle()
    image = button.grab().toImage()
    mark = image.pixelColor(18, button.height() - 3)
    assert mark.alpha() > 200 and abs(mark.hsvHue() - QColor(ink).hsvHue()) <= 12
    assert button._icons  # drawn for this look

    themes.apply(restyled(idle_ink=True, icon_tone="text", on_mark=""))
    assert not button._icons  # a new style draws its icons afresh
    assert button._inks(themes.current, None) == (ink, ink)
    holder.deleteLater()
