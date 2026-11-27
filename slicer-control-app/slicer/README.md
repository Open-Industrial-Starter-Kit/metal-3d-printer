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
| `retarget.py` | Translates the engine's output into this machine's `M610`/`M611`/`M600` block dialect. |
| `test_retarget.py` | Regression tests for that translation. `python3 -m slicer.test_retarget` |

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

- **No Slicer-tab UI.** `retarget.py` and the engine are callable, but
  `ui/pages/cam.py` is still the placeholder. Nothing in the app drives this.
- **Never run end to end.** PrusaSlicer is not installed on the development
  machine, so the engine has been imported and its G-code generators exercised,
  but no model has been sliced. The retargeting is tested against output from
  the real generators; the mask→path stage is not.
- **The field map is nominal.** `retarget.py` centres the bed in a 250 mm field
  on the same nominal linear mm scale CPU-C uses. There is no distortion table,
  and the field has not been calibrated against the bed.
- **Bed geometry is OLOS's.** `DEFAULT_BED_MM = 96` and the machine defaults in
  `slicers_shared_methods.py` (parking X −395.5, recoater −405, bed reference
  −238.5/−158) are upstream's numbers. They match this machine's envelope
  closely enough to be plausible, and have not been checked against it.
