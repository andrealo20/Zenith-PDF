"""Writing helpers that need no language model: which language a text is in, small fixes to spacing,
punctuation and capitals, the sentences that carry a text, and study notes from a document's highlights.

Everything here is plain Python over the text, in English, Italian, French, Spanish,
German and Portuguese. Spelling itself comes from Windows (core/spelling.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

LANGUAGES = ("en", "it", "fr", "es", "de", "pt")

STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""a about above after again against all also am an and any are as at be because been before being
        below between both but by can could did do does doing down during each few for from further had has have
        having he her here hers him his how i if in into is it its itself just me more most my no nor not now of
        off on once only or other our ours out over own same she should so some such than that the their theirs them
        then there these they this those through to too under until up very was we were what when where which while
        who whom why will with would you your yours""".split()),
    "it": frozenset("""a ad al alla alle allo agli ai anche ancora avere aveva avevano c che chi ci come con contro
        cui da dal dalla dalle dallo dagli dai degli dei del della delle dello di dove e ed è era erano essere fa
        fino fra gli ha hanno i il in io la le lei li lo loro lui ma me mi mio mia miei mie molto ne nei nel nella
        nelle nello negli noi non nostro nostra o oppure per perché però più poi quale quali quando quanto quella
        quelle quello questa queste questo questi se sei si sia siamo sono su sua sue sui sul sulla sulle sullo suo
        suoi te ti tra tu tua tuo tutti tutto un una uno vi voi""".split()),
    "fr": frozenset("""à au aux avec ce ces cet cette comme dans de des du elle elles en est et été être eu il ils
        je la le les leur leurs lui ma mais me même mes moi mon ne nos notre nous on ou où par pas plus pour qu que
        qui sa se ses si son sont sur ta te tes toi ton tu un une vos votre vous y ça était sont aussi très bien""".split()),
    "es": frozenset("""a al algo como con de del el ella ellas ellos en entre era es esa ese eso esta este esto está
        están fue ha han hay la las le les lo los más me mi muy no nos o para pero por que qué se sea ser si sin
        sobre su sus también te tiene todo tu un una uno unos unas y ya yo""".split()),
    "de": frozenset("""aber alle als am an auch auf aus bei bin bis das dass dem den der des die dies diese dieser
        doch du ein eine einem einen einer es für hat haben ich ihr im in ist ja kann kein mit muss nach nicht noch
        nur oder ohne sein sich sie sind so über um und uns von vor war wie wir wird zu zum zur""".split()),
    "pt": frozenset("""a ao aos as à às com como da das de do dos e é ela elas ele eles em entre era essa esse está
        estão eu foi há isso isto já mais mas me muito na nas não no nos o os ou para pela pelo por que quem se
        sem ser seu seus sua suas também tem um uma umas uns você""".split()),
}
ALL_STOPWORDS = frozenset().union(*STOPWORDS.values())

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
# Short words that end with a full stop without ending the sentence.
ABBREVIATIONS = frozenset("""e.g i.e etc fig figs eq eqs no nr vs cf al approx ca dr mr mrs ms prof ing dott sig
    sec ch vol pag pp p ad es ecc cfr art n p.es""".split())


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text)]


def detect_language(text: str, default: str | None = None) -> str | None:
    """The language most of the text's little words belong to, or `default` when it can't tell."""
    found = words(text)
    if not found:
        return default
    counts = {code: sum(1 for w in found if w in STOPWORDS[code]) for code in LANGUAGES}
    best = max(LANGUAGES, key=lambda code: (counts[code], code == default))
    if counts[best] == 0 or (len(found) > 6 and counts[best] < 2):
        return default
    return best


# Tidy: spacing, punctuation, capitals ------------------------------------------------------


@dataclass(frozen=True)
class Fix:
    start: int
    end: int
    replacement: str
    reason: str

    @property
    def length(self) -> int:
        return self.end - self.start


