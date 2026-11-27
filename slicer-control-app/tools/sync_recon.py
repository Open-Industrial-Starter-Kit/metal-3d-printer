#!/usr/bin/env python3
"""
tools/sync_recon.py
Re-copy the reconstruction algorithm from the source project into recon/.

    python3 tools/sync_recon.py --from "../../surface_reconstruction project"
    python3 tools/sync_recon.py --check          # report drift, change nothing

recon/ is a copy, not a dependency, for the reasons in recon/__init__.py: the
source imports PyQt6 and this app is PySide6, and the source is a plain folder
rather than a package or a git repository. That copy has to be re-made whenever
the algorithm moves, so the three edits it needs are applied here rather than by
hand — they are mechanical, and doing them by hand is how a copy silently stops
matching its source.

The edits, and nothing else:

  1. workers.py    — drop the FileLoadWorker and ReconstructionWorker QThread
                     classes, repoint `from utils import` at the package.
  2. autoconfig.py — drop the PyQt6 import and rewrite AutoTuneWorker as the
                     plain function `auto_tune`.
  3. isolate.py    — the child process imports recon.workers, not workers, and
                     an `abort()` method is appended (see ISOLATE_ABORT).

Only (1) and (3) are pure deletions/renames. (2) replaces a class with the
function in PORTED_AUTO_TUNE below, so if the upstream AutoTuneWorker gains
behaviour, that function needs the same change — the script says so loudly
rather than quietly shipping the old one.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import io
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECON = os.path.join(REPO_ROOT, "recon")

#: Default location of the source project, relative to the repo root.
DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(REPO_ROOT)), "surface_reconstruction project")

FILES = ("utils.py", "workers.py", "autoconfig.py", "isolate.py")

#: The body that replaces AutoTuneWorker. Kept here, beside the script that
#: applies it, so there is one place to update when the original changes.
PORTED_AUTO_TUNE = '''#: Above this point count, candidates are scored on a decimated copy. Scoring
#: reconstructs every candidate, so the cost is per-candidate, not one-off.
AUTOTUNE_DECIMATE_ABOVE = 200000


def auto_tune(pcd, desc, candidates, gap_baseline, seal_base, service,
              objective='fidelity', progress=None):
    """Reconstruct each candidate and score it against the original cloud.

    Returns ``(results, note)``; *results* is sorted best-first. *progress* is
    an optional ``fn(index, total, label)`` called before each candidate.

    A background thread in the original project. It is a plain function here so
    this module stays free of Qt bindings -- ui/pages/ply_viewer.py runs it off
    the GUI thread and turns the callback into a signal.
    """
    from recon.workers import settings_to_params
    from recon.isolate import ReconstructionFailed, to_mesh

    def report(index, total, label):
        if progress is not None:
            progress(index, total, label)

    note = ""
    source = pcd
    n = len(source.points)
    if n > AUTOTUNE_DECIMATE_ABOVE:
        source = source.random_down_sample(AUTOTUNE_DECIMATE_ABOVE / n)
        note = (f"Scored on a {len(source.points):,} point decimated copy "
                f"of {n:,} points - settings are applied to the full cloud.")
    service.set_cloud(np.asarray(source.points))

    points = np.asarray(pcd.points)
    rng = np.random.default_rng(0)
    probe = points if len(points) <= 8000 else points[rng.choice(len(points), 8000, replace=False)]

    # Only the measured shape decides this. Sealing used to force it on, which
    # meant the 0.75 non-watertight penalty landed on every open-sheet
    # candidate while a Poisson blob - watertight on its own - collected
    # nothing, handing the search to exactly the result the overreach term
    # exists to prevent. A candidate does not need to close itself when the
    # seal is going to close it afterwards.
    expect_closed = not desc['height_field_ok']
    target_triangles = max(2000, len(points))

    results = []
    total = len(candidates)
    for index, (label, settings) in enumerate(candidates):
        report(index, total, label)
        params = settings_to_params(settings, gap_baseline, seal_base)
        failed_metrics = {'rms': np.inf, 'coverage': 0.0, 'triangles': 0,
                          'watertight': False, 'manifold': False}
        scorer = score_smooth if objective == 'smooth' else score_mesh
        warning = None
        try:
            vertices, triangles, normals, info = service.reconstruct(params)
            mesh = to_mesh(vertices, triangles, normals)
            score, metrics = scorer(mesh, probe, desc['noise_sigma'],
                                    desc['spacing'], expect_closed, target_triangles,
                                    info.get('surface_triangles'))
            error = None
            # a candidate that reconstructed fine but could not be sealed is
            # still a real result, so it stays ranked rather than being
            # reported as a failure
            warning = info.get('seal_error')
        except ReconstructionFailed as exc:
            score, metrics, error = np.inf, failed_metrics, str(exc)
        except Exception as exc:
            score, metrics, error = np.inf, failed_metrics, str(exc)
        results.append({'label': label, 'settings': settings, 'score': score,
                        'metrics': metrics, 'error': error, 'warning': warning})

    report(total, total, "done")
    results.sort(key=lambda item: item['score'])
    return results, note
'''

ISOLATE_ABORT = '''
    def abort(self):
        """Kill the helper immediately, without taking the lock.

        shutdown() is the polite path and it waits on ``self._lock`` -- which is
        held for the whole of reconstruct(), up to DEFAULT_TIMEOUT (ten
        minutes). So shutdown() cannot interrupt the very thing an interactive
        caller needs to interrupt: a reconstruction in flight.

        This is the interrupt. The helper dies, the blocked ``_await`` notices
        on its next poll and returns ``('died', None)``, and the caller raises
        ReconstructionFailed instead of waiting out the timeout -- which
        releases the lock and lets shutdown() proceed.

        Added for the app's viewer (ui/pages/ply_viewer.py), where closing the
        window mid-reconstruction otherwise froze the whole GUI.
        """
        process = self._process
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
            except Exception:
                pass
'''

WORKERS_HEADER = '''"""
recon/workers.py
Surface reconstruction core, from the surface_reconstruction project.

