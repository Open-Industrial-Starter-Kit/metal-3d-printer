# CAD - OISK Metal 3D Printer

The machine CAD is published in two formats:

- `Fusion 360/` - native Autodesk Fusion archive (`.f3d`)
- `Step/` - neutral STEP (ISO 10303-21) export, readable by any CAD system

Both files are large, so each one is compressed into a ZIP archive and then split
into parts of max. 24.9 MB, in order to stay inside the GitHub file size limits.

| File | Split archive | Uncompressed |
| --- | --- | --- |
| `OISK Metal 3D Printer.f3d` | `Fusion 360/OISK Metal 3D Printer.f3d.zip.001` - `.002` | ~42 MB |
| `OISK Metal 3D Printer.step` | `Step/OISK Metal 3D Printer.step.zip.001` - `.002` | ~205 MB |

## How to reassemble

Download **all** the parts of the file you need into the same folder, join them back
into a single ZIP and then extract it.

### macOS / Linux

```bash
cat "OISK Metal 3D Printer.step.zip."* > "OISK Metal 3D Printer.step.zip"
unzip "OISK Metal 3D Printer.step.zip"
```

```bash
cat "OISK Metal 3D Printer.f3d.zip."* > "OISK Metal 3D Printer.f3d.zip"
unzip "OISK Metal 3D Printer.f3d.zip"
```

### Windows

In the command prompt (`cmd`), inside the folder with the parts:

```bat
copy /b "OISK Metal 3D Printer.step.zip.001" + "OISK Metal 3D Printer.step.zip.002" "OISK Metal 3D Printer.step.zip"
```

Then extract the resulting ZIP with Windows Explorer or 7-Zip.

Alternatively, with 7-Zip installed, right click on the `.001` part and choose
*7-Zip > Extract Here* - 7-Zip joins the parts automatically.

## License

The CAD files are released under the CERN Open Hardware Licence Version 2 -
Strongly Reciprocal (**CERN-OHL-S**), see
[LICENSE_CERN_OHL_S_V2.txt](../LICENSE_CERN_OHL_S_V2.txt).
