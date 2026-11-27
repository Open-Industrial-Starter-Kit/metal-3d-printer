"""
ui/app_menu.py
Hamburger (sandwich) app menu — a 1:1 port of the mobile app's menu sheet.

Organisation mirrors the Android App.tsx menu exactly:
  • Hardware settings  -> sub-sheet (probe height,
    probe height, steps-per-mm calculator)
  • Machine info       -> sub-sheet (sensors, firmware, usage, job history)
  • Firmware debugging -> sub-sheet (one switch per firmware debug option)
  • Version footer
"""

from PySide6.QtCore import Qt, QSettings, QTimer, QPoint, QProcess
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QAbstractScrollArea, QButtonGroup,
)
from PySide6.QtGui import QCursor

from ui.theme import (WHITE, LIGHT, PANEL, BORDER, TXT, TXT2, TXT3, NAVY, RUN_C,
                      WARN_C)
from ui.widgets import lbl, hline, toggle_switch
from ui.scale import fs, px, bind, autoscale
from ui import icons
from ui.tooltips import TRANSPARENT, TRANSPARENT_NO_BORDER


# Desktop app version (shown in the menu footer — mobile parity).
APP_VERSION = "0.6.3"


# ── Small shared pieces ───────────────────────────────────────────────────────

def _section_title(text: str) -> QLabel:
    return lbl(text, sz=13, bold=True, color=TXT2)


def _row_button(value: str, action: str,
                icon_name: str = "chevron-right") -> QPushButton:
    """Light row button: value on the left, '<action> <icon>' on the right."""
    b = QPushButton()
    b.setCursor(QCursor(Qt.PointingHandCursor))
    b.setObjectName("menuRow")
    h = QHBoxLayout(b)

    val = lbl(value, sz=13, color=TXT)
    val.setAttribute(Qt.WA_TransparentForMouseEvents)
    h.addWidget(val, 1)
    act = lbl(action, sz=12, color=TXT2)
    act.setAttribute(Qt.WA_TransparentForMouseEvents)
    h.addWidget(act)
    ic = QLabel()
    ic.setAttribute(Qt.WA_TransparentForMouseEvents)
    ic.setStyleSheet(TRANSPARENT_NO_BORDER)
    h.addWidget(ic)

    def _style(x):
        x.setFixedHeight(px(42))
        x.setStyleSheet(f"""
            #menuRow {{ background: {LIGHT}; border: none; border-radius: {px(10)}px;
                        text-align: left; }}
            #menuRow:hover {{ background: #E4E4E4; }}
            #menuRow:disabled {{ background: #F4F4F4; }}
        """)
        h.setContentsMargins(px(12), px(4), px(12), px(4))
        h.setSpacing(px(6))
        ic.setPixmap(icons.pixmap(icon_name, px(16), TXT2))

    autoscale(b, _style)
    b._value_lbl = val
    return b


def _switch_row(parent_lay, label: str, checked: bool = False,
                enabled: bool = True, icon_name: str | None = None):
    """Label (+ optional leading icon) + pill switch; returns (label, switch)."""
    row = QHBoxLayout()
    row.setContentsMargins(px(4), 0, px(4), 0)
    row.setSpacing(px(8))
    if icon_name:
        ic = QLabel()
        ic.setStyleSheet(TRANSPARENT_NO_BORDER)
        autoscale(ic, lambda x, n=icon_name: x.setPixmap(
            icons.pixmap(n, px(18), TXT)))
        row.addWidget(ic)
    t = lbl(label, sz=13, color=TXT)
    row.addWidget(t, 1)
    sw = toggle_switch(checked)
    sw.setEnabled(enabled)
    row.addWidget(sw)
    parent_lay.addLayout(row)
    return t, sw


