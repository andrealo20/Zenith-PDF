"""Audio notes (the recorder and player, with stand-ins for the devices, and their dialogs) and the insert dialogs.

Nothing here touches the microphone or plays a sound, except the last test, which
records 0.3 s only when ZENITH_AUDIO_DEVICE_TESTS=1 is set.
"""

import io
import os
import threading
import time
import wave
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QStandardPaths, Qt
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QFileDialog, QLabel, QMenu, QPushButton

from zenith_pdf.core import audio
from zenith_pdf.core.audio import AudioError, Player, Recorder, wav_bytes, wav_seconds
from zenith_pdf.ui import audio_dialog, insert_dialogs
from zenith_pdf.ui.audio_dialog import LevelMeter, RecordDialog, ask_audio, clock, play_audio

TONE = (b"\x00\x10" + b"\x00\xf0") * 551  # 50 ms at 22050 Hz, peak 4096 (about -18 dBFS)


class FakeMic:
    """Stands in for waveIn: every read() brings 50 ms of `chunk`."""

    def __init__(self, chunk: bytes = TONE, fail: str = ""):
        self.chunk, self.fail = chunk, fail
        self.opened = self.closed = 0

    def open(self, rate, channels, bits):
        if self.fail:
            raise AudioError(self.fail)
        self.opened += 1

    def read(self):
        return [self.chunk]

    def close(self):
        self.closed += 1
        return [self.chunk[:200]]


class StuckMic(FakeMic):
    """A driver that takes its time to open: open() waits until `release` is set."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def open(self, rate, channels, bits):
        self.release.wait(5)
        super().open(rate, channels, bits)


class FakeMci:
    """Stands in for mciSendString, keeping the mode and position MCI would report."""

    def __init__(self, length_ms: int = 1500, refuse: bool = False):
        self.length_ms, self.refuse = length_ms, refuse
        self.commands: list[str] = []
        self.mode, self.position = "stopped", 0

    def __call__(self, command: str) -> str:
        self.commands.append(command)
        words = command.split()
        if words[0] == "open" and self.refuse:
            raise AudioError("The specified device is not open or is not recognized by MCI.")
        if words[0] == "status":
            return {"length": str(self.length_ms), "mode": self.mode, "position": str(self.position)}[words[2]]
        if words[0] == "play":
            self.mode = "playing"
            if "from" in words:
                self.position = int(words[-1])
        elif words[0] == "pause":
            self.mode = "paused"
        elif words[0] == "stop":
            self.mode = "stopped"
        elif words[0] == "seek":
            self.position = 0 if words[-1] == "start" else int(words[-1])
        return ""


def wait_for(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert condition()


def fake_players(made: list, length_ms: int = 1500):
    def factory(data, name):
        player = Player(data, name, send=FakeMci(length_ms))
        made.append(player)
        return player
    return factory


def clipped_text(dialog) -> list[str]:
    """Labels and buttons of a shown dialog whose one-line text doesn't fit their width."""
    dialog.show()
    QApplication.processEvents()
    clipped = []
    for label in dialog.findChildren(QLabel):
        if (label.isVisible() and label.text() and not label.wordWrap() and label.textFormat() != Qt.TextFormat.RichText
                and label.fontMetrics().horizontalAdvance(label.text()) > label.contentsRect().width() + 1):
            clipped.append(label.text())
    for button in dialog.findChildren(QPushButton):
        room = button.width() - (button.iconSize().width() + 4 if not button.icon().isNull() else 0) - 8
        if button.isVisible() and button.text() and button.fontMetrics().horizontalAdvance(button.text()) > room:
            clipped.append(button.text())
    dialog.close()
    return clipped


# Core -------------------------------------------------------------------------------


