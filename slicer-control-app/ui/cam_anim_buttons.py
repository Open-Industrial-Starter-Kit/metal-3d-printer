"""
ui/cam_anim_buttons.py
Withdraws three of the four view buttons on the animation bar: two that do not
work, and one that does.

Why
---
The transport carries four view buttons on its right: *transparency*, *show
model*, *stock box* and *path*. *stock box* and *path* do not work, in either
mode, for four different reasons — which is why they look fine and behave like
dead pixels. *transparency* works and is withdrawn anyway, on request; that
half is recorded under "One that worked" below.

Which animator runs decides half of it (`init-ui.js`)::

    env.animVer = env.isIndexed ? 1 : 0;

A flat job gets `anim-2d.js`, a rotary one `anim-3d.js`, over the same row of
DOM buttons.

**On a flat job, path is never bound.** `button.path.onclick` is assigned in
`anim-3d.js` and nowhere else, and `init-menu.js` builds the button with
`newButton(null, null, …)` — a null action leaves `newButton`'s own onclick a
`switch (typeof action)` with no matching case. The button is wired to a
function that deliberately does nothing.

**On a flat job, stock box calls something that has already returned.**
`toggleStock` emits `cam.stock.toggle`; the single listener does
`env.showStock = bool ?? !env.showStock; updateStock()`, and `updateStock` opens
with `if (env.isAnimate) { …; return; }`. `env.showStock` is mentioned on two
lines in the whole CAM tree — one declares it, the other is that self-flip.
Nothing reads it. Both are *correct* for what a flat animation is: the stock is
not a box beside the job, it is the mesh being carved, and the toolpaths are the
stack layers whose checkboxes sit right above the transport.

**In rotary, the first click on either is a no-op.** Both call
`api.local.toggle(key)` with no value, and `localGetBoolean(key, def = true)`
answers `val === true || val === 'true' || val === def` — for an unset key,
false. So the first click computes `!false` and stores *true*, while the meshes
and the path lines are already visible. Measured against the real `local.js`:
unset → click → `true` → nothing changes; only the second click hides anything.

**And in rotary, the path button poisons its own state.** `initPathMesh` reads
`show = (api.local.get('cam.anim.path') ?? 'true') == 'true'`, a *string*
comparison, while `toggle` stores a boolean — and `true == 'true'` is false. So
once the button has been touched, every later animation starts with the paths
hidden and the button's own state saying they are shown.

On top of all that neither handler calls `space.update()`, and `moto/space.js`
renders only while `Date.now() - lastAction < 1500`, so on a paused animation
even a change that does happen may not be painted until the camera moves.

One that worked
---------------
*transparency* is a different case: it is bound in both animators and it does
what it says, switching `material.transparent` on the mesh being carved. It is
hidden because it was asked for, so :data:`WITHDRAWN_BUTTONS` is kept separate
from :data:`BROKEN_BUTTONS` — the two are hidden for reasons that would not
survive being merged into one list, and only the broken pair is governed by
:data:`ALWAYS_HIDE`.

Hiding it leaves the transparency *state* wherever the animator puts it, which
is upstream's default in both modes: `anim-2d.js` applies the stored
`cam.anim.trans` at setup and `conf/defaults.js` ships it `true`, so a flat
animation stays see-through, and `anim-3d.js` calls `toggleTrans(undefined,
false)` at setup, so a rotary one does not. That is what the app did before with
nobody touching the button, and it is worth knowing when wondering why the
carve is transparent with no control in sight.

`anim-3d.js` also *un-hides* this button itself on a manifold job::

    if (manifold) { anim.trans.classList.remove('hide'); }

which is why hiding it once is not enough. That line runs inside `animate2`
before it emits `animate`, and this module listens to `animate` — so the
re-assert lands after upstream's removal rather than racing it.

How
---
They are hidden, with the same `hide` class upstream uses a few lines away to
withdraw `anim.trans` on a non-manifold job, and re-applied on the events that
rebuild or re-enter the bar. The bar re-lays itself out around the gap, and
`ui/cam_anim_layers.py` measures the row rather than assuming its height, so the
checkbox row above follows without being told.

Hiding rather than fixing is a judgement about value, not difficulty. The rotary
faults are all reachable from outside — seed the two keys as the *strings*
`'true'` so the first click flips something real and `initPathMesh` agrees with
`getBoolean`, then wrap the two handlers to call `SPACE.update()` afterwards.
What that buys is hiding the stock and the path trace during a rotary
animation, which the layer checkboxes and *show model* already largely cover.
Two buttons that need four workarounds to earn their place did not earn it. To
restore them for rotary only, make :data:`ALWAYS_HIDE` false and apply the four
fixes above; the tests describe what each one is for.
"""

