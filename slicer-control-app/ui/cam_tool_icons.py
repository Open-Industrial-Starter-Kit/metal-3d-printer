"""
ui/cam_tool_icons.py
An illustrated tool-type picker in the CAM tool library.

Why
---
Kiri picks the cutter geometry from a dropdown of five words — *flat end, ball
end, taper tip, taper ball, drill bit*. The words are accurate and completely
unhelpful at a glance: the difference between a taper tip and a taper ball is a
4 mm ball on the end of a cone, and no amount of naming makes that as fast to
read as a picture of it. Every other CAM package shows the shapes; Fusion's
library is a grid of them.

So the dropdown is replaced by the shapes. Same underlying control — the row of icons *drives* the original ``<select>`` rather than
replacing the logic behind it, so everything that reads ``ui.toolType``
(``updateTool``, the save path, the 3D preview in ``#tool-view``) is untouched.

Which types the picker offers
-----------------------------
``consts.js`` ships five::

    camtool: [
        { name: "flat end",   id: "endmill"   },
        { name: "ball end",   id: "ballmill"  },
        { name: "taper tip",  id: "tapermill" },
        { name: "taper ball", id: "taperball" },
        { name: "drill bit",  id: "drill"     },
    ]

The picker shows four of them plus a **face mill** in taper ball's place.

Face mill is added, not native. The engine asks only ``isBallMill()``,
``isTaperMill()``/``isTaperBall()`` and ``isDrill()``; anything else is cut as a
flat-bottomed cylinder of ``flute_diam``. That is exactly a face mill's cutting
envelope -- it *is* a flat end, just wide and short -- so ``facemill`` needs no
engine support and toolpaths treat it correctly. The type is registered by
appending ``{ name: "face mill", id: "facemill" }`` to ``api.lists.camtool``,
the shared array ``rec2ui`` rebuilds the hidden ``<select>`` from on every tool
selection; it then saves with the tool like any other type.

Taper ball is dropped from the *picker* only. It stays in ``api.lists.camtool``
so a tool that already has ``type: "taperball"`` keeps it -- removing it from the
list would make ``rec2ui`` fall back to the first option, and the next edit
would silently save that tool as a flat end. Such a tool simply shows no
highlighted icon.

There are still no bull-nose, chamfer or boring-bar icons: those *do* cut a
different shape than a cylinder, and an icon for a cutter the engine would
silently treat as a flat end would be a lie about what the software does.

Why not icons beside each tool in the list
------------------------------------------
The list is ``<select id="tool-select" size="10">`` and its entries are
``<option>`` elements. No browser renders markup or images inside an option, so
per-tool icons would mean replacing the list widget outright — and the widget
is load-bearing: add, copy, delete and save all address the selection through
``ui.toolSelect.selectedIndex``. Not worth the re-wiring for a decoration.

Fields that do not apply
------------------------
The *taper* group — angle and tip — is shown for every tool type upstream, but
``Tool.isTaperMill()`` tests for ``tapermill`` or ``taperball`` and nothing else
reads ``taper_angle`` or ``taper_tip``. On a flat end, a ball end or a drill the
two boxes are inert. They are hidden along with their header when the type is
anything else, which also buys back two rows in a column that has none to spare.

Keeping the highlight honest
----------------------------
The selected type changes from two directions: this picker, and picking a
different tool in the list (``selectTool`` pushes the whole record through
``rec2ui``, which assigns ``ui.toolType.value`` directly and fires no event).
There is no event to listen for on the second path, so the highlight is polled
off the ``<select>`` while the dialog is open. Cheap, and it cannot drift.
"""

from __future__ import annotations

import json

#: Id on the picker, and the "already built" guard.
PICKER_ID = "piccola-tool-types"

#: Class prefix for the picker's parts.
CLS = "piccola-tt"

#: The id of Kiri's tool-type ``<select>``. Set in init-menu.js as
#: ``newSelect('type', { ... id: 'tool-type' ... }, "camtool")``.
SELECT_ID = "tool-type"

#: The dialog the picker lives in. Polling only runs while this is open.
DIALOG_ID = "mod-tools"

#: How often to re-read the select. Only ticks while the tool dialog is up.
POLL_MS = 250

#: Caption above the icons. Built but hidden by the stylesheet: the row it
#: replaces already sat under a "type" heading, and a second one cost a line
#: of height in a column that has none to spare.
CAPTION = "type"

#: The only types the taper fields mean anything for. `Tool.isTaperMill()`
#: tests exactly these two, and `calcTaperAngle` / `calcTaperLength` are the
#: only readers of `taper_angle` and `taper_tip` -- on a flat end, a ball end or
#: a drill the two boxes are inert, and showing them invites someone to type a
#: number that will be ignored.
TAPER_TYPES = ("tapermill", "taperball")

