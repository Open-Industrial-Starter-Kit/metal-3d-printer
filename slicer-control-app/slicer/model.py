"""
slicer/model.py
Reading a model file: its footprint, its triangles, and whether it fits.

Separate from ``job.py`` for one concrete reason: importing the engine costs
``cv2``, ``shapely`` and six thousand lines of slicer, and the interface needs
none of that to put a file in a list and draw it. A file picker and a 3D view
import this module and pull in nothing but numpy.

Three jobs:

* **The footprint.** The engine never opens the model file -- it hands the path
  to PrusaSlicer and is *told* the size, which sets the mask's physical scale.
  A wrong number there scales every coordinate in the output and raises
  nothing, so this is measured, from the file, every time.
* **The triangles**, for the model view, in the layout ``MeshCanvas`` wants.
* **Whether it fits the bed**, up front. The engine checks this too, from
  ``_validate_region_on_bed``, but it does so part-way through a slice -- after
  PrusaSlicer has run and layers are being traced. An operator should be told
  before the wait, not during it.

STL is read here. The other formats PrusaSlicer accepts -- 3MF, OBJ, AMF -- are
measured by asking PrusaSlicer itself, which costs a subprocess but is exactly
right, and means the file dialog can offer what the engine can actually slice.
Those arrive without triangles, so they get no 3D preview; the alternative was
either refusing files the engine handles perfectly well, or writing three more
mesh parsers to draw them.
"""

import os
import re
import struct
import subprocess

import numpy as np


class ModelError(ValueError):
    """The model file cannot be read, or holds nothing sliceable."""


#: Extensions the engine can slice. STL is parsed here; the rest are measured
#: by PrusaSlicer and shown without a preview.
NATIVE_EXTENSIONS = (".stl",)
OTHER_EXTENSIONS = (".3mf", ".obj", ".amf")
SLICEABLE_EXTENSIONS = NATIVE_EXTENSIONS + OTHER_EXTENSIONS

#: For a Qt file dialog.
FILE_FILTER = ("Models (*.stl *.3mf *.obj *.amf);;"
               "STL (*.stl);;3MF (*.3mf);;OBJ (*.obj);;AMF (*.amf);;"
               "All files (*)")


class ModelSize:
    """The footprint the engine asks for: ``.x`` and ``.y`` in millimetres.

    The engine reads these as attributes, not dict keys, which is why this is a
    class and not a tuple. ``.z`` is carried for the interface's benefit -- it
    is what bounds the layer count -- and the engine never looks at it.
    """

    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __repr__(self):
        return f"ModelSize(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f})"

    def __eq__(self, other):
        return (isinstance(other, ModelSize)
                and (self.x, self.y, self.z) == (other.x, other.y, other.z))


# ──────────────────────────────────────────────────────────────────────────────
# STL
# ──────────────────────────────────────────────────────────────────────────────
#
# Written out rather than taken from numpy-stl or trimesh: the whole job is
# forty lines, and both are sizeable dependencies for an app that has to install
# on an operator's machine. Triangles come back as well as bounds, so the model
# view does not read the file a second time.

def read_stl(path):
    """Return ``(vertices, triangles)`` as float32/int32 arrays.

    Handles binary and ASCII STL, and raises :class:`ModelError` rather than
    letting a truncated file surface as a struct error.
    """
    if not os.path.isfile(path):
        raise ModelError(f"No such model file: {path}")
    size = os.path.getsize(path)
    if size < 15:
        raise ModelError(f"{os.path.basename(path)} is too small to be an STL.")

    with open(path, "rb") as f:
        head = f.read(84)
        # A binary STL declares its triangle count at byte 80, and the file is
        # then exactly 84 + 50n bytes. Checking that arithmetic is more reliable
        # than sniffing for a leading "solid": plenty of binary STLs carry that
        # word inside their 80-byte comment header.
        if len(head) == 84:
            (count,) = struct.unpack("<I", head[80:84])
            if count and size == 84 + 50 * count:
                f.seek(84)
                raw = np.frombuffer(f.read(50 * count), dtype=np.uint8)
                raw = raw.reshape(count, 50)
                # bytes 12..48 of each record are the three vertices
                verts = raw[:, 12:48].copy().view("<f4").reshape(count * 3, 3)
                tris = np.arange(count * 3, dtype=np.int32).reshape(count, 3)
                return verts.astype(np.float32), tris

        f.seek(0)
        text = f.read().decode("utf-8", "replace")

    coords = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            try:
                coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                raise ModelError(
                    f"{os.path.basename(path)}: unreadable vertex {line.strip()!r}")
    if not coords or len(coords) % 3:
        raise ModelError(
            f"{os.path.basename(path)} is neither a valid binary nor ASCII STL "
            f"({len(coords)} vertices found).")
    verts = np.asarray(coords, dtype=np.float32)
    tris = np.arange(len(coords), dtype=np.int32).reshape(-1, 3)
    return verts, tris


