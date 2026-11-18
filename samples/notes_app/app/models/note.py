"""The Note model."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass
class Note:
    title: str
    body: str = ""
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def slug(self) -> str:
        base = _SLUG.sub("-", self.title.lower()).strip("-")
        return base or self.id

    @property
    def excerpt(self) -> str:
        text = " ".join(self.body.split())
        return text[:140] + ("…" if len(text) > 140 else "")

    def touch(self) -> None:
        self.updated_at = time.time()

    def update(self, title: str, body: str, tags: list[str]) -> None:
        self.title = title.strip() or self.title
        self.body = body
        self.tags = normalize_tags(tags)
        self.touch()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def normalize_tags(tags) -> list[str]:
    seen, result = set(), []
    for tag in tags:
        clean = tag.strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
