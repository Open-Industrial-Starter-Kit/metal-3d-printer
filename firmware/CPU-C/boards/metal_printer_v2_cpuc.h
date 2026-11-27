/*
 * Metal 3D Printer Control Board V2 — CPU-C
 *
 * Replaces weact_studio_rp2350b_core, whose defaults are wrong for this PCB in three ways
 * that matter:
 *
 *   - PSRAM chip select is GPIO 0 on the WeAct board. Here PSRAM_CS_C is GPIO 47, and
 *     GPIO 0 is UART0TX_C, the link to CPU-B. Initialising PSRAM under the WeAct header
 *     would drive a chip select onto the inter-CPU UART.
 *   - Flash is declared as 16 MB. This board fits a W25Q32JVSSIQ, which is 4 MB
 *     (BOM C179173, confirmed on the assembled board).
 *   - The default UART/LED/I2C/SPI pins are all for the WeAct module.
 *
 * Pin assignments below are taken from the CPU-C sheet of the V2 schematic.
 */

#ifndef _BOARDS_METAL_PRINTER_V2_CPUC_H
#define _BOARDS_METAL_PRINTER_V2_CPUC_H

pico_board_cmake_set(PICO_PLATFORM, rp2350)

#define METAL_PRINTER_V2_CPUC

// --- UART: the CPU-B link, UART0 on GP0/GP1 through the 10R resistors R90/R91 ---
#ifndef PICO_DEFAULT_UART
#define PICO_DEFAULT_UART 0
#endif
#ifndef PICO_DEFAULT_UART_TX_PIN
#define PICO_DEFAULT_UART_TX_PIN 0
#endif
#ifndef PICO_DEFAULT_UART_RX_PIN
#define PICO_DEFAULT_UART_RX_PIN 1
#endif

// --- status LED ---
#ifndef PICO_DEFAULT_LED_PIN
#define PICO_DEFAULT_LED_PIN 2
#endif

// --- flash: W25Q32JVSSIQ, 4 MB ---
#define PICO_BOOT_STAGE2_CHOOSE_W25Q080 1
#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 2
#endif
pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (4 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)
#endif

// --- PSRAM: APS6404L-3SQR-SN, 8 MB, on QMI CS1 = GPIO 47 (net PSRAM_CS_C) ---
//
// Set explicitly rather than auto-detected: PICO_AUTO_DETECT_PSRAM wiggles every GPIO the
// QMI can use as CS1, and on this board one of those is GPIO 0, the UART to CPU-B.
#ifndef PICO_PSRAM_CS_PIN
#define PICO_PSRAM_CS_PIN 47
#endif
pico_board_cmake_set_default(PICO_PSRAM_SIZE_BYTES, (8 * 1024 * 1024))
#ifndef PICO_PSRAM_SIZE_BYTES
#define PICO_PSRAM_SIZE_BYTES (8 * 1024 * 1024)
#endif

#define PICO_RP2350A 0
pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#ifndef PICO_RP2350_A2_SUPPORTED
#define PICO_RP2350_A2_SUPPORTED 1
#endif

#endif
