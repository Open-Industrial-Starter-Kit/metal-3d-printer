"""
ui/cam_rotation.py
Makes EasyTrace's Board Rotation apply itself once typing stops.

Upstream needs a second gesture to commit an angle: type it, then press *Apply
Rotation* (or Enter). The field otherwise sits there looking applied while the
board has not moved — the one control in the Board Placement panel that does
nothing on its own now that picking an origin applies itself
(``ui/cam_origin.py``).

So the angle is committed automatically a beat after the last keystroke, and the
button that used to be the only way to do it is hidden.

How it commits
--------------
Through the tool's own button. ``commitRotation()`` is a closure inside
``setupRotationControls()`` — not reachable from outside — but it is what
``#apply-rotation-btn`` calls, so clicking that hidden button runs the real
thing: the scene update, the origin-display refresh, the write-back of the
normalised angle and the status line. Nothing is reimplemented, and an upstream
change to what committing means is picked up for free. This is the same
hidden-control-driven-programmatically trick as ``ui/cam_export.py``.

Two guards on the debounce:

* **Nothing to do.** The click is skipped when the typed angle already matches
  the board's rotation, so idle keystrokes (arrows, retyping the same number)
  do not repeat the status line or fire needless transform notifications.

* **Half-typed values.** For ``<input type="number">`` a partial entry like
  ``-`` reads as ``""``, and ``parseFloat("") || 0`` is 0 — committing then
  would apply 0° *and* rewrite the field, wiping the minus sign the moment the
  timer expired. That is exactly the bug ``ui/cam_fixes.py`` works around for
  the origin offsets, so the commit waits for the field to hold a real number.

Enter still commits immediately: that is upstream's own handler and it is left
alone.

Whether the field can be used at all is not decided here: ``ui/cam_origin.py``
disables the whole Board Placement panel — this field and *Reset Rotation*
included — until a board is open, since it already holds the listener that
notices files arriving.
"""

from __future__ import annotations

#: Quiet period after the last keystroke before the angle is applied, in ms.
#: Long enough to type "-12.5" without it committing between digits, short
#: enough that the board follows the field rather than lagging behind it.
IDLE_MS = 500

_FIELD_ID = "rotation-angle"
_APPLY_ID = "apply-rotation-btn"
_RESET_ID = "reset-rotation-btn"


def css() -> str:
    """Hide Apply Rotation and re-centre what is left of its row."""
    return "\n".join([
        f"#{_APPLY_ID} {{ display: none !important; }}",
        # The row is a two-column grid that held Apply + Reset; with Apply gone
        # Reset would sit in the left half. Span it so it reads as the row's
        # own control, matching the Origin Position grid above it.
        f"#{_RESET_ID} {{ grid-column: 1 / -1; }}",
    ])


def auto_apply_js() -> str:
    """JS that commits the rotation once typing has stopped."""
    return f"""
(function () {{
  var FIELD_ID = {_q(_FIELD_ID)};
  var APPLY_ID = {_q(_APPLY_ID)};
  var IDLE_MS = {IDLE_MS};
  var timer = null;

  function rotation() {{
    try {{ return window.easytrace.getUI().scene.transform.rotation; }}
    catch (e) {{ return null; }}
  }}

  function commit() {{
    var field = document.getElementById(FIELD_ID);
    var apply = document.getElementById(APPLY_ID);
    if (!field || !apply) return;
    // Half-typed value ("-", "", "1e"): <input type=number> reports "" and the
    // tool would read that as 0 and overwrite what is being typed.
    if (field.value === '') return;
    var typed = parseFloat(field.value);
    if (!isFinite(typed)) return;
    var now = rotation();
    // Already there — nothing to say and nothing to notify. Compared on the
    // tool's own display precision, since that is what the field shows.
    if (now !== null && Math.abs(typed - now) < 0.05) return;
    apply.click();          // runs the tool's own commitRotation()
  }}

  function schedule() {{
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {{ timer = null; commit(); }}, IDLE_MS);
  }}

  function install() {{
    var field = document.getElementById(FIELD_ID);
    if (!field) return false;
    field.addEventListener('input', schedule);
    // Enter is upstream's own immediate commit; drop any pending timer so it
    // cannot fire a second one behind it.
    field.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' && timer) {{ clearTimeout(timer); timer = null; }}
    }});
    return true;
  }}

  // The markup appears after this script runs, so retry briefly.
  var tries = 400;
  (function wait() {{
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();
}})();
"""


def _q(s: str) -> str:
    """Minimal JS string literal — these are all internal constants."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
