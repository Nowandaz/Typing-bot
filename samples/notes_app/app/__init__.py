"""Application factory."""
from __future__ import annotations

from flask import Flask

from .config import Config
from .routes.notes import notes_bp
from .routes.api import api_bp
from .services.store import NoteStore


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config or Config())

    store = NoteStore(app.config["DATA_FILE"])
    store.load()
    app.extensions["store"] = store

    app.register_blueprint(notes_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.context_processor
    def inject_globals():
        return {"app_name": app.config["APP_NAME"]}

    return app
