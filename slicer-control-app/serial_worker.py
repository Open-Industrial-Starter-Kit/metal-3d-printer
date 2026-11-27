"""
Serial I/O worker with automatic port discovery.

On start_auto_connect() a background thread cycles through all available
serial ports, sends a serialPing at 2 000 000 baud and waits for any JSON
response that contains a "result" key.  The first port that replies is
adopted as the connected device.  If the connection drops unexpectedly the
discovery loop restarts automatically.

Thread safety: all public signals are delivered on the Qt event-loop thread
via Qt's queued connection mechanism.
"""

import json
import threading
import time

import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, Signal

from app_config import SerialConfig


class SerialWorker(QObject):
    line_received      = Signal(str)        # one stripped UTF-8 line from the device
    connection_changed = Signal(bool, str)  # (connected, status_message)
    probe_status       = Signal(str)        # informational messages during discovery

    # A single write timeout means the firmware paused draining its buffer
    # (homing, probing, SD writes) — skip sends briefly and only declare the
    # link dead after several consecutive strikes.
    _TIMEOUT_STRIKES_MAX = 3
    _TIMEOUT_COOLDOWN_S = 1.5

    def __init__(self, config: SerialConfig) -> None:
        super().__init__()
        self._config = config
        self._serial: serial.Serial | None = None
        self._running   = False
        self._stop_evt  = threading.Event()   # set only on explicit disconnect()
        self._write_lock = threading.Lock()
        self._timeout_strikes = 0
        self._send_cooldown_until = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> str | None:
        """Device path of the adopted port, or None when not connected.

        The port is discovered, not configured, so it is not knowable from
        anywhere else — and anything that has to talk to the same board by
        another route (the UF2 reboot touch) needs exactly this one rather than
        whatever else is plugged in."""
        return self._serial.port if self._serial is not None else None

    def start_auto_connect(self) -> None:
        """Begin continuous port-discovery in a daemon thread."""
        self._stop_evt.clear()
        threading.Thread(target=self._auto_connect_loop, daemon=True).start()

    def disconnect(self) -> None:
        """User-initiated disconnect; suppresses automatic reconnection."""
        self._stop_evt.set()
        self._running = False
        if self._serial:
            try:
                try:
                    if hasattr(self._serial, "cancel_write"):
                        self._serial.cancel_write()
                except Exception:
                    pass
                try:
                    self._serial.reset_output_buffer()
                except Exception:
                    pass
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self.connection_changed.emit(False, "Disconnected")

    def send(self, data: str) -> None:
        """Send a raw string; CRLF is appended automatically.

        No flush(): tcdrain waits until the UART has physically transmitted
        everything, and these sends run on the GUI thread ~30x/s (poll timer)
        — any device stall would freeze the whole app. The OS transmits the
        buffered bytes on its own; write() is bounded by write_timeout_s."""
        if self.is_connected:
            self._write((data + "\r\n").encode("utf-8"))

    def send_priority(self, data: str) -> None:
        """Send a raw string after discarding stale buffered output.

        Used for explicit user actions and motion commands so old poll traffic
        cannot delay or reorder a move on the host/UART side."""
        if self.is_connected:
            self._write((data + "\r\n").encode("utf-8"), discard_pending=True,
                        flush_after=True)

    def send_bytes(self, data: bytes) -> None:
        """Send raw bytes as-is (no framing added)."""
        if self.is_connected:
            self._write(data)

    def send_bytes_priority(self, data: bytes) -> None:
        """Send raw bytes after discarding stale buffered output."""
        if self.is_connected:
            self._write(data, discard_pending=True, flush_after=True)

    def _write(self, data: bytes, discard_pending: bool = False,
               flush_after: bool = False) -> None:
        if time.monotonic() < self._send_cooldown_until:
            return          # device is catching up — drop (the polls repeat)
        with self._write_lock:
            try:
                if discard_pending:
                    self._discard_pending_output_locked()
                self._serial.write(data)
                if flush_after:
                    # Priority path only: bound command latency at the UART side.
                    # Poll traffic intentionally skips flush() to avoid UI stalls.
                    self._serial.flush()
                self._timeout_strikes = 0
            except serial.SerialTimeoutException as exc:
                # Transient stall (firmware busy in a blocking operation):
                # don't kill a healthy link on the first strike — pause the
                # outgoing traffic and give the device time to drain.
                self._timeout_strikes += 1
                if self._timeout_strikes >= self._TIMEOUT_STRIKES_MAX:
                    self.connection_changed.emit(False, f"Write error: {exc}")
                    self._drop_connection()
                else:
                    self._send_cooldown_until = (
                        time.monotonic() + self._TIMEOUT_COOLDOWN_S)
                    self.probe_status.emit(
                        "Serial link busy — pausing outgoing messages…")
            except serial.SerialException as exc:
                self.connection_changed.emit(False, f"Write error: {exc}")
                self._drop_connection()

    def _discard_pending_output_locked(self) -> None:
        s = self._serial
        if s is None or not s.is_open:
            return
        try:
            if hasattr(s, "cancel_write"):
                s.cancel_write()
        except Exception:
            pass
        try:
            s.reset_output_buffer()
        except Exception:
            pass

    def _drop_connection(self) -> None:
        """Close a wedged port (e.g. after a write timeout). The read loop
        then exits on its own and, since this is not a user disconnect, the
        auto-connect loop restarts and re-adopts the device when it drains."""
        s, self._serial = self._serial, None
        if s is not None:
            try:
                try:
                    if hasattr(s, "cancel_write"):
                        s.cancel_write()
                except Exception:
                    pass
                try:
                    s.reset_output_buffer()
                except Exception:
                    pass
                s.close()
            except Exception:
                pass

    # ── Auto-connect ──────────────────────────────────────────────────────

    def _auto_connect_loop(self) -> None:
        while not self._stop_evt.is_set():
            ports = sorted(p.device for p in serial.tools.list_ports.comports())
            if not ports:
                self.probe_status.emit("No serial ports found, retrying…")
                self._stop_evt.wait(self._config.retry_interval_s)
                continue
            for port in ports:
                if self._stop_evt.is_set():
                    return
                port_lower = port.lower()
                if any(token in port_lower for token in self._config.skip_port_tokens):
                    self.probe_status.emit(f"Skipping {port} (filtered by name)")
                    continue
                self.probe_status.emit(f"Probing {port} @ {self._config.baudrate:,} baud…")
                if self._probe_port(port):
                    return  # successfully connected
            self.probe_status.emit(
                f"No CPUB device found on any port. Retrying in {self._config.retry_interval_s:.3f} s…"
            )
            self._stop_evt.wait(self._config.retry_interval_s)

    def _probe_port(self, port: str) -> bool:
        """
        Open *port*, send a serialPing and wait up to PROBE_TIMEOUT seconds
        for a JSON response containing a 'result' key.
        Returns True and starts the read loop if successful.
        """
        try:
            s = serial.Serial(port, self._config.baudrate,
                              timeout=self._config.probe_timeout_s,
                              write_timeout=self._config.write_timeout_s)
        except serial.SerialException as exc:
            self.probe_status.emit(f"  {port}: cannot open – {exc}")
            return False
        try:
            try:
                s.reset_output_buffer()
            except Exception:
                pass
            s.reset_input_buffer()
            s.write(b'{"type":"serialPing"}\r\n')
            s.flush()
            deadline = time.monotonic() + self._config.probe_timeout_s
            while time.monotonic() < deadline:
                raw = s.readline()
                if not raw:
                    continue
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if "result" in parsed:
                        # Valid CPUB JSON response – adopt this port
                        self._serial = s
                        self._timeout_strikes = 0
                        self._send_cooldown_until = 0.0
                        self._running = True
                        threading.Thread(target=self._read_loop, daemon=True).start()
                        self.connection_changed.emit(
                            True, f"Connected to {port} at {self._config.baudrate:,} baud"
                        )
                        return True
                except (json.JSONDecodeError, ValueError):
                    pass  # not JSON yet, keep waiting
        except Exception as exc:
            self.probe_status.emit(f"  {port}: error during probe – {exc}")
        try:
            s.close()
        except Exception:
            pass
        self.probe_status.emit(f"  {port}: no response")
        return False

    # ── Read loop ─────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        while self._running and self._serial and self._serial.is_open:
            try:
                raw = self._serial.readline()
                if raw:
                    line = raw.decode(errors="replace").strip()
                    if line:
                        self.line_received.emit(line)
            except Exception:
                break
        if self._running:
            # Unexpected loss – restart discovery
            self._running = False
            self._serial = None
            self.connection_changed.emit(False, "Connection lost – reconnecting…")
            if not self._stop_evt.is_set():
                threading.Thread(target=self._auto_connect_loop, daemon=True).start()
