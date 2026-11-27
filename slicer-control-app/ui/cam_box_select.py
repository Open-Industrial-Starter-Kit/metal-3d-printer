"""
ui/cam_box_select.py
AutoCAD-style rubber-band box selection for the Kiri:Moto workspace.

Why
---
The multi-selection model was already there and complete — ``Ctrl/Cmd+A``
selects everything, ``shift+click`` toggles one part, and Duplicate, Mirror,
Rotate and Delete all act on the selected set. The only thing missing was a way
to *define* that set spatially. Picking eight parts out of twenty meant eight
shift+clicks and no mistakes.

So: drag a rectangle, get the parts.

The rule — direction decides the mode
-------------------------------------
Straight from AutoCAD, because it is the convention anyone who has drawn a
selection box before already has in their fingers:

* **left → right** is a **window**. A part is selected only if it lies
  *entirely* inside the box. Drawn as a solid outline with a blue fill.
* **right → left** is a **crossing**. A part is selected if it is inside the
  box *or if the box touches it at all*. Drawn dashed, with a green fill.

Only the sign of ``x_end - x_start`` matters; vertical direction is ignored, so
up-right and down-right are both Window. The mode is recomputed on every mouse
move, which means dragging back across the start point flips the box style
live — that is the whole affordance. The user does not pick a mode from a menu,
they pick it by watching the box change and letting go when it is the one they
want.

Screen space, not model space
-----------------------------
The rectangle is 2D and the scene is 3D, so the test has to run against each
part's *projection* under the live camera.

The tempting shortcut — project the part's bounding box, take the axis-aligned
screen bounds of that, and test the rectangle against it — is wrong, and wrong
in the direction users notice. Under perspective and rotation an L-shaped or
diagonal part has screen bounds far larger than the part itself, so Crossing
grabs things the box visibly never touched. This module never does that test.

What it does instead, per part, in three stages that get more expensive and
more exact:

1. **Convex hull of the projected bounding-box corners.** Eight points, a
   monotone-chain hull, then a separating-axis test against the rectangle. The
   projected geometry is always a subset of this hull, so *disjoint means
   rejected* — exactly, not approximately.
2. **Hull entirely inside the rectangle** means the geometry is too. Accept,
   both modes, without touching a single triangle.
3. Otherwise the hull straddles an edge and the cheap stages cannot answer, so
   fall through to the real geometry: for Window, every projected vertex must
   be inside; for Crossing, any vertex inside, any triangle edge crossing a
   rectangle edge, or the rectangle sitting wholly within one projected
   triangle.

Stage 3 is the only expensive one, it runs only for parts the first two stages
could not settle, and it runs on mouse *up* rather than on every mouse move.
Nothing is highlighted mid-drag, so nothing has to be recomputed mid-drag.

Because the test uses the live camera every time, it is stable under orbit:
turn the view ninety degrees, draw the same rectangle over the same visible
area, get the same parts.

Two things to select
--------------------
"Select" means different things depending on what is armed, and the box follows
that rather than inventing a third meaning.

Normally it selects **parts**, as above. But when an operation's surface picker
is open — the *select* button on a pocket, area or helical op — clicking a face
picks a whole connected **surface**: Kiri raycasts the click, derives
``faceid = min(face.a, face.b, face.c) / 3``, and asks the worker to grow the
region out to the op's edge angle. That is why one click on a dome gets the
dome and not one triangle.

So when that picker is armed, the box picks surfaces instead. It does not
reimplement any of it: the picker arms itself by installing
``api.feature.on_mouse_up``, and that handler wants a raycast intersection —
which is something this module can produce. The box simply replays the clicks
the user would otherwise have made, one per distinct surface it covers.

Two consequences worth knowing:

* **Sampling, not triangle intersection.** Surfaces are found by raycasting a
  grid of points inside the rectangle and taking the *nearest* hit at each. So
  what gets picked is what the box actually covers on screen. Testing triangles
  against the rectangle instead would also select the pocket floor and the far
  walls hiding behind the thing you drew the box around.
* **Window still means "entirely inside"**, but a surface is only known once
  the worker has grown it — so they are grown first and any that overhang the
  box are toggled straight back off.

``surface_toggle`` is a toggle, so a face already inside a picked group is
skipped rather than clicked again, and the round trip to the worker is waited
out (by watching the widget's ``_surfaces`` map) before moving to the next
sample.

The binding
-----------
Every other drag on this canvas is taken — plain left orbits, right pans, meta
pans, middle zooms, ctrl/cmd and alt draw and erase support columns in FDM.
``shift`` is free, and it already means *selection* here (shift+click toggles a
part), so shift+drag reads as "select a region" without teaching anything new.
Orbit stays on plain left-drag, untouched.

    shift + drag          draw the box, replacing the selection
    shift + alt + drag    draw the box, removing the matched parts

Taking the gesture away from orbit
----------------------------------
Kiri's orbit control binds ``mousedown`` on the container and only then
attaches its own ``mousemove``/``mouseup`` to the document. So stopping the
gesture at ``mousedown`` — one capture-phase listener on ``document``, which
runs before any listener further down — means the orbit control never starts,
never attaches anything, and needs no "please stop" API. `moto/space.js`'s own
window-level handlers are stopped by the same call, which is what keeps a
shift-drag from also dragging the parts under the cursor.

The cost is that ``shift+click`` — a sub-threshold shift-drag — gets swallowed
too, so this module has to put it back: under ``MIN_DRAG_PX`` of movement it
raycasts the click itself and calls ``platform.select(widget, true)``, which is
precisely what the handler it displaced would have done.

Vendor surface
--------------
One line, in ``0020-expose-space-internals.patch``: ``api.space.internals()``,
which is how this module reaches the camera. Everything else is here, so the
gesture can be changed with a relaunch rather than a re-vendor.
"""

from __future__ import annotations

import json

#: Movement below this (in CSS pixels, the larger of the two axes) is a click,
#: not a box. Matches the "~4 px" in the request; low enough that a deliberate
#: small box still works, high enough to absorb the wobble in a click.
MIN_DRAG_PX = 4

