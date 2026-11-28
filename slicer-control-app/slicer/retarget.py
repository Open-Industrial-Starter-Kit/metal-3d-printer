"""
slicer/retarget.py
Turning OLOS G-code into this machine's routed block dialect.

The vendored OLOS engine emits one stream of single-coordinate-system GRBL
G-code: ``G53`` to a bed reference, ``G92`` to zero there, then ``G0``/``G1``
with ``M3``/``M5`` laser gating. In that machine the laser is carried by the
gantry, so a scan path *is* an XY move. There is no galvo anywhere in OLOS --
the word appears in one photo caption in its README and nowhere in its code.

This machine scans with a galvo instead, and ``X``/``Y`` therefore mean two
different things depending on who is listening: the gantry (CPU-A, grblHAL) or
the mirrors (CPU-C). The job file says which, with the M-codes CPU-B consumes
and never forwards -- ``M610`` stage, ``M611`` galvo, ``M600`` layer break.
See firmware/CPU-B/README.md section 5.13.2.1.

So OLOS's output cannot drive this machine as it stands: every scan path would
be sent to the gantry, which would try to drag the whole optical head along the
toolpath at the marking feedrate. This module is the adapter.

What goes where
---------------
The split is not a heuristic about intent -- it is readable from the axis
letters, because the two coordinate systems use disjoint ones:

* ``G53`` moves are machine-coordinate positioning: bed reference, X/Y parking,
  recoater throw. Always stage.
* ``Z`` is the build piston, ``A`` the powder reservoir, ``B`` the blade
  recoater. Always stage. (OLOS uses the same three letters for the same three
  things as this machine does, which is why its recoat routine transfers
  unchanged.)
* A bare ``G0``/``G1`` with only ``X``/``Y``/``F``, inside the laser-gated
  section, is a scan path. Galvo.
* ``M3``/``M5``/``S`` gate the laser and belong with the scan. Galvo.

Coordinates
-----------
After OLOS's ``G92 X0 Y0`` the scan paths run in bed coordinates, 0..96 mm on a
96 mm circular bed. The galvo field here is 250 x 250 mm (``X_MAX_POS_MM`` in
firmware/CPU-C/galvo_test_with_relays.cpp), which is larger than the bed, so the
bed fits inside a single field and no stage tiling is required -- the gantry
never moves during a scan. The bed is centred in the field: a bed coordinate
``b`` becomes ``b + (field - bed) / 2``, putting bed 0..96 at field 77..173.

``G92`` itself is dropped. It exists in the OLOS stream to make the gantry's
work origin coincide with the bed, and this translation has already resolved
that by mapping coordinates into the field directly. Forwarding it would reset
grblHAL's work offsets mid-job for no reason.
"""

import re

#: Galvo field, from X_MAX_POS_MM / Y_MAX_POS_MM in CPU-C.
DEFAULT_FIELD_MM = 250.0

#: OLOS's circular build bed.
DEFAULT_BED_MM = 96.0

#: Block markers, from firmware/CPU-B/src/Constants.h.
STAGE_MARKER = "M610"
GALVO_MARKER = "M611"
LAYER_BREAK = "M600"

_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")
_COMMENT = re.compile(r"\s*;.*$")

#: Axis letters that only ever mean the gantry/piston/powder/recoater.
_STAGE_AXES = set("ZAB")


def _strip(line):
    return _COMMENT.sub("", line).strip()


def _words(line):
    return {m.group(1).upper(): float(m.group(2))
            for m in _WORD.finditer(line)}


#: How close to the work origin a move has to be to count as the park. This is
#: the engine's own tolerance, from ``hard_gate_laser_motion`` and
#: ``enforce_m5_guarded_long_travel``, so the two agree on what a parking move
#: is rather than each deciding for itself.
_ORIGIN_EPS = 0.0001


