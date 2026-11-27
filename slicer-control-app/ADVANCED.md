# Advanced CAM (branch `advanced-cam`)

Features developed on the side, until each one is safe to ship. `main` is the
shared version; nothing here reaches it until a feature's checklist below is
done and it is moved over on purpose.

This file lives only on this branch. Keep it in its own commit, so moving a
feature to `main` never carries it along.

## Where things are

| Folder (on the Desktop) | Branch | What it is |
|---|---|---|
| `piccola-cnc-desktop-cam-app-main` | `main` | Shared version, Daniele works here |
| `piccola-cnc-advanced-cam` | `advanced-cam` | `main` + the features below |

Both folders are the same repository (a git worktree), so a commit in one is
visible from the other. Each folder has its own Kiri:Moto build in
`vendor/kirimoto` (git ignores it); `venv` here is a link to the main folder's.

Only one copy of the app runs at a time. Both copies share one saved CAM
workspace: a job that uses an advanced operation will not open cleanly in the
`main` app, so keep such jobs for this one.

## Features

| Feature | Commit | Status |
|---|---|---|
| Material guide | `Add material guide for beginners in CAM` | in progress |
| Rotary (4-axis) operations | `Add rotary rough and surface rough for 4-axis roughing`, `render preview changes` (contour, spiral, Animate, shoulder fixes), then the rotary Z-zero commit | in progress |

### Material guide

*Materials* menu in the CAM tab (next to *Analysis*): cutter, spindle, feed,
plunge, depth and step-over per material, for woods & MDF, plastics, soft
metals and foam & wax. **Apply to selected operations** works the values out
for each operation's own cutter and, where the material needs it (e.g.
single-flute for acrylic), switches to a better tool from the library.

Files:
- `ui/cam_materials.py` — everything: the material table, the panel, Apply
- `ui/pages/cam.py` — two lines: the import and the `("materials", ...)` entry
- `README.md` — the *Materials* paragraph in the CAM section

Before moving to `main`:
- [ ] Every material's numbers checked against the machine limits
      (12,000 RPM, 1,500 mm/min) and against a real cut in that material
- [ ] Apply on a job with every operation type; values land in the right fields
- [ ] Acrylic switches to a single-flute cutter; materials without a special
      cutter leave the tool alone
- [ ] Apply on a job with no tools in the library that fit: says so, changes nothing
- [ ] Panel readable in light and dark mode, Windows and macOS

### Rotary (4-axis) operations

Fusion's 4-axis strategies, as far as a 4-axis machine with a vertical tool
can do them (its Swarf / Multi-Axis Contour / Multi-Axis Finishing need a
tilting head). A *Rotary (4-axis)* group in the add-operation menu, shown
only in Rotary Mode, each entry with a hover diagram:

