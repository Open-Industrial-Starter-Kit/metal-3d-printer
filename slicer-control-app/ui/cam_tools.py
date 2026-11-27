"""
ui/cam_tools.py
The tool libraries of the two embedded CAM tools.

Neither vendored tree is patched. Both libraries are shaped at runtime through
the tool's own API, so `vendor/` stays byte-identical and an upgrade stays a
folder swap.

* **EasyTrace** — bits its catalogue does not ship are added.
  See :data:`EXTRA_TOOLS` / :func:`add_tools_js`.
* **Kiri:Moto** — its seven-bit sample library is cut down to the two endmills
  it seeds first, and 6 mm / 3 mm metric endmills, R0.5 / R1.0 taper balls
  and a 1.0-3.175 mm ball nose set are added.
  See :data:`KIRI_DROP` / :data:`KIRI_ADD` / :func:`kiri_tools_js`.


EasyTrace
=========
The library is `vendor/easytrace/tools.json` — 41 bits, byte-identical to
upstream and staying that way. Extra bits are registered at runtime through
`ToolLibrary.addTool()`, the same public method the library's own loader uses,
so they index by id, type and operation exactly like the shipped ones and appear
in the tool dropdown for their operations.

What the pipeline does with a V-bit
-----------------------------------
Worth knowing before adding one, because it decides what the numbers below mean:
**EasyTrace does not model the taper.** `ToolLibrary.getToolDiameter()` returns
`tipDiameter` for a `v_bit` and nothing else in the tool ever reads
`geometry.angle` — the whole toolpath pipeline sees one number, `toolDiameter`,
and treats the bit as a straight cutter of that width.

So the isolation gap is planned at the tip width while the real cut widens with
depth:

    width = tipDiameter + 2 x depth x tan(angle / 2)

For the 30 degree / 0.2 mm bit at the stock -0.1 mm cut depth that is
0.2 + 2 x 0.1 x tan(15 deg) = 0.254 mm — about 27% wider than planned, and the
error grows with depth. That is the same on every V-bit in the catalogue,
including the 45 degree default; it is not specific to this addition.

Nothing here changes that. `toolDiameter` is an ordinary editable parameter
(0.01-10 mm) seeded from the tool, so the effective width can be dialled in per
job by typing it — which is the honest place for it, since only the operator
knows the depth they will actually cut. Making the pipeline compute it would
change toolpaths for every V-bit, and is a decision to take deliberately rather
than as a side effect of adding a bit.
"""

from __future__ import annotations

#: Bits registered on top of the shipped catalogue, in the library's own schema
#: (see tools.json). Cutting values are interpolated from the two neighbours
#: upstream ships — `vbit_30deg_0.1mm` (feed 100, plunge 30, 0.05 mm/pass) and
#: `vbit_45deg_0.2mm` (feed 120, plunge 40, 0.08 mm/pass): a wider tip than the
#: first takes a little more, a narrower included angle than the second leaves a
#: weaker tip section that takes a little less.
#:
#: `spindleSpeed` is required by `validateTool()` but is not what runs: the
#: machine's own 12000 RPM is applied to every operation
#: (`_EASYTRACE_SPINDLE_RPM` in ui/pages/cam.py), so the tool carries the same
#: number rather than advice that would never be followed.
EXTRA_TOOLS = (
    {
        "id": "vbit_30deg_0.2mm",
        "name": "30° V-Bit 0.2mm Tip",
        "type": "v_bit",
        "category": "engraving",
        "geometry": {
            "angle": 30,
            "tipDiameter": 0.2,
            "maxDiameter": 3.175,
            "cuttingLength": 5.0,
            "shankDiameter": 3.175,
            "totalLength": 38,
        },
        "cutting": {
            "feedRate": 110,
            "plungeRate": 35,
            "spindleSpeed": 12000,
            "maxDepthPerPass": 0.06,
            "stepOver": 40,
        },
        "operations": ["isolation"],
        "material": "carbide",
    },
)


