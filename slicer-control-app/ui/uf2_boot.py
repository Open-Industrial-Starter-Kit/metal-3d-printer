"""
ui/uf2_boot.py
Putting the controller into UF2 (BOOTSEL) mode from the app, with picotool.

What this talks to
------------------
The board the app is connected to over USB serial — CPUB, a Pico 2 W
(`board = rpipico2w` in the firmware's platformio.ini), so an RP2350. It runs
the JSON API, reads the job off the SD card and streams it to the motion
controller. Rebooting it into UF2 mode replaces its USB serial device with a
mass-storage bootloader, which is why the connection does not come back on its
own: there is nothing left running to answer a serialPing.

How, without installing anything
--------------------------------
The Arduino core this firmware is built on implements the classic Arduino
auto-reset. From its own `SerialUSB.cpp`:

    Implements an ACM which will reboot into UF2 mode on a 1200bps DTR toggle.

    void SerialUSB::checkSerialReset() {
        if (!_ss.rebooting && (_ss.bps == 1200) && (!_ss.dtr)) {
            ...
            reset_usb_boot(0, 0);

`checkSerialReset()` is called from BOTH USB callbacks — the one for a baud
rate change and the one for a DTR change — so the two conditions can be met in
either order. Opening the port at 1200 baud and then dropping DTR is enough,
and that is `touch_to_uf2()`: pyserial, which the app already depends on, and
nothing else. No tool to install, and it acts on the exact port the app is
connected to rather than on whatever RP2350 happens to be plugged in.

The device disappears mid-call, so an error from the close afterwards is the
expected ending, not a failure.

picotool, as the fallback
-------------------------
Kept for when the touch cannot run — most plausibly another process holding
the port, since picotool reaches the board through the SDK's USB reset
interface instead of the serial one:

    -u   reboot INTO BOOTSEL/UF2 mode (rather than restarting the firmware)
    -f   force it from a device that is NOT already in BOOTSEL, i.e. one
         running normal firmware — which is the whole point here

Both flags are needed. Without `-u` the board restarts back into its firmware;
without `-f` picotool only talks to boards already in BOOTSEL and reports that
it found none. It is optional: when it is missing the only honest thing to do
is say so and say where to get it, which is what `install_hint()` is for.

"""

from __future__ import annotations

import os
import shutil
import sys
import time

#: The baud rate the firmware watches for. Not a speed — nothing is
#: transmitted at it — it is the signal, and it is the same 1200 the Arduino
#: IDE has used to reset boards for years.
TOUCH_BAUD = 1200

#: How long to hold the port open with DTR low. The firmware acts on the USB
#: callback, so the reboot is already under way before this elapses; it is
#: there so the close does not race the host's own line-state handling.
TOUCH_HOLD_S = 0.08


def touch_to_uf2(port: str, serial_module=None, sleep=time.sleep):
    """Reboot the controller into UF2 mode over its own serial port.

    Opens *port* at `TOUCH_BAUD` and drops DTR, which is what the firmware
    watches for (see the module docstring). Returns (rebooted, detail).

    The device vanishes the moment it acts, so writing, reading or closing
    afterwards can all fail — and that failure means it worked. Only failing to
    OPEN the port is a real failure, and it has one likely cause: something
    else is holding it.
    """
    mod = serial_module
    if mod is None:
        import serial as mod                      # noqa: PLC0415 (optional at import time)
    try:
        link = mod.Serial(port, TOUCH_BAUD)
    except Exception as exc:                      # SerialException, OSError, ...
        return False, str(exc)
    try:
        # Opening asserts DTR; the firmware wants to see it go LOW while the
        # line coding says 1200. Setting it explicitly is what makes the edge.
        link.dtr = False
        sleep(TOUCH_HOLD_S)
    except Exception:
        pass                                      # the board is already going
    try:
        link.close()
    except Exception:
        pass                                      # ...and gone
    return True, ""


#: The command. `reboot` is picotool's verb; the flags are explained above and
#: are not interchangeable — see `test_uf2_boot.py`, which pins them.
REBOOT_ARGS = ("reboot", "-f", "-u")

#: How long picotool may take. It is a USB control transfer and a reset, so it
#: answers in well under a second; anything beyond this is a hung device or a
#: driver waiting on something, and the operator needs to be told rather than
#: left watching a spinner.
TIMEOUT_MS = 10_000

