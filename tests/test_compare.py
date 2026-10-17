"""Comparing two versions of a PDF: words with their place, and what was added, removed or changed."""

import pypdfium2 as pdfium
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import _add_text
from zenith_pdf.core import compare
from zenith_pdf.core.document import Document
from zenith_pdf.ui.compare import CompareWindow


def make_pdf(path, pages):
    pdf = pdfium.PdfDocument.new()
    for lines in pages:
        page = pdf.new_page(612, 792)
        for number, line in enumerate(lines):
            _add_text(pdf, page, line, 72, 700 - 30 * number)
        page.gen_content()
        page.close()
    pdf.save(str(path))
    pdf.close()
    return path


def test_words_know_their_page_and_place(tmp_path):
    document = Document(make_pdf(tmp_path / "a.pdf", [["Solar cells turn light"], ["into current."]]))
    words = compare.words(document)
    assert [w.text for w in words] == ["Solar", "cells", "turn", "light", "into", "current."]
    assert [w.page for w in words] == [0, 0, 0, 0, 1, 1]
    x, y, w, h = words[0].rect
    assert 70 <= x <= 75 and 70 <= y <= 95 and w > 20 and h > 5  # display y: about 792 - 700, near the top
    assert words[1].rect[0] > words[0].rect[0] + words[0].rect[2]  # each word after the one before
    document.close()


def test_added_removed_and_changed_words(tmp_path):
    old = Document(make_pdf(tmp_path / "old.pdf", [["The panel gives 300 W at noon."], ["Keep it clean."]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [["The panel gives 320 W at noon in summer."], ["Keep it."]]))
    changes = compare.compare(compare.words(old), compare.words(new))
    found = [(c.kind, [w.text for w in c.old], [w.text for w in c.new]) for c in changes]
    assert found[:2] == [("changed", ["300"], ["320"]), ("changed", ["noon."], ["noon", "in", "summer."])]
    assert "clean." in [w.text for c in changes[2:] for w in c.old]  # what went from page 2
    assert changes[0].page == 0 and changes[-1].page == 1
    assert compare.counts(changes)["changed"] >= 2
    assert compare.compare(compare.words(old), compare.words(old)) == []
    assert changes[0].summary() == "300  →  320"
    old.close()
    new.close()


def test_the_window_lights_the_changes_on_both_sides(qt_app, tmp_path):
    old = Document(make_pdf(tmp_path / "old.pdf", [["One two three."], ["Four five."]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [["One two three four."], ["Five."]]))
    window = CompareWindow(old, new)
    window.resize(1400, 420)  # short: each side scrolls through its pages
    window.show()
    for _ in range(300):
        QApplication.processEvents()
        if window.changes:
            break
        QTest.qWait(10)
    assert window.changes and window.list.count() == len(window.changes)
    assert "added" in window.status.text() or "changed" in window.status.text()
    window.step(1)
    assert window.list.currentRow() == 0
    for _ in range(100):  # the columns lay their pages out after each resize: wait until they have
        QTest.qWait(10)
        bar = window.right.verticalScrollBar()
        if len(window.right.tops) > 1 and bar.maximum() >= window.right.tops[1]:
            break
    QTest.qWait(50)
    window.right.verticalScrollBar().setValue(round(window.right.tops[1]))
    QApplication.processEvents()
    assert window.right.spot()[0] == 1 and window.left.spot()[0] == 1  # the older version follows to the same page
    window.close()
