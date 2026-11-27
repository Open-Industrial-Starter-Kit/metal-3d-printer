"""
ui/cam_origin_marker.py
Restyles Kiri:Moto's origin marker: red, unlit, double-sided, and a little
larger.

Why
---
The marker is the one thing on screen that says *this is where the machine
thinks zero is*, and upstream draws it in `0xcceeff` on `0x88aadd` — a pale
blue on a slightly less pale blue, at `opacity: 0.5`, on a light grey bed. It
is easy to lose against the platform grid and easy to mistake for part of it.
Zero is worth being sure about before pressing cycle start, so it goes red: the
one colour nothing else in the viewport uses.

Two further problems, both in the same four lines of `moto/space.js`.

**It disappears from below.** The marker is flat geometry — four
`CircleGeometry` quadrants, a `RingGeometry`, and two crossed planes — and both
its materials leave `side` at the default `FrontSide`; upstream has
`// side: THREE.DoubleSide` sitting commented out in each. Flat single-sided
geometry seen from behind is not dim, it is *gone*. Nothing else is
responsible: the bed is `FrontSide` too, with `depthWrite: false`, so it is not
occluding anything — look up from under the platform and the marker simply
ceases to exist.

**And it would read wrong even double-sided.** Both materials are
`MeshPhongMaterial`, so they are lit, and the light is above the bed. Turning on
the back faces would show them shaded almost black from underneath — present,
but not obviously the same marker. A reference marker should not be lit at all:
it is a symbol, not a surface. `MeshBasicMaterial` renders it the same red from
every angle.

How
---
By wrapping, not by patching. `space.platform.setOrigin` is public — it is what
`kiri/app/platform.js` calls — and it *rebuilds the whole group* on every
change, so wrapping it catches every rebuild rather than racing them. The group
itself is internal (`grid.origin.group`), but `setOrigin` hands it to
`Space.scene.add`, which is also public: wrapping that for the duration of the
call captures exactly the object upstream just built, with no traversal and no
guessing which group in the scene is the marker.

Size is handled on the children rather than the group. Upstream keeps
`grid.origin.scale()` on the group and re-runs it on every camera move
(`group.scale.set(s, s, s)`), so anything written there is overwritten within a
frame. A scale on each child multiplies with the group's and survives.

Placement is upstream's, in every mode
--------------------------------------
This wrapper changes how the marker *looks*, never where it goes. An earlier
version moved it onto the rotary axis, on the grounds that `platform.js` halves
the origin for display in indexed mode (`oz = camStockIndexed ? z / 2 : z`)
while the export path zeroes on the axis — so the drawn marker sat a radius
above the machine's zero. That was true, and it still made the picker harder to
use rather than easier, because it put the rotary origin somewhere the flat
origin never is. Consistency won: the marker is placed by `update_origin` alone,
so rotary and flat behave the same way and there is one rule to learn instead of
two.
"""

from __future__ import annotations

import json

#: The disc — upstream's four quadrant circles, `0xcceeff`. Kept translucent so
#: the bed grid still reads through it and the marker does not become a blob.
DISC_COLOR = 0xE5484D
DISC_OPACITY = 0.40

#: The ring and crosshair — upstream's `0x88aadd`. This is the part that has to
#: be found at a glance, so it is the stronger tone and nearly opaque.
LINE_COLOR = 0xC1121F
LINE_OPACITY = 0.95

#: Size multiplier on each child. Upstream's marker is deliberately small and
#: distance-scaled, and this keeps that behaviour.
#:
#: It was 1.5 — half again as big as upstream — on the reasoning that the thing
#: has to be findable at a glance. That was the wrong trade: at 1.5 the ring
#: covers the part it marks, and the stock-point handles beside it are the
#: controls you actually aim at. A third of that reads as a reference mark
#: rather than an object, which is what it is.
SCALE = 0.5

#: The `api.ui` key of Preferences > display > *show origin*, and the class the
#: app marks a withdrawn settings row with.
SHOW_ORIGIN_UI_KEY = "showOrigin"
HIDDEN_ROW_CLASS = "piccola-hidden-row"

#: Drawn over everything rather than depth-tested against it. A zero marker is
#: a reference, not an object in the scene: it is wanted precisely when it is
#: behind something, and in rotary it is *inside* the blank by definition (see
#: :func:`origin_marker_js`), where depth testing would hide it completely.
RENDER_ORDER = 12

