"""
ui/cam_arcs.py
Makes EasyTrace emit straight moves only, so height-map compensation applies to
every cut.

Why
---
The firmware patches Z line by line while a job runs with a height map active
(README 5.37):

    G1 moves: the Z word is always rewritten with programmedZ + interpolatedOffset
    G0 moves: rewritten only if Z is explicitly present
    G2/G3 arcs and relative-mode (G91) lines: passed through without modification

So an arc is not *approximately* compensated — it is not compensated at all, and
it holds whatever Z the preceding G1 was rewritten to. On a board that is not
perfectly flat the tool then cuts at the right depth along straights, at a frozen
wrong depth through every arc, and steps between the two at each junction. That
is the artifact.

What this does
--------------
Turns off the post-processor's arc capability, which is a switch EasyTrace
already has: ``gcode-generator.js`` linearises arcs whenever the active
processor reports ``supportsArcCommands: false``, and ``base-processor.js``
falls back from ``generateArc()`` to ``generateLinear()``. Upstream ships the
Roland processor that way, so the path is the tool's own, not ours.

It is applied by wrapping ``registerProcessor`` — the moment each processor is
registered, before anything can use it — rather than by hunting down the
instance afterwards. Every processor is cleared, not just GRBL: the picker is
pinned to GRBL anyway, and this way no stale saved value could quietly bring
arcs back.

The cost is file size, not accuracy. ``arcResolution`` is a segment *length*,
scaled by radius in the generator (halved under 2 mm, doubled over 10 mm), and
the deviation from the true arc is the sagitta, L²/8r:

    r =  1 mm, L = 0.05 mm -> 0.3 µm
    r =  5 mm, L = 0.10 mm -> 0.25 µm
    r = 20 mm, L = 0.20 mm -> 0.25 µm

Orders below the height map's own resolution. And nothing is lost in motion
quality: grbl-family firmware segments arcs internally before stepping them, so
G2/G3 in the file buys smaller files rather than smoother movement.

Kiri:Moto has the same exposure but is safe by default (``camArcEnabled: false``
in its shipped defaults). It is a visible checkbox, so the Carve preset in
ui/pages/cam.py pins it off for the same reason.
"""

from __future__ import annotations

import json

#: Segment length for linearised arcs, in mm — EasyTrace's own default, kept
#: here so the one number that decides file size against smoothness is visible
#: and tunable. The generator scales it by radius: x0.5 below 2 mm, x2 above
#: 10 mm. Passed explicitly rather than left to the `|| 0.1` fallback, so the
#: value in force is the value written here.
SEGMENT_MM = 0.1


