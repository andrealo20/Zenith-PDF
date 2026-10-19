"""Application entry point."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QDialog, QMenu, QProxyStyle, QStyleFactory, QWidget

# Only what the splash needs is imported here: the windows, the icons and the renderer load behind it.
from . import __version__
from .core import default_app
from .core.i18n import set_language
from .core.platforms import MAC, native_keys
from .settings import Settings
from .splash import show_splash
from .theme import themes
from .ui.instance import Instance, forward, own_command, register_scheme

if TYPE_CHECKING:
    from .ui.main_window import MainWindow

DWMWA_WINDOW_CORNER_PREFERENCE = 33  # Windows 11 and later
DWMWCP_ROUND = 2  # the 8 px corners Windows 11 gives its own menus; the style sheet rounds menus by as much
SPIN_INSET = 12  # px before a spin box's text, as in a line edit
REVEAL_WAIT_S = 3.0  # the longest the window stays out of sight for its first pages to be drawn


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
        elif kind == QEvent.Type.FileOpen:
            from .ui.main_window import MainWindow

            target = event.file() or event.url().toString()  # a PDF, or a zenith-pdf link
            if target and MainWindow.open_windows:
                MainWindow.open_windows[-1].open_targets([target])
                return True
        return False


def taskbar_id() -> str | None:
    """The id Windows groups the taskbar button by, when Zenith PDF has to give one.

    Run from source, the program is Python: an id of its own keeps its button apart from other Python programs.
    The installed Zenith-PDF.exe gives none, so Windows goes by the program itself, as it does for the Start menu
    and desktop shortcuts and for a button pinned to the taskbar: with an id the shortcuts don't carry, a click on
    the pinned button opened the window under a second button beside it.
    """
    return None if getattr(sys, "frozen", False) else "ZenithPDF.ZenithPDF"


def named_app(argv: list[str]) -> QApplication:
    """The Qt application, with the names its settings are stored under: before any Settings() is made. The
    taskbar id too, before any window: a window stays under the button of the id it was made with."""
    if sys.platform == "win32" and taskbar_id():
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(taskbar_id())
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Zenith PDF")
    app.setOrganizationName("Zenith PDF")
    app.setApplicationVersion(__version__)
    return app


def create_app(argv: list[str], settings: Settings | None = None) -> tuple[QApplication, MainWindow]:
    from .core.render import RenderPool
    from .icons import icon, style as icon_style
    from .ui.help import badges
    from .ui.main_window import MainWindow
    from .ui.widgets import set_glass, set_glass_adaptive

    app = named_app(argv)
    app.setStyle(ZenithStyle())

    settings = settings or Settings()
    settings.upgrade()
    set_language(settings.get("language"))  # before any window: every text is shown in it
    themes.choose(settings.theme, settings.get("accent"))
    _default_glass(settings)
    set_glass(settings.get("glass"))
    set_glass_adaptive(settings.get("glass_adaptive"))
    icon_style.colourful = bool(settings.get("icon_colours"))
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


def self_test() -> int:
    """For the build (tools/build.py): every module imports and Qt finds what the app needs. 0 when all is well.

    The report goes to the file ZENITH_SELF_TEST_LOG names: a windowed program has no console to print to.
    """
    import importlib
    import pkgutil

    from PySide6.QtGui import QImageReader

    import zenith_pdf

    app = QApplication.instance() or QApplication(["zenith-pdf"])
    problems, lines = [], []
    modules = [info.name for info in pkgutil.walk_packages(zenith_pdf.__path__, "zenith_pdf.")]
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as error:  # anything at all: the report says what
            problems.append(f"{name}: {type(error).__name__}: {error}")
    formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
    for needed in ("png", "jpg", "svg"):
        if needed not in formats:
            problems.append(f"Qt can't read {needed} pictures")
    try:
        from PySide6.QtTextToSpeech import QTextToSpeech

        lines.append(f"voices: {', '.join(QTextToSpeech.availableEngines()) or 'none'}")
    except ImportError as error:
        problems.append(f"text to speech: {error}")
    from .core.i18n import LANGUAGES, set_language, tr

    for code in LANGUAGES:  # the translations travel with the app
        if code != "en" and (set_language(code) != code or tr("Settings") == "Settings"):
            problems.append(f"the {LANGUAGES[code]} texts are missing")
    set_language("en")
    problems += _self_test_window()
    lines.insert(0, f"Zenith PDF {__version__}: {len(modules)} modules, platform {app.platformName()}")
    lines += problems or ["all well"]
    report = os.environ.get("ZENITH_SELF_TEST_LOG")
    if report:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        print("\n".join(lines))
    return 1 if problems else 0


def _self_test_window() -> list[str]:
    """A window with settings of its own (never the user's), and a page drawn by PDFium: what went wrong."""
    import tempfile

    import pypdfium2 as pdfium
    from PySide6.QtCore import QSettings

    from .core.document import Document

    folder = Path(tempfile.mkdtemp(prefix="zenith-self-test-"))
    os.environ["ZENITH_CACHE_DIR"] = str(folder / "cache")  # thumbnails too stay out of the user's cache
    try:
        sample = folder / "sample.pdf"
        pdf = pdfium.PdfDocument.new()
        pdf.new_page(300, 400).close()
        pdf.save(str(sample))
        pdf.close()
        document = Document(sample)
        image = document.render(0, 1.0)
        document.close()
        if image.isNull():
            return ["PDFium drew nothing"]
        settings = Settings(QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat))
        _app, window = create_app(["zenith-pdf"], settings)
        window.show()
        window.open_file(sample)
        QApplication.processEvents()
        window.close()
        window.renderer.shutdown()
    except Exception as error:  # anything at all: the report says what
        return [f"window: {type(error).__name__}: {error}"]
    return []


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()  # lets render processes start from a frozen .exe too
    argv = sys.argv if argv is None else argv
    if "--self-test" in argv:
        return self_test()
    targets = [arg for arg in argv[1:] if not arg.startswith("-")]
    named_app(argv)
    if forward(targets):  # Zenith PDF is already running: it opens them, in its own window
        return 0
    settings = Settings()
    splash = show_splash(settings.theme)
    app, window = create_app(argv, settings)
    from .ui.main_window import MainWindow

    instance = Instance(app)
    instance.received.connect(lambda more: _bring_forward(more))
    register_scheme(own_command())
    default_app.register_for_user(own_command())  # on Windows, among the apps suggested for PDF files
    # Shown out of sight, behind the splash, until its first screen is drawn: no window growing to its size,
    # no Home before the documents that were open.
    window.setWindowOpacity(0.0)
    window.show()
    # Once the window is up: the tour after an update, or the first time, whether to open PDF files.
    _Reveal(window, splash, lambda: QTimer.singleShot(700, lambda: window.after_update() or window.offer_default_app()))
    if not targets and window.settings.get("on_start") == "restore":
        window.restore_session()
    window.open_targets(targets)
    try:
        indexer = MainWindow.make_indexer(window.settings)
    except (OSError, sqlite3.Error):  # an index that can't be opened costs the search inside documents, not the app
        indexer = None
    if indexer is not None:
        # The files on the home page, a while after starting: searching inside them works from then on.
        QTimer.singleShot(15000, lambda: indexer.add(window.settings.recent_files()))
    updater = MainWindow.make_updater(window.settings)
    if updater.due():
        QTimer.singleShot(8000, updater.check)  # a moment after starting, never in the way of it
    return app.exec()


class _Reveal(QObject):
    """Brings the starting window into sight, and closes the splash, once the pages on screen are drawn: after
    REVEAL_WAIT_S at the latest, and at once when a dialog needs the user (a password, a file that won't open)."""

    def __init__(self, window: MainWindow, splash: QWidget, then):
        super().__init__(window)
        self._window, self._splash, self._then = window, splash, then
        self._deadline = time.monotonic() + REVEAL_WAIT_S
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(30)
        QApplication.instance().installEventFilter(self)

    def _check(self) -> None:
        workspace = self._window.current_workspace()
        if workspace is None or workspace.view.missing_tiles() == 0 or time.monotonic() > self._deadline:
            self._show()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            self._show()
        return False

    def _show(self) -> None:
        if not self._timer.isActive():
            return  # shown already
        self._timer.stop()
        QApplication.instance().removeEventFilter(self)
        self._window.setWindowOpacity(1.0)
        self._splash.close()
        self._then()


def _bring_forward(targets: list[str]) -> None:
    """A later start handed these over: open them in the window used last, and bring it to the front."""
    from .ui.main_window import MainWindow

    if not MainWindow.open_windows:
        return
    window = MainWindow.open_windows[-1]
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    window.open_targets(targets)
