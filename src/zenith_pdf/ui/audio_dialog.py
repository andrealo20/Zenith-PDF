"""Audio notes: record one from the microphone, attach a sound file, play one back on the page."""

from __future__ import annotations

import hashlib
import html
import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import AUDIO_SUFFIXES
from ..core.audio import MAX_SECONDS, AudioError, Recorder, default_player as Player, input_device_count, temp_folder
from ..core.platforms import MAC, WINDOWS, settings_place
from ..icons import icon
from ..theme import themes
from . import messages
from .widgets import FloatingBar, IconButton, muted_label

LARGE_FILE = 50 * 1024 * 1024  # bytes; a bigger file makes a heavy PDF, so ask first
QUIET_SECONDS = 2.0  # this long with no sound and the dialog says so
ASK_DISCARD_SECONDS = 5.0  # a take this long is confirmed before Cancel drops it; a shorter one just goes
DIALOG_WIDTH = 440
RECORD_RED = "#e5484d"
READY = f"Press the red button to record, up to {MAX_SECONDS // 60} minutes."
RECORDING = "Recording. Press the square to stop."
RECORDED = "Listen to it, record it again, or add it to the page."
# Short enough for two lines with their link: the status keeps room for two, so the dialog never jumps.
NO_SOUND = "No sound is coming in. Check the microphone, and that desktop apps may use it."
NO_DEVICE = "No microphone was found. Plug one in, or attach a sound file instead."
# The system's own pages for the microphone, where it has them: links nowhere would be dead.
if WINDOWS:
    PRIVACY_SETTINGS = ("Microphone privacy settings", "ms-settings:privacy-microphone")
    SOUND_SETTINGS = ("Sound settings", "ms-settings:sound")
