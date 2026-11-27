"""
ui/machine_chooser.py
Modal "Select a machine" chooser shown when two or more CNC machines announce
themselves over WiFi within the discovery window (mobile-app parity).

Matches the desktop design language (frameless white card, gray rounded rows).
The list updates live as machines finish identifying themselves, and the dialog
closes automatically once a machine is selected (here or elsewhere) or the
discovery is dismissed (e.g. serial took over).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel,
    QScrollArea, QFrame,
)

from ui.theme import WHITE, LIGHT, BORDER, PANEL, TXT, TXT2, TXT3, IDLE_C
from ui.widgets import lbl, rounded_dialog_card
from ui.scale import fs, px, bind
from ui.tooltips import TRANSPARENT


class _MachineRow(QFrame):
    """One tappable machine entry: label + deviceId/firmware + chevron."""

    clicked = Signal(str)        # emits the machine's clientId

    def __init__(self, machine: dict, parent=None):
        super().__init__(parent)
        self._client_id = str(machine.get("clientId", ""))
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("machineRow")
        self._h = QHBoxLayout(self)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        self._col = col
        name = machine.get("label") or machine.get("name") or "Unknown machine"
        col.addWidget(lbl(name, sz=16, bold=True, color=TXT))
        sub_bits = []
        if machine.get("deviceId"):
            sub_bits.append(str(machine["deviceId"]))
        if machine.get("firmware"):
            sub_bits.append(f"fw {machine['firmware']}")
        sub = " · ".join(sub_bits) if sub_bits else "Identifying…"
        self._sub = lbl(sub, sz=12, color=TXT2)
        col.addWidget(self._sub)
        self._h.addLayout(col, 1)

        self._chev = QLabel("›")
        self._chev.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self._h.addWidget(self._chev)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setStyleSheet(f"""
            #machineRow {{
                background: {PANEL}; border: none; border-radius: {px(12)}px;
            }}
            #machineRow:hover {{ background: #D2D2D2; }}
        """)
        self._h.setContentsMargins(px(18), px(12), px(18), px(12))
        self._h.setSpacing(px(14))
        self._col.setSpacing(px(3))
        self._sub.setStyleSheet(
            f"background: transparent; color: {TXT2}; font-size: {fs(12)}px;")
        self._chev.setStyleSheet(
            f"background: transparent; font-size: {fs(26)}px; color: {TXT3};")

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self._client_id)
        super().mouseReleaseEvent(e)


class MachineChooserDialog(QDialog):
    """Frameless white-card chooser bound to a Connection.

    Created with the initial choice list; ``update_choices`` refreshes it live.
    Selecting a row calls ``conn.select_machine(clientId)``; the dialog closes on
    ``ws_connected_changed(True)`` or ``machine_chooser_dismissed``."""

    def __init__(self, conn, choices: list, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._closing = False
        self._n = 0
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)

        self._card = rounded_dialog_card(self)
        self._v = QVBoxLayout(self._card)

        self._title = lbl("Select a machine", sz=22, color=TXT,
                          align=Qt.AlignCenter)
        self._v.addWidget(self._title)
        self._subtitle = lbl("", sz=14, color=TXT2, align=Qt.AlignCenter)
        self._v.addWidget(self._subtitle)
        self._v.addSpacing(px(6))

        # Scrollable list host (handles many machines gracefully).
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(TRANSPARENT)
        self._list_host = QWidget()
        self._list_host.setStyleSheet(TRANSPARENT)
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.addStretch()
        self._scroll.setWidget(self._list_host)
        self._v.addWidget(self._scroll, 1)

        self.update_choices(choices)

        bind(self, self._apply_scale)

        # Close automatically when selection resolves elsewhere or is dismissed.
        conn.ws_connected_changed.connect(self._on_ws_connected)
        conn.machine_chooser_dismissed.connect(self._close_dialog)

    def _apply_scale(self, *_):
        self.setFixedWidth(px(480))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(16)}px; }}")
        self._v.setContentsMargins(px(36), px(30), px(36), px(30))
        self._v.setSpacing(px(10))
        self._list.setSpacing(px(10))
        # Size the scroll area to the content up to a sensible cap.
        self._scroll.setFixedHeight(min(max(self._n, 1), 5) * px(66))

    # ── live updates ──────────────────────────────────────────────────────

    def update_choices(self, choices: list) -> None:
        choices = list(choices or [])
        n = len(choices)
        self._n = n
        self._subtitle.setText(
            f"{n} machines are available" if n != 1
            else "1 machine is available")
        # Clear existing rows (keep the trailing stretch).
        while self._list.count() > 1:
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for m in choices:
            row = _MachineRow(m)
            row.clicked.connect(self._on_row_clicked)
            self._list.insertWidget(self._list.count() - 1, row)
        # Size the scroll area to the content up to a sensible cap.
        self._scroll.setFixedHeight(min(max(n, 1), 5) * px(66))

    # ── selection / close ─────────────────────────────────────────────────

    def _on_row_clicked(self, client_id: str) -> None:
        # Drive selection through the Connection; the resulting
        # ws_connected_changed(True) closes us via _on_ws_connected.
        self._conn.select_machine(client_id)

    def _on_ws_connected(self, connected: bool) -> None:
        if connected:
            self._close_dialog()

    def _close_dialog(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.accept()

    def done(self, r) -> None:
        # Detach from the Connection on any close path (accept/reject/Esc).
        for sig, slot in ((self._conn.ws_connected_changed, self._on_ws_connected),
                          (self._conn.machine_chooser_dismissed, self._close_dialog)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(r)

    # ── center over parent ────────────────────────────────────────────────

    def showEvent(self, e) -> None:
        super().showEvent(e)
        par = self.parentWidget()
        if par is not None:
            geo = par.window().geometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)
