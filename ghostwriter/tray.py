"""Notification-area (system tray) icon and global hotkeys.

While GhostWriter types, its window is hidden so it is not in the way (or in
the recording).  The tray icon shows progress and offers Pause / Stop / Show
window; the hotkeys do the same without taking focus away from VS Code.
"""
from __future__ import annotations

import sys
import threading


class Tray:
    """Wraps ``pystray``.  ``available`` is False when it is not installed."""

    def __init__(self, on_command):
        self.on_command = on_command        # called with "toggle" | "stop" | "show" from the tray thread
        self.icon = None
        self.status = "GhostWriter"
        self.paused = False
        try:
            import pystray
            from PIL import Image, ImageDraw
            self._pystray, self._image, self._draw = pystray, Image, ImageDraw
            self.available = True
        except Exception:                   # missing package or no usable desktop backend
            self.available = False

    def _picture(self):
        image = self._image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = self._draw.Draw(image)
        draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(23, 25, 31, 255))
        draw.rectangle((16, 18, 40, 23), fill=(82, 127, 156, 255))      # lines of "code"
        draw.rectangle((16, 29, 48, 34), fill=(228, 230, 235, 255))
        draw.rectangle((16, 40, 30, 45), fill=(228, 230, 235, 255))
        draw.rectangle((34, 38, 38, 48), fill=(82, 127, 156, 255))      # the caret
        return image

    def start(self) -> bool:
        if not self.available or self.icon is not None:
            return self.icon is not None
        Item, Menu = self._pystray.MenuItem, self._pystray.Menu
        menu = Menu(
            Item(lambda item: self.status[:60], None, enabled=False),
            Menu.SEPARATOR,
            Item(lambda item: "Resume" if self.paused else "Pause", lambda: self.on_command("toggle")),
            Item("Stop", lambda: self.on_command("stop")),
            Item("Show window", lambda: self.on_command("show"), default=True),
        )
        try:
            self.icon = self._pystray.Icon("ghostwriter", self._picture(), "GhostWriter", menu)
            self.icon.run_detached()
        except Exception:
            self.icon = None
            return False
        return True

    def update(self, status: str | None = None, paused: bool | None = None):
        if status is not None:
            self.status = status
        if paused is not None:
            self.paused = paused
        if self.icon is None:
            return
        try:
            self.icon.title = ("GhostWriter - " + self.status)[:63]
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, message: str):
        if self.icon is None:
            return
        try:
            self.icon.notify(message, "GhostWriter")
        except Exception:
            pass

    def stop(self):
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass


_VK = {f"f{n}": 0x70 + n - 1 for n in range(1, 13)}
_VK.update({"pause": 0x13, "scrolllock": 0x91})


class Hotkeys:
    """Global hotkeys (Windows ``RegisterHotKey``).  Single keys, no
    modifiers, so nothing is held down while GhostWriter is typing."""

    def __init__(self):
        self.thread = None
        self.thread_id = None
        self.failed: list[str] = []

    def start(self, bindings: dict) -> bool:
        """bindings: key name (e.g. 'f9') -> callable."""
        bindings = {k.lower(): v for k, v in bindings.items() if k and k.lower() in _VK}
        if sys.platform != "win32" or not bindings or self.thread is not None:
            return False
        ready = threading.Event()

        def loop():
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.thread_id = ctypes.WinDLL("kernel32").GetCurrentThreadId()
            ids = {}
            for number, (name, callback) in enumerate(bindings.items(), start=1):
                if user32.RegisterHotKey(None, number, 0x4000, _VK[name]):      # MOD_NOREPEAT
                    ids[number] = callback
                else:
                    self.failed.append(name)
            ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam in ids:         # WM_HOTKEY
                    try:
                        ids[message.wParam]()
                    except Exception:
                        pass
            for number in ids:
                user32.UnregisterHotKey(None, number)

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()
        ready.wait(2.0)
        return True

    def stop(self):
        if self.thread is None:
            return
        try:
            import ctypes
            ctypes.WinDLL("user32").PostThreadMessageW(self.thread_id, 0x0012, 0, 0)   # WM_QUIT
        except Exception:
            pass
        self.thread = None
