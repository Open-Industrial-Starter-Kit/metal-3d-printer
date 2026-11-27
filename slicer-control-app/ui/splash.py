"""
ui/splash.py
Full-screen connection splash, reproducing the design's Home.svg 1:1: a white
screen with the bold "Welcome to Piccola CNC" title, a "Searching for connection"
subtitle, and a continuously rotating recycling spinner. Shown whenever no
machine is connected; fades out (450 ms, like the mobile app) once a serial /
WiFi transport comes up.

The title is rendered live as bold text (so the brand name is editable); the
subtitle and spinner are the exact vectorised paths extracted from the design
(icons/searching.svg, images/splash_subtitle.svg), so the spinner shape matches
the mockup pixel-for-pixel and ships inside the frozen build. Everything is sized
and positioned from the design's 1920×1080 artboard coordinates and scaled to
the live window width, so the composition stays faithful at any size.
"""

import math
import os

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QVariantAnimation, QEasingCurve, Signal,
)
from PySide6.QtGui import QPainter, QPixmap, QFont, QFontMetrics, QColor, QCursor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QLabel, QGraphicsOpacityEffect, QPushButton,
)

from ui.theme import BG
from ui.scale import px, fs
from ui import icons
from ui.tooltips import TRANSPARENT

_IMG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "images"))
_SPIN_C  = "#1C1B1F"      # design: recycling spinner colour

# The welcome wordmark is rendered live as text (using the app's own font stack)
# rather than a baked SVG, so the brand name is a real editable string.
TITLE_TEXT  = "Welcome to Piccola CNC"
_TITLE_FONT = "Helvetica Neue"    # matches the app's global font (ui/theme.py)

# Rotation geometry of the recycling glyph, measured from icons/searching.svg as
# scale-invariant fractions of the rendered glyph size. The glyph has 3-fold
# symmetry, so its centre of mass (the point it visually spins around) is offset
# from its bounding-box centre — pivoting about the bbox centre makes it wobble.
_SPIN_CFX, _SPIN_CFY = 0.5409, 0.4541    # centre of mass within the glyph pixmap
_SPIN_RMAX_F = 0.5527                    # max glyph radius from the pivot (sweep)
_SPIN_BBOX_DX, _SPIN_BBOX_DY = -0.0414, 0.0454   # glyph bbox centre − widget centre

# Design artboard size and element metrics (Home.svg), in artboard pixels.
_ART_W, _ART_H = 1920.0, 1080.0
_TITLE_H, _SUB_H, _SPIN_H = 47.2, 39.1, 140.4          # element heights
_TITLE_C = (959.25 / _ART_W, 339.10 / _ART_H)          # normalised centres
_SUB_C   = (959.55 / _ART_W, 486.05 / _ART_H)
_SPIN_CP = (955.15 / _ART_W, 652.80 / _ART_H)


