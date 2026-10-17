"""Side panel listing every annotation by page, with export to CSV or plain text."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.annotations import Annotation, Attachment, FreeText, ImageStamp, Link, Measurement, TextMarkup
from ..core.document import Document
from ..core.i18n import tr
from ..core.store import AnnotationStore
from ..icons import icon, readable
from ..theme import qcolor, themes
from . import messages
from .widgets import IconButton, muted_label, panel_title

ANNOTATION_ROLE = Qt.ItemDataRole.UserRole + 1
HEADER_ROLE = Qt.ItemDataRole.UserRole + 2  # a page heading: its page index
ICONS = {
    "Highlight": "z-text-highlight",
    "Underline": "text-underline",
    "StrikeOut": "text-strikethrough",
    "Square": "square",
    "Circle": "circle",
    "Line": "line-segment",
    "Ink": "pencil-simple",
    "FreeText": "text-t",
    "Text": "note",
    "Stamp": "image",
    "Redact": "eye-slash",
    "Squiggly": "z-squiggle",
    "PolyLine": "line-segments",
    "Polygon": "hexagon",
    "Link": "link",
    "FileAttachment": "paperclip",
}
LABEL_ICONS = {  # where one PDF type covers several tools
    "Arrow": "arrow-up-right", "Cloud": "cloud", "Highlighter": "marker-circle", "Callout": "chat-teardrop-text",
    "Tape": "z-tape", "Distance": "ruler", "Perimeter": "path", "Area": "bounding-box", "Equation": "z-equation",
    "Audio note": "microphone",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def icon_name(a: Annotation) -> str:
    return LABEL_ICONS.get(a.label) or ICONS.get(a.kind, "line-segment")


def icon_color(a: Annotation) -> str:
    """The annotation's own colour, made to stand out on the panel: black turns grey on the dark one."""
    t = themes.current
    return readable(a.color, t.surface, t.text)


def when(moment: datetime | None, now: datetime | None = None) -> str:
    """ "Today 14:05", "12 Oct 14:05" this year, "12 Oct 2024" before: in English, whatever Windows speaks."""
    if moment is None:
        return ""
    now = now or datetime.now(moment.tzinfo)
    clock = f"{moment.hour:02d}:{moment.minute:02d}"
    if moment.date() == now.date():
        return f"Today {clock}"
    day = f"{moment.day} {MONTHS[moment.month - 1]}"
    return f"{day} {clock}" if moment.year == now.year else f"{day} {moment.year}"


class AnnotationDelegate(QStyledItemDelegate):
    def __init__(self, panel: AnnotationsPanel):
        super().__init__(panel)
        self._panel = panel

    def sizeHint(self, option, index) -> QSize:
        if index.data(HEADER_ROLE) is not None:
            return QSize(200, option.fontMetrics.height() + 18)
        return QSize(200, 2 * option.fontMetrics.height() + 16)

    def paint(self, painter: QPainter, option, index) -> None:
        t = themes.current
        page = index.data(HEADER_ROLE)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if page is not None:
            font = QFont(option.font)
            font.setPointSizeF(max(font.pointSizeF() - 1, 7.5))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(t.text_muted))
            painter.drawText(option.rect.adjusted(8, 10, -6, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             tr(f"PAGE {page + 1}"))
            painter.restore()
            return

        a: Annotation = index.data(ANNOTATION_ROLE)
        rect = option.rect.adjusted(6, 2, -6, -2)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.selection))
            painter.drawRoundedRect(rect, 6, 6)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(t.hover))
            painter.drawRoundedRect(rect, 6, 6)

        icon(icon_name(a), icon_color(a)).paint(painter, QRect(rect.x() + 8, rect.y() + 8, 18, 18))

        metrics = option.fontMetrics
        x = rect.x() + 34
        width = rect.right() - x - 6
        painter.setPen(QColor(t.text))
        title = a.subject if a.subject and a.subject != a.label else a.label
        painter.drawText(QRect(x, rect.y() + 6, width, metrics.height()), 0,
                         metrics.elidedText(tr(title), Qt.TextElideMode.ElideRight, width))
        summary = " ".join(self._panel.summary(a).split())
        if not summary:  # nothing written on it: who made it, and when
            summary = " · ".join(part for part in (a.author, self._panel.date_text(a)) if part) or "No comment"
        painter.setPen(QColor(t.text_muted))
        painter.drawText(QRect(x, rect.y() + 6 + metrics.height(), width, metrics.height()), 0,
                         metrics.elidedText(summary, Qt.TextElideMode.ElideRight, width))
        painter.restore()