def add_tools_js() -> str:
    """JS registering :data:`EXTRA_TOOLS` with the tool library."""
    import json

    return f"""
(function () {{
  var TOOLS = {json.dumps(list(EXTRA_TOOLS))};

  function add() {{
    var lib;
    try {{ lib = window.easytrace.core.toolLibrary; }} catch (e) {{ return false; }}
    // The library loads its catalogue asynchronously; adding before that
    // finishes would put our bits at the front of a list about to be rebuilt.
    if (!lib || !lib.isLoaded || typeof lib.addTool !== 'function') return false;

    TOOLS.forEach(function (tool) {{
      if (lib.getTool && lib.getTool(tool.id)) return;   // already registered
      try {{
        // Same check the loader runs, so a malformed entry fails here with the
        // library's own message rather than halfway through indexing.
        if (typeof lib.validateTool === 'function') lib.validateTool(tool);
        lib.addTool(tool);
      }} catch (e) {{
        if (window.console) console.error('[piccola] tool rejected:', e);
      }}
    }});
    return true;
  }}

  var tries = 400;
  (function wait() {{
    if (add()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();
}})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# Kiri:Moto
# ─────────────────────────────────────────────────────────────────────────────
#
# Kiri's tool list is not a catalogue file — it is `settings.tools`, part of the
# workspace settings kept in localStorage under `ws-settings`
# (kiri/app/conf/manager.js `saveSettings`). It is seeded once from
# `conf/defaults.js template.tools` (ids 1000-1006) and is the user's from then
# on: `normalize()` only fills *missing* top-level keys, so an edited list is
# never re-merged with the template.
#
# That is why this seeds rather than pins. The bed size and the origin are facts
# about the machine and are re-applied on every load (_PRESET_JS in
# ui/pages/cam.py); a tool library is not — Kiri gives it an add / copy / delete
# / import UI, and fighting the user's own edits on every launch would be wrong.
# So the shaping below runs ONCE per browser profile, guarded by
# :data:`KIRI_SEED_KEY`, and after that the library belongs to the operator.
#
# The seven bits upstream seeds are a sampler for a generic mill: 1/4, 1/8 and
# 1/16 endmills, a vee, a ball and two drills, all imperial. Piccola keeps the
# two endmills that its own defaults already point at, and gains the metric
# endmills the machine actually ships with.
#
# Units: a tool carries its own. `Tool.unitScale()` (mode/cam/core/tool.js)
# multiplies by 25.4 for an imperial tool and by 1 for a metric one, so
# `metric: true` means every dimension below is millimetres.

#: localStorage key + value marking the library as already shaped. Bump the
#: value to re-run the seed on machines that have already been through it —
#: which also re-drops any of :data:`KIRI_DROP` the user has since added back,
#: so bump it deliberately.
KIRI_SEED_KEY = "piccola-kiri-tools"
#: 2: adds the R0.5 taper ball. 3: adds the R1.0 taper ball. 4: adds the
#: Hozly ball nose set (1.0 to 3.175 mm).
KIRI_SEED_VERSION = "4"

#: Upstream defaults to remove, by the id `conf/defaults.js` gives them.
#:
#: `name` and `type` are matched too, and a record that no longer agrees is left
#: alone: a default the operator renamed or repurposed is theirs, not upstream's
#: sample any more. Tools they *add* are never touched — Kiri stamps those with
#: `Date.now()`, which cannot collide with these ids.
KIRI_DROP = (
    {"id": 1002, "name": "end 1/16",  "type": "endmill"},
    {"id": 1003, "name": "vee 1/8",   "type": "tapermill"},
    {"id": 1004, "name": "ball 1/8",  "type": "ballmill"},
    {"id": 1005, "name": "drill 1/8", "type": "drill"},
    {"id": 1006, "name": "drill 1/4", "type": "drill"},
)
# Kept: 1000 "end 1/4" and 1001 "end 1/8" — the two every op default in
# defaults.js already names (camRoughTool, camOutlineTool, camPocketTool, ...
# are all 1000).

#: Endmills added on top. Ids are fixed constants, not timestamps, because an
#: operation stores the *id* of its tool (`camRoughTool` and friends): an id
#: that moved between launches would silently repoint every op that used it.
#: They sit far outside upstream's 1000-1006 block and are not 13-digit
#: `Date.now()` values, so they can collide with neither source.
#:
#: `number` is deliberately absent — it is the T word in `M6 T{tool}`, so it has
#: to be unique within the library, and the seed assigns it from what is
#: actually left rather than from an assumption about what was dropped.
KIRI_ADD = (
    {
        "id": 9000006,
        "name": "end 6mm",
        "type": "endmill",
        "metric": True,
        "shaft_diam": 6,
        "shaft_len": 30,
        "flute_diam": 6,
        "flute_len": 20,
        "taper_tip": 0,
    },
    {
        "id": 9000003,
        "name": "end 3mm",
        "type": "endmill",
        "metric": True,
        "shaft_diam": 3,
        "shaft_len": 30,
        "flute_diam": 3,
        "flute_len": 12,
        "taper_tip": 0,
    },
    # Tapered ball nose, 0.5 mm tip radius (taper_tip is the tip *diameter*).
    # Same series as the R0.25 operators add by hand -- 3.175 shank, 16.3 mm
    # of taper at 2.6 deg a side -- so the flute widens from 1.0 at the ball to
    # 1.0 + 2 x 16.3 x tan(2.6 deg) = 2.5 mm. Kiri derives the angle from these
    # three numbers (Tool.taperAngle, mode/cam/core/tool.js); taper_angle is
    # only what the tool dialog shows.
    {
        "id": 9000105,
        "name": "Taper ball R0.5 2F",
        "type": "taperball",
        "metric": True,
        "shaft_diam": 3.175,
        "shaft_len": 21.7,
        "flute_diam": 2.5,
        "flute_len": 16.3,
        "taper_angle": 2.6,
        "taper_tip": 1.0,
    },
    # R1.0 of the same series. The same 2.6 deg taper reaches the 3.175 shank
    # after (3.175 - 2.0) / 2 / tan(2.6 deg) = 12.8 mm, so that is where the
    # flute ends; the 38 mm overall length of the others is kept, which leaves
    # 25.2 mm of shank. 16.3 mm of taper would need a 3.5 mm top -- wider than
    # the shank it is ground from.
    {
        "id": 9000110,
        "name": "Taper ball R1.0 2F",
        "type": "taperball",
        "metric": True,
        "shaft_diam": 3.175,
        "shaft_len": 25.2,
        "flute_diam": 3.175,
        "flute_len": 12.8,
        "taper_angle": 2.6,
        "taper_tip": 2.0,
    },
    # Hozly ball nose end mill set: 3.175 shank, cutting diameter / cutting
    # length 1.0/4, 1.5/6, 2.0/12, 2.5/17, 3.175/22, 38.5-45 mm overall (the
    # box gives the range, not each length: 38.5 is taken for the three
    # smallest, 45 for the two largest). The small ones neck down from the
    # shank to the cutter; Kiri draws that neck as shank, which errs on the
    # wide side for clearance.
    *(
        {
            "id": tid,
            "name": f"ball {d}mm 2F",
            "type": "ballmill",
            "metric": True,
            "shaft_diam": 3.175,
            "shaft_len": oal - cel,
            "flute_diam": d,
            "flute_len": cel,
            "taper_tip": 0,
        }
        for tid, d, cel, oal in (
            (9000201, 1.0, 4, 38.5),
            (9000202, 1.5, 6, 38.5),
            (9000203, 2.0, 12, 38.5),
            (9000204, 2.5, 17, 45),
            (9000205, 3.175, 22, 45),
        )
    ),
)


def kiri_tools_js() -> str:
    """JS shaping Kiri's CAM tool library, once per browser profile.

    Drops :data:`KIRI_DROP`, adds :data:`KIRI_ADD`, and repoints anything that
    referenced a dropped bit.

    That last part is the reason this is more than two list operations. Kiri
    does not complain about a dangling tool id — `new Tool(settings, id)`
    (mode/cam/core/tool.js) silently falls back to `settings.tools[0]`, and the
    op panel's tool dropdown, rebuilt from `settings.tools` by
    `updateFieldsFromSettings`, leaves `selectedIndex` at 0 when it cannot find
    the saved id. So a drill op would keep running, showing the first tool in
    the list and cutting with it, while the setting behind it still named a bit
    that no longer exists. Upstream ships exactly that trap: `camDrillTool`
    defaults to 1006, one of the drills dropped here.

    Rather than pick one replacement for all of them, each dropped bit is
    remapped to the surviving tool of *nearest cutting diameter* — so the 1/4"
    drill lands on the 6 mm endmill and the 1/8" one on the 3 mm, which is the
    closest a milling library can come to a bit it no longer has. The remap
    covers the `cam*Tool` process defaults (in the live process and every saved
    CAM process) and the tool on each operation already in the op list.

    Saving through `api.conf.save()` is what makes it visible: it emits
    `settings.saved`, which Kiri's CAM UI answers with `validateTools()` and
    `opRender()` — so the op panels rebuild against the new list instead of
    showing a stale one until the next reload.
    """
    import json

    return """
