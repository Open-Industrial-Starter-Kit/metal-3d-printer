"""
ui/cam_tool_library.py
Tool libraries for Kiri:Moto's tool dialog: export every tool as a named
library file, and import one -- added to the tools already there, or in place
of them.

What was there
--------------
Kiri's tool dialog has *export* and *import* (``mode/cam/app/tools.js``). Export
already wrote every tool -- ``{version, tools, time}``, base64 JSON, as
``tools.km`` -- but nothing said so, and the app's download handler filed it as
a "CAM profile" (``ui/pages/cam.py``). Import went through Kiri's generic file
import, which only *adds* tools whose id it does not already have: a library
loaded again after editing a tool changed nothing, and there was no way to swap
the whole set for another. And the import button read "Open Project", because
``ui/cam_labels.py`` renames Kiri's ``import`` word for the File menu and this
button carries the same key.

What this does
--------------
* The two buttons become **export library** and **import library**, with their
  own labels (the shared ``lk`` key is taken off them).
* **Export library** asks for a name and saves every tool as
  ``<name>.tools.km`` -- the same format Kiri writes, so the file still opens in
  any Kiri:Moto, with the name kept inside (``library``). The app saves it
  through a *Save tool library* dialog (``ui/pages/cam.py``).
* **Import library** reads a ``.km`` file (a library, or Kiri's own tools
  export) and asks:

  - *Add and update*: tools the library has and you do not are added; a tool
    with the same id is replaced by the library's version; yours that the
    library lacks stay.
  - *Replace my tools*: your tools become exactly the library's. Operations and
    defaults that used a tool that is gone are moved to the remaining tool of
    nearest cutting diameter (the same rule ``ui/cam_tools.py`` uses), because
    Kiri silently falls back to the first tool in the list for a missing id.

  Tool numbers (the T in ``M6 T``) are kept, except where an imported tool's
  number is already used by another one: it gets the next free number.

Both buttons are taken over with a capturing click listener on the document,
because Kiri assigns their ``onclick`` again every time the dialog opens.
"""

from __future__ import annotations

import json

#: File name ending of a library; still ends in .km, so Kiri itself opens it.
LIBRARY_EXT = ".tools.km"


