"""
ui/pages/live_preview.py
Live Preview page — 3-D orbit view of the opened G-code toolpath, mirroring the
mobile app's WebGL preview: grid / axes / home / help buttons, view-side
shortcuts, executed-path highlighting (orange) vs pending (light blue), live
tool position, and a "clear stored cache" action.
"""

import math
import os
import shutil
from array import array

from PySide6.QtCore import (
    Qt, QPointF, QRectF, QSettings, QTimer, QLineF, QElapsedTimer,
    QThread, Signal,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QColor, QCursor, QPolygonF,
    QMatrix4x4, QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector3D,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram,
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy,
)

# While orbiting/zooming, draw at most this many segments (full detail is
# restored as soon as the interaction ends) — keeps dragging at 60 fps even
# for very large jobs.
_LOD_MAX_SEGS = 8000       # decimation budget while orbiting (higher = crisper)

from ui.theme import BG
from ui.scale import fs, px, bind
from ui import icons
from ui.tooltips import TRANSPARENT, TRANSPARENT_NO_BORDER

_CANVAS_BG = QColor("#0B0B0B")           # mobile: setClearColor(0x0b0b0b)
_HOST_BG = QColor("#0E0E10")             # black host frame behind the canvas
_GRID_MAJOR = QColor("#666666")          # mobile GridHelper colours
_GRID_MINOR = QColor("#333333")
_PENDING_C  = QColor("#6EC9E2")          # mobile pending vertices (110,201,226)
_DONE_C     = QColor("#D4620A")          # mobile executed vertices (212,98,10)
_RAPID_C    = QColor("#4A4E55")
_AXIS_X     = QColor("#E05050")
_AXIS_Y     = QColor("#50C050")
_AXIS_Z     = QColor("#5080E0")
_FOV_DEG    = 50.0

# ── GPU renderer ─────────────────────────────────────────────────────────────
# The toolpath is uploaded once as a vertex buffer and redrawn with a single
# draw call, so orbiting costs the same whether the job has 8 000 segments or
# 400 000 — no re-projection in Python, no level-of-detail, no quality drop
# while dragging.
#
# Portability is deliberately conservative, because this has to run on whatever
# GPU a shop's Windows or Linux box happens to have:
#   * no core-profile request, so macOS gives its 2.1 context and Mesa/ANGLE
#     stay happy — GLSL 120 (attribute/varying/gl_FragColor) is the common
#     denominator that compiles on all three;
#   * no vertex-array objects (a core-profile requirement) — the buffer is
#     bound and the attribute pointers set per frame, which is GL 2.0 era and
#     universally available;
#   * only calls that exist in QOpenGLFunctions (the GL ES 2.0 subset Qt
#     guarantees), so no version-specific function resolution;
#   * line thickness is requested, never assumed: drivers silently clamp
#     glLineWidth, and a core profile clamps it to 1.0.
# Anything that still fails falls back to the software painter below.

# Colours arrive as uniforms and the executed/pending split is decided per
# vertex from the line index, so job progress is a uniform change rather than a
# rebuild — the thing that used to invalidate the whole projection cache.
_VERT_SRC = """
#version 120
attribute vec3  aPos;
attribute float aLine;      // source line index, for the progress split
attribute float aKind;      // 1.0 = rapid, 0.0 = cutting move
attribute float aDash;      // distance along a rapid, for the dash pattern
uniform mat4  uMVP;
uniform float uProgress;
uniform vec3  uRapid;
uniform vec3  uDone;
uniform vec3  uPending;
varying vec3  vColor;
varying float vDash;
varying float vKind;
varying float vDone;        // 1.0 = already executed (both ends share a line)
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vDone  = (aLine <= uProgress) ? 1.0 : 0.0;
    vColor = (aKind > 0.5) ? uRapid : ((vDone > 0.5) ? uDone : uPending);
    vDash  = aDash;
    vKind  = aKind;
}
"""

# The rounded fillet is cut per fragment rather than with a clip path: the
# widget is translucent, so discarding outside the rounded rect leaves the host
# frame showing through the corners exactly as the painter version did.
_ROUND_CLIP = """
uniform vec3 uRect;         // width, height, corner radius — in DEVICE pixels
bool outside_fillet() {
    vec2 half_size = uRect.xy * 0.5;
    vec2 d = abs(gl_FragCoord.xy - half_size) - (half_size - vec2(uRect.z));
    return (min(max(d.x, d.y), 0.0) + length(max(d, 0.0))) > uRect.z;
}
"""

_FRAG_SRC = """
#version 120
""" + _ROUND_CLIP + """
uniform float uDashPeriod;
uniform float uHideDone;    // 1.0 = leave out everything already executed
varying vec3  vColor;
varying float vDash;
varying float vKind;
varying float vDone;
void main() {
    if (outside_fillet()) discard;
    // "Hide done": the executed part of the job, rapids included, drops out so
    // only what is still to be cut is drawn.
    if (uHideDone > 0.5 && vDone > 0.5) discard;
    // Rapids stay dashed. The period is handed in scaled by camera distance so
    // the dashes keep a roughly constant size on screen as the view zooms.
    if (vKind > 0.5 && uDashPeriod > 0.0 && fract(vDash / uDashPeriod) > 0.55)
        discard;
    gl_FragColor = vec4(vColor, 1.0);
}
"""

# Grid, axes and the canvas background: per-vertex colour, and a flag to take
# positions as clip-space directly so the background quad can reuse the program.
_DECO_VERT_SRC = """
#version 120
attribute vec3 aPos;
attribute vec3 aColor;
uniform mat4  uMVP;
uniform float uClipSpace;
varying vec3  vColor;
void main() {
    gl_Position = (uClipSpace > 0.5) ? vec4(aPos, 1.0) : uMVP * vec4(aPos, 1.0);
    vColor = aColor;
}
"""

_DECO_FRAG_SRC = """
#version 120
""" + _ROUND_CLIP + """
varying vec3 vColor;
void main() {
    if (outside_fillet()) discard;
    gl_FragColor = vec4(vColor, 1.0);
}
"""


def _rgb(c: QColor):
    return c.redF(), c.greenF(), c.blueF()



import re
_WORD_RE = re.compile(r"([GXYZIJFA])\s*(-?\d+\.?\d*)", re.IGNORECASE)

# A rotary move sweeping more than this many degrees is subdivided, so the
# wrapped path draws as a curve around the stock instead of a chord cutting
# straight through it.
_ROTARY_STEP_DEG = 4.0


def _wrap(x: float, r: float, a_deg: float):
    """Tool position for a 4th-axis file: *r* is the radial distance from the
    rotation axis (the file's Z) and *a_deg* the rotation about it."""
    t = math.radians(a_deg)
    return x, r * math.sin(t), r * math.cos(t)


def pack_path(segs):
    """Interleaved vertex array for *segs*, as ``(array('f'), vertex count)``.

    Split out of the canvas so :class:`_ParseTask` can do it off the GUI thread
    — it is about a fifth of the cost of opening a file, and leaving it behind
    would put that share of the stall straight back. It touches no Qt and no
    widget state, which is what makes it safe to run there.
    """
    if not segs:
        return None, 0
    buf = array("f")
    for rapid_pass in (True, False):
        for x0, y0, z0, x1, y1, z1, is_rapid, li in segs:
            if bool(is_rapid) is not rapid_pass:
                continue
            k = 1.0 if is_rapid else 0.0
            d = math.dist((x0, y0, z0), (x1, y1, z1)) if is_rapid else 0.0
            buf.extend((x0, y0, z0, li, k, 0.0,
                        x1, y1, z1, li, k, d))
    return buf, len(buf) // 6


class ParseCancelled(Exception):
    """Raised out of the progress callback to abandon a parse in flight."""


class _ParseTask(QThread):
    """Reads a toolpath off the GUI thread.

    Why off it: `parse_gcode` plus the vertex-array build is linear in file
    size and runs to completion before returning — 3.3 s for a 33 MB file on a
    fast machine. Everything that keeps the machine link alive lives on the GUI
    thread (`connection._poll`, the websocketPing that answers the firmware's
    5 s watchdog), so a parse long enough to matter dropped the connection.
    Nothing about the toolpath needs the GUI thread, so it does not use it.

    The vertex array is built here too. It is a fifth of the cost, and building
    it on the GUI thread afterwards would put a chunk of the stall straight
    back.
    """

    progress = Signal(int)                  # 0..100 of the file read
    ready = Signal(object, object, object, object)  # segs, bounds, packed, cut
    failed = Signal(str)
    unsized = Signal()      # a rotary job whose bar size is not in the file

    def __init__(self, path: str, build, parent=None):
        super().__init__(parent)
        self._path = path
        self._build = build
        self._cancelled = False
        self._last_pct = -1

    def cancel(self) -> None:
        """Abandon the read. The thread notices at its next progress tick."""
        self._cancelled = True

    def _tick(self, fraction: float) -> None:
        if self._cancelled:
            raise ParseCancelled()
        pct = int(fraction * 100)
        if pct != self._last_pct:
            self._last_pct = pct
            self.progress.emit(pct)

    def run(self):
        try:
            cut = {}
            segs, bounds = parse_gcode(self._path, progress=self._tick,
                                       cut_out=cut)
            if self._cancelled:
                return
            if cut.get("rotary_unsized"):
                self.unsized.emit()
                return
            # The byte tally ticks per block of lines, so the last one lands
            # short — 98% on a 13 MB file. Say 100 before the pack, which is
            # the part with nothing left to report.
            if self._last_pct < 100:
                self._last_pct = 100
                self.progress.emit(100)
            packed = self._build(segs)
        except ParseCancelled:
            return
        except Exception as exc:                        # pragma: no cover
            if not self._cancelled:
                self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if not self._cancelled:
            self.ready.emit(segs, bounds, packed, cut.get("cut"))