def _targets_work_origin(bare):
    """True if this line commands both X and Y to the work origin (0, 0).

    The engine pins the work origin at 0, 0 itself -- ``prusa_slicer.py`` sets
    ``work_origin_x``/``_y`` to 0.0 before generating any layer -- so this does
    not need to be configurable, and hard-coding it keeps the definition in one
    place with the engine's.
    """
    seen = {}
    for m in _WORD.finditer(bare):
        letter = m.group(1).upper()
        if letter in ("X", "Y"):
            seen[letter] = float(m.group(2))
    return (len(seen) == 2
            and abs(seen["X"]) < _ORIGIN_EPS
            and abs(seen["Y"]) < _ORIGIN_EPS)


def classify(line, laser_on=True):
    """Return 'stage', 'galvo', 'laser', 'feed', 'drop' or 'blank' for one line.

    'laser' is separated from 'galvo' because M3/M5 must stay with the scan but
    carry no coordinates to translate.

    ``laser_on`` is whether the laser gate is open at this line, which the
    caller tracks from the M3/M4/M5 it has already seen. It is the only thing
    that disambiguates a bare X/Y move, and :func:`retarget` always passes the
    real state. The default is ``True`` because that is what a line quoted on
    its own, out of a scan, almost always is -- but a caller classifying a whole
    program and leaving it at the default will misroute the travel moves.
    """
    bare = _strip(line)
    if not bare:
        return "blank"

    upper = bare.upper()

    # G53 is machine-coordinate motion: parking, bed reference, recoater throw.
    if "G53" in upper:
        return "stage"

    words = _words(bare)

    # G92 needs splitting by axis, not dropping wholesale.
    #
    #   G92 X0 Y0  -- resets the gantry work origin to the bed reference. The
    #                 coordinate mapping below already resolves that, so this
    #                 is redundant here and would only disturb grblHAL's work
    #                 offsets mid-job.
    #   G92 Z0     -- resets the build piston origin to where it physically is,
    #   G92 A0        and likewise the powder reservoir. These are essential:
    #                 the routine then issues "G1 Z<layer_height>" as a single
    #                 step UP from here, not as an absolute height. Dropping
    #                 them turns every layer's step into the same absolute
    #                 0.075 mm and the bed never descends past layer one.
    if "G92" in upper:
        if _STAGE_AXES & set(words):
            return "stage"
        return "drop"

    # Laser gating travels with the scan.
    if upper.startswith("M3") or upper.startswith("M5"):
        return "laser"

    # Stage axes are unambiguous.
    if _STAGE_AXES & set(words):
        return "stage"

    # A motion word with only X/Y/F left is where the two systems are genuinely
    # ambiguous, because X and Y are the axis letters of both of them. The laser
    # gate is what separates them, and it is structural in OLOS's output: every
    # layer's scan is wrapped in M3 ... M5, and everything outside that pairing
    # is the gantry moving the optics.
    #
    # The gate alone is not enough, and assuming it was would have been a bad
    # bug. ``hard_gate_laser_motion`` in slicers_shared_methods.py wraps *any*
    # laser-off travel of 25 mm or more in S0/M5 ... M3, so a jump between two
    # islands 30 mm apart on the bed sits outside the gate and is still very
    # much a galvo jump. Treating "gate shut" as "stage" would hand those to the
    # gantry: it would travel 30 mm mid-layer while the mirrors stayed put, and
    # the rest of the scan would land offset by that much. A part smaller than
    # 25 mm never shows it, which is the worst property a bug can have.
    #
    # So the rule is the narrow one the engine itself uses. Both
    # ``hard_gate_laser_motion`` and ``enforce_m5_guarded_long_travel`` single
    # out a move to the work origin -- |X| and |Y| within 0.0001 -- as the
    # parking move, and that is the only XY-only line in the stream that is not
    # a scan path. It is emitted once per layer, immediately before the recoat.
    #
    # Before this, each of those became "G1 X77 Y77 F10000" inside the galvo
    # block: every layer the mirrors swept to the corner of the field for no
    # reason, and the gantry never parked before the recoater threw. Measured on
    # real engine output, not hypothesised.
    if ("G0" in upper or "G1" in upper) and ({"X", "Y"} & set(words)):
        if not laser_on and _targets_work_origin(bare):
            return "stage"
        return "galvo"

    # A modal feedrate with no motion ("G1 F3000", "F3000") belongs to
    # whichever run it sits in -- it is OLOS setting the speed for the lines
    # that follow. In the recoat routine those are the parking feed and the
    # recoater throw feed, so dropping them would leave the recoater running at
    # the powder reservoir's F50: sixty times too slow, and silently.
    if "F" in words and not ({"X", "Y"} & set(words)):
        return "feed"
    return "drop"


