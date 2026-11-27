"""
ui/cam_measure.py
A measuring tool: click two points on the model, read the distance between them.

Why it is needed
----------------
The workspace can tell you the *bounding box* of a selection -- that is the
Scale / Size panel -- and nothing else. How wide is that boss, how far apart are
those two holes, how deep is that step: none of those are the bounding box of
anything, and the only way to answer them was to go back to the CAD package the
model came from. A ruler is the cheapest possible answer, and CAM is exactly
where the questions get asked, because the answer decides the tool.

The gesture
-----------
A toggle in the view bar arms it. While armed, a *click* on the model drops a
point; the second one completes a measurement and the readout appears. A third
click starts over. Escape, or the button again, puts everything away.

Picks arrive through ``api.feature.on_mouse_up``, which is the hook Kiri already
provides for "this click means something other than selecting" -- the pocket
surface picker and the helical picker both use it. It is used for *that a click
happened*, not for where: the intersection it passes has ``point`` sitting at
the origin, since upstream's own users of the hook read only ``face`` and
``object``. The position comes from this module's own cast at the same pixel. Kiri only calls it for a
genuine click (its own test is that the pointer did not move between press and
release), only over the canvas, and only after it has put its view control back
in order.

That last part is the whole reason for using it. The first version of this
listened on the document and called ``stopPropagation`` on the release, to keep
Kiri from changing the selection. It worked, and it broke the viewport:
``space.js onMouseUp`` is what re-enables ``viewControl`` and hands it the
release, so swallowing the event left the control believing the button was
still down. Every mouse move after the first pick panned the scene. Going
through the hook means no event is intercepted at all -- nothing to get wrong,
because nothing is being taken away from anyone.

The one thing the hook cannot do is report a *miss*: Kiri raycasts first and
only calls the hook when something was hit. A miss is picked up by a plain
listener that neither cancels nor stops the event -- see the note on corners
below.

That listener has to wait for the hook before deciding the click was a miss, and
"wait" cannot mean "run later in the bubble". Kiri binds its mouse handlers on
``window`` (``space.js:2125``), and window is the *last* stop in the bubble
path, so a listener on ``document`` runs before it no matter when it was added.
The first version assumed the opposite and read its own "the hook claimed this"
flag while it was still false -- so every click landed two points at once, one
from each path, and the readout showed a measurement after a single click. The
check is deferred a tick instead, which puts it after all synchronous handling
however upstream binds it.

Snapping
--------
A raw surface hit is the wrong answer to most of these questions. Asked how wide
something is, nobody wants the distance between two arbitrary points near its
edges -- they want corner to corner. So a hit within :data:`SNAP_PX` *on screen*
of one of its triangle's three corners snaps to that corner.

Screen distance rather than model distance, because that is the tolerance the
user is actually working to: a pixel is a pixel whatever the zoom, so snapping
gets finer as you zoom in, which is exactly when you are being careful.

The same reasoning is why a miss is retried. A corner is on the *silhouette*,
where one pixel outside the outline is not the model at all -- measured: a ray
through a corner vertex hit, and the same ray one pixel over missed everything.
Clicking a corner and having nothing happen, with nothing said about why, is the
worst outcome available, and corners are what this tool is mostly aimed at. So a
ray that hits nothing is retried on a ring of offsets out to :data:`SNAP_PX`,
which is the same tolerance the snapping already works to.

That retry is where the fallback listener comes in: Kiri's hook never fires for
a miss, so the listener notices a click the hook did not claim and sweeps from
there. It is passive -- no ``preventDefault``, no ``stopPropagation`` -- so it
cannot repeat the panning bug.

Frames
------
Kiri's widgets hang under a group rotated -90 degrees about X -- the model is
Z-up, three.js is Y-up -- so a raycast hit, which arrives in world space, has
its Y and Z swapped relative to every number in the rest of the UI. The straight
line distance does not care (rotation preserves length) but the per-axis split
very much does, and reporting a part's height under "Y" would be worse than
reporting nothing.

So points are converted through that group with ``worldToLocal`` rather than by
hardcoding the rotation: if upstream ever changes how the scene is oriented,
this follows it instead of quietly lying.

Drawing
-------
Two markers and a line, added to the scene root, with ``depthTest`` off so they
stay visible when the point is on the far side of the model or inside a pocket.
A measurement you cannot see the ends of is not a measurement.

That choice is also why leaving the view has to clear them. Drawn through
everything, a marker left behind while the operator moves to *Animate* sits on
top of the simulated stock looking exactly like a speck of uncut material --
reported as one, and a reasonable reading of it. Escape used to be the only
thing that put them away, so anything that was not Escape left them there.
Changing view now ends the measurement: a stale answer about a view you are no
longer looking at is worth less than no answer.

While you are still choosing
----------------------------
Between the two clicks a dashed line follows the pointer from the first point,
with a ghost marker at the end of it and the distance updating as it goes. Until
that existed the first click left nothing to aim *from* -- a lone dot, and no
way to see what was being measured until it had already been measured.

The preview casts once per frame at most, and only straight at the pointer: the
ring sweep that rescues a missed corner is worth ~40 casts on a click and is not
worth them sixty times a second.

Where a point lands when it is not on the model
-----------------------------------------------
Off the part, the ray still has to land somewhere, or the band blinks out every
time the pointer crosses the background and the tool feels broken rather than
precise. Three surfaces are tried in order:

1. the model, snapped to a corner where there is one;
2. the **build platform** -- the bed is a real plane in the scene with real
   coordinates, so a point on it is a genuine position, not a guess;
3. a plane through the first point, square to the camera -- the last resort,
   used when the bed is not under the pointer either (looking from below, or
   out past the horizon). It keeps the band attached to the cursor and keeps
   the distance honest in the plane you are looking at.

Only the first of those is a point *on the part*, and that is the distinction
worth knowing about: measuring to the bed is exact, measuring on the view plane
is exact only in the two directions across the screen. Both are far better than
a band that vanishes.

The readout
-----------
Pinned just above the point that completed the measurement, not parked in a
corner of the window. The number answers a question asked *about that spot*, and
an answer at the other end of the screen makes the eye do the work of joining
them up. It flips below the point when there is no room above, and slides rather
than leaving the viewport at the edges.
"""

