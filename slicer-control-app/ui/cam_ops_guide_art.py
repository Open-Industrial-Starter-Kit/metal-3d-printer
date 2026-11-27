"""
ui/cam_ops_guide_art.py
Illustrations for the Operations guide (ui/cam_ops_guide.py): the settings that
are easier to see than to read about.

Each is a self-contained SVG string, drawn from numbers rather than by eye, in
the op diagrams' palette (ui/cam_op_art.py): blue for the cutting path, green
for moves in air, greys for the part, amber for what goes wrong.

* ``step-angle``  -- GT2 teeth unrolled, the toolpath at 1, 6 and 18 degrees.
* ``linear-spiral`` -- the same toothed bar, lines along it vs a helix round it.
* ``ramp``        -- entering a layer: straight plunge vs a slope.
* ``step-down``   -- a finish split into equal passes down to the profile.
* ``gt2-gap``     -- GT2 tooth gaps to scale: a 1 mm ball fits, a 3 mm flat does not.
* ``stick-out``   -- short vs long cutter stick-out.
* ``small-endmill`` -- the 1 mm ball finishing GT2 teeth, with the WD-40 drip.
"""

from __future__ import annotations

import math

_CUT = "#0a7fd4"
_AIR = "#2f9e44"
_BAD = "#d9480f"
_PART = "#c9ced4"
_PART_EDGE = "#6f7782"
_STOCK = "#eef1f4"
_TXT = "#2b3138"
_NOTE = "#6b7280"
_OIL = "#e0a82e"         # WD-40 drops
_FONT = "font-family=\"system-ui, -apple-system, 'Segoe UI', sans-serif\""


def _t(x, y, s, size=12, color=_TXT, weight="400", anchor="start"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" {_FONT}>{s}</text>')


def _poly(pts, color, w=2.0, fill="none", dash=None, extra=""):
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<polyline points="{d}" fill="{fill}" stroke="{color}" stroke-width="{w}"'
            f' stroke-linejoin="round" stroke-linecap="round"{da}{extra}/>')


def _svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="100%" style="max-width:{w}px;display:block;margin:6px auto">{body}</svg>')


# ── Step Angle: GT2 teeth, unrolled ──────────────────────────────────────────

_PITCH = 9.0          # degrees per tooth, 40 teeth
_GAP_HALF = 2.7       # half the gap width, degrees
_GAP_CENTRE = 4.5     # first gap centre


def _gap_depth(a, half=_GAP_HALF):
    """0 at the tips, 1 at the bottom of a gap (a rounded gap)."""
    d = ((a - _GAP_CENTRE + _PITCH / 2) % _PITCH) - _PITCH / 2
    if abs(d) >= half:
        return 0.0
    return math.sqrt(1.0 - (d / half) ** 2)


#: Where the ball's centre can go down in a gap: narrower than the gap itself
#: (a 1 mm ball cannot reach into its corners).
_REACH_HALF = 1.2