def _map_xy(line, offset):
    """Shift the X/Y of one scan line from bed frame into galvo field frame."""
    def repl(m):
        letter, value = m.group(1).upper(), float(m.group(2))
        if letter in ("X", "Y"):
            return f"{letter}{value + offset:.3f}"
        return m.group(0)
    return _WORD.sub(repl, _strip(line))


def retarget(olos_gcode, field_mm=DEFAULT_FIELD_MM, bed_mm=DEFAULT_BED_MM,
             mark_feed_mm_min=None):
    """Translate an OLOS G-code stream into M610/M611/M600 blocks.

    Returns the routed program as a string. Stage runs and scan runs are
    emitted in the order they appear, each under its own marker, so the machine
    recoats and scans in exactly the sequence OLOS laid out. A layer break is
    emitted after each scan run that is followed by more stage work, which is
    where the next recoat begins.
    """
    offset = (float(field_mm) - float(bed_mm)) / 2.0
    if offset < 0:
        raise ValueError(
            f"bed {bed_mm} mm does not fit the {field_mm} mm galvo field; "
            f"stage tiling would be required and is not implemented")

    out = [
        "; Routed from OLOS G-code by slicer/retarget.py",
        f"; bed {bed_mm:g} mm centred in the {field_mm:g} mm galvo field "
        f"(offset {offset:g} mm)",
        "; M610 = stage (CPU-A), M611 = galvo (CPU-C), M600 = layer break",
        ";",
        "G21",
        "G90",
    ]

    # Group consecutive lines of the same destination into runs.
    runs = []            # list of (dest, [lines])
    pending_feeds = []   # feedrates seen before any run was open
    laser_on = False
    for raw in olos_gcode.splitlines():
        kind = classify(raw, laser_on=laser_on)
        if kind in ("blank", "drop"):
            continue

        # Track the gate before routing the line, so the M5 itself still lands
        # in the scan run it closes rather than opening a stage run.
        if kind == "laser":
            laser_on = _strip(raw).upper().startswith(("M3", "M4"))

        if kind == "feed":
            # Sticky to the open run; otherwise held for the run it precedes.
            if runs:
                runs[-1][1].append(_strip(raw))
            else:
                pending_feeds.append(_strip(raw))
            continue

        dest = "stage" if kind == "stage" else "galvo"
        body = _strip(raw) if dest == "stage" else _map_xy(raw, offset)
        if runs and runs[-1][0] == dest:
            runs[-1][1].append(body)
        else:
            runs.append((dest, list(pending_feeds) + [body]))
            pending_feeds = []

    for index, (dest, lines) in enumerate(runs):
        if dest == "stage":
            out.append("")
            out.append(STAGE_MARKER)
            out.extend(lines)
        else:
            out.append("")
            out.append(GALVO_MARKER)
            if mark_feed_mm_min is not None:
                out.append(f"F{int(mark_feed_mm_min)}")
            out.extend(lines)
            # A layer break closes the scan whenever stage work follows.
            if index + 1 < len(runs) and runs[index + 1][0] == "stage":
                out.append(LAYER_BREAK)

    out.append("")
    return "\n".join(out) + "\n"


def summarise(routed_gcode):
    """Count blocks and layers in a routed program, for the UI and for tests."""
    stage = galvo = breaks = 0
    for line in routed_gcode.splitlines():
        bare = _strip(line).upper()
        if bare == STAGE_MARKER:
            stage += 1
        elif bare == GALVO_MARKER:
            galvo += 1
        elif bare == LAYER_BREAK:
            breaks += 1
    return {"stage_blocks": stage, "galvo_blocks": galvo,
            "layer_breaks": breaks}
