"""Questions, messages and one-field prompts in the app's own style.

They stand in for the static QMessageBox and QInputDialog calls: a heading, a
muted explanation, an optional Phosphor icon, and verb buttons on the right,
the main one in the accent (or red when it destroys something). Enter picks
the main button, Esc cancels. Call them through the module, as
messages.ask(...), so tests can monkeypatch them.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ..core.i18n import tr
from ..icons import icon as theme_icon
from ..theme import themes
from . import motion
from .widgets import focus_ring, keyboard_focus

Button = tuple[str, str, str]  # (key, label, role): role "primary", "danger" or ""
CANCEL = "cancel"  # the key of a button that dismisses: the answer is None, as for Esc
OK_CANCEL: list[Button] = [("ok", "OK", "primary"), (CANCEL, "Cancel", "")]
MIN_WIDTH = 440
ICON_SIZE = 28
INT_RANGE = 2_147_483_647
RING_GAP = 3  # px from a control's edge to the outside of its focus ring: one of air, then the ring's two


class FocusRing(QWidget):
    """The keyboard focus ring of a dialog's buttons and tick boxes, drawn round them as the app's own controls draw
    theirs (widgets.focus_ring). Qt shows the focus of a button as a faint box inside it, or not at all under the
    style sheet; a ring outside also shows on the accent fill of the main button. It follows the focus only when the
    keyboard put it there, so a click leaves no ring."""

    TARGETS = (QPushButton, QToolButton, QCheckBox, QRadioButton)

    def __init__(self, dialog: QWidget):
        super().__init__(dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target: QWidget | None = None
        self._watched: list[QWidget] = []  # the control and what holds it: where any of them moves, the ring goes
        self.hide()
        dialog.setStyleSheet(f"{dialog.styleSheet()} QPushButton:focus, QToolButton:focus, QCheckBox:focus, "
                             "QRadioButton:focus { outline: 0; }")  # the box Qt draws inside, which the ring replaces
        QApplication.instance().focusChanged.connect(self._focused)
        themes.changed.connect(self.update)

    def _focused(self, _before, now) -> None:
        if now is self._target:
            self._place()
            return
        for widget in self._watched:
            if isValid(widget):
                widget.removeEventFilter(self)
        self._target, self._watched = None, []
        if isinstance(now, self.TARGETS) and now.window() is self.window():
            self._target = now
            holder = now
            while holder is not None and holder is not self.parentWidget():
                holder.installEventFilter(self)
                self._watched.append(holder)
                holder = holder.parentWidget()
        self._place()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self._place()
        return False

    def _place(self) -> None:
        target = self._target
        if target is None or not isValid(target) or not target.isVisible() or not keyboard_focus(target):
            self.hide()
            return
        whole = target.size()
        if target.visibleRegion().boundingRect().size() != whole:  # scrolled partly out of its area: no ring half cut
            self.hide()
            return
        size = whole
        if isinstance(target, (QCheckBox, QRadioButton)):  # a tick box's label ends where its text does, not its cell
            size = QSize(min(whole.width(), target.sizeHint().width()) + RING_GAP, whole.height())  # air after the word
        box = QRect(target.mapTo(self.parentWidget(), QPoint(0, 0)), size)
        self.setGeometry(box.adjusted(-RING_GAP, -RING_GAP, RING_GAP, RING_GAP))
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, event) -> None:
        look = themes.current.look
        if isinstance(self._target, QPushButton):
            radius = 15 if look.pill_buttons else look.control
        elif isinstance(self._target, QToolButton):
            radius = look.item
        else:
            radius = look.check
        painter = QPainter(self)
        focus_ring(painter, QRectF(self.rect()), radius + RING_GAP)


class _Recentre(QObject):
    """The entrance moves a dialog, which Qt takes for a place chosen on purpose: shown again, a dialog would open
    where it was instead of centring on its parent. When it goes, that mark goes with it."""

    def __init__(self, dialog: QDialog):
        super().__init__(dialog)
        self._dialog = dialog  # held to see when it is gone
        dialog.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        # Not for a minimised window (that is spontaneous), nor for a dialog being deleted: it is hidden first.
        if event.type() == QEvent.Type.Hide and not event.spontaneous() and isValid(self._dialog):
            motion.appear(self._dialog)  # hidden: it starts nothing, and ends an entrance that would go on moving it
            self._dialog.setAttribute(Qt.WidgetAttribute.WA_Moved, False)
        return False


def house(dialog: QDialog, rise: int = motion.RISE) -> None:
    """What every dialog of the app shares: it comes in as it is shown, the keyboard's focus shows as a ring, and its
    buttons are as tall as everywhere (the style sheet sizes them by the name MessageDialog)."""
    if not dialog.objectName():
        dialog.setObjectName("MessageDialog")
    motion.appear_on_show(dialog, rise)
    if dialog.findChild(FocusRing) is None:  # once, however many layers of a dialog ask
        FocusRing(dialog)
        _Recentre(dialog)


def style_progress(bar: QProgressBar) -> None:
    """The thin bar of a job's progress, in the colours of the theme."""
    t = themes.current
    rounding = min(3, t.look.item)
    bar.setStyleSheet(f"QProgressBar {{ background: {t.border}; border: none; border-radius: {rounding}px; }}"
                      f"QProgressBar::chunk {{ background: {t.accent}; border-radius: {rounding}px; }}")


