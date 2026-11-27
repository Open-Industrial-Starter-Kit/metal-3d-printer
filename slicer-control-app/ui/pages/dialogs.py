"""
ui/pages/dialogs.py
Modal dialogs matching the Piccola CNC desktop design language: white card, large
icon, big title, warning rows, and gray rounded check/close buttons.

Includes the multi-phase procedure flows mirrored from the mobile app:
probe Z0, tool length, rotary mode, homing options, plus generic confirm /
input dialogs.
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


def _warning_row(text: str, icon_name: str | None = None,
                 dialog_w: int = 520) -> QWidget:
    """⚠ + wrapped text row used in confirm phases.

    The label is pinned to the width it will actually be given, because a
    wrapped QLabel's sizeHint is a guess at
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
        and the tallest of them came close to the height of a 1280x800 display — where the scale factor
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
        this reason (`_warning_row`, `_option_card`). Doing
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
    """Base for probe macros driven by procedure_completed signals."""

    completion_type = ""        # msg type that finishes the procedure

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        conn.procedure_completed.connect(self._on_completed)

    def done(self, r):
        # done() catches every close path (accept/reject/Esc); closeEvent does
        # not fire for programmatic dialog closes.
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
