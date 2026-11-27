"""
ui/bottom_bar.py
Bottom file bar. Empty state shows a centered "No open file"; with a file open
it shows file info (name, measurements, date), feed-rate / spindle-speed
override controls while running, play/stop, and the estimated-remaining-time
progress — matching "Controls - file open - desktop(-1).svg".
"""

import threading
import time

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QPushButton, QLabel,
    QProgressBar, QWidget, QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal

from ui.theme import DARK, WHITE, TXT, TXT2, TXT3, WTXT, LIME
from ui.scale import fs, px, autoscale, bind
from ui import icons
from ui.tooltips import TRANSPARENT

import job_time

#: How long the play button stays unavailable after a pause, in ms. Long enough
#: for the head to finish decelerating and lifting; short enough that a
#: deliberate resume does not feel blocked.
_PAUSE_SETTLE_MS = 3000

# Disabled look for the play / stop job-control circles: a muted circle that
# recedes into the dark bar with a faded glyph, so it's clearly not pressable.
_JOB_CTRL_DISABLED_BG = "#767E8C"
_JOB_CTRL_DISABLED_FG = "#9DA3AE"


#: Lowest spindle speed the override may leave the spindle turning at, in RPM.
#:
#: Mirrors SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM in the firmware's Config.h, which
#: is what actually enforces it: the firmware refuses the override step and
#: replies `spindleRpmBelowMinimum`, so the limit holds for every client, not
#: just this one. The copy here only stops the button FIRST — a limit you meet
#: by pressing a button that does nothing is indistinguishable from a bug.
_SPINDLE_OVERRIDE_MIN_RPM = 3000



class _Planner(QObject):
    """Works out a file's time (job_time.plan_file) off the UI thread: a
    276,000-line file takes a couple of seconds."""
    done = Signal(str, object)          # (file name, cumulative seconds per line)

    def start(self, name: str, path: str, settings: dict) -> None:
        def run():
            try:
                cum = job_time.plan_file(path, settings)
            except (OSError, ValueError, ZeroDivisionError):
                cum = None
            self.done.emit(name, cum)
        threading.Thread(target=run, daemon=True).start()

class _OverrideRow(QWidget):
    """One override control row: label, ±coarse/fine buttons, value, reset."""

    def __init__(self, title: str, send_action, floor_rpm: int | None = None):
        super().__init__()
        self._send = send_action
        #: Reported value below which the ▼ buttons stop (None = no floor, the
        #: feed-rate row). Applies to the spindle row only.
        self._floor = floor_rpm
        self._pct_val = 100        # last override % reported
        self._num_val = 0          # last value reported, parsed (0 = unknown)
        self._dec_btns: list[tuple[QPushButton, int]] = []   # (button, % step)
        self._v = QVBoxLayout(self)

        self._title_lbl = QLabel(title)
        self._v.addWidget(self._title_lbl)

        self._row = QHBoxLayout()
        self._step_btns: list[QPushButton] = []

        def _b(glyph: str, sub: str, action: str,
               step_down: int = 0) -> QPushButton:
            b = QPushButton(f"{glyph}\n{sub}")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: self._send(action))
            self._step_btns.append(b)
            if step_down:
                self._dec_btns.append((b, step_down))
            return b

        self._row.addWidget(_b("◀◀", "-10%", "decreaseCoarse", 10))
        self._row.addWidget(_b("◀", "-1%", "decreaseFine", 1))

        val_col = QVBoxLayout()
        val_col.setSpacing(0)
        self._pct = QLabel("100%")
        self._pct.setAlignment(Qt.AlignCenter)
        self._val = QLabel("—")
        self._val.setAlignment(Qt.AlignCenter)
        val_col.addWidget(self._pct)
        val_col.addWidget(self._val)
        self._vw = QWidget()
        self._vw.setLayout(val_col)
        self._vw.setStyleSheet(TRANSPARENT)
        self._row.addWidget(self._vw)

        self._row.addWidget(_b("▶", "+1%", "increaseFine"))
        self._row.addWidget(_b("▶▶", "+10%", "increaseCoarse"))

        self._reset = QPushButton("↺")
        self._reset.setCursor(Qt.PointingHandCursor)
        self._reset.clicked.connect(lambda: self._send("reset"))
        self._row.addWidget(self._reset)
        self._v.addLayout(self._row)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(px(3))
        self._title_lbl.setStyleSheet(
            f"font-size: {fs(10)}px; color: #C8CCD4; background: transparent;")
        self._row.setSpacing(px(5))
        for b in self._step_btns:
            b.setFixedSize(px(40), px(32))
            b.setStyleSheet(f"""
                QPushButton {{ background: {WHITE}; border: none; border-radius: {px(7)}px;
                               font-size: {fs(8)}px; color: {TXT}; }}
                QPushButton:hover {{ background: #EBEBEB; }}
                QPushButton:pressed {{ background: #DDDDDD; }}
                QPushButton:disabled {{ background: {_JOB_CTRL_DISABLED_BG};
                                        color: {_JOB_CTRL_DISABLED_FG}; }}
            """)
        self._pct.setStyleSheet(
            f"font-size: {fs(14)}px; font-weight: bold; color: {WTXT}; background: transparent;")
        self._val.setStyleSheet(
            f"font-size: {fs(9)}px; color: #C8CCD4; background: transparent;")
        self._vw.setFixedWidth(px(58))
        self._reset.setFixedSize(px(32), px(32))
        self._reset.setStyleSheet(f"""
            QPushButton {{ background: {WHITE}; border: none; border-radius: {px(7)}px;
                           font-size: {fs(14)}px; color: {TXT}; }}
            QPushButton:hover {{ background: #EBEBEB; }}
        """)

    def set_override(self, pct: int) -> None:
        self._pct.setText(f"{pct}%")
        self._pct_val = int(pct)
        self._sync_floor()

    def set_value(self, text: str) -> None:
        self._val.setText(text)
        try:
            self._num_val = int(float(text))
        except (TypeError, ValueError):
            self._num_val = 0
        self._sync_floor()

    def _sync_floor(self) -> None:
        """Stop the ▼ buttons where the firmware stops, and say why.

        GRBL scales the PROGRAMMED speed by the override and reports the
        result — `rpm_overridden` in the status line is what arrives here — so
        the programmed speed is ``rpm * 100 / pct`` and a step down to
        ``pct - n`` lands at ``rpm * (pct - n) / pct``. Kept in integers:
        ``rpm * (pct - n) >= floor * pct`` is the same comparison without the
        rounding.

        Left alone when nothing is turning (the reading is 0 with the spindle
        off) or when no reading has arrived: the firmware refuses the step
        either way, and a button disabled on a guess is worse than one that
        reports a limit when pressed.
        """
        if self._floor is None:
            return
        rpm, pct = self._num_val, self._pct_val
        for btn, step in self._dec_btns:
            allowed = True
            if rpm > 0 and pct > 0:
                allowed = rpm * (pct - step) >= self._floor * pct
            btn.setEnabled(allowed)
            btn.setToolTip("" if allowed else
                           f"The override does not take the spindle below "
                           f"{self._floor} rpm")


class BottomBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("bottomBar")

        self._conn = None
        self._is_running = False
        self._is_paused = False
        self._status_base = ""            # lowercase machine status (gating)
        self._flag_job_running = False
        self._flag_job_paused = False

        # Resume feedback (mobile parity, App.tsx ~7368): resuming a job takes a
        # while before the firmware ramps back to Run. Show a spinner in the play
        # button immediately on press — optimistically, not waiting for the
        # firmware's jobResuming flag (which lags) — reconciled by the real flag /
        # resumeCompleted event, with a safety timeout so it can never stick.
        self._resuming = False
        self._spinner_i = 0
        self._resume_timer = QTimer(self)         # safety: force-clear the spinner
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(15000)
        self._resume_timer.timeout.connect(self._clear_resuming)
        # Pausing is the mirror image, and it exists for a mechanical reason
        # rather than a cosmetic one. `pauseJob` is a feed hold: the machine
        # decelerates and lifts Z clear of the work, and that takes a moment.
        # Play used to flip to ▶ the instant the button was pressed, so the
        # obvious second click — the one an operator makes when nothing seems
        # to have happened yet — resumed the job while the head was still on
        # its way up.
        #
        # Rather than trying to detect "Z is all the way up" (there is no event
        # for it; it would mean watching the position stream and guessing at a
        # target), the button simply refuses to offer a resume for
        # _PAUSE_SETTLE_MS. It is a settling delay, not a state.
        self._pausing = False
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.setInterval(_PAUSE_SETTLE_MS)
        self._pause_timer.timeout.connect(self._clear_pausing)
        self._spinner_timer = QTimer(self)        # animates the play-button spinner
        self._spinner_timer.setInterval(140)
        self._spinner_timer.timeout.connect(self._spin_tick)
        self._est_total_seconds = 0
        # the app's own estimate (job_time.py), next to the machine's
        self._app_cum: list | None = None      # seconds at the end of each line
        self._app_name: str | None = None      # the file _app_cum belongs to
        self._app_pending: str | None = None   # file being planned now
        self._override = 100                   # feed override, %
        self._machine_noted: str | None = None # run whose machine estimate is logged
        self._planner = _Planner()
        self._planner.done.connect(self._on_plan_done)
        self._last_progress: tuple | None = None
        self._bar_active = False          # True while override rows are shown
        self._circle_btns: list[tuple] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(TRANSPARENT)
        outer.addWidget(self._stack)

        self._stack.addWidget(self._build_empty_page())   # index 0
        self._stack.addWidget(self._build_file_page())    # index 1
        self._stack.setCurrentIndex(0)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedHeight(px(122) if self._bar_active else px(96))
        # Fillet the top-left corner so the dark bar meets the content card and
        # side gap with a rounded corner (id selector so it doesn't cascade).
        self.setStyleSheet(
            f"#bottomBar {{ background: {DARK}; border: none; "
            f"border-top-left-radius: {px(12)}px; }}")
        for b, size, font_sz in self._circle_btns:
            b.setFixedSize(px(size), px(size))
            b.setStyleSheet(f"""
                QPushButton {{ background: {WHITE}; border-radius: {px(size) // 2}px;
                               border: none; color: {TXT}; font-size: {fs(font_sz)}px; }}
                QPushButton:hover   {{ background: #F0F0F0; }}
                QPushButton:pressed {{ background: #E0E0E0; }}
                QPushButton:disabled {{ background: {_JOB_CTRL_DISABLED_BG};
                                        color: {_JOB_CTRL_DISABLED_FG}; }}
            """)
        # Centered stop-square sized relative to the circle.
        self._stop_square.setFixedSize(px(18), px(18))
        self._style_stop_square()

    def _style_stop_square(self) -> None:
        # The square is a child of the stop button, so it doesn't follow the
        # button's disabled state on its own — fade it to match.
        on = self._stop_btn.isEnabled()
        self._stop_square.setStyleSheet(
            f"background: {TXT if on else _JOB_CTRL_DISABLED_FG}; "
            f"border-radius: {px(3)}px; border: none;")

        # ── empty + file pages ──────────────────────────────────────────────
        self._empty_lbl.setStyleSheet(
            f"font-size: {fs(22)}px; font-weight: bold; color: {WTXT}; background: transparent;")
        self._filename_lbl.setStyleSheet(
            f"font-size: {fs(20)}px; font-weight: bold; color: {WTXT}; background: transparent;")
        self._chip.setPixmap(icons.pixmap("height-map-ready", px(20), LIME))
        # Smaller and quieter than the file-close ✕ a few pixels to its right,
        # and tinted like the indicator it belongs to, so the two ✕ marks are
        # not read as the same offer.
        self._hm_clear_btn.setFixedSize(px(18), px(18))
        self._hm_clear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {LIME};
                           border-radius: {px(9)}px; color: {LIME};
                           font-size: {fs(9)}px; padding: 0; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.15); }}
        """)
        self._close_btn.setFixedSize(px(26), px(26))
        self._close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1.5px solid {WTXT};
                           border-radius: {px(13)}px; color: {WTXT}; font-size: {fs(11)}px; }}
            QPushButton:hover {{ background: rgba(255,255,255,0.15); }}
        """)
        self._meas_head.setStyleSheet(
            f"font-size: {fs(10)}px; color: #C8CCD4; background: transparent;")
        self._meas_lbl.setStyleSheet(
            f"font-size: {fs(12)}px; color: {WTXT}; background: transparent;")
        self._date_lbl.setStyleSheet(
            f"font-size: {fs(10)}px; color: #C8CCD4; background: transparent;")
        self._et_lbl.setStyleSheet(
            f"font-size: {fs(11)}px; color: #C8CCD4; background: transparent;")
        self._time_lbl.setStyleSheet(
            f"font-size: {fs(11)}px; color: {WTXT}; background: transparent;")
        self._pct_lbl.setStyleSheet(
            f"font-size: {fs(11)}px; color: {WTXT}; background: transparent;")
        self._app_lbl.setStyleSheet(
            f"font-size: {fs(10)}px; color: #C8CCD4; background: transparent;")
        self._progress_bar.setFixedSize(px(280), px(6))
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: #AAB0BC; border-radius: {px(3)}px; border: none; }}
            QProgressBar::chunk {{ background: {LIME}; border-radius: {px(3)}px; }}
        """)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT)
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self._empty_lbl = QLabel("No open file")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._empty_lbl)
        return page

    def _build_file_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(TRANSPARENT)
        row = QHBoxLayout(page)
        row.setContentsMargins(px(12), px(6), px(12), px(6))
        row.setSpacing(px(18))

        # ── File info column ────────────────────────────────────────────────
        info_col = QVBoxLayout()
        info_col.setSpacing(px(2))
        fn_row = QHBoxLayout()
        fn_row.setSpacing(px(10))
        self._filename_lbl = QLabel("—")
        fn_row.addWidget(self._filename_lbl)
        # Height-map-ready indicator (green dots-grid + check, per the design).
        # Hidden unless the machine reports a height map bound to the file.
        self._chip = QLabel()
        self._chip.setStyleSheet(TRANSPARENT)
        self._chip.setToolTip("Height map ready for this file")
        self._chip.hide()
        fn_row.addWidget(self._chip)
        # Attached to that indicator, and shown by exactly the same condition:
        # a button offering to remove something the bar is not currently
        # claiming to have would be a puzzle.
        self._hm_clear_btn = QPushButton("✕")
        self._hm_clear_btn.setCursor(Qt.PointingHandCursor)
        self._hm_clear_btn.setToolTip(
            "Remove the height map from this file")
        self._hm_clear_btn.clicked.connect(self._clear_height_map)
        self._hm_clear_btn.hide()
        fn_row.addWidget(self._hm_clear_btn)
        self._close_btn = QPushButton("✕")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        fn_row.addWidget(self._close_btn)
        fn_row.addStretch()
        info_col.addLayout(fn_row)

        self._meas_head = QLabel("Measurements (L x W x H)")
        info_col.addWidget(self._meas_head)
        self._meas_lbl = QLabel("—")
        info_col.addWidget(self._meas_lbl)
        self._date_lbl = QLabel("")
        info_col.addWidget(self._date_lbl)
        row.addLayout(info_col, 2)

        # ── Override block (visible while running / paused) ─────────────────
        self._override_host = QWidget()
        self._override_host.setStyleSheet(TRANSPARENT)
        ov = QVBoxLayout(self._override_host)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(px(4))
        self._feed_row = _OverrideRow(
            "Feed rate (mm/s)",
            lambda a: self._conn and self._conn.cmd("setFeedRateOverride", action=a))
        self._spindle_row = _OverrideRow(
            "Spindle speed (rpm)",
            lambda a: self._conn and self._conn.cmd("setSpindleOverride", action=a),
            floor_rpm=_SPINDLE_OVERRIDE_MIN_RPM)
        ov.addWidget(self._feed_row)
        ov.addWidget(self._spindle_row)
        self._override_host.hide()
        row.addWidget(self._override_host)

        row.addStretch(1)

        # ── Play / Stop ─────────────────────────────────────────────────────
        self._play_btn = self._circle_btn("▶", 56, 18)
        # The stop "square" is drawn as a centered child frame rather than a "■"
        # glyph, whose font metrics leave it visually off-centre in the circle.
        self._stop_btn = self._circle_btn("", 56, 20)
        stop_lay = QHBoxLayout(self._stop_btn)
        stop_lay.setContentsMargins(0, 0, 0, 0)
        self._stop_square = QFrame()
        self._stop_square.setAttribute(Qt.WA_TransparentForMouseEvents)
        stop_lay.addWidget(self._stop_square, alignment=Qt.AlignCenter)
        row.addWidget(self._play_btn, alignment=Qt.AlignVCenter)
        row.addWidget(self._stop_btn, alignment=Qt.AlignVCenter)

        row.addStretch(1)

        # ── Estimated remaining time + progress ─────────────────────────────
        # Wrapped in a widget so the whole column can be hidden for manual
        # motion (no job → no ETA / progress to show).
        self._time_col = QWidget()
        self._time_col.setStyleSheet(TRANSPARENT)
        tc_col = QVBoxLayout(self._time_col)
        tc_col.setContentsMargins(0, 0, 0, 0)
        tc_col.setSpacing(px(5))
        tc_col.setAlignment(Qt.AlignVCenter)
        head = QHBoxLayout()
        head.setSpacing(px(8))
        self._et_lbl = QLabel("Estimated remaining time")
        head.addWidget(self._et_lbl)
        head.addStretch()
        self._time_lbl = QLabel("—")
        head.addWidget(self._time_lbl)
        tc_col.addLayout(head)

        self._progress_bar = QProgressBar()
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        tc_col.addWidget(self._progress_bar)

        foot = QHBoxLayout()
        # the app's estimate from the file and the machine's $110-$123
        # (job_time.py); the line above is the machine's own
        self._app_lbl = QLabel("")
        self._app_lbl.setToolTip(
            "Worked out by the app from the file, with the machine's speed and "
            "acceleration settings. The time above is the machine's own estimate. "
            "Every finished job is logged with both in job-times.csv.")
        foot.addWidget(self._app_lbl)
        foot.addStretch()
        self._pct_lbl = QLabel("0%")
        foot.addWidget(self._pct_lbl)
        tc_col.addLayout(foot)
        row.addWidget(self._time_col)

        return page

    def _circle_btn(self, glyph: str, size: int, font_sz: int) -> QPushButton:
        b = QPushButton(glyph)
        b.setCursor(Qt.PointingHandCursor)
        # Styling/sizing is applied (and rescaled) in _apply_scale.
        self._circle_btns.append((b, size, font_sz))
        return b

    # ── Display updates ───────────────────────────────────────────────────────

    def update_progress(self, percent: int, time_text: str = "") -> None:
        # Called ~5x/s during a run; skip repaints when nothing changed.
        if (percent, time_text) == self._last_progress:
            return
        self._last_progress = (percent, time_text)
        self._progress_bar.setValue(max(0, min(100, percent)))
        self._pct_lbl.setText(f"{percent}%")
        if time_text:
            self._time_lbl.setText(time_text)

    @staticmethod
    def _fmt_remaining(seconds: float) -> str:
        s = max(0, int(seconds))
        if s >= 3600:
            return f"{s // 3600} h {(s % 3600) // 60} min"
        if s >= 60:
            return f"{s // 60} min"
        return f"{s} s"

    # ── Real-machine wiring ───────────────────────────────────────────────────

    def wire(self, conn) -> None:
        self._conn = conn
        self._play_btn.clicked.connect(lambda: self._on_play_pause(conn))
        self._stop_btn.clicked.connect(lambda: self._on_stop(conn))
        self._close_btn.clicked.connect(lambda: conn.cmd("closeGcodeFile"))

        conn.opened_file_changed.connect(self._on_file_changed)
        conn.file_list_changed.connect(self._on_file_list)
        conn.machine_status_changed.connect(self._on_machine_status)
        conn.serial_state_changed.connect(lambda *_: self._sync_gates())
        conn.ws_connected_changed.connect(lambda *_: self._sync_gates())
        conn.job_progress_changed.connect(self._on_job_progress)
        conn.machine_flags_changed.connect(self._on_flags)
        conn.feed_override_changed.connect(self._feed_row.set_override)
        conn.feed_override_changed.connect(self._on_override)
        conn.spindle_override_changed.connect(self._spindle_row.set_override)
        conn.procedure_completed.connect(self._on_procedure_completed)
        conn.feed_rate_changed.connect(
            lambda v: self._feed_row.set_value(str(v)))
        conn.spindle_rpm_changed.connect(
            lambda v: self._spindle_row.set_value(str(v)))

    def _on_play_pause(self, conn) -> None:
        if self._is_paused:
            # Optimistically enter the resuming state so the spinner shows at
            # once; the firmware's jobResuming flag / resumeCompleted reconcile it.
            self._begin_resuming()
            conn.cmd("resumeJob")
        elif self._is_running:
            self._begin_pausing()
            conn.cmd("pauseJob")
        else:
            self._spindle_check_then_start(conn)

    # ── Pre-start check: the spindle must be stopped ─────────────────────────
    #
    # Starting a job with the spindle already running (left on from the
    # spindle controls) has destroyed the spindle motor driver. So Start first
    # reads the spindle speed fresh from the machine; if it is turning, or
    # stopped too recently to have coasted down, SpindleRunningDialog stops it
    # and counts down before the job may start. The firmware should refuse
    # such a start too (it covers every client); this is the explanation.

    _SPINDLE_READ_WAIT_S = 1.0

    def _spindle_check_then_start(self, conn) -> None:
        if getattr(self, "_spindle_checking", False):
            return
        self._spindle_checking = True
        got = {}

        def on_rpm(rpm) -> None:
            got["rpm"] = rpm

        conn.spindle_rpm_changed.connect(on_rpm)
        conn.get("currentSpindleRPM")
        t0 = time.monotonic()

        def poll() -> None:
            if "rpm" not in got and time.monotonic() - t0 < self._SPINDLE_READ_WAIT_S:
                QTimer.singleShot(50, poll)
                return
            try:
                conn.spindle_rpm_changed.disconnect(on_rpm)
            except (RuntimeError, TypeError):
                pass
            self._spindle_checking = False
            from ui.pages.dialogs import SpindleRunningDialog, SPINDLE_SPINDOWN_S
            # No answer in time: fall back on the last polled speed
            rpm = got.get("rpm", conn.spindle_rpm())
            coasting = conn.spindle_coast_left(SPINDLE_SPINDOWN_S)
            if rpm > 0 or coasting > 0:
                dlg = SpindleRunningDialog(conn, running=rpm > 0,
                                           wait_s=coasting,
                                           parent=self.window())
                if not dlg.exec():
                    return
            self._preflight_then_start(conn)

        QTimer.singleShot(50, poll)

    # ── Pre-start check: does the job fit from the current zero? ─────────────
    #
    # A job whose extents fall outside the machine's travel from where the
    # work zero is set is refused by the firmware's soft-limit check during the
    # job -- cutter in the material. Everything that decides it is known now,
    # so it is checked here, before startJob. See ui/job_fit.py.
    #
    # The two inputs that may need fetching (the firmware's bounds reply and
    # the $13x travel) are asked for together and waited on briefly. A check
    # that cannot be made -- no extents, settings not readable -- does not
    # block the start: it would only be a check that stops working jobs.

    _PREFLIGHT_WAIT_S = 2.5

    def _preflight_then_start(self, conn) -> None:
        if getattr(self, "_preflighting", False):
            return
        self._preflighting = True
        from ui import job_fit
        state = {"reply": None}

        def on_bounds(p) -> None:
            state["reply"] = dict(p)

        conn.opened_file_bounds_received.connect(on_bounds)
        conn.get("getOpenedFileBounds")
        if job_fit.machine_ranges(conn.grbl_settings()) is None:
            conn.request_grbl_settings()
        t0 = time.monotonic()

        def poll() -> None:
            have = (state["reply"] is not None and
                    job_fit.machine_ranges(conn.grbl_settings()) is not None)
            if not have and time.monotonic() - t0 < self._PREFLIGHT_WAIT_S:
                QTimer.singleShot(100, poll)
                return
            try:
                conn.opened_file_bounds_received.disconnect(on_bounds)
            except (RuntimeError, TypeError):
                pass
            self._preflighting = False
            self._finish_preflight(conn, state["reply"])

        QTimer.singleShot(100, poll)

    def _finish_preflight(self, conn, reply) -> None:
        from ui import job_fit
        name = conn.opened_file_name()
        # the local copy has all three axes; the firmware reply only X and Y
        ext = job_fit.extents_from_reply(reply)
        ext.update(job_fit.extents_from_file(conn.cached_file_path(name) if name else None))
        ranges = job_fit.machine_ranges(conn.grbl_settings())
        problems = (job_fit.check(ext, conn.current_position(), ranges)
                    if ext and ranges else [])
        if problems:
            from ui.pages.dialogs import ConfirmDialog
            dlg = ConfirmDialog(
                "Job does not fit",
                "From where the work zero is set now, this job would go past "
                "the machine's travel, and the machine would stop in the "
                "middle of the cut:\n\n" + "\n\n".join(problems),
                parent=self.window(), yes_label="Start anyway", no_label="Cancel")
            if not dlg.exec():
                return
        start_time = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.cmd("startJob", startTime=start_time)

    def _on_stop(self, conn) -> None:
        self._clear_resuming()
        self._clear_pausing()
        conn.cmd("stopJobReset")
        QTimer.singleShot(500, lambda: conn.cmd("grblUnlock"))

    # ── Resume-in-progress feedback ────────────────────────────────────────────

    _SPIN_FRAMES = ("◐", "◓", "◑", "◒")

    def _resume_active(self) -> bool:
        """True while a resume ramp is in progress — from the optimistic local
        flag OR the firmware's jobResuming flag — and a job is still live."""
        flag = bool(self._conn and self._conn.flags().get("jobResuming", False))
        return (self._resuming or flag) and (self._is_running or self._is_paused)

    def _begin_resuming(self) -> None:
        self._resuming = True
        self._resume_timer.start()               # 15 s safety net
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()
        self._refresh_run_pause_state()

    def _clear_resuming(self) -> None:
        self._resuming = False
        self._resume_timer.stop()
        if not self._pause_active():
            self._spinner_timer.stop()
        self._refresh_run_pause_state()
        self._sync_gates()

    def _pause_active(self) -> bool:
        """True during the settling window after a pause was requested. Gated on
        the job still being live so that stopping — or the job simply ending —
        cannot leave the button stuck."""
        return self._pausing and (self._is_running or self._is_paused)

    def _begin_pausing(self) -> None:
        self._pausing = True
        self._pause_timer.start()
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()
        self._refresh_run_pause_state()
        self._sync_gates()

    def _clear_pausing(self) -> None:
        self._pausing = False
        self._pause_timer.stop()
        if not self._resume_active():
            self._spinner_timer.stop()
        self._refresh_run_pause_state()
        self._sync_gates()

    def _spin_tick(self) -> None:
        self._spinner_i = (self._spinner_i + 1) % len(self._SPIN_FRAMES)
        if self._resume_active() or self._pause_active():
            self._play_btn.setText(self._SPIN_FRAMES[self._spinner_i])
        else:
            self._spinner_timer.stop()

    def _on_procedure_completed(self, mtype: str, result: str, payload: dict) -> None:
        if mtype == "resumeCompleted":
            self._clear_resuming()

    def _on_machine_status(self, status: str) -> None:
        base = str(status).split(":")[0].strip()
        base_l = base.lower()
        self._status_base = base_l
        self._refresh_run_pause_state()

    def _refresh_run_pause_state(self) -> None:
        # "jog" counts as running: a continuous jog must be pausable and
        # stoppable from the file bar just like a job (feed-hold works on
        # jog motion too).
        machine_shows_running = self._status_base in ("run", "jog")
        machine_shows_paused = self._status_base == "hold"
        self._is_running = (self._flag_job_running or machine_shows_running) and not machine_shows_paused
        self._is_paused = (not self._is_running) and (self._flag_job_paused or machine_shows_paused)

        # The resume ramp is over once the machine is actually running again, or
        # the job is no longer live — drop the optimistic flag in either case.
        if self._resuming and ((self._is_running and self._status_base == "run")
                               or not (self._is_running or self._is_paused)):
            self._clear_resuming()

        # The settling window ends on its own timer, but a job that stops (or is
        # stopped) mid-window has nothing left to resume into — drop it early
        # rather than leaving the button spinning over a finished job.
        if self._pausing and not (self._is_running or self._is_paused):
            self._clear_pausing()

        if self._resume_active() or self._pause_active():
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
            self._play_btn.setText(self._SPIN_FRAMES[self._spinner_i])
        else:
            if self._spinner_timer.isActive():
                self._spinner_timer.stop()
            if self._is_paused:
                self._play_btn.setText("▶")
            elif self._is_running:
                self._play_btn.setText("❚❚")
            else:
                self._play_btn.setText("▶")
        active = self._is_running or self._is_paused
        # The override rows and the taller bar are for an ACTUAL job only —
        # keyed on the jobRunning/jobPaused flags, which manual motion never
        # sets. Any manual move keeps the compact bar so its size never changes
        # vs idle: a continuous jog reports status "jog", but a discrete tap
        # move reports status "run" (indistinguishable from a job by status
        # alone), so the flags are the only reliable signal. Play/stop stay
        # available (via `active`) so the motion can still be paused/stopped.
        expanded = self._flag_job_running or self._flag_job_paused
        self._override_host.setVisible(expanded)
        self._bar_active = expanded
        self.setFixedHeight(px(122) if expanded else px(96))
        self._sync_gates()
        self._update_visible_page()

    def _refresh_hm_chip(self) -> None:
        """Show the green height-map indicator, and the button that removes the
        map, only when a map is active for the *currently opened file* (mobile
        parity: HeightMapFooterIndicator uses heightMapActiveForOpenedFile,
        per-job — not the global heightMapReady).

        One gate for both: the button undoes what the indicator reports, so
        showing either without the other would be describing a different
        machine state than the one in force."""
        flags = self._conn.flags() if self._conn else {}
        has_file = bool(self._conn and self._conn.opened_file_name())
        active = has_file and bool(
            flags.get("heightMapActiveForOpenedFile", False))
        self._chip.setVisible(active)
        self._hm_clear_btn.setVisible(active)

    def _clear_height_map(self) -> None:
        """Drop the map for this file, after asking.

        Asked, unlike the file-close ✕ beside it, because the two undo very
        different amounts of work: a closed file reopens in a second, while a
        cleared map is minutes of scanning that cannot be recovered — the map
        is volatile and lives only on the machine.

        `clearHeightMap` also disables compensation (README 5.39), so the
        question says that rather than leaving the operator to discover a job
        running uncompensated."""
        from ui.pages.dialogs import ConfirmDialog
        name = (self._conn.opened_file_name() if self._conn else "") or "this file"
        dlg = ConfirmDialog(
            "Remove the height map?",
            f"The map scanned for {name} will be discarded and compensation "
            "turned off. Scanning it again takes as long as the first time.",
            parent=self.window(), yes_label="Remove", no_label="Cancel")
        if not dlg.exec():
            return
        self._conn.cmd("clearHeightMap")

    def _on_flags(self, flags: dict) -> None:
        if "heightMapActiveForOpenedFile" in flags:
            self._refresh_hm_chip()
        if "jobRunning" in flags:
            self._flag_job_running = bool(flags["jobRunning"])
        if "jobPaused" in flags:
            self._flag_job_paused = bool(flags["jobPaused"])
        self._refresh_run_pause_state()
        self._sync_gates()

    def _sync_gates(self) -> None:
        """Android button-mask parity (OpenedFileBar, App.tsx ~2346): start
        only from Idle with a file open and no procedure busy; pause/resume
        while running/held (but not during the resume ramp); stop whenever
        something can be stopped; close only while nothing runs.

        Plus the door: an open lid is what pauses a job in the first place, so
        Play must not offer to resume straight back into it — nor to start one.
        Stop stays live throughout; you must always be able to end a job."""
        conn = self._conn
        if conn is None:
            return
        connected = conn.is_serial_connected or conn.is_ws_active
        flags = conn.flags()
        st = self._status_base
        busy = bool(flags.get("procedureBusy", False))
        running = ((bool(flags.get("jobRunning", False))
                    or st in ("run", "jog"))
                   and st != "hold")
        paused = not running and (bool(flags.get("jobPaused", False))
                                  or st == "hold")
        resuming = (bool(flags.get("jobResuming", False)) or self._resuming) \
            and (running or paused)
        # Settling after a pause — see _PAUSE_SETTLE_MS. Held here rather than
        # only in the glyph so the second click an operator makes when nothing
        # appears to have happened cannot resume into a head that is still
        # lifting.
        pausing = self._pausing and (running or paused)
        has_file = conn.opened_file_name() is not None
        door_blocked = conn.door_blocked()
        can_start = (connected and has_file and not running and not paused
                     and not busy and st == "idle")
        self._play_btn.setEnabled(
            (can_start or (connected and (running or paused)))
            and not resuming and not pausing and not door_blocked)
        self._stop_btn.setEnabled(
            connected and (running or paused or st in ("run", "hold", "jog")
                           or busy))
        self._close_btn.setEnabled(
            connected and has_file and not running and not paused and not busy
            and st != "home")           # can't change the file mid-homing
        self._style_stop_square()       # fade the stop glyph with its button

    # ── the app's own time estimate (job_time.py) ─────────────────────────────

    def _on_override(self, pct) -> None:
        try:
            self._override = max(1, int(pct))
        except (TypeError, ValueError):
            self._override = 100

    def _start_plan(self, name: str) -> None:
        if not self._conn or self._app_pending == name:
            return
        path = self._conn.cached_file_path(name)
        if not path:
            return                      # no local copy (yet): tried again on progress
        self._app_pending = name
        self._planner.start(name, path, self._conn.grbl_settings())

    def _on_plan_done(self, name: str, cum) -> None:
        if self._app_pending == name:
            self._app_pending = None
        opened = self._conn.opened_file_name() if self._conn else None
        if name != opened or not cum:
            return
        self._app_cum, self._app_name = cum, name
        if not (self._is_running or self._is_paused):
            self._app_lbl.setText("app estimate " + self._fmt_remaining(cum[-1]))

    def _app_progress(self, jp: dict) -> None:
        name = self._conn.opened_file_name() if self._conn else None
        if not name:
            return
        if self._app_name != name:
            self._start_plan(name)
            return
        left = job_time.remaining(self._app_cum, int(jp.get("currentLine", 0) or 0),
                                  self._override)
        self._app_lbl.setText("app estimate " + self._fmt_remaining(left))
        # the machine's own estimate of the whole run, once early on, for the log
        eta = int(jp.get("etaSeconds", 0) or 0)
        if (self._machine_noted != name and eta > 0
                and int(jp.get("percent", 0) or 0) >= 2):
            ran = self._conn.job_elapsed()
            if ran is not None:
                job_time.note_machine_estimate(name, ran + eta)
                self._machine_noted = name

    def _on_job_progress(self, jp: dict) -> None:
        if self._is_running or self._is_paused:
            self._app_progress(jp)
        pct = int(jp.get("percent", 0))
        remaining = ""
        # Prefer the firmware's live estimate (accounts for pauses and feed
        # overrides); fall back to scaling the file's estimated duration.
        eta = int(jp.get("etaSeconds", 0) or 0)
        if eta > 0:
            remaining = self._fmt_remaining(eta)
        elif self._est_total_seconds > 0:
            remaining = self._fmt_remaining(
                self._est_total_seconds * (1 - pct / 100.0))
        self.update_progress(pct, remaining)

    def _on_file_changed(self, filename) -> None:
        has_file = bool(filename)
        if self._resuming:                   # opening/closing a file ends any resume
            self._clear_resuming()
        self._machine_noted = None
        if self._app_name != filename:
            self._app_cum, self._app_name = None, None
            self._app_lbl.setText("")
        if has_file:
            self._filename_lbl.setText(str(filename))
            self._refresh_file_meta(str(filename))
            self._start_plan(str(filename))
        else:
            self.update_progress(0, "—")
            self._est_total_seconds = 0
        # Recompute the run/pause state (and thus the bar height / overrides,
        # which depend on has_file) now that the opened file is known — the
        # status message may have arrived before the file was set.
        self._refresh_run_pause_state()
        self._refresh_hm_chip()          # per-file height-map indicator

    def _update_visible_page(self) -> None:
        """Show the control page (play/stop/close) whenever a file is open OR
        the machine is active, so a jog / manual move can be paused and
        stopped from the bar even with no file loaded."""
        has_file = bool(self._conn and self._conn.opened_file_name())
        active = self._is_running or self._is_paused
        show_controls = has_file or active
        self._stack.setCurrentIndex(1 if show_controls else 0)
        # The close (✕) button closes the opened file — hide it entirely while
        # there is no file (a bare jog / manual move has nothing to close). The
        # "Measurements" header/value and the ETA / progress column only make
        # sense for an opened file (a real job), so hide them for manual motion.
        self._close_btn.setVisible(has_file)
        self._meas_head.setVisible(has_file)
        self._time_col.setVisible(has_file)
        if show_controls and not has_file:
            # Active with no job file open (jog / manual move): show the
            # play/stop controls but leave the file-info labels blank (the
            # override rows stay hidden so the bar keeps its compact height).
            self._filename_lbl.setText("")
            self._meas_lbl.setText("")
            self._date_lbl.setText("")
            self._chip.setVisible(False)

    def _on_file_list(self, _files: list) -> None:
        # The opened-file notification can beat the first fileList reply (on
        # a fresh connect the status poll carries openedFileName first), so
        # the metadata read in _on_file_changed comes up empty. Re-read it
        # whenever the list (and thus the metadata) arrives or changes.
        name = self._conn.opened_file_name() if self._conn else None
        if name:
            self._refresh_file_meta(name)

    def _refresh_file_meta(self, filename: str) -> None:
        meta = self._conn.file_meta(filename) if self._conn else {}
        try:
            w = float(meta.get("fileWidth", 0))
            d = float(meta.get("fileDepth", 0))
            h = float(meta.get("fileHeight", 0))
            self._meas_lbl.setText(f"{w:g} × {d:g} × {h:g} mm" if (w or d or h) else "—")
        except (TypeError, ValueError):
            self._meas_lbl.setText("—")
        date = str(meta.get("fileDate", "")).split(" ")[0].replace("-", ".")
        self._date_lbl.setText(date)
        try:
            self._est_total_seconds = int(meta.get("estimatedDurationSeconds", 0) or 0)
        except (TypeError, ValueError):
            self._est_total_seconds = 0
        if self._est_total_seconds and not (self._is_running or self._is_paused):
            self._time_lbl.setText(self._fmt_remaining(self._est_total_seconds))
