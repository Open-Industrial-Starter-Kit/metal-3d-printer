"""
ui/cam_stock.py
Reworks Kiri:Moto's Stock group: the **offset** switch goes and is pinned on,
and round stock gets an axis choice with fields that describe a cylinder.

What the switch did
-------------------
`camStockOffset` does not enable or disable an offset — it changes what the
three *width* / *depth* / *height* fields **mean**
(`platformUpdateStock`, `app/platform.js`)::

    let stock = { x: camStockX, y: camStockY, z: camStockZ };
    if (camStockOffset || (stock.x * stock.y * stock.z === 0)) {
        stock.x += bounds.max.x - bounds.min.x;
        stock.y += bounds.max.y - bounds.min.y;
        stock.z += bounds.max.z - bounds.min.z;
    }

On, they are margins *added around* the part. Off, they are the stock's
absolute dimensions. Kiri ships the switch on with all three fields at 5, so
turning it off leaves a **5 x 5 x 5 mm** stock: for any real part that is a
cube buried inside the model, drawn but invisible, which reads as the stock box
having disappeared. That is how it was found.

Why it goes rather than being explained
---------------------------------------
Nothing is actually lost. The stock is centred on the part's bounds either way
(`stock.center` is the bounds centre regardless of the switch), so absolute and
margin mode differ only in which number you type: a 100 mm blank around a 60 mm
part is `100` in absolute mode or `40` in margin mode. One mode is enough, and
margins are the one that cannot silently produce a stock smaller than the part.

The trade, stated plainly: describing a **fixed blank** now means subtracting
the part size first. That is arithmetic the operator does once, against a
switch that otherwise costs a confusing failure every time it is touched.

`camStockOffset: True` therefore joins the pinned process fields in
`ui/pages/cam.py`, and the row goes with it — a control whose value is pinned
is a control that lies about being adjustable, the same reasoning as
ui/cam_toolchange.py.

A note on the field labels
--------------------------
They still read *width* / *depth* / *height*, which now means "beyond the
part". Kiri's own tooltip already says so ("0 defaults to part size"), and
renaming them is a separate decision from removing the switch, so they are left
alone here.

Shape is chosen; orientation is not
-----------------------------------
The Stock group leads with one question — **rectangular** or **cylindrical** —
as a segmented pair rather than a checkbox, because that is a choice between
two things, not a modifier on one.

Which way a cylinder lies is *not* a second question. Turning the blank about X
is what a bar on its side means, so:

    rotary off  ->  the cylinder stands on the bed, round face perpendicular
                    to Z, cleared as a circular footprint (patch 0006)
    rotary on   ->  the cylinder lies along X, round face perpendicular to the
                    bed, turned about its axis

The orientation therefore follows `camStockIndexed` and has no control of its
own. An axis selector beside the rotary switch would be a second way to say the
same thing, and two controls that must agree are two controls that can disagree.

That also settles a question Kiri's own model cannot answer. Its stock is a
prism — one 2D footprint extruded to a single `stock.z`, no per-XY height
anywhere in the slicer — so a cylinder lying on the bed has no 3-axis
representation better than its bounding box. It does not need one: lying down
*is* the rotary case, where `prepare.js` already clears the swept envelope with
`hypot(stock.y, stock.z) / 2`, and `stockIsRound()` correctly stays false there
because the footprint really is a rectangle.

Where the selector sits, and why it matters
-------------------------------------------
It goes in as the first **row** of the Stock group — which is not the same as
its first child. ``newGroup()`` appends a ``.set-header`` to the group div
before any row, so inserting at ``firstChild`` puts the selector *above* the
header: outside the collapsible section, with the header pushed down below it.
Inserted after the header it is inside the group and fixed in place.

Appending instead is no good either: Kiri's three box rows are shown and hidden
with the shape, so a selector below them moved up and down by three rows every
time it was used, walking out from under the pointer mid-click. First row, and
only what is under it changes.

Collapsing takes the selector with it
-------------------------------------
Kiri's group header collapses its own rows through ``addModeControls``, which
marks each registered element with the class ``hide`` and is driven by the
inputs module's own ``refresh()`` — a path that never reaches a row injected
from outside. So the selector sat there on its own with the group shut.

It is not registerable from here (the group's element list is module-private),
so instead the state is *read* rather than tracked: one of Kiri's own rows is
consulted for the ``hide`` class, and the selector is given or denied the same
class to match. That works precisely because this module hides rows with a
class of its own — the two never collide, so ``hide`` still means exactly what
Kiri means by it. The header's click handler is wrapped to re-sync after
upstream's runs, and the state is read once at install for a group that starts
collapsed.

Deriving is not optional at load
--------------------------------
The margins that make a cylinder are *derived* from the part, so they go stale
the moment the part does — including on the very first load, where nothing had
derived them at all. Left alone, ``min(stock.x, stock.y)`` is then the part's
**shorter side**, so the stock comes out smaller than the model it is meant to
contain, and only fixes itself when a shape switch happens to force a
re-derive. So the derivation runs whenever the part's size changes, install
included, and not only when a control is touched.

Fields that describe a cylinder
-------------------------------
A cylinder is a diameter and a length, so with **cylindrical** chosen the three
box fields are replaced by two, named for how the stock is lying:

============  ===========================  ============================
rotary        side                         the other field
============  ===========================  ============================
off           diameter, beyond the part    **height** (Z)
on            diameter, beyond the part    **length** (X, along the bar)
============  ===========================  ============================

Both are margins in exactly the sense the box fields are — material beyond the
part — so **the numbers carry across a shape change**. A box with 5 mm all
round and 7 mm of height becomes a cylinder with 5 mm all round and 7 mm of
height, and switching back gives the box its 5/5/7 again. Leaving the cylinder
writes the plain margins rather than the derived per-axis ones, which encode a
diameter and would be nonsense as box dimensions; that round trip is asserted.

The same applies when the rotary switch moves under a cylinder: the margins
mean the same thing but the axes have swapped roles, so the stock is
re-derived rather than left describing the previous orientation.

Making the model fit
--------------------
The diameter is not a free number: the part has to be inside it. Patch 0006
takes the diameter as `min(stock.x, stock.y)`, and stock dimensions are the
part's bounds plus a margin per axis — so a margin typed into *width* and
*depth* separately gives a circle of `min(bw, bd) + margin`, and any part that
is not square pokes out of it. The enclosing circle of a rectangular footprint
has the diameter of its **diagonal**, not its shorter side.

So *side* is not written straight through. It is the material wanted beyond the
part, and the per-axis margins are derived so the circle circumscribes it::

    vertical    D = swept(x, y) + side   ->  camStockX = D - bw
                                             camStockY = D - bd
                                             camStockZ = height

    horizontal  D = swept(y, z) + side   ->  camStockY = D - bd
                                             camStockZ = D - bh
                                             camStockX = length

which makes `stock.x == stock.y == D` (or `y == z == D` lying down), so the
`min()` the worker takes is the diameter meant, and the part is inside it by
construction — with `side = 0` the cylinder is the smallest one that contains
the part.

`swept` is measured from the GEOMETRY: twice the greatest distance of any vertex
from the axis, taken about the part's own centre in the two radial axes. It used
to be `hypot` of the bounding box — its DIAGONAL — which is the smallest circle
around a *rectangle*, and is right only for a part that fills the corners of its
box. A lathe part does not. A disc of diameter d has a d x d box whose diagonal
is 1.414 d, so with *side* at zero the stock came out 41%% wider than the part it
was meant to hug, which is what a rotary job is nearly always cutting. On a
square bar the two agree exactly, which is why the diagonal looked right until
something round went through it.

Each derived margin is still non-negative for free: the swept radius is at least
half of each radial extent, so `D >= bd` and `D >= bh`. When the vertices cannot
be read — no widget yet, or an API that moved — it falls back to the diagonal,
which over-sizes rather than under-sizes.

Values are pushed through `api.conf.update_fields()` rather than assigned to
the process alone: `updateSettingsFromFields()` reads `api.ui[key]` back over
the settings on the next panel interaction, so a field left disagreeing with
the record is a value that quietly reverts.

How the row is hidden
---------------------
By marking it, not by styling it directly: Kiri writes inline
``row.style.display`` when it refreshes group visibility, so the class (which
carries `!important`) is what survives. The class and its rule belong to
ui/cam_arcs.py — shared, so one stylesheet rule covers every row this app
removes.
"""

