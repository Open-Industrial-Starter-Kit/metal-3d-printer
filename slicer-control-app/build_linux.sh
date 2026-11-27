#!/usr/bin/env bash
# ============================================================================
#  Build Piccola CNC for Linux: a .deb package and a single-file AppImage.
#
#      ./build_linux.sh                 # version from ui/app_menu.py
#      VERSION=1.2.0 ./build_linux.sh
#
#  Output (in dist/):
#      piccola-cnc_<version>_amd64.deb       sudo apt install ./piccola-cnc_*.deb
#      PiccolaCNC-<version>-x86_64.AppImage  chmod +x, then run it
#      PiccolaCNC/                           the raw PyInstaller one-folder build
#
#  Both are self-contained: Python, Qt/QtWebEngine, Open3D and the CAM bundles
#  are all inside. What is NOT bundled is the base system every desktop already
#  has (glibc, the GL/EGL driver stack, Wayland/X11 client libraries) — those
#  must come from the target OS, and the .deb declares them so apt installs any
#  that are missing.
#
#  BUILD ON THE OLDEST UBUNTU YOU WANT TO SUPPORT. The binaries link against
#  the build machine's glibc and will refuse to start on anything older (the
#  .deb states the minimum libc6 it needs, so apt rejects it cleanly instead).
#
#  Needs: python3 (+venv), git, dpkg-deb, curl. For the Kiri:Moto CAM bundle
#  also node + npm (skipped with a warning if missing, like build_windows.bat).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Default: the version the app itself shows (APP_VERSION in ui/app_menu.py),
# the same source tools/build_app.py names the macOS/Windows archives from.
VERSION="${VERSION:-$(sed -n 's/^APP_VERSION *= *"\([^"]*\)".*/\1/p' ui/app_menu.py)}"
VERSION="${VERSION:-0.0.0}"
ARCH=amd64
PKG=piccola-cnc
APP=PiccolaCNC                       # must match name= in piccola-cnc.spec
OUT=dist
STAGE=build/linux-pkg

echo
echo "=== Piccola CNC - Linux build ${VERSION} ==="
echo

# ── 1. Python environment ────────────────────────────────────────────────────
# Reuse the dev venv when there is one (README setup); otherwise make a
# dedicated build venv, as build_windows.bat does. BUILD_VENV names a venv to
# use (created if missing) — build_linux_container.sh sets it, because the
# host's venv/ points at the host's Python and is useless inside the container.
if [[ -n "${BUILD_VENV:-}" ]]; then
    [[ -x "$BUILD_VENV/bin/python" ]] || python3 -m venv "$BUILD_VENV"
    PY="$BUILD_VENV/bin/python"
elif [[ -x venv/bin/python ]]; then
    PY=venv/bin/python
else
    echo "[1/6] Creating build virtual environment..."
    python3 -m venv build-venv
    PY=build-venv/bin/python
fi
echo "[1/6] Installing dependencies..."
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r requirements.txt pyinstaller

if ! "$PY" -c "import open3d" 2>/dev/null; then
    echo "[WARN] Open3D is not installed - the 3-D scan Surface view will be"
    echo "       unavailable in this build (no wheel for this Python?)."
fi

# ── 2. CAM vendor bundles ────────────────────────────────────────────────────
# Kiri:Moto is a local npm build that takes minutes; reuse an existing one
# unless asked. REFETCH_VENDOR=1 forces a rebuild (e.g. after a patch change).
echo "[2/6] CAM vendor bundles..."
if [[ -f vendor/kirimoto/VERSION && "${REFETCH_VENDOR:-0}" != 1 ]]; then
    echo "      vendor/kirimoto present - reusing (REFETCH_VENDOR=1 to rebuild)"
elif ! "$PY" tools/fetch_vendor.py all; then
    echo "[WARN] A CAM bundle could not be prepared - continuing without it."
    echo "       That tab will say so at runtime. See docs/cam-integration.md."
fi

# ── 3. PyInstaller one-folder build ──────────────────────────────────────────
# Through tools/build_app.py, like build_windows.bat and build_unix.sh, so the
# Linux packages are made from exactly the build every platform gets. It clears
# dist/ first; --no-zip skips its .tar.gz, since the .deb/AppImage replace it.
echo "[3/6] Building the application (this can take several minutes)..."
"$PY" tools/build_app.py --no-zip

# ── 4. Stage the install tree ────────────────────────────────────────────────
echo "[4/6] Staging package contents..."
rm -rf "$STAGE"
ROOT="$STAGE/deb"
mkdir -p "$ROOT/opt/$PKG" "$ROOT/usr/bin" "$ROOT/DEBIAN" \
         "$ROOT/usr/share/applications" "$ROOT/usr/share/metainfo" \
         "$ROOT/usr/share/icons/hicolor/192x192/apps" \
         "$ROOT/usr/lib/udev/rules.d"
cp -a "$OUT/$APP/." "$ROOT/opt/$PKG/"
ln -s "/opt/$PKG/$APP" "$ROOT/usr/bin/$PKG"
install -m644 packaging/linux/$PKG.desktop "$ROOT/usr/share/applications/"
install -m644 images/app_icon.png "$ROOT/usr/share/icons/hicolor/192x192/apps/$PKG.png"
install -m644 packaging/linux/$PKG.metainfo.xml "$ROOT/usr/share/metainfo/"
install -m644 packaging/linux/60-$PKG.rules "$ROOT/usr/lib/udev/rules.d/"
# PyInstaller leaves the build user's umask on the tree (often 775/664); an
# installed package must not be group-writable.
chmod -R u=rwX,go=rX "$ROOT"

# ── 5. .deb ──────────────────────────────────────────────────────────────────
echo "[5/6] Building .deb..."
# The minimum libc6 is whatever newest GLIBC_x.y symbol version the bundle
# actually references, so apt refuses the package on an older system rather
# than it failing at launch with "version `GLIBC_2.xx' not found".
GLIBC_MIN=$(find "$ROOT/opt/$PKG" -type f \( -name '*.so*' -o -name "$APP" \) \
                -exec objdump -T {} + 2>/dev/null \
            | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' | sort -uV | tail -1)
INSTALLED_KB=$(du -sk "$ROOT" | cut -f1)

# Libraries the bundle loads from the system rather than shipping (found with
# ldd over the whole bundle). The libxcb-* utilities are what Qt's X11 platform
# plugin needs; a Wayland session never loads them, but X11 / XWayland does.
cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: $ARCH
Maintainer: InMachines Ingrassia GmbH <info@inmachines.net>
Installed-Size: $INSTALLED_KB
Section: electronics
Priority: optional
Depends: libc6 (>= $GLIBC_MIN), libgl1, libegl1, libglx0, libdrm2,
 libwayland-client0, libwayland-cursor0, libwayland-egl1, libwayland-server0,
 libxcb1, libxcb-dri3-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0,
 libxcb-keysyms1, libxcb-render-util0, fontconfig, fonts-dejavu-core
Homepage: https://piccolacnc.com
Description: Control and CAM app for the Piccola CNC milling machine
 Desktop controller for the Piccola CNC: jogging, job control, file
 management, probing and height maps over USB serial or WiFi, with
 Kiri:Moto (2D/3D) and EasyTrace5000 (PCB) CAM built in.
 .
 Self-contained: Python, Qt and all libraries are bundled under
 /opt/$PKG. Installs a udev rule so the logged-in user can open the
 machine's USB serial port without joining the dialout group.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
# Apply the serial-port rule to a machine that is already plugged in.
if [ "$1" = configure ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=tty --attr-match=idVendor=2e8a || true
fi
EOF
cat > "$ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
fi
EOF
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

DEB="$OUT/${PKG}_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$DEB"

# ── 6. AppImage ──────────────────────────────────────────────────────────────
# One executable file, no install. Unlike PyInstaller's one-file mode it does
# not unpack to a temp dir on every start — it mounts in place — so
# QtWebEngine's helper process finds its resources and the CAM tab works.
# It cannot install the udev rule: users of the AppImage need to be in the
# dialout group (sudo usermod -aG dialout $USER, then log out and in).
echo "[6/6] Building AppImage..."
TOOL=build/appimagetool-x86_64.AppImage
if [[ ! -x "$TOOL" ]]; then
    curl -fsSL --retry 3 --retry-all-errors -o "$TOOL" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
APPDIR="$STAGE/$APP.AppDir"
mkdir -p "$APPDIR"
cp -a "$OUT/$APP/." "$APPDIR/"
cp packaging/linux/$PKG.desktop "$APPDIR/"
cp images/app_icon.png "$APPDIR/$PKG.png"
cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
exec "\$(dirname "\$(readlink -f "\$0")")/$APP" "\$@"
EOF
chmod 755 "$APPDIR/AppRun"
APPIMAGE="$OUT/$APP-$VERSION-x86_64.AppImage"
# --appimage-extract-and-run: works on build machines without FUSE.
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$APPIMAGE"

echo
echo "Done."
echo "  $DEB   (needs libc6 >= $GLIBC_MIN)"
echo "  $APPIMAGE"
echo
