"""
app_config.py
Minimal config shim so serial_worker.py (copied verbatim from the test app)
can import SerialConfig without changes.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SerialConfig:
    baudrate: int = 2_000_000
    probe_timeout_s: float = 0.25
    retry_interval_s: float = 0.5
    # Upper bound on a single blocking write. Sends run on the GUI thread, so
    # without this a stalled device (busy firmware, USB hiccup) freezes the
    # whole app; with it the write errors out and auto-reconnect takes over.
    write_timeout_s: float = 1.0
    skip_port_tokens: list = field(default_factory=lambda: ["bluetooth", "cameo", "charge"])