def step_angle():
    W, H = 640, 316
    x0, x1, a1 = 150.0, 620.0, 36.0
    k = (x1 - x0) / a1
    D = 26.0
    rows = [
        ("1°", 1.0, 0.0, "follows every gap", _CUT, ""),
        ("6°", 6.0, 1.0, "dives too wide: cuts into", _BAD, "the teeth, misses gaps"),
        ("18°", 18.0, 0.0, "never reaches the gaps,", _BAD, "or cuts the teeth"),
    ]
    out = [_t(W / 2, 18, "Step Angle on GT2 teeth (unrolled: one gap every 9°)",
              13, weight="600", anchor="middle")]
    for i, (name, step, phase, note, col, note2) in enumerate(rows):
        top = 52 + i * 84
        surf = lambda a: top + D * _gap_depth(a)
        prof = [(x0 + j * 0.1 * k, surf(j * 0.1)) for j in range(int(a1 * 10) + 1)]
        out.append(_poly(prof + [(x1, top + D + 14), (x0, top + D + 14)], _PART_EDGE, 1.0,
                         fill=_PART))
        # a point every Step Angle, as deep as the ball's centre can go there;
        # the machine moves in a straight line from one to the next
        samples = []
        a = phase - step
        while a <= a1 + step + 1e-9:
            samples.append((a, top + D * _gap_depth(a, _REACH_HALF) - 2.5))
            a += step
        def path_y(a):
            for (pa, py), (qa, qy) in zip(samples, samples[1:]):
                if pa <= a <= qa:
                    return py + (qy - py) * (a - pa) / (qa - pa)
            return None
        # where the straight line runs below the part's surface: cut into it
        run = []
        for j in range(int(a1 * 20) + 1):
            aa = j * 0.05
            py = path_y(aa)
            if py is not None and py > surf(aa) + 1.0:
                run.append((aa, py))
            elif run:
                if len(run) > 2:
                    pts = [(x0 + q * k, y) for q, y in run] +                           [(x0 + q * k, surf(q)) for q, _ in reversed(run)]
                    out.append(_poly(pts, _BAD, 0.6, fill=_BAD, extra=' fill-opacity="0.55"'))
                run = []
        if step > 1:
            for g in range(4):
                gc = _GAP_CENTRE + g * _PITCH
                if all(path_y(gc + d) < top + 2 for d in (-1.0, 0.0, 1.0)):
                    cx = x0 + (_GAP_CENTRE + g * _PITCH) * k
                    out.append(f'<circle cx="{cx:.1f}" cy="{top + D * 0.55:.1f}" r="11" '
                               f'fill="none" stroke="{_BAD}" stroke-width="1.6" stroke-dasharray="3 2"/>')
        out.append(_poly([(x0 + j * 0.05 * k, path_y(j * 0.05)) for j in range(int(a1 * 20) + 1)],
                         _CUT, 2.0))
        if step > 1:
            for (sa, sy) in samples:
                if 0 <= sa <= a1:
                    out.append(f'<circle cx="{x0 + sa * k:.1f}" cy="{sy:.1f}" r="3" fill="{_CUT}"/>')
        out.append(_t(14, top + 10, f"Step Angle {name}", 13, weight="700"))
        out.append(_t(14, top + 28, note, 11.5, color=col))
        if note2:
            out.append(_t(14, top + 43, note2, 11.5, color=col))
    out.append(_t(W / 2, H - 22, "blue = toolpath: a point every Step Angle, straight lines between",
                  11, _NOTE, anchor="middle"))
    out.append(_t(W / 2, H - 7, "red = cut into the teeth;  dashed circle = gap never cut",
                  11, _NOTE, anchor="middle"))
    return _svg(W, H, "".join(out))


# ── Linear vs Spiral on a toothed bar ────────────────────────────────────────

def _bar(cx, cy, L, R, grooves=True):
    out = [f'<rect x="{cx - L / 2:.1f}" y="{cy - R:.1f}" width="{L:.1f}" height="{2 * R:.1f}" '
           f'fill="url(#gl-bar)" stroke="{_PART_EDGE}" stroke-width="1"/>',
           f'<ellipse cx="{cx + L / 2:.1f}" cy="{cy:.1f}" rx="{R * 0.28:.1f}" ry="{R:.1f}" '
           f'fill="#b9c0c8" stroke="{_PART_EDGE}" stroke-width="1"/>']
    if grooves:
        # axial tooth gaps, seen on the front half of the bar
        for j in range(-8, 9):
            phi = j * math.pi / 18
            y = cy + R * math.sin(phi)
            out.append(f'<line x1="{cx - L / 2:.1f}" y1="{y:.1f}" x2="{cx + L / 2:.1f}" '
                       f'y2="{y:.1f}" stroke="#9aa3ad" stroke-width="{1.6 * math.cos(phi) + 0.3:.2f}"/>')
    return "".join(out)


