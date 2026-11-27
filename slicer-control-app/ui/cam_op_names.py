"""
ui/cam_op_names.py
Gives every CAM operation its own name, so every operation becomes its own
toolpath.

Why
---
Two *rough* operations in one job produce **one** toolpath. The layer an
operation draws into is named by the operation's *type*, not by the operation:
``op-rough.js`` passes ``rename: op.rename ?? "rough"`` and ``op-area.js`` ends
at ``.setLayer(rename ?? state.opname ?? "clear", …)``. Both roughs therefore
hand ``setLayer`` the same string, land in the same layer, and arrive in
Preview and Animate as a single checkbox — there is no way to see, toggle or
animate one of them without the other. A third operation of a different type
(*pocket*) is separate only because its literal differs.

Upstream has the missing half already, but it is manual and undocumented in the
UI. An operation whose **note** contains a ``#tag`` is split off into a layer of
its own (``work/slice.js``)::

    let named = note ? note.split(' ').filter(v => v.charAt(0) === '#') : [];
    let layername = named.length ? named : (note ? `${type} (${note})` : type);
    let opname = note ? layername : undefined;

and the same note drives the label and ``op.rename`` in the op list
(``app/init-ui.js``)::

    rec.rename = notable[0].slice(1);
    label += ` (${rec.rename})`;

So the machinery for "one operation, one toolpath" is all there; what is missing
is that nobody types the tags. This module types them.

How
---
Every operation that does not already carry a ``#tag`` is given one — its type
and the lowest number not already taken by that type, so a job of rough, rough,
pocket becomes ``rough_1``, ``rough_2``, ``pocket_1``. Because the name goes in
the note, *both* upstream paths pick it up: ``op.rename`` for the operations
that read it (rough, pocket, trace) and ``state.opname`` for the ones that only
have the note (contour, level, helical, register, lathe). Any operation type,
including ones added later, separates without further work here.

Numbers are assigned once and then left alone. Deleting ``rough_1`` leaves the
other as ``rough_2`` rather than renumbering it, because the name is how the
operation is identified in the layer list — silently moving it to a number that
used to mean a different operation is worse than a gap in the sequence. A new
rough then takes ``rough_1``, the lowest free number, so names stay unique
without ever being reused while their owner is alive.

A note the user typed is never touched: a ``#tag`` already present wins, and a
note without one keeps upstream's ``type (note)`` behaviour.

The label
---------
Upstream renders a named operation as ``rough (rough_1)`` — the type, then the
name it was just given, which reads as a mistake when the name *is* the type
plus a number. The list is relabelled to the bare name instead. That is done on
the rendered DOM rather than by patching ``opRender``: the op list is rebuilt
wholesale (``listel.innerHTML = html.join('')``) from a function that is not
reachable on the public api, and each rebuilt row carries its record on
``el.rec``, so observing the list and rewriting the labels needs nothing
private. Rows this module did not name — the ops-chain clock and loop markers —
are left exactly as upstream drew them.

Renaming
--------
Upstream can already rename an operation, but only by editing its *note* (the
strip above the operation's pop-up) and typing ``#name`` by hand, without
spaces. Each named row gets a pencil button next to its delete cross instead;
it asks for a name and writes it into the note as the ``#tag``. That is the same
place the automatic name lives, so everything that follows the tag --
``op.rename``, the toolpath layer, the G-code header (ui/cam_gcode_header.py) --
follows the new name too.

* Names must stay unique, or two operations would share one toolpath layer
  again -- the very thing this module exists to prevent. A name already used by
  another operation is refused, ignoring case.
* Spaces are kept, as no-break spaces (U+00A0). Upstream splits the note on
  ordinary spaces, so a plain space would end the tag at the first word.
* An empty name hands the operation back to the automatic ``type_N`` naming.
* A custom name is shown exactly as typed; the automatic ones keep the list's
  capitalised look.

The strip above an operation's pop-up -- upstream's note editor, showing the
raw ``#rough_1 …`` note in a free-text box -- is relabelled ``✎ rough_1`` and
opens the same prompt, so the ``#`` convention never reaches the user. Any
other words in the note are kept, just not shown there.

The pop-up also stays open longer once the pointer leaves it (POP_GRACE_MS on
top of upstream's 250 ms), long enough to reach that strip from the row.

The pencil is a ``mousedown`` handler that stops the event, exactly like
upstream's delete cross: a mousedown on the row itself starts a drag and
rebuilds the list on release, so neither a click nor a double-click on the row
would survive to be handled.
"""

from __future__ import annotations

import json

