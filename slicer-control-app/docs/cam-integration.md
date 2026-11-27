# CAM integration

The **CAM** tab embeds two open-source, browser-native CAM tools so Piccola
covers CAD → CAM → control in one app:

| Mode | Tool | License | Scope |
|---|---|---|---|
| **2D/3D** | [Kiri:Moto](https://github.com/GridSpace/grid-apps) | MIT | *All* general 2D / 2.5D / 3D CAM — SVG, STL, OBJ, 3MF |
| **PCB** | [EasyTrace5000](https://github.com/RicardoJCMarques/EasyTrace5000) | AGPL-3.0 | Gerber/Excellon only — isolation routing, drilling, copper clearing, cutouts |

The split is deliberate. EasyTrace ships a sibling tool, EasyShape5000, for
general shapes; we do **not** expose it. Kiri:Moto is far more mature for that
work, and two overlapping general-CAM entry points would only confuse. The PCB
tab therefore deep-links past EasyTrace's suite landing page straight into
`easytrace5000/`.

## How it fits together

```
main_window.py
  ├── CamServer (cam_server.py)   one loopback origin PER BUNDLE, fixed ports
  └── CamPage   (ui/pages/cam.py) native header + mode switch
        ├── 2D/3D → QWebEngineView → http://127.0.0.1:<portA>/kiri/
        └── PCB   → QWebEngineView → http://127.0.0.1:<portB>/easytrace5000/
                          │
                          └── download intercepted → batched → offered to the
                              File Manager transfer path (FileManagerPage.upload_file)
```

EasyTrace is not patched at all, and Kiri carries exactly one patch (see
*Patching Kiri:Moto* below). G-code leaves both through their own export button;
we intercept the resulting browser download. That keeps the AGPL obligation
trivial (we redistribute EasyTrace byte-identical) and keeps upgrades close to a
folder swap.

### Why an HTTP server at all

Both tools are 100% client-side, but neither runs from `file://`: ES-module
imports, web workers and WASM fetches all fail against the opaque origin a file
URL gets. Both geometry cores are WASM (Clipper2 for EasyTrace, kiri-geo/manifold
for Kiri:Moto), so this is not optional. The servers bind `127.0.0.1` only, on
OS-assigned ports. **No network access is required at runtime.**

### Why the ports are fixed

Each bundle binds a fixed loopback port — Kiri 8771, EasyTrace 8772 — rather than
letting the kernel assign one.

This is not about reachability, it is about **storage**. Each tool keeps its
settings in `localStorage`, and `localStorage` is partitioned per *origin*:
scheme, host **and port**. Binding port 0 gets a different port every launch, so
every launch is a different origin, so every launch starts with empty storage.
Kiri's tool library, stock sizes, operation lists and UI preferences all silently
reset. It presents as "the app forgets things", which is nobody's first guess at
a port number.

Measured before the fix, two launches sharing one on-disk webview profile:

```
launch 1  ->  http://127.0.0.1:51464   localStorage counter = 1
launch 2  ->  http://127.0.0.1:51493   localStorage counter = 1     <- reset
```

and after:

```
launch 1  ->  http://127.0.0.1:8771    counter = 1   tools: ..., MY 4mm
launch 2  ->  http://127.0.0.1:8771    counter = 2   tools: ..., MY 4mm
launch 3  ->  http://127.0.0.1:8771    counter = 3   tools: ..., MY 4mm
```

The ports sit just above the machine WebSocket server's 8765 and below the
ephemeral range (49152+ on macOS/BSD), so the kernel will not hand one of them
out at random to something else.

**Fallback.** If a port is already held — a second Piccola instance, or an
unrelated dev server — the bundle falls back to a kernel-assigned port and prints
a warning naming the consequence:

```
[cam] port 8771 unavailable (Address already in use); falling back to a random
      port. Settings saved in the CAM tools will not survive a restart.
```

That instance works normally but loses persistence, which is the right trade
against refusing to start. It also means the *first* instance keeps the stable
port and its settings; the second is the degraded one.

This is also what makes the tool-library seed (see *Tool library*) behave as
designed. Its "run once per browser profile" marker lives in `localStorage`, so
before this fix the seed re-ran on every launch — harmless, because it is
idempotent, but the promise that the library belongs to the operator afterwards
was not actually being kept.

### Why one server *per bundle*

Not tidiness — the tools force it. Both reference **server-absolute** paths, so
mounting them under a shared prefix (`/kirimoto/…`, `/easytrace/…`) 404s
everything:

* Kiri:Moto's page pulls `/lib/main/kiri.js`, `/moto/theme.css`, `/font/…`, and
  its offline loader registers a service worker at scope `/`.
* EasyTrace's suite landing page links `/easytrace5000/`.

Giving each bundle its own origin also makes the navigation guard trivial: same
port ⇒ same tool. `ToolPage.acceptNavigationRequest` allows in-bundle
navigation, sends other http(s) links to the system browser, and refuses the
rest — so a docs or GitHub link cannot strand the user inside the CAM tab.

### Cross-origin isolation (Kiri only)

Kiri's toolpath **animation** refuses to run without `window.SharedArrayBuffer`:

```js
if (A || !window.SharedArrayBuffer) { ... "The security context of this window
                                            prevents animations" }
```

The browser only exposes `SharedArrayBuffer` in a *cross-origin isolated*
context, so the Kiri bundle is served with:

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

which makes `crossOriginIsolated` true and the gate pass. This is safe precisely
because every request the tab makes is same-origin — measured at zero external
requests — which is the condition `require-corp` enforces. It does mean any
future cross-origin resource added to Kiri would be blocked unless it carries
`Cross-Origin-Resource-Policy`.

The headers are per-bundle and applied to Kiri only; EasyTrace does not need
isolation and stays without them.

Kiri does have a fallback path for non-isolated contexts ("transfer" mode, via
the `cam.anim.xfer.test` local flag), but it only engages inside an iframe or
when explicitly forced, and the isolated path is the better one anyway.

### Reproducing Kiri:Moto's URL mapping

Kiri:Moto is not served straight from disk even upstream. `grid-apps/app.js`
does `mod.static("/lib/", "alt")` **and** `mod.static("/lib/", "src")` — `/lib/`
is two overlaid directories — plus a rewrite table for the entry points.
`cam_server.py` reproduces exactly that:

| URL | Resolves to |
|---|---|
| `/` | `vendor/kirimoto/web/` |
| `/lib/…` | `vendor/kirimoto/alt/…`, then `src/…` |
| `/wasm/…` | `vendor/kirimoto/src/wasm/…` |
| `/lib/main/kiri.js` | → `/lib/pack/kiri-main.js` (rewrite) |
| `/lib/kiri/run/{worker,minion,engine}.js` | → `/lib/pack/kiri-{work,pool,eng}.js` |

There is one shim beyond upstream's rules. The slicing worker is *served* at
`/lib/kiri/run/worker.js` but its content is the packed `kiri-work.js`, which
requests `"../wasm/x.wasm"` — relative to the serving URL that becomes
`/lib/kiri/wasm/x.wasm`, a path in no tree, because the rewrite moved the script
without moving its base URL. Unmatched `*.wasm` therefore falls back to
`src/wasm/` by basename. Without it the worker is handed a 404 HTML page and
dies with `expected magic word 00 61 73 6d, found 3c 21 44 4f` (`<!DO`).

### Presets

`_PRESET_JS` in `ui/pages/cam.py` drives each tool's own public API and UI after
load — it does not patch the vendored source. For Kiri:Moto it:

1. sets **CAM** mode (it otherwise boots into FDM, so the user would land on
   slicing settings on a CNC machine);
1b. selects the **`Any.Generic.Grbl`** device — Kiri's stock GRBL post (`.nc`
   output, `G21`/`G90` preamble, `M30` end, `G4` dwell), then calls
   `devices.refresh()`, because switching mode does *not* repopulate the machine
   dropdown and it would otherwise keep listing FDM printers and showing the
   wrong machine name while the active device really was GRBL. The device is
   only set when none has been chosen yet, so it is a default rather than an
   override of a machine the user picked;
2. dismisses the **GDPR cookie notice**. Upstream suppresses that banner when the
   host is exactly `localhost`; we serve on `127.0.0.1`, which does not match the
   string. Pointing the webview at `localhost` would also work but that name can
   resolve to `::1` while the server binds IPv4 only — not worth the risk for a
   banner;
3. dismisses the **Help modal**, which is gated on a stored "help version seen"
   value and so reappears after every Kiri:Moto upgrade, not just on first run.

(2) and (3) click the tool's own controls (`#gotit`, `#mod-x`) rather than hiding
elements, so Kiri's real handlers run and persist the dismissal. All of it
retries briefly, since `window.kiri` and both overlays appear after
`loadFinished`, and gives up quietly if anything upstream moves.

`_INIT_JS` is separate and runs at **document creation**, before the tool's own
scripts — the only point early enough to affect first-run behaviour. It pre-sets
Kiri's `kiri-seed` key so the **`sample cube.stl` demo part never loads**, and
`gdpr` so the cookie banner never paints.

That ordering is forced. By the time `window.kiri` exists, the seed decision has
already been made — setting `kiri.api.feature.seed = false` from `_PRESET_JS`
leaves the cube loaded (verified). And deleting it afterwards is not safe: Kiri
restores a saved workspace on launch, so "clear the platform" would throw away
the user's own model.

Machine geometry is pinned from `ui/pages/cam.py`: work area `_WORK_AREA_MM`
(150 x 150 mm) and Z envelope `_MAX_Z_MM` (70 mm — Kiri's `maxHeight`; note
`bedHeight` is the bed plate's own thickness, not travel). The **Machine
Profile** panel (`#all-devpro`) is hidden via CSS injected at document creation,
since the machine is fixed; the device can therefore no longer be changed from
the tool's UI.

That pin also drives the 3D view: `platform.update_size()` passes `maxHeight`
through to `space.platform.setSize(w, d, h, maxz)`, which draws the build-volume
wireframe — four corner pillars and a top rectangle — at exactly that height
(`moto/space.js`). `volumeOn` is `true` at module scope and nothing in Kiri ever
calls `show_volume`, so the box is always on: the viewport shows a 150 x 150 bed
under a 70 mm ceiling.

**Pinning once at load is not enough**, and the reason is worth spelling out
because the symptom looks cosmetic and is not.

`setDeviceCode()` does

```js
dev = current.device = conf.device_from_code(code, mode)
```

— it *replaces* the live device record with a fresh one parsed from the shipped
profile, then writes the dialog's fields from it via `update_fields()`. Opening
Setup > Machines runs `showDevices()` -> `renderDevices()` -> `setDeviceCode()`,
so merely **looking** at that dialog throws the pin away. Measured across the
call: bed 150/150/70 before, **400/400/150 after** — fields *and* live record.
Every toolpath computed after that glance used a 400 mm bed until the next
reload.

So `_PRESET_JS` puts the pin in `applyPins()`, calls it once, and registers it on
Kiri's `device.selected` event, which `setDeviceCode()` emits at the end of its
own work — after the replacement, whatever triggered it (dialog, dropdown, or
the first-run selection). Nothing `applyPins()` calls emits that event back, so
it cannot recurse.

Two smaller things fall out of the same code path:

* `api.conf.update_fields()` has to run after `api.conf.save()`, for **all** the
  pins and not just the device ones. Fields are written from the records at
  specific moments, so pinning a value behind the panel's back leaves the field
  showing the old one — and that is not a display bug for long: the next click
  on any control runs `onBooleanClick` -> `api.conf.update()` ->
  `updateSettingsFromFields(process)`, which reads the stale *field* back into
  the setting and undoes the pin. Measured while the refresh sat inside the
  device branch: `camFastFeed` 500 in the settings with `"6000"` in the field,
  then 6000 in both after one click anywhere in the panel.
* The process pins (`_ORIGIN_DEFAULTS`, `_KIRI_PROCESS_DEFAULTS`) survive a
  device selection when the device has no named profile bound to it, which is
  always here now that Setup > profiles is hidden. They are re-applied in
  `applyPins()` anyway: a device with a bound profile takes the
  `setconf.load()` branch, which swaps the entire process record.

Measured after the fix: opening the dialog leaves bed 150/150/70, fields
150/150/70, and every process pin unchanged.

**Feed and plunge ceilings.** The two rows in Kiri's *limits* group are pinned
with the same mechanism (`_KIRI_PROCESS_DEFAULTS`): `camFastFeed` 500 and
`camFastFeedZ` 200 mm/min, against Kiri's shipped 6000 / 300.

Despite the plain labels these are ceilings, not speeds — their tooltips read
"max xy cutting feedrate" and "max z cutting plunge rate", and `prepare.js`
clamps every emitted feed against them:

```js
maxf = (dz < 0 ? maxZd : maxXYd) || speed
feed = Math.min(speed || maxf, maxf)
```

So an operation asking for 1200 mm/min still exports `F500`. They are pinned
rather than seeded because a travel ceiling is a fact about the machine, the
same as the bed size — to raise it for a job, raise it here.

#### The rotary switch, and which one it is

Setup > machines is hidden outright: one machine, and the dialog behind it is
read-only for a stock device anyway. What made that safe to do was working out
that the one control in there which is *not* a machine constant does nothing.

Kiri has two checkboxes that read like "turn on the 4th axis":

`useIndexed` — the dialog's **enable 4th axis** — appears five times in the
source, four of them plumbing. Its only effect is

```js
(ui.gcodeResetA = newGCode(LANG.dv_resa_s, { ... show:() => ui.useIndexed.checked }))
```

i.e. it reveals the **reset a** gcode-macro button, one dialog over. It does not
gate the rotary operations, the indexed stock, or anything in the viewport. It
is also *disabled* on a stock device (`e.disabled = !local`), so it could not be
ticked without first cloning the profile with **customize** — verified:
`.click()` on it leaves both `checked` and `device.useIndexed` false.

`camStockIndexed` — the Stock group's **indexed** — is the real one.
`updateAxisMode()` reads it and nothing else, and it is what unlocks `index` /
`lathe` / `loop`, moves the Z anchor to middle, hides the bed and re-wraps the
stock. Verified in isolation: `useIndexed` off, `camStockIndexed` on, all three
rotary ops `display: flex`.

So `ui/cam_rotary.py` lifts `camStockIndexed` to the top of `#panel-slide`,
just under the grid switch. The row is re-parented, not rebuilt:
`api.ui.camStockIndexed` is the registered `<input>`, so the click handler
(including `onBooleanClick`'s `el === ui.camStockIndexed` special case),
`updateSettingsFromFields` and `uc.setMode()` all keep working across the move.

**No group, no heading.** It used to sit in a captioned *rotary* group with
`camStockIndexGrid` (**show grid**) as its sub-option; since patch 0010 that
switch applies in both modes and `ui/cam_grid.py` lifts it separately, leaving a
heading over a single checkbox — furniture around one control. Both are gone,
and the switch names itself instead: `ui/cam_labels.py` words it **rotary mode**
rather than "enable", which only worked while a heading supplied the context.

**A moved row has to leave its group behind.** Kiri collapses a section by
tagging each row with the group it was *built* under — `div._group = groupName`
in `newDiv` — and then hiding whatever matches a collapsed name:

```js
let hidn = hidden[el._group] === true;   // inputs.js setMode
el.setVisible(!hidn && hmod && show && xprt && disp);
```

That is a property of the element, not of where it sits. Both switches are
created after `newGroup(LANG.cs_menu, ..., group:"cam-stock")`, so once lifted
to the top of the panel they still answered to the **Stock** header and
disappeared whenever it was collapsed — visibly a bug, since by then they are
nowhere near it. Both movers therefore clear `row._group` on every placement
pass, not just the first, because the row is re-placed on several events and
Kiri rebuilding the panel can hand back a freshly tagged one. Nothing else
reads `_group`, and the rows still answer to mode changes as before; they just
no longer belong to a section they have left.

Placement is asked for explicitly rather than raced: the row requests the slot
*after* the grid row, instead of both modules inserting at `panel.firstChild`
and the later one winning. The in-place test needs both the parent and the
sibling to agree — a row that is the last child of another group has a null
`nextSibling`, and so does the target slot when the grid row is last in the
panel, so comparing siblings alone reads as "already placed" and the row never
moves. `before === row` is treated as in-place too, since inserting a node
before itself is a reorder at best.

Each moved row gets the id `piccola-rotary-<key>`, since upstream gives them
none, and `ui/cam_labels.py` hangs the wording on that: the switch reads
**enable** rather than "indexed", which is Kiri's word for stock mounted in a
rotary indexer and reads as a state rather than a control. Under a *rotary*
heading the question it answers is just whether the rotary axis is in use.

**Show grid** below it is upstream's own dependent — `newBoolean(..., show:() =>
ui.camStockIndexed.checked)` — so it is absent until the switch is on, and that
is unchanged by the move (measured: `display: none` with the switch off,
`flex` with it on).

The cost of hiding the dialog is that the `reset a` macro can no longer be
edited. It is empty on the generic GRBL profile and only `op-lathe.js` reads it,
so the effect today is nil — but that is the thing to restore first if a custom
A-axis reset is ever wanted.

**The machine popup has no machine picker.** Its top row — the "select device"
label, the dropdown, and the copy / rename / delete / export buttons — is hidden
whole. Only one of those buttons is ever visible for a stock device anyway: Kiri
hides the other three itself and relabels `#device-add` to "customize" when the
device is not local.

Customize goes *with* the dropdown rather than surviving it. It clones the stock
profile into a local device and selects the clone, so leaving it would strand the
user on a copy with no way back to the original. What remains is a read-only
summary of the real machine — work area, max spindle, output flags, gcode macros
— which is all the popup could ever be here, since Kiri disables every field on
a non-local device (`e.disabled = !local`).

With no picker, whatever is selected becomes permanent — so the preset **forces**
the device rather than defaulting it: `if (dev !== 'Any.Generic.Grbl') select(...)`,
not `if (!dev)`. A profile that had already drifted onto another machine, or onto
a customize copy made before the row was hidden, would otherwise be stuck there.
The forced selection replaces the device record with the shipped 400 x 400 x 150
one; `applyPins()` runs immediately after and puts it back to 150 x 150 x 70.

**The CAM viewport is hard-centred and cannot be made positive-only.** Kiri
draws the bed spanning -w/2..+w/2 in both axes with 0,0 at the centre, and the
device-level `originCenter` flag has *no effect* in CAM mode — renders with it
`true` and `false` are pixel-identical (verified: same file hash, 0 differing
pixels across 65k samples). It is pinned `false` anyway, for determinism rather
than appearance. Showing a 0..150 positive grid would mean patching Kiri's own
platform/grid code inside the vendored bundle, which is fragile and breaks the
folder-swap upgrade path — not recommended. (EasyTrace, by contrast, has its own
Bottom-Left / Center origin buttons and a mm ruler starting at 0, so the PCB tab
already offers this.)

**The origin is stock-relative, and there is no bed-corner origin.** "0,0 at
the top-left of the work area" has no direct expression in CAM mode: the origin
there is *stock*-relative (front-left of the stock, or its top face with
`camOriginTop`), with only numeric offsets on top. The bed-relative
`ctOriginBounds`/`ctOriginCenter` pair belongs to the laser/drag/waterjet modes,
not CAM. Offsetting Y by the bed depth moves the origin relative to the *stock*,
not the bed, so it does not mean "top-left" and would silently shift every
emitted coordinate.

Within the stock, though, any point can be reached — see [Stock origin
picking](#stock-origin-picking). `_ORIGIN_DEFAULTS` pins the two anchor flags
(`camOriginCenter` false, `camOriginTop` true) because that is what the picker
measures from; the three offsets are deliberately **not** pinned, since they are
where a picked origin is stored and it has to survive a reload.

> **Note on tool changes.** `Any.Generic.Grbl` emits `M6 T{tool}` for tool
> changes, which stock GRBL 1.1 does not implement — it is normally handled or
> stripped by the sender. Single-tool jobs are unaffected. Worth confirming
> against the Piccola firmware before relying on multi-tool output.

The header's right-hand label credits each tool — name, licence and author, taken
from `package.json`/`license.md` and `CITATION.cff`/`NOTICE`. EasyTrace's NOTICE
asks for credit, and this is the cheapest honest way to give it. It used to show
the loopback port, which was an implementation detail that read as debug output.

### First-run overlays

Both tools open with introductory overlays that are noise inside an embedded
tab. Each is suppressed at its own root cause, before first paint:

| Tool | Overlay | Gate | Suppression |
|---|---|---|---|
| Kiri:Moto | cookie notice | `gdpr` in localStorage | key pre-set |
| Kiri:Moto | Help modal | `kiri-last` = version seen | key pre-set from the vendored version |
| Kiri:Moto | `sample cube.stl` | `kiri-seed` absent = first run | key pre-set |
| EasyTrace | Welcome modal | *none* — shown every load | CSS |
| EasyTrace | quickstart / laserConfig | `easytrace5000-hide-welcome` | key pre-set |

EasyTrace's Welcome modal is the odd one out: `cam-easytrace5000.js`
`onFinalize()` ends in an unconditional `showModal('welcome')`, so there is no
flag to set — `<prefix>-hide-welcome` only gates the follow-ups that open after a
workflow card is clicked. (Measured: with that key set, the modal was still
visible in 431 of 435 samples.) It is therefore hidden with CSS, which is safe
because in EasyTrace the `.modal` element *is* the backdrop — `position:fixed;
inset:0` toggled between `display:none` and `flex` — so hiding it leaves no
overlay to swallow clicks.

The trade-off: EasyTrace's workflow picker becomes unreachable, so the tool stays
on its default pipeline. That default is CNC (`pipelineState = { type: 'cnc' }`),
correct for a milling machine, but the Laser pipeline can no longer be selected
from the UI.

#### Hidden dialogs must not hold the keyboard

`ui/cam_modals.py`. Hiding a modal with CSS is only half of suppressing it:
`ModalManager.showModal()` records `activeModal` and installs a focus trap
*before* anything is drawn, and never asks whether the dialog is visible. A
hidden modal therefore opens invisibly but for real, and while it is "open"
`ShortcutManager.handleKeyDown()` returns early for every key except Escape — the
tool's shortcuts stop working — and the focus trap cycles Tab inside a dialog
nobody can see. The only way out is Escape, which nobody tries against a screen
that looks idle.

That is not hypothetical: upstream shows the welcome modal unconditionally on
every load, so the PCB tab had been booting straight into that state.

`showModal` is therefore wrapped: if what it just opened cannot be seen
(`offsetWidth`/`offsetHeight`/`getClientRects()` all empty), it is closed again
immediately through the tool's own `closeModal()`, which removes the focus trap,
clears `activeModal` and restores focus. Visibility is measured *after* the call
on purpose — `.modal` is `display:none` until `.active` is added, so the question
cannot be asked beforehand.

This also removes the **Help** dialog cleanly. `#btn-help` (top right) and its
`#help-modal` — "Starting + Keyboard Shortcuts" — are hidden: it is an upstream
tour of a workflow this app has since reshaped, describing origin, rotation and
export steps that no longer match the panel and listing shortcuts for controls
that are gone. Both the button and the modal are hidden, so no other route (F1, a
deep link, a link in another dialog) can put it on screen, and F1 needs no
special handling — it opens the modal and the guard closes it in the same tick.

Verified against a stand-in with upstream's bookkeeping: `showModal('welcome')`
and `showModal('help')` leave `activeModal` null with no focus trap and shortcuts
alive, while a dialog that is *not* hidden — the export manager — still opens and
holds the keyboard exactly as upstream intends.

### Relabelling

`ui/cam_labels.py` renames a few controls whose wording does not fit a CNC
machine. Today that is Kiri's toolbar **Slice -> Generate**: "slice" is slicer
vocabulary inherited from its 3D-printing side, and in CAM the button computes
toolpaths — the original only makes sense if you think in layers.

Also renamed: Output > **ease down -> ramp**, Kiri's name for plunging along a
descent rather than straight in (its own tooltip: "plunge cuts will spiral down
or ease along a linear path"). That move is a ramp, which is what it is called
at the machine and in every other CAM package; "ease down" only reads as
gentleness. And per-operation **spindle rpm -> spindle RPM**.

The two rows in **limits** are renamed for what they actually are: **feed rate
-> max feed rate** and **plunge rate -> max plunge rate**. They are not the
speed the job cuts at, they are ceilings every operation's own feed is clamped
against — `prepare.js` reads them as

```js
feedRate   = Math.min(camFastFeed, feed || feedRate || plunge);
plungeRate = Math.min(camFastFeed, feedRate || plunge, plunge || plungeRate || feedRate);
```

so an operation asking for 1200 mm/min still exports F500 when the limit says
500. Plainly labelled they read as the speed, which is the one thing they are
not, and a job that quietly runs slower than every number in front of you says
is a bad half hour to debug. Upstream's own tooltips already say "max xy cutting
feedrate" and "max z cutting plunge rate", so the labels are catching up with
them rather than inventing a term. Renaming the *language keys* is safe here
because `cc_rapd_s` and `cc_rzpd_s` each have exactly one call site
(`init-menu.js`, `camFastFeed` and `camFastFeedZ`); a per-operation feed, which
really is a speed, uses different keys and is untouched.

Two details make it stick. Each tool populates labels from a language pack
*after* our script runs and re-applies them on any language change, so the
replacement is maintained with a MutationObserver rather than assigned once
(verified by forcing the label back and watching it restore). And casing is left
to the tool: Kiri renders these with `text-transform: capitalize`, so the
replacement is written lowercase and comes out matching its neighbours.

**Three anchors, by what there is to hold on to.** `RELABEL` takes a CSS
selector, for anything with an id. `RELABEL_UI` takes a Kiri `api.ui` key and
walks out to the `.var-row` around it — the right anchor for the settings
panel, whose rows carry no ids and whose order is not ours to depend on, and
where writing to the row itself would replace its checkbox with a text node.

`RELABEL_LANG` renames the *word*, in `api.language.current`, for text Kiri
stamps into many places from one key. The spindle field is that case: every
operation type with a spindle builds its own copy (eleven call sites in
`mode/cam/app/cl-ops.js`), so the rename belongs to the term rather than to a
control. Controls are then created with the new wording; anything already on
screen is caught up by a sweep scoped to `.cam-pop-op` and `.var-row` labels.

**The write has to be repeated, not made once**, and getting that wrong is why
the first attempt did not stick. `init_lang()` emits `init.lang` and only
*then* calls `LANG.set()`, which assigns every key of the chosen map over
`LANG.current`:

```js
Object.keys(map).forEach(key => { ... LANG.current[key] = val; });
```

An injected script racing Kiri's boot writes before that and is silently wiped.
So the rename is re-applied on `init.lang` (deferred past the `set()` that
follows it), `init.one`, `init.two`, `init-done` and `settings.saved`. The
writes are idempotent, and the CAM menu that carries the label is built well
after the last of them. The test models that order — apply, let Kiri's `set()`
clobber it, assert it comes back — because a test that only checks the first
write passes against code that fails in the app.

This is also why `capitalize` cannot be left to do the job here: it title-cases
each word and leaves the rest alone, so "rpm" renders as "Rpm" and no
stylesheet can know which words are initialisms. Writing the unit in capitals
survives it — "spindle RPM" renders as "Spindle RPM".

### Fixed-machine simplifications

**The preview tab is hidden.** Nothing needs it: both of the things anyone goes
on to do run the slice themselves — `function.animate` calls
`api.function.prepare()` before animating, and export does the same — so
Preview is a stop on the way rather than a step. Hiding the tab does not
disable the view: `setViewMode(PREVIEW)` still works, and Kiri lands there by
itself after a slice, which is exactly what makes the tab redundant.

**File > recent is hidden.** It opens Kiri's own file catalogue
(`sync.js`: `api.modal.show('files')`) — models cached inside browser storage,
which is a second and invisible place for a part to live alongside the app's own
File Manager. One of them has to be the answer to "where is my file", and it is
the one with a folder on disk behind it. Hidden rather than removed, since
`sync.js` binds `$('file-recent').onclick` unconditionally during init and would
throw on a missing node.

**The view menu is hidden.** Everything it offered is on the view box in the
bottom right: its *contents* / *home* / *top* are the same `SPACE.view.*` calls
those buttons make, *focal point* is driven from there, and *fullscreen* was
already hidden. A menu whose every entry has a nearer twin is a second place to
look for one answer.

Hidden, not removed, and that distinction carries weight here: `ui/cam_views.py`
reaches `#context-setfocus` by **clicking** it, and a programmatic `.click()`
fires the handler on a `display: none` element exactly as on a visible one.
Removing the node would take the focal-point button with it. Verified in a
browser rather than assumed — and the first version of that test asserted the
wrong thing, checking `getComputedStyle(item).display`, which still reports the
item's own value inside a hidden parent; `getClientRects().length` is the
question that was actually being asked.

Matched by the language key on its label (`span:has(> label[lk="vu_menu"])`),
since `topMenu()` gives the span no id — the same shape as the mode menu.

**The expert group is hidden.** Three of its five rows were already gone (the
arc trio, `camArcEnabled` being pinned off) and both survivors are now pinned in
`_KIRI_PROCESS_DEFAULTS`, so it was a header over two controls whose values are
decided elsewhere. `#cam-expert` is the container and `newGroup` appends the
`.set-header` *into* it, so hiding the container takes the caption too rather
than leaving an empty heading.

Worth writing down what went with it: *skip shadow* (`camExpertFast`) is
upstream's escape hatch for a model that hangs during shadowing — its own
tooltip reads "try enabling if slicing hangs during shadowing". It is pinned
**off**, so if a part ever refuses to slice, that pin is the first thing to
flip. *rounded corners* (`camRoundCorners`) is pinned **on**, matching
upstream's default; it becomes `ClipperLib.JoinType.jtRound` in `op-area.js`.

Both are pinned rather than left to whatever a stored profile holds, on the
usual rule: a setting with no control and no fixed value is one that can differ
between two machines with no way to see it.

A CSS hide fails silently — a selector matching nothing looks exactly like one
that works until the app is opened — so `test_hides.py` loads the real
`index.html` with the stylesheet layered on and asks the browser what it
computed, then checks that *every* id the stylesheet hides is one the app
actually builds. The left-panel groups are static markup and can be measured
directly; the toolbar is assembled at runtime by `app/init/menu.js`
(`span({ id: 'act-preview', ... })`), so those ids are verified against the
source that creates them.


Controls that cannot meaningfully vary on a fixed machine are removed, and the
underlying setting pinned so a stale saved value cannot survive the control
disappearing:

| Tool | Removed | Pinned to |
|---|---|---|
| Kiri:Moto | Machine Profile panel (`#all-devpro`) | device `Any.Generic.Grbl` |
| Kiri:Moto | Mode menu (`label[lk="mo_menu"]`) | CAM mode |
| Kiri:Moto | Setup > install / uninstall | n/a — see below |
| Kiri:Moto | Setup > profiles (`#set-profs`) | `_ORIGIN_DEFAULTS` + `_KIRI_PROCESS_DEFAULTS`, re-applied every load |
| Kiri:Moto | CAM *expert* group: `arc output` + `arc tolerance` / `arc resolution` only | `camArcEnabled: false` |
| Kiri:Moto | machine popup's selector row (`#dev-sel`, `#dev-list`, `#device-add`) | device forced to `Any.Generic.Grbl` every load |
| Kiri:Moto | Setup > machines (`#set-device`) | the whole dialog — see *The rotary switch* below |
| Kiri:Moto | funding prompts (`#export-support`, `#app-don8`, `#mod-don8`) | n/a |
| Kiri:Moto | View > fullscreen (`#app-xpnd`) | n/a |
| Kiri:Moto | Info menu (`label[lk="info"]`) | n/a |
| Kiri:Moto | language picker (`span:has([id^="lset-"])`) | app UI language |
| EasyTrace | Post-Processor picker (`#post-processor`) | `postProcessor: 'grbl'` |
| EasyTrace | Start Code / End Code (`#start-code-ta`, `#end-code-ta`) | `_EASYTRACE_START_CODE` / `_END_CODE` (`None` = processor default) |
| EasyTrace | Units picker (`#gcode-units`) | `units: 'mm'` |
| EasyTrace | — (Rapid Feed, control left visible) | `speeds.rapidFeed` / `rapidFeedRate` = 1500 mm/min |
| EasyTrace | — (Spindle Speed, field left editable) | `getDefaults()` → 12000 RPM |
| EasyTrace | Coolant + Vacuum section | `machine.coolant: 'none'`, `machine.vacuum: false` |
| EasyTrace | dark/light toggle (`#theme-toggle`) | light theme |
| EasyTrace | Actions dropdown (`#quick-actions-btn`) | n/a — see below |
| EasyTrace | Solder Stencil category (`[data-op-type="stencil"]`) | file type dropped from `core.fileTypes` |
| EasyTrace | CNC Output Options block (`#exporter-cnc-options`) | optimize pinned checked, tool changes unchecked |
| EasyTrace | Cutting Strategy group (`#prop-entryType`) | `entryType: 'plunge'` — its only option |
| EasyTrace | Include operation comments (`#exporter-include-comments`) | pinned checked |
| EasyTrace | Split drill files by hole diameter (`#exporter-split-drills-field`) | unticked, as shipped |
| EasyTrace | — (Multi-depth Cutting) | `multiDepth: false` via `getDefaults()` |
| EasyTrace | — (rules under the sidebar group headings) | n/a — headings kept |
| EasyTrace | — (rule above the stage action button) | n/a — button kept |
| EasyTrace | — (input/unit seam in `#property-form`) | n/a — unit chip kept |
| EasyTrace | — (`#x-offset`, `#y-offset` spinner step) | `step="1"`, i.e. 1 mm a click |
| EasyTrace | Apply Origin + Reset Unsaved Changes | picking a spot applies it — see below |
| EasyTrace | Apply Rotation (`#apply-rotation-btn`) | applied 500 ms after typing stops |
| EasyTrace | onboarding highlight loop (`.onboarding-highlight`) | `dismissSuggestion()` at load |
| EasyTrace | help markers in the properties sidebar (`#sidebar-right .tooltip-trigger`) | n/a — labels unchanged |
| EasyTrace | Help button + dialog (`#btn-help`, `#help-modal`) | n/a — see *Hidden dialogs* above |
| EasyTrace | toolbar rule (`.cam-toolbar .toolbar-separator`) | n/a — view controls sit flush right |

The **Info menu** is removed whole: it holds exactly two entries, help and
donate, and both are unwanted here — donate is a funding prompt, and help opens
the same modal suppressed at startup, so leaving the menu would just be a manual
route back to a popup deliberately removed.

The **language picker** (the `fa-language` icon at the far right) goes too:
Piccola ships one UI language, and switching only the embedded tool would leave
the CAM tab speaking something different from the app around it. Its span
carries no id or language key, so the rule anchors on the `#lset-*` entries it
contains — those are language codes and stable. Verified it matches no ancestor
of the Setup menu, which sits in a sibling span.

**View > fullscreen** is removed: its handler is
`document.body.requestFullscreen()`, which in an embedded webview would try to
blow the web content up over the app. QtWebEngine only honours that with
`FullScreenSupportEnabled` plus an explicit `fullScreenRequested` handler, and
the CAM tab is a panel inside Piccola rather than a window of its own. Kiri hides
the entry itself when `requestFullscreen` is unavailable; in Chromium it is
available, so it needs hiding explicitly. The rest of the View menu — set focus,
contents, home, top — is untouched.

Kiri's **funding prompts** are removed too — the "this project needs your
support!" link in the export dialog, the donate entry in the Info menu, and the
Patreon / GitHub Sponsors / PayPal modal it opens. Reasonable on grid.space, out
of place inside a machine control app. Attribution is untouched and deliberately
kept: Kiri is MIT, its LICENSE ships in `vendor/kirimoto/`, and the CAM header
credits "Kiri:Moto · MIT · Stewart Allen" on every visit.

The rule uses exact ids, because "support" is overloaded in this markup:
`#fdm-support` and `#sla-support` are support-*structure* settings groups
(`class="set2-group mode-fdm"`) and must keep working.

**Setup > install / uninstall** are removed because they are a hazard here.
Upstream they register `/boot/service.js` as a service worker at scope `/`,
which downloads `bundle-<version>.bin` (~25 MB of the whole app) so a hosted
grid.space copy keeps working offline. Ours is already served from `vendor/` on
loopback — offline by construction — and `tools/fetch_vendor.py` drops that
bundle as redundant, so `/boot/bundle-<version>.bin` is a 404. Clicking install
would register a worker that intercepts every request the tab makes and then
fails to fetch what it is meant to serve. `uninstall` goes with it, since nothing
can create a worker once install is gone. (Each launch also gets an OS-assigned
port, so every run is a distinct origin and registrations cannot carry over
between sessions.)

Kiri's Mode menu is removed because its other modes (FDM, SLA, laser, drag
knife, waterjet) would drop the user into a slicer unrelated to the machine; the
preset already forces CAM on every load, so hiding the menu removes the only way
back out. It is anchored on `lk="mo_menu"` — Kiri's own language *key* for that
menu rather than its visible text — so the rule survives a UI-language change.
The `#mode-*` divs inside `#mode-tools` are left alone: those are the per-mode
tool panels, and the CAM one is what we want on screen.

Neither coolant removal is cosmetic. A non-`none` coolant makes
`export/processors/base-processor.js` inject `M7` (mist) or `M8` (flood) into the
start code and `M9` into the end code, and vacuum emits `M10`/`M11` — commands a machine with
neither can not honour. The two share one `.property-section` and are its only
fields, so the whole section is hidden rather than leaving an empty container;
the section has no unique id, hence anchoring on `#coolant-type`. Note the vacuum
input sits *inside* its label, so the `:has()` rule uses a descendant match and
the fallback targets the label as well.

EasyTrace's **Actions dropdown** — the toolbar menu at top left — is removed
whole, along with its three entries, because each is either a second route to
something the workspace already offers or a route back into a dialog
deliberately suppressed:

* *Add Files…* opens the quickstart modal, the follow-up to the welcome modal
  removed above and the same picker whose laser/CNC choice is pinned to CNC.
  Files are still added from the operations tree ("Click to add Isolation file"
  and friends) or by dropping them on the window — both untouched.
* *Manage Toolpaths* opens the export manager. That dialog stays reachable: it
  is what an operation's own machine/export stage opens
  (`ui-trace-operation-panel.js` `onExportStage`), and it is where the Save as
  File / Save to Machine buttons live. Only the shortcut goes.
* *Export Canvas SVG* downloads a picture of the canvas. It is not G-code, so it
  lands in the staging folder and is reported as "no G-code recognised" — a dead
  end inside this app.

The whole `.dropdown-menu` wrapper is hidden rather than the button alone, so no
empty dropdown container is left behind; `.toolbar-left` keeps its `flex: 1`, so
the workspace title stays centred with the section empty. Upstream's click
handlers still bind to the hidden elements, which is harmless — nothing can
reach them — and keeps this a pure CSS change.

The **Solder Stencil (Exp.)** category is removed from the operations tree
because it is the one operation type that never produces G-code.
`cam-controller.js` routes stencil operations away from the CNC path entirely
(`if (op.type === 'stencil') stencilOperationIds.push(id)`) and `cam-core.js`
exports them through `generateLaserExportFiles()` with `format: 'svg'` — artwork
for a laser or a stencil cutter, not toolpaths for a mill. Such a file lands in
the export staging folder and is reported as "no G-code recognised", so the
category could only ever lead to a dead end. The rule anchors on
`data-op-type="stencil"`, the attribute EasyTrace's own tree code keys off,
rather than on position or the visible label.

CSS alone is not enough here. A dropped file is typed by extension
(`getOperationTypeFromExtension` over `core.fileTypes`), and `.gtp` / `.gbp` —
the paste layers — are claimed by nothing but stencil, so dropping one would
still create a stencil operation whose tree node now sits inside a hidden
category: invisible, unselectable, and silently part of the next export. The PCB
preset therefore also deletes the stencil entry from `core.fileTypes`, which
makes those two extensions unrecognised and the drop a no-op. Every other
extension is claimed by an earlier entry (isolation, clearing, cutout, drill) and
is unaffected, and `setFileTypes()` runs once from the app profile during init,
well before the preset, so nothing puts it back. With no way to create a stencil
operation, the export manager's stencil block stays hidden by its own
`jobHasStencil` check.

**Optimize toolpaths (Experimental)** is removed from the export manager for the
one-machine-one-behaviour reason. Upstream ships the checkbox checked and the
export reads it as `?.checked ?? true`, so the option's only real use is turning
optimisation *off* — and the two settings emit different G-code for the same job:
`toolpath-optimizer.js` reorders paths, clusters staydown moves, and simplifies
segments (collinear points within 1°, sub-0.05 mm moves). The setting that ships
as the default is also the one upstream tests, so the control goes and the value
is pinned on. That is already the DOM state; the preset re-asserts it so an
upstream markup change cannot silently flip it while the control is invisible.

It shares the **CNC Output Options** block with *Insert tool change commands*,
which upstream ships `disabled` — EasyTrace has no M6 support, so that box cannot
be ticked and the export reads it as false. With both of its fields unusable the
block is a heading over a dead control, so the whole `#exporter-cnc-options` div
is hidden rather than the two rows inside it; a plain id selector, so no `:has()`
fallback is needed. Both values are still read from the hidden elements at export
time, which is why the pin matters and why the tool-change box is left
unchecked.

Two more rows go from the **export manager**. *Include operation comments* is
hidden and pinned checked (`_EASYTRACE_INCLUDE_COMMENTS`): comments are the
header EasyTrace writes in front of a program — operation, tool, feeds — which is
what makes a staged file readable weeks later, and nothing here is helped by
turning them off. *Split drill files by hole diameter* is hidden as well; it
exists because EasyTrace has no M6, splitting a drill job into one file per
diameter, i.e. per bit. It ships unticked, so hiding it changes nothing about
today's output — every hole in a drill operation is cut with the one tool that
operation selects, which is what milling them does anyway. That row has a wrapper
of its own (`#exporter-split-drills-field`), so the whole field goes; the
comments checkbox shares a `.property-field` with two others, so its label is
hidden instead.

**Multi-depth Cutting** starts **off** (`_EASYTRACE_SINGLE_PASS_OPS`). Upstream's
definition ships `default: true`, which splits a cut into `depthPerPass` passes;
off, the toolpath goes to full depth in one (`calculateDepthLevels` returns a
single level). It is pinned through `getDefaults()` like the spindle, so it is a
starting value rather than a lock — the box stays usable and a value already
saved on an operation still wins.

Worth knowing before leaving that tuple at every operation type: on isolation and
clearing the cut is 0.1 mm deep and one pass is simply right. Cutout and milled
drill holes default to -1.8 mm, and a single pass to that depth is a lot to ask
of a small endmill. Dropping `cutout` and `drill` from the tuple restores
upstream's multi-pass for exactly those two.

The **Cutting Strategy** group goes from an operation's Strategy stage. Its only
field is *Entry Type*, whose select has exactly one option — "Plunge (Vertical)"
— which is also its default and what the pipeline falls back to everywhere it
reads the value (`strategy.entryType || 'plunge'`): a heading and a dropdown that
can only say one thing. It is hidden rather than removed, so the parameter is
still rendered and keeps its value — `ParameterManager` loads every definition
regardless of what is on screen, so `entryType` stays `plunge` with nothing to
pin. The group has no id of its own (each category renders as a bare
`<div class="property-section"><h3>…</h3>`), so the rule anchors on the field it
contains.

The **seam inside a number field** goes as well — the vertical line where the
input meets its unit chip, standing beside *Mill Stepover*, the field that
appears under *Mill Holes*. `.input-unit input` drops its right border while
`.unit` carries a full one, so the two meet in a rule. Only that edge is removed:
the chip keeps its top, right and bottom border and its own background, so it
still reads as a unit chip rather than as part of the input. Scoped to
`#property-form`, where the per-operation numbers are set; the Board Placement
and Machine Settings fields keep theirs.

The **rule above the stage's action button** goes with them. `.property-actions`
carries a `border-top`, and the button is appended straight after the parameter
form — so in a drill's Geometry stage, where *Mill Holes* (and *Mill Stepover*
under it) are the last fields, that line lands right beneath them. It is the last
horizontal rule in the panel once the group headings have lost theirs, and the
button needs no help being noticed: it is full width and in the accent colour.
The padding and margin stay, so it keeps its distance from the field above.

The **rules under the group headings** in the properties sidebar go too. Every
parameter category draws one, so a stage like a drill's Strategy — *Depth
Settings*, *Peck Drill Parameters* around the Dwell Time field — was cut into
bands by lines the headings already imply. The headings stay: uppercase and
letter-spaced, they carry the separation on their own. The padding the rule sat
on goes with it so no dead gap is left, and the selector is scoped to
`#sidebar-right`, leaving the export dialog's own headings alone.

The **Origin Offset** fields keep both arrows but step by **1 mm** instead of
upstream's `0.01`, which nudged the board by 10 µm a click — 100 clicks to move
it a millimetre, and below anything that matters when placing a board here.
`step` is the only lever: the arrows are the browser's own spin buttons and
always move by the field's step. Typed values are unaffected — a step mismatch
marks the input `:invalid` but never rewrites `.value`, EasyTrace reads the field
with `parseFloat()`, and nothing in the bundle calls `checkValidity()` or styles
`:invalid` — so `0.5` can still be typed and still applies as `0.5`. (Typing into
these fields at all needs the fix in *Upstream bug workarounds* below.)

GRBL is both EasyTrace's own default (`config.js`) and, per its docs, its
best-tested post-processor; the alternatives (Marlin, LinuxCNC, Roland RML) are
ways to silently emit a dialect the Piccola firmware does not speak. The picker
row is removed with `:has()` so no orphan label is left, with element-level rules
as a fallback. Hiding alone is not enough — a choice saved before the control was
hidden would stick with no way to change it — so the PCB preset also forces the
setting back through EasyTrace's own `updateSettings()`. Verified by setting it
to `roland` and confirming the preset restores `grbl`.

**Start Code, End Code and Units** go with it — the rest of that CNC Output
section — so the whole `.property-section` is hidden rather than four fields
inside a container that would then be empty. It is anchored on `#gcode-units`,
since the section carries no id of its own.

Start and end code are the preamble and postamble wrapped around every export. On
a fixed machine they are a property of the machine, not of the job, and they are
the easiest field in the tool to break silently — a stray line here is a stray
line in front of every cut. They become two constants in `ui/pages/cam.py`
instead:

```python
_EASYTRACE_START_CODE = None   # None -> the post-processor's own default
_EASYTRACE_END_CODE = None
```

`None` is what an untouched install already emits — for GRBL, `T1\n` and
`M5\nG0 X0Y0\nM2` (`export/processors/grbl-processor.js`). Putting a string there
overrides it for every export with no further wiring: EasyTrace resolves
`userStartCode`/`userEndCode` over the processor default
(`resolveStartCode`/`resolveEndCode`) and `cam-core.js` passes both into
generation. Deferring to the processor rather than copying its text here means a
vendor refresh that improves the GRBL preamble is picked up instead of being
pinned to a stale copy. Note `''` is not "no preamble" — upstream treats empty as
unset and falls back to the default, so an empty preamble has to be a comment
line.

Units are pinned to `mm`. The machine is metric, every other length in the app is
mm, and the setting picks the G20/G21 word *and* the units coordinates are
written in — so an accidental "inch" is a 25.4× error on every coordinate, not a
display preference.

All four are pinned in one pass, and `undefined`/`null`/`''` are treated as
equal, since they mean the same thing to the resolver — otherwise a never-set
field would look different from `None` and rewrite storage on every launch.
Verified against the pin logic directly: a fresh install needs no write at all;
a session carrying custom start/end code has both cleared back to the processor
default; and one saved as `roland` + `inch` comes back as `grbl` + `mm`.

**Rapid feed** is defaulted to **1500 mm/min** (`_EASYTRACE_RAPID_FEED_MM_MIN`),
up from EasyTrace's 1000. It is the machine's traverse rate, so it belongs to the
machine rather than to a job. The control is deliberately *left visible* — this
is a default re-applied on every load, not a lock — and the preset also writes
the field, because the Machine Settings panel fills its inputs during UI init and
would otherwise keep showing the old number while the setting behind it had
moved.

It is written to **two** keys, because upstream disagrees with itself about the
name: the Machine Settings field reads and writes `speeds.rapidFeed`
(`ui-machine-settings.js`), while the toolpath pipeline reads
`speeds.rapidFeedRate` (`cam-core.js` `buildToolpathContext`) — a key `config.js`
never defines. Setting only the field's key would leave the value invisible to
the code that consumes it.

What this changes is the job-time estimate, not the G-code: rapids are emitted as
`G0` with no feed word, and `rapidFeedRate` is read by `calculatePathMetrics()`
alone. With the key undefined every rapid contributes `undefined` there, fails
the `feed > 0` test and is costed as zero time, so estimates count only cutting
moves; setting it makes them count travel at the real traverse rate. (The
sibling `maxFeedRate`/`maxFeed` mismatch is harmless — `base-processor.js` falls
back to the processor's own `maxRapidRate`, and it only drives a warning.)

Verified against the pin logic: stock settings come out with both keys at 1500
and the other speeds untouched, a second load is a no-op, and a session carrying
a hand-typed 800 is brought back to 1500 in both keys and in the field.

**Spindle speed** starts at **12000 RPM** (`_EASYTRACE_SPINDLE_RPM`). One
machine means one spindle, but EasyTrace takes the starting RPM from whichever
tool the library picks, and `tools.json` spreads it from 5000 to 18000 across the
catalogue — so it changed with the endmill rather than with the machine.

Unlike the settings above this is not a stored setting but an operation
*parameter*, resolved in `ParameterManager.getDefaults()` from
`tool.cutting.spindleSpeed`. It is pinned by wrapping that method on the
prototype, which sets what every operation starts with while leaving the field
editable: `loadFromOperation` prefers live state, then the operation's saved
settings, and only then these defaults, so a value the user has already set is
never overwritten. Changing the tool does not undo it either — that path
re-derives only `toolDiameter`, never the cutting feeds.

It does reach the machine: the value flows through the operation's parameters
into `plan.metadata.spindleSpeed` and out as the `S` word, well inside both the
parameter's own 100–30000 range and GRBL's `maxSpindleSpeed`.

Verified against a stand-in of `getDefaults`: a new isolation operation starts at
12000 instead of its tool's 15000 and a drill at 12000 instead of 8000, with the
other cutting parameters untouched, while a value already saved on an operation
still wins.

### Arcs and the height map

`ui/cam_arcs.py`. EasyTrace is made to emit straight moves only, because an arc
in a height-map job is not compensated at all. The firmware patches Z line by
line while a map is active (firmware README §5.37):

> `G1` moves: the Z word is always rewritten with `programmedZ + interpolatedOffset` …
> `G0` moves: rewritten only if Z is explicitly present …
> **`G2`/`G3` arcs and relative-mode (`G91`) lines: passed through without modification.**

So the tool holds whatever Z the preceding `G1` was rewritten to right through
the curve: correct depth along straights, a frozen wrong depth through every
arc, and a step at each junction — which is exactly the milling artifact that
prompted this.

The switch already exists upstream: `gcode-generator.js` linearises arcs
whenever the active processor reports `supportsArcCommands: false`, and
`base-processor.js` falls back from `generateArc()` to `generateLinear()`. The
Roland processor ships that way, so this is the tool's own path rather than ours.
It is applied by wrapping `registerProcessor`, which runs inside the generator's
constructor — every processor is cleared before anything can use one, so no
stale post-processor choice could quietly bring arcs back.

**The cost is file size, not accuracy.** `arcResolution` (`SEGMENT_MM`, 0.1 mm)
is a segment *length*, scaled by radius in the generator — halved under 2 mm,
doubled over 10 mm — and the deviation from the true arc is the sagitta, L²/8r.
Measured by running the vendored `linearizeArc()` on a full circle:

| radius | segment | lines | sagitta |
|---|---|---|---|
| 0.5 mm | 0.05 mm | 63 | 0.63 µm |
| 1 mm | 0.05 mm | 126 | 0.31 µm |
| 5 mm | 0.10 mm | 315 | 0.25 µm |
| 20 mm | 0.20 mm | 629 | 0.25 µm |

Sub-micron, orders below the height map's own resolution. Line rate stays modest
— 37 lines/s for 1 mm pads at 110 mm/min, 33 lines/s for a cutout at 400 mm/min.
And nothing is lost in motion quality: grbl-family firmware segments arcs
internally before stepping them, so `G2`/`G3` in the file buys smaller files
rather than smoother movement.

Kiri:Moto has the same exposure and is safe by default (`camArcEnabled: false`),
but it is a visible checkbox, so `_KIRI_PROCESS_DEFAULTS` pins it off with the
origin defaults.

### Tool library

`ui/cam_tools.py`. Both tools ship a catalogue that is not the one this machine
wants, and neither vendored tree is patched: each library is shaped at runtime
through the tool's own API.

#### EasyTrace

Registers bits the shipped catalogue does not have.
`vendor/easytrace/tools.json` (41 bits) stays byte-identical; extras go in at
runtime through `ToolLibrary.addTool()` — the same public method the library's
own loader uses — so they index by id, type and operation like the rest and show
up in the tool dropdown. Registration waits for `isLoaded`, skips ids that are
already present, and runs the library's own `validateTool()` first so a
malformed entry fails with its message rather than halfway through indexing.

Currently one bit: **30° V-Bit 0.2mm Tip** (`vbit_30deg_0.2mm`). Upstream ships
30° only with a 0.1 mm tip and 0.2 mm only at 45° and 60°. Its cutting values are
interpolated from those two neighbours — a wider tip than the 30°/0.1 takes a
little more feed and depth, a narrower included angle than the 45°/0.2 leaves a
weaker tip section that takes a little less. `spindleSpeed` carries the machine's
own 12000 RPM rather than advice that would never be followed, since
`_EASYTRACE_SPINDLE_RPM` applies to every operation anyway.

#### What the G-code generation does with a V-bit

**It does not model the taper.** `getToolDiameter()` returns `tipDiameter` for a
`v_bit`, and nothing else in the tool reads `geometry.angle` — the whole toolpath
pipeline sees one number, `toolDiameter`, and treats the bit as a straight cutter
of that width. So the isolation gap is planned at the tip width while the real
cut widens with depth:

```
width = tipDiameter + 2 × depth × tan(angle / 2)

depth 0.05 mm -> 0.227 mm  (+13%)
depth 0.10 mm -> 0.254 mm  (+27%)   <- the stock -0.1 mm cut depth
depth 0.15 mm -> 0.280 mm  (+40%)
depth 0.20 mm -> 0.307 mm  (+54%)
```

This is not specific to the added bit — it is the same on every V-bit in the
catalogue, including the 45° one that is the isolation default.

Nothing here changes it. `toolDiameter` is an ordinary editable parameter
(0.01–10 mm) seeded from the tool, so the effective width can be dialled in per
job by typing it, which is the honest place for it: only the operator knows the
depth they will actually cut. Teaching the pipeline to compute it would change
toolpaths for **every** V-bit, and is a decision to take deliberately rather than
as a side effect of adding a bit.

Verified against the real `ToolLibrary` class loaded from `vendor/` with the real
`tools.json`: the bit joins the isolation list as a seventh V-bit, is indexed by
id and type, passes `validateTool()`, reports 0.2 mm from `getToolDiameter()`,
and a second run adds no duplicate.

#### Kiri:Moto

Kiri has no catalogue file. Its tool list is `settings.tools`, part of the
workspace settings in localStorage (`ws-settings`), seeded once from
`conf/defaults.js template.tools` — seven imperial sample bits, ids 1000-1006 —
and the user's from then on: `normalize()` only fills *missing* top-level keys,
so an edited list is never re-merged with the template.

That is why this **seeds rather than pins**. Work area and origin are facts about
the machine and are re-applied on every load; a tool library is not — Kiri gives
it an add / copy / delete / import UI, and overwriting the operator's own edits
on every launch would be wrong. The shaping runs once per browser profile,
recorded under `piccola-kiri-tools`, and the library belongs to the operator
afterwards. Bumping `KIRI_SEED_VERSION` re-runs it, which also re-drops anything
in `KIRI_DROP` that has since been added back — so bump it deliberately.

The result is a four-bit library:

| Tool | id | Ø | Source |
|---|---|---|---|
| `end 1/4` | 1000 | 6.35 mm | upstream, kept — every op default names it |
| `end 1/8` | 1001 | 3.175 mm | upstream, kept |
| `end 6mm` | 9000006 | 6 mm | added (`KIRI_ADD`) |
| `end 3mm` | 9000003 | 3 mm | added (`KIRI_ADD`) |

Dropped: `end 1/16`, `vee 1/8`, `ball 1/8`, `drill 1/8`, `drill 1/4`. Each is
matched on **id + name + type**, so a default the operator renamed or repurposed
is theirs and survives. Tools they add are stamped `Date.now()` by Kiri and can
never collide with these ids.

Added ids are fixed constants, not timestamps: an operation stores the *id* of
its tool (`camRoughTool` and friends), so an id that moved between launches would
silently repoint every op that used it. `number` — the T word in
`M6 T{tool}` — is assigned from what is actually left, not from an assumption
about what was dropped.

**Dangling references are the real work here.** Kiri does not complain about a
tool id that no longer exists: `new Tool(settings, id)` (`mode/cam/core/tool.js`)
silently falls back to `settings.tools[0]`, and the op panel's dropdown, rebuilt
from `settings.tools` by `updateFieldsFromSettings`, leaves `selectedIndex` at 0
when it cannot find the saved id. A drill op would keep running, showing the
first tool in the list and cutting with it, while the setting behind it still
named a bit that was gone. Upstream ships exactly that trap — `camDrillTool`
defaults to 1006, `drill 1/4`.

So every reference to a dropped bit is repointed at the surviving tool of
**nearest cutting diameter** (in mm, honouring each tool's own `metric` flag),
across the `cam*Tool` fields of the live process and of every saved CAM process,
and the tool on each operation already in the op list. `drill 1/4` (6.35 mm)
lands on `end 1/4`, `drill 1/8` (3.175 mm) on `end 1/8`, `end 1/16` on `end 3mm`.

`api.conf.save()` is what makes it visible: it emits `settings.saved`, which the
CAM UI answers with `validateTools()` and `opRender()`, so the op panels rebuild
against the new list instead of showing a stale one until the next reload.

Verified by running the generated script against a mock of Kiri's settings object
seeded with the real `template.tools`: the library comes out as the four bits
above numbered 1-4, `camDrillTool` moves 1006 → 1000, a saved process and an
existing drill op are repointed with it, a second run changes nothing, a renamed
`end 1/16` survives untouched, and a library holding only droppable defaults is
never left empty.

#### Kiri:Moto: the same policy, from the other end

Kiri already ships arcs off and `_KIRI_PROCESS_DEFAULTS` pins them there, so
nothing needs rewriting on the way out. What is left is the *control* — a
checkbox in the CAM **expert** group that could turn back on the one thing the
height map cannot compensate, leaving the panel and the exported file quietly
disagreeing.

Only the three arc rows go: `arc output`, and the `arc tolerance` /
`arc resolution` inputs that render while it is ticked. The group's other two
settings — `skip shadow` and `rounded corners` — have nothing to do with arc
output and stay. (An earlier version hid `#cam-expert` whole and took those two
with it. It shouldn't have.)

**Why a class and not a selector.** The rows carry no ids. They could be matched
on their `title` text, but that is English prose from `lang-en.js`, and a
selector that silently stops matching is worse here than one that never matched.
So they are found through Kiri's own handle: `api.ui.camArcEnabled` is the
`<input>` that `newBoolean()` returned, and its parent is the `.var-row` wrapper
(`inputs.js`: `row.setAttribute("class", "var-row")`). Language-independent, and
it survives any reordering of the group.

**Why a class and not `style.display`.** Inline display is exactly what Kiri
writes on every panel refresh — `row.style.display = hide ? 'none' : ''`, five
call sites in `inputs.js` — so an inline style of ours would be erased the first
time the panel refreshed. A stylesheet rule carrying `!important` outranks a
non-important inline style, so the marking survives every one of those writes.

Verified after forcing `conf.update_fields()`, `uc.refresh(1)` and a device
selection: the three arc rows stay `display: none`, `skip shadow` and
`rounded corners` stay 19 px tall, and the `expert` header is still there.

### Footer links

`ui/cam_links.py`. The status bar's right-hand strip — Version, Home, AGPLv3,
Doc, Help, GitHub, Support Development — is the tool's only outbound signposting,
and inside this webview almost none of it worked:

| Link | Before | After |
|---|---|---|
| Version | system browser (already fine) | unchanged |
| Home | dead — `/` is outside the bundle's nav base, so `ToolPage` refuses it | `https://cam.eltryus.design/` |
| AGPLv3 | dead — `target="_blank"` | the repository's `LICENSE` |
| Doc | dead — `target="_blank"`; had it worked it would have replaced the CAM tool with in-bundle docs, in a webview with no back button | the same docs online |
| Help | dead — `target="_blank"` | GitHub issues |
| GitHub | dead — `target="_blank"` | the repository |
| Support Development | opened an in-app modal | the Ko-fi page that modal offers |

`target="_blank"` is the main culprit: a new-window request goes to
`QWebEnginePage.createWindow()`, whose default implementation returns nothing, so
QtWebEngine drops the navigation. Each link is therefore rewritten to an absolute
`https` URL with `target` removed, which turns the click into an ordinary
main-frame navigation to a foreign origin — exactly the case `ToolPage` already
hands to `QDesktopServices.openUrl`. The browser opens and the CAM tab stays put.

Destinations come from the project's own metadata (`sitemap.xml`,
`CITATION.cff`), not from guesswork, and links are keyed on their literal `href`
attribute rather than their text, which carries a version number.

This is deliberately the opposite of hiding: EasyTrace is AGPL-3.0, its NOTICE
asks that the software be credited, and most of these links *are* that
attribution — they now reach the real source, licence and documentation.

Verified against the real markup plus `ToolPage`'s navigation rule: before, five
of the seven were dead and Support opened a dialog; after, all seven resolve to a
system-browser open.

### Origin marker

`ui/cam_origin_marker.py` restyles the marker Kiri draws at machine zero: red,
unlit, double-sided, half again as large.

It is the one thing on screen that says *this is where the machine thinks zero
is*, and upstream draws it `0xcceeff` on `0x88aadd` at `opacity: 0.5` — a pale
blue on a slightly less pale blue, over a light grey bed. Easy to lose against
the platform grid, easy to mistake for part of it.

Two further problems live in the same four lines of `moto/space.js`. **It
disappears from below**: the marker is flat geometry (four `CircleGeometry`
quadrants, a `RingGeometry`, two crossed planes) and both materials leave `side`
at the default `FrontSide` — upstream has `// side: THREE.DoubleSide` sitting
commented out in each. Flat single-sided geometry seen from behind is not dim,
it is *gone*. Nothing else is responsible: the bed is `FrontSide` too with
`depthWrite: false`, so it occludes nothing. **And it would read wrong even
double-sided**, because both materials are `MeshPhongMaterial` and the light is
above the bed, so the back faces would shade to near black. A reference marker
is a symbol, not a surface; `MeshBasicMaterial` renders it the same red from
every angle.

Done by wrapping, not patching. `space.platform.setOrigin` is public — it is
what `kiri/app/platform.js` calls — and it rebuilds the whole group on every
change, so wrapping it catches every rebuild rather than racing them. The group
itself is internal (`grid.origin.group`), but `setOrigin` hands it to the
equally public `Space.scene.add`: wrapping that for the duration of the call
captures exactly the object upstream just built, with no traversal and no
guessing which group in the scene is the marker. A marker already on screen is the one the operator is looking at, and reaching
it means getting it rebuilt — but re-running `api.platform.update_origin()` is
not enough to do that:

```js
let unchanged = x === or.x && y === or.y && z === or.z && show === or.show;
if (unchanged) { updateDraws(); return; }
```

Recomputing the same origin recomputes the same numbers, so `setOrigin` returns
early and nothing is rebuilt. That is exactly how it failed in practice: every
*later* rebuild came out red, and the marker already on screen stayed pale blue
until something happened to move the origin. So the state is changed first —
`setOrigin(0, 0, 0, false)` differs from the live `show: true`, taking the
not-unchanged path and dropping the old group, and the update that follows then
differs from *that* and builds a fresh one through the wrapper.

Size goes on the *children*, not the group: upstream keeps
`grid.origin.scale()` on the group and re-runs it on every camera move
(`group.scale.set(s, s, s)`), so anything written there is overwritten within a
frame. A scale on each child multiplies with the group's and survives.

**In rotary the marker also moves onto the axis.** It was being drawn one
radius above where zero is. `platform.js` halves the origin for display, in
indexed mode and only in indexed mode — `let oz = camStockIndexed ? z / 2 : z`
— which lands it on the *skin* of the blank. Nothing else agrees:
`work/export.js` computes `offset.z = camOriginTop ? origin.z - zmax :
origin.z`, which comes out at 0.01 mm, and the rotary work frame zeroes on the
axis, since `updateAxisMode` forces `camZAnchor = "middle"` whose indexed branch
is `widget.setTopZ(zdelta, true)` with `zdelta = camZOffset = 0`. The machine
zeroes on the axis; the marker sat on the surface.

So the wrapper passes `z = 0` in indexed mode. Only the *drawn* position is
touched — `settings.origin`, which is what `work/export.js` reads, is computed
in `platform.js` and never passes through the wrapper, so no G-code changes.
The stock-origin handles are derived from the marker (see **Stock origin
picking**) and follow it there, so dot and marker stay coincident.

That puts both inside the material, which is where a rotary zero belongs and
exactly where depth testing would hide them, so the marker's materials and the
handles' get `depthTest: false` and a high `renderOrder`. Clicking is
unaffected: `api.feature.hovers` scopes the raycast to the handle array, so the
stock is never a candidate to be hit first — burying a handle costs visibility,
not reachability.

### Panel groups and what belongs to which

Kiri's left panel is six collapsible groups, and a control's DOM home is *not*
what decides whether a collapse hides it. `newDiv` stamps `div._group =
groupName` when the control is built, `newGroup` flips `hidden[group]` on a
header click, and `setMode` reads the tag:

```js
let hidn = hidden[el._group] === true;
el.setVisible(!hidn && hmod && show && xprt && disp);
```

So anything we move, inject or hide has two facts to keep straight, and they
come apart in different ways:

* **Moved rows keep their old allegiance.** `camStockIndexGrid` and
  `camStockIndexed` are built under `group:"cam-stock"`, so after being lifted
  to the top of the panel they still vanished whenever **Stock** was collapsed.
  Both movers now clear `row._group` on every placement pass. See the rotary
  section for the detail.
* **Injected roots have no allegiance at all.** The stock shape selector and the
  origin picker are our own elements, never registered with `addModeControls`,
  so Kiri neither hides nor shows them and each has to mirror its group itself.
  Doing that from a handler on the group header alone is a single point of
  failure: miss it once and the panel is stuck in whatever state it was last
  left in, with nothing to correct it. Both now also re-read the group on
  `settings.saved` / `boolean.update` / `widget.add` / `platform.layout` /
  `init-done`, which makes them self-correcting — whatever happened, the next
  event puts them back in step.
* **`#cam-origin` is a static div in `index.html`**, present from the first
  millisecond, so waiting only on the host let the origin picker install into an
  empty box: appended *above* the header rather than below the rows, with no
  `.set-header` yet to hang the collapse handler on and no row to read the group
  state back from. It now waits for `api.ui.camOriginOffX` and a header, the way
  `ui/cam_stock.py` already waited on its own group's keys.

The audit behind this is two harnesses. `test_panel_groups.js` builds the whole
panel with Kiri's collapse machinery transcribed from `inputs.js` and runs every
injected module against it, collapsing each group in turn. That is a model, and
a model agreeing with itself proves nothing about the app, so there is also
`audit_live.py`: it boots the real vendored bundle behind the real server with
the real injected scripts, switches to CAM, clicks every group header and
reports what actually disappears. Two things it needs that are easy to lose an
afternoon to — the mode must be set to CAM or every CAM group container is
mode-hidden and the whole panel reads as collapsed, and visibility has to be
measured *inside* `#panel-slide`, since the panel itself lives in a drawer that
is closed until hovered. Offscreen it also needs a software rasteriser
(`QTWEBENGINE_CHROMIUM_FLAGS=--use-angle=swiftshader --enable-unsafe-swiftshader`),
and even then WebGL init is flaky enough to want a retry.

The live result, group by group: **Tabs** hides its own four rows; **Stock**
hides `camStockX/Y/Z` and the shape selector; **Limits** hides the Z rows and
both feed ceilings; **Output** hides its own; **Origin** hides the three offsets
and the whole picker; **Expert** hides its own. Hidden at rest, all
deliberately: `camStockOffset`, `camStockRound`, `camToolInit`, `camOriginTop`,
`camOriginCenter`, `camArcEnabled`. No collapse touches the two lifted switches.

### Stock origin picking

`ui/cam_stock_origin.py` gives Kiri:Moto's CAM origin the gesture Fusion has:
put the origin on any point of the stock, by clicking it on the stock box in
the 3D view.

Kiri's own origin model is two checkboxes and three numbers — **origin top**
and **origin center** choose which corner of the stock it measures from, and
`camOriginOffX/Y/Z` shift it. Four anchors, no choice of corner. Its group also
holds a **select** button (`mode/cam/app/cl-origin.js`) which *is* the right
gesture, but it only offers points it can find on the *model*: the centres of
circular traces and the endpoints of open ones. On a rectangular block it offers
nothing, and it never offers a point on the stock.

What is installed is both halves of the Fusion behaviour, over Kiri's own
fields — nothing under `vendor/` is touched:

* a **stock point** grid in the Origin group: the nine XY spots laid out as they
  sit on the bed (back row at the top) plus a segmented top / middle / bottom
  for Z, so all 27 points — eight corners, twelve edge midpoints, six face
  centres and the body centre — are one or two clicks away. The Z row is drawn
  as a single joined control rather than three buttons, because three loose
  buttons read as three independent toggles; the behaviour was always exclusive
  (asserted across all 108 anchor/spot combinations), only the appearance
  suggested otherwise. It stays "middle" rather than "center" because the XY
  grid already has a *center*, and one panel using both words for different
  axes is how the wrong one gets set;
* **pick on stock**, which drops a handle on each of those 27 points on the
  stock box already drawn around the model. Hovering lights a handle, clicking
  one sets the origin there, `[esc]` cancels. The handle the origin is already
  on stays lit, so the pick shows the current origin as well as changing it.

Neither needs new machinery. Kiri publishes `mouse.hover` and `mouse.hover.up`
on the public api (`app/init/input.js`) and raycasts against
`api.feature.hovers` (`app/mouse.js`) — which is exactly how its own
`face-tool.js` picks faces. The handles are ordinary meshes in
`api.const.SPACE.world`, so the interaction is upstream's, borrowed rather than
rebuilt.

**The arithmetic**, because three coordinate frames are involved and they do not
agree:

* `WORLD.rotation.x = -PI/2` (`moto/space.js`), so world `(x, y, z)` draws at
  scene `(x, z, -y)`;
* the stock box is a unit cube scaled to `stock.{x,y,z}` at world `stock.center`
  (`cl-stock.js`), so it spans `center ± half` on each world axis;
* the origin marker is placed by `setOrigin(x, y, z)` at scene `(x, z, y)`, so
  **setOrigin's y is world y negated**.

Writing a point as fractions `(fx, fy, fz)` of the box on the *world* axes and
reading `update_origin()` (`app/platform.js`) back through that gives

```
camOriginOffX = camOriginCenter ? (fx - 0.5) * stock.x : fx * stock.x
camOriginOffY = camOriginCenter ? (fy - 0.5) * stock.y : fy * stock.y
camOriginOffZ = camOriginTop    ? (fz - 1.0) * stock.z : fz * stock.z
```

Checked by simulation over 432 placements (four stock sizes x four anchor
combinations x 27 spots): every handle coincides with where Kiri then draws the
marker, worst error 7e-15 mm. It also reproduces the one case upstream
documents — all offsets zero with the pinned anchor is `fx=0, fy=0, fz=1`, the
stock's front-left top corner. World `fy = 0` is the *front* (scene +Z, toward
the camera), which is why the grid's bottom row is the front row.

Because the pick is stored as offsets recomputed from fractions, it survives a
stock resize: the origin stays on the corner it was put on instead of drifting
into the block. With round stock (patch 0006) the nine XY spots move onto the
circle — centre on the axis, the other eight out at the rim on their own
bearing — so no handle hangs in air off a corner with no material under it.

**Upstream's two anchor checkboxes are removed.** `camOriginTop` and
`camOriginCenter` choose which corner of the stock Kiri measures from — top
face or bottom, centre or front-left. They are *anchors*, not positions, and
the offsets this module writes are computed against whichever is set:

```js
camOriginOffX = camOriginCenter ? (fx - 0.5) * stock.x : fx * stock.x
camOriginOffZ = camOriginTop    ? (fz - 1.0) * stock.z : fz * stock.z
```

so the picker reaches every spot from either anchor and neither checkbox
changes anything an operator can observe. Both are pinned by `_ORIGIN_DEFAULTS`
besides, making them controls that cannot be changed — tick one and the next
load puts it back. The pins are untouched and the arithmetic still reads them
live; only the rows go, so the origin is chosen in one place instead of three
that disagree about what they mean.

Asserted rather than argued: with each flag pinned true *and* false, the height
buttons put the origin at the same three heights, and the grid spots land at
the same absolute XY.

**Upstream's *select* / *reset* row is removed too.** Kiri's Origin group also
carries a *select* button (`cl-origin.js`) that scatters small red boxes on
features it finds in the **part** — circle centres and open-trace endpoints —
and sets the origin to whichever is clicked, beside a *reset* that zeroes the
offsets.

Two origin pickers in one group is already a lot to ask of a panel, and this
one is worse than redundant on rotary stock: it derives every point from the
widget's *unrotated* trace geometry, with `camStockIndexed`, `indexRad` and
`setAxisIndex` appearing zero times in the file. On a turned bar its points
land where the part would be lying flat — floating below the stock, which is
how it was noticed — and `pointHoverUp()` computes offsets from those same
displaced points without zeroing Y as indexed work requires, so a click sets an
origin wrong in two axes, quietly.

Nothing is lost with *reset*: it writes `camOriginOffX/Y/Z = 0`, and with the
pinned anchors that is the stock's front-left top corner — the same thing
clicking **front left** and **top** does, which the tests assert. What does go
is snapping to a *part* feature such as a hole centre, which this module has no
equivalent for; that is the trade, and the button offering it was the one
dropping red boxes under rotary parts.

Indexed (rotary) stock picks **along the bar only**. Turning the blank changes
what an origin can mean: `update_origin()` forces `origin.y = 0` and patch 0004
applies the same rule to the emitted start point, because the machine parks Y
at the rotary working position and locks it there. Z is the axis for the same
reason — the stock centre moves to z=0 and depth is a radius, not a height off
a face. What is left is X, where along the bar the origin sits, which is the
thing worth setting on a rotary job and which reaches the G-code untouched
(`originx` in `work/prepare.js`, with no indexed override).

So in rotary the nine XY spots collapse to the three along the axis, the Z row
stands down, and the handles are three dots on the centreline. They are
**hidden rather than greyed**: six dead cells around three live ones read as a
picker that is switched off, and the first thing anyone does is click the
corner they use on flat stock and conclude rotary has no origin picking at all
— which is exactly what got reported. Shown as a single row of three under the
caption *point along the bar*, the panel says what the mode offers. Hidden
spots are disabled too, so nothing that reaches them can act. The offsets
follow: X as always, Y and Z written as zero rather than left to drift in from
whatever a previous flat job put there. The gated spots carry the reason as a
tooltip rather than simply not responding.

**Two things had to change around it.** Kiri draws an origin marker but
`controller.showOrigin` defaults to false (`conf/defaults.js`), so out of the
box nothing in the viewport says where the origin is — half of why the origin
controls read as "top and center and that's it". It is switched on, seeded
rather than pinned (it is a display preference, not a machine fact): once per
browser profile, recorded under `piccola-kiri-show-origin`, and the operator's
to switch off for good afterwards under Preferences > display.

And `applyPins()` used to re-apply `camOriginOffX/Y/Z = 0` on every load and
every `device.selected`, which meant any origin the operator set — typed, or
picked with Kiri's own select — was silently reset at the next launch, and
immediately if Setup > Machines was opened. Those three fields left
`_ORIGIN_DEFAULTS`; the two anchor flags stay pinned, since the picker measures
from them and reads them live either way.

**Rotary withdraws the front/back column, and nothing else.** The grid's middle
row (left, centre, right), all three heights and the picker stay.

`platform.js` ends `update_origin` with `if (isIndexed) origin.y = 0`, and
`work/prepare.js` pins the start point to that plane, so "front left", "left"
and "back left" all name the same origin. Three buttons that cannot differ are
worse than one — they invite a choice and then ignore it. Hidden rather than
greyed: six dead cells around three live ones read as a picker that is switched
off, and the first thing anyone does is click the corner they use on flat stock
and conclude the whole thing is broken.

Only the *offer* narrows. The arithmetic is the one flat stock uses, so a
surviving button means there what it means here — asserted across the nine
remaining spot/height pairs. Getting that assertion right needed the clicks in
XY-then-height order: the default origin is front-left-top, a spot rotary does
not offer, so `currentOr` starts from a different place in each mode and the
other order carries that difference into the result rather than comparing like
with like.

An earlier version went much further — three spots along the axis, no height
row, Y and Z written as zero. Every step was defensible alone, and the result
was a picker whose shape depended on a checkbox elsewhere in the panel and a
marker sitting somewhere the flat marker never does.

### Stock offset

`ui/cam_stock.py`. Kiri's Stock group ships an **offset** switch that does not
enable or disable an offset — it changes what the *width* / *depth* / *height*
fields mean (`platformUpdateStock`, `app/platform.js`):

```js
let stock = { x: camStockX, y: camStockY, z: camStockZ };
if (camStockOffset || (stock.x * stock.y * stock.z === 0)) {
    stock.x += bounds.max.x - bounds.min.x;   // ... and y, z
}
```

On, they are margins *added around* the part; off, they are the stock's
absolute size. Kiri ships it on with all three fields at 5, so turning it off
leaves a **5 × 5 × 5 mm** stock — for any real part, a cube buried inside the
model, drawn but invisible, which reads as the stock box having disappeared.
That is how it was found.

Nothing is lost by pinning it on. The stock is centred on the part's bounds
either way (`stock.center` is the bounds centre regardless), so the two modes
differ only in which number you type: a 100 mm blank around a 60 mm part is
`100` absolute or `40` as a margin. The trade, stated plainly, is that
describing a **fixed blank** now means subtracting the part size first — once,
against a switch that otherwise costs a confusing failure every time it is
touched.

So `camStockOffset: True` joins the pinned process fields and the row goes with
it.

**The group leads with the shape**: a segmented **rectangular / cylindrical**
pair, rather than a "round" modifier checkbox, because that is a choice between
two things. It drives `camStockRound` by clicking Kiri's own checkbox — what
`updateSettingsFromFields()` reads back — and that checkbox's row is hidden.

**Which way a cylinder lies is not a second question.** Turning the blank about
X is what a bar on its side *means*, so the orientation follows the rotary
switch and has no control of its own:

| rotary | the cylinder |
|---|---|
| off | stands on the bed, round face perpendicular to Z, cleared as a circular footprint |
| on | lies along X, turned about its axis |

An axis selector beside the rotary switch would be a second way to say the same
thing, and two controls that must agree are two controls that can disagree.
That also settles what Kiri's model cannot express: its stock is a prism — one
2D footprint extruded to a single `stock.z` — so a lying cylinder has no 3-axis
representation better than its bounding box, and does not need one, because
lying down *is* the rotary case, where `prepare.js` clears the swept envelope
with `hypot(stock.y, stock.z)/2` and `stockIsRound()` correctly stays false
since the footprint really is a rectangle.

**A cylinder is a diameter and a length**, so with cylindrical chosen the three
box fields are replaced by *side* (material beyond the part, all the way round)
and *height* — or *length* when the bar is lying down.

Both are margins in the same sense the box fields are, so **the numbers carry
across a shape change**: a box with 5 mm all round and 7 mm of height becomes a
cylinder with 5 mm all round and 7 mm of height, and back again. Leaving the
cylinder writes the plain margins rather than the derived per-axis ones, which
encode a diameter and would be nonsense as box dimensions. The same re-derivation
happens when rotary moves under a cylinder — the margins mean the same, the axes
do not.

**Margins start at zero.** Kiri ships 5 mm on every axis, so a part arrived
wrapped in stock nobody asked for. Zero is the honest default — the stock is the
part until someone says otherwise — and it is *seeded*, not pinned, because a
margin is a fact about the job: once the operator sets one it is theirs.
Recorded under `piccola-kiri-stock-zero`.

*When* it is seeded is the whole of whether it works, and the first attempt got
it wrong: written at install, it raced Kiri restoring the stored profile over
the top, and because the marker was written by the same call, the one shot was
spent losing that race — the old margins came back and nothing was left to
correct them. So it now runs on `init.two`, emitted from `init_sync()` under the
comment "SECOND STAGE INIT AFTER UI RESTORED" and reached after
`restoreSettings()`, and again on `init-done` at the end of the same function.
The second visit is what writes the marker, and only if it finds the zeros still
in place; anything that put the old values back gets zeroed again and the shot
stays unspent for the next load. Nothing later than those two is listened to —
`settings.saved` would fire while the operator works, and the seed must never be
in a position to erase a margin somebody typed.

The seed also **skips round stock**. A cylinder's margins are neither a default
nor the operator's: they are derived from the part so the circle circumscribes
it, and zeroing them collapses the stock inside the model until something forces
a re-derive. The box margins a cylinder was seeded from are carried in the
side/height fields regardless, so nothing is lost by leaving them alone.

**Showing a row clears its inline display, not just the class.** Kiri writes
`row.style.display` itself when it refreshes group visibility, and an inline
`none` outlives our class being removed — which is how selecting *rectangular*
could come back without its width/depth/height fields. The stub could not see
it either, because its elements had no `style` object at all; that gap is
closed and the case is asserted.

**The rotary re-derive is deferred.** Enabling rotary already runs
`updateAxisMode()`, which re-wraps the stock and every widget, and the cylinder
re-derivation fires from inside that work — putting an `update_bounds()` and a
`save()` in the middle of it, which is what made the switch slow. It now runs
once, after, collapsed if several events land together.

The selector is inserted as the first **row** of the group — not its first
child. `newGroup()` appends a `.set-header` before any row, so inserting at
`firstChild` puts the selector above the header: outside the collapsible
section, with the header pushed down. Appending is no good either, since the
three box rows show and hide with the shape and a selector below them walked up
and down by three rows every time it was used.

**Leaving the cylinder writes the plain margins *before* the shape flips.** The
box fields are revealed by the flip and still held the derived, diameter-encoding
values at that moment, so they showed 12.1 / 32.1 for a frame before being
corrected — a visible flicker on every switch. Values first, commit after.

**The stock-point picker collapses with its group too** (`ui/cam_stock_origin.py`),
by the same mechanism and for the same reason: the grid, the height row and
*pick on stock* are injected, so the inputs module's `refresh()` never reaches
them and they stayed on screen with the Origin group shut. The X-offset row —
Kiri's, in that group, never touched by this app — is read for the `hide` class
and the block takes the same.

**Collapsing takes the selector with it.** Kiri's header collapses its own rows
through `addModeControls`, marking each with the class `hide`, driven by the
inputs module's own `refresh()` — a path that never reaches an injected row, so
the selector sat there alone with the group shut. It cannot be registered from
outside (the group's element list is module-private), so the state is *read*
instead: one of Kiri's rows is consulted for `hide` and the selector is given the
same class. That works precisely because this app hides rows with a class of its
own, so `hide` still means only what Kiri means by it. The header's handler is
wrapped to re-sync after upstream's runs.

**Deriving is not optional at load.** The cylinder's margins are derived from
the part, so they go stale when the part does — including on first load, where
nothing had derived them at all. `min(stock.x, stock.y)` is then the part's
*shorter side*, so the stock came out smaller than the model and only corrected
itself when a shape switch forced a re-derive. It now runs whenever the part's
size changes, install included.

*side* is not written straight through, because **the part has to fit**. Patch
0006 takes the diameter as `min(stock.x, stock.y)`, and stock dimensions are
the part's bounds plus a per-axis margin — so a margin typed into width and
depth gives a circle of `min(bw, bd) + margin`, and any part that is not square
pokes out of it. The enclosing circle of a rectangular footprint has the
diameter of its **diagonal**. So the margins are derived:

```
vertical    D = hypot(bw, bd) + side  ->  x = D - bw, y = D - bd, z = height
horizontal  D = hypot(bd, bh) + side  ->  y = D - bd, z = D - bh, x = length
```

making `stock.x == stock.y == D` (or `y == z` lying down), so the `min()` the
worker takes is the diameter meant and the part is inside by construction —
`side = 0` gives the smallest cylinder that contains it. Margins cannot come
out negative, since `hypot(a, b) >= a`. The field labels still read width/depth/height, now meaning "beyond the
part"; Kiri's own tooltip already says so ("0 defaults to part size"), and
renaming them is a separate decision from removing the switch.

### Tool init

`ui/cam_toolchange.py`. Kiri's Output group ships a **tool init** switch
(`camToolInit`, default **true**) — upstream's "add tool change with first
operation". The exporter reads it once:

```js
if (!laserOp && (spro.camToolInit || toolChanges > 0)) {
    filterEmit(cmdToolChange, { ...consts, spindle: newSpindle });
}
```

The first operation always trips the surrounding `changeTool` (`pos.t` starts
unset), so with the default on, every program opens with the device's
tool-change macro — `M6 T{tool}` on `Any.Generic.Grbl`.

Piccola has one spindle and no changer, so the switch has no meaningful "on"
position: it can only add a line describing a machine this is not. Nor is that
line inert — `M6` is not implemented by grbl 1.1, and the desktop app hands the
program to the firmware as-is, with no sender in between to strip it the way a
hosted sender would.

So `camToolInit: false` joins `_KIRI_PROCESS_DEFAULTS` — a fact about the
machine, re-applied on every load so a saved profile cannot bring it back — and
the checkbox is removed with it, because a control whose value is pinned is a
control that lies about being adjustable. The row is *marked* rather than
styled directly, sharing `ui/cam_arcs.py`'s `HIDDEN_ROW_CLASS`: Kiri writes
inline `row.style.display` when it refreshes group visibility, and only a
class carrying `!important` outranks that.

**This covers the preamble only.** A genuine multi-tool job still emits the
macro at each later change, through the `toolChanges > 0` half of the
condition, which no setting reaches — deliberately, since a program that really
needs a different cutter part way through should say so rather than silently
carve the rest with the wrong one. That is the case the tool-change note above
flags as unconfirmed against this firmware; nothing here changes it.

### Both ends of the animation bar

`ui/cam_anim_end.py` adds a **jump to the end** button to the animation bar and
moves the existing restart next to it. Seeing the finished cut is the answer to "will this
leave what I meant to leave", and the only routes there were watching the whole
job or dragging the timeline to its last pixel. The bar opened with a *restart*
button (`fa-fast-backward`) and had nothing answering it at the other end; this
is its mirror, same shape and opposite direction.

It works *through* the timeline rather than around it: the button sets
`anim.timeline` to its maximum and fires the element's own `onchange`, the same
path a drag takes. So everything `seek()` already knows applies unchanged — the
fast pass rather than a real-time replay (the cut is destructive, so the stock
at the last record exists only by having carved every record before it: there
is no jumping to the end, only arriving quickly), and it stops there rather
than playing on, because 0008's `seekThenPlay` returns without a follow-up once
the cursor is at the end and 0007 has already restored the transport to *play*.

That leaves the module owning nothing but a button, and needing no patch: the
insertion point is `api.ui.anim.speed` and the action is expressible entirely
through the timeline's public interface. Kiri rebuilds the animator's handlers
on every setup, but only for controls it created, so this one keeps working
across replays without being re-bound.

**Restart moves to join it**, and is retitled **reset preview**. Upstream puts
it at the far left, ahead of play — a reasonable place for "start over" when it
is the only such control, but with a jump-to-the-end at the other end of the
transport the two belong together: one gesture is "show me the beginning", the
other "show me the end". The wording changes because "restart" reads as "run it
again" and it does not run anything; `replay()` clears and rebuilds the
animation at record 0, paused. The node is *moved*, not rebuilt, so Kiri's own
`button.replay.onclick = replay` still finds it — that binding is by reference,
not by position. The bar now ends:

```
[▶] [⏸] [⏭ single step] [⏩ toggle speed] [⏮ reset preview] [⏭⏭ jump to the end]
```

### View bar

`ui/cam_views.py` puts a small bar over the top right of the viewport: the six
standard views, *home*, *fit*, and a perspective/orthographic switch.

All of it already worked and none of it could be found.
`Space.view.top/bottom/front/back/left/right/home/fit()` are public methods that
animate the camera to a canonical position; upstream binds hotkeys to some of
them — `t` top, `f` front, `b` back, `h` home, `F` fit, `z` reset — and leaves
left, right and bottom with no key at all, with no visible control for any of
them anywhere. Squaring up on a face is the ordinary way to check a toolpath
against a feature, and orbiting until it looks straight is not the same as being
straight.

The projection switch existed too, as a checkbox at the bottom of Preferences.
That is a fine home for a preference and a poor one for a control you flip while
looking at a part: orthographic is how you read whether two faces line up,
perspective is how you read depth, so which you want changes several times in a
session.

No patch is needed for any of it. `api.const.SPACE` *is* the moto space module
(`kiri/app/api.js` puts it in `api.const`), so `api.const.SPACE.view[name]()` is
the same call the hotkeys make.

**The projection switch drives Kiri's checkbox rather than the camera.** It
writes `api.ui.ortho.checked` and invokes that element's own `onclick`, which is
`preferences.booleanSave` — and that saves `controller.ortho`, emits
`boolean.update`, and calls `space.view.setProjection()` itself. Going through
it means the setting persists, the Preferences dialog stays in step, and one
code path decides what the camera is. Assigning `checked` before invoking rather
than calling `click()` is deliberate: a toggle acts on whatever state the
checkbox holds, so if it had drifted out of step with the camera it would switch
the wrong way.

**Reading goes to the camera, not the preference.** `space.view.getProjection()`
is what is actually on screen, so a disagreement between the two shows as the
truth. The neighbouring `Space.isOrtho()` / `Space.isPerspective()` helpers are
unusable: both test a bare `type` that exists nowhere at module scope — a
leftover from when `setProjection`'s parameter was in scope — so in a module
either one throws `ReferenceError` on the first call. Neither has a caller
upstream, which is presumably why nobody has hit it.

Swapping the camera repeatedly is safe. `setProjection` saves the view position
and target, builds the new camera in the same place, rebuilds `viewControl`
around it, restores the position and disposes the old one. The only place in
Kiri that holds a camera is `face-tool.js`, and it re-reads
`space.internals().camera` each time, so nothing points at the disposed one.

**Where it sits.** `#mid` is the overlay layer above the viewport
(`position: fixed`, `pointer-events: none`); `#container`, which holds the
canvas, is `z-index: -1` and sits behind everything. The bar hangs off `#mid`
itself, in the bottom **right**, with its right edge lined up with the
operations panel above it.

`#mid` rather than `#mid-mid` for that alignment: `#mid-mid` *stops* where the
side panel begins, so from inside it the panel is unreachable. `#mid` is
`position: fixed` and spans the window, covering both. The inset is measured
every time (`hb.right - panelRight.right`) rather than fixed, since the panel is
a flex item whose width follows its content and the window; with nothing to
align to — panel empty or not yet built, measuring zero width — the inline value
is cleared and the stylesheet's own inset stands, because aligning to a
zero-width box would throw the bar off the edge of the window. Anchoring needs
nothing else: `index.css` opens with `div { position: relative }`, so every div is
already a containing block. What it *does* need is to stay out of the flow —
`#mid-mid` is `display: flex`, so an in-flow child shrinks to its content, and
the first attempt (a zero-height wrapper holding only the absolutely positioned
bar) collapsed to zero width and put 194 px of bar at x = -197. Absolute
positioning means the flex container is never consulted.

**It stacks above Kiri's own bottom furniture rather than under it.**
`#layer-slider` spans the bottom edge during preview, and the animation
transport is `left: 0; right: 0` with centred content — the same few hundred
pixels this bar wants. Both come and go with the preview and the animation, so
the offset is measured, not fixed: `reposition()` reads each one's
`getBoundingClientRect()`, ignores any that measures zero-height (which is what
`display: none` reports, whichever of the several ways Kiri hid it) and lifts
the bar clear of the tallest survivor. A `ResizeObserver` on the pair drives it,
since collapsing to `display: none` is a resize to 0×0 and coming back is
another — so both arrive without having to watch for *how* it happened. The
`z-index: 210` against the transport's 200 covers the frame between the
transport appearing and the reposition landing.

**The tooltip on the preference checkbox was stale.** Upstream's is
`["orthographic display", "requires page refresh"]`, and it does not require
one — `booleanSave` swaps the camera in place. The second half dates from when
`controller.ortho` was read once, by `space.init(container, slider, ortho)` at
startup. It is corrected in `ui/cam_views.py` rather than through
`ui/cam_labels.py` because a language-table rewrite cannot reach it: the title
is stamped onto the element by `newBoolean()` when the control is built. As a
bonus the array no longer reaches `setAttribute`, which had been rendering it
with the comma in.

**Fit leaves more room than upstream's.** `#container` is the whole window —
`top/left/right/bottom: 0` — and every other piece of UI floats above it, so a
fit framed to the window puts the part under the 45 px menubar and under this
bar. `ui/cam_views.py` widens the space's own fit padding at install
(`space.view.setFitPadding`) rather than passing it per click, so the `F` hotkey
and any fit Kiri runs by itself get the same room.

The two projections take `padding` in **opposite senses**, which is worth
stating plainly because getting it backwards zooms in rather than out.
Perspective multiplies the camera distance —
`desiredDistance = maxDim / tan(fov/2) * padding` — so a bigger number stands
further back. The orthographic branch divides by it, under upstream's own
comment *"keep historical fit padding semantics: lower padding => more margin"*.
So the same factor is multiplied into one and divided into the other: 0.5 → 0.59
and 0.9 → 0.763. The base values are repeated in `ui/cam_views.py` because they
are module locals in `moto/space.js` with a setter and no getter.

The box is captioned **views**, and carries the View menu's actions. Three of
the five were already there under other names — the menu's *contents*, *home*
and *top* are bound in `init/sync.js` straight to the same `SPACE.view.*` calls
the buttons above make, so duplicating them would add rows without adding
anything. *fullscreen* is deliberately left out: `#app-xpnd` is hidden (see the
hide list) and making the embedded page fullscreen inside the app's own window
is not what such a button appears to promise.

That leaves **focal point**, which is reached by clicking upstream's own menu
item rather than calling its handler. `context-setfocus` runs
`view_tools.startFocus(...)` and `view_tools` is not on the public surface; the
element's `onclick` is, so a click gets the whole interaction — the pick, the
raycast, `api.space.set_focus` — with nothing reimplemented and nothing to keep
in step. If that item is ever missing the button is *disabled* rather than
hidden: a missing button says nothing, a disabled one says the control exists
and is not available.

Labels are words, not icons. A cube with one face shaded says nothing useful
about *back* or *bottom*, whose faces are the ones you cannot see. Casing is
left to Kiri, which renders buttons `text-transform: capitalize`, so they are
written lowercase and come out matching their neighbours.

**The bar has to be allowed to wrap.** It is one long `nowrap` row — transport,
X/Y/Z/A readouts, percentage, timeline, then four view toggles (*transparency*,
*show model*, *stock box*, *path*) — inside a container that centres it
(`#layer-animate { left: 0; right: 0 }`, `class="f-col a-center"`). Centring an
overflowing row overflows it *symmetrically*: as the window narrows the row does
not clip on one side and stay usable, it walks off **both** edges at once,
taking the restart button off the left and all four toggles off the right. They
are simply gone — nothing to scroll, no hint anything was there. Measured
against Kiri's own stylesheet at 820 px: four buttons off-screen.

`ui/cam_anim_end.py` adds `flex-wrap: wrap` and lets the timeline give up width
first (`flex: 1 1 160px`), so a narrow window gets two lines instead of one that
does not fit. Nothing needs to be told the new height: the view bar above
measures what is on screen, so a bar that has just become two rows tall pushes
it up by exactly that much. `test_anim_layout.py` renders the real markup
through Chromium at five widths and asserts nothing leaves the viewport and the
two bars never overlap — with a run *without* the CSS kept as the control, since
a test that cannot fail proves nothing.

### Which render mode is on

*render* offers solid, wireframe and transparent — three states of one setting,
where choosing one turns the other two off — and said nothing about which was
in force. The only way to answer "what am I looking at" was to look at the
model and infer it, which is the question the menu should have been answering,
and it is least reliable exactly when it matters: a part with no internal
detail looks much the same solid as it does with edges on, and transparent at
0.25 against a light bed reads as a slicing artefact.

Remembering the last click would not have been an answer. The same three modes
are bound to keys — `keyboard.js` calls `$('render-solid').onclick()` directly,
which fires no DOM listener — and the mode is restored from storage on boot, so
on a fresh window nobody has clicked anything and one of the three is still in
effect.

`ui/cam_render_menu.py` reads the state instead. Kiri keeps the whole render
mode in local-storage keys and its own keyboard handler decides what is current
from two of them:

```js
let isWireframe = api.local.getBoolean('model.wireframe');
let isGhost = !isWireframe && (api.local.getFloat('model.opacity') ?? 1.0) < 1;
```

so this uses the same test and solid is what is left over. Click, keystroke and
restore all land on the same mark by construction. *toggle edges* is the fourth
item and a different kind of thing — an independent on/off whose name says it
flips something but not which way — so it is marked whenever `model.edges` is
on, alongside whichever mode is current.

`getBoolean`'s second argument is not passed, deliberately. It looks like a
fallback and is not one:

```js
function localGetBoolean(key, def = true) {
    return val === true || val === 'true' || val === def;
}
```

It returns true when the stored value *equals* it, so `getBoolean(k, false)`
answers true for a stored `false`. Upstream's own detection leaves it alone and
so does this; a mutation of the test suite that passes `false` fails eight
assertions.

The mark is Kiri's own `selected` class, which `menubar.css` already styles for
a dropdown row and which the app already uses for "this is the current one" on
`view-arrange`, `act-slice` and `act-animate`. No new CSS, and on our palette it
comes out as the cyan accent at 35% with the navy border — verified in a real
browser by `test_render_mark_visible.py`, which layers the vendor stylesheets
and ours in the app's load order and asks Chromium for the computed background,
because a class nothing draws looks exactly like a working one from the JS side.

**Refreshed on the writes, not on `settings.saved`.** The four menu handlers do
end with `api.conf.save()`, so the event would catch them — but
`state/visual.js#savePreferences` writes `model.edges` and `model.wireframe` and
saves no settings at all, and a mark that silently stops matching in one path is
worse than no mark. The keys *are* the state, so `api.local`'s writers (`set`,
`put`, `setItem`, `toggle`) are wrapped and a write to any `model.*` key
schedules a re-read, coalesced through a zero timeout because `render-ghost`
writes three of them in one handler. The wrappers return what they wrapped:
`local.set` returns the stored value and `local.toggle` the new one, and
callers use those.

What it does not follow is `state/visual.js#applyModeState`, which layers a
per-view opacity over the top — what is on screen during slice or animate is
not necessarily `model.opacity`. That is a view's own presentation and it
reverts; the menu reports the mode the menu sets, which is the persisted choice
and the same one the keyboard reads.

### Play at the end of a rotary job

Patch 0007 made play-at-the-end restart the job and fixed both animators. It has
worked on flat jobs ever since. On a rotary one it wiped the finished cut, wound
back to record 1 and stopped there — which is worse than doing nothing, and is
what patch 0013 fixes.

The restart is in two halves: `replayThenPlay()` sets `playWhenReady`, tears the
animation down and rebuilds it, and the run starts later, when the worker says
it is free. That second half has exactly one consumer, in the update handler,
and in `anim-3d.js` it could not be reached:

```js
function handleUpdate(data, reply) {
    if (!data) {
        return;
    }
    ...
    if (playWhenReady && reply && reply.done) { … startRun(…) }
```

The worker signs off with a bare `send.done()` — no data, at four call sites in
`work/anim-3d.js` — and `app/workers.js` delivers a reply as `onreply(reply.data,
reply)`. So the one message meaning "I am free now" arrives as
`handleUpdate(undefined, { done: true })` and is discarded by the first line of
the function. `playWhenReady` stayed true forever, `running` was never cleared,
and a seek waiting on the same reply was never taken either.

`anim-2d.js` has no such early return — it guards each block with `data &&` —
which is why the same fix always worked there, and why this went unnoticed: the
regression test drove the flat animator only. Measured against the real module
on a virtual clock, 20 records, before:

```
worker: cleanup
worker: setup (t=4250ms)
worker: animate2 speed=1 steps=1 from=0 (t=4300ms)
-- after play again -- pathIndex 1/20, running true, playWhenReady true
```

That single step is the one `animate2()` schedules for itself; nothing followed
it. After the fix: `animate2 speed=1 steps=Infinity from=1`, running to 20 again,
at every worker pacing from 1 ms to 1200 ms per step.

**Patch 0007 declared no bundle marker**, so nothing had ever checked that
either half reached the bundle the app serves — the exact gap
`tools/verify_patches.py` exists to close, left open on the patch that needed it
most. It has markers now, and so does 0013.

Two tests cover this, because the source is not what the app loads.
`test_anim_restart_3d.js` runs the real `anim-3d.js` against a worker
implementing `work/anim-3d.js`'s dispatch semantics, and
`test_anim_restart_pack.js` lifts the *minified* handler straight out of both
served packs and drives it with its free variables stubbed. Both were run
against the pre-fix code first: 11 and 10 failures respectively, which is what
makes them worth having.

### What is left on the animation bar

The transport carried four view buttons on its right — *transparency*, *show
model*, *stock box* and *path*. Three are now hidden by
`ui/cam_anim_buttons.py`, leaving *show model*, and they are hidden for two
different reasons that are worth keeping apart.

*stock box* and *path* do not work, in either mode, for four separate reasons —
which is why they looked fine and behaved like dead pixels. *transparency*
works and is withdrawn anyway, on request.

Which animator runs accounts for half of it
([init-ui.js:137](../vendor/kirimoto/src/kiri/mode/cam/app/init-ui.js#L137)):
`env.animVer = env.isIndexed ? 1 : 0`. A flat job gets `anim-2d.js`, a rotary one
`anim-3d.js`, over the same row of DOM buttons.

**Flat — path is never bound.** `button.path.onclick` is assigned in
`anim-3d.js` and nowhere else, and `init-menu.js` builds the button as
`newButton(null, null, …)` — a null action leaves `newButton`'s own onclick a
`switch (typeof action)` with no matching case. It is wired to a function that
deliberately does nothing.

**Flat — stock box calls something that has already returned.** `toggleStock`
emits `cam.stock.toggle`; the single listener does `env.showStock = bool ??
!env.showStock; updateStock()`, and `updateStock` opens with `if (env.isAnimate)
{ …; return; }`. `env.showStock` is mentioned on two lines in the whole CAM tree
— one declares it, the other is that self-flip. Nothing reads it. Both are
*correct* for what a flat animation is: the stock is not a box drawn beside the
job, it is the mesh being carved, and the toolpaths are the stack layers whose
checkboxes sit right above the transport.

**Rotary — the first click is a no-op.** Both handlers call
`api.local.toggle(key)` with no value, and `localGetBoolean(key, def = true)`
answers `val === true || val === 'true' || val === def` — for an unset key,
false. So the first click computes `!false`, stores *true*, and the meshes and
path lines were already visible. This is not read off the source: the test loads
Kiri's real `local.js` and runs the toggle. Unset → click → `true`, nothing
changes; only the second click hides anything.

**Rotary — the path button poisons its own state.** `initPathMesh` computes
`show = (api.local.get('cam.anim.path') ?? 'true') == 'true'` — a *string*
comparison — while `toggle` stores a boolean, and `true == 'true'` is false. So
once the button has been touched, every later animation starts with paths hidden
while the button's own state says they are shown. Only the literal string
`'true'` satisfies both readers.

On top of that neither rotary handler calls `space.update()`, and `moto/space.js`
renders only while `Date.now() - lastAction < 1500`, so on a paused animation a
change that does happen may not be painted until the camera moves.

**Hidden rather than fixed**, which is a judgement about value. All four rotary
faults are reachable from outside — seed both keys as the strings `'true'` so
the first click flips something real and `initPathMesh` agrees with
`getBoolean`, then wrap the handlers to call `SPACE.update()`. What that buys is
hiding the stock and the path trace during a rotary animation, which the layer
checkboxes and *show model* already largely cover. Two buttons needing four
workarounds to earn their place did not earn it; `ALWAYS_HIDE` in the module
says how to reverse the decision.

**And one that worked.** *transparency* is bound in both animators and does
what it says, switching `material.transparent` on the mesh being carved. It is
hidden because it was asked for, so `WITHDRAWN_BUTTONS` is kept separate from
`BROKEN_BUTTONS`: the reasons would not survive being merged into one list, and
only the broken pair is governed by `ALWAYS_HIDE`.

Hiding it leaves the transparency *state* wherever the animator puts it, which
is upstream's default in both modes — `anim-2d.js` applies the stored
`cam.anim.trans` at setup and `conf/defaults.js` ships it `true`, so a flat
animation stays see-through; `anim-3d.js` calls `toggleTrans(undefined, false)`,
so a rotary one does not. That is what the app already did with nobody touching
the button, and it is the answer to "why is the carve transparent with no
control in sight".

`anim-3d.js` also un-hides that button itself on a manifold job
(`anim.trans.classList.remove('hide')`), which is why hiding it once is not
enough. That line runs inside `animate2` *before* it emits `animate`, and this
module listens to `animate`, so the re-assert lands after upstream's removal
rather than racing it — asserted by comparing the two positions in the vendor
source, not by timing.

The `hide` class is upstream's own, used a few lines away to withdraw
`anim.trans` on a non-manifold job, and it is re-applied on the events that
re-enter or rebuild the bar. The bar re-lays itself out around the gap, and
`ui/cam_anim_layers.py` measures the row rather than assuming its height, so the
checkbox row above follows without being told.

### Toolpath layers during animation

Two separate things hid the toolpath layer checkboxes when an animation
started, and fixing one left the other, which is worth recording because the
first fix looked like it had failed.

The destructive one was `STACKS.clear()` in `animate()` — see patch **0011**.
With it gone the toolpath *geometry* stays drawn under the cut.

The second is why the *list* was still missing afterwards. `#layers` — the row
of per-layer checkboxes with the colour swatches — is a child of
`#layer-slider`, sharing that container with the layer scrub bar, and
`slider.hide()` takes both down together:

```js
hide() {
    this.#ui.layers.style.display = 'none';    // the checkboxes
    this.#slider.div.style.display = 'none';   // the scrub bar
}
```

`animate()` calls `api.hide.slider()` one line above the clear, so the row is
hidden by inline style whatever the stacks are doing.

Hiding the scrub bar is right — the animation has its own timeline, and two
scrubbers over one job is worse than one — so `ui/cam_anim_layers.py` clears
just the row's inline `display`, on `view.set` and the events the animation
builds on, plus a deferred pass because some paths build the animation *after*
`view.set` and hide the slider again on the way. There is no need to intercept
`slider.hide()`: its two writes are independent, only one is unwanted, so
letting it run and re-showing the row keeps the bar hidden for free. Outside
animate it does nothing — Kiri owns the row then, and `slider.show()` restores
it.

Two of the four checkboxes are then removed. They are toolpath *display*
layers, not operations — `prepare.js` names the two path kinds
`other: "moving"` and `action: "milling"`, and the animator adds `tool` and
`arrows` alongside them — and four checkboxes for one question is more than it
is worth. `tool` is pinned **on** (watching the cutter is the point of the
animation) and `arrows` **off** (direction arrows over a whole job are noise);
`moving` and `milling` keep theirs, because which of travel and cutting you
want to see is a real question whose answer changes.

Pinned rather than defaulted, because the layer set is rebuilt from scratch on
every slice: `stacks.clear()` empties the registry and `create()` builds it
again, so a value set once would not survive the next generate. It goes through
the public `api.stacks.setVisible(label, bool)`, which sets the checkbox as well
as the materials — that matters, since the checkbox is what a later `setVisible`
or rebuild reads back, and leaving it out of step would let a stale value
return. The rows are found by what their `<label>` says, `newBoolean` having put
the layer name there.

The gate is `api.view.is_animate()` rather than the `view.set` payload, since
that event carries a numeric mode whose constants are not on the public
surface; it is read after the event, by which point `viewMode` is assigned.

### Undo in the workspace

`ui/cam_undo.py` gives Kiri Ctrl-Z / Ctrl-Shift-Z for part placement. There was
none, for two separate reasons.

`app/keyboard.js` has cases for the arrows, `a`, `s` and `l` — there is no `z`
case, so the shortcut was never bound. And there was nothing to bind it *to*:
`app/do.js` is a complete undo/redo stack — action pairs per edit,
`api.doit.undo`, an alert when it runs out — but **no module imports it**.
Nothing loads the file, so `api.doit.undo` stays the `noop` declared in
`app/api.js` (with a `// do.js` comment pointing at the file meant to replace
it), and the `#undo` / `#redo` / `#doit` elements it binds to on `load-done`
are not in `web/kiri/index.html` either. Dead code wired to nothing, in 4.7.2.

No patch was needed to revive it, because everything it used is public: the
transform events carry their own deltas, `api.selection.widgets()` returns the
real widget objects, and those carry the same `_move` / `rotate` / `_scale`
methods `do.js` called. So the stack is rebuilt outside the bundle and
`vendor/` stays untouched — if upstream ever wires its own file back up, the
two do not collide.

The recorded semantics are `do.js`'s, deliberately rather than invented: rotate
inverts by negation, scale by reciprocal (ignoring anything within 1e-4 of 1,
since the size fields fire on every keystroke), and a **move is accumulated
from `selection.drag` and committed once on `mouse.drag.done`** — a drag emits
a move event per mouse-move, so recording those directly would bury the stack
in one-pixel steps. Undo replays through the widget methods, which emit no
events, so an undo cannot record itself: the re-entrancy guard is structural.

What it does **not** cover, since "undo" invites the assumption of everything:
only part placement — move, rotate and scale of what is on the platform.
Adding, deleting or editing a CAM *operation* is not undoable, nor are stock,
origin, tool or settings changes. Typed moves are not recorded, only dragged
ones (both go through the same event, with no way to tell one keystroke's worth
from another's). The stack clears on `widget.add` / `widget.delete` /
`platform.layout`, whose actions hold widget references those events
invalidate — upstream cleared it for the same reason — and it is Arrange-view
only. A focused input or textarea keeps its own native text undo.

### Origin Position

`ui/cam_origin.py` reworks EasyTrace's origin presets. Upstream ships two of the
five spots — *Bottom-Left* and *Center* — plus a separate *Apply Origin* that
saves whatever they or the X/Y offset fields left behind, and nothing indicates
where the origin currently is: both presets stay grey after being pressed and
their status line reads "(not saved)" until Apply.

What is installed instead is one captioned group, **Origin Position**, holding
all five spots laid out the way they sit on the board:

```
Top-Left      Top-Right
Bottom-Left   Bottom-Right
       Center
```

Upstream's two buttons are *moved* into that grid rather than replaced, so their
own handlers and wording survive; the three missing corners are ours, reading
`scene.getVisualBounds()` — post rotation and mirror, the same source
`setOriginToBottomLeft()` and `centerOriginOnBoard()` use, so a rotated board
still offers its own visible corners. With no bounds yet a click is refused with
a status message instead of moving the origin to a garbage coordinate.

**Picking applies.** Each click ends in `applyOffsetAndSetOrigin()` — the method
Apply Origin called — so that button has nothing left to do and is hidden, and
*Reset Unsaved Changes* goes with it: no unsaved state remains for it to undo,
and re-picking a spot is the same gesture as reverting to one. Worth knowing
that applying was never the difference between "the origin moved" and "it did
not": the toolpath pipeline reads `scene.transform.origin` (cam-core.js
`getTransforms()` builds its machine matrix from it), which the presets already
set. Committing rebases the offset fields on the new origin, so they read 0 and
the pick becomes the reference the offsets are measured from.

The X/Y offset fields are untouched and still live — they move the real origin
as they are typed, and Enter commits them. A typed offset therefore reads as
"this spot, plus X/Y".

**The active spot is drawn in the accent colour** (`btn--primary`, the blue
Apply Origin used to have — `--color-accent-primary`, which `ui/cam_theme.py`
maps to the app's navy). Which one that is comes from comparing the live origin
against the board's corners on every transform change, not from remembering the
last click, so typing an offset, rotating the board or loading a different board
clears the highlight on its own: the origin genuinely is not on a corner any
more. The match tolerance is 0.1 µm — far below anything that matters when
placing a board, far above the float noise of the transform maths.

**Opening the first file places the origin.** It goes to the **bottom-left**
corner with that button lit, instead of staying at (0,0) — which for a fresh
session is a corner of the *empty placeholder* board rather than of the real one.
Bottom-left is the GRBL convention and the corner the machine's own axes run away
from.

The pick then stays with the board: if a second file grows the bounds while the
origin is still exactly where this module put it, the origin follows the corner,
so "the lit button is where the origin is" keeps holding as a job is assembled.
The moment the origin is somewhere it was not put — a typed offset, another spot,
a rotation — it is left alone for good. Only two states trigger an automatic
placement, and both are checked against the live scene: no pick yet for this
board, or the pick's corner having moved out from under an untouched origin.

**Nothing in the Board Placement panel is usable until a board is open.** The
five spots, the X/Y offset fields, Board Rotation (field and *Reset Rotation*)
and both Mirror Geometry toggles are `disabled` until at least one isolation,
clearing, drill or cutout operation exists, with the reason on each control as a
tooltip; a click on a spot in that state is refused with the same message rather
than moving anything. The panel says where the board sits and which way round it
is, and with no files upstream falls back to an empty 100×100 default — a corner,
rotation or mirror set against *that* quietly survives into the first real file.
Removing the last file disables the panel again and drops the highlight.

The gate lives in `cam_origin.py` because that module already holds the
transform listener; `cam_rotation.py` owns *when* a rotation commits, not whether
it can be typed. `#apply-rotation-btn` is deliberately left out of the gate: it
is hidden but still clicked programmatically, and a disabled button ignores
`click()` — gating the field it reads is what actually blocks input.

The types are listed explicitly rather than counting operations, so the removed
stencil type could not re-enable the controls if it ever came back. Neither the
gating nor the highlight needs telling when a file arrives: every path that adds
or removes an operation ends in `core.updateBoardBounds()`, which calls
`setBoardBounds()`/`initializeEmptyBoardBounds()` and so notifies the transform
listener this module already holds — no polling, and nothing hooked on the file
side.

Verified against a stand-in with the same DOM and scene API: the caption lands
above the grid in the same `.property-field` wrapper the *Origin Offset* field
uses, the grid comes out in board order with Center spanning both columns, the
emptied preset row is removed, each click moves both `origin` and
`committedOrigin` to the right corner and highlights exactly one button, and a
simulated typed offset clears the highlight. With no operations every button and
both fields report `disabled`, a click leaves the origin at (0,0), a
stencil-only session stays disabled, loading an isolation file enables the lot,
and removing it disables them again with the highlight cleared. Opening the
first file moves the origin to the board's bottom-left and lights that button; a
second file growing the board carries the origin to the new corner; after
picking Center that pick is what follows instead; a typed offset stops the
following and clears the highlight; and emptying the board and opening a new
file starts again at bottom-left.

The **toolbar's vertical rule** goes as well. It divided the view controls from
Help and the theme toggle, and with both of those hidden it was the last thing in
the row — a 1 px line hanging off the right edge with nothing after it to
separate. Removing it also puts *Fit to View* / *Zoom In* / *Zoom Out* flush
right, since the rule's own left and right margins were the gap holding them off
the edge. The rule is scoped to `.cam-toolbar`, because `.separator` is also used
inside dialogs where it still divides something.

The **help markers** — the small `?` glyphs
(`.tooltip-trigger`, `#icon-help`) that `ui-tooltip.js` appends to labels — are
hidden. They come from three places: static labels carrying `data-i18n-tooltip`
(Origin Offset, Board Rotation, Mirror Geometry, the machine settings), the
per-operation fields the parameter manager generates, and the export dialog's own
`attachTo()` calls — so one sits beside almost every label in the tool. They are
hidden everywhere rather than per panel. Since the tooltip is attached to the
marker rather than the label, this does drop that hover help; the labels are
unchanged, and hidden markers leave the tab order too.

The **onboarding highlight loop** in the operations tree is switched off. 12.5 s
after a file is added — and re-armed by every further file —
`ui-nav-tree-panel.js` starts an interval that walks a 2 px accent ring across
the loaded file nodes every 3.25 s (`scheduleSuggestion` →
`startSuggestionCycle` → `cycleHighlight`), as a hint that files are clickable;
it stops as soon as an operation is selected. It is drawn in the same accent
colour as a real selection, so inside this app it reads as the selection moving
by itself between the isolation and drill files — a tab left open looks like it
is being driven by something.

It is turned off through the tool's own `dismissSuggestion()`, which is what a
click calls: it clears both timers, drops the class, and latches a flag that
makes every later `scheduleSuggestion()` a no-op, so files added afterwards
cannot restart it. The PCB preset calls it with a short retry, because the tree
panel is built during UI init and can land after the preset. A CSS rule
neutralises `.onboarding-highlight` as well, so an upstream rename of the method
would cost the timers rather than show a moving ring.

### Board Rotation

`ui/cam_rotation.py` commits the angle **500 ms after the last keystroke** and
hides *Apply Rotation*, which was the only way to do it besides Enter. Once
picking an origin applies itself, rotation was the last control in the Board
Placement panel that sat there looking applied while the board had not moved.

It commits through the tool's own button. `commitRotation()` is a closure inside
`setupRotationControls()` and cannot be called from outside, but it is what
`#apply-rotation-btn` runs — so clicking that hidden button performs the real
commit: scene update, origin-display refresh, write-back of the normalised angle
and the status line. Nothing is reimplemented, and a change to what committing
means upstream is picked up for free. Same hidden-control-driven-programmatically
trick as the export buttons.

Two guards on the debounce:

* **Nothing to do** — the click is skipped when the typed angle already matches
  the board's rotation, so retyping the same number or nudging with the arrows
  does not repeat the status line or fire needless transform notifications.
* **Half-typed values** — for `<input type="number">` a partial entry like `-`
  reads as `""` and `parseFloat("") || 0` is 0, so committing would apply 0° and
  rewrite the field, wiping the minus the moment the timer expired. That is the
  same trap as the origin-offset bug above, so the commit waits for a real
  number.

Enter is upstream's own immediate commit and is left alone; a pending timer is
dropped when it fires so nothing commits twice. *Reset Rotation* spans the full
width of the row now that the button beside it is gone, matching the Center
button in the Origin Position grid.

Verified against a stand-in of `setupRotationControls()`: typing `-12.5` with
120 ms between keys commits nothing until 500 ms after the last one, then once —
field and rotation both `347.5` (upstream's normalisation) with one status line;
at 200 ms nothing has fired yet; a lone `-` never commits and keeps its minus;
and retyping the current angle commits nothing.

### Upstream bug workarounds

`ui/cam_fixes.py` holds corrections applied to the tools at runtime, by wrapping
a public method on the page — never by editing files under `vendor/`. That keeps
the bundles byte-identical and each workaround visible in one place instead of
buried as a diff against a third-party file. Each should be reported upstream and
deleted once fixed.

**EasyTrace: Excellon coordinate format.** Drill files exported by EAGLE landed
at 1/10 scale, clustering every hole near the origin. `FMAT,2` is the Excellon
*format standard* (Format 1 vs Format 2 command syntax), not a coordinate digit
count — but `parseFormat()` reads it as one, setting 2.4 digits and raising
`formatExplicitlySet`, which then suppresses the correct metric default. The
digit count the file actually declares (`000.000` in `METRIC,TZ,000.000` = 3.3)
is never parsed. Both are six digits wide, so padding is identical and only the
decimal point moves — hence exactly 10x.

Measured on a real EAGLE file: X came out 3.86..9.62 mm where it should be
38.60..96.20. The fix wraps `processLine` to apply the file's own declared digit
spec, which works whichever order `FMAT` and `METRIC` appear in and only fires
when a spec is present. Regression-checked across five dialects: the EAGLE file
now parses to 3.3 (first hole 74.630, 68.500 — matching a hand calculation),
metric files without a spec keep the 3.3 default, inch files with `FMAT,2` and no
spec keep the old 2.4 fallback, and an explicit 4.2 spec is now honoured instead
of being overridden.

**EasyTrace: Origin Offset cannot be typed into.** A minus sign will not go into
the X/Y Origin Offset fields — type `-` and it becomes `0.000` as the key lands,
so a negative offset is unreachable. Decimals fare no better: `1` is rewritten to
`1.000` with the caret at the end, so `1.5` ends up as `1.0005`. Only the spinner
arrows work.

The field echoes itself back mid-edit. `ui-core-trace.js` binds `applyPreview` to
the `input` event; it pushes the value into `scene.setOrigin()`, which always
notifies its transform listeners (there is no did-it-change check), and one of
those listeners writes `(origin - committedOrigin).toFixed(3)` straight back into
the input. For `<input type="number">` a partial value like `-` is invalid, so
`.value` reads as `""` → `parseFloat("") || 0` → the origin does not move → the
round trip replaces the `-` with `0.000`.

Upstream already knows the shape of this: the Board Rotation field beside it
guards its own sync with `document.activeElement !== el`. The offset fields never
got the same treatment, so the fix applies that guard to
`updateOffsetInputsWithTracking()` from outside — the preview still follows every
valid keystroke, only the echo into the field is suppressed.
`applyOffsetAndSetOrigin()` is wrapped too: it deliberately calls the same method
to zero the fields against the newly saved origin, and that write must land even
with focus still in the field. Without it the stale `-5` would sit in a field
that now means zero, and the next keystroke would apply that `-5` a second time,
moving the job again. The guard counts depth rather than setting a flag, because
the commit path re-enters through `updateOriginDisplay()`.

Verified against a stand-in reproducing the same call graph: typing `-12.5`
one keystroke at a time leaves `0.001` in the field and an origin of `0.0005`
unpatched, and `-12.5` / `-12.5` patched, with the field correctly back to
`0.000` after Apply.

### Theming

`ui/cam_theme.py` re-points each tool's CSS custom properties at the palette in
`ui/theme.py`, so both read as part of the app and track any future change to
its colours. The stylesheet is injected at document creation alongside the other
init script, so neither tool flashes its native colours first.

Three rules keep this maintainable:

1. **Variables only** — no structural selectors. Variable names survive upstream
   refactors; DOM structure does not, and the vendoring design rests on upgrades
   being a folder swap. The sole exception is a `body` font rule, which is
   inherited rather than matched. It deliberately carries no `!important`, so
   icon fonts (Kiri renders its toolbar with Font Awesome) keep their own family.
2. **Chrome only** — semantic colour is left alone. EasyTrace's
   `--color-operations-*` (isolation / drill / clearing / cutout / stencil),
   `--color-geometry-*`, `--color-semantic-*` and `--color-debug-*` all *mean*
   something on screen; repainting them to match a brand palette would make the
   tools harder to use, not prettier. Same for Kiri's toolpath colours.
3. **Light theme only** — both tools use the same convention (`:root` light,
   `:root[data-theme="dark"]` dark). Overrides are scoped to light, so a user who
   switches a tool to dark gets that tool's own coherent dark theme rather than a
   half-converted hybrid.

EasyTrace defaults to dark, so its theme key (`cam-theme`, from its `config.js`)
is set to `light` before boot — only when unset, so a deliberate choice of dark
is respected. Its loader applies theme values as *inline* styles on
`documentElement`, which is why the overrides carry `!important`: that is what
lets an author stylesheet beat an inline value.

Kiri's palette is a blue-grey ramp (`--blue-00`..`--blue-6`) plus `--main-color`
/ `--highlight` and the generic `--ui-*` widget set; `--blue-3` is the settings
section-header background, the single most visible mapping.

**Out of reach by design:** Kiri's 3D viewport (Three.js/WebGL) and EasyTrace's
board canvas are painted, not styled, so CSS cannot reach them.

#### Kiri's right tray (the one structural exception)

Rule 1 above is why `_KIRI_PANEL_CSS` lives in `ui/pages/cam.py` and not in the
palette bridge: it is layout, and it names elements.

Kiri's right tray is content-sized — nothing sets a width, so it is as wide as
the widest row inside it, **81 px with an empty workspace** (measured offscreen
against the vendored page with the real injection). The section headers cannot
push it out, because `.set-header` is `overflow:hidden; white-space:nowrap`, and
the disclosure marker is an `::after` pinned `position:absolute; right:5px` — so
it paints *on top of* the label rather than beside it. "operation list" is 73 px
of text in an 81 px tray, and the arrow lands **8 px inside the word**;
"objects", at 40 px, escapes it.

| | as shipped | with `_KIRI_PANEL_CSS` |
|---|---|---|
| `#panel-right` width | 81 px | 130 px |
| `operation list` → marker | **8 px overlap** | 41 px clear |
| `objects` → marker | 25 px clear | 74 px clear |
| horizontal page overflow | 0 | 0 |

Two rules, two jobs. `padding-right: 20px` gives the marker a lane of its own —
that is what fixes the overlap, and it holds at *any* tray width, since a label
too long for the space now clips at the padding edge, which is what
`.set-header`'s own `overflow:hidden` was always for. `min-width: 130px` is the
"wider" half: a floor, not a width, so the tray still grows for long operation
names as before, and it only binds when the lists are empty — which is when the
tray is narrowest and the overlap was worst.

### One broken module must not take the rest with it

All sixteen injected modules go into a single `<script>`. A throw at the top
level of one used to stop the whole file: every module *after* it silently never
installed. Measured with a deliberately broken host — one missing DOM method —
**2 of 16** installed and nothing was said.

From outside, that looks like a feature that does not exist, while everything
injected *before* the failure works perfectly. The working half misdirects the
search: `ui/cam_stock_origin.py` (the origin picker) is injected 6th and
`ui/cam_origin_marker.py` (which makes the marker red) is 14th, so a throw
between them leaves a picker that works, a marker that moves correctly, and a
marker that is never recoloured — a combination that reads as "the colour change
is broken" and sends you to the wrong module. Three separate explanations were
offered for exactly that report before this was found.

`_isolate_modules` gives each one its own try/catch. A failure is named — to the
console, where the page subclass captures it, and to `window.__piccolaFailed`.
Continuing past a broken module is right: they are independent by design, each
polling for what it needs and giving up quietly, so fourteen working features
beat none.

With the same broken host: **15 of 16** install, one fails, and it says which.

Recording it is only half the value — unreported, the result is still a feature
that quietly does not exist. `_report_module_failures` reads
`window.__piccolaFailed` back after each load and names it in the CAM status
line: *"injected module failed: origin-marker"*. If nothing is recorded but the
page logged an error — a module can also throw from one of its own retry timers,
where the wrapper cannot reach it — that is shown instead. So the next report of
this shape starts from a module name rather than a symptom.

### When it never gets past "Kiri:Moto is Loading"

That screen is `#curtain`, and it is hidden by the **last statement** of
`init_sync()`. Anything that throws on the way there leaves it up for good, with
nothing to click and nothing said. The usual culprit is the renderer:

```
THREE.WebGLRenderer: A WebGL context could not be created.
  Reason: Web page caused context loss and was blocked
Uncaught TypeError: Cannot read properties of undefined (reading 'scale')
```

— seen repeatedly on this machine while another GL application (Fusion) held
the GPU. Kiri has no recovery for it.

`_watch_load` polls the curtain after every load. A lost context is usually
transient, so a tab still stuck after ~15 s is **reloaded once**; if it is still
stuck after that, the status line says so and names the last console error
rather than reloading forever — a machine that cannot give the tab a context at
all deserves a message, not a loop. The probe is the curtain, not
`window.kiri.api`, which appears long before init finishes and would report
success on exactly the runs that fail.

Console errors are captured by the page subclass for this purpose
(`piccola_errors`, last five). Without them a stuck tab could only say "still
loading", and the actual reason was visible only to someone running the app from
a terminal.

### Saving and opening a project

The two halves of one round trip, and they are named as a pair: **File > save
project** and **File > open project**. Upstream calls them *export workspace*
and *import*, which are not opposites and do not say that the second opens what
the first wrote. Both language keys have exactly one call site each
(`init/menu.js`, `#app-export` and `#file-import`), so the rename cannot reach
anything else — `ui/cam_labels.py`.

Renaming the language table is not enough on its own for a *menu* item, which
is what made this look like it had not been applied. `menuItem()` resolves the
text once, at build time — `resolved = lk ? tr(lk, text) : text` — so rewriting
`LANG.current` afterwards changes nothing already on screen, and the catch-up
pass in `ui/cam_labels.py` only walked `.var-row` and `.cam-pop-op` labels,
neither of which the menubar is. The settings renames worked; these two did
nothing visible.

It now also rewrites by key: `menuItem` leaves `lk` on the label it builds, so
`[lk="rc_xpws"]` finds it exactly, with no text matching and no risk of touching
prose. The text walk stays for controls built from a literal string, which carry
no key at all.

**What the narrower name gave up, and how it came back.** `#file-import` also
accepts STL, OBJ, 3MF, SVG, DXF, PNG and G-code. Calling it *open project* was
excused on the grounds that models arrive through the app's own File Manager —
they do not. [ui/pages/file_manager.py](../ui/pages/file_manager.py) handles
machine jobs and point clouds and has no path into Kiri, so that rename left the
only route to an STL behind a name that denies it. The question "how do I import
an STL" then had a menu item that answered it and a label that hid it.

The pair is worth keeping, so the name is duplicated rather than fixed:
`ui/cam_import_file.py` inserts a second item, **import file**, directly above
*open project*, calling the same `api.event.import` that upstream's own item
calls. Two labels, one control, no second load path to keep in step.

`accept` is deliberately not split between them. It is one attribute on one
shared input, so per-item filtering means writing it on every click — and
upstream's list is already narrower than the loader, which handles `.3mf`,
`.ini`, `.gbr`, `.b64` and extension-less raw vertex files that the attribute
never mentions. Narrowing it further would hide more formats than it filtered,
and a dialog that refuses a file the app can open is worse than one offering too
much.

Clicking either item opens exactly one file chooser, which the markup invites
you to doubt: the hidden input is a *child* of `#file-import`, so `input.click()`
bubbles back into that item's own handler and upstream re-enters itself on every
import. Measured in this app's Chromium: handler twice, chooser once — the
nested `click()` is swallowed by the input's *click in progress* flag. The new
item goes round the same loop from the outside (it fires, `#file-import` fires
from the bubble, its `api.event.import` is the call that gets swallowed), which
is why it needs no input of its own. `test_import_file_live.py` counts the
openings in a real browser rather than trusting the reasoning, and also compares
the row's computed layout against a sibling built by upstream's `menuItem()`.

Kiri's *Workspace Export* — **File > export work**, `conf/settings.js
export_profile()`, wired to `#app-export` by `init/bind.js` — zips the
settings, devices and workspace into a `.kmz` and hands it to
`api.util.download`. That is the same door G-code leaves by, and everything
through that door was being treated as a program: renamed to the batch's job
name, filed under a timestamped `carve-*` export folder, and offered to the
*machine* if the last export intent said so.

So saving a profile looked like it did nothing. The file existed — under another
name, somewhere the operator had no reason to look, and possibly queued for the
controller.

`_save_profile_download` now takes it first and asks where to put it. A profile
is the one export whose destination is genuinely the operator's business: they
are keeping it, not running it. Cancelling the dialog cancels the download,
rather than accepting it into a scratch directory and leaving a file nobody
asked for.

The test is a whitelist of profile extensions (`.kmz`, `.km`), not a blacklist
of program extensions — a new G-code extension appearing must never start
opening a save dialog mid-job. `test_profile_save.py` covers both directions,
including that `.nc` / `.gcode` / `.tap` are not touched at all.

**The dialog says what opening one does.** `settingsImport` runs
`api.space.clear()`, assigns `settings = normalize(data.settings)` straight over
the live record and into `localStorage`, clears the platform and re-adds the
saved parts — the current workspace and its settings are gone, with no undo. The
dialog that asks about that said:

```
Import settings made in Kiri:Moto version 4.7.2 on
Wed Sep 03 2026 16:20:11 GMT+0200
```

which is provenance, and answers a question nobody asked at that moment. The one
destructive thing about the operation was the one thing it did not mention. It
now reads *"Opening a project clears the current workspace and its settings.
Continue?"*

`ui/cam_import_prompt.py` does it by wrapping `api.uc.confirm`:
`settingsImport` does `const { uc } = api` and then `uc.confirm(...)`, so the
property is looked up on the shared object at call time and a replacement is
seen — no patch, and nothing about the import itself is touched. The match is on
the prompt's opening words rather than a flag, because `uc.confirm` is the app's
general confirm and also asks about replacing a device, replacing a process and
clearing tabs; the test covers those staying untouched. The screenshot above the
text (`opt.pre`) is kept — it is not text, and seeing which project is about to
be opened is worth more than a tidier dialog.

**Importing one back.** `File > open project` — or `File > import file`, the
same control under its other name (`#file-import`, whose hidden
`<input type=file>` accepts `.km,.kmz,…`) — or dropping the file on the
viewport.
`platform.js` dispatches on the extension — `iskmz` → `api.settings.import_zip`,
`.km`/`.b64` → `api.settings.import` — and both prompt before replacing.

What comes back is the job: operations, stock, origin offsets, the tool library,
and the parts themselves if *Exclude meshes* was left unchecked. What does not
is the machine, and that took a fix. `settingsImport` replaces the settings
object wholesale (`settings = normalize(data.settings)`, straight into
localStorage) and emits no device event, so `applyPins()` never ran and a
workspace exported from another machine put **its** bed size, spindle ceiling
and feed limits into force until the next launch — the fields least worth
inheriting from somewhere else. `applyPins` is now hooked to `settings.saved`
as well as `device.selected`. It is safe to hook despite calling
`api.conf.save()` itself: the second pass finds nothing to change and returns
before saving again, so the loop is one deep and stops.

Note this is separate from the hidden **profiles** dialog (`#set-profs`, see
above): that one manages named process libraries inside browser storage, which
is what "one machine, one process" rules out. Writing the current settings to a
file the operator keeps is a different thing, and is the thing worth having.

### Export hand-off

Each tool's export button is replaced by an explicit destination choice —
**Save as File** and **Save to Machine** (`ui/cam_export.py`):

| Mode | Tool control replaced | Left alone |
|---|---|---|
| PCB | `#exporter-execute-btn` ("Export Files") | Cancel, Calculate Toolpaths |
| 2D/3D | `#print-download` ("gcode") | zip / 3mf mesh exports |

The tool's own control is hidden and clicked programmatically, so all of its
export logic still runs — for EasyTrace the operation ordering, single-file vs
per-tool split and comments; for Kiri the post processor and its
`#print-filename` field. Button classes and capitalisation follow each tool's
own idiom, so the replacements read as native.

**Naming a job.** The staged filename is what reaches the machine —
`FileManagerPage.upload_file()` only forces a `.nc` extension — and for EasyTrace
that name is ours, not the tool's.

EasyTrace treats its filename field as a *stem* and decorates it per operation:
typing `board` yields `board-isolation-copper_top.nc`, `board-drill-1.0mm.nc`,
`board-cutout-outline.nc`. What should land on the machine is the name that was
typed, whatever combination of toolpaths ended up in the file — so the field is
relabelled **File Name** (`ui/cam_labels.py`), its value is sent along with the
destination, and `CamPage._batch_filename()` names the staged file after it.

Only the *extension* comes from the tool, and it is never rewritten: it is what
marks a file as G-code (`_GCODE_EXT`) rather than a preview, so changing it would
change what gets offered to the machine. A trailing extension in the typed value
is stripped with the same rule EasyTrace applies to its own baseName, so
`board.nc` and `board` behave identically.

One batch can still hold several files — without M6 the tool exports one per
tool, and *Export as single file* merges only what one file can hold. They cannot
all be `board.nc`, so the second and later get `-2`, `-3` … in arrival order; the
first keeps the name exactly, and files differing only by extension (G-code and
an SVG preview) both keep the plain name. The uniquing also applies without a job
name, so a batch cannot overwrite itself inside the staging folder.

Kiri sends no name and keeps its own `#print-filename` behaviour.

The ordering matters. Previously every export went through one button and the
app asked afterwards where the files should go; on a machine about to cut, that
question is worth answering before committing, not after.

**How the intent reaches Python.** Qt cancels a `QWebEngineDownloadRequest`
unless `accept()` is called before the signal handler returns, so the
destination cannot be queried from the page once a download is in flight. Each
button therefore first records the intent — and the job name — with a same-origin
request to `/__piccola/export-intent` (`cam_server.CONTROL_PREFIX`) and only then
clicks the real export button; awaiting the fetch guarantees the ordering.
Reusing the server we already run avoids standing up a QWebChannel and injecting
`qwebchannel.js`. Unknown destinations fall back to `file`, never `machine`, and
the name is kept raw in the control dict (length-capped) and sanitised only where
it becomes a path.

Destination, name and staging folder are taken **once per batch**, not per
download: EasyTrace emits one file per tool and every file of one export shares
them. The batch is marked open by a flag on the first download *request* — not by
the list of finished files, which fills more slowly than a burst arrives, so
keying on it would re-consume the destination per file and leave all but the
first defaulting to disk. A cancelled or interrupted download settles the batch
like a completed one, so a failed export cannot leave its name and destination
open for the next.

* **Save to Machine** switches to the File Manager and uploads sequentially
  through `FileManagerPage.upload_file()` — the same path the manual upload
  button uses, so the progress row looks identical. No confirmation, since the
  user already chose. If no machine is connected it says so and leaves the files
  staged rather than failing silently.

  Each staged file is deleted once it is **successfully** on the machine, and
  the per-export folder is pruned with its last file. `transfer_done` fires for
  success, abort and error alike, so `Connection.last_transfer_ok` distinguishes
  them: an aborted or failed upload keeps the file so it can be retried from the
  File Manager instead of being re-exported. Deletion is contained to the export
  root, so a queue bug cannot unlink anything else. Live Preview is unaffected —
  `Connection.start_transfer` keeps its own copy in the g-code cache.

  Queue advance polls rather than firing once: FileManagerPage holds its upload
  row ~600 ms after `transfer_done` so the finished bar stays readable, and
  `can_upload()` is False for that whole window. Retrying every 200 ms up to
  ~5 s avoids coupling to that constant — a single deferred attempt deadlocks,
  because the re-queued file would wait on a `transfer_done` that never comes.
* **Save as File** asks for a destination *folder* (multi-file is the normal
  case, not the exception) and copies there. The staging copy under
  `<AppLocalData>/cam-exports/` is left in place as a history; cancelling the
  dialog reports where the files already are. Re-exporting a job writes the same
  filename again now that names are the user's, so the status line says how many
  files a save **replaced** rather than reporting a plain save over something
  that is now gone.

#### Download interception

`QWebEngineProfile.downloadRequested` is redirected into
`<AppLocalData>/cam-exports/<mode>-<timestamp>/`, suppressing Chromium's own
save dialog. EasyTrace has no M6 tool-change support and so exports **one file
per tool** — several downloads arrive back to back. `CamPage` debounces them
(700 ms) into a single batch and emits `gcode_exported`, so the user gets one
prompt listing every file. The folder is stamped once per batch, so one export is
one folder even when its files straddle a second boundary. Accepting uploads them **sequentially**: the machine
transfer path handles one file at a time, so `main_window` queues them and
advances on each `transfer_done`.

## Vendoring

```bash
python3 tools/fetch_vendor.py all        # or: easytrace | kirimoto
```

### Why a patch sometimes seems to do nothing

Three things have to line up before a vendor change is visible, and each fails
silently on its own:

1. the patch is applied to `vendor/kirimoto/src` — `fetch_vendor.py` does this;
2. the **packed bundle** was rebuilt from that source. The app loads
   `/lib/pack/kiri-main.js` and `kiri-work.js`, never the individual source
   files, so a patch present in `src/` and absent from the pack does nothing at
   all. Note the `/lib/` overlay searches `alt` before `src`, so `kiri-main.js`
   is served from `alt/pack/` and `kiri-work.js` from `src/pack/` — editing one
   copy is not enough;
3. the desktop app was **restarted, not reloaded**. Injected JS (`ui/cam_*.py`)
   is compiled into a `QWebEngineScript` in the Python process when the webview
   is created. Reloading the Kiri page re-fetches everything under `vendor/` but
   cannot pick up a change to `ui/*.py`. When a vendor change appears and an
   injected one does not, that is always why.

`tools/verify_patches.py` answers 1 and 2 for the whole series:

```
./venv/bin/python tools/verify_patches.py          # everything
./venv/bin/python tools/verify_patches.py 0011     # one patch
```

The check that matters is against the *served* pack, because
`test_patch_symbols.js` inspects the source tree and passes happily while the
running app has none of it — which is exactly how patch 0011 came to be "done"
twice without being live.

Minification is why each patch declares its own marker rather than the tool
grepping for what the patch added: esbuild renames module-local functions, so
patch 0006's `stockCircle` appears in no bundle under that name. String literals
and property names survive, so a patch header says what to look for:

```
Bundle-Check: jump to a point in the job     must match the pack
Bundle-Absent: showGrid2\(!                  must NOT match it
```

A marker has to match *some* candidate pack, not every one — a patch spanning
`app/` and `work/` code has markers belonging to one bundle or the other. Five
of the eleven patches declare markers; the rest are reported as *undeclared*
rather than passing, since "we did not look" and "we looked and it was fine" are
different answers.

The automatic diff-versus-source comparison is printed as a **note**, never a
failure. The series is applied in order and later patches rewrite lines earlier
ones added — a line from 0002 legitimately no longer exists verbatim once 0008
has been through the same function — so judging each patch against the finished
tree cannot distinguish "never applied" from "applied then superseded", and a
check that cries wolf is worse than no check.

Proven to fail when it should: reverting `alt/pack/kiri-main.js` to its
pre-0011 state makes the 0011 check report both a missing marker and a
surviving one, naming the rebuild command.

**`0012-timeline-parks-where-you-put-it.patch`** — dragging the timeline went
to the point and then walked away from it, because patch 0008 made the seek
play on. That was asked for too ("go to that point and start the animation from
that moment"), and clicking and dragging turn out to want opposite things: a
click is a request to watch from there, a drag is a request to *look* at there,
and a scrubber that restarts under your hand cannot be used to inspect
anything. Both gestures land in the same `onchange`, so one has to win, and
inspecting is the one with no alternative — play is a button away, but there is
no other way to hold a frame still.

`seekThenPlay` now puts the transport back to *play* on arrival instead of
starting a run. The name is a slight lie and is kept: it is what patches 0008
and 0009 call. Patch 0009 is unaffected — scrubbing *during* playback still
works, the run being stopped, the seek queued and then performed; it simply
ends paused, the same as a scrub from a standstill. Both animators are changed,
since a behaviour that depends on which one a job happens to be using is worse
than either behaviour alone.

Also applied directly to the served bundles, network being unavailable for a
rebuild — see **Why a patch sometimes seems to do nothing**. The
`Bundle-Absent` marker for it is anchored on `||(` rather than the button flip
alone: patch 0007's `playWhenReady` branch has the same flip and still starts a
run on purpose, so the looser pattern reported a correct bundle as stale.

### Patching Kiri:Moto

`tools/patches/<bundle>/*.patch` are applied to the checkout in filename order
after clone and **before** the build, so the shipped bundles are compiled from
patched sources. `VERSION` in the vendored tree records which patches went in.

Patching is deliberate and rare. Everything Piccola can do from outside a tool it
does from outside — `ui/cam_*.py` drive each tool's own API through injected
script — precisely so `vendor/` stays byte-identical and an upgrade stays a
folder swap. A patch is for what that cannot reach.

There are currently ten:

**`0001-lathe-step-down-on-the-cpu-path.patch`** — Kiri's lathe op has a
`step down` (`camLatheDown`) and an `expand` (`camLatheExpand`) that grows the
starting radius for it, but both are implemented only inside `sliceGPU()`, and
the input is hidden behind `show: isWebGPU`. Without WebGPU the lathe op is a
single finish pass at final depth with no way to rough down to it.

That is not a preference we can flip. QtWebEngine ships Chromium built without
Dawn (`BUILDFLAG(USE_DAWN) is false`), so `navigator.gpu` exists but
`requestAdapter()` always resolves null — verified across seven flag
combinations in both an off-screen and a real on-screen window. Enabling Kiri's
`web gpu` preference makes things worse, not better: it routes slicing into
`sliceGPU()`, whose rasteriser throws `"WebGPU not available"` with no CPU
fallback, taking `contour` and area tracing down with it.

The code that needed changing runs inside a web worker, and
`/lib/kiri/run/worker.js` is rewritten to a 3.7 MB pre-bundled `kiri-work.js` —
so no CSS or main-page injection can reach it, and overlaying the source file
does nothing because that file is never fetched. Hence a patch.

The patch adds `Topo.latheStepDown(paths)`, a direct port of the block at the end
of `sliceGPU()`: raise every point of the finish profile by `down`, clamp at
`maxZ`, repeat, then reverse the stack so it cuts outside-in and ends on the true
profile. It differs from upstream's only in where `maxZ` comes from — a max over
the produced points rather than the GPU raster rows. It is called from
`latheWorker()` and `latheMinions()` before each one's render loop, so the
preview draws the passes that will be cut, and it drops `show: isWebGPU` from the
input.

Verified by extracting the method verbatim from the patched source and running it
against stub geometry — a profile of radius 10 with a groove down to 2:

```
down=0            -> [[10,6,2,6,10]]                  (single pass, unchanged)
down=3, expand=0  -> [10,10,8,10,10]                  (outermost first)
                     [10,9,5,9,10]
                     [10,6,2,6,10,10]                 (finish + retract to maxZ)
down=3, expand=5  -> 5 passes starting at 15           (roughing begins outside the part)
```

No pass removes more than `down` radially at any point, which is the whole
purpose of the parameter.

**Known rough edge, inherited.** Upstream left the entry move into each roughing
pass unfinished in their own implementation:

```js
// first move of new stack is to maxZ (problematic)
// let first = stack[0].points;
// first.splice(0,0,first[0].clone().setZ(maxZ));
```

That commented-out block is theirs, and this port does not solve it either. The
plunge at the start of a pass is the thing to watch on a dry run.

**This should be upstreamed and deleted.** The CPU path lacking a parameter the
GPU path has is an upstream gap, not a Piccola-specific need.

**`0002-cam-animate-speeds-and-timeline.patch`** — the toolpath animator's speed
table stopped at a visible 8x with an unlabelled "!!" above it, and there was no
way to seek. It now reads 1x 2x 4x 8x 16x 32x 50x, in both animators (`init-ui.js`
picks between them per job: `env.animVer = env.isIndexed ? 1 : 0`, so rotary work
animates through `anim-3d.js` and everything else through `anim-2d.js`), and a
timeline scrubs the job. The patch header records what the higher speeds actually
buy — scaling stops near 8x, and the wall is the stock boolean subtraction, not
the sleep.

**`0003-no-y-word-in-indexed-output.patch`** — on a 4th-axis job the machine
parks Y at the rotary working position and locks it there; commanding Y raises
`rotaryYLock` (see `_MOTION_ERROR_NOTICES` in `connection.py`). Kiri puts the
rotation on A correctly but still emitted a handful of Y-carrying rapids, so the
Y word is withheld from the file whenever the stock is indexed. `pos.y` and the
run box are still tracked, so bounds reporting is unchanged; ordinary 3-axis
output is not touched.

**`0004-rotary-stay-in-plane-and-park-clear.patch`** — the same problem one level
down, plus where the tool ends up.

0003 keeps the Y word out of the *file*; the prepared *path* still had the
motion, so it was still in the preview and the animation, and the export patch
was the only thing standing between it and an alarm. The path points are fine —
topo4's lathe generator builds every one at widget `y = 0` — but the point the
job starts from is the stock's front-left corner (`originy = -stock.y / 2`, so
Y-15 on a 30mm stock), and every operation transition rapids back out through it.
`platform.js` already applies `if (isIndexed) { origin.y = 0 }` to the export
origin; the patch applies the same rule on the prepare side, to the one point
that was not already in the plane. After it an indexed job has no Y delta
anywhere, and 0003 is a backstop rather than the only defence.

The second half is the park. The lathe op ended with
`addGCode("G0 Z" + zSafe)`, and text added that way is emitted verbatim —
export's coordinate remap walks real points only (`if (out.gcode || out.type ===
'lerp') return`), so that Z never gets the origin transform every other Z in the
file carries. On a 60x30x30 indexed stock with the origin at the top of the stock
the line reads `G0 Z22.21` in a file whose cutting moves are all near Z-15: a
rapid ~37mm above the work zero, in no coordinate system. Because it is not a
point, export's own `pos.z` also stayed at the pre-retract height, so the next
real move — prepare's end-of-op retract — dived back down and left the tool
parked 1mm (`camZClearance`) off the piece, which is where the job ended and
where the operator then reached in to unmount it.

It becomes a real rapid: Z only, which for indexed work is the whole of "out of
the way", so it goes through the remap and is the last motion in the file. The
height is `stockZClear`, which for indexed stock is now `stockZ +
max(camZClearance, 3)` — 3mm above the stock surface rather than the 1mm retract
height. `zSafe` is deliberately untouched: it keeps `hypot(stock.y, stock.z) / 2
+ camZClearance`, the height that clears the corners of a square stock as it
turns, and that is still what operation transitions rapid at.

**`0005-per-operation-preview-layers.patch`** — two operations of the same type
shared one preview toggle, so a duplicated op could not be looked at on its own.

The layer checkboxes beside the viewport are keyed by layer *name*: `stacks.js`
builds `DYN[label]` once per distinct name and pools every matching material
into it. Every op of a type passes the same literal, so two lathe ops are both
`lathe`, land in one control and toggle together — no way to compare a roughing
pass against a finish pass.

Kiri already computes the name that fixes it. The per-op slice loop derives
`layername` from the operation's **note** (`lathe (finish)`, or `#tag` words),
and the note is already editable from the op panel — `cl-ops.js` pops an *Edit
Note for Operation* prompt. But `state.layername` is read by exactly one
operation, `op-drill`; `op-area` grew a parallel `rename` field; everyone else
passes a literal. Computed for all, consumed by one.

The patch publishes a second field, `opname` — the same string, but only when
the operation carries a note — and has each op's *toolpath* layer read
`state.opname ?? "<its old literal>"`. So an un-noted job keeps exactly the
layer names it always had, and only a noted operation splits off a layer of its
own. `rename` still wins where op-area offers it.

Renamed are the layers an op draws its cut path into: area's clear/trace/linear,
contour, helical, level, register, and the lathe's three render paths in
`topo4`. The diagnostic overlays are left pooled — "base", "shadow", "tool
shadow" (all behind `if (devel)`), "coastline", "clip.*", "heights", op-shadow's
own output and op-area's orange region. The test is the colour: a toolpath layer
uses the operation's own `color`, a diagnostic a hardcoded literal.

**This should be upstreamed and deleted.** A name computed for every operation
and read by one reads as an unfinished refactor, not a decision.

The operator-facing side of this — how to note, duplicate and isolate
operations — is written up separately in [cam-operations.md](cam-operations.md).

**`0010-grid-switch-applies-everywhere.patch`** — *show grid*
(`camStockIndexGrid`) only appeared beside the rotary switch, and the reason was
not that it made no sense elsewhere but that it *did* nothing elsewhere:

```js
SPACE.platform.setVisible(!env.isIndexed);
SPACE.platform.showGrid2(!env.isIndexed || camStockIndexGrid);
```

Outside indexed mode the `!env.isIndexed ||` short-circuits to true, forcing the
lines on and making the setting inert — so upstream hid the checkbox there,
which is the honest thing to do with a control that cannot act. It becomes a
real choice in rotary only because that mode hides the bed, leaving the grid as
the only spatial reference; that says the grid is *useful* there, not that it is
unwanted on a flat job where some would rather look at a clean bed.

So the flag is honoured in both modes and the `show:` predicate goes with it.
`showGrid2()` sets `grid.lines.visible` alone — the X/Y axis lines and the bed
are untouched — and the shipped default is already true, so nothing changes for
anyone who does not go looking for the switch. `ui/cam_grid.py` lifts the row
out of the rotary group to the top of the panel at the same time, since a
general view option nested under *rotary* would read as applying only there.

**`0009-timeline-works-while-playing.patch`** — dragging the timeline mid-run
did nothing. It worked while paused, so the control looked functional and
simply ignored you at the one moment you most want it: watching a job go past
the bit you wanted to look at.

Same cause as 0007 and 0008, in its third guise — the worker takes one animate
at a time and drops the rest (`if (animating) return send.done()`), so a seek
issued during playback never reaches the path. Measured on the real module:
play from record 1, drag to 90%, and both the seek and 0008's follow-on play
come back `DROPPED`, with the job carrying on to its natural end.

So a seek during playback stops the run first and waits for it to actually
stop. Stopping is itself an animate — `{ speed: 0 }`, what `pause()` sends —
and the worker applies the speed *before* the in-flight guard, so the running
loop sees `renderSpeed === 0` on its next tick and ends. Two replies come back:
the stop's own, immediately, and the running exchange's when it winds down.
Only the second means the worker is free, so the stop is sent with an empty
callback and the seek resumes from the update handler.

Knowing whether anything is in flight needs one honest flag, and the way to
keep a flag honest is to give it one door: `startRun()` sets `running`, every
continuous run goes through it, and it is cleared on any `done` and by
`pause()`. `seeking` now also stays set until the deferred seek runs, so
progress updates in the gap do not drag the thumb out from under the operator.

**`0008-timeline-plays-on-from-the-click.patch`** — clicking a point on the
animation timeline moved the cut to that moment and stopped there, so watching
the job from that point meant clicking play afterwards. Playing from an
arbitrary moment is the whole reason to click a timeline, so `seek()` now hands
its follow-up to the run: forward, which fast-forwards from where it is, and
backward, which rewinds and re-cuts up to the target. Landing on the very end is
left alone — nothing to play, and 0007 has already restored the transport.

What makes it more than moving a line is that the play has to wait for the seek
to land. The worker refuses a second animate while one is in flight
(`if (animating) return send.done()`), so a play issued immediately after the
seek is dropped and the job sits still — exactly the bug being removed. The
reply envelope already carries the signal: `app/workers.js` calls the callback
as `onreply(reply.data, reply)` with `done` set on an exchange's last message.
`seekThenPlay()` takes that second argument, forwards the payload to the normal
update handler, and issues the play only once `done` arrives.

**`0007-play-restarts-a-finished-animation.patch`** — run the toolpath animation
to the end, press play, and nothing happens.

The worker keeps one cursor into the path (`pathIndex`) and resets it in exactly
one place, `animate_setup`; `dispatch.animate` never touches it. Once a run has
completed the cursor is parked past the last record, so `renderPath()` finds
`path[pathIndex]` undefined, falls into its stop branch and calls `send.done()`
without rendering. Every later play is that same no-op — reproduced by
transcribing the state machine: a 500-record path runs 0 → 500, and a second
play leaves it at 500 having done nothing.

There is no cursor to rewind. The cut is destructive — the stock at record N
exists only by having carved 0..N-1 — so the only way back to the start is a
fresh `animate_setup`, which is what **replay** does and what `seek()` already
does when asked to go backwards. Play at the end now takes that same route.
Stepping is left alone (a step at the end is a no-op, not a request to start
over), and so is the speed button, whose job is speed.

**The restart's second half cannot be a timer**, and the first cut of this
patch got that wrong in a way worth recording. `animate()` ends by scheduling a
single step of its own, and the worker refuses a second animate while one is in
flight (`if (animating) return send.done()`). A play posted on a fixed delay
lands on top of that step whenever the step outlasts the delay — a long first
move, a slow machine — and is dropped without a trace, leaving the animation at
record 1 with the stock uncut. The button still did nothing, only now from the
other end, which is exactly what got reported back.

So the play waits on a `done` reply instead. Measured on a virtual clock
against the real module: with the old fixed 250 ms, a rebuild step of 250 ms or
longer loses the play every time and the job ends at 1/20 records; chained on
the reply it completes at every pacing from 1 ms to 1200 ms. That case is now
a regression test that loads the actual module rather than a transcription of
it — the transcription passed while the shipped code failed, which is the whole
lesson.

The second half is why it reads as a dead button rather than a finished job:
nothing reset the transport when a run ended by itself. `play()` hides play and
shows pause, and only `pause()`, `seek()` and setup put them back — so reaching
the last record left **pause** showing with nothing to pause. `handleGridUpdate`
already carries the cursor and total (from 0002, for the timeline), so it now
restores the buttons when the cursor arrives at the end.

Both animators are fixed: `anim-2d.js` drives every non-indexed job
(`env.animVer = env.isIndexed ? 1 : 0`), `anim-3d.js` the rotary ones.

**`0006-cylindrical-stock.patch`** — Kiri's stock is a box and nothing else:
three numbers, `camStockX/Y/Z`, with no notion of shape. The only
`CylinderGeometry` in the whole CAM app is the marker `cl-hole.js` puts on
holes. Round bar is ordinary material on this machine and there was no way to
tell the CAM about it.

Without it the operator has to describe a round blank as the square that
circumscribes it. That is safe but wasteful — roughing, facing and the contour
clip all clear the full square, so ~21.5% of the cleared area is air. The
tempting fix, typing the *inscribed* square, is the dangerous one: the CAM then
believes there is no material at the round edge and will rapid and plunge
through it.

The blocking part is that stock shape lives in the slicer, which runs in the
web worker — the same wall as 0001. Hence a patch.

It is smaller than it sounds, because the stock only ever enters the pipeline
as a *polygon*, and only where an op works against the stock instead of the
part's shadow. There are exactly four such places:

| Site | Active when |
|---|---|
| `op-rough.js` | **all** — clear all of stock |
| `op-level.js` | **stock** — face the whole blank |
| `op-shadow.js` | `skipTerrain` — shadow when terrain is skipped |
| `topo3.js` | `contour.clipto` — clip contouring to the stock |

Everything downstream of them is shape-agnostic polygon work, so a circle flows
through untouched. Each site keeps its rectangle expression **verbatim** and
only branches ahead of it, so nothing about rectangular stock changes.

The shared half is `work/stock-poly.js`. `stockIsRound()` is false in indexed
mode — there the blank is a bar lying along X, already round in effect, with its
cross-section given by Y and Z rather than X and Y, which `prepare.js` already
clears with `hypot(stock.y, stock.z)/2`; mixing the two would put the circle in
the wrong plane. `stockCircle()` takes the diameter as `min(x, y)`, the largest
cylinder the operator's own fields can hold — erring inward whichever way they
are filled in, which is the safe direction: too small only leaves material
behind, where too large has the tool cutting through what it believes is air.
Segment count comes from a 5 µm chord tolerance, not a guess at the size.

The **setting** is patched in rather than injected, which is the exception to
how Piccola normally works. A process key set from the page does reach the
worker, but it would not survive Kiri replacing the process record from a device
profile — and a stock shape that silently reverts to square is the failure this
patch exists to prevent. `round` is hidden while `indexed` is ticked.
`cl-stock.js` draws the cylinder so the viewport agrees with what the ops clear.

Verified by extracting `stock-poly.js` against a stub `newPolygon` carrying
`geo/polygon.js`'s own `centerCircle`/`centerRectangle`: radius, centring, z,
winding, inscription in the X/Y box, the indexed/unset truth table, the
`op-level` overrun, chord error 4.9–5.0 µm from Ø10 to Ø150, and the 21.5% area
saving — 20 assertions. The four rectangle expressions are asserted to still
appear literally in the patched sources.

Both are pinned in `SOURCES` in that script. Bump refs deliberately and re-test
a cut — an unnoticed change to a post-processor is a change to the G-code we
hand a machine.

The two are fetched very differently, and this is the main thing to know:

* **EasyTrace5000 is a static site.** Its repo *is* the deployable. It is
  **committed** to this repo under `vendor/easytrace/` (~5 MB) — being AGPL, a
  byte-identical in-tree copy is also what keeps redistribution simple.
* **Kiri:Moto is not.** GridSpace publishes only Electron installers
  (~120–190 MB) as release assets; there is no static web build to download.
  Servable assets must be produced by its own npm pipeline. That needs Node +
  npm **at vendor time only**; the app runtime stays pure-Python + Qt. The
  result is ~63 MB of generated MIT build output, so `vendor/kirimoto/` is
  **gitignored** — rebuild it rather than committing it.

The build order is easy to get wrong and fails confusingly:

```
npm install            # deps only
npm run webpack-ext    # generates src/ext/* — NOT part of `npm install`
npm run pack-prod      # esbuild + dryrun + bundle
```

Skipping `webpack-ext` makes `pack-prod` die with ~25 `Could not resolve
"../ext/three.js"` errors. It normally runs as part of `npm run setup`.
`fetch_vendor.py` does all three in order.

Only `web/`, `alt/` and `src/` are kept — the trees `cam_server.py` serves.
`web/boot/bundle-*.bin` (~25 MB) is dropped: it is the service-worker bundle of
the same assets we already serve from disk. Symlinks are resolved on copy,
because `web/font` and `web/fon2` point into `node_modules`, which does not
survive the build tree.

`vendor/*/VERSION` records the upstream, ref and commit for each.

**`0011-keep-toolpath-layers-in-animate.patch`** — starting an animation threw
away the toolpath layers and the checkboxes that control them. `animate()` calls
`STACKS.clear()`, and that is total rather than cosmetic: every stack is removed
from its view, `stacks` and `UI.dyn` are emptied, and `#layers` — the column of
per-layer checkboxes patch 0005 made per-operation — is wiped. Because the
stacks are *gone* rather than hidden, nothing can restore them without slicing
again. So the paths you were just looking at, and their controls, vanished at
the moment you asked to watch the job being cut; preview had them, animate did
not, and they are the same paths.

The animation does not depend on the clear — it builds and owns its own meshes,
makes the model translucent itself on the line above, and hides the layer
*slider* rather than the layers — so the call simply goes.

This one cannot be done by injection, which is why it is a patch.
`stacks.clear()` has six call sites and two of them run on the way into animate:
`function.js` clears and immediately re-creates a stack in both the render
(`stacks.create('print', ...)`) and parse (`stacks.create('parse', ...)`) paths,
both reached through `api.function.prepare()` — which is exactly what the
animate button calls before `animate()` runs. Wrapping `api.stacks.clear` and
suppressing it for the duration would swallow those two as well, leaving the
rebuild adding layers to a registry that was never emptied.

**Applied ahead of the next re-vendor.** A patch only takes effect through the
packed bundle — `animate()` is inlined there as
`function NM(){…AU.clear()…}`, so editing `src/` alone changes nothing — and
rebuilding means `tools/fetch_vendor.py kirimoto`, which clones grid-apps and
runs npm. Where that is not available, the same eleven characters (`AU.clear(),`)
were removed directly from the two shipped bundles, `alt/pack/kiri-main.js` and
`src/pack/kiri-main.js` (the `/lib/` overlay searches `alt` first, so that is
the one actually served; both are edited so a change of overlay order cannot
resurrect it). Verified by putting the call back and confirming each file is
then byte-identical to its backup, so nothing else moved.

Those bundles are build output and are **not tracked in git**, so the edit has
no version-control safety net and will be overwritten — correctly, by this
patch — the next time the vendor tree is refreshed. That is the intended end
state: the patch is the durable form, the bundle edit is what makes it work
until then.

**`0022-drill-tip-angle.patch`** — a drill's point angle becomes tool
geometry, and the point is drawn at its real size.

*Tip angle.* Tools gain `drill_angle` (included angle, degrees), edited as
**tip angle** in the flute group; `ui/cam_tool_icons.py` shows that row only
when the type is drill. `calcDrillAngle()` accepts 0 < angle < 180 and
otherwise uses 118, so tools saved before the field existed read as 118.
`drillTipLength()` was a hard-coded 140° through a formula that was not a cone
length at all; it is now `(d / 2) / tan(angle / 2)`.

*Point size.* `Tool.generateProfile()` sloped a drill's end with
`z_offset = -dist_from_center / 45`: the distance is in raster *pixels*, the
offset in mm, so the point depended on the grid resolution, not the drill. The
preview rasterises inch tools 25.4x finer, which gave a 1/4" drill a ~28 mm
needle. The cone is now measured in mm and sized to `drillTipLength()` — 1.91
mm for 1/4" at 118°.

*Not changed:* the drill **operation** does not read the point. It plunges to
its depth with the tip as before — upstream never compensated for the cone, and
`drillTipLength()` had no caller. The angle shapes the tool profile: the
preview, and raster ops (rough, outline, contour…) run with a drill-type tool.
The matching icon (spiral flutes, short point) is in `ui/cam_tool_icons.py`.

**`0023-tool-list-by-number.patch`** — the tool library list showed names
only, sorted alphabetically, so a tool's number (the T word in `M6 T{tool}`)
was visible only after selecting it. Each entry now leads with it —
`T4  end 3mm`, padded with figure spaces so names stay aligned past T9 — and
the list is in number order (numeric, so T10 follows T9; ties by name; tools
with no number last). The sort replaces both upstream name sorts, on open and
on save, and runs on every render, so a changed tool # moves its row at once.
Saving stores the library in that order, so the op panels' tool dropdowns
follow it.

Because the list re-sorts, selection follows the tool record rather than a row
position: Add and Clone select what they created wherever it lands (upstream
assumed "last row"), an edit keeps the current tool highlighted, and Delete
moves to the first remaining tool. A patch rather than injection because the
numbers being edited live in the module-private `editTools`, not in saved
settings.

**`0024-new-tools-start-metric.patch`** — a tool added with **+** was metric
only while the display units preference was mm; on inches it came out
imperial (0.25" sizes, *metric* unticked). New tools now always start metric,
with upstream's own mm defaults (2 / 15 / 2 / 20). Inch remains a per-tool
choice via the checkbox; existing tools and the display units are untouched.

**`0025-tool-preview-uses-edited-tool.patch`** — the tool preview drew the
*saved* library, not the tool on screen: `renderTool()` used
`new Tool(settings(), tool.id)`, and the editor's copy (`editTools`) reaches
`settings()` only on Save. An unsaved tool's id is not found, and `Tool` falls
back to `settings.tools[0]` without complaint, so a new tool previewed as tool
#1 and an edited one kept its old shape until saved. The preview now builds
the `Tool` from the record being edited.

**`0026-rough-negative-leave-stock.patch`** — negative stock to leave on the
rough operation, for cutting past the model surface (fit clearance, a known
undersize). `op-area.js` already offsets clearing passes by
`-(toolDiam / 2 + leave_xy)` and lifts them by `leave_z`, so negative values
just move the passes into the walls and below the floor; only the UI stood in
the way. *Leave stock z* was bounded 0 – 10 and is now −10 – 10. *Leave
stock* (XY) was never bounded, but past minus the tool radius the clearing
offset turns from an inset into an outset and grows the cleared area into the
part, so the rough op now refuses `leave <= -(tool radius)` with a message,
checked at slice time because the limit depends on the tool. A negative
*leave stock z* also lowers the last pass, below the operation's Z bottom.

**`0027-animate-overcut-colour.patch`** — *Animate* paints over-cut ("negative
material": stock cut below the part's own surface) red. Upstream flagged only
cuts below the bottom of the stock. At animation start each model's top
surface is rasterised into a height map on the animation's grid, in the
stock's local frame (placed as `work/slice.js` places it); a near-horizontal
stock fragment more than 0.02 mm below it is over-cut. Steep faces are never
judged — the stock's outer skirt and the one-cell strip at every wall are
vertical and below the part's top without being over-cut — and the map is
eroded by one cell so a floor cell straddling a wall reads its lower side. A
wall over-cut lies inside the model's footprint, hidden by the model, so it is
also drawn as a translucent red overlay without depth test (an x-ray).

Verified on a real job (1.5 mm flat end, rough, clear stock): with leave XY 0
no cell is flagged; with −0.5 the flagged cells lie within 6–7 cells
(≈0.5 mm) of the walls. It also catches what the tip-only G-code check cannot:
the same job with a 3 mm shank on a 4 mm flute, cut 10 mm deep, lights the
walls up 0.75 mm deep — the shank rubbing, which is real. Flat animator only;
rotary (`anim-3d`) is unchanged.

**`0028-seek-shows-only-the-result.patch`** — *jump to the end* (and a
timeline drag) seeks with `fast: true`, but the worker still sent a full
`renderUpdate` (tool move + `mesh_update`) and waited out the playback pause
every 600 moves, so the jump played as a fast-forward of the whole cut. In a
fast run that stop now sends progress only and continues on the next tick; the
stock is a shared buffer the page uploads only on `mesh_update`, so the view
holds still and the finished cut appears in one update when the seek lands.
Measured with the real button on a 31k-move rough job: one stock upload during
the jump (at the end) instead of one per 600 moves. Normal playback unchanged.

**`0029-scallop-finish.patch`** — a *scallop* finish for ball end mills, after
Fusion's: rings from the area's outline inwards, each a constant stepover from
the last measured **along the surface**, so cusps stay even on steep walls.
Kiri's *offset* surface pattern spaces its rings in plan view, so on a 60° wall
they land twice the stepover apart on the surface. The new pattern lives in the
area op (`sr_type: "scallop"`, `work/scallop.js`); an add-operation entry,
*scallop*, adds that preset with the part's shadow as the area and a ball end
mill from the library. A *ring order* choice (`sr_order`, saved as
`camAreaScallopOrder`) cuts the rings outside in or inside out; inside out
runs them in reverse and turns each round, so climb stays climb. Piccola
files it under *finish strategy*
(`ui/cam_op_menu.py`), names it `scallop_N` (`ui/cam_op_names.py`) and draws
its diagram (`ui/cam_op_art.py`).

The rings come from a surface-distance field: the part rasterised, the tool
profile run over it (topo3's CPU `Probe` arithmetic) for the tool-tip
surface, Dijkstra over that surface on a 16-neighbour stencil, and marching
squares at every multiple of the stepover. A separate, finer grid then places
the tool along each ring.

It is all CPU, and that fixes more than scallop. Both upstream surface
patterns drape with the WebGPU raster unconditionally, and QtWebEngine has no
WebGPU (see 0001), so *linear* and *offset* never produced a toolpath in the
app — the slice ended in `Failed to initialize WebGPU`. They now fall back to
the same CPU drape when the raster will not start; where WebGPU exists they
still use it.

Verified in Node on surfaces with known answers: on 30° and 60° slopes the
rings sit cos(slope) × stepover apart in plan, i.e. exactly the stepover on
the surface; on a triangulated spherical cap with a 3 mm ball the draped tool
is never above the analytic position and at most 0.006 mm below it (the
cap's own faceting), and neighbouring rings are 0.599 mm apart (median) for a
0.6 mm stepover.

**`0030-scallop-limits-and-rest.patch`** — limits for the scallop, after
Fusion's, in a *scallop limits* section of the area op: a **slope** range,
the op's own **Z top / bottom**, **avoid surfaces** (a second face picker,
`op.avoid`, with a clearance) and **rest machining** after a larger
**previous tool**. Each limit only removes cells from the region the rings
are laid in; the tool is still placed against the whole part, so none can
cause a gouge. Avoid keeps the tool's centre further than radius + clearance
in plan from every picked face; rest runs the previous tool over the grid
(`leftover()`, the lowest its underside reaches everywhere it fits) and cuts
only where more than 0.02 mm is left, plus this tool's radius round it. With
a limit on, the first ring sits half a stepover inside the region instead of
on its raster edge. Checked in Node on a capped plate with a slot: no ring
point breaks the slope or avoid limit, and rest finds the slot and the cap's
inside corner — the two places a 6 mm ball cannot reach — and nothing else.

**`0031-exact-drape-and-ring-links.patch`** — two gouges in the CPU drape of
0029, found by running the scallop G-code from the app's own Kiri against the
exact part (a dome cut by two 2 mm slots, 1/8" ball): the tool went up to
0.145 mm into the part at a slot's rim, and the Node checks found 0.25–0.5 mm
on links over a box's top edge.

* *Sampled placement.* Drape placed the tool on a grid of sampled heights with
  a sampled tool profile. Where the tool's side rides over a sharp edge, the
  edge falls between samples — worst at a ball's equator, where its side is
  steepest. A **ball or flat end mill** is now dropped on the triangles
  themselves (vertex, edge and face tests, bucketed so only triangles under
  the tool are tested), with a shaft wider than the flutes as a flat disc at
  the flute length. Other tools keep the grid, which now takes the highest of
  the four grid heights across a jump instead of blending into a wall.
* *Straight chords.* Points went down every ≤0.2 mm and were joined straight;
  more now go in wherever the midpoint would be more than 2 µm low.
* *Links.* Each ring was its own path, and `prepare.js` links paths up to two
  stepovers apart with a straight move — through anything convex between
  them. A ring that starts that close now continues the same path, so its
  link is draped, unless the link crosses a cell the rings may not cut (an
  avoided face, a slope or height left out, a hole). For every CPU-draped
  pattern, a straight link that would dip into the part, and the first path
  of each area after the first, lifts over instead.

After it the same e2e run (plain, inside out, slope 40–90°, rest) is at most
0.002 mm into the part, the check's own tolerance, with 4 rapids instead of
22. A brute-force drop on the slotted dome and on a box with vertical walls
(ball and flat, two sizes) is never above the exact one.

**`0032-step-import.patch`** — `.step` / `.stp` import, through the one load
path everything uses (`platform.js load_files`: *import file*, the file
chooser, drag and drop). Kiri reads STL/OBJ/3MF and only *writes* STEP
(`src/load/step.js` is an exporter). The converter is OpenCascade compiled to
WebAssembly, **occt-import-js 0.0.23** (LGPL-2.1): a dependency in Kiri's
`package.json`, copied by a new `webpack-occt` step of `webpack-ext` (the step
that already copies planegcs) to `src/ext/occt/` — script, 7.6 MB `.wasm` and
both licence texts — and served as `/lib/ext/occt/`. Separate files, so the
LGPL library stays replaceable. `src/load/step-in.js` runs it in a Blob worker
(no file of its own in the served tree) that is terminated after each file.

It asks for millimetres and 0.01 mm chordal / 0.1 rad angular deflection,
turns a reversed (inward) shell round, keeps one body per solid (with the
same *group N bodies?* prompt as OBJ/3MF), and lays the file flat: rotated so
its largest planar CAD face points straight down, preferring, among faces as
large as the largest, the one already facing down, so a part drawn lying
flat is not turned. A file with only curves (`GEOMETRIC_CURVE_SET`, a
sketch) has nothing to cut and is refused with *STEP import failed: the file
has no solids or surfaces in it*.

Checked with real parts: *Bed Ball Nose Test.step* (155 CAD faces → 99 036
triangles) imports in 3.1 s in the page, lying flat at 234.5 × 176.5 × 9.5 mm;
a Scallop over it slices in 6.6 s, and 2 500 sampled cut points are at most
0.0011 mm inside the triangles (G-code rounding). `tools/verify_patches.py`
now reports top-level build inputs such as `package.json` as "not vendored"
instead of failing on them: `fetch_vendor.py` keeps only `web/`, `alt/`,
`src/`.

**`0033-exact-z-zero.patch`** — upstream wrote every Z 0.01 mm deep. Found
while checking 0032: `platform.js update_origin` put a top origin at
`stock.z + 0.01` (a lift that keeps the marker's flat discs clear of the
stock's top face), and `export.js` takes the G-code's Z offset from that same
origin (`origin.z - zmax`). So with *origin top* every Z Kiri wrote — cuts,
plunges, the safe height (Z0.99 for a 1 mm clearance), every operation — was
0.01 mm below where it was computed; the Animate Z readout and the contour
op's Z limits, which read the same origin, were off by the same amount.
`origin.z` is now exactly the stock top and the 0.01 is added only where the
marker is drawn, so the marker has not moved — and neither has anything
`ui/cam_stock_origin.py` derives from it (its rotary handles mirror the
marker; only its comments changed). Checked in the page: the export origin
equals the stock top, the safe height is Z1.0 and the STEP part's flat top is
cut at Z-0.0001 (the file's own float rounding), and the scallop over it is
at most 0.0011 mm inside the triangles with no correction applied.

**`0034-z-top-model-button.patch`** — Z Top in one click. Every input built
with a `selector` and `top` (Z Top on rough, outline, trace, pocket and area;
the stock's `camZTop`) gets a second button, `.z-select.z-model-top` with
`fa-angles-up`, beside upstream's round hover picker. It sets the field to
`modelTopZ()` (`cl-zplane.js`): the highest point of the platform's models,
`Box3.setFromObject(mesh).max.y` — scene height, the frame the hover picker
reads and `showZTop` draws in — over the view's unit scale, as the picker's
value is. Hovering it previews that plane. Level, contour, drill, helical and
register have no Z Top, so no button. The button is sized to the round one
(16 px; that one gets its size from padding, this has none). Checked in the
page: on the STEP bed part with stock = part, the area op's Z Top goes from 0
to 9.5001 — the part's top above the stock bottom.

**`0035-step-cad-face-picking.patch`** — on a STEP part a surface pick is
one whole CAD face. The pickers (area/pocket *surface*, scallop *avoid
surfaces*) store the clicked triangle, and `cam_surface_find` grows it at
pick and slice time by an angle flood over neighbouring normals (the
*follow* angle) — a guess on a mesh, which swallows or stops short of a
fillet depending on the angle. `load/step-in.js` now also returns each
body's CAD faces (occt-import-js `brep_faces`) as flat `[first, last]`
triangle ranges; the STEP import puts them in `widget.meta.cadFaces`, with
`cadTris` the triangle count they describe, before the widget is
catalogued (`load_verts` takes an optional `meta`). `meta` reaches the
worker with every sync and is saved with the workspace. `surface_find`
(`cam/work/init-work.js`) returns the picked triangle's whole range when
the widget has `cadFaces` for its current triangle count; any other widget,
a `filterZ` caller, or a STEP part whose mesh has since been edited keeps
the flood. Checked in the page on *Bed Ball Nose Test.step* (155 faces): 16
of 16 picks returned exactly their face; the angle flood agreed on 14 and
on the other two took 525 triangles for faces of 216 and 73. A scallop
limited to the picked top face (9 110 triangles) slices in 4.9 s, cuts only
at Z 0, and is 0.0000 mm into the part over 1 500 sampled points.

**`0036-step-cad-holes.patch`** — a STEP part's drill holes from its CAD
faces. `slice.js holes` slices the part at its flats and every `precision`
step, keeps round loops and stacks loops of one size into a depth. On *Bed
Ball Nose Test.step* that put every hole starting under a countersink 0.41 mm
too shallow (its top is no flat, so it is first seen a step lower), others
0.11 mm shallow, read 4.196 for 4.2, and added 65 "holes" 0.008 mm deep (the
countersink cones at the top flat), in 5.3 s. `work/cad-holes.js` reads them
off `meta.cadFaces`: a face of vertical facets whose vertices fit a circle
within 2 µm is a cylinder, a hole when it faces its axis; faces of one
cylinder are merged (CAD often splits a hole into two halves) and kept only
when they go all the way round (no gap over 45°), so fillets and slot ends
drop out; a counterbore is its two diameters. The output is what the slicing
finder returns (top-centre point, `area`, `depth`; shadow check, *from top*
and same-size selection shared through `finishDrills`). Rotary work and
non-STEP parts keep the slicing finder. In the page: 71 holes, every diameter
and depth exact, in 2.5 s; a drill op on the 62 Ø4.2 holes drills each to
Z-9.5001 (the 9.5 mm plate's bottom). Synthetic checks: split hole merged,
fillet and pin rejected, counterbore as two.

**`0037-step-assembly-bodies.patch`** — STEP assemblies. `bed_piccola.step`
is a 234.5 × 176.5 × 5.5 mm plate with six DIN 7991 M5 × 16 screws in its
holes, 10.5 mm out of its underside; imported grouped they made the part and
the stock 16 mm tall, named "body 1".."body 6". `load/step-in.js` names each
body after its assembly node (or nearest named ancestor) from occt-import-js's
`root` tree when the solid has no name of its own, numbers repeats
(" #1".." #6"), and returns each body's box `size` and `volume`. The
multi-body prompt names the largest body (by volume; by box for open
surfaces) and the others and offers **Largest only** / **All, grouped** /
**All, separate**. Checked in the page: *Largest only* on `bed_piccola.step`
gives one widget, 234.5 × 176.5 × 5.5 mm, centred on the bed. (Placement was
never the CAD file's: Kiri centres a new widget or group and lays the
platform out, which is why it sat beside an STL already loaded.)

**`0038-slice-errors-not-hangs.patch`** — a slicing error is reported, not an
endless "slicing". `run/worker.js` called the async `driver.slice()` and only
finished through its `ondone`; a throw inside `cam_slice` was a rejected
promise nobody awaited, so the worker never answered and the page sat on
"slicing"/"Rendering" with nothing in its console. The rejection is now
caught and sent as the slice's error ("slicing failed: …"), once. Found by a
walk of every operation: a loop with nothing after it, whose guard compared
the array of following operations to the count (`remain < count`, never
true) and then read `remain[0].type`; it compares `remain.length` now. (On a
flat job Kiri disables *loop*, *lathe* and the operations inside a loop —
they are rotary-only — so the app's "There are no operations" box is the
expected answer there.)

**`0039-laser-off-safe-z.patch`** — `OpLaserOff.prepare()` read `zmax` from
the ops object `prepare.js` hands every op; it has `zSafe`, not `zmax`, so the
retract went to Z undefined and every slice with a laser off logged
"computeBoundingSphere(): Computed radius is NaN" in the preview. It retracts
to `zSafe`, and only when a tool has run. And `prepare()` re-injected a last
move to the safe height carrying the current tool, which a job of only laser
on / off never had: `export.js` then read `getNumber()` of undefined. That
move now needs a tool. Checked: laser off alone, laser on alone, and laser
on → outline → laser off all export with no NaN and no console error; rough,
outline and scallop jobs still end on their `G0 Z1.0`.

The app's *There are no operations* box (`ui/cam_no_ops.py`) now says why when
the list is not empty: loop / lathe (and a loop's operations) switched off by
Kiri on a flat job, or every operation switched off.

**`0040-step-hole-wall-pocket.patch`** — area and pocket build their region
from the picked faces seen from above; a hole's wall is vertical, has no area
there, and `op-area.js` drops it ("remove invalid edges (eg. when vertical
walls are the only selection)"). A pocket with three hole walls picked made
no toolpath and no message — and with 0035 a click on a hole picks exactly
its wall. On a STEP part, a picked wall that is one of the part's round holes
(`cad-holes.js cadPickedHoles`, the drill finder's holes) now adds the hole's
exact circle (chords within 5 µm) to the region, at the depth the hole ends;
each hole once. Checked in the page: three Ø5.2 hole walls, 3 mm end mill →
3 092 moves on circles of radius 1.1 mm (2.6 − 1.5) at exactly those three
centres, down to the part's underside; before, 0 moves.

**`0041-expand-smooth-help.patch`** — the pocket / area *expand* and *smooth*
tooltips (`web/kiri/lang/en.js`) gave no number anyone could act on. *Expand*
moves the picked area's edge sideways by the value, in mm: 0 cuts to the edge,
+0.2 makes the pocket 0.2 mm bigger on each side, −0.2 leaves 0.2 mm on the
walls; depth and floors never change. *Smooth* is value ÷ 10 mm
(`op-area.js`): the outline is offset out and back in by it, filling notches
narrower than twice that and rounding inside corners — 1 (default) is 0.1 mm,
0 is off. Both get a range (−10 ~ 10, 0 ~ 20), which `ui/cam_ranges.py` shows
beside the label.

**`0042-ui-click-does-not-pick.patch`** — `moto/space.js` watches mouseup on
the whole window, and `kiri/app/mouse.js upSelect` handed every still click to
the pick modes with whatever lay under the pointer: the plain selection
checked the click was on the view (`target.nodeName === "CANVAS"`), the
surface / avoid / cylinder pick (`feature.on_mouse_up`) and the hover picks
(Z plane, holes) did not. Pressing *select surfaces* to finish a pick, over
the part, toggled the face behind the button. Both now need a click on the
view. Checked with real mouse events: after the fix, pressing the button over
the part leaves the picked faces as they were and ends the pick.

**`0043-ramp-per-operation.patch`** — ramping in ("ease down", shown as
*Ramp* by `ui/cam_labels.py`) was one switch for the job in Output. Rough,
outline, trace, pocket and area (clear / trace) now carry `ramp` and
`ramp_angle` (1–45°, shown while ramp is on), with per-type defaults
`camRoughRamp` … `camAreaRamp` (off) and the angle from `camEaseAngle`.
`prepare.js polyEmit` ramps by `currentOp.ramp ?? camEaseDown` and
`currentOp.ramp_angle ?? camEaseAngle`, so an operation saved before this
keeps the job's setting; the job-wide controls are hidden. The tooltip
arrays are joined with `\n` in place, since `language.js` only joins `LANG`
strings. Checked in the page: two pockets in one job, ramp on → 0 straight
plunges / 1 019 ramp moves, ramp off → 2 straight plunges.

**`0044-export-leaves-toolpath.patch`** — `work/export.js` turned
`print.output` into G-code coordinates in place (origin offset, scale,
inversions, and `z - stock.z` for a top origin). Animate plays that same
`print.output` in the stock's frame and does not prepare again once a path is
prepared, so Generate → Export → Animate played the path a stock height too
low: every cut through the bottom, and the rapids between cuts carving
channels through the stock. Export now remaps a copy of each layer (records,
points, and the layer's `mode` / `spindle`). Checked in the page: five holes,
Export then Animate — no channels, the readout ends at the clearance, and the
G-code is byte for byte the same as before the change.

Not an Animate fault, but it looks like one: a shank wider than the flute
(`shaft_diam` > `flute_diam`) is part of the animated cutter, so a cut deeper
than the flute length shows the shank rubbing the top as a red over-cut ring
round each hole (patch 0027), and the holes themselves read as shallow from
above. That is the job really gouging, not the simulation stopping early.

Not done in this stage, deliberately: **arcs** stay off (`ui/cam_arcs.py` —
the firmware does not height-map-correct G2/G3, and grbl splits arcs into
lines anyway); **floor heights** were already exact (a planar CAD face
tessellates into planar triangles at its true Z, which Kiri's flat detection
reads); **helical**'s cylinder pick already measures the whole cylinder and
gains little.

`tools/patches/kirimoto/0007-*.patch` declared its bundle check with `\w+`
for minified names; esbuild also uses `$` (`$u`, `E$`), and once 0034 shifted
the names the check failed although the code was there. Its `Bundle-Check`
lines now use `[\w$]+` (header only; the diff is unchanged).

**Line endings.** `.gitattributes` marks `*.patch` as `-text`. With Git for
Windows' default `core.autocrlf=true`, the patches were checked out as CRLF and
every one failed `git apply` against Kiri's LF sources.

### License hygiene

* `vendor/easytrace/` keeps its `LICENSE` and `NOTICE`. AGPL-3.0 obliges us to
  publish the corresponding source of any modified version we distribute —
  keeping the copy byte-identical means shipping it as-is satisfies that. If a
  change ever becomes unavoidable it **must** be published; prefer contributing
  it upstream.
* **"Eltryus"** is EasyTrace's brand and is trademarked. Credit it; never brand
  Piccola with it.
* Kiri:Moto is MIT — no restrictions beyond attribution.
* occt-import-js (the STEP reader, patch 0032) and the OpenCascade inside it are
  LGPL-2.1. It ships as separate files (`src/ext/occt/`, with both licence
  texts) loaded at run time, so a user can replace it — keep it that way:
  never bundle it into Kiri's packed scripts.

## Packaging

`piccola-cnc.spec` now builds **one folder, not one file**, and no longer excludes
QtWebEngine.

QtWebEngine is not a single library: it is a helper executable
(`QtWebEngineProcess`) plus ICU data, locale packs and v8 snapshots that
Chromium locates by path relative to that executable. One-file packing unpacks
everything to a fresh temp dir per launch — ~150–300 MB of extraction each
start, and `QtWebEngineProcess` frequently fails to resolve its resources,
giving a blank view rather than a clear error. Ship `dist/PiccolaCNC/` zipped.

`PICCOLA_ONEFILE=1` still selects the old single-file layout, but treat CAM
breakage there as expected.

> **Verify a packaged Windows build early.** This is the riskiest step in the
> whole integration and the one most likely to bite late.

## Presets (not yet done)

Neither tool is preloaded with Piccola machine parameters yet. Both store
settings in `localStorage`, which persists across restarts because the CAM page
uses a *named* (non-off-the-record) `QWebEngineProfile`, so a user configuring
the machine once is not re-doing it every launch — but a first run still starts
from upstream defaults.

Planned, in rough order of value:

1. **Kiri:Moto device profile** — work area, GRBL post, spindle-RPM command
   format. Injectable through its iframe frame-messaging API or as a bundled
   device JSON. Its GRBL output is the basic dialect (G0/G1/G2/G3, M3/M5, plunge
   drilling, no canned cycles), which matches the firmware.
2. **EasyTrace machine parameters** — GRBL is its best-tested post-processor.
   Longer term, contribute a "Piccola" post-processor upstream rather than
   forking.
3. **In-CAM "Send to machine"** via `QWebChannel` or Kiri:Moto's frame
   messaging, replacing download interception with a real button.
4. **Theming.** Note the app palette is *light* (`ui/theme.py`: `BG #FFFFFF`),
   while EasyTrace's workspace defaults to `data-theme="dark"`. Any EasyTrace CSS
   change must be published under AGPL; Kiri:Moto supports embed customization.
5. **Auto-levelling / probe-mesh for PCB milling** — the firmware already has
   height-map features; see bCNC (MIT) for reference probe-levelling code.

## Constraints

* Do **not** port either tool's toolpath math to Python. They are JS/WASM.
  Integrate, don't rewrite.
* Loopback only. No runtime network access.
* Follow existing UI conventions: pages are `QWidget` classes, sizing via
  `ui/scale.py` (`px`/`fs`/`bind`), colours from `ui/theme.py`.
