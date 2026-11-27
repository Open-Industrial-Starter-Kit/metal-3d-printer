"""
ui/cam_gcode_header.py
A readable comment header at the top of every Kiri:Moto G-code export -- tools,
operations, how deep the job cuts -- and a bigger "gcode preview" in the export
dialog to read it in.

Why
---
The program Kiri hands over opens straight with ``G21`` / ``G90``: nothing in
the file says which cutter it was made for or how far below zero it goes. The
stock ``Any.Generic.Grbl`` profile ships ``"strip-comments": true``, and with
that set ``work/export.js`` drops its own header (stock, ``; tool#=… flute=…``)
along with every other comment. Turning the strip off is not the fix: what
comes back is Kiri's dump of *every* process setting, a hundred-odd
``; camXxx = …`` lines, with the tool line in inch-or-mm "flute" units and still
no lowest Z. So the strip stays on and this module writes a short header of its
own, in the spirit of the one Fusion puts in front of a program
(``(T2 D=1.5 CR=0 - ZMIN=-4.4 - flat end mill)``)::

    ; Piccola CNC - Kiri:Moto 4.7.2
    ; created 2026-10-13 08:48
    ; units mm, absolute (G21 G90)
    ; stock X 72.600 Y 44.400 Z 5.000
    ; --- tools ---
    ; T1 D=3.175 flute 12.000 - ZMIN=-5.010 - flat end mill "end 1/8"
    ; --- operations ---
    ; rough_1   rough   T1  down 0.375  feed 800  plunge 200  spindle 12000
    ; --- extents ---
    ; X -0.749 .. 72.000   Y -44.400 .. 0.000   Z -5.010 .. 1.990
    ; ZMIN=-5.010

In Rotary Mode the exported Z is a radius from the rotary axis and Z 0 is
that axis, or with ``camRotaryZTop`` the top of the bar (Kiri:Moto patch
0053), so the header says which and measures the depth from the bar's
surface instead of a top face::

    ; origin X 0.500 Y 0.500 Z 0.500 - center, rotary axis
    ; rotary: Z 0 on the rotary axis, Z = radius - stock radius 15.517
    ; T7 D=2.000 flute 6.000 - ZMIN=12.000 - flat end mill "2 flute 6mm"

    ; origin X 0.500 Y 0.500 Z 1.000 - center, top of the bar
    ; rotary: Z 0 on top of the bar, Z = radius - 15.517 - stock radius 15.517
    (rotary: Z 0 on top of the bar, Z = radius - 15.517 - stock radius 15.517)
    ; T7 D=2.000 flute 6.000 - ZMIN=-3.517 - flat end mill "2 flute 6mm"

The rotary line is written twice, the second time as a ``( )`` comment. The
machine drops ``;`` lines when it stores a program, so the copy Live Preview
fetches back from it ("generate preview") has no header at all; ``( )``
comments survive (a Fusion program's ``(T2 D=1.5 …)`` lines come back
intact). Without that line a top-of-the-bar job is unreadable: its Z is
measured from the bar's surface, and read as a radius from the axis it wraps
the whole job into a tangle a few millimetres round the centre. Seen
2026-11-18 on a 24.5 mm roller: drawn at radius 0-6.4 mm instead of
11.3-18.7 mm.

Comments cost nothing on the machine: the firmware drops comment lines before
the program is transferred (see ``connection.py``), and Live Preview and
``gcode_bounds`` already skip them.

How
---
Kiri runs a G-code post processor of its own choosing at export:
``app/export.js`` does ::

    codeproc = settings.device.gcodeProc,
    ...
    if (codeproc && self[codeproc]) { gcode = self[codeproc](gcode); }

before the text is used for *anything* -- the preview, the download, and so the
"save as file" / "save to machine" buttons from ui/cam_export.py, which click
that download. :func:`gcode_header_js` defines that function on ``window`` as
:data:`PROC_NAME`, and ``CamPage`` pins ``device.gcodeProc`` to it next to the
other device pins (bed size, spindle), so a profile switch or import cannot
quietly drop the header.

The extents and ZMIN are read back from the finished G-code, not from Kiri's
settings: what the file does is the thing worth stating. Every ``X``/``Y``/``Z``
word on a motion line counts (Kiri writes absolute coordinates, ``G90``, and
arcs are pinned off), and ZMIN is tracked per tool by following ``T`` words --
before the first one, moves belong to the tool of the first operation. Tools
and operations come from ``api.conf.get()`` at that moment, which is the same
settings object the export was made from.

Not covered: Kiri's *zip* download builds its per-operation files from the raw
sections and never calls the post processor, so those files stay header-less.

Bigger preview
--------------
Upstream sizes the preview at ``height: 7em`` in ``x-small`` text -- four
readable lines. Kiri already has a tall layout for it, used only in its
developer mode (``.devel #modal-box { height: 80% }`` and a preview that grows
into the space); :func:`css` applies the same thing whenever the export dialog
is the one showing, with a readable font size and a wider box.
"""

