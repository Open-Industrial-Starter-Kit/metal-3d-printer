> ## Where this came from
>
> This is a copy of the **Piccola CNC desktop CAM app**, taken on 2026-11-27 from
> `piccola-cnc-desktop-cam-app` as the starting point for the OISK Metal 3D Printer's slicer
> and control app. **Nothing below has been changed yet**: every feature, page and protocol
> detail described in this README is the Piccola CNC milling machine's, not this printer's.
> Read it as the inherited starting point, not as documentation of what this app currently does.
>
> It is a reasonable base because the transport is already the same shape: the app probes
> serial ports with `{"type":"serialPing"}` and speaks line-delimited JSON at 2,000,000 baud to
> a processor it calls CPUB, which is how this printer's CPU-B works too. The machine-control
> side -- `connection.py`, `serial_worker.py`, `ws_server.py`, the Controls, Files and Console
> pages -- is the part worth keeping. The milling-specific parts are not: spindle, tool length,
> probing, facing, rotary axis, and the CAM page.
>
> ### What was deliberately left out
>
> - **`vendor/kirimoto/`** -- Kiri:Moto, the CAM/slicing engine (~71 MB). It was generated build
>   output and gitignored in the source repo too.
> - **`vendor/easytrace/`** -- EasyTrace5000, PCB image tracing (~5 MB).
> - **`tools/patches/kirimoto/`** -- 70 patches against Kiri:Moto, useless without it.
> - Build and release output, virtualenvs, `__pycache__`, and the source repo's git history.
>
> ### What has been removed since
>
> - **The CAM subsystem.** `ui/pages/cam.py` is now an empty placeholder that keeps the page's
>   interface (`gcode_exported`, `wire`, `export_root`, `shutdown`) so the export hand-off and
>   upload queue in `main_window.py` stay wired for a real slicer. The 54 `ui/cam_*.py` modules,
>   `cam_server.py`, the CAM docs and the vendor-fetch tooling are gone with it.
> - **The laser distance sensor**, which this machine does not have.
> - **Spindle control**, which this machine does not have.
> - **WiFi**, leaving USB serial as the only transport: the WebSocket server, the BLE
>   discovery beacon, the multi-machine chooser and the WLAN/hotspot settings sheets.
>
> Each was a separate commit, so `git log -- slicer-control-app` shows exactly what went and why,
> and any one of them can be read back in isolation.
>
> ### Still inherited and still wrong for this machine
>
> The milling-specific flows that were not in scope above are still here and still describe a
> mill: Z0 and tool-length probing, Facing, and Rotary Mode. Facing still emits M3/S/M5 into the
> G-code it generates, which is why the spindle removal left it alone -- half-editing it would
> produce wrong G-code rather than no G-code. The README text below documents all of it, for the
> Piccola machine.
>
> Original project README follows.

# Piccola CNC — CNC Desktop App

Cross-platform (Windows / macOS / Linux) desktop control app for the Piccola CNC
milling machine, built with **PySide6 (Qt)**. It mirrors the functionality of
the Android mobile app and talks to the machine firmware (CPUB / ESP32-C3) using
the same JSON protocol.

> **Keep this README current.** Any change that adds, removes or renames a
> feature, or that changes where the app writes files or logs, updates the
> matching section below in the same commit.

## Main functions

The side menu switches between five pages; the app menu (top bar) holds machine
settings.

