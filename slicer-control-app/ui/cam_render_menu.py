"""
ui/cam_render_menu.py
Marks which render mode is on, in the menu that sets it.

Why
---
*render* offers solid, wireframe and transparent, and those are three states of
one setting: choosing one turns the other two off. The menu says nothing about
which one is in force, so the only way to answer "what am I looking at" is to
look at the model and infer it — which is the question the menu should have
been answering. Reading it off the model is least reliable exactly when it
matters: a part with no internal detail looks much the same solid as it does
with edges on, and transparent at 0.25 against a light bed is easy to mistake
for a slicing artefact.

"whatever I clicked last" is not an answer either. The same three modes are
bound to keys (`keyboard.js` calls `$('render-solid').onclick()` directly), and
the mode is restored from storage on boot — so on a fresh window nobody has
clicked anything and one of the three is still in effect.

*toggle edges* is the fourth item and a different kind of thing: an independent
on/off. Its name says it flips something but not which way, so it reads as a
question with no answer until the current state is shown next to it.

How
---
By reading the state, not by remembering the click. Kiri keeps the whole render
mode in local-storage keys, and its own keyboard handler decides what is
current from exactly two of them::

    let isWireframe = api.local.getBoolean('model.wireframe');
    let isGhost = !isWireframe && (api.local.getFloat('model.opacity') ?? 1.0) < 1;

This uses the same test — solid is what is left over — so the menu agrees with
the shortcut keys by construction, and a mode arrived at by click, by keystroke
or by restore all mark the same item. `api.local` is public (`api.js` exports
it) and `visuals.js` writes these keys through it, so there is nothing internal
being reached into here.

`getBoolean`'s second argument is not passed, deliberately. It looks like a
fallback and is not one::

    function localGetBoolean(key, def = true) {
        return val === true || val === 'true' || val === def;
    }

— it returns true when the *stored value equals* it, so `getBoolean(k, false)`
answers true for a stored `false`. Upstream's own detection leaves it alone and
so does this.

The mark is Kiri's `selected` class, which `menubar.css` already styles for a
dropdown row::

    #menubar .top-menu-drop .content > div.selected { … background-color: var(--menu-active-bg) }

so there is no new CSS, and it is the same highlight the app already uses for
"this is the current one" on the mode buttons — `view-arrange`, `act-slice`,
`act-animate`. Nothing upstream writes `selected` on a `render-*` item, so
there is no one else to fight with over it.

Refreshed when the state changes
--------------------------------
`api.local`'s writers are wrapped rather than listening for `settings.saved`.
The four menu handlers do end with `api.conf.save()`, so the event would catch
them — but `state/visual.js#savePreferences` writes `model.edges` and
`model.wireframe` and saves no settings at all, and a mark that silently stops
matching in one path is worse than no mark. The keys *are* the state, so a
write to one of them is the signal, whoever makes it. Coalesced through a zero
timeout because a single handler writes three of them.

What it does not follow
-----------------------
`state/visual.js#applyModeState` layers a per-view opacity over the top, so
what is on screen during slice or animate is not necessarily `model.opacity`.
That is a view's own presentation and it reverts; the menu reports the mode the
menu sets, which is the persisted choice, and the same one the keyboard reads.
"""

from __future__ import annotations

import json

#: The three mutually exclusive modes, and the independent toggle under the
#: separator. Ids, because that is what `init/sync.js` binds and what survives
#: minification into the pack.
SOLID_ID = "render-solid"
WIRE_ID = "render-wire"
GHOST_ID = "render-ghost"
EDGES_ID = "render-edges"

#: Kiri's own "this is the current one" class, already styled for a dropdown
#: row in `web/moto/menubar.css` and used on the mode buttons.
MARK_CLASS = "selected"

#: Where the state lives. `model.opacity` is the primary key;
#: `model.wireframe.opacity` is kept beside it for backwards compatibility and
#: is not read here.
KEY_WIREFRAME = "model.wireframe"
KEY_OPACITY = "model.opacity"
KEY_EDGES = "model.edges"

