"""Spelling, through the spell checker of the system.

- Windows 8 and later: ISpellCheckerFactory, for every language added in
  Settings > Time & language > Language & region. The COM interfaces are called
  through their vtables with ctypes, so no extra package is needed.
- macOS: NSSpellChecker, through pyobjc, for the languages macOS has.
- Linux: Hunspell dictionaries through Enchant (pyenchant), one package per
  language, such as hunspell-it.

Nothing is installed or sent anywhere. Call from the GUI thread.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import POINTER, byref, c_int, c_ulong, c_void_p, c_wchar_p
from dataclasses import dataclass

NAMES = {"en": "English", "it": "Italian", "fr": "French", "es": "Spanish", "de": "German", "pt": "Portuguese"}
PREFERRED = {"en": "en-US", "it": "it-IT", "fr": "fr-FR", "es": "es-ES", "de": "de-DE", "pt": "pt-PT"}
SUGGESTIONS = 5  # alternatives offered for a word
ACTIONS = {1: "suggest", 2: "replace", 3: "delete"}  # CORRECTIVE_ACTION; 0 (none) never comes as an error
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")  # letters, with apostrophes inside: l'anno, don't
DOUBLED = re.compile(r"\b([^\W\d_]+)[ \t]+(\1)\b", re.IGNORECASE)  # "the the"

_CLSID_FACTORY = "{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}"
_IID_FACTORY = "{8E018A9D-2415-4677-BF08-794EA61F94BB}"


@dataclass(frozen=True)
class Issue:
    """A word the checker flags. `start` and `length` count Python characters in the text checked."""

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
    function = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *types)(vtable[index])
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

    def issues(self, tag: str, text: str, limit: int) -> list[Issue]:
        checker = self.checker(tag)
        if checker is None:
            return []
        position = _to_python(text)
        issues = []
        for start, length, action, replacement in self.check(tag, text, limit):
            first, last = position(start), position(start + length)
            word = text[first:last]
            kind = ACTIONS.get(action, "suggest")
            if kind == "replace":
                suggestions = (replacement,) if replacement else ()
            elif kind == "suggest":
                suggestions = self.suggest(checker, word, SUGGESTIONS)
            else:
                suggestions = ()
            issues.append(Issue(first, last - first, word, kind, suggestions))
        return issues


class _Mac:
    """NSSpellChecker, the checker every Mac app shares."""

    def __init__(self):
        self.checker = None
        self.failed = False
        self.native: dict[str, str] = {}  # "en-GB": "en_GB", as macOS names it

    def ready(self) -> bool:
        if self.checker is not None:
            return True
        if self.failed or sys.platform != "darwin":
            return False
        try:
            from AppKit import NSSpellChecker
        except ImportError:
            self.failed = True
            return False
        self.checker = NSSpellChecker.sharedSpellChecker()
        return self.checker is not None

    def languages(self) -> list[str]:
        if not self.ready():
            return []
        self.native = {str(name).replace("_", "-"): str(name) for name in self.checker.availableLanguages()}
        return list(self.native)

    def supported(self, tag: str) -> bool:
        if not self.native:
            self.languages()
        return tag in self.native

    def issues(self, tag: str, text: str, limit: int) -> list[Issue]:
        if not self.ready():
            return []
        language = self.native.get(tag, tag.replace("-", "_"))
        position = _to_python(text)
        units = len(text.encode("utf-16-le")) // 2  # macOS counts UTF-16 units, as Windows does
        issues, start = [], 0
        while start < units and len(issues) < limit:
            found = self.checker.checkSpellingOfString_startingAt_language_wrap_inSpellDocumentWithTag_wordCount_(
                text, start, language, False, 0, None)
            found = found[0] if isinstance(found, tuple) else found
            if found.length == 0 or found.location >= units:
                break
            first, last = position(found.location), position(found.location + found.length)
            guesses = self.checker.guessesForWordRange_inString_language_inSpellDocumentWithTag_(
                found, text, language, 0) or []
            issues.append(Issue(first, last - first, text[first:last], "suggest",
                                tuple(str(guess) for guess in list(guesses)[:SUGGESTIONS])))
            start = found.location + found.length
        return _with_doubled(text, issues)


class _Enchant:
    """Hunspell dictionaries through Enchant, as most Linux apps check spelling."""

    def __init__(self):
        self.broker = None
        self.failed = False
        self.dictionaries: dict[str, object] = {}

    def ready(self) -> bool:
        if self.broker is not None:
            return True
        if self.failed:
            return False
        try:
            import enchant

            self.broker = enchant.Broker()
        except Exception:  # pyenchant missing, or the Enchant library it loads
            self.failed = True
            return False
        return True

    def languages(self) -> list[str]:
        if not self.ready():
            return []
        return sorted({str(tag).replace("_", "-") for tag in self.broker.list_languages()})

    def supported(self, tag: str) -> bool:
        if not self.ready():
            return False
        try:
            return bool(self.broker.dict_exists(tag.replace("-", "_")))
        except Exception:
            return False

    def _dictionary(self, tag: str):
        if tag not in self.dictionaries:
            self.dictionaries[tag] = self.broker.request_dict(tag.replace("-", "_"))
        return self.dictionaries[tag]

    def issues(self, tag: str, text: str, limit: int) -> list[Issue]:
        if not self.ready():
            return []
        try:
            dictionary = self._dictionary(tag)
        except Exception:
            return []
        issues = []
        for match in WORD.finditer(text):
            word = match.group()
            if len(word) < 2 or word.isupper() or _known(dictionary, word):
                continue  # single letters and acronyms (PDF, OCR) are left alone
            issues.append(Issue(match.start(), len(word), word, "suggest",
                                tuple(_swaps_first(word, dictionary.suggest(word))[:SUGGESTIONS])))
            if len(issues) >= limit:
                break
        return _with_doubled(text, issues)


def _swaps_first(word: str, suggestions: list[str]) -> list[str]:
    """The suggestions with the same letters as `word` first: two swapped letters are the commonest slip."""
    letters = sorted(word.lower())
    return sorted(suggestions, key=lambda suggestion: sorted(suggestion.lower()) != letters)


def _known(dictionary, word: str) -> bool:
    """In the dictionary, or an elision whose word is: l'anno, dell'acqua, qu'il."""
    if dictionary.check(word):
        return True
    for mark in ("'", "’"):
        if mark in word:
            rest = word.rsplit(mark, 1)[1]
            return len(rest) > 1 and dictionary.check(rest)
    return False