| Page / area | What it does | Code |
|---|---|---|
| **Files** | Jobs and `.ply` scan files on the machine's SD card: search, sort, upload, download, rename, (batch) delete. Uploading a name that is already on the machine (case-insensitive) asks **Replace**, **Keep both** (uploads as `name_1.nc`, `name_2.nc`, …) or **Cancel**; for a CAM batch, Cancel skips that file. Upload progress shows at the top of the list, and the **Controls** and **Console** tabs and the axis-zero buttons are locked (dimmed) while it runs; once sent, the file stays there as "Upload finished, processing…" until the machine lists it. The machine sends its file list in one reply of at most 8 KB (about 47 files); past that the list arrives cut off, and the page shows the files that came through, with the count as e.g. **Files (48+)** (hover it for why). Delete old files to see the rest. Opening a job shows a blocking **Opening &lt;file&gt;** popup (with the toolpath read %) until the machine has opened it and the preview has read it. Opening a `.ply` shows it in the 3-D scan viewer, with optional surface reconstruction. | `ui/pages/file_manager.py`, `ui/pages/ply_viewer.py`, `recon/` |
| **Controls** | Jog pad (XY + Z), step size and speed, homing, unlock, soft reset, and the probing flows: Z0, tool length, XY0, centre, height map, rotary mode. **Go to laser sensor zero** (under Frame, and as step 2 of the **Height map** wizard's checklist) moves Z at 300 mm/min until the distance laser reads 0 ± 0.05 mm, the middle of its range, where the height map is most accurate. Use it only with the bed installed and the laser over a flat part of the bed, not a bed hole (a hole reads far away and Z would go very low). It switches the laser on if needed, reads it, moves Z by the reading, and corrects with the next reading (a first move *down* is a 1 mm test step until the direction is confirmed). Out of the laser's ±34 mm range it asks you to jog closer first. **Facing** (under Dry run, left of Go to laser sensor zero) generates a facing program and uploads it, without going through the CAM page: give it the face's X and Y size (**at most 130 × 130 mm**), how much to take off (**at most 2 mm**), a stepdown, a stepover (**at most the tool diameter**) and the tool diameter, pick the start corner and whether the passes run horizontally or vertically, and it writes a serpentine raster and sends it to the machine as a `.nc` file. Work zero is the corner you pick, with Z0 on top of the stock — jog there and zero X, Y and Z before running it. **Check where that zero sits**: every move is measured from it and the passes reach half the tool past each edge, so a zero too near the end of the travel trips a hard or soft limit part-way through the job — the popup says how far the program reaches from zero on each axis so you can check before sending. The 130 mm cap is why: the machine has 150 mm of travel, and a larger face leaves no margin for a zero set by hand at the corner of a piece of stock. The cutter is extended half its diameter past all four edges, so nothing is left standing at the rim; stepover and stepdown are read as maxima and spread evenly (50 mm at a 3 mm stepover becomes 18 passes of 2.94 mm, not 16 of 3 mm and a ragged remainder). The popup says how many passes and levels that comes to, how far the tool travels and roughly how long it will take before anything is sent, and refuses a face over 130 mm, a cut deeper than 2 mm, a stepover wider than the tool, or a job whose cutter sweep is wider than the machine's travel — one reason at a time, in form order, with the ✓ button greyed out until none are left. The 2 mm cut-depth cap is there because facing skims a surface flat rather than roughs it out, and because a depth typed with a slipped decimal point is the one mistake on this form the machine will carry out without anything looking wrong. Settings are remembered between runs. Greyed out in Rotary Mode and while a job is running. In Rotary Mode the X step sizes above 10 mm and the Z step sizes above 5 mm are greyed out (the custom step stays available), and the **Bed to front** button becomes **Bed forward**: it raises Z to the top of its travel, then moves the bed 40 mm toward the front (or less if the travel ends sooner; hovering the button says so), for a tool change with room to reach the chuck; the button then reads **Bed back** and returns the bed to where the rotary setup parked it. (The firmware refuses its own Y commands while the rotary is connected, so these two moves are sent as GRBL `$J=` jogs, which it passes through.) In Rotary Mode **Go to XY₀** becomes **Go to X₀** (the firmware's XY₀ move also moves Y, which it refuses with the rotary connected): Z goes up to the top of its travel, then X moves to the work zero; Y and A do not move and Z stays up. X₀ is checked against the soft limits first; outside them it says so and nothing moves. **Frame** checks the opened job's X/Y outline against the machine's travel first: if it would pass the soft limit from the current work zero, a **Frame Would Pass the Soft Limit** box says which axis and by how much, and nothing moves (if the outline or the travel cannot be read in time, it frames as before). **Frame** becomes **Stock length** in Rotary Mode (Frame traces X and Y, and Y is locked): Z goes up to the top of its travel, then X runs from the opened job's X minimum to its X maximum and back to where it was, so you can see and measure which stretch of the bar the job will cut. Y and A do not move and Z stays up. The job's X extent is checked against the travel (soft limits) first; if it does not fit from the current work zero, it says so and nothing moves. The laser sensor is switched off for it and back on after, as for Frame. | `ui/pages/controls.py`, `ui/pages/dialogs.py`, `ui/facing.py`, `ui/pages/facing_dialog.py` |
| **CAM** | Built-in CAM: Kiri:Moto (2D/2.5D/3D, rotary) and EasyTrace5000 (PCB). Exported G-code goes straight to the machine's file list. See [CAM](#cam). | `ui/pages/cam.py`, `ui/cam_*.py`, `cam_server.py` |
| **Live Preview** | 3-D view of the opened job's toolpath with live tool position; already-cut path in orange. Under the top-left buttons: where the job's **origin** (work zero) sits on the stock — e.g. *bottom left · top of stock*, or *center · rotary axis* for a Rotary Mode job — its **Min Z** (for a rotary job also how far into the bar that is), and the **tool(s)** it uses, read from the header this app's CAM export writes; a tool too short for its cut is shown in amber ("too short"). Files from other CAM software (e.g. Fusion) show Min Z and an *estimated* origin ("Origin ≈ top left · top of stock"), read from where the toolpath cuts. | `ui/pages/live_preview.py` |
| **Console** | Raw TX/RX traffic with the machine; send JSON or G-code by hand; save the log to a `.txt`. | `ui/pages/console.py` |
| **App menu** | Machine name, WiFi / hotspot, **CAM graphics** (GPU on/off for Kiri:Moto), Hardware settings (housing, door interlock, laser offsets, steps per mm, controller UF2 mode), Machine info and job history, **Firmware debugging** (switch each of the firmware's debug-print options on or off; the output appears in the Console), app version. In the developer offline mode (**Develop offline** on the search screen) it also has **Rotary axis connected (offline)**: on puts the simulated machine in Rotary Mode as if the rotary cable were plugged in, off takes it out. Offline, the **Rotary** setup on Controls runs through too: its two bed moves are acted out (Run, then Idle) and the cable counts as connected once the bed reaches the installation position. Nothing physical is simulated beyond that (jogs and other moves change nothing), and an offline setup does not overwrite the real machine's Bed back position. | `ui/app_menu.py`, `connection.py` |
| **Job finished** | A "Job finished" box, with the file name and run time, when a job runs to its end; the taskbar entry flashes if the app is in the background. Not shown when the job is stopped from the app, ends in an alarm, stops by itself below 90% progress, or the connection drops. | `connection.py` (`job_finished`), `main_window.py` |
| **Start check** | Pressing **Start** first reads the spindle speed from the machine: if the spindle is running (starting a job over a running spindle can destroy the spindle motor driver), a **Spindle is running** box offers **Stop spindle**, sends `M5`, counts down 5 s while it coasts to a stop (`SPINDLE_SPINDOWN_S` in `ui/pages/dialogs.py`), then asks **Start the job now?**. The same countdown runs if the spindle was switched off less than 5 s before. Then it proofreads the opened job against the machine: the program's extents (the `;MINICNC_BOUNDS` line the machine adds on upload, or the local copy), the travel (`$130`–`$132`, direction from `$23`) and the current work zero. If any part would go past the travel — e.g. a retract to Z 15.6 with the Z zero set only 12 mm below the top — a **Job does not fit** box says which axis, by how much, and what to move, with **Start anyway** / **Cancel**. If the numbers can't be read in time, the job starts as before. A soft-limit block during a running job shows **Job Outside Travel**. | `ui/job_fit.py`, `ui/bottom_bar.py` |
| **Time estimates** | The bottom bar shows two remaining times while a job runs: the machine's own (*Estimated remaining time*) and, under the progress bar, the **app estimate** -- worked out from the file the way GRBL's planner runs it (feed rates, the axes' maximum rates and accelerations `$110`-`$123`, corner speeds from `$11`), scaled by the feed override. On rotary jobs the machine's figure was measured nearly 3x too long (1 h 54 min for a 42-minute pulley finish); a 300 deg A test in 1, 300 and 3000 lines ran in the app's 15 s each. The *Job finished* run time no longer counts the 2 s end check. Every finished job is logged with its real time and both estimates in `job-times.csv` (see below). | `job_time.py`, `ui/bottom_bar.py` |
| **On connect** | Machine chooser when several machines answer over WiFi, then the homing prompt. | `ui/machine_chooser.py`, `ui/homing.py` |

## Connection model

The machine is always the **initiator** over WiFi (it's a WebSocket *client*),
while serial is a direct host→machine link. The app uses **serial when present
and falls back to WiFi otherwise**:

| Transport | Role | Discovery |
|-----------|------|-----------|
| **USB Serial** (preferred) | Host opens the port, JSON lines @ 2,000,000 baud | Auto-probes ports with `{"type":"serialPing"}` |
| **WiFi / WebSocket** (fallback) | App runs a WS **server** on port `8765`; the machine connects in | App **advertises over BLE** the Local Name `minicnc_<ip>` (dots → underscores). The machine scans for the `minicnc_` prefix, decodes the IP, and dials `ws://<ip>:8765` |

On connect the app sends `{"type":"set","setType":"setJsonSettings","jsonResponse":true}`
so the firmware replies in JSON, then polls `status`, `positions`,
`currentFeedRate`, `currentSpindleRPM`, `fileList` and the opened-file name.

BLE advertising is started only while serial is **not** connected (WiFi is the
fallback). It requires a per-OS backend (see below); if none is available the app
still works fully over serial.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate    # (Windows: venv\Scripts\activate)
pip install -r requirements.txt
python3 tools/fetch_vendor.py kirimoto              # optional, see CAM below
python main.py
```

### BLE backends (optional, per-OS)
- **macOS** – `pyobjc-framework-CoreBluetooth` (CoreBluetooth / `CBPeripheralManager`)
- **Linux** – `dbus-fast` + BlueZ ≥ 5.43 (`org.bluez.LEAdvertisement1`)
- **Windows** – `winrt` (`BluetoothLEAdvertisementPublisher`)

These install automatically from `requirements.txt` on the matching platform.

## CAM

The **CAM** tab embeds two open-source, browser-native CAM tools, giving Piccola
an all-in-one CAD → CAM → control workflow:

| Mode | Tool | License | Scope |
|---|---|---|---|
| **2D/3D** | [Kiri:Moto](https://github.com/GridSpace/grid-apps) | MIT | All general 2D / 2.5D / 3D CAM (SVG, STL, OBJ, 3MF, STEP) |
| ↳ STEP reader | [occt-import-js](https://github.com/kovacsv/occt-import-js) (OpenCascade) | LGPL-2.1 | Converts `.step` / `.stp` for Kiri:Moto; separate, replaceable files |
| **PCB** | [EasyTrace5000](https://github.com/RicardoJCMarques/EasyTrace5000) | AGPL-3.0 | Gerber/Excellon PCB only |

Both run entirely offline, served over loopback HTTP from `vendor/` by
`cam_server.py`. G-code exported inside either tool is intercepted and offered
to the existing File Manager upload path.

On export, each tool's deepest cut below the stock top is checked against its flute length (flute plus shank for a necked tool). A tool that cannot reach is listed in the G-code header as `; WARNING tool length: ...` and the export stops with **Cancel** / **Export anyway** before the file is saved or sent (`ui/cam_gcode_header.py`, `ui/pages/cam.py`). Holder stick-out is not checked: Kiri's tool record does not carry it.

**Step down 0.** In Rotary Rough, Surface Rough and Rotary Contour a *step down* of **0** means no step down: each area is cut straight to its final depth in one layer (the floor and, with *flat faces*, each cylindrical face; Surface Rough only its last, even layer). Rotary Finish already read 0 this way (Kiri:Moto patch `0068-rotary-rough-step-down-zero.patch`).

**One way only.** On this machine the bar shook when it turned with its top toward the operator (A up). Rotary Rough, Surface Rough, Rotary Contour and Rotary Finish have **one way only** (on for new operations): while cutting, A only goes down (the top of the bar moving away). Rings that go all round the bar are swept from left to right, so they still cut climbing; finishing passes all run the way the first does, the bar turning on instead of back, and the tool returns to the start of each pass clear of the part. Rings that do not go round the bar (pockets, islands) still turn both ways. Cut Off already only turns A down (Kiri:Moto patch `0067-rotary-one-way.patch`).

**Toolpath colour.** After Generate, toolpaths are drawn blue instead of black, so they show against the solid part (Kiri:Moto patch `0066-toolpath-lines-blue.patch`).

**Render starts on Solid.** Kiri:Moto remembered the last Render choice (Solid, Wire or Ghost) and brought it back at the next start; now every CAM session starts on **Solid**. Wire and Ghost still work within a session, and the part keeps the choice after **Generate** too, where Kiri:Moto used to redraw it as a 20% ghost (`ui/cam_render_solid.py`). *Animate* draws the model solid too, where it was a see-through yellow ghost (0.75, 0.5 in rotary; Kiri:Moto patch `0069-animate-model-solid.patch`).

**Tool presets.** Every operation panel has a **Preset** row under *Tool*: named cutting settings kept with the tool, such as *fast rotary al* or *finish al*. Choosing one fills the operation's spindle, feed, plunge, step over, step down, step angle, ramp, ramp angle and leave stock (whichever it has); **Save As…** stores the operation's current values as a new preset under a name you choose (the same name replaces it), **Update** writes them into the chosen preset (its name and type stay; settings the operation does not have are kept), **Delete** removes it. Each preset remembers the operation type it was saved from (Rotary Rough, Cut Off, ...): an operation lists the presets for its own type, and those for any, first, and the others under *Other operations* with their type shown -- so a cut-off preset is not applied to a rough by mistake, but nothing is hidden. Presets are part of the tool, so they travel with Export / Import Library. In the tool dialog, under the *Preview*, a **Presets** section shows the highlighted tool's presets: its presets in a list with their operation type, **new**, **rename**, **delete**, an **Operation** field (*Any* or one type) and one field per setting -- an empty field is not part of the preset, so applying it leaves that setting alone (`ui/cam_tool_presets.py`).

**Tool libraries.** In the tool dialog, **Export Library** saves every tool, under a name you give it, as `<name>.tools.km` (by default in `Documents\Piccola CNC\Tool libraries`; the same format Kiri:Moto writes, so it opens in any Kiri:Moto). **Import Library** loads such a file, or Kiri:Moto's own tools export, and asks: *Add and update* (new tools are added, tools with the same ID are replaced by the library's version, yours stay) or *Replace my tools* (your tools become exactly the library's; operations and defaults that used a tool that is gone move to the nearest cutting diameter left, and the app says how many). Tool numbers are kept unless one is already taken. Keep one library per material or project and load the one you need (`ui/cam_tool_library.py`).

A **Guide** menu (next to *Analysis*) holds two beginner's guides.

**Operations guide** is a set of illustrated guides, one button each along the top of the panel (click one to open it; the ◀ / ▶ buttons at the bottom step to the previous and next): *Start here* (the order of a job: rough, finish, Animate, Export), *Rotary: operations* (Rotary Rough, Surface Rough, Rotary Finish — *Linear* for features along the bar such as pulley teeth, *Spiral* for round shapes — Rotary Contour, Cut Off), *Rotary: settings* (feed in degrees per minute, Step Angle and how a coarse one misses gaps or cuts into teeth, Step Down as equal passes, Step Over as a fraction of the cutter, A zero and Bed back), *Flat (3-axis): operations* with Ramp, and *Example: GT2 pulley* with its settings, a to-scale drawing of why the teeth need a 1 mm ball, and a drawing of the 1 mm ball at work with the precautions that keep a 1 mm cutter from breaking (short stick-out, careful Z zero, no plunging, a drop of WD-40 from the PiccolaCNC drip add-on, clearing chips). Each operation shows the same diagram as the add-operation menu (`ui/cam_op_art.py`); Step Angle, Linear vs Spiral, Ramp, Step Down, the GT2 tooth gap, tool stick-out and the 1 mm ball finishing teeth have their own drawings (`ui/cam_ops_guide_art.py`); photos live in `images/` and are listed in `PHOTOS`. The text is the `TOPICS` list in `ui/cam_ops_guide.py`.

**Materials** is a guide to what the machine can cut: woods & MDF, plastics (POM, HDPE, acrylic, PVC foam), soft metals (aluminium, brass) and foam & wax. Each material shows the cutter to use, spindle speed, feed, plunge, depth per pass, step-over, a warning and tips — starting points within this machine's limits (12,000 RPM, 1,500 mm/min). **Apply to selected operations** works the values out for each operation's own cutter (feed = RPM × flutes × chip load) and, where the material needs a different cutter (e.g. single-flute for acrylic), switches to the best match in the tool library, read by flute count from the tool name. Every change is listed before it is applied (`ui/cam_materials.py`).

In Kiri:Moto's **Animate** view the simulated stock is drawn as a solid block —
opaque, and closed underneath — so the toolpath no longer shows through the
material it is cutting (`ui/cam_anim_solid.py`). The simulation is a height
field, so a cut that goes right through the stock still reads as closed when
viewed from below.

The **Animate** transport keeps up with every button: changing the speed
while it plays, pausing and playing again quickly, stepping, or dragging the
timeline mid-run no longer leaves *jump to the end* doing nothing or the
play/pause button out of step with the cut. A press that needs the
simulation to stop first waits for it rather than being dropped, and play at
the last record starts over. **1x is real time**: each move takes its length
over its feed rate, with the simulation's own computing time counted in, so
4x is four times the machine (rotary used to run 1x slow and 4x-16x up to 80%
too fast; flat moved 1 mm per frame whatever the feed). When the stock cutting
cannot keep up -- a fine finishing pass at 16x or 50x -- it plays as fast as it
can, never faster than the setting. Rotary stock cutting is about 5x faster on
finishing passes (the tool's shaft is left out of a cut when it cannot reach
the bar; the result is identical), and a jump stays answerable: pause or a new
click lands within a fraction of a second even late in a heavy job, where it
used to wait for a minute-long batch of cuts. The timeline works like a media
player's. A rotary job's stock is simulated as a **radius grid** -- a radius
for every point along and around the bar, the rotary form of the flat
animator's height field -- instead of exact 3-D booleans, which made a fine
finishing pass take most of an hour to simulate and a jump minutes. Its cells
are 0.05 mm along the bar and about 0.05 mm wide round its surface (1760
round a 28 mm bar), and every cut lands at the tool's exact angle, so a 1 mm
ball's fillets come out round rather than chamfered. As soon
as the job opens in Animate the whole of it is **rendered ahead** in the
background (about 12 seconds for a 1 mm ball finish on a 25 mm pulley) with
a snapshot of the stock every half percent; a **Rendered** bar under the
**Progress** timeline fills as it goes and disappears once the whole job is
ready. Click anywhere, or press *jump to the end*, and the stock,
the tool and the percentage are there within a tenth of a second, the
finished part included; the animation then plays on from there if it was
playing, or waits there if paused. The rendered snapshots are kept on disk
(`cam\anim-cache\`, see below), so a job already rendered once opens with
every point of it at once. The grid cannot show an undercut -- a sliver left
hanging over a deeper cut at the same spot, a fraction of a millimetre at a
steep slot wall; the exact simulation is still there behind the
`controller.animMesh` setting, and the grid's cell size behind
`controller.animGrid` (mm). Flat jobs keep their height field; a jump
there cuts its way to the point, which takes seconds. The timeline has its own row under the
buttons, as wide as the bar and drawn 1.2x Kiri's size so it is easier to hit.
The tool on screen is drawn smooth (72 sides) rather than as a 20-sided prism. The rotary toolpath trace now draws
(it was hidden by a stray NaN at the start of the job), and the percentage
only reads 100.0 at the real end (Kiri:Moto patch
`0070-animate-transport-one-run-at-a-time.patch`; the timeline row is
`ui/cam_anim_end.py`).

**Export from Animate.** Kiri:Moto tears the animation down when an export
starts (upstream does, so the worker is free for the G-code), but it stays in
the Animate view -- which then showed no stock and a render bar stuck at 0%.
When the export dialog closes and Kiri is still in Animate, the app now presses
Animate again: a job rendered before is back at once from `camnim-cache\`,
one that was not starts its render again (`ui/cam_anim_after_export.py`).

Each operation in Kiri:Moto's operation list is named automatically
(`rough_1`, `pocket_1`, …) and can be renamed with the pencil button next to
its delete cross, or by clicking the `✎ name` strip at the top of its
settings pop-up. Names must be unique, since each name is its own toolpath
layer; clearing the name gives the automatic one back (`ui/cam_op_names.py`).

Every Kiri:Moto G-code export opens with a short comment header: tools used
(number, diameter, flute length, type, lowest Z each one reaches), the
operations with their step-down, feeds and spindle speed, and the program's
X/Y/Z extents ending in `; ZMIN=…`. The export dialog's **gcode preview** is
enlarged to show it (`ui/cam_gcode_header.py`). The machine drops `;` lines
when it stores a program, so a Rotary Mode export also carries its
`rotary:` line (where Z 0 is, and the stock radius) a second time as a
`(rotary: ...)` comment, which the machine keeps: Live Preview needs it to
draw a *top of the bar* job at the right radius when it fetches the file
back from the machine. Kiri's *zip* download is not
covered; its per-operation files have no header. Kiri's 3D-printer section of the export dialog
(*printer* / *spool* / *send* / *print*) is not part of it: `tools/fetch_vendor.py`
cuts it out of the page and builds Kiri:Moto with only the upstream modules
this app uses (`"only"` and `"cut"` in its sources table).

Kiri:Moto gets an **analysis** menu next to *Render*. Its **minimum tool
diameter** item answers one question before a job: the largest flat cutter
that can still reach every area of the model from the top, where the
narrowest spot is, and which tools in the library fit or are too big
(`ui/cam_analysis.py`). It is 3-axis only: ball and taper tools are judged by
their diameter, rotary (indexed) parts are skipped, and gaps narrower than
about three grid cells (shown in the report) are not detected.

**Scallop** (under *Finish strategy* in Kiri:Moto's add-operation menu) is a
3-D finish for ball end mills, like Fusion's Scallop: rings from the part's
outline inwards, each a constant stepover from the last measured *along the
surface*, so steep walls get as fine a finish as flats. It adds an *area*
operation set to surface mode with the new **scallop** pattern, the part's
outline as the area and a ball end mill from the library; stepover is the
usual fraction of the tool diameter, and **ring order** chooses outside in
(from the boundary inwards) or inside out (from the middle outwards). Its
**scallop limits** narrow where it cuts, after Fusion's: a **slope** range
(e.g. only walls steeper than 40°), the op's own **Z top / Z bottom**,
**avoid surfaces** picked on the model with a **clearance** the whole tool
keeps from them, and **rest machining** after a larger **previous tool** —
only where that tool could not reach. Limits only take area away; the tool is
always placed against the whole part, so none of them can make it gouge. It runs on the CPU, and the same patch
makes Kiri's own *linear* and *offset* surface patterns work in the app — they
need WebGPU, which the app's built-in browser does not have, and used to fail
with "Failed to initialize WebGPU". A ball or flat end mill is placed exactly
on the model's triangles, and the short moves between rings follow the
surface (or lift over it), so sharp edges such as a slot's rim are not cut
into. It is three Kiri:Moto patches
(`tools/patches/kirimoto/0029-scallop-finish.patch`,
`0030-scallop-limits-and-rest.patch`, `0031-exact-drape-and-ring-links.patch`),
so it needs Kiri:Moto re-vendored
(below) on each machine before it appears.

**Rotary (4-axis) operations.** With Rotary Mode on, the add-operation
menu has a *Rotary (4-axis)* group: roughing first, then finishing, then
*Cut Off*, then *Index* and *Loop*. The group disappears on a flat job. Hovering an entry
shows a diagram of its toolpath. While Rotary Mode is on, an amber **ROTARY MODE**
badge sits in the top-left corner of the viewport; it steps down below the part
size while a part is selected, and below the Arrange / Generate bar when a narrow
window brings that bar over the corner (`ui/cam_rotary_badge.py`).

- **Rotary Rough** cuts in layers of constant radius, one *step down* apart,
  from the stock's outside inwards, and always ends on the floor: a 2.4 mm
  groove with step down 2 is cut at 2.0 and then 2.4. **flat faces** adds a
  layer at the radius of each cylindrical face so it is roughed flat instead
  of in steps. The floor and face layers sit at the top of the face's
  facets, up to 0.05 mm above its lowest point (before patch
  `0062-rotary-rough-floor-on-faceted-faces.patch` that layer could be
  dropped, so with a large step the floor was never reached).
- **Surface Rough** cuts in layers that follow the part's own surface, from
  the stock in to an even *leave stock* skin for the finish.
- **Rotary Finish** is Kiri:Moto's lathe operation under a clearer name: a
  ball end mill along the part as it turns, in lines along X (*linear*), a
  circle at each step, or, with **spiral**, one continuous helix with a full
  circle at each end, so no sideways step marks the surface. Where the
  profile rises steeper than 45° between two samples (a shoulder), every
  style goes up first and then across instead of cutting the corner, so it
  cannot cut into the shoulder. With a *step down*, the finish is cut as a
  stack of passes from the outside in, each one the profile moved outwards
  by a multiple of the step (a helix each with **spiral**); *expand* says how
  far outside the part's largest radius the first pass starts, and no pass
  ever goes inside the profile (before patch `0054` a negative *expand* pulled
  the passes into the part). The depth from that start to the deepest point
  is split into equal passes, none deeper than the step: 0.815 mm at step
  0.2 is five passes of 0.163 (before patch `0064` it was four of 0.2 and a
  first one 0.015 deep, a whole helix all but in air). *Expand* is 0 or more:
  a negative one used to start the passes below the profile, so step down
  did nothing and the finish cut the full depth in one pass (patch `0063`).
  With **linear**, **climb only** (on for new operations) cuts every line
  climbing: on this machine (A down turns the top of the bar away from the
  operator, X+ to the right, spindle clockwise) a line climbs when X runs the
  way A steps, and between lines the tool goes back 0.3 mm clear of the part
  and drops in at the plunge rate. Off, lines go back and forth and every
  other one cuts conventional, which in aluminium smears and welds to the
  cutter. Spiral already climbs on every pass on this machine (patch `0065`).
- **Rotary Contour** finishes steep walls (ribs, shoulders, flights) after
  roughing: the Rotary Rough layers, 0.5 mm apart by default, each cut only
  with the ring that runs along the walls. The ring stands off the walls by
  up to one sample (radius × step angle, about 0.1 mm at the default 0.5°);
  a smaller step angle brings it closer and takes longer.
- **Cut Off** parts the finished piece from the bar: a groove just outside
  each end of the part (*gap at start* / *gap at end* from the end faces,
  kept by the widest part of the tool, shank or flutes; a negative gap, down
  to -10 mm, moves the groove into the part to take off a ring left at the
  end face),
  the tool at one place along the bar while the stock turns and goes a
  *step down* deeper with every turn, down to a *core diameter* left in
  the middle (16 mm by default) that you saw through by hand, so nothing
  falls free while the machine runs. *Groove width* wider than the tool
  cuts several passes side by side, never more than three quarters of the
  tool's diameter apart so that they overlap and leave no thin wall between
  them (6 mm with a 3 mm cutter is three passes 1.5 mm apart). Either end
  can be switched off. The
  tool's flutes must reach from the bar's surface down to the core (the
  tool-length check on export says if they don't): a 2 mm cutter with 6 mm
  flutes parts a 26 mm bar to a 15 mm core, not to 4.

**Rotary feeds.** The machine applies the F word to the A axis as degrees
per minute, so an operation's *max feed rate* is also how fast the stock
turns: 1200 is 3.3 turns a minute, and a spiral finish advances one *step
over* per turn -- a fraction of the tool's diameter, so 0.2 with a 1.5 mm
ball is 0.3 mm a turn and X creeps along at about 1 mm a minute at 1200.
Its *max plunge rate* applies to moves that are mostly
down (the plunge to the first point, a shoulder taken as a Z move); a
helix or a sloping pass that turns the stock while it descends runs at
the feed rate (before Kiri:Moto patch `0058-rotary-feed-not-plunge.patch`
every descending move was capped at the Limits panel's *Max Plunge Rate*,
so a 1200 finish ran at 200 wherever the profile fell). The feed override
in the bottom bar scales all of it, and stays set between jobs.

Rotary Rough and Surface Rough clear each layer in offset rings round the
part, one *step over* apart, only where there is stock above the layer (a
square blank is not cleared in air out to its corners), and lift to the
safe height only between separate areas. **Ramp** (on by default, also on
Rotary Contour) enters each layer on a slope instead of plunging: the tool
starts 0.5 mm above the layer it just cut, goes down along the ring at the
**ramp angle** (default 5°) while the stock turns, then cuts the whole ring
at depth. Switched off, each layer is entered with a straight move down the
radius, as before patch `0060-rotary-ramp-in.patch`. Each operation has its own feed,
plunge rate, step down, step angle and leave stock, kept apart from the
others'. The rotary axis is the centre of the part's bounding box. With
**Cylindrical** stock (the **Stock margin** panel's *side* and *length* are material beyond the part, each a total split between both sides, and the line under them says the bar that makes, e.g. *stock Ø 31.04 × 20.05 mm long*), *Animate* carves a round bar, as the toolpath assumes,
and colours it by what has cut it: grey for untouched stock, blue where a
roughing operation has been, green where a finishing operation has (the
model's own surface). The progress box names each step as the menu does
("Rotary Rough").

**Rotary Z zero.** In Rotary Mode every Z in the exported G-code counts
from a place the origin picker's *z zero* row chooses: **rotary axis** (the
centre line of chuck and tailstock, as Fusion's rotary posts write it: Z is
the radius) or **stock top** (the top of the mounted bar: Z is the depth
below it). Set the machine's Z zero to match: for *rotary axis*, with the
bar not yet mounted, touch the tool on the tailstock centre point and press
Z0, then mount the bar; for *stock top*, touch the top of the mounted bar
and press Z0. X 0 is where the picker puts it along the bar (the middle by
default); Y is locked. The *Origin top* / *Offset Z* fields do not move a
rotary job's Z. The header says which (`; rotary: Z 0 on the rotary axis
…` / `… on top of the bar …`), Live Preview shows *center · rotary axis*
or *center · top of the bar* and how far into the bar the job cuts, and
the tool-length check measures that depth from the bar's surface. A rotary
job retracts *Z clearance* above the bar's surface (above a square blank's
corners) and ends parked 3 mm above it, so the machine needs at least 3 mm
of Z travel above the bar top from that zero; with a long tool, push it
further into the collet if the start check says the job does not fit. A
rotary job winds A up a turn per revolution; the exported file ends with
`G10 L20 P1 A<remainder>`, which drops the whole turns from the read-out
and keeps the fraction, so A reads as a plain angle after the job and A0
stays where it was set.
Before
Kiri:Moto patch `0053-rotary-z-zero-on-axis.patch` the export put Z 0 a
whole stock diameter above the axis, so a job zeroed on the axis cut one
diameter too deep and every tool looked "too short": re-export such jobs.
Kiri:Moto patches `tools/patches/kirimoto/0045-rotary-roughing.patch`,
`0046-rotary-contour.patch`, `0047-lathe-spiral.patch`,
`0048-rotary-animate-round-stock.patch`, `0049-rotary-progress-names.patch`,
`0050-rotary-animate-cut-colours.patch`, `0051-lathe-no-corner-cutting.patch`,
`0052-surface-rough-no-wall-corners.patch`,
`0053-rotary-z-zero-on-axis.patch`, `0054-lathe-step-down-levels.patch`,
`0055-rotary-retract-round-stock.patch`, `0056-rotary-unwind-a-at-end.patch`,
`0057-rotary-cut-off.patch`, `0058-rotary-feed-not-plunge.patch`,
`0059-rotary-part-on-axis.patch`, `0060-rotary-ramp-in.patch`,
`0062-rotary-rough-floor-on-faceted-faces.patch`,
`0063-lathe-expand-not-below-the-part.patch`,
`0064-lathe-equal-step-down-passes.patch`,
`0065-lathe-linear-climb-only.patch`,
`0066-toolpath-lines-blue.patch`,
`0067-rotary-one-way.patch`,
`0068-rotary-rough-step-down-zero.patch`,
`0069-animate-model-solid.patch`,
`0070-animate-transport-one-run-at-a-time.patch` and
`0072-rotary-anim-grid-index-typed.patch`,
so they need
Kiri:Moto re-vendored (below) on each machine before they appear; the menu
group and diagrams are `ui/cam_op_menu.py` and `ui/cam_op_art.py`.

**STEP import.** Kiri:Moto opens **`.step` / `.stp`** files (*import file*,
or drag and drop) as well as STL, OBJ and 3MF, and every operation works on
them. OpenCascade, compiled to WebAssembly (`occt-import-js`, LGPL-2.1,
shipped as separate files under `vendor/kirimoto/src/ext/occt/` with its
licence texts), turns the CAD model into triangles within **0.01 mm** of its
surfaces, in millimetres whatever unit the file uses. It runs in the
background while the page shows *Converting STEP*. The part comes in **laid
flat** on its largest flat face (a part already lying on it is left as
drawn), and is placed in the middle of the bed — not where it sat in the
CAD file. A file with no solids or surfaces in it (only lines — a sketch) is
refused with *STEP import failed: the file has no solids or surfaces in it*.
Kiri:Moto patch `0032-step-import.patch`; it needs the same re-vendor.

**Assemblies.** A STEP file often holds the part *and* the screws or
fittings modelled with it. Each body is named after the CAD assembly
("DIN 7991 M5 16mm (1) #2", not "body 2"), and a file with several bodies
asks how to bring them in, naming the largest and the others: **Largest
only** (just the main part — what you usually want to machine), **All,
grouped** or **All, separate**. Kiri:Moto patch
`0037-step-assembly-bodies.patch`.

On a STEP part, **picking a surface takes one whole CAD face**, exactly as
it was drawn — Area and Pocket's *surface* picker and Scallop's *avoid
surfaces* alike. On an STL there are no CAD faces, and a pick still spreads
to neighbouring triangles within the *follow* angle. The faces are saved
with the workspace; editing the part's mesh afterwards falls back to the
angle pick. Kiri:Moto patch `0035-step-cad-face-picking.patch`. Picking a
**hole's wall** (Pocket or Area) machines that hole: its exact circle, down
to where the hole ends — a wall is vertical, so on its own it has no area
and used to give no toolpath. The tool has to fit inside the hole. Kiri:Moto
patch `0040-step-hole-wall-pocket.patch`.

On a STEP part, **Drill finds its holes from the CAD model**: each round,
vertical hole with its exact diameter, where it starts (under a countersink
or counterbore too) and how deep it goes — no sliced estimate, and no
countersink cones mistaken for shallow holes. Fillets, slot ends and pins
are not holes. Other parts, and rotary work, keep Kiri:Moto's own hole
search. Kiri:Moto patch `0036-step-cad-holes.patch`.

**Help on hover.** Hovering a control in Kiri:Moto shows what it does in a
readable panel; hovering an operation in the add-operation menu adds a
picture of its toolpath. The Area / Scallop pop-up's **Mode** menu shows
its three choices — *Clear* (empty the area to a flat floor), *Trace*
(follow its edge) and *Surface* (ride the 3D shape) — each with a small
picture and what it is for (`ui/cam_tooltips.py`, `ui/cam_op_art.py`,
`ui/cam_mode_tips.py`).

**Slicing errors.** If generating a toolpath fails inside Kiri:Moto, the
error is shown and the slice ends, instead of the page staying on
*slicing* for good (Kiri:Moto patch `0038-slice-errors-not-hangs.patch`).
When nothing would run, Generate says why: an empty list (*There are no
operations*), *Loop* or a rotary operation on a flat job (they work only with Rotary
Mode on, so Kiri:Moto switches them off, and the operations inside a loop
too), or every operation switched off (Ctrl+click one to switch it back
on) — `ui/cam_no_ops.py`. A *laser off* operation retracts to the safe
height, and a job of only *laser on* / *laser off* exports (Kiri:Moto patch
`0039-laser-off-safe-z.patch`).

**Ramp in, per operation.** Rough, Outline, Trace, Pocket and Area (clear /
trace) each have a **Ramp** on/off under *Step Down*: on, the tool goes down
along its own path at the **Ramp Angle** (1–45°, default 10°) instead of
plunging straight down — a spiral (helical) entry in a hole. Each operation
type remembers its own choice for the next one; contour and surface passes
do not ramp. The old job-wide *Ramp* in Output is no longer shown (Kiri:Moto
patch `0043-ramp-per-operation.patch`).

**Views panel.** The **VIEWS** panel at the bottom right of the 3D view
(top / front / … / fit, perspective or orthographic, focal point, measure,
stock box) folds away: click its **VIEWS** caption to hide the buttons —
it shrinks to a small *VIEWS ▸* tab, so it no longer covers the Operation
List — and click again to bring them back. The app remembers which
(`ui/cam_views.py`).

**Part and stock size.** Click a part and its size shows in the top-left
corner of the 3D view, e.g. *part 25.00 × 25.00 × 25.00 mm* (X × Y × Z as it
sits on the bed; several selected parts show the box round all of them). It
goes away when nothing is selected (`ui/cam_part_size.py`). In the **Stock
margin** panel (Kiri's *Stock* group, renamed because every field in it is
material added beyond the part), **Rectangular** stock shows the block its offsets make under the
three offset fields, e.g. *stock 39.00 × 30.00 × 30.00 mm*, and
**Cylindrical** stock shows the bar, e.g. *stock Ø 24.90 × 49.38 mm long*.
Both follow the fields as you type.

**Z Top = top of the model.** Every **Z Top** field — on Rough, Outline,
Trace, Pocket and Area (Scallop included) pop-ups, and the stock's own Z Top
— has a second small button (**⇈**) beside its round picker. One click sets
it to the model's highest point; hovering it shows that height as the Z
plane. The round picker still takes the height of any point clicked on the
model. Kiri:Moto patch `0034-z-top-model-button.patch`.

**Z zero.** With the origin on top of the stock, Z0 in the exported G-code is
exactly the stock top. Upstream Kiri:Moto wrote every Z 0.01 mm deeper than
computed (its origin marker's display lift leaked into the export; a 1 mm
safe height came out as Z0.99) — fixed by Kiri:Moto patch
`0033-exact-z-zero.patch`. G-code made before it cuts 0.01 mm deeper than the
same job exported now.

`vendor/easytrace/` is committed. `vendor/kirimoto/` is **not** — upstream
publishes no static web build, so it is generated locally (needs Node + npm,
**at vendor time only**):

```bash
python3 tools/fetch_vendor.py kirimoto
```

Without it the app runs fine; the Carve tab just says it is not vendored yet.
See [docs/cam-integration.md](docs/cam-integration.md) for the full design,
the URL-mapping details, and the packaging consequences.

## Layout

`main.py` boots the Qt app; `main_window.py` assembles the shell (top bar, side
menu, page stack, right panel, bottom bar). Pages live in `ui/pages/`. All UI
panels are wired to the central `Connection` hub (`connection.py`), which owns
the `SerialWorker`, `WsServer`, and `BleAdvertiser`.

## Packaging

`piccola-cnc.spec` builds a **one-folder** bundle (`dist/PiccolaCNC/`), not a single
`.exe` — QtWebEngine's helper process and resource files do not survive one-file
packing. Ship the whole folder. See `build_windows.bat`.

## Troubleshooting, errors and logs

The packaged app is a windowed program: it has **no console**, so anything it
prints is lost. When something goes wrong on a user's PC, collect the files
below. Paths are for the packaged Windows app; Linux/macOS equivalents are
given where they differ.

> Shortcut: in Windows press **Win+R**, paste `%LOCALAPPDATA%\PiccolaCNC` and
> press Enter to open the app's data folder.

### Files the app writes

| File / folder | What it holds | When it appears |
|---|---|---|
| `%LOCALAPPDATA%\PiccolaCNC\crash.log`<br>(Linux/macOS: `~/.config/piccola-cnc/crash.log`) | One `=== start …` line per launch (with the graphics mode), the Python stack of every thread on a hard crash, and any uncaught Python exception. Appended across launches; cleared once it passes 1 MB. | Every launch (builds **0.6.2 and later**) |
| `%LOCALAPPDATA%\PiccolaCNC\job-times.csv`<br>(Linux/macOS: `~/.config/piccola-cnc/job-times.csv`) | One row per finished job: start, end, file, real minutes, the app's estimate and the machine's estimate (its first estimate during the run plus the time already run). Appended; delete it to start over. | After the first finished job |
| `%LOCALAPPDATA%\PiccolaCNC\cam\load-failure.txt` | Why a CAM tool did not start: time, tool, Chromium version, Chromium flags, WebGL renderer (`none` = no WebGL), compatibility-graphics on/off, last console errors. Overwritten each time. | When Kiri:Moto stays on its loading screen |
| `%LOCALAPPDATA%\PiccolaCNC\safe-graphics` | Marker: if present, the CAM tools run on software rendering (no GPU). Delete it, or switch **Enable GPU for Kiri:Moto** back on in the app menu, to use the GPU again. | After accepting "Restart with software rendering" |
| `%LOCALAPPDATA%\PiccolaCNC\cam\storage`, `cam\cache` | Kiri:Moto's browser storage (workspace, settings) and HTTP cache. Deleting both resets the CAM tools to a clean state. | First CAM use |
| `%LOCALAPPDATA%\PiccolaCNC\cam\anim-cache\` | The rotary animator's rendered snapshots, one file per job (named by a fingerprint of its toolpath; each stores only what changed between snapshots, tens to a few hundred MB depending on the job; the 24 newest are kept, within 4 GB in all). Delete the folder to re-render from scratch. | After a rotary job's first Animate |
| `%LOCALAPPDATA%\PiccolaCNC\cam-exports\` | G-code staged by CAM exports, and by **Facing** on the Controls page, before it is sent to the machine. Each file is deleted once the machine has it. | On CAM export, or on sending a facing job |
| `~\.piccola-cnc\gcode-cache\` | Copies of uploaded and downloaded G-code files and point-cloud scans (`.ply`), used by Live Preview and the scan viewer. Deleting a file in the file manager also removes its copy here; "clear stored cache" empties it. | On upload / preview |
| `%TEMP%\piccola-cnc-desktop.lock` | Single-instance lock. If the app says it is already running when it is not (e.g. after a crash), delete this file. | While the app runs |
| Registry `HKCU\Software\InMachines\MiniCNC2`<br>(Linux: `~/.config/InMachines/MiniCNC2.conf`) | User preferences (step size, speed, view options, …). | On first change |

Running from source, Qt names the data folder after the interpreter, so the
`cam\…` and `cam-exports` paths above sit under `%LOCALAPPDATA%\python\`
instead. `crash.log` and `safe-graphics` stay under `%LOCALAPPDATA%\PiccolaCNC`.

### Logs Windows keeps itself

Useful for crashes in builds without `crash.log`, and for native crashes (GPU
driver, Qt) where Python gets no chance to write anything:

1. **Event Viewer**: **Win+R** → `eventvwr.msc` → *Windows Logs → Application*.
   Look for **Application Error** entries for `PiccolaCNC.exe` at the time of
   the problem. **Faulting module name** names the culprit; an AMD/Intel/NVIDIA
   driver DLL (e.g. `atiumd64.dll`, `amdxx64.dll`, `igd10iumd64.dll`,
   `nvwgf2umx.dll`) means a graphics driver crash: update the driver, or turn
   the GPU off for Kiri:Moto.
2. **Windows Error Reporting**: `C:\ProgramData\Microsoft\Windows\WER\ReportArchive`
   and `…\WER\ReportQueue`, folders named `AppCrash_PiccolaCNC.exe_…`, each with
   a readable `Report.wer`.

### Seeing everything: run from source

Messages such as `[live-preview] GPU renderer unavailable …`,
`[mesh-canvas] …` and `[cam] port … unavailable` are printed to the console
only. To see them, run the app from a terminal:

```bash
venv\Scripts\python main.py                    # Windows
python3 main.py                                # Linux / macOS
python main.py --safe-graphics                 # one run with CAM software rendering
```

### Common problems

| Symptom | Where to look / what to do |
|---|---|
| Kiri:Moto stuck on "Kiri:Moto is Loading" | `cam\load-failure.txt`. `webgl: none` → GPU/driver blocked; accept the software-rendering restart, or update the graphics driver. |
| `load-failure.txt` empty, or the app closes by itself | `crash.log` and the Event Viewer **Application Error** entry; usually a graphics driver crash. |
| CAM tools behave oddly after an update | Delete `cam\cache` (and `cam\storage` to reset CAM settings). |
| "Piccola CNC is already running" | Close other copies, or delete `%TEMP%\piccola-cnc-desktop.lock`. |
| Machine not found over WiFi | BLE backend missing or Bluetooth off; see [BLE backends](#ble-backends-optional-per-os). USB serial always works. |
| Large download (e.g. a multi-MB `.ply` scan) times out or the machine disconnects | Firmware older than the `downloadedBytes` change blocks for ~2 s per MB before it starts sending; update the firmware. The app re-requests a missing chunk up to 4 times; the **Console** shows `[Download] … asking again`, or `a chunk went missing` when it had to give up. |
| Machine stops in **Alarm** during **Frame** (or a jog/move) | The frame went past the machine's travel. A **Soft Limit Triggered** popup says so (from the firmware's `softLimitExceeded`, GRBL `ALARM:2`, or `error:15`); any other alarm while framing shows **Frame Stopped** with its code. Check the work zero and that the job fits inside the machine, then unlock. |
| **Frame** does nothing, machine stays Idle | The firmware's own soft-limit check blocked the frame before it moved (Console: `Soft-limit blocked command: …` / `Macro aborted by soft limits …`). An **Outside Machine Travel** popup names the position it would have gone to, in work coordinates. Move the work zero (or the part) so the whole job fits inside the machine's travel. |
| **Y Axis Locked** in Rotary Mode | The firmware blocks every Y command of its own (Y jog, Y homing, Go to XY₀, Frame) while the rotary is connected. To bring the bed nearer for a tool change use **Bed forward** on the Controls page, and **Bed back** before running the job. Bed back always returns to the machine Y where **Rotary mode** parked the bed, saved when Rotary mode finishes and kept across restarts (Bed forward never changes it). The button reads *Bed back* whenever the bed is more than 0.5 mm from that park, judged from the live position, and refuses to move until the machine has reported where the bed is (*Position Not Known Yet*, right after a launch). To change the park, run **Rotary mode** again. |
| CAM tab goes **blank** (status bar says *page out of memory, reloading*) | The CAM page's browser process died, usually out of memory while generating a job on a very large model (millions of vertices). The app reloads the page by itself and the tool restores its workspace. Simplify the model (a few hundred thousand vertices is plenty for milling) before generating again. On a Mac with many cores, **Animate** on a rotary job did the same until Kiri:Moto patch `0072-rotary-anim-grid-index-typed.patch`; re-vendor Kiri:Moto if it still happens. |
| **End of Travel** when jogging (e.g. Z up) | The axis is already at the end of its travel, so the machine refused the move — normal, not a fault. After homing, Z sits at the top: Z cannot go higher. Move the axis the other way. |
| **Job does not fit** when pressing Start, or **Job Outside Travel** mid-job | From the current work zero, part of the program (often the clearance/retract height) lies outside the machine's travel. The box names the axis and distance. Set that zero elsewhere (Z: lower), or lower the clearance height in the CAM and export again. |
| **Rotary** Animate shows the bar beside the part, or the toolpath sits next to the model after a scale, rotate or Arrange | The part had been moved off the rotary axis (Kiri's Arrange packer, a drag in the viewport or a restored workspace can do that), while the stock, toolpath and Animate are built on the axis. With Kiri:Moto patch `0059-rotary-part-on-axis.patch` an indexed part is put back on the axis whenever its bounds are recomputed. Until then: Rotary Mode off and on again recentres it. |
| **Live Preview** says *Bar size unknown* for a rotary job (before this check it drew it as a small tangle round the axis, nothing like *Animate*) | The job's Z 0 is on top of the bar and the copy shown was fetched from the machine (*generate preview*), which drops the `;` header that gives the bar's size; read as radii from the axis it would be drawn a whole bar radius too small, so Live Preview shows the message instead of a wrong picture. Export the job again and send it to the machine: exports now also carry the `(rotary: ...)` line the machine keeps. A job sent from this computer is shown from its own copy and is not affected. |
| **Rotary** job starts cutting far below the Z zero (one stock diameter too deep), or every tool is reported "too short" on export | The G-code was exported before Kiri:Moto patch `0053-rotary-z-zero-on-axis.patch`: its Z 0 sat a whole stock diameter above the rotary axis. Re-vendor Kiri:Moto, export again, and set Z0 where the origin picker's *z zero* row says (rotary axis or stock top) — see *Rotary Z zero* under [CAM](#cam). |
| **A** reads 0 right after the app starts, though the chuck was not re-zeroed | On purpose: at the first Idle report after each launch the app sets A to 0 at wherever the chuck stands (the A zero button's `setJobA`; Console: `[A] A read … at launch: set to 0`), because A otherwise came up hundreds of degrees off after a relaunch. Not while a job is running or paused, and not again on a reconnect in the same session (`connection.py`). If you relaunch between two operations on the same part, turn the chuck back to your reference mark and press A zero before the next one. |
| Connection drops right after uploading a **large file** (9 MB+) | Once the last chunk arrives, the machine copies and hashes the whole file before it answers, and reads nothing meanwhile. The app now shows "Upload finished, processing…" and keeps the link quiet until that answer comes (up to 5 min, `TRANSFER_FINALIZE_TIMEOUT_S` in `connection.py`). Over **WiFi** the firmware's own 5 s ping watchdog can still close the link when the copy ends; that needs a firmware fix (refresh the ping watchdogs after `finalizeTransferredFile`). |
| **Rotary** features cut at the wrong angle on the next file (A0 drifts) | Some rotary posts (Fusion "rotary post v2.4") end with `G10 L20 P1 A0` while the chuck is part-way through a turn, which moves A0. On upload the app sends a corrected copy that keeps the remainder (e.g. `G10 L20 P1 A16.52`) and says so; the file on disk is unchanged (`ui/rotary_fix.py`). Files uploaded before this fix must be re-uploaded, and A0 set again once at the reference mark. |
| **Files** list empty or missing files, a new upload never appears (count shows e.g. **Files (48+)**) | The machine holds more files than fit in its 8 KB file-list reply (about 47), so the reply is cut off mid-entry; the Console shows `[Files] The machine's file list is cut off …`. The files are still on the SD card. The app lists every file that came through, but the ones after the cut (usually the newest) cannot be shown until the list is shorter: delete old files from the machine. Before this was handled the cut-off reply was dropped whole and the list stayed empty. Lifting the limit needs a firmware change (a bigger reply buffer, or a paged `fileList`). |
| Machine traffic questions | **Console** page → save logs. |
| **Surface** greyed out in the `.ply` viewer, or reconstruction fails | Open3D is missing or unusable for the Python running the app. It publishes no wheel for very new Pythons (none for 3.14 yet) — run the app from a 3.11–3.12 virtualenv with `pip install -r requirements.txt`. Keep Open3D below 0.20: 0.20.0 aborts in native code on real scans. On Apple Silicon, Open3D also needs `libusb` from the **arm64** Homebrew (`arch -arm64 /opt/homebrew/bin/brew install libusb`). Poisson can fail at high octree depths on some scans; the viewer then retries one or two depths coarser and says so under the result ("built at octree depth 8 — depth 9 failed"). |
