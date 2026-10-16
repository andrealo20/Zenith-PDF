"""Back and forward through the places a reader jumped from: links, bookmarks, Ctrl+G, Home and End.

A place is a Spot, a page and a height on it, so it stays right at any zoom and layout.
Page numbers change when pages are added, moved or removed: the view clears the list then.
"""

from __future__ import annotations

from dataclasses import dataclass

LIMIT = 50  # places kept to go back to
NEAR = 24.0  # points: a jump from where the last one started adds no second entry


@dataclass(frozen=True)
class Spot:
    page: int
    y: float  # display points from the top of the page, at the top of the reading area


class History:
    def __init__(self):
        self._back: list[Spot] = []
        self._forward: list[Spot] = []

    @property
    def can_back(self) -> bool:
        return bool(self._back)

    @property
    def can_forward(self) -> bool:
        return bool(self._forward)

    def peek_back(self) -> Spot | None:
        """Where back() would go, without going."""
        return self._back[-1] if self._back else None

    def push(self, here: Spot) -> None:
        """A jump starts at `here`: remember it, and forget the way forward."""
        self._forward.clear()
        last = self.peek_back()
        if last is not None and last.page == here.page and abs(last.y - here.y) < NEAR:
            return
        self._back.append(here)
        del self._back[:-LIMIT]

    def back(self, here: Spot) -> Spot | None:
        """The place before `here`, which forward() comes back to; None if there is none."""
        if not self._back:
            return None
        self._forward.append(here)
        return self._back.pop()

    def forward(self, here: Spot) -> Spot | None:
        """The place back() left, with `here` to go back to again; None if there is none."""
        if not self._forward:
            return None
        self._back.append(here)
        return self._forward.pop()

    def clear(self) -> None:
        self._back.clear()
        self._forward.clear()
