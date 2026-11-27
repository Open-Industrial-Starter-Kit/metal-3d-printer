"""
ui/side_menu.py
Collapsible left navigation panel with File Manager, Controls, Slicer,
Live Preview, and Console nav items.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QSizePolicy, QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from ui.theme import PANEL, BORDER, LIGHT, WHITE, TXT, TXT2
from ui.icons import pixmap
from ui.scale import fs, px, bind
from ui.tooltips import TRANSPARENT, STYLE as TOOLTIP_STYLE


class NavItem(QFrame):
    clicked = Signal(int)

    ICONS = {0: "nav-files", 1: "nav-controls", 2: "nav-cam", 3: "nav-preview",
             4: "nav-console"}

    def __init__(self, idx: int, text: str):
        super().__init__()
        self.idx = idx
        self._active = False
        self._locked = False
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        self._lay = QVBoxLayout(self)
        self._lay.setAlignment(Qt.AlignCenter)

        self.icon_lbl = QLabel()
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setStyleSheet(TRANSPARENT)
        self._lay.addWidget(self.icon_lbl)

        self.txt_lbl = QLabel(text)
        self.txt_lbl.setAlignment(Qt.AlignCenter)
        self.txt_lbl.setWordWrap(True)
        self._lay.addWidget(self.txt_lbl)

        # _refresh is scale-aware; bind re-runs it on every scale change too.
        bind(self, self._refresh)

    def set_active(self, v: bool):
        self._active = v
        self._refresh()

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """Dim the entry and ignore clicks on it (tooltip says why)."""
        self._locked = locked
        if locked:
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.35)
            self.setGraphicsEffect(eff)
            self.setCursor(QCursor(Qt.ForbiddenCursor))
        else:
            self.setGraphicsEffect(None)
            self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip(reason if locked else "")

    def _refresh(self, *_):
        self.setMinimumHeight(px(70))
        self._lay.setSpacing(px(5))
        self._lay.setContentsMargins(px(4), px(8), px(4), px(8))
        bg = WHITE if self._active else "transparent"
        # `*` + the tooltip rule, not a bare declaration: a bare one is applied
        # to the "locked" tooltip too, which then draws as a black box (see
        # ui/tooltips.py).
        self.setStyleSheet(f"* {{ background: {bg}; border: none; }} "
                           + TOOLTIP_STYLE)
        color = TXT if self._active else TXT2
        self.icon_lbl.setPixmap(pixmap(self.ICONS[self.idx], size=px(52), color="#000000"))
        self.txt_lbl.setStyleSheet(
            f"font-size: {fs(15)}px; background: transparent; color: {color};"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._locked:
            self.clicked.emit(self.idx)


class SideMenu(QFrame):
    page_changed = Signal(int)

    def __init__(self):
        super().__init__()
        bind(self, self._apply_scale)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Order must match the page stack in main_window. Console stays last so
        # it remains the bottom entry.
        labels = ["File\nManager", "Controls", "Slicer", "Live\nPreview", "Console"]
        self._items: list[NavItem] = []
        for i, text in enumerate(labels):
            item = NavItem(i, text)
            item.clicked.connect(self._on_click)
            lay.addWidget(item, 1)   # equal stretch → divide full height evenly
            self._items.append(item)

        self._set_active(1)  # default: Controls

    def _apply_scale(self, *_):
        self.setFixedWidth(px(120))
        self.setStyleSheet(f"background: {PANEL}; border-right: 1px solid {BORDER};")

    def _on_click(self, idx: int):
        self._set_active(idx)
        self.page_changed.emit(idx)

    def _set_active(self, idx: int):
        for item in self._items:
            item.set_active(item.idx == idx)

    def set_page(self, idx: int):
        self._set_active(idx)

    def set_locked(self, indices, reason: str = "") -> None:
        """Lock the entries in *indices*, unlock all the others."""
        for item in self._items:
            item.set_locked(item.idx in indices, reason)
