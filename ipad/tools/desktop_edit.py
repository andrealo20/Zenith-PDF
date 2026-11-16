"""Edit an iPad note the way a person would on the desktop, for the iPad to read back.

    python ipad/tools/desktop_edit.py <ipad-ink.pdf> <out.pdf> <expected.json>

On the first six iPad strokes, in the order the iPad wrote them: moves stroke 0 by (12, 7) display points;
recolours stroke 1 to (0.8, 0.1, 0.1) at opacity 0.6; doubles the width of stroke 2 as the style bar does
(every width scaled); erases the second half of stroke 3; turns stroke 4 into a highlighter; leaves the rest.
Adds one desktop pen stroke with widths. Saves with the desktop's Document.save, checks that /ZenithNoteID
survived, and writes what each /NM should read as on the iPad (DesktopCompatibilityTests.testReadsDesktopEdits).
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from zenith_pdf.core.annotations import Ink  # noqa: E402
from zenith_pdf.core.document import Document  # noqa: E402

EDIT_FORMAT = "zenith-desktop-edit-1"
MOVE = (12.0, 7.0)
RECOLOUR = (0.8, 0.1, 0.1)


def doubled(ink: Ink) -> Ink:
    """The style bar's width change (document_view.apply_style): the widths keep their shape, scaled."""
    factor = 2.0
    widths = tuple(tuple(w * factor for w in stroke) for stroke in ink.widths)
    return replace(ink, width=ink.width * factor, widths=widths)


def half(ink: Ink) -> Ink:
    """The eraser took the second half of the stroke."""
    stroke = ink.strokes[0]
    keep = max(2, len(stroke) // 2)
    widths = (ink.stroke_widths()[0][:keep],) if ink.widths else ()
    return replace(ink, strokes=(stroke[:keep],), widths=widths)


def desktop_pen(page: int) -> Ink:
    points = tuple((round(80 + i * 6.0, 2), round(700 + math.sin(i / 2) * 10, 2)) for i in range(25))
    widths = tuple(round(1.5 + 1.0 * abs(math.sin(i / 4)), 2) for i in range(25))
    return Ink(page=page, strokes=(points,), widths=(widths,), width=max(widths), color=(0.0, 0.3, 0.6))


def read_back(ink: Ink) -> dict:
    return dict(paths=[[list(p) for p in s] for s in ink.strokes], widths=[list(w) for w in ink.stroke_widths()],
                color=list(ink.color), opacity=ink.opacity, highlighter=ink.highlighter)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: desktop_edit.py <ipad-ink.pdf> <out.pdf> <expected.json>", file=sys.stderr)
        return 2
    app = QGuiApplication.instance() or QGuiApplication(["desktop-edit"])  # noqa: F841
    source, out, expected_path = Path(argv[1]), Path(argv[2]), Path(argv[3])
    with pikepdf.open(source) as pdf:
        note_id = pdf.docinfo.get("/ZenithNoteID")
        note_id = str(note_id) if note_id is not None else None
    if note_id is None:
        print(f"{source} has no /ZenithNoteID", file=sys.stderr)
        return 1

    document = Document(source)
    annotations = list(document.annotations)
    strokes = [i for i, a in enumerate(annotations) if isinstance(a, Ink)]
    if len(strokes) < 6:
        print(f"{source} has {len(strokes)} strokes, the edits need six", file=sys.stderr)
        return 1
    expectations = []
    for number, position in enumerate(strokes):
        ink = annotations[position]
        if number == 0:
            annotations[position] = ink.translated(*MOVE)
            expectations.append(dict(id=ink.id, expect="moved", offset=list(MOVE)))
        elif number == 1:
            annotations[position] = replace(ink, color=RECOLOUR, opacity=0.6)
            expectations.append(dict(id=ink.id, expect="restyled", color=list(RECOLOUR), opacity=0.6, widthScale=1.0,
                                     highlighter=ink.highlighter))
        elif number == 2:
            annotations[position] = doubled(ink)
            expectations.append(dict(id=ink.id, expect="restyled", color=list(ink.color), opacity=ink.opacity,
                                     widthScale=2.0, highlighter=ink.highlighter))
        elif number == 3:
            annotations[position] = half(ink)
            expectations.append(dict(id=ink.id, expect="rebuilt"))
        elif number == 4:
            annotations[position] = replace(ink, highlighter=True)
            expectations.append(dict(id=ink.id, expect="restyled", color=list(ink.color), opacity=ink.opacity,
                                     widthScale=1.0, highlighter=True))
        else:
            expectations.append(dict(id=ink.id, expect="unchanged"))
    added = desktop_pen(annotations[strokes[0]].page)
    annotations.append(added)
    expectations.append(dict(id=added.id, expect="rebuilt"))
    document.save(annotations, out)
    document.close()

    with pikepdf.open(out) as pdf:
        kept = pdf.docinfo.get("/ZenithNoteID")
    if kept is None or str(kept) != note_id:
        print(f"/ZenithNoteID did not survive the desktop save: {kept!r}", file=sys.stderr)
        return 1

    # What the desktop now reads, for the strokes the iPad must rebuild along the desktop's points.
    saved = Document(out)
    read = {a.id: a for a in saved.annotations if isinstance(a, Ink)}
    saved.close()
    for entry in expectations:
        ink = read.get(entry["id"])
        if ink is None:
            print(f"stroke {entry['id']} is missing after the desktop save", file=sys.stderr)
            return 1
        if entry["expect"] == "rebuilt":
            entry.update(read_back(ink))
    expected_path.write_text(json.dumps(dict(format=EDIT_FORMAT, noteID=note_id, strokes=expectations), indent=1),
                             encoding="utf-8")
    print(f"{out}: {len(expectations)} strokes, {sum(e['expect'] != 'unchanged' for e in expectations)} edited")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