#: Separator between an operation's type and its number: ``rough_1``.
NAME_SEP = "_"

#: Operation types that are structure rather than cutting, and so are neither
#: named nor relabelled. ``|`` is the end-of-chain clock, ``loop`` repeats the
#: operations after it; neither draws a toolpath layer of its own.
SKIP_TYPES = ("|", "loop")

#: When to (re)assign. ``slice.begin`` is the one that has to be right — it is
#: emitted before the widgets are dispatched to the worker, so a name written
#: there reaches the slicer. The rest keep the list labels in step as
#: operations are added, removed and reordered.
EVENTS = ("slice.begin", "init-done", "settings.saved")

#: Longest name accepted from the rename prompt.
MAX_NAME = 40

#: Classes on the op-list rows: the rename button, and rows whose name was
#: typed by the user (shown without the list's capitalisation).
RENAME_CLS = "piccola-op-rename"
CUSTOM_CLS = "piccola-op-custom"
NAMED_CLS = "piccola-op-named"

#: Extra time, in ms, an operation's pop-up stays open after the pointer
#: leaves it, on top of upstream's own 250 ms. 250 ms is not enough to travel
#: from a row in the list to the pop-up's name strip, which sits above the
#: pop-up and away from the row.
POP_GRACE_MS = 700

#: Text of the rename prompt, from the pencil and from the name strip alike.
PROMPT = "Operation name (also the name of its toolpath)"


def css() -> str:
    """Styling for the rename button, sat beside upstream's delete cross."""
    return "\n".join([
        f"#oplist > div.{NAMED_CLS} {{ padding-right: 38px !important; }}",
        f"#oplist div .{RENAME_CLS} {{",
        "  position: absolute; right: 24px; font-size: 12px;",
        "  color: rgba(0,0,0,0.25); cursor: pointer; z-index: 103;",
        "}",
        f"#oplist div:hover .{RENAME_CLS} {{ color: #888; }}",
        f"#oplist div .{RENAME_CLS}:hover {{ color: #000; }}",
        f"#oplist > div.{CUSTOM_CLS}, #oplist > div.{CUSTOM_CLS} label.label"
        " { text-transform: none !important; }",
        # the pop-up's name strip: reads as a button that renames
        "#camops .pop-tics > div { cursor: pointer; font-size: 12px !important; }",
        "#camops .pop-tics > div:hover { border-color: rgba(0,0,0,0.5) !important; }",
    ])


