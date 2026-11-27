# OISK Metal 3D Printer — Slicer and Control App

Cross-platform (macOS / Windows / Linux) desktop app for the OISK Metal 3D Printer,
built with **PySide6 (Qt)**. It talks to **CPU-B** over USB serial using the JSON
protocol documented in [`../firmware/CPU-B/README.md`](../firmware/CPU-B/README.md),
and reaches **CPU-C** (galvo and laser) through CPU-B's `"target":"cpuc"`
passthrough — see [`../firmware/CPU-C/README.md`](../firmware/CPU-C/README.md).

> **Where this came from.** This started on 2026-11-27 as a copy of the Piccola CNC
> desktop CAM app, which controls a 3-axis milling machine over the same shape of JSON
> protocol. The machine-control half transferred; the milling half did not. What has
> been removed since, and what is still inherited and still wrong for this machine, is
> at the bottom of this file. The original 56 KB Piccola README is in git history at the
> commit that added this directory.

## Pages

| Page | What it does | Code |
|---|---|---|
| **Files** | Jobs on CPU-B's SD card: search, sort, upload, download, rename, delete. Uploads go out as `startFileTransfer` + `fcl` chunks. | `ui/pages/file_manager.py` |
| **Controls** | Jog pad (XY + Z), step size and speed, homing, unlock, soft reset, work-zero buttons, Frame. | `ui/pages/controls.py` |
| **Slicer** | Empty placeholder. This is where layer generation will go. | `ui/pages/cam.py` |
| **Live Preview** | 3-D view of the opened job's toolpath with live position; already-cut path in orange. | `ui/pages/live_preview.py` |
| **Console** | Raw TX/RX with the machine; send JSON or G-code by hand; save the log. | `ui/pages/console.py` |
| **App menu** | Machine name, hardware settings, machine info and job history, firmware debug toggles, graphics mode. | `ui/app_menu.py` |

## Connection

**USB serial only.** The app probes serial ports with `{"type":"serialPing"}` at
2,000,000 baud and connects to whichever answers. There is no WiFi, no WebSocket server
and no BLE discovery: CPU-B has no WiFi, so all of that was removed.

A **developer offline mode** (*Develop offline* on the search screen) simulates a
connected idle machine so the UI can be exercised with no hardware. It is the only other
way a "link" is reported up — see `is_offline_link` / `link_changed` in `connection.py`.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate    # (Windows: venv\Scripts\activate)
pip install -r requirements.txt
python main.py
```

Open3D is optional and only powers surface reconstruction in the `.ply` viewer; the app
runs without it.

## Layout

`main.py` boots the Qt app; `main_window.py` assembles the shell (top bar, side menu,
page stack, right panel, bottom bar). Pages live in `ui/pages/`. Every panel is wired to
the central `Connection` hub (`connection.py`), which owns the `SerialWorker`.

## Packaging

`piccola-cnc.spec` builds a **one-folder** bundle, not a single `.exe` — QtWebEngine's
helper process and resource files do not survive one-file packing. Ship the whole folder.
See `build_windows.bat`, `build_unix.sh`, `build_linux.sh`.

## What was removed, and what is still inherited

Each removal was its own commit, so `git log -- slicer-control-app` shows what went and
why, and any one can be read back in isolation.

- **The CAM subsystem.** Kiri:Moto and EasyTrace5000 generate *milling* toolpaths, so
  none of that work transfers to laser scan paths. `ui/pages/cam.py` is now an empty
  placeholder that keeps the page's interface (`gcode_exported`, `wire`, `export_root`,
  `shutdown`), so the export hand-off and upload queue in `main_window.py` stay wired for
  a real slicer to emit into.
- **The laser distance sensor**, which this machine does not have — with the flows that
  existed only to consume it: edge finding, centre detection, height map, 3D scanning,
  and the laser-zero move.
- **Spindle control**, which this machine does not have — including the pre-start check
  that stopped the spindle before a job.
- **WiFi**, in full.

**Still inherited and still wrong for this machine:** Z0 and tool-length probing, Facing,
and Rotary Mode. Facing still emits `M3`/`S`/`M5` into the G-code it generates, which is
why the spindle removal left it alone — half-editing it would produce wrong G-code rather
than no G-code. These are to port or remove whole, not to half-edit.

**Not yet connected to this machine at all:** nothing here knows about layers, the galvo,
the recoater or CPU-C. Driving a build means using CPU-B's `sendFileToCPUC` and CPU-C's
layer API, neither of which this app calls yet.
