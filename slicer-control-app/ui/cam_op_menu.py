"""
ui/cam_op_menu.py
Names the *add operation* button, and sorts the menu behind it by strategy.

The button
----------
It is a bare ``+``. That is enough once you know the app and nothing at all
before then: a plus under a list of operations could as easily mean "duplicate
the selected one". It now says what it adds.

The menu
--------
Seventeen operations in one flat column, ordered by nothing in particular —
``laser on`` and ``laser off`` first, ``loop`` last, ``rough`` fourteenth. The
order is upstream's markup order, which is not an order at all.

Machining has a natural one. You take material off fast and inaccurately, then
you take a little off slowly and accurately, and separately from both there are
the operations that are not cutting strategies at all — drilling, flipping the
part, registration holes, a loop, raw gcode. So:

* **rough strategy** — the ops whose job is volume removal, and which all carry
  a *stock to leave* so that something is left for the second pass.
* **finish strategy** — the ops that cut to the final surface, following
  geometry rather than clearing area.
* **other** — everything else, which is not a lesser category but a different
  kind of thing.

The split is a judgement, and one worth stating rather than burying:

``level`` faces the top of the blank flat, ``rough`` clears bulk to a shadow,
``pocket`` clears down onto selected surfaces and ``area`` clears within a
boundary — all four remove volume and all four take a leave amount.
``outline`` cuts the part free on its silhouette, ``contour`` passes back and
forth across the form in X or Y, ``trace`` follows selected edges, and
``helical`` descends a conical selection, and ``scallop`` (patch 0029) finishes
the 3-D surface with a ball end mill in rings a constant distance apart along
it — all five cut to final size and none of them are trying to be quick.

Every rotary operation has its own group, *rotary (4-axis)*: roughing
(``rotary rough``, ``surface rough``, patch 0045), finishing (``lathe``, shown
as *Rotary Finish*; ``rotary contour``, patch 0046), then ``index`` and
``loop``. Kiri shows them only in Rotary Mode; when it hides every entry of a
group, the group's header goes too (``syncHeads``), so a flat job's menu has
no empty *rotary* heading.

``area`` is the honest awkward case: its own tooltip says "many operations are
simple wrappers around this", so it can be either. It sits under roughing
because that is what it does when nobody has told it otherwise.

Unlisted operations
-------------------
Anything not named in :data:`OP_GROUPS` falls into the last group rather than
disappearing. So a Kiri upgrade that adds an operation puts it under *other*,
where it is visible and usable, instead of silently dropping it out of the menu
— which is the failure mode that matters here, because the menu is the only way
to reach these at all.
"""

from __future__ import annotations

import json

#: Entries whose menu text is not how the thing is spelled: menu name -> label.
#:
#: Renaming one is not free. Kiri's handler is
#: ``switch (ev.target.innerText.toLowerCase())``, so an entry that no longer
#: says "gcode" stops adding a gcode operation. Each renamed entry therefore
#: gets a click shim that hands the original word back — see ``rename()``.
OP_RENAMES = {
    "gcode": "G-code",
    # Kiri's lathe op is the rotary finish: the name it has next to Rotary
    # Rough / Surface Rough / Rotary Contour in the rotary group
    "lathe": "Rotary Finish",
}

#: Class on a renamed entry. It opts out of the capitalize rule the others use,
#: because CSS ``capitalize`` treats a hyphen as a word break and would render
#: "G-code" as "G-Code".
EXACT_CLS = "piccola-op-exact"

#: Attribute naming the diagram each entry wants, read by the hover panel.
#: Spelled here rather than imported from ``ui/cam_op_art.py`` so the two
#: modules stay independent of injection order.
ART_ATTR = "data-piccola-art"

#: Marks the list as sorted, so a second run is a no-op.
DONE_ATTR = "data-piccola-op-groups"

#: Class on the inserted headers.
HEAD_CLS = "piccola-op-head"

#: Class on the relabelled add button's text.
LABEL_CLS = "piccola-op-add-label"

#: Ids of the two elements this touches, both static in Kiri's index.html.
LIST_ID = "op-add-list"
BUTTON_ID = "op-add"

#: What the add button says. Lowercase because Kiri renders these with
#: ``text-transform: capitalize``, so this comes out matching its neighbours.
#: The plus is the icon already in the button, kept and bracketed rather than
#: replaced — it is what people have been clicking.
ADD_LABEL = "add operations ("
ADD_LABEL_END = ")"