class _Sheet(QDialog):
    """White rounded sheet popup base (like the mobile modal sheets).

    Qt.Popup → clicking anywhere outside dismisses it (no close button needed).
    A QDialog cannot paint a stylesheet background on a translucent window, so
    the rounded white card is an inner QFrame that does the painting — that is
    what produces real filleted corners."""

    def __init__(self, parent=None, width: int = 400):
        super().__init__(parent)
        self._width = width
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = QFrame()
        self._card.setObjectName("sheetCard")
        outer.addWidget(self._card)

        self._lay = QVBoxLayout(self._card)
        # NB: concrete subclasses call bind(self, self._apply_scale) at the end
        # of their own __init__, once their extra widgets exist.

    def _apply_scale(self, *_):
        self.setFixedWidth(px(self._width))
        self._card.setStyleSheet(
            f"#sheetCard {{ background: {WHITE}; border: 1px solid {BORDER}; "
            f"border-radius: {px(16)}px; }}"
        )
        self._lay.setContentsMargins(px(24), px(20), px(24), px(20))
        self._lay.setSpacing(px(12))

    def add_header(self, title: str) -> None:
        self._lay.addWidget(lbl(title, sz=16, bold=True))

    def add_close_header(self, title: str) -> QPushButton:
        """Header row: bold title + a navy round close (X) button — matches the
        mobile sub-sheets, which carry an explicit close affordance."""
        row = QHBoxLayout()
        row.addWidget(lbl(title, sz=16, bold=True), 1)
        close_b = QPushButton()
        close_b.setObjectName("sheetClose")
        close_b.setCursor(QCursor(Qt.PointingHandCursor))
        close_b.clicked.connect(self.accept)

        def _style(x):
            x.setFixedSize(px(36), px(36))
            x.setIcon(icons.icon("close", px(16), "#FFFFFF"))
            x.setStyleSheet(f"""
                #sheetClose {{ background: {NAVY}; border: none;
                               border-radius: {px(10)}px; }}
                #sheetClose:hover {{ background: #2A3A5F; }}
            """)
        autoscale(close_b, _style)
        row.addWidget(close_b)
        self._lay.addLayout(row)
        return close_b

    def showEvent(self, e):
        # Popups position at the cursor by default — center on the parent
        # window instead so the sheets appear like proper dialogs.
        super().showEvent(e)
        host = self.parent()
        if host is not None:
            g = host.window().geometry()
            self.move(g.center().x() - self.width() // 2,
                      g.center().y() - self.height() // 2)


def _style_line_edit(le: QLineEdit) -> None:
    """House style for a QLineEdit. It lived on the WLAN settings sheet as a
    static method until that sheet was removed; it was never WiFi-specific."""
    def _style(x):
        x.setFixedHeight(px(40))
        x.setStyleSheet(
            f"background: {LIGHT}; border: 1px solid {BORDER}; "
            f"border-radius: {px(8)}px; padding: {px(4)}px {px(10)}px; "
            f"font-size: {fs(14)}px; color: {TXT};"
        )
    autoscale(le, _style)


# ── Hardware-settings sub-sheet ───────────────────────────────────────────────

class HardwareSettingsDialog(_Sheet):
    """Sub-sheet: probe height and the steps-per-mm calculator.

    It used to carry two desktop-only switches, Sound and Notifications, which
    are gone. They were the only writers of `soundEnabled` and
    `notificationsEnabled` and there were no readers anywhere in the app: the
    desktop build plays no sound and raises no notification, so both switches
    stored a preference that nothing ever consulted. Every row left here talks
    to the machine.

    The stored keys are left where they are rather than deleted. They are inert
    either way, and clearing settings a user set is not this dialog's business."""

    def __init__(self, conn, parent=None):
        super().__init__(parent, width=420)
        self._conn = conn
        self.add_close_header("Hardware settings")

        connected = conn.is_serial_connected or conn.is_offline_link

        self._probe_h = _row_button(self._probe_height_text(), "Edit",
                                    icon_name="settings")
        self._probe_h.setEnabled(connected)
        self._probe_h.clicked.connect(self._edit_probe_height)
        self._lay.addWidget(self._probe_h)

        self._steps = _row_button("Steps per mm", "Calibrate",
                                  icon_name="settings")
        self._steps.setEnabled(connected)
        self._steps.clicked.connect(self._open_steps_per_mm)
        self._lay.addWidget(self._steps)

        # Controller UF2 mode. Gated on the USB link, not on `connected`: over
        # the machine may not be on this computer's USB at all, so picotool
        # cannot see it however well the app is talking to it — and a button
        # that answers "no device found" to a connected machine is worse than
        # one that is plainly unavailable. Also refused while a job is running
        # or paused: this reboots the board that is streaming the job, and it
        # would walk away from a spinning tool mid-cut.
        self._uf2 = _row_button("Controller UF2 mode", "Reboot",
                                icon_name="settings")
        self._uf2.clicked.connect(self._enter_uf2)
        self._lay.addWidget(self._uf2)
        self._sync_uf2()

        bind(self, self._apply_scale)

        conn.machine_flags_changed.connect(self._on_flags)
        conn.machine_info_changed.connect(self._on_info)
        conn.get("getProbeHeight")

    def done(self, r):
        if not getattr(self, "_disconnected", False):
            self._disconnected = True
            for sig, slot in ((self._conn.machine_flags_changed, self._on_flags),
                              (self._conn.machine_info_changed, self._on_info)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().done(r)

    def _on_flags(self, flags: dict) -> None:
        if "jobRunning" in flags or "jobPaused" in flags:
            self._sync_uf2()

    def _on_info(self, info: dict) -> None:
        if "probeHeight" in info:
            self._probe_h._value_lbl.setText(self._probe_height_text())

    def _probe_height_text(self) -> str:
        v = self._conn.machine_info().get("probeHeight")
        try:
            return f"Probe Height ({float(v):g} mm)"
        except (TypeError, ValueError):
            return "Probe Height"

    def _edit_probe_height(self) -> None:
        from ui.pages.dialogs import TextInputDialog
        host = self.parent()
        cur = self._conn.machine_info().get("probeHeight")
        try:
            initial = f"{float(cur):g}"
        except (TypeError, ValueError):
            initial = "14.0"
        self.accept()

        class _ProbeHeightDialog(TextInputDialog):
            """Same input dialog, taller so the multi-line notice fits."""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # The notice label sits right under the title in the layout.
                self._v.itemAt(1).widget().setWordWrap(True)

            def _apply_scale(self, *_):
                super()._apply_scale()
                self.setFixedSize(px(420), px(300))

        dlg = _ProbeHeightDialog(
            "Probe Height",
            "Thickness of the touch plate used when probing job Z zero. "
            "An incorrect value will offset every Z zero set with the probe. "
            "Measure your touch plate before changing it.",
            initial=initial, suffix="mm", parent=host)
        if dlg.exec():
            try:
                v = float(dlg.text().replace(",", "."))
            except ValueError:
                return
            # Firmware rejects <= 0 (it is a physical plate thickness).
            if 1 <= v <= 50:
                self._conn.set("setProbeHeight", probeHeight=v)

    def _open_steps_per_mm(self) -> None:
        host = self.parent()
        self.accept()
        StepsPerMmDialog(self._conn, parent=host).show()

    # ── Controller UF2 mode ───────────────────────────────────────────────

    def _uf2_blocked_reason(self) -> str:
        """Why the UF2 row is unavailable, or "" when it is available."""
        flags = self._conn.flags()
        if flags.get("jobRunning") or flags.get("jobPaused"):
            return ("A job is running or paused. Rebooting the controller now "
                    "would abandon it mid-job.")
        if not self._conn.is_serial_connected:
            return ("The machine is not connected over USB. picotool talks to "
                    "the controller over the cable, so a cableless connection is "
                    "not enough.")
        return ""

    def _sync_uf2(self) -> None:
        reason = self._uf2_blocked_reason()
        self._uf2.setEnabled(not reason)
        self._uf2.setToolTip(reason)

    def _enter_uf2(self) -> None:
        from ui.pages.dialogs import ConfirmDialog, InfoDialog
        from ui import uf2_boot

        host = self.parent()
        reason = self._uf2_blocked_reason()
        if reason:                       # the row is disabled, but be certain
            InfoDialog("Cannot enter UF2 mode", reason, parent=host,
                       success=False).exec()
            return

        port = self._conn.serial_port()
        self.accept()
        if not ConfirmDialog(
                "Enter UF2 mode?",
                "The controller restarts into UF2 flashing mode and appears as "
                "a drive on this computer.\n\n"
                "It disconnects from the app and STAYS disconnected — there is "
                "no firmware running to answer. The machine comes back only "
                "after you flash a .uf2 onto that drive, or power-cycle it.",
                parent=host).exec():
            return

        # The touch has to hold the port itself, and the discovery loop would
        # otherwise reopen it from its own thread mid-way. Handed back either
        # way: after a successful reboot there is nothing to find until the
        # board is flashed, and after a failed one the machine is still there
        # and the app should reconnect to it.
        self._conn.release_serial()
        if port:
            rebooted, detail = uf2_boot.touch_to_uf2(port)
        else:
            rebooted, detail = False, "the serial port is not known"
        self._conn.resume_serial()

        if rebooted:
            InfoDialog("UF2 mode", uf2_boot.SUCCESS_TEXT, parent=host,
                       success=True).exec()
            return

        # The serial route could not even open the port — something else is
        # holding it. picotool reaches the board through a different USB
        # interface, so it is worth a try when it is installed.
        tool = uf2_boot.find_picotool()
        if tool is None:
            InfoDialog(
                "Could not enter UF2 mode",
                "The app could not open the controller's port to reboot it:\n"
                "%s\n\nClose anything else using %s and try again.\n\n"
                "picotool is the other way in, but it is not installed here. "
                "%s" % (detail, port or "the port", uf2_boot.install_hint()),
                parent=host, success=False).exec()
            return

        proc = QProcess(host)
        proc.setProgram(tool)
        proc.setArguments(list(uf2_boot.REBOOT_ARGS))
        proc.setProcessChannelMode(QProcess.MergedChannels)

        def _finished(code, _status):
            out = bytes(proc.readAll()).decode("utf-8", "replace")
            good, msg = uf2_boot.describe_result(code, out)
            InfoDialog("UF2 mode" if good else "Could not enter UF2 mode",
                       msg, parent=host, success=good).exec()
            proc.deleteLater()

        def _failed(_err):
            InfoDialog("Could not run picotool",
                       "picotool was found at %s but would not start.\n\n%s"
                       % (tool, uf2_boot.install_hint()),
                       parent=host, success=False).exec()
            proc.deleteLater()

        proc.finished.connect(_finished)
        proc.errorOccurred.connect(_failed)
        proc.start()


# ── Steps-per-mm calculator ───────────────────────────────────────────────────

class StepsPerMmDialog(_Sheet):
    """Sub-sheet: recalibrate an axis's steps/mm from a test move.

    The current values are GRBL's $100 / $101 / $102 (X / Y / Z), read with
    `$$` when the sheet opens. The user moves or cuts a known distance,
    measures what the machine actually did, and the new value is

        new = current × commanded / measured

    written back unrounded as `$10N=<new>`. A `$$` re-read after the write is
    what confirms it, rather than trusting the `ok`.

    GRBL only accepts `$` writes in Idle or Alarm, so saving is refused while
    the machine is doing anything else."""

    _AXES = (("X", 100), ("Y", 101), ("Z", 102))

    #: A test move off by more than this is not a steps/mm problem.
    _MECHANICS_WARN_MM = 0.5

    def __init__(self, conn, parent=None):
        super().__init__(parent, width=440)
        self._conn = conn
        self._pending_write: tuple[int, float] | None = None
        self.add_close_header("Steps per mm")

        # Axis selector
        axes = QHBoxLayout()
        self._axis_group = QButtonGroup(self)
        self._axis_btns = []
        for i, (name, _n) in enumerate(self._AXES):
            b = QPushButton(name)
            b.setObjectName("axisBtn")
            b.setCheckable(True)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            self._axis_group.addButton(b, i)
            self._axis_btns.append(b)
            axes.addWidget(b)
        self._axis_btns[0].setChecked(True)
        self._axis_group.idClicked.connect(lambda _i: self._on_axis())
        self._lay.addLayout(axes)

        self._current_lbl = lbl("", sz=13, color=TXT)
        self._lay.addWidget(self._current_lbl)

        self._lay.addWidget(lbl("Commanded distance (mm)", sz=12, color=TXT2))
        self._commanded = QLineEdit()
        self._commanded.setPlaceholderText("e.g. 100")
        _style_line_edit(self._commanded)
        self._lay.addWidget(self._commanded)

        self._lay.addWidget(lbl("Measured distance (mm)", sz=12, color=TXT2))
        self._measured = QLineEdit()
        self._measured.setPlaceholderText("What the machine actually moved")
        _style_line_edit(self._measured)
        self._lay.addWidget(self._measured)

        hint = lbl("Any distance works, but the longer the distance, the more "
                   "accurate the result.", sz=12, color=TXT2)
        hint.setWordWrap(True)
        self._lay.addWidget(hint)

        self._result_lbl = lbl("", sz=14, bold=True, color=TXT)
        self._result_lbl.setWordWrap(True)
        self._lay.addWidget(self._result_lbl)

        self._warn_lbl = lbl("", sz=12, color=WARN_C)
        self._warn_lbl.setWordWrap(True)
        self._warn_lbl.hide()
        self._lay.addWidget(self._warn_lbl)

        self._status_lbl = lbl("", sz=12, color=TXT2)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.hide()
        self._lay.addWidget(self._status_lbl)

        btns = QHBoxLayout()
        btns.addStretch()
        self._save = QPushButton("save")
        self._save.setCursor(QCursor(Qt.PointingHandCursor))
        self._save.clicked.connect(self._write)
        btns.addWidget(self._save)
        btns.addStretch()
        self._lay.addLayout(btns)

        self._commanded.textChanged.connect(self._recalc)
        self._measured.textChanged.connect(self._recalc)

        bind(self, self._apply_scale)

        conn.grbl_setting_received.connect(self._on_setting)
        conn.machine_status_changed.connect(self._on_status)
        conn.machine_flags_changed.connect(self._on_flags)
        conn.request_grbl_settings()
        # The opening read can be lost the same way as a confirm read (see
        # _write); ask again if nothing has arrived.
        QTimer.singleShot(1500, self, self._retry_initial_read)
        self._on_axis()

    def _retry_initial_read(self) -> None:
        if self._current() is None:
            self._conn.request_grbl_settings()

    def done(self, r):
        if not getattr(self, "_disconnected", False):
            self._disconnected = True
            for sig, slot in ((self._conn.grbl_setting_received, self._on_setting),
                              (self._conn.machine_status_changed, self._on_status),
                              (self._conn.machine_flags_changed, self._on_flags)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().done(r)

    def _apply_scale(self, *_):
        super()._apply_scale()
        for b in self._axis_btns:
            b.setFixedHeight(px(38))
            b.setStyleSheet(f"""
                #axisBtn {{ background: {LIGHT}; border: 1px solid {BORDER};
                            border-radius: {px(8)}px; font-size: {fs(14)}px;
                            color: {TXT}; }}
                #axisBtn:hover {{ background: #E4E4E4; }}
                #axisBtn:checked {{ background: {NAVY}; border-color: {NAVY};
                                    color: white; }}
            """)
        self._save.setFixedSize(px(110), px(42))
        self._save.setStyleSheet(f"""
            QPushButton {{ background: {NAVY}; border: none; border-radius: {px(10)}px;
                           color: white; font-size: {fs(14)}px; }}
            QPushButton:hover {{ background: #2A3A5F; }}
            QPushButton:disabled {{ background: #EDEDED; color: {TXT3}; }}
        """)

    # ── State ─────────────────────────────────────────────────────────────

    def _axis(self) -> tuple[str, int]:
        return self._AXES[max(0, self._axis_group.checkedId())]

    def _current(self) -> float | None:
        return self._conn.grbl_settings().get(self._axis()[1])

    @staticmethod
    def _num(le: QLineEdit) -> float | None:
        try:
            v = float(le.text().strip().replace(",", "."))
        except ValueError:
            return None
        return v if v > 0 else None

    def _new_value(self) -> float | None:
        cur = self._current()
        cmd, meas = self._num(self._commanded), self._num(self._measured)
        if cur is None or cmd is None or meas is None:
            return None
        return cur * cmd / meas

    def _blocked_reason(self) -> str:
        """Why saving is unavailable right now, or "" when it is allowed."""
        conn = self._conn
        if not (conn.is_serial_connected or conn.is_offline_link):
            return "The machine is not connected."
        flags = conn.flags()
        if flags.get("jobRunning") or flags.get("jobPaused"):
            return "A job is running or paused."
        status = conn.machine_status()
        if status and status not in ("Idle", "Alarm"):
            return f"The machine is busy ({status}). Wait until it is idle."
        return ""

    # ── Signals ───────────────────────────────────────────────────────────

    def _on_axis(self) -> None:
        self._show_status("")
        self._refresh_current()
        self._recalc()

    def _on_setting(self, n: int, v: float) -> None:
        if self._pending_write and self._pending_write[0] == n:
            _n, written = self._pending_write
            self._pending_write = None
            # `$$` prints 3 decimals, so compare at that resolution.
            if abs(v - written) < 0.0015:
                self._show_status(f"Saved. The machine now reports ${n}={v:.5f}.")
                # The test move was made with the old value; it says nothing
                # about the new one.
                self._measured.clear()
            else:
                self._show_status(
                    f"The machine still reports ${n}={v:.5f}; the change was not "
                    "accepted. See the console for the error.", warn=True)
        if n == self._axis()[1]:
            self._refresh_current()
            self._recalc()

    def _on_status(self, _s: str) -> None:
        self._recalc()

    def _on_flags(self, flags: dict) -> None:
        if "jobRunning" in flags or "jobPaused" in flags:
            self._recalc()

    # ── Display ───────────────────────────────────────────────────────────

    def _refresh_current(self) -> None:
        name, n = self._axis()
        cur = self._current()
        self._current_lbl.setText(
            f"Current {name}: reading from the machine…" if cur is None else
            f"Current {name}: {cur:.5f} steps/mm  (${n})")

    def _recalc(self) -> None:
        new = self._new_value()
        self._result_lbl.setText(
            "" if new is None else f"New value: {new:.5f} steps/mm")

        cmd, meas = self._num(self._commanded), self._num(self._measured)
        off = None if cmd is None or meas is None else abs(cmd - meas)
        if off is not None and off > self._MECHANICS_WARN_MM:
            self._warn_lbl.setText(
                f"⚠ The measured distance is off by {off:g} mm. An error this "
                "large usually points to a mechanical problem rather than wrong "
                "steps/mm — check belts, pulleys, couplers and grub screws, and "
                "look for binding, before saving.")
            self._warn_lbl.show()
        else:
            self._warn_lbl.hide()

        reason = self._blocked_reason()
        self._save.setEnabled(new is not None and not reason
                              and self._pending_write is None)
        self._save.setToolTip(reason)

    def _show_status(self, text: str, warn: bool = False) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color: {WARN_C if warn else TXT2}; background: transparent;")
        self._status_lbl.setVisible(bool(text))

    # ── Write ─────────────────────────────────────────────────────────────

    def _write(self) -> None:
        new = self._new_value()
        reason = self._blocked_reason()
        if new is None or reason:
            if reason:
                self._show_status(reason, warn=True)
            return
        n = self._axis()[1]
        write = (n, new)
        self._pending_write = write
        self._conn.send_raw(f"${n}={new!r}")
        self._show_status("Saving…")
        self._recalc()
        # Don't read back straight away: saving a setting writes the
        # controller's flash, which stalls it briefly, and a `$$` sent into
        # that stall is dropped (the serial link pauses outgoing traffic when
        # the device stops draining). Read after it settles, and retry in case
        # one read is lost anyway.
        for delay in self._CONFIRM_READS_MS:
            QTimer.singleShot(delay, self, lambda w=write: self._confirm_read(w))
        QTimer.singleShot(self._CONFIRM_TIMEOUT_MS, self,
                          lambda w=write: self._write_timed_out(w))

    #: When to re-read `$$` after a write, and when to give up waiting.
    _CONFIRM_READS_MS = (1000, 3000, 6000)
    _CONFIRM_TIMEOUT_MS = 9000

    def _confirm_read(self, write: tuple[int, float]) -> None:
        if self._pending_write is write:
            self._conn.request_grbl_settings()

    def _write_timed_out(self, write: tuple[int, float]) -> None:
        """No `$$` echo came back for the write — don't sit on "Saving…"."""
        if self._pending_write is not write:
            return
        self._pending_write = None
        self._show_status("The machine did not confirm the change. Check the "
                          "console, then reopen this sheet to re-read the "
                          "value.", warn=True)
        self._recalc()

# ── Firmware debugging ────────────────────────────────────────────────────────

class DebuggingDialog(_Sheet):
    """Sub-sheet: the firmware's debug-print switches, one per option.

    Read with getDebuggingSettings when the sheet opens. Each switch sends its
    own setDebugging<Name>, which the firmware answers with a bare success and
    saves in EEPROM — so every change is followed by a re-read, and the
    switches only ever show what the machine reports. The prints themselves
    arrive on USB serial and show up in the Console."""

    _LABELS = {
        "debuggingBase": "Base",
        "debuggingStatus": "Status",
        "debuggingMacroBuffer": "Macro buffer",
        "debuggingSDCard": "SD card",
        "debuggingJson": "JSON",
        "debuggingESP32": "ESP32",
        "debuggingEEPROM": "EEPROM",
        "debuggingJob": "Job",
    }

    def __init__(self, conn, parent=None):
        super().__init__(parent, width=420)
        from connection import DEBUGGING_KEYS
        self._conn = conn
        self.add_close_header("Firmware debugging")

        note = lbl("Debug messages from the machine appear in the Console. "
                   "Settings are saved on the machine and stay after a "
                   "restart. Turn off what you don't need: a lot of output "
                   "slows the USB link.", sz=12, color=TXT2)
        note.setWordWrap(True)
        self._lay.addWidget(note)

        self._switches = {}
        for key in DEBUGGING_KEYS:
            _, sw = _switch_row(self._lay, self._LABELS.get(key, key),
                                enabled=False)
            sw.toggled.connect(lambda on, k=key: self._toggle(k, on))
            self._switches[key] = sw

        self._status_lbl = lbl("Reading from the machine…", sz=12, color=TXT2)
        self._status_lbl.setWordWrap(True)
        self._lay.addWidget(self._status_lbl)

        bind(self, self._apply_scale)

        conn.debugging_settings_received.connect(self._on_settings)
        conn.get("getDebuggingSettings")

    def done(self, r):
        if not getattr(self, "_disconnected", False):
            self._disconnected = True
            try:
                self._conn.debugging_settings_received.disconnect(
                    self._on_settings)
            except (RuntimeError, TypeError):
                pass
        super().done(r)

    def _on_settings(self, settings: dict) -> None:
        connected = self._conn.is_serial_connected or self._conn.is_offline_link
        for key, sw in self._switches.items():
            if key in settings:
                sw.blockSignals(True)
                sw.setChecked(settings[key])
                sw.blockSignals(False)
                sw.setEnabled(connected)
        on = sum(1 for k in self._switches if settings.get(k))
        self._status_lbl.setText(
            "All debugging is off." if on == 0 else
            f"{on} option{'' if on == 1 else 's'} on.")

    def _toggle(self, key: str, on: bool) -> None:
        # Hold the switch until the re-read confirms it; if the machine did
        # not take the change, the re-read puts it back.
        self._switches[key].setEnabled(False)
        self._conn.set("set" + key[0].upper() + key[1:], **{key: bool(on)})
        self._conn.get("getDebuggingSettings")


# ── Machine info popup ────────────────────────────────────────────────────────

class MachineInfoDialog(_Sheet):
    def __init__(self, conn, parent=None):
        super().__init__(parent, width=420)
        self._conn = conn
        self.add_close_header("Machine info")

        info = conn.machine_info()
        flags = conn.flags()

        def _info_row(label: str, value: str, color: str = TXT):
            r = QHBoxLayout()
            r.addWidget(lbl(label, sz=13, color=TXT2))
            r.addStretch()
            v = lbl(value, sz=13, color=color)
            r.addWidget(v)
            self._lay.addLayout(r)
            return v

        _info_row("Firmware version", str(info.get("firmwareVersion", "—")))
        _info_row("Machine ID", str(info.get("deviceId", "—")))

        def _hours(v):
            try:
                return f"{float(v):.1f} h"
            except (TypeError, ValueError):
                return "—"
        _info_row("Power-on hours", _hours(info.get("powerOnHours")))
        _info_row("Running hours", _hours(info.get("runHours")))
        try:
            self._job_count = int(info.get("jobCount") or 0)
        except (TypeError, ValueError):
            self._job_count = 0
        _info_row("Job count", str(self._job_count) if info.get("jobCount")
                  is not None else "—")

        hist = _row_button("Job history", "View")
        hist.setEnabled(self._job_count > 0)
        hist.clicked.connect(self._open_history)
        self._lay.addWidget(hist)

        bind(self, self._apply_scale)

        # Mirror the mobile app: refresh the usage counters on open.
        conn.get("getMachineUsage")

    def _open_history(self) -> None:
        conn, count, host = self._conn, self._job_count, self.parent()
        self.accept()
        JobHistoryDialog(conn, count, parent=host).show()


# ── Job history popup (paged getJobHistory) ───────────────────────────────────

class JobHistoryDialog(_Sheet):
    def __init__(self, conn, job_count: int, parent=None):
        super().__init__(parent, width=460)
        self._conn = conn
        self._loading = False
        self._queue: list = []          # pending range starts
        self._offset = 0
        self._entries: list = []
        self.add_close_header("Job history")

        total = max(0, job_count)
        self._lay.addWidget(lbl(
            f"Select one or more 100-job ranges ({total} job"
            f"{'' if total == 1 else 's'} total)", sz=12, color=TXT2))

        # Range chips
        self._chips: list = []
        chip_host = QWidget()
        self._chip_lay = QHBoxLayout(chip_host)
        self._chip_lay.setContentsMargins(0, 0, 0, 0)
        n_ranges = (total + 99) // 100
        for i in range(n_ranges):
            start = i * 100 + 1
            end = min(start + 99, total)
            c = QPushButton(f"{start}–{end}")
            c.setCheckable(True)
            c.setCursor(QCursor(Qt.PointingHandCursor))
            c.setProperty("rangeStart", start)
            self._chips.append(c)
            self._chip_lay.addWidget(c)
        self._chip_lay.addStretch()
        self._lay.addWidget(chip_host)

        # Actions: select all / load
        self._act = QHBoxLayout()
        self._all_b = QPushButton("Select all")
        self._all_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._all_b.clicked.connect(self._toggle_all)
        self._act.addWidget(self._all_b)
        self._act.addStretch()
        self._load_b = QPushButton("Load")
        self._load_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._load_b.clicked.connect(self._load)
        self._act.addWidget(self._load_b)
        self._lay.addLayout(self._act)

        self._status = lbl("", sz=12, color=TXT2)
        self._lay.addWidget(self._status)

        # Entry list
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.addStretch()
        self._list_host.setStyleSheet(TRANSPARENT)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._list_host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet(TRANSPARENT)
        self._lay.addWidget(self._scroll)

        bind(self, self._apply_scale)

        conn.job_history_received.connect(self._on_page)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(8000)
        self._timeout.timeout.connect(self._on_timeout)

    def _apply_scale(self, *_):
        super()._apply_scale()
        self._chip_lay.setSpacing(px(8))
        for c in self._chips:
            c.setFixedHeight(px(32))
            c.setStyleSheet(f"""
                QPushButton {{ background: {LIGHT}; border: 1px solid {BORDER};
                               border-radius: {px(16)}px; font-size: {fs(12)}px;
                               color: {TXT}; padding: 0 {px(14)}px; }}
                QPushButton:checked {{ background: {NAVY}; color: white;
                                       border: none; }}
            """)
        self._all_b.setFixedHeight(px(36))
        self._all_b.setStyleSheet(f"""
            QPushButton {{ background: {LIGHT}; border: 1px solid {BORDER};
                           border-radius: {px(8)}px; font-size: {fs(12)}px;
                           padding: 0 {px(16)}px; color: {TXT}; }}
            QPushButton:hover {{ background: #E4E4E4; }}
        """)
        self._load_b.setFixedSize(px(110), px(36))
        self._load_b.setStyleSheet(f"""
            QPushButton {{ background: {NAVY}; border: none; border-radius: {px(8)}px;
                           color: white; font-size: {fs(13)}px; }}
            QPushButton:hover {{ background: #2A3A5F; }}
            QPushButton:disabled {{ background: #B9BEC8; }}
        """)
        self._list_lay.setSpacing(px(6))
        self._scroll.setFixedHeight(px(240))

    def done(self, r):
        if not getattr(self, "_disconnected", False):
            self._disconnected = True
            try:
                self._conn.job_history_received.disconnect(self._on_page)
            except (RuntimeError, TypeError):
                pass
        super().done(r)

    def _toggle_all(self) -> None:
        all_on = all(c.isChecked() for c in self._chips) and self._chips
        for c in self._chips:
            c.setChecked(not all_on)
        self._all_b.setText("Clear" if not all_on else "Select all")

    def _load(self) -> None:
        starts = sorted(c.property("rangeStart")
                        for c in self._chips if c.isChecked())
        if not starts or self._loading:
            return
        self._loading = True
        self._load_b.setEnabled(False)
        self._entries = []
        self._clear_list()
        self._queue = list(starts)
        self._offset = 0
        self._request_next()

    def _request_next(self) -> None:
        if not self._queue:
            self._finish()
            return
        start = self._queue[0]
        self._status.setText(
            f"Reading {start}–{start + 99} ({len(self._entries)} jobs)…")
        self._conn.send({"type": "get", "getType": "getJobHistory",
                         "rangeStart": start, "offset": self._offset})
        self._timeout.start()

    def _on_page(self, payload: dict) -> None:
        if not self._loading:
            return
        self._timeout.stop()
        entries = payload.get("jobHistory", [])
        for e in entries:
            if isinstance(e, dict):
                self._entries.append(e)
        returned = int(payload.get("returned", len(entries)) or 0)
        has_more = bool(payload.get("hasMore"))
        if has_more and returned > 0:
            self._offset += returned
        else:
            self._queue.pop(0)
            self._offset = 0
        self._request_next()

    def _on_timeout(self) -> None:
        if self._loading and self._queue:
            start = self._queue[0]
            self._status.setText(f"Failed to read range {start}–{start + 99}.")
            self._finish()

    def _finish(self) -> None:
        self._loading = False
        self._load_b.setEnabled(True)
        self._timeout.stop()
        n = len(self._entries)
        if self._status.text().startswith("Reading") or n:
            self._status.setText(
                "No jobs in the selected range(s)." if n == 0
                else f"{n} job{'' if n == 1 else 's'} loaded.")
        for e in self._entries:
            row = QHBoxLayout()
            name = lbl(str(e.get("fileName", "—")), sz=12, color=TXT)
            row.addWidget(name, 1)
            row.addWidget(lbl(str(e.get("startTime", "—")), sz=11, color=TXT2))
            host = QWidget()
            host.setLayout(row)
            self._list_lay.insertWidget(self._list_lay.count() - 1, host)

    def _clear_list(self) -> None:
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


# ── The app menu drawer ───────────────────────────────────────────────────────

class AppMenuDrawer(QDialog):
    """Left-anchored menu sheet opened by the hamburger button."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        # Qt.Popup → clicking anywhere outside the menu closes it. The white
        # rounded card is an inner QFrame (a translucent QDialog can't paint a
        # stylesheet background itself) — that gives real filleted corners.
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._card = QFrame()
        self._card.setObjectName("menuCard")
        outer.addWidget(self._card)

        self._settings = QSettings("InMachines", "MiniCNC2")

        # The mobile sheet scrolls — wrap the content so the menu stays usable
        # at the smallest window size / scale.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet(TRANSPARENT)
        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.addWidget(self._scroll)

        body = QWidget()
        body.setStyleSheet(TRANSPARENT)
        self._scroll.setWidget(body)
        v = QVBoxLayout(body)
        self._v = v

        connected = conn.is_serial_connected or conn.is_offline_link
        info = conn.machine_info()

        # ── Hardware settings / Machine info sub-sheets ─────────────────────
        hw = _row_button("Hardware settings", "View", icon_name="chevron-right")
        hw.clicked.connect(self._open_hardware)
        v.addWidget(hw)
        mi = _row_button("Machine info", "View", icon_name="chevron-right")
        mi.clicked.connect(self._open_machine_info)
        v.addWidget(mi)
        dbg = _row_button("Firmware debugging", "View",
                          icon_name="chevron-right")
        dbg.clicked.connect(self._open_debugging)
        v.addWidget(dbg)
        v.addWidget(hline())

        # ── Version footer ──────────────────────────────────────────────────
        v.addWidget(lbl(f"Version {APP_VERSION}", sz=12, color=TXT3,
                        align=Qt.AlignCenter))

        bind(self, self._apply_scale)

        # Refresh what the sub-sheets read when the menu opens.
        for g in ("firmwareVersion", "deviceId",
                  "getMachineUsage", "getProbeHeight"):
            conn.get(g)

    def _apply_scale(self, *_):
        self.setFixedWidth(px(360))
        self._card.setStyleSheet(
            f"#menuCard {{ background: {WHITE}; border: 1px solid {BORDER}; "
            f"border-radius: {px(16)}px; }}"
        )
        self._v.setContentsMargins(px(20), px(16), px(20), px(16))
        self._v.setSpacing(px(8))
        if self.isVisible():
            self._fit_height()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_height()

    def _fit_height(self) -> None:
        """Size the sheet to its content, capped to the window so it scrolls only
        when it genuinely can't fit (mobile parity — the menu sheet scrolls)."""
        body = self._scroll.widget()
        body.layout().activate()
        content_h = body.sizeHint().height() + px(2)   # + card border
        cap = px(760)
        host = self.parent()
        if host is not None:
            cap = min(cap, host.window().height() - px(20))
        self.setFixedHeight(max(px(120), min(content_h, cap)))

    def done(self, r):
        super().done(r)

    # ── Live state ───────────────────────────────────────────────────────────

    # ── Actions (exact mobile API calls) ─────────────────────────────────────

    def _host(self):
        """Parent widget for sub-dialogs (the main window, once we close)."""
        return self.parent()

    def _open_hardware(self) -> None:
        conn, host = self._conn, self._host()
        self.accept()
        HardwareSettingsDialog(conn, parent=host).show()

    def _open_machine_info(self) -> None:
        conn, host = self._conn, self._host()
        self.accept()
        MachineInfoDialog(conn, parent=host).show()

    def _open_debugging(self) -> None:
        conn, host = self._conn, self._host()
        self.accept()
        DebuggingDialog(conn, parent=host).show()

    # ── Placement ─────────────────────────────────────────────────────────────

    def open_at(self, global_pos: QPoint) -> None:
        # show() (NOT exec()): exec() would force application modality, which
        # breaks the Qt.Popup close-on-outside-click behaviour.
        self.move(global_pos)
        self.show()
