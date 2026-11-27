"""
ui/cam_anim_end.py
Pairs the two ends of Kiri:Moto's animation bar: a **jump to the end** button,
and the existing restart moved next to it and renamed.

Why
---
Seeing the finished cut is a normal thing to want — it is the answer to "will
this leave what I meant to leave" — and until now the only ways there were to
watch the whole job at whatever speed it runs at, or to drag the timeline
slider to its right-hand end and hope to land on the last pixel. There was a
*restart* button (`fa-fast-backward`) at the head of the bar with nothing
answering it at the other end.

So one goes in after the speed toggle, mirroring restart: same shape, opposite
direction.

And the restart moves to join it. Upstream puts it at the far left, ahead of
play, which is a sensible place for "start over" when it is the only such
control; with a jump-to-the-end at the other end of the transport, the two
belong together — one gesture is "show me the beginning", the other "show me
the end", and hunting to opposite ends of the bar for them is the kind of
small friction nobody reports. Its tooltip becomes **reset preview**, which is
what it does: `replay()` clears and rebuilds the animation at record 0,
paused. Nothing about its behaviour changes, and it is *moved*, not rebuilt —
Kiri re-binds `button.replay.onclick` on every animation setup, and that finds
the same element wherever it now sits.

How
---
Through the timeline, not around it. The button sets `anim.timeline` to its
maximum and fires the element's own `onchange`, which is the same path a drag
takes, so everything the seek already knows how to do applies unchanged:

* forward from wherever the animation is, or a rewind-and-re-cut if it is
  somehow past the target — `seek()` picks (patch 0008);
* the fast pass rather than a real-time replay, because seeking is what this
  is: the cut is destructive, so the stock at the last record exists only by
  having carved every record before it. There is no jumping to the end, only
  arriving at it quickly;
* and it stops there rather than playing on, because patch 0008's
  `seekThenPlay` returns without a follow-up once the cursor is at the end,
  with patch 0007 having already put the transport back to *play*.

That leaves this module owning nothing but a button. Kiri rebuilds the
animator's handlers on every setup (`button.play.onclick = play` and friends)
but only for controls it created, so ours keeps working across replays without
being re-bound.

Nothing here needs a patch: the insertion point is reachable as
``api.ui.anim.speed`` and the action is expressible entirely through the
timeline element's public interface.
"""

from __future__ import annotations

import json

#: Id given to the button, and the "already added" check so a re-run cannot
#: stack two of them.
BUTTON_ID = "piccola-anim-end"

#: FontAwesome glyph. The mirror of the restart button's `fa-fast-backward`,
#: which is the point: one goes to the start, this goes to the end.
ICON = '<i class="fas fa-fast-forward"></i>'

#: Put on the timeline input so css() can give it a row of its own.
TIMELINE_ROW_CLASS = "piccola-timeline-row"

#: Put on the animation bar's row of controls, so it can wrap.
ANIM_ROW_CLASS = "piccola-anim-row"

#: The block under the transport holding the timeline and the render-ahead bar,
#: each with its label to the left (see css()).
TIMELINE_BLOCK_CLASS = "piccola-tl"

#: The render-ahead bar and its label: shown only while an animator reports
#: one (patch 0070 sets ``data-rendered`` on the timeline's parent).
RENDERED_CLASS = "piccola-tl-rendered"

#: Labels, in the bar's lower-case voice, and what they mean on hover.
PROGRESS_LABEL = "progress"
PROGRESS_TIP = "where the animation is -- click or drag to jump there"
RENDERED_LABEL = "rendered"
RENDERED_TIP = ("how much of the job is simulated ahead\n"
                "a jump inside the filled part lands at once\n"
                "gone once the whole job is ready")

#: The timeline's size against Kiri's native range control: its track and
#: thumb, scaled in both directions (CSS ``zoom``) so the thumb stays round.
#: The width is set as a percentage, which zoom leaves alone, so the control
#: still spans its row. ui/cam_anim_markers.py reads the zoom back from the
#: element, so its ticks follow.
TIMELINE_ZOOM = 1.2

#: Half the native thumb's width, in px at zoom 1 (as in cam_anim_markers):
#: the render-ahead bar is inset by it, scaled, so a fraction of the bar sits
#: under the thumb at that fraction of the timeline.
THUMB_HALF_PX = 7

#: Hover text, in the same voice as its neighbours ("restart", "play",
#: "single step", "toggle speed").
TITLE = "jump to the end"

#: The `api.ui.anim` key this is inserted after — the speed toggle, which is
#: the last of the transport buttons before the readouts begin.
AFTER_KEY = "speed"

#: The `api.ui.anim` key of the restart button, moved to the head of the
#: transport — immediately before *slow down*, so the run reads left to right:
#: back to the start, slower, play, and onward.
MOVED_KEY = "replay"

