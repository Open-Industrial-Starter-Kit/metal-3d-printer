"""
ui/facing.py
Facing toolpath generator -- the serpentine raster that skims a rectangle of
stock flat, emitted as a standalone G-code program.

Why this is native and not a CAM operation
------------------------------------------
Every other toolpath in this app comes out of the embedded CAM tools
(Kiri:Moto, EasyTrace -- see ``ui/pages/cam.py``), and for anything with a
model behind it that is the right place. Facing has no model: it is a
rectangle, a depth, and two step sizes. Asking the operator to build a stock
box and a clearing operation in a 3-D CAM tool to flatten a piece of MDF is
more ceremony than the job deserves, and the generated program is short enough
to read in full before running it.

So this module is a pure function of its inputs -- no machine state, no web
view, no files -- and ``ui/pages/facing_dialog.py`` collects the numbers while
``main_window`` stages the result and hands it to the existing upload queue.

The geometry
------------
Work zero is the **corner the operator chose**, with Z0 on top of the stock:
they jog to that corner, zero X/Y/Z there, and run. The stock then extends
away from zero in whichever direction that corner implies -- from *bottom left*
into +X/+Y, from *top right* into -X/-Y, and so on (``_CORNER_SIGNS``). This is
the same vocabulary Live Preview already uses for a job's origin ("bottom left
- top of stock"), so the corner named in the dialog is the corner named in the
preview.

Passes run either along X (*horizontal*) or along Y (*vertical*), stepping over
in the other axis. The cutter is extended by **half its diameter past all four
edges**: each pass overshoots its two ends by ``r``, and the first and last pass
centrelines sit *on* the stock edge with half the tool hanging off. Facing a
surface with the tool kept fully on the stock leaves up to ``r`` of material
standing at two of the edges, which defeats the point of the operation::

        centreline on the edge, so the cut reaches past it
              |
        +-----|---------------+
     <--+-----o---------------o--+-->   overshoot r at both ends
        |     .   S T O C K   .  |
     <--+-----o---------------o--+-->
        +-----|---------------+
              |
        nothing left standing at any edge

Step sizes are **redistributed, not truncated**. A 50 mm width with a 3 mm
stepover is cut as 17 passes of 2.941 mm rather than 16 of 3 mm and a ragged
1.5 mm remainder, and the same for depth levels against the stepdown. The
numbers typed are read as *maxima* -- the generator never exceeds them, and
never leaves a final pass that cuts a different amount from all the others,
because an uneven last pass is the one that chatters.

Levels alternate direction
--------------------------
After the last pass of a level the tool is at the far end of the raster. Rather
than retract, rapid back to the start corner and plunge again, the next level
simply plunges where it stands and runs the raster backwards (the point list is
reversed). The material below is untouched either way, so the plunge is the
same cut it would have been at the start corner, and a multi-level facing job
saves one full retract-and-return per level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

#: Corner names, in the vocabulary Live Preview uses for a job's origin.
CORNERS = ("bottom left", "bottom right", "top left", "top right")

#: Which way the stock extends from each corner, as (x_sign, y_sign). "bottom"
#: is -Y of the far edge, i.e. the stock runs toward +Y; "left" likewise runs
#: toward +X.
_CORNER_SIGNS = {
    "bottom left":  (+1.0, +1.0),
    "bottom right": (-1.0, +1.0),
    "top left":     (+1.0, -1.0),
    "top right":    (-1.0, -1.0),
}

#: Pass direction: along X, or along Y.
DIRECTIONS = ("horizontal", "vertical")

#: Rapid height above the stock, mm. Small on purpose -- a facing job is run on
#: a piece with nothing standing on it, and a tall retract between levels is
#: wasted travel.
SAFE_Z_MM = 5.0

#: Largest face this will generate, per axis, mm.
#:
#: The machine has 150 mm of travel and the cutter reaches half its diameter
#: past every edge, so a face near the full bed can only be cut if work zero
#: happens to sit exactly right -- and the operator sets that zero by hand, by
#: eye, at the corner of a piece of stock. 130 mm leaves 20 mm of travel to be
#: wrong by. Past that the limit is not reached at the start of the program,
#: where an alarm is merely annoying, but somewhere in the middle of a raster
#: with the tool down and the spindle running, which is the expensive kind.
MAX_FACE_MM = 130.0

#: The travel MAX_FACE_MM is reasoned against, quoted in the message explaining
#: it. Only ever used in that sentence -- the real check is against the work
#: area the caller passes in, which comes from the machine.
WORK_AREA_HINT_MM = 150.0

#: Most this will take off the top in one job, mm.
#:
#: Facing skims a surface flat. Taking off more than a couple of millimetres is
#: a roughing job, and asking a facing raster to do it means a full-width cut
#: over the whole face at every level, with the stock held by whatever is
#: clamping it -- on a machine this size that is where the work moves, not the
#: cutter.
#:
#: It is also the field a slipped decimal point does the most damage in. Every
#: other number here is wrong in a way the operator sees: a bad stepover leaves
#: ridges, a bad face size cuts air. "20" typed for "2" drives the cutter 20 mm
#: below the top of the stock and through whatever is underneath it, and the
#: program is perfectly valid -- nothing but a cap catches that.
MAX_DEPTH_MM = 2.0


@dataclass(frozen=True)
class FacingJob:
    """Everything the generator needs. Lengths in mm, feeds in mm/min."""

    width: float                 # stock X extent from the zero corner
    depth: float                 # stock Y extent from the zero corner
    total_depth: float           # how much to take off the top, overall
    stepdown: float              # max depth of cut per level
    stepover: float              # max spacing between pass centrelines
    tool_dia: float
    corner: str = "bottom left"
    direction: str = "horizontal"
    feed: float = 800.0
    plunge: float = 200.0
    rpm: int = 12000
    safe_z: float = SAFE_Z_MM


# ── step arithmetic ──────────────────────────────────────────────────────────

def _steps(span: float, max_step: float) -> int:
    """How many equal steps of at most *max_step* it takes to cover *span*.

    The epsilon matters: a 50 mm span at a 2.5 mm stepover is 20 steps, and
    without it binary floating point makes ``50 / 2.5`` come out a hair over 20
    often enough to add a redundant 21st pass of nearly zero width.
    """
    if span <= 1e-9 or max_step <= 0:
        return 0
    return max(1, math.ceil(span / max_step - 1e-9))


def _offsets(span: float, max_step: float) -> list[float]:
    """Pass centreline offsets across *span*, from 0 to *span* inclusive.

    Both ends are included, so the first and last pass sit on the two edges and
    the cutter hangs half off each -- see the module docstring. A zero span is
    one pass, which is what facing a line of stock would be.
    """
    n = _steps(span, max_step)
    if n == 0:
        return [0.0]
    return [span * i / n for i in range(n + 1)]


def _levels(job: FacingJob) -> list[float]:
    """Z of each depth level, deepest last, all negative (Z0 = top of stock)."""
    n = _steps(job.total_depth, job.stepdown)
    if n == 0:
        return []
    return [-job.total_depth * i / n for i in range(1, n + 1)]


def _level_points(job: FacingJob) -> list[tuple[float, float]]:
    """The (x, y) waypoints of one depth level, starting at the zero corner.

    Two points per pass -- its two ends -- which is all a straight pass needs.
    Consecutive passes are emitted in opposite directions, so the move from one
    pass's end to the next pass's start is a pure step-over along the cross
    axis and the raster never lifts or rapids inside a level.
    """
    r = job.tool_dia / 2.0
    sx, sy = _CORNER_SIGNS[job.corner]

    # "along" is the axis the passes run on, "cross" the one they step in.
    if job.direction == "horizontal":
        along_span, cross_span, a_sign, c_sign = job.width, job.depth, sx, sy
    else:
        along_span, cross_span, a_sign, c_sign = job.depth, job.width, sy, sx

    a0 = -a_sign * r                        # overshoot before the near edge
    a1 = a_sign * (along_span + r)          # and past the far one

    pts: list[tuple[float, float]] = []
    for i, off in enumerate(_offsets(cross_span, job.stepover)):
        cross = c_sign * off
        lo, hi = (a0, a1) if i % 2 == 0 else (a1, a0)
        pts.append((lo, cross))
        pts.append((hi, cross))

    if job.direction == "horizontal":
        return pts                                  # (along, cross) is (x, y)
    return [(cross, along) for along, cross in pts]  # (along, cross) is (y, x)


# ── validation ───────────────────────────────────────────────────────────────

def validate(job: FacingJob, work_area=None) -> list[str]:
    """Reasons *job* cannot be cut, in the operator's terms. Empty means fine.

    *work_area* is the machine's (width, depth) in mm when it should be checked
    against -- the swept area includes the half-tool overhang on all four
    sides, so the program needs ``tool_dia`` more travel than the face is wide.
    """
    problems: list[str] = []

    # First, because a NaN fails every comparison below rather than tripping
    # one: left to the ordinary checks it would be reported as a valid job and
    # written into a program. This module's output is run by a machine, so it
    # does not rely on its caller having screened the numbers.
    if not all(math.isfinite(v) for v in (
            job.width, job.depth, job.total_depth, job.stepdown, job.stepover,
            job.tool_dia, job.feed, job.plunge, job.rpm, job.safe_z)):
        return ["Every measurement must be a real number."]

    if job.tool_dia <= 0:
        problems.append("Tool diameter must be greater than 0.")
    face_ok = True
    if job.width <= 0 or job.depth <= 0:
        face_ok = False
        problems.append("The face needs an X and a Y size greater than 0.")
    # A micron of tolerance would reject 130.0001 and then print it, rounded,
    # as "this one is 130 x 130". Nothing on this machine resolves past a
    # micron anyway, so anything inside a thousandth of the cap is the cap.
    elif job.width > MAX_FACE_MM + 1e-3 or job.depth > MAX_FACE_MM + 1e-3:
        face_ok = False
        problems.append(
            f"The face can be at most {MAX_FACE_MM:.0f} \u00d7 {MAX_FACE_MM:.0f} mm "
            f"(this one is {job.width:g} \u00d7 {job.depth:g}). Larger leaves no "
            f"margin on {float(WORK_AREA_HINT_MM):.0f} mm of travel for where "
            "you set zero.")
    if job.total_depth <= 0:
        problems.append("Cut depth must be greater than 0 — "
                        "there would be nothing to cut.")
    elif job.total_depth > MAX_DEPTH_MM + 1e-3:
        problems.append(
            f"Cut depth can be at most {MAX_DEPTH_MM:g} mm (this one is "
            f"{job.total_depth:g}). Facing skims a surface flat; taking off "
            "more than that is a roughing job.")
    if job.stepdown <= 0:
        problems.append("Stepdown must be greater than 0.")
    if job.stepover <= 0:
        problems.append("Stepover must be greater than 0.")
    # Same thousandth of tolerance the two caps above use, so a stepover typed
    # as exactly the tool diameter is never rejected by a rounding artefact.
    elif job.tool_dia > 0 and job.stepover > job.tool_dia + 1e-3:
        problems.append(
            f"Stepover {job.stepover:g} mm is wider than the {job.tool_dia:g} mm "
            "tool \u2014 the passes would not touch. Use at most the tool "
            "diameter.")
    if job.feed <= 0 or job.plunge <= 0:
        problems.append("Feed and plunge rates must be greater than 0.")
    if job.rpm <= 0:
        problems.append("Spindle speed must be greater than 0.")
    if job.corner not in _CORNER_SIGNS:
        problems.append(f"Unknown start corner {job.corner!r}.")
    if job.direction not in DIRECTIONS:
        problems.append(f"Unknown pass direction {job.direction!r}.")

    # Only worth checking once the face itself is legal. An over-cap face also
    # sweeps past the travel, and saying both says the same thing twice -- in
    # a card that then needs two more lines to say it.
    if face_ok and work_area and job.tool_dia > 0:
        need_x = job.width + job.tool_dia
        need_y = job.depth + job.tool_dia
        if need_x > float(work_area[0]) + 1e-6 or need_y > float(work_area[1]) + 1e-6:
            problems.append(
                f"The cutter sweeps {need_x:.1f} \u00d7 {need_y:.1f} mm with the "
                f"tool's overhang, past the machine's "
                f"{float(work_area[0]):.0f} \u00d7 {float(work_area[1]):.0f} mm travel.")

    return problems


# ── summary, for the dialog to show before anything is sent ──────────────────

def summary(job: FacingJob) -> dict:
    """What this job will actually do, for the dialog's live preview.

    Distances are the real path length, measured off the same waypoints the
    program is written from, so the estimate cannot drift away from the file.
    """
    pts = _level_points(job)
    zs = _levels(job)
    n_levels = len(zs)
    n_passes = len(pts) // 2

    cut_mm = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    cut_mm *= n_levels
    plunge_mm = job.total_depth

    minutes = 0.0
    if job.feed > 0:
        minutes += cut_mm / job.feed
    if job.plunge > 0:
        minutes += plunge_mm / job.plunge

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    return {
        "passes": n_passes,
        "levels": n_levels,
        "stepover": (job.depth if job.direction == "horizontal" else job.width)
                    / max(1, n_passes - 1) if n_passes > 1 else 0.0,
        "stepdown": job.total_depth / n_levels if n_levels else 0.0,
        "cut_mm": cut_mm,
        "minutes": minutes,
        "extents": (min(xs), max(xs), min(ys), max(ys)),
        "z_min": zs[-1] if zs else 0.0,
    }


# ── emission ─────────────────────────────────────────────────────────────────

def _n(v: float) -> str:
    """A coordinate, at the 1 micron the machine can act on and no longer."""
    s = f"{_c(v):.3f}".rstrip("0").rstrip(".")
    return s or "0"


def _c(v: float) -> float:
    """*v* at micron resolution and without a negative zero.

    A face zeroed on its own top-right corner runs to exactly X0, and the
    sign that falls out of the arithmetic there is -0.0. Printed as
    "-0.000" in a header the operator is reading to check the job, that is
    a coordinate that looks wrong and is not.
    """
    return round(v, 3) + 0.0


def fmt_minutes(minutes: float) -> str:
    """A run time worth printing. Under a minute, say so rather than "0 min".

    Public because the dialog shows the same estimate it writes into the
    header, and the two must read identically.
    """
    if minutes < 1.0:
        return "under a minute"
    return f"about {minutes:.0f} min"


def header(job: FacingJob, when: datetime | None = None) -> list[str]:
    """The comment block at the top of the program.

    Follows ``ui/cam_gcode_header.py``: comments cost nothing because the
    firmware drops them before the program is transferred, and a program that
    says which cutter it was made for and how deep it goes is one the operator
    can check before running it.
    """
    s = summary(job)
    x0, x1, y0, y1 = s["extents"]
    pct = (job.stepover / job.tool_dia * 100.0) if job.tool_dia > 0 else 0.0
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return [
        "; Piccola CNC - facing",
        f"; created {stamp}",
        "; units mm, absolute (G21 G90)",
        f"; origin {job.corner} corner of the face, Z0 on top of the stock",
        f"; face X {job.width:.3f} Y {job.depth:.3f} - taking off {job.total_depth:.3f}",
        f"; T D={job.tool_dia:.3f} - extended {job.tool_dia / 2.0:.3f} past every edge",
        f"; {job.direction} passes - {s['passes']} x stepover {s['stepover']:.3f}"
        f" ({pct:.0f}% of D)",
        f"; {s['levels']} level(s) x stepdown {s['stepdown']:.3f}",
        f"; feed {job.feed:g}  plunge {job.plunge:g}  spindle {job.rpm:g}",
        f"; extents X {_c(x0):+.3f} .. {_c(x1):+.3f}"
        f"   Y {_c(y0):+.3f} .. {_c(y1):+.3f}"
        f"   Z {_c(s['z_min']):+.3f} .. {_c(job.safe_z):+.3f}",
        f"; ZMIN={_c(s['z_min']):.3f}",
        f"; cutting {s['cut_mm'] / 1000.0:.2f} m, {fmt_minutes(s['minutes'])}",
    ]


def gcode(job: FacingJob, when: datetime | None = None) -> str:
    """The whole facing program for *job*, ready to write out and upload.

    Raises ``ValueError`` if the job is not cuttable -- callers validate first
    and show the reasons, so reaching this is a bug rather than bad input.
    """
    problems = validate(job)
    if problems:
        raise ValueError("; ".join(problems))

    pts = _level_points(job)
    zs = _levels(job)

    out = header(job, when)
    out += [
        "G21",                                  # mm
        "G90",                                  # absolute
        f"M3 S{_n(job.rpm)}",                   # spindle on, up to speed
        f"G0 Z{_n(job.safe_z)}",                # clear the stock *before* XY
        f"G0 X{_n(pts[0][0])} Y{_n(pts[0][1])}",
    ]

    # Feed is stated only when it changes: every plunge sets it to the plunge
    # rate, so the first cutting move of each level restates the cutting feed
    # and the rest of the raster inherits it.
    feed_now: float | None = None
    for li, z in enumerate(zs):
        # Odd levels run the raster backwards, so the tool plunges where the
        # previous level left it instead of retracting and returning.
        level_pts = pts if li % 2 == 0 else list(reversed(pts))
        out.append(f"; level {li + 1}/{len(zs)}  Z{_n(z)}")
        out.append(f"G1 Z{_n(z)} F{_n(job.plunge)}")
        feed_now = job.plunge

        last = level_pts[0]
        for x, y in level_pts[1:]:
            words = []
            if abs(x - last[0]) > 1e-9:
                words.append(f"X{_n(x)}")
            if abs(y - last[1]) > 1e-9:
                words.append(f"Y{_n(y)}")
            if not words:
                continue
            line = "G1 " + " ".join(words)
            if feed_now != job.feed:
                line += f" F{_n(job.feed)}"
                feed_now = job.feed
            out.append(line)
            last = (x, y)

    out += [
        f"G0 Z{_n(job.safe_z)}",
        "M5",                                   # spindle off
        "M30",
    ]
    return "\n".join(out) + "\n"
