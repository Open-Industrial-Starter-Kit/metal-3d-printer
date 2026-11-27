"""
ui/height_map_view.py
Shows the height map the machine measured, as a 3-D surface you can turn.

Why
---
The height map is a laser scan of the bed used to compensate Z during a job, and
until now it was a number: *143 points scanned*. Whether those points describe
the surface actually under the tool, or a stray reading, or a tilt nobody
intended, could only be found out by cutting. This draws it, so the map can be
checked before it is trusted — the operator sees what will be compensated, and
firmware work on the scan has something to look at.

It reads, and never writes. Nothing here can start a scan, clear a map or move
the machine.

The vertical scale is a lie, and says so
----------------------------------------
The whole point of a height map is that it is nearly flat: hundreds of
millimetres across, a few hundredths deep. Drawn true to scale it is a
featureless sheet — the one rendering guaranteed to teach the operator nothing.
So Z is exaggerated, and the factor is always written on screen.

It opens on a **fit**: whatever factor makes this map's range about a fifth of
its own width. That is the right default for looking at one map, and the wrong
one for comparing two, because it draws every map the same height — a bed that
looks alarming at x1200 is flatter than one that looks mild at x24. So the
factor is a slider, and pinning it to a number makes two scans comparable by
eye. The readout says which of the two it is showing, since "x240" and
"x240 (fit)" mean different things: the second will be a different number on the
next map.

The fit is the opening state and has no button of its own — moving the slider
leaves it, and reopening the map returns to it. `set_z_factor(None)` is still
how the model expresses it, and `set_points` uses that, so a button (or
`reset view`, which today only moves the camera) could restore it in one line if
it turns out to be wanted.

The slider is logarithmic. The automatic fit lands near x24 on a 1 mm-deep map
and near x1200 on a 0.02 mm one, so a linear control would spend nine tenths of
its travel in the range where nothing visibly changes.

**reset scale** is the slider's far-left stop, 1:1, and has its own button
because it is the honest cross-check: at true scale an ordinary map is a flat
sheet, and that is how small the correction really is. It resets to life size,
not to the opening fit — those are different destinations, and the readout names
whichever one is in force.

The grid comes from the coordinates, not the scan order
-------------------------------------------------------
`getHeightMapPoints` returns points in scan order, and the firmware says that
order is serpentine — row 0 runs +X, row 1 runs -X. Rows like that cannot be
reshaped into a grid by counting. Every point carries its own `x` and `y`, so
the grid is rebuilt by clustering those instead: the distinct X values become
the columns, the distinct Y values the rows, and each point drops into the cell
it names. Serpentine, row-major or shuffled all give the same picture, and a map
with a hole in it draws the quads it can rather than a wrong surface.

The coordinates are RELATIVE to the corner the scan started from: `x` runs
0, 2, 4 … and `y` runs 0, -2, -4 … at the fine pitch. (`heightMapCoordinateSpace`
said `machine` in an earlier revision of the firmware document, in error.) So
the axis triad is drawn at the true origin when the numbers straddle zero —
that corner is where the operator parked the end mill — and falls back to the
low corner for a map from a firmware that really did send machine coordinates.

Some of the surface may never have been measured
------------------------------------------------
Since the firmware moved to a line scan, each cell is the trimmed mean of tens
of readings, and a cell that collected fewer than five is left empty and filled
from its neighbours afterwards. `heightMapCellsInterpolated` counts those, and
the firmware explicitly asks the frontend to warn about it rather than "showing
a smooth-looking map that was partly fabricated". So a non-zero count gets its
own line under the map, in amber. It cannot mark WHICH cells — the points reply
does not say — so it names the count and leaves the operator to distrust the
whole surface, which is the honest reading of what is known.

Rendered in software, deliberately
----------------------------------
`ui/pages/mesh_canvas.py` records what a `QOpenGLWidget` does to the
`QWebEngineView` in the same window — it leaves the web view painting black,
which would take out both CAM tabs — and works around it with an offscreen
context. A height map is at most a few hundred quads, so it needs none of that:
QPainter draws it back-to-front and the dialog cannot break anything else in the
app, on any driver.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QTimer, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QCursor, QPolygonF, QBrush, QLinearGradient,
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton, QSlider,
)

from ui.theme import WHITE, TXT, TXT2, TXT3, BORDER, PANEL, WARN_C
from ui.scale import fs, px, bind, font
from ui import icons

# ── Fetching ────────────────────────────────────────────────────────────────

#: Points per request. The firmware defaults to 25 and clamps anything larger
#: to keep the reply inside its JSON buffer, so this is a preference, not a
#: promise: paging advances by `heightMapPointsReturned`, whatever comes back.
PAGE_SIZE = 40

#: How long to wait for a page before giving up. Generous: the reply queues
#: behind whatever else the machine is doing.
PAGE_TIMEOUT_MS = 8000

# ── Rendering ───────────────────────────────────────────────────────────────

_BG_C     = QColor("#0B0B0B")     # matches the point-cloud viewer
_GRID_C   = QColor("#26262A")
_POINT_C  = QColor("#FFFFFF")
_EDGE_C   = QColor(255, 255, 255, 40)
_FOV_DEG  = 50.0

#: Blue (low) through green to red (high) — the usual reading of a height map,
#: and the ends stay distinguishable when the range is only hundredths of a mm.
_RAMP = (
    (0.00, (49, 84, 168)),
    (0.25, (60, 168, 196)),
    (0.50, (86, 186, 106)),
    (0.75, (226, 196, 110)),
    (1.00, (226, 92, 78)),
)

#: X red, Y green, Z blue — the convention every CAM tool uses.
_AXIS_C = {"X": QColor("#E05C4E"), "Y": QColor("#56BA6A"), "Z": QColor("#5A9FD4")}

#: The measured range is drawn this fraction of the map's width when the
#: vertical scale is left on its automatic fit.
_Z_SHARE = 0.2

#: What the exaggeration slider spans. Logarithmic, because the useful range
#: covers three decades — the automatic fit lands near x24 on a 1 mm-deep map
#: and near x1200 on a 0.02 mm one, and a linear slider would spend nine tenths
#: of its travel above x500 where nothing changes.
#:
#: The ceiling is half what it was (5000). Past a couple of thousand the
#: surface is mostly the sensor's own noise stretched into scenery — a 0.02 mm
#: ripple drawn 50 mm tall — which reads as a bed fault that is not there. The
#: cap binds the automatic fit too, so a very flat map opens flatter than the
#: usual fifth of its width rather than being inflated to fill the frame.
Z_FACTOR_MIN = 1.0
Z_FACTOR_MAX = 2500.0

#: Slider steps per decade. 100 gives about 1% per step, which is finer than
#: the eye can see and keeps the readout from jumping.
_Z_STEPS_PER_DECADE = 100


def factor_to_slider(factor: float) -> int:
    """Exaggeration -> slider position (log10, clamped to the ends)."""
    f = max(Z_FACTOR_MIN, min(Z_FACTOR_MAX, float(factor)))
    return int(round(math.log10(f) * _Z_STEPS_PER_DECADE))


def slider_to_factor(pos: int) -> float:
    """Slider position -> exaggeration."""
    return 10.0 ** (pos / _Z_STEPS_PER_DECADE)


def ramp_color(t: float) -> QColor:
    """Colour for a normalised height *t* (0…1), linear between ramp stops."""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    for i in range(len(_RAMP) - 1):
        t0, c0 = _RAMP[i]
        t1, c1 = _RAMP[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return QColor(round(c0[0] + (c1[0] - c0[0]) * f),
                          round(c0[1] + (c1[1] - c0[1]) * f),
                          round(c0[2] + (c1[2] - c0[2]) * f))
    return QColor(*_RAMP[-1][1])


def _cluster(values, want=None):
    """Group nearly-equal coordinates into the columns (or rows) they name.

    The coordinates are where the machine *actually got to*, so two passes over
    the same column differ in the last decimal or two. Grouping them is
    therefore not optional — ungrouped, a 5 mm pitch with 0.02 mm of wobble
    produces three columns per column and the surface comes out shredded.

    *want*, when the machine has told us how many there are
    (`heightMapGridPointsX` / `Y`), is the exact answer: split the sorted values
    at the ``want - 1`` largest gaps and there is nothing to tune.

    Without it, the split is at a quarter of the LARGEST gap. Not the smallest,
    which is the bug this replaced: the smallest gap on a jittered scan is the
    jitter, so the tolerance derived from it is smaller than the wobble it is
    supposed to absorb. The largest gap is the pitch (or a multiple of it where
    the scan skipped), and a quarter of that separates real columns while
    swallowing any wobble. It would merge two clusters of points scanned far
    apart, which is not a shape this firmware produces: a height map is one
    rectangular raster.
    """
    vals = sorted(values)
    if not vals:
        return []
    gaps = [(b - a, i) for i, (a, b) in enumerate(zip(vals, vals[1:]))
            if b - a > 1e-9]
    if want and want > 1 and len(gaps) >= want - 1:
        cuts = {i for _g, i in sorted(gaps, reverse=True)[:want - 1]}
        splitter = lambda i: i in cuts
    else:
        tol = (max(g for g, _ in gaps) * 0.25) if gaps else 1e-6
        splitter = lambda i: (vals[i + 1] - vals[i]) > tol
    out, cur = [], [vals[0]]
    for i in range(len(vals) - 1):
        if splitter(i):
            out.append(sum(cur) / len(cur))
            cur = []
        cur.append(vals[i + 1])
    out.append(sum(cur) / len(cur))
    return out


def build_grid(points, nx: int | None = None, ny: int | None = None):
    """Reshape scanned points into a grid, from their coordinates.

    *points* is a sequence of ``(x, y, value)`` in machine millimetres; *nx* and
    *ny* are the grid dimensions the machine reported, when they are known.
    Returns ``(xs, ys, cells)``: the column and row coordinates in ascending
    order, and ``cells[(ix, iy)] = value`` for every point that landed.
    """
    if not points:
        return [], [], {}

    xs = _cluster([p[0] for p in points], nx)
    ys = _cluster([p[1] for p in points], ny)

    def nearest(seq, v):
        best, bi = None, 0
        for i, s in enumerate(seq):
            d = abs(s - v)
            if best is None or d < best:
                best, bi = d, i
        return bi

    cells = {}
    for (x, y, v) in points:
        cells[(nearest(xs, x), nearest(ys, y))] = v
    return xs, ys, cells


class HeightMapCanvas(QWidget):
    """Orbit view of a height-map grid: coloured surface, points, axes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(px(520), px(360))
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self.setMouseTracking(True)
        # Unhandled buttons must not bubble to the parent — the native
        # fallback crashes on macOS (see Gcode3DCanvas).
        self.setAttribute(Qt.WA_NoMousePropagation, True)

        self._pts: list[tuple[float, float, float]] = []
        self._xs: list[float] = []
        self._ys: list[float] = []
        self._cells: dict[tuple[int, int], float] = {}
        self._nx: int | None = None
        self._ny: int | None = None
        self._relative = False
        self._zmin = self._zmax = 0.0
        self._span = 1.0
        self._z_override: float | None = None   # None = follow auto_z_factor
        self._show_points = True
        self._hover = None          # (x, y, value) under the pointer

        self._yaw = math.radians(-55)
        self._pitch = math.radians(32)
        self._dist = 1.0
        self._pan = QPointF(0, 0)
        self._last = QPointF()
        self._mode = None

    # ── data ────────────────────────────────────────────────────────────────

    def set_points(self, points, nx: int | None = None,
                   ny: int | None = None, relative: bool = False) -> None:
        """Show *points*; *nx*/*ny* are the machine's own grid dimensions when
        it has reported them, which makes the reshape exact. *relative* says
        the coordinates are offsets from the scan-start corner rather than
        machine positions, which is what the firmware sends today."""
        pts = [(float(x), float(y), float(v)) for (x, y, v) in points]
        # Showing the SAME map again is not a new map. The dialog re-runs this
        # when a status reply arrives carrying grid dimensions, and
        # connection.py polls `getHeightMapStatus` every ten seconds as a
        # backstop for the footer indicator — so an unconditional reset here
        # threw away the operator's camera, and their pinned exaggeration with
        # it, every ten seconds while they were looking at the map.
        same = (pts == self._pts and (nx, ny) == (self._nx, self._ny)
                and bool(relative) == self._relative)
        self._pts = pts
        self._nx, self._ny = nx, ny
        self._relative = bool(relative)
        if not same:
            self._z_override = None  # a new map is shown at its own best fit
        self._xs, self._ys, self._cells = build_grid(self._pts, nx, ny)
        if self._pts:
            zs = [p[2] for p in self._pts]
            self._zmin, self._zmax = min(zs), max(zs)
            w = (max(p[0] for p in self._pts) - min(p[0] for p in self._pts))
            d = (max(p[1] for p in self._pts) - min(p[1] for p in self._pts))
            self._span = max(w, d, 1e-6)
        else:
            self._zmin = self._zmax = 0.0
            self._span = 1.0
        if same:
            self.update()        # metadata may have moved the axes, nothing else
        else:
            self.reset_view()

    @property
    def auto_z_factor(self) -> float:
        """The factor that makes the measured range :data:`_Z_SHARE` of the
        map's width — what the view opens on.

        Capped at :data:`Z_FACTOR_MAX`, so an unusually flat map is drawn
        flatter than a fifth of its width instead of having its noise inflated
        to fill the frame. Without the cap the fit could also land past the
        slider's right stop, leaving the readout and the slider disagreeing.
        """
        rng = self._zmax - self._zmin
        if rng <= 1e-9:
            return 1.0
        return min((self._span * _Z_SHARE) / rng, Z_FACTOR_MAX)

    @property
    def z_factor(self) -> float:
        """How much taller than life the surface is drawn."""
        return self.auto_z_factor if self._z_override is None \
            else self._z_override

    def set_z_factor(self, factor: float | None) -> None:
        """Pin the vertical exaggeration, or pass None to follow the map's own
        best fit again."""
        if factor is not None:
            factor = max(Z_FACTOR_MIN, min(Z_FACTOR_MAX, float(factor)))
        self._z_override = factor
        self.update()

    @property
    def is_auto_z(self) -> bool:
        return self._z_override is None

    @property
    def z_range(self) -> tuple[float, float]:
        return self._zmin, self._zmax

    @property
    def grid_size(self) -> tuple[int, int]:
        return len(self._xs), len(self._ys)

    def set_show_points(self, on: bool) -> None:
        self._show_points = bool(on)
        self.update()

    # ── view control ────────────────────────────────────────────────────────

    def reset_view(self) -> None:
        self._yaw = math.radians(-55)
        self._pitch = math.radians(32)
        # 1.45, not the 2.4 a point cloud wants: a height map is a single
        # sheet with nothing around it, so there is nothing to leave room for
        # and a distant camera just wastes the window.
        base = (self._span / 2) / math.tan(math.radians(_FOV_DEG / 2))
        self._dist = base * 1.45
        self._pan = QPointF(0, 0)
        self.update()

    def wheelEvent(self, e):
        e.accept()
        factor = 0.88 if e.angleDelta().y() > 0 else 1 / 0.88
        lo = max(self._span * 0.15, 1e-3)
        self._dist = max(lo, min(self._span * 12, self._dist * factor))
        self.update()

    def mousePressEvent(self, e):
        e.accept()
        self._last = e.position()
        if e.button() == Qt.LeftButton and not (e.modifiers() & Qt.ShiftModifier):
            self._mode = "orbit"
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        else:
            self._mode = "pan"
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseMoveEvent(self, e):
        e.accept()
        if self._mode is None:
            self._update_hover(e.position())
            return
        d = e.position() - self._last
        self._last = e.position()
        if self._mode == "orbit":
            self._yaw -= d.x() * 0.008
            self._pitch = max(-1.55, min(1.55, self._pitch + d.y() * 0.008))
        else:
            self._pan += d
        self.update()

    def mouseReleaseEvent(self, e):
        e.accept()
        self._mode = None
        self.setCursor(QCursor(Qt.OpenHandCursor))

    def leaveEvent(self, e):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(e)

    def _update_hover(self, pos) -> None:
        """Name the point under the pointer — this is a debugging view, and the
        first question about a suspicious bump is which reading made it."""
        cam = self._camera()
        best, bp = None, None
        for (x, y, v) in self._pts:
            sp = self._project(cam, x, y, v)
            if sp is None:
                continue
            d = (sp.x() - pos.x()) ** 2 + (sp.y() - pos.y()) ** 2
            if best is None or d < best:
                best, bp = d, (x, y, v)
        hit = bp if (best is not None and best <= px(14) ** 2) else None
        if hit != self._hover:
            self._hover = hit
            self.update()

    # ── projection ──────────────────────────────────────────────────────────

    def _centre(self):
        if not self._pts:
            return 0.0, 0.0, 0.0
        cx = (max(p[0] for p in self._pts) + min(p[0] for p in self._pts)) / 2
        cy = (max(p[1] for p in self._pts) + min(p[1] for p in self._pts)) / 2
        cz = (self._zmax + self._zmin) / 2
        return cx, cy, cz

    def _camera(self):
        """Eye position and basis, orbiting the map centre with Z up."""
        cy_, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        ex = self._dist * cp * cy_
        ey = self._dist * cp * sy
        ez = self._dist * sp
        flen = math.sqrt(ex * ex + ey * ey + ez * ez) or 1.0
        fx, fy, fz = -ex / flen, -ey / flen, -ez / flen
        rx, ry, rz = fy, -fx, 0.0
        rlen = math.sqrt(rx * rx + ry * ry + rz * rz) or 1.0
        rx, ry, rz = rx / rlen, ry / rlen, rz / rlen
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx
        focal = (self.height() / 2) / math.tan(math.radians(_FOV_DEG / 2))
        return {
            "e": (ex, ey, ez), "f": (fx, fy, fz), "r": (rx, ry, rz),
            "u": (ux, uy, uz), "focal": focal,
            "cx": self.width() / 2 + self._pan.x(),
            "cy": self.height() / 2 + self._pan.y(),
            "o": self._centre(), "zf": self.z_factor,
        }

    def _project(self, cam, x, y, z):
        """World (machine mm, Z exaggerated) to screen, or None if behind."""
        ox, oy, oz = cam["o"]
        ex, ey, ez = cam["e"]
        dx = (x - ox) - ex
        dy = (y - oy) - ey
        dz = (z - oz) * cam["zf"] - ez
        fx, fy, fz = cam["f"]
        zc = dx * fx + dy * fy + dz * fz
        if zc <= 1e-6:
            return None
        rx, ry, rz = cam["r"]
        ux, uy, uz = cam["u"]
        xc = dx * rx + dy * ry + dz * rz
        yc = dx * ux + dy * uy + dz * uz
        return QPointF(cam["cx"] + xc / zc * cam["focal"],
                       cam["cy"] - yc / zc * cam["focal"])

    def _depth(self, cam, x, y, z):
        ox, oy, oz = cam["o"]
        ex, ey, ez = cam["e"]
        dx = (x - ox) - ex
        dy = (y - oy) - ey
        dz = (z - oz) * cam["zf"] - ez
        fx, fy, fz = cam["f"]
        return dx * fx + dy * fy + dz * fz

    def _norm(self, v: float) -> float:
        rng = self._zmax - self._zmin
        return 0.5 if rng <= 1e-9 else (v - self._zmin) / rng

    # ── painting ────────────────────────────────────────────────────────────

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), _BG_C)
        if not self._pts:
            p.setPen(QPen(QColor("#777")))
            p.setFont(font(11))
            p.drawText(self.rect(), Qt.AlignCenter, "No height map points")
            p.end()
            return

        cam = self._camera()
        self._draw_floor(p, cam)
        self._draw_surface(p, cam)
        if self._show_points:
            self._draw_points(p, cam)
        self._draw_axes(p, cam)
        self._draw_hover(p, cam)
        p.end()

    def _draw_floor(self, p, cam):
        """A grid on the Z=zmin plane, so the surface has something to sit
        over and the eye can tell tilt from curvature."""
        if not self._xs or not self._ys:
            return
        p.setPen(QPen(_GRID_C, 1))
        x0, x1 = self._xs[0], self._xs[-1]
        y0, y1 = self._ys[0], self._ys[-1]
        z = self._zmin
        for i in range(0, len(self._xs)):
            a = self._project(cam, self._xs[i], y0, z)
            b = self._project(cam, self._xs[i], y1, z)
            if a and b:
                p.drawLine(a, b)
        for j in range(0, len(self._ys)):
            a = self._project(cam, x0, self._ys[j], z)
            b = self._project(cam, x1, self._ys[j], z)
            if a and b:
                p.drawLine(a, b)

    def _draw_surface(self, p, cam):
        """Quads, painted back to front.

        A painter's algorithm rather than a depth buffer: the cells form a
        single-valued grid seen from outside, so sorting by centroid depth is
        exact for this shape and costs nothing.
        """
        quads = []
        for ix in range(len(self._xs) - 1):
            for iy in range(len(self._ys) - 1):
                c = [self._cells.get((ix, iy)), self._cells.get((ix + 1, iy)),
                     self._cells.get((ix + 1, iy + 1)), self._cells.get((ix, iy + 1))]
                if any(v is None for v in c):
                    continue        # a hole in the scan is left as a hole
                xs = (self._xs[ix], self._xs[ix + 1], self._xs[ix + 1], self._xs[ix])
                ys = (self._ys[iy], self._ys[iy], self._ys[iy + 1], self._ys[iy + 1])
                mx = sum(xs) / 4, sum(ys) / 4, sum(c) / 4
                quads.append((self._depth(cam, *mx), xs, ys, c, mx[2]))
        quads.sort(key=lambda q: -q[0])
        pen = QPen(_EDGE_C, 1)
        for _d, xs, ys, c, mz in quads:
            poly = []
            for k in range(4):
                sp = self._project(cam, xs[k], ys[k], c[k])
                if sp is None:
                    poly = None
                    break
                poly.append(sp)
            if not poly:
                continue
            p.setBrush(QBrush(ramp_color(self._norm(mz))))
            p.setPen(pen)
            p.drawPolygon(QPolygonF(poly))
        p.setBrush(Qt.NoBrush)

    def _draw_points(self, p, cam):
        pen = QPen(_POINT_C)
        pen.setWidth(max(2, px(3)))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        out = []
        for (x, y, v) in self._pts:
            sp = self._project(cam, x, y, v)
            if sp is not None:
                out.append(sp)
        if out:
            p.drawPoints(QPolygonF(out))

    def axis_origin(self) -> tuple[float, float]:
        """Where the X/Y/Z triad is planted.

        The corner the scan started from, which in the firmware's `relative`
        space is literally (0, 0): x runs 0, 2, 4 … and y runs 0, -2, -4 … away
        from it (README 7.31). Putting the triad there means the axes mark
        where the operator parked the end mill, not an arbitrary corner of the
        bounding box.

        Falls back to the low corner when the coordinates do not straddle zero,
        which is what a machine-space map from an older firmware looks like —
        planting the axes at an absolute (0, 0) hundreds of millimetres off the
        map would be worse than useless.
        """
        if not self._xs or not self._ys:
            return 0.0, 0.0
        ox = 0.0 if self._xs[0] <= 0.0 <= self._xs[-1] else self._xs[0]
        oy = 0.0 if self._ys[0] <= 0.0 <= self._ys[-1] else self._ys[0]
        return ox, oy

    def _draw_axes(self, p, cam):
        """X / Y / Z from the map's low corner, each labelled at its tip."""
        if not self._xs or not self._ys:
            return
        ox, oy = self.axis_origin()
        oz = self._zmin
        # Each arm is a share of its own axis's extent. A shared length looks
        # wrong on an oblong map, and on a 13x2 strip the Y arm would be longer
        # than the map it labels.
        # Arms point INTO the map from wherever the origin sits, so on a
        # relative map (y all negative) the Y arm follows the scan rather than
        # sticking out into empty space behind it.
        xspan = self._xs[-1] - self._xs[0]
        yspan = self._ys[-1] - self._ys[0]
        xext = max(xspan * 0.38, self._span * 0.12)
        yext = max(yspan * 0.38, self._span * 0.12)
        if ox > (self._xs[0] + self._xs[-1]) / 2:
            xext = -xext
        if oy > (self._ys[0] + self._ys[-1]) / 2:
            yext = -yext
        zrng = self._zmax - self._zmin
        zext = (zrng if zrng > 1e-9 else self._span * 0.02) * 1.4
        axes = (("X", (ox + xext, oy, oz)),
                ("Y", (ox, oy + yext, oz)),
                ("Z", (ox, oy, oz + zext)))
        base = self._project(cam, ox, oy, oz)
        if base is None:
            return
        p.setFont(font(11, bold=True))
        for name, (tx, ty, tz) in axes:
            tip = self._project(cam, tx, ty, tz)
            if tip is None:
                continue
            col = _AXIS_C[name]
            # A dark halo under each arm, so it stays legible where it crosses
            # the surface it is labelling.
            p.setPen(QPen(QColor(0, 0, 0, 160), max(4, px(5))))
            p.drawLine(base, tip)
            p.setPen(QPen(col, max(2, px(3))))
            p.drawLine(base, tip)
            # the letter sits just past the tip, pushed along the axis
            dx, dy = tip.x() - base.x(), tip.y() - base.y()
            n = math.sqrt(dx * dx + dy * dy) or 1.0
            lp = QPointF(tip.x() + dx / n * px(12), tip.y() + dy / n * px(12))
            box = QRectF(lp.x() - px(10), lp.y() - px(9), px(20), px(18))
            # the letter gets the same halo treatment: an axis tip often lands
            # on the pale end of the ramp, where coloured text disappears
            p.setPen(QPen(QColor(0, 0, 0, 190)))
            for ox_, oy_ in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(box.translated(ox_, oy_), Qt.AlignCenter, name)
            p.setPen(QPen(col))
            p.drawText(box, Qt.AlignCenter, name)

    def _draw_hover(self, p, cam):
        if self._hover is None:
            return
        x, y, v = self._hover
        sp = self._project(cam, x, y, v)
        if sp is None:
            return
        p.setPen(QPen(QColor("#FFFFFF"), max(1, px(1))))
        p.setBrush(Qt.NoBrush)
        r = px(5)
        p.drawEllipse(sp, r, r)
        text = "%sX %.2f   Y %.2f   Z %+.4f mm" % (
            "from scan start:  " if self._relative else "", x, y, v)
        p.setFont(font(10))
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + px(12)
        h = fm.height() + px(8)
        bx = min(max(sp.x() + px(10), 0), self.width() - w)
        by = min(max(sp.y() - h - px(8), 0), self.height() - h)
        box = QRectF(bx, by, w, h)
        p.setBrush(QBrush(QColor(0, 0, 0, 200)))
        p.setPen(QPen(QColor(255, 255, 255, 60)))
        p.drawRoundedRect(box, px(4), px(4))
        p.setPen(QPen(QColor("#FFFFFF")))
        p.drawText(box, Qt.AlignCenter, text)


