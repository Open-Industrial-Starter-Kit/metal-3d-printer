"""
ui/icons.py
Load the bundled SVG line-icons (icons/*.svg) as crisp, recolorable QPixmaps /
QIcons.  The SVGs use ``stroke="currentColor"`` so a single source renders in
any colour; rendering is done at 2× for HiDPI sharpness.
"""

import os

from PySide6.QtCore import Qt, QByteArray, QSize, QRectF
from PySide6.QtGui import QPixmap, QIcon, QPainter
from PySide6.QtSvg import QSvgRenderer

from ui.theme import TXT

_ICON_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "icons"))
_cache: dict[tuple, QPixmap] = {}


def pixmap(name: str, size: int = 24, color: str = TXT) -> QPixmap:
    """Render icon *name* to a QPixmap of *size* px in *color* (HiDPI-aware)."""
    key = (name, size, color)
    if key in _cache:
        return _cache[key]

    path = os.path.join(_ICON_DIR, f"{name}.svg")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read()
    except OSError:
        pm = QPixmap()
        _cache[key] = pm
        return pm

    data = data.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))

    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)

    _cache[key] = pm
    return pm


def pixmap_box(name: str, w: int, h: int, color: str = TXT) -> QPixmap:
    """Render icon *name* into a *w*×*h* box, preserving its aspect ratio and
    centring it (for wide illustrations like the scan setup diagrams, which the
    square :func:`pixmap` would stretch)."""
    path = os.path.join(_ICON_DIR, f"{name}.svg")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = fh.read()
    except OSError:
        return QPixmap()
    data = data.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))

    dpr = 2
    pm = QPixmap(w * dpr, h * dpr)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    vb = renderer.viewBoxF()
    if vb.width() > 0 and vb.height() > 0:
        s = min(w / vb.width(), h / vb.height())
        tw, th = vb.width() * s, vb.height() * s
        renderer.render(painter, QRectF((w - tw) / 2 * dpr, (h - th) / 2 * dpr,
                                        tw * dpr, th * dpr))
    else:
        renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, size: int = 24, color: str = TXT) -> QIcon:
    """Return a QIcon for icon *name*."""
    return QIcon(pixmap(name, size, color))


def icon_size(size: int = 24) -> QSize:
    return QSize(size, size)
