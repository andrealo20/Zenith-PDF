"""Check that the desktop reads the notes the iPad wrote, through its real open path.

    python ipad/tools/check_desktop_reads.py <exports> [--png <folder>]

1. Every <name>.pdf in <exports> with an iPad export <name>.json (from testExportForDesktop): each stroke is one
   Ink with its points, widths, colour, opacity and highlighter flag; each text box a FreeText with its text, font
   and rect; each picture an ImageStamp.
2. desktop-ink-ipad.pdf (the desktop fixture opened and saved by the iPad) against the fixture itself: ink as
   polylines, every other annotation equal as a model value, except kinds listed in known_losses.json (warnings);
   the text box the iPad edited reads back with the iPad's font.
3. Every iPad-written file carries /ZenithNoteID in its document information.
4. --png: every page of every PDF in <exports>, and of the fixture, rendered at 2x to <folder>/desktop-<file>-<page>.png.

Exit 1 on any failure, printing what differed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from zenith_pdf.core import images  # noqa: E402
from zenith_pdf.core.annotations import FreeText, ImageStamp, Ink  # noqa: E402
from zenith_pdf.core.document import Document  # noqa: E402

IPAD = Path(__file__).resolve().parents[1]
FIXTURE = IPAD / "ZenithPDF" / "Resources" / "Demo" / "desktop-ink.pdf"
FIXTURE_JSON = IPAD / "ZenithPDFTests" / "Fixtures" / "desktop-ink.json"
KNOWN_LOSSES = IPAD / "tools" / "known_losses.json"
EXPORT_FORMAT = "zenith-ipad-export-1"


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def fail(self, where: str, what: str) -> None:
        self.failures.append(f"{where}: {what}")
        print(f"FAIL {where}: {what}")

    def check(self, condition: bool, where: str, what: str) -> bool:
        if not condition:
            self.fail(where, what)
        return condition


def close(a: float, b: float, tolerance: float) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def segment_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def nearest(p, line, widths):
    """Distance from p to the polyline and the width there, interpolated along the closest segment."""
    if len(line) == 1:
        return math.hypot(p[0] - line[0][0], p[1] - line[0][1]), widths[0]
    best, width = math.inf, widths[0]
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        d = segment_distance(p, a, b)
        if d < best:
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = dx * dx + dy * dy
            t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
            best, width = d, widths[i] + (widths[i + 1] - widths[i]) * t
    return best, width


def same_polyline(a, wa, b, wb, distance: float, width: float) -> str | None:
    """None when every point of each stroke lies within `distance` of the other and widths agree; else why not."""
    for line, ws, other, other_ws in ((a, wa, b, wb), (b, wb, a, wa)):
        for p, w in zip(line, ws):
            d, there = nearest(p, other, other_ws)
            if d > distance:
                return f"point {p} is {d:.3f} pt from the other stroke"
            if abs(w - there) > width:
                return f"width {w:.3f} at {p}, {there:.3f} on the other stroke"
    return None


def similar(a, b, tolerance: float = 0.01) -> bool:
    """Model values equal, floats within `tolerance`."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and close(a, b, tolerance)
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(similar(x, y, tolerance) for x, y in zip(a, b))
    return a == b


def differences(a, b) -> list[str]:
    """The fields in which two annotations differ, ids ignored; pictures compared by their size in pixels."""
    if type(a) is not type(b):
        return [f"kind {type(a).__name__} became {type(b).__name__}"]
    found = []
    for field in dataclasses.fields(a):
        if field.name == "id" or field.name == "data":
            continue
        x, y = getattr(a, field.name), getattr(b, field.name)
        if field.name == "digest" and isinstance(a, ImageStamp):
            if x != y and images.size(a.data) != images.size(b.data):
                found.append(f"picture {images.size(a.data)} became {images.size(b.data)}")
            continue
        if not similar(x, y):
            found.append(f"{field.name} {x!r} became {y!r}")
    return found


def note_id(path: Path) -> str | None:
    with pikepdf.open(path) as pdf:
        value = pdf.docinfo.get("/ZenithNoteID")
        return str(value) if value is not None else None


