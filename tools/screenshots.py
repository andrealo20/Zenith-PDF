"""Make the pictures in the README: a demo lecture, marked up with Zenith PDF's own tools, shown screen by screen.

    python tools/screenshots.py [output folder, default docs/images]

Everything is drawn offscreen, from the app's widgets: nothing is taken from the
screen. The demo documents are written here, so the pictures show no one's files.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

# An offscreen screen as large as a real one, so popups open where they would, not squeezed to its edge. The
# platform's options are split at colons, so the file is named relative to its folder (see main).
_SCREEN = Path(tempfile.mkdtemp(prefix="zenith-screen-")) / "screen.json"
_SCREEN.write_text(json.dumps({"screens": [{"name": "shots", "x": 0, "y": 0, "width": 2560, "height": 1600,
                                            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}))
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_SCREEN.name}"
os.environ.setdefault("QT_SCALE_FACTOR", "1.25")  # sharper pictures than 100%
os.environ.setdefault("ZENITH_RENDER_PROCESSES", "0")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["ZENITH_CACHE_DIR"] = tempfile.mkdtemp(prefix="zenith-shots-cache-")

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PySide6.QtCore import QMarginsF, QPoint, QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen, QPolygonF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zenith_pdf.app import create_app  # noqa: E402
from zenith_pdf.core import audio  # noqa: E402
from zenith_pdf.core.annotations import (Attachment, FreeText, Ink, Line, Measurement, Note, Shape,  # noqa: E402
                                         Tape, TextMarkup)
from zenith_pdf.settings import Settings  # noqa: E402
from zenith_pdf.ui import writing_dialogs  # noqa: E402

W, H = 595.0, 842.0  # A4 in points
LEFT, RIGHT = 72.0, 72.0
SERIF, SANS = "Georgia", "Segoe UI"
INK = QColor(28, 30, 36)
GREY = QColor(105, 110, 120)
BLUE = QColor(37, 99, 235)
ORANGE = QColor(234, 120, 20)
TOOLBAR = ["select", "hand", "pen", "marker", "eraser", "highlight", "underline", "strikeout", "rectangle", "ellipse",
           "arrow", "text", "note", "equation", "audio", "distance"]


# The demo lecture ---------------------------------------------------------------------------


class Page:
    """Writes one page top to bottom, keeping track of where the next line goes."""

    def __init__(self, painter: QPainter):
        self.p = painter
        self.y = 60.0

    def font(self, family: str, size: float, bold=False, italic=False) -> QFont:
        font = QFont(family)
        font.setPointSizeF(size)
        font.setBold(bold)
        font.setItalic(italic)
        return font

    def text(self, string: str, size=11.0, family=SERIF, bold=False, italic=False, color=INK, gap=8.0,
             x=LEFT, width=None, align=Qt.AlignmentFlag.AlignLeft) -> QRectF:
        width = width or (W - LEFT - RIGHT)
        self.p.setFont(self.font(family, size, bold, italic))
        self.p.setPen(color)
        flags = int(align) | int(Qt.TextFlag.TextWordWrap)
        rect = self.p.boundingRect(QRectF(x, self.y, width, 2000), flags, string)
        self.p.drawText(QRectF(x, self.y, width, rect.height()), flags, string)
        self.y += rect.height() + gap
        return rect

    def heading(self, string: str) -> None:
        self.y += 6
        self.text(string, 15, SANS, bold=True, gap=6)

    def rule(self) -> None:
        self.p.setPen(QPen(QColor(210, 214, 222), 0.8))
        self.p.drawLine(QPointF(LEFT, self.y), QPointF(W - RIGHT, self.y))
        self.y += 14

    def header(self, title: str) -> None:
        self.text("RENEWABLE ENERGY ENGINEERING  ·  LECTURE 4", 8.5, SANS, bold=True, color=GREY, gap=2)
        self.p.setPen(QPen(QColor(210, 214, 222), 0.8))
        self.p.drawLine(QPointF(LEFT, 52), QPointF(W - RIGHT, 52))
        self.y = 70
        if title:
            self.heading(title)

    def footer(self, number: int) -> None:
        self.p.setFont(self.font(SANS, 8.5))
        self.p.setPen(GREY)
        self.p.drawText(QRectF(LEFT, H - 46, W - LEFT - RIGHT, 14), int(Qt.AlignmentFlag.AlignRight),
                        f"Photovoltaic Systems  ·  {number}")


def box(p: QPainter, rect: QRectF, label: str, fill: QColor) -> None:
    p.setPen(QPen(fill.darker(150), 1.2))
    p.setBrush(fill)
    p.drawRoundedRect(rect, 8, 8)
    font = QFont(SANS)
    font.setPointSizeF(9.5)
    font.setBold(True)
    p.setFont(font)
    p.setPen(INK)
    p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), label)


def arrow(p: QPainter, start: QPointF, end: QPointF) -> None:
    p.setPen(QPen(QColor(70, 76, 90), 1.6))
    p.drawLine(start, end)
    angle = math.atan2(end.y() - start.y(), end.x() - start.x())
    wing = [QPointF(end.x() - 8 * math.cos(angle - s), end.y() - 8 * math.sin(angle - s)) for s in (0.45, -0.45)]
    p.setBrush(QColor(70, 76, 90))
    p.drawPolygon(QPolygonF([end, wing[0], wing[1]]))


def sun(p: QPainter, centre: QPointF, radius: float) -> None:
    p.setPen(QPen(QColor(245, 170, 20), 2))
    for i in range(12):
        a = i * math.pi / 6
        p.drawLine(QPointF(centre.x() + (radius + 4) * math.cos(a), centre.y() + (radius + 4) * math.sin(a)),
                   QPointF(centre.x() + (radius + 10) * math.cos(a), centre.y() + (radius + 10) * math.sin(a)))
    p.setBrush(QColor(255, 196, 40))
    p.drawEllipse(centre, radius, radius)


def page_one(page: Page) -> None:
    p = page.p
    page.text("RENEWABLE ENERGY ENGINEERING  ·  LECTURE 4", 8.5, SANS, bold=True, color=GREY, gap=10)
    page.text("Photovoltaic Systems", 30, SANS, bold=True, gap=2)
    page.text("From the solar cell to a working installation", 13, SANS, color=GREY, gap=14)
    page.rule()
    page.heading("1  How a solar cell works")
    page.text("A solar cell is a thin slice of semiconductor, usually silicon, that converts sunlight directly into "
              "electricity. When light falls on the cell, photons give their energy to electrons, which are pushed "
              "across the junction between two layers of silicon. The flow of these electrons is a direct current.")
    page.text("A single cell gives about half a volt, so cells are connected in series to build a module, and "
              "modules are connected in series and in parallel to build an array with the voltage and the current "
              "a system needs.", gap=18)
    top = page.y + 34
    sun(p, QPointF(LEFT + 40, top - 16), 9)
    labels = [("PV array", QColor(219, 234, 254)), ("Charge controller", QColor(220, 252, 231)),
              ("Battery", QColor(254, 243, 199)), ("Inverter", QColor(237, 233, 254)), ("AC loads", QColor(255, 228, 230))]
    width, gap = 76.0, 15.0
    rects = []
    for i, (label, fill) in enumerate(labels):
        rect = QRectF(LEFT + i * (width + gap), top, width, 46)
        box(p, rect, label, fill)
        rects.append(rect)
    for a, b in zip(rects, rects[1:]):
        arrow(p, QPointF(a.right() + 2, a.center().y()), QPointF(b.left() - 2, b.center().y()))
    page.y = top + 58
    page.text("Figure 1. The parts of a stand-alone photovoltaic system.", 9.5, SANS, italic=True, color=GREY,
              gap=16, align=Qt.AlignmentFlag.AlignHCenter)
    page.text("The charge controller protects the battery from overcharging, and the inverter turns the battery's "
              "direct current into the alternating current household appliances use.", gap=16)
    facts = QRectF(LEFT, page.y, W - LEFT - RIGHT, 92)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(243, 245, 249))
    p.drawRoundedRect(facts, 10, 10)
    p.setBrush(BLUE)
    p.drawRoundedRect(QRectF(facts.left(), facts.top(), 4, facts.height()), 2, 2)
    page.y += 12
    page.text("Key facts", 11, SANS, bold=True, x=LEFT + 18, gap=4)
    for line in ("Typical cell efficiency: 18% to 23%", "One cell gives about 0.5 V",
                 "The output falls as the cell heats up"):
        page.text(f"•   {line}", 10.5, x=LEFT + 18, gap=3)
    page.y = facts.bottom() + 18
    page.heading("Further reading")
    page.text("Course notes, chapter 7, and the lecture slides of week 4.", gap=8)
    page.footer(1)


def page_two(page: Page) -> QPointF:
    p = page.p
    page.header("2  The I-V curve")
    page.text("The current a cell gives depends on the voltage across it. Plotting current against voltage gives "
              "the I-V curve. Power is the product of the two, and it peaks at a single point of the curve, the "
              "maximum power point (MPP). A good charge controller keeps the panel working at this point, a "
              "technique called maximum power point tracking.", gap=16)
    plot = QRectF(LEFT + 36, page.y + 6, 380, 200)
    p.setPen(QPen(QColor(225, 228, 235), 0.6))
    for i in range(1, 8):
        x = plot.left() + plot.width() * i / 8
        p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
    for i in range(1, 5):
        y = plot.top() + plot.height() * i / 5
        p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
    p.setPen(QPen(INK, 1.2))
    p.drawLine(plot.bottomLeft(), plot.bottomRight())
    p.drawLine(plot.bottomLeft(), plot.topLeft())
    p.setFont(page.font(SANS, 8.5))
    p.setPen(GREY)
    for i in range(0, 9):
        x = plot.left() + plot.width() * i / 8
        p.drawText(QRectF(x - 12, plot.bottom() + 4, 24, 12), int(Qt.AlignmentFlag.AlignHCenter), str(i * 5))
    for i in range(0, 6):
        y = plot.bottom() - plot.height() * i / 5
        p.drawText(QRectF(plot.left() - 30, y - 6, 24, 12), int(Qt.AlignmentFlag.AlignRight), str(i * 2))
    p.drawText(QRectF(plot.left(), plot.bottom() + 18, plot.width(), 14), int(Qt.AlignmentFlag.AlignHCenter),
               "Voltage (V)")
    p.save()
    p.translate(plot.left() - 44, plot.center().y())
    p.rotate(-90)
    p.drawText(QRectF(-60, -7, 120, 14), int(Qt.AlignmentFlag.AlignHCenter), "Current (A)")
    p.restore()

    def current(v: float) -> float:  # a 60-cell module: 9.5 A short circuit, 38 V open circuit
        return max(0.0, 9.5 * (1 - math.exp((v - 38.0) / 2.2)))

    def point(v: float, value: float, top: float) -> QPointF:
        return QPointF(plot.left() + plot.width() * v / 40, plot.bottom() - plot.height() * value / top)

    curve = QPainterPath(point(0, current(0), 10))
    power = QPainterPath(point(0, 0, 330))
    best = (0.0, 0.0)
    for step in range(1, 401):
        v = step * 38.0 / 400
        curve.lineTo(point(v, current(v), 10))
        power.lineTo(point(v, v * current(v), 330))
        if v * current(v) > best[0] * current(best[0]):
            best = (v, current(v))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(BLUE, 2.2))
    p.drawPath(curve)
    p.setPen(QPen(ORANGE, 2.2, Qt.PenStyle.DashLine))
    p.drawPath(power)
    mpp = point(best[0], best[1], 10)
    p.setPen(QPen(INK, 1))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(mpp, 4, 4)
    p.setFont(page.font(SANS, 8.5, bold=True))
    p.setPen(BLUE)
    p.drawText(QPointF(plot.left() + 12, plot.top() + 58), "Current")
    p.setPen(ORANGE)
    p.drawText(QPointF(plot.left() + 12, plot.top() + 71), "Power")
    page.y = plot.bottom() + 40
    page.text("Figure 2. Current and power of a 60-cell module at 1000 W/m² and 25 °C.", 9.5, SANS, italic=True,
              color=GREY, gap=16, align=Qt.AlignmentFlag.AlignHCenter)
    page.text("P = V · I          η = Pmax / (G · A)", 14, "Cambria Math", italic=True, gap=6,
              align=Qt.AlignmentFlag.AlignHCenter)
    page.text("where G is the irradiance in W/m² and A the module area in m².", 10, color=GREY, gap=14,
              align=Qt.AlignmentFlag.AlignHCenter)
    page.text("Every degree above 25 °C lowers the voltage, and with it the power, by about 0.4%. On a hot roof a "
              "module can lose more than a tenth of its rated output, which is why panels are mounted with room "
              "for air to flow behind them.")
    page.footer(2)
    return mpp


def page_three(page: Page) -> None:
    p = page.p
    page.header("3  Sizing a stand-alone system")
    page.text("Sizing starts from the energy the loads need in a day. List every appliance with its power and the "
              "hours it runs, and add up the energy.", gap=14)
    columns = [("Appliance", 170), ("Power (W)", 90), ("Hours a day", 90), ("Energy (Wh)", 101)]
    rows = [("LED lights", "32", "5", "160"), ("Laptop", "60", "4", "240"), ("Fridge", "90", "8", "720"),
            ("Water pump", "250", "1", "250"), ("Router", "10", "24", "240"), ("Total", "", "", "1610")]
    y = page.y
    height = 22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(30, 64, 175))
    p.drawRoundedRect(QRectF(LEFT, y, 451, height), 4, 4)
    x = LEFT
    p.setFont(page.font(SANS, 9.5, bold=True))
    p.setPen(QColor(255, 255, 255))
    for title, width in columns:
        p.drawText(QRectF(x + 8, y, width - 16, height), int(Qt.AlignmentFlag.AlignVCenter), title)
        x += width
    y += height
    for number, row in enumerate(rows):
        last = number == len(rows) - 1
        if number % 2 or last:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(239, 243, 250) if not last else QColor(226, 232, 245))
            p.drawRect(QRectF(LEFT, y, 451, height))
        x = LEFT
        p.setFont(page.font(SERIF, 10.5, bold=last))
        p.setPen(INK)
        for (title, width), value in zip(columns, row):
            align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if title == "Appliance"
                                                     else Qt.AlignmentFlag.AlignRight)
            p.drawText(QRectF(x + 8, y, width - 16, height), int(align), value)
            x += width
        y += height
    page.y = y + 16
    page.text("With 4.5 peak sun hours and system losses of 25%, the array must give 1610 / (4.5 × 0.75) ≈ 477 W. "
              "Three 200 W modules cover it with a margin.")
    page.text("For two days of autonomy at a 50% depth of discharge, the battery must store 1610 × 2 / 0.5, that "
              "is 6.4 kWh.", gap=14)
    page.heading("Key points")
    for line in ("Size the system from the energy the loads need, not from their power.",
                 "Allow for losses: wiring, temperature, dust and the inverter.",
                 "A battery is sized by the days it must run without sun."):
        page.text(f"•   {line}", 10.5, gap=4)
    page.footer(3)


def page_four(page: Page) -> None:
    p = page.p
    page.header("4  Orientation and tilt")
    page.text("In the northern hemisphere a module faces south. Its tilt β, the angle between the module and the "
              "ground, is best close to the latitude of the site for the most energy over the year; steeper "
              "favours winter, flatter favours summer.", gap=18)
    ground = page.y + 190
    base = QPointF(LEFT + 60, ground)
    top = QPointF(LEFT + 300, ground - 140)
    p.setPen(QPen(INK, 1.4))
    p.drawLine(QPointF(LEFT + 20, ground), QPointF(W - RIGHT - 20, ground))
    p.setPen(QPen(QColor(120, 85, 60), 2))
    p.setBrush(QColor(240, 226, 210))
    p.drawPolygon(QPolygonF([base, top, QPointF(top.x(), ground)]))
    p.setPen(QPen(QColor(30, 64, 175), 6))
    p.drawLine(QPointF(base.x() + 40, ground - 23), QPointF(top.x() - 30, top.y() + 18))
    p.setPen(QPen(INK, 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(base.x() - 46, ground - 46, 92, 92), 0, int(30 * 16))
    p.setFont(page.font(SERIF, 13, italic=True))
    p.drawText(QPointF(base.x() + 52, ground - 10), "β")
    sun(p, QPointF(W - RIGHT - 60, ground - 170), 12)
    p.setPen(QPen(QColor(245, 170, 20), 1.4, Qt.PenStyle.DashLine))
    p.drawLine(QPointF(W - RIGHT - 74, ground - 158), QPointF(top.x() - 80, top.y() + 52))
    page.y = ground + 20
    page.text("Figure 3. Tilt angle β of a roof-mounted module.", 9.5, SANS, italic=True, color=GREY, gap=16,
              align=Qt.AlignmentFlag.AlignHCenter)
    page.text("Shade matters more than tilt. A shadow over a single cell can cut the current of the whole string "
              "of cells in series, which is why modules carry bypass diodes.")
    page.footer(4)


def page_five(page: Page) -> None:
    page.header("5  Exercises")
    exercises = [
        ("A module gives 8.9 A at 33.4 V at its maximum power point. What is its power?", "Answer: 297 W"),
        ("A house uses 2.4 kWh a day. With 5 peak sun hours and 20% losses, how large must the array be?",
         "Answer: 600 W"),
        ("Why does a charge controller need maximum power point tracking?", "Answer: the MPP moves with light and "
                                                                           "temperature."),
    ]
    for number, (question, answer) in enumerate(exercises, start=1):
        page.text(f"{number}.  {question}", gap=6)
        page.text(answer, 10.5, SANS, color=QColor(22, 101, 52), x=LEFT + 18, gap=16)
    page.footer(5)


def make_lecture(path: Path) -> QPointF:
    """Write the lecture; returns where the maximum power point is drawn on page 2, in points."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(72)
    writer.setTitle("Photovoltaic Systems")
    writer.setCreator("Zenith PDF")
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    mpp = QPointF()
    for number, draw in enumerate((page_one, page_two, page_three, page_four, page_five)):
        if number:
            writer.newPage()
        result = draw(Page(painter))
        if number == 1:
            mpp = result
    painter.end()
    return mpp


