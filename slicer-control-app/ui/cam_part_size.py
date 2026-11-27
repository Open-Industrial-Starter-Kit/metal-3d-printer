"""The size of the selected part, shown in the viewport while it is selected.

Kiri measures a selection already — `selection.update_info()` writes it into
the *Size* dialog's X/Y/Z fields — but that dialog has to be opened to be
read, and it is also the place where typing changes the part, which is not
what someone checking a dimension wants to be near. This puts the same
measurement where the click happened: a small chip in the top-left corner of
the viewport, up while something is selected, gone when nothing is.

What it says: the selection's bounding box along X, Y and Z, as placed on the
bed (so a rotated part reports its rotated extents, which is what a stock
block has to cover). Several selected parts report the box round all of
them, with a count.

Where it sits: top-left of `#mid-mid`, the viewport strip between the two
side panels. That corner is free in CAM mode except for the animation
scrubber (`#top-slider`), which only shows while *Animate* runs; the bottom
edge carries Kiri's layer slider and transport and the app's own view bar.
The chip is parented to `#mid` (the fixed overlay layer, see
``ui/cam_views.py``) and its `left` is measured from `#mid-mid` on every show
and resize, because `#mid-mid` is a flex item whose left edge moves with the
left panel.
"""

from __future__ import annotations

import json

#: The chip.
CHIP_ID = "piccola-part-size"

#: The fixed overlay layer it is parented to, and the viewport strip whose
#: left edge it is aligned to.
HOST_ID = "mid"
ALIGN_ID = "mid-mid"

#: Inset from the corner.
GAP_PX = 10

#: Events after which the selection, or its size, may have changed. Kiri's
#: own `update_info()` runs on the same set.
EVENTS = ("widget.select", "widget.deselect", "widget.delete",
          "selection.scale", "selection.rotate", "selection.move",
          "platform.layout", "mode.set")


def css() -> str:
    """Styling for the chip."""
    return "\n".join([
        f"#{CHIP_ID} {{",
        "  position: absolute !important;",
        f"  top: {GAP_PX}px !important;",
        "  z-index: 210 !important;",
        "  padding: 4px 8px !important;",
        "  border-radius: 5px !important;",
        "  background: var(--menubar-back, #F0F0F0) !important;",
        "  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25) !important;",
        "  font: 11px/1.45 system-ui, -apple-system, 'Segoe UI', sans-serif"
        " !important;",
        "  color: #22282e !important;",
        "  text-transform: none !important;",
        "  white-space: nowrap !important;",
        "  pointer-events: none !important;",
        "  opacity: 0.92 !important;",
        "}",
        f"#{CHIP_ID} b {{ font-weight: 600 !important; }}",
    ])


def part_size_js() -> str:
    """JS showing the selected part's size while it is selected."""
    return """
(function () {
  var CHIP = %(chip)s, HOST = %(host)s, ALIGN = %(align)s, GAP = %(gap)s;
  var EVENTS = %(events)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function chip() {
    var el = document.getElementById(CHIP);
    if (el) return el;
    var host = document.getElementById(HOST);
    if (!host) return null;
    el = document.createElement('div');
    el.id = CHIP;
    el.style.display = 'none';
    host.appendChild(el);
    return el;
  }

  // The selection's box as placed on the bed. The same measurement Kiri's
  // Size dialog makes: each mesh's own box, moved to where its widget sits,
  // all of them joined.
  function measure() {
    var a = api();
    if (!a || !a.selection) return null;
    var ws = [];
    try { ws = a.selection.widgets() || []; } catch (e) { return null; }
    if (!ws.length) return null;
    var lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (var i = 0; i < ws.length; i++) {
      var w = ws[i], box, pos;
      try {
        box = w.mesh.getBoundingBox();
        pos = (w.track && w.track.pos) || { x: 0, y: 0, z: 0 };
      } catch (e) { return null; }
      if (!box || !box.min || !box.max) return null;
      var mn = [box.min.x + pos.x, box.min.y + pos.y, box.min.z + pos.z];
      var mx = [box.max.x + pos.x, box.max.y + pos.y, box.max.z + pos.z];
      for (var k = 0; k < 3; k++) {
        if (mn[k] < lo[k]) lo[k] = mn[k];
        if (mx[k] > hi[k]) hi[k] = mx[k];
      }
    }
    if (!isFinite(lo[0]) || !isFinite(hi[0])) return null;
    var s = 1;
    try { s = a.view.unit_scale() || 1; } catch (e) { }
    return {
      n: ws.length,
      x: (hi[0] - lo[0]) / s, y: (hi[1] - lo[1]) / s, z: (hi[2] - lo[2]) / s,
      unit: s === 1 ? 'mm' : 'in'
    };
  }

  function place(el) {
    var host = document.getElementById(HOST);
    var target = document.getElementById(ALIGN);
    if (!host || !target) return;
    var hb = host.getBoundingClientRect(), tb = target.getBoundingClientRect();
    el.style.left = Math.round(tb.left - hb.left + GAP) + 'px';
  }

  function fmt(v) { return v.toFixed(2); }

  function update() {
    var el = chip();
    if (!el) return;
    var m = measure();
    if (!m) { el.style.display = 'none'; return; }
    el.textContent = '';
    var lead = document.createElement('span');
    lead.appendChild(document.createTextNode(m.n > 1 ? m.n + ' Parts ' : 'Part '));
    el.appendChild(lead);
    var b = document.createElement('b');
    b.appendChild(document.createTextNode(
      fmt(m.x) + ' \\u00d7 ' + fmt(m.y) + ' \\u00d7 ' + fmt(m.z)));
    el.appendChild(b);
    el.appendChild(document.createTextNode(' ' + m.unit));
    el.title = 'Size of the selected part along X, Y and Z, as it sits on the bed.';
    el.style.display = 'block';
    place(el);
  }

  // A deferred pass: the select event fires before Kiri has finished moving
  // or re-measuring the widget, so the first reading can be the old one.
  function later() { setTimeout(update, 0); }

  function install() {
    var a = api();
    if (!a || !a.event || !document.getElementById(HOST)) return false;
    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, later); } catch (e) { }
    });
    window.addEventListener('resize', function () {
      var el = document.getElementById(CHIP);
      if (el && el.style.display !== 'none') place(el);
    });
    later();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "chip": json.dumps(CHIP_ID),
        "host": json.dumps(HOST_ID),
        "align": json.dumps(ALIGN_ID),
        "gap": json.dumps(GAP_PX),
        "events": json.dumps(list(EVENTS)),
    }
