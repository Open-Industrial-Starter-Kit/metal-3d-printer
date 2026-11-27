"""
ui/gcode_bounds.py
Checks an exported program against the machine's travel before it is used.

Why
---
GRBL answers an unreachable move with an alarm and stops, mid-cut, with the
spindle down. By then the part is scrap and the operator is reading a number.
The program's extents are knowable long before that — they are in the file — so
the check belongs here, where there is still time to do something about it.

What can and cannot be known
----------------------------
A program is written in **work** coordinates, measured from wherever the
operator sets zero. The machine's travel is in **machine** coordinates. The
offset between them (G54) is decided at the machine, minutes after the file
leaves this app, and is not in the file. So "will this fit" cannot be answered
here in general.

Two things can:

**It cannot fit anywhere.** If the program is wider than the machine's travel,
no choice of zero saves it. That is a definite error and is reported as one.

**How much room each direction needs.** A program that runs to X -19 needs
19 mm of travel to the left of zero. That number is the one that matters and is
the one nobody has: Kiri's origin defaults to the *centre* of the stock, so a
program is routinely half negative in X and Y, and a machine homed to a corner
has very little room on that side. This is the common way to earn an alarm, and
it is why the reach is reported per direction rather than as one bounding box.

Rapids count
------------
Retracts and traverses are included. A rapid that leaves the envelope alarms
exactly like a cut that does, and the retract height is often the highest the
program ever goes.
"""

from __future__ import annotations

import os
import re

#: Words parsed out of a line. Only the axis words matter here; feed and
#: spindle are irrelevant to where the machine goes.
_WORD = re.compile(r"([XYZ])\s*(-?\d*\.?\d+)", re.I)

#: Motion codes that move the tool. G2/G3 arcs are included: their endpoints are
#: what this measures, which understates a bulging arc slightly but never
#: reports an in-range program as out of range.
_MOVES = ("G0", "G1", "G00", "G01", "G2", "G3", "G02", "G03")


class Bounds:
    """The reach of one program, in the work coordinates it is written in."""

    def __init__(self) -> None:
        self.lo = {"X": None, "Y": None, "Z": None}
        self.hi = {"X": None, "Y": None, "Z": None}
        self.moves = 0

    def add(self, axis: str, value: float) -> None:
        lo, hi = self.lo[axis], self.hi[axis]
        self.lo[axis] = value if lo is None else min(lo, value)
        self.hi[axis] = value if hi is None else max(hi, value)

    def span(self, axis: str) -> float:
        lo, hi = self.lo[axis], self.hi[axis]
        return 0.0 if lo is None else hi - lo

    def empty(self) -> bool:
        return self.moves == 0


def scan(path: str) -> Bounds:
    """Read *path* and return how far the program reaches on each axis.

    Modal and incremental-aware only as far as it needs to be: axis words are
    sticky (G90 absolute, which is what this app emits and pins), so an omitted
    word means "unchanged", not "zero".
    """
    b = Bounds()
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line[0] in ";(%":
                    continue
                head = line.split(None, 1)[0].upper()
                if head not in _MOVES:
                    continue
                for axis, raw in _WORD.findall(line):
                    try:
                        pos[axis.upper()] = float(raw)
                    except ValueError:
                        continue
                b.moves += 1
                for axis in ("X", "Y", "Z"):
                    b.add(axis, pos[axis])
    except OSError:
        return b
    return b


def check(path: str, work_area, max_z: float):
    """Compare *path* against a machine of *work_area* mm and *max_z* mm.

    Returns ``(fits, lines)``: whether the program can fit the machine at all,
    and the lines of a report worth showing either way. ``fits`` False means no
    choice of zero can save it.
    """
    b = scan(path)
    if b.empty():
        return True, []

    limits = {"X": float(work_area[0]), "Y": float(work_area[1]), "Z": float(max_z)}
    too_big = [a for a in ("X", "Y", "Z") if b.span(a) > limits[a] + 1e-6]

    lines = [f"{os.path.basename(path)} — reach from wherever you set zero:"]
    for axis in ("X", "Y", "Z"):
        lo, hi = b.lo[axis], b.hi[axis]
        # what the operator needs on each side of zero, which is the thing the
        # machine will run out of
        left = max(0.0, -lo)
        right = max(0.0, hi)
        note = f"{left:.1f} mm one way, {right:.1f} mm the other"
        flag = "  ** wider than the machine **" if axis in too_big else ""
        lines.append(
            f"   {axis}  {lo:+8.2f} .. {hi:+8.2f}   needs {note}"
            f"   (travel {limits[axis]:.0f} mm){flag}")
    return not too_big, lines


def negative_reach(path: str):
    """How far a program goes *below* zero on each axis, as ``{axis: mm}``.

    Split out because this is the number that produces an alarm on a machine
    homed to a corner, where there is nothing on the negative side to travel
    into. Zero means the program never goes that way.
    """
    b = scan(path)
    out = {}
    for axis in ("X", "Y", "Z"):
        lo = b.lo[axis]
        out[axis] = 0.0 if lo is None else max(0.0, -lo)
    return out