def _with_doubled(text: str, issues: list[Issue]) -> list[Issue]:
    """`issues`, plus each word written twice in a row: Windows flags those itself, the others don't."""
    doubled = [Issue(m.start(2), len(m.group(2)), m.group(2), "delete") for m in DOUBLED.finditer(text)]
    spelled = [i for i in issues if not any(d.start < i.start + i.length and i.start < d.start + d.length
                                            for d in doubled)]
    return sorted(spelled + doubled, key=lambda issue: issue.start)


_windows = _Windows()
_backend = _windows if sys.platform == "win32" else _Mac() if sys.platform == "darwin" else _Enchant()


def languages() -> list[str]:
    """The language tags the system can check here, such as "en-GB" or "it-IT"."""
    return _backend.languages()


def tag_for(language: str) -> str | None:
    """The tag to check a language ("it", or a tag like "pt-BR") with, among those the system has; None if none."""
    language = language.strip()
    if not language:
        return None
    if "-" in language and _backend.supported(language):
        return language
    base = language.split("-")[0].lower()
    preferred = PREFERRED.get(base)
    if preferred and _backend.supported(preferred):
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
    """The words of `text` the system flags in `language` ("it", "en", or a full tag), with what to do about
    each. Empty when the system has no dictionary for the language."""
    tag = tag_for(language)
    if tag is None or not text.strip():
        return []
    return _backend.issues(tag, text, limit)
