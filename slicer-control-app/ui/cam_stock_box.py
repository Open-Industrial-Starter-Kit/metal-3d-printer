"""
ui/cam_stock_box.py
A show/hide switch for the stock outline — the pale box drawn around the job.

Why it wants switching off
--------------------------
In *Arrange* the outline is the only thing showing where the blank is, so it
earns its place. In *Animate* the simulated stock is drawn solid in the same
space, and the outline becomes a wireframe box sitting over the thing it was
describing — white lines across the part, over the toolpath, and through the
cutter. Nothing is wrong with it; there are just two drawings of one object.

What it is
----------
``cl-stock.js`` builds it as ``env.camStock``: a barely-there grey box
(``opacity: 0.05``) carrying a ``LineSegments`` child at ``0xaaaaaa``, which is
the part actually visible. Both are hidden together — hiding the lines alone
would leave a faint grey pane behind, which is the same clutter with less of the
information.

Finding it without `env`
------------------------
``env`` is module-private, so the object is found in the scene instead. The
handle is the property Kiri hangs on it for its own use::

    env.camStock.lines = lines;

A mesh carrying a ``lines`` child that is a ``LineSegments`` is the stock box
and nothing else — checked against a live scene: exactly one match, scaled
105 x 131 x 30, which is the stock. Matching on that rather than on the colours
means a restyle upstream does not quietly break this.

Re-applying
-----------
Kiri rebuilds ``camStock`` from scratch whenever the stock changes — a new
offset, a different shape, a reloaded model — and the replacement is visible.
So the choice is re-applied on a timer rather than set once. The timer is cheap
by construction: the found object is cached, and while that cache is still in
the scene and already in the right state the tick does nothing. A full traverse
only happens when the box has actually been replaced.
"""

from __future__ import annotations

import json

#: The view bar this adds a button to — ``ui/cam_views.py``'s.
BAR_ID = "piccola-view-bar"
BTN_ID = "piccola-stock-box-btn"

#: Class marking the button lit, matching the rest of the bar.
ON_CLASS = "piccola-view-on"

#: Button text and hover text. The label names the thing, not the action, so it
#: reads the same whichever state it is in — the lit/unlit state says which.
BTN_LABEL = "stock box"
BTN_TITLE = "show or hide the stock outline"

#: Where the choice is remembered. Per browser profile, like Kiri's own
#: preferences, so it survives a relaunch.
STORE_KEY = "piccola-stock-box"

#: How often the choice is re-asserted. Kiri replaces the box on any stock
#: change and the replacement arrives visible; see the module notes for why
#: this costs almost nothing.
INTERVAL_MS = 700


def css() -> str:
    """Styling for the button."""
    return f"#{BTN_ID} {{ width: 100% !important; }}"


def stock_box_js() -> str:
    """JS adding a show/hide switch for the stock outline."""
    js = """
(function () {
  var BAR = %(bar)s, BTN = %(btn)s, ON = %(on)s;
  var LABEL = %(label)s, TITLE = %(title)s;
  var KEY = %(key)s, EVERY = %(every)s;

  var shown = true, box = null;

  function api() {
    try { return window.kiri.api; } catch (e) { return null; }
  }

  function load() {
    try {
      var v = localStorage.getItem(KEY);
      // absent means "never chosen", and the outline is upstream's default
      if (v !== null) shown = v !== '0';
    } catch (e) { }
  }

  function save() {
    try { localStorage.setItem(KEY, shown ? '1' : '0'); } catch (e) { }
  }

  function root() {
    var a = api();
    try {
      // the platform is in the scene whether or not anything is loaded, so
      // this works before a model arrives
      var o = a.space.internals().platform;
      while (o.parent) o = o.parent;
      return o;
    } catch (e) { return null; }
  }

  // The mesh Kiri hangs its outline off. See the module notes for why this is
  // matched on structure rather than on colour.
  function find() {
    var r = root();
    if (!r) return null;
    var hit = null;
    r.traverse(function (o) {
      if (!hit && o.isMesh && o.lines && o.lines.type === 'LineSegments') hit = o;
    });
    return hit;
  }

  function paint() {
    var b = document.getElementById(BTN);
    if (b) b.classList.toggle(ON, shown);
  }

  // Cheap by design: only re-finds when the cached box has left the scene,
  // which happens when Kiri rebuilds it after a stock change.
  function apply() {
    if (!box || !box.parent) box = find();
    if (!box) return;
    if (box.visible === shown) return;
    box.visible = shown;
    var a = api();
    try { a.space.update(); } catch (e) { }
  }

  function toggle() {
    shown = !shown;
    save();
    paint();
    box = box && box.parent ? box : find();
    apply();
  }

  function install() {
    var bar = document.getElementById(BAR);
    if (!bar) return false;
    if (document.getElementById(BTN)) return true;
    var b = document.createElement('button');
    b.id = BTN;
    b.textContent = LABEL;
    b.setAttribute('title', TITLE);
    b.onclick = toggle;
    bar.appendChild(b);
    paint();
    setInterval(function () { try { apply(); } catch (e) { } }, EVERY);
    return true;
  }

  load();

  var tries = 600;
  (function wait() {
    var ok = false;
    try { ok = install(); } catch (e) { }
    if (ok) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "bar": json.dumps(BAR_ID),
        "btn": json.dumps(BTN_ID),
        "on": json.dumps(ON_CLASS),
        "label": json.dumps(BTN_LABEL),
        "title": json.dumps(BTN_TITLE),
        "key": json.dumps(STORE_KEY),
        "every": json.dumps(INTERVAL_MS),
    }
    assert "%" not in js, "unescaped % survived formatting in stock_box_js()"
    return js
