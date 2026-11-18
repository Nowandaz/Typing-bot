"""Build test cases for the Monaco oracle (see run.mjs).

Two kinds of case are produced for every sample file and settings profile:

* ``naive``  - the file typed character by character with no planning, so the
  buffer ends up garbled exactly the way a naive typing bot garbles it.  This
  checks the editor model in isolation.
* ``plan``   - the planner's keystroke plan, which must end with the buffer
  equal to the file.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ghostwriter.editor import EditorSettings, VirtualEditor  # noqa: E402
from ghostwriter.languages import detect_language  # noqa: E402

PROFILES = {
    "default": EditorSettings(),
    "advanced": EditorSettings(auto_indent="advanced"),
    "keep": EditorSettings(auto_indent="keep"),
    "noclose": EditorSettings(auto_closing_brackets="never", auto_closing_quotes="never"),
    "tab2": EditorSettings(tab_size=2),
    "tabs": EditorSettings(insert_spaces=False),
}


def snapshot(ed):
    return {"text": ed.text(), "row": ed.row, "col": ed.col}


def naive_case(name, lang, settings, text, every=1):
    ed = VirtualEditor(lang, settings)
    steps = []
    for i, ch in enumerate(text):
        if ch == "\n":
            ed.key("enter")
            step = ["key", "enter"]
        else:
            ed.type_char(ch)
            step = ["type", ch]
        step.append(snapshot(ed) if (i % every == 0 or ch == "\n") else None)
        steps.append(step)
    return {"name": name, "language": lang, "settings": asdict(settings), "steps": steps, "final": ed.text()}


def plan_case(name, lang, settings, text, seed, style):
    from ghostwriter.planner import Planner, PlanOptions
    from ghostwriter.actions import Key, Type, Paste, GotoLine
    plan = Planner(text, lang, settings, PlanOptions(seed=seed, **style)).build()
    ed = VirtualEditor(lang, settings)
    steps = []
    for a in plan.actions:
        if isinstance(a, Type):
            ed.type_char(a.ch); steps.append(["type", a.ch, snapshot(ed)])
        elif isinstance(a, Key):
            ed.key(a.name); steps.append(["key", a.name, snapshot(ed)])
        elif isinstance(a, Paste):
            ed.paste(a.text); steps.append(["paste", a.text, snapshot(ed)])
        elif isinstance(a, GotoLine):
            ed.goto_line(a.line); steps.append(["goto", a.line, snapshot(ed)])
    return {"name": name, "language": lang, "settings": asdict(settings), "steps": steps, "final": plan.target}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["naive", "plan", "both"], default="plan",
                    help="naive cases type garbage on purpose and probe the model far outside what plans do")
    ap.add_argument("--profiles", default=",".join(PROFILES))
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--stress", action="store_true", help="make corrections, rewrites and revisits very frequent")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()
    files = [Path(f) for f in args.files] or sorted((ROOT / "samples").glob("*.*")) + sorted(
        (ROOT / "tests" / "corpus").glob("*.*"))
    cases = []
    for f in files:
        text = f.read_text(encoding="utf-8").rstrip("\n")
        lang = detect_language(f.name, text)
        for pname in args.profiles.split(","):
            settings = PROFILES[pname]
            if args.kind in ("naive", "both"):
                cases.append(naive_case(f"naive/{f.name}/{pname}", lang, settings, text))
            if args.kind in ("plan", "both"):
                for seed in range(args.seeds):
                    style = {} if seed else {"mistakes": 0.0, "revisits": 0.0}
                    if args.stress:
                        style = {"mistakes": 0.2, "rewrites": 0.6, "revisits": 1.0, "reviews": 1.0}
                    cases.append(plan_case(f"plan/{f.name}/{pname}/seed{seed}", lang, settings, text, seed, style))
    json.dump(cases, sys.stdout)


if __name__ == "__main__":
    main()
