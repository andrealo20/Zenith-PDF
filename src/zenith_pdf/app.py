"""Application entry point."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QMenu, QProxyStyle, QStyleFactory, QWidget

from . import __version__
from .core.platforms import MAC, native_keys
from .core.render import RenderPool
from .icons import icon
from .settings import Settings
from .theme import themes
from .ui.help import badges
from .ui.instance import Instance, forward, own_command, register_scheme
from .ui.main_window import MainWindow
from .ui.widgets import set_glass

DWMWA_WINDOW_CORNER_PREFERENCE = 33  # Windows 11 and later
DWMWCP_ROUND = 2  # the 8 px corners Windows 11 gives its own menus; the style sheet rounds menus by as much
SPIN_INSET = 12  # px before a spin box's text, as in a line edit


def system_transparency() -> bool:
    """Whether Windows' "Transparency effects" is on (always True elsewhere)."""
    if sys.platform != "win32":
        return True
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
    except OSError:
        return True
    return bool(value)


def _native_corners() -> bool:
    """Whether Windows itself can round popup windows: Windows 11 on its own platform plugin."""
    return (sys.platform == "win32" and QGuiApplication.platformName() == "windows"
            and sys.getwindowsversion().build >= 22000)


class _NativeCorners(QObject):
    """Asks Windows 11 to round a popup's corners as it shows, which keeps the system shadow."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show:
            import ctypes
            from ctypes import wintypes

            preference = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(int(watched.winId())), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(preference),
                ctypes.sizeof(preference))
        return False


class ZenithStyle(QProxyStyle):
    """Fusion, with rounded menus and drop-down lists, which Qt draws as square boxes.

    On Windows 11 the window manager rounds them. Elsewhere a menu gets a transparent
    background, so the style sheet's rounded border becomes its edge. Spin boxes get
    their text inset here: the style sheet's padding never reaches the editor inside.
    """

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))
        self._corners = _NativeCorners(self) if _native_corners() else None

    def polish(self, target):  # also called with the palette and the application
        result = super().polish(target)
        if isinstance(target, QMenu) or (isinstance(target, QWidget) and target.inherits("QComboBoxPrivateContainer")):
            if self._corners is not None:
                target.installEventFilter(self._corners)
            elif isinstance(target, QMenu):
                target.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)  # it would be square
                target.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        elif isinstance(target, QAbstractSpinBox) and target.lineEdit() is not None:
            target.lineEdit().setTextMargins(SPIN_INSET, 0, 0, 0)
        return result


class MacEvents(QObject):
    """What a Mac app gets from the system, through one filter on the app (every event passes it):

    - a file opened from the Finder arrives as an event, not on the command line;
    - tooltips and menus write keys as the Mac does (⌘K, ⇧⌘S), wherever their text is set.
    """

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTipChange and isinstance(watched, QWidget):
            tip = watched.toolTip()
            if tip and (native := native_keys(tip)) != tip:
                watched.setToolTip(native)  # sends the event again, now with nothing to change
        elif kind in (QEvent.Type.ActionAdded, QEvent.Type.ActionChanged) and isinstance(watched, QMenu):
            action = event.action()
            text = action.text()
            if text and (native := native_keys(text)) != text:
                action.setText(native)
        elif kind == QEvent.Type.FileOpen and MainWindow.open_windows:
            target = event.file() or event.url().toString()  # a PDF, or a zenith-pdf link
            if target:
                MainWindow.open_windows[-1].open_targets([target])
                return True
        return False


def create_app(argv: list[str], settings: Settings | None = None) -> tuple[QApplication, MainWindow]:
    if sys.platform == "win32":
        # Group the taskbar button under our own id rather than python.exe's.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZenithPDF.ZenithPDF")

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Zenith PDF")
    app.setOrganizationName("Zenith PDF")
    app.setApplicationVersion(__version__)
    app.setStyle(ZenithStyle())

    settings = settings or Settings()
    themes.choose(settings.theme, settings.get("accent"))
    _default_glass(settings)
    set_glass(settings.get("glass"))
    badges.set_mode(settings.get("help_badges"))
    badges.delay = settings.get("help_delay")
    # Windows takes its sizes from the .ico; macOS and Linux scale the picture.
    app_icon = Path(__file__).parent / "assets" / ("zenith.ico" if sys.platform == "win32" else "zenith.png")
    app.setWindowIcon(QIcon(str(app_icon)) if app_icon.exists() else icon("file-pdf", themes.current.accent))
    if MAC and app.findChild(MacEvents) is None:
        app.installEventFilter(MacEvents(app))

    # ZENITH_RENDER_PROCESSES=0 renders in-process only (tests, debugging); else the setting.
    processes = os.environ.get("ZENITH_RENDER_PROCESSES")
    renderer = RenderPool(int(processes) if processes else settings.render_processes, app)
    app.aboutToQuit.connect(renderer.shutdown)
    window = MainWindow(renderer, settings)
    return app, window


def _default_glass(settings: Settings) -> None:
    """With Windows' transparency effects off, the bars start solid, until the user picks a glass."""
    stored = getattr(settings, "stored", None)  # Settings.stored(key): whether the user ever set it
    if stored is not None and not stored("glass") and not system_transparency():
        settings.set("glass", "solid")


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # lets render processes start from a frozen .exe too
    argv = sys.argv if argv is None else argv
    targets = [arg for arg in argv[1:] if not arg.startswith("-")]
    QApplication.instance() or QApplication(argv)
    if forward(targets):  # Zenith PDF is already running: it opens them, in its own window
        return 0
    app, window = create_app(argv)
    instance = Instance(app)
    instance.received.connect(lambda more: _bring_forward(more))
    register_scheme(own_command())
    window.show()
    if not targets and window.settings.get("on_start") == "restore":
        window.restore_session()
    window.open_targets(targets)
    return app.exec()


def _bring_forward(targets: list[str]) -> None:
    """A later start handed these over: open them in the window used last, and bring it to the front."""
    if not MainWindow.open_windows:
        return
    window = MainWindow.open_windows[-1]
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    window.open_targets(targets)
