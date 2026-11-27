"""
ui/cam_origin.py
Reworks EasyTrace's Origin Position controls: all four board corners plus the
centre, applied on click, with the active one shown.

What upstream ships
-------------------
Two preset buttons — *Bottom-Left* and *Center* — and a separate *Apply Origin*
that saves whatever the presets or the X/Y offset fields left behind. The other
three corners are simply absent, and nothing shows where the origin currently
is: both presets stay grey after being pressed, and their status line says
"(not saved)" until Apply is pressed.

What this installs instead
--------------------------
* A captioned group ("Origin Position") holding all five spots, laid out the way
  they sit on the board::

      Top-Left      Top-Right
      Bottom-Left   Bottom-Right
             Center

  Upstream's two buttons are *moved* into that grid rather than replaced, so
  their own handlers keep running; the three new corners are ours.

* Clicking a spot applies it. Upstream's own commit — ``applyOffsetAndSetOrigin``,
  the method Apply Origin called — runs as part of the click, so Apply Origin
  itself has nothing left to do and is hidden, and *Reset Unsaved Changes* goes
  with it: there is no unsaved state left for it to undo, and re-picking a spot
  is the same gesture as reverting to one.

  Applying was never the difference between "the origin moved" and "it did not":
  the toolpath pipeline reads ``scene.transform.origin`` (cam-core.js
  ``getTransforms()`` builds its machine matrix from it), which the presets
  already set. Committing rebases the offset fields on the new origin, so they
  read 0 and the pick becomes the reference the X/Y offsets are measured from.

  The X/Y offset fields are unaffected and still live — they move the real
  origin as they are typed, and Enter commits them as before. A typed offset
  therefore reads as "this spot, plus X/Y", with no button highlighted, because
  the origin is no longer on a corner.

* The active spot is drawn in the accent colour (``btn--primary`` — the blue
  Apply Origin used to have). Which one that is comes from comparing the live
  origin against the board's corners on every transform change, not from
  remembering the last click: type an offset, rotate the board or press Reset
  and the highlight clears itself, because the origin genuinely is not on a
  corner any more.

* Opening the first file puts the origin on the **bottom-left** corner and
  lights that button, rather than leaving it at (0,0) — which for a fresh
  session is a corner of the empty placeholder board, not of the real one.
  Bottom-left is the GRBL convention and the corner the machine's own axes run
  away from.

  A pick then stays with the board: if a second file grows the bounds while the
  origin is still exactly where this module put it, the origin follows the
  corner, so "the lit button is where the origin is" keeps holding. The moment
  the origin is somewhere it was not put — a typed offset, another spot, a
  rotation — it is left alone for good.

* The whole Board Placement panel is disabled until at least one isolation,
  clearing, drill or cutout file is open (``GATE_TYPES``), with the reason on
  each control as a tooltip: the five spots, the X/Y offset fields, Board
  Rotation and Mirror Geometry (``GATED_IDS``).

  The panel says where the board sits and which way round it is, so with no
  board every control in it edits a placeholder — upstream falls back to an
  empty 100×100 default, and a corner, rotation or mirror set against *that*
  quietly survives into the first real file. Removing the last file disables the
  panel again and drops the highlight.

Corners come from ``scene.getVisualBounds()`` — post rotation and mirror, the
same source ``setOriginToBottomLeft()`` and ``centerOriginOnBoard()`` use, so a
rotated board still highlights its own visible corners. With no bounds yet the
click is refused with a status message rather than moving the origin to a
garbage coordinate.

Neither half needs telling when a file arrives: every path that adds or removes
an operation ends in ``core.updateBoardBounds()``, which calls
``setBoardBounds()`` / ``initializeEmptyBoardBounds()`` and so notifies the
transform listener this module already holds.

Nothing under ``vendor/`` is touched: the buttons are built and wired from the
outside, through the tool's own scene and UI API.
"""

from __future__ import annotations

#: Caption above the group.
LABEL = "Origin Position"

