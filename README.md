<div align="center"><h1 style=align="center">OISK Metal 3D Printer</h1></div>

<div align="center"><img src="media/OISK_Metal_3D_Printer_1.jpg" width="60%"></div>
<div align="center"><i>Open source metal 3D printer (LPBF) - Open Industrial Starter Kit</i></div>

<br>

Introduction
--

The OISK Metal 3D Printer is an open source metal additive manufacturing machine based on **Laser Powder Bed Fusion (LPBF)**. It is designed as a *dual-configuration* platform: the same machine architecture can be operated with a low-cost multi-diode laser source or with a fiber laser source, so that the cost and the capability of the machine can be scaled to the application.

The machine is built around a Cartesian gantry carrying the optics over a circular build area, with a reserve cylinder and a printing cylinder driven by stepper-actuated pistons, a blade recoater for powder deposition, and a fully enclosed stainless steel cabinet running a nitrogen atmosphere. The current prototype runs a **60 W, 450 nm blue diode** source and has been validated with **316L stainless steel**.

The aim of the project is to make metal additive manufacturing accessible outside of the proprietary, closed and expensive machine market, by releasing a replicable machine with open CAD, open electronics, open documentation and open software.

<img src="media/OISK_Metal_3D_Printer_Overview.jpg" width="100%">

<div align="center"><i>Machine architecture: enclosure, gantry with galvo scanner and laser diode, reserve and printing cylinders</i></div>

Specifications
--

- process: Laser Powder Bed Fusion (LPBF)
- configuration: dual-configuration laser source (multi-diode / fiber)
- laser: 60 W, 450 nm blue diode module
- build area: circular build platform
- motion: Cartesian XY gantry, five axes - X and Y carry the laser head, Z drives the build piston, A the powder reservoir, B the blade recoater
- axis travel: X 400 mm, Y 200 mm, Z 92 mm, A 102 mm, B 425 mm
- powder handling: SFU1204 ball screws on Z and A, aluminium cylinders and pistons with PTFE inserts, blade recoater
- validated layer height: 0.075 mm
- build chamber: fully enclosed, nitrogen atmosphere, chamber O₂ typically below 0.5% after purge
- thermal architecture: passive - no build-plate heater, no chamber pre-heat, no active cooling
- housing: stainless steel cabinet with viewing window, aluminium profile frame
- controller: Metal 3D Printer Control Board V2 - 3 × RP2350B (motion, supervisor, galvo and laser), grblHAL motion control
- validated material: 316L (1.4404) stainless steel, gas atomised, 10-45 µm
- software: OLOS - Open Lab Operating System

### Laser source specifications

| Property | Value |
| --- | --- |
| Module | LASER TREE K60, 6-diode |
| Wavelength | 450 nm ± 10 nm (blue diode) |
| Rated optical power | 60 W+ (switchable 20 / 40 / 60 W; operated in 60 W mode) |
| Measured optical power | 69-71 W (5-unit sample, mean 70.4 W) |
| Output stability | 1.8-3.4% attenuation over 30 min continuous operation |
| Focal length / focus | 40 mm, fixed focus with adjustment lever |
| Spot size at 60 W | 0.15 × 0.25 mm (rectangular) |

<img src="media/OISK_Metal_3D_Printer_Gantry_CAD.jpg" width="49%"> <img src="media/OISK_Metal_3D_Printer_Cylinders_CAD.jpg" width="27%">

<div align="center"><i>Gantry with galvo scanner (left) and the reserve / printing cylinder assembly (right)</i></div>

<br>

<img src="media/OISK_Metal_3D_Printer_Gantry.jpg" width="49%"> <img src="media/OISK_Metal_3D_Printer_Build_Platforms.jpg" width="49%">

<div align="center"><i>Gantry prototype (left) and build platforms (right)</i></div>

<br>

<div align="center"><img src="media/OISK_Metal_3D_Printer_Cylinder_Assembly.jpg" width="40%"></div>

<div align="center"><i>Assembly of the printing cylinder with the piston and the lead screw</i></div>

The Machine in Operation
--

<img src="media/OISK_Metal_3D_Printer_Chamber_Gantry.jpg" width="49%"> <img src="media/OISK_Metal_3D_Printer_Build_Bed.jpg" width="49%">

<div align="center"><i>The process chamber with the XY gantry and the 60 W blue diode laser head (left) and the build bed with the reserve and printing cylinders, the 316L powder and the recoater blade (right)</i></div>

<br>

The machine is installed inside a dedicated containment tent that confines the fine 316L powder. The gas and air plant stands immediately outside it: a compressed-air supply feeding a membrane nitrogen generator, a nitrogen buffer tank and a vacuum pump. A free-standing air filter unit filters and recirculates the tent atmosphere.

<img src="media/OISK_Metal_3D_Printer_Containment_Tent.jpg" width="40%"> <img src="media/OISK_Metal_3D_Printer_Air_Filter.jpg" width="27%"> <img src="media/OISK_Metal_3D_Printer_Operator_Station.jpg" width="29%">

