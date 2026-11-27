"""
ui/cam_anim_markers.py
Marks where each operation starts on the animation timeline.

Why
---
The timeline is one undivided run however many operations went into the job, so
while an animation plays there is nothing that says which toolpath is cutting,
and while dragging it there is nothing to aim at. "Show me the start of the
pocket" means scrubbing until the tool changes and guessing.

Patch 0014 carries the missing half: the worker tags each path record with its
operation before flattening them into one list, and sends the contiguous runs as
`{ name, start }` boundaries, which the app-side animators re-emit as
`animate.segments` with the total. This draws them.

How
---
A strip is laid exactly over the timeline, matched to its box and kept there by
a ResizeObserver, carrying one tick per operation at `start / total` of the
track. The ticks cross the slider itself, sized just over the track so they
divide it without becoming a rule through the whole control.

Hovering the track names the operation under the cursor in a small chip above
it, which is the other half of the same question: the ticks say where the job
divides, the readout says which side of a division you are looking at. The chip
is `pointer-events: none` like the strip, so it never intercepts a drag.

They are dividers, not start flags: a tick sits where one operation's records
give way to the next in the run, so it separates toolpaths rather than promising
that anything begins at exactly that instant. Names are deliberately not drawn
-- the division is the useful part, and a row of labels over a short track is
noise. It is inert (`pointer-events: none`) so dragging and
clicking the range underneath behave exactly as before -- the markers are a
readout, never a control.

Ticks are inset by half a thumb width at each end. A range input's thumb travels
between its own centres, not the full track, so a naive `left: pct%` drifts from
what the thumb actually does -- most visibly at the ends, where the first marker
would sit left of where the thumb can reach.

"""

from __future__ import annotations

import json

#: Event the patched animators emit at setup: { segments: [{name, start}], total }.
SEGMENTS_EVENT = "animate.segments"

#: Half the range thumb's width, in px. See the module docstring on inset.
THUMB_HALF_PX = 7

#: Height of the range control's own track, in px. Chromium's default -- the
#: served CSS styles neither ``::-webkit-slider-runnable-track`` nor the thumb,
#: and a track height is not reachable from script, so it is named here rather
#: than measured.
TRACK_H_PX = 6

#: Tick height as a multiple of the track. Just over 1 so the divider reads as
#: crossing the bar rather than being swallowed by it, without turning into the
#: full-height rule that spanned the whole control before.
TICK_SCALE = 1.05

#: Tick colour. Dark, with a white hairline in the CSS below.
TICK_COLOR = "#2B2B2B"

#: Hover readout colours. Dark chip, light text -- it sits over a pale bar and
#: has to be legible at a glance without being another thing to decode.
TIP_BG = "rgba(43, 43, 43, 0.92)"
TIP_FG = "#F2F2F2"


def anim_markers_css() -> str:
    """CSS for the marker strip over the animation timeline."""
    return """
.piccola-anim-markers {
    position: fixed;
    pointer-events: none;
    z-index: 1000;
}
.piccola-anim-markers .pam-tick {
    position: absolute;
    top: 50%%;
    transform: translateY(-50%%);
    width: 2px;
    /* scaled with the timeline (ui/cam_anim_end.py zooms it): --pam-zoom is
       the control's zoom, written onto the strip when it is placed */
    height: calc(%(h).2fpx * var(--pam-zoom, 1));
    background: %(tick)s;
    /* A hairline of white around it, so the same tick reads against the blue
       fill on the played side AND the grey track on the unplayed side. A flat
       dark tick disappears into the blue; a flat light one disappears into the
       grey. */
    box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.75);
    border-radius: 1px;
}
.piccola-anim-tip {
    position: fixed;
    pointer-events: none;
    z-index: 1001;
    background: %(tipbg)s;
    color: %(tipfg)s;
    font-size: 10px;
    line-height: 12px;
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
    transform: translateX(-50%%);
    display: none;
}
""" % {"tick": TICK_COLOR, "h": TRACK_H_PX * TICK_SCALE,
       "tipbg": TIP_BG, "tipfg": TIP_FG}