(function () {
  var KEY = %(key)s, VERSION = %(version)s;
  var DROP = %(drop)s, ADD = %(add)s;

  function seeded() {
    // Storage disabled: claim it is done rather than reshaping the library on
    // every load with no way to record that we did.
    try { return localStorage.getItem(KEY) === VERSION; } catch (e) { return true; }
  }

  // Cutting diameter in mm, whatever units the tool carries.
  function mm(tool) {
    return (tool.metric ? 1 : 25.4) * (tool.flute_diam || 0);
  }

  function shape() {
    var api = window.kiri && window.kiri.api;
    if (!api || !api.conf || typeof api.conf.get !== 'function'
        || typeof api.conf.save !== 'function') return false;

    var conf = api.conf.get();
    if (!conf || !Array.isArray(conf.tools) || !conf.tools.length) return false;

    var byId = {};
    conf.tools.forEach(function (t) { byId[t.id] = t; });

    // 1. drop — only records still identical to the shipped sample
    var dropped = {};
    DROP.forEach(function (d) {
      var t = byId[d.id];
      if (t && t.name === d.name && t.type === d.type) dropped[d.id] = t;
    });
    var kept = conf.tools.filter(function (t) { return !dropped[t.id]; });

    // 2. add — skip anything already present, so a re-seed is idempotent
    var maxNum = 0;
    kept.forEach(function (t) {
      if (Number.isInteger(t.number)) maxNum = Math.max(maxNum, t.number);
    });
    ADD.forEach(function (t) {
      if (byId[t.id]) return;
      var add = JSON.parse(JSON.stringify(t));
      add.number = ++maxNum;
      kept.push(add);
    });

    if (!kept.length) return true;   // never hand Kiri an empty library

    // 3. remap references to a dropped bit onto the nearest surviving diameter
    var remap = {};
    for (var id in dropped) {
      var want = mm(dropped[id]), best = kept[0], bestd = Infinity;
      kept.forEach(function (t) {
        var d = Math.abs(mm(t) - want);
        if (d < bestd) { bestd = d; best = t; }
      });
      remap[id] = best.id;
    }
    var procs = [conf.process];
    var saved = (conf.sproc || {}).CAM || {};
    for (var name in saved) procs.push(saved[name]);

    // 4. commit as a unit, or not at all.
    //
    // api.conf.save() reaches into the 3D space on its way to storage
    // (saveSettings -> space.view.save() -> viewControl.getPosition), and this
    // polls from document creation — early enough to arrive before the space is
    // built, in which case the save throws. A library trimmed in memory but
    // never written would show one list this session and trim again on the
    // next, so nothing is applied until the save is known to have worked.
    var writes = [{ obj: conf, key: 'tools', was: conf.tools, now: kept }];
    procs.forEach(function (p) {
      if (!p) return;
      for (var field in p) {
        // the cam*Tool process defaults: camRoughTool, camDrillTool, ...
        if (/^cam.*Tool$/.test(field) && remap[p[field]] !== undefined) {
          writes.push({ obj: p, key: field, was: p[field], now: remap[p[field]] });
        }
      }
      // and the operations already placed in the op list
      (Array.isArray(p.ops) ? p.ops : []).forEach(function (op) {
        if (op && remap[op.tool] !== undefined) {
          writes.push({ obj: op, key: 'tool', was: op.tool, now: remap[op.tool] });
        }
      });
    });

    writes.forEach(function (w) { w.obj[w.key] = w.now; });
    try {
      api.conf.save();
    } catch (e) {
      // not ready yet — put everything back and let the poll try again
      for (var i = writes.length; i-- > 0;) writes[i].obj[writes[i].key] = writes[i].was;
      return false;
    }
    try { localStorage.setItem(KEY, VERSION); } catch (e) { /* not fatal */ }
    return true;
  }

  if (seeded()) return;
  // Kiri publishes its namespace before the settings are wired up, so poll
  // rather than firing once into a half-built api.
  var tries = 400;
  (function wait() {
    try { if (shape()) return; } catch (e) {
      if (window.console) console.error('[piccola] tool seed failed:', e);
      return;
    }
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "key": json.dumps(KIRI_SEED_KEY),
        "version": json.dumps(KIRI_SEED_VERSION),
        "drop": json.dumps(list(KIRI_DROP)),
        "add": json.dumps(list(KIRI_ADD)),
    }
