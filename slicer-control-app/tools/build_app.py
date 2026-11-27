#!/usr/bin/env python3
"""One build command for macOS, Windows and Linux.

    python3 tools/build_app.py              # build, then package for handing over
    python3 tools/build_app.py --no-zip     # leave dist/ as it is
    python3 tools/build_app.py --clean      # throw away build/ and dist/ first

Each platform is built ON that platform — there is no cross-compiling with
PyInstaller — so this is meant to be run unchanged on all three machines and
produce the same shape of result:

    macOS     dist/Piccola CNC.app          → PiccolaCNC-<ver>-macos-arm64.zip
    Windows   dist/PiccolaCNC/PiccolaCNC.exe → PiccolaCNC-<ver>-windows-x86_64.zip
    Linux     dist/PiccolaCNC/PiccolaCNC     → PiccolaCNC-<ver>-linux-x86_64.tar.gz
              (build_linux.sh then makes the .deb and AppImage from it)

What it does, in order
----------------------
1. Checks the things that silently produce a broken app rather than a failed
   build: a missing PyInstaller, and on macOS a missing .icns (PyInstaller warns
   and ships its own icon).
2. Removes the previous build/ and dist/ for this platform. PyInstaller is
   happy to reuse them, and a stale dist/ is how a "rebuilt" app ends up
   shipping yesterday's code.
3. Runs PyInstaller against piccola-cnc.spec, which holds every platform
   difference in one place (see its `_PLATFORM`).
4. Packages the result into a single file to send. On macOS that is `ditto`,
   not `zip`: a .app is a directory whose symlinks and resource forks a plain
   zip flattens, and the difference shows up as an app that will not launch on
   the other machine.

Why the builds cannot break each other
--------------------------------------
Nothing here is shared between platforms except the spec, and inside the spec
the only branching is a dict of three keys. There are no per-OS spec files to
drift apart, no build script that knows about one platform and guesses at the
others, and no generated artifacts committed. Each machine has its own build/
and dist/, which are never copied between them.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Mirrors piccola-cnc.spec's `_PLATFORM`. Kept here as a name only — the spec is
#: what decides; this is for finding the output and naming the archive.
APP_NAME = "Piccola CNC" if sys.platform == "darwin" else "PiccolaCNC"

#: Bundles the CAM tab serves. Their absence is not a build error — the tab
#: reports it at runtime — which is exactly why it is worth saying up front.


def say(msg: str) -> None:
    print("[build] %s" % msg, flush=True)


def app_version() -> str:
    import re
    try:
        src = open(os.path.join(ROOT, "ui", "app_menu.py"), encoding="utf-8").read()
    except OSError:
        return "0.0.0"
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', src, re.M)
    return m.group(1) if m else "0.0.0"


def platform_tag() -> str:
    """os-arch, as it goes in the archive name."""
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    arch = {"AMD64": "x86_64", "x86_64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine(),
                                                      platform.machine())
    return "%s-%s" % (os_name, arch)


def preflight() -> list[str]:
    """Everything that would otherwise produce a quietly broken app."""
    problems = []
    try:
        import PyInstaller                                  # noqa: F401
    except ImportError:
        problems.append("PyInstaller is not installed (pip install pyinstaller)")

    if sys.platform == "darwin" and not os.path.isfile(
            os.path.join(ROOT, "images", "app_icon.icns")):
        problems.append("images/app_icon.icns missing — run: "
                        "python3 tools/make_icns.py")
    if sys.platform == "win32" and not os.path.isfile(
            os.path.join(ROOT, "images", "app_icon.ico")):
        problems.append("images/app_icon.ico missing — run: "
                        "python3 tools/make_icon.py")
    return problems


def built_path() -> str:
    """Where PyInstaller will have put the result."""
    if sys.platform == "darwin":
        return os.path.join(ROOT, "dist", "%s.app" % APP_NAME)
    return os.path.join(ROOT, "dist", APP_NAME)


def tree_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(base, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit != "GB" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def package(src: str, version: str) -> str | None:
    """One file to hand over. Returns its path."""
    stem = "PiccolaCNC-%s-%s" % (version, platform_tag())
    out_dir = os.path.join(ROOT, "dist")

    if sys.platform == "darwin":
        out = os.path.join(out_dir, stem + ".zip")
        if os.path.exists(out):
            os.remove(out)
        # ditto, not zip: a .app is a directory of symlinks (every Qt framework
        # is one) and a plain zip can flatten them into copies, or lose the
        # executable bit. `ditto -c -k --sequesterRsrc --keepParent` is what
        # Apple's own notarisation instructions use.
        r = subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc",
                            "--keepParent", src, out])
        return out if r.returncode == 0 else None

    if sys.platform == "win32":
        out = os.path.join(out_dir, stem)
        return shutil.make_archive(out, "zip", root_dir=os.path.dirname(src),
                                   base_dir=os.path.basename(src))

    out = os.path.join(out_dir, stem)
    return shutil.make_archive(out, "gztar", root_dir=os.path.dirname(src),
                               base_dir=os.path.basename(src))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-zip", action="store_true",
                    help="stop after the build, do not package it")
    ap.add_argument("--clean", action="store_true",
                    help="delete build/ and dist/ before building")
    args = ap.parse_args()

    os.chdir(ROOT)
    version = app_version()
    say("Piccola CNC %s for %s (python %s)"
        % (version, platform_tag(), platform.python_version()))

    problems = preflight()
    if problems:
        for p in problems:
            print("  ✗ %s" % p)
        return 1

    for d in ("build", "dist"):
        path = os.path.join(ROOT, d)
        if args.clean or d == "dist":
            # dist always: PyInstaller merges into an existing one, so a file
            # dropped from the build would stay in the shipped folder forever.
            if os.path.isdir(path):
                say("removing %s/" % d)
                shutil.rmtree(path, ignore_errors=True)

    started = time.time()
    say("running PyInstaller (several minutes; QtWebEngine is large)")
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "piccola-cnc.spec"])
    if r.returncode:
        say("PyInstaller failed")
        return r.returncode

    out = built_path()
    if not os.path.exists(out):
        say("PyInstaller reported success but %s is not there" % out)
        return 1
    say("built %s (%s) in %d s"
        % (os.path.relpath(out, ROOT), human(tree_size(out)), time.time() - started))

    if args.no_zip:
        return 0

    archive = package(out, version)
    if archive is None:
        say("packaging failed")
        return 1
    say("packaged %s (%s)"
        % (os.path.relpath(archive, ROOT), human(os.path.getsize(archive))))

    if sys.platform == "darwin":
        print("""
    This build is NOT signed or notarised. On the receiving Mac, macOS will
    refuse it the first time. Either:

        xattr -dr com.apple.quarantine "/Applications/%s.app"

    or open it once, let macOS refuse, then System Settings → Privacy &
    Security → "Open Anyway". After that it launches normally.

    It is built for %s only.""" % (APP_NAME, platform_tag().split("-")[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
