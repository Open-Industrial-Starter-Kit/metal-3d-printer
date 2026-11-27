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

SCAN = ("G92X0Y0\n"
        "M3\n"
        "; infill\n"
        "G0 X10.000 Y10.000\n"
        "G1 X86.000 Y10.000 F3000\n"
        "G1 X86.000 Y86.000\n"
        "M5\n"
        "; contour\n"
        "G0 X20.000 Y20.000\n"
        "M3\n"
        "G1 X76.000 Y20.000 F1200\n"
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
    assert f"X{10.0 + offset:.3f}" in joined, "bed X10 not mapped"
    assert f"X{86.0 + offset:.3f}" in joined, "bed X86 not mapped"
    # The span must be preserved exactly -- an offset, never a scale.
    assert (86.0 + offset) - (10.0 + offset) == 76.0


@check
def test_layer_break_between_scan_and_next_recoat():
    """M600 closes a scan only when stage work follows it."""
    routed = retarget(two_layer_olos())
    s = summarise(routed)
    assert s == {"stage_blocks": 2, "galvo_blocks": 2, "layer_breaks": 1}, s
    # The final scan is not followed by a recoat, so it gets no break.
    assert not routed.rstrip().endswith("M600")


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
