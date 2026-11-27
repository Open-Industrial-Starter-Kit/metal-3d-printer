"""
ui/pages/mesh_canvas.py
Shaded surface view for a reconstructed scan, sharing the point viewer's camera.

Renders a triangle mesh on the GPU but **offscreen**: the mesh lives in a vertex
buffer, each frame is one draw call into a framebuffer object, and the result is
blitted into this plain widget with QPainter.

The offscreen detour is not incidental, and the reason is the same one recorded
in ui/pages/live_preview.py: a ``QOpenGLWidget`` anywhere in the same top-level
window as a ``QWebEngineView`` leaves the web view painting black, which would
take out both CAM tabs. Rendering through our own context and FBO keeps the GPU
work and leaves the widget hierarchy free of GL widgets.

Why not reuse Gcode3DCanvas
---------------------------
That canvas draws ``GL_LINES`` with depth testing *off*, because a toolpath is
composited in file order. A surface needs the opposite — triangles, a depth
buffer, and a light — so the shaders, the FBO attachment and the draw call all
differ. What is genuinely shared is the scaffolding: context bring-up, the FBO
blit, and the orbit camera. That duplication is deliberate for now; folding both
onto a common base is a worthwhile follow-up, but not one to make while the
toolpath renderer is the thing standing between the operator and a moving
gantry.

If the context, the shaders or the FBO fail to come up — an old driver, a remote
session, a headless run — the canvas says so and draws nothing rather than
falling back to a software rasteriser. A point cloud degrades gracefully to
fewer points; a surface does not degrade to anything useful, and a wrong-looking
surface is worse than an honest message.
"""

import math

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QCursor,
    QMatrix4x4, QOffscreenSurface, QOpenGLContext, QSurfaceFormat,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram,
)
from PySide6.QtWidgets import QWidget

from ui.scale import px

_BG_C = QColor("#0B0B0B")           # matches the point view
_FOV_DEG = 50.0                     # matches the point view

#: Surface colour, lit. Kept close to the point view's gold so toggling between
#: the two reads as the same object rather than two different scans.
_SURF_RGB = (0.898, 0.757, 0.345)

# GL enum values used directly — QOpenGLFunctions exposes the calls, not the
# constants, and this is the same approach live_preview.py takes.
_GL_DEPTH_TEST = 0x0B71
_GL_TRIANGLES = 0x0004
_GL_FLOAT = 0x1406
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_BUFFER_BIT = 0x00000100
_GL_CULL_FACE = 0x0B44

