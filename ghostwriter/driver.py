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

import math
import os
import random
import sys
import threading
import time

from .actions import GotoLine, Key, Note, Paste, Pause, Plan, Type
from .motion import PACES, Rhythm, hover_path, mouse_path  # noqa: F401  (PACES re-exported for the UI)


class EmergencyStop(Exception):
    """Raised when the mouse is moved to the top-left corner of the screen."""


class Stopped(Exception):
    pass


class NotFocused(Exception):
    """GhostWriter's own window still had focus when typing was due to start."""


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

        self.INPUT, self.KEYBDINPUT, self.MOUSEINPUT = INPUT, KEYBDINPUT, MOUSEINPUT
        self.POINT, self.RECT, self.DWORD = wintypes.POINT, wintypes.RECT, wintypes.DWORD
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self.user32.MapVirtualKeyW.restype = wintypes.UINT
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        self.user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))

    supports_hold = True

    def prepare_thread(self):
        """Use real pixels on this thread so window and pointer coordinates agree."""
        try:
            self.user32.SetThreadDpiAwarenessContext(self.ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass

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

    def type_char(self, ch, hold=0.0):
        data = ch.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        for unit in units:                                    # KEYEVENTF_UNICODE key down
            self._send((0, unit, 0x0004))
        if hold:
            time.sleep(hold)
        for unit in units:
            self._send((0, unit, 0x0004 | 0x0002))

    def key(self, name, hold=0.0):
        parts = name.split("+")
        mods, main = parts[:-1], parts[-1]
        try:
            for m in mods:
                self._send(self._vk_event(m))
            if mods:
                time.sleep(0.02 if hold else 0.005)
            self._send(self._vk_event(main))
            if hold:
                time.sleep(hold)
            self._send(self._vk_event(main, up=True))
        finally:
            for m in reversed(mods):                          # never leave a modifier stuck down
                self._send(self._vk_event(m, up=True))

    # -- focus
    def foreground(self):
        return self.user32.GetForegroundWindow()

    def is_own_window(self, hwnd) -> bool:
        pid = self.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return pid.value == os.getpid()

    def window_rect(self, hwnd=None):
        rect = self.RECT()
        if not self.user32.GetWindowRect(hwnd or self.foreground(), self.ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    # -- pointer (moves and wheel only; GhostWriter never clicks)
    def mouse_pos(self):
        point = self.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(point))
        return point.x, point.y

    def mouse_move(self, x, y):
        self.user32.SetCursorPos(int(x), int(y))

    def scroll(self, notches):
        item = self.INPUT()
        item.type = 0  # INPUT_MOUSE
        item.mi = self.MOUSEINPUT(0, 0, (int(notches) * 120) & 0xFFFFFFFF, 0x0800, 0, 0)   # MOUSEEVENTF_WHEEL
        self.user32.SendInput(1, self.ctypes.byref(item), self.ctypes.sizeof(self.INPUT))

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

    def mouse_pos(self):
        return tuple(self.gui.position())

    def mouse_move(self, x, y):
        self.gui.moveTo(int(x), int(y), _pause=False)

    def scroll(self, notches):
        self.gui.scroll(int(notches))

    def window_rect(self, hwnd=None):
        width, height = self.gui.size()
        return 0, 0, width, height


def create_backend(kind: str = "auto"):
    if kind == "sendinput" or (kind == "auto" and sys.platform == "win32"):
        return SendInputBackend()
    return PyAutoGuiBackend()


# -------------------------------------------------------------------- timing
class Timing(Rhythm):
    """Keystroke rhythm configured from config.json values."""

    def __init__(self, pace: str = "Natural", think_min: float = 1.0, think_max: float = 3.5,
                 line_min: float = 0.18, line_max: float = 0.65, rng: random.Random | None = None):
        super().__init__(pace, rng, think=(think_min, think_max), line=(line_min, line_max))


# -------------------------------------------------------------------- runner
class Runner:
    """Executes plans with pause/stop support.  Runs on a worker thread."""

    def __init__(self, backend, timing: Timing | None = None, on_status=None, on_progress=None,
                 sleep=time.sleep, time_scale: float = 1.0, mouse: bool = False):
        self.backend = backend
        self.timing = timing or Timing()
        self.on_status = on_status or (lambda text: None)
        self.on_progress = on_progress or (lambda done, total: None)
        self.stop_event = threading.Event()
        self.run_event = threading.Event()
        self.run_event.set()
        self._sleep = sleep
        self.time_scale = time_scale        # 0 makes every wait instantaneous (tests)
        self.mouse = mouse                  # move / scroll the pointer during pauses
        self.focus_target = None            # window that must stay in the foreground
        self._last_status = ""

    # -- control
    def stop(self):
        self.stop_event.set()
        self.run_event.set()

    def pause(self):
        self.run_event.clear()

    def resume(self):
        self.run_event.set()

    def toggle(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    @property
    def paused(self) -> bool:
        return not self.run_event.is_set()

    def status(self, text: str):
        self._last_status = text
        self.on_status(text)

    def lock_focus(self):
        """Remember the window that has focus now; typing waits whenever
        another window comes to the front."""
        prepare = getattr(self.backend, "prepare_thread", None)
        if prepare:
            prepare()
        foreground = getattr(self.backend, "foreground", None)
        if foreground is None:
            return
        hwnd = foreground()
        if hwnd and self.backend.is_own_window(hwnd):
            raise NotFocused()
        self.focus_target = hwnd or None

    def _checkpoint(self):
        while not self.run_event.wait(0.1):
            if self.stop_event.is_set():
                break
        if self.stop_event.is_set():
            raise Stopped()
        self.backend.check_failsafe()
        if self.focus_target is not None and self.backend.foreground() != self.focus_target:
            previous = self._last_status
            self.on_status("Waiting: VS Code is not the active window")
            while self.backend.foreground() != self.focus_target:
                if self.stop_event.is_set():
                    raise Stopped()
                self.backend.check_failsafe()
                self._sleep(0.1 if self.time_scale else 0)
            self._sleep(0.8 * self.time_scale)          # let the window settle before typing on
            self.on_status(previous or "Typing")

    def wait(self, seconds: float):
        until = time.monotonic() + seconds * self.time_scale
        while True:
            self._checkpoint()
            left = until - time.monotonic()
            if left <= 0:
                return
            self._sleep(min(0.04, left))

    # -- sending
    def _press(self, name: str, hold: float = 0.0):
        if hold and getattr(self.backend, "supports_hold", False):
            self.backend.key(name, hold * self.time_scale)
        else:
            self.backend.key(name)

    def _char(self, ch: str, hold: float = 0.0):
        if hold and getattr(self.backend, "supports_hold", False):
            self.backend.type_char(ch, hold * self.time_scale)
        else:
            self.backend.type_char(ch)

    def send_key(self, name: str, settle: float = 0.0):
        self._checkpoint()
        self._press(name, self.timing.hold(0.2))
        if settle:
            self.wait(settle)

    def send_text(self, text: str, delay: float | None = None):
        """Type text into a prompt (Quick Open, Go to Line) at typing speed."""
        prev = None
        for ch in text:
            gap = self.timing.char_gap(ch, prev) if delay is None else delay
            self.wait(gap)
            self._char(ch, self.timing.hold(gap))
            prev = ch

    def run_plan(self, plan: Plan):
        actions = plan.actions
        total = plan.keystrokes
        done = 0
        prev_char = None            # last typed character (None after any other key)
        run_gaps: list[float] = []  # gaps left for the current run of identical keys
        run_name = None
        for index, action in enumerate(actions):
            self._checkpoint()
            if isinstance(action, Note):
                self.status(action.text)
                continue
            if isinstance(action, Pause):
                length = self.timing.pause(action.kind)
                spent = self._pointer_gesture(action.kind) if self.mouse else 0.0
                self.wait(max(0.0, length - spent))
                prev_char, run_name = None, None
                continue
            if isinstance(action, Type):
                ahead = 0
                if prev_char in (" ", None):
                    while index + ahead < len(actions) and isinstance(actions[index + ahead], Type) \
                            and (actions[index + ahead].ch.isalnum() or actions[index + ahead].ch == "_"):
                        ahead += 1
                gap = self.timing.char_gap(action.ch, prev_char, ahead)
                self.wait(gap)
                self._char(action.ch, self.timing.hold(gap))
                prev_char, run_name = action.ch, None
            elif isinstance(action, Key):
                if action.name != run_name or not run_gaps:
                    count = 1
                    while index + count < len(actions) and actions[index + count] == action:
                        count += 1
                    run_gaps = self.timing.key_gaps(action.name, count)
                    run_name = action.name
                gap = run_gaps.pop(0)
                self.wait(gap)
                self._press(action.name, self.timing.hold(gap))
                prev_char = None
            elif isinstance(action, Paste):
                self.backend.set_clipboard(action.text)
                self.wait(0.05)
                self._press("ctrl+v")
                self.wait(0.08)
                prev_char, run_name = None, None
            elif isinstance(action, GotoLine):
                self.wait(self.timing.key_gaps("ctrl+g", 1)[0])
                self._press("ctrl+g", self.timing.hold(0.2))
                self.wait(0.4)
                self.send_text(str(action.line))
                self.wait(0.2)
                self._press("enter", self.timing.hold(0.2))
                self.wait(0.3)
                prev_char, run_name = None, None
            done += 1
            if done % 5 == 0 or done == total:
                self.on_progress(done, total)

    # -- pointer
    def _follow_path(self, points) -> bool:
        """Drive the pointer through a path.  Gives up (returns False) the
        moment the person moves the mouse themselves."""
        backend = self.backend
        last = backend.mouse_pos()
        for px, py, dt in points:
            self.wait(dt)
            now = backend.mouse_pos()
            if math.hypot(now[0] - last[0], now[1] - last[1]) > 6:
                return False
            backend.mouse_move(px, py)
            last = (px, py)
        return True

    def _glide(self, x: float, y: float) -> bool:
        """Move the pointer to (x, y) along a hand-like curved path."""
        return self._follow_path(mouse_path(self.backend.mouse_pos(), (x, y), self.timing.rng))

    def _hover(self, center) -> bool:
        """Small idle movements, as if a hand is resting on the mouse."""
        return self._follow_path(hover_path(center, self.timing.rng))

    # How often a pause produces any pointer movement, and which gestures it
    # draws from.  Movement clusters around thinking and reviewing; during a
    # run of keystrokes the pointer stays put, which is what a person does.
    GESTURE_CHANCE = {"review": 0.85, "think": 0.5, "plan": 0.3, "notice": 0.12, "line": 0.05}
    GESTURE_MIX = {
        "review": {"read": 70, "follow": 20, "hover": 10},
        "think": {"hover": 35, "drift": 25, "read": 15, "aside": 15, "follow": 10},
        "plan": {"drift": 40, "aside": 30, "hover": 20, "follow": 10},
        "notice": {"nudge": 65, "hover": 35},
        "line": {"nudge": 100},
    }

    def _pointer_gesture(self, kind: str) -> float:
        """Make one occasional, purposeful pointer movement during a pause.

        A person rests a hand on the mouse, drifts it, follows the code, moves
        it aside to type, or scrolls back to re-read.  Only moves and the
        wheel are used - never a click, which would move the text caret.
        Returns the real seconds spent, so the gesture fits inside the pause
        rather than lengthening the run.
        """
        backend, rng = self.backend, self.timing.rng
        if not hasattr(backend, "mouse_move"):
            return 0.0
        if rng.random() >= self.GESTURE_CHANCE.get(kind, 0.0):
            return 0.0
        rect = backend.window_rect(self.focus_target) if self.focus_target else backend.window_rect()
        if not rect:
            return 0.0
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width < 300 or height < 200:
            return 0.0
        started = time.monotonic()
        x0, y0 = backend.mouse_pos()
        lo_x, hi_x = left + 0.40 * width, right - 0.10 * width    # editor text column, clear of the side bars
        lo_y, hi_y = top + 0.22 * height, bottom - 0.22 * height
        caret_y = top + 0.62 * height                            # where the caret tends to sit
        inside = lo_x <= x0 <= hi_x and lo_y <= y0 <= hi_y

        def clamp(x, y):
            return min(hi_x, max(lo_x, x)), min(hi_y, max(lo_y, y))

        mix = self.GESTURE_MIX.get(kind, {"nudge": 100})
        gesture = rng.choices(list(mix), weights=list(mix.values()))[0]

        if gesture == "read":
            if not inside:
                self._glide(*clamp(rng.uniform(lo_x, hi_x), caret_y + rng.gauss(0, 40)))
            self.wait(rng.uniform(0.1, 0.35))
            notches = rng.randint(2, 5)
            for _ in range(notches):
                backend.scroll(1)                                # wheel up: glance back at earlier lines
                self.wait(rng.uniform(0.06, 0.16))
            self.wait(rng.uniform(0.5, 1.4) * self.timing.scale)
            for _ in range(notches):
                backend.scroll(-1)                               # and back down to the caret
                self.wait(rng.uniform(0.06, 0.16))
        elif gesture == "follow":
            self._glide(*clamp(rng.uniform(lo_x + 0.15 * width, hi_x), caret_y + rng.gauss(0, 55)))
        elif gesture == "drift":
            self._glide(*clamp(rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y)))
        elif gesture == "aside":
            self._glide(*clamp(hi_x - rng.uniform(0, 0.08 * width), rng.uniform(lo_y, hi_y)))
        elif gesture == "hover":
            center = (x0, y0) if inside else clamp(rng.uniform(lo_x, hi_x), caret_y)
            self._hover(center)
        else:  # nudge: the hand shifts the mouse a little without going anywhere
            self._glide(*clamp(x0 + rng.gauss(0, 7), y0 + rng.gauss(0, 6)))
        return (time.monotonic() - started) / self.time_scale if self.time_scale else 0.0

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
