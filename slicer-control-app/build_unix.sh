#!/usr/bin/env bash
# =============================================================================
#  Build Piccola CNC on macOS or Linux.   ./build_unix.sh
#
#  The counterpart to build_windows.bat: same steps, same order, same output
#  shape. Both end by calling tools/build_app.py, which is where the build and
#  the packaging actually live — so the three platforms cannot drift apart in
#  anything but the environment setup below.
#
#  On Linux this gives a plain .tar.gz. For a .deb and an AppImage that run on
#  Ubuntu 22.04 and later, use ./build_linux_container.sh instead.
#
#  Requires Python 3.10+. Node.js + git are needed only to (re)fetch the CAM
#  bundles; without them a vendor/ that is already populated is used as is.
# =============================================================================
set -u
cd "$(dirname "$0")"

say() { printf '\n=== %s ===\n' "$1"; }

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
    echo "[ERROR] $PY not found. Install Python 3.10+ (or set PYTHON=...)."; exit 1; }

say "Piccola CNC — $(uname -s) build"

VENV="build-venv"
if [ ! -d "$VENV" ]; then
    echo "[1/4] Creating build virtual environment..."
    "$PY" -m venv "$VENV" || exit 1
else
    echo "[1/4] Reusing $VENV/"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"

echo "[2/4] Installing dependencies..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller || exit 1

# Open3D is the one dependency that can be missing while everything else
# installs: ~284 MB of compiled code whose wheels trail new Python releases.
# The app runs without it — the .ply viewer's Surface view is disabled and says
# why — but that is worth knowing here rather than on the shop floor.
python -c "import open3d" >/dev/null 2>&1 || cat <<'WARN'

[WARN] Open3D is not installed in the build environment.
       The app will build and run, but the 3-D scan viewer's Surface
       reconstruction will be unavailable. Usually this means no Open3D wheel
       exists for this Python version — try the newest Python it publishes for.

WARN

echo "[3/4] Fetching CAM vendor bundles (needs Node.js + git)..."
python tools/fetch_vendor.py all || \
    echo "[WARN] A CAM bundle could not be prepared — continuing without it."

# The icon has to exist before PyInstaller runs, or it ships its own.
if [ "$(uname -s)" = "Darwin" ]; then
    python tools/make_icns.py || exit 1
fi

echo "[4/4] Building..."
python tools/build_app.py "$@"
