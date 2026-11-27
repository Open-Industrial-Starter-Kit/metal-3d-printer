"""
ui/pages/console.py
Console page matching the Piccola CNC desktop design: a dark log panel with search,
download and clear actions on top, colored TX/RX log lines, and a command
input bar at the bottom for raw JSON or G-code passthrough.
"""

import html
import json
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QLineEdit, QPushButton, QLabel, QFileDialog, QCheckBox,
)
from PySide6.QtCore import Qt, QEvent, QSettings

from ui.theme import BG, WHITE, LIGHT, BORDER, TXT, TXT2
from ui.widgets import lbl
from ui.scale import fs, px, bind
from ui import icons
from ui.tooltips import POPUPS, TRANSPARENT, TRANSPARENT_NO_BORDER

_CONSOLE_BG = "#3B4252"        # dark slate console area (design)
_MAX_LINES = 2000
_MAX_HISTORY = 50              # ↑/↓ command-recall depth (persisted)

# Console log kinds, matching the mobile app's filter (Console - filter options
# design): Sent / Received / Info / Warning / Error. Each connection log colour
# maps to one of these kinds so the filter can hide whole categories.
_KIND_ORDER = ["tx", "rx", "info", "warn", "error"]
_KIND_LABELS = {"tx": "Sent", "rx": "Received", "info": "Info",
                "warn": "Warning", "error": "Error"}
_KIND_LABEL_COLORS = {"tx": "#4ec9b0", "rx": "#ce9178", "info": "#C8CCD4",
                      "warn": "#dcdcaa", "error": "#f44747"}
# Connection.C_* log colours → kind (see connection.py).
_COLOR_KIND = {
    "#4ec9b0": "tx", "#c586c0": "tx",      # C_TX, C_WS_TX
    "#ce9178": "rx", "#9cdcfe": "rx",      # C_RX, C_WS_RX
    "#888888": "info",                     # C_INFO
    "#dcdcaa": "warn",                     # C_WARN
    "#f44747": "error",                    # C_ERR
}


def _kind_of(color: str) -> str:
    """Classify a log line's css colour into a filter kind."""
    return _COLOR_KIND.get((color or "").lower(), "info")


