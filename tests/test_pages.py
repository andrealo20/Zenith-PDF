"""Page operations: annotations must travel with their pages."""

import io
import shutil

import pikepdf
import pytest

from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.document import Document
from zenith_pdf.core.geometry import PageGeometry

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture
def document(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    document = Document(path)
    yield document
    document.close()


def page_texts(document):
    return [" ".join(document.text_span(i, 0, 12)[1].split()) for i in range(document.page_count)]


def test_duplicate_copies_the_page_and_its_annotations(document):
    shape = Shape(page=1, rect=(50, 60, 100, 80))
    document.duplicate([shape], 1)
    assert document.page_count == 5
    assert page_texts(document)[1:3] == ["Page 2 needle"] * 2
    copies = sorted(document.annotations, key=lambda a: a.page)
    assert [a.page for a in copies] == [1, 2]
    assert copies[0].id == shape.id and copies[1].id != shape.id
    assert copies[1].rect == pytest.approx(shape.rect, abs=0.01)


def test_delete_drops_the_page_and_shifts_the_rest(document):
    kept = Shape(page=2, rect=(10, 10, 20, 20))
    gone = Shape(page=1, rect=(10, 10, 20, 20))
    document.delete([kept, gone], [1])
    assert document.page_count == 3
    assert page_texts(document) == ["Page 1 needle", "Page 3 needle", "Page 4 needle"]
    assert [(a.id, a.page) for a in document.annotations] == [(kept.id, 1)]
    with pytest.raises(ValueError):
        document.delete(document.annotations, [0, 1, 2])
    assert document.page_count == 3  # a refused edit leaves everything as it was


def test_rotate_turns_annotations_with_the_page(document):
    shape = Shape(page=0, rect=(100, 150, 200, 50))
    before = document.geometry(0)
    pdf_rect = before.to_pdf(100, 150) + before.to_pdf(300, 200)
    document.rotate([shape], [0], 90)
    after = document.geometry(0)
    assert after.rotation == 90
    assert document.page_size(0) == pytest.approx((792, 612))
    [turned] = document.annotations
    expected = PageGeometry(after.box, 90).rect_to_display((min(pdf_rect[0], pdf_rect[2]), min(pdf_rect[1], pdf_rect[3]),
                                                            max(pdf_rect[0], pdf_rect[2]), max(pdf_rect[1], pdf_rect[3])))
    assert turned.rect == pytest.approx(expected, abs=0.01)


def test_move_reorders_pages(document):
    document.move([], [2, 3], 0)
    assert page_texts(document) == ["Page 3 needle", "Page 4 needle", "Page 1 needle", "Page 2 needle"]
    document.move([], [0], 4)
    assert page_texts(document) == ["Page 4 needle", "Page 1 needle", "Page 2 needle", "Page 3 needle"]


def test_templates_extend_and_watermark(document):
    document.insert_template([], 1, "grid")
    assert document.page_count == 5
    assert document.page_size(1) == pytest.approx(document.page_size(0))
    width = document.page_size(2)[0]
    document.extend_margin([], 2, 0.5)
    assert document.page_size(2)[0] == pytest.approx(width * 1.5)
    document.watermark([], [0], "BOZZA")
    assert "BOZZA" in document.text_in(0, [(0, 0, *document.page_size(0))])


def test_snapshot_and_restore_round_trip(document):
    shape = Shape(page=3, rect=(20, 30, 40, 50))
    data = document.snapshot([shape])
    document.delete([shape], [3])
    assert document.annotations == []
    document.restore(data)
    assert [a.id for a in document.annotations] == [shape.id]


def test_ocr_makes_an_image_only_page_searchable(document):
    from zenith_pdf.core import ocr

    if not ocr.available():
        pytest.skip("Windows text recognition is not available here")
    # Turn page 1 into a picture of itself: its text is no longer text.
    data, width, height, stride = document.render_region(0, 2.0)
    from PySide6.QtGui import QImage

    image = QImage(data, width, height, stride, QImage.Format.Format_RGB32).convertToFormat(QImage.Format.Format_RGB888)
    rgb = b"".join(bytes(image.constScanLine(y))[: width * 3] for y in range(height))
    document.insert_image([], 0, rgb, width, height)
    assert list(document.iter_search("needle"))[0].page == 1  # the picture has no text

    document.add_text_layer([], 0, ocr.recognize_page(document, 0))
    assert [hit.page for hit in document.iter_search("needle")][:2] == [0, 1]


def test_page_edits_keep_encryption(sample_pdf, tmp_path):
    path = tmp_path / "secret.pdf"
    with pikepdf.open(sample_pdf) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user="secret"))
    document = Document(path, password="secret")
    document.rotate([], [0], 90)
    document.save([])
    document.close()
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(path)


