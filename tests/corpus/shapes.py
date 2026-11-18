#!/usr/bin/env python3
"""Geometry helpers.

Used to exercise docstrings, decorators, nested brackets
and continuation lines.
"""
from __future__ import annotations

import math
import sys
from typing import Iterable

PI_ISH = 3.14159
COLOURS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
}
SIZES = [
    1, 2, 3,
    5, 8, 13,
]


class Shape:
    """Base class for all shapes."""

    sides = 0

    def area(self) -> float:
        raise NotImplementedError

    def describe(self) -> str:
        """Return a one-line description.

        The format is ``Name(area=1.23)``.
        """
        return f"{type(self).__name__}(area={self.area():.2f})"


class Circle(Shape):
    def __init__(self, radius: float = 1.0):
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius ** 2

    @property
    def diameter(self):
        return self.radius * 2

    @staticmethod
    def unit() -> "Circle":
        return Circle(1)


class Rect(Shape):
    sides = 4

    def __init__(self, width, height):
        self.width, self.height = width, height

    def area(self):
        return self.width * self.height

    def scale(self, factor):
        if factor <= 0:
            raise ValueError("factor must be positive")
        elif factor == 1:
            return self
        return Rect(
            self.width * factor,
            self.height * factor,
        )


def total_area(shapes: Iterable[Shape]) -> float:
    total = 0.0
    for shape in shapes:
        try:
            total += shape.area()
        except NotImplementedError:
            continue
        finally:
            pass
    return total


def classify(shape):
    match shape:
        case Circle(radius=r) if r > 10:
            return "big circle"
        case Rect():
            return "rect"
        case _:
            return 'unknown: "?"'


def parse(argv):
    names = [arg.lower() for arg in argv if not arg.startswith("-")]
    lookup = {name: COLOURS.get(name, (0, 0, 0)) for name in names}
    with open("log.txt", "a") as log:
        log.write(f"{len(lookup)} colours: {', '.join(lookup)}\n")
    long_condition = (len(names) > 3 and
                      names[0] != "none" and
                      PI_ISH < 4)
    while names and long_condition:
        names.pop()
    return lookup if lookup else None


if __name__ == "__main__":
    shapes = [Circle(2), Rect(3, 4), Circle.unit()]
    print(total_area(shapes), parse(sys.argv[1:]))
    sys.exit(0)
