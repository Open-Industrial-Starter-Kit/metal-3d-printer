#!/usr/bin/env python3
"""
tools/fetch_vendor.py
Fetch (and where necessary build) the browser CAM bundles into vendor/.

    python3 tools/fetch_vendor.py easytrace
    python3 tools/fetch_vendor.py kirimoto
    python3 tools/fetch_vendor.py all

Both tools are vendored at a PINNED ref — they move fast, and an unnoticed
upstream change to a post-processor is a change to the G-code we hand a machine.
Bump the refs in SOURCES deliberately, re-run, and re-test a cut.

Why the two tools are fetched so differently
--------------------------------------------
EasyTrace5000 is a plain static site: its repository *is* the deployable, so
vendoring is a checkout and a copy.

Kiri:Moto is not. GridSpace publishes only Electron installers (~120-190 MB) as
release assets — there is no static web build to download — so servable assets
have to be produced locally by its own npm pipeline (esbuild + several webpack
passes). That needs Node and network access at *vendor* time. Nothing extra is
needed at app runtime: the output is static files served by cam_server.py.

Patches
-------
A bundle may carry patches in ``tools/patches/<bundle>/*.patch``, applied to the
checkout in filename order after clone and *before* the build, so the shipped
bundles are built from patched sources. Each patch file starts with a prose
header explaining why it exists and what would let it be deleted.

Patching is deliberate and rare. Everything Piccola can do from outside a tool
it does from outside (see ui/cam_*.py, which drive each tool's own API through
injected script), precisely so ``vendor/`` stays byte-identical to upstream and
an upgrade stays a folder swap. A patch is for what that cannot reach: code
that runs inside a pre-bundled web worker, where there is no seam to inject at.

The cost is real: a patch that no longer applies stops the vendor step dead,
which is the intended failure — silently building without it would ship a
bundle missing a feature the app's UI advertises. Rebase it, or delete it if
upstream has fixed the gap.

Requires: git, and for kirimoto also node + npm.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(REPO_ROOT, "vendor")
PATCHES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")

SOURCES = {
    "easytrace": {
        "name": "EasyTrace5000 (+ EasyShape5000)",
        "repo": "https://github.com/RicardoJCMarques/EasyTrace5000",
        "ref": "v1.4.2",
        "license": "AGPL-3.0",
        "build": None,          # static site — no build step
        "keep": None,           # whole tree
        "drop": [],
        "note": (
            "AGPL-3.0: vendored byte-identical to the tag so redistribution "
            "needs no extra source drop. Do not edit in place — contribute "
            "upstream instead.\n"
            '"Eltryus" is EasyTrace\'s trademark: credit it, never brand '
            "Piccola with it."
        ),
    },
    "kirimoto": {
        "name": "Kiri:Moto (grid-apps)",
        "repo": "https://github.com/GridSpace/grid-apps",
        "ref": "4.7.2",
        "license": "MIT",
        # Order matters and is easy to get wrong:
        #   webpack-ext generates src/ext/* (three.js, jszip, quickjs, planegcs).
        #   Skipping it makes pack-prod die with ~25 "Could not resolve
        #   ../ext/three.js" errors. It is part of `npm run setup`, NOT of a
        #   plain `npm install`.
        #   pack-prod then esbuilds the sources, runs the server once in dryrun
        #   mode to emit the composed bundles, and minifies.
        "build": [
            "npm install --no-audit --no-fund",
            "npm run webpack-ext",
            "npm run pack-prod",
        ],
        # Upstream modules (mods/) built in; every other one is deleted from
        # the checkout before the build. Upstream also ships a hand-off to one
        # maker's 3D printers there: the build appends ~100 KB of its client
        # code to the kiri bundle, and whether it loaded depended on a
        # dependency's install script, so two builds of the same sources
        # could differ. Piccola is a CNC mill and does not ship it.
        "only": {"mods": ["cam-anim", "devel", "proxy"]},
        # Cut out of the checkout before the build: the export dialog's
        # 3D-printer section (printer / spool / send / print), between the
        # gcode/palette/zip/3mf button row and "send to print". Upstream only
        # hides it from the module above, so without that module it showed,
        # empty and disabled, on every CAM export. Found by its neighbours and
        # checked by its labels; a page that has moved fails the build.
        "cut": [{
            "file": "web/kiri/index.html",
            "after": ('id="print-3mf"', "</div>"),
            "before": '<div id="send-remote-head"',
            "expect": [">printer<", ">spool<", "disabled>send<",
                       "disabled>print<"],
        }],
        # Only the trees cam_server.py serves. The rest of the checkout
        # (node_modules, docs, electron scaffolding) is build-time only.
        "keep": ["web", "alt", "src"],
        # The service-worker bundle is ~25 MB of the same assets we already
        # serve from disk; static hosting never reads it.
        "drop": ["web/boot/bundle-*.bin"],
        "note": (
            "MIT. Built locally: upstream publishes only Electron installers,\n"
            "no static web build. Node + npm are needed for THIS script only,\n"
            "never at app runtime.\n"
            "Not committed — vendor/kirimoto/ is gitignored build output.\n"
            "Carries Piccola patches: see tools/patches/kirimoto/ and the\n"
            "`patches:` line above. They are applied to the checkout before the\n"
            "build, so the bundles here are built from patched sources.\n"
            "cam_server.py reproduces upstream's URL mapping (app.js): /lib/ is\n"
            "overlaid on alt/ then src/, plus the kiri entry-point rewrites."
        ),
    },
}

# Directories never worth vendoring (build inputs, VCS, CI).
PRUNE = {".git", ".github", "node_modules", ".cache", "dist", "build"}


def run(cmd: str, cwd: str) -> None:
    print(f"    $ {cmd}")
    proc = subprocess.run(cmd, cwd=cwd, shell=True)
    if proc.returncode != 0:
        raise SystemExit(f"[fail] command exited {proc.returncode}: {cmd}")


def clone(repo: str, ref: str, dest: str) -> str:
    run(f"git clone --depth 1 --branch {ref} {repo} {dest}", cwd=os.path.dirname(dest))
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dest, text=True).strip()
    return sha


def apply_patches(bundle: str, dest: str) -> list[str]:
    """Apply tools/patches/<bundle>/*.patch to the checkout, in filename order.

    ``git apply`` rather than ``patch``: the checkout is a git tree, the error
    messages name the file and hunk, and --3way can rebase a hunk whose context
    has drifted slightly. A patch that will not apply raises, so a re-vendor
    against a bumped ref fails loudly instead of quietly dropping the change.
    """
    root = os.path.join(PATCHES, bundle)
    if not os.path.isdir(root):
        return []
    names = sorted(n for n in os.listdir(root) if n.endswith(".patch"))
    for name in names:
        path = os.path.join(root, name)
        print(f"    patch: {name}")
        proc = subprocess.run(
            ["git", "apply", "--3way", "--whitespace=nowarn", path],
            cwd=dest)
        if proc.returncode != 0:
            raise SystemExit(
                f"[fail] patch did not apply: tools/patches/{bundle}/{name}\n"
                f"       The ref may have moved. Rebase the patch against "
                f"{SOURCES[bundle]['ref']}, or delete it if upstream now "
                f"carries the change.")
    return names


def copy_tree(src: str, dst: str, keep=None, drop=()) -> None:
    """Copy src → dst, dropping PRUNE dirs and resolving symlinks.

    ``symlinks=False`` matters: grid-apps' web/ tree symlinks its font
    directories into node_modules, and those would dangle the moment the build
    checkout is deleted. Copying the targets makes vendor/ self-contained.

    *keep* limits the copy to named top-level entries; *drop* removes glob
    matches afterwards.
    """
    import glob

    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    entries = keep if keep else os.listdir(src)
    for name in entries:
        s = os.path.join(src, name)
        if not os.path.exists(s):
            raise SystemExit(f"expected {name}/ in the build output, not found")
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, symlinks=False,
                            ignore=shutil.ignore_patterns(*PRUNE))
        else:
            shutil.copy2(s, d)

    for pattern in drop:
        for path in glob.glob(os.path.join(dst, pattern)):
            os.remove(path)


def write_version(bundle: str, spec: dict, sha: str, patches=()) -> None:
    applied = (
        "patches: " + ", ".join(patches) + "\n" if patches else
        "patches: none\n"
    )
    path = os.path.join(VENDOR, bundle, "VERSION")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f"project:  {spec['name']}\n"
            f"upstream: {spec['repo']}\n"
            f"ref:      {spec['ref']}\n"
            f"commit:   {sha}\n"
            f"license:  {spec['license']}\n"
            f"{applied}\n"
            f"Vendored by tools/fetch_vendor.py. Do not edit in place.\n\n"
            f"{spec['note']}\n"
        )


def keep_only(bundle: str, src: str, only: dict) -> None:
    """In each directory named in *only*, delete every entry not listed.

    A listed entry that is missing fails the build: upstream has renamed or
    dropped something this app relies on.
    """
    for rel, names in only.items():
        root = os.path.join(src, *rel.split("/"))
        present = set(os.listdir(root)) if os.path.isdir(root) else set()
        missing = [n for n in names if n not in present]
        if missing:
            raise SystemExit(
                f"[fail] {rel}/ has no {', '.join(missing)}: upstream moved it. "
                f"Update \"only\" for {bundle} in tools/fetch_vendor.py.")
        for name in sorted(present - set(names)):
            target = os.path.join(root, name)
            print(f"    remove: {rel}/{name}")
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)


def cut_blocks(bundle: str, src: str, cuts) -> None:
    """Delete the whole lines between two anchors in a checkout file.

    ``after`` is (marker, end): the cut starts on the line after the first
    *end* that follows *marker*. ``before`` is the marker whose line ends it.
    Every ``expect`` string must be inside the cut, so a page that has
    changed fails the build rather than losing the wrong lines.
    """
    for c in cuts:
        path = os.path.join(src, *c["file"].split("/"))
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        marker, end = c["after"]
        try:
            i = text.index(end, text.index(marker))
            i = text.index("\n", i) + 1
            j = text.index(c["before"], i)
        except ValueError:
            raise SystemExit(
                f"[fail] cut anchors not found in {c['file']}: upstream moved "
                f"it. Update \"cut\" for {bundle} in tools/fetch_vendor.py.")
        j = text.rfind("\n", 0, j) + 1
        block = text[i:j]
        lost = [e for e in c.get("expect", ()) if e not in block]
        if not block.strip() or lost:
            raise SystemExit(
                f"[fail] cut in {c['file']} would not remove what it should "
                f"(missing {lost}). Update \"cut\" for {bundle} in "
                f"tools/fetch_vendor.py.")
        print(f"    cut: {c['file']} ({block.count(chr(10))} lines)")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text[:i] + text[j:])


def fetch(bundle: str) -> None:
    spec = SOURCES[bundle]
    print(f"\n== {bundle}: {spec['name']} @ {spec['ref']} ==")
    with tempfile.TemporaryDirectory(prefix=f"vendor-{bundle}-") as tmp:
        src = os.path.join(tmp, bundle)
        sha = clone(spec["repo"], spec["ref"], src)
        print(f"    commit {sha}")

        # before the build: the bundles are compiled from these sources
        patches = apply_patches(bundle, src)

        # parts of upstream this app does not ship (see "only" and "cut" in
        # SOURCES)
        keep_only(bundle, src, spec.get("only", {}))
        cut_blocks(bundle, src, spec.get("cut", ()))

        for step in spec["build"] or []:
            run(step, cwd=src)

        os.makedirs(VENDOR, exist_ok=True)
        dst = os.path.join(VENDOR, bundle)
        copy_tree(src, dst, keep=spec.get("keep"), drop=spec.get("drop", ()))
        # Carry the upstream licence across even when `keep` trims the tree.
        for cand in ("LICENSE", "license.md", "LICENSE.md", "NOTICE"):
            s = os.path.join(src, cand)
            if os.path.isfile(s) and not os.path.exists(os.path.join(dst, cand)):
                shutil.copy2(s, os.path.join(dst, cand))
        write_version(bundle, spec, sha, patches)

    total = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _dn, fn in os.walk(os.path.join(VENDOR, bundle)) for f in fn
    )
    print(f"    -> vendor/{bundle}/  ({total / 1e6:.1f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("bundle", choices=[*SOURCES, "all"],
                    help="which bundle to vendor")
    args = ap.parse_args()

    targets = list(SOURCES) if args.bundle == "all" else [args.bundle]
    failed = []
    for b in targets:
        try:
            fetch(b)
        except SystemExit as e:
            # One bundle failing must not block the other: a missing bundle is a
            # degraded CAM tab, not a broken app.
            print(f"[warn] {b}: {e}", file=sys.stderr)
            failed.append(b)
    if failed:
        print(f"\n[warn] not vendored: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
