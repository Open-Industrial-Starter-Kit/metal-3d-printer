"""
ui/cam_export.py
Replaces each CAM tool's export/download button with an explicit destination
choice: save to disk, or send straight to the machine.

Why replace rather than add
---------------------------
Before this, every export went through one button and the app asked afterwards
where the files should go. That is the wrong order: by the time the prompt
appears the user has already committed, and on a machine that is about to cut
metal "where does this go" is worth stating up front rather than confirming
after the fact.

How the intent reaches Python
-----------------------------
Qt cancels a ``QWebEngineDownloadRequest`` unless ``accept()`` is called before
the signal handler returns, so the destination cannot be queried from the page
once a download is already in flight. Each button therefore *first* records the
intent with a same-origin request to our own server
(``/__piccola/export-intent``, see ``cam_server.CONTROL_PREFIX``) and only then
clicks the tool's real export button. Awaiting the fetch keeps the ordering
guaranteed.

Going through the server we already run avoids standing up a QWebChannel and
injecting ``qwebchannel.js``; being same-origin it needs no extra permissions.

Neither tool is modified: its own button is hidden and driven programmatically,
so all of its export logic still runs — for EasyTrace the operation ordering,
single-file vs per-tool split and comments; for Kiri:Moto the post processor and
the ``#print-filename`` field.

Naming is the one thing not left to the tool. What the user types is sent along
with the intent (``name_id`` below) and ``CamPage`` names the staged file after
it, because EasyTrace treats that field as a *stem* and decorates it per
operation ("<name>-isolation-copper_top.nc"). The staged name is what reaches
the machine — ``FileManagerPage.upload_file`` only forces a ``.nc`` extension.
Kiri:Moto sends no name and keeps its own ``#print-filename`` behaviour.
"""

from __future__ import annotations

#: Per-mode wiring. ``exec_id`` is the tool's own export control — hidden, and
#: clicked programmatically. Button classes borrow each tool's own styling so
#: the replacements read as native, and the labels follow its capitalisation:
#: EasyTrace uses Title Case ("Export Files", "Cancel"), Kiri lowercase
#: ("gcode", "palette", "zip").
TOOLS = {
    "pcb": {
        "exec_id": "exporter-execute-btn",
        # The field the user names the job in ("File Name" after
        # ui/cam_labels.py relabels it). Sent with the intent so CamPage can name
        # what it stages after it — EasyTrace derives per-operation filenames
        # from it ("<name>-isolation-copper_top.nc"), and what should reach the
        # machine is the name that was typed. See CamPage._batch_filename.
        "name_id": "exporter-filename",
        "buttons": (
            ("piccola-export-file", "Save as File", "file", "btn btn--secondary"),
            ("piccola-export-machine", "Save to Machine", "machine", "btn btn--primary"),
        ),
    },
    "carve": {
        # Kiri's export dialog (#mod-x-any) download row: gcode | palette | zip
        # | 3mf. Only the gcode button is replaced; the mesh exports are a
        # different job and are left alone.
        "exec_id": "print-download",
        "buttons": (
            ("piccola-export-file", "save as file", "file", "grow"),
            ("piccola-export-machine", "save to machine", "machine", "grow"),
        ),
    },
}


def hide_css(mode: str) -> str:
    """CSS hiding the tool's own export control for *mode*."""
    spec = TOOLS.get(mode)
    if not spec:
        return ""
    return f"#{spec['exec_id']} {{ display: none !important; }}"


def export_buttons_js(mode: str, control_path: str) -> str:
    """JS swapping *mode*'s export button for the two destination buttons.

    *control_path* is the same-origin endpoint that records the intent.
    """
    spec = TOOLS.get(mode)
    if not spec:
        return ""
    specs = ",".join(
        f"{{id:{_q(i)},label:{_q(l)},intent:{_q(m)},cls:{_q(c)}}}"
        for i, l, m, c in spec["buttons"]
    )
    return f"""
(function () {{
  var CONTROL = {_q(control_path)};
  var EXEC_ID = {_q(spec['exec_id'])};
  var NAME_ID = {_q(spec.get('name_id') or '')};
  var SPECS = [{specs}];
  var installed = false;

  // Read at click time, not at install time: the field is edited between the
  // dialog opening and the export starting.
  function jobName() {{
    if (!NAME_ID) return '';
    var el = document.getElementById(NAME_ID);
    return (el && el.value) ? String(el.value) : '';
  }}

  function install() {{
    if (installed) return true;
    var exec = document.getElementById(EXEC_ID);
    if (!exec || !exec.parentElement) return false;
    var host = exec.parentElement;

    SPECS.forEach(function (spec) {{
      if (document.getElementById(spec.id)) return;
      var b = document.createElement('button');
      b.id = spec.id;
      b.className = spec.cls;
      b.textContent = spec.label;
      b.setAttribute('aria-label', spec.label);
      b.addEventListener('click', function (ev) {{
        ev.preventDefault();
        // Record the destination and the job name BEFORE the export starts —
        // see module docs. The export runs whether or not this succeeds; a
        // failed fetch just means the safe default (save to disk, and the
        // tool's own filenames) applies.
        var q = '?mode=' + encodeURIComponent(spec.intent);
        var name = jobName();
        if (name) q += '&name=' + encodeURIComponent(name);
        fetch(CONTROL + q, {{ cache: 'no-store' }})
          .catch(function () {{}})
          .then(function () {{ exec.click(); }});
      }});
      host.appendChild(b);
    }});

    installed = true;
    return true;
  }}

  // The dialogs are static markup, but scripts may re-render a footer, so keep
  // trying briefly and then re-check once the document is ready.
  var tries = 200;
  (function wait() {{
    if (install()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();

  document.addEventListener('DOMContentLoaded', function () {{
    installed = false;
    install();
  }});
}})();
"""


def _q(s: str) -> str:
    """Minimal JS string literal — these are all internal constants."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
