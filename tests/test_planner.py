"""Every plan, replayed through the editor model, must reproduce the file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import unittest

from ghostwriter.actions import GotoLine, Key, Type
from ghostwriter.editor import EditorSettings
from ghostwriter.languages import detect_language
from ghostwriter.outline import build_outline, summarize
from ghostwriter.planner import Planner, PlanOptions, normalize_target, plan_file, replay

ROOT = Path(__file__).resolve().parent.parent
FILES = sorted((ROOT / "samples").glob("*.*")) + sorted((ROOT / "tests" / "corpus").glob("*.*"))
PROFILES = {
    "default": EditorSettings(),
    "advanced": EditorSettings(auto_indent="advanced"),
    "brackets": EditorSettings(auto_indent="brackets"),
    "keep": EditorSettings(auto_indent="keep"),
    "none": EditorSettings(auto_indent="none"),
    "noclose": EditorSettings(auto_closing_brackets="never", auto_closing_quotes="never"),
    "always": EditorSettings(auto_closing_brackets="always", auto_closing_quotes="always"),
    "tab2": EditorSettings(tab_size=2),
    "tabs": EditorSettings(insert_spaces=False),
}
PLAIN = dict(mistakes=0.0, rewrites=0.0, revisits=0.0, reviews=0.0)
BUSY = dict(mistakes=0.2, rewrites=0.6, revisits=1.0, reviews=1.0)


def load(path):
    text = path.read_text(encoding="utf-8").rstrip("\n")
    return text, detect_language(path.name, text)


class PlansReproduceTheFile(unittest.TestCase):
    def check(self, style, seeds):
        for path in FILES:
            text, lang = load(path)
            for name, settings in PROFILES.items():
                for seed in seeds:
                    with self.subTest(file=path.name, profile=name, seed=seed):
                        planner = Planner(text, lang, settings, PlanOptions(seed=seed, **style))
                        plan = planner.build()
                        self.assertEqual(planner.warnings, [])
                        self.assertEqual(replay(plan, settings), normalize_target(text))

    def test_plain(self):
        self.check(PLAIN, [0])

    def test_default_behaviour(self):
        self.check({}, [1, 2, 3])

    def test_busy(self):
        self.check(BUSY, [4, 5])


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.text, self.lang = load(ROOT / "samples" / "inventory.py")

    def test_editor_indentation_is_used_instead_of_typing_spaces(self):
        plan = plan_file(self.text, "python", EditorSettings(), PlanOptions(seed=0, **PLAIN))
        spaces_typed = sum(1 for a in plan.actions if a == Type(" "))
        self.assertLess(plan.keystrokes, len(self.text) * 0.9)
        self.assertLess(spaces_typed, self.text.count(" ") * 0.5)

    def test_closing_brackets_are_typed_over_or_stepped_past(self):
        text = "data = {\n    \"a\": [1, 2],\n}\nprint(data)"
        plan = plan_file(text, "python", EditorSettings(), PlanOptions(seed=0, **PLAIN))
        keys = [a.name for a in plan.actions if isinstance(a, Key)]
        self.assertIn("down", keys)                 # steps over the auto-inserted "}"
        self.assertNotIn("delete", keys)
        self.assertEqual(plan.stats["bracket_blocks"], 1)
        self.assertEqual(replay(plan), text)

    def test_corrections_are_made_and_undone(self):
        plan = plan_file(self.text, "python", EditorSettings(), PlanOptions(seed=3, **BUSY))
        self.assertGreater(plan.stats["typos"], 5)
        self.assertGreater(plan.stats["rewrites"], 2)
        keys = [a.name for a in plan.actions if isinstance(a, Key)]
        self.assertIn("backspace", keys)
        self.assertIn("shift+left", keys)           # selects the draft token
        self.assertEqual(replay(plan), self.text)

    def test_navigation(self):
        plan = plan_file(self.text, "python", EditorSettings(), PlanOptions(seed=3, **BUSY))
        self.assertGreater(plan.stats["revisits"], 0)
        self.assertGreater(plan.stats["reviews"], 0)
        keys = [a.name for a in plan.actions if isinstance(a, Key)]
        self.assertIn("up", keys)
        self.assertTrue(any(isinstance(a, GotoLine) for a in plan.actions) or "ctrl+end" in keys)

    def test_same_seed_same_plan(self):
        a = plan_file(self.text, "python", EditorSettings(), PlanOptions(seed=11))
        b = plan_file(self.text, "python", EditorSettings(), PlanOptions(seed=11))
        self.assertEqual(a.actions, b.actions)

    def test_replace_existing_content(self):
        options = PlanOptions(seed=1, replace_existing=True, existing_text="old\n  stuff(")
        plan = plan_file("x = 1\ny = 2", "python", EditorSettings(), options)
        self.assertEqual(replay(plan, existing="old\n  stuff("), "x = 1\ny = 2")

    def test_escape_precedes_enter_after_typing(self):
        plan = plan_file("abc\ndef", "python", EditorSettings(), PlanOptions(seed=0, **PLAIN))
        names = [a.name if isinstance(a, Key) else "type" for a in plan.actions if isinstance(a, (Key, Type))]
        self.assertEqual(names[names.index("enter") - 1], "escape")

    def test_odd_input(self):
        for text in ["", "\n\n", "x", "a\tb", "s = 'héllo ☃'", "   \n\tindented\n", "((([[[{{{", ")]}'\"`",
                     "if x:\r\n    pass\r\n"]:
            for lang in ("python", "javascript", "html", "css", "plain"):
                with self.subTest(text=text, lang=lang):
                    plan = plan_file(text, lang, EditorSettings(), PlanOptions(seed=2))
                    self.assertEqual(replay(plan), normalize_target(text))


class Outline(unittest.TestCase):
    def test_python(self):
        text, lang = load(ROOT / "samples" / "inventory.py")
        blocks = build_outline(text, lang)
        labels = [b.label for b in blocks]
        self.assertIn("class Inventory", labels)
        self.assertIn("function low_stock()", labels)
        self.assertIn("for loop", labels)
        self.assertIn("if condition", labels)
        self.assertEqual(summarize(blocks), "2 classes, 7 functions, 4 loops, 10 conditions")
        report = next(b for b in blocks if b.name == "report")
        self.assertEqual(text.split("\n")[report.end].strip(), 'return "\\n".join(lines)')

    def test_javascript(self):
        text, lang = load(ROOT / "samples" / "todo.js")
        labels = [b.label for b in build_outline(text, lang)]
        for expected in ("class Todo", "function toggle()", "function summary()", "for loop", "switch condition"):
            self.assertIn(expected, labels)

    def test_css_and_html(self):
        text, lang = load(ROOT / "samples" / "style.css")
        self.assertIn("rule .app h1::after", [b.label for b in build_outline(text, lang)])
        text, lang = load(ROOT / "samples" / "index.html")
        self.assertIn("element script", [b.label for b in build_outline(text, lang)])


if __name__ == "__main__":
    unittest.main()
