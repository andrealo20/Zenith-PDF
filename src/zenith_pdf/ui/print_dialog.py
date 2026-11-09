"""Printing with a preview: each sheet as it will come out of the printer, and the choices that change it.

Windows' own print dialog shows no pages for a Qt app, so the preview is drawn here, by the same code that prints:
paint_sheet() lays the pages of one sheet out for both. The choices a driver keeps for itself are here too (two-sided
printing, the paper tray, colour); Printer settings... still opens the system's dialog for anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QMarginsF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..icons import icon
from ..theme import outline, themes
from . import messages, motion
from .pdf_tools import ToolForm
from .widgets import IconButton

if TYPE_CHECKING:
    from ..settings import Settings
    from .document_view import DocumentView

PREVIEW_SIZE = QSize(380, 480)  # the least room for the sheet
SHEET_MARGIN = 18  # px of canvas round the sheet in the preview
OPTIONS_WIDTH = 350
CACHED = 12  # preview pictures kept, so going back and forth does not render again
GAP = 8.0  # points between the pages of a sheet with several
GRIDS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 9: (3, 3), 16: (4, 4)}  # pages across the short, the long side
QUALITY = {"draft": 150, "standard": 300, "high": 600}  # dpi of the pages as images: more only makes huge spool files
MARGINS = {"default": None, "narrow": 6.35, "normal": 12.7, "wide": 25.4}  # mm all round; None: the printer's least
DUPLEX = {"off": QPrinter.DuplexMode.DuplexNone, "long": QPrinter.DuplexMode.DuplexLongSide,
          "short": QPrinter.DuplexMode.DuplexShortSide}
TRAYS = {  # Qt's names of the paper sources, as people call them
    QPrinter.PaperSource.Auto: "Automatic", QPrinter.PaperSource.Upper: "Upper tray",
    QPrinter.PaperSource.Middle: "Middle tray", QPrinter.PaperSource.Lower: "Lower tray",
    QPrinter.PaperSource.Manual: "Manual feed", QPrinter.PaperSource.Envelope: "Envelope",
    QPrinter.PaperSource.EnvelopeManual: "Envelope, manual feed", QPrinter.PaperSource.Tractor: "Tractor feed",
    QPrinter.PaperSource.SmallFormat: "Small format", QPrinter.PaperSource.LargeFormat: "Large format",
    QPrinter.PaperSource.LargeCapacity: "Large capacity", QPrinter.PaperSource.Cassette: "Cassette",
    QPrinter.PaperSource.FormSource: "Form source", QPrinter.PaperSource.OnlyOne: "Only tray",
}


@dataclass(frozen=True)
class PrintOptions:
    pages: tuple[int, ...] = ()  # indices, in the order chosen
    scaling: str = "fit"  # "fit" to the paper, "shrink" pages too large, "actual" size, or "custom"
    percent: float = 100.0  # the size with "custom"
    per_sheet: int = 1  # pages on one side of the paper, a key of GRIDS
    turn: bool = True  # a landscape page on upright paper (or the other way) is turned a quarter to fill it
    borders: bool = False  # a thin line round each page of a sheet with several
    reverse: bool = False  # the last sheet first, so the pile comes out in order on some printers
    annotations: bool = True
    gray: bool = False
    quality: str = "standard"  # a key of QUALITY

    def sheets(self) -> list[tuple[int, ...]]:
        """The pages of each sheet, in the order they print."""
        sheets = [self.pages[i:i + self.per_sheet] for i in range(0, len(self.pages), self.per_sheet)]
        return sheets[::-1] if self.reverse else sheets


def placement(page: tuple[float, float], area: QSizeF, scaling: str = "fit", percent: float = 100.0,
              turn: bool = False) -> tuple[QRectF, bool]:
    """Where a page `page` points large goes in `area` (points, from its top left corner), and whether it is turned
    a quarter: when `turn` is on and the page lies the other way from the area. Fitted, shrunk only if too large,
    at its own size, or at `percent`; centred."""
    width, height = (max(side, 1.0) for side in page)
    turned = turn and width != height and (width > height) != (area.width() > area.height())
    if turned:
        width, height = height, width
    fit = min(area.width() / width, area.height() / height)
    scale = {"fit": fit, "shrink": min(fit, 1.0), "actual": 1.0}.get(scaling, percent / 100)
    return QRectF((area.width() - width * scale) / 2, (area.height() - height * scale) / 2, width * scale,
                  height * scale), turned


def cells(area: QSizeF, per_sheet: int) -> list[QRectF]:
    """The places of the pages on a sheet with `per_sheet` of them, left to right and top to bottom: more along the
    long side, as two upright pages go side by side on a landscape sheet."""
    short, long = GRIDS.get(per_sheet, (1, 1))
    columns, rows = (long, short) if area.width() > area.height() else (short, long)
    width = (area.width() - GAP * (columns - 1)) / columns
    height = (area.height() - GAP * (rows - 1)) / rows
    return [QRectF(column * (width + GAP), row * (height + GAP), width, height)
            for row in range(rows) for column in range(columns)]


def sheet_orientation(page: tuple[float, float], per_sheet: int) -> QPageLayout.Orientation:
    """The way a sheet lies for pages like `page`: as the page, or across it for 2 or 6 to a sheet."""
    width, height = page
    landscape = (width > height) != (per_sheet in (2, 6))
    return QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait


def paint_sheet(painter: QPainter, area: QRectF, unit: float, pages: tuple[int, ...], options: PrintOptions,
                page_size: Callable[[int], tuple[float, float]],
                picture: Callable[[int, float, float], QImage]) -> None:
    """Lay the pages of one sheet out in `area`, the printable part of the paper in the painter's units, at `unit`
    of them to a point. `picture(index, scale, fit)` gives the page as an image, for `scale` (and, fitted to its
    place, `fit`) of its points to a point of paper."""
    room = QSizeF(area.width() / unit, area.height() / unit)
    scaling = options.scaling if options.per_sheet == 1 else "fit"
    for index, cell in zip(pages, cells(room, options.per_sheet)):
        size = page_size(index)
        place, turned = placement(size, cell.size(), scaling, options.percent, options.turn)
        fitted, _ = placement(size, cell.size(), "fit", turn=options.turn)
        scale = (place.height() if turned else place.width()) / max(size[0], 1.0)
        fit = (fitted.height() if turned else fitted.width()) / max(size[0], 1.0)
        image = picture(index, scale, fit)
        target = QRectF(area.left() + (cell.left() + place.left()) * unit,
                        area.top() + (cell.top() + place.top()) * unit, place.width() * unit, place.height() * unit)
        painter.save()
        painter.setClipRect(QRectF(area.left() + cell.left() * unit, area.top() + cell.top() * unit,
                                   cell.width() * unit, cell.height() * unit))
        if turned:  # a quarter anticlockwise, as a printer turns a landscape page: its top to the paper's left
            painter.translate(target.center())
            painter.rotate(-90)
            painter.drawImage(QRectF(-target.height() / 2, -target.width() / 2, target.height(), target.width()),
                              image)
        else:
            painter.drawImage(target, image)
        painter.restore()
        if options.borders and options.per_sheet > 1:
            painter.save()
            painter.setPen(QPen(QColor(0, 0, 0), max(unit * 0.5, 1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target)
            painter.restore()


def _finished(image: QImage, gray: bool) -> QImage:
    return image.convertToFormat(QImage.Format.Format_Grayscale8) if gray else image


class SheetPreview(QWidget):
    """One sheet of paper as the printer will print it."""

    def __init__(self, dialog: PrintDialog):
        super().__init__()
        self._dialog = dialog
        self._pictures: dict[tuple, QImage] = {}
        self.setMinimumSize(PREVIEW_SIZE)
        self.setAccessibleName(tr("Preview"))

    def wheelEvent(self, event) -> None:
        self._dialog.turn(-1 if event.angleDelta().y() > 0 else 1)

    def paintEvent(self, event) -> None:
        t = themes.current
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.canvas))
        painter.drawPath(outline(QRectF(self.rect()), t.look.card, t.look))  # the style's corners, as the fields have
        layout = self._dialog.printer.pageLayout()
        full = layout.fullRect(QPageLayout.Unit.Point)
        room = QRectF(self.rect()).adjusted(SHEET_MARGIN, SHEET_MARGIN, -SHEET_MARGIN, -SHEET_MARGIN)
        zoom = min(room.width() / full.width(), room.height() / full.height())
        sheet = QRectF(room.center().x() - full.width() * zoom / 2, room.center().y() - full.height() * zoom / 2,
                       full.width() * zoom, full.height() * zoom)
        if t.look.shadow == "soft":  # a little depth under the paper, where the style has shadows
            for lift, alpha in ((4, 18), (2, 34)):
                painter.setBrush(QColor(0, 0, 0, alpha))
                painter.drawRect(sheet.translated(0, lift).adjusted(-1, -1, 1, 1))
        painter.setPen(QPen(QColor(t.border), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(sheet.adjusted(-0.5, -0.5, 0.5, 0.5))
        pages = self._dialog.shown_sheet()
        if not pages:
            painter.setPen(QColor(t.text_muted))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No pages to print"))
            return
        paint = layout.paintRect(QPageLayout.Unit.Point)
        area = QRectF(sheet.left() + paint.left() * zoom, sheet.top() + paint.top() * zoom, paint.width() * zoom,
                      paint.height() * zoom)
        options, ratio, view = self._dialog.options(), self.devicePixelRatioF(), self._dialog.view

        def picture(index: int, scale: float, fit: float) -> QImage:
            pixels = min(scale, fit * 2) * zoom * ratio  # a poster at its own size: no larger than twice fitted
            key = (index, round(pixels, 3), options.annotations, options.gray)
            image = self._pictures.get(key)
            if image is None:
                image = _finished(view.page_image(index, pixels, options.annotations), options.gray)
                if len(self._pictures) >= CACHED:
                    self._pictures.pop(next(iter(self._pictures)))
                self._pictures[key] = image
            return image

        paint_sheet(painter, area, zoom, pages, options, view.document.page_size, picture)


class PrintDialog(ToolForm):
    """The printer, the pages and how they sit on the paper, beside a preview of each sheet."""

    def __init__(self, printer: QPrinter, view: DocumentView, current: int, parent: QWidget,
                 settings: Settings | None = None):
        super().__init__("Print", "", parent, action="Print")
        self.printer, self.view, self._settings = printer, view, settings
        saved = self._saved()
        self._sheet = 0  # which of the sheets the preview shows
        self._pages: tuple[int, ...] = ()
        names = QPrinterInfo.availablePrinterNames()
        default = saved.get("printer") if saved.get("printer") in names else QPrinterInfo.defaultPrinterName()
        self._printer_box = self.choose("printer", "Printer", {name: name for name in names},
                                        default if default in names else None)
        self._copies = self.number("copies", "Copies", 1, 999, 1)
        self._page_box, self._page_pick = self.pages("pages", view.document.page_count, current)
        self._duplex = self.choose("duplex", "Two-sided",
                                   {"off": "One-sided", "long": "Flip on long edge", "short": "Flip on short edge"},
                                   saved.get("duplex", "off"))
        self._turn_box = self.choose("orientation", "Orientation",
                                     {"auto": "Automatic", "portrait": "Portrait", "landscape": "Landscape"})
        self._per_sheet = self.choose("per_sheet", "Pages per sheet", {n: str(n) for n in GRIDS},
                                      saved.get("per_sheet", 1))
        self._scaling = self.choose("scaling", "Size", {"fit": "Fit to paper", "shrink": "Shrink large pages",
                                                         "actual": "Actual size", "custom": "Custom"},
                                    saved.get("scaling", "fit"))
        self._percent = self.number("percent", "Scale", 10, 400, int(saved.get("percent", 100)), " %")
        self._gray = self.check("gray", "Black and white", bool(saved.get("gray", False)))

        # More settings, folded away until wanted: rows of the same form, shown or hidden together.
        self._more = QToolButton()
        self._more.setObjectName("Link")
        self._more.setText(tr("More settings"))
        self._more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._form.addRow(self._more)
        first = self._form.rowCount()
        self._paper = self.choose("paper", "Paper", {})
        self._tray = self.choose("tray", "Paper tray", {})
        self._margins = self.choose("margins", "Margins", {"default": "Printer minimum", "narrow": "Narrow",
                                                            "normal": "Normal", "wide": "Wide"},
                                    saved.get("margins", "default"))
        self._quality = self.choose("quality", "Quality", {"draft": "Draft", "standard": "Standard",
                                                            "high": "High"}, saved.get("quality", "standard"))
        self._annotations = self.check("annotations", "Annotations", bool(saved.get("annotations", True)))
        self._turn = self.check("turn", "Turn pages to fit the paper", bool(saved.get("turn", True)))
        self._borders = self.check("borders", "Page borders", bool(saved.get("borders", False)))
        self._reverse = self.check("reverse", "Reverse order", bool(saved.get("reverse", False)))
        self._collate = self.check("collate", "Collate copies", bool(saved.get("collate", True)))
        system = QPushButton(tr("Printer settings…"))
        system.setObjectName("Link")
        system.setCursor(Qt.CursorShape.PointingHandCursor)
        system.clicked.connect(self._system_dialog)
        self._form.addRow("", system)
        self._more_rows = range(first, self._form.rowCount())
        # The form makes the labels of its rows itself: held here, or Python frees the wrappers the fades paint with.
        self._more_widgets = self._widgets_of(self._more_rows)
        self._more.clicked.connect(lambda: self._show_more(not self._more_shown))
        self._show_more(bool(saved.get("more", False)))
        if not names:
            self.note("No printer found")
            system.setEnabled(False)
            self.button("ok").setEnabled(False)

        # The preview at the left of the choices, which scroll when they are taller than the window.
        self.preview = SheetPreview(self)
        self._where = QLabel()
        self._where.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._where.setMinimumWidth(80)
        back, forth = IconButton("caret-left", "Previous page"), IconButton("caret-right", "Next page")
        back.clicked.connect(lambda: self.turn(-1))
        forth.clicked.connect(lambda: self.turn(1))
        steps = QHBoxLayout()
        steps.addStretch()
        for widget in (back, self._where, forth):
            steps.addWidget(widget)
        steps.addStretch()
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.preview, 1)
        left.addLayout(steps)
        self._fields.removeWidget(self._box)
        scroll = QScrollArea()
        scroll.setWidget(self._box)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(OPTIONS_WIDTH)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Tab goes control to control, and brings each into view
        scroll.viewport().setAutoFillBackground(False)
        self._box.setAutoFillBackground(False)
        row = QHBoxLayout()
        row.setSpacing(24)
        row.addLayout(left, 1)
        row.addWidget(scroll)
        self._fields.addLayout(row, 1)

        self._fill_printer()
        self._printer_box.currentIndexChanged.connect(self._fill_printer)
        for box in (self._paper, self._tray, self._turn_box, self._scaling, self._page_box, self._duplex,
                    self._per_sheet, self._margins, self._quality):
            box.currentIndexChanged.connect(self._refresh)
        for check in (self._annotations, self._gray, self._turn, self._borders, self._reverse, self._collate):
            check.toggled.connect(self._refresh)
        self._page_pick.textChanged.connect(self._refresh)
        for spin in (self._copies, self._percent):
            spin.valueChanged.connect(self._refresh)
        self._refresh()

    # What is chosen ---------------------------------------------------------------------

    def options(self) -> PrintOptions:
        return PrintOptions(self._pages, self._scaling.currentData(), self._percent.value(),
                            self._per_sheet.currentData() or 1, self._turn.isChecked(), self._borders.isChecked(),
                            self._reverse.isChecked(), self._annotations.isChecked(), self._gray.isChecked(),
                            self._quality.currentData())

    def shown_sheet(self) -> tuple[int, ...]:
        sheets = self.options().sheets()
        return sheets[self._sheet] if sheets else ()

    def turn(self, step: int) -> None:
        """Show the next (1) or the previous (-1) sheet."""
        count = len(self.options().sheets())
        if count:
            self._sheet = max(0, min(self._sheet + step, count - 1))
            self._show_where()
            self.preview.update()

    def keyPressEvent(self, event) -> None:
        steps = {Qt.Key.Key_PageDown: 1, Qt.Key.Key_PageUp: -1}
        if event.key() in steps:
            self.turn(steps[event.key()])
            return
        super().keyPressEvent(event)

    def _widgets_of(self, rows) -> list[QWidget]:
        """The widgets of these rows of the form, each once: a row that spans it gives its one widget for two roles."""
        found: list[QWidget] = []
        for row in rows:
            for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole,
                         QFormLayout.ItemRole.SpanningRole):
                item = self._form.itemAt(row, role)
                if item is not None and item.widget() is not None and item.widget() not in found:
                    found.append(item.widget())
        return found

    def _show_more(self, shown: bool) -> None:
        self._more_shown = shown
        for row in self._more_rows:
            self._form.setRowVisible(row, shown)
        if shown:  # the rows that were folded away come in, each in its place
            for widget in self._more_widgets:
                motion.appear(widget, 4)
        self._more.setIcon(icon("caret-down" if shown else "caret-right"))

    def _saved(self) -> dict:
        saved = self._settings.get("print_options") if self._settings is not None else {}
        return saved if isinstance(saved, dict) else {}

    def _fill_printer(self, *_) -> None:
        """What the printer picked can do: its paper sizes, its trays, two-sided printing, colour."""
        name = self._printer_box.currentData()
        if name:
            self.printer.setPrinterName(name)
        info = QPrinterInfo.printerInfo(name) if name else None
        known = info is not None and not info.isNull()
        sizes = info.supportedPageSizes() if known else []
        default = info.defaultPageSize() if known else self.printer.pageLayout().pageSize()
        saved = self._saved().get("paper")
        self._paper.blockSignals(True)
        self._paper.clear()
        for size in sizes or [default]:
            self._paper.addItem(size.name(), size)
        keys = [size.key() for size in sizes]
        self._paper.setCurrentIndex(keys.index(saved) if saved in keys else
                                    next((i for i, size in enumerate(sizes) if size.isEquivalentTo(default)), 0))
        self._paper.blockSignals(False)

        trays = [source for source in self.printer.supportedPaperSources() if source in TRAYS] if name else []
        self._tray.blockSignals(True)
        self._tray.clear()
        for source in trays or [QPrinter.PaperSource.Auto]:
            self._tray.addItem(tr(TRAYS[source]), source)
        self._tray.setEnabled(len(trays) > 1)
        self._tray.blockSignals(False)

        duplex = info.supportedDuplexModes() if known else []
        two_sided = any(mode in duplex for mode in (QPrinter.DuplexMode.DuplexLongSide,
                                                    QPrinter.DuplexMode.DuplexAuto))
        self._duplex.setEnabled(two_sided)
        if not two_sided:
            self._duplex.setCurrentIndex(self._duplex.findData("off"))
        colours = info.supportedColorModes() if known else []
        if colours and QPrinter.ColorMode.Color not in colours:  # a black-and-white printer
            self._gray.setChecked(True)
        self._refresh()

    def _refresh(self, *_) -> None:
        """The printer set as chosen, and the preview drawn again."""
        try:
            self._pages = tuple(self._getters["pages"]())
        except ValueError:
            self._pages = ()  # a range being written
        per_sheet = self._per_sheet.currentData() or 1
        self._scaling.setEnabled(per_sheet == 1)  # several pages to a sheet are each fitted to their place
        self._percent.setEnabled(per_sheet == 1 and self._scaling.currentData() == "custom")
        self._borders.setEnabled(per_sheet > 1)
        self._collate.setEnabled(self._copies.value() > 1)
        printer = self.printer
        paper = self._paper.currentData()
        if isinstance(paper, QPageSize):
            printer.setPageSize(paper)
        choice = self._turn_box.currentData()
        if choice == "auto":
            first = self._pages[0] if self._pages else 0
            printer.setPageOrientation(sheet_orientation(self.view.document.page_size(first), per_sheet))
        else:
            printer.setPageOrientation(QPageLayout.Orientation.Landscape if choice == "landscape" else
                                       QPageLayout.Orientation.Portrait)
        margins = MARGINS.get(self._margins.currentData())
        if margins is None:
            layout = printer.pageLayout()
            printer.setPageMargins(layout.minimumMargins(), layout.units())
        else:
            printer.setPageMargins(QMarginsF(margins, margins, margins, margins), QPageLayout.Unit.Millimeter)
        tray = self._tray.currentData()
        if tray is not None and self._tray.isEnabled():
            printer.setPaperSource(tray)
        printer.setDuplex(DUPLEX.get(self._duplex.currentData(), QPrinter.DuplexMode.DuplexNone))
        printer.setCopyCount(self._copies.value())
        printer.setCollateCopies(self._collate.isChecked())
        printer.setColorMode(QPrinter.ColorMode.GrayScale if self._gray.isChecked() else QPrinter.ColorMode.Color)
        self._sheet = min(self._sheet, max(len(self.options().sheets()) - 1, 0))
        self._show_where()
        self.preview.update()

    def _show_where(self) -> None:
        count = len(self.options().sheets())
        self._where.setText(f"{self._sheet + 1} / {count}" if count else "")

    def _remember(self) -> None:
        """The choices for the next time, but the pages and the copies: those are this time's."""
        if self._settings is None:
            return
        options = self.options()
        paper = self._paper.currentData()
        self._settings.set("print_options", {
            "printer": self._printer_box.currentData(), "duplex": self._duplex.currentData(),
            "paper": paper.key() if isinstance(paper, QPageSize) else None, "margins": self._margins.currentData(),
            "more": self._more_shown, "collate": self._collate.isChecked(),
        } | {field.name: getattr(options, field.name) for field in fields(options) if field.name != "pages"})

    def _system_dialog(self) -> None:
        """Windows' (or the system's) own dialog, for what only the printer's driver knows; printing from it
        prints."""
        dialog = QPrintDialog(self.printer, self)
        for option in (QPrintDialog.PrintDialogOption.PrintPageRange, QPrintDialog.PrintDialogOption.PrintSelection,
                       QPrintDialog.PrintDialogOption.PrintCurrentPage):
            dialog.setOption(option, False)  # the pages are chosen here
        accepted = dialog.exec() == QPrintDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            self._refresh()  # the printer as it was chosen here
            return
        if self._pages:  # its choices win: out without setting the printer again
            self._remember()
            self._values = {"pages": list(self._pages)}
            super(ToolForm, self).accept()

    def accept(self) -> None:
        if not self._pages:
            messages.warn(self, self.windowTitle(), "Write pages like 1-3, 7.")
            return
        self._remember()
        super().accept()

    def run(self) -> PrintOptions | None:
        """The choices once Print is pressed, the printer set to them; None if the dialog was cancelled."""
        self.resize(self.sizeHint().expandedTo(QSize(860, 640)))
        return self.options() if super().run() is not None else None


