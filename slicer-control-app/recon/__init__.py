"""
recon/
Surface reconstruction for 3-D scans, from the `surface_reconstruction project`.

The .ply viewer (ui/pages/ply_viewer.py) shows a scan as points by default and
can reconstruct a surface from the cloud that is already open. This package is
the algorithm side of that: it holds no UI and no Qt.

Why the code is copied and not imported
---------------------------------------
The original modules import **PyQt6**. This app is **PySide6**, and the two sets
of bindings cannot safely share a process — they are separate builds of the same
C++ classes, and loading both gives you two incompatible QObject universes in one
address space. The source project is also a plain folder, not a package or a git
repository, so there is nothing to depend on that would survive a build on
another machine or a PyInstaller bundle.

So the algorithm is vendored here. What changed, and nothing else:

* ``workers.py`` — dropped ``FileLoadWorker`` and ``ReconstructionWorker``. Both
  were QThread wrappers around calls this app makes for itself; the maths
  (``reconstruct``, ``settings_to_params`` and their helpers) is untouched.
* ``autoconfig.py`` — ``AutoTuneWorker`` became :func:`autoconfig.auto_tune`, a
  plain function taking a ``progress(index, total, label)`` callback and
  returning ``(results, note)`` instead of emitting signals. The body, including
  the scoring and the decimation rule, is unchanged.
* ``isolate.py`` — unchanged except the child process now imports
  ``recon.workers`` rather than ``workers``.
* ``utils.py`` — verbatim.

Qt lives entirely in ui/pages/ply_viewer.py, which runs these on PySide6 threads.

Keeping it in step
------------------
The source project remains where the algorithm is developed. When it moves,
re-copy the four files and re-apply the three edits above — they are small and
deliberately mechanical for that reason. ``tools/sync_recon.py`` does it and
reports what it changed.

Reconstruction runs out of process
----------------------------------
Not for parallelism — for survival. Open3D's Poisson solver can abort the whole
process from inside its C++ (not raise: abort), which in this app would take the
machine connection down with it. :class:`isolate.ReconstructionService` holds the
cloud in a child process and returns a message on failure, so the worst case is
a failed reconstruction rather than a dead app mid-job.

One OpenMP runtime, not two
---------------------------
``import open3d`` unconditionally runs ``import open3d.ml``, and Open3D-ML's
dataset modules import scikit-learn, which ships its **own** ``libomp.dylib``.
So every process that imported Open3D ended up with two OpenMP runtimes loaded
side by side -- and Open3D sets ``KMP_DUPLICATE_LIB_OK=True`` on import, which
is the switch that silences libomp's own refusal to run that way.

Two runtimes in one process can deadlock at a parallel barrier, and here they
did: Surface sat on "Reconstructing..." indefinitely with the helper at 0% CPU
and every thread parked in ``kmp_flag_64::wait``. Whether it happens depends on
thread timing, which is why it came and went between runs of the same scan.

Nothing in this package uses Open3D-ML -- reconstruction is geometry and
Poisson / ball pivoting -- so ``open3d.ml`` is replaced with an empty module
before Open3D can import it. scikit-learn then never loads, one ``libomp`` is
left, and the deadlock has nothing to happen between. Verified by mapping the
helper's loaded libraries: two copies before, one after.

This has to run before the first ``import open3d`` in the process, and being in
the package ``__init__`` is how it gets there: every module here imports
Open3D *through* the package, and the helper process is started on
``recon.isolate``, so this file runs first in that process too. The two call
sites outside the package (``ui/pages/recon_panel.py``, ``ui/pages/ply_viewer.py``)
import ``recon`` before Open3D for the same reason.
"""

import sys as _sys
import types as _types

if "open3d" not in _sys.modules and "open3d.ml" not in _sys.modules:
    _sys.modules["open3d.ml"] = _types.ModuleType("open3d.ml")