from __future__ import annotations

import json

from ui.cam_arcs import HIDDEN_ROW_CLASS

#: Class marking the chosen axis segment. Namespaced like the rest.
_ON_CLASS = "piccola-stock-on"

#: The `api.ui` key whose row is removed.
KIRI_UI_KEY = "camStockOffset"

#: Kiri stock rows replaced by the two cylinder fields while round is chosen.
BOX_FIELD_KEYS = ("camStockX", "camStockY", "camStockZ")

#: Kiri's own round checkbox, superseded by the shape selector.
ROUND_UI_KEY = "camStockRound"

#: Kiri ships 5 mm of margin on every axis, so a part arrives wrapped in stock
#: nobody asked for. Zero is the honest default — the stock is the part until
#: someone says otherwise — and it is *seeded*, not pinned: a margin is a fact
#: about the job, so once the operator sets one it is theirs. Recorded under
#: this key so the seeding happens once per browser profile.
#:
#: **When** it runs is the whole of whether it works. Written at install it is
#: a race with Kiri's own settings load, which restores the stored process over
#: the top — and because the marker was written at the same moment, the one
#: shot was spent losing that race and the old margins came back for good. It
#: is therefore attempted on the init events *and* on settings changes, all
#: guarded by the marker, so it lands after the profile is in place rather than
#: before.
ZERO_MARGIN_KEYS = ("camStockX", "camStockY", "camStockZ")
ZERO_SEED_KEY = "piccola-kiri-stock-zero"
#: Bumped to "2": version 1 could be recorded by an attempt that a later
#: settings load overwrote, leaving the marker set and the old margins in
#: place. Those profiles get one more, correctly-timed, attempt.
ZERO_SEED_VERSION = "2"

