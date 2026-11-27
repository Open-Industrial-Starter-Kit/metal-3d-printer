"""
ui/cam_theme.py
Palette bridge that makes the embedded CAM tools look like the rest of the app.

Both Kiri:Moto and EasyTrace5000 drive their chrome from CSS custom properties,
so matching them to Piccola is a matter of re-pointing those variables at the
palette in ``ui/theme.py`` — the single source of truth, so the tools track any
future change to the app's colours.

Three rules keep this from turning into a maintenance problem:

1. **Variables only.**  No structural selectors (``.panel > div:nth-child(3)``
   and friends).  Variable names survive upstream refactors; DOM structure does
   not, and the whole vendoring design rests on upgrades being a folder swap.
   The one exception is a ``body`` font rule, which is inherited rather than
   matched.

2. **Chrome only — semantic colour is left alone.**  EasyTrace's
   ``--color-operations-*`` (isolation / drill / clearing / cutout / stencil),
   ``--color-geometry-*``, ``--color-semantic-*`` and ``--color-debug-*`` all
   *mean* something to the person reading the screen.  Repainting them to match
   a brand palette would make the tools harder to use, not prettier.  Same for
   Kiri's toolpath colours, which live in WebGL anyway.

3. **Scoped to each tool's light theme.**  Both use the same convention:
   ``:root`` is light, ``:root[data-theme="dark"]`` is dark.  Overrides apply
   only in light mode, so a user who switches a tool to dark gets that tool's
   own coherent dark theme instead of a half-converted hybrid.

What this deliberately cannot reach: Kiri's 3D viewport (Three.js/WebGL, styled
from JS) and EasyTrace's board canvas.  Those are painted, not styled.
"""

from __future__ import annotations

from ui.theme import (
    PANEL, WHITE, LIGHT, BORDER, DARK, IDLE_C, NAVY, TXT, TXT2, TXT3,
)


