"""
slicer/test_model.py
Checks on reading a model file.

Run with: python3 -m slicer.test_model   (from slicer-control-app/)

The footprint is the number that matters most here and the one whose errors are
quietest: the engine never opens the model file, it is *told* the size, and that
size sets the mask's physical scale. A footprint wrong by a factor produces
well-formed G-code at the wrong scale, which looks like a successful slice.

Everything that needs PrusaSlicer is marked and skipped without it.
"""

import os
import struct
import sys
import tempfile

import numpy as np

from slicer import model
from slicer.olos.binary import prusa_slicer_available

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def needs_prusa(fn):
    fn.needs_prusa = True
    return check(fn)


# ── fixtures ────────────────────────────────────────────────────────────────

def _box_triangles(w, h, d, ox=0.0, oy=0.0, oz=0.0):
    v = [(ox, oy, oz), (ox + w, oy, oz), (ox + w, oy + h, oz), (ox, oy + h, oz),
         (ox, oy, oz + d), (ox + w, oy, oz + d), (ox + w, oy + h, oz + d),
         (ox, oy + h, oz + d)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, dd in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[dd]))
    return tris


def _normal(tri):
    """Computed, not zeroed. PrusaSlicer refuses an STL whose facet normals are
    all zero -- and refuses some geometries and not others, so a fixture with
    zero normals is one that happens to pass rather than one that is valid."""
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
    return nx / ln, ny / ln, nz / ln


def write_binary(path, tris):
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", *_normal(t)))
            for pt in t:
                f.write(struct.pack("<3f", *pt))
            f.write(struct.pack("<H", 0))
    return path


def write_ascii(path, tris):
    with open(path, "w") as f:
        f.write("solid box\n")
        for t in tris:
            f.write("  facet normal %f %f %f\n" % _normal(t))
            f.write("    outer loop\n")
            for pt in t:
                f.write("      vertex %f %f %f\n" % pt)
            f.write("    endloop\n  endfacet\n")
        f.write("endsolid box\n")
    return path


# ── reading ─────────────────────────────────────────────────────────────────

@check
def test_binary_and_ascii_measure_the_same():
    with tempfile.TemporaryDirectory() as d:
        tris = _box_triangles(12.0, 7.0, 3.0)
        a = model.load(write_binary(os.path.join(d, "b.stl"), tris))
        b = model.load(write_ascii(os.path.join(d, "a.stl"), tris))
        assert a.size == b.size, (a.size, b.size)
        assert (round(a.size.x, 3), round(a.size.y, 3), round(a.size.z, 3)) \
            == (12.0, 7.0, 3.0), a.size
        assert a.triangle_count == b.triangle_count == 12