from __future__ import annotations

import json

#: Global function name Kiri's ``device.gcodeProc`` is pinned to.
PROC_NAME = "piccolaGcodeHeader"

#: Readable names for Kiri's tool ``type`` values. Unknown types print as-is.
TOOL_TYPES = {
    "endmill": "flat end mill",
    "ballmill": "ball end mill",
    "tapermill": "taper mill",
    "taperball": "taper ball mill",
    "drill": "drill",
}

#: Kiri's clock-divider operation type. `slice.js` ignores everything after it
#: (same reading as ui/cam_no_ops.py).
CLOCK_TYPE = "|"

#: Prefix of a header line saying a tool cuts deeper than it reaches.
#: ``ui/pages/cam.py`` stops the export when it finds one, so the two must
#: agree: change it in one place only.
LENGTH_WARNING = "; WARNING tool length:"

#: How far past its reach a tool may go before it counts (mm). Kiri rounds to
#: three decimals and the stock is often the part's own height, so a cut
#: exactly to the bottom must not trip it on rounding alone.
LENGTH_TOL_MM = 0.01


def css() -> str:
    """Make the export dialog's G-code preview large and readable."""
    return "\n".join([
        # Kiri's own developer-mode layout, keyed on the export dialog being
        # the visible one instead of on body.devel.
        "#modal-box:has(> #mod-x-any[style*='flex'])"
        " { height: 88%; max-height: 88%; }",
        "#mod-x-any[style*='flex'] { height: 100%; box-sizing: border-box; }",
        "#mod-x-any #code-preview { flex-grow: 1; min-height: 0; }",
        "#mod-x-any #code-preview > div { height: 100%; }",
        "#mod-x-any #code-preview-textarea {"
        " min-width: min(1100px, 85vw); height: 100%; min-height: 20em;"
        " font-size: 13px; line-height: 1.35; resize: vertical; }",
    ])