def linear_spiral():
    W, H = 640, 260
    L, R = 220.0, 62.0
    out = ['<defs><linearGradient id="gl-bar" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0" stop-color="#e9ecef"/><stop offset="0.45" stop-color="#d4d9de"/>'
           '<stop offset="1" stop-color="#a9b1ba"/></linearGradient></defs>']
    # linear
    cx, cy = 165.0, 120.0
    out.append(_bar(cx, cy, L, R))
    for j in range(-7, 8, 2):
        phi = j * math.pi / 18 + math.pi / 36
        y = cy + R * math.sin(phi)
        out.append(_poly([(cx - L / 2 + 6, y), (cx + L / 2 - 6, y)], _CUT, 2.2))
    out.append(_t(cx, 24, "Linear", 15, weight="700", anchor="middle"))
    out.append(_t(cx, 214, "lines run ALONG the bar,", 12, anchor="middle"))
    out.append(_t(cx, 230, "each one inside a tooth gap", 12, anchor="middle"))
    out.append(_t(cx, 248, "best for teeth, splines, flutes, flats", 11.5, _CUT, "600", "middle"))
    # spiral
    cx = 470.0
    out.append(_bar(cx, cy, L, R))
    turns, n = 5, 400
    seg = []
    for i in range(n + 1):
        t = i / n
        phi = t * turns * 2 * math.pi
        x = cx - L / 2 + 8 + t * (L - 16)
        if math.cos(phi) > 0:          # front half only
            # the tool rides the tips and drops into each gap it crosses
            dip = 6.0 * max(0.0, math.cos(phi * 18)) ** 8
            seg.append((x, cy - R * math.sin(phi) + dip * math.cos(phi)))
        elif len(seg) > 1:
            out.append(_poly(seg, _CUT, 2.0))
            seg = []
        else:
            seg = []
    if len(seg) > 1:
        out.append(_poly(seg, _CUT, 2.0))
    out.append(_t(cx, 24, "Spiral", 15, weight="700", anchor="middle"))
    out.append(_t(cx, 214, "one helix AROUND the bar: on teeth", 12, anchor="middle"))
    out.append(_t(cx, 230, "it dives into every gap it crosses", 12, anchor="middle"))
    out.append(_t(cx, 248, "best for round shapes: cylinders, shoulders", 11.5, _CUT, "600", "middle"))
    return _svg(W, H, "".join(out))


# ── Ramp vs plunge ───────────────────────────────────────────────────────────

def _tool(x, y, h=46, w=14):
    return (f'<rect x="{x - w / 2:.1f}" y="{y - h:.1f}" width="{w}" height="{h - w / 2:.1f}" '
            f'fill="#4a525b"/><circle cx="{x:.1f}" cy="{y - w / 2:.1f}" r="{w / 2}" fill="#4a525b"/>')


def ramp():
    W, H = 640, 260
    out = []
    for i, (title, good) in enumerate((("Plunge (Ramp off)", False), ("Ramp on", True))):
        ox = 20 + i * 315
        top, floor_prev, floor_new = 92.0, 130.0, 182.0
        out.append(f'<rect x="{ox:.1f}" y="{floor_prev:.1f}" width="290" height="{232 - floor_prev:.1f}" '
                   f'fill="{_PART}" stroke="{_PART_EDGE}" stroke-width="1"/>')
        out.append(f'<rect x="{ox:.1f}" y="{floor_prev:.1f}" width="290" height="{floor_new - floor_prev:.1f}" '
                   f'fill="#dde1e6" stroke="none"/>')
        out.append(_t(ox + 286, floor_prev - 4, "previous layer", 10.5, _NOTE, anchor="end"))
        out.append(_t(ox + 286, floor_new + 16, "new layer", 10.5, _NOTE, anchor="end"))
        out.append(f'<line x1="{ox:.1f}" y1="{floor_new:.1f}" x2="{ox + 290:.1f}" y2="{floor_new:.1f}" '
                   f'stroke="{_PART_EDGE}" stroke-dasharray="4 3" stroke-width="1"/>')
        sx = ox + 40
        if not good:
            out.append(_poly([(sx, top - 30), (sx, floor_prev)], _AIR, 2.0, dash="5 3"))
            out.append(_poly([(sx, floor_prev), (sx, floor_new)], _BAD, 3.0))
            out.append(_poly([(sx, floor_new), (ox + 275, floor_new)], _CUT, 2.4))
            out.append(_tool(sx, floor_new))
            out.append(_t(sx + 16, floor_prev + 20, "straight down into the stock:", 11.5, _BAD))
            out.append(_t(sx + 16, floor_prev + 35, "the full step hits the tip at once", 11.5, _BAD))
        else:
            start = floor_prev - 14
            run = 170.0
            out.append(_poly([(sx, top - 30), (sx, start)], _AIR, 2.0, dash="5 3"))
            out.append(_poly([(sx, start), (sx + run, floor_new)], _CUT, 2.4))
            out.append(_poly([(sx + run, floor_new), (ox + 275, floor_new)], _CUT, 2.4))
            out.append(_tool(sx + run * 0.78, start + (floor_new - start) * 0.78))
            out.append(_t(sx + 8, start - 4, "starts 0.5 mm above", 11, _NOTE))
            out.append(_t(sx + 4, floor_new + 16, "slopes down at the Ramp Angle", 11.5, _CUT))
            out.append(_t(sx + 4, floor_new + 31, "while the bar turns", 11.5, _CUT))
        out.append(_t(ox + 145, 22, title, 14, weight="700", anchor="middle",
                      color=_BAD if not good else _CUT))
    out.append(_t(W / 2, H - 6, "green dashed = move in air;  blue = cutting", 11, _NOTE, anchor="middle"))
    return _svg(W, H, "".join(out))