elif MAC:
    PRIVACY_SETTINGS = ("Microphone privacy settings",
                        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
    SOUND_SETTINGS = ("Sound settings", "x-apple.systempreferences:com.apple.preference.sound")
else:
    PRIVACY_SETTINGS = SOUND_SETTINGS = None
STATUS_LINES = 2  # the status line keeps room for this many lines


def clock(seconds: float) -> str:
    """0:07, 1:05, 10:00."""
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def note_name(when: datetime | None = None) -> str:
    return f"Audio note {(when or datetime.now()):%Y-%m-%d %H.%M}.wav"


class LevelMeter(QWidget):
    """How loud the microphone is, on a decibel scale from -60 dBFS (empty) to 0 (full).

    While a recording plays it shows how far along it is instead (set_fraction).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0
        self._live = False
        self.setFixedHeight(6)
        self.setMinimumWidth(120)
        themes.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(288, 6)

    def set_level(self, level: float) -> None:
        shown = max(0.0, min(1.0, (20 * math.log10(level) + 60) / 60)) if level > 0 else 0.0
        self._level = max(shown, self._level * 0.8)  # rises at once, falls back smoothly
        self._live = True
        self.update()

    def set_fraction(self, fraction: float) -> None:
        self._level = max(0.0, min(1.0, fraction))
        self._live = False
        self.update()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setBrush(QColor(t.border))
        painter.drawRoundedRect(rect, radius, radius)
        if self._level > 0.01:
            painter.setBrush(QColor(RECORD_RED if self._live and self._level > 0.97 else t.accent))
            painter.drawRoundedRect(QRectF(0, 0, max(rect.height(), rect.width() * self._level), rect.height()),
                                    radius, radius)


class _RoundButton(QAbstractButton):
    """The big button of the dialog: a red dot to record, a red square to stop, a triangle to play."""

    TIPS = {"record": "Record", "stop": "Stop", "play": "Play", "pause": "Pause"}

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_kind(kind)
        themes.changed.connect(self.update)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.setToolTip(self.TIPS[kind])
        self.setAccessibleName(self.TIPS[kind])
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(64, 64)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mark = QColor(RECORD_RED if self.kind in ("record", "stop") else t.accent)
        if self.isDown():
            mark = mark.darker(115)
        elif self.underMouse():
            mark = mark.lighter(112)
        ring = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        ring_color = QColor(t.accent if self.hasFocus() and self.isEnabled() else t.border)
        if self.hasFocus():
            ring_color.setAlpha(150)
        painter.setPen(QPen(ring_color, 2.0))
        painter.setBrush(QColor(t.raised))
        painter.drawEllipse(ring)
        if not self.isEnabled():  # no microphone: a crossed-out one, not a grey dot that looks broken
            glyph = icon("microphone-slash", t.text_muted).pixmap(QSize(28, 28), self.devicePixelRatioF())
            painter.drawPixmap(QRectF(ring.center().x() - 14, ring.center().y() - 14, 28, 28), glyph,
                               QRectF(glyph.rect()))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mark)
        x, y = ring.center().x(), ring.center().y()
        if self.kind == "record":
            painter.drawEllipse(ring.center(), 21, 21)
        elif self.kind == "stop":
            painter.drawRoundedRect(QRectF(x - 11, y - 11, 22, 22), 5, 5)
        elif self.kind == "pause":
            painter.drawRoundedRect(QRectF(x - 10, y - 12, 7, 24), 2, 2)
            painter.drawRoundedRect(QRectF(x + 3, y - 12, 7, 24), 2, 2)
        else:  # a triangle, nudged right so it looks centred
            painter.drawPolygon(QPolygonF([QPointF(x - 7, y - 13), QPointF(x + 14, y), QPointF(x - 7, y + 13)]))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


def _open_link(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class RecordDialog(QDialog):
    """Record an audio note: Record, Stop, listen, Record again, Add note; or attach a file instead."""

    def __init__(self, parent=None, recorder: Recorder | None = None, player_factory=Player, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Audio note")
        self.setFixedWidth(DIALOG_WIDTH)
        self._real_device = recorder is None
        self.recorder = recorder if recorder is not None else Recorder()
        self._player_factory = player_factory
        self._player = None
        self._start_dir = start_dir
        self._attached: tuple[str, bytes] | None = None
        self._name = ""
        self._can_record = True
        self.state = "ready"  # ready, recording, recorded, playing
        self.message = ""  # what the status line says, as plain text

        # One big round button at a time: Record, then Stop, then Play or Pause.
        self.record_button = _RoundButton("record")
        self.record_button.clicked.connect(self.record)
        self.stop_button = _RoundButton("stop")
        self.stop_button.clicked.connect(self.stop)
        self.play_button = _RoundButton("play")
        self.play_button.clicked.connect(self.toggle_play)
        self.time_label = QLabel("0:00")
        self.meter = LevelMeter()
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(18, 18)
        self.status = QLabel()
        self.status.setObjectName("DialogBody")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.status.linkActivated.connect(_open_link)

        self.again_button = QPushButton("Record again")
        self.again_button.clicked.connect(self.record_again)
        self.attach_button = QPushButton("Attach a file…")
        self.attach_button.setToolTip("Attach a sound file instead of recording")
        self.attach_button.clicked.connect(self.attach)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Add note")
        self.add_button.setObjectName("Primary")
        self.add_button.clicked.connect(self._add)
        for button in (self.again_button, self.attach_button, cancel, self.add_button):
            button.setAutoDefault(False)  # Enter adds the note once there is one, never anything else
        self._refresh_icons()
        themes.changed.connect(self._refresh_icons)

        heading = QLabel("Audio note")
        heading.setObjectName("DialogTitle")
        intro = QLabel("Record from the microphone, or attach a sound file.")
        intro.setObjectName("DialogBody")
        intro.setWordWrap(True)
        readout = QVBoxLayout()
        readout.setSpacing(8)
        readout.addStretch()
        readout.addWidget(self.time_label)
        readout.addWidget(self.meter)
        readout.addStretch()
        recorder_row = QHBoxLayout()
        recorder_row.setSpacing(16)
        for button in (self.record_button, self.stop_button, self.play_button):
            recorder_row.addWidget(button)
        recorder_row.addLayout(readout, 1)
        # The status keeps room for two lines, so the dialog keeps its size from one state to the
        # next; a one-line message sits in the middle of that room, not on top of an empty line.
        status_box = QWidget()
        status_column = QVBoxLayout(status_box)
        status_column.setContentsMargins(0, 0, 0, 0)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.status, 1)
        status_column.addStretch()
        status_column.addLayout(status_row)
        status_column.addStretch()
        self.status.ensurePolished()  # the style sheet's font, not the default one
        status_box.setMinimumHeight(self.status.fontMetrics().lineSpacing() * STATUS_LINES + 2)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.attach_button)  # before a take; Record again after it
        buttons.addWidget(self.again_button)
        buttons.addStretch()
        buttons.addWidget(self.add_button)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(0)
        layout.addWidget(heading)
        layout.addSpacing(8)
        layout.addWidget(intro)
        layout.addSpacing(20)
        layout.addLayout(recorder_row)
        layout.addSpacing(12)
        layout.addWidget(status_box)
        layout.addSpacing(20)  # as in the other dialogs, 20 above the buttons
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._say(READY)
        self._set_state("ready")
        if self._real_device and input_device_count() == 0:
            self._unavailable(NO_DEVICE, SOUND_SETTINGS)

    # States -------------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self.state = state
        has_take = state in ("recorded", "playing")
        self.record_button.setVisible(state == "ready")
        self.record_button.setEnabled(self._can_record)
        self.stop_button.setVisible(state == "recording")
        self.play_button.setVisible(has_take)
        self.play_button.set_kind("pause" if state == "playing" else "play")
        self.again_button.setVisible(has_take)
        self.attach_button.setVisible(not has_take)
        self.add_button.setEnabled(has_take)
        self.add_button.setDefault(has_take)
        # The clock turns red while the microphone is open, so it is plain that it records.
        self.time_label.setStyleSheet("font-size: 20pt; font-weight: 600;"
                                      + (f" color: {RECORD_RED};" if state == "recording" else ""))
        if state in ("recording", "playing"):
            self._timer.start()
        else:
            self._timer.stop()
        if state != "playing":
            self.meter.set_fraction(0.0)
        self._show_time()
        for button in (self.record_button, self.stop_button, self.play_button):
            if button.isVisibleTo(self) and button.isEnabled():
                button.setFocus()  # Space works the big button in every state

    def _say(self, text: str, problem: bool = False, link: tuple[str, str] | None = None) -> None:
        """Put a message on the status line; a problem gets a warning sign, and maybe a settings link."""
        self.message = text
        shown = html.escape(text)
        if link is not None:
            shown += f' <a href="{link[1]}">{html.escape(link[0])}</a>'
        self.status.setText(shown)
        self.status_icon.setVisible(problem)
        if problem:
            self.status_icon.setPixmap(icon("warning-circle", themes.current.danger).pixmap(
                QSize(18, 18), self.devicePixelRatioF()))

    def _unavailable(self, reason: str, link: tuple[str, str] | None = PRIVACY_SETTINGS) -> None:
        self._can_record = False
        self.record_button.setEnabled(False)
        self.record_button.setToolTip("Recording is not available")
        self._say(reason, problem=True, link=link)
        self.attach_button.setFocus()

    def _refresh_icons(self, *_) -> None:
        self.again_button.setIcon(icon("arrow-counter-clockwise"))
        self.attach_button.setIcon(icon("paperclip"))
        if self.status_icon.isVisibleTo(self):
            self.status_icon.setPixmap(icon("warning-circle", themes.current.danger).pixmap(
                QSize(18, 18), self.devicePixelRatioF()))

    def _show_time(self) -> None:
        if self.state == "playing" and self._player is not None:
            position, length = self._player.position, self._player.length
            self.time_label.setText(f"{clock(position)} / {clock(length)}")
            self.meter.set_fraction(position / length if length else 0.0)
        else:
            self.time_label.setText(clock(self.recorder.seconds))

    def _tick(self) -> None:
        if self.state == "recording":
            self.meter.set_level(self.recorder.level)
            if not self.recorder.recording:  # the device failed, or the time ran out
                failure = self.recorder.error
                self._finish_recording()
                if failure:
                    self._say(f"Recording stopped: {failure}", problem=True)
                elif self.recorder.full:
                    self._say(f"Recording stops at {MAX_SECONDS // 60} minutes. {RECORDED}")
                return
            if not self.recorder.heard and self.recorder.seconds >= QUIET_SECONDS:
                if self.message != NO_SOUND:
                    self._say(NO_SOUND, problem=True, link=PRIVACY_SETTINGS)
            elif self.recorder.heard and self.message != RECORDING:
                self._say(RECORDING)
        elif self.state == "playing" and self._player is not None and not self._player.playing:
            self._set_state("recorded")
            return
        self._show_time()

    # Actions ------------------------------------------------------------------------

    def record(self) -> None:
        if self.state != "ready" or not self._can_record:
            return
        if self._real_device and not WINDOWS and not self._microphone_allowed():
            return
        try:
            self.recorder.start()
        except AudioError as error:
            self._unavailable(f"The microphone could not be opened. {error}")  # Attach a file has the focus now
            return
        self._name = note_name()
        self._say(RECORDING)
        self._set_state("recording")

    def _microphone_allowed(self) -> bool:
        """macOS asks the user once before an app may use the microphone. Asks, and records on a yes."""
        try:
            from PySide6.QtCore import QMicrophonePermission
        except ImportError:  # a Qt before 6.5
            return True
        app = QApplication.instance()
        status = app.checkPermission(QMicrophonePermission())
        if status == Qt.PermissionStatus.Granted:
            return True
        if status == Qt.PermissionStatus.Undetermined:
            app.requestPermission(QMicrophonePermission(), self, lambda _answer: self._permission_answered())
        else:
            self._refused()
        return False

    def _permission_answered(self) -> None:
        from PySide6.QtCore import QMicrophonePermission

        if QApplication.instance().checkPermission(QMicrophonePermission()) == Qt.PermissionStatus.Granted:
            self.record()
        else:
            self._refused()

    def _refused(self) -> None:
        self._unavailable(f"Zenith PDF may not use the microphone. Allow it in {settings_place('microphone')}.")

    def stop(self) -> None:
        if self.state == "recording":
            self._finish_recording()

    def _finish_recording(self) -> None:
        self.recorder.stop()
        if self.recorder.seconds <= 0:
            self._say(self.recorder.error or "Nothing was recorded. Try again.", problem=True)
            self._set_state("ready")
            return
        if self.recorder.heard:
            self._say(RECORDED)
        else:
            self._say(NO_SOUND, problem=True, link=PRIVACY_SETTINGS)
        self._set_state("recorded")

    def toggle_play(self) -> None:
        if self.state == "playing":
            try:
                self._player.pause()
            except AudioError:
                self._close_player()
            self._set_state("recorded")
            return
        if self.state != "recorded":
            return
        try:
            if self._player is None:
                self._player = self._player_factory(self.recorder.wav(), "recording.wav")
            self._player.play()
        except AudioError as error:
            self._close_player()
            self._say(f"It can't be played here: {error} It can still be added to the page.", problem=True)
            self.play_button.setEnabled(False)
            self.add_button.setFocus()
            return
        self._set_state("playing")

    def record_again(self) -> None:
        self._close_player()
        self.recorder.clear()
        self.play_button.setEnabled(True)
        self._say(READY)
        self._set_state("ready")
        self.record()

    def attach(self) -> None:
        if self.state == "recording":
            self._finish_recording()
        chosen = choose_audio_file(self, self._start_dir)
        if chosen is not None:
            self._attached = chosen
            self.accept()

    def _add(self) -> None:
        if self.state == "recording":
            self._finish_recording()
        if self.recorder.seconds > 0:
            self.accept()

    def audio(self) -> tuple[str, bytes] | None:
        """(file name, bytes) of the note, after exec() returned Accepted."""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        if self._attached is not None:
            return self._attached
        if self.recorder.seconds > 0:
            return self._name or note_name(), self.recorder.wav()
        return None

    def _close_player(self) -> None:
        if self._player is not None:
            player, self._player = self._player, None
            player.close()

    def reject(self) -> None:
        """Cancel, Esc or the close button: a take of a few seconds or more is confirmed before it goes."""
        if self.state == "recording":
            self._finish_recording()
        if self.state == "playing":
            self.toggle_play()
        if self._attached is None and self.recorder.seconds >= ASK_DISCARD_SECONDS:
            answer = messages.ask(self, "Discard the recording?",
                                  f"It is {clock(self.recorder.seconds)} long and is not on the page yet.",
                                  [("discard", "Discard", "danger"), (messages.CANCEL, "Go back", "")],
                                  icon="trash")
            if answer != "discard":
                return
        super().reject()

    def done(self, result: int) -> None:
        """However the dialog closes, the microphone and the player are let go; a cancel drops the take."""
        self._timer.stop()
        self.recorder.stop()
        self._close_player()
        if result != QDialog.DialogCode.Accepted:
            self.recorder.clear()
        super().done(result)


# Choosing a file ----------------------------------------------------------------------


def choose_audio_file(parent, start_dir: str) -> tuple[str, bytes] | None:
    """Ask for a sound file; (file name, bytes), or None if cancelled or unreadable."""
    patterns = " ".join(f"*{suffix}" for suffix in AUDIO_SUFFIXES)
    path, _ = QFileDialog.getOpenFileName(parent, "Attach an audio file", start_dir,
                                          f"Audio files ({patterns});;All files (*)")
    if not path:
        return None
    file = Path(path)
    try:
        size = file.stat().st_size
        if size > LARGE_FILE and not messages.confirm(
                parent, "Attach a large file?",
                f"{file.name} is {size / (1024 * 1024):.0f} MB, and the PDF grows by as much.", "Attach",
                icon="paperclip"):
            return None
        data = file.read_bytes()
    except OSError as error:
        messages.warn(parent, "The file could not be read", f"{file.name} can't be attached.",
                      detail=error.strerror or str(error))
        return None
    return file.name, data


def _ask_source(parent, global_pos) -> str | None:
    """The menu of the "ask" setting: "record", "file", or None if it was dismissed."""
    menu = QMenu(parent)
    record = menu.addAction(icon("microphone"), "Record from the microphone…")
    attach = menu.addAction(icon("paperclip"), "Attach an audio file…")
    chosen = menu.exec(global_pos if global_pos is not None else QCursor.pos())
    menu.deleteLater()
    return "record" if chosen is record else "file" if chosen is attach else None


def ask_audio(parent, source: str, start_dir: str, global_pos=None) -> tuple[str, bytes] | None:
    """An audio note to place: recorded ("record"), from a file ("file"), or whichever the user picks ("ask")."""
    if source not in ("record", "file"):
        source = _ask_source(parent, global_pos)
    if source == "file":
        return choose_audio_file(parent, start_dir)
    if source == "record":
        dialog = RecordDialog(parent, start_dir=start_dir)
        try:
            return dialog.audio() if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()
    return None


# Playing a note on the page -----------------------------------------------------------


class _Scrubber(QSlider):
    """Where the sound is: a thin track filled up to the position, with a round handle.

    A press jumps straight to that point and dragging follows the pointer, as in
    media players (a plain QSlider steps a page at a time on a click).
    """

    HANDLE = 7.0  # radius

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(20)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the popover takes the keys
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        themes.changed.connect(self.update)

    def _value_at(self, x: float) -> int:
        span = max(1.0, self.width() - 2 * self.HANDLE)
        fraction = max(0.0, min(1.0, (x - self.HANDLE) / span))
        return round(self.minimum() + fraction * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setSliderDown(True)  # sliderPressed
        self.setValue(self._value_at(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            self.setSliderDown(False)  # sliderReleased
        event.accept()

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        span = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / span if span else 0.0
        left, right = self.HANDLE, self.width() - self.HANDLE
        middle = self.height() / 2
        x = left + fraction * (right - left)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.border))
        painter.drawRoundedRect(QRectF(left, middle - 2, right - left, 4), 2, 2)
        painter.setBrush(QColor(t.accent))
        painter.drawRoundedRect(QRectF(left, middle - 2, x - left, 4), 2, 2)
        # The handle of the app's other sliders (theme.py): white, a faint rim, the accent under the pointer.
        hot = self.isSliderDown() or self.underMouse()
        painter.setPen(QPen(QColor(t.accent) if hot else QColor(0, 0, 0, 61), 1.0))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(x, middle), self.HANDLE - 0.5, self.HANDLE - 0.5)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class AudioPopover(FloatingBar):
    """A small player that opens by an audio note: play or pause, a position bar, the time, close.

    Space plays or pauses, the arrow keys go 5 s back or forward, Esc closes it.
    """

    STEP = 5.0  # seconds an arrow key moves
    NAME_WIDTH = 190  # the note's name and the position bar under it

    def __init__(self, player, name: str, parent=None):
        super().__init__(parent, popup=True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.player = player
        self._name = Path(name).stem or name
        self.title = QLabel()
        self.title.setToolTip(name)
        self._show_title()
        self.close_button = IconButton("x", "Close", icon_size=14)
        self.close_button.setFixedSize(24, 24)
        self.close_button.clicked.connect(self.close)
        self.play_button = IconButton("play-fill", "Play", icon_size=22)
        self.play_button.setFixedSize(40, 40)
        self.play_button.clicked.connect(self.toggle)
        self.bar = _Scrubber()
        self.bar.setRange(0, max(1, round(player.length * 1000)))
        self.bar.setFixedWidth(self.NAME_WIDTH)
        self.bar.sliderReleased.connect(self._seek)
        self.time_label = muted_label()
        self.time_label.ensurePolished()
        self.time_label.setMinimumWidth(self.time_label.fontMetrics().horizontalAdvance("00:00 / 00:00") + 4)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_label.setContentsMargins(0, 0, 6, 0)  # ends where the close button's cross ends
        # The big play button on the left; the name over the position bar, the time under the close button,
        # so the name and the bar start in one line, and the close button and the time end in one.
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(0)
        layout.addWidget(self.play_button, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.title, 0, 1)
        layout.addWidget(self.close_button, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.bar, 1, 1)
        layout.addWidget(self.time_label, 1, 2)
        layout.setColumnStretch(1, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    def _show_title(self, problem: str = "") -> None:
        """The note's name, or what went wrong in its place."""
        self.title.ensurePolished()
        self.title.setText(self.title.fontMetrics().elidedText(problem or self._name, Qt.TextElideMode.ElideMiddle,
                                                               self.NAME_WIDTH))
        if problem:
            self.title.setToolTip(problem)

    def show_at(self, global_pos=None) -> None:
        """Show just below and to the right of `global_pos`, kept on its screen."""
        self.adjustSize()
        point = global_pos if global_pos is not None else QCursor.pos()
        point = QPoint(round(point.x()), round(point.y()))
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        x, y = point.x() + 12, point.y() + 12
        if screen is not None:
            area = screen.availableGeometry()
            if x + self.width() > area.right():
                x = point.x() - self.width() - 12
            if y + self.height() > area.bottom():
                y = point.y() - self.height() - 12
            x = max(area.left(), min(x, area.right() - self.width()))
            y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)
        self.show()

    def toggle(self) -> None:
        try:
            if self.player.playing:
                self.player.pause()
            else:
                self.player.play()
        except AudioError as error:
            self._show_title(str(error))
        self._tick()

    def skip(self, seconds: float) -> None:
        """Go forward (or back, for a negative number) by `seconds`."""
        try:
            self.player.seek(max(0.0, min(self.player.position + seconds, self.player.length)))
        except AudioError as error:
            self._show_title(str(error))
        self._tick()

    def _seek(self) -> None:
        try:
            self.player.seek(self.bar.value() / 1000)
        except AudioError as error:
            self._show_title(str(error))
        self._tick()

    def _tick(self) -> None:
        playing = self.player.playing
        position = self.player.position
        if not self.bar.isSliderDown():
            self.bar.blockSignals(True)
            self.bar.setValue(round(position * 1000))
            self.bar.blockSignals(False)
            self.bar.update()
        shown = self.bar.value() / 1000 if self.bar.isSliderDown() else position
        self.play_button.set_icon_name("pause-fill" if playing else "play-fill")
        self.play_button.setToolTip("Pause" if playing else "Play")
        self.time_label.setText(f"{clock(shown)} / {clock(self.player.length)}")

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_K, Qt.Key.Key_MediaPlay, Qt.Key.Key_MediaTogglePlayPause):
            self.toggle()
        elif key == Qt.Key.Key_Left:
            self.skip(-self.STEP)
        elif key == Qt.Key.Key_Right:
            self.skip(self.STEP)
        elif key == Qt.Key.Key_Home:
            self.skip(-self.player.length)
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        self.player.close()
        super().hideEvent(event)


