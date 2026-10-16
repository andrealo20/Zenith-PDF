"""Audio notes: record from the microphone and play back, through Windows' winmm with ctypes.

Recording uses waveIn, polled from a worker thread: MCI gives no input level on
this kind of device, needs every format field in one command and cannot save
to a path of 128 characters or more. Playback uses MCI, which plays WAV and,
through DirectShow, MP3, WMA and the other formats Windows knows. No Qt here:
ui/audio_dialog.py drives these classes.
"""

from __future__ import annotations

import array
import ctypes
import io
import os
import re
import sys
import tempfile
import threading
import time
import wave
from ctypes import wintypes
from functools import cache
from pathlib import Path

RATE = 22050  # speech; about 2.6 MB a minute inside the PDF
CHANNELS = 1
BITS = 16
MAX_SECONDS = 600
SILENCE = 10 ** (-60 / 20)  # a peak under -60 dBFS: nothing is coming in
MCI_PATH_LIMIT = 128  # MCI rejects longer file names (error 304)
POLL = 0.01  # seconds between looks at the waveIn buffers
OPEN_SECONDS = 2.0  # the longest start() waits for the device to open
JOIN_SECONDS = 3.0  # the longest stop() waits for a worker held up in the sound driver
TEMP_FOLDER = "zenith-pdf"
STALE_SECONDS = 24 * 3600  # a player's copy older than this was left by an app that was killed
PLAYER_FILE = re.compile(r"a[0-9a-f]{8}\.[a-z0-9]{1,5}")  # the names Player gives its copies


class AudioError(RuntimeError):
    """A sound device failed or refused; the message is Windows' own text when there is one."""


# winmm ------------------------------------------------------------------------------

WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0
WHDR_DONE = 1


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                    ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]

HWAVEIN = wintypes.HANDLE


@cache
def _winmm():
    """winmm.dll with the signatures used here, or None where there is no winmm (not Windows)."""
    if sys.platform != "win32":
        return None
    try:
        winmm = ctypes.WinDLL("winmm")
    except OSError:
        return None
    winmm.waveInGetNumDevs.argtypes = []
    winmm.waveInGetNumDevs.restype = wintypes.UINT
    winmm.waveInOpen.argtypes = [ctypes.POINTER(HWAVEIN), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                                 ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    winmm.waveInOpen.restype = wintypes.UINT
    for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(winmm, name).argtypes = [HWAVEIN, ctypes.POINTER(WAVEHDR), wintypes.UINT]
        getattr(winmm, name).restype = wintypes.UINT
    for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
        getattr(winmm, name).argtypes = [HWAVEIN]
        getattr(winmm, name).restype = wintypes.UINT
    winmm.waveInGetErrorTextW.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    winmm.waveInGetErrorTextW.restype = wintypes.UINT
    winmm.mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
    winmm.mciSendStringW.restype = wintypes.DWORD
    winmm.mciGetErrorStringW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT]
    winmm.mciGetErrorStringW.restype = wintypes.BOOL
    return winmm


def _check(code: int) -> None:
    """Raise AudioError with waveIn's text for a non-zero result."""
    if code:
        text = ctypes.create_unicode_buffer(256)
        _winmm().waveInGetErrorTextW(code, text, 256)
        raise AudioError(text.value or f"The sound device reported error {code}.")


def input_device_count() -> int:
    """How many microphones (waveIn devices) Windows offers; 0 off Windows."""
    winmm = _winmm()
    return int(winmm.waveInGetNumDevs()) if winmm is not None else 0


# WAV --------------------------------------------------------------------------------


