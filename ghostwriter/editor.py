"""A model of how the VS Code text editor reacts to keystrokes.

``VirtualEditor`` holds a buffer and a cursor and applies the same rules the
real editor applies when a key arrives: auto-closing brackets and quotes,
typing over auto-inserted closers, auto-indent on Enter (on-Enter rules,
bracket indent, indentation rules), electric closing brackets, tab stops on
Backspace, and trimming of auto-inserted whitespace.

The rules are a port of the editor core that VS Code and Monaco share
(``cursorTypeEditOperations``, ``enterAction``, ``autoIndent``).  The planner
runs every keystroke through this model before anything is sent to the real
editor, so it always knows what the buffer looks like.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache

from .languages import COMMENT, OTHER, STRING, Language, Lexer, get_language

WORD_SEPARATORS = "`~!@#$%^&*()-=+[{]}\\|;:'\",.<>/?"
AUTO_INDENT_LEVELS = {"none": 0, "keep": 1, "brackets": 2, "advanced": 3, "full": 4}
_TYPE_NAMES = {STRING: "string", COMMENT: "comment"}


@dataclass(frozen=True)
class EditorSettings:
    """The VS Code settings that change what a keystroke does."""
    tab_size: int = 4
    insert_spaces: bool = True
    auto_indent: str = "full"                      # none|keep|brackets|advanced|full
    auto_closing_brackets: str = "languageDefined"  # always|languageDefined|beforeWhitespace|never
    auto_closing_quotes: str = "languageDefined"
    auto_closing_comments: str = "languageDefined"
    auto_closing_overtype: str = "auto"            # always|auto|never
    auto_closing_delete: str = "auto"
    trim_auto_whitespace: bool = True
    use_tab_stops: bool = True

    def with_(self, **kw) -> "EditorSettings":
        return replace(self, **kw)


def leading_ws(s: str) -> str:
    return s[: len(s) - len(s.lstrip(" \t"))]


def _is_blank(s: str) -> bool:
    return s.strip(" \t") == ""


@lru_cache(maxsize=20000)
def _lex_cached(lang_id, line, state):
    return Lexer(lang_id).lex(line, state)


@lru_cache(maxsize=None)
def _bracket_rx(lang_id: str):
    parts = {b for pair in get_language(lang_id).brackets for b in pair}
    return re.compile("|".join(re.escape(b) for b in sorted(parts, key=len, reverse=True)))


@lru_cache(maxsize=None)
def _open_rx(bracket: str):
    s = re.escape(bracket)
    if re.match(r"\w", bracket[0]):
        s = r"\b" + s
    return re.compile(s + r"\s*$")


@lru_cache(maxsize=None)
def _close_rx(bracket: str):
    s = re.escape(bracket)
    if re.match(r"\w", bracket[-1]):
        s += r"\b"
    return re.compile(r"^\s*" + s)


class VirtualEditor:
    def __init__(self, lang_id: str = "plain", settings: EditorSettings | None = None, text: str = ""):
        self.lang_id = lang_id
        self.lang: Language = get_language(lang_id)
        self.settings = settings or EditorSettings()
        self.lines: list[str] = text.split("\n")
        self.row = 0
        self.col = 0
        self.anchor: tuple | None = None
        self.closers: list[list] = []      # [open_row, open_col, close_row, close_col]
        self.trim_rows: list[int] = []
        self._goal: int | None = None       # remembered visible column for Up/Down
        self._states: list = [Lexer(lang_id).initial()]
        self._level = AUTO_INDENT_LEVELS[self.settings.auto_indent]

    # ------------------------------------------------------------------ basics
    def clone(self) -> "VirtualEditor":
        c = VirtualEditor.__new__(VirtualEditor)
        c.lang_id, c.lang, c.settings, c._level = self.lang_id, self.lang, self.settings, self._level
        c.lines = list(self.lines)
        c.row, c.col, c.anchor = self.row, self.col, self.anchor
        c.closers = [list(m) for m in self.closers]
        c.trim_rows = list(self.trim_rows)
        c._goal = self._goal
        c._states = list(self._states)
        return c

    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line(self) -> str:
        return self.lines[self.row]

    @property
    def before(self) -> str:
        return self.lines[self.row][: self.col]

    @property
    def after(self) -> str:
        return self.lines[self.row][self.col:]

    @property
    def pos(self) -> tuple:
        return (self.row, self.col)

    def has_selection(self) -> bool:
        return self.anchor is not None and self.anchor != self.pos

    def selection(self) -> tuple:
        if not self.has_selection():
            return self.pos, self.pos
        return min(self.anchor, self.pos), max(self.anchor, self.pos)

    def selected_text(self) -> str:
        (r1, c1), (r2, c2) = self.selection()
        if r1 == r2:
            return self.lines[r1][c1:c2]
        return "\n".join([self.lines[r1][c1:]] + self.lines[r1 + 1:r2] + [self.lines[r2][:c2]])

    # ------------------------------------------------------------------ lexing
    def _lex_row(self, row: int):
        while len(self._states) <= row:
            r = len(self._states) - 1
            self._states.append(_lex_cached(self.lang_id, self.lines[r], self._states[r])[3])
        return _lex_cached(self.lang_id, self.lines[row], self._states[row])

    def _invalidate(self, row: int):
        del self._states[row + 1:]

    def _types(self, row: int) -> str:
        return self._lex_row(row)[0]

    def _inline_type(self, row: int, col: int) -> str:
        """Token type a neutral character inserted at (row, col) would get."""
        self._lex_row(row)
        return _lex_cached(self.lang_id, self.lines[row][:col], self._states[row])[2]

    def _region(self, row: int, col: int) -> str:
        """Language at a position (differs from the root only inside HTML)."""
        if self.lang_id != "html":
            return self.lang_id
        _, regions, _, _, _ = self._lex_row(row)
        if not regions:
            return self._states[row][0]
        return regions[min(col, len(regions) - 1)]

    def _lang_at(self, row: int, col: int) -> Language:
        return get_language(self._region(row, col))

    def _processed(self, row: int, text: str | None = None) -> str:
        """Line with brackets inside strings and comments removed."""
        line = self.lines[row]
        types = self._types(row)
        if text is not None and text != line:
            types = _lex_cached(self.lang_id, text, self._states[row])[0]
            line = text
        return self._strip(line, types)

    def _strip(self, text: str, types: str) -> str:
        """Remove bracket tokens that sit inside strings or comments."""
        if STRING not in types and COMMENT not in types:
            return text
        rx = _bracket_rx(self.lang_id)
        out, i, n = [], 0, len(text)
        while i < n:
            j = i
            plain = types[i] == OTHER
            while j < n and (types[j] == OTHER) == plain:
                j += 1
            out.append(text[i:j] if plain else rx.sub("", text[i:j]))
            i = j
        return "".join(out)

    # ------------------------------------------------------------- indentation
    def _visible(self, text: str) -> int:
        n = 0
        for ch in text:
            n = (n // self.settings.tab_size + 1) * self.settings.tab_size if ch == "\t" else n + 1
        return n

    def _ws(self, width: int) -> str:
        if self.settings.insert_spaces:
            return " " * width
        return "\t" * (width // self.settings.tab_size) + " " * (width % self.settings.tab_size)

    def normalize_indentation(self, text: str) -> str:
        ws = leading_ws(text)
        return self._ws(self._visible(ws)) + text[len(ws):]

    def shift_indent(self, indentation: str) -> str:
        size = self.settings.tab_size
        return self._ws((self._visible(indentation) // size + 1) * size)

    def unshift_indent(self, indentation: str) -> str:
        size = self.settings.tab_size
        width = self._visible(indentation)
        return self._ws(max(0, ((width - 1) // size) * size) if width else 0)

    # ----------------------------------------------------------------- editing
    def _replace(self, start: tuple, end: tuple, text: str, auto_ws: bool = False):
        """Replace [start, end) with text; leaves the cursor after the text."""
        s = self.settings
        pending = [r for r in self.trim_rows if r < len(self.lines)] if s.trim_auto_whitespace else []
        self.trim_rows = []
        trim_now = []
        for r in pending:
            if start[0] <= r <= end[0]:
                inserts_after = (start == end and start == (r, len(self.lines[r])) and text.startswith("\n"))
                inserts_before = (start == end and start == (r, 0) and text.endswith("\n"))
                if not (inserts_after or inserts_before):
                    continue
                trim_now.append((r, inserts_before))
            else:
                trim_now.append((r, False))

        (r1, c1), (r2, c2) = start, end
        old_start_line = self.lines[r1]
        head, tail = self.lines[r1][:c1], self.lines[r2][c2:]
        new = text.split("\n")
        end_row = r1 + len(new) - 1
        end_col = (c1 if len(new) == 1 else 0) + len(new[-1])
        new[0] = head + new[0]
        new[-1] = new[-1] + tail
        self.lines[r1:r2 + 1] = new
        row_delta = end_row - r2
        self._invalidate(r1)

        def move(p):
            if p < start:
                return p
            if p < end:
                return None
            if p[0] == r2:
                return (end_row, p[1] - c2 + end_col)
            return (p[0] + row_delta, p[1])

        kept = []
        for m in self.closers:
            o, c = move((m[0], m[1])), move((m[2], m[3]))
            if (m[0], m[1]) >= start and (m[0], m[1]) < end:
                o = None
            if o is not None and c is not None:
                kept.append([o[0], o[1], c[0], c[1]])
        self.closers = kept
        self.row, self.col, self.anchor = end_row, end_col, None

        for r, before in trim_now:
            target = r + row_delta if (r > r2 or before) else r
            if 0 <= target < len(self.lines) and self.lines[target] and _is_blank(self.lines[target]):
                removed = len(self.lines[target])
                self.lines[target] = ""
                self._invalidate(target)
                if self.row == target:
                    self.col = max(0, self.col - removed)

        if auto_ws and start == end:
            for r in range(r1, end_row + 1):
                old = old_start_line if r == r1 else ""
                if r == r1 and not _is_blank(old):
                    continue
                cur = self.lines[r]
                if cur and cur != old and _is_blank(cur):
                    self.trim_rows.append(r)

    def _validate_closers(self):
        a, b = self.selection()
        self.closers = [m for m in self.closers if (m[0], m[1]) < a and b <= (m[2], m[3])]

    def _delete_selection(self):
        a, b = self.selection()
        self._replace(a, b, "")

    # ----------------------------------------------------------------- typing
    def type_char(self, ch: str):
        assert len(ch) == 1 and ch != "\n"
        self._goal = None
        if self.has_selection():
            a, b = self.selection()
            self._replace(a, b, ch)
        elif not self._type_auto_indent(ch):
            close = None
            if self._is_overtype(ch):
                self.col += 1
            else:
                close = self._auto_close(ch)
                if close is not None:
                    row, col = self.row, self.col
                    self._replace(self.pos, self.pos, ch + close)
                    self.col = col + 1
                    if len(close) == 1:
                        self.closers.append([row, col, row, col + 1])
                elif not self._type_electric(ch):
                    self._replace(self.pos, self.pos, ch)
        self._validate_closers()

    def _is_overtype(self, ch: str) -> bool:
        mode = self.settings.auto_closing_overtype
        if mode == "never" or not any(p.close == ch for p in self.lang.pairs):
            return False
        if self.line[self.col:self.col + 1] != ch:
            return False
        if ch in "\"'`" and self.col >= 1 and self.line[self.col - 1] == "\\":
            return False
        if mode == "auto" and not any((m[2], m[3]) == self.pos for m in self.closers):
            return False
        return True

    def _find_pair(self, ch: str):
        best = None
        for p in self.lang.pairs:
            if p.open.endswith(ch) and (best is None or len(p.open) > len(best.open)):
                if self.before.endswith(p.open[:-1]):
                    best = p
        return best

    def _auto_close(self, ch: str) -> str | None:
        """Returns the text to insert after ``ch`` or None (no auto-closing)."""
        pair = self._find_pair(ch)
        if pair is None:
            return None
        s, lang = self.settings, self.lang
        is_quote = ch in "\"'`"
        check_balance = False
        if is_quote:
            mode = s.auto_closing_quotes
        elif lang.block_comment and lang.block_comment[0] in pair.open:
            mode = s.auto_closing_comments
        else:
            mode = s.auto_closing_brackets
            check_balance = True
        if mode == "never":
            return None
        contained = None
        if len(pair.open) > 1:
            for c in lang.pairs:
                if c.open != pair.open and c.open in pair.open and pair.close.endswith(c.close):
                    if contained is None or len(c.open) > len(contained.open):
                        contained = c
        contained_close = contained.close if contained else ""
        before, after = self.before, self.after
        if after:
            nxt = after[0]
            starts_open = any(after.startswith(p.open) for p in lang.pairs)
            starts_close = any(after.startswith(p.close) for p in lang.pairs)
            before_closer = starts_close and not starts_open
            if mode == "always":
                allowed = True
            elif mode == "beforeWhitespace":
                allowed = nxt in " \t"
            elif lang.explicit_close_before or is_quote:
                allowed = nxt in lang.auto_close_before
            else:
                allowed = nxt in "'\"`" + lang.auto_close_before
            if not before_closer and not allowed:
                return None
        if check_balance and mode != "always" and self._unmatched_closer_after(pair.open):
            return None
        if len(pair.open) == 1 and ch in "\"'" and mode != "always" and before:
            prev = before[-1]
            if prev not in WORD_SEPARATORS and not prev.isspace():
                return None
        if pair.not_in:
            if self.line:
                kind = self._types(self.row)[max(self.col - 1, 0)]
                if _TYPE_NAMES.get(kind) in pair.not_in:
                    return None
            if _TYPE_NAMES.get(self._inline_type(self.row, self.col)) in pair.not_in:
                return None
        if contained and after.startswith(contained_close):
            return pair.close[: len(pair.close) - len(contained_close)]
        return pair.close

    def _code_chars(self, row: int) -> str:
        line, types = self.lines[row], self._types(row)
        return "".join(c if t == OTHER else " " for c, t in zip(line, types))

    def _unmatched_closer_after(self, open_bracket: str) -> bool:
        close = next((c for o, c in self.lang.brackets if o == open_bracket), None)
        if close is None or len(close) != 1:
            return False
        open_bracket = open_bracket[-1]
        depth_before = 0
        for r in range(0, self.row + 1):
            code = self._code_chars(r)
            if r == self.row:
                code = code[: self.col]
            for c in code:
                if c == open_bracket:
                    depth_before += 1
                elif c == close and depth_before:
                    depth_before -= 1
        depth = 0
        for r in range(self.row, len(self.lines)):
            code = self._code_chars(r)
            if r == self.row:
                code = code[self.col:]
            for c in code:
                if c == open_bracket:
                    depth += 1
                elif c == close:
                    if depth:
                        depth -= 1
                    elif depth_before:
                        depth_before -= 1
                    else:
                        return True
        return False

    def _matching_open_row(self, close: str) -> int | None:
        opener = next((o for o, c in self.lang.brackets if c == close and len(o) == 1), None)
        if opener is None:
            return None
        depth = 0
        for r in range(self.row, -1, -1):
            code = self._code_chars(r)
            if r == self.row:
                code = code[: self.col]
            for c in reversed(code):
                if c == close:
                    depth += 1
                elif c == opener:
                    if depth == 0:
                        return r
                    depth -= 1
        return None

    def _type_electric(self, ch: str) -> bool:
        if not any(c.endswith(ch) for _, c in self.lang.brackets):
            return False
        types = self._types(self.row)
        kind = types[min(self.col, len(types) - 1)] if types else self._inline_type(self.row, self.col)
        if kind != OTHER:
            return False
        text = self.before + ch
        found = None
        for o, c in self.lang.brackets:
            for b in (o, c):
                i = text.rfind(b)
                if i >= 0 and (found is None or i + len(b) > found[0] + len(found[1])):
                    found = (i, b)
        if found is None:
            return False
        i, bracket = found
        if any(bracket == o for o, _ in self.lang.brackets) or not _is_blank(text[:i]):
            return False
        if len(bracket) != 1:
            return False
        match_row = self._matching_open_row(bracket)
        if match_row is None or match_row == self.row:
            return False
        indent = self.normalize_indentation(leading_ws(self.lines[match_row]))
        line = self.line
        first = len(leading_ws(line)) if not _is_blank(line) else self.col
        prefix = line[first:self.col]
        self._replace((self.row, 0), self.pos, indent + prefix + ch)
        return True

    # ----------------------------------------------- indentation rules (Full)
    def _inherit_indent(self, lines_get, row: int, honor: bool = True):
        """Port of getInheritIndentForLine.  ``row`` is 0-based.

        Returns None or (indentation, indent_action_bool, line_row).
        """
        lang = self.lang
        if self._level < 4 or not lang.has_indent_rules:
            return None

        def proc(r):
            return self._processed(r, lines_get(r))

        def inc(r):
            return bool(lang.increase and lang.increase.search(proc(r)))

        def dec(r):
            return bool(lang.decrease and lang.decrease.search(proc(r)))

        def nxt(r):
            return bool(lang.indent_next and lang.indent_next.search(proc(r)))

        def ignore(r):
            return bool(lang.unindented and lang.unindented.search(proc(r)))

        if row <= 0:
            return ("", False, None)
        for prior in range(row - 1, -1, -1):
            if lines_get(prior) != "":
                break
            if prior == 0:
                return ("", False, None)
        region = self._region_at_line_start(row)
        preceding = None
        for r in range(row - 1, -1, -1):
            if self._region_at_line_start(r) != region:
                break
            if ignore(r) or _is_blank(lines_get(r)):
                preceding = r
                continue
            preceding = r
            break
        else:
            preceding = None
        if preceding is None:
            return None
        if inc(preceding) or nxt(preceding):
            return (leading_ws(lines_get(preceding)), True, preceding)
        if dec(preceding):
            return (leading_ws(lines_get(preceding)), False, preceding)
        if preceding == 0:
            return (leading_ws(lines_get(0)), False, 0)
        prev = preceding - 1
        prev_text = lines_get(prev)
        p_inc = bool(lang.increase and lang.increase.search(prev_text))
        p_dec = bool(lang.decrease and lang.decrease.search(prev_text))
        p_nxt = bool(lang.indent_next and lang.indent_next.search(prev_text))
        if not (p_inc or p_dec) and p_nxt:
            stop = -1
            for i in range(prev - 1, -1, -1):
                if nxt(i):
                    continue
                stop = i
                break
            return (leading_ws(lines_get(stop + 1)), False, stop + 1)
        if honor:
            return (leading_ws(lines_get(preceding)), False, preceding)
        for i in range(preceding, -1, -1):
            if inc(i):
                return (leading_ws(lines_get(i)), True, i)
            if nxt(i):
                stop = -1
                for j in range(i - 1, -1, -1):
                    if nxt(i):
                        continue
                    stop = j
                    break
                return (leading_ws(lines_get(stop + 1)), False, stop + 1)
            if dec(i):
                return (leading_ws(lines_get(i)), False, i)
        return (leading_ws(lines_get(0)), False, 0)

    def _region_at_line_start(self, row: int) -> str:
        if self.lang_id != "html":
            return self.lang_id
        if row >= len(self.lines):
            return self._lex_row(len(self.lines) - 1)[3][0]
        _, regions, _, _, _ = self._lex_row(row)
        return regions[0] if regions else self._states[row][0]

    def _type_auto_indent(self, ch: str) -> bool:
        """Port of AutoIndentOperation (autoIndent "full" only)."""
        if self._level < 4:
            return False
        if self.lang_id == "html" and self._region(self.row, self.col) != self._region_at_line_start(self.row):
            return False
        lang = self._lang_at(self.row, self.col)
        if not lang.has_indent_rules or lang.decrease is None:
            return False
        types = self._types(self.row)
        line = self.line

        def proc(a, b):
            return self._strip(line[a:b], types[a:b])

        before, after = proc(0, self.col), proc(self.col, len(line))
        around, with_ch = before + after, before + ch + after
        indentation = None
        if not lang.decrease.search(around) and lang.decrease.search(with_ch):
            r = self._inherit_indent(lambda i: self.lines[i], self.row, honor=False)
            if r is None:
                return False
            indentation = r[0] if r[1] else self.unshift_indent(r[0])
        elif self.row > 0 and lang.indent_next and lang.increase:
            prev = self.lines[self.row - 1]
            if lang.indent_next.search(prev) and lang.increase.search(with_ch):
                r = self._inherit_indent(lambda i: self.lines[i], self.row, honor=False)
                if r is not None:
                    inferred = self.shift_indent(r[0])
                    if inferred == leading_ws(line) and _is_blank(around) and any(
                            p.open.endswith(ch) for p in self.lang.pairs):
                        indentation = r[0]
        if indentation is None:
            return False
        current = leading_ws(line)[: self.col]
        if indentation == self.normalize_indentation(current):
            return False
        close = self._auto_close(ch)
        text = self.normalize_indentation(indentation)
        if not _is_blank(line):
            text += line[len(leading_ws(line)):self.col]
        row = self.row
        if close is not None:
            self._replace((row, 0), self.pos, text + ch + close)
            self.col = len(text) + 1
            if len(close) == 1:
                self.closers.append([row, len(text), row, len(text) + 1])
        else:
            self._replace((row, 0), self.pos, text + ch, auto_ws=True)
        return True

    # ------------------------------------------------------------------ Enter
    def _on_enter(self, lang: Language, previous: str, before: str, after: str):
        """Port of OnEnterSupport.onEnter: returns (action, append, remove)."""
        if self._level >= 3:
            for rule in lang.enter_rules:
                if rule.before and not rule.before.search(before):
                    continue
                if rule.after and not rule.after.search(after):
                    continue
                if rule.previous and not rule.previous.search(previous):
                    continue
                return rule.indent, rule.append, rule.remove
        if self._level >= 2:
            if before and after:
                for o, c in lang.brackets:
                    if _open_rx(o).search(before) and _close_rx(c).search(after):
                        return "indentOutdent", "", 0
            if before:
                for o, _ in lang.brackets:
                    if _open_rx(o).search(before):
                        return "indent", "", 0
        return None

    def _enter_action(self, row: int, col: int):
        """Port of getEnterAction: (action, append_text, indentation) or None."""
        lang = self._lang_at(row, col)
        line = self.lines[row]
        types = self._types(row)

        def proc(a, b, text=line, tt=types):
            return self._strip(text[a:b], tt[a:b])

        before, after = proc(0, col), proc(col, len(line))
        previous = ""
        if row > 0:
            prev_line = self.lines[row - 1]
            if self._region(row - 1, len(prev_line)) == lang.id or self.lang_id != "html":
                previous = proc(0, len(prev_line), prev_line, self._types(row - 1))
        result = self._on_enter(lang, previous, before, after)
        if result is None:
            return None
        action, append, remove = result
        if not append:
            append = "\t" if action in ("indent", "indentOutdent") else ""
        elif action == "indent":
            append = "\t" + append
        indentation = leading_ws(line)[:col]
        if remove:
            indentation = indentation[: len(indentation) - remove]
        return action, append, indentation

    def _enter(self):
        if self.has_selection():
            self._delete_selection()
        # A line break between an opener and its auto-inserted closer ends
        # the editor's "type over the closer" tracking.
        self.closers = []
        row, col = self.row, self.col
        line = self.line
        norm = self.normalize_indentation
        if self._level == 0:
            return self._replace(self.pos, self.pos, "\n", auto_ws=True)
        keep = leading_ws(line)[:col]
        if self._level == 1:
            return self._replace(self.pos, self.pos, "\n" + norm(keep), auto_ws=True)
        r = self._enter_action(row, col)
        if r is not None:
            action, append, indentation = r
            if action in ("none", "indent"):
                return self._replace(self.pos, self.pos, "\n" + norm(indentation + append), auto_ws=True)
            if action == "indentOutdent":
                normal, increased = norm(indentation), norm(indentation + append)
                self._replace(self.pos, self.pos, "\n" + increased + "\n" + normal, auto_ws=True)
                self.row -= 1
                self.col = len(increased)
                return
            if action == "outdent":
                return self._replace(self.pos, self.pos, "\n" + norm(self.unshift_indent(indentation) + append),
                                     auto_ws=True)
        if self._level >= 4:
            lang = self._lang_at(row, col)
            if lang.has_indent_rules:
                types = self._types(row)

                def proc(a, b):
                    return self._strip(line[a:b], types[a:b])

                before_text, after_text = proc(0, col), proc(col, len(line))

                def virtual(i):
                    return before_text if i == row else self.lines[i]

                inherited = self._inherit_indent(virtual, row + 1)
                if inherited is None:
                    after_indent = leading_ws(before_text)
                else:
                    after_indent = inherited[0]
                    if inherited[1]:
                        after_indent = self.shift_indent(after_indent)
                    if lang.decrease and lang.decrease.search(after_text):
                        after_indent = self.unshift_indent(after_indent)
                end = self.pos
                first = len(leading_ws(line))
                if not _is_blank(line):
                    end = (row, max(col, first))
                else:
                    end = (row, len(line))
                text = "\n" + norm(after_indent)
                self._replace(self.pos, end, text, auto_ws=True)
                return
        self._replace(self.pos, self.pos, "\n" + norm(keep), auto_ws=True)

    # -------------------------------------------------------------------- Tab
    def _good_indent(self, row: int):
        action = False
        indentation = ""
        inherited = self._inherit_indent(lambda i: self.lines[i], row, honor=False)
        if inherited is not None:
            indentation, action = inherited[0], inherited[1]
        elif row > 0:
            last = row - 1
            while last >= 0 and _is_blank(self.lines[last]):
                last -= 1
            if last < 0:
                return None
            r = self._enter_action(last, len(self.lines[last])) if self._level >= 2 else None
            if r is not None:
                indentation = r[2] + r[1]
        if action:
            indentation = self.normalize_indentation(self.shift_indent(indentation))
        return indentation or None

    def _tab(self):
        if self.has_selection():
            self._delete_selection()
        line = self.line
        if _is_blank(line):
            good = self.normalize_indentation(self._good_indent(self.row) or "\t")
            if not line.startswith(good):
                self._replace((self.row, 0), (self.row, len(line)), good, auto_ws=True)
                return
        if self.settings.insert_spaces:
            size = self.settings.tab_size
            text = " " * (size - self._visible(self.before) % size)
        else:
            text = "\t"
        self._replace(self.pos, self.pos, text, auto_ws=True)

    # ----------------------------------------------------------------- delete
    def _backspace(self):
        if self.has_selection():
            return self._delete_selection()
        s, line, col = self.settings, self.line, self.col
        if 1 <= col < len(line) and s.auto_closing_delete != "never":
            ch, nxt = line[col - 1], line[col]
            mode = s.auto_closing_quotes if ch in "\"'`" else s.auto_closing_brackets
            if not (s.auto_closing_brackets == "never" and s.auto_closing_quotes == "never") and mode != "never":
                if any(p.open == ch and p.close == nxt for p in self.lang.pairs):
                    if s.auto_closing_delete != "auto" or any((m[2], m[3]) == self.pos for m in self.closers):
                        return self._replace((self.row, col - 1), (self.row, col + 1), "")
        if col == 0:
            if self.row == 0:
                return
            prev = self.row - 1
            return self._replace((prev, len(self.lines[prev])), self.pos, "")
        if s.use_tab_stops:
            indent_end = len(line) if _is_blank(line) else len(leading_ws(line))
            if col <= indent_end:
                width = self._visible(line[:col])
                target = ((width - 1) // s.tab_size) * s.tab_size
                to_col = col
                while to_col > 0 and self._visible(line[:to_col - 1]) >= target:
                    to_col -= 1
                    if self._visible(line[:to_col]) == target:
                        break
                return self._replace((self.row, to_col), self.pos, "")
        self._replace((self.row, col - 1), self.pos, "")

    def _delete(self):
        if self.has_selection():
            return self._delete_selection()
        if self.col < len(self.line):
            return self._replace(self.pos, (self.row, self.col + 1), "")
        if self.row + 1 >= len(self.lines):
            return
        nxt = self.lines[self.row + 1]
        col = 0
        if not _is_blank(self.line) and not _is_blank(nxt):
            col = len(leading_ws(nxt))
        self._replace(self.pos, (self.row + 1, col), "")

    # --------------------------------------------------------------- movement
    def _col_for_visible(self, text: str, goal: int) -> int:
        width = 0
        for i, ch in enumerate(text):
            nxt = (width // self.settings.tab_size + 1) * self.settings.tab_size if ch == "\t" else width + 1
            if nxt > goal:
                return i if goal - width < nxt - goal else i + 1
            width = nxt
        return len(text)

    def _move(self, name: str, select: bool):
        if not select and self.has_selection() and name in ("left", "right"):
            a, b = self.selection()
            self.row, self.col = a if name == "left" else b
            self.anchor = None
            return
        if select and self.anchor is None:
            self.anchor = self.pos
        if not select:
            self.anchor = None
        row, col, line = self.row, self.col, self.line
        last = len(self.lines) - 1
        if name == "left":
            if col > 0:
                self.col -= 1
            elif row > 0:
                self.row, self.col = row - 1, len(self.lines[row - 1])
        elif name == "right":
            if col < len(line):
                self.col += 1
            elif row < last:
                self.row, self.col = row + 1, 0
        elif name in ("up", "down"):
            goal = self._goal if self._goal is not None else self._visible(line[:col])
            if name == "up" and row == 0:
                self.col, goal = 0, 0
            elif name == "down" and row == last:
                self.col = len(line)
                goal = self._visible(line)
            else:
                self.row = row - 1 if name == "up" else row + 1
                self.col = self._col_for_visible(self.lines[self.row], goal)
            self._goal = goal
            return
        elif name == "home":
            first = len(leading_ws(line)) if not _is_blank(line) else len(line)
            self.col = 0 if col == first else first
        elif name == "end":
            self.col = len(line)
        elif name == "ctrl+home":
            self.row, self.col = 0, 0
        elif name == "ctrl+end":
            self.row, self.col = last, len(self.lines[last])
        else:
            raise ValueError(name)

    # ------------------------------------------------------------- public API
    MOVES = ("left", "right", "up", "down", "home", "end", "ctrl+home", "ctrl+end")

    def key(self, name: str):
        """Apply one key press (or chord such as ``shift+end``)."""
        if name not in ("up", "down", "shift+up", "shift+down"):
            self._goal = None
        if name == "enter":
            self._enter()
        elif name == "tab":
            self._tab()
        elif name == "backspace":
            self._backspace()
        elif name == "delete":
            self._delete()
        elif name == "escape":
            self.anchor = None
        elif name == "ctrl+a":
            last = len(self.lines) - 1
            self.anchor = (0, 0)
            self.row, self.col = last, len(self.lines[last])
        elif name in self.MOVES:
            self._move(name, False)
        elif name.startswith("shift+") and name[6:] in self.MOVES:
            self._move(name[6:], True)
        else:
            raise ValueError(f"unknown key {name!r}")
        self._validate_closers()

    def paste(self, text: str):
        """Insert text literally (clipboard paste of a short fragment)."""
        a, b = self.selection()
        self._goal = None
        self._replace(a, b, text)
        self._validate_closers()

    def goto_line(self, number: int):
        """Go to Line (Ctrl+G): cursor to column 1 of the 1-based line."""
        self.row = max(0, min(len(self.lines) - 1, number - 1))
        self.col = 0
        self.anchor = None
        self._goal = None
        self._validate_closers()
