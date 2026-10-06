from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SavedPlace:
    """One row from a Google Takeout Saved collection."""

    title: str
    url: str
    note: str = ""
    tags: tuple[str, ...] = ()
    comment: str = ""
    source_row: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SavedList:
    """A normalized saved collection discovered in a Takeout export."""

    identifier: str
    name: str
    description: str
    source: str
    places: tuple[SavedPlace, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