#: Views the marker is withdrawn for, named by their `api.view.is_<name>()`
#: predicate. Empty tuple keeps it on screen everywhere.
#:
#: In ARRANGE the marker answers "where is zero", which is worth drawing through
#: the part it marks (see :data:`RENDER_ORDER`). In these two nothing is being
#: set up — the result is being read — and `depthTest: false` means the marker
#: floats over the toolpaths and the carve rather than sitting under them: a
#: bright ring on top of the one thing each view exists to show.
#:
#: `preview` is deliberately not in this list. It is the same argument and could
#: be added by name alone, but it was not asked for.
HIDE_IN_VIEWS = ("animate", "slice")

#: Events that can move into or out of one of :data:`HIDE_IN_VIEWS`. `view.set`
#: is emitted by `view-mode.js` on every switch; `animate` is emitted by both
#: animators once they have bound, which covers an animation started without a
#: mode change; `slice.end` covers Generate, whose view switch happens before
#: the toolpaths the marker would sit on top of exist.
ANIMATE_EVENTS = ("view.set", "animate", "slice.end", "init-done")


def origin_marker_js() -> str:
    """JS restyling the origin marker wherever Kiri rebuilds it."""
    return """
(function () {
  var DISC = %(disc)s, DISC_O = %(disc_o)s;
  var LINE = %(line)s, LINE_O = %(line_o)s, SCALE = %(scale)s;
  var ORDER = %(order)s;
  var SHOW_KEY = %(show_key)s, HIDDEN_ROW = %(hidden_row)s;
  var HIDE_VIEWS = %(hide_views)s, ANIM_EVENTS = %(anim_events)s;

  // The group currently on screen, and whether the animate view has withdrawn
  // it. Kept as state because the two change independently: Kiri rebuilds the
  // group whenever the origin moves, and the view switches under a group that
  // may not be rebuilt for the whole session.
  var marker = null, withdrawn = false;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function space() {
    var a = api();
    try { return a && a.const && a.const.SPACE; } catch (e) { return null; }
  }
  function conf() {
    var a = api();
    try { return a && a.conf.get(); } catch (e) { return null; }
  }

  // Upstream builds the disc from CircleGeometry and the ring/crosshair from
  // RingGeometry and PlaneGeometry. Telling them apart by geometry keeps the
  // two tones upstream chose, rather than flattening the marker to one colour.
  function isDisc(mesh) {
    var g = mesh && mesh.geometry;
    var t = g && (g.type || (g.constructor && g.constructor.name));
    return t === 'CircleGeometry';
  }

  // `setOrigin` adds the marker group and *then* calls `updateDraws()`, which
  // runs `updateGrid()` -- and that does its own `Space.scene.remove(oldView);
  // Space.scene.add(grid.view)`. So the last object handed to `scene.add`
  // during the call is the platform GRID, not the marker. Keeping only the
  // last capture therefore restyled the grid and left the marker untouched,
  // which is exactly why it stayed upstream's pale blue however many times it
  // was rebuilt. Every add is collected and the marker picked out by shape:
  // upstream builds it from four CircleGeometry quadrants, a RingGeometry and
  // two crossed PlaneGeometry -- seven children, four of them circles.
  function isMarkerGroup(o) {
    if (!o || !o.children || o.children.length !== 7) return false;
    var circles = 0;
    for (var i = 0; i < 7; i++) if (isDisc(o.children[i])) circles++;
    return circles === 4;
  }

  function pickMarker(list) {
    if (!list) return null;
    for (var i = 0; i < list.length; i++) {
      if (isMarkerGroup(list[i])) return list[i];
    }
    return null;
  }

  function restyle(group) {
    var THREE = window.THREE;
    if (!group || !group.children || !THREE) return false;
    var touched = 0;
    group.children.forEach(function (mesh) {
      var disc = isDisc(mesh);
      // Basic, not Phong: a marker is a symbol, not a surface, and a lit
      // material seen from under the bed is shaded to near black.
      var mat = new THREE.MeshBasicMaterial({
        color: disc ? DISC : LINE,
        transparent: true,
        opacity: disc ? DISC_O : LINE_O,
        // the whole point of the exercise: flat geometry seen from behind is
        // not dim, it is absent
        side: THREE.DoubleSide,
        depthWrite: false,
        // and drawn through whatever is in front of it: on the rotary axis the
        // marker is inside the blank, so depth testing would hide it outright
        depthTest: false
      });
      try { if (mesh.material && mesh.material.dispose) mesh.material.dispose(); }
      catch (e) { }
      mesh.material = mat;
      mesh.renderOrder = ORDER;
      // On the child, because upstream re-runs its own scale on the GROUP on
      // every camera move and would overwrite anything written there.
      if (mesh.scale && mesh.scale.set) mesh.scale.set(SCALE, SCALE, SCALE);
      touched++;
    });
    return touched > 0;
  }

  // Kiri ships `showOrigin` false, so there is no marker at all until the
  // preference is on -- and "no marker" reads as a broken feature rather than
  // an unset checkbox, which is how this survived three attempts at making the
  // marker red.
  //
  // Pinned, not seeded: a one-shot write has to win a race against
  // `restoreSettings()` putting the stored value back, and losing it is silent.
  // Re-applied on every event that can write the controller record, with the
  // checkbox kept in step because `preferences.booleanSave` reads the
  // *checkbox* back into the record -- a stale unchecked box would otherwise
  // carry `false` in on the next preference change of any kind.
  //
  // It lives here rather than with the picker for a reason that cost real
  // time: the picker cannot install until its own settings group exists, and
  // hanging the marker's existence off that left a booted app with this module
  // installed, the picker not, and no marker in the viewport. This module needs
  // only SPACE and THREE, so it is already in place to restyle the group its
  // own pin causes to be built.
  function pinShowOrigin() {
    var a = api(), c = conf();
    if (!a || !c || !c.controller) return;
    var ui = a.ui && a.ui[SHOW_KEY];
    if (ui && ui.checked !== true) ui.checked = true;
    if (c.controller.showOrigin === true) return;
    c.controller.showOrigin = true;
    try { a.platform.update_origin(); } catch (e) { }
    try { a.conf.save(); } catch (e) { }
  }

  // The checkbox that used to offer it, withdrawn. It lives in the preferences
  // dialog, built later than this installs, so it is retried on the same events.
  function hideShowOriginRow() {
    var a = api();
    var el = a && a.ui && a.ui[SHOW_KEY];
    if (!el) return;
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) {
        n.classList.add(HIDDEN_ROW);
        return;
      }
    }
  }

  // Visibility is held on the group rather than by switching `showOrigin` off
  // and on. That preference is *pinned* true a few lines up, and every write to
  // it goes through `conf.save()` and a rebuild — so driving it from the view
  // would fight the pin, persist a display choice the user never made, and
  // churn the marker on every play. `group.visible` touches neither.
  function applyVisibility() {
    if (!marker) return;
    var want = !withdrawn;
    if (marker.visible === want) return;
    marker.visible = want;
    // moto/space.js renders only while `Date.now() - lastAction < 1500`, so on
    // a paused animation the change would otherwise not be painted until the
    // camera moved.
    var s = space();
    try { if (s && s.update) s.update(); } catch (e) { }
  }

  // Any of the views the marker is withdrawn for, asked through each one's own
  // predicate rather than by comparing mode constants -- those are internal
  // numbers, the predicates are the public surface.
  function inHiddenView() {
    var a = api();
    try {
      if (!a || !a.view) return false;
      for (var i = 0; i < HIDE_VIEWS.length; i++) {
        var fn = a.view['is_' + HIDE_VIEWS[i]];
        if (typeof fn === 'function' && fn.call(a.view)) return true;
      }
      return false;
    } catch (e) { return false; }
  }

  function syncVisibility() {
    if (!HIDE_VIEWS.length) return;
    var want = inHiddenView();
    if (withdrawn === want) return;
    withdrawn = want;
    // `marker` is only ever set by the setOrigin wrapper, so a marker that was
    // built before this installed and never rebuilt since leaves nothing to
    // hide. Kiri keeps the group on `grid.origin`, which is internal and has no
    // public accessor -- forcing one rebuild is how the restyle reaches it too
    // (see install()), and it hands the wrapper a group to capture.
    if (withdrawn && !marker) forceRebuild();
    applyVisibility();
  }

  // Get the marker currently on screen rebuilt, so it comes back through the
  // wrapper. See the note in install() for why a plain update will not do it.
  function forceRebuild() {
    var a = api(), s = space();
    if (!a || !s || !s.platform) return;
    try { s.platform.setOrigin(0, 0, 0, false); } catch (e) { }
    try { a.platform.update_origin(); } catch (e) { }
  }

  function install() {
    var a = api(), s = space();
    if (!a || !s || !s.platform || !s.scene || !window.THREE) return false;
    if (typeof s.platform.setOrigin !== 'function') return false;
    if (s.platform.__piccolaOriginMarker) return true;

    var capturing = false, captured = null;

    var addOrig = s.scene.add;
    s.scene.add = function (o) {
      // every add during the call, because setOrigin is not the only thing
      // adding to the scene inside it (see isMarkerGroup)
      if (capturing && o && captured) captured.push(o);
      return addOrig.apply(this, arguments);
    };

    var setOrig = s.platform.setOrigin;
    s.platform.setOrigin = function (x, y, z, show) {
      // Placement is left entirely to platform.js, in every mode.
      capturing = true;
      captured = [];
      try {
        return setOrig.call(this, x, y, z, show);
      } finally {
        capturing = false;
        captured = pickMarker(captured);
        // `show: false` returns without building anything, and an unchanged
        // origin returns early too -- both leave nothing captured, which is
        // not a failure
        if (captured) {
          restyle(captured);
          // A rebuild replaces the object `marker` pointed at, and the new one
          // is born visible -- so the withdrawal has to be re-applied to it or
          // moving the origin mid-animation would put the marker back.
          marker = captured;
          applyVisibility();
        }
      }
    };
    s.platform.__piccolaOriginMarker = true;

    // A marker is normally already on screen by the time this installs, and it
    // is the one the operator is looking at. Reaching it means getting it
    // rebuilt, the group being internal with no other handle on it -- but
    // simply re-running Kiri's update is not enough:
    //
    //     let unchanged = x === or.x && y === or.y && z === or.z &&
    //                     show === or.show;
    //     if (unchanged) { updateDraws(); return; }
    //
    // Recomputing the same origin recomputes the same numbers, so setOrigin
    // returns early and nothing is rebuilt. That is exactly what went wrong:
    // every *later* rebuild came out red, and the marker already on screen
    // stayed pale blue until something happened to move the origin.
    //
    // So the state is changed first. `show: false` differs from the live
    // `show: true`, which takes the not-unchanged path, drops the old group and
    // records the new state; the update that follows then differs from *that*
    // and builds a fresh group through the wrapper. With origin display
    // switched off there is simply no group either way.
    // Turn it on first: there is nothing to restyle until there is a marker,
    // and pinning it from here means the group is built through the wrapper
    // installed just above.
    ['init.one', 'init.two', 'init-done', 'settings.saved',
     'boolean.update'].forEach(function (ev) {
      try { a.event.on(ev, pinShowOrigin); } catch (e) { }
      try { a.event.on(ev, hideShowOriginRow); } catch (e) { }
    });
    pinShowOrigin();
    hideShowOriginRow();

    // Withdraw the marker for the animate view. `view.set` carries the mode,
    // but it is read back from `api.view` instead of trusted from the argument:
    // the same handler then serves `animate`, which carries the mode *name*
    // ('CAM'), and there is one source of truth rather than two.
    if (HIDE_VIEWS.length) {
      ANIM_EVENTS.forEach(function (ev) {
        try { a.event.on(ev, syncVisibility); } catch (e) { }
      });
    }

    forceRebuild();
    // and again once Kiri has finished starting up, in case this installed
    // before there was anything to rebuild
    try { a.event.on('init-done', forceRebuild); } catch (e) { }
    // and settle the initial state, in case this installed with the animate
    // view already up
    syncVisibility();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "disc": json.dumps(DISC_COLOR),
        "disc_o": json.dumps(DISC_OPACITY),
        "line": json.dumps(LINE_COLOR),
        "line_o": json.dumps(LINE_OPACITY),
        "scale": json.dumps(SCALE),
        "order": json.dumps(RENDER_ORDER),
        "show_key": json.dumps(SHOW_ORIGIN_UI_KEY),
        "hidden_row": json.dumps(HIDDEN_ROW_CLASS),
        "hide_views": json.dumps(list(HIDE_IN_VIEWS)),
        "anim_events": json.dumps(list(ANIMATE_EVENTS)),
    }