#: Strategy -> the operations under it, by the exact text each menu entry
#: carries. Order within a group is the order they are offered in, which is
#: roughly the order a job uses them.
#:
#: The final group collects anything not named above it, so an operation added
#: upstream lands there instead of vanishing. Its name is therefore the one
#: that has to stay generic.
OP_GROUPS = (
    ("rough strategy", ("level", "rough", "pocket", "area")),
    ("finish strategy", ("outline", "contour", "trace", "helical", "scallop")),
    # roughing, then finishing, then the two that turn the part / repeat ops
    ("rotary (4-axis)", ("rotary rough", "surface rough", "lathe",
                         "rotary contour", "cut off", "index", "loop")),
    ("other", ()),
)


def css() -> str:
    """Styling for the group headers and the named add button."""
    return "\n".join([
        # the button is a flex column by default (.opdiv), which would stack the
        # label above the icon
        f"#{BUTTON_ID} {{",
        "  flex-direction: row !important;",
        "  align-items: center !important;",
        "  justify-content: center !important;",
        "  gap: 3px !important;",
        "}",
        f".{LABEL_CLS} {{",
        "  text-transform: capitalize !important;",
        "  white-space: nowrap !important;",
        "}",
        # Kiri sets `text-transform: none !important` on this list, which is why
        # every operation reads lowercase while the rest of the UI is title
        # case. These are proper names for strategies, so they get capitals.
        # The click handler switches on innerText, but innerText reports the
        # *source* text rather than the transformed rendering, so this is
        # display-only and "rough" still matches.
        f"#{LIST_ID} > div {{",
        "  text-transform: capitalize !important;",
        "  white-space: nowrap !important;",
        "}",
        # Three equal columns. Kiri's `1fr 1fr 1fr` sits in a pop-up too narrow
        # for its content, so each column shrinks to its own longest name and
        # one long entry made its column wider than the others. Sized to its
        # content instead, the list gives every `1fr` the same width -- that
        # of the widest entry -- and the buttons line up.
        f"#{LIST_ID} {{",
        "  width: max-content !important;",
        "  grid-template-columns: repeat(3, 1fr) !important;",
        "}",
        # a renamed entry is spelled out in full and must not be re-cased
        f"#{LIST_ID} > div.{EXACT_CLS} {{",
        "  text-transform: none !important;",
        "}",
        # headers are labels, not entries: no border, no hover, not clickable
        f"#{LIST_ID} > div.{HEAD_CLS} {{",
        # the list is a three-column grid (.op-grid wins with !important), so a
        # header left as an ordinary cell sits *beside* two operations instead
        # of above its group
        "  grid-column: 1 / -1 !important;",
        "  border: none !important;",
        "  background: transparent !important;",
        "  padding: 5px 2px 1px 2px !important;",
        "  margin: 0 !important;",
        "  font-size: 10px !important;",
        "  font-weight: 600 !important;",
        "  letter-spacing: 0.04em !important;",
        "  text-transform: uppercase !important;",
        "  text-align: left !important;",
        "  opacity: 0.55 !important;",
        "  cursor: default !important;",
        "  pointer-events: none !important;",
        "}",
        # the first header should not push the menu open with a gap on top
        f"#{LIST_ID} > div.{HEAD_CLS}:first-child {{",
        "  padding-top: 1px !important;",
        "}",
    ])