#: Where picotool installs when it is not on PATH. PATH is always tried first —
#: these are only for the common case of a GUI app launched from Finder or the
#: Start menu, which inherits a minimal PATH that often lacks Homebrew's.
_CANDIDATES = {
    "darwin": (
        "/opt/homebrew/bin/picotool",        # Homebrew, Apple silicon
        "/usr/local/bin/picotool",           # Homebrew, Intel
        os.path.expanduser("~/.pico-sdk/picotool/picotool"),
        # PlatformIO installs one with the RP2040/RP2350 toolchain, so anyone
        # who has ever built this firmware already has a picotool.
        os.path.expanduser(
            "~/.platformio/packages/tool-picotool-rp2040-earlephilhower/picotool"),
    ),
    "win32": (
        r"C:\Program Files\Raspberry Pi\picotool\picotool.exe",
        r"C:\Program Files (x86)\Raspberry Pi\picotool\picotool.exe",
        os.path.expanduser(r"~\.pico-sdk\picotool\picotool.exe"),
        os.path.expanduser(
            r"~\.platformio\packages\tool-picotool-rp2040-earlephilhower\picotool.exe"),
    ),
    "linux": (
        "/usr/bin/picotool",
        "/usr/local/bin/picotool",
        os.path.expanduser("~/.pico-sdk/picotool/picotool"),
        os.path.expanduser(
            "~/.platformio/packages/tool-picotool-rp2040-earlephilhower/picotool"),
    ),
}


def find_picotool(platform: str | None = None, env_path: str | None = None):
    """Absolute path to a usable picotool, or None.

    PATH first (that is where a developer's own build lives), then the standard
    install locations for this platform — a windowed app does not inherit the
    shell's PATH, so "it works in my terminal" and "the button works" are
    genuinely different questions.
    """
    plat = platform or sys.platform
    found = shutil.which("picotool", path=env_path)
    if found:
        return found
    for path in _CANDIDATES.get(plat, ()):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def install_hint(platform: str | None = None) -> str:
    """One line telling the operator how to get picotool on this machine."""
    plat = platform or sys.platform
    if plat == "darwin":
        return "Install it with:  brew install picotool"
    if plat == "win32":
        return ("Install it with the Raspberry Pi Pico Windows installer, or "
                "download picotool from github.com/raspberrypi/picotool")
    return ("Install it with your package manager (apt install picotool), or "
            "build it from github.com/raspberrypi/picotool")


def describe_result(exit_code: int, output: str,
                    platform: str | None = None) -> tuple[bool, str]:
    """(succeeded, what to tell the operator) for a finished picotool run.

    picotool's own wording is kept in the failure text — it is often the only
    thing that says which of several USB problems this is — but it is prefixed
    with the reading that matters, because "No accessible RP2040/RP2350 devices
    in BOOTSEL mode were found" does not obviously mean "your board is fine,
    the reset interface just is not reachable".
    """
    plat = platform or sys.platform
    text = (output or "").strip()
    low = text.lower()

    if exit_code == 0:
        return True, SUCCESS_TEXT
    return _failure_text(exit_code, text, low, plat)


#: What the operator is told when it worked, whichever route did it.
SUCCESS_TEXT = ("The controller is now in UF2 mode and has disconnected "
                "from the app.\n\nIt stays that way until you flash a "
                ".uf2 onto the drive it exposes, or power-cycle the "
                "machine.")


def _failure_text(exit_code: int, text: str, low: str, plat: str):
    if "no accessible" in low or "not found" in low or "no devices" in low:
        extra = ("\n\nOn Windows the reset interface needs its WinUSB driver — "
                 "Zadig can bind it." if plat == "win32" else "")
        return False, ("picotool could not reach the controller over USB.\n\n"
                       "Check that the machine is connected by cable and "
                       "powered on." + extra
                       + ("\n\npicotool said: " + text if text else ""))

    if "permission" in low or "access" in low or "libusb" in low:
        return False, ("picotool was not allowed to talk to the controller.\n\n"
                       + ("Try running the app as administrator."
                          if plat == "win32" else
                          "A udev rule may be needed for the device."
                          if plat.startswith("linux") else
                          "macOS may be blocking USB access for this app.")
                       + ("\n\npicotool said: " + text if text else ""))

    return False, ("picotool failed (exit code %d).%s"
                   % (exit_code, "\n\n" + text if text else ""))
