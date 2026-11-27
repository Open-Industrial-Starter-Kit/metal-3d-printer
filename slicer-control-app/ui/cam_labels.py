"""
ui/cam_labels.py
Relabels a few controls inside the vendored CAM tools.

Kept separate from the styling (``ui/cam_theme.py``) and the export rework
(``ui/cam_export.py``) because it is a different kind of change: not colour, not
behaviour, just wording that does not fit a CNC machine.

The labels are re-applied through a MutationObserver rather than set once. Each
tool populates its own text from a language pack *after* our script runs, and
re-applies it on any language change, so a single assignment at load would be
silently overwritten.

Casing is left to the tools: Kiri renders toolbar labels with
``text-transform: capitalize``, so the replacements are written lowercase and
come out matching their neighbours.

Three ways to name a control, by what there is to hold on to.
:data:`RELABEL` takes a CSS selector, for anything with an id of its own.
:data:`RELABEL_UI` takes a Kiri ``api.ui`` key and walks out to the row — the
right anchor for the settings panel, whose rows carry no ids and whose order is
not ours to depend on. :data:`RELABEL_LANG` renames the *word* rather than a
control, for text Kiri stamps into many places from one language key.
"""

from __future__ import annotations

#: mode -> {CSS selector: replacement label}
#:
#: Selectors rather than ids: the control that carries the *text* is not always
#: the one carrying the id. EasyTrace's filename caption is a `<label for=...>`
#: beside the input, so there is nothing else to anchor it on.
#:
#: "slice" is slicer vocabulary inherited from Kiri's 3D-printing side. In CAM
#: the button computes toolpaths, and "generate" is what that means to someone
#: cutting a part — the word "slice" only makes sense if you think in layers.
#:
#: "Base Project Name" describes what EasyTrace does with the field — it is the
#: stem it builds every per-operation filename from. Here it is simply the name
#: of the file that comes out, because ui/pages/cam.py names the export after it
#: (see _batch_filename), so the label says that instead.
#: "indexed" is Kiri's word for stock mounted in a rotary indexer, and it reads
#: as a state rather than a switch. It used to sit under a *rotary* heading and
#: could therefore just say "enable"; the heading is gone (one checkbox does not
#: need a captioned group around it), so the switch has to name itself, and says
#: "rotary mode". The row is addressed through the id ui/cam_rotary.py assigns
#: it rather than by position.
RELABEL = {
    "carve": {
        "#act-slice": "generate",
        "#piccola-rotary-camStockIndexed label": "rotary mode",
    },
    "pcb": {
        'label[for="exporter-filename"]': "File Name",
    },
}

#: mode -> {Kiri `api.ui` key: replacement label}
#:
#: For settings-panel rows, which have no ids: the control itself is the anchor,
#: and the row is found by walking out to the `.var-row` wrapper around it.
#:
#: "ease down" is Kiri's name for plunging along a descent rather than straight
#: in — its own tooltip says "plunge cuts will spiral down or ease along a
#: linear path". That move is a ramp, which is what it is called at the machine
#: and in every other CAM package; "ease down" only reads as gentleness.
RELABEL_UI = {
    "carve": {
        "camEaseDown": "ramp",
    },
}

