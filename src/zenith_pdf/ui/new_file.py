"""The panel under the + button: open, new document, templates, Doc Builder, pinned folders."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.i18n import tr
from ..settings import Settings
from ..theme import themes
from .widgets import FloatingBar, IconButton, icon_pixmap

PANEL_WIDTH = 380


class Row(QFrame):
    """A large clickable row: icon, label, optional small buttons on the right."""

    clicked = Signal()

    def __init__(self, icon_name: str, text: str, accent: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 8, 9)
        layout.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(icon_pixmap(icon_name, 22, themes.current.accent if accent else None))
        label = QLabel(tr(text))
        label.setObjectName("PanelRowText")
        layout.addWidget(glyph)
        layout.addWidget(label, 1)
        self._trailing = layout

    def add_button(self, icon_name: str, tip: str, slot) -> None:
        button = IconButton(icon_name, tip, icon_size=16)
        button.clicked.connect(slot)
        self._trailing.addWidget(button)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


class _Header(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class Section(QWidget):
    """A titled group of rows that folds away under its header."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._header = _Header()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self.toggle)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(4, 6, 4, 2)
        label = QLabel(tr(title))
        label.setObjectName("SectionTitle")
        self._chevron = QLabel()
        header.addWidget(label, 1)
        header.addWidget(self._chevron)
        self._body = QWidget()
        self.rows = QVBoxLayout(self._body)
        self.rows.setContentsMargins(0, 2, 0, 4)
        self.rows.setSpacing(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._header)
        layout.addWidget(self._body)
        self._set(expanded)

    def _set(self, expanded: bool) -> None:
        self._body.setVisible(expanded)
        self._chevron.setPixmap(icon_pixmap("caret-down" if expanded else "caret-right", 14, themes.current.text_muted))

    def toggle(self) -> None:
        self._set(not self._body.isVisible())
        self.window().adjustSize()

    def add(self, row: Row) -> Row:
        self.rows.addWidget(row)
        return row


class NewFilePanel(FloatingBar):
    open_requested = Signal()
    open_in_folder = Signal(str)
    new_document = Signal()
    from_template = Signal(dict)
    edit_template = Signal(int)
    delete_template = Signal(int)
    new_template = Signal()
    builder_requested = Signal()
    pin_folder = Signal()
    unpin_folder = Signal(int)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent, popup=True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # a new one opens each time
        self.setFixedWidth(PANEL_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(6)

        new_file = Section("New file")
        new_file.add(Row("folder-open", "Open from device", accent=True)).clicked.connect(lambda: self._do(self.open_requested))
        new_file.add(Row("file", "New document")).clicked.connect(lambda: self._do(self.new_document))
        layout.addWidget(new_file)

        templates = Section("Saved templates")
        for position, template in enumerate(settings.templates):
            row = templates.add(Row("notebook", template["name"]))
            row.setToolTip(tr(f"{template['paper']} {template['orientation']}, {template['kind']}, {template['pages']} pages"))
            row.clicked.connect(lambda t=template: self._do(self.from_template, t))
            row.add_button("pencil-simple", "Edit template", lambda _=False, p=position: self._do(self.edit_template, p))
            row.add_button("x", "Delete template", lambda _=False, p=position: self._do(self.delete_template, p))
        templates.add(Row("plus-circle", "New template")).clicked.connect(lambda: self._do(self.new_template))
        layout.addWidget(templates)

        other = Section("Other")
        other.add(Row("squares-four", "Doc Builder", accent=True)).clicked.connect(lambda: self._do(self.builder_requested))
        layout.addWidget(other)

        pinned = Section("Pinned folders", expanded=bool(settings.pinned_folders))
        for position, folder in enumerate(settings.pinned_folders):
            row = pinned.add(Row("folder-simple", Path(folder).name or folder))
            row.setToolTip(folder)
            row.clicked.connect(lambda f=folder: self._do(self.open_in_folder, f))
            row.add_button("x", "Unpin", lambda _=False, p=position: self._do(self.unpin_folder, p))
        pinned.add(Row("push-pin", "Pin a folder…")).clicked.connect(lambda: self._do(self.pin_folder))
        layout.addWidget(pinned)

    def _do(self, signal, *args) -> None:
        self.close()
        signal.emit(*args)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        self.move(anchor.mapToGlobal(QPoint(0, anchor.height() + 6)))
        self.set_backdrop(anchor.window())
        self.show()
