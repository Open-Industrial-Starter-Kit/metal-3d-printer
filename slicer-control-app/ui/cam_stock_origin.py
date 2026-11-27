"""
ui/cam_stock_origin.py
Fusion-style origin picking on the stock box, for Kiri:Moto's CAM mode.

What upstream ships
-------------------
Kiri's whole CAM origin model is two checkboxes and three numbers
(``mode/cam/app/init-menu.js``): **origin top** (``camOriginTop``) puts it on
the stock's top face rather than its bottom, **origin center**
(``camOriginCenter``) puts it at the stock's centre rather than its front-left
corner, and ``camOriginOffX/Y/Z`` shift whatever that produced. Four anchors,
no choice of corner.

There is also a **select** button in that group (``cl-origin.js``), and it is
the right gesture — hover the part, click a point, origin moves there — but it
only offers points it can *find on the model*: the centres of circular traces
and the endpoints of open ones. On a plain rectangular block it offers nothing
at all, and it never offers a point on the stock.

What this installs instead
--------------------------
Both halves of what Fusion does, over Kiri's own fields — nothing under
``vendor/`` is touched:

* **A stock-point grid in the Origin group.** A 3x3 of the nine XY spots laid
  out the way they sit on the bed (back row at the top), plus top / middle /
  bottom for Z. Twenty-seven points: eight corners, twelve edge midpoints, six
  face centres and the body centre.

* **Picking on the stock in the 3D view.** *pick on stock* drops a handle on
  each of those twenty-seven points, on the stock box already drawn around the
  model. Hovering one lights it, clicking one sets the origin there. The
  handle where the origin already is stays lit, so the current origin is
  visible in the same gesture that changes it.

Neither needs new machinery. Kiri publishes ``mouse.hover`` and
``mouse.hover.up`` on the public api (``app/init/input.js``), and
``api.feature.hovers`` is the array the raycaster tests against
(``app/mouse.js``) — which is how ``face-tool.js`` does its own picking. So the
handles are ordinary meshes in ``api.const.SPACE.world`` and the interaction is
upstream's, borrowed rather than rebuilt. ``key.esc`` cancels, the same event
``face-tool.js`` binds.

The arithmetic
--------------
A picked point becomes offsets, because offsets are all Kiri's pipeline reads.
Three frames are involved and they do not agree, so, once and for all:

* ``WORLD.rotation.x = -PI/2`` (``moto/space.js``), so a world point
  ``(x, y, z)`` draws at scene ``(x, z, -y)``.
* The stock box is a unit cube scaled to ``(stock.x, stock.y, stock.z)`` at
  world ``stock.center`` (``cl-stock.js``), so the box spans ``center +/- half``
  on each world axis.
* The origin marker is placed by ``setOrigin(x, y, z)`` at scene ``(x, z, y)``
  (``moto/space.js``), so **setOrigin's y is world y negated**.

Writing a point as fractions ``(fx, fy, fz)`` of the box in *world* axes — 0 at
the minimum, 1 at the maximum — and reading ``update_origin()``
(``app/platform.js``) back through that mapping gives::

    camOriginOffX = camOriginCenter ? (fx - 0.5) * stock.x : fx * stock.x
    camOriginOffY = camOriginCenter ? (fy - 0.5) * stock.y : fy * stock.y
    camOriginOffZ = camOriginTop    ? (fz - 1.0) * stock.z : fz * stock.z

which checks out against the one case upstream documents: all offsets zero,
``camOriginCenter`` false, ``camOriginTop`` true is ``fx = fy = 0, fz = 1`` —
Kiri's default, the stock's front-left top corner. Note world ``fy = 0`` is the
*front* (scene +Z, toward the camera), which is why the grid's bottom row is
the front row.

Because it is expressed in fractions, a pick survives a stock resize: the
offsets are recomputed from the new dimensions and the origin stays on the
corner it was put on, rather than drifting into the middle of the block.

Round stock (``camStockRound``, patch 0006) keeps the same nine XY spots but
puts them on the circle: the centre stays on the axis and the other eight are
pushed out to the rim on their own bearing. Nothing is then left hanging in the
air off a corner that has no material, and "back left" still means back left.
Offsets are taken from the resulting world point rather than from the fractions
directly, which is the general form of the same arithmetic — on a box the two
agree exactly.

What rotary drops, and what it keeps
------------------------------------
In indexed mode the front/back column is withdrawn. Everything else stays: the
grid's middle row (left, centre, right), all three heights, and the picker.

The reason is upstream's and cannot be fixed from here. `platform.js` ends
``update_origin`` with ``if (isIndexed) origin.y = 0``, and `work/prepare.js`
pins the start point to that plane (``if (camStockIndexed) printPoint.y =
wmy``). So "front left", "left" and "back left" all name the same origin. Three
buttons that cannot differ are worse than one: they invite a choice and then
ignore it.

Hidden rather than greyed, because six dead cells around three live ones read as
a picker that is switched off — the first thing anyone does is click the corner
they use on flat stock and conclude the whole thing is broken.

Only the *offer* narrows. The arithmetic is the one flat stock uses, so a button
that survives means exactly what it means there, checked across the nine
surviving spot/height pairs. An earlier version of this module went much
further — three spots along the axis, no height row, Y and Z written as zero.
Each step was defensible on its own, and the result was a picker whose shape
depended on a checkbox elsewhere in the panel and a marker sitting somewhere the
flat marker never does.

Round stock (``camStockRound``, patch 0006) keeps the same nine XY spots but
puts them on the circle: the centre stays on the axis and the other eight are
pushed out to the rim on their own bearing. Nothing is then left hanging in the
air off a corner that has no material, and "back left" still means back left.
Offsets are taken from the resulting world point rather than from the fractions
directly, which is the general form of the same arithmetic — on a box the two
agree exactly.

Round stock (``camStockRound``, patch 0006) keeps the same nine XY spots but
puts them on the circle: the centre stays on the axis and the other eight are
pushed out to the rim on their own bearing. Nothing is then left hanging in the
air off a corner that has no material, and "back left" still means back left.
Offsets are taken from the resulting world point rather than from the fractions
directly, which is the general form of the same arithmetic — on a box the two
agree exactly.

Indexed (rotary) stock picks along the bar only. Turning the blank changes
what an origin can even mean: ``update_origin()`` forces ``origin.y = 0`` and
patch 0004 applies the same rule to the emitted start point, because the
machine parks Y at the rotary working position and locks it there. Z is the
axis for the same reason — the stock centre moves to z=0
(``platformUpdateStock``) and depth is a radius, not a height. What is left is
X: **where along the bar** the origin sits, which is exactly the thing worth
setting on a rotary job and which flows through to the G-code untouched
(``originx`` in ``work/prepare.js``, with no indexed override).

So in rotary the nine XY spots collapse to the three along the axis — one end,
the middle, the other end — and the Z row stands down. They are **hidden**
rather than greyed: six dead cells around three live ones read as a broken
picker, and the first thing anyone does is click the corner they use on flat
stock and conclude the whole thing is off. Shown as a single row of three, the
panel says what the mode actually offers. The offsets follow: X
as always, Y and Z written as zero rather than left to drift, since neither has
a meaning the machine would honour.

The three **handles** are drawn wherever ``update_origin()`` is going to put
the *marker* for that pick — not at a position chosen to look right. Two
attempts at choosing one both failed. On the rotary axis, which follows from
the offsets, the picker was unusable: the axis runs through the middle of the
material, so the centre handle was buried out of sight, the two ends sat dead
centre on the end caps, and none could be clicked, the ray reaching the stock
long before a point inside it. Moved to the top of the bar they were visible
and clickable and *disagreed with the marker*, so clicking a dot moved the
origin somewhere else — which is worse, because it looks like it worked.

There is no third guess to make: the marker's position is a function, so the
handle is that function. Copying it rather than re-deriving it surfaced two
things neither attempt had:

* the marker is pinned to world **y = 0** in indexed mode (``if (isIndexed)
  origin.y = 0``), *not* to the stock's Y centre — so a blank that is not
  centred on the bed put the dot a full ``center.y`` from the marker;
* its height is ``oz = origin.z / 2`` and so depends on ``camOriginTop``, which
  cannot be forced true to make the dot land nicely: ``work/export.js`` reads
  the same flag to set the exported Z zero (``z: camOriginTop ? origin.z - zmax
  : origin.z``), so flipping it would move a flat job's zero. (A rotary job's
  exported Z zero is the axis whatever the flag says, since Kiri:Moto patch
  0053; the marker still follows the flag, and this picker copies the marker.)

With ``camOriginTop`` on, as :data:`~ui.pages.cam._ORIGIN_DEFAULTS` pins it,
``oz`` is half the stock height — for a bar centred on ``z = 0``, the top of it.
Visible, clickable, and exactly under the marker. Checked across 108
combinations of stock centre, shape and anchor: worst disagreement 0 mm.

Upstream's two **anchor** checkboxes go
--------------------------------------
``camOriginTop`` and ``camOriginCenter`` choose which corner of the stock Kiri
measures the origin from — top face or bottom, centre or front-left. They are
*anchors*, not positions, and the offsets this module writes are computed
against whichever is set::

    camOriginOffX = camOriginCenter ? (fx - 0.5) * stock.x : fx * stock.x
    camOriginOffZ = camOriginTop    ? (fz - 1.0) * stock.z : fz * stock.z

so the picker reaches every spot from either anchor and the two checkboxes
change nothing an operator can observe. Both are pinned by ``_ORIGIN_DEFAULTS``
in ui/pages/cam.py besides, which makes them controls that cannot actually be
changed: tick one and the next load puts it back. Two reasons each to take the
rows out, and the panel then has one place where the origin is chosen instead
of three that disagree about what they mean.

The pins stay exactly as they were — this hides the controls, it does not alter
the values or the arithmetic that reads them, which still reads them live.

Upstream's **select** / **reset** row goes too
---------------------------------------------
Kiri's Origin group also carries a *select* button (``cl-origin.js``) which
scatters small red boxes on features it finds in the **part** — circle centres
and open-trace endpoints — and sets the origin to whichever is clicked, next to
a *reset* that zeroes the three offsets.

Two origin pickers side by side is already a lot to ask of a panel, and this
one is worse than redundant in indexed mode: it derives every point from the
widget's **unrotated** trace geometry::

    let { pos, tzoff } = widget.track;
    trace.fixClosed().clone().move(pos).move({ x:0, y:0, z: -tzoff })

with nothing in the file accounting for the rotation (``camStockIndexed``,
``indexRad`` and ``setAxisIndex`` appear zero times). On a turned bar the boxes
land where the part would be lying flat — floating below the stock — and
``pointHoverUp()`` computes the offsets from those same displaced points,
without zeroing Y as indexed work requires. Clicking one sets an origin wrong
in two axes, quietly.

Nothing is lost with *reset*: it writes ``camOriginOffX/Y/Z = 0``, and with the
pinned anchors that is the stock's front-left top corner — which is what
clicking **front left** and **top** in the grid does, asserted in the tests.

What does go is snapping to a *part* feature, a hole centre say, which this
module has no equivalent for. It is worth knowing that is the trade; the button
that offered it was also the one dropping red boxes under a rotary part.

Collapsing takes the picker with it
-----------------------------------
Kiri's group headers collapse their own rows through ``addModeControls``, which
marks each registered element with the class ``hide`` and is driven by the
inputs module's own ``refresh()`` — a path that never reaches a row injected
from outside. So the grid, the height row and *pick on stock* stayed on screen
with the Origin group shut.

They cannot be registered from here (the group's element list is module-private),
so the state is *read* instead: one of Kiri's own rows in the group — the X
offset, which this module never touches — is consulted for the ``hide`` class,
and the block is given or denied the same class to match. That works precisely
because the rows this module hides carry a class of its own, so ``hide`` still
means only what Kiri means by it. The header's click handler is wrapped to
re-sync after upstream's runs, and it is read at install for a group that starts
collapsed.

Making the origin visible
-------------------------
Kiri draws an origin marker, but ``controller.showOrigin`` defaults to false
(``conf/defaults.js``), so out of the box there is nothing in the viewport to
tell you where the origin went — which is half of why "top and center" felt
like the whole story. This turns it on, and because it is a display preference
so out of the box there is nothing in the viewport to tell you where the origin
went — which is half of why "top and center" felt like the whole story.

It is **pinned on**, and the Preferences checkbox that used to offer it is
hidden with the other controls this module supersedes.

That is the third answer to this question, and the first two were both wrong in
the same way. Treating it as a display preference belonging to the operator
meant seeding it once per profile — which requires winning a race against
``restoreSettings()`` putting the stored ``false`` back, and losing that race is
*silent*: the storage key is spent, the preference stays off, and there is no
marker in the viewport at all. Not a marker in the wrong place or the wrong
colour: nothing, which reads as a broken feature rather than an unset checkbox.
Moving the seed to ``init.two``/``init-done`` and only recording it once
confirmed was a better race, and still a race.

A picker whose result cannot be seen is not a picker, so the marker stops being
optional — but the pin does **not** live here. This module cannot install until
its own group exists (`api.ui.camOriginOffX` and a `.set-header` inside
``#cam-origin``), and hanging the marker's existence off that meant a picker
that had not finished installing left the viewport with no marker at all.
Measured in a booted app: the marker module reported installed while this one
had not. So it moved to ``ui/cam_origin_marker.py``, which owns the marker,
installs on `SPACE` and `THREE` alone, and is therefore already in place to
restyle the group its own pin causes to be built.

Why the offsets had to stop being pinned
----------------------------------------
``ui/pages/cam.py`` re-applied ``camOriginOffX/Y/Z = 0`` on every load and on
every ``device.selected`` (``applyPins``), which meant *any* origin — typed
into the offset fields, or picked with Kiri's own select button — was silently
thrown away at the next launch, and immediately if Setup > Machines was opened.
An origin picker whose result does not survive the app restarting is not a
picker, so the three offsets left the pinned set. ``camOriginTop`` and
``camOriginCenter`` stay pinned: they are the anchor this module measures
from, and the arithmetic above reads them live either way.
"""

