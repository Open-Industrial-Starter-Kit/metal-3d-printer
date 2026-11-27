"""
ui/cam_anim_layers.py
Keeps the toolpath layer checkboxes on screen while an animation runs.

Why
---
Two separate things were hiding them, and fixing one left the other.

The first was destructive: `animate()` called `STACKS.clear()`, which empties
the stack registry, `UI.dyn` and the `#layers` markup outright, so the layers
could not come back without slicing again. That is
`tools/patches/kirimoto/0011-keep-toolpath-layers-in-animate.patch`, and with it
the toolpath geometry stays drawn under the cut.

The second is this one, and it is why the *list* was still missing after the
paths came back. `#layers` — the row of per-layer checkboxes with the colour
swatches — is a child of `#layer-slider`, sharing that container with the layer
scrub bar, and `slider.hide()` takes both down together::

    hide() {
        this.#ui.layers.style.display = 'none';    // the checkboxes
        this.#slider.div.style.display = 'none';   // the scrub bar
    }

`animate()` calls `api.hide.slider()` one line above the clear, so the row is
hidden by inline style whatever the stacks are doing.

Hiding the scrub bar is right: the animation has its own timeline, and two
different scrubbers over one job is worse than none. The checkboxes are not
part of that — they choose which operations are drawn, which is a question
about the *view*, and watching a cut is exactly when it is worth asking. So the
bar stays hidden and the row comes back.

And showing it is still not enough
----------------------------------
Un-hidden, the row is *behind* the animation transport. Both are absolutely
positioned against `#mid-mid` and both are pinned to its bottom edge::

    #layer-slider { bottom: max(3px, env(safe-area-inset-bottom)); }   /* no z-index */
    #layer-animate { bottom: 0; z-index: 200; }                        /* opaque */

The transport is 40-75 px tall depending on whether it has wrapped, has a
`set2-group` background, and wins on z-index, so it covers the row completely.
Nothing is visibly wrong — the checkboxes are in the page, laid out, and
painted under an opaque panel.

So the row is lifted clear of it, measured rather than assumed, exactly as
`ui/cam_views.py` lifts the view bar: read the transport's
`getBoundingClientRect()`, ignore it if it measures zero-height (hidden), and
set the row's `bottom` above it. The stack from the bottom up ends up
transport, then the checkbox row, then the view bar — which composes for free,
because the view bar measures `#layer-slider` too and follows it up.

How
---
By clearing one inline style, on the events that can set it. There is no need
to intercept `slider.hide()` — its two writes are independent, and only one of
them is unwanted, so letting it run and then re-showing the row keeps the scrub
bar hidden for free.

`api.view.is_animate()` is the gate rather than the event's payload, because
`view.set` fires with a numeric mode whose constants are not on the public
surface. It is read *after* the event, by which point `viewMode` is already
assigned. Outside animate this does nothing at all: Kiri owns the row then, and
`slider.show()` is what puts it back.
"""

from __future__ import annotations

import json

#: The checkbox row. A child of ``#layer-slider``, which is why it is caught by
#: ``slider.hide()`` along with the scrub bar.
LAYERS_ID = "layers"

#: Events after which the row may have been hidden. `view.set` is the one that
#: matters — `animate()` hides the slider immediately before switching view —
#: and the animation's own setup runs on the others.
EVENTS = ("view.set", "slice.end", "preview.end", "code.loaded", "init-done",
          "stack.show", "stack.hide")

#: The container the row lives in, which is what gets lifted: the row itself is
#: in normal flow inside it.
SLIDER_ID = "layer-slider"

#: The animation transport, which shares the bottom edge and wins on z-index.
TRANSPORT_ID = "layer-animate"

#: The region both are positioned against.
HOST_ID = "mid-mid"

#: Space left between the row and the transport under it.
GAP_PX = 6

#: Layers whose checkbox is removed and whose state is decided here instead.
#: These are toolpath *display* layers, not operations: `prepare.js` names the
#: two path kinds `other: "moving"` and `action: "milling"`, and the animator
#: adds `tool` and `arrows` alongside them. Four checkboxes for one question is
#: more than it is worth, so the two that are never really a choice are pinned:
#:
#:   tool    on  -- watching the cutter is the point of the animation
#:   arrows  off -- direction arrows over a whole job are noise
#:
#: `moving` and `milling` keep their checkboxes: which of travel and cutting
#: you want to see is a real question and the answer changes.
#:
#: Pinned rather than defaulted, because the layer set is rebuilt from scratch
#: on every slice — `stacks.clear()` empties the registry and `create()` builds
#: it again — so a value set once would not survive the next generate.
PINNED_LAYERS = {"tool": True, "arrows": False}

#: Layers switched OFF each time an animation starts, leaving the stock
#: simulation on its own -- what other CAM packages show by default: the
#: material being cut, not a web of path lines over it. Unlike PINNED these keep
#: their checkboxes, so ticking one back on mid-animation works as normal; the
#: next animation starts clean again.
#:
#: Done on the transition INTO animate only. Kiri restores its remembered
#: per-view choice (`settings.labels["CAM-<view>-<label>"]`, written by the
#: stack.show / stack.hide handlers in init/input.js) at the end of the preview
#: that animate runs first, i.e. before the view switches -- so this lands
#: after it. The animate view's remembered value is set to off as well, so a
#: later `update_stack_labels()` while animating does not bring the lines back.
ANIM_DEFAULT_OFF = ("moving", "milling")