#: Ids for the controls this module builds.
ROOT_ID = "piccola-stock-round"
SHAPE_ID = "piccola-stock-shape"
SIDE_ID = "piccola-stock-side"
LONG_ID = "piccola-stock-long"
#: The readout under the two fields: the cylinder those margins make, as a
#: diameter and a length. The fields are *margins beyond the part*, which is
#: the right thing to type but not what the operator has to go and buy.
SIZE_ID = "piccola-stock-size"
#: The same readout for the block: under Kiri's three offset rows, the box
#: those offsets make, X by Y by Z. Each offset is a total across its axis,
#: so the block is simply the part plus the offset on each.
BOX_SIZE_ID = "piccola-stock-box-size"

#: The shape choice, and the `camStockRound` state each means.
SHAPE_CHOICES = (("rectangular", False), ("cylindrical", True))

#: The second field's label, by how the cylinder is lying — which follows the
#: rotary switch, not a control of its own. A standing cylinder has a height, a
#: bar lying along X has a length; calling both "height" would be the sort of
#: label that means nothing on half the jobs it appears in.
LONG_LABEL = {"vertical": "height", "horizontal": "length"}

#: The radial field. "side" is the operator's word for it — material beyond the
#: part, all the way round.
SIDE_LABEL = "side"

#: The group heading. Kiri calls it "stock"; every field under it is a margin
#: added beyond the part, so it says so.
HEADING = "stock margin"

#: What Kiri's three stock fields are renamed to, and what their hover text
#: becomes: `api.ui` key -> (label, tooltip lines).
#:
#: In this app these fields are *always* offsets. ``camStockOffset`` is pinned
#: true (``_KIRI_PROCESS_DEFAULTS``) and its switch is removed by
#: ``hide_stock_offset_js`` below, so the absolute reading is unreachable.
#: Labelled "width" the row says the stock is 1 mm wide; what a 1 actually does
#: is add 1 mm of material across that axis. Named by axis rather than by
#: dimension because that is the frame everything else in this panel uses — the
#: origin offsets, the Z limits, the jog readout — and because "depth" and
#: "height" have to be re-learned per app, where Y and Z do not.
#:
#: The tooltips are replaced along with the labels rather than left behind.
#: Upstream's — "width (x) in workspace units" / "0 defaults to part size" —
#: both describe the mode that is not in use here.
#:
#: The split is the part worth stating, because it is the one that surprises:
#: the number is the total across the axis, half of it per side. Measured — an
#: 80 x 50 part with all three set to 5 gives 85 x 55 of stock, with the part
#: sitting 2.5 in from each face.
#:
#: Renamed here rather than through ``ui/cam_labels.py``'s language table:
#: ``newInput`` is handed the already-resolved string, so these labels carry no
#: ``lk`` attribute for that mechanism to find. Reaching them through the
#: ``api.ui`` registry is exact — no matching on the text that is being changed.
OFFSET_FIELDS = {
    "camStockX": ("offset X", (
        "stock beyond the part along X",
        "split evenly: 5 adds 2.5 per side",
        "0 for stock the size of the part",
    )),
    "camStockY": ("offset Y", (
        "stock beyond the part along Y",
        "split evenly: 5 adds 2.5 per side",
        "0 for stock the size of the part",
    )),
    "camStockZ": ("offset Z", (
        "stock beyond the part along Z",
        "split evenly: 5 adds 2.5 per side",
        "0 for stock the size of the part",
    )),
}

#: Marks a row already renamed.
OFFSET_DONE_ATTR = "data-piccola-stock-offset"


def css() -> str:
    """Styling for the round-stock controls."""
    return "\n".join([
        f"#{ROOT_ID} {{ padding: 2px 0; }}",
        # the axis pair is one choice, so it is drawn as one control — the same
        # segmented treatment the origin height row gets
        f"#{SHAPE_ID} {{ display: flex; gap: 0; padding: 2px 0; }}",
        f"#{SHAPE_ID} > button {{ flex: 1 1 0; min-width: 0; border-radius: 0;"
        f" margin-left: -1px; font-size: 11px; }}",
        f"#{SHAPE_ID} > button:first-child {{ margin-left: 0;"
        f" border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}",
        f"#{SHAPE_ID} > button:last-child {{"
        f" border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}",
        f"#{SHAPE_ID} > button.{_ON_CLASS} {{ position: relative; z-index: 1;"
        f" background: #3A78B5 !important; border-color: #3A78B5 !important;"
        f" color: #FFFFFF !important; }}",
        f"#{ROOT_ID} .piccola-stock-row {{ display: flex; align-items: center;"
        f" justify-content: space-between; gap: 6px; padding: 1px 0; }}",
        f"#{ROOT_ID} .piccola-stock-row > label {{ opacity: 0.8;"
        f" font-size: 11px; }}",
        f"#{ROOT_ID} .piccola-stock-row > input {{ width: 60px; }}",
        # text-transform: the panel capitalises every word ("Mm Long")
        f"#{SIZE_ID}, #{BOX_SIZE_ID} {{ font-size: 11px; opacity: 0.8;"
        f" padding: 2px 0 1px; text-align: right; text-transform: none; }}",
    ])


