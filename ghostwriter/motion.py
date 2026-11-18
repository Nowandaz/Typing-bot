"""Human-looking timing and pointer motion.

``Rhythm`` produces the gaps between keystrokes and how long each key is held
down.  The model follows what is known about typing: intervals are log-normal
rather than bell-shaped, alternating hands is quicker than using the same
finger twice, shifted characters and symbols cost extra, words start a little
slowly, speed drifts over time, and a held key auto-repeats.

``mouse_path`` produces a curved, eased pointer path with a little tremor and
an occasional overshoot, the way a hand moves a mouse.
"""
from __future__ import annotations

import math
import random

# seconds: median gap between ordinary letters, and pause scale
PACES = {
    "Slow": (0.200, 1.3),
    "Natural": (0.135, 1.0),
    "Fast": (0.085, 0.65),
    "Very fast": (0.042, 0.3),
}
PAUSES = {           # median seconds at Natural pace
    "line": 0.45, "plan": 0.8, "think": 2.0, "notice": 0.55, "review": 2.0, "settle": 0.2,
}

_LEFT = "qwertasdfgzxcvb12345`~!@#$%"
_FINGERS = [("1qaz`~!", 0), ("2wsx@", 1), ("3edc#", 2), ("4rfv5tgb$%", 3),
            ("6yhn7ujm^&", 4), ("8ik,*<", 5), ("9ol.(>", 6), ("0p;/-['=])_{\"+}\\|:?", 7)]
_SHIFTED = set('~!@#$%^&*()_+{}|:"<>?')
_REPEATABLE = {"backspace", "delete", "left", "right", "up", "down",
               "shift+left", "shift+right", "shift+up", "shift+down"}


def _finger(ch: str):
    low = ch.lower()
    for keys, finger in _FINGERS:
        if low in keys:
            return finger
    return None


class Rhythm:
    def __init__(self, pace: str = "Natural", rng: random.Random | None = None,
                 think=(1.0, 3.5), line=(0.18, 0.65)):
        self.pace = pace if pace in PACES else "Natural"
        self.rng = rng or random.Random()
        self.base, self.scale = PACES[self.pace]
        self.think, self.line = think, line
        self._drift = 1.0

    def _lognormal(self, median: float, sigma: float) -> float:
        return median * math.exp(self.rng.gauss(0.0, sigma))

    def _step_drift(self) -> float:
        self._drift = 1.0 + 0.92 * (self._drift - 1.0) + self.rng.gauss(0.0, 0.025)
        self._drift = min(1.35, max(0.75, self._drift))
        return self._drift

    # -- characters
    def char_gap(self, ch: str, prev: str | None, word_ahead: int = 0) -> float:
        """Seconds to wait after the previous keystroke before typing ``ch``."""
        factor = 1.0
        if prev is not None and len(prev) == 1 and prev.isprintable() and prev != " ":
            if prev.lower() == ch.lower():
                factor *= 0.85
            else:
                f1, f2 = _finger(prev), _finger(ch)
                if f1 is not None and f2 is not None:
                    if f1 == f2:
                        factor *= 1.25                       # same finger, different key
                    elif (prev.lower() in _LEFT) != (ch.lower() in _LEFT):
                        factor *= 0.86                       # hands alternate
        if ch == " ":
            factor *= 0.9
        elif prev in (" ", None):
            factor *= 1.22                                   # starting a word
            if word_ahead > 9:
                factor *= 1.0 + min(1.2, (word_ahead - 9) * 0.12)   # recalling a long name
        if ch.isupper() or ch in _SHIFTED:
            factor *= 1.35
        elif ch.isdigit() or (not ch.isalnum() and ch != " "):
            factor *= 1.22
        gap = self._lognormal(self.base * factor * self._step_drift(), 0.27)
        if prev in (" ", None) and self.rng.random() < 0.02:
            gap += self.rng.uniform(0.3, 0.9) * self.scale    # a moment's hesitation
        return min(gap, 2.5)

    def hold(self, gap: float) -> float:
        """How long the key stays down."""
        dwell = self._lognormal(0.085 * (self.base / 0.135) ** 0.6, 0.22)
        return max(0.02, min(dwell, 0.18, 0.7 * gap))

    # -- keys
    def key_gaps(self, name: str, count: int) -> list[float]:
        """Gaps before each press in a run of ``count`` identical keys."""
        rng = self.rng
        if count >= 6 and name in _REPEATABLE:
            # the key is held: initial delay, then the keyboard repeat rate
            first = self._lognormal(0.16 * self.scale + 0.08, 0.2)
            return [first, rng.uniform(0.32, 0.5)] + [max(0.02, rng.gauss(0.034, 0.004)) for _ in range(count - 2)]
        tap = 0.9 * self.base + 0.03
        gaps = [self._lognormal(tap * (1.5 if i == 0 else 1.0), 0.22) for i in range(count)]
        if name in ("home", "end", "ctrl+home", "ctrl+end", "escape", "tab", "enter") or "+" in name:
            gaps[0] += rng.uniform(0.04, 0.16) * (self.scale + 0.4)
        return gaps

    # -- pauses
    def pause(self, kind: str) -> float:
        if kind == "think":
            lo, hi = self.think
            median, sigma = math.sqrt(max(lo, 0.01) * max(hi, 0.02)), 0.4
        elif kind == "line":
            lo, hi = self.line
            median, sigma = math.sqrt(max(lo, 0.01) * max(hi, 0.02)), 0.45
        else:
            median, sigma = PAUSES.get(kind, 0.4), 0.35
        value = self._lognormal(median, sigma) * self.scale
        if kind in ("think", "plan") and self.rng.random() < 0.06:
            value *= self.rng.uniform(1.8, 3.0)                # now and then a long think
        return min(value, 12.0)


