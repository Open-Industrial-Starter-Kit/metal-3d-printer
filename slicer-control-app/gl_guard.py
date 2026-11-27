"""
gl_guard.py
Decides, out of process, whether this machine's OpenGL driver is safe to use,
and falls back to Qt's software OpenGL when it is not.

Why
---
Live Preview (ui/pages/live_preview.py) and the mesh canvas
(ui/pages/mesh_canvas.py) draw through an offscreen ``QOpenGLContext``. On a
machine whose OpenGL driver is broken, creating that context does not fail --
it CRASHES the whole process: an access violation at address 0 inside the
driver, which no Python ``try`` can catch. Seen on a laptop with an AMD Radeon
integrated GPU (2021 driver) next to a Radeon RX 6800M: the app died every time
Live Preview painted, and a bare ``QOpenGLContext.openGLModuleType()`` in a
fresh process died the same way. With ``QT_OPENGL=software`` -- Qt's bundled
Mesa llvmpipe (``opengl32sw.dll``) -- the same context came up fine.

How
---
The question is asked of a THROWAWAY process: this executable (or main.py from
source) is started with :data:`PROBE_ARG`, creates a context, reads the GL
renderer string and exits 0. If the driver crashes, only that child dies. The
app then sets ``QT_OPENGL=software`` for itself before its first OpenGL use;
the Windows platform plugin reads that variable when the first context is
created, which is lazily -- nothing touches OpenGL at startup -- so setting it
in-process still takes effect. Live Preview then draws in 3D on the CPU instead
of taking the app down.

:func:`start` launches the probe early (it costs about a second) so that by the
time anything needs OpenGL the answer is in; :func:`ensure` is called right
before the first context is created and waits for it if needed.

Windows only: elsewhere there is no ``QT_OPENGL`` switch to fall back to, and
the crash has not been seen. A ``QT_OPENGL`` the user set themselves is always
respected and nothing is probed.
"""

from __future__ import annotations

import os
import subprocess
import sys

#: Command-line flag that turns a launch of the app into the probe.
PROBE_ARG = "--gl-probe"

#: How long the probe may take before it counts as a failure.
PROBE_TIMEOUT_S = 20

_proc = None          # the running probe, if started
_decided = False      # ensure() has run
result = None         # "hardware", "software (probe failed: ...)", "skipped"


def _command() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, PROBE_ARG]
    main = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [sys.executable, main, PROBE_ARG]


def _applies() -> bool:
    return sys.platform == "win32" and not os.environ.get("QT_OPENGL")


def start() -> None:
    """Launch the probe in the background. Cheap to call; idempotent."""
    global _proc
    if _proc is not None or not _applies():
        return
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _proc = subprocess.Popen(_command(), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        _proc = None


def ensure() -> None:
    """Before the first OpenGL context: fall back to software GL if the probe
    could not create one. Waits for the probe if it is still running."""
    global _decided, result
    if _decided:
        return
    _decided = True
    if not _applies():
        result = "skipped"
        return
    start()
    if _proc is None:
        result = "skipped (probe could not start)"
        return
    try:
        rc = _proc.wait(timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _proc.kill()
        rc = "timeout"
    if rc == 0:
        result = "hardware"
        return
    os.environ["QT_OPENGL"] = "software"
    result = f"software (probe failed: {rc})"
    print(f"[gl-guard] OpenGL driver probe failed ({rc}); "
          f"using Qt's software OpenGL", flush=True)


def probe() -> int:
    """Run in the child: create a context the way the app does. Returns 0 on
    success. A broken driver crashes this process instead of returning."""
    if sys.platform == "win32":
        # An expected crash here should die quietly and quickly: no
        # "python has stopped working" box, and no Windows Error Reporting pass
        # (which held a crashing probe up for several seconds).
        try:
            import ctypes
            # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
            ctypes.windll.kernel32.SetThreadErrorMode(0x0001 | 0x0002 | 0x8000, None)
        except Exception:
            pass
    from PySide6.QtGui import (QGuiApplication, QOffscreenSurface,
                               QOpenGLContext, QSurfaceFormat)
    app = QGuiApplication(["piccola-gl-probe"])      # noqa: F841
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setSamples(4)
    surf = QOffscreenSurface()
    surf.setFormat(fmt)
    surf.create()
    if not surf.isValid():
        return 2
    ctx = QOpenGLContext()
    ctx.setFormat(fmt)
    if not ctx.create() or not ctx.makeCurrent(surf):
        return 3
    renderer = ctx.functions().glGetString(0x1F01)    # GL_RENDERER
    ctx.doneCurrent()
    return 0 if renderer else 4
