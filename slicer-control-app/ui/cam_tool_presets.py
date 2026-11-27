"""
ui/cam_tool_presets.py
Named cutting presets per tool -- "fast rotary al", "finish al", "wood rough" --
picked in any operation that uses the tool, the way Fusion 360's tool library
keeps feeds and speeds with the cutter.

Why
---
A cutter's right numbers depend on the job: the same 1 flute 3 mm runs at 650
deg/min and 0.2 mm a layer in aluminium and three times that in POM. Kiri keeps
those numbers only on each operation, so every new job meant typing them again,
or copying them from notes. A preset is typed once, named by the operator, and
travels with the tool -- including through Export / Import Library
(``ui/cam_tool_library.py``), because it is stored on the tool record itself
(``tool.presets``).

In the operation panel
----------------------
Under *Tool* every operation gets a **Preset** row:

* the drop-down lists the presets of the tool the operation uses (it follows
  the Tool field when that changes);
* choosing one fills the operation's cutting fields with it;
* the save button stores the operation's current cutting fields as a preset of
  its tool, under a name you give (the same name again replaces it);
* the bin deletes the chosen preset.

What a preset holds
-------------------
The cutting fields, found by their labels so the same preset serves every
operation that has them (a rotary rough and a rotary finish both have *Step
Down*): spindle RPM, feed rate, plunge rate, step over, step down, step angle,
ramp, ramp angle and leave stock. An operation takes only the ones it has.

How a value gets in
-------------------
Through the panel's own inputs, never the record behind them: Kiri commits a
panel on ``blur`` (``app/inputs.js``) through the operation's own ``bind``,
which reads every input back into the record and the process defaults and
saves -- the same path as typing. Kiri's panel state (``env.poppedRec``) is not
reachable from here, and writing the record directly would bypass the
process defaults the panel also keeps.
"""

from __future__ import annotations


