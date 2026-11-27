"""
ui/job_summary.py
What a G-code file says about itself: the tools it uses, how deep it cuts, and
whether a tool is too short -- read from the header our CAM export writes.

Why
---
Live Preview draws the toolpath but said nothing about what the job needs at
the machine. The two facts an operator checks before pressing start are which
tool goes in the spindle and how far below zero the job goes; both are already
written at the top of every program the CAM tab exports
(``ui/cam_gcode_header.py``)::

    ; T5 D=3.000 flute 8.000 - ZMIN=-16.660 - flat end mill "1 flute 3mm"
    ; WARNING tool length: T5 "1 flute 3mm" cuts 16.660 deep, flute only 8.000 ...
    ; ZMIN=-16.660

so this reads them back rather than working anything out a second time -- one
source for the numbers, so the preview and the file cannot disagree.

A file from elsewhere (another CAM package, an older export) has no such
header. Then there is no tool to name and :func:`read` says so with an empty
``tools`` list rather than guessing; the minimum Z still comes from the
toolpath itself, which Live Preview has already measured.
"""

from __future__ import annotations

import re

#: Header lines are at the top; nothing past this is read.
HEADER_LINES = 400

#: ``; T5 D=3.000 flute 8.000 - ZMIN=-16.660 - flat end mill "1 flute 3mm"``
#: Every field after the number is optional: older headers had fewer.
_TOOL = re.compile(
    r"^;\s*T(?P<num>\d+)"
    r"(?:\s+D=(?P<diam>[-\d.]+))?"
    r"(?:\s+flute\s+(?P<flute>[-\d.]+))?"
    r"(?:\s+tip\s+[-\d.]+)?"
    r"(?:\s*-\s*ZMIN=(?P<zmin>[-\d.]+))?"
    r"(?:\s*-\s*(?P<kind>[^\"]+?))?"
    r"(?:\s+\"(?P<name>[^\"]*)\")?\s*$")

#: ``; origin X 0.500 Y 0.500 Z 1.000 - center, top of stock`` -- where work
#: zero sits on the stock, as fractions of the box (ui/cam_gcode_header.py).
_ORIGIN = re.compile(r"^;\s*origin\s+X\s+(?P<x>[-\d.]+)\s+Y\s+(?P<y>[-\d.]+)"
                     r"\s+Z\s+(?P<z>[-\d.]+)")

#: ``; ZMIN=-16.660`` -- the program's overall lowest point.
_ZMIN = re.compile(r"^;\s*ZMIN=(?P<z>[-\d.]+)\s*$")

#: ``; rotary: Z 0 on the rotary axis, Z = radius - stock radius 15.517`` or
#: ``; rotary: Z 0 on top of the bar, Z = radius - 15.517 - stock radius
#: 15.517`` -- a Rotary Mode job: Z counts from the axis or from the top of
#: the bar, and the stock reaches this far out from the axis. Also read in
#: its ``(rotary: …)`` form, the copy of that line the machine keeps when it
#: drops the ``;`` header (ui/cam_gcode_header.py).
_ROTARY = re.compile(r"^[;(]\s*rotary:\s*Z 0 on (?P<where>the rotary axis|top of the bar)"
                     r".*stock radius\s+(?P<r>[-\d.]+)")

