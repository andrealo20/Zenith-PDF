"""Comparing two versions of a PDF: words with their place, and what was added, removed or changed."""

import pypdfium2 as pdfium
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from conftest import _add_text
from zenith_pdf.core import compare
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.i18n import tr
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


def shown_colour(column, page, point):
    """The colour the column shows at a point of a page (display space), once the page is drawn."""
    for _ in range(200):
        QApplication.processEvents()
        if page in column._drawn:
            break
        QTest.qWait(10)
    image = column._pages[page].pixmap().toImage()
    scale = column.scale_ * image.devicePixelRatio()
    return image.pixelColor(round(point[0] * scale), round(point[1] * scale))


def test_the_pages_show_their_annotations(qt_app, tmp_path):
    """A note made of annotations on a blank page (pictures, text boxes, shapes) showed as a blank page."""
    old = Document(make_pdf(tmp_path / "old.pdf", [["One two three."]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [[]]))
    red = Shape(page=0, rect=(100.0, 100.0, 300.0, 200.0), color=(1.0, 0.0, 0.0), fill=(1.0, 0.0, 0.0))
    new.annotations.append(red)  # as read from its file
    window = CompareWindow(old, new)
    window.resize(1400, 900)
    window.show()
    colour = shown_colour(window.right, 0, (200, 150))
    assert colour.red() > 200 and colour.green() < 80, colour.name()
    window.close()


def test_the_open_document_shows_its_annotations_as_they_are_now(qt_app, tmp_path):
    old = Document(make_pdf(tmp_path / "old.pdf", [["One."]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [["One."]]))
    unsaved = Shape(page=0, rect=(100.0, 300.0, 300.0, 400.0), color=(0.0, 0.0, 1.0), fill=(0.0, 0.0, 1.0))
    window = CompareWindow(old, new, new_annotations=[unsaved])
    window.resize(1400, 900)
    window.show()
    colour = shown_colour(window.right, 0, (200, 350))
    assert colour.blue() > 200 and colour.red() < 80, colour.name()
    assert shown_colour(window.left, 0, (200, 350)).name() == "#ffffff"  # the older file never had it
    window.close()


def test_scans_are_not_said_to_read_the_same(qt_app, tmp_path):
    old = Document(make_pdf(tmp_path / "old.pdf", [[]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [[]]))
    window = CompareWindow(old, new)
    window.show()
    for _ in range(300):
        QApplication.processEvents()
        if window.status.text() != tr("Comparing…"):
            break
        QTest.qWait(10)
    assert "no text" in window.status.text() or "Neither" in window.status.text()
    window.close()


def test_closing_before_the_comparison_ends_leaves_nothing_to_run(qt_app, tmp_path):
    """Closed before the comparison finished, the window could get its result once it was gone: a crash."""
    old = Document(make_pdf(tmp_path / "old.pdf", [["One two."]]))
    new = Document(make_pdf(tmp_path / "new.pdf", [["One three."]]))
    window = CompareWindow(old, new)
    window.show()
    show = window._show
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert not isValid(window)  # gone
    show([], True)  # the result arriving late does nothing
    window.right._draw_visible()  # nor does a drawing that was still to come