def op_names_js() -> str:
    """JS giving every CAM operation its own name, hence its own toolpath."""
    return """
(function () {
  var SEP = %(sep)s, SKIP = %(skip)s, EVENTS = %(events)s;
  var MAX = %(max)d, RENAME_CLS = %(rename_cls)s, CUSTOM_CLS = %(custom_cls)s,
      NAMED_CLS = %(named_cls)s, NBSP = '\\u00a0';
  var GRACE = %(grace)d, PROMPT = %(prompt)s;
  var relabelling = false, observer = null;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  function ops() {
    var a = api();
    try {
      var o = a.conf.get().process.ops;
      return Array.isArray(o) ? o : null;
    } catch (e) { return null; }
  }

  function skipped(op) {
    return !op || !op.type || SKIP.indexOf(op.type) >= 0;
  }

  // Upstream's own reading of a note: the first space-delimited word starting
  // with '#', minus the '#'. Anything else in the note is the user's and is
  // left alone.
  function tagOf(op) {
    var note = op && op.note;
    if (typeof note !== 'string' || !note) return null;
    var parts = note.split(' ');
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].charAt(0) === '#' && parts[i].length > 1) {
        return parts[i].slice(1);
      }
    }
    return null;
  }

  // What an operation is called in its automatic name: its type, except that
  // an area op set up as a scallop finish (patch 0029) is a scallop -- that is
  // the menu entry it was added from -- likewise the rotary operations. A tag
  // cannot hold a space (upstream splits the note on spaces), so names that
  // are two words are hyphenated.
  function kind(op) {
    if (op.type === 'area' && op.mode === 'surface' && op.sr_type === 'scallop') {
      return 'scallop';
    }
    // the rotary roughing ops (patch 0045): their types run the words together
    if (op.type === 'rotaryrough') return 'rotary-rough';
    if (op.type === 'surfacerough') return 'surface-rough';
    if (op.type === 'rotarycontour') return 'rotary-contour';
    if (op.type === 'rotarycutoff') return 'cut-off';
    // the lathe op is shown as Rotary Finish in the menu (ui/cam_op_menu.py)
    if (op.type === 'lathe') return 'rotary-finish';
    return op.type;
  }

  // Lowest free number for this type, so a name is never reused while the
  // operation holding it is still in the list. See the module docstring.
  function nextFree(taken, type) {
    for (var n = 1; n < 10000; n++) {
      if (!taken[type + SEP + n]) return n;
    }
    return 1;
  }

  function assignNames() {
    var list = ops();
    if (!list) return false;
    var taken = {}, i, op, tag;
    for (i = 0; i < list.length; i++) {
      if (Array.isArray(list[i])) continue;
      tag = tagOf(list[i]);
      if (tag) taken[tag] = true;
    }
    var changed = false;
    for (i = 0; i < list.length; i++) {
      op = list[i];
      if (Array.isArray(op) || skipped(op) || tagOf(op)) continue;
      var name = kind(op) + SEP + nextFree(taken, kind(op));
      taken[name] = true;
      // Prepended, so a note the user had already written survives ahead of
      // nothing and behind the tag upstream looks for first.
      op.note = op.note ? ('#' + name + ' ' + op.note) : ('#' + name);
      changed = true;
    }
    return changed;
  }

  function display(tag) { return String(tag || '').replace(/\\u00a0/g, ' '); }

  function isAuto(tag, type) {
    var head = type + SEP, rest = tag.slice(head.length);
    return tag.indexOf(head) === 0 && /^[0-9]+$/.test(rest);
  }

  // What the user typed, made safe to be a tag: no '#', no markup characters
  // (opRender puts the name into innerHTML), spaces as no-break spaces.
  function clean(text) {
    return String(text).replace(/[#<>&"']/g, '').trim()
      .replace(/\\s+/g, NBSP).slice(0, MAX);
  }

  // Replace (or with an empty name, drop) the tag in a note, leaving the
  // rest of the note as the user wrote it.
  function setTag(op, name) {
    var parts = (typeof op.note === 'string' && op.note) ? op.note.split(' ') : [];
    var at = -1;
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].charAt(0) === '#' && parts[i].length > 1) { at = i; break; }
    }
    if (name) {
      if (at >= 0) parts[at] = '#' + name; else parts.unshift('#' + name);
    } else if (at >= 0) {
      parts.splice(at, 1);
    }
    op.note = parts.join(' ').trim();
  }

  function rename(rec) {
    var a = api();
    if (!a || !a.uc || !a.uc.prompt) return;
    a.uc.prompt(PROMPT, display(tagOf(rec))).then(function (text) {
      if (text === undefined || text === null) return;      // cancelled
      var name = clean(text), list = ops() || [];
      if (name) {
        for (var i = 0; i < list.length; i++) {
          var other = list[i];
          if (other === rec || Array.isArray(other)) continue;
          var t = tagOf(other);
          if (t && t.toLowerCase() === name.toLowerCase()) {
            if (a.uc.alert) a.uc.alert('"' + display(name) + '" is already the name of another operation');
            return;
          }
        }
      }
      setTag(rec, name);
      if (!name) assignNames();            // back to the automatic type_N name
      rec.rename = tagOf(rec) || undefined;
      try { a.conf.save(); } catch (e) { }
      try { a.event.emit('cam.op.render'); } catch (e) { sync(); }
    });
  }

  function addRenameButton(row, rec) {
    if (row.querySelector('.' + RENAME_CLS)) return;
    var b = document.createElement('label');
    b.className = RENAME_CLS;
    b.title = 'Rename operation';
    b.innerHTML = '<i class="fa-solid fa-pen"></i>';
    // mousedown, stopped, like upstream's delete cross: a mousedown that
    // reaches the row starts a drag instead
    b.onmousedown = function (ev) {
      ev.stopPropagation();
      ev.preventDefault();
      rename(rec);
    };
    row.appendChild(b);
  }

  // `opRender` writes `rough (rough_1)`; with a name that already carries its
  // type that reads as a stutter, so the row is relabelled to the bare name.
  function relabel() {
    var listel = document.getElementById('oplist');
    if (!listel) return;
    relabelling = true;
    try {
      var rows = listel.children;
      for (var i = 0; i < rows.length; i++) {
        var rec = rows[i].rec;
        if (!rec || skipped(rec)) continue;
        var tag = tagOf(rec);
        if (!tag) continue;
        var lbl = rows[i].querySelector('label.label'), shown = display(tag);
        if (lbl && lbl.textContent !== shown) lbl.textContent = shown;
        rows[i].classList.add(NAMED_CLS);
        // automatic under the kind it was named for, or under its type
        // (an area switched to scallop keeps its area_N name)
        rows[i].classList.toggle(CUSTOM_CLS, !isAuto(tag, kind(rec)) && !isAuto(tag, rec.type));
        addRenameButton(rows[i], rec);
        slowUnpop(rows[i]);
      }
    } finally {
      relabelling = false;
    }
    relabelStrip();
  }

  // The operation whose pop-up is (or was last) open: the row last under the
  // pointer, since the pop-up only opens on hovering a row.
  var hoveredRec = null;

  // The strip above the pop-up is upstream's note editor, which shows the raw
  // note ("#rough_1 …") and edits it in a free-text box. It is turned into a
  // plain "✎ name" button that opens the same rename prompt as the pencil.
  function relabelStrip() {
    var camops = document.getElementById('camops');
    if (!camops || !hoveredRec) return;
    var tag = tagOf(hoveredRec);
    if (!tag) return;
    var labels = camops.querySelectorAll('.pop-tics label');
    for (var i = 0; i < labels.length; i++) {
      var text = '\\u270e ' + display(tag);
      if (labels[i].textContent !== text) labels[i].textContent = text;
      labels[i].parentNode.title = 'Click to rename this operation';
    }
  }

  // Upstream closes a pop-up 250 ms after the pointer leaves it, through the
  // row's `unpop`, which its timer looks up on the row at call time. Wrapped,
  // the close waits POP_GRACE_MS more and is dropped if the pointer is back
  // on the row or the pop-up by then -- either one's mouseleave starts the
  // next attempt, so it cannot stay open. Hovering another row still closes at
  // once: its onEnter calls this same function first, and the two pop-ups can
  // be the same element (one per operation *type*).
  function slowUnpop(row) {
    if (row.__piccolaUnpop || typeof row.unpop !== 'function') return;
    var real = row.unpop, timer = null;
    row.__piccolaUnpop = true;
    row.unpop = function () {
      clearTimeout(timer);
      var listel = document.getElementById('oplist');
      var other = listel && listel.querySelector(':scope > div:hover');
      if (other && other !== row) return real();
      timer = setTimeout(function () {
        if (row.matches(':hover') || document.querySelector('#camops > .cam-pop-op:hover')) return;
        real();
      }, GRACE);
    };
  }

  function sync() {
    assignNames();
    relabel();
  }

  function install() {
    var a = api();
    if (!a || !a.event || !a.conf) return false;
    var listel = document.getElementById('oplist');
    if (!listel) return false;
    if (listel.__piccolaOpNames) return true;
    listel.__piccolaOpNames = true;

    EVENTS.forEach(function (ev) {
      try { a.event.on(ev, sync); } catch (e) { }
    });

    // `opRender` rebuilds the list with `innerHTML = …` and is not reachable on
    // the public api, so the list itself is watched instead. Relabelling edits
    // the rows, which would re-enter the observer -- hence the guard.
    try {
      observer = new MutationObserver(function () {
        if (relabelling) return;
        sync();
      });
      observer.observe(listel, { childList: true });
    } catch (e) { }

    // Which operation the pop-up belongs to (see hoveredRec).
    listel.addEventListener('mouseover', function (ev) {
      var t = ev.target;
      while (t && t !== listel && !t.rec) t = t.parentNode;
      // a loop row has a pop-up too but no name: forget the last row rather
      // than let its strip rename that one
      if (t && t.rec) hoveredRec = skipped(t.rec) ? null : t.rec;
    });

    var camops = document.getElementById('camops');
    if (camops) {
      // The pop-up is appended to #camops on every hover, with upstream's
      // note text already written into the strip -- rewrite it each time.
      try {
        new MutationObserver(relabelStrip).observe(camops, { childList: true });
      } catch (e) { }
      // A click on the strip renames. Caught on the way down, so upstream's
      // own onclick on the strip (its free-text note editor) never runs.
      camops.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!(t && t.closest && t.closest('.pop-tics')) || !hoveredRec) return;
        ev.stopPropagation();
        ev.preventDefault();
        rename(hoveredRec);
      }, true);
    }

    sync();
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "sep": json.dumps(NAME_SEP),
        "skip": json.dumps(list(SKIP_TYPES)),
        "events": json.dumps(list(EVENTS)),
        "max": MAX_NAME,
        "rename_cls": json.dumps(RENAME_CLS),
        "custom_cls": json.dumps(CUSTOM_CLS),
        "named_cls": json.dumps(NAMED_CLS),
        "grace": POP_GRACE_MS,
        "prompt": json.dumps(PROMPT),
    }
