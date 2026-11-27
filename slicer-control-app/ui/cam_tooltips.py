"""
ui/cam_tooltips.py
Replaces the browser's native tooltips in the CAM tools with a readable panel.

The problem
-----------
Kiri:Moto documents every control through the ``title`` attribute::

    if (opt.title) row.setAttribute("title", opt.title);

which hands the text to the *browser* to draw. That has two consequences, and
both of them are worse in this app than they are on the web.

**It is drawn by the platform, not by us.** On macOS that is a pale system
tooltip that sits over the 3D viewport with no contrast against it. Inside the
Windows build of QtWebEngine it comes out as white text on a black box — which
is not a bug in the text, it is Qt's tooltip palette, and no amount of styling
inside the page can reach it. A native tooltip is not part of the document.

**The text is written as lines.** 286 of the long-form strings in
``lang-en.js`` are arrays, one line per element::

    cc_feed_l:  ["max cutting speed in","workspace units / minute"]
    ou_ztop_l:  ["offset from stock bottom","to set start of cutting depth",
                 "in workspace units","* drill/contour ignore this *"]

``language.js`` joins them with newlines before they reach ``setAttribute``, and
a native tooltip honours those. The operation menu in ``index.html`` writes its
breaks as ``&#13;`` instead — a bare carriage return — so both have to be split
on. Anything drawn in HTML has to put them back
deliberately, because a raw newline in a ``div`` collapses to a space.

What this does
--------------
Suppresses the native tooltip and draws the same text as an HTML panel, in the
shape Fusion 360 uses: the control's own name in bold, the explanation beneath
it. Nothing new has to be written — the name is already on screen next to the
control, and the explanation is already in ``title``.

Suppression is by removal, not by CSS: there is no way to style a native
tooltip, so the ``title`` attribute is taken off the element while the pointer
is over it and put straight back on the way out. It comes off on ``mouseover``
and not when the panel is built — the panel's delay is ours to choose, but the
delay before the *native* tooltip appears belongs to the browser, and in
QtWebEngine it is the shorter of the two. Stripping it late let Qt paint first,
and its tooltip then sat on top of the panel repeating it in black. That also means the attribute
survives for anything else that reads it, and an element destroyed mid-hover
costs nothing.

Putting the lines back
----------------------
Split on newlines, and on nothing else. An earlier version of this split on
commas, on the theory that ``setAttribute`` had stringified the array itself --
it has not, ``language.js:82`` does ``val.join('\\n')`` first. Splitting on
commas left every tooltip as one run-on line and would have chopped prose
commas in half.

Lines wrapped in asterisks -- ``* drill/contour ignore this *`` -- are the
convention these strings use for a caveat, and are drawn as one.

Where it points
---------------
Below the control and left-aligned with it, flipping above when there is no room
below and sliding left rather than off the right edge. It never covers the
control it describes, which is the failure mode of a tooltip pinned to the
cursor.

The heading
-----------
``title`` is set on both the input and the row around it, so the nearest titled
ancestor is walked outward while the text stays the same. That lands on the row,
whose first child is the visible label — "Feed Rate", "Z Anchor". Icon buttons
have no label, and simply get no heading rather than an empty line.
"""

from __future__ import annotations

import json

#: Class on the panel. Also the "already installed" guard.
TIP_CLASS = "piccola-tip"

#: How long the pointer must rest before the panel appears. Long enough not to
#: flash while someone is travelling across a panel of controls, short enough
#: to feel like an answer. Native tooltips wait about a second, which is most of
#: why they feel unhelpful.
DELAY_MS = 350

#: Gap between the control and the panel.
OFFSET_PX = 6

#: Wrapping width. Fusion's is about this, and these strings are short.
MAX_WIDTH_PX = 300

#: Distance kept from the window edges when clamping.
MARGIN_PX = 6

