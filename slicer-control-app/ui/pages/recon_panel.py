"""
ui/pages/recon_panel.py
The Qt side of surface reconstruction: threads, and the parameter panel.

recon/ holds the algorithm and knows nothing about Qt. This module is the only
place the two meet — it runs recon's functions off the GUI thread and turns
their callbacks into signals.

Threading, and why there are two layers of it
---------------------------------------------
Reconstruction runs in a **child process** (recon/isolate.py), because Open3D's
Poisson solver can abort the process from inside its C++ rather than raise. In
this app that would take the machine connection down with it, mid-job. The child
turns that into a message.

A child process alone is not enough, though: waiting on it still blocks whoever
called. So each call also runs on a QThread, and the GUI thread only ever sees a
signal. Nothing here touches widgets from a worker thread.

Every ``run()`` catches everything. An exception escaping ``QThread.run()``
terminates the application, which is not an acceptable outcome for a scan that
would not triangulate.
"""

import contextlib
import sys
import threading

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QSlider, QCheckBox, QProgressBar, QFrame, QSizePolicy,
)

from ui.scale import fs, px, bind

#: The four reconstruction methods, in `method_idx` order — the index the
#: algorithm switches on, so the order is not cosmetic.
METHODS = (
    "Poisson Surface Reconstruction",
    "Ball Pivoting Algorithm (BPA)",
    "B-Spline / NURBS Surface (Smooth Sheet)",
    "Polynomial Surface Fitting",
)

#: key -> (label, min, max, default, scale, suffix)
#:
#: *scale* converts the integer slider position into the value shown to the
#: operator; the algorithm always receives the raw integer, which is what
#: `settings_to_params` expects.
PARAMS = (
    ("knn",           "Normal estimation neighbours (KNN)", 5, 80, 50, 1, ""),
    ("depth",         "Octree depth (resolution)",          5, 10,  5, 1, ""),
    ("trim",          "Artifact removal quantile",          0, 50,  0, 0.01, ""),
    ("voxel",         "Grid uniformity filter (voxel)",     0, 30, 30, 0.1, "x"),
    ("bpa",           "Ball radius scale",                  2, 60, 55, 0.1, "x"),
    ("grid_res",      "Control grid resolution",           10, 120, 40, 1, ""),
    ("spline_smooth", "Spline smoothing",                   1, 100, 10, 1, ""),
    ("poly_degree",   "Polynomial degree",                  1,  5,  2, 1, ""),
)

#: Which controls each method actually consumes. Taken from the source project's
#: `on_method_changed`, which seeds exactly these per method; `voxel` is a
#: pre-filter on the cloud and applies throughout. The rest stay visible but
#: disabled, so the panel does not change shape as the method changes.
METHOD_PARAMS = {
    0: {"knn", "depth", "trim", "voxel"},
    1: {"knn", "bpa", "voxel"},
    2: {"grid_res", "spline_smooth", "voxel"},
    3: {"grid_res", "poly_degree", "voxel"},
}


#: Threads that are still running, keyed by nothing — this exists only to hold a
#: Python reference.
#:
#: A QThread must not be garbage collected, nor destroyed as a QObject child,
#: while its run() is still going: Qt terminates the process for it. Parenting a
#: task to the controller looks tidy and is exactly wrong, because the
#: controller dies with the dialog and a reconstruction outlives both. So tasks
#: are parentless, held here, and drop out when they finish.
_LIVE_TASKS = set()


def _hold(task):
    _LIVE_TASKS.add(task)
    task.finished.connect(lambda: _LIVE_TASKS.discard(task))
    return task


