"""What differs between Windows, macOS and Linux: fonts, how keys are written, the names of system places.

Fonts are picked from what the system has, the first of each list that is
installed; Qt asks the system for the rest. Keys are written as the system
writes them: Qt already turns Ctrl into Command on a Mac, so the texts here
follow it.
"""

from __future__ import annotations

import re
import sys
from functools import cache

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", ".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", "Inter",
            "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans", "Liberation Sans")
MONO_FONTS = ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono",
              "Liberation Mono")
MATH_FONTS = ("Cambria Math", "STIX Two Math", "STIXGeneral", "Latin Modern Math", "DejaVu Serif", "Times New Roman")
SERIF_FONTS = ("Cambria", "Georgia", "Charter", "Times New Roman", "Noto Serif", "DejaVu Serif", "Liberation Serif")

SYSTEM_NAME = "Windows" if WINDOWS else "macOS" if MAC else "Linux"
# Keys macOS keeps for itself (⌘H hides the app, ⌘Tab switches apps, F11 shows the desktop), and what
# Zenith PDF takes there instead. Meta is the Control key on a Mac.
MAC_MOVED = {"Ctrl+H": "Ctrl+Shift+R", "Ctrl+Tab": "Meta+Tab", "Ctrl+Shift+Tab": "Meta+Shift+Tab",
             "F11": "Meta+Ctrl+F"}
_MAC_SYMBOLS = {"Meta": "⌃", "Alt": "⌥", "Shift": "⇧", "Ctrl": "⌘"}  # in the order a Mac writes them
_MOVED = re.compile(r"(?<![\w+])(" + "|".join(re.escape(k) for k in sorted(MAC_MOVED, key=len, reverse=True))
                    + r")(?![\w+])")
_CHORD = re.compile(r"(?<![\w+])((?:(?:Ctrl|Alt|Shift|Meta)\+)+)")


def _installed() -> set[str]:
    from PySide6.QtGui import QFontDatabase

    return set(QFontDatabase.families())


@cache
def _first(candidates: tuple[str, ...], fallback: str) -> str:
    families = _installed()
    found = next((name for name in candidates if name in families), None)
    if found is not None:
        return found
    from PySide6.QtGui import QFontDatabase

    kind = {"mono": QFontDatabase.SystemFont.FixedFont}.get(fallback, QFontDatabase.SystemFont.GeneralFont)
    return QFontDatabase.systemFont(kind).family()


def ui_font() -> str:
    """The family the app's text is set in. Needs the Qt application."""
    return _first(UI_FONTS, "general")


def mono_font() -> str:
    return _first(MONO_FONTS, "mono")


def math_font() -> str:
    return _first(MATH_FONTS, "general")


def serif_font() -> str:
    return _first(SERIF_FONTS, "general")


def key(sequence: str) -> str:
    """The key sequence to bind for `sequence` here: on a Mac, a key the system keeps is moved (MAC_MOVED)."""
    return MAC_MOVED.get(sequence, sequence) if MAC else sequence


def native_keys(text: str) -> str:
    """`text` with its key names as this system writes them: on a Mac "Ctrl+K" is "⌘K" and "Alt+Left" "⌥Left"."""
    if not MAC or not text:
        return text
    text = _MOVED.sub(lambda m: MAC_MOVED[m.group(1)], text)
    text = _CHORD.sub(lambda m: "".join(symbol for name, symbol in _MAC_SYMBOLS.items()
                                        if f"{name}+" in m.group(1)), text)
    return re.sub(r"\bCtrl\b", "⌘", text)


def settings_place(what: str) -> str:
    """Where in the system's own settings `what` is added: "languages", "microphone"."""
    if WINDOWS:
        return {"languages": "Windows Settings > Time & language > Language & region",
                "microphone": "Windows Settings > Privacy & security > Microphone",
                "camera": "Windows Settings > Privacy & security > Camera"}[what]
    if MAC:
        return {"languages": "System Settings > General > Language & Region",
                "microphone": "System Settings > Privacy & Security > Microphone",
                "camera": "System Settings > Privacy & Security > Camera"}[what]
    return {"languages": "your package manager",
            "microphone": "your system's sound settings",
            "camera": "your system's settings"}[what]


@cache
def package_family() -> str:
    """The package this copy was installed from on Windows, when it came from the Microsoft Store (an MSIX
    package, which declares PDF files and links itself and gets its updates from the Store); "" otherwise."""
    if not WINDOWS:
        return ""
    import ctypes

    length = ctypes.c_uint32(0)
    get = ctypes.windll.kernel32.GetCurrentPackageFamilyName
    if get(ctypes.byref(length), None) != 122:  # ERROR_INSUFFICIENT_BUFFER; APPMODEL_ERROR_NO_PACKAGE when none
        return ""
    name = ctypes.create_unicode_buffer(length.value)
    return name.value if get(ctypes.byref(length), name) == 0 else ""
