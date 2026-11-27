"""
ui/cam_scale_live.py
Applies the Scale / Size panel as it is typed, instead of only on Enter.

What it was
-----------
``selected.js:547`` binds the six fields and says so in a comment::

    // on enter but not on blur
    space.event.onEnterKey([
        ui.scale.X, (e) => input_scale(e, ui),
        ...
        ui.size.Z,  (e) => input_resize(e, ui),
    ]);

    // on enter and blur
    space.event.onEnterKey([ ui.toolName, updateTool, ... ], true);

So the tool fields commit when you leave them and the scale fields do not. Type
a size, click into the viewport to look at what you did, and nothing happened --
the number is sitting in the box, the part is the size it always was, and there
is nothing on screen to say which of the two is the truth. That is worse than a
field that simply needs Enter, because the panel is showing a value the model
does not have.

What it is now
--------------
The same commit, on three triggers instead of one: Enter (untouched, Kiri's own
listener), leaving the field, and a pause in typing.

Why a pause rather than every keystroke: the handlers are *relative*. Each one
reads ``e.target.was``, divides the new value by it, applies the ratio and
writes ``was`` back. Applying every keystroke is arithmetically harmless --
80/8 x 8/40 is 80/40, so the ratios compose back to the right answer -- but it
is not harmless to watch, and one intermediate is genuinely bad: typing "0" on
the way to "0.5" gives a ratio of zero, which ``input_scale`` rejects with an
"invalid scale value" alert. A pause skips every intermediate rather than
having to reason about which ones are safe.

How the commit is made
----------------------
By dispatching the event Kiri is already listening for -- ``keyup`` with
``keyCode === 13`` (``moto/space.js:506``) -- rather than by reaching for
``input_scale``, which is module-private and not on the api.

``keyCode`` is defined on the event with ``defineProperty`` instead of being
passed to the constructor. Chromium does accept it in ``KeyboardEventInit``, but
that is a legacy extension rather than something the spec promises, and this is
the one property the whole mechanism turns on.

What this deliberately does not do
----------------------------------
It does not refresh the panel afterwards. Both handlers already write every
field of both rows, so ``was`` is correct for the next edit without help --
checked against stock Kiri on a clean profile, a size edit followed by a scale
edit lands on the same number either way. An earlier version of this called
``api.selection.update_info()`` after each commit to "fix" a drift that turned
out to be test-profile contamination, not a defect.

The caret
---------
The handlers write to all three fields of their row, the typed one included, so
a commit mid-edit sets ``value`` on the element the caret is in and Chrome
collapses the selection to the end. The position is saved and restored when the
value came back unchanged, which is the case whenever the user typed a plain
number.
"""

from __future__ import annotations

import json

#: How long typing has to stop before the value is applied. Long enough to type
#: "0.5" without the "0" being committed on its own, short enough that it feels
#: like the field is live rather than delayed.
SETTLE_MS = 450

#: The six fields, by the ids ``init/menu.js`` builds them with.
FIELD_IDS = ("size_x", "size_y", "size_z", "scale_x", "scale_y", "scale_z")

#: Marks a field as wired, so a second run is a no-op.
DONE_ATTR = "data-piccola-live-scale"


def scale_live_js() -> str:
    """JS committing the Scale / Size fields on pause and on blur."""
    js = """
(function () {
  var IDS = %(ids)s, SETTLE = %(settle)s, DONE = %(done)s;
  var timer = null, pending = null;

  function clear() {
    if (timer) { clearTimeout(timer); timer = null; }
    pending = null;
  }

  // A value worth committing. Empty is what a field looks like mid-edit, and
  // zero or negative is what it looks like on the way to "0.5" -- the handlers
  // reject both, one of them with an alert, so neither is sent.
  function usable(el) {
    var raw = (el.value || '').trim();
    if (!raw) return false;
    var v = parseFloat(raw);
    return isFinite(v) && v > 0;
  }

  // Kiri listens for keyup with keyCode 13 (moto/space.js). keyCode is defined
  // here rather than passed to the constructor: Chromium honours it in the init
  // dict, but that is a legacy extension, and this is the single property the
  // whole thing depends on.
  function commit(el) {
    if (!usable(el)) return;
    var at = (document.activeElement === el) ? el.selectionStart : null;
    var before = el.value;
    var ev = new KeyboardEvent('keyup', { bubbles: true });
    try {
      Object.defineProperty(ev, 'keyCode', { get: function () { return 13; } });
    } catch (e) { return; }
    el.dispatchEvent(ev);
    // the handler rewrites every field of the row, this one included, which
    // drops the caret to the end of a field still being typed in
    if (at !== null && document.activeElement === el && el.value === before) {
      try { el.setSelectionRange(at, at); } catch (e) { }
    }
  }

  function wire(el) {
    el.addEventListener('input', function () {
      clear();
      pending = el;
      timer = setTimeout(function () {
        timer = null;
        var target = pending;
        pending = null;
        if (target) commit(target);
      }, SETTLE);
    });
    // leaving the field commits it now rather than after the pause, so the
    // value in the box and the model always agree once focus has gone
    el.addEventListener('blur', function () {
      var had = (pending === el);
      clear();
      if (had) commit(el);
      // a field left empty or at zero was never applied, so it is showing a
      // number the model does not have. Put back the last committed one.
      if (!usable(el) && el.was !== undefined && el.was !== null) {
        el.value = el.was;
      }
    });
    // Enter is Kiri's own binding and still fires; the pending timer would
    // otherwise commit the same edit a second time
    el.addEventListener('keyup', function (ev) {
      if (ev.keyCode === 13) clear();
    });
    el.setAttribute(DONE, '1');
  }

  // Counting what is wired, not what is left: injected at document creation,
  // where nothing exists yet, a count of outstanding work reads as "finished"
  // on the first tick and the poll stops before the panel is ever built.
  function attach() {
    var done = 0;
    for (var i = 0; i < IDS.length; i++) {
      var el = document.getElementById(IDS[i]);
      if (!el) continue;
      if (!el.getAttribute(DONE)) wire(el);
      done++;
    }
    return done >= IDS.length;
  }

  var tries = 400;
  (function wait() {
    var ok = false;
    try { ok = attach(); } catch (e) { }
    if (ok) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "ids": json.dumps(list(FIELD_IDS)),
        "settle": json.dumps(SETTLE_MS),
        "done": json.dumps(DONE_ATTR),
    }
    assert "%" not in js, "unescaped % survived formatting in scale_live_js()"
    return js