def tidy(text: str, language: str | None = None) -> list[Fix]:
    """Small, safe fixes: doubled spaces, spaces before punctuation, a missing space after it, spaces at the end
    of a line, and a capital letter to start a sentence. French keeps its space before ; : ! and ?"""
    fixes: list[Fix] = []
    for match in re.finditer(r"(?<=\S)[ \t]{2,}(?=\S)", text):
        fixes.append(Fix(match.start(), match.end(), " ", "Extra space"))
    before = r"[ \t]+([,.])" if language == "fr" else r"[ \t]+([,.;:!?])"
    for match in re.finditer(before, text):
        if match.start() > 0 and text[match.start() - 1] not in " \t\n" and not _starts_number(text, match.end()):
            if _in_address(text, match.end() - 1):
                continue  # a file type or an address: ".pdf", ":8080"
            if match.end() < len(text) and text[match.end()].isalpha():
                # "word ,word": the space goes after the comma instead
                fixes.append(Fix(match.start(), match.end(), match.group(1) + " ", "Space before punctuation"))
            else:
                fixes.append(Fix(match.start(), match.end(1) - 1, "", "Space before punctuation"))
    for match in re.finditer(r"(?<=[^\W\d_])([,;:!?])(?=[^\W\d_])", text):
        if not _in_address(text, match.start()):
            fixes.append(Fix(match.end(), match.end(), " ", "Missing space"))
    for match in re.finditer(r"(?<=[a-zà-ÿ]{2})\.(?=[A-ZÀ-Ý][a-zà-ÿ])", text):
        if not _in_address(text, match.start()):
            fixes.append(Fix(match.end(), match.end(), " ", "Missing space"))
    for match in re.finditer(r"[ \t]+(?=\n|$)", text):
        if match.start() > 0:
            fixes.append(Fix(match.start(), match.end(), "", "Space at the end of a line"))
    if len(words(text)) >= 4:  # prose: a label like "x = 3" or "v out" keeps its small letters
        for start in _sentence_starts(text):
            word = WORD.match(text, start)
            if word is not None and len(word.group()) >= 3 and word.group().islower():
                fixes.append(Fix(start, start + 1, text[start].upper(), "Capital letter"))
    return _without_overlaps(fixes)


def _starts_number(text: str, index: int) -> bool:
    return index < len(text) and text[index].isdigit()


def _in_address(text: str, index: int) -> bool:
    """Inside a web or e-mail address, a file name or a path, where punctuation runs on without spaces."""
    left = text.rfind(" ", 0, index) + 1
    right = text.find(" ", index)
    token = text[left:right if right != -1 else len(text)]
    return bool(re.search(r"://|www\.|@|\\|/|\.(pdf|docx?|xlsx?|pptx?|png|jpe?g|txt|com|org|net|it|eu)\b", token,
                          re.IGNORECASE))


def _sentence_starts(text: str):
    first = re.search(r"\S", text)
    if first is not None and text[first.start()].isalpha():
        yield first.start()
    for match in re.finditer(r"([.!?])[ \t]+(?=[^\W\d_])", text):
        if match.group(1) == "." and _abbreviation_before(text, match.start()):
            continue
        yield match.end()


def _abbreviation_before(text: str, dot: int) -> bool:
    left = dot
    while left > 0 and not text[left - 1].isspace():
        left -= 1
    token = text[left:dot].lower().strip("([\"'")
    return token in ABBREVIATIONS or len(token) <= 1 or "." in token


def _without_overlaps(fixes: list[Fix]) -> list[Fix]:
    kept: list[Fix] = []
    for fix in sorted(fixes, key=lambda f: (f.start, f.end)):
        if kept and fix.start < kept[-1].end:
            continue
        kept.append(fix)
    return kept


def apply_fixes(text: str, fixes) -> str:
    """`text` with the fixes (anything with start, end and replacement) made, from the end backwards."""
    for fix in sorted(fixes, key=lambda f: f.start, reverse=True):
        text = text[:fix.start] + fix.replacement + text[fix.end:]
    return text


# Key sentences ----------------------------------------------------------------------------


def clean_pdf_text(text: str) -> str:
    """Text as a PDF hands it out, as running prose: words split at the end of a line joined, lines run on."""
    text = re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[a-zà-ÿ])", "", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def sentences(text: str) -> list[str]:
    """The sentences of `text`, split after . ! ? and a capital, but not after an abbreviation."""
    text = clean_pdf_text(text)
    found, start = [], 0
    for match in re.finditer(r"[.!?…]+[\"'”»)]?\s+(?=[\"'“«(]?[A-ZÀ-Ý0-9])", text):
        if text[match.start()] == "." and _abbreviation_before(text, match.start()):
            continue
        piece = text[start:match.end()].strip()
        if piece:
            found.append(piece)
        start = match.end()
    rest = text[start:].strip()
    if rest:
        found.append(rest)
    return found


