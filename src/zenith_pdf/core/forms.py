"""PDF forms: the fields of a document, and filling them in.

Fields are read with pikepdf from the document's own PDF, each with the boxes it shows on the pages (a radio
group has one per choice). Filling sets the values and draws the fields' appearances again, so PDFium shows
them and every other viewer too; Document.fill_fields runs it as an edit that Undo takes back.
"""

from __future__ import annotations

from dataclasses import dataclass

import pikepdf
from pikepdf.form import (
    CheckboxField,
    ChoiceField,
    DefaultAppearanceStreamGenerator,
    Form,
    RadioButtonGroup,
    TextField,
)

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Box:
    """Where a field shows: its page (from 0), its box in PDF space (left, bottom, right, top), and for a check
    box or a radio button the name of its "on" state."""

    page: int
    rect: Rect
    state: str = ""


@dataclass(frozen=True)
class Field:
    name: str  # the full name, as the PDF knows it
    kind: str  # "text", "checkbox", "radio", "choice"
    value: object  # text: str; checkbox: bool; radio: the chosen state's name or ""; choice: str
    boxes: tuple[Box, ...]
    options: tuple[str, ...] = ()  # a choice's options; a radio group's states
    read_only: bool = False
    multiline: bool = False
    max_length: int | None = None
    editable: bool = False  # a combo box that takes text of its own


def _name(value) -> str:
    return str(value).lstrip("/") if value is not None else ""


def _on_state(annotation) -> str:
    """The name of the state a check box or radio button shows when it is on."""
    appearance = annotation.obj.get("/AP")
    states = appearance.get("/N") if appearance is not None else None
    if isinstance(states, pikepdf.Dictionary):
        for key in states.keys():
            if key != "/Off":
                return _name(key)
    return "Yes"


def read_fields(pdf: pikepdf.Pdf) -> list[Field]:
    """The document's fields that can be filled in, in page order; signature fields and buttons left out."""
    if "/AcroForm" not in pdf.Root:
        return []
    acroform = pdf.acroform
    boxes: dict[str, list[Box]] = {}
    order: list[str] = []
    for index, page in enumerate(pdf.pages):
        for annotation in acroform.get_widget_annotations_for_page(page):
            field = acroform.get_field_for_annotation(annotation)
            name = field.fully_qualified_name
            rect = annotation.rect
            state = _on_state(annotation) if field.field_type == "/Btn" else ""
            boxes.setdefault(name, []).append(Box(index, (rect.llx, rect.lly, rect.urx, rect.ury), state))
            if name not in order:
                order.append(name)
    found = {}
    for name, field in Form(pdf).items():
        if name not in boxes:
            continue
        common = dict(name=name, boxes=tuple(boxes[name]), read_only=bool(field.is_read_only))
        if isinstance(field, TextField):
            found[name] = Field(kind="text", value=str(field.value or ""), multiline=bool(field.is_multiline),
                                max_length=field.max_length or None, **common)
        elif isinstance(field, CheckboxField):
            found[name] = Field(kind="checkbox", value=bool(field.checked), **common)
        elif isinstance(field, RadioButtonGroup):
            value = _name(field.value)
            found[name] = Field(kind="radio", value="" if value == "Off" else value,
                                options=tuple(_name(o.on_value) for o in field.options), **common)
        elif isinstance(field, ChoiceField):
            options = tuple(str(getattr(o, "display_value", o)) for o in field.options)
            found[name] = Field(kind="choice", value=str(field.value or ""), options=options,
                                editable=bool(field.allow_edit), **common)
    return [found[name] for name in order if name in found]


def fill(pdf: pikepdf.Pdf, values: dict[str, object]) -> None:
    """Set the fields named in `values` and draw their appearances again. Unknown names are left alone."""
    form = Form(pdf, generate_appearances=DefaultAppearanceStreamGenerator)
    fields = dict(form.items())
    for name, value in values.items():
        field = fields.get(name)
        if field is None or field.is_read_only:
            continue
        if isinstance(field, TextField):
            field.value = str(value)
        elif isinstance(field, CheckboxField):
            field.checked = bool(value)
        elif isinstance(field, RadioButtonGroup):
            for option in field.options:
                if _name(option.on_value) == value:
                    option.select()
                    break
            else:
                if value in ("", None, "Off") and field.can_toggle_off:
                    field.value = pikepdf.Name.Off
        elif isinstance(field, ChoiceField):
            # By what the list shows, which may not be what the PDF stores for it.
            option = next((o for o in field.options if str(getattr(o, "display_value", o)) == str(value)), None)
            if option is not None:
                option.select()
            else:
                field.value = str(value)  # a combo box that takes text of its own