def _svg_pixmap(path: str, height: int) -> QPixmap:
    """Render an SVG to a QPixmap *height* px tall (width from aspect), at 2×."""
    r = QSvgRenderer(path)
    vb = r.viewBoxF()
    if vb.height() <= 0:
        return QPixmap()
    dpr = 2
    w = max(1, round(height * vb.width() / vb.height()))
    pm = QPixmap(w * dpr, height * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def _text_pixmap(text: str, height: int) -> QPixmap:
    """Render *text* as a bold black wordmark whose glyphs are *height* px tall.

    The pixel size is solved from the font's tight bounding box so the rendered
    letters match the design's title height exactly, regardless of font metrics.
    Rendered at 2× for crisp HiDPI output, mirroring :func:`_svg_pixmap`."""
    dpr = 2
    probe = 100
    f = QFont(_TITLE_FONT)
    f.setBold(True)
    f.setPixelSize(probe)
    tb = QFontMetrics(f).tightBoundingRect(text)
    if tb.height() <= 0:
        return QPixmap()
    f.setPixelSize(max(8, round(probe * height / tb.height())))
    tb = QFontMetrics(f).tightBoundingRect(text)

    pad = 2
    pm = QPixmap((tb.width() + 2 * pad) * dpr, (tb.height() + 2 * pad) * dpr)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setFont(f)
    p.setPen(QColor("black"))
    # tightBoundingRect is relative to the baseline; offset so the glyph's
    # top-left lands at (pad, pad) inside the pixmap.
    p.drawText(pad - tb.left(), pad - tb.top(), text)
    p.end()
    return pm


class _Spinner(QWidget):
    """The design's recycling glyph, rotating continuously about its true centre
    of mass (mobile parity: 360° every ~1.6 s, linear).

    Because the glyph spins about its centre of mass rather than its bounding-box
    centre, the widget is sized to the full rotation sweep (diameter 2·Rmax) so
    the glyph never clips, and positioned via :meth:`place_center`, which lands
    the glyph's *visual* centre on the design coordinate."""

    def __init__(self, parent, glyph: int, color: str = _SPIN_C):
        super().__init__(parent)
        self._color = color
        self._glyph = 0                    # rendered glyph size (px)
        self._pm = QPixmap()
        self.setStyleSheet(TRANSPARENT)
        self.set_glyph_size(glyph)
        self._angle = 0.0
        self._opacity = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(20)        # 50 fps
        self._timer.timeout.connect(self._tick)

    def set_opacity(self, o: float):
        # Applied inside paintEvent rather than via a QGraphicsOpacityEffect: a
        # graphics effect fights this widget's custom painter for the device.
        self._opacity = max(0.0, min(1.0, o))
        self.update()

    def set_glyph_size(self, glyph: int):
        glyph = max(8, glyph)
        if glyph == self._glyph:
            return
        self._glyph = glyph
        self._pm = icons.pixmap("searching", glyph, self._color)
        # Span the whole rotation sweep (+2 px for anti-aliasing) so nothing clips.
        side = math.ceil(2 * _SPIN_RMAX_F * glyph) + 2
        self.setFixedSize(side, side)

    def place_center(self, px: float, py: float):
        """Move so the glyph's visual (bbox) centre sits at (px, py) in parent."""
        ox = _SPIN_BBOX_DX * self._glyph
        oy = _SPIN_BBOX_DY * self._glyph
        self.move(round(px - ox - self.width() / 2),
                  round(py - oy - self.height() / 2))

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def _tick(self):
        # 360° / 1600 ms × 20 ms ≈ 4.5° per frame
        self._angle = (self._angle + 4.5) % 360.0
        self.update()

    def paintEvent(self, _e):
        if self._opacity <= 0.0:
            return
        p = QPainter()
        if not p.begin(self):
            # On some platforms, a transient nested paint can make begin() fail.
            # Bail out safely instead of issuing painter ops on an inactive device.
            return
        try:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.setOpacity(self._opacity)
            c = self.width() / 2               # widget centre = pivot
            p.translate(c, c)
            p.rotate(self._angle)
            # Draw so the glyph's centre of mass coincides with the pivot.
            g = self._glyph
            p.translate(-_SPIN_CFX * g, -_SPIN_CFY * g)
            p.drawPixmap(0, 0, self._pm)
        finally:
            p.end()


class SplashOverlay(QWidget):
    """White full-window overlay shown while disconnected; fades on connect.

    The entrance mirrors the mobile app (``ConnectionSplashScreen``): the
    "Welcome to Piccola CNC" wordmark holds centred for 1200 ms (the *intro*), then
    over 600 ms it slides up to its mockup position while the "Searching for
    connection" subtitle and the spinner fade in (the *searching* transition);
    the spinner then rotates continuously (360° / 1.6 s, linear). On connect the
    whole overlay fades out over 450 ms."""

    offline_requested = Signal()   # the "develop offline" button was pressed

    _INTRO_HOLD_MS = 1200
    _TRANS_MS = 600
    _INTRO_DY_F = 0.17       # title drop (fraction of height) during the intro hold

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("splashOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#splashOverlay {{ background: {BG}; }}")
        self._anim = None
        self._cur_th = self._cur_sh = 0
        self._phase = "hidden"   # "hidden" | "intro" | "searching"
        self._phase_t = 1.0      # 0 = intro (text centred), 1 = searching (text up)
        self._intro_timer = None
        self._trans = None

        self._title = QLabel(self)
        self._title.setStyleSheet(TRANSPARENT)
        self._sub = QLabel(self)
        self._sub.setStyleSheet(TRANSPARENT)
        self._sub_eff = QGraphicsOpacityEffect(self._sub)
        self._sub.setGraphicsEffect(self._sub_eff)
        self._spinner = _Spinner(self, 110)

        # Developer offline entry: a subtle outlined pill in the top-right corner
        # that lets the app run without any machine (fades in with the search phase).
        self._offline_btn = QPushButton("Offline mode", self)
        self._offline_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._offline_btn.clicked.connect(self.offline_requested)
        self._off_eff = QGraphicsOpacityEffect(self._offline_btn)
        self._offline_btn.setGraphicsEffect(self._off_eff)
        self._off_eff.setOpacity(0.0)

    # ── responsive layout (scale design metrics to the live window) ──────────────

    def resizeEvent(self, _e):
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        s = max(0.5, min(1.2, w / _ART_W))      # design → screen scale

        th, sh, sp = round(_TITLE_H * s), round(_SUB_H * s), round(_SPIN_H * s)
        if th != self._cur_th:
            self._title.setPixmap(_text_pixmap(TITLE_TEXT, th))
            self._title.adjustSize()
            self._cur_th = th
        if sh != self._cur_sh:
            self._sub.setPixmap(_svg_pixmap(os.path.join(_IMG_DIR, "splash_subtitle.svg"), sh))
            self._sub.adjustSize()
            self._cur_sh = sh
        self._spinner.set_glyph_size(sp)
        self._style_offline_btn()
        self._position_all()

    def _style_offline_btn(self):
        b = self._offline_btn
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: #8A8A8A;
                border: 1px solid #C8C8C8; border-radius: {px(16)}px;
                padding: {px(7)}px {px(18)}px; font-size: {fs(13)}px;
            }}
            QPushButton:hover   {{ background: #ECECEC; color: #5A5A5A;
                                   border-color: #B0B0B0; }}
            QPushButton:pressed {{ background: #E0E0E0; }}
        """)
        b.adjustSize()

    def _position_all(self):
        """Lay the three elements out for the current animation progress."""
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        # The title slides from a near-centred intro spot up to its mockup centre.
        dy = (1.0 - self._phase_t) * self._INTRO_DY_F * h
        tcx, tcy = _TITLE_C[0] * w, _TITLE_C[1] * h + dy
        self._title.move(round(tcx - self._title.width() / 2),
                         round(tcy - self._title.height() / 2))
        self._place(self._sub, _SUB_C, w, h)
        self._spinner.place_center(_SPIN_CP[0] * w, _SPIN_CP[1] * h)
        # Offline button pinned to the top-right corner with a small margin.
        m = px(16)
        b = self._offline_btn
        b.move(w - b.width() - m, m)

    @staticmethod
    def _place(widget, norm_center, w, h):
        cx, cy = norm_center[0] * w, norm_center[1] * h
        widget.move(round(cx - widget.width() / 2), round(cy - widget.height() / 2))

    # ── entrance sequence ─────────────────────────────────────────────────────────

    # ── child fade effects ───────────────────────────────────────────────────────
    #
    # The subtitle and the offline button fade in through their own
    # QGraphicsOpacityEffect, and finish() fades the WHOLE overlay out through
    # another one on the overlay itself. Qt does not support a graphics effect
    # inside a graphics effect: rendering the outer one paints each child into
    # a pixmap while its own effect is already painting it, and the console
    # fills with "QPainter::begin: A paint device can only be painted by one
    # painter at a time" / "QWidgetEffectSourcePrivate::pixmap: Painter not
    # active" for the length of the fade. So the child effects are taken off
    # before the outer fade and put back when the splash is shown again.
    # (setGraphicsEffect deletes the effect it replaces, hence recreating.)

    def _ensure_child_effects(self):
        if self._sub_eff is None:
            self._sub_eff = QGraphicsOpacityEffect(self._sub)
            self._sub.setGraphicsEffect(self._sub_eff)
            self._sub.show()
        if self._off_eff is None:
            self._off_eff = QGraphicsOpacityEffect(self._offline_btn)
            self._offline_btn.setGraphicsEffect(self._off_eff)
            self._offline_btn.show()

    def _drop_child_effects(self):
        # A child that is still faded out is hidden instead, so losing its
        # effect cannot flash it up at full opacity during the fade.
        for w, eff in ((self._sub, self._sub_eff), (self._offline_btn, self._off_eff)):
            if eff is None:
                continue
            if eff.opacity() < 0.5:
                w.hide()
            w.setGraphicsEffect(None)
        self._sub_eff = None
        self._off_eff = None

    def _set_phase_t(self, t: float):
        """Drive the intro→searching progress (0..1): title position + fade-ins."""
        self._phase_t = t
        self._ensure_child_effects()
        self._sub_eff.setOpacity(t)
        self._spinner.set_opacity(t)
        self._off_eff.setOpacity(t)
        # Only clickable once it has faded in, so it can't be hit during intro.
        self._offline_btn.setEnabled(t > 0.99)
        self._position_all()

    def _start_intro(self):
        self._phase = "intro"
        self._stop_timers()
        self._spinner.stop()
        self._set_phase_t(0.0)          # title centred, subtitle + spinner hidden
        self._intro_timer = QTimer(self)
        self._intro_timer.setSingleShot(True)
        self._intro_timer.timeout.connect(self._begin_searching)
        self._intro_timer.start(self._INTRO_HOLD_MS)

    def _begin_searching(self):
        self._phase = "searching"
        self._spinner.start()
        self._trans = QVariantAnimation(self)
        self._trans.setStartValue(0.0)
        self._trans.setEndValue(1.0)
        self._trans.setDuration(self._TRANS_MS)
        self._trans.setEasingCurve(QEasingCurve.InOutQuad)
        self._trans.valueChanged.connect(lambda v: self._set_phase_t(float(v)))
        self._trans.start()

    def _stop_timers(self):
        if self._intro_timer is not None:
            self._intro_timer.stop()
            self._intro_timer = None
        if self._trans is not None:
            self._trans.stop()
            self._trans = None

    # ── show / hide ──────────────────────────────────────────────────────────────

    def search(self):
        """Disconnected → show the splash and play the entrance once."""
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self.setGraphicsEffect(None)
        self._ensure_child_effects()
        playing = (not self.isHidden()) and self._phase in ("intro", "searching")
        if self.isHidden():
            self.show()
        self.raise_()
        if playing:
            if self._phase == "searching":
                self._spinner.start()
            return
        self._start_intro()

    def finish(self):
        """Connected → fade the splash out (450 ms), then hide and stop."""
        if self.isHidden() or self._anim is not None:
            return
        self._stop_timers()
        self._drop_child_effects()      # no effect inside an effect, see above
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._anim = QPropertyAnimation(eff, b"opacity", self)
        self._anim.setDuration(450)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._after_fade)
        self._anim.start()

    def _after_fade(self):
        self._anim = None
        self._spinner.stop()
        self.setGraphicsEffect(None)
        self.hide()
        self._phase = "hidden"
