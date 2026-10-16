"""The live set of annotations of one document, observable through signals."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal

from .annotations import Annotation


class AnnotationStore(QObject):
    added = Signal(object)  # Annotation
    removed = Signal(object)  # Annotation
    changed = Signal(object, object)  # old, new (same id)
    reset = Signal()  # everything replaced, after a change to the pages
    reordered = Signal()  # drawing order changed

    def __init__(self, annotations: Iterable[Annotation] = (), parent: QObject | None = None):
        super().__init__(parent)
        self._items: dict[str, Annotation] = {a.id: a for a in annotations}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, annotation_id: str) -> bool:
        return annotation_id in self._items

    def get(self, annotation_id: str) -> Annotation | None:
        return self._items.get(annotation_id)

    def all(self) -> list[Annotation]:
        return list(self._items.values())

    def add(self, annotation: Annotation) -> None:
        self._items[annotation.id] = annotation
        self.added.emit(annotation)

    def put_back(self, placed: list[tuple[int, Annotation]]) -> None:
        """Add annotations at their old places in the drawing order: (position, annotation)
        pairs, positions as order() gave them before the annotations were removed."""
        items = list(self._items.items())
        for position, annotation in sorted(placed, key=lambda pair: pair[0]):
            items.insert(position, (annotation.id, annotation))
        self._items = dict(items)
        for _, annotation in placed:
            self.added.emit(annotation)
        if any(position < len(items) - len(placed) for position, _ in placed):
            self.reordered.emit()  # not all on top: whoever stacks them must restack

    def remove(self, annotation_id: str) -> None:
        self.removed.emit(self._items.pop(annotation_id))

    def replace(self, annotation: Annotation) -> None:
        old = self._items[annotation.id]
        self._items[annotation.id] = annotation
        self.changed.emit(old, annotation)

    def replace_all(self, annotations: Iterable[Annotation]) -> None:
        self._items = {a.id: a for a in annotations}
        self.reset.emit()

    def order(self) -> list[str]:
        """Ids from back to front: later ones are drawn over earlier ones."""
        return list(self._items)

    def set_order(self, ids: list[str]) -> None:
        self._items = {i: self._items[i] for i in ids}
        self.reordered.emit()
