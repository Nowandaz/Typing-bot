"""Turns a source file into a sequence of editor actions.

The planner drives a :class:`VirtualEditor` - a model of VS Code's editor -
and decides, keystroke by keystroke, what a person would press to end up
with exactly the target file:

* after Enter it looks at the indentation the editor produced and corrects
  it (Backspace to dedent, Tab or spaces to indent) instead of typing the
  file's own leading whitespace on top of it;
* it lets the editor auto-close brackets and quotes, types over the closers,
  and when a block is opened it presses Enter between the pair and later
  steps over the closing bracket with the arrow keys;
* it makes typos and "first draft" slips and repairs them with Backspace or
  by selecting the wrong token and retyping it;
* it leaves a comment or import out, carries on, then navigates back up to
  add it;
* it uses an outline of the code (functions, classes, loops, conditions) for
  pacing, status messages and for jumping back to look at a finished block.

Every embellishment is run as a transaction against the model: if the buffer
afterwards is not identical to what plain typing would have produced, the
transaction is rolled back.  ``build()`` finally asserts that the model's
buffer equals the target.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .actions import GotoLine, Key, Note, Paste, Pause, Plan, Type
from .editor import EditorSettings, VirtualEditor, leading_ws
from .languages import LANGUAGE_LABELS, Lexer, strip_code
from .outline import CLASS, FUNCTION, Block, build_outline, summarize


class PlanError(Exception):
    pass


@dataclass
class PlanOptions:
    seed: int | None = None
    mistakes: float = 0.035     # chance per word of a typo that is fixed at once
    rewrites: float = 0.10      # chance per eligible line of a draft token that is selected and retyped
    revisits: float = 0.45      # chance per eligible comment/import of leaving it out and coming back
    reviews: float = 0.25       # chance after a function/class of jumping back to look at it
    dismiss_suggestions: bool = True   # press Escape before navigation/Enter so IntelliSense cannot intercept
    replace_existing: bool = False     # start with Ctrl+A, Backspace
    existing_text: str = ""            # what the editor buffer holds when replace_existing is set


_NEIGHBOURS = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh", "u": "yij", "i": "uok", "o": "ipl",
    "p": "ol", "a": "sqz", "s": "adw", "d": "sfe", "f": "dgr", "g": "fht", "h": "gjy", "j": "hku", "k": "jli",
    "l": "kop", "z": "xas", "x": "zcs", "c": "xvd", "v": "cbf", "b": "vng", "n": "bmh", "m": "nj",
}
# A name someone might reach for first and then change their mind about.  Only
# used the first time a name appears, never for a name that already exists.
_FIRST_THOUGHTS = {
    "item": "entry", "index": "idx", "result": "res", "value": "val", "count": "total", "error": "err",
    "event": "evt", "message": "msg", "number": "num", "element": "node", "callback": "handler",
    "response": "result", "request": "req", "config": "options", "lines": "rows", "path": "file",
}
_KEYWORDS = {
    "return", "import", "from", "class", "def", "function", "const", "let", "var", "while", "for", "elif", "else",
    "true", "false", "none", "null", "undefined", "self", "this", "async", "await", "yield", "break", "continue",
    "export", "default", "print", "pass", "raise", "except", "finally", "lambda", "with", "new", "typeof", "switch",
    "case", "try", "catch", "throw", "in", "is", "not", "and", "or", "if", "of", "str", "int", "float", "bool",
}
# Boundary and strictness slips; never a comparison that means the opposite.
_OPERATOR_DRAFTS = {"<=": "<", ">=": ">", "===": "==", "!==": "!="}
_GUARDED = {"enter", "tab", "left", "right", "up", "down", "home", "end", "ctrl+home", "ctrl+end",
            "shift+left", "shift+right", "shift+home", "shift+end"}
_IMPORT_PY = re.compile(r"^(?:import\s+(\w+)|from\s+[\w.]+\s+import\s+(\w+))")
_IMPORT_JS = re.compile(r"^(?:import\b.*?\b(\w+)\b.*\bfrom\b|const\s+\{?\s*(\w+).*=\s*require\()")


def normalize_target(text: str) -> str:
    """Line endings to LF; whitespace-only lines become empty (VS Code trims
    them as you type, so they cannot be reproduced keystroke for keystroke)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join("" if not line.strip() else line for line in text.split("\n"))