#: Id on the overlay rectangle. Doubles as the "already installed" check, so a
#: re-run cannot leave two boxes on screen.
OVERLAY_ID = "piccola-box-select"

#: The modifier that turns the drag into a subtraction. Read at mouse *up*
#: rather than at mouse down, so it can be decided during the drag like the
#: window/crossing mode can. The two published styles below are mode-only, so
#: this does not change how the box looks.
SUBTRACT_MODIFIER = "altKey"

#: Spacing of the raycast sample grid, in canvas pixels, when a face picker is
#: armed. Surfaces grown by the worker are large regions, not single triangles,
#: so this only has to be fine enough to land one sample on each one.
SURFACE_SAMPLE_PX = 4

#: Ceiling on samples per drag. A box over the whole viewport at 4 px would be
#: ~80k raycasts; past this the step doubles until it fits.
SURFACE_MAX_SAMPLES = 2500

#: How long to wait for one surface to come back from the worker before moving
#: on, and how often to look. `surface_toggle` round-trips to grow the region,
#: so the result is not there when the picker's handler returns.
SURFACE_SETTLE_MS = 1500
SURFACE_SETTLE_STEP_MS = 20

#: The toast shown after a box picks surfaces, and how long it stays.
#: Long enough to read and act on, short enough not to sit over the model.
SURFACE_TOAST_ID = "piccola-surface-toast"
SURFACE_TOAST_MS = 6000

#: Distance from the bottom of the window. Clears the transport bar, which is
#: the only thing that lives down there in arrange.
SURFACE_TOAST_BOTTOM_PX = 90

#: Buttons on it. *Reselect* clears every picked surface and leaves the picker
#: armed, so the next drag starts from nothing; *Keep* just dismisses.
RESELECT_LABEL = "Reselect"
KEEP_LABEL = "Keep"

#: The always-available reset. The toast is transient by design, so once it
#: fades the only way back to an empty selection was to click every picked
#: surface off again. This sits in the same place and stays for as long as the
#: picker is armed and anything is picked.
SURFACE_PILL_ID = "piccola-surface-cancel"
CANCEL_LABEL = "Cancel selections"

#: How often to re-check whether it should be on screen. There is no event for
#: "the picker closed" or "a surface was clicked off", so this is polled --
#: cheaply, and only while the page is up.
SURFACE_PILL_POLL_MS = 400

#: The *select all surfaces* button, and the button it is placed above.
#: Anchoring to the label means it appears only on ops that actually have a
#: surface picker -- the area op's equivalent is called "surface" and is not
#: matched, which is correct: it sits beside an "edge" button where "all" would
#: be ambiguous.
SELECT_ALL_ID = "piccola-select-all-surfaces"
SELECT_ALL_LABEL = "select all surfaces"
SELECT_ALL_ANCHOR = "select surfaces"

#: Ceiling on how many surface groups one *select all* may create. Each one is
#: a round trip to the worker, so a model whose every triangle is its own
#: region would otherwise sit there grinding. Far above any real part: the
#: sphere-in-a-pocket in the screenshots is 7.
SELECT_ALL_MAX_GROUPS = 400

#: The hint that rides alongside Kiri's own "[esc] cancels surface selection"
#: alert. Box selection is invisible until someone tries it, and the moment the
#: picker opens is exactly when it is worth knowing about.
SURFACE_TIP_ID = "piccola-surface-tip"
SURFACE_TIP_TEXT = "Shift+drag to select"

#: Gap between the alert and the tip, in pixels.
SURFACE_TIP_GAP_PX = 8

#: Only ride along with the alert that means surface picking -- the alert area
#: is shared, and the tip has nothing to say about a slice progress message.
SURFACE_TIP_MATCH = "surface selection"

#: How each mode draws. Solid + blue for window, dashed + green for crossing —
#: the AutoCAD convention, and the only thing distinguishing the two on screen,
#: so they must not converge on a re-theme.
STYLES = {
    "window": {
        "border": "1px solid #2f6fd0",
        "fill": "rgba(80, 150, 230, 0.18)",
    },
    "crossing": {
        "border": "1px dashed #2f9e4f",
        "fill": "rgba(90, 200, 120, 0.18)",
    },
}


