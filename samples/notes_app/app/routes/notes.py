"""HTML pages for browsing and editing notes."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from ..models.note import Note, normalize_tags
from ..services.search import search_notes, tag_counts
from ..services.store import NoteNotFound

notes_bp = Blueprint("notes", __name__)


def store():
    return current_app.extensions["store"]


@notes_bp.route("/")
def index():
    query = request.args.get("q", "").strip()
    tag = request.args.get("tag") or None
    notes = search_notes(store().ordered(), query, tag)
    return render_template(
        "index.html",
        notes=notes,
        tags=tag_counts(store().all()),
        query=query,
        active_tag=tag,
    )


@notes_bp.route("/note/<note_id>")
def view(note_id: str):
    try:
        note = store().get(note_id)
    except NoteNotFound:
        abort(404)
    return render_template("note.html", note=note, editing=False)


@notes_bp.route("/note/<note_id>/edit")
def edit(note_id: str):
    try:
        note = store().get(note_id)
    except NoteNotFound:
        abort(404)
    return render_template("note.html", note=note, editing=True)


@notes_bp.route("/new")
def new():
    blank = Note(title="", body="")
    return render_template("note.html", note=blank, editing=True, creating=True)


@notes_bp.route("/save", methods=["POST"])
def save():
    note_id = request.form.get("id") or ""
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "")
    tags = normalize_tags(request.form.get("tags", "").split(","))
    if not title:
        abort(400, "A title is required")
    if note_id:
        try:
            note = store().get(note_id)
        except NoteNotFound:
            abort(404)
        note.update(title, body, tags)
        store().save()
    else:
        note = store().add(Note(title=title, body=body, tags=tags))
    return redirect(url_for("notes.view", note_id=note.id))


@notes_bp.route("/note/<note_id>/pin", methods=["POST"])
def pin(note_id: str):
    try:
        store().toggle_pin(note_id)
    except NoteNotFound:
        abort(404)
    return redirect(request.referrer or url_for("notes.index"))


@notes_bp.route("/note/<note_id>/delete", methods=["POST"])
def delete(note_id: str):
    try:
        store().remove(note_id)
    except NoteNotFound:
        abort(404)
    return redirect(url_for("notes.index"))