# ──────────────────────────────────────────────────────────────────────────────
# Other formats, measured by PrusaSlicer
# ──────────────────────────────────────────────────────────────────────────────

_INFO_FIELDS = ("size_x", "size_y", "size_z", "min_x", "min_y", "min_z",
                "number_of_facets", "number_of_parts", "manifold", "volume")


def prusa_info(path, timeout=120):
    """Ask PrusaSlicer to describe a model. Returns a dict of its own fields.

    ``--info`` reports size, bounds, facet count, part count and whether the
    mesh is manifold -- everything needed to measure a format this module does
    not parse, and a validity check worth having for one it does.
    """
    from .olos.binary import resolve_prusa_slicer

    proc = subprocess.run([resolve_prusa_slicer(), "--info", path],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    info = {}
    for name in _INFO_FIELDS:
        m = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", out, re.M)
        if m:
            value = m.group(1)
            if name in ("manifold",):
                info[name] = value.strip().lower() in ("yes", "true", "1")
            elif name in ("number_of_facets", "number_of_parts"):
                try:
                    info[name] = int(float(value))
                except ValueError:
                    pass
            else:
                try:
                    info[name] = float(value)
                except ValueError:
                    pass
    if "size_x" not in info:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ModelError(
            f"PrusaSlicer could not read {os.path.basename(path)}.\n"
            + (detail[-1] if detail else "It reported nothing."))
    return info


# ──────────────────────────────────────────────────────────────────────────────
# A loaded model
# ──────────────────────────────────────────────────────────────────────────────

class Model:
    """A model file that has been read: its size, and its mesh if we have one."""

    def __init__(self, path, size, lower, triangles=None, vertices=None,
                 info=None):
        self.path = path
        self.size = size
        #: The model's own minimum corner, which the engine ignores -- it
        #: centres each layer's region on the bed regardless of where the model
        #: sat in its file.
        self.lower = lower
        self._tris = triangles
        self._verts = vertices
        #: PrusaSlicer's own report, where one was needed. ``None`` for an STL,
        #: which is measured here without launching anything.
        self.info = info

    # ── identity ────────────────────────────────────────────────────────────

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def triangle_count(self):
        if self._tris is not None:
            return len(self._tris)
        return (self.info or {}).get("number_of_facets", 0)

    @property
    def has_mesh(self):
        return self._tris is not None and len(self._tris) > 0

    @property
    def diagonal(self):
        """The bounding box's diagonal, which frames the camera."""
        s = self.size
        return float(np.sqrt(s.x ** 2 + s.y ** 2 + s.z ** 2)) or 1.0

    # ── display ─────────────────────────────────────────────────────────────

    def display_mesh(self):
        """``(vertices, triangles)`` centred on the origin in X and Y, sitting
        on z=0, for :class:`ui.pages.mesh_canvas.MeshCanvas`.

        Centring is not cosmetic: the canvas orbits the origin, so a model that
        sits at 0..20 in its file would be framed around its own corner. It
        also matches what the engine does -- it centres each layer on the bed
        and ignores where the model sat.
        """
        if not self.has_mesh:
            return None, None
        v = np.array(self._verts, dtype=np.float32, copy=True)
        v[:, 0] -= self.lower[0] + self.size.x / 2.0
        v[:, 1] -= self.lower[1] + self.size.y / 2.0
        v[:, 2] -= self.lower[2]
        return v, self._tris

    # ── checks ──────────────────────────────────────────────────────────────

    def bed_fit(self, bed_width, bed_height=None):
        """``(status, message)`` for this model on a bed of that size, where
        status is ``"ok"``, ``"warn"`` or ``"fail"``.

        The engine checks this too, in ``_validate_region_on_bed``, and raises
        *part-way through a slice* -- after PrusaSlicer has run and layers are
        being traced. An operator should hear it before the wait.

        It treats the bed as a circle of radius ``min(width, height) / 2``,
        because this machine's bed is round. The important difference is that
        the engine tests each layer's **actual geometry** against that circle,
        and all this has is the bounding box. A 70 mm disc on a 96 mm bed fits
        the circle perfectly and has a bounding box whose corners do not, so
        refusing on the box would refuse parts that slice fine. Hence the
        middle status: the box overhanging is a reason to warn, never to stop.
        """
        w = float(bed_width)
        h = float(bed_height) if bed_height else w
        if self.size.x > w or self.size.y > h:
            return "fail", (
                f"{self.name} is {self.size.x:.1f} x {self.size.y:.1f} mm and the "
                f"bed is {w:.0f} x {h:.0f} mm. It will not fit however it is placed.")

        half_diag = float(np.hypot(self.size.x, self.size.y)) / 2.0
        radius = min(w, h) / 2.0
        if half_diag > radius:
            return "warn", (
                f"{self.name} may overhang the round bed. Centred, the corners of "
                f"its {self.size.x:.1f} x {self.size.y:.1f} mm footprint reach "
                f"{half_diag:.1f} mm from the middle, and the bed radius is "
                f"{radius:.1f} mm. If the part is round or tapered its actual "
                f"geometry may still fit -- the slice will say so, and will stop "
                f"with an error if it does not.")
        return "ok", ""

    def layer_count(self, layer_height):
        """How many layers this model will take, for an estimate before slicing."""
        if layer_height <= 0:
            return 0
        return max(1, int(round(self.size.z / float(layer_height))))

    def warnings(self):
        """Anything worth saying about the file before it is sliced."""
        out = []
        info = self.info or {}
        if info.get("manifold") is False:
            out.append(
                f"{self.name} is not a closed mesh. PrusaSlicer will repair it, "
                f"and the repair decides what the inside of the part is -- check "
                f"the layer preview before printing.")
        parts = info.get("number_of_parts")
        if parts and parts > 1:
            out.append(
                f"{self.name} contains {parts} separate parts. They will all be "
                f"scanned, on the same bed, at the same settings.")
        return out

    def __repr__(self):
        return f"Model({self.name!r}, {self.size!r}, {self.triangle_count} tris)"


def load(path, inspect=False):
    """Read a model file. Returns a :class:`Model`.

    An STL is parsed here, giving a mesh for the viewer. Any other sliceable
    format is measured by PrusaSlicer and comes back without one.

    ``inspect`` also runs PrusaSlicer over an STL, which costs a subprocess and
    buys the manifold and part-count checks that :meth:`Model.warnings` reports.
    """
    if not os.path.isfile(path):
        raise ModelError(f"No such model file: {path}")
    ext = os.path.splitext(path)[1].lower()

    if ext in NATIVE_EXTENSIONS or ext not in SLICEABLE_EXTENSIONS:
        try:
            verts, tris = read_stl(path)
        except ModelError:
            if ext in NATIVE_EXTENSIONS:
                raise
            # An unknown extension that is not an STL either: let PrusaSlicer
            # decide, rather than refusing a file it might well handle.
            return _load_via_prusa(path)
        lower = verts.min(axis=0)
        upper = verts.max(axis=0)
        span = upper - lower
        if span[0] <= 0 or span[1] <= 0:
            raise ModelError(
                f"{os.path.basename(path)} has no footprint "
                f"({span[0]:.3f} x {span[1]:.3f} mm): it is flat in X or Y.")
        info = prusa_info(path) if inspect else None
        return Model(path, ModelSize(*span), tuple(float(c) for c in lower),
                     triangles=tris, vertices=verts, info=info)

    return _load_via_prusa(path)


def _load_via_prusa(path):
    info = prusa_info(path)
    size = ModelSize(info["size_x"], info["size_y"], info.get("size_z", 0.0))
    if size.x <= 0 or size.y <= 0:
        raise ModelError(
            f"{os.path.basename(path)} has no footprint "
            f"({size.x:.3f} x {size.y:.3f} mm).")
    lower = (info.get("min_x", 0.0), info.get("min_y", 0.0), info.get("min_z", 0.0))
    return Model(path, size, lower, info=info)


# ── kept for callers that only want the number ──────────────────────────────

def model_bounds(path):
    """``(min_xyz, max_xyz)`` of the model, in millimetres."""
    m = load(path)
    lo = np.asarray(m.lower, dtype=float)
    return lo, lo + np.array([m.size.x, m.size.y, m.size.z])


def measure_model(path):
    """The model's footprint, as the engine wants it."""
    return load(path).size
