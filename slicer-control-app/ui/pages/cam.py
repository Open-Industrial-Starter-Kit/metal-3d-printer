"""
ui/pages/cam.py
CAM page — hosts the two vendored browser CAM tools behind a native mode switch.

    Carve  -> Kiri:Moto      (MIT)       general 2D / 2.5D / 3D CAM from SVG/STL/OBJ/3MF
    PCB    -> EasyTrace5000  (AGPL-3.0)  isolation routing / drilling from Gerber+Excellon

Both are unmodified upstream bundles under ``vendor/``, served over loopback HTTP
by ``cam_server.py`` (they cannot run from ``file://``).  Neither tool is patched:
G-code leaves them through their own "export / save" button, and we intercept the
resulting browser download — see :meth:`CamPage._on_download`.  That keeps the
AGPL story simple (we ship EasyTrace byte-identical) and makes upgrades a folder
swap.

Two deliberate design points:

* **Lazy webviews.**  Spawning a Chromium render process costs ~100 ms and a lot
  of RAM, so a mode's view is built on first visit, not at app start.  Each mode
  keeps its own view afterwards, so flipping Carve <-> PCB does not reload the
  tool or lose the user's in-progress job.
* **Batched exports.**  EasyTrace has no M6 tool-change support and therefore
  exports *one file per tool* — several downloads land in quick succession.  We
  debounce them into a single batch and emit one :attr:`gcode_exported` so the
  user gets one prompt listing every file, not four prompts.  A batch shares one
  staging folder, one destination and one name — see :meth:`CamPage._on_download`
  and :meth:`CamPage._batch_filename`.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

import shiboken6

import cam_server

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QButtonGroup, QStackedWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QStandardPaths, QRectF
from PySide6.QtGui import QCursor, QColor, QPainter, QPainterPath

from ui.theme import WHITE, LIGHT, BORDER, TXT, TXT2
from ui.scale import fs, px, bind, autoscale
from ui import gcode_bounds
from ui import (cam_theme, cam_fixes, cam_export, cam_labels, cam_origin,
                cam_rotation, cam_modals, cam_links, cam_tools, cam_arcs,
                cam_rotary, cam_rotary_badge, cam_stock_origin, cam_undo,
                cam_toolchange,
                cam_anim_end, cam_anim_after_export, cam_stock, cam_grid,
                cam_views,
                cam_op_names, cam_auto_z, cam_anim_markers, cam_origin_marker,
                cam_anim_layers, cam_import_prompt,
                cam_render_menu, cam_import_file, cam_anim_buttons,
                cam_box_select, cam_tooltips, cam_tool_icons,
                cam_op_menu, cam_op_art, cam_group_tips, cam_scale_live,
                cam_measure, cam_rough_flats, cam_ranges, cam_stock_box,
                cam_anim_solid, cam_no_ops, cam_preview_solid,
                cam_analysis, cam_gcode_header, cam_minion_pool, cam_materials,
                cam_mode_tips, cam_part_size, cam_ops_guide, cam_tool_library,
                cam_render_solid, cam_tool_presets)
from ui.widgets import lbl
from ui.tooltips import TRANSPARENT

# (key, button label, vendor bundle directory, human name for messages)
# The keys are internal and deliberately stable — they name the export folders
# (cam-exports/<key>-<timestamp>/) and index _PRESET_JS — so relabelling a tab
# is a label change only.
MODES = (
    ("carve", "2D/3D", "kirimoto", "Kiri:Moto"),
    ("pcb",   "PCB",   "easytrace", "EasyTrace5000"),
)

# Upstream attribution shown in the CAM header, taken from each project's own
# metadata rather than assumed: grid-apps' package.json + license.md, and
# EasyTrace's CITATION.cff + NOTICE.
#
# This is not decoration. EasyTrace is AGPL-3.0 and its NOTICE asks that the
# software be credited; showing licence and author in the UI is the cheapest
# honest way to do that. "Eltryus" is credited here as EasyTrace's brand and is
# never used to brand Piccola.
CREDITS = {
    "carve": ("MIT", "Stewart Allen"),
    "pcb":   ("AGPL-3.0", "Ricardo Marques (Eltryus)"),
}

# Extensions we treat as machine-bound G-code. Kiri:Moto emits .nc/.gcode;
# EasyTrace emits .nc per tool. Anything else the tools download (project JSON,
# SVG previews, DXF) is saved but not offered for upload.
_GCODE_EXT = {".nc", ".gcode", ".ngc", ".tap", ".cnc", ".txt"}

# Several per-tool files arrive back-to-back; collect for this long before
# prompting so the user sees one batch.
_BATCH_MS = 700

# Kiri:Moto device profile. "Any.Generic.Grbl" is its stock GRBL post: .nc
# output, a G21/G90 preamble, M30 to end, G4 dwell — the basic dialect the
# Piccola firmware speaks. Selected only when no device has been chosen yet, so
# it acts as a default rather than overwriting a machine the user picked.
_GRBL_DEVICE = "Any.Generic.Grbl"

#: Piccola work area in mm (X, Y). Kiri's stock GRBL profile ships a different
#: bed size, and this value drives its stock/origin maths and out-of-bounds
#: checks — so it has to match the real machine travel.
_WORK_AREA_MM = (150, 150)

#: Spindle ceiling handed to Kiri, in RPM.
#:
#: Kiri treats ``device.spindleMax`` as a *gate*, not just a display: every
#: operation's spindle RPM field is `show: hasSpindle`, which is
#: ``device.spindleMax > 0``, and export.js will not emit a spindle command at
#: all unless it is set —
#:
#:     if (spindleMax && newSpindle && (changeTool || ...)) { ... }
#:
#: The shipped Any.Generic.Grbl profile leaves it at 0, so with the machine
#: dialog hidden there was no spindle control anywhere in the CAM tab and no
#: M3 in the G-code. 12000 matches the figure the PCB side already applies to
#: every operation (_EASYTRACE_SPINDLE_RPM below); the two describe the same
#: spindle and should not disagree.
#:
#: Note this is Kiri's ceiling, not the app's: ui/pages/controls.py deliberately
#: leaves manual spindle control uncapped (_SPINDLE_MAX_RPM = None).
_SPINDLE_MAX_RPM = 12000

#: The spindle command itself. Kiri falls back to "M3 S{speed}" only when the
#: field is absent — the GRBL profile ships it as an empty list, which is
#: truthy, so `device.gcodeSpindle || ["M3 S{speed}"]` keeps the empty list and
#: emits nothing. Setting it explicitly is what puts M3 in the file.
_SPINDLE_GCODE = ["M3 S{speed}"]

#: Z envelope in mm. This is Kiri's ``maxHeight`` (the usable height above the
#: bed), NOT ``bedHeight`` — that one is the bed plate's own thickness, drawn
#: for looks and defaulting to 2.5.
_MAX_Z_MM = 70

#: The origin *anchor*, pinned. Kiri's CAM origin is stock-relative: these two
#: flags choose which corner of the stock it measures from — front-left rather
#: than centre, top face rather than bottom — and `camOriginOffX/Y/Z` shift it
#: from there.
#:
#: "0,0 at the top-left of the work area" has no direct expression here. There
#: is no bed-corner origin and no choice of which corner — the bed-relative
#: `ctOriginBounds`/`ctOriginCenter` pair belongs to the laser/drag/waterjet
#: modes, not CAM. Offsetting Y by the bed depth moves the origin relative to
#: the stock, not the bed, so it does not mean "top-left" and would silently
#: shift every emitted coordinate.
#:
#: **The three offsets are deliberately NOT pinned.** They were, which meant an
#: origin the operator had set — typed into the offset fields, or picked with
#: Kiri's own select button — was reset to zero on the next load and the moment
#: Setup > Machines was opened. They are now the one part of the origin that
#: belongs to the job: ui/cam_stock_origin.py drives them to put the origin on
#: any point of the stock, and a pick has to survive the app restarting.
#: The anchor stays pinned because that module measures from it.
_ORIGIN_DEFAULTS = {"camOriginCenter": False, "camOriginTop": True}

#: Process fields that describe the machine rather than the job, re-applied on
#: every load (and on every device selection) by _PRESET_JS.
#:
#: **Arc output** stays off. Kiri already ships `camArcEnabled: false`, but it is
#: a visible checkbox, and an arc in a height-map job is not compensated at all —
#: the firmware passes G2/G3 through untouched, so the tool holds the previous
#: G1's corrected Z right through the curve (see ui/cam_arcs.py, which does the
#: same job on the EasyTrace side where arcs *are* on by default). The three
#: arc controls are hidden with it — see ui/cam_arcs.py. The rest of the CAM
#: *expert* group (skip shadow, rounded corners) is untouched.
#:
#: **Feed and plunge** are the two rows in Kiri's *limits* group, and despite the
#: plain labels they are ceilings, not speeds: `camFastFeed` is "max xy cutting
#: feedrate" and `camFastFeedZ` "max z cutting plunge rate". prepare.js clamps
#: every emitted feed against them —
#:
#:     maxf = (dz < 0 ? maxZd : maxXYd) || speed
#:     feed = Math.min(speed || maxf, maxf)
#:
#: — so an operation asking for 3000 mm/min still exports F2000. Kiri ships
#: 6000 / 300, which is a generic hobby-router number, not Piccola's. Pinned
#: rather than seeded because a travel ceiling is a fact about the machine, the
#: same as the bed size: to raise it for a job, raise it here. Being pinned, it
#: is re-applied on every load, so a number typed into the field does not
#: survive a restart — that is the trade for the ceiling being a property of
#: the machine rather than of the job.
#: Spindle RPM every *new* operation starts at. Kiri ships 1000, which is a
#: tenth of this machine's spindle.
#:
#: These are the process defaults an operation is seeded from
#: (`createPopOp('rough', { spindle: 'camRoughSpindle', ... })`), not the value
#: on an operation that already exists — those keep whatever they were given.
#: So pinning them sets the starting point without ever overriding a per-job
#: choice, which is the one place a pinned value and an editable one agree.
_OP_SPINDLE_KEYS = (
    "camAreaSpindle", "camContourSpindle", "camDrillSpindle",
    "camHelicalSpindle", "camLatheSpindle", "camLevelSpindle",
    "camOutlineSpindle", "camPocketSpindle", "camRoughSpindle",
    "camTraceSpindle",
)

_KIRI_PROCESS_DEFAULTS = {
    "camArcEnabled": False,
    # Stock > "offset". Not a toggle but a mode switch: it decides whether the
    # width/depth/height fields are margins around the part or the stock's
    # absolute size. Off with Kiri's shipped 5/5/5 leaves a 5 mm cube inside
    # the model, which reads as the stock box vanishing. Pinned on and the
    # switch removed; see ui/cam_stock.py for what that trades away.
    "camStockOffset": True,
    # Output > "tool init", which opens every program with the device's
    # tool-change macro — `M6 T{tool}` on Any.Generic.Grbl. One spindle, no
    # changer, and grbl 1.1 does not implement M6, so the line describes a
    # machine this is not. Pinned off and the checkbox removed with it; see
    # ui/cam_toolchange.py, which also covers what this does NOT reach.
    "camToolInit": False,
    # Both live in the *expert* group, which is hidden (_HIDE_PANELS_CSS), so
    # they are pinned rather than left to whatever a stored profile holds — a
    # setting with no control and no fixed value is one that can differ between
    # two machines with no way to see it. Upstream's own defaults, so this
    # changes nothing today beyond making it stay that way:
    #   camExpertFast   skip overhang detection -- off, the safe reading
    #   camRoundCorners round sharp outside angles in area ops -- on
    "camExpertFast": False,
    "camRoundCorners": True,
    # Roughing > "clear faces": add a clearing pass at every flat Z the model
    # has. Upstream ships this OFF, and off it means *no flat surface is ever
    # cut to size*.
    #
    # The step-down is laid out as an even ladder from the top of the stock to
    # the bottom of it, so where a rung happens to fall relative to the part's
    # own flats is an accident of arithmetic. Measured on an 80x50x15 plate with
    # two pockets and a dividing rib, 6.35 mm endmill, 0.4 stepover, 2 mm
    # step-down, leave 0 -- the rungs land at -1.95, -3.90, -5.84, -7.79, -9.73,
    # -11.68 ... while the part's flats are at -2.50 (wall and rib tops) and
    # -12.50 (pocket floors). Neither is on the ladder, and what is left behind
    # is:
    #
    #     pocket floors     0.823 mm proud, everywhere
    #     wall / rib tops   0.546 mm proud, everywhere
    #
    # With this on, two rungs are added at exactly -12.51 and -2.51 and both
    # figures go to -0.010 mm -- cut to size, with the 0.01 mm of overcut the
    # flats pass deliberately applies. The cost is passes: 465 cutting moves
    # became 578 on that test, about a quarter more.
    #
    # Pinned rather than merely defaulted because the alternative is a job that
    # looks finished in preview and leaves a film on every horizontal face. This
    # sets the value new operations are created with; the per-operation "clear
    # faces" checkbox still decides for an operation that already exists.
    "camRoughFlat": True,
    "camFastFeed": 2000,
    "camFastFeedZ": 200,
    **{k: _SPINDLE_MAX_RPM for k in _OP_SPINDLE_KEYS},
}

# The machine never changes, so Kiri's "Machine Profile" panel (#all-devpro —
# the device and profile pickers) is hidden. The CSS goes in at document
# creation so the panel never paints, rather than being visible for a moment and
# then disappearing.
#
# Consequence worth knowing: with the panel hidden the device can no longer be
# changed from the UI. The preset above only *selects* GRBL when no device has
# been chosen, so if a different one ever ends up stored, it has to be corrected
# here or by clearing the CAM profile storage rather than in the tool.
# The Mode menu goes too: this tab is a CNC tool, and Kiri's other modes (FDM,
# SLA, laser, drag knife, waterjet) would put the user in a slicer with no
# relation to the machine. The preset already forces CAM on every load, so this
# removes the only way back out.
#
# Anchored on `lk="mo_menu"` — Kiri's own language *key* for that menu, not its
# visible text, so the rule survives a UI-language change. `:has()` hides the
# whole menu span (label plus its dropdown); the label-only rule after it is a
# fallback for engines without `:has()`, and suffices on its own because the
# label is the dropdown's trigger.
#
# The `#mode-*` divs inside `#mode-tools` are NOT touched — those are the
# per-mode tool panels, and the CAM one is exactly what we want on screen.
# Setup > install / uninstall are removed as well. Upstream they register
# /boot/service.js as a service worker at scope "/", which downloads
# bundle-<version>.bin (~25 MB of the whole app) so a hosted grid.space copy
# keeps working offline.
#
# Here that is pointless and actively harmful. Pointless because the tool is
# already served from vendor/ on loopback — it is offline by construction.
# Harmful because tools/fetch_vendor.py drops web/boot/bundle-*.bin (25 MB of
# assets we already serve from disk), so /boot/bundle-4.7.2.bin is a 404: the
# worker would register at scope "/", intercept every request the tab makes, and
# then fail to fetch what it is supposed to serve.
#
# `uninstall` goes too — it only means anything once a worker exists, and with
# install gone nothing can create one. Worth knowing that each launch gets an
# OS-assigned port, so every run is a distinct origin and service-worker
# registrations cannot carry over between sessions anyway.
_HIDE_PANELS_CSS = "\n".join([
    "#all-devpro { display: none !important; }",
    # The *preview* tab. Nothing needs it: both of the things anyone goes on to
    # do run the slice themselves — `function.animate` calls
    # `api.function.prepare()` before animating, and export does the same — so
    # it is a stop on the way rather than a step. Hiding the tab does not
    # disable the view; `setViewMode(PREVIEW)` still works, and Kiri lands in it
    # by itself after a slice, which is exactly why the tab is redundant.
    "#act-preview { display: none !important; }",
    # File > recent. It opens Kiri's own file catalogue
    # (`sync.js`: `api.modal.show('files')`) — a list of models cached inside
    # browser storage, which is a second, invisible place for a part to live
    # alongside the app's own File Manager. One of them has to be the answer to
    # "where is my file", and it is the one with a folder on disk behind it.
    #
    # Hidden, not removed: `sync.js` does `$('file-recent').onclick = ...`
    # unconditionally during init and would throw on a missing node.
    "#file-recent { display: none !important; }",
    # The *view* top menu. Everything it offers is on the view box in the
    # bottom right now: its `contents` / `home` / `top` are the same
    # `SPACE.view.*` calls those buttons make, `focal point` is driven from
    # there by clicking this menu's own item, and `fullscreen` is already
    # hidden (`#app-xpnd`). A menu whose every entry has a nearer twin is a
    # second place to look for one answer.
    #
    # Hidden, not removed, and that matters here: `ui/cam_views.py` reaches
    # `#context-setfocus` by clicking it, and a programmatic `.click()` fires
    # the handler on a `display: none` element exactly as it would on a visible
    # one. Removing the node would take the focal-point button with it.
    #
    # Matched by the language key on its label, since `topMenu()` gives the
    # span no id -- the same shape as the mode menu above.
    '#menubar span:has(> label[lk="vu_menu"])'
    " { display: none !important; }",
    '#menubar label[lk="vu_menu"] { display: none !important; }',
    # The *expert* group at the bottom of the left panel. Three of its five rows
    # are already hidden (the arc trio, since camArcEnabled is pinned off), and
    # both survivors are pinned below — see _KIRI_PROCESS_DEFAULTS — so the
    # group is a header over two controls whose values are decided elsewhere.
    #
    # The header is inside the container (`newGroup` does `div.appendChild(row)`
    # before any row), so hiding #cam-expert takes the caption with it and does
    # not leave an empty heading behind, the way hiding only the rows would.
    #
    # What goes with it is worth writing down: *skip shadow* (camExpertFast) is
    # upstream's escape hatch for a model that hangs during shadowing — its own
    # tooltip says "try enabling if slicing hangs during shadowing". Pinned off
    # below, so if a part ever refuses to slice, that pin is the thing to flip.
    "#cam-expert { display: none !important; }",
    '#menubar span.menu-right:has(> label[lk="mo_menu"])'
    " { display: none !important; }",
    '#menubar label[lk="mo_menu"] { display: none !important; }',
    "#install, #uninstall { display: none !important; }",
    # Setup > profiles. It opens Kiri's saved-process manager (app/init/sync.js
    # binds it to `api.conf.show()`, the "saves" modal): named libraries of CAM
    # process settings layered on top of the per-device default.
    #
    # Piccola pins the parts of that process which belong to the machine rather
    # than to a job — work area, origin, arc output (_ORIGIN_DEFAULTS,
    # _KIRI_PROCESS_DEFAULTS) — from _PRESET_JS on every load. So a saved
    # profile can only ever restore a subset of what it captured, and is
    # silently overwritten in exactly the fields where being wrong cuts air or
    # crashes the gantry. One machine, one process.
    #
    # Hidden, not removed: sync.js does `$('set-profs').onclick = ...`
    # unconditionally during init and would throw on a missing node. Nothing
    # else opens the dialog — there is no keyboard shortcut for it — and the
    # left panel's "Machine Profile" group (#all-devpro) is already hidden
    # above, so the profile idea leaves the UI entirely.
    "#set-profs { display: none !important; }",
    # Setup > machines. One machine, and the dialog behind it is read-only for a
    # stock device anyway (Kiri disables every field on a non-local one), so the
    # entry offers a browse of constants the app already pins.
    #
    # Nothing irreplaceable goes with it. The dialog's one non-constant is
    # `enable 4th axis` (`useIndexed`), and despite the name that switch does
    # not gate rotary CAM at all — its single effect in the whole source is
    # `show:() => ui.useIndexed.checked` on the *reset a* macro button, one
    # dialog over. The switch that actually turns rotary on is the Stock group's
    # `indexed`, which ui/cam_rotary.py lifts to the top of the left panel.
    #
    # The cost is that the `reset a` gcode macro can no longer be edited. It is
    # empty on the generic GRBL profile and only op-lathe.js reads it, so the
    # effect today is nil — but that is the thing to restore first if a custom
    # A-axis reset is ever wanted.
    #
    # Hidden, not removed: app/init/sync.js binds `$('set-device').onclick`
    # unconditionally during init and would throw on a missing node.
    "#set-device { display: none !important; }",
    # The machine popup's whole top row: the "select device" label, the machine
    # dropdown, and the copy / rename / delete / export buttons — of which only
    # one, "customize", is ever visible for a stock device (Kiri hides the other
    # three itself and relabels `#device-add` when the device is not local).
    #
    # One machine, so there is nothing to pick. Customize goes with the dropdown
    # rather than surviving it: it clones the stock profile into a *local*
    # device and selects the clone, which with no dropdown left would strand the
    # user on a copy with no way back to the original.
    #
    # What remains in the popup is a read-only summary of the real machine —
    # work area, max spindle, output flags, gcode macros — since Kiri disables
    # every field on a non-local device anyway (`e.disabled = !local`).
    #
    # The row carries no id, so it is matched through the label it contains;
    # the element rules after it are the fallback for engines without `:has()`,
    # and the sibling `.set-sep` goes too or the dialog opens on a rule with
    # nothing above it.
    '#mod-setup > div.f-row:has(> #dev-sel) { display: none !important; }',
    '#mod-setup > div.f-row:has(> #dev-sel) + div.set-sep'
    " { display: none !important; }",
    "#dev-sel, #dev-list, #device-add { display: none !important; }",
    # Upstream's funding prompts: the "this project needs your support!" link in
    # the export dialog, the donate entry in the Info menu, and the modal it
    # opens (Patreon / GitHub Sponsors / PayPal). Reasonable on grid.space,
    # out of place inside a machine control app.
    #
    # Attribution is unaffected and deliberately kept: Kiri is MIT, its LICENSE
    # ships in vendor/kirimoto/, and the CAM header credits
    # "Kiri:Moto - MIT - Stewart Allen" on every visit.
    #
    # Exact ids only. "support" is overloaded in this markup — #fdm-support and
    # #sla-support are support-STRUCTURE settings groups and must keep working.
    "#export-support, #app-don8, #mod-don8 { display: none !important; }",
    # View > fullscreen. Its handler is `document.body.requestFullscreen()`,
    # which in an embedded webview would try to blow the *web content* up over
    # the app — QtWebEngine only honours that with FullScreenSupportEnabled and
    # an explicit fullScreenRequested handler, neither of which we want here.
    # The CAM tab is a panel inside Piccola, not a window of its own.
    #
    # Kiri hides this entry itself when requestFullscreen is unavailable; in
    # Chromium it is available, so it needs hiding explicitly. The rest of the
    # View menu (set focus, contents, home, top) is untouched.
    "#app-xpnd { display: none !important; }",
    # The Info menu goes as a whole. It holds exactly two entries — help and
    # donate — and both are already unwanted here: donate is a funding prompt,
    # and help opens the same modal we suppress at startup (it is upstream's
    # release-notes/links dialog, pointing at docs, forums and Discord). Leaving
    # the menu would just be a manual way back to a popup we deliberately
    # removed.
    #
    # Anchored on lk="info", Kiri's language key, so it survives a UI-language
    # change — same approach as the Mode menu above.
    '#menubar span.menu-right:has(> label[lk="info"])'
    " { display: none !important; }",
    '#menubar label[lk="info"] { display: none !important; }',
    # The language picker (the fa-language icon at the far right, opening a
    # dropdown of #lset-zh / -da / -de / -en / ...). Piccola ships one UI
    # language, and switching only the embedded tool would leave the CAM tab
    # speaking something different from the app around it.
    #
    # Its span carries no id or language key, so this anchors on the #lset-*
    # entries it contains — those are language codes and stable. The second
    # rule empties the dropdown for engines without :has(); the icon would
    # remain but open nothing.
    '#menubar span:has([id^="lset-"]) { display: none !important; }',
    '#menubar [id^="lset-"] { display: none !important; }',
])

# Kiri's right tray is content-sized — nothing sets a width on it, so it is only
# as wide as the widest row inside, which is 81 px with an empty workspace
# (measured). The section headers do not push it out: `.set-header` is
# `overflow:hidden; white-space:nowrap`, and the disclosure marker is a `::after`
# pinned `position:absolute; right:5px`, so it is painted ON TOP of the label
# instead of beside it. "operation list" is 73 px of text in an 81 px panel, so
# the arrow lands 8 px inside the word; "objects" is short enough to escape it.
#
# Two rules doing two different jobs:
#
# * `padding-right` gives the marker a lane of its own. This is what actually
#   fixes the overlap, and it holds at any panel width — a label too long for
#   the space now clips at the padding edge, which is what `.set-header`'s own
#   `overflow:hidden` was always there for.
# * `min-width` is the "wider" half. A floor, not a width: the tray still grows
#   past it for long operation names exactly as it did before, and it only
#   binds when the lists are empty — which is when the panel is narrowest and
#   the overlap was worst.
#
# Structural selectors, so this lives here rather than in ui/cam_theme.py, whose
# first rule is variables only — a palette bridge has to survive upstream
# refactors untouched, and this cannot make that promise.
_KIRI_PANEL_CSS = "\n".join([
    "#panel-right { min-width: 130px; }",
    "#panel-right summary.set-header { padding-right: 20px; }",
])

# EasyTrace's "Welcome to EasyTrace5000" modal — title, blurb, upstream release
# note and workflow picker — is shown on EVERY load, not just the first:
# easytrace5000/cam-easytrace5000.js `onFinalize()` ends in an unconditional
# `this.modalManager.showModal('welcome')`. There is no storage flag for it.
#
# The `<prefix>-hide-welcome` key (config.js: storageKeys.forApp lowercases the
# app name and strips non-alphanumerics) only gates the *follow-up* modals —
# quickstart and laserConfig — which open after a workflow card is clicked. It
# is set anyway, defensively; on its own it does nothing for the welcome modal.
# Verified: with the key set, the modal was still visible in 431 of 435 samples.
#
# So the modal itself is suppressed with CSS. That is clean here because in
# EasyTrace the `.modal` element *is* the backdrop (position:fixed; inset:0;
# background rgba(0,0,0,.6), toggled display:none/flex) — there is no separate
# overlay left behind to swallow clicks.
#
# Consequence: the workflow picker is unreachable, so the tool stays on its
# default pipeline. That default is CNC (cam-controller.js:
# `pipelineState = { type: 'cnc', laser: null }`), which is the right workflow
# for a milling machine — but it does mean the Laser pipeline can no longer be
# chosen from the UI.
_EASYTRACE_HIDE_WELCOME_KEY = "easytrace5000-hide-welcome"

# Also hidden: EasyTrace's own dark/light toggle (#theme-toggle in its workspace
# header). The app has a single light identity, and ui/cam_theme.py scopes its
# palette overrides to the light theme — so switching the tool to dark would
# drop it to EasyTrace's stock dark colours, coherent in isolation but jarring
# inside a white app. Removing the control keeps the tab on one look.
#
# Only the button is hidden; ThemeLoader itself is untouched, so the theme can
# still be changed from our side by the localStorage key if that is ever wanted.
# And the post-processor picker. The machine is fixed, and GRBL is both
# EasyTrace's default (config.js: `postProcessor: 'grbl'`) and — per its own
# docs — its best-tested output. Offering Marlin / LinuxCNC / Roland here is a
# way to silently emit a dialect the Piccola firmware does not speak.
#
# `:has()` removes the whole labelled row so no orphan label or gap is left; the
# element-level rules after it are a fallback for engines without `:has()`.
# Hiding the control alone is not enough — a previously saved choice would
# persist with no way to change it — so the preset below also forces the setting
# back to grbl through EasyTrace's own updateSettings().
_EASYTRACE_POST_PROCESSOR = "grbl"

# Start Code / End Code and Units go with it — the rest of that CNC Output
# section — so the whole `.property-section` is hidden rather than four fields
# inside a container that would then be empty.
#
# Start and end code are the preamble and postamble wrapped around every export.
# On a fixed machine they are a property of the machine, not of the job, and they
# are the easiest field in the tool to break silently: a stray line here is a
# stray line in front of every cut. So they stop being a text box the user can
# edit and become the two constants below.
#
# `None` means "whatever the post-processor ships", which is what an untouched
# install already emits — for GRBL (export/processors/grbl-processor.js):
#
#     startCode: 'T1\n'
#     endCode:   'M5\nG0 X0Y0\nM2'
#
# Put a string here to override that, and it applies to every export with no
# further wiring: EasyTrace resolves `userStartCode` / `userEndCode` over the
# processor default (export/gcode-generator.js resolveStartCode/resolveEndCode)
# and cam-core.js passes both into generation. Reading the default from the
# processor rather than copying the text here means a vendor refresh that
# improves the GRBL preamble is picked up instead of being silently pinned to a
# stale copy.
#
# Note that `''` is not "no preamble" — upstream treats empty as unset and falls
# back to the default. An empty preamble would have to be expressed as a comment
# line.
_EASYTRACE_START_CODE = None
_EASYTRACE_END_CODE = None

# Units. The machine is metric, every other length in this app is mm, and the
# setting only picks the G20/G21 word plus the units the coordinates are written
# in — so an accidental "inch" is a 25.4x error on every coordinate, not a
# display preference. Already EasyTrace's own default (config.js: `units: 'mm'`);
# pinned so a value saved before the picker was hidden cannot stick.
_EASYTRACE_UNITS = "mm"

# Rapid feed, in mm/min. This is the machine's traverse rate, so it belongs to
# the machine rather than to a job; EasyTrace ships 1000 (config.js:
# `speeds.rapidFeed`). The control itself is left visible — this is a default,
# re-applied on every load, not a lock.
#
# Written to two keys, because upstream disagrees with itself about the name:
# the Machine Settings field reads and writes `speeds.rapidFeed`
# (ui-machine-settings.js), while the toolpath pipeline reads
# `speeds.rapidFeedRate` (cam-core.js buildToolpathContext), a key config.js
# never defines. Setting only the field's key would leave the value invisible to
# the code that consumes it, so both are set to the same number.
#
# What it actually changes is the job-time estimate, not the G-code: rapids are
# emitted as G0 with no feed word, and `rapidFeedRate` is read by
# calculatePathMetrics() alone. With the key undefined, every rapid contributes
# `undefined` there, fails the `feed > 0` test and is costed as zero time — so
# estimates today count only cutting moves. Setting it makes them count travel
# too, at the real traverse rate.
_EASYTRACE_RAPID_FEED_MM_MIN = 1500

# Spindle speed, in RPM. One machine, one spindle — but EasyTrace takes this
# from whichever tool the library picks, and tools.json spreads it from 5000 to
# 18000 across the catalogue, so the starting RPM changes with the endmill
# rather than with the machine.
#
# Unlike the settings above this is not a stored setting: it is an operation
# *parameter*, resolved in ParameterManager.getDefaults() from
# `tool.cutting.spindleSpeed`. So it is pinned by wrapping that method — see the
# preset — which sets what every operation starts with while leaving the field
# editable and any value the user already saved untouched (loadFromOperation
# prefers live state, then saved settings, and only then these defaults).
#
# It does reach the machine: the value flows through the operation's parameters
# into plan.metadata.spindleSpeed (toolpath-geometry-translator.js) and out as
# the S word. Well inside both the parameter's own 100..30000 range and GRBL's
# maxSpindleSpeed.
#
# Changing the tool does not undo it — that path re-derives only toolDiameter
# (ui-base-operation-panel.js onParameterChange), never the cutting feeds.
_EASYTRACE_SPINDLE_RPM = 12000

# Operation types that start with "Multi-depth Cutting" off — every one, so the
# box is unticked wherever it appears. Upstream's parameter definition ships
# `default: true`, which splits a cut into `depthPerPass` passes; off, the
# toolpath goes to full depth in one (cam-core.js calculateDepthLevels returns
# a single level).
#
# Pinned the same way as the spindle, through getDefaults(), so it is a starting
# value and not a lock: the box stays usable and a value already saved on an
# operation still wins.
#
# Worth knowing before leaving this at "every type": on isolation and clearing
# the cut is 0.1 mm deep and a single pass is simply right. Cutout and milled
# drill holes default to -1.8 mm (profile-trace.json operationDefaults), and one
# pass to that depth is a lot to ask of a small endmill. Dropping "cutout" and
# "drill" from this tuple restores upstream's multi-pass for exactly those two
# and leaves the shallow work single-pass.
_EASYTRACE_SINGLE_PASS_OPS = ("isolation", "clearing", "drill", "cutout")

# Coolant and vacuum are removed for the same reason. The machine has neither,
# and these are not cosmetic settings: export/processors/base-processor.js
# injects M7 (mist) / M8 (flood) plus M9 from coolant, and M10 / M11 from
# vacuum. A stray value emits commands the machine cannot honour.
#
# Both already default off (config.js: `coolant: 'none'`, `vacuum: false`); the
# preset re-asserts them so a value saved before the controls were hidden cannot
# stick.
#
# They share one `.property-section` and are its only two fields, so the whole
# section is hidden — that avoids leaving an empty container behind. The section
# carries no unique id, hence anchoring on `#coolant-type`. If upstream ever adds
# a third control there it would be hidden too, which is the trade for not
# depending on DOM position.
_EASYTRACE_COOLANT = "none"
_EASYTRACE_VACUUM = False

# The toolbar's "Actions" dropdown (#quick-actions-btn) and everything in it.
# Its three entries are either a second route to something the workspace already
# offers, or a route back into a dialog we deliberately suppress:
#
# * "Add Files..." opens the quickstart modal — the follow-up to the welcome
#   modal we remove, and the same picker whose laser/CNC workflow choice we
#   deliberately leave pinned to CNC. Files are added from the operations tree
#   ("Click to add Isolation file" and friends) or by dropping them on the
#   window, both untouched.
# * "Manage Toolpaths" opens the export manager. That dialog stays reachable —
#   it is what an operation's own machine/export stage opens
#   (ui-trace-operation-panel.js onExportStage), which is where our Save as File
#   / Save to Machine buttons live. Only this shortcut to it goes.
# * "Export Canvas SVG" downloads a picture of the canvas. It is not G-code, so
#   it lands in the export staging folder and gets counted as "no G-code
#   recognised" — a dead end inside this app.
#
# The whole `.dropdown-menu` wrapper is hidden rather than the button alone, so
# no empty dropdown container is left in the toolbar. `.toolbar-left` keeps its
# `flex: 1`, so the workspace title stays centred with the section empty.
#
# Upstream's click handlers still bind to the hidden elements, which is harmless
# — nothing can reach them — and keeps this a pure CSS change to a byte-identical
# bundle.

# The Solder Stencil (Exp.) category in the operations tree goes too. It is the
# one operation type that never produces G-code: cam-controller.js routes stencil
# operations away from the CNC path entirely
#
#     if (op.type === 'stencil') stencilOperationIds.push(id);
#
# and cam-core.js exports them through generateLaserExportFiles() with
# `format: 'svg'` — artwork for a laser or a stencil cutter, not toolpaths for a
# mill. Inside Piccola such a file lands in the export staging folder and is
# reported as "no G-code recognised", so the category could only ever lead to a
# dead end. (It is also marked experimental upstream.)
#
# Anchored on `data-op-type="stencil"`, the attribute EasyTrace's own tree code
# keys off, rather than on position or the visible label — so it survives a
# reordering or a rename. The whole `.operation-category` goes, header and file
# list together, leaving the other four (isolation, drilling, clearing, cutout)
# untouched.
#
# Nothing else needs hiding: with no way to create a stencil operation, the
# export manager's stencil block (#exporter-stencil-options) stays hidden by its
# own `jobHasStencil` check.
#
# Hiding the category is not quite enough on its own, though. A dropped file is
# typed by extension (cam-easytrace5000.js getOperationTypeFromExtension, over
# core.fileTypes), and `.gtp` / `.gbp` — the paste layers — are claimed by
# nothing but stencil. Dropping one would still create a stencil operation, now
# with its tree node inside a hidden category: invisible, unselectable, and
# silently part of the next export. So the preset drops the stencil file type as
# well, which makes those extensions unrecognised and the drop a no-op. Every
# other extension is claimed by an earlier entry (isolation/clearing/cutout/
# drill) and is unaffected.
_EASYTRACE_STENCIL_OP = "stencil"

# The export manager's "Optimize toolpaths (Experimental)" checkbox. Upstream
# ships it checked, and the export reads it as `?.checked ?? true`, so the
# option's only real use is turning optimisation *off* — an experimental switch
# whose two settings emit different G-code for the same job:
# toolpath-optimizer.js reorders paths, clusters staydown moves, and simplifies
# segments (collinear points within 1°, sub-0.05 mm moves).
#
# One machine, one behaviour, and the one that ships as the default is also the
# one upstream tests: the control goes and the value is pinned on. That is
# already the DOM state, but the preset re-asserts it so a future markup change
# upstream cannot silently flip it while the control is invisible — the same
# reasoning as the post-processor pin.
#
# It shares the "CNC Output Options" block with "Insert tool change commands",
# which upstream ships `disabled`: EasyTrace has no M6 support, so the checkbox
# cannot be ticked and the export reads it as false. With both of its fields
# unusable the block is nothing but a heading over a dead control, so the whole
# `#exporter-cnc-options` div is hidden rather than the two fields inside it.
# Both values are still read from the (hidden) elements at export time, which is
# why the pin below matters and why the tool-change box is left unchecked.
_EASYTRACE_OPTIMIZE_PATHS = True

# "Include operation comments", same treatment: the row is hidden and the box
# pinned on. Comments are the header EasyTrace writes in front of a program —
# operation, tool, feeds — which is what makes a staged file readable weeks
# later, and nothing on this machine is helped by turning them off. Upstream
# ships the box checked, so this only guards against a markup change flipping it
# while the control is invisible.
_EASYTRACE_INCLUDE_COMMENTS = True

# Origin Offset spinner step, in mm. Upstream ships `step="0.01"`, so a click on
# the arrows (or an Up/Down key) nudges the board by 10 µm — far below anything
# that matters when placing a board on this machine, and 100 clicks to move it a
# millimetre.
#
# `step` is the only lever here: the arrows are the browser's own spin buttons,
# and they always move by the field's step. Typed values are unaffected — a step
# mismatch marks the input :invalid but never rewrites `.value`, EasyTrace reads
# the field with parseFloat(), and nothing in the bundle calls checkValidity()
# or styles :invalid — so 0.5 can still be typed and still applies as 0.5.
_EASYTRACE_OFFSET_STEP_MM = 1

# The operations tree's onboarding highlight loop. 12.5 s after a file is added
# — and re-armed by every further file — ui-nav-tree-panel.js starts an interval
# that walks a 2 px accent ring across the loaded file nodes every 3.25 s
# (scheduleSuggestion -> startSuggestionCycle -> cycleHighlight). It is meant as
# a hint that files are clickable, and it stops the moment an operation is
# actually selected.
#
# In this app it reads as the selection moving on its own between the isolation
# and drill files — the ring is drawn in the same accent colour as a real
# selection, so a tab left open looks like it is being driven by something.
#
# Turned off through the tool's own `dismissSuggestion()`, which is what a click
# calls: it clears both timers, drops the class, and latches a flag that makes
# every later scheduleSuggestion() a no-op — so files added afterwards cannot
# restart it. The CSS rule below is a second line of defence, in case an
# upstream rename ever makes the call a no-op.
_EASYTRACE_ONBOARDING_TRIES = 30
_EASYTRACE_ONBOARDING_INTERVAL_MS = 300

_EASYTRACE_HIDE_CSS = "\n".join([
    "#welcome-modal { display: none !important; }",
    "#theme-toggle { display: none !important; }",
    # The whole CNC Output section: post-processor, Start Code, End Code and
    # Units are its only four fields and all four are pinned, so hiding the
    # section avoids leaving an empty container and its margins behind. Anchored
    # on #gcode-units, since the section carries no id of its own.
    ".property-section:has(> .property-field > #gcode-units)"
    " { display: none !important; }",
    # Fallbacks for engines without :has(), field by field.
    ".property-field:has(> #post-processor) { display: none !important; }",
    "#post-processor, label[for=\"post-processor\"],"
    " #start-code-ta, label[for=\"start-code-ta\"],"
    " #end-code-ta, label[for=\"end-code-ta\"],"
    " #gcode-units, label[for=\"gcode-units\"] { display: none !important; }",
    # Whole section: coolant + vacuum are its only two fields.
    ".property-section:has(#coolant-type) { display: none !important; }",
    # Fallback for engines without :has(). The vacuum input sits *inside* its
    # label, so the label has to be targeted too or the caption would remain.
    "#coolant-type, label[for=\"coolant-type\"],"
    " #vacuum-toggle, label[for=\"vacuum-toggle\"] { display: none !important; }",
    # Whole dropdown: the Actions button is its only trigger.
    ".dropdown-menu:has(> #quick-actions-btn) { display: none !important; }",
    # Fallback for engines without :has(); enough on its own, since the button
    # is what opens the menu and the menu is hidden here too.
    "#quick-actions-btn, #quick-actions-menu { display: none !important; }",
    # Solder Stencil: SVG artwork, never G-code. See above.
    f'.operation-category[data-op-type="{_EASYTRACE_STENCIL_OP}"]'
    " { display: none !important; }",
    # The export manager's whole "CNC Output Options" block — the heading, the
    # permanently-disabled "Insert tool change commands" row and the "Optimize
    # toolpaths (Experimental)" row. Neither field is usable here (see above),
    # and hiding the block rather than the two rows leaves no heading standing
    # over nothing. Plain id selector, so no `:has()` fallback is needed.
    "#exporter-cnc-options { display: none !important; }",
    # "Calculate Toolpaths" and the G-code preview it feeds.
    #
    # The button is not a step in exporting, which is what makes it confusing:
    # it looks like something you must press first, and it isn't. Export runs
    # the pipeline itself when nothing is cached — cam-core.js executeExport():
    #
    #     let gcodeResults = intent.gcodeResults;
    #     if (!gcodeResults || Object.keys(gcodeResults).length === 0) {
    #         gcodeResults = await this.generateCNCResults({ ... });
    #     }
    #
    # and the modal passes `gcodeResults: ... : null` when the button was never
    # pressed. So all it ever did was populate the preview pane early and cache
    # the result for the export that follows.
    #
    # The preview goes with it, and has to: #exporter-cnc-preview — the heading,
    # the operation selector, the Lines / Operations / Est. Time / Distance
    # stats, the G-code textarea and its copy-to-clipboard button — is fed by
    # that one handler and nothing else (runToolpathOrchestration has exactly
    # one caller, this button). Left on its own it would sit there permanently
    # reading "Click Calculate Toolpaths to generate G-code" with 0 lines and
    # --:-- time, which is a worse version of the confusion being removed.
    #
    # Nothing is lost on the export side: G-code leaves through Export Files and
    # is intercepted into the File Manager upload path, so the textarea was
    # never how anyone here reads it.
    "#exporter-calculate-btn, #exporter-cnc-preview { display: none !important; }",
    # …and the width that was there to hold it. The dialog is a two-column flex
    # body — `.gcode-options` at `flex: 0 0 45%` beside `.exporter-preview` at
    # `flex: 1`. Hiding the preview takes away the second column but not the
    # room reserved for it, so the operations list kept its 45% and the rest of
    # the panel stood empty: measured in a 1400x900 window, an 850px dialog
    # holding a 368px column of controls. It is now 540px holding 508px — the
    # controls came out *wider* than they were; only the empty half is gone.
    #
    # The three declarations on `.gcode-options` are exactly what EasyTrace's
    # own `.gcode-options.is-full-width` applies — the modifier it uses for the
    # pipelines that have no preview (laser, stencil). Same intent, reached
    # from CSS rather than by adding the class from script, so the tool is
    # still untouched.
    #
    # 540px is not a new number either: it is `.modal-warning`'s width, so the
    # export panel now matches a dialog this UI already draws instead of
    # inventing a size.
    #
    # Note the compound selector. Upstream writes `.modal--exporter
    # .modal-content` (modals.css:898) but the markup is a single
    # `<div class="modal-content modal--exporter">`, so that descendant rule
    # matches nothing and its 900px never applied — the dialog was falling back
    # to the generic `.modal-content { max-width: var(--modal-max-width) }`,
    # measured at 850px in a 1400x900 window. Copying their selector would have
    # inherited the bug, which is exactly what the first attempt here did.
    ".modal-content.modal--exporter { max-width: min(540px, 95vw) !important; }",
    ".modal--exporter .gcode-options"
    " { flex: 1 1 auto !important; border-right: none !important;"
    " padding-right: 0 !important; }",
    # Short windows: modals.css stacks the body and caps the options column at
    # `max-height: 45%` to leave the lower half for the preview. With no
    # preview that cap just squashes the list against a gap, so it is lifted at
    # the same breakpoint (@media max-height 550px, modals.css:1348).
    "@media (max-height: 550px) {"
    " .modal--exporter .gcode-options { max-height: none !important; } }",
    # The "Cutting Strategy" group in an operation's Strategy stage. Its only
    # field is Entry Type, and that select has exactly one option — "Plunge
    # (Vertical)" — which is also its default and what the pipeline falls back
    # to everywhere it reads the value (`strategy.entryType || 'plunge'`). A
    # heading and a dropdown that can only say one thing.
    #
    # Hidden rather than removed, so the parameter is still rendered and keeps
    # its value: ParameterManager loads every definition regardless of what is
    # on screen, so `entryType` stays "plunge" with nothing to pin.
    #
    # The group has no id — each category renders as a bare
    # `<div class="property-section"><h3>…</h3>` (ui-base-operation-panel.js
    # renderParameterForm) — so it is anchored on the field it contains, with
    # the tool's own `prop-` id prefix.
    "#property-form .property-section:has(#prop-entryType)"
    " { display: none !important; }",
    # Fallback for engines without :has(); leaves the heading standing.
    "#prop-entryType, label[for=\"prop-entryType\"]"
    " { display: none !important; }",
    # The rules under the group headings in the properties sidebar. Every
    # parameter category draws one (`.property-section h3` has a border-bottom),
    # so a stage like a drill's Strategy — Depth Settings, Peck Drill Parameters
    # around the Dwell Time field — is cut into bands by lines the headings
    # already imply. The headings stay; they are uppercase and letter-spaced and
    # carry the separation on their own. The padding the rule sat on goes with
    # it, so no dead gap is left behind.
    #
    # Scoped to the sidebar: the export dialog uses the same markup for its own
    # headings and is left alone.
    "#sidebar-right .property-section h3"
    " { border-bottom: none !important; padding-bottom: 0 !important; }",
    # "Include operation comments" in the export manager. Comments are the
    # header EasyTrace writes in front of a program — operation, tool, feeds —
    # which is what makes an exported file readable later, and there is no
    # reason on this machine to turn them off. The row goes and the box stays
    # ticked; see _EASYTRACE_INCLUDE_COMMENTS.
    #
    # The three checkboxes share one `.property-field`, so the label is hidden
    # rather than the container — the other two are handled separately.
    "label.checkbox-label:has(> #exporter-include-comments)"
    " { display: none !important; }",
    # Fallback for engines without :has(): the caption sits inside the label
    # with no `for`, so this hides the box and leaves the text.
    "#exporter-include-comments { display: none !important; }",
    # "Split drill files by hole diameter". It exists because EasyTrace has no
    # M6: splitting gives one file per diameter, i.e. per drill bit. It ships
    # unticked, so hiding it changes nothing about today's output — every hole
    # in a drill operation is cut with the one tool that operation selects,
    # which is what milling them (millHoles) does anyway.
    #
    # This row has a wrapper of its own, so the whole field can go.
    "#exporter-split-drills-field { display: none !important; }",
    # The rule above the stage's action button. `.property-actions` carries a
    # border-top, and the button is appended straight after the parameter form
    # — so in a drill's Geometry stage, where Mill Holes (and Mill Stepover
    # under it) are the last fields, that line lands right beneath them.
    #
    # It is the last horizontal rule left in the panel now that the group
    # headings have lost theirs, and the button is unmistakable on its own: it
    # is full width and in the accent colour. The padding and margin stay, so
    # the button keeps its distance from the field above.
    "#property-form .property-actions { border-top: none !important; }",
    # The seam inside a number field, between the input and its unit chip.
    # `.input-unit input` drops its right border and `.unit` carries a full one,
    # so the two meet in a vertical line — the one standing beside Mill
    # Stepover, the field that appears under Mill Holes when it is ticked.
    #
    # Only that edge goes: the chip keeps its top, right and bottom border and
    # its own background, so it still reads as a unit chip rather than as part
    # of the input. Scoped to the operation properties form, which is where the
    # per-operation numbers are set; the Board Placement and Machine Settings
    # fields keep theirs.
    "#property-form .input-unit .unit { border-left: none !important; }",
    # The onboarding highlight ring (see above). The preset stops the loop that
    # applies this class; neutralising the class itself means an upstream rename
    # of dismissSuggestion() would cost the timers, not a visible effect.
    ".file-node-content.onboarding-highlight"
    " { box-shadow: none !important; }",
    # The toolbar's vertical rule. It divided the view controls from Help and
    # the theme toggle, and with both of those gone it is the last thing in the
    # row: a 1 px line hanging off the right edge with nothing after it to
    # separate. Hiding it also puts Fit to View / Zoom In / Zoom Out flush
    # right, because the rule's own left and right margins were the gap holding
    # them off the edge.
    #
    # Scoped to the toolbar: `.separator` is also used inside dialogs, where it
    # still divides something.
    ".cam-toolbar .toolbar-separator { display: none !important; }",
    # The help markers — a "?" glyph (`.tooltip-trigger`, #icon-help) that
    # ui-tooltip.js appends to labels. They come from three places: static
    # labels carrying data-i18n-tooltip (Origin Offset, Board Rotation, Mirror
    # Geometry, the machine settings), the per-operation fields the parameter
    # manager generates, and the export dialog's own attachTo() calls — so one
    # sits beside almost every label in the tool.
    #
    # Hidden everywhere rather than per panel, which is both simpler and what
    # was asked for twice over. The tooltip is attached to the marker rather
    # than the label, so this does drop that hover help; the labels themselves
    # are unchanged, and hidden markers also leave the tab order.
    ".tooltip-trigger { display: none !important; }",
])

# Injected at *document creation*, before the tool's own scripts run — which is
# the only point early enough to influence its first-run behaviour.
#
# Kiri:Moto decides whether to load its "sample cube.stl" demo part by checking
# whether the localStorage key `kiri-seed` exists; if it does not, that is a
# first run and it loads the cube. By the time window.kiri exists (when
# _PRESET_JS can run) the decision has already been taken, so the flag cannot be
# switched off from there — verified: setting kiri.api.feature.seed = false at
# that point still leaves the cube loaded.
#
# Deleting the part afterwards is not an option either: Kiri restores a saved
# workspace on launch, so "clear the platform" would throw away the user's own
# loaded model. Marking the app as already-run leaves the demo unloaded without
# touching anything the user owns.
#
# `gdpr` is the cookie notice's dismissal timestamp; pre-setting it means the
# banner never paints, rather than being clicked away a moment later.
# `kiri-last` holds the version whose Help modal has been seen; Kiri shows the
# modal whenever it differs from the running version. Read the version out of the
# vendored source rather than hard-coding it, so a vendor refresh cannot silently
# leave a stale value here (which would bring the modal back).
_KIRI_VERSION_RE = re.compile(r'VERSION:\s*"([^"]+)"')

#: The language Kiri:Moto is pinned to. `init/lang.js` treats 'en' and 'en-us'
#: as "no translation to load" and skips the script injection entirely, so this
#: is the value that costs nothing rather than one that loads an English file.
_KIRI_LANG = "en"


def _kiri_version(bundle_dir: str) -> str | None:
    path = os.path.join(bundle_dir, "src", "moto", "license.js")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            m = _KIRI_VERSION_RE.search(fh.read())
    except OSError:
        return None
    return m.group(1) if m else None


def _isolate_modules(parts) -> str:
    """Join the injected modules so one failure cannot take out the rest.

    They all go into a single ``<script>``, and a throw at the top level of one
    stops the whole file: every module after it silently never installs. That is
    not a hypothetical — with a deliberately broken host, 2 of 16 modules
    installed and the other 14 vanished without a word. What it looks like from
    the outside is a feature that does not exist, with the features injected
    *before* the failure all working perfectly, which is a very misleading thing
    to debug.

    Each module therefore gets its own try/catch, and a failure is named. The
    message goes to the console (captured by the page subclass, so a stuck or
    half-working tab can say which module died) and to
    ``window.__piccolaFailed`` for anything that wants to ask.

    Modules are independent by design — each polls for what it needs and gives
    up quietly — so continuing past a broken one is right: fourteen working
    features beat none.
    """
    out = []
    for name, js in parts:
        out.append(
            "\n;(function () { try {\n%s\n} catch (e) {\n"
            "  try { (window.__piccolaFailed = window.__piccolaFailed || [])"
            ".push([%s, String((e && e.message) || e)]); } catch (_) { }\n"
            "  try { console.error('piccola module failed:', %s, e); }"
            " catch (_) { }\n} })();\n" % (js, json.dumps(name), json.dumps(name)))
    return "".join(out)


def _style_js(css: str) -> str:
    """JS that installs *css* as a <style> element.

    Runs at document creation, so the rules are in place before the tool's own
    stylesheets paint anything — no flash of the tool's native colours.
    documentElement may not exist that early, hence the short retry.
    """
    return f"""
  (function addStyle() {{
    var root = document.documentElement || document.head || document.body;
    if (!root) {{ setTimeout(addStyle, 10); return; }}
    var s = document.createElement('style');
    s.setAttribute('data-piccola', 'theme');
    s.textContent = {json.dumps(css)};
    root.appendChild(s);
  }})();"""


def _init_js(key: str, server) -> str | None:
    """Build the document-creation script for *key*, or None if not needed."""
    stmts = []
    css = ""
    extra_js = ""

    if key == "carve":
        stmts += [
            # First-run marker: prevents the sample cube ever loading.
            "if (!localStorage.getItem('kiri-seed'))"
            " localStorage.setItem('kiri-seed', String(Date.now()));",
            # Cookie-notice dismissal timestamp.
            "if (!localStorage.getItem('gdpr'))"
            " localStorage.setItem('gdpr', String(Date.now()));",
            # English, whatever the machine is set to. Kiri picks its language
            # at boot (`init/lang.js`):
            #
            #     let lang = SETUP.ln ? SETUP.ln[0]
            #              : sdb.getItem('kiri-lang') || api.language.get();
            #
            # and `api.language.get()` is `navigator.language` — the SYSTEM
            # language. On an Italian or German desktop the tool would load
            # `/kiri/lang/it-it.js` and rename its own controls, while every
            # label this app rewrites (ui/cam_labels.py) is matched by English
            # `lk` key and English text: half the panel would come out
            # translated and half not, and the relabelled ones would be the
            # half naming machine limits.
            #
            # Set unconditionally rather than only when absent, because that is
            # what "stays English" means: a machine whose system language
            # changes later, or a stored value from before this pin, would
            # otherwise bring the translation back. The cost is that Kiri's own
            # language picker no longer sticks across a restart.
            f"localStorage.setItem('kiri-lang', {json.dumps(_KIRI_LANG)});",
            # Drop any service worker left on this origin. Install is hidden
            # (see _HIDE_PANELS_CSS), but the port is fixed and the profile is
            # persistent, so a worker registered by an older build would still
            # sit at scope "/" and answer every request -- from a cache of a
            # previous Kiri version, or with 404s for the missing bundle.
            "try { if (navigator.serviceWorker)"
            " navigator.serviceWorker.getRegistrations().then(function (rs) {"
            " rs.forEach(function (r) { r.unregister(); }); }); } catch (e) {}",
        ]
        version = _kiri_version(server.bundle_dir("kirimoto")) if server else None
        if version:
            # Set unconditionally, not only when absent: after a Kiri:Moto
            # upgrade a stale value is exactly what re-triggers the modal.
            stmts.append(
                f"localStorage.setItem('kiri-last', {json.dumps(version)});")
        css = "\n".join([_HIDE_PANELS_CSS, _KIRI_PANEL_CSS,
                          cam_export.hide_css("carve"), cam_arcs.hidden_row_css(),
                          cam_stock_origin.css(), cam_stock.css(),
                          cam_views.css(), cam_rotary_badge.css(),
                          cam_anim_end.css(),
                          cam_anim_markers.anim_markers_css(),
                          cam_tooltips.css(),
                          cam_tool_icons.css(),
                          cam_op_menu.css(),
                          cam_measure.css(),
                          cam_part_size.css(),
                          cam_ranges.css(),
                          cam_stock_box.css(),
                          cam_gcode_header.css(),
                          cam_op_names.css(),
                          cam_theme.kirimoto_css()])
        extra_js = _isolate_modules([
            ("export", cam_export.export_buttons_js(
                "carve", cam_server.CONTROL_PREFIX + "export-intent")),
            ("labels", cam_labels.relabel_js("carve")),
            ("tools", cam_tools.kiri_tools_js()),
            ("arcs", cam_arcs.hide_kiri_arc_controls_js()),
            ("rotary", cam_rotary.move_rotary_switch_js()),
            ("rotary-badge", cam_rotary_badge.rotary_badge_js()),
            ("stock-origin", cam_stock_origin.stock_origin_js()),
            ("undo", cam_undo.undo_js()),
            ("toolchange", cam_toolchange.hide_tool_init_js()),
            ("anim-end", cam_anim_end.jump_to_end_js()),
            ("anim-after-export", cam_anim_after_export.anim_after_export_js()),
            ("stock-offset", cam_stock.hide_stock_offset_js()),
            ("stock-offset-labels", cam_stock.offset_labels_js()),
            ("stock-round", cam_stock.round_stock_js()),
            ("part-size", cam_part_size.part_size_js()),
            ("grid", cam_grid.lift_grid_switch_js()),
            ("views", cam_views.view_bar_js()),
            ("op-names", cam_op_names.op_names_js()),
            ("auto-z", cam_auto_z.auto_z_js()),
            ("anim-markers", cam_anim_markers.anim_markers_js()),
            ("origin-marker", cam_origin_marker.origin_marker_js()),
            ("anim-layers", cam_anim_layers.keep_layers_js()),
            ("import-prompt", cam_import_prompt.import_prompt_js()),
            ("render-menu", cam_render_menu.render_menu_js()),
            ("import-file", cam_import_file.import_file_js()),
            ("anim-buttons", cam_anim_buttons.anim_buttons_js()),
            ("box-select", cam_box_select.box_select_js()),
            # before "tooltips": both listen for mouseover on the document
            # in the capture phase, and the Mode row's title must be on it
            # before the panel reads it on the same event
            ("mode-tips", cam_mode_tips.mode_tips_js()),
            ("tooltips", cam_tooltips.tooltips_js()),
            ("tool-icons", cam_tool_icons.tool_icons_js()),
            ("op-art", cam_op_art.op_art_js()),
            ("op-menu", cam_op_menu.op_menu_js()),
            ("group-tips", cam_group_tips.group_tips_js()),
            ("scale-live", cam_scale_live.scale_live_js()),
            ("measure", cam_measure.measure_js()),
            ("rough-flats", cam_rough_flats.rough_flats_js()),
            ("ranges", cam_ranges.ranges_js()),
            ("stock-box", cam_stock_box.stock_box_js()),
            ("anim-solid", cam_anim_solid.anim_solid_js()),
            ("no-ops", cam_no_ops.no_ops_js()),
            ("preview-solid", cam_preview_solid.preview_solid_js()),
            ("analysis", cam_analysis.analysis_js()),
            ("materials", cam_materials.materials_js()),
            ("ops-guide", cam_ops_guide.ops_guide_js()),
            ("tool-library", cam_tool_library.tool_library_js()),
            ("render-solid", cam_render_solid.render_solid_js()),
            ("tool-presets", cam_tool_presets.tool_presets_js()),
            ("gcode-header", cam_gcode_header.gcode_header_js()),
            ("minion-pool", cam_minion_pool.minion_pool_js()),
        ])

    elif key == "pcb":
        # EasyTrace defaults to its dark theme. Select light before it boots so
        # it never paints dark first, and so our overrides (which are scoped to
        # the light theme) apply. Only when unset — a user who deliberately
        # picks dark keeps it, and gets EasyTrace's own coherent dark theme.
        stmts.append(
            f"if (!localStorage.getItem({json.dumps(cam_theme.EASYTRACE_THEME_KEY)}))"
            f" localStorage.setItem({json.dumps(cam_theme.EASYTRACE_THEME_KEY)},"
            f" {json.dumps(cam_theme.EASYTRACE_THEME)});")
        # Set every load, not just when absent — this blocks the quickstart /
        # laserConfig follow-ups. The welcome modal itself is handled by CSS.
        stmts.append(
            f"localStorage.setItem({json.dumps(_EASYTRACE_HIDE_WELCOME_KEY)}, '1');")
        css = "\n".join([_EASYTRACE_HIDE_CSS, cam_export.hide_css("pcb"),
                          cam_origin.css(), cam_rotation.css(),
                          cam_modals.css(), cam_theme.easytrace_css()])
        extra_js = (cam_fixes.js_for_mode("pcb") + "\n"
                    + cam_export.export_buttons_js(
                        "pcb", cam_server.CONTROL_PREFIX + "export-intent")
                    + "\n" + cam_labels.relabel_js("pcb")
                    + "\n" + cam_origin.origin_buttons_js()
                    + "\n" + cam_rotation.auto_apply_js()
                    + "\n" + cam_modals.modal_guard_js()
                    + "\n" + cam_links.footer_links_js()
                    + "\n" + cam_tools.add_tools_js()
                    + "\n" + cam_arcs.linearize_arcs_js())

    if not stmts and not css and not extra_js:
        return None

    js = ""
    if stmts:
        js += ("try {\n  " + "\n  ".join(stmts) +
               "\n} catch (e) { /* storage disabled — fallbacks still apply */ }")
    if css:
        js += _style_js(css)
    if extra_js:
        # Upstream bug workarounds (ui/cam_fixes.py). Injected at document
        # creation so the patch is installed before the tool's own scripts run.
        js += "\n" + extra_js
    return js

# Post-load setup run inside a tool, keyed by mode. This is *not* patching the
# vendored source — it drives the tool's own public API and its own UI from
# outside, so the bundles stay byte-identical and upgrades stay a folder swap.
#
# 1. Kiri:Moto boots into FDM (3D printing), the wrong tool for a CNC machine:
#    the user would land on slicing settings and have to hunt for the mode
#    switch. Put it in CAM mode.
# 2. Dismiss the two first-run overlays Kiri:Moto shows — the GDPR cookie notice
#    and the Help modal (docs / forums / Discord links). Both are noise inside an
#    embedded CAM tab.
#
#    On the cookie notice: upstream already suppresses it when the host is
#    exactly "localhost". We serve on 127.0.0.1, which does not match that
#    string, so it appears. Pointing the webview at "localhost" would also fix
#    it, but that name can resolve to ::1 while the server binds IPv4 only — not
#    a risk worth taking for a banner.
#
#    On the Help modal: it is gated on a stored "help version seen" value, so it
#    fires on first run *and after every Kiri:Moto upgrade*.
#
#    Both are dismissed by clicking the tool's own controls (#gotit, #mod-x)
#    rather than hiding elements, so Kiri's real handlers run and record the
#    dismissal in its settings — they stay gone on later launches. Each is
#    created during init and can appear after the mode API exists, so this polls
#    for a few seconds and clicks whatever shows up. It only ever clicks a
#    control that is actually visible.
# 3. Pin the work area. The machine is fixed, so the bed is too. Kiri's stock
#    GRBL profile ships a different size (400 x 400 x 150), and the value is
#    what its stock/origin maths and out-of-bounds checks key off, so leaving it
#    wrong would let a job lay out past the real travel. The machine dialog's
#    fields are refreshed with it, or they keep showing the stock numbers while
#    the toolpaths use ours.
_PRESET_JS = {
    "carve": """
        kiri.api.mode.set('CAM');
        try {
            var api = kiri.api;
            // Force the device, do not merely default it. With the machine
            // dropdown gone (see _HIDE_PANELS_CSS) whatever is selected becomes
            // permanent, so a profile that had drifted onto another machine —
            // or onto a local "customize" copy made before that row was hidden
            // — would be stuck there with no way back. Selecting only when
            // unset would preserve exactly that state.
            var dev = (api.conf.get().device || {}).deviceName;
            if (dev !== '%(grbl)s') { api.devices.select('%(grbl)s'); }
            // Switching mode does not repopulate the machine dropdown, so
            // without this it keeps listing the FDM printers (Marlin, Klipper)
            // and displays the wrong machine name even though the active device
            // really is GRBL. refresh() rebuilds the list for the current mode.
            api.devices.refresh();

            // Pin the work area and Z envelope to the real machine travel, and
            // normalise the process fields that belong to the machine rather
            // than to a job. See _ORIGIN_DEFAULTS / _KIRI_PROCESS_DEFAULTS.
            //
            // originCenter is pinned false (corner reference, the GRBL
            // convention) purely for determinism: Kiri's CAM viewport ignores
            // it — renders with it true and false are pixel-identical — but
            // leaving it unmanaged means whatever is in a saved profile wins.
            function applyPins() {
                var changed = false;
                var d = api.conf.get().device;
                var spinGcode = %(spin_gcode)s;
                if (d && (d.bedWidth !== %(bed_w)d || d.bedDepth !== %(bed_d)d
                          || d.maxHeight !== %(max_z)d || d.originCenter !== false
                          || d.spindleMax !== %(spin_max)d
                          || d.gcodeProc !== %(gcode_proc)s
                          || JSON.stringify(d.gcodeSpindle || [])
                             !== JSON.stringify(spinGcode))) {
                    d.bedWidth = %(bed_w)d;
                    d.bedDepth = %(bed_d)d;
                    d.maxHeight = %(max_z)d;
                    d.originCenter = false;
                    // Both are needed: spindleMax gates the per-operation RPM
                    // field and the export branch, gcodeSpindle is what gets
                    // written. See _SPINDLE_MAX_RPM.
                    d.spindleMax = %(spin_max)d;
                    d.gcodeSpindle = spinGcode.slice();
                    // Kiri's export post processor: the comment header with
                    // tools, operations and ZMIN. See ui/cam_gcode_header.py.
                    d.gcodeProc = %(gcode_proc)s;
                    changed = true;
                }
                var pr = api.conf.get().process;
                var want = %(origin)s;
                if (pr) {
                    for (var k in want) {
                        if (pr[k] !== want[k]) { pr[k] = want[k]; changed = true; }
                    }
                }
                if (!changed) return;

                api.conf.save();
                // update_fields() is not cosmetic, and it has to cover the
                // process pins as well as the device ones. Fields are written
                // from the records at specific moments — setDeviceCode() does it
                // once per device selection — and pinning a value behind the
                // panel's back leaves the field showing the old one. That is not
                // a display bug for long: the next click on any control runs
                // onBooleanClick -> api.conf.update() ->
                // updateSettingsFromFields(process), which reads the stale
                // FIELD back into the setting and undoes the pin.
                //
                // Measured before this was hoisted out of the device branch:
                // camFastFeed 500 in the settings with "6000" in the field, then
                // 6000 in both after a single click anywhere in the panel.
                if (api.conf.update_fields) api.conf.update_fields();
                if (api.platform.update_size) api.platform.update_size();
                if (api.platform.update_bounds) api.platform.update_bounds();
            }

            applyPins();

            // Show the G-code in the export dialog by default. Kiri ships
            // controller.exportPreview false, which hides its "gcode preview"
            // pane (#code-preview, export.js) -- so a fresh profile on one
            // machine had it and another did not, depending on whether someone
            // had ticked Setup > "export preview". SEEDED once per profile, not
            // pinned: unticking it in Setup still sticks.
            try {
                if (!localStorage.getItem('piccola-export-preview-seed')) {
                    var ctl = api.conf.get().controller;
                    if (ctl && ctl.exportPreview !== true) {
                        ctl.exportPreview = true;
                        api.conf.save();
                        if (api.ui && api.ui.exportPreview) api.ui.exportPreview.checked = true;
                    }
                    localStorage.setItem('piccola-export-preview-seed', '1');
                }
            } catch (e) { }

            // Pinning once at load is not enough. Opening Setup > Machines runs
            // showDevices() -> renderDevices() -> setDeviceCode(), and that does
            //
            //     dev = current.device = conf.device_from_code(code, mode)
            //
            // i.e. it REPLACES the live device record with a fresh one parsed
            // from the shipped profile. Measured: bed 150/150/70 before opening
            // the dialog, 400/400/150 after — so every toolpath computed after
            // a look at that dialog used a 400 mm bed until the next reload.
            // The process pins survive it (measured), but they are re-applied
            // here anyway, since a device bound to a named profile would take
            // the setconf.load() branch and swap the whole process record.
            //
            // 'device.selected' is emitted at the end of setDeviceCode, so
            // re-pinning there lands after the replacement, whatever caused it
            // — dialog, dropdown or first-run selection. None of the calls in
            // applyPins() emit it back, so this cannot recurse.
            if (api.event && api.event.on && !window.__piccolaPinned) {
                window.__piccolaPinned = true;
                api.event.on('device.selected', applyPins);
                // And on 'settings.saved', which is what an *import* ends up
                // emitting. `settingsImport` replaces the whole settings
                // object wholesale -- `settings = normalize(data.settings)`,
                // straight into localStorage -- and emits no device event, so
                // a workspace exported from another machine put its bed size,
                // spindle ceiling and feed limits into force until the next
                // launch. Those are the fields most worth not inheriting from
                // somewhere else.
                //
                // Safe to hook despite applyPins() calling api.conf.save():
                // the second pass finds nothing to change and returns before
                // saving again, so the loop is one deep and then stops.
                api.event.on('settings.saved', applyPins);
            }
        } catch (e) { /* leave the device alone if the API moved */ }
        (function tidy(tries) {""" % {
        "grbl": _GRBL_DEVICE,
        "bed_w": _WORK_AREA_MM[0],
        "bed_d": _WORK_AREA_MM[1],
        "max_z": _MAX_Z_MM,
        "origin": json.dumps({**_ORIGIN_DEFAULTS, **_KIRI_PROCESS_DEFAULTS}),
        "spin_max": _SPINDLE_MAX_RPM,
        "spin_gcode": json.dumps(_SPINDLE_GCODE),
        "gcode_proc": json.dumps(cam_gcode_header.PROC_NAME),
    } + """
            var vis = function (el) { return !!(el && (el.offsetWidth || el.offsetHeight)); };
            var gdpr = document.getElementById('gdpr');
            var gotit = document.getElementById('gotit');
            if (vis(gdpr) && gotit) { gotit.click(); }
            var help = document.getElementById('mod-help');
            var close = document.getElementById('mod-x');
            if (vis(help) && close) { close.click(); }
            if (tries > 0) setTimeout(function () { tidy(tries - 1); }, 300);
        })(30);
    """,
    # EasyTrace: pin the settings whose controls _EASYTRACE_HIDE_CSS removes —
    # post-processor, coolant/vacuum, toolpath optimisation — so a value saved
    # (or shipped) before the control disappeared cannot stick with no way to
    # change it back. Also drops the stencil file type, the half of the stencil
    # removal that CSS cannot do.
    "pcb": """
        try {
            // Post-processor, start/end code and units: every setting behind a
            // control _EASYTRACE_HIDE_CSS removes from the CNC Output section.
            // null start/end code means "the post-processor's own default",
            // which is what resolveStartCode()/resolveEndCode() fall back to.
            var g = easytrace.core.settings.gcode || {};
            var wantGcode = %(gcode)s;
            // undefined / null / '' all mean the same thing to the resolver, so
            // treat them as equal — otherwise a never-set field would look
            // different from null and rewrite storage on every launch.
            var unset = function (v) {
                return v === undefined || v === null || v === '';
            };
            var changed = false;
            for (var k in wantGcode) {
                var have = g[k], want = wantGcode[k];
                if (unset(have) && unset(want)) continue;
                if (have !== want) { changed = true; }
            }
            if (changed) easytrace.core.updateSettings('gcode', wantGcode);
            var m = easytrace.core.settings.machine || {};
            if (m.coolant !== '%(coolant)s' || m.vacuum !== %(vacuum)s) {
                easytrace.core.updateSettings('machine', {
                    coolant: '%(coolant)s', vacuum: %(vacuum)s });
            }
            // Rapid feed: the machine's traverse rate. Both spellings are set —
            // the field's key and the one the pipeline reads. See
            // _EASYTRACE_RAPID_FEED_MM_MIN.
            var sp = (easytrace.core.settings.machine || {}).speeds || {};
            var rapid = %(rapid)s;
            if (sp.rapidFeed !== rapid || sp.rapidFeedRate !== rapid) {
                var speeds = {};
                for (var s in sp) { speeds[s] = sp[s]; }
                speeds.rapidFeed = rapid;
                speeds.rapidFeedRate = rapid;
                easytrace.core.updateSettings('machine', { speeds: speeds });
            }
            // The Machine Settings panel fills its fields during UI init, which
            // can run before this, so the field would keep showing the old
            // number while the setting behind it had moved.
            var rf = document.getElementById('rapid-feed');
            if (rf && parseFloat(rf.value) !== rapid) rf.value = rapid;
        } catch (e) { /* leave it alone if the API moved */ }
        try {
            // Parameter defaults that belong to this machine rather than to the
            // endmill the library happens to suggest. getDefaults() is what
            // feeds the parameter panel when an operation is first loaded, and
            // it outranks the parameter definitions' own defaults
            // (loadFromOperation: live state, then saved settings, then these,
            // then def.default) — so this sets what every operation starts with
            // while leaving anything already saved alone. Patched on the
            // prototype, so instances already built pick it up too.
            // See _EASYTRACE_SPINDLE_RPM and _EASYTRACE_SINGLE_PASS_OPS.
            var PM = window.ParameterManager;
            if (PM && PM.prototype && !PM.__piccolaDefaults) {
                var origDefaults = PM.prototype.getDefaults;
                var singlePass = %(single_pass)s;
                PM.prototype.getDefaults = function (operationType) {
                    var d = origDefaults.apply(this, arguments) || {};
                    d.spindleSpeed = %(spindle)d;
                    if (singlePass.indexOf(operationType) !== -1) {
                        d.multiDepth = false;
                    }
                    return d;
                };
                PM.__piccolaDefaults = true;
            }
        } catch (e) { /* the tool's and the profile's own defaults still apply */ }
        try {
            // Stencil is hidden from the operations tree; drop its file type so
            // a dropped .gtp/.gbp cannot create an operation with no visible
            // node. setFileTypes() runs once from the app profile during init,
            // well before this, so nothing puts it back.
            if (easytrace.core.fileTypes) {
                delete easytrace.core.fileTypes['%(stencil)s'];
            }
        } catch (e) { /* the drop would just be typed as before */ }
        try {
            // Pin "Optimize toolpaths" now that its checkbox is hidden. The
            // export reads the element directly, so this is the value.
            var opt = document.getElementById('exporter-optimize-paths');
            if (opt) opt.checked = %(optimize)s;
            var cmt = document.getElementById('exporter-include-comments');
            if (cmt) cmt.checked = %(comments)s;
        } catch (e) { /* markup moved — the export falls back to true */ }
        try {
            // Origin Offset arrows: 1 mm a click instead of upstream's 0.01.
            ['x-offset', 'y-offset'].forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.step = '%(offset_step)s';
            });
        } catch (e) { /* the arrows keep the tool's own step */ }
        // Stop the operations tree's onboarding highlight loop. The panel is
        // built during UI init, which can land after this preset, so retry
        // briefly rather than firing once — the loop itself only arms 12.5 s
        // after the first file, so there is time in hand either way.
        (function dismissOnboarding(tries) {
            try {
                var panel = easytrace.getUI && easytrace.getUI().navTreePanel;
                if (panel && panel.dismissSuggestion) {
                    panel.dismissSuggestion();
                    return;
                }
            } catch (e) { /* fall through to the retry */ }
            if (tries > 0) {
                setTimeout(function () { dismissOnboarding(tries - 1); },
                           %(onboard_ms)d);
            }
        })(%(onboard_tries)d);
    """ % {"gcode": json.dumps({
               "postProcessor": _EASYTRACE_POST_PROCESSOR,
               "userStartCode": _EASYTRACE_START_CODE,
               "userEndCode": _EASYTRACE_END_CODE,
               "units": _EASYTRACE_UNITS,
           }),
           "coolant": _EASYTRACE_COOLANT,
           "vacuum": "true" if _EASYTRACE_VACUUM else "false",
           "rapid": json.dumps(_EASYTRACE_RAPID_FEED_MM_MIN),
           "spindle": _EASYTRACE_SPINDLE_RPM,
           "single_pass": json.dumps(list(_EASYTRACE_SINGLE_PASS_OPS)),
           "stencil": _EASYTRACE_STENCIL_OP,
           "optimize": "true" if _EASYTRACE_OPTIMIZE_PATHS else "false",
           "comments": "true" if _EASYTRACE_INCLUDE_COMMENTS else "false",
           "offset_step": _EASYTRACE_OFFSET_STEP_MM,
           "onboard_tries": _EASYTRACE_ONBOARDING_TRIES,
           "onboard_ms": _EASYTRACE_ONBOARDING_INTERVAL_MS},
}
# Readiness probe per mode: the exact API each preset calls. Both tools publish
# their global before it is fully wired, so probing the namespace alone would
# fire the preset too early.
_PRESET_READY = {
    "carve": "!!(window.kiri && kiri.api && kiri.api.mode && kiri.api.mode.set)",
    "pcb": "!!(window.easytrace && easytrace.core"
           " && easytrace.core.settings && easytrace.core.updateSettings)",
}

# The globals appear asynchronously after load, so retry briefly rather than
# firing once into an empty window.
_PRESET_TRIES = 20
_PRESET_INTERVAL_MS = 400

#: "Kiri:Moto is Loading" is a curtain (`#curtain`) hidden by the *last*
#: statement of `init_sync()`, so anything that throws on the way there leaves
#: it up for good. The usual culprit is the renderer:
#:
#:     THREE.WebGLRenderer: A WebGL context could not be created.
#:       Reason: Web page caused context loss and was blocked
#:     Uncaught TypeError: Cannot read properties of undefined (reading 'scale')
#:
#: — seen repeatedly on this machine while another GL application held the GPU.
#: Kiri has no recovery for it, so the tab sits on the splash indefinitely.
#:
#: Context loss is usually transient, so a stuck load is reloaded once before
#: giving up and saying so. The probe is the curtain rather than
#: `window.kiri.api`, which appears long before init finishes.
_LOAD_DONE_PROBE = {
    "carve": "(function () {"
             " try {"
             "  var c = document.getElementById('curtain');"
             "  return !!(window.kiri && window.kiri.api) &&"
             "         (!c || getComputedStyle(c).display === 'none');"
             " } catch (e) { return false; }"
             "})()",
}

#: Long enough to cover a cold start on a slow machine — a false alarm here
#: reloads a tab that was merely still working.
_LOAD_WATCH_TRIES = 30
_LOAD_WATCH_MS = 500

#: Whether this Chromium can create a WebGL context at all, asked once a tool is
#: known to be stuck. Tells "the GPU refused" apart from any other init failure.
#: Answers with the renderer name (which driver Chromium actually got, for the
#: load report) or "" when there is no context, so it is still falsy for "no
#: WebGL".
_WEBGL_PROBE = ("(function () { try { var c = document.createElement('canvas');"
                " var g = c.getContext('webgl2') || c.getContext('webgl');"
                " if (!g) return '';"
                " var d = g.getExtension('WEBGL_debug_renderer_info');"
                " return (d && g.getParameter(d.UNMASKED_RENDERER_WEBGL))"
                "        || g.getParameter(g.RENDERER) || 'available';"
                " } catch (e) { return ''; } })()")


def _load_report_path() -> str:
    base = QStandardPaths.writableLocation(
        QStandardPaths.AppLocalDataLocation) or os.path.expanduser("~/.piccola-cnc")
    return os.path.join(base, "cam", "load-failure.txt")


def _write_load_report(tool: str, webgl, safe: bool, errs: list) -> None:
    """Keep what a stuck load knew, for a user who cannot read a status line
    to someone over the phone. Best effort: never raises.

    The whole text is built before the file is opened and written in one go,
    then flushed to disk. A report from an AMD laptop came back as an empty
    file: opening truncates, and if the process dies before the buffered
    lines reach disk (a GPU driver taking the app down with it), nothing of
    what was known survives. Each field is also guarded on its own so one
    bad value cannot cost the rest.
    """
    def field(name, get):
        try:
            return f"{name}: {get()}"
        except Exception as e:   # noqa: BLE001 -- say why, keep going
            return f"{name}: <{type(e).__name__}: {e}>"

    def chromium():
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
        return qWebEngineChromiumVersion()

    lines = [
        field("time", lambda: datetime.now().isoformat(timespec="seconds")),
        field("tool", lambda: tool),
        field("chromium", chromium),
        field("flags", lambda: os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")),
        field("webgl", lambda: webgl or "none"),
        field("compatibility graphics", lambda: safe),
        "console errors:",
    ]
    lines += [f"  {e!s}" for e in errs] or ["  (none)"]
    text = "\n".join(lines) + "\n"
    try:
        path = _load_report_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


#: Extensions that mean "this download is a saved profile, not a program".
#: Kiri's *Workspace Export* writes `.kmz` (a zip around `workspace.json`) and
#: its older single-file form is `.km`. Deliberately a whitelist of profile
#: types rather than a blacklist of program types: a new G-code extension
#: appearing must not silently start opening a save dialog mid-job.
_PROFILE_EXTS = (".kmz", ".km")

#: Extensions that mean "this download is a model, not a program". *Save As
#: OBJ* and *Save As STL* (File menu -> `conf/settings.js export_objects`) hand
#: a mesh to `api.util.download`, the same door G-code leaves by, and everything
#: through that door was being treated as a program: filed under a timestamped
#: `carve-*` folder inside the app's private data directory, and then dropped,
#: because `_flush_batch` only offers files whose extension is in _GCODE_EXT.
#: The export worked -- the files were written, intact and complete -- but
#: nothing ever told the operator where, and the folder is one no one has a
#: reason to open. That is indistinguishable from "saving the model does not
#: work", which is exactly how it was reported.
#:
#: Whitelisted for the same reason as _PROFILE_EXTS: a new G-code extension
#: appearing upstream must not start opening a save dialog in the middle of a
#: job. `.3mf` is here because Kiri's export dialog can write one too.
_MODEL_EXTS = (".stl", ".obj", ".3mf")


def _tool_library_dir() -> str:
    """Where tool libraries are saved by default: Documents/Piccola CNC/Tool libraries."""
    docs = (QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
            or os.path.expanduser("~"))
    path = os.path.join(docs, "Piccola CNC", "Tool libraries")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return docs
    return path


def _safe_filename(name: str, fallback: str = "cam-export.nc") -> str:
    """Strip anything path-like or shell-hostile out of a suggested filename.

    The name comes from inside the webview, so it is untrusted: collapse it to a
    basename and allow only a conservative character set.
    """
    name = os.path.basename(name or "").strip()
    name = _SAFE_NAME.sub("_", name).lstrip(".")
    return name or fallback


def _mode_btn(text: str, active: bool = False) -> QPushButton:
    """Segmented-control button, styled like the step/speed toggles elsewhere."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(active)
    b.setCursor(QCursor(Qt.PointingHandCursor))

    def style(x):
        x.setMinimumWidth(px(96))
        x.setFixedHeight(px(34))
        x.setStyleSheet(f"""
            QPushButton {{
                background: {LIGHT}; border-radius: {px(8)}px;
                border: 1px solid {BORDER}; font-size: {fs(14)}px;
                padding: 0 {px(14)}px;
            }}
            QPushButton:checked {{
                background: {WHITE}; border: 1.5px solid #999;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: #E8E8E8; }}
        """)

    return autoscale(b, style)


