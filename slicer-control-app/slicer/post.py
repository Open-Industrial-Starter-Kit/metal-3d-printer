"""
slicer/post.py
Turning the engine's output into a job this machine will actually run.

The engine emits one stream of single-coordinate-system GRBL, because OLOS
scans with its gantry. This machine has two XY systems, so there are two ways to
take that output, and this module is both of them behind one interface:

* :class:`GantryPost` -- leave it as it is. CPU-B's dialect says "a file with no
  marker at all is a stage file" (firmware/CPU-B/README.md 5.13.2.1), and
  OLOS's output *is* a stage file, so the gantry route needs no translation at
  all. What it needs is checking.
* :class:`GalvoPost` -- split it into ``M610``/``M611``/``M600`` blocks through
  ``retarget.py``, so the mirrors scan and the gantry only recoats.

Both return a :class:`PostResult`: the program, and a list of notes the
interface shows. The notes are the point of this module. Two of them are the
difference between a print and a confusing afternoon.

**The soft limits will kill an OLOS job silently.** CPU-B ships Piccola's
envelope -- X 0..143, Y -145..0, Z -70..0 in ``Config.h`` -- and OLOS's very
first instruction after the feedrate is ``G53 X-238.5 Y-158.0``, its bed
reference. That is outside X 0..143, so
``evaluateAndApplySoftLimitsForOutgoingLine`` returns false, and on the job path
(``MetalPrinter_CPUB.ino`` around line 2469) a rejection calls
``stopJobWithReset()`` and returns **without sending the app any error**. The
machine stops at the second line of the program and nothing says why. The
interactive move commands do answer ``softLimitExceeded``; the job path does
not. So this module resolves every coordinate the way the firmware does and
refuses to hand over a program that would die.

**The gantry route cannot fire the laser.** The laser gate and its power PWM are
CPU-C pins (GP20 and GP21), driven by CPU-C's own ``M3``/``M5``/``S`` parser. In
a stage block ``M3 S500`` goes to CPU-A, which is not wired to the fiber laser.
So a gantry job today traces every path correctly with the beam off. That is
worth knowing before loading powder.
"""

import re

from . import retarget


# ──────────────────────────────────────────────────────────────────────────────
# The machine envelope
# ──────────────────────────────────────────────────────────────────────────────
#
# These are CPU-B's current values, copied from firmware/CPU-B/src/Config.h so
# the check predicts what the firmware will actually do. They are *Piccola's*
# envelope, not this machine's -- this printer is roughly X 400, Y 200, Z 92 --
# and the zero and direction convention for it has not been settled. That is
# exactly why they are settings here rather than constants: when Config.h is
# corrected, these move with it, and until then the check reports the truth
# about what the firmware will accept today.

DEFAULT_ENVELOPE = {
    "min_x": 0.0, "max_x": 143.0,
    "min_y": -145.0, "max_y": 0.0,
    "min_z": -70.0, "max_z": 0.0,
}

_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


class Note:
    """One thing to tell the operator. Severity is 'info', 'warn' or 'fail'."""

    __slots__ = ("severity", "text")

    def __init__(self, severity, text):
        self.severity = severity
        self.text = text

    def __repr__(self):
        return f"Note({self.severity!r}, {self.text!r})"

    def __eq__(self, other):
        return (isinstance(other, Note)
                and (self.severity, self.text) == (other.severity, other.text))


class PostResult:
    """A finished program, plus what should be said about it."""

    def __init__(self, text, notes=None, stats=None):
        self.text = text
        self.notes = list(notes or ())
        self.stats = dict(stats or {})

    @property
    def ok(self):
        """False if anything would stop this program running."""
        return not any(n.severity == "fail" for n in self.notes)

    @property
    def failures(self):
        return [n for n in self.notes if n.severity == "fail"]

    @property
    def warnings(self):
        return [n for n in self.notes if n.severity == "warn"]


# ──────────────────────────────────────────────────────────────────────────────
# Resolving coordinates the way the firmware does
# ──────────────────────────────────────────────────────────────────────────────

