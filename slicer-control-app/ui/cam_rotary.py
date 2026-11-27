"""
ui/cam_rotary.py
Puts Kiri:Moto's rotary switch at the top of the left settings panel, on its
own.

Which switch is the rotary switch
---------------------------------
Not the obvious one. Kiri has two controls that read like "turn on the 4th
axis", and only one of them does anything:

``useIndexed`` — the machine dialog's **enable 4th axis** checkbox — appears in
exactly five places in the source, and four of them are plumbing (its own
definition, its default, and `setDeviceCode()` reading it back into the form).
The fifth is its only effect::

    (ui.gcodeResetA = newGCode(LANG.dv_resa_s, {... show:() => ui.useIndexed.checked}))

It reveals the **reset a** gcode-macro button, inside the machine dialog. That
is all. It does not gate the rotary operations, the indexed stock, or anything
in the viewport.

``camStockIndexed`` — the **indexed** checkbox in the Stock group — is the real
one. `updateAxisMode()` in `mode/cam/app/init-ui.js` reads it and nothing else::

    const { camStockIndexGrid, camStockIndexed } = env.current.process;
    let newIndexed = camStockIndexed;
    ...
    $('cam-index').style.display = showIndexed;
    $('cam-lathe').style.display = showIndexed;
    $('cam-loop').style.display  = showIndexed;

so it is what unlocks `index` / `lathe` / `loop`, switches the Z anchor to
middle, hides the bed and re-wraps the stock. Verified by clicking each in
isolation: with `useIndexed` off and `camStockIndexed` on, all three rotary ops
are `display: flex`.

So this moves `camStockIndexed`, not `useIndexed`.

`camStockIndexGrid` (**show grid**) used to travel with it, as that switch's own
sub-option: outside indexed mode the flag was ignored, so upstream rendered its
checkbox only while rotary was ticked. Patch 0010 makes it apply in both modes,
which stops it being a sub-option of anything — a general view switch nested
under *rotary* would read as applying only there. ui/cam_grid.py lifts it to the
top of the panel instead, and it is no longer moved here.

How
---
By moving the DOM nodes, not by building replacements. `api.ui.camStockIndexed`
is the `<input>` that `newBoolean()` returned and registered; everything Kiri
does with it goes through that reference, never through where it sits:

* the click handler is bound to the element, including `onBooleanClick`'s
  `if (el === ui.camStockIndexed) api.view.set_arrange()` special case;
* persistence is `updateSettingsFromFields(process, ...)`, which walks the
  *settings* keys and reads `api.ui[key]` — position-independent;
* `show:() => ui.camStockIndexed.checked` on the grid row is a predicate on the
  element, so it keeps working across the move;
* `uc.setMode()` still owns each row's mode visibility.

Re-parenting therefore keeps upstream's behaviour exactly, with none of it
reimplemented — the same reasoning as ui/cam_export.py driving the tool's own
hidden button rather than duplicating what it does.
"""

from __future__ import annotations

from ui.cam_grid import MOVED_ID as _GRID_ROW_ID

#: Prefix for the id given to the moved row, also the "already done" check so
#: a re-run cannot move it twice.
GROUP_ID = "piccola-rotary"

#: There is no heading any more. A captioned group made sense while this held a
#: switch and its sub-option; with the sub-option gone (**show grid** applies in
#: both modes since patch 0010 and is lifted separately by ui/cam_grid.py) a
#: heading over one checkbox is a row of furniture around a single control. The
#: switch says what it is instead — see ui/cam_labels.py, which words it
#: "rotary mode".

#: `api.ui` keys to lift into the group, in the order they should appear.
#:
#: Just the switch now: **show grid** applies in both modes since patch 0010 and
#: is lifted to the top of the panel by ui/cam_grid.py, so nesting it here would
#: say it were rotary-only.
#:
#: Each moved row is given the id ``<GROUP_ID>-<key>`` so it can be addressed
#: from outside; ui/cam_labels.py uses that to reword the switch.
MOVED_UI_KEYS = ("camStockIndexed",)


def move_rotary_switch_js() -> str:
    """JS lifting the rotary switch to the top of the left panel."""
    import json

    return """
(function () {
  var GROUP = %(group)s, KEYS = %(keys)s, GRID_ROW = %(grid_row)s;

  function rowOf(el) {
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) return n;
    }
    return null;
  }

  function place() {
    var api = window.kiri && window.kiri.api;
    var ui = api && api.ui;
    // built with the rest of the CAM menu, well after the kiri namespace
    // appears, so absence here means "not yet", not "gone".
    if (!ui || !ui.camStockIndexed) return false;
    var panel = document.getElementById('panel-slide');
    if (!panel) return false;

    var row = document.getElementById(GROUP + '-' + KEYS[0]) ||
              rowOf(ui[KEYS[0]]);
    if (!row) return false;
    // Stable id so the row can be addressed from outside — ui/cam_labels.py
    // hangs the wording on it. Upstream gives these rows no id of its own.
    row.id = GROUP + '-' + KEYS[0];

    // No group and no heading: one checkbox does not need furniture around it.
    // It goes under the grid switch, which ui/cam_grid.py keeps at the very
    // top — asking for that position explicitly rather than both modules
    // racing for panel.firstChild.
    var grid = document.getElementById(GRID_ROW);
    var before = grid ? grid.nextSibling : panel.firstChild;
    // Comparing only nextSibling is not enough: a row that is the last child
    // of some other group has a null nextSibling, and so does the target slot
    // when the grid row is last in the panel — null === null would read as
    // "already in place" and the row would never move. The parent has to agree
    // as well.
    // `before === row` means it already occupies the slot: inserting a node
    // before itself is at best a no-op and at worst a reorder, so say so.
    // Lifted clear of its group, so it has to leave the group behind as well.
    // Kiri collapses by tagging each row with `_group` when it is built and
    // then hiding whatever matches a collapsed name --
    // `hidn = hidden[el._group] === true` in inputs.js `setMode` -- which is a
    // property of the element, not of where it sits. Both rows are created
    // under `newGroup(LANG.cs_menu, ..., group:"cam-stock")`, so at the top of
    // the panel they still answered to the Stock header and disappeared with
    // it. Cleared on every pass, since the row is re-placed on several events.
    row._group = undefined;
    if (before !== row &&
        (row.parentNode !== panel || row.nextSibling !== before)) {
      panel.insertBefore(row, before);
    }
    return true;
  }

  function install() {
    var api = window.kiri && window.kiri.api;
    if (!api || !api.event || !place()) return false;
    // re-assert: the grid switch settles into place on the same events, and
    // the panel is redrawn on several of them
    ['settings.saved', 'boolean.update', 'widget.add', 'platform.layout']
      .forEach(function (ev) {
        try { api.event.on(ev, place); } catch (e) { }
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
        "group": json.dumps(GROUP_ID),
        "keys": json.dumps(list(MOVED_UI_KEYS)),
        "grid_row": json.dumps(_GRID_ROW_ID),
    }
