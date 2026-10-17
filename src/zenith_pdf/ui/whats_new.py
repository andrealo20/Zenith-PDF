"""The tour of what changed, shown once after an update: an overview, then one card per highlight with a
Show me button that opens the tool, setting or command it is about. The guide keeps the full list."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.platforms import native_keys
from ..core.releases import Highlight, Release
from ..icons import icon
from ..theme import themes

WIDTH = 560
GLYPH = 44  # the icon of a highlight's card
ROW_GLYPH = 20  # the icons of the overview


def _label(text: str, name: str, rich: bool = False) -> QLabel:
    label = QLabel(native_keys(text))
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    return label


class WhatsNewDialog(QDialog):
    """`show_me` carries a highlight's action when its button is pressed; the dialog then closes."""

    show_me = Signal(str)

    def __init__(self, releases: list[Release], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MessageDialog")
        self.setWindowTitle("What's new")
        self.setFixedWidth(WIDTH)
        self.highlights: list[Highlight] = [h for release in releases for h in release.highlights]
        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview(releases))
        for highlight in self.highlights:
            self.pages.addWidget(self._card(highlight))

        self.dots = QLabel()
        self.dots.setObjectName("Muted")
        self.back = QPushButton("Back")
        self.back.setAutoDefault(False)
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next = QPushButton()
        self.next.setObjectName("Primary")
        self.next.setDefault(True)
        self.next.clicked.connect(self._next)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.dots)
        row.addStretch()
        row.addWidget(self.back)
        row.addWidget(self.next)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)
        layout.addWidget(self.pages, 1)
        layout.addLayout(row)
        self.go(0)

    def _overview(self, releases: list[Release]) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        newest = releases[0].version if releases else __version__
        column.addWidget(_label(f"Zenith PDF {newest} is here", "DialogTitle"))
        column.addWidget(_label("Here is what changed. Next shows each one, and Show me takes you to it.",
                                "DialogBody"))
        for release in releases:
            if len(releases) > 1:
                column.addWidget(_label(f"Version {release.version}", "WhatsNewVersion"))
            for highlight in release.highlights:
                row = QHBoxLayout()
                row.setSpacing(10)
                glyph = QLabel()
                glyph.setFixedSize(ROW_GLYPH, ROW_GLYPH)
                glyph.setPixmap(icon(highlight.icon, themes.current.accent).pixmap(
                    QSize(ROW_GLYPH, ROW_GLYPH), self.devicePixelRatioF()))
                row.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(_label(highlight.title, "DialogBody"), 1)
                column.addLayout(row)
            for line in release.more:
                column.addWidget(_label(f"•  {line}", "Muted"))
        column.addStretch()
        return page

    def _card(self, highlight: Highlight) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(12)
        glyph = QLabel()
        glyph.setFixedSize(GLYPH, GLYPH)
        glyph.setPixmap(icon(highlight.icon, themes.current.accent).pixmap(QSize(GLYPH, GLYPH),
                                                                          self.devicePixelRatioF()))
        column.addWidget(glyph)
        column.addWidget(_label(highlight.title, "DialogTitle"))
        column.addWidget(_label(highlight.text, "DialogBody"))
        if highlight.action:
            show = QPushButton("Show me")
            show.setIcon(icon("arrow-right", themes.current.accent))
            show.setAutoDefault(False)
            show.setObjectName("ShowMe")
            show.clicked.connect(lambda _=False, action=highlight.action: self._show(action))
            column.addWidget(show, 0, Qt.AlignmentFlag.AlignLeft)
        column.addStretch()
        return page

    def go(self, index: int) -> None:
        index = min(max(index, 0), self.pages.count() - 1)
        self.pages.setCurrentIndex(index)
        last = index == self.pages.count() - 1
        self.back.setVisible(index > 0)
        self.next.setText("Done" if last else ("Show me each one" if index == 0 else "Next"))
        self.dots.setText(f"{index} of {len(self.highlights)}" if index else "")

    def _next(self) -> None:
        if self.pages.currentIndex() == self.pages.count() - 1:
            self.accept()
        else:
            self.go(self.pages.currentIndex() + 1)

    def _show(self, action: str) -> None:
        self.accept()
        self.show_me.emit(action)
