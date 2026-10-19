"""The card in the middle of the screen while Zenith PDF starts: its logo and its name.

It needs only Qt, so it shows before the rest of the app is loaded.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .core.platforms import ui_font
from .theme import DARK, LIGHT, THEMES, Theme

NAME = "Zenith PDF"
LOGO = Path(__file__).parent / "assets" / "zenith.png"
WIDTH, HEIGHT, RADIUS = 280, 210, 18  # logical px
LOGO_SIZE, LOGO_TOP = 96, 34


def card(theme: Theme, ratio: float) -> QPixmap:
    """The picture of the card, sharp at `ratio` device pixels per logical pixel."""
    pixmap = QPixmap(round(WIDTH * ratio), round(HEIGHT * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    outline = QPainterPath()
    outline.addRoundedRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1), RADIUS, RADIUS)
    painter.fillPath(outline, QColor(theme.raised))
    painter.setPen(QColor(theme.border))
    painter.drawPath(outline)
    logo = QPixmap(str(LOGO))
    if not logo.isNull():
        logo = logo.scaled(round(LOGO_SIZE * ratio), round(LOGO_SIZE * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        logo.setDevicePixelRatio(ratio)
        painter.drawPixmap(round((WIDTH - LOGO_SIZE) / 2), LOGO_TOP, logo)
    font = QFont(ui_font(), 16)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor(theme.text))
    painter.drawText(QRectF(0, LOGO_TOP + LOGO_SIZE + 14, WIDTH, 36), Qt.AlignmentFlag.AlignCenter, NAME)
    painter.end()
    return pixmap


class Splash(QWidget):
    """The card in a window of its own. Not QSplashScreen: its show() waits up to a second for the screen."""

    def __init__(self, picture: QPixmap):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # the rounded corners
        self.setFixedSize(WIDTH, HEIGHT)
        self._picture = picture

    def paintEvent(self, event) -> None:
        QPainter(self).drawPixmap(0, 0, self._picture)


def show_splash(theme_name: str) -> Splash:
    """The card in the theme the app starts in, shown and drawn now, on the screen the pointer is on (where the
    app was started from)."""
    if theme_name == "system":
        theme = LIGHT if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light else DARK
    else:
        theme = THEMES.get(theme_name, DARK)
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    splash = Splash(card(theme, screen.devicePixelRatio()))
    splash.move(screen.availableGeometry().center() - splash.rect().center())
    splash.show()
    QApplication.processEvents()  # on screen before the app goes on loading, not when its event loop starts
    return splash
