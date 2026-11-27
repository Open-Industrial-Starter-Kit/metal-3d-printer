# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Piccola CNC desktop app.

Build (on the TARGET OS — there is no cross-compiling here):

    python3 tools/build_app.py           # does all of the below, on any of the three

or by hand:

    pip install -r requirements.txt pyinstaller
    pyinstaller --noconfirm piccola-cnc.spec

Output, one-FOLDER in every case (see below):

    macOS     dist/Piccola CNC.app          arm64 or x86_64, whichever Python is
    Windows   dist/PiccolaCNC/PiccolaCNC.exe
    Linux     dist/PiccolaCNC/PiccolaCNC

Everything platform-specific is decided in one place, `_PLATFORM` below, so a
change made for one OS cannot quietly alter another's build. The three differ
in exactly four things: the app name, the icon format, whether there is a macOS
.app wrapper around the folder, and the Info.plist that wrapper needs.

The icons/ and images/ folders are bundled as data; the app loads them with a
path relative to the ui/ package ("../icons", "../images"), which resolves to
the PyInstaller bundle root at runtime, so no code changes are required.

────────────────────────────────────────────────────────────────────────────────
ONE-FOLDER, NOT ONE-FILE — this changed when the CAM page landed
────────────────────────────────────────────────────────────────────────────────
This spec used to produce a single windowed PiccolaCNC.exe and explicitly EXCLUDED
QtWebEngine to keep it small. It is still listed below, because the 3-D views
pull Qt's WebEngine in; the CAM page itself is now an empty placeholder.

QtWebEngine is not one-file friendly. It is not a single library but a helper
*executable* (QtWebEngineProcess) plus ICU data, locale packs and v8 snapshots
that Chromium locates by path relative to that executable. In a one-file build
everything is unpacked to a fresh temp dir on every launch, which means:

  * ~150-300 MB extracted per start — many seconds of delay each time,
  * QtWebEngineProcess frequently fails to resolve its resources and the render
    process dies with a blank view instead of a clear error.

One-folder is the configuration Qt and PyInstaller actually support for
WebEngine, so that is what this spec builds. Ship the dist/PiccolaCNC/ folder
(zip it) rather than a lone .exe.