def wav_bytes(pcm: bytes, rate: int = RATE, channels: int = CHANNELS, bits: int = BITS) -> bytes:
    """PCM samples wrapped in a WAV file."""
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(bits // 8)
        file.setframerate(rate)
        file.writeframes(pcm)
    return out.getvalue()


def wav_seconds(data: bytes) -> float:
    """How long a WAV file plays; 0.0 if it can't be read as one."""
    try:
        with wave.open(io.BytesIO(data), "rb") as file:
            return file.getnframes() / file.getframerate() if file.getframerate() else 0.0
    except (wave.Error, EOFError, ValueError):
        return 0.0


def _peak(chunk: bytes, bits: int) -> float:
    """The loudest sample of a chunk, 0 to 1."""
    if not chunk:
        return 0.0
    if bits == 8:  # unsigned, silence at 128
        return max(max(chunk) - 128, 128 - min(chunk)) / 128
    samples = array.array("h")
    samples.frombytes(chunk[:len(chunk) - len(chunk) % 2])
    if not samples:
        return 0.0
    if sys.byteorder == "big":
        samples.byteswap()
    return min(1.0, max(max(samples), -min(samples)) / 32768)


# Recording --------------------------------------------------------------------------


class _WaveIn:
    """The microphone through waveIn, with polled buffers (CALLBACK_NULL).

    The device fills the queued buffers in order and each one is queued again
    as soon as it is read, so reading always starts where the last read stopped.
    Every call must come from the same thread: the recorder's worker.
    """

    BUFFERS = 16
    BUFFER_SECONDS = 0.05  # 16 of 50 ms: 800 ms of slack if the worker is held up

    def __init__(self):
        self._handle = None
        self._headers: list[WAVEHDR] = []
        self._buffers: list = []
        self._next = 0

    def open(self, rate: int, channels: int, bits: int) -> None:
        winmm = _winmm()
        if winmm is None:
            raise AudioError("Recording needs Windows.")
        block = channels * bits // 8
        fmt = WAVEFORMATEX(1, channels, rate, rate * block, block, bits, 0)
        handle = HWAVEIN()
        _check(winmm.waveInOpen(ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(fmt), 0, 0, CALLBACK_NULL))
        self._handle = handle
        size = max(block, int(rate * self.BUFFER_SECONDS) * block)
        self._buffers = [ctypes.create_string_buffer(size) for _ in range(self.BUFFERS)]
        self._headers = [WAVEHDR(ctypes.cast(b, ctypes.c_void_p), size, 0, 0, 0, 0, None, 0) for b in self._buffers]
        self._next = 0
        try:
            for header in self._headers:
                self._queue(header)
            _check(winmm.waveInStart(handle))
        except AudioError:
            self.close()
            raise

    def _queue(self, header: WAVEHDR) -> None:
        winmm = _winmm()
        header.dwFlags = 0
        header.dwBytesRecorded = 0
        _check(winmm.waveInPrepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR)))
        _check(winmm.waveInAddBuffer(self._handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR)))

    def _finished(self) -> list[bytes]:
        """The filled buffers, oldest first, unprepared."""
        winmm = _winmm()
        chunks = []
        for _ in range(len(self._headers)):
            header = self._headers[self._next]
            if not header.dwFlags & WHDR_DONE:
                break
            chunks.append(ctypes.string_at(self._buffers[self._next], header.dwBytesRecorded))
            winmm.waveInUnprepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
            header.dwFlags = 0
            self._next = (self._next + 1) % len(self._headers)
        return chunks

    def read(self) -> list[bytes]:
        """What came in since the last read; the buffers go back in the queue."""
        if self._handle is None:
            return []
        done = self._finished()
        for offset in range(len(done)):
            self._queue(self._headers[(self._next - len(done) + offset) % len(self._headers)])
        return done

    def close(self) -> list[bytes]:
        """Stop, and return the last partial buffers."""
        if self._handle is None:
            return []
        winmm = _winmm()
        winmm.waveInReset(self._handle)  # hands back every queued buffer, marked done
        chunks = [chunk for chunk in self._finished() if chunk]
        for header in self._headers:
            winmm.waveInUnprepareHeader(self._handle, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
        winmm.waveInClose(self._handle)
        self._handle = None
        self._headers, self._buffers = [], []
        return chunks


class Recorder:
    """Records PCM in a worker thread; start() after stop() carries on the same recording.

    `backend` stands in for the microphone (tests pass a fake): open(rate,
    channels, bits), read() -> list of chunks, close() -> the last chunks.
    """

    def __init__(self, rate: int = RATE, channels: int = CHANNELS, bits: int = BITS, backend=None):
        self.rate, self.channels, self.bits = rate, channels, bits
        self._backend = backend if backend is not None else _WaveIn()
        self._block = channels * bits // 8
        self._max_bytes = MAX_SECONDS * rate * self._block
        self._chunks: list[bytes] = []
        self._bytes = 0
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self.recording = False
        self.level = 0.0  # peak of the last chunk, 0 to 1
        self.heard = False  # anything above SILENCE so far
        self.error: str | None = None  # set if the device fails mid-recording

    @property
    def seconds(self) -> float:
        return self._bytes / (self.rate * self._block)

    @property
    def full(self) -> bool:
        """MAX_SECONDS are recorded: start() does nothing more."""
        return self._max_bytes - self._bytes < self._block

    def start(self) -> None:
        """Open the microphone and record; AudioError with Windows' text if it can't open."""
        if self.recording or self.full:
            return
        self.stop()  # a worker still closing from a timed-out start
        if self._thread is not None:  # and still stuck in the driver: two workers can't share a device
            raise AudioError("The microphone did not answer. Another app may be using it.")
        self.error = None
        self._halt.clear()
        opened = threading.Event()
        failure: list[str] = []
        self._thread = threading.Thread(target=self._run, args=(opened, failure), name="zenith-recorder",
                                        daemon=True)
        self._thread.start()
        if not opened.wait(OPEN_SECONDS):
            self._halt.set()
            raise AudioError("The microphone did not answer. Another app may be using it.")
        if failure:
            self._thread.join()
            self._thread = None
            raise AudioError(failure[0])

    def _run(self, opened: threading.Event, failure: list[str]) -> None:
        try:
            self._backend.open(self.rate, self.channels, self.bits)
        except Exception as error:  # any failure belongs to start(), not to a traceback on stderr
            failure.append(str(error) or "The microphone could not be opened.")
            opened.set()
            return
        live = not self._halt.is_set()  # False when start() gave up waiting: close at once, keep nothing
        self.recording = live
        opened.set()
        try:
            while live and not self._halt.is_set() and not self.full:
                self._take(self._backend.read())
                time.sleep(POLL)
        except Exception as error:
            self.error = str(error) or "The microphone stopped working."
        finally:
            try:
                last = self._backend.close()
                if live:
                    self._take(last)
            except Exception as error:
                self.error = self.error or str(error)
            self.recording = False
            self.level = 0.0

    def _take(self, chunks: list[bytes]) -> None:
        for chunk in chunks:
            room = self._max_bytes - self._bytes
            chunk = chunk[:max(0, min(len(chunk), room)) // self._block * self._block]
            if not chunk:
                continue
            with self._lock:
                self._chunks.append(chunk)
                self._bytes += len(chunk)
            self.level = _peak(chunk, self.bits)
            if self.level > SILENCE:
                self.heard = True

    def stop(self) -> None:
        """Stop recording and keep what was recorded. Safe to call at any time.

        Waits at most JOIN_SECONDS, so a hung driver can't freeze the window;
        such a worker is kept, and start() refuses until it has gone.
        """
        self._halt.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_SECONDS)
        if thread is None or not thread.is_alive():
            self._thread = None
        self.recording = False
        self.level = 0.0

    def clear(self) -> None:
        """Stop and forget the recording, for "Record again"."""
        self.stop()
        with self._lock:
            self._chunks = []
            self._bytes = 0
        self.heard = False
        self.error = None

    def pcm(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    def wav(self) -> bytes:
        return wav_bytes(self.pcm(), self.rate, self.channels, self.bits)


# Playback ---------------------------------------------------------------------------


def _mci(command: str) -> str:
    """Send an MCI command string; its answer, or AudioError with MCI's text."""
    winmm = _winmm()
    if winmm is None:
        raise AudioError("Playing sound needs Windows.")
    answer = ctypes.create_unicode_buffer(512)
    code = winmm.mciSendStringW(command, answer, 512, None)
    if code:
        text = ctypes.create_unicode_buffer(512)
        winmm.mciGetErrorStringW(code, text, 512)
        raise AudioError(text.value or f"The player reported error {code}.")
    return answer.value


@cache
def _short_path_function():
    """GetShortPathNameW, or None off Windows."""
    if sys.platform != "win32":
        return None
    function = ctypes.WinDLL("kernel32").GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    return function


def _short_path(path: Path) -> str:
    """The 8.3 form of an existing path (MCI chokes on long ones), or the path itself."""
    function = _short_path_function()
    if function is None:
        return str(path)
    buffer = ctypes.create_unicode_buffer(1024)
    length = function(str(path), buffer, 1024)
    return buffer.value if 0 < length < 1024 else str(path)


def temp_folder() -> Path:
    folder = Path(tempfile.gettempdir()) / TEMP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@cache
def _remove_stale_copies() -> None:
    """Once a run: delete the players' copies that a killed app left in the temporary folder."""
    limit = time.time() - STALE_SECONDS
    try:
        entries = list(os.scandir(temp_folder()))
    except OSError:
        return
    for entry in entries:
        try:
            if PLAYER_FILE.fullmatch(entry.name) and entry.stat().st_mtime < limit:
                os.unlink(entry.path)
        except OSError:  # in use by another copy of the app
            pass


class Player:
    """Plays a sound from a temporary copy (MCI needs a file, and a short path to it).

    `send` stands in for mciSendString (tests pass a fake). Positions are seconds.
    """

    def __init__(self, data: bytes, name: str = "audio.wav", send=None):
        self._send = send if send is not None else _mci
        self.alias = f"zplay{id(self)}"
        self._open = False
        suffix = "." + (re.sub(r"[^a-z0-9]", "", Path(name).suffix.lower())[:5] or "wav")
        _remove_stale_copies()
        try:
            self.file = temp_folder() / f"a{os.urandom(4).hex()}{suffix}"
            self.file.write_bytes(data)
        except OSError as error:  # a full disk, or a temporary folder that can't be written
            if hasattr(self, "file"):
                self._remove_file()
            raise AudioError(f"The sound could not be copied for playing: {error.strerror or error}") from None
        path = _short_path(self.file)
        if len(path) >= MCI_PATH_LIMIT:
            self._remove_file()
            raise AudioError("The temporary folder's path is too long for the Windows player.")
        reason = "This sound can't be played here."
        for kind in (("waveaudio", "mpegvideo") if suffix == ".wav" else ("mpegvideo",)):
            try:
                self._send(f'open "{path}" type {kind} alias {self.alias}')
            except AudioError as error:
                reason = str(error)
                continue
            self._open = True
            break
        if not self._open:
            self._remove_file()
            raise AudioError(reason)
        try:
            self._send(f"set {self.alias} time format milliseconds")
            self.length = _seconds(self._send(f"status {self.alias} length"))
        except AudioError:
            self.close()
            raise

    def play(self) -> None:
        """Play from where it stopped, or from the start once it has reached the end."""
        if self.length and self.position >= self.length - 0.05:
            self._send(f"seek {self.alias} to start")
        self._send(f"play {self.alias}")

    def pause(self) -> None:
        if self.playing:
            self._send(f"pause {self.alias}")

    def stop(self) -> None:
        self._send(f"stop {self.alias}")
        self._send(f"seek {self.alias} to start")

    def seek(self, seconds: float) -> None:
        """Jump to a time; keeps playing if it was."""
        ms = round(max(0.0, min(seconds, self.length)) * 1000)
        if self.playing:
            self._send(f"play {self.alias} from {ms}")
        else:
            self._send(f"seek {self.alias} to {ms}")

    @property
    def playing(self) -> bool:
        return self._open and self._ask("mode") == "playing"

    @property
    def position(self) -> float:
        return _seconds(self._ask("position")) if self._open else 0.0

    def _ask(self, item: str) -> str:
        try:
            return self._send(f"status {self.alias} {item}")
        except AudioError:
            return ""

    def close(self) -> None:
        """Release the device and delete the temporary copy. Safe to call twice."""
        if self._open:
            self._open = False
            try:
                self._send(f"close {self.alias}")
            except AudioError:
                pass
        self._remove_file()

    def _remove_file(self) -> None:
        try:
            self.file.unlink(missing_ok=True)
        except OSError:  # still held by the player; the temporary folder is cleaned by Windows
            pass


def _seconds(answer: str) -> float:
    try:
        return max(0.0, float(answer) / 1000)
    except ValueError:
        return 0.0