def check_export(pdf_path: Path, expected: dict, report: Report) -> None:
    where = pdf_path.name
    document = Document(pdf_path)
    by_id = {a.id: a for a in document.annotations}
    for number, stroke in enumerate(expected.get("strokes", [])):
        label = f"{where} stroke {number} ({stroke['id']})"
        ink = by_id.get(stroke["id"])
        if not report.check(isinstance(ink, Ink), label, f"read as {type(ink).__name__}, not Ink"):
            continue
        report.check(ink.page == stroke["page"], label, f"on page {ink.page}, not {stroke['page']}")
        if not report.check(len(ink.strokes) == len(stroke["paths"]), label,
                            f"{len(ink.strokes)} paths, expected {len(stroke['paths'])}"):
            continue
        report.check(bool(ink.widths), label, "no widths read (/ZenithWidths missing or the wrong shape)")
        for path_index, (read, want) in enumerate(zip(ink.strokes, stroke["paths"])):
            if not report.check(len(read) == len(want), label, f"path {path_index}: {len(read)} points, expected {len(want)}"):
                continue
            far = max((math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(read, want)), default=0.0)
            report.check(far <= 0.1, label, f"path {path_index}: a point is {far:.3f} pt off")
            if ink.widths:
                widths = ink.widths[path_index]
                report.check(len(widths) == len(read), label, f"path {path_index}: {len(widths)} widths for {len(read)} points")
                off = max((abs(w - v) for w, v in zip(widths, stroke["widths"][path_index])), default=0.0)
                report.check(off <= 0.01, label, f"path {path_index}: a width is {off:.3f} off")
        report.check(similar(ink.color, stroke["color"], 1 / 255), label, f"colour {ink.color}, expected {stroke['color']}")
        report.check(close(ink.opacity, stroke["opacity"], 0.01), label, f"opacity {ink.opacity}, expected {stroke['opacity']}")
        report.check(ink.highlighter == stroke["highlighter"], label, f"highlighter {ink.highlighter}")
    boxes = [a for a in document.annotations if isinstance(a, FreeText)]
    for box in expected.get("textBoxes", []):
        label = f"{where} text box {box['contents']!r}"
        found = [a for a in boxes if a.contents == box["contents"] and a.page == box["page"]]
        if not report.check(len(found) == 1, label, f"{len(found)} FreeText with this text"):
            continue
        read = found[0]
        report.check(read.font == box["font"], label, f"font {read.font}, expected {box['font']}")
        report.check(read.bold == box["bold"] and read.italic == box["italic"], label,
                     f"bold {read.bold} italic {read.italic}, expected {box['bold']} {box['italic']}")
        report.check(close(read.font_size, box["size"], 0.01), label, f"size {read.font_size}, expected {box['size']}")
        report.check(similar(read.rect, box["rect"], 1.0), label, f"rect {read.rect}, expected {box['rect']}")
        report.check(read.fill is None, label, f"filled with {read.fill}")
    pictures = [a for a in document.annotations if isinstance(a, ImageStamp)]
    for picture in expected.get("pictures", []):
        label = f"{where} picture at {picture['rect']}"
        found = [a for a in pictures if a.page == picture["page"] and similar(a.rect, picture["rect"], 1.0)]
        report.check(len(found) == 1, label, f"{len(found)} ImageStamp there; pictures read: {[a.rect for a in pictures]}")
    document.close()
    wanted = expected.get("noteID")
    found_id = note_id(pdf_path)
    report.check(found_id is not None and (wanted is None or found_id == wanted), where,
                 f"/ZenithNoteID is {found_id!r}, expected {wanted!r}")


