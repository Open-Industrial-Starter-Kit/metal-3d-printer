"""
ble_advertiser.py
Bluetooth-LE *advertiser* used as the WiFi-discovery side-channel.

The MiniCNC firmware (ESP32-C3) is a BLE **scanner** — it actively scans for a
3-second window looking for a peripheral whose *Complete Local Name* begins with
``minicnc_``.  The remainder of the name is the host's IPv4 address with dots
replaced by underscores, e.g.::

    minicnc_192_168_1_50      ->   ws://192.168.1.50:8765

Having read that name, the machine connects out to ``ws://<ip>:8765`` (the
WebSocket server hosted by this desktop app).  This mirrors exactly what the
Android app does (it advertises its own IP over BLE so the machine can find it).

There is no single cross-platform Python API for BLE *advertising* (peripheral
role), so this module selects a per-OS backend at runtime:

    * macOS    – CoreBluetooth (CBPeripheralManager) via PyObjC
    * Linux    – BlueZ LEAdvertisement over D-Bus (dbus-fast)
    * Windows  – WinRT BluetoothLEAdvertisementPublisher

If the platform backend or its optional dependency is missing the advertiser
becomes a no-op and emits an informational status message — the rest of the app
(serial transport, WebSocket server) keeps working regardless.
"""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, Signal

# Local-name prefix the firmware filters on (DEFAULT_ESP32C3_BLE_SCAN_NAME_FILTER).
NAME_PREFIX = "minicnc_"


def ip_to_local_name(ip: str) -> str:
    """Encode an IPv4 address into the BLE Local Name the firmware expects."""
    return NAME_PREFIX + ip.replace(".", "_")


class BleAdvertiser(QObject):
    """
    Advertises ``minicnc_<ip>`` over BLE so the machine can discover this host.

    Signals
    -------
    status(str)   human-readable progress / error messages (for the log)
    """

    status = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._backend = None
        self._current_ip: str | None = None
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_advertising(self) -> bool:
        return self._backend is not None and self._current_ip is not None

    def start(self, ip: str) -> None:
        """(Re)start advertising the given host IPv4 address."""
        with self._lock:
            if not ip:
                self.status.emit("[BLE] No host IP available — advertising skipped.")
                return
            if self._current_ip == ip and self._backend is not None:
                return  # already advertising this IP
            self._stop_locked()
            name = ip_to_local_name(ip)
            try:
                self._backend = _make_backend(name, self._emit)
            except Exception as exc:  # noqa: BLE001 - never let BLE crash the app
                self._backend = None
                self.status.emit(f"[BLE] Advertiser unavailable: {exc}")
                return
            if self._backend is None:
                self.status.emit(
                    f"[BLE] No BLE advertising backend for this platform "
                    f"({sys.platform}) — WiFi discovery via Bluetooth disabled."
                )
                return
            self._current_ip = ip
            self.status.emit(f"[BLE] Advertising as '{name}' (ws://{ip}:8765)")

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    # ── Internal ──────────────────────────────────────────────────────────

    def _stop_locked(self) -> None:
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
            self._backend = None
        self._current_ip = None

    def _emit(self, msg: str) -> None:
        # Backends call this from their own threads; Signal is thread-safe.
        self.status.emit(msg)


# ── Backend factory ─────────────────────────────────────────────────────────

def _make_backend(local_name: str, log):
    """Return a started backend object exposing ``stop()`` or None."""
    if sys.platform == "darwin":
        return _MacBackend(local_name, log)
    if sys.platform.startswith("linux"):
        return _LinuxBackend(local_name, log)
    if sys.platform.startswith("win"):
        return _WindowsBackend(local_name, log)
    return None


# ── macOS (CoreBluetooth) ────────────────────────────────────────────────────

