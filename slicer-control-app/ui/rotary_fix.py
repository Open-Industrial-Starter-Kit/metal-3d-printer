"""
ui/rotary_fix.py
Stops a rotary program from moving A0 when it re-zeroes A at the end.

What went wrong
---------------
A Fusion rotary post ("rotary post v2.4") lets A accumulate across the job --
736.52 degrees, -914.79 degrees: whole turns plus a remainder -- and then, to
"unwind" those turns, ends with::

    X-15.312 A736.52 F1617.31
    ...
    G10 L20 P1 A0

``G10 L20 P1 A0`` means "wherever A is now, call it zero". The chuck is at
736.52 = two whole turns + 16.52 degrees, so A0 is quietly moved 16.52 degrees
off where the operator set it -- and the controller stores that offset. The
next file starts from the moved zero and every feature lands at the wrong
angle: carved letters appear twice, a rough and a finish that do not line up.
Measured on files that ran fine on their own in August: the giveaway job
leaves A0 16.52 degrees off, the keychain job 165.21.

The unwind was meant to drop the whole turns, not the remainder. The line that
does that keeps the remainder::

    G10 L20 P1 A16.52

which tells the controller the chuck is 16.52 degrees past zero -- true -- so
A0 stays exactly where it was set, and the multi-turn count is still gone.

What this does
--------------
:func:`fix` rewrites every ``G10 L20 P1 ... A0`` to carry the A position the
program had actually reached there, reduced to (-180, 180]. It follows the
program to know that position: absolute A words, incremental ones under G91,
G90/G91 switching (``G28 G91 Z0.`` then ``G90`` is in exactly these files).
A re-zero that already sits on a whole turn is left alone, as is anything it
cannot follow with certainty -- a G10 on another work offset, or A moved by
something it does not model -- because a wrong correction would be worse than
the bug.

The program is otherwise byte-for-byte unchanged, line endings included.
"""

from __future__ import annotations

import os
import re
import tempfile

#: A whole-turn re-zero is already correct; anything nearer than this to a
#: multiple of 360 degrees is treated as one.
_TURN_TOL = 0.0005

_WORD = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)", re.I)
_COMMENT = re.compile(r"\([^)]*\)|;.*$")


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _remainder(a: float) -> float:
    """*a* reduced to (-180, 180]: the same physical angle, fewest degrees."""
    r = (a + 180.0) % 360.0 - 180.0
    return 180.0 if r == -180.0 else r


def fix(text: str):
    """Return ``(new_text, changes)``.

    *changes* lists ``(line_number, old_line, new_line)`` for each re-zero that
    was corrected; empty when the program needed nothing.
    """
    lines = text.splitlines(keepends=True)
    absolute = True
    a = 0.0
    known = True            # False once A moved in a way we cannot follow
    changes = []

    for i, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        code = _COMMENT.sub("", body).upper()
        if not code.strip():
            continue
        words = [(w.upper(), v) for w, v in _WORD.findall(code)]
        gs = [float(v) for w, v in words if w == "G"]

        if 10 in gs:
            L = next((float(v) for w, v in words if w == "L"), None)
            P = next((float(v) for w, v in words if w == "P"), None)
            A = next((float(v) for w, v in words if w == "A"), None)
            if L == 20 and P == 1 and A is not None and abs(A) < _TURN_TOL and known:
                r = _remainder(a)
                if abs(r) > _TURN_TOL:
                    new_body = re.sub(r"(A\s*)[-+]?\d*\.?\d+", lambda m: m.group(1) + _fmt(r),
                                      body, count=1, flags=re.I)
                    ending = raw[len(body):]
                    lines[i] = new_body + ending
                    changes.append((i + 1, body, new_body))
            # after any G10 on A, the program's own A position is now the value
            # it set: follow that so a second re-zero is judged correctly
            if L == 20 and P == 1 and A is not None:
                a = _remainder(a) if changes and changes[-1][0] == i + 1 else A
            continue

        if 90 in gs:
            absolute = True
        if 91 in gs:
            absolute = False
        # G28/G30/G53 move by other rules; A in them is not followed
        if any(g in gs for g in (28, 30, 53, 92)) and any(w == "A" for w, _ in words):
            known = False
            continue
        for w, v in words:
            if w == "A":
                a = float(v) if absolute else a + float(v)

    return "".join(lines), changes


def fixed_copy(path: str):
    """Correct *path* into a temporary copy. Returns ``(upload_path, changes)``.

    The original file is never modified. When nothing needs correcting the
    original path is returned and *changes* is empty.
    """
    try:
        with open(path, "r", newline="", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return path, []
    new_text, changes = fix(text)
    if not changes:
        return path, []
    folder = tempfile.mkdtemp(prefix="piccola-rotary-")
    out = os.path.join(folder, os.path.basename(path))
    with open(out, "w", newline="") as fh:
        fh.write(new_text)
    return out, changes
