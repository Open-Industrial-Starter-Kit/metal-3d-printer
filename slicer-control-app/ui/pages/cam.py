"""
ui/pages/cam.py
Empty CAM page.

The Piccola CNC app this started from had a browser-based CAM subsystem here:
Kiri:Moto for milling and EasyTrace5000 for PCB isolation, each in its own
QWebEngineView, served off a loopback HTTP server (cam_server.py) and driven by
about fifty ui/cam_*.py modules of injected JavaScript. None of it came across:
those engines generate milling toolpaths, and LPBF needs layer-wise laser scan
paths instead, so none of that work transfers.

This is the placeholder in its place. It keeps CamPage's public interface --
``gcode_exported``, ``wire``, ``set_server``, ``export_root``, ``shutdown`` --
so main_window.py's export hand-off, upload queue and staging-directory cleanup
stay wired up and ready for a real slicer to emit into. Nothing emits yet.

To see what the removed version did, read ui/pages/cam.py at the commit that
added slicer-control-app.
"""

import os

from PySide6.QtCore import Qt, QStandardPaths, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from ui.theme import BG
from ui.scale import fs


class CamPage(QWidget):
    """Placeholder for the slicer, holding the interface main_window expects."""

    #: Emitted once an export batch settles: (absolute paths, destination).
    #: Destination is "machine" — the user asked for it to be sent — or "file",
    #: which the page would handle itself and never emit. Nothing emits it yet.
    gcode_exported = Signal(list, str)

    def __init__(self, server=None):
        super().__init__()
        self._server = server
        self._conn = None

        self.setStyleSheet(f"background: {BG};")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addStretch(1)

        self._label = QLabel("Slicer", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #8A8A8E;")
        v.addWidget(self._label)

        v.addStretch(1)
        self._apply_scale()

    def _apply_scale(self, *_):
        f = self._label.font()
        f.setPointSizeF(fs(18))
        self._label.setFont(f)

    # ── interface main_window.py drives ──────────────────────────────────────

    def wire(self, conn) -> None:
        """Hold the connection the way the real page did, so the call site and
        its ordering in main_window stay unchanged."""
        self._conn = conn

    def set_server(self, server) -> None:
        self._server = server

    def export_root(self) -> str:
        """Directory exports are staged in. Public so main_window can verify a
        path lies inside it before deleting.

        Still created, and still the same location: main_window stages the
        Facing program here and deletes it from here once the machine has it,
        and a slicer's output belongs in the same place.
        """
        base = QStandardPaths.writableLocation(
            QStandardPaths.AppLocalDataLocation) or os.path.expanduser("~/.oisk-metal-printer")
        path = os.path.join(base, "cam-exports")
        os.makedirs(path, exist_ok=True)
        return path

    def shutdown(self) -> None:
        """Nothing to tear down: the render processes that needed this are gone."""
        return
