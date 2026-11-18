"""Timing, pointer motion and the focus guard."""
import math
import random
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ghostwriter.actions import Key, Pause, Plan, Type
from ghostwriter.driver import NotFocused, Runner, Stopped, Timing
from ghostwriter.editor import VirtualEditor
from ghostwriter.motion import PACES, Rhythm, mouse_path
from ghostwriter.planner import PlanOptions, plan_file

from fake_vscode import FakeVSCode

SAMPLE = (Path(__file__).resolve().parent.parent / "samples" / "inventory.py").read_text()


class Desktop(FakeVSCode):
    """FakeVSCode plus a window, a pointer and a record of what was sent."""
    supports_hold = True

    def __init__(self, editors):
        super().__init__(editors)
        self.window = 100
        self.front = [100]              # foreground window per call (last value repeats)
        self.pointer = (900, 500)
        self.moves, self.wheel, self.holds, self.sent_while_away = [], [], [], 0

    def foreground(self):
        return self.front.pop(0) if len(self.front) > 1 else self.front[0]

    def is_own_window(self, hwnd):
        return hwnd == 1

    def window_rect(self, hwnd=None):
        return (0, 0, 1600, 900)

    def mouse_pos(self):
        return self.pointer

    def mouse_move(self, x, y):
        self.pointer = (x, y)
        self.moves.append((x, y))

    def scroll(self, notches):
        self.wheel.append(notches)

    def type_char(self, ch, hold=0.0):
        self.holds.append(hold)
        super().type_char(ch)

    def key(self, name, hold=0.0):
        super().key(name)


class RhythmTests(unittest.TestCase):
    def gaps(self, pace, text=SAMPLE[:1500], seed=1):
        rhythm = Rhythm(pace, random.Random(seed))
        chars = [c for c in text if c != "\n"]
        return [rhythm.char_gap(c, p) for p, c in zip([None] + chars, chars)]

    def test_paces_are_ordered_and_humanly_possible(self):
        medians = [statistics.median(self.gaps(p)) for p in ("Slow", "Natural", "Fast", "Very fast")]
        self.assertEqual(medians, sorted(medians, reverse=True))
        natural = self.gaps("Natural")
        wpm = 60 / statistics.mean(natural) / 5
        self.assertTrue(40 < wpm < 110, wpm)
        self.assertGreater(min(natural), 0.02)

    def test_intervals_are_skewed_not_uniform(self):
        gaps = self.gaps("Natural")
        self.assertGreater(statistics.mean(gaps), statistics.median(gaps))        # long right tail
        self.assertGreater(statistics.pstdev(gaps) / statistics.mean(gaps), 0.25)

    def test_finger_and_hand_effects(self):
        rhythm = Rhythm("Natural", random.Random(2))
        mean = lambda a, b: statistics.mean(rhythm.char_gap(b, a) for _ in range(3000))
        self.assertLess(mean("f", "j"), mean("f", "d"))     # other hand beats same hand
        self.assertLess(mean("f", "d"), mean("f", "r"))     # ... which beats the same finger
        self.assertLess(mean("a", "s"), mean("a", "S"))     # Shift costs time
        self.assertLess(mean("e", "t"), mean(" ", "t"))     # words start slower

    def test_speed_drifts(self):
        rhythm = Rhythm("Natural", random.Random(3))
        gaps = [rhythm.char_gap("e", "t") for _ in range(4000)]
        blocks = [statistics.mean(gaps[i:i + 100]) for i in range(0, 4000, 100)]
        self.assertGreater(max(blocks) / min(blocks), 1.08)

    def test_key_is_held_for_a_plausible_time(self):
        rhythm = Rhythm("Natural", random.Random(4))
        holds = [rhythm.hold(0.14) for _ in range(500)]
        self.assertTrue(all(0.02 <= h <= 0.18 for h in holds))
        self.assertGreater(len({round(h, 3) for h in holds}), 30)

    def test_long_runs_look_like_a_held_key(self):
        rhythm = Rhythm("Natural", random.Random(5))
        held = rhythm.key_gaps("backspace", 12)
        self.assertGreater(held[1], 0.3)                          # repeat delay
        self.assertTrue(all(0.02 <= g < 0.06 for g in held[2:]))  # repeat rate
        taps = rhythm.key_gaps("backspace", 3)
        self.assertTrue(all(g > 0.06 for g in taps))

    def test_pauses_scale_with_pace(self):
        slow = statistics.mean(Rhythm("Slow", random.Random(6)).pause("think") for _ in range(400))
        fast = statistics.mean(Rhythm("Fast", random.Random(6)).pause("think") for _ in range(400))
        self.assertGreater(slow, fast * 1.5)
        self.assertIn("Natural", PACES)


