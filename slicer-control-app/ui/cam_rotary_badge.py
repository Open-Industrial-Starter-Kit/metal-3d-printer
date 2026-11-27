"""
ui/cam_rotary_badge.py
A "Rotary mode" watermark in the top-left corner of the Kiri:Moto viewport,
shown while the rotary switch is on.

Why
---
Rotary mode changes what every operation does -- Z is measured from the axis,
the stock is a cylinder, the rotary operations appear -- but the only sign of
it is one ticked checkbox at the top of the left panel (ui/cam_rotary.py). With
the panel scrolled, or the eye on the part, it is easy to set up a whole job
without noticing which mode it is in. The badge sits over the viewport itself,
where the eye already is.

What it reads
-------------
``api.ui.camStockIndexed``, the same ``<input>`` ui/cam_rotary.py moves: that
checkbox is the real rotary switch (see that module's notes). The badge also
needs the checkbox to be *showing*, since Kiri keeps the element when it hides
the CAM rows in another mode -- a ticked but hidden switch means "not in CAM",
not "rotary on".

When it updates
---------------
On the checkbox's own ``change``, on the Kiri events that reload settings or
switch mode, and on a slow poll as a backstop: a settings import or a profile
switch can set the checkbox from script, which fires no ``change`` event.

Where
-----
It starts in the corner and steps down below whatever is already there: the
part-size chip (ui/cam_part_size.py) while a part is selected, and Kiri's
Arrange / Generate bar when a narrow window brings that bar's left end over
the corner. Re-checked on every update and on window resize.

Absolutely positioned in ``#mid-mid``, the region between the two side panels,
so its left edge follows the settings panel rather than the window. Every div
in Kiri is ``position: relative`` (index.css), so ``#mid-mid`` is already its
containing block; out of flow, ``#mid-mid``'s flex layout is not consulted (see
ui/cam_views.py for what happens otherwise). ``pointer-events: none`` -- it is
a watermark, and must never catch a click meant for the part underneath.
"""

from __future__ import annotations

import json

from ui.cam_part_size import CHIP_ID as _PART_CHIP_ID

BADGE_ID = "piccola-rotary-badge"

#: The region the badge is anchored inside -- the viewport between the panels.
HOST_ID = "mid-mid"

TEXT = "ROTARY MODE"

#: Inset from the viewport's top-left corner, and the gap kept under the
#: part-size chip (ui/cam_part_size.py), which shares this corner while a part
#: is selected: the badge drops below it rather than sitting underneath it.
TOP_PX = 12
GAP_PX = 6

#: The first button of Kiri's Arrange / Generate / Animate / Export bar
#: (`init/menu.js`); its parent is the bar. On a narrow window the bar's left
#: end reaches this corner, and the badge steps down below it.
TOOLBAR_BTN_ID = "view-arrange"

#: Backstop poll for changes made from script (settings import, profile load).
POLL_MS = 1000


def css() -> str:
    """Styling for the rotary-mode badge."""
    return "\n".join([
        f"#{BADGE_ID} {{ position: absolute; top: {TOP_PX}px; left: 14px;"
        f" z-index: 205; pointer-events: none; display: none;"
        f" align-items: center; gap: 6px; padding: 4px 10px;"
        f" border-radius: 4px; font-size: 12px; font-weight: 700;"
        f" letter-spacing: 0.08em; color: #b45309;"
        f" background: rgba(245, 158, 11, 0.14);"
        f" border: 1px solid rgba(217, 119, 6, 0.55);"
        f" user-select: none; }}",
        f"#{BADGE_ID}.on {{ display: flex; }}",
        f"#{BADGE_ID} svg {{ width: 14px; height: 14px; flex: none; }}",
    ])


