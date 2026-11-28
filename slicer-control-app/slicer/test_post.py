"""
slicer/test_post.py
Checks on the two routes a sliced program can take to the machine.

Run with: python3 -m slicer.test_post   (from slicer-control-app/)

Most of this is about the pre-flight check, because what it predicts is a
failure with no symptoms. CPU-B rejects a line outside its soft limits, and on
the job path it rejects by calling ``stopJobWithReset()`` and returning without
sending the app an error -- so the machine stops and nothing says why. Getting
the coordinate resolution wrong in either direction is costly: too strict and it
refuses programs that would run, too lax and it lets through one that dies at
line two.
"""

import sys

from slicer import post, retarget
from slicer.olos import slicers_shared_methods as ssm

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


SETTINGS = {"visicut_style": True, "layer_height": 0.075}

SCAN = ("G92X0Y0\n"
        "M3\n"
        "; infill\n"
        "G0X38.3700Y38.8000S0\n"
        "G1X57.5633Y38.8000S55F2000\n"
        "M5\n"
        "G1X0Y0F10000\n")


def two_layer():
    return (ssm.generate_init_block(SETTINGS)
            + SCAN
            + ssm.generate_routine_block(SETTINGS, 0.075)
            + SCAN)


#: Wide enough for OLOS's own geometry, for the tests that are not about limits.
ROOMY = {"min_x": -500.0, "max_x": 500.0,
         "min_y": -500.0, "max_y": 500.0,
         "min_z": -100.0, "max_z": 100.0}


# ── resolving coordinates ───────────────────────────────────────────────────

@check
def test_g53_is_machine_coordinates():
    r = post._Resolver()
    assert r.feed("G53 X-238.5 Y-158.0") == {"X": -238.5, "Y": -158.0}
    assert r.machine["X"] == -238.5


@check
def test_g92_renames_without_moving():
    """G92 sets the work offset so the current position reads the given value.
    It commands no motion, so it must not be reported as a target."""
    r = post._Resolver()
    r.feed("G53 X-238.5 Y-158.0")
    assert r.feed("G92 X0 Y0") is None
    assert r.wco["X"] == -238.5, r.wco
    assert r.machine["X"] == -238.5, "G92 moved the machine"


@check
def test_work_coordinates_resolve_through_the_offset():
    """The whole point: a scan path at work X38.37 is machine X-200.13 once
    OLOS's own init block has set the origin, and that is what the firmware
    compares against its limits."""
    r = post._Resolver()
    r.feed("G53 X-238.5 Y-158.0")
    r.feed("G92 X0 Y0")
    t = r.feed("G1 X38.37 Y10.0 F2000")
    assert abs(t["X"] - (-200.13)) < 1e-6, t
    assert abs(t["Y"] - (-148.0)) < 1e-6, t


@check
def test_relative_mode_is_tracked():
    r = post._Resolver()
    r.feed("G53 X10 Y10")
    r.feed("G91")
    t = r.feed("G1 X5")
    assert abs(t["X"] - 15.0) < 1e-6, t
    r.feed("G90")
    t = r.feed("G1 X5")
    assert abs(t["X"] - 5.0) < 1e-6, t


@check
def test_g92_per_axis_only():
    """G92 Z0 must not reset X and Y as well."""
    r = post._Resolver()
    r.feed("G53 X-100 Y-100 Z-5")
    r.feed("G92 Z0")
    assert r.wco["Z"] == -5.0, r.wco
    assert r.wco["X"] == 0.0, r.wco


# ── the envelope check ──────────────────────────────────────────────────────

@check
def test_olos_output_is_refused_against_cpub_defaults():
    """The finding this module exists for: with CPU-B's shipped envelope, an
    OLOS program cannot run, and the firmware would stop it silently."""
    notes, spans = post.check_envelope(two_layer())
    fails = [n for n in notes if n.severity == "fail"]
    assert fails, "the shipped envelope should refuse OLOS's bed reference"
    assert any("soft limits" in n.text for n in fails), fails
    assert spans["X"][0] <= -395.5 + 1e-6, spans["X"]


@check
def test_a_roomy_envelope_passes():
    notes, _ = post.check_envelope(two_layer(), ROOMY)
    fails = [n for n in notes if n.severity == "fail"]
    assert not fails, fails