# --------------------------------------------------------------------- mouse
def mouse_path(start, end, rng: random.Random, hz: int = 120, overshoot: bool = True):
    """Points (x, y, seconds_before) from ``start`` to ``end``."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 2:
        return [(ex, ey, 0.0)]
    points = []
    if overshoot and dist > 220 and rng.random() < 0.35:
        extra = rng.uniform(5, 16)
        mid = (ex + dx / dist * extra + rng.gauss(0, 2), ey + dy / dist * extra + rng.gauss(0, 2))
        points = mouse_path(start, mid, rng, hz, False)
        tail = mouse_path(mid, end, rng, hz, False)
        tail[0] = (tail[0][0], tail[0][1], tail[0][2] + rng.uniform(0.06, 0.14))
        return points + tail
    duration = (0.16 + 0.11 * math.log2(1 + dist / 30)) * rng.uniform(0.85, 1.3)
    steps = max(4, int(duration * hz))
    px, py = -dy / dist, dx / dist                           # unit normal
    bend1, bend2 = rng.gauss(0, 0.10 * dist), rng.gauss(0, 0.07 * dist)
    c1 = (sx + dx * 0.3 + px * bend1, sy + dy * 0.3 + py * bend1)
    c2 = (sx + dx * 0.72 + px * bend2, sy + dy * 0.72 + py * bend2)
    dt = duration / steps
    for i in range(1, steps + 1):
        t = i / steps
        s = 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5           # minimum-jerk easing
        a, b = 1 - s, s
        x = a ** 3 * sx + 3 * a * a * b * c1[0] + 3 * a * b * b * c2[0] + b ** 3 * ex
        y = a ** 3 * sy + 3 * a * a * b * c1[1] + 3 * a * b * b * c2[1] + b ** 3 * ey
        if i < steps:
            x += rng.gauss(0, 0.5)
            y += rng.gauss(0, 0.5)
        points.append((round(x), round(y), dt))
    points[-1] = (round(ex), round(ey), dt)
    if dist > 350 and len(points) > 6 and rng.random() < 0.2:
        k = rng.randint(len(points) // 3, 2 * len(points) // 3)   # a brief mid-flight hesitation
        x, y, pdt = points[k]
        points[k] = (x, y, pdt + rng.uniform(0.05, 0.14))
    return points


def hover_path(center, rng: random.Random, moves: int | None = None):
    """A hand resting on the mouse: a few small nearby positions with short
    holds between them.  Points are (x, y, seconds_before)."""
    cx, cy = center
    moves = moves if moves is not None else rng.randint(2, 4)
    out: list = []
    pos = (cx, cy)
    for _ in range(moves):
        target = (cx + rng.gauss(0, 6), cy + rng.gauss(0, 5))
        out.extend(mouse_path(pos, target, rng, overshoot=False))
        out.append((round(target[0]), round(target[1]), rng.uniform(0.2, 0.7)))   # the hand pauses
        pos = target
    return out