class Planner:
    def __init__(self, text: str, lang_id: str, settings: EditorSettings | None = None,
                 options: PlanOptions | None = None):
        self.target = normalize_target(text)
        self.T = self.target.split("\n")
        self.lang_id = lang_id
        self.settings = settings or EditorSettings()
        self.opt = options or PlanOptions()
        self.blocks: list[Block] = build_outline(self.target, lang_id)
        self.code = strip_code(Lexer(lang_id), self.T)
        self.match = self._match_brackets()
        self.warnings: list[str] = []

    # --------------------------------------------------------------- analysis
    def _match_brackets(self) -> dict:
        pairs = {"(": ")", "[": "]", "{": "}"}
        closers = {v: k for k, v in pairs.items()}
        stack, match = [], {}
        for r, line in enumerate(self.code):
            for c, ch in enumerate(line):
                if ch in pairs:
                    stack.append((ch, r, c))
                elif ch in closers:
                    for k in range(len(stack) - 1, -1, -1):
                        if stack[k][0] == closers[ch]:
                            _, orow, ocol = stack[k]
                            match[(orow, ocol)] = (r, c)
                            del stack[k:]
                            break
        return match

    def _is_comment_line(self, i: int) -> bool:
        line = self.T[i].strip()
        if not line or self.code[i].strip():
            return False
        if self.lang_id == "python":
            return line.startswith("#") and not line.startswith("#!")
        if self.lang_id in ("javascript", "typescript"):
            return line.startswith("//")
        return False

    def _import_name(self, i: int) -> str | None:
        line = self.T[i]
        if line[:1] in " \t" or line.count("(") != line.count(")") or line.rstrip().endswith(("(", "{", ",")):
            return None
        rx = _IMPORT_PY if self.lang_id == "python" else _IMPORT_JS if self.lang_id in (
            "javascript", "typescript") else None
        m = rx.match(line) if rx else None
        if not m:
            return None
        return m.group(1) or m.group(2)

    # ------------------------------------------------------------------ build
    def build(self) -> Plan:
        attempts = [self.opt,
                    PlanOptions(**{**self.opt.__dict__, "rewrites": 0.0, "revisits": 0.0, "reviews": 0.0}),
                    PlanOptions(**{**self.opt.__dict__, "mistakes": 0.0, "rewrites": 0.0, "revisits": 0.0,
                                   "reviews": 0.0})]
        error = None
        for n, opt in enumerate(attempts):
            try:
                return self._build(opt)
            except PlanError as exc:
                error = exc
                self.warnings.append(f"Plan attempt {n + 1} was discarded: {exc}")
        raise PlanError(f"Could not plan this file for the current editor settings: {error}")

    def _build(self, opt: PlanOptions) -> Plan:
        self.o = opt
        self.rng = random.Random(opt.seed)
        self.ed = VirtualEditor(self.lang_id, self.settings, opt.existing_text if opt.replace_existing else "")
        self.actions: list = []
        self.placed: list[int] = []
        self.pending: list[int] = []
        self.deferred: tuple | None = None       # (target line, due line, trigger name)
        self.stats = {"typos": 0, "rewrites": 0, "revisits": 0, "reviews": 0, "bracket_blocks": 0}
        self._last_typo = -10 ** 6           # action index of the last typo
        self._last_rewrite = -10 ** 6        # target line of the last rewrite
        self._suggest = False
        self._quiet = 0
        n = len(self.T)
        starts = {}
        for b in self.blocks:
            starts.setdefault(b.start, b)
        ends = {}
        for b in self.blocks:
            if b.kind in (FUNCTION, CLASS) and b.end - b.start >= 2:
                ends[b.end] = b

        self._note(f"{LANGUAGE_LABELS.get(self.lang_id, self.lang_id)}: {summarize(self.blocks)}")
        if opt.replace_existing and opt.existing_text:
            self._key("ctrl+a")
            self._key("backspace")
        i = 0
        while i < n:
            if i > 0 and self._should_defer(i):
                delay = self.rng.randint(2, 5)
                self.deferred = (i, i + delay, self._import_name(i))
                self.stats["revisits"] += 1
                i += 1
                continue
            block = starts.get(i)
            if block is not None:
                self._note(f"Typing {block.label}")
                self._pause("think" if block.kind in (FUNCTION, CLASS) else "plan")
            if i == 0:
                self.placed.append(0)
            else:
                self._advance_to(i)
            if self._settle_line(i):
                self._write_line(i)
            self._pause("line")
            if self.deferred and self._deferred_due(i):
                self._insert_deferred()
            if i in ends and self.rng.random() < self.o.reviews:
                self._review(ends[i])
            i += 1
        if self.deferred:
            self._insert_deferred()
        self._clear_rest()
        if self.pending:
            raise PlanError("closing brackets left over below the cursor")
        if self.ed.text() != self.target:
            raise PlanError("model buffer does not match the target")
        self._note("Finished typing")
        return Plan(self.target, self.lang_id, self.actions, self.blocks, dict(self.stats), list(self.warnings))

    # ------------------------------------------------------------ primitives
    def _emit(self, action):
        self.actions.append(action)

    def _note(self, text: str):
        if not self._quiet:
            self._emit(Note(text))

    def _pause(self, kind: str):
        if self.actions and isinstance(self.actions[-1], Pause):
            return
        self._emit(Pause(kind))

    def _key(self, name: str, times: int = 1):
        for _ in range(times):
            if self.o.dismiss_suggestions and self._suggest and name in _GUARDED:
                self._emit(Key("escape"))
                self._suggest = False
            self.ed.key(name)
            self._emit(Key(name))

    def _type(self, ch: str):
        self.ed.type_char(ch)
        self._emit(Type(ch))
        self._suggest = True

    def _paste(self, text: str):
        self.ed.paste(text)
        self._emit(Paste(text))

    def _snapshot(self):
        return (self.ed.clone(), len(self.actions), list(self.placed), list(self.pending), self._suggest,
                dict(self.stats), self.deferred)

    def _restore(self, snap):
        self.ed, count, self.placed, self.pending, self._suggest, self.stats, self.deferred = snap
        self.ed = self.ed.clone()
        del self.actions[count:]

    # ------------------------------------------------------- line transitions
    def _clear_rest(self):
        """Delete auto-inserted closers that the target does not want."""
        guard = 0
        while self.ed.after:
            before = len(self.ed.after)
            self._key("delete")
            guard += 1
            if len(self.ed.after) >= before or guard > 200:
                raise PlanError("could not clear text right of the cursor")

    def _split_target(self, i: int, rest: str) -> int | None:
        """If pressing Enter now would push ``rest`` onto its own line and a
        later target line starts with exactly that line, return its index."""
        ed = self.ed
        prev = self.placed[-1]
        probe = ed.clone()
        probe.key("enter")
        if probe.row != ed.row + 1 or len(probe.lines) != len(ed.lines) + 2:
            return None
        below = probe.lines[probe.row + 1]
        if below.strip() != rest.strip() or probe.after:
            return None
        if all(ch in ")]}" for ch in rest):
            code = self.code[prev][: len(ed.before)]
            stack = []
            for c, ch in enumerate(code):
                if ch in "([{":
                    stack.append(c)
                elif ch in ")]}" and stack:
                    stack.pop()
            if len(stack) < len(rest):
                return None
            rows = set()
            for k, ch in enumerate(rest):
                target = self.match.get((prev, stack[-1 - k]))
                if target is None or self.T[target[0]][target[1]] != ch:
                    return None
                if target[1] != len(leading_ws(self.T[target[0]])) + k:
                    return None
                rows.add(target[0])
            if len(rows) != 1:
                return None
            j = rows.pop()
        elif rest.strip() == "*/":
            j = next((k for k in range(i, len(self.T)) if self.T[k].strip().startswith("*/")), None)
            if j is None:
                return None
        else:
            return None
        if j <= i or not self.T[j].startswith(below):
            return None
        if self.pending and j >= self.pending[0]:
            return None
        if self.deferred and i <= self.deferred[0] <= j:
            return None
        if not self.T[i].strip() and j == i + 1:
            return None
        return j

    def _advance_to(self, i: int):
        """Move from the end of the current line to the start of line ``i``."""
        ed = self.ed
        if self.pending and self.pending[0] == i:
            self._clear_rest()
            self._key("down")
            self._key("end")
            self.pending.pop(0)
            self.placed.append(i)
            if not self.T[i].startswith(ed.before) or ed.after:
                raise PlanError(f"line {i + 1}: closing bracket line is not where it was expected")
            return
        rest = ed.after
        if rest:
            j = self._split_target(i, rest)
            if j is None:
                self._clear_rest()
            else:
                self.pending.insert(0, j)
                self.stats["bracket_blocks"] += 1
        self._key("enter")
        self.placed.append(i)

    def _settle_line(self, i: int):
        """Make what is left of the cursor a prefix of target line ``i``."""
        ed = self.ed
        want = self.T[i]
        if want == "" and ed.before and not ed.before.strip() and not ed.after:
            # Auto-indent whitespace on a line that stays blank: VS Code trims
            # it by itself when the next line is started with Enter.
            nxt = i + 1
            if nxt < len(self.T) and not (self.pending and self.pending[0] == nxt) and ed.row in ed.trim_rows:
                return False
        guard = 0
        while not want.startswith(ed.before):
            size = len(ed.before)
            self._key("backspace")
            guard += 1
            if len(ed.before) >= size or guard > 400:
                raise PlanError(f"line {i + 1}: could not correct the indentation")
        return True

    # ---------------------------------------------------------- typing a line
    def _write_line(self, i: int, allow_slips: bool = True):
        if allow_slips and self.o.rewrites and not self._quiet and i - self._last_rewrite >= 5 \
                and self.rng.random() < self.o.rewrites:
            if self._write_with_rewrite(i):
                self._last_rewrite = i
                return
        self._write_plain(i, allow_slips)

    def _write_plain(self, i: int, allow_slips: bool = True, want: str | None = None):
        ed = self.ed
        want = self.T[i] if want is None else want
        guard = 0
        while True:
            guard += 1
            if guard > 4 * len(want) + 50:
                raise PlanError(f"line {i + 1}: no progress")
            k = len(ed.before)
            if ed.before != want[:k]:
                raise PlanError(f"line {i + 1}: buffer diverged from the target")
            remaining = want[k:]
            if not remaining:
                break
            rest = ed.after
            ch = remaining[0]
            if rest and rest[0] == ch:
                probe = ed.clone()
                probe.type_char(ch)
                if probe.after != rest[1:] or probe.before != want[:k + 1]:
                    # not typed over by the editor: step across instead
                    m = 0
                    while m < len(rest) and m < len(remaining) and rest[m] == remaining[m]:
                        m += 1
                    if m == len(rest) and m > 1:
                        self._key("end")
                    else:
                        self._key("right", m)
                    continue
            if ch in " \t" and not want[:k].strip():
                if self._indent_step(want, k):
                    continue
            if ch == "\t":
                self._literal_tab(want, k)
                continue
            if allow_slips and self.o.mistakes and not self._quiet and self._maybe_typo(want, k):
                continue
            self._type_checked(i, want, k)

    def _indent_step(self, want: str, k: int) -> bool:
        """Use Tab for indentation when it lands on the target's whitespace."""
        ed = self.ed
        indent = leading_ws(want) if want.strip() else want
        probe = ed.clone()
        probe.key("tab")
        if probe.after == ed.after and len(probe.before) > k and indent.startswith(probe.before) \
                and probe.row == ed.row:
            self._key("tab")
            return True
        return False

    def _literal_tab(self, want: str, k: int):
        ed = self.ed
        probe = ed.clone()
        probe.key("tab")
        if probe.before == want[:k + 1] and probe.after == ed.after:
            self._key("tab")
        else:
            self._paste("\t")

    def _type_checked(self, i: int, want: str, k: int):
        """Type want[k] and make sure the line still matches afterwards."""
        ed = self.ed
        ch = want[k]
        if ord(ch) < 32:
            raise PlanError(f"line {i + 1}: control character {ch!r} cannot be typed")
        probe = ed.clone()
        probe.type_char(ch)
        if probe.before == want[:k + 1] and probe.row == ed.row:
            self._type(ch)
            return
        # The editor re-indented the line (electric bracket / indentation
        # rules).  Type it, then put the indentation back by hand.
        typed = probe.before
        if probe.row != ed.row or typed.lstrip(" \t") != want[:k + 1].lstrip(" \t"):
            raise PlanError(f"line {i + 1}: unexpected editor reaction to {ch!r}")
        self._type(ch)
        tail = len(ed.after)
        content = len(ed.before.lstrip(" \t"))
        self._key("home")
        if len(ed.before) != len(leading_ws(ed.line)):
            self._key("home")
        indent = leading_ws(want)
        guard = 0
        while ed.before != indent:
            guard += 1
            if guard > 200:
                raise PlanError(f"line {i + 1}: could not restore indentation")
            if indent.startswith(ed.before):
                nxt = indent[len(ed.before)]
                if nxt == "\t":
                    probe = ed.clone()
                    probe.key("tab")
                    if probe.before == indent[:len(ed.before) + 1]:
                        self._key("tab")
                    else:
                        self._paste("\t")
                else:
                    self._type(" ")
            else:
                self._key("backspace")
        if tail == 0:
            self._key("end")
        else:
            self._key("right", content)
        if ed.before != want[:k + 1]:
            raise PlanError(f"line {i + 1}: could not restore indentation")

    # ------------------------------------------------------------ corrections
    def _maybe_typo(self, want: str, k: int) -> bool:
        """Possibly mistype the word around want[k] the way fingers do.

        Slips are the common motor errors - a neighbouring key, two letters
        in the wrong order, a doubled letter, a skipped letter - and they are
        noticed at once, a letter or two later, or at the end of the word.
        They are rarer in short words, never hit the first letter, and never
        come in clusters.
        """
        ch = want[k]
        low = ch.lower()
        if low not in _NEIGHBOURS or k == 0 or not want[k - 1].isalpha():
            return False
        start = k
        while start > 0 and want[start - 1].isalpha():
            start -= 1
        end = k
        while end < len(want) and want[end].isalpha() and ord(want[end]) < 128:
            end += 1
        length = end - start
        if length < 4 or len(self.actions) - self._last_typo < 90:
            return False
        # about `mistakes` per five-letter word, more for long words
        if self.rng.random() >= self.o.mistakes * min(2.0, length / 5) / (length - 1):
            return False
        rest_of_word = want[k + 1:end]
        style = self.rng.choices(["neighbour", "swap", "double", "skip"], [40, 25, 15, 20])[0]
        if style in ("swap", "skip") and (not rest_of_word or rest_of_word[0].lower() == low):
            style = "neighbour"
        if style == "double" and rest_of_word[:1].lower() == low:
            style = "neighbour"
        if style == "neighbour":
            wrong = self.rng.choice(_NEIGHBOURS[low])
            slip, good = (wrong.upper() if ch.isupper() else wrong), ch
        elif style == "swap":
            slip, good = rest_of_word[0] + ch, ch + rest_of_word[0]
        elif style == "double":
            slip, good = ch + ch, ch
        else:
            slip, good = rest_of_word[0], ch + rest_of_word[0]
        following = want[k + len(good):end]
        notice = self.rng.choices(["now", "soon", "word"], [45, 35, 20])[0]
        if style == "skip" and notice == "now":
            notice = "soon"               # a missing letter is only seen once more is typed
        if notice == "soon":
            tail = following[: self.rng.randint(1, 2)]
        elif notice == "word" and len(following) <= 5:
            tail = following
        else:
            tail = ""
        typed, correct = slip + tail, good + tail
        straight = self.ed.clone()
        for c in correct:
            straight.type_char(c)
        snap = self._snapshot()
        try:
            for c in typed:
                self._type(c)
            self._pause("notice")
            self._key("backspace", len(typed))
            for c in correct:
                self._type(c)
        except Exception:
            self._restore(snap)
            return False
        ed = self.ed
        if ed.text() != straight.text() or ed.pos != straight.pos or ed.closers != straight.closers:
            self._restore(snap)
            return False
        self.stats["typos"] += 1
        self._last_typo = len(self.actions)
        return True

    def _names_before(self, i: int, col: int):
        """Identifiers already typed above (line i, col).

        Returns (names, attrs, pairs): plain names and attribute names with
        the line each was last seen on, and the set of attribute pairs that
        have appeared together on one line on the same object (``w.width``
        and ``w.height``) - the ones that really do get mixed up.
        """
        names, attrs, pairs = {}, {}, set()
        for r in range(0, i + 1):
            code = self.code[r] if r < i else self.code[r][:col]
            on_line = {}
            for m in re.finditer(r"(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)", code):
                owner, word = m.group(1), m.group(2)
                if m.start() > 0 and code[m.start() - 1] == "." and owner is None:
                    attrs[word] = r
                    continue
                if owner is None:
                    if word.lower() not in _KEYWORDS:
                        names[word] = r
                    continue
                if owner.lower() not in _KEYWORDS or owner in ("self", "this"):
                    names.setdefault(owner, r)
                attrs[word] = r
                for other in on_line.get(owner, ()):
                    if other != word:
                        pairs.add((owner, other, word))
                        pairs.add((owner, word, other))
                on_line.setdefault(owner, []).append(word)
        return names, attrs, pairs

    def _draft_for(self, i: int):
        """Pick a token on line ``i`` and the slip a programmer could make.

        The slips are ones that still read as reasonable code:
        * a name that already exists and is easily confused with the right
          one (singular/plural, a sibling such as ``width``/``height`` on the
          same object, or a name sharing a prefix);
        * a first idea for a brand-new name that is then renamed;
        * an off-by-one in a bound, range or index;
        * ``<`` for ``<=`` (and the like) at a boundary.
        """
        code = self.code[i]
        if self.lang_id not in ("python", "javascript", "typescript") or not code.strip():
            return None
        everything = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", "\n".join(self.code)))
        options, weights = [], []
        for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", code):
            word = m.group(0)
            if word.lower() in _KEYWORDS or len(word) < 3:
                continue
            is_attr = m.start() > 0 and code[m.start() - 1] == "."
            names, attrs, pairs = self._names_before(i, m.start())
            known = attrs if is_attr else names
            owner = re.search(r"([A-Za-z_]\w*)\.$", code[:m.start()]) if is_attr else None
            confusable = []
            for other, seen_on in known.items():
                if other == word or other.lower() == word.lower() or i - seen_on > 25:
                    continue
                plural = other == word + "s" or word == other + "s"
                prefix = len(word) >= 5 and len(other) >= 5 and other[:3] == word[:3] \
                    and abs(len(other) - len(word)) <= 3
                sibling = owner is not None and (owner.group(1), word, other) in pairs
                if plural or prefix or sibling:
                    confusable.append(other)
            if confusable:
                options.append((m.start(), m.end(), self.rng.choice(sorted(confusable)), "name"))
                weights.append(4)
                continue
            first = _FIRST_THOUGHTS.get(word)
            if first and not is_attr and word not in names and first not in everything:
                options.append((m.start(), m.end(), first, "name"))
                weights.append(3)
        # off-by-one only where people make them: bounds, ranges and indexes
        for m in re.finditer(r"(?:(?<=[<>]\s)|(?<=[<>]=\s)|(?<=range\()|(?<=\[))(?<!=>\s)\d{1,2}(?![\w.])", code):
            value = int(m.group(0))
            other = value + self.rng.choice([-1, 1]) if value > 0 else 1
            options.append((m.start(), m.end(), str(other), "value"))
            weights.append(1)
        for m in re.finditer(r"(?<![<>=!])(===|!==|<=|>=)(?![=>])", code):
            options.append((m.start(), m.end(), _OPERATOR_DRAFTS[m.group(0)], "comparison"))
            weights.append(2)
        if not options:
            return None
        return self.rng.choices(options, weights)[0]

    def _write_with_rewrite(self, i: int) -> bool:
        """Type line ``i`` with a first-draft token, then select and fix it."""
        draft = self._draft_for(i)
        if draft is None:
            return False
        a, b, text, what = draft
        want = self.T[i]
        if a < len(self.ed.before):
            return False
        draft_line = want[:a] + text + want[b:]
        straight = self._dry_run(lambda: self._write_plain(i, False))
        if straight is None or straight.after:
            return False
        snap = self._snapshot()
        # retyping a whole line is only believable when the line is short
        whole_line = self.rng.random() < 0.15 and len(want.strip()) <= 24
        try:
            self._quiet += 1
            self._write_plain(i, False, draft_line)
            ed = self.ed
            if ed.after or ed.text() == straight.text():
                raise PlanError("draft left the line unfinished")
            self._quiet -= 1
            self._pause("notice")
            self._note(f"Fixing the {what} on line {ed.row + 1}")
            self._quiet += 1
            if whole_line:
                self._key("home")
                if len(ed.before) != len(leading_ws(ed.line)):
                    self._key("home")
                self._key("shift+end")
                self._pause("notice")
                self._key("backspace")
                self._write_plain(i, False)
            else:
                self._key("left", len(draft_line) - (a + len(text)))
                self._key("shift+left", len(text))
                self._pause("notice")
                self._key("backspace")
                for c in want[a:b]:
                    self._type(c)
                self._key("end")
        except PlanError:
            self._quiet = max(0, self._quiet - 1)
            self._restore(snap)
            return False
        self._quiet -= 1
        ed = self.ed
        if ed.text() != straight.text() or ed.pos != straight.pos:
            self._restore(snap)
            return False
        self.stats["rewrites"] += 1
        return True

    def _dry_run(self, fn):
        snap = self._snapshot()
        self._quiet += 1
        try:
            fn()
            return self.ed.clone()
        except PlanError:
            return None
        finally:
            self._quiet -= 1
            self._restore(snap)

    # ----------------------------------------------------- deferred insertion
    def _should_defer(self, i: int) -> bool:
        if not self.o.revisits or self.deferred or i >= len(self.T) - 1 or self.ed.after:
            return False
        if self.placed[-1] != i - 1 or not self.T[i - 1].strip() or not self.T[i + 1].strip():
            return False
        if self.pending and self.pending[0] in (i, i + 1):
            return False
        if not (self._is_comment_line(i) or self._import_name(i)):
            return False
        if self._is_comment_line(i) and (self._is_comment_line(i - 1) or self._is_comment_line(i + 1)):
            return False
        return self.rng.random() < self.o.revisits

    def _deferred_due(self, i: int) -> bool:
        line, due, name = self.deferred
        if self.ed.after:
            return False
        if name and re.search(rf"\b{re.escape(name)}\b", self.code[i]) and i > line:
            return True
        return i >= due

    def _travel(self, rows_up: int, target_row: int):
        if rows_up > 8:
            self._emit_goto(target_row + 1)
        else:
            self._key("up", rows_up)

    def _emit_goto(self, line_number: int):
        if self.o.dismiss_suggestions and self._suggest:
            self._emit(Key("escape"))
            self._suggest = False
        self.ed.goto_line(line_number)
        self._emit(GotoLine(line_number))

    def _return_to(self, row: int):
        ed = self.ed
        if row == len(ed.lines) - 1:
            self._key("ctrl+end")
        else:
            distance = row - ed.row
            if distance > 8:
                self._emit_goto(row + 1)
            else:
                self._key("down", distance)
            self._key("end")

    def _insert_deferred(self):
        line, _, _ = self.deferred
        ed = self.ed
        if ed.after:
            return
        anchor = line - 1
        if anchor not in self.placed:
            raise PlanError("lost track of a deferred line")
        home_row = ed.row
        row_a = self.placed.index(anchor)
        what = "import" if self._import_name(line) else "comment"
        self._pause("notice")
        self._note(f"Going back to line {row_a + 1} to add the {what}")
        self._travel(home_row - row_a, row_a)
        self._key("end")
        self._pause("plan")
        self._key("enter")
        self.placed.insert(row_a + 1, line)
        self.deferred = None
        self._settle_line(line)
        self._write_plain(line, True)
        self._clear_rest()
        self._pause("line")
        self._return_to(home_row + 1)
        if ed.row != home_row + 1 or ed.after or self.T[self.placed[-1]] != ed.line:
            raise PlanError("did not return to the right place after going back")

    def _review(self, block: Block):
        ed = self.ed
        if ed.after or block.start not in self.placed:
            return
        home_row = ed.row
        if self.placed[-1] != block.end:
            return
        top = self.placed.index(block.start)
        if home_row - top < 2:
            return
        snap = self._snapshot()
        before = ed.text()
        self._pause("line")
        self._note(f"Looking back over {block.label}")
        self._travel(home_row - top, top)
        self._pause("review")
        self._return_to(home_row)
        if ed.row != home_row or ed.after or ed.text() != before or ed.col != len(ed.line):
            self._restore(snap)
            return
        self.stats["reviews"] += 1


def plan_file(text: str, lang_id: str, settings: EditorSettings | None = None,
              options: PlanOptions | None = None) -> Plan:
    return Planner(text, lang_id, settings, options).build()


def replay(plan: Plan, settings: EditorSettings | None = None, existing: str = "") -> str:
    """Run a plan through a fresh editor model and return the buffer."""
    ed = VirtualEditor(plan.language, settings or EditorSettings(), existing)
    for a in plan.actions:
        if isinstance(a, Type):
            ed.type_char(a.ch)
        elif isinstance(a, Key):
            ed.key(a.name)
        elif isinstance(a, Paste):
            ed.paste(a.text)
        elif isinstance(a, GotoLine):
            ed.goto_line(a.line)
    return ed.text()