def print_pages(parent: QWidget, printer, view: DocumentView, options: PrintOptions) -> None:
    """Print the sheets of `options` on `printer`, laid out as the preview shows them."""
    document = view.document
    sheets = options.sheets()
    dpi = min(printer.resolution(), QUALITY.get(options.quality, QUALITY["standard"]))
    progress = QProgressDialog(tr("Printing…"), tr("Cancel"), 0, len(sheets), parent)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    painter = QPainter()
    if not painter.begin(printer):
        progress.close()
        progress.deleteLater()
        messages.warn(parent, "Can't print", "The printer could not be started.")
        return

    def picture(index: int, scale: float, fit: float) -> QImage:
        # Never more pixels than the page fitted to its place: a poster page at its own size would not fit in memory.
        return _finished(view.page_image(index, min(scale, fit) * dpi / 72, options.annotations), options.gray)

    failure = None
    try:
        for count, pages in enumerate(sheets):
            if progress.wasCanceled():
                break
            progress.setValue(count)
            if count:
                printer.newPage()
            area = QRectF(painter.viewport())  # the printable part of the paper, in device pixels
            paint_sheet(painter, area, printer.resolution() / 72, pages, options, document.page_size, picture)
    except Exception as error:  # reported once the printer is let go
        failure = error
    finally:
        painter.end()  # a painter left open on the printer aborts the whole app, unsaved tabs and all
        progress.close()
        progress.deleteLater()
    if failure is not None:
        messages.warn(parent, "Can't print", f"{document.path.name} could not be printed.", detail=str(failure))
