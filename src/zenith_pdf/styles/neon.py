"""The Neon style: neon noir, a blue-black chrome where the accent behaves like a lit tube.

From cyberpunk and synthwave interfaces and the screen graphics of TRON: Legacy it takes the dark, slightly violet
ground, saturated accents with dark text on lit fills, cool hairlines, chamfered corners on the painted surfaces,
a glow round floating bars instead of a shadow, a line of light along their top edge, a faint grid behind the
pages, letterspaced DIN capitals for the small labels and a mono face for numbers. It leaves out flicker,
scanlines and glitches, and nothing glows on text, so an hour of reading stays comfortable.

It has no light palette: a glow needs a dark ground, and on a light one it reads as a smudge.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from ..core.platforms import first_font
from ..theme import Look, Style, Theme

MONO = ("Cascadia Mono", "SF Mono", "Menlo", "Noto Sans Mono", "DejaVu Sans Mono", "Consolas")
# Bahnschrift is Windows' DIN 1451. Windows lists its named instances as families of their own; elsewhere the
# chain falls to the nearest condensed sans the system ships.
CONDENSED = ("Bahnschrift SemiBold SemiCondensed", "Bahnschrift", "Avenir Next Condensed", "Ubuntu Condensed",
             "DejaVu Sans Condensed", "Noto Sans")
LINE = "#545c8a"  # the bottom line of fields and the edge of plain buttons: 3:1 on the field, where the border is 1.5


def _rgba(colour: str, alpha: int) -> str:
    c = QColor(colour)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _sheet(t: Theme) -> str:
    accent, focus, mono = t.accent, t.focus or t.accent, first_font(MONO, "mono")
    return f"""
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit#SpellingPreview,
    QTextBrowser#SummaryText, QListWidget#AttachmentList {{
        background: {t.field}; border: 1px solid {t.border}; border-bottom-color: {LINE};
    }}
    QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: {LINE}; border-bottom-color: {t.text_muted};
    }}
    /* Focus lights the edge and doubles the bottom line; a pixel less padding below keeps the text still. */
    QLineEdit:focus, QComboBox:focus, QComboBox:on {{
        border-color: {focus}; border-bottom: 2px solid {focus}; padding-bottom: 4px;
    }}
    QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {focus}; border-bottom: 2px solid {focus}; padding-bottom: 3px;
    }}
    QLineEdit#PageField {{ font-family: "{mono}"; }}
    QLineEdit#PageField:focus {{ padding-bottom: 2px; }}
    QLineEdit#SettingsSearch {{ border-radius: 2px; background: {t.field}; }}
    QLineEdit#SettingsSearch:focus {{ padding-bottom: 6px; }}

    QPushButton {{ background: transparent; border: 1px solid {LINE}; }}
    QPushButton:hover {{ background: {t.hover}; border-color: {accent}; color: {t.accent_ink}; }}
    QPushButton:pressed {{ background: {t.pressed}; }}

    /* Document tabs: a flat strip, the open one lifted with a lit rail along its top. */
    QTabBar::tab {{
        border: none; border-top: 2px solid transparent; border-right: 1px solid {t.border};
        padding-top: 5px; margin: 6px 0 0 0;
    }}
    QTabBar::tab:selected {{ border-top-color: {accent}; font-weight: 600; }}
    QTabBar[home="true"]::tab:selected {{ border-top-color: transparent; font-weight: 400; }}
    QTabWidget QTabBar::tab {{ border: 1px solid transparent; margin: 0 4px 8px 0; padding: 6px 14px; }}
    QTabWidget QTabBar::tab:selected {{
        background: {_rgba(accent, 30)}; border-color: {accent}; color: {t.accent_ink};
    }}

    /* A selected row carries a bar of the accent at its left edge. */
    QTreeView::item {{ border-left: 3px solid transparent; padding-left: 1px; }}
    QTreeView::item:selected {{ border-left-color: {accent}; }}
    QTreeView {{ show-decoration-selected: 0; }}  /* else the indent is a block of the solid accent */
    #SettingsNavList::item {{ border-left: 3px solid transparent; padding-left: 7px; }}
    #SettingsNavList::item:selected {{ background: {t.selection}; border-left-color: {accent}; }}
    QListWidget#AssistantList::item {{ border-left: 3px solid transparent; }}
    QListWidget#AssistantList::item:selected {{ border-left-color: {accent}; }}
    QPushButton#HomeNav {{ border-left: 3px solid transparent; }}
    QPushButton#HomeNav:checked {{ border-left-color: {accent}; }}

    QMenu {{ border: 1px solid {t.border}; border-top-color: {accent}; padding: 4px 0; }}
    QMenu::item {{ border-left: 2px solid transparent; padding-left: 14px; }}
    QMenu::item:selected {{ border-left-color: {accent}; }}
    QMenu::separator {{ height: 0; background: transparent; border-top: 1px dashed {LINE}; margin: 5px 10px; }}
    QComboBox QAbstractItemView {{ border-top-color: {accent}; }}

    QScrollBar::handle {{ border-radius: 0; }}
    QScrollBar::handle:hover {{ background: {_rgba(accent, 150)}; }}
    QScrollBar::handle:pressed {{ background: {accent}; }}

    QToolTip {{ border: 1px solid {_rgba(accent, 140)}; padding: 4px 8px; }}

    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px; border: 1px solid {t.text_muted}; background: {t.field};
    }}
    QRadioButton::indicator:checked {{
        width: 14px; height: 14px; border: 1px solid {accent};
        background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                    stop: 0 {accent}, stop: 0.42 {accent}, stop: 0.5 {t.field}, stop: 1 {t.field});
    }}

    QSlider::groove:horizontal {{ height: 2px; border-radius: 0; }}
    QSlider::groove:vertical {{ width: 2px; border-radius: 0; }}
    QSlider::sub-page, QSlider::add-page {{ border-radius: 0; }}
    QSlider::handle {{
        background: {t.window}; border: 2px solid {accent}; width: 10px; height: 10px; border-radius: 1px;
    }}
    QSlider::handle:hover {{ background: {accent}; }}
    QSlider::handle:disabled {{ background: {t.window}; border-color: {t.border}; }}

    /* Floating bars: square chips, and a checked tool marked with a bar of the accent below. */
    #FloatingBar QToolButton {{ border-radius: 2px; }}
    #FloatingBar QToolButton:checked {{ border-bottom: 2px solid {accent}; padding-bottom: 3px; }}
    QToolButton#ZoomButton, #SelectionCount, #KeyChip, #HelpKeys {{ font-family: "{mono}"; }}

    /* Standard widgets cannot be chamfered: they keep near square corners where Zenith rounds them. */
    #QuickCard, #TipCard, #PanelRow {{ border-radius: 2px; }}
    #QuickCard:hover {{ border-color: {accent}; }}
    /* Its icon keeps the text colour, which a lit fill would drown: a tube, lit inside. */
    QToolButton#RoundAccent {{ background: {_rgba(accent, 36)}; border: 1px solid {accent}; border-radius: 2px; }}
    QToolButton#RoundAccent:hover {{ background: {_rgba(accent, 64)}; }}
    #ChooserFooter {{ background: transparent; }}  /* square, it would fill the panel's cut corner */
    #FileTable QHeaderView::section {{ text-transform: uppercase; letter-spacing: 1px; font-size: 9pt; }}
    """


LOOK = Look(
    key="neon",
    fonts=("Bahnschrift", "Avenir Next", "Ubuntu", "Noto Sans", "DejaVu Sans", "Segoe UI"),
    title_fonts=CONDENSED,
    label_fonts=CONDENSED,
    title_spacing=0.5,
    label_upper=True,
    label_spacing=1.6,
    # With chamfer on, a radius is how far the painted surfaces' corners are cut; the style sheet's controls
    # stay near square.
    control=2,
    item=0,
    overlay=0,  # menus square, with the system's own shadow on Windows 11
    card=10,
    panel=10,
    chip=2,
    tab=0,
    check=2,
    pill_buttons=False,
    capsules=False,
    chamfer=True,
    glass=True,
    shadow="glow",
    icon_weight="light",  # thin outlines, like tubes
    switch="neon",
    canvas="grid",
    tint=(40, 36),
    checked_icon="accent",
    rail=True,
    on_mark="bar",  # a lit line under the tool that is on
    accents={
        "blue": ("#2ec4ff", "#0a5fe0"),
        "teal": ("#1df2d0", "#00747e"),
        "violet": ("#b46cff", "#7a2ff0"),
        "pink": ("#ff45d0", "#c4129a"),
        "salmon": ("#ff6f7d", "#c42d3b"),
        "orange": ("#ffa340", "#a84b00"),
        "green": ("#4dff94", "#00764a"),
    },
    sheet=_sheet,
)

DARK = Theme(
    name="dark",
    scheme=Qt.ColorScheme.Dark,
    window="#0a0b14",
    surface="#0f1120",
    raised="#151829",
    canvas="#0c0e1a",
    border="#272c4a",
    text="#e4e9ff",
    text_muted="#9ea7cc",
    text_disabled="#5a6188",
    hover="rgba(130, 160, 255, 20)",
    pressed="rgba(130, 160, 255, 38)",
    accent="#2ec4ff",
    accent_hover="#5fd2ff",
    accent_text="#060714",  # dark ink on a lit fill, as on a HUD's buttons
    accent_ink="#77d9ff",
    selection="rgba(46, 196, 255, 40)",
    scrollbar="rgba(140, 160, 255, 70)",
    search_hit=QColor(255, 214, 0, 90),
    search_current=QColor(255, 140, 0, 150),
    # Smoked glass, tinted far heavier than Zenith's: over a white page the bars stay dark, and their text reads
    # at 10:1 in the Clear glass (14:1 in the Balanced).
    glass_base=QColor(12, 14, 28),
    glass_alpha=0.94,
    glass_clear_alpha=0.84,
    glass_highlight=QColor(46, 196, 255, 170),  # the lit rail along the top; Look.rail recolours it per accent
    glass_border=QColor(140, 165, 255, 46),
    glass_text_muted="#aab2d6",
    glass_solid=QColor(20, 23, 40),
    glass_hover="rgba(130, 160, 255, 26)",
    glass_pressed="rgba(130, 160, 255, 44)",
    glass_checked="rgba(46, 196, 255, 56)",
    danger="#ff4f6e",
    danger_fill="#d9204a",
    danger_hover="#c81a40",
    field="#080912",  # an inset darker than the window
    track="rgba(130, 160, 255, 14)",
    segment="#343c72",  # 1.6:1 on the track, so the choice shows without the accent
    paper="#ffffff",
    shadow=QColor(0, 0, 0, 90),  # with Look.shadow "glow" its alpha is the glow's, in the accent
    canvas_mark="rgba(110, 130, 255, 28)",  # felt more than seen: 1.1:1
    page_edge="rgba(150, 170, 255, 64)",
    look=LOOK,
)

SUMMARY = "Blue-black and sharp-cornered, with glowing accent edges and a faint grid behind the pages."
STYLE = Style("neon", "Neon", SUMMARY, DARK, None)
