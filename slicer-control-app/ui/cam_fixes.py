"""
ui/cam_fixes.py
Runtime corrections applied to the vendored CAM tools.

These are upstream bugs we work around from the outside — by wrapping a public
method on the page, never by editing files under ``vendor/``. That keeps the
bundles byte-identical (upgrades stay a folder swap, and EasyTrace's AGPL
redistribution stays trivial) and keeps each workaround visible in one place
instead of buried as a diff against a 90 kB third-party file.

Everything here should ideally be reported upstream and deleted once fixed.
"""

from __future__ import annotations

# ── EasyTrace5000: Excellon coordinate format ────────────────────────────────
#
# Symptom: drill files exported by EAGLE land at 1/10 scale, so every hole sits
# in a cluster near the origin instead of on the board.
#
# Cause: `FMAT,2` is the Excellon *format standard* — it selects Format 1 vs
# Format 2 command syntax. It is NOT a coordinate digit count. EasyTrace's
# parseFormat() reads it as one:
#
#     if (code === 2) this.options.format = { integer: 2, decimal: 4 };
#     this.formatExplicitlySet = true;
#
# and the `formatExplicitlySet` flag it raises then suppresses the correct
# metric default further down. Meanwhile the digit count the file actually
# declares — the `000.000` in `METRIC,TZ,000.000` — is never parsed at all.
#
# For a typical EAGLE header:
#
#     FMAT,2
#     METRIC,TZ,000.000        <- 3 integer + 3 decimal digits
#     X74630Y68500             <- 74.630, 68.500 mm
#
# the parser ends up on 2.4 instead of 3.3. Both are six digits wide, so the
# padding is identical and only the decimal point moves — every coordinate comes
# out exactly 10x small (measured: X 3.86..9.62 mm where it should be
# 38.60..96.20).
#
# Fix: after the original handles a units line, apply the digit spec the file
# itself declares. Hooking processLine rather than the units branch means it
# works whichever order FMAT and METRIC appear in, and it only fires when an
# explicit spec is present — files without one keep their existing behaviour,
# including inch files that legitimately rely on the FMAT fallback.
#
# The class is patched through a property setter on window, so it is corrected
# the instant parser-excellon.js assigns it; there is no window where an
# unpatched parser could be used.
_EXCELLON_FORMAT_FIX = r"""
(function () {
  var PATCH_FLAG = '__piccolaExcellonFormatFix';

  function patch(Cls) {
    if (!Cls || !Cls.prototype || Cls[PATCH_FLAG]) return Cls;
    var orig = Cls.prototype.processLine;
    if (typeof orig !== 'function') return Cls;

    // "METRIC,TZ,000.000" / "INCH,LZ,00.0000" -> {integer, decimal}
    var SPEC = /^(?:METRIC|INCH)\b.*?(0+)\.(0+)\s*$/i;

    Cls.prototype.processLine = function (line, lineNumber) {
      var result = orig.call(this, line, lineNumber);
      try {
        var m = SPEC.exec(String(line).trim());
        if (m) {
          var fmt = { integer: m[1].length, decimal: m[2].length };
          this.options.format = fmt;
          if (this.drillData) this.drillData.format = fmt;
          // Claim the format so FMAT's guess cannot win, whichever came first.
          this.formatExplicitlySet = true;
        }
      } catch (e) { /* never let the fix break a parse */ }
      return result;
    };

    Cls[PATCH_FLAG] = true;
    return Cls;
  }

  // Already loaded (defensive — our script normally runs first).
  if (window.ExcellonParser) { patch(window.ExcellonParser); return; }

  var held;
  try {
    Object.defineProperty(window, 'ExcellonParser', {
      configurable: true,
      enumerable: true,
      get: function () { return held; },
      set: function (v) { held = patch(v); }
    });
  } catch (e) {
    // Fall back to polling if the property cannot be trapped.
    var tries = 200;
    (function wait() {
      if (window.ExcellonParser) { patch(window.ExcellonParser); return; }
      if (tries-- > 0) setTimeout(wait, 50);
    })();
  }
})();
"""


def excellon_format_fix_js() -> str:
    """Correct EasyTrace's Excellon coordinate-format handling (see above)."""
    return _EXCELLON_FORMAT_FIX


