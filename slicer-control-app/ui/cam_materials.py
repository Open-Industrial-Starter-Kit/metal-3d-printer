"""
ui/cam_materials.py
A *Guide* menu in Kiri:Moto. Its *Materials* entry is a beginner's guide to
what this machine can cut, with starting settings that can be applied to the
job's operations; its *Operations guide* entry opens ui/cam_ops_guide.py.

Why
---
The first question on a new machine is not "which operation" but "what do I
type into feed and speed for this piece of wood / plastic / aluminium". Kiri
offers fields and no guidance, and the wrong answer is not subtle: acrylic
melts back into the cut, aluminium welds to a small cutter, MDF dust is a
health problem. The guide answers per material -- which cutter, how fast, how
deep, what goes wrong -- and **Apply** writes those numbers into the operations
so a beginner does not have to transcribe them.

How the numbers are worked out
------------------------------
Not a fixed table per material, because the right feed depends on the cutter:

    feed   = RPM x flutes x chip load            (capped at the machine's 1500)
    chip load scales with diameter               (cl3 is the value at 3 mm)
    depth per pass = factor x diameter
    step-over      = fraction of diameter (Kiri's own unit for it)

so a 2 mm and a 6 mm cutter get different, both sensible, values. The limits
are this machine's: spindle 1500..12000 RPM ($31/$30) and 1500 mm/min on every
axis ($110..$112) -- :data:`SPINDLE_MAX`, :data:`FEED_MAX`. The chip loads are
the conservative end of small-cutter, router-class figures: a desktop machine
flexes, and a beginner who starts low and listens loses nothing.

Which cutter
------------
Some materials need a particular cutter, not just different numbers: acrylic
wants a single flute so chips leave before they melt; aluminium and plastics
prefer one or two. Kiri's tool record has no flute count, so it is read from
the tool's *name* ("1 flute 3mm", "2 flute 3mm", "4 Flutes"): when an
operation's cutter is the wrong kind for the material, Apply switches it to the
best match in the library -- same shape, an accepted flute count, the closest
diameter not larger than the one it replaces (a smaller cutter still reaches
everything the job was planned for). When the library has none, the cutter is
left as it is and the panel says what to add, rather than guessing.

What Apply touches
------------------
Only the cutting fields each operation type actually has (``cl-ops.js``):
``spindle`` (RPM), ``rate`` (feed), ``plunge``, ``down`` (depth per pass) and
``step`` (step-over, a fraction of the diameter). Contour is a finishing pass,
so it gets a fine step-over and no depth. Facing's ``down`` is a Z *offset*
there, not a depth per pass, and is left alone. Tapered cutters read ``step``
in millimetres rather than as a fraction, so their step-over is not written.
Drilling and helical ops are skipped: their fields mean different things.

Nothing is written without being shown first: the panel lists every operation
with current -> new values, each with a checkbox. Writes go through
``conf.save()``, the same way ui/cam_auto_z.py does, with the same guard
against the save re-entering.
"""

from __future__ import annotations

import json

MENU_ID = "piccola-materials-menu"
DLG_ID = "piccola-materials-dlg"

#: This machine's limits ($30 / $31, $110..$112).
SPINDLE_MAX = 12000
SPINDLE_MIN = 1500
FEED_MAX = 1500
#: A plunge slower than this stalls in nothing and wastes time.
PLUNGE_MIN = 30

#: Operation types Apply writes to, and the fields each gets.
APPLY_FIELDS = {
    "rough":   ("spindle", "rate", "plunge", "down", "step"),
    "pocket":  ("spindle", "rate", "plunge", "down", "step"),
    "outline": ("spindle", "rate", "plunge", "down"),
    "trace":   ("spindle", "rate", "plunge", "down"),
    "area":    ("spindle", "rate", "plunge", "down"),
    "level":   ("spindle", "rate", "plunge", "step"),
    "contour": ("spindle", "rate", "step"),
}
#: Contour is a finishing pass: a fine step-over, whatever the material.
FINISH_STEP = 0.1

