"""Persistent run logs.

Everything GhostWriter writes to the in-window log is also appended to a
dated file under ``logs/`` next to the program, so a session can be reviewed
or kept after the window is closed.  Entries are plain text with a timestamp;
nothing about the machine or the user is recorded beyond what the session
itself prints (file names, counts, status).
"""
from __future__ import annotations

import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
MAX_FILES = 30


class RunLog:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else LOG_DIR
        self.path: Path | None = None
        self._handle = None

    def start(self, title: str = "") -> Path | None:
        """Open a new log file for a run.  Returns its path, or None if the
        folder cannot be written (logging then falls back to the window only)."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.path = self.directory / f"ghostwriter-{stamp}.log"
            self._handle = self.path.open("a", encoding="utf-8")
            header = time.strftime("%Y-%m-%d %H:%M:%S")
            self._handle.write(f"# GhostWriter run {header}{(' - ' + title) if title else ''}\n")
            self._handle.flush()
            self._prune()
            return self.path
        except OSError:
            self.path, self._handle = None, None
            return None

    def write(self, text: str):
        if self._handle is None:
            return
        try:
            self._handle.write(f"{time.strftime('%H:%M:%S')}  {text}\n")
            self._handle.flush()
        except OSError:
            pass

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None

    def _prune(self):
        files = sorted(self.directory.glob("ghostwriter-*.log"))
        for old in files[:-MAX_FILES]:
            try:
                old.unlink()
            except OSError:
                pass

    def recent(self, limit: int = MAX_FILES):
        """Existing log files, newest first: (path, modified_time)."""
        files = sorted(self.directory.glob("ghostwriter-*.log"), reverse=True)
        return [(p, p.stat().st_mtime) for p in files[:limit] if p.exists()]
