"""
ui/cam_ranges.py
Shows the accepted range beside every numeric field that has one.

The problem
-----------
*Step Over* is a **fraction of the cutter diameter**, bounded 0.01 to 1. Nothing
on screen says so. Typed as 1 it means "step across a whole tool width per pass"
-- zero overlap between neighbouring passes -- and a pocket comes out with thin
ribs of uncut material standing in it, full depth. That was measured on a test
part at 3 mm tool, step over 1: ribs 16.99 mm proud. At 0.4 the same part came
out clean.

The number was not wrong because the operator was careless. It was wrong because
a field that takes 0.01-1 and a field that takes 0-9999 look exactly alike, and
the only way to find out which is which was to read ``cl-ops.js``.

What this does
--------------
Puts the bound next to the label: *Step Over (0.01 ~ 1)*.

How the numbers are found
-------------------------
Not from a table copied out of the source, which would rot the first time
upstream retuned a limit. ``inputs.js`` attaches the clamp itself to the element::

    if (opt.bound) ip.bound = opt.bound;

and the clamp is a closure over its own limits::

    function bound(low, high) {
        return function(v) {
            if (isNaN(v)) return low;
            return v < low ? low : v > high ? high : v;
        };
    }

so it answers the question if you ask it properly: ``fn(-Infinity)`` returns the
low, ``fn(Infinity)`` returns the high. Every bounded field in the UI is read
this way, including any that upstream adds later.

When it runs
------------
Not on a timer. The operation popups are built lazily -- ``createPopOp`` has not
run at all until the CAM operation UI first renders, so *Step Over* does not
exist in the document when the page settles, and a poll with any finite budget
is a race against how long the operator takes to click an operation.

A ``MutationObserver`` watches for nodes arriving instead, debounced so a burst
of DOM building costs one pass. An immediate pass covers everything already
present, which is most of the settings column.

What is left out
----------------
A bound of 0 to 9999 is a type check wearing a limit's clothes -- it says
"a number, not enormous", which is not information anyone needs beside a label.
Anything whose upper bound reaches :data:`NO_LIMIT` is skipped, so the hint
appears only where the range genuinely constrains what to type.
"""

from __future__ import annotations

import json

#: Upper bounds at or above this are treated as "no real limit" and not shown.
#: 9999 is the value upstream uses for the stock dimensions, which accept
#: anything that fits a machine.
NO_LIMIT = 9999

#: Fields that get no hint, by their ``api.ui`` key. The three tab sizes
#: (0.005 to 100) and Z clearance (0.01 to 100) are sanity ranges, not ones
#: anyone types near, and the hints were clutter in small groups.
SKIP_UI_KEYS = ("camTabsWidth", "camTabsHeight", "camTabsDepth",
                "camZClearance")

#: Class on the hint, and the attribute marking a label already done.
HINT_CLASS = "piccola-range"
DONE_ATTR = "data-piccola-range"

#: How the range is written. The tilde is the user's own notation.
SEPARATOR = " ~ "

#: How long a burst of DOM changes is allowed to settle before re-scanning.
#: Long enough that building a whole popup is one pass, short enough that the
#: hint is there before the eye lands on the field.
DEBOUNCE_MS = 120


def css() -> str:
    """Styling for the range hint."""
    return "\n".join([
        f".{HINT_CLASS} {{",
        # lighter and smaller than the label it follows: it is a note about the
        # field, not part of the field's name
        "  opacity: 0.55 !important;",
        "  font-size: 90% !important;",
        "  margin-left: 4px !important;",
        "  white-space: nowrap !important;",
        "  text-transform: none !important;",
        "  font-weight: normal !important;",
        "}",
    ])


def ranges_js() -> str:
    """JS appending the accepted range to each bounded field's label."""
    js = """
(function () {
  var CLS = %(cls)s, DONE = %(done)s, SEP = %(sep)s;
  var NO_LIMIT = %(no_limit)s, DEBOUNCE = %(debounce)s, SKIP = %(skip)s;

  // The inputs named in SKIP, looked up through Kiri's api each pass. Null
  // means "cannot tell yet" (api.ui not built): the field is left undecided
  // rather than hinted, and a later pass -- the DOM keeps changing while Kiri
  // builds -- settles it.
  function skipped(ip) {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return null; }
    if (!ui || !ui[SKIP[0]]) return null;
    for (var i = 0; i < SKIP.length; i++) {
      if (ui[SKIP[i]] === ip) return true;
    }
    return false;
  }

  // Ask the clamp for its own limits. `bound(low, high)` returns a function
  // that answers `low` for anything below it and `high` for anything above, so
  // the extremes read straight back out. Anything that is not that shape --
  // a converter, a hand-written clamp -- fails the checks below and is left
  // alone rather than guessed at.
  function limits(fn) {
    if (typeof fn !== 'function') return null;
    var lo, hi;
    try { lo = fn(-Infinity); hi = fn(Infinity); } catch (e) { return null; }
    if (typeof lo !== 'number' || typeof hi !== 'number') return null;
    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return null;
    return { lo: lo, hi: hi };
  }

  // 0.01 -> "0.01", 1 -> "1", 85 -> "85". Trailing zeros are noise in a hint.
  function num(v) {
    return (Math.round(v * 1000) / 1000).toString();
  }

  function labelOf(input) {
    var row = input.parentElement;
    if (!row) return null;
    // the row's own label, not one belonging to a nested control
    for (var i = 0; i < row.children.length; i++) {
      var el = row.children[i];
      if (el.tagName === 'LABEL') return el;
    }
    return null;
  }

  function annotate() {
    var inputs = document.querySelectorAll('input, textarea');
    for (var i = 0; i < inputs.length; i++) {
      var ip = inputs[i];
      if (!ip.bound) continue;
      var lab = labelOf(ip);
      if (!lab || lab.getAttribute(DONE)) continue;
      var sk = skipped(ip);
      if (sk === null) continue;
      if (sk) { lab.setAttribute(DONE, 'skip'); continue; }
      var lim = limits(ip.bound);
      if (!lim) { lab.setAttribute(DONE, 'skip'); continue; }
      // a ceiling this high is a sanity check, not a range worth stating
      if (lim.hi >= NO_LIMIT) { lab.setAttribute(DONE, 'skip'); continue; }
      var hint = document.createElement('span');
      hint.className = CLS;
      hint.textContent = '(' + num(lim.lo) + SEP + num(lim.hi) + ')';
      lab.appendChild(hint);
      lab.setAttribute(DONE, '1');
    }
  }

  function safeAnnotate() {
    try { annotate(); } catch (e) { }
  }

  // React to the document being built rather than guessing when it is done:
  // the operation popups do not exist until an operation is first opened.
  var pending = null;
  function schedule() {
    if (pending) return;
    pending = setTimeout(function () { pending = null; safeAnnotate(); }, DEBOUNCE);
  }

  function start() {
    if (!document.body) return false;
    safeAnnotate();
    try {
      new MutationObserver(schedule)
        .observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {
      return false;
    }
    return true;
  }

  if (!start()) {
    // injected before <body> exists; try again once the document is parsed
    document.addEventListener('DOMContentLoaded', start);
  }
})();
""" % {
        "cls": json.dumps(HINT_CLASS),
        "done": json.dumps(DONE_ATTR),
        "sep": json.dumps(SEPARATOR),
        "no_limit": json.dumps(NO_LIMIT),
        "debounce": json.dumps(DEBOUNCE_MS),
        "skip": json.dumps(list(SKIP_UI_KEYS)),
    }
    assert "%" not in js, "unescaped % survived formatting in ranges_js()"
    return js