def key_sentences(text: str, count: int | None = None, language: str | None = None) -> list[str]:
    """The sentences that carry most of `text`, in their order: those made of the words the text uses most,
    with a little weight for the first ones. `count` defaults to about a quarter, from 2 to 6."""
    all_sentences = sentences(text)
    if len(all_sentences) <= 2:
        return all_sentences
    language = language or detect_language(text, "en")
    stop = STOPWORDS.get(language, frozenset()) | ALL_STOPWORDS
    content = [[w for w in words(s) if len(w) > 2 and w not in stop] for s in all_sentences]
    frequency = Counter(w for sentence in content for w in set(sentence))
    if not frequency:
        return all_sentences[: count or 2]
    top = max(frequency.values())
    if count is None:
        count = max(2, min(6, round(len(all_sentences) * 0.25)))
    count = min(count, len(all_sentences))
    scores = []
    for index, (sentence, terms) in enumerate(zip(all_sentences, content)):
        length = len(words(sentence))
        if not terms or length < 4:
            scores.append(0.0)
            continue
        weight = sum(frequency[w] / top for w in set(terms)) / math.sqrt(len(terms))
        weight *= 1.15 if index == 0 else 1.05 if index == 1 else 1.0
        if length > 60:
            weight *= 0.7
        scores.append(weight)
    chosen = sorted(sorted(range(len(all_sentences)), key=lambda i: -scores[i])[:count])
    return [all_sentences[i] for i in chosen]


# Study notes ------------------------------------------------------------------------------

COLOUR_NAMES = (
    ("Yellow", (1.0, 0.85, 0.0)), ("Green", (0.2, 0.75, 0.3)), ("Blue", (0.2, 0.5, 0.95)),
    ("Pink", (1.0, 0.45, 0.7)), ("Red", (0.9, 0.15, 0.15)), ("Orange", (1.0, 0.55, 0.0)),
    ("Purple", (0.55, 0.3, 0.9)), ("Cyan", (0.1, 0.8, 0.9)), ("Grey", (0.55, 0.55, 0.55)),
    ("Black", (0.1, 0.1, 0.1)),
)
MARK_NAMES = {"Highlight": "highlight", "Underline": "underline", "Squiggly": "squiggle", "StrikeOut": "strikethrough"}


def colour_name(color) -> str:
    """The nearest everyday name of an RGB colour (channels 0 to 1)."""
    r, g, b = (float(c) for c in color[:3])
    return min(COLOUR_NAMES, key=lambda item: sum((x - y) ** 2 for x, y in zip((r, g, b), item[1])))[0]


@dataclass(frozen=True)
class Marked:
    """One mark on the text of a page: what it marks, how, and the comment on it."""

    page: int  # from 0
    style: str  # Highlight, Underline, Squiggly or StrikeOut
    color: tuple[float, float, float]
    text: str
    comment: str = ""
    top: float = 0.0  # where it is on the page, to keep reading order


def study_notes(marks: list[Marked], title: str, by: str = "page") -> str:
    """Markdown notes of `marks`: grouped by page (in reading order) or by colour, with comments under each."""
    if not marks:
        return f"# {title}\n\nNo highlights, underlines or strikethroughs yet.\n"
    ordered = sorted(marks, key=lambda m: (m.page, m.top))
    lines = [f"# {title}", ""]
    if by == "colour":
        groups: dict[str, list[Marked]] = {}
        for mark in ordered:
            groups.setdefault(colour_name(mark.color), []).append(mark)
        for name, members in groups.items():
            lines += [f"## {name}", ""]
            for mark in members:
                kind = "" if mark.style == "Highlight" else f" ({MARK_NAMES.get(mark.style, mark.style.lower())})"
                lines.append(f"- p. {mark.page + 1}{kind}: {_quoted(mark.text)}")
                if mark.comment.strip():
                    lines.append(f"  - Note: {mark.comment.strip()}")
            lines.append("")
    else:
        page = None
        for mark in ordered:
            if mark.page != page:
                if page is not None:
                    lines.append("")
                page = mark.page
                lines += [f"## Page {page + 1}", ""]
            kind = MARK_NAMES.get(mark.style, mark.style.lower())
            lines.append(f"- **{colour_name(mark.color)} {kind}:** {_quoted(mark.text)}")
            if mark.comment.strip():
                lines.append(f"  - Note: {mark.comment.strip()}")
        lines.append("")
    return "\n".join(lines)


def _quoted(text: str) -> str:
    text = clean_pdf_text(text)
    return text if text else "(no text under it)"
