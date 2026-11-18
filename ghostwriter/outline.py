"""Code awareness: find functions, classes, loops and conditions.

The outline is deliberately lightweight (regular expressions over the code
with strings and comments blanked out) - enough to know what the typist is
"working on", to pace the typing, and to decide where to navigate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .languages import Lexer, strip_code

FUNCTION, CLASS, LOOP, CONDITION, RULE, ELEMENT = "function", "class", "loop", "condition", "rule", "element"


@dataclass
class Block:
    kind: str
    name: str
    start: int      # first line (0-based)
    end: int        # last line, inclusive
    depth: int = 0

    @property
    def label(self) -> str:
        if self.kind == FUNCTION:
            return f"function {self.name}()"
        if self.kind == CLASS:
            return f"class {self.name}"
        if self.kind in (LOOP, CONDITION):
            return f"{self.name} {self.kind}"
        return f"{self.kind} {self.name}".strip()


_PY = [
    (FUNCTION, re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")),
    (CLASS, re.compile(r"^\s*class\s+(\w+)")),
    (LOOP, re.compile(r"^\s*(?:async\s+)?(for|while)\b")),
    (CONDITION, re.compile(r"^\s*(if|elif|else|match|case|try|except|finally|with)\b")),
]
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "do", "try", "new", "typeof"}
_JS = [
    (CLASS, re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)")),
    (FUNCTION, re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)")),
    (FUNCTION, re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|\w+\s*=>)")),
    (LOOP, re.compile(r"^\s*(?:\}\s*)?(for|while|do)\b")),
    (CONDITION, re.compile(r"^\s*(?:\}\s*)?(if|else|switch|try|catch|finally)\b")),
    (FUNCTION, re.compile(r"^\s*(?:static\s+|async\s+|get\s+|set\s+|public\s+|private\s+|protected\s+)*(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]| ]+)?\s*\{")),
]
_CSS = [
    (CONDITION, re.compile(r"^\s*(@media|@supports|@container)\b")),
    (RULE, re.compile(r"^\s*([^{}@/][^{}]*?)\s*\{")),
]
_HTML = [(ELEMENT, re.compile(r"^\s*<([A-Za-z][\w-]*)\b"))]
_HTML_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
              "track", "wbr"}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _extent(lines: list[str], start: int) -> int:
    """Last line of the block starting at ``start``, by indentation, with a
    closing-bracket line at the same indentation included."""
    base = _indent(lines[start])
    end = start
    for i in range(start + 1, len(lines)):
        text = lines[i]
        if not text.strip():
            continue
        ind = _indent(text)
        if ind > base:
            end = i
            continue
        if ind == base and re.match(r"\s*(?:[}\])]|</)", text):
            end = i
        break
    return end


def build_outline(text: str, lang_id: str) -> list[Block]:
    lines = text.split("\n")
    code = strip_code(Lexer(lang_id), lines)
    if lang_id == "python":
        patterns = _PY
    elif lang_id in ("javascript", "typescript"):
        patterns = _JS
    elif lang_id == "css":
        patterns = _CSS
    elif lang_id == "html":
        patterns = _HTML
    else:
        return []
    blocks: list[Block] = []
    for i, line in enumerate(code):
        if not line.strip():
            continue
        for kind, rx in patterns:
            m = rx.match(line)
            if not m:
                continue
            name = " ".join(lines[i][m.start(1):m.end(1)].split())
            if lang_id in ("javascript", "typescript") and kind == FUNCTION and name in _JS_KEYWORDS:
                continue
            if lang_id == "html" and name.lower() in _HTML_VOID:
                break
            end = _extent(code, i)
            if lang_id in ("css", "html") and end == i:
                break
            blocks.append(Block(kind, name, i, end))
            break
    for b in blocks:
        b.depth = sum(1 for o in blocks if o is not b and o.start <= b.start and o.end >= b.end
                      and (o.start, o.end) != (b.start, b.end))
    return blocks


def summarize(blocks: list[Block]) -> str:
    order = [(CLASS, "class", "classes"), (FUNCTION, "function", "functions"), (LOOP, "loop", "loops"),
             (CONDITION, "condition", "conditions"), (RULE, "rule", "rules"), (ELEMENT, "element", "elements")]
    parts = []
    for kind, one, many in order:
        n = sum(1 for b in blocks if b.kind == kind)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) if parts else "no code structure recognised"
