"""A typing session: one or more files planned and typed in order."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import vscode
from .actions import Plan
from .driver import Runner, first_difference
from .editor import EditorSettings
from .languages import LANGUAGE_LABELS, detect_language
from .outline import summarize
from .planner import PlanError, PlanOptions, Planner, normalize_target

VSCODE_LANGUAGE_IDS = {
    "python": ["python"], "javascript": ["javascript", "javascriptreact"],
    "typescript": ["typescript", "typescriptreact"], "html": ["html"], "css": ["css"],
}


@dataclass
class Job:
    name: str                       # file name or project-relative path
    text: str
    language: str = ""
    plan: Plan | None = None
    status: str = "queued"
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if not self.language:
            self.language = detect_language(self.name, self.text)


@dataclass
class SessionOptions:
    project_dir: Path | None = None     # None: type into whichever editor has focus
    apply_settings: bool = True         # project mode: write .vscode/settings.json
    replace_existing: bool = False
    verify: bool = True
    repair: bool = True
    save: bool = True
    countdown: int = 5
    plan: PlanOptions = field(default_factory=PlanOptions)


class Session:
    def __init__(self, jobs: list[Job], options: SessionOptions, log=None, user_settings: dict | None = None):
        self.jobs = jobs
        self.o = options
        self.log = log or (lambda text: None)
        self.user_settings = vscode.load_settings(vscode.user_settings_path()) if user_settings is None \
            else user_settings
        self.workspace_settings: dict = {}

    # ---------------------------------------------------------------- planning
    def _workspace_layer(self) -> dict:
        o = self.o
        if o.project_dir is None:
            return {}
        existing = vscode.load_settings(Path(o.project_dir) / ".vscode" / "settings.json")
        if not o.apply_settings:
            return existing
        layer = dict(existing)
        layer.update(vscode.RECOMMENDED)
        for job in self.jobs:
            size, spaces = vscode.detect_indentation(job.text)
            for lang in VSCODE_LANGUAGE_IDS.get(job.language, []):
                block = dict(layer.get(f"[{lang}]", {}))
                block.update({"editor.tabSize": size, "editor.insertSpaces": spaces})
                layer[f"[{lang}]"] = block
        return layer

    def settings_for(self, job: Job) -> EditorSettings:
        return vscode.editor_settings(job.language, self.user_settings, self.workspace_settings)

    def prepare(self) -> list[str]:
        """Plan every job.  Returns messages worth showing before starting."""
        o = self.o
        messages = []
        self.workspace_settings = self._workspace_layer()
        managed = o.project_dir is not None and o.apply_settings
        for job in self.jobs:
            job.notes.clear()
            settings = self.settings_for(job)
            options = PlanOptions(**o.plan.__dict__)
            if o.project_dir is not None:
                dest = Path(o.project_dir) / job.name
                existing = dest.read_text(encoding="utf-8-sig", errors="replace") if dest.is_file() else ""
                if existing.strip():
                    if not o.replace_existing:
                        job.status = "skipped"
                        job.plan = None
                        messages.append(f"{job.name}: already has content in the project folder - skipped "
                                        f"(tick 'Replace existing content' to overwrite it).")
                        continue
                    options.replace_existing, options.existing_text = True, existing.replace("\r\n", "\n")
                elif existing:
                    options.replace_existing, options.existing_text = True, existing.replace("\r\n", "\n")
            elif o.replace_existing:
                options.replace_existing, options.existing_text = True, "?"
            size, spaces = vscode.detect_indentation(job.text, settings.tab_size)
            if (size, spaces) != (settings.tab_size, settings.insert_spaces) and job.text.strip():
                style = f"{size} spaces" if spaces else "tabs"
                editor = f"{settings.tab_size} spaces" if settings.insert_spaces else "tabs"
                job.notes.append(f"indented with {style} but VS Code is set to {editor}; expect extra "
                                 f"indentation corrections")
            if not managed:
                for hazard in vscode.hazards(job.language, self.user_settings, self.workspace_settings):
                    if hazard not in messages:
                        messages.append(hazard)
            try:
                planner = Planner(job.text, job.language, settings, options)
                job.plan = planner.build()
                job.status = "planned"
                job.notes.extend(planner.warnings)
            except PlanError as exc:
                job.plan = None
                job.status = "failed"
                messages.append(f"{job.name}: {exc}")
                continue
            label = LANGUAGE_LABELS.get(job.language, job.language)
            self.log(f"{job.name}: {label}, {summarize(job.plan.blocks)}; {job.plan.keystrokes} keystrokes"
                     + "".join(f"\n    note: {n}" for n in job.notes))
        return messages

    def prepare_project(self):
        """Create the destination files and workspace settings (project mode)."""
        o = self.o
        if o.project_dir is None:
            return
        root = Path(o.project_dir)
        root.mkdir(parents=True, exist_ok=True)
        if o.apply_settings:
            path = vscode.write_workspace_settings(root, {
                k: v for k, v in self.workspace_settings.items()
                if k in vscode.RECOMMENDED or k.startswith("[")})
            self.log(f"VS Code workspace settings written to {path}")
        for job in self.jobs:
            if job.plan is None:
                continue
            dest = (root / job.name).resolve()
            if root.resolve() not in dest.parents:
                raise ValueError(f"{job.name} is outside the project folder")
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("", encoding="utf-8")

    # ----------------------------------------------------------------- running
    def run(self, runner: Runner):
        o = self.o
        for remaining in range(o.countdown, 0, -1):
            runner.on_status(f"Focus VS Code… {remaining}")
            runner.wait(1.0)
        runner.lock_focus()
        todo = [j for j in self.jobs if j.plan is not None]
        for number, job in enumerate(todo, start=1):
            prefix = f"[{number}/{len(todo)}] " if len(todo) > 1 else ""
            if o.project_dir is not None:
                runner.status(f"{prefix}Opening {job.name}")
                runner.send_key("escape", 0.1)
                runner.send_key("ctrl+p", 0.5)
                runner.send_text(job.name.replace("\\", "/"))
                runner.wait(0.7)
                runner.send_key("enter", 1.0)
            job.status = "typing"
            self.log(f"{prefix}Typing {job.name}")
            runner.run_plan(job.plan)
            job.status = "typed"
            if o.verify:
                runner.status(f"{prefix}Checking {job.name}")
                outcome = runner.verify(job.plan.target, o.repair)
                job.status = {"match": "verified", "repaired": "repaired"}.get(outcome, outcome)
                if outcome == "match":
                    self.log(f"    {job.name}: editor contents match the source exactly")
                elif outcome == "repaired":
                    self.log(f"    {job.name}: the editor differed from the source and was corrected by paste")
                elif outcome == "unreadable":
                    self.log(f"    {job.name}: could not read the editor contents to check them")
                else:
                    actual = runner.read_buffer() or ""
                    self.log(f"    {job.name}: editor differs from the source - "
                             f"{first_difference(job.plan.target, actual)}")
            if o.project_dir is not None and o.save and job.status in ("typed", "verified", "repaired"):
                runner.send_key("ctrl+s", 0.5)
        runner.status("Finished")


def jobs_from_paths(paths: list[Path], root: Path | None = None) -> list[Job]:
    jobs = []
    for path in paths:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        name = path.name
        if root is not None:
            try:
                name = path.resolve().relative_to(Path(root).resolve()).as_posix()
            except ValueError:
                pass
        jobs.append(Job(name, normalize_target(text)))
    return jobs