# ── Equal step-down passes ───────────────────────────────────────────────────

def step_down():
    W, H = 640, 230
    x0, x1 = 60.0, 600.0
    top, depth = 60.0, 120.0           # 0.815 mm drawn as 120 px

    def prof(x):
        # two tooth gaps between tips at the top
        u = (x - x0) / (x1 - x0)
        g = max(0.0, math.cos((u - 0.25) * math.pi * 4)) ** 1.5 if 0.0 < u < 1.0 else 0.0
        return top + depth * g

    xs = [x0 + (x1 - x0) * i / 200 for i in range(201)]
    out = [_poly([(x, prof(x)) for x in xs] + [(x1, 200), (x0, 200)], _PART_EDGE, 1.0, fill=_PART)]
    out.append(f'<line x1="{x0:.1f}" y1="{top:.1f}" x2="{x1:.1f}" y2="{top:.1f}" stroke="{_PART_EDGE}" '
               f'stroke-dasharray="4 3"/>')
    passes = 5
    each = depth / passes
    for k in range(passes - 1, -1, -1):
        pts = [(x, max(top, prof(x) - k * each) - 2) for x in xs]
        out.append(_poly(pts, _CUT, 2.0 if k else 2.6))
    # dimension: one pass
    gx = x0 + (x1 - x0) * 0.25
    for k in range(passes + 1):
        y = top + k * each
        out.append(f'<line x1="{x0 - 14:.1f}" y1="{y:.1f}" x2="{x0 - 4:.1f}" y2="{y:.1f}" stroke="{_TXT}"/>')
    out.append(_t(x0 + (x1 - x0) * 0.5, top + depth * 0.62, "each pass the same depth,", 11.5,
                  anchor="middle"))
    out.append(_t(x0 + (x1 - x0) * 0.5, top + depth * 0.62 + 15, "never more than Step Down",
                  11.5, anchor="middle"))
    out.append(_t(x0, 34, "top of the part (+ Expand)", 11.5, _NOTE))
    out.append(_t(x1, 34, "0.815 mm at Step Down 0.2 = 5 passes of 0.163", 11.5, _NOTE, anchor="end"))
    out.append(_t(W / 2, 222, "blue = one pass each; the last one is the finished profile",
                  11, _NOTE, anchor="middle"))
    return _svg(W, H, "".join(out))


# ── GT2: the tooth gap, to scale, with the cutters that do and do not fit ────

#: GT2 pulley groove, mm: 2 mm pitch, 0.75 mm deep, bottom radius 0.555,
#: about 1.1 mm wide at the tips. 40 teeth -> pitch diameter 25.46, outside
#: 24.95, which is why it is cut from a 25 mm bar.
_GT2_PITCH = 2.0
_GT2_DEPTH = 0.75
_GT2_R = 0.555
_GT2_TOP_HALF = 0.55


