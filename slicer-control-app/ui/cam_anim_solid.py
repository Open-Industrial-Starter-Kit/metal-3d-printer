"""
ui/cam_anim_solid.py
Draws the simulated stock solid instead of see-through.

What it looked like
-------------------
In *Animate* the blank is drawn at ``opacity: 0.9`` with ``transparent`` on, so
the toolpath, the model and the grid all show through the material that is
supposed to be hiding them. Mid-cut that reads as a sheet of tinted glass
rather than a block of aluminium, and the one question the animation exists to
answer -- how much material is left, and where -- is asked of a surface you can
see straight through.

Transparency had a job upstream: with the *show model* switch off and no
timeline, seeing the part emerge through the stock was the only way to tell
whether the cut was going where it should. This app has the model switch, a
seekable timeline (patches 0002, 0008, 0009, 0012) and per-operation markers
(0014), so the stock no longer has to be seen past.

Why a stored value and not a material edit
------------------------------------------
``anim-2d.js`` already reads the choice at setup::

    toggleTrans(0, api.local.getBoolean('cam.anim.trans', true));

so the honest place to make the change is that stored value, not the material
it produces. Touching ``material.transparent`` directly would be a second
writer of the same state, racing the animator's own call on every entry to
*Animate* -- and losing it, because upstream's runs after the ``animate`` event
this module listens to.

``conf/defaults.js`` ships the key ``true`` (:data:`KIRI_DEFAULT`), and those
defaults are merged over the stored settings on load, so it is **pinned**
rather than seeded: written at install, on the init events, and again on each
``animate``, which is the last moment before the animator reads it. A seed
would be overwritten by the next defaults merge and the stock would come back
transparent on some later launch, which is a worse failure than never having
changed it -- intermittent rather than wrong.

The flat animator only
----------------------
``anim-3d.js`` already calls ``toggleTrans(undefined, false)`` at its own setup,
so a rotary animation is opaque upstream and this module changes nothing there.
Writing the same value it is about to write itself is harmless either way.

A note on ``getBoolean``
------------------------
Upstream's helper is::

    function localGetBoolean(key, def = true) {
        let val = localGet(key);
        return val === true || val === 'true' || val === def;
    }

The ``val === def`` arm means the *default* argument is really "a second value
that counts as true", so ``getBoolean(key, false)`` returns **true** for a
stored ``false``. Only one caller reads this key and it passes ``true``
(``anim-2d.js:109``), where a stored ``false`` correctly yields false. Worth
knowing before adding a second reader: pass ``true`` or do not use the helper.

The floor
---------
Opaque was not enough, and the reason is geometry rather than material. The
simulated stock is a height field -- one vertex per grid cell -- so what it has
is a *top*. ``createGrid`` (``work/anim-2d.js``) does drop the outermost ring of
points to z 0::

    pos[ai++] = stock && (x * y === 0 || x === ex || y === ey) ? 0 : size.z;

which gives the block its side walls, one cell wide. Nothing closes the
underside. Drawn ``FrontSide`` (patch 0017) that open bottom is invisible from
below and see-through from any angle low enough to look under the edge -- which
is exactly what it looks like: a lid with walls, hanging over the toolpath.

So a floor is added: one plane at the mesh's own minimum z, spanning its
bounding box, wound to face **down**, in the stock's colour and material. It is
parented to the stock mesh, so it inherits every transform and disappears with
it when the animation is torn down.

Done here rather than in ``createGrid`` deliberately. Adding vertices there
means growing the ``SharedArrayBuffer`` the worker and the main thread both
index by cell, for a surface that never changes while the other 800,000 do --
all of the risk of a patch and none of its necessity. ``window.THREE`` is
already on the page, the mesh is findable by its material, and a static child
mesh costs one draw call.

What the floor is not
---------------------
It is flat and it stays. A cut that goes *through* the stock leaves a hole in
the height field, and from underneath the floor now covers that hole, so a
cut-through reads as closed from below. Fusion, simulating a real solid, shows
the hole. Matching that needs the real thing -- a solid model rather than a
height field -- which is a different and much larger piece of work.

What is not changed
-------------------
The transparency button stays withdrawn -- see ui/cam_anim_buttons.py for why,
and for the note that predicted exactly this confusion.
"""

from __future__ import annotations

import json

#: The stored choice the flat animator reads at setup. ``true`` means
#: see-through.
TRANS_KEY = "cam.anim.trans"

