# isolate.py
"""Runs reconstruction in a separate process so the app cannot be killed by it.

Open3D's bundled PoissonRecon terminates the whole process from its ERROR_OUT
path - it prints "Failed to close loop ..." and calls exit(), so there is no
exception to catch and no crash report. Any Poisson run can do this depending on
the data and the octree depth, which took the dashboard down mid-session.

A single long lived helper process does the work. If it dies we simply notice,
report the failure and start a new one; the GUI stays up. The helper keeps the
point cloud between calls so estimated normals are still reused exactly as they
were when everything ran in-process.
"""
import multiprocessing as mp
import queue
import threading
import time

import numpy as np

POLL_SECONDS = 0.2
DEFAULT_TIMEOUT = 600.0


def _helper_loop(requests, responses):
    """Child process: hold one point cloud, reconstruct on demand."""
    import open3d as o3d
    from recon.workers import reconstruct

    cloud = {'id': None, 'pcd': None}

    while True:
        message = requests.get()
        if message is None:
            return

        kind, payload = message

        if kind == 'cloud':
            cloud_id, points = payload
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(points)
            cloud['id'], cloud['pcd'] = cloud_id, pcd
            responses.put(('ok', None))

        elif kind == 'run':
            cloud_id, params = payload
            if cloud['id'] != cloud_id:
                responses.put(('stale', None))
                continue
            info = {}
            try:
                mesh = reconstruct(cloud['pcd'], params, info=info)
            except Exception as exc:
                responses.put(('error', str(exc)))
                continue
            if mesh is None:
                responses.put(('error', 'reconstruction returned nothing'))
                continue
            responses.put(('mesh', (
                np.asarray(mesh.vertices),
                np.asarray(mesh.triangles),
                np.asarray(mesh.vertex_normals),
                info,
            )))


class ReconstructionService:
    """Parent side handle. Thread safe; calls are serialised."""

    def __init__(self):
        self._ctx = mp.get_context('spawn')
        self._process = None
        self._requests = None
        self._responses = None
        self._cloud_id = None
        self._points = None
        self._lock = threading.Lock()

    # -- process management

    def _start(self):
        self._requests = self._ctx.Queue()
        self._responses = self._ctx.Queue()
        self._process = self._ctx.Process(
            target=_helper_loop, args=(self._requests, self._responses), daemon=True
        )
        self._process.start()
        self._cloud_id = None       # a fresh helper holds no cloud

    def _ensure_running(self):
        if self._process is None or not self._process.is_alive():
            self._discard()
            self._start()

    def _discard(self):
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2.0)

        # A queue whose reader died still has a feeder thread holding buffered
        # data, and that thread blocks interpreter exit. Cut it loose.
        for q in (self._requests, self._responses):
            if q is not None:
                try:
                    q.cancel_join_thread()
                    q.close()
                except Exception:
                    pass

        self._process = None
        self._requests = None
        self._responses = None
        self._cloud_id = None

    def _await(self, timeout):
        """Wait for a reply, noticing if the helper died instead of answering."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._responses.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if self._process is None or not self._process.is_alive():
                    return ('died', None)
                if time.monotonic() > deadline:
                    self._discard()
                    return ('timeout', None)
            except Exception:
                # The helper can die mid-transfer, which breaks the pipe and
                # surfaces as EOFError / OSError / a truncated pickle. Treat any
                # of it as "the helper is gone" rather than letting it escape.
                self._discard()
                return ('died', None)

    # -- public API

    def set_cloud(self, points):
        """Hand the helper a new cloud. Cheap to call repeatedly with the same one."""
        with self._lock:
            self._points = np.asarray(points)
            self._cloud_id = None       # force a re-send on the next run

    def reconstruct(self, params, timeout=DEFAULT_TIMEOUT):
        """Returns (vertices, triangles, normals, info) or raises ReconstructionFailed.

        `info` carries what the arrays cannot: see workers.reconstruct().
        """
        with self._lock:
            if self._points is None:
                raise ReconstructionFailed("no point cloud loaded")

            for attempt in range(2):        # one retry, in case the helper had died
                try:
                    self._ensure_running()

                    if self._cloud_id is None:
                        token = object().__hash__()
                        self._requests.put(('cloud', (token, self._points)))
                        kind, _ = self._await(timeout)
                        if kind != 'ok':
                            self._discard()
                            continue
                        self._cloud_id = token

                    self._requests.put(('run', (self._cloud_id, params)))
                    kind, payload = self._await(timeout)
                except Exception as exc:
                    # starting or talking to the helper failed outright
                    self._discard()
                    if attempt:
                        raise ReconstructionFailed(f"helper process error: {exc}")
                    continue

                if kind == 'mesh':
                    return payload
                if kind == 'error':
                    raise ReconstructionFailed(payload)
                if kind == 'timeout':
                    raise ReconstructionFailed(f"timed out after {timeout:.0f}s")
                # 'died' or 'stale': drop the helper and try once more
                self._discard()

            raise ReconstructionFailed(
                "the reconstruction helper exited - Open3D's Poisson solver aborts the "
                "process on some inputs; try a different octree depth or method"
            )

    def abort(self):
        """Kill the helper immediately, without taking the lock.

        shutdown() is the polite path and it waits on ``self._lock`` -- which is
        held for the whole of reconstruct(), up to DEFAULT_TIMEOUT (ten
        minutes). So shutdown() cannot interrupt the very thing an interactive
        caller needs to interrupt: a reconstruction in flight.

        This is the interrupt. The helper dies, the blocked ``_await`` notices
        on its next poll and returns ``('died', None)``, and the caller raises
        ReconstructionFailed instead of waiting out the timeout -- which
        releases the lock and lets shutdown() proceed.

        Added for the app's viewer (ui/pages/ply_viewer.py), where closing the
        window mid-reconstruction otherwise froze the whole GUI.
        """
        process = self._process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
            except Exception:
                pass

    def shutdown(self):
        with self._lock:
            if self._process is not None and self._process.is_alive():
                try:
                    self._requests.put(None)
                    self._process.join(timeout=2.0)
                except Exception:
                    pass
            self._discard()


class ReconstructionFailed(RuntimeError):
    pass


def to_mesh(vertices, triangles, normals=None):
    """Rebuild an Open3D mesh from the arrays the helper sent back."""
    import open3d as o3d

    mesh = o3d.geometry.TriangleMesh()
    mesh.vertices = o3d.utility.Vector3dVector(vertices)
    mesh.triangles = o3d.utility.Vector3iVector(triangles)

    if normals is not None and len(normals) == len(vertices):
        mesh.vertex_normals = o3d.utility.Vector3dVector(normals)
        # Open3D's renderer shades from TRIANGLE normals. A mesh carrying only
        # vertex normals draws as a flat silhouette with no lighting at all.
        mesh.compute_triangle_normals()
    else:
        mesh.compute_vertex_normals()

    mesh.paint_uniform_color([0.1, 0.3, 0.9])
    return mesh
