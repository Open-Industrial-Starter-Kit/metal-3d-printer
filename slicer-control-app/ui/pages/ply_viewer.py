"""
ui/pages/ply_viewer.py
3-D viewer for .ply scans, with two ways of looking at the same cloud.

**Points** is the default and mirrors the mobile app's WebGL viewer: gold points
on a near-black canvas, orbit/zoom/pan with clamped zoom, and a home button. It
is a software renderer, it needs nothing installed, and it always works.

**Surface** reconstructs a mesh from the cloud that is already open and shades
it (ui/pages/mesh_canvas.py, recon/). There is no file picker on that side by
design: the scan being looked at is the input, so the only question left is how
to reconstruct it, not what to reconstruct.

Surface needs Open3D, and an install without it is expected rather than broken —
`piccola-cnc.spec` says as much when it cannot collect the library. So the toggle
is disabled and says why, instead of being live and reporting a
ModuleNotFoundError from the worker thread. Points needs nothing beyond Qt and
always works.

Points stays the default because it is the honest view. A point cloud is what
the machine measured; a surface is an interpretation of it, and which
interpretation you get depends on the method and eight parameters. The toggle
makes that a deliberate step rather than something the viewer decides.

The two canvases share a camera: toggling hands the current yaw/pitch/distance
across, so the surface appears where the points were rather than snapping to a
default view.
"""

import math

from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QCursor, QPolygonF
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QStackedWidget, QFileDialog, QMessageBox,
)

# While orbiting/zooming, draw at most this many points (full cloud returns
# as soon as the interaction ends).
_LOD_MAX_POINTS = 20_000

from ui.theme import WHITE, TXT
from ui.scale import fs, px, bind
from ui import icons

_BG_C     = QColor("#0B0B0B")          # mobile: renderer.setClearColor(0x0b0b0b)
_POINT_C  = QColor("#E5C158")          # mobile: PointCloudMaterial color 0xe5c158
_GRID_C   = QColor("#26262A")
_FOV_DEG  = 50.0                       # mobile: PerspectiveCamera(50, …)
_MAX_POINTS = 120_000                  # subsample beyond this for interactivity


def parse_ply(path: str):
    """Parse an ASCII .ply file → (points, center, diag, dims).

    Mirrors the mobile parser: skip to end_header, then read 'x y z' per line.
    Points are returned centered on the cloud's bounding-box center.
    """
    pts = []
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    try:
        with open(path, errors="ignore") as fh:
            in_header = True
            for line in fh:
                s = line.strip()
                if in_header:
                    if s == "end_header":
                        in_header = False
                    continue
                if not s:
                    continue
                parts = s.split()
                if len(parts) < 3:
                    continue
                try:
                    x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                except ValueError:
                    continue
                if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                    continue
                pts.append((x, y, z))
                min_x = min(min_x, x); max_x = max(max_x, x)
                min_y = min(min_y, y); max_y = max(max_y, y)
                min_z = min(min_z, z); max_z = max(max_z, z)
    except OSError:
        return [], (0, 0, 0), 1.0, (0, 0, 0)

    if not pts:
        return [], (0, 0, 0), 1.0, (0, 0, 0)

    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    cz = (min_z + max_z) / 2
    dx, dy, dz = max_x - min_x, max_y - min_y, max_z - min_z
    diag = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0

    if len(pts) > _MAX_POINTS:
        step = len(pts) / _MAX_POINTS
        pts = [pts[int(i * step)] for i in range(_MAX_POINTS)]

    centered = [(x - cx, y - cy, z - cz) for x, y, z in pts]
    return centered, (cx, cy, cz), diag, (dx, dy, dz)


#: Whether the reconstruction stack is importable in THIS install.
#:
#: Open3D carries the reconstruction itself and there is no lighter substitute
#: (see requirements.txt), so without it Surface cannot work. It is also the
#: one dependency most likely to be absent: ~284 MB, compiled, and its wheels
#: trail new Python releases by months — a Windows box on a Python it does not
#: publish for gets everything else and not this.
#:
#: Asked once and cached, because the answer cannot change while the app runs
#: and the import is expensive. Nothing here imports Open3D itself: importlib
#: only looks for it.
_HAVE_RECON: bool | None = None


def recon_available() -> bool:
    """True when the surface reconstruction can run in this install."""
    global _HAVE_RECON
    if _HAVE_RECON is None:
        import importlib.util
        try:
            _HAVE_RECON = all(importlib.util.find_spec(m) is not None
                              for m in ("open3d", "numpy", "scipy"))
        except (ImportError, ValueError):       # a broken partial install
            _HAVE_RECON = False
    return _HAVE_RECON