def make_simple(path: Path, title: str, lines: list[str], pages: int = 3, tint=QColor(37, 99, 235)) -> None:
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setResolution(72)
    writer.setTitle(title)
    painter = QPainter(writer)
    for number in range(pages):
        if number:
            writer.newPage()
        page = Page(painter)
        painter.fillRect(QRectF(0, 0, W, 8), tint)
        page.text(title if number == 0 else f"{title} (continued)", 22 if number == 0 else 14, SANS, bold=True,
                  gap=14)
        for line in lines:
            page.text(line)
        page.footer(number + 1)
    painter.end()


# Marking it up ------------------------------------------------------------------------------


def found(document, text: str, page: int):
    """The rectangles of `text` on `page`: one per piece of text, and Qt writes text in many pieces."""
    return next(h for h in document.iter_search(text, False, False) if h.page == page).rects


def line_of(document, text: str, page: int):
    """The rectangle round `text`, when it fits on one line."""
    rects = found(document, text, page)
    left, top = min(r[0] for r in rects), min(r[1] for r in rects)
    right, bottom = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
    return left, top, right - left, bottom - top


def circle(cx, cy, rx, ry, turns=1.08, wobble=2.0, start=-2.4):
    points = []
    for i in range(80):
        a = start + turns * 2 * math.pi * i / 79
        r = 1 + wobble / 100 * math.sin(i * 0.7)
        points.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return tuple(points)