@check
def test_the_piston_direction_is_called_out_separately():
    """Z climbing against a zero-topped envelope is a different problem from
    the limits being too narrow, and has a different fix, so it gets its own
    note rather than hiding inside the generic one."""
    notes, _ = post.check_envelope(two_layer())
    piston = [n for n in notes if "piston" in n.text]
    assert piston, "the +Z piston step was not reported"
    assert piston[0].severity == "fail"
    # It must not fire where the envelope allows positive Z.
    notes, _ = post.check_envelope(two_layer(), ROOMY)
    assert not [n for n in notes if "piston" in n.text]


@check
def test_a_and_b_are_reported_but_never_refused():
    """The firmware's isWithinSoftLimitRange only covers X, Y and Z, so the
    reservoir and the blade must not be refused against limits that do not
    apply to them -- B reaches -405 mm by design."""
    notes, spans = post.check_envelope(two_layer(), ROOMY)
    assert spans["B"][0] <= -405.0 + 1e-6, spans["B"]
    for n in notes:
        assert not (n.severity == "fail" and ("reservoir" in n.text
                                              or "blade" in n.text)), n


@check
def test_galvo_field_coordinates_are_not_measured_as_machine_travel():
    """CPU-B skips galvo blocks when it bounds-checks an upload, because their
    coordinates are field millimetres. Measuring them as machine travel would
    refuse a 170 mm field position against a 143 mm envelope -- for a move the
    gantry never makes."""
    routed = retarget.retarget(two_layer())
    with_galvo = post.machine_extremes(routed, skip_galvo_blocks=False)
    without = post.machine_extremes(routed, skip_galvo_blocks=True)
    assert with_galvo["X"][1] > without["X"][1] + 1.0, (with_galvo, without)


# ── the gantry route ────────────────────────────────────────────────────────

@check
def test_gantry_route_does_not_alter_the_program():
    """CPU-B treats an unmarked file as a stage file and OLOS already emits
    one, so this route must be byte-identical -- which is what keeps its output
    comparable with OLOS's own."""
    src = two_layer()
    r = post.GantryPost().process(src, envelope=ROOMY)
    assert r.text == src, "the gantry route modified the program"
    assert retarget.STAGE_MARKER not in r.text
    assert retarget.GALVO_MARKER not in r.text


@check
def test_gantry_route_warns_the_laser_will_not_fire():
    """The gate and power are CPU-C pins; M3 in a stage block reaches CPU-A,
    which is not wired to the laser. Tracing a part with the beam off should
    not be a surprise."""
    r = post.GantryPost().process(two_layer(), envelope=ROOMY)
    assert any(n.severity == "warn" and "will not fire" in n.text
               for n in r.notes), r.notes


# ── the galvo route ─────────────────────────────────────────────────────────

@check
def test_galvo_route_produces_blocks():
    r = post.GalvoPost().process(two_layer(), envelope=ROOMY)
    assert r.ok, r.failures
    assert r.stats["galvo_blocks"] == 2, r.stats
    assert r.stats["layer_breaks"] == 2, r.stats
    assert retarget.GALVO_MARKER in r.text


@check
def test_galvo_route_honours_a_configured_field_and_bed():
    r = post.GalvoPost().process(
        two_layer(), settings={"galvo_field_mm": 200.0, "bed_width": 50.0},
        envelope=ROOMY)
    assert r.ok, r.failures
    assert r.stats["field_mm"] == 200.0 and r.stats["bed_mm"] == 50.0
    # bed 50 centred in field 200 -> offset 75
    assert "75" in [n.text for n in r.notes if "maps to field" in n.text][0]


@check
def test_galvo_route_refuses_a_bed_bigger_than_the_field():
    """Stage tiling is what this would need, and it does not exist, so the
    refusal has to be explicit rather than a silently clipped scan."""
    r = post.GalvoPost().process(
        two_layer(), settings={"galvo_field_mm": 50.0, "bed_width": 96.0})
    assert not r.ok, r.notes
    assert any("tiling" in n.text for n in r.failures), r.failures
    assert r.text == "", "a refused program should carry no output"


@check
def test_galvo_route_refuses_input_with_nothing_to_scan():
    """A program where everything classifies as stage work is not engine output
    in the expected shape, and routing it would produce a file that recoats
    repeatedly and never scans."""
    r = post.GalvoPost().process("G53 X-10 Y-10\nG1 Z0.5 F50\n", envelope=ROOMY)
    assert not r.ok, r.notes
    assert any("Nothing was routed" in n.text for n in r.failures), r.failures


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
