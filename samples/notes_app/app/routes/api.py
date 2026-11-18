"""A small JSON API mirroring the HTML routes."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..models.note import Note, normalize_tags
from ..services.search import search_notes
from ..services.store import NoteNotFound

api_bp = Blueprint("api", __name__)


def store():
    return current_app.extensions["store"]


@api_bp.get("/notes")
def list_notes():
    query = request.args.get("q", "").strip()
    tag = request.args.get("tag") or None
    notes = search_notes(store().ordered(), query, tag)
    return jsonify([note.to_dict() for note in notes])


@api_bp.get("/notes/<note_id>")
def get_note(note_id: str):
    try:
        return jsonify(store().get(note_id).to_dict())
    except NoteNotFound:
        return jsonify({"error": "not found"}), 404


@api_bp.post("/notes")
def create_note():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    note = Note(
        title=title,
        body=payload.get("body", ""),
        tags=normalize_tags(payload.get("tags", [])),
    )
    store().add(note)
    return jsonify(note.to_dict()), 201


@api_bp.put("/notes/<note_id>")
def update_note(note_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        note = store().get(note_id)
    except NoteNotFound:
        return jsonify({"error": "not found"}), 404
    note.update(
        payload.get("title", note.title),
        payload.get("body", note.body),
        normalize_tags(payload.get("tags", note.tags)),
    )
    store().save()
    return jsonify(note.to_dict())


@api_bp.delete("/notes/<note_id>")
def delete_note(note_id: str):
    try:
        store().remove(note_id)
    except NoteNotFound:
        return jsonify({"error": "not found"}), 404
    return "", 204
