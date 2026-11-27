"""
ui/pages/dialogs.py
Modal dialogs matching the Piccola CNC desktop design language: white card, large
icon, big title, warning rows, and gray rounded check/close buttons.

Includes the multi-phase procedure flows mirrored from the mobile app:
probe Z0, tool length, detect XY0, detect center, height map, rotary mode,
homing options, plus generic confirm / input dialogs.
"""

import math
import warnings
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import QGuiApplication, QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QLabel,
    QLineEdit, QPushButton, QButtonGroup, QRadioButton, QFrame, QScrollArea,
)

from ui.theme import WHITE, LIGHT, BORDER, PANEL, TXT, TXT2, TXT3, RUN_C, ERR_C
from ui.widgets import lbl, btn, rounded_dialog_card
from ui.scale import fs, px, bind, autoscale
from ui import icons
from ui.tooltips import TRANSPARENT


#: Left/right padding inside a FlowDialog's white card. Shared so a phase row
#: can work out the width its own content will be laid out at.
_CARD_SIDE_MARGIN = 36

# ── Shared building blocks ────────────────────────────────────────────────────

def _dlg_button(glyph: str, w: int = 110) -> QPushButton:
    """Gray rounded dialog button with a large glyph (✓ / ✕ / text). The "✓"
    confirm glyph renders as the mobile app's Material "check" icon."""
    is_check = glyph == "✓"
    b = QPushButton("" if is_check else glyph)
    b.setCursor(Qt.PointingHandCursor)

    def style(x):
        # Sized for a glyph or a short word; a longer label ("Export anyway")
        # widens its own button rather than being clipped. Measured at the
        # font size set below, so it holds at every UI scale.
        width = px(w)
        if not is_check and len(glyph) > 2:
            f = QFont(x.font())
            f.setPixelSize(fs(20))
            width = max(width, QFontMetrics(f).horizontalAdvance(glyph) + px(36))
        x.setFixedSize(width, px(52))
        x.setStyleSheet(f"""
            QPushButton {{
                background: {PANEL}; border: none; border-radius: {px(10)}px;
                font-size: {fs(20)}px; color: {TXT};
            }}
            QPushButton:hover   {{ background: #D2D2D2; }}
            QPushButton:pressed {{ background: #C8C8C8; }}
            QPushButton:disabled {{ background: #EDEDED; color: {TXT3}; }}
        """)
        if is_check:
            x.setIcon(icons.icon("check", px(24), TXT))
            x.setIconSize(icons.icon_size(px(24)))

    return autoscale(b, style)


def _setup_diagram(icon_name: str, w: int = 170, h: int = 96) -> QLabel:
    """Centred setup illustration (spindle / probe over the bed with the bed
    line) shown in a scan flow's confirm phase, mirroring the mobile app."""
    d = QLabel()
    d.setAlignment(Qt.AlignCenter)
    d.setStyleSheet(TRANSPARENT)
    autoscale(d, lambda x: x.setPixmap(
        icons.pixmap_box(icon_name, px(w), px(h), TXT)))
    return d


class _BedLaserAreaDiagram(QWidget):
    """Plan view of the bed with the laser-reachable area marked — the mobile
    app's MarkedWorkAreaIcon (App.tsx ~1503), drawn to scale from this
    machine's own mount offset instead of as a fixed illustration.

    The distance sensor is mounted behind the spindle, so placing the laser
    over a point costs that much extra spindle travel (`laser = spindle -
    offset`, firmware MiniCNC_CPUB.ino ~799). The far X and Y strips of the
    bed are therefore spindle-only: they can be milled, but no laser
    procedure — edge probing, center finding, height map, point cloud — can
    reach them. Anything the sensor must see has to sit inside the mark.

    Axes are the plan-view convention the mark depends on: +X right, +Y back
    (up), so the unreachable strips are the right-hand and top edges.
    """

    #: Scale grid only — spacing is a round number for reading distances off
    #: the drawing, and is not meant to be the bed's own engraved lines.
    _GRID_MM = 25.0
    _C_REACH = QColor(TXT)              # reachable: dark fill, white grid
    _C_OUT = QColor("#C0C0C0")          # spindle-only: grey fill, dark grid
    _C_OUT_GRID = QColor("#8C8C8C")

    def __init__(self, conn, side: int = 92):
        super().__init__()
        self._conn = conn
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        autoscale(self, lambda x: x.setFixedSize(px(side), px(side)))

    def paintEvent(self, _e) -> None:
        bed_w, bed_d = self._conn.work_area_mm()
        dx, dy = self._conn.laser_offset_mm()

        p = QPainter(self)
        # Off: a 1 px grid on a small drawing goes muddy when smoothed.
        p.setRenderHint(QPainter.Antialiasing, False)

        edge = max(1, int(round(px(1.4))))
        side = min(self.width(), self.height()) - edge
        bed = QRectF((self.width() - side) / 2, (self.height() - side) / 2,
                     side, side)
        sx, sy = side / bed_w, side / bed_d

        # +Y is up, so the reachable area sits against the bottom-left corner.
        reach = QRectF(bed.left(),
                       bed.bottom() - max(0.0, bed_d - dy) * sy,
                       max(0.0, bed_w - dx) * sx,
                       max(0.0, bed_d - dy) * sy)

        p.fillRect(bed, self._C_OUT)
        p.fillRect(reach, self._C_REACH)

        # Grid twice, clipped: dark across the whole bed, then white where it
        # crosses the mark — the mobile icon's read, and it keeps the scale
        # legible on both fills.
        for clip, color in ((bed, self._C_OUT_GRID), (reach, QColor(WHITE))):
            p.save()
            p.setClipRect(clip)
            p.setPen(QPen(color, 1))
            n = 1
            while n * self._GRID_MM < bed_w:
                gx = bed.left() + n * self._GRID_MM * sx
                p.drawLine(QPointF(gx, bed.top()), QPointF(gx, bed.bottom()))
                n += 1
            n = 1
            while n * self._GRID_MM < bed_d:
                gy = bed.bottom() - n * self._GRID_MM * sy
                p.drawLine(QPointF(bed.left(), gy), QPointF(bed.right(), gy))
                n += 1
            p.restore()

        p.setPen(QPen(QColor(TXT), edge))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bed)


def _laser_area_row(conn, note: str = "", dialog_w: int = 520) -> QWidget:
    """Bed diagram + caption showing where the laser sensor can be driven.

    Shown by every flow that scans with it, because the reachable area is
    smaller than the bed and nothing else in the UI says so — a material
    parked on the far right of the bed simply fails to probe."""
    reach_w, reach_d = conn.laser_area_mm()
    bed_w, bed_d = conn.work_area_mm()
    dx, dy = conn.laser_offset_mm()

    side, pad, gap = 92, 20, 14
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(px(pad), 0, px(pad), 0)
    h.setSpacing(px(gap))
    h.addWidget(_BedLaserAreaDiagram(conn, side), 0, Qt.AlignTop)

    # Nested layouts do not propagate height-for-width, so a word-wrapped
    # label in this column would report the height of its *unwrapped* width
    # and the phase would be sized too short — squeezing the rows around it.
    # Pinning the column to the width it will actually get makes the wrapped
    # height it reports the real one.
    text_w = (px(dialog_w) - 2 * px(_CARD_SIDE_MARGIN) - 2 * px(pad)
              - px(side) - px(gap))
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(px(3))
    head = lbl(f"Laser area — {reach_w:.0f} × {reach_d:.0f} mm",
               sz=13, bold=True, color=TXT)
    head.setWordWrap(True)
    col.addWidget(head)
    # The two fills are named in the text rather than in a legend: these
    # dialogs are already close to the height of a small screen.
    body = lbl(f"The sensor sits {dx:.1f} mm (X) and {dy:.1f} mm (Y) off the "
               f"spindle, so it never reaches the grey strips at the right and "
               f"back of the {bed_w:.0f} × {bed_d:.0f} mm bed. Keep the "
               f"material inside the dark area."
               + (f" {note}" if note else ""),
               sz=12, color=TXT2)
    body.setWordWrap(True)
    col.addWidget(body)
    col.addStretch()

    for text_lbl in (head, body):
        text_lbl.setFixedWidth(text_w)
        # A wrapped QLabel's own sizeHint is a guess at a pleasing box, not
        # the height at the width it ends up with, so ask for that height
        # directly — otherwise the phase is measured short and the widgets
        # above it get squeezed to make room.
        text_lbl.setMinimumHeight(text_lbl.heightForWidth(text_w))
    h.addLayout(col, 1)
    return w


def _warning_row(text: str, icon_name: str | None = None,
                 dialog_w: int = 520) -> QWidget:
    """⚠ + wrapped text row used in confirm phases.

    The label is pinned to the width it will actually be given, for the same
    reason `_laser_area_row` does it: a wrapped QLabel's sizeHint is a guess at
    a pleasing box, not the height at that width, so a row long enough to wrap
    past the guess is measured short and drawn clipped. Two-line rows got away
    with it; the first row that needed four did not.
    """
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(px(14))
    if icon_name:
        il = QLabel()
        il.setPixmap(icons.pixmap(icon_name, px(30), TXT))
        il.setFixedWidth(px(36))
        il.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        il.setStyleSheet(TRANSPARENT)
        h.addWidget(il)
    else:
        tri = QLabel("⚠")
        tri.setFixedWidth(px(36))
        tri.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        autoscale(tri, lambda x: x.setStyleSheet(
            f"background: transparent; font-size: {fs(28)}px; color: #3A3A3A;"
        ))
        h.addWidget(tri)
    t = lbl(text, sz=14, color=TXT)
    t.setWordWrap(True)
    text_w = px(dialog_w) - 2 * px(_CARD_SIDE_MARGIN) - px(36) - px(14)
    t.setFixedWidth(text_w)
    t.setMinimumHeight(t.heightForWidth(text_w))
    h.addWidget(t, 1)
    return w


