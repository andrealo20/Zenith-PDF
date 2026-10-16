"""Spelling, through the spell checker built into Windows (ISpellCheckerFactory, Windows 8 and later).

Windows checks every language it has a dictionary for: those of the languages
added in Settings > Time & language > Language & region. Nothing is installed
or sent anywhere. The COM interfaces are called through their vtables with
ctypes, so no extra package is needed. Call from the GUI thread.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import HRESULT, POINTER, byref, c_int, c_ulong, c_void_p, c_wchar_p
from dataclasses import dataclass

NAMES = {"en": "English", "it": "Italian", "fr": "French", "es": "Spanish", "de": "German", "pt": "Portuguese"}
PREFERRED = {"en": "en-US", "it": "it-IT", "fr": "fr-FR", "es": "es-ES", "de": "de-DE", "pt": "pt-PT"}
SUGGESTIONS = 5  # alternatives offered for a word
ACTIONS = {1: "suggest", 2: "replace", 3: "delete"}  # CORRECTIVE_ACTION; 0 (none) never comes as an error

_CLSID_FACTORY = "{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}"
_IID_FACTORY = "{8E018A9D-2415-4677-BF08-794EA61F94BB}"


@dataclass(frozen=True)
class Issue:
    """A word Windows flags. `start` and `length` count Python characters in the text checked."""

    start: int
    length: int
    word: str
    action: str  # "suggest": pick one of `suggestions`; "replace": suggestions[0] for sure; "delete": a repeated word
    suggestions: tuple[str, ...] = ()


class _Guid(ctypes.Structure):
    _fields_ = [("a", c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]


def _call(obj: c_void_p, index: int, *args, types=()):
    """Call method `index` of the COM interface `obj` (0-2 are IUnknown's)."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    function = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *types)(vtable[index])
    return function(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_ulong, c_void_p)(vtable[2])(obj)


class _Windows:
    """The factory and one checker per language, made on first use."""

    def __init__(self):
        self.factory: c_void_p | None = None
        self.checkers: dict[str, c_void_p] = {}
        self.failed = False

    def ready(self) -> bool:
        if self.factory is not None:
            return True
        if self.failed or sys.platform != "win32":
            return False
        try:
            ole32 = ctypes.OleDLL("ole32")
            try:
                ole32.CoInitializeEx(None, 2)  # apartment threaded, as Qt's GUI thread is
            except OSError:
                pass  # the thread already has COM, in another mode: the checker works in both
            clsid, iid = _Guid(), _Guid()
            ole32.CLSIDFromString(c_wchar_p(_CLSID_FACTORY), byref(clsid))
            ole32.CLSIDFromString(c_wchar_p(_IID_FACTORY), byref(iid))
            factory = c_void_p()
            ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid), byref(factory))
            self.factory = factory
            self._ole32 = ole32
            return True
        except OSError:
            self.failed = True
            return False

    def free(self, pointer) -> None:
        self._ole32.CoTaskMemFree(pointer)

    def languages(self) -> list[str]:
        if not self.ready():
            return []
        found = []
        strings = c_void_p()
        try:
            _call(self.factory, 3, byref(strings), types=(POINTER(c_void_p),))  # get_SupportedLanguages
            while True:
                item, fetched = c_wchar_p(), c_ulong()
                _call(strings, 3, 1, byref(item), byref(fetched), types=(c_ulong, POINTER(c_wchar_p), POINTER(c_ulong)))
                if not fetched.value:
                    break
                found.append(item.value)
                self.free(item)
        except OSError:
            pass
        finally:
            _release(strings)
        return found

    def supported(self, tag: str) -> bool:
        if not self.ready():
            return False
        answer = ctypes.c_int()
        try:
            _call(self.factory, 4, tag, byref(answer), types=(c_wchar_p, POINTER(ctypes.c_int)))  # IsSupported
        except OSError:
            return False
        return bool(answer.value)

    def checker(self, tag: str) -> c_void_p | None:
        if tag not in self.checkers:
            if not self.supported(tag):
                return None
            checker = c_void_p()
            try:
                _call(self.factory, 5, tag, byref(checker), types=(c_wchar_p, POINTER(c_void_p)))  # CreateSpellChecker
            except OSError:
                return None
            self.checkers[tag] = checker
        return self.checkers[tag]

    def suggest(self, checker: c_void_p, word: str, limit: int) -> tuple[str, ...]:
        found: list[str] = []
        strings = c_void_p()
        try:
            _call(checker, 5, word, byref(strings), types=(c_wchar_p, POINTER(c_void_p)))  # Suggest
            while strings and len(found) < limit:
                item, fetched = c_wchar_p(), c_ulong()
                _call(strings, 3, 1, byref(item), byref(fetched), types=(c_ulong, POINTER(c_wchar_p), POINTER(c_ulong)))
                if not fetched.value:
                    break
                found.append(item.value)
                self.free(item)
        except OSError:
            pass
        finally:
            _release(strings)
        return tuple(found)

    def check(self, tag: str, text: str, limit: int) -> list[tuple[int, int, int, str]]:
        """(start, length, action, replacement) in UTF-16 units, as Windows counts."""
        checker = self.checker(tag)
        if checker is None:
            return []
        found = []
        errors = c_void_p()
        try:
            _call(checker, 4, text, byref(errors), types=(c_wchar_p, POINTER(c_void_p)))  # Check
            while errors:
                error = c_void_p()
                if _call(errors, 3, byref(error), types=(POINTER(c_void_p),)) != 0 or not error:
                    break  # S_FALSE: no more
                try:
                    start, length, action = c_ulong(), c_ulong(), c_int()
                    replacement = c_wchar_p()
                    _call(error, 3, byref(start), types=(POINTER(c_ulong),))
                    _call(error, 4, byref(length), types=(POINTER(c_ulong),))
                    _call(error, 5, byref(action), types=(POINTER(c_int),))
                    _call(error, 6, byref(replacement), types=(POINTER(c_wchar_p),))
                    found.append((start.value, length.value, action.value, replacement.value or ""))
                    if replacement:
                        self.free(replacement)
                finally:
                    _release(error)
                if len(found) >= limit:
                    break
        except OSError:
            pass
        finally:
            _release(errors)
        return found


