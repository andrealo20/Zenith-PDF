"""Telling about new versions and installing them: one Updater for the app, a card in the window.

The check runs at most once a day, in a thread, when Settings > Help allows it. With Install updates
automatically on, a new version downloads in the background and installs when Zenith PDF closes; the
card offers to restart now. Otherwise the card says a version is out and offers to update.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from ..core import updates
from ..core.i18n import tr
from ..core.platforms import package_family
from ..core.updates import Update, UpdateError
from ..settings import Settings
from ..theme import themes
from .widgets import FloatingBar, IconButton, icon_pixmap

CARD_WIDTH = 360
EDGE = 16  # the card's distance from the window's right edge and from the tabs


def _first_line(notes: str) -> str:
    """The first line of a release's notes that says something, without Markdown marks."""
    for line in notes.splitlines():
        line = line.strip().lstrip("#*-• ").strip()
        if line and not line.lower().startswith(("what's changed", "full changelog")):
            return line if len(line) <= 140 else line[:137] + "…"
    return ""


class Updater(QObject):
    """Finds, downloads and installs new versions; the windows show its state on their cards."""

    found = Signal(object)  # an Update newer than this version
    up_to_date = Signal()  # a check asked for by hand found nothing newer
    progress = Signal(int, int)  # bytes done, bytes in all
    ready = Signal(object)  # the downloaded file is checked and waits to be installed
    failed = Signal(str)
    _checked = Signal(float)  # from the checking thread: when it asked, for the GUI thread to store

    def __init__(self, settings: Settings, parent: QObject | None = None, fetch=updates.latest,
                 fetch_file=updates.download, kind: str | None = None):
        super().__init__(parent)
        self._settings = settings
        self._fetch, self._fetch_file = fetch, fetch_file
        self.kind = updates.install_kind() if kind is None else kind
        self.update: Update | None = None
        self.file: Path | None = None
        self.relaunch = False  # whether the app starts again after installing at quit
        self.pending = False  # install when the app quits
        self.busy = False
        self._stop = False
        self._checked.connect(self._remember_check)

    def _remember_check(self, when: float) -> None:
        self._settings.set("update_last_check", when)

    # Checking ---------------------------------------------------------------------------

    def due(self) -> bool:
        if package_family():  # the Microsoft Store updates its copy
            return False
        last = float(self._settings.get("update_last_check") or 0)
        return bool(self._settings.get("update_check")) and time.time() - last >= updates.CHECK_EVERY

    def check(self, manual: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._check, args=(manual,), daemon=True).start()

    def _check(self, manual: bool) -> None:
        try:
            update = self._fetch()
        except Exception as error:  # any error, or busy stays on and no check runs again until a restart
            self.busy = False
            if manual:
                self.failed.emit(str(error))
            return
        self._checked.emit(time.time())
        self.busy = False
        if not update.newer:
            if manual:
                self.up_to_date.emit()
            return
        if not manual and update.version == self._settings.get("update_skipped"):
            return
        self.update = update
        self.found.emit(update)
        if self._settings.get("update_auto") and self.kind and update.sha256:
            self.download()

    # Downloading and installing ---------------------------------------------------------

    def download(self) -> None:
        if self.busy or self.update is None:
            return
        self.busy, self._stop = True, False
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self) -> None:
        try:
            path = self._fetch_file(self.update, updates.downloads_folder(), progress=self.progress.emit,
                                    cancelled=lambda: self._stop)
        except Exception as error:
            self.busy = False
            if not self._stop:  # stopped from the card, which is closed already
                self.failed.emit(str(error))
            return
        self.busy = False
        self.file = path
        self.pending, self.relaunch = True, False  # installs when the app closes, unless restarted now
        self.ready.emit(path)

    def cancel(self) -> None:
        self._stop = True

    def skip(self) -> None:
        if self.update is not None:
            self._settings.set("update_skipped", self.update.version)

    def restart_now(self) -> None:
        """Close every window (each asks about unsaved changes) and install; the app starts again after."""
        from .main_window import MainWindow

        self.pending, self.relaunch = True, True
        for window in list(MainWindow.open_windows):
            if not window.close():
                self.relaunch = False  # kept open: install when the app closes, without starting again
                return

    def install_at_quit(self) -> None:
        """Connected to aboutToQuit: the checked file installs as the app goes."""
        if not self.pending or self.file is None or not self.kind:
            return
        self.pending = False
        try:
            updates.install(self.file, self.kind, relaunch=self.relaunch)
        except (UpdateError, OSError):
            pass  # nothing to tell anyone any more: the next start finds the update again