def _gt2_gap(u):
    """Depth (mm, 0 at the tips) of the groove at u mm from its centre."""
    u = abs(u)
    if u >= _GT2_TOP_HALF:
        return 0.0
    # round bottom of radius _GT2_R, flanks straight up to the tips
    bottom_y = _GT2_DEPTH - _GT2_R           # centre of the bottom arc, below the tips
    if u <= _GT2_R * 0.9:
        y = bottom_y + math.sqrt(max(0.0, _GT2_R ** 2 - u ** 2))
    else:
        y = bottom_y + math.sqrt(max(0.0, _GT2_R ** 2 - (_GT2_R * 0.9) ** 2))
        y *= (_GT2_TOP_HALF - u) / (_GT2_TOP_HALF - _GT2_R * 0.9)
    return min(_GT2_DEPTH, y)


def gt2_gap():
    W, H = 640, 330
    s = 92.0                       # px per mm
    top = 150.0
    shank_top = 62.0               # below the titles
    out = [_t(W / 2, 20, "GT2 tooth gaps, to scale (2 mm pitch, 0.75 mm deep)", 13,
              weight="600", anchor="middle")]
    for i, (title, dia, ok) in enumerate((("ball 1.0 mm", 1.0, True), ("flat 3 mm", 3.0, False))):
        ox = 20 + i * 315
        cx = ox + 145.0
        x0, x1 = ox, ox + 290.0
        n = 120
        prof = []
        for j in range(n + 1):
            x = x0 + (x1 - x0) * j / n
            u = ((x - cx) / s + _GT2_PITCH / 2) % _GT2_PITCH - _GT2_PITCH / 2
            prof.append((x, top + s * _gt2_gap(u)))
        out.append(_poly(prof + [(x1, top + s * 1.25), (x0, top + s * 1.25)], _PART_EDGE, 1.0,
                         fill=_PART))
        r = dia / 2 * s
        if ok:
            # ball sitting in the bottom of the gap: centre one radius above it
            by = top + s * _GT2_DEPTH - r
            out.append(f'<rect x="{cx - r:.1f}" y="{shank_top:.1f}" width="{2 * r:.1f}" '
                       f'height="{by - shank_top:.1f}" fill="#4a525b"/>')
            out.append(f'<circle cx="{cx:.1f}" cy="{by:.1f}" r="{r:.1f}" fill="#4a525b"/>')
            out.append(_t(cx, top + s * 1.25 + 22, "fits the round bottom of the gap", 11.5,
                          _CUT, "600", "middle"))
        else:
            # a 3 mm flat end can only reach the tips: wider than a whole tooth
            out.append(f'<rect x="{cx - r:.1f}" y="{shank_top:.1f}" width="{2 * r:.1f}" '
                       f'height="{top - 2 - shank_top:.1f}" fill="#4a525b" fill-opacity="0.85"/>')
            out.append(f'<line x1="{cx - r:.1f}" y1="{top - 1:.1f}" x2="{cx + r:.1f}" y2="{top - 1:.1f}" '
                       f'stroke="{_BAD}" stroke-width="3"/>')
            out.append(_t(cx, top + s * 1.25 + 22, "wider than a whole tooth:", 11.5,
                          _BAD, "600", "middle"))
            out.append(_t(cx, top + s * 1.25 + 37, "rough the bar with it, not the teeth", 11.5,
                          _BAD, anchor="middle"))
        out.append(_t(cx, 46, title, 14, weight="700", anchor="middle",
                      color=_CUT if ok else _BAD))
    out.append(_t(W / 2, H - 6, "40 teeth: pitch Ø 25.46 mm, outside Ø 24.95 mm, from a 25 mm bar",
                  11, _NOTE, anchor="middle"))
    return _svg(W, H, "".join(out))


# ── Small cutters: stick-out ─────────────────────────────────────────────────