#: The tip-angle input added by patch 0022 (``newInput('tip angle', { id: ...
#: })`` in init-menu.js). Only a drill has a point angle, so the row shows for
#: the drill type alone -- the same treatment the taper rows get above.
DRILL_ANGLE_ID = "tool-drill-angle"
DRILL_TYPE = "drill"

#: The type this module adds to Kiri's list (see "Which types the picker
#: offers"). The engine cuts it as a flat end; the name is for the operator.
FACE_MILL = {"name": "face mill", "id": "facemill"}

#: Side profiles of the cutters in the picker, drawn to a shared 40x62 grid so the
#: shanks line up and only the business end differs. Deliberately schematic:
#: this has to read at 34 px, where flute helices turn to mush.
#:
#: Shared geometry — shank 14..26 from y=3, flutes 11..29 from y=22.
_SHANK = '<rect x="14" y="3" width="12" height="19" rx="1" fill="#9aa3ab"/>'
_FLUTE_LINES = (
    '<path d="M12 30 L28 24 M12 38 L28 32 M12 46 L28 40"'
    ' stroke="#ffffff" stroke-width="1.6" opacity="0.55" fill="none"/>'
)

#: id -> (label, svg body). The label matches ``consts.js`` so the picker and
#: the dropdown it replaces cannot drift apart in wording.
TOOL_TYPES = (
    ("endmill", "flat end",
     _SHANK
     + '<path d="M11 22 H29 V52 H11 Z" fill="#4a5560"/>'
     + _FLUTE_LINES),
    ("ballmill", "ball end",
     _SHANK
     + '<path d="M11 22 H29 V43 A9 9 0 0 1 11 43 Z" fill="#4a5560"/>'
     + _FLUTE_LINES),
    ("tapermill", "taper tip",
     _SHANK
     + '<path d="M11 22 H29 L23 52 H17 Z" fill="#4a5560"/>'
     + '<path d="M12 30 L28 25 M14 38 L26 33 M16 46 L24 41"'
       ' stroke="#ffffff" stroke-width="1.6" opacity="0.55" fill="none"/>'),
    # A face mill: the shank into a body far wider than it, short, flat on the
    # bottom, with indexable inserts along the cutting edge -- width and the
    # inserts are what tell it from a flat end.
    ("facemill", "face mill",
     _SHANK
     + '<path d="M9 22 H31 L37 28 V46 H3 V28 Z" fill="#4a5560"/>'
     + '<path d="M4 41 H10 V46 H4 Z M17 41 H23 V46 H17 Z M30 41 H36 V46 H30 Z"'
       ' fill="#c9ced3"/>'),
    # A twist drill: curved bands for the spiral flutes -- the one cue that
    # tells a drill from an endmill at a glance -- and a short ~118 degree point
    # rather than a spike.
    ("drill", "drill bit",
     _SHANK
     + '<path d="M13 22 H27 V48 L20 52 L13 48 Z" fill="#4a5560"/>'
     # a helix seen side-on: steep at the edges, flat across the middle
     + '<path d="M13 21 C15 25 25 25 27 29 M13 29 C15 33 25 33 27 37'
       ' M13 37 C15 41 25 41 27 45 M14 45 C16 48 21 48 23 50"'
       ' stroke="#ffffff" stroke-width="2.6" opacity="0.6" fill="none"/>'),
)


