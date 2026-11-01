"""The Material style: Material You (Material 3) as Pixel phones draw it.

From Material 3 and its dynamic colour, the whole palette is made from the accent. The accent's hue seeds the
tonal palettes of Android's "tonal spot" scheme (primary, secondary, neutral and neutral variant, each at its own
chroma), and every colour of the app is a tone of one of them, so even the greys carry a trace of the accent and
change with it. Depth is told by tone, containers lighter in the dark and whiter in the light, with soft shadows
only under what floats. From the components: pill buttons, tonal unless they are the main action; the chosen row
of a list or of a navigation column in a pill; filled text fields, square at the bottom, with a line under them
that turns into the accent on focus; underlined tabs; inverse tooltips; settings in rows of cards, as Android's
own settings show them; the bar-shaped slider handle and the switch whose thumb grows when on.

Material's colour space (HCT, built on CAM16) is approximated in OKLCH: a tone is CIELAB lightness L*, as in HCT,
so the contrast between two tones is exact, and the hue kept is OKLCH's, a few degrees from CAM16's on purples.
Google Sans and Roboto are taken when installed; neither ships with Windows or macOS, where the system's own
sans takes their place.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ..theme import ACCENTS, Look, Style, Theme, luminance

# The plain Segoe UI before the variable one: Qt on Windows draws Segoe UI Variable at weight 600 in bold, the
# plain one in semibold, the nearer to Material's medium labels.
TEXT_FONTS = ("Google Sans Flex", "Google Sans Text", "Google Sans", "Roboto Flex", "Roboto", "Segoe UI",
              ".AppleSystemUIFont", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
TITLE_FONTS = ("Google Sans Flex", "Google Sans Display", "Google Sans", "Roboto Flex", "Roboto",
               "Segoe UI Variable Display", "Segoe UI", ".AppleSystemUIFont", "Noto Sans", "Cantarell", "Ubuntu",
               "DejaVu Sans")

# The chroma of each tonal palette in Android's tonal spot scheme, in HCT units.
PRIMARY, SECONDARY, NEUTRAL, NEUTRAL_VARIANT = 36, 16, 6, 8
# Material's error palette is fixed: tones 40 and 80.
ERROR_40, ERROR_80 = "#b3261e", "#f2b8b5"


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _encoded(channel: float) -> int:
    channel = min(1.0, max(0.0, channel))
    return round(255 * (channel * 12.92 if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055))


def _hue(colour: QColor) -> float:
    """The OKLCH hue of `colour`, in degrees."""
    r, g, b = (_linear(c) for c in (colour.redF(), colour.greenF(), colour.blueF()))
    l, m, s = (v ** (1 / 3) for v in (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                                      0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                                      0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b))
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    b = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return math.degrees(math.atan2(b, a)) % 360


def _oklch(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    """Linear sRGB of an OKLCH colour; channels may fall outside 0 to 1."""
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def _at_luminance(chroma: float, hue: float, target: float) -> tuple[float, float, float]:
    """The colour of this OKLCH chroma and hue whose luminance is `target`, found by halving."""
    low, high = 0.0, 1.0
    for _ in range(40):
        lightness = (low + high) / 2
        r, g, b = _oklch(lightness, chroma, hue)
        if 0.2126 * r + 0.7152 * g + 0.0722 * b < target:
            low = lightness
        else:
            high = lightness
    return _oklch(lightness, chroma, hue)


def _tone(hue: float, chroma: float, tone: float) -> str:
    """Tone `tone` (CIELAB L*, 0 black to 100 white) of the tonal palette of `hue` at HCT `chroma`. Where sRGB
    has no such colour the chroma is cut, keeping the hue and the tone, as HCT does."""
    target = ((tone + 16) / 116) ** 3 if tone > 8 else tone * 27 / 24389
    chroma = chroma * (0.0014 + 0.000028 * chroma)  # HCT chroma in OKLCH's, fitted on Material's baseline seed

    def inside(rgb):
        return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)

    rgb = _at_luminance(chroma, hue, target)
    if not inside(rgb):
        low, high = 0.0, chroma
        for _ in range(25):
            middle = (low + high) / 2
            low, high = (middle, high) if inside(_at_luminance(middle, hue, target)) else (low, middle)
        rgb = _at_luminance(low, hue, target)
    return QColor(*(_encoded(c) for c in rgb)).name()


assert _tone(_hue(QColor("#6750a4")), 48, 40) == "#6750a4"  # Material's baseline seed comes back as itself


def _contrast(a: str, b: str) -> float:
    first, second = luminance(QColor(a)), luminance(QColor(b))
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _mix(base: str, over: str, share: float) -> str:
    """`over` laid on `base` at `share` opacity: a Material state layer, flattened (QSS can't stack fills)."""
    a, b = QColor(base), QColor(over)
    return QColor.fromRgbF(*(x + (y - x) * share for x, y in (
        (a.redF(), b.redF()), (a.greenF(), b.greenF()), (a.blueF(), b.blueF())))).name()


def _rgba(colour: str, alpha: int) -> str:
    c = QColor(colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


@lru_cache(maxsize=32)
def _scheme(name: str, seed: str) -> Theme:
    """The whole "dark" or "light" palette grown from the hue of `seed`, by Material's colour roles (2021 tones).
    Cached: the style picker asks for it on every paint."""
    hue, dark = _hue(QColor(seed)), name == "dark"

    def role(chroma: float, dark_tone: float, light_tone: float) -> str:
        return _tone(hue, chroma, dark_tone if dark else light_tone)

    window = role(NEUTRAL, 12, 94)  # surface container: the chrome round everything
    surface = role(NEUTRAL, 6, 98)  # surface: the panels
    raised = role(NEUTRAL, 17, 100)  # surface container high, and lowest in the light: menus, cards, bars
    field = role(NEUTRAL, 22, 90)  # surface container highest: filled fields, tracks
    text = role(NEUTRAL, 90, 10)  # on surface
    muted = role(NEUTRAL_VARIANT, 80, 30)  # on surface variant
    border = role(NEUTRAL_VARIANT, 30, 80)  # outline variant
    accent = role(PRIMARY, 80, 40)  # primary
    on_accent = role(PRIMARY, 20, 100)  # on primary: dark on the pastel accent of the dark scheme
    selection = role(SECONDARY, 30, 90)  # secondary container: the tonal fill of what is chosen
    ink = accent if _contrast(accent, selection) >= 4.5 else role(PRIMARY, 90, 30)
    shadow = QColor(0, 0, 0, 110 if dark else 46)
    return Theme(
        name=name,
        scheme=Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light,
        window=window,
        surface=surface,
        raised=raised,
        canvas=role(NEUTRAL, 4, 90),  # darker than the chrome in the dark, a tinted grey under white pages
        border=border,
        text=text,
        text_muted=muted,
        text_disabled=_mix(window, text, 0.38),  # Material's disabled content: the text at 38%
        hover=_rgba(text, 20),  # Material's state layers: 8% of the content colour
        pressed=_rgba(text, 26),  # 10%
        accent=accent,
        accent_hover=_mix(accent, on_accent, 0.08),
        accent_text=on_accent,
        accent_ink=ink,
        selection=selection,
        scrollbar=_rgba(muted, 110),
        search_hit=QColor(255, 214, 0, 90 if dark else 110),  # on the pages, which keep their colours
        search_current=QColor(255, 140, 0, 150 if dark else 160),
        glass_base=QColor(raised),  # no glass: every bar is solid, in the colour of its container
        glass_alpha=1.0,
        glass_clear_alpha=1.0,
        glass_highlight=QColor(0, 0, 0, 0),
        glass_border=QColor(border),
        glass_text_muted=muted,
        glass_solid=QColor(raised),
        glass_hover=_rgba(text, 20),
        glass_pressed=_rgba(text, 26),
        glass_checked=selection,
        danger=ERROR_80 if dark else ERROR_40,
        danger_fill=ERROR_40,  # under white text in both schemes
        danger_hover=_mix(ERROR_40, "#ffffff", 0.08),
        field=field,
        track=field,
        segment=selection,
        paper="#ffffff",
        shadow=shadow,
        look=LOOK,
        focus=role(SECONDARY, 80, 40),  # secondary: Material's focus ring
        segment_text=role(SECONDARY, 90, 10),  # on secondary container
        canvas_mark=border,
        page_edge="rgba(0, 0, 0, 0)",  # the light pages have a shadow, the dark ones stand out by themselves
    )


def _derive(theme: Theme, seed: QColor) -> Theme:
    return _scheme(theme.name, seed.name())


def _sheet(t: Theme) -> str:
    """Material's own rules for the standard widgets, over the shared ones."""
    on_chosen = t.segment_text
    tonal_hover, tonal_pressed = _mix(t.selection, on_chosen, 0.08), _mix(t.selection, on_chosen, 0.12)
    field_hover = _mix(t.field, t.text, 0.08)
    card_hover = _mix(t.raised, t.text, 0.08)
    red = _rgba(t.danger, 26)
    accent = t.accent
    # The floating action button's primary container: its icon keeps the text colour, which reads on it.
    container = _tone(_hue(QColor(accent)), PRIMARY, 30 if t.name == "dark" else 90)
    return f"""
    /* Filled text fields: square at the bottom, with a line that turns into a 2 px accent one on focus. Paddings
       give back the pixels the borders take, so the text never moves and the fields stay 32 px tall. */
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QToolButton#MenuButton {{
        background: {t.field}; border: none; border-bottom: 1px solid {t.text_muted};
        border-radius: 0; border-top-left-radius: 4px; border-top-right-radius: 4px;
    }}
    QLineEdit, QComboBox {{ padding: 7px 12px 5px 12px; }}
    QSpinBox, QDoubleSpinBox {{ padding: 5px 10px 4px 10px; }}
    QPlainTextEdit {{ padding: 5px 4px 4px 4px; }}
    QToolButton#MenuButton {{ padding: 6px 28px 5px 12px; }}
    QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
    QToolButton#MenuButton:hover {{ background: {field_hover}; border-bottom-color: {t.text}; }}
    QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-bottom: 2px solid {accent}; padding-bottom: 4px; }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-bottom: 2px solid {accent}; padding-bottom: 3px; }}
    QPlainTextEdit:focus {{ border-bottom: 2px solid {accent}; padding-bottom: 3px; }}
    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled {{
        background: {_rgba(t.text, 10)}; border-bottom-color: {t.text_disabled};
    }}
    /* The tool menu rounds its value boxes all round: a line under them would bend, so it shows on focus only. */
    QComboBox#ValueBox {{ border-bottom-color: transparent; }}
    QComboBox#ValueBox:focus, QComboBox#ValueBox:on {{ border-bottom-color: {accent}; }}
    /* The search bars are pills, with no line. */
    QLineEdit#SettingsSearch {{ background: {t.field}; border: 1px solid {t.field}; padding: 7px 14px; }}
    QLineEdit#SettingsSearch:hover {{ background: {field_hover}; border-color: {field_hover}; }}
    QLineEdit#SettingsSearch:focus {{ border-color: {accent}; }}
    QLineEdit#PageField {{ background: {t.field}; border: 1px solid {t.field}; border-radius: 8px; }}
    QLineEdit#PageField:hover {{ background: {field_hover}; border-color: {field_hover}; }}
    QLineEdit#PageField:focus {{ background: {t.field}; border-color: {accent}; }}

    /* Tonal buttons; the main action stays the accent. */
    QPushButton {{ background: {t.selection}; color: {on_chosen}; border: 1px solid {t.selection}; }}
    QPushButton:hover {{ background: {tonal_hover}; border-color: {tonal_hover}; }}
    QPushButton:pressed {{ background: {tonal_pressed}; border-color: {tonal_pressed}; }}
    QPushButton:disabled {{ background: {_rgba(t.text, 31)}; color: {t.text_disabled}; border-color: transparent; }}
    QPushButton#MenuButtonWide, QPushButton#MenuButtonWide:hover {{ color: {t.text}; border-color: {t.field}; }}
    QPushButton#DangerText {{ background: transparent; border-color: transparent; }}
    QPushButton#DangerText:hover {{ background: {red}; border-color: transparent; }}
    QToolButton#RoundAccent {{ background: {container}; border-radius: 12px; }}
    QToolButton#RoundAccent:hover {{ background: {_mix(container, t.text, 0.08)}; }}

    /* Underlined tabs: the document tabs as Material's primary tabs, those in dialogs as its secondary ones. */
    QTabBar::tab {{
        background: transparent; padding: 7px 6px 4px 12px; border: none; border-bottom: 3px solid transparent;
        border-radius: 0;
    }}
    QTabBar::tab:selected {{ background: transparent; color: {t.accent_ink}; border-bottom-color: {accent}; }}
    QTabBar[home="true"]::tab:selected {{ color: {t.text_muted}; border-bottom-color: transparent; }}
    QTabWidget QTabBar::tab {{
        padding: 7px 14px 5px 14px; border: none; border-bottom: 2px solid transparent; border-radius: 0;
    }}
    QTabWidget QTabBar::tab:selected {{
        background: transparent; color: {t.accent_ink}; border-bottom-color: {accent};
    }}
    QToolButton#ChooserTab:checked {{ color: {t.accent_ink}; }}

    /* What is chosen sits in a pill of the secondary container. */
    QTreeView {{ show-decoration-selected: 0; }}  /* the pill alone, not a square of it under the branch arrow */
    QTreeView::item:selected, QListView::item:selected {{ background: {t.selection}; color: {on_chosen}; }}
    #SettingsNavList::item {{ border-radius: 21px; padding: 4px 16px; }}  /* 21: the Guide's rows are 42 px */
    QTabBar QToolButton {{ border-radius: 10px; }}  /* the tab's 21 px close button */
    #SettingsNavList::item:selected {{ background: {t.selection}; color: {on_chosen}; }}
    QPushButton#HomeNav {{ border-radius: 18px; color: {t.text}; }}
    QPushButton#HomeNav:checked {{ background: {t.selection}; }}
    QListWidget#AssistantList::item {{ border-radius: 24px; }}
    #FloatingBar QToolButton:checked {{ border-radius: 12px; }}
    #Rail QToolButton {{ border-radius: 16px; }}

    QMenu::item {{ border-radius: 12px; }}
    QMenu::separator {{ margin: 4px 12px; }}
    QScrollBar::handle {{ border-radius: 3px; }}
    QScrollBar::handle:vertical {{ margin: 0 1px; }}
    QScrollBar::handle:horizontal {{ margin: 1px 0; }}
    QToolTip {{ background: {t.text}; color: {t.window}; border: none; padding: 5px 8px; }}

    /* Group headings in the accent, as Android's settings show them. */
    #HomeNavHeading, QLabel#AssistantSection, #HelpHeading {{
        color: {t.accent_ink}; letter-spacing: 0px; text-transform: none;
    }}

    /* Settings in rows of cards on the tinted window. */
    #SettingRow {{ background: {t.raised}; border-radius: 12px; padding: 0 16px; }}
    #SettingRow[flash="true"] {{ background: {t.selection}; border-radius: 12px; }}
    #SettingLine {{ background: transparent; }}

    /* No dividers: the columns stand apart by their tone. */
    #SettingsNav, #HomeSide {{ border-right-color: transparent; }}
    #HomeAside {{ border-left-color: transparent; }}
    QLabel#DialogTitle {{ font-size: 14pt; }}
    QTextEdit#SpellingPreview, QTextBrowser#SummaryText, QListWidget#AttachmentList {{
        border-color: {t.field}; border-radius: 12px;
    }}
    #QuickCard {{ background: {t.raised}; border-color: {t.raised}; }}
    #QuickCard:hover {{ background: {card_hover}; border-color: {card_hover}; }}
    #TipCard {{ border-color: {t.raised}; }}

    QCheckBox::indicator {{ width: 14px; height: 14px; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 10px; }}
    QRadioButton::indicator:checked {{
        width: 16px; height: 16px; border: 2px solid {accent};
        background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                    stop: 0 {accent}, stop: 0.6 {accent}, stop: 0.68 transparent);
    }}
    /* The slider of Material 3 Expressive: a thick track, the rest in the secondary container, and a bar for
       a handle. */
    QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {t.selection}; }}
    QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 3px; }}
    QSlider::add-page:horizontal {{ background: {t.selection}; border-radius: 3px; }}
    QSlider::groove:vertical {{ width: 6px; border-radius: 3px; background: {t.selection}; }}
    QSlider::add-page:vertical {{ background: {accent}; border-radius: 3px; }}
    QSlider::sub-page:vertical {{ background: {t.selection}; border-radius: 3px; }}
    QSlider::handle {{ background: {accent}; border: none; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 4px; height: 20px; margin: -7px 0; }}
    QSlider::handle:vertical {{ width: 20px; height: 4px; margin: 0 -7px; }}
    QSlider::handle:hover {{ background: {t.accent_ink}; }}
    """


# Each accent key's primary tones 80 and 40, from the hue of Zenith's shade of it.
ACCENT_SHADES = {key: (_tone(_hue(QColor(dark)), PRIMARY, 80), _tone(_hue(QColor(dark)), PRIMARY, 40))
                 for key, (_, dark, _light) in ACCENTS.items()}

LOOK = Look(
    key="material",
    fonts=TEXT_FONTS,
    title_fonts=TITLE_FONTS,
    title_weight=400,  # large and regular, as Google Sans headlines
    title_spacing=0.0,
    label_upper=False,  # sentence case everywhere
    control=8,  # the fields round only their top corners, by 4 (the sheet)
    item=12,
    overlay=16,
    card=12,
    panel=28,
    chip=8,
    tab=0,  # underlined, with no box
    check=2,
    pill_buttons=True,
    capsules=True,
    chamfer=False,
    glass=False,
    shadow="soft",
    icon_weight="",
    switch="material",
    canvas="",
    dot_titles=False,
    tint=(0, 0),  # derive picks the selection: the secondary container
    checked_icon="text",
    rail=False,
    accents=ACCENT_SHADES,
    derive=_derive,
    sheet=_sheet,
)

BLUE = ACCENT_SHADES["blue"]
SUMMARY = "Every colour drawn from the accent, tonal surfaces, pill buttons and large round shapes."
STYLE = Style("material", "Material", SUMMARY, _scheme("dark", BLUE[0]), _scheme("light", BLUE[1]))
