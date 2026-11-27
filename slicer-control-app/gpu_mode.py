"""
gpu_mode.py — compatibility graphics for the CAM webviews.

Kiri:Moto draws everything with WebGL. If Chromium cannot get a WebGL context,
Kiri's init throws before it hides its splash and the tab stays on
"Kiri:Moto is Loading" indefinitely. On someone else's PC that usually means
Chromium has blocklisted their GPU or driver (old Intel/AMD drivers, Remote
Desktop, VMs) or the driver is broken.

*Compatibility graphics* renders through WARP instead: the software D3D11
rasterizer ("Microsoft Basic Render Driver") built into every Windows 10/11.
Slower, but it draws with no GPU at all. Measured with QtWebEngine 6.11
(Chromium 140), because the obvious flags do not work there:

* ``--use-angle=swiftshader`` / ``--use-gl=...`` are ignored: QtWebEngine picks
  the ANGLE backend from Qt's own RHI device, so WebGL stays on the GPU.
* ``--enable-unsafe-swiftshader`` does not bring WebGL back under
  ``--disable-gpu``.
* ``QSG_RHI_PREFER_SOFTWARE_RENDERER=1`` does move Qt -- and so Chromium -- to
  WARP, but Chromium blocklists WARP for WebGL ("WebGL1 blocklisted") unless
  ``--ignore-gpu-blocklist`` is also passed. Both together give WebGL on
  "Microsoft Basic Render Driver".

Switched on by a marker file that the CAM page writes when Kiri fails to load
and the user agrees to restart, or for one run with ``--safe-graphics``. The
app menu (ui/app_menu.py) has a switch to turn it back off once the GPU works.

Must run before QtWebEngine initialises (i.e. before QApplication exists):
Chromium reads its flags, and Qt picks its RHI adapter, exactly once.
"""

import os
import sys

_SAFE_FLAGS = ["--ignore-gpu-blocklist"]


def _state_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PiccolaCNC")
    return os.path.join(os.path.expanduser("~"), ".config", "piccola-cnc")


def _marker() -> str:
    return os.path.join(_state_dir(), "safe-graphics")


_active = False


def safe_graphics() -> bool:
    """True if *this run* uses software rendering (fixed at startup)."""
    return _active


def saved_safe_graphics() -> bool:
    """True if the next launch will use software rendering."""
    return os.path.exists(_marker())


def set_safe_graphics(on: bool) -> bool:
    """Persist the choice for the next launch. False if it could not be saved."""
    try:
        if on:
            os.makedirs(_state_dir(), exist_ok=True)
            with open(_marker(), "w", encoding="utf-8") as f:
                f.write("Delete this file to use the GPU for the CAM tools again.\n")
        elif os.path.exists(_marker()):
            os.remove(_marker())
        return True
    except OSError:
        return False


def apply() -> None:
    """Set up compatibility graphics if enabled; keeps any flags set by the user."""
    global _active
    _active = "--safe-graphics" in sys.argv or saved_safe_graphics()
    if not _active:
        return
    os.environ["QSG_RHI_PREFER_SOFTWARE_RENDERER"] = "1"
    flags = _SAFE_FLAGS
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    for f in flags:
        if f not in existing:
            existing.append(f)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(existing)


_restart = False


def request_restart() -> None:
    """Ask main() to start a fresh copy once this one has fully shut down."""
    global _restart
    _restart = True


def restart_if_requested() -> None:
    """Relaunch the app. Called after the single-instance lock is released."""
    if not _restart:
        return
    import subprocess
    # Inside a Linux AppImage, sys.executable is in the image's temporary mount,
    # which the AppImage runtime tears down as soon as this process exits — a
    # copy started from there dies with it. Relaunch the AppImage file itself.
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        args = [appimage]
    else:
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(os.path.abspath(sys.argv[0]))
    args += [a for a in sys.argv[1:] if a != "--safe-graphics"]
    flags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW, not DETACHED_PROCESS: run from source, python.exe
        # (or the venv launcher's child) is a console program, and a detached
        # one opens a brand-new console window that sits behind the app. The
        # frozen build is a windowed exe and gets no console either way.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, close_fds=True, creationflags=flags)
    except OSError:
        pass
