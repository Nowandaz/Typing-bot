"""A tag with a count, used for the sidebar."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tag:
    name: str
    count: int = 0

    @property
    def is_popular(self) -> bool:
        return self.count >= 5

    def with_count(self, count: int) -> "Tag":
        return Tag(self.name, count)