def _edit_menu(view, pos) -> None:
    """Right-click menu of a CAM web view: editing only, in the app's look.

    QtWebEngine's own menu is a browser's -- Back, Forward, Reload, Save page,
    View page source -- and was drawn as a black box: it is a top-level window
    styled by the pages' ``background: transparent`` sheets (see
    ui/tooltips.py), and a transparent top-level window is painted black.
    Worse than unreadable, Reload throws away whatever is open in the tool.
    So a text field gets Undo / Cut / Copy / Paste / Select all, selected text
    gets Copy, and anywhere else no menu (Kiri's 3-D view uses the right
    button itself).
    """
    from PySide6.QtWebEngineCore import QWebEnginePage
    req = view.lastContextMenuRequest()
    if req is None:
        return
    A = QWebEnginePage.WebAction
    if req.isContentEditable():
        groups = [(A.Undo, A.Redo), (A.Cut, A.Copy, A.Paste), (A.SelectAll,)]
    elif req.selectedText():
        groups = [(A.Copy,)]
    else:
        return
    from PySide6.QtWidgets import QMenu
    menu = QMenu(view)
    menu.setStyleSheet(f"""
        QMenu {{ background: {WHITE}; border: 1px solid {BORDER}; padding: {px(4)}px; }}
        QMenu::item {{ padding: {px(6)}px {px(22)}px; font-size: {fs(13)}px; color: {TXT}; }}
        QMenu::item:selected {{ background: {LIGHT}; }}
        QMenu::item:disabled {{ color: {TXT2}; }}
        QMenu::separator {{ height: 1px; background: {BORDER}; margin: {px(4)}px {px(6)}px; }}
    """)
    page = view.page()
    for i, group in enumerate(groups):
        if i:
            menu.addSeparator()
        for act in group:
            menu.addAction(page.action(act))
    menu.exec(view.mapToGlobal(pos))
    menu.deleteLater()