from __future__ import annotations

import json

#: Ids. The bar is ``ui/cam_views.py``'s; the rest are ours.
BAR_ID = "piccola-view-bar"
BTN_ID = "piccola-measure-btn"
PANEL_ID = "piccola-measure-panel"

#: Class marking the button lit, matching the view bar's own selected state.
ON_CLASS = "piccola-view-on"

#: Button text and hover text.
BTN_LABEL = "measure"
BTN_TITLE = "click two points on the model to measure between them"

#: How far the pointer may travel between press and release and still count as
#: a click rather than an orbit. Four pixels is about the shake in a click made
#: while looking at something else.
CLICK_SLOP_PX = 4

#: How close to a triangle corner, *on screen*, a hit has to be for the point to
#: snap to it. Generous: the corner is nearly always what was being aimed at.
SNAP_PX = 14

#: Gap between the readout and the point it is describing, and the margin it
#: keeps from the window edges. One number: it is the same "don't touch" space
#: in both roles.
GAP_PX = 10

#: Marker radius, as a fraction of the largest model dimension, and the floor
#: under it in model units.
MARKER_FRACTION = 0.012
MARKER_MIN = 0.35

#: Colours. The first point is the app's cut blue, the second its link green,
#: and the line between them is drawn in the selection amber already used for
#: "this is what you picked" everywhere else in this app.
COLOR_A = 0x0A7FD4
COLOR_B = 0x2F9E44
COLOR_LINE = 0xD69A2D


def css() -> str:
    """Styling for the measure button and its readout."""
    return "\n".join([
        f"#{BTN_ID} {{ width: 100% !important; }}",
        # The readout sits directly above the view bar rather than beside the
        # cursor: a panel that follows the pointer is in the way of the second
        # click, which is the one that needs aiming.
        f"#{PANEL_ID} {{",
        # fixed, and parented to <body>: it is placed at a point in the
        # viewport, not inside the bar it used to hang off
        "  position: fixed !important;",
        "  z-index: 211 !important;",
        "  min-width: 132px !important;",
        "  padding: 6px 8px !important;",
        "  border-radius: 5px !important;",
        "  background: var(--menubar-back, #F0F0F0) !important;",
        "  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25) !important;",
        "  font: 11px/1.45 system-ui, -apple-system, 'Segoe UI', sans-serif"
        " !important;",
        "  color: #22282e !important;",
        "  text-align: left !important;",
        "  pointer-events: none !important;",
        "}",
        f"#{PANEL_ID} .piccola-measure-hint {{",
        "  opacity: 0.7 !important;",
        "}",
        f"#{PANEL_ID} .piccola-measure-dist {{",
        "  font-size: 16px !important;",
        "  font-weight: 600 !important;",
        "  letter-spacing: 0.01em !important;",
        "  margin: 1px 0 3px 0 !important;",
        "}",
        f"#{PANEL_ID} .piccola-measure-axes {{",
        "  display: grid !important;",
        "  grid-template-columns: auto auto !important;",
        "  column-gap: 8px !important;",
        "  opacity: 0.8 !important;",
        "}",
        f"#{PANEL_ID} .piccola-measure-axes b {{",
        "  font-weight: 600 !important;",
        "  opacity: 0.65 !important;",
        "}",
    ])