def _step_row(number: int, text: str, extra: QWidget | None = None,
              dialog_w: int = 520) -> QWidget:
    """Numbered checklist row: a round step number + wrapped text (rich text
    allowed, e.g. <b>), with an optional widget under the text. Laid out and
    measured like `_warning_row`, so the two sit together in one phase."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(px(14))
    num = QLabel(str(number))
    num.setAlignment(Qt.AlignCenter)
    autoscale(num, lambda x: (
        x.setFixedSize(px(30), px(30)),
        x.setStyleSheet(
            f"background: {PANEL}; color: {TXT}; border-radius: {px(15)}px; "
            f"font-size: {fs(15)}px; font-weight: bold;"),
    ))
    nc = QVBoxLayout()
    nc.setContentsMargins(px(3), 0, px(3), 0)
    nc.addWidget(num)
    nc.addStretch(1)
    h.addLayout(nc)
    col = QVBoxLayout()
    col.setContentsMargins(0, px(4), 0, 0)
    col.setSpacing(px(8))
    t = lbl(text, sz=14, color=TXT, align=Qt.AlignLeft | Qt.AlignTop)
    t.setWordWrap(True)
    text_w = px(dialog_w) - 2 * px(_CARD_SIDE_MARGIN) - px(36) - px(14)
    t.setFixedWidth(text_w)
    t.setMinimumHeight(t.heightForWidth(text_w))
    col.addWidget(t)
    if extra is not None:
        col.addWidget(extra, 0, Qt.AlignLeft)
    h.addLayout(col, 1)
    return w


def _small_button(text: str) -> QPushButton:
    """Compact grey button for an action inside a phase's body (the big
    `_dlg_button` is for the ✓ / ✕ row at the bottom)."""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    return autoscale(b, lambda x: (
        x.setFixedHeight(px(36)),
        x.setStyleSheet(f"""
            QPushButton {{
                background: {PANEL}; border: none; border-radius: {px(8)}px;
                font-size: {fs(14)}px; color: {TXT};
                padding: 0 {px(16)}px;
            }}
            QPushButton:hover   {{ background: #D2D2D2; }}
            QPushButton:pressed {{ background: #C8C8C8; }}
        """),
    ))


def _section_label(text: str) -> QLabel:
    """Small bold section header inside a procedure phase, indented to line up
    with the parameter widgets (which carry a px(40) left margin) below it."""
    w = QLabel(text)
    w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def style(x):
        x.setContentsMargins(px(40), px(8), px(40), 0)
        x.setStyleSheet(f"color: {TXT2}; background: transparent; "
                        f"font-size: {fs(13)}px; font-weight: bold;")

    return autoscale(w, style)


def _option_card(name: str, desc: str, checked: bool, group: QButtonGroup,
                 idx: int, value: str, on_change=None,
                 dialog_w: int = 520, indent: int = 40) -> QWidget:
    """Selectable option card (mobile parity, App.tsx ~3648): a bold name and a
    one-line description on the left, a radio indicator on the right, and a
    highlighted border/background when selected — the whole card is clickable.
    Registers a hidden-label radio into *group* (id *idx*, ``density`` = value)
    so existing checkedButton() lookups keep working."""
    card = QFrame()
    card.setObjectName("optionCard")
    card.setCursor(Qt.PointingHandCursor)
    rl = QHBoxLayout(card)

    col = QVBoxLayout()
    col.addWidget(lbl(name, sz=14, bold=True))
    d = lbl(desc, sz=12, color=TXT2)
    d.setWordWrap(True)
    # Pinned to the width it will be given, for the reason `_warning_row`
    # is: a wrapped QLabel's sizeHint is a guess at a pleasing box, not its
    # height at that width, so a description longer than a line or two is
    # measured short and drawn clipped. One-line descriptions got away with it
    # until a card had something to explain.
    text_w = (px(dialog_w) - 2 * px(_CARD_SIDE_MARGIN) - 2 * px(indent)
              - px(14) * 2 - px(12) - px(22))
    d.setFixedWidth(text_w)
    d.setMinimumHeight(d.heightForWidth(text_w))
    col.addWidget(d)
    rl.addLayout(col, 1)

    rb = QRadioButton()
    rb.setChecked(checked)
    rb.setCursor(Qt.PointingHandCursor)
    rb.setProperty("density", value)
    group.addButton(rb, idx)
    rl.addWidget(rb, 0, Qt.AlignVCenter)

    def _restyle():
        sel = rb.isChecked()
        card.setStyleSheet(
            f"#optionCard {{ background: {LIGHT if sel else WHITE}; "
            f"border: {px(1.5)}px solid {TXT if sel else BORDER}; "
            f"border-radius: {px(10)}px; }}")

    rb.toggled.connect(lambda *_: _restyle())
    if on_change is not None:
        rb.toggled.connect(on_change)
    card.mousePressEvent = lambda _e: rb.setChecked(True)
    autoscale(card, lambda x: (
        rl.setContentsMargins(px(14), px(12), px(14), px(12)),
        rl.setSpacing(px(12)),
        col.setSpacing(px(4)),
        _restyle(),
    ))
    return card


class _Spinner(QLabel):
    """Lightweight text spinner for in-progress phases."""

    _FRAMES = ["◐", "◓", "◑", "◒"]

    def __init__(self, size: int = 44):
        super().__init__(self._FRAMES[0])
        self._i = 0
        self._size = size
        self.setAlignment(Qt.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setStyleSheet(
            f"background: transparent; font-size: {fs(self._size)}px; color: {TXT2};"
        )

    def _tick(self):
        self._i = (self._i + 1) % len(self._FRAMES)
        self.setText(self._FRAMES[self._i])


class FlowDialog(QDialog):
    """Frameless white-card dialog hosting swappable phase widgets."""

    def __init__(self, parent=None, width: int = 520):
        super().__init__(parent)
        self._width = width
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._card = rounded_dialog_card(self)
        self._lay = QVBoxLayout(self._card)
        self._phase_widget: QWidget | None = None
        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedWidth(px(self._width))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(16)}px; }}"
        )
        self._lay.setContentsMargins(px(_CARD_SIDE_MARGIN), px(32),
                                     px(_CARD_SIDE_MARGIN), px(28))
        self._lay.setSpacing(px(18))

    # phase assembly ------------------------------------------------------

    def set_phase(self, w: QWidget) -> None:
        if self._phase_widget is not None:
            self._lay.removeWidget(self._phase_widget)
            self._phase_widget.deleteLater()
        self._phase_widget = w
        self._lay.addWidget(w)
        self.adjustSize()
        self._fit_to_screen()
        # Again once the old phase is really gone (deleteLater): until then
        # the card kept the previous phase's height, so a short phase after
        # a tall one sat stretched, its rows spread over empty space. A timer
        # the dialog owns, so a dialog closed in the meantime takes it along
        # instead of being called after it is deleted.
        refit = getattr(self, "_refit_timer", None)
        if refit is None:
            refit = self._refit_timer = QTimer(self)
            refit.setSingleShot(True)
            refit.setInterval(0)
            refit.timeout.connect(self.adjustSize)
        refit.start()

    def showEvent(self, e):
        super().showEvent(e)
        # Word-wrapped labels only report their real height once they have been
        # laid out at a known width, so the height check has to run again here:
        # before the first show, adjustSize() under-measures the tall phases.
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Scroll a phase that is taller than the screen rather than letting it
        run off the bottom.

        These cards are laid out at a fixed width and grow with their content,
        and the tallest of them (XY0 detection, with the laser-area diagram)
        is close to the height of a 1280x800 display — where the scale factor
        is still 1.0, so nothing shrinks it. A phase that fits is left exactly
        as it was; none of them gains a scroll area it does not need."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if (screen is None or self._phase_widget is None
                or isinstance(self._phase_widget, QScrollArea)):
            return
        m = self._lay.contentsMargins()
        cap = int(screen.availableGeometry().height() * 0.94)
        if self.height() <= cap:
            return
        inner = self._phase_widget
        self._lay.removeWidget(inner)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setStyleSheet(TRANSPARENT)
        sa.setWidget(inner)                 # reparents; keeps the card white
        sa.viewport().setStyleSheet(TRANSPARENT)
        sa.setFixedHeight(max(px(200), cap - m.top() - m.bottom()))
        # Pin the phase to the card's content width. A scroll area otherwise
        # honours its child's own width hint, and word-wrapped labels ask for
        # far more than the card is wide — the phase would be cut off down the
        # right-hand side instead of wrapping.
        inner_w = max(px(120), px(self._width) - m.left() - m.right()
                      - sa.verticalScrollBar().sizeHint().width())
        inner.setFixedWidth(inner_w)
        # Narrower than it was measured for, so anything wrapped needs its
        # height again — otherwise the scroll area scrolls to the bottom of a
        # box that is still too short for its text.
        self._measure_wrapped(inner, inner_w)
        self._phase_widget = sa
        self._lay.addWidget(sa)
        # activate() before adjustSize(): the layout is still holding the hint
        # it cached for the un-scrolled phase, and adjustSize() believes it —
        # leaving the window at the full height of the content it was supposed
        # to have just put behind a scrollbar.
        self._lay.activate()
        self.adjustSize()

    def _content_width(self) -> int:
        """The width a phase's rows are actually laid out at."""
        return max(px(80), px(self._width) - 2 * px(_CARD_SIDE_MARGIN))

    def _measure_wrapped(self, root: QWidget, width: int) -> None:
        """Give every word-wrapped label under *root* the height its text needs.

        A wrapped QLabel's sizeHint is a guess at a pleasing box, not its height
        at the width it will be given. The layout believes the guess, so the
        card comes out shorter than its own message and the text is drawn
        clipped at BOTH ends — the label centres what it can fit, so the first
        line rides up over the title and the last is cut off above the buttons.
        A two-line message got away with it; the first four-line one did not.

        Individual rows in this file have been pinning themselves for exactly
        this reason (`_warning_row`, `_option_card`, `_laser_area_row`). Doing
        it here instead covers every phase, including the plain message of a
        confirm or an info dialog, which is where it was going wrong.

        Height only, never width: fixing the width here would make a phase that
        later goes into a scroll area (`_fit_to_screen` makes it narrower by a
        scrollbar) too wide to fit, trading a vertical clip for a horizontal
        one. Idempotent, and safe over a row that pinned its own narrower
        width — it is measured at that width and keeps the height it had.
        """
        for label in root.findChildren(QLabel):
            if not label.wordWrap() or not label.text():
                continue
            # At the width it will really get: a row pinned WIDER than this
            # card (a builder's dialog_w default that does not match) would
            # otherwise be measured for a width it never has, and come out
            # short by a line or two.
            h = label.heightForWidth(min(width, label.maximumWidth()))
            # This can only ever RAISE the height, so a row that measured
            # itself keeps what it asked for and no guard is needed: QLabel
            # folds its own minimum into the height it reports for a width, so
            # heightForWidth never comes back smaller than the minimum already
            # set. (The same behaviour is why `_update_dir_label` has to clear
            # the minimum before re-measuring a label whose text changed — its
            # measurement would otherwise be stuck at the previous text's.)
            if h > 0:
                label.setMinimumHeight(h)

    def build_phase(
        self,
        icon_name: str | None,
        title: str,
        rows: list,                      # list[QWidget] body rows
        buttons: list,                   # list[(glyph, callback, enabled)]
        spinner: bool = False,
        big_icon_glyph: str | None = None,
        icon_color: str = TXT,
    ) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(px(18))

        if spinner:
            v.addWidget(_Spinner(), alignment=Qt.AlignHCenter)
        elif big_icon_glyph == "✓":
            # Success icon: mobile app's Material "check" (matches the buttons).
            il = QLabel()
            il.setAlignment(Qt.AlignCenter)
            il.setStyleSheet(TRANSPARENT)
            autoscale(il, lambda x, c=icon_color: x.setPixmap(
                icons.pixmap("check", px(52), c)))
            v.addWidget(il)
        elif big_icon_glyph:
            g = QLabel(big_icon_glyph)
            g.setAlignment(Qt.AlignCenter)
            autoscale(g, lambda x: x.setStyleSheet(
                f"background: transparent; font-size: {fs(52)}px; color: {icon_color};"
            ))
            v.addWidget(g)
        elif icon_name:
            il = QLabel()
            il.setPixmap(icons.pixmap(icon_name, px(48), TXT))
            il.setAlignment(Qt.AlignCenter)
            il.setStyleSheet(TRANSPARENT)
            v.addWidget(il)

        t = lbl(title, sz=22, bold=False, color=TXT, align=Qt.AlignCenter)
        t.setWordWrap(True)
        v.addWidget(t)

        for row in rows:
            v.addWidget(row)

        # Recorded so a phase can enable/disable its own buttons later without
        # hunting for them in the widget tree -- a phase whose body contains
        # buttons of its own (the facing form's corner picker, say) cannot tell
        # them apart from these by type.
        self.phase_buttons: list[QPushButton] = []
        if buttons:
            br = QHBoxLayout()
            br.setSpacing(px(40))
            br.addStretch()
            for glyph, cb, enabled in buttons:
                b = _dlg_button(glyph)
                b.setEnabled(enabled)
                if cb:
                    b.clicked.connect(cb)
                br.addWidget(b)
                self.phase_buttons.append(b)
            br.addStretch()
            v.addLayout(br)
        self._measure_wrapped(w, self._content_width())
        return w

    # convenience phases ---------------------------------------------------

    def show_progress(self, title: str, message: str, on_cancel) -> None:
        msg = lbl(message, sz=14, color=TXT2, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, title, [msg],
            [("✕", on_cancel, True)], spinner=True,
        ))

    def show_result(self, success: bool, title: str, message: str) -> None:
        msg = lbl(message, sz=14, color=TXT2, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, title, [msg],
            [("✓", self.accept, True)],
            big_icon_glyph="✓" if success else "✕",
            icon_color=RUN_C if success else ERR_C,
        ))


def cache_clear_message() -> tuple:
    """Build (message, has_files) for the clear-cache confirmation, listing the
    cached files with sizes and the total to be cleared."""
    from connection import Connection

    def _sz(b: float) -> str:
        if b >= 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} Mb"
        if b >= 1024:
            return f"{b / 1024:.1f} Kb"
        return f"{b:.0f} b"

    files = Connection.cache_contents()
    if not files:
        return "The local cache is empty — nothing to clear.", False
    lines = [f"{name}  —  {_sz(size)}" for name, size in files[:12]]
    if len(files) > 12:
        lines.append(f"… and {len(files) - 12} more")
    total = sum(size for _n, size in files)
    lines.append("")
    lines.append(f"Total: {_sz(total)} ({len(files)} "
                 f"file{'' if len(files) == 1 else 's'})")
    lines.append("Files on the machine are not affected.")
    return "\n".join(lines), True


# ── Generic confirm / info dialogs ────────────────────────────────────────────

class ConfirmDialog(FlowDialog):
    """Design-style confirmation: title, ⚠ icon, message, confirm / cancel
    buttons. Defaults to ✓ / ✕ glyphs; pass yes_label/no_label to override."""

    def __init__(self, title: str, message: str, parent=None,
                 yes_label: str = "✓", no_label: str = "✕"):
        super().__init__(parent, width=480)
        msg = lbl(message, sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, title, [msg],
            [(yes_label, self.accept, True), (no_label, self.reject, True)],
            big_icon_glyph="⚠", icon_color="#3A3A3A",
        ))