def test_wav_bytes_header_and_length():
    data = wav_bytes(TONE * 20)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    with wave.open(io.BytesIO(data)) as file:
        assert (file.getframerate(), file.getnchannels(), file.getsampwidth()) == (22050, 1, 2)
    assert wav_seconds(data) == pytest.approx(1.0, abs=0.001)
    assert wav_seconds(b"not a wave file") == 0.0
    assert wav_seconds(b"") == 0.0


def test_recorder_records_stops_and_clears():
    mic = FakeMic()
    recorder = Recorder(backend=mic)
    recorder.start()
    assert recorder.recording and mic.opened == 1
    wait_for(lambda: recorder.seconds > 0.2)
    assert recorder.heard and 0.1 < recorder.level < 0.2
    recorder.stop()
    recorder.stop()  # twice is fine
    assert not recorder.recording and mic.closed == 1 and recorder.level == 0.0
    seconds = recorder.seconds
    data = recorder.wav()
    assert wav_seconds(data) == pytest.approx(seconds, abs=0.001)
    recorder.start()  # carries on the same recording
    wait_for(lambda: recorder.seconds > seconds + 0.1)
    recorder.clear()
    assert recorder.seconds == 0.0 and not recorder.heard and not recorder.recording
    assert wav_seconds(recorder.wav()) == 0.0


def test_recorder_reports_a_device_that_will_not_open():
    recorder = Recorder(backend=FakeMic(fail="The device is already in use."))
    with pytest.raises(AudioError, match="already in use"):
        recorder.start()
    assert not recorder.recording and recorder.seconds == 0.0


def test_a_slow_driver_never_gets_two_workers(monkeypatch):
    monkeypatch.setattr(audio, "OPEN_SECONDS", 0.05)
    monkeypatch.setattr(audio, "JOIN_SECONDS", 0.05)
    mic = StuckMic()
    recorder = Recorder(backend=mic)
    with pytest.raises(AudioError, match="did not answer"):
        recorder.start()
    with pytest.raises(AudioError, match="did not answer"):  # the first worker is still in open()
        recorder.start()
    started = time.monotonic()
    recorder.stop()  # bounded: the window never freezes on a hung driver
    assert time.monotonic() - started < 1.0
    mic.release.set()
    wait_for(lambda: mic.closed == 1)  # the late worker closes the device at once and keeps nothing
    assert recorder.seconds == 0.0 and not recorder.recording
    mic.release.set()
    recorder.start()
    wait_for(lambda: recorder.seconds > 0.1)
    recorder.stop()
    assert mic.opened == 2 and recorder.heard


def test_recorder_stops_by_itself_at_the_limit():
    recorder = Recorder(backend=FakeMic())
    recorder._max_bytes = 22050 * 2 // 4  # a quarter of a second
    recorder.start()
    wait_for(lambda: not recorder.recording)
    assert recorder.seconds == pytest.approx(0.25, abs=0.001)
    recorder.start()  # nothing left to record into
    assert not recorder.recording


def test_silence_is_not_heard():
    recorder = Recorder(backend=FakeMic(chunk=bytes(2204)))
    recorder.start()
    wait_for(lambda: recorder.seconds > 0.2)
    recorder.stop()
    assert not recorder.heard


def test_player_uses_a_short_temporary_copy():
    mci = FakeMci()
    player = Player(wav_bytes(TONE * 30), "My note.wav", send=mci)
    opened = mci.commands[0]
    path = opened.split('"')[1]
    assert opened.startswith("open ") and "type waveaudio" in opened and len(path) < audio.MCI_PATH_LIMIT
    assert player.file.exists() and player.length == pytest.approx(1.5)
    player.play()
    assert player.playing
    player.pause()
    assert not player.playing
    player.seek(0.5)
    assert player.position == pytest.approx(0.5)
    player.close()
    player.close()
    assert not player.file.exists() and mci.commands.count(f"close {player.alias}") == 1