If you must have a single file, set PICCOLA_ONEFILE=1 before running PyInstaller
— it is wired up below, but treat any CAM breakage in that mode as expected.
"""

import io
import os
import re
import sys

block_cipher = None

# ── The only platform-dependent decisions in this file ──────────────────────
#
# name    what the user sees and launches. macOS gets the product name with its
#         space, which is normal there and is what the .app is called. Windows
#         and Linux use PiccolaCNC (no space: it is a binary name, and on Linux
#         build_linux.sh wraps it in a .deb whose launcher shows "Piccola CNC").
#         Saved preferences are unaffected — QSettings still keys on
#         "MiniCNC2", deliberately, so renaming the binary loses nothing.
# icon    .icns on macOS, .ico on Windows, none on Linux (the desktop file
#         carries the icon there). Handed the wrong format PyInstaller warns
#         and silently ships its own, which is how an app ends up looking like
#         a generic terminal window.
# bundle  macOS only: COLLECT produces a plain folder, and BUNDLE wraps it in
#         the .app that Finder and the Dock understand.
_PLATFORM = {
    "darwin": dict(name="Piccola CNC", icon="images/app_icon.icns", bundle=True),
    "win32":  dict(name="PiccolaCNC",  icon="images/app_icon.ico",  bundle=False),
}.get(sys.platform, dict(name="PiccolaCNC", icon=None, bundle=False))

APP_NAME = _PLATFORM["name"]


def _app_version(default="0.0.0"):
    """The version the app shows in its menu footer.

    Read out of ui/app_menu.py rather than duplicated here: that is where it is
    maintained, and a second copy would drift the moment one was bumped. Read
    as text, not imported — importing it would pull in PySide6 during the spec
    evaluation, which is both slow and a good way to confuse the analysis.
    """
    try:
        src = io.open(os.path.join(SPECPATH, "ui", "app_menu.py"),
                      encoding="utf-8").read()
    except OSError:
        return default
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', src, re.M)
    return m.group(1) if m else default

APP_VERSION = _app_version()

ONEFILE = os.environ.get("PICCOLA_ONEFILE") == "1"

datas = [
    ("icons", "icons"),
    ("images", "images"),
]

# Optional backends imported lazily / conditionally — list them so the frozen
# build keeps working even though the import-graph scanner may miss them.
hiddenimports = [
    "serial",
    "serial.tools.list_ports",
    # Imported for its side effect in main.py (sets AA_ShareOpenGLContexts) and
    # lazily inside ui/pages/cam.py, so the scanner can under-detect them.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",   # QtWebEngineWidgets links against it
    # The Live Preview draws the toolpath on the GPU. These are imported at
    # module scope in ui/pages/live_preview.py, but the scanner has missed
    # PySide6 sub-modules before and a miss here costs the whole preview page,
    # so they are pinned: without them the app fails to start, not just the tab.
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    # Surface reconstruction (recon/). Everything under recon/ is imported
    # lazily — the .ply viewer builds it only when Surface is switched on — so
    # the scanner sees no import at all from the entry point.
    "recon",
    "recon.workers",
    "recon.autoconfig",
    "recon.isolate",
    "recon.utils",
    "scipy.spatial",
    # multiprocessing spawns the reconstruction helper by re-executing this
    # binary; main() calls freeze_support() so the child runs the helper rather
    # than a second copy of the app.
    "multiprocessing",
]

# Open3D ships compiled extensions and a data tree that PyInstaller's scanner
# does not follow on its own. Collected explicitly, and softly: a build machine
# without Open3D still produces a working app, it just has no Surface view.
try:
    from PyInstaller.utils.hooks import (collect_data_files,
                                         collect_dynamic_libs,
                                         collect_submodules)
    datas += collect_data_files("open3d")
    o3d_binaries = collect_dynamic_libs("open3d")
    # Open3D reaches its own submodules through attribute access on the
    # compiled extension, so the scanner sees none of them. NumPy comes with
    # it, and is therefore NOT excluded below when Open3D is in the build.
    hiddenimports += ["open3d", "numpy"] + collect_submodules("open3d")
    import importlib.util
    HAVE_O3D = importlib.util.find_spec("open3d") is not None
except Exception as exc:                                   # pragma: no cover
    print(f"[spec] Open3D not collected ({exc}); "
          f"the Surface view will be unavailable in this build")
    o3d_binaries = []
    HAVE_O3D = False

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=o3d_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim heavy Qt modules the app never uses → smaller, faster exe.
    # NOTE: QtWebEngineCore/Widgets used to be excluded here. They must NOT be —
    # the CAM page depends on them. QtQuick/QtQml are likewise required, because
    # QtWebEngine is built on them internally even for the Widgets API.
    excludes=[
        "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "tkinter", "matplotlib", "PIL",
    # Keyed on Open3D being installed, NOT on o3d_binaries: on Linux
    # collect_dynamic_libs("open3d") returns [] (libOpen3D is reached through
    # the extension's own linkage instead), and keying on it dropped numpy
    # there — a Surface view that could never run.
    ] + ([] if HAVE_O3D else ["numpy"]),
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Qt's GTK3 platform theme plugin drags the build machine's whole GTK3 stack
# into the bundle. That GTK then reads the TARGET desktop's GSettings schemas,
# and on newer GNOME (Ubuntu 26.04) the xsettings 'antialiasing' key it asks
# for is gone: GLib aborts the process at startup. Without the plugin Qt falls
# back to the xdg-desktop-portal theme, so nothing the app uses is lost.
if sys.platform.startswith("linux"):
    _GTK = ("libqgtk3.so", "libgtk-3.so", "libgdk-3.so")
    a.binaries = [b for b in a.binaries
                  if not os.path.basename(b[0]).startswith(_GTK)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_exe_common = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # windowed app, no console window
    disable_windowed_traceback=False,
    # None = the architecture of the Python running this build. On an Apple
    # Silicon Mac that is arm64; there is no universal2 build to be had, since
    # PySide6 does not ship universal wheels, so an Intel Mac needs its own
    # build from an Intel (or Rosetta) Python.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # A real multi-resolution .ico, committed. PyInstaller can only convert a
    # PNG with Pillow, which this project does not depend on and the excludes
    # above drop anyway — so the PNG that used to be here was quietly ignored
    # and the exe shipped PyInstaller's default icon. The old source was also
    # the 432x96 wordmark; Windows icons are square, and the taskbar draws them
    # at 16 px. tools/make_icon.py rebuilds it when the art changes, and
    # tools/make_icns.py builds the macOS .icns beside it.
    icon=_PLATFORM["icon"],
)

if ONEFILE:
    # Unsupported with QtWebEngine — see the module docstring.
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        runtime_tmpdir=None,
        **_exe_common,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        **_exe_common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )

    if _PLATFORM["bundle"]:
        app = BUNDLE(
            coll,
            name="%s.app" % APP_NAME,
            icon=_PLATFORM["icon"],
            bundle_identifier="com.inmachines.piccolacnc",
            version=APP_VERSION,
            info_plist={
                "CFBundleName": APP_NAME,
                "CFBundleDisplayName": APP_NAME,
                "CFBundleShortVersionString": APP_VERSION,
                "CFBundleVersion": APP_VERSION,
                "LSMinimumSystemVersion": "11.0",
                # Without this the whole UI is drawn at 1x and upscaled — a
                # blurry app on every Mac made in the last decade.
                "NSHighResolutionCapable": True,
                # The app has no document types and is not a background agent.
                "LSBackgroundOnly": False,
            },
        )