class InfoDialog(FlowDialog):
    """Design-style notice: title, ✓ icon, message, single ✓ button."""

    def __init__(self, title: str, message: str, parent=None,
                 success: bool = True):
        super().__init__(parent, width=500)
        msg = lbl(message, sz=14, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, title, [msg],
            [("✓", self.accept, True)],
            big_icon_glyph="✓" if success else "✕",
            icon_color=RUN_C if success else ERR_C,
        ))


class FileExistsDialog(FlowDialog):
    """An upload whose name is already on the machine: replace the file
    there, keep both (upload under *alt_name*), or cancel. The answer is in
    :attr:`choice` — "replace", "keep" or None."""

    def __init__(self, name: str, alt_name: str, parent=None):
        super().__init__(parent, width=600)
        self.choice: str | None = None
        msg = lbl(f"{name} is already on the machine.\n\n"
                  f"Replace it, or keep both and upload the new file "
                  f"as {alt_name}?",
                  sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "File already exists", [msg],
            [("Replace", lambda: self._pick("replace"), True),
             ("Keep both", lambda: self._pick("keep"), True),
             ("Cancel", self.reject, True)],
            big_icon_glyph="⚠", icon_color="#3A3A3A",
        ))

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


#: How long the spindle is given to coast to a full stop after M5 before a job
#: may start, in seconds. GRBL reports 0 rpm as soon as M5 lands, while the
#: motor is still turning.
SPINDLE_SPINDOWN_S = 5


class SpindleRunningDialog(FlowDialog):
    """Stops the spindle before a job starts.

    Starting a job with the spindle already running (left on from the spindle
    controls) has destroyed the spindle motor driver: the job start resets the
    firmware's spindle state while the motor is still turning at speed. So
    Start goes through here first: "Spindle is running" → Stop spindle (M5) →
    a SPINDLE_SPINDOWN_S countdown while it coasts down → "Start the job now?".
    Accepted means: stopped, start the job. With *running* False the spindle
    is already off but stopped too recently, so it opens on the countdown
    (*wait_s* left).
    """

    def __init__(self, conn, running: bool, wait_s: float = SPINDLE_SPINDOWN_S,
                 parent=None):
        super().__init__(parent, width=540)
        self._conn = conn
        self._rpm: int | None = None          # readings since the M5
        self._until = 0.0
        self._asked = False
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        conn.spindle_rpm_changed.connect(self._on_rpm)
        if running:
            self._show_running()
        else:
            self._start_countdown(wait_s)

    def _show_running(self, still: bool = False) -> None:
        text = ("The spindle is still reported as running. "
                if still else "") + (
            "The spindle must be stopped before a job starts: starting a job "
            "with the spindle already running can damage the spindle motor "
            "driver.")
        msg = lbl(text, sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Spindle is running", [msg],
            [("Stop spindle", self._stop, True), ("Cancel", self.reject, True)],
            big_icon_glyph="⚠", icon_color="#3A3A3A",
        ))

    def _stop(self) -> None:
        self._conn.send_raw("M5")
        self._start_countdown(SPINDLE_SPINDOWN_S)

    def _start_countdown(self, seconds: float) -> None:
        import time
        self._rpm = None
        self._asked = False
        self._until = time.monotonic() + seconds
        self._count = lbl("", sz=15, color=TXT2, align=Qt.AlignCenter)
        self._count.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Stopping the spindle…", [self._count],
            [("Cancel", self.reject, True)], spinner=True,
        ))
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        import math
        import time
        left = self._until - time.monotonic()
        if left > 0:
            self._count.setText(
                f"Waiting for it to come to a full stop… {math.ceil(left)} s")
            # One fresh reading near the end, so the verdict is not stale
            # (earlier replies may answer polls sent before the M5)
            if left < 2 and not self._asked:
                self._asked = True
                self._rpm = None
                self._conn.get("currentSpindleRPM")
            return
        self._timer.stop()
        if self._rpm is not None and self._rpm > 0:
            self._show_running(still=True)      # the M5 did not take
        else:
            self._show_ready()

    def _show_ready(self) -> None:
        msg = lbl("The spindle has stopped. Start the job now?",
                  sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Spindle stopped", [msg],
            [("Start", self.accept, True), ("Cancel", self.reject, True)],
            big_icon_glyph="✓", icon_color=RUN_C,
        ))

    def _on_rpm(self, rpm: int) -> None:
        self._rpm = rpm

    def done(self, result) -> None:
        self._timer.stop()
        try:
            self._conn.spindle_rpm_changed.disconnect(self._on_rpm)
        except (RuntimeError, TypeError):
            pass
        super().done(result)


class OpeningFileDialog(FlowDialog):
    """Modal 'Opening <file>' notice while a job file opens.

    Opening a large file keeps the app busy for a while: the machine has to
    open it, then the preview reads the cached toolpath (and the job-time plan
    runs alongside). The UI can stutter meanwhile, so this says what is going
    on and keeps the rest of the app out of reach until the file is ready.
    It closes itself: when the machine reports the file open and no toolpath
    read is running, when that read ends, when another file is opened, when
    the machine goes away, or after a timeout. Esc does not close it.
    """

    #: The machine reports the opened file through its regular poll
    _MACHINE_TIMEOUT_MS = 20_000
    #: A 33 MB toolpath reads in a few seconds; this is only a backstop
    _READ_TIMEOUT_MS = 180_000

    def __init__(self, name: str, conn, preview, parent=None):
        super().__init__(parent, width=460)
        self._name = name
        self._conn = conn
        self._preview = preview
        self._msg = lbl("Waiting for the machine to open the file…",
                        sz=14, color=TXT2, align=Qt.AlignCenter)
        self._msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, f"Opening {name}", [self._msg], [], spinner=True))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)
        self._timer.start(self._MACHINE_TIMEOUT_MS)

        self._links = [
            (conn.opened_file_changed, self._on_opened),
            (conn.serial_state_changed, self._on_link_changed),
            (conn.ws_connected_changed, self._on_link_changed),
            (preview.read_progress, self._on_progress),
            (preview.read_finished, self._on_read_done),
        ]
        for sig, slot in self._links:
            sig.connect(slot)

    def _on_opened(self, opened) -> None:
        # None is ignored: while the machine is still opening the file, its
        # poll can answer "no file open" for a moment.
        if opened is None:
            return
        if opened != self._name:
            self._finish()
        elif self._preview.is_reading():
            self._msg.setText("Reading toolpath… 0%")
            self._timer.start(self._READ_TIMEOUT_MS)
        else:
            self._finish()

    def _on_progress(self, pct: int) -> None:
        self._msg.setText(f"Reading toolpath… {pct}%")

    def _on_read_done(self, _name: str) -> None:
        self._finish()

    def _on_link_changed(self, *_) -> None:
        if not (self._conn.is_serial_connected or self._conn.is_ws_active):
            self._finish()

    def reject(self) -> None:
        pass                                  # Esc must not dismiss it

    def _finish(self) -> None:
        self._timer.stop()
        for sig, slot in self._links:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._links = []
        self.done(QDialog.Accepted)
        self.deleteLater()


# ── Procedure flows ───────────────────────────────────────────────────────────

class _ProcedureFlow(FlowDialog):
    """Base for probe/laser macros driven by procedure_completed signals."""

    completion_type = ""        # msg type that finishes the procedure

    # Laser readiness gate: the distance sensor takes ~1.5 s to initialize
    # after enableLaserSensor, so error replies retry until the timeout.
    _LASER_CHECK_TIMEOUT_MS = 6000
    _LASER_CHECK_RETRY_MS = 800

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._laser_on_ready = None            # callback while a check runs
        self._laser_signals_connected = False
        self._laser_timeout_timer: QTimer | None = None
        self._laser_retry_timer: QTimer | None = None
        conn.procedure_completed.connect(self._on_completed)

    def done(self, r):
        # done() catches every close path (accept/reject/Esc); closeEvent does
        # not fire for programmatic dialog closes.
        self._laser_check_cleanup()
        for sig, slot in (
                (self._conn.procedure_completed, self._on_completed),
                (self._conn.machine_status_changed, getattr(self, "_on_status", None)),
                (self._conn.machine_flags_changed, getattr(self, "_on_flags", None)),
                (self._conn.machine_status_changed, getattr(self, "_on_install_status", None)),
                (self._conn.machine_status_changed, getattr(self, "_on_position_status", None)),
                (self._conn.opened_file_bounds_received,
                 getattr(self, "_on_job_bounds", None))):
            if slot is None:
                continue
            # A flow closed before it connected a slot has nothing to undo, and
            # PySide reports that as a RuntimeWarning on stderr rather than
            # raising -- noise in the log, not a fault.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().done(r)

    # ── Laser readiness gate (shared by the height-map / point-cloud flows) ──

    def _laser_check_start(self, on_ready) -> None:
        """Enable the distance laser if needed, then poll distanceLaserMm
        until a reading arrives (→ on_ready()) or the timeout elapses (→
        failure phase). Mirrors the mobile app's readiness check before
        laser procedures; warm-up errors retry instead of failing."""
        if not self._conn.flags().get("laserSensorEnabled", False):
            self._conn.cmd("enableLaserSensor")
        self._laser_on_ready = on_ready
        self._conn.laser_distance_changed.connect(self._on_laser_ready)
        self._conn.laser_distance_error.connect(self._on_laser_error)
        self._laser_signals_connected = True
        self._laser_timeout_timer = QTimer(self)
        self._laser_timeout_timer.setSingleShot(True)
        self._laser_timeout_timer.setInterval(self._LASER_CHECK_TIMEOUT_MS)
        self._laser_timeout_timer.timeout.connect(self._on_laser_timeout)
        self._laser_timeout_timer.start()
        self._laser_retry_timer = QTimer(self)
        self._laser_retry_timer.setSingleShot(True)
        self._laser_retry_timer.setInterval(self._LASER_CHECK_RETRY_MS)
        self._laser_retry_timer.timeout.connect(self._laser_check_poll)
        self._laser_check_poll()

    def _laser_check_poll(self) -> None:
        if self._laser_on_ready is not None:
            self._conn.get("distanceLaserMm")

    def _laser_check_cleanup(self) -> None:
        self._laser_on_ready = None
        for t in (self._laser_timeout_timer, self._laser_retry_timer):
            if t is not None:
                t.stop()
        self._laser_timeout_timer = None
        self._laser_retry_timer = None
        if not self._laser_signals_connected:
            return
        self._laser_signals_connected = False
        for sig, slot in ((self._conn.laser_distance_changed, self._on_laser_ready),
                          (self._conn.laser_distance_error, self._on_laser_error)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _on_laser_ready(self, _mm: float) -> None:
        cb = self._laser_on_ready
        self._laser_check_cleanup()
        if cb is not None:
            cb()

    def _on_laser_error(self, _error: str) -> None:
        # Warm-up in progress — poll again shortly; the timeout decides.
        if self._laser_on_ready is not None and self._laser_retry_timer:
            self._laser_retry_timer.start()

    def _on_laser_timeout(self) -> None:
        if self._laser_on_ready is None:
            return
        self._laser_check_cleanup()
        self.show_result(False, "Laser sensor not ready",
                         "No distance reading was received from the laser "
                         "sensor — the material surface cannot be read. "
                         "Check the sensor and try again.")

    def _cancel_procedure(self):
        self._conn.cmd("stopJobReset")
        QTimer.singleShot(300, lambda: self._conn.cmd("grblUnlock"))
        self.reject()

    def _on_completed(self, mtype: str, result: str, payload: dict) -> None:
        if mtype == self.completion_type:
            self.on_procedure_done(result == "success", payload)

    # overridden by subclasses
    def on_procedure_done(self, success: bool, payload: dict) -> None:
        pass


class LaserZeroFlow(_ProcedureFlow):
    """Move Z until the distance laser reads zero, the middle of its range.

    The height map reads most accurately around the laser's zero, so the
    operator brings Z there first. App-side, no firmware procedure: read
    distanceLaserMm, send one relative moveZ of that distance, read again.
    The reading tracks Z about 1:1, so one move lands within a fraction of a
    millimetre and a second, small one finishes it.

    The reading is an *inverted* distance in the firmware (it grows as the
    surface gets closer — Config.h), so a positive reading means too close
    and Z goes up by it. That is the first move's guess; every move after it
    uses the change the previous move actually produced, so a sensor that
    counts the other way, or not exactly 1:1, still converges (and a reading
    that does not follow Z at all stops the flow instead of driving on).

    Out of range (no surface within ±34 mm) is not searched for: that would
    mean moving Z blind. The operator jogs into range and tries again.
    """

    #: Slow on purpose: the operator watches the end mill come down.
    FEED_MM_MIN = 300
    #: Close enough to zero (mm)
    TOLERANCE_MM = 0.05
    #: Moves before giving up (the first is the 1:1 guess, then corrections)
    MAX_MOVES = 4
    #: No single move longer than the laser's whole range
    MAX_STEP_MM = 34.0
    #: Longest first move DOWN, made before the direction is confirmed
    FIRST_DOWN_STEP_MM = 1.0
    #: After the move should have ended: status poll lag + the reading settling
    _SETTLE_S = 0.8
    #: Consecutive out-of-range replies before that is the answer, not warm-up
    _OUT_OF_RANGE_REPLIES = 3

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self._mm: float | None = None
        self._prev_mm: float | None = None
        self._last_dz = 0.0
        self._moves = 0
        self._out_of_range = 0
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(100)
        self._move_timer.timeout.connect(self._check_move_done)
        msg = lbl("The Z axis will slowly move towards the laser sensor zero "
                  "(the middle of its measuring range), where the height map "
                  "measures most accurately.",
                  sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Go to laser sensor zero",
            [msg,
             _warning_row("Do this only with the bed installed, and with the "
                          "laser pointing at a flat part of the bed, not into "
                          "one of its holes: a hole reads far away and Z "
                          "would go very low."),
             _warning_row("Make sure no long end mill is installed, and watch "
                          "out for possible collisions while Z moves.")],
            [("✓", self._start, True), ("✕", self.reject, True)],
            big_icon_glyph="⚠", icon_color="#3A3A3A",
        ))

    # ── readings ─────────────────────────────────────────────────────────────

    def _start(self) -> None:
        self._status = lbl("Reading the laser sensor…", sz=14, color=TXT2,
                           align=Qt.AlignCenter)
        self._status.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Going to laser sensor zero", [self._status],
            [("✕", self._cancel_procedure, True)], spinner=True,
        ))
        # Switches the laser on first if it is off, and waits out its warm-up
        self._read()

    def _read(self) -> None:
        self._out_of_range = 0
        self._laser_check_start(self._on_reading)

    def _on_laser_ready(self, mm: float) -> None:
        self._mm = float(mm)
        super()._on_laser_ready(mm)

    def _on_laser_error(self, error: str) -> None:
        if error == "distanceLaserOutOfRange" and self._laser_on_ready is not None:
            self._out_of_range += 1
            if self._out_of_range >= self._OUT_OF_RANGE_REPLIES:
                self._laser_check_cleanup()
                self.show_result(
                    False, "Surface out of laser range",
                    "The laser sensor cannot see the surface: it is more than "
                    "34 mm from its zero. Jog Z until the laser sensor shows "
                    "a reading, then try again.")
                return
        super()._on_laser_error(error)

    def _on_reading(self) -> None:
        mm = self._mm
        if abs(mm) <= self.TOLERANCE_MM:
            self.show_result(True, "Laser sensor at zero",
                             f"The laser sensor reads {mm:+.3f} mm.")
            return
        if self._moves >= self.MAX_MOVES:
            self._fail(f"After {self._moves} moves the laser sensor still "
                       f"reads {mm:+.3f} mm.")
            return
        if self._prev_mm is None:
            dz = mm                     # inverted reading: + = too close, go up
            # Down is the only direction that can hit anything, and this
            # first move rests on the sign guess: if the guess were wrong, a
            # full move down would drive the tool into the stock. So a first
            # move down is a short test step; the next one, with the
            # direction measured, goes the rest of the way.
            dz = max(dz, -self.FIRST_DOWN_STEP_MM)
        else:
            gain = (mm - self._prev_mm) / self._last_dz   # reading per mm of Z
            if abs(gain) < 0.3:
                self._fail("The laser reading did not change with the Z move. "
                           "The move may have been refused (outside the "
                           "machine's travel), or the laser is not pointing "
                           "at the surface below.")
                return
            dz = -mm / gain
        dz = max(-self.MAX_STEP_MM, min(self.MAX_STEP_MM, dz))
        self._prev_mm, self._last_dz = mm, dz
        self._moves += 1
        self._move(dz)

    def _fail(self, text: str) -> None:
        self.show_result(False, "Could not reach laser zero", text)

    # ── the move ─────────────────────────────────────────────────────────────

    def _move(self, dz: float) -> None:
        import time
        self._conn.cmd("moveZ", z=round(dz, 4), feedRate=self.FEED_MM_MIN,
                       relative=True)
        self._msg_text(f"Moving Z {'up' if dz > 0 else 'down'} {abs(dz):.2f} mm…")
        self._move_ends = (time.monotonic() + abs(dz) / self.FEED_MM_MIN * 60.0
                           + self._SETTLE_S)
        self._move_timer.start()

    def _check_move_done(self) -> None:
        import time
        status = self._conn.machine_status().split(":")[0].strip().lower()
        if status == "alarm":
            self._move_timer.stop()
            self._fail("The machine stopped with an alarm while Z was moving.")
            return
        if time.monotonic() < self._move_ends or status != "idle":
            return
        self._move_timer.stop()
        self._msg_text("Reading the laser sensor…")
        self._read()

    def _msg_text(self, text: str) -> None:
        self._status.setText(text)

    def done(self, r):
        self._move_timer.stop()
        super().done(r)