_VERT_SRC = """
attribute highp vec3 aPos;
attribute highp vec3 aNormal;
uniform highp mat4 uMVP;
uniform highp mat4 uModel;
varying highp vec3 vNormal;
varying highp vec3 vPos;
void main() {
    vNormal = mat3(uModel[0].xyz, uModel[1].xyz, uModel[2].xyz) * aNormal;
    vPos = (uModel * vec4(aPos, 1.0)).xyz;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

# Two-sided Lambert plus a rim term. Two-sided matters: a reconstructed surface
# is routinely an open sheet, and its back faces are a legitimate view of the
# scan rather than something to cull into blackness.
_FRAG_SRC = """
uniform highp vec3 uColor;
uniform highp vec3 uEye;
varying highp vec3 vNormal;
varying highp vec3 vPos;
void main() {
    highp vec3 n = normalize(vNormal);
    highp vec3 v = normalize(uEye - vPos);
    if (dot(n, v) < 0.0) n = -n;            // light whichever face we can see
    highp vec3 l = normalize(uEye - vPos);  // headlight
    highp float diff = max(dot(n, l), 0.0);
    highp float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0) * 0.25;
    highp vec3 c = uColor * (0.28 + 0.72 * diff) + vec3(rim);
    gl_FragColor = vec4(c, 1.0);
}
"""


class MeshCanvas(QWidget):
    """Orbit view of a reconstructed surface (Z up), GPU rendered offscreen."""

    def __init__(self, diag: float):
        super().__init__()
        self._diag = max(diag, 1e-6)
        self.setMinimumSize(px(400), px(300))
        self.setCursor(QCursor(Qt.OpenHandCursor))
        # Don't let unhandled buttons bubble to the parent — the native
        # fallback crashes on macOS (see Gcode3DCanvas).
        self.setAttribute(Qt.WA_NoMousePropagation, True)

        self._verts = b""            # interleaved pos(3) + normal(3) floats
        self._vert_count = 0
        self._message = "No surface yet"

        # Same home view as PlyCanvas, so toggling does not move the camera.
        base = (self._diag / 2) / math.tan(math.radians(_FOV_DEG / 2)) * 1.5
        home_vec = (0.55, -0.75, 0.6)
        vlen = math.sqrt(sum(c * c for c in home_vec))
        self._home_dist = base * vlen
        self._home_yaw = math.atan2(home_vec[1], home_vec[0])
        self._home_pitch = math.asin(home_vec[2] / vlen)
        self._min_dist = max(self._diag * 0.08, 0.5)
        self._max_dist = self._diag * 8

        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = self._home_dist
        self._pan = QPointF(0, 0)
        self._last = QPointF()
        self._mode = None

        self._gl_ok = False
        self._gl_failed = False
        self._ctx = None
        self._surface = None
        self._gl = None
        self._prog = None
        self._vbo = None
        self._fbo = None
        self._fbo_size = None

    # ── data ─────────────────────────────────────────────────────────────────

    def set_mesh(self, vertices, triangles, normals) -> None:
        """Load a mesh from numpy arrays: (V,3) vertices, (T,3) indices, (V,3) normals.

        Triangles are expanded to flat vertices rather than drawn through an
        index buffer. It costs memory (a 200k-triangle mesh is about 14 MB) and
        buys a single ``glDrawArrays`` with no element buffer to manage, which
        keeps this class to one buffer and one code path.
        """
        import numpy as np

        if vertices is None or len(triangles) == 0:
            self._verts, self._vert_count = b"", 0
            self._message = "No surface yet"
            self.update()
            return

        v = np.asarray(vertices, dtype=np.float32)
        t = np.asarray(triangles, dtype=np.int64)
        if normals is None or len(normals) != len(vertices):
            n = self._face_normals(v, t)
        else:
            n = np.asarray(normals, dtype=np.float32)
            n = n[t.ravel()]
        pos = v[t.ravel()]
        inter = np.empty((len(pos), 6), dtype=np.float32)
        inter[:, 0:3] = pos
        inter[:, 3:6] = n
        self._verts = inter.tobytes()
        self._vert_count = len(pos)
        self._message = ""
        self._fbo = None            # force a re-upload path next frame
        self._uploaded = False
        self.update()

    @staticmethod
    def _face_normals(v, t):
        """Per-vertex normals from face normals, for meshes that carry none."""
        import numpy as np

        tri = v[t]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        length = np.linalg.norm(fn, axis=1, keepdims=True)
        fn = np.divide(fn, np.where(length == 0, 1.0, length))
        return np.repeat(fn, 3, axis=0).astype(np.float32)

    def has_mesh(self) -> bool:
        return self._vert_count > 0

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    # ── view control (mirrors PlyCanvas so the two feel identical) ───────────

    def reset_view(self):
        self._yaw = self._home_yaw
        self._pitch = self._home_pitch
        self._dist = self._home_dist
        self._pan = QPointF(0, 0)
        self.update()

    def view_state(self):
        return (self._yaw, self._pitch, self._dist, QPointF(self._pan))

    def set_view_state(self, state):
        self._yaw, self._pitch, self._dist, pan = state
        self._pan = QPointF(pan)
        self.update()

    def wheelEvent(self, e):
        step = 0.0015 * e.angleDelta().y()
        self._dist = max(self._min_dist, min(self._max_dist,
                                             self._dist * math.exp(-step)))
        self.update()

    def mousePressEvent(self, e):
        self._last = e.position()
        if e.button() == Qt.LeftButton:
            self._mode = "orbit"
            self.setCursor(QCursor(Qt.ClosedHandCursor))
        elif e.button() == Qt.RightButton:
            self._mode = "pan"
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def mouseMoveEvent(self, e):
        if self._mode is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        if self._mode == "orbit":
            self._yaw -= d.x() * 0.01
            self._pitch = max(-1.5, min(1.5, self._pitch + d.y() * 0.01))
        else:
            self._pan += d
        self.update()

    def mouseReleaseEvent(self, _e):
        self._mode = None
        self.setCursor(QCursor(Qt.OpenHandCursor))

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), _BG_C)
        img = self._render_offscreen() if self._vert_count else None
        if img is not None:
            p.drawImage(0, 0, img)
        else:
            p.setPen(QPen(QColor("#777")))
            p.drawText(self.rect(), Qt.AlignCenter, self._message or "No surface yet")
        p.end()

    def _eye(self):
        cy, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        return (self._dist * cp * cy, self._dist * cp * sy, self._dist * sp)

    def _mvp(self, w: float, h: float) -> QMatrix4x4:
        ex, ey, ez = self._eye()
        m = QMatrix4x4()
        m.perspective(_FOV_DEG, max(w, 1) / max(h, 1),
                      max(self._diag * 0.001, 0.01), self._diag * 20)
        # Pan is a screen-space nudge, applied after the projection so it
        # behaves the same at any zoom — the point view does the same thing by
        # offsetting its projected pixels.
        fov = math.tan(math.radians(_FOV_DEG / 2))
        m.translate(-self._pan.x() / max(h, 1) * 2 * fov * self._dist,
                    self._pan.y() / max(h, 1) * 2 * fov * self._dist, 0)
        look = QMatrix4x4()
        look.lookAt(_v3(ex, ey, ez), _v3(0, 0, 0), _v3(0, 0, 1))
        return m * look

    def _ensure_gl(self) -> bool:
        if self._gl_ok:
            return True
        if self._gl_failed:
            return False
        self._gl_failed = True          # cleared on success below
        try:
            # a broken driver crashes on context creation rather than failing;
            # gl_guard has asked a throwaway process first (see gl_guard.py)
            import gl_guard
            gl_guard.ensure()
            fmt = QSurfaceFormat.defaultFormat()
            fmt.setDepthBufferSize(24)
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
            pr = QOpenGLShaderProgram(self)
            if not pr.addShaderFromSourceCode(QOpenGLShader.Vertex, _VERT_SRC):
                raise RuntimeError(f"vertex shader: {pr.log()}")
            if not pr.addShaderFromSourceCode(QOpenGLShader.Fragment, _FRAG_SRC):
                raise RuntimeError(f"fragment shader: {pr.log()}")
            if not pr.link():
                raise RuntimeError(f"link: {pr.log()}")
            self._prog = pr
            self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            if not self._vbo.create():
                raise RuntimeError("could not create a vertex buffer")
            self._vbo.setUsagePattern(QOpenGLBuffer.StaticDraw)
            self._gl_ok = True
            self._gl_failed = False
            self._uploaded = False
            return True
        except Exception as exc:                        # pragma: no cover
            self._gl_ok = False
            self._message = f"3-D surface view unavailable ({exc})"
            print(f"[mesh-canvas] GPU renderer unavailable ({exc})")
            return False
        finally:
            if self._ctx is not None:
                self._ctx.doneCurrent()

    def _render_offscreen(self):
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
                ffmt.setSamples(4)
                # A surface needs a depth buffer; the toolpath canvas does not,
                # which is the one FBO difference between the two.
                ffmt.setAttachment(QOpenGLFramebufferObject.Depth)
                self._fbo = QOpenGLFramebufferObject(dev[0], dev[1], ffmt)
                self._fbo_size = dev
                if not self._fbo.isValid():
                    raise RuntimeError("framebuffer object is not valid")
            self._fbo.bind()
            gl = self._gl
            gl.glViewport(0, 0, dev[0], dev[1])
            gl.glClearColor(_BG_C.redF(), _BG_C.greenF(), _BG_C.blueF(), 1.0)
            gl.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
            gl.glEnable(_GL_DEPTH_TEST)
            gl.glDisable(_GL_CULL_FACE)     # open sheets are viewed from both sides

            self._vbo.bind()
            if not getattr(self, "_uploaded", False):
                self._vbo.allocate(self._verts, len(self._verts))
                self._uploaded = True

            pr = self._prog
            pr.bind()
            pr.setUniformValue(pr.uniformLocation("uMVP"), self._mvp(w, h))
            pr.setUniformValue(pr.uniformLocation("uModel"), QMatrix4x4())
            ex, ey, ez = self._eye()
            pr.setUniformValue(pr.uniformLocation("uEye"), _v3(ex, ey, ez))
            pr.setUniformValue(pr.uniformLocation("uColor"), _v3(*_SURF_RGB))
            for name, size, offset in (("aPos", 3, 0), ("aNormal", 3, 12)):
                loc = pr.attributeLocation(name)
                if loc >= 0:
                    pr.enableAttributeArray(loc)
                    pr.setAttributeBuffer(loc, _GL_FLOAT, offset, size, 24)
            gl.glDrawArrays(_GL_TRIANGLES, 0, self._vert_count)
            pr.release()
            self._vbo.release()
            self._fbo.release()
            img = self._fbo.toImage()
            img.setDevicePixelRatio(dpr)
            return img
        except Exception as exc:                        # pragma: no cover
            self._gl_ok = False
            self._gl_failed = True
            self._message = f"3-D surface view failed ({exc})"
            print(f"[mesh-canvas] GPU render failed ({exc})")
            return None
        finally:
            self._ctx.doneCurrent()


def _v3(x, y, z):
    from PySide6.QtGui import QVector3D
    return QVector3D(float(x), float(y), float(z))