def _rgba(hex_color: str, alpha: float) -> str:
    """``#RRGGBB`` -> ``rgba(r, g, b, a)`` so alpha-blended vars can be matched."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# A couple of in-between tones the app palette does not name, derived here so
# the ramps below stay smooth.
_HOVER = "#E8E8E8"          # matches the hover used in ui/widgets.py
_MID = "#8C93A1"            # between DARK and BORDER
_DEEP = "#24314D"           # between NAVY and DARK
_BORDER_STRONG = "#999999"

# ── EasyTrace5000 ───────────────────────────────────────────────────────────
# Its theme-loader flattens themes/<id>.json onto documentElement with
# element.style.setProperty(), i.e. INLINE styles — which beat a normal author
# stylesheet.  Hence !important on these declarations; that is what lets a
# stylesheet win over an inline value.
_EASYTRACE_VARS = {
    "--color-bg-primary":      WHITE,
    "--color-bg-secondary":    LIGHT,
    "--color-bg-tertiary":     PANEL,
    "--color-bg-hover":        _HOVER,
    "--color-bg-active":       BORDER,

    "--color-text-primary":    TXT,
    "--color-text-secondary":  TXT2,
    "--color-text-disabled":   TXT3,
    "--color-text-hint":       TXT3,

    "--color-border-primary":  BORDER,
    "--color-border-secondary": PANEL,
    "--color-border-focus":    IDLE_C,

    # Primary action colour: the app's navy, as used for the toggle switches and
    # the InMachines mark.
    "--color-accent-primary":  NAVY,
    "--color-accent-hover":    _DEEP,
    "--color-accent-active":   "#101A2E",
}

# ── Kiri:Moto ───────────────────────────────────────────────────────────────
# Two sets: web/moto/palette.css (generic UI widgets) and web/kiri/index.css
# (a blue-grey ramp --blue-00..--blue-6 plus accents, which is what paints the
# left/right settings panels and their section headers).
_KIRI_VARS = {
    # menubar strip
    "--menubar-back":   LIGHT,
    "--menubar-fore":   TXT,
    "--menubar-vsep":   _rgba(BORDER, 0.9),
    "--menu-hover-bg":  _HOVER,
    "--menu-active-bg": _rgba(IDLE_C, 0.35),
    "--appname-blue":   NAVY,

    # generic widgets
    "--ui-text":                TXT,
    "--ui-text-muted":          TXT2,
    "--ui-border":              BORDER,
    "--ui-border-strong":       _BORDER_STRONG,
    "--ui-hr":                  BORDER,
    "--ui-input-bg":            LIGHT,
    "--ui-input-fg":            TXT,
    "--ui-input-disabled-bg":   "#E4E4E4",
    "--ui-input-disabled-fg":   TXT3,
    "--ui-select-bg":           LIGHT,
    "--ui-textarea-bg":         WHITE,
    "--ui-textarea-fg":         TXT,
    "--ui-button-bg":           LIGHT,
    "--ui-button-fg":           TXT,
    "--ui-button-border":       BORDER,
    "--ui-button-hover-bg":     _HOVER,
    "--ui-button-hover-fg":     TXT,
    "--ui-button-disabled-fg":  TXT3,
    "--ui-dialog-bg":           "rgba(255,255,255,0.96)",
    "--ui-dialog-fg":           TXT,
    "--ui-dialog-border-top":   _rgba(BORDER, 1),
    "--ui-dialog-border-bottom": _rgba(BORDER, 1),
    "--ui-option-selected-bg":  IDLE_C,
    "--border-gray":            _rgba(BORDER, 0.9),

    # blue-grey ramp -> the app's neutral/navy ramp. --blue-3 is the settings
    # section header background, so this is the most visible single mapping.
    "--blue-00": NAVY,
    "--blue-0":  _DEEP,
    "--blue-1":  DARK,
    "--blue-2":  _MID,
    "--blue-3":  PANEL,
    "--blue-4":  "#E6E6E6",
    "--blue-5":  LIGHT,
    "--blue-6":  "#F7F7F7",

    "--fg-gray":          TXT2,
    "--main-color":       DARK,
    "--main-color-hover": "#5A6270",
    "--highlight":        _rgba(IDLE_C, 0.35),
    "--row-hover":        _rgba(IDLE_C, 0.18),
    "--header":           _rgba(PANEL, 0.9),
    "--menu":             LIGHT,
    "--menu-text":        TXT,
    "--menu-hover":       _HOVER,
    "--menu-border":      BORDER,
    "--slide-bg":         _rgba(PANEL, 0.5),
}

# Font is applied to body only, and WITHOUT !important, so icon fonts keep their
# own family — Kiri renders its toolbar glyphs with Font Awesome, and forcing
# font-family globally would turn those into stray letters.
_FONT_CSS = """
body {
  font-family: 'Helvetica Neue', Arial, Helvetica, sans-serif;
}
"""


def _vars_block(selector: str, mapping: dict, important: bool) -> str:
    bang = " !important" if important else ""
    lines = "\n".join(f"  {k}: {v}{bang};" for k, v in mapping.items())
    return f"{selector} {{\n{lines}\n}}\n"


def easytrace_css() -> str:
    """Palette overrides for EasyTrace5000, light theme only."""
    return (_vars_block(':root[data-theme="light"]', _EASYTRACE_VARS, True)
            + _FONT_CSS)


#: The progress box names the step running ("slicing", "shadow", "rotary
#: rough"); Kiri writes them lowercase. Capitalised to match the add-operation
#: menu, which shows the same names as "Rotary Rough".
_PROGRESS_CSS = "#progtxt { text-transform: capitalize !important; }\n"


def kirimoto_css() -> str:
    """Palette overrides for Kiri:Moto, light theme only."""
    return (_vars_block(':root:not([data-theme="dark"])', _KIRI_VARS, True)
            + _FONT_CSS + _PROGRESS_CSS)


#: EasyTrace defaults to its dark theme; this is the localStorage key it reads
#: (config.js -> storageKeys.theme). Setting it to "light" before the tool boots
#: means it never paints dark first.
EASYTRACE_THEME_KEY = "cam-theme"
EASYTRACE_THEME = "light"


CSS_FOR_MODE = {
    "carve": kirimoto_css,
    "pcb": easytrace_css,
}