Vendored rather than imported: the original depends on Qt bindings that cannot
share a process with this app's PySide6. See recon/__init__.py for what changed.
"""
import traceback

import open3d as o3d
import numpy as np
from scipy.spatial import KDTree

from recon.utils import build_grid_triangles, build_poly_design_matrix'''


def transform(name: str, text: str) -> str:
    """Apply this file's edits. Raises if the source no longer looks as expected."""
    if name == "utils.py":
        return text

    if name == "workers.py":
        lines = text.split("\n")
        if not (lines[0].startswith("# workers.py")
                and any(l.startswith("from PyQt6") for l in lines[:12])):
            raise SystemExit("workers.py: header is not what this script expects")
        cut = next(i for i, l in enumerate(lines)
                   if l.startswith("from utils import"))
        text = "\n".join(WORKERS_HEADER.split("\n") + lines[cut + 1:])
        for start, stop in (("class FileLoadWorker(QThread):",
                             "# A connected piece of a Poisson result is kept"),
                            ("class ReconstructionWorker(QThread):", None)):
            if start not in text:
                raise SystemExit(f"workers.py: expected to find {start!r}")
            i = text.index(start)
            text = text[:i] + (text[text.index(stop):] if stop else "")
        return text.rstrip() + "\n"

    if name == "autoconfig.py":
        lines = text.split("\n")
        qt = [i for i, l in enumerate(lines[:25]) if l.startswith("from PyQt6")]
        ut = [i for i, l in enumerate(lines[:25]) if l.startswith("from utils import")]
        if not qt or not ut:
            raise SystemExit("autoconfig.py: header is not what this script expects")
        del lines[qt[0]]
        idx = ut[0] - (1 if ut[0] > qt[0] else 0)
        lines[idx] = "from recon.utils import build_poly_design_matrix"
        text = "\n".join(lines)
        if "class AutoTuneWorker(QThread):" not in text:
            raise SystemExit("autoconfig.py: AutoTuneWorker not found — the port "
                             "in PORTED_AUTO_TUNE may be stale, check it by hand")
        return text[:text.index("class AutoTuneWorker(QThread):")] + PORTED_AUTO_TUNE

    if name == "isolate.py":
        old = "    from workers import reconstruct"
        if text.count(old) != 1:
            raise SystemExit("isolate.py: child import is not what this script expects")
        text = text.replace(old, "    from recon.workers import reconstruct")
        anchor = "    def shutdown(self):"
        if text.count(anchor) != 1:
            raise SystemExit("isolate.py: shutdown() not found, cannot place abort()")
        if "def abort(" in text:
            raise SystemExit("isolate.py: the source now has its own abort() — "
                             "drop ISOLATE_ABORT from this script")
        return text.replace(anchor, ISOLATE_ABORT.strip("\n") + "\n\n" + anchor)

    raise SystemExit(f"no rule for {name}")


def qt_free(text: str) -> bool:
    """True when nothing in the module *imports* Qt (prose may mention it)."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        for m in mods:
            if m.split(".")[0] in ("PyQt6", "PyQt5", "PySide6", "PySide2"):
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("--from", dest="source", default=DEFAULT_SOURCE,
                    help="the surface_reconstruction project folder")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero; write nothing")
    args = ap.parse_args()

    if not os.path.isdir(args.source):
        print(f"[fail] source not found: {args.source}", file=sys.stderr)
        return 2

    drift = 0
    for name in FILES:
        src = os.path.join(args.source, name)
        if not os.path.isfile(src):
            print(f"[fail] missing in source: {name}", file=sys.stderr)
            return 2
        want = transform(name, io.open(src, encoding="utf-8").read())
        if not qt_free(want):
            print(f"[fail] {name} still imports Qt after transform", file=sys.stderr)
            return 2
        ast.parse(want)                      # never write something unparsable

        dst = os.path.join(RECON, name)
        have = io.open(dst, encoding="utf-8").read() if os.path.isfile(dst) else ""
        if have == want:
            print(f"  {name}: up to date")
            continue
        drift += 1
        if args.check:
            print(f"  {name}: DRIFTED")
            for line in list(difflib.unified_diff(
                    have.splitlines(), want.splitlines(),
                    fromfile=f"recon/{name}", tofile=f"source/{name}", n=1))[:40]:
                print("    " + line)
        else:
            io.open(dst, "w", encoding="utf-8").write(want)
            print(f"  {name}: updated")

    if args.check and drift:
        print(f"\n{drift} file(s) differ from the source project.", file=sys.stderr)
        return 1
    print("\nrecon/ is in step with the source project."
          if not drift else f"\n{drift} file(s) updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