def tool_library_js() -> str:
    """JS taking over the tool dialog's export / import buttons."""
    js = r"""
(function () {
  var EXT = %(ext)s, DLG = 'piccola-toollib-dlg';
  function api() { try { return window.kiri && window.kiri.api; } catch (e) { return null; } }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function mm(t) { return (t && t.metric === false ? 25.4 : 1) * (+(t && t.flute_diam) || 0); }
  function safeName(s) { return String(s).replace(/[\\/:*?"<>|]+/g, '-').trim() || 'tools'; }

  // ---- the buttons ----
  function relabel() {
    var imp = document.getElementById('tools-import');
    var exp = document.getElementById('tools-export');
    if (!imp || !exp) return;
    [[imp, 'import library',
      'Load a tool library file (.km): add its tools to yours, or replace yours with it.'],
     [exp, 'export library',
      'Save all your tools as one library file (.km), to keep, share or load on another computer.']
    ].forEach(function (b) {
      b[0].removeAttribute('lk');           // or the File menu's "open project" lands here
      if (b[0].textContent !== b[1]) b[0].textContent = b[1];
      b[0].title = b[2];
    });
  }
  document.addEventListener('click', function (ev) {
    var t = ev.target && ev.target.closest && ev.target.closest('#tools-import, #tools-export');
    if (!t) return;
    ev.preventDefault();
    ev.stopPropagation();                   // Kiri's own onclick never sees it
    if (t.id === 'tools-export') exportLibrary(); else pickFile();
  }, true);
  setInterval(relabel, 1000);

  // ---- export ----
  function exportLibrary() {
    var a = api();
    if (!a) return;
    a.uc.prompt('Library name', 'my tools').then(function (name) {
      if (!name) return;
      var record = { version: a.version, library: String(name), time: Date.now(),
                     tools: (a.conf.get().tools || []) };
      a.util.download(a.util.b64enc(record), safeName(name) + EXT);
    });
  }

  // ---- import ----
  function pickFile() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = '.km,.b64,.json';
    input.onchange = function () {
      var f = input.files && input.files[0];
      if (!f) return;
      var r = new FileReader();
      r.onload = function () { read(String(r.result || ''), f.name); };
      r.readAsText(f);
    };
    input.click();
  }

  function decode(text) {
    text = text.trim();
    try { return JSON.parse(text); } catch (e) { /* not plain JSON */ }
    return api().util.b64dec(text);
  }

  function read(text, fname) {
    var a = api(), data = null;
    try { data = decode(text); } catch (e) { data = null; }
    var lib = data && Array.isArray(data.tools) ? data.tools.filter(function (t) {
      return t && typeof t === 'object' && t.id !== undefined;
    }) : [];
    if (!lib.length) {
      a.uc.alert('No tools found in ' + esc(fname) + '.');
      return;
    }
    ask(lib, data.library || fname.replace(/\.tools\.km$|\.km$/i, ''));
  }

  function ask(lib, name) {
    var a = api(), mine = a.conf.get().tools || [];
    var have = {};
    mine.forEach(function (t) { have[t.id] = 1; });
    var update = lib.filter(function (t) { return have[t.id]; }).length;
    var add = lib.length - update;
    var back = document.createElement('div');
    back.id = DLG;
    back.setAttribute('style', 'position:fixed;inset:0;z-index:100001;background:rgba(0,0,0,.35);' +
      'display:flex;align-items:center;justify-content:center;');
    // Kiri's index.css makes every div `display:flex` (a row) and every button
    // a capitalised flex box, so each block below states its own layout --
    // otherwise the paragraphs sit side by side and the box outgrows the tool
    // dialog it opens over.
    back.innerHTML =
      '<style>' +
      '#' + DLG + ' .box{display:block;box-sizing:border-box;background:#fff;color:#222;' +
        'border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.35);padding:16px 18px;' +
        'width:440px;max-width:calc(100vw - 48px);max-height:calc(100vh - 48px);overflow:auto;' +
        'font:13px/1.45 system-ui,Segoe UI,sans-serif;white-space:normal}' +
      '#' + DLG + ' .box div{display:block}' +
      '#' + DLG + ' .box .ttl{font-size:16px;font-weight:600;margin:0 0 8px}' +
      '#' + DLG + ' .box .p{margin:8px 0 0}' +
      '#' + DLG + ' .box .row{display:flex;flex-direction:row;flex-wrap:wrap;gap:8px;' +
        'justify-content:flex-end;margin-top:14px}' +
      '#' + DLG + ' .box button{display:inline-block;flex:none;margin:0;padding:5px 12px;' +
        'font:inherit;text-transform:none;white-space:nowrap;cursor:pointer;color:#222;' +
        'background:#eee;border:1px solid #bbb;border-radius:4px}' +
      '#' + DLG + ' .box button.go{background:#3A78B5;color:#fff;border-color:#3A78B5}' +
      '</style>' +
      '<div class="box">' +
      '<div class="ttl">Import tool library</div>' +
      '<div><b>' + esc(name) + '</b>: ' + lib.length + ' tool' + (lib.length === 1 ? '' : 's') +
      '. Your library has ' + mine.length + '.</div>' +
      '<div class="p"><b>Add and update</b>: adds ' + add + ' new tool' + (add === 1 ? '' : 's') +
      ' and replaces ' + update + ' with the same ID by the library\'s version. Your other tools stay.</div>' +
      '<div class="p"><b>Replace my tools</b>: your tools become exactly these ' + lib.length +
      '. Operations using a tool that goes are moved to the nearest diameter left.</div>' +
      '<div class="row">' +
      '<button data-k="cancel">Cancel</button>' +
      '<button data-k="replace">Replace my tools</button>' +
      '<button data-k="add" class="go">Add and update</button></div></div>';
    document.body.appendChild(back);
    back.addEventListener('click', function (ev) {
      var k = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-k');
      if (!k && ev.target !== back) return;
      back.remove();
      if (k === 'add' || k === 'replace') apply(lib, k === 'replace', name);
    });
  }

  function apply(lib, replace, name) {
    var a = api(), conf = a.conf.get();
    var mine = conf.tools || [];
    var result;
    if (replace) {
      result = lib.map(clone);
    } else {
      result = mine.slice();
      lib.forEach(function (t) {
        var i = -1;
        for (var j = 0; j < result.length; j++) if (result[j].id === t.id) { i = j; break; }
        if (i >= 0) result[i] = clone(t); else result.push(clone(t));
      });
    }
    // T numbers: keep each, unless another tool already has it
    var fromLib = {};
    lib.forEach(function (t) { fromLib[t.id] = 1; });
    var used = {}, max = 0;
    result.forEach(function (t) {
      if (Number.isInteger(t.number)) max = Math.max(max, t.number);
      if (!fromLib[t.id] && Number.isInteger(t.number)) used[t.number] = 1;   // yours keep theirs
    });
    result.forEach(function (t) {
      if (!fromLib[t.id]) return;
      if (!Number.isInteger(t.number) || used[t.number]) t.number = ++max;
      used[t.number] = 1;
    });
    // anything still pointing at a tool that is gone: the nearest diameter
    var keep = {};
    result.forEach(function (t) { keep[t.id] = t; });
    var remap = {};
    mine.forEach(function (t) {
      if (keep[t.id] || !result.length) return;
      var want = mm(t), best = result[0], bestd = Infinity;
      result.forEach(function (r) {
        var d = Math.abs(mm(r) - want) + (r.type === t.type ? 0 : 0.001);
        if (d < bestd) { bestd = d; best = r; }
      });
      remap[t.id] = best.id;
    });
    var procs = [conf.process];
    var saved = (conf.sproc || {}).CAM || {};
    for (var n in saved) procs.push(saved[n]);
    var moved = 0;
    procs.forEach(function (p) {
      if (!p) return;
      for (var f in p) {
        if (/^cam.*Tool$/.test(f) && remap[p[f]] !== undefined) p[f] = remap[p[f]];
      }
      (Array.isArray(p.ops) ? p.ops : []).forEach(function (op) {
        if (op && remap[op.tool] !== undefined) { op.tool = remap[op.tool]; moved++; }
      });
    });
    conf.tools = result;
    try { a.conf.save(); } catch (e) { /* settings still updated in memory */ }
    try { a.show.tools(); } catch (e) { }
    var msg = 'Imported ' + esc(name) + ': ' + result.length + ' tools now.';
    if (moved) msg += ' ' + moved + ' operation' + (moved === 1 ? '' : 's') +
      ' moved to the nearest tool left -- check them before generating.';
    try { a.uc.alert(msg); } catch (e) { }
  }

  window.__piccolaToolLibrary = { exportLibrary: exportLibrary, importText: read };
})();
"""
    return js % {"ext": json.dumps(LIBRARY_EXT)}
