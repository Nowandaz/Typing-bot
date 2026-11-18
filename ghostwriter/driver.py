"""Sends a plan to the focused application as real keyboard input.

Two input backends:

* ``SendInputBackend`` (Windows, default) talks to ``SendInput`` directly.
  Characters are sent as Unicode packets, so the result does not depend on
  the keyboard layout (no dead-key surprises with quotes on international
  layouts) and navigation keys carry the extended-key flag, which is what
  makes Shift+Arrow selection work regardless of Num Lock.
* ``PyAutoGuiBackend`` is the fallback used on other platforms.

This is ordinary user-mode UI automation.  It does not hide itself from the
operating system or from other software.
"""
from __future__ import annotations

import random
import sys
import threading
import time
from dataclasses import dataclass

from .actions import GotoLine, Key, Note, Paste, Pause, Plan, Type


class EmergencyStop(Exception):
    """Raised when the mouse is moved to the top-left corner of the screen."""


class Stopped(Exception):
    pass


# ------------------------------------------------------------------ backends
class RecordingBackend:
    """Collects what would be sent; used by tests and by 'Preview plan'."""

    def __init__(self):
        self.events: list = []
        self.clipboard = ""

    def type_char(self, ch):
        self.events.append(("type", ch))

    def key(self, name):
        self.events.append(("key", name))

    def set_clipboard(self, text):
        self.clipboard = text

    def get_clipboard(self):
        return self.clipboard

    def check_failsafe(self):
        pass