def opens_with(path) -> str:
    for password in ("", "secret", "new", "owner"):
        try:
            with pikepdf.open(path, password=password) as pdf:
                return password if pdf.is_encrypted else "no password"
        except pikepdf.PasswordError:
            continue
    return "nothing"


@pytest.fixture
def secret(sample_pdf, tmp_path):
    path = tmp_path / "secret.pdf"
    with pikepdf.open(sample_pdf) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user="secret"))
    return path


@pytest.mark.parametrize("security", [False, pikepdf.Encryption(owner="x", user="new")])
def test_undoing_a_password_change_after_a_page_edit(secret, security):
    """Remove or set a password, rotate, undo both, save: the file is as it was."""
    document = Document(secret, password="secret")
    shape = Shape(page=1, rect=(40, 40, 80, 60))
    before_security = document.security
    document.set_security(security, "new")
    before = document.snapshot([shape])
    document.rotate([shape], [0], 90)
    document.restore(before)  # undo the rotation
    document.set_security(before_security)  # undo the password change
    document.save(document.annotations)
    document.close()
    assert opens_with(secret) == "secret"
    with pikepdf.open(secret, password="owner") as pdf:  # the owner password nobody typed survives too
        assert pdf.owner_password_matched


def test_a_new_password_reaches_the_file_after_page_edits(secret):
    document = Document(secret, password="secret")
    document.set_security(pikepdf.Encryption(owner="x", user="new"), "new")
    document.rotate([], [0], 90)
    document.delete([], [3])
    document.save([])
    document.close()
    assert opens_with(secret) == "new"


def test_encryption_survives_bytes_without_it(secret):
    """Bytes from elsewhere (a background job) may come back unencrypted: saving still encrypts."""
    document = Document(secret, password="secret")
    with pikepdf.open(io.BytesIO(document.data), password="secret") as pdf:
        plain = io.BytesIO()
        pdf.save(plain)
    document.restore(plain.getvalue())
    document.save([])
    document.close()
    assert opens_with(secret) == "secret"


def test_copied_annotations_keep_their_ids_through_undo_and_redo(document):
    shape = Shape(page=0, rect=(100, 100, 60, 40))
    before = document.snapshot([shape])
    document.duplicate([shape], 0)
    after = document.data
    ids = sorted(a.id for a in document.annotations)
    document.restore(before)  # undo
    document.restore(after)  # redo
    assert sorted(a.id for a in document.annotations) == ids and len(ids) == 2
    document.restore(before)
    document.restore(after)
    assert sorted(a.id for a in document.annotations) == ids


def test_snapshots_share_bytes_until_something_changes(document):
    shape = Shape(page=0, rect=(10, 10, 20, 20))
    first = document.snapshot([shape])
    assert document.snapshot([shape]) is first
    document.rotate([shape], [1], 90)
    assert document.snapshot(document.annotations) is document.data  # after a page edit: the loaded bytes
    moved = shape.__class__(page=0, rect=(30, 10, 20, 20), id=shape.id)
    changed = document.snapshot([moved])
    assert changed is not document.data
    document.restore(changed)
    assert [a.rect for a in document.annotations] == [pytest.approx((30, 10, 20, 20), abs=0.01)]
    assert not hasattr(document, "optimize")