class _Resolver:
    """Tracks machine position and work offset through a program.

    Mirrors ``evaluateAndApplySoftLimitsForOutgoingLine``: ``G53`` addresses
    machine coordinates directly, anything else is work coordinates plus the
    work offset.

    The firmware takes that offset from grblHAL's live ``WCO:`` report rather
    than tracking ``G92`` itself, so a perfect prediction would need the
    machine's state at the moment the job starts. It is predictable anyway,
    because an OLOS program sets its own origin in its first two instructions:
    ``G53`` to the bed reference, then ``G92 X0 Y0`` to call that zero. Simulate
    those two and every later coordinate resolves.
    """

    def __init__(self):
        self.machine = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": 0.0}
        self.wco = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": 0.0}
        self.absolute = True

    def feed(self, line):
        """Apply one line. Returns the machine-coordinate target it commands,
        as a dict of the axes it moves, or ``None`` for a non-motion line."""
        bare = line.split(";", 1)[0].split("(", 1)[0].strip()
        if not bare:
            return None
        up = bare.upper()
        words = dict((m.group(1).upper(), float(m.group(2)))
                     for m in _WORD.finditer(bare))

        if "G90" in up:
            self.absolute = True
        if "G91" in up:
            self.absolute = False

        axes = {k: v for k, v in words.items() if k in self.machine}
        if not axes:
            return None

        if "G92" in up:
            # G92 renames the current position, it does not move. The offset is
            # whatever makes the current machine position read the given value.
            for ax, val in axes.items():
                self.wco[ax] = self.machine[ax] - val
            return None

        if "G53" in up:
            target = dict(axes)
        elif self.absolute:
            target = {ax: val + self.wco[ax] for ax, val in axes.items()}
        else:
            target = {ax: self.machine[ax] + val for ax, val in axes.items()}

        self.machine.update(target)
        return target


def machine_extremes(gcode, skip_galvo_blocks=False):
    """The machine-coordinate range a program demands, per axis.

    ``skip_galvo_blocks`` honours what CPU-B does with a routed file: "Uploads
    measure stage blocks only... their coordinates are scan-field millimetres,
    and a 170 mm field position would otherwise fail a 143 mm machine
    envelope." So for a galvo program only the ``M610`` blocks are measured.
    """
    r = _Resolver()
    spans = {}
    in_galvo = False
    for line in gcode.splitlines():
        t = line.strip()
        if t == retarget.GALVO_MARKER:
            in_galvo = True
            continue
        if t == retarget.STAGE_MARKER:
            in_galvo = False
            continue
        if t.startswith(retarget.LAYER_BREAK):
            in_galvo = False
            continue
        if skip_galvo_blocks and in_galvo:
            continue
        target = r.feed(line)
        if not target:
            continue
        for ax, val in target.items():
            lo, hi = spans.get(ax, (val, val))
            spans[ax] = (min(lo, val), max(hi, val))
    return spans


def check_envelope(gcode, envelope=None, skip_galvo_blocks=False):
    """Notes about whether this program stays inside the machine envelope.

    Only X, Y and Z are checked, because those are the three the firmware
    checks. A and B -- the powder reservoir and the recoater blade -- are not in
    ``isWithinSoftLimitRange`` at all, so their extremes are reported for the
    operator and never refused.
    """
    env = dict(DEFAULT_ENVELOPE)
    env.update(envelope or {})
    spans = machine_extremes(gcode, skip_galvo_blocks=skip_galvo_blocks)
    notes = []

    for ax in ("X", "Y", "Z"):
        if ax not in spans:
            continue
        lo, hi = spans[ax]
        emin = env[f"min_{ax.lower()}"]
        emax = env[f"max_{ax.lower()}"]
        if lo < emin - 1e-6 or hi > emax + 1e-6:
            notes.append(Note("fail",
                f"{ax} reaches {lo:.1f}..{hi:.1f} mm in machine coordinates, "
                f"and CPU-B's soft limits allow {emin:.1f}..{emax:.1f}. The "
                f"firmware checks G53 moves too, so this program stops at the "
                f"first offending line -- and on the job path it stops by "
                f"resetting, without sending the app any error. Either widen "
                f"SOFT_LIMIT_*_{ax}_MM in firmware/CPU-B/src/Config.h to match "
                f"this machine, or move the bed reference and parking position "
                f"in the Machine Routine settings."))

    # The piston's direction, which is a different problem from the limits
    # being too narrow and has a different fix.
    #
    # OLOS steps the build piston with "G1 Z<layer_height>" after "G92 Z0", so
    # it climbs by one layer height per layer -- +5 mm over a hundred layers of
    # 0.05 mm. In LPBF the platform descends as the part grows, so on OLOS's
    # machine positive Z is down. CPU-B's envelope runs Z -70..0, which says
    # zero is the top of travel and down is negative. Both cannot be true: if
    # the envelope is right about this machine, OLOS's step drives the piston
    # *up*, into the recoater blade.
    #
    # This is not corrected here. Inverting the sign on an assumption is how a
    # recoater gets destroyed, and the machine's zero and direction convention
    # has not been settled. So it is stated, with the evidence.
    if "Z" in spans:
        zlo, zhi = spans["Z"]
        if zhi > 1e-6 and env["max_z"] <= 1e-6:
            notes.append(Note("fail",
                f"The build piston steps towards +Z, reaching {zhi:.2f} mm, but "
                f"CPU-B's envelope allows Z {env['min_z']:.1f}..{env['max_z']:.1f} "
                f"-- zero at the top and down negative. OLOS climbs Z by one "
                f"layer height per layer, which on its machine lowers the "
                f"platform. One of the two conventions is wrong for this "
                f"machine, and guessing is how the recoater gets hit. Settle "
                f"which direction lowers the piston, then either invert the "
                f"sign here or correct SOFT_LIMIT_*_Z_MM."))

    for ax in ("A", "B"):
        if ax in spans:
            lo, hi = spans[ax]
            notes.append(Note("info",
                f"{ax} travels {lo:.2f}..{hi:.2f} mm "
                f"({'powder reservoir' if ax == 'A' else 'recoater blade'}). "
                f"Not soft-limit checked by the firmware."))
    return notes, spans