#: Material groups, in tab order.
GROUPS = ["Woods & MDF", "Plastics", "Soft metals", "Foam & wax"]

#: One entry per material. ``cl3`` chip load (mm/tooth) at a 3 mm cutter;
#: ``doc`` depth per pass and ``step`` step-over as fractions of the diameter;
#: ``plunge`` as a fraction of the feed; ``flutes`` the accepted flute counts,
#: preferred first; ``level`` 1 easiest .. 3 hardest.
MATERIALS = [
    # ── Woods & MDF ─────────────────────────────────────────────────────────
    {"group": "Woods & MDF", "key": "softwood", "name": "Softwood (pine, spruce)",
     "level": 1, "rpm": 12000, "cl3": 0.035, "doc": 0.5, "step": 0.4, "plunge": 0.3,
     "flutes": [2, 1, 3], "tool": "2-flute flat end mill, up-cut",
     "warn": "Tears out across the grain on the exit side.",
     "tips": ["Cut along the grain where you can.",
              "A down-cut bit leaves a cleaner top edge on veneers.",
              "Clamp well: soft wood moves under a dull cutter."]},
    {"group": "Woods & MDF", "key": "hardwood", "name": "Hardwood (oak, beech, maple)",
     "level": 2, "rpm": 12000, "cl3": 0.028, "doc": 0.33, "step": 0.35, "plunge": 0.3,
     "flutes": [2, 3, 1], "tool": "2-flute flat end mill, carbide",
     "warn": "Burn marks if the feed is too slow for the speed.",
     "tips": ["Brown edges mean rubbing, not cutting: raise the feed a little.",
              "Take more, shallower passes than in softwood."]},
    {"group": "Woods & MDF", "key": "plywood", "name": "Plywood",
     "level": 1, "rpm": 12000, "cl3": 0.03, "doc": 0.4, "step": 0.4, "plunge": 0.3,
     "flutes": [2, 1, 3], "tool": "2-flute flat end mill (compression or down-cut for clean faces)",
     "warn": "The glue layers dull cutters faster than solid wood.",
     "tips": ["Top veneer splinters with an up-cut bit; a down-cut keeps it clean.",
              "Use tabs on cut-outs so the last layer does not break free."]},
    {"group": "Woods & MDF", "key": "mdf", "name": "MDF",
     "level": 1, "rpm": 12000, "cl3": 0.038, "doc": 0.5, "step": 0.4, "plunge": 0.3,
     "flutes": [2, 1, 3], "tool": "2-flute flat end mill, up-cut",
     "warn": "The dust is very fine and harmful: use extraction and a mask.",
     "tips": ["Holds detail well, a good first material.",
              "Edges turn fuzzy when the cutter is dull."]},
    # ── Plastics ────────────────────────────────────────────────────────────
    {"group": "Plastics", "key": "pom", "name": "POM / Delrin",
     "level": 1, "rpm": 12000, "cl3": 0.04, "doc": 0.33, "step": 0.4, "plunge": 0.3,
     "flutes": [1, 2], "tool": "1- or 2-flute flat end mill, sharp",
     "warn": "Long stringy chips wrap around the cutter: clear them often.",
     "tips": ["One of the easiest plastics to machine; gives a fine finish.",
              "Keep the cutter moving, never dwell in one spot."]},
    {"group": "Plastics", "key": "hdpe", "name": "HDPE",
     "level": 1, "rpm": 10000, "cl3": 0.045, "doc": 0.33, "step": 0.4, "plunge": 0.3,
     "flutes": [1, 2], "tool": "1-flute (O-flute) or 2-flute flat end mill",
     "warn": "Soft and springy: thin parts push away from the cutter.",
     "tips": ["Clamp close to the cut; double-sided tape holds poorly on HDPE.",
              "Burrs come off easily with a deburring tool."]},
    {"group": "Plastics", "key": "acrylic", "name": "Acrylic (PMMA)",
     "level": 2, "rpm": 12000, "cl3": 0.04, "doc": 0.17, "step": 0.3, "plunge": 0.25,
     "flutes": [1], "tool": "1-flute (O-flute) end mill",
     "warn": "Melts and re-welds if the chips cannot escape or the feed is too slow.",
     "tips": ["Cast acrylic machines far better than extruded.",
              "Blow air on the cut to clear chips and keep it cool.",
              "Leave the protective film on until the job is done."]},
    {"group": "Plastics", "key": "pvcfoam", "name": "PVC foam board (Forex)",
     "level": 1, "rpm": 10000, "cl3": 0.05, "doc": 0.5, "step": 0.4, "plunge": 0.3,
     "flutes": [1, 2], "tool": "1- or 2-flute flat end mill",
     "warn": "PVC dust and fumes are irritating: ventilate.",
     "tips": ["Very easy to cut; good for signs and prototypes."]},
    # ── Soft metals ─────────────────────────────────────────────────────────
    {"group": "Soft metals", "key": "aluminium", "name": "Aluminium (6061, 5083)",
     "level": 3, "rpm": 12000, "cl3": 0.012, "doc": 0.07, "step": 0.25, "plunge": 0.2,
     "flutes": [2, 1, 3], "tool": "1- or 2-flute carbide end mill for aluminium (polished)",
     "warn": "Chips weld to a small cutter: use air or a little lubricant, and small bites.",
     "tips": ["Shallow passes (about 0.2 mm on a 3 mm cutter) and patience.",
              "Squealing means too fast or too deep: stop and back off.",
              "Climb milling gives a better finish on this machine."]},
    {"group": "Soft metals", "key": "brass", "name": "Brass",
     "level": 3, "rpm": 10000, "cl3": 0.012, "doc": 0.07, "step": 0.25, "plunge": 0.2,
     "flutes": [2, 3, 4], "tool": "2-flute carbide end mill (zero or low rake if you have it)",
     "warn": "Grabs: a sharp positive-rake cutter can pull itself into the work.",
     "tips": ["Cuts cleanly without lubricant.",
              "Small, sharp chips fly far: wear eye protection."]},
    # ── Foam & wax ──────────────────────────────────────────────────────────
    {"group": "Foam & wax", "key": "wax", "name": "Machining wax",
     "level": 1, "rpm": 9000, "cl3": 0.08, "doc": 1.0, "step": 0.5, "plunge": 0.5,
     "flutes": [2, 1, 3, 4], "tool": "any flat or ball end mill",
     "warn": "Too fast a spindle with too slow a feed melts it.",
     "tips": ["The ideal first job: forgiving, and chips can be melted and reused.",
              "Perfect for trying a toolpath before cutting the real material."]},
    {"group": "Foam & wax", "key": "foam", "name": "Foam (XPS, PU tooling board)",
     "level": 1, "rpm": 9000, "cl3": 0.1, "doc": 1.0, "step": 0.5, "plunge": 0.5,
     "flutes": [2, 1, 3, 4], "tool": "any flat or ball end mill (long reach for deep foam)",
     "warn": "Light dust everywhere: extraction helps a lot.",
     "tips": ["Very fast to cut; great for moulds and models.",
              "Hold it with double-sided tape or a vacuum, not hard clamps."]},
]