def stick_out():
    W, H = 640, 270
    out = []
    for i, (title, out_mm, ok) in enumerate((("Short stick-out", 6.0, True),
                                              ("Long stick-out", 16.0, False))):
        ox = 20 + i * 315
        cx = ox + 145.0
        s = 7.0                                    # px per mm
        nose = 68.0                                # collet nut bottom
        out.append(f'<rect x="{cx - 46:.1f}" y="{nose - 30:.1f}" width="92" height="30" rx="4" '
                   f'fill="#8a939d"/>')
        out.append(_t(cx + 52, nose - 10, "collet", 10.5, _NOTE))
        # 3 mm shank, tapering to a 1 mm tip over the last 3 mm
        L = out_mm * s
        tip = nose + L
        shank_r, tip_r = 1.5 * s, 0.5 * s
        out.append(_poly([(cx - shank_r, nose), (cx - shank_r, tip - 3 * s), (cx - tip_r, tip - 0.5 * s),
                          (cx + tip_r, tip - 0.5 * s), (cx + shank_r, tip - 3 * s), (cx + shank_r, nose)],
                         "#4a525b", 1.0, fill="#4a525b"))
        out.append(f'<circle cx="{cx:.1f}" cy="{tip - 0.5 * s:.1f}" r="{tip_r:.1f}" fill="#4a525b"/>')
        out.append(f'<rect x="{ox:.1f}" y="{tip:.1f}" width="290" height="{max(18.0, 214 - tip):.1f}" '
                   f'fill="{_PART}" stroke="{_PART_EDGE}" stroke-width="1"/>')
        # dimension
        dx = cx - 40
        out.append(f'<line x1="{dx:.1f}" y1="{nose:.1f}" x2="{dx:.1f}" y2="{tip:.1f}" stroke="{_TXT}" '
                   f'stroke-width="1"/>')
        out.append(_t(dx - 6, (nose + tip) / 2 + 4, f"{out_mm:.0f} mm", 11, anchor="end"))
        if not ok:
            # the tip deflects: drawn as a bent ghost
            out.append(_poly([(cx, tip - 3 * s), (cx + 9, tip - 2)], _BAD, 2.0, dash="4 3"))
        out.append(_t(cx, 240, "stiff: the tip goes where it is sent" if ok
                      else "bends and chatters: the tip snaps", 11.5, _CUT if ok else _BAD, "600",
                      "middle"))
        out.append(_t(cx, 18, title, 14, weight="700", anchor="middle", color=_CUT if ok else _BAD))
    out.append(_t(W / 2, H - 4, "stiffness falls with the cube of the stick-out: twice as long, 8x as bendy",
                  11, _NOTE, anchor="middle"))
    return _svg(W, H, "".join(out))


# ── Finishing GT2 teeth with the 1 mm ball (drawn from a workshop photo) ─────

