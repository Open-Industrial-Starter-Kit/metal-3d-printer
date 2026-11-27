"""
ui/cam_no_ops.py
Warn, in a message box, when Generate or Animate is pressed with no operations
to run.

Why
---
With an empty operation list Kiri still starts a slice -- Generate directly,
Animate through its own ``prepare()`` first. The worker gives up inside
``mode/cam/work/slice.js`` (``'no processes specified'``) and the only trace is
a brief status line, so the button looks like it did nothing -- or like it
broke. A modal that says why, with nothing started, is the honest answer.

What counts as "no operations"
------------------------------
Not simply ``process.ops.length === 0``. The list always carries a *clock* entry
(``type: '|'``, the thin bar under *Operation List*), added by ``init-ui.js``
whenever it is missing, and ``slice.js`` stops at it: everything below the bar
is skipped. Disabled operations (``op.disabled``) are skipped as well. So the
job is empty when nothing *enabled* sits *above the bar* -- which is what
:func:`no_ops_js` counts. That also catches the less obvious case of a list
whose only operations have all been dragged below the bar or switched off.

How
---
``api.function.slice`` and ``api.function.animate`` are wrapped once, after
Kiri's api exists. Every way in looks the function up at the moment it fires
-- the buttons (``init/build.js``: ``'act-slice': stop(() =>
api.function.slice())``, and the same for ``'act-animate'``) and the keyboard
shortcuts (``keyboard.js``: ``this.#api.function.slice()`` / ``.animate()``) --
so the wrappers see all of them. When the job is empty they show Kiri's own
modal, ``api.uc.alert``, and return without starting anything; otherwise they
call straight through with the original arguments.

Wrapping ``animate`` rather than ``prepare`` is deliberate: Animate reaches its
slice through the module-local ``prepareSlices``, out of reach, but it enters
through ``api.function.animate`` -- and stopping it there keeps the view from
switching to an empty animation as well.

Export is not guarded; it runs its own ``prepare()``.
"""

from __future__ import annotations

import json

#: The message in the box.
MESSAGE = "There are no operations"

#: When there are operations above the bar but every one is switched off, the
#: box says why -- "no operations" over a list full of them reads as a bug.
#: Kiri itself switches *loop*, *lathe* and the rotary roughing / contour ops
#: (patches 0045, 0046) off on a flat (non-rotary) job, and every operation inside a loop with
#: them (``init-ui.js``: ``!env.isIndexed && (type === 'loop' || rec._numloop)``
#: / ``type === 'lathe'`` ...).
MESSAGE_ROTARY = ("There are no operations to run. Loop and the rotary operations work "
                  "only with Rotary Mode on, so on a flat job they are switched off, and "
                  "so are the operations inside a loop.")
MESSAGE_ALL_OFF = ("There are no operations to run: every operation is switched off. "
                   "Ctrl+click an operation to switch it back on.")

#: Kiri's clock-divider operation type. `slice.js` ignores everything after it.
CLOCK_TYPE = "|"

#: Guard so a reload of the injected script cannot wrap the wrapper.
WRAPPED_FLAG = "__piccolaNoOps"

#: The ``api.function`` entries guarded: Generate and Animate.
GUARDED = ("slice", "animate")


def no_ops_js() -> str:
    """JS wrapping :data:`GUARDED` with the empty-job check."""
    js = """
(function () {
  var MESSAGE = %(message)s, CLOCK = %(clock)s, FLAG = %(flag)s;
  var MESSAGE_ROTARY = %(message_rotary)s, MESSAGE_ALL_OFF = %(message_all_off)s;
  var GUARDED = %(guarded)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  // Enabled operations above the clock bar: exactly what slice.js would run.
  function runnableOps(a) {
    var ops;
    try { ops = a.conf.get().process.ops; } catch (e) { return -1; }
    if (!Array.isArray(ops)) return -1;
    var n = 0;
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (!op || Array.isArray(op)) continue;
      if (op.type === CLOCK) break;
      if (!op.disabled) n++;
    }
    return n;
  }

  // Why nothing would run: an empty list, rotary-only operations on a flat
  // job, or everything switched off.
  function whyNone(a) {
    var ops = [], indexed = false;
    try { ops = a.conf.get().process.ops || []; } catch (e) { }
    try { indexed = !!a.conf.get().process.camStockIndexed; } catch (e) { }
    var listed = 0, rotary = false;
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (!op || Array.isArray(op)) continue;
      if (op.type === CLOCK) break;
      listed++;
      if (op.type === 'loop' || op.type === 'lathe' || op.type === 'rotaryrough' ||
          op.type === 'surfacerough' || op.type === 'rotarycontour' ||
          op.type === 'rotarycutoff' || op._numloop) rotary = true;
    }
    if (!listed) return MESSAGE;
    if (rotary && !indexed) return MESSAGE_ROTARY;
    return MESSAGE_ALL_OFF;
  }

  function wrapOne(fns, name) {
    if (typeof fns[name] !== 'function') return false;
    if (fns[name][FLAG]) return true;
    var original = fns[name];
    var guarded = function () {
      var b = api();
      // only in CAM, and only when the list could be read: -1 means "unknown",
      // and an unknown must never block a slice
      var cam = false;
      try { cam = b.mode.is_cam(); } catch (e) { }
      if (cam && runnableOps(b) === 0) {
        var msg = whyNone(b);
        try { b.uc.alert(msg); } catch (e) { window.alert(msg); }
        return;
      }
      return original.apply(this, arguments);
    };
    guarded[FLAG] = true;
    fns[name] = guarded;
    return true;
  }

  function wrap() {
    var a = api();
    if (!a || !a.function) return false;
    var done = true;
    GUARDED.forEach(function (name) {
      if (!wrapOne(a.function, name)) done = false;
    });
    return done;
  }

  var tries = 600;
  (function wait() {
    if (wrap()) return;
    if (tries-- > 0) setTimeout(wait, 100);
  })();
})();
""" % {
        "message": json.dumps(MESSAGE),
        "message_rotary": json.dumps(MESSAGE_ROTARY),
        "message_all_off": json.dumps(MESSAGE_ALL_OFF),
        "clock": json.dumps(CLOCK_TYPE),
        "flag": json.dumps(WRAPPED_FLAG),
        "guarded": json.dumps(list(GUARDED)),
    }
    assert "%" not in js, "unescaped % survived formatting in no_ops_js()"
    return js