_windows = _Windows()


def languages() -> list[str]:
    """The language tags Windows can check here, such as "en-GB" or "it-IT"."""
    return _windows.languages()


def tag_for(language: str) -> str | None:
    """The tag to check a language ("it", or a tag like "pt-BR") with, among those Windows has; None if none."""
    language = language.strip()
    if not language:
        return None
    if "-" in language and _windows.supported(language):
        return language
    base = language.split("-")[0].lower()
    preferred = PREFERRED.get(base)
    if preferred and _windows.supported(preferred):
        return preferred
    return next((tag for tag in languages() if tag.lower().split("-")[0] == base), None)


def available(language: str) -> bool:
    return tag_for(language) is not None


def _to_python(text: str):
    """A function mapping UTF-16 positions in `text` to positions in the Python string."""
    if all(ord(c) < 0x10000 for c in text):
        return lambda unit: unit
    units = []
    for index, char in enumerate(text):
        units.append(index)
        if ord(char) >= 0x10000:
            units.append(index)  # the second half of a surrogate pair
    units.append(len(text))
    return lambda unit: units[min(unit, len(units) - 1)]


def check(text: str, language: str, limit: int = 200) -> list[Issue]:
    """The words of `text` Windows flags in `language` ("it", "en", or a full tag), with what to do about each.
    Empty when Windows has no dictionary for the language."""
    tag = tag_for(language)
    if tag is None or not text.strip():
        return []
    checker = _windows.checker(tag)
    position = _to_python(text)
    issues = []
    for start, length, action, replacement in _windows.check(tag, text, limit):
        first, last = position(start), position(start + length)
        word = text[first:last]
        kind = ACTIONS.get(action, "suggest")
        if kind == "replace":
            suggestions = (replacement,) if replacement else ()
        elif kind == "suggest":
            suggestions = _windows.suggest(checker, word, SUGGESTIONS)
        else:
            suggestions = ()
        issues.append(Issue(first, last - first, word, kind, suggestions))
    return issues
