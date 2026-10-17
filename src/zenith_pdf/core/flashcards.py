"""Flashcards from the marks on a document: to study in the app, or to take to Anki.

A mark with a comment is a question and its answer: the comment asks, the marked text answers. A mark
without one becomes a gap: its sentence with the marked words blanked out. A mark that is a whole sentence
blanks its longest word that says something, so the card still has a context to answer from.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .writing import ALL_STOPWORDS, Marked, clean_pdf_text, colour_name, sentences

GAP = "_____"
WHOLE = 0.8  # a mark this much of its sentence counts as the whole sentence


@dataclass(frozen=True)
class Card:
    front: str
    back: str
    page: int  # from 0
    colour: str  # the everyday name of the mark's colour


def _sentence_around(text: str, page_text: str) -> str:
    """The sentence of `page_text` that holds `text`, or "" when it can't be found."""
    wanted = " ".join(text.split()).lower()
    if not wanted:
        return ""
    for sentence in sentences(page_text):
        if wanted in " ".join(sentence.split()).lower():
            return sentence
    return ""


def _blank(sentence: str, answer: str) -> str:
    """`sentence` with `answer` replaced by the gap, matched without regard to case and spacing."""
    pattern = r"\s+".join(re.escape(word) for word in answer.split())
    return re.sub(pattern, GAP, sentence, count=1, flags=re.IGNORECASE)


def _key_word(sentence: str) -> str:
    """The word of `sentence` most worth asking for: the longest that is not a little word."""
    words = [w.strip(".,;:!?()[]\"'“”«»") for w in sentence.split()]
    useful = [w for w in words if len(w) > 3 and w.lower() not in ALL_STOPWORDS and any(c.isalpha() for c in w)]
    return max(useful, key=len, default="")


def card_for(mark: Marked, page_text: str) -> Card | None:
    answer = clean_pdf_text(mark.text)
    if not answer:
        return None
    colour = colour_name(mark.color)
    if mark.comment.strip():
        return Card(mark.comment.strip(), answer, mark.page, colour)
    sentence = _sentence_around(answer, page_text) or answer
    if len(answer) >= WHOLE * len(sentence):
        word = _key_word(sentence)
        if not word:
            return None
        return Card(_blank(sentence, word), word, mark.page, colour)
    return Card(_blank(sentence, answer), answer, mark.page, colour)


def make_cards(marks: list[Marked], page_texts: dict[int, str], colours: set[str] | None = None) -> list[Card]:
    """The cards of the marks in reading order, of the chosen colours (all when None). Strikethroughs ask
    nothing, and are left out."""
    cards = []
    for mark in sorted(marks, key=lambda m: (m.page, m.top)):
        if mark.style == "StrikeOut" or (colours is not None and colour_name(mark.color) not in colours):
            continue
        card = card_for(mark, page_texts.get(mark.page, ""))
        if card is not None:
            cards.append(card)
    return cards


def anki_text(cards: list[Card], deck: str, source: str) -> str:
    """Cards as a file Anki imports (File > Import): Basic notes, front and back, in the deck `deck`."""
    def cell(text: str) -> str:
        return html.escape(text).replace("\t", " ").replace("\n", "<br>")

    tag = re.sub(r"[^\w-]+", "_", source).strip("_") or "zenith-pdf"
    lines = ["#separator:tab", "#html:true", "#notetype:Basic", f"#deck:{deck}", "#tags column:3"]
    for card in cards:
        back = f"{cell(card.back)}<br><br><small>{cell(source)}, p. {card.page + 1}</small>"
        lines.append(f"{cell(card.front)}\t{back}\tzenith-pdf {tag}")
    return "\n".join(lines) + "\n"
