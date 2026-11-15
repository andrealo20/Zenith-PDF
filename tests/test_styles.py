"""Settings > View & Layout > Style: every style reads well in each of its palettes and accents, and changing
style changes the look of everything open at once, never what it does."""

from dataclasses import replace

import pytest
from conftest import settle
from test_integration import window  # noqa: F401  (the fixture)
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel

from zenith_pdf.settings import CHOICES, DEFAULTS, Settings
from zenith_pdf.styles import STYLES
from zenith_pdf.theme import ACCENTS, DARK, LIGHT, ZENITH, Look, _style_sheet, luminance, themes, with_accent
from zenith_pdf.ui.widgets import Switch

PALETTES = [(style.key, scheme) for style in STYLES.values() for scheme in style.schemes]


def ratio(a: str, b: str) -> float:
    first, second = luminance(QColor(a)), luminance(QColor(b))
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def test_every_style_can_be_chosen_and_zenith_is_the_default():
    assert tuple(STYLES) == CHOICES["style"]
    assert DEFAULTS["style"] == "zenith" and STYLES["zenith"] is ZENITH
    assert ZENITH.dark is DARK and ZENITH.light is LIGHT and DARK.look == Look()
    for style in STYLES.values():
        assert style.schemes, style.key
        for scheme in style.schemes:
            palette = style.palette(scheme)
            assert palette.name == scheme and palette.look.key == style.key


def test_a_stored_style_that_is_gone_reads_as_zenith(tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("style", "vaporwave")
    assert Settings(store).get("style") == "zenith"


@pytest.mark.parametrize(("key", "scheme"), PALETTES)
def test_text_reads_in_every_palette_and_accent(qt_app, key, scheme):
    """As the app asks of its own: text 7:1 on every panel, muted text 4.5:1 (6:1 on the window), accent text
    on tinted fills 4.5:1, the text on the accent 4.5:1, hovered too."""
    base = STYLES[key].palette(scheme)
    for accent in ACCENTS:
        t = with_accent(base, accent)
        for back in (t.window, t.surface, t.raised, t.field):
            assert ratio(t.text, back) >= 7, (key, scheme, accent, back)
            assert ratio(t.text_muted, back) >= 4.5, (key, scheme, accent, back)
        assert ratio(t.text_muted, t.window) >= 6, (key, scheme, accent)
        assert ratio(t.danger, t.window) >= 4.5, (key, scheme, accent)
        assert ratio(t.accent_ink, t.window) >= 4.5, (key, scheme, accent)
        for fill in (t.accent_fill, t.accent_fill_hover):
            assert ratio(t.accent_text, fill) >= 4.5, (key, scheme, accent, fill)
        assert _style_sheet(t)  # every rule builds


def test_changing_style_restyles_what_is_open(window):
    settings = window._settings
    before = settings.get("style"), settings.theme
    try:
        for key, style in STYLES.items():
            settings.set("style", key)
            settle()
            assert themes.style is style and themes.current.look.key == key
            assert themes.current.name in style.schemes
            assert themes.current.window in qt_app_sheet()
    finally:
        settings.set("style", before[0])
        settings.theme = before[1]
        settle()
    assert themes.style is STYLES[before[0]]


def qt_app_sheet() -> str:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance().styleSheet()


def restyled(**look):
    """The theme in use with some of its Look changed."""
    return replace(themes.current, look=replace(themes.current.look, **look))


def test_each_switch_style_draws_its_own(qt_app):
    before = themes.current
    pictures = []
    try:
        for kind in ("zenith", "material", "dot", "neon"):
            themes.apply(restyled(switch=kind))
            switch = Switch(True)
            switch.resize(switch.sizeHint())
            settle()
            pictures.append(switch.grab().toImage())
    finally:
        themes.apply(before)
    assert all(a != b for i, a in enumerate(pictures) for b in pictures[i + 1:])


def test_dot_titles_draw_the_large_titles_as_dots(qt_app):
    before = themes.current
    label = QLabel("Settings")
    label.setObjectName("SettingsTitle")
    try:
        themes.apply(restyled(dot_titles=False))
        label.ensurePolished()
        label.resize(label.sizeHint())
        hint, plain = label.sizeHint(), label.grab().toImage()
        themes.apply(restyled(dot_titles=True))
        settle()
        assert label.sizeHint() == hint
        assert label.grab().toImage() != plain
        themes.apply(restyled(dot_titles=False))
        settle()
        assert label.grab().toImage() == plain  # the filter goes with the style
    finally:
        themes.apply(before)


def test_folio_icons_all_exist(qt_app):
    from zenith_pdf import icons

    for name in STYLES["folio"].dark.look.icons.values():
        assert name in icons.CUSTOM or name in icons._phosphor(), name


def test_solid_styles_keep_their_edge():
    """A solid bar draws its edge in glass_border once the style has no glass: those that had the border keep it."""
    for style in STYLES.values():
        if style.key == "folio":
            continue
        for scheme in style.schemes:
            palette = style.palette(scheme)
            if not palette.look.glass:
                assert QColor(palette.glass_border) == QColor(palette.border), (style.key, scheme)


def test_five_style_tiles_fit_the_settings_page(window):
    from zenith_pdf.ui.settings_page import StylePicker

    settings = window._settings
    before = settings.get("style")
    page = window.settings_window()
    page.show()
    try:
        for key in STYLES:
            settings.set("style", key)
            settle()
            picker = page.findChild(StylePicker)
            row = picker.parentWidget()
            assert picker.sizeHint().width() <= row.contentsRect().width(), key
            assert not page.scroll.horizontalScrollBar().isVisible(), key
    finally:
        page.hide()
        settings.set("style", before)
        settle()