def _make_page_class():
    """Build the per-tool QWebEnginePage subclass.

    A factory rather than a module-level subclass so importing this module never
    pulls in QtWebEngineCore: that import must happen before QApplication is
    constructed (see main.py), while pages are built lazily long afterwards.
    """
    from PySide6.QtWebEngineCore import QWebEnginePage

    class ToolPage(QWebEnginePage):
        """Keeps a tool inside its own bundle.

        Both tools carry links that lead out of the workspace — EasyTrace's
        header has a ``/`` home link to the suite landing page, and both have
        docs/GitHub links.  Following those inside the CAM tab would strand the
        user in an embedded browser with no way back, so:

        * navigation within the bundle's own origin *and* below its nav base is
          allowed (each bundle has its own port, so the port check alone
          establishes "this tool"),
        * anything else http(s) opens in the real browser,
        * everything else is refused.
        """

        def __init__(self, profile, parent, port: int, base_path: str):
            super().__init__(profile, parent)
            self._port = port
            self._base_path = base_path
            #: Last few console errors, kept so a stuck load can say why it is
            #: stuck. Without this the tab reports "still loading" and the real
            #: message — a WebGL context failure, a blocked resource — is only
            #: visible to someone running the app from a terminal.
            self.piccola_errors = []

        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
            try:
                from PySide6.QtWebEngineCore import QWebEnginePage as _P
                if level == _P.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                    self.piccola_errors.append(str(message))
                    del self.piccola_errors[:-5]
            except Exception:
                pass

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
            if not is_main_frame:
                return True
            own_origin = (url.host() in ("127.0.0.1", "localhost")
                          and url.port() == self._port)
            if own_origin and url.path().startswith(self._base_path):
                return True
            if url.scheme() in ("http", "https") and not own_origin:
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(url)   # docs / GitHub → system browser
            return False

    return ToolPage


