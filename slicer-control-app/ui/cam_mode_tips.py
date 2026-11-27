"""
ui/cam_mode_tips.py
Explains the area operation's **Mode** menu -- clear, trace, surface -- with
a picture of each.

The gap
-------
Mode is the first and most consequential choice in the area pop-up (the one
behind Area and Scallop): it decides whether the area is emptied, its edge
followed, or its 3D shape ridden. Yet ``cl-ops.js`` builds it as
``UC.newSelect(LANG.mo_menu, { post: opRender }, "opmode")`` -- no ``title``,
so no tooltip at all, and three bare words in a drop-down.

What this does
--------------
Stamps the Mode row with a ``title`` (one line) and with ``ART_ATTR`` naming
the ``area-mode`` picture in ``ui/cam_op_art.py``: three rows, a small scene
and a few words each. ``ui/cam_tooltips.py`` draws both, as it does for the
operation menu -- the same door every other tooltip comes through.

Finding the row
---------------
By its options, not by position: the select in ``#area-op`` whose values are
``clear``, ``trace`` and ``surface``. The pop-up is built when Kiri first needs
it, so the row is looked for on every pointer move into a pop-up (capture
phase, so the title is there before the tooltip panel reads it on the same
event), and once stamped it is left alone.
"""

from __future__ import annotations

import json

#: Must match ui/cam_op_art.py's ART_ATTR and its OP_ART key.
ART_ATTR = "data-piccola-art"
ART_KEY = "area-mode"

#: The pop-up the area operation (and so Scallop) uses.
POPUP_ID = "area-op"

#: Values of the Mode select's options.
MODES = ("clear", "trace", "surface")

#: The tooltip's text under its heading; the picture says the rest.
TITLE = "How the selected area is cut. Three choices:"

DONE_ATTR = "data-piccola-mode-tip"


def mode_tips_js() -> str:
    """JS stamping the area pop-up's Mode row with its tooltip and picture."""
    js = """
(function () {
  var POPUP = %(popup)s, MODES = %(modes)s, TITLE = %(title)s;
  var ART_ATTR = %(art_attr)s, ART_KEY = %(art_key)s, DONE = %(done)s;

  function isModeSelect(sel) {
    var vals = [].map.call(sel.options || [], function (o) { return o.value; });
    return MODES.every(function (m) { return vals.indexOf(m) >= 0; });
  }

  function stamp() {
    var pop = document.getElementById(POPUP);
    if (!pop) return false;
    var sels = pop.querySelectorAll('select');
    for (var i = 0; i < sels.length; i++) {
      if (!isModeSelect(sels[i])) continue;
      var row = sels[i].closest('.var-row') || sels[i].parentElement;
      if (!row || row.getAttribute(DONE)) return true;
      // never overwrite a title upstream starts setting for itself
      if (!row.getAttribute('title')) row.setAttribute('title', TITLE);
      row.setAttribute(ART_ATTR, ART_KEY);
      row.setAttribute(DONE, '1');
      return true;
    }
    return false;
  }

  var done = false;
  document.addEventListener('mouseover', function (ev) {
    if (done) return;
    var t = ev.target;
    if (!t || !t.closest || !t.closest('#' + POPUP)) return;
    try { done = stamp(); } catch (e) { }
  }, true);
})();
""" % {
        "popup": json.dumps(POPUP_ID),
        "modes": json.dumps(list(MODES)),
        "title": json.dumps(TITLE),
        "art_attr": json.dumps(ART_ATTR),
        "art_key": json.dumps(ART_KEY),
        "done": json.dumps(DONE_ATTR),
    }
    assert "%" not in js, "unescaped % survived formatting in mode_tips_js()"
    return js
