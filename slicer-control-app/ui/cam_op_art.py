"""
ui/cam_op_art.py
A diagram per CAM operation, for the hover panel.

Why a drawing and not a render
------------------------------
The thing a newcomer needs to know about an operation is the *shape of the
toolpath it makes* — offsets stepping down, parallel passes riding a surface, a
single profile, a descending helix. That is one picture each, and a schematic
one carries it better than a screenshot: no lighting, no incidental part
geometry to read past, and it stays legible at the 240 px the panel gives it.

Isometric, because Z is half the answer
---------------------------------------
The first version of these was drawn top-down. It was legible and it was wrong
about the most important thing: in milling, what separates one strategy from
another is usually what the tool does in **Z**, and a plan view cannot show it.
*Contour* is the clearest case — its whole character is the cutter rising and
falling as it tracks the form, and from above that is indistinguishable from
*level*, which is flat by definition. Roughing stepping down through depths,
helical descending a bore, drilling plunging and retracting: all of it is Z.

So everything here is built in 3D and projected. Nothing is drawn by eye:

    sx = (x - y) * cos(30°)
    sy = (x + y) * sin(30°) - z

Increasing *x* or *y* moves down and across the screen, increasing *z* moves
straight up — which is what makes a rise in the toolpath read as a rise. Each
diagram is auto-fitted to the canvas from its own projected bounds, so adding a
taller part later cannot push a path off the edge.

What is drawn
-------------
Every diagram is the same scene so only the toolpath differs: the stock blank as
three shaded faces, the part inside it, and the path the operation would cut.
The part is a block with a dome on it — deliberately the same shape as the
sphere-on-a-block test part — because a dome is the cheapest geometry that makes
a Z-following pass look like one.

The palette matches the app's own toolpath colours, cyan for cutting and green
for links, so a diagram and the Preview view are talking about the same thing.

The rotary (4-axis) operations -- *rotary rough*, *surface rough*, *rotary
finish* (Kiri's lathe) and *rotary contour* -- use their own scene: the part
is a bar lying along X, and each ring round it is drawn only where it faces
the viewer (``_bar_arc``).

*Scallop* is the exception: a side view, because what it has to explain -- the
ridges a ball leaves between passes, and spacing measured along a slope --
only shows in a section (see ``_scallop``).

Words, for beginners
--------------------
Every diagram also says what its parts are and what the operation is for:
two or three short callouts with leader lines ("steps down each layer",
"boundary you pick", "drill bit"), placed by hand in the corners each drawing
leaves empty, and a "Use for:" line under a hairline at the bottom
(:data:`_USE`). A picture alone shows the shape of a toolpath; a newcomer
also needs the names, and when to reach for it. The drawing is fitted above
the caption band (:data:`USE_TOP`).

Operations with no diagram
--------------------------
``laser on``, ``laser off``, ``index``, ``loop`` and ``gcode`` are not
cutting strategies: they switch a mode, rotate an axis, repeat a block or
inject text. There is no toolpath shape to show, and inventing one would be
worse than the words already in the tooltip. They get no picture, and the panel
omits the slot.
"""

from __future__ import annotations

import json
import math
import re

#: Registry the tooltip panel reads. One global rather than an import, because
#: the two modules are injected separately and must not depend on load order.
REGISTRY = "__piccolaOpArt"

#: Attribute naming which diagram an element wants. Stamped by
#: ``ui/cam_op_menu.py`` onto each entry in the add-operation menu.
ART_ATTR = "data-piccola-art"

#: Canvas. Taller than the top-down version was: an isometric box of the same
#: footprint is about half again as tall once it has a height at all.
VIEW_W = 260
VIEW_H = 190
VIEWBOX = f"0 0 {VIEW_W} {VIEW_H}"

#: Palette. Cut and link are the app's own toolpath colours so a diagram and the
#: real preview agree. The three stock faces are one colour at three values,
#: which is what makes a flat projection read as a solid.
#: The solids are deliberately NEUTRAL grey, not the blue-grey they started as.
#: A cyan toolpath on blue-grey stock is one hue against itself and vanishes;
#: the same line on neutral grey is what makes Fusion's previews readable at a
#: glance. The colour carries the meaning, the grey stays out of its way.
_TOP = "#ededed"
_LEFT = "#cccccc"
_RIGHT = "#dedede"
_EDGE = "#8f8f8f"
_PART_TOP = "#c2c2c2"
_PART_LEFT = "#a2a2a2"
_PART_RIGHT = "#b2b2b2"
_PART_EDGE = "#6f6f6f"
#: Stroke weights, in the same model units as the solids — so these are a direct
#: ratio against the grey outlines, which are drawn at 1.0. The path is a third
#: of the weight of the box it sits in, which is what makes it read as a cut
#: rather than as part of the drawing.
#:
#: Not lower. At 0.2 and below the stroke falls under a device pixel at the size
#: the panel renders these, and antialiasing turns a continuous pass into a row
#: of faint dots — it stops being a line before it stops being visible. 0.35 is
#: the thinnest that still draws as one.
CUT_W = 0.35
LINK_W = 0.25
CUT_W = 0.35
LINK_W = 0.25

_CUT = "#0a7fd4"
_LINK = "#2f9e44"
_SEL = "#d69a2d"
#: The cutter, when a diagram shows one. Near-black, like every CAM package
#: draws it — it is the one thing in the picture that is not the workpiece.
_TOOL = "#2b3138"
_TOOL_TOP = "#4a525b"

#: Stock, in model units. The part is a slab with a dome on top of it.
SX, SY, SZ = 100.0, 72.0, 34.0
PX0, PX1 = 24.0, 76.0
PY0, PY1 = 14.0, 58.0
PZ = 16.0                     #: top of the part's slab
DOME_R = 19.0                 #: dome radius, centred on the slab
DOME_H = 13.0                 #: how far the dome rises above the slab
DOME_CX, DOME_CY = (PX0 + PX1) / 2, (PY0 + PY1) / 2

_COS30 = math.cos(math.radians(30))
_SIN30 = math.sin(math.radians(30))


def _proj(x, y, z):
    """Model space to the unscaled 2D plane."""
    return ((x - y) * _COS30, (x + y) * _SIN30 - z)


#: The drawing scale whose line weights every diagram keeps to at most:
#: *outline*'s. A diagram fitted larger has its strokes thinned to match;
#: smaller ones (pocket, area) keep their own, lighter lines.
STROKE_REF_SCALE = 1.83


#: Words on the diagrams. Each says what the parts of the picture are and,
#: underneath, what the operation is for -- the two things a beginner cannot
#: get from a drawing alone. Canvas units; the panel shows the 260-wide canvas
#: about 280 px wide, so 9.6 here is about 10 px.
_FONT = ("font-family=\"system-ui, -apple-system, 'Segoe UI', sans-serif\""
         " font-size=\"9.6\"")
_LINE = 11.0                  #: line spacing
_TXT = "#2b3138"
_NOTE = "#7a7f86"
_LEAD = "#6b7280"             #: callout leader lines
#: Top of the "Use for:" caption band; drawings with a caption fit above it.
USE_TOP = 153.0
#: Characters per caption line, at this font across the canvas.
_USE_WRAP = 50


def _text(x, y, lines, color=_TXT, weight="400", anchor="start"):
    """Lines of text, the first baseline at y."""
    return "".join(
        f'<text x="{x:.1f}" y="{y + i * _LINE:.1f}" fill="{color}"'
        f' font-weight="{weight}" text-anchor="{anchor}" {_FONT}>{line}</text>'
        for i, line in enumerate(lines))


#: Rough width of one character at this font, for placing leaders.
_CHAR_W = 5.0


