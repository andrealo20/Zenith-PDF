"""The Folio style: a paper notebook, where the page comes first and the chrome stays quiet.

The controls are opaque white cards that float over the pages on a wide, faint shadow with no outline, their
corners rounded rectangles rather than capsules. Text is near black and the icons a softer slate; each tool is
drawn as a small object with a pale body, the writing tools lying on a 45 degree diagonal with the tip at the
lower left. Colour shows only where it means something: the tool in use takes its own ink, in its outline, a pale
tint inside it and a short line under it, while the blue accent is kept for focus, the main button and links, and
selections stay a neutral grey. Large titles are set in a bold serif, everything else in a plain grotesque.

The text takes Inter, then the system's own sans; the titles take Source Serif 4, then Georgia or Charter, which
ship with Windows and macOS. Each is used only when installed: nothing is bundled.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ..core.platforms import first_font
from ..theme import Look, Style, Theme

TEXT_FONTS = ("Inter", "Segoe UI Variable Text", "Segoe UI", ".AppleSystemUIFont", "Helvetica Neue", "Noto Sans",
              "Cantarell", "Ubuntu", "DejaVu Sans")
SERIF = ("Source Serif 4", "Georgia", "Charter", "Noto Serif", "DejaVu Serif")


def _sheet(t: Theme) -> str:
    serif = first_font(SERIF, "serif")
    return f"""
    /* Large titles in a bold serif; panel and dialog titles stay in the bold sans. */
    #SettingsTitle, #ChooserTitle, #HomeSection, #AboutName {{
        font-family: "{serif}"; font-weight: 700; letter-spacing: 0px;
    }}

    /* The chosen settings page: a neutral fill and near-black text, not the accent. */
    #SettingsNavList::item:selected {{ background: {t.selection}; color: {t.text}; }}

    /* The settings search is a rounded field with a cool line, not a pill. */
    QLineEdit#SettingsSearch {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 8px; }}
    QLineEdit#SettingsSearch:focus {{ border-color: {t.focus or t.accent}; }}

    /* Floating bars: no accent chip under checked buttons; their icon and the ink line mark them. */
    #FloatingBar QToolButton:checked {{ background: {t.glass_checked}; }}
    #FloatingBar QToolButton#BackChip {{ background: transparent; }}
    #FloatingBar QToolButton#BackChip:hover {{ background: {t.glass_hover}; }}

    /* Hover rings in the slate, never the accent. */
    #QuickCard:hover, #PanelRow:hover {{ border-color: {t.text_muted}; }}

    QToolTip {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 6px; padding: 5px 8px; }}
    """


LOOK = Look(
    key="folio",
    fonts=TEXT_FONTS,
    title_fonts=(),  # the serif goes only on the large titles, through the sheet
    label_fonts=(),
    title_weight=700,
    title_spacing=-0.2,
    label_upper=False,  # sentence case, not letterspaced
    label_spacing=0.0,
    control=8,
    item=8,
    overlay=12,
    card=10,
    panel=12,
    chip=6,
    tab=8,
    check=5,
    pill_buttons=False,
    capsules=False,  # rounded rectangles: floating bars take `panel`
    chamfer=False,
    glass=False,  # opaque white cards
    shadow="soft",
    icon_weight="",
    switch="zenith",
    canvas="",
    dot_titles=False,
    tint=(0, 0),  # selection stays neutral grey, whatever the accent
    checked_icon="accent",
    rail=False,
    on_mark="ink",
    icons={
        "pen-nib": "z-folio-pen",
        "marker-circle": "z-folio-marker",
        "eraser": "z-folio-eraser",
        "text-t": "z-folio-text",
        "cursor": "z-folio-select",
        "z-lasso": "z-folio-lasso",
        "image": "z-folio-picture",
        "microphone": "z-folio-mic",
        "ruler": "z-folio-ruler",
        "z-tape": "z-folio-tape",
        "z-laser": "z-folio-laser",
        "hand": "hand-pointing",
    },
    duotone=0.16,  # the pale body of tool icons: slate at 16% on white comes out a light grey
    idle_ink=False,  # idle tools are all slate; only the one in use shows its colour
    icon_tone="muted",
    accents={
        "blue": ("#6f97ff", "#4278ff"),
        "teal": ("#2ec4b6", "#0e7f78"),
        "violet": ("#a98bff", "#7448e0"),
        "pink": ("#ff6fa5", "#cf3577"),
        "salmon": ("#ff7f6e", "#d24a36"),
        "orange": ("#ffa04a", "#c25a0a"),
        "green": ("#47c97a", "#198a46"),
    },
    derive=None,
    sheet=_sheet,
)

DARK = Theme(
    name="dark",
    scheme=Qt.ColorScheme.Dark,
    window="#1c1e23",  # a neutral cool charcoal
    surface="#22252b",
    raised="#2b2e35",  # the floating cards, lifted by tone
    canvas="#131417",  # darker than the chrome
    border="#3b404b",
    text="#eef0f4",
    text_muted="#b3bac7",  # a light slate: the icons' colour
    text_disabled="#6b7280",
    hover="rgba(255, 255, 255, 18)",
    pressed="rgba(255, 255, 255, 32)",
    accent="#6f97ff",
    accent_hover="#8aabff",
    accent_text="#ffffff",
    accent_ink="#9db8ff",
    selection="rgba(255, 255, 255, 26)",
    scrollbar="rgba(255, 255, 255, 60)",
    search_hit=QColor(255, 214, 0, 90),
    search_current=QColor(255, 140, 0, 150),
    glass_base=QColor("#2b2e35"),  # no glass: the bars are solid cards
    glass_alpha=1.0,
    glass_clear_alpha=1.0,
    glass_highlight=QColor(0, 0, 0, 0),
    glass_border=QColor(255, 255, 255, 20),  # the solid bars' only edge
    glass_text_muted="#b3bac7",
    glass_solid=QColor("#2b2e35"),
    glass_hover="rgba(255, 255, 255, 20)",
    glass_pressed="rgba(255, 255, 255, 34)",
    glass_checked="rgba(255, 255, 255, 14)",  # a hint of a chip: the ink line and the ink icon mark the tool
    danger="#ff6b81",
    danger_fill="#d42f57",
    danger_hover="#bf2a4e",
    field="#25282f",
    track="rgba(255, 255, 255, 14)",
    segment="#3a3e47",
    paper="#fcfaf4",  # a warm cream, for previews drawn as on a page
    shadow=QColor(0, 0, 0, 110),
    look=LOOK,
)

LIGHT = Theme(
    name="light",
    scheme=Qt.ColorScheme.Light,
    window="#f6f6f8",
    surface="#fbfbfc",
    raised="#ffffff",  # the floating cards
    canvas="#e8ebf0",  # a cool grey behind the pages
    border="#d5dbe3",
    text="#171a20",
    text_muted="#444f60",  # the slate of idle icons: 7.7:1 on the window
    text_disabled="#aab5c6",
    hover="rgba(23, 26, 32, 12)",  # the text colour as a state layer
    pressed="rgba(23, 26, 32, 24)",
    accent="#4278ff",
    accent_hover="#5f8cff",
    accent_text="#ffffff",
    accent_ink="#2456d6",
    selection="rgba(68, 79, 96, 30)",  # a neutral slate
    scrollbar="rgba(23, 26, 32, 64)",
    search_hit=QColor(255, 214, 0, 110),
    search_current=QColor(255, 140, 0, 160),
    glass_base=QColor("#ffffff"),
    glass_alpha=1.0,
    glass_clear_alpha=1.0,
    glass_highlight=QColor(0, 0, 0, 0),
    glass_border=QColor(23, 26, 32, 18),  # nearly invisible: the shadow sets the cards apart
    glass_text_muted="#444f60",
    glass_solid=QColor("#ffffff"),
    glass_hover="rgba(23, 26, 32, 12)",
    glass_pressed="rgba(23, 26, 32, 22)",
    glass_checked="rgba(23, 26, 32, 10)",
    danger="#c8284f",
    danger_fill="#d42f57",
    danger_hover="#bf2a4e",
    field="#ffffff",
    track="rgba(23, 26, 32, 10)",
    segment="#ffffff",
    paper="#fcfaf4",
    shadow=QColor(23, 26, 32, 38),  # wide and faint
    look=LOOK,
)

SUMMARY = "White floating cards, two-tone slate tool icons, and the tool in use drawn in its own ink."
STYLE = Style("folio", "Folio", SUMMARY, DARK, LIGHT)
