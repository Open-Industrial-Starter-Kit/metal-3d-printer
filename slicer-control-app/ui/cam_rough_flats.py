"""
ui/cam_rough_flats.py
Turns *clear faces* on, once, for roughing operations saved before it was pinned.

Why this exists as well as the pin
----------------------------------
``_KIRI_PROCESS_DEFAULTS`` in ``ui/pages/cam.py`` pins ``camRoughFlat`` true, and
that is what a **new** rough operation is created with -- verified: add one and
its record reads ``flats: true``. It does nothing for an operation that already
exists, because the flag lives on the operation's own record in the saved job,
not in the process defaults it was born from.

So a job made before the pin keeps roughing the way it always did: an even
ladder of step-downs from the top of the stock to the bottom, landing on the
part's flat faces only by coincidence. Measured on an 80x50x15 plate with two
pockets and a rib -- 6.35 mm endmill, 0.4 stepover, 2 mm step-down -- that left
0.823 mm standing on every pocket floor and 0.546 mm on every wall top. With
*clear faces* on, passes are added at exactly those heights and both go to
-0.010 mm.

Once, and then it is yours
--------------------------
Each operation is marked as it is migrated, and a marked operation is never
touched again. That distinction matters: a setting this module re-asserted on
every load would be one the operator could not turn off, and *clear faces* costs
passes -- about a quarter more cutting moves on the test part -- so wanting it
off for a quick clearing job is a legitimate position, not a mistake to correct.

The mark goes on the operation record, which is what gets saved with the job, so
it survives a reload the same way the setting does.
"""

from __future__ import annotations

import json

#: Marks an operation this has already dealt with. Prefixed and spelled out
#: because it is written into the user's saved job alongside upstream's own
#: fields, where an unexplained short key would be a small mystery for ever.
MARK = "piccolaFlatsMigrated"

#: How long to keep looking for the settings. The op list is restored during
#: boot, well after this is injected at document creation.
TRIES = 400
INTERVAL_MS = 50


def rough_flats_js() -> str:
    """JS enabling *clear faces* once on pre-existing rough operations."""
    js = """
(function () {
  var MARK = %(mark)s, TRIES = %(tries)s, EVERY = %(every)s;

  function migrate() {
    var api = window.kiri && window.kiri.api;
    if (!api || !api.conf) return false;
    var conf;
    try { conf = api.conf.get(); } catch (e) { return false; }
    var proc = conf && conf.process;
    // an empty list is a legitimate state, but it is also what "not restored
    // yet" looks like, so it does not count as done
    if (!proc || !Array.isArray(proc.ops) || !proc.ops.length) return false;

    var changed = 0;
    for (var i = 0; i < proc.ops.length; i++) {
      var op = proc.ops[i];
      if (!op || op.type !== 'rough') continue;
      if (op[MARK]) continue;          // already handled -- the flag is theirs now
      op[MARK] = 1;
      if (!op.flats) { op.flats = true; changed++; }
    }
    if (changed) {
      try { api.conf.save(); } catch (e) { }
      // the popup reads from the record when it opens, so nothing has to be
      // redrawn here; the next slice is what the change is for
    }
    return true;
  }

  var left = TRIES;
  (function wait() {
    var done = false;
    try { done = migrate(); } catch (e) { }
    if (done) return;
    if (left-- > 0) setTimeout(wait, EVERY);
  })();
})();
""" % {
        "mark": json.dumps(MARK),
        "tries": json.dumps(TRIES),
        "every": json.dumps(INTERVAL_MS),
    }
    assert "%" not in js, "unescaped % survived formatting in rough_flats_js()"
    return js
