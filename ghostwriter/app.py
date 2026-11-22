"""GhostWriter 3.1 desktop window (Tkinter)."""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, vscode
from .driver import EmergencyStop, NotFocused, PACES, Runner, Stopped, Timing, create_backend
from .languages import EXTENSIONS, LANGUAGE_LABELS, detect_language
from .planner import PlanOptions
from .logs import RunLog
from .session import Job, Session, SessionOptions, jobs_from_paths
from .tray import Hotkeys, Tray

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
DEFAULTS = {
    "pace": "Natural", "countdown_seconds": 5,
    "line_pause_min_s": 0.18, "line_pause_max_s": 0.65,
    "thinking_pause_min_s": 1.0, "thinking_pause_max_s": 3.5,
    "typo_probability": 0.035, "rewrite_probability": 0.10,
    "revisit_probability": 0.45, "review_probability": 0.25,
    "allow_demo_corrections": True, "allow_navigation": True,
    "dismiss_suggestions": True, "verify_after_typing": True, "repair_by_paste": True,
    "save_files": True, "apply_recommended_settings": True,
    "input_backend": "auto", "seed": None,
    "hide_window_while_typing": True, "mouse_movement": True,
    "close_when_finished": False,
    "hotkey_pause": "f9", "hotkey_stop": "f10",
}
PASTED = "Pasted code"
CODE_TYPES = " ".join("*" + ext for ext in sorted(EXTENSIONS)) + " *.json *.md *.txt"
SKIP_DIRS = {".git", ".vscode", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def load_config() -> dict:
    config = dict(DEFAULTS)
    try:
        config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return config


class GhostWriterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        root.title(f"GhostWriter {__version__}")
        root.geometry("1060x900")
        root.minsize(960, 820)

        self.jobs: list[Job] = [Job(PASTED, "", "python")]
        self.current = 0
        self.runner: Runner | None = None
        self.worker: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.runlog = RunLog()
        self._on_plan_done = None
        self._pending_backend = None
        self._closing = False

        c = self.config
        self.pace = tk.StringVar(value=c["pace"] if c["pace"] in PACES else "Natural")
        self.language = tk.StringVar(value="Auto-detect")
        self.mode = tk.StringVar(value="focused")
        self.project_dir = tk.StringVar(value="")
        self.status = tk.StringVar(value="Ready")
        self.opt_typos = tk.BooleanVar(value=bool(c["allow_demo_corrections"]))
        self.opt_rewrites = tk.BooleanVar(value=bool(c["allow_demo_corrections"]))
        self.opt_navigation = tk.BooleanVar(value=bool(c["allow_navigation"]))
        self.opt_verify = tk.BooleanVar(value=bool(c["verify_after_typing"]))
        self.opt_replace = tk.BooleanVar(value=False)
        self.opt_settings = tk.BooleanVar(value=bool(c["apply_recommended_settings"]))
        self.opt_hide = tk.BooleanVar(value=bool(c["hide_window_while_typing"]))
        self.opt_mouse = tk.BooleanVar(value=bool(c["mouse_movement"]))
        self.opt_close = tk.BooleanVar(value=bool(c["close_when_finished"]))
        self.tray = Tray(lambda command: self.events.put(("tray", command)))
        self.hotkeys = Hotkeys()
        self.hidden = False

        self._setup_style()
        self._build()
        self._refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(80, self._drain)

    # ------------------------------------------------------------------- theme
    def _setup_style(self):
        p = self.palette = {
            "bg": "#eef1f6", "surface": "#ffffff", "border": "#d7dde6",
            "ink": "#1e2733", "muted": "#687385", "subtle": "#97a1b0",
            "accent": "#2f6feb", "accent_active": "#2458c6", "accent_fg": "#ffffff",
            "accent_soft": "#a9c1f4", "danger": "#c0392b",
            "editor_bg": "#1b1f27", "editor_fg": "#e7eaf0", "caret": "#7aa2f7",
            "console_bg": "#f5f7fa",
        }
        self.root.configure(background=p["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Segoe UI", 10)
        style.configure(".", background=p["bg"], foreground=p["ink"], font=base,
                        bordercolor=p["border"], focuscolor=p["bg"])
        style.configure("TFrame", background=p["bg"])
        style.configure("Card.TFrame", background=p["surface"], bordercolor=p["border"], relief="solid",
                        borderwidth=1)
        style.configure("CardBody.TFrame", background=p["surface"], relief="flat", borderwidth=0)
        style.configure("TLabel", background=p["bg"], foreground=p["ink"])
        style.configure("Card.TLabel", background=p["surface"], foreground=p["ink"])
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        style.configure("CardMuted.TLabel", background=p["surface"], foreground=p["muted"])
        style.configure("Title.TLabel", background=p["bg"], foreground=p["ink"],
                        font=("Segoe UI Semibold", 18))
        style.configure("Subtitle.TLabel", background=p["bg"], foreground=p["muted"],
                        font=("Segoe UI", 9))
        style.configure("Section.TLabel", background=p["surface"], foreground=p["subtle"],
                        font=("Segoe UI", 8, "bold"))
        style.configure("Status.TLabel", background=p["bg"], foreground=p["muted"],
                        font=("Segoe UI Semibold", 10))
        # buttons
        style.configure("TButton", background="#e4e9f0", foreground=p["ink"], relief="flat",
                        padding=(13, 7), borderwidth=0)
        style.map("TButton", background=[("pressed", "#d2dae5"), ("active", "#d9e0ea"),
                                         ("disabled", "#eef1f6")],
                  foreground=[("disabled", p["subtle"])])
        style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"], padding=(18, 8))
        style.map("Accent.TButton", background=[("pressed", p["accent_active"]), ("active", p["accent_active"]),
                                                ("disabled", p["accent_soft"])],
                  foreground=[("disabled", "#eef3fd")])
        style.configure("Ghost.TButton", background=p["surface"], foreground=p["ink"], padding=(11, 6))
        style.map("Ghost.TButton", background=[("active", "#eef2f7"), ("pressed", "#e6ecf4")])
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(name, background=p["surface"], foreground=p["ink"], focuscolor=p["surface"])
            style.map(name, background=[("active", p["surface"])],
                      indicatorcolor=[("selected", p["accent"])])
        style.configure("TEntry", fieldbackground=p["surface"], bordercolor=p["border"], padding=5)
        style.configure("TCombobox", fieldbackground=p["surface"], bordercolor=p["border"],
                        arrowcolor=p["ink"], padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", p["surface"])],
                  foreground=[("readonly", p["ink"])])
        self.root.option_add("*TCombobox*Listbox.background", p["surface"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["accent_fg"])
        style.configure("Horizontal.TProgressbar", troughcolor="#dfe5ee", background=p["accent"],
                        bordercolor="#dfe5ee", lightcolor=p["accent"], darkcolor=p["accent"], thickness=6)

    def _card(self, parent, title=None, pad=14):
        card = ttk.Frame(parent, style="Card.TFrame")
        inner = ttk.Frame(card, style="CardBody.TFrame", padding=pad)
        inner.pack(fill="both", expand=True)
        if title:
            ttk.Label(inner, text=title.upper(), style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        content = ttk.Frame(inner, style="CardBody.TFrame")
        content.pack(fill="both", expand=True)
        return card, content

    # ------------------------------------------------------------------ layout
    def _build(self):
        p = self.palette
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        titles = ttk.Frame(header)
        titles.pack(side="left")
        ttk.Label(titles, text="GhostWriter", style="Title.TLabel").pack(side="left")
        ttk.Label(titles, text=f"  {__version__}", style="Subtitle.TLabel").pack(side="left", pady=(8, 0))
        ttk.Label(header, text="Code-aware editor simulator", style="Subtitle.TLabel").pack(side="right", pady=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        # -- left: file queue
        left_card, left = self._card(body, "Files", pad=12)
        left_card.pack(side="left", fill="y", padx=(0, 14))
        left_card.configure(width=240)
        left_card.pack_propagate(False)
        self.listbox = tk.Listbox(left, height=8, exportselection=False, activestyle="none",
                                  background=p["surface"], foreground=p["ink"], highlightthickness=1,
                                  highlightbackground=p["border"], highlightcolor=p["accent"],
                                  selectbackground=p["accent"], selectforeground=p["accent_fg"],
                                  borderwidth=0, font=("Segoe UI", 10))
        self.listbox.pack(fill="both", expand=True, pady=(0, 10))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        ttk.Button(left, text="Add files…", style="Ghost.TButton", command=self.add_files).pack(fill="x")
        ttk.Button(left, text="Add folder…", style="Ghost.TButton", command=self.add_folder).pack(fill="x", pady=6)
        row = ttk.Frame(left, style="CardBody.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="↑", width=3, style="Ghost.TButton", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="↓", width=3, style="Ghost.TButton", command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(row, text="Remove", style="Ghost.TButton", command=self.remove).pack(side="left", fill="x", expand=True)

        # -- right column
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        code_card, code = self._card(right, pad=12)
        code_card.pack(fill="both", expand=True)
        top = ttk.Frame(code, style="CardBody.TFrame")
        top.pack(fill="x", pady=(0, 8))
        self.code_label = ttk.Label(top, text="Code", style="Section.TLabel")
        self.code_label.pack(side="left", pady=(2, 0))
        self.language_box = ttk.Combobox(
            top, textvariable=self.language, state="readonly", width=14,
            values=["Auto-detect"] + [LANGUAGE_LABELS[k] for k in ("python", "javascript", "typescript", "html",
                                                                   "css", "plain")])
        self.language_box.pack(side="right")
        ttk.Label(top, text="Language", style="CardMuted.TLabel").pack(side="right", padx=(8, 6))
        self.language_box.bind("<<ComboboxSelected>>", lambda e: self._store_current())
        self.editor = tk.Text(code, wrap="none", undo=True, font=("Consolas", 10), height=9,
                              background=p["editor_bg"], foreground=p["editor_fg"], insertbackground=p["caret"],
                              selectbackground="#2d3444", borderwidth=0, padx=12, pady=10)
        self.editor.pack(fill="both", expand=True)

        dest_card, dest = self._card(right, "Where to type")
        dest_card.pack(fill="x", pady=(14, 0))
        ttk.Radiobutton(dest, text="Into the editor I focus during the countdown (selected file only)",
                        variable=self.mode, value="focused", command=self._mode_changed).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=1)
        ttk.Radiobutton(dest, text="Into a project folder open in VS Code (all files):",
                        variable=self.mode, value="project", command=self._mode_changed).grid(
            row=1, column=0, sticky="w", pady=1)
        self.project_entry = ttk.Entry(dest, textvariable=self.project_dir, width=34)
        self.project_entry.grid(row=1, column=1, sticky="ew", padx=6)
        self.browse_btn = ttk.Button(dest, text="Browse…", style="Ghost.TButton", command=self.choose_project)
        self.browse_btn.grid(row=1, column=2)
        self.settings_chk = ttk.Checkbutton(
            dest, text="Write recommended VS Code settings (.vscode/settings.json)",
            variable=self.opt_settings)
        self.settings_chk.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.open_btn = ttk.Button(dest, text="Open folder in VS Code", style="Ghost.TButton",
                                   command=self.open_project)
        self.open_btn.grid(row=2, column=2, pady=(4, 0))
        dest.columnconfigure(1, weight=1)

        tune_card, tune = self._card(right, "Typing")
        tune_card.pack(fill="x", pady=(14, 0))
        pace_row = ttk.Frame(tune, style="CardBody.TFrame")
        pace_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(pace_row, text="Pace", style="CardMuted.TLabel").pack(side="left", padx=(0, 8))
        ttk.Combobox(pace_row, textvariable=self.pace, state="readonly", width=12,
                     values=list(PACES)).pack(side="left")
        checks = [
            (self.opt_typos, "Typos fixed with Backspace"),
            (self.opt_rewrites, "Select and rewrite draft code"),
            (self.opt_navigation, "Navigate back and review blocks"),
            (self.opt_verify, "Verify and repair afterwards"),
            (self.opt_replace, "Replace existing content"),
            (self.opt_mouse, "Move the mouse during pauses"),
            (self.opt_hide, "Hide in the tray while typing"),
            (self.opt_close, "Close GhostWriter when finished"),
        ]
        for i, (var, text) in enumerate(checks):
            r, col = divmod(i, 2)
            ttk.Checkbutton(tune, text=text, variable=var).grid(row=r + 1, column=col, sticky="w", padx=(0, 18), pady=1)
        tune.columnconfigure(0, weight=1)
        tune.columnconfigure(1, weight=1)

        # -- action bar
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(16, 0))
        self.start_btn = ttk.Button(controls, text="Start typing", style="Accent.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(controls, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.preview_btn = ttk.Button(controls, text="Preview plan", command=self.preview)
        self.preview_btn.pack(side="left", padx=6)
        ttk.Button(controls, text="Logs…", command=self.show_logs).pack(side="left")
        self.status_label = ttk.Label(controls, textvariable=self.status, style="Status.TLabel")
        self.status_label.pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="determinate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(12, 10))

        self.log = tk.Text(outer, height=4, wrap="word", state="disabled", font=("Consolas", 9),
                           background=p["console_bg"], foreground=p["ink"], relief="flat",
                           highlightthickness=1, highlightbackground=p["border"], padx=10, pady=8)
        self.log.pack(fill="x")
        ttk.Label(
            outer, wraplength=940, style="Muted.TLabel", justify="left",
            text="GhostWriter sends ordinary keyboard input to the focused window — it does not conceal that "
                 "the input is automated. Use a scratch file or a disposable project folder. While typing: "
                 f"{self.config['hotkey_pause'].upper()} pauses or resumes, "
                 f"{self.config['hotkey_stop'].upper()} stops, and moving the mouse to the top-left corner "
                 "of the screen is the emergency stop."
        ).pack(anchor="w", pady=(10, 0))
        self._mode_changed()

    # --------------------------------------------------------------- file list
    def _job_language(self) -> str:
        label = self.language.get()
        for key, value in LANGUAGE_LABELS.items():
            if value == label:
                return key
        return ""

    def _store_current(self):
        if not self.jobs:
            return
        job = self.jobs[self.current]
        job.text = self.editor.get("1.0", "end-1c")
        chosen = self._job_language()
        job.language = chosen or detect_language(None if job.name == PASTED else job.name, job.text)

    def _show(self, index: int):
        self.current = index
        job = self.jobs[index]
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", job.text)
        self.code_label.configure(text=job.name)
        self.language.set("Auto-detect" if job.name == PASTED else LANGUAGE_LABELS.get(job.language, "Auto-detect"))

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for job in self.jobs:
            self.listbox.insert("end", job.name)
        self.current = min(self.current, len(self.jobs) - 1)
        self.listbox.selection_set(self.current)
        self._show(self.current)

    def _on_select(self, _event=None):
        selection = self.listbox.curselection()
        if not selection or selection[0] == self.current:
            return
        self._store_current()
        self._show(selection[0])

    def _add_jobs(self, jobs: list[Job]):
        self._store_current()
        if len(self.jobs) == 1 and self.jobs[0].name == PASTED and not self.jobs[0].text.strip():
            self.jobs = []
        names = {j.name for j in self.jobs}
        for job in jobs:
            if job.name not in names:
                self.jobs.append(job)
                names.add(job.name)
        if not self.jobs:
            self.jobs = [Job(PASTED, "", "python")]
        self.current = len(self.jobs) - 1
        self._refresh_list()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add source files", filetypes=[("Code and text files", CODE_TYPES), ("All files", "*.*")])
        if paths:
            self._add_jobs(jobs_from_paths([Path(p) for p in paths]))

    def add_folder(self):
        folder = filedialog.askdirectory(title="Add every code file in a folder")
        if not folder:
            return
        root = Path(folder)
        paths = sorted(p for p in root.rglob("*")
                       if p.is_file() and p.suffix.lower() in EXTENSIONS
                       and not any(part in SKIP_DIRS for part in p.relative_to(root).parts))
        if len(paths) > 40 and not messagebox.askyesno("GhostWriter", f"Add {len(paths)} files?"):
            return
        self._add_jobs(jobs_from_paths(paths, root))

    def remove(self):
        self._store_current()
        del self.jobs[self.current]
        if not self.jobs:
            self.jobs = [Job(PASTED, "", "python")]
        self._refresh_list()

    def move(self, delta: int):
        self._store_current()
        target = self.current + delta
        if 0 <= target < len(self.jobs):
            self.jobs[self.current], self.jobs[target] = self.jobs[target], self.jobs[self.current]
            self.current = target
            self._refresh_list()

    # ------------------------------------------------------------ destination
    def _mode_changed(self):
        state = "normal" if self.mode.get() == "project" else "disabled"
        for widget in (self.project_entry, self.browse_btn, self.open_btn, self.settings_chk):
            widget.configure(state=state)

    def choose_project(self):
        folder = filedialog.askdirectory(title="Project folder that is (or will be) open in VS Code")
        if folder:
            self.project_dir.set(folder)

    def open_project(self):
        folder = self.project_dir.get().strip()
        if not folder:
            return messagebox.showinfo("GhostWriter", "Choose the project folder first.")
        Path(folder).mkdir(parents=True, exist_ok=True)
        if not vscode.open_in_vscode(Path(folder), reuse_window=False):
            messagebox.showwarning("GhostWriter", "Could not find the 'code' command. Open the folder in "
                                                  "VS Code yourself (File > Open Folder).")

    # ---------------------------------------------------------------- session
    def _write_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.runlog.write(text)

    def show_logs(self):
        window = tk.Toplevel(self.root)
        window.title("GhostWriter logs")
        window.geometry("780x560")
        top = ttk.Frame(window, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Saved run logs:").pack(side="left")
        folder = self.runlog.directory
        ttk.Button(top, text="Open logs folder",
                   command=lambda: self._open_path(folder)).pack(side="right")
        picker = ttk.Combobox(top, state="readonly", width=34)
        picker.pack(side="right", padx=8)

        viewer = tk.Text(window, wrap="none", font=("Consolas", 9), state="disabled",
                         background="#f4f5f7")
        viewer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        import time as _time
        entries = self.runlog.recent(limit=30)
        labels = [f"{_time.strftime('%Y-%m-%d %H:%M', _time.localtime(m))}  ({p.name})" for p, m in entries]

        def display(_event=None):
            index = picker.current()
            viewer.configure(state="normal")
            viewer.delete("1.0", "end")
            if 0 <= index < len(entries):
                try:
                    viewer.insert("1.0", entries[index][0].read_text(encoding="utf-8"))
                except OSError as exc:
                    viewer.insert("1.0", f"Could not read this log:\n{exc}")
            viewer.see("end")
            viewer.configure(state="disabled")

        picker.configure(values=labels)
        picker.bind("<<ComboboxSelected>>", display)
        if labels:
            picker.current(0)
            display()
        else:
            viewer.configure(state="normal")
            viewer.insert("1.0", "No saved logs yet. Run a session and they will appear here, and in:\n"
                                 f"{folder}")
            viewer.configure(state="disabled")

    def _open_path(self, path: Path):
        import subprocess
        import sys as _sys
        try:
            path.mkdir(parents=True, exist_ok=True)
            if _sys.platform == "win32":
                import os
                os.startfile(str(path))                      # noqa: S606 (user-initiated)
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except (OSError, ValueError) as exc:
            messagebox.showinfo("GhostWriter", f"The logs are in:\n{path}\n\n({exc})")

    def _busy(self) -> bool:
        return self.runner is not None or (self.worker is not None and self.worker.is_alive())

    def _build_session_unplanned(self) -> Session | None:
        """Validate the inputs and build the Session, but do NOT plan yet.

        Planning can be heavy (hundreds of files), so it is left to run on a
        background thread; this does only the cheap, UI-thread-safe checks.
        """
        self._store_current()
        c = self.config
        project = self.mode.get() == "project"
        if project:
            folder = self.project_dir.get().strip()
            if not folder:
                messagebox.showinfo("GhostWriter", "Choose the project folder that is open in VS Code.")
                return None
            jobs = [j for j in self.jobs if j.text.strip()]
            if any(j.name == PASTED for j in jobs):
                messagebox.showinfo("GhostWriter", "Project mode types files by name. Remove the pasted "
                                                   "entry or add the code as a file.")
                return None
        else:
            jobs = [self.jobs[self.current]] if self.jobs[self.current].text.strip() else []
        if not jobs:
            messagebox.showinfo("GhostWriter", "Paste code or add a source file first.")
            return None
        for job in jobs:
            job.plan, job.status = None, "queued"
        navigation = self.opt_navigation.get()
        plan = PlanOptions(
            seed=c["seed"],
            mistakes=c["typo_probability"] if self.opt_typos.get() else 0.0,
            rewrites=c["rewrite_probability"] if self.opt_rewrites.get() else 0.0,
            revisits=c["revisit_probability"] if navigation else 0.0,
            reviews=c["review_probability"] if navigation else 0.0,
            dismiss_suggestions=bool(c["dismiss_suggestions"]),
        )
        options = SessionOptions(
            project_dir=Path(self.project_dir.get().strip()) if project else None,
            apply_settings=self.opt_settings.get(), replace_existing=self.opt_replace.get(),
            verify=self.opt_verify.get(), repair=bool(c["repair_by_paste"]), save=bool(c["save_files"]),
            countdown=int(c["countdown_seconds"]), plan=plan,
        )
        # The log goes through the event queue so it is safe from any thread.
        return Session(jobs, options, log=lambda text: self.events.put(("log", text)))

    def _plan_in_background(self, session: Session, done_event: str, on_done):
        """Run session.prepare() on a worker thread with progress."""
        def report(done, total):
            self.events.put(("plan_progress", done, total))
            if done % 5 == 0:
                time.sleep(0.003)       # planning is CPU-bound; yield so the window stays smooth

        def work():
            try:
                problems = session.prepare(progress=report)
                for message in problems:
                    self.events.put(("log", "! " + message))
                self.events.put((done_event, session, problems))
            except Exception as exc:        # planning must never take the window down
                self.events.put(("error", f"Could not plan the typing session:\n{exc}"))
                self.events.put((done_event, None, []))

        self.status.set("Planning…")
        self.progress.configure(mode="determinate", maximum=1, value=0)
        self.start_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self._on_plan_done = on_done
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def preview(self):
        if self._busy():
            return
        session = self._build_session_unplanned()
        if session is None:
            return
        self._plan_in_background(session, "preview_done", self._preview_ready)

    def _preview_ready(self, session, problems):
        self.start_btn.configure(state="normal")
        self.preview_btn.configure(state="normal")
        if session is None:
            self.status.set("Planning failed")
            return
        planned = 0
        for job in session.jobs:
            if job.plan:
                planned += 1
                stats = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in job.plan.stats.items() if v)
                self._write_log(f"    plan for {job.name}: {stats or 'straight typing'}")
        self.status.set(f"Plan ready ({planned} file{'s' if planned != 1 else ''})")

    def start(self):
        if self._busy():
            return
        try:
            backend = create_backend(self.config["input_backend"])
        except ImportError:
            return messagebox.showerror(
                "Missing packages",
                "Open PowerShell in the GhostWriter folder and run:\n\npy -m pip install -r requirements.txt")
        session = self._build_session_unplanned()
        if session is None:
            return
        self._pending_backend = backend
        self._plan_in_background(session, "start_done", self._start_ready)

    def _start_ready(self, session, problems):
        if session is None:
            self.start_btn.configure(state="normal")
            self.preview_btn.configure(state="normal")
            self.status.set("Planning failed")
            return
        todo = [j.name for j in session.jobs if j.plan]
        if not todo:
            self.start_btn.configure(state="normal")
            self.preview_btn.configure(state="normal")
            self.status.set("Nothing to type")
            messagebox.showwarning("GhostWriter", "Nothing to type:\n\n" + "\n".join(problems[:10]))
            return
        if session.o.project_dir is not None:
            question = (f"GhostWriter will create/type {len(todo)} file(s) in\n{session.o.project_dir}\n"
                        "in the VS Code window you focus during the countdown.\n\n"
                        "That window must have this folder open. Continue?")
        else:
            question = ("GhostWriter will type into whichever window has focus after the countdown.\n\n"
                        "Click into an empty VS Code editor tab during the countdown. Continue?")
        if problems:
            question += "\n\nNotes:\n• " + "\n• ".join(problems[:6])
        if not messagebox.askyesno("Ready to type?", question):
            self.start_btn.configure(state="normal")
            self.preview_btn.configure(state="normal")
            self.status.set("Ready")
            return

        c = self.config
        timing = Timing(pace=self.pace.get(), think_min=c["thinking_pause_min_s"],
                        think_max=c["thinking_pause_max_s"], line_min=c["line_pause_min_s"],
                        line_max=c["line_pause_max_s"])
        self.runner = Runner(self._pending_backend, timing,
                             on_status=lambda text: self.events.put(("status", text)),
                             on_progress=lambda done, total: self.events.put(("progress", done, total)),
                             mouse=self.opt_mouse.get())
        destination = str(session.o.project_dir) if session.o.project_dir else "focused editor"
        path = self.runlog.start(f"{len(todo)} file(s) -> {destination}")
        if path:
            self._write_log(f"Logging this run to {path}")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.hotkeys.start({c["hotkey_pause"]: lambda: self.events.put(("tray", "toggle")),
                            c["hotkey_stop"]: lambda: self.events.put(("tray", "stop"))})
        for name in self.hotkeys.failed:
            self._write_log(f"! Hotkey {name.upper()} is already taken by another program.")
        if self.opt_hide.get():
            self._hide_window(int(c["countdown_seconds"]))
        self.worker = threading.Thread(target=self._run, args=(session,), daemon=True)
        self.worker.start()

    # ------------------------------------------------------------------- tray
    def _hide_window(self, countdown: int):
        self.hidden = True
        if self.tray.start():
            self.root.withdraw()
            self.tray.notify(f"Typing starts in {countdown} s - click into VS Code. "
                             f"{self.config['hotkey_pause'].upper()} pauses, "
                             f"{self.config['hotkey_stop'].upper()} stops.")
        else:
            # no tray support installed: get out of the way on the taskbar instead
            self._write_log("Tray icon unavailable (pip install pystray pillow); minimising instead.")
            self.root.iconify()

    def _show_window(self):
        if self.hidden:
            self.hidden = False
            self.root.deiconify()
            self.root.lift()
        self.tray.stop()

    def _run(self, session: Session):
        try:
            session.prepare_project()        # create the files / write workspace settings
            session.run(self.runner)
            final = "Finished"
        except (OSError, ValueError) as exc:
            self.events.put(("error", f"Could not prepare the project folder:\n{exc}"))
            final = "Error"
        except Stopped:
            final = "Stopped"
        except EmergencyStop:
            final = "Emergency stop"
        except NotFocused:
            self.events.put(("error", "GhostWriter's own window still had focus when the countdown ended.\n\n"
                                      "Click into the VS Code editor during the countdown."))
            final = "Not started"
        except Exception as exc:
            self.events.put(("error", str(exc)))
            final = "Error"
        self.events.put(("done", final))

    def _drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.status.set(event[1])
                    self.tray.update(status=event[1])
                elif kind == "tray":
                    if event[1] == "toggle":
                        self.toggle_pause()
                    elif event[1] == "stop":
                        self.stop()
                    elif event[1] == "show":
                        self._show_window()
                elif kind == "progress":
                    self.progress.configure(maximum=max(1, event[2]), value=event[1])
                elif kind == "plan_progress":
                    done, total = event[1], event[2]
                    self.progress.configure(maximum=max(1, total), value=done)
                    if total > 12:
                        self.status.set(f"Planning {done}/{total}…")
                        self.tray.update(status=f"Planning {done}/{total}…")
                elif kind in ("preview_done", "start_done"):
                    callback, self._on_plan_done = self._on_plan_done, None
                    if callback:
                        callback(event[1], event[2])
                elif kind == "log":
                    self._write_log(event[1])
                elif kind == "error":
                    self._show_window()
                    messagebox.showerror("GhostWriter", event[1])
                elif kind == "done":
                    final = event[1]
                    self.status.set(final)
                    self._write_log(f"Run ended: {final}")
                    self.runlog.close()
                    self.hotkeys.stop()
                    self.runner = None
                    # Quiet auto-close only on a clean finish; failures and stops
                    # always bring the window back so they are never hidden.
                    if final == "Finished" and self.opt_close.get():
                        self._closing = True
                        self.tray.stop()
                        self.root.after(150, self.root.destroy)
                        return
                    self.tray.notify(final)
                    self._show_window()
                    self.start_btn.configure(state="normal")
                    self.preview_btn.configure(state="normal")
                    self.pause_btn.configure(state="disabled", text="Pause")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(80, self._drain)

    def toggle_pause(self):
        if not self.runner:
            return
        if self.runner.paused:
            self.runner.resume()
            self.pause_btn.configure(text="Pause")
            self.status.set("Typing")
        else:
            self.runner.pause()
            self.pause_btn.configure(text="Resume")
            self.status.set("Paused")
        self.tray.update(status=self.status.get(), paused=self.runner.paused)

    def stop(self):
        if self.runner:
            self.runner.stop()
            self.status.set("Stopping…")

    def close(self):
        self._closing = True
        if self.runner:
            self.runner.stop()
        self.runlog.close()
        self.hotkeys.stop()
        self.tray.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    GhostWriterApp(root)
    root.mainloop()
