"""Write desktop-ink.pdf with the desktop's own code, and the values the iPad must read from it.

    python ipad/tools/make_fixtures.py <out.pdf> <out.json>

The PDF holds the ink the desktop pen writes (pressure widths, a highlighter, constant width, several paths
in one annotation) and one annotation of every other kind the desktop creates, on an A4 page, a page turned
90 degrees and a cropped page. The JSON lists every ink annotation as the desktop reads the file back
(display space, one width per point), the text box the iPad test edits, and the page geometries.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf  # noqa: E402
from pikepdf import Array  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

from zenith_pdf.core import images  # noqa: E402
from zenith_pdf.core.annotations import (  # noqa: E402
    Attachment, FreeText, ImageStamp, Ink, Line, Link, Measurement, Note, Poly, Shape, Tape, TextStamp,
)
from zenith_pdf.core.document import Document  # noqa: E402
from zenith_pdf.core.pages import new_page  # noqa: E402
from zenith_pdf.core.pdf_annots import page_geometry, write_page  # noqa: E402

A4 = (595.28, 841.89)
EDIT_TEXT = "Edit me on the iPad"


def wave(x: float, y: float, count: int, step: float = 7.0, height: float = 9.0) -> tuple:
    return tuple((round(x + i * step, 2), round(y + math.sin(i / 3) * height, 2)) for i in range(count))


def pressure(count: int, low: float = 1.2, high: float = 3.4) -> tuple:
    return tuple(round(low + (high - low) * (0.5 + 0.5 * math.sin(i / 2.5)), 2) for i in range(count))


def picture(color: QColor, width: int = 48, height: int = 32) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    return images.encode(image)


def pen(page: int, x: float, y: float, count: int, color, ident: str) -> Ink:
    widths = pressure(count)
    return Ink(page=page, strokes=(wave(x, y, count),), widths=(widths,), width=max(widths), color=color, id=ident)


def first_page() -> list:
    zigzag = ((60.0, 520.0), (61.0, 540.0), (110.0, 522.0), (112.5, 600.0), (160.0, 598.0), (220.0, 520.0))
    return [
        pen(0, 60, 90, 40, (0.1, 0.2, 0.7), "fixture-pen"),
        Ink(page=0, strokes=(wave(60, 150, 30, height=2),), width=12.0, color=(1.0, 0.85, 0.0), highlighter=True,
            opacity=0.6, id="fixture-highlighter"),
        Ink(page=0, strokes=(wave(60, 200, 25, height=14),), width=2.5, color=(0.0, 0.0, 0.0), id="fixture-constant"),
        Ink(page=0, strokes=(wave(320, 90, 12), wave(320, 120, 8), ((330.0, 150.0), (380.0, 152.0))), width=1.5,
            color=(0.2, 0.5, 0.2), id="fixture-multipath"),
        Ink(page=0, strokes=(zigzag,), width=2.0, color=(0.6, 0.1, 0.5), id="fixture-zigzag"),
        Shape(page=0, shape="Square", rect=(60, 260, 120, 70), width=2.0, color=(0.1, 0.1, 0.1), fill=(0.3, 0.6, 0.9),
              pattern="diagonal", id="fixture-pattern"),
        Shape(page=0, shape="Square", rect=(220, 260, 110, 70), width=1.5, color=(0.85, 0.1, 0.1), cloudy=True,
              id="fixture-cloud"),
        Shape(page=0, shape="Circle", rect=(370, 260, 90, 70), width=2.0, color=(0.1, 0.4, 0.1), dash="dashed",
              id="fixture-ellipse"),
        Line(page=0, start=(60, 360), end=(250, 380), width=2.0, head_start="OpenArrow", head_end="ClosedArrow",
             color=(0.0, 0.0, 0.6), id="fixture-arrow"),
        Poly(page=0, points=((300, 360), (380, 350), (420, 400), (330, 420)), closed=True, fill=(0.95, 0.9, 0.5),
             width=1.5, color=(0.4, 0.3, 0.0), id="fixture-polygon"),
        Poly(page=0, points=((440, 360), (480, 380), (520, 360)), width=1.0, head_end="OpenArrow",
             color=(0.3, 0.3, 0.3), id="fixture-polyline"),
        Tape(page=0, start=(60, 440), end=(260, 440), width=18.0, id="fixture-tape"),
        Measurement(page=0, points=((60, 480), (200, 480)), mode="distance", id="fixture-distance"),
        Measurement(page=0, points=((300, 470), (380, 470), (380, 510)), mode="perimeter", id="fixture-perimeter"),
        Measurement(page=0, points=((420, 460), (500, 460), (500, 510), (420, 510)), mode="area", id="fixture-area"),
        Measurement(page=0, points=((260, 560), (300, 520), (340, 560)), mode="angle", id="fixture-angle"),
        Measurement(page=0, points=((380, 540), (460, 540), (460, 600), (380, 600)), mode="volume", depth=2.0,
                    id="fixture-volume"),
        FreeText(page=0, rect=(60, 640, 160, 60), contents="A callout, turned", font_size=12.0, border_width=1.0,
                 border_color=(0.2, 0.2, 0.6), callout=((250, 720), (230, 700)), rotation=1, font="Times",
                 id="fixture-callout"),
        FreeText(page=0, rect=(300, 640, 200, 30), contents=EDIT_TEXT, font_size=14.0, bold=True, id="fixture-edit"),
        Note(page=0, position=(520, 60), contents="A sticky note", id="fixture-note"),
        TextStamp(page=0, rect=(420, 120, 120, 40), text="APPROVED", name="Approved", id="fixture-stamp"),
        ImageStamp(page=0, rect=(60, 720, 96, 64), data=picture(QColor(200, 60, 40)), id="fixture-picture"),
        ImageStamp(page=0, rect=(180, 730, 80, 30), data=picture(QColor(255, 255, 255), 80, 30), equation="E = mc^2",
                   equation_size=14.0, equation_color=(0.0, 0.0, 0.0), id="fixture-equation"),
        Attachment(page=0, position=(520, 100), file_name="memo.m4a", data=b"\x00\x00\x00\x18ftypM4A ", audio=True,
                   id="fixture-audio"),
        Link(page=0, rect=(300, 740, 120, 20), target=1, target_point=(0.0, 500.0), id="fixture-page-link"),
        Link(page=0, rect=(430, 740, 120, 20), uri="https://example.org/", id="fixture-uri-link"),
        Shape(page=0, shape="Square", rect=(300, 780, 40, 30), width=1.0, group="fixture-group", id="fixture-group-a"),
        Shape(page=0, shape="Circle", rect=(350, 780, 40, 30), width=1.0, group="fixture-group", id="fixture-group-b"),
    ]


def turned_page() -> list:
    return [
        pen(1, 80, 100, 30, (0.0, 0.0, 0.0), "fixture-turned-pen"),
        Ink(page=1, strokes=(wave(80, 200, 20, height=3),), width=10.0, color=(0.4, 1.0, 0.4), highlighter=True,
            id="fixture-turned-highlighter"),
        FreeText(page=1, rect=(80, 300, 200, 30), contents="On a turned page", font_size=12.0, id="fixture-turned-text"),
        Shape(page=1, shape="Circle", rect=(400, 300, 80, 60), width=2.0, color=(0.6, 0.0, 0.0), id="fixture-turned-circle"),
    ]


def cropped_page() -> list:
    return [
        pen(2, 40, 60, 30, (0.5, 0.0, 0.0), "fixture-cropped-pen"),
        Ink(page=2, strokes=(wave(40, 140, 20, height=1),), width=2.0, color=(0.0, 0.0, 0.0), id="fixture-cropped-constant"),
    ]


def build(out_pdf: Path) -> None:
    pdf = pikepdf.new()
    for kind in ("lined", "blank", "grid"):
        pdf.pages.append(new_page(pdf, A4, kind))
    pdf.pages[1].obj.Rotate = 90
    pdf.pages[2].obj.CropBox = Array([40.0, 50.0, 540.0, 800.0])
    sources: dict = {}
    pictures: dict = {}
    for index, annotations in enumerate((first_page(), turned_page(), cropped_page())):
        write_page(pdf, index, page_geometry(pdf.pages[index].obj), annotations, sources, [], frozenset(), pictures)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(out_pdf)


def expected(out_pdf: Path) -> dict:
    """What the desktop reads back from the file it wrote: the values the iPad must read too."""
    document = Document(out_pdf)
    written = first_page() + turned_page() + cropped_page()
    read = {a.id: a for a in document.annotations}
    missing = [a.id for a in written if a.id not in read]
    if missing:
        raise SystemExit(f"the desktop does not read back: {missing}")
    ink = []
    for annotation in document.annotations:
        if not isinstance(annotation, Ink):
            continue
        ink.append(dict(
            page=annotation.page, id=annotation.id,
            paths=[[list(p) for p in stroke] for stroke in annotation.strokes],
            widths=[list(ws) for ws in annotation.stroke_widths()],
            hasWidths=bool(annotation.widths), width=annotation.width, color=list(annotation.color),
            opacity=annotation.opacity, highlighter=annotation.highlighter,
        ))
    pages = []
    for index in range(document.page_count):
        geometry = document.geometry(index)
        pages.append(dict(rotation=geometry.rotation, box=list(geometry.box)))
    result = dict(pages=pages, ink=ink, annotations=len(document.annotations),
                  editText=dict(page=0, id="fixture-edit", contents=EDIT_TEXT, font="Times", bold=False, size=18.0))
    document.close()
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    app = QGuiApplication.instance() or QGuiApplication(["make-fixtures"])  # noqa: F841 - fonts and images need it
    out_pdf, out_json = Path(argv[1]), Path(argv[2])
    build(out_pdf)
    values = expected(out_pdf)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(values, indent=1), encoding="utf-8")
    print(f"{out_pdf}: {values['annotations']} annotations, {len(values['ink'])} ink")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
