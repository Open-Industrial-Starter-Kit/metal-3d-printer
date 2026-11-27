"""
ui/top_bar.py
Top navigation bar with hamburger menu, app title, and the vendor logo.
"""

import os
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap

from ui.theme import BG, PANEL, BORDER, NAVY, LIME, WTXT, RUN_C, TXT3, TXT
from ui.widgets import lbl
from ui.scale import fs, px, font, bind
from ui.tooltips import TRANSPARENT_NO_BORDER

_LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "images", "InMachines_Logo.png")


class TopBar(QFrame):
    hamburger_toggled = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False
        self.setObjectName("topBar")
        # Rounded bottom corners (styled in _apply_scale) so the gray chrome
        # meets the content with a fillet, matching the right panel.

        self._row = QHBoxLayout(self)

        self._ham = QPushButton("☰")
        self._ham.setCursor(Qt.PointingHandCursor)
        self._ham.clicked.connect(self.hamburger_toggled)
        self._row.addWidget(self._ham)

        self._row.addSpacing(px(6))
        # Plain QLabel styled directly (not via lbl/setFont): the global
        # "* { font-size: 14px }" stylesheet overrides setFont(), so the title
        # size must be set in the widget's own stylesheet to take effect.
        self._title = QLabel("OISK Metal 3D Printer")
        self._row.addWidget(self._title)
        self._dot = QLabel("●")   # grey until connected
        self._row.addWidget(self._dot)
        self._row.addStretch()

        self._logo_src = QPixmap(os.path.normpath(_LOGO_PATH))
        self._logo_lbl = QLabel()
        self._logo_lbl.setStyleSheet(TRANSPARENT_NO_BORDER)
        if self._logo_src.isNull():
            self._logo_lbl.setText("INMACHINES")
        self._row.addWidget(self._logo_lbl)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedHeight(px(92))
        self.setStyleSheet(f"#topBar {{ background: {PANEL}; border: none; }}")
        self._row.setContentsMargins(px(16), 0, px(16), 0)
        self._row.setSpacing(px(10))
        self._ham.setFixedSize(px(72), px(72))
        self._ham.setStyleSheet(f"""
            QPushButton {{ font-size: {px(46)}px; background: transparent; border: none; }}
            QPushButton:hover {{ background: {BORDER}; border-radius: {px(12)}px; }}
        """)
        self._title.setStyleSheet(
            f"color: {TXT}; background: transparent; "
            f"font-size: {px(40)}px; font-weight: bold;")
        self._refresh_dot()
        self._scale_logo()

    def _scale_logo(self):
        if self._logo_src.isNull():
            return
        # The PNG is exported at 2× — render at 2× the logical height and set the
        # device pixel ratio so it stays crisp on HiDPI displays.
        h = px(38)
        pm = self._logo_src.scaledToHeight(h * 2, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(2)
        self._logo_lbl.setPixmap(pm)

    def _refresh_dot(self):
        color = RUN_C if self._connected else TXT3
        self._dot.setStyleSheet(
            f"color: {color}; background: transparent; font-size: {px(20)}px;")

    def set_menu_enabled(self, enabled: bool) -> None:
        """Grey the hamburger while the app menu is unavailable (job running,
        homing, procedure busy — Android parity)."""
        self._ham.setEnabled(enabled)

    def set_title(self, text: str) -> None:
        """Show the machine name (from the API) as the app title."""
        self._title.setText(text or "OISK Metal 3D Printer")

    def set_connected(self, connected: bool) -> None:
        """Green dot when a transport is connected, grey otherwise."""
        self._connected = connected
        self._refresh_dot()
