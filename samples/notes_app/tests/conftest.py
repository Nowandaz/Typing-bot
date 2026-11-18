"""Shared pytest fixtures."""
import tempfile
from pathlib import Path

import pytest

from app import create_app
from app.config import TestConfig


@pytest.fixture
def app_config(tmp_path):
    class Config(TestConfig):
        DATA_FILE = tmp_path / "notes.json"
    return Config()


@pytest.fixture
def client(app_config):
    app = create_app(app_config)
    app.testing = True
    with app.test_client() as client:
        yield client