def round_stock_js() -> str:
    """JS building the axis choice and the two cylinder fields."""
    return """
(function () {
  var ROOT = %(root)s, SHAPE = %(shape)s, SIDE = %(side)s, LONG = %(long)s;
  var SIZE = %(size)s, BOX_SIZE = %(box_size)s;
  var ON = %(on)s, HIDDEN = %(hidden)s;
  var CHOICES = %(choices)s, LONG_LABEL = %(long_label)s, SIDE_LABEL = %(side_label)s;
  var HEADING = %(heading)s;
  var BOX_KEYS = %(box_keys)s, ROUND_KEY = %(round_key)s;
  var ZERO_KEYS = %(zero_keys)s, SEED_KEY = %(seed_key)s, SEED_VERSION = %(seed_ver)s;

  var shapeBtns = {}, sideIn = null, longIn = null, longLab = null, sizeEl = null;
  var boxSizeEl = null;
  var root = null, fieldsEl = null;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function conf() { var a = api(); try { return a.conf.get(); } catch (e) { return null; } }
  function uiOf(k) { var a = api(); try { return a.ui[k]; } catch (e) { return null; } }
  function rowOf(el) {
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) return n;
    }
    return null;
  }

  // Removing our class is not enough to bring a row back: Kiri writes inline
  // `row.style.display` of its own when it refreshes group visibility, and an
  // inline none outlives the class going away. So showing clears both.
  // Kiri's own class for a row hidden by its group being collapsed. Distinct
  // from ours, which is why it can be read as the group's state.
  var KIRI_HIDE = 'hide';

  // Any row Kiri registered with the group; its `hide` class is the collapse
  // state, whatever else we may have done to the row ourselves.
  function groupProbe() { return rowOf(uiOf('camStockX')); }

  function syncCollapse() {
    var probe = groupProbe();
    if (!probe || !probe.classList || !root || !root.classList) return;
    var folded = probe.classList.contains(KIRI_HIDE);
    root.classList.toggle(KIRI_HIDE, folded);
    // the box readout is outside `root`, after Kiri's own rows, so it folds
    // with the group the same way
    if (boxSizeEl) boxSizeEl.classList.toggle(KIRI_HIDE, folded);
  }

  function showRow(row, hidden) {
    if (!row || !row.classList) return;
    row.classList.toggle(HIDDEN, hidden);
    if (!hidden && row.style && row.style.display === 'none') {
      row.style.display = '';
    }
  }
  function isRound() {
    var c = conf();
    return !!(c && c.process && c.process.camStockRound);
  }
  function isIndexed() {
    var c = conf();
    return !!(c && c.process && c.process.camStockIndexed);
  }
  function axisNow() { return isIndexed() ? 'horizontal' : 'vertical'; }

  // The part, as Kiri measures it. No bounds yet means no file open.
  function partSize() {
    var c = conf();
    var b = c && c.bounds;
    if (!b || !b.min || !b.max) return null;
    var w = b.max.x - b.min.x, d = b.max.y - b.min.y, h = b.max.z - b.min.z;
    if (!(w > 0 && d > 0 && h > 0)) return null;
    return { w: w, d: d, h: h };
  }

  function num(el, dflt) {
    var v = parseFloat(el && el.value);
    return isFinite(v) && v >= 0 ? v : dflt;
  }

  function setNum(el, v) {
    if (el) el.value = String(Math.round(v * 1000) / 1000);
  }

  // A margin means the same thing in both shapes: material beyond the part.
  // So the numbers carry across rather than resetting — a box with 5 mm all
  // round becomes a cylinder with 5 mm all round, and back again.
  //
  // Which of Kiri's three fields holds which depends on how the stock lies:
  // standing, the radial pair is X/Y and the length is Z; lying, the radial
  // pair is Y/Z and the length is X.
  function readMargins() {
    var c = conf();
    if (!c) return { side: 0, long: 0 };
    var pr = c.process, h = isIndexed();
    return h
      ? { side: Math.min(pr.camStockY || 0, pr.camStockZ || 0), long: pr.camStockX || 0 }
      : { side: Math.min(pr.camStockX || 0, pr.camStockY || 0), long: pr.camStockZ || 0 };
  }

  // Leaving the cylinder, the derived per-axis margins (which encode a
  // diameter) would be nonsense as box dimensions, so they are written back as
  // the plain margins they came from.
  //
  // `commit` is separate because *when* this runs matters. The box fields are
  // revealed by the shape flip, and they still hold the derived values at that
  // moment — so doing this afterwards showed 12.1 / 32.1 for a frame before
  // they were replaced by the real margins. Writing the values first and
  // committing after leaves nothing to see: the fields are already right when
  // they appear, and the stock is recomputed once.
  function writeBoxMargins(commit) {
    var a = api(), c = conf();
    if (!a || !c) return;
    var side = num(sideIn, 0), long = num(longIn, 0), pr = c.process;
    if (isIndexed()) {
      pr.camStockY = side; pr.camStockZ = side; pr.camStockX = long;
    } else {
      pr.camStockX = side; pr.camStockY = side; pr.camStockZ = long;
    }
    try { a.conf.update_fields(pr); } catch (e) { }
    if (!commit) return;
    try { a.platform.update_bounds(); } catch (e) { }
    try { a.conf.save(); } catch (e) { }
  }

  // Once per browser profile: drop Kiri's shipped 5 mm to nothing, so a part
  // arrives as its own stock. Seeded rather than pinned — see ZERO_SEED_KEY.
  // Applied once this page load, but not yet confirmed to have survived.
  var zeroApplied = false;

  function seedZeroMargins() {
    var seeded = true;
    try { seeded = localStorage.getItem(SEED_KEY) === SEED_VERSION; } catch (e) { }
    if (seeded) return;
    var a = api(), c = conf();
    if (!a || !c) return;
    // A cylinder's margins are not the operator's and not a default: they are
    // derived from the part, so zeroing them would shrink the stock inside the
    // model until something forced a re-derive. Only a box has margins this is
    // about, and the box margins a cylinder was seeded from are carried in the
    // side/height fields regardless.
    if (isRound()) return;
    var pr = c.process;

    // Two visits, not one. Whichever event gets here first may still be ahead
    // of the stored profile landing, and the marker is what makes this
    // one-shot: spend it on a write that a later load overwrites and the old
    // margins are back for good, with nothing left to correct them. So the
    // marker is only written on a *later* visit that finds the zeros still in
    // place; anything that put them back gets zeroed again instead.
    if (zeroApplied && ZERO_KEYS.every(function (k) { return pr[k] === 0; })) {
      try { localStorage.setItem(SEED_KEY, SEED_VERSION); } catch (e) { }
      return;
    }

    var changed = false;
    ZERO_KEYS.forEach(function (k) {
      if (pr[k] !== 0) { pr[k] = 0; changed = true; }
    });
    zeroApplied = true;
    if (!changed) return;
    try { a.conf.update_fields(pr); } catch (e) { }
    try { a.platform.update_bounds(); } catch (e) { }
    try { a.conf.save(); } catch (e) { }
  }

  function seedFields() {
    var m = readMargins();
    setNum(sideIn, m.side);
    setNum(longIn, m.long);
  }

  // The smallest cylinder the part fits inside, measured from the GEOMETRY.
  //
  // The radius is the greatest distance of any vertex from the axis, taken
  // about the part's own centre in the two radial axes — which is where Kiri
  // puts the axis (`widget.center()` centres the part on it, adding half the
  // height when `track.indexed`). Measuring the offset from that same centre
  // makes the answer independent of where the part sits on the bed.
  //
  // This replaces the bounding box's DIAGONAL, which is the right answer only
  // for a part that fills the corners of its box. A lathe part does not: a
  // disc of diameter d has a d x d box whose diagonal is d * 1.414, so with
  // *side* at zero the stock came out 41%% wider than the part it was supposed
  // to hug. On a square-section bar the two agree exactly, which is why the
  // old rule looked right until someone turned something round.
  //
  // Returns null when the vertices cannot be read, and the caller falls back
  // to the diagonal — an over-estimate, but never an under-estimate.
  function sweptDiameter(horizontal) {
    var a = api();
    if (!a || !a.widgets || typeof a.widgets.all !== 'function') return null;
    var widgets;
    try { widgets = a.widgets.all(); } catch (e) { return null; }
    if (!widgets || !widgets.length) return null;
    var lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
    var arrays = [];
    // radial axes: Y,Z for a bar lying along X; X,Y for a standing cylinder
    var i0 = horizontal ? 1 : 0, i1 = horizontal ? 2 : 1;
    for (var w = 0; w < widgets.length; w++) {
      var pos;
      try {
        pos = widgets[w].getGeoVertices({ unroll: false, translate: false });
      } catch (e) { return null; }
      if (!pos || !pos.length) return null;
      arrays.push(pos);
      for (var i = 0; i + 2 < pos.length; i += 3) {
        var u = pos[i + i0], v = pos[i + i1];
        if (u < lo[0]) lo[0] = u;
        if (u > hi[0]) hi[0] = u;
        if (v < lo[1]) lo[1] = v;
        if (v > hi[1]) hi[1] = v;
      }
    }
    if (!isFinite(lo[0]) || !isFinite(lo[1])) return null;
    var cu = (lo[0] + hi[0]) / 2, cv = (lo[1] + hi[1]) / 2;
    var r2 = 0;
    for (var k = 0; k < arrays.length; k++) {
      var arr = arrays[k];
      for (var j = 0; j + 2 < arr.length; j += 3) {
        var du = arr[j + i0] - cu, dv = arr[j + i1] - cv;
        var d2 = du * du + dv * dv;
        if (d2 > r2) r2 = d2;
      }
    }
    return r2 > 0 ? 2 * Math.sqrt(r2) : null;
  }

  // side + length -> the three per-axis margins, chosen so the cylinder
  // circumscribes the part. See the module docstring.
  function marginsFor(side, long, horizontal, p) {
    var swept = sweptDiameter(horizontal);
    if (horizontal) {
      var Dh = (swept !== null ? swept : Math.hypot(p.d, p.h)) + side;
      return { x: long, y: Dh - p.d, z: Dh - p.h };
    }
    var Dv = (swept !== null ? swept : Math.hypot(p.w, p.d)) + side;
    return { x: Dv - p.w, y: Dv - p.d, z: long };
  }

  // The cylinder the fields describe, in the units the operator buys stock
  // in: diameter = smallest cylinder round the part + side; length = the
  // part along the axis + the long field. Read from the same numbers apply()
  // writes, so it says what the stock will be, not what the record last was.
  function showSize() {
    var p = partSize(), round = isRound();
    if (sizeEl) {
      if (!p || !round) {
        sizeEl.textContent = '';
      } else {
        var h = isIndexed(), swept = sweptDiameter(h);
        var D = (swept !== null ? swept : (h ? Math.hypot(p.d, p.h) : Math.hypot(p.w, p.d)))
              + num(sideIn, 0);
        var L = (h ? p.w : p.h) + num(longIn, 0);
        sizeEl.textContent = 'Stock \u00d8 ' + D.toFixed(2) + ' \u00d7 ' + L.toFixed(2)
          + ' mm ' + (h ? 'long' : 'tall');
      }
    }
    // The block: part plus each offset, read off the fields so it follows
    // the typing the same way the cylinder line does. An offset is the total
    // across its axis (see OFFSET_FIELDS), not a per-side amount.
    if (boxSizeEl) {
      showRow(boxSizeEl, !p || round);
      if (!p || round) {
        boxSizeEl.textContent = '';
      } else {
        var W = p.w + num(uiOf('camStockX'), 0);
        var Dp = p.d + num(uiOf('camStockY'), 0);
        var H = p.h + num(uiOf('camStockZ'), 0);
        boxSizeEl.textContent = 'Stock ' + W.toFixed(2) + ' \u00d7 ' + Dp.toFixed(2)
          + ' \u00d7 ' + H.toFixed(2) + ' mm';
      }
    }
  }

  function apply() {
    var a = api(), c = conf(), p = partSize();
    if (!a || !c || !p || !isRound() || busy) return;
    busy = true;
    try { applyNow(a, c, p); } finally { busy = false; }
  }

  function applyNow(a, c, p) {
    var m = marginsFor(num(sideIn, 0), num(longIn, 0), isIndexed(), p);
    var pr = c.process;
    pr.camStockX = m.x;
    pr.camStockY = m.y;
    pr.camStockZ = m.z;
    // the fields as well as the record: updateSettingsFromFields() reads
    // api.ui[key] back over the settings on the next panel interaction
    try { a.conf.update_fields(pr); } catch (e) { }
    try { a.platform.update_bounds(); } catch (e) { }
    try { a.conf.save(); } catch (e) { }
    showSize();
  }

  // Throw Kiri's own checkbox rather than writing the field: it is what
  // updateSettingsFromFields() reads back, and clicking it runs the handlers
  // upstream hangs off it.
  function setShape(name) {
    var want = false;
    CHOICES.forEach(function (c) { if (c[0] === name) want = c[1]; });
    if (isRound() === want) { refresh(); return; }
    // Carry the operator's margins across the change rather than resetting --
    // and do it BEFORE the flip, so the fields about to be revealed already
    // hold the right numbers instead of flashing the derived ones.
    if (want) seedFields(); else writeBoxMargins(false);
    var box = uiOf(ROUND_KEY);
    if (box && box.click) box.click();
    setTimeout(function () {
      if (isRound()) apply(); else writeBoxMargins(true);
      refresh();
    }, 0);
  }

  var lastAxis = null, lastPart = null, busy = false, pending = false;

  function refresh() {
    var round = isRound(), p = partSize();
    // The margins that make a cylinder are DERIVED from the part, so they go
    // stale the moment the part does — including on the very first load, where
    // nothing had derived them at all. Left alone, `min(stock.x, stock.y)` is
    // then the part's shorter side and the stock comes out smaller than the
    // model, which only fixed itself when a shape switch forced a re-derive.
    var partKey = p ? [p.w, p.d, p.h].join() : null;
    var partChanged = partKey !== lastPart;
    lastPart = partKey;
    // The rotary switch changes which axes are radial, so a cylinder has to be
    // re-derived when it moves — the margins mean the same, the axes do not.
    //
    // Deferred, and once: enabling rotary already runs updateAxisMode(), which
    // re-wraps the stock and every widget, and this fires from inside that
    // work. Re-deriving there put an update_bounds() and a save() in the middle
    // of it, which is what made the switch take so long. Out of the way, and
    // collapsed if several events land together.
    var axis = axisNow();
    var axisChanged = lastAxis !== null && lastAxis !== axis;
    lastAxis = axis;
    if (round && p && (axisChanged || partChanged) && !pending) {
      pending = true;
      setTimeout(function () { pending = false; apply(); }, 0);
    }
    // the shape selector is always offered; only the cylinder fields come and go
    if (fieldsEl) fieldsEl.classList.toggle(HIDDEN, !round);
    // Kiri's three box fields say nothing useful about a cylinder
    BOX_KEYS.forEach(function (k) { showRow(rowOf(uiOf(k)), round); });
    // upstream's own round checkbox is what this selector replaces
    var rk = rowOf(uiOf(ROUND_KEY));
    if (rk && rk.classList) rk.classList.add(HIDDEN);
    Object.keys(shapeBtns).forEach(function (name) {
      var want = false;
      CHOICES.forEach(function (c) { if (c[0] === name) want = c[1]; });
      shapeBtns[name].classList.toggle(ON, want === round);
    });
    // which way it lies is the rotary switch's business, not a control here
    if (longLab) longLab.textContent = LONG_LABEL[axis];
    if (sideIn) sideIn.disabled = !p;
    if (longIn) longIn.disabled = !p;
    showSize();
    syncCollapse();
  }

  function install() {
    var a = api();
    if (!a || !a.conf || !a.ui) return false;
    var host = document.getElementById('cam-stock');
    if (!host || !uiOf(ROUND_KEY) || !uiOf('camStockX')) return false;
    if (document.getElementById(ROOT)) return true;

    root = document.createElement('div');
    root.id = ROOT;
    root.className = 'f-col';

    var shapeRow = document.createElement('div');
    shapeRow.id = SHAPE;
    CHOICES.forEach(function (c) {
      var b = document.createElement('button');
      b.appendChild(document.createTextNode(c[0]));
      b.title = c[1]
        ? 'a cylinder — standing on the bed, or lying along X when rotary is on'
        : 'a rectangular block';
      b.onclick = function () { setShape(c[0]); };
      shapeRow.appendChild(b);
      shapeBtns[c[0]] = b;
    });
    root.appendChild(shapeRow);

    // only these come and go with the shape; the selector above always shows
    fieldsEl = document.createElement('div');
    fieldsEl.className = 'f-col';
    root.appendChild(fieldsEl);

    function field(id, label, tip) {
      var row = document.createElement('div');
      row.className = 'piccola-stock-row';
      row.title = tip;
      var lab = document.createElement('label');
      lab.appendChild(document.createTextNode(label));
      var inp = document.createElement('input');
      inp.id = id;
      inp.type = 'text';
      inp.value = '0';
      inp.onchange = apply;
      inp.oninput = showSize;     // the readout follows the typing
      row.appendChild(lab);
      row.appendChild(inp);
      fieldsEl.appendChild(row);
      return { input: inp, label: lab };
    }
    // the split is the part that surprises: the number is a total, half of it
    // per side, the same as the box offsets' tooltips say
    sideIn = field(SIDE, SIDE_LABEL,
      'added to the part\\'s diameter\\nsplit evenly: 4 adds 2 per side\\n' +
      '0 for stock the size of the part').input;
    var lf = field(LONG, LONG_LABEL.vertical,
      'added to the part\\'s length (height when standing)\\n' +
      'split evenly: 4 adds 2 at each end\\n' +
      '0 for stock the size of the part');
    longIn = lf.input;
    longLab = lf.label;
    sizeEl = document.createElement('div');
    sizeEl.id = SIZE;
    sizeEl.title = 'The bar these margins make: diameter and length.';
    fieldsEl.appendChild(sizeEl);

    // The block readout goes under Kiri's own three rows, which sit after
    // `root` in the group; the last of them is where "under" is.
    var zRow = rowOf(uiOf('camStockZ'));
    if (zRow && zRow.parentNode) {
      boxSizeEl = document.createElement('div');
      boxSizeEl.id = BOX_SIZE;
      boxSizeEl.title = 'The block these offsets make: X by Y by Z.';
      zRow.parentNode.insertBefore(boxSizeEl, zRow.nextSibling);
      BOX_KEYS.forEach(function (k) {
        var el = uiOf(k);
        if (el && el.addEventListener) el.addEventListener('input', showSize);
      });
    }

    // First *row* in the group, which is not the same as first child:
    // newGroup() appends a `.set-header` to the group div before any row, so
    // inserting at firstChild puts the selector above the header — outside the
    // collapsible section, with the header pushed down below it. After the
    // header it is inside the group and fixed in place, and only the rows
    // under it come and go with the shape. (Appending instead would put it
    // below the three box rows, which show and hide, so it walked up and down
    // by three rows every time it was used.)
    var header = null;
    for (var i = 0; i < host.children.length; i++) {
      var ch = host.children[i];
      if (ch.classList && ch.classList.contains('set-header')) { header = ch; break; }
    }
    host.insertBefore(root, header ? header.nextSibling : host.firstChild);

    // Every number in this group is material added beyond the part (the
    // offset switch is pinned on), so the heading says "stock margin" rather
    // than reading as the stock's own size. Set on the header's own text
    // node: newGroup() bakes LANG.cs_menu into it when the menu is built,
    // before a language-table rewrite (ui/cam_labels.py) can land.
    var headLink = header && header.querySelector('a');
    if (headLink) headLink.textContent = HEADING;

    // Not seeded here: install happens before Kiri's settings load, which
    // would put the stored margins straight back. `init.two` is emitted from
    // init_sync(), commented upstream as "SECOND STAGE INIT AFTER UI RESTORED"
    // and reached after `restoreSettings()`; `init-done` closes the same
    // function. Two moments, both once per load and both before the operator
    // can touch anything, which is what the confirm step in seedZeroMargins()
    // wants. See ZERO_SEED_KEY.
    ['init.two', 'init-done'].forEach(function (ev) {
      try { a.event.on(ev, seedZeroMargins); } catch (e) { }
    });

    // Collapse the selector with the group. Upstream's handler runs first and
    // sets the state; ours reads it back off one of its own rows.
    if (header) {
      var upstreamClick = header.onclick;
      header.onclick = function () {
        if (upstreamClick) upstreamClick.apply(this, arguments);
        syncCollapse();
      };
    }

    seedFields();

    ['settings.saved', 'widget.add', 'widget.delete', 'platform.layout',
     'boolean.update'].forEach(function (ev) {
      try { a.event.on(ev, refresh); } catch (e) { }
    });
    // The header click is not the only way the collapse state can move, and a
    // handler on one element is a single point of failure -- miss it once and
    // the selector is stuck in whatever state it was left in, with nothing to
    // correct it. Re-reading the group on the events that redraw the panel
    // makes it self-correcting: whatever happened, the next one puts it back.
    ['settings.saved', 'boolean.update', 'widget.add', 'platform.layout',
     'init-done'].forEach(function (ev) {
      try { a.event.on(ev, syncCollapse); } catch (e) { }
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
        "root": json.dumps(ROOT_ID),
        "shape": json.dumps(SHAPE_ID),
        "side": json.dumps(SIDE_ID),
        "long": json.dumps(LONG_ID),
        "size": json.dumps(SIZE_ID),
        "box_size": json.dumps(BOX_SIZE_ID),
        "on": json.dumps(_ON_CLASS),
        "hidden": json.dumps(HIDDEN_ROW_CLASS),
        "choices": json.dumps([[n, r] for n, r in SHAPE_CHOICES]),
        "long_label": json.dumps(LONG_LABEL),
        "side_label": json.dumps(SIDE_LABEL),
        "heading": json.dumps(HEADING),
        "box_keys": json.dumps(list(BOX_FIELD_KEYS)),
        "round_key": json.dumps(ROUND_UI_KEY),
        "zero_keys": json.dumps(list(ZERO_MARGIN_KEYS)),
        "seed_key": json.dumps(ZERO_SEED_KEY),
        "seed_ver": json.dumps(ZERO_SEED_VERSION),
    }


def hide_stock_offset_js() -> str:
    """JS marking Kiri's stock *offset* row as hidden."""
    return """
(function () {
  var KEY = %(key)s, CLASS = %(cls)s;

  function mark() {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return false; }
    // built with the rest of the CAM menu, well after the kiri namespace
    // appears, so absence here means "not yet", not "gone"
    if (!ui || !ui[KEY]) return false;

    // walk out to the .var-row wrapper newBoolean() put the control in
    for (var n = ui[KEY], hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) {
        n.classList.add(CLASS);
        return true;
      }
    }
    return false;
  }

  var tries = 400;
  (function wait() {
    if (mark()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "key": json.dumps(KIRI_UI_KEY),
        "cls": json.dumps(HIDDEN_ROW_CLASS),
    }


def offset_labels_js() -> str:
    """JS renaming Kiri's stock size fields to say they are offsets."""
    js = """
(function () {
  var FIELDS = %(fields)s, DONE = %(done)s;

  function rowOf(el) {
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && n.classList.contains('var-row')) return n;
    }
    return null;
  }

  function rename() {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return false; }
    if (!ui) return false;
    var left = 0;
    for (var key in FIELDS) {
      var el = ui[key];
      // built with the rest of the CAM menu, so absence means "not yet"
      if (!el) { left++; continue; }
      var row = rowOf(el);
      if (!row) { left++; continue; }
      if (row.getAttribute(DONE)) continue;
      var lab = row.querySelector('label');
      if (!lab) { left++; continue; }
      // replace the text only, so anything else appended to this label --
      // the range hint from ui/cam_ranges.py -- survives the rename
      var done = false;
      for (var i = 0; i < lab.childNodes.length; i++) {
        var n = lab.childNodes[i];
        if (n.nodeType === 3) { n.nodeValue = FIELDS[key][0]; done = true; break; }
      }
      if (!done) lab.insertBefore(document.createTextNode(FIELDS[key][0]), lab.firstChild);
      row.setAttribute('title', FIELDS[key][1].join('\\n'));
      row.setAttribute(DONE, '1');
    }
    return left === 0;
  }

  var tries = 400;
  (function wait() {
    var ok = false;
    try { ok = rename(); } catch (e) { }
    if (ok) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "fields": json.dumps({k: [lab, list(tip)] for k, (lab, tip) in OFFSET_FIELDS.items()}),
        "done": json.dumps(OFFSET_DONE_ATTR),
    }
    assert "%" not in js, "unescaped % survived formatting in offset_labels_js()"
    return js
