"""
main_window.py
MainWindow: assembles all UI components into the application shell and wires
them to the central Connection hub.
"""

import job_time
import os

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFrame, QApplication, QMessageBox,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, QPoint

from ui.theme import BG, DARK, PANEL, global_ss
from ui.scale import mgr, px, font, bind
from ui.widgets import lbl
from ui.top_bar import TopBar
from ui.splash import SplashOverlay
from ui.homing import HomingOverlay
from ui.side_menu import SideMenu
from ui.right_panel import RightPanel
from ui.bottom_bar import BottomBar
from ui.pages.controls import ControlsPage
from ui.pages.file_manager import FileManagerPage
from ui.pages.live_preview import LivePreviewPage
from ui.pages.console import ConsolePage
from ui.pages.cam import CamPage
from connection import Connection
from ui.tooltips import TRANSPARENT


class AdaptiveStack(QStackedWidget):
    """QStackedWidget whose size hints follow the CURRENT page only, so one
    wide page (e.g. Controls) doesn't force scrollbars on narrower pages."""

    def __init__(self):
        super().__init__()
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w else super().minimumSizeHint()


class StatusBar(QFrame):
    """Simple bottom strip shown when no file is loaded."""

    def __init__(self, text: str = "No open file"):
        super().__init__()
        self.setStyleSheet(f"background: {DARK}; border: none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lbl = lbl(text, sz=13, color="#C0C0C0", align=Qt.AlignCenter)
        lay.addWidget(self._lbl)
        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedHeight(px(42))
        self._lbl.setFont(font(13))

    def update_text(self, text: str) -> None:
        self._lbl.setText(text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OISK Metal 3D Printer")
        self.resize(1420, 820)
        # Minimum size keeps the full controls page (incl. the step-size area)
        # visible without scrolling, and lets the responsive file manager fit
        # without hiding rows or the upload button.
        # 896 (controls min) + 105 (side menu) + 210 (status column) ≈ 1215.
        # Lowered + scale-aware so the whole shell fits on small / scaled laptop
        # displays; the responsive factor (ui/scale.py) compresses the content,
        # page minimums, and chrome to fit rather than forcing scrollbars.
        self.setMinimumSize(1010, 640)
        self.setStyleSheet(global_ss() + f"QMainWindow {{ background: {BG}; }}")

        # Debounce live resizes so dragging the window doesn't thrash the tree.
        self._rescale_timer = QTimer(self)
        self._rescale_timer.setSingleShot(True)
        self._rescale_timer.setInterval(50)
        self._rescale_timer.timeout.connect(self._recompute_scale)

        central = QWidget()
        central.setObjectName("centralHost")
        # Gray behind the chrome so the top bar / right panel can round the
        # corners that face the content and reveal gray (a convex fillet)
        # instead of a white notch. The pages paint their own white/BG on top,
        # so only the rounded-corner slivers show this gray.
        central.setStyleSheet(f"#centralHost {{ background: {PANEL}; }}")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────────────────
        self._top_bar = TopBar()
        self._top_bar.hamburger_toggled.connect(self._toggle_sidebar)
        root.addWidget(self._top_bar)

        # ── Body: side menu (full height) + right column ──────────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._side_menu = SideMenu()
        self._side_menu.page_changed.connect(self._on_page_change)
        body.addWidget(self._side_menu)

        # Page stack  (index order must match SideMenu nav items)
        # 0 = File Manager  |  1 = Controls (default)  |  2 = CAM
        # 3 = Live Preview  |  4 = Console (kept last so it stays bottom)
        self._stack = AdaptiveStack()
        # Transparent so the scroll viewport's white rounded background shows
        # through — the content then reads as a white rounded card on the gray.
        self._stack.setStyleSheet(TRANSPARENT)

        self._file_page     = FileManagerPage()
        self._controls_page = ControlsPage()
        self._preview_page  = LivePreviewPage()
        self._console_page  = ConsolePage()
        self._cam_page      = CamPage()
        self._cam_page.gcode_exported.connect(self._on_cam_export)
        self._controls_page.facing_job_ready.connect(self._on_facing_job)

        for page in [self._file_page, self._controls_page, self._cam_page,
                     self._preview_page, self._console_page]:
            self._stack.addWidget(page)
        self._stack.setCurrentIndex(1)

        self._right_panel = RightPanel()

        # Only the page stack lives inside the scroll area, so shrinking the
        # window scrolls the page content while the side menu (left) and the
        # status column (right) stay pinned and always visible.
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidget(self._stack)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._style_scroll()

        # Bottom file bar: always visible (shows "No open file" until a file is
        # loaded). Kept for status text only.
        self._status_bar = StatusBar("Connecting…")
        self._status_bar.hide()
        self._bottom_bar = BottomBar()

        # The dark file bar is inset 12px on the left (over a BG-coloured wrap) so
        # the same white gap that separates the side menu from the content panels
        # also appears to the left of the file bar.
        bottom_wrap = QWidget()
        bottom_wrap.setStyleSheet(f"background: {BG};")
        bw_lay = QHBoxLayout(bottom_wrap)
        bw_lay.setContentsMargins(12, 0, 0, 0)
        bw_lay.setSpacing(0)
        bw_lay.addWidget(self._bottom_bar)

        # Upper row: scrollable page content + pinned status column.
        upper_row = QHBoxLayout()
        upper_row.setContentsMargins(0, 0, 0, 0)
        upper_row.setSpacing(0)
        upper_row.addWidget(scroll, 1)
        upper_row.addWidget(self._right_panel)

        # Middle column = upper row above, bottom bar below. The side menu sits
        # to its left and therefore spans the full height of both.
        mid_col = QVBoxLayout()
        mid_col.setContentsMargins(0, 0, 0, 0)
        mid_col.setSpacing(0)
        mid_col.addLayout(upper_row, 1)
        mid_col.addWidget(bottom_wrap)
        body.addLayout(mid_col, stretch=1)

        root.addLayout(body, stretch=1)

        # ── Connection hub ────────────────────────────────────────────────────
        self._serial_ok = False
        self._offline_ok = False
        self._conn = Connection(self)

        # Wire all pages / panels to the live connection
        self._controls_page.wire(self._conn)
        self._file_page.wire(self._conn)
        self._preview_page.wire(self._conn)
        self._console_page.wire(self._conn)
        self._cam_page.wire(self._conn)
        self._right_panel.wire(self._conn)
        self._bottom_bar.wire(self._conn)

        # Pending CAM exports waiting for the single transfer slot (see
        # _on_cam_export). Connected after _file_page.wire() so FileManagerPage's
        # own transfer_done handler runs first and releases the slot.
        self._cam_queue: list[str] = []
        self._cam_uploading: str | None = None
        self._conn.transfer_done.connect(self._on_transfer_done_for_cam)

        # Machine name (from the API) → app title in the top bar
        self._conn.machine_info_changed.connect(
            lambda info: self._top_bar.set_title(info.get("machineName", "")))

        # Serial state → update status bar text
        self._conn.serial_state_changed.connect(self._on_serial_state)

        # The developer offline mode's simulated link, which also counts as
        # "connected" for the splash and the status dot.
        self._conn.link_changed.connect(self._on_link_changed)

        # File open/close → show bottom bar or status bar
        self._conn.opened_file_changed.connect(self._on_file_changed)
        # Opening a file → modal "Opening file" popup until it is ready
        self._file_page.file_opening.connect(self._show_opening_file)
        # An upload takes most of the link: commands sent meanwhile get lost,
        # so the Controls and Console tabs are locked until it ends.
        self._conn.transfer_started.connect(lambda: self._lock_for_upload(True))
        self._conn.transfer_done.connect(lambda: self._lock_for_upload(False))

        # "Searching for connection…" splash overlay (mobile-app parity). Created
        # last so it sits on top of every other child in the central widget.
        self._splash = SplashOverlay(central)
        # Developer offline mode: the splash's "Develop offline" button simulates
        # a connected, idle machine so the UI can be used without any hardware.
        self._splash.offline_requested.connect(self._enter_offline)

        # Initial homing flow (mobile-app parity): shown once a machine connects
        # and reports it has not homed since power-on. Sits above the splash so
        # it takes over the moment the splash finishes fading.
        self._homing_done = False
        self._homing_in_progress = False
        self._homing_failed = False
        self._rotary_connected = False
        self._rotary_known = False
        self._homing_retry = QTimer(self)
        self._homing_retry.setSingleShot(True)
        self._homing_retry.setInterval(150)
        self._homing_retry.timeout.connect(self._sync_homing)

        self._homing = HomingOverlay(central)
        self._homing.start_requested.connect(self._start_homing)
        self._homing.cancel_requested.connect(self._cancel_homing)
        self._conn.machine_flags_changed.connect(self._on_machine_flags)
        # Re-evaluate the homing overlay on status changes too, so it appears
        # promptly once the machine starts reporting after a (re)connect.
        self._conn.machine_status_changed.connect(lambda *_: self._sync_homing())
        self._conn.procedure_completed.connect(self._on_procedure_completed)
        # The app menu is unreachable while a job runs/holds, during homing
        # or while a procedure is busy (Android parity, App.tsx ~10449).
        self._menu_status = ""
        self._conn.machine_status_changed.connect(self._on_menu_gate_status)
        self._conn.machine_flags_changed.connect(
            lambda *_: self._sync_menu_gate())
        # Popup when the firmware refuses a move/jog (soft limit, not homed…),
        # mirroring the mobile app. One at a time — the connection already
        # throttles repeats from a held jog.
        self._notice_dlg = None
        self._conn.motion_error.connect(self._show_notice)
        # A rejected upload gets the same treatment as a refused move: the
        # firmware names the cause, so the user is told it rather than being
        # left with a progress bar that stopped for no stated reason.
        self._conn.transfer_error.connect(self._show_notice)
        # "Job finished" box when a job runs to its end (not when stopped).
        self._done_dlg = None
        self._conn.job_finished.connect(self._show_job_done)
        # every finished job's real time next to the estimates (job_time.py)
        self._conn.job_finished.connect(job_time.log_run)

        # Re-apply the scaled base stylesheet + scroll metrics whenever the
        # responsive factor changes (the rest of the tree listens too).
        mgr.scaleChanged.connect(self._apply_chrome_scale)

        # Start serial auto-connect + WS server, then reflect initial (disconnected) state
        self._conn.start()
        self._update_connection()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._push_viewport_width()
        self._layout_overlays()
        # Recompute the responsive scale from the new window size (debounced).
        self._rescale_timer.start()

    def _recompute_scale(self):
        mgr.set_from_size(self.width(), self.height())

    def _apply_chrome_scale(self, *_):
        self.setStyleSheet(global_ss() + f"QMainWindow {{ background: {BG}; }}")
        self._style_scroll()

    def _style_scroll(self):
        scroll = getattr(self, "_scroll", None)
        if scroll is None:
            return
        bar = px(11)
        rad = px(5)
        mn = px(30)
        r = px(12)
        # Transparent frame + gray page behind → the white rounded viewport
        # reads as a content card floating on the gray, its corners filleted.
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:horizontal {{ background: transparent; height: {bar}px; margin: 0; }}
            QScrollBar:vertical   {{ background: transparent; width: {bar}px;  margin: 0; }}
            QScrollBar::handle:horizontal,
            QScrollBar::handle:vertical {{ background: #B0B0B0; border-radius: {rad}px; min-width: {mn}px; min-height: {mn}px; }}
            QScrollBar::handle:hover {{ background: #909090; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        """)
        # Round only the top corners: the bottom meets the file bar, so squaring
        # it removes the stray gray/white triangle at the bottom-left corner.
        scroll.viewport().setStyleSheet(
            f"background: {BG}; "
            f"border-top-left-radius: {r}px; border-top-right-radius: {r}px; "
            f"border-bottom-left-radius: 0px; border-bottom-right-radius: 0px;")

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._push_viewport_width)
        self._layout_overlays()

    def _layout_overlays(self):
        # Keep the splash + homing overlays covering the whole client area (incl.
        # the top bar). The homing overlay sits above the splash.
        central = self.centralWidget()
        if central is None:
            return
        splash = getattr(self, "_splash", None)
        if splash is not None:
            splash.setGeometry(central.rect())
            splash.raise_()
        homing = getattr(self, "_homing", None)
        if homing is not None:
            homing.setGeometry(central.rect())
            if not homing.isHidden():
                homing.raise_()

    def _push_viewport_width(self):
        # Let the file manager compact its columns to the visible width
        # (before its own minimum width would force horizontal scrolling).
        self._file_page.set_available_width(self._scroll.viewport().width())

    def _show_notice(self, title: str, message: str) -> None:
        """Firmware rejection popup — a refused move/jog, or a failed upload."""
        # Don't stack popups: if one is already up, let it stand (the next
        # rejection within the throttle window is dropped anyway).
        if self._notice_dlg is not None:
            return
        from ui.pages.dialogs import InfoDialog
        # A guided flow (e.g. a height-map / point-cloud scan) runs its own
        # application-modal exec() loop and sits raised on top. Parenting the
        # notice to that dialog — not the main window — stacks it ABOVE the
        # flow so it stays clickable; otherwise it opens behind the modal scan
        # dialog, visible but unreachable (a soft-limit hit mid-scan can't be
        # dismissed). Fall back to the main window when no flow is open.
        top = QApplication.activeModalWidget() or self
        dlg = InfoDialog(title, message, parent=top, success=False)
        self._notice_dlg = dlg
        dlg.finished.connect(lambda *_: setattr(self, "_notice_dlg", None))
        dlg.open()
        dlg.raise_()
        dlg.activateWindow()

    def _show_job_done(self, file_name: str, seconds: float) -> None:
        """Tell the operator a job has run to its end."""
        from ui.pages.dialogs import InfoDialog
        if self._done_dlg is not None:
            self._done_dlg.close()
        mins, secs = divmod(int(round(seconds)), 60)
        hours, mins = divmod(mins, 60)
        took = (f"{hours} h {mins} min" if hours
                else f"{mins} min {secs} s" if mins else f"{secs} s")
        name = file_name or "The job"
        top = QApplication.activeModalWidget() or self
        dlg = InfoDialog("Job finished", f"{name} is done.\nRun time: {took}",
                         parent=top, success=True)
        self._done_dlg = dlg
        dlg.finished.connect(lambda *_: setattr(self, "_done_dlg", None))
        dlg.open()
        dlg.raise_()
        dlg.activateWindow()
        # Flash the taskbar entry when the app is behind other windows.
        QApplication.alert(self)

    def _on_menu_gate_status(self, status: str) -> None:
        self._menu_status = str(status).split(":")[0].strip().lower()
        self._sync_menu_gate()

    def _menu_available(self) -> bool:
        return (not self._conn.flags().get("procedureBusy", False)
                and self._menu_status not in ("run", "hold", "home"))

    def _sync_menu_gate(self) -> None:
        self._top_bar.set_menu_enabled(self._menu_available())

    def _toggle_sidebar(self):
        # The hamburger opens the app menu (mobile-app parity): machine name,
        # connectivity, preferences, machine info.
        if not self._menu_available():
            return
        from ui.app_menu import AppMenuDrawer
        menu = AppMenuDrawer(self._conn, parent=self)
        pos = self.mapToGlobal(self._top_bar.geometry().bottomLeft())
        menu.open_at(pos + QPoint(8, 4))

    def _on_page_change(self, idx: int):
        self._stack.setCurrentIndex(idx)

    # ── Facing job → machine hand-off ────────────────────────────────────────

    def _on_facing_job(self, gcode: str, name: str) -> None:
        """Stage a generated facing program and send it like a CAM export.

        The facing dialog (ui/pages/facing_dialog.py) produces G-code, not a
        file: the staging folder and the transfer queue both live here, and
        routing the result through _on_cam_export means a facing job gets
        everything a CAM export already gets -- the "no machine / transfer
        busy" explanation, the name-collision prompt, the progress row on the
        Files page, and the staged copy being deleted once the machine has it.

        It is staged in the CAM export root for that last point specifically:
        _discard_staged_export only ever unlinks inside that folder, so a
        facing file written anywhere else would be uploaded and then left
        behind for ever.
        """
        if not gcode:
            return
        root = self._cam_page.export_root()
        path = os.path.join(root, os.path.basename(name))
        try:
            with open(path, "w") as fh:
                fh.write(gcode)
        except OSError as e:
            QMessageBox.warning(
                self, "Facing",
                f"The facing program could not be written to disk, so there is "
                f"nothing to send.\n\n{path}\n\n{e}")
            return
        self._on_cam_export([path], "machine")

    # ── CAM export → machine hand-off ────────────────────────────────────────

    def _on_cam_export(self, paths: list, destination: str = "machine") -> None:
        """A CAM tool exported G-code the user asked to send to the machine.

        The destination is chosen *before* exporting now — EasyTrace's export
        button is replaced by explicit "Save as File" / "Save to Machine" buttons
        (ui/cam_export.py) — so there is nothing left to confirm here. CamPage
        handles the save-to-disk case itself and only emits for "machine".

        EasyTrace has no M6 tool-change support, so a PCB job legitimately comes
        out as several files (isolation, drill, cutout). They are uploaded one
        after another rather than raced through the single-transfer path.
        """
        if not paths or destination != "machine":
            return

        if not self._file_page.can_upload():
            # Only reachable by asking for the machine while it is unavailable,
            # so say why instead of failing silently. The files are already on
            # disk, so nothing is lost.
            QMessageBox.information(
                self, "Slicer export",
                f"{len(paths)} G-code file(s) exported, but they cannot be sent "
                "right now.\n\nNo machine is connected, or a transfer is already "
                "running. The files are saved here and can be uploaded later "
                f"from the File Manager:\n\n{os.path.dirname(paths[0])}")
            return

        self._cam_queue = list(paths)
        self._side_menu.set_page(0)      # show the transfer progress
        self._on_page_change(0)
        self._start_next_cam_upload()

    #: Retry window for the next queued upload. FileManagerPage deliberately
    #: holds its upload row ~600 ms after transfer_done so the finished progress
    #: bar stays readable, and can_upload() is False for that whole time. Rather
    #: than couple to that number, poll until the slot frees.
    _CAM_RETRY_MS = 200
    _CAM_RETRY_MAX = 25          # ~5 s, comfortably past the cosmetic delay

    def _start_next_cam_upload(self, tries: int = 0) -> None:
        if not self._cam_queue:
            return
        path = self._cam_queue[0]
        started = self._file_page.upload_file(path)
        if started:
            self._cam_queue.pop(0)
            self._cam_uploading = path
            return
        if started is None:
            # The name was already on the machine and the user cancelled:
            # skip this file (it stays on disk) and go on with the rest.
            self._cam_queue.pop(0)
            QTimer.singleShot(0, self._start_next_cam_upload)
            return

        # Transfer slot still busy — most often FileManagerPage's own row
        # lingering after the previous file. Keep the entry queued and retry.
        if tries < self._CAM_RETRY_MAX:
            QTimer.singleShot(
                self._CAM_RETRY_MS,
                lambda: self._start_next_cam_upload(tries + 1))
            return

        remaining = len(self._cam_queue)
        self._cam_queue.clear()
        QMessageBox.warning(
            self, "Slicer export",
            f"Could not send {remaining} remaining file(s) — the machine stayed "
            "busy.\n\nThey are still on disk and can be uploaded from the File "
            f"Manager:\n\n{os.path.dirname(path)}")

    def _on_transfer_done_for_cam(self) -> None:
        """Clean up the staged file, then advance the CAM upload queue.

        ``transfer_done`` also fires for ordinary user uploads and for aborted
        or failed ones, hence both guards: only a file this queue put on the
        wire, and only one that actually completed, is removed.

        Deferred by one event-loop turn so FileManagerPage's own handler has
        already cleared its upload row and freed the transfer slot.
        """
        done, self._cam_uploading = self._cam_uploading, None
        if done is not None:
            if self._conn.last_transfer_ok:
                self._discard_staged_export(done)
            else:
                # Aborted or errored: keep the file so the user can retry from
                # the File Manager instead of having to re-export.
                self._cam_queue.clear()

        if self._cam_queue:
            QTimer.singleShot(0, self._start_next_cam_upload)

    def _discard_staged_export(self, path: str) -> None:
        """Delete a CAM export once it is safely on the machine.

        The staging copy under cam-exports/ exists only to hand a real file to
        the transfer path; keeping it after a successful upload just accumulates
        duplicates of what the machine already has. Live Preview is unaffected —
        Connection.start_transfer keeps its own copy in the g-code cache.

        Containment check: only ever unlink inside the export root, so a bug in
        the queue can never delete something else.
        """
        root = os.path.realpath(self._cam_page.export_root())
        target = os.path.realpath(path)
        if os.path.commonpath([target, root]) != root:
            return
        try:
            os.remove(target)
        except OSError:
            return
        # Drop the per-export folder once its last file is gone.
        folder = os.path.dirname(target)
        if os.path.realpath(folder) != root:
            try:
                os.rmdir(folder)
            except OSError:
                pass    # not empty yet — later files still to upload

    def _on_serial_state(self, connected: bool, msg: str) -> None:
        self._serial_ok = connected
        # Re-read the offline side from the connection rather than trusting the
        # cached flag: a serial takeover ends the developer offline mode, whose
        # simulated link would otherwise linger and keep the app looking
        # connected after the cable is pulled.
        self._offline_ok = self._conn.is_offline_link
        if connected:
            self._status_bar.update_text(f"Serial connected: {msg}")
        self._update_connection()

    def _on_link_changed(self, connected: bool) -> None:
        self._offline_ok = connected
        self._update_connection()

    # ── Initial homing flow ─────────────────────────────────────────────────

    def _on_machine_flags(self, flags: dict) -> None:
        self._sync_homing()

    def _on_procedure_completed(self, mtype: str, result: str, _payload: dict) -> None:
        if mtype != "homingCompleted":
            return
        self._homing_in_progress = False
        if result == "success":
            self._homing_done = True
            self._homing_failed = False
        else:
            self._homing_failed = True
        self._sync_homing()

    def _start_homing(self) -> None:
        if self._rotary_connected:
            return
        self._homing_in_progress = True
        self._homing_failed = False
        self._conn.home("grblHoming")     # marks HOMING status while it runs
        self._sync_homing()

    def _cancel_homing(self) -> None:
        self._conn.cmd("stopJobReset")
        self._homing_in_progress = False
        self._homing_failed = False
        self._sync_homing()

    def _reset_homing(self) -> None:
        """A dropped connection clears homing state so it re-prompts on reconnect."""
        self._homing_done = False
        self._homing_in_progress = False
        self._homing_failed = False
        self._rotary_known = False
        self._rotary_connected = False
        self._homing_retry.stop()
        self._homing.hide()

    def _sync_homing(self) -> None:
        """Show/update the homing overlay based on the current machine state.

        Reads the accumulated flag snapshot (conn.flags()) rather than the
        machine_flags_changed delta, so a reconnect to a machine in the same
        state still evaluates correctly (mobile-app parity: HomingModal shows
        while wsConnected && rotaryAxisStatusKnown && !homingDone)."""
        connected = self._serial_ok or self._offline_ok
        flags = self._conn.flags()
        # rotary status is "known" once the machine has reported it at least
        # once this session (the flag key is present in the snapshot).
        rotary_known = "rotaryAxisConnected" in flags
        self._rotary_connected = bool(flags.get("rotaryAxisConnected", False))
        # Homing is done for this power cycle once the machine reports it, or
        # once a homingCompleted success arrived (sticky until disconnect).
        if flags.get("homedSincePowerOn") is True:
            self._homing_done = True

        should_show = connected and rotary_known and not self._homing_done
        if not should_show:
            self._homing.hide()
            # Still connected and not yet homed, but the deciding rotary flag
            # hasn't arrived (or was missed): keep polling so the overlay
            # appears as soon as the machine reports — this closes the
            # intermittent "homing popup never came up" gap on (re)connect.
            if connected and not self._homing_done and not rotary_known:
                self._homing_retry.start()
            return
        # Wait for the connection splash to finish fading before taking over.
        if not self._splash.isHidden():
            self._homing_retry.start()
            return
        self._homing.set_state(
            in_progress=self._homing_in_progress,
            failed=self._homing_failed,
            rotary_connected=self._rotary_connected,
            machine_name=self._conn.machine_info().get("machineName", ""))
        self._layout_overlays()
        self._homing.show_overlay()

    def _update_connection(self) -> None:
        """Reflect overall transport connectivity in the dot, status badge, and bar."""
        connected = self._serial_ok or self._offline_ok
        self._top_bar.set_connected(connected)
        self._right_panel.set_connected(connected)
        if connected:
            self._splash.finish()
            self._sync_homing()
        else:
            self._status_bar.update_text("Disconnected — searching for machine…")
            self._splash.search()
            self._reset_homing()

    def _enter_offline(self) -> None:
        """Simulate a connected machine (no hardware) for UI development."""
        self._conn.enter_offline_mode()
        self._top_bar.set_title("Offline")

    def _on_file_changed(self, filename) -> None:
        # The bar stays visible at all times; BottomBar updates its own filename /
        # chip / close state. Nothing to toggle here.
        pass

    #: Side-menu / page-stack indices locked during an upload
    _UPLOAD_LOCKED_PAGES = (1, 4)            # Controls, Console

    def _lock_for_upload(self, locked: bool) -> None:
        self._side_menu.set_locked(
            self._UPLOAD_LOCKED_PAGES if locked else (),
            "Unavailable while a file is uploading")
        if locked and self._stack.currentIndex() in self._UPLOAD_LOCKED_PAGES:
            self._side_menu.set_page(0)      # File Manager shows the progress
            self._on_page_change(0)

    def _show_opening_file(self, name: str) -> None:
        from ui.pages.dialogs import OpeningFileDialog
        OpeningFileDialog(name, self._conn, self._preview_page, parent=self).open()

    def closeEvent(self, event):
        self._conn.stop()
        # Tool popups first: they poll the machine on their own timers, and a
        # timer that outlives the connection it is polling fires into a dead
        # object during teardown.
        self._preview_page.shutdown()
        self._cam_page.shutdown()
        super().closeEvent(event)
