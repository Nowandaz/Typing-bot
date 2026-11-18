"""A small JSON-backed note store.

Not a real database — it keeps every note in memory and writes the whole file
on each change.  Fine for a demo and easy to reason about.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..models.note import Note


class NoteNotFound(KeyError):
    pass


class NoteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._notes: dict[str, Note] = {}

    # -- persistence
    def load(self) -> None:
        if not self.path.exists():
            self._notes = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        self._notes = {}
        for item in raw:
            note = Note.from_dict(item)
            self._notes[note.id] = note

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [note.to_dict() for note in self.ordered()]
        handle = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=self.path.parent, encoding="utf-8"
        )
        with handle:
            json.dump(data, handle, indent=2)
        Path(handle.name).replace(self.path)

    # -- queries
    def ordered(self) -> list[Note]:
        return sorted(
            self._notes.values(),
            key=lambda n: (not n.pinned, -n.updated_at),
        )

    def all(self) -> list[Note]:
        return list(self._notes.values())

    def get(self, note_id: str) -> Note:
        try:
            return self._notes[note_id]
        except KeyError as exc:
            raise NoteNotFound(note_id) from exc

    def __len__(self) -> int:
        return len(self._notes)

    # -- mutations
    def add(self, note: Note) -> Note:
        self._notes[note.id] = note
        self.save()
        return note

    def remove(self, note_id: str) -> None:
        if note_id not in self._notes:
            raise NoteNotFound(note_id)
        del self._notes[note_id]
        self.save()

    def toggle_pin(self, note_id: str) -> Note:
        note = self.get(note_id)
        note.pinned = not note.pinned
        note.touch()
        self.save()
        return note