def linearize_arcs_js() -> str:
    """JS turning arc output off and pinning the segment length."""
    return f"""
(function () {{
  var PATCH_FLAG = '__piccolaLinearizeArcs';
  var SEGMENT_MM = {json.dumps(SEGMENT_MM)};

  function patch(Cls) {{
    if (!Cls || !Cls.prototype || Cls[PATCH_FLAG]) return Cls;
    var proto = Cls.prototype;
    var register = proto.registerProcessor;
    var generate = proto.generate;
    if (typeof register !== 'function' || typeof generate !== 'function') {{
      return Cls;
    }}

    // Every processor loses its arcs as it is registered — which happens in
    // the generator's own constructor, so nothing can have used one yet.
    proto.registerProcessor = function (name, processor) {{
      try {{
        if (processor && processor.config) {{
          processor.config.supportsArcCommands = false;
          // The descriptor's copy is what getProcessorInfo() reports; the
          // emitting code reads config, but keep the two telling one story.
          if (processor.descriptor && processor.descriptor.capabilities) {{
            processor.descriptor.capabilities.supportsArcCommands = false;
          }}
        }}
      }} catch (e) {{ /* a processor we cannot read keeps its own behaviour */ }}
      return register.apply(this, arguments);
    }};

    // Segment length. Copied rather than set on the caller's object, so the
    // generator's options stay the caller's business.
    proto.generate = function (plans, options) {{
      var opts = {{}};
      for (var k in (options || {{}})) opts[k] = options[k];
      if (opts.arcResolution === undefined) opts.arcResolution = SEGMENT_MM;
      return generate.call(this, plans, opts);
    }};

    Cls[PATCH_FLAG] = true;
    return Cls;
  }}

  // Already loaded (defensive — our script normally runs first).
  if (window.GCodeGenerator) {{ patch(window.GCodeGenerator); return; }}

  var held;
  try {{
    Object.defineProperty(window, 'GCodeGenerator', {{
      configurable: true,
      enumerable: true,
      get: function () {{ return held; }},
      set: function (v) {{ held = patch(v); }}
    }});
  }} catch (e) {{
    var tries = 200;
    (function wait() {{
      if (window.GCodeGenerator) {{ patch(window.GCodeGenerator); return; }}
      if (tries-- > 0) setTimeout(wait, 50);
    }})();
  }}
}})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# Kiri:Moto: the same policy, from the other end
# ─────────────────────────────────────────────────────────────────────────────
#
# Kiri already ships arcs off (`camArcEnabled: false`) and ui/pages/cam.py pins
# it there through `_KIRI_PROCESS_DEFAULTS`, so nothing has to be rewritten on
# the way out the way EasyTrace's processors do above. What is left is the
# *control*: a checkbox in the CAM "expert" group that could turn back on the
# one thing the height map cannot compensate, leaving the panel and the exported
# file quietly disagreeing.
#
# Only the three arc rows go — `arc output` and the `arc tolerance` /
# `arc resolution` inputs that render while it is ticked. The group's other two
# settings, `skip shadow` and `rounded corners`, have nothing to do with arc
# output and stay.
#
# Why a class and not a selector
# ------------------------------
# The rows carry no ids. They could be matched on their `title` text, but that
# is English prose from `lang-en.js` — a selector that silently stops matching
# is worse here than one that never matched at all.
#
# So the rows are found through Kiri's own handle on them: `api.ui.camArcEnabled`
# is the `<input>` that `newBoolean()` returned, and its parent is the
# `.var-row` wrapper (`inputs.js`: `row.setAttribute("class", "var-row")`).
# That is language-independent and survives any reordering of the group.
#
# The marking is a class rather than `row.style.display`, because inline display
# is exactly what Kiri writes on every refresh:
#
#     row.style.display = hide ? 'none' : ''        (inputs.js, five call sites)
#
# An inline style of ours would be overwritten the first time the panel
# refreshed. A stylesheet rule carrying `!important` outranks a non-important
# inline style, so the class survives every one of those writes.

#: Class added to the rows, and the rule that hides them. Namespaced so it
#: cannot collide with anything in the vendored bundle.
#:
#: Public, and shared: ui/cam_toolchange.py marks its own row with it rather
#: than defining a second class, so one rule in the carve stylesheet covers
#: every row this app takes out of Kiri's panel.
HIDDEN_ROW_CLASS = "piccola-hidden-row"

#: Kiri UI keys for the arc controls, in `api.ui`.
_KIRI_ARC_UI_KEYS = ("camArcEnabled", "camArcTolerance", "camArcResolution")


def hidden_row_css() -> str:
    """The rule that hides a marked row, `!important` so inline display loses."""
    return f".{HIDDEN_ROW_CLASS} {{ display: none !important; }}\n"


def hide_kiri_arc_controls_js() -> str:
    """JS marking Kiri's three arc-output rows as hidden."""
    import json

    return """
(function () {
  var KEYS = %(keys)s, CLASS = %(cls)s;

  function mark() {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return false; }
    // camArcEnabled is built with the rest of the CAM menu, well after the kiri
    // namespace appears, so absence here means "not yet", not "gone".
    if (!ui || !ui.camArcEnabled) return false;

    KEYS.forEach(function (key) {
      var el = ui[key];
      if (!el) return;
      // walk out to the .var-row wrapper newBoolean()/newInput() put the
      // control in; a couple of levels is generous, the input sits directly
      // inside it.
      for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {
        if (n.classList && n.classList.contains('var-row')) {
          n.classList.add(CLASS);
          return;
        }
      }
    });
    return true;
  }

  var tries = 400;
  (function wait() {
    if (mark()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "keys": json.dumps(list(_KIRI_ARC_UI_KEYS)),
        "cls": json.dumps(HIDDEN_ROW_CLASS),
    }
