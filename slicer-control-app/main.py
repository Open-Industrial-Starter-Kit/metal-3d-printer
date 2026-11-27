#!/usr/bin/env python3
"""
maieeeeen.py — application entry point.
All UI code lives in ui/ and main_window.py.
"""

import multiprocessing
import os
import sys

# Must precede the QApplication construction below: importing QtWebEngineCore is
# what sets Qt::AA_ShareOpenGLContexts, and Qt refuses to set it once a
# QCoreApplication exists. The CAM page (ui/pages/cam.py) creates its webviews
# lazily, long after startup, so without this import here the first visit to the
# CAM tab would fail. Importing the module does NOT start a render process —
# that only happens when a QWebEngineView is constructed.
# Chromium reads its command-line flags once, when QtWebEngine initialises, so
# compatibility graphics (gpu_mode.py) is set before anything Qt is imported.
import gpu_mode
gpu_mode.apply()

# The OpenGL driver probe (gl_guard.py): a launch with this flag only tries to
# create an OpenGL context and exits, so a crashing driver takes down this
# throwaway process instead of the app. Before the crash log, so a probe that
# dies does not write a crash report.
import gl_guard
if gl_guard.PROBE_ARG in sys.argv:
    sys.exit(gl_guard.probe())


def _enable_crash_log() -> None:
    """Record hard crashes and uncaught exceptions in crash.log.

    The frozen build is a windowed exe with no stderr, so a native crash (a
    GPU driver taking the process down) or an exception in a Qt slot leaves no
    trace at all. faulthandler writes the Python stack of every thread on a
    fatal signal. Appended rather than overwritten, so relaunching after a
    crash does not wipe the evidence; trimmed when it grows large.
    """
    import faulthandler
    import traceback
    from datetime import datetime
    try:
        path = os.path.join(gpu_mode._state_dir(), "crash.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "w" if (os.path.exists(path)
                       and os.path.getsize(path) > 1_000_000) else "a"
        f = open(path, mode, encoding="utf-8", errors="replace", buffering=1)
        f.write(f"\n=== start {datetime.now().isoformat(timespec='seconds')} "
                f"safe-graphics={gpu_mode.safe_graphics()} "
                f"argv={sys.argv[1:]} ===\n")
        faulthandler.enable(f, all_threads=True)
    except Exception:
        return

    prev = sys.excepthook

    def hook(et, ev, tb):
        try:
            f.write("".join(traceback.format_exception(et, ev, tb)))
        except Exception:
            pass
        prev(et, ev, tb)

    sys.excepthook = hook
    _enable_crash_log.file = f    # keep the handle alive for faulthandler


_enable_crash_log()

# A frozen build's reconstruction helper (recon/isolate.py, spawn context) is
# this same binary re-executed, and freeze_support() is what turns it into the
# helper. It has to run BEFORE the Qt imports below, not just inside main():
# a helper that has imported PySide6 cannot exit() -- Shiboken's teardown
# needs the GIL that the Poisson call is holding -- so a fatal Poisson run
# hangs it instead of ending it. See _clean_service in ui/pages/recon_panel.py.
# A no-op everywhere except in that child.
multiprocessing.freeze_support()

from PySide6 import QtWebEngineCore  # noqa: F401  (imported for its side effect)

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QLockFile, QDir, Qt
from PySide6.QtGui import QColor, QIcon

from ui.theme import BG
from ui.scale import mgr, compute_scale
from main_window import MainWindow
import desktop_entry

# App / window icon: the InMachines brand mark (no wordmark), matching the
# mobile app's launcher icon (images/app_icon.png).
_APP_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "images", "app_icon.png")

#: Windows taskbar identity. Any unique string works; the convention is
#: Company.Product.Sub.Version, and it must stay stable or a pinned taskbar
#: entry stops matching the running app.
_APP_USER_MODEL_ID = "InMachines.MiniCNC2.Desktop.1"


def main():
    # freeze_support() for the reconstruction helper is called at import time,
    # above the Qt imports -- see the note there.

    # Fractional HiDPI: honour 125%/150% display scaling instead of rounding it
    # to whole multiples, so the responsive layout stays crisp on any monitor.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    # Ask the OpenGL driver in the background (gl_guard.py), so the answer is in
    # by the time Live Preview first draws.
    gl_guard.start()
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(_APP_ICON))
    # On macOS the taskbar/Dock icon is not taken from setWindowIcon when the app
    # runs unbundled (it follows the launching process), so set it directly via
    # AppKit. Guarded so it's a harmless no-op on other platforms / if unavailable.
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication, NSImage
            img = NSImage.alloc().initByReferencingFile_(_APP_ICON)
            if img is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
        except Exception:
            pass
    # Windows has the same problem for a different reason: the taskbar groups
    # windows by Application User Model ID, and a process that does not set one
    # inherits the host's — python.exe when run from source — so the taskbar
    # button shows the Python icon however the window is set. Naming the app
    # gives it its own group, and its own icon.
    elif sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                _APP_USER_MODEL_ID)
        except Exception:
            pass
    # Linux docks match a window to its launcher by the .desktop file name
    # (the Wayland app_id / X11 WM_CLASS). Without it the running app shows a
    # generic icon next to the installed "Piccola CNC" launcher instead of
    # grouping under it. Must match packaging/linux/piccola-cnc.desktop.
    # An AppImage or a run from source installs no .desktop file, so write a
    # per-user one (desktop_entry.py); a no-op when the .deb provided it.
    else:
        app.setDesktopFileName("piccola-cnc")
        desktop_entry.ensure()


    # Seed the responsive scale from the screen the app opens on, before any UI
    # is built, so the first paint is already sized for this display. The window
    # then refines it live from its own size on every resize.
    screen = app.primaryScreen()
    if screen is not None:
        avail = screen.availableSize()
        mgr.set_scale(compute_scale(avail.width(), avail.height()))

    # Single-instance guard: a second launch would fight over the serial port
    # and the WebSocket server port, so refuse to start one.
    lock = QLockFile(QDir.tempPath() + "/piccola-cnc-desktop.lock")
    lock.setStaleLockTime(0)          # never treat a live lock as stale
    if not lock.tryLock(100):
        QMessageBox.warning(
            None, "OISK Metal 3D Printer",
            "OISK Metal 3D Printer is already running.\n"
            "Only one instance of the app can be open at a time.")
        sys.exit(1)

    # Force a neutral palette so Fusion doesn't clash with custom stylesheets
    pal = app.palette()
    pal.setColor(pal.ColorRole.Window, QColor(BG))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    rc = app.exec()
    lock.unlock()
    gpu_mode.restart_if_requested()
    sys.exit(rc)


if __name__ == "__main__":
    main()
