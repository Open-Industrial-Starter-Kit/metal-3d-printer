# Firmware - OISK Metal 3D Printer

The firmware of the OISK Metal 3D Printer runs on the **Metal 3D Printer Control
Board V2**, which carries three RP2350B microcontrollers connected to each other
by serial links. Each CPU can be operated individually or as part of the
coordinated machine.

- **[Metal 3D printer firmware.pdf](Metal%203D%20printer%20firmware.pdf)** -
  firmware architecture and responsibility of each CPU

## Architecture

| CPU | Role | Responsibilities |
| --- | --- | --- |
| CPU-A | Motion | grblHAL, 5 axes (X and Y move the diode laser, Z the printing piston, A the reserve piston, B the recoater), inductive end stops, incoming G-code, diode laser power modulation |
| CPU-B | Main controller | Ethernet link to the computer, communication between the other two CPUs, oxygen / temperature / fine dust and other sensors, dispatching of a custom G-code extension, coordination of the machine |
| CPU-C | Galvo and fiber laser | XY2-100 protocol at 2 MHz generated in PIO for the galvanometer X and Y axes, power modulation and frequency of the fiber laser module, galvo G-code handling with speed and acceleration profiling - a custom port of OpenGalvo (originally running on a Teensy 4.0) |

The hardware these firmwares target is documented in **[../pcb](../pcb)**.

## Status

The source code is in preparation and will be published here. Current work:

- testing of the galvo firmware code on CPU-C
- integration of the communication between the three CPU firmwares
- adaptation to the latest version of the control board

## License

See the [licenses of this repository](../README.md#license).
