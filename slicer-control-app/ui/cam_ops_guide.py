"""
ui/cam_ops_guide.py
An *Operations guide* in Kiri:Moto (Guide menu, next to Materials): which
operation does what, the settings that matter, and the mistakes that cost a
part -- written for someone new to the machine.

Why
---
The Materials guide answers "how fast". The questions after that are "which
operation" and "what do these fields mean", and on the 4th axis the answers
are not the ones a flat-CNC user expects: the feed rate turns the stock in
degrees per minute, Step Over is a fraction of the cutter, a coarse Step Angle
skips or cuts into the teeth of a pulley, and spiral vs linear decides whether
a finish follows the part or hammers across it. Each of those was found the
hard way on the GT2 pulley; this puts them where the next person looks first.

The content is :data:`TOPICS` -- plain data, one guide per entry, picked by
clicking its button along the top; each is cards of paragraphs, lists, small tables, warnings and
pictures -- so it can be corrected without touching the renderer.
``**bold**`` is the only inline markup.

Pictures
--------
``{"svg": name}`` is one of the guide's own illustrations
(ui/cam_ops_guide_art.py: Step Angle, Linear vs Spiral, Ramp, Step Down).
``{"art": name}`` is an operation's diagram from the op menu's hover panel
(ui/cam_op_art.py, published as ``window.__piccolaOpArt``), so the guide and
the menu show the same picture; a missing one is simply left out.

The menu itself is built by ui/cam_materials.py (the Guide menu); this module
only provides the panel and ``window.__piccolaOpsGuide.open()``.
"""

from __future__ import annotations

import base64
import json
import os

from ui.cam_ops_guide_art import GUIDE_ART

DLG_ID = "piccola-ops-guide-dlg"
#: ui/cam_op_art.py's REGISTRY (spelled here so neither module imports the other).
OP_ART_REG = "__piccolaOpArt"

#: Photos the guide can show, by name -> file in images/. Embedded as data
#: URIs when the panel is built: the Kiri page cannot read the app's files.
PHOTOS: dict = {
}
_IMG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "images"))