class ProbeZ0Flow(_ProcedureFlow):
    completion_type = "probeZ0Completed"

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self.set_phase(self.build_phase(
            "sensor", "Z0 detection",
            [_warning_row("Make sure the probe is placed under the spindle and "
                          "the crocodile clamp is attached to the end mill."),
             _setup_diagram("setup-probe-bed"),
             lbl("Is this the current setup?", sz=15, color=TXT,
                 align=Qt.AlignCenter)],
            [("✓", self._start, True), ("✕", self.reject, True)],
        ))

    def _start(self):
        self._conn.cmd("probeSetJobZ0Macro")
        self.show_progress("Z0 detection", "Probing in progress…",
                           self._cancel_procedure)

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        if success:
            self.show_result(True, "Z0 set successfully",
                             "The probe plate has been detected and Z0 has been set.")
        else:
            self.show_result(False, "Probing failed",
                             "The probe did not trigger. Check the probe plate "
                             "connection and try again.")


class ToolLengthFlow(_ProcedureFlow):
    completion_type = "probeToolLengthCompleted"

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self.set_phase(self.build_phase(
            "tool-length", "Tool length detection",
            [_warning_row("The probe must be placed on top of the bed, "
                          "aligned with the tool."),
             _setup_diagram("setup-probe-bed"),
             lbl("Is this the current setup?", sz=15, color=TXT,
                 align=Qt.AlignCenter)],
            [("✓", self._start, True), ("✕", self.reject, True)],
        ))

    def _start(self):
        self._conn.cmd("probeToolLengthMacro")
        self.show_progress("Tool length detection in progress",
                           "Please wait until the process is finished.",
                           self._cancel_procedure)

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        if success:
            try:
                plain = f"{float(payload.get('toolLength')):.1f}"
                value = f"{plain} mm"
            except (TypeError, ValueError):
                plain, value = "", "—"
            val = lbl(value, sz=30, bold=True, color=TXT, align=Qt.AlignCenter)
            self.set_phase(self.build_phase(
                None, "Tool length successfully detected", [val],
                [("copy", lambda: QGuiApplication.clipboard().setText(plain),
                  bool(plain)),
                 ("✓", self.accept, True)],
                big_icon_glyph="✓", icon_color=RUN_C,
            ))
        else:
            self.show_result(False, "Measurement failed",
                             "The probe did not trigger. Check the probe plate "
                             "connection and try again.")


class DetectXY0Flow(_ProcedureFlow):
    completion_type = "laserEdgeProbingCompleted"

    # corner value (firmware probeLaserEdges `corner`) → human label
    _CORNER_LABELS = {"topLeft": "top-left", "topRight": "top-right",
                      "bottomLeft": "bottom-left", "bottomRight": "bottom-right"}

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self._selected_corner = "bottomLeft"
        rows = [
            _warning_row("The laser head is positioned on top of the material, "
                         "near the corner to detect."),
            _warning_row("The material is opaque (non-transparent)."),
            _warning_row("The material has a regular square or rectangular shape "
                         "and the top surface must be flat."),
            _warning_row("The material edges are aligned with the bed lines."),
        ]
        head = lbl("Before starting, make sure:", sz=15, color=TXT)
        self.set_phase(self.build_phase(
            "detect-center", "XY₀ detection",
            [head] + rows
            + [_laser_area_row(conn, dialog_w=self._width),
               _section_label("Corner to detect"), self._build_corner_selector(),
               _section_label("Offset into the material (mm)"),
               self._build_offset_inputs()],
            [("✓", self._start, True), ("✕", self.reject, True)],
        ))

    # ── parameter widgets ─────────────────────────────────────────────────────

    def _build_corner_selector(self) -> QWidget:
        """2×2 grid of corner buttons laid out to match the physical corners;
        the selected one fills dark (mobile-parity segmented look)."""
        wrap = QWidget()
        g = QGridLayout(wrap)
        g.setContentsMargins(px(40), 0, px(40), 0)
        g.setSpacing(px(8))
        self._corner_group = QButtonGroup(self)
        cells = [("topLeft", "Top left", 0, 0), ("topRight", "Top right", 0, 1),
                 ("bottomLeft", "Bottom left", 1, 0),
                 ("bottomRight", "Bottom right", 1, 1)]
        for i, (val, text, r, c) in enumerate(cells):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(val == self._selected_corner)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("corner", val)
            autoscale(b, lambda x: x.setStyleSheet(f"""
                QPushButton {{ background: transparent;
                    border: {px(1.5)}px solid {BORDER}; border-radius: {px(8)}px;
                    padding: {px(10)}px {px(6)}px; font-size: {fs(13)}px;
                    color: {TXT}; }}
                QPushButton:checked {{ background: {TXT};
                    border: {px(1.5)}px solid {TXT}; color: {WHITE};
                    font-weight: bold; }}
            """))
            self._corner_group.addButton(b, i)
            g.addWidget(b, r, c)
        return wrap

    def _build_offset_inputs(self) -> QWidget:
        wrap = QWidget()
        g = QGridLayout(wrap)
        g.setContentsMargins(px(40), 0, px(40), 0)
        g.setSpacing(px(10))
        self._in_xoff = QLineEdit("0")
        self._in_yoff = QLineEdit("0")
        for i, (label, inp) in enumerate([("X offset", self._in_xoff),
                                          ("Y offset", self._in_yoff)]):
            g.addWidget(lbl(label, sz=13, color=TXT2), i, 0)
            autoscale(inp, lambda x: (
                x.setFixedHeight(px(38)),
                x.setStyleSheet(
                    f"background: {LIGHT}; border: 1px solid {BORDER}; "
                    f"border-radius: {px(8)}px; padding: {px(4)}px {px(10)}px; "
                    f"font-size: {fs(14)}px;"),
            ))
            g.addWidget(inp, i, 1)
        return wrap

    def _selected_offsets(self) -> tuple[float, float]:
        def _parse(inp) -> float:
            try:
                return float(inp.text().replace(",", "."))
            except ValueError:
                return 0.0
        return _parse(self._in_xoff), _parse(self._in_yoff)

    def _start(self):
        cb = self._corner_group.checkedButton()
        self._selected_corner = cb.property("corner") if cb else "bottomLeft"
        x_off, y_off = self._selected_offsets()
        if not self._conn.flags().get("laserSensorEnabled", False):
            self._conn.cmd("enableLaserSensor")
        self._conn.cmd("probeLaserEdges", corner=self._selected_corner,
                       xOffset=x_off, yOffset=y_off)
        self.show_progress("Edge detection in progress",
                           "The machine is scanning the material edges. Please wait.",
                           self._cancel_procedure)

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        if not success:
            self.show_result(False, "Edge detection failed",
                             "Could not detect all material edges. Make sure the "
                             "laser head is above the material center and the "
                             "material is opaque and flat.")
            return
        corner = self._CORNER_LABELS.get(self._selected_corner, "selected")
        msg = lbl(f"Is the red dot precisely aligned with the {corner} corner "
                  "of the material?", sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Verify position", [msg],
            [("yes", self._move_to_offset, True), ("no", self.reject, True)],
            big_icon_glyph="◎", icon_color="#3A78B5",
        ))

    def _move_to_offset(self):
        self._conn.cmd("moveToLaserOffsetZero")
        self.show_progress("Moving to XY₀ position",
                           "Please wait until the machine reaches the origin.",
                           self._cancel_procedure)
        # The move ends when the machine returns to Idle.
        self._conn.machine_status_changed.connect(self._on_status)

    def _on_status(self, status: str) -> None:
        if status.split(":")[0] == "Idle":
            try:
                self._conn.machine_status_changed.disconnect(self._on_status)
            except (RuntimeError, TypeError):
                pass
            corner = self._CORNER_LABELS.get(self._selected_corner, "selected")
            self.show_result(True, "XY₀ set successfully",
                             f"The tool center is now at the {corner} corner "
                             "of the material.")