class ConsolePage(QWidget):
    def __init__(self):
        super().__init__()
        self._conn = None
        self._entries: list[tuple[str, str, str]] = []   # (time, text, color)
        self._enabled_kinds: dict[str, bool] = {k: True for k in _KIND_ORDER}
        self._filter_popup: QFrame | None = None
        self._prefs = QSettings("InMachines", "MiniCNC2")
        # Command history (bash-style ↑/↓ recall), persisted across sessions.
        hist = self._prefs.value("console/history", [])
        if isinstance(hist, str):        # QSettings may collapse 1-item lists
            hist = [hist]
        self._history: list[str] = [str(h) for h in (hist or [])][-_MAX_HISTORY:]
        self._hist_idx: int | None = None   # None = not navigating
        self._hist_draft = ""               # text typed before pressing ↑
        # Follow the newest line automatically (toggleable, persisted).
        self._autoscroll = self._prefs.value(
            "console/autoScroll", True, type=bool)
        self.setStyleSheet(TRANSPARENT)

        self._root = QVBoxLayout(self)

        # ── Dark console panel ───────────────────────────────────────────────
        self._panel = QFrame()
        self._panel.setObjectName("consolePanel")
        self._pl = QVBoxLayout(self._panel)

        # Top row: search + download + clear
        self._top = QHBoxLayout()

        self._search_wrap = QFrame()
        self._search_wrap.setObjectName("conSearch")
        self._sw = QHBoxLayout(self._search_wrap)
        si = QLabel()
        self._search_icon = si
        si.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._sw.addWidget(si)
        self._search = QLineEdit()
        self._search.textChanged.connect(self._rerender)
        self._sw.addWidget(self._search)
        self._top.addWidget(self._search_wrap, 1)

        self._chips: list[tuple[QPushButton, str]] = []

        def _chip(icon_name: str, text: str) -> QPushButton:
            b = QPushButton(f"  {text}")
            b.setCursor(Qt.PointingHandCursor)
            self._chips.append((b, icon_name))
            return b

        self._download_btn = _chip("download", "Download")
        self._download_btn.clicked.connect(self._save_logs)
        self._top.addWidget(self._download_btn)
        self._clear_btn = _chip("reset", "Clear")
        self._clear_btn.clicked.connect(self._confirm_clear)
        self._top.addWidget(self._clear_btn)
        self._filter_btn = _chip("filter", "Filter")
        self._filter_btn.clicked.connect(self._open_filter)
        self._top.addWidget(self._filter_btn)
        self._scroll_btn = _chip("sync", "Auto scroll")
        self._scroll_btn.clicked.connect(self._toggle_autoscroll)
        self._top.addWidget(self._scroll_btn)
        self._pl.addLayout(self._top)

        # Log area
        self._browser = QTextBrowser()
        self._browser.setReadOnly(True)
        self._browser.setOpenLinks(False)
        self._pl.addWidget(self._browser, 1)

        # Command input row (inside the dark panel, like the design)
        self._cmd_wrap = QFrame()
        self._cmd_wrap.setObjectName("conCmd")
        self._cw = QHBoxLayout(self._cmd_wrap)
        self._cmd_input = QLineEdit()
        self._cmd_input.setPlaceholderText("Type Command")
        self._cmd_input.installEventFilter(self)   # ↑/↓ history recall
        self._cw.addWidget(self._cmd_input)
        self._send_btn = QPushButton("➤")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._cw.addWidget(self._send_btn)
        self._pl.addWidget(self._cmd_wrap)

        self._root.addWidget(self._panel, 1)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._root.setContentsMargins(px(12), px(12), px(12), px(12))
        self._root.setSpacing(px(10))

        self._panel.setStyleSheet(
            f"#consolePanel {{ background: {_CONSOLE_BG}; "
            f"border-radius: {px(12)}px; border: none; }}"
        )
        self._pl.setContentsMargins(px(14), px(12), px(14), px(12))
        self._pl.setSpacing(px(10))

        self._top.setSpacing(px(10))

        self._search_wrap.setFixedHeight(px(36))
        self._search_wrap.setStyleSheet(
            f"#conSearch {{ background: #4A5264; border: none; "
            f"border-radius: {px(18)}px; }}"
        )
        self._sw.setContentsMargins(px(12), px(2), px(12), px(2))
        self._sw.setSpacing(px(6))
        self._search_icon.setPixmap(icons.pixmap("search", px(15), "#C8CCD4"))
        self._search.setStyleSheet(
            f"* {{ background: transparent; border: none; "
            f"font-size: {fs(12)}px; color: #E8EAF0; }}" + POPUPS
        )

        filtering = not all(self._enabled_kinds.values())
        for b, icon_name in self._chips:
            # Filter chip highlights (cyan border) while any kind is hidden;
            # the auto-scroll chip highlights while following is on.
            active = ((b is self._filter_btn and filtering)
                      or (b is self._scroll_btn and self._autoscroll))
            border = f"1.5px solid #56C0D4" if active else "none"
            bg = "#E2EEF2" if active else LIGHT
            b.setIcon(icons.icon(icon_name, px(15), TXT))
            b.setFixedHeight(px(34))
            b.setStyleSheet(f"""
                QPushButton {{ background: {bg}; border: {border};
                               border-radius: {px(8)}px;
                               font-size: {fs(12)}px; color: {TXT};
                               padding: 0 {px(14)}px; }}
                QPushButton:hover {{ background: #E2E2E2; }}
            """)

        self._browser.setStyleSheet(
            f"background: {_CONSOLE_BG}; border: none; "
            f"font-family: 'Menlo', 'Courier New'; font-size: {fs(12)}px;"
        )

        self._cmd_wrap.setFixedHeight(px(44))
        self._cmd_wrap.setStyleSheet(
            f"#conCmd {{ background: {WHITE}; border-radius: {px(10)}px; "
            f"border: none; }}"
        )
        self._cw.setContentsMargins(px(14), px(4), px(6), px(4))
        self._cw.setSpacing(px(8))
        self._cmd_input.setStyleSheet(
            f"* {{ border: none; background: transparent; font-size: {fs(12)}px; }}"
            + POPUPS
        )
        self._send_btn.setFixedSize(px(34), px(34))
        self._send_btn.setStyleSheet(f"""
            QPushButton {{ background: {LIGHT}; border-radius: {px(8)}px;
                           border: 1px solid {BORDER}; font-size: {fs(13)}px; }}
            QPushButton:hover {{ background: #E0E0E0; }}
        """)

    # ── Log handling ──────────────────────────────────────────────────────────

    def _append(self, text: str, color: str) -> None:
        time_str = datetime.now().strftime("%H:%M:%S")
        self._entries.append((time_str, text, color))
        if len(self._entries) > _MAX_LINES:
            self._entries = self._entries[-_MAX_LINES:]
        query = self._search.text().strip().lower()
        if self._enabled_kinds[_kind_of(color)] and (not query or query in text.lower()):
            self._append_html(time_str, text, color)

    def _append_html(self, time_str: str, text: str, color: str) -> None:
        safe = html.escape(text).replace("\n", "<br/>")
        self._browser.append(
            f'<span style="color:#7F8694;font-size:{fs(10)}px">[{time_str}]</span>'
            f'&nbsp;<span style="color:{color}">{safe}</span>'
        )
        if self._autoscroll:
            sb = self._browser.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _toggle_autoscroll(self) -> None:
        self._autoscroll = not self._autoscroll
        self._prefs.setValue("console/autoScroll", self._autoscroll)
        self._apply_scale()             # refresh the chip's active highlight
        if self._autoscroll:            # catch up with the newest line
            sb = self._browser.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _rerender(self) -> None:
        query = self._search.text().strip().lower()
        self._browser.clear()
        for time_str, text, color in self._entries:
            if self._enabled_kinds[_kind_of(color)] and (not query or query in text.lower()):
                self._append_html(time_str, text, color)

    # ── Filter dropdown ─────────────────────────────────────────────────────

    def _open_filter(self) -> None:
        """Show the 'Filter console messages' dropdown below the filter chip,
        matching the mobile app's per-kind toggle list."""
        pop = self._build_filter_popup()
        for kind, cb in self._kind_checks.items():
            cb.setChecked(self._enabled_kinds[kind])
        pop.adjustSize()
        anchor = self._filter_btn.mapToGlobal(self._filter_btn.rect().bottomRight())
        pop.move(anchor.x() - pop.width(), anchor.y() + px(6))
        pop.show()

    def _build_filter_popup(self) -> QFrame:
        if self._filter_popup is not None:
            return self._filter_popup
        pop = QFrame(self, Qt.Popup)
        pop.setObjectName("filterPop")
        pop.setStyleSheet(
            f"#filterPop {{ background: #4A5264; border: 1px solid #5A6276; "
            f"border-radius: {px(10)}px; }}"
        )
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(px(14), px(12), px(14), px(12))
        lay.setSpacing(px(8))

        title = QLabel("Filter console messages")
        title.setStyleSheet(
            f"background: transparent; color: #E8EAF0; font-size: {fs(13)}px; "
            f"font-weight: bold;")
        lay.addWidget(title)

        self._kind_checks: dict[str, QCheckBox] = {}
        for kind in _KIND_ORDER:
            row = QHBoxLayout()
            row.setSpacing(px(16))
            name = QLabel(_KIND_LABELS[kind])
            name.setStyleSheet(
                f"background: transparent; color: {_KIND_LABEL_COLORS[kind]}; "
                f"font-size: {fs(12)}px;")
            row.addWidget(name)
            row.addStretch()
            cb = QCheckBox()
            cb.setCursor(Qt.PointingHandCursor)
            cb.setChecked(self._enabled_kinds[kind])
            cb.toggled.connect(lambda v, k=kind: self._set_kind(k, v))
            self._kind_checks[kind] = cb
            row.addWidget(cb)
            lay.addLayout(row)

        self._filter_popup = pop
        return pop

    def _set_kind(self, kind: str, enabled: bool) -> None:
        self._enabled_kinds[kind] = enabled
        self._rerender()
        self._apply_scale()   # refresh the filter chip's active highlight

    def _confirm_clear(self) -> None:
        from ui.pages.dialogs import ConfirmDialog
        dlg = ConfirmDialog("Clear all data from the console?",
                            "This data will be permanently deleted and cannot "
                            "be recovered.", parent=self)
        if dlg.exec():
            self._entries = []
            self._browser.clear()

    def _save_logs(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save console logs", f"piccola-cnc-logs-{stamp}.txt",
            "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w") as fh:
                for time_str, text, _color in self._entries:
                    fh.write(f"[{time_str}] {text}\n")
        except OSError:
            pass

    # ── Command history (bash-style ↑/↓ recall) ──────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self._cmd_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Up:
                self._history_step(-1)
                return True
            if event.key() == Qt.Key_Down:
                self._history_step(+1)
                return True
        return super().eventFilter(obj, event)

    def _history_step(self, direction: int) -> None:
        """Walk the sent-command history: ↑ goes back, ↓ forward; stepping
        past the newest entry restores whatever was being typed."""
        if not self._history:
            return
        if self._hist_idx is None:
            if direction > 0:
                return                      # nothing newer to go to
            self._hist_draft = self._cmd_input.text()
            self._hist_idx = len(self._history) - 1
        else:
            self._hist_idx += direction
            if self._hist_idx >= len(self._history):
                self._hist_idx = None       # back to the unsent draft
                self._cmd_input.setText(self._hist_draft)
                return
            self._hist_idx = max(0, self._hist_idx)
        self._cmd_input.setText(self._history[self._hist_idx])

    def _history_push(self, text: str) -> None:
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            self._history = self._history[-_MAX_HISTORY:]
            self._prefs.setValue("console/history", self._history)
        self._hist_idx = None
        self._hist_draft = ""

    # ── Real-machine wiring ───────────────────────────────────────────────────

    def wire(self, conn) -> None:
        self._conn = conn
        conn.log_received.connect(self._append)

        def _send() -> None:
            text = self._cmd_input.text().strip()
            if not text:
                return
            if text.startswith("{"):
                try:
                    conn.send(json.loads(text))
                except json.JSONDecodeError:
                    conn.send_raw(text)
            else:
                # Plain text → raw G-code/GRBL passthrough (as the mobile app does).
                conn.send_raw(text)
            self._history_push(text)
            self._cmd_input.clear()

        self._cmd_input.returnPressed.connect(_send)
        self._send_btn.clicked.connect(_send)