from __future__ import annotations

import json

from ui.cam_arcs import HIDDEN_ROW_CLASS

#: Container built inside Kiri's own ``#cam-origin`` group, and the "already
#: installed" check so a re-run cannot stack two of them.
ROOT_ID = "piccola-stock-origin"

_GRID_ID = "piccola-so-grid"
_ZROW_ID = "piccola-so-z"
_PICK_ID = "piccola-so-pick"
_ACTIVE_CLASS = "piccola-so-on"

#: Row labels above the two choosers. Lower case to match Kiri's own panel
#: headings, which are rendered through `text-transform: capitalize`.
#:
#: "xy zero" and "z zero" rather than one "stock point" heading over both,
#: because that is the split every other CAM package makes and the words they
#: make it in. Vectric's job setup has an *XY Datum Position* (a 3x3 of corners
#: and centres, exactly this grid) and a separate *Z Zero Position*; Carbide
#: Create calls the pair the *zero location*. Fusion keeps them together under
#: *stock box point*, which is where "stock point" came from -- but Fusion can
#: afford one control because it draws the 27 points on a picture of the stock,
#: and this panel has to say in words what that picture shows.
#:
#: The Z row had no label at all, which is the part that actually confused: a
#: segmented *top | middle | bottom* under a grid of compass directions reads
#: as a fourth row of the same chooser rather than the other half of the
#: answer.
LABEL = "xy zero"
Z_LABEL = "z zero"