def box_select_js() -> str:
    """JS installing the shift+drag box-selection gesture."""
    js = """
(function () {
  var MIN_DRAG = %(min_drag)s;
  var OVERLAY_ID = %(overlay_id)s;
  var STYLES = %(styles)s;
  var SUBTRACT = %(subtract)s;
  var SAMPLE_PX = %(sample_px)s, MAX_SAMPLES = %(max_samples)s;
  var SETTLE_MS = %(settle_ms)s, SETTLE_STEP_MS = %(settle_step_ms)s;
  var TOAST_ID = %(toast_id)s, TOAST_MS = %(toast_ms)s;
  var TOAST_BOTTOM_PX = %(toast_bottom)s;
  var RESELECT_LABEL = %(reselect_label)s, KEEP_LABEL = %(keep_label)s;
  var PILL_ID = %(pill_id)s, CANCEL_LABEL = %(cancel_label)s;
  var PILL_POLL_MS = %(pill_poll_ms)s;
  var TIP_ID = %(tip_id)s, TIP_TEXT = %(tip_text)s;
  var TIP_GAP = %(tip_gap)s, TIP_MATCH = %(tip_match)s;
  var ALL_ID = %(all_id)s, ALL_LABEL = %(all_label)s;
  var ALL_ANCHOR = %(all_anchor)s, ALL_MAX = %(all_max)s;
  var WINDOW_MODE = 'window', CROSSING_MODE = 'crossing';

  function api() {
    try { return window.kiri.api; } catch (e) { return null; }
  }

  // camera + renderer, via the one line patch 0020 adds. Re-read every time:
  // switching between perspective and orthographic builds a new camera, and
  // a resize can rebuild the renderer.
  function internals() {
    var a = api();
    try {
      var i = a.space.internals();
      return (i && i.camera && i.renderer) ? i : null;
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- gesture

  var drag = null;

  function takes(ev, ins) {
    var a = api();
    if (!a || ev.button !== 0 || !ev.shiftKey) return false;
    // ctrl/cmd is lay-flat and pan; alt alone paints supports
    if (ev.ctrlKey || ev.metaKey) return false;
    // support painting owns the drag while it is on
    if (a.feature && a.feature.hover) return false;
    // selection only means anything in arrange, which is also where
    // platform.select() agrees to do anything
    try { if (!a.view.is_arrange()) return false; } catch (e) { return false; }
    return ev.target === ins.renderer.domElement;
  }

  function onDown(ev) {
    var ins = internals();
    if (!ins || !takes(ev, ins)) return;
    ev.preventDefault();
    // Runs before the orbit control's own mousedown, so it never starts a
    // rotate and never attaches its document-level move/up handlers.
    ev.stopPropagation();
    drag = { x0: ev.clientX, y0: ev.clientY, x1: ev.clientX, y1: ev.clientY, box: null };
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);
    window.addEventListener('blur', cancel, true);
  }

  // takes the drag record rather than reading the live one: onUp asks this
  // after teardown() has already cleared it
  function far(d, x, y) {
    return Math.max(Math.abs(x - d.x0), Math.abs(y - d.y0)) >= MIN_DRAG;
  }

  function modeOf(x1) {
    return x1 >= drag.x0 ? WINDOW_MODE : CROSSING_MODE;
  }

  function onMove(ev) {
    if (!drag) return;
    drag.x1 = ev.clientX;
    drag.y1 = ev.clientY;
    if (!far(drag, drag.x1, drag.y1)) return;
    ev.preventDefault();
    paint();
  }

  function paint() {
    var b = drag.box;
    if (!b) {
      b = drag.box = document.createElement('div');
      b.id = OVERLAY_ID;
      // fixed, not absolute: the canvas sits inside panels that scroll and
      // shift, and clientX/clientY are already viewport coordinates
      b.style.cssText = 'position:fixed;pointer-events:none;box-sizing:border-box;'
        + 'z-index:2147483000;';
      document.body.appendChild(b);
    }
    var s = STYLES[modeOf(drag.x1)];
    b.style.border = s.border;
    b.style.background = s.fill;
    b.style.left = Math.min(drag.x0, drag.x1) + 'px';
    b.style.top = Math.min(drag.y0, drag.y1) + 'px';
    b.style.width = Math.abs(drag.x1 - drag.x0) + 'px';
    b.style.height = Math.abs(drag.y1 - drag.y0) + 'px';
  }

  function teardown() {
    document.removeEventListener('mousemove', onMove, true);
    document.removeEventListener('mouseup', onUp, true);
    window.removeEventListener('blur', cancel, true);
    var d = drag;
    drag = null;
    if (d && d.box && d.box.parentNode) d.box.parentNode.removeChild(d.box);
    return d;
  }

  // a mouseup lost outside the window would otherwise leave the box on screen
  function cancel() { teardown(); }

  function onUp(ev) {
    var d = teardown();
    if (!d) return;
    ev.preventDefault();
    ev.stopPropagation();
    // the shift+click this gesture displaced at mousedown
    if (!far(d, ev.clientX, ev.clientY)) return clickSelect(ev);
    d.x1 = ev.clientX;
    d.y1 = ev.clientY;
    var mode = d.x1 >= d.x0 ? WINDOW_MODE : CROSSING_MODE;
    var sub = !!ev[SUBTRACT];
    // an armed face picker changes what "select" means: surfaces on one part,
    // not whole parts
    if (pickerArmed()) return applyFaces(rectOf(d), mode, sub, ev);
    apply(rectOf(d), mode, sub);
  }

  // The op surface/cylinder pickers arm themselves by installing
  // api.feature.on_mouse_up, and that handler takes a raycast intersection --
  // exactly what this module can produce. So rather than reimplementing
  // surface growing, the box replays the clicks the user would have made.
  function pickerArmed() {
    var a = api();
    return !!(a && a.feature && typeof a.feature.on_mouse_up === 'function');
  }

  // Picking a surface is asynchronous -- every one waits on the worker -- so a
  // second drag, or a Reselect press, can land while the first is still
  // running. Two loops toggling the same groups interleave and leave a
  // selection that matches neither gesture. Everything that touches surfaces
  // therefore goes through one queue.
  var chain = Promise.resolve();

  function serial(fn) {
    chain = chain.then(fn, fn);
    return chain;
  }

  // rectangle in canvas pixels, plus the canvas size the projection needs
  function rectOf(d) {
    var cr = internals().renderer.domElement.getBoundingClientRect();
    return {
      x0: Math.min(d.x0, d.x1) - cr.left,
      x1: Math.max(d.x0, d.x1) - cr.left,
      y0: Math.min(d.y0, d.y1) - cr.top,
      y1: Math.max(d.y0, d.y1) - cr.top,
      w: cr.width,
      h: cr.height
    };
  }

  function clickSelect(ev) {
    var a = api(), ins = internals();
    if (!a || !ins) return;
    var hit = rayAt(ev.clientX, ev.clientY);
    if (pickerArmed()) {
      // hand the picker the click it was waiting for
      if (hit) a.feature.on_mouse_up(hit, ev);
      return;
    }
    if (hit && hit.object && hit.object.widget) {
      // true == shift, which is what this click was
      a.platform.select(hit.object.widget, true);
    } else {
      a.platform.deselect();
    }
  }

  // nearest widget intersection under a viewport point
  function rayAt(clientX, clientY) {
    var a = api(), ins = internals();
    var cr = ins.renderer.domElement.getBoundingClientRect();
    var rc = new THREE.Raycaster();
    rc.setFromCamera(new THREE.Vector2(
      ((clientX - cr.left) / cr.width) * 2 - 1,
      -((clientY - cr.top) / cr.height) * 2 + 1
    ), ins.camera);
    return rc.intersectObjects(a.widgets.meshes(), false)[0] || null;
  }

  // ---------------------------------------------------------------- surfaces

  // Which surface groups a widget currently holds. The pickers keep them on
  // the widget as { rootFaceId: [faceId, ...] }.
  function groupsOf(w) { return (w && w._surfaces) || {}; }

  function pickedAlready(w, faceid) {
    var g = groupsOf(w);
    for (var k in g) if (g[k] && g[k].indexOf(faceid) >= 0) return true;
    return false;
  }

  function groupCount(w) { return Object.keys(groupsOf(w)).length; }

  // surface_toggle round-trips to the worker to grow the region, so the result
  // is not there when on_mouse_up returns. Wait for the group count to move.
  function settle(w, before) {
    return new Promise(function (done) {
      var waited = 0;
      (function poll() {
        if (groupCount(w) !== before || waited >= SETTLE_MS) return done();
        waited += SETTLE_STEP_MS;
        setTimeout(poll, SETTLE_STEP_MS);
      })();
    });
  }

  // every vertex of every face in the group, inside the rectangle?
  function groupInside(w, root, r) {
    var faces = groupsOf(w)[root] || [];
    var pos = w.mesh.geometry.attributes.position;
    if (!pos) return false;
    var ax = pos.array, its = pos.itemSize;
    var cam = internals().camera;
    var proj = projector(w.mesh, cam, r), o = {}, i, v, k;
    for (i = 0; i < faces.length; i++) {
      for (v = 0; v < 3; v++) {
        k = (faces[i] * 3 + v) * its;
        proj(ax[k], ax[k + 1], ax[k + 2], o);
        if (o.behind || !inRect(o.x, o.y, r)) return false;
      }
    }
    return faces.length > 0;
  }

  // Sampling, not triangle intersection, and on purpose: a raycast returns the
  // NEAREST hit, so what gets picked is what the box actually covers on
  // screen. Testing triangles against the rectangle would also select the
  // pocket floor and the far walls hiding behind the dome.
  function samplesIn(r) {
    var w = Math.max(1, r.x1 - r.x0), h = Math.max(1, r.y1 - r.y0);
    var step = SAMPLE_PX;
    while (((w / step) + 1) * ((h / step) + 1) > MAX_SAMPLES) step *= 2;
    var out = [], x, y;
    for (y = r.y0; y <= r.y1; y += step)
      for (x = r.x0; x <= r.x1; x += step) out.push([x, y]);
    return out;
  }

  function applyFaces(r, mode, subtract, ev) {
    // a stale count from the last drag must not sit there during this one
    toastHide();
    return serial(function () { return applyFacesRun(r, mode, subtract, ev); });
  }

  async function applyFacesRun(r, mode, subtract, ev) {
    var a = api(), ins = internals();
    if (!a || !ins) return;
    var cr = ins.renderer.domElement.getBoundingClientRect();
    var pts = samplesIn(r), added = [], i;

    for (i = 0; i < pts.length; i++) {
      var hit = rayAt(pts[i][0] + cr.left, pts[i][1] + cr.top);
      if (!hit || !hit.object || !hit.object.widget) continue;
      var w = hit.object.widget;
      var faceid = Math.min(hit.face.a, hit.face.b, hit.face.c) / 3;
      var has = pickedAlready(w, faceid);
      // on_mouse_up TOGGLES, so only call it when the call does what we want
      if (subtract ? !has : has) continue;
      var keysBefore = Object.keys(groupsOf(w));
      a.feature.on_mouse_up(hit, ev);
      await settle(w, keysBefore.length);
      if (!subtract) {
        // Which group the worker just grew. Not "the last key": these are
        // integer-like, so the object iterates them in ascending numeric
        // order, not insertion order.
        var root = Object.keys(groupsOf(w)).filter(function (k) {
          return keysBefore.indexOf(k) < 0;
        })[0];
        if (root !== undefined) added.push({ w: w, root: root, hit: hit });
      }
    }

    // Window means "entirely inside", and the group is only known after it is
    // grown -- so grow first, then put back anything that overhangs the box.
    if (!subtract && mode === WINDOW_MODE) {
      for (i = 0; i < added.length; i++) {
        var rec = added[i];
        if (groupInside(rec.w, rec.root, r)) continue;
        var n = groupCount(rec.w);
        a.feature.on_mouse_up(rec.hit, ev);
        await settle(rec.w, n);
      }
    }
    a.space.update();
    showToast(ev);
  }

  // ------------------------------------------------------------------ toast

  // A box can pick more, or less, than it looked like it would -- occluded
  // surfaces, a window box rejecting something that overhung by a few pixels.
  // So say what landed, and offer to start over without hunting for a way to
  // undo it.
  var toastEl = null, toastTimer = null;

  function toastHide() {
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
    if (toastEl && toastEl.parentNode) toastEl.parentNode.removeChild(toastEl);
    toastEl = null;
  }

  function surfaceCount() {
    var a = api(), n = 0;
    a.widgets.each(function (w) { n += groupCount(w); });
    return n;
  }

  // Kiri's stylesheet reaches these elements, and a plain inline style does not
  // always win -- the first build of this came out stacked in the bottom-left
  // corner, on top of the settings panel. So every property that decides the
  // layout is set !important, and the position is measured rather than trusted
  // to text-align.
  function imp(el, props) {
    for (var k in props) el.style.setProperty(k, props[k], 'important');
    return el;
  }

  function toastButton(label) {
    var b = document.createElement('button');
    b.textContent = label;
    return imp(b, {
      'display': 'inline-block', 'margin': '0 0 0 8px', 'padding': '2px 10px',
      'font': 'inherit', 'line-height': 'normal', 'cursor': 'pointer',
      'border': '1px solid rgba(0,0,0,0.25)', 'border-radius': '3px',
      'background': '#fff', 'color': '#222', 'width': 'auto', 'height': 'auto',
      'vertical-align': 'middle', 'box-shadow': 'none',
      'text-transform': 'none', 'letter-spacing': 'normal'
    });
  }

  // Undo by replaying: toggling a group's root face off is the same call the
  // picker makes, so the op record stays in step with the widget.
  async function clearSurfaces(ev) {
    var a = api();
    if (!pickerArmed()) return;
    var ws = a.widgets.all(), i, j;
    for (i = 0; i < ws.length; i++) {
      var w = ws[i], roots = Object.keys(groupsOf(w));
      for (j = 0; j < roots.length; j++) {
        var f = parseInt(roots[j], 10);
        var before = groupCount(w);
        a.feature.on_mouse_up(
          { face: { a: f * 3, b: f * 3 + 1, c: f * 3 + 2 }, object: w.mesh }, ev);
        await settle(w, before);
      }
    }
    a.space.update();
  }

  function showToast(ev) {
    toastHide();
    var ins = internals();
    if (!ins) return;
    var n = surfaceCount();
    var box = toastEl = document.createElement('div');
    box.id = TOAST_ID;
    imp(box, {
      'position': 'fixed', 'display': 'block', 'white-space': 'nowrap',
      'z-index': '2147483000', 'box-sizing': 'border-box',
      'font': '13px system-ui,-apple-system,sans-serif', 'color': '#222',
      'background': 'rgba(255,255,255,0.96)',
      'border': '1px solid rgba(0,0,0,0.2)', 'border-radius': '4px',
      'padding': '7px 12px', 'box-shadow': '0 2px 8px rgba(0,0,0,0.2)',
      'left': '0px', 'top': 'auto', 'right': 'auto',
      'bottom': TOAST_BOTTOM_PX + 'px', 'width': 'auto', 'max-width': 'none'
    });
    var msg = imp(document.createElement('span'), {
      'display': 'inline-block', 'vertical-align': 'middle', 'color': '#222'
    });
    msg.textContent = n === 1 ? '1 surface selected'
      : (n ? n + ' surfaces selected' : 'no surfaces selected');
    var again = toastButton(RESELECT_LABEL);
    var keep = toastButton(KEEP_LABEL);
    again.onclick = function () {
      again.disabled = keep.disabled = true;
      msg.textContent = 'clearing...';
      serial(function () { return clearSurfaces(ev); }).then(toastHide, toastHide);
    };
    keep.onclick = toastHide;
    box.appendChild(msg);
    box.appendChild(again);
    box.appendChild(keep);
    document.body.appendChild(box);
    // centred on the canvas, not the window -- the left panel is not viewport
    center(box, ins);
    toastTimer = setTimeout(toastHide, TOAST_MS);
  }

  function center(box, ins) {
    var cr = ins.renderer.domElement.getBoundingClientRect();
    var left = cr.left + (cr.width - box.offsetWidth) / 2;
    box.style.setProperty('left', Math.max(4, Math.round(left)) + 'px', 'important');
  }

  // ------------------------------------------------------------------- pill

  // The toast says what just happened and then gets out of the way, which
  // leaves nothing to press once it has gone: clearing a selection meant
  // clicking every surface off again, one at a time. This is the standing
  // alternative -- present for as long as the picker is armed and something is
  // picked, saying how much, and offering to drop all of it.
  var pillEl = null, pillMsg = null;

  function pillHide() {
    if (pillEl && pillEl.parentNode) pillEl.parentNode.removeChild(pillEl);
    pillEl = pillMsg = null;
  }

  function pillBuild() {
    var box = pillEl = document.createElement('div');
    box.id = PILL_ID;
    imp(box, {
      'position': 'fixed', 'display': 'block', 'white-space': 'nowrap',
      'z-index': '2147482999', 'box-sizing': 'border-box',
      'font': '13px system-ui,-apple-system,sans-serif', 'color': '#222',
      'background': 'rgba(255,255,255,0.92)',
      'border': '1px solid rgba(0,0,0,0.18)', 'border-radius': '4px',
      'padding': '5px 10px', 'box-shadow': '0 1px 5px rgba(0,0,0,0.15)',
      'left': '0px', 'top': 'auto', 'right': 'auto',
      'bottom': TOAST_BOTTOM_PX + 'px', 'width': 'auto', 'max-width': 'none'
    });
    pillMsg = imp(document.createElement('span'), {
      'display': 'inline-block', 'vertical-align': 'middle', 'color': '#555'
    });
    var cancel = toastButton(CANCEL_LABEL);
    cancel.onclick = function (ev) {
      cancel.disabled = true;
      pillMsg.textContent = 'clearing...';
      serial(function () { return clearSurfaces(ev); }).then(pillSync, pillSync);
    };
    box.appendChild(pillMsg);
    box.appendChild(cancel);
    document.body.appendChild(box);
  }

  // No event fires for "the picker closed" or "that surface was clicked off",
  // so this is polled rather than pushed.
  function pillSync() {
    var ins = internals();
    var n = (ins && pickerArmed()) ? surfaceCount() : 0;
    // the toast owns the spot while it is up, and says the same thing
    if (!n || toastEl) return pillHide();
    if (!pillEl) pillBuild();
    var text = (n === 1 ? '1 surface selected' : n + ' surfaces selected');
    if (pillMsg.textContent !== text) pillMsg.textContent = text;
    center(pillEl, ins);
  }

  // -------------------------------------------------------------------- tip

  // Kiri already puts "[esc] cancels surface selection" at the top of the
  // viewport the moment the picker opens. That is the one moment someone is
  // looking for how this works, so the box gesture introduces itself there,
  // rather than in documentation nobody reads.
  var tipEl = null;

  function tipHide() {
    if (tipEl && tipEl.parentNode) tipEl.parentNode.removeChild(tipEl);
    tipEl = null;
  }

  function tipBuild() {
    var box = tipEl = document.createElement('div');
    box.id = TIP_ID;
    box.textContent = TIP_TEXT;
    imp(box, {
      'position': 'fixed', 'display': 'block', 'white-space': 'nowrap',
      'z-index': '2147482998', 'box-sizing': 'border-box',
      'font': '13px system-ui,-apple-system,sans-serif', 'color': '#555',
      'background': 'rgba(255,255,255,0.92)',
      'border': '1px solid rgba(0,0,0,0.18)', 'border-radius': '4px',
      'padding': '5px 10px', 'box-shadow': '0 1px 5px rgba(0,0,0,0.15)',
      'left': '0px', 'top': '0px', 'right': 'auto', 'bottom': 'auto',
      'width': 'auto', 'max-width': 'none', 'text-transform': 'none',
      'pointer-events': 'none'
    });
    document.body.appendChild(box);
  }

  // `alert.dialog` (#alert-area) is a full-width flex container -- anchoring to
  // it puts the tip a whole viewport to the right. `alert.text` is the pill you
  // can actually see, so that is what gets measured; the container still says
  // whether anything is showing, since display:none on it is how alerts.js puts
  // the whole thing away.
  function alertBox() {
    var a = api();
    try {
      var d = a.ui.alert.dialog, t = a.ui.alert.text;
      if (!d || !t || getComputedStyle(d).display === 'none') return null;
      return t.getBoundingClientRect().width ? t : null;
    } catch (e) { return null; }
  }

  function tipSync() {
    var dlg = alertBox();
    // the alert area is shared, so ride only the one that means picking
    if (!pickerArmed() || !dlg ||
        (dlg.innerText || '').toLowerCase().indexOf(TIP_MATCH) < 0) {
      return tipHide();
    }
    if (!tipEl) tipBuild();
    var r = dlg.getBoundingClientRect();
    var left = r.right + TIP_GAP;
    // fall in beneath rather than off the edge of a narrow window
    if (left + tipEl.offsetWidth > window.innerWidth - 4) {
      left = Math.max(4, r.left);
      tipEl.style.setProperty('top', Math.round(r.bottom + TIP_GAP) + 'px', 'important');
    } else {
      tipEl.style.setProperty(
        'top', Math.round(r.top + (r.height - tipEl.offsetHeight) / 2) + 'px', 'important');
    }
    tipEl.style.setProperty('left', Math.round(left) + 'px', 'important');
  }

  // ------------------------------------------------------------- projection

  // World -> canvas pixels for one mesh. The matrix is folded once per part
  // and the inner function stays arithmetic, because stage 3 runs it over
  // every vertex.
  function projector(mesh, camera, r) {
    var m = new THREE.Matrix4();
    m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    m.multiply(mesh.matrixWorld);
    var e = m.elements, hw = r.w / 2, hh = r.h / 2;
    return function (x, y, z, out) {
      var X = e[0] * x + e[4] * y + e[8] * z + e[12];
      var Y = e[1] * x + e[5] * y + e[9] * z + e[13];
      var W = e[3] * x + e[7] * y + e[11] * z + e[15];
      if (W <= 1e-6) { out.behind = true; return out; }
      out.behind = false;
      out.x = (X / W + 1) * hw;
      out.y = (-Y / W + 1) * hh;
      return out;
    };
  }

  function inRect(x, y, r) {
    return x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
  }

  // --------------------------------------------------------------- stage 1

  // monotone chain; returns the hull counter-clockwise in screen pixels
  function convexHull(pts) {
    if (pts.length < 3) return pts.slice();
    var p = pts.slice().sort(function (a, b) {
      return a[0] === b[0] ? a[1] - b[1] : a[0] - b[0];
    });
    var cross = function (o, a, b) {
      return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    };
    var lo = [], hi = [], i;
    for (i = 0; i < p.length; i++) {
      while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], p[i]) <= 0) lo.pop();
      lo.push(p[i]);
    }
    for (i = p.length - 1; i >= 0; i--) {
      while (hi.length >= 2 && cross(hi[hi.length - 2], hi[hi.length - 1], p[i]) <= 0) hi.pop();
      hi.push(p[i]);
    }
    lo.pop();
    hi.pop();
    return lo.concat(hi);
  }

  function boxHull(mesh, proj) {
    var geo = mesh.geometry;
    if (!geo.boundingBox) geo.computeBoundingBox();
    var b = geo.boundingBox;
    if (!b) return null;
    var xs = [b.min.x, b.max.x], ys = [b.min.y, b.max.y], zs = [b.min.z, b.max.z];
    var pts = [], o = {}, i;
    for (i = 0; i < 8; i++) {
      proj(xs[i & 1], ys[(i >> 1) & 1], zs[(i >> 2) & 1], o);
      // a corner behind the camera makes the projected hull meaningless;
      // stage 3 handles the part instead
      if (o.behind) return null;
      pts.push([o.x, o.y]);
    }
    return convexHull(pts);
  }

  // separating-axis test, convex hull against the axis-aligned rectangle
  function disjoint(hull, r) {
    var i, j, d, minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (i = 0; i < hull.length; i++) {
      if (hull[i][0] < minX) minX = hull[i][0];
      if (hull[i][0] > maxX) maxX = hull[i][0];
      if (hull[i][1] < minY) minY = hull[i][1];
      if (hull[i][1] > maxY) maxY = hull[i][1];
    }
    if (maxX < r.x0 || minX > r.x1 || maxY < r.y0 || minY > r.y1) return true;
    var corners = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
    for (i = 0; i < hull.length; i++) {
      var a = hull[i], b = hull[i + 1 === hull.length ? 0 : i + 1];
      var nx = -(b[1] - a[1]), ny = b[0] - a[0];
      var h0 = Infinity, h1 = -Infinity, c0 = Infinity, c1 = -Infinity;
      for (j = 0; j < hull.length; j++) {
        d = hull[j][0] * nx + hull[j][1] * ny;
        if (d < h0) h0 = d;
        if (d > h1) h1 = d;
      }
      for (j = 0; j < 4; j++) {
        d = corners[j][0] * nx + corners[j][1] * ny;
        if (d < c0) c0 = d;
        if (d > c1) c1 = d;
      }
      if (h1 < c0 || c1 < h0) return true;
    }
    return false;
  }

  // --------------------------------------------------------------- stage 3

  function exactWindow(mesh, proj, r) {
    var pos = mesh.geometry.attributes.position;
    if (!pos) return false;
    var ax = pos.array, its = pos.itemSize, n = pos.count, o = {}, i, k;
    for (i = 0; i < n; i++) {
      k = i * its;
      proj(ax[k], ax[k + 1], ax[k + 2], o);
      if (o.behind) return false;
      if (!inRect(o.x, o.y, r)) return false;
    }
    return true;
  }

  // Liang-Barsky: does the segment touch the rectangle at all (inside counts)
  function segHitsRect(x0, y0, x1, y1, r) {
    var dx = x1 - x0, dy = y1 - y0, t0 = 0, t1 = 1, i, p, q, t;
    var ps = [-dx, dx, -dy, dy];
    var qs = [x0 - r.x0, r.x1 - x0, y0 - r.y0, r.y1 - y0];
    for (i = 0; i < 4; i++) {
      p = ps[i];
      q = qs[i];
      if (p === 0) {
        if (q < 0) return false;
        continue;
      }
      t = q / p;
      if (p < 0) {
        if (t > t1) return false;
        if (t > t0) t0 = t;
      } else {
        if (t < t0) return false;
        if (t < t1) t1 = t;
      }
    }
    return true;
  }

  function pointInTri(px, py, ax, ay, bx, by, cx, cy) {
    var d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by);
    var d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy);
    var d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay);
    var neg = (d1 < 0) || (d2 < 0) || (d3 < 0);
    var pos = (d1 > 0) || (d2 > 0) || (d3 > 0);
    return !(neg && pos);
  }

  function exactCrossing(mesh, proj, r) {
    var geo = mesh.geometry;
    var pos = geo.attributes.position;
    if (!pos) return false;
    var ax = pos.array, its = pos.itemSize, n = pos.count;
    var sx = new Float64Array(n), sy = new Float64Array(n), ok = new Uint8Array(n);
    var o = {}, i, k;
    for (i = 0; i < n; i++) {
      k = i * its;
      proj(ax[k], ax[k + 1], ax[k + 2], o);
      if (o.behind) continue;
      ok[i] = 1;
      sx[i] = o.x;
      sy[i] = o.y;
      // any vertex inside the box is already a crossing
      if (inRect(o.x, o.y, r)) return true;
    }
    var idx = geo.index ? geo.index.array : null;
    var len = idx ? idx.length : n;
    for (i = 0; i + 2 < len; i += 3) {
      var a = idx ? idx[i] : i, b = idx ? idx[i + 1] : i + 1, c = idx ? idx[i + 2] : i + 2;
      if (!ok[a] || !ok[b] || !ok[c]) continue;
      if (segHitsRect(sx[a], sy[a], sx[b], sy[b], r)) return true;
      if (segHitsRect(sx[b], sy[b], sx[c], sy[c], r)) return true;
      if (segHitsRect(sx[c], sy[c], sx[a], sy[a], r)) return true;
      // no edge crossed and no vertex inside: the only way left to be touching
      // is the box sitting wholly inside this one projected triangle
      if (pointInTri(r.x0, r.y0, sx[a], sy[a], sx[b], sy[b], sx[c], sy[c])) return true;
    }
    return false;
  }

  // -------------------------------------------------------------- the test

  function hits(mesh, camera, r, mode) {
    var proj = projector(mesh, camera, r);
    var hull = boxHull(mesh, proj);
    if (hull && hull.length) {
      // the projected geometry is a subset of this hull, so this rejection
      // is exact rather than conservative
      if (disjoint(hull, r)) return false;
      var all = true;
      for (var i = 0; i < hull.length; i++) {
        if (!inRect(hull[i][0], hull[i][1], r)) { all = false; break; }
      }
      // hull inside the box means every vertex is: both modes accept
      if (all) return true;
    }
    return mode === WINDOW_MODE
      ? exactWindow(mesh, proj, r)
      : exactCrossing(mesh, proj, r);
  }

  function selectable(w) {
    if (!w || !w.mesh || !w.mesh.geometry) return false;
    if (typeof w.isVisible === 'function' && !w.isVisible()) return false;
    if (typeof w.isSynth === 'function' && w.isSynth()) return false;
    if (w.meta && w.meta.disabled) return false;
    return true;
  }

  function apply(r, mode, subtract) {
    var a = api(), ins = internals();
    if (!a || !ins || !(r.x1 > r.x0 || r.y1 > r.y0)) return;
    var cam = ins.camera;
    // the matrices are current between renders, but a drag that ends without
    // one leaves them stale, and stale here means selecting the wrong parts
    cam.updateMatrixWorld();
    cam.matrixWorldInverse.copy(cam.matrixWorld).invert();

    var found = [];
    a.widgets.each(function (w) {
      if (!selectable(w)) return;
      w.mesh.updateMatrixWorld();
      if (hits(w.mesh, cam, r, mode)) found.push(w);
    });

    var i;
    if (subtract) {
      for (i = 0; i < found.length; i++) {
        if (a.selection.contains(found[i])) a.platform.deselect(found[i]);
      }
    } else {
      a.platform.deselect();
      for (i = 0; i < found.length; i++) {
        // platform.select(w, true) TOGGLES, so a part already pulled in by a
        // group-mate's selection would be turned back off by selecting it
        if (!a.selection.contains(found[i])) a.platform.select(found[i], true);
      }
    }
    a.space.update();
  }

  // -------------------------------------------------- select all surfaces

  // The op panel is rebuilt every time the pointer enters the op, so the
  // button is re-added rather than added once.
  function anchorButton() {
    var btns = document.querySelectorAll('.ext-buttons button');
    for (var i = 0; i < btns.length; i++) {
      var t = (btns[i].innerText || '').trim().toLowerCase();
      if (t === ALL_ANCHOR && (btns[i].offsetWidth || btns[i].offsetHeight)) {
        return btns[i];
      }
    }
    return null;
  }

  function allButtonSync() {
    var anchor = anchorButton();
    var existing = document.getElementById(ALL_ID);
    if (!anchor) {
      // the panel went away, and took the anchor with it
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
      return;
    }
    // still in the right panel?
    if (existing && existing.parentNode && existing.nextElementSibling ===
        (anchor.closest('.ext-buttons') || anchor)) return;
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    var row = anchor.closest('.ext-buttons') || anchor.parentElement;
    if (!row || !row.parentNode) return;
    // a row of its own, above the one it sits over, so it reads as a separate
    // action rather than a second option on the same line
    var wrap = document.createElement('div');
    wrap.id = ALL_ID;
    wrap.className = row.className;
    var b = document.createElement('button');
    b.textContent = ALL_LABEL;
    b.onclick = function () { runSelectAll(b); };
    wrap.appendChild(b);
    row.parentNode.insertBefore(wrap, row);
  }

  // Arm the picker first if it is not already: pressing "select all" without
  // having pressed "select surfaces" should still do the obvious thing.
  async function armPicker() {
    if (pickerArmed()) return true;
    var anchor = anchorButton();
    if (!anchor) return false;
    anchor.click();
    for (var i = 0; i < 60 && !pickerArmed(); i++) {
      await new Promise(function (r) { setTimeout(r, 100); });
    }
    return pickerArmed();
  }

  function coveredFaces(w) {
    var g = groupsOf(w), out = {};
    for (var k in g) {
      var arr = g[k] || [];
      for (var i = 0; i < arr.length; i++) out[arr[i]] = 1;
    }
    return out;
  }

  // Walk the triangles, and for each one not already inside a picked group ask
  // the worker to grow the region it belongs to. Each call absorbs a whole
  // surface, so the number of round trips is the number of distinct surfaces,
  // not the number of triangles.
  async function selectAllSurfaces(ev) {
    var a = api();
    if (!pickerArmed()) return;
    var ws = a.widgets.all().filter(selectable);
    for (var i = 0; i < ws.length; i++) {
      var w = ws[i];
      var pos = w.mesh.geometry.attributes.position;
      if (!pos) continue;
      var faces = Math.floor(pos.count / 3);
      var covered = coveredFaces(w);
      for (var f = 0; f < faces; f++) {
        if (covered[f]) continue;
        if (groupCount(w) >= ALL_MAX) break;
        var before = groupCount(w);
        a.feature.on_mouse_up(
          { face: { a: f * 3, b: f * 3 + 1, c: f * 3 + 2 }, object: w.mesh }, ev);
        await settle(w, before);
        if (groupCount(w) === before) {
          // the worker found no region for this face; do not come back to it
          covered[f] = 1;
        } else {
          covered = coveredFaces(w);
        }
      }
    }
    a.space.update();
  }

  function runSelectAll(btn) {
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'selecting...';
    toastHide();
    serial(async function () {
      if (await armPicker()) await selectAllSurfaces(null);
    }).then(function () {
      btn.disabled = false;
      btn.textContent = label;
      showToast(null);
    }, function () {
      btn.disabled = false;
      btn.textContent = label;
    });
  }

  // -------------------------------------------------------------- install

  function install() {
    if (!internals()) return false;
    if (document.body.getAttribute('data-piccola-box-select')) return true;
    // capture on the document, so this runs before the orbit control's own
    // mousedown listener further down the tree
    document.addEventListener('mousedown', onDown, true);
    setInterval(function () {
      try { pillSync(); } catch (e) { }
      try { tipSync(); } catch (e) { }
      try { allButtonSync(); } catch (e) { }
    }, PILL_POLL_MS);
    document.body.setAttribute('data-piccola-box-select', '1');
    return true;
  }

  var tries = 400;
  (function wait() {
    try { if (install()) return; } catch (e) { /* not up yet */ }
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "min_drag": json.dumps(MIN_DRAG_PX),
        "overlay_id": json.dumps(OVERLAY_ID),
        "styles": json.dumps(STYLES),
        "subtract": json.dumps(SUBTRACT_MODIFIER),
        "sample_px": json.dumps(SURFACE_SAMPLE_PX),
        "max_samples": json.dumps(SURFACE_MAX_SAMPLES),
        "settle_ms": json.dumps(SURFACE_SETTLE_MS),
        "settle_step_ms": json.dumps(SURFACE_SETTLE_STEP_MS),
        "toast_id": json.dumps(SURFACE_TOAST_ID),
        "toast_ms": json.dumps(SURFACE_TOAST_MS),
        "toast_bottom": json.dumps(SURFACE_TOAST_BOTTOM_PX),
        "reselect_label": json.dumps(RESELECT_LABEL),
        "keep_label": json.dumps(KEEP_LABEL),
        "pill_id": json.dumps(SURFACE_PILL_ID),
        "cancel_label": json.dumps(CANCEL_LABEL),
        "pill_poll_ms": json.dumps(SURFACE_PILL_POLL_MS),
        "tip_id": json.dumps(SURFACE_TIP_ID),
        "tip_text": json.dumps(SURFACE_TIP_TEXT),
        "tip_gap": json.dumps(SURFACE_TIP_GAP_PX),
        "tip_match": json.dumps(SURFACE_TIP_MATCH),
        "all_id": json.dumps(SELECT_ALL_ID),
        "all_label": json.dumps(SELECT_ALL_LABEL),
        "all_anchor": json.dumps(SELECT_ALL_ANCHOR),
        "all_max": json.dumps(SELECT_ALL_MAX_GROUPS),
    }
    # A literal % anywhere in the JS above would be eaten by the format on the
    # line before this one. Nothing here needs one -- the hull walk uses an
    # explicit wrap rather than a modulo -- so assert that it stayed that way.
    assert "%" not in js, "unescaped % survived formatting in box_select_js()"
    return js