def gcode_header_js() -> str:
    """JS defining ``window[PROC_NAME]``, Kiri's export post processor."""
    js = """
(function () {
  var NAME = %(name)s, TYPES = %(types)s, CLOCK = %(clock)s;
  var WARN = %(warn)s, TOL = %(tol)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }
  function n(v) { return (Math.round(v * 1000) / 1000).toFixed(3); }
  function pad(s, w) { s = String(s); while (s.length < w) s += ' '; return s; }
  function two(v) { return (v < 10 ? '0' : '') + v; }
  function range() { return { min: Infinity, max: -Infinity }; }
  function grow(r, v) { if (v < r.min) r.min = v; if (v > r.max) r.max = v; }

  // Diameter / length of a tool record in mm (Kiri stores inch tools in inch,
  // and like Kiri's Tool.unitScale a record without `metric` is inch).
  function mm(tool, v) { return (tool && tool.metric ? 1 : 25.4) * (+v || 0); }

  // Enabled operations above the clock bar: exactly what slice.js runs.
  function runOps(settings) {
    var ops = (settings.process && settings.process.ops) || [], out = [];
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (!op || Array.isArray(op)) continue;
      if (op.type === CLOCK) break;
      if (!op.disabled) out.push(op);
    }
    return out;
  }

  function opName(op) {
    // renamed operations keep spaces as no-break spaces (ui/cam_op_names.py);
    // the file gets plain ones
    var name = op.rename;
    if (!name) {
      var tags = String(op.note || '').split(' ').filter(function (t) { return t.charAt(0) === '#'; });
      name = tags.length ? tags[0].slice(1) : op.type;
    }
    return String(name).replace(/\\u00a0/g, ' ');
  }

  // Rotary Mode: the stock turns about X, the toolpath is made with that axis
  // at z = 0 and every z a radius, and the export keeps it so (Kiri:Moto patch
  // 0053) -- Z 0 is the rotary axis, or with camRotaryZTop the top of the
  // bar, whatever the origin fields say.
  function rotary(settings) {
    return !!((settings.process || {}).camStockIndexed);
  }

  // The G-code's Z 0 as a radius from the axis: 0 on the axis, the bar's
  // radius on top of it -- the same arithmetic as the export's.
  function rotaryZero(settings) {
    var P = settings.process || {}, st = settings.stock || {};
    if (!rotary(settings) || !P.camRotaryZTop) return 0;
    var y = +st.y || 0, z = +st.z || 0;
    return (P.camStockRound ? Math.min(y, z) : z) / 2;
  }

  function rotaryWhere(settings) {
    return rotaryZero(settings) ? 'top of the bar' : 'rotary axis';
  }

  // How far the stock reaches above the rotary axis: the bar's radius, or a
  // square blank's corner -- the most material a rotary cut can have above the
  // tool tip, which is what a flute has to span.
  function stockRadius(settings) {
    var P = settings.process || {}, st = settings.stock || {};
    var y = +st.y || 0, z = +st.z || 0;
    return P.camStockRound ? Math.min(y, z) / 2 : Math.hypot(y, z) / 2;
  }

  // Where the stock's top face sits in the program's own Z. Work zero is
  // ``fz * stock.z`` above the stock bottom, with fz read back from
  // camOriginTop / camOriginOffZ exactly as ui/cam_stock_origin.py writes them,
  // so the top is ``(1 - fz) * stock.z`` above zero: 0 with the default origin.
  function stockTopZ(settings) {
    var P = settings.process || {}, z = +((settings.stock || {}).z) || 0;
    if (!(z > 0)) return 0;
    var off = +P.camOriginOffZ || 0;
    var fz = P.camOriginTop ? off / z + 1 : off / z;
    return (1 - fz) * z;
  }

  // Where work zero sits on the stock, as fractions of the box (0 = left /
  // front(bottom) / bottom face, 1 = right / back(top) / top face), read back
  // from camOriginCenter/Top + camOriginOff* exactly as ui/cam_stock_origin.py
  // writes them. Verified on a live job: centre origin on a 90.4 x 92.7 stock
  // has offsets 45.2 / 46.35 -> 0.5 / 0.5, matching its -45.2..45.2 extents.
  function originFractions(settings) {
    var P = settings.process || {}, st = settings.stock || {};
    if (!(st.x > 0 && st.y > 0 && st.z > 0)) return null;
    var c = !!P.camOriginCenter;
    if (rotary(settings)) {
      // Y is locked to the rotary working position and Z 0 is the axis: only
      // the place along the bar is a choice (ui/cam_stock_origin.py).
      return { x: (c ? (+P.camOriginOffX || 0) / st.x + 0.5 : (+P.camOriginOffX || 0) / st.x),
               y: 0.5, z: rotaryZero(settings) ? 1 : 0.5 };
    }
    return {
      x: (c ? (+P.camOriginOffX || 0) / st.x + 0.5 : (+P.camOriginOffX || 0) / st.x),
      y: (c ? (+P.camOriginOffY || 0) / st.y + 0.5 : (+P.camOriginOffY || 0) / st.y),
      z: (P.camOriginTop ? (+P.camOriginOffZ || 0) / st.z + 1 : (+P.camOriginOffZ || 0) / st.z)
    };
  }

  // The same position in words, for whoever reads the file. ui/job_summary.py
  // words it again from the fractions for the Live Preview chip.
  function originWords(o, rot) {
    function near(v, t) { return Math.abs(v - t) <= 0.02; }
    function axis(v, lo, hi) {
      return near(v, 0) ? lo : near(v, 1) ? hi : near(v, 0.5) ? 'center'
        : Math.round(v * 100) + '%% from ' + lo;
    }
    var x = axis(o.x, 'left', 'right'), y = axis(o.y, 'bottom', 'top');
    if (rot) return (x === 'center' ? 'center' : x + ' end') + ', ' + rot;
    var xy = (x === 'center' && y === 'center') ? 'center' : y + ' ' + x;
    var z = near(o.z, 1) ? 'top of stock' : near(o.z, 0) ? 'bottom of stock'
      : Math.round(o.z * 100) + '%% up the stock';
    return xy + ', ' + z;
  }

  // How deep a tool can cut before something other than flutes touches the
  // work. The flutes, normally. A reduced (necked) shank -- narrower than the
  // cutter -- cannot rub a wall the cutter made, so there the reach is flutes
  // plus shank. The holder is not modelled: Kiri's tool record has no
  // stick-out, so this is the tool's own length, not how far it hangs out.
  function reachOf(t) {
    var flute = mm(t, t.flute_len), shank = mm(t, t.shaft_len);
    var dCut = mm(t, t.flute_diam), dShank = mm(t, t.shaft_diam);
    if (!(flute > 0)) return null;
    var necked = dShank > 0 && dCut > 0 && dShank < dCut * 0.98;
    return { len: necked && shank > 0 ? flute + shank : flute, necked: necked };
  }

  // Tools that cut deeper than they reach, as "; WARNING tool length: ..." lines.
  // The prefix is what ui/pages/cam.py looks for to stop the export.
  function lengthWarnings(settings, used, zmin) {
    // Rotary: Z is a radius, so the depth is from the bar's surface in to the
    // smallest radius the tool reaches, not from a top face.
    var rot = rotary(settings), zero = rotaryZero(settings);
    var top = rot ? stockRadius(settings) : stockTopZ(settings), out = [];
    used.forEach(function (t) {
      var z = zmin[t.number];
      if (z === undefined) return;
      var reach = reachOf(t);
      if (!reach) return;
      var r = z + zero;  // rotary: the smallest radius the tool reaches
      var depth = top - r;
      if (depth > reach.len + TOL) {
        out.push(WARN + ' T' + t.number + (t.name ? ' "' + t.name + '"' : '')
          + ' cuts ' + n(depth) + ' deep' + (rot ? ' into the bar (radius ' + n(top) + ' to ' + n(r) + ')' : '')
          + ', ' + (reach.necked ? 'reach' : 'flute')
          + ' only ' + n(reach.len) + ' - short by ' + n(depth - reach.len));
      }
    });
    return out;
  }

  // Positions the program visits, read back from the G-code itself.
  function scan(gcode, firstTool) {
    var ext = { x: range(), y: range(), z: range() }, zmin = {};
    var pos = { x: null, y: null, z: null }, motion = null, tool = firstTool;
    var lines = gcode.split(/\\r?\\n/);
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].split(';')[0].replace(/\\([^)]*\\)/g, '').toUpperCase();
      if (!line.trim()) continue;
      var re = /([A-Z])\\s*([-+]?\\d*\\.?\\d+)/g, m, moved = false;
      while ((m = re.exec(line))) {
        var w = m[1], v = parseFloat(m[2]);
        if (w === 'G' && v >= 0 && v <= 3 && v === Math.floor(v)) motion = v;
        else if (w === 'T') tool = v;
        else if (w === 'X' || w === 'Y' || w === 'Z') { pos[w.toLowerCase()] = v; moved = true; }
      }
      if (!moved || motion === null) continue;
      if (pos.x !== null) grow(ext.x, pos.x);
      if (pos.y !== null) grow(ext.y, pos.y);
      if (pos.z !== null) {
        grow(ext.z, pos.z);
        if (tool != null && (zmin[tool] === undefined || pos.z < zmin[tool])) zmin[tool] = pos.z;
      }
    }
    return { ext: ext, zmin: zmin };
  }

  function header(gcode) {
    var a = api(), settings = a && a.conf && a.conf.get();
    if (!settings) return [];
    var tools = settings.tools || [], ops = runOps(settings);
    function toolById(id) {
      for (var i = 0; i < tools.length; i++) if (tools[i].id === id) return tools[i];
      return null;
    }

    // tools in the order the operations first use them
    var used = [], seen = {};
    ops.forEach(function (op) {
      var t = op.tool != null ? toolById(op.tool) : null;
      if (t && !seen[t.id]) { seen[t.id] = 1; used.push(t); }
    });
    var res = scan(gcode, used.length ? used[0].number : null);
    var ext = res.ext;

    var d = new Date(), out = [];
    var ver = (a.version || (window.kiri && window.kiri.version) || '');
    out.push('; Piccola CNC - Kiri:Moto' + (ver ? ' ' + ver : ''));
    out.push('; created ' + d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate())
             + ' ' + two(d.getHours()) + ':' + two(d.getMinutes()));
    out.push('; units mm, absolute (G21 G90)');
    var st = settings.stock || {};
    if (st.x || st.y || st.z) out.push('; stock X ' + n(st.x || 0) + ' Y ' + n(st.y || 0) + ' Z ' + n(st.z || 0));
    var rot = rotary(settings);
    var org = originFractions(settings);
    if (org) out.push('; origin X ' + n(org.x) + ' Y ' + n(org.y) + ' Z ' + n(org.z) + ' - ' + originWords(org, rot ? rotaryWhere(settings) : null));
    // read back by ui/job_summary.py: where Z 0 is and how far out the stock
    // reaches, so the Live Preview can say how far into the bar the job goes
    // -- and, for a Z 0 on top of the bar, wrap the toolpath at the right
    // radius at all. The same words go out a second time as a ( ) comment:
    // the machine keeps those but drops every ; line, so a copy fetched back
    // from it (Live Preview's "generate preview") still says where Z 0 is.
    if (rot) {
      var zero = rotaryZero(settings);
      var rline = 'rotary: Z 0 on ' + (zero ? 'top of the bar' : 'the rotary axis')
        + ', Z = radius' + (zero ? ' - ' + n(zero) : '') + ' - stock radius ' + n(stockRadius(settings));
      out.push('; ' + rline);
      out.push('(' + rline + ')');
    }

    if (used.length) {
      out.push('; --- tools ---');
      used.forEach(function (t) {
        var z = res.zmin[t.number];
        var s = '; T' + t.number + ' D=' + n(mm(t, t.flute_diam));
        if (t.flute_len) s += ' flute ' + n(mm(t, t.flute_len));
        if ((t.type === 'tapermill' || t.type === 'taperball') && t.taper_tip)
          s += ' tip ' + n(mm(t, t.taper_tip));
        if (z !== undefined) s += ' - ZMIN=' + n(z);
        s += ' - ' + (TYPES[t.type] || t.type || 'tool');
        if (t.name) s += ' "' + t.name + '"';
        out.push(s);
      });
    }

    var warns = lengthWarnings(settings, used, res.zmin);
    if (warns.length) {
      out.push('; --- warnings ---');
      warns.forEach(function (w) { out.push(w); });
    }

    if (ops.length) {
      out.push('; --- operations ---');
      ops.forEach(function (op) {
        var t = op.tool != null ? toolById(op.tool) : null;
        var s = '; ' + pad(opName(op), 12) + ' ' + pad(op.type, 8);
        if (t) s += ' T' + t.number;
        if (op.down) s += '  down ' + n(op.down);
        if (op.rate) s += '  feed ' + op.rate;
        if (op.plunge) s += '  plunge ' + op.plunge;
        if (op.spindle) s += '  spindle ' + op.spindle;
        out.push(s.replace(/\\s+$/, ''));
      });
    }

    if (ext.z.min !== Infinity) {
      out.push('; --- extents ---');
      var e = [];
      if (ext.x.min !== Infinity) e.push('X ' + n(ext.x.min) + ' .. ' + n(ext.x.max));
      if (ext.y.min !== Infinity) e.push('Y ' + n(ext.y.min) + ' .. ' + n(ext.y.max));
      e.push('Z ' + n(ext.z.min) + ' .. ' + n(ext.z.max));
      out.push('; ' + e.join('   '));
      out.push('; ZMIN=' + n(ext.z.min));
    }
    return out;
  }

  // Kiri's post-processor contract: G-code text in, G-code text out. Any
  // failure returns the program untouched -- a missing header must never cost
  // the export.
  window[NAME] = function (gcode) {
    try {
      var head = header(gcode);
      if (!head.length) return gcode;
      return head.join('\\r\\n') + '\\r\\n' + gcode;
    } catch (e) {
      try { console.warn('piccola gcode header failed', e); } catch (e2) { }
      return gcode;
    }
  };
})();
"""
    return js % {
        "name": json.dumps(PROC_NAME),
        "types": json.dumps(TOOL_TYPES),
        "clock": json.dumps(CLOCK_TYPE),
        "warn": json.dumps(LENGTH_WARNING),
        "tol": json.dumps(LENGTH_TOL_MM),
    }