#: The `api.ui.anim` key it is placed before. Falls back to jump-to-end if that
#: button is not there, which is what happens on a build without patch 0019.
MOVED_BEFORE_KEY = "slow"

#: What that button becomes. Upstream calls it "restart", which reads as "run
#: it again"; it does not run anything — it puts the preview back to the start
#: and leaves it there.
MOVED_TITLE = "reset preview"


def css() -> str:
    """Let the animation bar wrap instead of running off both edges.

    The bar is one long row — transport, X/Y/Z/A readouts, the percentage, the
    timeline, then four view toggles (*transparency*, *show model*, *stock
    box*, *path*) — laid out `nowrap` inside a container that centres it
    (`#layer-animate { left: 0; right: 0 }`, `class="f-col a-center"`). Centring
    an overflowing row overflows it *symmetrically*, so as the window narrows
    the row does not clip on one side and stay usable: it walks off both edges
    at once, taking the restart button off the left and all four toggles off the
    right. They are simply gone, with nothing to scroll and no hint that there
    was ever anything there.

    Wrapping is the whole fix. The row keeps its order and its centring, and on
    a narrow window it becomes two lines instead of one that does not fit.
    Nothing needs to know the new height: the view bar above it measures what is
    actually on screen (`ui/cam_views.py`, `reposition`), so a bar that has just
    become two rows tall pushes it up by exactly that much.
    """
    return "\n".join([
        # The row holding the transport, readouts and timeline has no class of
        # its own, so jump_to_end_js() tags it; a `.f-row` rule here never
        # matched anything.
        f"#{'layer-animate'} .{ANIM_ROW_CLASS} {{ flex-wrap: wrap;"
        f" justify-content: center; align-items: center; }}",
        # The timeline gets a row of its own, under the transport and readouts,
        # as wide as the bar. Squeezed in between the percentage and the view
        # toggles it was left whatever width they did not use -- a few
        # centimetres on a laptop screen, too short to aim at a point in a
        # long job. `order` sends it after every button, and a 100% basis
        # makes it wrap onto its own line. The range input sits directly in
        # that row (it has no wrapper of its own there), and Kiri's newRange()
        # never applies the `anim-timeline` class it is given, so
        # jump_to_end_js() puts this class on the input itself; the old
        # `.anim-timeline` rule here never matched anything. Width and
        # max-width are reset because Kiri caps every range at 85px and
        # `.var-row input` at 7ch.
        # The timeline and the render-ahead bar sit in a block of their own
        # below the transport, a two-column grid: a label, then the control,
        # so both bars start at the same x and the labels line up. The block
        # is put in by jump_to_end_js(), which moves the timeline into it.
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS} {{ order: 100;"
        f" flex: 1 0 100%; display: grid; grid-template-columns: auto 1fr;"
        f" column-gap: 6px; row-gap: 1px; align-items: center;"
        f" margin-top: 4px; box-sizing: border-box; min-width: 0; }}",
        # capitalised the way Kiri's own labels are (text-transform, see
        # ui/cam_labels.py), so the words are written lowercase here too
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS} > label {{ margin: 0;"
        f" padding: 1px 6px; font-size: 12px; justify-content: flex-start;"
        f" cursor: default; text-transform: capitalize; }}",
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS} > label.{RENDERED_CLASS} {{"
        f" font-size: 10px; font-weight: normal; padding: 0 6px; line-height: 12px; }}",
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS} input.{TIMELINE_ROW_CLASS} {{"
        f" zoom: {TIMELINE_ZOOM}; width: 100%; max-width: none; min-width: 0;"
        f" box-sizing: border-box; margin: 0 !important; }}",
        # How much of the job the animator has rendered ahead (patch 0070):
        # filled to the fraction the animator writes into --piccola-rendered on
        # the timeline's parent -- a video's buffered bar. A seek inside it
        # lands at once. Inset by half a (scaled) thumb at each end, the range
        # the thumb's centre travels, so the two agree on where a point is.
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS} div.{RENDERED_CLASS} {{"
        f" height: 4px; margin: 0 {THUMB_HALF_PX * TIMELINE_ZOOM:.1f}px;"
        f" border-radius: 2px; background: linear-gradient(to right,"
        f" rgba(70, 130, 180, 0.75) var(--piccola-rendered, 0%),"
        f" rgba(0, 0, 0, 0.10) var(--piccola-rendered, 0%)); }}",
        # Shown only while the render is under way: once every point of the
        # job is ready there is nothing left to say, and a full bar under the
        # timeline was one more thing to read. Nothing on a flat job either.
        f"#{'layer-animate'} .{TIMELINE_BLOCK_CLASS}:not([data-rendered=\"partial\"])"
        f" .{RENDERED_CLASS} {{ display: none; }}",
        # a wrapped row must not run under the panel edges
        f"#{'layer-animate'} {{ max-width: 100%; box-sizing: border-box; }}",
    ])


def jump_to_end_js() -> str:
    """JS adding the jump-to-end button to the animation bar."""
    return """
(function () {
  var ID = %(id)s, ICON = %(icon)s, TITLE = %(title)s, AFTER = %(after)s;
  var BEFORE = %(before)s, ROW = %(row)s, BAR = %(bar)s;
  var BLOCK = %(block)s, RENDERED = %(rendered)s;
  var PROGRESS_LABEL = %(plabel)s, PROGRESS_TIP = %(ptip)s;
  var RENDERED_LABEL = %(rlabel)s, RENDERED_TIP = %(rtip)s;
  var MOVED = %(moved)s, MOVED_TITLE = %(moved_title)s;

  function anim() {
    try { return window.kiri.api.ui.anim; } catch (e) { return null; }
  }

  function jump() {
    var a = anim();
    var t = a && a.timeline;
    // No animation set up yet: the slider has no handler, and seek() would
    // refuse anyway for want of a job length.
    if (!t || typeof t.onchange !== 'function') return;
    t.value = t.max || 1000;
    t.onchange();
  }

  function label(text, tip, cls) {
    var l = document.createElement('label');
    l.textContent = text;
    l.setAttribute('title', tip);
    if (cls) l.className = cls;
    return l;
  }

  // Kiri builds the timeline straight into the transport row. It moves into a
  // block of its own -- the same element, so Kiri's handlers and every
  // reference to api.ui.anim.timeline still find it; the render-ahead
  // animator (patch 0070) writes its fraction onto the timeline's parent,
  // which is now this block, and the bar inside it reads that.
  function timelineBlock(tl) {
    var row = tl.parentNode;
    if (row.classList.contains(BLOCK)) return;
    tl.classList.add(ROW);
    row.classList.add(BAR);
    var block = document.createElement('div');
    block.className = BLOCK;
    row.insertBefore(block, tl);
    var pl = label(PROGRESS_LABEL, PROGRESS_TIP);
    // the label is not a <label for>: a click on it must not move the slider
    block.appendChild(pl);
    block.appendChild(tl);
    block.appendChild(label(RENDERED_LABEL, RENDERED_TIP, RENDERED));
    var bar = document.createElement('div');
    bar.className = RENDERED;
    bar.setAttribute('title', RENDERED_TIP);
    block.appendChild(bar);
  }

  function add() {
    var a = anim();
    // built with the rest of the CAM menu, well after the kiri namespace
    // appears, so absence here means "not yet", not "gone"
    if (!a || !a[AFTER] || !a[AFTER].parentNode) return false;
    // the timeline's own block (see css()): moved out of the transport row
    // into a labelled grid under it, with the render-ahead bar below
    if (a.timeline && a.timeline.parentNode && a.timeline.classList) {
      timelineBlock(a.timeline);
    }
    if (document.getElementById(ID)) return true;

    // Same shape newButton() produces: <button title><div><i/></div></button>
    var b = document.createElement('button');
    b.id = ID;
    b.setAttribute('title', TITLE);
    var d = document.createElement('div');
    d.innerHTML = ICON;
    b.appendChild(d);
    b.onclick = jump;

    var after = a[AFTER];
    after.parentNode.insertBefore(b, after.nextSibling);

    // Put restart at the head of the transport and say what it does. Moving the
    // node keeps Kiri's own handler: it re-binds button.replay.onclick on every
    // setup, by reference, not by position.
    var back = a[MOVED];
    if (back && back.parentNode) {
      // Ahead of slow-down, so the row reads left to right: back to the start,
      // slower, play, and onward. Falls back to sitting beside jump-to-end,
      // which is where it went before there was a slow-down button.
      var mark = a[BEFORE] && a[BEFORE].parentNode ? a[BEFORE] : b;
      mark.parentNode.insertBefore(back, mark);
      back.setAttribute('title', MOVED_TITLE);
    }
    return true;
  }

  var tries = 400;
  (function wait() {
    if (add()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "id": json.dumps(BUTTON_ID),
        "icon": json.dumps(ICON),
        "title": json.dumps(TITLE),
        "after": json.dumps(AFTER_KEY),
        "before": json.dumps(MOVED_BEFORE_KEY),
        "row": json.dumps(TIMELINE_ROW_CLASS),
        "bar": json.dumps(ANIM_ROW_CLASS),
        "moved": json.dumps(MOVED_KEY),
        "moved_title": json.dumps(MOVED_TITLE),
        "block": json.dumps(TIMELINE_BLOCK_CLASS),
        "rendered": json.dumps(RENDERED_CLASS),
        "plabel": json.dumps(PROGRESS_LABEL),
        "ptip": json.dumps(PROGRESS_TIP),
        "rlabel": json.dumps(RENDERED_LABEL),
        "rtip": json.dumps(RENDERED_TIP),
    }