def pen_widths(stroke, base):
    return tuple(base * (0.7 + 0.45 * math.sin(i / max(len(stroke) - 1, 1) * math.pi)) for i in range(len(stroke)))


def mark_up(workspace, mpp: QPointF) -> None:
    """Annotations of every kind, placed by the text they go with."""
    view, document = workspace.view, workspace.document
    author = dict(author="Andrea")
    yellow, green, red, blue = (1.0, 0.86, 0.1), (0.35, 0.85, 0.4), (0.9, 0.2, 0.2), (0.15, 0.4, 0.95)
    made = [
        TextMarkup(page=0, quads=found(document, "converts sunlight directly into", 0), color=yellow, **author),
        TextMarkup(page=0, style="Underline", quads=found(document, "connected in series", 0), color=red, **author),
        TextMarkup(page=1, quads=found(document, "maximum power point (MPP)", 1), color=green, **author),
        TextMarkup(page=1, style="Squiggly", quads=found(document, "about 0.4%", 1), color=blue, **author),
        TextMarkup(page=2, quads=found(document, "6.4 kWh", 2), color=yellow, **author),
    ]
    # Page 1: a pen loop round a block of the diagram, a note on the key facts, a reminder in red.
    controller = line_of(document, "Charge controller", 0)
    cx, cy = controller[0] + controller[2] / 2, controller[1] + controller[3] / 2 + 6
    loop = circle(cx, cy, 54, 36)
    facts = line_of(document, "Key facts", 0)
    figure = line_of(document, "Figure 1.", 0)
    made += [
        Ink(page=0, strokes=(loop,), widths=(pen_widths(loop, 2.4),), width=3.0, color=blue, **author),
        Note(page=0, position=(W - RIGHT - 30, facts[1] - 2), contents="Know the five blocks of Figure 1 for the exam.",
             color=(1.0, 0.82, 0.2), **author),
        FreeText(page=0, rect=(W - RIGHT - 190, figure[1] - 128, 190, 22), contents="Exam: draw this from memory",
                 font_size=11, color=(0.8, 0.1, 0.1), **author),
    ]
    # Page 2: a callout at the maximum power point, a dashed box round the equations.
    x, y = mpp.x(), mpp.y()
    irradiance = line_of(document, "irradiance", 1)
    made += [
        FreeText(page=1, rect=(x + 40, y - 30, 150, 44), contents="Run the module here: about 31 V and 8.9 A",
                 font_size=10.5, fill=(1.0, 1.0, 1.0), border_width=1.2, border_color=red,
                 callout=((x + 5, y + 2), (x + 26, y - 8)), color=(0.12, 0.12, 0.14), **author),
        Shape(page=1, rect=(LEFT + 70, irradiance[1] - 36, W - LEFT - RIGHT - 140, 54), color=blue, width=1.4,
              dash="dashed", **author),
    ]
    # Page 3: a cloud round the total, a tick by the worked example.
    total = line_of(document, "1610", 2)
    worked = line_of(document, "477 W", 2)
    tx, ty = worked[0] + worked[2] + 10, worked[1] + worked[3] / 2
    tick = ((tx, ty), (tx + 6, ty + 7), (tx + 20, ty - 12))
    made += [
        Shape(page=2, rect=(LEFT - 4, total[1] - 6, 459, total[3] + 12), color=(0.1, 0.62, 0.3), width=1.6,
              cloudy=True, **author),
        Ink(page=2, strokes=(tick,), width=2.6, color=(0.1, 0.62, 0.3), **author),
    ]
    # Page 4: the roof measured at a scale, an arrow at the tilt angle.
    caption = line_of(document, "Figure 3.", 3)
    ground = caption[1] - 20
    made += [
        Measurement(page=3, points=((LEFT + 60, ground + 14), (LEFT + 300, ground + 14)), scale=6.0 / 240, unit="m",
                    color=(0.05, 0.55, 0.75), **author),
        FreeText(page=3, rect=(LEFT, ground - 214, 180, 22), contents="β close to the latitude", font_size=11,
                 color=red, **author),
        Line(page=3, start=(LEFT + 70, ground - 188), end=(LEFT + 108, ground - 22), color=red, width=2.2,
             head_end="ClosedArrow", **author),
    ]
    # Page 5: tape over the answers, to test yourself, and a voice note.
    answers = [line_of(document, "Answer: 297 W", 4), line_of(document, "Answer: 600 W", 4)]
    made += [Tape(page=4, start=(a[0] - 4, a[1] + a[3] / 2), end=(a[0] + a[2] + 4, a[1] + a[3] / 2), width=a[3] + 6,
                  **author) for a in answers]
    third = line_of(document, "Why does a charge", 4)
    pcm = b"".join(int(8000 * math.sin(i / 9)).to_bytes(2, "little", signed=True) for i in range(22050))
    made.append(Attachment(page=4, position=(W - RIGHT - 20, third[1] - 3), file_name="Exercise 3, explained.wav",
                           data=audio.wav_bytes(pcm), audio=True, **author))
    view.add_annotations(made, "Demo")