#: One guide per entry, one button each along the top of the panel. A card's body is a list of
#: blocks: {"p": text}, {"ul": [items]}, {"table": [header row, rows...]},
#: {"warn": text}, {"svg": illustration}, {"art": operation diagram} or
#: {"img": photo name, "caption": text}.
TOPICS = [
    {"tab": "Start here", "cards": [
        {"title": "The order of every job", "body": [
            {"ul": [
                "**Rough** first: a flat end mill takes most of the material off "
                "quickly and leaves a thin skin (Leave Stock 0.1-0.3 mm).",
                "**Finish** second: usually a ball end mill cuts that skin to "
                "the final shape, with Leave Stock 0.",
                "**Generate**, then look at the toolpath: every repeated feature "
                "(teeth, grooves, holes) should get the same pattern of lines.",
                "**Animate** carves the stock on screen. This, not the toolpath "
                "view, shows what the part will look like.",
                "**Export**, and read the file's header: the tools, the lowest Z "
                "(ZMIN) and the time.",
            ]},
        ]},
        {"title": "Before you press Start", "body": [
            {"ul": [
                "Set the work zero where the CAM origin says (for rotary jobs: "
                "the origin picker's z zero row -- top of the bar or rotary axis).",
                "Run new jobs with the feed override at about 70% and raise it "
                "once the cut sounds clean.",
                "Feeds and speeds for each material: Guide > Materials.",
            ]},
            {"warn": "Squealing, chatter, melting or a tool that bends means: slow "
                     "the feed or cut shallower. Small cutters (1-2 mm) break first."},
        ]},
    ]},

    {"tab": "Rotary: operations", "cards": [
        {"title": "Rotary Rough", "body": [
            {"art": "rotary rough"},
            {"p": "Removes the bulk in layers of constant radius, from the outside "
                  "of the bar inwards, one Step Down apart, only where there is "
                  "stock. Use a flat end mill."},
            {"ul": [
                "**Step Down**: how much deeper each layer goes. The last layer "
                "always lands on the floor of the part.",
                "**Flat Faces**: adds a layer at each cylindrical face so it is "
                "cut flat, not in steps. Leave it on.",
                "**Ramp** (on by default) and **Ramp Angle** (5 deg): the tool goes "
                "into each layer on a slope while the bar turns, never straight "
                "down. Use 2-3 deg for the smallest cutters.",
                "**Leave Stock**: 0.1-0.3 mm for the finish to remove.",
            ]},
            {"svg": "ramp"},
        ]},
        {"title": "Surface Rough", "body": [
            {"art": "surface rough"},
            {"p": "Like Rotary Rough, but each layer follows the shape of the part, "
                  "so the last one leaves an even skin everywhere. Good before a "
                  "finish on curved parts."},
        ]},
        {"title": "Rotary Finish", "body": [
            {"art": "lathe"},
            {"p": "Cuts the final surface with a ball end mill, following the part. "
                  "Two styles:"},
            {"ul": [
                "**Linear**: straight lines along the bar (X), the bar turning one "
                "Step Angle between lines. Best for anything that runs ALONG the "
                "bar: pulley teeth, splines, flutes, flats.",
                "**Spiral**: one continuous helix around the bar. Best for round "
                "shapes: cylinders, shoulders, beads -- a smooth surface with no "
                "step marks.",
            ]},
            {"svg": "linear-spiral"},
            {"warn": "Spiral on pulley teeth crosses every tooth on every turn: the "
                     "tool dives into each gap and jumps back up, and with a coarse "
                     "Step Angle gaps are missed or teeth cut into. Use Linear for "
                     "teeth."},
            {"p": "**Climb only** (with Linear, on by default) cuts every line "
                  "climbing and goes back clear of the part between lines. Climb "
                  "milling gives the cleaner finish, above all in aluminium, where "
                  "cutting the other way (conventional) rubs, smears and welds "
                  "chips to the cutter. Spiral already climbs on this machine."},
        ]},
        {"title": "Rotary Contour", "body": [
            {"art": "rotary contour"},
            {"p": "After roughing: finishes steep walls (ribs, shoulders, flights) "
                  "with one ring per layer that runs along the walls."},
        ]},
        {"title": "Cut Off", "body": [
            {"art": "cut off"},
            {"p": "Parts the finished piece from the bar: a groove just outside "
                  "each end, a step deeper each turn, down to a core (16 mm by "
                  "default) that you saw through by hand, so nothing falls while "
                  "the machine runs. The cutter's flutes must reach from the bar "
                  "surface down to the core."},
        ]},
    ]},

    {"tab": "Rotary: settings", "cards": [
        {"title": "Feed rate on the 4th axis", "body": [
            {"p": "When the bar turns, the machine reads the feed rate as "
                  "**degrees per minute**, not mm/min. 1200 is 3.3 turns a minute. "
                  "The speed at the cutter depends on the bar:"},
            {"p": "**deg/min = 115 x (mm/min you want) / bar diameter**. "
                  "Example: 400 mm/min on a 25 mm bar = 115 x 400 / 25 = 1840."},
            {"p": "Straight moves along X (Linear finish lines) are ordinary mm/min."},
        ]},
        {"title": "Step Angle", "body": [
            {"p": "Degrees the bar turns between two points of the toolpath. The "
                  "machine goes in a straight line from one point to the next, so "
                  "with a coarse angle it misses details -- or cuts through them."},
            {"svg": "step-angle"},
            {"table": [
                ["Step Angle", "Spacing on a 25 mm bar", "On GT2 teeth (one every 9 deg)"],
                ["0.5 deg", "0.11 mm", "about 10 points per gap: exact"],
                ["1 deg", "0.22 mm", "about 5 points per gap: good"],
                ["6 deg", "1.31 mm", "gaps missed or teeth cut into"],
                ["18 deg", "3.93 mm", "unusable on teeth"],
            ]},
            {"p": "Pick an angle that divides the feature spacing evenly (for 9 "
                  "deg teeth: 0.5, 1, 1.5), so every tooth gets the same points. "
                  "A smaller angle does not make the job slower -- the bar turns "
                  "the same distance -- but the file gets bigger. Plain round "
                  "surfaces are fine at 2-3 deg."},
        ]},
        {"title": "Step Down in Rotary Finish", "body": [
            {"p": "The depth from the top of the part to its deepest point is "
                  "split into **equal passes**, none deeper than Step Down; the "
                  "last pass is the finished surface. **Expand** (0 or more) "
                  "starts the first pass that far above the part."},
            {"svg": "step-down"},
        ]},
        {"title": "Step Over", "body": [
            {"p": "A **fraction of the cutter's diameter**, not millimetres: 0.2 "
                  "with a 1.5 mm ball is 0.3 mm. In a Spiral finish that is how far "
                  "X moves per turn (so X creeps very slowly -- that is normal); in "
                  "Linear, the spacing of points along each line."},
        ]},
        {"title": "A axis zero and the bed", "body": [
            {"ul": [
                "The app sets A to 0 where the chuck stands each time it starts. If "
                "you restart between two operations on the same part, turn the "
                "chuck back to your mark and press A zero before the next one.",
                "**Bed forward** brings the bed out for a tool change; **Bed back** "
                "always returns it to where Rotary mode parked it.",
            ]},
        ]},
    ]},

    {"tab": "Flat (3-axis): operations", "cards": [
        {"title": "Level", "body": [
            {"art": "level"},
        ]},
        {"title": "Rough", "body": [
            {"art": "rough"},
        ]},
        {"title": "Pocket", "body": [
            {"art": "pocket"},
        ]},
        {"title": "Area", "body": [
            {"art": "area"},
        ]},
        {"title": "Outline", "body": [
            {"art": "outline"},
        ]},
        {"title": "Contour", "body": [
            {"art": "contour"},
        ]},
        {"title": "Scallop", "body": [
            {"art": "scallop"},
        ]},
        {"title": "Trace", "body": [
            {"art": "trace"},
        ]},
        {"title": "Drill", "body": [
            {"art": "drill"},
        ]},
        {"title": "Ramp", "body": [
            {"p": "Rough, Outline, Trace, Pocket and Area each have **Ramp**: the "
                  "tool goes down along its path at the Ramp Angle (10 deg default) "
                  "instead of plunging. Turn it on for small cutters and use 3-5 "
                  "deg."},
        ]},
    ]},

    {"tab": "Example: GT2 pulley", "cards": [
        {"title": "GT2 40T pulley, 6 mm belt, 25 mm bar", "body": [
            {"p": "The settings used for this pulley in plastic. For other "
                  "materials take the feeds from Guide > Materials and keep the "
                  "rest."},
            {"table": [
                ["", "Rotary Rough", "Rotary Finish (teeth)"],
                ["Tool", "1 flute 3mm (flat)", "ball 1.0mm 2F"],
                ["Spindle", "12000", "12000"],
                ["Max Feed Rate", "1000", "400 (Linear: mm/min)"],
                ["Step Angle", "2", "0.5"],
                ["Step Over", "0.4", "0.1"],
                ["Step Down", "2 (ramp on, 5 deg)", "0.3"],
                ["Leave Stock", "0.1", "0"],
                ["Style", "Flat Faces on", "Linear on, Spiral off, Expand 0"],
            ]},
            {"p": "Then Cut Off to part it from the bar."},
            {"warn": "Check in Animate that every tooth gap has the same depth "
                     "and rounded bottom before exporting."},
        ]},
        {"title": "Why such a small cutter", "body": [
            {"p": "A GT2 tooth gap is only about 1.1 mm wide and 0.75 mm deep, "
                  "with a round bottom. Only a **1 mm ball** reaches it. The "
                  "3 mm end mill roughs the bar down to the tips of the teeth "
                  "and no further."},
            {"svg": "gt2-gap"},
        ]},
        {"title": "Careful: a 1 mm cutter breaks easily", "body": [
            {"svg": "small-endmill"},
            {"ul": [
                "**Short stick-out**: clamp the cutter as far up in the collet "
                "as the job allows. Every extra millimetre makes it bend more.",
                "**Set Z zero carefully.** 0.2 mm too low adds 0.2 mm to the "
                "first cut: with Step Down 0.3 that is the ball's whole radius.",
                "**Never plunge**: Ramp on for the rough, and keep the finish "
                "at Step Down 0.3, Step Over 0.1, Linear 400 mm/min.",
                "**A drop of WD-40 at the tip**: the PiccolaCNC drip add-on "
                "keeps aluminium from sticking to the ball and washes chips "
                "out of the gaps. Brush away what is left: chips packed into "
                "a tooth gap are cut again and snap the tip.",
                "**Full spindle speed** (12000). Too slow and each tooth takes "
                "a chip the cutter cannot carry.",
                "**Watch the first pass** with a hand near Hold. A squeal or a "
                "change in sound means stop.",
            ]},
            {"svg": "stick-out"},
            {"warn": "Run Animate and read the exported file's ZMIN before "
                     "cutting: a mistake that only scratches with a 3 mm end "
                     "mill breaks a 1 mm one."},
        ]},
    ]},
]