#: What ``conf/defaults.js`` ships it as, and therefore what this module is
#: holding the line against on every settings merge.
KIRI_DEFAULT = True

#: The two colours ``animate_setup`` gives the stock, light theme and dark
#: (``anim-2d.js``: ``color = settings.controller.dark ? 0x48607B : 0x607FA4``).
#: The mesh is found by them because it carries no name, no flag and no handle
#: of its own -- and matching the colour its own code assigns is exact, where
#: matching "the biggest mesh in the scene" would be a guess that a large model
#: could win.
STOCK_COLORS = ("607fa4", "48607b")

#: Marks a stock mesh that already has its floor, so the tick below is a
#: property read in the common case.
FLOOR_FLAG = "piccolaFloor"

#: How often a new stock mesh is looked for. The mesh is rebuilt on every entry
#: to *Animate* and on every replay, and none of those emit an event carrying
#: it, so it is polled. The work is one cached-property check unless the mesh
#: has actually been replaced.
INTERVAL_MS = 600


def anim_solid_js() -> str:
    """JS pinning the simulated stock opaque and giving it a floor."""
    js = """
(function () {
  var KEY = %(key)s, COLORS = %(colors)s, FLAG = %(flag)s, EVERY = %(every)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  // Written only when it is not already false, so the repeated calls below
  // cost a read and nothing else.
  function pin() {
    var a = api();
    if (!a) return;
    try {
      if (a.local.get(KEY) !== false) a.local.set(KEY, false);
    } catch (e) { }
  }

  function root() {
    var a = api();
    try {
      var o = a.space.internals().platform;
      while (o.parent) o = o.parent;
      return o;
    } catch (e) { return null; }
  }

  // The carved height field, found by the colour its own setup assigns it.
  function findStock() {
    var r = root();
    if (!r) return null;
    var hit = null;
    r.traverse(function (o) {
      if (hit || !o.isMesh || !o.material || !o.material.color) return;
      // our own floor wears the same colour and material; skip it
      if (o.userData && o.userData.piccolaIsFloor) return;
      if (o.material.type !== 'MeshPhongMaterial') return;
      if (COLORS.indexOf(o.material.color.getHexString().toLowerCase()) < 0) return;
      hit = o;
    });
    return hit;
  }

  // One plane at the bottom of the block, facing down. Rotated rather than
  // built from vertices so the normal is unambiguous: a PlaneGeometry faces
  // +Z, and a half turn about X makes it face -Z, which is the only side of
  // it anyone can ever see.
  function addFloor(mesh) {
    var THREE = window.THREE;
    if (!THREE || !mesh.geometry) return;
    mesh.geometry.computeBoundingBox();
    var b = mesh.geometry.boundingBox;
    if (!b) return;
    var w = b.max.x - b.min.x, d = b.max.y - b.min.y;
    if (!(w > 0 && d > 0)) return;
    var mat = new THREE.MeshPhongMaterial({
      flatShading: true,
      transparent: false,
      color: mesh.material.color.getHex(),
      side: THREE.FrontSide
    });
    var floor = new THREE.Mesh(new THREE.PlaneGeometry(w, d), mat);
    floor.rotation.x = Math.PI;
    floor.userData.piccolaIsFloor = true;
    floor.position.set((b.min.x + b.max.x) / 2, (b.min.y + b.max.y) / 2, b.min.z);
    mesh.add(floor);
    mesh[FLAG] = true;
  }

  function tick() {
    var mesh = findStock();
    if (!mesh || mesh[FLAG]) return;
    addFloor(mesh);
    var a = api();
    try { a.space.update(); } catch (e) { }
  }

  function arm() {
    var a = api();
    if (!a) return false;
    pin();
    try {
      // `animate` is emitted by the animator immediately BEFORE it reads the
      // stored value, and listeners run synchronously, so this lands in time.
      a.event.on('animate', pin);
      a.event.on('init-done', pin);
      a.event.on('settings.load', pin);
      a.event.on('settings.saved', pin);
    } catch (e) { }
    setInterval(function () { try { tick(); } catch (e) { } }, EVERY);
    return true;
  }

  var tries = 600;
  (function wait() {
    var ok = false;
    try { ok = arm(); } catch (e) { }
    if (ok) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "key": json.dumps(TRANS_KEY),
        "colors": json.dumps(list(STOCK_COLORS)),
        "flag": json.dumps(FLOOR_FLAG),
        "every": json.dumps(INTERVAL_MS),
    }
    assert "%" not in js, "unescaped % survived formatting in anim_solid_js()"
    return js