def small_endmill():
    W, H = 640, 342
    s = 9.0                                        # px per mm (the cutter)
    cx = 360.0                                     # cutter axis
    cy, R = 232.0, 56.0                            # bar axis and radius (not to scale)
    R2 = 70.0                                      # shoulder, left of the teeth
    bar_top = cy - R
    out = ['<defs><linearGradient id="gl-sm-bar" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0" stop-color="#e9ecef"/><stop offset="0.45" stop-color="#d4d9de"/>'
           '<stop offset="1" stop-color="#a9b1ba"/></linearGradient>'
           f'<marker id="gl-sm-arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
           f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
           f'fill="{_NOTE}"/></marker>'
           f'<marker id="gl-sm-cut" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
           f'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{_CUT}"/></marker>'
           '</defs>']
    # the bar: shoulder on the left, toothed pulley section, end face on the right
    x_sh0, x_sh1, x_end = 20.0, 200.0, 560.0
    out.append(f'<rect x="{x_sh0:.1f}" y="{cy - R2:.1f}" width="{x_sh1 - x_sh0:.1f}" '
               f'height="{2 * R2:.1f}" fill="url(#gl-sm-bar)" stroke="{_PART_EDGE}" stroke-width="1"/>')
    out.append(f'<ellipse cx="{x_sh1:.1f}" cy="{cy:.1f}" rx="{R2 * 0.18:.1f}" ry="{R2:.1f}" '
               f'fill="#b9c0c8" stroke="{_PART_EDGE}" stroke-width="1"/>')
    out.append(f'<rect x="{x_sh1:.1f}" y="{bar_top:.1f}" width="{x_end - x_sh1:.1f}" '
               f'height="{2 * R:.1f}" fill="url(#gl-sm-bar)" stroke="{_PART_EDGE}" stroke-width="1"/>')
    # tooth gaps along the bar, cut only up to the cutter so far
    for j in range(-8, 9):
        phi = j * math.pi / 18
        y = cy + R * math.sin(phi)
        if y < bar_top + 2:
            continue
        out.append(f'<line x1="{x_sh1 + 4:.1f}" y1="{y:.1f}" x2="{cx:.1f}" y2="{y:.1f}" '
                   f'stroke="#9aa3ad" stroke-width="{1.6 * math.cos(phi) + 0.3:.2f}"/>')
    out.append(f'<ellipse cx="{x_end:.1f}" cy="{cy:.1f}" rx="{R * 0.22:.1f}" ry="{R:.1f}" '
               f'fill="#b9c0c8" stroke="{_PART_EDGE}" stroke-width="1"/>')
    # A turns the bar
    ex = x_end + R * 0.22 + 14
    out.append(f'<path d="M{ex:.1f},{cy + 40:.1f} A 22 60 0 0 0 {ex:.1f},{cy - 40:.1f}" fill="none" '
               f'stroke="{_NOTE}" stroke-width="1.6" marker-end="url(#gl-sm-arr)"/>')
    out.append(_t(ex + 6, cy + 4, "A", 13, _NOTE, "700"))
    # the pass along the gap the ball is in
    out.append(f'<line x1="{x_sh1 + 6:.1f}" y1="{bar_top + 1:.1f}" x2="{cx - 6:.1f}" y2="{bar_top + 1:.1f}" '
               f'stroke="{_CUT}" stroke-width="2.4"/>')
    out.append(f'<line x1="{cx + 8:.1f}" y1="{bar_top + 1:.1f}" x2="{cx + 60:.1f}" y2="{bar_top + 1:.1f}" '
               f'stroke="{_CUT}" stroke-width="2.4" stroke-dasharray="5 3" marker-end="url(#gl-sm-cut)"/>')
    # spindle nose and collet nut
    out.append(f'<rect x="{cx - 95:.1f}" y="-10" width="190" height="52" rx="6" fill="#8a939d"/>')
    out.append(f'<rect x="{cx - 34:.1f}" y="42" width="68" height="22" rx="3" fill="#a3abb4" '
               f'stroke="{_PART_EDGE}" stroke-width="1"/>')
    # cutter: 3 mm shank, tapering to the 1 mm ball, short stick-out
    nose = 64.0
    tip = bar_top + 0.5 * s                        # ball centre half a mm into the gap
    shank_r, tip_r = 1.5 * s, 0.5 * s
    taper_at = tip - 4.5 * s
    out.append(_poly([(cx - shank_r, nose), (cx - shank_r, taper_at), (cx - tip_r, tip),
                      (cx + tip_r, tip), (cx + shank_r, taper_at), (cx + shank_r, nose)],
                     "#4a525b", 1.0, fill="#4a525b"))
    out.append(f'<circle cx="{cx:.1f}" cy="{tip:.1f}" r="{tip_r:.1f}" fill="#4a525b"/>')
    # WD-40 drip (PiccolaCNC add-on): reservoir on the left, a tube down to a
    # needle beside the cutter, drops falling onto the cut
    rx, ry, rw, rh = 92.0, 62.0, 46.0, 70.0
    out.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" rx="8" '
               f'fill="#f4f6f8" stroke="{_PART_EDGE}" stroke-width="1.2"/>')
    out.append(f'<rect x="{rx + 3:.1f}" y="{ry + rh * 0.35:.1f}" width="{rw - 6:.1f}" '
               f'height="{rh * 0.65 - 3:.1f}" rx="5" fill="{_OIL}" fill-opacity="0.75"/>')
    out.append(f'<rect x="{rx + rw / 2 - 7:.1f}" y="{ry - 9:.1f}" width="14" height="10" rx="2" '
               f'fill="#8a939d"/>')
    nx, ny = cx - 26, bar_top - 22                 # needle tip
    out.append(f'<path d="M{rx + rw / 2:.1f},{ry + rh:.1f} C{rx + rw / 2:.1f},{ry + rh + 40:.1f} '
               f'{nx - 70:.1f},{ny - 50:.1f} {nx - 12:.1f},{ny - 12:.1f}" fill="none" '
               f'stroke="#8a939d" stroke-width="4" stroke-linecap="round"/>')
    out.append(f'<line x1="{nx - 12:.1f}" y1="{ny - 12:.1f}" x2="{nx:.1f}" y2="{ny:.1f}" '
               f'stroke="{_PART_EDGE}" stroke-width="2" stroke-linecap="round"/>')
    for k, (dx, dy) in enumerate(((6, 7), (12, 15))):
        x, y = nx + dx, ny + dy
        out.append(f'<path d="M{x:.1f},{y - 5:.1f} q4,5 0,7.5 q-4,-2.5 0,-7.5 z" fill="{_OIL}"/>')
    out.append(f'<ellipse cx="{cx - 9:.1f}" cy="{bar_top + 1:.1f}" rx="9" ry="2.6" fill="{_OIL}" '
               f'fill-opacity="0.8"/>')
    # chips: on the bar ahead of the cutter, and flung off
    chips = [(cx + 20, bar_top - 4), (cx + 30, bar_top - 14), (cx + 44, bar_top - 6),
             (cx + 26, bar_top - 30), (cx + 48, bar_top - 24), (cx + 66, bar_top - 36),
             (cx + 72, bar_top - 12), (cx + 90, bar_top - 28)]
    for k, (x, y) in enumerate(chips):
        a = (k * 47) % 180
        out.append(f'<path d="M{x - 3:.1f},{y:.1f} q3,-4 6,0" fill="none" stroke="{_PART_EDGE}" '
                   f'stroke-width="1.6" stroke-linecap="round" transform="rotate({a} {x:.1f} {y:.1f})"/>')

    # labels
    def lead(x0, y0, x1, y1, color=_NOTE):
        return (f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="{color}" '
                f'stroke-width="1"/>')
    out.append(lead(cx + shank_r + 2, (nose + taper_at) / 2, cx + 70, 84))
    out.append(_t(cx + 74, 80, "short stick-out:", 11.5, _TXT, "600"))
    out.append(_t(cx + 74, 95, "clamped high in the collet", 11.5, _NOTE))
    out.append(lead(cx + tip_r + 1, tip - 2, cx + 112, bar_top - 22, _BAD))
    out.append(_t(cx + 116, bar_top - 32, "1 mm ball: thinner", 11.5, _BAD, "600"))
    out.append(_t(cx + 116, bar_top - 17, "than a pencil lead", 11.5, _BAD, "600"))
    out.append(_t(rx + rw + 10, ry + 14, "WD-40 drip", 11.5, _TXT, "600"))
    out.append(_t(rx + rw + 10, ry + 29, "(PiccolaCNC add-on): a drop", 11.5, _NOTE))
    out.append(_t(rx + rw + 10, ry + 44, "at the tip keeps aluminium", 11.5, _NOTE))
    out.append(_t(rx + rw + 10, ry + 59, "from sticking to the ball", 11.5, _NOTE))
    out.append(_t((x_sh1 + cx) / 2, cy + R + 16, "finished gaps", 10.5, _NOTE, anchor="middle"))
    out.append(_t((cx + x_end) / 2, cy + R + 16, "still to cut", 10.5, _NOTE, anchor="middle"))
    out.append(_t(x_sh0 + 8, cy + 4, "shoulder", 10.5, _NOTE))
    out.append(_t(W / 2, H - 8, "chips packed into a tooth gap get cut again and snap the tip", 11.5,
                  _BAD, anchor="middle"))
    return _svg(W, H, "".join(out))


#: name -> SVG markup, as the guide's {"svg": name} blocks refer to them.
GUIDE_ART = {
    "step-angle": step_angle(),
    "linear-spiral": linear_spiral(),
    "ramp": ramp(),
    "step-down": step_down(),
    "gt2-gap": gt2_gap(),
    "stick-out": stick_out(),
    "small-endmill": small_endmill(),
}
