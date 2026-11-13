# Settings - OISK Metal 3D Printer

- **[OISK_Metal_3D_Printer_Settings.xlsx](OISK_Metal_3D_Printer_Settings.xlsx)**

The workbook collects the machine and process settings of the OISK Metal 3D Printer as
recorded during the 316L process-development campaign (Tests 1-22):

| Sheet | Contents |
| --- | --- |
| Laser Source | Laser module specification |
| Feedstock 316L | Powder certificates for both batches used |
| Machine Hardware | Mechanical, control, gas and containment hardware |
| Axes and Travel | Axis assignment, resolution, rates and travel |
| Controller grblHAL | Full controller configuration as exported with `$$` |
| Validated Recipe T19 | Zone-by-zone recipe of the validated full part |
| OLOS Slicer Settings | Complete OLOS slicer settings record for that build |
| Process Window | Operating regimes identified across the campaign |

Power values are the percentage `S` of the 60 W laser mode and speeds are feed rates in
mm/min, as in the emitted G-code. Energy densities carry a ±15% uncertainty from the laser
module.

> These are prototype research settings developed on one machine, not a qualified production
> process. They are published so that builds can be reproduced and improved on.

Source: D. Tiwari, *Process Parameter Development for Laser Powder Bed Fusion of 316L
Stainless Steel on the OISK Open-Source Platform*, Bachelor Thesis, Rhine-Waal University of
Applied Sciences, 21.10.2026.