<div align="center"><i>Containment tent with nitrogen generator and buffer tank (left), air filter unit (centre), operator station inside the tent (right)</i></div>

Control Electronics
--

The machine is driven by the **Metal 3D Printer Control Board V2**, a single board that combines three subsystems usually sold as separate industrial boxes: a grblHAL motion controller, a fiber-laser interface and a galvo scanner interface. The work is split across **three RP2350B microcontrollers** - CPU-A for motion, CPU-B as supervisor and communications, CPU-C for the galvo and the fiber laser.

<img src="media/OISK_Metal_3D_Printer_Control_Board.jpg" width="43%"> <img src="media/OISK_Metal_3D_Printer_Control_Board_Detail.jpg" width="55%">

<div align="center"><i>Metal 3D Printer Control Board V2</i></div>

<br>

<div align="center"><img src="pcb/MetalPrinter_V2_IO_Annotated.png" width="100%"></div>

<div align="center"><i>Inputs, outputs and power connectors of the Control Board V2</i></div>

| Item | Value |
| --- | --- |
| Main supply | 24 V DC, 10 A, dual fused, with current monitoring |
| Controllers | 3 × RP2350B, 4 MB flash each, USB-C each; 8 MB PSRAM on CPU-C |
| Motion | 5 axes (X, Y, Z, A, B), buffered 5 V STEP/DIR/EN for external stepper drivers |
| End stops | 6 isolated channels (X, Y, Z, A, B, Door), NC/NO selectable |
| Laser #1 | 24 V diode channel - relay-switched power plus 5 V logic PWM |
| Laser #2 | Raycus RFL-C200A fiber laser over DB25 - analog power, modulation, enables, interlock relays, status inputs, RS-232 |
| Galvo | XY2-100 over DB15, with relay-switched ±15 V head supply |
| Networking | Ethernet, RS-422, RS-485, RS-232, I²C, 3 UART links |
| Sensors | Chamber oxygen (I²C), PM2.5/PM10 dust, NTC temperature, input current monitor |
| Storage and UI | microSD, RGB status LED, GPIO/ADC breakout headers |

The oxygen and dust sensor inputs are specific to metal printing: the chamber has to be kept inert and low in oxygen, and fine metal powder has to be monitored in and around the machine.

### Schematic

<img src="pcb/screenshots/Schematic_01_System_Overview.png" width="49%"> <img src="pcb/screenshots/Schematic_07_CPU_A_Motion.png" width="49%">

<div align="center"><i>System overview sheet with the three CPUs, power and motion blocks (left) and the CPU-A motion controller (right)</i></div>

<br>

<img src="pcb/screenshots/Schematic_05_Galvo_XY2_100.png" width="49%"> <img src="pcb/screenshots/Schematic_06_Fiber_Laser.png" width="49%">

<div align="center"><i>Galvo scanner interface, XY2-100 over DB15 (left) and the Raycus fiber laser control interface over DB25 (right)</i></div>

<br>

Schematic, user manual, hardware documentation, BOM, Altium sources and manufacturing data are in **[pcb](pcb)**.

Process Settings
--

A systematic process-parameter campaign of twenty-two builds was carried out on this machine to develop a working 316L process. The complete record - laser, feedstock, hardware, controller configuration, the validated slicer recipe and the process window - is published as a workbook:

- **[Machine and Process Settings](settings/OISK_Metal_3D_Printer_Settings.xlsx)**

### Validated recipe

The table below is the zone-by-zone configuration of the first geometrically complete 316L part produced on the machine - a closed hull, deck and cabin over 463 layers at 0.075 mm, built in approximately 6 hours. Power is commanded as the percentage **S** of the 60 W mode and feed **F** in mm/min; VED is the volumetric energy density P / (v · t · h).

| Zone | Layers | Power / feed | Emitted pitch | VED |
| --- | --- | --- | --- | --- |
| First (engraved) | 4 | S55 / F3000 | 0.200 mm | 44.0 J/mm³ |
| Intermediate (engraved) | 5 | S45 / F4000 | 0.200 mm | 27.0 J/mm³ |
| Bulk infill (vector) | 454 | S33 / F6000 | 0.200 mm | 13.2 J/mm³ |
| Bulk contour, 3 lines | — | S40 / F6000 | 0.200 mm | 16.0 J/mm³ |
| Flat surface skin | 14 | S30 / F3000 | 0.266 mm | 18.0 J/mm³ |
| Slanted surface skin | 12 | S30 / F3500 | 0.200 mm | 20.6 J/mm³ |

The full OLOS slicer settings record for this build is in the workbook, so the part can be reproduced on an OISK machine without reference to the thesis.

### Process window

| Regime | Energy density | Power / feed |
| --- | --- | --- |
| Validated lattice regime | 12-16 J/mm³ | S33-S35 / F6000 |
| Solid-section bulk | 18-24 J/mm³ | see workbook |
| Sacrificial anchor pad | 30-36 J/mm³ | S50-S55 / F3000 |
| Anchoring layers | 27-44 J/mm³ | S45-S55 / F3000-F4000 |

