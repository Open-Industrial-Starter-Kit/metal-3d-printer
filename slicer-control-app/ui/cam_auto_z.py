"""
ui/cam_auto_z.py
Fills each CAM operation's *Z Top* and *Z Bottom* from the imported model, so
the job is bounded by the part instead of by the stock.

Why
---
An operation ships with ``ov_topz`` and ``ov_botz`` at ``0``, and ``0`` means
*unset* -- the slicer falls back to the stock (``work/slice.js``)::

    top_z    = camZTop    ? bottom_stock + camZTop    : top_stock;
    ...
    workover.top_z    = isIndexed ? ov_topz : bottom_stock + ov_topz;
    workover.bottom_z = isIndexed ? ov_botz : bottom_stock + ov_botz;

So a fresh operation cuts the whole block, and bounding it to the part means
opening *Top & Bottom*, clicking the Z-plane selector, and picking the height off
the model in the viewport -- once per operation, every time a model is imported.
Nothing about those two numbers is a judgement call: the top of the part and the
bottom of the part are facts about the mesh that was just loaded.

The arithmetic
--------------
Both fields are measured UP FROM THE BOTTOM OF THE STOCK, not in world Z, which
is the one thing that makes them easy to get wrong. From the same file::

    zbOff       = isIndexed ? (stock.z - bounds.dim.z) / 2 : (wztop - track.box.d)
    bottom_stock = -zbOff

Setting ``workover.top_z`` to the top of the part means ``bottom_stock + ov_topz
== bounds.max.z``, hence::

    ov_topz = bounds.max.z + zbOff
    ov_botz = bounds.min.z + zbOff

Checked against a hand-picked value rather than derived and hoped for: on the
sphere sample -- stock 41 x 41 x 21.5, ``track.top`` 21, ``track.box.d`` 20.5,
model Z 0 .. 20.5 -- this gives ``ov_topz`` 21.000 and ``ov_botz`` 0.500, and 21
is exactly what the Z-plane selector produces when the top face is clicked. The
0.5 is the stock that sits below the part.

What is left alone
------------------
Only a field still at ``0`` is filled. A number that is already there was either
put there by hand or filled by an earlier run, and either way it is the
operator's; this never overwrites one. A measured value of exactly ``0`` is not
written either -- it is the unset value, and it already means "the bottom of the
stock", which is where a part resting on the stock floor is anyway. (Treating
that no-op as a change once saved the settings in a loop and hung the CAM tab
on load.) Operation types without the two fields
(the ops-chain clock, ``loop``, ``index``) are skipped, and so is the whole pass
while there is no stock or no model to measure.

Values are rounded to three decimals. The selector stores what the raycaster
returns, so a hand-picked top face arrives as 20.999999999999996; writing that
number back into a field the operator reads is needless noise.
"""

from __future__ import annotations

import json

#: Operation fields this fills, and the value Kiri treats as "unset".
Z_TOP_FIELD = "ov_topz"
Z_BOT_FIELD = "ov_botz"
UNSET = 0

#: Operation types that carry no Z window: the end-of-chain clock and the two
#: structural ops.
SKIP_TYPES = ("|", "loop", "index")

#: Decimal places. See the module docstring on why this is not left raw.
ROUND_DP = 3

#: When to run. ``widget.add`` is the import itself; the rest catch an operation
#: added after the model, and a model swapped under an existing operation list.
EVENTS = ("widget.add", "widget.delete", "init-done", "settings.saved",
          "slice.begin")


def auto_z_js() -> str:
    """JS filling each operation's Z window from the imported model."""
    return """
(function () {
  var TOP = %(top)s, BOT = %(bot)s, UNSET = %(unset)s;
  var SKIP = %(skip)s, DP = %(dp)s, EVENTS = %(events)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function ops() {
    var a = api();
    try {
      var o = a.conf.get().process.ops;
      return Array.isArray(o) ? o : null;
    } catch (e) { return null; }
  }

  function round(v) {
    var m = Math.pow(10, DP);
    return Math.round(v * m) / m;
  }

  // The part's Z extent expressed in the frame the two fields use: up from the
  // bottom of the stock. See the module docstring for where zbOff comes from.
  function partZ() {
    var a = api();
    var widgets;
    try { widgets = a.widgets.all(); } catch (e) { return null; }
    if (!widgets || !widgets.length) return null;
    var lo = null, hi = null;
    for (var i = 0; i < widgets.length; i++) {
      var w = widgets[i];
      if (!w || !w.track || !w.track.box || !w.getBoundingBox) continue;
      var b;
      try { b = w.getBoundingBox(); } catch (e) { continue; }
      if (!b || !b.min || !b.max) continue;
      var zbOff = w.track.top - w.track.box.d;
      var l = b.min.z + zbOff, h = b.max.z + zbOff;
      lo = (lo === null) ? l : Math.min(lo, l);
      hi = (hi === null) ? h : Math.max(hi, h);
    }
    if (lo === null || hi === null || !(hi > lo)) return null;
    return { bottom: round(lo), top: round(hi) };
  }

  function hasStock() {
    var a = api();
    try {
      var s = a.conf.get().stock;
      return !!(s && s.x && s.y && s.z);
    } catch (e) { return false; }
  }

  // Re-entrancy guard: conf.save() below raises settings.saved and re-renders
  // the op list, and both of those call apply() again.
  var applying = false;

  function apply() {
    if (applying) return;
    var list = ops();
    if (!list || !hasStock()) return;
    var z = partZ();
    if (!z) return;
    var changed = false;
    for (var i = 0; i < list.length; i++) {
      var op = list[i];
      if (!op || Array.isArray(op) || !op.type) continue;
      if (SKIP.indexOf(op.type) >= 0) continue;
      // Only an operation that actually carries the two fields; everything
      // else would gain a pair of properties that nothing reads.
      if (op[TOP] === undefined || op[BOT] === undefined) continue;
      // Never overwrite a value that is already there -- see the docstring.
      // And only count a write that changes something: a part resting on the
      // floor of the stock has a bottom of exactly 0, which IS the unset
      // value, so "filling" it is a no-op. Counting it as a change saved the
      // settings, which raised settings.saved and redrew the op list, which
      // ran this again -- forever, and the CAM tab never finished loading.
      if (op[TOP] === UNSET && z.top !== UNSET) { op[TOP] = z.top; changed = true; }
      if (op[BOT] === UNSET && z.bottom !== UNSET) { op[BOT] = z.bottom; changed = true; }
    }
    if (changed) {
      var a = api();
      applying = true;
      try { a.conf.save(); } catch (e) { }
      finally { applying = false; }
    }
  }

  function install() {
    var a = api();
    if (!a || !a.event || !a.conf || !a.widgets) return false;
    if (a.__piccolaAutoZ) return true;
    a.__piccolaAutoZ = true;
    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, apply); } catch (e) { }
    });
    // An operation added after the model arrives does not raise any of those,
    // and the op list is rebuilt wholesale when it changes -- so watch it, the
    // same way ui/cam_op_names.py does.
    try {
      var listel = document.getElementById('oplist');
      if (listel) {
        new MutationObserver(apply).observe(listel, { childList: true });
      }
    } catch (e) { }
    apply();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "top": json.dumps(Z_TOP_FIELD),
        "bot": json.dumps(Z_BOT_FIELD),
        "unset": json.dumps(UNSET),
        "skip": json.dumps(list(SKIP_TYPES)),
        "dp": json.dumps(ROUND_DP),
        "events": json.dumps(list(EVENTS)),
    }