#: The 3x3 of XY spots, in DOM order (row-major, back row first). Each entry is
#: ``(fx, fy, label)`` as a fraction of the stock box on the *world* axes —
#: fy = 1 is the back of the bed, fy = 0 the front (see the module docstring).
XY_SPOTS = (
    (0.0, 1.0, "back left"),   (0.5, 1.0, "back"),   (1.0, 1.0, "back right"),
    (0.0, 0.5, "left"),        (0.5, 0.5, "center"), (1.0, 0.5, "right"),
    (0.0, 0.0, "front left"),  (0.5, 0.0, "front"),  (1.0, 0.0, "front right"),
)

#: Z levels, in DOM order — one choice of three, drawn as a segmented control.
#: ``(fraction, label, hover text)``.
#:
#: "middle" rather than "center" on purpose: the XY grid already has a *center*
#: spot, and one panel wanting both words to mean different axes is how an
#: operator ends up setting the wrong one.
#:
#: *stock top* and *stock bottom* rather than bare *top* and *bottom*: the
#: choice is the same one Vectric spells *material surface* vs *machine bed*,
#: and naming the thing the height is measured from is what makes it a choice
#: rather than a direction. The hover text gives the consequence, which is the
#: reason to pick one over the other -- zero at the top means depths are
#: measured into the material, zero at the bottom means they are measured from
#: the table, so a blank that is not quite the thickness you expected changes
#: what the program cuts in one case and not the other.
Z_SPOTS = (
    (1.0, "stock top", "zero at the top face — depths are cut into the material"),
    (0.5, "middle", "zero halfway up the stock"),
    (0.0, "stock bottom", "zero at the table — depths are measured up from it"),
)

#: Where the origin goes whenever the stock changes: the centre of the top
#: face, as ``[fx, fy, fz]``. Centre in XY lands on a cylinder's axis as readily
#: as the middle of a box — `pointFor` sends the centre spot to the centre
#: either way — so one answer covers every stock shape.
TOP_CENTRE_SPOT = (0.5, 0.5, 1.0)

#: How close an offset must be to a spot's own value for that spot to read as
#: active, in mm. Far below anything that matters when setting up a job, far
#: above the float noise of the arithmetic.
EPSILON_MM = 1e-4

#: Handle diameter as a fraction of the stock's smallest dimension, clamped.
#: Big enough to hit on a small block, small enough not to swamp a large one
#: or to hide the corner it is marking.
#:
#: Raised by half over the first sizing, which was tuned by eye against a large
#: blank and came out too small to get a pointer onto at ordinary zoom on a
#: small one. These are a click target before they are a decoration, so they
#: are sized for the pointer.
_HANDLE_FRACTION = 0.042
_HANDLE_MIN_MM = 0.75
_HANDLE_MAX_MM = 3.6

