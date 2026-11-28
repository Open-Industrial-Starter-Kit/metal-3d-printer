"""
slicer/job.py
Running a slice: model file in, G-code file out.

This is the layer between the UI and the vendored engine. It does four things
the engine does not do for itself:

* **Reads the model.** The engine never opens the model file -- it passes the
  path to PrusaSlicer and needs to be *told* the footprint, as a
  ``model_size`` object with ``.x``/``.y`` in millimetres, because that is what
  sets the mask's physical scale. Get it wrong and every coordinate in the
  output is wrong by that ratio, silently. So the footprint is measured here.
* **Assembles the parameters.** See ``slicer/params.py``.
* **Reports progress.** The engine pushes ``(layer_index, total_layers)`` into
  a queue per layer; ``ProgressQueue`` turns that into a callback.
* **Checks the output.** A G-code file that exists but holds no motion is the
  failure mode this pipeline has -- an empty mask set, a model off the bed, a
  print-height limit below the first layer -- and it looks like success.

Slicing is CPU-bound and takes tens of seconds to minutes, so nothing here may
be called on the Qt main thread. :func:`slice_model` is deliberately a plain
blocking function with a callback, not a QThread: the UI wraps it in whatever
it likes, and tests call it directly.
"""

import os
import struct

import numpy as np

from .olos.prusa_slicer import PrusaSlicer
from .olos.shim import ProgressQueue
from . import params


class ModelError(ValueError):
    """The model file cannot be read, or holds nothing sliceable."""


class SliceFailed(RuntimeError):
    """Slicing ran but produced nothing usable."""


class ModelSize:
    """The footprint the engine asks for: ``.x`` and ``.y`` in millimetres.

    The engine reads these as attributes, not dict keys, which is why this is a
    class and not a tuple. ``.z`` is carried for the UI's benefit -- it is what
    bounds the layer count -- and the engine never looks at it.
    """

    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __repr__(self):
        return f"ModelSize(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f})"


# ──────────────────────────────────────────────────────────────────────────────
# STL
# ──────────────────────────────────────────────────────────────────────────────
#
# Written out rather than taken from numpy-stl or trimesh, because the whole
# job is 40 lines and both of those are large dependencies for an app that has
# to install on an operator's machine. The triangles are returned as well as
# the bounds, so the model viewer can draw the part without reading the file a
# second time.

def read_stl(path):
    """Return ``(vertices, triangles)`` as float32/int32 arrays.

    Handles both binary and ASCII STL. Raises :class:`ModelError` rather than
    letting a truncated file surface as a struct error.
    """
    if not os.path.isfile(path):
        raise ModelError(f"No such model file: {path}")
    size = os.path.getsize(path)
    if size < 15:
        raise ModelError(f"{os.path.basename(path)} is too small to be an STL.")

    with open(path, "rb") as f:
        head = f.read(84)
        # A binary STL declares its triangle count at byte 80; the file is then
        # exactly 84 + 50n bytes. Checking the arithmetic is more reliable than
        # sniffing for a leading "solid", because plenty of binary STLs start
        # with that word inside their 80-byte comment header.
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


def model_bounds(path):
    """``(min_xyz, max_xyz)`` of the model, in its own file units (mm)."""
    verts, _ = read_stl(path)
    return verts.min(axis=0), verts.max(axis=0)


def measure_model(path):
    """The model's footprint, as the engine wants it."""
    lo, hi = model_bounds(path)
    span = hi - lo
    if span[0] <= 0 or span[1] <= 0:
        raise ModelError(
            f"{os.path.basename(path)} has no footprint "
            f"({span[0]:.3f} x {span[1]:.3f} mm): it is flat in X or Y.")
    return ModelSize(span[0], span[1], span[2])


# ──────────────────────────────────────────────────────────────────────────────
# Slicing
# ──────────────────────────────────────────────────────────────────────────────

def summarise_gcode(path):
    """Count what actually came out, for the output check and for the UI.

    Keyed on the engine's own section markers and on the block structure it
    emits, so this reports the engine's view of the file rather than a guess.
    """
    motion = contours = infills = layers = 0
    laser_on = 0
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith(";"):
                if s.startswith("; contour"):
                    contours += 1
                elif s.startswith("; infill"):
                    infills += 1
                continue
            up = s.upper()
            if up.startswith(("G0", "G1")) and ("X" in up or "Y" in up):
                motion += 1
            if up.startswith("M3") or up.startswith("M4"):
                laser_on += 1
            if "G92" in up and "Z" in up:
                layers += 1
    return {"motion_moves": motion, "contour_sections": contours,
            "infill_sections": infills, "layer_resets": layers,
            "laser_on_commands": laser_on,
            "bytes": os.path.getsize(path)}


def slice_model(model_path, gcode_path, overrides=None, on_progress=None,
                print_height_limit=None, print_height_lower_limit=None):
    """Slice ``model_path`` to ``gcode_path``. Blocking. Returns a summary dict.

    ``on_progress(layer_index, total_layers)`` is called once per layer, from
    this thread.

    The result is checked for motion before returning. The engine will happily
    write a header-and-footer-only file when no layer yielded geometry, and
    that is indistinguishable from success at the filesystem level -- a
    well-formed ``.nc`` of a few hundred bytes that would upload, start, and
    print nothing.
    """
    size = measure_model(model_path)
    data = params.build_slicing_data(
        model_file_path=model_path,
        gcode_file_path=gcode_path,
        model_size=size,
        overrides=overrides,
        print_height_limit=print_height_limit,
        print_height_lower_limit=print_height_lower_limit,
    )

    os.makedirs(os.path.dirname(os.path.abspath(gcode_path)), exist_ok=True)
    PrusaSlicer.slice_model(data, ProgressQueue(on_progress))

    if not os.path.isfile(gcode_path):
        raise SliceFailed(
            f"The engine reported no error but wrote no file to {gcode_path}.")
    summary = summarise_gcode(gcode_path)
    if summary["motion_moves"] == 0:
        raise SliceFailed(
            f"Sliced to {gcode_path} ({summary['bytes']} bytes) but it contains "
            "no XY motion -- nothing would be scanned. Usual causes: the model "
            "sits outside the bed, the layer height exceeds the model height, "
            "or a print-height limit cut every layer.")
    summary["model_size"] = size
    return summary