class DetectCenterFlow(_ProcedureFlow):
    completion_type = "laserMaterialBoundsCompleted"

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        rows = [
            _warning_row("The material is flat, not transparent and has a "
                         "rectangular or circular shape."),
            _warning_row("The laser pointer is on top of the material, possibly "
                         "close to the center."),
            _warning_row("Rectangular material: the edges align to the bed "
                         "lines."),
        ]
        head = lbl("Before starting, make sure that:", sz=15, color=TXT)
        self.set_phase(self.build_phase(
            "detect-center", "Initializing center detection",
            [head] + rows + [_laser_area_row(conn, dialog_w=self._width)],
            [("✓", self._start, True), ("✕", self.reject, True)],
        ))

    def _start(self):
        if not self._conn.flags().get("laserSensorEnabled", False):
            self._conn.cmd("enableLaserSensor")
        self._conn.cmd("probeLaserMaterialBounds")
        self.show_progress("Center detection in progress",
                           "Scanning the material edges. Please wait until the "
                           "process is finished.", self._cancel_procedure)

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        if not success:
            self.show_result(False, "Center detection failed",
                             "Could not detect the material bounds. Start with "
                             "the laser above the material, near its center, and "
                             "make sure the material is opaque and flat.")
            return
        msg = lbl("Is the laser pointer positioned in the center of the material?\n"
                  "Confirm to reset the XY₀ to this position.",
                  sz=15, color=TXT, align=Qt.AlignCenter)
        msg.setWordWrap(True)
        self.set_phase(self.build_phase(
            "detect-center", "Center detection is finalized", [msg],
            [("✓", self._confirm_center, True), ("✕", self.reject, True)],
        ))

    def _confirm_center(self):
        self._conn.cmd("confirmMaterialCenterAndSetZero")
        self.show_progress("Moving to center position",
                           "Please wait until the machine head reaches the "
                           "laser-indicated center position.",
                           self._cancel_procedure)
        self._conn.machine_status_changed.connect(self._on_status)

    def _on_status(self, status: str) -> None:
        if status.split(":")[0] == "Idle":
            try:
                self._conn.machine_status_changed.disconnect(self._on_status)
            except (RuntimeError, TypeError):
                pass
            self.show_result(True, "Center set successfully",
                             "X and Y have been zeroed at the center of "
                             "the material.")


#: Which way a head-anchored height-map patch has to grow to land on the job.
#:
#: The firmware reads the SIGN of the two sizes as a direction and their
#: magnitude as the extent (firmware 0.8.3, `startHeightMapScan`)::
#:
#:     sizeX > 0 extends +X      sizeX < 0 extends -X
#:     sizeY > 0 extends -Y      sizeY < 0 extends +Y
#:
#: and normalises that to a top-left origin before anything downstream sees it.
#: Positive-only sizes therefore always scan right-and-forward from the end
#: mill, which is right for exactly one of the four corners a board can be
#: zeroed in. Zero the second side of a double-sided board at its bottom-right
#: corner — normal, since flipping the board moves zero to the opposite corner
#: — and the toolpath runs -X/+Y from zero while the scan walked off the other
#: way, over bare table.
#:
#: So the direction is taken from where the toolpath actually sits, which the
#: file's own MINX/MAXX/MINY/MAXY say and nothing else does: a size is a
#: magnitude, and `fileWidth`/`fileDepth` cannot distinguish the four corners.
def scan_size_signs(bounds) -> tuple[float, float] | None:
    """(x_sign, y_sign) for a job whose bounds are *(minX, maxX, minY, maxY)*.

    None when the bounds are missing or degenerate — the direction is then
    genuinely unknown and must not be guessed; see `signed_scan_size`.
    """
    if not bounds:
        return None
    try:
        min_x, max_x, min_y, max_y = (float(v) for v in bounds)
    except (TypeError, ValueError):
        return None
    if not (max_x > min_x and max_y > min_y):
        return None
    # The dominant side decides. A job entirely on one side of zero (the four
    # corner cases) is unambiguous; one straddling zero cannot be covered by a
    # patch growing one way at all, and the larger half is the better half to
    # have. "Scan the whole toolpath" is the option for that case.
    x_sign = 1.0 if abs(max_x) >= abs(min_x) else -1.0
    y_sign = 1.0 if abs(min_y) >= abs(max_y) else -1.0
    return x_sign, y_sign


def scan_direction_words(signs) -> tuple[str, str]:
    """The two directions a signed scan grows in, named for an operator.

    Axis letters alone are ambiguous at the machine — which way is +Y depends
    on where you are standing — so each is paired with the word for it from in
    front of the bed. The Y pairing is the one worth reading twice: a POSITIVE
    size grows -Y, toward the operator.
    """
    x_sign, y_sign = signs
    return ("\u2212X (left)" if x_sign < 0 else "+X (right)",
            "+Y (back)" if y_sign < 0 else "\u2212Y (front)")


def signed_scan_size(size_x: float, size_y: float,
                     bounds) -> tuple[float, float]:
    """Typed sizes carrying the job's direction.

    The magnitude is the operator's — it is what the field asks for — and the
    sign is the job's. With no bounds to read a direction from, the sizes stay
    positive, which is the firmware's own fallback (+X / -Y from the head) and
    what the dialog then tells the operator to park for.
    """
    x_sign, y_sign = scan_size_signs(bounds) or (1.0, 1.0)
    return abs(size_x) * x_sign, abs(size_y) * y_sign


