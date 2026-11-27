"""
ui/cam_undo.py
Ctrl-Z / Ctrl-Shift-Z for placing a part in Kiri:Moto's workspace.

Why there was none
------------------
Two separate reasons, and the second is the surprising one.

Kiri's keyboard handler (``app/keyboard.js``) has cases for the arrow keys,
``a`` (select all), ``s`` (save) and ``l`` (restore). There is no ``z`` case at
all, so Ctrl-Z was never bound to anything.

And there was nothing to bind it to. ``app/do.js`` is a complete undo/redo
stack — a move/rotate/scale action pair pushed per edit, ``api.doit.undo`` and
``api.doit.redo``, an alert when the stack runs out — but **no module imports
it**. Nothing loads the file, so it never runs: ``api.doit.undo`` stays the
``noop`` that ``app/api.js`` declares (with a ``// do.js`` comment pointing at
the file that was supposed to replace it), and the ``#undo`` / ``#redo`` /
``#doit`` elements it binds to on ``load-done`` do not exist in
``web/kiri/index.html`` either. Dead code, wired to nothing, in 4.7.2.

What this does
--------------
Reimplements it from outside, because everything it needs is already public:
the transform events carry their own deltas, ``api.selection.widgets()`` hands
back the real widget objects, and those carry the same ``_move`` / ``rotate`` /
``_scale`` methods ``do.js`` called. So no patch — ``vendor/`` stays untouched
and the upstream file can come back to life on an upgrade without colliding.

The recorded semantics are ``do.js``'s, deliberately, rather than an
invention of ours:

* **rotate** — inverse is the negated angles.
* **scale** — inverse is the reciprocals, ignoring anything within 1e-4 of 1
  (Kiri fires a scale event on every keystroke in the size fields).
* **move** — accumulated from ``selection.drag`` and committed as one action on
  ``mouse.drag.done``. A drag emits a move event per mouse-move, so recording
  those directly would bury the stack in hundreds of one-pixel steps; this is
  why ``do.js`` accumulates, and why it is copied rather than simplified.

Undo replays through the widget methods (``w._move`` and friends) exactly as
``do.js`` did. Those do not emit selection events, so an undo cannot record
itself as a new action — the re-entrancy guard is structural rather than a
flag.

What it does not cover
----------------------
The same things ``do.js`` did not, called out because "undo" invites the
assumption of everything:

* **Only part placement.** Move, rotate, scale of what is on the platform.
  Adding, deleting or editing a CAM *operation* is not undoable, nor are stock,
  origin, tool or settings changes.
* **Typed moves are not recorded**, only dragged ones — the X/Y/Z position
  fields go through the same event a drag does, with no way to tell one
  keystroke's worth from another's.
* **The stack is cleared** when a widget is added or deleted, or the platform
  is laid out, because the actions hold widget references that those events
  invalidate. Upstream cleared it for the same reason.
* **Arrange view only**, which is the only view where the transforms it undoes
  can be made in the first place.

A text field keeps its own native undo: the shortcut is ignored while an input
or textarea has focus, so Ctrl-Z in the file-name box still edits text.
"""

from __future__ import annotations

import json

#: Set on `window` so a re-run cannot install a second set of listeners — the
#: script is injected at document creation and the page is never reloaded
#: in-place, but a Kiri mode switch re-runs the injection.
INSTALLED_FLAG = "__piccolaUndoInstalled"

#: Scale events land on every keystroke in the size fields; anything this close
#: to unity is not an edit worth recording. Upstream's own threshold (do.js).
SCALE_EPSILON = 1e-4

#: How long the "nothing to undo" style alerts stay up, in ms.
ALERT_MS = 1500

#: Messages, matching upstream's wording where it had any.
MSG_NOTHING_UNDO = "nothing to undo"
MSG_NOTHING_REDO = "nothing to redo"


