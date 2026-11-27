"""
ui/pages/controls.py
Controls / Home page: jog pad, Z-axis pad, step-size and speed selectors,
and top action buttons (homing, unlock, soft reset, etc.).
"""

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QButtonGroup, QLabel, QSizePolicy, QSpacerItem, QDialog,
    QToolButton,
)
from PySide6.QtCore import Qt, QSize, QSettings, QTimer, QObject, Signal, QEvent
from PySide6.QtGui import QCursor, QGuiApplication, QRegion

from ui.theme import BG, PANEL, WHITE, LIGHT, BORDER, TXT, TXT2, TXT3, IDLE_C
from ui.widgets import lbl, btn, jog_btn, JogDirButton, hline, rounded_dialog_card
from ui.scale import fs, px, font, bind, autoscale
from ui import icons
from ui.tooltips import TRANSPARENT, TRANSPARENT_NO_BORDER


#: Width a multi-sentence tooltip wraps at (design px), as the CAM tooltip
#: panel does (ui/cam_group_tips.py).
TIP_WIDTH = 300


def _tip(*sentences: str) -> str:
    """A readable tooltip: one sentence per line, each a full sentence
    (capital, full stop), wrapped at TIP_WIDTH.

    A plain-text tooltip is drawn on one line however long it is -- "Go to
    X0"'s ran across most of the window. Rich text in a fixed-width table is
    what makes Qt wrap it."""
    import html
    body = "<br>".join(html.escape(s) for s in sentences)
    return (f'<table width="{px(TIP_WIDTH)}" cellspacing="0" cellpadding="0">'
            f"<tr><td>{body}</td></tr></table>")

# Step-size values (mm) indexed 0..3
_STEP_VALUES = [0.1, 1.0, 5.0, 10.0, 50.0, 100.0]   # X / Y step sizes (mm)
_Z_STEP_VALUES = [0.01, 0.05, 0.1, 1.0, 5.0, 10.0, 50.0]   # fine 0.01/0.05 up to 50 mm/step
# Feed rates (mm/min) for Slow / Normal / Fast — matches the Android app config.
_FEED_VALUES = [200, 750, 2000]
# A-axis (rotary) step choices in degrees.
_A_STEP_VALUES = [1.0, 10.0, 45.0, 90.0]
# Continuous-jog timing: a press released within the threshold is a discrete
# step move; holding longer streams jog keep-alives every 120 ms, comfortably
# below the firmware's 350 ms keep-alive watchdog.
#
# The threshold is deliberately NOT the mobile app's 200 ms. An ordinary tap —
# a stiff panel, a slow click — easily runs past 200 ms, and the two gestures
# then produce visibly different moves from what feels like the same press:
#
#   tap   -> _tap_fn(), a discrete move of exactly one step size
#   hold  -> conn.jog(), which runs while held and is cancelled on release
#
# so a tap misread as a hold moves the axis for the few tens of milliseconds it
# was held rather than by the step, and the axis lands *short* of where the step
# size said it would. That reads as the machine losing steps or the step setting
# being wrong, which is the worst way for a jog control to be wrong: it is the
# discrete step that is meant to be the precise, absolute one.
#
# 750 ms is long enough that a hold is unmistakably a decision, and it costs
# nothing the other way — a hold is still a hold, it just declares itself half a
# second later.
_JOG_HOLD_THRESHOLD_MS = 750
_JOG_KEEPALIVE_MS = 120
# Spindle target-RPM bounds / default (Android parity).
# No artificial upper cap on the spindle target (_SPINDLE_MAX_RPM = None); the
# firmware enforces the real hardware limit. Only a minimum-of-1 guard remains.
_SPINDLE_MIN_RPM, _SPINDLE_MAX_RPM, _SPINDLE_DEFAULT_RPM = 1, None, 8000


def _clamp_rpm(rpm: int) -> int:
    """Clamp a spindle target to the valid range: at least _SPINDLE_MIN_RPM, and
    capped at _SPINDLE_MAX_RPM only when a maximum is set (None = no upper cap)."""
    rpm = max(_SPINDLE_MIN_RPM, int(rpm))
    if _SPINDLE_MAX_RPM is not None:
        rpm = min(_SPINDLE_MAX_RPM, rpm)
    return rpm


# Evident disabled look for the action / detect buttons (their icon + text live
# in child QLabels that don't dim on their own, so we dim them explicitly).
_DISABLED_BG = "#EDEDED"
_DISABLED_BORDER = "#E2E2E2"
_DISABLED_FG = "#B7BBC1"


class IconButton(QPushButton):
    """Card-style button with an SVG icon on top and (wrapping) text below.

    The width is *flexible*: the button prefers ``w`` px but may shrink down to
    ``min_w`` px when the panel is narrow, so a full row of these never forces a
    horizontal scrollbar on the page. Height is fixed."""

    def __init__(self, icon_name: str | None, text: str,
                 checkable: bool = False, w: int = 106, h: int = 66,
                 icon_size: int = 20, min_w: int = 72):
        super().__init__()
        self.setCheckable(checkable)
        self._pref_w, self._min_w, self._fixed_h = w, min_w, h
        self._icon_name, self._icon_size = icon_name, icon_size
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        # Stretch-based centering (not layout AlignCenter): a centered layout
        # sizes a word-wrapped label to a single line, so the wrapped second
        # line of a long caption ("Type coordinates") overflowed and clipped.
        # With stretches top/bottom the label gets its true height-for-width.
        self._lay = QVBoxLayout(self)
        self._lay.addStretch(1)

        self._il = None
        if icon_name:
            il = QLabel()
            il.setAlignment(Qt.AlignCenter)
            il.setStyleSheet(TRANSPARENT_NO_BORDER)
            il.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._lay.addWidget(il, 0, Qt.AlignHCenter)
            self._il = il

        self._tl = QLabel(text)
        self._tl.setAlignment(Qt.AlignCenter)
        self._tl.setWordWrap(True)
        self._tl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._lay.addWidget(self._tl)

        self._lay.addStretch(1)
        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedHeight(px(self._fixed_h))
        self.setMinimumWidth(px(self._min_w))
        self.setMaximumWidth(px(self._pref_w))
        self.setStyleSheet(f"""
            QPushButton {{
                background: {WHITE}; border-radius: {px(10)}px;
                border: 1px solid {BORDER};
            }}
            QPushButton:hover   {{ background: #EFEFEF; }}
            QPushButton:pressed {{ background: #DCDCDC; }}
            QPushButton:checked {{ background: #E2EEF2; border: 1.5px solid {IDLE_C}; }}
            QPushButton:disabled {{ background: {_DISABLED_BG};
                                    border: 1px solid {_DISABLED_BORDER}; }}
        """)
        self._lay.setContentsMargins(px(4), px(5), px(4), px(5))
        self._lay.setSpacing(px(3))
        self._refresh_enabled_look()
        self.updateGeometry()

    def set_text(self, text: str) -> None:
        """Change the caption (the Bed button reads differently in rotary mode)."""
        self._tl.setText(text)

    def set_icon(self, icon_name: str) -> None:
        """Change the icon (Frame becomes Stock length in rotary mode)."""
        if self._il is None or icon_name == self._icon_name:
            return
        self._icon_name = icon_name
        self._refresh_enabled_look()

    def _refresh_enabled_look(self) -> None:
        """Dim the icon + caption when disabled (child QLabels don't inherit
        the button's disabled state, so without this the button looks active)."""
        fg = TXT if self.isEnabled() else _DISABLED_FG
        if self._il is not None:
            self._il.setPixmap(icons.pixmap(self._icon_name, px(self._icon_size), fg))
        self._tl.setStyleSheet(
            f"background: transparent; border: none; font-size: {fs(11)}px; "
            f"color: {fg};")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.EnabledChange:
            self._refresh_enabled_look()

    # Prefer the design width so a row of these grows to fill the panel, but
    # report the smaller minimum so the layout can compress them (instead of
    # forcing a page-level horizontal scrollbar) when the window is narrow.
    def sizeHint(self) -> QSize:
        return QSize(px(self._pref_w), px(self._fixed_h))

    def minimumSizeHint(self) -> QSize:
        return QSize(px(self._min_w), px(self._fixed_h))


