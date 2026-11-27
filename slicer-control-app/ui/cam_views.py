"""
ui/cam_views.py
A view bar over the Kiri:Moto viewport: the six standard views, home, fit, and
a perspective/orthographic switch.

Why
---
All of it already worked, and none of it could be found.

`Space.view.top/bottom/front/back/left/right/home/fit()` are public methods on
the moto space module, and each one animates the camera to a canonical
position. Upstream binds hotkeys to *some* of them — `t` top, `f` front,
`b` back, `h` home, `F` fit, `z` reset (`moto/space.js`, `keyHandler`) — and
leaves left, right and bottom with no key at all. There is no visible control
for any of them anywhere in the interface, so unless you have read the source
the machine appears to have one viewpoint you must orbit to by hand. Squaring
up on a face is the ordinary way to check a toolpath against a feature, and
dragging until it looks straight is not the same thing as being straight.

The projection switch existed too, as a checkbox at the bottom of the
Preferences dialog. That is a reasonable home for a preference and a poor one
for a control you flip while looking at a part: orthographic is how you read
whether two faces line up, and perspective is how you read depth, so which one
you want changes several times in a session and is a property of what you are
doing right now, not of how you like your software set up.

So both come out to where the model is.

How
---
Entirely through the public surface — no vendor patch. `api.const.SPACE` is the
moto space module itself (`kiri/app/api.js` puts it in `api.const`), so
`api.const.SPACE.view[name]()` is the same call the hotkeys make.

The projection switch does **not** call `setProjection` directly. It writes
`api.ui.ortho.checked` and invokes that checkbox's own `onclick`, which is
upstream's `preferences.booleanSave` — and that saves `controller.ortho`,
emits `boolean.update`, and calls `space.view.setProjection()` itself. Going
through it means the setting persists, the Preferences dialog stays in step,
and there is exactly one code path deciding what the camera is. Driving the
checkbox rather than clicking it is deliberate: `click()` toggles, so it acts
on whatever state the checkbox happens to be in, while assigning `checked`
first states the intent and cannot invert.

Reading the current projection goes to `space.view.getProjection()`, the camera
itself, rather than to the saved preference, so a disagreement between the two
shows as what you are actually looking at. Note that the neighbouring
`Space.isOrtho()` / `Space.isPerspective()` helpers cannot be used: both test a
bare `type` that exists nowhere at module scope — a leftover from when
`setProjection`'s parameter was in scope — so in a module (strict mode) either
one throws `ReferenceError` on the first call. They have no callers upstream,
which is presumably why nobody has noticed.

Swapping the camera is safe to do repeatedly. `setProjection` saves the view
position and target, builds the new camera at the same place, rebuilds
`viewControl` around it, restores the position and disposes the old one. The
only place in the whole of Kiri that holds a camera is `face-tool.js`, and it
re-reads `space.internals().camera` on each use, so nothing is left pointing at
the disposed one.

Where it sits
-------------
`#mid` is the overlay layer above the viewport — `position: fixed`,
`pointer-events: none`, with children switching pointing back on. `#container`,
which holds the actual canvas, is `z-index: -1` and sits behind everything, so
it is not a candidate. The bar goes in the bottom *right* of `#mid-mid`, the
growing region between the two side panels — right rather than centre because
the centre of that edge is the busiest strip in the viewport: the animation
transport centres its controls there, and the toolpath checkbox row does too.

That is also where Kiri keeps its own bottom furniture, so the bar stacks above
it rather than under it. `#layer-slider` spans the bottom edge during preview,
and the animation transport is `left: 0; right: 0` with centred content — the
same few hundred pixels this bar wants. Both come and go with the preview and
the animation, so the offset is measured rather than fixed:
:func:`reposition` reads each one's `getBoundingClientRect()`, ignores any that
measures zero-height — which is what `display: none` reports, whichever of the
several ways Kiri hid it — and lifts the bar clear of the tallest survivor. A
`ResizeObserver` on the pair drives it, because collapsing to `display: none`
is a resize to 0x0 and coming back is another, so both arrive without having to
watch for *how* it happened.

Anchoring needs nothing: `index.css` opens with `div { position: relative }`,
so every div in Kiri is already a containing block and an absolutely positioned
child of `#mid-mid` resolves against `#mid-mid`. Which is just as well, because
`#mid-mid` is also `display: flex` — an in-flow child of it is a flex item and
shrinks to its content, so a wrapper holding only an absolutely positioned bar
collapses to zero width and takes the bar off the left edge of the screen with
it. Out of flow, the flex container is not consulted at all. (That was the
first attempt, and it rendered exactly as described: 194 px of bar sitting at
x = -197.)

Paint order puts it at `z-index: 210`, just above the animation transport's
200 — they share an edge, so this is what decides the overlap in the moment
between the transport appearing and the reposition landing.
"""

