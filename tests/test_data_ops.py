"""Copy and paste, the markup library, drawing order on undo, the annotation dialogs and the annotations panel."""

import csv
import json
import shutil
from dataclasses import fields

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QUndoStack
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from zenith_pdf.core.annotations import (
    Attachment, FreeText, ImageStamp, Ink, Line, Link, Measurement, Note, Poly, Redaction, Shape, Tape, TextMarkup,
)
from zenith_pdf.core.document import Document, pdf_time
from zenith_pdf.core.store import AnnotationStore
from zenith_pdf.icons import contrast
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import clipboard, dialogs, messages
from zenith_pdf.ui.annotations_panel import AnnotationsPanel, icon_color, icon_name, when
from zenith_pdf.ui.commands import RemoveAnnotations
from zenith_pdf.ui.dialogs import MIXED, ApplyToPagesDialog, NewDocumentDialog, PropertiesDialog, WatermarkDialog
from zenith_pdf.ui.library import Library, LibraryPanel, preview

pytestmark = pytest.mark.usefixtures("qt_app")


@pytest.fixture(autouse=True)
def windows_deleted():
    """Delete the dialogs and panels a test made. Left for the end of the run, Qt may destroy
    them after the application itself, and the process then crashes on exit."""
    before = set(QApplication.topLevelWidgets())
    yield
    for widget in QApplication.topLevelWidgets():
        if widget not in before and widget.parent() is None:
            widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def png() -> bytes:
    image = QImage(8, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(30, 120, 200))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def every_kind(page: int = 0) -> list:
    """One annotation of each kind, with values away from the defaults."""
    common = dict(page=page, author="Andrea", subject="Exam", contents="check this", opacity=0.6)
    return [
        TextMarkup(style="Underline", quads=((10, 10, 50, 12),), **common),
        Shape(shape="Circle", rect=(20, 30, 40, 20), width=2.5, fill=(1, 1, 0), dash="dashed", border=False,
              highlight=True, color=(0.9, 0.1, 0.1), **common),
        Line(start=(5, 5), end=(80, 40), width=1.5, head_start="Diamond", head_end="ROpenArrow", dash="dotted",
             highlight=True, **common),
        Ink(strokes=(((1, 1), (5, 6), (9, 3)),), width=3.0, widths=((1.0, 3.0, 2.0),), highlighter=True, **common),
        FreeText(rect=(30, 40, 90, 30), font_size=13.5, fill=(1, 1, 1), border_width=1.0, border_color=(0.2, 0.3, 0.4),
                 callout=((5, 5), (20, 20)), rotation=1, **common),
        Note(position=(60, 60), icon="Key", **common),
        ImageStamp(rect=(5, 70, 40, 30), data=png(), equation=r"\frac{a}{b}", equation_size=16.0,
                   equation_color=(0.1, 0.2, 0.3), rotation=3, **common),
        Redaction(rects=((10, 100, 30, 10), (10, 112, 20, 10)), fill=(0.2, 0.2, 0.2), **common),
        Poly(points=((0, 0), (30, 5), (10, 30)), closed=False, width=1.5, dash="dashdot", head_end="Circle", **common),
        Tape(start=(10, 150), end=(90, 150), width=14, revealed=True, **common),
        Measurement(points=((0, 200), (72, 200)), mode="distance", scale=2.54 / 72, unit="cm", **common),
        Link(rect=(100, 10, 40, 12), uri="https://example.com", border=1.0, **common),
        Attachment(position=(120, 120), file_name="Audio note.wav", data=b"RIFF....WAVE", audio=True, **common),
    ]


def compared(annotation) -> dict:
    return {f.name: getattr(annotation, f.name) for f in fields(annotation)
            if f.compare and f.name not in ("id", "page", "locked")}


# Clipboard and library ----------------------------------------------------------------