def _photo_uris() -> dict:
    """PHOTOS as data URIs; a missing file is left out, not an error."""
    out = {}
    for name, fn in PHOTOS.items():
        try:
            with open(os.path.join(_IMG_DIR, fn), "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue
        mime = "image/png" if fn.lower().endswith(".png") else "image/jpeg"
        out[name] = f"data:{mime};base64,{data}"
    return out


def ops_guide_js() -> str:
    """JS providing the Operations guide panel (opened from the Guide menu)."""
    js = r"""
(function () {
  var DLG_ID = %(dlg_id)s, TOPICS = %(topics)s, SVGS = %(svgs)s, OP_ART_REG = %(op_art_reg)s;
  var PHOTOS = %(photos)s;
  var state = { tab: 0 };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function inl(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>'); }

  // an operation's diagram from the op menu's hover panel (ui/cam_op_art.py)
  function opArt(name) {
    var reg = window[OP_ART_REG];
    if (!reg || !reg.art || !reg.art[name]) return '';
    var vb = (reg.boxes && reg.boxes[name]) || reg.viewBox;
    return '<div class="pg-art"><svg xmlns="http://www.w3.org/2000/svg" viewBox="' + vb + '">' +
           reg.art[name] + '</svg></div>';
  }

  function block(b) {
    if (b.p) return '<p class="pg-p">' + inl(b.p) + '</p>';
    if (b.warn) return '<div class="pg-warn">&#9888; ' + inl(b.warn) + '</div>';
    if (b.ul) return '<ul class="pg-ul">' + b.ul.map(function (t) { return '<li>' + inl(t) + '</li>'; }).join('') + '</ul>';
    if (b.table) return '<table class="pg-tbl">' + b.table.map(function (row, i) {
      return '<tr>' + row.map(function (c) { return (i ? '<td>' : '<th>') + inl(c) + (i ? '</td>' : '</th>'); }).join('') + '</tr>';
    }).join('') + '</table>';
    if (b.svg) return SVGS[b.svg] ? '<div class="pg-ill">' + SVGS[b.svg] + '</div>' : '';
    if (b.art) return opArt(b.art);
    if (b.img) return PHOTOS[b.img] ? '<figure class="pg-photo"><img src="' + PHOTOS[b.img] + '" alt="">' +
      (b.caption ? '<figcaption>' + inl(b.caption) + '</figcaption>' : '') + '</figure>' : '';
    return '';
  }

  function close() {
    var d = document.getElementById(DLG_ID);
    if (d) d.remove();
    document.removeEventListener('keydown', onKey, true);
  }
  function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } }

  function go(i) {
    state.tab = (i + TOPICS.length) %% TOPICS.length;
    render();
    var box = document.querySelector('#' + DLG_ID + ' .pg-box');
    if (box) box.scrollTop = 0;
  }

  function render() {
    var box = document.querySelector('#' + DLG_ID + ' .pg-box');
    if (!box) return;
    var t = TOPICS[state.tab];
    var h = '<div class="pg-head"><b>Operations guide</b><span class="pg-x" title="close">&#10005;</span></div>';
    // every guide is a button: all of them in view, one click to open
    h += '<div class="pg-nav">' + TOPICS.map(function (tp, i) {
           return '<button class="pg-tab' + (i === state.tab ? ' pg-on' : '') + '" data-i="' + i + '">' +
                  esc(tp.tab) + '</button>';
         }).join('') + '</div>';
    h += '<div class="pg-body">' + t.cards.map(function (c) {
      return '<div class="pg-card"><div class="pg-title">' + esc(c.title) + '</div>' + c.body.map(block).join('') + '</div>';
    }).join('') + '</div>';
    h += '<div class="pg-btns">' +
         '<button class="pg-prev" title="previous guide">&#9664; ' + esc(TOPICS[(state.tab + TOPICS.length - 1) %% TOPICS.length].tab) + '</button>' +
         '<button class="pg-next" title="next guide">' + esc(TOPICS[(state.tab + 1) %% TOPICS.length].tab) + ' &#9654;</button>' +
         '<span class="pg-gap"></span><button class="pg-close">Close</button></div>';
    box.innerHTML = h;
    box.querySelector('.pg-x').onclick = close;
    box.querySelector('.pg-close').onclick = close;
    Array.prototype.forEach.call(box.querySelectorAll('.pg-tab'), function (b) {
      b.onclick = function () { go(+b.getAttribute('data-i')); };
    });
    box.querySelector('.pg-prev').onclick = function () { go(state.tab - 1); };
    box.querySelector('.pg-next').onclick = function () { go(state.tab + 1); };
  }

  function style() {
    if (document.getElementById(DLG_ID + '-style')) return;
    var s = document.createElement('style');
    s.id = DLG_ID + '-style';
    var R = '#' + DLG_ID + ' ';
    s.textContent = [
      R + '.pg-box, ' + R + '.pg-box div { display: block !important; white-space: normal !important; }',
      R + '.pg-box { background:#fff; color:#222; border-radius:8px; box-shadow:0 10px 40px rgba(0,0,0,.35);' +
        ' padding:14px 18px 12px; width:min(720px,94vw); max-height:88vh; overflow:auto; font-size:13px; line-height:1.45; }',
      R + '.pg-box div.pg-head { display:flex !important; justify-content:space-between; align-items:center; font-size:16px; margin-bottom:8px; }',
      R + '.pg-x { cursor:pointer; padding:2px 6px; color:#666; }',
      R + '.pg-box div.pg-nav { display:flex !important; flex-direction:row !important; flex-wrap:wrap; align-items:center; gap:6px; margin:6px 0 4px;' +
        ' position:sticky; top:-14px; background:#fff; padding:6px 0; z-index:1; }',
      R + '.pg-box div.pg-btns { display:flex !important; flex-direction:row !important; gap:6px; margin:6px 0; }',
      R + '.pg-box button { display:inline-block !important; width:auto !important; flex:0 0 auto; margin:0; }',
      R + '.pg-tab { font-size:13px; padding:6px 12px; border:1px solid #c9c9c9; border-radius:5px; background:#f5f5f5; color:#222; cursor:pointer; }',
      R + '.pg-tab:hover { border-color:#3A78B5; }',
      R + '.pg-tab.pg-on { background:#3A78B5; border-color:#3A78B5; color:#fff; cursor:default; }',
      R + '.pg-gap { flex:1 1 auto; }',
      R + '.pg-prev, ' + R + '.pg-next { border:1px solid #c9c9c9; background:#f5f5f5; border-radius:5px; padding:5px 10px; cursor:pointer; font-size:12px; }',
      R + '.pg-card { border:1px solid #e2e2e2; border-radius:6px; padding:10px 12px; margin:8px 0; background:#fbfbfb; }',
      R + '.pg-title { font-size:15px; font-weight:600; margin-bottom:6px; }',
      R + '.pg-p { margin:4px 0; }',
      R + '.pg-ul { margin:4px 0 4px 18px; padding:0; } ' + R + '.pg-ul li { display:list-item !important; margin:2px 0; }',
      R + '.pg-tbl { display:table !important; border-collapse:collapse; width:100%%; margin:6px 0; font-size:12px; }',
      R + '.pg-tbl tr { display:table-row !important; }',
      R + '.pg-tbl th, ' + R + '.pg-tbl td { display:table-cell !important; padding:3px 8px 3px 0; border-bottom:1px solid #eee; text-align:left; vertical-align:top; }',
      R + '.pg-tbl th { color:#555; font-weight:600; }',
      R + '.pg-warn { margin:8px 0 4px; padding:6px 8px; background:#fff4e0; border:1px solid #e0a030; border-radius:5px; color:#6b4a00; }',
      R + '.pg-art svg { display:block; width:100%%; max-width:320px; height:auto; margin:4px auto 6px; }',
      R + '.pg-photo { display:block; margin:6px 0 8px; }',
      R + '.pg-photo img { display:block; width:100%%; max-width:560px; height:auto; margin:0 auto; border-radius:6px; }',
      R + '.pg-photo figcaption { font-size:12px; color:#555; text-align:center; margin-top:4px; }',
      R + '.pg-ill { background:#fff; border:1px solid #eceff2; border-radius:6px; padding:4px; margin:8px 0; }',
      R + '.pg-box div.pg-btns { justify-content:flex-end; margin-top:10px; }',
      R + '.pg-btns button { padding:5px 14px; font-size:13px; cursor:pointer; }'
    ].join('\n');
    document.head.appendChild(s);
  }

  function open(tab) {
    style();
    close();
    if (typeof tab === 'number' && TOPICS[tab]) state.tab = tab;
    var back = document.createElement('div');
    back.id = DLG_ID;
    back.setAttribute('style', 'position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.35);' +
      'display:flex;align-items:center;justify-content:center;');
    var box = document.createElement('div');
    box.className = 'pg-box';
    back.appendChild(box);
    back.onclick = function (ev) { if (ev.target === back) close(); };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    render();
  }
  window.__piccolaOpsGuide = { open: open, topics: TOPICS };
})();
"""
    return js % {
        "dlg_id": json.dumps(DLG_ID),
        "topics": json.dumps(TOPICS),
        "svgs": json.dumps(GUIDE_ART),
        "op_art_reg": json.dumps(OP_ART_REG),
        "photos": json.dumps(_photo_uris()),
    }