#: The five spots, in grid order (2 columns, row-major). ``pick`` is JS reading
#: a corner off the visual bounds; ``adopt`` names the upstream button to move
#: into the grid instead of creating a new one; ``wide`` spans both columns.
SPOTS = (
    ("top-left",     "Top-Left",     "[b.minX, b.maxY]",       None,                     False),
    ("top-right",    "Top-Right",    "[b.maxX, b.maxY]",       None,                     False),
    ("bottom-left",  "Bottom-Left",  "[b.minX, b.minY]",       "bottom-left-origin-btn", False),
    ("bottom-right", "Bottom-Right", "[b.maxX, b.minY]",       None,                     False),
    ("center",       "Center",       "[b.centerX, b.centerY]", "center-origin-btn",      True),
)

#: How close the origin must sit to a corner for that button to read as active,
#: in mm. Well below anything that matters when placing a board (0.1 µm), and
#: far above the float noise of the transform maths.
_EPSILON_MM = 1e-4

#: Operation types that count as "there is a board to place". Every type the
#: tool can still create — isolation, copper clearing, drilling, board cutout —
#: listed explicitly rather than counting operations, so the removed stencil
#: type could not re-enable the controls if it ever came back.
GATE_TYPES = ("isolation", "clearing", "drill", "cutout")

#: Everything in the Board Placement panel that only means something once a
#: board exists — every control in it except the origin buttons, which are built
#: here and gated by element rather than by id.
#:
#: The panel describes where the board sits and which way round it is, so with
#: no board every one of these edits a placeholder: `getVisualBounds()` falls
#: back to an empty 100x100 default, and a rotation or mirror set against *that*
#: silently carries into the first real file.
#:
#: The gate lives in this module because it already holds the scene transform
#: listener that notices files arriving; ``ui/cam_rotation.py`` owns *when* a
#: rotation commits, not whether it can be typed. ``#apply-rotation-btn`` is
#: deliberately absent: it is hidden but still clicked programmatically by that
#: module, and a disabled button ignores ``click()``. Gating the field it reads
#: is what actually blocks input.
GATED_IDS = (
    "x-offset", "y-offset",             # Origin Offset
    "rotation-angle", "reset-rotation-btn",   # Board Rotation
    "mirror-x-toggle", "mirror-y-toggle",     # Mirror Geometry
)

#: Shown on every disabled control in the panel, so the reason is one hover away
#: rather than a control that simply does not respond.
GATE_HINT = "Add an isolation, clearing, drill or cutout file first"

#: Where the origin goes on its own once a board exists. Bottom-left is the GRBL
#: convention and the corner the machine's own axes run away from, so it is both
#: the sane default and the one a job is usually set up against — better than
#: leaving the origin at (0,0), which for a fresh session is a corner of the
#: empty placeholder board rather than of the real one.
DEFAULT_SPOT = "bottom-left"

_LABEL_ID = "piccola-origin-label"
_GRID_ID = "piccola-origin-grid"
_WIDE_CLASS = "piccola-origin-wide"
_ACTIVE_CLASS = "btn--primary"
_IDLE_CLASS = "btn--secondary"


def css() -> str:
    """Styling for the reworked group."""
    return "\n".join([
        # The old action row goes whole — it held exactly Apply Origin and
        # Reset Unsaved Changes, and neither survives the rework (see the
        # module docstring). Hiding the row rather than the two buttons avoids
        # leaving an empty grid and its margin behind.
        ".origin-buttons:has(> #apply-set-origin-btn)"
        " { display: none !important; }",
        # Fallback for engines without :has().
        "#apply-set-origin-btn, #reset-origin-btn { display: none !important; }",
        f"#{_GRID_ID} .{_WIDE_CLASS} {{ grid-column: 1 / -1; }}",
    ])