# Pictures -----------------------------------------------------------------------------------


def to_pil(pixmap) -> Image.Image:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (image.width(), image.height()), bytes(image.constBits()), "raw", "RGBA",
                            image.bytesPerLine(), 1).copy()


def rounded_mask(size, radius) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def framed(picture: Image.Image, dark: bool, radius=14, margin=40) -> Image.Image:
    """The picture with rounded corners, a hairline edge and a soft shadow, on a transparent ground."""
    w, h = picture.size
    canvas = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shade = rounded_mask((w, h), radius).point(lambda v: v * (110 if dark else 70) // 255)
    shadow.paste((0, 0, 0, 255), (margin, margin + 10), shade)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.alpha_composite(canvas, shadow)
    mask = rounded_mask((w, h), radius)
    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body.paste(picture, (0, 0), ImageChops.multiply(mask, picture.getchannel("A")))
    edge = ImageDraw.Draw(body)
    edge.rounded_rectangle((0, 0, w - 1, h - 1), radius, outline=(255, 255, 255, 30) if dark else (0, 0, 0, 40))
    canvas.alpha_composite(body, (margin, margin))
    return canvas


def with_popups(window, popups) -> Image.Image:
    """The window with popups (menus, panels, dialogs) drawn where they are, each with its shadow."""
    picture = to_pil(window.grab())
    ratio = picture.width / window.width()
    origin = window.mapToGlobal(QPoint(0, 0))
    for popup in popups:
        layer = to_pil(popup.grab())
        offset = popup.mapToGlobal(QPoint(0, 0)) - origin
        x, y = int(offset.x() * ratio), int(offset.y() * ratio)
        alpha = layer.getchannel("A")
        shadow = Image.new("RGBA", picture.size, (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 120), (x, y + int(8 * ratio)), alpha)
        picture = Image.alpha_composite(picture, shadow.filter(ImageFilter.GaussianBlur(14 * ratio)))
        picture.alpha_composite(layer, (max(x, 0), max(y, 0)))
    return picture


def dimmed(picture: Image.Image, dialog_picture: Image.Image) -> Image.Image:
    """A dialog over the window, which darkens a little behind it, as a modal dialog makes it feel."""
    base = Image.alpha_composite(picture, Image.new("RGBA", picture.size, (0, 0, 0, 90)))
    x = (base.width - dialog_picture.width) // 2
    y = (base.height - dialog_picture.height) // 2
    rounded = Image.new("RGBA", dialog_picture.size, (0, 0, 0, 0))
    rounded.paste(dialog_picture, (0, 0), rounded_mask(dialog_picture.size, 12))
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (x, y + 12), rounded.getchannel("A"))
    base = Image.alpha_composite(base, shadow.filter(ImageFilter.GaussianBlur(22)))
    base.alpha_composite(rounded, (x, y))
    return base


