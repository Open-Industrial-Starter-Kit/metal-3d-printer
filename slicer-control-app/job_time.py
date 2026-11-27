"""
job_time.py
How long a G-code file takes on this machine, worked out from the file, and a
log of how long jobs really took.

Why
---
The bottom bar's "Estimated remaining time" is the firmware's
(``estimatedRemainingSeconds``), and on rotary jobs it was far off: the GT2
pulley finish (``fixstep_pass_.nc``) showed 1 h 37 min left at 15%, about
1 h 54 min in all, for a file the machine moves through in 42 minutes. A
test of 300 deg of A at F1200 in 1, 300 and 3000 lines ran in 15 s each (17-18
s on the job-finished popup, which then still counted its 2 s end check), so
the machine keeps up with the G-code and the estimate is what is wrong.

The estimate here
-----------------
:func:`plan_lines` runs the file the way GRBL's planner does: every move at its
feed rate, capped by each axis's maximum rate ($110-$113, per minute) and
accelerating at each axis's limit ($120-$123, per second squared), with corner
speeds from junction deviation ($11) and full look-ahead -- so it accounts for
slowing into corners and short segments. A counts as a length in degrees, as
the controller does. G0 runs at the axes' maximum rates, G4 dwells are added,
arcs (G2/G3, XY plane) are their arc length. What it leaves out: spindle
spin-up, pauses, tool changes and the time between lines (measured: none worth
counting up to 200 lines a second).

It returns the time at the end of each line of the file, so the remaining time
at any line the machine reports (``currentFileLine``) is one subtraction.

The run log
-----------
:func:`log_run` appends one row per finished job to ``job-times.csv`` in the
app's data folder (``%LOCALAPPDATA%\\PiccolaCNC`` on Windows): start, end, file,
the real run time, this module's estimate and the machine's own (its first
estimate during the run plus the time already run then), so the three can be
compared job by job.
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
import time

#: GRBL settings used when the machine has not reported its own ($$).
DEFAULTS = {
    110: 1500.0, 111: 1500.0, 112: 1500.0, 113: 7200.0,   # max rate, /min
    120: 250.0, 121: 250.0, 122: 100.0, 123: 250.0,       # acceleration, /s^2
    11: 0.010,                                            # junction deviation, mm
}

_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")
_PAREN = re.compile(r"\([^)]*\)")
_AXES = "XYZA"

#: name -> seconds: this module's estimate of each file planned this session,
#: and the machine's estimate seen during its run -- read back by log_run().
_ESTIMATES: dict[str, float] = {}
_MACHINE: dict[str, float] = {}


def _limits(settings) -> tuple[list, list, float]:
    s = dict(DEFAULTS)
    for k, v in (settings or {}).items():
        try:
            k, v = int(k), float(v)
        except (TypeError, ValueError):
            continue
        if k in s and v > 0:
            s[k] = v
    rate = [s[110] / 60.0, s[111] / 60.0, s[112] / 60.0, s[113] / 60.0]   # per second
    acc = [s[120], s[121], s[122], s[123]]
    return rate, acc, s[11]


def plan_lines(lines, settings=None) -> list[float]:
    """Seconds from the start to the end of each line (index = line - 1).

    *lines* is any iterable of text lines; *settings* the machine's GRBL
    settings as ``{number: value}`` (missing ones fall back to DEFAULTS).
    """
    rate, acc, jd = _limits(settings)
    pos = [0.0, 0.0, 0.0, 0.0]
    absolute, inch, motion, feed = True, False, None, None
    segs = []            # (line index, length, speed, accel, unit vector)
    dwell = {}           # line index -> seconds
    n_lines = 0
    for idx, raw in enumerate(lines):
        n_lines = idx + 1
        text = _PAREN.sub("", raw.split(";", 1)[0]).upper()
        words = _WORD.findall(text)
        if not words:
            continue
        no_move = False
        dwell_p = None
        for k, v in words:
            if k != "G":
                continue
            g = float(v)
            if g in (0.0, 1.0, 2.0, 3.0):
                motion = int(g)
            elif g == 90.0:
                absolute = True
            elif g == 91.0:
                absolute = False
            elif g == 20.0:
                inch = True
            elif g == 21.0:
                inch = False
            elif g == 4.0:
                dwell_p = True
            elif g in (10.0, 28.0, 30.0, 53.0, 92.0) or math.floor(g) == 92.0:
                # offsets and machine-coordinate moves: their axis words are
                # not a move from here that this can follow
                no_move = True
        target = list(pos)
        moved = False
        ij = {}
        for k, v in words:
            val = float(v)
            if k == "F":
                feed = val * (25.4 if inch else 1.0)
            elif k in _AXES:
                i = _AXES.index(k)
                if inch and k != "A":
                    val *= 25.4
                target[i] = val if absolute else pos[i] + val
                moved = True
            elif k in ("I", "J"):
                ij[k] = val * (25.4 if inch else 1.0)
            elif k == "P" and dwell_p:
                dwell[idx] = dwell.get(idx, 0.0) + max(0.0, val)
        if no_move:
            continue           # not a move from here: keep the old position
        if not moved or motion is None:
            continue
        d = [target[i] - pos[i] for i in range(4)]
        chord = math.sqrt(sum(x * x for x in d))
        if chord < 1e-9:
            pos = target
            continue
        length = chord
        if motion in (2, 3) and ij:
            cx, cy = pos[0] + ij.get("I", 0.0), pos[1] + ij.get("J", 0.0)
            r = math.hypot(pos[0] - cx, pos[1] - cy)
            a0 = math.atan2(pos[1] - cy, pos[0] - cx)
            a1 = math.atan2(target[1] - cy, target[0] - cx)
            sweep = a1 - a0
            if motion == 2 and sweep >= 0:
                sweep -= 2 * math.pi
            elif motion == 3 and sweep <= 0:
                sweep += 2 * math.pi
            length = math.hypot(r * abs(sweep), math.hypot(d[2], d[3]))
        u = [x / chord for x in d]
        vmax = min(rate[i] / abs(u[i]) for i in range(4) if abs(u[i]) > 1e-12)
        if motion == 0 or not feed:
            v = vmax
        else:
            v = min(feed / 60.0, vmax)
        a = min(acc[i] / abs(u[i]) for i in range(4) if abs(u[i]) > 1e-12)
        segs.append((idx, length, v, a, u))
        pos = target

    n = len(segs)
    vj = [0.0] * (n + 1)          # speed at the start of each segment
    for i in range(1, n):
        u0, u1 = segs[i - 1][4], segs[i][4]
        c = -sum(x * y for x, y in zip(u0, u1))
        if c > 0.999999:
            vj[i] = 0.0                       # full reversal
        elif c < -0.999999:
            vj[i] = float("inf")              # straight on
        else:
            sh = math.sqrt(0.5 * (1.0 - c))
            vj[i] = math.sqrt(min(segs[i - 1][3], segs[i][3]) * jd * sh / (1.0 - sh))
        vj[i] = min(vj[i], segs[i - 1][2], segs[i][2])
    for i in range(n - 1, -1, -1):           # must be able to slow down in time
        vj[i] = min(vj[i], math.sqrt(vj[i + 1] ** 2 + 2 * segs[i][3] * segs[i][1]))
    for i in range(n):                       # and to speed up in time
        vj[i + 1] = min(vj[i + 1], math.sqrt(vj[i] ** 2 + 2 * segs[i][3] * segs[i][1]))

    per_line = [0.0] * n_lines
    for i, (idx, length, v, a, _u) in enumerate(segs):
        v0, v1 = vj[i], vj[i + 1]
        up, down = (v * v - v0 * v0) / (2 * a), (v * v - v1 * v1) / (2 * a)
        if up + down <= length:
            t = (v - v0) / a + (v - v1) / a + (length - up - down) / v
        else:
            peak = math.sqrt(max(0.0, (2 * a * length + v0 * v0 + v1 * v1) / 2))
            t = (peak - v0) / a + (peak - v1) / a
        per_line[idx] += t
    for idx, sec in dwell.items():
        per_line[idx] += sec
    total, cum = 0.0, []
    for t in per_line:
        total += t
        cum.append(total)
    return cum


def plan_file(path: str, settings=None) -> list[float]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        cum = plan_lines(fh, settings)
    _ESTIMATES[os.path.basename(path)] = cum[-1] if cum else 0.0
    return cum


def remaining(cum: list[float], line: int, override_pct: float = 100.0) -> float:
    """Seconds left after the machine reports *line* (1-based), at a feed
    override of *override_pct* (scales the whole remainder: rapids included,
    which is close enough on cutting jobs)."""
    if not cum:
        return 0.0
    i = min(max(int(line) - 1, 0), len(cum) - 1)
    left = cum[-1] - cum[i]
    return left * 100.0 / max(1.0, float(override_pct or 100.0))


def note_machine_estimate(name: str, total_seconds: float) -> None:
    """The machine's own estimate of the whole run, for the log."""
    _MACHINE[name] = float(total_seconds)


def _data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PiccolaCNC")
    return os.path.join(os.path.expanduser("~"), ".config", "piccola-cnc")


def log_path() -> str:
    return os.path.join(_data_dir(), "job-times.csv")


def log_run(name: str, seconds: float) -> None:
    """Append one finished job to job-times.csv. Never raises."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        end = time.time()
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
        mins = lambda s: "" if s is None else f"{s / 60.0:.1f}"
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["start", "end", "file", "real_min",
                            "app_estimate_min", "machine_estimate_min"])
            w.writerow([fmt(end - seconds), fmt(end), name, mins(seconds),
                        mins(_ESTIMATES.get(name)), mins(_MACHINE.pop(name, None))])
    except OSError:
        pass
