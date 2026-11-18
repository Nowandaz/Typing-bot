"""Knowledge about the VS Code installation: settings and file opening."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .editor import EditorSettings

# Settings that make the editor behave deterministically while it is being
# driven.  Bracket/quote auto-closing and auto-indent stay ON - they are the
# behaviours GhostWriter works with - while everything that reacts to typing
# asynchronously (IntelliSense, inline suggestions, formatters, HTML tag
# closing) is switched off.
RECOMMENDED = {
    "editor.autoIndent": "advanced",
    "editor.autoClosingBrackets": "languageDefined",
    "editor.autoClosingQuotes": "languageDefined",
    "editor.autoClosingComments": "languageDefined",
    "editor.autoClosingOvertype": "auto",
    "editor.autoClosingDelete": "auto",
    "editor.trimAutoWhitespace": True,
    "editor.useTabStops": True,
    "editor.detectIndentation": False,
    "editor.quickSuggestions": {"other": "off", "comments": "off", "strings": "off"},
    "editor.suggestOnTriggerCharacters": False,
    "editor.acceptSuggestionOnEnter": "off",
    "editor.acceptSuggestionOnCommitCharacter": False,
    "editor.wordBasedSuggestions": "off",
    "editor.tabCompletion": "off",
    "editor.parameterHints.enabled": False,
    "editor.inlineSuggest.enabled": False,
    "editor.formatOnType": False,
    "editor.formatOnPaste": False,
    "editor.formatOnSave": False,
    "editor.linkedEditing": False,
    "editor.autoIndentOnPaste": False,
    "html.autoClosingTags": False,
    "html.autoCreateQuotes": False,
    "javascript.autoClosingTags": False,
    "typescript.autoClosingTags": False,
    "python.analysis.autoIndent": False,
    "emmet.triggerExpansionOnTab": False,
    "files.autoSave": "off",
}

_EDITOR_KEYS = {
    "editor.tabSize": "tab_size",
    "editor.insertSpaces": "insert_spaces",
    "editor.autoIndent": "auto_indent",
    "editor.autoClosingBrackets": "auto_closing_brackets",
    "editor.autoClosingQuotes": "auto_closing_quotes",
    "editor.autoClosingComments": "auto_closing_comments",
    "editor.autoClosingOvertype": "auto_closing_overtype",
    "editor.autoClosingDelete": "auto_closing_delete",
    "editor.trimAutoWhitespace": "trim_auto_whitespace",
    "editor.useTabStops": "use_tab_stops",
}
_ALLOWED = {
    "auto_indent": {"none", "keep", "brackets", "advanced", "full"},
    "auto_closing_brackets": {"always", "languageDefined", "beforeWhitespace", "never"},
    "auto_closing_quotes": {"always", "languageDefined", "beforeWhitespace", "never"},
    "auto_closing_comments": {"always", "languageDefined", "beforeWhitespace", "never"},
    "auto_closing_overtype": {"always", "auto", "never"},
    "auto_closing_delete": {"always", "auto", "never"},
}


def parse_jsonc(text: str) -> dict:
    """Parse VS Code's settings.json dialect (comments, trailing commas)."""
    out, i, n = [], 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True; out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    cleaned = "".join(out)
    # drop trailing commas (outside strings)
    result, in_string, i = [], False, 0
    while i < len(cleaned):
        ch = cleaned[i]
        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < len(cleaned):
                result.append(cleaned[i + 1]); i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True; result.append(ch)
        elif ch == ",":
            j = i + 1
            while j < len(cleaned) and cleaned[j] in " \t\r\n":
                j += 1
            if j < len(cleaned) and cleaned[j] in "}]":
                i += 1
                continue
            result.append(ch)
        else:
            result.append(ch)
        i += 1
    data = json.loads("".join(result) or "{}")
    return data if isinstance(data, dict) else {}


def user_settings_path() -> Path | None:
    candidates = []
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            candidates = [Path(base) / name / "User" / "settings.json" for name in ("Code", "Code - Insiders", "VSCodium")]
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        candidates = [base / name / "User" / "settings.json" for name in ("Code", "Code - Insiders", "VSCodium")]
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        candidates = [base / name / "User" / "settings.json" for name in ("Code", "Code - Insiders", "VSCodium")]
    return next((p for p in candidates if p.is_file()), None)


