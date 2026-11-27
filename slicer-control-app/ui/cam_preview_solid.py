"""
ui/cam_preview_solid.py
Draws the part as an opaque solid behind the toolpaths in the preview view.

Why
---
In CAM mode Kiri ghosts the model in the preview: ``color/cam.js`` sets
``preview_opacity`` 0.2 in light grey (``0xdddddd``), applied in
``function.js`` at the start of the preview. At 20 % the part hides nothing,
so every layer's passes show through it top to bottom -- the far side of the
job, the passes under the floor, the walls behind the walls -- and the preview
reads as one tangle of lines over a grey haze. Other CAM packages draw the part
solid: what is behind it is hidden, and the passes around it read against a
surface.

So in the *preview* view the part is made opaque, in a mid slate that the
cyan cutting and green rapid lines of patch 0016 both stand out against.

The *slice* view (what Generate lands on) is not handled here: it follows
the Render menu (``ui/cam_render_solid.py``), in the part's own colour. A solid part was tried there too and works -- its passes are
black (``work/slice.js``: ``color = dark ? 0xbbbbbb : 0``), so it would want a
light grey part rather than this slate -- but it is not wanted for now.

How
---
After the events around the preview (``preview.begin``, ``preview.end``,
``view.set``), each widget's material gets ``opacity 1``, ``transparent
false``, and the colour -- through the material only (``color.set``), never
``widget.setColor``, which would SAVE it as the part's own colour.

The material also gets ``polygonOffset`` (factor/units 1), which pushes the
part's surface a hair back in depth. Toolpaths that lie exactly on a surface --
a contour or finishing pass, a facing pass on a flat -- would otherwise
z-fight with the now-opaque surface and flicker half-hidden; with the offset
the line always wins.

What it looked like before is recorded per material on the way in and put
back on the way out of the preview, so the slice view (and anything else)
gets Kiri's own ghost back rather than an opaque slate part that only the
preview asked for. *Animate* is not touched -- it has its own model handling
(patch 0018).
"""

from __future__ import annotations

import json

#: The part's colour in the preview: a mid slate, dark enough that the cyan
#: cutting moves and the green rapids both read against it, light enough that
#: the Phong shading still shows its shape.
PREVIEW_COLOR = 0x7D8B9C

#: Events after which the view, or Kiri's own ghost, may have changed.
EVENTS = ("preview.begin", "preview.end", "view.set", "slice.end")

#: Where the pre-preview look is kept on each material.
SAVED_KEY = "__piccolaPreviewSaved"


def preview_solid_js() -> str:
    """JS making the part opaque in the preview view only."""
    js = """
(function () {
  var COLOR = %(color)s, EVENTS = %(events)s, SAVED = %(saved)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function inPreview(a) {
    try { return !!a.view.is_preview(); } catch (e) { return false; }
  }

  function solid(m) {
    if (!m[SAVED]) {
      m[SAVED] = { opacity: m.opacity, transparent: m.transparent,
                   color: m.color ? m.color.getHex() : null };
    }
    m.opacity = 1;
    m.transparent = false;
    m.visible = true;
    if (m.color) m.color.setHex(COLOR);
    m.polygonOffset = true;
    m.polygonOffsetFactor = 1;
    m.polygonOffsetUnits = 1;
    m.needsUpdate = true;
  }

  function restore(m) {
    var s = m[SAVED];
    if (!s) return;
    m.opacity = s.opacity;
    m.transparent = s.transparent;
    if (m.color && s.color !== null) m.color.setHex(s.color);
    m.polygonOffset = false;
    m.needsUpdate = true;
    delete m[SAVED];
  }

  function apply() {
    var a = api();
    if (!a || !a.widgets) return;
    try { if (!a.mode.is_cam()) return; } catch (e) { return; }
    var on = inPreview(a);
    a.widgets.all().forEach(function (w) {
      var m;
      try { m = w.getMaterial(); } catch (e) { return; }
      if (!m) return;
      if (on) solid(m); else restore(m);
    });
    try { a.space.update(); } catch (e) { }
  }

  function later() { setTimeout(apply, 0); }

  function install() {
    var a = api();
    if (!a || !a.event || !a.view) return false;
    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, later); } catch (e) { }
    });
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "color": json.dumps(PREVIEW_COLOR),
        "events": json.dumps(list(EVENTS)),
        "saved": json.dumps(SAVED_KEY),
    }
    assert "%" not in js, "unescaped % survived formatting in preview_solid_js()"
    return js