from __future__ import annotations

import json

#: `api.ui.anim` keys for the two that do not work, with the titles they carry
#: so they can be found on screen: *stock box* (`fa-cube`) and *path*
#: (`fa-draw-polygon`).
BROKEN_BUTTONS = ("shade", "path")

#: And the one that does work, withdrawn on request: *transparency*
#: (`fa-border-none`). Separate from the pair above because the reason is
#: different — this one is a choice, not a fault — and because
#: :data:`ALWAYS_HIDE` has nothing to say about it.
WITHDRAWN_BUTTONS = ("trans",)

#: Whether the broken pair is hidden in rotary as well as on a flat job. They
#: are broken in both, by different faults; see the module docstring before
#: changing this. Does not affect :data:`WITHDRAWN_BUTTONS`.
ALWAYS_HIDE = True

#: Kiri's own display:none class — what `anim-3d.js` uses to withdraw
#: `anim.trans` when the job is not manifold.
HIDE_CLASS = "hide"

#: The process flag `env.isIndexed` is assigned from, and therefore the one
#: that picks the animator. Only consulted when :data:`ALWAYS_HIDE` is false.
INDEXED_KEY = "camStockIndexed"

#: When to re-apply. `animate` is emitted by both animators once they have
#: finished binding; the rest cover entering the view and changing the flag.
EVENTS = ("animate", "view.set", "init-done", "boolean.update",
          "settings.saved")


def anim_buttons_js() -> str:
    """JS hiding the animation-bar buttons that do nothing."""
    return """
(function () {
  var BROKEN = %(broken)s, GONE = %(gone)s;
  var HIDE = %(hide)s, INDEXED = %(indexed)s;
  var EVENTS = %(events)s, ALWAYS = %(always)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function indexed() {
    var a = api();
    try { return !!a.conf.get().process[INDEXED]; } catch (e) { return false; }
  }

  function set(anim, keys, hidden) {
    for (var i = 0; i < keys.length; i++) {
      var b = anim[keys[i]];
      if (!b || !b.classList) continue;
      if (hidden) b.classList.add(HIDE); else b.classList.remove(HIDE);
    }
  }

  function apply() {
    var a = api();
    var anim = a && a.ui && a.ui.anim;
    if (!anim) return;
    // the broken pair works in neither mode; the flag is what a reader would
    // reach for first, so it stays wired up rather than being deleted
    set(anim, BROKEN, ALWAYS || !indexed());
    // and the one that works, which is not a mode question
    set(anim, GONE, true);
  }

  function install() {
    var a = api();
    if (!a || !a.event || !a.ui || !a.ui.anim) return false;
    var first = a.ui.anim[BROKEN[0]];
    if (!first) return false;
    // marked on the button, not on `api.ui.anim`: that object is upstream's
    // map of controls and a stray non-control key in it is someone else's
    // trap to fall into
    if (first.__piccolaAnimButtons) return true;
    first.__piccolaAnimButtons = true;
    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, apply); } catch (e) { }
    });
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
        "broken": json.dumps(list(BROKEN_BUTTONS)),
        "gone": json.dumps(list(WITHDRAWN_BUTTONS)),
        "hide": json.dumps(HIDE_CLASS),
        "indexed": json.dumps(INDEXED_KEY),
        "events": json.dumps(list(EVENTS)),
        "always": json.dumps(ALWAYS_HIDE),
    }