class HeightMapFlow(_ProcedureFlow):
    completion_type = "heightMapScanCompleted"

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self._density = "coarse"
        self._progress_lbl: QLabel | None = None   # live "n / m points" label
        #: (minX, maxX, minY, maxY) of the open file's toolpath, in job
        #: coordinates, or None while unanswered/unknown. Asked for here rather
        #: than at the size phase because the answer is only needed two screens
        #: later and `getOpenedFileBounds` fails outright (`fileNotFound`) for a
        #: file written before that metadata existed — no reply is an answer,
        #: and by then it has had the whole pre-flight to arrive.
        self._job_bounds: tuple[float, float, float, float] | None = None
        self._dir_lbl: QLabel | None = None
        conn.opened_file_bounds_received.connect(self._on_job_bounds)
        conn.get("getOpenedFileBounds")
        # Where the map lands, in the firmware's order of preference
        # (README 5.36):
        #
        #   float scanOriginX = haveFileOrigin ? fileOriginX : currentJobPositionX;
        #
        # — the file's own toolpath bounds (MINX/MAXY) when it carries them,
        # otherwise the head. Which means the head only matters in two cases:
        # a typed size, or a file written before MIN/MAX metadata existed. In
        # the first case the operator is deliberately scanning a patch; in the
        # second the head is the only clue where the toolpath is, and the
        # firmware says to park on its top-left corner. And:
        #
        #   "THE MAP IS DROPPED AUTOMATICALLY IF THE JOB XY ZERO CHANGES.
        #    … CPUB watches the work offset itself, so this covers every way of
        #    setting zero … A ready map is invalidated and
        #    `heightMapCompensationEnabled` is forced back to false."
        #
        # Which makes zeroing a precondition, not a detail: it goes first. Note
        # the operator is NOT asked to remember anything — the machine drops the
        # map by itself; the row says so, because "you must rescan" and "it will
        # be gone" call for different amounts of vigilance.
        #
        # Only X and Y are watched. Re-zeroing Z after a tool change is safe —
        # the stored values are deltas from the origin cell, so a new Z zero
        # shifts the cut depth uniformly and leaves the map valid — and saying
        # so stops an operator rescanning after every tool change.
        #
        # The anchor is the END MILL, never the laser. The grid is held in the
        # tool frame and each scan move drives the spindle to
        # `gridTarget + laserOffset` so the *laser* lands on the grid point.
        # This row used to say the laser sensor had to be over the corner,
        # which would have the operator start the map one laser offset
        # (25 mm-ish in X and Y) away from where they meant to.
        #
        # Laid out as what to do, in order: a numbered "before you scan"
        # checklist, then one "after the scan" note. Step 2 carries its own
        # Go to laser sensor zero button (LaserZeroFlow, opened on top), so
        # the operator does not have to leave the wizard to do it.
        # What to do AFTER the scan (Z zero, compensation on) is said on the
        # "Height map ready" screen, when it is due, not here.
        intro = lbl("The laser measures the material's height so the cut "
                    "follows its surface. Before you scan:",
                    sz=15, color=TXT, align=Qt.AlignCenter)
        intro.setWordWrap(True)
        zero_btn = _small_button("Go to laser sensor zero")
        zero_btn.clicked.connect(
            lambda: LaserZeroFlow(self._conn, parent=self).exec())
        rows = [
            intro,
            _step_row(1, "Open the job file and set the <b>X and Y zero</b> "
                         "at the corner the job uses. Changing them later "
                         "deletes the map."),
            _step_row(2, "Put the laser over the material and bring it to "
                         "its zero:", extra=zero_btn),
            _step_row(3, "Keep the material inside the <b>laser area</b> "
                         "below. Where the head is now does not matter."),
            _laser_area_row(self._conn, dialog_w=self._width),
        ]
        self.set_phase(self.build_phase(
            "height-map", "Height map", rows,
            [("✓", self._accuracy_phase, True), ("✕", self.reject, True)],
        ))

    def _accuracy_phase(self):
        text = lbl("How far apart should the measurements be?",
                   sz=15, color=TXT, align=Qt.AlignCenter)
        opts = QWidget()
        ov = QVBoxLayout(opts)
        ov.setContentsMargins(px(40), 0, px(40), 0)
        ov.setSpacing(px(12))
        self._acc_group = QButtonGroup(self)
        # 4 mm / 2 mm since the firmware moved to a line scan (README 5.36);
        # it was 6 / 3 when every point was a separate stop-and-settle. The
        # figures set the ROW PITCH and cell width — the sample spacing along a
        # row is set by the laser's read rate, not by this choice.
        for i, (name, desc, val) in enumerate([
                ("Coarse — every 4 mm",
                 "Faster. Enough for most boards and flat stock.", "coarse"),
                ("Fine — every 2 mm",
                 "Slower. Follows small bumps and warps more closely.",
                 "fine")]):
            ov.addWidget(_option_card(name, desc, checked=(i == 0),
                                      group=self._acc_group, idx=i, value=val))
        self.set_phase(self.build_phase(
            "height-map", "Height map — detail", [text, opts],
            [("✓", self._area_phase, True), ("✕", self.reject, True)],
        ))

    def _area_phase(self):
        checked = self._acc_group.checkedButton()
        self._density = checked.property("density") if checked else "coarse"

        text = lbl("What should the scan cover?", sz=15, color=TXT,
                   align=Qt.AlignCenter)
        opts = QWidget()
        ov = QVBoxLayout(opts)
        ov.setContentsMargins(px(40), 0, px(40), 0)
        ov.setSpacing(px(12))
        self._area_group = QButtonGroup(self)
        for i, (name, desc, val) in enumerate([
                ("The whole job (recommended)",
                 "Scans the area the toolpath of the open file covers. Works "
                 "from any zero corner.", "file"),
                ("A rectangle from the end mill",
                 "Scans the size you type below, starting at the end mill: "
                 "park it on the job's corner first. For a test area, or a "
                 "file without size information.", "patch")]):
            ov.addWidget(_option_card(name, desc, checked=(i == 0),
                                      group=self._area_group, idx=i, value=val))

        # The size fields belong to the second choice only, and follow it.
        form = QWidget()
        g = QGridLayout(form)
        g.setContentsMargins(px(40), 0, px(40), 0)
        g.setSpacing(px(10))
        meta = {}
        opened = self._conn.opened_file_name()
        if opened:
            meta = self._conn.file_meta(opened)
        self._in_x = QLineEdit(str(meta.get("fileWidth", "") or ""))
        self._in_y = QLineEdit(str(meta.get("fileDepth", "") or ""))
        for i, (label, inp) in enumerate([("X size (mm)", self._in_x),
                                          ("Y size (mm)", self._in_y)]):
            g.addWidget(lbl(label, sz=13, color=TXT2), i, 0)
            autoscale(inp, lambda x: (
                x.setFixedHeight(px(38)),
                x.setStyleSheet(
                    f"background: {LIGHT}; border: 1px solid {BORDER}; "
                    f"border-radius: {px(8)}px; padding: {px(4)}px {px(10)}px; "
                    f"font-size: {fs(14)}px;"
                ),
            ))
            g.addWidget(inp, i, 1)
        self._size_form = form
        form.setVisible(False)

        # Which way the scan will actually go, said outright. The sign is
        # derived rather than typed, so without this the operator has no way to
        # know whether it was derived correctly until the head is moving.
        #
        # Pinned to the width it will be given, like every other wrapped label
        # in these dialogs — and re-measured on each change, because this one's
        # text is rewritten as the bounds arrive and as the option changes, and
        # a label measured while it held a shorter string stays that tall.
        self._dir_lbl = lbl("", sz=12, color=TXT2)
        self._dir_lbl.setWordWrap(True)
        self._dir_w = px(self._width) - 2 * px(_CARD_SIDE_MARGIN) - 2 * px(40)
        self._dir_lbl.setFixedWidth(self._dir_w)
        dir_row = QWidget()
        dh = QHBoxLayout(dir_row)
        dh.setContentsMargins(px(40), 0, px(40), 0)
        dh.setSpacing(0)
        dh.addWidget(self._dir_lbl, 1)
        self._update_dir_label()

        self._area_group.idToggled.connect(
            lambda idx, on: form.setVisible(
                on and self._area_group.button(idx).property("density")
                == "patch"))
        self._area_group.idToggled.connect(lambda *_: self._update_dir_label())

        self.set_phase(self.build_phase(
            "height-map", "Height map — area",
            [text, opts, form, dir_row],
            [("✓", self._start_scan, True), ("✕", self.reject, True)],
        ))

    def _on_job_bounds(self, payload: dict) -> None:
        """getOpenedFileBounds answered. Only success is routed here (the
        reply is keyed on `fileMinXmm`), so a file with no MIN/MAX metadata
        simply never calls this and the bounds stay None."""
        try:
            self._job_bounds = (float(payload["fileMinXmm"]),
                                float(payload["fileMaxXmm"]),
                                float(payload["fileMinYmm"]),
                                float(payload["fileMaxYmm"]))
        except (KeyError, TypeError, ValueError):
            self._job_bounds = None
        self._update_dir_label()

    def _update_dir_label(self) -> None:
        """Say where this scan is going to go, in the operator’s words.

        Four sentences, because the two choices fail differently: the file
        option is the firmware’s own anchoring and needs no corner at all,
        while a patch is anchored at the head. Both degrade to +X/−Y when the
        file carries no bounds, and that is the case worth a warning: it is the
        one where parking the head somewhere particular is load-bearing.
        """
        if self._dir_lbl is None:
            return

        def say(text: str) -> None:
            self._dir_lbl.setText(text)
            # Cleared first: QLabel folds its own minimum into the height it
            # reports for a width, so measuring while the previous (longer)
            # text's minimum still stands can only ever grow the label. The
            # warning is the tallest of these, and it is the one that would
            # otherwise leave a gap under every line that replaced it.
            self._dir_lbl.setMinimumHeight(0)
            self._dir_lbl.setMinimumHeight(
                self._dir_lbl.heightForWidth(self._dir_w))

        signs = scan_size_signs(self._job_bounds)
        by_file = self._use_file_bounds()
        if signs is None:
            x_dir, y_dir = scan_direction_words((1.0, 1.0))
            where = ("the machine places the map at the end mill"
                     if by_file else "the patch grows from the end mill")
            say(
                "⚠ This file carries no toolpath bounds, so the direction "
                "cannot be read from it: %s and scans %s and %s. Park it on "
                "the job’s top-left corner." % (where, x_dir, y_dir))
            return
        if by_file:
            say(
                "The map is placed from the file’s own bounds, so any corner "
                "zero works and where the head sits does not matter.")
            return
        x_dir, y_dir = scan_direction_words(signs)
        say(
            "The toolpath runs %s and %s from zero, so the patch scans that "
            "way from the end mill." % (x_dir, y_dir))

    def _use_file_bounds(self) -> bool:
        b = self._area_group.checkedButton() if hasattr(self, "_area_group") else None
        return b is None or b.property("density") == "file"

    def _start_scan(self):
        # Firmware key names per README 5.36 (heightMap* prefixed).
        params = {"heightMapDensity": self._density}
        # ANCHORING, and the reason this is the default (firmware 0.8.3):
        #
        # A size says how big the job is. It does not say which way it goes.
        # The app used to compute one from the file's width and depth and send
        # it with `heightMapUseOpenedFileSize: false`, which anchors the map at
        # the head and grows +X / -Y. Mill the second side of a board and you
        # flip it and put job zero in a different corner — the toolpath then
        # runs the other way, and a map grown +X from the head scans away from
        # the copper.
        #
        # `heightMapUseOpenedFileSize: true` hands that decision to the
        # firmware, which anchors on the file's own MINX/MAXY and spans to
        # MAXX/MINY. Nothing in that assumes a direction, so both sides of a
        # board work identically and the head position stops mattering.
        #
        # The explicit path is kept for the two cases it is still right for: a
        # deliberate test patch, and a file whose metadata predates MIN/MAX. It
        # anchors at the head, but it no longer assumes a DIRECTION: the sizes
        # carry the job’s own, so all four corner zeros scan toward the work
        # (see `signed_scan_size`). Only a file with no bounds at all is left
        # with the +X/-Y default, and the dialog says so before it starts.
        if self._use_file_bounds():
            params.update(heightMapUseOpenedFileSize=True)
        else:
            try:
                sx = float(self._in_x.text().replace(",", "."))
                sy = float(self._in_y.text().replace(",", "."))
            except ValueError:
                params.update(heightMapUseOpenedFileSize=True)
            else:
                # Magnitude from the field, sign from the job.
                sx, sy = signed_scan_size(sx, sy, self._job_bounds)
                params.update(heightMapUseOpenedFileSize=False,
                              heightMapSizeXmm=sx, heightMapSizeYmm=sy)
        # Readiness gate (mirrors the mobile app): the scan only starts once
        # the laser answers with a distance reading (see _ProcedureFlow).
        self.show_progress("Height map",
                           "Checking that the laser can read the "
                           "material…", self.reject)
        self._laser_check_start(lambda: self._begin_scan(params))

    def _begin_scan(self, params: dict) -> None:
        self._conn.cmd("startHeightMapScan", **params)
        self._progress_lbl = lbl("Scanning… Do not move the material or touch "
                                 "the machine.", sz=14, color=TXT2,
                                 align=Qt.AlignCenter)
        self._progress_lbl.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Scanning the height map", [self._progress_lbl],
            [("✕", self._abort_scan, True)], spinner=True,
        ))

    # scan progress / completion -------------------------------------------

    def _abort_scan(self):
        self._progress_lbl = None
        self._conn.cmd("abortHeightMapScan")
        self.reject()

    def _on_completed(self, mtype: str, result: str, payload: dict) -> None:
        if mtype == "heightMapScanProgress" and self._progress_lbl is not None:
            # Rows when the firmware reports them: since the line scan it emits
            # one progress event per completed ROW, so a points figure now
            # advances in jumps of a whole row and sits still in between. The
            # points fields are still sent for older frontends, and are the
            # fallback here for a legacy-grid build.
            rows = payload.get("heightMapRowsScanned")
            rows_total = payload.get("heightMapRowsTotal")
            scanned = payload.get("heightMapPointsScanned")
            total = payload.get("heightMapPointsTotal")
            if rows is not None and rows_total:
                unit, done, all_of = "rows", rows, rows_total
            elif scanned is not None and total:
                unit, done, all_of = "points", scanned, total
            else:
                unit = None
            if unit:
                try:
                    self._progress_lbl.setText(
                        f"Scanning… {int(done)} of {int(all_of)} {unit} done. "
                        f"Do not move the material or touch the machine.")
                except RuntimeError:
                    self._progress_lbl = None
        super()._on_completed(mtype, result, payload)

    #: What the firmware's abort diagnostics mean, in words the operator can
    #: act on. `heightMapAbortStage` / `heightMapAbortDetail` are explicitly
    #: NOT a stable enumeration (README 5.36), so anything not listed here
    #: falls through to a generic message that still shows the raw pair.
    _ABORT_REASONS = {
        ("FINALIZE", "no_valid_cells"):
            "Not one cell got a usable reading, so there was nothing to build "
            "a map from. The laser could not read the material at all — too "
            "reflective, out of range, or not over the work.",
        ("FINALIZE", "empty_grid"):
            "The computed grid was empty. Check the scan size.",
        ("MOVE_ROW_START", "schedule_failed"):
            "The machine refused the move to the start of a row, normally a "
            "soft limit. Start nearer the front-left of the bed, or scan a "
            "smaller area.",
        ("START_ROW", "schedule_failed"):
            "The machine refused a row sweep, normally a soft limit. Start "
            "nearer the front-left of the bed, or scan a smaller area.",
        ("ABORT", "user_requested"): "The scan was cancelled.",
        ("ZERO_CHANGED", "work_offset_moved"):
            "The X or Y job zero changed while the scan was running, so it was "
            "abandoned — a map is anchored to the zero it started with. Set "
            "zero first, then scan.",
        ("BASELINE", "reading_near_range_limit"):
            "The laser is too close to the end of its range to measure from. "
            "Put the laser over the material, press Go to laser sensor zero, "
            "then scan again.",
        ("BASELINE", "no_valid_readings"):
            "No reading at job zero. Check the Z height, and that there is "
            "material over job (0,0) — that point is the map's reference.",
    }

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        self._progress_lbl = None
        if success:
            self.show_result(True, "Height map ready",
                             "The map belongs to the open file. Now set the Z "
                             "zero at the job's X0 Y0 and turn on height-map "
                             "compensation before starting the job."
                             + self._quality_note(payload))
            return
        stage = str(payload.get("heightMapAbortStage", "") or "")
        detail = str(payload.get("heightMapAbortDetail", "") or "")
        why = self._ABORT_REASONS.get((stage, detail))
        if why is None:
            # Neither the documented pair nor an `error` key is guaranteed on
            # this event; before the abort fields existed this branch showed
            # "Unknown error" for every failed scan.
            why = str(payload.get("error", "") or "").strip()
            if stage or detail:
                why = (why + "  " if why else "") + "(%s / %s)" % (
                    stage or "?", detail or "?")
            why = why or "The scan did not finish."
        scanned = payload.get("heightMapPointsScanned")
        if scanned:
            why += "  %s cells were scanned before it stopped." % int(scanned)
        self.show_result(False, "Height map scan failed", why)

    @staticmethod
    def _quality_note(payload: dict) -> str:
        """What the line scan measured about itself.

        `heightMapCellsInterpolated` is the one the firmware asks the frontend
        to warn about: those cells collected no usable reading and were filled
        from their neighbours, so part of a map that looks smooth was never
        measured. Bare copper and other specular stock is where it shows up.
        """
        def num(key):
            try:
                return payload[key]
            except KeyError:
                return None

        filled = num("heightMapCellsInterpolated")
        spread = num("heightMapMaxCellSpreadMm")
        bits = []
        # Where the map actually landed, so it can be checked against the
        # file's own bounds rather than taken on trust.
        ox, oy = num("heightMapOriginXmm"), num("heightMapOriginYmm")
        if ox is not None and oy is not None:
            try:
                bits.append("Origin X %.2f, Y %.2f." % (float(ox), float(oy)))
            except (TypeError, ValueError):
                pass
        if spread is not None:
            try:
                noisy = num("heightMapNoisyCells")
                bits.append("Noisiest cell spread %.3f mm%s."
                            % (float(spread),
                               "" if not noisy else ", %d noisy cells" % int(noisy)))
            except (TypeError, ValueError):
                pass
        # The sensor spans about ±30 mm; readings crowding either end mean the
        # Z height wants adjusting before the next scan.
        lo, hi = num("heightMapMinReadingMm"), num("heightMapMaxReadingMm")
        if lo is not None and hi is not None:
            try:
                bits.append("Raw readings %.2f to %.2f mm." % (float(lo), float(hi)))
            except (TypeError, ValueError):
                pass
        if num("heightMapDatumFromCells") is False:
            bits.append("Job zero fell outside the map, so a less accurate Z "
                        "reference was used — check the map covers the job.")
        if filled:
            try:
                bits.append(
                    "%d cell(s) had no usable reading and were filled in from "
                    "their neighbours — that part of the map is inferred, not "
                    "measured." % int(filled))
            except (TypeError, ValueError):
                pass
        return ("\n\n" + " ".join(bits)) if bits else ""