class TopActionButton(QPushButton):
    """Top-row action button: icon on the left, text on the right. Gray by
    default; ``white=True`` gives a bordered white card (used for *unlock*)."""

    def __init__(self, icon_name: str, text: str,
                 checkable: bool = False, white: bool = False):
        super().__init__()
        self.setCheckable(checkable)
        self._white = white
        self._icon_name = icon_name
        self._fg = TXT2 if white else TXT
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(QCursor(Qt.PointingHandCursor))

        # Compact icon + text so the seven-button row still shows full labels
        # at the minimum window width (no clipping like "spindl"/"contro").
        self._lay = QHBoxLayout(self)
        self._lay.setAlignment(Qt.AlignCenter)

        self._il = QLabel()
        self._il.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._il.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._lay.addWidget(self._il)

        self._tl = QLabel(text)
        self._tl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._lay.addWidget(self._tl)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setMinimumHeight(px(84))
        bg     = WHITE if self._white else PANEL
        border = f"1px solid {BORDER}" if self._white else "none"
        hover  = "#F4F4F4" if self._white else "#D2D2D2"
        press  = "#E8E8E8" if self._white else "#C8C8C8"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; border-radius: {px(16)}px; border: {border};
            }}
            QPushButton:hover   {{ background: {hover}; }}
            QPushButton:pressed {{ background: {press}; }}
            QPushButton:checked {{ background: #E2EEF2; border: 1.5px solid {IDLE_C}; }}
            QPushButton:disabled {{ background: {_DISABLED_BG};
                                    border: 1px solid {_DISABLED_BORDER}; }}
        """)
        self._lay.setContentsMargins(px(8), px(8), px(8), px(8))
        self._lay.setSpacing(px(8))
        self._refresh_enabled_look()

    def _refresh_enabled_look(self) -> None:
        """Dim the icon + label when disabled (child QLabels don't follow the
        button's disabled state on their own)."""
        fg = self._fg if self.isEnabled() else _DISABLED_FG
        self._il.setPixmap(icons.pixmap(self._icon_name, px(26), fg))
        self._tl.setStyleSheet(
            f"background: transparent; border: none; font-size: {fs(15)}px; "
            f"color: {fg};")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.EnabledChange:
            self._refresh_enabled_look()


class ToolLengthButton(QFrame):
    """Compound tool-length control: icon, title, editable value pill, and a
    'detect' sub-button (white card)."""

    #: Shown until the machine reports a tool length. The card must never
    #: invent a number — anything in this pill reads as a real measurement.
    PLACEHOLDER = "—"

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._lay = QHBoxLayout(self)

        self._icon = QLabel()
        self._icon.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._lay.addWidget(self._icon)
        self._lay.addStretch()          # centre the title/pill between icon and detect

        # Title + editable value pill
        self._center = QVBoxLayout()
        self._center.setAlignment(Qt.AlignCenter)
        self._title = QLabel("Tool length")
        self._title.setAlignment(Qt.AlignCenter)
        self._center.addWidget(self._title)

        self._pill = QFrame()
        self._pill_l = QHBoxLayout(self._pill)
        self._value_lbl = QLabel(self.PLACEHOLDER)
        self._value_lbl.setAlignment(Qt.AlignCenter)
        self._pill_l.addWidget(self._value_lbl)
        self._center.addWidget(self._pill)
        self._lay.addLayout(self._center)
        self._lay.addStretch()          # …and keep the detect button hard right

        # Detect sub-button (wifi icon + label)
        self._detect_btn = QPushButton()
        self._detect_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._db = QVBoxLayout(self._detect_btn)
        self._db.setAlignment(Qt.AlignCenter)
        self._wifi = QLabel()
        self._wifi.setAlignment(Qt.AlignCenter)
        self._wifi.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._wifi.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._db.addWidget(self._wifi)
        self._dl = QLabel("Detect")
        self._dl.setAlignment(Qt.AlignCenter)
        self._dl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._db.addWidget(self._dl)
        self._lay.addWidget(self._detect_btn)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setMinimumHeight(px(84))
        # Dim the whole card (icon, text, pill, detect sub-button) while disabled
        # — child labels/buttons don't follow the frame's disabled state on their
        # own, so without this the tool-length card still looks active during a
        # job or manual motion.
        on = self.isEnabled()
        fg = TXT if on else _DISABLED_FG
        fg2 = TXT2 if on else _DISABLED_FG
        card_bg = WHITE if on else _DISABLED_BG
        card_bd = BORDER if on else _DISABLED_BORDER
        self.setStyleSheet(
            f"background: {card_bg}; border-radius: {px(12)}px; "
            f"border: 1px solid {card_bd};"
        )
        self._lay.setContentsMargins(px(16), px(8), px(12), px(8))
        self._lay.setSpacing(px(12))
        self._icon.setPixmap(icons.pixmap("tool-length", px(34), fg))
        self._center.setSpacing(px(4))
        self._title.setStyleSheet(
            f"background: transparent; border: none; font-size: {fs(15)}px; color: {fg};"
        )
        self._pill.setStyleSheet(
            f"background: {LIGHT if on else _DISABLED_BG}; "
            f"border-radius: {px(15)}px; border: none;"
        )
        self._pill_l.setContentsMargins(px(14), px(4), px(14), px(4))
        self._pill_l.setSpacing(px(8))
        self._value_lbl.setStyleSheet(
            f"background: transparent; border: none; font-size: {fs(13)}px; color: {fg2};"
        )
        self._detect_btn.setFixedSize(px(64), px(58))
        self._detect_btn.setStyleSheet(f"""
            QPushButton {{ background: {PANEL if on else _DISABLED_BG};
                           border: none; border-radius: {px(10)}px; }}
            QPushButton:hover {{ background: {'#D2D2D2' if on else _DISABLED_BG}; }}
        """)
        self._db.setContentsMargins(px(6), px(4), px(6), px(4))
        self._db.setSpacing(px(2))
        self._wifi.setPixmap(icons.pixmap("detect-sensor", px(26), fg))
        self._dl.setStyleSheet(
            f"background: transparent; border: none; font-size: {fs(12)}px; color: {fg};"
        )

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.EnabledChange:
            self._apply_scale()

    def set_value(self, text: str) -> None:
        self._value_lbl.setText(text)

    def clear_value(self) -> None:
        """Back to the placeholder — no machine, no measurement."""
        self._value_lbl.setText(self.PLACEHOLDER)


# ── Sub-widgets ───────────────────────────────────────────────────────────────

class _CapsuleFrame(QFrame):
    """A capsule/pill frame that *clips its children* to its rounded outline.

    Needed because the Fusion style ignores a large ``border-radius`` on a
    QPushButton, so a selected segment's fill renders near-square and would poke
    past the capsule ends. A rounded mask clips everything to the capsule."""

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        r = h // 2                                   # capsule: radius = half height
        reg = QRegion(r, 0, w - 2 * r, h)            # central rectangle
        reg = reg.united(QRegion(0, 0, 2 * r, h, QRegion.Ellipse))          # left cap
        reg = reg.united(QRegion(w - 2 * r, 0, 2 * r, h, QRegion.Ellipse))  # right cap
        self.setMask(reg)


def _seg_radius(shape: str) -> str:
    """Scaled CSS corner-radius for a segmented-cell shape."""
    r = px(15)
    if shape == "all":
        return f"{r}px"
    if shape == "left":
        return f"{r}px 0 0 {r}px"
    if shape == "right":
        return f"0 {r}px {r}px 0"
    return "0"


def _make_seg_styler(shape: str, fill_bg: str, min_seg_w: int):
    """A scale-aware styler for one segment button (radius depends on position)."""
    def _style(b):
        radius = _seg_radius(shape)
        b.setFixedHeight(px(30))
        b.setMinimumWidth(px(min_seg_w))
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {fs(12)}px; color: {TXT};
            }}
            QPushButton:hover   {{ background: rgba(0,0,0,0.04);
                                   border-radius: {radius}; }}
            QPushButton:checked {{ background: {fill_bg};
                                   border-radius: {radius}; font-weight: bold; }}
            QPushButton:disabled {{ color: {_DISABLED_FG}; }}
        """)
    return _style


def _segmented_pill(labels, default, track_bg, fill_bg, min_seg_w=36):
    """Build one rounded 'segmented control': a *track_bg* capsule with fully
    rounded ends, thin 1px dividers between cells, and the active cell filled
    solid *fill_bg* edge-to-edge — rounded only at the capsule's outer ends, so
    the selection reads as a full-cell fill and never as a smaller pill floating
    inside. Returns ``(pill QFrame, QButtonGroup)``."""
    pill = _CapsuleFrame()
    pill.setObjectName("segPill")

    def _style_pill(p):
        p.setFixedHeight(px(34))
        p.setStyleSheet(
            f"#segPill {{ background: {track_bg}; border: 1px solid {BORDER}; "
            f"border-radius: {px(17)}px; }}"
        )
    autoscale(pill, _style_pill)

    pl = QHBoxLayout(pill)
    # Left/right inset clears the 1px border so the end-cell fills nest; no
    # top/bottom inset so the dividers run edge-to-edge (top to bottom).
    pl.setContentsMargins(px(2), 0, px(2), 0)
    pl.setSpacing(0)

    n = len(labels)
    group = QButtonGroup(pill)
    for i, text in enumerate(labels):
        if i > 0:
            div = QFrame()

            def _style_div(d):
                d.setFixedWidth(1)                   # 1px width stays literal
                d.setStyleSheet(f"background: {BORDER}; border: none;")
            autoscale(div, _style_div)
            # Fill the full pill height so the separators go bottom-to-top.
            div.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            pl.addWidget(div)
        if n == 1:
            shape = "all"
        elif i == 0:
            shape = "left"
        elif i == n - 1:
            shape = "right"
        else:
            shape = "none"
        b = QPushButton(text)
        b.setCheckable(True)
        b.setChecked(i == default)
        b.setCursor(QCursor(Qt.PointingHandCursor))
        autoscale(b, _make_seg_styler(shape, fill_bg, min_seg_w))
        group.addButton(b, i)
        pl.addWidget(b, 1)
    return pill, group


class StepSizeRow(QWidget):
    """Labeled row of step-size values shown as one segmented pill plus a
    custom-step pill: ``X  [ 0.1 | 1 | 10 | 100 ]  …``. The active value fills
    its cell solid grey edge-to-edge (rounded only at the pill ends). Clicking
    the trailing "…" pill asks for a custom step (0.01–500 mm) which then shows
    on the pill and becomes the selected step."""

    value_changed = Signal()   # effective step size changed (segment or custom)

    def __init__(self, axis_label: str, default: int = 2,
                 values: list[float] | None = None,
                 cmin: float = 0.1, cmax: float = 100.0,
                 min_seg_w: int = 36):
        super().__init__()
        self._values = list(values) if values is not None else list(_STEP_VALUES)
        self._cmin, self._cmax = cmin, cmax
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)

        self._ax = lbl(axis_label, sz=12, bold=True)
        self._row.addWidget(self._ax)

        # White capsule with a solid grey fill on the selected step.
        pill, self._group = _segmented_pill(
            [f"{v:g}" for v in self._values], default,
            track_bg=WHITE, fill_bg=LIGHT, min_seg_w=min_seg_w)
        self._row.addWidget(pill, 1)
        self._group.idClicked.connect(self._on_segment_clicked)

        # "…" — custom step-size entry, sits outside the pill.
        self._custom_value: float | None = None
        self._custom_active = False
        self._extra = QPushButton("…")
        self._extra.setCursor(QCursor(Qt.PointingHandCursor))
        self._extra.setToolTip("Custom step size")
        self._extra.clicked.connect(self._on_custom_clicked)
        self._row.addWidget(self._extra)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._row.setSpacing(px(8))
        self._ax.setFixedWidth(px(14))
        self._extra.setFixedHeight(px(30))
        self._extra.setMinimumWidth(px(42))
        self._style_extra()

    def _style_extra(self) -> None:
        """Restyle the custom pill: filled grey + bold while it is the selected
        step (mirroring a checked segment), plain white otherwise."""
        bg = LIGHT if self._custom_active else WHITE
        fg = TXT if self._custom_active else TXT2
        weight = "font-weight: bold;" if self._custom_active else ""
        self._extra.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 1px solid {BORDER}; "
            f"border-radius: {px(15)}px; color: {fg}; font-size: {fs(12)}px; "
            f"{weight} }}"
            f"QPushButton:hover {{ background: rgba(0,0,0,0.04); }}"
        )

    def _on_segment_clicked(self, _i: int) -> None:
        if self._custom_active:
            self._custom_active = False
            self._style_extra()
        self.value_changed.emit()

    def _on_custom_clicked(self) -> None:
        from ui.pages.dialogs import TextInputDialog
        initial = f"{self._custom_value:g}" if self._custom_value is not None else ""
        dlg = TextInputDialog("Custom step", "Step size must be a number "
                              f"between {self._cmin:g} and {self._cmax:g}.",
                              initial=initial,
                              suffix="mm", parent=self.window())
        if not dlg.exec():
            return
        try:
            v = float(dlg.text().replace(",", "."))
        except ValueError:
            return
        if not (self._cmin <= v <= self._cmax):
            return
        self._custom_value = v
        self._extra.setText(f"{v:g}")
        if not self._custom_active:
            # De-select the segmented pill so the custom pill reads as active.
            self._group.setExclusive(False)
            checked = self._group.checkedButton()
            if checked is not None:
                checked.setChecked(False)
            self._group.setExclusive(True)
            self._custom_active = True
        self._style_extra()
        self.value_changed.emit()

    def set_max(self, limit: float | None) -> None:
        """Grey out the segments above *limit* (None: all usable). Rotary Mode
        asks for this: with the bar in the chuck a 50 mm X step or a 10 mm Z
        step is a crash waiting for a tap, so those stay visible but dead. The
        custom "…" pill is left alone. If the selected segment is one that just
        went dead, the largest usable one takes over and value_changed fires."""
        changed = False
        for i, v in enumerate(self._values):
            b = self._group.button(i)
            if b is None:
                continue
            ok = limit is None or v <= limit
            if b.isEnabled() != ok:
                b.setEnabled(ok)
                b.setToolTip("" if ok else _tip(f"{v:g} mm is too big a step in Rotary Mode."))
            if not ok and b.isChecked():
                changed = True
        if changed:
            best = max((i for i, v in enumerate(self._values)
                        if limit is None or v <= limit), default=None)
            if best is not None:
                self._group.button(best).setChecked(True)
                self.value_changed.emit()

    def value(self) -> float:
        """Return the currently selected step size in mm."""
        if self._custom_active and self._custom_value is not None:
            return self._custom_value
        idx = self._group.checkedId()
        return self._values[idx] if 0 <= idx < len(self._values) else 10.0

    def copy_selection_from(self, other: "StepSizeRow") -> None:
        """Mirror *other*'s current selection (segment or custom step) onto this
        row. Programmatic setChecked doesn't fire idClicked, so this does not
        emit value_changed — the caller drives any follow-up refresh."""
        if other._custom_active and other._custom_value is not None:
            self._custom_value = other._custom_value
            self._extra.setText(f"{other._custom_value:g}")
            if not self._custom_active:
                self._group.setExclusive(False)
                checked = self._group.checkedButton()
                if checked is not None:
                    checked.setChecked(False)
                self._group.setExclusive(True)
                self._custom_active = True
            self._style_extra()
        else:
            idx = other._group.checkedId()
            b = self._group.button(idx) if idx >= 0 else None
            if b is not None:
                if self._custom_active:
                    self._custom_active = False
                    self._style_extra()
                b.setChecked(True)


class SpeedRow(QWidget):
    """Slow | Normal | Fast selector — one rounded white pill with the active
    segment filled solid grey (rounded ends, matching the step-size pills)."""

    def __init__(self, default: int = 0):
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)

        # Indent/right-reserve to line the pill up with the step-size pills above
        # (their X/Y axis-label column on the left and "45" column on the right).
        self._lead = QSpacerItem(px(14), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._row.addItem(self._lead)
        pill, self._group = _segmented_pill(
            ["Slow", "Normal", "Fast"], default,
            track_bg=WHITE, fill_bg=LIGHT, min_seg_w=54)
        self._row.addWidget(pill, 1)
        self._trail = QSpacerItem(px(42), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._row.addItem(self._trail)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._row.setSpacing(px(8))
        # Keep the pill aligned with the step-size pills above as the scale changes.
        self._lead.changeSize(px(14), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._trail.changeSize(px(42), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._row.invalidate()

    def feed_mmmin(self) -> int:
        """Return the selected feed rate in mm/min."""
        idx = self._group.checkedId()
        return _FEED_VALUES[idx] if 0 <= idx < len(_FEED_VALUES) else 800


class _InfoBox(QFrame):
    """Centre read-out box: ↔ header, coordinate line(s), divider, speed label."""

    def __init__(self, lines: list[str], w: int = 104, h: int = 104):
        super().__init__()
        self._w, self._h = w, h
        # Scope the border/radius to this frame via an id selector. A bare
        # (selector-less) stylesheet cascades `border` onto every child label,
        # which drew an unwanted rectangle around each readout line.
        self.setObjectName("infoBox")
        self._lay = QVBoxLayout(self)
        self._lay.setAlignment(Qt.AlignCenter)

        self._lay.addWidget(lbl("↔", sz=14, bold=True, color=TXT, align=Qt.AlignCenter))
        self._line_lbls = []
        for text in lines:
            l = lbl(text, sz=11, color=TXT2, align=Qt.AlignCenter)
            self._line_lbls.append(l)
            self._lay.addWidget(l)

        self._divider = hline()
        self._lay.addWidget(self._divider, alignment=Qt.AlignHCenter)

        self._speed_lbl = lbl("Slow", sz=10, color=TXT3, align=Qt.AlignCenter)
        self._lay.addWidget(self._speed_lbl)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self.setFixedSize(px(self._w), px(self._h))
        self.setStyleSheet(
            f"#infoBox {{ background: transparent; border-radius: {px(22)}px; "
            f"border: 1px solid {BORDER}; }}"
        )
        self._lay.setContentsMargins(px(8), px(8), px(8), px(8))
        self._lay.setSpacing(px(2))
        self._divider.setFixedWidth(px(self._w - 28))

    def set_lines(self, lines: list[str], speed: str) -> None:
        for l, text in zip(self._line_lbls, lines):
            l.setText(text)
        self._speed_lbl.setText(speed)


class JogPad(QWidget):
    """XY directional jog pad — cardinal buttons rectangular, diagonals circular."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background: {BG}; border: none;")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        grid = QGridLayout(self)
        self._grid = grid

        # Cardinal buttons: rectangular, 72 × 72 px. Triangle points outward,
        # axis label sits on the inner (centre-facing) edge.
        self._btn_n = JogDirButton("up",    "Y", size=72)                       # ▲ / Y
        self._btn_s = JogDirButton("down",  "Y", size=72, label_first=True)     # Y / ▼
        self._btn_w = JogDirButton("left",  "X", size=72, horizontal=True)      # ◀ X
        self._btn_e = JogDirButton("right", "X", size=72, horizontal=True,
                                   label_first=True)                            # X ▶

        # Diagonal buttons: circular, 62 × 62 px. Arrow glyphs are the exact
        # design paths (NW → right, NE → left, SW → right-up, SE → left-up).
        self._btn_nw = JogDirButton(svg="nw", size=62, circular=True)
        self._btn_ne = JogDirButton(svg="ne", size=62, circular=True)
        self._btn_sw = JogDirButton(svg="sw", size=62, circular=True)
        self._btn_se = JogDirButton(svg="se", size=62, circular=True)

        self._info = _InfoBox(["X 100", "Y 100"], w=104, h=104)
        self._rotary = False

        #       col0     col1     col2
        # row0:  nw       n        ne
        # row1:  w      center      e
        # row2:  sw       s        se
        C = Qt.AlignCenter
        grid.addWidget(self._btn_nw, 0, 0, C)
        grid.addWidget(self._btn_n,  0, 1, C)
        grid.addWidget(self._btn_ne, 0, 2, C)
        grid.addWidget(self._btn_w,  1, 0, C)
        grid.addWidget(self._info,   1, 1, C)
        grid.addWidget(self._btn_e,  1, 2, C)
        grid.addWidget(self._btn_sw, 2, 0, C)
        grid.addWidget(self._btn_s,  2, 1, C)
        grid.addWidget(self._btn_se, 2, 2, C)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._grid.setSpacing(px(6))
        self._grid.setContentsMargins(px(2), px(2), px(2), px(2))

    def set_rotary(self, rotary: bool) -> None:
        """Rotary-axis parity with the mobile app: the vertical (N/S) buttons
        become the A axis, the four diagonal buttons disappear (combined XY
        moves have no rotary equivalent), and the centre read-out shows A."""
        self._rotary = bool(rotary)
        axis = "A" if self._rotary else "Y"
        self._btn_n.set_axis_label(axis)
        self._btn_s.set_axis_label(axis)
        for d in (self._btn_nw, self._btn_ne, self._btn_sw, self._btn_se):
            d.setVisible(not self._rotary)

    def set_info(self, step_x: str, step_y: str, speed: str) -> None:
        axis = "A" if self._rotary else "Y"
        self._info.set_lines([f"X {step_x}", f"{axis} {step_y}"], speed)


