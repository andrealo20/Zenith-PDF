"""Undoable edits. Annotations are immutable, so every edit swaps whole values."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QUndoCommand

from ..core.annotations import Annotation
from ..core.store import AnnotationStore


class AddAnnotations(QUndoCommand):
    def __init__(self, store: AnnotationStore, annotations: list[Annotation], text: str):
        super().__init__(text)
        self._store = store
        self._annotations = annotations

    def redo(self) -> None:
        for annotation in self._annotations:
            self._store.add(annotation)

    def undo(self) -> None:
        for annotation in reversed(self._annotations):
            self._store.remove(annotation.id)


class RemoveAnnotations(QUndoCommand):
    """Delete, cut, erase, clean. Undo puts each annotation back at its place in the
    drawing order, not on top: what was under another stays under it."""

    def __init__(self, store: AnnotationStore, annotations: list[Annotation], text: str):
        super().__init__(text)
        self._store = store
        self._annotations = annotations
        self._placed: list[tuple[int, Annotation]] = []

    def redo(self) -> None:
        positions = {annotation_id: i for i, annotation_id in enumerate(self._store.order())}
        self._placed = [(positions[a.id], a) for a in self._annotations]
        for annotation in self._annotations:
            self._store.remove(annotation.id)

    def undo(self) -> None:
        self._store.put_back(self._placed)


class ChangePages(QUndoCommand):
    """A change to the page structure, as snapshots of the whole PDF before and after.

    The change has already been made when this is pushed (an edit that fails must
    not land on the stack, and Qt pushes even if redo raises), so the first redo
    does nothing; later ones restore the result.
    """

    def __init__(self, text: str, before: bytes, after: bytes, restore: Callable[[bytes], None]):
        super().__init__(text)
        self._before = before
        self._after = after
        self._restore = restore
        self._done = True

    def redo(self) -> None:
        if self._done:
            self._done = False
        else:
            self._restore(self._after)

    def undo(self) -> None:
        self._restore(self._before)


class ReorderAnnotations(QUndoCommand):
    """Bring to front, send to back: swaps the whole drawing order."""

    def __init__(self, store: AnnotationStore, old: list[str], new: list[str], text: str):
        super().__init__(text)
        self._store = store
        self._old = old
        self._new = new

    def redo(self) -> None:
        self._store.set_order(self._new)

    def undo(self) -> None:
        self._store.set_order(self._old)


class ChangeAnnotations(QUndoCommand):
    def __init__(self, store: AnnotationStore, old: list[Annotation], new: list[Annotation], text: str):
        super().__init__(text)
        self._store = store
        self._old = old
        self._new = new

    def redo(self) -> None:
        for annotation in self._new:
            self._store.replace(annotation)

    def undo(self) -> None:
        for annotation in self._old:
            self._store.replace(annotation)
