"""
ui/cam_group_tips.py
Documents the collapsed sections of the CAM settings column.

The gap
-------
Every *control* in that column is documented: ``init-menu.js`` passes a
``title`` to each ``newInput``/``newBoolean``/``newSelect``, and
``ui/cam_tooltips.py`` draws it. The *sections* those controls live in are not.
``newGroup`` takes a label and no title at all::

    _____: newGroup(LANG.ct_menu, $('cam-tabs'), { modes:CAM, ... })

so "Tabs", "Stock", "Limits", "Output" and "Origin" are five words with nothing
behind them. They are also collapsed by default, which makes the gap worse than
an ordinary missing tooltip: the only way to find out what a section is for is
to open it and read the controls inside, and the reason to open it is knowing
what it is for. A name like "Limits" does not survive that on its own — it could
as easily mean travel limits, feed limits, or the machine's soft limits, and it
is in fact the depth of cut in Z plus a speed ceiling.

What this does
--------------
Stamps a ``title`` on each section header. Nothing else: the panel that draws it
already exists and already handles the heading, the line breaks and the
placement, and a header row's first child is its ``<a>`` label, so
``cam_tooltips.headingFor`` reads "Tabs" off it with no help.

Why a title rather than a new mechanism: it is the same door every other
tooltip in this app comes through, it degrades to a native tooltip if the panel
ever fails to load, and anything else that reads titles keeps working.

Writing the text
----------------
Each entry says what the *section* is for, not what its controls do — those are
documented individually, one layer down, and repeating them here would make the
longest tooltip in the app out of the one place a summary is wanted. Lowercase
and clipped, to match the voice of the strings already in ``lang-en.js``.

The wording is checked against what each group actually contains, at
``init-menu.js:125-182``, rather than against the name on the header. That is
the whole point: *Limits* holds the Z anchor, the top and bottom of the cut, the
clearance height and the two feed ceilings, and no reading of the word "limits"
gets you there.
"""

from __future__ import annotations

import json

#: Marks a header as done, so a second run is a no-op and a header that already
#: carries a title (from a future Kiri, say) is never overwritten.
DONE_ATTR = "data-piccola-group-tip"

#: Groups that may legitimately not exist, and so must not hold the poll open.
#: *expert* is gated on a beta flag and is absent in most builds.
OPTIONAL = ("cam-expert",)

#: Container id -> the lines of its tooltip.
#:
#: Keyed by the id of the group's *container*, which is what ``init-menu.js``
#: passes to ``newGroup`` and is stable markup in ``index.html``. The header row
#: is that container's own ``.set-header`` child -- ``newGroup`` builds the row
#: and appends it to the div it was handed.
#:
#: One entry per sentence, each written as a sentence (capital, full stop): the
#: tooltip panel draws one span per entry and wraps it at 300px. Breaking a
#: sentence across entries the way ``lang-en.js`` does left no way to tell where
#: one sentence ended and the next began, so the capitals came out wrong.
#:
#: *expert* is included though it is not always shown -- it is gated on a beta
#: flag. A group that is absent is simply skipped, so listing it costs nothing
#: and stops it being the one unexplained header if it does appear.
GROUP_TIPS = {
    "cam-tabs": (
        "Uncut bridges that hold the part to the stock around it, so it cannot "
        "break loose on the pass that cuts it free.",
        "Size them here, then place them by hand.",
    ),
    "cam-stock": (
        "The raw block the part is cut from: width, depth and height, either as "
        "fixed sizes or as a margin left around the model.",
    ),
    "cam-limits": (
        "How deep the job cuts and how fast it moves.",
        "The top and bottom of the cut in Z, the height that moves clear the work "
        "at, and the ceiling on feed and plunge rates.",
    ),
    "cam-output": (
        "How the G-code is ordered and entered: easing down instead of plunging, "
        "cutting deepest or innermost first, and where the tool starts and ends "
        "in Z.",
    ),
    "cam-origin": (
        "The zero every coordinate in the G-code is measured from: the G54 work "
        "offset, which Fusion calls the WCS.",
        "Set XY from the grid and Z from the stock top, middle or bottom, or pick "
        "a point.",
    ),
    "cam-expert": (
        "Arc fitting: collapses runs of short straight moves into G2/G3 arcs, for "
        "smaller files and smoother motion on controllers that want them.",
    ),
}


def group_tips_js() -> str:
    """JS stamping a title on each CAM settings section header."""
    js = """
(function () {
  var TIPS = %(tips)s, DONE = %(done)s, OPTIONAL = %(optional)s;

  // True once every group that must exist has been stamped.
  //
  // Counting what is *done* rather than what is left is the point. An earlier
  // version counted only "box present but header missing", so on the first
  // tick -- injected at document creation, where nothing exists yet -- it found
  // nothing left to do, declared itself finished and never ran again. Nothing
  // was ever stamped. A completion test has to be able to tell "finished" from
  // "has not started", and a count of outstanding work cannot.
  function stamp() {
    var done = 0, want = 0;
    for (var id in TIPS) {
      if (OPTIONAL.indexOf(id) < 0) want++;
      var box = document.getElementById(id);
      if (!box) continue;
      var row = box.querySelector('.set-header');
      if (!row) continue;
      if (!row.getAttribute(DONE)) {
        // never overwrite a title upstream has started setting for itself
        if (!row.getAttribute('title')) {
          row.setAttribute('title', TIPS[id].join('\\n'));
        }
        row.setAttribute(DONE, '1');
      }
      if (OPTIONAL.indexOf(id) < 0) done++;
    }
    return done >= want;
  }

  // The column is built during init, long after this is injected, and a
  // group's container is in the markup before its header row is appended to
  // it -- so this polls for the rows, not for the boxes.
  var tries = 400;
  (function wait() {
    var done = false;
    try { done = stamp(); } catch (e) { }
    if (done) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "tips": json.dumps({k: list(v) for k, v in GROUP_TIPS.items()}),
        "done": json.dumps(DONE_ATTR),
        "optional": json.dumps(list(OPTIONAL)),
    }
    assert "%" not in js, "unescaped % survived formatting in group_tips_js()"
    return js