from __future__ import annotations

import json

#: The overlay layer the bar is anchored inside. `#mid`, not `#mid-mid`: the
#: bar lines its right edge up with the operations panel, and `#mid-mid` stops
#: where that panel begins, so from inside it the panel is unreachable. `#mid`
#: is `position: fixed` and spans the window, so it is a containing block that
#: covers both.
HOST_ID = "mid"

#: What the right edge is aligned to — the panel carrying *Objects* and the
#: *Operation List*. Measured rather than assumed: it is a flex item whose
#: width follows its content and the window.
ALIGN_ID = "panel-right"

#: The bar.
BAR_ID = "piccola-view-bar"

#: The segmented perspective/orthographic control.
PROJ_ID = "piccola-view-proj"

#: The caption over the bar. It is also the bar's show / hide switch: the
#: open bar covers the bottom of the Operation List on a smaller window, so
#: one click folds it to just this caption and another brings it back.
TITLE_ID = "piccola-view-title"
TITLE_TEXT = "views"
TITLE_TIP = "Show or hide the view buttons."

#: On the bar while it is folded away.
COLLAPSED_CLASS = "piccola-view-collapsed"

#: Where the folded / open choice is remembered. Per viewer and per browser
#: profile, which is what it is: a layout convenience, not a setting.
COLLAPSED_KEY = "piccola-view-bar-collapsed"

#: The one View-menu action the bar did not already have. *contents*, *home*
#: and *top* are the same `SPACE.view.*` calls the buttons above already make
#: (`init/sync.js` binds the menu entries straight to them), so they are not
#: duplicated; *fullscreen* is deliberately left out — `#app-xpnd` is hidden
#: (see `_HIDE_PANELS_CSS`) and making the embedded page fullscreen inside the
#: app's own window is not what the button appears to promise.
#:
#: Reached by clicking upstream's menu item rather than calling its handler:
#: `context-setfocus` runs `view_tools.startFocus(...)`, and `view_tools` is not
#: on the public surface. The element's own `onclick` is, and clicking it gets
#: the whole interaction — the pick, the raycast, `api.space.set_focus` — with
#: nothing reimplemented.
FOCUS_MENU_ID = "context-setfocus"
FOCUS_LABEL = "focal point"
FOCUS_TITLE = "click a point to orbit around"

#: Our button for it.
FOCUS_BTN_ID = "piccola-view-focus"

#: Marks the lit segment. Same name shape as the stock shape selector's, which
#: is the same control drawn the same way.
_ON_CLASS = "piccola-view-on"

#: `(Space.view method, label, tooltip)`, laid out four to a row: the three
#: opposing pairs down columns 1-3, and the two whole-model views in column 4.
#: Words rather than icons — a cube with one face shaded says nothing about
#: *back* or *bottom*, whose faces are the ones you cannot see.
VIEW_BUTTONS = (
    ("top", "top", "look straight down"),
    ("front", "front", "look at the front"),
    ("left", "left", "look at the left side"),
    ("home", "home", "back to the default angle"),
    ("bottom", "bottom", "look straight up"),
    ("back", "back", "look at the back"),
    ("right", "right", "look at the right side"),
    ("fit", "fit", "zoom to fit everything"),
)

#: The two projections, and whether each means `controller.ortho`.
PROJECTIONS = (
    ("perspective", "perspective", False),
    ("orthographic", "orthographic", True),
)