class PlyCanvas(QWidget):
    """Software-rendered orbit view of a centered point cloud (Z up)."""

    def __init__(self, points, diag: float):
        super().__init__()
        self._pts = points
        self._diag = max(diag, 1e-6)
        self.setMinimumSize(px(400), px(300))
        self.setCursor(QCursor(Qt.OpenHandCursor))
        # Don't let unhandled buttons (e.g. the middle button) bubble to the
        # parent — the native fallback crashes on macOS (see Gcode3DCanvas).
        self.setAttribute(Qt.WA_NoMousePropagation, True)

        # Mobile home view: dist = (diag/2)/tan(fov/2) * 1.5, pos = d*(0.55,-0.75,0.6)
        base = (self._diag / 2) / math.tan(math.radians(_FOV_DEG / 2)) * 1.5
        home_vec = (0.55, -0.75, 0.6)
        vlen = math.sqrt(sum(c * c for c in home_vec))
        self._home_dist = base * vlen
        self._home_yaw = math.atan2(home_vec[1], home_vec[0])
        self._home_pitch = math.asin(home_vec[2] / vlen)

        # Zoom clamps (mobile: minDistance diag*0.08, maxDistance diag*8)
        self._min_dist = max(self._diag * 0.08, 0.5)
        self._max_dist = self._diag * 8

        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = self._home_dist
        self._pan = QPointF(0, 0)
        self._last = QPointF()
        self._mode = None          # 'orbit' | 'pan'

        # Level-of-detail: subsampled cloud while interacting
        self._lod = False
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(220)
        self._lod_timer.timeout.connect(self._end_lod)

    def _begin_lod(self):
        self._lod = True
        self._lod_timer.start()

    def _end_lod(self):
        self._lod = False
        self.update()

    # ── view control ─────────────────────────────────────────────────────────

    def reset_view(self):
        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = self._home_dist
        self._pan = QPointF(0, 0)
        self.update()

    def wheelEvent(self, e):
        e.accept()
        factor = 0.88 if e.angleDelta().y() > 0 else 1 / 0.88
        self._dist = max(self._min_dist, min(self._max_dist, self._dist * factor))
        self._begin_lod()
        self.update()

    def mousePressEvent(self, e):
        e.accept()
        self._last = e.position()
        # Left mouse → orbit; right mouse (or shift+left) → pan
        if e.button() == Qt.LeftButton and not (e.modifiers() & Qt.ShiftModifier):
            self._mode = "orbit"
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        elif e.button() in (Qt.RightButton, Qt.MiddleButton, Qt.LeftButton):
            self._mode = "pan"
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseMoveEvent(self, e):
        e.accept()
        if self._mode is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        if self._mode == "orbit":
            self._yaw -= d.x() * 0.008
            self._pitch = max(-1.55, min(1.55, self._pitch + d.y() * 0.008))
        else:
            self._pan += d
        self._begin_lod()
        self.update()

    def mouseReleaseEvent(self, e):
        e.accept()
        self._mode = None
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self._end_lod()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), _BG_C)
        if not self._pts:
            p.setPen(QPen(QColor("#777")))
            p.drawText(self.rect(), Qt.AlignCenter, "No points")
            p.end()
            return

        w, h = self.width(), self.height()
        # Camera basis (orbit around origin, Z up — like the mobile viewer)
        cy_, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        # camera position on the orbit sphere
        ex = self._dist * cp * cy_
        ey = self._dist * cp * sy
        ez = self._dist * sp
        # forward = -eye (towards origin); right/up from Z-up cross products
        flen = math.sqrt(ex * ex + ey * ey + ez * ez) or 1.0
        fx, fy, fz = -ex / flen, -ey / flen, -ez / flen
        # right = forward × up(0,0,1)
        rx, ry, rz = fy * 1 - fz * 0, fz * 0 - fx * 1, fx * 0 - fy * 0
        rlen = math.sqrt(rx * rx + ry * ry + rz * rz) or 1.0
        rx, ry, rz = rx / rlen, ry / rlen, rz / rlen
        # true up = right × forward
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx

        focal = (h / 2) / math.tan(math.radians(_FOV_DEG / 2))
        cx_px = w / 2 + self._pan.x()
        cy_px = h / 2 + self._pan.y()

        # Ground grid (XY plane through the cloud centre), drawn first
        p.setPen(QPen(_GRID_C, 1))
        gext = self._diag * 0.75
        gstep = max(gext / 6, 1e-3)
        gi = -gext
        while gi <= gext + 1e-9:
            for (x0, y0, x1, y1) in ((gi, -gext, gi, gext), (-gext, gi, gext, gi)):
                pts2 = []
                ok = True
                for (gx, gy) in ((x0, y0), (x1, y1)):
                    dxc = gx - ex
                    dyc = gy - ey
                    dzc = 0 - ez
                    zc = dxc * fx + dyc * fy + dzc * fz
                    if zc <= 1e-6:
                        ok = False
                        break
                    xc = dxc * rx + dyc * ry + dzc * rz
                    yc = dxc * ux + dyc * uy + dzc * uz
                    pts2.append(QPointF(cx_px + xc / zc * focal,
                                        cy_px - yc / zc * focal))
                if ok:
                    p.drawLine(pts2[0], pts2[1])
            gi += gstep

        # Points (gold, fixed pixel size — like the mobile viewer).
        # Subsample while interacting so orbiting stays smooth on big clouds.
        pts = self._pts
        if self._lod and len(pts) > _LOD_MAX_POINTS:
            stride = (len(pts) + _LOD_MAX_POINTS - 1) // _LOD_MAX_POINTS
            pts = pts[::stride]
        pen = QPen(_POINT_C)
        pen.setWidth(3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        out = []
        append = out.append
        for (x, y, z) in pts:
            dxc = x - ex
            dyc = y - ey
            dzc = z - ez
            zc = dxc * fx + dyc * fy + dzc * fz
            if zc <= 1e-6:
                continue
            xc = dxc * rx + dyc * ry + dzc * rz
            yc = dxc * ux + dyc * uy + dzc * uz
            append(QPointF(cx_px + xc / zc * focal, cy_px - yc / zc * focal))
        if out:
            p.drawPoints(QPolygonF(out))
        p.end()


class PlyViewerDialog(QDialog):
    """Popup point-cloud viewer: info header, home button, close."""

    def __init__(self, file_name: str, path: str, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(file_name)
        self.resize(px(960), px(640))
        self.setStyleSheet("QDialog { background: #0B0B0B; }")

        points, _center, diag, dims = parse_ply(path)

        self._root = QVBoxLayout(self)

        # Overlay header: info left, home + close right
        self._head = QHBoxLayout()
        self._info = QLabel(
            f"{file_name}   ·   {len(points):,} points   ·   "
            f"{dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm")
        self._head.addWidget(self._info)
        self._head.addStretch()

        self._chips: list[QPushButton] = []

        def _chip(text: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            self._chips.append(b)
            return b

        self._canvas = PlyCanvas(points, diag)

        # Points | Surface. A checkable chip rather than two buttons: there are
        # exactly two states and one is the default, so a toggle says that and a
        # pair of buttons would not.
        self._mode_b = _chip("Surface")
        self._mode_b.setCheckable(True)
        self._mode_b.toggled.connect(self._on_mode)
        self._head.addWidget(self._mode_b)
        # Disabled, with the reason, rather than live and failing: the thread
        # that does the reconstruction reports what it caught, so without this
        # the operator clicked Surface and got
        # "ModuleNotFoundError: No module named 'open3d'" — a true sentence
        # that tells them nothing they can act on.
        if recon_available():
            self._mode_b.setToolTip("Reconstruct a surface from these points")
        else:
            self._mode_b.setEnabled(False)
            self._mode_b.setToolTip(
                "Surface reconstruction is not available in this build — it "
                "needs the Open3D library, which is not installed here. The "
                "point view is unaffected.")

        # Icon-only home button (same house icon as the app's homing button)
        self._home_b = _chip()
        self._home_b.setToolTip("Reset view")
        self._home_b.clicked.connect(self._reset_view)
        self._head.addWidget(self._home_b)
        self._root.addLayout(self._head)

        # Surface view and its panel are built on first use — importing Open3D
        # costs seconds and most visits never leave the point view.
        self._mesh_canvas = None
        self._panel = None
        self._recon = None
        self._mesh = None                # (verts, tris) of the last result

        self._stack = QStackedWidget()
        self._stack.addWidget(self._canvas)
        self._body = QHBoxLayout()
        self._body.addWidget(self._stack, 1)
        self._root.addLayout(self._body, 1)

        self._hint = QLabel("Left mouse: orbit   ·   Right mouse: pan   ·   "
                            "Scroll wheel: zoom")
        self._hint.setAlignment(Qt.AlignCenter)
        self._root.addWidget(self._hint)

        self._points = points
        self._diag = diag

        bind(self, self._apply_scale)

    # ── mode switching ───────────────────────────────────────────────────────

    def _reset_view(self):
        self._current_canvas().reset_view()

    def _current_canvas(self):
        w = self._stack.currentWidget()
        return w if w is not None else self._canvas

    def _on_mode(self, surface: bool) -> None:
        if surface:
            self._ensure_surface()
            # Hand the camera across so the surface appears where the points
            # were, rather than snapping back to the home view.
            self._mesh_canvas.set_view_state(
                (self._canvas._yaw, self._canvas._pitch,
                 self._canvas._dist, self._canvas._pan))
            self._stack.setCurrentWidget(self._mesh_canvas)
            self._panel.show()
            if self._mesh is None and not self._analysed:
                self._recon.analyse(self._points)
        else:
            if self._mesh_canvas is not None:
                y, p_, d, pan = self._mesh_canvas.view_state()
                self._canvas._yaw, self._canvas._pitch = y, p_
                self._canvas._dist, self._canvas._pan = d, pan
                self._canvas.update()
            self._stack.setCurrentWidget(self._canvas)
            if self._panel is not None:
                self._panel.hide()
        self._update_hint()

    _analysed = False

    def _ensure_surface(self) -> None:
        if self._mesh_canvas is not None:
            return
        from ui.pages.mesh_canvas import MeshCanvas
        from ui.pages.recon_panel import ReconController, ReconPanel

        self._mesh_canvas = MeshCanvas(self._diag)
        self._mesh_canvas.set_message("Measuring the cloud…")
        self._stack.addWidget(self._mesh_canvas)

        self._panel = ReconPanel()
        self._panel.setFixedWidth(px(300))
        self._body.addWidget(self._panel)

        self._recon = ReconController(self)
        self._recon.analysed.connect(self._on_analysed)
        self._recon.mesh_ready.connect(self._on_mesh)
        self._recon.tuned.connect(self._on_tuned)
        self._recon.tune_progress.connect(self._panel.show_progress)
        self._recon.status.connect(self._on_status)
        self._recon.failed.connect(self._on_failed)

        self._panel.reconstruct_requested.connect(self._on_reconstruct)
        self._panel.tune_requested.connect(self._on_tune)
        self._panel.export_requested.connect(self._on_export)
        self._panel.cancel_requested.connect(self._on_cancel)

    # ── reconstruction flow ──────────────────────────────────────────────────

    def _on_status(self, text: str) -> None:
        self._recon._cancelled = False
        self._panel.set_busy(True)
        self._mesh_canvas.set_message(text)
        self._update_hint(text)

    #: Failures that mean "the library is not usable here", not "this cloud
    #: could not be reconstructed". Open3D is compiled, and on Windows it fails
    #: the second way as often as the first: the module is present and importing
    #: it raises a DLL load error, which `find_spec` cannot see. Both come back
    #: from the worker thread as a raw exception string.
    _IMPORT_FAILURES = ("ModuleNotFoundError", "ImportError", "DLL load failed",
                        "No module named")

    def _on_failed(self, text: str) -> None:
        if self._recon is None or self._recon._cancelled:
            # the helper was killed on purpose; _on_cancel already said so
            return
        if any(m in text for m in self._IMPORT_FAILURES):
            # Say what it means, and keep the original: whoever has to fix the
            # install needs the exact error, and the operator needs the
            # sentence above it.
            text = ("Surface reconstruction is not available in this build — "
                    "the Open3D library did not load.\n\n" + text)
            self._mode_b.setEnabled(False)
            self._mode_b.setToolTip(
                "Surface reconstruction is not available in this build")
        self._panel.set_busy(False)
        self._panel.set_note(text)
        if not self._mesh_canvas.has_mesh():
            self._mesh_canvas.set_message(text)
        self._update_hint()

    def _on_analysed(self, desc, settings, reasons) -> None:
        self._analysed = True
        self._panel.apply_settings(settings)
        self._panel.set_note("\n".join(reasons))
        self._panel.set_busy(False)
        # The measurement is the only input the operator did not choose, so the
        # first reconstruction runs on it straight away rather than waiting for
        # a click on settings they have not seen a result for yet.
        self._recon.reconstruct(self._panel.settings(), self._panel.seal_base())

    def _on_cancel(self) -> None:
        """Give up on the running job and hand the controls back."""
        self._recon.cancel()
        self._panel.set_busy(False)
        self._panel.set_note("Cancelled.")
        if not self._mesh_canvas.has_mesh():
            self._mesh_canvas.set_message("Cancelled")
        self._update_hint()

    def _on_reconstruct(self, settings, seal) -> None:
        self._recon.reconstruct(settings, seal)

    def _on_tune(self, settings, seal, objective) -> None:
        self._recon.auto_tune(settings, seal, objective)

    def _on_mesh(self, verts, tris, normals, info) -> None:
        self._mesh = (verts, tris)
        self._mesh_canvas.set_mesh(verts, tris, normals)
        self._panel.set_busy(False)
        self._panel.set_has_mesh(True)
        note = f"{len(tris):,} triangles"
        if info.get("depth_used") is not None:
            # Poisson kept dying at the depth asked for and the panel fell back
            # (ReconController._reconstruct_with_fallback): say so, because the
            # surface is coarser than the slider claims.
            note += (f"   ·   built at octree depth {info['depth_used']} — "
                     f"depth {info.get('depth_asked')} failed in Poisson")
        if info.get("seal_error"):
            note += f"   ·   not sealed: {info['seal_error']}"
        self._panel.set_note(note)
        self._update_hint()

    def _on_tuned(self, results, note) -> None:
        self._panel.set_busy(False)
        self._panel.show_progress(1, 1, "")
        best = next((r for r in results if r.get("error") is None), None)
        if best is None:
            self._panel.set_note(note or "No candidate reconstructed cleanly.")
            return
        self._panel.apply_settings(best["settings"])
        m = best["metrics"]
        self._panel.set_note(
            f"Best: {best['label']}   ·   rms {m['rms']:.3f}   ·   "
            f"coverage {m['coverage']:.0%}   ·   {m['triangles']:,} triangles"
            + (f"\n{note}" if note else ""))
        self._recon.reconstruct(best["settings"], self._panel.seal_base())

    def _on_export(self) -> None:
        if self._mesh is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export reconstructed mesh", "surface.ply",
            "PLY mesh (*.ply);;STL mesh (*.stl);;OBJ mesh (*.obj)")
        if not path:
            return
        if not recon_available():
            QMessageBox.warning(
                self, "Export failed",
                "Writing a mesh needs the Open3D library, which is not "
                "installed in this build.")
            return
        try:
            import recon  # noqa: F401 -- before Open3D: one OpenMP runtime, see recon/__init__.py
            import open3d as o3d
            from recon.isolate import to_mesh
            mesh = to_mesh(self._mesh[0], self._mesh[1], None)
            mesh.compute_vertex_normals()
            if not o3d.io.write_triangle_mesh(path, mesh):
                raise RuntimeError("Open3D refused to write the file")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self._panel.set_note(f"Exported to {path}")

    # ── housekeeping ─────────────────────────────────────────────────────────

    def _update_hint(self, override: str | None = None) -> None:
        if override:
            self._hint.setText(override)
            return
        self._hint.setText("Left mouse: orbit   ·   Right mouse: pan   ·   "
                           "Scroll wheel: zoom")

    def closeEvent(self, e):
        """Stop the helper process before the window goes.

        Reconstruction outlives the dialog otherwise: the child process would
        keep a copy of the cloud and a CPU core, and a running QThread would
        outlive the widgets its signals point at.
        """
        if self._recon is not None:
            self._recon.shutdown()
        super().closeEvent(e)

    def _apply_scale(self, *_):
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._head.setContentsMargins(px(14), px(10), px(14), 0)
        self._head.setSpacing(px(8))
        self._info.setStyleSheet(
            f"background: transparent; color: #C8CCD4; font-size: {fs(12)}px;")

        for b in self._chips:
            # The mode toggle carries a word, so it gets a width; the rest are
            # icon-only squares.
            if b is self._mode_b:
                b.setFixedSize(px(96), px(40))
            else:
                b.setFixedSize(px(40), px(40))
            b.setStyleSheet(f"""
                QPushButton {{ background: #2E2E33; border: none;
                               border-radius: {px(8)}px;
                               font-size: {fs(12)}px; color: #E8EAF0; }}
                QPushButton:hover {{ background: #3D3D44; }}
                QPushButton:checked {{ background: #E5C158; color: #1A1A1E; }}
            """)
        self._home_b.setIcon(icons.icon("home", px(24), "#E8EAF0"))

        self._hint.setStyleSheet(
            f"background: transparent; color: #5A5E66; "
            f"font-size: {fs(11)}px; padding: {px(6)}px;")
