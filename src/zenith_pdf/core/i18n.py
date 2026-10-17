"""Zenith PDF in English, Italian, French, Spanish, German or Portuguese.

The app is written in English; tr() turns a text into the language in use as it is shown (the Qt calls that
show text go through it: labels, buttons, tooltips, menus, window titles, painted text). The translations are
in locales/<language>.json, keyed by the English text. Texts built from parts, such as "Page 12" or "Zenith PDF
0.5.1 is out", are keyed by their template, "Page {}", and the parts are put back into the translation, in
order or as {0}, {1}... A part that is itself a known text is translated too.

The language is the system's when it is one of these, else English; Settings > View & Layout picks another.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

LANGUAGES = {"en": "English", "it": "Italiano", "fr": "Français", "es": "Español", "de": "Deutsch",
             "pt": "Português"}
LOCALES = Path(__file__).resolve().parent.parent / "locales"
_FIELD = re.compile(r"\{(\d*)\}")


class _Template(NamedTuple):
    pattern: re.Pattern
    value: str
    parts: list[str]  # its own words, between the {}

    def could_match(self, text: str) -> bool:
        """Whether `text` has the template's words in order, start and end included: a look in one pass, so the
        regular expression only runs where it can match (a template of several {} tried on a long text that
        doesn't fit would take ages to fail)."""
        first, *middle, last = self.parts
        if not text.startswith(first) or not text.endswith(last):
            return False
        at, end = len(first), len(text) - len(last)
        for part in middle:
            at = text.find(part, at + 1, end)
            if at < 0:
                return False
            at += len(part)
        return at < end


class _Translations:
    def __init__(self):
        self.code = "en"
        self.exact: dict[str, str] = {}
        self.templates: list[_Template] = []
        self._by_start: dict[str, list[_Template]] = {}
        self.reverse: dict[str, str] = {}  # translation -> English, for code that looks texts up by their English


_current = _Translations()


def system_language() -> str:
    """The system's language, if Zenith PDF speaks it; else English."""
    from PySide6.QtCore import QLocale

    for tag in QLocale.system().uiLanguages() or [QLocale.system().name()]:
        code = tag.replace("_", "-").split("-")[0].lower()
        if code in LANGUAGES:
            return code
    return "en"


def language() -> str:
    return _current.code


def _pattern(template: str) -> re.Pattern | None:
    """The regular expression a template's texts match: its words as they are, each {} any text. A template with
    less than two words of its own, and no label such as "Theme: {}", only takes numbers for its {}, so "{} pages"
    can't eat a file's name."""
    parts = re.split(r"\{\d*\}", template)
    fields = len(parts) - 1
    if not fields:
        return None
    literal = " ".join("".join(parts).split())
    if not any(ch.isalpha() for ch in literal):
        return None
    # Two words of its own (commas are not words), or a label before its {}: "Theme: {}", "Tool · {}".
    loose = (sum(any(ch.isalpha() for ch in word) for word in literal.split()) >= 2
             or any(re.search(r"\w\s?[:·]\s*$", part) for part in parts[:-1]))
    group = "(.+?)" if loose else r"([\d.,:/%+-]+)"
    body = group.join(re.escape(part) for part in parts)
    return re.compile(f"^{body}$", re.DOTALL)


def set_language(code: str | None) -> str:
    """Use `code` ("auto" or None for the system's); the language in use."""
    if code in (None, "", "auto"):
        code = os.environ.get("ZENITH_LANGUAGE") or system_language()
    code = code if code in LANGUAGES else "en"
    found = _Translations()
    found.code = code
    if code != "en":
        try:
            table = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = {}
        for key, value in table.items():
            if not value or value == key:
                continue
            if _FIELD.search(key) and (pattern := _pattern(key)) is not None:
                found.templates.append(_Template(pattern, value, re.split(r"\{\d*\}", key)))
            else:
                found.exact[key] = value
        found.reverse = {value: key for key, value in found.exact.items()}
        # The longest templates first: "Version {} of {}" before "Version {}".
        found.templates.sort(key=lambda item: -len(item.pattern.pattern))
        for template in found.templates:
            found._by_start.setdefault(template.parts[0][:6], []).append(template)
    global _current
    _current = found
    return code


def source(text: str) -> str:
    """The English a translated text came from (itself when it is English, or unknown)."""
    return _current.reverse.get(text, text)


def tr_in(context: str, text: str) -> str:
    """`text` as it reads in `context`, for a word whose translation depends on where it is: the "Clear" of
    the glass is not the Clear button. The locale keys it as "context|text"; without one, tr(text)."""
    found = _current.exact.get(f"{context}|{text}")
    return found if found is not None else tr(text)


def tr(text):
    """`text` in the language in use, or as it is when it has no translation (or isn't text)."""
    if not isinstance(text, str) or _current.code == "en" or not text:
        return text
    found = _current.exact.get(text)
    if found is not None:
        return found
    stripped = text.strip()
    if stripped != text and stripped in _current.exact:  # the same words with spaces round them
        return text.replace(stripped, _current.exact[stripped])
    head, tab, keys = text.partition("\t")  # a menu entry: its words, then the keys it shows
    if tab and head in _current.exact:
        return _current.exact[head] + tab + keys
    for start in dict.fromkeys(text[:n] for n in range(6, -1, -1)):  # the templates with most words first
        for template in _current._by_start.get(start, ()):
            match = template.could_match(text) and template.pattern.match(text)
            if match:
                parts = [_current.exact.get(part, part) for part in match.groups()]
                try:
                    return template.value.format(*parts)
                except (IndexError, KeyError, ValueError):
                    return text
    return text
