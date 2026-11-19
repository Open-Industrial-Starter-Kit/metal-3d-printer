/*
 * CPU-B passthrough test - OISK Metal 3D Printer, Control Board V2
 *
 * Bridges the CPU-B USB-C port to the UART0 link that runs to CPU-A, so a
 * G-code sender on the host can talk to grblHAL on CPU-A without CPU-A
 * needing its own USB connection.
 *
 * Host  <--USB CDC-->  CPU-B  <--UART0, GP0/GP1-->  CPU-A (grblHAL)
 *
 * This is deliberately minimal. It touches GP0 and GP1 and nothing else.
 * The full Piccola CNC CPU-B firmware in ../../CPU-B drives several pins
 * that belong to other functions on this board - among them GP15, which is
 * the RS-485 receiver output MAX485_DO, and GP4/GP5, which are the serial
 * link to CPU-C. Do not flash that one here until it has been ported.
 *
 * Serial1 uses the Arduino-Pico default pins for UART0, GP0 = TX and
 * GP1 = RX, which is how the Control Board V2 wires CPU-B to CPU-A.
 *
 * SPDX-License-Identifier: CC-BY-SA-4.0
 * Copyright (c) 2026 Daniele Ingrassia
 */

#include <Arduino.h>

// grblHAL on CPU-A runs at its default 115200 baud.
static const uint32_t CPUA_BAUD_RATE = 115200;

// Deep enough not to drop bytes while grblHAL floods status reports.
static const size_t SERIAL_FIFO_SIZE = 1024;

void setup()
{
    Serial.begin(CPUA_BAUD_RATE);   // USB CDC - the baud rate is ignored

    Serial1.setFIFOSize(SERIAL_FIFO_SIZE);
    Serial1.begin(CPUA_BAUD_RATE);  // GP0 TX, GP1 RX -> CPU-A
}

void loop()
{
    while (Serial.available()) {
        Serial1.write((uint8_t)Serial.read());
    }

    while (Serial1.available()) {
        Serial.write((uint8_t)Serial1.read());
    }
}
