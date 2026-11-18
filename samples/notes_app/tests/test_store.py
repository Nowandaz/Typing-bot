"""Tests for the JSON note store."""
from app.models.note import Note
from app.services.store import NoteStore, NoteNotFound

import pytest


def make_store(tmp_path):
    return NoteStore(tmp_path / "notes.json")


def test_add_and_get(tmp_path):
    store = make_store(tmp_path)
    note = store.add(Note(title="First", body="hello", tags=["a", "b"]))
    assert len(store) == 1
    assert store.get(note.id).title == "First"


def test_persistence_round_trip(tmp_path):
    store = make_store(tmp_path)
    store.add(Note(title="Keep me", tags=["x"]))
    reloaded = make_store(tmp_path)
    reloaded.load()
    assert len(reloaded) == 1
    assert reloaded.all()[0].title == "Keep me"


def test_pinned_notes_sort_first(tmp_path):
    store = make_store(tmp_path)
    a = store.add(Note(title="A"))
    b = store.add(Note(title="B"))
    store.toggle_pin(b.id)
    assert store.ordered()[0].id == b.id


def test_remove_missing_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(NoteNotFound):
        store.remove("nope")
