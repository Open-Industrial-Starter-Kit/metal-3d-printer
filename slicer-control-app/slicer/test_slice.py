"""
slicer/test_slice.py
End-to-end: a model file in, a routed program out.

Run with: python3 -m slicer.test_slice   (from slicer-control-app/)

This is the test that would have caught every problem found while getting the
engine to run for the first time, and all of them were invisible to
``test_retarget.py`` because that one builds its G-code from the generators
rather than from a real slice:

* PrusaSlicer being handed flags it does not have, or handing our request to a
  running GUI instance and writing nothing.
* The engine being told the wrong model footprint, which scales every
  coordinate in the output.
* A layer yielding no geometry, which produces a well-formed file with no
  motion in it.
* The parking move being routed to the mirrors.

It needs PrusaSlicer, so it skips rather than fails where that is absent --
a developer without it can still run the rest of the suite. ``slicer/README.md``
says how to install it.

The model is generated here rather than checked in: it is 684 bytes of
arithmetic, and a box with a known footprint makes the bed-placement assertion
exact.
"""

import os
import struct
import sys
import tempfile

from slicer import params
from slicer.job import slice_model, measure_model, read_stl, ModelError
from slicer.olos.binary import prusa_slicer_available
from slicer.retarget import retarget, summarise, DEFAULT_FIELD_MM

BOX_MM = (20.0, 20.0, 5.0)
LAYER_MM = 0.5


def write_box_stl(path, w=BOX_MM[0], h=BOX_MM[1], d=BOX_MM[2]):
    """A binary STL of an axis-aligned box sitting on z=0."""
    v = [(0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0),
         (0, 0, d), (w, 0, d), (w, h, d), (0, h, d)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, dd in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[dd]))
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for pt in t:
                f.write(struct.pack("<3f", *pt))
            f.write(struct.pack("<H", 0))
    return path