def test_every_kind_survives_copy_and_paste_as_json():
    originals = every_kind()
    text = json.dumps(clipboard.to_records(originals))  # bytes travel as base64
    pasted = clipboard.from_records(json.loads(text), page=3, at=None)
    kept = [a for a in originals if not isinstance(a, TextMarkup)]  # markup belongs to its text
    assert [type(a) for a in pasted] == [type(a) for a in kept]
    for original, copy in zip(kept, pasted):
        assert copy.page == 3 and copy.id != original.id
        assert compared(copy) == compared(original.translated(12.0, 12.0)), type(original).__name__
    stamp = next(a for a in pasted if isinstance(a, ImageStamp))
    sound = next(a for a in pasted if isinstance(a, Attachment))
    assert stamp.data == png() and sound.data == b"RIFF....WAVE" and sound.digest == kept[-1].digest


def test_records_from_another_version_are_read_or_skipped():
    records = clipboard.to_records([Shape(page=0, rect=(0, 0, 10, 10))])
    records[0]["sparkle"] = True  # a field this version doesn't know
    records.append({"kind": "Hologram", "rect": [0, 0, 1, 1]})
    records.append({"kind": "Shape", "rect": "not a rectangle", "width": 2})
    records.append({"kind": "Attachment", "data": "!!! not base64 !!!"})
    made = clipboard.from_records(records, 0, (0.0, 0.0))
    assert [type(a) for a in made] == [Shape]


def test_the_library_keeps_and_draws_every_kind(tmp_path, monkeypatch):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    library = Library(settings)
    library.add("All of them", every_kind())
    [entry] = library.entries()
    assert len(clipboard.from_records(entry["records"], 0, (0.0, 0.0))) == len(every_kind()) - 1
    image = preview(entry["records"], 1.0).toImage()
    assert any(image.pixelColor(x, y) != QColor("white") for x in range(0, 52, 2) for y in range(0, 52, 2))

    panel = LibraryPanel(library, 1.0)
    monkeypatch.setattr(messages, "get_text", lambda *a, **k: "Renamed")
    panel._rename(0, "All of them")
    assert library.entries()[0]["name"] == "Renamed" and panel.list.item(0).text() == "Renamed"
    settings.markup_library = settings.markup_library + [{"name": 3}, "junk"]  # a hand-edited settings file
    assert [e["name"] for e in library.entries()] == ["Renamed"]


# Drawing order ------------------------------------------------------------------------


def test_undoing_a_removal_puts_annotations_back_in_their_place():
    red, green, blue, top = (Shape(page=0, rect=(i, i, 10, 10)) for i in range(4))
    store = AnnotationStore([red, green, blue, top])
    stack = QUndoStack()
    restacked = []
    store.reordered.connect(lambda: restacked.append(True))
    stack.push(RemoveAnnotations(store, [blue, red], "Delete"))
    assert store.order() == [green.id, top.id]
    stack.undo()
    assert store.order() == [red.id, green.id, blue.id, top.id] and restacked
    stack.redo()
    stack.push(RemoveAnnotations(store, [top], "Delete"))  # the top one: nothing to restack on undo
    restacked.clear()
    stack.undo()
    assert store.order() == [green.id, top.id] and not restacked
    stack.undo()
    assert store.order() == [red.id, green.id, blue.id, top.id]


# Dialogs ------------------------------------------------------------------------------


def test_properties_change_nothing_unless_asked_for_every_kind():
    for annotation in every_kind() + [Shape(page=0, rect=(0, 0, 9, 9), width=0.25, opacity=0.333)]:
        dialog = PropertiesDialog([annotation])
        assert dialog.changes() == {}, type(annotation).__name__
        assert callable(dialog.width) and callable(dialog.size) and callable(dialog.font)  # no shadowed methods
        assert dialog.width() > 0
        dialog.deleteLater()


def test_properties_keep_half_points_and_apply_only_what_changed():
    thin = Shape(page=0, rect=(0, 0, 9, 9), width=0.5, color=(1, 0, 0))
    thick = Line(page=0, start=(0, 0), end=(5, 5), width=2.5, color=(0, 0, 1), opacity=0.4)
    dialog = PropertiesDialog([thin])
    assert dialog.width_box.value() == 0.5 and dialog.width_box.text() == "0.5 pt"
    dialog.width_box.stepBy(2)
    assert dialog.changes() == {"width": 1.5}

    both = PropertiesDialog([thin, thick])
    assert both.color_button.color is MIXED and both.width_box.text() == "Mixed"
    assert both.locked_box.checkState() == Qt.CheckState.Unchecked and both.changes() == {}
    both.width_box.setValue(3.0)
    assert both.changes() == {"width": 3.0}  # colour and opacity keep each one's own
    both.opacity_slider.setValue(50)
    both.color_button.set_color((0.0, 1.0, 0.0))
    assert both.changes() == {"width": 3.0, "opacity": 0.5, "color": (0.0, 1.0, 0.0)}