# ── EasyTrace5000: Origin Offset fields cannot be typed into ─────────────────
#
# Symptom: a minus sign will not go into X/Y Origin Offset. Type "-" and it
# turns into "0.000" as the key lands, so a negative offset is unreachable.
# Decimals are just as bad: "1" becomes "1.000" with the caret at the end, so
# "1.5" ends up as "1.0005" and is then rewritten again. Only the spinner works.
#
# Cause: the field echoes itself back mid-edit. ui-core-trace.js wires
#
#     xInput.addEventListener('input', applyPreview);
#
# and applyPreview() pushes the value into the scene:
#
#     this.scene.setOrigin(this.committedOrigin.x + (parseFloat(xInput.value) || 0), ...)
#
# setOrigin() always notifies its transform listeners — there is no
# "did it actually change" check — and one of those listeners is
#
#     this.scene.addTransformListener(() => this.updateOffsetInputsWithTracking());
#
# which writes `(origin - committedOrigin).toFixed(3)` straight back into the
# input. For `<input type="number">` a partial value like "-" is *invalid*, so
# `.value` reads as "" — parseFloat("") || 0 gives 0, the origin does not move,
# and the round trip replaces the "-" the user just typed with "0.000".
#
# Upstream already knows the shape of this bug: the Board Rotation field beside
# it guards its own sync with `document.activeElement !== el`. The offset fields
# simply never got the same treatment.
#
# Fix: apply that guard to the offset sync, from outside — skip the write-back
# while one of the two inputs has focus. The preview still follows every valid
# keystroke; only the echo into the field is suppressed.
#
# applyOffsetAndSetOrigin() is wrapped as well, because it deliberately calls
# the same method to zero the fields against the newly saved origin, and that
# write must land even though focus is still in the field. Skipping it would
# leave the old text ("-5") sitting in a field that now means zero — and the
# next keystroke would apply that -5 a second time, moving the job again.
#
# The class is patched through a property setter on window, so the fix is in
# place before any instance exists.
_ORIGIN_TYPING_FIX = r"""
(function () {
  var PATCH_FLAG = '__piccolaOriginTypingFix';
  var IDS = ['x-offset', 'y-offset'];

  function editing() {
    var el = document.activeElement;
    return !!(el && IDS.indexOf(el.id) !== -1);
  }

  function patch(Cls) {
    if (!Cls || !Cls.prototype || Cls[PATCH_FLAG]) return Cls;
    var proto = Cls.prototype;
    var sync = proto.updateOffsetInputsWithTracking;
    var commit = proto.applyOffsetAndSetOrigin;
    if (typeof sync !== 'function') return Cls;

    // Depth, not a boolean: the commit path re-enters through
    // updateOriginDisplay(), and a plain flag would be cleared by the inner
    // call while the outer one still needs it.
    var forced = 0;

    proto.updateOffsetInputsWithTracking = function () {
      if (!forced && editing()) return;
      return sync.apply(this, arguments);
    };

    if (typeof commit === 'function') {
      proto.applyOffsetAndSetOrigin = function () {
        forced++;
        try { return commit.apply(this, arguments); }
        finally { forced--; }
      };
    }

    Cls[PATCH_FLAG] = true;
    return Cls;
  }

  // Already loaded (defensive — our script normally runs first).
  if (window.EasyTraceUI) { patch(window.EasyTraceUI); return; }

  var held;
  try {
    Object.defineProperty(window, 'EasyTraceUI', {
      configurable: true,
      enumerable: true,
      get: function () { return held; },
      set: function (v) { held = patch(v); }
    });
  } catch (e) {
    var tries = 200;
    (function wait() {
      if (window.EasyTraceUI) { patch(window.EasyTraceUI); return; }
      if (tries-- > 0) setTimeout(wait, 50);
    })();
  }
})();
"""


def origin_typing_fix_js() -> str:
    """Let EasyTrace's Origin Offset fields be typed into (see above)."""
    return _ORIGIN_TYPING_FIX


#: mode -> the fixes to inject, in order. Injected at document creation so each
#: patch is installed before the tool's own scripts run.
JS_FOR_MODE = {
    "pcb": (excellon_format_fix_js, origin_typing_fix_js),
}


def js_for_mode(mode: str) -> str:
    """Every fix for *mode*, concatenated. Empty string if there are none."""
    return "\n".join(fix() for fix in JS_FOR_MODE.get(mode, ()))