def anim_markers_js() -> str:
    """JS drawing operation start markers over the animation timeline."""
    return """
(function () {
  var EVENT = %(event)s, THUMB = %(thumb)s;
  var strip = null, segs = null, total = 0, ro = null, rafId = null;
  var tip = null, boundEl = null;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  // The timeline may be drawn larger than Kiri's own (CSS zoom, see
  // ui/cam_anim_end.py); the thumb, and so the inset, grow with it.
  function zoomOf(tl) {
    var z = 1;
    try { z = parseFloat(getComputedStyle(tl).zoom) || 1; } catch (e) {}
    return z;
  }

  function timelineEl() {
    var a = api();
    try { return a && a.ui && a.ui.anim ? a.ui.anim.timeline : null; }
    catch (e) { return null; }
  }

  function ensureStrip(tl) {
    if (strip && strip.parentNode) return strip;
    strip = document.createElement('div');
    strip.className = 'piccola-anim-markers';
    // On the body and positioned `fixed` from the control's screen rect. The
    // first version hung it off `tl.offsetParent` with `absolute`, which put it
    // wherever that happened to be -- in practice below the transport bar,
    // which sits on the bottom edge of the window, so the ticks rendered
    // correctly and were clipped off-screen.
    document.body.appendChild(strip);
    return strip;
  }

  // Last rect the strip was placed against, so the follow loop below only
  // touches the DOM when something actually moved.
  var lastRect = null;

  function sameRect(a, b) {
    return a && b && a.left === b.left && a.top === b.top && a.width === b.width
      && a.height === b.height;
  }

  function place() {
    var tl = timelineEl();
    if (!tl || !strip) return;
    // Measured from the control's own screen rect every time, because the
    // transport bar reflows with the window and a strip pinned once drifts off
    // the track. ABOVE the slider: the bar is on the bottom edge of the window,
    // so anything below it is off-screen, and above is where the names read
    // against the run anyway.
    var r = tl.getBoundingClientRect();
    if (!r || !r.width) return;
    // Laid exactly over the control, so the ticks cross the slider itself
    // rather than floating in the bar above it. Labels are pulled above the
    // strip in CSS, which keeps them clear of the thumb.
    if (sameRect(lastRect, r)) return;
    lastRect = { left: r.left, top: r.top, width: r.width, height: r.height };
    strip.style.left   = r.left + 'px';
    strip.style.top    = r.top + 'px';
    strip.style.width  = r.width + 'px';
    strip.style.height = r.height + 'px';
    strip.style.setProperty('--pam-zoom', String(zoomOf(tl)));
    // Width drives where the ticks land, so a reflow needs them redrawn too.
    draw();
  }

  // The transport bar reflows WHILE the job plays: the X / Y / Z / %% readouts
  // change width as the numbers change, which slides the slider sideways
  // underneath a strip that is pinned to a screen rect. A ResizeObserver does
  // not help -- the control keeps its size and only moves, and observers do not
  // report position. So the rect is followed for as long as there is something
  // drawn, and the DOM is touched only when it actually differs.
  function follow() {
    if (!segs || !strip) { rafId = null; return; }
    place();
    rafId = requestAnimationFrame(follow);
  }

  function startFollowing() {
    if (rafId === null) rafId = requestAnimationFrame(follow);
  }

  function render() {
    var tl = timelineEl();
    if (!tl || !segs || !segs.length || !total) {
      if (strip) strip.innerHTML = '';
      return;
    }
    ensureStrip(tl);
    place();
    draw();
  }

  function draw() {
    var tl = timelineEl();
    if (!tl || !strip || !segs || !segs.length || !total) return;
    var inset = THUMB * zoomOf(tl);
    var usable = Math.max(1, tl.getBoundingClientRect().width - inset * 2);
    var html = [];
    for (var i = 0; i < segs.length; i++) {
      var frac = Math.max(0, Math.min(1, segs[i].start / total));
      // Inset by half a thumb: the thumb travels between its own centres, not
      // the full width of the track.
      var x = inset + frac * usable;
      html.push('<div class="pam-tick" style="left:' + x.toFixed(1) + 'px"></div>');
    }
    strip.innerHTML = html.join('');
  }

  function onSegments(payload) {
    if (!payload) return;
    segs = payload.segments || null;
    total = payload.total || 0;
    lastRect = null;          // force a re-place on the next frame
    render();
    bindHover();
    startFollowing();
  }

  function clear() {
    segs = null; total = 0; lastRect = null;
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    if (strip) strip.innerHTML = '';
    hideTip();
  }

  function ensureTip() {
    if (tip && tip.parentNode) return tip;
    tip = document.createElement('div');
    tip.className = 'piccola-anim-tip';
    document.body.appendChild(tip);
    return tip;
  }

  // Which operation owns a point on the track. The segments are boundaries --
  // { name, start } in ascending order -- so the owner of an index is the last
  // one that starts at or before it.
  function nameAt(frac) {
    if (!segs || !segs.length || !total) return null;
    var idx = frac * total, found = null;
    for (var i = 0; i < segs.length; i++) {
      if (segs[i].start <= idx) found = segs[i]; else break;
    }
    return found ? found.name : null;
  }

  function onMove(ev) {
    var tl = timelineEl();
    if (!tl || !segs || !segs.length || !total) return hideTip();
    var r = tl.getBoundingClientRect();
    if (!r || !r.width) return hideTip();
    // Inverse of the tick placement: the thumb travels between its own centres,
    // so the usable track is inset by half a thumb at each end.
    var inset = THUMB * zoomOf(tl);
    var usable = Math.max(1, r.width - inset * 2);
    var frac = (ev.clientX - r.left - inset) / usable;
    frac = Math.max(0, Math.min(1, frac));
    var name = nameAt(frac);
    if (!name) return hideTip();
    var t = ensureTip();
    t.textContent = name;
    t.style.display = 'block';
    // Above the track, following the cursor, clamped so it cannot run off the
    // window at either end.
    var x = Math.max(r.left + 4, Math.min(r.right - 4, ev.clientX));
    t.style.left = x + 'px';
    t.style.top = (r.top - 18) + 'px';
  }

  function hideTip() {
    if (tip) tip.style.display = 'none';
  }

  // Bound to whichever element is the timeline now: entering animate can build
  // a fresh control, and listeners left on the old one would read a stale rect.
  function bindHover() {
    var tl = timelineEl();
    if (!tl || tl === boundEl) return;
    if (boundEl) {
      boundEl.removeEventListener('mousemove', onMove);
      boundEl.removeEventListener('mouseleave', hideTip);
    }
    tl.addEventListener('mousemove', onMove);
    tl.addEventListener('mouseleave', hideTip);
    boundEl = tl;
  }

  function install() {
    var a = api();
    if (!a || !a.event) return false;
    var tl = timelineEl();
    if (!tl) return false;
    // Marked on the timeline element, not on `api`: a property written to the
    // api object does not survive being read back (it is not a plain own-
    // property bag), so a guard kept there silently never guards.
    if (tl.__piccolaAnimMarkers) return true;
    tl.__piccolaAnimMarkers = true;
    try { a.event.on(EVENT, onSegments); } catch (e) { }
    // Leaving animate takes the transport with it; a strip left behind would
    // hang over whatever replaces it.
    try { a.event.on('view.set', function () {
      try { if (!a.view.is_animate()) clear(); } catch (e) { }
    }); } catch (e) { }
    bindHover();
    try {
      ro = new ResizeObserver(function () { place(); render(); });
      ro.observe(tl);
    } catch (e) { }
    window.addEventListener('resize', function () { place(); render(); });
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "event": json.dumps(SEGMENTS_EVENT),
        "thumb": json.dumps(THUMB_HALF_PX),
    }
