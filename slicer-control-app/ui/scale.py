"""
ui/scale.py
Central responsive-scaling engine.

A single global factor ``S`` drives every font-size and pixel dimension in the
app so the UI scales fluidly with the screen it opens on AND with manual window
resizing.  It follows conservative responsive-typography guidance: a 14px base
body size (the recommended hard floor for body copy), never rendered below
``FONT_FLOOR_PX`` once scaled down, with headings stepping up from there.

Usage
-----
    from ui.scale import mgr, fs, px, font, bind

    # inside a stylesheet f-string (sizes are px):
    f"font-size: {fs('body')}px; padding: {px(8)}px; border-radius: {px(10)}px;"
    f"font-size: {fs(18)}px;"                 # a raw px value works too

    # for QFont-based widgets (lbl(), …) call sites use POINT sizes:
    w.setFont(font(13, bold=True))           # 13pt scaled by S

    # to (re)apply on every scale change — calls fn() now, then on resize:
    bind(self, self._apply_scale)

Live rescaling is driven by :class:`ScaleManager`.  Only connect *bound methods*
of ``QObject`` subclasses to :attr:`ScaleManager.scaleChanged` (Qt auto-removes
them when the widget is destroyed).  Plain factory widgets read ``mgr.s`` once at
construction; their owning page re-styles them from its own ``_apply_scale``.
"""

from PySide6.QtCore import QObject, Signal

# ── Reference geometry ──────────────────────────────────────────────────────
# The mockup was drawn for a ~1280×800 content area.  This is the CONSERVATIVE
# profile the user chose: S is capped at 1.0 (the design never grows past mockup
# size on big monitors) and only scales DOWN toward MIN_SCALE so the whole
# layout still fits on small / scaled laptop displays.
REF_W = 1280
REF_H = 800
MIN_SCALE = 0.80
MAX_SCALE = 1.00

# ── Conservative type scale (logical px at S = 1.0) ─────────────────────────
# Body / buttons sit at 14px — the recommended base for body text.  FONT_FLOOR_PX
# is a low absolute legibility net (NOT the 14px body rule): it stops micro-labels
# from disappearing at MIN_SCALE without enlarging the design's intentionally
# small 8–10px captions at S = 1.0.
ROLE_PX = {
    "caption": 13,
    "small":   13,
    "body":    14,
    "button":  14,
    "label":   14,
    "h3":      18,
    "h2":      22,
    "h1":      28,
    "display": 34,
}
FONT_FLOOR_PX = 8
LINE_HEIGHT = 1.5


def compute_scale(w: int, h: int) -> float:
    """Factor that keeps a REF_W×REF_H design fitting inside *w*×*h*."""
    if w <= 0 or h <= 0:
        return 1.0
    s = min(w / REF_W, h / REF_H)
    return max(MIN_SCALE, min(MAX_SCALE, s))


class ScaleManager(QObject):
    """Holds the current scale factor and notifies listeners when it changes."""

    scaleChanged = Signal(float)

    def __init__(self):
        super().__init__()
        self._s = 1.0

    @property
    def s(self) -> float:
        return self._s

    def set_scale(self, s: float) -> bool:
        """Set the factor (clamped).  Returns True (and emits) only on a real
        change — a small dead-band stops a resize drag from thrashing the tree."""
        s = max(MIN_SCALE, min(MAX_SCALE, float(s)))
        if abs(s - self._s) < 0.012:
            return False
        self._s = s
        self.scaleChanged.emit(s)
        return True

    def set_from_size(self, w: int, h: int) -> bool:
        return self.set_scale(compute_scale(w, h))


# Module-level singleton — import and use directly.
mgr = ScaleManager()


def _scaled(v: float) -> int:
    return max(1, round(v * mgr.s))


def px(v: float) -> int:
    """Scale an arbitrary pixel dimension (spacing, radius, fixed size, icon)."""
    return _scaled(v)


def fs(role_or_px) -> int:
    """Scaled font-size in px.  Accepts a ROLE_PX role name or a raw px value."""
    base = ROLE_PX[role_or_px] if isinstance(role_or_px, str) else role_or_px
    return max(FONT_FLOOR_PX, round(base * mgr.s))


def font(role_or_pt, bold: bool = False, family: str = "Helvetica Neue"):
    """A QFont scaled for the current factor.

    A role name → pixel sizing via ROLE_PX; a numeric value is treated as a
    POINT size (back-compat with lbl(sz=…) call sites that used point sizes)."""
    from PySide6.QtGui import QFont
    f = QFont(family)
    if isinstance(role_or_pt, str):
        f.setPixelSize(fs(role_or_pt))
    else:
        f.setPointSizeF(max(8.0, role_or_pt * mgr.s))
    f.setBold(bold)
    return f


def bind(widget, fn) -> None:
    """Apply *fn* now and re-apply on every global scale change.

    *fn* must be a bound method of *widget* (a QObject) so Qt auto-disconnects
    it when the widget is destroyed — callers don't manage teardown."""
    fn()
    mgr.scaleChanged.connect(fn)


class _Rescaler(QObject):
    """Re-runs a styling callback on every scale change for a plain widget.

    Parented to the widget, so Qt destroys it (and drops its signal connection)
    when the widget goes away — making :func:`autoscale` safe for the bare
    QWidgets returned by the factory helpers, which can't host a bound slot."""

    def __init__(self, widget, styler):
        super().__init__(widget)
        self._widget = widget
        self._styler = styler
        styler(widget)
        mgr.scaleChanged.connect(self._on_changed)

    def _on_changed(self, *_):
        try:
            self._styler(self._widget)
        except RuntimeError:
            pass  # underlying C++ widget already deleted


def autoscale(widget, styler):
    """Style *widget* now via ``styler(widget)`` and re-style on every scale
    change, with the connection's lifetime bound to *widget*.  Returns *widget*
    so it can wrap a factory return value inline."""
    _Rescaler(widget, styler)
    return widget