def undo_js() -> str:
    """JS installing the undo/redo stack and its keyboard shortcuts."""
    return """
(function () {
  var FLAG = %(flag)s, SCALE_EPS = %(eps)s, ALERT_MS = %(alert_ms)s;
  var MSG_UNDO = %(msg_undo)s, MSG_REDO = %(msg_redo)s;

  if (window[FLAG]) return;

  // stack of { undo, redo } action records, and the cursor into it
  var stack = [], stpos = 0;
  // drag deltas accumulate here until the drag ends (see the module docstring)
  var moved = { x: 0, y: 0 };
  var msgid = null;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function arranging() {
    var a = api();
    try { return !!a.view.is_arrange(); } catch (e) { return false; }
  }
  function widgets() {
    var a = api();
    try { return a.selection.widgets(true) || []; } catch (e) { return []; }
  }
  function redraw() {
    var a = api();
    try { if (a.const.SPACE.update) a.const.SPACE.update(); } catch (e) { }
  }
  function message(txt) {
    var a = api();
    try {
      if (msgid) a.hide.alert(msgid);
      msgid = a.show.alert(txt, ALERT_MS);
    } catch (e) { }
  }

  function push(rec) {
    stack.length = stpos++;      // drop any redo future
    stack.push(rec);
  }

  function clear() {
    stack = [];
    stpos = 0;
    moved = { x: 0, y: 0 };
  }

  // Replayed through the widget methods rather than through api.selection,
  // because those do not emit selection events — so applying an action cannot
  // record itself as a new one.
  function apply(rec) {
    var a = api();
    if (!rec || !rec.widgets) return;
    for (var i = 0; i < rec.widgets.length; i++) {
      var w = rec.widgets[i];
      if (!w) continue;
      try {
        if (rec.type === 'move') w._move(rec.dist.x, rec.dist.y, 0);
        else if (rec.type === 'rotate') w.rotate(rec.x, rec.y, rec.z);
        else if (rec.type === 'scale') w._scale(rec.x, rec.y, rec.z);
      } catch (e) { }
    }
    if (rec.type === 'scale') {
      try { a.selection.update_info(); } catch (e) { }
    }
    redraw();
  }

  function undo() {
    if (!arranging()) return;
    if (stpos > 0) apply(stack[--stpos].undo);
    else message(MSG_UNDO);
  }

  function redo() {
    if (!arranging()) return;
    if (stpos < stack.length) apply(stack[stpos++].redo);
    else message(MSG_REDO);
  }

  /* ---- recording ------------------------------------------------------- */

  function install() {
    var a = api();
    if (!a || !a.event || !a.selection || !a.view) return false;

    a.event.on('selection.rotate', function (rec) {
      if (!rec) return;
      var x = rec.x || 0, y = rec.y || 0, z = rec.z || 0;
      if (!(x || y || z)) return;
      var w = widgets();
      if (!w.length) return;
      push({
        redo: { type: 'rotate', widgets: w, x: x, y: y, z: z },
        undo: { type: 'rotate', widgets: w, x: -x, y: -y, z: -z }
      });
    });

    a.event.on('selection.scale', function (rec) {
      if (!rec) return;
      var x = rec[0], y = rec[1], z = rec[2];
      if (!(x && y && z)) return;
      // the size fields fire on every keystroke; only real changes count
      if (Math.abs(1 - (x * y * z)) < SCALE_EPS) return;
      var w = widgets();
      if (!w.length) return;
      push({
        redo: { type: 'scale', widgets: w, x: x, y: y, z: z },
        undo: { type: 'scale', widgets: w, x: 1 / x, y: 1 / y, z: 1 / z }
      });
    });

    // A drag emits a move per mouse-move, so it is accumulated and committed
    // once, when the drag ends.
    a.event.on('selection.drag', function (delta) {
      if (!delta) return;
      moved.x += delta.x || 0;
      moved.y += delta.y || 0;
    });

    a.event.on('mouse.drag.done', function () {
      if (!moved.x && !moved.y) return;
      var w = widgets();
      var dist = { x: moved.x, y: moved.y };
      moved = { x: 0, y: 0 };
      if (!w.length) return;
      push({
        redo: { type: 'move', widgets: w, dist: dist },
        undo: { type: 'move', widgets: w, dist: { x: -dist.x, y: -dist.y } }
      });
    });

    // The actions hold widget references, which these invalidate.
    a.event.on('widget.add', clear);
    a.event.on('widget.delete', clear);
    a.event.on('platform.layout', clear);

    /* ---- the shortcut Kiri never had --------------------------------- */

    document.addEventListener('keydown', function (ev) {
      if (!(ev.metaKey || ev.ctrlKey) || ev.altKey) return;
      var key = (ev.key || '').toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      // a text field keeps its own undo
      var el = document.activeElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
                 el.isContentEditable)) return;
      if (!arranging()) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (key === 'y' || ev.shiftKey) redo(); else undo();
    }, true);   // capture: ahead of Kiri's own document handler

    window[FLAG] = true;
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "flag": json.dumps(INSTALLED_FLAG),
        "eps": repr(SCALE_EPSILON),
        "alert_ms": repr(ALERT_MS),
        "msg_undo": json.dumps(MSG_NOTHING_UNDO),
        "msg_redo": json.dumps(MSG_NOTHING_REDO),
    }