def keep_layers_js() -> str:
    """JS keeping the layer checkbox row visible during animation."""
    return """
(function () {
  var LAYERS = %(layers)s, EVENTS = %(events)s;
  var SLIDER = %(slider)s, TRANSPORT = %(transport)s, HOST = %(host)s;
  var GAP = %(gap)s, PINNED = %(pinned)s, DEFAULT_OFF = %(default_off)s;
  var wasAnimating = false;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function animating() {
    var a = api();
    try { return !!a.view.is_animate(); } catch (e) { return false; }
  }

  // Only the row. `#slider` -- the layer scrub bar in the same container -- is
  // left hidden: the animation has its own timeline, and two scrubbers over one
  // job is worse than one.
  function showLayers() {
    if (!animating()) return;
    var el = document.getElementById(LAYERS);
    if (!el || !el.style) return;
    if (el.style.display === 'none') el.style.display = '';
  }

  // The checkboxes for PINNED are removed and their layers set from here.
  // `api.stacks.setVisible` is public and sets the (now hidden) checkbox as
  // well as the materials, which matters: the checkbox is what a later
  // `setVisible` or rebuild reads back, so leaving it out of step would let a
  // stale value return.
  function pinLayers() {
    var a = api();
    var host = document.getElementById(LAYERS);
    if (!a || !host || !a.stacks) return;
    Object.keys(PINNED).forEach(function (label) {
      var want = !!PINNED[label];
      try { a.stacks.setVisible(label, want); } catch (e) { }
    });
    // and hide the rows themselves. newBoolean() puts the name in a <label>
    // inside the .var-row, so the row is found by what it says.
    var rows = host.querySelectorAll ? host.querySelectorAll('.var-row') : [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var lab = row.querySelector ? row.querySelector('label') : null;
      var text = lab ? (lab.textContent || '').trim() : '';
      if (Object.prototype.hasOwnProperty.call(PINNED, text)) {
        row.style.display = 'none';
      }
    }
  }

  // Path lines off at the start of every animation (see ANIM_DEFAULT_OFF).
  // Only on the transition into animate, so a box the operator ticks while
  // watching is not switched straight back off by the next event.
  function defaultOffOnEntry() {
    var now = animating();
    if (now && !wasAnimating) {
      var a = api();
      var labels = null, key = null;
      try {
        labels = a.conf.get().labels;
        key = a.mode.get() + '-' + a.view.get() + '-';
      } catch (e) { }
      DEFAULT_OFF.forEach(function (label) {
        try { a.stacks.setVisible(label, false); } catch (e) { }
        if (labels && key) labels[key + label] = false;
      });
    }
    wasAnimating = now;
  }

  function apply() { defaultOffOnEntry(); showLayers(); pinLayers(); lift(); }

  // Lift the row clear of the animation transport, which shares the bottom
  // edge, is opaque, and has z-index 200. Measured, not assumed: the transport
  // is one row or two depending on the window width.
  function lift() {
    var slider = document.getElementById(SLIDER);
    var host = document.getElementById(HOST);
    if (!slider || !slider.style || !host ||
        typeof host.getBoundingClientRect !== 'function') return;
    if (!animating()) {
      // outside animate the stylesheet's own bottom applies again
      if (slider.style.bottom) slider.style.bottom = '';
      return;
    }
    var anim = document.getElementById(TRANSPORT);
    var r = anim && typeof anim.getBoundingClientRect === 'function'
            ? anim.getBoundingClientRect() : null;
    var over = (r && r.height)
             ? (host.getBoundingClientRect().bottom - r.top) + GAP : 0;
    slider.style.bottom = over ? (over + 'px') : '';
    // the view bar measures #layer-slider, so it has to re-measure now that it
    // has moved; a resize is what it already listens to
    try {
      if (window.dispatchEvent && typeof Event === 'function') {
        window.dispatchEvent(new Event('resize'));
      }
    } catch (e) { }
  }

  function install() {
    var a = api();
    if (!a || !a.event || !a.view || !document.getElementById(LAYERS)) {
      return false;
    }
    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, apply); } catch (e) { }
    });
    // the animation is built after view.set in some paths, and building it
    // hides the slider again
    try { a.event.on('view.set', function () { setTimeout(apply, 0); }); }
    catch (e) { }
    // the transport changes height when it wraps, which changes how far the
    // row has to be lifted
    if (typeof ResizeObserver === 'function') {
      try {
        var ro = new ResizeObserver(lift);
        var t = document.getElementById(TRANSPORT);
        if (t) ro.observe(t);
      } catch (e) { }
    }
    apply();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "layers": json.dumps(LAYERS_ID),
        "events": json.dumps(list(EVENTS)),
        "slider": json.dumps(SLIDER_ID),
        "transport": json.dumps(TRANSPORT_ID),
        "host": json.dumps(HOST_ID),
        "gap": json.dumps(GAP_PX),
        "pinned": json.dumps(PINNED_LAYERS),
        "default_off": json.dumps(list(ANIM_DEFAULT_OFF)),
    }