class UpdateCard(FloatingBar):
    """The card at the top right of a window: a version is out, downloading, ready, or a problem."""

    def __init__(self, updater: Updater, parent: QWidget):
        super().__init__(parent, solid=True)
        self.updater = updater
        self.setObjectName("UpdateCard")
        self.setFixedWidth(CARD_WIDTH)
        self.glyph = QLabel()
        self.glyph.setFixedSize(20, 20)
        self.title = QLabel()
        self.title.setObjectName("HelpTitle")
        self.title.setWordWrap(True)
        close = IconButton("x", "Close", icon_size=14)
        close.clicked.connect(self.hide)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.title, 1)
        header.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.text = QLabel()
        self.text.setObjectName("TipText")
        self.text.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 14)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.text)
        layout.addWidget(self.bar)
        layout.addLayout(self.buttons)
        updater.found.connect(self._found)
        updater.progress.connect(self._progress)
        updater.ready.connect(self._ready)
        updater.failed.connect(self._failed)
        updater.up_to_date.connect(self._up_to_date)
        themes.changed.connect(self._restyle)
        self._restyle()
        self.hide()

    def _restyle(self, *_) -> None:
        t = themes.current
        self.glyph.setPixmap(icon_pixmap("arrow-circle-up", 20, t.accent))
        self.bar.setStyleSheet(f"QProgressBar {{ background: {t.border}; border: none; border-radius: 3px; }}"
                               f"QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}")

    def _set(self, title: str, text: str, buttons: list[tuple[str, object, bool]], progress: bool = False) -> None:
        self.title.setText(tr(title))
        self.text.setText(tr(text))
        self.text.setVisible(bool(text))
        self.bar.setVisible(progress)
        while self.buttons.count():
            widget = self.buttons.takeAt(0).widget()  # held: once unparented, a temporary wrapper would delete it
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.buttons.addStretch()
        for label, slot, primary in buttons:
            button = QPushButton(tr(label))
            button.setObjectName("Primary" if primary else "")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the card never takes the keyboard from the pages
            button.clicked.connect(slot)
            self.buttons.addWidget(button)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.width() - self.width() - EDGE, EDGE + 40)

    # States -------------------------------------------------------------------------------

    def _found(self, update: Update) -> None:
        if self.updater.busy:  # downloading by itself: the card waits until it is ready
            return
        summary = _first_line(update.notes)
        if self.updater.kind and update.sha256:
            self._set(f"Zenith PDF {update.version} is out", summary,
                      [("Skip this version", self._skip, False), ("Later", self.hide, False),
                       ("Update", self._update, True)])
        else:  # run from source, or a copy that can't replace itself
            self._set(f"Zenith PDF {update.version} is out", summary or "See what changed on the release page.",
                      [("Later", self.hide, False), ("Open the release page", lambda: self._open(update.page), True)])

    def _update(self) -> None:
        self._set("Downloading the update…", "", [("Cancel", self._cancel, False)], progress=True)
        self.bar.setRange(0, 0)
        self.updater.download()

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(done * 1000 / total))

    def _ready(self, _path) -> None:
        version = self.updater.update.version if self.updater.update else ""
        self._set(f"Zenith PDF {version} is ready", "It installs when you close Zenith PDF, or now: your documents "
                  "reopen where they were.",
                  [("When I close", self.hide, False), ("Restart now", self.updater.restart_now, True)])

    def _failed(self, message: str) -> None:
        self._set("The update didn't work", message, [("Close", self.hide, False),
                                                     ("Try again", lambda: self.updater.check(manual=True), True)])

    def _up_to_date(self) -> None:
        from .. import __version__

        self._set("Zenith PDF is up to date", f"Version {__version__} is the latest.", [("OK", self.hide, True)])

    def _skip(self) -> None:
        self.updater.skip()
        self.hide()

    def _cancel(self) -> None:
        self.updater.cancel()
        self.hide()

    def _open(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
        self.hide()