def _callout(at, lines, x, y, anchor="start"):
    """Text at (x, y) with a leader to the canvas point `at`.

    The leader leaves the side of the text facing the point -- left or right,
    or the top or bottom edge when the point is above or below it -- so it
    never crosses the words.
    """
    ax, ay = at
    # a label is a sentence: it starts with a capital
    lines = [lines[0][:1].upper() + lines[0][1:]] + list(lines[1:])
    w = max(len(line) for line in lines) * _CHAR_W
    left = x if anchor == "start" else x - w
    right = left + w
    top, bot = y - 7.5, y + (len(lines) - 1) * _LINE + 2.5
    if ax < left:
        sx, sy = left - 3, min(max(ay, top), bot)
    elif ax > right:
        sx, sy = right + 3, min(max(ay, top), bot)
    else:
        sx, sy = min(max(ax, left), right), (top - 2 if ay < top else bot + 2)
    return (_text(x, y, lines, anchor=anchor)
            + f'<path d="M{sx:.1f} {sy:.1f} L{ax:.1f} {ay:.1f}" fill="none"'
              f' stroke="{_LEAD}" stroke-width="0.7"/>'
            + f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="1.5" fill="{_LEAD}"/>')


def _use_for(text):
    """The caption band: what the operation is for, under a hairline."""
    import textwrap
    lines = textwrap.wrap("Use for: " + text, _USE_WRAP)
    out = [f'<path d="M6 {USE_TOP - 1:.1f} L254 {USE_TOP - 1:.1f}"'
           f' stroke="#dfe3e8" stroke-width="0.8"/>']
    for i, line in enumerate(lines):
        y = USE_TOP + 10.5 + i * _LINE
        if i == 0:
            rest = line[len("Use for:"):]
            out.append(f'<text x="8" y="{y:.1f}" fill="{_TXT}" {_FONT}>'
                       f'<tspan font-weight="600">Use for:</tspan>{rest}</text>')
        else:
            out.append(f'<text x="8" y="{y:.1f}" fill="{_TXT}" {_FONT}>{line}</text>')
    return "".join(out)


def _hull(pts):
    """The 3D points whose projections make the convex outline, in order."""
    pairs = sorted({_proj(*p): p for p in pts}.items())

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def half(seq):
        out = []
        for q, p in seq:
            while len(out) >= 2 and turn(out[-2][0], out[-1][0], q) <= 0:
                out.pop()
            out.append((q, p))
        return out[:-1]

    return [p for _q, p in half(pairs) + half(pairs[::-1])]


def _dome_z(x, y):
    """Height of the part surface at (x, y) — slab, plus dome where there is one."""
    d = math.hypot(x - DOME_CX, y - DOME_CY)
    if d >= DOME_R:
        return PZ
    # a spherical cap, so the pass that rides it has a real curve to climb
    return PZ + DOME_H * math.sqrt(max(0.0, 1.0 - (d / DOME_R) ** 2))


class _Scene:
    """Collects projected geometry, then fits it to the canvas.

    Fitting from the actual projected bounds rather than from hand-tuned numbers
    is what lets a diagram add a taller part or a higher retract without anything
    silently leaving the frame.
    """

    def __init__(self):
        self.parts: list[str] = []
        self._pts: list[tuple[float, float]] = []

    def _xy(self, p):
        q = _proj(*p)
        self._pts.append(q)
        return q

    def poly(self, pts, fill, stroke=None, width=1.0, op=1.0):
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                     for i, (x, y) in enumerate(self._xy(p) for p in pts)) + " Z"
        st = f' stroke="{stroke}" stroke-width="{width}"' if stroke else ""
        o = f' fill-opacity="{op}"' if op != 1.0 else ""
        self.parts.append(f'<path d="{d}" fill="{fill}"{o}{st}/>')

    def poly_hole(self, outer, hole, fill, stroke=None, width=1.0):
        """A face with an opening punched through it.

        Two subpaths in one path with ``fill-rule="evenodd"``: the inner ring
        cancels the outer one, so whatever was painted *before* this shows
        through the hole and everything else is covered. That is the whole trick
        behind the pocket diagram -- the cavity is drawn first and this lid goes
        on last, which clips the cavity to its own opening the way a real
        aperture does, with no clip path and no mask.
        """
        def sub(pts):
            return " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                            for i, (x, y) in enumerate(self._xy(q) for q in pts)) + " Z"
        # one opening, or several: a list of outlines
        holes = hole if hole and isinstance(hole[0], list) else [hole]
        d = sub(outer) + " " + " ".join(sub(h) for h in holes)
        st = f' stroke="{stroke}" stroke-width="{width}"' if stroke else ""
        self.parts.append(
            f'<path d="{d}" fill="{fill}" fill-rule="evenodd"{st}/>')

    def line(self, pts, stroke, width=1.8, dash="", cap="round"):
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                     for i, (x, y) in enumerate(self._xy(p) for p in pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}"'
            f' stroke-linecap="{cap}" stroke-linejoin="round"{da}/>')

    def cut(self, pts, width=None):
        self.line(pts, _CUT, CUT_W if width is None else width)

    def link(self, pts):
        self.line(pts, _LINK, LINK_W, dash="2.5 2")

    def sel(self, pts, close=True):
        p = list(pts) + ([pts[0]] if close else [])
        self.line(p, _SEL, 1.8, dash="4.5 3")

    def box(self, x0, y0, z0, x1, y1, z1, top, left, right, edge):
        """Three visible faces of a box: top, the +y face, and the +x face."""
        self.poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
                  top, edge, 1.0)
        self.poly([(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)],
                  left, edge, 1.0)
        self.poly([(x1, y0, z1), (x1, y1, z1), (x1, y1, z0), (x1, y0, z0)],
                  right, edge, 1.0)

    def solid_dome(self, gid):
        """The dome as a solid: its outline filled with a shaded gradient.

        *gid* names the gradient; it must be unique on a page that shows
        several diagrams at once (the review sheet does).
        """
        pts = []
        for i in range(25):                          # rings from the base up
            frac = i / 24
            r = DOME_R * math.sqrt(max(0.0, 1.0 - frac ** 2))
            z = PZ + DOME_H * frac
            pts += [(DOME_CX + r * math.cos(a), DOME_CY + r * math.sin(a), z)
                    for a in [k / 48 * 2 * math.pi for k in range(48)]]
        self.parts.append(
            f'<defs><radialGradient id="{gid}" cx="0.36" cy="0.3" r="0.78">'
            f'<stop offset="0" stop-color="#f4f4f4"/>'
            f'<stop offset="0.55" stop-color="{_PART_TOP}"/>'
            f'<stop offset="1" stop-color="#8f8f8f"/></radialGradient></defs>')
        self.poly(_hull(pts), f"url(#{gid})", _PART_EDGE, 1.0)

    def dome(self):
        """A few rings and meridians — enough to read as a dome, cheap to draw."""
        for frac in (0.35, 0.68, 0.9):
            r = DOME_R * math.sqrt(max(0.0, 1.0 - frac ** 2))
            z = PZ + DOME_H * frac
            ring = [(DOME_CX + r * math.cos(a), DOME_CY + r * math.sin(a), z)
                    for a in [i / 36 * 2 * math.pi for i in range(37)]]
            self.line(ring, _PART_EDGE, 0.8)
        for k in range(4):
            a = k / 4 * math.pi
            mer = []
            for i in range(25):
                t = -1 + 2 * i / 24
                mer.append((DOME_CX + DOME_R * t * math.cos(a),
                            DOME_CY + DOME_R * t * math.sin(a),
                            _dome_z(DOME_CX + DOME_R * t * math.cos(a),
                                    DOME_CY + DOME_R * t * math.sin(a))))
            self.line(mer, _PART_EDGE, 0.8)

    def tool(self, x, y, z, h=30.0, r=3.4):
        """An endmill standing at a point, tip at z.

        The body is the silhouette of a cylinder: project the rim and take the
        two extreme points, which under this projection are the edges of the
        shape. Exact enough at this size, and it costs no trig beyond the rim
        that is drawn anyway.
        """
        ring = [i / 24 * 2 * math.pi for i in range(24)]
        bot = [(x + r * math.cos(a), y + r * math.sin(a), z) for a in ring]
        top = [(x + r * math.cos(a), y + r * math.sin(a), z + h) for a in ring]
        px = [_proj(*p)[0] for p in bot]
        lo, hi = px.index(min(px)), px.index(max(px))
        self.poly([bot[lo], bot[hi], top[hi], top[lo]], _TOOL)
        self.poly(bot, _TOOL)
        self.poly(top, _TOOL_TOP)

    def ball_tool(self, x, y, z, h=15.0, r=2.1):
        """A ball end mill standing at a point, tip at z.

        The body is :meth:`tool`'s cylinder silhouette, starting at the ball's
        centre. The ball is its projected outline: the convex hull of points
        on the sphere, since this projection squashes a sphere into an ellipse
        rather than a circle.
        """
        cz = z + r
        ring = [i / 24 * 2 * math.pi for i in range(24)]
        mid = [(x + r * math.cos(a), y + r * math.sin(a), cz) for a in ring]
        top = [(x + r * math.cos(a), y + r * math.sin(a), cz + h) for a in ring]
        px = [_proj(*p)[0] for p in mid]
        lo, hi = px.index(min(px)), px.index(max(px))
        sphere = [(x + r * math.cos(a) * math.sin(t), y + r * math.sin(a) * math.sin(t),
                   cz + r * math.cos(t))
                  for t in [math.pi * k / 12 for k in range(13)] for a in ring]
        self.poly(_hull(sphere), _TOOL)
        self.poly([mid[lo], mid[hi], top[hi], top[lo]], _TOOL)
        self.poly(top, _TOOL_TOP)

    def drill_tool(self, x, y, z, h=15.0, r=2.1):
        """A drill standing at a point, its 118 degree point's tip at z."""
        self.pointed_tool(x, y, z, h, r, 118.0)

    def pointed_tool(self, x, y, z, h=15.0, r=2.1, angle=60.0):
        """A tool with a conical point -- a drill (118 deg), a V-bit (60) --
        standing with its tip at z."""
        tl = r / math.tan(math.radians(angle / 2))   # height of the point
        ring = [i / 24 * 2 * math.pi for i in range(24)]
        rim = [(x + r * math.cos(a), y + r * math.sin(a), z + tl) for a in ring]
        top = [(x + r * math.cos(a), y + r * math.sin(a), z + tl + h) for a in ring]
        px = [_proj(*p)[0] for p in rim]
        lo, hi = px.index(min(px)), px.index(max(px))
        self.poly(_hull(rim + [(x, y, z)]), _TOOL)
        self.poly([rim[lo], rim[hi], top[hi], top[lo]], _TOOL)
        self.poly(top, _TOOL_TOP)

    def hole(self, x, y, z, r=2.4):
        """The mouth of a hole in a horizontal face."""
        self.poly(_circle(x, y, r, z, 36), "#5d646c", _PART_EDGE, 0.6)

    def stock(self):
        self.box(0, 0, 0, SX, SY, SZ, _TOP, _LEFT, _RIGHT, _EDGE)

    def part(self, dome=True):
        self.box(PX0, PY0, 0, PX1, PY1, PZ,
                 _PART_TOP, _PART_LEFT, _PART_RIGHT, _PART_EDGE)
        if dome:
            self.dome()

    def render(self, labels=(), use=None, fit=None, stroke_scale=STROKE_REF_SCALE):
        """Scale and centre everything on the canvas, then add the words.

        *labels* are callouts: ``(point, lines, (x, y), anchor)`` -- a model
        point to point at, the text, where its first baseline goes on the
        canvas, and ``"start"`` or ``"end"`` for which side of that x the text
        runs. A leader runs from the text to the point. They are placed by
        hand, in the corners each drawing leaves empty.

        *use* is the "Use for:" caption under the drawing; with one, the
        drawing is fitted above it. *fit* overrides the drawing's box,
        ``(x0, y0, x1, y1)`` on the canvas, to leave room for labels.
        *stroke_scale* is the fit above which lines are thinned back (see
        below); scenes shown side by side pass the same one so their lines
        match.
        """
        xs = [p[0] for p in self._pts]
        ys = [p[1] for p in self._pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        pad = 8.0
        x0, y0, x1, y1 = fit or (pad, pad, VIEW_W - pad,
                                 (USE_TOP - 4) if use else VIEW_H - pad)
        s = min((x1 - x0) / w, (y1 - y0) / h)
        tx = x0 - min(xs) * s + (x1 - x0 - w * s) / 2
        ty = y0 - min(ys) * s + (y1 - y0 - h * s) / 2
        body = "".join(self.parts)
        # Stroke widths are in model units, so they grow with the fit: a
        # small scene zoomed in (the drill's cut-away) drew its outlines
        # nearly three times as heavy as the pocket's. Past STROKE_REF_SCALE
        # they are thinned back to what they would be at that scale.
        if s > stroke_scale:
            f = stroke_scale / s
            body = re.sub(r'stroke-width="([\d.]+)"',
                          lambda m: f'stroke-width="{float(m.group(1)) * f:.3f}"', body)
        out = [f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.4f})"'
               f' vector-effect="non-scaling-stroke"'
               f' stroke-width="{1 / s:.3f}">'
               + body + "</g>"]
        for point, lines, (lx, ly), anchor in labels:
            qx, qy = _proj(*point)
            out.append(_callout((tx + qx * s, ty + qy * s), lines, lx, ly, anchor))
        if use:
            out.append(_use_for(use))
        return "".join(out)


#: "Use for:" -- when to pick each operation, in a beginner's words.
_USE = {
    "rough": "taking off most of the material fast. It leaves a thin layer for a finishing pass.",
    "level": "making the top of the stock flat and even, before anything else.",
    "pocket": "cutting a recess with a flat floor, down to a surface you pick.",
    "area": "clearing inside an outline you choose, for a recess or a raised shape.",
    "outline": "cutting the part free from the stock, or finishing its outside walls.",
    "contour": "3D finishing with a ball end mill, in passes along X or Y.",
    "trace": "engraving, lettering and chamfers along edges you pick.",
    "helical": "cones, round bosses and bores: it spirals down a round shape you pick.",
    "scallop": "3D finishing with a ball end mill, with the same smooth finish on flats and steep walls.",
    "drill": "drilling holes with a drill bit. Deep holes are drilled in pecks to clear the chips.",
    "register": "two-sided jobs: holes that line the stock up again after you flip it.",
    "flip": "machining the back of the part. Set the origin again after you turn it over.",
    "rotary rough": "4-axis roughing of a bar: clears the stock in layers of even depth, down to the part.",
    "surface rough": "4-axis roughing that keeps the part's shape, leaving an even skin for the finish.",
    "lathe": "4-axis finishing of round parts with a ball end mill: lines, circles or one smooth spiral.",
    "rotary contour": "4-axis finishing of steep walls after roughing: ribs, shoulders and flights.",
    "cut off": "4-axis parting: a groove just outside each end of the part, down to a core you saw through.",
}


