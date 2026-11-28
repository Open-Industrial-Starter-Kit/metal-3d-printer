# `slicer/` — the LPBF slicer

This is the OLOS metal-printing slicer, vendored from
[OLOS-metal-3d-printer](https://github.com/Open-Industrial-Starter-Kit/OLOS-metal-3d-printer)
and adapted to this machine. GPL-3.0; the upstream licence is kept at
`olos/OLOS-LICENSE.txt`, and this repository already carries
`LICENSES/GPL-3.0-or-later.txt`.

## Layout

| Path | What it is |
| --- | --- |
| `olos/prusa_slicer.py` | The engine, 6378 lines, vendored essentially unchanged. Masks → scan paths → G-code. |
| `olos/slicers_shared_methods.py` | 831 lines: per-zone exposure, the recoat routine, the init block, bounds clamping. |
| `olos/shim.py` | The four OLOS server pieces the engine imports, and nothing else. |
| `olos/binary.py` | Finds the PrusaSlicer CLI the engine shells out to. |
| `params.py` | The slicing-parameter contract: all 230 keys the engine reads, and their defaults. |
| `job.py` | Running a slice: measures the model, assembles the parameters, checks the output. |
| `schema.py` | The same parameters as a table the interface builds itself from: labels, groups, units, ranges, choices, help. |
| `retarget.py` | Translates the engine's output into this machine's `M610`/`M611`/`M600` block dialect. |
| `test_retarget.py` | Regression tests for that translation. `python3 -m slicer.test_retarget` |
| `test_slice.py` | End-to-end: model in, routed blocks out. `python3 -m slicer.test_slice` |
| `test_schema.py` | That the table, the defaults and the engine agree. `python3 -m slicer.test_schema` |

## What had to change, and why

**The engine is standalone; its server coupling was five constants.** Upstream
it lives inside a FastAPI application, importing `core.constants`,
`utils.tmp_files_manager`, `utils.image_to_gcode_helper` and
`utils.websocket_json_data`. Measured rather than assumed, the engine touches
five constants from the first, one method from the third, and one message
builder from the fourth. `olos/shim.py` supplies exactly those, so the engine
runs in a PySide6 desktop app with no server, no websocket and no event loop.
The two engine files are otherwise byte-identical to upstream apart from their
import lines, which is what keeps them updatable.

**`autotrace` was a dead dependency.** It is imported at module scope upstream
and used in exactly one method, `_generate_svg_contour`, which nothing calls —
contours come from shapely and `cv2.findContours` instead. The import is now
lazy, inside that method, so a native-library dependency is not mandatory for
an unreachable path. `opencv-python-headless` is used rather than
`opencv-python`: the latter ships its own Qt plugins, which clash with PySide6.

**PrusaSlicer is not vendored.** Upstream bundles
`slicers/PrusaSlicer/prusa-slicer-console.exe` and hardcodes that path. It is
Windows-only — there is no macOS or Linux build under `slicers/` — so on this
host it cannot resolve at all. `olos/binary.py` searches `$OISK_PRUSA_SLICER`,
then the platform's standard install location, then `PATH`, and raises with an
install hint instead of failing inside `subprocess.run`.

## The machine-level problem: OLOS has no galvo

This is the part that is not a port but a translation.

**OLOS scans with the gantry.** The word "galvo" appears once in its
repository, in a photo caption, and nowhere in its code. Its output is a single
stream of single-coordinate-system GRBL: `G53` to a bed reference, `G92` to
zero there, then `G0`/`G1` with `M3`/`M5` gating. A scan path *is* an XY move,
because the laser is carried by the gantry.

**This machine has two XY systems** — the gantry on CPU-A and the mirrors on
CPU-C — and a job file says which one it is addressing with `M610`/`M611`/`M600`
(firmware/CPU-B/README.md §5.13.2.1). Fed OLOS's output directly, CPU-B would
route every scan path to the gantry and try to drag the optical head along the
toolpath at the marking feedrate.

`retarget.py` does the split, and it reads from the axis letters rather than
guessing at intent, because the two systems use disjoint ones:

- `G53` is machine-coordinate positioning — bed reference, parking, recoater
  throw. Stage.
- `Z` build piston, `A` powder reservoir, `B` blade recoater. Stage. **OLOS uses
  the same three letters for the same three things as this machine**, which is
  why its recoat routine transfers unchanged — the one piece of luck here.
- `G0`/`G1` with only `X`/`Y`/`F` inside the gated section. Galvo.
- `M3`/`M5` travel with the scan.

Two lines needed care, and both would have failed silently rather than loudly:

- **`G92` cannot be dropped wholesale.** `G92 X0 Y0` sets the gantry work
  origin and is redundant once coordinates are mapped into the field. But
  `G92 Z0` and `G92 A0` reset the piston and reservoir origins to where they
  physically are, and the routine then issues `G1 Z<layer_height>` as a *single
  step up from there*. Drop them and every layer commands the same absolute
  0.075 mm, so the bed never descends past layer one.
- **A bare `G1 F3000` is a modal feedrate belonging to its run.** In the recoat
  routine those set the parking and recoater-throw speeds. Drop them and the
  recoater throws at the powder reservoir's `F50` — sixty times too slow.

**Coordinates are offset, never scaled.** OLOS scan paths run in bed
coordinates on a 96 mm circular bed. The galvo field here is 250 × 250 mm
(`X_MAX_POS_MM`, firmware/CPU-C/galvo_test_with_relays.cpp), so the bed fits
inside a single field and **no stage tiling is required** — the gantry does not
move during a scan. The bed is centred: bed 0..96 → field 77..173.

## Not done yet

- **No Slicer-tab UI.** The engine, the retargeting and the settings table are
  all callable, but `ui/pages/cam.py` is still the placeholder. Nothing in the
  app drives this yet.
- ~~**Never run end to end.**~~ It runs. A 20×20×5 mm box slices in 0.3 s;
  two 15 mm cubes at 0.05 mm give 55 layers and 32,755 moves in 1.1 s, about
  20 ms per layer. `test_slice.py` covers it and skips without PrusaSlicer.
- **The field map is nominal.** `retarget.py` centres the bed in a 250 mm field
  on the same nominal linear mm scale CPU-C uses. There is no distortion table,
  and the field has not been calibrated against the bed.
- **Bed geometry is OLOS's.** `DEFAULT_BED_MM = 96` and the machine defaults in
  `slicers_shared_methods.py` (parking X −395.5, recoater −405, bed reference
  −238.5/−158) are upstream's numbers. They match this machine's envelope
  closely enough to be plausible, and have not been checked against it.

## What running it for the first time changed

Nothing here was found by reading the code. Each of these took a real slice.

**PrusaSlicer's command line was wrong off Windows.** `--sw-renderer` does not
exist in 2.9.7 — it answers `Unknown option`, prints its usage and exits 1.
`--single-instance` is the dangerous one: its own help says the arguments are
handed to a running GUI instance, so with PrusaSlicer open the request goes to
that window, the process returns 0, and `--output` is never written. Both are
gone. `olos/binary.py` keeps a per-platform flag hook, deliberately empty.

**Its errors were being swallowed.** The engine ran with `check=True` and
discarded stderr, so a refused model surfaced as a `CalledProcessError` printing
a twenty-argument command line. The cause of the one that cost most time —
`Loading of a model file failed`, returned for an STL whose facet normals are
all zero — was on the discarded stream. PrusaSlicer accepts such a file for some
geometries and not others: a 20×20×5 box loads, a 15×15×3 box does not. Operators
do arrive with files like that, so the message is now carried through.

**The SL1 archive is verified.** PrusaSlicer is a GUI binary with a CLI mode and
has several ways to exit 0 having written nothing. The archive is checked for
existence, size and zip-ness before the engine opens it.

**The gantry's parking move was going to the mirrors.** Every layer ends with
`G1 X0 Y0 F10000` — the gantry parking before the recoat — and the retargeter
read the X/Y and mapped it into the field as `G1 X77 Y77 F10000`. So every layer
the mirrors swept to the corner of the field for no reason, and the gantry never
parked before the recoater threw. The classifier now takes the laser gate, and
uses the same definition of a parking move the engine itself uses: within
0.0001 mm of the work origin.

**`layerHeight` defaults to 1 mm upstream.** That is kept, because this module's
job is to reproduce OLOS faithfully, but it is not a usable LPBF layer — powder
layers are 0.02–0.1 mm and the proven files on this machine step 0.075 mm.
`params.LAYER_HEIGHT_MM_RANGE` is there so the UI does not present the inherited
default as a suggestion.

## Two things the engine does that look like bugs and are not

**`M9`/`M2` are never emitted.** `slice_model` forces `visicut_style = True`, and
the end-of-job write is gated on that being false. The program still ends safely
— `M5`, then a return to the work origin — it just carries no program-end code.

**Three post-processors are unreachable.** `visicut_style` also takes the early
return in `generate_gcode_for_layer`, so `hard_gate_laser_motion` and
`enforce_m5_guarded_long_travel` never run on this path. What that branch *does*
guarantee is worth knowing, because the routing depends on it: exactly one
`M3` … `M5` per layer, infill and contour inside the same pairing, and the park
after the `M5`.

## The settings table

`schema.py` carries all 220 parameters as rows: label, group, type, unit, range,
choices and help text. The interface is built by walking it, so the gantry and
galvo panels are the same widget instantiated twice over the same rows — which
is the only way 220 settings get an interface without being typed out twice and
drifting apart.

It is **extracted from OLOS's own interface**, not written: labels from the
captions and `label=` attributes in `PrusaSettings.vue`, groups from the
`q-expansion-item` each control sits in, units and ranges from the `suffix`,
`min`, `max` and `step` attributes, choices from the `q-btn-toggle` option
arrays (which carry both the engine's value and the text to show), and help from
`PRINTING_SETTINGS_ASSISTANT_MESSAGES`. Hand-authoring 220 labels would mean
inventing names for machine behaviour someone else already named, and getting
some of them quietly wrong.

Getting the extraction right took three passes, and each wrong pass was wrong in
a way that would have shipped:

- **Attributes were read from a line window** around each `v-model`, which
  picked up neighbouring controls'. `bed_offset_x` came out labelled `mm/min`
  and `grid_clearance` as `deg`. Attributes are now read from the control's own
  tag only.
- **The group stack never popped** on `</q-expansion-item>`, so controls sitting
  between sections — `solid_mode_enabled` and the two engraving settings are at
  the template root — were filed under whichever section closed last.
- **Tooltips were taken by proximity**, which steals the previous control's
  help. A wrong tooltip is worse than none, because it confidently describes a
  different setting. They are now paired with the caption they belong to, plus
  an exact name match against the constants table (`INFILL_POWER` →
  `infill_power`). That is 36 of 196, correctly attributed, rather than 50-odd
  with some lying.

Three groups of keys needed more than extraction:

- **14 are ours.** The bed, parking position, powder reservoir, recoater and
  raster resolution. OLOS hardcodes these in its handler and never shows them,
  which is reasonable for one fixed machine and useless here — they are exactly
  the numbers that differ between its printer and this one.
- **32 are exposed nowhere.** `surface_skin_*`, `internal_floor_support_*` and a
  few derived values are read by the engine, defined in `constants.ts`, and
  absent from the form. They sit under *Advanced*, marked as defaults that are
  the only values they have ever run with.
- **3 are hidden on purpose.** `is_hollowing_enabled`, `is_support_enabled` and
  `is_pad_enabled` are inside `v-if="false"` sections upstream, so they are
  always off. That is right for powder-bed fusion — loose powder supports the
  part — and the detail settings behind those flags never reach the engine.
  They are kept in the table with `hidden=True` so the reason is recorded.

`test_schema.py` asserts the table, `params.py` and the engine agree: same keys,
same defaults, every choice offering its own default, no control for a key the
engine ignores, nothing editable unreachable through the groups, and a built
`slicing_data` satisfying every lookup the engine makes. Two generated tables
describing the same thing is exactly where drift goes unnoticed.