def test_properties_offer_fill_once_and_never_none_for_a_redaction():
    shape = PropertiesDialog([Shape(page=0, rect=(0, 0, 9, 9), fill=(1, 1, 0))])
    assert shape.fill_button._none_label == "No fill"
    shape.fill_button.clear()
    assert shape.changes() == {"fill": None}
    redaction = PropertiesDialog([Redaction(page=0, rects=((0, 0, 5, 5),))])
    assert redaction.fill_button._none_label == ""
    mixed = PropertiesDialog([Shape(page=0, rect=(0, 0, 9, 9)), Redaction(page=0, rects=((0, 0, 5, 5),))])
    assert mixed.fill_button._none_label == ""
    buttons = [w for w in shape.findChildren(QWidget) if isinstance(w, dialogs.ColorButton)]
    assert len(buttons) == 2  # border and fill: one control each


def test_properties_mixed_lock_and_endings():
    locked = Line(page=0, start=(0, 0), end=(5, 5), locked=True, head_end="ClosedArrow")
    free = Poly(page=0, points=((0, 0), (5, 5)), head_end="Slash")
    dialog = PropertiesDialog([locked, free])
    assert dialog.locked_box.checkState() == Qt.CheckState.PartiallyChecked
    assert dialog.end_box.currentData() is MIXED and dialog.start_box.currentData() == "None"
    assert dialog.changes() == {}
    dialog.locked_box.click()
    dialog.end_box.setCurrentIndex(dialog.end_box.findData("Circle"))
    assert dialog.changes() == {"locked": True, "head_end": "Circle"}
    single = PropertiesDialog([FreeText(page=0, rect=(0, 0, 9, 9), contents="two\r\nlines", font_size=11.5)])
    assert single.font_box.text() == "11.5 pt" and single.changes() == {}
    single.contents_field.setPlainText("one line")
    single.subject_field.setText("  Important ")
    assert single.changes() == {"contents": "one line", "subject": "Important"}


def test_watermark_apply_to_pages_and_new_document_dialogs():
    watermark = WatermarkDialog()
    assert watermark.options()["font_size"] == 72 and watermark.options()["opacity"] == pytest.approx(0.22)
    watermark.text_field.setText("  ")
    assert watermark.options()["text"] == "DRAFT"
    watermark.grab()  # the preview paints

    apply = ApplyToPagesDialog(12, 0)
    apply.pages_box.setCurrentIndex(apply.pages_box.findData("range"))
    apply.range.setText("40-50")
    apply.press()
    assert apply.result() != QDialog.DialogCode.Accepted and apply.problem.isVisibleTo(apply)
    apply.range.setText("2-3, 12")
    apply.press()
    assert apply.result() == QDialog.DialogCode.Accepted and apply.pages() == [1, 2, 11]

    new = NewDocumentDialog({"name": "Notes", "kind": "dotted", "orientation": "landscape", "pages": 7,
                             "paper": "A5"})
    assert new.spec() == {"name": "Notes", "paper": "A5", "orientation": "landscape", "kind": "dotted", "pages": 7}
    lined = next(card for card in new.cards.buttons() if card.kind == "lined")
    lined.click()
    new.orientation.set_current(0)
    assert new.spec()["kind"] == "lined" and new.spec()["orientation"] == "portrait"
    assert [card.kind for card in new.cards.buttons()] == ["blank", "lined", "grid", "dotted"]
    new.grab()
    assert not NewDocumentDialog(editing_template=True).keep.isVisibleTo(new)
    thickness = PropertiesDialog([Shape(page=0, rect=(0, 0, 9, 9))]).width_box
    for box in (watermark.size_box, watermark.angle_box, new.count, thickness):
        assert box.lineEdit().textMargins().left() == dialogs.SPIN_INSET  # text lined up with the other fields


