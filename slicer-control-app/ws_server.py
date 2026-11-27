"""
WebSocket server with optional mDNS advertising via zeroconf.

Runs in a dedicated daemon thread (asyncio event loop).  All log output and
the running/stopped state change are delivered to the Qt main thread via Qt
signals, so it is safe to connect them directly to UI slots.
"""

import asyncio
import socket
import threading

import websockets
from PySide6.QtCore import QObject, Signal


def _local_ip() -> str:
    """Best-effort probe to find the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class WsServer(QObject):
    # Signals are always dispatched to the Qt event loop thread via auto-
    # queued connection because this QObject lives on the main thread.
    log_signal           = Signal(str, str)   # (text, css-color)
    state_changed        = Signal(bool)       # True = running, False = stopped
    client_count_changed = Signal(int)        # emits connected client count
    client_connected     = Signal(str)        # a client connected (per-conn id)
    client_disconnected  = Signal(str)        # a client disconnected (id)
    message_received     = Signal(str, str)   # (client id, raw text message)

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._zeroconf  = None
        self._svc_info  = None
        self._client_count = 0
        # clientId → websocket. Each connection gets a unique id so callers can
        # target / close a specific machine instead of broadcasting to all.
        self._clients: dict = {}              # accessed only from asyncio thread
        self._next_client_id = 0

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, port: int, mdns_name: str) -> None:
        """Start the server in a background daemon thread."""
        if self.is_running:
            return
        self._client_count = 0
        self._clients = {}
        self._thread = threading.Thread(
            target=self._run, args=(port, mdns_name), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Request a clean shutdown from any thread."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def send_to_all(self, message: str) -> None:
        """Broadcast *message* to every connected WS client (thread-safe)."""
        if self._loop and self._clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)

    def send_to_client(self, client_id: str, message: str) -> None:
        """Send *message* to one client by id (thread-safe, no-op if gone)."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_one(client_id, message), self._loop
            )

    def close_client(self, client_id: str) -> None:
        """Close one client's connection by id (thread-safe). The handler's
        ``finally`` block performs bookkeeping and emits client_disconnected."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._close_one(client_id), self._loop)

    # ── Background thread ─────────────────────────────────────────────────

    def _run(self, port: int, mdns_name: str) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve(port, mdns_name))
        except Exception as exc:
            self.log_signal.emit(f"[WS] Unexpected error: {exc}", "#f44747")
        finally:
            self._teardown_mdns()
            self._loop.close()
            self._loop = None
            self._stop_event = None
            self.state_changed.emit(False)
            self.log_signal.emit("[WS] Server stopped.", "#888888")

    async def _serve(self, port: int, mdns_name: str) -> None:
        self._stop_event = asyncio.Event()
        try:
            async with websockets.serve(
                self._handler, "0.0.0.0", port,
                ping_interval=20,  # send a keep-alive ping every 20 s
                # Tolerate up to 20 s for the pong. The ESP32-C3 AT bridge can
                # block its serial channel for up to ~10 s on a failing PC-path
                # AT+WSOPEN (firmware README §12.3), during which a healthy,
                # already-connected machine can't pong. The old 5 s deadline
                # false-dropped such machines, and the resulting reconnect churn
                # made the discovery window resolve with only one live socket —
                # so a second WiFi machine never made it into the chooser.
                ping_timeout=20,
            ):
                ip = _local_ip()
                name_part = f" — advertised as {mdns_name}._ws._tcp.local" if mdns_name else ""
                self.log_signal.emit(
                    f"[WS] Server listening on {ip}:{port}{name_part}",
                    "#4ec94e",
                )
                self.state_changed.emit(True)
                # Run mDNS registration in background so it doesn't delay startup
                if mdns_name:
                    threading.Thread(
                        target=self._setup_mdns, args=(port, mdns_name, ip), daemon=True
                    ).start()
                await self._stop_event.wait()
        except OSError as exc:
            self.log_signal.emit(f"[WS] Failed to bind port {port}: {exc}", "#f44747")

    async def _broadcast(self, message: str) -> None:
        """Send *message* to all connected clients (runs on the asyncio thread)."""
        for ws in list(self._clients.values()):
            try:
                await ws.send(message)
            except Exception:
                pass

    async def _send_one(self, client_id: str, message: str) -> None:
        ws = self._clients.get(client_id)
        if ws is not None:
            try:
                await ws.send(message)
            except Exception:
                pass

    async def _close_one(self, client_id: str) -> None:
        ws = self._clients.get(client_id)
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _handler(self, websocket) -> None:
        addr = websocket.remote_address
        label = f"{addr[0]}:{addr[1]}" if addr else "unknown"
        client_id = str(self._next_client_id)
        self._next_client_id += 1
        self._clients[client_id] = websocket
        self._client_count = len(self._clients)
        self.client_count_changed.emit(self._client_count)
        self.client_connected.emit(client_id)
        self.log_signal.emit(
            f"[WS] Client connected: {label}  "
            f"(id {client_id}, {self._client_count} connected)",
            "#4ec9b0",
        )
        try:
            async for message in websocket:
                self.message_received.emit(client_id, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.pop(client_id, None)
            self._client_count = len(self._clients)
            self.client_count_changed.emit(self._client_count)
            self.client_disconnected.emit(client_id)
            self.log_signal.emit(
                f"[WS] Client disconnected: {label}  ({self._client_count} remaining)",
                "#888888",
            )

    # ── mDNS helpers ─────────────────────────────────────────────────────

    def _setup_mdns(self, port: int, name: str, ip: str) -> None:
        if not name:
            return
        try:
            from zeroconf import Zeroconf, ServiceInfo  # optional dependency
            info = ServiceInfo(
                "_ws._tcp.local.",
                f"{name}._ws._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=port,
                properties={"path": "/"},
                server=f"{name}.local.",
            )
            self._zeroconf = Zeroconf()
            self._zeroconf.register_service(info)
            self._svc_info = info
            self.log_signal.emit(
                f"[WS] mDNS: {name}._ws._tcp.local → {ip}:{port}", "#888888"
            )
        except ImportError:
            self.log_signal.emit(
                "[WS] zeroconf not installed — mDNS advertising skipped.", "#dcdcaa"
            )
        except Exception as exc:
            self.log_signal.emit(f"[WS] mDNS registration failed: {exc}", "#dcdcaa")

    def _teardown_mdns(self) -> None:
        if self._zeroconf and self._svc_info:
            try:
                self._zeroconf.unregister_service(self._svc_info)
                self._zeroconf.close()
            except Exception:
                pass
        self._zeroconf = None
        self._svc_info = None
