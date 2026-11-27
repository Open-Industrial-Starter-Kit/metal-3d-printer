"""
ui/right_panel.py
Right-hand status and coordinate panel showing machine state (IDLE / RUNNING /
PAUSED / ERROR) and XYZA axis positions with zero buttons.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
)
from PySide6.QtCore import Qt

from ui.theme import (
    BG, PANEL, BORDER, WHITE, TXT2, TXT3,
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

        # Secondary mode chips (muted, under the state badge) — hidden until
        # the matching machine flag is reported.
        self._chips_lay = QHBoxLayout()
        self._chips_lay.setAlignment(Qt.AlignCenter)
        self._chip_dry = QLabel("DRY RUN")
        for chip in (self._chip_dry,):
            chip.setAlignment(Qt.AlignCenter)
            chip.hide()
            self._chips_lay.addWidget(chip)
        self._status_lay.addLayout(self._chips_lay)

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
        for axis in ["X", "Y", "Z", "A"]:
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

        # ── Door / lid state ────────────────────────────────────────────────
        # Directly under the axis rows: the door gates motion (see
        # controls._door_blocked), so it belongs with the read-outs the
        # operator is already watching. Hidden until the machine reports a
        # door sensor at all.
        self._metric_cards: list[tuple] = []
        self._door_open: bool | None = None
        self._door_lbl = self._metric_row(self._lay, None, "Door", None)
        self._door_card = self._metric_cards[-1][0]
        self._door_card.hide()

        # Whether a housing is registered on the machine. Not a live sensor —
        # a stored setting that only reaches us from getHousingSettings — but
        # it decides whether the *firmware* acts on the door at all, so it
        # belongs next to the door reading.
        self._housing: bool | None = None
        self._housing_lbl = self._metric_row(self._lay, None, "Housing", None)
        self._housing_card = self._metric_cards[-1][0]
        self._housing_card.hide()

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
        self._status_lay.setSpacing(px(4))
        self._status_lay.setContentsMargins(px(8), px(8), px(8), px(8))
        self.status_title.setFont(font(18, bold=True))
        self._chips_lay.setSpacing(px(4))
        for chip in (self._chip_dry,):
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

        # Restore the status badge and door/housing visuals (colours/text) at
        # the new scale — all set colours that lbl()/set_status re-apply on
        # rescale.
        self.set_status(self._status_key)
        self._refresh_door()
        self._refresh_housing()

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
        # Work-zero an axis (set the job/work coordinate origin) — these are
        # 'set' messages, not commands.
        self._zero_btns["X"].clicked.connect(lambda: conn.set("setJobX", jobX=0.0))
        self._zero_btns["Y"].clicked.connect(lambda: conn.set("setJobY", jobY=0.0))
        self._zero_btns["Z"].clicked.connect(lambda: conn.set("setJobZ", jobZ=0.0))
        self._zero_btns["A"].clicked.connect(lambda: conn.set("setJobA", jobA=0.0))
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
            # Neither is knowable while no machine is talking to us; the flag
            # cache is dropped on the next link anyway.
            self._door_open = None
            self._housing = None
        else:
            self._refresh_status_from_state()
        self._refresh_door()
        self._refresh_housing()
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
        """Toggle the DRY RUN chip and the door row."""
        dry = bool(flags.get("dryRunEnabled", False) or flags.get("dryRunActive", False))
        self._job_running = bool(flags.get("jobRunning", self._job_running))
        self._job_paused = bool(flags.get("jobPaused", self._job_paused))
        self._chip_dry.setVisible(dry)
        # doorOpen rides on every status poll; housingInstalled only arrives
        # from getHousingSettings (asked for on connect).
        if "doorOpen" in flags:
            self._door_open = bool(flags["doorOpen"])
        if "housingInstalled" in flags:
            self._housing = bool(flags["housingInstalled"])
        self._refresh_door()
        self._refresh_housing()
        if self._connected:
            self._refresh_status_from_state()
        self._sync_zero_gates()

    def _refresh_door(self) -> None:
        """Show OPEN / CLOSED for the lid, or hide the row when the machine
        never reported a door sensor (and while offline).

        Amber rather than red for OPEN: it locks motion out
        (controls._door_blocked) but it is a caution, not a fault."""
        known = self._door_open is not None and self._connected
        self._door_card.setVisible(known)
        if not known:
            return
        self._door_lbl.setText("OPEN" if self._door_open else "CLOSED")
        color = WARN_C if self._door_open else RUN_C
        # lbl() pins its colour inline and re-applies it on every scale change,
        # so this runs from _apply_scale too (after lbl()'s own styler).
        self._door_lbl.setStyleSheet(f"color: {color}; background: transparent;")

    def _refresh_housing(self) -> None:
        """Show whether a housing is registered, or hide the row until the
        machine has told us (and while offline).

        Muted rather than amber for NONE: a machine with no housing is a valid
        configuration, not a warning — it only means the firmware's own door
        interlock is inactive. "NONE" and not "NOT INSTALLED" because the
        latter does not fit the panel at any scale."""
        known = self._housing is not None and self._connected
        self._housing_card.setVisible(known)
        if not known:
            return
        self._housing_lbl.setText("INSTALLED" if self._housing else "NONE")
        color = RUN_C if self._housing else TXT3
        self._housing_lbl.setStyleSheet(f"color: {color}; background: transparent;")

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
        self._axis_mvals["X"].setText(f"{pos.get('mx', 0.0):.3f}")
        self._axis_mvals["Y"].setText(f"{pos.get('my', 0.0):.3f}")
        self._axis_mvals["Z"].setText(f"{pos.get('mz', 0.0):.3f}")
        self._axis_mvals["A"].setText(f"{pos.get('ma', 0.0):.3f}")

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