def measure_js() -> str:
    """JS installing the measure tool."""
    js = """
(function () {
  var BAR = %(bar)s, BTN = %(btn)s, PANEL = %(panel)s, ON = %(on)s;
  var LABEL = %(label)s, TITLE = %(title)s;
  var SLOP = %(slop)s, SNAP = %(snap)s, GAP = %(gap)s;
  var MARK_F = %(mark_f)s, MARK_MIN = %(mark_min)s;
  var C_A = %(c_a)s, C_B = %(c_b)s, C_LINE = %(c_line)s;

  var armed = false, pts = [], art = null, live = null;
  // the point under the pointer while the second click is still to come
  var preview = null, hover = null, raf = 0;
  // where the last pick landed on screen, which is where the readout goes
  var at = null;
  // press position and whether Kiri's hook already claimed this release
  var down = null, claimed = false, prevHook, held = false;

  function api() {
    try { return window.kiri.api; } catch (e) { return null; }
  }

  // camera + renderer, via patch 0020. Re-read every time: switching
  // projection builds a new camera and a resize can rebuild the renderer.
  function internals() {
    var a = api();
    try {
      var i = a.space.internals();
      return (i && i.camera && i.renderer) ? i : null;
    } catch (e) { return null; }
  }

  function redraw() {
    var a = api();
    try { a.space.update(); } catch (e) { }
  }

  // ------------------------------------------------------------------ frame

  // The group every widget hangs under -- rotated -90 about X, because the
  // model is Z-up and three.js is Y-up. Read off a live widget rather than
  // assumed, so this follows upstream instead of duplicating it.
  function frame() {
    var a = api();
    try {
      var m = a.widgets.all()[0].mesh;
      return m.parent || null;
    } catch (e) { return null; }
  }

  // world point -> the frame every number in the rest of the UI is quoted in
  function toModel(p) {
    var f = frame();
    return f ? f.worldToLocal(p.clone()) : p.clone();
  }

  function scene() {
    var a = api();
    try {
      var o = a.widgets.all()[0].mesh;
      while (o.parent) o = o.parent;
      return o;
    } catch (e) { return null; }
  }

  // ------------------------------------------------------------------ picks

  function ndc(clientX, clientY, ins) {
    var cr = ins.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(
      ((clientX - cr.left) / cr.width) * 2 - 1,
      -((clientY - cr.top) / cr.height) * 2 + 1
    );
  }

  // where a world point lands on screen, in client pixels
  function screenOf(p, ins) {
    var cr = ins.renderer.domElement.getBoundingClientRect();
    var v = p.clone().project(ins.camera);
    return {
      x: cr.left + (v.x + 1) / 2 * cr.width,
      y: cr.top + (-v.y + 1) / 2 * cr.height
    };
  }

  // Nearest corner of the triangle that was hit, if one is within SNAP pixels.
  // Corners are what people mean when they point at an edge, and screen
  // distance is the tolerance they are actually working to.
  function snap(hit, clientX, clientY, ins) {
    var f = hit.face, geo = hit.object.geometry;
    if (!f || !geo || !geo.attributes || !geo.attributes.position) return hit.point;
    var pos = geo.attributes.position, best = null, bestD = SNAP;
    var idx = [f.a, f.b, f.c];
    for (var i = 0; i < 3; i++) {
      var v = new THREE.Vector3().fromBufferAttribute(pos, idx[i]);
      hit.object.localToWorld(v);
      var s = screenOf(v, ins);
      var d = Math.hypot(s.x - clientX, s.y - clientY);
      if (d < bestD) { bestD = d; best = v; }
    }
    return best || hit.point;
  }

  // The bed, as a surface to land on. Kiri raycasts it the same way when it
  // needs a platform click (`space.js onMouseDown`): force it visible for the
  // cast, because three.js skips hidden objects, then put it back.
  function bedAt(x, y, ins) {
    var pf = ins.platform;
    if (!pf) return null;
    var was = pf.visible;
    pf.visible = true;
    var rc = new THREE.Raycaster();
    rc.setFromCamera(ndc(x, y, ins), ins.camera);
    var hit = rc.intersectObject(pf, true)[0];
    pf.visible = was;
    return hit ? hit.point.clone() : null;
  }

  // A plane through `through`, square to the view. Nothing in the scene is
  // under the pointer at this stage, so the only frame left is the screen.
  function planeAt(x, y, ins, through) {
    if (!through) return null;
    var rc = new THREE.Raycaster();
    rc.setFromCamera(ndc(x, y, ins), ins.camera);
    var n = new THREE.Vector3();
    ins.camera.getWorldDirection(n);
    var plane = new THREE.Plane().setFromNormalAndCoplanarPoint(n, through);
    var out = new THREE.Vector3();
    return rc.ray.intersectPlane(plane, out) ? out : null;
  }

  function castAt(x, y, a, ins) {
    var rc = new THREE.Raycaster();
    rc.setFromCamera(ndc(x, y, ins), ins.camera);
    return rc.intersectObjects(a.widgets.meshes(), false)[0] || null;
  }

  function pick(clientX, clientY, deep) {
    var a = api(), ins = internals();
    if (!a || !ins) return null;
    var hit = castAt(clientX, clientY, a, ins);
    // A miss right on the outline is the common case at a corner, which is
    // where this tool gets pointed most. Sweep a ring outwards rather than
    // giving up: nearest first, so a genuine hit close by still wins.
    if (!hit && deep) {
      for (var r = 2; r <= SNAP && !hit; r += 3) {
        for (var k = 0; k < 8 && !hit; k++) {
          var t = k / 8 * 2 * Math.PI;
          hit = castAt(clientX + Math.cos(t) * r, clientY + Math.sin(t) * r, a, ins);
        }
      }
    }
    if (!hit) {
      // off the part: the bed, then the plane of the point already placed
      return bedAt(clientX, clientY, ins)
          || planeAt(clientX, clientY, ins, pts.length === 1 ? pts[0] : null);
    }
    return snap(hit, clientX, clientY, ins);
  }

  // ---------------------------------------------------------------- drawing

  // Marker size follows the model, so it is neither a speck on a big part nor
  // a boulder on a small one.
  function markerSize() {
    var a = api(), big = 0;
    try {
      var all = a.widgets.all();
      for (var i = 0; i < all.length; i++) {
        var b = new THREE.Box3().setFromObject(all[i].mesh);
        big = Math.max(big, b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z);
      }
    } catch (e) { }
    return Math.max(MARK_MIN, big * MARK_F);
  }

  function dot(p, color, r) {
    var m = new THREE.Mesh(
      new THREE.SphereGeometry(r, 16, 12),
      // depthTest off: a point on the far face, or down inside a pocket, is
      // exactly the kind of point worth measuring from
      new THREE.MeshBasicMaterial({ color: color, depthTest: false })
    );
    m.position.copy(p);
    m.renderOrder = 10000;
    return m;
  }

  function drop(g) {
    if (!g) return;
    g.traverse(function (o) {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose();
    });
  }

  function clearLive() {
    var s = scene();
    if (live && s) s.remove(live);
    drop(live);
    live = null;
  }

  // The rubber band: dashed, so it reads as "not settled yet" beside the solid
  // line a finished measurement leaves behind.
  function drawLive(p) {
    clearLive();
    var s = scene();
    if (!s || pts.length !== 1) return;
    var r = markerSize();
    live = new THREE.Group();
    var g = dot(p, C_B, r * 0.72);
    g.material.transparent = true;
    g.material.opacity = 0.7;
    live.add(g);
    var line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([pts[0], p]),
      new THREE.LineDashedMaterial({
        color: C_LINE, depthTest: false,
        dashSize: Math.max(r * 1.6, 0.6), gapSize: Math.max(r * 1.1, 0.4)
      })
    );
    // dashes are computed from vertex distances, not for free
    line.computeLineDistances();
    line.renderOrder = 10000;
    live.add(line);
    s.add(live);
    redraw();
  }

  function clearArt() {
    var s = scene();
    if (art && s) s.remove(art);
    drop(art);
    art = null;
  }

  function draw() {
    clearArt();
    var s = scene();
    if (!s || !pts.length) { redraw(); return; }
    var r = markerSize();
    art = new THREE.Group();
    art.add(dot(pts[0], C_A, r));
    if (pts.length > 1) {
      art.add(dot(pts[1], C_B, r));
      var line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([pts[0], pts[1]]),
        new THREE.LineBasicMaterial({ color: C_LINE, depthTest: false })
      );
      line.renderOrder = 10000;
      art.add(line);
    }
    s.add(art);
    redraw();
  }

  // --------------------------------------------------------------- readout

  function units() {
    var a = api();
    try { return a.view.unit_scale() === 25.4 ? 'in' : 'mm'; } catch (e) { return 'mm'; }
  }

  function scaleOf() {
    var a = api();
    try { return a.view.unit_scale() || 1; } catch (e) { return 1; }
  }

  function fmt(v) {
    var n = Math.abs(v) < 10 ? 3 : 2;
    return (+v.toFixed(n)).toString();
  }

  function panel() {
    var el = document.getElementById(PANEL);
    if (el) return el;
    el = document.createElement('div');
    el.id = PANEL;
    document.body.appendChild(el);
    return el;
  }

  // Just above the point that was clicked, flipping below it when the top of
  // the window is in the way and sliding along rather than going off the side.
  // Measured after the content is in, because the size follows the text.
  function place(el) {
    var w = el.offsetWidth, h = el.offsetHeight;
    var p = at || { x: window.innerWidth - GAP - w / 2, y: window.innerHeight - GAP };
    var x = p.x - w / 2, y = p.y - h - GAP;
    if (y < GAP) y = p.y + GAP;
    x = Math.max(GAP, Math.min(x, window.innerWidth - w - GAP));
    y = Math.max(GAP, Math.min(y, window.innerHeight - h - GAP));
    el.style.left = Math.round(x) + 'px';
    el.style.top = Math.round(y) + 'px';
  }

  function report() {
    var el = panel();
    if (!el) return;
    el.style.display = armed ? 'block' : 'none';
    if (!armed) return;
    // the far end is whichever exists: the committed second point, or the one
    // under the pointer on the way to it
    var far = pts.length > 1 ? pts[1] : (pts.length === 1 ? preview : null);
    if (!pts.length || !far) {
      el.innerHTML = '';
      var hint = document.createElement('div');
      hint.className = 'piccola-measure-hint';
      hint.textContent = pts.length
        ? 'click the second point'
        : 'click a point on the model';
      el.appendChild(hint);
      place(el);
      return;
    }
    var s = scaleOf();
    var a = toModel(pts[0]), b = toModel(far);
    var dx = (b.x - a.x) / s, dy = (b.y - a.y) / s, dz = (b.z - a.z) / s;
    var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
    var u = units();
    el.innerHTML = '';
    var d = document.createElement('div');
    d.className = 'piccola-measure-dist';
    d.textContent = fmt(dist) + ' ' + u;
    el.appendChild(d);
    var g = document.createElement('div');
    g.className = 'piccola-measure-axes';
    [['X', dx], ['Y', dy], ['Z', dz]].forEach(function (row) {
      var k = document.createElement('b');
      k.textContent = row[0];
      var v = document.createElement('span');
      // magnitudes: the sign is a statement about which point was clicked
      // first, which is not something anyone measured on purpose
      v.textContent = fmt(Math.abs(row[1]));
      g.appendChild(k);
      g.appendChild(v);
    });
    el.appendChild(g);
    place(el);
  }

  // --------------------------------------------------------------- gesture

  function add(p, x, y) {
    if (pts.length >= 2) pts = [];
    pts.push(p);
    at = { x: x, y: y };
    // the band belongs to the point that is still being chosen; once a second
    // point exists there is a real line to look at instead
    preview = null;
    clearLive();
    draw();
    report();
  }

  // Kiri's own hook, called for a click that hit something. Returning nothing
  // is what tells it this click was not a selection.
  //
  // What the hook is used for is *that* this click is a pick. Not *where*: the
  // intersection it hands over arrives with `point` at the origin -- upstream's
  // own users of this hook read only `face` and `object`, so nothing there ever
  // needed it filled in. Trusting it put both markers at 0,0,0 and reported
  // every measurement as zero. So the position comes from our own cast at the
  // same pixel, which is the one the snapping works from anyway.
  function onHook(obj, ev) {
    if (!armed || !obj || !ev) return;
    claimed = true;
    var p = pick(ev.clientX, ev.clientY, true);
    if (p) add(p, ev.clientX, ev.clientY);
  }

  // At most one cast per frame, straight at the pointer -- see the note on the
  // preview in the module docstring.
  function refreshLive() {
    raf = 0;
    if (!armed || pts.length !== 1 || !hover) return;
    var p = pick(hover.x, hover.y, false);
    preview = p;
    at = hover;
    if (p) drawLive(p); else { clearLive(); redraw(); }
    report();
  }

  function onMove(ev) {
    if (!armed || pts.length !== 1) return;
    var ins = internals();
    if (!ins || ev.target !== ins.renderer.domElement) return;
    hover = { x: ev.clientX, y: ev.clientY };
    if (raf) return;
    raf = requestAnimationFrame(refreshLive);
  }

  function onDown(ev) {
    if (!armed || ev.button !== 0) return;
    claimed = false;
    down = { x: ev.clientX, y: ev.clientY };
  }

  // Only for the click the hook did not claim -- a miss, which is what aiming
  // at a corner produces. Passive on purpose: cancelling or stopping this
  // event is what left the view control stuck mid-drag the first time round.
  function onUp(ev) {
    if (!armed) return;
    var start = down;
    down = null;
    if (!start) return;
    if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > SLOP) return;
    var ins = internals();
    if (!ins || ev.target !== ins.renderer.domElement) return;
    var x = ev.clientX, y = ev.clientY;
    // Deferred, because Kiri's handlers are on `window` and this one is on
    // `document` -- so the hook has not run yet and `claimed` is still false
    // here. A tick later everything synchronous is done and the flag is true.
    setTimeout(function () {
      var was = claimed;
      claimed = false;
      if (was || !armed) return;
      var p = pick(x, y, true);
      if (p) add(p, x, y);
    }, 0);
  }

  function onKey(ev) {
    if (armed && ev.key === 'Escape') { arm(false); }
  }

  // Leaving the view puts everything away -- see the note on drawing.
  // Guarded so the event firing during init is a no-op.
  function onView() {
    if (armed || art || live || pts.length) arm(false);
  }

  function arm(on) {
    var a = api();
    armed = !!on;
    pts = [];
    at = null;
    preview = null;
    hover = null;
    clearLive();
    // take the hook while armed and hand it back afterwards, so a picker that
    // was already using it is not quietly dropped
    try {
      if (armed) {
        if (!held) { prevHook = a.feature.on_mouse_up; held = true; }
        a.feature.on_mouse_up = onHook;
      } else if (held) {
        // restore even when what was there was `undefined` -- that is the
        // normal state, and a separate flag is the only way to tell "nothing
        // was installed" from "nothing has been saved yet"
        a.feature.on_mouse_up = prevHook;
        prevHook = undefined;
        held = false;
      }
    } catch (e) { }
    clearArt();
    redraw();
    var b = document.getElementById(BTN);
    if (b) b.classList.toggle(ON, armed);
    var ins = internals();
    if (ins) ins.renderer.domElement.style.cursor = armed ? 'crosshair' : '';
    report();
  }

  // ------------------------------------------------------------------ setup

  function install() {
    var bar = document.getElementById(BAR);
    if (!bar) return false;
    if (document.getElementById(BTN)) return true;
    var b = document.createElement('button');
    b.id = BTN;
    b.textContent = LABEL;
    b.setAttribute('title', TITLE);
    b.onclick = function () { arm(!armed); };
    bar.appendChild(b);
    // bubble phase, after Kiri has had the event: these only notice a click
    // its hook did not claim, and never interfere with one it did
    document.addEventListener('mousedown', onDown, false);
    document.addEventListener('mouseup', onUp, false);
    document.addEventListener('mousemove', onMove, false);
    document.addEventListener('keydown', onKey, true);
    try { api().event.on('view.set', onView); } catch (e) { }
    return true;
  }

  // Counting what exists, not what is missing: injected at document creation,
  // a test for outstanding work reads as "finished" on the first tick.
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
        "panel": json.dumps(PANEL_ID),
        "on": json.dumps(ON_CLASS),
        "label": json.dumps(BTN_LABEL),
        "title": json.dumps(BTN_TITLE),
        "slop": json.dumps(CLICK_SLOP_PX),
        "snap": json.dumps(SNAP_PX),
        "gap": json.dumps(GAP_PX),
        "mark_f": json.dumps(MARKER_FRACTION),
        "mark_min": json.dumps(MARKER_MIN),
        "c_a": hex(COLOR_A),
        "c_b": hex(COLOR_B),
        "c_line": hex(COLOR_LINE),
    }
    assert "%" not in js, "unescaped % survived formatting in measure_js()"
    return js