def load_settings(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return {}
    try:
        return parse_jsonc(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def effective(lang_id: str, *layers: dict) -> dict:
    """Merge settings layers (user, then workspace); language blocks win."""
    merged: dict = {}
    for layer in layers:
        merged.update({k: v for k, v in layer.items() if not k.startswith("[")})
    for layer in layers:
        for key, value in layer.items():
            if key.startswith("[") and isinstance(value, dict):
                if lang_id in key.strip("[]").replace("][", ",").split(","):
                    merged.update(value)
    return merged


def editor_settings(lang_id: str, *layers: dict) -> EditorSettings:
    merged = effective(lang_id, *layers)
    values = {}
    for key, field in _EDITOR_KEYS.items():
        if key not in merged:
            continue
        value = merged[key]
        if field == "tab_size":
            if isinstance(value, int) and 1 <= value <= 16:
                values[field] = value
        elif field in ("insert_spaces", "trim_auto_whitespace", "use_tab_stops"):
            if isinstance(value, bool):
                values[field] = value
        elif value in _ALLOWED[field]:
            values[field] = value
    return EditorSettings(**values)


def hazards(lang_id: str, *layers: dict) -> list[str]:
    """Settings that let VS Code change the text behind the typist's back."""
    s = effective(lang_id, *layers)
    found = []
    quick = s.get("editor.quickSuggestions", True)
    suggestions_on = quick is not False and not (
        isinstance(quick, dict) and all(v in ("off", False) for v in quick.values()))
    if suggestions_on and s.get("editor.acceptSuggestionOnCommitCharacter", True):
        found.append("IntelliSense can accept a suggestion when '.', '(' or ';' is typed "
                     "(editor.acceptSuggestionOnCommitCharacter).")
    if s.get("editor.formatOnType", False):
        found.append("Format On Type is enabled (editor.formatOnType).")
    if s.get("editor.linkedEditing", False):
        found.append("Linked editing is enabled (editor.linkedEditing).")
    if lang_id == "html":
        if s.get("html.autoClosingTags", True):
            found.append("HTML closing tags are inserted automatically (html.autoClosingTags).")
        if s.get("html.autoCreateQuotes", True):
            found.append("HTML attribute quotes are inserted automatically (html.autoCreateQuotes).")
    if lang_id == "python" and s.get("editor.formatOnType", False) and s.get("python.analysis.autoIndent", True):
        found.append("Pylance auto-indent is active (python.analysis.autoIndent).")
    if s.get("editor.inlineSuggest.enabled", True) and any(
            k.startswith(("github.copilot", "codeium", "tabnine")) for k in s):
        found.append("An AI inline-completion extension appears to be configured; its ghost text can be "
                     "accepted by Tab.")
    return found


def recommended_for(tab_size: int, insert_spaces: bool) -> dict:
    settings = dict(RECOMMENDED)
    settings["editor.tabSize"] = tab_size
    settings["editor.insertSpaces"] = insert_spaces
    return settings


def write_workspace_settings(folder: Path, settings: dict) -> Path:
    """Merge ``settings`` into <folder>/.vscode/settings.json.

    An existing file is kept as settings.json.ghostwriter-backup the first
    time it is changed.
    """
    path = Path(folder) / ".vscode" / "settings.json"
    current = {}
    if path.is_file():
        original = path.read_text(encoding="utf-8-sig")
        current = parse_jsonc(original)        # raises ValueError if unreadable: do not clobber
        if all(current.get(k) == v for k, v in settings.items()):
            return path
        backup = path.with_name("settings.json.ghostwriter-backup")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
    current.update(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=4) + "\n", encoding="utf-8")
    return path


def detect_indentation(text: str, default_size: int = 4) -> tuple[int, bool]:
    """(tab_size, insert_spaces) used by a source file."""
    widths = []
    tabs = spaces = 0
    for line in text.split("\n"):
        ws = line[: len(line) - len(line.lstrip(" \t"))]
        if not ws or not line.strip():
            continue
        if ws[0] == "\t":
            tabs += 1
        else:
            spaces += 1
            widths.append(len(ws))
    if tabs > spaces:
        return default_size, False
    if not widths:
        return default_size, True
    for size in (8, 4, 3, 2):
        if sum(1 for w in widths if w % size == 0) >= 0.9 * len(widths):
            return size, True
    return default_size, True


def find_code_cli() -> str | None:
    found = shutil.which("code") or shutil.which("code-insiders") or shutil.which("codium")
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
            if base:
                for sub in ("Programs/Microsoft VS Code/bin/code.cmd", "Microsoft VS Code/bin/code.cmd"):
                    path = Path(base) / sub
                    if path.is_file():
                        return str(path)
    return None


def open_in_vscode(*paths: Path, reuse_window: bool = True) -> bool:
    cli = find_code_cli()
    if not cli:
        return False
    args = [cli] + (["--reuse-window"] if reuse_window else []) + [str(p) for p in paths]
    try:
        subprocess.Popen(args, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
