"""Flashcards from highlights, and study notes sent to an Obsidian vault (a made-up one, in a temporary folder)."""

import json

from zenith_pdf.core import flashcards, obsidian, writing
from zenith_pdf.core.writing import Marked
from zenith_pdf.ui.flashcards import StudyDialog
from zenith_pdf.ui.instance import page_link

YELLOW, GREEN, RED = (1.0, 0.85, 0.0), (0.2, 0.75, 0.3), (0.9, 0.1, 0.1)
PAGE = ("A solar cell turns sunlight into electricity. The open-circuit voltage falls as the cell warms up. "
        "Batteries store the energy for the night.")


def test_cards_ask_the_comment_or_hide_the_marked_words():
    marks = [
        Marked(0, "Highlight", YELLOW, "sunlight into electricity", top=10),
        Marked(0, "Highlight", GREEN, "open-circuit voltage", "What falls as the cell warms up?", top=20),
        Marked(0, "Underline", YELLOW, "Batteries store the energy for the night.", top=30),
        Marked(0, "StrikeOut", RED, "solar", top=5),
    ]
    cards = flashcards.make_cards(marks, {0: PAGE})
    assert [(c.front, c.back) for c in cards] == [
        ("A solar cell turns _____.", "sunlight into electricity"),
        ("What falls as the cell warms up?", "open-circuit voltage"),
        ("_____ store the energy for the night.", "Batteries"),  # a whole sentence: its key word is asked
    ]
    yellow = flashcards.make_cards(marks, {0: PAGE}, colours={"Yellow"})
    assert [c.colour for c in yellow] == ["Yellow", "Yellow"]
    text = flashcards.anki_text(cards, "Zenith PDF::Lecture 4", "Lecture 4")
    lines = text.splitlines()
    assert lines[:5] == ["#separator:tab", "#html:true", "#notetype:Basic", "#deck:Zenith PDF::Lecture 4",
                         "#tags column:3"]
    assert lines[5].split("\t")[0] == "A solar cell turns _____." and "p. 1" in lines[5]
    assert len(lines) == 8 and all(line.count("\t") == 2 for line in lines[5:])


def test_studying_brings_a_missed_card_back(qt_app):
    cards = [flashcards.Card(f"Q{n}", f"A{n}", 0, "Yellow") for n in range(3)]
    dialog = StudyDialog(cards, shuffle=False)
    assert dialog.front.text() == "Q0" and not dialog.back.isVisibleTo(dialog)
    dialog.reveal()
    assert dialog.back.isVisibleTo(dialog)
    dialog.answer(False)  # Q0 comes back after the others
    for _ in range(3):
        dialog.reveal()
        dialog.answer(True)
    assert dialog.right == 2 and not dialog.queue and "2 of 3" in dialog.front.text()
    dialog.deleteLater()


def test_vaults_come_from_obsidians_own_list(tmp_path):
    older, newer, gone = tmp_path / "Old vault", tmp_path / "Thesis", tmp_path / "Deleted"
    older.mkdir()
    newer.mkdir()
    config = tmp_path / "obsidian.json"
    config.write_text(json.dumps({"vaults": {
        "a": {"path": str(older), "ts": 1},
        "b": {"path": str(newer), "ts": 5, "open": True},
        "c": {"path": str(gone), "ts": 9},
    }}), encoding="utf-8")
    assert obsidian.vaults([config, tmp_path / "missing.json"]) == [newer, older]
    assert obsidian.note_name(tmp_path / "Lecture 4: PV [draft].pdf") == "Lecture 4 PV draft.md"


def test_notes_in_the_vault_keep_what_the_user_wrote(tmp_path):
    vault = tmp_path / "vault"
    document = tmp_path / "Photovoltaic Systems - Lecture 4.pdf"
    marks = [Marked(2, "Highlight", YELLOW, "maximum power point", "MPP", top=40)]
    notes = writing.study_notes(marks, "Study notes", "page", link=lambda page: page_link(document, page))
    assert "## [Page 3](zenith-pdf://open?file=" in notes and "&page=3)" in notes
    note = obsidian.write_note(vault, "Zenith PDF", document, notes, page_link)
    assert note == vault / "Zenith PDF" / "Photovoltaic Systems - Lecture 4.md"
    first = note.read_text(encoding="utf-8")
    assert first.startswith("---\nsource: ") and obsidian.START in first and "maximum power point" in first
    note.write_text(first.replace(obsidian.START, "My own line.\n" + obsidian.START) + "\nAfterwards, mine too.\n",
                    encoding="utf-8")
    again = writing.study_notes([Marked(2, "Highlight", YELLOW, "fill factor", top=50)], "Study notes")
    obsidian.write_note(vault, "Zenith PDF", document, again, page_link)
    text = note.read_text(encoding="utf-8")
    assert "My own line." in text and "Afterwards, mine too." in text
    assert "fill factor" in text and "maximum power point" not in text  # Zenith PDF's part is the new one
    assert obsidian.open_uri(note).startswith("obsidian://open?path=")