def origin_buttons_js() -> str:
    """JS that builds and wires the Origin Position group."""
    spots = ",".join(
        "{{key:{k},label:{l},pick:function(b){{return {p};}},adopt:{a},wide:{w}}}".format(
            k=_q(key), l=_q(label), p=pick,
            a=_q(adopt) if adopt else "null",
            w="true" if wide else "false")
        for key, label, pick, adopt, wide in SPOTS
    )
    gate_types = ",".join(_q(t) for t in GATE_TYPES)
    gated_ids = ",".join(_q(i) for i in GATED_IDS)
    return f"""
(function () {{
  var SPOTS = [{spots}];
  var LABEL = {_q(LABEL)};
  var LABEL_ID = {_q(_LABEL_ID)};
  var GRID_ID = {_q(_GRID_ID)};
  var WIDE = {_q(_WIDE_CLASS)};
  var ACTIVE = {_q(_ACTIVE_CLASS)};
  var IDLE = {_q(_IDLE_CLASS)};
  var EPS = {_EPSILON_MM!r};
  var GATE_TYPES = [{gate_types}];
  var GATE_HINT = {_q(GATE_HINT)};
  var DEFAULT_KEY = {_q(DEFAULT_SPOT)};
  var GATED_IDS = [{gated_ids}];

  var buttons = {{}};        // key -> element
  var installed = false;

  // Where the origin was last put by this module, and the point it went to.
  // Only used to decide whether an automatic placement is due (see autoPlace);
  // what is *lit* is always derived from the live origin, never from this.
  var lastKey = null, lastPoint = null;
  var busy = false;          // guards apply() -> setOrigin -> refresh re-entry

  function ui() {{
    try {{ return window.easytrace.getUI(); }} catch (e) {{ return null; }}
  }}
  function scene() {{ var u = ui(); return (u && u.scene) || null; }}

  // Is there a board to place? Anything else in the panel — the offset fields
  // included — is meaningless until one file is open.
  function hasBoard() {{
    var ops;
    try {{ ops = window.easytrace.core.operations; }} catch (e) {{ return false; }}
    if (!ops || !ops.length) return false;
    for (var i = 0; i < ops.length; i++) {{
      if (ops[i] && GATE_TYPES.indexOf(ops[i].type) !== -1) return true;
    }}
    return false;
  }}
  function bounds() {{
    var s = scene();
    // Same source as the tool's own presets: post rotation and mirror.
    return s ? s.getVisualBounds() : null;
  }}
  function status(msg, kind) {{
    var u = ui();
    if (u && u.setStatus) u.setStatus(msg, kind);
  }}

  // Which spot the origin is sitting on right now, or null. Derived state:
  // typing an offset or rotating the board moves the origin off the corner and
  // the highlight clears itself.
  function activeKey() {{
    var s = scene(), b = bounds();
    if (!s || !b || !hasBoard()) return null;
    var o = s.transform.origin;
    for (var i = 0; i < SPOTS.length; i++) {{
      var p = SPOTS[i].pick(b);
      if (Math.abs(p[0] - o.x) < EPS && Math.abs(p[1] - o.y) < EPS) {{
        return SPOTS[i].key;
      }}
    }}
    return null;
  }}

  function gate(el, enabled) {{
    if (!el) return;
    el.disabled = !enabled;
    // Native `disabled` already dims and blocks; the title says why.
    if (enabled) el.removeAttribute('title');
    else el.setAttribute('title', GATE_HINT);
  }}

  function spotFor(key) {{
    for (var i = 0; i < SPOTS.length; i++) {{
      if (SPOTS[i].key === key) return SPOTS[i];
    }}
    return null;
  }}
  function samePoint(a, b) {{
    return !!(a && b && Math.abs(a[0] - b[0]) < EPS && Math.abs(a[1] - b[1]) < EPS);
  }}

  // Place the origin without being asked, in exactly two cases:
  //
  //   1. A board has just appeared and nothing has been picked for it yet —
  //      the first file lands on DEFAULT_KEY rather than on whatever the origin
  //      happened to be, which for a fresh session is (0,0): a corner of the
  //      empty placeholder board, not of the real one.
  //   2. The pick is still where we put it but the corner has moved under it,
  //      which is what a second file growing the board does. Following it keeps
  //      "the lit button is where the origin is" true.
  //
  // The moment the origin is somewhere we did not put it — a typed offset,
  // another spot, a rotation — neither case matches again and the origin is
  // left alone.
  function autoPlace() {{
    if (busy) return;
    var s = scene(), b = bounds();
    if (!s || !b) return;
    if (lastKey === null) {{
      var first = spotFor(DEFAULT_KEY);
      if (first) apply(first);
      return;
    }}
    var spot = spotFor(lastKey);
    if (!spot) return;
    var corner = spot.pick(b);
    var o = s.transform.origin;
    if (samePoint([o.x, o.y], lastPoint) && !samePoint(corner, lastPoint)) {{
      apply(spot);
    }}
  }}

  // One pass over the whole group: what is usable, where the origin goes if
  // nobody has said, and what is lit. All read off live state, so nothing has
  // to be told when a file is added or removed.
  function refresh() {{
    var enabled = hasBoard();
    if (!enabled) {{ lastKey = null; lastPoint = null; }}
    else autoPlace();
    var key = enabled ? activeKey() : null;
    Object.keys(buttons).forEach(function (k) {{
      var el = buttons[k];
      var on = (k === key);
      el.classList.toggle(ACTIVE, on);
      el.classList.toggle(IDLE, !on);
      el.setAttribute('aria-pressed', on ? 'true' : 'false');
      gate(el, enabled);
    }});
    GATED_IDS.forEach(function (id) {{
      gate(document.getElementById(id), enabled);
    }});
  }}

  function apply(spot) {{
    var s = scene(), u = ui(), b = bounds();
    if (!hasBoard()) {{
      status(GATE_HINT, 'warning');
      return;
    }}
    if (!s || !u || !b) {{
      status('Cannot set origin — no board bounds yet', 'error');
      return;
    }}
    var p = spot.pick(b);
    busy = true;
    try {{
      s.setOrigin(p[0], p[1]);
      // The tool's own commit — what Apply Origin ran. Rebases the offset
      // fields on the new origin so they read 0 and Reset cannot walk it back.
      if (u.applyOffsetAndSetOrigin) u.applyOffsetAndSetOrigin();
      else if (u.updateOriginDisplay) u.updateOriginDisplay();
      lastKey = spot.key;
      lastPoint = p;
      status('Origin set to ' + spot.label.toLowerCase(), 'success');
    }} finally {{
      busy = false;
    }}
    refresh();
  }}

  function install() {{
    if (installed) return true;
    var s = scene();
    if (!s) return false;
    // Anchor on one of the tool's own preset buttons: its row is where the
    // group goes, and its presence means the sidebar markup is up.
    var anchor = null;
    for (var i = 0; i < SPOTS.length && !anchor; i++) {{
      if (SPOTS[i].adopt) anchor = document.getElementById(SPOTS[i].adopt);
    }}
    var row = anchor && anchor.parentElement;
    if (!row) return false;

    var field = document.createElement('div');
    field.className = 'property-field';
    field.setAttribute('role', 'group');
    field.setAttribute('aria-labelledby', LABEL_ID);

    var caption = document.createElement('label');
    caption.id = LABEL_ID;
    caption.textContent = LABEL;
    field.appendChild(caption);

    var grid = document.createElement('div');
    grid.className = 'origin-buttons';
    grid.id = GRID_ID;
    field.appendChild(grid);

    SPOTS.forEach(function (spot) {{
      // Move the tool's own button where there is one, so its handler and its
      // wording stay; build the missing corners ourselves.
      var el = spot.adopt ? document.getElementById(spot.adopt) : null;
      if (!el) {{
        el = document.createElement('button');
        el.type = 'button';
        el.className = 'btn btn--compact ' + IDLE;
        el.textContent = spot.label;
      }}
      if (spot.wide) el.classList.add(WIDE);
      el.addEventListener('click', function () {{ apply(spot); }});
      grid.appendChild(el);
      buttons[spot.key] = el;
    }});

    row.parentElement.insertBefore(field, row);
    // The row the presets came out of held nothing else; the next one (Apply
    // Origin + Reset Unsaved Changes) is hidden by css() above.
    if (!row.querySelector('button')) row.remove();

    // Every path that adds or removes a file ends in core.updateBoardBounds(),
    // which calls setBoardBounds()/initializeEmptyBoardBounds() and so notifies
    // this listener — that is the signal for both halves of refresh(), with no
    // polling and nothing to hook on the file side.
    try {{ s.addTransformListener(refresh); }} catch (e) {{ /* the group then
      only refreshes on click, which is the common case anyway */ }}
    refresh();

    installed = true;
    return true;
  }}

  // Both the markup and the tool's globals appear after this script runs, so
  // retry briefly rather than firing once into an empty window.
  var tries = 400;
  (function wait() {{
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();
}})();
"""


def _q(s: str) -> str:
    """Minimal JS string literal — these are all internal constants."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