def rotary_badge_js() -> str:
    """JS showing the badge over the viewport while rotary mode is on."""
    js = """
(function () {
  var BADGE = %(badge)s, HOST = %(host)s, TEXT = %(text)s, POLL = %(poll)s;
  var CHIP = %(chip)s, TOP = %(top)s, GAP = %(gap)s;
  var TOOLBAR_BTN = %(toolbar_btn)s;
  // a circular arrow: the stock turning on the A axis
  var ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
    ' stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v4.5h-4.5"/></svg>';

  function badge() {
    var b = document.getElementById(BADGE);
    if (b) return b;
    var host = document.getElementById(HOST);
    if (!host) return null;
    b = document.createElement('div');
    b.id = BADGE;
    b.setAttribute('aria-live', 'polite');
    b.innerHTML = ICON + '<span></span>';
    b.lastChild.textContent = TEXT;
    host.appendChild(b);
    return b;
  }

  function update() {
    var api = window.kiri && window.kiri.api;
    var sw = api && api.ui && api.ui.camStockIndexed;
    var b = badge();
    if (!b) return;
    // offsetParent is null while the row (or the CAM panel) is hidden
    var on = !!(sw && sw.checked && sw.offsetParent !== null);
    if (b.classList.contains('on') !== on) b.classList.toggle('on', on);
    if (on) settle(b);
  }

  // Kiri's Arrange / Generate / Animate / Export bar, as the box round its
  // visible buttons. Not the bar element itself: #mode-tools is laid out
  // `left: 0; right: 0` with its buttons centred, so its own rect is the full
  // width and would push the badge down on every window. The buttons are
  // what reach into this corner on a narrow window.
  function toolbar() {
    var first = document.getElementById(TOOLBAR_BTN);
    var bar = first && first.parentElement;
    if (!bar) return null;
    var box = null;
    [].forEach.call(bar.children, function (c) {
      var r = c.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      if (!box) box = { left: r.left, right: r.right, top: r.top,
                        bottom: r.bottom, height: 1 };
      box.left = Math.min(box.left, r.left);
      box.right = Math.max(box.right, r.right);
      box.top = Math.min(box.top, r.top);
      box.bottom = Math.max(box.bottom, r.bottom);
    });
    return box;
  }

  // Start in the corner and step down below anything already there: the
  // part-size chip while it shows, and the action bar when the two would
  // share the same stretch of the top edge. A zero-height rect is what
  // display:none reports, so a hidden obstacle is skipped.
  function settle(b) {
    var host = document.getElementById(HOST);
    if (!host) return;
    var hostTop = host.getBoundingClientRect().top;
    var br = b.getBoundingClientRect();
    var top = TOP;
    var chip = document.getElementById(CHIP);
    [chip && chip.getBoundingClientRect(), toolbar()].forEach(function (r) {
      if (!r || r.height <= 0) return;
      var sharesX = br.left < r.right && br.right > r.left;
      var sharesY = hostTop + top < r.bottom &&
                    hostTop + top + br.height > r.top;
      if (sharesX && sharesY) top = Math.round(r.bottom - hostTop + GAP);
    });
    var px = top + 'px';
    if (b.style.top !== px) b.style.top = px;
  }

  function install() {
    var api = window.kiri && window.kiri.api;
    if (!api || !api.event || !api.ui || !api.ui.camStockIndexed) return false;
    if (!document.getElementById(HOST)) return false;
    api.ui.camStockIndexed.addEventListener('change', update);
    ['boolean.update', 'settings.load', 'settings.saved', 'mode.set',
     'widget.add', 'platform.layout', 'widget.select', 'widget.deselect',
     'widget.delete', 'selection.scale', 'selection.rotate'].forEach(function (ev) {
      try { api.event.on(ev, function () { setTimeout(update, 0); }); }
      catch (e) { }
    });
    window.addEventListener('resize', update);
    setInterval(update, POLL);
    update();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "badge": json.dumps(BADGE_ID),
        "host": json.dumps(HOST_ID),
        "text": json.dumps(TEXT),
        "poll": json.dumps(POLL_MS),
        "chip": json.dumps(_PART_CHIP_ID),
        "top": json.dumps(TOP_PX),
        "gap": json.dumps(GAP_PX),
        "toolbar_btn": json.dumps(TOOLBAR_BTN_ID),
    }
    assert "%" not in js, "unescaped % survived formatting in rotary_badge_js()"
    return js