class _Legend(QWidget):
    """The colour ramp with the measured range written against it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lo = self._hi = 0.0
        self.setFixedWidth(px(96))

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bar = QRectF(px(6), px(18), px(16), max(px(40), self.height() - px(44)))
        g = QLinearGradient(bar.topLeft(), bar.bottomLeft())
        for t, _c in _RAMP:
            g.setColorAt(1.0 - t, ramp_color(t))
        p.fillRect(bar, QBrush(g))
        p.setPen(QPen(QColor(BORDER)))
        p.drawRect(bar)
        p.setFont(font(9))
        p.setPen(QPen(QColor(TXT2)))
        p.drawText(QRectF(bar.right() + px(6), bar.top() - px(6),
                          px(64), px(16)),
                   Qt.AlignLeft | Qt.AlignVCenter, "%+.4f" % self._hi)
        p.drawText(QRectF(bar.right() + px(6), bar.bottom() - px(10),
                          px(64), px(16)),
                   Qt.AlignLeft | Qt.AlignVCenter, "%+.4f" % self._lo)
        p.drawText(QRectF(px(6), px(2), self.width() - px(12), px(14)),
                   Qt.AlignLeft | Qt.AlignVCenter, "mm")
        p.end()


class HeightMapDialog(QDialog):
    """Fetches the stored height map and shows it. Read-only."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._points: list[tuple[float, float, float]] = []
        self._total = 0
        self._next = 0
        self._space = ""
        self._gx: int | None = None
        self._gy: int | None = None
        self._quality: dict = {}
        self._job_rect = None            # the opened file's MIN/MAX, if it has any
        self._done = False
        self._syncing = False

        self.setWindowTitle("Height map")
        self.setModal(False)
        self.resize(px(880), px(620))

        self._root = QVBoxLayout(self)

        head = QHBoxLayout()
        self._title = QLabel("Height map")
        head.addWidget(self._title)
        head.addStretch()
        self._close_b = QPushButton("Close")
        self._close_b.clicked.connect(self.close)
        head.addWidget(self._close_b)
        self._root.addLayout(head)

        self._status = QLabel("Reading the map from the machine…")
        self._root.addWidget(self._status)

        self._quality_lbl = QLabel("")
        self._quality_lbl.setWordWrap(True)
        self._quality_lbl.hide()
        self._root.addWidget(self._quality_lbl)

        self._coverage_lbl = QLabel("")
        self._coverage_lbl.setWordWrap(True)
        self._coverage_lbl.hide()
        self._root.addWidget(self._coverage_lbl)

        body = QHBoxLayout()
        self._canvas = HeightMapCanvas()
        body.addWidget(self._canvas, 1)
        self._legend = _Legend()
        body.addWidget(self._legend)
        self._root.addLayout(body, 1)

        tools = QHBoxLayout()
        self._scale_b = QPushButton("Reset scale")
        self._scale_b.setToolTip(
            "Put Z back to 1:1 — its real size. A height map is hundredths of a "
            "millimetre deep over hundreds wide, so this looks flat, which is "
            "the honest picture of how small the correction is.")
        self._scale_b.clicked.connect(lambda: self._set_factor(1.0))
        tools.addWidget(self._scale_b)

        self._z_slider = QSlider(Qt.Horizontal)
        self._z_slider.setRange(factor_to_slider(Z_FACTOR_MIN),
                                factor_to_slider(Z_FACTOR_MAX))
        self._z_slider.setValue(factor_to_slider(1.0))
        self._z_slider.setToolTip("Vertical exaggeration")
        self._z_slider.setFixedWidth(px(180))
        self._z_slider.valueChanged.connect(self._on_slider)
        tools.addWidget(self._z_slider)

        self._pts_b = QPushButton("Points")
        self._pts_b.setCheckable(True)
        self._pts_b.setChecked(True)
        self._pts_b.toggled.connect(self._canvas.set_show_points)
        tools.addWidget(self._pts_b)
        self._reset_b = QPushButton("Reset view")
        self._reset_b.clicked.connect(self._canvas.reset_view)
        tools.addWidget(self._reset_b)
        tools.addStretch()
        self._info = QLabel("")
        tools.addWidget(self._info)
        self._root.addLayout(tools)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(PAGE_TIMEOUT_MS)
        self._timeout.timeout.connect(self._on_timeout)

        bind(self, self._apply_scale)

        conn.height_map_points_received.connect(self._on_page)
        conn.height_map_status_changed.connect(self._on_status_reply)
        conn.opened_file_bounds_received.connect(self._on_job_bounds)
        conn.get("getHeightMapStatus")
        # Where the toolpath sits. `fileList` carries sizes only, and the
        # firmware refuses this for a file with no MIN/MAX rather than letting
        # the position be guessed from them — so a missing answer stays missing.
        conn.get("getOpenedFileBounds")
        self._request(0)

    # ── fetching ────────────────────────────────────────────────────────────

    def _request(self, start: int) -> None:
        self._next = start
        self._timeout.start()
        self._conn.get("getHeightMapPoints",
                       heightMapPointsStartIndex=start,
                       heightMapPointsMaxCount=PAGE_SIZE)

    #: Fields the line scan reports about its own quality (README 7.30). They
    #: are absent on a legacy-grid build, where `heightMapLineScan` is false.
    def _quality_bits(self) -> list[str]:
        q = self._quality
        out = []
        pitch = q.get("heightMapSpacingMm")
        if pitch is not None:
            try:
                out.append("pitch %.2f mm" % float(pitch))
            except (TypeError, ValueError):
                pass
        spread = q.get("heightMapMaxCellSpreadMm")
        if spread is not None:
            try:
                out.append("noisiest cell %.3f mm" % float(spread))
            except (TypeError, ValueError):
                pass
        least = q.get("heightMapMinCellSamples")
        if least is not None:
            try:
                out.append("fewest samples in a cell %d" % int(least))
            except (TypeError, ValueError):
                pass
        return out

    def _on_job_bounds(self, p: dict) -> None:
        try:
            self._job_rect = (float(p["fileMinXmm"]), float(p["fileMaxXmm"]),
                              float(p["fileMinYmm"]), float(p["fileMaxYmm"]))
        except (KeyError, TypeError, ValueError):
            self._job_rect = None
        self._update_coverage()

    def _on_status_reply(self, p: dict) -> None:
        # The reported grid makes the reshape exact. It is a separate request,
        # so it can land after the points -- in which case the surface is
        # rebuilt with it rather than left on the fallback.
        for key, attr in (("heightMapGridPointsX", "_gx"),
                          ("heightMapGridPointsY", "_gy")):
            try:
                v = int(p.get(key, 0) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                setattr(self, attr, v)
        # Only when the dimensions actually changed: this fires on every
        # status snapshot, and the map is static once loaded.
        if (self._done and self._points and (self._gx or self._gy)
                and (self._gx, self._gy) != (self._canvas._nx,
                                             self._canvas._ny)):
            self._canvas.set_points(self._points, self._gx, self._gy,
                                    relative=self._space == "relative")

        for key in ("heightMapSpacingMm", "heightMapMaxCellSpreadMm",
                    "heightMapMinCellSamples", "heightMapSamplesTaken",
                    "heightMapCellsInterpolated", "heightMapLineScan",
                    "heightMapOriginXmm", "heightMapOriginYmm",
                    "heightMapGridPointsX", "heightMapGridPointsY",
                    "heightMapCoversOpenedJob"):
            if key in p:
                self._quality[key] = p[key]
        self._update_quality()

        bound = str(p.get("heightMapBoundFile") or "").strip()
        density = str(p.get("heightMapDensity") or "").strip()
        bits = [b for b in (bound, density and "%s scan" % density) if b]
        self._title.setText("Height map" + (" — " + " · ".join(bits) if bits else ""))

    def _map_rect(self):
        """The map's rectangle in JOB coordinates, or None.

        The firmware reports where the map's (0,0) cell sits and the grid it
        was built on, and the map always grows +X and -Y from that corner, so
        the four numbers follow. Reported rather than inferred from the points,
        which arrive in coordinates relative to that same corner and so cannot
        say where it is.
        """
        q = self._quality

        def num(key):
            try:
                return float(q[key])
            except (KeyError, TypeError, ValueError):
                return None

        ox, oy = num("heightMapOriginXmm"), num("heightMapOriginYmm")
        nx, ny = num("heightMapGridPointsX"), num("heightMapGridPointsY")
        pitch = num("heightMapSpacingMm")
        if None in (ox, oy, nx, ny, pitch) or nx < 1 or ny < 1:
            return None
        return (ox, ox + (nx - 1) * pitch, oy - (ny - 1) * pitch, oy)

    def _update_coverage(self) -> None:
        """Where the map sits, and whether it covers the job.

        The verdict is the firmware's, not ours: `heightMapCoversOpenedJob` is
        the same check `startJob` enforces before it will run with
        compensation, so what this says cannot drift from what the machine
        does — and the 0.05 mm tolerance lives in one place rather than being
        copied here.

        The key is OMITTED when the answer cannot be determined: no map, no
        file open, or a file written before MIN/MAX metadata existed. Absent
        means unknown, never "does not cover", so that case says unknown.

        There used to be a size comparison here, on the grounds that a map
        smaller than the job cannot cover it wherever it sits. That is true and
        it is still not a substitute, which is why it is gone: a map big enough
        for the job still misses it if it was scanned somewhere else, and next
        to the word "unknown" a reassuring "big enough" reads as the verdict it
        is not.

        Why it is worth a warning at all: an uncovered job does not fail when
        it runs. Lookups outside the rectangle are clamped to the nearest edge
        cell, so the tool rides at a constant error for the whole cut — wrong
        in a way nothing else reports.
        """
        rect = self._map_rect()
        if rect is None:
            self._coverage_lbl.setText("")
            self._coverage_lbl.hide()
            return
        x0, x1, y0, y1 = rect
        parts = ["Map covers X %.1f … %.1f, Y %.1f … %.1f (job)"
                 % (x0, x1, y0, y1)]
        if self._job_rect is not None:
            jx0, jx1, jy0, jy1 = self._job_rect
            parts.append("toolpath X %.1f … %.1f, Y %.1f … %.1f"
                         % (jx0, jx1, jy0, jy1))
        warn = False

        covers = self._quality.get("heightMapCoversOpenedJob")
        if covers is True:
            parts.append("covers the toolpath")
        elif covers is False:
            parts.append("⚠ does NOT cover the toolpath — outside the map the "
                         "compensation is clamped to the nearest edge cell, so "
                         "the cut rides at a constant error rather than failing")
            warn = True
        else:
            parts.append("coverage unknown — no file open, or the file predates "
                         "the bounds metadata the check needs")

        if x0 <= 0.0 <= x1 and y0 <= 0.0 <= y1:
            parts.append("job zero is inside it")
        else:
            parts.append("⚠ job zero is OUTSIDE it — the Z datum every height "
                         "is measured from falls off the map")
            warn = True

        self._coverage_lbl.setText("  ·  ".join(parts))
        self._coverage_lbl.setStyleSheet(
            f"color: {WARN_C if warn else TXT3}; background: transparent;")
        self._coverage_lbl.show()

    def _update_quality(self) -> None:
        """The scan's own account of itself, under the map.

        `heightMapCellsInterpolated` gets its own sentence and its own colour
        because the firmware asks for exactly that: those cells collected no
        usable reading and were filled from their neighbours, so a map that
        looks smooth is partly inferred. Nothing here says WHICH cells —
        `getHeightMapPoints` does not mark them — so the honest thing is to
        name the count and let the operator distrust the whole surface.
        """
        self._update_coverage()
        bits = self._quality_bits()
        filled = self._quality.get("heightMapCellsInterpolated")
        try:
            filled = int(filled) if filled is not None else None
        except (TypeError, ValueError):
            filled = None
        if filled:
            self._quality_lbl.setText(
                "⚠ %d cell(s) had no usable reading and were filled in from "
                "their neighbours — that much of this surface is inferred, not "
                "measured.%s" % (filled, ("   " + " · ".join(bits)) if bits else ""))
            self._quality_lbl.setStyleSheet(
                f"color: {WARN_C}; background: transparent;")
        elif bits:
            done = self._quality.get("heightMapSamplesTaken")
            if done:
                bits.insert(0, "%s raw readings" % done)
            self._quality_lbl.setText(" · ".join(bits))
            self._quality_lbl.setStyleSheet(
                f"color: {TXT3}; background: transparent;")
        else:
            self._quality_lbl.setText("")
        self._quality_lbl.setVisible(bool(self._quality_lbl.text()))

    def _on_page(self, p: dict) -> None:
        if self._done:
            return
        self._timeout.stop()
        try:
            total = int(p.get("heightMapPointsTotal", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        self._total = total or self._total
        self._space = str(p.get("heightMapCoordinateSpace") or self._space)

        coords = p.get("heightMapPointCoordinates") or []
        for c in coords:
            try:
                self._points.append((float(c["x"]), float(c["y"]),
                                     float(c["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        try:
            returned = int(p.get("heightMapPointsReturned", len(coords)) or 0)
        except (TypeError, ValueError):
            returned = len(coords)

        got = len(self._points)
        if self._total:
            self._status.setText("Reading the map… %d / %d points"
                                 % (got, self._total))

        # Advance by what came back, not by what was asked for: the firmware
        # clamps the page size to fit its JSON buffer.
        if returned <= 0:
            self._finish(short=True)
            return
        if self._total and got >= self._total:
            self._finish()
            return
        self._request(self._next + returned)

    def _on_timeout(self) -> None:
        if self._done:
            return
        if self._points:
            self._finish(short=True)
        else:
            self._done = True
            self._status.setText(
                "The machine did not answer with the height map. It may have "
                "been cleared since the button was enabled.")

    def _finish(self, short: bool = False) -> None:
        self._done = True
        self._timeout.stop()
        self._canvas.set_points(self._points, self._gx, self._gy,
                                relative=self._space == "relative")
        lo, hi = self._canvas.z_range
        self._legend.set_range(lo, hi)
        gx, gy = self._canvas.grid_size
        n = len(self._points)
        if not n:
            self._status.setText("The machine reported no height-map points.")
            return
        space = self._space or "machine"
        self._sync_slider()
        msg = ("%d points · grid %d × %d · %s coordinates · range %+.4f to "
               "%+.4f mm" % (n, gx, gy, space, lo, hi))
        if short and self._total and n < self._total:
            msg += "  (stopped early: %d of %d)" % (n, self._total)
        self._status.setText(msg)
        self._update_info()

    def _on_slider(self, pos: int) -> None:
        # Guard: moving the slider from code (fit, 1:1, a map arriving) must not
        # come back through here and pin the value it was only displaying.
        if self._syncing:
            return
        self._canvas.set_z_factor(slider_to_factor(pos))
        self._update_info()

    def _set_factor(self, factor: float) -> None:
        self._canvas.set_z_factor(factor)
        self._sync_slider()
        self._update_info()

    def _sync_slider(self) -> None:
        """Put the slider where the canvas actually is, without pinning it."""
        self._syncing = True
        try:
            self._z_slider.setValue(factor_to_slider(self._canvas.z_factor))
        finally:
            self._syncing = False

    def _update_info(self) -> None:
        f = self._canvas.z_factor
        if abs(f - 1.0) < 5e-3:
            text = "Z 1:1 — true scale"
        else:
            text = "Z ×%s" % ("%.0f" % f if f >= 10 else "%.1f" % f)
        # Which of the two it is matters: on `fit` the number changes with the
        # map, so it cannot be compared with another scan's.
        self._info.setText(text + ("  (fit)" if self._canvas.is_auto_z else ""))

    # ── housekeeping ────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self._done = True
        self._timeout.stop()
        for sig, slot in ((self._conn.height_map_points_received, self._on_page),
                          (self._conn.height_map_status_changed,
                           self._on_status_reply),
                          (self._conn.opened_file_bounds_received,
                           self._on_job_bounds)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(e)

    def _apply_scale(self, *_):
        self.setStyleSheet(f"QDialog {{ background: {PANEL}; }}")
        self._title.setFont(font(15, bold=True))
        self._title.setStyleSheet(f"color: {TXT}; background: transparent;")
        self._status.setFont(font(10))
        self._status.setStyleSheet(f"color: {TXT2}; background: transparent;")
        self._quality_lbl.setFont(font(10))
        self._coverage_lbl.setFont(font(10))
        self._info.setFont(font(10))
        self._info.setStyleSheet(f"color: {TXT3}; background: transparent;")
        self._root.setContentsMargins(px(14), px(14), px(14), px(14))
        self._root.setSpacing(px(10))
        self._z_slider.setFixedWidth(px(180))
        for b in (self._scale_b, self._pts_b, self._reset_b, self._close_b):
            b.setFont(font(10))
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {WHITE}; color: {TXT};
                    border: 1px solid {BORDER}; border-radius: {px(6)}px;
                    padding: {px(5)}px {px(12)}px;
                }}
                QPushButton:hover {{ background: #E8E8E8; }}
                QPushButton:checked {{ background: #DCE9F5; }}
            """)