#: How much room a fit leaves around the part, as a factor on upstream's
#: framing. The canvas is the whole window — `#container` is `top/left/right/
#: bottom: 0` — and everything else floats on top of it, so upstream's fit,
#: which knows only the window, frames a part that then runs under the 45 px
#: menubar and under this bar. 1.18 buys back roughly the 15% of window height
#: those two take on a normal screen, with a little slack.
FIT_ROOM = 1.18

#: Upstream's defaults (`moto/space.js`), which these adjust. They are module
#: locals with a setter and no getter, so the base values have to be repeated
#: here; if upstream retunes them this is the line to follow.
_FIT_PADDING_PERSPECTIVE = 0.5
_FIT_PADDING_ORTHOGRAPHIC = 0.9

#: The two modes take `padding` in **opposite senses**, which is worth stating
#: plainly because getting it backwards zooms in rather than out. Perspective
#: multiplies the camera distance — `desiredDistance = maxDim / tan(fov/2) *
#: padding` — so a bigger number stands further back. The orthographic branch
#: divides by it, under an upstream comment reading "keep historical fit padding
#: semantics: lower padding => more margin". Hence one is multiplied and the
#: other divided by the same factor.
FIT_PADDING = {
    "perspective": round(_FIT_PADDING_PERSPECTIVE * FIT_ROOM, 4),
    "orthographic": round(_FIT_PADDING_ORTHOGRAPHIC / FIT_ROOM, 4),
}

#: Kiri's own bottom-edge overlays, which the bar sits above when they show.
BOTTOM_NEIGHBOURS = ("layer-slider", "layer-animate")

#: Space left under the bar, and between it and anything it stacks on.
BOTTOM_GAP_PX = 8

#: Space left to the right of the bar. It sits bottom *right* rather than
#: bottom centre because the centre of that edge is the busiest strip in the
#: viewport: the animation transport centres its controls there, and so does
#: the toolpath checkbox row, which shares the same bottom edge. Three things
#: competing for one strip is part of what made those checkboxes hard to find.
#: Off to the side it still clears whatever is showing — the lift is measured,
#: not positional — but it stops taking part in the argument.
SIDE_GAP_PX = 8

#: The `api.ui` key of upstream's preference checkbox, which is what actually
#: performs the switch. See the module notes.
ORTHO_UI_KEY = "ortho"

#: What that checkbox's hover text becomes. Upstream's is
#: ``["orthographic display", "requires page refresh"]`` — and it does not:
#: `booleanSave` calls `space.view.setProjection()`, which swaps the camera in
#: place. The second half dates from when `controller.ortho` was only read once,
#: by `space.init(container, slider, ortho)` at startup. Left alone it now tells
#: the operator that the button they can see working does not work. It is also
#: an array reaching `setAttribute`, so the browser renders it with the comma
#: in: "orthographic display,requires page refresh".
ORTHO_TITLE = "orthographic display"