#: Writes to keys under this prefix re-read the state. Broader than the three
#: keys on purpose: `model.wireframe.color` and friends are written in the same
#: breath, and one extra refresh costs three storage reads.
WATCH_PREFIX = "model."

#: Belt and braces around the storage wrapper: the first paint, and the points
#: where a whole settings record can arrive from somewhere else (a restore, an
#: opened project) without going through `api.local`.
EVENTS = ("init.two", "init-done", "settings.saved", "view.set")


def render_menu_js() -> str:
    """JS marking the active render mode in the render menu."""
    return """
(function () {
  var SOLID = %(solid)s, WIRE = %(wire)s, GHOST = %(ghost)s, EDGES = %(edges)s;
  var MARK = %(mark)s;
  var K_WIRE = %(k_wire)s, K_OPAC = %(k_opac)s, K_EDGE = %(k_edge)s;
  var WATCH = %(watch)s, EVENTS = %(events)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function mark(id, want) {
    var el = document.getElementById(id);
    if (!el || !el.classList) return;
    if (want) el.classList.add(MARK); else el.classList.remove(MARK);
  }

  // No default argument: `getBoolean(key, def)` returns true when the stored
  // value EQUALS def, so passing one inverts the answer rather than supplying
  // a fallback. keyboard.js reads these keys the same way.
  function truthy(a, key) {
    try { return a.local.getBoolean(key) === true; } catch (e) { return false; }
  }

  function refresh() {
    var a = api();
    if (!a || !a.local) return;
    var wire = truthy(a, K_WIRE);
    var opacity;
    try { opacity = a.local.getFloat(K_OPAC); } catch (e) { opacity = null; }
    // getFloat answers null for an unset key, and upstream reads that as 1.0
    if (opacity === null || opacity === undefined || isNaN(opacity)) opacity = 1;
    var ghost = !wire && opacity < 1;
    // exactly one of the three, whichever way the state was arrived at
    mark(SOLID, !wire && !ghost);
    mark(WIRE, wire);
    mark(GHOST, ghost);
    // and the independent toggle, which says what it would be flipping from
    mark(EDGES, truthy(a, K_EDGE));
  }

  // `render-ghost` writes three keys in one handler; one refresh is enough.
  var pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    setTimeout(function () { pending = false; refresh(); }, 0);
  }

  function install() {
    var a = api();
    if (!a || !a.local || !a.event) return false;
    // the menu is built by init/menu.js; without it there is nothing to mark
    // and no way to tell a missing element from a wrong one
    if (!document.getElementById(SOLID)) return false;
    if (a.local.__piccolaRenderMark) return true;

    // The keys are the state, so a write to one is the signal -- whoever makes
    // it. `settings.saved` would miss state/visual.js, which writes these two
    // and saves nothing.
    ['set', 'put', 'setItem', 'toggle'].forEach(function (name) {
      var orig = a.local[name];
      if (typeof orig !== 'function') return;
      a.local[name] = function (key) {
        var out = orig.apply(this, arguments);
        if (typeof key === 'string' && key.indexOf(WATCH) === 0) schedule();
        return out;
      };
    });
    a.local.__piccolaRenderMark = true;

    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, schedule); } catch (e) { }
    });
    refresh();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "solid": json.dumps(SOLID_ID),
        "wire": json.dumps(WIRE_ID),
        "ghost": json.dumps(GHOST_ID),
        "edges": json.dumps(EDGES_ID),
        "mark": json.dumps(MARK_CLASS),
        "k_wire": json.dumps(KEY_WIREFRAME),
        "k_opac": json.dumps(KEY_OPACITY),
        "k_edge": json.dumps(KEY_EDGES),
        "watch": json.dumps(WATCH_PREFIX),
        "events": json.dumps(list(EVENTS)),
    }
