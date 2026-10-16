"""Page text as PDFium reads it, made fit to copy and search.

A font without a Unicode mapping comes out of PDFium as its raw character
codes. The usual case is a TeX document set in the T1 (Cork) encoding with
Type 3 fonts: "Qualifiers" reads "Quali\\x1cers", because code 0x1C is the fi
ligature there. Ligature characters such as U+FB01 are spelled out too, and
the mark PDFium leaves where a word was hyphenated across two lines goes, so
copied text reads, and searches, like typed text.

Nothing here touches tabs and line breaks: PDFium uses them for the layout.
"""

from __future__ import annotations

import re

# The control codes of the T1 (Cork) font encoding, by what they draw.
T1_CODES = {
    0x00: "`", 0x01: "´", 0x02: "ˆ", 0x03: "˜", 0x04: "¨", 0x05: "˝",
    0x06: "˚", 0x07: "ˇ", 0x08: "˘", 0x0B: "¸", 0x0C: "˛",
    0x0E: "‹", 0x0F: "›", 0x10: "“", 0x11: "”", 0x12: "„", 0x13: "«",
    0x14: "»", 0x15: "\u2013", 0x16: "\u2014",  # the en and em dash
    0x17: "",  # the compound word mark: an invisible break between letters
    0x18: "0", 0x19: "ı", 0x1A: "ȷ",
    0x1B: "ff", 0x1C: "fi", 0x1D: "fl", 0x1E: "ffi", 0x1F: "ffl",
}
LIGATURES = {0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl", 0xFB03: "ffi", 0xFB04: "ffl", 0xFB05: "st", 0xFB06: "st"}
HYPHEN_MARK = 0xFFFE  # PDFium's stand-in for a hyphen at a line end, the line break dropped: the word goes on
TABLE: dict[int, str] = {**T1_CODES, **LIGATURES, HYPHEN_MARK: ""}
_FOUND = re.compile("[" + "".join(re.escape(chr(code)) for code in TABLE) + "]")


WORD_MARKS = "_-'’"  # inside a word they keep it whole: snake_case, well-known, don't


def in_word(char: str) -> bool:
    """Whether a character of a page's raw text belongs to a word: a letter or digit, a ligature code that
    stands for letters, one of WORD_MARKS, or a mark PDFium leaves inside a word (a hyphen at a line end)."""
    spelled = TABLE.get(ord(char), char) if char else ""
    if char and not spelled:
        return True  # it vanishes when cleaned: the word goes on across it
    return spelled.isalnum() or (len(spelled) == 1 and spelled in WORD_MARKS)


def needs_cleaning(text: str) -> bool:
    return _FOUND.search(text) is not None


def clean(text: str) -> str:
    return text.translate(TABLE) if needs_cleaning(text) else text


def cleaned(text: str) -> tuple[str, list[tuple[int, int]]]:
    """`text` cleaned, and for each character of the result the characters of `text` it came
    from, as (first, end) indices.

    Indices count UTF-16 units, as PDFium numbers the characters of a page:
    a character beyond U+FFFF takes two.
    """
    parts, origin, index = [], [], 0
    for char in text:
        replacement = TABLE.get(ord(char), char)
        end = index + (2 if ord(char) > 0xFFFF else 1)
        parts.append(replacement)
        origin.extend([(index, end)] * len(replacement))
        index = end
    return "".join(parts), origin


def units(text: str) -> int:
    """How many characters PDFium counts in `text`."""
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def find(text: str, query: str, match_case: bool = False, whole_word: bool = False) -> list[tuple[int, int]]:
    """Where `query` occurs in a page's raw text, comparing cleaned text: (first character, count).

    Any run of white space, a line break included, matches any white space in the query.
    """
    words = clean(query).split()
    if not words:
        return []
    pattern = r"\s+".join(re.escape(word) for word in words)
    if whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    haystack, origin = cleaned(text)
    hits = []
    for match in re.finditer(pattern, haystack, 0 if match_case else re.IGNORECASE):
        first, end = origin[match.start()][0], origin[match.end() - 1][1]
        hits.append((first, end - first))
    return hits