def test_player_opens_other_formats_through_directshow_and_reports_failure():
    mci = FakeMci()
    player = Player(b"ID3", "song.MP3 ", send=mci)  # an odd suffix still makes a clean file name
    assert "type mpegvideo" in mci.commands[0] and player.file.suffix == ".mp3"
    player.close()
    with pytest.raises(AudioError):
        Player(b"junk", "voice.m4a", send=FakeMci(refuse=True))
    assert not list(audio.temp_folder().glob("a*.m4a"))


def test_player_reports_a_temporary_folder_it_cannot_write(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "temp_folder", lambda: tmp_path / "gone" / "deeper")
    with pytest.raises(AudioError, match="could not be copied"):
        Player(wav_bytes(TONE), "note.wav", send=FakeMci())


def test_stale_player_copies_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "temp_folder", lambda: tmp_path)
    old = time.time() - audio.STALE_SECONDS - 60
    for name in ("a0123abcd.wav", "a89abcdef.mp3", "caret-down-bold-9b9ca5.png", "notes.wav"):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "a00000000.wav").write_bytes(b"x")  # a player that is still open
    audio._remove_stale_copies.__wrapped__()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a00000000.wav", "caret-down-bold-9b9ca5.png", "notes.wav"]


# Audio dialogs ----------------------------------------------------------------------


def test_clock():
    assert [clock(s) for s in (0, 7.9, 65, 600)] == ["0:00", "0:07", "1:05", "10:00"]


def test_level_meter_paints(qt_app):
    meter = LevelMeter()
    meter.resize(288, 6)
    for level in (0.0, 0.001, 0.1, 1.0):
        meter.set_level(level)
        assert not meter.grab().isNull()
    assert meter._level == pytest.approx(1.0)


def test_record_dialog_records_and_adds(qt_app):
    players = []
    dialog = RecordDialog(recorder=Recorder(backend=FakeMic()), player_factory=fake_players(players))
    assert dialog.state == "ready" and not dialog.add_button.isEnabled() and dialog.record_button.isEnabled()
    assert dialog.focusWidget() is dialog.record_button and dialog.message == audio_dialog.READY
    dialog.record_button.click()
    assert dialog.state == "recording" and dialog.stop_button.isVisibleTo(dialog)
    assert dialog.focusWidget() is dialog.stop_button  # Space stops, as it started
    wait_for(lambda: dialog.recorder.seconds > 1.2)
    dialog.stop_button.click()
    assert dialog.state == "recorded" and dialog.add_button.isEnabled() and dialog.add_button.isDefault()
    assert dialog.again_button.isVisibleTo(dialog) and not dialog.record_button.isVisibleTo(dialog)
    assert dialog.time_label.text().startswith("0:0")
    assert dialog.time_label.text() != "0:00"
    dialog.play_button.click()
    assert dialog.state == "playing" and dialog.play_button.kind == "pause" and players[0].playing
    dialog.play_button.click()
    assert dialog.state == "recorded"
    dialog.add_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    name, data = dialog.audio()
    assert name.startswith("Audio note ") and name.endswith(".wav") and data[:4] == b"RIFF"
    assert wav_seconds(data) > 1.2
    assert not players[0].file.exists()  # done() closed the player


def test_record_again_starts_over(qt_app):
    dialog = RecordDialog(recorder=Recorder(backend=FakeMic()), player_factory=fake_players([]))
    dialog.record_button.click()
    wait_for(lambda: dialog.recorder.seconds > 0.5)
    dialog.stop_button.click()
    first = dialog.recorder.seconds
    dialog.again_button.click()
    assert dialog.state == "recording" and dialog.recorder.seconds < first
    dialog.reject()


def test_cancelling_while_recording_stops_and_drops_it(qt_app):
    mic = FakeMic()
    dialog = RecordDialog(recorder=Recorder(backend=mic))
    dialog.record_button.click()
    wait_for(lambda: dialog.recorder.seconds > 0.1)
    dialog.reject()
    assert not dialog.recorder.recording and dialog.recorder.seconds == 0.0 and mic.closed == 1
    assert dialog.audio() is None


