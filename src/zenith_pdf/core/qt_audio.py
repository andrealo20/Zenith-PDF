"""Audio notes on macOS and Linux, through Qt Multimedia (core/audio.py has the Windows side).

The recorder backend has the shape core.audio.Recorder drives: open(rate,
channels, bits), read() -> chunks, close() -> the last chunks, all from the
recorder's worker thread. The microphone is read by polling, which needs no
event loop in that thread. The player takes the same calls as audio.Player.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .audio import AudioError, temp_folder, wav_seconds


def input_device_count() -> int:
    from PySide6.QtMultimedia import QMediaDevices

    return len(QMediaDevices.audioInputs())


class QtInput:
    """The default microphone through QAudioSource, read in pull mode."""

    def __init__(self):
        self._source = None
        self._device = None

    def open(self, rate: int, channels: int, bits: int) -> None:
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise AudioError("No microphone was found.")
        wanted = QAudioFormat()
        wanted.setSampleRate(rate)
        wanted.setChannelCount(channels)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16 if bits == 16 else QAudioFormat.SampleFormat.UInt8)
        if not device.isFormatSupported(wanted):
            raise AudioError("The microphone can't record in the format audio notes use.")
        self._source = QAudioSource(device, wanted)
        self._device = self._source.start()
        if self._device is None or self._source.error() != QAudio.Error.NoError:
            self._source = self._device = None
            raise AudioError("The microphone could not be opened. Another app may be using it, or the system may "
                             "not allow this app to use it.")

    def read(self) -> list[bytes]:
        if self._device is None:
            return []
        data = bytes(self._device.readAll().data())
        return [data] if data else []

    def close(self) -> list[bytes]:
        last = self.read()
        if self._source is not None:
            self._source.stop()
        self._source = self._device = None
        return last


class QtPlayer:
    """Plays a sound from a temporary copy through QMediaPlayer. Positions are seconds."""

    def __init__(self, data: bytes, name: str = "audio.wav"):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        suffix = "." + (re.sub(r"[^a-z0-9]", "", Path(name).suffix.lower())[:5] or "wav")
        try:
            self.file = temp_folder() / f"a{os.urandom(4).hex()}{suffix}"
            self.file.write_bytes(data)
        except OSError as error:
            raise AudioError(f"The sound could not be copied for playing: {error.strerror or error}") from None
        self._seconds = wav_seconds(data)
        self._output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._output)
        self._player.setSource(QUrl.fromLocalFile(str(self.file)))
        if self._player.error() != QMediaPlayer.Error.NoError:
            message = self._player.errorString() or "This sound can't be played here."
            self.close()
            raise AudioError(message)

    @property
    def length(self) -> float:
        duration = self._player.duration() / 1000 if self._player is not None else 0.0
        return duration or self._seconds

    def play(self) -> None:
        if self.length and self.position >= self.length - 0.05:
            self._player.setPosition(0)
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._player.stop()
        self._player.setPosition(0)

    def seek(self, seconds: float) -> None:
        self._player.setPosition(round(max(0.0, min(seconds, self.length)) * 1000))

    @property
    def playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self._player is not None and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def position(self) -> float:
        return self._player.position() / 1000 if self._player is not None else 0.0

    def close(self) -> None:
        """Release the player and delete the temporary copy. Safe to call twice."""
        player, self._player = getattr(self, "_player", None), None
        if player is not None:
            player.stop()
            player.setSource(type(player.source())())  # let go of the file before it is deleted
            player.deleteLater()
        try:
            self.file.unlink(missing_ok=True)
        except OSError:
            pass