def test_edit_pages_takes_the_snapshot_it_is_given(document, monkeypatch):
    before = document.snapshot([])
    calls = []
    original = document._serialize
    monkeypatch.setattr(document, "_serialize", lambda: calls.append(1) or original())
    document.edit_pages([], lambda pdf: pdf.pages[0].rotate(90, relative=True), before)
    assert len(calls) == 1  # only the result: the given snapshot is the one kept for a failure

    def broken(pdf):
        pdf.pages[1].rotate(90, relative=True)
        raise RuntimeError("no")

    current = document.data
    with pytest.raises(RuntimeError):
        document.edit_pages([], broken, current)
    assert document.geometry(1).rotation == 90  # untouched: page 2 was already turned 90 in the sample


def link_targets(document, page):
    from zenith_pdf.core.annotations import Link

    return sorted((a.target, a.uri) for a in document.annotations if isinstance(a, Link) and a.page == page)


@pytest.fixture
def linked(document, tmp_path):
    from zenith_pdf.core.annotations import Link

    document.save([Link(page=0, rect=(100, 100, 80, 20), target=2), Link(page=0, rect=(100, 140, 80, 20), target=0),
                   Link(page=0, rect=(100, 180, 80, 20), uri="https://example.com")])
    reopened = Document(document.path)
    yield reopened
    reopened.close()


def test_a_duplicated_page_keeps_its_links(linked):
    linked.duplicate(linked.annotations, 0)
    # The copy is page 2: its link to page 3 now goes to page 4 (the same page), its link to itself to itself.
    assert link_targets(linked, 0) == [(-1, "https://example.com"), (0, ""), (3, "")]
    assert link_targets(linked, 1) == [(-1, "https://example.com"), (1, ""), (3, "")]
    with pikepdf.open(io.BytesIO(linked.data)) as pdf:
        pages_in_file = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and o.get("/Type") == "/Page"]
        assert len(pages_in_file) == len(pdf.pages)  # no stray copy of the target page


def test_copied_pages_find_their_links_when_pasted_back(linked):
    copy = linked.copy_pages(linked.annotations, [0])
    linked.insert_pdf(linked.annotations, 4, copy)
    assert link_targets(linked, 4) == [(-1, "https://example.com"), (2, ""), (4, "")]
    linked.rotate(linked.annotations, [1], 90)  # the pages changed: an old copy's page numbers no longer hold
    linked.insert_pdf(linked.annotations, 5, copy)
    assert link_targets(linked, 5) == [(-1, "https://example.com"), (5, "")]


def test_extracted_pages_link_to_each_other_and_drop_dead_links(linked, tmp_path):
    out = tmp_path / "part.pdf"
    out.write_bytes(linked.extract_pages(linked.annotations, [0, 2]))
    part = Document(out)
    assert link_targets(part, 0) == [(-1, "https://example.com"), (0, ""), (1, "")]
    part.close()
    out.write_bytes(linked.extract_pages(linked.annotations, [0]))
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages[0].obj.Annots) == 2  # the link to page 3 led nowhere and is gone
        assert "/ZenithOrigin" not in pdf.Root


def test_builder_keeps_links_between_copied_pages(linked, tmp_path):
    from zenith_pdf.core.builder import Entry, Source, build

    linked.save(linked.annotations)
    sources = {"a": Source("a", linked.path)}
    result = tmp_path / "built.pdf"
    result.write_bytes(build(sources, [Entry("a", 2), Entry("a", 0), Entry("a", 1, rotation=90)]))
    built = Document(result)
    assert link_targets(built, 1) == [(-1, "https://example.com"), (0, ""), (1, "")]
    built.close()
    result.write_bytes(build(sources, [Entry("a", 0)]))
    built = Document(result)
    assert link_targets(built, 0) == [(-1, "https://example.com"), (0, "")]
    built.close()
