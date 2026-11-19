/*
 * OISK Metal 3D Printer - Control Board V2
 * Pico SDK board definition for the RP2350B CPUs (CPU-A / CPU-B / CPU-C).
 *
 * Copyright (c) 2020 Raspberry Pi (Trading) Ltd.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

// -----------------------------------------------------
// NOTE: THIS HEADER IS ALSO INCLUDED BY ASSEMBLER SO
//       SHOULD ONLY CONSIST OF PREPROCESSOR DIRECTIVES
// -----------------------------------------------------

#ifndef _BOARDS_METAL_PRINTER_V2_H
#define _BOARDS_METAL_PRINTER_V2_H

pico_board_cmake_set(PICO_PLATFORM, rp2350)

// For board detection
#define METAL_PRINTER_V2

// --- UART ---
// CPU-A <-> CPU-B serial link (UART0 on GP0/GP1).
#ifndef PICO_DEFAULT_UART
#define PICO_DEFAULT_UART 0
#endif
#ifndef PICO_DEFAULT_UART_TX_PIN
#define PICO_DEFAULT_UART_TX_PIN 0
#endif
#ifndef PICO_DEFAULT_UART_RX_PIN
#define PICO_DEFAULT_UART_RX_PIN 1
#endif

// --- LED ---
// CpuAled
#ifndef PICO_DEFAULT_LED_PIN
#define PICO_DEFAULT_LED_PIN 39
#endif

// --- FLASH ---
// W25Q32 class, 4 MB QSPI per CPU.
#define PICO_BOOT_STAGE2_CHOOSE_W25Q080 1

#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 2
#endif

pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (4 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (4 * 1024 * 1024)
#endif

// --- RP2350 VARIANT ---
// This means RP2350B (48 GPIO).
#define PICO_RP2350A 0

pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#ifndef PICO_RP2350_A2_SUPPORTED
#define PICO_RP2350_A2_SUPPORTED 1
#endif

#endif
