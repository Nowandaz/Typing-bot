"""The vocabulary shared by the planner, the editor model and the driver."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Type:
    """Type one printable character."""
    ch: str


@dataclass(frozen=True)
class Key:
    """Press a key or chord: enter, tab, backspace, delete, escape, left,
    right, up, down, home, end, ctrl+home, ctrl+end, shift+<move>, ctrl+a."""
    name: str


@dataclass(frozen=True)
class Paste:
    """Insert a fragment literally (characters the keyboard cannot produce)."""
    text: str


@dataclass(frozen=True)
class GotoLine:
    """VS Code's Go to Line (Ctrl+G, number, Enter)."""
    line: int


@dataclass(frozen=True)
class Pause:
    """A human pause.  kind: line | think | plan | notice | review | settle"""
    kind: str


@dataclass(frozen=True)
class Note:
    """Status text for the GhostWriter window; sends nothing to the editor."""
    text: str


@dataclass
class Plan:
    target: str
    language: str
    actions: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def keystrokes(self) -> int:
        return sum(1 for a in self.actions if isinstance(a, (Type, Key, Paste, GotoLine)))