def check_round_trip(original_path: Path, ipad_path: Path, edit: dict, losses: dict[str, str], report: Report) -> None:
    where = ipad_path.name
    original, ipad = Document(original_path), Document(ipad_path)
    for page in range(original.page_count):
        before = [a for a in original.annotations if a.page == page]
        after = [a for a in ipad.annotations if a.page == page]
        strokes_before = [(s, w) for a in before if isinstance(a, Ink) for s, w in zip(a.strokes, a.stroke_widths())]
        strokes_after = [(s, w) for a in after if isinstance(a, Ink) for s, w in zip(a.strokes, a.stroke_widths())]
        if report.check(len(strokes_before) == len(strokes_after), f"{where} page {page}",
                        f"{len(strokes_after)} ink paths, the desktop wrote {len(strokes_before)}"):
            for number, ((a, wa), (b, wb)) in enumerate(zip(strokes_before, strokes_after)):
                why = same_polyline(a, wa, b, wb, 0.2, 0.05)
                report.check(why is None, f"{where} page {page} ink path {number}", why or "")
        others_before = [a for a in before if not isinstance(a, Ink)]
        others_after = [a for a in after if not isinstance(a, Ink)]
        # A kind listed in known_losses.json may be missing after the iPad saved: warned about, not failed.
        lost = [a for a in others_before if type(a).__name__ in losses]
        if lost and len(others_after) != len(others_before):
            for a in lost:
                kind = type(a).__name__
                print(f"WARNING {where} page {page} {kind} {a.id} (known loss: {losses[kind]})")
            others_before = [a for a in others_before if type(a).__name__ not in losses]
            others_after = [a for a in others_after if type(a).__name__ not in losses]
        if not report.check(len(others_before) == len(others_after), f"{where} page {page}",
                            f"{len(others_after)} other annotations, the desktop wrote {len(others_before)}: "
                            f"{[type(a).__name__ for a in others_after]}"):
            continue
        for a, b in zip(others_before, others_after):
            label = f"{where} page {page} {type(a).__name__} {a.id}"
            if a.id == edit.get("id") or (isinstance(a, FreeText) and a.contents == edit.get("contents")):
                if report.check(isinstance(b, FreeText), label, f"read as {type(b).__name__}"):
                    report.check(b.font == edit["font"] and b.bold == edit["bold"] and close(b.font_size, edit["size"], 0.01),
                                 label, f"edited box reads {b.font} bold {b.bold} {b.font_size} pt, expected "
                                        f"{edit['font']} bold {edit['bold']} {edit['size']} pt")
                continue
            found = differences(a, b)
            if not found:
                continue
            kind = type(a).__name__
            if kind in losses:
                print(f"WARNING {label} (known loss: {losses[kind]}): {'; '.join(found)}")
            else:
                report.fail(label, "; ".join(found))
    original.close()
    ipad.close()
    report.check(note_id(ipad_path) is not None, where, "no /ZenithNoteID")


def render(pdfs: list[Path], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for path in pdfs:
        document = pdfium.PdfDocument(str(path))
        try:
            for index in range(len(document)):
                image = document[index].render(scale=2).to_pil()
                image.save(folder / f"desktop-{path.stem}-{index + 1}.png")
        finally:
            document.close()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="The desktop reads what the iPad wrote.")
    parser.add_argument("exports", type=Path)
    parser.add_argument("--png", type=Path)
    args = parser.parse_args(argv[1:])
    app = QGuiApplication.instance() or QGuiApplication(["check-desktop-reads"])  # noqa: F841 - pictures need it
    report = Report()
    exports: Path = args.exports
    pdfs = sorted(exports.glob("*.pdf")) if exports.is_dir() else []

    checked = 0
    for pdf_path in pdfs:
        json_path = pdf_path.with_suffix(".json")
        if not json_path.exists():
            continue
        expected = json.loads(json_path.read_text(encoding="utf-8"))
        if expected.get("format") != EXPORT_FORMAT:
            continue  # another tool's expectations, such as desktop_edit.py's
        check_export(pdf_path, expected, report)
        checked += 1
    print(f"{checked} iPad export(s) checked")

    round_trip = exports / "desktop-ink-ipad.pdf"
    if round_trip.exists() and FIXTURE.exists():
        edit = json.loads(FIXTURE_JSON.read_text(encoding="utf-8")).get("editText", {}) if FIXTURE_JSON.exists() else {}
        entries = json.loads(KNOWN_LOSSES.read_text(encoding="utf-8")) if KNOWN_LOSSES.exists() else []
        losses = {entry["kind"]: entry.get("reason", "") for entry in entries}
        check_round_trip(FIXTURE, round_trip, edit, losses, report)
        print("desktop fixture round trip checked")
    else:
        print(f"WARNING no round trip to check ({round_trip.name} or the fixture is missing)")

    if args.png:
        render(pdfs + ([FIXTURE] if FIXTURE.exists() else []), args.png)

    if report.failures:
        print(f"{len(report.failures)} failure(s)")
        return 1
    print("the desktop reads every iPad note")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