def test_dialogs_can_be_read_after_exec(tmp_path):
    """Callers read the values once exec() returns: the dialog must still be there."""
    cases = [(NewDocumentDialog(), NewDocumentDialog.spec), (WatermarkDialog(), WatermarkDialog.options),
             (PropertiesDialog([Shape(page=0, rect=(0, 0, 9, 9))]), PropertiesDialog.changes),
             (ApplyToPagesDialog(3, 0), ApplyToPagesDialog.pages)]
    for dialog, read in cases:
        QTimer.singleShot(0, dialog.press)
        assert dialog.exec() == QDialog.DialogCode.Accepted
        assert read(dialog) is not None
        dialog.deleteLater()

    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    count = len(settings.templates)
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().press())
    dialogs.edit_template(settings, None)
    assert len(settings.templates) == count + 1 and settings.templates[-1]["name"] == "New template"


# Annotations panel --------------------------------------------------------------------


@pytest.fixture
def document(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    document = Document(path)
    yield document
    document.close()


def test_panel_groups_by_page_and_keeps_links_aside(document, tmp_path):
    marks = every_kind(page=1) + [Link(page=0, rect=(5, 5, 20, 10), target=2), Shape(page=0, rect=(5, 40, 9, 9))]
    document.save(marks)
    document.restore(document.path.read_bytes())  # as saved: dates and all
    store = AnnotationStore(document.annotations)
    panel = AnnotationsPanel(document, store)
    texts = [panel.list.item(i).data(Qt.ItemDataRole.UserRole + 2) for i in range(panel.list.count())]
    assert texts[0] == 0 and texts.count(0) == 1 and texts.count(1) == 1  # one heading per page
    shown = [a for a in panel.ordered()]
    assert not any(isinstance(a, Link) for a in shown) and panel.links_chip.text() == "Links (2)"
    panel.links_chip.setChecked(True)
    assert sum(isinstance(a, Link) for a in panel.ordered()) == 2

    bare = next(a for a in document.annotations if isinstance(a, Shape) and a.page == 0)
    assert panel.summary(bare) == "" and panel.date_text(bare).startswith("Today")
    distance = next(a for a in document.annotations if isinstance(a, Measurement))
    assert panel.summary(distance) == "check this"
    for annotation in every_kind():
        assert icon_name(annotation) != "line-segment" or isinstance(annotation, Line)
    panel.grab()

    target = tmp_path / "out.csv"
    panel.write_export(str(target))
    with open(target, encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["Page", "Type", "Author", "Date", "Text", "Comment"]
    measured = next(row for row in rows if row[1] == "Distance")
    assert measured[4] == "2.54 cm"  # the value, not an empty cell
    panel.write_export(str(tmp_path / "out.txt"), plain=True)
    assert "2.54 cm" in (tmp_path / "out.txt").read_text(encoding="utf-8")


def test_panel_icons_stand_out_in_both_themes():
    previous = themes.current
    try:
        for name in ("dark", "light"):
            themes.choose(name)
            for color in ((0, 0, 0), (1, 1, 1), (1, 0.9, 0.1), (0.9, 0.1, 0.1)):
                shown = QColor(icon_color(Shape(page=0, rect=(0, 0, 5, 5), color=color)))
                assert contrast(shown, QColor(themes.current.surface)) >= 3.0, (name, color)
    finally:
        themes.apply(previous)


def test_pdf_dates_and_how_they_read():
    moment = pdf_time("D:20261016140512+02'00'")
    assert moment is not None and moment.utcoffset() is not None
    assert (moment.year, moment.month) == (2026, 10)
    assert pdf_time("D:2024") is not None and pdf_time("not a date") is None and pdf_time("D:20241399") is None
    now = pdf_time("D:20261016120000Z")
    assert when(now, now).startswith("Today") and when(pdf_time("D:20240305"), now) == "5 Mar 2024"
    assert when(pdf_time("D:20260305081500"), now) == "5 Mar 08:15"
