# GhostWriter 3.1 — code-aware editor simulator

GhostWriter types source code into VS Code the way a person would, for demos,
screen recordings, tutorials and editor testing. Version 3 sent characters one
by one and hoped VS Code would not interfere. Version 3.1 **knows what it is
typing and how VS Code reacts to each key**, so it works with the editor
instead of against it.

| Feature | What 3.1 does |
| --- | --- |
| Smart indentation | Lets VS Code auto-indent after Enter, then corrects only the difference (Backspace to dedent, Tab/Space to indent). Uses VS Code's own rules for Python, JavaScript/TypeScript, HTML and CSS. |
| Bracket awareness | Lets VS Code auto-close `()`, `[]`, `{}`, quotes and `/** */`, types over the closers, presses Enter between a pair to open a block and later steps past the closing bracket with ↓ / End. |
| Cursor navigation | Moves with arrows, Home/End, Ctrl+End and Go to Line (Ctrl+G) between lines and code blocks. |
| Editing | Selects text with Shift+arrows or Home / Shift+End, deletes it and rewrites it. |
| Corrections | Typos fixed with Backspace, and "first draft" slips (a wrong name, value or comparison) that are selected and retyped. Every correction is reversible: the final file is always the source file. |
| Multi-file workflow | Creates the files of a project, opens each one with Quick Open (Ctrl+P), types it, checks it and saves it. |
| Code awareness | Recognises functions, classes, loops and conditions; uses them for pacing, status messages, and for jumping back to review a finished block or add a comment/import that was "forgotten". |

## Install

Requires Windows 10/11, Python 3.10+ and VS Code.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py main.py
```

## Use

### One file, into the focused editor

1. Paste code, or **Add files…** and select one in the list.
2. Pick a pace and the behaviours you want.
3. Click **Start typing** and, during the countdown, click into an **empty**
   VS Code editor tab whose language matches the code (e.g. an untitled file
   with the language mode set, or an empty `.py` file).

GhostWriter reads your VS Code user settings (`settings.json`) to learn the
tab size, auto-indent mode and auto-closing behaviour it should expect.

### A whole project

1. **Add files…** or **Add folder…** to queue several files (order them with ▲ ▼).
2. Choose **Into a project folder open in VS Code** and pick a (preferably new,
   disposable) folder. **Open folder in VS Code** opens it for you.
3. Leave **Write recommended VS Code settings** ticked (see below).
4. Click **Start typing** and focus that VS Code window during the countdown.

GhostWriter creates each file empty in the folder, then for every file:
Ctrl+P → types the path → Enter → types the code → checks it → Ctrl+S.
Files that already have content are skipped unless **Replace existing
content** is ticked.

**Preview plan** shows what would happen (keystrokes, corrections, revisits)
without sending anything.

### Emergency stop

Move the mouse to the top-left corner of the primary display, or press
**Stop**. **Pause** / **Resume** work at any time.

## How it works

```
source file ──► Planner ──► actions (type / key / go-to-line / pause) ──► Driver ──► VS Code
                   ▲                                                         │
                   └── VirtualEditor: a model of VS Code's editor ◄── verify ─┘