The central result of the campaign is that the achievable process window on this platform is bounded by **thermal boundary conditions** rather than by the laser or by parameter choice: static in-band energy densities fail as build height grows, while a height-staged power and feed profile sustains builds at a nominally sub-threshold 12-16 J/mm³ by exploiting accumulated substrate heat. The measured platform signature is a constant additive dimensional deviation of **+0.23 mm** and a melt track width of **0.38 mm**.

> These are prototype research settings developed on one machine, not a qualified production process. They are published so the builds can be reproduced and improved on.

### Results

<img src="media/OISK_Metal_3D_Printer_Validated_Part.jpg" width="49%"> <img src="media/OISK_Metal_3D_Printer_Validated_Part_Detail.jpg" width="49%">

<div align="center"><i>The validated full part: a geometrically complete 316L benchmark with closed hull, deck and cabin</i></div>

<br>

<img src="media/OISK_Metal_3D_Printer_Solid_Specimen.jpg" width="37%"> <img src="media/OISK_Metal_3D_Printer_Solid_Specimen_Pad.jpg" width="37%">

<div align="center"><i>Solid-section 8 mm specimens built on a sacrificial anchor pad</i></div>

Files
--

- **[CAD](cad)** - Fusion 360 and STEP models of the machine (see [cad/README.md](cad/README.md) for how to reassemble the split archives)
- **[PCB](pcb)** - Control Board V2: schematic, user manual, hardware documentation, BOM, Altium sources and manufacturing data
- **[Settings](settings/OISK_Metal_3D_Printer_Settings.xlsx)** - machine, controller and process settings
- **[BOM](OISK_Metal_3D_Printer_BOM_Preliminary.xlsx)** - machine BOM, *preliminary*
- **[PCB BOM](pcb/BOM/MetalPrinterV2_BOM_2026-06-15.xlsx)** - Control Board V2, 142 line items
- **[Firmware](firmware)** - firmware architecture of the three RP2350B CPUs; source code *in preparation*
- **[PCB Schematic](pcb/MetalPrinter_V2_Schematic.pdf)** - Control Board V2, 9 sheets
- **Machine Wiring Schematic** - *in preparation*

Status
--

Current work on the machine:

- currently testing the galvo firmware code
- integrating the communication of the 3 CPU firmware
- modifying the hardware to support the latest version of the PCB
- upcoming test with the galvo and the fiber laser source

Roadmap
--

Next steps planned until December 2026:

- testing of the fiber laser source (dual-configuration LPBF)
- integration of the new controller PCB
- machine refinement based on the prototype results
- full open documentation release - BOM, CAD, assembly manual
- further publication in preparation

Publication
--

- T. Redlich, D. Ingrassia, M. Moritz, M. Omer, *Towards an Open Source Metal 3D Printer: A Dual-Configuration LPBF Concept Based on Benchmarking and Design Analysis*, Procedia CIRP, Vol. 142 (2026) - open access

Author
--

The OISK Metal 3D Printer has been designed and built by **[InMachines Ingrassia GmbH](https://www.inmachines.net/)**.

<img src="https://irp.cdn-website.com/2b5ccdcd/dms3rep/multi/InMachines_Logo_positive_white.png" width="50%">

<br>

Machine design:
- **[Daniele Ingrassia](https://www.linkedin.com/in/danieleingrassia/)**

316L process development and the OLOS metal slicing workflow:
- **Dharunjay Tiwari**

The machine is part of the **Open Industrial Starter Kit (OISK)** group of open source industrial digital fabrication machines.

OISK is developed by **[InMachines Ingrassia GmbH](https://www.inmachines.net/)** for the **[Dtec project](https://dtecbw.de/home/forschung/hsu/projekt-fabcity)** at **[Fab City Hamburg](https://www.fabcity.hamburg/en/)**.

OISK Partners:
<br><br>
<img src="https://irp.cdn-website.com/2b5ccdcd/dms3rep/multi/OLSK_partners.png" width="80%">

Contact
--

- daniele@inmachines.net
- [https://www.inmachines.net/](https://www.inmachines.net/)

License
--

Hardware design, CAD and PCB files, BOM, settings and other technical or design files are released under the following license:

- CERN Open Hardware Licence Version 2 Strongly Reciprocal - **[CERN-OHL-S](LICENSE_CERN_OHL_S_V2.txt)**

Assembly manual, pictures, videos, presentations, description text and other type of media are released under the following license:

- Creative-Commons-Attribution-ShareAlike 4.0 International - **[CC BY-SA 4.0](LICENSE_CC_BY_SA_4.0.txt)**

The CPU-A firmware in [firmware/CPU-A](firmware/CPU-A) is a vendored copy of [grblHAL](https://github.com/grblHAL) and keeps its own upstream licensing:

- GNU General Public License v3.0 or later - **[GPL-3.0-or-later](LICENSES/GPL-3.0-or-later.txt)**, see [firmware/CPU-A/COPYING](firmware/CPU-A/COPYING) for the components inside it under BSD-3-Clause, MIT and the FatFs license