def op_menu_js() -> str:
    """JS naming the add button and grouping the operation menu."""
    js = """
(function () {
  var LIST_ID = %(list)s, BUTTON_ID = %(button)s, DONE = %(done)s;
  var HEAD_CLS = %(head)s, LABEL_CLS = %(label_cls)s;
  var ADD_LABEL = %(add_label)s, ADD_LABEL_END = %(add_end)s;
  var GROUPS = %(groups)s, ART_ATTR = %(art_attr)s;
  var RENAMES = %(renames)s, EXACT_CLS = %(exact_cls)s;

  function label() {
    var b = document.getElementById(BUTTON_ID);
    if (!b) return false;
    if (b.getElementsByClassName(LABEL_CLS).length) return true;
    // 'i, svg' because FontAwesome's script rewrites <i class="fas fa-plus">
    // into an <svg> in place, and whether that has happened yet depends on load
    // order. It replaces the node at the same position either way, so brackets
    // put around it now still sit around it afterwards.
    var icon = b.querySelector('i, svg');
    // text, then the existing plus icon, then the closing bracket -- the icon
    // is kept rather than replaced because it is what people already aim at
    var open = document.createElement('span');
    open.className = LABEL_CLS;
    open.textContent = ADD_LABEL;
    var close = document.createElement('span');
    close.className = LABEL_CLS;
    close.textContent = ADD_LABEL_END;
    if (icon) {
      b.insertBefore(open, icon);
      b.insertBefore(close, icon.nextSibling);
    } else {
      open.textContent = ADD_LABEL + ADD_LABEL_END;
      b.insertBefore(open, b.firstChild);
    }
    return true;
  }

  function group() {
    var list = document.getElementById(LIST_ID);
    if (!list) return false;
    if (list.getAttribute(DONE)) return true;
    // entries are plain divs whose text is what the click handler switches on
    var items = {}, order = [];
    var kids = [].slice.call(list.children);
    for (var i = 0; i < kids.length; i++) {
      var key = (kids[i].innerText || '').trim().toLowerCase();
      if (!key) continue;
      items[key] = kids[i];
      order.push(key);
      // names which diagram the hover panel should draw for this entry; the
      // panel ignores it when no diagram of that name is registered
      kids[i].setAttribute(ART_ATTR, key);
    }
    if (!order.length) return false;

    var used = {}, frag = document.createDocumentFragment();
    for (var g = 0; g < GROUPS.length; g++) {
      var name = GROUPS[g][0], names = GROUPS[g][1], picked = [];
      if (g === GROUPS.length - 1) {
        // last group takes whatever is left, in the order upstream had it, so
        // an operation added in a future Kiri still appears
        for (var k = 0; k < order.length; k++) {
          if (!used[order[k]]) picked.push(items[order[k]]);
        }
      } else {
        for (var n = 0; n < names.length; n++) {
          if (items[names[n]]) { picked.push(items[names[n]]); used[names[n]] = 1; }
        }
      }
      if (!picked.length) continue;
      var head = document.createElement('div');
      head.className = HEAD_CLS;
      head.textContent = name;
      frag.appendChild(head);
      for (var p = 0; p < picked.length; p++) frag.appendChild(picked[p]);
    }
    // appending a node that is already a child moves it, so this reorders the
    // originals rather than cloning them -- their ids, titles and any handler
    // bound to them survive
    list.appendChild(frag);
    rename(list, items);
    list.setAttribute(DONE, '1');
    syncHeads(list);
    // Kiri switches entries on and off with their inline display as the
    // stock goes in and out of Rotary Mode; follow it
    new MutationObserver(function () { syncHeads(list); })
      .observe(list, { attributes: true, subtree: true, attributeFilter: ['style'] });
    return true;
  }

  // A header whose entries are all hidden is hidden with them.
  function syncHeads(list) {
    var kids = list.children, head = null, shown = false;
    function close() {
      if (!head) return;
      var want = shown ? '' : 'none';
      // only on a change: this runs from an observer on these attributes
      if (head.style.display !== want) head.style.display = want;
    }
    for (var i = 0; i < kids.length; i++) {
      if (kids[i].classList.contains(HEAD_CLS)) {
        close();
        head = kids[i];
        shown = false;
      } else if (kids[i].style.display !== 'none') {
        shown = true;
      }
    }
    close();
  }

  // Kiri's handler reads the entry's own text to decide what to add, so
  // changing that text silently breaks the entry. The shim below intercepts the
  // click before the list sees it and calls the list's handler with the word it
  // is still expecting -- the handler only ever looks at target.innerText, so a
  // stand-in object is enough and nothing has to be restored afterwards.
  function rename(list, items) {
    for (var key in RENAMES) {
      var el = items[key];
      if (!el) continue;
      el.textContent = RENAMES[key];
      el.classList.add(EXACT_CLS);
      (function (name) {
        el.addEventListener('click', function (ev) {
          ev.stopPropagation();
          if (typeof list.onclick === 'function') {
            list.onclick({ target: { innerText: name } });
          }
        }, true);
      })(key);
    }
  }

  var tries = 400;
  (function wait() {
    var a = false, b = false;
    try { a = label(); } catch (e) { }
    try { b = group(); } catch (e) { }
    if (a && b) return;
    if (tries-- > 0) setTimeout(wait, 50);
  })();
})();
""" % {
        "list": json.dumps(LIST_ID),
        "button": json.dumps(BUTTON_ID),
        "done": json.dumps(DONE_ATTR),
        "head": json.dumps(HEAD_CLS),
        "label_cls": json.dumps(LABEL_CLS),
        "add_label": json.dumps(ADD_LABEL),
        "add_end": json.dumps(ADD_LABEL_END),
        "groups": json.dumps([[n, list(v)] for n, v in OP_GROUPS]),
        "art_attr": json.dumps(ART_ATTR),
        "renames": json.dumps(OP_RENAMES),
        "exact_cls": json.dumps(EXACT_CLS),
    }
    assert "%" not in js, "unescaped % survived formatting in op_menu_js()"
    return js
