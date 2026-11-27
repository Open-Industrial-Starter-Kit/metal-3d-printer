"""
ui/cam_anim_after_export.py
Brings the animation back when the export dialog closes over Animate.

What happened
-------------
Export pressed while Animate is showing: the dialog opens, the G-code is
saved, the dialog closes -- and Animate is empty. No stock, no tool, the
render bar under the timeline back at 0 % and never moving. Seen 2026-11-18
on a rotary roller finish: Animate opened, the background render started,
the job was exported a few seconds later, and the view stayed blank.

Why
---
Upstream does it on purpose (``mode/cam/app/init-ui.js``)::

    api.event.on("function.export", (mode) => {
        if (env.isAnimate) {
            env.isAnimate = false;
            animFn().animate_clear(api);
        }
    });

Export needs the worker, and the animation is the heaviest thing in it, so
the animation is torn down first. The view is not changed, though: Kiri is
still in Animate (``view.get()`` reads ANIMATE) with nothing in it. Before
the render-ahead animator (Kiri:Moto patch 0070) that cost one press of
Animate; now it also throws the half-finished render away, and the render bar
(cleared by ``animate_clear2``) sits at 0 %.

What this does
--------------
Upstream's teardown is left as it is. When an export starts while Animate is
showing, this remembers it; when the next dialog closes (``modal.hide``) and
the view is still Animate, it presses Animate again (``api.function.animate``).
A job whose render was finished before is back at once from the app's
animation cache (cam_server.py, anim-cache); one that was not starts its render
again from the beginning. Leaving Animate in between, or pressing Animate
yourself, cancels it.
"""

from __future__ import annotations

import json

#: Kiri's view number for Animate (``VIEWS.ANIMATE``), used when the page does
#: not expose its constants.
ANIMATE_VIEW = 4

#: Delay before pressing Animate again after the dialog has closed (ms), so
#: the export's own clean-up runs first.
RESTART_MS = 150


def anim_after_export_js() -> str:
    """JS re-opening Animate after an export taken from it."""
    js = """
(function () {
  var FALLBACK = %(view)s, DELAY = %(delay)s;
  var again = false;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function animView(a) {
    try {
      var V = (a.const && a.const.VIEWS) || {};
      return V.ANIMATE !== undefined ? V.ANIMATE : FALLBACK;
    } catch (e) { return FALLBACK; }
  }

  function inAnimate(a) {
    try { return a.mode.is_cam() && a.view.get() === animView(a); }
    catch (e) { return false; }
  }

  function arm() {
    var a = api();
    if (!a || !a.event || !a.event.on || !a.view || !a.function) return false;
    a.event.on('function.export', function () {
      again = inAnimate(a);
    });
    a.event.on('function.animate', function () { again = false; });
    a.event.on('view.set', function (v) {
      if (v !== animView(a)) again = false;
    });
    a.event.on('modal.hide', function () {
      if (!again) return;
      again = false;
      setTimeout(function () {
        if (!inAnimate(a)) return;
        try { a.function.animate(); } catch (e) { }
      }, DELAY);
    });
    return true;
  }

  var left = 600;
  (function wait() {
    var ok = false;
    try { ok = arm(); } catch (e) { }
    if (ok) return;
    if (left-- > 0) setTimeout(wait, 100);
  })();
})();
""" % {
        "view": json.dumps(ANIMATE_VIEW),
        "delay": json.dumps(RESTART_MS),
    }
    assert "%" not in js, "unescaped % survived formatting in anim_after_export_js()"
    return js
