"""
ui/widgets.py
Reusable helper factory functions that produce styled PySide6 widgets.
"""

from PySide6.QtWidgets import (
    QLabel, QPushButton, QFrame, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, QPointF, QByteArray, QRectF
from PySide6.QtGui import QCursor, QPainter, QPolygonF, QColor
from PySide6.QtSvg import QSvgRenderer

from ui.theme import (
    TXT, TXT2, TXT3, WHITE, LIGHT, BORDER, WTXT,
)
from ui.scale import fs, px, font, autoscale, bind


def add_shadow(widget, blur: int = 20, dy: int = 4, alpha: int = 50):
    """Apply a soft drop shadow so a widget reads as an elevated card."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setXOffset(0)
    eff.setYOffset(dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return widget


def rounded_dialog_card(dialog) -> QFrame:
    """Give a frameless QDialog real filleted (rounded) corners.

    A QDialog cannot clip its own stylesheet background to a border-radius on a
    translucent window, so the rounded card must be an inner QFrame that paints
    the background (same trick as the app-menu sheets). This makes *dialog*
    translucent, drops an object-named QFrame filling it, and returns that frame
    so the caller parents its content layout to the card and sets the rounded
    background via ``#dialogCard {{ ... }}`` in its scale hook."""
    dialog.setAttribute(Qt.WA_TranslucentBackground)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    card = QFrame()
    card.setObjectName("dialogCard")
    outer.addWidget(card)
    return card


def lbl(text, sz=13, bold=False, color=TXT, align=Qt.AlignLeft | Qt.AlignVCenter):
    """Create a styled QLabel (font scales with the responsive factor)."""
    w = QLabel(text)
    w.setAlignment(align)

    def style(x):
        x.setFont(font(sz, bold))
        x.setStyleSheet(f"color: {color}; background: transparent;")

    return autoscale(w, style)


def btn(text, bg=WHITE, fg=TXT, radius=8, h=38, bold=False, border=True, min_w=None):
    """Create a styled QPushButton (scales with the responsive factor)."""
    b = QPushButton(text)
    bdr = f"1px solid {BORDER}" if border else "none"
    fw = "bold" if bold else "normal"

    def style(x):
        mw = f"min-width: {px(min_w)}px;" if min_w else ""
        x.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {fg}; border-radius: {px(radius)}px;
                border: {bdr}; font-weight: {fw};
                padding: {px(2)}px {px(10)}px; {mw}
            }}
            QPushButton:hover   {{ background: #E0E0E0; }}
            QPushButton:pressed {{ background: #D0D0D0; }}
        """)
        x.setMinimumHeight(px(h))

    return autoscale(b, style)


def icon_btn(text, size=48, r=None, bg=WHITE, fg=TXT, font_sz=15):
    """Create a square/circular icon button (scales with the factor)."""
    b = QPushButton(text)
    base_r = size // 2 if r is None else r

    def style(x):
        x.setFixedSize(px(size), px(size))
        x.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; border-radius: {px(base_r)}px;
                border: 1px solid {BORDER}; font-size: {fs(font_sz)}px; color: {fg};
            }}
            QPushButton:hover   {{ background: #E0E0E0; }}
            QPushButton:pressed {{ background: #CCCCCC; }}
        """)

    return autoscale(b, style)


def jog_btn(text, size=52, circular=False, bg=WHITE, font_sz=14):
    """Create a square jog-pad button (optionally circular), elevated card style."""
    b = QPushButton(text)

    def style(x):
        x.setFixedSize(px(size), px(size))
        r = px(size // 2) if circular else px(14)
        x.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; border-radius: {r}px;
                border: none; font-size: {fs(font_sz)}px;
            }}
            QPushButton:hover   {{ background: #F2F2F2; }}
            QPushButton:pressed {{ background: #E4E4E4; }}
        """)

    autoscale(b, style)
    add_shadow(b)
    return b


_JOG_ARROW_COLOR = "#79747E"

# Solid-triangle vertices (unit [0,1]² box) for the cardinal buttons.
_CARDINAL = {
    "up":    [(0.50, 0.14), (0.16, 0.82), (0.84, 0.82)],
    "down":  [(0.50, 0.86), (0.16, 0.18), (0.84, 0.18)],
    "left":  [(0.14, 0.50), (0.82, 0.16), (0.82, 0.84)],
    "right": [(0.86, 0.50), (0.18, 0.16), (0.18, 0.84)],
}

# Diagonal (45°) jog arrows — solid triangles pointing OUTWARD toward each
# corner (NW → up-left, NE → up-right, SW → down-left, SE → down-right), so they
# agree in direction with the outward-pointing N/S/E/W cardinal arrows. viewBox
# is a plain 0..100 box; each is an isoceles triangle with its tip at the corner.
_DIAG_ARROWS = {
    "nw": ("0 0 100 100", "M18 18 L42 90 L90 42 Z"),   # tip up-left  (outward)
    "ne": ("0 0 100 100", "M82 18 L58 90 L10 42 Z"),   # tip up-right (outward)
    "sw": ("0 0 100 100", "M18 82 L42 10 L90 58 Z"),   # tip down-left (outward)
    "se": ("0 0 100 100", "M82 82 L58 10 L10 58 Z"),   # tip down-right (outward)
}


class TriangleLabel(QWidget):
    """Paints one jog-direction arrow.

    *direction* → a named cardinal ("up"/"down"/"left"/"right"), drawn as a solid
    polygon. *svg* → one of the diagonal keys ("nw"/"ne"/"sw"/"se"), rendered
    from the exact design path so its orientation matches the mockup precisely.
    """

    def __init__(self, direction: str | None = None, svg: str | None = None,
                 extent: int = 24, color: str = _JOG_ARROW_COLOR):
        super().__init__()
        self._renderer = None
        self._pts = None
        if svg is not None:
            view_box, path_d = _DIAG_ARROWS[svg]
            data = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">'
                    f'<path d="{path_d}" fill="{color}"/></svg>')
            self._renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
        else:
            self._pts = _CARDINAL[direction]
        self._color = QColor(color)
        self._extent = extent
        self.setFixedSize(px(extent), px(extent))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_extent(self, extent: int) -> None:
        self._extent = extent
        self.setFixedSize(px(extent), px(extent))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._renderer is not None:
            self._renderer.render(p, QRectF(0, 0, self.width(), self.height()))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(self._color)
            w, h = self.width(), self.height()
            p.drawPolygon(QPolygonF([QPointF(x * w, y * h) for x, y in self._pts]))
        p.end()


class JogDirButton(QPushButton):
    """Directional jog button: a solid triangle + optional axis label.

    The label sits on the *inner* edge so the triangle points outward, matching
    the Piccola CNC design (N: ▲/Y, S: Y/▼, W: ◀ X, E: X ▶).

    horizontal=True  → arrow and label sit side-by-side (for ◀/▶ buttons).
    label_first=True → axis label comes before the arrow in layout order.
    """

    def __init__(self, direction: str | None = None, axis: str | None = None,
                 size: int = 72, circular: bool = False,
                 horizontal: bool = False, label_first: bool = False,
                 svg: str | None = None, bg: str = WHITE):
        super().__init__()
        self._size = size
        self._circular = circular
        self._bg = bg
        self.setCursor(QCursor(Qt.PointingHandCursor))
        add_shadow(self)

        self._extent = 30 if svg else 26
        self._arrow = TriangleLabel(direction=direction, svg=svg, extent=self._extent)

        self._axis_lbl = None
        items = [self._arrow]
        if axis:
            axis_lbl = QLabel(axis)
            axis_lbl.setAlignment(Qt.AlignCenter)
            axis_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._axis_lbl = axis_lbl
            items = [axis_lbl, self._arrow] if label_first else [self._arrow, axis_lbl]

        lay = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self._lay = lay
        for it in items:
            lay.addWidget(it, 0, Qt.AlignCenter)

        bind(self, self._apply_scale)

    def set_axis_label(self, text: str) -> None:
        """Change the axis letter shown beside the arrow (e.g. Y ↔ A when a
        state changes). No-op if this button has no label."""
        if self._axis_lbl is not None:
            self._axis_lbl.setText(text)

    def _apply_scale(self, *_):
        ssize = px(self._size)
        self.setFixedSize(ssize, ssize)
        r = px(self._size // 2) if self._circular else px(18)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {self._bg}; border-radius: {r}px; border: none;
            }}
            QPushButton:hover   {{ background: #F2F2F2; }}
            QPushButton:pressed {{ background: #E4E4E4; }}
        """)
        self._arrow.set_extent(self._extent)
        if self._axis_lbl is not None:
            self._axis_lbl.setStyleSheet(
                f"background: transparent; border: none; "
                f"font-size: {fs(18)}px; color: {_JOG_ARROW_COLOR};")
        m = px(4)
        self._lay.setContentsMargins(m, m, m, m)
        self._lay.setSpacing(px(2))


def step_btn(text, active=False, size=44):
    """Create a checkable step-size button (scales with the factor)."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(active)

    def style(x):
        x.setFixedSize(px(size), px(32))
        x.setStyleSheet(f"""
            QPushButton {{
                background: {LIGHT}; border-radius: {px(8)}px;
                border: 1px solid {BORDER}; font-size: {fs(12)}px;
            }}
            QPushButton:checked {{
                background: {WHITE}; border: 1.5px solid #999;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: #E8E8E8; }}
        """)

    return autoscale(b, style)


def speed_btn(text, active=False):
    """Create a checkable speed toggle button (scales with the factor)."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(active)

    def style(x):
        x.setMinimumWidth(px(56))
        x.setFixedHeight(px(32))
        x.setStyleSheet(f"""
            QPushButton {{
                background: {LIGHT}; border-radius: {px(8)}px;
                border: 1px solid {BORDER}; font-size: {fs(12)}px;
            }}
            QPushButton:checked {{
                background: {WHITE}; border: 1.5px solid #999;
                font-weight: bold;
            }}
            QPushButton:hover {{ background: #E8E8E8; }}
        """)

    return autoscale(b, style)


def toggle_switch(checked: bool = False) -> "QCheckBox":
    """Pill-style on/off switch (mobile-app look) backed by a QCheckBox."""
    from PySide6.QtWidgets import QCheckBox
    sw = QCheckBox()
    sw.setChecked(checked)
    sw.setCursor(QCursor(Qt.PointingHandCursor))

    def style(x):
        w_, h_, r_ = px(42), px(24), px(12)
        x.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: {w_}px; height: {h_}px; border-radius: {r_}px;
                background: #C7C7C7;
            }}
            QCheckBox::indicator:checked  {{ background: #192540; }}
            QCheckBox::indicator:disabled {{ background: #E0E0E0; }}
        """)

    return autoscale(sw, style)


def hline(color=BORDER):
    """Create a 1px horizontal divider line."""
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {color}; border: none;")
    return f


def vline(color=BORDER):
    """Create a 1px vertical divider line."""
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background: {color}; border: none;")
    return f


def card(r=10, bg=WHITE):
    """Create a rounded card frame (ID selector so the style doesn't cascade)."""
    f = QFrame()
    f.setObjectName("card")

    def style(x):
        x.setStyleSheet(
            f"#card {{ background: {bg}; border-radius: {px(r)}px; "
            f"border: 1px solid {BORDER}; }}"
        )

    return autoscale(f, style)
