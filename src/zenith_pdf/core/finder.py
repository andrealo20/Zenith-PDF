"""The assistant's search: finds tools, commands, settings, shortcuts and guide chapters from a few words or a
question, in English, Italian, French, Spanish, German or Portuguese, with typos forgiven.

A question goes through the lexicon (core/lexicon.py) to the English words the app's
names are made of. Each word then matches whole words, the start of a word as it is
typed, or a word one or two letters away, in an entry's title, keywords, subtitle and
description, weighted in that order and by how rare the word is.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

from .lexicon import FILLER, SYNONYMS
from .writing import ALL_STOPWORDS, CJK, split_cjk

FIELDS = (("title", 3.0), ("keywords", 2.0), ("subtitle", 1.2), ("text", 1.0))
TOP_FIELD = 3.0
KIND_WEIGHT = {"tool": 1.0, "command": 1.0, "writing": 1.0, "info": 0.9, "setting": 0.92, "shortcut": 0.85,
               "guide": 0.8}
EXACT, PLURAL, STARTS, LONGER, NEAR = 1.0, 0.95, 0.75, 0.6, 0.55
TRANSLATED, STEMMED, MISTYPED = 0.95, 0.6, 0.7
OPTIONAL = 0.25  # the weight of little words ("in", "on", "da"): they help, but a result needn't have them
GENERIC = frozenset({"pdf", "document", "documento", "documents", "file", "zenith"})  # true of everything here
GENERIC_WEIGHT = 0.4
WHOLE_TITLE = 0.25  # a question that names every word of a title ("signature", "zoom in") means that very thing
FOCUS = 0.15  # and the more of a title it names, the likelier that is what it means
THRESHOLD = 0.08


def normalize(text: str) -> str:
    """Lower case, without accents, with only letters, digits and spaces. Chinese, Japanese and Korean letters
    stay whole: taking them apart would turn a Korean syllable into its letters."""
    out = []
    for char in text.lower():
        if CJK.match(char):
            out.append(char)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)))
    return "".join(c if c.isalnum() else " " for c in "".join(out))


def tokens(text: str) -> list[str]:
    """The words of `text`; a run of Chinese, Japanese or Korean as its overlapping pairs of characters."""
    return [part for word in normalize(text).split() for part in (split_cjk(word) if CJK.search(word) else [word])]


def distance(a: str, b: str, limit: int = 2) -> int:
    """Damerau-Levenshtein distance between `a` and `b`, or limit + 1 once it is past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = ca != cb
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if previous2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def _lexicon() -> dict[str, set[str]]:
    """Every word the lexicon knows, without accents, and the English words it leads to."""
    words: dict[str, set[str]] = {}
    for english, others in SYNONYMS.items():
        targets = set(tokens(english))
        for word in tokens(others) + tokens(english):
            words.setdefault(word, set()).update(targets)
    return words


LEXICON = _lexicon()
LITTLE = frozenset(normalize(" ".join(ALL_STOPWORDS)).split()) | FILLER


def expand(word: str) -> dict[str, float]:
    """`word` and the English words it may stand for, each with how sure that is."""
    found = {word: 1.0}
    for term in LEXICON.get(word, ()):
        found[term] = max(found.get(term, 0.0), TRANSLATED)
    if word not in LEXICON and len(word) >= 5:
        stem = word[:5]
        for known, terms in LEXICON.items():
            if len(known) >= 5 and known[:5] == stem:  # ruotiamo, ruotare: ruota
                factor = STEMMED
            elif len(word) >= 6 and abs(len(known) - len(word)) <= 1 and distance(word, known, 1) <= 1:
                factor = MISTYPED  # evidenzaire
            else:
                continue
            for term in terms:
                found[term] = max(found.get(term, 0.0), factor)
    return found


@dataclass(frozen=True)
class Entry:
    """Something the assistant can find. `key` is unique, `kind` one of KIND_WEIGHT's keys."""

    key: str
    kind: str
    title: str
    subtitle: str = ""
    keywords: str = ""
    text: str = ""
    keys: str = ""  # a keyboard shortcut to show beside it
    icon: str = ""
    color: str = ""
    enabled: bool = True
    note: str = ""  # why it can't be used now, when not enabled


