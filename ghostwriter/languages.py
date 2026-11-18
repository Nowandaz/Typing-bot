"""Language knowledge: VS Code language configurations plus small lexers.

The bracket pairs, auto-closing pairs, on-Enter rules and indentation rules
are loaded from ``data/language_configs.json``, which holds the configuration
VS Code itself ships for each language.  The lexers classify every character
of a line as code, string or comment; VS Code uses that classification to
decide whether a quote or bracket should be auto-closed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

OTHER, STRING, COMMENT = "o", "s", "c"

_DATA = Path(__file__).with_name("data") / "language_configs.json"

EXTENSIONS = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".html": "html", ".htm": "html",
    ".css": "css",
}

LANGUAGE_LABELS = {
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
    "html": "HTML", "css": "CSS", "plain": "Plain text",
}


@dataclass(frozen=True)
class Pair:
    open: str
    close: str
    not_in: frozenset = frozenset()


@dataclass(frozen=True)
class EnterRule:
    before: re.Pattern | None
    after: re.Pattern | None
    previous: re.Pattern | None
    indent: str            # none | indent | outdent | indentOutdent
    append: str = ""
    remove: int = 0


@dataclass
class Language:
    id: str
    line_comment: str | None = None
    block_comment: tuple | None = None
    brackets: list = field(default_factory=list)          # [(open, close)]
    pairs: list = field(default_factory=list)             # [Pair]
    auto_close_before: str = ";:.,=}])> \n\t"
    explicit_close_before: bool = False
    enter_rules: list = field(default_factory=list)
    increase: re.Pattern | None = None
    decrease: re.Pattern | None = None
    indent_next: re.Pattern | None = None
    unindented: re.Pattern | None = None

    @property
    def has_indent_rules(self) -> bool:
        return self.increase is not None or self.decrease is not None

    def bracket_chars(self) -> str:
        return "".join(o + c for o, c in self.brackets if len(o) == 1 and len(c) == 1)


def _rx(spec) -> re.Pattern | None:
    """Translate a JavaScript regex (string or {pattern, flags}) to Python."""
    if spec is None:
        return None
    flags = 0
    if isinstance(spec, dict):
        if "i" in spec.get("flags", ""):
            flags |= re.IGNORECASE
        spec = spec["pattern"]
    # JS tolerates a class escape as a range endpoint ([\w-.]); Python does not.
    spec = spec.replace("\\w-.", "\\w\\-.")
    return re.compile(spec, flags)


def _build(lang_id: str, cfg: dict) -> Language:
    comments = cfg.get("comments", {})
    lang = Language(
        id=lang_id,
        line_comment=comments.get("lineComment"),
        block_comment=tuple(comments["blockComment"]) if comments.get("blockComment") else None,
        brackets=[tuple(b) for b in cfg.get("brackets", [])],
    )
    for p in cfg.get("autoClosingPairs", []):
        if isinstance(p, list):
            lang.pairs.append(Pair(p[0], p[1]))
        else:
            lang.pairs.append(Pair(p["open"], p["close"], frozenset(p.get("notIn", []))))
    if "autoCloseBefore" in cfg:
        lang.auto_close_before = cfg["autoCloseBefore"]
        lang.explicit_close_before = True
    for r in cfg.get("onEnterRules", []):
        action = r["action"]
        lang.enter_rules.append(EnterRule(
            _rx(r.get("beforeText")), _rx(r.get("afterText")), _rx(r.get("previousLineText")),
            action["indent"], action.get("appendText", ""), action.get("removeText", 0),
        ))
    rules = cfg.get("indentationRules", {})
    lang.increase = _rx(rules.get("increaseIndentPattern"))
    lang.decrease = _rx(rules.get("decreaseIndentPattern"))
    lang.indent_next = _rx(rules.get("indentNextLinePattern"))
    lang.unindented = _rx(rules.get("unIndentedLinePattern"))
    return lang


@lru_cache(maxsize=None)
def get_language(lang_id: str) -> Language:
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    if lang_id in data and lang_id != "_source":
        return _build(lang_id, data[lang_id])
    # Plain text: VS Code only knows the three bracket pairs.
    return Language(
        id="plain",
        brackets=[("{", "}"), ("[", "]"), ("(", ")")],
        pairs=[Pair("{", "}"), Pair("[", "]"), Pair("(", ")")],
    )


def detect_language(filename: str | None, text: str = "") -> str:
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in EXTENSIONS:
            return EXTENSIONS[ext]
        if ext:
            return "plain"
    head = text.lstrip()[:400].lower()
    if head.startswith("<!doctype html") or head.startswith("<html") or re.search(r"<(div|body|head|script|p|h1)\b", head):
        return "html"
    if re.search(r"^\s*(def |class |import |from \w+ import )", text, re.M) and not re.search(r"[{;]\s*$", text, re.M):
        return "python"
    if re.search(r"\b(function|const|let|var|=>)\b", text):
        return "javascript"
    if re.search(r"^[^\n{]+\{\s*$[^}]*:[^}]*;", text, re.M):
        return "css"
    return "plain"


# --------------------------------------------------------------------------
# Lexers.  Each takes (line, carry_state) and returns
#   (types, regions, inline_state_type, carry_state)
# ``types`` has one of OTHER/STRING/COMMENT per character; ``regions`` the
# language id per character (only differs from the root inside HTML);
# ``inline_state_type`` is the type a character appended to the line would get.
# --------------------------------------------------------------------------

def _lex_python(line, state):
    """State is a stack of frames: ('s', delimiter, is_fstring) | ('e', depth)."""
    stack = list(state or ())
    types = []
    i, n = 0, len(line)
    in_comment = False
    while i < n:
        ch = line[i]
        top = stack[-1] if stack else None
        if top and top[0] == "s":
            delim, is_f = top[1], top[2]
            if ch == "\\" and i + 1 < n:
                types.append(STRING * 2); i += 2
            elif line.startswith(delim, i):
                types.append(STRING * len(delim)); i += len(delim); stack.pop()
            elif is_f and ch == "{":
                if line.startswith("{{", i):
                    types.append(STRING * 2); i += 2
                else:
                    types.append(OTHER); i += 1; stack.append(("e", 0))
            else:
                types.append(STRING); i += 1
            continue
        if top and top[0] == "e":
            if ch == "}" and top[1] == 0:
                stack.pop(); types.append(OTHER); i += 1
                continue
            if ch in "{[(":
                stack[-1] = ("e", top[1] + 1)
            elif ch in "}])" and top[1] > 0:
                stack[-1] = ("e", top[1] - 1)
        if ch == "#" and not top:
            types.append(COMMENT * (n - i)); i = n; in_comment = True
        elif ch in "\"'":
            delim = ch * 3 if line.startswith(ch * 3, i) else ch
            j = i
            while j > 0 and line[j - 1].isalpha():
                j -= 1
            prefix = line[j:i].lower()
            stack.append(("s", delim, "f" in prefix and len(prefix) <= 2))
            types.append(STRING * len(delim)); i += len(delim)
        else:
            types.append(OTHER); i += 1
    inline = STRING if (stack and stack[-1][0] == "s") else COMMENT if in_comment else OTHER
    carry = tuple(stack) if stack and len(stack[0][1]) == 3 else None
    return "".join(types), inline, carry


def _lex_clike(line, state, template=True, line_comment="//"):
    """JavaScript / TypeScript / CSS.

    State is a stack of frames: ('c',) block comment | ('s', quote) |
    ('t',) template literal | ('e', depth) ``${...}`` expression.
    """
    stack = list(state or ())
    types = []
    i, n = 0, len(line)
    in_comment = False
    while i < n:
        ch = line[i]
        top = stack[-1] if stack else None
        kind = top[0] if top else None
        if kind == "c":
            end = line.find("*/", i)
            if end < 0:
                types.append(COMMENT * (n - i)); i = n
            else:
                types.append(COMMENT * (end + 2 - i)); i = end + 2; stack.pop()
            continue
        if kind == "s":
            if ch == "\\" and i + 1 < n:
                types.append(STRING * 2); i += 2
            else:
                types.append(STRING); i += 1
                if ch == top[1]:
                    stack.pop()
            continue
        if kind == "t":
            if ch == "\\" and i + 1 < n:
                types.append(STRING * 2); i += 2
            elif ch == "`":
                types.append(STRING); i += 1; stack.pop()
            elif line.startswith("${", i):
                types.append(OTHER * 2); i += 2; stack.append(("e", 0))
            else:
                types.append(STRING); i += 1
            continue
        if kind == "e":
            if ch == "}" and top[1] == 0:
                stack.pop(); types.append(OTHER); i += 1
                continue
            if ch == "{":
                stack[-1] = ("e", top[1] + 1)
            elif ch == "}":
                stack[-1] = ("e", top[1] - 1)
        if line_comment and line.startswith(line_comment, i):
            types.append(COMMENT * (n - i)); i = n; in_comment = True
        elif line.startswith("/*", i):
            stack.append(("c",)); types.append(COMMENT * 2); i += 2
        elif ch in "\"'":
            stack.append(("s", ch)); types.append(STRING); i += 1
        elif template and ch == "`":
            stack.append(("t",)); types.append(STRING); i += 1
        else:
            types.append(OTHER); i += 1
    kind = stack[-1][0] if stack else None
    inline = COMMENT if (kind == "c" or in_comment) else STRING if kind in ("s", "t") else OTHER
    while stack and stack[-1][0] == "s":
        stack.pop()
    return "".join(types), inline, (tuple(stack) or None)


_SCRIPT_OPEN = re.compile(r"<(script|style)\b", re.I)


def _lex_html(line, state):
    """State: (region, sub).  region: html | javascript | css.

    html sub: None | 'comment' | ('tag', name, quote)
    """
    region, sub = state or ("html", None)
    types, regions = [], []
    i, n = 0, len(line)
    inline = OTHER
    while i < n:
        if region != "html":
            closer = "</script" if region == "javascript" else "</style"
            end = line.lower().find(closer, i)
            chunk = line[i:end] if end >= 0 else line[i:]
            if region == "javascript":
                t, inline, sub = _lex_clike(chunk, sub)
            else:
                t, inline, sub = _lex_clike(chunk, sub, template=False, line_comment=None)
            types.append(t); regions.extend([region] * len(chunk)); i += len(chunk)
            if end >= 0:
                region, sub, inline = "html", None, OTHER
            continue
        ch = line[i]
        if sub == "comment":
            end = line.find("-->", i)
            stop = n if end < 0 else end + 3
            types.append(COMMENT * (stop - i)); regions.extend(["html"] * (stop - i)); i = stop
            if end >= 0:
                sub = None; inline = OTHER
            else:
                inline = COMMENT
        elif isinstance(sub, tuple):
            _, name, quote = sub
            if quote:
                types.append(STRING); inline = STRING
                if ch == quote:
                    sub = ("tag", name, None); inline = OTHER
            elif ch in "\"'":
                types.append(STRING); sub = ("tag", name, ch); inline = STRING
            elif ch == ">":
                types.append(OTHER); inline = OTHER
                self_closed = i > 0 and line[i - 1] == "/"
                sub = None
                if name in ("script", "style") and not self_closed:
                    regions.append("html"); i += 1
                    region = "javascript" if name == "script" else "css"
                    continue
            else:
                types.append(OTHER)
            regions.append("html"); i += 1
        elif line.startswith("<!--", i):
            sub = "comment"
        elif ch == "<":
            m = re.match(r"</?([A-Za-z][\w:-]*)", line[i:])
            if m and not line.startswith("</", i):
                sub = ("tag", m.group(1).lower(), None)
            elif m:
                sub = ("tag", "/", None)
            types.append(OTHER); regions.append("html"); i += 1
        else:
            types.append(OTHER); regions.append("html"); i += 1
    return "".join(types), regions, inline, (region, sub)


class Lexer:
    """Per-document lexer with state carried across lines."""

    def __init__(self, lang_id: str):
        self.lang_id = lang_id

    def initial(self):
        return ("html", None) if self.lang_id == "html" else None

    def lex(self, line: str, state):
        """Returns (types, regions, inline_type, carry_state, end_region)."""
        lid = self.lang_id
        if lid == "python":
            t, inline, carry = _lex_python(line, state)
        elif lid in ("javascript", "typescript"):
            t, inline, carry = _lex_clike(line, state)
        elif lid == "css":
            t, inline, carry = _lex_clike(line, state, template=False, line_comment=None)
        elif lid == "html":
            t, regions, inline, carry = _lex_html(line, state)
            return t, regions, inline, carry, carry[0]
        else:
            t, inline, carry = OTHER * len(line), OTHER, None
        return t, [lid] * len(line), inline, carry, lid


def strip_code(lexer: Lexer, lines: list[str]) -> list[str]:
    """Blank out string and comment contents so structure can be scanned."""
    out, state = [], lexer.initial()
    for line in lines:
        types, _, _, state, _ = lexer.lex(line, state)
        out.append("".join(ch if t == OTHER else " " for ch, t in zip(line, types)))
    return out
