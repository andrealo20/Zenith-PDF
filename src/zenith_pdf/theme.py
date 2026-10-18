"""Colour tokens, palette and style sheet for the light and dark themes."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .core.platforms import MAC, SYSTEM_NAME, ui_font


@dataclass(frozen=True)
class Theme:
    name: str
    scheme: Qt.ColorScheme
    window: str  # application chrome
    surface: str  # side panels
    raised: str  # floating bars, menus, cards
    canvas: str  # behind the pages
    border: str
    text: str
    text_muted: str
    hover: str
    pressed: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_ink: str  # the accent as text on tinted fills (a selected item, a chip): 4.5:1 or more
    selection: str
    scrollbar: str
    search_hit: QColor
    search_current: QColor
    # Frosted glass of the floating bars
    glass_base: QColor  # laid over the blurred backdrop
    glass_alpha: float  # its least opacity in the Balanced glass; the bars raise it where reading needs it
    glass_clear_alpha: float  # its least opacity in the Clear glass
    glass_highlight: QColor  # one pixel of light inside the top edge
    glass_border: QColor
    glass_text_muted: str  # muted text on solid floating panels; on glass over the pages it takes the text colour
    glass_solid: QColor  # when there is no backdrop to blur
    glass_hover: str  # buttons on glass
    glass_pressed: str
    glass_checked: str  # the accent at 22%
    # Shared by dialogs, panels and the tool menu
    danger: str  # text and icons of destructive actions
    danger_fill: str  # a destructive button that is the dialog's main action
    danger_hover: str
    field: str  # inside inputs
    track: str  # behind a segmented control
    segment: str  # the selected segment
    paper: str  # previews drawn as on a page
    shadow: QColor  # under popovers and menus


DARK = Theme(
    name="dark",
    scheme=Qt.ColorScheme.Dark,
    window="#1b1c1f",
    surface="#232428",
    raised="#2c2d32",
    canvas="#131416",
    border="#393a40",
    text="#ececf0",
    text_muted="#9b9ca5",
    hover="rgba(255, 255, 255, 20)",
    pressed="rgba(255, 255, 255, 36)",
    accent="#2a8cff",  # the blues of the logo
    accent_hover="#52a3ff",
    accent_text="#ffffff",
    accent_ink="#6cb0ff",
    selection="rgba(42, 140, 255, 56)",
    scrollbar="rgba(255, 255, 255, 60)",
    search_hit=QColor(255, 214, 0, 90),
    search_current=QColor(255, 140, 0, 150),
    glass_base=QColor(28, 29, 34),
    glass_alpha=0.72,
    glass_clear_alpha=0.16,
    glass_highlight=QColor(255, 255, 255, 46),
    glass_border=QColor(255, 255, 255, 26),
    glass_text_muted="#bfc0c8",
    glass_solid=QColor(38, 39, 44),
    glass_hover="rgba(255, 255, 255, 26)",
    glass_pressed="rgba(255, 255, 255, 41)",
    glass_checked="rgba(42, 140, 255, 56)",
    danger="#ff6b5f",
    danger_fill="#e5484d",
    danger_hover="#ee6165",
    field="#25262b",
    track="rgba(255, 255, 255, 15)",
    segment="#3a3b42",
    paper="#ffffff",
    shadow=QColor(0, 0, 0, 115),
)

LIGHT = Theme(
    name="light",
    scheme=Qt.ColorScheme.Light,
    window="#f1f1f4",
    surface="#f9f9fb",
    raised="#ffffff",
    canvas="#dcdde2",
    border="#d6d7dd",
    text="#1c1d21",
    text_muted="#676872",
    hover="rgba(0, 0, 0, 14)",
    pressed="rgba(0, 0, 0, 26)",
    accent="#0a6cf0",
    accent_hover="#2d82f5",
    accent_text="#ffffff",
    accent_ink="#0058c9",
    selection="rgba(10, 108, 240, 40)",
    scrollbar="rgba(0, 0, 0, 70)",
    search_hit=QColor(255, 214, 0, 110),
    search_current=QColor(255, 140, 0, 160),
    glass_base=QColor(246, 247, 250),
    glass_alpha=0.70,
    glass_clear_alpha=0.12,
    glass_highlight=QColor(255, 255, 255, 230),
    glass_border=QColor(0, 0, 0, 23),
    glass_text_muted="#4a4b52",
    glass_solid=QColor(247, 247, 249),
    glass_hover="rgba(0, 0, 0, 15)",
    glass_pressed="rgba(0, 0, 0, 26)",
    glass_checked="rgba(10, 108, 240, 56)",
    danger="#d93a2f",
    danger_fill="#d93a2f",
    danger_hover="#e25349",
    field="#ffffff",
    track="rgba(0, 0, 0, 10)",
    segment="#ffffff",
    paper="#ffffff",
    shadow=QColor(0, 0, 0, 41),
)

THEMES = {theme.name: theme for theme in (DARK, LIGHT)}
THEME_NAMES = {"dark": "Dark", "light": "Light", "system": f"Same as {SYSTEM_NAME}"}

# Accent colours: (label, on the dark theme, on the light theme). Light needs darker shades for contrast.
ACCENTS = {
    "blue": ("Blue", "#2a8cff", "#0a6cf0"),
    "teal": ("Teal", "#22b5ac", "#0e8a83"),
    "violet": ("Violet", "#9a5cff", "#7b3ae0"),  # the violet of the logo
    "pink": ("Pink", "#f2649f", "#d23c7b"),
    "salmon": ("Salmon", "#ff7f6e", "#e0553f"),
    "orange": ("Orange", "#ff9640", "#d9650f"),
    "green": ("Green", "#3fc46d", "#1c9447"),
}


def with_accent(theme: Theme, accent: str) -> Theme:
    """`theme` with another accent colour, and the hover and selection shades derived from it."""
    if accent not in ACCENTS or accent == "blue":
        return theme
    base = QColor(ACCENTS[accent][1 if theme.name == "dark" else 2])
    hover = base.lighter(118) if theme.name == "dark" else base.lighter(112)
    alpha = 56 if theme.name == "dark" else 40
    rgb = f"{base.red()}, {base.green()}, {base.blue()}"
    return replace(
        theme,
        accent=base.name(),
        accent_hover=hover.name(),
        accent_ink=_ink(base, theme),
        selection=f"rgba({rgb}, {alpha})",
        glass_checked=f"rgba({rgb}, 56)",
    )


def luminance(color: QColor) -> float:
    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(color.redF()) + 0.7152 * linear(color.greenF()) + 0.0722 * linear(color.blueF())


def _ink(accent: QColor, theme: Theme) -> str:
    """The accent as text on a fill tinted with it (a selected item, a chosen chip): lighter on dark, darker on
    light, from a third or a quarter of the way to white or black, and further until it reads at 4.5:1 on each."""
    toward, share = (1.0, 0.35) if theme.name == "dark" else (0.0, 0.25)
    rgb = (accent.redF(), accent.greenF(), accent.blueF())
    fills = []
    for back in (theme.window, theme.surface, theme.raised):
        under = QColor(back)
        fills.append(luminance(QColor.fromRgbF(*(u + (a - u) * 0.16 for u, a in
                                                 zip((under.redF(), under.greenF(), under.blueF()), rgb)))))
    while True:
        ink = QColor.fromRgbF(*(c + (toward - c) * share for c in rgb))
        light = luminance(ink)
        if share >= 1.0 or all((max(light, f) + 0.05) / (min(light, f) + 0.05) >= 4.6 for f in fills):
            return ink.name()
        share = min(share + 0.05, 1.0)


def qcolor(token: str) -> QColor:
    """QColor from a theme token. QColor alone can't parse the rgba(...) form QSS needs."""
    if token.startswith("rgba("):
        r, g, b, a = (int(part) for part in token[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(token)


def _palette(t: Theme) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.window,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface,
        QPalette.ColorRole.AlternateBase: t.raised,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.raised,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.ToolTipBase: t.raised,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.PlaceholderText: t.text_muted,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_text,
        QPalette.ColorRole.Link: t.accent,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.text_muted))
    return palette