def parse_gcode(path: str, arc_segments: int = 20, progress=None,
                cut_out=None):
    """Parse a G-code file into 3-D segments.

    *progress*, when given, is called with a 0..1 fraction of the file read, at
    most a few times a second's worth of lines apart. It exists so a worker can
    report a percentage and so a superseded parse can be abandoned: raising
    :class:`ParseCancelled` from it stops the read where it stands.

    Returns (segments, bounds) where each segment is
    (x0, y0, z0, x1, y1, z1, is_rapid, source_line_index) and bounds is
    (min_x, min_y, min_z, max_x, max_y, max_z).

    *cut_out*, when given a dict, gets ``"cut"``: the same six numbers over
    the feed moves (G1/G2/G3) only, or None for a rotary file or one without
    any. Rapids, retract heights and park moves are left out, so it is the
    region the tool actually cuts -- what ``job_summary.origin_from_cut``
    places work zero against.

    Rotary (4th-axis) files are wrapped onto the stock rather than flattened.
    Such a post emits A — degrees about the X axis — in place of Y, and its Z
    is the *radial* distance from the rotation axis, so cutting deeper means a
    smaller Z. That makes the real tool position fully determined by the file,
    with no stock radius to supply from outside::

        x = X,  y = Z·sin(A),  z = Z·cos(A)

    One exception: this app's own rotary export can put Z 0 on the top of the
    bar instead (``; rotary: Z 0 on top of the bar … stock radius R`` in the
    header, see ui/cam_gcode_header.py); then the radius is Z + R, and the
    header line, read before the first move, supplies R. It is also read as
    ``(rotary: …)``: the machine drops ``;`` lines, so a copy downloaded from
    it carries only that form, and without it the job is wrapped a whole bar
    radius too small.

    The switch happens on the first A word rather than up front, which saves
    pre-scanning the whole file. That is safe because anything emitted before
    it has Y = 0 and A = 0, where the flat and wrapped mappings agree exactly —
    so no already-emitted segment ever needs revisiting.
    """
    segs = []
    x = y = z = 0.0
    a = 0.0
    rotary = False
    # radius = Z + rz: 0 for a Z zero on the rotary axis (Fusion, and this
    # app's default), the stock radius for one on the top of the bar
    rz = 0.0
    # whether a rotary: line was read, and the lowest Z a rotary feed move
    # reaches: without the line, a cut below Z 0 means Z 0 is not the axis
    # (no radius is negative) and the bar's radius is nowhere in the file
    rot_line = False
    rot_low = float("inf")
    mode = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    clo = [float("inf")] * 3
    chi = [float("-inf")] * 3

    def _track(px, py, pz):
        lo[0] = min(lo[0], px); hi[0] = max(hi[0], px)
        lo[1] = min(lo[1], py); hi[1] = max(hi[1], py)
        lo[2] = min(lo[2], pz); hi[2] = max(hi[2], pz)

    def _track_cut(px, py, pz):
        clo[0] = min(clo[0], px); chi[0] = max(chi[0], px)
        clo[1] = min(clo[1], py); chi[1] = max(chi[1], py)
        clo[2] = min(clo[2], pz); chi[2] = max(chi[2], pz)

    if cut_out is not None:
        cut_out["cut"] = None

    try:
        # Progress is measured in BYTES, not lines: the line count is unknown
        # until the file has been read, which is the thing being reported on.
        try:
            total_bytes = max(os.path.getsize(path), 1)
        except OSError:
            total_bytes = 0
        seen_bytes = 0
        with open(path, errors="ignore") as fh:
            for idx, raw in enumerate(fh):
                if progress is not None:
                    seen_bytes += len(raw)
                    # Every 8192 lines: often enough that a 30 MB file reports
                    # about forty times, rare enough that the check itself is
                    # not part of the cost being measured.
                    if total_bytes and (idx & 8191) == 0:
                        progress(min(seen_bytes / total_bytes, 1.0))
                line = raw.split(";")[0].split("(")[0].strip().upper()
                if not line:
                    # ";" in the app's own copy, "(" in one fetched back
                    # from the machine, which keeps only ( ) comments
                    if idx < 400 and raw.lstrip()[:1] in (";", "("):
                        from ui.job_summary import _ROTARY
                        m = _ROTARY.match(raw.strip())
                        if m:
                            rot_line = True
                        if m and m.group("where") == "top of the bar":
                            rz = float(m.group("r"))
                    continue
                words = {}
                g_codes = []
                for m in _WORD_RE.finditer(line):
                    k, v = m.group(1).upper(), float(m.group(2))
                    if k == "G":
                        g_codes.append(int(v))
                    else:
                        words[k] = v
                for g in g_codes:
                    if g in (0, 1, 2, 3):
                        mode = g
                # G10 (set offsets) and G92 move nothing: a rotary job ends
                # with `G10 L20 P1 A<remainder>` to drop its whole turns from
                # the read-out, which read as a move here would be a sweep of
                # tens of thousands of degrees back to the start
                if any(g in (10, 92) for g in g_codes):
                    continue
                if "A" in words:
                    rotary = True
                # A rotary move can turn the stock without touching X/Y/Z —
                # dropping those would lose most of the toolpath.
                if not ("X" in words or "Y" in words or "Z" in words
                        or (rotary and "A" in words)):
                    continue
                nx = words.get("X", x)
                ny = words.get("Y", y)
                nz = words.get("Z", z)
                na = words.get("A", a)
                if rotary:
                    # Subdivide by swept angle so the wrap is a curve, not a
                    # chord. Arcs are left to the linear case on purpose: a
                    # rotary post emits its curvature as A, and its own header
                    # warns that I/J arcs may not display correctly.
                    if mode != 0:
                        rot_low = min(rot_low, nz)
                    steps = max(1, math.ceil(abs(na - a) / _ROTARY_STEP_DEG))
                    p = _wrap(x, z + rz, a)
                    _track(*p)
                    for i in range(1, steps + 1):
                        t = i / steps
                        q = _wrap(x + (nx - x) * t, z + rz + (nz - z) * t,
                                  a + (na - a) * t)
                        segs.append((*p, *q, mode == 0, idx))
                        _track(*q)
                        p = q
                    x, y, z, a = nx, ny, nz, na
                    continue
                if mode in (2, 3) and ("I" in words or "J" in words):
                    cx = x + words.get("I", 0.0)
                    cy = y + words.get("J", 0.0)
                    r = math.hypot(x - cx, y - cy)
                    a0 = math.atan2(y - cy, x - cx)
                    a1 = math.atan2(ny - cy, nx - cx)
                    if mode == 2:
                        while a1 >= a0:
                            a1 -= 2 * math.pi
                    else:
                        while a1 <= a0:
                            a1 += 2 * math.pi
                    px, py, pz = x, y, z
                    for i in range(1, arc_segments + 1):
                        t = i / arc_segments
                        a = a0 + (a1 - a0) * t
                        qx = cx + r * math.cos(a)
                        qy = cy + r * math.sin(a)
                        qz = z + (nz - z) * t
                        segs.append((px, py, pz, qx, qy, qz, False, idx))
                        _track(qx, qy, qz)
                        _track_cut(qx, qy, qz)
                        px, py, pz = qx, qy, qz
                else:
                    segs.append((x, y, z, nx, ny, nz, mode == 0, idx))
                    _track(x, y, z)
                    _track(nx, ny, nz)
                    if mode != 0:
                        _track_cut(x, y, z)
                        _track_cut(nx, ny, nz)
                x, y, z, a = nx, ny, nz, na
    except OSError:
        return [], (0, 0, 0, 0, 0, 0)

    if not segs:
        return [], (0, 0, 0, 0, 0, 0)
    if cut_out is not None and not rotary and clo[0] != float("inf"):
        cut_out["cut"] = (clo[0], clo[1], clo[2], chi[0], chi[1], chi[2])
    if cut_out is not None:
        # A Z-from-the-top-of-the-bar job whose rotary: line is gone -- the
        # machine drops ; lines, and exports before the ( ) copy of that line
        # have nothing else. Read as radii from the axis it would be drawn a
        # whole bar radius too small, a tangle round the centre; the caller
        # shows a message instead of a wrong picture.
        cut_out["rotary_unsized"] = (rotary and not rot_line
                                     and rot_low < -0.001)
    return segs, (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


class Gcode3DCanvas(QWidget):
    """3-D orbit view of the toolpath (Z up, like mobile).

    GPU-rendered, but *offscreen*: the toolpath lives in a vertex buffer and
    every frame is one draw call into a framebuffer object, which is then
    blitted into this plain widget with QPainter. Orbiting a 400 000-segment
    job therefore costs the same as an 8 000 segment one and never drops
    detail.

    The offscreen detour is not incidental — it is the whole reason this is a
    QWidget and not a QOpenGLWidget. A QOpenGLWidget anywhere in the same
    top-level window as a QWebEngineView leaves the web view painting black,
    which would take out both CAM tabs. Rendering through our own context and
    FBO keeps the GPU work and leaves the widget hierarchy free of GL widgets.

    If the context, the shaders or the FBO fail to come up — an old driver, a
    remote desktop, a VM without acceleration — it falls back to the software
    painter, which is slower while dragging but works anywhere.
    """

    def __init__(self):
        super().__init__()
        self._gl_ok = False          # set once the context + shaders are up
        self._gl_failed = False      # tried and could not — software from here
        self._ctx = None
        self._surface = None
        self._fbo = None
        self._fbo_size = (0, 0)
        self._gl = None
        self._prog = None
        self._deco_prog = None
        self._path_vbo = None
        self._deco_vbo = None
        self._path_data = None       # array('f') awaiting upload
        self._path_verts = 0
        self._deco_blob = b""
        self._deco_verts = 0
        self._grid_verts = 0
        self._deco_key = None
        self.setMinimumSize(px(300), px(300))
        self.setCursor(QCursor(Qt.OpenHandCursor))
        # Keep every mouse event on the canvas. Without this an unhandled
        # button (notably the middle button, which QWidget ignores by default)
        # bubbles up to the enclosing QScrollArea — on macOS that native
        # fallback path crashes the app when middle-dragging to pan.
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        # Translucent so the corners outside the rounded clip stay transparent
        # and the rounded host frame (and the white card behind it) shows through
        # — otherwise the canvas would paint square black corners over the fillet.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._segs = []
        self._center = (0.0, 0.0, 0.0)
        self._diag = 1.0
        self._progress_line = -1
        self.show_grid = True
        self.show_axes = True
        # Leave out the already-executed part of the job (see set_hide_done).
        self.hide_done = False
        self.corner_radius = px(12)   # match the host frame's rounded corners

        self._home_yaw = math.atan2(-0.75, 0.55)
        self._home_pitch = math.asin(0.6 / math.sqrt(0.55**2 + 0.75**2 + 0.6**2))
        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = 100.0
        self._pan = QPointF(0, 0)
        self._mode = None
        self._last = QPointF()

        # Level-of-detail: low-res while interacting, full quality at rest
        self._lod_segs = None      # decimated, connected copy used while dragging
        self._lod = False
        # Cached full-quality projected toolpath (pan-free). Panning is a pure
        # translation and zooming is (very nearly) a uniform scale about the
        # view centre, so both reuse this cached projection and apply the change
        # as a cheap painter transform — no re-projection, no quality loss.
        # Stored as (base_key, dist, rapid, done, pending) where base_key is
        # (yaw, pitch, progress, width, height): a pan keeps everything the
        # same; a zoom keeps base_key and only changes dist.
        self._proj_cache = None
        self._zooming = False
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(220)
        self._lod_timer.timeout.connect(self._end_lod)

    def _begin_lod(self):
        self._lod = True
        self._lod_timer.start()

    def _begin_zoom(self):
        # Zoom keeps full quality (painter scale on the cached projection); it
        # only needs the recovery timer to snap back to an exact re-projection
        # once the wheel stops.
        self._zooming = True
        self._lod_timer.start()

    def _end_lod(self):
        self._lod = False
        self._zooming = False
        self.update()

    def _build_lod_segs(self):
        """Decimated toolpath for smooth interaction. Merges runs of consecutive
        same-type segments into single chords (≈ _LOD_MAX_SEGS total). Because
        the toolpath is one connected chain, merging keeps it connected — unlike
        dropping every Nth segment, which leaves gaps that make a solid path look
        dashed while dragging. Built once per file and reused all interaction."""
        segs = self._segs
        n = len(segs)
        stride = max(1, (n + _LOD_MAX_SEGS - 1) // _LOD_MAX_SEGS)
        out = []
        i = 0
        while i < n:
            s0 = segs[i]
            is_rapid = s0[6]
            j = i
            # extend the run over same-type segments, up to `stride` long
            while (j + 1 < n and (j - i + 1) < stride
                   and segs[j + 1][6] == is_rapid):
                j += 1
            e = segs[j]
            # chord: start of the first segment → end of the last; keep the
            # first line index so executed/pending colouring stays close.
            out.append((s0[0], s0[1], s0[2], e[3], e[4], e[5], is_rapid, s0[7]))
            i = j + 1
        return out

    # ── data ─────────────────────────────────────────────────────────────────

    def _build_path_data(self) -> None:
        """Pack the toolpath into an interleaved vertex array for the GPU.

        Six floats per vertex — position, source line index, rapid flag and the
        distance along a rapid that drives its dash. Rapids are emitted first so
        they stay *under* the cutting moves, matching the painter's draw order;
        within the cutting moves, file order already puts executed segments
        before pending ones, so the layering comes out the same.
        """
        self._path_data, self._path_verts = pack_path(self._segs)

    def set_parsed(self, segs, bounds, packed=None) -> None:
        """Show an already-parsed toolpath.

        *packed* is what :func:`pack_path` returned, when a worker has already
        done it; None means pack here. Everything after this point is cheap —
        bounds arithmetic and a camera reset — so it is fine on the GUI thread.
        """
        self._segs = segs or []
        b = bounds if segs else (0, 0, 0, 0, 0, 0)
        if packed is None:
            self._build_path_data()
        else:
            self._path_data, self._path_verts = packed
        self._finish_load(b)

    def load(self, path: str | None) -> None:
        """Read *path* on the calling thread. Kept for callers that have no
        event loop to wait on; the preview page uses :class:`_ParseTask` and
        :meth:`set_parsed` instead, so a large file cannot stall the link."""
        if path:
            self._segs, b = parse_gcode(path)
        else:
            self._segs, b = [], (0, 0, 0, 0, 0, 0)
        self._build_path_data()
        self._finish_load(b)

    def _finish_load(self, b) -> None:
        self._lod_segs = None      # rebuilt lazily on next interaction
        self._proj_cache = None    # projected lines are for the old toolpath
        if self._segs:
            self._center = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2,
                            (b[2] + b[5]) / 2)
            dx, dy, dz = b[3] - b[0], b[4] - b[1], b[5] - b[2]
            self._diag = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        else:
            self._center = (0.0, 0.0, 0.0)
            self._diag = 1.0
        self._progress_line = -1
        self.reset_view()

    @property
    def has_data(self) -> bool:
        return bool(self._segs)

    def set_progress_line(self, line: int) -> None:
        if line != self._progress_line:
            self._progress_line = line
            self.update()

    def set_hide_done(self, on: bool) -> None:
        """Draw only what is still to be cut: the executed part (orange) and
        the rapids already travelled drop out, and keep dropping out as the job
        advances, since both renderers decide it from the progress line."""
        on = bool(on)
        if on != self.hide_done:
            self.hide_done = on
            self.update()

    # ── view control ─────────────────────────────────────────────────────────

    def _home_dist(self) -> float:
        base = (self._diag / 2) / math.tan(math.radians(_FOV_DEG / 2)) * 1.5
        return base * math.sqrt(0.55**2 + 0.75**2 + 0.6**2)

    def reset_view(self) -> None:
        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = self._home_dist()
        self._pan = QPointF(0, 0)
        self.update()

    def set_view(self, side: str) -> None:
        """Snap the camera to a face view (front/back/left/right/top/bottom)."""
        views = {
            "front":  (-90.0, 0.0),
            "back":   (90.0, 0.0),
            "right":  (0.0, 0.0),
            "left":   (180.0, 0.0),
            "top":    (-90.0, 88.0),     # avoid the Z-up gimbal singularity
            "bottom": (-90.0, -88.0),
        }
        if side in views:
            yaw_d, pitch_d = views[side]
            self._yaw = math.radians(yaw_d)
            self._pitch = math.radians(pitch_d)
            self._pan = QPointF(0, 0)
            self.update()

    def wheelEvent(self, e):
        e.accept()
        factor = 0.88 if e.angleDelta().y() > 0 else 1 / 0.88
        lo = max(self._diag * 0.08, 0.5)
        hi = self._diag * 8
        d0 = self._dist
        d1 = max(lo, min(hi, d0 * factor))
        if d1 != d0:
            # Zoom toward the cursor: keep the point under the mouse fixed.
            # Zoom scales the view about its centre (pc); shift the pan so the
            # cursor's world point lands back under the cursor afterwards.
            s = d0 / d1
            pc = QPointF(self.width() / 2, self.height() / 2)
            cur = e.position()
            self._pan = cur - pc - (cur - self._pan - pc) * s
            self._dist = d1
        self._begin_zoom()
        self.update()

    def mousePressEvent(self, e):
        e.accept()
        self._last = e.position()
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
            self._begin_lod()          # orbit changes the projection → LOD
        else:
            # Panning is a pure 2-D shift: the cached projection is reused and
            # translated, so it stays full quality (no LOD, no re-projection).
            self._pan += d
        self.update()

    def mouseReleaseEvent(self, e):
        e.accept()
        self._mode = None
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self._end_lod()

    # ── GPU path ─────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        img = self._render_offscreen()
        if img is not None:
            p.drawImage(0, 0, img)
        else:
            self._paint_software(p)
        p.end()

    def _ensure_gl(self) -> bool:
        """Bring up the offscreen context and compile the shaders, once.

        Any failure is not fatal — it sets ``_gl_failed`` and the software
        painter takes over for the rest of the session.
        """
        if self._gl_ok:
            return True
        if self._gl_failed:
            return False
        self._gl_failed = True       # cleared on success below
        try:
            # A broken OpenGL driver does not fail context creation, it crashes
            # the process. gl_guard asked a throwaway process first and switches
            # to Qt's software OpenGL if that one died (see gl_guard.py).
            import gl_guard
            gl_guard.ensure()
            # Start from the application's default format: QtWebEngine sets one
            # up, and a bare QSurfaceFormat() would drop its depth/stencil/swap
            # settings. Alpha keeps the rounded corners transparent, samples
            # give smooth lines; both degrade silently if refused.
            fmt = QSurfaceFormat.defaultFormat()
            fmt.setAlphaBufferSize(8)
            fmt.setSamples(4)
            self._surface = QOffscreenSurface()
            self._surface.setFormat(fmt)
            self._surface.create()
            if not self._surface.isValid():
                raise RuntimeError("no offscreen surface")
            self._ctx = QOpenGLContext()
            self._ctx.setFormat(fmt)
            if not self._ctx.create():
                raise RuntimeError("could not create an OpenGL context")
            if not self._ctx.makeCurrent(self._surface):
                raise RuntimeError("could not make the context current")
            self._gl = self._ctx.functions()
            progs = []
            for vert, frag in ((_VERT_SRC, _FRAG_SRC),
                               (_DECO_VERT_SRC, _DECO_FRAG_SRC)):
                pr = QOpenGLShaderProgram(self)
                if not pr.addShaderFromSourceCode(QOpenGLShader.Vertex, vert):
                    raise RuntimeError(f"vertex shader: {pr.log()}")
                if not pr.addShaderFromSourceCode(QOpenGLShader.Fragment, frag):
                    raise RuntimeError(f"fragment shader: {pr.log()}")
                if not pr.link():
                    raise RuntimeError(f"link: {pr.log()}")
                progs.append(pr)
            self._prog, self._deco_prog = progs
            self._path_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._deco_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            for vbo in (self._path_vbo, self._deco_vbo):
                if not vbo.create():
                    raise RuntimeError("could not create a vertex buffer")
                vbo.setUsagePattern(QOpenGLBuffer.StaticDraw)
            self._gl_ok = True
            self._gl_failed = False
            return True
        except Exception as exc:                        # pragma: no cover
            self._gl_ok = False
            print(f"[live-preview] GPU renderer unavailable ({exc}); "
                  f"falling back to software rendering")
            return False
        finally:
            if self._ctx is not None:
                self._ctx.doneCurrent()

    def _build_deco_data(self):
        """Grid on the Z floor and the origin axes, as coloured line vertices.

        Returns (data, grid_vertex_count) — the grid comes first so the two can
        be drawn as separate ranges at their own line weights, the way the
        painter drew them (1 px grid, 2 px axes).
        """
        buf = array("f")
        cx0, cy0, _cz0 = self._center
        if self.show_grid:
            ext = max(self._diag * 0.75, 30)
            step = 10
            gz = 0.0 if abs(self._center[2]) < self._diag else self._center[2]
            n = int(ext // step)
            for i in range(-n, n + 1):
                g = i * step
                c = _rgb(_GRID_MAJOR if i % 5 == 0 else _GRID_MINOR)
                buf.extend((cx0 + g, cy0 - ext, gz, *c,
                            cx0 + g, cy0 + ext, gz, *c,
                            cx0 - ext, cy0 + g, gz, *c,
                            cx0 + ext, cy0 + g, gz, *c))
        grid_verts = len(buf) // 6
        if self.show_axes:
            alen = self._diag * 0.4
            for tip, col in (((alen, 0, 0), _AXIS_X),
                             ((0, alen, 0), _AXIS_Y),
                             ((0, 0, alen), _AXIS_Z)):
                c = _rgb(col)
                buf.extend((0.0, 0.0, 0.0, *c, *tip, *c))
        return buf, grid_verts

    def _mvp(self, w: float, h: float) -> QMatrix4x4:
        """Camera matrix for the current orbit / zoom / pan.

        Pan moves the camera in its own right/up plane rather than shifting the
        finished image: a clip-space translation would scale with depth, and
        this is what a 3-D view is expected to do anyway.
        """
        cx0, cy0, cz0 = self._center
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        cy_, sy = math.cos(self._yaw), math.sin(self._yaw)
        ex, ey, ez = (self._dist * cp * cy_, self._dist * cp * sy,
                      self._dist * sp)
        eye = QVector3D(cx0 + ex, cy0 + ey, cz0 + ez)
        centre = QVector3D(cx0, cy0, cz0)
        up = QVector3D(0, 0, 1)
        # World units per screen pixel at the focus plane, so a drag tracks the
        # cursor at the same rate the painter version did.
        focal = (h / 2) / math.tan(math.radians(_FOV_DEG / 2)) or 1.0
        forward = (centre - eye).normalized()
        right = QVector3D.crossProduct(forward, up).normalized()
        true_up = QVector3D.crossProduct(right, forward).normalized()
        shift = (right * (-self._pan.x() * self._dist / focal)
                 + true_up * (self._pan.y() * self._dist / focal))
        span = max(self._diag, 1.0)
        proj = QMatrix4x4()
        proj.perspective(_FOV_DEG, (w / h) if h else 1.0,
                         max(self._dist * 0.005, 0.01), self._dist + span * 8)
        view = QMatrix4x4()
        view.lookAt(eye + shift, centre + shift, up)
        return proj * view

    def _render_offscreen(self):
        """Draw the scene on the GPU and return it as a QImage, or None to let
        the software painter handle this frame."""
        if not self._ensure_gl():
            return None
        w, h = max(self.width(), 1), max(self.height(), 1)
        dpr = self.devicePixelRatioF()
        dev = (max(1, int(w * dpr)), max(1, int(h * dpr)))
        if not self._ctx.makeCurrent(self._surface):
            return None
        try:
            if self._fbo is None or self._fbo_size != dev:
                ffmt = QOpenGLFramebufferObjectFormat()
                ffmt.setSamples(4)          # silently reduced if unsupported
                self._fbo = QOpenGLFramebufferObject(dev[0], dev[1], ffmt)
                self._fbo_size = dev
                if not self._fbo.isValid():
                    raise RuntimeError("framebuffer object is not valid")
            self._fbo.bind()
            self._gl.glViewport(0, 0, dev[0], dev[1])
            self._draw_scene(w, h, dpr)
            self._fbo.release()
            img = self._fbo.toImage()
            # Tag it so QPainter maps device pixels back to logical ones.
            img.setDevicePixelRatio(dpr)
            return img
        except Exception as exc:                        # pragma: no cover
            self._gl_ok = False
            self._gl_failed = True
            print(f"[live-preview] GPU render failed ({exc}); "
                  f"falling back to software rendering")
            return None
        finally:
            self._ctx.doneCurrent()

    def _draw_scene(self, w: int, h: int, dpr: float) -> None:
        gl = self._gl
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(0x00004000)                          # GL_COLOR_BUFFER_BIT
        gl.glDisable(0x0B71)                            # GL_DEPTH_TEST — draw
        # order is the file order, matching the painter's flat compositing.
        # gl_FragCoord is in device pixels, so the fillet must be too.
        rect = (w * dpr, h * dpr, self.corner_radius * dpr)
        mvp = self._mvp(w, h)

        # Rebuild the grid/axes only when something they depend on moved. Keyed
        # on the values themselves rather than a dirty flag, so the plain
        # ``show_grid``/``show_axes`` attributes the page sets keep working.
        deco_key = (self.show_grid, self.show_axes, self._center, self._diag)
        if deco_key != self._deco_key:
            data, grid_verts = self._build_deco_data()
            self._deco_blob = data.tobytes()
            self._deco_verts = len(data) // 6
            self._grid_verts = grid_verts
            self._deco_key = deco_key

        pr = self._deco_prog
        pr.bind()
        pr.setUniformValue("uRect", QVector3D(*rect))
        self._deco_vbo.bind()
        # Background first: a clip-space triangle covering the viewport in the
        # canvas colour, cut to the rounded fillet by the same discard the rest
        # of the scene uses.
        bg = _rgb(_CANVAS_BG)
        quad = array("f", (-1, -1, 0, *bg, 3, -1, 0, *bg, -1, 3, 0, *bg))
        pr.setUniformValue1f("uClipSpace", 1.0)
        self._draw(pr, quad.tobytes(), 3, 0x0004, first=0)       # GL_TRIANGLES
        # Then grid and axes in world space, each at the weight the painter
        # used. glLineWidth is global state that persists across frames, so
        # every pass sets its own rather than inheriting the last one's.
        if self._deco_verts:
            pr.setUniformValue1f("uClipSpace", 0.0)
            pr.setUniformValue("uMVP", mvp)
            self._deco_vbo.allocate(self._deco_blob, len(self._deco_blob))
            self._bind_attrs(pr, (("aPos", 3, 0), ("aColor", 3, 12)), 24)
            if self._grid_verts:
                gl.glLineWidth(max(1.0, dpr))
                gl.glDrawArrays(0x0001, 0, self._grid_verts)          # LINES
            if self._deco_verts > self._grid_verts:
                gl.glLineWidth(max(1.0, 2.0 * dpr))
                gl.glDrawArrays(0x0001, self._grid_verts,
                                self._deco_verts - self._grid_verts)
        self._deco_vbo.release()
        pr.release()

        if self._path_verts:
            if self._path_data is not None:
                self._path_vbo.bind()
                self._path_vbo.allocate(self._path_data.tobytes(),
                                        len(self._path_data) * 4)
                self._path_vbo.release()
                self._path_data = None      # uploaded; drop the CPU copy
            pr = self._prog
            pr.bind()
            pr.setUniformValue("uMVP", mvp)
            pr.setUniformValue("uRect", QVector3D(*rect))
            pr.setUniformValue1f("uProgress", float(self._progress_line))
            pr.setUniformValue1f("uHideDone", 1.0 if self.hide_done else 0.0)
            pr.setUniformValue("uRapid", QVector3D(*_rgb(_RAPID_C)))
            pr.setUniformValue("uDone", QVector3D(*_rgb(_DONE_C)))
            pr.setUniformValue("uPending", QVector3D(*_rgb(_PENDING_C)))
            # Dash period in world units, tied to camera distance so the dashes
            # hold their apparent size through a zoom.
            pr.setUniformValue1f("uDashPeriod", max(self._dist * 0.012, 1e-4))
            # Ask for the painter's line weight; drivers clamp silently and a
            # core profile pins it to 1.0, which still looks right under MSAA.
            gl.glLineWidth(max(1.0, 1.6 * dpr))
            self._path_vbo.bind()
            self._bind_attrs(pr, (("aPos", 3, 0), ("aLine", 1, 12),
                                  ("aKind", 1, 16), ("aDash", 1, 20)), 24)
            gl.glDrawArrays(0x0001, 0, self._path_verts)          # GL_LINES
            self._path_vbo.release()
            pr.release()

    @staticmethod
    def _bind_attrs(pr, attrs, stride: int) -> None:
        """Point the shader's attributes at the currently bound buffer."""
        for name, size, offset in attrs:
            loc = pr.attributeLocation(name)
            if loc >= 0:
                pr.enableAttributeArray(loc)
                pr.setAttributeBuffer(loc, 0x1406, offset, size, stride)  # FLOAT

    def _draw(self, pr, blob: bytes, verts: int, mode: int, first: int = 0) -> None:
        """Upload *blob* into the bound deco buffer and draw it."""
        self._deco_vbo.allocate(blob, len(blob))
        self._bind_attrs(pr, (("aPos", 3, 0), ("aColor", 3, 12)), 24)
        self._gl.glDrawArrays(mode, first, verts)

    # ── painting (software fallback) ─────────────────────────────────────────

    def _paint_software(self, p: QPainter):
        # Antialiasing is the single biggest raster cost for thousands of lines,
        # so skip it while interacting (orbit or zoom) and restore full quality
        # at rest. Panning keeps it on — a pure translate is cheap either way.
        if not self._lod and not self._zooming:
            p.setRenderHint(QPainter.Antialiasing)
        # Fillet the visible black area: clip to a rounded rect and fill only
        # inside it. The corners outside the clip are left transparent (the
        # widget is translucent), so the rounded host frame / white card shows
        # through and the black area reads as one filleted panel.
        r = self.corner_radius
        if r > 0:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), r, r)
            p.setClipPath(clip)
        p.fillRect(self.rect(), _CANVAS_BG)
        if not self._segs:
            return

        w, h = self.width(), self.height()
        cx0, cy0, cz0 = self._center
        cy_, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)

        # While zooming, freeze the projection at the cached distance and show
        # the change as a uniform scale about the view centre (cheap + full
        # quality). Everything (grid, axes, toolpath) is projected at this same
        # frozen distance so it scales together; it snaps to the exact
        # perspective on the next at-rest paint.
        prog = self._progress_line
        hide_done = self.hide_done
        # hide_done is part of the key: the cached projection holds the lines it
        # was built with, and toggling must rebuild it.
        base_key = (self._yaw, self._pitch, prog, w, h, hide_done)
        proj_dist = self._dist
        zoom_scale = 1.0
        if (self._zooming and self._proj_cache is not None
                and self._proj_cache[0] == base_key and self._dist):
            proj_dist = self._proj_cache[1]
            zoom_scale = proj_dist / self._dist

        ex = proj_dist * cp * cy_
        ey = proj_dist * cp * sy
        ez = proj_dist * sp
        flen = math.sqrt(ex * ex + ey * ey + ez * ez) or 1.0
        fx, fy, fz = -ex / flen, -ey / flen, -ez / flen
        rx, ry, rz = fy, -fx, 0.0                  # forward × up(0,0,1)
        rlen = math.sqrt(rx * rx + ry * ry) or 1.0
        rx, ry = rx / rlen, ry / rlen
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx

        focal = (h / 2) / math.tan(math.radians(_FOV_DEG / 2))
        # Project pan-free; pan and zoom are applied as painter transforms so
        # they reuse the cached projection instead of recomputing it. Pan =
        # translate; zoom = scale about the view centre.
        px_c = w / 2
        py_c = h / 2
        p.translate(self._pan.x(), self._pan.y())
        if zoom_scale != 1.0:
            p.translate(px_c, py_c)
            p.scale(zoom_scale, zoom_scale)
            p.translate(-px_c, -py_c)

        def _map(gx, gy, gz):
            dxc, dyc, dzc = gx - cx0 - ex, gy - cy0 - ey, gz - cz0 - ez
            zc = dxc * fx + dyc * fy + dzc * fz
            if zc <= 1e-6:
                return None
            xc = dxc * rx + dyc * ry            # rz == 0
            yc = dxc * ux + dyc * uy + dzc * uz
            return QPointF(px_c + xc / zc * focal, py_c - yc / zc * focal)

        # Grid on the Z floor (10 mm minor, 50 mm major — like GridHelper)
        if self.show_grid:
            ext = max(self._diag * 0.75, 30)
            step = 10
            gz = -self._diag * 0.0 + (self._center[2] - self._diag * 0.0)
            gz = self._center[2] - (self._diag / 2 if False else 0)
            gz = 0.0 if abs(self._center[2]) < self._diag else self._center[2]
            n = int(ext // step)
            for i in range(-n, n + 1):
                g = i * step
                major = (i % 5 == 0)
                p.setPen(QPen(_GRID_MAJOR if major else _GRID_MINOR, 1))
                a = _map(cx0 + g, cy0 - ext, gz)
                b = _map(cx0 + g, cy0 + ext, gz)
                if a and b:
                    p.drawLine(a, b)
                a = _map(cx0 - ext, cy0 + g, gz)
                b = _map(cx0 + ext, cy0 + g, gz)
                if a and b:
                    p.drawLine(a, b)

        # Axes (X red / Y green / Z blue) from the model origin
        if self.show_axes:
            alen = self._diag * 0.4
            o = _map(0, 0, 0)
            for axis, color in ((( alen, 0, 0), _AXIS_X),
                                ((0,  alen, 0), _AXIS_Y),
                                ((0, 0,  alen), _AXIS_Z)):
                tip = _map(*axis)
                if o and tip:
                    p.setPen(QPen(color, 2))
                    p.drawLine(o, tip)

        # Toolpath — batched by pen so the painter is touched 3 times instead of
        # once per segment. Projection is the expensive part, so:
        #   • orbit / zoom (self._lod): re-project a decimated copy (merged into
        #     connected chords) with antialiasing off, for a smooth interaction;
        #   • at rest and while panning: use the full toolpath at full quality,
        #     reusing the cached projection so a pan is just a painter translate.
        segs = self._segs
        n = len(segs)
        ox, oy, oz = cx0 + ex, cy0 + ey, cz0 + ez

        def _project(src):
            rapid, done, pending = [], [], []
            for x0, y0, z0, x1, y1, z1, is_rapid, line_idx in src:
                if hide_done and line_idx <= prog:
                    continue            # executed: left out, rapids included
                dxa, dya, dza = x0 - ox, y0 - oy, z0 - oz
                za = dxa * fx + dya * fy + dza * fz
                if za <= 1e-6:
                    continue
                dxb, dyb, dzb = x1 - ox, y1 - oy, z1 - oz
                zb = dxb * fx + dyb * fy + dzb * fz
                if zb <= 1e-6:
                    continue
                ax = px_c + (dxa * rx + dya * ry) / za * focal
                ay = py_c - (dxa * ux + dya * uy + dza * uz) / za * focal
                bx = px_c + (dxb * rx + dyb * ry) / zb * focal
                by = py_c - (dxb * ux + dyb * uy + dzb * uz) / zb * focal
                line = QLineF(ax, ay, bx, by)
                if is_rapid:
                    rapid.append(line)
                elif line_idx <= prog:
                    done.append(line)
                else:
                    pending.append(line)
            return rapid, done, pending

        cache = self._proj_cache
        if self._lod:
            # Orbiting — re-project a decimated copy (antialiasing off).
            src = segs
            if n > _LOD_MAX_SEGS:
                if self._lod_segs is None:
                    self._lod_segs = self._build_lod_segs()
                src = self._lod_segs
            rapid_lines, done_lines, pending_lines = _project(src)
        elif self._zooming and cache is not None and cache[0] == base_key:
            # Zooming — reuse the cached lines (projected at the frozen
            # distance) and let the painter scale above do the zoom.
            rapid_lines, done_lines, pending_lines = cache[2], cache[3], cache[4]
        elif (not self._zooming and cache is not None
                and cache[0] == base_key and cache[1] == self._dist):
            # At rest / panning — exact cache hit, reuse directly.
            rapid_lines, done_lines, pending_lines = cache[2], cache[3], cache[4]
        else:
            # Full projection at the current (frozen while zooming) distance;
            # cache it for subsequent pans / zooms.
            rapid_lines, done_lines, pending_lines = _project(segs)
            self._proj_cache = (base_key, proj_dist,
                                rapid_lines, done_lines, pending_lines)

        if rapid_lines:
            rapid_pen = QPen(_RAPID_C)
            rapid_pen.setStyle(Qt.DashLine)
            p.setPen(rapid_pen)
            p.drawLines(rapid_lines)
        if done_lines:
            done_pen = QPen(_DONE_C)
            done_pen.setWidthF(1.8)
            p.setPen(done_pen)
            p.drawLines(done_lines)
        if pending_lines:
            pending_pen = QPen(_PENDING_C)
            pending_pen.setWidthF(1.6)
            p.setPen(pending_pen)
            p.drawLines(pending_lines)


class LivePreviewPage(QWidget):
    #: The opened file's toolpath read: percent done, and its end (the file
    #: name, drawn or failed). The "Opening file" popup waits on these.
    read_progress = Signal(int)
    read_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self._conn = None
        self._current_file: str | None = None
        self._loaded_file: str | None = None   # file currently drawn on the canvas
        self._machine_busy = False             # combined busy gate (see below)
        self._status_busy = False              # Run/Hold/Home/Alarm/Door part
        # Cache-freshness check: file name/path awaiting the machine's stored
        # SHA-256 (None when no check is pending).
        self._hash_name: str | None = None
        self._hash_path: str | None = None
        self._hash_timer = QTimer(self)
        self._hash_timer.setSingleShot(True)
        self._hash_timer.setInterval(2500)
        self._hash_timer.timeout.connect(self._on_hash_timeout)
        # Progress-recolor throttle (see _on_job_progress).
        self._progress_last_line = -2
        self._progress_paint_timer = QElapsedTimer()
        self._prefs = QSettings("InMachines", "MiniCNC2")
        self.setStyleSheet(TRANSPARENT)

        self._chips: list[tuple[QPushButton, str | None]] = []
        self._parse_task = None          # _ParseTask reading a file, or None
        self._parse_notice = None        # delay before the progress readout
        self._parse_name = None
        self._parse_pct = 0

        self._root = QVBoxLayout(self)

        self._host = QFrame()
        self._host.setObjectName("lpHost")
        self._hl = QVBoxLayout(self._host)

        # ── Canvas / empty state fill the whole black host ───────────────────
        self._canvas = Gcode3DCanvas()
        self._canvas.show_grid = self._prefs.value("previewGrid", True, bool)
        self._canvas.show_axes = self._prefs.value("previewAxes", True, bool)
        self._canvas.hide_done = self._prefs.value("previewHideDone", False, bool)
        self._hl.addWidget(self._canvas, 1)

        self._empty_host = QWidget()
        self._empty_host.setStyleSheet(TRANSPARENT)
        self._ev = QVBoxLayout(self._empty_host)
        self._ev.setAlignment(Qt.AlignCenter)
        self._empty_lbl = QLabel("No open file")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        # Hold the label to its text height. Left to expand it swallows the
        # layout's spare space, which pushes anything below it (the offline
        # cache picker) down to the bottom edge. The layout is centred, so the
        # text still sits mid-panel when it is the only thing showing.
        self._empty_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._ev.addWidget(self._empty_lbl)
        self._fetch_btn = QPushButton("Generate preview")
        self._fetch_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._fetch_btn.clicked.connect(self._fetch_current)
        self._fetch_btn.hide()
        self._ev.addWidget(self._fetch_btn, alignment=Qt.AlignHCenter)
        # Mobile parity: no new preview while the machine is busy — the button
        # greys out and this short hint explains why.
        self._busy_hint = QLabel("Preview unavailable while the machine is busy")
        self._busy_hint.setAlignment(Qt.AlignCenter)
        self._busy_hint.hide()
        self._ev.addWidget(self._busy_hint)
        # Offline (dev) mode has no machine to report an opened file, so the
        # empty state offers the local preview cache instead — every toolpath
        # downloaded on a previous run, ready to draw without hardware.
        self._picker = QScrollArea()
        self._picker.setWidgetResizable(True)
        self._picker.setFrameShape(QFrame.NoFrame)
        self._picker.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._picker_host = QWidget()
        self._picker_lay = QVBoxLayout(self._picker_host)
        self._picker_lay.setAlignment(Qt.AlignTop)
        self._picker.setWidget(self._picker_host)
        self._picker.hide()
        # No stretch: the list stays tight under its label instead of taking
        # the leftover height and leaving a gap.
        self._ev.addWidget(self._picker, 0, Qt.AlignHCenter)
        self._hl.addWidget(self._empty_host, 1)
        self._canvas.hide()

        # ── Floating overlay buttons (semi-transparent, over the canvas) ─────
        # Grouped at the edges like the mobile app: clear-cache top-left, view-
        # face shortcuts bottom-left, grid/origin/reset stacked on the right,
        # and the navigation-help button top-right. The groups are children of
        # the black host so their translucent fills blend over the 3-D canvas.
        self._ov_tl = self._overlay_group(horizontal=True)
        self._clear_btn = self._chip("  Clear stored cache", icon_name="reset")
        self._clear_btn.clicked.connect(self._clear_cache)
        self._ov_tl.layout().addWidget(self._clear_btn)

        # Under them: what the opened job needs at the machine -- how deep it
        # cuts and which tool(s), read from the header the CAM export writes
        # (ui/job_summary.py). A tool the header flags as too short is shown
        # in amber, so the warning is in front of the operator before start.
        self._ov_info = self._overlay_group(horizontal=True)
        self._ov_info.hide()

        self._ov_bl = self._overlay_group(horizontal=True)
        for side in ("front", "back", "left", "right", "top", "bottom"):
            b = self._chip(side.capitalize())
            b.clicked.connect(lambda _c=False, s=side: self._canvas.set_view(s))
            self._ov_bl.layout().addWidget(b)

        # Grid / origin / reset stack in the BOTTOM-RIGHT corner, reset at the
        # bottom with the toggles above it (mobile layout: homeBtn / gridBtn /
        # axesBtn stacked up from bottom-right).
        self._ov_br = self._overlay_group(horizontal=False)
        # Already-cut toolpath: shown (orange) or left out, so a long job's
        # remaining work is not buried under everything already done. Checked
        # means shown, like the grid and origin toggles below it.
        self._done_btn = self._chip("", checkable=True)
        self._done_btn.setChecked(not self._canvas.hide_done)
        self._done_btn.setToolTip("Show or hide the part of the job that has already been cut")
        self._done_btn.toggled.connect(self._toggle_done)
        self._ov_br.layout().addWidget(self._done_btn)
        self._axes_btn = self._chip("", checkable=True)
        self._axes_btn.setChecked(self._canvas.show_axes)
        self._axes_btn.toggled.connect(self._toggle_axes)
        self._ov_br.layout().addWidget(self._axes_btn)
        self._grid_btn = self._chip("", checkable=True)
        self._grid_btn.setChecked(self._canvas.show_grid)
        self._grid_btn.toggled.connect(self._toggle_grid)
        self._ov_br.layout().addWidget(self._grid_btn)
        self._home_b = self._chip("  Reset view", icon_name="home")
        self._home_b.setToolTip("Reset view")
        self._home_b.clicked.connect(lambda: self._canvas.reset_view())
        self._ov_br.layout().addWidget(self._home_b)
        self._update_toggle_labels()

        self._ov_tr = self._overlay_group(horizontal=True)
        self._help_b = self._chip("?")
        self._help_b.setToolTip("How to navigate")
        self._help_b.clicked.connect(self._toggle_help)
        self._ov_tr.layout().addWidget(self._help_b)

        self._overlays = [self._ov_tl, self._ov_bl, self._ov_br, self._ov_tr,
                          self._ov_info]

        self._root.addWidget(self._host, 1)
        self._host.resizeEvent = self._on_host_resize

        # Help overlay (mobile "How to navigate" modal) — child of the host so
        # it centres correctly whether the canvas or the empty state is showing.
        self._help = QFrame(self._host)
        self._help.setObjectName("helpCard")
        self._hv = QVBoxLayout(self._help)
        self._help_title = QLabel("How to navigate")
        self._hv.addWidget(self._help_title)
        self._help_rows: list[QLabel] = []
        for action, gesture in [
                ("Orbit", "hold the left mouse button and drag"),
                ("Pan", "hold the right mouse button and drag (or shift+drag)"),
                ("Zoom", "scroll the mouse wheel (limits keep the path in view)"),
                ("Reset", "click the home button to restore the start view"),
                ("Views", "front / back / left / right / top / bottom snap the camera")]:
            row = QLabel(f"<b style='color:#E8EAF0'>{action}</b>"
                         f"<span style='color:#A8ACB4'> — {gesture}</span>")
            self._help_rows.append(row)
            self._hv.addWidget(row)
        self._close_hint = QLabel("Click to close")
        self._close_hint.setAlignment(Qt.AlignCenter)
        self._hv.addWidget(self._close_hint)
        self._help.hide()
        self._help.mousePressEvent = lambda e: self._hide_help()

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._root.setContentsMargins(px(12), px(12), px(12), px(12))
        self._root.setSpacing(0)

        self._host.setStyleSheet(
            f"#lpHost {{ background: #0E0E10; border-radius: {px(12)}px; }}")
        self._canvas.corner_radius = px(12)   # keep the fillet at the new scale
        self._hl.setContentsMargins(0, 0, 0, 0)
        self._hl.setSpacing(0)

        for g in self._overlays:
            g.layout().setContentsMargins(0, 0, 0, 0)
            g.layout().setSpacing(px(8))

        for b, icon_name in self._chips:
            b.setFixedHeight(px(34))
            # Stronger, darker gray to match the mobile overlay buttons
            # (rgba(30,30,30,0.85) over the 3-D canvas).
            b.setStyleSheet(f"""
                QPushButton {{ background: rgba(30, 30, 30, 85%);
                               border: 1px solid rgba(255, 255, 255, 20%);
                               border-radius: {px(8)}px;
                               font-size: {fs(12)}px; color: #E8EAF0;
                               padding: 0 {px(12)}px; }}
                QPushButton:hover   {{ background: rgba(46, 46, 46, 92%); }}
                QPushButton:checked {{ background: rgba(110, 201, 226, 25%);
                                       border: 1px solid #6EC9E2; }}
                QPushButton:disabled {{ background: rgba(30, 30, 30, 55%);
                                        border: 1px solid rgba(255,255,255,8%);
                                        color: #6E727A; }}
            """)
            if icon_name:
                b.setIcon(icons.icon(icon_name, px(15), "#E8EAF0"))
        self._help_b.setFixedWidth(px(40))

        self._ev.setSpacing(px(14))
        self._empty_lbl.setStyleSheet(
            f"background: transparent; font-size: {fs(16)}px; color: #8A8E96;")
        self._fetch_btn.setFixedHeight(px(36))
        self._fetch_btn.setStyleSheet(f"""
            QPushButton {{ background: #5A5E66; border: none;
                           border-radius: {px(8)}px;
                           font-size: {fs(12)}px; color: #E8EAF0;
                           padding: 0 {px(18)}px; }}
            QPushButton:hover {{ background: #6A6E76; }}
            QPushButton:disabled {{ background: #3A3E45; color: #7A7E86; }}
        """)
        self._busy_hint.setStyleSheet(
            f"background: transparent; font-size: {fs(11)}px; color: #8A8E96;")
        self._style_picker()

        self._reposition_overlays()

        self._help.setStyleSheet(f"""
            #helpCard {{ background: rgba(20,20,24,0.92);
                        border: 1px solid #3A3E45;
                        border-radius: {px(14)}px; }}
        """)
        self._hv.setContentsMargins(px(26), px(20), px(26), px(20))
        self._hv.setSpacing(px(10))
        self._help_title.setStyleSheet(
            f"background: transparent; color: #E8EAF0; "
            f"font-size: {fs(15)}px; font-weight: bold;")
        for row in self._help_rows:
            row.setStyleSheet(
                f"background: transparent; font-size: {fs(12)}px;")
        self._close_hint.setStyleSheet(
            f"background: transparent; color: #6A6E76; font-size: {fs(10)}px;")

    # ── Chip factory ─────────────────────────────────────────────────────────

    def _chip(self, text: str, icon_name: str | None = None,
              checkable: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(checkable)
        b.setCursor(QCursor(Qt.PointingHandCursor))
        self._chips.append((b, icon_name))
        return b

    def _overlay_group(self, horizontal: bool) -> QWidget:
        """A transparent floating cluster (child of the black host) holding a
        row/column of semi-transparent overlay buttons."""
        g = QWidget(self._host)
        g.setStyleSheet(TRANSPARENT)
        lay = QHBoxLayout(g) if horizontal else QVBoxLayout(g)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(8))
        return g

    # ── Overlay placement ──────────────────────────────────────────────────────

    def _on_host_resize(self, e) -> None:
        QFrame.resizeEvent(self._host, e)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        """Pin the floating clusters to the corners/edges of the black host."""
        if not hasattr(self, "_overlays"):
            return
        m = px(12)
        hw, hh = self._host.width(), self._host.height()
        for g in self._overlays:
            g.adjustSize()
        self._ov_tl.move(m, m)
        self._ov_info.move(m, m + self._ov_tl.height() + px(8))
        self._ov_tr.move(max(m, hw - self._ov_tr.width() - m), m)
        # Grid / origin / reset pinned to the bottom-right corner.
        br_x = max(m, hw - self._ov_br.width() - m)
        br_y = max(m, hh - self._ov_br.height() - m)
        self._ov_br.move(br_x, br_y)
        # View-face shortcuts along the bottom-left; if the window is narrow
        # enough that they would run under the bottom-right stack, lift them
        # onto the row just above it so nothing overlaps.
        bl_y = max(m, hh - self._ov_bl.height() - m)
        if (m + self._ov_bl.width() > br_x - m
                and bl_y + self._ov_bl.height() > br_y):
            bl_y = max(m, br_y - self._ov_bl.height() - px(8))
        self._ov_bl.move(m, bl_y)
        for g in self._overlays:
            g.raise_()

    def _update_toggle_labels(self) -> None:
        self._done_btn.setText("Hide done" if self._done_btn.isChecked()
                               else "Show done")
        self._grid_btn.setText("Hide grid" if self._grid_btn.isChecked()
                               else "Show grid")
        self._axes_btn.setText("Hide origin" if self._axes_btn.isChecked()
                               else "Show origin")

    # ── Toolbar actions ──────────────────────────────────────────────────────

    def _toggle_done(self, shown: bool) -> None:
        self._canvas.set_hide_done(not shown)
        self._prefs.setValue("previewHideDone", not shown)
        self._update_toggle_labels()
        self._reposition_overlays()

    def _toggle_grid(self, on: bool) -> None:
        self._canvas.show_grid = on
        self._prefs.setValue("previewGrid", on)
        self._canvas.update()
        self._update_toggle_labels()
        self._reposition_overlays()

    def _toggle_axes(self, on: bool) -> None:
        self._canvas.show_axes = on
        self._prefs.setValue("previewAxes", on)
        self._canvas.update()
        self._update_toggle_labels()
        self._reposition_overlays()

    def _toggle_help(self) -> None:
        if self._help.isVisible():
            self._hide_help()
            return
        self._help.adjustSize()
        hw, hh = self._host.width(), self._host.height()
        self._help.move((hw - self._help.width()) // 2,
                        (hh - self._help.height()) // 2)
        self._help.show()
        self._help.raise_()
        # Dismiss the card on the next click anywhere in the window (mobile
        # parity: tapping outside the "How to navigate" modal closes it). An
        # invisible layer over the whole window takes that click and swallows
        # it, so it does not also trigger something behind. Not an application
        # event filter: a Python one crashes PySide as soon as the CAM tab's
        # web view runs (see ui/tooltips.py).
        win = self.window()
        self._help_shield = QWidget(win)
        self._help_shield.setStyleSheet(TRANSPARENT)
        self._help_shield.setGeometry(win.rect())
        self._help_shield.mousePressEvent = lambda e: self._hide_help()
        self._help_shield.show()
        self._help_shield.raise_()

    def _hide_help(self) -> None:
        shield = getattr(self, "_help_shield", None)
        if shield is not None:
            self._help_shield = None
            shield.hide()
            shield.deleteLater()
        if self._help.isVisible():
            self._help.hide()

    # ── State handling ───────────────────────────────────────────────────────

    def _show_canvas(self, show: bool) -> None:
        self._canvas.setVisible(show)
        self._empty_host.setVisible(not show)
        # View-manipulation clusters only make sense over a drawn toolpath; the
        # clear-cache and help clusters stay available in the empty state too.
        self._ov_bl.setVisible(show)
        self._ov_br.setVisible(show)
        # The job's min Z / tools belong to the drawn toolpath: gone with it
        # (no open file, a parse in progress), back when it is re-shown.
        self._ov_info.setVisible(show and self._ov_info.layout().count() > 0)
        if show:
            self._reposition_overlays()

    # ── Offline cache picker ─────────────────────────────────────────────────

    # Sidecars and other caches share the directory; only toolpaths are
    # drawable. .fwhash records the firmware's digest, .ply is the point-cloud
    # viewer's cache.
    _PICKABLE = (".nc", ".gcode", ".ngc", ".tap", ".gc", ".cnc", ".txt")

    def _cached_toolpaths(self) -> list:
        """Cached toolpath files, newest first — the useful order for testing."""
        from connection import Connection
        out = []
        for name, size in Connection.cache_contents():
            if not name.lower().endswith(self._PICKABLE):
                continue
            path = Connection.cached_file_path(name)
            if not path:
                continue
            try:
                out.append((os.path.getmtime(path), name, size, path))
            except OSError:
                continue
        out.sort(reverse=True)
        return out

    def _style_picker(self) -> None:
        """Size the picker to the scale factor; called on every rescale and
        after a refill (the rows are rebuilt, so they need styling again)."""
        row_h, gap = px(32), px(6)
        self._picker.setFixedWidth(px(420))
        # A scroll area expands to whatever it is given, which would strand the
        # list at the bottom of the empty state. Pin it to the rows it actually
        # has, capped so a large cache still leaves the label visible.
        rows = self._picker_lay.count()
        self._picker.setFixedHeight(
            min(px(260), max(row_h, rows * (row_h + gap))))
        self._picker.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._picker_host.setStyleSheet(TRANSPARENT)
        self._picker_lay.setContentsMargins(0, 0, px(16), 0)  # clear the scrollbar
        self._picker_lay.setSpacing(gap)
        for i in range(self._picker_lay.count()):
            b = self._picker_lay.itemAt(i).widget()
            if b is None:
                continue
            b.setFixedHeight(px(32))
            b.setStyleSheet(f"""
                QPushButton {{ background: #2A2E35; border: none;
                               border-radius: {px(7)}px; text-align: left;
                               font-size: {fs(12)}px; color: #C8CCD4;
                               padding: 0 {px(12)}px; }}
                QPushButton:hover {{ background: #3A3E45; color: #E8EAF0; }}
            """)

    def _fill_picker(self) -> None:
        while self._picker_lay.count():
            w = self._picker_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for _mtime, name, size, path in self._cached_toolpaths():
            mb = size / 1e6
            b = QPushButton(f"{name}   ·   {mb:.1f} MB" if mb >= 0.1
                            else f"{name}   ·   {size / 1e3:.0f} kB")
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.clicked.connect(
                lambda _c=False, n=name, p=path: self._pick_cached(n, p))
            self._picker_lay.addWidget(b)
        self._style_picker()

    def _pick_cached(self, name: str, path: str) -> None:
        """Draw a cached file chosen by hand (offline mode only)."""
        self._current_file = name
        self._picker.hide()
        self._empty_lbl.hide()
        self._render_cached(name, path)

    def _offline(self) -> bool:
        return bool(self._conn and self._conn.is_offline)

    def _on_opened_file(self, name) -> None:
        self._cancel_hash_check()          # a file change supersedes any check
        # ...and so does a read in flight. Parenthesised: without them Python
        # reads `a if c else b != d` as `a if c else (b != d)`.
        if (str(name) if name else None) != self._current_file:
            self._cancel_parse()
        self._current_file = str(name) if name else None
        if not self._current_file:
            self._loaded_file = None
            self._canvas.load(None)
            self._fetch_btn.hide()
            self._update_fetch_gate()
            self._show_canvas(False)
            if self._offline():
                # No machine to open a file, so offer the cache instead.
                files = self._cached_toolpaths()
                self._empty_lbl.setText(
                    "Offline (dev) — pick a cached toolpath"
                    if files else
                    "Offline (dev) — the preview cache is empty")
                self._empty_lbl.show()
                self._fill_picker()
                self._picker.setVisible(bool(files))
                return
            self._picker.hide()
            self._empty_lbl.setText("No open file")
            self._empty_lbl.show()
            return
        self._picker.hide()
        path = self._conn.cached_file_path(self._current_file) if self._conn else None
        if path:
            # A local cache exists → just show it (no re-download).
            self._render_cached(self._current_file, path)
        else:
            # Not cached locally → the user must click "generate preview"
            # to download it (mirroring the mobile app). No auto-download.
            self._loaded_file = None
            self._empty_lbl.hide()
            self._fetch_btn.show()
            self._update_fetch_gate()
            self._show_canvas(False)

    #: Below this, opening looks instant and a progress readout would be a
    #: flicker. A 3 MB file parses in about a third of a second; the delay is
    #: the honest way to say "only report this when there is something to
    #: report".
    _PARSE_NOTICE_MS = 500

    def _render_cached(self, name: str, path: str) -> None:
        """Draw the locally cached copy of *name*.

        The read happens on a worker thread. It is linear in file size — 3.3 s
        for 33 MB — and the GUI thread is what answers the firmware's 5 s ping
        watchdog, so doing it here is what dropped the connection on a large
        file.

        Re-showing the file already on screen skips the whole thing, which is
        what keeps a page-show re-sync cheap and leaves the camera where the
        operator put it.
        """
        if self._loaded_file == name and self._parse_task is None:
            self._show_canvas(self._canvas.has_data)
            return
        self._cancel_parse()
        self._parse_name = name
        self._show_canvas(False)
        self._fetch_btn.hide()
        self._update_fetch_gate()
        # Silent for the first half-second: see _PARSE_NOTICE_MS.
        self._parse_notice = QTimer(self)
        self._parse_notice.setSingleShot(True)
        self._parse_notice.setInterval(self._PARSE_NOTICE_MS)
        self._parse_notice.timeout.connect(self._show_parse_notice)
        self._parse_notice.start()

        self._ov_info.hide()       # the previous job's facts no longer apply
        task = _ParseTask(path, pack_path, self)
        self._parse_task = task
        task.progress.connect(self._on_parse_progress)
        task.ready.connect(lambda segs, b, packed, cut, n=name, p=path:
                           self._on_parse_ready(n, segs, b, packed, p, cut))
        task.failed.connect(self._on_parse_failed)
        task.unsized.connect(self._on_parse_unsized)
        task.finished.connect(lambda t=task: self._forget_parse(t))
        task.start()

    def _show_parse_notice(self) -> None:
        if self._parse_task is None:
            return
        self._empty_lbl.setText("Reading toolpath… %d%%" % self._parse_pct)
        self._empty_lbl.show()

    def is_reading(self) -> bool:
        """True while the opened file's toolpath is being read."""
        return self._parse_task is not None

    def _on_parse_progress(self, pct: int) -> None:
        self._parse_pct = pct
        self.read_progress.emit(pct)
        if self._parse_notice is not None and not self._parse_notice.isActive():
            self._empty_lbl.setText("Reading toolpath… %d%%" % pct)
            self._empty_lbl.show()

    def _on_parse_ready(self, name, segs, bounds, packed, path=None,
                        cut=None) -> None:
        # A file change while this was reading supersedes it.
        if name != self._current_file:
            return
        self._stop_parse_notice()
        self._canvas.set_parsed(segs, bounds, packed)
        self._set_job_info(path, bounds if segs else None, cut)
        self._loaded_file = name
        self._show_canvas(self._canvas.has_data)
        if not self._canvas.has_data:
            self._empty_lbl.setText("No drawable toolpath found in file")
            self._empty_lbl.show()
            self._fetch_btn.hide()
            self._update_fetch_gate()
        self.read_finished.emit(name)

    #: Info chip looks: the normal overlay grey, and amber for a warning.
    _INFO_STYLE = ("background: rgba(30, 30, 30, 85%%); "
                   "border: 1px solid rgba(255, 255, 255, 20%%); "
                   "border-radius: %(r)spx; color: #E8EAF0; "
                   "font-size: %(f)spx; padding: 0 %(p)spx;")
    _ORIGIN_STYLE = ("background: rgba(110, 201, 226, 18%%); "
                     "border: 1px solid #6EC9E2; "
                     "border-radius: %(r)spx; color: #CDEFF8; "
                     "font-size: %(f)spx; padding: 0 %(p)spx;")
    _WARN_STYLE = ("background: rgba(70, 48, 0, 88%%); "
                   "border: 1px solid #E0A030; "
                   "border-radius: %(r)spx; color: #FFD27A; "
                   "font-size: %(f)spx; padding: 0 %(p)spx;")

    def _set_job_info(self, path, bounds, cut=None) -> None:
        """Show the opened job's origin, minimum Z and tool(s) under the
        top-left row.

        Min Z is the header's own ``ZMIN`` when there is one -- the cutting
        depth, as the CAM export measured it -- and otherwise the lowest point
        of the toolpath just drawn. The origin is the header's ``; origin``
        line when there is one; a file from other CAM software has none, and
        then it is estimated from *cut*, the extent of the feed moves, and
        marked as such. The tools exist only in the header; a file without one
        says so rather than naming a tool it cannot know.
        """
        from ui import job_summary
        lay = self._ov_info.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        info = job_summary.read(path) if path else {"tools": [], "zmin": None,
                                                     "warnings": {}}
        zmin = info["zmin"]
        if zmin is None and bounds:
            zmin = bounds[2]
        warned = info["warnings"]
        # Rotary Mode job: Z 0 is the rotary axis and every Z a radius, so
        # the chips say so and give the depth from the bar's surface
        rot = info.get("rotary")
        rot_top = bool(info.get("rotary_top"))
        sty = {"r": px(8), "f": fs(12), "p": px(12)}

        def chip(text, tip="", warn=False, style=None):
            c = QLabel(text)
            c.setFixedHeight(px(34))
            if style is None:
                style = self._WARN_STYLE if warn else self._INFO_STYLE
            c.setStyleSheet(style % sty)
            if tip:
                c.setToolTip(tip)
            lay.addWidget(c)

        if info.get("origin") and rot is not None:
            xy, z = job_summary.origin_text(info["origin"], rot, rot_top)
            how = ("Set Z0 with the tool touching the top of the mounted "
                   "bar." if rot_top else
                   "Set Z0 with the tool on the tailstock centre point "
                   "before the bar goes in.")
            chip(f"Origin  {xy}  ·  {z}",
                 f"Work zero: X0 at the {xy} of the bar, Z0 on the {z}, "
                 f"Y locked. {how}",
                 style=self._ORIGIN_STYLE)
        elif info.get("origin"):
            xy, z = job_summary.origin_text(info["origin"])
            chip(f"Origin  {xy}  ·  {z}",
                 f"Work zero (X0 Y0 Z0) is at the {xy} of the stock, on the "
                 f"{z}. Set the machine's zero there before starting.",
                 style=self._ORIGIN_STYLE)
        elif cut:
            guess = job_summary.origin_from_cut(cut, info["tools"])
            if guess:
                xy, z = job_summary.origin_text(guess)
                chip(f"Origin ≈ {xy}  ·  {z}",
                     f"Work zero (X0 Y0 Z0) looks to be at the {xy} of the "
                     f"part, on the {z}. This file does not say where its "
                     "origin is, so this is read from where the toolpath "
                     "cuts — check it against the CAM setup before "
                     "setting the machine's zero.",
                     style=self._ORIGIN_STYLE)
        if zmin is not None and rot is not None:
            depth = -zmin if rot_top else rot - zmin
            chip(f"Min Z  {job_summary._fmt(zmin)} mm  ·  "
                 f"{job_summary._fmt(depth)} mm into the bar",
                 ("Lowest Z the job cuts to, from the top of the bar (Z0): "
                  if rot_top else
                  "Smallest radius the job cuts to, from the rotary axis (Z0): ")
                 + f"{job_summary._fmt(depth)} mm below the bar's surface "
                 f"(radius {job_summary._fmt(rot)}). The tool's flutes must "
                 "reach at least that far.",
                 warn=bool(warned))
        elif zmin is not None:
            chip(f"Min Z  {job_summary._fmt(zmin)} mm",
                 "Lowest point the job cuts to, from the program's work zero. "
                 "The tool must reach at least this deep below the stock top.",
                 warn=bool(warned))
        if info["tools"]:
            for t in info["tools"]:
                w = warned.get(t["number"])
                text = job_summary.tool_text(t)
                tip = f'"{t["name"]}"' if t.get("name") else ""
                if w:
                    text = "⚠  " + text + "  ·  too short"
                    tip = (tip + "\n" if tip else "") + w
                chip(text, tip, warn=bool(w))
        self._ov_info.setVisible(lay.count() > 0)
        self._reposition_overlays()

    def _on_parse_failed(self, text: str) -> None:
        self._ov_info.hide()
        self._stop_parse_notice()
        self._loaded_file = None
        self._show_canvas(False)
        self._empty_lbl.setText("Could not read the toolpath — %s" % text)
        self._empty_lbl.show()
        self.read_finished.emit(self._parse_name or "")

    #: Shown in place of a rotary job that cannot be drawn at its real size.
    UNSIZED_TEXT = ("Bar size unknown — this rotary job measures Z from the "
                    "top of the bar, but the file does not say how big the "
                    "bar is.\nExport it again from the CAM page and send it "
                    "to the machine to preview it.")

    def _on_parse_unsized(self) -> None:
        """A rotary job with Z 0 on the bar but no bar size in the file
        (see parse_gcode): say so rather than draw it a bar radius too
        small. Nothing is kept, so the previous job cannot show through."""
        self._ov_info.hide()
        self._stop_parse_notice()
        self._loaded_file = None
        self._canvas.load(None)
        self._show_canvas(False)
        self._empty_lbl.setText(self.UNSIZED_TEXT)
        self._empty_lbl.show()
        self.read_finished.emit(self._parse_name or "")

    def _stop_parse_notice(self) -> None:
        if self._parse_notice is not None:
            self._parse_notice.stop()
            self._parse_notice = None
        self._parse_pct = 0

    def _forget_parse(self, task) -> None:
        if self._parse_task is task:
            self._parse_task = None

    def _cancel_parse(self) -> None:
        """Abandon a read in flight. Its thread notices at the next progress
        tick and exits; nothing waits for it, because the only thing it still
        holds is a file handle."""
        self._stop_parse_notice()
        task, self._parse_task = self._parse_task, None
        if task is not None:
            task.cancel()

    def _fetch_current(self) -> None:
        # "generate preview" is only offered for a file that isn't cached, and
        # is blocked while the machine is busy.
        if not (self._conn and self._current_file) or self._machine_busy:
            return
        self._start_download(self._current_file)

    def _start_download(self, name: str) -> None:
        self._empty_lbl.setText("Preparing preview… 0%")
        self._empty_lbl.show()
        self._fetch_btn.hide()
        self._update_fetch_gate()
        self._show_canvas(False)
        self._conn.download_file(name)

    # ── Cache freshness (hash-verified, Android parity) ──────────────────────

    def _begin_hash_check(self, name: str, path: str) -> None:
        """Confirm the cached copy of *name* is still current before trusting
        it. The connection records the firmware-reported SHA-256 alongside
        every download (sidecar), so freshness = "the machine still reports
        the hash we downloaded". No sidecar (e.g. a cache seeded verbatim at
        upload time, which lacks the firmware's metadata line) → re-download
        once to get the machine-exact copy. No reply / no stored hash on the
        machine → the check is impossible; trust the cache as before."""
        known = self._conn.cached_file_fw_hash(name)
        if known is None:
            self._start_download(name)
            return
        self._cancel_hash_check()
        self._hash_name = name
        self._hash_path = path
        self._empty_lbl.setText("Verifying cached preview…")
        self._empty_lbl.show()
        self._fetch_btn.hide()
        self._update_fetch_gate()
        self._show_canvas(False)
        self._conn.request_file_hash(name)
        self._hash_timer.start()

    def _cancel_hash_check(self) -> None:
        self._hash_timer.stop()
        self._hash_name = None
        self._hash_path = None

    def _on_file_hash(self, fname: str, sha: str) -> None:
        # Ignore replies for other files and the connection's own
        # post-download integrity checks (no check pending here for those).
        if self._hash_name is None or fname != self._hash_name:
            return
        name, path = self._hash_name, self._hash_path
        self._cancel_hash_check()
        if name != self._current_file:
            return
        sha = str(sha).strip().lower()
        known = self._conn.cached_file_fw_hash(name)
        if not sha or (known and known == sha) or self._machine_busy:
            # "" = machine has no stored hash (older file): unverifiable —
            # keep the cache rather than re-downloading on every open. A
            # machine that went busy mid-check also falls back to the cache
            # (never start a download competing with a running job).
            self._render_cached(name, path)
        else:
            self._start_download(name)     # changed on the machine

    def _on_hash_timeout(self) -> None:
        if self._hash_name is None:
            return
        name, path = self._hash_name, self._hash_path
        self._cancel_hash_check()
        if name == self._current_file:
            # No answer (e.g. firmware without gcodeFileHash): behave like
            # the pre-check code and render the cache directly.
            self._render_cached(name, path)

    def _on_download_progress(self, name: str, pct: int) -> None:
        if name == self._current_file and not self._canvas.isVisible():
            self._empty_lbl.setText(f"Preparing preview… {pct}%")
            self._empty_lbl.show()

    def _on_download_done(self, name: str, path: str) -> None:
        if name != self._current_file:
            return
        self._cancel_hash_check()          # fresh copy supersedes any check
        if path:
            # The same worker as a cached open: a file just downloaded is the
            # largest one the operator is likely to open, and the link has just
            # carried it, so this is the worst moment to stall the loop.
            self._loaded_file = None
            self._render_cached(name, path)
        else:
            self._empty_lbl.setText("Preview failed — try again")
            self._empty_lbl.show()
            self._fetch_btn.show()
            self._update_fetch_gate()

    def _on_job_progress(self, jp: dict) -> None:
        # Recoloring the executed path repaints the whole canvas (a software
        # reprojection of every segment), and the poll advances currentLine
        # up to 5x per second during a run — cap the recolor rate. A line
        # that moved backwards (new job start) applies immediately.
        line = int(jp.get("currentLine", -1))
        if line == self._progress_last_line:
            return
        if (line > self._progress_last_line
                and self._progress_paint_timer.isValid()
                and self._progress_paint_timer.elapsed() < 400):
            return
        self._progress_last_line = line
        self._progress_paint_timer.restart()
        self._canvas.set_progress_line(line)

    def _on_machine_status(self, status: str) -> None:
        # "Jog" = manual motion; blocked too so a jog can't compete with a
        # preview download for the machine link.
        self._status_busy = (str(status).split(":")[0].strip()
                             in ("Run", "Jog", "Hold", "Home", "Alarm", "Door"))
        self._recompute_busy()

    def _recompute_busy(self) -> None:
        """Generating a preview is blocked while the machine is busy — a job
        (running/held), manual motion (jog), homing/alarm, or a guided
        procedure (procedureBusy). An already-rendered preview stays untouched."""
        flags = self._conn.flags() if self._conn else {}
        busy = (self._status_busy
                or bool(flags.get("procedureBusy", False))
                or bool(flags.get("jobRunning", False))
                or bool(flags.get("jobPaused", False)))
        if busy != self._machine_busy:
            self._machine_busy = busy
            self._update_fetch_gate()

    def shutdown(self) -> None:
        """Called by MainWindow.closeEvent, like the CAM page's own."""
        self._cancel_parse()

    def _update_fetch_gate(self) -> None:
        self._fetch_btn.setEnabled(not self._machine_busy)
        self._busy_hint.setVisible(self._machine_busy
                                   and not self._fetch_btn.isHidden())

    def _clear_cache(self) -> None:
        from ui.pages.dialogs import ConfirmDialog, InfoDialog, cache_clear_message
        from connection import CACHE_DIR
        message, has_files = cache_clear_message()
        if not has_files:
            InfoDialog("Stored cache", message, parent=self).exec()
            return
        dlg = ConfirmDialog("Clear stored cache?", message, parent=self,
                            yes_label="Yes", no_label="No")
        if not dlg.exec():
            return
        try:
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            os.makedirs(CACHE_DIR, exist_ok=True)
        except OSError:
            pass
        self._on_opened_file(self._current_file)

    # ── Wiring ───────────────────────────────────────────────────────────────

    def wire(self, conn) -> None:
        self._conn = conn
        conn.opened_file_changed.connect(self._on_opened_file)
        conn.download_progress.connect(self._on_download_progress)
        conn.download_done.connect(self._on_download_done)
        conn.job_progress_changed.connect(self._on_job_progress)
        conn.machine_status_changed.connect(self._on_machine_status)
        conn.machine_flags_changed.connect(lambda *_: self._recompute_busy())
        conn.file_hash_received.connect(self._on_file_hash)
        # Offline (dev) mode is entered long after wiring, and it never reports
        # an opened file — so refresh the empty state when the link flips, or
        # the cache picker would never appear.
        conn.ws_connected_changed.connect(self._on_link_changed)
        # Pick up a file that's already open + cached at wire time.
        self._on_opened_file(conn.opened_file_name())
        self._update_height_map_gate()

    def _on_link_changed(self, *_) -> None:
        if self._loaded_file is None:
            self._on_opened_file(self._conn.opened_file_name()
                                 if self._conn else None)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._reposition_overlays()
        self._update_height_map_gate()
        # Re-sync to the machine's opened file each time the page is shown, so an
        # already-open, already-cached file previews automatically even when it
        # was opened/cached while another page was visible. Positive-only: only
        # auto-loads a cached file not shown yet — it never tears down an
        # in-progress download or empty state (those are driven by the signals).
        if self._conn is None:
            return
        name = self._conn.opened_file_name()
        if name and self._loaded_file != name and self._conn.cached_file_path(name):
            self._on_opened_file(name)
        elif self._offline() and self._loaded_file is None:
            # Offline: re-read the cache each time the page comes up, so a file
            # downloaded on an earlier run (or cleared since) is reflected.
            self._on_opened_file(None)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        # Abandon a cache-freshness check cleanly when the page goes away —
        # the showEvent re-sync starts a fresh one if it is still needed.
        if self._hash_name is not None:
            self._cancel_hash_check()
            self._empty_lbl.hide()
            self._fetch_btn.show()
            self._update_fetch_gate()