def test_a_long_take_is_confirmed_before_cancel_drops_it(qt_app, monkeypatch):
    monkeypatch.setattr(audio_dialog, "ASK_DISCARD_SECONDS", 0.3)
    answers = []
    monkeypatch.setattr(audio_dialog.messages, "ask", lambda *a, **k: answers.pop(0))
    dialog = RecordDialog(recorder=Recorder(backend=FakeMic()))
    dialog.show()
    dialog.record_button.click()
    wait_for(lambda: dialog.recorder.seconds > 0.5)
    answers.append(None)  # Go back
    dialog.reject()
    assert dialog.isVisible() and dialog.state == "recorded" and dialog.recorder.seconds > 0.5
    answers.append("discard")
    dialog.reject()
    assert not dialog.isVisible() and dialog.recorder.seconds == 0.0 and dialog.audio() is None


def test_no_sound_and_no_device_messages(qt_app):
    dialog = RecordDialog(recorder=Recorder(backend=FakeMic(chunk=bytes(2204))))
    dialog.record_button.click()
    wait_for(lambda: dialog.message == audio_dialog.NO_SOUND)
    assert dialog.status_icon.isVisibleTo(dialog)
    assert audio_dialog.PRIVACY_SETTINGS[1] in dialog.status.text()  # a link to the Windows page
    dialog.reject()
    broken = RecordDialog(recorder=Recorder(backend=FakeMic(fail="No driver.")))
    broken.record_button.click()
    assert broken.state == "ready" and not broken.record_button.isEnabled()
    assert "No driver." in broken.message and broken.attach_button.isEnabled()
    assert broken.focusWidget() is broken.attach_button and not broken.record_button.grab().isNull()
    broken.reject()


def test_no_microphone_at_all(qt_app, monkeypatch):
    monkeypatch.setattr(audio_dialog, "input_device_count", lambda: 0)
    monkeypatch.setattr(audio_dialog, "Recorder", lambda: Recorder(backend=FakeMic()))
    dialog = RecordDialog()
    assert dialog.message == audio_dialog.NO_DEVICE and not dialog.record_button.isEnabled()
    dialog.record()  # does nothing
    assert dialog.state == "ready" and dialog.recorder.seconds == 0.0
    dialog.reject()


def test_a_take_that_cannot_be_played_can_still_be_added(qt_app):
    def refuse(data, name):
        raise AudioError("No player.")

    dialog = RecordDialog(recorder=Recorder(backend=FakeMic()), player_factory=refuse)
    dialog.record_button.click()
    wait_for(lambda: dialog.recorder.seconds > 0.2)
    dialog.stop_button.click()
    dialog.play_button.click()
    assert dialog.state == "recorded" and "No player." in dialog.message and not dialog.play_button.isEnabled()
    dialog.add_button.click()
    assert dialog.audio()[1][:4] == b"RIFF"


def test_attach_instead(qt_app, tmp_path, monkeypatch):
    sound = tmp_path / "voice memo.mp3"
    sound.write_bytes(b"ID3 some sound")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(sound), "")))
    dialog = RecordDialog(recorder=Recorder(backend=FakeMic()))
    dialog.attach_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.audio() == ("voice memo.mp3", b"ID3 some sound")


