"""
ui/cam_import_prompt.py
Says what opening a project actually does, in the dialog that asks.

Why
---
Opening a project replaces everything: `settingsImport` runs
`api.space.clear()`, assigns `settings = normalize(data.settings)` straight over
the live record and into `localStorage`, clears the platform and re-adds the
saved parts. The current workspace and its settings are gone, with no undo.

The dialog that asks about that says::

    Import settings made in Kiri:Moto version 4.7.2 on
    Wed Sep 03 2026 16:20:11 GMT+0200

which is provenance — where the file came from — and answers a question nobody
asked at that moment. What the operator needs to know before clicking OK is that
their current setup is about to be discarded. A version and a timestamp read as
detail to skim past, so the one destructive thing about the operation is the one
thing the dialog does not mention.

How
---
By wrapping `api.uc.confirm`. `settingsImport` does `const { uc, ui } = api` and
then calls `uc.confirm(...)`, so the property is looked up on the shared `api.uc`
object at call time and a replacement there is seen. No patch, and nothing about
the import itself is touched — only the sentence it asks with.

Matched on the prompt's own opening words rather than on a flag, because
`uc.confirm` is Kiri's general-purpose confirm: the same function asks about
replacing a device, replacing a process, clearing tabs. Rewriting the wrong one
would be worse than leaving all of them alone, so the test is narrow and the
fallthrough passes everything else through untouched.

The screenshot Kiri puts above the text (`opt.pre`, present when the project was
saved with one) is kept. It is not text, and being able to see which project is
about to be opened is worth more than a tidier dialog.
"""

from __future__ import annotations

import json

#: The opening of upstream's prompt for a whole-settings import, which is the
#: one this replaces. Deliberately anchored at the start: `uc.confirm` is used
#: for every other confirmation in the app too.
MATCH_PREFIX = "Import settings made in"

#: What it says instead. A question, because it is answered with OK/Cancel, and
#: the destructive half first because that is the part worth reading.
PROMPT = ("Opening a project clears the current workspace and its settings."
          "<br>Continue?")


def import_prompt_js() -> str:
    """JS replacing the project-open confirmation text."""
    return """
(function () {
  var MATCH = %(match)s, PROMPT = %(prompt)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function install() {
    var a = api();
    if (!a || !a.uc || typeof a.uc.confirm !== 'function') return false;
    if (a.uc.__piccolaImportPrompt) return true;

    var orig = a.uc.confirm;
    a.uc.confirm = function (msg, ok, cancel, opt) {
      // Narrow on purpose: this is the app's general confirm, and it also
      // asks about replacing a device, replacing a process and clearing tabs.
      if (typeof msg === 'string' && msg.indexOf(MATCH) === 0) {
        msg = PROMPT;
      }
      return orig.call(this, msg, ok, cancel, opt);
    };
    a.uc.__piccolaImportPrompt = true;
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "match": json.dumps(MATCH_PREFIX),
        "prompt": json.dumps(PROMPT),
    }
