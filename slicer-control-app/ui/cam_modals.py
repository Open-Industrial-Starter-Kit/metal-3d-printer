"""
ui/cam_modals.py
Removes EasyTrace's Help dialog, and keeps any suppressed dialog from holding
the keyboard.

The Help button
---------------
`#btn-help` in the workspace toolbar (and F1) opens `#help-modal`, "Starting +
Keyboard Shortcuts": an upstream tour of a workflow this app has since reshaped —
its origin, rotation and export instructions no longer match what the panel does,
and several of the shortcuts it lists are for controls that are gone. Left in, it
documents a tool the user is not looking at.

Why hiding a modal is not enough on its own
-------------------------------------------
`ModalManager.showModal()` records `this.activeModal` and installs a focus trap
*before* anything is drawn, and it never asks whether the dialog is visible. A
modal hidden with CSS therefore opens invisibly but for real, and while it is
"open":

* ``ShortcutManager.handleKeyDown()`` returns early for every key except Escape,
  so the tool's shortcuts stop working, and
* the focus trap cycles Tab inside a dialog nobody can see.

The only way out is Escape, which is not a thing anyone tries against a screen
that looks idle.

That is not hypothetical, and it is not new with the Help dialog: the welcome
modal is suppressed the same way (see ``_EASYTRACE_HIDE_CSS`` in
``ui/pages/cam.py``) and upstream shows it unconditionally on every load, so the
PCB tab has been booting into that state.

So ``showModal`` is wrapped: if what it just opened cannot be seen, it is closed
again straight away, through the tool's own ``closeModal()`` — which removes the
focus trap, clears ``activeModal`` and restores focus. Suppressed dialogs stay
suppressed, and the keyboard stays alive. F1 needs no special handling under
this: it opens the Help modal and the guard closes it in the same tick.

The class is patched through a property setter on window, so it is in place
before any instance exists.
"""

from __future__ import annotations

#: Elements hidden outright. The modal is listed as well as its button so no
#: other route — F1, a deep link, a leftover link in another dialog — can put it
#: on screen; the guard below is what keeps that from wedging the keyboard.
HIDDEN = ("btn-help", "help-modal")


def css() -> str:
    """CSS hiding the Help button and its dialog."""
    return "\n".join(
        f"#{name} {{ display: none !important; }}" for name in HIDDEN)


def modal_guard_js() -> str:
    """JS closing any modal that opens without being visible."""
    return """
(function () {
  var PATCH_FLAG = '__piccolaModalGuard';

  function visible(el) {
    return !!(el && (el.offsetWidth || el.offsetHeight ||
                     el.getClientRects().length));
  }

  function patch(Cls) {
    if (!Cls || !Cls.prototype || Cls[PATCH_FLAG]) return Cls;
    var show = Cls.prototype.showModal;
    if (typeof show !== 'function') return Cls;

    Cls.prototype.showModal = function (name) {
      var result = show.apply(this, arguments);
      try {
        var modal = this.modals && this.modals[name];
        // Measured after the fact on purpose: `.modal` is display:none until
        // `.active` is added, so "would this be visible" cannot be asked
        // beforehand. Only a dialog we suppress stays invisible with the class
        // on it.
        if (modal && this.activeModal === modal && !visible(modal) &&
            typeof this.closeModal === 'function') {
          this.closeModal();
        }
      } catch (e) { /* never let the guard break opening a real dialog */ }
      return result;
    };

    Cls[PATCH_FLAG] = true;
    return Cls;
  }

  // Already loaded (defensive — our script normally runs first).
  if (window.ModalManager) { patch(window.ModalManager); return; }

  var held;
  try {
    Object.defineProperty(window, 'ModalManager', {
      configurable: true,
      enumerable: true,
      get: function () { return held; },
      set: function (v) { held = patch(v); }
    });
  } catch (e) {
    var tries = 200;
    (function wait() {
      if (window.ModalManager) { patch(window.ModalManager); return; }
      if (tries-- > 0) setTimeout(wait, 50);
    })();
  }
})();
"""
