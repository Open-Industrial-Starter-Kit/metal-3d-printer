# Test firmware - OISK Metal 3D Printer

Small, single-purpose firmwares for bringing up the Control Board V2. They are
not part of the machine firmware: each one does one thing so that a single
subsystem can be tested on its own.

| Test | CPU | Purpose |
| --- | --- | --- |
| **[CPU-B_passthrough](CPU-B_passthrough)** | CPU-B | Bridge the CPU-B USB-C port to the UART0 link to CPU-A, to drive grblHAL from a host G-code sender |

## CPU-B_passthrough

```
Host  <--USB CDC-->  CPU-B  <--UART0, GP0/GP1, 115200-->  CPU-A (grblHAL)
```

It touches **GP0 and GP1 only**. `Serial1` uses the Arduino-Pico default pins
for UART0, GP0 = TX and GP1 = RX, which is exactly how the Control Board V2
wires CPU-B to CPU-A: net `UART0RX_A` joins CPU-B GP0 to CPU-A GP1, and net
`UART0TX_A` joins CPU-B GP1 to CPU-A GP0.

Build and flash:

```
cd CPU-B_passthrough
pio run                 # build
pio run -t upload       # flash over USB, or copy .pio/build/cpub_passthrough/firmware.uf2
```

Then flash CPU-A with the **UART** build of grblHAL,
`firmware/CPU-A/build/grblHAL.uf2`, not the `build-usb` one: CPU-A has to talk
on GP0/GP1 for this bridge to reach it.

Connect a G-code sender to the CPU-B serial port at 115200 and send `$$` or
`?`. A grblHAL settings dump or a status report means the whole chain works.

### Why not the full CPU-B firmware

The Piccola CNC CPU-B firmware in [../CPU-B](../CPU-B) talks to CPU-A on the
same pins and at the same baud rate, and already has a passthrough mode, but
its `setup()` drives pins that carry other functions on the Control Board V2:

| CPU-B pin | Piccola firmware | Control Board V2 |
| --- | --- | --- |
| GP15 | `BRUSHLESS_MOTOR_DIR`, output | `MAX485_DO`, the RS-485 receiver **output** |
| GP2 | `ENABLE_X`, output | `SPI0_SCK_B` |
| GP26 / GP27 | sensor direction / enable, outputs | `SPI1_SCK_B` / `SPI1_TX_B` |
| GP8 / GP9 | `Serial2` to the ESP32C3 | `GM490_DI` / `GM490_DO`, RS-422 |
| GP4 / GP5 | distance laser sensor UART | `UART0RX_C` / `UART0TX_C`, the link to CPU-C |

GP15 and GP2 would put two drivers on the same net. That firmware needs porting
before it is flashed on this board.
