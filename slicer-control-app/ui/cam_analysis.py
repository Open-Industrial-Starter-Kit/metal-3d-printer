"""
ui/cam_analysis.py
An *Analysis* menu next to *Render* in Kiri:Moto, with a **minimum tool
diameter** analysis: the largest flat cutter that can still reach every area of
the model, from the top (3-axis).

What it answers
---------------
"What is the biggest tool I can use and still cut everything?" -- equivalently,
the smallest tool the job needs. For 3-axis work from above, that is decided by
the narrowest gaps, slots and pockets at each floor height: a cutter wider than
a gap cannot enter it. The report is that one number, WHERE the limiting spot
is, and which tools in the library are small enough -- nothing more. (A
per-floor table and a list of every enclosed hole were tried and were more
detail than the question needs.)

Sharp inside corners are deliberately not the limit. Any round cutter leaves a
sliver in a sharp corner, however small it is -- counting those would make the
answer "an infinitely small tool" for nearly every part. Only material a cutter
genuinely cannot get to (it does not fit) counts.

How
---
1. The model's top surface is rasterised into a height map (the same way the
   over-cut colour in Animate does it, patch 0027), on a cell size chosen to
   keep the grid around 1.5 million cells (about 0.08 mm on a 100 x 54 mm part),
   padded with free space around the stock so a cutter can approach from
   outside.
2. Every floor height of the model (a flat area of at least 1 mm2) is a level
   the cutter has to work at, and so is the bottom of the model, always:
   through-holes and the outside contour have no floor of their own, but the
   cutter goes all the way down them. At each level the material is what
   stands above it.
3. At each level an exact Euclidean distance transform of the free space gives,
   for every free cell, its distance to the nearest material, d(p). A cutter of
   radius r can be centred where d >= r; the area it can reach is those centres
   grown by r (a morphological opening) -- a second distance transform.
4. What the cutter cannot reach is split into connected patches, each judged
   whole. A patch with no cut area next to it is a pocket the cutter cannot
   enter: real. Otherwise its depth -- how far it reaches past the area the
   cutter covers -- decides: a corner sliver reaches at most ~1 radius past it
   (0.41 r for a 90 degree corner, r for 60), a slot too narrow for the cutter
   goes its whole length deep. (A per-cell test, "closer to the wall than
   0.3 r", was tried first and is wrong: for a large cutter a whole narrow slot
   is that close to its walls and was taken for a corner.)
5. Radii are scanned upward and the first that does not fit is the answer at
   that level, narrowed down by bisection; the overall answer is the smallest
   over all levels, i.e. the narrowest spot. Library tools are judged against
   those per-level answers, so the tool line cannot contradict the headline.
   A level with no limit found up to MAX_RADIUS_MM limits no tool.

Checked against parts with known answers: a 2.0 mm slot -> 1.95, a 3.2 mm slot
-> 3.15, a 6 x 4 mm sharp-cornered pocket -> 3.97, a 16 x 10 mm one -> 9.99
(its corners correctly not counted), all at a 0.05 mm cell.

Limits worth knowing: flat end mills only (ball and taper tips are treated by
their diameter), from the top only, and the precision is about one cell (shown
in the report).
"""

from __future__ import annotations

import json

#: Ids for the menu and its single item.
MENU_ID = "piccola-analysis-menu"
ITEM_ID = "piccola-analysis-mintool"

#: Target size of the analysis grid, in cells. Sets the cell size.
GRID_CELLS = 1_500_000

#: Smallest cell, mm: finer than this buys nothing a tool can use.
MIN_CELL_MM = 0.04

#: Largest tool radius searched, mm (so tools up to 2x this in diameter).
MAX_RADIUS_MM = 12.0

#: A flat area at least this big (mm2) is a floor the cutter must reach.
MIN_FLOOR_MM2 = 1.0

#: At most this many floor levels are analysed (the largest ones).
MAX_LEVELS = 16

#: Corner-sliver allowance: an unreachable patch next to cut area that reaches
#: no further than this many tool radii past the cutter is a corner sliver
#: (exactly 1.0 for a 60 degree corner, 0.41 for 90), not a tool that does not
#: fit. Slightly above 1 so 60 degree corners are tolerated.
CORNER_FRAC = 1.05

#: Leftover below this (mm2) is raster noise, not a problem.
NOISE_MM2 = 0.05


