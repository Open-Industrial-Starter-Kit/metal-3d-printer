#!/usr/bin/env python3
"""Are the vendor patches actually live in the app, or only on disk?

Three different things have to line up before a change is visible, and each one
fails quietly on its own:

1. the patch is applied to ``vendor/kirimoto/src`` (``fetch_vendor.py`` does
   this when it rebuilds);
2. the *packed bundle* was rebuilt from that source -- the app loads
   ``/lib/pack/kiri-main.js`` and ``kiri-work.js``, never the individual source
   files, so a patch present in ``src/`` and absent from the pack does nothing
   at all;
3. the desktop app was restarted, not just reloaded -- see RESTART_NOTE.

Point 2 is the one that has cost the most time. `test_patch_symbols.js` checks
the source tree and passes happily while the running app has none of it.

Usage
-----
    ./venv/bin/python tools/verify_patches.py          # check everything
    ./venv/bin/python tools/verify_patches.py 0011     # one patch

Exit status is non-zero if a patch is missing from the source, or if a declared
bundle marker is missing from the pack the app serves.

Declaring a bundle marker
-------------------------
Minification renames module-local functions -- patch 0006 adds `stockCircle`
and that name appears in no bundle -- so a patch cannot be found in the pack by
grepping for what it added. What survives is string literals and property
names. So a patch says for itself what to look for, in its header:

    Bundle-Check: showGrid2\\(\\w+\\)      a regex that MUST match the pack
    Bundle-Absent: showGrid2\\(!          a regex that must NOT match it

Both are optional and may repeat. A patch with neither is reported as
*undeclared* rather than passing, because "we did not look" and "we looked and
it was fine" are not the same answer.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_DIR = os.path.join(ROOT, "tools/patches/kirimoto")
SRC = os.path.join(ROOT, "vendor/kirimoto/src")
#: The trees fetch_vendor.py keeps from the build checkout ("keep").
VENDORED_TREES = ("web", "alt", "src")

#: Which packed bundle a patched source file ends up in. Anything under a
#: ``work/`` directory is worker code and is bundled into `kiri-work.js`;
#: everything else in the kiri or moto trees goes into `kiri-main.js`.
def pack_for(rel: str) -> str:
    return "kiri-work.js" if "/work/" in rel else "kiri-main.js"


#: The overlay the server searches for `/lib/`, in order (see cam_server.py).
#: `alt` wins where it has the file, which is why an edit to `src/pack` alone
#: can appear to do nothing.
OVERLAYS = ("alt", "src")

RESTART_NOTE = """\
Injected JS (ui/cam_*.py) is compiled into a QWebEngineScript in the Python
process when the webview is created. Reloading the Kiri page re-fetches
everything under vendor/ but CANNOT pick up a change to ui/*.py -- only
quitting and relaunching the app does. If a vendor change shows up and an
injected one does not, that is what happened."""


def served(pack: str):
    """The file the server actually serves for a pack name, and where from."""
    for base in OVERLAYS:
        p = os.path.join(ROOT, "vendor/kirimoto", base, "pack", pack)
        if os.path.isfile(p):
            return p, base
    return None, None


def parse(path: str):
    """Target files with their added and removed lines, plus declared markers."""
    text = open(path, encoding="utf-8", errors="replace").read()
    head = text.split("diff --git")[0]
    checks = re.findall(r"^Bundle-Check:\s*(.+)$", head, re.M)
    absent = re.findall(r"^Bundle-Absent:\s*(.+)$", head, re.M)

    files, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m:
            cur = m.group(1)
            files.setdefault(cur, {"add": [], "del": []})
            continue
        if cur is None or line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            files[cur]["add"].append(line[1:])
        elif line.startswith("-"):
            files[cur]["del"].append(line[1:])
    return files, [c.strip() for c in checks], [a.strip() for a in absent]


def meaningful(line: str) -> bool:
    """Lines worth asserting on: skip blanks and bare punctuation."""
    s = line.strip()
    if len(s) < 8:
        return False
    return bool(re.search(r"[A-Za-z]{3}", s))


def main() -> int:
    want = sys.argv[1:]
    names = sorted(f for f in os.listdir(PATCH_DIR) if f.endswith(".patch"))
    if want:
        names = [n for n in names if any(w in n for w in want)]
    if not names:
        print("no patches matched"); return 1

    bad = 0
    undeclared = []
    for name in names:
        path = os.path.join(PATCH_DIR, name)
        files, checks, absents = parse(path)
        print("\n%s" % name)

        # 1. applied to the source tree?
        for rel, hunk in files.items():
            # patch paths are relative to the grid-apps root, which the vendor
            # tree mirrors -- src/... and web/... both live directly under it
            target = os.path.join(ROOT, "vendor/kirimoto", rel)
            # a build input (package.json): fetch_vendor.py copies only the
            # served trees, so its effect shows in what it built, not here
            if rel.split("/")[0] not in VENDORED_TREES:
                print("  --    build input, not vendored: %s" % rel); continue
            if not os.path.isfile(target):
                print("  FAIL  source missing: %s" % rel); bad += 1; continue
            body = open(target, encoding="utf-8", errors="replace").read()
            miss = [l for l in hunk["add"] if meaningful(l) and l.strip() not in body]
            left = [l for l in hunk["del"] if meaningful(l) and l.strip() in body]
            # Informational, deliberately not a failure. The series is
            # applied in order and later patches rewrite lines earlier ones
            # added, so a line from 0002 legitimately no longer exists verbatim
            # once 0008 has been through the same function. Judging each patch
            # against the finished tree therefore cannot distinguish "never
            # applied" from "applied then superseded", and a check that cries
            # wolf is worse than no check. The declared markers below are the
            # authoritative answer.
            if miss or left:
                print("  note  source: %s -- %d added line(s) absent, %d removed"
                      " line(s) still present (expected where a later patch"
                      " rewrites the same lines)" % (rel, len(miss), len(left)))
            else:
                print("  ok    source: %s matches verbatim" % rel)

        # 2. present in the bundle the app actually serves?
        packs = sorted({pack_for(r) for r in files})
        if not checks and not absents:
            undeclared.append(name)
            print("  --    bundle: no marker declared (%s) -- not checked"
                  % ", ".join(packs))
            continue
        # against the source first: a marker missing here means the patch was
        # never applied at all, which is a different fault from a stale pack
        for rel in files:
            target = os.path.join(ROOT, "vendor/kirimoto", rel)
            if not os.path.isfile(target):
                continue
            body = open(target, encoding="utf-8", errors="replace").read()
            for rx in checks:
                # source markers are minification-proof by construction, but a
                # regex written for the pack may not match the pretty source
                if re.search(rx, body):
                    print("  ok    source %s matches /%s/" % (rel, rx))
        # A marker has to match SOME pack, not every pack: a patch that spans
        # app/ and work/ code (0002 touches an anim-2d.js in each tree) has
        # markers that belong to one bundle or the other. Requiring all of them
        # everywhere reported a correctly-built pack as broken.
        loaded = []
        for pack in packs:
            f, base = served(pack)
            if not f:
                print("  FAIL  bundle: %s is not present at all" % pack)
                bad += 1
                continue
            loaded.append((pack, base,
                           open(f, encoding="utf-8", errors="replace").read()))
        for rx in checks:
            hit = [(p_, b_) for p_, b_, blob in loaded if re.search(rx, blob)]
            if hit:
                print("  ok    bundle %s/%s matches /%s/"
                      % (hit[0][1], hit[0][0], rx))
            else:
                print("  FAIL  bundle MISSING /%s/ in %s"
                      % (rx, ", ".join(p_ for p_, _, _ in loaded)))
                print("          the pack was not rebuilt from the patched"
                      " source -- run tools/fetch_vendor.py kirimoto")
                bad += 1
        for rx in absents:
            hit = [(p_, b_) for p_, b_, blob in loaded if re.search(rx, blob)]
            if hit:
                print("  FAIL  bundle %s/%s still contains /%s/"
                      % (hit[0][1], hit[0][0], rx))
                print("          the pack predates this patch -- run"
                      " tools/fetch_vendor.py kirimoto")
                bad += 1
            else:
                print("  ok    bundle clear of /%s/ in %s"
                      % (rx, ", ".join(p_ for p_, _, _ in loaded)))

    print("\n" + "-" * 68)
    if undeclared:
        print("%d patch(es) declare no bundle marker, so only their source was"
              " checked:" % len(undeclared))
        for n in undeclared:
            print("    %s" % n)
        print("  add a `Bundle-Check:` or `Bundle-Absent:` line to a patch"
              " header to cover it")
    print("\n%s" % RESTART_NOTE)
    print("\n%s" % ("FAILURES: %d" % bad if bad else "all checked patches are live"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