class _Task(QThread):
    """Runs one callable off the GUI thread. Never lets an exception escape."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:                        # pragma: no cover
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _TuneTask(QThread):
    """Auto-tune, with the candidate-by-candidate progress callback re-emitted."""

    progress = Signal(int, int, str)
    done = Signal(object, str)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            results, note = self._fn(self.progress.emit)
            self.done.emit(results, note)
        except Exception as exc:                        # pragma: no cover
            import traceback
            traceback.print_exc()
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ReconController(QObject):
    """Owns the reconstruction child process and the threads that drive it.

    The service is started lazily, on the first reconstruction rather than when
    the viewer opens: a child process plus Open3D is a real cost, and most
    visits to a scan never leave the point view.
    """

    analysed = Signal(dict, dict, list)     # desc, recommended settings, reasons
    mesh_ready = Signal(object, object, object, dict)   # verts, tris, normals, info
    tuned = Signal(object, str)             # results, note
    tune_progress = Signal(int, int, str)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = None
        self._pcd = None
        self._points = None
        self._desc = None
        self._gap = 0.1
        self._task = None
        self._tune = None
        self._closed = False
        self._cancelled = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure_service(self):
        if self._closed:
            raise RuntimeError("the viewer is closing")
        if self._service is None:
            self._service = _clean_service()
            self._service.set_cloud(self._points)
        return self._service

    def _reconstruct_with_fallback(self, settings: dict, seal_base: bool):
        """Reconstruct, stepping Poisson's octree depth down if it keeps dying.

        Poisson's fatal path (``Failed to close loop`` -> exit() in the helper)
        is not a property of the settings alone: on the same cloud and the same
        settings it fires on some runs and not others. ReconstructionService
        already retries once at the same depth. What decides how often both
        tries die is the octree depth -- measured on a 120k-point scan with
        Seal on, 30 runs per depth after that retry: depth 8 never failed,
        depth 9 failed 1 in 10, depth 10 failed 4 in 10.

        So a run that still dies is retried one depth coarser, at most
        ``_DEPTH_FALLBACK_STEPS`` times, and the depth actually used is reported
        in ``info["depth_used"]`` so the panel can say the result is coarser
        than asked -- a lower-resolution surface the operator was told about
        beats an error, and beats a coarser one they were not.

        Only for Poisson (``method_idx`` 0); depth means nothing to the others,
        and any failure that is not the helper dying is raised as it was.
        """
        from recon.workers import settings_to_params
        from recon.isolate import ReconstructionFailed

        service = self._ensure_service()
        asked = settings.get("depth")
        poisson = settings.get("method_idx") == 0 and isinstance(asked, int)
        floor = max(self._DEPTH_FLOOR, (asked or 0) - self._DEPTH_FALLBACK_STEPS)

        tried = dict(settings)
        while True:
            params = settings_to_params(tried, self._gap, seal_base)
            try:
                verts, tris, normals, info = service.reconstruct(params)
            except ReconstructionFailed as exc:
                died = "helper exited" in str(exc)
                if not (poisson and died and tried["depth"] > floor):
                    raise
                tried = dict(tried, depth=tried["depth"] - 1)
                continue
            if poisson and tried["depth"] != asked:
                info = dict(info or {}, depth_used=tried["depth"], depth_asked=asked)
            return verts, tris, normals, info

    def cancel(self):
        """Abandon whatever is running, without waiting for it.

        Kills the helper process, which is the only thing that actually stops a
        reconstruction: it is C++ inside Open3D and there is no check-a-flag
        point to interrupt. The blocked service call then fails fast instead of
        sitting out its ten-minute timeout.
        """
        self._cancelled = True
        service, self._service = self._service, None
        if service is not None:
            try:
                service.abort()
            except Exception:                           # pragma: no cover
                pass
            # The polite close waits on the service lock, which the dying call
            # still holds for a moment. Off the GUI thread, so it cannot freeze
            # the window the way it used to.
            threading.Thread(target=self._quiet_shutdown, args=(service,),
                             daemon=True).start()

    @staticmethod
    def _quiet_shutdown(service):
        try:
            service.shutdown()
        except Exception:                               # pragma: no cover
            pass

    def shutdown(self):
        """Detach from everything still running and return immediately.

        This is called from the dialog's closeEvent, so it must not block. It
        used to, in two places, and both froze the whole application:

        * ``QThread.wait(3000)`` after a ``requestInterruption()`` that nothing
          checked — three seconds per task, for a flag no worker read.
        * ``service.shutdown()``, which takes the same lock ``reconstruct()``
          holds for its entire run. Closing the window during a reconstruction
          therefore waited on it, up to DEFAULT_TIMEOUT — ten minutes of a dead
          GUI. Measured: the close never returned, and the event loop was so
          thoroughly blocked that a watchdog QTimer could not fire either.

        Signals are disconnected first: a task that finishes after the dialog is
        gone would otherwise deliver its result into destroyed widgets. The
        threads themselves are left to unwind on their own and are kept alive by
        _LIVE_TASKS until they do.
        """
        self._closed = True
        for task in (self._task, self._tune):
            if task is None:
                continue
            for sig in ("done", "failed", "progress"):
                s = getattr(task, sig, None)
                if s is not None:
                    try:
                        s.disconnect()
                    except (RuntimeError, TypeError):
                        pass
        tasks = [t for t in (self._task, self._tune) if t is not None]
        self._task = self._tune = None
        self.cancel()

        # A short, bounded wait — not the old three seconds per task for a flag
        # nobody read. cancel() has just killed the helper, so a thread blocked
        # in the service returns within POLL_SECONDS (0.2 s) and this costs
        # nothing. It is here to avoid destroying a QThread mid-run, which Qt
        # answers by terminating the process.
        #
        # It can still time out: analyse() runs its KD-tree in-process, and that
        # is not interruptible. The task then keeps running, held alive by
        # _LIVE_TASKS with its signals already disconnected, and unwinds on its
        # own into nothing.
        for task in tasks:
            if task.isRunning():
                task.wait(600)

    def busy(self) -> bool:
        return bool((self._task and self._task.isRunning())
                    or (self._tune and self._tune.isRunning()))

    # ── steps ────────────────────────────────────────────────────────────────

    def analyse(self, points):
        """Measure the cloud and recommend settings for it.

        *points* is the (N,3) array the viewer already parsed, so the file is
        not read twice and the reconstruction sees exactly what is on screen.
        """
        self._points = points

        def work():
            import numpy as np
            import recon  # noqa: F401 -- before Open3D: one OpenMP runtime, see recon/__init__.py
            import open3d as o3d
            from scipy.spatial import KDTree
            from recon.autoconfig import analyze_shape, recommend_settings

            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=float))
            desc = analyze_shape(pcd)
            # `gap_baseline` is the 95th percentile nearest-neighbour distance —
            # the same measure the source project computes when it loads a file.
            pts = np.asarray(pcd.points)
            tree = KDTree(pts)
            dists, _ = tree.query(pts, k=2, workers=-1)
            gap = float(np.percentile(dists[:, 1], 95)) if len(dists) else 0.1
            settings, reasons = recommend_settings(desc, pcd)
            return pcd, desc, gap, settings, reasons

        def ok(result):
            self._pcd, self._desc, self._gap, settings, reasons = result
            self.analysed.emit(self._desc, settings, list(reasons))

        self.status.emit("Measuring the cloud…")
        self._task = _hold(_Task(work))
        self._task.done.connect(ok)
        self._task.failed.connect(self.failed)
        self._task.start()

    #: How far below the requested octree depth Poisson may fall back, and
    #: never below this. See _reconstruct_with_fallback.
    _DEPTH_FALLBACK_STEPS = 2
    _DEPTH_FLOOR = 5

    def reconstruct(self, settings: dict, seal_base: bool):
        def work():
            return self._reconstruct_with_fallback(settings, seal_base)

        def ok(result):
            verts, tris, normals, info = result
            self.mesh_ready.emit(verts, tris, normals, info)

        self.status.emit("Reconstructing…")
        self._task = _hold(_Task(work))
        self._task.done.connect(ok)
        self._task.failed.connect(self.failed)
        self._task.start()

    def auto_tune(self, settings: dict, seal_base: bool, objective: str = "fidelity"):
        """Score a set of candidate settings and return them ranked."""

        def work(progress):
            from recon.autoconfig import build_candidates, build_smooth_candidates, auto_tune
            service = self._ensure_service()
            builder = build_smooth_candidates if objective == "smooth" else build_candidates
            candidates = builder(self._desc, settings, seal_base=seal_base)
            results, note = auto_tune(self._pcd, self._desc, candidates, self._gap,
                                      seal_base, service, objective=objective,
                                      progress=progress)
            # auto_tune leaves the (possibly decimated) scoring cloud loaded;
            # put the full one back so the next reconstruct is at full fidelity
            service.set_cloud(self._points)
            return results, note

        self.status.emit("Searching candidate settings…")
        self._tune = _hold(_TuneTask(work))
        self._tune.progress.connect(self.tune_progress)
        self._tune.done.connect(self.tuned)
        self._tune.failed.connect(self.failed)
        self._tune.start()


class ReconPanel(QWidget):
    """Method, the eight parameters, and the automatic helpers."""

    reconstruct_requested = Signal(dict, bool)
    tune_requested = Signal(dict, bool, str)
    export_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sliders = {}
        self._value_labels = {}
        self._rows = {}
        self._widgets = []

        self._root = QVBoxLayout(self)

        self._title = QLabel("Surface")
        self._root.addWidget(self._title)

        self._method = QComboBox()
        self._method.addItems(METHODS)
        self._method.currentIndexChanged.connect(self._on_method)
        self._root.addWidget(self._method)

        for key, label, lo, hi, default, scale, suffix in PARAMS:
            row = QWidget()
            rl = QVBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            head = QHBoxLayout()
            name = QLabel(label)
            value = QLabel("")
            value.setAlignment(Qt.AlignRight)
            head.addWidget(name)
            head.addStretch()
            head.addWidget(value)
            rl.addLayout(head)
            s = QSlider(Qt.Horizontal)
            s.setMinimum(lo)
            s.setMaximum(hi)
            s.setValue(default)
            s.valueChanged.connect(self._on_slider)
            rl.addWidget(s)
            self._root.addWidget(row)
            self._sliders[key] = s
            self._value_labels[key] = value
            self._rows[key] = row
            self._widgets += [name, value]

        self._seal = QCheckBox("Seal solid bottom base")
        self._root.addWidget(self._seal)

        self._apply_b = QPushButton("Reconstruct")
        self._apply_b.clicked.connect(
            lambda: self.reconstruct_requested.emit(self.settings(), self.seal_base()))
        self._root.addWidget(self._apply_b)

        self._tune_b = QPushButton("Auto-tune (search candidates)")
        self._tune_b.clicked.connect(
            lambda: self.tune_requested.emit(self.settings(), self.seal_base(), "fidelity"))
        self._root.addWidget(self._tune_b)

        self._smooth_b = QPushButton("Auto-smooth surface")
        self._smooth_b.clicked.connect(
            lambda: self.tune_requested.emit(self.settings(), self.seal_base(), "smooth"))
        self._root.addWidget(self._smooth_b)

        self._cancel_b = QPushButton("Cancel")
        self._cancel_b.clicked.connect(self.cancel_requested)
        self._cancel_b.hide()
        self._root.addWidget(self._cancel_b)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.hide()
        self._root.addWidget(self._progress)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._root.addWidget(self._note)

        self._root.addStretch()

        self._export_b = QPushButton("Export mesh…")
        self._export_b.clicked.connect(self.export_requested)
        self._export_b.setEnabled(False)
        self._root.addWidget(self._export_b)

        self._buttons = [self._apply_b, self._tune_b, self._smooth_b, self._export_b]
        self._all_buttons = self._buttons + [self._cancel_b]
        self._on_method()
        self._on_slider()
        bind(self, self._apply_scale)

    # ── state ────────────────────────────────────────────────────────────────

    def settings(self) -> dict:
        s = {k: sl.value() for k, sl in self._sliders.items()}
        s["method_idx"] = self._method.currentIndex()
        # `reconstruct` reads this to decide whether a height-field shortcut is
        # legitimate for this cloud; it is a measurement, not a preference, so
        # it is carried through from the analysis rather than exposed.
        s["height_field_ok"] = self._height_field_ok
        return s

    def seal_base(self) -> bool:
        return self._seal.isChecked()

    _height_field_ok = True

    def apply_settings(self, settings: dict) -> None:
        """Load a settings dict into the controls without firing a rebuild."""
        self._height_field_ok = bool(settings.get("height_field_ok", True))
        for key, slider in self._sliders.items():
            if key in settings:
                slider.blockSignals(True)
                slider.setValue(int(settings[key]))
                slider.blockSignals(False)
        if "method_idx" in settings:
            self._method.blockSignals(True)
            self._method.setCurrentIndex(int(settings["method_idx"]))
            self._method.blockSignals(False)
        self._on_method()
        self._on_slider()

    def set_busy(self, busy: bool) -> None:
        # Cancel is the only control that lives while busy. Without it a slow
        # reconstruction left every control disabled with no way out but closing
        # the window — which is exactly what people did, and closing used to
        # freeze the app.
        self._cancel_b.setVisible(busy)
        self._cancel_b.setEnabled(busy)
        for b in self._buttons:
            b.setEnabled(not busy)
        if not busy:
            self._export_b.setEnabled(self._has_mesh)
        self._method.setEnabled(not busy)
        for s in self._sliders.values():
            s.setEnabled(not busy)
        if not busy:
            self._on_method()

    _has_mesh = False

    def set_has_mesh(self, has: bool) -> None:
        self._has_mesh = has
        self._export_b.setEnabled(has)

    def set_note(self, text: str) -> None:
        self._note.setText(text)

    def show_progress(self, index: int, total: int, label: str) -> None:
        if index >= total:
            self._progress.hide()
            return
        self._progress.show()
        self._progress.setRange(0, total)
        self._progress.setValue(index)
        self._progress.setFormat(f"{index + 1}/{total}  {label}")

    # ── internals ────────────────────────────────────────────────────────────

    def _on_method(self, *_):
        active = METHOD_PARAMS.get(self._method.currentIndex(), set())
        for key, slider in self._sliders.items():
            on = key in active
            slider.setEnabled(on)
            self._rows[key].setEnabled(on)

    def _on_slider(self, *_):
        for key, label, lo, hi, default, scale, suffix in PARAMS:
            v = self._sliders[key].value() * scale
            if key == "voxel" and self._sliders[key].value() == 0:
                self._value_labels[key].setText("Off")
            elif scale == 1:
                self._value_labels[key].setText(f"{int(v)}{suffix}")
            else:
                self._value_labels[key].setText(f"{v:.2f}{suffix}")

    def _apply_scale(self, *_):
        self._root.setContentsMargins(px(14), px(10), px(14), px(12))
        self._root.setSpacing(px(8))
        self._title.setStyleSheet(
            f"background: transparent; color: #E8EAF0; font-size: {fs(14)}px;")
        for w in self._widgets + [self._note]:
            w.setStyleSheet(
                f"background: transparent; color: #C8CCD4; font-size: {fs(11)}px;")
        self._note.setStyleSheet(
            f"background: transparent; color: #8A8F99; font-size: {fs(10)}px;")
        for b in self._all_buttons:
            b.setMinimumHeight(px(32))
            b.setStyleSheet(f"""
                QPushButton {{ background: #2E2E33; border: none;
                               border-radius: {px(8)}px;
                               font-size: {fs(11)}px; color: #E8EAF0; }}
                QPushButton:hover {{ background: #3D3D44; }}
                QPushButton:disabled {{ color: #6A6E76; }}
            """)
        self._seal.setStyleSheet(
            f"background: transparent; color: #C8CCD4; font-size: {fs(11)}px;")
        self._method.setStyleSheet(
            f"QComboBox {{ background: #2E2E33; color: #E8EAF0; border: none; "
            f"border-radius: {px(6)}px; padding: {px(5)}px; "
            f"font-size: {fs(11)}px; }}")


# ── a helper process with no Qt in it ───────────────────────────────────────
#
# The helper exists so that Poisson can *die* without taking the app with it:
# PoissonRecon's fatal path calls exit() from inside an OpenMP worker thread.
# That only works if exit() can finish -- and it could not. A helper started
# with the "spawn" method re-imports the parent's __main__ to rebuild its
# environment, and this app's main.py imports PySide6 at module level, so the
# helper carried Qt's binding layer. exit() runs C++ static destructors;
# Shiboken's BindingManager destructor calls PyGILState_Ensure; the GIL is held
# by the helper's main thread, which is inside the Open3D call waiting at the
# OpenMP join for the very worker that called exit(). Each waited on the other,
# CPU sat at 0%, and the viewer said "Reconstructing..." until the 600 s
# timeout. Seen with "Seal solid bottom base", which reaches Poisson's fatal
# path on scans the unsealed run gets through.
#
# Spawn decides what to re-import from __main__.__spec__ and __main__.__file__
# (multiprocessing.spawn.get_preparation_data). Hiding both while the helper
# starts means it imports recon.isolate and nothing else: no Qt, no app, and
# an exit() that actually exits -- so a fatal Poisson run is reported as a
# failure in a second instead of hanging.
#
# Frozen builds do not take this path: there the child re-runs the app binary,
# and main.py calls multiprocessing.freeze_support() before importing PySide6
# for the same reason.
#
# A subclass rather than an edit to recon/isolate.py, which tools/sync_recon.py
# re-copies from the source project.

_MISSING = object()


@contextlib.contextmanager
def _main_module_hidden():
    main = sys.modules.get("__main__")
    if main is None or getattr(sys, "frozen", False):
        yield
        return
    saved_file = main.__dict__.pop("__file__", _MISSING)
    saved_spec = getattr(main, "__spec__", None)
    main.__spec__ = None
    try:
        yield
    finally:
        if saved_file is not _MISSING:
            main.__file__ = saved_file
        main.__spec__ = saved_spec


def _clean_service():
    """A ReconstructionService whose helper starts without the app's __main__."""
    from recon.isolate import ReconstructionService

    class _CleanStart(ReconstructionService):
        def _start(self):
            with _main_module_hidden():
                super()._start()

    return _CleanStart()