def _arrow(name: str, color: str) -> str:
    """A small Phosphor glyph as a PNG file, for the arrows the style sheet draws (QSS needs a file)."""
    import qtawesome as qta

    folder = Path(tempfile.gettempdir()) / "zenith-pdf"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}-{QColor(color).name()[1:]}.png"
    if not path.exists():
        qta.icon(f"ph.{name}", color=color).pixmap(QSize(24, 24)).save(str(path))
    return path.as_posix()


def _style_sheet(t: Theme) -> str:
    down = _arrow("caret-down-bold", t.text_muted)
    up = _arrow("caret-up-bold", t.text_muted)
    right = _arrow("caret-right-bold", t.text_muted)
    check = _arrow("check-bold", t.accent_text)
    dash = _arrow("minus-bold", t.accent_text)
    red = QColor(t.danger)
    danger_soft = f"rgba({red.red()}, {red.green()}, {red.blue()}, 34)"  # under a destructive button's pointer
    return _for_platform(f"""
    * {{ font-family: "{ui_font()}"; font-size: 10pt; }}
    QWidget {{ color: {t.text}; }}
    QWidget:disabled {{ color: {t.text_muted}; }}
    QMainWindow, #Central, #TopBar, #Home {{ background: {t.window}; }}

    QTabBar {{ background: transparent; }}
    QTabBar::tab {{
        background: transparent; color: {t.text_muted};
        padding: 7px 6px 7px 12px; margin: 6px 2px 0 0;
        border-top-left-radius: 8px; border-top-right-radius: 8px;
        min-width: 110px; max-width: 220px;
    }}
    QTabBar::tab:hover {{ background: {t.hover}; color: {t.text}; }}
    QTabBar::tab:selected {{ background: {t.surface}; color: {t.text}; }}
    QTabBar[home="true"]::tab:selected {{ background: transparent; color: {t.text_muted}; }}
    QTabBar[home="true"]::tab:selected:hover {{ background: {t.hover}; color: {t.text}; }}
    QTabBar QToolButton {{ background: transparent; border-radius: 6px; padding: 3px; }}
    QTabBar QToolButton:hover {{ background: {t.hover}; }}
    QTabBar QToolButton#ScrollLeftButton, QTabBar QToolButton#ScrollRightButton {{ background: {t.window}; }}

    QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 5px; }}
    QToolButton:hover {{ background: {t.hover}; }}
    QToolButton:pressed {{ background: {t.pressed}; }}
    QToolButton:checked {{ background: {t.selection}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    QToolButton#ZoomButton {{ padding: 4px 6px; min-width: 44px; }}
    QToolButton#MenuButton {{
        border: 1px solid {t.border}; border-radius: 8px; padding: 5px 28px 5px 12px; background: {t.field};
    }}
    QToolButton#MenuButton:hover {{ border-color: {t.text_muted}; background: {t.field}; }}
    QToolButton#MenuButton::menu-indicator {{
        image: url({down}); width: 11px; height: 11px; subcontrol-origin: padding; subcontrol-position: right center;
        right: 10px;
    }}

    #Rail {{ background: {t.surface}; border-right: 1px solid {t.border}; }}
    #Panel {{ background: {t.surface}; border-right: 1px solid {t.border}; }}
    #PanelTitle {{ font-size: 11pt; font-weight: 600; }}
    #FloatingBar QToolButton {{ border-radius: 10px; }}
    #FloatingBar QToolButton:hover {{ background: {t.glass_hover}; }}
    #FloatingBar QToolButton:pressed {{ background: {t.glass_pressed}; }}
    #FloatingBar QToolButton:checked {{ background: {t.glass_checked}; }}
    #FloatingBar QToolButton#DangerButton:hover {{ background: {danger_soft}; }}
    #FloatingBar QToolButton#BackChip {{
        background: {t.glass_hover}; border-radius: 14px; padding: 5px 10px 5px 8px; font-weight: 600;
    }}
    #FloatingBar QToolButton#BackChip:hover {{ background: {t.glass_pressed}; }}
    #FloatingBar QToolButton#BackChip:pressed {{ background: {t.glass_checked}; }}
    #FloatingBar QLabel#Muted {{ color: {t.glass_text_muted}; }}
    #FloatingBar[glass="true"] QLabel#Muted {{ color: {t.text}; }}  /* over the pages: full contrast */
    #Separator {{ background: {t.border}; }}
    QLabel#Muted, QCheckBox#Muted {{ color: {t.text_muted}; }}
    QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; }}

    QLineEdit, QPlainTextEdit {{
        background: {t.field}; border: 1px solid {t.border}; border-radius: 8px;
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QLineEdit {{ padding: 6px 10px 5px 10px; }}  /* 32 px tall, as the buttons and drop-downs */
    QPlainTextEdit {{ padding: 4px; }}
    QLineEdit:hover, QPlainTextEdit:hover {{ border-color: {t.text_muted}; }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {t.accent}; }}
    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled, QPlainTextEdit:disabled {{
        color: {t.text_muted}; background: transparent; border-color: {t.border};
    }}
    QLineEdit#PageField {{ padding: 3px 4px; background: transparent; border-radius: 6px; }}
    QLineEdit#PageField:hover {{ border-color: {t.text_muted}; }}
    QLineEdit#PageField:focus {{ border-color: {t.accent}; background: {t.field}; }}

    QListView, QTreeView {{ background: transparent; border: none; outline: 0; }}
    QTreeView::item {{ padding: 5px 4px; border-radius: 6px; }}
    QTreeView::item:hover, QListView::item:hover {{ background: {t.hover}; }}
    QTreeView::item:selected, QListView::item:selected {{ background: {t.selection}; color: {t.text}; }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
    QScrollBar::handle {{ background: {t.scrollbar}; border-radius: 4px; }}
    QScrollBar::handle:vertical {{ min-height: 32px; margin: 0 2px; }}
    QScrollBar::handle:horizontal {{ min-width: 32px; margin: 2px 0; }}
    QScrollBar::handle:hover {{ background: {t.text_muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QMenu {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t.hover}; }}
    QMenu::item:disabled {{ color: {t.text_muted}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    QMenu::right-arrow {{ image: url({right}); width: 12px; height: 12px; margin-right: 8px; }}

    QPushButton {{
        background: {t.raised}; border: 1px solid {t.border}; border-radius: 8px; padding: 7px 16px 6px 16px;
    }}
    QPushButton:hover {{ border-color: {t.text_muted}; }}
    QPushButton:pressed {{ background: {t.pressed}; }}
    QPushButton:disabled {{ color: {t.text_muted}; border-color: {t.border}; background: transparent; }}
    QPushButton#Primary {{
        background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent}; font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
    QPushButton#Primary:disabled {{ background: {t.hover}; color: {t.text_muted}; border-color: {t.border}; }}
    QPushButton#Danger {{
        background: {t.danger_fill}; color: #ffffff; border: 1px solid {t.danger_fill}; font-weight: 600;
    }}
    QPushButton#Danger:hover {{ background: {t.danger_hover}; border-color: {t.danger_hover}; }}
    QPushButton#Danger:disabled {{ background: {t.hover}; color: {t.text_muted}; border-color: {t.border}; }}
    QPushButton#DangerText {{ color: {t.danger}; }}
    QPushButton#DangerText:hover {{ border-color: {t.danger}; }}
    QPushButton#DangerText:disabled {{ color: {t.text_muted}; }}

    QToolTip {{ background: {t.raised}; color: {t.text}; border: 1px solid {t.border}; padding: 4px 6px; }}
    #FloatingBar QLabel#ToastText {{ color: {t.text}; }}  /* on glass, so always full contrast */
    #PageManager, #Builder {{ background: {t.canvas}; }}
    #PanelRow {{ background: {t.hover}; border: 1px solid {t.hover}; border-radius: 10px; }}
    #PanelRow:hover {{ background: {t.pressed}; border-color: {t.accent}; }}
    #PanelRowText {{ font-weight: 600; }}
    #SelectionCount {{ font-weight: 700; padding: 0 6px; }}
    #BuilderName {{ font-size: 13pt; font-weight: 600; background: transparent; border: 1px solid transparent; }}
    #BuilderName:hover, #BuilderName:focus {{ border-color: {t.border}; }}
    #SourceList::item {{ border-bottom: 1px solid {t.border}; }}
    #BuilderGrid {{ background: transparent; }}
    #PageManager QListView {{ background: transparent; }}
    QMenu::icon {{ padding-left: 8px; }}
    #SettingsNav {{ background: {t.surface}; border-right: 1px solid {t.border}; }}
    #SettingsTitle {{ font-size: 20pt; font-weight: 600; }}
    QLineEdit#SettingsSearch {{ border-radius: 17px; padding: 7px 12px; background: {t.window}; }}
    #SettingsNavList::item {{ padding: 4px 10px; border-radius: 8px; margin: 1px 0; }}
    #SettingsNavList::item:selected {{ background: {t.hover}; color: {t.accent_ink}; font-weight: 600; }}
    #SettingsNavList::item:disabled {{ background: transparent; }}
    #SettingsContent, #SettingsScroll, #SettingsWindow {{ background: {t.window}; }}
    #SettingsSection {{ font-size: 15pt; font-weight: 600; padding-bottom: 4px; }}
    #SettingTitle {{ font-size: 10.5pt; }}
    #SettingLine {{ background: {t.border}; }}
    #KeyChip {{ background: {t.hover}; border: 1px solid {t.border}; border-radius: 5px; padding: 2px 8px; }}
    #AboutName {{ font-size: 17pt; font-weight: 600; }}
    QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {t.field}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px 10px 5px 10px;
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QSpinBox, QDoubleSpinBox {{ padding: 4px 10px; }}
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {t.text_muted}; }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
    QComboBox:on {{ border-color: {t.accent}; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{ image: url({down}); width: 11px; height: 11px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
        border: none; width: 20px; background: transparent; border-radius: 4px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {t.hover}; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}
    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
    QComboBox QAbstractItemView {{
        background: {t.raised}; border: 1px solid {t.border}; border-radius: 8px; padding: 4px;
        selection-background-color: {t.hover}; selection-color: {t.text}; outline: 0;
    }}

    QSlider {{ min-height: 20px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::add-page:horizontal {{ background: {t.border}; border-radius: 2px; }}
    QSlider::groove:vertical {{ width: 4px; background: {t.border}; border-radius: 2px; }}
    QSlider::add-page:vertical {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::sub-page:vertical {{ background: {t.border}; border-radius: 2px; }}
    QSlider::handle {{
        background: #ffffff; border: 1px solid rgba(0, 0, 0, 61); width: 14px; height: 14px; border-radius: 8px;
    }}
    QSlider::handle:horizontal {{ margin: -6px 0; }}
    QSlider::handle:vertical {{ margin: 0 -6px; }}
    QSlider::handle:hover {{ border-color: {t.accent}; }}
    QSlider::sub-page:horizontal:disabled, QSlider::add-page:vertical:disabled {{ background: {t.text_muted}; }}
    QSlider::handle:disabled {{ background: {t.border}; border-color: {t.border}; }}

    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 12px; height: 12px; border: 2px solid {t.text_muted}; background: transparent;
    }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check}); }}
    QCheckBox::indicator:indeterminate {{ background: {t.accent}; border-color: {t.accent}; image: url({dash}); }}
    QRadioButton::indicator:checked {{ width: 6px; height: 6px; border: 5px solid {t.accent}; background: #ffffff; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {t.border}; }}
    QCheckBox::indicator:checked:disabled, QCheckBox::indicator:indeterminate:disabled {{
        background: {t.border}; border-color: {t.border};
    }}
    QRadioButton::indicator:checked:disabled {{ border-color: {t.border}; }}

    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabWidget::tab-bar {{ alignment: left; }}
    QTabWidget QTabBar::tab {{
        background: transparent; color: {t.text_muted}; padding: 6px 14px; margin: 0 4px 8px 0;
        border: 1px solid transparent; border-radius: 6px; min-width: 0; max-width: 1000px;
    }}
    QTabWidget QTabBar::tab:hover {{ background: {t.hover}; color: {t.text}; }}
    QTabWidget QTabBar::tab:selected {{
        background: {t.segment}; color: {t.text}; border-color: {t.border}; font-weight: 600;
    }}
    #HomeSide {{ background: {t.surface}; border-right: 1px solid {t.border}; }}
    #HomeAside {{ background: {t.surface}; border-left: 1px solid {t.border}; }}
    #HomeBrand {{ font-size: 13.5pt; font-weight: 600; }}
    #HomeSection {{ font-size: 15pt; font-weight: 600; }}
    #HomeNavHeading {{ color: {t.text_muted}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
    QPushButton#HomeNav {{ background: transparent; border: none; border-radius: 8px; padding: 0; text-align: left; }}
    QPushButton#HomeNav:hover {{ background: {t.hover}; }}
    QPushButton#HomeNav:checked {{ background: {t.selection}; }}
    #QuickCard {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
    #QuickCard:hover {{ border-color: {t.accent}; background: {t.raised}; }}
    #TipCard {{ background: {t.raised}; border: 1px solid {t.border}; border-radius: 10px; padding: 12px; }}
    QToolButton#RoundAccent {{ background: {t.accent}; border-radius: 18px; padding: 8px; }}
    QToolButton#RoundAccent:hover {{ background: {t.accent_hover}; }}
    #FileTable {{ background: transparent; border: none; }}
    #FileTable QHeaderView::section {{
        background: transparent; color: {t.text_muted}; border: none; border-bottom: 1px solid {t.border};
        padding: 6px 6px; font-weight: 600;
    }}
    #FileTable QHeaderView {{ background: transparent; }}
    #ChooserTitle {{ font-size: 20pt; font-weight: 600; }}
    QToolButton#ChooserTab {{
        font-size: 11.5pt; padding: 6px 4px 8px 4px; margin-right: 14px; border-radius: 0;
        border-bottom: 3px solid transparent; background: transparent; color: {t.text_muted};
    }}
    QToolButton#ChooserTab:hover {{ color: {t.text}; background: transparent; }}
    QToolButton#ChooserTab:checked {{ color: {t.text}; border-bottom-color: {t.accent}; background: transparent; }}
    #ChooserFooter {{
        background: {t.canvas if t.name == "dark" else t.window}; border-top: 1px solid {t.border};
        border-bottom-left-radius: 13px; border-bottom-right-radius: 13px;
    }}
    #EquationPreview {{ background: {t.paper}; border: 1px solid {t.border}; border-radius: 8px; }}
    QListWidget#AttachmentList {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 8px;
                                 padding: 4px; }}
    QTextBrowser#GuideText {{ background: {t.window}; border: none; }}
    #HelpTitle {{ font-size: 10.5pt; font-weight: 600; }}
    #HelpHeading {{ color: {t.text_muted}; font-size: 8.5pt; font-weight: 600; padding-top: 2px; }}
    #HelpKeys {{ background: {t.hover}; border: 1px solid {t.border}; border-radius: 5px; padding: 1px 7px;
                 font-size: 9pt; }}
    QDialog {{ background: {t.window}; }}
    QLabel#DialogTitle {{ font-size: 12pt; font-weight: 600; }}
    QLabel#DialogBody {{ color: {t.text_muted}; }}
    QLabel#WhatsNewVersion {{ font-weight: 600; margin-top: 6px; }}
    QLabel#CardFront {{ font-size: 14pt; font-weight: 600; }}
    QLabel#CardBack {{ font-size: 12pt; color: {t.accent_ink}; }}
    QLabel#DialogDetail {{
        color: {t.text_muted}; font-size: 9pt; background: {t.hover}; border-radius: 6px; padding: 8px 10px;
    }}
    #MessageDialog QPushButton {{ min-width: 64px; min-height: 30px; padding: 0 16px; }}
    QPushButton#Link {{ background: transparent; border: none; color: {t.text_muted}; padding: 4px 2px; }}
    QPushButton#Link:hover {{ color: {t.text}; text-decoration: underline; }}
    #SettingRow[flash="true"] {{ background: {t.selection}; border-radius: 8px; }}
    QLineEdit#AssistantSearch {{
        background: transparent; border: none; font-size: 13pt; padding: 6px 2px;
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}
    QListWidget#AssistantList {{ background: transparent; border: none; outline: 0; }}
    QListWidget#AssistantList::item {{ border-radius: 8px; margin: 0 2px; }}
    QListWidget#AssistantList::item:selected {{ background: {t.selection}; }}
    QListWidget#AssistantList::item:hover:!selected {{ background: {t.hover}; }}
    QLabel#AssistantSection {{
        color: {t.text_muted}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; padding: 8px 10px 0 10px;
    }}
    QLabel#AssistantTitle {{ font-size: 10pt; }}
    QLabel#AssistantKind {{ color: {t.text_muted}; font-size: 8.5pt; }}
    QTextEdit#SpellingPreview, QTextBrowser#SummaryText {{
        background: {t.field}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px 8px;
    }}
    """)