def test_ask_audio_sources(qt_app, tmp_path, monkeypatch):
    sound = tmp_path / "a.wav"
    sound.write_bytes(wav_bytes(TONE))
    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda parent, title, folder, *a: asked.append(folder) or (str(sound), "")))
    assert ask_audio(None, "file", str(tmp_path)) == ("a.wav", sound.read_bytes())
    assert asked == [str(tmp_path)]

    class Menu(QMenu):  # QMenu.exec can't be patched on the class itself
        pick = 1

        def exec(self, *args):
            return self.actions()[self.pick] if self.pick is not None else None

    monkeypatch.setattr(audio_dialog, "QMenu", Menu)
    assert ask_audio(None, "ask", str(tmp_path), QPoint(10, 10)) == ("a.wav", sound.read_bytes())
    Menu.pick = None
    assert ask_audio(None, "ask", str(tmp_path), QPoint(10, 10)) is None

    class Answer:
        def __init__(self, parent=None, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def audio(self):
            return "Audio note.wav", b"RIFF"

        def deleteLater(self):
            pass

    monkeypatch.setattr(audio_dialog, "RecordDialog", Answer)
    Menu.pick = 0
    assert ask_audio(None, "ask", str(tmp_path)) == ("Audio note.wav", b"RIFF")
    assert ask_audio(None, "record", str(tmp_path)) == ("Audio note.wav", b"RIFF")


def test_large_file_is_confirmed(qt_app, tmp_path, monkeypatch):
    sound = tmp_path / "long.wav"
    sound.write_bytes(b"RIFF" + bytes(64))
    monkeypatch.setattr(audio_dialog, "LARGE_FILE", 10)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(sound), "")))
    answers = []
    monkeypatch.setattr(audio_dialog.messages, "confirm", lambda *a, **k: answers.append(a[2]) or False)
    assert audio_dialog.choose_audio_file(None, str(tmp_path)) is None
    assert "long.wav" in answers[0]


def test_play_audio_popover(qt_app, monkeypatch):
    players = []
    popover = play_audio(None, "Audio note 2026-10-16 09.30.wav", wav_bytes(TONE * 30), QPoint(40, 40),
                         player_factory=fake_players(players))
    assert popover is not None and popover.isVisible() and players[0].playing
    assert popover.time_label.text() == "0:00 / 0:01"
    assert popover.bar.maximum() == 1500
    popover.play_button.click()
    assert not players[0].playing
    assert not popover.grab().isNull()
    popover.close()
    assert not players[0].file.exists()

    opened = []
    monkeypatch.setattr(audio_dialog, "_open_elsewhere", lambda name, data: opened.append(name))

    def refuse(data, name):
        raise AudioError("Unsupported")

    assert play_audio(None, "voice.m4a", b"m4a", player_factory=refuse) is None
    assert opened == ["voice.m4a"]
    warned = []
    monkeypatch.setattr(audio_dialog.messages, "warn", lambda *a, **k: warned.append(a[2]))
    assert play_audio(None, "setup.exe", b"MZ", player_factory=refuse) is None  # a program is never started
    assert opened == ["voice.m4a"] and "setup.exe" in warned[0]


