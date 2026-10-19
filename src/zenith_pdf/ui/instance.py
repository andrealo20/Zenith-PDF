"""One Zenith PDF per user: a second start hands its files and links to the one already running.

A link of the form zenith-pdf://open?file=<path>&page=<n> opens a PDF at a page: the notes sent to
Obsidian carry them. The Windows installer, the macOS bundle and the Linux desktop entry register the
scheme; a copy run from source registers it for the user on Windows when nothing else has.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SCHEME = "zenith-pdf"
WAIT_MS = 600  # how long a second start waits for the first one to answer


def server_name() -> str:
    """The name the running app listens on: one per user, short enough for every system's sockets."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or str(Path.home())
    return "zenith-pdf-" + hashlib.sha1(user.encode("utf-8", "replace")).hexdigest()[:12]


def page_link(path: str | Path, page: int) -> str:
    """A link that opens `path` at `page` (counted from 1) in Zenith PDF."""
    return f"{SCHEME}://open?file={quote(str(Path(path)), safe='')}&page={int(page)}"


def parse_target(text: str) -> tuple[Path, int | None] | None:
    """A file path or a zenith-pdf link as (file, page counted from 1, or None); None if it is neither."""
    if text.lower().startswith(f"{SCHEME}:"):
        query = parse_qs(urlparse(text).query)
        file = (query.get("file") or [""])[0]
        # A link can come from any web page: a PDF on this computer only. A network path would hand
        # Windows' sign-in to whoever runs that server.
        if not file or file.startswith(("\\\\", "//")) or not file.lower().endswith(".pdf"):
            return None
        page = (query.get("page") or [""])[0]
        return Path(file), int(page) if page.isdigit() and int(page) > 0 else None
    if text.startswith("-"):
        return None
    return Path(text), None


def forward(targets: list[str], name: str | None = None) -> bool:
    """Hand `targets` to the Zenith PDF already running; False when none answers."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(WAIT_MS):
        return False
    socket.write(json.dumps({"targets": targets}).encode("utf-8") + b"\n")
    socket.flush()
    socket.waitForBytesWritten(WAIT_MS)
    # The running app hangs up once it has read the message: going away first could lose it.
    answered = socket.waitForDisconnected(WAIT_MS * 3) or socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
    socket.abort()
    return answered


class Instance(QObject):
    """The running app's end: emits what later starts hand over."""

    received = Signal(list)

    def __init__(self, parent: QObject | None = None, name: str | None = None):
        super().__init__(parent)
        self.name = name or server_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self.name):
            QLocalServer.removeServer(self.name)  # left behind by an app that crashed (macOS and Linux)
            self._server.listen(self.name)
        self._server.newConnection.connect(self._accept)

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def _accept(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            buffer = bytearray()

            def read(socket=socket, buffer=buffer) -> None:
                buffer.extend(bytes(socket.readAll().data()))
                if b"\n" not in buffer:
                    return
                try:
                    targets = json.loads(buffer.split(b"\n", 1)[0])["targets"]
                except (ValueError, KeyError, TypeError):
                    targets = []
                socket.disconnected.connect(socket.deleteLater)
                socket.disconnectFromServer()
                if isinstance(targets, list):
                    self.received.emit([str(target) for target in targets])

            socket.readyRead.connect(read)
            read()


def register_scheme(command: list[str]) -> None:
    """On Windows, make zenith-pdf links open with `command` for this user, when nothing handles them yet.

    The installer registers them for every user; this covers a copy run from source.
    """
    from ..core.platforms import package_family

    if sys.platform != "win32" or package_family():  # the Store's package declares the links itself
        return
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, SCHEME))
        return  # the installer, or an earlier start, already did
    except OSError:
        pass
    line = " ".join(f'"{part}"' for part in command) + ' "%1"'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Zenith PDF link")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, line)
    except OSError:
        pass


def own_command() -> list[str]:
    """How to start this copy of Zenith PDF again: the program itself, or Python with the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    python = Path(sys.executable)
    windowed = python.with_name("pythonw.exe")
    return [str(windowed if windowed.exists() else python), "-m", "zenith_pdf"]