def css() -> str:
    """Styling for the picker.

    ``!important`` throughout for the same reason the tooltip panel needs it:
    Kiri's dialog rules reach anything inserted into ``#tool-details``, and a
    plain declaration loses to them.
    """
    return "\n".join([
        # Kiri writes every label in this dialog in lower case -- "name",
        # "tool #", "shaft", "diameter", "tip angle". Sentence case reads as a
        # form: capitalise the first letter only, so "tip angle" becomes "Tip
        # angle" rather than "Tip Angle". All three kinds are flex items
        # (.f-row / .var-row / .set-header are flex), so ::first-letter applies.
        # The tool *names* in the list are the operator's own and are left as
        # typed.
        "#mod-tools .t-head > label::first-letter,",
        "#tool-details .var-row > label::first-letter,",
        "#tool-details .set-header > a::first-letter {",
        "  text-transform: uppercase !important;",
        "}",
        # The detail column is capped at 350px by #tool-cols with no overflow
        # rule of its own, so anything past that height is simply unreachable --
        # true before this picker existed, and the reason the fields below it
        # could not be scrolled to.
        "#tool-details {",
        "  overflow-y: auto !important;",
        "  overflow-x: hidden !important;",
        "}",
        f"#{PICKER_ID} {{",
        "  display: flex !important;",
        # one row, always: wrapping is what pushed the fields below out of
        # reach in the first place
        "  flex-wrap: nowrap !important;",
        "  overflow-x: auto !important;",
        "  gap: 1px !important;",
        "  align-items: flex-start !important;",
        "  justify-content: flex-start !important;",
        "  margin: 1px 0 4px 0 !important;",
        "  padding: 0 0 2px 0 !important;",
        "}",
        f"#{PICKER_ID} .{CLS}-cap {{",
        "  display: none !important;",
        "}",
        f"#{PICKER_ID} .{CLS}-cell {{",
        "  display: flex !important;",
        "  flex: 0 0 auto !important;",
        "  flex-direction: column !important;",
        "  align-items: center !important;",
        "  gap: 1px !important;",
        "  width: 30px !important;",
        "  padding: 2px 0 1px 0 !important;",
        "  border: 1px solid transparent !important;",
        "  border-radius: 3px !important;",
        "  cursor: pointer !important;",
        "  background: transparent !important;",
        "}",
        f"#{PICKER_ID} .{CLS}-cell:hover {{",
        "  background: rgba(0, 0, 0, 0.06) !important;",
        "}",
        # the selected type, in the app's own selection blue
        f"#{PICKER_ID} .{CLS}-cell.{CLS}-on {{",
        "  background: #d8e8fb !important;",
        "  border-color: #5b9bd5 !important;",
        "}",
        f"#{PICKER_ID} .{CLS}-cell svg {{",
        "  display: block !important;",
        "  width: 16px !important;",
        "  height: 26px !important;",
        "}",
        f"#{PICKER_ID} .{CLS}-name {{",
        "  font-size: 7.5px !important;",
        "  line-height: 1.1 !important;",
        "  text-align: center !important;",
        "  white-space: normal !important;",
        "  text-transform: capitalize !important;",
        "  opacity: 0.85 !important;",
        "}",
    ])