def css() -> str:
    return ""


def materials_js() -> str:
    """JS building the Materials menu and its panel."""
    js = r"""
(function () {
  var MENU_ID = %(menu_id)s, DLG_ID = %(dlg_id)s;
  var MATERIALS = %(materials)s, GROUPS = %(groups)s, FIELDS = %(fields)s;
  var SPMAX = %(spmax)s, SPMIN = %(spmin)s, FMAX = %(fmax)s, PMIN = %(pmin)s, FSTEP = %(fstep)s;

  function api() { try { return window.kiri && window.kiri.api; } catch (e) { return null; } }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function mm(t, v) { return (t && t.metric === false ? 25.4 : 1) * (+v || 0); }
  function r1(v) { return Math.round(v * 10) / 10; }
  function r2(v) { return Math.round(v * 100) / 100; }

  // Flute count from the tool's name: "1 flute 3mm", "2-flute", "4 Flutes", "O-flute".
  function flutesOf(t) {
    var n = String((t && t.name) || '');
    var m = n.match(/(\d+)\s*-?\s*flutes?/i) || n.match(/(\d+)\s*F\b/);
    if (m) return +m[1];
    if (/o-?flute|single/i.test(n)) return 1;
    return null;
  }

  // The settings for one material and one cutter.
  function settingsFor(mat, diam, flutes) {
    var d = diam > 0 ? diam : 3, z = flutes || 2;
    var cl = mat.cl3 * d / 3;
    cl = Math.max(mat.cl3 * 0.4, Math.min(mat.cl3 * 2, cl));
    var rpm = Math.max(SPMIN, Math.min(SPMAX, mat.rpm));
    var feed = Math.min(FMAX, Math.round(rpm * z * cl));
    return {
      spindle: rpm, rate: feed,
      plunge: Math.max(PMIN, Math.round(feed * mat.plunge)),
      down: Math.max(0.05, Math.round(mat.doc * d * 20) / 20),   // nearest 0.05 mm
      step: r2(mat.step)
    };
  }

  function tools() { var a = api(); try { return a.conf.get().tools || []; } catch (e) { return []; } }
  function toolById(id) { var ts = tools(); for (var i = 0; i < ts.length; i++) if (ts[i].id === id) return ts[i]; return null; }
  function tapered(t) { return t && /^taper/.test(t.type || ''); }

  // A better cutter for this material from the library, or null. Same shape,
  // an accepted flute count, closest diameter not larger than the current one.
  // A material is strict when only one flute count will do (acrylic: 1).
  function strict(mat) { return mat.flutes.length === 1; }

  // Contour is the 3D finish: its cutter's shape IS the surface it leaves, so
  // it is never swapped for another shape. Every other operation cuts with the
  // side and bottom of the cutter, where a flat end mill is the right tool.
  function shapeOk(opType, cur, t) {
    if (t.type === cur.type) return true;
    return opType !== 'contour' && t.type === 'endmill';
  }

  function betterTool(mat, cur, opType) {
    var f = flutesOf(cur);
    if (f != null && mat.flutes.indexOf(f) >= 0) return null;          // already fine
    if (f == null && !strict(mat)) return null;                         // unknown, and it may do
    var d0 = mm(cur, cur.flute_diam), best = null, bestKey = null;
    tools().forEach(function (t) {
      if (t === cur || !shapeOk(opType, cur, t)) return;
      var tf = flutesOf(t);
      if (tf == null || mat.flutes.indexOf(tf) < 0) return;
      var d = mm(t, t.flute_diam);
      // same shape first, then not wider than the cutter it replaces, then
      // closest diameter, then the material's preferred flute count
      var key = [t.type === cur.type ? 0 : 1, d <= d0 + 1e-6 ? 0 : 1, Math.abs(d0 - d), mat.flutes.indexOf(tf)];
      var better = !bestKey;
      for (var i = 0; !better && i < key.length; i++) {
        if (key[i] !== bestKey[i]) { better = key[i] < bestKey[i]; break; }
      }
      if (better) { best = t; bestKey = key; }
    });
    return best;
  }

  function ops() {
    var a = api();
    try {
      var list = a.conf.get().process.ops || [], out = [];
      for (var i = 0; i < list.length; i++) {
        var op = list[i];
        if (!op || Array.isArray(op) || !op.type) continue;
        if (op.type === '|') break;
        out.push(op);
      }
      return out;
    } catch (e) { return []; }
  }

  function opName(op) {
    var name = op.rename;
    if (!name) {
      var tags = String(op.note || '').split(' ').filter(function (t) { return t.charAt(0) === '#'; });
      name = tags.length ? tags[0].slice(1) : op.type;
    }
    return String(name).replace(/ /g, ' ');
  }

  // What Apply would do to each operation: [{op, tool, swapTo, fields:[{k, from, to}], why}]
  function plan(mat) {
    return ops().map(function (op) {
      var keys = FIELDS[op.type];
      var cur = toolById(op.tool);
      if (!keys) return { op: op, skip: 'not changed by the guide (' + op.type + ')' };
      if (!cur) return { op: op, skip: 'has no tool' };
      var swap = betterTool(mat, cur, op.type), t = swap || cur;
      // an unknown flute count is taken as the material's preferred one --
      // the conservative guess for plastics and metals, the same for wood
      var want = settingsFor(mat, mm(t, t.flute_diam), flutesOf(t) || mat.flutes[0]);
      if (op.type === 'contour') want.step = FSTEP;
      var fields = [];
      keys.forEach(function (k) {
        if (k === 'step' && tapered(t)) return;
        if (op[k] === undefined && k !== 'spindle') return;
        if (+op[k] !== +want[k]) fields.push({ k: k, from: op[k], to: want[k] });
      });
      var note = '';
      var f = flutesOf(cur);
      var wrong = (f != null && mat.flutes.indexOf(f) < 0) || (f == null && strict(mat));
      if (!swap && wrong)
        note = mat.name + ' needs a ' + mat.flutes.join('- or ') + '-flute cutter' +
               (op.type === 'contour' ? ' of this shape' : '') +
               ' and the library has none: add one (' + mat.tool + ')';
      else if (!swap && f == null)
        note = 'flute count not in the tool name, so ' + mat.flutes[0] + ' is assumed';
      if (swap && swap.type !== cur.type)
        note = 'switched to a flat end mill: the right cutter for this ' + op.type + ' in ' + mat.name;
      return { op: op, tool: cur, swapTo: swap, fields: fields, note: note };
    });
  }

  var LABEL = { spindle: 'RPM', rate: 'feed', plunge: 'plunge', down: 'per pass', step: 'step-over' };
  function fmtField(k, v) {
    if (v === undefined || v === null || v === '') return '–';
    if (k === 'step') return Math.round(+v * 100) + '%%';
    if (k === 'down') return r2(+v) + ' mm';
    if (k === 'spindle') return Math.round(+v);
    return Math.round(+v) + ' mm/min';
  }

  // ---------------------------------------------------------------- panel --
  var state = { group: GROUPS[0], key: null };

  function close() {
    var d = document.getElementById(DLG_ID);
    if (d) d.remove();
    document.removeEventListener('keydown', onKey, true);
  }
  function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } }

  function stars(n) { return '★★★'.slice(0, n) + '☆☆☆'.slice(0, 3 - n); }
  function levelText(n) { return ['', 'beginner-friendly', 'some practice', 'advanced'][n]; }

  function render() {
    var box = document.querySelector('#' + DLG_ID + ' .pm-box');
    if (!box) return;
    var inGroup = MATERIALS.filter(function (m) { return m.group === state.group; });
    if (!state.key || !inGroup.some(function (m) { return m.key === state.key; })) state.key = inGroup[0].key;
    var mat = MATERIALS.filter(function (m) { return m.key === state.key; })[0];
    var ref = settingsFor(mat, 3, mat.flutes[0]);

    var h = '<div class="pm-head"><b>Materials</b><span class="pm-x" title="close">✕</span></div>';
    h += '<div class="pm-tabs">' + GROUPS.map(function (g) {
      return '<button class="pm-tab' + (g === state.group ? ' on' : '') + '" data-g="' + esc(g) + '">' + esc(g) + '</button>';
    }).join('') + '</div>';
    h += '<div class="pm-mats">' + inGroup.map(function (m) {
      return '<button class="pm-mat' + (m.key === state.key ? ' on' : '') + '" data-k="' + m.key + '">' + esc(m.name) + '</button>';
    }).join('') + '</div>';

    h += '<div class="pm-card"><div class="pm-title">' + esc(mat.name) +
         '<span class="pm-lvl">' + stars(mat.level) + ' ' + levelText(mat.level) + '</span></div>';
    h += '<table class="pm-tbl">' +
      '<tr><td>Cutter</td><td>' + esc(mat.tool) + '</td></tr>' +
      '<tr><td>Spindle</td><td>' + ref.spindle + ' RPM</td></tr>' +
      '<tr><td>Feed</td><td>' + ref.rate + ' mm/min &nbsp; plunge ' + ref.plunge + ' mm/min</td></tr>' +
      '<tr><td>Per pass</td><td>' + ref.down + ' mm deep &nbsp; step-over ' + Math.round(ref.step * 100) + '%%</td></tr>' +
      '</table>';
    h += '<div class="pm-ref">for a 3 mm ' + mat.flutes[0] + '-flute cutter. Apply works the values out for each operation\'s own cutter.</div>';
    h += '<div class="pm-warn">⚠ ' + esc(mat.warn) + '</div>';
    h += '<ul class="pm-tips">' + mat.tips.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
    h += '<div class="pm-note">Starting points, not limits: listen to the cut. Squealing, chatter or melting means slow down or cut shallower.</div>';
    h += '</div>';

    // the per-operation preview: exactly what Apply will write
    var p = plan(mat);
    h += '<div class="pm-sub">Apply to this job</div>';
    if (!p.length) {
      h += '<div class="pm-ref">No operations yet. Add operations, then come back to apply these settings.</div>';
    } else {
      h += '<div class="pm-ops">' + p.map(function (e, i) {
        var line = '<label class="pm-op"><input type="checkbox" data-i="' + i + '"' +
          (e.skip || (!e.fields.length && !e.swapTo) ? ' disabled' : ' checked') + '> <b>' + esc(opName(e.op)) + '</b>';
        if (e.skip) return line + ' <span class="pm-dim">' + esc(e.skip) + '</span></label>';
        var bits = [];
        if (e.swapTo) bits.push('tool <s>' + esc(e.tool.name) + '</s> → <b>' + esc(e.swapTo.name) + '</b>');
        e.fields.forEach(function (f) {
          bits.push(LABEL[f.k] + ' ' + fmtField(f.k, f.from) + ' → <b>' + fmtField(f.k, f.to) + '</b>');
        });
        if (!bits.length) bits.push('<span class="pm-dim">already set</span>');
        line += '<div class="pm-chg">' + bits.join(' &nbsp;·&nbsp; ') + '</div>';
        if (e.note) line += '<div class="pm-dim">' + esc(e.note) + '</div>';
        return line + '</label>';
      }).join('') + '</div>';
    }
    h += '<div class="pm-btns"><button class="pm-apply"' + (p.some(function (e) { return !e.skip && (e.fields.length || e.swapTo); }) ? '' : ' disabled') +
         '>Apply to selected operations</button><button class="pm-close">Close</button></div>';
    box.innerHTML = h;

    box.querySelector('.pm-x').onclick = close;
    box.querySelector('.pm-close').onclick = close;
    Array.prototype.forEach.call(box.querySelectorAll('.pm-tab'), function (b) {
      b.onclick = function () { state.group = b.getAttribute('data-g'); state.key = null; render(); };
    });
    Array.prototype.forEach.call(box.querySelectorAll('.pm-mat'), function (b) {
      b.onclick = function () { state.key = b.getAttribute('data-k'); render(); };
    });
    var ap = box.querySelector('.pm-apply');
    if (ap) ap.onclick = function () {
      var chosen = Array.prototype.filter.call(box.querySelectorAll('.pm-ops input[type=checkbox]'),
        function (c) { return c.checked && !c.disabled; }).map(function (c) { return p[+c.getAttribute('data-i')]; });
      apply(chosen);
      render();
      var msg = document.createElement('div');
      msg.className = 'pm-done';
      msg.textContent = chosen.length ? ('Applied ' + mat.name + ' settings to ' + chosen.length +
        ' operation' + (chosen.length === 1 ? '' : 's') + '. Generate again to see them.') : 'Nothing selected.';
      var nb = document.querySelector('#' + DLG_ID + ' .pm-btns');
      if (nb) nb.parentNode.insertBefore(msg, nb);
    };
  }

  var applying = false;
  function apply(entries) {
    if (applying || !entries.length) return;
    entries.forEach(function (e) {
      if (e.swapTo) e.op.tool = e.swapTo.id;
      e.fields.forEach(function (f) { e.op[f.k] = f.to; });
    });
    var a = api();
    applying = true;
    try { a.conf.save(); } catch (e) { }
    finally { applying = false; }
  }

  function style() {
    if (document.getElementById(DLG_ID + '-style')) return;
    var s = document.createElement('style');
    s.id = DLG_ID + '-style';
    var R = '#' + DLG_ID + ' ';
    s.textContent = [
      R + '.pm-box, ' + R + '.pm-box div { display: block !important; white-space: normal !important; }',
      R + '.pm-box { background:#fff; color:#222; border-radius:8px; box-shadow:0 10px 40px rgba(0,0,0,.35);' +
        ' padding:14px 18px 12px; width:min(640px,94vw); max-height:88vh; overflow:auto; font-size:13px; line-height:1.4; }',
      // flex rows must out-rank the "every div is a block" rule above
      R + '.pm-box div.pm-head { display:flex !important; justify-content:space-between; align-items:center; font-size:16px; margin-bottom:8px; }',
      R + '.pm-x { cursor:pointer; padding:2px 6px; color:#666; }',
      R + '.pm-box div.pm-tabs, ' + R + '.pm-box div.pm-mats, ' + R + '.pm-box div.pm-btns { display:flex !important; flex-direction:row !important; flex-wrap:wrap; gap:6px; margin:6px 0; }',
      // Kiri styles buttons as full-width blocks; these are inline chips
      R + '.pm-box button { display:inline-block !important; width:auto !important; flex:0 0 auto; margin:0; }',
      R + '.pm-tab, ' + R + '.pm-mat { border:1px solid #c9c9c9; background:#f5f5f5; border-radius:5px; padding:4px 10px; cursor:pointer; font-size:12px; }',
      R + '.pm-tab.on { background:#3A78B5; color:#fff; border-color:#3A78B5; }',
      R + '.pm-mat.on { background:#dfeaf5; border-color:#3A78B5; font-weight:600; }',
      R + '.pm-card { border:1px solid #e2e2e2; border-radius:6px; padding:10px 12px; margin:8px 0; background:#fbfbfb; }',
      R + '.pm-box div.pm-title { display:flex !important; justify-content:space-between; align-items:baseline; gap:12px; font-size:15px; font-weight:600; margin-bottom:6px; }',
      R + '.pm-lvl { font-size:12px; font-weight:400; color:#b07800; }',
      R + '.pm-tbl { display:table !important; border-collapse:collapse; width:100%%; }',
      R + '.pm-tbl tr { display:table-row !important; } ' + R + '.pm-tbl td { display:table-cell !important; padding:2px 6px 2px 0; vertical-align:top; }',
      R + '.pm-tbl td:first-child { color:#666; width:72px; white-space:nowrap; }',
      R + '.pm-warn { margin-top:8px; padding:6px 8px; background:#fff4e0; border:1px solid #e0a030; border-radius:5px; color:#6b4a00; }',
      R + '.pm-tips { margin:6px 0 2px 18px; padding:0; } ' + R + '.pm-tips li { display:list-item !important; }',
      R + '.pm-ref, ' + R + '.pm-dim { color:#777; font-size:12px; }',
      R + '.pm-note { margin-top:6px; color:#555; font-size:12px; font-style:italic; }',
      R + '.pm-sub { font-weight:600; margin-top:10px; }',
      R + '.pm-op { display:block !important; padding:5px 0; border-bottom:1px solid #eee; cursor:pointer; }',
      R + '.pm-chg { margin:2px 0 0 22px; font-size:12px; }',
      R + '.pm-box div.pm-btns { justify-content:flex-end; margin-top:10px; }',
      R + '.pm-btns button { padding:5px 14px; font-size:13px; cursor:pointer; }',
      R + '.pm-apply { background:#3A78B5; color:#fff; border:1px solid #3A78B5; border-radius:5px; }',
      R + '.pm-apply[disabled] { background:#aac0d6; border-color:#aac0d6; cursor:default; }',
      R + '.pm-done { margin-top:8px; padding:6px 8px; background:#e8f4ea; border:1px solid #7cb98a; border-radius:5px; color:#1e5b2c; }'
    ].join('\n');
    document.head.appendChild(s);
  }

  function open() {
    style();
    close();
    var back = document.createElement('div');
    back.id = DLG_ID;
    back.setAttribute('style', 'position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.35);' +
      'display:flex;align-items:center;justify-content:center;');
    var box = document.createElement('div');
    box.className = 'pm-box';
    back.appendChild(box);
    back.onclick = function (ev) { if (ev.target === back) close(); };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    render();
  }
  window.__piccolaMaterials = { open: open, settingsFor: settingsFor, flutesOf: flutesOf,
                                betterTool: betterTool, plan: plan, materials: MATERIALS };

  // ---- the menu, next to Analysis (or Render), built like Kiri's own ----
  function install() {
    if (document.getElementById(MENU_ID)) return true;
    var anchor = document.getElementById('piccola-analysis-menu');
    if (!anchor) {
      var render = document.querySelector('label[lk="re_menu"]');
      anchor = render && render.parentElement;
    }
    if (!anchor || !anchor.parentElement) return false;
    var span = document.createElement('span');
    span.id = MENU_ID;
    var lab = document.createElement('label');
    lab.textContent = 'guide';
    span.appendChild(lab);
    var drop = document.createElement('div');
    drop.className = 'top-menu-drop top-menu-left';
    var content = document.createElement('div');
    content.className = 'content';
    function entry(text, icon, title, onclick) {
      var item = document.createElement('div');
      item.title = title;
      var il = document.createElement('label');
      il.textContent = text;
      var ic = document.createElement('span');
      ic.innerHTML = '<i class="fas ' + icon + '"></i>';
      item.appendChild(il); item.appendChild(ic);
      item.onclick = function (ev) { ev.stopPropagation(); onclick(); };
      content.appendChild(item);
    }
    entry('materials', 'fa-layer-group',
          'What this machine can cut, and starting settings for each material', open);
    // the panel comes from ui/cam_ops_guide.py, injected separately
    entry('operations guide', 'fa-book',
          'Which operation does what, the settings that matter, and a worked example',
          function () { if (window.__piccolaOpsGuide) window.__piccolaOpsGuide.open(); });
    drop.appendChild(content);
    span.appendChild(drop);
    anchor.parentElement.insertBefore(span, anchor.nextSibling);
    return true;
  }

  var tries = 600;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
"""
    out = js % {
        "menu_id": json.dumps(MENU_ID),
        "dlg_id": json.dumps(DLG_ID),
        "materials": json.dumps(MATERIALS),
        "groups": json.dumps(GROUPS),
        "fields": json.dumps({k: list(v) for k, v in APPLY_FIELDS.items()}),
        "spmax": json.dumps(SPINDLE_MAX),
        "spmin": json.dumps(SPINDLE_MIN),
        "fmax": json.dumps(FEED_MAX),
        "pmin": json.dumps(PLUNGE_MIN),
        "fstep": json.dumps(FINISH_STEP),
    }
    return out