class SendInputBackend:
    VK = {
        "enter": 0x0D, "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "escape": 0x1B, "space": 0x20,
        "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
        "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    }
    EXTENDED = {"delete", "left", "up", "right", "down", "home", "end"}

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        import pyperclip  # noqa: F401  (fail early if the clipboard helper is missing)

        self.ctypes = ctypes
        ULONG_PTR = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

        self.INPUT, self.KEYBDINPUT = INPUT, KEYBDINPUT
        self.POINT = wintypes.POINT
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self.user32.MapVirtualKeyW.restype = wintypes.UINT

    def _send(self, *events):
        """events: (vk, scan, flags) tuples sent as one atomic batch."""
        array = (self.INPUT * len(events))()
        for item, (vk, scan, flags) in zip(array, events):
            item.type = 1  # INPUT_KEYBOARD
            item.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        sent = self.user32.SendInput(len(events), array, self.ctypes.sizeof(self.INPUT))
        if sent != len(events):
            raise OSError("SendInput was blocked (is an elevated or secure window focused?)")

    def _vk_event(self, name, up=False):
        vk = self.VK[name] if name in self.VK else ord(name.upper())
        flags = (0x0002 if up else 0) | (0x0001 if name in self.EXTENDED else 0)
        return (vk, self.user32.MapVirtualKeyW(vk, 0), flags)

    def type_char(self, ch):
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            self._send((0, unit, 0x0004), (0, unit, 0x0004 | 0x0002))  # KEYEVENTF_UNICODE down, up

    def key(self, name):
        parts = name.split("+")
        mods, main = parts[:-1], parts[-1]
        for m in mods:
            self._send(self._vk_event(m))
        time.sleep(0.005 if mods else 0)
        self._send(self._vk_event(main))
        self._send(self._vk_event(main, up=True))
        for m in reversed(mods):
            self._send(self._vk_event(m, up=True))

    def set_clipboard(self, text):
        import pyperclip
        pyperclip.copy(text)

    def get_clipboard(self):
        import pyperclip
        return pyperclip.paste()

    def check_failsafe(self):
        point = self.POINT()
        if self.user32.GetCursorPos(self.ctypes.byref(point)) and point.x <= 0 and point.y <= 0:
            raise EmergencyStop()


class PyAutoGuiBackend:
    def __init__(self):
        import pyautogui
        import pyperclip
        self.gui, self.clip = pyautogui, pyperclip
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0
        self.mod = "command" if sys.platform == "darwin" else "ctrl"

    def type_char(self, ch):
        if ch == " ":
            self.gui.press("space")
        elif 32 < ord(ch) < 127:
            self.gui.write(ch)
        else:
            self.clip.copy(ch)
            self.gui.hotkey(self.mod, "v")

    def key(self, name):
        parts = [self.mod if p == "ctrl" else p for p in name.split("+")]
        try:
            if len(parts) == 1:
                self.gui.press(parts[0])
            else:
                self.gui.hotkey(*parts)
        except self.gui.FailSafeException as exc:
            raise EmergencyStop() from exc

    def set_clipboard(self, text):
        self.clip.copy(text)

    def get_clipboard(self):
        return self.clip.paste()

    def check_failsafe(self):
        if tuple(self.gui.position()) == (0, 0):
            raise EmergencyStop()


def create_backend(kind: str = "auto"):
    if kind == "sendinput" or (kind == "auto" and sys.platform == "win32"):
        return SendInputBackend()
    return PyAutoGuiBackend()


# -------------------------------------------------------------------- timing
PACES = {            # mean seconds per key, spread, pause scale
    "Slow": (0.105, 0.075, 1.3),
    "Natural": (0.065, 0.055, 1.0),
    "Fast": (0.035, 0.030, 0.6),
    "Very fast": (0.018, 0.018, 0.3),
}
PAUSES = {           # seconds (min, max) at Natural pace
    "line": (0.18, 0.65), "plan": (0.4, 1.1), "think": (1.0, 3.5), "notice": (0.3, 0.8),
    "review": (1.2, 2.8), "settle": (0.15, 0.3),
}


@dataclass
class Timing:
    pace: str = "Natural"
    think_min: float = 1.0
    think_max: float = 3.5
    line_min: float = 0.18
    line_max: float = 0.65
    rng: random.Random | None = None

    def __post_init__(self):
        self.rng = self.rng or random.Random()

    def keystroke(self, action, previous) -> float:
        mean, spread, _ = PACES.get(self.pace, PACES["Natural"])
        rng = self.rng
        delay = max(0.008, rng.gauss(mean, spread))
        if isinstance(action, Type):
            ch = action.ch
            if ch in "{}[]()=+-*/<>":
                delay += rng.uniform(0.035, 0.12) * mean / 0.065
            elif ch in ".,:;":
                delay += rng.uniform(0.025, 0.09) * mean / 0.065
            elif ch == " ":
                delay *= rng.uniform(0.65, 1.0)
        elif isinstance(action, Key):
            if previous == action:
                delay = max(0.012, rng.gauss(mean * 0.55, spread * 0.3))   # held / repeated key
            elif action.name.startswith(("shift+", "ctrl+")) or action.name in ("home", "end"):
                delay += rng.uniform(0.05, 0.16)
        return min(delay, 1.2)

    def pause(self, kind: str) -> float:
        lo, hi = PAUSES.get(kind, PAUSES["line"])
        if kind == "think":
            lo, hi = self.think_min, self.think_max
        elif kind == "line":
            lo, hi = self.line_min, self.line_max
        scale = PACES.get(self.pace, PACES["Natural"])[2]
        return self.rng.uniform(lo, hi) * scale


# -------------------------------------------------------------------- runner
class Runner:
    """Executes plans with pause/stop support.  Runs on a worker thread."""

    def __init__(self, backend, timing: Timing | None = None, on_status=None, on_progress=None,
                 sleep=time.sleep, time_scale: float = 1.0):
        self.backend = backend
        self.timing = timing or Timing()
        self.on_status = on_status or (lambda text: None)
        self.on_progress = on_progress or (lambda done, total: None)
        self.stop_event = threading.Event()
        self.run_event = threading.Event()
        self.run_event.set()
        self._sleep = sleep
        self.time_scale = time_scale        # 0 makes every wait instantaneous (tests)

    # -- control
    def stop(self):
        self.stop_event.set()
        self.run_event.set()

    def pause(self):
        self.run_event.clear()

    def resume(self):
        self.run_event.set()

    @property
    def paused(self) -> bool:
        return not self.run_event.is_set()

    def _checkpoint(self):
        while not self.run_event.wait(0.1):
            if self.stop_event.is_set():
                break
        if self.stop_event.is_set():
            raise Stopped()
        self.backend.check_failsafe()

    def wait(self, seconds: float):
        until = time.monotonic() + seconds * self.time_scale
        while True:
            self._checkpoint()
            left = until - time.monotonic()
            if left <= 0:
                return
            self._sleep(min(0.04, left))

    # -- sending
    def send_key(self, name: str, settle: float = 0.0):
        self._checkpoint()
        self.backend.key(name)
        if settle:
            self.wait(settle)

    def send_text(self, text: str, delay: float = 0.03):
        for ch in text:
            self._checkpoint()
            self.backend.type_char(ch)
            self.wait(delay)

    def run_plan(self, plan: Plan):
        total = plan.keystrokes
        done = 0
        previous = None
        for action in plan.actions:
            self._checkpoint()
            if isinstance(action, Note):
                self.on_status(action.text)
                continue
            if isinstance(action, Pause):
                self.wait(self.timing.pause(action.kind))
                continue
            if isinstance(action, Type):
                self.backend.type_char(action.ch)
            elif isinstance(action, Key):
                self.backend.key(action.name)
            elif isinstance(action, Paste):
                self.backend.set_clipboard(action.text)
                self.wait(0.05)
                self.backend.key("ctrl+v")
                self.wait(0.08)
            elif isinstance(action, GotoLine):
                self.backend.key("ctrl+g")
                self.wait(0.35)
                self.send_text(str(action.line), 0.06)
                self.wait(0.15)
                self.backend.key("enter")
                self.wait(0.3)
            done += 1
            if done % 5 == 0 or done == total:
                self.on_progress(done, total)
            self.wait(self.timing.keystroke(action, previous))
            previous = action

    # -- checking the real buffer
    def read_buffer(self) -> str | None:
        """Select all + copy in the focused editor; returns its text."""
        marker = "⁣ghostwriter-empty⁣"
        self.backend.set_clipboard(marker)
        self.wait(0.05)
        self.send_key("ctrl+a", 0.12)
        self.send_key("ctrl+c", 0.12)
        text = marker
        for _ in range(20):
            text = self.backend.get_clipboard()
            if text != marker:
                break
            self.wait(0.05)
        self.send_key("ctrl+end", 0.05)
        if text == marker:
            return None          # nothing was copied (empty buffer or copy blocked)
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def verify(self, expected: str, repair: bool = True) -> str:
        """Compare the editor buffer with ``expected``.

        Returns 'match', 'repaired', 'mismatch' or 'unreadable'.
        """
        actual = self.read_buffer()
        if actual is None:
            return "match" if expected == "" else "unreadable"
        if actual == expected:
            return "match"
        if not repair:
            return "mismatch"
        self.backend.set_clipboard(expected)
        self.wait(0.05)
        self.send_key("ctrl+a", 0.12)
        self.send_key("ctrl+v", 0.3)
        return "repaired" if self.read_buffer() == expected else "mismatch"


def first_difference(expected: str, actual: str) -> str:
    """Human-readable location of the first differing line."""
    a, b = expected.split("\n"), actual.split("\n")
    for n, (x, y) in enumerate(zip(a, b), start=1):
        if x != y:
            return f"line {n}: expected {x!r}, editor has {y!r}"
    if len(a) != len(b):
        return f"expected {len(a)} lines, editor has {len(b)}"
    return "no difference"
