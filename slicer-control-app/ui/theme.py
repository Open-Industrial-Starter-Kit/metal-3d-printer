"""
ui/theme.py
Color palette and global stylesheet constants.
"""

# ── Palette ───────────────────────────────────────────────────────────────────

BG      = "#FFFFFF"
PANEL   = "#DCDCDC"
WHITE   = "#FFFFFF"
LIGHT   = "#F0F0F0"
BORDER  = "#C8C8C8"
DARK    = "#687080"        # dark bar
IDLE_C  = "#56C0D4"        # cyan-blue
RUN_C   = "#87BB44"        # lime green
WARN_C  = "#D69A2D"        # amber — caution, not a fault
ERR_C   = "#C84040"        # red
LIME    = "#B5CC2C"        # accent dot / progress
NAVY    = "#192540"        # INMACHINES logo bg
TXT     = "#1A1A1A"
TXT2    = "#555555"
TXT3    = "#AAAAAA"
WTXT    = "#FFFFFF"

# ── Global stylesheet ─────────────────────────────────────────────────────────


def global_ss() -> str:
    """Application-wide base stylesheet, scaled to the current factor.

    Re-applied to the main window on every resize so the base font-size and
    scrollbar metrics track the responsive scale (conservative 14px base body
    text).  See ``ui/scale.py``."""
    from ui.scale import fs, px
    base = fs("body")
    sb = px(6)
    rad = px(3)
    minlen = px(20)
    return f"""
* {{
    font-family: 'Helvetica Neue', Arial;
    font-size: {base}px;
    color: {TXT};
}}
QScrollBar:vertical {{
    border: none; background: {LIGHT}; width: {sb}px; border-radius: {rad}px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: {rad}px; min-height: {minlen}px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    border: none; background: {LIGHT}; height: {sb}px; border-radius: {rad}px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: {rad}px; min-width: {minlen}px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


# Back-compat: a few call sites still import GLOBAL_SS. It reflects S = 1.0 at
# import time; live updates go through global_ss().
GLOBAL_SS = ""
