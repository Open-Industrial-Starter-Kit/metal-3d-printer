"""
desktop_entry.py — give the app its icon on Linux when nothing installed it.

GNOME (and most Wayland desktops) ignore the icon a window sets on itself.
The dock looks up a .desktop file whose name matches the app's id —
"piccola-cnc", set with QGuiApplication.setDesktopFileName in main.py — and
takes the icon from that. The .deb installs one; an AppImage or a run from
source installs nothing, so the dock showed a generic icon.

ensure() writes a per-user entry (~/.local/share/applications) pointing at
however the app is being run, so the icon is right and the app can be found
and pinned like any other. It stands aside when a system-wide entry exists
(the .deb), and only rewrites the file when it would change — e.g. the
AppImage was moved or replaced by a new version. Best effort: any failure is
swallowed, since a missing icon must never stop the app from starting.
"""

import os
import shutil
import sys

APP_ID = "piccola-cnc"          # must match setDesktopFileName in main.py

_HERE = os.path.dirname(os.path.abspath(__file__))
_ICON_SRC = os.path.join(_HERE, "images", "app_icon.png")


def _quote(arg: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec."""
    for ch in ('\\', '"', '`', '$'):
        arg = arg.replace(ch, '\\' + ch)
    return '"%s"' % arg


def _launch_command() -> list[str]:
    appimage = os.environ.get("APPIMAGE")
    if appimage:                                   # AppImage runtime sets this
        return [appimage]
    if getattr(sys, "frozen", False):              # unpacked PyInstaller build
        return [sys.executable]
    return [sys.executable, os.path.join(_HERE, "main.py")]   # from source


def _system_entry_exists() -> bool:
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return any(os.path.isfile(os.path.join(d, "applications", APP_ID + ".desktop"))
               for d in dirs.split(":") if d)


#: Marks entries this module wrote, so it only ever removes its own.
_MARKER = "X-Piccola-Generated=true\n"


def ensure() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = os.path.join(data, "applications", APP_ID + ".desktop")

        if _system_entry_exists():
            # The .deb is installed. A per-user entry left by an earlier
            # AppImage run would shadow it (user entries win) and still launch
            # the old AppImage — remove it, if it is one of ours.
            try:
                with open(path, encoding="utf-8") as fh:
                    ours = _MARKER in fh.read()
                if ours:
                    os.remove(path)
            except OSError:
                pass
            return

        # Absolute icon path rather than a theme name: a theme lookup under
        # ~/.local/share/icons can need an icon-cache refresh before the shell
        # sees it, an absolute path never does.
        icon = os.path.join(data, "icons", APP_ID + ".png")
        if os.path.isfile(_ICON_SRC):
            os.makedirs(os.path.dirname(icon), exist_ok=True)
            if (not os.path.isfile(icon)
                    or os.path.getsize(icon) != os.path.getsize(_ICON_SRC)):
                shutil.copyfile(_ICON_SRC, icon)

        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Piccola CNC\n"
            "GenericName=CNC Machine Control\n"
            "Comment=Control and CAM for the Piccola CNC milling machine\n"
            "Exec=%s\n"
            "Icon=%s\n"
            "Terminal=false\n"
            "Categories=Development;Engineering;\n"
            "StartupWMClass=%s\n"
            + _MARKER
        ) % (" ".join(_quote(a) for a in _launch_command()), icon, APP_ID)
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == entry:
                    return
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(entry)
        os.replace(tmp, path)                      # never a half-written entry
    except Exception:
        pass