# ──────────────────────────────────────────────────────────────────────────────
# The two routes
# ──────────────────────────────────────────────────────────────────────────────

class Post:
    """What a slicer panel needs from a post-processor."""

    #: Shown on the panel's tab.
    name = "?"
    #: One line, shown under the name.
    description = ""
    #: Appended to an exported filename, before the extension.
    suffix = ""

    def process(self, gcode, settings=None, envelope=None):
        raise NotImplementedError


class GantryPost(Post):
    """The engine's output, unchanged, as a stage job for CPU-A.

    There is deliberately no translation. CPU-B treats an unmarked file as a
    stage file, and the engine already emits exactly that: one coordinate
    system, the gantry's. Adding ``M610`` around it would be legal and would
    change nothing, so it is not added -- the file stays byte-identical to what
    the engine produced, which makes it comparable with OLOS's own output.
    """

    name = "Gantry"
    description = ("The optics ride the gantry to every scan path, the way OLOS "
                   "does it. No field limit, so the part can be as large as the "
                   "bed.")
    suffix = "gantry"

    def process(self, gcode, settings=None, envelope=None):
        notes, spans = check_envelope(gcode, envelope)
        stats = {"machine_spans": spans,
                 "lines": sum(1 for _ in gcode.splitlines())}

        # The finding that matters most about this route.
        notes.insert(0, Note("warn",
            "The laser will not fire on this route. Its gate and power are "
            "CPU-C pins, driven by CPU-C's own M3/M5/S parsing; in a stage "
            "block those commands go to CPU-A, which is not wired to the fiber "
            "laser. The gantry will trace every path correctly with the beam "
            "off. Useful for a dry run, not for a part."))

        if "X" in spans and "Y" in spans:
            (xlo, xhi), (ylo, yhi) = spans["X"], spans["Y"]
            notes.append(Note("info",
                f"Gantry travel: X {xlo:.1f}..{xhi:.1f}, Y {ylo:.1f}..{yhi:.1f} mm "
                f"in machine coordinates."))
        return PostResult(gcode, notes, stats)


class GalvoPost(Post):
    """The engine's output split so the mirrors scan and the gantry recoats."""

    name = "Galvo"
    description = ("The gantry holds still and the mirrors scan, which is what "
                   "the machine is built to do. The part must fit the galvo "
                   "field.")
    suffix = "galvo"

    def process(self, gcode, settings=None, envelope=None):
        settings = settings or {}
        field = float(settings.get("galvo_field_mm", retarget.DEFAULT_FIELD_MM))
        bed = float(settings.get("bed_width", retarget.DEFAULT_BED_MM))

        try:
            routed = retarget.retarget(gcode, field_mm=field, bed_mm=bed)
        except ValueError as exc:
            # The bed not fitting the field is the one case retargeting refuses
            # outright, because the answer is stage tiling and that does not
            # exist yet.
            return PostResult("", [Note("fail", str(exc))], {})

        summary = retarget.summarise(routed)
        notes, spans = check_envelope(routed, envelope, skip_galvo_blocks=True)
        stats = dict(summary)
        stats["machine_spans"] = spans
        stats["lines"] = sum(1 for _ in routed.splitlines())
        stats["field_mm"] = field
        stats["bed_mm"] = bed

        offset = (field - bed) / 2.0
        notes.append(Note("info",
            f"{summary['galvo_blocks']} galvo blocks, "
            f"{summary['stage_blocks']} stage blocks, "
            f"{summary['layer_breaks']} layer breaks. Bed 0..{bed:g} mm maps to "
            f"field {offset:g}..{offset + bed:g} mm -- an offset, never a scale."))

        if summary["galvo_blocks"] == 0:
            notes.append(Note("fail",
                "Nothing was routed to the galvo. Every line was classified as "
                "stage work, which means this is not engine output in the "
                "expected shape."))
        return PostResult(routed, notes, stats)


#: The routes a panel can be built for, in the order they should appear.
POSTS = (GantryPost, GalvoPost)