#: Minimum gap between neighbouring passes, in model units. It tracks the stroke
#: weight: fat lines need air between them or the passes merge into a solid
#: block and the strategy stops being readable. At hairline weight they can sit
#: much closer, which is what a real stepover looks like.
MIN_STEP = 3.0

#: Spacing between Level's passes and Rough's single lap per depth, in the
#: drawing. Wider than MIN_STEP: the rough-strategy diagrams read as a tangle
#: when every pass is drawn at a real stepover.
LEVEL_STEP = 6.0
ROUGH_OFF = 3.0


def _rrect(x0, y0, x1, y1, r, n=96):
    """`n` points once around a rounded rectangle, corners included.

    Used where a profile has to be walked rather than drawn: giving each point
    its own Z is what turns a stack of separate loops into one continuous
    descent, which is what a real profile pass does.
    """
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    segs = [
        ("l", (x1 - r, y0), (x1 - r, y1)),
        ("a", (x1 - r, y1 - r), (-90.0, 0.0)),
        ("l", (x1, y1 - r), (x0 + r, y1 - r)) if False else ("l", (x1, y1 - r), (x1, y1 - r)),
    ]
    # simpler and exact: sample the four straights and four arcs in order
    pts = []
    straights = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)),
                 ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
    arcs = [((x1 - r, y0 + r), 270.0), ((x1 - r, y1 - r), 0.0),
            ((x0 + r, y1 - r), 90.0), ((x0 + r, y0 + r), 180.0)]
    for i in range(4):
        (ax, ay), (bx, by) = straights[i]
        for k in range(8):
            t = k / 8
            pts.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        (cx, cy), a0 = arcs[i]
        for k in range(8):
            a = math.radians(a0 + k * 90 / 8)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _split_front(path, x0, y0, x1, y1):
    """A path around a box, cut into the runs behind it and in front of it.

    Seen from this projection the box's silhouette corners are (x1, y0) and
    (x0, y1); the diagonal through them divides what the box hides (the -x,
    -y side) from what is in front of it (+x, +y). Returns ``(back, front)``,
    lists of point runs, each run sharing its end point with the next so the
    line stays continuous. Draw the back runs, then the box, then the front
    runs -- the box then hides the far side of the path the way the pocket's
    lid clips its cavity.
    """
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2, (y1 - y0) / 2

    def front(p):
        return (p[0] - cx) * hy + (p[1] - cy) * hx > 0

    # cut long segments short first, so the split lands where the line really
    # passes the box's corner and not at the segment's far end
    dense = []
    for a, b in zip(path, path[1:]):
        n = max(1, int(math.dist(a, b) / 1.0))
        dense += [tuple(a[k] + (b[k] - a[k]) * i / n for k in range(3)) for i in range(n)]
    dense.append(path[-1])

    back, fwd, run, side = [], [], [], None
    for p in dense:
        f = front(p)
        if side is None or f == side:
            run.append(p)
        else:
            (fwd if side else back).append(run + [p])
            run = [p]
        side = f
    if run:
        (fwd if side else back).append(run)
    return back, fwd


def _inset(poly, d):
    """A polygon moved in by d on every side, or None once it closes.

    Mitred: each corner is where the neighbouring edges' moved lines cross.
    Fine for any simple outline until the offset changes its shape (an edge
    shrinking past nothing), which is when it returns None.
    """
    # points in a straight run (a sampled straight side) would give two
    # parallel edges with no crossing -- drop them
    pts = []
    m = len(poly)
    for i in range(m):
        (ax, ay), (bx, by), (cx, cy) = poly[i - 1], poly[i], poly[(i + 1) % m]
        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-9:
            pts.append(poly[i])
    poly = pts
    n = len(poly)
    area = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1]
               for i in range(n)) / 2
    sgn = 1 if area > 0 else -1                 # inward is left of CCW edges
    lines = []
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        ln = math.hypot(bx - ax, by - ay)
        nx, ny = -(by - ay) / ln * sgn, (bx - ax) / ln * sgn
        lines.append(((ax + nx * d, ay + ny * d), (bx - ax, by - ay)))
    out = []
    for i in range(n):
        (p, u), (q, v) = lines[i - 1], lines[i]
        den = u[0] * v[1] - u[1] * v[0]
        if abs(den) < 1e-9:
            return None
        t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
        out.append((p[0] + u[0] * t, p[1] + u[1] * t))
    # an edge that has shrunk past nothing comes back reversed
    for i in range(n):
        (ax, ay), (bx, by) = out[i], out[(i + 1) % n]
        u = lines[i][1]
        if (bx - ax) * u[0] + (by - ay) * u[1] <= 0:
            return None
    a2 = sum(out[i][0] * out[(i + 1) % n][1] - out[(i + 1) % n][0] * out[i][1]
             for i in range(n)) / 2
    return out if a2 * area > 1.0 else None


def _arc(cx, cy, r, z, a0, a1, n=48):
    """`n`+1 points along a horizontal arc, `a0` to `a1` in degrees."""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)), z)
            for i in range(n + 1)]


def _circle(cx, cy, r, z, n=72):
    """A closed horizontal circle."""
    return _arc(cx, cy, r, z, 0.0, 360.0, n)


def _ring(x0, y0, x1, y1, z, steps=1):
    """A rectangular pass at one depth."""
    return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z), (x0, y0, z)]


def _rough():
    """Clearing the material from around the part, one depth at a time.

    No blank is drawn. The passes *are* the blank -- every loop is a lap the
    cutter takes through material that is no longer there, and the part they
    ring is the thing being uncovered. The part is drawn solid, the dome as a
    shaded half sphere, and it hides the far side of every lap the way the
    pocket's lid hides its cavity: laps behind it first, then the part, then
    the laps in front.
    """
    s = _Scene()
    depths = (PZ - 1.5, PZ - 6.5, PZ - 11.5)
    laps = []
    # three depths of offsets, each ringing the part from the silhouette
    # outwards. The step-down between them is the whole point of roughing.
    # one lap per depth: enough to show the layers, few enough to read
    o = ROUGH_OFF
    for z in depths:
        laps.append(_ring(PX0 - o, PY0 - o, PX1 + o, PY1 + o, z))
    split = [_split_front(lap, PX0, PY0, PX1, PY1) for lap in laps]
    for back, _ in split:
        for run in back:
            s.cut(run)
    s.part(dome=False)
    s.solid_dome("piccola-rough-dome")
    for _, front in split:
        for run in front:
            s.cut(run)
    # the drop from one depth to the next, on the right-hand corner where it
    # can be seen
    s.link([(PX1 + o, PY0 - o, depths[0]), (PX1 + o, PY0 - o, depths[-1])])
    # the cutter on the top lap, on the corner nearest the viewer
    o3 = o
    s.tool(PX1 + o3, PY1 + o3, depths[0], h=14.0, r=2.1)
    return s.render(labels=[
        ((PX0 - o3, PY1 + o3, depths[1]), ["Laps around", "the part"], (8, 40), "start"),
        ((PX1 + o, PY0 - o, depths[1]), ["Steps down", "each layer"], (254, 40), "end"),
        ((PX1 + o3, PY1 + o3, depths[0] + 14.0), ["End mill"], (200, 140), "start"),
    ], use=_USE["rough"])


def _level():
    """Facing the top flat: parallel passes back and forth across one plane.

    The dome is left off. Levelling is the operation that makes a face flat
    before anything else happens to it, so a formed top would be describing a
    different job.
    """
    s = _Scene()
    s.part(dome=False)
    z = PZ + 0.5
    over = 4.0                 # run off each end, so the pass enters and leaves
    pts, left = [], True
    y = PY0 + 1.0
    while y <= PY1 - 1.0:
        a, b = PX0 - over, PX1 + over
        pts.append([(a if left else b, y, z), (b if left else a, y, z)])
        y += LEVEL_STEP
        left = not left
    for i, seg in enumerate(pts):
        s.cut(seg)
        if i + 1 < len(pts):
            s.link([seg[1], pts[i + 1][0]])
    # a wide flat end mill, part way along the last pass
    last = pts[-1]
    tx = (last[0][0] + last[1][0]) / 2
    s.tool(tx, last[0][1], z, h=14.0, r=3.4)
    return s.render(labels=[
        (pts[2][0], ["Back-and-forth", "passes"], (14, 30), "start"),
    ], use=_USE["level"])


#: Distance between Contour's passes in the drawing -- wide enough that a
#: handful of passes show the shape without crowding it.
CONTOUR_STEP = 5.5
#: Contour's part: a block whose top rises and falls in waves along X. Gentle
#: enough (slope under 1) that every point of it faces the eye, so no pass
#: is ever hidden or broken.
WAVE_BASE = PZ + 2.0
WAVE_AMP = 4.0
WAVE_TURNS = 1.25


def _wave_z(x):
    """Height of Contour's wavy top at x."""
    t = (x - PX0) / (PX1 - PX0)
    return WAVE_BASE + WAVE_AMP * math.sin(t * WAVE_TURNS * 2 * math.pi)


def _wave_part(s):
    """The wavy block: shaded top strips, the front face, the right face."""
    n = 48
    xs = [PX0 + (PX1 - PX0) * i / n for i in range(n + 1)]
    for x0, x1 in zip(xs, xs[1:]):
        z0, z1 = _wave_z(x0), _wave_z(x1)
        # lighter where the strip faces up and toward the light (from -x)
        slope = (z1 - z0) / (x1 - x0)
        shade = max(0.0, min(1.0, 0.55 - 0.35 * slope))
        g = int(178 + 60 * shade)
        s.poly([(x0, PY0, z0), (x1, PY0, z1), (x1, PY1, z1), (x0, PY1, z0)],
               f"#{g:02x}{g:02x}{g:02x}", f"#{g:02x}{g:02x}{g:02x}", 0.4)
    s.poly([(x, PY1, _wave_z(x)) for x in xs] + [(PX1, PY1, 0), (PX0, PY1, 0)],
           _PART_LEFT, _PART_EDGE, 1.0)
    s.poly([(PX1, PY0, _wave_z(PX1)), (PX1, PY1, _wave_z(PX1)), (PX1, PY1, 0), (PX1, PY0, 0)],
           _PART_RIGHT, _PART_EDGE, 1.0)
    # the top's outline, so the solid reads crisply
    s.line([(x, PY0, _wave_z(x)) for x in xs], _PART_EDGE, 1.0)
    s.line([(PX0, PY0, _wave_z(PX0)), (PX0, PY1, _wave_z(PX0))], _PART_EDGE, 1.0)


def _contour():
    """Parallel passes along X, each one riding up and down the part's waves.

    The part is a block with a wavy top rather than a dome: every point of a
    gentle wave faces the eye, so every pass is one unbroken line, rising and
    falling with the surface -- which is the whole of what contour does.
    """
    s = _Scene()
    _wave_part(s)
    # back and forth, so each link to the next pass is a short hop at the end
    passes = []
    y = PY0 + 3.0
    while y <= PY1 - 3.0:
        xs = [PX0 + i * 0.5 for i in range(int((PX1 - PX0) / 0.5) + 1)]
        if len(passes) % 2:
            xs.reverse()
        passes.append([(x, y, _wave_z(x) + 0.5) for x in xs])
        y += CONTOUR_STEP
    for path in passes:
        s.cut(path)
    for prev, path in zip(passes, passes[1:]):
        s.link([prev[-1], path[0]])
    # a ball end mill riding the front pass, on a crest
    front = passes[-1]
    crest = max(front, key=lambda p: p[2] if p[0] > PX0 + 25 else -1)
    s.ball_tool(crest[0], crest[1], crest[2], h=11.0, r=1.8)
    fx = min(front, key=lambda p: abs(p[0] - (PX0 + 6.0)))
    return s.render(labels=[
        (fx, ["Parallel passes", "ride up and down", "the shape"], (8, 121), "start"),
        ((crest[0], crest[1], crest[2] + 13.0), ["Ball", "end mill"], (226, 40), "start"),
    ], use=_USE["contour"])