class ZPad(QWidget):
    """Z-axis jog column (up / info / down) with the Z-top-limit button to the
    right of the up button."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid = QGridLayout(self)
        self._grid = grid

        self._btn_up = JogDirButton("up", "Z", size=72)                     # ▲ / Z
        self._btn_down = JogDirButton("down", "Z", size=72, label_first=True)  # Z / ▼

        top_stop = jog_btn("", size=46, bg=WHITE)
        top_stop.setToolTip("Z top limit")

        self._info = _InfoBox(["Z 10"], w=96, h=88)
        self._btn_ztop = top_stop

        #         col0          col1
        # row0:   up button     z-top (top-aligned)
        # row1:   z_info
        # row2:   down button
        grid.addWidget(self._btn_up,   0, 0, Qt.AlignCenter)
        grid.addWidget(top_stop,       0, 1, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self._info,     1, 0, Qt.AlignCenter)
        grid.addWidget(self._btn_down, 2, 0, Qt.AlignCenter)
        grid.setRowStretch(3, 1)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._grid.setSpacing(px(6))
        self._grid.setContentsMargins(px(2), px(2), px(2), px(2))
        self._btn_ztop.setIcon(icons.icon("z-top", px(22), TXT))
        self._btn_ztop.setIconSize(QSize(px(22), px(22)))

    def set_info(self, step_z: str, speed: str) -> None:
        self._info.set_lines([f"Z {step_z}"], speed)


class _HoldJog(QObject):
    """Tap-vs-hold dispatcher for one jog button (firmware README 5.31.1/5.31.2).

    A press released within _JOG_HOLD_THRESHOLD_MS is a *tap* and runs
    ``tap_fn`` — the existing discrete step move, precise and absolute. Holding
    longer starts a continuous jog: ``conn.jog(feed, **signs)`` fires
    immediately and then repeats every _JOG_KEEPALIVE_MS as a keep-alive (the
    firmware watchdog cancels 350 ms after the last one); releasing stops the
    timer and sends ``conn.jog_cancel()`` for an instant stop.

    Press and release come from the button's own **mouse events**, not from its
    ``pressed`` / ``released`` signals, because those do not mean what a held
    jog needs them to mean (both measured against PySide6 6.11):

    * ``released`` fires when the pointer merely *drifts off* a button that is
      still physically held — QAbstractButton un-presses itself on the way out.
      A finger sliding a few pixels on a touch panel therefore cancelled the jog
      mid-hold, which is what "continuous jogging does not work" looks like.
    * ``released`` does **not** fire when the button is finally let go outside
      its own rect; only the raw ``MouseButtonRelease`` is delivered. So that
      jog was never cancelled — it ran on until the firmware's 350 ms keep-alive
      watchdog stopped it — and the keep-alive timer was left running, so the
      next tap on that arrow fired a stray ``jog`` and started an uncommanded
      continuous move.

    The mouse events have neither problem: pressing grabs the mouse implicitly,
    so the matching release is delivered here wherever the pointer has wandered
    to, and nothing in between is mistaken for a release. ``clicked`` stays
    connected for keyboard activation (space / enter) only — the swallow flag
    below drops the one Qt emits after a mouse release we have already handled.
    """

    def __init__(self, button: QPushButton, conn, signs: dict, feed_fn, tap_fn,
                 hold_enabled_fn=None):
        super().__init__(button)
        self._btn = button
        self._conn = conn
        self._signs = signs          # e.g. {"x": +1, "y": -1} — sign only
        self._feed_fn = feed_fn      # () -> feed rate (mm/min)
        self._tap_fn = tap_fn        # discrete step move
        # () -> bool gating continuous hold. The rotary A axis has no firmware
        # jog (README 5.31), so its buttons pass a fn returning False and stay
        # discrete-tap only, matching the mobile app.
        self._hold_enabled_fn = hold_enabled_fn
        self._pressed = False
        self._continuous = False
        self._swallow_click = False
        self._feed = 0

        self._hold_timer = QTimer(button)
        self._hold_timer.setSingleShot(True)
        # Precise, not Qt's default coarse timer, which is documented as free
        # to fire up to 5% early -- 37 ms on 750, so a press of a little over
        # 710 ms could start a continuous jog. The whole point of the threshold
        # is that a press under it is a step of exactly the step size, and
        # "under it" has to mean the number on the tin. Measured: a 700 ms
        # press was starting continuous jogs about one run in six.
        self._hold_timer.setTimerType(Qt.PreciseTimer)
        self._hold_timer.setInterval(_JOG_HOLD_THRESHOLD_MS)
        self._hold_timer.timeout.connect(self._start_continuous)

        self._keepalive = QTimer(button)
        self._keepalive.setInterval(_JOG_KEEPALIVE_MS)
        self._keepalive.timeout.connect(self._send_jog)

        button.installEventFilter(self)
        button.clicked.connect(self._on_clicked)

    def eventFilter(self, obj, ev) -> bool:
        et = ev.type()
        if (et in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick)
                and ev.button() == Qt.LeftButton):
            # DblClick, because that is what the second press of a fast repeat
            # arrives as — Qt sends it *instead of* MouseButtonPress, and only
            # QWidget's own handler forwards it on to mousePressEvent, which is
            # after this filter. Without it every other tap of someone stepping
            # the axis quickly would be dropped.
            self._on_pressed()
        elif et == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            inside = self._btn.rect().contains(ev.position().toPoint())
            # Qt only emits clicked() for a release inside the button, so that
            # is the only case whose clicked() has to be swallowed.
            self._swallow_click = inside
            self._release(tap=inside)
        elif et == QEvent.Hide or (et == QEvent.EnabledChange
                                   and not self._btn.isEnabled()):
            # The page was switched, or the gate disabled the pad mid-hold (a
            # job started, the door opened, the link dropped). Both take the
            # press away: a hidden or disabled widget loses the implicit mouse
            # grab, so the release goes somewhere else and never reaches us.
            # Stop and cancel here rather than leaving the machine to the
            # firmware's 350 ms watchdog.
            self._release(tap=False)
        return False                 # never consume: the button still paints

    def _on_pressed(self) -> None:
        self._pressed = True
        self._continuous = False
        if self._hold_enabled_fn is not None and not self._hold_enabled_fn():
            return                   # tap-only mode: never arm continuous jog
        self._hold_timer.start()

    def _start_continuous(self) -> None:
        self._continuous = True
        self._feed = self._feed_fn()
        self._send_jog()
        self._keepalive.start()

    def _send_jog(self) -> None:
        # Safety net for a release we never saw: a modal dialog stealing the
        # mouse grab, the window deactivating mid-hold. It asks the platform
        # whether the button is still physically down rather than whether the
        # widget still considers itself pressed — the pointer drifting off the
        # arrow clears the latter while the user is still holding it.
        if not (QGuiApplication.mouseButtons() & Qt.LeftButton):
            self._release(tap=False)
            return
        self._conn.jog(self._feed, **self._signs)

    def _release(self, tap: bool) -> None:
        """End the press. *tap* is True only for a release inside the button
        that never became a hold — that alone runs the discrete step move."""
        was_press = self._pressed
        self._pressed = False
        self._hold_timer.stop()
        self._keepalive.stop()
        if self._continuous:
            self._continuous = False
            self._conn.jog_cancel()
        elif was_press and tap:
            self._tap_fn()

    def _on_clicked(self) -> None:
        # Keyboard activation only. A mouse click has already been handled by
        # the release above, and this is the clicked() that follows it.
        if self._swallow_click:
            self._swallow_click = False
            return
        self._tap_fn()


def _section_header(text: str) -> QWidget:
    """A small section label followed by a horizontal rule filling the row,
    used to separate 'Step Size' from 'Speed' (and the XY / Z blocks)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(px(8))
    h.addWidget(lbl(text, sz=11, color=TXT2))
    h.addWidget(hline(), 1, Qt.AlignVCenter)
    return w