#: mode -> {Kiri language key: replacement text}
#:
#: Written into ``api.language.current``, which is the tool's own mechanism:
#: every control that names the key picks the new wording up as it is created.
#:
#: The write has to be *repeated*, not made once. ``init_lang()`` emits
#: ``init.lang`` and only then calls ``LANG.set()``, which assigns every key of
#: the chosen map over ``LANG.current`` — so anything written before that point
#: is silently wiped, and an injected script racing Kiri's boot usually loses.
#: It is therefore re-applied on ``init.lang``, ``init.one``, ``init.two`` and
#: on settings changes; the writes are idempotent, and the CAM menu (with the
#: operation popups that carry this label) is built well after the last of
#: them.
#:
#: ``cc_spnd_s`` is the per-operation spindle field, and every operation type
#: that has a spindle builds its own copy of it (ten call sites in
#: ``mode/cam/app/cl-ops.js``) — so this is a rename of the term, not of one
#: control. Kiri ships "spindle rpm" and renders panel labels through
#: ``text-transform: capitalize``, which title-cases each word and leaves the
#: rest alone: "rpm" comes out as "Rpm". Writing the unit in capitals here
#: survives that — "spindle RPM" renders as "Spindle RPM" — where fixing it in
#: CSS could not, since `capitalize` cannot know which words are initialisms.
#: ``cc_rapd_s`` / ``cc_rzpd_s`` are the two rows in the *limits* group, and
#: they are **ceilings, not speeds** — `prepare.js` clamps every operation's
#: requested feed against them::
#:
#:     feedRate   = Math.min(camFastFeed, feed || feedRate || plunge);
#:     plungeRate = Math.min(camFastFeed, feedRate || plunge, ...);
#:
#: so an operation asking for 1200 mm/min still exports F500 if the limit says
#: 500. Labelled plainly "feed rate" and "plunge rate" they read as the speed
#: the job will cut at, which is the one thing they are not; upstream's own
#: tooltips already say "max xy cutting feedrate" and "max z cutting plunge
#: rate", so the labels are simply catching up with them. Each key is used at
#: exactly one call site, so renaming the term cannot touch a per-operation
#: feed field, which really is a speed.
RELABEL_LANG = {
    "carve": {
        "cc_spnd_s": "spindle RPM",
        # The rough op's radial stock, beside "leave stock z". Plain "leave
        # stock" did not say it was the X/Y (side-wall) amount, which made the
        # pair read as "stock" and "stock, but only Z". One call site
        # (`cl-ops.js`, the rough op). Capitals survive text-transform, so this
        # renders "Leave Stock XY".
        "cr_lsto_s": "leave stock XY",
        "cc_rapd_s": "max feed rate",
        "cc_rzpd_s": "max plunge rate",
        # The two halves of one round trip, named as a pair. Upstream calls
        # them "export workspace" (File > export work) and "import", which do
        # not read as opposites and do not say that the second one opens what
        # the first one wrote. Both keys have exactly one call site each
        # (`init/menu.js`, `#app-export` and `#file-import`), so renaming the
        # term cannot reach anything else.
        #
        # `#file-import` also accepts STL, OBJ, 3MF, SVG, DXF, PNG and
        # G-code, so "open project" is narrower than what the control does.
        # That was first excused on the grounds that models arrive through the
        # app's own File Manager — they do not: `ui/pages/file_manager.py`
        # handles machine jobs and point clouds and has no path into Kiri, so
        # this rename left the only route to an STL hidden behind a name that
        # denies it. The fix is not here, because "save project" and "open
        # project" are worth keeping as a pair: `ui/cam_import_file.py` adds a
        # second item, "import file", over the same control.
        "rc_xpws": "save project",
        "import": "open project",
    },
}