def tool_presets_js() -> str:
    """JS adding the Preset row to every operation panel."""
    return r"""
(function () {
  var ROW = 'piccola-preset-row';
  var NONE = '';
  // label text (lower case, range hint removed) -> preset key
  var MAP = [
    [/^spindle/, 'spindle'], [/plunge/, 'plunge'], [/feed/, 'rate'],
    [/^step ?over/, 'step'], [/^step ?down/, 'down'], [/^step angle/, 'angle'],
    [/^ramp angle/, 'ramp_angle'], [/^ramp$/, 'ramp'],
    [/^leave stock z/, 'leavez'], [/^leave stock/, 'leave']
  ];
  var NAMES = { spindle: 'Spindle RPM', rate: 'Feed Rate', plunge: 'Plunge Rate', step: 'Step Over',
    down: 'Step Down', angle: 'Step Angle', ramp: 'Ramp', ramp_angle: 'Ramp Angle',
    leave: 'Leave Stock', leavez: 'Leave Stock Z' };
  var SHOW = ['spindle', 'rate', 'plunge', 'step', 'down', 'angle', 'ramp', 'ramp_angle', 'leave', 'leavez'];

  function api() { try { return window.kiri && window.kiri.api; } catch (e) { return null; } }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

  function labelText(row) {
    var l = row.querySelector('label');
    if (!l) return '';
    var t = (l.childNodes[0] && l.childNodes[0].nodeType === 3) ? l.childNodes[0].textContent : l.textContent;
    return String(t).replace(/\(.*$/, '').trim().toLowerCase();
  }
  function keyOf(text) {
    for (var i = 0; i < MAP.length; i++) if (MAP[i][0].test(text)) return MAP[i][1];
    return null;
  }
  // the panel's cutting inputs that are showing: {key: input}
  function fields(pop) {
    var out = {};
    Array.prototype.forEach.call(pop.querySelectorAll('.var-row'), function (row) {
      if (row.classList.contains(ROW) || row.classList.contains(ROW + '-acts') ||
          row.style.display === 'none') return;
      var ip = row.querySelector('input');
      if (!ip) return;
      var k = keyOf(labelText(row));
      if (k && !out[k]) out[k] = ip;
    });
    return out;
  }
  function toolSelect(pop) {
    var row = pop.querySelector('.var-row[source="tools"]');
    return row ? row.querySelector('select') : null;
  }
  function toolOf(pop) {
    var a = api(), sel = toolSelect(pop);
    if (!a || !sel) return null;
    var id = parseInt(sel.value);
    var tools = (a.conf.get().tools || []);
    for (var i = 0; i < tools.length; i++) if (tools[i].id === id) return tools[i];
    return null;
  }

  function read(pop) {
    var f = fields(pop), v = {};
    for (var k in f) {
      var ip = f[k];
      if (ip.type === 'checkbox') v[k] = !!ip.checked;
      else {
        var n = parseFloat(ip.value);
        if (isFinite(n)) v[k] = n;
      }
    }
    return v;
  }

  function apply(pop, values) {
    var f = fields(pop), commit = null, n = 0;
    // a ramp switched on first, so its angle row shows before it is filled
    if (f.ramp && values.ramp !== undefined) f.ramp.checked = !!values.ramp;
    for (var k in values) {
      var ip = f[k];
      if (!ip || k === 'ramp') continue;
      if (ip.type === 'checkbox') { ip.checked = !!values[k]; }
      else {
        if (ip.setv) ip.setv(values[k]); else ip.value = String(values[k]);
        commit = commit || ip;
      }
      n++;
    }
    if (f.ramp && values.ramp !== undefined) n++;
    // Kiri commits the whole panel when one of its inputs loses focus
    var target = commit || f.ramp;
    if (target) {
      if (target.type === 'checkbox' && target.onclick) target.onclick({ target: target });
      else target.dispatchEvent(new Event('blur'));
    }
    return n;
  }

  // Operation types, as the panels are named (cl-ops.js createPopOp ids
  // "<type>-op"), for the presets' operation tag.
  var OPNAMES = { rotaryrough: 'Rotary Rough', surfacerough: 'Surface Rough',
    rotarycontour: 'Rotary Contour', lathe: 'Rotary Finish', rotarycutoff: 'Cut Off',
    level: 'Level', rough: 'Rough', pocket: 'Pocket', area: 'Area', outline: 'Outline',
    contour: 'Contour', scallop: 'Scallop', trace: 'Trace', helical: 'Helical', drill: 'Drill' };
  function opType(pop) { return String(pop.id || '').replace(/-op$/, ''); }
  function opName(t) { return OPNAMES[t] || t; }

  // the tool's presets for this panel: those made for its operation type (or
  // for any) first, the rest under "Other operations" with their type shown
  function presetOptions(list, type) {
    var mine = list.filter(function (p) { return !p.op || p.op === type; });
    var rest = list.filter(function (p) { return p.op && p.op !== type; });
    var opt = function (p, tag) {
      return '<option value="' + esc(p.name) + '">' + esc(p.name) +
        (tag ? ' (' + esc(opName(p.op)) + ')' : '') + '</option>';
    };
    if (!rest.length) return mine.map(function (p) { return opt(p, false); }).join('');
    return (mine.length ? '<optgroup label="For ' + esc(opName(type)) + '">' +
        mine.map(function (p) { return opt(p, false); }).join('') + '</optgroup>' : '') +
      '<optgroup label="Other operations">' +
        rest.map(function (p) { return opt(p, true); }).join('') + '</optgroup>';
  }

  function refresh(pop, keep) {
    var row = pop.querySelector('.' + ROW);
    if (!row) return;
    var sel = row.querySelector('select'), tool = toolOf(pop);
    var list = (tool && Array.isArray(tool.presets)) ? tool.presets : [];
    var want = keep !== undefined ? keep : sel.value;
    sel.innerHTML = '<option value="">' + (list.length ? 'Choose a preset' : 'No presets for this tool') +
      '</option>' + presetOptions(list, opType(pop));
    sel.value = list.some(function (p) { return p.name === want; }) ? want : NONE;
    row.dataset.tool = tool ? String(tool.id) : '';
    var del = pop.querySelector('.pp-del'), upd = pop.querySelector('.pp-upd');
    if (del) del.disabled = !sel.value;
    if (upd) upd.disabled = !sel.value;
  }

  // one setting per line, "Spindle RPM : 12000", in the panel's order and
  // left-aligned inside Kiri's centred message box (returns HTML)
  function describe(v) {
    var keys = SHOW.filter(function (k) { return v[k] !== undefined; })
      .concat(Object.keys(v).filter(function (k) { return SHOW.indexOf(k) < 0; }));
    return '<div style="display:inline-block;text-align:left;margin-top:6px;line-height:1.5">' +
      keys.map(function (k) {
        return esc(NAMES[k] || k) + ' : ' + esc(typeof v[k] === 'boolean' ? (v[k] ? 'on' : 'off') : v[k]);
      }).join('<br>') + '</div>';
  }

  function install(pop) {
    if (pop.querySelector('.' + ROW)) return;
    var tsel = toolSelect(pop);
    if (!tsel) return;
    // The tips sit on the label, the list and each button -- never on a row
    // that holds other titled controls: the tooltip panel takes the title of
    // the control under the pointer, and the browser then showed the row's as
    // its own plain one-line tooltip beside it.
    var TIP = 'Named cutting settings kept with this tool.\n' +
      'Choose one to fill this operation\'s feeds and speeds.';
    var row = document.createElement('div');
    row.className = 'var-row ' + ROW;
    row.innerHTML = '<label>preset</label><select class="pp-sel" style="max-width:150px"></select>';
    row.querySelector('label').title = TIP;
    row.querySelector('select').title = TIP;
    var acts = document.createElement('div');
    acts.className = 'var-row ' + ROW + '-acts';
    acts.setAttribute('style', 'display:flex !important;gap:6px;justify-content:flex-end;padding:2px 0 4px');
    var BTN = 'display:inline-block !important;width:auto !important;flex:0 0 auto;' +
      'padding:2px 10px;cursor:pointer;font-size:12px';
    acts.innerHTML =
      '<button class="pp-save" style="' + BTN + '">save as…</button>' +
      '<button class="pp-upd" style="' + BTN + '">update</button>' +
      '<button class="pp-del" style="' + BTN + '">delete</button>';
    acts.querySelector('.pp-save').title = 'Save this operation\'s cutting settings as a new preset of ' +
      'this tool, under a name you choose.\nThe same name again replaces it.';
    acts.querySelector('.pp-upd').title = 'Write this operation\'s current cutting settings into the ' +
      'preset chosen above.\nIts name and operation type stay as they are.';
    acts.querySelector('.pp-del').title = 'Delete the preset chosen above.';
    var trow = tsel.closest('.var-row');
    trow.parentNode.insertBefore(row, trow.nextSibling);
    row.parentNode.insertBefore(acts, row.nextSibling);
    var sel = row.querySelector('select');

    // Keep the panel open while a preset is being chosen or saved. Kiri closes
    // an operation panel 250 ms after the pointer leaves it unless something
    // has made it "sticky" (init-ui.js onLeave, UC.isSticky) -- its own
    // fields do on focus -- and the drop-down's list and the name / confirm
    // boxes all sit outside the panel, so moving onto them closed it before
    // the click landed. Held from the press until the pointer is back inside.
    var held = false;
    function hold() {
      var a = api();
      try { a.uc.setSticky(true); } catch (e) { }
      held = true;
    }
    pop.addEventListener('mouseenter', function () {
      if (!held) return;
      held = false;
      var a = api();
      // a moment later, so the click that brought it back is not undone
      setTimeout(function () { if (!held) { try { a.uc.setSticky(false); } catch (e) { } } }, 300);
    });
    sel.addEventListener('mousedown', hold);
    sel.addEventListener('focus', hold);
    acts.addEventListener('mousedown', hold, true);

    sel.addEventListener('change', function () {
      var tool = toolOf(pop);
      acts.querySelector('.pp-del').disabled = !sel.value;
      acts.querySelector('.pp-upd').disabled = !sel.value;
      if (!tool || !sel.value) return;
      var p = (tool.presets || []).filter(function (x) { return x.name === sel.value; })[0];
      if (p) apply(pop, p.values || {});
    });
    acts.querySelector('.pp-save').addEventListener('click', function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var a = api(), tool = toolOf(pop);
      if (!a || !tool) return;
      var values = read(pop);
      if (!Object.keys(values).length) { a.uc.alert('This operation has no cutting settings to save.'); return; }
      a.uc.prompt('Preset name for ' + esc(tool.name), sel.value || '').then(function (name) {
        name = String(name || '').trim();
        if (!name) return;
        tool.presets = (tool.presets || []).filter(function (p) { return p.name !== name; });
        tool.presets.push({ name: name, values: values, op: opType(pop) });
        tool.presets.sort(function (x, y) { return x.name.localeCompare(y.name); });
        try { a.conf.save(); } catch (e) { }
        refresh(pop, name);
        a.uc.alert('Saved preset "' + esc(name) + '" for ' + esc(tool.name) + ', for ' +
          esc(opName(opType(pop))) + ':<br>' + describe(values));
      });
    });
    acts.querySelector('.pp-upd').addEventListener('click', function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var a = api(), tool = toolOf(pop), name = sel.value;
      if (!a || !tool || !name) return;
      var p = (tool.presets || []).filter(function (x) { return x.name === name; })[0];
      if (!p) return;
      var values = read(pop);
      if (!Object.keys(values).length) { a.uc.alert('This operation has no cutting settings to save.'); return; }
      // the settings this operation has replace the preset's; ones it does
      // not have (a rough's step over, kept for when a rough applies it) stay
      p.values = Object.assign({}, p.values || {}, values);
      try { a.conf.save(); } catch (e) { }
      refresh(pop, name);
      a.uc.alert('Updated preset "' + esc(name) + '" of ' + esc(tool.name) + ':<br>' + describe(p.values));
    });
    acts.querySelector('.pp-del').addEventListener('click', function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var a = api(), tool = toolOf(pop), name = sel.value;
      if (!a || !tool || !name) return;
      a.uc.confirm('Delete preset "' + esc(name) + '" of ' + esc(tool.name) + '?').then(function (yes) {
        if (!yes) return;
        tool.presets = (tool.presets || []).filter(function (p) { return p.name !== name; });
        try { a.conf.save(); } catch (e) { }
        refresh(pop, NONE);
      });
    });
    refresh(pop, NONE);
  }

  // every operation panel, and the list following its Tool field
  setInterval(function () {
    Array.prototype.forEach.call(document.querySelectorAll('.cam-pop-op'), function (pop) {
      install(pop);
      var row = pop.querySelector('.' + ROW), tool = toolOf(pop);
      if (row && String(tool ? tool.id : '') !== row.dataset.tool) refresh(pop, NONE);
    });
  }, 500);

  // ---- the tool dialog: a Presets section for the tool being edited ----
  //
  // The same tool.presets as above, edited by hand: a list of the tool's
  // presets, New / Rename / Delete, and one field per setting. An empty field
  // is not part of the preset, so applying it leaves that setting alone.
  var ORDER = ['spindle', 'rate', 'plunge', 'step', 'down', 'angle', 'ramp', 'ramp_angle', 'leave'];
  var LABELS = { spindle: 'Spindle RPM', rate: 'Feed Rate', plunge: 'Plunge Rate', step: 'Step Over',
    down: 'Step Down', angle: 'Step Angle', ramp: 'Ramp', ramp_angle: 'Ramp Angle', leave: 'Leave Stock' };
  var SEC = 'piccola-tool-presets';

  function byNumber(a, b) {          // tools.js byNumber: the list's own order
    var na = parseInt(a.number), nb = parseInt(b.number);
    var ka = isNaN(na) ? Infinity : na, kb = isNaN(nb) ? Infinity : nb;
    if (ka !== kb) return ka < kb ? -1 : 1;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  }
  // the tool highlighted in the dialog's list
  function dialogTool() {
    var a = api(), list = document.getElementById('tool-select');
    if (!a || !list || list.selectedIndex < 0) return null;
    var tools = (a.conf.get().tools || []).slice().sort(byNumber);
    if (tools.length === list.options.length) return tools[list.selectedIndex] || null;
    // the list holds a tool not saved yet: match by name and number instead
    var name = (document.getElementById('tool-name') || {}).value;
    var num = parseInt((document.getElementById('tool-num') || {}).value);
    return tools.filter(function (t) { return t.name === name && t.number === num; })[0] || null;
  }

  function save() { var a = api(); try { a.conf.save(); } catch (e) { } }

  // The Preview column is split: the drawing on top, the presets below it.
  // #tool-view itself cannot hold them -- renderTool (tools.js) replaces its
  // whole content and sizes the drawing to fill it -- so the column becomes
  // a wrapper holding #tool-view (shortened) and this section, and the
  // dialog's rows are allowed a little more height.
  var WRAP = 'piccola-tool-right';
  function layout() {
    var view = document.getElementById('tool-view'), cols = document.getElementById('tool-cols');
    if (!view || !cols) return null;
    var wrap = document.getElementById(WRAP);
    if (wrap && wrap.contains(view)) return wrap;
    wrap = document.createElement('div');
    wrap.id = WRAP;
    wrap.className = view.className.replace(/\bt-body\b/, '').trim();
    wrap.setAttribute('style', 'display:flex !important;flex-direction:column !important;gap:6px;min-height:0');
    view.parentNode.insertBefore(wrap, view);
    wrap.appendChild(view);
    view.style.flex = '1 1 0';
    view.style.minHeight = '150px';
    // its 't-33' (a third of the row) now applies inside the column: full width there
    view.style.width = '100%';
    view.style.maxWidth = 'none';
    cols.style.maxHeight = '620px';
    // the drawing was sized for the whole column: draw it again in its part
    var list = document.getElementById('tool-select');
    var opt = list && list.options[list.selectedIndex];
    if (opt && opt.onclick) setTimeout(function () { opt.onclick(); }, 60);
    return wrap;
  }

  function section() {
    var host = layout();
    if (!host) return null;
    var sec = host.querySelector('.' + SEC);
    if (sec) return sec;
    sec = document.createElement('div');
    sec.className = SEC + ' t-body';
    sec.setAttribute('style', 'display:flex !important;flex-direction:column !important;flex:0 0 auto;' +
      'max-height:330px;overflow:auto;padding:0 0 4px');
    var BTN = 'display:inline-block !important;width:auto !important;padding:2px 8px;cursor:pointer;font-size:12px';
    var ROWS = 'display:flex !important;flex-direction:row !important;justify-content:space-between;' +
      'align-items:center;padding:2px 6px';
    sec.innerHTML =
      '<div style="display:block !important;background:#dcdcdc;font-size:15px;padding:4px 6px">Presets</div>' +
      '<div style="' + ROWS + '"><select class="tp-list" style="flex:1 1 auto;max-width:100%"></select></div>' +
      '<div style="' + ROWS + ';justify-content:flex-end;gap:4px">' +
      '<button class="tp-new" style="' + BTN + '">new</button>' +
      '<button class="tp-ren" style="' + BTN + '">rename</button>' +
      '<button class="tp-del" style="' + BTN + '">delete</button></div>' +
      '<div class="tp-fields" style="display:flex !important;flex-direction:column !important"></div>' +
      '<div class="tp-hint" style="display:block !important;font-size:11px;color:#666;padding:4px 6px">' +
      'Empty = left as it is in the operation. Feed on rotary jobs is deg/min. Operations can also save ' +
      'their settings here with "save as" and "update".</div>';
    host.appendChild(sec);
    sec.querySelector('.tp-list').title = 'Presets of this tool: named cutting settings an operation can apply.';
    sec.querySelector('.tp-new').title = 'Add a preset to this tool, under a name you choose.';
    sec.querySelector('.tp-ren').title = 'Give the chosen preset a new name.';
    sec.querySelector('.tp-del').title = 'Delete the chosen preset.';
    sec.querySelector('.tp-list').addEventListener('change', function () { draw(sec, this.value); });
    sec.querySelector('.tp-new').addEventListener('click', function (ev) {
      ev.preventDefault();
      var a = api(), tool = sec._tool;
      if (!a || !tool) return;
      a.uc.prompt('New preset for ' + esc(tool.name), '').then(function (name) {
        name = String(name || '').trim();
        if (!name) return;
        tool.presets = (tool.presets || []).filter(function (p) { return p.name !== name; });
        tool.presets.push({ name: name, values: {} });
        tool.presets.sort(function (x, y) { return x.name.localeCompare(y.name); });
        save(); draw(sec, name);
      });
    });
    sec.querySelector('.tp-ren').addEventListener('click', function (ev) {
      ev.preventDefault();
      var a = api(), tool = sec._tool, p = current(sec);
      if (!a || !tool || !p) return;
      a.uc.prompt('Rename preset', p.name).then(function (name) {
        name = String(name || '').trim();
        if (!name || name === p.name) return;
        tool.presets = tool.presets.filter(function (q) { return q === p || q.name !== name; });
        p.name = name;
        tool.presets.sort(function (x, y) { return x.name.localeCompare(y.name); });
        save(); draw(sec, name);
      });
    });
    sec.querySelector('.tp-del').addEventListener('click', function (ev) {
      ev.preventDefault();
      var a = api(), tool = sec._tool, p = current(sec);
      if (!a || !tool || !p) return;
      a.uc.confirm('Delete preset "' + esc(p.name) + '" of ' + esc(tool.name) + '?').then(function (yes) {
        if (!yes) return;
        tool.presets = tool.presets.filter(function (q) { return q !== p; });
        save(); draw(sec, NONE);
      });
    });
    return sec;
  }

  function current(sec) {
    var tool = sec._tool, name = sec.querySelector('.tp-list').value;
    return tool && name ? (tool.presets || []).filter(function (p) { return p.name === name; })[0] : null;
  }

  function draw(sec, pick) {
    var tool = sec._tool;
    var list = (tool && tool.presets) || [];
    var sel = sec.querySelector('.tp-list');
    sel.innerHTML = '<option value="">' + (list.length ? 'Choose a preset' : 'No presets yet') +
      '</option>' + list.map(function (p) {
        return '<option value="' + esc(p.name) + '">' + esc(p.name) +
          ' (' + esc(p.op ? opName(p.op) : 'any') + ')</option>';
      }).join('');
    sel.value = list.some(function (p) { return p.name === pick; }) ? pick : NONE;
    var p = current(sec);
    sec.querySelector('.tp-ren').disabled = !p;
    sec.querySelector('.tp-del').disabled = !p;
    var box = sec.querySelector('.tp-fields');
    box.innerHTML = '';
    if (!p) return;
    var v = p.values || (p.values = {});
    // which operation the preset is for: "any" (untagged) or one type
    var orow = document.createElement('div');
    orow.setAttribute('style', 'display:flex !important;flex-direction:row !important;justify-content:space-between;' +
      'align-items:center;padding:2px 6px;border-bottom:1px solid #e4e4e4');
    var olab = document.createElement('label');
    olab.textContent = 'Operation';
    olab.setAttribute('style', 'text-transform:none');
    olab.title = 'Which operation this preset is for. An operation lists its own presets ' +
      'first and the rest under "Other operations". "Any" fits every operation.';
    var osel = document.createElement('select');
    osel.innerHTML = '<option value="">Any</option>' + Object.keys(OPNAMES).map(function (t) {
      return '<option value="' + t + '">' + esc(OPNAMES[t]) + '</option>';
    }).join('');
    if (p.op && !OPNAMES[p.op]) osel.innerHTML += '<option value="' + esc(p.op) + '">' + esc(p.op) + '</option>';
    osel.value = p.op || '';
    osel.addEventListener('change', function () {
      if (osel.value) p.op = osel.value; else delete p.op;
      save();
    });
    orow.appendChild(olab); orow.appendChild(osel);
    box.appendChild(orow);
    ORDER.forEach(function (k) {
      var row = document.createElement('div');
      row.setAttribute('style', 'display:flex !important;flex-direction:row !important;justify-content:space-between;align-items:center;' +
        'padding:2px 6px;border-bottom:1px solid #e4e4e4');
      var lab = document.createElement('label');
      lab.textContent = LABELS[k];
      lab.setAttribute('style', 'text-transform:none');
      row.appendChild(lab);
      var ip;
      if (k === 'ramp') {
        ip = document.createElement('select');
        ip.innerHTML = '<option value="">—</option><option value="on">on</option><option value="off">off</option>';
        ip.value = v.ramp === undefined ? '' : (v.ramp ? 'on' : 'off');
        ip.addEventListener('change', function () {
          if (ip.value === '') delete v.ramp; else v.ramp = ip.value === 'on';
          save();
        });
      } else {
        ip = document.createElement('input');
        ip.type = 'text';
        ip.size = 7;
        ip.value = v[k] === undefined ? '' : String(v[k]);
        ip.setAttribute('style', 'text-align:right;width:70px');
        ip.addEventListener('change', function () {
          var n = parseFloat(ip.value);
          if (ip.value.trim() === '' || !isFinite(n)) { delete v[k]; ip.value = ''; }
          else v[k] = n;
          save();
        });
      }
      row.appendChild(ip);
      box.appendChild(row);
    });
  }

  // follow the list: redraw when another tool is highlighted
  setInterval(function () {
    var host = document.getElementById('tool-view');
    if (!host || !host.offsetParent) return;          // dialog not showing
    var sec = section(), tool = dialogTool();
    if (!sec) return;
    // the tool's presets as a list of names and types: a preset saved from an
    // operation while the dialog is open shows up here too
    var sig = tool ? JSON.stringify((tool.presets || []).map(function (p) { return [p.name, p.op || '']; })) : '';
    if (sec._tool !== tool) {
      sec._tool = tool;
      sec._sig = sig;
      sec.style.display = tool ? '' : 'none';
      if (tool) draw(sec, NONE);
    } else if (tool && sec._sig !== sig) {
      sec._sig = sig;
      var keep = sec.querySelector('.tp-list').value;
      var active = document.activeElement;
      // not while a field of this section is being typed in
      if (!active || !sec.contains(active) || active.tagName === 'SELECT') draw(sec, keep);
    }
  }, 400);

  window.__piccolaToolPresets = { read: read, apply: apply, fields: fields,
                                  dialogTool: dialogTool };
})();
"""