| Menu entry | Kiri op type | Like Fusion's |
|---|---|---|
| Rotary Rough | `rotaryrough` | Rotary Offset Roughing |
| Surface Rough | `surfacerough` | Multi-Axis Roughing |
| Rotary Finish (Kiri's Lathe) + **spiral** | `lathe` | Rotary Parallel (Line / Circular / Spiral) |
| Rotary Contour | `rotarycontour` | Rotary Contour |
| Cut Off | `rotarycutoff` | (parting groove at each end, down to a core) |

Files:
- `tools/patches/kirimoto/0045-rotary-roughing.patch` — Rotary Rough and
  Surface Rough (new `work/rotary-rough.js`, hooks in Kiri's op list, pop-ups,
  menu and defaults)
- `tools/patches/kirimoto/0046-rotary-contour.patch` — Rotary Contour, on the
  same engine
- `tools/patches/kirimoto/0047-lathe-spiral.patch` — the lathe's spiral style
- `tools/patches/kirimoto/0048-rotary-animate-round-stock.patch` — Animate
  carves a round bar with Cylindrical stock (a bug on `main` too: worth moving
  over on its own)
- `tools/patches/kirimoto/0049-rotary-progress-names.patch` — the progress box
  shows "rotary rough", not "rotaryrough"
- `tools/patches/kirimoto/0050-rotary-animate-cut-colours.patch` — Animate
  colours the stock: grey untouched, blue roughed, green finished
- `tools/patches/kirimoto/0051-lathe-no-corner-cutting.patch` — Rotary
  Finish (Kiri's lathe) no longer cuts into shoulders between samples. Kiri's
  own lathe on `main` has this bug too (up to 3.7 mm on a 4 mm shoulder):
  worth moving over on its own
- `tools/patches/kirimoto/0052-surface-rough-no-wall-corners.patch` — Surface
  Rough no longer cuts the corner of a groove wall or shoulder
- `tools/patches/kirimoto/0053-rotary-z-zero-on-axis.patch` — rotary G-code's
  Z 0 is the rotary axis (every Z a radius) or, with the new process setting
  `camRotaryZTop`, the top of the bar; Animate's Z readout agrees. The
  choice is read at export time (`run/worker.js` carries it from the live
  settings onto the prepared print), so switching it after Generate works
  without regenerating.
  Upstream shifted indexed output by the full stock height, which put Z 0 a
  whole stock diameter above the axis: a job zeroed on the axis (tailstock
  centre) cut one diameter too deep, and the tool-length check called every
  tool too short. Found 2026-11-03 on the GT2 pulley job. Kiri's own lathe on
  `main` has the same bug: worth moving over on its own, with the header
  changes below
- `tools/patches/kirimoto/0054-lathe-step-down-levels.patch` — Rotary Finish
  with a step down cuts levels from the outside in, each the profile moved
  out by k × step and never inside it (a negative *expand* used to clamp
  every pass to "max radius + expand", 1.2 mm into the pulley's teeth at
  expand −2), and spiral works on every level. Found 2026-11-03
- `tools/patches/kirimoto/0055-rotary-retract-round-stock.patch` — the rotary
  retract is the bar's radius plus the clearance for cylindrical stock (was
  a square blank's corner radius, 5.5 mm higher on a 26 mm bar). Found
  2026-11-03 when a 38 mm taper tool left 2.2 mm of headroom above the bar
- `tools/patches/kirimoto/0056-rotary-unwind-a-at-end.patch` — a rotary job
  ends with `G10 L20 P1 A<remainder>` so A reads as a plain angle afterwards
  (the pulley finish ended at A −27720); Live Preview skips G10/G92 lines
- `tools/patches/kirimoto/0057-rotary-cut-off.patch` — the Cut Off operation
  (new `work/op-cutoff.js`: a helical parting groove outside each end of
  the part, a step down per turn, to a core diameter; popup, defaults, menu
  entry, progress name, Animate colour). Asked for 2026-11-03
- `tools/patches/kirimoto/0058-rotary-feed-not-plunge.patch` — a rotary move
  is capped at the plunge rate only when it is mostly Z; a helix or a
  sloping pass that turns the stock runs at the feed rate (every descending
  move used to get the process Max Plunge Rate: a 1200 finish ran at 200
  wherever the profile fell, the cut-off's whole groove at 200). Found
  2026-11-03 on the first cut-off run
- `tools/patches/kirimoto/0059-rotary-part-on-axis.patch` — an indexed part
  is kept on the rotary axis (x = y = 0) in `platform.update_bounds` and
  after a layout; Kiri's Arrange packer, a drag or a restored workspace
  could leave it at e.g. (19, 12.6) while stock, toolpath and Animate were
  drawn on the axis. Found 2026-11-04 on a scaled roller
- `ui/cam_gcode_header.py`, `ui/job_summary.py`, `ui/pages/live_preview.py` —
  the header says `; rotary: Z 0 on the rotary axis … stock radius R` (or
  `on top of the bar`), the tool-length depth is measured from the bar's
  surface, and Live Preview shows *center · rotary axis* / *top of the bar*
  and the depth into the bar
- `ui/pages/live_preview.py` — a rotary file zeroed on the bar top is drawn
  at radius Z + stock radius (read from the header), not at Z
- `ui/cam_stock_origin.py` — in Rotary Mode the picker's *z zero* row is
  *rotary axis* / *stock top* (`camRotaryZTop`) instead of the flat heights
- `ui/cam_theme.py` — capitalises the progress box's text
- `ui/cam_op_menu.py` — the *Rotary (4-axis)* group, Lathe shown as Rotary
  Finish, the group header hidden on flat jobs
- `ui/cam_op_art.py` — the five diagrams (our own drawings)
- `ui/cam_op_names.py` — names `rotary-rough_N`, `surface-rough_N`,
  `rotary-finish_N`, `rotary-contour_N`
- `ui/cam_no_ops.py` — counts them as rotary-only operations
- `README.md` — the *Rotary (4-axis) operations* paragraph, one phrase in
  *Slicing errors*

All fifteen patches need Kiri rebuilt, see below.

G-code verified 2026-10-31 with an independent checker (the real tool shape --
flute, ball, shank -- against the part mesh at every exported point and every
0.3 mm between), on a bat handle and on a stepped shaft with a groove and a
flat: flat end mills 2, 3 and 6 mm with Rotary Rough, Surface Rough and
Rotary Contour; a 3 mm ball (R1.5) with Rotary Finish line / circle / spiral,
Rotary Contour and Surface Rough. 32 programs: no cut into the part beyond
0.05 mm (largest 0.043, the checker's own sampling), roughing never closer
than its leave stock, no rapid into stock, no bad numbers or Y words, every
large A turn above the stock. That is the G-code; it is not yet a cut.

Before moving to `main`:
- [x] G-code checked against the part with 2, 3, 6 mm flat and 3 mm ball (2026-10-31)
- [ ] Cut in foam or wax: Rotary Rough and Surface Rough on a round part
- [ ] Same on square stock (only the corners cut on the first layers)
- [ ] Rotary Contour on a part with a rib or a shoulder, after roughing:
      walls clean, about 0.1 mm left at the default step angle
- [ ] Rotary Finish with spiral: no step marks; ends cut clean
- [ ] Z zero on the rotary axis (tailstock centre point) and A zero set the
      usual way give the right depth on the machine. G-code side done
      2026-11-03 (patch 0053: Z = radius, checked on the pulley job); the cut
      itself still to try
- [ ] Long job: A winds many turns (A in the tens of thousands of degrees);
      the machine and the A-zero reset at the end handle it
- [ ] Stop and resume in the middle of a job
- [ ] Animate matches what the machine cuts
- [ ] Menu on a flat job: no Rotary group; in Rotary Mode: all six entries
- [ ] Animate with Cylindrical stock: a round bar, carved as the machine cuts
- [ ] Animate colours: grey stock, blue after roughing, green after finishing
- [ ] Progress box while generating: "Rotary Rough" etc., capitalised

## Day to day

Work and commit here as usual. To bring in what Daniele has pushed:

```
# main folder
git pull

# advanced-cam folder
git merge main
```

If the merge brought in a Kiri patch, or you changed one here, rebuild this
folder's Kiri. Quit the app first, and close any Finder window inside
`vendor/kirimoto` (Finder rewrites `.DS_Store` and the delete fails):

```
rm -rf vendor/kirimoto
arch -arm64 python3 tools/fetch_vendor.py kirimoto
python3 tools/verify_patches.py        # must end "all checked patches are live"
```

Run this version with `arch -arm64 ./venv/bin/python main.py` from this folder.

**Kiri patch numbers.** Patches apply in filename order. If `main` gains a
patch with the same number as one here (e.g. its own `0045-...`), rename ours
to the next free number after `main`'s last one when merging, then rebuild.

## Moving a feature to `main`

When a feature's checklist is done:

```
# advanced-cam folder: find the feature's commit(s)
git log --oneline main..advanced-cam

# main folder
git cherry-pick <commit>          # one per commit of that feature
```

Then in the main folder: if the feature has a Kiri patch, rebuild Kiri (steps
above), run `verify_patches.py`, start the app and check the feature once
more. Mark it *moved to main* in the table here.

Later commits that fix the same feature should stay one feature per commit,
with the feature's name in the subject, so this list stays easy to pick from.
