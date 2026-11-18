"""End to end: a multi-file session typed into a simulated VS Code."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import tempfile
import unittest

from ghostwriter import vscode
from ghostwriter.driver import Runner, Stopped
from ghostwriter.editor import EditorSettings, VirtualEditor
from ghostwriter.planner import PlanOptions
from ghostwriter.session import Job, Session, SessionOptions, jobs_from_paths

from fake_vscode import FakeVSCode

ROOT = Path(__file__).resolve().parent.parent


class ProjectSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def run_session(self, jobs, options, user_settings=None, existing=None):
        log = []
        session = Session(jobs, options, log=log.append, user_settings=user_settings or {})
        messages = session.prepare()
        session.prepare_project()
        editors = {j.name: VirtualEditor(j.language, session.settings_for(j), (existing or {}).get(j.name, ""))
                   for j in jobs}
        fake = FakeVSCode(editors)
        session.run(Runner(fake, time_scale=0))
        return session, fake, messages, log

    def test_files_are_created_opened_typed_verified_and_saved(self):
        jobs = jobs_from_paths(sorted((ROOT / "samples").glob("*.*")))
        options = SessionOptions(project_dir=self.project, countdown=0, plan=PlanOptions(seed=5))
        session, fake, messages, log = self.run_session(jobs, options)
        self.assertEqual(fake.opened, [j.name for j in jobs])
        self.assertEqual(fake.saved, [j.name for j in jobs])
        for job in jobs:
            self.assertEqual(fake.editors[job.name].text(), job.text)
            self.assertEqual(job.status, "verified")
            self.assertTrue((self.project / job.name).is_file())
        written = json.loads((self.project / ".vscode" / "settings.json").read_text())
        self.assertEqual(written["editor.autoIndent"], "advanced")
        self.assertEqual(written["[python]"]["editor.tabSize"], 4)
        self.assertEqual(messages, [])

    def test_per_language_indentation_comes_from_the_files(self):
        jobs = jobs_from_paths([ROOT / "tests" / "corpus" / "page.html", ROOT / "samples" / "inventory.py"])
        options = SessionOptions(project_dir=self.project, countdown=0, plan=PlanOptions(seed=1))
        session, fake, _, _ = self.run_session(jobs, options)
        self.assertEqual(session.settings_for(jobs[0]).tab_size, 2)
        self.assertEqual(session.settings_for(jobs[1]).tab_size, 4)
        self.assertEqual(fake.editors["page.html"].text(), jobs[0].text)

    def test_existing_files_are_skipped_unless_replacing(self):
        (self.project / "a.py").write_text("print('keep me')\n")
        jobs = [Job("a.py", "x = 1"), Job("b.py", "y = 2")]
        options = SessionOptions(project_dir=self.project, countdown=0, plan=PlanOptions(seed=1))
        session, fake, messages, _ = self.run_session(jobs, options, existing={"a.py": "print('keep me')\n"})
        self.assertEqual(jobs[0].status, "skipped")
        self.assertEqual(fake.editors["a.py"].text(), "print('keep me')\n")
        self.assertEqual(fake.editors["b.py"].text(), "y = 2")
        self.assertTrue(any("already has content" in m for m in messages))

        jobs = [Job("a.py", "x = 1")]
        options.replace_existing = True
        session, fake, _, _ = self.run_session(jobs, options, existing={"a.py": "print('keep me')\n"})
        self.assertEqual(fake.editors["a.py"].text(), "x = 1")

    def test_prepare_reports_progress_and_survives_many_files(self):
        jobs = [Job(f"pkg/mod{i}.py", f"x = {i}\n\n\ndef f():\n    return {i}\n") for i in range(60)]
        options = SessionOptions(project_dir=self.project, countdown=0, plan=PlanOptions(seed=1))
        session = Session(jobs, options, log=lambda s: None, user_settings={})
        seen = []
        messages = session.prepare(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(len(seen), 60)                 # one progress call per file
        self.assertEqual(seen[-1], (60, 60))
        self.assertEqual(sum(1 for j in jobs if j.plan), 60)
        self.assertEqual(messages, [])

    def test_one_unplannable_file_does_not_abort_the_batch(self):
        jobs = [Job("a.py", "x = 1"), Job("b.py", "y = 2"), Job("c.py", "z = 3")]
        options = SessionOptions(project_dir=self.project, countdown=0, plan=PlanOptions(seed=1))
        session = Session(jobs, options, log=lambda s: None, user_settings={})
        # Make the middle file blow up inside the planner.
        import ghostwriter.session as sessmod
        real = sessmod.Planner

        class Exploding(real):
            def build(self):
                if "y = 2" in self.target:
                    raise RuntimeError("boom")
                return super().build()

        sessmod.Planner = Exploding
        try:
            messages = session.prepare()
        finally:
            sessmod.Planner = real
        self.assertIsNone(jobs[1].plan)
        self.assertIsNotNone(jobs[0].plan)
        self.assertIsNotNone(jobs[2].plan)
        self.assertTrue(any("b.py" in m and "boom" in m for m in messages))

    def test_paths_cannot_escape_the_project(self):
        session = Session([Job("../evil.py", "x = 1")], SessionOptions(project_dir=self.project, countdown=0),
                          user_settings={})
        session.prepare()
        with self.assertRaises(ValueError):
            session.prepare_project()

    def test_existing_workspace_settings_are_backed_up_and_merged(self):
        folder = self.project / ".vscode"
        folder.mkdir()
        (folder / "settings.json").write_text('{\n  // mine\n  "editor.fontSize": 15,\n}\n')
        jobs = [Job("a.py", "x = 1")]
        self.run_session(jobs, SessionOptions(project_dir=self.project, countdown=0))
        merged = json.loads((folder / "settings.json").read_text())
        self.assertEqual(merged["editor.fontSize"], 15)
        self.assertFalse(merged["editor.formatOnType"])
        self.assertIn("// mine", (folder / "settings.json.ghostwriter-backup").read_text())


class FocusedSession(unittest.TestCase):
    def run_one(self, job, options, editor_settings, user_settings):
        session = Session([job], options, user_settings=user_settings)
        messages = session.prepare()
        fake = FakeVSCode({job.name: VirtualEditor(job.language, editor_settings)})
        session.run(Runner(fake, time_scale=0))
        return fake, messages

    def test_user_settings_drive_the_plan(self):
        text = (ROOT / "samples" / "todo.js").read_text().rstrip("\n")
        user = {"editor.tabSize": 2, "editor.autoClosingBrackets": "never", "[javascript]": {"editor.autoIndent": "keep"}}
        expected = EditorSettings(tab_size=2, auto_closing_brackets="never", auto_indent="keep")
        job = Job("todo.js", text)
        fake, messages = self.run_one(job, SessionOptions(countdown=0, plan=PlanOptions(seed=2)), expected, user)
        self.assertEqual(fake.ed.text(), text)
        self.assertEqual(job.status, "verified")
        self.assertTrue(any("acceptSuggestionOnCommitCharacter" in m for m in messages))

    def test_wrong_assumptions_are_caught_and_repaired(self):
        # The plan assumes VS Code defaults, but this "VS Code" never auto-closes.
        text = (ROOT / "samples" / "inventory.py").read_text().rstrip("\n")
        job = Job("inventory.py", text)
        actual = EditorSettings(auto_closing_brackets="never", auto_closing_quotes="never")
        fake, _ = self.run_one(job, SessionOptions(countdown=0, plan=PlanOptions(seed=2)), actual, {})
        self.assertEqual(job.status, "repaired")
        self.assertEqual(fake.ed.text(), text)

    def test_mismatch_is_reported_when_repair_is_off(self):
        job = Job("a.py", "x = [\n    1,\n]")
        actual = EditorSettings(auto_closing_brackets="never")
        options = SessionOptions(countdown=0, repair=False, plan=PlanOptions(seed=2))
        fake, _ = self.run_one(job, options, actual, {})
        self.assertEqual(job.status, "mismatch")

    def test_stop(self):
        job = Job("a.py", "x = 1\n" * 50)
        session = Session([job], SessionOptions(countdown=0), user_settings={})
        session.prepare()
        fake = FakeVSCode({"a.py": VirtualEditor("python")})
        runner = Runner(fake, time_scale=0)
        seen = []

        def progress(done, total):
            seen.append(done)
            if done >= 20:
                runner.stop()

        runner.on_progress = progress
        with self.assertRaises(Stopped):
            session.run(runner)
        self.assertLess(len(fake.ed.text()), 60)


class Settings(unittest.TestCase):
    def test_jsonc(self):
        data = vscode.parse_jsonc('{\n // c\n "a": "x // y", /* z */ "b": [1, 2,],\n}')
        self.assertEqual(data, {"a": "x // y", "b": [1, 2]})

    def test_language_blocks_win(self):
        user = {"editor.tabSize": 4, "[python]": {"editor.tabSize": 8}}
        workspace = {"editor.tabSize": 2}
        self.assertEqual(vscode.editor_settings("python", user, workspace).tab_size, 8)
        self.assertEqual(vscode.editor_settings("css", user, workspace).tab_size, 2)

    def test_invalid_values_are_ignored(self):
        self.assertEqual(vscode.editor_settings("css", {"editor.autoIndent": "weird", "editor.tabSize": "x"}),
                         EditorSettings())

    def test_indentation_detection(self):
        self.assertEqual(vscode.detect_indentation("a\n  b\n    c"), (2, True))
        self.assertEqual(vscode.detect_indentation("a\n\tb\n\t\tc"), (4, False))
        self.assertEqual(vscode.detect_indentation("a\n    b"), (4, True))


if __name__ == "__main__":
    unittest.main()
