"""The Dot style: the black and white of Nothing OS, with one colour for whatever is on.

From Nothing OS (the opaque 2 and 3 releases): OLED black chrome in the dark and white paper in the light, 1 px
hairlines and no shadow, gradient or glass, round controls (pills, circles, capsules), page titles as a dot
matrix, small labels and numbers in monospace capitals, a dot grid behind the pages, and the "on" state shown
inverted (a white pill with black text in the dark). One red marks what is live: its shade is the Salmon
accent key, the nearest of the seven to Nothing's red.

No font ships with a dot matrix, so the titles are drawn as dots by the app (Look.dot_titles) from a monospace
system font; Nothing's own NDot, NType and Lettera are not free to bundle.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from ..theme import Look, Style, Theme, _arrow

TEXT_FONTS = ("Segoe UI Variable Text", "Segoe UI", ".AppleSystemUIFont", "Helvetica Neue", "Noto Sans", "Cantarell",
              "Ubuntu", "DejaVu Sans", "Liberation Sans")
TITLE_FONTS = ("Segoe UI Variable Display", "Segoe UI", ".AppleSystemUIFont", "Helvetica Neue", "Noto Sans",
               "Cantarell", "Ubuntu", "DejaVu Sans")
MONO_FONTS = ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "Monaco", "Noto Sans Mono", "DejaVu Sans Mono",
              "Ubuntu Mono", "Liberation Mono", "Courier New")

# What the palettes have no token for: the outline of fields and plain buttons (3:1 on the window, a boundary
# that can be found), and the inverse fill under the pointer and pressed.
FIELD_LINE = {"dark": "#636363", "light": "#8a8a8a"}
INVERSE_HOVER = {"dark": "#d9d9d9", "light": "#333333"}
INVERSE_PRESSED = {"dark": "#bfbfbf", "light": "#4d4d4d"}


def _dot(colour: str, size: int = 6) -> str:
    """A round dot `size` px across in the middle of a 16 px PNG, for the style sheet (QSS needs a file)."""
    folder = Path(tempfile.gettempdir()) / "zenith-pdf"
    folder.mkdir(exist_ok=True)
    path = folder / f"dot-{size}-{QColor(colour).name()[1:]}.png"
    if not path.exists():
        image = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QPointF(8, 8), size / 2, size / 2)  # on whole pixels, so its edge stays crisp
        painter.end()
        image.save(str(path))
    return path.as_posix()


def _sheet(t: Theme) -> str:
    line, ink, paper = FIELD_LINE[t.name], t.text, t.window  # "on" is drawn inverted: ink fill, paper text
    check = _arrow("check-bold", paper)
    dash = _arrow("minus-bold", paper)
    live = _dot(t.accent)
    mono = t.look.label_font()
    return f"""
    /* Outlined fields: a line that can be found, and the text colour once focused. */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ border-color: {line}; }}
    QPlainTextEdit, QTextEdit#SpellingPreview, QTextBrowser#SummaryText {{ border-radius: 12px; }}
    QComboBox:on {{ border-color: {ink}; }}

    /* Plain buttons are outlines; the main action is inverted. The accent never fills a button. */
    QPushButton {{ background: transparent; border-color: {line}; font-weight: 500; }}
    QPushButton:hover {{ border-color: {ink}; }}
    QPushButton#Primary {{ background: {ink}; color: {paper}; border-color: {ink}; }}
    QPushButton#Primary:hover {{ background: {INVERSE_HOVER[t.name]}; border-color: {INVERSE_HOVER[t.name]}; }}
    QPushButton#Primary:pressed {{
        background: {INVERSE_PRESSED[t.name]}; border-color: {INVERSE_PRESSED[t.name]};
    }}
    QToolButton#RoundAccent {{ background: transparent; border: 1px solid {line}; }}
    QToolButton#RoundAccent:hover {{ background: {t.hover}; border-color: {ink}; }}

    /* Document tabs float as pills. */
    QTabBar::tab {{ border-radius: 14px; margin: 5px 2px 5px 0; padding: 6px 6px 6px 12px; }}
    QTabBar::tab:selected {{ background: {t.raised}; border: 1px solid {t.border}; }}

    /* Lists select with a neutral pill; navigation adds the live dot. */
    QTreeView::item {{ border-radius: 12px; }}
    QTreeView {{ selection-background-color: transparent; }}  /* else the arrow of a chosen row sits on the accent */
    #SettingsNavList::item:selected {{
        background-color: {t.selection}; color: {ink}; font-weight: 400;
        background-image: url({live}); background-origin: content; background-position: right center;
        background-repeat: no-repeat;
    }}

    /* Menus: dotted rules, and a dot for what is checked. */
    QMenu {{ padding: 6px; }}
    QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 12px; }}
    QMenu::separator {{
        height: 0; background: transparent; border-top: 1px dotted {t.text_disabled}; margin: 6px 10px;
    }}
    QMenu::indicator {{ width: 16px; height: 16px; padding-left: 6px; }}
    QMenu::indicator:checked {{ image: url({live}); }}
    #SettingLine {{ background: transparent; border-top: 1px dotted {t.text_disabled}; }}

    QScrollBar::handle {{ border-radius: 2px; }}

    /* Checks are inverted; the radio and the slider carry the live dot. */
    QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t.text_muted}; }}
    QCheckBox::indicator {{ border-radius: 5px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ink}; }}
    QCheckBox::indicator:checked {{ background: {ink}; border-color: {ink}; image: url({check}); }}
    QCheckBox::indicator:indeterminate {{ background: {ink}; border-color: {ink}; image: url({dash}); }}
    QRadioButton::indicator:checked {{
        width: 14px; height: 14px; border: 1px solid {ink};
        background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                    stop: 0 {t.accent}, stop: 0.42 {t.accent}, stop: 0.5 transparent);
    }}
    QSlider::groove:horizontal {{ height: 2px; border-radius: 1px; }}
    QSlider::sub-page:horizontal {{ background: {ink}; border-radius: 1px; }}
    QSlider::add-page:horizontal {{ border-radius: 1px; }}
    QSlider::handle {{
        background: {t.accent}; border: 2px solid {paper}; width: 12px; height: 12px; border-radius: 8px;
    }}
    QSlider::handle:horizontal {{ margin: -7px 0; }}
    QSlider::handle:hover {{ border-color: {ink}; }}

    QToolTip {{ border-radius: 0; padding: 4px 8px; }}

    /* Hover rings in the text colour, never the accent. */
    #QuickCard:hover, #PanelRow:hover {{ border-color: {ink}; }}

    /* Small labels and numbers in monospace, as Nothing sets them. */
    #HomeNavHeading, QLabel#AssistantSection, #HelpHeading {{ font-weight: 400; }}
    #FileTable QHeaderView::section {{
        font-family: "{mono}"; font-size: 9pt; font-weight: 400; text-transform: uppercase;
    }}
    QLineEdit#PageField, QToolButton#ZoomButton, #SelectionCount, #KeyChip, #HelpKeys {{ font-family: "{mono}"; }}
    /* Round ends on the shorter controls: Qt squares a radius over half the height. */
    QComboBox {{ border-radius: 14px; }}
    QLineEdit#PageField {{ border-radius: 12px; }}
    #HelpKeys {{ border-radius: 9px; }}
    QTabBar QToolButton {{ border-radius: 10px; }}
    #PanelTitle, #HomeBrand {{ font-family: "{mono}"; font-weight: 400; text-transform: uppercase; }}
    #PanelTitle {{ font-size: 10pt; }}
    #HomeBrand {{ font-size: 12pt; letter-spacing: 1px; }}
    /* The titles drawn as dots: monospace capitals at the regular weight give even, one-dot strokes, and a
       little larger than plain titles, so each dot is big enough to read as one. */
    #SettingsTitle, #ChooserTitle, #HomeSection, #SettingsSection, #AboutName {{
        font-family: "{mono}"; font-weight: 400; text-transform: uppercase;
    }}
    #SettingsTitle, #ChooserTitle {{ font-size: 24pt; }}
    #SettingsSection, #HomeSection {{ font-size: 18pt; }}
    #AboutName {{ font-size: 20pt; }}

    /* The tool chooser's tabs: pills, the chosen one inverted. */
    QToolButton#ChooserTab {{
        border: none; border-radius: 14px; padding: 5px 12px; margin-right: 6px; font-size: 10.5pt;
    }}
    QToolButton#ChooserTab:hover {{ background: {t.hover}; }}
    QToolButton#ChooserTab:checked {{ background: {ink}; color: {paper}; }}
    """


LOOK = Look(
    key="dot",
    fonts=TEXT_FONTS,
    title_fonts=TITLE_FONTS,
    label_fonts=MONO_FONTS,
    title_weight=400,
    label_upper=True,
    label_spacing=0,
    control=16,
    item=14,
    overlay=16,
    card=20,
    panel=24,
    chip=10,
    tab=14,
    check=5,
    pill_buttons=True,
    capsules=True,
    glass=False,
    shadow="none",
    icon_weight="light",
    switch="dot",
    canvas="dots",
    dot_titles=True,
    tint=(0, 0),  # selection stays neutral white or black, whatever the accent
    checked_icon="accent",  # the active tool's icon is the one spot of colour on the bar
    on_mark="dot",  # and the red dot under it, as a live indicator
    accents={
        "blue": ("#3d85ff", "#1a55e0"),
        "teal": ("#14b8a6", "#00786c"),
        "violet": ("#a173ff", "#6c37db"),
        "pink": ("#ff4f8b", "#c8195c"),
        "salmon": ("#e5222a", "#d71921"),  # Nothing's red
        "orange": ("#ff7a1a", "#bf4f00"),
        "green": ("#2fc25e", "#147a3a"),
    },
    sheet=_sheet,
)

DARK = Theme(
    name="dark",
    scheme=Qt.ColorScheme.Dark,
    window="#000000",  # OLED black
    surface="#0a0a0a",  # depth is a step of tone and a hairline, never a shadow
    raised="#141414",
    canvas="#000000",
    border="#333333",
    text="#ffffff",
    text_muted="#a3a3a3",
    text_disabled="#5e5e5e",
    hover="rgba(255, 255, 255, 20)",
    pressed="rgba(255, 255, 255, 38)",
    accent="#3d85ff",
    accent_hover="#6ba2ff",
    accent_text="#000000",  # the window colour, as on the inverted main button whose icon takes it
    accent_ink="#81b0ff",
    selection="rgba(255, 255, 255, 36)",
    scrollbar="rgba(255, 255, 255, 64)",
    search_hit=QColor(255, 199, 0, 96),
    search_current=QColor(229, 34, 42, 110),  # the red of what is live
    glass_base=QColor("#141414"),
    glass_alpha=1.0,
    glass_clear_alpha=1.0,
    glass_highlight=QColor(0, 0, 0, 0),
    glass_border=QColor("#333333"),
    glass_text_muted="#a3a3a3",
    glass_solid=QColor("#141414"),
    glass_hover="rgba(255, 255, 255, 20)",
    glass_pressed="rgba(255, 255, 255, 38)",
    glass_checked="rgba(255, 255, 255, 36)",
    danger="#ff5a5f",
    danger_fill="#d71921",
    danger_hover="#b51219",
    field="#0a0a0a",
    track="rgba(0, 0, 0, 0)",
    segment="#ffffff",  # the chosen segment is inverted
    paper="#ffffff",
    shadow=QColor(0, 0, 0, 0),
    look=LOOK,
    focus="#ffffff",
    segment_text="#000000",
    canvas_mark="rgba(255, 255, 255, 36)",
    page_edge="rgba(255, 255, 255, 40)",  # keeps a dark page apart from the black canvas
)

LIGHT = Theme(
    name="light",
    scheme=Qt.ColorScheme.Light,
    window="#ffffff",
    surface="#f7f7f7",
    raised="#ffffff",
    canvas="#e9e9e9",
    border="#b8b8b8",  # the only edge of a white menu over white, with no shadow
    text="#000000",
    text_muted="#545454",
    text_disabled="#a6a6a6",
    hover="rgba(0, 0, 0, 14)",
    pressed="rgba(0, 0, 0, 28)",
    accent="#1a55e0",
    accent_hover="#1d5ffb",
    accent_text="#ffffff",
    accent_ink="#1440a8",
    selection="rgba(0, 0, 0, 22)",
    scrollbar="rgba(0, 0, 0, 72)",
    search_hit=QColor(255, 199, 0, 96),
    search_current=QColor(215, 25, 33, 110),
    glass_base=QColor("#ffffff"),
    glass_alpha=1.0,
    glass_clear_alpha=1.0,
    glass_highlight=QColor(0, 0, 0, 0),
    glass_border=QColor("#b8b8b8"),
    glass_text_muted="#545454",
    glass_solid=QColor("#ffffff"),
    glass_hover="rgba(0, 0, 0, 14)",
    glass_pressed="rgba(0, 0, 0, 28)",
    glass_checked="rgba(0, 0, 0, 22)",
    danger="#c8102e",
    danger_fill="#c8102e",
    danger_hover="#a50d25",
    field="#ffffff",
    track="rgba(0, 0, 0, 0)",
    segment="#000000",
    paper="#ffffff",
    shadow=QColor(0, 0, 0, 0),
    look=LOOK,
    focus="#000000",
    segment_text="#ffffff",
    canvas_mark="rgba(0, 0, 0, 40)",
    page_edge="rgba(0, 0, 0, 46)",
)

SUMMARY = "Black and white, one red, dot-matrix titles and thin outlines."
STYLE = Style("dot", "Dot", SUMMARY, DARK, LIGHT, accent="salmon")  # Nothing's one red
