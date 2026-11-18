# GhostWriter 3.1 — Quick start (Windows)

Setup and a staged test, from cloning the repo to a multi-file project run.
Commands are for **PowerShell**. Requires Windows 10/11, Python 3.10+ and
VS Code.

## 1. Get the code

```powershell
cd C:\Users\<you>\Desktop
git clone <your-repo-url> Typing-bot
cd Typing-bot
```

## 2. Install

```powershell
py --version                       # must be 3.10 or newer
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

If the activate line errors with *"running scripts is disabled,"* run this
once in the same window, then retry it:

```powershell
Set-ExecutionPolicy -Scope Process -Bypass
```

If `py` isn't recognised, use `python`, or reinstall Python from python.org
with **"Add Python to PATH"** ticked.

## 3. Run it

```powershell
py main.py
```

The GhostWriter window opens. Each later session: `cd` to the folder, run
`.\.venv\Scripts\Activate.ps1`, then `py main.py`.

Optional check that the engine is healthy on your machine:

```powershell
py -m unittest discover -s tests   # should print OK
```

## 4. Smoke test — one snippet

1. In VS Code, open a new file (**Ctrl+N**) and set its language to **Python**
   (click the language in the bottom-right status bar).
2. In GhostWriter, paste a few lines of Python into the code box.
3. Keep the mode on **"Into the editor I focus during the countdown."** Untick
   **"Hide this window in the system tray"** for now so you can watch the
   status line.
4. Click **Start typing**, confirm, and during the 5-second countdown click
   into the empty VS Code file.
5. It types the lines and finishes with the log reporting that the contents
   match.

## 5. A full sample file

1. **Add files…** → `samples\inventory.py`, and select it in the list.
2. Start typing into a fresh empty Python file.
3. The log should say **"editor contents match the source exactly."**

## 6. Tray and hotkeys

1. Re-tick **"Hide this window in the system tray."**
2. Run the sample again — the window hides to a tray icon and a countdown
   notification appears.
3. While typing: **F9** pauses/resumes, **F10** stops. Right-click the tray
   icon for Pause / Stop / Show window. The window returns when typing ends.

## 7. Project folder test (multiple files)

1. Make an empty, throwaway folder, e.g. `C:\Users\<you>\Desktop\gw-test`.
2. **Add files…** → two or three from `samples\` (e.g. `inventory.py` and
   `todo.js`). Order them with ▲ ▼ if you like.
3. Choose **"Into a project folder open in VS Code,"** click **Browse…**, and
   pick `gw-test`.
4. Click **Open folder in VS Code** (or open it yourself: File → Open Folder →
   `gw-test`). That window must have the folder open.
5. Leave **"Write recommended VS Code settings"** ticked.
6. **Start typing** and focus that VS Code window during the countdown.
7. Each file is created, opened with Ctrl+P, typed, verified and saved.
   Afterward, check that both files exist and match, that a
   `gw-test\.vscode\settings.json` was created, and that the **Logs…** viewer
   shows the run.

## If something goes wrong

- **Nothing types at all** → close GhostWriter, set
  `"input_backend": "pyautogui"` in `config.json`, reopen and retry.
- **It types into the GhostWriter window** → you'll get a "still had focus"
  message; click into VS Code sooner during the countdown.
- **Brackets or indentation look wrong** → check the note in the README about
  editor settings; the post-typing verification will usually repair the file.
- Always do a first run into a **scratch file or a disposable folder**.

See `README.md` for how it works, all the config options, and the current
limitations.
