"""
ui/cam_grid.py
Lifts Kiri:Moto's **show grid** switch to the top of the left panel.

Why it moves
------------
It used to live beside the rotary switch, and ui/cam_rotary.py deliberately put
it there: `camStockIndexGrid` was a rotary sub-option, because outside indexed
mode the flag was ignored ::

    SPACE.platform.showGrid2(!env.isIndexed || camStockIndexGrid);

Patch 0010 makes it apply in both modes. A view option nested under *rotary*
would then read as applying only there — the exact confusion the patch removes
— so it comes out and sits at the top of the panel, above the groups, where a
switch about what the viewport draws belongs.

Nothing is rebuilt. `api.ui.camStockIndexGrid` is the input `newBoolean()`
registered, and everything Kiri does with it goes through that reference rather
than through where it sits: the click handler is bound to the element, and
persistence walks the *settings* keys reading `api.ui[key]`. Re-parenting the
row therefore keeps upstream's behaviour exactly — the same reasoning
ui/cam_rotary.py sets out for the switch it moves.

On ordering
-----------
The row is placed first in the panel, and re-asserted rather than placed once.
ui/cam_rotary.py inserts its group at the same spot, both modules poll for a
menu that is built asynchronously, and whichever lands last would otherwise
win. Re-asserting on the events that redraw the panel makes the outcome
independent of that race; upstream's own insert happens once and is guarded, so
this settles rather than fighting it.
"""

from __future__ import annotations

import json

#: The `api.ui` key of the switch being moved.
KIRI_UI_KEY = "camStockIndexGrid"

#: Marks the row once moved, so the work is not repeated and the row can be
#: found again cheaply.
MOVED_ID = "piccola-grid-row"


def lift_grid_switch_js() -> str:
    """JS moving the *show grid* row to the top of the left settings panel."""
    return """
(function () {
  var KEY = %(key)s, ID = %(id)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function rowOf(el) {
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) return n;
    }
    return null;
  }

  // First in the panel, and kept there: ui/cam_rotary.py inserts its group at
  // the same place and both poll for a menu built asynchronously, so a single
  // placement would depend on which landed last.
  function place() {
    var a = api();
    var panel = document.getElementById('panel-slide');
    if (!a || !panel) return false;
    var row = document.getElementById(ID) || rowOf(a.ui && a.ui[KEY]);
    if (!row) return false;
    row.id = ID;
    // Lifted clear of its group, so it has to leave the group behind as well.
    // Kiri collapses by tagging each row with `_group` when it is built and
    // then hiding whatever matches a collapsed name --
    // `hidn = hidden[el._group] === true` in inputs.js `setMode` -- which is a
    // property of the element, not of where it sits. Both rows are created
    // under `newGroup(LANG.cs_menu, ..., group:"cam-stock")`, so at the top of
    // the panel they still answered to the Stock header and disappeared with
    // it. Cleared on every pass, since the row is re-placed on several events.
    row._group = undefined;
    if (panel.firstChild !== row) panel.insertBefore(row, panel.firstChild);
    return true;
  }

  function install() {
    var a = api();
    if (!a || !a.event || !place()) return false;
    ['settings.saved', 'boolean.update', 'widget.add', 'platform.layout']
      .forEach(function (ev) {
        try { a.event.on(ev, place); } catch (e) { }
      });
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "key": json.dumps(KIRI_UI_KEY),
        "id": json.dumps(MOVED_ID),
    }