def tool_icons_js() -> str:
    """JS building the picker and keeping it in step with the select."""
    js = """
(function () {
  var PICKER_ID = %(picker)s, CLS = %(cls)s, SELECT_ID = %(select)s;
  var DIALOG_ID = %(dialog)s, POLL_MS = %(poll)s, CAPTION = %(caption)s;
  var TYPES = %(types)s, TAPER_TYPES = %(taper)s;
  var DRILL_ANGLE_ID = %(drill_angle)s, DRILL_TYPE = %(drill_type)s;
  var FACE_MILL = %(face_mill)s;

  function sel() { return document.getElementById(SELECT_ID); }

  // Add the face mill to the list rec2ui builds the type <select> from. Kept
  // idempotent: the poll calls it until Kiri's api exists.
  function registerFaceMill() {
    var list;
    try { list = window.kiri.api.lists.camtool; } catch (e) { return false; }
    if (!Array.isArray(list)) return false;
    var have = list.some(function (t) { return t && t.id === FACE_MILL.id; });
    if (!have) list.push({ name: FACE_MILL.name, id: FACE_MILL.id });
    return true;
  }

  // The select may have been built before the type was registered (it is
  // rebuilt only when a tool is selected). Assigning a value with no matching
  // option silently does nothing, so make sure the option is there first.
  function ensureOption(s, id) {
    for (var i = 0; i < s.options.length; i++) {
      if (s.options[i].value === id) return;
    }
    if (id !== FACE_MILL.id) return;
    var opt = document.createElement('option');
    opt.value = id;
    opt.textContent = FACE_MILL.name;
    s.appendChild(opt);
  }

  function dialogOpen() {
    var d = document.getElementById(DIALOG_ID);
    if (!d) return false;
    // the modal host hides it; offsetParent is null for a display:none subtree
    return !!(d.offsetWidth || d.offsetHeight || d.getClientRects().length);
  }

  // Drive the original control rather than the model behind it: its onchange is
  // updateTool, which is what actually writes the tool record.
  function pick(id) {
    var s = sel();
    if (!s) return;
    ensureOption(s, id);
    s.value = id;
    if (typeof s.onchange === 'function') s.onchange({ target: s });
    else s.dispatchEvent(new Event('change', { bubbles: true }));
    sync();
  }

  function build() {
    var s = sel();
    if (!s) return false;
    if (document.getElementById(PICKER_ID)) return true;
    var row = s.closest('.var-row') || s.parentElement;
    if (!row || !row.parentNode) return false;

    var wrap = document.createElement('div');
    wrap.id = PICKER_ID;

    var cap = document.createElement('div');
    cap.className = CLS + '-cap';
    cap.textContent = CAPTION;
    wrap.appendChild(cap);

    TYPES.forEach(function (t) {
      var cell = document.createElement('div');
      cell.className = CLS + '-cell';
      cell.setAttribute('data-type', t[0]);
      // deliberately no title: the name is already printed under the icon, and
      // a tooltip here lands on top of the row below it
      var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 40 62');
      svg.innerHTML = t[2];
      cell.appendChild(svg);
      var name = document.createElement('div');
      name.className = CLS + '-name';
      name.textContent = t[1];
      cell.appendChild(name);
      cell.onclick = function () { pick(t[0]); };
      wrap.appendChild(cell);
    });

    row.parentNode.insertBefore(wrap, row.nextSibling);
    // the dropdown it replaces, and the "type" label beside it
    row.style.setProperty('display', 'none', 'important');
    sync();
    return true;
  }

  // The type also changes when a different tool is picked in the list:
  // selectTool pushes the record through rec2ui, which assigns .value directly
  // and fires nothing. So read the select rather than wait for an event.
  function sync() {
    var s = sel(), wrap = document.getElementById(PICKER_ID);
    if (!s || !wrap) return;
    var now = s.value;
    var cells = wrap.getElementsByClassName(CLS + '-cell');
    for (var i = 0; i < cells.length; i++) {
      var on = cells[i].getAttribute('data-type') === now;
      cells[i].classList.toggle(CLS + '-on', on);
    }
    syncTaper(now);
    syncDrillAngle(now);
  }

  // A point angle only means something on a drill. Absent entirely on a Kiri
  // built without patch 0022, in which case there is nothing to do.
  function syncDrillAngle(value) {
    var input = document.getElementById(DRILL_ANGLE_ID);
    if (!input) return;
    var row = (input.closest && input.closest('.var-row')) || input.parentElement;
    if (!row) return;
    if (value === DRILL_TYPE) row.style.removeProperty('display');
    else row.style.setProperty('display', 'none', 'important');
  }

  // The taper fields only apply to the two taper cutters. Kiri shows them for
  // every type, so a flat endmill offers an angle and a tip that nothing will
  // ever read. Hidden rather than disabled: there is nothing to say about a
  // taper on a cutter that has none.
  function syncTaper(value) {
    var ui;
    try { ui = window.kiri.api.ui; } catch (e) { return; }
    var a = ui && ui.toolTaperAngle, t = ui && ui.toolTaperTip;
    if (!a || !t) return;
    var ar = (a.closest && a.closest('.var-row')) || a.parentElement;
    var tr = (t.closest && t.closest('.var-row')) || t.parentElement;
    if (!ar || !tr) return;
    var show = TAPER_TYPES.indexOf(value) >= 0;
    var head = ar.previousElementSibling;
    var parts = [ar, tr];
    // only the group's own header, matched on what it says -- so a reordering
    // upstream cannot make this hide somebody else's section
    if (head && head.classList && head.classList.contains('set-header') &&
        (head.innerText || '').trim().toLowerCase() === 'taper') {
      parts.push(head);
    }
    parts.forEach(function (el) {
      if (show) el.style.removeProperty('display');
      else el.style.setProperty('display', 'none', 'important');
    });
  }

  var registered = false;

  function tick() {
    // before the dialog check: the list should hold the face mill by the time
    // the dialog first opens and builds its select from it
    if (!registered) registered = registerFaceMill();
    if (!dialogOpen()) return;
    if (!document.getElementById(PICKER_ID)) build();
    else sync();
  }

  var tries = 400;
  (function wait() {
    // the dialog markup ships in index.html, so the select exists long before
    // the dialog is ever opened
    if (sel()) {
      setInterval(function () { try { tick(); } catch (e) { } }, POLL_MS);
      return;
    }
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "picker": json.dumps(PICKER_ID),
        "cls": json.dumps(CLS),
        "select": json.dumps(SELECT_ID),
        "dialog": json.dumps(DIALOG_ID),
        "poll": json.dumps(POLL_MS),
        "caption": json.dumps(CAPTION),
        "types": json.dumps([list(t) for t in TOOL_TYPES]),
        "taper": json.dumps(list(TAPER_TYPES)),
        "drill_angle": json.dumps(DRILL_ANGLE_ID),
        "drill_type": json.dumps(DRILL_TYPE),
        "face_mill": json.dumps(FACE_MILL),
    }
    assert "%" not in js, "unescaped % survived formatting in tool_icons_js()"
    return js
