# Project notes for Claude — slicer-control-app

This directory is the OISK Metal 3D Printer's slicer and control app. It started as an
unmodified copy of the Piccola CNC desktop CAM app; see the top of `README.md` for what came
across, what was left out, and what therefore does not work.

- **Keep `README.md` current.** When a change adds, removes or renames a user-facing
  feature, or changes where the app writes files, logs or settings, update the
  matching README section ("Main functions", "Troubleshooting, errors and logs")
  in the same change.
- **The README still describes the Piccola CNC milling machine.** As parts are ported to this
  printer, replace the inherited text rather than adding alongside it, so the README never
  describes two machines at once. Shrink the provenance note as its warnings stop applying.
- **The firmware this app talks to lives in `../firmware`.** CPU-B's JSON API is documented in
  `../firmware/CPU-B/README.md` and CPU-C's in `../firmware/CPU-C/README.md`. Those are the
  authority on the protocol; the app's own notes about a "CPUB" describe the Piccola firmware.
