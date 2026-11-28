"""
slicer/job.py
Running a slice: model file in, G-code file out.

This is the layer between the UI and the vendored engine. It does four things
the engine does not do for itself:

* **Measures the model**, through ``model.py``. The engine never opens the
  model file -- it passes the path to PrusaSlicer and needs to be *told* the
  footprint, because that is what sets the mask's physical scale. Get it wrong
  and every coordinate in the output is wrong by that ratio, silently.
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

from .olos.prusa_slicer import PrusaSlicer
from .olos.shim import ProgressQueue
from . import params
from .model import (Model, ModelError, ModelSize, load, measure_model,
                   model_bounds, read_stl)


class SliceFailed(RuntimeError):
    """Slicing ran but produced nothing usable."""


#: Re-exported, so a caller holding ``job`` need not also import ``model``.
#: The reading itself lives in ``model.py`` because the interface imports that
#: on its own: a file picker and a 3D view should not drag in cv2, shapely and
#: six thousand lines of slicer just to show a part.
__all__ = ["slice_model", "summarise_gcode", "SliceFailed",
           "Model", "ModelError", "ModelSize", "load", "measure_model",
           "model_bounds", "read_stl"]


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