def relabel_js(mode: str) -> str:
    """JS that applies and maintains *mode*'s label overrides."""
    mapping = RELABEL.get(mode)
    ui_mapping = RELABEL_UI.get(mode)
    lang_mapping = RELABEL_LANG.get(mode)
    if not mapping and not ui_mapping and not lang_mapping:
        return ""
    pairs = ",".join(f"{_q(k)}:{_q(v)}" for k, v in (mapping or {}).items())
    ui_pairs = ",".join(f"{_q(k)}:{_q(v)}" for k, v in (ui_mapping or {}).items())
    lang_pairs = ",".join(f"{_q(k)}:{_q(v)}" for k, v in (lang_mapping or {}).items())
    return f"""
(function () {{
  var LABELS = {{{pairs}}};
  var UI_LABELS = {{{ui_pairs}}};
  var LANG_LABELS = {{{lang_pairs}}};

  function textNodeOf(host) {{
    // Kiri wraps each action label in <label lk="...">. A settings row has a
    // plain <label> instead, and that distinction matters: writing to the row
    // itself would replace its checkbox with a text node. Only fall through to
    // the host when it carries no label of its own — as when the selector
    // already names one.
    return host.querySelector('label[lk]')
        || host.querySelector('label')
        || host;
  }}

  // Rename the word itself, in the language table the panels are built from,
  // so controls are created with the new wording rather than corrected after.
  // The old text is kept so anything already on screen can be caught up
  // (fixLang below).
  //
  // Idempotent on purpose: LANG.set() rewrites every key of the language map
  // over LANG.current during boot, after init.lang is emitted, so a single
  // early write does not survive. This is re-run on the init events instead.
  var langWas = {{}};
  function setLang() {{
    var lang;
    try {{ lang = window.kiri.api.language.current; }} catch (e) {{ return; }}
    if (!lang) return;
    Object.keys(LANG_LABELS).forEach(function (key) {{
      var want = LANG_LABELS[key];
      if (lang[key] === want) return;
      if (langWas[key] === undefined) langWas[key] = lang[key];
      lang[key] = want;
    }});
  }}

  // Catch up any label built before setLang() landed. Scoped to the operation
  // popups (.cam-pop-op) and the settings rows, so this cannot touch prose.
  function fixLang() {{
    Object.keys(LANG_LABELS).forEach(function (key) {{
      var want = LANG_LABELS[key];

      // Anything rendered from the language table keeps the key on the
      // element (`menuItem` puts `lk` on the label it builds), so those are
      // found exactly rather than by matching their text. This is the only
      // thing that reaches the MENUS: a menu label is resolved once, at build
      // time -- `resolved = lk ? tr(lk, text) : text` -- so rewriting the table
      // afterwards does not touch it, and the walk below only covers settings
      // rows and operation popups. That is why "save project" and "open
      // project" appeared to do nothing while the settings renames worked.
      var keyed = document.querySelectorAll('[lk="' + key + '"]');
      for (var k = 0; k < keyed.length; k++) {{
        if (keyed[k].textContent !== want) keyed[k].textContent = want;
      }}

      // Controls built with a literal string carry no key, so those are still
      // found by the text they were given. Only the label's own leading text
      // node is compared and replaced: ui/cam_ranges.py appends a range hint
      // inside some labels ("leave stock z (-10 ~ 10)"), and matching the
      // whole textContent would miss those -- or, replaced, drop the hint.
      var was = langWas[key];
      if (!was || was === want) return;
      var labels = document.querySelectorAll('.cam-pop-op label, .var-row label');
      for (var i = 0; i < labels.length; i++) {{
        var t = labels[i].firstChild;
        if (t && t.nodeType === 3 && t.data.trim() === was) t.data = want;
      }}
    }});
  }}

  // The operation popups are built once and kept DETACHED: Kiri appends one
  // to #camops only while its operation is hovered. A document-wide pass at
  // the init events never sees them, which is why the popup renames did not
  // show. So catch each one as it is attached.
  var popWatched = false;
  function watchPops() {{
    if (popWatched) return true;
    var host = document.getElementById('camops');
    if (!host || typeof MutationObserver !== 'function') return false;
    try {{
      new MutationObserver(fixLang).observe(host, {{ childList: true }});
      popWatched = true;
    }} catch (e) {{ }}
    return popWatched;
  }}

  // Resolve a Kiri api.ui key to the .var-row wrapper around that control.
  function rowFor(key) {{
    var ui;
    try {{ ui = window.kiri.api.ui; }} catch (e) {{ return null; }}
    var el = ui && ui[key];
    if (!el) return null;
    for (var n = el, hops = 0; n && hops < 3; n = n.parentNode, hops++) {{
      if (n.classList && n.classList.contains('var-row')) return n;
    }}
    return null;
  }}

  function apply(host, want) {{
    var el = textNodeOf(host);
    if (el && el.textContent !== want) el.textContent = want;
  }}

  var installed = {{}};

  function install() {{
    var done = 0, want_total = 0;
    setLang();
    fixLang();
    function place(id, host, want) {{
      if (!host) return;
      done++;
      apply(host, want);
      // Poll until *every* label resolves, but observe each host once: one
      // that arrives late would otherwise leave the ones already found
      // collecting a fresh MutationObserver every 50 ms.
      if (installed[id]) return;
      installed[id] = true;
      // The language pack rewrites labels after load and on any change, so
      // watch and re-apply rather than assuming one assignment sticks.
      try {{
        new MutationObserver(function () {{ apply(host, want); }})
          .observe(host, {{ childList: true, subtree: true, characterData: true }});
      }} catch (e) {{ /* observer unavailable — the initial set still applies */ }}
    }}

    Object.keys(LABELS).forEach(function (sel) {{
      want_total++;
      place('sel:' + sel, document.querySelector(sel), LABELS[sel]);
    }});
    Object.keys(UI_LABELS).forEach(function (key) {{
      want_total++;
      place('ui:' + key, rowFor(key), UI_LABELS[key]);
    }});
    return done === want_total;
  }}

  // Re-apply the wording past every point that can overwrite it. LANG.set()
  // runs after init.lang is emitted and assigns the whole map, so subscribing
  // to the later init events is what makes the rename stick.
  function relang() {{
    setLang();
    fixLang();
  }}
  function watchLang() {{
    var a;
    try {{ a = window.kiri.api; }} catch (e) {{ return false; }}
    if (!a || !a.event || !a.event.on) return false;
    ['init.lang', 'init.one', 'init.two', 'init-done', 'settings.saved']
      .forEach(function (ev) {{
        // init.lang fires *before* LANG.set(), so that one is deferred past it
        a.event.on(ev, function () {{ setTimeout(relang, 0); }});
      }});
    return true;
  }}

  var tries = 200, langWatched = false;
  (function wait() {{
    if (!langWatched) langWatched = watchLang();
    relang();
    var popsOk = !Object.keys(LANG_LABELS).length || watchPops();
    if (install() && langWatched && popsOk) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();
}})();
"""


def _q(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
