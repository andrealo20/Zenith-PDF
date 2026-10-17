"""Writing helpers that need no language model: which language a text is in, small fixes to spacing,
punctuation and capitals, the sentences that carry a text, and study notes from a document's highlights.

Everything here is plain Python over the text, in English, Italian, French, Spanish, German, Portuguese,
Russian, Ukrainian, Polish, Czech, Dutch and Turkish, and in Chinese, Japanese and Korean, whose words are
read two characters at a time as they have no spaces between them. Spelling itself comes from Windows
(core/spelling.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

LANGUAGES = ("en", "it", "fr", "es", "de", "pt", "ru", "uk", "pl", "cs", "nl", "tr", "zh", "ja", "ko")
CJK_LANGUAGES = ("zh", "ja", "ko")
# Chinese characters, Japanese kana and Korean hangul: scripts written without spaces between words.
CJK = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")

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
    "ru": frozenset("""а без более бы был была были было быть в вам вас весь во вот все всего всех вы где да даже
        для до его ее если есть еще же за здесь и из или им их к как ко когда кто ли либо мне может мы на над
        нам нас не него нее нет ни них но ну о об однако он она они оно от очень по под при с со так также такой
        там те тем то того тоже той только том ты у уже хотя чего чей чем что чтобы чье эта эти это я""".split()),
    "uk": frozenset("""а або аж але без би був була були було бути в вам вас ви від він вона вони воно все всі де
        для до є же з за звідки и із інший її їх й к коли котрий ми мене мені на над нам нас не неї нема ні ніж
        ну о об один ось під після по при про та так також там те тим то тобі того тоді той тому ти у уже хоча
        це цей ці цього чи що щоб як який яка які я""".split()),
    "pl": frozenset("""a aby ale bardzo bez był była było były być ci co czy dla do gdy gdzie go i ich im jak
        jakie jako je jego jej jest jestem już każdy kiedy kto która które który lub ma mi mnie może można na nad
        nam nas nawet nie nich nim no o od on ona one oni ono oraz po pod przed przez przy się są ta tak także tam
        te tego tej ten to tu tylko tym w we wszystko z za ze że""".split()),
    "cs": frozenset("""a aby ale ani až bez by byl byla bylo byli být co což či do i jak jako je jeho jej její
        jejich jen jenž ještě již jsem jsme jsou jste k kde kdo když ke která které který mezi mi mně mít my na
        nad nás ne nebo než ni nic o od on ona oni ono pak po pod podle pokud pro proto protože před při s se si
        své svůj ta tak také tam te ten tento tím to toho tu ty u už v ve více všechno z za ze že""".split()),
    "nl": frozenset("""aan al als bij dan dat de den der deze die dit doch door dus een en er ge geen had heb hebben
        heeft hem het hier hij hoe hun ik in is ja je kan kon maar me meer men met mij mijn na naar niet niets nog
        nu of om omdat ons ook op over te tegen toch tot u uit van veel voor want waren was wat we wel werd wie wij
        worden wordt zal ze zei zich zij zijn zo zonder zou""".split()),
    "tr": frozenset("""acaba ama ancak aslında az bazı belki ben beni benim bile bir biraz birçok birkaç biz bu
        buna bunda bundan bunu bunun burada çok çünkü da daha de değil diye en gibi hem hep hepsi her hiç için ile
        ise kadar ki kim mi mı mu mü nasıl ne neden nerede niçin o olan olarak olduğu ona onlar onu onun sen siz
        şey şu tüm ve veya ya yani""".split()),
}
ALL_STOPWORDS = frozenset().union(*STOPWORDS.values())

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
# Short words that end with a full stop without ending the sentence.
ABBREVIATIONS = frozenset("""e.g i.e etc fig figs eq eqs no nr vs cf al approx ca dr mr mrs ms prof ing dott sig
    sec ch vol pag pp p ad es ecc cfr art n p.es""".split())


def split_cjk(word: str) -> list[str]:
    """A word of Chinese, Japanese or Korean as the overlapping pairs of characters that stand for its words;
    the rest of it, Latin letters say, as they are."""
    out = []
    for run in re.findall(f"{CJK.pattern}+|[^{CJK.pattern[1:-1]}]+", word):
        if CJK.match(run):
            out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
        else:
            out.append(run)
    return out


def words(text: str) -> list[str]:
    found = [w.lower() for w in WORD.findall(text)]
    return [part for w in found for part in (split_cjk(w) if CJK.search(w) else [w])]


def detect_language(text: str, default: str | None = None) -> str | None:
    """The language most of the text's little words belong to, or `default` when it can't tell. Chinese,
    Japanese and Korean go by their scripts."""
    letters = [c for c in text if c.isalpha()]
    cjk = sum(1 for c in letters if CJK.match(c))
    if letters and cjk / len(letters) > 0.3:
        if sum(1 for c in letters if HANGUL.match(c)) > cjk * 0.3:
            return "ko"
        return "ja" if sum(1 for c in letters if KANA.match(c)) > cjk * 0.1 else "zh"
    found = words(text)
    if not found:
        return default
    counts = {code: sum(1 for w in found if w in STOPWORDS[code]) for code in STOPWORDS}
    best = max(STOPWORDS, key=lambda code: (counts[code], code == default))
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
    if language in CJK_LANGUAGES or detect_language(text) in CJK_LANGUAGES:
        # No spaces round punctuation, and no capitals: only the spaces left at the end of a line.
        fixes += [Fix(m.start(), m.end(), "", "Space at the end of a line")
                  for m in re.finditer(r"[ \t]+(?=\n|$)", text) if m.start() > 0]
        return _without_overlaps(fixes)
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
                capital = "İ" if language == "tr" and text[start] == "i" else text[start].upper()  # Turkish dotted i
                fixes.append(Fix(start, start + 1, capital, "Capital letter"))
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
    """Text as a PDF hands it out, as running prose: words split at the end of a line joined, lines run on.
    Chinese and Japanese lines run on with no space, as they are written."""
    text = re.sub(r"(?<=[^\W\d_])-\s*\n\s*(?=[a-zà-ÿ])", "", text)
    text = re.sub(f"(?<={CJK.pattern})[ \\t]*\\n\\s*(?={CJK.pattern})", lambda m: "" if not HANGUL.match(
        m.string[m.start() - 1]) else " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


# The end of a sentence: . ! ? and a space before a capital or a number, or a full-width 。！？ with no space.
SENTENCE_END = re.compile(r"[.!?…]+[\"'”»)]?\s+|[。！？]+[」』”）)]*\s*")


def sentences(text: str) -> list[str]:
    """The sentences of `text`, split after . ! ? and a capital, but not after an abbreviation."""
    text = clean_pdf_text(text)
    return [text[start:end] for start, end in sentence_spans(text)]


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Where the sentences of `text` are, as (start, end) indices: split as sentences() splits, on the text as
    it is, line breaks and all, so the positions are those of the original (a page's characters)."""
    found, start = [], 0
    for match in SENTENCE_END.finditer(text):
        if text[match.start()] in ".!?…":
            following = text[match.end():].lstrip("\"'“«(")[:1]
            if not following or not (following.isupper() or following.isdigit() or CJK.match(following)):
                continue  # "e.g. this": no capital after it, no new sentence; Cyrillic and Polish capitals count
            if text[match.start()] == "." and _abbreviation_before(text, match.start()):
                continue
        elif match.end() >= len(text):
            continue
        found.append((start, match.end()))
        start = match.end()
    found.append((start, len(text)))
    spans = []
    for first, end in found:
        while first < end and text[first].isspace():
            first += 1
        while end > first and text[end - 1].isspace():
            end -= 1
        if first < end:
            spans.append((first, end))
    return spans


