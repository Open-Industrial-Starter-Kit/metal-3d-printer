"""
ui/job_fit.py
Before a job starts: does the program fit inside the machine's travel from
where the work zero is set now?

Why
---
A program is written in work coordinates; the machine's travel is in machine
coordinates; the work zero links them. When the zero is set so that some part
of the program falls outside the travel, the firmware's soft-limit check
refuses that move -- *during the job*, with the cutter in the material. Seen:
a rotary job that retracts to Z 15.6 (work), started with the Z zero set so
close to the top of travel that the spindle could not rise that far; it
stopped mid-cut with "Z cannot go higher".

Every number that decides this is known before pressing Start:

* the program's extents -- the ``;MINICNC_BOUNDS`` line the machine writes at
  the top of every uploaded file (MINX..MAXZ), or a scan of the local copy, or
  the firmware's ``getOpenedFileBounds`` reply (X/Y only);
* the machine's travel -- ``$130``..``$132``;
* which way each axis's travel runs from machine zero -- ``$23``: an axis whose
  homing-direction bit is set runs 0 .. +travel, every other 0 .. -travel.
  (Read against a live machine: X at +119 with bit 0 set, Y at -55 and Z at
  0.000 at the top, with theirs clear.);
* the current work-zero offset -- machine position minus work position, both
  on every status report.

:func:`check` puts them together and says, per axis, how far the program goes
past the end of travel and which way -- so the operator can move the zero
before the job instead of finding out in the middle of it.

The A axis is not checked: a rotary axis has no end of travel (``$133`` is set
effectively unlimited on purpose).
"""

from __future__ import annotations

import re

#: Axes checked, and the words for their + / - directions.
_AXES = {"X": ("right", "left"), "Y": ("back", "front"), "Z": ("up", "down")}

#: Rounding slack: the bounds line has three decimals.
TOL = 0.01

_BOUNDS_LINE = re.compile(r"MINICNC_BOUNDS")
_BOUNDS_WORD = re.compile(r"\b(MIN|MAX)([XYZ])=(-?\d+(?:\.\d+)?)")


def extents_from_file(path: str | None) -> dict:
    """``{axis: (lo, hi)}`` from a local copy of the program; {} if none.

    The ``;MINICNC_BOUNDS`` header is used when present -- it is the
    machine's own measurement, rapids included. Otherwise the program is
    scanned (ui/gcode_bounds.py), which counts every move the same way.
    """
    if not path:
        return {}
    try:
        with open(path, "r", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 60:
                    break
                if _BOUNDS_LINE.search(line):
                    vals = {}
                    for mm, axis, v in _BOUNDS_WORD.findall(line.upper()):
                        vals[(mm, axis)] = float(v)
                    out = {}
                    for axis in "XYZ":
                        if ("MIN", axis) in vals and ("MAX", axis) in vals:
                            out[axis] = (vals[("MIN", axis)], vals[("MAX", axis)])
                    if out:
                        return out
    except OSError:
        return {}
    try:
        from ui import gcode_bounds
        b = gcode_bounds.scan(path)
        if b.empty():
            return {}
        return {a: (b.lo[a], b.hi[a]) for a in "XYZ" if b.lo[a] is not None}
    except Exception:
        return {}


def extents_from_reply(p: dict | None) -> dict:
    """``{axis: (lo, hi)}`` from a ``getOpenedFileBounds`` reply."""
    out = {}
    for axis in "XYZ":
        try:
            out[axis] = (float(p[f"fileMin{axis}mm"]), float(p[f"fileMax{axis}mm"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def machine_ranges(grbl: dict) -> dict | None:
    """``{axis: (lo, hi)}`` of reachable machine coordinates, or None.

    None when ``$130``..``$132`` have not been read yet.
    """
    try:
        travel = {"X": float(grbl[130]), "Y": float(grbl[131]), "Z": float(grbl[132])}
    except (KeyError, TypeError, ValueError):
        return None
    mask = int(float(grbl.get(23, 0) or 0))
    out = {}
    for i, axis in enumerate("XYZ"):
        t = abs(travel[axis])
        out[axis] = (0.0, t) if mask & (1 << i) else (-t, 0.0)
    return out


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def check(extents: dict, position: dict, ranges: dict) -> list:
    """Every way the program leaves the travel, as plain sentences.

    *position* is a ``current_position()`` snapshot (``mx``/``jx``…). Empty
    when the program fits on every axis that could be checked.
    """
    problems = []
    for axis, (pos_word, neg_word) in _AXES.items():
        if axis not in extents or axis not in ranges:
            continue
        lo, hi = extents[axis]
        mlo, mhi = ranges[axis]
        k = axis.lower()
        try:
            off = float(position["m" + k]) - float(position["j" + k])
        except (KeyError, TypeError, ValueError):
            continue
        # the window of WORK coordinates the machine can actually reach
        wlo, whi = mlo - off, mhi - off
        if hi > whi + TOL:
            short = hi - whi
            fix = ("Set the Z zero lower, or lower the clearance (retract) "
                   "height in the CAM." if axis == "Z" else
                   f"Move the {axis} zero further {neg_word}, or the part.")
            problems.append(
                f"{axis} goes to {_fmt(hi)} but can only reach {_fmt(whi)} "
                f"from this zero: {_fmt(short)} mm too far {pos_word}. {fix}")
        if lo < wlo - TOL:
            short = wlo - lo
            fix = ("The job cuts deeper than Z can travel from here: set the "
                   "Z zero higher, raise the part, or use a longer tool."
                   if axis == "Z" else
                   f"Move the {axis} zero further {pos_word}, or the part.")
            problems.append(
                f"{axis} goes to {_fmt(lo)} but can only reach {_fmt(wlo)} "
                f"from this zero: {_fmt(short)} mm too far {neg_word}. {fix}")
    return problems