#: Handle colours: idle, hovered, and the spot the origin is already on.
#:
#: Idle is the app's NAVY (ui/theme.py) rather than its mid blue: these sit on
#: a pale grey stock box on a white bed, where a mid tone washes out and a dark
#: one reads at a glance from any angle. Hover and current keep the app's green
#: and amber, which stay legible against it.
_C_IDLE = 0x192540
_C_HOVER = 0x87BB44
_C_CURRENT = 0xD69A2D

#: Upstream rows in the Origin group that this picker supersedes, hidden by
#: `api.ui` key — see the module docstring. The two anchors are made invisible
#: by the picker and are pinned besides; `camOriginSelect` is the *select* /
#: *reset* pair, a second origin picker that is wrong on rotary stock.
SUPERSEDED_UI_KEYS = ("camOriginTop", "camOriginCenter", "camOriginSelect")

#: localStorage key + value marking the origin marker as already switched on.
#: Bump the value to switch it back on for profiles that have already been
#: through it — including any where the operator has since turned it off, so
#: bump it deliberately.
#: Shown while picking. Kiri's alerts take HTML.
PICK_HINT = "Click a point on the stock.<br>Press Esc to cancel."

#: Shown on the spots rotary work cannot use — see the module docstring.
#: Shown when there is no stock to pick on at all.
NO_STOCK_HINT = "Open a file first: there is no stock to pick on yet."


def css() -> str:
    """Styling for the stock-point grid."""
    return "\n".join([
        f"#{_GRID_ID} {{ display: grid; grid-template-columns: repeat(3, 1fr);"
        f" gap: 2px; padding: 2px 0; }}",
        # Kiri's panel buttons are sized for text; these are a spatial map, so
        # they are square-ish and carry their meaning in position, not label.
        f"#{_GRID_ID} > button {{ min-width: 0; padding: 3px 0; font-size: 10px;"
        f" line-height: 1; overflow: hidden; text-overflow: ellipsis; }}",
        # The Z row is one choice of three, so it is drawn as one control:
        # segments joined into a single pill, shared borders, only the ends
        # rounded. Three separate buttons read as three independent toggles,
        # which is what prompted this — the behaviour was always exclusive.
        f"#{_ZROW_ID} {{ display: flex; gap: 0; }}",
        f"#{_ZROW_ID} > button {{ flex: 1 1 0; min-width: 0; border-radius: 0;"
        f" margin-left: -1px; }}",
        f"#{_ZROW_ID} > button:first-child {{ margin-left: 0;"
        f" border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}",
        f"#{_ZROW_ID} > button:last-child {{"
        f" border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}",
        # the selected segment owns the shared border rather than being
        # overlapped by its neighbour's
        f"#{_ZROW_ID} > button.{_ACTIVE_CLASS} {{ position: relative;"
        f" z-index: 1; }}",
        f"#{ROOT_ID} .{_ACTIVE_CLASS} {{ background: #3A78B5 !important;"
        f" border-color: #3A78B5 !important; color: #FFFFFF !important; }}",
        f"#{ROOT_ID} > .piccola-so-label {{ opacity: 0.7; font-size: 11px;"
        f" padding: 4px 0 0 0; }}",
    ])