#: Must match ``cam_gcode_header.LENGTH_WARNING``; imported lazily below so
#: this module stays free of the CAM page's imports.
_WARN_PREFIX = "; WARNING tool length:"


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def read(path: str) -> dict:
    """Read the header of *path*.

    Returns ``{"tools": [...], "zmin": float | None, "warnings": {num: text}}``
    where each tool is ``{"number", "diam", "flute", "zmin", "kind", "name"}``
    (missing fields are None) and ``warnings`` maps a tool number to the
    header's tool-length warning for it. ``rotary`` is the stock radius of a
    Rotary Mode job, else None, and ``rotary_top`` whether that job's Z 0 is
    the top of the bar (True) or the rotary axis (False).
    """
    out = {"tools": [], "zmin": None, "warnings": {}, "origin": None,
           "rotary": None, "rotary_top": False}
    try:
        from ui.cam_gcode_header import LENGTH_WARNING as prefix
    except Exception:
        prefix = _WARN_PREFIX
    try:
        with open(path, "r", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= HEADER_LINES:
                    break
                line = line.strip()
                if line.startswith("("):
                    # only the rotary line comes in this form
                    m = _ROTARY.match(line)
                    if m:
                        out["rotary"] = _num(m.group("r"))
                        out["rotary_top"] = m.group("where") == "top of the bar"
                    continue
                if not line.startswith(";"):
                    continue
                if line.startswith(prefix):
                    text = line[len(prefix):].strip()
                    m = re.match(r"T(\d+)", text)
                    if m:
                        out["warnings"][int(m.group(1))] = text
                    continue
                m = _ORIGIN.match(line)
                if m:
                    fx, fy, fz = (_num(m.group(k)) for k in ("x", "y", "z"))
                    if None not in (fx, fy, fz):
                        out["origin"] = (fx, fy, fz)
                    continue
                m = _ZMIN.match(line)
                if m:
                    out["zmin"] = _num(m.group("z"))
                    continue
                m = _ROTARY.match(line)
                if m:
                    out["rotary"] = _num(m.group("r"))
                    out["rotary_top"] = m.group("where") == "top of the bar"
                    continue
                m = _TOOL.match(line)
                if m:
                    out["tools"].append({
                        "number": int(m.group("num")),
                        "diam": _num(m.group("diam")),
                        "flute": _num(m.group("flute")),
                        "zmin": _num(m.group("zmin")),
                        "kind": (m.group("kind") or "").strip() or None,
                        "name": m.group("name"),
                    })
    except OSError:
        pass
    return out


def _fmt(v: float) -> str:
    """Three decimals trimmed: 3.000 -> 3, 16.660 -> 16.66."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def tool_text(tool: dict) -> str:
    """One tool as a chip label: ``T5  Ø3 flat end mill  ·  flute 8``."""
    bits = [f"T{tool['number']}"]
    if tool.get("diam"):
        bits.append(f"Ø{_fmt(tool['diam'])}")
    if tool.get("kind"):
        bits.append(tool["kind"])
    head = " ".join(bits)
    if tool.get("flute"):
        head += f"  ·  flute {_fmt(tool['flute'])}"
    return head


def origin_from_cut(cut, tools=()) -> tuple[float, float, float] | None:
    """Estimate work zero from where a program cuts, for a file whose header
    does not say (Fusion, other CAM software, older exports).

    *cut* is ``(min_x, min_y, min_z, max_x, max_y, max_z)`` over the feed
    moves. Per axis, zero at or just outside one end of that range puts the
    origin on that edge, near its middle the centre, and anywhere else a
    fraction of the way across. "Just outside" allows for the tool running
    past the stock edge -- a facing pass overhangs by about its radius -- so
    the slack is the largest header tool's radius plus 1 mm, or 8% of the
    range when that is more. Z has no stock height to go by: a program that
    cuts below zero is taken as zeroed on the top face, one that stays at or
    above it on the bottom.

    Returns ``(fx, fy, fz)`` in :func:`origin_text`'s terms, or None when the
    cut has no extent in X or Y.
    """
    radius = max((t["diam"] for t in tools if t.get("diam")), default=4) / 2

    def frac(lo, hi):
        span = hi - lo
        if span <= 1e-6:
            return None
        slack = max(0.08 * span, radius + 1.0)
        if lo >= -slack:
            return 0.0
        if hi <= slack:
            return 1.0
        if abs(lo + hi) / 2 <= slack:
            return 0.5
        return -lo / span

    fx, fy = frac(cut[0], cut[3]), frac(cut[1], cut[4])
    if fx is None or fy is None:
        return None
    return fx, fy, (1.0 if cut[2] < -0.01 else 0.0)


def origin_text(origin, rotary=None, rotary_top=False) -> tuple[str, str]:
    """Work zero in words: ``("bottom left", "top of stock")``.

    *origin* is ``(fx, fy, fz)``, fractions of the stock box seen from above:
    x 0 = left, y 0 = bottom (the front of the machine), z 1 = the top face.
    With *rotary* set (a Rotary Mode job) only the place along the bar is a
    choice: ``("left end", "rotary axis")``, or ``"top of the bar"`` with
    *rotary_top*.
    """
    fx, fy, fz = origin

    def near(v, t):
        return abs(v - t) <= 0.02

    def axis(v, lo, hi):
        if near(v, 0):
            return lo
        if near(v, 1):
            return hi
        if near(v, 0.5):
            return "center"
        return f"{round(v * 100)}% from {lo}"

    x, y = axis(fx, "left", "right"), axis(fy, "bottom", "top")
    if rotary is not None:
        return (("center" if x == "center" else f"{x} end"),
                "top of the bar" if rotary_top else "rotary axis")
    xy = "center" if x == y == "center" else f"{y} {x}"
    if near(fz, 1):
        z = "top of stock"
    elif near(fz, 0):
        z = "bottom of stock"
    else:
        z = f"{round(fz * 100)}% up the stock"
    return xy, z