#: The *outline* part stands on its own, with the passes running down past its
#: bottom face. Cutting a part free is the operation where the material around
#: it is gone by definition, so there is nothing left to draw around it.
OUT_BASE_Z = 0.0
OUT_TOP_Z = 28.0
#: The cutter's centre runs this far out from the part's wall (its radius).
OUT_OFF = 2.6
#: Depth of each pass below the top: a 2D contour steps down level by level.
OUT_STEPS = (OUT_TOP_Z - 6.0, OUT_TOP_Z - 13.0, OUT_TOP_Z - 20.0, OUT_BASE_Z + 0.5)


def _outline():
    """The part's outside, cut in passes that step down to the bottom.

    Drawn the way a 2D contour toolpath is: one loop hugging the part's
    outline at each depth, a short drop between them, the last at the bottom
    so the part comes free. The part hides the far side of every loop --
    loops behind it first, then the part, then the loops in front.
    """
    s = _Scene()
    ring = _rrect(PX0 - OUT_OFF, PY0 - OUT_OFF, PX1 + OUT_OFF, PY1 + OUT_OFF, OUT_OFF + 1.0)
    # start every loop at the part's right-hand corner, where the tool and the
    # drops between layers stand clear of the part instead of over its top
    k0 = min(range(len(ring)), key=lambda i: math.dist(ring[i], (PX1 + OUT_OFF, PY0 - OUT_OFF)))
    ring = ring[k0:] + ring[:k0]
    loops = [[(x, y, z) for x, y in ring] + [(ring[0][0], ring[0][1], z)] for z in OUT_STEPS]
    split = [_split_front(lp, PX0, PY0, PX1, PY1) for lp in loops]
    for back, _ in split:
        for run in back:
            s.cut(run, CUT_W * 1.6)
    s.box(PX0, PY0, OUT_BASE_Z, PX1, PY1, OUT_TOP_Z,
          _PART_TOP, _PART_LEFT, _PART_RIGHT, _PART_EDGE)
    for _, front in split:
        for run in front:
            s.cut(run, CUT_W * 1.6)
    sx0, sy0 = ring[0]
    for z0, z1 in zip(OUT_STEPS, OUT_STEPS[1:]):
        s.link([(sx0, sy0, z0), (sx0, sy0, z1)])
    # the tool, coming down to the first pass
    s.link([(sx0, sy0, OUT_TOP_Z + 9), (sx0, sy0, OUT_STEPS[0])])
    s.tool(sx0, sy0, OUT_TOP_Z + 9, h=12.0, r=2.1)
    near = [p for p in loops[1] if p[1] > PY1]
    return s.render(labels=[
        (near[len(near) // 3], ["Follows the", "outline, one", "layer at a time"], (8, 112), "start"),
        ((sx0, sy0, OUT_TOP_Z + 21), ["End mill"], (196, 24), "end"),
        ((sx0, sy0, OUT_STEPS[-1]), ["Part comes", "free at the", "bottom"], (254, 118), "end"),
    ], use=_USE["outline"])


#: The pocket diagram's cavity, in model units. Wide and shallow, because the
#: thing being shown is the cleared *area*, not the depth.
POCKET_R = 25.0
POCKET_DEPTH = 11.0
POCKET_CX, POCKET_CY = SX / 2, SY / 2

#: The arc of the cavity wall that faces away from the eye, and so is the part
#: of the inside you can see. A point on the rim at angle `a` moves up the
#: screen when cos(a) + sin(a) < 0 under this projection, which is exactly
#: 135 deg .. 315 deg. Spelled as constants because the numbers are a
#: consequence of the projection and would otherwise look arbitrary.
WALL_A0, WALL_A1 = 135.0, 315.0

#: Clearance between the outermost pass and the wall it is cutting to -- the
#: cutter has a radius, so its centre never reaches the finished face.
POCKET_CLEAR = 3.0


def _pocket():
    """A cavity milled out of the blank, with the spiral that cleared it.

    Pocketing is the one operation whose *result* is a hole, so this draws the
    hole rather than a stack of loops hovering above an untouched face. Order is
    the whole construction: floor, then the far band of wall, then the path
    lying on that floor, and the stock's top face goes on last with an opening
    cut in it. The lid clips all three to the aperture, which is what produces
    the reading that matters -- the near wall hides part of its own floor, the
    far wall is a visible band of cut face, and the spiral is unmistakably
    *down inside* the part instead of floating over it.

    Nothing is dashed onto the floor to mark the selected surface. The floor
    *is* the selected surface, it is already the only cut face in the frame, and
    a ring drawn round its edge only read as a chamfer.
    """
    zt = SZ
    zf = SZ - POCKET_DEPTH
    s = _Scene()
    s.stock()

    # the cut faces, in the order they stack up the screen
    s.poly(_circle(POCKET_CX, POCKET_CY, POCKET_R, zf), _PART_TOP, _PART_EDGE, 1.0)
    s.poly(_arc(POCKET_CX, POCKET_CY, POCKET_R, zt, WALL_A0, WALL_A1) +
           _arc(POCKET_CX, POCKET_CY, POCKET_R, zf, WALL_A1, WALL_A0),
           _PART_LEFT, _PART_EDGE, 1.0)

    # one continuous spiral out from the centre: a pocket is entered from above
    # and cleared outwards, and a spiral says both at once. The pitch is the
    # stepover, so the density of the picture is the density of the real path.
    reach = POCKET_R - POCKET_CLEAR
    turns = max(2, int(round(reach / MIN_STEP)))
    steps = turns * 40
    s.cut([(POCKET_CX + reach * (i / steps) * math.cos(i / steps * turns * 2 * math.pi),
            POCKET_CY + reach * (i / steps) * math.sin(i / steps * turns * 2 * math.pi),
            zf) for i in range(steps + 1)])
    # ... and one lap round the wall to size it, which is what the last pass of
    # a pocket actually is
    s.cut(_circle(POCKET_CX, POCKET_CY, reach, zf))

    # the lid, last, with the mouth of the pocket cut out of it
    s.poly_hole([(0, 0, zt), (SX, 0, zt), (SX, SY, zt), (0, SY, zt)],
                _circle(POCKET_CX, POCKET_CY, POCKET_R, zt),
                _TOP, _EDGE, 1.0)

    # the cutter enters from above -- the sentence in every manual about what a
    # pocket op is for, drawn
    s.link([(POCKET_CX, POCKET_CY, zt + 11), (POCKET_CX, POCKET_CY, zf)])
    # slim, because it stands over the start of the spiral and a fat one would
    # hide the very thing the picture is about
    s.tool(POCKET_CX, POCKET_CY, zf, h=17.0, r=2.1)
    return s.render(labels=[
        ((POCKET_CX, POCKET_CY, zf + 17.0), ["End mill goes", "in from above"], (12, 30), "start"),
        ((POCKET_CX + reach * 0.7, POCKET_CY + reach * 0.7, zf),
         ["Spiral clears", "a flat floor"], (254, 128), "end"),
        ((POCKET_CX - POCKET_R * 0.7, POCKET_CY - POCKET_R * 0.7, zt - POCKET_DEPTH / 2),
         ["Straight", "walls"], (226, 30), "start"),
    ], use=_USE["pocket"])


#: The *area* diagram: two boundaries on one stock, the two ways the operation
#: is used. Left, a recess -- everything inside the boundary is cleared.
#: Right, a raised shape -- inside the boundary, the material around a shape
#: is cleared and the shape is left standing. Rounded rectangles (x0, y0, x1,
#: y1, corner radius) on the stock's top.
AREA_RECESS = (9.0, 16.0, 43.0, 56.0, 6.0)
AREA_RAISED = (52.0, 11.0, 93.0, 61.0, 7.0)
AREA_ISLAND = (64.0, 27.0, 81.0, 45.0, 3.0)
AREA_DEPTH = 11.0
#: Distance between the area's passes in the drawing: wider than a real
#: stepover, so a few passes say "it steps in" without filling the floor.
AREA_STEP = 5.5
#: How far the passes keep from the raised shape (the cutter's radius).
AREA_CLEAR = 2.5


def _rr(box, d=0.0):
    """The rounded rectangle `box` moved out by d (in, for negative d)."""
    x0, y0, x1, y1, r = box
    return _rrect(x0 - d, y0 - d, x1 + d, y1 + d, max(r + d, 0.01))


def _cleared(s, box, island=None):
    """One cleared region: its floor, the walls facing the eye, the passes,
    and -- when there is one -- the shape left standing, last, so it hides the
    passes behind it. The stock's top goes on afterwards (see :func:`_area`).
    Returns the passes.
    """
    zt, zf = SZ, SZ - AREA_DEPTH

    def walls(outline, outward):
        n = len(outline)
        a2 = sum(outline[i][0] * outline[(i + 1) % n][1] - outline[(i + 1) % n][0] * outline[i][1]
                 for i in range(n))
        sgn = (1 if a2 > 0 else -1) * (-1 if outward else 1)
        # far ones first; each stroked in its own colour so a curved wall
        # does not draw as a row of stripes
        for i in sorted(range(n), key=lambda i: outline[i][0] + outline[(i + 1) % n][0]
                        + outline[i][1] + outline[(i + 1) % n][1]):
            (ax, ay), (bx, by) = outline[i], outline[(i + 1) % n]
            nx, ny = -(by - ay) * sgn, (bx - ax) * sgn
            if nx + ny > 0:
                c = _PART_LEFT if ny > nx else _PART_RIGHT
                s.poly([(ax, ay, zt), (bx, by, zt), (bx, by, zf), (ax, ay, zf)], c, c, 0.5)

    outline = _rr(box)
    s.poly([(x, y, zf) for x, y in outline], _PART_TOP, _PART_EDGE, 1.0)
    walls(outline, outward=False)
    # passes in from the boundary, stopping short of the shape when there is
    # one, then a lap hugging the shape
    x0, y0, x1, y1, _ = box
    half = min(x1 - x0, y1 - y0) / 2
    stop = 1.5
    if island:
        ix0, iy0, ix1, iy1, _ = island
        stop = min(ix0 - x0, iy0 - y0, x1 - ix1, y1 - iy1) - AREA_CLEAR - 1.0
    rings, d = [], AREA_STEP
    while d < (stop if island else half - stop):
        rings.append(_rr(box, -d))
        d += AREA_STEP
    if island:
        rings.append(_rr(island, AREA_CLEAR))
    for p in rings:
        s.cut([(x, y, zf) for x, y in p] + [(p[0][0], p[0][1], zf)])
    if island:
        top = _rr(island)
        walls(top, outward=True)
        s.poly([(x, y, zt) for x, y in top], _TOP, _EDGE, 1.0)
    return rings


def _area():
    """Material cleared inside a boundary you pick -- both ways it is used.

    Built like *pocket*: each region's floor, walls and passes (and the
    raised shape), then the stock's sides, then its top with both boundaries
    cut out of it, then the boundaries themselves as thin lines on the rims.
    """
    zt = SZ
    s = _Scene()
    recess = _cleared(s, AREA_RECESS)
    _cleared(s, AREA_RAISED, AREA_ISLAND)
    rims = [_rr(AREA_RECESS), _rr(AREA_RAISED)]
    # the stock's sides after the cuts: a floor near the front edge projects
    # past the top face, and the sides must cover it, not the other way round
    s.poly([(0, SY, zt), (SX, SY, zt), (SX, SY, 0), (0, SY, 0)], _LEFT, _EDGE, 1.0)
    s.poly([(SX, 0, zt), (SX, SY, zt), (SX, SY, 0), (SX, 0, 0)], _RIGHT, _EDGE, 1.0)
    s.poly_hole([(0, 0, zt), (SX, 0, zt), (SX, SY, zt), (0, SY, zt)],
                [[(x, y, zt) for x, y in rim] for rim in rims], _TOP, _EDGE, 1.0)
    for rim in rims:
        s.line([(x, y, zt + 0.2) for x, y in rim + rim[:1]], _SEL, 1.0)
    inner = recess[-1]
    ix0, iy0, ix1, iy1, _ = AREA_ISLAND
    return s.render(labels=[
        ((sum(x for x, _ in inner) / len(inner), sum(y for _, y in inner) / len(inner), zt - AREA_DEPTH),
         ["Recess: inside", "is cleared"], (8, 24), "start"),
        (max(((x, y, zt) for x, y in rims[1]), key=lambda p: p[0] - p[1]),
         ["Boundary", "you pick"], (254, 24), "end"),
        (((ix0 + ix1) / 2, (iy0 + iy1) / 2, zt), ["Raised: shape", "left standing"], (254, 128), "end"),
    ], use=_USE["area"])


#: The *trace* diagram's engraving: "CNC" in single-line letters, 12 high,
#: on the plate's top. Letter coordinates are (along, up) in model units;
#: "along" runs +x and "up" runs -y, so the word reads along the plate.
TRACE_LETTER_H = 12.0
#: How much bigger than that the letters are drawn, to fill the plate.
TRACE_SCALE = 1.15
TRACE_GROOVE = "#5d646c"


def _trace_letters():
    """The strokes of "CNC", each a list of (along, up) points."""
    def c_at(u0):
        return [(u0 + 6 + 6 * math.cos(math.radians(a)), 6 + 6 * math.sin(math.radians(a)))
                for a in range(45, 316, 9)]
    return [c_at(0.0), [(14.0, 0.0), (14.0, 12.0), (22.0, 0.0), (22.0, 12.0)], c_at(29.0)]


def _trace():
    """Engraving: a V-bit following lines you pick, cutting a groove.

    "C" and "N" are done -- a dark groove with the toolpath along its centre.
    The last "C" is half way: the V-bit stands at the point it has reached,
    and the rest of the letter is still only the orange line you picked.
    """
    s = _Scene()
    s.part(dome=False)
    u0 = PX0 + (PX1 - PX0 - 41.0 * TRACE_SCALE) / 2
    v0 = (PY0 + PY1) / 2 + TRACE_LETTER_H * TRACE_SCALE / 2
    z = PZ - 0.3

    def to_model(pts):
        k = TRACE_SCALE
        return [(u0 + u * k, v0 - v * k, z) for u, v in pts]

    strokes = [to_model(st) for st in _trace_letters()]
    done, last = strokes[:-1], strokes[-1]
    half = len(last) // 2
    cut, todo = last[:half + 1], last[half:]
    for st in done + [cut]:
        s.line(st, TRACE_GROOVE, 2.6)               # the groove
        s.cut(st)                                   # the path along its centre
    s.line([(x, y, PZ + 0.2) for x, y, _ in todo], _SEL, 1.2)
    # moving between letters: lift, across, down
    for a_, b_ in zip(done + [cut], strokes[1:]):
        p, q = a_[-1], b_[0]
        s.link([p, (p[0], p[1], PZ + 6), (q[0], q[1], PZ + 6), q])
    tx, ty, tz = cut[-1]
    s.pointed_tool(tx, ty, tz, h=11.0, r=2.4, angle=60.0)
    n_mid = strokes[1][1]
    return s.render(labels=[
        (n_mid, ["Engraved", "groove"], (8, 26), "start"),
        ((tx, ty, tz + 15.0), ["V-bit follows", "the line"], (254, 26), "end"),
        (todo[len(todo) // 2][:2] + (PZ + 0.2,), ["Line you pick"], (254, 132), "end"),
    ], use=_USE["trace"])


#: The *helical* diagram's traffic cone: base radius at the bottom, radius at
#: the (cut-off) top, and the heights, on a square base plate.
CONE_CX, CONE_CY = 50.0, 36.0
CONE_R0, CONE_R1 = 15.0, 3.5
CONE_Z0, CONE_Z1 = 6.0, 36.0           #: on a part block CONE_Z0 high
#: The cone's shading: dark edge, light, mid -- the part greys, as the dome.
CONE_GREYS = ("#8f8f8f", "#f0f0f0", _PART_TOP)
#: How far the tool's path stands off the cone (its radius), and its turns.
HEL_OFF = 1.6
HEL_TURNS = 5


def _cone_r(z):
    """The cone's radius at height z."""
    t = (z - CONE_Z0) / (CONE_Z1 - CONE_Z0)
    return CONE_R0 + (CONE_R1 - CONE_R0) * t


def _cone_seen(a):
    """True where the cone's side, at angle a (radians), faces the eye.

    The side's outward normal is (cos a, sin a, k), k its outward lean; the
    eye looks along (-1, -1, -1), so the side shows where that normal has a
    positive dot product with (1, 1, 1).
    """
    k = (CONE_R0 - CONE_R1) / (CONE_Z1 - CONE_Z0)
    return math.cos(a) + math.sin(a) + k > 0


def _cone_arc(z, n=40):
    """The visible arc of the cone's side at height z, left edge to right."""
    k = (CONE_R0 - CONE_R1) / (CONE_Z1 - CONE_Z0)
    half = math.acos(max(-1.0, min(1.0, -k / math.sqrt(2))))
    r = _cone_r(z)
    mid = math.pi / 4
    return [(CONE_CX + r * math.cos(mid + half - 2 * half * i / n),
             CONE_CY + r * math.sin(mid + half - 2 * half * i / n), z) for i in range(n + 1)]


def _gradient(gid, dark, light, mid):
    """A left-to-right shading for a round solid: dark edge, light, mid, dark."""
    return (f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{dark}"/><stop offset="0.35" stop-color="{light}"/>'
            f'<stop offset="0.75" stop-color="{mid}"/><stop offset="1" stop-color="{dark}"/>'
            f'</linearGradient></defs>')


def _helical():
    """A cone on the part, and the tool spiralling down around it.

    Helical follows a downward helix around a round shape you pick -- a
    cone, a boss, a bore. The cone is a shaded solid like the dome: its
    outline is where the side turns away from the eye, and it hides the far
    half of every turn of the path -- turns behind it first, then the cone,
    then the turns in front.
    """
    s = _Scene()
    s.box(PX0, PY0, 0, PX1, PY1, CONE_Z0, _PART_TOP, _PART_LEFT, _PART_RIGHT, _PART_EDGE)
    # the path: down the cone, a tool radius out from its side
    path, n = [], HEL_TURNS * 72
    for i in range(n + 1):
        t = i / n
        z = CONE_Z1 - 1.0 - t * (CONE_Z1 - 1.0 - CONE_Z0 - 1.0)
        a = math.radians(-20) + t * HEL_TURNS * 2 * math.pi
        r = _cone_r(z) + HEL_OFF
        path.append((CONE_CX + r * math.cos(a), CONE_CY + r * math.sin(a), z, a))
    back, front, run, side = [], [], [], None
    for x, y, z, a in path:
        f = _cone_seen(a)
        if side is None or f == side:
            run.append((x, y, z))
        else:
            (front if side else back).append(run + [(x, y, z)])
            run = [(x, y, z)]
        side = f
    (front if side else back).append(run)
    for r_ in back:
        s.cut(r_, CUT_W * 1.3)
    # the cone: its side, then its flat top
    s.parts.append(_gradient("piccola-cone", *CONE_GREYS))
    side_ = _cone_arc(CONE_Z0) + list(reversed(_cone_arc(CONE_Z1)))
    s.poly(side_, "url(#piccola-cone)", _PART_EDGE, 1.0)
    s.poly(_circle(CONE_CX, CONE_CY, CONE_R1, CONE_Z1, 36), "#e2e2e2", _PART_EDGE, 1.0)
    for r_ in front:
        s.cut(r_, CUT_W * 1.3)
    # the cutter coming in from above, over the start of the spiral
    sx0, sy0, sz0 = path[0][:3]
    s.link([(sx0, sy0, CONE_Z1 + 10), (sx0, sy0, sz0)])
    s.tool(sx0, sy0, CONE_Z1 + 10, h=11.0, r=2.1)
    seen = [p[:3] for p in path[n // 2:] if _cone_seen(p[3])]
    on_cone = _cone_arc((CONE_Z0 + CONE_Z1) / 2)[10]
    return s.render(labels=[
        (on_cone, ["Cone", "you pick"], (8, 40), "start"),
        (seen[len(seen) // 3], ["Spirals down", "around it"], (254, 124), "end"),
    ], use=_USE["helical"])


#: Scallop's rings, in the drawing: one stepover apart measured along the
#: dome's surface, so they crowd together in plan on its steep foot.
SCALLOP_STEP = 2.6


def _dome_faces_viewer(x, y):
    """True where the dome's surface faces the eye (or it is the flat top).

    The eye looks along (-1, -1, -1) under this projection, so a surface
    point shows when its normal has a positive dot product with (1, 1, 1).
    """
    d = math.hypot(x - DOME_CX, y - DOME_CY)
    if d >= DOME_R:
        return True
    k = max(1e-6, 1.0 - (d / DOME_R) ** 2)
    g = DOME_H / (DOME_R ** 2 * math.sqrt(k))          # -dz/dx = g * (x - cx)
    return g * (x - DOME_CX) + g * (y - DOME_CY) + 1.0 > 0


def _scallop():
    """Rings riding over the dome, a constant distance apart ALONG it.

    Each ring sits one stepover further up the dome's profile than the last,
    measured along the curve, so seen from above they crowd together on the
    steep foot and open out over the flatter crown -- the difference from an
    offset pattern, and why the finish comes out even. The dome is the solid
    of *rough*; each ring is drawn where the dome faces the eye, like a line
    of latitude on a globe. One ring lies on the flat round the dome, drawn
    before it so the dome hides its far side.
    """
    s = _Scene()
    s.part(dome=False)
    lift = 0.5
    s.cut(_circle(DOME_CX, DOME_CY, DOME_R + 4.0, PZ + lift, 96))
    s.solid_dome("piccola-scallop-dome")
    # rings up the dome, one every SCALLOP_STEP of arc length from the foot
    n, d_prev, z_prev, arc, nxt = 3000, DOME_R, PZ, 0.0, SCALLOP_STEP * 0.6
    radii = []
    for i in range(1, n + 1):
        d = DOME_R * (1 - i / n)
        z = _dome_z(DOME_CX + d, DOME_CY)
        arc += math.hypot(d_prev - d, z - z_prev)
        d_prev, z_prev = d, z
        if arc >= nxt and d > 1.2:
            radii.append(d)
            nxt += SCALLOP_STEP
    for r in radii:
        z = _dome_z(DOME_CX + r, DOME_CY) + lift
        # walk the ring from the back (225 deg), so the part facing the eye
        # comes out as one unbroken arc
        run, runs = [], []
        for k in range(145):
            a = math.radians(225) + k / 144 * 2 * math.pi
            x, y = DOME_CX + r * math.cos(a), DOME_CY + r * math.sin(a)
            if _dome_faces_viewer(x, y):
                run.append((x, y, z))
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        for rr in runs:
            if len(rr) > 1:
                s.cut(rr)
    # a ball end mill on a ring near the foot, facing the eye
    rt = radii[2]
    a = math.radians(45)
    tx, ty = DOME_CX + rt * math.cos(a), DOME_CY + rt * math.sin(a)
    tz = _dome_z(tx, ty) + lift
    s.ball_tool(tx, ty, tz, h=10.0, r=1.8)
    rm = radii[len(radii) // 2]
    am = math.radians(100)
    mid = (DOME_CX + rm * math.cos(am), DOME_CY + rm * math.sin(am),
           _dome_z(DOME_CX + rm, DOME_CY) + lift)
    rf = radii[0]
    af = math.radians(-5)
    foot = (DOME_CX + rf * math.cos(af), DOME_CY + rf * math.sin(af),
            _dome_z(DOME_CX + rf, DOME_CY) + lift)
    return s.render(labels=[
        (mid, ["Rings follow", "the surface"], (8, 30), "start"),
        ((tx, ty, tz + 12.0), ["Ball", "end mill"], (254, 30), "end"),
        (foot, ["Closer where", "it is steep"], (254, 128), "end"),
    ], use=_USE["scallop"])


# ---------------------------------------------------------------------------
# Rotary (4-axis) operations: the part is a bar lying along X, turning about
# it, and the tool stands straight down over it. Drawn in the same projection
# as the rest, so X runs down and to the right and the bar's end faces the
# eye. A ring round the bar is drawn only where it faces the viewer -- the
# rest is behind the bar -- like the scallop's rings on the dome.

#: Bar length and stock radius, in model units.
BAR_L = 60.0
BAR_STOCK_R = 13.0


def _bar_visible(phi):
    """Whether the point at angle *phi* round the bar faces the viewer.

    The bar's surface normal there is (0, cos phi, sin phi); the projection
    looks along (-1, -1, -1), so a point faces the eye when cos + sin > 0.
    """
    return math.cos(phi) + math.sin(phi) > 0.0


def _bar_arc(x, r, n=40):
    """The visible half of a ring of radius *r* round the bar at *x*."""
    a0, a1 = math.radians(-45), math.radians(135)
    return [(x, r * math.cos(a0 + (a1 - a0) * k / n), r * math.sin(a0 + (a1 - a0) * k / n))
            for k in range(n + 1)]


def _bar_rim(x, r, n=48):
    return [(x, r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def _bar_section(s, x0, x1, r, gid, fill_cap=_PART_RIGHT, edge=_PART_EDGE, op=1.0):
    """A cylinder from x0 to x1 about the X axis: its outline, then its end.

    The outline is the hull of the two end rims, filled with a gradient
    across the bar so it reads as round; the end at x1 faces the viewer.
    Sections are drawn from the far end (low x) to the near one.
    """
    s.poly(_hull(_bar_rim(x0, r) + _bar_rim(x1, r)), f"url(#{gid})", edge, 1.0, op)
    s.poly(_bar_rim(x1, r), fill_cap, edge, 1.0, op)


def _bar_gradient(s, gid, light="#f2f2f2", mid=_PART_TOP, dark="#8f8f8f"):
    s.parts.append(
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0.35" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset="0.45" stop-color="{mid}"/>'
        f'<stop offset="1" stop-color="{dark}"/></linearGradient></defs>')


def _bar_stock(s, gid):
    """The round blank, faint, behind everything."""
    _bar_gradient(s, gid, "#fbfbfb", _TOP, _LEFT)
    _bar_section(s, 0.0, BAR_L, BAR_STOCK_R, gid, _RIGHT, _EDGE, 0.55)


def _bar_part(s, sections, gid):
    _bar_gradient(s, gid)
    for x0, x1, r in sections:
        _bar_section(s, x0, x1, r, gid)


#: The stepped shaft of the rotary rough / contour diagrams: (x0, x1, radius).
_SHAFT = ((0.0, 18.0, 7.0), (18.0, 40.0, 11.0), (40.0, BAR_L, 7.0))


def _rotary_rough():
    """Layers of one radius, each ringing the bar, stepping in to the part."""
    s = _Scene()
    _bar_stock(s, "piccola-rr-stock")
    _bar_part(s, _SHAFT, "piccola-rr-part")
    step = 4.0
    outer, inner = 12.0, 9.0
    # the outer layer runs the whole bar: everything under it is stock
    for k in range(int(BAR_L / step) + 1):
        x = min(BAR_L - 1.0, 1.0 + k * step)
        s.cut(_bar_arc(x, outer))
    # the next layer in: only where the part is lower than it
    for x0, x1, r in _SHAFT:
        if r < inner:
            x = x0 + 1.5
            while x < x1 - 1.0:
                s.cut(_bar_arc(x, inner))
                x += step
    # lift between the layers, over the big section
    s.link([(41.5, 0.0, inner), (41.5, 0.0, outer + 3.0), (29.0, 0.0, outer + 3.0)])
    s.tool(5.5, 0.0, inner, h=13.0, r=1.8)
    return s.render(labels=[
        ((25.0, outer * math.cos(math.radians(-30)), outer * math.sin(math.radians(-30))),
         ["Rings at one", "radius"], (8, 128), "start"),
        ((49.5, inner * math.cos(math.radians(60)), inner * math.sin(math.radians(60))),
         ["Next layer: only", "where stock is left"], (254, 24), "end"),
        ((5.5, 0.0, inner + 13.0), ["End mill"], (8, 30), "start"),
    ], use=_USE["rotary rough"])


def _bulb_r(x):
    """The surface rough / finish part: a spindle that swells in the middle."""
    return 6.0 + 4.5 * math.sin(math.pi * x / BAR_L)


def _bulb_part(s, gid, n=40):
    """The spindle as one smooth solid: it never narrows and widens again, so
    the hull of its rims is its outline. Its near end faces the viewer."""
    rims = []
    for k in range(n + 1):
        x = BAR_L * k / n
        rims += _bar_rim(x, _bulb_r(x), 36)
    _bar_gradient(s, gid)
    s.poly(_hull(rims), f"url(#{gid})", _PART_EDGE, 1.0)
    s.poly(_bar_rim(BAR_L, _bulb_r(BAR_L)), _PART_RIGHT, _PART_EDGE, 1.0)


def _surface_rough():
    """Layers that keep the part's own shape, out from it, down to a skin."""
    s = _Scene()
    _bar_stock(s, "piccola-sr-stock")
    _bulb_part(s, "piccola-sr-part")
    step = 4.0
    for off in (5.0, 1.2):
        x = 1.0
        while x < BAR_L:
            r = _bulb_r(x) + off
            if r < BAR_STOCK_R - 0.4:
                s.cut(_bar_arc(x, r))
            x += step
    s.tool(4.0, 0.0, _bulb_r(4.0) + 5.0, h=12.0, r=1.8)
    xm = 30.0
    a = math.radians(-30)
    return s.render(labels=[
        ((xm, (_bulb_r(xm) + 1.2) * math.cos(a), (_bulb_r(xm) + 1.2) * math.sin(a)),
         ["Last layer: an", "even skin"], (8, 128), "start"),
        ((49.0, 0.0, _bulb_r(49.0) + 5.0), ["Layers follow", "the part's shape"], (254, 24), "end"),
        ((4.0, 0.0, _bulb_r(4.0) + 17.0), ["End mill"], (8, 30), "start"),
    ], use=_USE["surface rough"])


def _rotary_finish():
    """One spiral round the part, following its profile, with a ball mill."""
    s = _Scene()
    _bulb_part(s, "piccola-rf-part")
    pitch, lift = 3.0, 0.35
    turns = BAR_L / pitch
    run, n = [], int(turns * 72)
    for k in range(n + 1):
        t = k / n
        x = t * BAR_L
        phi = 2 * math.pi * turns * t
        r = _bulb_r(x) + lift
        if _bar_visible(phi):
            run.append((x, r * math.cos(phi), r * math.sin(phi)))
        elif run:
            if len(run) > 1:
                s.cut(run)
            run = []
    if len(run) > 1:
        s.cut(run)
    xt = 21.0
    s.ball_tool(xt, 0.0, _bulb_r(xt) + lift, h=10.0, r=1.6)
    a = math.radians(60)
    return s.render(labels=[
        ((42.0, (_bulb_r(42.0) + lift) * math.cos(a), (_bulb_r(42.0) + lift) * math.sin(a)),
         ["One spiral as", "the part turns"], (254, 24), "end"),
        ((xt, 0.0, _bulb_r(xt) + 11.0), ["Ball end mill"], (8, 30), "start"),
    ], use=_USE["lathe"])


#: The rotary contour part: a shaft with a raised rib to finish the walls of.
_RIB = ((0.0, 24.0, 7.0), (24.0, 36.0, 12.0), (36.0, BAR_L, 7.0))


def _cut_off():
    """A groove just outside each end of the part, ring by ring to a core."""
    s = _Scene()
    _bar_stock(s, "piccola-co-stock")
    gid = "piccola-co-part"
    _bar_gradient(s, gid)
    x0, x1, r_part = 12.0, 48.0, 9.0
    tool_r, gap, core = 1.0, 0.8, 3.0
    xa, xb = x0 - gap - tool_r, x1 + gap + tool_r
    radii = []
    r = BAR_STOCK_R + 0.3
    while r > core:
        radii.append(r)
        r -= 1.2
    radii.append(core)
    # the far groove's rings sit behind the part, the near groove's in front
    for r in radii:
        s.cut(_bar_arc(xa, r))
    _bar_section(s, x0, x1, r_part, gid)
    for r in radii:
        s.cut(_bar_arc(xb, r))
    s.tool(xb, 0.0, core + 0.1, h=12.0, r=tool_r)
    a = math.radians(55)
    b = math.radians(-25)
    return s.render(labels=[
        ((xa, BAR_STOCK_R * math.cos(a), BAR_STOCK_R * math.sin(a)),
         ["A groove just", "outside each end"], (8, 30), "start"),
        ((xb, core * math.cos(b), core * math.sin(b)),
         ["Down to a core", "to saw through"], (8, 128), "start"),
    ], use=_USE["cut off"])


def _rotary_contour():
    """A ring against each wall of the rib, at every radius down it."""
    s = _Scene()
    gid = "piccola-rc-part"
    _bar_gradient(s, gid)
    tool_r = 1.8
    radii = [11.0, 10.0, 9.0, 8.0, 7.2]
    # far wall's rings go before the rib, which stands in front of them; the
    # near wall's after it, over the shaft that runs on towards the viewer
    (a0, a1, ra), (b0, b1, rb), (c0, c1, rc) = _RIB
    _bar_section(s, a0, a1, ra, gid)
    for r in radii:
        s.cut(_bar_arc(b0 - tool_r, r + 0.15))
    _bar_section(s, b0, b1, rb, gid)
    _bar_section(s, c0, c1, rc, gid)
    for r in radii:
        s.cut(_bar_arc(b1 + tool_r, r + 0.15))
    s.tool(36.0 + tool_r, 0.0, 9.15, h=11.0, r=tool_r)
    a = math.radians(20)
    return s.render(labels=[
        ((24.0 - tool_r, 10.15 * math.cos(a), 10.15 * math.sin(a)), ["A pass along", "the wall"], (8, 30), "start"),
        ((36.0 + tool_r, 7.35 * math.cos(math.radians(-20)), 7.35 * math.sin(math.radians(-20))),
         ["Steps down", "the wall"], (8, 128), "start"),
    ], use=_USE["rotary contour"])


#: The *drill* diagram is a side section, drawn straight on the canvas: a
#: peck cycle is about depth, and depth reads best face on. Canvas units.
DRILL_TOP, DRILL_BOT = 52.0, 140.0       #: the part's top face and bottom edge
DRILL_X0, DRILL_X1 = 8.0, 172.0          #: the part's section, left and right
DRILL_HX, DRILL_HW = 92.0, 10.0          #: the hole's centre and half width
DRILL_DEPTH = 74.0                       #: to the shoulder of the drill point
DRILL_POINT = DRILL_HW * 0.6             #: the point's cone (118 degrees)
DRILL_RETRACT = 42.0                     #: where the drill backs out to
#: The pecks, as fractions of the full depth, and where each is drawn across
#: the hole -- in the machine they share one line; side by side, each shows.
DRILL_PECKS = (0.36, 0.68, 1.0)
DRILL_COLS = (85.0, 91.0, 97.0)
DRILL_OUT = 2.6                          #: the way back out, beside each peck
#: The section's look: a light grey cut face, hatched.
_SECTION = "#d9dcdf"
_HATCH = "#b4b9bf"


def _drill():
    """Peck drilling, in a side section through the hole.

    The part is cut through the hole's centre and seen face on. The drill
    goes down a little (feed, blue), backs right out above the hole to clear
    the chips (rapid, green), comes back down, cuts a little deeper, and so on
    to full depth. A dashed line marks each peck's depth, so "deeper each
    time" can be read straight off the picture.
    """
    top, hx, hw = DRILL_TOP, DRILL_HX, DRILL_HW
    full = top + DRILL_DEPTH
    parts = [
        '<defs><pattern id="piccola-drill-hatch" patternUnits="userSpaceOnUse"'
        ' width="5" height="5" patternTransform="rotate(45)">'
        f'<rect width="5" height="5" fill="{_SECTION}"/>'
        f'<line x1="0" y1="0" x2="0" y2="5" stroke="{_HATCH}" stroke-width="0.8"/>'
        '</pattern></defs>']
    # the cut part, with the hole taken out of it (even-odd)
    hole = [(hx - hw, top), (hx - hw, full), (hx, full + DRILL_POINT),
            (hx + hw, full), (hx + hw, top)]
    outline = [(DRILL_X0, top), (DRILL_X1, top), (DRILL_X1, DRILL_BOT), (DRILL_X0, DRILL_BOT)]

    def sub(pts):
        return " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                        for i, (x, y) in enumerate(pts)) + " Z"
    parts.append(f'<path d="{sub(outline)} {sub(hole)}" fill="url(#piccola-drill-hatch)"'
                 f' fill-rule="evenodd" stroke="{_PART_EDGE}" stroke-width="1.0"/>')
    parts.append(f'<path d="{sub(hole)}" fill="#f6f7f9" stroke="{_PART_EDGE}" stroke-width="1.0"/>')

    # each peck's depth, dashed across to its label
    depths = [top + f * DRILL_DEPTH for f in DRILL_PECKS]
    names = ("1st peck", "2nd peck", "Full depth")
    for y, name in zip(depths, names):
        parts.append(f'<path d="M{hx + hw + 2:.1f} {y:.1f} L{DRILL_X1 + 8:.1f} {y:.1f}"'
                     f' stroke="{_LEAD}" stroke-width="0.6" stroke-dasharray="2 2"/>')
        parts.append(_text(DRILL_X1 + 11, y + 3.4, [name]))
    # the retract height, above the hole
    r = DRILL_RETRACT
    parts.append(f'<path d="M{hx + hw + 2:.1f} {r:.1f} L{DRILL_X1 + 8:.1f} {r:.1f}"'
                 f' stroke="{_LINK}" stroke-width="0.6" stroke-dasharray="2 2"/>')
    parts.append(_text(DRILL_X1 + 11, r - 4, ["Back out to", "clear chips"]))

    # the path: feed down (blue), rapid back out (green), across, down again
    def seg(x0, y0, x1, y1, color, width, dash=""):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<path d="M{x0:.1f} {y0:.1f} L{x1:.1f} {y1:.1f}" stroke="{color}"'
                     f' stroke-width="{width}" fill="none" stroke-linecap="round"{da}/>')

    prev = top
    for i, (x, y) in enumerate(zip(DRILL_COLS, depths)):
        # in from the retract height: rapid down to just above the last
        # peck's bottom, then feed on down to this one's
        if i:
            seg(x, r, x, prev - 2.0, _LINK, 1.0, "2.4 1.8")
        seg(x, r if not i else prev - 2.0, x, y, _CUT, 1.8)
        if i + 1 < len(depths):
            ox = x + DRILL_OUT
            seg(x, y, ox, y, _LINK, 1.0, "2.4 1.8")
            seg(ox, y, ox, r, _LINK, 1.0, "2.4 1.8")
            seg(ox, r, DRILL_COLS[i + 1], r, _LINK, 1.0, "2.4 1.8")
        prev = y
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{_CUT}"/>')

    # the drill, backed out above the hole
    tip = r - 3.0
    w = hw * 0.9
    parts.append(f'<path d="M{hx - w:.1f} 6 L{hx + w:.1f} 6 L{hx + w:.1f} {tip - DRILL_POINT:.1f}'
                 f' L{hx:.1f} {tip:.1f} L{hx - w:.1f} {tip - DRILL_POINT:.1f} Z" fill="{_TOOL}"/>')
    # its flutes, a couple of slanted lines, so it reads as a drill
    for k in range(3):
        y0 = 12 + k * 7
        parts.append(f'<path d="M{hx - w:.1f} {y0 + 5:.1f} L{hx + w:.1f} {y0:.1f}"'
                     f' stroke="{_TOOL_TOP}" stroke-width="1.4"/>')
    parts.append(_callout((hx - w, 18), ["Drill bit"], 8, 20, "start"))
    parts.append(_text(DRILL_X0 + 4, DRILL_BOT - 5, ["Cut part"], _NOTE))
    parts.append(_use_for(_USE["drill"]))
    return "".join(parts)


#: The two-step diagrams (*register*, *flip*) show the stock twice, before
#: and after it is turned over. A small block, W x D x H, and the second copy
#: shifted by (STEP_GAP, -STEP_GAP): under this projection that moves it
#: straight across the screen, so the two sit side by side.
STEP_W, STEP_D, STEP_H = 34.0, 24.0, 9.0
STEP_GAP = 37.0
#: The machine bed under the stock in *register*, and how far it reaches past
#: the stock all round.
BED_H, BED_MARGIN = 3.0, 5.0
#: Registration holes: radius, and how far in from each end.
REG_R, REG_IN = 1.8, 6.0
#: Axis colours of the origin marker: X red, Y green, Z blue, as CAD draws it.
AXIS_X, AXIS_Y, AXIS_Z = "#d64541", "#2f9e44", "#2f6fd6"
_PIN = "#4a525b"


def _block(s, ox, oy, z0=0.0):
    """The stock, at (ox, oy), its bottom at z0."""
    s.box(ox, oy, z0, ox + STEP_W, oy + STEP_D, z0 + STEP_H,
          _PART_TOP, _PART_LEFT, _PART_RIGHT, _PART_EDGE)


def _turn_arrow(s, a, b, rise=12.0):
    """A curved arrow over the top, from a to b: "turn it over"."""
    pts = []
    for i in range(25):
        t = i / 24
        pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                    a[2] + (b[2] - a[2]) * t + rise * math.sin(math.pi * t)))
    s.cut(pts, CUT_W * 2.4)
    end, back = pts[-1], pts[-3]
    d = [end[k] - back[k] for k in range(3)]
    n = math.sqrt(sum(v * v for v in d)) or 1.0
    d = [v / n for v in d]
    side = (d[1], -d[0], 0.0)                   # across the arrow, flat
    for sgn in (1, -1):
        s.cut([(end[0] - d[0] * 4 + side[0] * 2.4 * sgn,
                end[1] - d[1] * 4 + side[1] * 2.4 * sgn,
                end[2] - d[2] * 4 + 1.6), end], CUT_W * 2.4)
    return pts


def _face_word(s, text, x, y, z, size=5.5):
    """A word lying flat on a horizontal face, centred at (x, y, z).

    The text is mapped onto the face by an SVG matrix built from this
    projection: along the word is +x, down the letters is +y.
    """
    ox, oy = _proj(x, y, z)
    s._pts.append((ox, oy))
    s.parts.append(
        f'<text transform="matrix({_COS30:.4f} {_SIN30:.4f} {-_COS30:.4f} {_SIN30:.4f} {ox:.2f} {oy:.2f})"'
        f' x="0" y="{size * 0.35:.2f}" text-anchor="middle" font-weight="700"'
        f' font-family="system-ui, sans-serif" font-size="{size}" fill="#8c939b"'
        f' letter-spacing="0.6">{text}</text>')


def _origin_marker(s, x, y, z, size=7.0):
    """X, Y, Z arrows from a corner, and a dot at the corner itself."""
    for (dx, dy, dz), c in (((size, 0, 0), AXIS_X), ((0, -size, 0), AXIS_Y),
                            ((0, 0, size), AXIS_Z)):
        e = (x + dx, y + dy, z + dz)
        s.line([(x, y, z), e], c, 1.1)
        # a small head: two short strokes back from the tip
        if dz:
            s.line([(e[0] - 1.0, e[1] + 1.0, e[2] - 1.8), e, (e[0] + 1.0, e[1] - 1.0, e[2] - 1.8)], c, 1.1)
        else:
            ux, uy = (dx / size, dy / size)
            s.line([(e[0] - ux * 1.8, e[1] - uy * 1.8, e[2] + 1.0), e,
                    (e[0] - ux * 1.8, e[1] - uy * 1.8, e[2] - 1.0)], c, 1.1)
    s.poly(_circle(x, y, 1.3, z, 16), "#ffffff", _TXT, 0.8)


def _register():
    """Registration holes, in two steps, for a part machined from both sides.

    1: holes drilled right through the stock and on into the bed under it.
    2: the stock turned over; pins in the bed come up through the same
    holes, so it sits exactly where it was. The holes are mirror-placed
    about the middle, which is why they still meet after the turn.
    """
    s = _Scene()
    g = STEP_GAP
    holes = [REG_IN, STEP_W - REG_IN]
    hy = STEP_D / 2
    for k, (ox, oy) in enumerate(((0.0, 0.0), (g, -g))):
        m = BED_MARGIN
        s.box(ox - m, oy - m, -BED_H, ox + STEP_W + m, oy + STEP_D + m, 0.0,
              "#d9dde2", "#b9bec5", "#c9ced4", "#8c939b")
        _block(s, ox, oy)
        for hx in holes:
            s.hole(ox + hx, oy + hy, STEP_H, REG_R)
        if k == 0:
            # drilled through, into the bed: one done, the drill on the other
            x, y = ox + holes[0], oy + hy
            s.cut([(x, y, STEP_H), (x, y, -BED_H + 0.5)], CUT_W * 2.4)
            s.drill_tool(ox + holes[1], oy + hy, STEP_H + 3.0, h=9.0, r=1.8)
        else:
            for hx in holes:                   # pins up through the holes
                x, y = ox + hx, oy + hy
                s.poly(_circle(x, y, REG_R * 0.8, STEP_H + 3.0, 16), _PIN, _PIN, 0.5)
                s.line([(x, y, STEP_H), (x, y, STEP_H + 3.0)], _PIN, REG_R * 1.6, cap="butt")
                s.poly(_circle(x, y, REG_R * 0.8, STEP_H + 3.0, 16), "#6a737d", _PIN, 0.5)
    _turn_arrow(s, (STEP_W * 0.75, STEP_D * 0.2, STEP_H + 6), (g + STEP_W * 0.25, -g + STEP_D * 0.2, STEP_H + 6))
    return s.render(labels=[
        ((holes[0], hy, STEP_H * 0.4), ["1. Drill holes", "through, into", "the bed"], (8, 30), "start"),
        ((g + holes[1], -g + hy, STEP_H + 3.0), ["2. Flip it: pins", "in the holes", "line it up"], (254, 22), "end"),
    ], use=_USE["register"])


def _flip():
    """Before and after turning the part over, and where the origin goes.

    Left: the stock as it is, the origin marker on its top front-left
    corner. Right: turned over about its Y axis -- left and right change
    places and the bottom comes up. The origin is set again on the same
    corner of the stock (top front-left), which is now a different corner of
    the part; a ring marks where the old origin corner has gone.
    """
    s = _Scene()
    g = STEP_GAP
    _block(s, 0.0, 0.0)
    _block(s, g, -g)
    # which side is up: before, the top; after, what was the bottom
    _face_word(s, "TOP", STEP_W / 2, STEP_D / 2, STEP_H)
    _face_word(s, "BOTTOM", g + STEP_W / 2, -g + STEP_D / 2, STEP_H)
    # the old origin corner (0, D, H) turned over about the Y axis through
    # the block's middle: x -> W - x, z -> H - z
    ring = (g + STEP_W, -g + STEP_D, 0.0)
    s.poly(_circle(ring[0], ring[1], 3.0, ring[2], 24), "#fff4e0", _SEL, 1.3)
    _origin_marker(s, 0.0, STEP_D, STEP_H)
    _origin_marker(s, g, -g + STEP_D, STEP_H)
    _turn_arrow(s, (STEP_W * 0.6, STEP_D * 0.3, STEP_H + 4), (g + STEP_W * 0.4, -g + STEP_D * 0.3, STEP_H + 4))
    return s.render(labels=[
        ((0.0, STEP_D, STEP_H), ["Origin", "before"], (8, 112), "start"),
        ((g, -g + STEP_D, STEP_H), ["New origin", "after flip"], (62, 134), "start"),
        (ring, ["Old origin is now here"], (254, 146), "end"),
        ((STEP_W / 2 + g / 2, STEP_D * 0.3 - g / 2, STEP_H + 16), ["Turn it over"], (128, 20), "start"),
    ], use=_USE["flip"])


#: The *area mode* picture: what Clear, Trace and Surface each do, one row
#: per mode -- a small scene on the left, its name and what it is for on the
#: right. Taller than the operation diagrams, so it has its own canvas.
MODES_W, MODES_H = 260, 318
MODES_ROW = 104.0
#: The boundary all three rows use: a rounded rectangle on the stock's top.
MODE_BOX = (20.0, 12.0, 80.0, 60.0, 8.0)

#: Each mode's name and its lines: what it does, then "Use for".
_MODE_TEXT = {
    "clear": ("Clear", ["Empties the whole area,", "layer by layer, down to", "a flat floor."],
              "pockets and recesses."),
    "trace": ("Trace", ["Follows the edge of the", "area: on the line, or", "just inside or outside."],
              "grooves, chamfers and cutting along a line."),
    "surface": ("Surface", ["Rides the 3D shape inside", "the area. Pattern: Linear,", "Offset or Scallop."],
                "finishing curved surfaces."),
}


def _stock_top_with(s, rims):
    """The stock's sides, then its top with openings for *rims* (outlines)."""
    zt = SZ
    s.poly([(0, SY, zt), (SX, SY, zt), (SX, SY, 0), (0, SY, 0)], _LEFT, _EDGE, 1.0)
    s.poly([(SX, 0, zt), (SX, SY, zt), (SX, SY, 0), (SX, 0, 0)], _RIGHT, _EDGE, 1.0)
    if rims:
        s.poly_hole([(0, 0, zt), (SX, 0, zt), (SX, SY, zt), (0, SY, zt)],
                    [[(x, y, zt) for x, y in rim] for rim in rims], _TOP, _EDGE, 1.0)
    else:
        s.poly([(0, 0, zt), (SX, 0, zt), (SX, SY, zt), (0, SY, zt)], _TOP, _EDGE, 1.0)


def _mode_clear():
    """Clear: the area emptied to a flat floor, its passes on the floor."""
    s = _Scene()
    _cleared(s, MODE_BOX)
    rim = _rr(MODE_BOX)
    _stock_top_with(s, [rim])
    s.line([(x, y, SZ + 0.2) for x, y in rim + rim[:1]], _SEL, 1.4)
    return s


def _mode_trace():
    """Trace: a groove along the boundary, part cut, the tool on its way."""
    s = _Scene()
    _stock_top_with(s, [])
    rim = _rr(MODE_BOX)
    z = SZ - 0.3
    k = int(len(rim) * 0.62)
    done, todo = rim[:k + 1], rim[k:] + rim[:1]
    s.line([(x, y, z) for x, y in done], TRACE_GROOVE, 3.2)     # the groove
    s.cut([(x, y, z) for x, y in done], CUT_W * 2.4)            # the path in it
    s.line([(x, y, SZ + 0.2) for x, y in todo], _SEL, 1.4)       # still to cut
    tx, ty = done[-1]
    s.tool(tx, ty, z, h=16.0, r=2.4)
    return s


def _mode_surface():
    """Surface: passes riding the dome inside the area, a ball on one of them.

    Points on the far side of the dome that face away from the eye are left
    out: the view direction of this projection is (1, 1, 1), and a pass that
    carried on over the back would be drawn on top of the dome's front.
    """
    s = _Scene()
    s.part(dome=False)
    s.solid_dome("piccola-mode-dome")
    rho = (DOME_R ** 2 + DOME_H ** 2) / (2 * DOME_H)       # the cap's sphere
    zc = PZ + DOME_H - rho

    def faces_eye(x, y, z):
        if math.hypot(x - DOME_CX, y - DOME_CY) >= DOME_R:
            return True                                   # the flat slab
        return (x - DOME_CX) + (y - DOME_CY) + (z - zc) > 0

    y = PY0 + 4.0
    last = None
    while y < PY1 - 2.0:
        run = []
        for i in range(61):
            x = PX0 + 2.0 + (PX1 - PX0 - 4.0) * i / 60
            z = _dome_z(x, y) + 0.3
            if faces_eye(x, y, z):
                run.append((x, y, z))
            elif len(run) > 1:
                s.cut(run, CUT_W * 2.0)
                run = []
            else:
                run = []
        if len(run) > 1:
            s.cut(run, CUT_W * 2.0)
        last = y
        y += 5.0
    bx = DOME_CX + DOME_R * 0.45
    s.ball_tool(bx, last, _dome_z(bx, last), h=14.0, r=2.6)
    return s


def _area_modes():
    """The three area modes, one row each: scene left, words right."""
    import textwrap
    out = []
    for i, (key, scene) in enumerate((("clear", _mode_clear()),
                                      ("trace", _mode_trace()),
                                      ("surface", _mode_surface()))):
        top = 6.0 + i * MODES_ROW
        if i:
            out.append(f'<path d="M6 {top - 3:.1f} L{MODES_W - 6} {top - 3:.1f}"'
                       f' stroke="#dfe3e8" stroke-width="0.8"/>')
        # the surface scene is the small part, not the whole stock, so it
        # fits nearly twice as large: its lines are thinned to the others'
        out.append(scene.render(fit=(4.0, top + 2.0, 112.0, top + MODES_ROW - 10.0),
                                stroke_scale=0.73))
        name, lines, use = _MODE_TEXT[key]
        x = 120.0
        out.append(f'<text x="{x:.1f}" y="{top + 16:.1f}" fill="{_TXT}" font-weight="700"'
                   f' {_FONT.replace("9.6", "12")}>{name}</text>')
        out.append(_text(x, top + 32, lines))
        use_lines = textwrap.wrap("Use for: " + use, 27)
        y = top + 32 + len(lines) * _LINE + 5
        for j, line in enumerate(use_lines):
            if j == 0:
                rest = line[len("Use for:"):]
                out.append(f'<text x="{x:.1f}" y="{y:.1f}" fill="{_TXT}" {_FONT}>'
                           f'<tspan font-weight="600">Use for:</tspan>{rest}</text>')
            else:
                out.append(_text(x, y + j * _LINE, [line]))
    return "".join(out)


#: operation name (as the menu spells it) -> diagram body.
OP_ART = {
    "rough": _rough(),
    "pocket": _pocket(),
    "area": _area(),
    "level": _level(),
    "outline": _outline(),
    "contour": _contour(),
    "trace": _trace(),
    "helical": _helical(),
    "scallop": _scallop(),
    "drill": _drill(),
    "register": _register(),
    "flip": _flip(),
    # rotary (4-axis) operations
    "rotary rough": _rotary_rough(),
    "surface rough": _surface_rough(),
    "lathe": _rotary_finish(),
    "rotary contour": _rotary_contour(),
    "cut off": _cut_off(),
    # not an operation: the area op's Mode menu (ui/cam_mode_tips.py)
    "area-mode": _area_modes(),
}

#: Diagrams drawn on a canvas other than VIEWBOX.
OP_BOXES = {
    "area-mode": f"0 0 {MODES_W} {MODES_H}",
}


def op_art_js() -> str:
    """JS publishing the diagrams for the tooltip panel to pick up."""
    js = """
(function () {
  var REG = %(reg)s, VIEWBOX = %(viewbox)s, ART = %(art)s, BOXES = %(boxes)s;
  // A plain global rather than an import: this module and the panel that draws
  // these are injected separately, and neither may depend on the other's order.
  window[REG] = { viewBox: VIEWBOX, art: ART, boxes: BOXES };
})();
""" % {
        "reg": json.dumps(REGISTRY),
        "viewbox": json.dumps(VIEWBOX),
        "art": json.dumps(OP_ART),
        "boxes": json.dumps(OP_BOXES),
    }
    assert "%" not in js, "unescaped % survived formatting in op_art_js()"
    return js
