"""
ui/cam_toolchange.py
Takes Kiri:Moto's **tool init** switch out of the Output group, and pins the
tool-change preamble off.

What the switch does
--------------------
`camToolInit` (Output > *tool init*, `mode/cam/app/init-menu.js`) is upstream's
"add tool change with first operation"; with it off, "the gcode assumes the
first tool is loaded and offset probed". It ships **true**, and the one place
it is read is the exporter (`mode/cam/work/export.js`)::

    let changeTool = !(out.tool && pos.t) || out.tool.getID() !== pos.t.getID();
    if (changeTool) {
        ...
        if (!laserOp && (spro.camToolInit || toolChanges > 0)) {
            filterEmit(cmdToolChange, { ...consts, spindle: newSpindle });
        }
        toolChanges++;
    }

The first operation always trips `changeTool` — `pos.t` starts unset — so with
the default on, every program opens with the device's tool-change macro. On
`Any.Generic.Grbl` that macro is `M6 T{tool}`.

Why it goes
-----------
Piccola has one spindle and no changer. There is no tool to select and nothing
to select it with, so the switch has no meaningful "on" position: it can only
add a line describing a machine this is not.

That line is also not inert. `M6` is not implemented by grbl 1.1 — the desktop
app hands the program to the firmware as-is, with no sender in between to
strip or intercept it the way a hosted sender would. So the honest default is
off, and off is the only setting worth having.

`camToolInit: false` therefore joins the pinned process fields in
`ui/pages/cam.py` (`_KIRI_PROCESS_DEFAULTS`) — a fact about the machine rather
than about a job, re-applied on every load so a saved profile cannot bring it
back — and the checkbox goes, because a control whose value is pinned is a
control that lies about being adjustable.

What is *not* covered
---------------------
Only the preamble. A genuine multi-tool job still emits the macro at each
later change, through the `toolChanges > 0` half of that condition, which no
setting reaches. That is deliberate: a program that really does need a
different cutter part way through should say so out loud rather than silently
carve the rest with the wrong one. It is also the case the note in
docs/cam-integration.md flags as unconfirmed against this firmware — nothing
here changes it either way.

How the row is hidden
---------------------
By marking it, not by styling it directly. Kiri's own panel code writes inline
``row.style.display`` when it refreshes group visibility (`inputs.js`), so an
inline style of ours would be overwritten; the class carries `!important` and
outranks it. The class and its rule are ui/cam_arcs.py's — shared rather than
duplicated, so one stylesheet rule covers every row this app removes.
"""

from __future__ import annotations

import json

from ui.cam_arcs import HIDDEN_ROW_CLASS

#: The `api.ui` key of the control whose row is removed.
KIRI_UI_KEY = "camToolInit"


def hide_tool_init_js() -> str:
    """JS marking Kiri's *tool init* row as hidden."""
    return """
(function () {
  var KEY = %(key)s, CLASS = %(cls)s;

  function mark() {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return false; }
    // built with the rest of the CAM menu, well after the kiri namespace
    // appears, so absence here means "not yet", not "gone"
    if (!ui || !ui[KEY]) return false;

    // walk out to the .var-row wrapper newBoolean() put the control in; the
    // input sits directly inside it, so a couple of levels is generous
    for (var n = ui[KEY], hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) {
        n.classList.add(CLASS);
        return true;
      }
    }
    return false;
  }

  var tries = 400;
  (function wait() {
    if (mark()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "key": json.dumps(KIRI_UI_KEY),
        "cls": json.dumps(HIDDEN_ROW_CLASS),
    }