class _MissingBundle(QFrame):
    """Shown instead of a webview when a vendor bundle has not been built.

    Kiri:Moto publishes no static web build (its releases are Electron
    installers), so ``vendor/kirimoto/`` is produced by the npm pipeline in
    ``tools/fetch_vendor.py``.  A blank webview would look like a bug; this says
    what to run.
    """

    def __init__(self, tool: str, bundle: str):
        super().__init__()
        self._tool, self._bundle = tool, bundle
        self.setObjectName("missing")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)

        self._title = QLabel(f"{tool} is not vendored yet")
        self._title.setAlignment(Qt.AlignCenter)
        self._body = QLabel(
            f"vendor/{bundle}/ is empty or has no index.html.\n\n"
            f"Fetch and build it with:\n"
            f"    python3 tools/fetch_vendor.py {bundle}\n\n"
            f"Then reopen this tab."
        )
        self._body.setAlignment(Qt.AlignCenter)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self._title)
        lay.addWidget(self._body)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setStyleSheet(
            f"#missing {{ background: {WHITE}; border: 1px solid {BORDER}; "
            f"border-radius: {px(10)}px; }}")
        self._title.setStyleSheet(
            f"font-size: {fs('h2')}px; font-weight: bold; color: {TXT}; "
            f"background: transparent;")
        self._body.setStyleSheet(
            f"font-size: {fs('body')}px; color: {TXT2}; background: transparent;")