#: Where a diagram comes from, when the hovered element names one. Both are
#: owned by ``ui/cam_op_art.py``; they are duplicated here as strings rather
#: than imported so that neither module has to load before the other.
ART_REGISTRY = "__piccolaOpArt"
ART_ATTR = "data-piccola-art"


def css() -> str:
    """Styling for the tooltip panel.

    Deliberately close to the app's own surfaces rather than to Kiri's panels:
    a tooltip is a different plane to the UI it explains, and wants to read as
    floating above it.
    """
    return "\n".join([
        f".{TIP_CLASS} {{",
        # !important throughout: Kiri's own rules reach any element parented to
        # body, and an earlier build of this came out as four flex columns
        # instead of stacked lines.
        "  position: fixed !important;",
        "  display: block !important;",
        "  z-index: 2147483600 !important;",
        "  box-sizing: border-box !important;",
        f"  max-width: {MAX_WIDTH_PX}px !important;",
        "  width: auto !important;",
        "  height: auto !important;",
        "  padding: 9px 11px !important;",
        "  margin: 0 !important;",
        "  background: #f7f8fa !important;",
        "  border: 1px solid #b9c0c8 !important;",
        "  border-radius: 4px !important;",
        "  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.18) !important;",
        "  font: 12px/1.45 system-ui, -apple-system, 'Segoe UI', sans-serif"
        " !important;",
        "  color: #333 !important;",
        "  text-align: left !important;",
        "  text-transform: none !important;",
        "  letter-spacing: normal !important;",
        "  pointer-events: none !important;",
        "  white-space: normal !important;",
        "}",
        # the control's own name, the way Fusion heads its tooltips
        f".{TIP_CLASS} .{TIP_CLASS}-head {{",
        "  display: block !important;",
        "  width: auto !important;",
        "  margin: 0 0 4px 0 !important;",
        "  padding: 0 !important;",
        "  font-size: 13px !important;",
        "  font-weight: 600 !important;",
        "  color: #14181c !important;",
        "  text-transform: capitalize !important;",
        "  white-space: normal !important;",
        "}",
        f".{TIP_CLASS} .{TIP_CLASS}-line {{",
        "  display: block !important;",
        "  width: auto !important;",
        "  margin: 0 !important;",
        "  padding: 0 !important;",
        "  color: #3c444c !important;",
        "  white-space: normal !important;",
        "}",
        # the "* drill/contour ignore this *" style caveats these strings end on
        # the diagram, when the hovered thing has one
        f".{TIP_CLASS} .{TIP_CLASS}-art {{",
        "  display: block !important;",
        "  width: 100% !important;",
        "  height: auto !important;",
        "  margin: 7px 0 1px 0 !important;",
        "  border: 1px solid #dfe3e8 !important;",
        "  border-radius: 3px !important;",
        "  background: #ffffff !important;",
        "}",
        f".{TIP_CLASS} .{TIP_CLASS}-note {{",
        "  display: block !important;",
        "  width: auto !important;",
        "  margin: 4px 0 0 0 !important;",
        "  padding: 0 !important;",
        "  color: #7a6000 !important;",
        "  white-space: normal !important;",
        "}",
    ])