@check
def test_a_binary_stl_whose_header_says_solid_is_still_binary():
    """The binary/ASCII decision is file-length arithmetic, not a "solid"
    prefix: binary STLs routinely carry that word in their 80-byte header, and
    sniffing for it sends them down the text path where they parse as zero
    vertices."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tricky.stl")
        tris = _box_triangles(5.0, 5.0, 5.0)
        write_binary(p, tris)
        with open(p, "r+b") as f:
            f.seek(0)
            f.write(b"solid exported from something\0")
        m = model.load(p)
        assert m.triangle_count == 12, m.triangle_count
        assert round(m.size.x, 3) == 5.0, m.size


@check
def test_offset_model_is_measured_by_span_not_by_extent():
    """A model sitting away from its own origin has the same footprint. The
    engine centres each layer on the bed and ignores where the model sat."""
    with tempfile.TemporaryDirectory() as d:
        tris = _box_triangles(10.0, 4.0, 2.0, ox=137.0, oy=-62.0, oz=9.0)
        m = model.load(write_binary(os.path.join(d, "far.stl"), tris))
        assert (round(m.size.x, 3), round(m.size.y, 3)) == (10.0, 4.0), m.size
        assert round(m.lower[0], 3) == 137.0, m.lower


@check
def test_flat_model_is_refused():
    """A model with no footprint cannot be sliced, and the engine would divide
    by it rather than say so."""
    with tempfile.TemporaryDirectory() as d:
        tris = _box_triangles(10.0, 0.0, 5.0)
        try:
            model.load(write_binary(os.path.join(d, "flat.stl"), tris))
        except model.ModelError as exc:
            assert "footprint" in str(exc), exc
        else:
            raise AssertionError("a model flat in Y was accepted")


@check
def test_rubbish_is_refused_with_a_readable_error():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "not.stl")
        with open(p, "w") as f:
            f.write("this is prose, not a mesh\n")
        try:
            model.load(p)
        except model.ModelError:
            pass
        else:
            raise AssertionError("a text file was accepted as a model")


@check
def test_truncated_stl_is_refused():
    """A half-copied file must not read as a short mesh."""
    with tempfile.TemporaryDirectory() as d:
        p = write_binary(os.path.join(d, "cut.stl"), _box_triangles(8.0, 8.0, 8.0))
        whole = open(p, "rb").read()
        with open(p, "wb") as f:
            f.write(whole[:len(whole) // 2])
        try:
            model.load(p)
        except model.ModelError:
            pass
        else:
            raise AssertionError("a truncated STL was accepted")


# ── display ─────────────────────────────────────────────────────────────────

@check
def test_display_mesh_is_centred_and_sits_on_the_floor():
    """MeshCanvas orbits the origin, so an uncentred model is framed around its
    own corner. Z is left at zero because the part sits on the bed."""
    with tempfile.TemporaryDirectory() as d:
        tris = _box_triangles(20.0, 10.0, 4.0, ox=50.0, oy=50.0, oz=7.0)
        m = model.load(write_binary(os.path.join(d, "m.stl"), tris))
        v, t = m.display_mesh()
        assert v is not None and len(t) == 12
        assert abs(v[:, 0].min() + 10.0) < 1e-4 and abs(v[:, 0].max() - 10.0) < 1e-4, \
            (v[:, 0].min(), v[:, 0].max())
        assert abs(v[:, 1].min() + 5.0) < 1e-4 and abs(v[:, 1].max() - 5.0) < 1e-4
        assert abs(v[:, 2].min()) < 1e-4 and abs(v[:, 2].max() - 4.0) < 1e-4


@check
def test_display_mesh_does_not_disturb_the_measured_size():
    """Centring must not be done in place: the size is read after it."""
    with tempfile.TemporaryDirectory() as d:
        tris = _box_triangles(20.0, 10.0, 4.0, ox=50.0, oy=50.0)
        m = model.load(write_binary(os.path.join(d, "m.stl"), tris))
        m.display_mesh()
        m.display_mesh()
        assert (round(m.size.x, 3), round(m.size.y, 3)) == (20.0, 10.0), m.size
        assert round(m.lower[0], 3) == 50.0, m.lower


@check
def test_mesh_layout_matches_what_the_canvas_expects():
    """``MeshCanvas.set_mesh`` indexes vertices by the triangle array and
    computes normals itself when given None, so the indices must be in range
    and the arrays the right shapes."""
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "m.stl"),
                                    _box_triangles(6.0, 6.0, 6.0)))
        v, t = m.display_mesh()
        assert v.ndim == 2 and v.shape[1] == 3, v.shape
        assert t.ndim == 2 and t.shape[1] == 3, t.shape
        assert t.min() >= 0 and t.max() < len(v), (t.min(), t.max(), len(v))
        tri = v[t]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        assert np.linalg.norm(fn, axis=1).min() > 0, "a degenerate triangle"


# ── fitting the bed ─────────────────────────────────────────────────────────

@check
def test_a_part_wider_than_the_bed_fails():
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "big.stl"),
                                    _box_triangles(120.0, 20.0, 3.0)))
        status, msg = m.bed_fit(96)
        assert status == "fail", (status, msg)
        assert "will not fit" in msg


@check
def test_a_box_overhanging_the_circle_only_warns():
    """The engine tests each layer's real geometry against the bed circle; this
    has only the bounding box. A 70 mm disc fits the circle and its box does
    not, so refusing on the box would refuse parts that slice perfectly."""
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "sq.stl"),
                                    _box_triangles(70.0, 70.0, 3.0)))
        status, msg = m.bed_fit(96)
        assert status == "warn", (status, msg)
        assert "may overhang" in msg


@check
def test_a_part_inside_the_circle_is_clean():
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "ok.stl"),
                                    _box_triangles(40.0, 40.0, 3.0)))
        assert m.bed_fit(96) == ("ok", ""), m.bed_fit(96)


@check
def test_bed_circle_radius_follows_the_smaller_side():
    """The engine's radius is min(width, height)/2, so a rectangular bed is
    treated as the inscribed circle, not an ellipse."""
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "p.stl"),
                                    _box_triangles(30.0, 30.0, 3.0)))
        assert m.bed_fit(200, 40)[0] == "warn", m.bed_fit(200, 40)
        assert m.bed_fit(200, 200)[0] == "ok"


@check
def test_layer_count():
    with tempfile.TemporaryDirectory() as d:
        m = model.load(write_binary(os.path.join(d, "p.stl"),
                                    _box_triangles(10.0, 10.0, 5.0)))
        assert m.layer_count(0.05) == 100, m.layer_count(0.05)
        assert m.layer_count(0) == 0


# ── PrusaSlicer's own report ────────────────────────────────────────────────

@needs_prusa
def test_prusa_info_agrees_with_our_own_measurement():
    """Two independent measurements of the same file. They must agree, because
    one of them is what the engine is told."""
    with tempfile.TemporaryDirectory() as d:
        p = write_binary(os.path.join(d, "p.stl"), _box_triangles(23.0, 11.0, 4.0))
        ours = model.load(p).size
        theirs = model.prusa_info(p)
        assert abs(theirs["size_x"] - ours.x) < 1e-3, (theirs["size_x"], ours.x)
        assert abs(theirs["size_y"] - ours.y) < 1e-3
        assert abs(theirs["size_z"] - ours.z) < 1e-3
        assert theirs["number_of_facets"] == 12


@needs_prusa
def test_multiple_parts_are_reported():
    with tempfile.TemporaryDirectory() as d:
        tris = (_box_triangles(10.0, 10.0, 3.0)
                + _box_triangles(10.0, 10.0, 3.0, ox=40.0))
        m = model.load(write_binary(os.path.join(d, "two.stl"), tris), inspect=True)
        assert m.info["number_of_parts"] == 2, m.info
        assert any("2 separate parts" in w for w in m.warnings()), m.warnings()


@needs_prusa
def test_a_file_prusaslicer_cannot_read_says_so():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "broken.3mf")
        with open(p, "wb") as f:
            f.write(b"not a zip, not a 3mf\n")
        try:
            model.load(p)
        except model.ModelError as exc:
            assert "PrusaSlicer could not read" in str(exc), exc
        else:
            raise AssertionError("an invalid 3MF was accepted")


def main():
    have = prusa_slicer_available()
    failures = skipped = 0
    for fn in CHECKS:
        if getattr(fn, "needs_prusa", False) and not have:
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