def stock_origin_js() -> str:
    """JS building the stock-point grid and the 3D handle picker."""
    return """
(function () {
  var ROOT = %(root)s, GRID = %(grid)s, ZROW = %(zrow)s, PICK = %(pick)s;
  var ON = %(active)s, LABEL = %(label)s, ZLABEL = %(zlabel)s;
  var XY = %(xy)s, ZS = %(zs)s;
  var EPS = %(eps)s, CENTRE = %(centre)s;
  var FRAC = %(frac)s, HMIN = %(hmin)s, HMAX = %(hmax)s;
  var C_IDLE = %(c_idle)s, C_HOVER = %(c_hover)s, C_CURRENT = %(c_current)s;
  var HINT = %(hint)s, NO_STOCK = %(no_stock)s;
  var HIDDEN_ROW = %(hidden_row)s, SUPERSEDED = %(superseded)s;

  var xyButtons = [], zButtons = [], pickBtn = null, rootEl = null;
  var gridEl = null, zrowEl = null, labelEl = null, zlabelEl = null;
  // Rotary Mode: the exported Z zero is the rotary axis or the top of the bar
  // (camRotaryZTop, Kiri:Moto patch 0053), whatever height the flat picker's
  // Z row names, so that row is swapped for these two.
  var RZ = [
    [false, 'rotary axis', 'Z 0 on the centre line: set Z0 with the tool on the tailstock centre point, before the bar goes in'],
    [true, 'stock top', 'Z 0 on top of the bar: set Z0 with the tool touching the top of the mounted bar']
  ];
  var rzButtons = [], rrowEl = null;
  var picking = false, handles = [], hovered = null, alertRec = null;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function conf() {
    var a = api();
    try { return a && a.conf.get(); } catch (e) { return null; }
  }

  // The stock, or null when there is nothing to pick on: no file open (Kiri
  // leaves the dimensions at 0), or indexed stock, whose origin does not
  // follow the mapping this module inverts.
  // Kiri's own class for a row hidden by its group being collapsed. Distinct
  // from the one this module hides rows with, which is what lets it be read
  // back as the group's state.
  var KIRI_HIDE = 'hide';

  // The group's header, once Kiri has built it. newGroup() appends a
  // `.set-header` to the container ahead of every row.
  function headerOf(host) {
    for (var i = 0; i < host.children.length; i++) {
      var ch = host.children[i];
      if (ch.classList && ch.classList.contains('set-header')) return ch;
    }
    return null;
  }

  function syncCollapse() {
    // the X offset row: Kiri's, in this group, and never touched here
    var probe = rowOf(uiOf('camOriginOffX'));
    if (!probe || !probe.classList || !rootEl || !rootEl.classList) return;
    rootEl.classList.toggle(KIRI_HIDE, probe.classList.contains(KIRI_HIDE));
  }

  function uiOf(key) {
    var a = api();
    try { return a.ui[key]; } catch (e) { return null; }
  }

  // The row a control sits in. newBoolean() wraps its input in a .var-row, so
  // that is a walk upward; newRow() registers the row itself, which carries
  // .ext-buttons — hence starting at the element and accepting either.
  function rowOf(el) {
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
      if (n.classList && (n.classList.contains('var-row') ||
                          n.classList.contains('ext-buttons'))) return n;
    }
    return null;
  }

  function stock() {
    var c = conf();
    if (!c || !c.stock || !c.process) return null;
    var s = c.stock;
    if (!(s.x > 0 && s.y > 0 && s.z > 0) || !s.center) return null;
    return s;
  }

  // Rotary: the blank turns about X, so Y is locked to the working position
  // and Z is the axis. Only the position along the bar is pickable.
  function indexed() {
    var c = conf();
    return !!(c && c.process && c.process.camStockIndexed);
  }

  // The canonical set of spots for the current stock: in rotary, the three
  // along the axis; otherwise all 27. Used where a spot has to be a *thing* —
  // a handle to draw, a candidate for "which one is lit".
  //
  // Not the same test as the panel's gating, which only asks whether a button
  // is offered (fy alone). An axis-row button passes whatever Z is currently
  // in force when it applies, and in rotary the offsets ignore Z entirely, so
  // that click is legitimate even though its fz is not 0.5.
  function usable(fx, fy, fz) {
    // Rotary drops the front/back column and keeps everything else. The blank
    // turns about X and the machine parks Y at the working position -- Kiri
    // ends update_origin() with `if (isIndexed) origin.y = 0` and prepare.js
    // pins the start point to that plane -- so "front left" and "back left"
    // name the same origin as "left". Three spots that cannot differ are worse
    // than one: they invite a choice and then ignore it.
    //
    // The Z row stays: height above the axis is a real choice, and X along the
    // bar is the whole point. What is left is exactly the grid's middle row and
    // the three heights.
    return indexed() ? fy === 0.5 : true;
  }

  // Whether the stock is a cylinder standing on the bed. The same rule the
  // slicer applies (patch 0006, work/stock-poly.js): the diameter is the
  // smaller of the X/Y fields, and never in indexed mode — which stock()
  // has already refused.
  function roundStock() {
    var c = conf();
    return !!(c && c.process && c.process.camStockRound);
  }

  // The world point a spot sits on. On a box that is the corner, edge midpoint
  // or face centre the fractions name. On a cylinder the nine XY spots go onto
  // the circle instead — the centre stays put and the other eight are pushed
  // out to the rim on their own bearing — so no handle is left hanging in the
  // air outside the material, and "back left" still means back left.
  function pointFor(fx, fy, fz) {
    var s = stock(), c = conf();
    if (!s || !c) return null;
    // Rotary: wherever platform.js is going to put the MARKER for this pick.
    //
    // Guessing at a nice-looking spot is what went wrong twice here. First the
    // handles went on the rotary axis, which reads well from the arithmetic --
    // that is where the tool calls zero -- and is unusable, because the axis
    // runs inside the material: the middle handle was buried out of sight, the
    // two ends sat dead centre on the end caps, and none could be clicked,
    // since the ray meets the stock long before a point inside it. Then they
    // went on top of the bar, which is visible and clickable and *disagrees
    // with the marker*, so clicking a dot moved the origin somewhere else.
    //
    // There is no third guess to make. `update_origin()` decides where the
    // marker goes, so that is the position, derived from the same three lines:
    //
    //     if (camOriginTop) origin.z = stock.z;
    //     origin.z += camOriginOffZ;
    //     if (isIndexed) origin.y = 0;
    //     lift = camOriginTop ? 0.01 : 0;      // drawn clear of the stock face
    //     oz = isIndexed ? (origin.z + lift) / 2 : origin.z + lift;
    //
    // with `setOrigin(x, y, oz)` placing the group at scene (x, oz, y) and
    // `WORLD.rotation.x = -PI/2` making that world (x, -y, oz). Two things
    // followed from copying it rather than inventing it, neither of which was
    // guessable: the marker is pinned to world **y = 0**, not to the stock's Y
    // centre, so a blank that is not centred on the bed put the dot a full
    // `center.y` away from it; and its height is driven by `camOriginTop`,
    // which cannot simply be forced true here because `export.js` reads it to
    // set the exported Z zero (`z: camOriginTop ? origin.z - zmax : origin.z`)
    // -- flipping it to make the dot land nicely would move the job's zero.
    //
    // With `camOriginTop` on, as _ORIGIN_DEFAULTS pins it, `oz` is half the
    // stock height, which for a bar centred on z=0 is the top of it: visible,
    // clickable, and exactly under the marker.
    var z = s.center.z + (fz - 0.5) * s.z;
    if (!roundStock()) {
      return { x: s.center.x + (fx - 0.5) * s.x,
               y: s.center.y + (fy - 0.5) * s.y,
               z: z };
    }
    // A rotary blank LIES ALONG X: the round pair is Y and Z, and X is the
    // length of the bar. Projecting X onto the circle — which is right for a
    // standing cylinder, where X and Y are the round pair — put *left* and
    // *right* one radius from the middle instead of at the ends: on a 100 mm
    // bar of 30 mm stock they landed 15 mm out rather than 50, so the handle
    // stopped well short of the limit and only typing the offset by hand
    // reached it.
    if (indexed()) {
      var rz = Math.min(s.y, s.z) / 2;
      var ry = fy - 0.5, rr = fz - 0.5;
      var rlen = Math.sqrt(ry * ry + rr * rr);
      var base = { x: s.center.x + (fx - 0.5) * s.x, y: s.center.y,
                   z: s.center.z };
      if (rlen === 0) return base;
      base.y = s.center.y + (ry / rlen) * rz;
      base.z = s.center.z + (rr / rlen) * rz;
      return base;
    }
    var dx = fx - 0.5, dy = fy - 0.5;
    var len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) return { x: s.center.x, y: s.center.y, z: z };
    var r = Math.min(s.x, s.y) / 2;
    return { x: s.center.x + (dx / len) * r,
             y: s.center.y + (dy / len) * r,
             z: z };
  }

  // A world point as mm offsets from Kiri's anchor — the inverse of
  // update_origin(), reading the two anchor flags live so a picked point stays
  // where it was put if either is ever toggled.
  function offsetsForPoint(p) {
    var c = conf(), s = stock();
    if (!s || !p) return null;
    var pr = c.process;
    var x = p.x - s.center.x + (pr.camOriginCenter ? 0 : s.x / 2);
    // Z is not a straight inverse in rotary. `update_origin()` HALVES it there
    //
    //     oz = isIndexed ? origin.z / 2 : origin.z;
    //
    // so the marker moves half a millimetre for every millimetre of offset,
    // and offsets computed as if it moved one-for-one leave the dot somewhere
    // other than the handle that was clicked. Solving `oz == p.z` for the
    // offset instead:
    //
    //     p.z = (origin.z + lift) / 2, origin.z = (camOriginTop ? stock.z : 0) + offZ
    //     anchor = camOriginTop ? stock.z + 0.01 : 0   ->  offZ = 2*p.z - anchor
    //
    // (patch 0033 moved the 0.01 from origin.z to the drawing, so the
    // exported Z zero is exact; the marker, and so this, did not move)
    //
    // The old arithmetic happened to be right at exactly one height — the top
    // of the bar — which is the spot the rotary picker is nearly always used
    // for, so the other two went unnoticed. An indexed bar is drawn centred on
    // the rotary axis at z = 0, and so is the model: `widget.center()` adds
    // half the height when `track.indexed` is set, which is what puts the part
    // on the axis rather than on the bed.
    if (indexed()) {
      var anchor = pr.camOriginTop ? (s.z + 0.01) : 0;
      return {
        x: x,
        y: p.y - s.center.y + (pr.camOriginCenter ? 0 : s.y / 2),
        z: 2 * p.z - anchor
      };
    }
    return {
      x: x,
      y: p.y - s.center.y + (pr.camOriginCenter ? 0 : s.y / 2),
      z: p.z - s.center.z - (pr.camOriginTop ? s.z / 2 : -s.z / 2)
    };
  }

  function offsetsFor(fx, fy, fz) {
    return offsetsForPoint(pointFor(fx, fy, fz));
  }

  // The spot last picked here, and the numbers written for it. Kept so the
  // origin can follow the stock when the stock changes underneath it -- see
  // followStock().
  function apply(fx, fy, fz) {
    var a = api(), c = conf(), off = offsetsFor(fx, fy, fz);
    if (!a || !c || !off) return false;
    var pr = c.process;
    pr.camOriginOffX = off.x;
    pr.camOriginOffY = off.y;
    pr.camOriginOffZ = off.z;
    // The sequence upstream's own origin buttons use (cl-origin.js): write the
    // fields back, redraw the marker, persist.
    a.settings.update_fields(pr);
    a.platform.update_origin();
    a.conf.save();
    refresh();
    return true;
  }

  // Rotary Z zero: the axis (false) or the top of the bar (true). Read by the
  // export (patch 0053), the header and Animate; the marker does not move.
  function applyRotaryZ(top) {
    var a = api(), c = conf();
    if (!a || !c || !c.process) return;
    c.process.camRotaryZTop = !!top;
    a.conf.save();
    refresh();
  }

  // When the STOCK changes, put the origin on the centre of its top face.
  //
  // The offsets are absolute millimetres from the anchor, so they follow
  // nothing: change a margin or switch to a cylinder and the numbers still
  // describe the old block, leaving the origin somewhere with no relation to
  // the stock now on screen.
  //
  // The first attempt tried to be clever -- remember which spot was picked and
  // re-derive that same spot for the new stock. It did nothing in the case that
  // actually matters, because there is no remembered spot until someone clicks
  // one: open a file, change the stock, and the origin was left exactly as
  // adrift as before. Top centre every time is worth more than a preserved
  // corner that only works on the second attempt -- it is somewhere definite,
  // it is on the stock whatever shape the stock is, and it is the same answer
  // every time, so there is nothing to reason about.
  //
  // A pick still stands until the stock moves again: this fires on *changes*,
  // not on every event, or it would overwrite the operator mid-job. The
  // signature is what draws that line -- the events below arrive constantly and
  // say nothing about whether anything actually moved.
  var lastStockSig = null;

  function stockSig() {
    var c = conf(), s = stock();
    if (!c || !s || !c.process) return null;
    var ctr = s.center || {};
    return [s.x, s.y, s.z, ctr.x, ctr.y, ctr.z,
            !!c.process.camStockRound, !!c.process.camStockIndexed].join(',');
  }

  function followStock() {
    var sig = stockSig();
    if (!sig) return;
    // First sighting is the baseline, not a change: an origin saved from the
    // last session is the operator's, and startup is not a stock change.
    if (lastStockSig === null) {
      lastStockSig = sig;
      return;
    }
    if (sig === lastStockSig) return;
    lastStockSig = sig;
    apply(CENTRE[0], CENTRE[1], CENTRE[2]);
  }

  // Which spot the origin is on right now, as [fx, fy, fz], or null. Derived
  // from the live offsets every time, never remembered: type into the offset
  // fields and the highlight clears itself, because the origin genuinely is
  // not on a spot any more.
  function activeSpot() {
    var c = conf(), s = stock();
    if (!c || !s) return null;
    var pr = c.process;
    for (var i = 0; i < XY.length; i++) {
      for (var j = 0; j < ZS.length; j++) {
        if (!usable(XY[i][0], XY[i][1], ZS[j][0])) continue;
        var off = offsetsFor(XY[i][0], XY[i][1], ZS[j][0]);
        if (Math.abs(off.x - (pr.camOriginOffX || 0)) >= EPS) continue;
        if (Math.abs(off.y - (pr.camOriginOffY || 0)) >= EPS ||
            Math.abs(off.z - (pr.camOriginOffZ || 0)) >= EPS) continue;
        return [XY[i][0], XY[i][1], ZS[j][0]];
      }
    }
    return null;
  }

  function refresh() {
    var spot = activeSpot(), live = !!stock(), rot = indexed();
    for (var i = 0; i < xyButtons.length; i++) {
      var b = xyButtons[i];
      // Hidden rather than greyed: six dead cells around three live ones read
      // as a picker that is switched off, and the first thing anyone does is
      // click the corner they use on flat stock and conclude the whole thing
      // is broken. Shown as one row of three, the panel says what the mode
      // offers.
      var offAxis = rot && XY[i][1] !== 0.5;
      b.style.display = offAxis ? 'none' : '';
      // disabled as well as hidden: a control that cannot be seen should not
      // be able to act either, whatever reaches it
      b.disabled = !live || offAxis;
      b.title = live ? '' : NO_STOCK;
      var on = !!spot && spot[0] === XY[i][0] && spot[1] === XY[i][1];
      b.classList.toggle(ON, on);
    }
    if (zrowEl) zrowEl.style.display = rot ? 'none' : '';
    for (var j = 0; j < zButtons.length; j++) {
      var z = zButtons[j];
      z.disabled = !live || rot;
      z.title = live ? (ZS[j][2] || '') : NO_STOCK;
      z.classList.toggle(ON, !!spot && spot[2] === ZS[j][0]);
    }
    if (labelEl) labelEl.textContent = LABEL;
    if (zlabelEl) zlabelEl.textContent = ZLABEL;
    if (rrowEl) rrowEl.style.display = rot ? '' : 'none';
    var c = conf(), topZ = !!(c && c.process && c.process.camRotaryZTop);
    for (var k = 0; k < rzButtons.length; k++) {
      rzButtons[k].disabled = !live;
      rzButtons[k].classList.toggle(ON, rot && topZ === RZ[k][0]);
    }
    if (pickBtn) {
      pickBtn.disabled = !live;
      pickBtn.title = live ? '' : NO_STOCK;
      pickBtn.classList.toggle(ON, picking);
    }
    if (picking) colorHandles();
    syncCollapse();
  }

  // Clicking an XY spot keeps the Z level already in force, and vice versa, so
  // the two rows read as one 3D pick rather than two unrelated settings. With
  // the origin off-spot entirely, Kiri's own default (top) is the fallback.
  function currentOr(index, fallback) {
    var spot = activeSpot();
    return spot ? spot[index] : fallback;
  }

  /* ---- 3D handles ------------------------------------------------------ */

  function world() {
    var a = api();
    try { return a.const.SPACE.world; } catch (e) { return null; }
  }
  function redraw() {
    var a = api();
    try { if (a.const.SPACE.update) a.const.SPACE.update(); } catch (e) { }
  }

  function buildHandles() {
    var s = stock(), w = world();
    if (!s || !w || !window.THREE) return false;
    var size = Math.max(HMIN, Math.min(HMAX,
                        Math.min(s.x, s.y, s.z) * FRAC));   // diameter
    for (var i = 0; i < XY.length; i++) {
      for (var j = 0; j < ZS.length; j++) {
        var fx = XY[i][0], fy = XY[i][1], fz = ZS[j][0];
        if (!usable(fx, fy, fz)) continue;
        // A sphere, so a handle reads as the same round dot whatever angle
        // the view is orbited to — a cube turns into a differently-sized
        // silhouette as it rotates, which made the corners hard to compare.
        var geo = new THREE.SphereGeometry(size / 2, 16, 12);
        var mat = new THREE.MeshBasicMaterial({
          color: C_IDLE, opacity: 0.92, transparent: true,
          // On the rotary axis a handle is inside the blank by definition, and
          // depth testing would hide it outright. Clicking is unaffected
          // either way: `api.feature.hovers` scopes the raycast to this array,
          // so the stock is never a candidate to be hit first.
          depthTest: false
        });
        var mesh = new THREE.Mesh(geo, mat);
        // Same points the panel writes offsets for, so a handle always sits
        // exactly where clicking it puts the origin — on the box's corners, or
        // on the cylinder's rim when the stock is round.
        var at = pointFor(fx, fy, fz);
        mesh.position.set(at.x, at.y, at.z);
        mesh.renderOrder = 11;
        mesh._piccolaSpot = [fx, fy, fz];
        handles.push(mesh);
        w.add(mesh);
      }
    }
    colorHandles();
    redraw();
    return true;
  }

  function colorHandles() {
    var spot = activeSpot();
    for (var i = 0; i < handles.length; i++) {
      var h = handles[i], s = h._piccolaSpot;
      var on = !!spot && spot[0] === s[0] && spot[1] === s[1] && spot[2] === s[2];
      h.material.color.setHex(h === hovered ? C_HOVER
                              : on ? C_CURRENT : C_IDLE);
    }
    redraw();
  }

  function clearHandles() {
    var w = world();
    for (var i = 0; i < handles.length; i++) {
      if (w) w.remove(handles[i]);
    }
    handles = [];
    hovered = null;
    redraw();
  }

  function startPick() {
    var a = api();
    if (!a || picking) return;
    // Another hover tool owns the mouse (face select, Kiri's own point
    // select). Pre-empting it would leave that one believing it is still on.
    if (a.feature.hover) return;
    if (!buildHandles()) return;
    picking = true;
    a.feature.hover = true;
    a.feature.hovers = handles;
    try { alertRec = a.show.alert(HINT, 60000); } catch (e) { }
    refresh();
  }

  function stopPick() {
    var a = api();
    if (!picking) return;
    picking = false;
    if (a) {
      a.feature.hover = false;
      a.feature.hovers = undefined;
      try { a.hide.alert(alertRec); } catch (e) { }
    }
    alertRec = null;
    clearHandles();
    refresh();
  }

  function togglePick() { picking ? stopPick() : startPick(); }

  /* ---- the origin marker ----------------------------------------------- */

  /* ---- DOM ------------------------------------------------------------- */

  function button(text, title) {
    var b = document.createElement('button');
    b.appendChild(document.createTextNode(text));
    if (title) b.title = title;
    return b;
  }

  function install() {
    var a = api();
    if (!a || !a.conf || !a.platform || !a.event) return false;
    var host = document.getElementById('cam-origin');
    if (!host) return false;
    if (document.getElementById(ROOT)) return true;
    // `#cam-origin` is a static div in index.html, so it is there from the
    // first millisecond -- long before Kiri builds the group inside it. Waiting
    // only on the host meant this could install into an empty box: the picker
    // was appended above the header instead of below the rows, there was no
    // `.set-header` yet to hang the collapse handler on, and the group state
    // could never be read back. Wait for the group's own furniture instead.
    if (!uiOf('camOriginOffX') || !headerOf(host)) return false;

    var root = rootEl = document.createElement('div');
    root.id = ROOT;
    root.className = 'f-col';

    var label = document.createElement('div');
    label.className = 'piccola-so-label';
    label.appendChild(document.createTextNode(LABEL));
    root.appendChild(label);
    labelEl = label;

    var grid = document.createElement('div');
    grid.id = GRID;
    XY.forEach(function (spot, i) {
      var b = button(spot[2], spot[2]);
      b.onclick = function () { apply(spot[0], spot[1], currentOr(2, 1.0)); };
      grid.appendChild(b);
      xyButtons[i] = b;
    });
    root.appendChild(grid);
    gridEl = grid;

    var zlabel = document.createElement('div');
    zlabel.className = 'piccola-so-label';
    zlabel.appendChild(document.createTextNode(ZLABEL));
    root.appendChild(zlabel);
    zlabelEl = zlabel;

    var zrow = document.createElement('div');
    zrow.id = ZROW;
    zrow.className = 'ext-buttons f-row';
    ZS.forEach(function (spot, j) {
      var b = button(spot[1], spot[2]);
      b.onclick = function () {
        apply(currentOr(0, 0.0), currentOr(1, 0.0), spot[0]);
      };
      zrow.appendChild(b);
      zButtons[j] = b;
    });
    root.appendChild(zrow);
    zrowEl = zrow;

    var rrow = document.createElement('div');
    rrow.id = ZROW + '-rotary';
    rrow.className = 'ext-buttons f-row';
    rrow.style.display = 'none';
    RZ.forEach(function (opt, k) {
      var b = button(opt[1], opt[2]);
      b.onclick = function () { applyRotaryZ(opt[0]); };
      rrow.appendChild(b);
      rzButtons[k] = b;
    });
    root.appendChild(rrow);
    rrowEl = rrow;

    var prow = document.createElement('div');
    prow.className = 'ext-buttons f-row';
    pickBtn = button('pick on stock', 'click a point on the stock box in the 3D view');
    pickBtn.id = PICK;
    pickBtn.onclick = togglePick;
    prow.appendChild(pickBtn);
    root.appendChild(prow);

    host.appendChild(root);

    // Collapse with the group. Upstream's header handler runs first and sets
    // the state; ours reads it back off one of the group's own rows.
    var head = headerOf(host);
    if (head) {
      var upstreamClick = head.onclick;
      head.onclick = function () {
        if (upstreamClick) upstreamClick.apply(this, arguments);
        syncCollapse();
      };
    }

    // Rows this picker replaces. Marked with the shared class rather than an
    // inline style so Kiri's own panel refreshes cannot undo it, and done once
    // at install: unlike the rotary withdrawal these do not come back.
    SUPERSEDED.forEach(function (key) {
      var row = rowOf(uiOf(key));
      if (row && row.classList) row.classList.add(HIDDEN_ROW);
    });



    a.event.on('mouse.hover', function (ev) {
      if (!picking) return;
      var mesh = ev && ev.int && ev.int.object;
      var next = (mesh && mesh._piccolaSpot) ? mesh : null;
      if (next === hovered) return;
      hovered = next;
      colorHandles();
    });

    a.event.on('mouse.hover.up', function (ev) {
      if (!picking) return;
      // upSelect hands on the intersection record, not the mesh.
      var hit = ev && ev.object;
      var mesh = (hit && hit.object) || hit;
      if (!mesh || !mesh._piccolaSpot) return;
      var s = mesh._piccolaSpot;
      apply(s[0], s[1], s[2]);
      stopPick();
    });

    a.event.on('key.esc', stopPick);
    // The stock can change under a pick — a new file, a stock field edited —
    // which moves every handle. Settings are saved on all of those paths.
    a.event.on('settings.saved', function () {
      if (picking) { clearHandles(); buildHandles(); }
      refresh();
    });
    // The header click is not the only way this can fall out of step, and a
    // handler on one element is a single point of failure -- if it is ever
    // missed, the picker is stuck in whatever state it was last left in, with
    // nothing to correct it. Re-reading the group on the events that redraw the
    // panel makes it self-correcting instead: whatever happened, the next one
    // puts it back in step.
    ['settings.saved', 'boolean.update', 'widget.add', 'platform.layout',
     'init-done'].forEach(function (ev) {
      try { a.event.on(ev, syncCollapse); } catch (e) { }
    });
    // The stock changes on all of these: the margin fields write through
    // `settings.saved`, the shape selector and rotary switch through
    // `boolean.update`, and a re-layout or a new part through the other two.
    ['settings.saved', 'boolean.update', 'platform.layout',
     'widget.add', 'widget.delete'].forEach(function (ev) {
      try { a.event.on(ev, followStock); } catch (e) { }
    });
    // Take the baseline HERE, not on the first event. Reading it from the
    // first event meant that event was consumed as the baseline, so the first
    // real stock change after startup was the one change that did nothing --
    // which is precisely the case that gets noticed.
    lastStockSig = stockSig();
    a.event.on('widget.add', refresh);
    a.event.on('widget.delete', function () { stopPick(); refresh(); });

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
        "grid": json.dumps(_GRID_ID),
        "zrow": json.dumps(_ZROW_ID),
        "pick": json.dumps(_PICK_ID),
        "active": json.dumps(_ACTIVE_CLASS),
        "label": json.dumps(LABEL),
        "zlabel": json.dumps(Z_LABEL),
        "xy": json.dumps([[fx, fy, text] for fx, fy, text in XY_SPOTS]),
        "zs": json.dumps([[fz, text, tip] for fz, text, tip in Z_SPOTS]),
        "eps": repr(EPSILON_MM),
        "centre": json.dumps(list(TOP_CENTRE_SPOT)),
        "frac": repr(_HANDLE_FRACTION),
        "hmin": repr(_HANDLE_MIN_MM),
        "hmax": repr(_HANDLE_MAX_MM),
        "c_idle": hex(_C_IDLE),
        "c_hover": hex(_C_HOVER),
        "c_current": hex(_C_CURRENT),
        "hint": json.dumps(PICK_HINT),
        "no_stock": json.dumps(NO_STOCK_HINT),
        "hidden_row": json.dumps(HIDDEN_ROW_CLASS),
        "superseded": json.dumps(list(SUPERSEDED_UI_KEYS)),
    }