```

* **`ghostwriter/editor.py` – the editor model.** A Python port of the rules
  VS Code's editor applies to every keystroke: auto-closing pairs, typing over
  closers, on-Enter rules, bracket indent, indentation rules, electric closing
  brackets, tab stops on Backspace, trimming of auto-inserted whitespace. The
  per-language data (`ghostwriter/data/language_configs.json`) is VS Code's own
  language configuration.
* **`ghostwriter/planner.py` – the planner.** Runs every keystroke through the
  model first. After Enter it compares the model's line with the target line
  and presses only what is needed. Corrections, rewrites and revisits are
  transactions: if the buffer afterwards is not exactly what straight typing
  would have produced, they are rolled back. A plan is only returned if the
  model's final buffer equals the source file.
* **`ghostwriter/outline.py` – code awareness.** Finds functions, classes,
  loops and conditions.
* **`ghostwriter/driver.py` – the driver.** Sends the plan with Windows
  `SendInput`. Characters go as Unicode packets, so the keyboard layout does
  not matter (no dead-key problems with quotes on international layouts).
* **Verification.** After typing, GhostWriter selects all, copies, and
  compares the editor with the source. If they differ it says where, and (by
  default) corrects the buffer with a single paste. This uses the clipboard.

### Recommended VS Code settings

Features that react to typing *asynchronously* — IntelliSense accepting a
suggestion on `.` or Enter, inline AI completions, format-on-type, HTML tag
auto-closing — cannot be predicted keystroke by keystroke. In project mode
GhostWriter writes a `.vscode/settings.json` into the project folder that
turns those off for that folder only (an existing file is merged and backed up
as `settings.json.ghostwriter-backup`). Bracket/quote auto-closing and
auto-indent stay **on**; they are what GhostWriter works with. The full list
is `RECOMMENDED` in `ghostwriter/vscode.py`.

In single-file mode nothing is written. GhostWriter presses Escape before
Enter and navigation keys so an open suggestion list cannot swallow them, and
warns in the log if your settings contain something risky. If VS Code still
changes the text, the verification step catches and corrects it.

## Configuration (`config.json`)

| Key | Meaning |
| --- | --- |
| `pace` | `Slow`, `Natural`, `Fast`, `Very fast` |
| `countdown_seconds` | Time to focus VS Code |
| `line_pause_*`, `thinking_pause_*` | Pause ranges (seconds, at Natural pace) between lines and before functions/classes |
| `typo_probability` | Chance per word of a typo |
| `rewrite_probability` | Chance per eligible line of a draft token that is selected and rewritten |
| `revisit_probability` | Chance that a stand-alone comment or import is left out and added later |
| `review_probability` | Chance of jumping back to look at a function/class after finishing it |
| `allow_demo_corrections`, `allow_navigation` | Initial state of the check boxes |
| `dismiss_suggestions` | Press Escape before Enter / navigation |
| `verify_after_typing`, `repair_by_paste` | Check the editor afterwards; fix differences by paste |
| `save_files` | Ctrl+S after each file in project mode |
| `apply_recommended_settings` | Initial state of the settings check box |
| `input_backend` | `auto` (SendInput on Windows), `sendinput`, `pyautogui` |
| `seed` | A number makes the performance repeatable; `null` is different every time |

## What has and has not been tested

* **Unit and end-to-end tests** (`py -m unittest discover -s tests`): the
  model, the planner on Python/JS/HTML/CSS files under nine settings profiles,
  and complete multi-file sessions against a simulated VS Code.
* **Against the real editor core** (`tools/monaco_oracle`): plans are replayed
  with real key events in Monaco — the editor component VS Code is built on —
  configured with VS Code's language configurations, and the buffer and cursor
  are compared with the model after every keystroke.
* **Not tested: a real VS Code on Windows.** This version was developed on
  Linux. The `SendInput` backend, Quick Open file switching and clipboard
  verification have not been run on Windows yet. Try the first run in a
  disposable folder. If input does not arrive, set `"input_backend":
  "pyautogui"`.

Known limits:

* VS Code decides whether to auto-close a quote from its syntax highlighter;
  GhostWriter uses its own small lexers. Unusual constructs can differ; the
  verification step covers that.
* Languages other than Python, JavaScript/TypeScript, HTML and CSS are typed
  with generic bracket rules only.
* Whitespace-only lines in the source are typed as empty lines (VS Code trims
  them while typing).
* Extensions that rewrite text as you type (Vim, auto-rename-tag, AI
  completions) are not modelled.
* The destination editor must start empty (or use **Replace existing content**).

## Responsible use

GhostWriter is for demonstrations, recordings and editor testing. It sends
ordinary user-mode keyboard input, does not conceal that the input is
automated, and does not try to defeat activity monitoring or proctoring.

## Development

```
py -m unittest discover -s tests          # tests

cd tools/monaco_oracle && npm install     # once; also: npx playwright install chromium
python tools/monaco_oracle/export_cases.py > cases.json
node tools/monaco_oracle/run.mjs cases.json
```

`samples/` contains small demo files in each supported language.
