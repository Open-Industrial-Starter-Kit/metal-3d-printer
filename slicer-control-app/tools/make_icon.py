#!/usr/bin/env python3
"""Build images/app_icon.ico from images/app_icon.png.

Why this exists
---------------
Windows takes an application's icon from a `.ico` compiled into the exe, and
PyInstaller can only put one there if it is given one. Handed a `.png` it tries
to convert, which needs Pillow — not a dependency of this project, and
explicitly excluded from the bundle. So the conversion silently did not happen
and the built exe carried PyInstaller's default icon.

The source was also wrong: `InMachines_Logo.png` is 432x96, a horizontal
wordmark. Windows icons are square, and at the 16 px the taskbar and title bar
use, a squashed banner is a smudge. `app_icon.png` is 192x192 and is already
what the running window uses, so the exe and the window now agree.

Why it writes the container by hand
-----------------------------------
An ICO is a 6-byte header, a 16-byte directory entry per image, and the images
themselves; since Vista each may be a PNG rather than a BMP. Qt can scale and
encode PNGs, so assembling the file needs nothing that is not already a
dependency. Adding Pillow to build a single 30 KB asset would be the larger
change.

Run it when the icon art changes:

    ./venv/bin/python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "images", "app_icon.png")
DST = os.path.join(ROOT, "images", "app_icon.ico")

#: What Windows asks for. 16 is the title bar and the taskbar; 32 the alt-tab
#: switcher; 48 the desktop; 256 Explorer's extra-large view. The rest are the
#: intermediate steps it picks between when a display is scaled.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(image, size: int) -> bytes:
    """*image* scaled to size x size, PNG-encoded."""
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    scaled = image.scaled(size, size, Qt.KeepAspectRatio,
                          Qt.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    if not scaled.save(buf, "PNG"):
        raise RuntimeError("could not encode the %d px frame" % size)
    buf.close()
    return bytes(data)


def build(src: str = SRC, dst: str = DST) -> str:
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QApplication([])            # QImage scaling needs a QGuiApplication

    image = QImage(src)
    if image.isNull():
        raise SystemExit("cannot read %s" % src)
    if image.width() != image.height():
        raise SystemExit(
            "%s is %dx%d; an icon source must be square"
            % (src, image.width(), image.height()))

    frames = [(size, png_bytes(image, size)) for size in SIZES]

    out = [struct.pack("<HHH", 0, 1, len(frames))]      # reserved, type=icon, n
    offset = 6 + 16 * len(frames)
    for size, blob in frames:
        out.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # 0 means 256 in an ICO directory
            size if size < 256 else 0,
            0,                           # colours in palette (0 = truecolour)
            0,                           # reserved
            1,                           # colour planes
            32,                          # bits per pixel
            len(blob),
            offset))
        offset += len(blob)
    out.extend(blob for _s, blob in frames)

    with open(dst, "wb") as fh:
        fh.write(b"".join(out))
    return dst


if __name__ == "__main__":
    path = build()
    print("wrote %s (%d bytes, %s)"
          % (path, os.path.getsize(path),
             " ".join("%d" % s for s in SIZES)))
