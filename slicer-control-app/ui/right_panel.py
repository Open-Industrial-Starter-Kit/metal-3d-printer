"""
ui/right_panel.py
Right-hand status and coordinate panel showing machine state (IDLE / RUNNING /
PAUSED / ERROR) and XYZAB axis positions with zero buttons (A is the powder
reservoir, B the blade recoater).
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
)
from PySide6.QtCore import Qt

from ui.theme import (
    BG, PANEL, BORDER, WHITE, TXT, TXT2, TXT3,
    IDLE_C, RUN_C, WARN_C, ERR_C,
)
from ui.widgets import lbl
from ui.scale import fs, px, font, bind
from ui import icons
from ui.tooltips import TRANSPARENT_NO_BORDER


class RightPanel(QFrame):
    def __init__(self):
        super().__init__()
        self._connected = False
        self._last_status = "Idle"
        self._status_key = "disconnected"
        self._job_running = False
        self._job_paused = False
        self.setObjectName("rightPanel")

        self._lay = QVBoxLayout(self)

        # ── Status box ──────────────────────────────────────────────────────
        self.status_frame = QFrame()
        self._status_lay = QVBoxLayout(self.status_frame)
        self._status_lay.setAlignment(Qt.AlignCenter)

        self.status_title = QLabel("IDLE")
        self.status_title.setAlignment(Qt.AlignCenter)
        self._status_lay.addWidget(self.status_title)

        # Secondary mode chips (muted, under the state badge). Nothing uses
        # them now that dry run is gone; the row is kept because the status
        # column's spacing is laid out around it.
        self._chips_lay = QHBoxLayout()
        self._chips_lay.setAlignment(Qt.AlignCenter)
        self._status_lay.addLayout(self._chips_lay)

        # Galvo power (CPU-C's K6/K7 relays). Directly under the machine state,
        # because it is the other thing that decides whether a command does
        # anything: with it off the scan head is unpowered and a layer would
        # run with the mirrors dead.
        self._galvo_lbl = QLabel("Galvo —")
        self._galvo_lbl.setAlignment(Qt.AlignCenter)
        self._status_lay.addWidget(self._galvo_lbl)

        self._lay.addWidget(self.status_frame)

        # ── Coordinates ─────────────────────────────────────────────────────
        coords = QFrame()
        coords.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._coords_lay = QVBoxLayout(coords)

        self._axis_vals: dict[str, QLabel] = {}     # job (work) position
        self._axis_mvals: dict[str, QLabel] = {}    # machine position
        self._zero_btns: dict[str, QPushButton] = {}
        self._axis_letters: list[QLabel] = []
        self._axis_rows: list[QHBoxLayout] = []
        self._axis_row_frames: dict[str, QFrame] = {}
        for axis in ["X", "Y", "Z", "A", "B"]:
            frame = QFrame()
            frame.setStyleSheet(TRANSPARENT_NO_BORDER)
            self._axis_row_frames[axis] = frame
            row = QHBoxLayout(frame)
            row.setContentsMargins(0, 0, 0, 0)
            self._axis_rows.append(row)

            al = lbl(axis, sz=22, bold=True)
            self._axis_letters.append(al)
            row.addWidget(al)

            vcol = QVBoxLayout()
            vcol.setSpacing(0)
            pos = lbl("00.00", sz=19, bold=True)
            self._axis_vals[axis] = pos
            vcol.addWidget(pos)
            mpos = lbl("00.00", sz=10, color=TXT3)
            self._axis_mvals[axis] = mpos
            vcol.addWidget(mpos)
            row.addLayout(vcol)

            row.addStretch()

            zb = QPushButton(f"{axis}₀")
            self._zero_btns[axis] = zb
            row.addWidget(zb)
            self._coords_lay.addWidget(frame)

        # The A axis (powder reservoir) stays visible: this machine has one.

        self._lay.addWidget(coords)

        self._metric_cards: list[tuple] = []

        self._sep = QFrame()
        self._sep.setFixedHeight(1)
        self._sep.setStyleSheet(f"background: {BORDER}; border: none;")
        self._lay.addWidget(self._sep)

        # ── Feed rate read-out ──────────────────────────────────────────────
        self._feed_lbl = self._metric_row(self._lay, "feed-rate", "Feed\nRate", "mm/min")

        self._lay.addStretch()

        bind(self, self._apply_scale)
        self.set_status("disconnected")

    def _apply_scale(self, *_):
        self.setFixedWidth(px(210))
        # No left border — it read as a stray vertical line in the gray gap next
        # to the content card; the white-card/gray-panel colour change is enough.
        self.setStyleSheet(f"#rightPanel {{ background: {PANEL}; }}")
        # No top margin: the status tile's top lines up with the top edge of the
        # white content card to the left (which sits flush at the row top).
        self._lay.setContentsMargins(px(12), 0, px(12), px(12))
        self._lay.setSpacing(px(12))

        self.status_frame.setFixedHeight(px(110))
        self._galvo_lbl.setFont(font(11, bold=True))
        self._refresh_galvo()
        self._status_lay.setSpacing(px(4))
        self._status_lay.setContentsMargins(px(8), px(8), px(8), px(8))
        self.status_title.setFont(font(18, bold=True))
        self._chips_lay.setSpacing(px(4))
        for chip in ():
            chip.setFont(font(9, bold=True))
            chip.setStyleSheet(
                f"background: rgba(255, 255, 255, 0.55); color: {TXT2}; "
                f"border-radius: {px(6)}px; border: none; "
                f"padding: {px(2)}px {px(6)}px;"
            )

        self._coords_lay.setSpacing(px(10))
        self._coords_lay.setContentsMargins(0, px(4), 0, px(4))
        for row in self._axis_rows:
            row.setSpacing(px(6))
            row.setContentsMargins(0, 0, 0, 0)
        for al in self._axis_letters:
            al.setFixedWidth(px(22))
        for zb in self._zero_btns.values():
            zb.setFixedSize(px(36), px(36))
            zb.setStyleSheet(f"""
                QPushButton {{
                    background: {WHITE}; border-radius: {px(6)}px;
                    border: 1px solid {BORDER}; font-size: {fs(12)}px;
                }}
                QPushButton:hover {{ background: #E8E8E8; }}
                QPushButton:disabled {{ background: {PANEL}; color: {TXT3}; }}
            """)
        for card, row, icon_lbl, icon_name in self._metric_cards:
            card.setStyleSheet(
                f"#metricCard {{ background: {WHITE}; border-radius: {px(8)}px; "
                f"border: 1px solid {BORDER}; }}"
            )
            row.setContentsMargins(px(10), px(8), px(10), px(8))
            row.setSpacing(px(6))
            if icon_lbl is not None:
                icon_lbl.setPixmap(icons.pixmap(icon_name, px(24), TXT2))

        # Restore the status badge visuals (colours/text) at
        # the new scale — all set colours that lbl()/set_status re-apply on
        # rescale.
        self.set_status(self._status_key)

    def set_galvo_power(self, state) -> None:
        """Show CPU-C's galvo power: True, False, or None for not yet known."""
        self._galvo_power = state
        self._refresh_galvo()

    def _refresh_galvo(self) -> None:
        # The badge behind this changes colour with the machine state, so the
        # text has to stay legible on cyan, lime, amber and red alike: ON and
        # OFF both use the strong text colour and are told apart by the word,
        # not the hue. Only "not known yet" is deliberately faint.
        state = getattr(self, "_galvo_power", None)
        if state is None:
            self._galvo_lbl.setText("Galvo  —")
            colour = TXT3
        elif state:
            self._galvo_lbl.setText("Galvo  ON")
            colour = TXT
        else:
            self._galvo_lbl.setText("Galvo  OFF")
            colour = TXT
        self._galvo_lbl.setStyleSheet(f"color: {colour}; background: transparent;")

    def _metric_row(self, parent_lay, icon_name: str | None, title: str,
                    unit: str | None) -> QLabel:
        """Build a small card showing 'icon  title    <value> unit'. Returns the
        value label. *icon_name* and *unit* may each be None for a read-out
        that has neither."""
        card = QFrame()
        card.setObjectName("metricCard")
        row = QHBoxLayout(card)
        icon_lbl = None
        if icon_name is not None:
            icon_lbl = QLabel()
            icon_lbl.setStyleSheet(TRANSPARENT_NO_BORDER)
            row.addWidget(icon_lbl)
        row.addWidget(lbl(title, sz=10, color=TXT2))
        row.addStretch()
        vcol = QVBoxLayout()
        vcol.setSpacing(0)
        val = lbl("0", sz=14, bold=True, align=Qt.AlignRight | Qt.AlignVCenter)
        vcol.addWidget(val)
        if unit is not None:
            vcol.addWidget(lbl(unit, sz=9, color=TXT3,
                               align=Qt.AlignRight | Qt.AlignVCenter))
        row.addLayout(vcol)
        parent_lay.addWidget(card)
        self._metric_cards.append((card, row, icon_lbl, icon_name))
        return val

    # ── Real-machine wiring ───────────────────────────────────────────────

    # Mobile-app StatusCard colours (App.tsx ~806): the badge shows the raw GRBL
    # status uppercased, coloured green for run / jog / home, amber for hold,
    # red for alarm, grey when offline, and teal for idle / anything else. The
    # label text is always the same dark blue.
    _STATUS_BG = {
        "run": "#6EC98A", "jog": "#6EC98A", "home": "#6EC98A",
        "hold": "#E2C46E",
        "alarm": "#E27E6E",
        "disconnected": "#A0A0A0",
    }
    _STATUS_TEAL = "#6EC9E2"
    _STATUS_TXT = "#3E5E82"

    def wire(self, conn) -> None:
        """Bind to live connection signals."""
        self._conn = conn
        conn.machine_status_changed.connect(self._on_machine_status)
        conn.position_changed.connect(self._update_position)
        conn.feed_rate_changed.connect(lambda v: self._feed_lbl.setText(str(v)))
        conn.galvo_power_changed.connect(self.set_galvo_power)
        self.set_galvo_power(conn.galvo_power)
        # Work-zero an axis (set the job/work coordinate origin) — these are
        # 'set' messages, not commands.
        self._zero_btns["X"].clicked.connect(lambda: conn.set("setJobX", jobX=0.0))
        self._zero_btns["Y"].clicked.connect(lambda: conn.set("setJobY", jobY=0.0))
        self._zero_btns["Z"].clicked.connect(lambda: conn.set("setJobZ", jobZ=0.0))
        self._zero_btns["A"].clicked.connect(lambda: conn.set("setJobA", jobA=0.0))
        self._zero_btns["B"].clicked.connect(lambda: conn.set("setJobB", jobB=0.0))
        # A-axis row and mode chips follow the live machine flags. The signal
        # only carries the flags that changed, so re-read the full accumulated
        # state instead of acting on the delta.
        conn.machine_flags_changed.connect(
            lambda _changed: self._on_flags(conn.flags()))
        conn.transfer_started.connect(self._sync_zero_gates)
        conn.transfer_done.connect(self._sync_zero_gates)
        self._on_flags(conn.flags() or {})

    def set_connected(self, connected: bool) -> None:
        """Show a clear OFFLINE state when no transport is connected."""
        self._connected = connected
        if not connected:
            self.set_status("disconnected")
            pass
        else:
            self._refresh_status_from_state()
        self._sync_zero_gates()

    def _on_machine_status(self, status: str) -> None:
        self._last_status = status
        if self._connected:
            self._refresh_status_from_state()
        self._sync_zero_gates()

    def _refresh_status_from_state(self) -> None:
        # Show the raw machine status verbatim (mobile parity — the badge is the
        # GRBL state itself, not a job-flag-derived label).
        base = str(self._last_status).split(":")[0].strip().lower()
        self.set_status(base or "idle")

    def _on_flags(self, flags: dict) -> None:
        """Follow the job flags."""
        self._job_running = bool(flags.get("jobRunning", self._job_running))
        self._job_paused = bool(flags.get("jobPaused", self._job_paused))
        if self._connected:
            self._refresh_status_from_state()
        self._sync_zero_gates()

    def _sync_zero_gates(self) -> None:
        """Work-zeroing is blocked while a job runs / holds, during homing
        and while a procedure is busy (Android parity, App.tsx ~10438), and
        during a file upload, which takes most of the link."""
        conn = getattr(self, "_conn", None)
        if conn is None:
            return
        base = str(self._last_status).split(":")[0].strip().lower()
        uploading = conn.is_transfer_active
        allowed = (self._connected
                   and not uploading
                   and not conn.flags().get("procedureBusy", False)
                   and base not in ("run", "hold", "home"))
        tip = "Unavailable while a file is uploading" if uploading else ""
        for zb in self._zero_btns.values():
            zb.setEnabled(allowed)
            zb.setToolTip(tip)

    def _update_position(self, pos: dict) -> None:
        # Primary = job (work) position; secondary = machine position.
        self._axis_vals["X"].setText(f"{pos.get('jx', 0.0):.3f}")
        self._axis_vals["Y"].setText(f"{pos.get('jy', 0.0):.3f}")
        self._axis_vals["Z"].setText(f"{pos.get('jz', 0.0):.3f}")
        self._axis_vals["A"].setText(f"{pos.get('ja', 0.0):.3f}")
        self._axis_vals["B"].setText(f"{pos.get('jb', 0.0):.3f}")
        self._axis_mvals["X"].setText(f"{pos.get('mx', 0.0):.3f}")
        self._axis_mvals["Y"].setText(f"{pos.get('my', 0.0):.3f}")
        self._axis_mvals["Z"].setText(f"{pos.get('mz', 0.0):.3f}")
        self._axis_mvals["A"].setText(f"{pos.get('ma', 0.0):.3f}")
        self._axis_mvals["B"].setText(f"{pos.get('mb', 0.0):.3f}")

    def set_status(self, status: str):
        """Show the raw machine status, coloured like the mobile app: green for
        run / jog / home, amber for hold, red for alarm, grey when offline, and
        teal for idle / anything else. The label is the status uppercased."""
        base = str(status).split(":")[0].strip().lower() or "idle"
        self._status_key = base
        bg = self._STATUS_BG.get(base, self._STATUS_TEAL)
        # "home" (GRBL Home state / homing in progress) reads clearer as HOMING.
        label = {"disconnected": "DISCONNECTED",
                 "home": "HOMING"}.get(base, base.upper())
        self.status_frame.setStyleSheet(
            f"background: {bg}; border-radius: {px(10)}px; border: none;")
        self.status_title.setText(label)
        self.status_title.setStyleSheet(
            f"color: {self._STATUS_TXT}; background: transparent;")
