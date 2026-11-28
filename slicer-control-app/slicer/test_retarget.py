"""
slicer/test_retarget.py
Checks on the OLOS -> M610/M611 translation.

Run with: python3 -m slicer.test_retarget   (from slicer-control-app/)

These are regression tests, not a demonstration. Each one covers a way the
translation silently produced a program that would run and do the wrong thing,
which is the failure mode that matters here: a dropped line does not raise, it
just makes the machine move differently than the slicer intended.

The OLOS side is built from the real generators in
slicer/olos/slicers_shared_methods.py rather than a pasted sample, so a change
to the upstream recoat routine shows up here instead of passing against a stale
fixture. The scan body is hand-written because producing a real one needs
PrusaSlicer and a model.
"""

import sys

from slicer.olos import slicers_shared_methods as ssm
from slicer.retarget import (retarget, classify, summarise,
                             DEFAULT_FIELD_MM, DEFAULT_BED_MM)

SETTINGS = {"visicut_style": True, "layer_height": 0.075}

# One layer's scan, in the shape the engine really emits. Captured from a run
# over a 20 x 20 x 5 mm box at 0.5 mm -- trimmed in length, untouched in
# structure, which is what the routing depends on. What the earlier hand-written
# version got wrong, and what each line is here to preserve:
#
#   * **One M3 ... M5 per layer**, not one per section. The hand-written version
#     closed the gate between infill and contour, which the engine does not do,
#     and that masked how the gate has to be read.
#   * **G0 jumps carry S0 and live inside the gate.** "Laser off" is therefore
#     not what separates a jump from a travel move.
#   * **The layer ends with a park to the work origin**, after the M5 and before
#     the recoat. That is the one XY-only line in the stream that belongs to the
#     gantry, and routing it to the mirrors was a real bug.
#   * **S words ride on the motion lines** (S55 here, from the first-zone
#     exposure), and a continuation line carries no S at all.
SCAN = ("G92X0Y0\n"
        "M3\n"
        "; infill\n"
        "G0X38.3700Y38.8000S0\n"
        "G1X57.5633Y38.8000S55F2000\n"
        "G0X57.5633Y39.0000S0\n"
        "G1X38.3700Y39.0000S55F2000\n"
        "; contour\n"
        "G0X38.4000Y57.6000S0\n"
        "G1X57.5333Y57.6000S55F2000\n"
        "G1X57.5333Y38.4667\n"
        "G1X38.4000Y38.4667\n"
        "M5\n"
        "G1X0Y0F10000\n")


def two_layer_olos():
    return (ssm.generate_init_block(SETTINGS)
            + SCAN
            + ssm.generate_routine_block(SETTINGS, 0.075)
            + SCAN)


def blocks(routed):
    """Walk a routed program into [(marker, [lines])].

    Splitting on the marker strings would be wrong: the generated header
    comment names all three of them.
    """
    out, cur = [], None
    for line in routed.splitlines():
        t = line.strip()
        if not t or t.startswith(";"):
            continue
        if t in ("M610", "M611"):
            cur = (t, [])
            out.append(cur)
        elif cur is not None:
            cur[1].append(t)
    return out


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def test_classification():
    """Axis letters decide the destination."""
    cases = {
        "G53 X-395.5 Y-4.5": "stage",     # machine-coordinate parking
        "G53 B-405.0": "stage",           # recoater throw
        "G1 A-0.3 F50": "stage",          # powder reservoir
        "G1 Z0.075 F50": "stage",         # build piston
        "G92 Z0": "stage",                # piston origin reset -- essential
        "G92 A0": "stage",
        "G92 X0 Y0": "drop",              # gantry work origin -- resolved by mapping
        "G92X0Y0": "drop",
        "G1 X86.000 Y10.000 F3000": "galvo",
        "G0 X10 Y10": "galvo",
        "M3": "laser",
        "M5": "laser",
        "G1 F3000": "feed",               # modal, belongs to its run
        "F3000": "feed",
    }
    for line, want in cases.items():
        got = classify(line)
        assert got == want, f"{line!r}: expected {want}, got {got}"


@check
def test_recoat_routine_survives_intact():
    """The stage block keeps every line the recoat depends on, in order."""
    routed = retarget(two_layer_olos())
    recoat = [ls for m, ls in blocks(routed) if m == "M610"][1]

    for needed in ("G92 A0", "G92 Z0", "G1 A-0.3 F50", "G1 Z0.075 F50",
                   "G53 B-405.0", "G53 B-54.5", "G53 X-238.5 Y-158.0"):
        assert needed in recoat, f"recoat lost {needed!r}"

    # The piston origin must be reset before the one-layer step, or the step
    # becomes an absolute height and the bed stops descending after layer one.
    assert recoat.index("G92 Z0") < recoat.index("G1 Z0.075 F50")

    # The recoater throw must be preceded by its own feedrate, or it runs at
    # the powder reservoir's F50 -- sixty times too slow.
    assert "G1 F3000" in recoat
    assert recoat.index("G1 F3000") < recoat.index("G53 B-405.0")

    # Parking feed before the parking move.
    assert "G1 F10000" in recoat
    assert recoat.index("G1 F10000") < recoat.index("G53 X-395.5 Y-4.5")


