"""
ui/cam_render_solid.py
Kiri:Moto's Render menu starts on *Solid* every time the CAM page loads.

Kiri keeps the last render choice -- *solid*, *wireframe* or *transparent*
(ghost) -- in its local store (``model.opacity``, ``model.wireframe``,
``model.wireframe.opacity``; ``app/visuals.js``, ``app/init/sync.js``) and
brings it back at the next start. One click on Wire or Ghost therefore became
the look of every later session, and the part showed see-through or as a mesh
when it was opened next. The operator wants Solid as the default.

So on every load the stored choice is set to solid before Kiri reads it, and
once the workspace's parts are on the platform the solid look is applied to
them the same way the *Solid* menu entry does (``set_wireframe(false)``,
``set_opacity(1)``). Choosing Wire or Ghost during a session still works; it
just does not outlive the session. *Toggle Edges* is a separate switch and is
left as it is.

After Generate the part keeps the Render choice too: Kiri redraws it at a
fixed 0.2 for the slice view (``color/cam.js`` ``sliced_opacity``), which made a
Solid part a yellow ghost behind the toolpath. On ``slice.end`` it gets the
stored choice back, solid with a slight depth offset so toolpath lines on its
surface stay in front (as ``ui/cam_preview_solid.py`` does for the preview).
"""

from __future__ import annotations


def render_solid_js() -> str:
    """JS making Solid the render mode at the start of every CAM session."""
    return r"""
(function () {
  function api() { try { return window.kiri && window.kiri.api; } catch (e) { return null; } }

  // the stored choice, before Kiri's own start-up reads it
  var stored = false;
  function store(a) {
    try {
      a.local.set('model.wireframe', false);
      a.local.set('model.opacity', 1.0);
      a.local.set('model.wireframe.opacity', 1.0);
      stored = true;
    } catch (e) { }
  }

  // and the parts already on the platform, once there are any
  var tries = 300;   // 300 x 100 ms: the workspace restores within seconds
  (function wait() {
    var a = api();
    if (a && a.local && !stored) store(a);
    var any = false;
    try { a.widgets.each(function () { any = true; }); } catch (e) { }
    if (a && any && a.view && a.visuals) {
      try {
        a.view.set_wireframe(false);
        a.visuals.set_opacity(1.0);
      } catch (e) { }
      return;
    }
    if (tries-- > 0) setTimeout(wait, 100);
  })();

  // After Generate (the slice view) Kiri redraws the part at a fixed 0.2
  // (color/cam.js sliced_opacity, function.js) whatever Render says, so a
  // Solid part turned into a yellow ghost behind the toolpath. It now gets
  // the Render choice back: solid stays solid -- pushed a hair back in depth
  // so toolpath lines lying on its surface still show -- and Ghost / Wire
  // chosen in the session still show as such.
  function sliceLook() {
    var a = api();
    if (!a || !a.widgets) return;
    try { if (!a.mode.is_cam()) return; } catch (e) { return; }
    try { if (a.view.is_preview && a.view.is_preview()) return; } catch (e) { }
    var v = 1.0;
    try {
      var got = a.local.getFloat('model.opacity');
      if (got !== null && got !== undefined && isFinite(got)) v = got;
    } catch (e) { }
    var solid = v >= 1;
    a.widgets.each(function (w) {
      var m;
      try { m = w.getMaterial(); } catch (e) { return; }
      if (!m) return;
      m.opacity = v;
      m.transparent = !solid;
      m.visible = v > 0;
      m.polygonOffset = solid;
      m.polygonOffsetFactor = solid ? 1 : 0;
      m.polygonOffsetUnits = solid ? 1 : 0;
      m.needsUpdate = true;
    });
    try { a.space.update(); } catch (e) { }
  }
  var hooked = 400;
  (function hook() {
    var a = api();
    if (a && a.event && a.event.on) {
      try { a.event.on('slice.end', function () { setTimeout(sliceLook, 0); }); } catch (e) { }
      return;
    }
    if (hooked-- > 0) setTimeout(hook, 50);
  })();
})();
"""
