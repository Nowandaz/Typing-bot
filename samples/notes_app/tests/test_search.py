"""Tests for search and tag counting."""
from app.models.note import Note
from app.services.search import search_notes, tag_counts


def notes():
    return [
        Note(title="Groceries", body="milk and bread", tags=["home"]),
        Note(title="Python tips", body="use dataclasses", tags=["code", "python"]),
        Note(title="Reading list", body="python books", tags=["code"]),
    ]


def test_search_matches_title_over_body():
    results = search_notes(notes(), "python")
    assert results[0].title == "Python tips"


def test_search_filters_by_tag():
    results = search_notes(notes(), "", tag="code")
    assert {n.title for n in results} == {"Python tips", "Reading list"}


def test_empty_query_returns_everything():
    assert len(search_notes(notes(), "")) == 3


def test_tag_counts_are_ordered():
    counts = {t.name: t.count for t in tag_counts(notes())}
    assert counts["code"] == 2
    assert counts["home"] == 1