def _for_platform(sheet: str) -> str:
    """The style sheet as this system needs it. macOS counts 72 points to the inch where Windows and Linux count
    96, so text sizes there are scaled by 4/3: a 10 pt label is as large on every system."""
    if not MAC:
        return sheet
    return re.sub(r"(\d+(?:\.\d+)?)pt\b", lambda m: f"{float(m.group(1)) * 4 / 3:g}pt", sheet)


def tone_sheet(t: Theme) -> str:
    """The rules of a glass bar that took the other theme's colours over what is behind it: a light bar over a
    bright page in the dark theme, a dark one over a dark picture in the light theme. Set on the bar, they win
    over the app's own.

    Only on what sits on the bar itself ("> "): a menu that opens from it, such as a tool's, is solid and keeps
    the app's colours. Without it a light bar gave the dark menu dark text."""
    bar = "#FloatingBar >"
    return _for_platform(f"""
    {bar} QLabel, {bar} QLabel#Muted, {bar} QLabel#ToastText {{ color: {t.text}; }}
    {bar} QToolButton {{ color: {t.text}; }}
    {bar} QToolButton:hover {{ background: {t.glass_hover}; }}
    {bar} QToolButton:pressed {{ background: {t.glass_pressed}; }}
    {bar} QToolButton:checked {{ background: {t.glass_checked}; }}
    {bar} QToolButton#BackChip {{
        background: {t.glass_hover}; border-radius: 14px; padding: 5px 10px 5px 8px; font-weight: 600;
    }}
    {bar} QToolButton#BackChip:hover {{ background: {t.glass_pressed}; }}
    {bar} QLineEdit#PageField {{
        color: {t.text}; border-color: {t.border}; selection-background-color: {t.selection};
    }}
    {bar} QLineEdit#PageField:hover {{ border-color: {t.text_muted}; }}
    {bar} #Separator {{ background: {t.border}; }}
    """)


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.current: Theme = DARK
        self._choice = ("dark", "blue")
        self._listening = False

    def apply(self, theme: Theme, follow_system: bool = False) -> None:
        app = QApplication.instance()
        if follow_system:
            app.styleHints().unsetColorScheme()  # the title bar follows Windows by itself
        else:
            app.styleHints().setColorScheme(theme.scheme)  # also themes the native title bar
        app.setPalette(_palette(theme))
        app.setStyleSheet(_style_sheet(theme))
        self.current = theme
        self.changed.emit(theme)

    def tone(self, name: str) -> Theme:
        """The light or the dark theme with the accent in use: what a glass bar takes when it changes tone."""
        if name == self.current.name:
            return self.current
        return with_accent(THEMES.get(name, DARK), self._choice[1])

    def choose(self, name: str, accent: str = "blue") -> None:
        """Apply a theme by name: "dark", "light", or "system" to follow the system."""
        self._choice = (name, accent)
        hints = QApplication.instance().styleHints()
        if name == "system":
            hints.unsetColorScheme()  # forget an earlier explicit choice, so Windows' own shows
            base = LIGHT if hints.colorScheme() == Qt.ColorScheme.Light else DARK
            if not self._listening:
                hints.colorSchemeChanged.connect(self._on_system_scheme)
                self._listening = True
        else:
            base = THEMES.get(name, DARK)
        self.apply(with_accent(base, accent), follow_system=name == "system")

    def _on_system_scheme(self, _scheme) -> None:
        name, accent = self._choice
        if name == "system":
            base = LIGHT if _scheme == Qt.ColorScheme.Light else DARK
            if base.name != self.current.name:
                self.apply(with_accent(base, accent), follow_system=True)


themes = ThemeManager()