class AnnotationsPanel(QWidget):
    annotation_activated = Signal(str)

    def __init__(self, document: Document, store: AnnotationStore, parent=None):
        super().__init__(parent)
        self._document = document
        self._store = store
        self._marked_text: dict[tuple, str] = {}
        self._touched: dict[str, datetime] = {}  # annotations added or changed since the document was read

        export = IconButton("export", "Export annotations (CSV or text)", icon_size=18, help="export_annotations")
        export.clicked.connect(self.export)
        header = QHBoxLayout()
        header.addWidget(panel_title("Annotations"))
        header.addStretch()
        header.addWidget(export)

        # Links are mostly the document's own (a table of contents, page references): off unless asked for.
        self.links_chip = QToolButton()
        self.links_chip.setCheckable(True)
        self.links_chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.links_chip.setIconSize(QSize(14, 14))
        self.links_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.links_chip.setToolTip(tr("Show the document's links in the list"))
        self.links_chip.toggled.connect(self.refresh)
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.addWidget(self.links_chip)
        chips.addStretch()

        self.list = QListWidget()
        self.list.setItemDelegate(AnnotationDelegate(self))
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._clicked)
        self.empty = muted_label("No annotations yet. Pick a tool in the toolbar above the page.")
        self.empty.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 6, 0)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(chips)
        layout.addWidget(self.empty)
        layout.addWidget(self.list)

        # Several store signals can fire for one edit; rebuild once afterwards.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        store.added.connect(self._on_touched)
        store.changed.connect(self._on_changed)
        store.removed.connect(self._refresh_timer.start)
        store.reset.connect(self._on_reset)
        themes.changed.connect(self._style_chip)
        self._style_chip()
        self.refresh()

    def _style_chip(self, *_) -> None:
        t = themes.current
        self.links_chip.setIcon(icon("link", t.text_muted))
        self.links_chip.setStyleSheet(
            f"QToolButton {{ border: 1px solid {t.border}; border-radius: 12px; padding: 3px 10px; color: {t.text_muted}; }}"
            f"QToolButton:hover {{ background: {t.hover}; color: {t.text}; }}"
            f"QToolButton:checked {{ background: {t.selection}; border-color: {t.accent}; color: {t.text}; }}")

    def _on_touched(self, annotation: Annotation) -> None:
        self._touched[annotation.id] = datetime.now().astimezone()
        self._refresh_timer.start()

    def _on_changed(self, _old: Annotation, new: Annotation) -> None:
        self._on_touched(new)

    def _on_reset(self) -> None:
        self._marked_text.clear()  # pages moved: the text under a markup may differ now
        self._refresh_timer.start()

    def _clicked(self, item: QListWidgetItem) -> None:
        annotation = item.data(ANNOTATION_ROLE)
        if annotation is not None:
            self.annotation_activated.emit(annotation.id)

    def ordered(self) -> list[Annotation]:
        """What the list shows, page by page, top to bottom: links only when their chip is on."""
        def position(a: Annotation):
            x, y, _, _ = a.bounds()
            return a.page, round(y), x

        shown = [a for a in self._store.all() if self.links_chip.isChecked() or not isinstance(a, Link)]
        return sorted(shown, key=position)

    def refresh(self) -> None:
        self.list.clear()
        annotations = self.ordered()
        links = sum(1 for a in self._store.all() if isinstance(a, Link))
        self.links_chip.setText(tr(f"Links ({links})"))
        self.links_chip.setVisible(links > 0)
        page = None
        for annotation in annotations:
            if annotation.page != page:
                page = annotation.page
                heading = QListWidgetItem()
                heading.setData(HEADER_ROLE, page)
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(tr(heading))
            item = QListWidgetItem()
            item.setData(ANNOTATION_ROLE, annotation)
            item.setToolTip(annotation.contents[:500] if annotation.contents else "")
            self.list.addItem(item)
        self.empty.setVisible(not annotations)
        self.list.setVisible(bool(annotations))

    def marked_text(self, a: Annotation) -> str:
        """The document text under a text markup."""
        if not isinstance(a, TextMarkup):
            return ""
        key = (a.page, a.quads)
        if key not in self._marked_text:
            self._marked_text[key] = self._document.text_in(a.page, a.quads)
        return self._marked_text[key]

    def summary(self, a: Annotation) -> str:
        """What the entry says under its name: the comment, else what the annotation itself holds."""
        if a.contents:
            return a.contents
        if isinstance(a, TextMarkup):
            return self.marked_text(a)
        if isinstance(a, Measurement):
            return a.text()
        if isinstance(a, Link):
            return a.uri or (f"Goes to page {a.target + 1}" if a.target >= 0 else "")
        if isinstance(a, Attachment):
            return a.file_name
        if isinstance(a, ImageStamp) and a.equation:
            return a.equation
        return ""

    def date_text(self, a: Annotation) -> str:
        moment = self._touched.get(a.id) or self._document.modified(a.id)
        return when(moment)

    def export(self) -> None:
        annotations = self.ordered()
        if not annotations:
            messages.inform(self, "Export annotations", "There are no annotations to export.")
            return
        default = str(self._document.path.with_name(f"{self._document.path.stem} annotations.csv"))
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export annotations", default, "CSV spreadsheet (*.csv);;Plain text (*.txt)"
        )
        if not path:
            return
        self.write_export(path, plain=path.lower().endswith(".txt") or "txt" in chosen)

    def export_rows(self) -> list[tuple]:
        """(page, type, author, date, text, comment) for each annotation listed."""
        rows = []
        for a in self.ordered():
            if isinstance(a, TextMarkup):
                text = self.marked_text(a)
            elif isinstance(a, FreeText):
                text = a.contents
            elif isinstance(a, Measurement):
                text = a.text()  # the value is the point of a measurement
            else:
                text = ""
            comment = a.contents if not isinstance(a, FreeText) else ""
            rows.append((a.page + 1, a.label, a.author, self.date_text(a), text, comment))
        return rows

    def write_export(self, path: str, plain: bool = False) -> None:
        rows = self.export_rows()
        if plain:
            lines = []
            for page, label, author, date, text, comment in rows:
                lines.append(" · ".join(part for part in (f"Page {page}", label, author, date) if part))
                if text:
                    lines.append(f'"{text}"')
                if comment:
                    lines.append(f"Comment: {comment}")
                lines.append("")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        else:
            # utf-8-sig so Excel recognises the encoding of accented text.
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(("Page", "Type", "Author", "Date", "Text", "Comment"))
                writer.writerows(rows)