class MouseTests(unittest.TestCase):
    def test_path_is_smooth_and_arrives(self):
        for seed in range(30):
            rng = random.Random(seed)
            start, end = (rng.randint(0, 1900), rng.randint(0, 1000)), (rng.randint(0, 1900), rng.randint(0, 1000))
            path = mouse_path(start, end, rng)
            self.assertEqual(path[-1][:2], end)
            distance = math.dist(start, end)
            previous, longest = start, 0
            for x, y, dt in path:
                longest = max(longest, math.dist(previous, (x, y)))
                previous = (x, y)
            self.assertLess(longest, max(12, distance * 0.08))    # no teleporting
            self.assertTrue(0.1 < sum(p[2] for p in path) < 2.5)

    def test_path_is_curved_and_starts_and_ends_slowly(self):
        path = mouse_path((0, 0), (1000, 0), random.Random(1), overshoot=False)
        self.assertGreater(max(abs(y) for _, y, _ in path), 3)
        xs = [0] + [x for x, _, _ in path]
        steps = [b - a for a, b in zip(xs, xs[1:])]
        middle = max(steps)
        self.assertLess(steps[0], middle / 3)
        self.assertLess(steps[-1], middle / 3)


class RunnerTests(unittest.TestCase):
    def plan(self, seed=3):
        return plan_file(SAMPLE.rstrip("\n"), "python", options=PlanOptions(seed=seed, reviews=1.0))

    def test_pointer_moves_and_scrolls_but_text_is_unchanged(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        runner = Runner(desk, Timing(rng=random.Random(1)), time_scale=0, mouse=True)
        runner.lock_focus()
        plan = self.plan()
        runner.run_plan(plan)
        self.assertEqual(desk.ed.text(), plan.target)
        self.assertGreater(len(desk.moves), 20)
        self.assertTrue(desk.wheel)
        self.assertEqual(sum(desk.wheel), 0)                      # scrolled back to where it was
        self.assertFalse(hasattr(desk, "click"))
        for x, y in desk.moves:                                   # stays inside the editor area
            self.assertTrue(600 <= x <= 1460 and 180 <= y <= 720, (x, y))

    def test_pointer_is_left_alone_when_the_person_moves_it(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        runner = Runner(desk, Timing(rng=random.Random(1)), time_scale=0, mouse=True)
        calls = {"n": 0}
        original = desk.mouse_pos

        def hand_on_mouse():
            calls["n"] += 1
            if calls["n"] > 3:
                desk.pointer = (desk.pointer[0] + 40, desk.pointer[1] + 40)
            return original()

        desk.mouse_pos = hand_on_mouse
        self.assertFalse(runner._glide(1200, 400))
        self.assertLess(len(desk.moves), 5)

    def test_mouse_is_off_by_default(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        Runner(desk, time_scale=0).run_plan(self.plan())
        self.assertEqual(desk.moves, [])

    def test_typing_waits_while_another_window_is_in_front(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        statuses = []
        runner = Runner(desk, time_scale=0, on_status=statuses.append)
        runner.lock_focus()
        typed_at_switch = []
        original = desk.type_char

        def type_char(ch, hold=0.0):
            if desk.front[0] != 100:
                desk.sent_while_away += 1
            original(ch, hold)
            if len(desk.ed.text()) == 3 and not typed_at_switch:
                typed_at_switch.append(True)
                desk.front = [555] * 25 + [100]       # another window for a while, then back

        desk.type_char = type_char
        runner.run_plan(Plan("abcdef", "python", [Type(c) for c in "abcdef"]))
        self.assertEqual(desk.ed.text(), "abcdef")
        self.assertEqual(desk.sent_while_away, 0)
        self.assertIn("Waiting: VS Code is not the active window", statuses)

    def test_refuses_to_type_into_its_own_window(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        desk.front = [1]
        with self.assertRaises(NotFocused):
            Runner(desk, time_scale=0).lock_focus()

    def test_stop_while_waiting_for_focus(self):
        desk = Desktop({"a.py": VirtualEditor("python")})
        runner = Runner(desk, time_scale=0)
        runner.lock_focus()
        desk.front = [555]
        calls = {"n": 0}
        original = desk.check_failsafe

        def failsafe():
            calls["n"] += 1
            if calls["n"] > 10:
                runner.stop()
            original()

        desk.check_failsafe = failsafe
        with self.assertRaises(Stopped):
            runner.run_plan(Plan("ab", "python", [Type("a"), Pause("line"), Key("enter")]))
        self.assertEqual(desk.ed.text(), "")


if __name__ == "__main__":
    unittest.main()