def tooltips_js() -> str:
    """JS suppressing native tooltips and drawing the panel instead."""
    js = """
(function () {
  var CLS = %(cls)s, DELAY = %(delay)s, OFFSET = %(offset)s, MARGIN = %(margin)s;
  var ART_REG = %(art_reg)s, ART_ATTR = %(art_attr)s;
  var STASH = 'data-piccola-title';

  var tip = null, timer = null, host = null;

  function clearTimer() {
    if (timer) { clearTimeout(timer); timer = null; }
  }

  // Put the attribute back on whatever we took it from. Called on the way out
  // of an element and before taking a new one, so a stray pointer cannot leave
  // a control permanently undocumented.
  function restore() {
    if (host && host.getAttribute && host.hasAttribute(STASH)) {
      host.setAttribute('title', host.getAttribute(STASH));
      host.removeAttribute(STASH);
    }
    host = null;
  }

  function hide() {
    clearTimer();
    if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
    tip = null;
    restore();
  }

  // title is set on the input AND the row around it. Walk out while the text
  // is unchanged, so the heading can be read off the row rather than the input.
  function titledHost(from) {
    if (!from || !from.closest) return null;
    var el = from.closest('[title]');
    if (!el) return null;
    var text = el.getAttribute('title');
    if (!text || !text.trim()) return null;
    var up = el.parentElement;
    while (up && up.getAttribute && up.getAttribute('title') === text) {
      el = up;
      up = up.parentElement;
    }
    return el;
  }

  function firstText(el) {
    return ((el && el.innerText) || '').trim().split('\\n')[0].trim();
  }

  // The visible label beside the control. Icon buttons have none, and get no
  // heading rather than a blank one.
  function headingFor(el, body) {
    var head = '';
    if (el.tagName === 'BUTTON') {
      head = firstText(el);
    } else {
      for (var i = 0; i < el.children.length; i++) {
        head = firstText(el.children[i]);
        if (head) break;
      }
      if (!head) head = firstText(el);
    }
    if (!head) return '';
    // a heading that merely repeats the first line of the body is noise
    if (body.length && head.toLowerCase() === body[0].toLowerCase()) return '';
    return head;
  }

  // language.js already joined the author's array with newlines, so the line
  // structure is sitting in the attribute -- it just has nowhere to show in a
  // div, where a raw newline collapses to a space.
  function lines(text) {
    // \\r as well as \\n: the language pack joins with \\n, but the operation
    // menu in index.html writes its line breaks as &#13;, which is a lone \\r.
    return String(text).split(/\\r\\n|\\r|\\n/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length; });
  }

  // Sentence case, whoever wrote the text. Kiri's language pack writes its
  // help in lower case, one phrase per line ("max cutting speed in" /
  // "workspace units / minute"), and so did some of ours, which read as a
  // mix of capitals and small letters next to the panels' proper names. A
  // capital now starts the tooltip, every sentence after a full stop (not
  // after "e.g." and the like), every caveat line, and every line after one
  // that ended a sentence. A line that carries on the one before -- no full
  // stop at its end -- is a wrapped phrase and is left as it is.
  var ABBR = /\\b(e\\.g|i\\.e|vs|etc|approx|incl|min|max|no)\\.$/i;
  function cap(s) {
    return s.replace(/^([^A-Za-z0-9]*)([a-z])/, function (m, pre, c) {
      return pre + c.toUpperCase();
    });
  }
  function sentences(s) {
    return s.replace(/([.!?])(\\s+)([a-z])/g, function (m, p, sp, c, off, all) {
      if (ABBR.test(all.slice(0, off + 1))) return m;
      return p + sp + c.toUpperCase();
    });
  }
  function sentenceCase(body) {
    var ended = true;
    return body.map(function (line) {
      var note = line.charAt(0) === '*' && line.slice(-1) === '*';
      var out = sentences(line);
      if (ended || note) {
        out = note ? out.replace(/^(\\*+\\s*)([a-z])/, function (m, pre, c) {
          return pre + c.toUpperCase();
        }) : cap(out);
      }
      ended = note || /[.!?:]\\s*$/.test(out);
      return out;
    });
  }

  function build(el) {
    // the text was moved to the stash the moment the pointer arrived; see
    // onOver for why it cannot wait until now
    var text = el.getAttribute(STASH) || el.getAttribute('title');
    if (!text || !text.trim()) return;
    var body = lines(text);
    if (!body.length) return;
    var head = headingFor(el, body);
    body = sentenceCase(body);

    tip = document.createElement('div');
    tip.className = CLS;
    if (head) {
      var h = document.createElement('span');
      h.className = CLS + '-head';
      h.textContent = head;
      tip.appendChild(h);
    }
    for (var i = 0; i < body.length; i++) {
      var line = document.createElement('span');
      // these strings mark their caveats by wrapping them in asterisks
      var note = body[i].charAt(0) === '*' && body[i].slice(-1) === '*';
      line.className = CLS + (note ? '-note' : '-line');
      line.textContent = note ? body[i].replace(/^\\*+|\\*+$/g, '').trim() : body[i];
      tip.appendChild(line);
    }
    addArt(tip, el);
    document.body.appendChild(tip);
    place(el);
  }

  // A diagram, when the hovered element names one and the registry has it.
  // Absent art is the normal case -- most controls are a label and a sentence.
  function addArt(tip, el) {
    var key = el.getAttribute && el.getAttribute(ART_ATTR);
    if (!key) return;
    var reg = window[ART_REG];
    if (!reg || !reg.art || !reg.art[key]) return;
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', (reg.boxes && reg.boxes[key]) || reg.viewBox);
    svg.setAttribute('class', CLS + '-art');
    svg.innerHTML = reg.art[key];
    tip.appendChild(svg);
  }

  // Below the control and aligned to its left edge, so it never covers the
  // thing it is describing. Flips above, and slides left, rather than leaving
  // the window.
  function place(el) {
    var r = el.getBoundingClientRect();
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var left = r.left;
    if (left + w > vw - MARGIN) left = vw - MARGIN - w;
    if (left < MARGIN) left = MARGIN;
    var top = r.bottom + OFFSET;
    if (top + h > vh - MARGIN) {
      var above = r.top - OFFSET - h;
      top = above >= MARGIN ? above : Math.max(MARGIN, vh - MARGIN - h);
    }
    tip.style.left = Math.round(left) + 'px';
    tip.style.top = Math.round(top) + 'px';
  }

  function onOver(ev) {
    var el = titledHost(ev.target);
    if (!el) return;
    if (el === host) return;
    hide();
    // Strip the attribute NOW, not when the panel is built. The delay before
    // the panel appears is ours; the delay before the *native* tooltip appears
    // belongs to the browser, and in QtWebEngine it is the shorter of the two.
    // Removing it late let Qt paint its own tooltip first -- which then sat on
    // top of the panel, saying the same thing again in black.
    var text = el.getAttribute('title');
    if (text) {
      el.setAttribute(STASH, text);
      el.removeAttribute('title');
    }
    host = el;
    timer = setTimeout(function () {
      timer = null;
      // gone, or the pointer left while we waited
      if (!el.isConnected) return;
      build(el);
    }, DELAY);
  }

  function onOut(ev) {
    // ignore moves between an element and its own children
    var to = ev.relatedTarget;
    if (host && to && host.contains && host.contains(to)) return;
    if (!host) return;
    // leaving the element the title was taken from: give it back, whether or
    // not the panel ever got as far as being drawn
    if (!to || !host.contains(to)) hide();
  }

  function install() {
    if (!document.body) return false;
    if (document.body.getAttribute('data-piccola-tips')) return true;
    document.addEventListener('mouseover', onOver, true);
    document.addEventListener('mouseout', onOut, true);
    // any of these means the pointer is busy with something else
    ['mousedown', 'wheel', 'keydown'].forEach(function (e) {
      document.addEventListener(e, hide, true);
    });
    window.addEventListener('blur', hide, true);
    document.body.setAttribute('data-piccola-tips', '1');
    return true;
  }

  var tries = 400;
  (function wait() {
    try { if (install()) return; } catch (e) { /* not up yet */ }
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "cls": json.dumps(TIP_CLASS),
        "delay": json.dumps(DELAY_MS),
        "offset": json.dumps(OFFSET_PX),
        "margin": json.dumps(MARGIN_PX),
        "art_reg": json.dumps(ART_REGISTRY),
        "art_attr": json.dumps(ART_ATTR),
    }
    assert "%" not in js, "unescaped % survived formatting in tooltips_js()"
    return js