class _CornerCover(QWidget):
    """Paints the four corner slivers over whatever sits beneath it.

    The shape drawn is (bounding rect − rounded rect), filled with the colour of
    the surface *behind* the panel, using ordinary antialiased painting. That is
    the same thing a stylesheet ``border-radius`` does visually, except it works
    on top of a composited QWebEngineView.
    """

    def __init__(self, parent: "_RoundedHost"):
        super().__init__(parent)
        # Purely decorative: clicks must reach the CAM tool underneath.
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, _e):
        host = self.parent()
        outer = QPainterPath()
        outer.addRect(QRectF(self.rect()))
        inner = QPainterPath()
        inner.addRoundedRect(QRectF(self.rect()), host.radius, host.radius)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(host.backdrop))
        p.drawPath(outer.subtracted(inner))


class _RoundedHost(QFrame):
    """Container whose corners read as filleted even around a webview.

    A stylesheet ``border-radius`` is honoured only by Qt's own painting;
    ``QWebEngineView`` renders through a composited surface that ignores it, so
    the web content stays square and cuts through the corners.

    Masking the widget does clip it, but a mask is a 1-bit region: the arc comes
    out stair-stepped, and building the region from
    ``QPainterPath.toFillPolygon()`` is worse still — it flattens the arc into a
    few segments, which at a 12 px radius reads as a 45° *chamfer*. So instead of
    clipping, we overpaint: an antialiased cover draws the backdrop colour into
    the corners, matching ConsolePage's rounded panel exactly.
    """

    def __init__(self, radius: int = 12, backdrop: str = WHITE):
        super().__init__()
        self.radius = radius
        self.backdrop = backdrop
        self._cover = _CornerCover(self)

    def set_radius(self, r: int) -> None:
        self.radius = r
        self._cover.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._cover.setGeometry(self.rect())
        self._cover.raise_()       # must stay above the stacked webviews
        self._cover.update()