class MessageDialog(QDialog):
    """The dialog behind every helper here. `choice` is the key of the button pressed, or None."""

    def __init__(self, parent: QWidget | None, title: str, text: str = "", buttons: list[Button] | None = None,
                 default: str | None = None, icon: str | None = None, detail: str = "", check: str = "",
                 icon_color: str | None = None):
        super().__init__(parent)
        self.setObjectName("MessageDialog")
        self.setWindowTitle(tr(title))
        self.setMinimumWidth(MIN_WIDTH)
        self.choice: str | None = None
        buttons = buttons or OK_CANCEL
        self.default_key = _default_key(buttons, default)
        t = themes.current

        heading = QLabel(tr(title))
        heading.setObjectName("DialogTitle")
        heading.setWordWrap(True)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        self._column = QVBoxLayout()
        self._column.setSpacing(8)
        self._column.addWidget(heading)
        if text:
            body = QLabel(tr(text))
            body.setObjectName("DialogBody")
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._column.addWidget(body)
        if detail:
            more = QLabel(tr(detail))
            more.setObjectName("DialogDetail")
            more.setWordWrap(True)
            more.setTextFormat(Qt.TextFormat.PlainText)
            more.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._column.addWidget(more)
        self._fields = QVBoxLayout()
        self._fields.setSpacing(8)
        self._column.addLayout(self._fields)

        primary = any(role == "primary" for _, _, role in buttons)
        top = QHBoxLayout()
        top.setSpacing(16)
        if icon:
            danger = not primary and any(role == "danger" for _, _, role in buttons)
            glyph = QLabel()
            glyph.setObjectName("DialogIcon")
            glyph.setFixedSize(ICON_SIZE, ICON_SIZE)
            color = icon_color or (t.danger if danger else t.accent)
            glyph.setPixmap(theme_icon(icon, color).pixmap(QSize(ICON_SIZE, ICON_SIZE), self.devicePixelRatioF()))
            top.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
            heading.setMinimumHeight(ICON_SIZE)  # centred on the icon
        top.addLayout(self._column, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.check_box: QCheckBox | None = None
        if check:
            self.check_box = QCheckBox(tr(check))
            self.check_box.setObjectName("Muted")
            row.addWidget(self.check_box)
        row.addStretch()
        self._buttons: dict[str, QPushButton] = {}
        for key, label, role in buttons:
            button = QPushButton(tr(label.replace("&", "&&")))
            button.setAutoDefault(False)
            if role == "primary":
                button.setObjectName("Primary")
            elif role == "danger":
                # Filled red when it is the main action, red text beside an accent one.
                button.setObjectName("DangerText" if primary else "Danger")
            button.setProperty("key", key)
            # A bound method, held weakly: a lambda kept by the button would keep the dialog alive too.
            button.clicked.connect(self._clicked)
            row.addWidget(button)
            self._buttons[key] = button
        chosen = self._buttons.get(self.default_key)
        if chosen is not None:
            chosen.setDefault(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)
        layout.addLayout(top)
        layout.addLayout(row)
        if chosen is not None:
            chosen.setFocus()  # once the button is in the dialog: before, the tick box took the focus instead
        house(self)

    def sizeHint(self) -> QSize:
        # Wrapped text guesses its height for a narrow width: measure it at the width the dialog opens with.
        width = max(super().sizeHint().width(), self.minimumWidth())
        return QSize(width, self.layout().heightForWidth(width))

    def add_field(self, widget: QWidget, full_width: bool = True) -> QWidget:
        """Put an input under the text, and give it the focus."""
        if full_width:
            self._fields.addWidget(widget)
        else:
            self._fields.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
        widget.setFocus()
        return widget

    def press(self, key: str | None = None) -> None:
        """Press a button (the main one by default) if it is enabled, as Enter does."""
        button = self._buttons.get(key or self.default_key)
        if button is not None and button.isEnabled():
            button.click()

    def eventFilter(self, watched, event) -> bool:
        # In a multi-line field Enter starts a line, so Ctrl+Enter confirms.
        if (event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.press()
            return True
        return super().eventFilter(watched, event)

    def button(self, key: str) -> QPushButton | None:
        return self._buttons.get(key)

    def is_checked(self) -> bool:
        return self.check_box is not None and self.check_box.isChecked()

    def _clicked(self, *_) -> None:
        self._choose(self.sender().property("key"))

    def _choose(self, key: str) -> None:
        if key == CANCEL:
            self.reject()
            return
        self.choice = key
        self.accept()

    def reject(self) -> None:
        self.choice = None
        super().reject()


def _default_key(buttons: list[Button], default: str | None) -> str | None:
    """The button Enter presses: the one asked for, else the first primary, danger, or any button."""
    keys = [key for key, _, _ in buttons]
    if default in keys:
        return default
    for wanted in ("primary", "danger"):
        for key, _, role in buttons:
            if role == wanted:
                return key
    return keys[0] if keys else None


def run(dialog: MessageDialog) -> tuple[str | None, bool]:
    """Show `dialog`; the key of the button pressed and whether its box is ticked."""
    return _run(dialog)


def _run(dialog: MessageDialog) -> tuple[str | None, bool]:
    dialog.exec()
    answer = dialog.choice, dialog.is_checked()
    dialog.deleteLater()
    return answer


# Questions -----------------------------------------------------------------------------


def ask(parent: QWidget | None, title: str, text: str, buttons: list[Button] | None = None,
        default: str | None = None, icon: str | None = None, *, detail: str = "") -> str | None:
    """Ask with verb buttons. The key of the button pressed, or None for Cancel, Esc or closing."""
    return _run(MessageDialog(parent, title, text, buttons, default, icon, detail))[0]


def ask_once(parent: QWidget | None, settings, question: str, title: str, text: str,
             buttons: list[Button] | None = None, default: str | None = None, icon: str | None = None) -> str | None:
    """ask() with "Don't ask again". Once ticked with the main button, that answer comes back unasked.

    With no settings (in tests and scripts) nothing is asked either.
    """
    buttons = buttons or OK_CANCEL
    if settings is None or settings.asked(question):
        return _default_key(buttons, default)
    dialog = MessageDialog(parent, title, text, buttons, default, icon, check="Don't ask again")
    choice, checked = _run(dialog)
    if checked and choice is not None and choice == dialog.default_key:
        settings.stop_asking(question)
    return choice


def confirm(parent: QWidget | None, title: str, text: str, action: str = "OK", danger: bool = False,
            icon: str | None = None) -> bool:
    """True if the user pressed `action` (a verb such as "Delete"), False for Cancel."""
    buttons = [("ok", action, "danger" if danger else "primary"), (CANCEL, "Cancel", "")]
    return ask(parent, title, text, buttons, icon=icon) == "ok"


def confirm_once(parent: QWidget | None, settings, question: str, title: str, text: str, action: str = "OK",
                 danger: bool = False, icon: str | None = None) -> bool:
    """confirm() with "Don't ask again"."""
    buttons = [("ok", action, "danger" if danger else "primary"), (CANCEL, "Cancel", "")]
    return ask_once(parent, settings, question, title, text, buttons, icon=icon) == "ok"


def save_discard_cancel(parent: QWidget | None, title: str, text: str, icon: str | None = None) -> str | None:
    """"save", "discard", or None to go back."""
    buttons = [("save", "Save", "primary"), ("discard", "Don't save", "danger"), (CANCEL, "Cancel", "")]
    return ask(parent, title, text, buttons, "save", icon)


# Messages ------------------------------------------------------------------------------


def inform(parent: QWidget | None, title: str, text: str, detail: str = "", icon: str | None = "info") -> None:
    _run(MessageDialog(parent, title, text, [("ok", "OK", "primary")], icon=icon, detail=detail))


def warn(parent: QWidget | None, title: str, text: str, detail: str = "",
         icon: str | None = "warning-circle") -> None:
    """Something went wrong. `detail` holds the technical reason, in small print."""
    _run(MessageDialog(parent, title, text, [("ok", "OK", "primary")], icon=icon, detail=detail,
                       icon_color=themes.current.danger))


# One-field prompts ---------------------------------------------------------------------


def _prompt(parent, title: str, label: str, action: str) -> MessageDialog:
    return MessageDialog(parent, title, label, [("ok", action, "primary"), (CANCEL, "Cancel", "")])


def _require(dialog: MessageDialog, signal, value) -> None:
    """Keep the main button off while the field is empty."""
    button = dialog.button("ok")

    def update(*_) -> None:
        button.setEnabled(bool(value().strip()))

    signal.connect(update)
    update()


def get_text(parent: QWidget | None, title: str, label: str, text: str = "", *, password: bool = False,
             placeholder: str = "", action: str = "OK", required: bool = False) -> str | None:
    """A line of text, or None if cancelled."""
    dialog = _prompt(parent, title, label, action)
    field = QLineEdit(text)
    field.setPlaceholderText(tr(placeholder))
    if password:
        field.setEchoMode(QLineEdit.EchoMode.Password)
    dialog.add_field(field)
    field.selectAll()
    if required:
        _require(dialog, field.textChanged, field.text)
    return field.text() if _run(dialog)[0] == "ok" else None


def get_multiline_text(parent: QWidget | None, title: str, label: str, text: str = "", *, action: str = "OK",
                       required: bool = False) -> str | None:
    """Several lines of text, or None if cancelled. Enter starts a new line, Ctrl+Enter confirms."""
    dialog = _prompt(parent, title, label, action)
    field = QPlainTextEdit(text)
    field.setTabChangesFocus(True)
    field.setMinimumHeight(120)
    dialog.add_field(field)
    field.selectAll()
    field.installEventFilter(dialog)
    if required:
        _require(dialog, field.textChanged, field.toPlainText)
    return field.toPlainText() if _run(dialog)[0] == "ok" else None


def get_item(parent: QWidget | None, title: str, label: str, items: list[str], current: int = 0,
             editable: bool = False, *, action: str = "OK", required: bool = False) -> str | None:
    """One of `items` (or, if editable, whatever was typed), or None if cancelled."""
    dialog = _prompt(parent, title, label, action)
    box = QComboBox()
    box.addItems(list(items))
    box.setEditable(editable)
    if items:
        box.setCurrentIndex(min(max(current, 0), len(items) - 1))
    dialog.add_field(box)
    if editable and required:
        _require(dialog, box.currentTextChanged, box.currentText)
    return box.currentText() if _run(dialog)[0] == "ok" else None


def get_int(parent: QWidget | None, title: str, label: str, value: int = 0, minimum: int = -INT_RANGE,
            maximum: int = INT_RANGE, step: int = 1, *, suffix: str = "", action: str = "OK") -> int | None:
    """A whole number in [minimum, maximum], or None if cancelled."""
    dialog = _prompt(parent, title, label, action)
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setValue(value)
    box.setMinimumWidth(140)
    dialog.add_field(box, full_width=False)
    box.selectAll()
    return box.value() if _run(dialog)[0] == "ok" else None


def get_double(parent: QWidget | None, title: str, label: str, value: float = 0.0, minimum: float = -1e9,
               maximum: float = 1e9, decimals: int = 1, step: float = 1.0, *, suffix: str = "",
               action: str = "OK") -> float | None:
    """A number in [minimum, maximum], or None if cancelled."""
    dialog = _prompt(parent, title, label, action)
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setValue(value)
    box.setMinimumWidth(140)
    dialog.add_field(box, full_width=False)
    box.selectAll()
    return box.value() if _run(dialog)[0] == "ok" else None
