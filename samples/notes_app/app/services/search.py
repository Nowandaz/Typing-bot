"""Searching and tag counting over a collection of notes."""
from __future__ import annotations

import re
from collections import Counter

from ..models.note import Note
from ..models.tag import Tag

_WORD = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text)}


def score(note: Note, terms: set[str]) -> int:
    if not terms:
        return 0
    title = _tokens(note.title)
    body = _tokens(note.body)
    tags = {t.lower() for t in note.tags}
    total = 0
    for term in terms:
        if term in title:
            total += 5
        if term in tags:
            total += 3
        if term in body:
            total += 1
    return total


def search_notes(notes: list[Note], query: str, tag: str | None = None) -> list[Note]:
    terms = _tokens(query)
    results = []
    for note in notes:
        if tag and tag.lower() not in {t.lower() for t in note.tags}:
            continue
        points = score(note, terms)
        if terms and points == 0:
            continue
        results.append((points, note))
    results.sort(key=lambda pair: (-pair[0], not pair[1].pinned, -pair[1].updated_at))
    return [note for _, note in results]


def tag_counts(notes: list[Note]) -> list[Tag]:
    counter: Counter = Counter()
    for note in notes:
        for tag in note.tags:
            counter[tag.lower()] += 1
    return [Tag(name, count) for name, count in counter.most_common()]