def key_sentences(text: str, count: int | None = None, language: str | None = None) -> list[str]:
    """The sentences that carry most of `text`, in their order: those made of the words the text uses most,
    with a little weight for the first ones. `count` defaults to about a quarter, from 2 to 6."""
    all_sentences = sentences(text)
    if len(all_sentences) <= 2:
        return all_sentences
    language = language or detect_language(text, "en")
    stop = STOPWORDS.get(language, frozenset()) | ALL_STOPWORDS
    content = [[w for w in words(s) if (len(w) > 2 or CJK.match(w)) and w not in stop] for s in all_sentences]
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
        if not terms or length < (3 if CJK.search(sentence) else 4):
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


def study_notes(marks: list[Marked], title: str, by: str = "page", link=None) -> str:
    """Markdown notes of `marks`: grouped by page (in reading order) or by colour, with comments under each.
    With `link` (page from 1 -> URL), each page is a link to it."""

    def page_ref(number: int, label: str) -> str:
        return f"[{label}]({link(number)})" if link is not None else label

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
                lines.append(f"- {page_ref(mark.page + 1, f'p. {mark.page + 1}')}{kind}: {_quoted(mark.text)}")
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
                lines += [f"## {page_ref(page + 1, f'Page {page + 1}')}", ""]
            kind = MARK_NAMES.get(mark.style, mark.style.lower())
            lines.append(f"- **{colour_name(mark.color)} {kind}:** {_quoted(mark.text)}")
            if mark.comment.strip():
                lines.append(f"  - Note: {mark.comment.strip()}")
        lines.append("")
    return "\n".join(lines)


def _quoted(text: str) -> str:
    text = clean_pdf_text(text)
    return text if text else "(no text under it)"
