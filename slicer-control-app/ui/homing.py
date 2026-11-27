"""
ui/homing.py
Initial homing flow shown right after a machine connects (mobile-app parity:
``HomingModal``; design: "Home-1.svg" / "Home-2.svg"). A dimmed full-window
overlay hosts a white card that walks through the homing sequence:

    prompt  → "Initializing homing sequence" + start
    running → "Homing in progress"           + cancel
    failed  → red error message              + retry

A rotary-axis-connected variant disables the start button (homing is unsafe with
the rotary axis attached). The overlay only emits intent — ``start_requested`` /
``cancel_requested`` — and the owner (MainWindow) sends the actual commands and
drives the state from the machine's status / homingCompleted messages.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from ui.theme import WHITE, PANEL, BORDER, TXT, TXT2, TXT3, ERR_C
from ui.scale import fs, px, bind
from ui import icons
from ui.tooltips import TRANSPARENT


class HomingOverlay(QWidget):
    """Full-window dim + centered homing card. Parented to the central widget."""

    start_requested  = Signal()
    cancel_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("homingOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._machine_name = ""
        self._in_progress = False
        self._failed = False
        self._rotary_connected = False

        # Centre the card over the dimmed backdrop.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignCenter)

        self._card = QFrame()
        self._card.setObjectName("homingCard")
        card = QVBoxLayout(self._card)
        card.setAlignment(Qt.AlignHCenter)
        self._card_lay = card

        self._header = QLabel("Connected to")
        self._header.setAlignment(Qt.AlignCenter)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignCenter)

        self._title = QLabel("Initializing homing sequence")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)

        self._body = QLabel("Please ensure the work area is clear of all obstructions.")
        self._body.setAlignment(Qt.AlignCenter)
        self._body.setWordWrap(True)

        self._rotary_note = QLabel(
            "Remove rotary axis to continue.\n"
            "Homing is disabled while rotary axis is attached.")
        self._rotary_note.setAlignment(Qt.AlignCenter)
        self._rotary_note.setWordWrap(True)
        self._rotary_note.hide()

        self._btn = QPushButton("start")
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._on_btn)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._btn)
        btn_row.addStretch()

        card.addWidget(self._header)
        card.addWidget(self._icon)
        card.addWidget(self._title)
        card.addWidget(self._body)
        card.addWidget(self._rotary_note)
        card.addLayout(btn_row)

        outer.addWidget(self._card)

        bind(self, self._apply_scale)
        self._render()
        self.hide()

    # ── public API ──────────────────────────────────────────────────────────

    def set_state(self, *, in_progress: bool, failed: bool,
                  rotary_connected: bool, machine_name: str = "") -> None:
        self._in_progress = in_progress
        self._failed = failed
        self._rotary_connected = rotary_connected
        self._machine_name = machine_name or self._machine_name
        self._render()

    def show_overlay(self) -> None:
        self.show()
        self.raise_()

    # ── rendering ─────────────────────────────────────────────────────────────

    def _apply_scale(self, *_):
        self.setStyleSheet("#homingOverlay { background: rgba(0,0,0,0.55); }")
        self._card.setFixedWidth(px(440))
        self._card.setStyleSheet(
            f"#homingCard {{ background: {WHITE}; border-radius: {px(16)}px; }}")
        self._card_lay.setContentsMargins(px(32), px(28), px(32), px(28))
        self._card_lay.setSpacing(px(16))
        self._header.setStyleSheet(
            f"background: transparent; color: {TXT2}; font-size: {fs(12)}px;")
        self._title.setStyleSheet(
            f"background: transparent; color: {TXT}; font-size: {fs(20)}px; "
            f"font-weight: bold;")
        self._rotary_note.setStyleSheet(
            f"background: transparent; color: {TXT}; font-size: {fs(14)}px; "
            f"font-weight: bold;")
        self._btn.setFixedSize(px(150), px(48))
        self._style_button()
        self._refresh_icon()
        self._refresh_body_color()

    def _style_button(self):
        self._btn.setStyleSheet(f"""
            QPushButton {{ background: {PANEL}; border: none;
                           border-radius: {px(8)}px; font-size: {fs(15)}px;
                           color: {TXT}; }}
            QPushButton:hover   {{ background: #D2D2D2; }}
            QPushButton:pressed {{ background: #C8C8C8; }}
            QPushButton:disabled {{ background: #EDEDED; color: {TXT3}; }}
        """)

    def _refresh_icon(self):
        # setText / setPixmap override each other, so the QLabel shows whichever
        # was set last — no explicit clear needed.
        if self._in_progress:
            self._icon.setPixmap(icons.pixmap("home", px(48), TXT))
            self._icon.setStyleSheet(TRANSPARENT)
        else:
            self._icon.setText("⚠")
            self._icon.setStyleSheet(
                f"background: transparent; color: {TXT}; font-size: {fs(46)}px;")

    def _refresh_body_color(self):
        color = ERR_C if self._failed else TXT
        self._body.setStyleSheet(
            f"background: transparent; color: {color}; font-size: {fs(14)}px;")

    def _render(self):
        self._header.setText(f"Connected to {self._machine_name or 'machine'}")
        if self._in_progress:
            self._title.setText("Homing in progress")
            self._body.setText("Please wait until all coordinates are synchronized.")
            self._btn.setText("Cancel")
            self._btn.setEnabled(True)
            self._rotary_note.hide()
        else:
            self._title.setText("Initializing homing sequence")
            self._body.setText(
                "Homing failed. Please check the machine and try again."
                if self._failed
                else "Please ensure the work area is clear of all obstructions.")
            self._btn.setText("Retry" if self._failed else "Start")
            self._btn.setEnabled(not self._rotary_connected)
            self._rotary_note.setVisible(self._rotary_connected)
        self._refresh_icon()
        self._refresh_body_color()

    def _on_btn(self):
        if self._in_progress:
            self.cancel_requested.emit()
        else:
            self.start_requested.emit()