def css() -> str:
    """Styling for the view bar."""
    return "\n".join([
        # absolute keeps it out of #mid-mid's flex flow, which would otherwise
        # shrink it to nothing -- see the module notes
        # `bottom` is overwritten from script -- see reposition() -- so the
        # bar clears the layer slider and animation transport when they show
        f"#{BAR_ID} {{ position: absolute; bottom: {BOTTOM_GAP_PX}px;"
        f" right: {SIDE_GAP_PX}px;"
        f" z-index: 210; pointer-events: all; display: flex;"
        f" flex-direction: column; gap: 3px; padding: 4px;"
        f" border-radius: 5px; background: var(--menubar-back, #F0F0F0);"
        f" box-shadow: 0 1px 4px rgba(0,0,0,0.25); opacity: 0.92; }}",
        f"#{BAR_ID}:hover {{ opacity: 1; }}",
        f"#{BAR_ID} .piccola-view-grid {{ display: grid;"
        f" grid-template-columns: repeat(4, 1fr); gap: 2px; }}",
        # Kiri's buttons are flex boxes with no justification, so a label in a
        # grid cell wider than itself sits against the left edge and the column
        # reads as ragged
        f"#{BAR_ID} button {{ font-size: 11px; padding: 2px 6px;"
        f" white-space: nowrap; justify-content: center; margin: 0; }}",
        # one choice, so it is drawn as one control -- matching the stock
        # shape selector rather than inventing a second look for the same idea
        f"#{PROJ_ID} {{ display: flex; gap: 0; }}",
        f"#{PROJ_ID} > button {{ flex: 1 1 0; min-width: 0; border-radius: 0;"
        f" margin-left: -1px; }}",
        f"#{PROJ_ID} > button:first-child {{ margin-left: 0;"
        f" border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}",
        f"#{PROJ_ID} > button:last-child {{"
        f" border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}",
        f"#{TITLE_ID} {{ font-size: 10px; text-align: center; opacity: 0.65;"
        f" text-transform: uppercase; letter-spacing: 0.06em;"
        f" padding: 0 0 1px 0; cursor: pointer; user-select: none; }}",
        f"#{TITLE_ID}:hover {{ opacity: 1; }}",
        # folded: everything but the caption goes, whoever added it (the
        # measure and stock box buttons are other modules')
        f"#{BAR_ID}.{COLLAPSED_CLASS} > :not(#{TITLE_ID}) {{ display: none !important; }}",
        f"#{BAR_ID}.{COLLAPSED_CLASS} {{ padding: 2px 8px; }}",
        f"#{BAR_ID}.{COLLAPSED_CLASS} #{TITLE_ID} {{ padding: 0; }}",
        f"#{FOCUS_BTN_ID} {{ width: 100%; }}",
        f"#{PROJ_ID} > button.{_ON_CLASS} {{ position: relative; z-index: 1;"
        f" background: #3A78B5 !important; border-color: #3A78B5 !important;"
        f" color: #FFFFFF !important; }}",
    ])


