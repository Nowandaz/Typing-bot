"""Configuration."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    APP_NAME = "Notes"
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    DATA_FILE = Path(os.environ.get("NOTES_DATA", BASE_DIR / "data" / "notes.json"))
    PAGE_SIZE = 20
    DEBUG = False


class TestConfig(Config):
    DEBUG = True
    DATA_FILE = BASE_DIR / "data" / "notes.test.json"