def write_box_stl_ascii(path, w=4.0, h=6.0, d=2.0):
    """The same box as ASCII STL, to exercise the other reader branch."""
    v = [(0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0),
         (0, 0, d), (w, 0, d), (w, h, d), (0, h, d)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    with open(path, "w") as f:
        f.write("solid box\n")
        for a, b, c, dd in quads:
            for tri in ((v[a], v[b], v[c]), (v[a], v[c], v[dd])):
                f.write("  facet normal 0 0 0\n    outer loop\n")
                for pt in tri:
                    f.write("      vertex %f %f %f\n" % pt)
                f.write("    endloop\n  endfacet\n")
        f.write("endsolid box\n")
    return path


CHECKS = []
SKIPS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def needs_prusa(fn):
    fn.needs_prusa = True
    return check(fn)


# ── the model reader ─────────────────────────────────────────────────────────

@check
def test_binary_stl_footprint():
    """A binary STL's footprint is measured, not guessed."""
    with tempfile.TemporaryDirectory() as d:
        p = write_box_stl(os.path.join(d, "box.stl"))
        verts, tris = read_stl(p)
        assert tris.shape == (12, 3), tris.shape
        size = measure_model(p)
        assert (round(size.x, 3), round(size.y, 3), round(size.z, 3)) == BOX_MM, size


@check
def test_ascii_stl_footprint():
    """ASCII STL goes down the other branch and must measure the same way.

    The binary check is arithmetic on the file length, not a "solid" prefix,
    because binary STLs routinely begin with that word inside their 80-byte
    header. This asserts the fallback still works.
    """
    with tempfile.TemporaryDirectory() as d:
        p = write_box_stl_ascii(os.path.join(d, "box_ascii.stl"))
        size = measure_model(p)
        assert (round(size.x, 3), round(size.y, 3)) == (4.0, 6.0), size


@check
def test_unreadable_model_is_refused():
    """A file that is not an STL says so, rather than raising from struct."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "not.stl")
        with open(p, "w") as f:
            f.write("this is not a model, it is a sentence about one\n")
        try:
            measure_model(p)
        except ModelError:
            pass
        else:
            raise AssertionError("a non-STL was accepted")


# ── the parameter contract ───────────────────────────────────────────────────

@check
def test_required_keys_are_checked_up_front():
    """A missing required key names itself, instead of a KeyError from inside
    six thousand lines of engine."""
    class MS:
        x = y = 20.0
    try:
        params.build_slicing_data("/m.stl", "/o.nc", MS(), {"contour_power": None})
    except params.MissingParameters as exc:
        assert "contour_power" in str(exc), exc
    else:
        raise AssertionError("a missing required key was accepted")


@check
def test_overlaps_are_fractions_not_percentages():
    """OLOS stores six overlaps as percentages and divides by 100 on the way
    in. This module holds the engine's units, so 15% must be 0.15 -- not 15,
    which the engine would read as 1500%."""
    d = params.defaults()
    for key in params.PERCENT_KEYS:
        if key in d and d[key]:
            assert d[key] < 1.0, f"{key} = {d[key]} looks like a percentage"


# ── the whole pipeline ───────────────────────────────────────────────────────

@needs_prusa
def test_box_slices_to_motion():
    """The engine produces real motion, and enough of it."""
    with tempfile.TemporaryDirectory() as d:
        stl = write_box_stl(os.path.join(d, "box.stl"))
        nc = os.path.join(d, "box.nc")
        seen = []
        s = slice_model(stl, nc, overrides={"layer_height": LAYER_MM,
                                            "layer_speed": 1000},
                        on_progress=lambda i, n: seen.append((i, n)))
        expect_layers = int(BOX_MM[2] / LAYER_MM)
        assert s["contour_sections"] == expect_layers, s
        assert s["infill_sections"] == expect_layers, s
        assert s["motion_moves"] > 100, s
        assert seen, "no progress was reported"


@needs_prusa
def test_part_lands_centred_on_the_bed():
    """Coordinates must be the model's footprint centred on the bed.

    This is the assertion that catches a wrong ``model_size``: get it wrong and
    the output is still well-formed G-code, just at the wrong scale.
    """
    with tempfile.TemporaryDirectory() as d:
        stl = write_box_stl(os.path.join(d, "box.stl"))
        nc = os.path.join(d, "box.nc")
        slice_model(stl, nc, overrides={"layer_height": LAYER_MM,
                                        "layer_speed": 1000})
        import re
        xs = []
        for line in open(nc):
            up = line.strip().upper()
            if up.startswith(";") or "G53" in up or "G92" in up:
                continue
            if up.startswith(("G0", "G1")):
                xs += [float(v) for v in re.findall(r"X(-?\d*\.?\d+)", up)]
        scan = [x for x in xs if x > 0.0001]   # exclude the park at X0
        bed = params.MACHINE_DEFAULTS["bed_width"]
        lo, hi = (bed - BOX_MM[0]) / 2.0, (bed + BOX_MM[0]) / 2.0
        assert lo - 1 <= min(scan) and max(scan) <= hi + 1, \
            f"part spans X {min(scan):.2f}..{max(scan):.2f}, expected ~{lo}..{hi}"


@needs_prusa
def test_recoat_routine_is_emitted_between_layers():
    """Every layer but the last must be followed by a full recoat."""
    with tempfile.TemporaryDirectory() as d:
        stl = write_box_stl(os.path.join(d, "box.stl"))
        nc = os.path.join(d, "box.nc")
        slice_model(stl, nc, overrides={"layer_height": LAYER_MM,
                                        "layer_speed": 1000})
        text = open(nc).read()
        step = f"Z{LAYER_MM:.3f}"
        for needed in ("G92 Z0", "G92 A0", step,
                       f"G53 B{params.MACHINE_DEFAULTS['recoater_out']:.1f}",
                       f"G53 B{params.MACHINE_DEFAULTS['recoater_home']:.1f}"):
            assert needed in text, f"recoat routine is missing {needed!r}"


@needs_prusa
def test_real_output_routes_to_blocks():
    """The retargeter handles real engine output, not just generated fixtures."""
    with tempfile.TemporaryDirectory() as d:
        stl = write_box_stl(os.path.join(d, "box.stl"))
        nc = os.path.join(d, "box.nc")
        slice_model(stl, nc, overrides={"layer_height": LAYER_MM,
                                        "layer_speed": 1000})
        routed = retarget(open(nc).read())
        s = summarise(routed)
        layers = int(BOX_MM[2] / LAYER_MM)
        assert s["galvo_blocks"] == layers, s
        assert s["layer_breaks"] == layers, s

        # Every galvo coordinate must sit inside the field, and none may be the
        # park: bed 0 maps to the field offset, so seeing it means a parking
        # move was handed to the mirrors.
        import re
        inside, mode = 0, None
        for line in routed.splitlines():
            t = line.strip()
            if t in ("M610", "M611"):
                mode = t
                continue
            if mode != "M611" or t.startswith(";") or not t:
                continue
            assert "F10000" not in t, f"a parking move reached the galvo: {t!r}"
            for v in re.findall(r"[XY](-?\d*\.?\d+)", t.upper()):
                f = float(v)
                assert 0.0 <= f <= DEFAULT_FIELD_MM, f"{f} is outside the field"
                inside += 1
        assert inside > 100, f"only {inside} galvo coordinates found"


def main():
    have_prusa = prusa_slicer_available()
    failures = skipped = 0
    for fn in CHECKS:
        if getattr(fn, "needs_prusa", False) and not have_prusa:
            print(f"SKIP  {fn.__name__}: PrusaSlicer is not installed")
            skipped += 1
            continue
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        else:
            print(f"ok    {fn.__name__}")
    ran = len(CHECKS) - skipped
    tail = f", {skipped} skipped (no PrusaSlicer)" if skipped else ""
    print(f"\n{ran - failures}/{ran} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