@check
def test_no_stage_axis_reaches_the_galvo():
    """A galvo block may only carry X/Y/F and laser gating."""
    routed = retarget(two_layer_olos())
    for marker, lines in blocks(routed):
        if marker != "M611":
            continue
        for line in lines:
            assert "G53" not in line, f"G53 in a galvo block: {line!r}"
            body = line.split(";")[0].upper()
            for axis in "ZAB":
                assert axis not in body, f"stage axis {axis} in galvo: {line!r}"


@check
def test_scan_coordinates_centre_the_bed_in_the_field():
    """Bed 0..96 lands at field 77..173, keeping path geometry unchanged."""
    offset = (DEFAULT_FIELD_MM - DEFAULT_BED_MM) / 2.0
    routed = retarget(two_layer_olos())
    galvo = [ls for m, ls in blocks(routed) if m == "M611"][0]
    joined = "\n".join(galvo)
    # Two real coordinates from opposite sides of the part's footprint.
    assert f"X{38.3700 + offset:.3f}" in joined, "bed X38.37 not mapped"
    assert f"X{57.5633 + offset:.3f}" in joined, "bed X57.5633 not mapped"
    # The span must be preserved exactly -- an offset, never a scale.
    assert round((57.5633 + offset) - (38.3700 + offset), 4) == 19.1933

    # The park never reaches a galvo block, so field 77.000 -- which is what
    # bed 0 maps to -- must not appear in one. This is the assertion that would
    # have caught the misrouted park.
    assert f"X{offset:.3f}" not in joined, "the gantry park was mapped into the field"


@check
def test_layer_break_between_scan_and_next_recoat():
    """M600 closes a scan only when stage work follows it."""
    routed = retarget(two_layer_olos())
    s = summarise(routed)
    # Three stage blocks for two layers, because each layer ends by parking the
    # gantry at the work origin and that park opens a stage run of its own:
    #   init -> scan -> park+recoat -> scan -> park
    assert s == {"stage_blocks": 3, "galvo_blocks": 2, "layer_breaks": 2}, s


@check
def test_gantry_park_does_not_reach_the_mirrors():
    """The per-layer "G1 X0 Y0 F10000" is the gantry parking, not a scan path.

    It is emitted once per layer, after the M5 and before the recoat. Routed to
    the galvo it became "G1 X77 Y77 F10000": the mirrors swept to the corner of
    the field every layer, and the gantry never parked before the recoater
    threw.
    """
    routed = retarget(two_layer_olos())
    for marker, lines in blocks(routed):
        if marker != "M611":
            continue
        for line in lines:
            assert "F10000" not in line, f"a parking move reached the galvo: {line!r}"

    # And it has to actually be somewhere -- dropped is as wrong as misrouted.
    stage = [l for m, ls in blocks(routed) if m == "M610" for l in ls]
    assert any("X0" in l and "Y0" in l and "F10000" in l for l in stage), \
        "the gantry park was dropped entirely"


@check
def test_long_guarded_jump_stays_with_the_galvo():
    """A laser-off jump of 25 mm or more is still a galvo jump.

    ``hard_gate_laser_motion`` wraps any laser-off travel of 25 mm or more in
    S0/M5 ... M3, so a jump between two islands that far apart sits *outside*
    the M3/M5 gate while remaining a move the mirrors must make. Reading "gate
    shut" as "stage" would send it to the gantry, which would travel mid-layer
    and leave the rest of the scan offset. A part under 25 mm across never
    exercises this, so it is asserted directly.
    """
    far_apart = ("G92X0Y0\n"
                 "M3\n"
                 "G1X10.000Y10.000S55F2000\n"
                 "S0\n"
                 "M5\n"              # the guard the engine inserts
                 "G0X80.000Y80.000\n"  # 99 mm jump, gate shut, still galvo
                 "M3\n"
                 "G1X86.000Y80.000S55F2000\n"
                 "M5\n"
                 "G1X0Y0F10000\n")
    routed = retarget(far_apart)
    offset = (DEFAULT_FIELD_MM - DEFAULT_BED_MM) / 2.0
    galvo = "\n".join(l for m, ls in blocks(routed) if m == "M611" for l in ls)
    assert f"X{80.0 + offset:.3f}" in galvo, \
        "a guarded long jump was routed to the gantry"
    assert summarise(routed)["galvo_blocks"] == 1, \
        "a guarded long jump split the scan into separate galvo blocks"


@check
def test_bed_larger_than_field_is_refused():
    """Stage tiling is not implemented, so say so rather than clip silently."""
    try:
        retarget(two_layer_olos(), field_mm=50.0, bed_mm=96.0)
    except ValueError as exc:
        assert "tiling" in str(exc)
    else:
        raise AssertionError("expected a ValueError for a bed bigger than the field")


def main():
    failures = 0
    for fn in CHECKS:
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        else:
            print(f"ok    {fn.__name__}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