class CamPage(QWidget):
    """Carve / PCB tabs over a lazily-created QWebEngineView each."""

    #: Emitted once an export batch settles: (absolute paths, destination).
    #: Destination is "machine" — the user asked for it to be sent — or "file",
    #: which CamPage handles itself and never emits.
    gcode_exported = Signal(list, str)

    def __init__(self, server=None):
        super().__init__()
        self._server = server
        self._conn = None
        self._views: dict[str, QWidget] = {}      # mode key -> view or placeholder
        self._index: dict[str, int] = {}          # mode key -> stack index
        self._mode = MODES[0][0]
        self._profile = None
        self._shown_once = False

        # Export batching. Destination, job name and staging folder are taken
        # once per batch: EasyTrace emits one file per tool, and every file of
        # one export shares them. `_batch_open` spans the whole batch, from the
        # first download *request* to the flush; `_batch` only holds the files
        # that have finished writing.
        self._batch: list[str] = []
        self._batch_open = False
        self._batch_intent = "file"
        self._batch_name = ""
        self._batch_dir: str | None = None
        self._batch_used: set[str] = set()
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(_BATCH_MS)
        self._batch_timer.timeout.connect(self._flush_batch)

        # Same shell as ConsolePage: a transparent page with a px(12) margin, so
        # the white rounded scroll viewport behind it reads as the page card,
        # and a single rounded content panel sitting on that. Note there is no
        # extra white card here — the viewport already provides it.
        self.setStyleSheet(TRANSPARENT)

        self._root = QVBoxLayout(self)

        # ── Native header: title + segmented mode switch + status ─────────────
        self._header = QFrame()
        self._header.setObjectName("camhead")
        head = QHBoxLayout(self._header)

        self._title = QLabel("CAM")
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: dict[str, QPushButton] = {}
        for i, (key, label, _bundle, _tool) in enumerate(MODES):
            b = _mode_btn(label, active=(i == 0))
            self._group.addButton(b, i)
            self._btns[key] = b
            b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        head.addWidget(self._title)
        for key, *_ in MODES:
            head.addWidget(self._btns[key])
        head.addStretch(1)
        head.addWidget(self._status)
        self._root.addWidget(self._header)

        # ── Stack of tool views ───────────────────────────────────────────────
        # _RoundedHost masks its children, which is what actually gives the
        # webview filleted corners — a stylesheet border-radius alone would not.
        self._view_wrap = _RoundedHost(px(12))
        self._view_wrap.setObjectName("camView")
        vw = QVBoxLayout(self._view_wrap)
        vw.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(TRANSPARENT)
        vw.addWidget(self._stack)
        self._root.addWidget(self._view_wrap, 1)

        bind(self, self._apply_scale)

    # ── Styling ──────────────────────────────────────────────────────────────

    def _apply_scale(self, *_):
        # Outer margin only — the white rounded card behind is the scroll
        # viewport (main_window._style_scroll), exactly as for ConsolePage.
        self._root.setContentsMargins(px(12), px(12), px(12), px(12))
        self._root.setSpacing(px(10))

        # px(12) matches ConsolePage's #consolePanel radius.
        self._view_wrap.set_radius(px(12))
        self._view_wrap.setStyleSheet(
            f"#camView {{ background: {LIGHT}; border: none; "
            f"border-radius: {px(12)}px; }}")

        self._header.setStyleSheet(
            "#camhead { background: transparent; border: none; }")
        lay = self._header.layout()
        lay.setContentsMargins(px(2), 0, px(2), 0)
        lay.setSpacing(px(8))
        self._title.setStyleSheet(
            f"font-size: {fs('h2')}px; font-weight: bold; color: {TXT}; "
            f"background: transparent;")
        self._status.setStyleSheet(
            f"font-size: {fs('small')}px; color: {TXT2}; background: transparent;")

    # ── Wiring ───────────────────────────────────────────────────────────────

    def wire(self, conn) -> None:
        """Match the other pages' wiring contract.

        The CAM tools never touch the machine themselves — ``conn`` is kept only
        so the export prompt can say whether a machine is actually connected.
        """
        self._conn = conn

    def set_server(self, server) -> None:
        self._server = server

    # ── Lazy view construction ───────────────────────────────────────────────

    def showEvent(self, e):
        super().showEvent(e)
        # First paint of the page: build only the active mode's view.
        if not self._shown_once:
            self._shown_once = True
            QTimer.singleShot(0, lambda: self._ensure_view(self._mode))
        e = e  # keep signature explicit for readers

    def _ensure_profile(self):
        """One shared persistent profile for both tools.

        Named (not off-the-record) so each tool's localStorage — machine
        parameters, tool tables, UI preferences — survives an app restart.
        """
        if self._profile is not None:
            return self._profile
        from PySide6.QtWebEngineCore import QWebEngineProfile

        base = QStandardPaths.writableLocation(
            QStandardPaths.AppLocalDataLocation) or os.path.expanduser("~/.piccola-cnc")
        prof = QWebEngineProfile("piccola-cam", self)
        prof.setPersistentStoragePath(os.path.join(base, "cam", "storage"))
        prof.setCachePath(os.path.join(base, "cam", "cache"))
        prof.setDownloadPath(self._export_root())
        prof.downloadRequested.connect(self._on_download)
        self._profile = prof
        return prof

    def _ensure_view(self, key: str) -> None:
        if key in self._views:
            return
        bundle = next(b for k, _l, b, _t in MODES if k == key)
        tool = next(t for k, _l, _b, t in MODES if k == key)

        if self._server is None or not self._server.has_bundle(bundle):
            w = _MissingBundle(tool, bundle)
        else:
            w = self._build_webview(key,
                                    self._server.url_for(bundle),
                                    self._server.port_for(bundle),
                                    self._server.base_path(bundle))

        self._views[key] = w
        self._index[key] = self._stack.addWidget(w)
        if key == self._mode:
            self._stack.setCurrentIndex(self._index[key])
            self._update_status()

    def _build_webview(self, key: str, url: str, port: int, base_path: str):
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineScript

        view = QWebEngineView()
        page = _make_page_class()(self._ensure_profile(), view, port, base_path)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda pos, v=view: _edit_menu(v, pos))

        init_js = _init_js(key, self._server)
        if init_js:
            # Attached to the *page*, not the profile: both tools share one
            # profile, and this must only ever touch its own tool's storage.
            script = QWebEngineScript()
            script.setName(f"piccola-init-{key}")
            script.setSourceCode(init_js)
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            page.scripts().insert(script)

        s = page.settings()
        # Both tools are local-origin and need workers + local storage; they do
        # not need to reach out to the network or read arbitrary local files.
        s.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, False)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)

        view.setUrl(QUrl(url))
        view.loadFinished.connect(self._on_load_finished)
        # The page's renderer is a separate process; when it dies (seen: "V8
        # javascript OOM" generating a job on a 2M-vertex model) the view shows
        # nothing and never recovers on its own -- the operator saw a blank
        # CAM tab. Reload it, deferred: Qt forbids a reload from inside this
        # signal. The tool restores its workspace from its own storage.
        page.renderProcessTerminated.connect(
            lambda status, code, k=key, v=view: self._on_render_crash(k, v, status, code))
        return view

    def _on_render_crash(self, key: str, view, status, code) -> None:
        if not self._still_live(key, view):
            return
        tool = next(t for k, _l, _b, t in MODES if k == key)
        try:
            name = status.name
        except AttributeError:
            name = str(status)
        why = ("out of memory" if "Killed" in name or "Crash" in name else name)
        self._status.setText(f"{tool} \u00b7 page {why} (exit {code}), reloading")
        print(f"[cam] {tool} renderer terminated: {name} code={code}; reloading",
              flush=True)
        QTimer.singleShot(0, lambda: self._still_live(key, view) and view.reload())

    # ── Presets ──────────────────────────────────────────────────────────────

    def _still_live(self, key: str, view) -> bool:
        """True if *view* is still the live view for *key*.

        The probes below answer asynchronously (runJavaScript callbacks, retry
        timers). If the window is closed while one is pending -- typically while
        a tab is stuck on its splash -- `shutdown` has already deleted the view,
        and touching it raises "Internal C++ object already deleted" out of the
        event loop, which a frozen build reports as a crash on exit.
        """
        return (self._views.get(key) is view
                and shiboken6.isValid(view)
                and shiboken6.isValid(self._status))

    def _apply_preset(self, key: str, tries: int = _PRESET_TRIES) -> None:
        """Drive a freshly loaded tool into the state Piccola wants.

        Retried because the tools populate their globals after ``loadFinished``;
        gives up quietly rather than nagging if the API never shows up (an
        upstream rename should degrade to "wrong default mode", not an error).
        """
        js = _PRESET_JS.get(key)
        view = self._views.get(key)
        if not js or view is None or not hasattr(view, "page"):
            return

        def _check(ready) -> None:
            if not self._still_live(key, view):
                return
            if ready:
                view.page().runJavaScript(js)
            elif tries > 0:
                QTimer.singleShot(
                    _PRESET_INTERVAL_MS,
                    lambda: self._apply_preset(key, tries - 1))

        # Probe for the specific API the preset uses, not just the global —
        # both tools publish their namespace before it is fully wired up.
        probe = _PRESET_READY.get(key)
        if not probe:
            return
        view.page().runJavaScript(probe, _check)

    def _report_module_failures(self, key: str, tries: int = 3) -> None:
        """Say, in the status line, if an injected module failed to install.

        `_isolate_modules` catches a module that throws and records it on
        `window.__piccolaFailed` so one failure cannot take out the rest. That
        is only half the value: unreported, the result is still a feature that
        quietly does not exist. This reads the list back and names it, which
        turns "the marker is not red" into "origin-marker threw: <reason>".

        Retried a couple of times because a module can also throw later, from
        one of its own retry timers, where the wrapper cannot catch it — those
        arrive as page console errors instead and are picked up here too.
        """
        view = self._views.get(key)
        if view is None or not hasattr(view, "page"):
            return

        def _check(raw) -> None:
            if not self._still_live(key, view):
                return
            names = []
            try:
                for item in json.loads(raw or "[]"):
                    if isinstance(item, list) and item:
                        names.append(str(item[0]))
            except Exception:
                pass
            if not names:
                errs = list(getattr(view.page(), "piccola_errors", []))
                if errs and tries <= 0:
                    self._status.setText(f"page error: {errs[-1][:120]}")
                elif tries > 0:
                    QTimer.singleShot(
                        2000,
                        lambda: self._report_module_failures(key, tries - 1))
                return
            joined = ", ".join(sorted(set(names)))
            self._status.setText(f"injected module failed: {joined}")

        view.page().runJavaScript(
            "JSON.stringify(window.__piccolaFailed || [])", _check)

    def _watch_load(self, key: str, tries: int = _LOAD_WATCH_TRIES,
                    reloaded: bool = False) -> None:
        """Notice a tool that never finishes loading, and give it one more go.

        See :data:`_LOAD_DONE_PROBE` for why this is needed at all: the splash
        is hidden by the last line of Kiri's init, so a throw anywhere before it
        leaves the tab on "Kiri:Moto is Loading" with nothing to click.
        """
        probe = _LOAD_DONE_PROBE.get(key)
        view = self._views.get(key)
        if not probe or view is None or not hasattr(view, "page"):
            return

        def _check(done) -> None:
            if done or not self._still_live(key, view):
                return
            if tries > 0:
                QTimer.singleShot(
                    _LOAD_WATCH_MS,
                    lambda: self._watch_load(key, tries - 1, reloaded))
                return
            # No WebGL at all (blocklisted GPU or driver) is permanent for this
            # run, so a reload would only add another wait before the offer.
            view.page().runJavaScript(
                _WEBGL_PROBE, lambda gl: _stuck(gl))

        def _stuck(webgl) -> None:
            if not self._still_live(key, view):
                return
            if not webgl or reloaded:
                self._load_failed(key, view, webgl)
                return
            tool = next(t for k, _l, _b, t in MODES if k == key)
            # A lost GL context is usually transient, and a reload gets a
            # fresh one. Only once: a reload loop on a machine that cannot
            # give the tab a context at all would be worse than a message.
            #
            # Clear the HTTP cache first: files cached by an older build
            # (before responses were sent no-store) would otherwise mix two
            # Kiri versions in one page, which throws during init.
            self._status.setText(f"{tool} · did not finish loading, retrying")
            if self._profile is not None:
                try:
                    self._profile.clearHttpCache()
                except Exception:
                    pass
            view.reload()
            QTimer.singleShot(
                _LOAD_WATCH_MS,
                lambda: self._watch_load(key, _LOAD_WATCH_TRIES, True))

        view.page().runJavaScript(probe, _check)

    def _load_failed(self, key: str, view, webgl) -> None:
        """A tool is stuck on its splash (no WebGL, or still after a reload): say
        why, offer a fix.

        Most of the time the cause is graphics -- no WebGL context on this GPU
        or driver -- which the page cannot fix from inside. Compatibility
        graphics (gpu_mode.py) can, but Chromium only reads that at startup, so
        it takes a restart. Asked rather than done, because it makes the 3D
        view noticeably slower on a machine where the failure was a one-off.
        """
        if not self._still_live(key, view):
            return
        import gpu_mode
        from ui.pages.dialogs import ConfirmDialog, InfoDialog

        tool = next(t for k, _l, _b, t in MODES if k == key)
        errs = list(getattr(view.page(), "piccola_errors", []))
        why = f" ({errs[-1][:120]})" if errs else ""
        self._status.setText(f"{tool} · did not finish loading{why}")
        _write_load_report(tool, webgl, gpu_mode.safe_graphics(), errs)

        if gpu_mode.safe_graphics():
            InfoDialog(
                f"{tool} could not start",
                "It did not start in software rendering either.\n\n"
                f"Details were saved to:\n{_load_report_path()}\n\n"
                "Please send that file to InMachines support.",
                parent=self.window(), success=False).exec()
            return
        reason = ("3D graphics (WebGL) are not available with this computer's "
                  "graphics driver." if not webgl else
                  "It stopped while starting up.")
        if not ConfirmDialog(
                f"{tool} could not start",
                f"{reason}\n\nRestart with software rendering? The 3D view "
                "is slower, but it works on most computers. You can switch "
                "back to the GPU in the menu under CAM graphics.",
                parent=self.window(), yes_label="Restart",
                no_label="Not now").exec():
            return
        if not gpu_mode.set_safe_graphics(True):
            self._status.setText(f"{tool} · could not save the graphics setting")
            return
        gpu_mode.request_restart()
        win = self.window()
        if win is not None:
            win.close()

    # ── Mode switching ───────────────────────────────────────────────────────

    def set_mode(self, key: str) -> None:
        if key not in self._btns:
            return
        self._mode = key
        for k, b in self._btns.items():
            b.setChecked(k == key)
        self._ensure_view(key)
        self._stack.setCurrentIndex(self._index[key])
        self._update_status()

    def current_mode(self) -> str:
        return self._mode

    def _update_status(self) -> None:
        tool = next(t for k, _l, _b, t in MODES if k == self._mode)
        bundle = next(b for k, _l, b, _t in MODES if k == self._mode)
        # Tool, licence and author — see CREDITS. The loopback port used to be
        # shown here; it is an implementation detail and read as debug output.
        if self._server is not None and self._server.has_bundle(bundle):
            lic, author = CREDITS.get(self._mode, ("", ""))
            parts = [p for p in (tool, lic, author) if p]
            self._status.setText(" · ".join(parts))
        else:
            self._status.setText(f"{tool} · not vendored")

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            tool = next(t for k, _l, _b, t in MODES if k == self._mode)
            self._status.setText(f"{tool} · failed to load")
            return
        # Identify which view finished — loadFinished carries no sender context
        # we can rely on across modes.
        sender = self.sender()
        for key, view in self._views.items():
            if view is sender:
                self._apply_preset(key)
                self._watch_load(key)
                self._report_module_failures(key)
                break

    # ── Export interception ──────────────────────────────────────────────────

    def export_root(self) -> str:
        """Directory CAM exports are staged in. Public so main_window can
        verify a path lies inside it before deleting."""
        return self._export_root()

    def _save_download_as(self, item, title: str, filt: str,
                          fallback: str, label: str,
                          start_dir: str | None = None) -> bool:
        """Ask where to put *item*, then accept it there. Always True.

        The two callers below are the downloads that are not programs. Neither
        belongs in the export staging folder: that folder exists so a batch of
        G-code can be collected, renamed to the job name and handed to the
        machine, and none of those three things should happen to a profile or a
        model. So they are asked about instead of filed -- the destination is
        genuinely the operator's business, because they are keeping the file
        rather than running it.

        The dialog is modal and returns before this method does, which is what
        makes this safe: Qt cancels a download whose handler returns without
        calling accept(), and here accept() still happens inside the same call.
        """
        from PySide6.QtWidgets import QFileDialog

        name = item.suggestedFileName() or item.downloadFileName() or ""
        start = os.path.join(
            start_dir or
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation) or
            os.path.expanduser("~"),
            _safe_filename(name, fallback))
        target, _ = QFileDialog.getSaveFileName(
            self, title, start, filt + ";;All files (*)")
        if not target:
            # Cancelling the dialog cancels the download. Accepting it into a
            # scratch directory instead would leave a file nobody asked for.
            item.cancel()
            return True

        item.setDownloadDirectory(os.path.dirname(target) or ".")
        item.setDownloadFileName(os.path.basename(target))
        item.accept()

        def _done(state) -> None:
            from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self._status.setText(f"{label} saved: {target}")
            elif state in (QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
                           QWebEngineDownloadRequest.DownloadState.DownloadInterrupted):
                self._status.setText(
                    f"{label} save failed: {item.interruptReasonString()}")

        try:
            item.stateChanged.connect(_done)
        except Exception:
            pass
        return True

    def _save_model_download(self, item) -> bool:
        """Handle a model export (*Save As OBJ* / *Save As STL*), if it is one.

        Returns True if the download was ours to deal with, so the caller can
        stop. See :data:`_MODEL_EXTS` for why this exists.
        """
        ext = os.path.splitext(
            item.suggestedFileName() or item.downloadFileName() or "")[1].lower()
        if ext not in _MODEL_EXTS:
            return False
        # The filter names the format actually being written rather than all of
        # them, so the dialog cannot suggest saving an STL as ".obj" -- the
        # bytes are already encoded by the time this runs and renaming them
        # would produce a file that lies about itself.
        return self._save_download_as(
            item, "Save model", f"{ext[1:].upper()} model (*{ext})",
            f"model{ext}", "Model")

    def _save_profile_download(self, item) -> bool:
        """Handle a settings/profile download, if that is what this is.

        Kiri's *Workspace Export* (`conf/settings.js export_profile`) zips the
        settings, devices and workspace into a `.kmz` and hands it to
        `api.util.download` — the same door G-code leaves by. Everything through
        that door was being treated as a program: filed under a timestamped
        `carve-*` export folder, renamed to the batch's job name, and offered to
        the *machine* if the last export intent said so. A backup of your
        settings is none of those things, which is why saving a profile appeared
        not to work — the file existed, under another name, somewhere the
        operator had no reason to look.

        So a profile is asked about instead of filed. It is the one export whose
        destination is genuinely the operator's business: they are keeping it,
        not running it.

        Returns True if the download was ours to deal with, so the caller can
        stop.
        """
        name = item.suggestedFileName() or item.downloadFileName() or ""
        if os.path.splitext(name)[1].lower() not in _PROFILE_EXTS:
            return False
        if name.lower().endswith(cam_tool_library.LIBRARY_EXT):
            # the tool dialog's "export library" (ui/cam_tool_library.py)
            return self._save_download_as(
                item, "Save tool library",
                "Tool library (*.tools.km *.km)", "tools.tools.km", "Tool library",
                start_dir=_tool_library_dir())
        return self._save_download_as(
            item, "Save CAM profile", "Kiri:Moto profile (*.kmz *.km)",
            "piccola-profile.kmz", "Profile")

    def _export_root(self) -> str:
        base = QStandardPaths.writableLocation(
            QStandardPaths.AppLocalDataLocation) or os.path.expanduser("~/.piccola-cnc")
        path = os.path.join(base, "cam-exports")
        os.makedirs(path, exist_ok=True)
        return path

    def _batch_filename(self, suggested: str) -> str:
        """Name one file of the current batch, unique within it.

        With a job name (EasyTrace's "File Name" field, sent with the export
        intent — see ui/cam_export.py) the tool's own filename is used only for
        its *extension*. EasyTrace treats that field as a stem and decorates it
        per operation — "<name>-isolation-copper_top.nc",
        "<name>-drill-1.0mm.nc" — but what should reach the machine is the name
        that was typed, whatever combination of operations ended up in the file.

        A batch can still carry more than one file: without M6 the tool exports
        one per tool, and "Export as single file" merges only what one file can
        hold. Those cannot all be "<name>.nc", so the second and later get "-2",
        "-3" … in arrival order; the first keeps the name exactly. Files that
        differ only by extension (a G-code and an SVG preview) both keep the
        plain name — the suffix is only for a real collision.

        Without a job name (Kiri:Moto) the tool's own filename is kept; the
        uniquing still applies, so one batch cannot silently overwrite itself
        inside the staging folder.

        The extension is always the tool's, never rewritten: it is what makes a
        file G-code (_GCODE_EXT) rather than a preview, so changing it would
        change what gets offered to the machine.
        """
        stem, ext = os.path.splitext(_safe_filename(suggested))
        if self._batch_name:
            # Same rule EasyTrace applies to the field itself, so our name
            # matches the stem it derives: strip a trailing extension if the
            # user typed one (the dialog shows it — "pcb-output.nc").
            stem = _safe_filename(os.path.splitext(self._batch_name)[0],
                                  stem or "cam-export")
        candidate = f"{stem}{ext}"
        n = 1
        while candidate.lower() in self._batch_used:
            n += 1
            candidate = f"{stem}-{n}{ext}"
        self._batch_used.add(candidate.lower())
        return candidate

    def _on_download(self, item) -> None:
        """Redirect a tool's export into our own folder instead of ~/Downloads.

        Accepting with an explicit directory + filename suppresses Chromium's
        native save dialog, so the user's single click inside the CAM tool is the
        whole interaction.

        Two kinds of download are not programs and are handled first, by
        asking where they should go: a *profile* (:data:`_PROFILE_EXTS`) and a
        *model* (:data:`_MODEL_EXTS`).
        """
        if self._save_profile_download(item):
            return
        if self._save_model_download(item):
            return
        # Destination, job name and folder are taken once per batch: EasyTrace
        # emits one file per tool and every file of a single export shares them.
        #
        # The flag is what marks a batch open, not `self._batch` — that only
        # fills as downloads *complete*, and a burst arrives faster than it
        # fills, so keying on it would consume the intent again per file and
        # leave all but the first defaulting to "file".
        if not self._batch_open:
            self._batch_open = True
            if self._server is not None:
                self._batch_intent, self._batch_name = \
                    self._server.take_export_request()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._batch_dir = os.path.join(
                self._export_root(), f"{self._mode}-{stamp}")
        out_dir = self._batch_dir or self._export_root()
        os.makedirs(out_dir, exist_ok=True)

        item.setDownloadDirectory(out_dir)
        item.setDownloadFileName(self._batch_filename(
            item.suggestedFileName() or item.downloadFileName()))
        item.accept()

        def _on_state(state) -> None:
            from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                done = os.path.join(item.downloadDirectory(),
                                    item.downloadFileName())
                # Line endings, before the file is offered anywhere. A
                # controller that wants CRLF refuses a program written with
                # bare LFs, and the two tools disagree: Kiri:Moto joins with
                # \r\n already, EasyTrace does not. Normalising here — the one
                # point every export passes through — fixes both without
                # touching either vendored tool, and is a no-op for a file that
                # was already correct. See connection.normalize_crlf.
                from connection import normalize_crlf
                normalize_crlf(done)
                self._batch.append(done)
                # Restart the window on every arrival so a burst of per-tool
                # files collapses into one prompt.
                self._batch_timer.start()
            elif state in (QWebEngineDownloadRequest.DownloadState.DownloadCancelled,
                           QWebEngineDownloadRequest.DownloadState.DownloadInterrupted):
                self._status.setText(f"Export failed: {item.interruptReasonString()}")
                # Settle the batch anyway. Only completions used to start the
                # timer, so an export where every file failed left the batch
                # open for ever — and now that the batch carries a name and a
                # destination, a wedged one would hand them to the *next*
                # export.
                self._batch_timer.start()

        item.stateChanged.connect(_on_state)

    def _flush_batch(self) -> None:
        paths, self._batch = self._batch, []
        intent, self._batch_intent = self._batch_intent, "file"
        # Close the batch: the next download starts a fresh one, with its own
        # destination, name and folder.
        self._batch_open = False
        self._batch_name = ""
        self._batch_dir = None
        self._batch_used = set()
        gcode = [p for p in paths if os.path.splitext(p)[1].lower() in _GCODE_EXT]
        if not gcode:
            if paths:
                self._status.setText(
                    f"Saved {len(paths)} file(s) (no G-code recognised)")
            return

        n = len(gcode)
        plural = "s" if n != 1 else ""

        # Where the program reaches, before it is used for anything. See
        # ui/gcode_bounds.py -- an unreachable move is answered by the
        # controller with an alarm, mid-cut, spindle down; the extents are in
        # the file and can be read now instead.
        reach = self._check_reach(gcode)
        if reach is None:
            self._status.setText("Export cancelled — outside the machine")
            return

        # A tool asked to cut deeper than its flutes reach -- see
        # _check_tool_length.
        if not self._check_tool_length(gcode):
            self._status.setText("Export cancelled — tool too short for the cut")
            return

        if intent == "machine":
            # The user already chose the destination, so hand straight over
            # without asking again.
            self._status.setText(
                f"Sending {n} file{plural} to the machine{reach}")
            self.gcode_exported.emit(gcode, "machine")
        else:
            self._save_to_disk(gcode, reach)

    def _check_tool_length(self, paths: list) -> bool:
        """Stop for a tool that cuts deeper than it can reach. False = cancel.

        The depth is worked out where the facts are: ui/cam_gcode_header.py
        compares each tool's lowest cut below the stock top with its flute
        length (flute plus shank for a necked tool) and writes a
        ``LENGTH_WARNING`` line into the header for any that falls short. Here
        those lines are only collected and put in front of the operator --
        one source for the number, so the dialog and the file cannot disagree.

        Too short means the shank, not the flutes, meets the bottom of the cut:
        it rubs the wall, burns, and on a small tool snaps it. That is decided
        before the spindle starts, and this is the last point before the file
        leaves -- so it is a question with Cancel as the default, not a note.

        The holder is not considered: Kiri's tool record has no stick-out, so a
        tool that clears on flute length can still need to hang out further
        than it is clamped.
        """
        prefix = cam_gcode_header.LENGTH_WARNING
        found = []
        for path in paths:
            try:
                with open(path, "r", errors="replace") as fh:
                    # the header is at the top; the program after it is not read
                    for i, line in enumerate(fh):
                        if i > 400:
                            break
                        if line.startswith(prefix):
                            found.append(line[len(prefix):].strip())
            except OSError:
                continue
        if not found:
            return True

        # The app's own dialog, not a QMessageBox: on macOS in dark mode a
        # QMessageBox takes a dark native window while the app's stylesheet
        # keeps its text dark, and the warning came out unreadable.
        from ui.pages.dialogs import ConfirmDialog

        # The holder is the one thing this check cannot see -- Kiri's tool
        # record has no stick-out -- so the operator is told what it must be:
        # each short tool needs to hang out of the holder further than it cuts.
        import re
        needs = []
        for line in found:
            m = re.match(r"\s*(T\d+).*?cuts ([\d.]+) deep", line)
            if m:
                needs.append(f"{m.group(1)} more than {m.group(2)} mm")
        notice = ("\n\nNotice: the holder is not checked. Make sure each tool "
                  "sticks out of the holder further than it cuts"
                  + (f" — {', '.join(needs)} below the holder" if needs else "")
                  + ", so the holder cannot hit the stock.")

        dlg = ConfirmDialog(
            "Tool too short",
            "A tool cuts deeper than its flutes reach.\n\n"
            + "\n".join(found)
            + "\n\nThe shank will rub the bottom of the cut, which burns the "
            "wall and can break the tool. Use a longer tool, or reduce the "
            "depth of that operation." + notice,
            parent=self, yes_label="Export anyway", no_label="Cancel")
        return bool(dlg.exec())

    def _check_reach(self, paths: list) -> str | None:
        """Report where the program goes. None if the operator cancelled.

        Otherwise returns a note for the end of the export's status line --
        empty, or how far the job goes below zero. Returned rather than shown
        here because the status line that follows would overwrite it.

        A program is written in *work* coordinates and the machine's travel is
        in *machine* coordinates, so "does it fit" is not answerable here -- the
        offset between them is set at the machine after this file leaves. Two
        things are:

        * A program wider than the machine cannot fit wherever zero is put.
          That is a real error and is raised as one, with a way to stop.
        * How far it reaches each side of zero. That number is the one that
          earns an alarm, because this app's origin defaults to the *centre* of
          the stock -- so a program is routinely half negative in X and Y, and
          a machine homed to a corner has nothing on that side to travel into.
          It is shown every time rather than warned about, because with a
          centre origin it is the normal case, and a warning that fires on
          every export is one nobody reads.
        """
        bad = []
        reach = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        for path in paths:
            fits, lines = gcode_bounds.check(path, _WORK_AREA_MM, _MAX_Z_MM)
            # the batch needs as much room as its hungriest file
            for axis, mm in gcode_bounds.negative_reach(path).items():
                reach[axis] = max(reach[axis], mm)
            if not fits:
                bad.extend(lines)

        if bad:
            # the app's own dialog, for the dark-mode reason in
            # _check_tool_length
            from ui.pages.dialogs import ConfirmDialog
            dlg = ConfirmDialog(
                "Out of boundary",
                "This program is bigger than the machine can reach. No choice "
                "of work zero will make it fit — the travel itself is too "
                "small.\n\n" + "\n".join(bad),
                parent=self, yes_label="Export anyway", no_label="Cancel")
            if not dlg.exec():
                return None

        # the quiet half: say what the job needs on the minus side of zero,
        # which is what runs out first on a corner-homed machine
        need = ", ".join(f"{a} {reach[a]:.1f}" for a in ("X", "Y", "Z")
                         if reach[a] > 0.01)
        return f" — needs below zero: {need} mm" if need else ""

    def _save_to_disk(self, paths: list, reach: str = "") -> None:
        """Copy an export batch somewhere the user picks.

        A directory chooser rather than a save dialog: EasyTrace has no M6
        support and so exports one file per tool, which makes a multi-file batch
        the normal case, not the exception.
        """
        from PySide6.QtWidgets import QFileDialog

        target = QFileDialog.getExistingDirectory(
            self, f"Save {len(paths)} G-code file(s) to folder",
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation))
        if not target:
            # Cancelled — the files still exist in the staging folder, so say
            # where rather than implying the export was lost.
            self._status.setText(
                f"Export kept in {os.path.dirname(paths[0])}")
            return

        import shutil
        saved, failed, replaced = [], [], 0
        for src in paths:
            dst = os.path.join(target, os.path.basename(src))
            try:
                # Named exports repeat: re-running a job writes the same
                # filename again, so say when something was replaced instead of
                # reporting a plain save over a file that is now gone.
                existed = os.path.exists(dst)
                shutil.copy2(src, dst)
                saved.append(src)
                replaced += 1 if existed else 0
            except OSError as e:
                failed.append(f"{os.path.basename(src)}: {e}")

        if failed:
            self._status.setText(
                f"Saved {len(saved)} of {len(paths)} — {failed[0]}")
        else:
            note = f" (replaced {replaced})" if replaced else ""
            self._status.setText(
                f"Saved {len(saved)} file(s) to {target}{note}{reach}")

    # ── Teardown ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Tear the render processes down before the app exits.

        A live QWebEngineView outliving its profile crashes on interpreter exit,
        so drop the views first, then the profile.
        """
        for w in self._views.values():
            page = getattr(w, "page", None)
            if callable(page):
                try:
                    w.setPage(None)
                except (RuntimeError, TypeError):
                    pass
            w.setParent(None)
            w.deleteLater()
        self._views.clear()
        self._index.clear()
        self._profile = None