def save(picture: Image.Image, out: Path, name: str, dark: bool) -> None:
    final = framed(picture, dark)
    final.save(out / name, optimize=True)
    print(f"{name}: {final.width}x{final.height}, {(out / name).stat().st_size // 1024} KB")


def settle(ms=120):
    QTest.qWait(ms)
    QApplication.processEvents()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "images"
    out.mkdir(parents=True, exist_ok=True)
    here = Path.cwd()
    os.chdir(_SCREEN.parent)  # where the platform finds the screen's file
    QApplication.instance() or QApplication(["zenith-pdf"])  # also the fonts for the demo documents
    os.chdir(here)
    work = Path(tempfile.mkdtemp(prefix="zenith-demo-")) / "Energy course"  # the folder the home page shows
    work.mkdir()
    lecture = work / "Photovoltaic Systems - Lecture 4.pdf"
    mpp = make_lecture(lecture)
    others = [
        (work / "Thermodynamics formulas.pdf", "Thermodynamics formulas",
         ["First law: the change of internal energy equals the heat added minus the work done.",
          "Ideal gas: p V = n R T, with R = 8.314 J/(mol K).",
          "Carnot efficiency: 1 - Tc / Th, the most any engine between two temperatures can reach."], 4,
         QColor(220, 38, 38)),
        (work / "Lab report - inverter efficiency.pdf", "Lab report: inverter efficiency",
         ["Aim: measure the efficiency of a 1 kW inverter from 10% to 100% load.",
          "Method: input and output power read with two power analysers at each load step.",
          "Result: the efficiency peaks at 95.8% near 40% load and falls to 89% at 10%."], 6, QColor(22, 163, 74)),
        (work / "Electric machines - notes.pdf", "Electric machines",
         ["Transformers, induction motors and synchronous machines, with worked examples.",
          "The induction motor runs a little slower than its field: the difference is the slip."], 8,
         QColor(124, 58, 237)),
    ]
    for path, title, lines, pages, tint in others:
        make_simple(path, title, lines, pages, tint)

    settings = Settings(QSettings(str(work / "settings.ini"), QSettings.Format.IniFormat))
    for key, value in (("remember_position", False), ("tool_tips", False), ("help_badges", "never"),
                       ("author", "Andrea"), ("toolbar_tools", TOOLBAR), ("side_panel", "always"),
                       ("on_start", "home")):
        settings.set(key, value)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1440, 880)
    window.move(200, 120)
    window.show()
    QTest.qWaitForWindowExposed(window)
    for path, *_ in others:  # the home page lists them
        window.open_file(path)
        settle(300)
    settings.set("starred", [str(lecture.resolve()), str(others[1][0].resolve())])
    settings.set("file_tags", {str(lecture.resolve()): ["Energy", "Exam"], str(others[0][0].resolve()): ["Exam"]})
    window.open_file(lecture)
    settle(600)
    workspace = window.current_workspace()
    view = workspace.view
    mark_up(workspace, mpp)
    tools = workspace.tool_settings
    tools.update_style("pen", color=(0.15, 0.4, 0.95), pen="pro")

    def show_page(index: int, zoom: float, down: int = 0) -> None:
        view.set_zoom(zoom)
        view.goto_page(index)
        settle(200)
        if down:
            view.verticalScrollBar().setValue(view.verticalScrollBar().value() + down)
        settle(900)

    for theme in ("dark", "light"):
        settings.theme = theme
        dark = theme == "dark"
        settle(300)
        workspace.side.show_panel("pages")
        tools.set_tool("pen")
        show_page(1 if dark else 0, 1.15, 70)
        save(to_pil(window.grab()), out, f"editor-{theme}.png", dark)

        tools.set_tool("pen")
        workspace.toolbar.open_menu("pen")
        settle(300)
        menu = workspace.toolbar.tool_menu
        save(with_popups(window, [menu]), out, f"tool-menu-{theme}.png", dark)
        menu.close_menu()
        settle()

        if dark:
            workspace.show_tool_chooser()
            settle(300)
            chooser = workspace._chooser
            save(with_popups(window, [chooser]), out, "tool-chooser.png", dark)
            chooser.close()
            settle()

            window.show_assistant()
            panel = window._assistant
            panel.search.setText("come si ruota una pagina")
            settle(200)
            save(with_popups(window, [panel]), out, "assistant.png", dark)
            panel.close()
            settle()

            workspace.toggle_page_manager()
            for _ in range(8):  # each paint asks for the thumbnails still missing; they come back drawn later
                window.grab()
                settle(1200)
            save(to_pil(window.grab()), out, "page-manager.png", dark)
            workspace.toggle_page_manager()
            settle()

            show_page(4, 1.25, 30)
            window.reading_mode.enter()
            settle(700)
            save(to_pil(window.grab()), out, "reading-mode.png", dark)
            window.reading_mode.leave()
            workspace.toast.hide()  # its "Esc brings the bars back" belongs to reading mode only
            settle(300)

            dialog = writing_dialogs.StudyNotesDialog(window, writing_dialogs.marks_of(view),
                                                      "Study notes: Photovoltaic Systems")
            dialog.show()
            settle(300)
            show_page(0, 1.0)
            save(dimmed(to_pil(window.grab()), to_pil(dialog.grab())), out, "study-notes.png", dark)
            dialog.close()
            dialog.deleteLater()

            spelling = writing_dialogs.SpellingDialog(window, "Teh charge controller protects the the battery "
                                                              "from overcharging ,and the inverter  turns it "
                                                              "into AC .then the loads run.")
            spelling.show()
            settle(300)
            save(dimmed(to_pil(window.grab()), to_pil(spelling.grab())), out, "spelling.png", dark)
            spelling.close()
            spelling.deleteLater()

        window.show_home()
        settle(1500)
        save(to_pil(window.grab()), out, f"home-{theme}.png", dark)
        window._show_tab(window.workspaces.index(workspace))
        settle(300)

    settings.theme = "dark"
    settle(300)
    settings_window = window.settings_window()
    settings_window.resize(1100, 760)
    settings_window.show()
    settings_window.show_category("view")
    settle(400)
    save(to_pil(settings_window.grab()), out, "settings.png", True)
    settings_window.hide()

    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


if __name__ == "__main__":
    main()
