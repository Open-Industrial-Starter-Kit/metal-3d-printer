"""
ui/tooltips.py
One look for every tooltip in the app: dark text on white, a thin border.

Why
---
Tooltips showed as an empty black box (Windows and macOS alike) -- e.g. "Z top
limit" on the Controls page. Qt styles a tooltip with the style sheets of the
widget it belongs to and that widget's parents, nearest first, and two of
those meet badly:

* ``global_ss()`` sets ``* { color: TXT }`` -- dark text everywhere;
* containers such as the page stack (``main_window.py``) and the Controls page
  set a bare ``background: transparent;``, which Qt applies to everything
  under them -- the tooltip included.

So the tip got dark text on a transparent background, and a transparent
top-level window is drawn black. A ``QToolTip`` rule in the main window's
style sheet does not help: the nearer sheet wins whatever the specificity.

How
---
The fix goes where the problem is: those transparent containers use
:data:`TRANSPARENT` (or :data:`TRANSPARENT_NO_BORDER`) instead of the bare
declaration. ``* { ... }`` is exactly what a bare declaration means to Qt, so
every widget renders the same (checked pixel for pixel), and the ``QToolTip``
rule next to it in the same, nearest sheet is more specific than ``*``, so the
tooltip gets its own background.

The same leak hit the other popups that open from inside those containers: a
text field's right-click menu (Console, File Manager search) and a message
box opened from a page came out as black boxes too -- 213 of 218 such
containers, measured. :data:`POPUPS` adds a white ``QMenu`` and
``QMessageBox`` rule to the same sheets, by the same specificity argument.
Popups that style themselves (File Manager's rounded menu, the CAM tab's edit
menu) keep their own look: a widget's own sheet is nearer still.

Not an application event filter: that was the first version, and a Python
filter on the QApplication crashes PySide 6.11 as soon as a QWebEngineView
starts (the CAM tab) -- wrapping a new object for the filter fires another
event into the same filter mid-wrap, a segfault in getWrapperForQObject.
"""

from __future__ import annotations

from ui.theme import BORDER, LIGHT, TXT, TXT2, WHITE

#: The tooltip's look, as a rule to sit next to a container's own declarations.
STYLE = (f"QToolTip {{ background: {WHITE}; color: {TXT}; "
         f"border: 1px solid {BORDER}; padding: 3px 6px; }}")

#: Right-click menus and message boxes opened inside a transparent container:
#: white, like the tooltip, instead of inheriting the transparent background.
POPUPS = (
    f" QMenu {{ background: {WHITE}; border: 1px solid {BORDER}; padding: 4px; }}"
    f" QMenu::item {{ background: transparent; color: {TXT}; padding: 5px 22px; }}"
    f" QMenu::item:selected {{ background: {LIGHT}; }}"
    f" QMenu::item:disabled {{ color: {TXT2}; }}"
    f" QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}"
    f" QMessageBox {{ background: {WHITE}; }}"
    f" QMessageBox QLabel {{ color: {TXT}; }}"
    f" QMessageBox QPushButton {{ background: {LIGHT}; color: {TXT};"
    f" border: 1px solid {BORDER}; border-radius: 4px; padding: 5px 16px;"
    f" min-width: 60px; }}"
)

#: Replaces a bare ``background: transparent;`` without blacking out tooltips,
#: menus or message boxes.
TRANSPARENT = "* { background: transparent; } " + STYLE + POPUPS

#: Replaces a bare ``background: transparent; border: none;``, likewise.
TRANSPARENT_NO_BORDER = "* { background: transparent; border: none; } " + STYLE + POPUPS