class _MacBackend:
    """Advertise the Local Name via CBPeripheralManager (PyObjC)."""

    def __init__(self, local_name: str, log) -> None:
        import objc  # noqa: F401  (ensures PyObjC present)
        from CoreBluetooth import (
            CBPeripheralManager,
            CBAdvertisementDataLocalNameKey,
        )
        from Foundation import NSObject

        self._log = log
        self._name = local_name
        self._mgr = None
        self._stopped = False

        # Delegate that starts advertising as soon as the radio is powered on.
        class _Delegate(NSObject):
            def initWithBackend_(self, backend):
                self = objc.super(_Delegate, self).init()
                if self is None:
                    return None
                self._backend = backend
                return self

            def peripheralManagerDidUpdateState_(self, mgr):
                # Runs on CoreBluetooth's own dispatch queue, and can fire during
                # app teardown (e.g. the radio state changes as the process
                # exits). A Python exception raised here is converted to an
                # *uncaught NSException* and crashes the process, so the whole
                # body is guarded and it no-ops once the backend has stopped.
                try:
                    backend = self._backend
                    if backend is None or backend._stopped:
                        return
                    # CBManagerStatePoweredOn == 5
                    state = int(mgr.state())
                    if state == 5:
                        mgr.startAdvertising_(
                            {CBAdvertisementDataLocalNameKey: backend._name}
                        )
                        backend._log(
                            f"[BLE] CoreBluetooth powered on, advertising "
                            f"'{backend._name}'"
                        )
                    else:
                        backend._log(
                            f"[BLE] CoreBluetooth state={state} (need 5=PoweredOn). "
                            f"Check Bluetooth is on and the app has permission."
                        )
                except Exception:
                    pass

        self._delegate = _Delegate.alloc().initWithBackend_(self)

        # Run the manager on a dedicated dispatch queue so it is independent of
        # the Qt run loop; fall back to the main queue if libdispatch is absent.
        queue = None
        try:
            from libdispatch import dispatch_queue_create
            queue = dispatch_queue_create(b"piccola.ble.advertiser", None)
        except Exception:
            queue = None

        self._mgr = CBPeripheralManager.alloc().initWithDelegate_queue_(
            self._delegate, queue
        )

    def stop(self) -> None:
        # Mark stopped first so any in-flight delegate callback bails out, then
        # detach the delegate so CoreBluetooth stops calling into Python at all
        # (prevents the exit-time NSException crash), then stop advertising.
        self._stopped = True
        mgr = self._mgr
        if mgr is not None:
            try:
                mgr.setDelegate_(None)
            except Exception:
                pass
            try:
                mgr.stopAdvertising()
            except Exception:
                pass
        self._mgr = None
        self._delegate = None


# ── Linux (BlueZ over D-Bus) ─────────────────────────────────────────────────

class _LinuxBackend:
    """
    Register an org.bluez.LEAdvertisement1 object exposing a LocalName.

    Uses dbus-fast in a background asyncio loop.  BlueZ >= 5.43 required.
    """

    def __init__(self, local_name: str, log) -> None:
        import asyncio  # noqa: F401  (verify availability early)
        from dbus_fast.aio import MessageBus  # noqa: F401

        self._name = local_name
        self._log = log
        self._loop = None
        self._stop_evt = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001
            self._log(f"[BLE] Linux advertiser error: {exc}")

    async def _serve(self) -> None:
        import asyncio
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method, dbus_property
        from dbus_fast import BusType, Variant, PropertyAccess

        self._stop_evt = asyncio.Event()
        name = self._name

        class Advertisement(ServiceInterface):
            def __init__(self):
                super().__init__("org.bluez.LEAdvertisement1")

            @dbus_property(access=PropertyAccess.READ)
            def Type(self) -> "s":  # noqa: F821,N802
                return "broadcast"

            @dbus_property(access=PropertyAccess.READ)
            def LocalName(self) -> "s":  # noqa: F821,N802
                return name

            @dbus_property(access=PropertyAccess.READ)
            def Includes(self) -> "as":  # noqa: F821,N802
                return []

            @method()
            def Release(self):  # noqa: N802
                pass

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        adv = Advertisement()
        path = "/org/piccola/ble/adv0"
        bus.export(path, adv)

        # Find the first adapter (hci0) advertising manager.
        introspection = await bus.introspect("org.bluez", "/org/bluez/hci0")
        obj = bus.get_proxy_object("org.bluez", "/org/bluez/hci0", introspection)
        adv_mgr = obj.get_interface("org.bluez.LEAdvertisingManager1")
        await adv_mgr.call_register_advertisement(path, {})
        self._log(f"[BLE] BlueZ advertising '{name}' on hci0")

        try:
            await self._stop_evt.wait()
        finally:
            try:
                await adv_mgr.call_unregister_advertisement(path)
            except Exception:
                pass

    def stop(self) -> None:
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)


# ── Windows (WinRT) ──────────────────────────────────────────────────────────

class _WindowsBackend:
    """Advertise the Local Name via BluetoothLEAdvertisementPublisher (WinRT)."""

    # AD type 0x09 = Complete Local Name.
    _AD_LOCAL_NAME = 0x09

    def __init__(self, local_name: str, log) -> None:
        try:
            from winrt.windows.devices.bluetooth.advertisement import (
                BluetoothLEAdvertisementPublisher,
                BluetoothLEAdvertisementDataSection,
            )
            from winrt.windows.storage.streams import DataWriter
        except Exception:  # pragma: no cover - fallback to older package name
            from winsdk.windows.devices.bluetooth.advertisement import (  # type: ignore
                BluetoothLEAdvertisementPublisher,
                BluetoothLEAdvertisementDataSection,
            )
            from winsdk.windows.storage.streams import DataWriter  # type: ignore

        self._log = log
        writer = DataWriter()
        writer.write_string(local_name)
        section = BluetoothLEAdvertisementDataSection()
        section.data_type = self._AD_LOCAL_NAME
        section.data = writer.detach_buffer()

        self._publisher = BluetoothLEAdvertisementPublisher()
        self._publisher.advertisement.data_sections.append(section)
        self._publisher.start()
        self._log(f"[BLE] WinRT advertising '{local_name}'")

    def stop(self) -> None:
        if getattr(self, "_publisher", None) is not None:
            try:
                self._publisher.stop()
            except Exception:
                pass
        self._publisher = None
