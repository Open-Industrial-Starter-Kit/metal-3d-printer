#!/usr/bin/env python3
"""Build images/app_icon.icns from images/app_icon.png, for the macOS bundle.

Why a separate file from `make_icon.py`
---------------------------------------
Windows wants a multi-resolution `.ico`, macOS wants an `.icns`, and neither
reads the other. PyInstaller puts whichever it is given into the bundle it is
building; handed the wrong one it warns and ships its own default icon, which
is how an app ends up looking like a generic terminal window.

An `.icns` is built by Apple's own `iconutil` from an `.iconset` directory —
one PNG per named size — so that is what this assembles. `iconutil` ships with
macOS, so there is nothing to install, and this script only runs there.

About the source
----------------
`app_icon.png` is 192x192. macOS asks for up to 1024x1024 (the 512x512@2x
slot), so the large sizes are upscaled and will be soft if you look at the icon
at Finder's largest setting. Everything at 192 and below — the Dock, the
Finder list, the app switcher, which is where this icon is actually seen — is a
clean downscale. Replace `app_icon.png` with a 1024x1024 master if the big
sizes ever matter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "images", "app_icon.png")
ICNS = os.path.join(ROOT, "images", "app_icon.icns")

#: (pixel size, iconset file name). Apple's naming: `icon_<pt>x<pt>[@2x].png`,
#: where the @2x file is twice the pixel size of the point size it names.
SIZES = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]


def main() -> int:
    if sys.platform != "darwin":
        print("icns files are a macOS thing; nothing to do here.")
        return 0
    if not os.path.isfile(SOURCE):
        print("missing %s" % SOURCE)
        return 1
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed to resample the icon: pip install pillow")
        return 1

    iconset = os.path.join(ROOT, "build", "app_icon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset, exist_ok=True)

    src = Image.open(SOURCE).convert("RGBA")
    for px, name in SIZES:
        # LANCZOS both ways: the best of the cheap filters going down, and the
        # least bad going up from a source smaller than the slot.
        src.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))

    r = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", ICNS],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr.strip() or "iconutil failed")
        return 1
    shutil.rmtree(iconset, ignore_errors=True)
    print("wrote %s (%d bytes) from a %dx%d source"
          % (os.path.relpath(ICNS, ROOT), os.path.getsize(ICNS), *src.size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
