"""
ui/cam_minion_pool.py
Recycles Kiri:Moto's slicing workers after every slice, so their garbage cannot
pile up until the CAM page dies.

What happened
-------------
Generate a contour job, Animate it, go back and Generate again: on the second
or third round the whole CAM view went blank. The renderer had died with::

    V8 javascript OOM (CALL_AND_RETRY_LAST)

and the isolate that died held only 17-45 MB, which is not what running out of
memory looks like. Upstream Kiri 4.7.2 does it too, with none of this app's
patches applied -- it dies on round three instead of two.

Why
---
Kiri slices contour work in a pool of helper workers ("minions", one per core:
eleven here). Measured with every isolate attached over the debug port, after a
single contour slice::

    page 81 MB   main worker 81 MB   minions 63 .. 218 MB each  (~1.8 GB)

and after a forced garbage collection in each of them::

    page 14 MB   main worker 15 MB   minions < 3 MB each

So nothing is *leaked* -- Kiri does clear its minion caches (``clearCache``,
``trace_cleanup``) -- it is garbage nobody collects. Each minion decides when to
collect from its own heap, sees an idle worker far below its own limit, and
never bothers. But every isolate in a renderer allocates out of one shared
pointer-compression cage of about 4 GB, and no single isolate sees the cage
filling. The second slice doubled the minions to ~4 GB, and the next isolate
to ask for anything -- the page, mid-way through starting an animation -- was
refused. That is why a 17 MB heap could die of out-of-memory: it was not its
own memory that ran out.

What this does
--------------
After a slice (and after the toolpath preview that follows it) the pool is
stopped and started again. ``pool.stop()`` terminates every minion, which hands
its whole heap back at once whether or not it was ever collected; ``pool.start``
spins up fresh, empty ones for the next job. Measured with the fix: the minion
total returns to near zero after every slice instead of climbing.

Only when Kiri is idle. Terminating a minion that is still working leaves its
job waiting forever, so the recycle waits for ``client.isBusy()`` to read false
-- every request the page has outstanding answered -- rather than trusting that
``slice.end`` means nothing else has been queued behind it. And only when Kiri
runs a pool at all (``controller.threaded``, on by default): a pool the user has
switched off is not one to start.

Why not make the minions collect instead
----------------------------------------
JavaScript cannot ask V8 for a collection without ``--expose-gc``, which would
mean a Chromium flag in the app *and* a patch to ``run/minion.js``. Ending the
workers frees the same memory with neither, and a new worker costs a few
milliseconds against a slice that takes seconds.
"""

from __future__ import annotations

import json

#: Kiri events after which the pool may hold a slice's worth of garbage.
#: ``preview.end`` follows the toolpath preview, which can queue more work.
RECYCLE_EVENTS = ("slice.end", "slice.error", "preview.end")

#: How long to wait after the event before the first busy check, and how often
#: to look again while Kiri is busy (ms), and for how many checks at most.
SETTLE_MS = 300
RETRY_MS = 500
MAX_TRIES = 1200      # ten minutes of waiting for a long job to go idle


def minion_pool_js() -> str:
    """JS restarting Kiri's minion pool whenever a slice has finished."""
    js = """
(function () {
  var EVENTS = %(events)s, SETTLE = %(settle)s, RETRY = %(retry)s, MAX = %(max)s;
  var pending = false, tries = 0;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function threaded() {
    try { return api().conf.get().controller.threaded !== false; }
    catch (e) { return true; }
  }

  // Only once nothing is outstanding: a minion terminated mid-job never answers.
  function recycle() {
    var c = window.kiri_client;
    if (!c || !c.pool) { pending = false; return; }
    if (c.isBusy && c.isBusy()) {
      if (tries++ < MAX) { setTimeout(recycle, RETRY); return; }
      pending = false; tries = 0; return;
    }
    pending = false; tries = 0;
    if (!threaded()) return;
    try { c.pool.stop(); c.pool.start(); } catch (e) { }
  }

  // Several events can land for one slice; one recycle covers them all.
  function schedule() {
    if (pending) return;
    pending = true;
    setTimeout(recycle, SETTLE);
  }

  function arm() {
    var a = api();
    if (!a || !a.event || !window.kiri_client) return false;
    for (var i = 0; i < EVENTS.length; i++) {
      try { a.event.on(EVENTS[i], schedule); } catch (e) { }
    }
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
        "events": json.dumps(list(RECYCLE_EVENTS)),
        "settle": json.dumps(SETTLE_MS),
        "retry": json.dumps(RETRY_MS),
        "max": json.dumps(MAX_TRIES),
    }
    assert "%" not in js, "unescaped % survived formatting in minion_pool_js()"
    return js