class SpindlePanel(QDialog):
    """Spindle control popup (Android SpindleControlBar parity): −1000/−100/
    +100/+1000 buttons around a target-RPM chip, a live measured-RPM readout
    and an ON/OFF power button. The target RPM (min 1, default 8000, no upper
    cap — the firmware enforces the hardware limit) is
    persisted via QSettings; adjusting it while the spindle runs re-sends
    ``M3 S<rpm>`` so the change applies live.

    With the door open every control here is locked out (see
    :meth:`set_door_blocked`) — the popup stays up, showing the live measured
    RPM and why it is inert."""

    def __init__(self, conn, running: bool = False,
                 door_blocked: bool = False, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._running = running
        self._door_blocked = bool(door_blocked)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)

        self._prefs = QSettings("InMachines", "MiniCNC2")
        try:
            rpm = int(self._prefs.value("controls/spindleTargetRpm",
                                        _SPINDLE_DEFAULT_RPM))
        except (TypeError, ValueError):
            rpm = _SPINDLE_DEFAULT_RPM
        self._rpm = _clamp_rpm(rpm)

        self._card = rounded_dialog_card(self)
        self._v = QVBoxLayout(self._card)

        # Title row with a top-right cross close (matching the move-to popup).
        self._top = QHBoxLayout()
        self._top.addSpacing(px(28))          # balance the cross so the title stays centred
        self._top.addWidget(lbl("Spindle Speed", sz=18, bold=True,
                                align=Qt.AlignCenter), 1)
        self._close_btn = QPushButton("⊗")
        self._close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._close_btn.clicked.connect(self.reject)
        self._top.addWidget(self._close_btn)
        self._v.addLayout(self._top)

        # Target chip flanked by the coarse/fine adjust buttons.
        self._adjust_row = QHBoxLayout()

        # Styled in _apply_scale rather than through btn(): these have to carry a
        # :disabled rule (the factory's inline colours survive setEnabled(False),
        # so a locked-out button would look live).
        self._adj_btns: list[QPushButton] = []

        def _adj(delta: int) -> QPushButton:
            b = QPushButton(f"{delta:+d}")
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.clicked.connect(lambda *_: self._adjust_rpm(delta))
            self._adj_btns.append(b)
            return b

        self._chip = QFrame()
        self._chip.setObjectName("rpmChip")
        self._chip_l = QHBoxLayout(self._chip)
        self._rpm_lbl = lbl(f"{self._rpm} RPM", sz=15, bold=True,
                            align=Qt.AlignCenter)
        self._chip_l.addWidget(self._rpm_lbl)

        self._adjust_row.addWidget(_adj(-1000))
        self._adjust_row.addWidget(_adj(-100))
        self._adjust_row.addWidget(self._chip, 1)
        self._adjust_row.addWidget(_adj(+100))
        self._adjust_row.addWidget(_adj(+1000))
        self._v.addLayout(self._adjust_row)

        self._measured_lbl = lbl("0 RPM", sz=13, color=TXT2,
                                 align=Qt.AlignCenter)
        self._v.addWidget(self._measured_lbl)

        # Why the controls below are dead. Only shown while the door blocks
        # them; the card grows by this row's height (see _apply_scale).
        self._door_note = lbl("⚠  Door open — close it to control the spindle",
                              sz=12, color=TXT2, align=Qt.AlignCenter)
        self._v.addWidget(self._door_note)

        self._br = QHBoxLayout()
        self._br.addStretch()
        # Circular power button (mobile parity): white when off, green when the
        # spindle is running, with a power icon over an ON / OFF label.
        self._power = QToolButton()
        self._power.setCheckable(True)
        self._power.setChecked(running)
        self._power.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._power.setCursor(QCursor(Qt.PointingHandCursor))
        self._power.clicked.connect(self._toggle_power)
        self._br.addWidget(self._power)
        self._br.addStretch()
        self._v.addLayout(self._br)

        bind(self, self._apply_scale)
        self._refresh_power()
        self._apply_door_lock()

    def _apply_scale(self, *_):
        # The door note is an extra row, so the card is taller while it shows.
        self.setFixedSize(px(500), px(276 if self._door_blocked else 240))
        self._card.setStyleSheet(
            f"#dialogCard {{ background: {WHITE}; border-radius: {px(14)}px; }}")
        self._v.setContentsMargins(px(28), px(20), px(28), px(24))
        self._v.setSpacing(px(14))
        self._close_btn.setFixedSize(px(28), px(28))
        self._close_btn.setStyleSheet(
            f"background: transparent; border: none; "
            f"font-size: {fs(18)}px; color: {TXT2};")
        self._adjust_row.setSpacing(px(8))
        for b in self._adj_btns:
            b.setMinimumHeight(px(34))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {WHITE}; color: {TXT};
                    border: 1px solid {BORDER}; border-radius: {px(8)}px;
                    padding: {px(2)}px {px(10)}px; min-width: {px(48)}px;
                }}
                QPushButton:hover    {{ background: #E0E0E0; }}
                QPushButton:pressed  {{ background: #D0D0D0; }}
                QPushButton:disabled {{ background: {_DISABLED_BG};
                                        border: 1px solid {_DISABLED_BORDER};
                                        color: {_DISABLED_FG}; }}
            """)
        self._chip.setFixedHeight(px(34))
        self._chip.setStyleSheet(
            f"#rpmChip {{ background: {LIGHT}; border: 1px solid {BORDER}; "
            f"border-radius: {px(17)}px; }}"
            f"#rpmChip:disabled {{ background: {_DISABLED_BG}; "
            f"border: 1px solid {_DISABLED_BORDER}; }}")
        # lbl() pins its colour inline, so the chip's target RPM has to be dimmed
        # by hand — and from here, after lbl()'s own rescale styler has run.
        self._rpm_lbl.setStyleSheet(
            f"color: {_DISABLED_FG if self._door_blocked else TXT}; "
            f"background: transparent;")
        self._chip_l.setContentsMargins(px(12), 0, px(12), 0)
        self._br.setSpacing(px(16))
        d = px(64)
        self._power.setFixedSize(d, d)
        self._power.setIconSize(QSize(px(20), px(20)))
        self._power.setStyleSheet(f"""
            QToolButton {{
                background: {WHITE}; border: 1px solid {BORDER};
                border-radius: {d // 2}px; font-size: {fs(9)}px;
                font-weight: bold; color: {TXT}; padding-top: {px(4)}px;
            }}
            QToolButton:hover   {{ background: #F0F0F0; }}
            QToolButton:checked {{ background: #AFE26E; border: 1px solid #9BD05A; }}
            QToolButton:checked:hover {{ background: #A2D95E; }}
            /* After :checked so a door lock-out reads as locked even in the
               window where the spindle is still coasting down. */
            QToolButton:disabled {{
                background: {_DISABLED_BG}; border: 1px solid {_DISABLED_BORDER};
                color: {_DISABLED_FG};
            }}
            QToolButton:checked:disabled {{
                background: #CFE6A8; border: 1px solid {_DISABLED_BORDER};
                color: {_DISABLED_FG};
            }}
        """)
        self._refresh_power()

    # ── Door interlock ───────────────────────────────────────────────────────

    def set_door_blocked(self, blocked: bool) -> None:
        """Lock every control in the popup while the door is open.

        The dialog is modal, so the page's own gating can't reach it — the door
        opening under an open popup is exactly the case this covers. Only the
        close cross stays live; the measured-RPM readout keeps updating so the
        spindle coasting down is still visible."""
        blocked = bool(blocked)
        if blocked == self._door_blocked:
            return
        self._door_blocked = blocked
        self._apply_door_lock()
        self._apply_scale()          # the note row changes the card height

    def _apply_door_lock(self) -> None:
        for w in (*self._adj_btns, self._chip, self._power):
            w.setEnabled(not self._door_blocked)
        self._door_note.setVisible(self._door_blocked)

    # ── Spindle state / commands ─────────────────────────────────────────────

    def _adjust_rpm(self, delta: int) -> None:
        self._rpm = _clamp_rpm(self._rpm + delta)
        self._rpm_lbl.setText(f"{self._rpm} RPM")
        self._prefs.setValue("controls/spindleTargetRpm", self._rpm)
        if self._running:                       # apply the new target live
            self._conn.send_raw(f"M3 S{self._rpm}")

    def _refresh_power(self) -> None:
        """Sync the power button's icon / label / green fill to _running."""
        self._power.blockSignals(True)
        self._power.setChecked(self._running)
        self._power.blockSignals(False)
        self._power.setText("ON" if self._running else "OFF")
        self._power.setIcon(icons.icon("power", px(20), TXT))

    def _toggle_power(self, on: bool) -> None:
        self._running = bool(on)
        self._refresh_power()
        self._conn.send_raw(f"M3 S{self._rpm}" if on else "M5")

    def set_measured_rpm(self, rpm: int) -> None:
        """Live measured-RPM feed (wired to conn.spindle_rpm_changed). Also
        the source of truth for the running state — the machine can stop the
        spindle on its own (door pause, alarm, console M5) while the popup is
        open, and adjusting the target must not silently spin it back up."""
        self._measured_lbl.setText(f"{rpm} RPM")
        running = rpm > 0
        if running != self._running:
            self._running = running
            self._refresh_power()


# ── Page ──────────────────────────────────────────────────────────────────────

class ControlsPage(QWidget):
    #: A facing program is ready to go to the machine: (G-code text, file name).
    #: The page does not write or send it -- MainWindow stages it in the CAM
    #: export folder and puts it through the same upload queue a CAM export
    #: uses, so it gets the same collision prompt, progress row and cleanup.
    facing_job_ready = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._conn = None
        self._machine_status = ""       # base status, lowercase (gating)
        self._current_ja = 0.0          # latest A-axis job position (degrees)
        # Rotary mode: Bed back always returns to the machine Y the Rotary mode
        # setup parked the bed at (dialogs.ROTARY_PARK_KEY, persisted). Which
        # of Bed forward / Bed back the button offers is read off the live
        # machine Y against that park, so a restart cannot lose it.
        self._pos_known = False         # a position report has arrived
        self._current_my: float | None = None
        self._bed_back_shown: bool | None = None
        self._prefs = QSettings("InMachines", "MiniCNC2")
        # The old "where Bed forward left from" record -- the one a restart
        # could leave at the placeholder 0 -- is not used any more.
        self._prefs.remove("controls/rotaryBedParkMy")
        self._spindle_running = False   # measured RPM > 0
        self._hold_jogs: list[_HoldJog] = []
        self.setStyleSheet(TRANSPARENT)
        root = QVBoxLayout(self)
        self._root = root

        # Top action buttons ──────────────────────────────────────────────────
        # Icon-left / text-right cards that stretch to fill the full content
        # width (jog area + step-size area). Stretch factors set the relative
        # widths; the tool-length card is the widest.
        top_row = QHBoxLayout()
        self._top_row = top_row

        self._action_btns: dict[str, QPushButton] = {}
        self._action_btns["homing"]   = TopActionButton("home",   "Homing")
        self._action_btns["unlock"]   = TopActionButton("unlock", "Unlock")
        self._action_btns["softreset"] = TopActionButton("reset", "Reset")
        self._tool_length = ToolLengthButton()
        # Not checkable: the spindle-running state shows in the popup and the
        # status panel, so the top button must not turn blue when the spindle runs.
        self._action_btns["spindle"] = TopActionButton("spindle", "Spindle\ncontrol")
        self._action_btns["rotary"]  = TopActionButton("rotary",  "Rotary\nmode",     checkable=True)

        top_row.addWidget(self._action_btns["homing"],    2)
        top_row.addWidget(self._action_btns["unlock"],    2)
        top_row.addWidget(self._action_btns["softreset"], 2)
        top_row.addWidget(self._tool_length,              4)
        top_row.addWidget(self._action_btns["spindle"],   2)
        top_row.addWidget(self._action_btns["rotary"],    2)
        root.addLayout(top_row)

        # Main content row ────────────────────────────────────────────────────
        main_row = QHBoxLayout()
        self._main_row = main_row

        # Left: jog panel
        self._jog_panel = QFrame()
        self._jog_panel.setObjectName("jogPanel")
        jog_lay = QVBoxLayout(self._jog_panel)
        self._jog_lay = jog_lay

        jog_inner = QHBoxLayout()
        self._jog_inner = jog_inner
        self._jog_pad = JogPad()
        self._z_pad   = ZPad()
        self._jog_indent = QSpacerItem(px(56), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        jog_inner.addItem(self._jog_indent)   # indent the jog block to the right
        jog_inner.addWidget(self._jog_pad, 0, Qt.AlignTop)
        jog_inner.addWidget(self._z_pad,   0, Qt.AlignTop)
        jog_inner.addStretch()
        jog_lay.addLayout(jog_inner)
        jog_lay.addStretch(1)          # push bottom rows to foot of panel

        # ── Move-to / sensor-detection area ────────────────────────────────
        # Mirrors the design: each section header sits ABOVE its button group
        # ("move to" over go-to-XY₀; "sensor detection" over detect-center),
        # the two groups share one button row, and dry-run / height-map stack
        # at the right edge (dry-run level with the headers, height-map level
        # with the buttons). Previously the labels were inline to the left of
        # each row, which read as the label sitting on top of the first button.
        self._bottom_btns: dict[str, IconButton] = {}
        self._head_icons: list[tuple] = []

        def _head(label_icon: str, label_text: str) -> QHBoxLayout:
            h = QHBoxLayout()
            h.setContentsMargins(px(2), 0, 0, 0)
            h.setSpacing(px(4))
            il = QLabel()
            il.setStyleSheet(TRANSPARENT)
            self._head_icons.append((il, label_icon))
            h.addWidget(il)
            h.addWidget(lbl(label_text, sz=17, bold=True, color=TXT))
            h.addStretch()
            return h

        def _btn(key: str, icon_name, text: str, checkable: bool = False) -> IconButton:
            b = IconButton(icon_name, text, checkable=checkable)
            self._bottom_btns[key] = b
            return b

        move_grid = QGridLayout()
        self._move_grid = move_grid
        move_grid.setContentsMargins(0, 0, 0, 0)

        # Row 0: section headers, bottom-aligned so they hug the buttons below.
        move_grid.addLayout(_head("move-to", "Move to"),
                            0, 0, 1, 3, Qt.AlignLeft | Qt.AlignBottom)
        move_grid.addLayout(_head("sensor", "Sensor detection"),
                            0, 4, 1, 3, Qt.AlignLeft | Qt.AlignBottom)

        # Row 1: the two button groups (cols 0-2 and 4-6; col 3 is the gap).
        move_grid.addWidget(_btn("goto_xy0",   "target", "Go to XY₀"),        1, 0)
        move_grid.addWidget(_btn("bed_front",  "bed",    "Bed to front"),     1, 1)
        move_grid.addWidget(_btn("type_coord", None,     "Type coordinates"), 1, 2)
        move_grid.addWidget(_btn("detect_z0",     "detect-z0",     "Detect Z₀"),      1, 4)
        move_grid.setColumnStretch(7, 1)         # push the right pair to the edge

        jog_lay.addLayout(move_grid)

        # Dry-run toggle: top-right of the control section, to the right of the Z
        # move-up button. Inserted before the trailing stretch of the jog row
        # (items: indent, jog pad, Z pad, ►dry-run◄, stretch), top-aligned so it
        # sits level with the Z ▲ button.
        # Facing goes under it, which puts the buttons in this corner into a
        # block with Facing below the dry-run toggle.
        dry_col = QVBoxLayout()
        self._dry_col = dry_col
        dry_col.setContentsMargins(0, 0, 0, 0)
        dry_col.addWidget(_btn("dry_run", "dry-run", "Dry run", checkable=True))
        dry_col.addWidget(_btn("facing", "facing", "Facing"))
        dry_col.addStretch(1)
        jog_inner.insertLayout(3, dry_col)
        # Frame: traces the opened file's bounding box so the operator can
        # preview the area the job occupies on the stock. Sits to the right of
        # the dry-run toggle.
        frame_col = QVBoxLayout()
        self._frame_col = frame_col
        frame_col.setContentsMargins(0, 0, 0, 0)
        frame_col.addWidget(_btn("frame", "frame", "Frame"))
        frame_col.addStretch(1)
        jog_inner.insertLayout(4, frame_col)

        main_row.addWidget(self._jog_panel, stretch=3)

        # Right: step/speed settings panel
        self._settings = QFrame()
        self._settings.setObjectName("settingsPanel")
        sl = QVBoxLayout(self._settings)
        self._sl = sl

        xy_hdr = QHBoxLayout()
        xy_hdr.addWidget(lbl("XY", sz=14, bold=True))
        xy_hdr.addStretch()
        self._link_b = btn("", radius=8, h=28, min_w=32)
        self._link_b.setToolTip("Link X and Y step sizes")
        self._link_b.setCheckable(True)
        self._link_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._syncing_xy = False
        self._link_b.toggled.connect(self._on_link_toggled)
        xy_hdr.addWidget(self._link_b)
        sl.addLayout(xy_hdr)

        sl.addWidget(_section_header("Step Size (mm)"))
        self._step_x = StepSizeRow("X", default=3, cmin=0.1, cmax=100.0)   # "10" stays preselected
        self._step_y = StepSizeRow("Y", default=3, cmin=0.1, cmax=100.0)
        # A-axis step row (degrees) — replaces the Y row in place while a rotary
        # axis is connected (mobile parity); hidden otherwise.
        self._step_a = StepSizeRow("A", default=1, values=_A_STEP_VALUES,
                                   cmin=0.1, cmax=360.0)
        self._step_a.setVisible(False)
        sl.addWidget(self._step_x)
        sl.addWidget(self._step_y)
        sl.addWidget(self._step_a)
        sl.addWidget(_section_header("Speed (mm/s)"))
        self._speed_xy = SpeedRow(default=1)
        sl.addWidget(self._speed_xy)
        sl.addWidget(hline())
        sl.addWidget(lbl("Z", sz=14, bold=True))
        sl.addWidget(_section_header("Step Size (mm)"))
        # 7 segments (0.01 … 50): a smaller per-segment minimum keeps this pill's
        # footprint within the X/Y 6-segment rows so the panel width is unchanged.
        # default=5 → 10 mm stays preselected after prepending the two fine steps.
        self._step_z = StepSizeRow("Z", default=5, values=_Z_STEP_VALUES,
                                   cmin=0.01, cmax=50.0, min_seg_w=30)
        sl.addWidget(self._step_z)
        sl.addWidget(_section_header("Speed (mm/s)"))
        self._speed_z = SpeedRow(default=1)
        sl.addWidget(self._speed_z)
        sl.addStretch()

        main_row.addWidget(self._settings, stretch=2)
        root.addLayout(main_row)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._root.setContentsMargins(px(12), px(12), px(12), px(12))
        self._root.setSpacing(px(10))
        self._top_row.setSpacing(px(10))
        self._main_row.setSpacing(px(10))

        self._jog_panel.setStyleSheet(
            f"#jogPanel {{ background: {PANEL}; border-radius: {px(12)}px; "
            f"border: 1px solid {BORDER}; }}"
        )
        self._jog_lay.setContentsMargins(px(16), px(16), px(16), px(16))
        self._jog_lay.setSpacing(px(12))
        self._jog_inner.setSpacing(px(28))
        self._dry_col.setSpacing(px(6))       # as between the move-to buttons
        self._frame_col.setSpacing(px(6))     # as between the move-to buttons
        self._jog_indent.changeSize(px(56), 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._jog_inner.invalidate()

        self._move_grid.setHorizontalSpacing(px(6))
        self._move_grid.setVerticalSpacing(px(6))
        self._move_grid.setColumnMinimumWidth(3, px(16))   # gap between the two groups
        for il, name in self._head_icons:
            il.setPixmap(icons.pixmap(name, px(22), TXT))

        # Gray panel (matching the jog panel beside it) so the white step /
        # speed pills read as distinct capsules, as in the design. On a white
        # card the white pills blended in, separated only by their thin borders.
        self._settings.setStyleSheet(
            f"#settingsPanel {{ background: {PANEL}; border-radius: {px(12)}px; "
            f"border: 1px solid {BORDER}; }}"
        )
        self._sl.setContentsMargins(px(16), px(16), px(16), px(16))
        self._sl.setSpacing(px(10))
        self._link_b.setFixedSize(px(34), px(34))
        self._link_b.setIconSize(QSize(px(16), px(16)))
        self._refresh_link_look()          # icon + on/off styling (scale-aware)

    # ── Real-machine wiring ───────────────────────────────────────────────────

    def wire(self, conn) -> None:
        """Connect buttons to real machine commands via *conn*."""
        self._conn = conn
        self._action_btns["homing"].clicked.connect(self._on_homing)
        self._action_btns["unlock"].clicked.connect(lambda: conn.cmd("grblUnlock"))
        self._action_btns["softreset"].clicked.connect(lambda: conn.cmd("grblSoftReset"))

        # A quick tap jogs one step; press-and-hold switches to the firmware's
        # continuous jog / jogCancel commands (README 5.31.1 / 5.31.2) — see
        # _HoldJog below. The hold path is untouched by any of this.
        #
        # The step is sent as a DELTA (`relative: true`, README 5.23–5.26 and
        # 5.31), not as "the position we last heard + one step", which is what
        # it used to be and why a step sometimes arrived short, at an odd size.
        #
        # The position it added to is whatever the status poll last reported,
        # and the poll is behind the machine — the app asks every 500 ms and
        # the firmware's own reading can be 200 ms old again. Tap a second step
        # before that catches up and the target is worked out from where the
        # head WAS, so the move is short by exactly what the first step had
        # left to travel: a 1 mm step arriving as 0.37 mm. Nothing in the app
        # can close that window, because only the machine knows where it is.
        #
        # A delta has no window to be stale in. The firmware hands GRBL a G91
        # move, GRBL adds it to where the machine actually is, and successive
        # taps queue in the planner — three taps of 1 mm are 3 mm, whatever the
        # poll happened to be reporting. It also fixes a quieter one: the
        # diagonal used to send the cached Z as an ABSOLUTE Z target, so a
        # stale reading moved Z on an XY tap. Its Z delta is now zero.
        def _move(command: str, **delta) -> None:
            feed = delta.pop("feed")
            conn.cmd(command, feedRate=feed, relative=True, **delta)

        def _jog_x(sign: int) -> None:
            _move("moveX", x=round(self._step_x.value() * sign, 4),
                  feed=self._speed_xy.feed_mmmin())

        def _jog_y(sign: int) -> None:
            _move("moveY", y=round(self._step_y.value() * sign, 4),
                  feed=self._speed_xy.feed_mmmin())

        def _jog_z(sign: int) -> None:
            _move("moveZ", z=round(self._step_z.value() * sign, 4),
                  feed=self._speed_z.feed_mmmin())

        def _jog_diag(sx: int, sy: int) -> None:
            _move("moveXYZ", x=round(self._step_x.value() * sx, 4),
                  y=round(self._step_y.value() * sy, 4), z=0.0,
                  feed=self._speed_xy.feed_mmmin())

        # Each pad button dispatches tap vs. hold: taps keep the discrete step
        # moves above, holds stream conn.jog() keep-alives until release.
        # The Y direction is intentionally inverted on the jog pad (user
        # request): the "up" buttons jog −Y and the "down" buttons +Y, for
        # both tap steps and continuous holds. Flip _Y to +1 to restore
        # machine-direction Y jogging.
        _Y = -1
        xy_feed = self._speed_xy.feed_mmmin
        z_feed = self._speed_z.feed_mmmin
        jp = self._jog_pad
        zp = self._z_pad

        def _rotary() -> bool:
            return bool(conn.flags().get("rotaryAxisConnected", False))

        # In rotary mode the vertical buttons drive the A axis (absolute moveA,
        # tap-only — no firmware jog), otherwise they jog Y as before.
        self._hold_jogs = [
            _HoldJog(jp._btn_n, conn, {"y": +_Y}, xy_feed,
                     lambda: self._move_a(+1) if _rotary() else _jog_y(+_Y),
                     hold_enabled_fn=lambda: not _rotary()),
            _HoldJog(jp._btn_s, conn, {"y": -_Y}, xy_feed,
                     lambda: self._move_a(-1) if _rotary() else _jog_y(-_Y),
                     hold_enabled_fn=lambda: not _rotary()),
            _HoldJog(jp._btn_e,  conn, {"x": +1}, xy_feed, lambda: _jog_x(+1)),
            _HoldJog(jp._btn_w,  conn, {"x": -1}, xy_feed, lambda: _jog_x(-1)),
            # Diagonal Y matches the cardinal N/S: upper 45° arrows send the same
            # Y as the up (N) arrow, lower ones the same as the down (S) arrow.
            _HoldJog(jp._btn_ne, conn, {"x": +1, "y": +_Y}, xy_feed,
                     lambda: _jog_diag(+1, +_Y)),
            _HoldJog(jp._btn_nw, conn, {"x": -1, "y": +_Y}, xy_feed,
                     lambda: _jog_diag(-1, +_Y)),
            _HoldJog(jp._btn_se, conn, {"x": +1, "y": -_Y}, xy_feed,
                     lambda: _jog_diag(+1, -_Y)),
            _HoldJog(jp._btn_sw, conn, {"x": -1, "y": -_Y}, xy_feed,
                     lambda: _jog_diag(-1, -_Y)),
            _HoldJog(zp._btn_up,   conn, {"z": +1}, z_feed, lambda: _jog_z(+1)),
            _HoldJog(zp._btn_down, conn, {"z": -1}, z_feed, lambda: _jog_z(-1)),
        ]
        zp._btn_ztop.clicked.connect(lambda: conn.cmd("grblHomingZ"))

        # ── A-axis (rotary) row ────────────────────────────────────────────
        conn.position_changed.connect(self._on_position_changed)
        self._set_rotary_ui(conn.flags().get("rotaryAxisConnected", False))

        # ── Toolbar extras ─────────────────────────────────────────────────
        self._tool_length._detect_btn.clicked.connect(self._on_tool_length_detect)
        conn.tool_length_changed.connect(
            lambda v: self._tool_length.set_value(f"{v:.1f} mm"))
        self._action_btns["spindle"].clicked.connect(self._on_spindle)
        conn.spindle_rpm_changed.connect(self._on_spindle_rpm)
        self._action_btns["rotary"].clicked.connect(self._on_rotary)

        # ── Move-to / sensor-detection buttons ─────────────────────────────
        b = self._bottom_btns
        b["goto_xy0"].clicked.connect(self._on_goto_xy0)
        b["bed_front"].clicked.connect(self._on_bed_front)
        b["type_coord"].clicked.connect(self._on_type_coordinates)
        b["dry_run"].toggled.connect(self._on_dry_run_toggle)
        b["frame"].clicked.connect(self._on_frame)
        b["facing"].clicked.connect(self._on_facing)
        b["detect_z0"].clicked.connect(self._on_detect_z0)

        # Reflect machine flags back into the toggles (dry run)
        conn.machine_flags_changed.connect(self._on_machine_flags)

        # Info boxes follow the selected step sizes / speed. Step rows notify
        # through value_changed so a custom "…" step refreshes them too.
        for row in (self._step_x, self._step_y, self._step_a, self._step_z):
            row.value_changed.connect(self._refresh_info_boxes)
        for grp in (self._speed_xy._group, self._speed_z._group):
            grp.idClicked.connect(lambda _i: self._refresh_info_boxes())
        # X/Y step link: while the link button is on, changing one axis mirrors
        # the other so they stay in sync.
        self._step_x.value_changed.connect(lambda: self._sync_link("x"))
        self._step_y.value_changed.connect(lambda: self._sync_link("y"))
        self._refresh_info_boxes()

        # ── Run-state gating (Android button-mask parity, App.tsx ~2799) ──
        conn.machine_status_changed.connect(self._on_status_for_gates)
        conn.serial_state_changed.connect(lambda *_: self._sync_gates())
        conn.ws_connected_changed.connect(lambda *_: self._sync_gates())
        conn.opened_file_changed.connect(lambda *_: self._sync_gates())
        self._sync_gates()

    # ── Dialog launchers ──────────────────────────────────────────────────────

    def _on_homing(self) -> None:
        from ui.pages.dialogs import HomingOptionsDialog
        rotary = self._conn.flags().get("rotaryAxisConnected", False)
        HomingOptionsDialog(self._conn, rotary=rotary, parent=self).exec()

    def _on_tool_length_detect(self) -> None:
        from ui.pages.dialogs import ToolLengthFlow
        ToolLengthFlow(self._conn, parent=self).exec()

    def _on_detect_z0(self) -> None:
        from ui.pages.dialogs import ProbeZ0Flow
        ProbeZ0Flow(self._conn, parent=self).exec()

    def _on_spindle(self) -> None:
        """Open the spindle control popup; the button's checked state follows
        the measured RPM, not the click (same pattern as the rotary button)."""
        conn = self._conn
        panel = SpindlePanel(conn, running=self._spindle_running,
                             door_blocked=conn.door_blocked(), parent=self)
        conn.spindle_rpm_changed.connect(panel.set_measured_rpm)
        # The popup is modal, so _sync_gates can't gate it — feed it the live
        # door state instead, or a door opened under an open popup would leave
        # the controls live.
        def _on_door(*_) -> None:
            panel.set_door_blocked(conn.door_blocked())

        conn.machine_flags_changed.connect(_on_door)
        conn.machine_status_changed.connect(_on_door)
        # Read the live spindle status now (and keep reading via the signal
        # while the popup is open) so ON/OFF reflects the machine, not a stale
        # cached value — the reply refreshes the panel through the signal above.
        conn.get("currentSpindleRPM")
        panel.exec()
        for sig, slot in ((conn.spindle_rpm_changed, panel.set_measured_rpm),
                          (conn.machine_flags_changed, _on_door),
                          (conn.machine_status_changed, _on_door)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._sync_spindle_btn()

    def _on_spindle_rpm(self, rpm: int) -> None:
        self._spindle_running = rpm > 0
        self._sync_spindle_btn()

    def _sync_spindle_btn(self) -> None:
        b = self._action_btns["spindle"]
        b.blockSignals(True)
        b.setChecked(self._spindle_running)
        b.blockSignals(False)

    def _on_rotary(self) -> None:
        from ui.pages.dialogs import RotaryFlow
        RotaryFlow(self._conn, parent=self).exec()
        self._action_btns["rotary"].setChecked(
            self._conn.flags().get("rotaryAxisConnected", False))

    # ── State feedback ────────────────────────────────────────────────────────

    _FRAME_START_TIMEOUT_MS = 5000

    #: How long a Frame may take to visibly start before it is assumed refused,
    #: and how recent an ALARM:N line must be to be blamed on this frame (s).
    _FRAME_ALARM_WINDOW_S = 5.0

    def _on_frame(self) -> None:
        conn = self._conn
        if conn is None:
            return
        if conn.flags().get("rotaryAxisConnected", False):
            self._frame_stock()
            return
        if getattr(self, "_frame_checking", False):
            return
        self._frame_soft_limit_check()

    # ── Frame: soft limits first ─────────────────────────────────────────────
    #
    # The firmware's frameJob traces the job's X/Y outline with real moves, and
    # an outline that pokes past the travel was only found out when the
    # controller stopped at the soft limit mid-trace (see _frame_watch). The
    # job's X/Y extent and the work zero are known before that, so they are
    # checked first, the way the Start check does (job_fit), and a frame that
    # would pass the soft limit is not sent. Z is not checked: frameJob picks
    # its own height. If the extent or the travel cannot be read in time, the
    # frame goes ahead as before and _frame_watch still explains an alarm.
    def _frame_soft_limit_check(self) -> None:
        conn = self._conn
        import time
        from ui import job_fit
        self._frame_checking = True
        state = {"reply": None}

        def on_bounds(p) -> None:
            state["reply"] = dict(p)

        conn.opened_file_bounds_received.connect(on_bounds)
        conn.get("getOpenedFileBounds")
        if job_fit.machine_ranges(conn.grbl_settings()) is None:
            conn.request_grbl_settings()
        t0 = time.monotonic()

        def poll() -> None:
            have = (state["reply"] is not None and
                    job_fit.machine_ranges(conn.grbl_settings()) is not None)
            if not have and time.monotonic() - t0 < self._FS_BOUNDS_WAIT_S:
                QTimer.singleShot(100, poll)
                return
            try:
                conn.opened_file_bounds_received.disconnect(on_bounds)
            except (RuntimeError, TypeError):
                pass
            self._frame_checking = False
            name = conn.opened_file_name()
            ext = job_fit.extents_from_reply(state["reply"])
            ext.update(job_fit.extents_from_file(
                conn.cached_file_path(name) if name else None))
            ext = {a: ext[a] for a in ("X", "Y") if a in ext}
            ranges = job_fit.machine_ranges(conn.grbl_settings())
            if ext and ranges is not None and self._pos_known:
                problems = job_fit.check(ext, conn.current_position(), ranges)
                if problems:
                    conn.motion_error.emit(
                        "Frame Would Pass the Soft Limit",
                        "From where the work zero is set now, the job's "
                        "outline goes past the machine's travel, so the frame "
                        "would stop at the soft limit. Nothing was moved:\n\n"
                        + "\n\n".join(problems))
                    return
            self._frame_send()

        QTimer.singleShot(100, poll)

    def _frame_send(self) -> None:
        conn = self._conn
        if conn is None or not (conn.is_serial_connected or conn.is_ws_active):
            return
        # Watch the frame for an alarm -- see _frame_watch.
        self._framing = True
        self._frame_moved = False
        QTimer.singleShot(self._FRAME_START_TIMEOUT_MS, self._frame_watch_timeout)
        conn.cmd("frameJob")

    # ── Frame stopped by an alarm ────────────────────────────────────────────
    #
    # Framing traces the job outline with real moves, so an outline that pokes
    # past the machine's travel is stopped by the controller mid-trace and the
    # machine drops into Alarm. When the controller names the cause as a soft
    # limit (ALARM:2) the connection already shows "Soft Limit Triggered"
    # (connection._note_grbl_limit). Any OTHER alarm during a frame used to
    # leave the machine silently locked; this says what happened and what to
    # check. The frame is over once the machine is back at Idle after moving.
    def _frame_watch(self) -> None:
        if not getattr(self, "_framing", False) or self._conn is None:
            return
        st = self._machine_status
        if st in ("run", "jog", "home", "hold"):
            self._frame_moved = True
            return
        if st == "alarm":
            self._framing = False
            import time
            last = self._conn.last_alarm
            recent = last is not None and time.monotonic() - last[1] < self._FRAME_ALARM_WINDOW_S
            if recent and last[0] == 2:
                return          # soft limit: already announced by the connection
            code = f" (ALARM:{last[0]})" if recent else ""
            self._conn.motion_error.emit(
                "Frame Stopped",
                f"The machine went into alarm while framing{code}. This usually "
                "means the frame would cross the machine's travel limits (soft "
                "limit). Check the work zero and that the job fits inside the "
                "machine, then unlock it before continuing.")
        elif st == "idle" and self._frame_moved:
            self._framing = False

    def _frame_watch_timeout(self) -> None:
        # never seen to start: refused (a JSON refusal has its own notice) or
        # too short to catch -- either way, stop watching
        if getattr(self, "_framing", False) and not getattr(self, "_frame_moved", False):
            self._framing = False

    # ── Stock length (rotary mode) ───────────────────────────────────────────
    #
    # Frame traces the job's X/Y outline, and the firmware does it itself
    # (frameJob) -- with Y moves, which it refuses while the rotary is
    # connected. On a bar the question Frame answers is simpler anyway: which
    # stretch of the bar does the job cut, and how long is that? So in rotary
    # mode the button is Stock length: Z goes up to the top of its travel,
    # then X runs to the job's left end (X min), on to its right end (X max),
    # and back to where it was. Y and A do not move, and Z stays up.
    #
    # Z at the top, not at the job's own safe height: tried on 2026-11-18 and
    # the tool hovering 3 mm over the bar was hard to see along it; from the
    # top the run is easy to follow, and it is clear of anything on the bar.
    # (Until then this was "Frame stock".)
    #
    # The moves go out as GRBL ``$J=`` jogs, which the firmware passes through
    # with the rotary connected (as Bed forward does). X is in work coordinates
    # (``G90``, the active work offset), which is what the job's bounds are in;
    # Z goes to machine Z (``G53``). The job's X extent comes from the same two
    # places the Start check reads (job_fit): the firmware's
    # getOpenedFileBounds reply and the local copy of the file. It is checked
    # against the travel (the soft limits) first, so a move that cannot fit
    # says so instead of stopping at the limit.
    #
    # One jog at a time: a raw line is a priority send that clears whatever is
    # still waiting in the port's output buffer, so lines fired back to back
    # can lose one. Each is sent once the machine is seen to have finished the
    # last -- Jog, then Idle again -- or, for one too short to catch, after
    # _FS_QUIET_S of Idle. An alarm, a lost connection or a step that never
    _FS_BOUNDS_WAIT_S = 2.5
    _FS_POLL_MS = 100
    _FS_QUIET_S = 1.5
    _FS_STEP_TIMEOUT_S = 180.0

    def _frame_stock(self) -> None:
        conn = self._conn
        if conn is None or getattr(self, "_fs", None) is not None:
            return
        if not self._pos_known:
            conn.motion_error.emit(
                "Position Not Known Yet",
                "The machine has not reported where it is yet. "
                "Try again in a moment.")
            return
        import time
        from ui import job_fit
        self._fs = {"phase": "reading"}
        self._sync_gates()
        state = {"reply": None}

        def on_bounds(p) -> None:
            state["reply"] = dict(p)

        conn.opened_file_bounds_received.connect(on_bounds)
        conn.get("getOpenedFileBounds")
        if job_fit.machine_ranges(conn.grbl_settings()) is None:
            conn.request_grbl_settings()
        t0 = time.monotonic()

        def poll() -> None:
            have = (state["reply"] is not None and
                    job_fit.machine_ranges(conn.grbl_settings()) is not None)
            if not have and time.monotonic() - t0 < self._FS_BOUNDS_WAIT_S:
                QTimer.singleShot(100, poll)
                return
            try:
                conn.opened_file_bounds_received.disconnect(on_bounds)
            except (RuntimeError, TypeError):
                pass
            self._frame_stock_plan(state["reply"])

        QTimer.singleShot(100, poll)

    def _frame_stock_fail(self, title: str, text: str) -> None:
        self._fs = None
        self._sync_gates()
        if self._conn is not None:
            self._conn.motion_error.emit(title, text)

    def _frame_stock_plan(self, reply) -> None:
        conn = self._conn
        if conn is None:
            self._fs = None
            return
        from ui import job_fit
        name = conn.opened_file_name()
        ext = job_fit.extents_from_reply(reply)
        ext.update(job_fit.extents_from_file(conn.cached_file_path(name) if name else None))
        if "X" not in ext:
            self._frame_stock_fail(
                "Stock Length",
                "The opened job's extent along X could not be read, so there "
                "is nothing to measure. Open the job again and retry.")
            return
        ranges = job_fit.machine_ranges(conn.grbl_settings())
        if ranges is None:
            self._frame_stock_fail(
                "Machine Travel Not Read Yet",
                "The machine's travel ($130-$132) has not been read yet, so the "
                "soft limits are not known. Try again in a moment.")
            return
        pos = conn.current_position()
        xlo, xhi = ext["X"]
        problems = job_fit.check({"X": (xlo, xhi)}, pos, ranges)
        if problems:
            self._frame_stock_fail(
                "Stock Length Would Pass the Soft Limit",
                "From where the work zero is set now, the move would go past "
                "the machine's travel and stop at the soft limit, so nothing "
                "was moved:\n\n" + "\n\n".join(problems))
            return
        x0 = float(pos.get("jx", 0.0))
        feed = max(100, int(self._speed_xy.feed_mmmin()))
        zfeed = max(100, int(self._speed_z.feed_mmmin()))
        # Z to the top of its travel first (machine Z, G53), then the bar's
        # stretch from X min to X max, then back to where X was; Z stays up
        ztop = ranges["Z"][1] - 0.5
        steps = []
        if float(pos.get("mz", 0.0)) < ztop - 0.01:
            steps.append(f"$J=G53 G90 Z{ztop:.3f} F{zfeed}")
        steps += [f"$J=G90 X{xlo:.3f} F{feed}",
                  f"$J=G90 X{xhi:.3f} F{feed}",
                  f"$J=G90 X{x0:.3f} F{feed}"]
        self._fs = {"phase": "moving", "steps": steps, "i": 0,
                    "sent": 0.0, "started": False}
        self._sync_gates()
        QTimer.singleShot(0, self._frame_stock_next)

    # ── Go to X0 (rotary mode) ───────────────────────────────────────────────
    #
    # Go to XY0 is the firmware's returnJobXY0Macro, which moves Y -- refused
    # while the rotary is connected, so the button used to be greyed out in
    # rotary mode. There it is Go to X0 instead: Z up to the top of its travel
    # (clear of the bar and the chuck, whatever height the tool was left at),
    # then X to the work zero. Y and A do not move, and Z stays up. X0 is
    # checked against the travel (soft limits) first. Same raw-jog runner as
    # Stock length (_frame_stock_next).
    def _on_goto_xy0(self) -> None:
        conn = self._conn
        if conn is None:
            return
        if conn.flags().get("rotaryAxisConnected", False):
            self._goto_x0()
        else:
            conn.cmd("returnJobXY0Macro")

    def _goto_x0(self) -> None:
        conn = self._conn
        if conn is None or getattr(self, "_fs", None) is not None:
            return
        if not self._pos_known:
            conn.motion_error.emit(
                "Position Not Known Yet",
                "The machine has not reported where it is yet. "
                "Try again in a moment.")
            return
        from ui import job_fit
        ranges = job_fit.machine_ranges(conn.grbl_settings())
        if ranges is None:
            conn.request_grbl_settings()
            conn.motion_error.emit(
                "Machine Travel Not Read Yet",
                "The machine's travel ($130-$132) has not been read yet, so the "
                "soft limits and the top of Z are not known. Try again in a "
                "moment.")
            return
        pos = conn.current_position()
        problems = job_fit.check({"X": (0.0, 0.0)}, pos, ranges)
        if problems:
            conn.motion_error.emit(
                "Go to X₀ Would Pass the Soft Limit",
                "The X work zero is outside the machine's travel, so the move "
                "would stop at the soft limit. Nothing was moved:\n\n"
                + "\n\n".join(problems))
            return
        feed = max(100, int(self._speed_xy.feed_mmmin()))
        zfeed = max(100, int(self._speed_z.feed_mmmin()))
        ztop = ranges["Z"][1] - 0.5
        steps = []
        if float(pos.get("mz", 0.0)) < ztop - 0.01:
            steps.append(f"$J=G53 G90 Z{ztop:.3f} F{zfeed}")
        steps.append(f"$J=G90 X0.000 F{feed}")
        self._fs = {"phase": "moving", "steps": steps, "i": 0, "sent": 0.0,
                    "started": False, "title": "Go to X₀"}
        self._sync_gates()
        QTimer.singleShot(0, self._frame_stock_next)

    def _frame_stock_next(self) -> None:
        import time
        fs = getattr(self, "_fs", None)
        conn = self._conn
        if fs is None or conn is None:
            return
        if fs["i"] >= len(fs["steps"]):
            self._frame_stock_end()
            return
        line = fs["steps"][fs["i"]]
        fs["i"] += 1
        fs["sent"] = time.monotonic()
        fs["started"] = False
        conn.send_raw(line)
        QTimer.singleShot(self._FS_POLL_MS, self._frame_stock_poll)

    def _frame_stock_poll(self) -> None:
        import time
        fs = getattr(self, "_fs", None)
        conn = self._conn
        if fs is None or conn is None:
            return
        if not (conn.is_serial_connected or conn.is_ws_active):
            self._frame_stock_end()
            return
        st = self._machine_status
        if st == "alarm":
            self._frame_stock_end()
            what = fs.get("title", "Stock Length")
            conn.motion_error.emit(
                f"{what} Stopped",
                f"The machine went into alarm during {what}. Check the work "
                "zero and that the move fits inside the machine, then unlock "
                "it before continuing.")
            return
        if st in ("jog", "run"):
            fs["started"] = True
        waited = time.monotonic() - fs["sent"]
        if st == "idle" and (fs["started"] or waited >= self._FS_QUIET_S):
            QTimer.singleShot(150, self._frame_stock_next)
            return
        if waited > self._FS_STEP_TIMEOUT_S:
            self._frame_stock_end()
            return
        QTimer.singleShot(self._FS_POLL_MS, self._frame_stock_poll)

    def _frame_stock_end(self) -> None:
        self._fs = None
        self._sync_gates()

    def _on_facing(self) -> None:
        """Open the facing setup and pass the finished program upward.

        Nothing is sent from here: the dialog hands over the G-code text and a
        name, and the signal carries both to MainWindow, which owns the staging
        folder and the transfer queue.
        """
        from ui.pages.facing_dialog import FacingDialog
        dlg = FacingDialog(self._conn, parent=self)
        dlg.job_ready.connect(self.facing_job_ready)
        dlg.exec()

    def _on_dry_run_toggle(self, on: bool) -> None:
        if on != self._conn.flags().get("dryRunEnabled", False):
            self._conn.set("setDryRun", dryRunEnabled=bool(on))

    def _on_machine_flags(self, flags: dict) -> None:
        if "dryRunEnabled" in flags:
            b = self._bottom_btns["dry_run"]
            b.blockSignals(True)
            b.setChecked(flags["dryRunEnabled"])
            b.blockSignals(False)
        if "rotaryAxisConnected" in flags:
            b = self._action_btns["rotary"]
            b.blockSignals(True)
            b.setChecked(flags["rotaryAxisConnected"])
            b.blockSignals(False)
            self._set_rotary_ui(bool(flags["rotaryAxisConnected"]))
        self._sync_gates()

    def _set_rotary_ui(self, rotary: bool) -> None:
        """Reshape the jog UI for rotary parity with the mobile app: the jog
        pad's vertical buttons become the A axis and its diagonals disappear,
        and the settings panel swaps the Y step row for the A (degrees) row."""
        rotary = bool(rotary)
        self._jog_pad.set_rotary(rotary)
        self._step_y.setVisible(not rotary)
        self._step_a.setVisible(rotary)
        # Big jog steps make no sense with a bar in the chuck: X keeps up to
        # 10 mm, Z up to 5 mm; the rest is greyed until rotary comes off.
        self._step_x.set_max(10.0 if rotary else None)
        self._step_z.set_max(5.0 if rotary else None)
        self._refresh_info_boxes()
        # The park is NOT cleared when rotary reads off: every launch starts
        # with no flags (rotary off) until the machine reports, and clearing
        # here lost the park on each restart.
        self._bed_back_shown = None
        self._refresh_bed_button(rotary)
        self._refresh_frame_button(rotary)
        g = self._bottom_btns.get("goto_xy0")
        if g is not None:
            g.set_text("Go to X₀" if rotary else "Go to XY₀")
            g.setToolTip(_tip(
                "Z goes up to the top of its travel.",
                "Then X moves to the work zero.",
                "Y and A do not move.",
                "The move is checked against the soft limits first.")
                if rotary else "")

    def _refresh_frame_button(self, rotary: bool) -> None:
        b = self._bottom_btns.get("frame")
        if b is None:
            return
        if rotary:
            b.set_text("Stock length")
            b.set_icon("stock-length")
            b.setToolTip(_tip(
                "Z goes up to the top of its travel.",
                "Then X runs from the opened job's X minimum to its X "
                "maximum and back, showing the stretch of the bar the job "
                "will cut.",
                "Y and A do not move.",
                "The move is checked against the soft limits first."))
        else:
            b.set_text("Frame")
            b.set_icon("frame")
            b.setToolTip("")

    #: How far (mm) the bed may sit from the rotary park and still count as
    #: parked -- beyond it the button offers Bed back.
    _BED_AT_PARK_MM = 0.5

    def _rotary_park_my(self) -> float | None:
        """Machine Y Rotary mode parked the bed at, or None if never recorded."""
        from ui.pages.dialogs import ROTARY_PARK_KEY
        v = self._prefs.value(ROTARY_PARK_KEY, None)
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None

    def _bed_away_from_park(self) -> bool:
        park = self._rotary_park_my()
        if park is None or not self._pos_known or self._current_my is None:
            return False
        return abs(self._current_my - park) > self._BED_AT_PARK_MM

    def _refresh_bed_button(self, rotary: bool) -> None:
        b = self._bottom_btns.get("bed_front")
        if b is None:
            return
        if not rotary:
            self._bed_back_shown = None
            b.set_text("Bed to front")
            b.setToolTip(_tip("Homes Y: the bed moves all the way to the front."))
            return
        back = self._bed_away_from_park()
        if back == self._bed_back_shown:
            return
        self._bed_back_shown = back
        if back:
            b.set_text("Bed back")
            b.setToolTip(_tip(
                "The bed moves back to where Rotary Mode parked it "
                f"(machine Y {self._rotary_park_my():.3f})."))
        else:
            b.set_text("Bed forward")
            b.setToolTip(_tip(
                "Z goes up to the top of its travel.",
                f"Then the bed moves {self._bed_forward_mm():g} mm toward "
                "the front, so you can reach the tool."))

    def _bed_forward_mm(self) -> float:
        """Distance Bed forward moves (preference controls/rotaryBedForwardMm,
        40 mm unless changed)."""
        try:
            d = float(self._prefs.value("controls/rotaryBedForwardMm", 40.0))
        except (TypeError, ValueError):
            d = 40.0
        return d if d > 0 else 40.0

    def _on_bed_front(self) -> None:
        """Bed to front (Y homing) -- or, in rotary mode, Bed forward / Bed back.

        With the rotary connected the firmware refuses every Y command it
        understands (``rotaryYLock``: moveY, grblHomingY, the macros), because
        it parked the bed at the rotary working position itself. The operator
        still needs the bed nearer for a tool change on the rotary chuck. The
        firmware does pass raw GRBL lines through untouched, and a ``$J=`` jog
        on Y moves the bed with the rotary connected (checked on the machine).
        So in rotary mode this button jogs: Z to the top of its travel first
        (clears the stock), then Y toward the front by a stored distance (40 mm
        unless the preference is changed); the park position is recorded and the button turns
        into **Bed back**, which jogs Y back to it so the job runs from where
        the firmware parked the bed. Jogs are soft-limit checked by GRBL
        (``error:15`` -> the End of Travel notice), and a running jog is ended
        by the Reset button like any other.

        Front = machine Y toward its home end: Bed to front *is* Y homing, and
        the machine homes Y at machine 0 with its travel running negative
        (job_fit), so toward the front means increasing machine Y."""
        conn = self._conn
        if conn is None:
            return
        if not conn.flags().get("rotaryAxisConnected", False):
            conn.cmd("grblHomingY")
            return
        from ui import job_fit
        if not self._pos_known:
            # Right after a launch the position is still the placeholder 0:
            # a jog planned from it would go to the wrong place.
            conn.motion_error.emit(
                "Position Not Known Yet",
                "The machine has not reported where the bed is yet. "
                "Try again in a moment.")
            return
        feed = max(100, int(self._speed_xy.feed_mmmin()))
        if self._bed_away_from_park():
            conn.send_raw(f"$J=G53 G90 Y{self._rotary_park_my():.3f} F{feed}")
            return
        # One press, no dialog: the distance is a stored preference
        # (controls/rotaryBedForwardMm, 40 mm unless changed), clamped to the
        # travel below.
        dist = self._bed_forward_mm()
        pos = conn.current_position()
        my = float(pos.get("my", 0.0))
        ranges = job_fit.machine_ranges(conn.grbl_settings())
        if ranges is None:
            # Travel not read yet: ask for it (for next time) and let GRBL's
            # own soft limit refuse a jog that would go too far.
            conn.request_grbl_settings()
        else:
            room = ranges["Y"][1] - my - 0.5
            if room <= 0.01:
                conn.motion_error.emit(
                    "Bed Already at the Front",
                    "The bed is at the front end of its travel.")
                return
            dist = min(dist, room)
            ztop = ranges["Z"][1] - 0.5
            zfeed = max(100, int(self._speed_z.feed_mmmin()))
            if float(pos.get("mz", 0.0)) < ztop - 0.01:
                conn.send_raw(f"$J=G53 G90 Z{ztop:.3f} F{zfeed}")
                # The Y jog follows after a beat, not in the same instant: a
                # priority send cancels and resets whatever is still in the
                # port's output buffer before it writes, so two raw lines
                # fired back to back could lose the first (the Z lift) --
                # and a jog that never arrived is a press that did nothing.
                QTimer.singleShot(250, lambda: self._rotary_bed_jog(conn, my, dist, feed))
                return
        self._rotary_bed_jog(conn, my, dist, feed)

    def _rotary_bed_jog(self, conn, my: float, dist: float, feed: int) -> None:
        # Bed forward never moves the park. Only when Rotary mode has never
        # recorded one (set up before this app version, or from the machine
        # itself) is the place the bed leaves taken as the park.
        if self._rotary_park_my() is None:
            from ui.pages.dialogs import ROTARY_PARK_KEY
            self._prefs.setValue(ROTARY_PARK_KEY, f"{my:.3f}")
        conn.send_raw(f"$J=G53 G90 Y{my + dist:.3f} F{feed}")

    def _on_status_for_gates(self, status: str) -> None:
        self._machine_status = str(status).split(":")[0].strip().lower()
        self._sync_gates()
        self._frame_watch()

    def _sync_gates(self) -> None:
        """Enable/disable the page according to the machine state (Android
        button-mask parity): motion and procedures are blocked while a job
        runs (Run/Hold), during homing and while a guided procedure is busy;
        unlock is only offered in Alarm; detection additionally
        needs the distance sensor; the rotary axis locks Y motion, probing
        and homing (Z homing stays available on the Z pad)."""
        conn = self._conn
        if conn is None:
            return
        connected = conn.is_serial_connected or conn.is_ws_active
        flags = conn.flags()
        st = self._machine_status
        busy = bool(flags.get("procedureBusy", False))
        rotary = bool(flags.get("rotaryAxisConnected", False))
        job_running = bool(flags.get("jobRunning", False))
        job_paused = bool(flags.get("jobPaused", False))
        door_blocked = conn.door_blocked()
        has_file = conn.opened_file_name() is not None
        # Running state = a motion this page must not interrupt, or an active /
        # paused job. Using both status and flags avoids brief stale windows
        # where status has not updated yet but the firmware already reports
        # jobRunning/Paused.
        #
        # "jog" is deliberately NOT in that list, and this is the whole of
        # press-and-hold jogging working. `Jog` is the state the pad's own
        # continuous jog puts the machine in (firmware README 5.31.1 — "during
        # continuous jogging machineStatus is Jog"), and it arrives ~one status
        # poll after the hold starts. Disabling the pad on it takes the mouse
        # press out from under the button the user is still holding: the press
        # is cancelled, no release is ever delivered, and the jog ends about a
        # keep-alive later. A held arrow gave a twitch instead of continuous
        # motion. A jog is ended by releasing the button — never by greying out
        # the control that is driving it.
        #
        # This is also what the mobile app does; the divergence was ours. Its
        # mask is `run` / `hold` / `home` only (App.tsx `motionEnabled`), with
        # no `jog`. Discrete taps are unaffected either way: those go out as
        # move* commands and report `Run`.
        machine_running = (st in ("run", "home", "hold")
                   or job_running or job_paused)
        motion = connected and not busy and not machine_running
        acting = motion

        # Jog pads, step/speed pills, A axis
        for w in (self._jog_pad, self._z_pad,
                  self._step_x, self._step_y, self._step_a, self._step_z,
                  self._speed_xy, self._speed_z):
            w.setEnabled(motion)

        # Move-to / detection buttons
        b = self._bottom_btns
        # Go to XY0 moves Y, which the rotary locks -- in rotary mode it is
        # Go to X0 instead (raw jog lines, see _goto_x0), so it stays enabled;
        # not while that or Stock length is already running.
        b["goto_xy0"].setEnabled(acting and (not rotary
                                             or getattr(self, "_fs", None) is None))
        # Bed to front is Y homing, which the firmware refuses with the rotary
        # connected -- in rotary mode the button is Bed forward / Bed back
        # instead (raw jog lines, see _on_bed_front), so it stays enabled.
        b["bed_front"].setEnabled(acting)
        b["type_coord"].setEnabled(acting)
        b["dry_run"].setEnabled(acting and not rotary)
        # Facing generates a program and uploads it. No open file
        # needed; gated like dry-run because a flat raster means nothing on a
        # rotary bar, and nothing should go up the link mid-job.
        b["facing"].setEnabled(acting and not rotary)
        # Frame needs a file open and can't run while a job/macro is active.
        # It moves Y, which the rotary locks -- so in rotary mode the button is
        # Stock length instead (X and Z only, raw jog lines; see _frame_stock),
        # and stays enabled; not while one is already running.
        b["frame"].setEnabled(acting and has_file
                              and getattr(self, "_fs", None) is None)
        b["detect_z0"].setEnabled(acting and not rotary)

        # Toolbar actions.
        a = self._action_btns
        a["homing"].setEnabled(acting and not rotary)
        a["unlock"].setEnabled(connected and st == "alarm")
        a["softreset"].setEnabled(motion)
        # Spindle control follows the mobile app: available whenever the machine
        # is connected and not moving/running.
        # Plus the door: offering to spin the tool up with the lid open is the
        # one thing this panel should not do. The firmware would stop it anyway
        # — a spinning spindle and an open door always pause, whatever
        # allowMotionWhenDoorOpen says — but that is a pause after the fact, and
        # the button should not invite it in the first place.
        a["spindle"].setEnabled(connected and not machine_running
                                and not door_blocked)
        a["rotary"].setEnabled(acting and not rotary)

        # Tool length probing is deactivated in rotary mode
        self._tool_length.setEnabled(connected and not machine_running and not rotary)
        # No machine, no measurement: back to the placeholder, so the previous
        # machine's length can't be read as this one's. The connection forgets
        # its cached value at the same time (_reset_machine_state), so the next
        # one re-emits even when it reports the same number.
        if not connected:
            self._tool_length.clear_value()

    def _on_position_changed(self, pos: dict) -> None:
        self._current_ja = float(pos.get("ja", self._current_ja))
        if "my" in pos:
            self._pos_known = True
            self._current_my = float(pos["my"])
            if self._conn is not None:
                self._refresh_bed_button(
                    bool(self._conn.flags().get("rotaryAxisConnected", False)))

    def _move_a(self, direction: int) -> None:
        """Step the A axis one step in the given direction.

        Tap-only: the firmware has no A-axis jog (README 5.31). Relative for
        the same reason as the XYZ steps — `self._current_ja` comes from the
        status poll, so a second tap before it catches up used to step from a
        stale angle and come out short."""
        if not self._conn:
            return
        self._conn.cmd("moveA", a=round(direction * self._step_a.value(), 4),
                       relative=True, feedRate=self._speed_xy.feed_mmmin())

    def _on_link_toggled(self, on: bool) -> None:
        self._refresh_link_look()
        if on:                       # snap Y to X the moment the axes are linked
            self._sync_link("x")

    def _sync_link(self, source: str) -> None:
        """When linked, mirror the just-changed axis onto the other one."""
        if not self._link_b.isChecked() or self._syncing_xy:
            return
        self._syncing_xy = True
        if source == "x":
            self._step_y.copy_selection_from(self._step_x)
        else:
            self._step_x.copy_selection_from(self._step_y)
        self._syncing_xy = False
        self._refresh_info_boxes()

    def _refresh_link_look(self) -> None:
        """Highlight the link button (blue chain icon) while X/Y are linked."""
        on = self._link_b.isChecked()
        self._link_b.setIcon(icons.icon("link", px(16), IDLE_C if on else TXT2))
        self._link_b.setStyleSheet(
            f"QPushButton {{ background: {'#E2EEF2' if on else WHITE}; "
            f"border: 1.5px solid {IDLE_C if on else BORDER}; "
            f"border-radius: {px(8)}px; }}"
            f"QPushButton:hover {{ background: #EFEFEF; }}")

    def _refresh_info_boxes(self) -> None:
        """Mirror selected step sizes + speed into the jog info boxes."""
        def _fmt(v: float) -> str:
            return f"{v:g}"
        speed_names = {200: "Slow", 750: "Normal", 2000: "Fast"}
        sp = speed_names.get(self._speed_xy.feed_mmmin(), "Normal")
        spz = speed_names.get(self._speed_z.feed_mmmin(), "Normal")
        rotary = bool(self._conn and
                      self._conn.flags().get("rotaryAxisConnected", False))
        second = self._step_a.value() if rotary else self._step_y.value()
        self._jog_pad.set_info(_fmt(self._step_x.value()), _fmt(second), sp)
        self._z_pad.set_info(_fmt(self._step_z.value()), spz)

    def _on_type_coordinates(self) -> None:
        """Open the move-to dialog (machine/job coordinate entry). The dialog
        reads the live position and issues the move commands itself."""
        from ui.pages.dialogs import TypeCoordinatesDialog
        if not self._conn:
            return
        rotary = bool(self._conn.flags().get("rotaryAxisConnected", False))
        TypeCoordinatesDialog(self._conn, parent=self, rotary=rotary,
                              feed_provider=self._speed_xy.feed_mmmin).exec()