def view_bar_js() -> str:
    """JS installing the view bar over the viewport."""
    return """
(function () {
  var HOST = %(host)s, BAR = %(bar)s, PROJ = %(proj)s;
  var ON = %(on)s, VIEWS = %(views)s, PROJS = %(projs)s, ORTHO = %(ortho)s;
  var ORTHO_TITLE = %(ortho_title)s;
  var NEIGHBOURS = %(neighbours)s, GAP = %(gap)s, ALIGN = %(align)s;
  var TITLE = %(title_id)s, TITLE_TEXT = %(title)s, TITLE_TIP = %(title_tip)s;
  var COLLAPSED = %(collapsed)s, COLLAPSED_KEY = %(collapsed_key)s;
  var FOCUS_MENU = %(focus_menu)s, FOCUS_ID = %(focus_id)s;
  var FOCUS_LABEL = %(focus_label)s, FOCUS_TITLE = %(focus_title)s;
  var FIT_PADDING = %(fit_padding)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function space() {
    var a = api();
    try { return a && a.const && a.const.SPACE; } catch (e) { return null; }
  }
  function controller() {
    var a = api();
    try { return a && a.conf.get().controller; } catch (e) { return null; }
  }

  // The camera, not the saved preference, so a disagreement between the two
  // shows as what is actually on screen. Deliberately NOT Space.isOrtho(),
  // which throws: see the module notes.
  function isOrtho() {
    var s = space();
    try {
      if (s && s.view && s.view.getProjection) {
        return s.view.getProjection() === 'orthographic';
      }
    } catch (e) { }
    var c = controller();
    return !!(c && c.ortho);
  }

  // Applied to the space's own defaults rather than passed to our button's
  // fit(), so the `F` hotkey and any fit Kiri runs by itself get the same
  // room. A part framed to the window runs under the menubar and under this
  // bar, both of which float above the full-window canvas.
  function widenFit() {
    var s = space();
    if (!s || !s.view || typeof s.view.setFitPadding !== 'function') return;
    try { s.view.setFitPadding(FIT_PADDING); } catch (e) { }
  }

  // Upstream's own menu item, clicked. See FOCUS_MENU_ID for why this is a
  // click and not a call.
  function focus() {
    var el = document.getElementById(FOCUS_MENU);
    if (el && typeof el.click === 'function') el.click();
  }

  function go(name) {
    var s = space();
    if (!s || !s.view || typeof s.view[name] !== 'function') return;
    try { s.view[name](); } catch (e) { }
  }

  function setOrtho(want) {
    if (isOrtho() === want) return;
    var a = api(), cb = a && a.ui && a.ui[ORTHO];
    // Upstream's own handler: it saves controller.ortho, emits boolean.update
    // and calls space.view.setProjection. Assigning `checked` before invoking
    // it states the intent; click() would toggle whatever is there.
    if (cb && typeof cb.onclick === 'function') {
      cb.checked = want;
      try { cb.onclick(); } catch (e) { }
    } else {
      var s = space();
      if (s && s.view && s.view.setProjection) {
        try {
          s.view.setProjection(want ? 'orthographic' : 'perspective');
        } catch (e) { }
      }
    }
    refresh();
  }

  // The bottom edge is shared with the layer slider and the animation
  // transport, both of which come and go. Measured rather than assumed: a
  // hidden element reports a zero-height rect however it was hidden.
  function reposition() {
    var bar = document.getElementById(BAR);
    var host = document.getElementById(HOST);
    if (!bar || !host || typeof host.getBoundingClientRect !== 'function') return;
    var hb = host.getBoundingClientRect(), lift = 0;
    NEIGHBOURS.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || typeof el.getBoundingClientRect !== 'function') return;
      var r = el.getBoundingClientRect();
      if (!r || !r.height) return;
      lift = Math.max(lift, hb.bottom - r.top);
    });
    bar.style.bottom = (lift + GAP) + 'px';

    // Right edge flush with the operations panel above it, so the two read as
    // one column down the side rather than two things that nearly line up.
    // Measured every time: the panel is a flex item and its width moves with
    // its content and the window. With nothing there to line up against --
    // panel empty, or not built yet -- the stylesheet's own inset stands.
    var target = document.getElementById(ALIGN);
    var tr = target && typeof target.getBoundingClientRect === 'function'
             ? target.getBoundingClientRect() : null;
    if (tr && tr.width) {
      bar.style.right = Math.max(0, Math.round(hb.right - tr.right)) + 'px';
    } else {
      bar.style.right = '';
    }
  }

  function watchNeighbours() {
    // a hide is a resize to 0x0 and a show is another, so this catches both
    // without having to know which of Kiri's several ways did it
    if (typeof ResizeObserver !== 'function') return;
    var ro;
    try { ro = new ResizeObserver(reposition); } catch (e) { return; }
    NEIGHBOURS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) { try { ro.observe(el); } catch (e) { } }
    });
  }

  // Upstream's hover text on the preference checkbox is stale — see
  // ORTHO_TITLE. Corrected here rather than in ui/cam_labels.py because a
  // language-table rewrite cannot reach it: the title is stamped onto the
  // element by newBoolean() when the control is built.
  function fixOrthoTitle() {
    var a = api(), cb = a && a.ui && a.ui[ORTHO];
    if (!cb || typeof cb.setAttribute !== 'function') return;
    cb.setAttribute('title', ORTHO_TITLE);
    // newBoolean() puts it on the row as well, so both have to agree
    var row = cb.parentNode;
    if (row && typeof row.setAttribute === 'function') {
      row.setAttribute('title', ORTHO_TITLE);
    }
  }

  function refresh() {
    var bar = document.getElementById(BAR);
    if (!bar) return;
    var ortho = isOrtho();
    PROJS.forEach(function (p) {
      var b = document.getElementById(PROJ + '-' + p[0]);
      if (!b) return;
      if (p[2] === ortho) b.classList.add(ON); else b.classList.remove(ON);
    });
    var fb = document.getElementById(FOCUS_ID);
    if (fb) fb.disabled = !document.getElementById(FOCUS_MENU);
  }

  // Fold the bar to its caption, or open it again, and remember which.
  function fold(bar, folded, quiet) {
    if (folded) bar.classList.add(COLLAPSED); else bar.classList.remove(COLLAPSED);
    var title = document.getElementById(TITLE) || bar.querySelector('#' + TITLE);
    if (title) title.textContent = TITLE_TEXT + (folded ? ' ▸' : ' ▾');
    if (quiet) return;
    try { localStorage.setItem(COLLAPSED_KEY, folded ? '1' : '0'); } catch (e) { }
  }

  function build() {
    var bar = document.createElement('div');
    bar.id = BAR;

    var title = document.createElement('div');
    title.id = TITLE;
    title.setAttribute('title', TITLE_TIP);
    title.onclick = function () { fold(bar, !bar.classList.contains(COLLAPSED)); };
    bar.appendChild(title);
    var folded = false;
    try { folded = localStorage.getItem(COLLAPSED_KEY) === '1'; } catch (e) { }
    fold(bar, folded, true);

    var grid = document.createElement('div');
    grid.className = 'piccola-view-grid';
    VIEWS.forEach(function (v) {
      var b = document.createElement('button');
      b.textContent = v[1];
      b.setAttribute('title', v[2]);
      b.onclick = function () { go(v[0]); };
      grid.appendChild(b);
    });
    bar.appendChild(grid);

    var proj = document.createElement('div');
    proj.id = PROJ;
    PROJS.forEach(function (p) {
      var b = document.createElement('button');
      b.id = PROJ + '-' + p[0];
      b.textContent = p[1];
      b.setAttribute('title', p[1] + ' projection');
      b.onclick = function () { setOrtho(p[2]); };
      proj.appendChild(b);
    });
    bar.appendChild(proj);

    // The remaining View-menu action. Disabled rather than hidden when the
    // menu item is absent: it says the control exists and is not available,
    // which is the truth, where a missing button says nothing at all.
    var fb = document.createElement('button');
    fb.id = FOCUS_ID;
    fb.textContent = FOCUS_LABEL;
    fb.setAttribute('title', FOCUS_TITLE);
    fb.onclick = focus;
    bar.appendChild(fb);

    return bar;
  }

  function install() {
    var a = api();
    var host = document.getElementById(HOST);
    if (!a || !a.event || !host) return false;
    if (document.getElementById(BAR)) return true;

    // absolutely positioned, so it joins none of #mid-mid's flex flow and
    // where in the child list it lands changes nothing
    host.appendChild(build());

    // the preference dialog can change the projection too, and does not tell
    // anyone but itself
    ['boolean.update', 'settings.saved', 'init-done'].forEach(function (ev) {
      try { a.event.on(ev, refresh); } catch (e) { }
      try { a.event.on(ev, reposition); } catch (e) { }
    });
    // slicing and preview are what put the bottom furniture on screen; the
    // ResizeObserver is the real mechanism, these are belt and braces
    ['slice.end', 'preview.end', 'animate', 'view.set'].forEach(function (ev) {
      try { a.event.on(ev, reposition); } catch (e) { }
    });
    widenFit();
    watchNeighbours();
    try {
      if (window.addEventListener) window.addEventListener('resize', reposition);
    } catch (e) { }
    reposition();
    // the preferences dialog may not be built yet, so try again once it is
    try { a.event.on('init-done', fixOrthoTitle); } catch (e) { }
    fixOrthoTitle();
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
        "host": json.dumps(HOST_ID),
        "bar": json.dumps(BAR_ID),
        "proj": json.dumps(PROJ_ID),
        "on": json.dumps(_ON_CLASS),
        "views": json.dumps([list(v) for v in VIEW_BUTTONS]),
        "projs": json.dumps([list(p) for p in PROJECTIONS]),
        "ortho": json.dumps(ORTHO_UI_KEY),
        "ortho_title": json.dumps(ORTHO_TITLE),
        "neighbours": json.dumps(list(BOTTOM_NEIGHBOURS)),
        "gap": json.dumps(BOTTOM_GAP_PX),
        "align": json.dumps(ALIGN_ID),
        "title_id": json.dumps(TITLE_ID),
        "title": json.dumps(TITLE_TEXT),
        "title_tip": json.dumps(TITLE_TIP),
        "collapsed": json.dumps(COLLAPSED_CLASS),
        "collapsed_key": json.dumps(COLLAPSED_KEY),
        "focus_menu": json.dumps(FOCUS_MENU_ID),
        "focus_id": json.dumps(FOCUS_BTN_ID),
        "focus_label": json.dumps(FOCUS_LABEL),
        "focus_title": json.dumps(FOCUS_TITLE),
        "fit_padding": json.dumps(FIT_PADDING),
    }