def test_popover_keys_and_scrubbing(qt_app):
    players = []
    popover = play_audio(None, "note.wav", wav_bytes(TONE), QPoint(40, 40),
                         player_factory=fake_players(players, length_ms=60000))
    player = players[0]
    QTest.keyClick(popover, Qt.Key.Key_Right)
    assert player.position == pytest.approx(5.0) and player.playing
    QTest.keyClick(popover, Qt.Key.Key_Left)
    QTest.keyClick(popover, Qt.Key.Key_Left)  # not before the start
    assert player.position == 0.0
    QTest.keyClick(popover, Qt.Key.Key_Space)
    assert not player.playing
    bar = popover.bar
    x = round(bar.width() * 3 / 4)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(x, bar.height() // 2))
    assert bar.isSliderDown() and player.position == 0.0  # it moves on release, not on every step
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(x, bar.height() // 2))
    wanted = (x - bar.HANDLE) / (bar.width() - 2 * bar.HANDLE) * 60
    assert player.position == pytest.approx(wanted, abs=0.01)  # a click jumps there, not a page step
    assert popover.time_label.text() == f"{clock(wanted)} / 1:00"
    QTest.keyClick(popover, Qt.Key.Key_Escape)
    assert not popover.isVisible() and not player.file.exists()


# Insert dialogs ---------------------------------------------------------------------


def test_link_dialog(qt_app):
    dialog = insert_dialogs.LinkDialog(21)
    assert dialog.web.isChecked() and not dialog.ok_button.isEnabled()  # no address yet
    dialog.address.setText("example.org")
    assert dialog.ok_button.isEnabled() and dialog.result_link() == ("https://example.org", -1)
    dialog.address.setText("name@example.org")
    assert dialog.result_link() == ("mailto:name@example.org", -1)
    dialog.page.setChecked(True)
    assert dialog.fields.currentIndex() == 1 and dialog.ok_button.isEnabled()
    dialog.number.setValue(30)
    assert dialog.result_link() == ("", 20)  # kept within the document
    assert clipped_text(dialog) == []
    edit = insert_dialogs.LinkDialog(5, "", 2, existing=True)
    assert edit.page.isChecked() and edit.number.value() == 3
    [remove] = [b for b in edit.findChildren(QPushButton) if b.text() == "Remove link"]
    remove.click()
    assert edit.removed and edit.result() == QDialog.DialogCode.Accepted


def test_scale_dialog_names_the_scale(qt_app):
    dialog = insert_dialogs.ScaleDialog((1, "cm", 1, "m"))
    assert dialog.presets.currentText() == "1:100"
    dialog.real.setValue(2)
    assert dialog.presets.currentText() == "Custom"
    dialog.presets.activated.emit(1)
    assert dialog.measure() == (1, "cm", 50, "cm") and dialog.presets.currentText() == "1:50"
    assert clipped_text(dialog) == []


def test_equation_dialog(qt_app):
    dialog = insert_dialogs.EquationDialog()
    assert not dialog.ok_button.isEnabled() and dialog.preview.text()
    dialog.source.setPlainText(r"\frac{a}")
    assert dialog.problem and not dialog.ok_button.isEnabled() and dialog.problem in dialog.preview.text()
    height = dialog.sizeHint().height()
    dialog.source.setPlainText(r"\sqrt{x}")
    assert not dialog.problem and dialog.ok_button.isEnabled() and not dialog.preview.pixmap().isNull()
    assert dialog.sizeHint().height() == height  # a message never moves the buttons
    [fraction] = [b for b in dialog.findChildren(QPushButton) if b.accessibleName() == "Fraction"]
    dialog.source.moveCursor(QTextCursor.MoveOperation.End)
    fraction.click()
    assert dialog.source.toPlainText() == r"\sqrt{x}\frac{a}{b}"
    assert all(name in button.toolTip() for name, _, _ in insert_dialogs.EXAMPLES
               for button in dialog.findChildren(QPushButton) if button.accessibleName() == name)
    assert clipped_text(dialog) == []
    QTest.keyClick(dialog.source, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.equation() == (r"\sqrt{x}\frac{a}{b}", 16.0)


def test_signature_dialog_needs_something_to_save(qt_app, tmp_path, monkeypatch):
    dialog = insert_dialogs.SignatureDialog("Andrea")
    assert dialog.ok_button.isEnabled() and dialog.signature()["name"] == "Typed signature"
    dialog.name.clear()
    assert not dialog.ok_button.isEnabled() and dialog.signature() is None
    dialog.mode.set_current(1)
    assert dialog.pages.currentIndex() == 1 and not dialog.ok_button.isEnabled()
    dialog.show()
    QTest.mousePress(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(40, 60))
    for x in range(50, 200, 10):
        QTest.mouseMove(dialog.pad, QPoint(x, 60 + x % 30))
    QTest.mouseRelease(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(200, 70))
    assert dialog.ok_button.isEnabled() and dialog.signature()["name"] == "Drawn signature"
    dialog.pad.clear()
    assert not dialog.ok_button.isEnabled()
    picture = tmp_path / "scan.png"
    image = QImage(200, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    image.setPixelColor(100, 40, QColor("black"))
    image.save(str(picture))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(picture), "")))
    dialog.mode.set_current(2)
    assert not dialog.ok_button.isEnabled()
    [choose] = [b for b in dialog.findChildren(QPushButton) if b.text().startswith("Choose a picture")]
    choose.click()
    dialog.label.setText("Initials")
    assert dialog.ok_button.isEnabled() and dialog.signature()["name"] == "Initials"
    assert clipped_text(dialog) == []


def test_camera_dialog_picks_up_the_new_photo(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(QStandardPaths, "writableLocation", staticmethod(lambda location: str(tmp_path)))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    started = []
    monkeypatch.setattr(insert_dialogs.CameraDialog, "open_camera", lambda self: started.append(True))
    roll = tmp_path / "Camera Roll"
    roll.mkdir()
    dialog = insert_dialogs.CameraDialog()
    QApplication.processEvents()
    assert started == [True]
    dialog._look()
    assert dialog.result() != QDialog.DialogCode.Accepted
    (roll / "WIN_20261016_10_00_00_Pro.jpg").write_bytes(b"\xff\xd8 photo")
    dialog._folders.insert(0, tmp_path / "gone")  # a folder that vanished: skipped, no traceback
    dialog._look()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.photo.endswith("_Pro.jpg")
    assert clipped_text(dialog) == []
    dialog.deleteLater()


def test_segments(qt_app):
    segments = insert_dialogs.Segments(["One", "Two", "Three"])
    seen = []
    segments.changed.connect(seen.append)
    segments.set_current(2)
    segments.buttons[0].click()
    assert seen == [2, 0] and segments.current() == 0
    assert all(isinstance(b, QAbstractButton) and b.isCheckable() for b in segments.buttons)
    segments.resize(300, 32)
    assert not segments.grab().isNull()


def test_dialogs_follow_the_house_look(qt_app):
    """Heading, muted intro, the main verb before Cancel: the same in every insert dialog."""
    dialogs = [insert_dialogs.LinkDialog(3), insert_dialogs.ScaleDialog((1, "cm", 1, "cm")),
               insert_dialogs.EquationDialog(), insert_dialogs.SignatureDialog("A"),
               RecordDialog(recorder=Recorder(backend=FakeMic()))]
    for dialog in dialogs:
        assert dialog.findChild(QLabel, "DialogTitle") is not None
        assert dialog.findChild(QLabel, "DialogBody").wordWrap()
        assert dialog.minimumWidth() >= insert_dialogs.DIALOG_WIDTH
        margins = dialog.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (24, 20, 24, 20)
        dialog.show()
        QApplication.processEvents()
        [primary] = [b for b in dialog.findChildren(QPushButton) if b.objectName() == "Primary"]
        [cancel] = [b for b in dialog.findChildren(QPushButton) if b.text() == "Cancel"]
        assert primary.y() == cancel.y() and primary.geometry().right() < cancel.x()
        assert cancel.geometry().right() == dialog.width() - 24 - 1  # on the right edge of the margin
        dialog.reject()
        dialog.deleteLater()


@pytest.mark.skipif(os.environ.get("ZENITH_AUDIO_DEVICE_TESTS") != "1", reason="records from the real microphone")
def test_real_microphone_records():
    if audio.input_device_count() == 0:
        pytest.skip("no microphone")
    recorder = Recorder()
    recorder.start()
    time.sleep(0.3)
    recorder.stop()
    assert 0.2 < recorder.seconds < 0.6
    assert wav_seconds(recorder.wav()) == pytest.approx(recorder.seconds, abs=0.001)


@pytest.mark.skipif(os.environ.get("ZENITH_AUDIO_DEVICE_TESTS") != "1", reason="plays a sound on the speakers")
def test_real_player_plays_a_short_tone():
    player = Player(wav_bytes(TONE * 6), "tone.wav")
    try:
        assert player.length == pytest.approx(0.3, abs=0.05)
        player.play()
        time.sleep(0.1)
        assert player.playing or player.position > 0
    finally:
        player.close()
    assert not player.file.exists()
