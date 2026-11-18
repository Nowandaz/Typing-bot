"""GhostWriter 3.1 desktop window (Tkinter)."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, vscode
from .driver import EmergencyStop, PACES, Runner, Stopped, Timing, create_backend
from .languages import EXTENSIONS, LANGUAGE_LABELS, detect_language
from .planner import PlanOptions
from .session import Job, Session, SessionOptions, jobs_from_paths

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
        root.geometry("1040x760")
        root.minsize(900, 640)

        self.jobs: list[Job] = [Job(PASTED, "", "python")]
        self.current = 0
        self.runner: Runner | None = None
        self.worker: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()

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

        self._build()
        self._refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(80, self._drain)

    # ------------------------------------------------------------------ layout
    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text=f"GhostWriter {__version__}", font=("Segoe UI", 20, "bold")).pack(side="left")
        ttk.Label(header, text="CODE-AWARE EDITOR SIMULATOR", foreground="#527f9c").pack(side="right", pady=8)

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        # -- left: file queue
        left = ttk.Frame(body, padding=(0, 0, 10, 0), width=220)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="Files to type (in order)").pack(anchor="w")
        self.listbox = tk.Listbox(left, height=8, exportselection=False, activestyle="none")
        self.listbox.pack(fill="both", expand=True, pady=(2, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        ttk.Button(left, text="Add files…", command=self.add_files).pack(fill="x")
        ttk.Button(left, text="Add folder…", command=self.add_folder).pack(fill="x", pady=4)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="▲", width=3, command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="▼", width=3, command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(row, text="Remove", command=self.remove).pack(side="left", fill="x", expand=True)

        # -- right: code + options
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(right)
        top.pack(fill="x")
        self.code_label = ttk.Label(top, text="Code")
        self.code_label.pack(side="left")
        self.language_box = ttk.Combobox(
            top, textvariable=self.language, state="readonly", width=14,
            values=["Auto-detect"] + [LANGUAGE_LABELS[k] for k in ("python", "javascript", "typescript", "html",
                                                                   "css", "plain")])
        self.language_box.pack(side="right")
        ttk.Label(top, text="Language:").pack(side="right", padx=(8, 4))
        self.language_box.bind("<<ComboboxSelected>>", lambda e: self._store_current())

        self.editor = tk.Text(right, wrap="none", undo=True, font=("Consolas", 10), height=14,
                              background="#17191f", foreground="#e4e6eb", insertbackground="white",
                              padx=10, pady=10)
        self.editor.pack(fill="both", expand=True, pady=(2, 6))

        dest = ttk.LabelFrame(right, text="Where to type", padding=8)
        dest.pack(fill="x")
        ttk.Radiobutton(dest, text="Into the editor I focus during the countdown (selected file only)",
                        variable=self.mode, value="focused", command=self._mode_changed).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(dest, text="Into a project folder open in VS Code (all files, switching between them):",
                        variable=self.mode, value="project", command=self._mode_changed).grid(
            row=1, column=0, sticky="w")
        self.project_entry = ttk.Entry(dest, textvariable=self.project_dir, width=38)
        self.project_entry.grid(row=1, column=1, sticky="ew", padx=6)
        self.browse_btn = ttk.Button(dest, text="Browse…", command=self.choose_project)
        self.browse_btn.grid(row=1, column=2)
        self.open_btn = ttk.Button(dest, text="Open folder in VS Code", command=self.open_project)
        self.open_btn.grid(row=2, column=2, pady=(4, 0))
        self.settings_chk = ttk.Checkbutton(
            dest, text="Write recommended VS Code settings into the folder (.vscode/settings.json)",
            variable=self.opt_settings)
        self.settings_chk.grid(row=2, column=0, columnspan=2, sticky="w")
        dest.columnconfigure(1, weight=1)

        behave = ttk.LabelFrame(right, text="Behaviour", padding=8)
        behave.pack(fill="x", pady=(6, 0))
        ttk.Label(behave, text="Pace:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(behave, textvariable=self.pace, state="readonly", width=10,
                     values=list(PACES)).grid(row=0, column=1, sticky="w", padx=(4, 14))
        ttk.Checkbutton(behave, text="Typos fixed with Backspace", variable=self.opt_typos).grid(
            row=0, column=2, sticky="w")
        ttk.Checkbutton(behave, text="Select and rewrite draft code", variable=self.opt_rewrites).grid(
            row=0, column=3, sticky="w", padx=10)
        ttk.Checkbutton(behave, text="Navigate back (missed lines, reviewing blocks)",
                        variable=self.opt_navigation).grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Checkbutton(behave, text="Check the editor afterwards and correct differences",
                        variable=self.opt_verify).grid(row=2, column=2, columnspan=2, sticky="w")
        ttk.Checkbutton(behave, text="Replace existing content", variable=self.opt_replace).grid(
            row=1, column=0, columnspan=2, sticky="w")

        controls = ttk.Frame(right)
        controls.pack(fill="x", pady=(8, 0))
        self.start_btn = ttk.Button(controls, text="▶ Start typing", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(controls, text="Ⅱ Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(controls, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.preview_btn = ttk.Button(controls, text="Preview plan", command=self.preview)
        self.preview_btn.pack(side="left", padx=6)
        ttk.Label(controls, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(side="right")

        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 4))
        self.log = tk.Text(right, height=7, wrap="word", state="disabled", font=("Consolas", 9),
                           background="#f4f5f7")
        self.log.pack(fill="x")
        ttk.Label(
            right, wraplength=760, foreground="#666",
            text="GhostWriter sends ordinary keyboard input to the focused window. Use a scratch file or a "
                 "disposable project folder. Emergency stop: move the mouse to the top-left corner of the screen."
        ).pack(anchor="w", pady=(6, 0))
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

    def _build_session(self) -> Session | None:
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
        session = Session(jobs, options, log=self._write_log)
        try:
            self.problems = session.prepare()
        except Exception as exc:   # planning must never take the window down
            messagebox.showerror("GhostWriter", f"Could not plan the typing session:\n{exc}")
            return None
        for message in self.problems:
            self._write_log("! " + message)
        if not any(j.plan for j in jobs):
            messagebox.showwarning("GhostWriter", "Nothing to type:\n\n" + "\n".join(self.problems))
            return None
        return session

    def preview(self):
        session = self._build_session()
        if not session:
            return
        for job in session.jobs:
            if job.plan:
                stats = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in job.plan.stats.items() if v)
                self._write_log(f"    plan for {job.name}: {stats or 'straight typing'}")
        self.status.set("Plan ready")

    def start(self):
        try:
            backend = create_backend(self.config["input_backend"])
        except ImportError:
            return messagebox.showerror(
                "Missing packages",
                "Open PowerShell in the GhostWriter folder and run:\n\npy -m pip install -r requirements.txt")
        session = self._build_session()
        if not session:
            return
        project = session.o.project_dir is not None
        todo = [j.name for j in session.jobs if j.plan]
        if project:
            question = (f"GhostWriter will create {len(todo)} file(s) in\n{session.o.project_dir}\n"
                        "and type them in the VS Code window you focus during the countdown.\n\n"
                        "That window must have this folder open. Continue?")
        else:
            question = ("GhostWriter will type into whichever window has focus after the countdown.\n\n"
                        "Click into an empty VS Code editor tab during the countdown. Continue?")
        if self.problems:
            question += "\n\nNotes:\n• " + "\n• ".join(self.problems[:6])
        if not messagebox.askyesno("Ready to type?", question):
            return
        try:
            session.prepare_project()
        except (OSError, ValueError) as exc:
            return messagebox.showerror("GhostWriter", f"Could not prepare the project folder:\n{exc}")

        c = self.config
        timing = Timing(pace=self.pace.get(), think_min=c["thinking_pause_min_s"],
                        think_max=c["thinking_pause_max_s"], line_min=c["line_pause_min_s"],
                        line_max=c["line_pause_max_s"])
        self.runner = Runner(backend, timing,
                             on_status=lambda text: self.events.put(("status", text)),
                             on_progress=lambda done, total: self.events.put(("progress", done, total)))
        session.log = lambda text: self.events.put(("log", text))
        self.start_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Ⅱ Pause")
        self.stop_btn.configure(state="normal")
        self.worker = threading.Thread(target=self._run, args=(session,), daemon=True)
        self.worker.start()

    def _run(self, session: Session):
        try:
            session.run(self.runner)
            final = "Finished"
        except Stopped:
            final = "Stopped"
        except EmergencyStop:
            final = "Emergency stop"
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
                elif kind == "progress":
                    self.progress.configure(maximum=max(1, event[2]), value=event[1])
                elif kind == "log":
                    self._write_log(event[1])
                elif kind == "error":
                    messagebox.showerror("GhostWriter", event[1])
                elif kind == "done":
                    self.status.set(event[1])
                    self.start_btn.configure(state="normal")
                    self.preview_btn.configure(state="normal")
                    self.pause_btn.configure(state="disabled", text="Ⅱ Pause")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def toggle_pause(self):
        if not self.runner:
            return
        if self.runner.paused:
            self.runner.resume()
            self.pause_btn.configure(text="Ⅱ Pause")
            self.status.set("Typing")
        else:
            self.runner.pause()
            self.pause_btn.configure(text="▶ Resume")
            self.status.set("Paused")

    def stop(self):
        if self.runner:
            self.runner.stop()
            self.status.set("Stopping…")

    def close(self):
        if self.runner:
            self.runner.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    GhostWriterApp(root)
    root.mainloop()