class PointCloudFlow(_ProcedureFlow):
    """Scan the material surface with the distance laser into a .ply point
    cloud saved on the machine (firmware startPointCloudScan)."""

    completion_type = "pointCloudScanCompleted"

    _SPACING = {"ultrafine": 0.2, "fine": 0.5, "medium": 1.0, "coarse": 2.0}
    _MAX_X_MM = 90.0     # firmware POINT_CLOUD_MAX_SCAN_DIMENSION (X)
    _MAX_Y_MM = 135.0    # firmware POINT_CLOUD_MAX_SCAN_DIMENSION (Y)

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        self._aborted = False
        self._filename = ""
        self._est_rows: int | None = None
        self._progress_lbl: QLabel | None = None    # live "Row n / ~m" label
        self._setup_phase()

    def _setup_phase(self):
        text = lbl("Name the output file and set the scan area.",
                   sz=15, color=TXT, align=Qt.AlignCenter)

        form = QWidget()
        g = QGridLayout(form)
        g.setContentsMargins(px(40), 0, px(40), 0)
        g.setSpacing(px(10))

        def _style_input(inp):
            autoscale(inp, lambda x: (
                x.setFixedHeight(px(38)),
                x.setStyleSheet(
                    f"background: {LIGHT}; border: 1px solid {BORDER}; "
                    f"border-radius: {px(8)}px; padding: {px(4)}px {px(10)}px; "
                    f"font-size: {fs(14)}px;"
                ),
            ))

        self._rows_lbl = lbl("", sz=12, color=TXT2)

        # output file name (base name — the firmware appends .ply)
        self._in_name = QLineEdit("scan")
        _style_input(self._in_name)
        name_row = QWidget()
        nh = QHBoxLayout(name_row)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.setSpacing(px(8))
        nh.addWidget(self._in_name, 1)
        nh.addWidget(lbl(".ply", sz=14, color=TXT2))
        g.addWidget(lbl("File name", sz=13, color=TXT2), 0, 0)
        g.addWidget(name_row, 0, 1)
        overwrite = lbl("An existing file with the same name will be "
                        "overwritten.", sz=12, color=TXT3)
        overwrite.setWordWrap(True)
        g.addWidget(overwrite, 1, 1)

        # Density selector — compact segmented buttons, one per quality
        # (mobile parity, App.tsx ~3870): the selected button fills dark with
        # white text; the rest are outlined.
        opts = QWidget()
        ov = QHBoxLayout(opts)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(px(4))
        self._density_group = QButtonGroup(self)
        for i, (val, name) in enumerate([
                ("ultrafine", "Ultrafine"), ("fine", "Fine"),
                ("medium", "Medium"), ("coarse", "Coarse")]):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(val == "medium")
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("density", val)
            b.toggled.connect(self._update_estimate)
            autoscale(b, lambda x: x.setStyleSheet(f"""
                QPushButton {{ background: transparent;
                    border: {px(1.5)}px solid {BORDER}; border-radius: {px(6)}px;
                    padding: {px(7)}px {px(4)}px; font-size: {fs(12)}px;
                    color: {TXT}; }}
                QPushButton:checked {{ background: {TXT};
                    border: {px(1.5)}px solid {TXT}; color: {WHITE}; }}
            """))
            self._density_group.addButton(b, i)
            ov.addWidget(b, 1)
        g.addWidget(lbl("Density", sz=13, color=TXT2), 2, 0, Qt.AlignVCenter)
        g.addWidget(opts, 2, 1)

        # scan area — prefilled from the opened file, clamped to the limits
        meta = {}
        opened = self._conn.opened_file_name()
        if opened:
            meta = self._conn.file_meta(opened)

        def _prefill(value, maximum: float) -> str:
            try:
                v = float(value)
                if v > 0:
                    return f"{min(v, maximum):g}"
            except (TypeError, ValueError):
                pass
            return "50"

        self._in_x = QLineEdit(_prefill(meta.get("fileWidth"), self._MAX_X_MM))
        self._in_y = QLineEdit(_prefill(meta.get("fileDepth"), self._MAX_Y_MM))
        for i, (label, inp) in enumerate([("X size (mm)", self._in_x),
                                          ("Y size (mm)", self._in_y)], 3):
            _style_input(inp)
            g.addWidget(lbl(label, sz=13, color=TXT2), i, 0)
            g.addWidget(inp, i, 1)
        self._in_y.textChanged.connect(self._update_estimate)
        g.addWidget(self._rows_lbl, 5, 1)

        area_note = lbl("The scan starts from the current job XY position and "
                        "covers the area in the +X/+Y direction.",
                        sz=12, color=TXT2, align=Qt.AlignCenter)
        area_note.setWordWrap(True)
        self._err_lbl = lbl("", sz=12, color=ERR_C, align=Qt.AlignCenter)
        self._err_lbl.setWordWrap(True)

        self.set_phase(self.build_phase(
            "height-map", "Point cloud scan",
            [text, form, area_note,
             _laser_area_row(self._conn,
                             f"A single scan is capped to "
                             f"{self._MAX_X_MM:.0f} × {self._MAX_Y_MM:.0f} mm "
                             f"on top of that.",
                             dialog_w=self._width),
             self._err_lbl],
            [("✓", self._start_scan, True), ("✕", self.reject, True)],
        ))
        self._update_estimate()

    def _current_density(self) -> str:
        checked = self._density_group.checkedButton()
        return checked.property("density") if checked else "medium"

    def _update_estimate(self, *_):
        if not hasattr(self, "_in_y"):
            return              # radios toggle before the form is complete
        spacing = self._SPACING[self._current_density()]
        try:
            sy = float(self._in_y.text().replace(",", "."))
        except ValueError:
            sy = 0.0
        if 0 < sy <= self._MAX_Y_MM:
            self._rows_lbl.setText(
                f"≈ {math.floor(sy / spacing) + 1} rows")
        else:
            self._rows_lbl.setText("")

    def _start_scan(self):
        name = self._in_name.text().strip()
        if name.lower().endswith(".ply"):
            name = name[:-4].strip()
        if not name:
            self._err_lbl.setText("Enter a name for the output file.")
            return
        try:
            sx = float(self._in_x.text().replace(",", "."))
            sy = float(self._in_y.text().replace(",", "."))
        except ValueError:
            self._err_lbl.setText("Enter valid X and Y sizes in mm.")
            return
        if not 0 < sx <= self._MAX_X_MM:
            self._err_lbl.setText(
                f"X size must be between 0 and {self._MAX_X_MM:g} mm.")
            return
        if not 0 < sy <= self._MAX_Y_MM:
            self._err_lbl.setText(
                f"Y size must be between 0 and {self._MAX_Y_MM:g} mm.")
            return

        density = self._current_density()
        self._filename = name
        self._est_rows = math.floor(sy / self._SPACING[density]) + 1
        self._aborted = False
        # Readiness gate first — the scan reads the same distance laser, and
        # starting with a cold sensor would waste a multi-minute pass on
        # garbage data (see _ProcedureFlow._laser_check_start).
        self.show_progress("Point cloud scan",
                           "Checking the laser sensor reading over the "
                           "material…", self.reject)
        self._laser_check_start(
            lambda: self._begin_scan(name, sx, sy, density))

    def _begin_scan(self, name: str, sx: float, sy: float, density: str) -> None:
        self._conn.cmd("startPointCloudScan",
                       filename=name,
                       pointCloudSizeXmm=sx, pointCloudSizeYmm=sy,
                       pointCloudDensity=density,
                       fileDate=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._progress_lbl = lbl("Please wait until the process is finished.",
                                 sz=14, color=TXT2, align=Qt.AlignCenter)
        self._progress_lbl.setWordWrap(True)
        self.set_phase(self.build_phase(
            None, "Point cloud scan in progress", [self._progress_lbl],
            [("✕", self._abort_scan, True)], spinner=True,
        ))

    def _abort_scan(self):
        self._aborted = True
        self._progress_lbl = None
        self._conn.cmd("abortPointCloudScan")
        self.show_result(False, "Point cloud scan aborted",
                         "The scan was stopped. The partial "
                         f"{self._filename}.ply file was closed and left on "
                         "the machine.")

    def _on_completed(self, mtype: str, result: str, payload: dict) -> None:
        if mtype == "pointCloudScanProgress" and self._progress_lbl is not None:
            row = payload.get("pointCloudCurrentRow")
            total = payload.get("pointCloudGridPointsY") or self._est_rows
            if row is not None:
                try:
                    if total:
                        self._progress_lbl.setText(
                            f"Row {int(row)} / ~{int(total)}")
                    else:
                        self._progress_lbl.setText(f"Row {int(row)}")
                except RuntimeError:
                    self._progress_lbl = None
        super()._on_completed(mtype, result, payload)

    def on_procedure_done(self, success: bool, payload: dict) -> None:
        self._progress_lbl = None
        if self._aborted:
            return              # the aborted phase is already shown
        if success:
            fname = payload.get("pointCloudFilename") or f"{self._filename}.ply"
            msg = f"{fname} has been saved on the machine."
            pts = payload.get("pointCloudPointsScanned")
            if pts:
                msg += f"\n{pts} points scanned."
            self.show_result(True, "Point cloud scan completed", msg)
        else:
            detail = (payload.get("heightMapAbortDetail")
                      or payload.get("error") or "Unknown error")
            self.show_result(False, "Point cloud scan failed", str(detail))


#: Machine Y the Rotary mode setup parked the bed at (QSettings InMachines /
#: MiniCNC2). Written only when Rotary mode finishes parking; Bed back on the
#: Controls page always returns there.
ROTARY_PARK_KEY = "controls/rotaryParkMy"


def remember_rotary_park(conn) -> None:
    """Store the bed's current machine Y as the rotary park position."""
    from PySide6.QtCore import QSettings
    try:
        my = float(conn.current_position().get("my"))
    except (TypeError, ValueError, AttributeError):
        return
    QSettings("InMachines", "MiniCNC2").setValue(ROTARY_PARK_KEY, f"{my:.3f}")


class RotaryFlow(_ProcedureFlow):
    completion_type = ""        # driven by machine status + flags

    def __init__(self, conn, parent=None):
        super().__init__(conn, parent)
        rows = [
            _warning_row("The bed will move forward now. Please ensure there "
                         "are no obstacles."),
            _warning_row("Z probing and tool length will be deactivated."),
        ]
        self.set_phase(self.build_phase(
            "rotary", "Initializing rotary mode", rows,
            [("start", self._install, True), ("✕", self.reject, True)],
        ))

    def _install(self):
        self._conn.cmd("installRotary")
        self.show_progress("Moving to installation position…",
                           "Please ensure there are no obstacles.",
                           self._abort)
        self._conn.machine_status_changed.connect(self._on_install_status)

    def _abort(self):
        self._conn.cmd("grblSoftReset")
        self.reject()

    def _on_install_status(self, status: str) -> None:
        if status.split(":")[0] != "Idle":
            return
        try:
            self._conn.machine_status_changed.disconnect(self._on_install_status)
        except (RuntimeError, TypeError):
            pass
        self._setup_phase()

    def _setup_phase(self):
        steps = QWidget()
        sv = QVBoxLayout(steps)
        sv.setContentsMargins(px(20), 0, px(20), 0)
        sv.setSpacing(px(8))
        for i, step in enumerate([
                "For safety, remove any end mill from the spindle",
                "Install the rotary axis on the bed in the correct position",
                "Connect the rotary axis cable"], 1):
            sv.addWidget(lbl(f"{i}.  {step}", sz=14, color=TXT))
        self._done_btn_phase = self.build_phase(
            "rotary", "Rotary axis setup", [steps],
            [("done", self._position, self._conn.flags().get("rotaryAxisConnected", False)),
             ("✕", self._abort, True)],
        )
        self.set_phase(self._done_btn_phase)
        self._conn.machine_flags_changed.connect(self._on_flags)

    def _on_flags(self, flags: dict) -> None:
        if flags.get("rotaryAxisConnected"):
            try:
                self._conn.machine_flags_changed.disconnect(self._on_flags)
            except (RuntimeError, TypeError):
                pass
            self._setup_phase()      # rebuild with the done button enabled

    def _position(self):
        self._conn.cmd("moveToRotaryPosition")
        self.show_progress("Moving to rotary working position…", "",
                           self._abort)
        self._conn.machine_status_changed.connect(self._on_position_status)

    def _on_position_status(self, status: str) -> None:
        if status.split(":")[0] != "Idle":
            return
        try:
            self._conn.machine_status_changed.disconnect(self._on_position_status)
        except (RuntimeError, TypeError):
            pass
        # Remember where the bed was parked: Bed back (Controls) always returns
        # here. Read a beat later -- the status and the position arrive in the
        # same report, the status first, so the position seen right now can
        # still be from before the move ended.
        # Not in the developer offline mode: its bed never moved, and storing
        # that would send the real machine's Bed back to the wrong place.
        conn = self._conn
        if not getattr(conn, "is_offline", False):
            QTimer.singleShot(1000, lambda: remember_rotary_park(conn))
        self.show_result(True, "Rotary mode ready",
                         "The rotary axis is installed and in working position.")


# ── Homing options popup (design: Homing options - desktop.svg) ───────────────

class HomingOptionsDialog(QDialog):
    """XYZ / X / Y / Z homing chooser; emits the chosen command."""

    def __init__(self, conn, rotary: bool = False, parent=None):
        super().__init__(parent)
        self._conn = conn
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)

        self._card = rounded_dialog_card(self)
        self._v = QVBoxLayout(self._card)

        self._head = QHBoxLayout()
        self._hi = QLabel()
        self._hi.setStyleSheet(TRANSPARENT)
        self._head.addWidget(self._hi)
        self._head.addWidget(lbl("Homing options", sz=24, color=TXT))
        self._head.addStretch()
        self._close = QPushButton("✕")
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.clicked.connect(self.reject)
        self._head.addWidget(self._close)
        self._v.addLayout(self._head)

        self._grid = QHBoxLayout()

        self._home_btns: list[tuple] = []

        def _home_btn(text: str, command: str, w: int, h: int,
                      enabled: bool = True) -> QPushButton:
            b = QPushButton(text)
            b.setEnabled(enabled)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: self._run(command))
            self._home_btns.append((b, w, h))
            return b

        self._grid.addWidget(_home_btn("XYZ", "grblHoming", 170, 200, not rotary))
        self._mid = QVBoxLayout()
        self._mid.addWidget(_home_btn("X", "grblHomingX", 160, 91, not rotary))
        self._mid.addWidget(_home_btn("Y", "grblHomingY", 160, 91, not rotary))
        self._grid.addLayout(self._mid)
        self._grid.addWidget(_home_btn("Z", "grblHomingZ", 90, 200, True))
        self._v.addLayout(self._grid)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedSize(px(560), px(380))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(16)}px; }}"
        )
        self._v.setContentsMargins(px(40), px(36), px(40), px(36))
        self._v.setSpacing(px(28))

        self._head.setSpacing(px(16))
        self._hi.setPixmap(icons.pixmap("home", px(44), TXT))
        self._close.setFixedSize(px(32), px(32))
        self._close.setStyleSheet(
            f"background: transparent; border: none; "
            f"font-size: {fs(18)}px; color: {TXT2};"
        )

        self._grid.setSpacing(px(18))
        self._mid.setSpacing(px(18))
        for b, w, h in self._home_btns:
            b.setFixedSize(px(w), px(h))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {PANEL}; border: none; border-radius: {px(12)}px;
                    font-size: {fs(22)}px; color: {TXT};
                }}
                QPushButton:hover   {{ background: #D2D2D2; }}
                QPushButton:pressed {{ background: #C8C8C8; }}
                QPushButton:disabled {{ background: #EFEFEF; color: {TXT3}; }}
            """)

    def _run(self, command: str) -> None:
        # home() (not cmd()) so the status badge shows HOMING while it runs.
        self._conn.home(command)
        self.accept()


# ── Small input dialogs ───────────────────────────────────────────────────────

class TypeCoordinatesDialog(QDialog):
    """Move-to dialog — mobile 'move to' modal parity.

    A machine / job coordinate-system tab (each field's *placeholder* shows the
    current position in the selected system, updated live), per-axis inputs and
    a Go button, plus two quick-action cards (go to XY₀ / bed to front). Entering
    value(s) and pressing Go moves there in the selected system: job → ``move*``
    (work coords), machine → ``moveMachine*`` (absolute). In rotary mode the Y
    axis is replaced by A (degrees), the quick action becomes 'go to XA₀' and
    bed-to-front is disabled (mobile parity). The dialog issues the commands via
    *conn* itself and closes on action."""

    # axis → (job-position key, machine-position key)
    _AXIS_KEY = {"X": ("jx", "mx"), "Y": ("jy", "my"),
                 "Z": ("jz", "mz"), "A": ("ja", "ma")}

    def __init__(self, conn, parent=None, rotary: bool = False,
                 feed_provider=None):
        super().__init__(parent)
        self._conn = conn
        self._rotary = rotary
        self._feed = feed_provider or (lambda: 800)
        self._tab = "job"                       # "job" | "machine"
        self._axes = ["X", "A", "Z"] if rotary else ["X", "Y", "Z"]
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)

        self._axis_boxes: list = []
        self._inputs: dict[str, QLineEdit] = {}

        self._card = rounded_dialog_card(self)
        self._v = QVBoxLayout(self._card)

        # Title row
        self._top = QHBoxLayout()
        self._top.addWidget(lbl("Move to", sz=16, bold=True))
        self._top.addStretch()
        self._close_btn = QPushButton("⊗")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)
        self._top.addWidget(self._close_btn)
        self._v.addLayout(self._top)

        # Coordinate-system tab + per-axis inputs + Go
        self._tab_seg = self._make_tab()
        self._v.addWidget(self._tab_seg)
        for axis in self._axes:
            self._v.addLayout(self._make_axis_row(axis))
        self._v.addStretch()
        self._go = btn("go", bg=LIGHT, h=40, radius=8, bold=True)
        self._go.setCursor(Qt.PointingHandCursor)
        self._go.clicked.connect(self._on_go)
        self._go.setEnabled(False)
        self._v.addWidget(self._go)

        # Live position → field placeholders (disconnect on close).
        conn.position_changed.connect(self._refresh_placeholders)
        self.finished.connect(lambda *_: self._teardown())
        self._refresh_placeholders()

        bind(self, self._apply_scale)

    # ── builders ──────────────────────────────────────────────────────────────

    def _make_tab(self) -> QFrame:
        seg = QFrame(); seg.setObjectName("tabSeg")
        h = QHBoxLayout(seg)
        h.setContentsMargins(px(2), px(2), px(2), px(2)); h.setSpacing(0)
        grp = QButtonGroup(seg); grp.setExclusive(True)
        self._tab_machine = QPushButton("Machine")
        self._tab_job = QPushButton("Job")
        for b, tab in ((self._tab_machine, "machine"), (self._tab_job, "job")):
            b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, t=tab: self._set_tab(t))
            grp.addButton(b); h.addWidget(b, 1)
        self._tab_job.setChecked(True)          # job is the default (mobile parity)
        return seg

    def _make_axis_row(self, axis: str) -> QHBoxLayout:
        row = QHBoxLayout()
        al = lbl(axis, sz=14, bold=True)
        al.setFixedWidth(px(16))
        row.addWidget(al)
        box = QFrame(); box.setObjectName("axisBox")
        bl = QHBoxLayout(box)
        inp = QLineEdit()
        inp.setPlaceholderText("0.00")
        inp.textChanged.connect(self._update_go)
        self._inputs[axis] = inp
        bl.addWidget(inp, 1)
        unit = lbl("°" if axis == "A" else "mm", sz=12, color=TXT2)
        bl.addWidget(unit)
        self._axis_boxes.append((box, bl, inp, unit))
        row.addWidget(box, 1)
        return row

    # ── behaviour ──────────────────────────────────────────────────────────────

    def _set_tab(self, tab: str) -> None:
        self._tab = tab
        self._refresh_placeholders()

    @staticmethod
    def _parse(inp: QLineEdit):
        txt = inp.text().strip().replace(",", ".")
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _refresh_placeholders(self, *_):
        pos = self._conn.current_position()
        idx = 0 if self._tab == "job" else 1
        for axis, inp in self._inputs.items():
            key = self._AXIS_KEY[axis][idx]
            inp.setPlaceholderText(f"{pos.get(key, 0.0):.2f}")

    def _update_go(self, *_):
        self._go.setEnabled(
            any(self._parse(inp) is not None for inp in self._inputs.values()))

    def _on_go(self) -> None:
        feed = self._feed()
        vals = {a: self._parse(inp) for a, inp in self._inputs.items()}
        if not any(v is not None for v in vals.values()):
            return
        job = self._tab == "job"
        if self._rotary:
            # X / Z follow the coordinate system; A always rides moveA.
            if vals.get("X") is not None:
                self._conn.cmd("moveX" if job else "moveMachineX",
                               x=round(vals["X"], 4), feedRate=feed)
            if vals.get("A") is not None:
                self._conn.cmd("moveA", a=round(vals["A"], 4), feedRate=feed)
            if vals.get("Z") is not None:
                self._conn.cmd("moveZ" if job else "moveMachineZ",
                               z=round(vals["Z"], 4), feedRate=feed)
        else:
            # Combined move (mobile parity): unspecified axes hold their current
            # value in the selected coordinate system.
            pos = self._conn.current_position()
            idx = 0 if job else 1
            xyz = {}
            for a in ("X", "Y", "Z"):
                v = vals.get(a)
                xyz[a.lower()] = round(
                    v if v is not None else pos[self._AXIS_KEY[a][idx]], 4)
            self._conn.cmd("moveXYZ" if job else "moveMachineXYZ",
                           **xyz, feedRate=feed)
        self.accept()

    def _teardown(self) -> None:
        try:
            self._conn.position_changed.disconnect(self._refresh_placeholders)
        except (TypeError, RuntimeError):
            pass

    # ── styling ────────────────────────────────────────────────────────────────

    def _apply_scale(self, *_):
        rows = len(self._axes)
        self.setFixedSize(px(360), px(196 + rows * 52))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(14)}px; }}")
        self._v.setContentsMargins(px(24), px(20), px(24), px(20))
        self._v.setSpacing(px(12))

        self._close_btn.setFixedSize(px(28), px(28))
        self._close_btn.setStyleSheet(
            f"background: transparent; border: none; "
            f"font-size: {fs(18)}px; color: {TXT2};")

        self._tab_seg.setFixedHeight(px(36))
        self._tab_seg.setStyleSheet(
            f"#tabSeg {{ background: {LIGHT}; border: 1px solid {BORDER}; "
            f"border-radius: {px(10)}px; }}")
        for b in (self._tab_machine, self._tab_job):
            b.setFixedHeight(px(30))
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none;
                    border-radius: {px(8)}px; font-size: {fs(13)}px;
                    color: {TXT2}; }}
                QPushButton:checked {{ background: {WHITE}; color: {TXT};
                    font-weight: bold; }}
            """)

        for box, bl, inp, unit in self._axis_boxes:
            box.setFixedHeight(px(40))
            box.setStyleSheet(
                f"#axisBox {{ background: {LIGHT}; border: 1px solid {BORDER}; "
                f"border-radius: {px(8)}px; }}")
            bl.setContentsMargins(px(10), 0, px(10), 0)
            bl.setSpacing(px(6))
            inp.setStyleSheet(
                f"QLineEdit {{ background: transparent; border: none; "
                f"font-size: {fs(14)}px; color: {TXT}; }}")
        self._go.setFixedHeight(px(40))


class TextInputDialog(QDialog):
    """Single-line input dialog (rename file, reference Z, machine name)."""

    def __init__(self, title: str, label: str, initial: str = "",
                 suffix: str = "", parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)

        self._card = rounded_dialog_card(self)
        self._v = QVBoxLayout(self._card)
        self._v.addWidget(lbl(title, sz=18, bold=True))
        self._v.addWidget(lbl(label, sz=13, color=TXT2))

        self._row = QHBoxLayout()
        self._input = QLineEdit(initial)
        self._row.addWidget(self._input, 1)
        if suffix:
            self._row.addWidget(lbl(suffix, sz=14, color=TXT2))
        self._v.addLayout(self._row)

        self._br = QHBoxLayout()
        self._br.addStretch()
        ok = _dlg_button("✓", w=90)
        ok.clicked.connect(self.accept)
        cancel = _dlg_button("✕", w=90)
        cancel.clicked.connect(self.reject)
        self._br.addWidget(ok)
        self._br.addSpacing(px(16))
        self._br.addWidget(cancel)
        self._br.addStretch()
        self._v.addLayout(self._br)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedSize(px(420), px(210))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(14)}px; }}"
        )
        self._v.setContentsMargins(px(28), px(24), px(28), px(24))
        self._v.setSpacing(px(16))
        self._input.setFixedHeight(px(40))
        self._input.setStyleSheet(
            f"background: {LIGHT}; border: 1px solid {BORDER}; "
            f"border-radius: {px(8)}px; padding: {px(4)}px {px(10)}px; "
            f"font-size: {fs(14)}px;"
        )

    def text(self) -> str:
        return self._input.text().strip()
