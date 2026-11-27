"""
ui/cam_import_file.py
Puts *import file* back in the files menu, next to *open project*.

Why
---
Kiri has one import control. `#file-import` opens a single hidden file input
and `platform.js#load_files` decides what to do from the extension: `.stl`,
`.obj`, `.3mf`, `.step`/`.stp` (patch 0032), `.svg`, `.dxf`, `.png`, `.jpg`,
`.gcode`, `.nc` are loaded onto the platform, while `.km` and `.kmz` are
settings and replace the workspace.
One control, two quite different outcomes.

`ui/cam_labels.py` renames that control to *open project*, to pair with *save
project* — and the note there recorded the trade as safe because "models arrive
through the app's own File Manager". That was wrong. [ui/pages/file_manager.py]
handles machine jobs and point clouds and has no path into Kiri, so
`#file-import` is the *only* way to get an STL onto the platform, and after the
rename nothing in the menu says so. The question "how do I import an STL" has a
menu item that answers it and a name that hides it.

So the name is not fixed, it is duplicated: a second item, labelled for the
other half of what the control does, sitting directly above it. *open project*
keeps its pairing with *save project*, and *import file* says the thing a
person looks for when they have a part to cut.

How
---
By adding a sibling that calls the same public entry point. `api.event.import`
is what upstream's own item calls (`init/sync.js`: `$('file-import').onclick =
(ev) => { api.event.import(ev) }`), so both items are one line over the same
control and there is no second load path to keep in step.

Nothing narrows `accept`
------------------------
The tempting refinement — `.km,.kmz` on one item and the model formats on the
other — is left undone on purpose. `accept` is one attribute on one shared
input, so per-item filtering means writing it on every click, and upstream's
list is already *narrower than the loader*: `load_files` handles `.3mf`,
`.ini`, `.gbr`, `.b64` and extension-less raw vertex files that the attribute
never mentions. Narrowing it further would hide more formats than it filters,
and a file dialog that refuses a file the app can open is worse than one that
offers too much.

Clicking it opens exactly one dialog
------------------------------------
Worth recording, because the markup invites the opposite conclusion. The hidden
input is a *child* of `#file-import`, so `input.click()` bubbles back into that
item's own onclick — upstream already re-enters itself once on every import,
measured in this app's Chromium: handler twice, input clicked once. The second
click is swallowed by the input's *click in progress* flag, so only one chooser
opens. A sibling item behaves the same way: it fires once, `#file-import` fires
once from the bubble, and its `api.event.import` is the call that gets
swallowed. One dialog either way, which is why this needs no input of its own.
"""

from __future__ import annotations

import json

#: Upstream's import item, which this is inserted directly above and which
#: carries the hidden `#load-file` input inside it.
ANCHOR_ID = "file-import"

#: The new item. Prefixed because it is ours: nothing upstream may bind to it.
ITEM_ID = "piccola-file-import"

#: What it says. No `lk` attribute is set on the label, deliberately — that is
#: how `ui/cam_labels.py` finds the labels it rewrites, and this one is not
#: Kiri's to translate or to rename.
ITEM_TEXT = "import file"

#: FontAwesome is already loaded for the menu; upstream's own import item uses
#: `fa-file-upload` and this is its inward-pointing counterpart.
ITEM_ICON = "fas fa-file-import"

#: Kept in the title, where there is room for the part the label cannot say.
ITEM_TITLE = "load a model or G-code file onto the platform"


def import_file_js() -> str:
    """JS adding an *import file* item to the files menu."""
    return """
(function () {
  var ANCHOR = %(anchor)s, ID = %(id)s, TEXT = %(text)s;
  var ICON = %(icon)s, TITLE = %(title)s;

  function api() {
    try { return window.kiri && window.kiri.api; } catch (e) { return null; }
  }

  // Built in the shape menuItem() emits -- a <label> then a <span> holding the
  // icon -- because menubar.css styles `.content > div` and its two children
  // by position. A different shape would sit in the menu looking wrong.
  function build() {
    var div = document.createElement('div');
    div.id = ID;
    div.title = TITLE;
    var label = document.createElement('label');
    label.appendChild(document.createTextNode(TEXT));
    var span = document.createElement('span');
    var i = document.createElement('i');
    i.className = ICON;
    span.appendChild(i);
    div.appendChild(label);
    div.appendChild(span);
    div.onclick = function (ev) {
      var a = api();
      // the same public call upstream's own item makes
      try { a.event['import'](ev); } catch (e) { }
    };
    return div;
  }

  function install() {
    var a = api();
    if (!a || !a.event || typeof a.event['import'] !== 'function') return false;
    var anchor = document.getElementById(ANCHOR);
    if (!anchor || !anchor.parentNode) return false;
    if (document.getElementById(ID)) return true;
    // above it: importing a part is the everyday action and opening a project
    // the occasional one
    anchor.parentNode.insertBefore(build(), anchor);
    return true;
  }

  var tries = 400;
  (function wait() {
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "anchor": json.dumps(ANCHOR_ID),
        "id": json.dumps(ITEM_ID),
        "text": json.dumps(ITEM_TEXT),
        "icon": json.dumps(ITEM_ICON),
        "title": json.dumps(ITEM_TITLE),
    }
