"""Annotation dictionaries saved over what other apps or earlier saves left in them: nulls in /Annots, a group
whose first member went, an author cleared, rich text that no longer matches."""

from dataclasses import replace

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from zenith_pdf.core.annotations import FreeText, Note, Shape
from zenith_pdf.core.document import Document

pytestmark = pytest.mark.usefixtures("qt_app")


def blank_pdf(path, annots=None):
    """One 612 x 792 page; `annots(pdf)` gives its /Annots."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    if annots is not None:
        pdf.pages[0].obj.Annots = Array(annots(pdf))
    pdf.save(path)
    pdf.close()
    return path


def test_a_null_in_annots_does_not_stop_saving(tmp_path):
    def annots(pdf):
        link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[72, 40, 300, 60],
                                            A=Dictionary(S=Name.URI, URI=String("https://example.org/"))))
        return [None, link]

    path = blank_pdf(tmp_path / "null.pdf", annots)
    document = Document(path)
    shape = Shape(page=0, rect=(100, 100, 50, 50))
    document.save(document.annotations + [shape])
    document.close()

    with pikepdf.open(path) as pdf:
        kept = list(pdf.pages[0].obj.Annots)
        assert all(isinstance(a, Dictionary) for a in kept)
        assert [str(a.Subtype) for a in kept] == ["/Link", "/Square"]
    reopened = Document(path)
    assert shape.id in {a.id for a in reopened.annotations}
    reopened.close()


def test_deleting_a_groups_first_member_leaves_nothing_of_it_in_the_file(tmp_path):
    path = blank_pdf(tmp_path / "group.pdf")
    head = FreeText(page=0, rect=(100, 100, 200, 40), contents="IBAN IT60X SECRET", group="g1")
    mate = Shape(page=0, rect=(100, 200, 50, 50), group="g1")
    document = Document(path)
    document.save([head, mate])
    document.close()

    document = Document(path)
    document.save([a for a in document.annotations if a.id != head.id])  # the shape is left as it was
    document.close()

    with pikepdf.open(path) as pdf:
        [square] = pdf.pages[0].obj.Annots
        assert "/IRT" not in square and "/RT" not in square
        assert not any(isinstance(o, Dictionary) and "SECRET" in str(o.get("/Contents", "")) for o in pdf.objects)


def test_clearing_the_author_removes_it_from_the_file(tmp_path):
    path = blank_pdf(tmp_path / "author.pdf")
    document = Document(path)
    document.save([Note(page=0, position=(100, 100), contents="x", author="Mario Rossi")])
    document.close()

    document = Document(path)
    [note] = document.annotations
    document.save([replace(note, author="")])
    document.close()

    with pikepdf.open(path) as pdf:
        assert "/T" not in pdf.pages[0].obj.Annots[0]
    reopened = Document(path)
    assert reopened.annotations[0].author == ""
    reopened.close()


def test_rich_text_goes_when_the_text_changes_and_stays_when_it_only_moves(tmp_path):
    def rich(text):
        return String(f'<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml"><p>{text}</p></body>')

    def annots(pdf):
        def note(name, y, text):
            return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=[100, y, 120, y + 20],
                                                Name=Name.Comment, Contents=String(text), RC=rich(text),
                                                NM=String(name), F=4))
        return [note("edited", 700, "Pay 500 EUR"), note("moved", 600, "Call back")]

    path = blank_pdf(tmp_path / "rich.pdf", annots)
    document = Document(path)
    notes = {a.id: a for a in document.annotations}
    x, y = notes["moved"].position
    document.save([replace(notes["edited"], contents="Pay 50 EUR"), replace(notes["moved"], position=(x + 30, y))])
    document.close()

    with pikepdf.open(path) as pdf:
        saved = {str(a.NM): a for a in pdf.pages[0].obj.Annots}
        assert str(saved["edited"].Contents) == "Pay 50 EUR" and "/RC" not in saved["edited"]
        assert "Call back" in str(saved["moved"].RC)