def analysis_js() -> str:
    """JS adding the Analysis menu and running the minimum tool analysis."""
    js = r"""
(function () {
  var MENU_ID = %(menu_id)s, ITEM_ID = %(item_id)s;
  var GRID_CELLS = %(grid)s, MIN_CELL = %(min_cell)s, MAX_R = %(max_r)s;
  var MIN_FLOOR = %(min_floor)s, MAX_LEVELS = %(max_levels)s;
  var CORNER_FRAC = %(corner)s, NOISE = %(noise)s;
  var INF = 1e20, NONE = -1e9;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  // ---- exact Euclidean distance transform (Felzenszwalb & Huttenlocher) ----
  // f: Float32Array, 0 at feature cells, INF elsewhere; transformed in place
  // into SQUARED distances (in cells) to the nearest feature cell.
  var _v, _z, _d, _g;
  function edt1(f, off, stride, n) {
    if (!_v || _v.length < n) {
      _v = new Int32Array(n); _z = new Float64Array(n + 1);
      _d = new Float64Array(n); _g = new Float64Array(n);
    }
    var i, k = 0, s, p;
    for (i = 0; i < n; i++) _g[i] = f[off + i * stride];
    _v[0] = 0; _z[0] = -INF; _z[1] = INF;
    for (var q = 1; q < n; q++) {
      p = _v[k];
      s = ((_g[q] + q * q) - (_g[p] + p * p)) / (2 * q - 2 * p);
      while (s <= _z[k]) {             // z[0] = -INF stops this at k = 0
        k--;
        p = _v[k];
        s = ((_g[q] + q * q) - (_g[p] + p * p)) / (2 * q - 2 * p);
      }
      k++; _v[k] = q; _z[k] = s; _z[k + 1] = INF;
    }
    k = 0;
    for (q = 0; q < n; q++) {
      while (_z[k + 1] < q) k++;
      var dq = q - _v[k];
      _d[q] = dq * dq + _g[_v[k]];
    }
    for (i = 0; i < n; i++) f[off + i * stride] = _d[i];
  }
  function edt(f, W, H) {
    for (var x = 0; x < W; x++) edt1(f, x, W, H);       // columns
    for (var y = 0; y < H; y++) edt1(f, y * W, 1, W);   // rows
    return f;
  }

  // ---- the model's top surface, as a height map over the padded stock ----
  function heightMap(a) {
    var conf = a.conf.get(), st = conf.stock, ws = a.widgets.all().filter(function (w) {
      // rotary (indexed) parts are not cut from the top; patch 0027 skips them too
      return w && w.track && !w.track.ignore && !w.track.indexed && !(w.meta && w.meta.disabled);
    });
    if (!ws.length) return null;
    var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    ws.forEach(function (w) {
      var b = w.getBoundingBox(), p = w.track.pos;
      minx = Math.min(minx, b.min.x + p.x); maxx = Math.max(maxx, b.max.x + p.x);
      miny = Math.min(miny, b.min.y + p.y); maxy = Math.max(maxy, b.max.y + p.y);
    });
    if (st && st.x > 0 && st.y > 0 && st.center) {
      minx = Math.min(minx, st.center.x - st.x / 2); maxx = Math.max(maxx, st.center.x + st.x / 2);
      miny = Math.min(miny, st.center.y - st.y / 2); maxy = Math.max(maxy, st.center.y + st.y / 2);
    }
    var pad = MAX_R + 1;
    var x0 = minx - pad, y0 = miny - pad, sx = maxx - minx + 2 * pad, sy = maxy - miny + 2 * pad;
    var c = Math.max(MIN_CELL, Math.sqrt(sx * sy / GRID_CELLS));
    var W = Math.ceil(sx / c), H = Math.ceil(sy / c);
    var hm = new Float32Array(W * H).fill(NONE), zmin = Infinity;
    ws.forEach(function (w) {
      var v = w.getGeoVertices({ translate: true, unroll: true }), t = w.track;
      var zb = (t.top || 0) - ((t.box && t.box.d) || 0);
      zmin = Math.min(zmin, w.getBoundingBox().min.z + zb);
      var ox = (t.pos.x - x0) / c - 0.5, oy = (t.pos.y - y0) / c - 0.5;
      for (var i = 0; i + 8 < v.length; i += 9) {
        var ax = v[i] / c + ox, ay = v[i + 1] / c + oy, az = v[i + 2] + zb;
        var bx = v[i + 3] / c + ox, by = v[i + 4] / c + oy, bz = v[i + 5] + zb;
        var cx = v[i + 6] / c + ox, cy = v[i + 7] / c + oy, cz = v[i + 8] + zb;
        var d = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay);
        if (Math.abs(d) < 1e-9) continue;
        var i0 = Math.max(0, Math.ceil(Math.min(ax, bx, cx))), i1 = Math.min(W - 1, Math.floor(Math.max(ax, bx, cx)));
        var j0 = Math.max(0, Math.ceil(Math.min(ay, by, cy))), j1 = Math.min(H - 1, Math.floor(Math.max(ay, by, cy)));
        for (var jy = j0; jy <= j1; jy++) for (var ix = i0; ix <= i1; ix++) {
          var l1 = ((bx - ix) * (cy - jy) - (cx - ix) * (by - jy)) / d;
          var l2 = ((cx - ix) * (ay - jy) - (ax - ix) * (cy - jy)) / d;
          var l3 = 1 - l1 - l2;
          if (l1 < -1e-6 || l2 < -1e-6 || l3 < -1e-6) continue;
          var z = l1 * az + l2 * bz + l3 * cz, k = jy * W + ix;
          if (z > hm[k]) hm[k] = z;
        }
      }
    });
    // the stock region (where leftover counts): inside the original box
    var si0 = Math.floor((minx - x0) / c), si1 = Math.ceil((maxx - x0) / c);
    var sj0 = Math.floor((miny - y0) / c), sj1 = Math.ceil((maxy - y0) / c);
    return { hm: hm, W: W, H: H, c: c, x0: x0, y0: y0, box: [si0, si1, sj0, sj1], zmin: zmin };
  }

  // floor heights: FLAT areas of the top surface, largest first. Only cells
  // level with all four neighbours count -- a rounded or sloped top otherwise
  // shows up as a stack of thin 0.02 mm bands, each passing for a "floor".
  function floors(m) {
    var hist = {}, cell2 = m.c * m.c, top = -Infinity, W = m.W, hm = m.hm;
    var tol = Math.max(0.004, m.c * 0.02);
    for (var k = W; k < hm.length - W; k++) {
      var h = hm[k]; if (h <= NONE) continue;
      if (h > top) top = h;
      if (Math.abs(hm[k - 1] - h) > tol || Math.abs(hm[k + 1] - h) > tol ||
          Math.abs(hm[k - W] - h) > tol || Math.abs(hm[k + W] - h) > tol) continue;
      var key = Math.round(h * 50) / 50;      // 0.02 mm bins
      hist[key] = (hist[key] || 0) + 1;
    }
    var out = Object.keys(hist).map(function (k) { return { z: +k, area: hist[k] * cell2 }; })
      .filter(function (f) { return f.area >= MIN_FLOOR && f.z < top - 0.05; });
    out.sort(function (a, b) { return b.area - a.area; });
    out = out.slice(0, MAX_LEVELS);
    // The bottom of the model is a level too, always: through-holes and the
    // outside contour have no floor of their own, but the cutter has to go
    // all the way down them, past every wall of the part.
    if (isFinite(m.zmin) && !out.some(function (f) { return Math.abs(f.z - m.zmin) < 0.05; })) {
      out.push({ z: m.zmin, area: 0, bottom: true });
    }
    out.sort(function (a, b) { return a.z - b.z; });
    return out;
  }

  // one level: free space = model below z + 0.02 (and everything off the model)
  function level(m, z) {
    var n = m.W * m.H, mat = new Uint8Array(n), f = new Float32Array(n), any = false;
    for (var k = 0; k < n; k++) {
      var solid = m.hm[k] > z + 0.02;
      mat[k] = solid ? 1 : 0;
      f[k] = solid ? 0 : INF;
      if (solid) any = true;
    }
    if (!any) return null;
    edt(f, m.W, m.H);
    for (k = 0; k < n; k++) f[k] = Math.sqrt(f[k]);      // distance to material, cells
    return { mat: mat, d: f };
  }

  // Free area a radius-rc cutter cannot reach that is NOT a corner sliver.
  //
  // The leftover is split into connected patches and each is judged whole:
  //  * a patch with no reached free cell next to it is enclosed -- a pocket
  //    the cutter cannot enter at all: real;
  //  * otherwise its DEPTH, how far it reaches beyond the area the cutter
  //    covers, decides. A corner sliver reaches at most about one radius past
  //    the cutter (exactly r for a 60 degree corner, 0.41 r for 90); a slot
  //    too narrow for the cutter goes its whole length deep: real when deeper
  //    than CORNER_FRAC x r.
  // Cells within 1.5 cells of a wall are raster jaggies and never count.
  var _reach, _lab, _queue;
  function leftover(m, L, rc) {
    var W = m.W, n = W * m.H;
    if (!_reach || _reach.length !== n) {
      _reach = new Float32Array(n); _lab = new Int32Array(n); _queue = new Int32Array(n);
    }
    var g = _reach, k;
    for (k = 0; k < n; k++) g[k] = (!L.mat[k] && L.d[k] >= rc) ? 0 : INF;
    edt(g, W, m.H);
    var rr = (rc + 0.5) * (rc + 0.5);
    var b = m.box, lab = _lab, q = _queue;
    lab.fill(0);
    function left(k) { return !L.mat[k] && g[k] > rr && L.d[k] > 1.5; }
    function reached(k) { return !L.mat[k] && g[k] <= rr; }
    var real = 0, best = null, id = 0, depthMax = CORNER_FRAC * rc + 1.5;
    for (var j = b[2]; j < b[3]; j++) for (var i = b[0]; i < b[1]; i++) {
      k = j * W + i;
      if (lab[k] || !left(k)) continue;
      id++;
      var head = 0, tail = 0, cnt = 0, sx = 0, sy = 0, deep = 0, open = false;
      q[tail++] = k; lab[k] = id;
      while (head < tail) {
        var p = q[head++], px = p %% W, py = (p - px) / W;
        cnt++; sx += px; sy += py;
        var dp = Math.sqrt(g[p]) - rc;          // past the cutter's reach
        if (dp > deep) deep = dp;
        var nb = [p - 1, p + 1, p - W, p + W];
        for (var t = 0; t < 4; t++) {
          var nk = nb[t];
          if (nk < 0 || nk >= n || (t < 2 && ((nk %% W) !== px + (t ? 1 : -1)))) continue;
          if (reached(nk)) open = true;
          else if (!lab[nk] && left(nk)) { lab[nk] = id; q[tail++] = nk; }
        }
      }
      if (open && deep <= depthMax) continue;     // corner sliver
      real += cnt;
      if (!best || cnt > best.cnt) best = { cnt: cnt, cx: sx / cnt, cy: sy / cnt };
    }
    var area = real * m.c * m.c;
    return { area: area, cx: best ? best.cx : 0, cy: best ? best.cy : 0 };
  }

  // Scan radii upward and stop at the first that does not fit -- the FIRST
  // failure is the answer, which a plain bisection could jump past -- then
  // narrow down between the last fit and that failure.
  function largestRadius(m, L) {
    var hi = MAX_R / m.c, ok = 0, fail = null, bad = null, r = 1;
    while (r <= hi) {
      var res = leftover(m, L, r);
      if (res.area >= NOISE) { fail = r; bad = res; break; }
      ok = r;
      // the last step may overshoot hi: test hi itself, or the radii between
      // the last step and it would never be checked
      r = ok < hi ? Math.min(hi, Math.max(r + 1, r * 1.3)) : hi + 1;
    }
    if (fail === null) return { rc: hi, bad: null };
    for (var it = 0; it < 6 && fail - ok > 0.5; it++) {
      var mid = (ok + fail) / 2, rm = leftover(m, L, mid);
      if (rm.area >= NOISE) { fail = mid; bad = rm; } else ok = mid;
    }
    return { rc: ok, bad: bad };
  }

  function toolsOf(a) {
    try {
      return (a.conf.get().tools || []).map(function (t) {
        return { name: t.name, number: t.number, type: t.type,
                 d: (t.flute_diam || 0) * (t.metric ? 1 : 25.4) };
      }).filter(function (t) { return t.d > 0; })
        .sort(function (x, y) { return (x.number || 0) - (y.number || 0); });
    } catch (e) { return []; }
  }

  function fmt(v, dp) { return (Math.round(v * Math.pow(10, dp)) / Math.pow(10, dp)).toFixed(dp); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, function (ch) { return ({'&':'&amp;','<':'&lt;','>':'&gt;'})[ch]; }); }

  // The answer and nothing else: the largest cutter that reaches every area,
  // where the narrowest spot is, and which library tools are small enough.
  function report(m, results, tools) {
    var worst = null;
    results.forEach(function (r) { if (!worst || r.d < worst.d) worst = r; });
    var html = ['<div style="min-width:380px;max-width:520px;font-size:13px;line-height:1.5;text-align:left">',
      '<div style="font-size:15px;font-weight:600;margin-bottom:6px">Minimum tool diameter</div>'];
    if (!worst) {
      html.push('<div>Nothing to limit the tool: the model has no floors below its top to reach.</div>');
    } else if (capped(m, worst)) {
      html.push('<div>Any flat cutter up to <b style="font-size:15px">&Oslash; ' + fmt(2 * MAX_R, 0) +
        ' mm</b> reaches every area (larger ones are not checked).</div>');
    } else {
      html.push('<div>Use a flat cutter of <b style="font-size:16px">&Oslash; ' + fmt(worst.d, 2) +
        ' mm</b> or smaller to reach every area.</div>');
      if (worst.bad && worst.bad.area > 0) {
        var x = m.x0 + (worst.bad.cx + 0.5) * m.c, y = m.y0 + (worst.bad.cy + 0.5) * m.c;
        html.push('<div>Narrowest spot near X ' + fmt(x, 1) + ', Y ' + fmt(y, 1) + ' at Z ' + fmt(worst.z, 2) + ' mm.</div>');
      }
    }
    if (tools.length && worst) {
      var fit = tools.filter(function (t) { return !t.fail.length; });
      var big = tools.filter(function (t) { return t.fail.length; });
      var names = function (list) {
        return list.map(function (t) { return 'T' + esc(t.number) + ' &Oslash;' + fmt(t.d, 2); }).join(', ');
      };
      html.push('<div style="margin-top:8px">Your tools: ' +
        (fit.length ? '<b style="color:#2f855a">fit ' + names(fit) + '</b>' : '<b style="color:#c53030">none fit</b>') +
        (big.length ? ' &middot; <span style="color:#c53030">too big ' + names(big) + '</span>' : '') + '</div>');
    }
    html.push('<div style="opacity:.6;margin-top:10px;font-size:12px">3-axis, from the top, flat end mills; ' +
      'sharp inside corners are not counted. Precision &plusmn;' + fmt(m.c, 2) + ' mm; gaps narrower than ' +
      // left() ignores cells within 1.5 cells of a wall, so a gap under ~3 cells never shows
      fmt(3 * m.c, 2) + ' mm are not detected.</div></div>');
    showDialog(html.join(''));
  }

  // a level the search found no limit on up to MAX_R: "at least", not a limit
  function capped(m, r) { return r.d >= 2 * MAX_R - m.c; }

  // The report in a dialog of its own. Kiri's alert puts the message inside a
  // <label> its stylesheet lays out on one line, which ran a multi-line report
  // with a table off the side of the box.
  var DLG_ID = 'piccola-analysis-dialog';
  // Kiri's stylesheet makes every <div> a horizontal flex row, which lays the
  // report's blocks side by side as columns. Normal flow, scoped to the dialog.
  function dialogStyle() {
    if (document.getElementById(DLG_ID + '-style')) return;
    var s = document.createElement('style');
    s.id = DLG_ID + '-style';
    var R = '#' + DLG_ID + ' ';
    s.textContent = [
      R + '.pa-box, ' + R + '.pa-box div { display: block !important; white-space: normal !important; }',
      R + '.pa-box table { display: table !important; border-collapse: collapse !important; width: 100%%; }',
      R + '.pa-box tr { display: table-row !important; }',
      R + '.pa-box td { display: table-cell !important; white-space: nowrap; vertical-align: top; }',
      R + '.pa-box .pa-buttons { display: flex !important; justify-content: flex-end; }'
    ].join('\n');
    document.head.appendChild(s);
  }

  function showDialog(html) {
    dialogStyle();
    var old = document.getElementById(DLG_ID);
    if (old) old.remove();
    var back = document.createElement('div');
    back.id = DLG_ID;
    back.setAttribute('style', 'position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.35);' +
      'display:flex;align-items:center;justify-content:center;');
    var box = document.createElement('div');
    box.className = 'pa-box';
    box.setAttribute('style', 'background:#fff;color:#222;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.35);' +
      'padding:18px 20px 14px;max-width:min(620px,92vw);max-height:84vh;overflow:auto;white-space:normal;' +
      'font-family:inherit;user-select:text;');
    box.innerHTML = html;
    var row = document.createElement('div');
    row.className = 'pa-buttons';
    row.setAttribute('style', 'margin-top:12px;');
    var ok = document.createElement('button');
    ok.textContent = 'OK';
    ok.setAttribute('style', 'padding:5px 18px;font-size:13px;cursor:pointer;');
    row.appendChild(ok);
    box.appendChild(row);
    back.appendChild(box);
    function close() { back.remove(); document.removeEventListener('keydown', onKey, true); }
    function onKey(ev) { if (ev.key === 'Escape' || ev.key === 'Enter') { ev.stopPropagation(); close(); } }
    ok.onclick = close;
    back.onclick = function (ev) { if (ev.target === back) close(); };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    ok.focus();
  }

  function run() {
    var a = api(); if (!a) return;
    if (!a.widgets.all().length) { showDialog('Load a model first, then run the analysis.'); return; }
    try { a.show.busy('analysing the model'); } catch (e) { }
    setTimeout(function () {
      var m, levels, results = [], tools = toolsOf(a), idx = 0;
      try {
        m = heightMap(a);
      } catch (e) { done(e); return; }
      if (!m) {
        try { a.show.busy(false); } catch (e) { }
        showDialog('Nothing to analyse: only rotary (indexed) or disabled parts are loaded. ' +
          'The analysis covers 3-axis parts cut from the top.');
        return;
      }
      try { levels = floors(m); } catch (e) { done(e); return; }
      function step() {
        if (idx >= levels.length) return toolsPass();
        try {
          var L = level(m, levels[idx].z);
          if (L) {
            var r = largestRadius(m, L);
            results.push({ z: levels[idx].z, d: 2 * r.rc * m.c, bad: r.bad });
          }
        } catch (e) { return done(e); }
        idx++;
        setTimeout(step, 0);
      }
      // each tool is judged against the levels' answers, so the table can
      // never contradict the headline number
      function toolsPass() {
        // a capped level limits nothing: its d is where the search stopped
        tools.forEach(function (t) {
          t.fail = results.filter(function (r) { return !capped(m, r) && t.d > r.d + m.c; });
        });
        done(null);
      }
      function done(err) {
        try { a.show.busy(false); } catch (e) { }
        if (err) { showDialog('The analysis failed: ' + esc(err && err.message || err)); return; }
        report(m, results, tools);
      }
      step();
    }, 50);
  }
  window.__piccolaMinTool = run;     // for testing

  // ---- the menu, built like Kiri's own (init/menu.js topMenu / menuItem) ----
  function install() {
    if (document.getElementById(MENU_ID)) return true;
    var render = document.querySelector('label[lk="re_menu"]');
    var host = render && render.parentElement;          // the Render <span>
    if (!host || !host.parentElement) return false;
    var span = document.createElement('span');
    span.id = MENU_ID;
    var lab = document.createElement('label');
    lab.textContent = 'analysis';
    span.appendChild(lab);
    var drop = document.createElement('div');
    drop.className = 'top-menu-drop top-menu-left';
    var content = document.createElement('div');
    content.className = 'content';
    var item = document.createElement('div');
    item.id = ITEM_ID;
    item.title = 'The largest flat cutter that can still reach every area of the model';
    var il = document.createElement('label');
    il.textContent = 'minimum tool diameter';
    var ic = document.createElement('span');
    ic.innerHTML = '<i class="fas fa-ruler-combined"></i>';
    item.appendChild(il); item.appendChild(ic);
    item.onclick = function (ev) { ev.stopPropagation(); run(); };
    content.appendChild(item);
    drop.appendChild(content);
    span.appendChild(drop);
    host.parentElement.insertBefore(span, host.nextSibling);
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "menu_id": json.dumps(MENU_ID),
        "item_id": json.dumps(ITEM_ID),
        "grid": json.dumps(GRID_CELLS),
        "min_cell": json.dumps(MIN_CELL_MM),
        "max_r": json.dumps(MAX_RADIUS_MM),
        "min_floor": json.dumps(MIN_FLOOR_MM2),
        "max_levels": json.dumps(MAX_LEVELS),
        "corner": json.dumps(CORNER_FRAC),
        "noise": json.dumps(NOISE_MM2),
    }
    return js
