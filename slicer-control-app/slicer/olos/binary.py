"""
slicer/olos/binary.py
Finding the PrusaSlicer CLI, which the engine shells out to.

The engine does its own work -- masks to scan paths to G-code -- but the slicing
itself is PrusaSlicer's: it is invoked with ``--export-sla --sla-archive-format
SL1`` and hands back a zip of per-layer PNG masks. Upstream OLOS hardcoded the
path to a copy of ``prusa-slicer-console.exe`` bundled in the repository, which
is Windows-only; there is no macOS or Linux build under ``slicers/`` to fall
back to. On this machine that path cannot resolve at all, so it is replaced by a
search.

The bundled Windows build is deliberately **not** vendored here. It is ~90 MB of
.exe/.dll plus a full ``resources/`` profile tree, it cannot run on the host this
app is developed on, and PrusaSlicer is packaged for every platform already.

Search order, first hit wins:

1. ``$OISK_PRUSA_SLICER`` -- an explicit override, which is also the hook the
   app's settings use once a user points at a non-standard install.
2. The standard install location for the platform.
3. ``prusa-slicer`` on ``PATH``, covering Linux distributions, Homebrew and an
   AppImage the user has symlinked.

The console variant is preferred on Windows: ``prusa-slicer.exe`` detaches from
the console and ``subprocess.run`` returns before slicing has finished, which
would have the engine open an SL1 archive that is not written yet.
"""

import os
import shutil
import sys


class PrusaSlicerNotFound(RuntimeError):
    """Raised with install guidance rather than a bare FileNotFoundError."""


#: Per-platform install locations, checked in order.
_CANDIDATES = {
    "darwin": [
        "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
        "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
        os.path.expanduser("~/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"),
    ],
    "win32": [
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
        r"C:\Program Files (x86)\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
    ],
    "linux": [
        "/usr/bin/prusa-slicer",
        "/usr/local/bin/prusa-slicer",
        "/opt/PrusaSlicer/prusa-slicer",
    ],
}

_INSTALL_HINT = {
    "darwin": "Download PrusaSlicer for macOS from https://www.prusa3d.com/prusaslicer/ "
              "and drag it to /Applications, or `brew install --cask prusaslicer`.",
    "win32":  "Install PrusaSlicer from https://www.prusa3d.com/prusaslicer/ .",
    "linux":  "Install PrusaSlicer from your package manager, or download the "
              "AppImage from https://www.prusa3d.com/prusaslicer/ and symlink it "
              "as `prusa-slicer` on PATH.",
}


def _platform_key():
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def resolve_prusa_slicer():
    """Return an absolute path to a runnable PrusaSlicer CLI.

    Raises PrusaSlicerNotFound, with a platform-specific install hint, rather
    than returning a path that will fail later inside ``subprocess.run`` where
    the cause is much harder to read.
    """
    override = os.environ.get("OISK_PRUSA_SLICER", "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return os.path.abspath(override)
        raise PrusaSlicerNotFound(
            f"OISK_PRUSA_SLICER points at {override!r}, which is not an "
            f"executable file. Correct it or unset it to search the usual "
            f"install locations.")

    key = _platform_key()
    for candidate in _CANDIDATES.get(key, []):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    found = shutil.which("prusa-slicer") or shutil.which("prusa-slicer-console")
    if found:
        return os.path.abspath(found)

    raise PrusaSlicerNotFound(
        "PrusaSlicer was not found. The slicer shells out to its CLI to turn a "
        "model into SL1 layer masks.\n"
        + _INSTALL_HINT.get(key, "Install PrusaSlicer and put it on PATH.")
        + "\nOr set OISK_PRUSA_SLICER to the binary."
    )


def prusa_slicer_available():
    """True if the CLI can be located, for greying out the Slice button."""
    try:
        resolve_prusa_slicer()
        return True
    except PrusaSlicerNotFound:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Platform flags
# ──────────────────────────────────────────────────────────────────────────────
#
# Upstream OLOS passes two flags that are wrong outside Windows, and both were
# measured against PrusaSlicer 2.9.7 on macOS rather than reasoned about:
#
# ``--sw-renderer`` does not exist. PrusaSlicer answers "Unknown option
# --sw-renderer", prints its usage text and exits 1 without writing an archive.
# The engine runs with ``check=True``, so this surfaces as a CalledProcessError
# -- loud, but with the real message buried in a suppressed stderr. It is a
# Linux-only flag for forcing software GL, and the CLI slicing path needs no GL
# at all.
#
# ``--single-instance`` is worse, because it can fail quietly. Its own help text
# says the arguments "are sent to an existing instance of GUI PrusaSlicer, or an
# existing PrusaSlicer window is activated" -- so with the app already open, a
# slice request is handed to that window, the process returns 0, and ``--output``
# is never written. The engine would then open an SL1 archive that does not
# exist, or worse, a stale one from a previous run. Upstream used
# ``prusa-slicer-console.exe``, where the flag means something different; here it
# has no benefit and one silent-failure mode, so it is dropped everywhere.
#
# What remains is deliberately empty on every platform. The hook is kept because
# the version-specific flag set is exactly the thing that will need adjusting
# again, and a named function is a better place to notice that than a list
# literal inside a 6000-line vendored file.

_PLATFORM_FLAGS = {
    "darwin": [],
    "win32": [],
    "linux": [],
}


def cli_platform_flags():
    """Extra PrusaSlicer CLI flags for this platform. See the note above."""
    return list(_PLATFORM_FLAGS.get(_platform_key(), []))