class Finder:
    def __init__(self, entries):
        self.entries: list[Entry] = list(entries)
        # word -> {entry index: the weight of the best field it is in}
        self._index: dict[str, dict[int, float]] = {}
        self._titles = [normalize(e.title).strip() for e in self.entries]
        self._title_words = [frozenset(w for w in tokens(e.title) if w not in LITTLE and not w.isdigit())
                             for e in self.entries]
        for number, entry in enumerate(self.entries):
            for name, weight in FIELDS:
                for word in set(tokens(getattr(entry, name))):
                    places = self._index.setdefault(word, {})
                    places[number] = max(places.get(number, 0.0), weight)
        self._by_letter: dict[str, list[str]] = {}
        for word in self._index:
            self._by_letter.setdefault(word[0], []).append(word)
        count = max(len(self.entries), 1)
        self._idf = {word: math.log(1 + count / (1 + len(places))) for word, places in self._index.items()}

    def _matches(self, term: str, last: bool) -> dict[str, float]:
        """Words of the entries that `term` matches, and how well."""
        found: dict[str, float] = {}
        if term in self._index:
            found[term] = EXACT
        for word in self._by_letter.get(term[0], ()):
            if word == term:
                continue
            if word == term + "s" or term == word + "s":
                found[word] = max(found.get(word, 0.0), PLURAL)  # shortcut, shortcuts
            elif word.startswith(term) and (len(term) >= 3 or (last and len(term) >= 2)):
                found[word] = max(found.get(word, 0.0), STARTS)
            elif term.startswith(word) and len(word) >= 4:
                found[word] = max(found.get(word, 0.0), LONGER)  # highlights, highlight
            elif len(term) >= 5 and distance(term, word, 1 if len(term) < 8 else 2) <= (1 if len(term) < 8 else 2):
                found[word] = max(found.get(word, 0.0), NEAR)
        return found

    def search(self, query: str, limit: int = 30, boost: dict[str, float] | None = None) -> list[tuple[Entry, float]]:
        """The entries that match `query`, best first, with a score from 0 up."""
        words = tokens(query)
        if not words or not self.entries:
            return []
        content = [w for w in words if w not in LITTLE]
        weights = {w: (1.0 if (w in content or not content) else OPTIONAL) for w in words}
        if any(w not in GENERIC for w in content):
            weights.update({w: GENERIC_WEIGHT for w in content if w in GENERIC})
        best: dict[int, dict[str, float]] = {}  # entry -> query word -> how well it matched there
        named: dict[int, set[str]] = {}  # entry -> the words of its title the question named, translated or not
        rarity: dict[str, float] = {}
        for position, word in enumerate(words):
            last = position == len(words) - 1
            rarity[word] = 0.0
            for term, factor in expand(word).items():
                for match, quality in self._matches(term, last).items():
                    rarity[word] = max(rarity[word], self._idf[match])
                    for number, field in self._index[match].items():
                        value = field * quality * factor
                        seen = best.setdefault(number, {})
                        if value > seen.get(word, 0.0):
                            seen[word] = value
                        if quality >= STARTS and match in self._title_words[number]:
                            named.setdefault(number, set()).add(match)
        total = sum(weights[w] * max(rarity[w], 0.5) for w in words) * TOP_FIELD
        phrase = normalize(query).strip()
        results = []
        for number, matched in best.items():
            entry = self.entries[number]
            score = sum(weights[w] * max(rarity[w], 0.5) * value for w, value in matched.items()) / total
            needed = [w for w in words if weights[w] == 1.0] or [w for w in words if weights[w] > OPTIONAL] or words
            coverage = sum(1 for w in needed if w in matched) / len(needed)
            if len(needed) >= 2 and coverage < 0.5:
                continue
            score *= (0.5 + 0.5 * coverage) * KIND_WEIGHT.get(entry.kind, 0.9)
            title_words = self._title_words[number]
            if title_words:
                share = len(named.get(number, set()) & title_words) / len(title_words)
                score += FOCUS * share  # "rotate page" is more Rotate page than Page manager: reorder, rotate...
                if share == 1.0:
                    score += WHOLE_TITLE
            title = self._titles[number]
            if phrase and title.startswith(phrase):
                score += 0.4
            elif phrase and len(phrase) >= 3 and phrase in title:
                score += 0.25
            if not entry.enabled:
                score *= 0.85
            if boost:
                score += boost.get(entry.key, 0.0)
            if score >= THRESHOLD:
                results.append((entry, score))
        results.sort(key=lambda pair: -pair[1])
        return results[:limit]