def _open_elsewhere(name: str, data: bytes) -> None:
    """A sound the built-in player can't handle opens in the system's app for it."""
    folder = temp_folder() / hashlib.blake2b(data, digest_size=8).hexdigest()
    folder.mkdir(exist_ok=True)
    target = folder / (Path(name).name or "audio note")
    if not target.exists():
        target.write_bytes(data)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def play_audio(parent, name: str, data: bytes, global_pos=None, player_factory=Player) -> AudioPopover | None:
    """Play an audio note in a small popover by `global_pos`; formats it can't play open in another app.

    Only a sound file goes to another app: a note from someone else's PDF could
    carry a program under the audio flag, and that is never started.
    """
    try:
        player = player_factory(data, name)
    except AudioError as error:
        if not name.lower().endswith(AUDIO_SUFFIXES):
            messages.warn(parent, "The audio note can't be played", f"{name} is not a sound file Zenith PDF knows.",
                          detail=str(error))
            return None
        try:
            _open_elsewhere(name, data)
        except OSError as failure:
            messages.warn(parent, "The audio note can't be played", f"{name} could not be opened.",
                          detail=failure.strerror or str(failure))
        return None
    popover = AudioPopover(player, name, parent)
    popover.show_at(global_pos)
    popover.toggle()
    return popover
