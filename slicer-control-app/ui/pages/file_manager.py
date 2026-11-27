"""
ui/pages/file_manager.py
File Manager page matching the Piccola CNC desktop design: jobs / point-cloud file
category selector, search/sort, multi-select with batch delete, per-file
context menu (rename / delete / download), in-row transfer progress, and an
upload action.
"""

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QMenu, QFileDialog, QProgressBar, QButtonGroup,
    QSizePolicy, QGraphicsOpacityEffect, QApplication,
)
from PySide6.QtGui import QCursor, QAction, QFontMetrics


class _ElidingLabel(QLabel):
    """A QLabel that shows as much of its text as fits and appends an ellipsis
    when the rest is clipped, re-eliding on every resize. The full text is kept
    as the tooltip so a long filename is always readable on hover."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._reelide()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt override)
        self._full = text
        self.setToolTip(text)
        self._reelide()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reelide()

    def _reelide(self) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideRight,
                                     max(0, self.width())))

from ui.theme import BG, PANEL, WHITE, LIGHT, BORDER, TXT, TXT2, TXT3, LIME, ERR_C
from ui.widgets import lbl
from ui.scale import fs, px, font, bind
from ui import icons
from ui.tooltips import POPUPS, TRANSPARENT, TRANSPARENT_NO_BORDER


def _styled_menu(parent) -> QMenu:
    """QMenu with real rounded (filleted) corners: the popup window is made
    translucent so the stylesheet's border-radius actually clips the corners.

    Created fresh per click, so reading the scale here keeps popups crisp."""
    m = QMenu(parent)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint
                     | Qt.NoDropShadowWindowHint)
    m.setAttribute(Qt.WA_TranslucentBackground)
    m.setStyleSheet(f"""
        QMenu {{ background: {WHITE}; border: 1px solid {BORDER};
                 border-radius: {px(12)}px; padding: {px(6)}px; }}
        QMenu::item {{ padding: {px(8)}px {px(26)}px; font-size: {fs(13)}px; color: {TXT};
                       border-radius: {px(8)}px; }}
        QMenu::item:selected {{ background: {LIGHT}; }}
    """)
    return m


def _fmt_size(num_bytes) -> str:
    """Human size with exactly one digit after the dot (e.g. '15.0 Mb')."""
    try:
        b = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} Mb"
    if b >= 1024:
        return f"{b / 1024:.1f} Kb"
    return f"{b:.0f} b"


def _fmt_duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s <= 0:
        return "—"
    return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else \
        f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _fmt_dim(meta: dict) -> str:
    try:
        w = float(meta.get("fileWidth", 0))
        d = float(meta.get("fileDepth", 0))
        h = float(meta.get("fileHeight", 0))
        if w or d or h:
            return f"{w:g}×{d:g}×{h:g}mm"
    except (TypeError, ValueError):
        pass
    return "—"


def _fmt_date(date_str) -> tuple:
    """Split an ISO-ish date string into (date, time) display parts."""
    s = str(date_str or "").strip()
    if not s:
        return "—", ""
    if " " in s:
        d, _, t = s.partition(" ")
    elif "T" in s:
        d, _, t = s.partition("T")
    else:
        d, t = s, ""
    d = d.replace("-", ".")
    return d, t[:8]


#: Longest upload name offered for "keep both". The firmware allows 100
#: characters, and appends "_TMP" while the upload is in progress.
_MAX_UPLOAD_NAME = 90


def _free_name(name: str, taken: set) -> str:
    """First "<base>_<n><ext>" (n = 1, 2, …) not in *taken* (lower-case)."""
    base, ext = os.path.splitext(name)
    n = 1
    while True:
        suffix = f"_{n}{ext}"
        candidate = base[:_MAX_UPLOAD_NAME - len(suffix)] + suffix
        if candidate.lower() not in taken:
            return candidate
        n += 1


class FileRow(QFrame):
    """One machine-file row. Supports selection mode and an in-row transfer
    state (progress bar replacing the metadata columns, as in the design)."""

    def __init__(self, meta: dict, page: "FileManagerPage",
                 transfer_label: str | None = None):
        super().__init__()
        self._meta = meta
        self._page = page
        self.name = meta.get("fileName", "")
        self.setObjectName("fileRow")
        # .ply rows don't open on click → no pointing-hand affordance
        self.setCursor(QCursor(
            Qt.ArrowCursor if self.name.lower().endswith(".ply")
            else Qt.PointingHandCursor))
        self._opened = False
        self._selected = False
        self._in_transfer = False
        self._locked = False
        self._compact = 0
        self.processing = False      # uploaded, waiting for the machine to list it

        self._row = QHBoxLayout(self)

        # Selection indicator: squared checkbox (visible only in selection mode)
        self._check = QLabel("")
        self._check.setAlignment(Qt.AlignCenter)
        self._check.hide()
        self._row.addWidget(self._check)

        self._ic = QLabel()
        self._ic.setStyleSheet(TRANSPARENT_NO_BORDER)
        self._row.addWidget(self._ic)

        self._name_col = QVBoxLayout()
        # Eliding label so a long filename fills the whole name column and
        # tapers to "…" (full name on hover) instead of clipping mid-character.
        self._name_lbl = _ElidingLabel(self.name)
        self._name_col.addWidget(self._name_lbl)
        # Unified creation date + time on one label (all file types)
        date, time_part = _fmt_date(meta.get("fileDate"))
        stamp = f"Date: {date} {time_part}".rstrip()
        self._date_lbl = lbl(stamp, sz=11, color=TXT2)
        self._name_col.addWidget(self._date_lbl)
        # The name column takes ALL the horizontal slack (no competing stretch
        # below) so long filenames get the maximum available width.
        self._row.addLayout(self._name_col, 1)

        # ── Metadata columns (hidden while a transfer is shown) ────────────
        # Fixed-width columns so every row lines up vertically. .ply scans have
        # no job duration / dimensions → blank placeholders keep the alignment.
        cols = [
            (_fmt_size(meta.get("fileSize")), 90),
            ("" if self.is_ply else
             f"Est.Time {_fmt_duration(meta.get('estimatedDurationSeconds'))}", 140),
            ("" if self.is_ply else f"Dim: {_fmt_dim(meta)}", 170),
        ]
        self._meta_widgets = []
        self._meta_widths = []
        for text, width in cols:
            w = lbl(text, sz=11, color=TXT2)
            self._meta_widgets.append(w)
            self._meta_widths.append(width)
            self._row.addWidget(w)

        # ── In-row transfer area (label + progress + cancel) ────────────────
        self._tr_lbl = lbl("", sz=11, color=TXT2)
        self._tr_lbl.hide()
        self._row.addWidget(self._tr_lbl)
        self._tr_bar = QProgressBar()
        self._tr_bar.setTextVisible(False)
        self._tr_bar.hide()
        self._row.addWidget(self._tr_bar, 4)
        self._tr_cancel = QPushButton("⊗")
        self._tr_cancel.setCursor(QCursor(Qt.PointingHandCursor))
        self._tr_cancel.clicked.connect(lambda: self._page.cancel_transfer())
        self._tr_cancel.hide()
        self._row.addWidget(self._tr_cancel)

        # ── Context menu ────────────────────────────────────────────────────
        self._menu_b = QPushButton("⋮")
        self._menu_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._menu_b.clicked.connect(self._show_menu)
        self._row.addWidget(self._menu_b)

        bind(self, self._apply_scale)

        if transfer_label is not None:
            self.set_transfer(transfer_label)

    def _apply_scale(self, *_):
        self.setFixedHeight(px(76))
        self._row.setContentsMargins(px(18), px(8), px(8), px(8))
        self._row.setSpacing(px(14))
        self._check.setFixedSize(px(20), px(20))
        self._ic.setPixmap(icons.pixmap("doc", px(36), TXT))
        self._name_col.setSpacing(px(3))
        # Bold filename, styled here (the eliding label isn't built via lbl()).
        self._name_lbl.setFont(font(14, bold=True))
        self._name_lbl.setStyleSheet(f"color: {TXT}; background: transparent;")
        self._name_lbl.setMinimumWidth(px(110))   # allow shrinking on narrow windows
        self._name_lbl._reelide()                 # re-fit to the new font/scale
        for w, width in zip(self._meta_widgets, self._meta_widths):
            w.setFixedWidth(px(width))
        self._tr_bar.setFixedHeight(px(6))
        self._tr_bar.setStyleSheet(f"""
            QProgressBar {{ background: {LIGHT}; border-radius: {px(3)}px; border: none; }}
            QProgressBar::chunk {{ background: {LIME}; border-radius: {px(3)}px; }}
        """)
        self._tr_cancel.setFixedSize(px(30), px(30))
        self._tr_cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                           font-size: {fs(18)}px; color: {TXT}; }}
            QPushButton:hover {{ color: {ERR_C}; }}
        """)
        self._menu_b.setFixedSize(px(34), px(34))
        self._menu_b.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                           font-size: {fs(20)}px; color: {TXT}; }}
            QPushButton:hover {{ background: {LIGHT}; border-radius: {px(17)}px; }}
        """)
        # Re-apply state-dependent visuals at the new scale.
        self._style_check()
        self._apply_style()
        self.set_compact(self._compact)

    # ── States ───────────────────────────────────────────────────────────────

    def set_opened(self, opened: bool) -> None:
        self._opened = opened
        self._apply_style()

    def set_select_mode(self, on: bool) -> None:
        self._check.setVisible(on)
        self._menu_b.setVisible(not on)
        if not on:
            self.set_selected(False)

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        self._style_check()
        self._apply_style()

    def _style_check(self) -> None:
        """Squared checkbox look: empty outline / dark square with a check."""
        if self._selected:
            self._check.setText("✓")
            self._check.setStyleSheet(
                f"background: {TXT}; border: 1.5px solid {TXT}; border-radius: {px(4)}px; "
                f"font-size: {fs(13)}px; font-weight: bold; color: white;")
        else:
            self._check.setText("")
            self._check.setStyleSheet(
                f"background: transparent; border: 1.5px solid {TXT2}; "
                f"border-radius: {px(4)}px;")

    @property
    def selected(self) -> bool:
        return self._selected

    def set_transfer(self, label: str) -> None:
        """Switch the row into transfer mode: 'Uploading/Downloading [===] ⊗'."""
        self._in_transfer = True
        for w in self._meta_widgets:
            w.hide()
        self._date_lbl.hide()
        self._menu_b.hide()
        self._tr_lbl.setText(label)
        self._tr_lbl.show()
        self._tr_bar.setValue(0)
        self._tr_bar.show()
        self._tr_cancel.show()

    def set_processing(self) -> None:
        """Placeholder for a just-uploaded file the machine has not listed yet:
        a busy bar, no cancel, and no click / menu interaction."""
        self.processing = True
        self.set_transfer("Upload finished, processing…")
        self._tr_bar.setRange(0, 0)         # indeterminate (busy) bar
        self._tr_cancel.hide()
        self.setCursor(QCursor(Qt.ArrowCursor))

    def set_transfer_progress(self, pct: int) -> None:
        self._tr_bar.setValue(max(0, min(100, pct)))

    def clear_transfer(self) -> None:
        self._in_transfer = False
        self._tr_lbl.hide()
        self._tr_bar.hide()
        self._tr_cancel.hide()
        self._date_lbl.show()
        self._menu_b.show()
        self.set_compact(self._compact)

    def set_compact(self, level: int) -> None:
        """Responsive metadata columns: 0 = all, 1 = no dim, 2 = size only,
        3 = none. Ignored while the row shows a transfer."""
        self._compact = level
        if self._in_transfer:
            return
        # _meta_widgets order: [size, est.time, dim]
        vis = {0: (True, True, True), 1: (True, True, False),
               2: (True, False, False)}.get(level, (False, False, False))
        for w, v in zip(self._meta_widgets, vis):
            w.setVisible(v)

    def _apply_style(self) -> None:
        if self._selected:
            border = f"1.5px solid {TXT}"
            bg = "#CDD6DD"           # clearly darker than the white rows
        elif self._opened:
            border = f"1.5px solid {TXT2}"
            bg = WHITE
        else:
            border = f"1px solid {BORDER}"
            bg = WHITE
        self.setStyleSheet(
            f"#fileRow {{ background: {bg}; border: {border}; border-radius: {px(10)}px; }}"
        )

    def set_locked(self, locked: bool) -> None:
        """Dim the row and show a 'not-allowed' cursor while file selection is
        disabled (a job is running / a procedure is busy), so it's visually
        obvious the list can't be interacted with."""
        locked = bool(locked)
        if locked == self._locked:
            return
        self._locked = locked
        if locked:
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.4)
            self.setGraphicsEffect(eff)
            self.setCursor(QCursor(Qt.ForbiddenCursor))
        else:
            self.setGraphicsEffect(None)
            self.setCursor(QCursor(
                Qt.ArrowCursor if self.name.lower().endswith(".ply")
                else Qt.PointingHandCursor))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            shift = bool(e.modifiers() & Qt.ShiftModifier)
            self._page.row_clicked(self, shift=shift)

    @property
    def is_ply(self) -> bool:
        return self.name.lower().endswith(".ply")

    def _show_menu(self) -> None:
        # Android parity: the per-file menu is unreachable while a job runs
        # or a procedure is busy (App.tsx ~5465).
        if self._page._menu_locked():
            return
        m = _styled_menu(self)
        if self.is_ply:
            entries = [
                ("Visualize", lambda: self._page.visualize_file(self.name)),
                ("Download",  lambda: self._page.download_file(self.name)),
                ("Delete",    lambda: self._page.delete_files([self.name])),
            ]
        else:
            entries = [
                ("Open",     lambda: self._page.open_file(self.name)),
                ("Rename",   lambda: self._page.rename_file(self.name)),
                ("Download", lambda: self._page.download_file(self.name)),
                ("Delete",   lambda: self._page.delete_files([self.name])),
            ]
        for label, cb in entries:
            act = QAction(label, m)
            act.triggered.connect(cb)
            m.addAction(act)
        m.exec(QCursor.pos())


class FileManagerPage(QWidget):
    #: A job file was sent to the machine to open (name); the main window
    #: shows the "Opening file" popup for it.
    file_opening = Signal(str)

    def __init__(self):
        super().__init__()
        self._conn = None
        self._files: list = []
        self._rows: list = []
        self._sort_key = "fileDate"
        self._sort_desc = True
        self._opened_name = None
        self._machine_status = ""       # base status, lowercase (gating)
        self._upload_lines_total = 0
        self._upload_row: FileRow | None = None
        # Uploaded names the machine has not listed yet. A large file takes
        # the machine a while to process, and its list leaves the file out
        # until then, so a "processing" placeholder row stands in for it.
        self._processing: set[str] = set()
        self._download_row_name: str | None = None
        self._download_to_user = False
        self._pending_visualize: str | None = None
        self._select_mode = False
        self._anchor_name: str | None = None   # last clicked row (for shift-range)
        self.setStyleSheet(TRANSPARENT)

        root = QHBoxLayout(self)
        self._root = root

        # ── Left: machine file list panel ───────────────────────────────────
        self._panel = QFrame()
        self._panel.setObjectName("fmPanel")
        pl = QVBoxLayout(self._panel)
        self._pl = pl

        head = QHBoxLayout()
        self._head = head
        self._count_lbl = lbl("Files (0)", sz=15, bold=True)
        head.addWidget(self._count_lbl)

        # File-category selector: jobs (.nc) | 3D scans (.ply)
        self._type_wrap = QFrame()
        self._type_wrap.setObjectName("fmTypes")
        tw = QHBoxLayout(self._type_wrap)
        self._tw = tw
        tw.setSpacing(0)
        self._type_group = QButtonGroup(self)
        self._type_btns = []
        for i, label in enumerate(["jobs", "3D scans"]):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setMinimumWidth(86)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            self._type_group.addButton(b, i)
            self._type_btns.append(b)
            tw.addWidget(b)
        self._type_group.idClicked.connect(lambda _i: self._refresh_rows())
        # Starts disabled: no file list has arrived yet, so there are no scans
        # to show. _on_file_list re-evaluates it on every listing.
        self._sync_type_gate()
        head.addWidget(self._type_wrap)

        self._search_wrap = QFrame()
        self._search_wrap.setObjectName("fmSearch")
        sw = QHBoxLayout(self._search_wrap)
        self._sw = sw
        self._si = QLabel()
        self._si.setStyleSheet(TRANSPARENT_NO_BORDER)
        sw.addWidget(self._si)
        self._search = QLineEdit()
        self._search.textChanged.connect(self._refresh_rows)
        sw.addWidget(self._search)
        head.addWidget(self._search_wrap, 1)

        self._sort_b = self._head_btn("sort", "Sort")
        self._sort_b.clicked.connect(self._show_sort_menu)
        head.addWidget(self._sort_b)
        self._sel_b = self._head_btn("select-all", "Select all")
        self._sel_b.clicked.connect(self._toggle_select_all)
        head.addWidget(self._sel_b)

        # Enter selection mode without choosing anything. "select all" is the
        # only other way in, and it ticks every file on the way — fine for a
        # bulk delete, wrong when the point is to pick two files out of thirty.
        self._multi_b = self._head_btn("check", "Multi select")
        self._multi_b.clicked.connect(self._enter_multi_select)
        head.addWidget(self._multi_b)

        # Selection-mode actions (hidden until selection mode is active)
        self._del_sel_b = QPushButton("Delete")
        self._del_sel_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._del_sel_b.clicked.connect(self._delete_selected)
        self._del_sel_b.hide()
        head.addWidget(self._del_sel_b)

        self._cancel_sel_b = QPushButton("Cancel")
        self._cancel_sel_b.setCursor(QCursor(Qt.PointingHandCursor))
        self._cancel_sel_b.clicked.connect(self._exit_select_mode)
        self._cancel_sel_b.hide()
        head.addWidget(self._cancel_sel_b)
        pl.addLayout(head)

        self._divider = QFrame()
        self._divider.setFixedHeight(1)
        self._divider.setStyleSheet(f"background: {TXT3}; border: none;")
        pl.addWidget(self._divider)

        # Scrollable file rows
        self._list_host = QWidget()
        self._list_host.setStyleSheet(TRANSPARENT)
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        pl.addWidget(scroll, 1)
        self._scroll = scroll

        root.addWidget(self._panel, 3)

        # ── Right: upload column ────────────────────────────────────────────
        right_col = QVBoxLayout()
        self._right_col = right_col

        self._upload_btn = QPushButton("   upload file")
        self._upload_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._upload_btn.clicked.connect(self._pick_upload)
        right_col.addWidget(self._upload_btn)

        # Clear locally cached file copies (mobile-app parity)
        self._clear_cache_btn = QPushButton("   clear cache")
        self._clear_cache_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._clear_cache_btn.clicked.connect(self._clear_cache)
        right_col.addWidget(self._clear_cache_btn)

        right_col.addStretch()
        # Fixed-width host so the upload button can never be pushed out of view
        self._right_host = QWidget()
        self._right_host.setLayout(right_col)
        self._right_host.setStyleSheet(TRANSPARENT)
        root.addWidget(self._right_host)

        bind(self, self._apply_scale)

    def _apply_scale(self, *_):
        self._root.setContentsMargins(px(12), px(12), px(12), px(12))
        self._root.setSpacing(px(10))
        self._panel.setStyleSheet(
            f"#fmPanel {{ background: {PANEL}; border-radius: {px(12)}px; "
            f"border: 1px solid {BORDER}; }}"
        )
        self._pl.setContentsMargins(px(16), px(12), px(16), px(16))
        self._pl.setSpacing(px(10))
        self._head.setSpacing(px(14))

        self._type_wrap.setFixedHeight(px(34))
        self._type_wrap.setStyleSheet(
            f"#fmTypes {{ background: {WHITE}; border: 1px solid {BORDER}; "
            f"border-radius: {px(17)}px; }}"
        )
        self._tw.setContentsMargins(px(4), px(3), px(4), px(3))
        for b in self._type_btns:
            b.setFixedHeight(px(26))
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none;
                               border-radius: {px(13)}px; font-size: {fs(12)}px; color: {TXT}; }}
                QPushButton:checked {{ background: {LIGHT}; font-weight: bold; }}
                QPushButton:disabled {{ color: {TXT3}; }}
            """)

        self._search_wrap.setFixedHeight(px(36))
        self._search_wrap.setStyleSheet(
            f"#fmSearch {{ background: {PANEL}; border: 1px solid {TXT2}; "
            f"border-radius: {px(18)}px; }}"
        )
        self._sw.setContentsMargins(px(12), px(2), px(12), px(2))
        self._sw.setSpacing(px(6))
        self._si.setPixmap(icons.pixmap("search", px(16), TXT))
        self._search.setMinimumWidth(px(40))     # let the search bar shrink
        self._search.setStyleSheet(
            f"* {{ background: transparent; border: none; font-size: {fs(13)}px; }}"
            + POPUPS
        )

        # Header action buttons keep their compact text/min-width (owned by
        # _apply_responsive); here we only rescale their height, icon and style.
        for b, icon_name in ((self._sort_b, "sort"), (self._sel_b, "select-all"),
                             (self._multi_b, "check")):
            b.setFixedHeight(px(34))
            b.setIcon(icons.icon(icon_name, px(18), TXT))
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: none;
                               font-size: {fs(13)}px; color: {TXT}; padding: 0 {px(10)}px; }}
                QPushButton:hover {{ background: rgba(0,0,0,0.06); border-radius: {px(8)}px; }}
            """)

        self._del_sel_b.setFixedHeight(px(34))
        self._del_sel_b.setStyleSheet(f"""
            QPushButton {{ background: {ERR_C}; border: none; border-radius: {px(8)}px;
                           font-size: {fs(13)}px; color: white; padding: 0 {px(16)}px; }}
            QPushButton:hover {{ background: #B23939; }}
            QPushButton:disabled {{ background: {BORDER}; color: {TXT3}; }}
        """)
        self._cancel_sel_b.setFixedHeight(px(34))
        self._cancel_sel_b.setStyleSheet(f"""
            QPushButton {{ background: {LIGHT}; border: 1px solid {BORDER};
                           border-radius: {px(8)}px; font-size: {fs(13)}px; color: {TXT};
                           padding: 0 {px(14)}px; }}
            QPushButton:hover {{ background: #E4E4E4; }}
        """)

        # Right margin keeps the file rows clear of the scrollbar
        self._list_lay.setContentsMargins(0, px(4), px(12), px(4))
        self._list_lay.setSpacing(px(10))

        self._right_col.setSpacing(px(10))
        self._right_host.setFixedWidth(px(180))
        for b, icon_name, isz in ((self._upload_btn, "upload", 24),
                                  (self._clear_cache_btn, "reset", 20)):
            b.setIcon(icons.icon(icon_name, px(isz), TXT))
            b.setFixedHeight(px(58))
            b.setStyleSheet(f"""
                QPushButton {{ background: {PANEL}; border: 1px solid {BORDER};
                               border-radius: {px(12)}px; font-size: {fs(14)}px; color: {TXT};
                               text-align: left; padding-left: {px(20)}px; }}
                QPushButton:hover {{ background: #D2D2D2; }}
                QPushButton:disabled {{ background: #EDEDED; border: 1px solid #E2E2E2;
                                        color: #B7BBC1; }}
            """)

    # ── Small factories ──────────────────────────────────────────────────────

    @staticmethod
    def _head_btn(icon_name: str, text: str) -> QPushButton:
        # Initial size/style; FileManagerPage._apply_scale re-applies on rescale.
        b = QPushButton(f"  {text}")
        b.setIcon(icons.icon(icon_name, px(18), TXT))
        b.setFixedHeight(px(34))
        b.setMinimumWidth(96)
        b.setCursor(QCursor(Qt.PointingHandCursor))
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                           font-size: {fs(13)}px; color: {TXT}; padding: 0 {px(10)}px; }}
            QPushButton:hover {{ background: rgba(0,0,0,0.06); border-radius: {px(8)}px; }}
        """)
        return b

    # ── List handling ────────────────────────────────────────────────────────

    def _current_ext(self) -> str:
        return ".nc" if self._type_group.checkedId() == 0 else ".ply"

    def _sync_type_gate(self) -> None:
        """Offer the 3D-scans tab only when the machine actually has scans.

        Switching to an always-empty tab reads as a fault ("where are my
        files?"), so the button is disabled until at least one .ply exists. If
        the last scan is deleted while that tab is open, fall back to jobs
        rather than leaving the user on a dead tab.
        """
        has_ply = any(f.get("fileName", "").lower().endswith(".ply")
                      for f in self._files)
        btn = self._type_btns[1]
        btn.setEnabled(has_ply)
        btn.setToolTip("" if has_ply else "No 3D scans on the machine yet")
        if not has_ply and self._type_group.checkedId() == 1:
            self._type_btns[0].setChecked(True)

    def _on_file_list(self, files: list) -> None:
        self._files = [f for f in files if isinstance(f, dict)]
        # The machine lists a processed upload again → drop its placeholder
        self._processing -= {f.get("fileName", "") for f in self._files}
        self._sync_type_gate()
        self._refresh_rows()

    def _park_focus(self) -> None:
        """Move keyboard focus off a row that is about to be deleted.

        A clicked button keeps focus (a row's ⋮, the upload row's ⊗). When its
        row is deleted, Qt hands focus down the chain of buttons, the scroll
        area follows it, and the list jumped to the bottom whenever it was
        rebuilt (e.g. when an uploaded file finished processing). The scroll
        area itself takes the focus instead: it does not move the list.
        """
        fw = QApplication.focusWidget()
        if fw is not None and self._list_host.isAncestorOf(fw):
            self._scroll.setFocus(Qt.OtherFocusReason)

    def _refresh_rows(self) -> None:
        # An in-row transfer keeps the current rows alive (progress would reset)
        if self._upload_row is not None or self._download_row_name is not None:
            return
        self._park_focus()
        bar = self._scroll.verticalScrollBar()
        keep = bar.value()
        for r in self._rows:
            self._list_lay.removeWidget(r)
            r.setParent(None)
            r.deleteLater()
        self._rows = []
        # Re-applied once the new rows are laid out: emptying the list clamps
        # the position to 0, so without this every refresh jumped to the top.
        QTimer.singleShot(0, lambda: bar.setValue(keep))

        ext = self._current_ext()
        query = self._search.text().strip().lower()
        files = [f for f in self._files
                 if f.get("fileName", "").lower().endswith(ext)
                 and (not query or query in f.get("fileName", "").lower())
                 and f.get("fileName", "") not in self._processing]

        def _key(f):
            v = f.get(self._sort_key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        if self._sort_key in ("fileName", "fileDate"):
            files.sort(key=lambda f: str(f.get(self._sort_key, "")).lower(),
                       reverse=self._sort_desc)
        else:
            files.sort(key=_key, reverse=self._sort_desc)

        if self._conn is not None and self._conn.file_list_truncated:
            # The firmware cut its reply off at 8 KB: say so, or the files
            # after the cut look deleted and new uploads look lost.
            self._count_lbl.setText(f"Files ({len(files)}+)")
            self._count_lbl.setToolTip(
                "The machine's file list is longer than it can send (8 KB per "
                "reply), so some files are not shown, usually the newest ones. "
                "They are still on the machine. Delete old files to see them.")
        else:
            self._count_lbl.setText(f"Files ({len(files)})")
            self._count_lbl.setToolTip("")
        level = self._compact_level()
        locked = self._machine_locked()
        for f in files:
            row = FileRow(f, self)
            row.set_opened(f.get("fileName") == self._opened_name)
            row.set_select_mode(self._select_mode)
            row.set_compact(level)
            row.set_locked(locked)      # dim newly-built rows if selection is locked
            self._rows.append(row)
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)

        # Uploads still being processed sit at the top, like the upload row
        for name in sorted(self._processing):
            if name.lower().endswith(ext):
                row = FileRow({"fileName": name}, self)
                row.set_processing()
                self._rows.insert(0, row)
                self._list_lay.insertWidget(0, row)

    def _show_sort_menu(self) -> None:
        m = _styled_menu(self)
        for label, key, desc in [("By name", "fileName", False),
                                 ("By size", "fileSize", True),
                                 ("By date", "fileDate", True)]:
            act = QAction(label, m)
            act.triggered.connect(
                lambda _c=False, k=key, d=desc: self._set_sort(k, d))
            m.addAction(act)
        m.exec(QCursor.pos())

    def _set_sort(self, key: str, desc: bool) -> None:
        self._sort_key = key
        self._sort_desc = desc
        self._refresh_rows()

    # ── Selection mode ───────────────────────────────────────────────────────

    def _toggle_select_all(self) -> None:
        if not self._rows:
            return
        if not self._select_mode:
            self._enter_select_mode()
        # select every visible row
        for r in self._rows:
            r.set_selected(True)
        self._update_selection_ui()

    def _enter_multi_select(self) -> None:
        """Show the selection boxes with nothing ticked.

        The rows are cleared explicitly rather than trusted to be clean.
        Leaving selection mode does clear them (set_select_mode(False) calls
        set_selected(False)), so this is belt and braces — but "no files are
        selected" is the whole point of the button, and a stale tick would mean
        a delete that takes a file the operator never chose.
        """
        if not self._rows:
            return
        if not self._select_mode:
            self._enter_select_mode()
        for r in self._rows:
            r.set_selected(False)
        self._update_selection_ui()

    def _enter_select_mode(self) -> None:
        self._select_mode = True
        for r in self._rows:
            r.set_select_mode(True)
        # Swap the header actions (mobile-app parity): hide sort + select-all,
        # reveal delete + cancel. The swap keeps the header no wider than its
        # normal state, so the page can't overflow the scroll viewport and push
        # the right-hand upload / clear-cache column off-screen.
        self._sort_b.hide()
        self._sel_b.hide()
        self._multi_b.hide()
        self._del_sel_b.show()
        self._cancel_sel_b.show()
        # Right-side actions stay available throughout selection.
        self._upload_btn.show()
        self._clear_cache_btn.show()

    def _exit_select_mode(self) -> None:
        self._select_mode = False
        for r in self._rows:
            r.set_select_mode(False)
        self._del_sel_b.hide()
        self._cancel_sel_b.hide()
        self._sort_b.show()
        self._sel_b.show()
        self._multi_b.show()
        self._upload_btn.show()
        self._clear_cache_btn.show()
        self._apply_responsive()   # restore correct compact text on sort/select-all

    def _update_selection_ui(self) -> None:
        n = sum(1 for r in self._rows if r.selected)
        self._del_sel_b.setText(f"Delete ({n})" if n else "Delete")
        self._del_sel_b.setEnabled(n > 0)

    def row_clicked(self, row: FileRow, shift: bool = False) -> None:
        # Ignore all row interaction (open / select) while a job is running or
        # held, or a procedure is busy — the file list must not be touched then.
        if self._machine_locked() or row.processing:
            return
        if shift:
            # Shift+click: multi-selection for batch deletion. Selects the
            # range from the previously clicked row (anchor) to this one, so
            # both the first-clicked file and this one end up selected.
            if not self._select_mode:
                self._enter_select_mode()
            names = [r.name for r in self._rows]
            if self._anchor_name in names and row.name in names:
                i0 = names.index(self._anchor_name)
                i1 = names.index(row.name)
                lo, hi = min(i0, i1), max(i0, i1)
                for r in self._rows[lo:hi + 1]:
                    if not r.processing:
                        r.set_selected(True)
            else:
                row.set_selected(True)
            self._update_selection_ui()
            self._anchor_name = row.name
            return

        self._anchor_name = row.name
        if self._select_mode:
            row.set_selected(not row.selected)
            self._update_selection_ui()
        elif not row.is_ply:
            # Only .nc job files open on click; .ply scans are visualized
            # through their ⋮ menu instead.
            self.open_file(row.name)

    def _delete_selected(self) -> None:
        names = [r.name for r in self._rows if r.selected]
        if names:
            self.delete_files(names)

    # ── File actions ─────────────────────────────────────────────────────────

    def open_file(self, name: str) -> None:
        if self._conn and not self._machine_locked():
            already_open = name == self._conn.opened_file_name()
            self._conn.cmd("openGcodeFile", filename=name)
            if not already_open:
                self.file_opening.emit(name)

    def rename_file(self, name: str) -> None:
        if self._menu_locked():
            return
        from ui.pages.dialogs import TextInputDialog
        base = name[:-3] if name.endswith(".nc") else name
        dlg = TextInputDialog("Rename file", "New filename",
                              initial=base, suffix=".nc", parent=self)
        if dlg.exec() and self._conn:
            new = dlg.text()
            if not new:
                return
            if not new.endswith(".nc"):
                new += ".nc"
            self._conn.cmd("renameGcodeFile", filename=name, newFilename=new)

    def delete_files(self, names: list) -> None:
        if self._menu_locked():
            return
        from ui.pages.dialogs import ConfirmDialog
        if len(names) == 1:
            msg = f"Delete {names[0]}? This cannot be undone."
            title = "Delete file"
        else:
            msg = f"Delete {len(names)} files? This cannot be undone."
            title = "Delete files"
        dlg = ConfirmDialog(title, msg, parent=self)
        if not dlg.exec() or not self._conn:
            return
        # If the file being deleted is the one currently open, close it first so
        # it stops showing in the file bar.
        if self._conn.opened_file_name() in names:
            self._conn.cmd("closeGcodeFile")
        # Stagger batch deletes by 500 ms (mobile-app behaviour)
        for i, name in enumerate(names):
            # Forget the cached copy now: a later file with the same name
            # (G-code or a new scan) must be downloaded, not opened stale.
            self._conn.forget_cached_file(name)
            QTimer.singleShot(
                500 * i,
                lambda n=name: self._conn and self._conn.cmd(
                    "deleteGcodeFile", filename=n))
        self._exit_select_mode()

    def download_file(self, name: str, to_downloads: bool = True) -> None:
        """Download *name* from the machine. With *to_downloads* the file is
        also copied into the user's Downloads folder and a popup confirms it."""
        if not self._conn or self._download_row_name or self._upload_row:
            return
        row = next((r for r in self._rows if r.name == name), None)
        if row is None:
            return
        self._download_row_name = name
        self._download_to_user = to_downloads
        row.set_transfer("Downloading")
        self._conn.download_file(name)

    def visualize_file(self, name: str) -> None:
        """Open the 3-D point-cloud viewer for *name* (downloading it first
        into the local cache if needed)."""
        if not self._conn or not self._machine_idle_for_ply():
            return
        path = self._conn.cached_file_path(name)
        if path:
            self._open_ply_viewer(name, path)
            return
        self._pending_visualize = name
        self.download_file(name, to_downloads=False)

    def _open_ply_viewer(self, name: str, path: str) -> None:
        from ui.pages.ply_viewer import PlyViewerDialog
        PlyViewerDialog(name, path, parent=self).exec()

    def cancel_transfer(self) -> None:
        if self._conn:
            if self._upload_row is not None:
                self._conn.abort_transfer()
            if self._download_row_name is not None:
                self._conn.abort_download()
        self._end_upload_row()
        self._end_download_row()

    def _clear_cache(self) -> None:
        """Delete the locally cached file copies (machine files untouched)."""
        import shutil
        from ui.pages.dialogs import ConfirmDialog, InfoDialog, cache_clear_message
        from connection import CACHE_DIR
        message, has_files = cache_clear_message()
        if not has_files:
            InfoDialog("Stored cache", message, parent=self).exec()
            return
        dlg = ConfirmDialog("Clear stored cache?", message, parent=self,
                            yes_label="Yes", no_label="No")
        if not dlg.exec():
            return
        try:
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            os.makedirs(CACHE_DIR, exist_ok=True)
        except OSError:
            pass

    # ── Upload ───────────────────────────────────────────────────────────────

    def can_upload(self) -> bool:
        """True when a transfer could start right now (connected and idle).

        Public so callers that did not open the file dialog — the CAM page
        handing over freshly generated G-code — can check before offering to
        send, instead of silently doing nothing.
        """
        return bool(self._conn) and not self._upload_row \
            and not self._download_row_name

    def _pick_upload(self) -> None:
        if not self.can_upload():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload G-code file", "", "G-code files (*.nc *.gcode *.txt)")
        if not path:
            return
        self.upload_file(path)

    def upload_file(self, path: str) -> bool:
        """Start an upload for an already-chosen *path*.

        Split out of :meth:`_pick_upload` so the transfer path can be driven
        programmatically — the CAM page uses it to send an exported toolpath
        without making the user re-pick the file they just generated.
        Returns True once the upload has started, False if a transfer is
        already in flight or there is no machine, and None if the name is
        already on the machine and the user cancelled.
        """
        if not self.can_upload():
            return False
        name = os.path.basename(path)
        if not name.endswith(".nc"):
            name = os.path.splitext(name)[0] + ".nc"
        # Same name already on the machine (or just uploaded and still being
        # processed there): replace it, keep both, or cancel. Compared
        # case-insensitively, as the SD card's FAT file system does.
        taken = {n.lower() for n in
                 {f.get("fileName", "") for f in self._files} | self._processing}
        if name.lower() in taken:
            from ui.pages.dialogs import FileExistsDialog
            alt = _free_name(name, taken)
            dlg = FileExistsDialog(name, alt, parent=self)
            if not dlg.exec():
                return None
            if dlg.choice == "keep":
                name = alt
        # A rotary program that re-zeroes A at its end must not move A0 for the
        # next file -- see ui/rotary_fix.py. The corrected copy is what gets
        # uploaded; the file on disk is never touched.
        from ui import rotary_fix
        path, a_fixes = rotary_fix.fixed_copy(path)
        if a_fixes:
            self._tell_rotary_fix(os.path.basename(path), a_fixes)
        try:
            with open(path) as fh:
                self._upload_lines_total = sum(1 for ln in fh if ln.strip())
        except OSError:
            self._upload_lines_total = 0
        # Synthetic in-list row showing the upload progress (per the design).
        # It goes at the top of the list, scrolled into view, so the progress
        # is visible however many files the machine already holds.
        self._upload_row = FileRow({"fileName": name}, self,
                                   transfer_label="Uploading")
        self._rows.insert(0, self._upload_row)
        self._list_lay.insertWidget(0, self._upload_row)
        self._scroll.verticalScrollBar().setValue(0)
        self._conn.start_transfer(path, name)
        return True

    def _tell_rotary_fix(self, name: str, fixes: list) -> None:
        """Say that the uploaded copy's A re-zero was corrected, and why."""
        from ui.pages.dialogs import InfoDialog
        _n, old, new = fixes[0]
        more = f" (and {len(fixes) - 1} more)" if len(fixes) > 1 else ""
        dlg = InfoDialog(
            "Rotary A-zero corrected",
            f"{name} ends by re-zeroing A with \"{old}\"{more}. The chuck is "
            "not on a whole turn there, so that line would have moved your A0 "
            "and the next file would cut at the wrong angle.\n\n"
            f"The copy sent to the machine uses \"{new}\" instead: the extra "
            "turns are still cleared, and A0 stays where you set it. Your "
            "file on disk is unchanged.",
            parent=self, success=True)
        dlg.open()

    def _end_upload_row(self) -> None:
        if self._upload_row is not None:
            self._park_focus()
            self._list_lay.removeWidget(self._upload_row)
            self._upload_row.setParent(None)
            self._upload_row.deleteLater()
            if self._upload_row in self._rows:
                self._rows.remove(self._upload_row)
            self._upload_row = None
            self._refresh_rows()
            # Re-enable the upload / clear-cache buttons now that the upload row
            # is gone — the gate keys on `_upload_row is None`, so without this
            # the upload button stays disabled after a successful upload.
            self._sync_gates()

    def _end_download_row(self) -> None:
        if self._download_row_name is not None:
            row = next((r for r in self._rows
                        if r.name == self._download_row_name), None)
            if row is not None:
                row.clear_transfer()
            self._download_row_name = None

    def _on_transfer_progress(self, lines_sent: int) -> None:
        if self._upload_row is None:
            return
        if self._upload_lines_total > 0:
            pct = min(99, round(lines_sent * 100 / self._upload_lines_total))
        else:
            pct = min(99, lines_sent // 10)
        self._upload_row.set_transfer_progress(pct)

    def _on_transfer_finalizing(self) -> None:
        # Everything is sent; the machine now copies and hashes the file
        # before it answers. Busy bar, and no cancel: it cannot act on one.
        if self._upload_row is not None:
            self._upload_row.set_processing()

    def _on_transfer_done(self) -> None:
        if self._upload_row is not None:
            # Only a finished upload gets the bar filled. `transfer_done` also
            # fires for a user abort and for a machine rejection, and snapping
            # to 100% there would read as "sent" for a file that was refused
            # and never stored (the popup says why — connection._fail_transfer).
            if self._conn is None or self._conn.last_transfer_ok:
                self._upload_row.set_transfer_progress(100)
                # Marked as processing now, not when the placeholder replaces
                # the upload row 600 ms later: the first file list after the
                # upload can arrive inside those 600 ms, and later identical
                # lists are not re-emitted (connection dedupes them), so a
                # name added after it would never be cleared.
                name = self._upload_row.name
                self._processing.add(name)
                QTimer.singleShot(600, lambda n=name: self._start_processing(n))
            else:
                QTimer.singleShot(600, self._end_upload_row)

    #: Give up on a placeholder whose file never shows up in the machine's list
    _PROCESSING_TIMEOUT_MS = 120_000

    def _start_processing(self, name: str) -> None:
        """Swap the finished upload row for a 'processing' placeholder that
        stays until the machine lists the file (see _on_file_list).

        *name* is already in ``_processing`` (set by _on_transfer_done), or
        already gone from it if the machine listed the file in the meantime;
        either way the rows are rebuilt from that state here."""
        QTimer.singleShot(self._PROCESSING_TIMEOUT_MS,
                          lambda: self._stop_processing(name))
        self._end_upload_row()

    def _stop_processing(self, name: str) -> None:
        if name in self._processing:
            self._processing.discard(name)
            self._refresh_rows()

    def _on_download_progress(self, name: str, pct: int) -> None:
        if name == self._download_row_name:
            row = next((r for r in self._rows if r.name == name), None)
            if row is not None:
                row.set_transfer_progress(pct)

    def _on_download_done(self, name: str, path: str) -> None:
        if name == self._download_row_name:
            QTimer.singleShot(400, self._end_download_row)
            if self._download_to_user and path:
                self._save_to_downloads(name, path)
            self._download_to_user = False
        if name == self._pending_visualize:
            self._pending_visualize = None
            if path:
                self._open_ply_viewer(name, path)

    def _save_to_downloads(self, name: str, cache_path: str) -> None:
        """Copy the downloaded file into ~/Downloads (asking before overwriting)
        and confirm with a popup."""
        import shutil
        from ui.pages.dialogs import InfoDialog, ConfirmDialog
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        dest = os.path.join(downloads, name)
        if os.path.exists(dest):
            dlg = ConfirmDialog(
                "File already exists",
                f"{name} already exists in your Downloads folder.\n"
                "Do you want to overwrite it?", parent=self)
            if not dlg.exec():
                return
        try:
            os.makedirs(downloads, exist_ok=True)
            shutil.copyfile(cache_path, dest)
            # The cached copy is deliberately byte-exact with the file on the
            # machine's SD card — the sha256 integrity check in
            # Connection._download_chunk compares against the firmware's stored
            # hash, so it must not be rewritten. The machine stores bare LFs,
            # so the copy the user carries away is fixed here instead, where
            # nothing depends on its bytes any more.
            from connection import normalize_crlf
            normalize_crlf(dest)
        except OSError as exc:
            InfoDialog("Download failed",
                       f"Could not save {name}:\n{exc}",
                       parent=self, success=False).exec()
            return
        InfoDialog("Download complete",
                   f"{name} has been saved to your Downloads folder:\n{dest}",
                   parent=self).exec()

    # ── Responsive metadata columns ──────────────────────────────────────────

    def set_available_width(self, w: int) -> None:
        """Called by the main window with the scroll viewport width, so the
        page can compact BEFORE its own minimum forces a horizontal scroll."""
        self._avail_w = w
        self._apply_responsive()

    def _compact_level(self) -> int:
        w = getattr(self, "_avail_w", 0) or self.width()
        if w >= 950:
            return 0          # size + est.time + dim
        if w >= 815:
            return 1          # size + est.time
        if w >= 650:
            return 2          # size only
        return 3              # name + date only

    def _apply_responsive(self) -> None:
        level = self._compact_level()
        for r in self._rows:
            r.set_compact(level)
        # Compact the header too: icon-only sort / select-all buttons
        compact_head = level >= 2
        self._sort_b.setText("" if compact_head else "  Sort")
        self._sel_b.setText("" if compact_head else "  Select all")
        self._multi_b.setText("" if compact_head else "  Multi select")
        for b in (self._sort_b, self._sel_b, self._multi_b):
            b.setMinimumWidth(40 if compact_head else 96)
        for i in range(2):
            self._type_group.button(i).setMinimumWidth(
                56 if compact_head else 86)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_responsive()

    # ── Wiring ───────────────────────────────────────────────────────────────

    def wire(self, conn) -> None:
        self._conn = conn
        conn.file_list_changed.connect(self._on_file_list)
        conn.transfer_progress.connect(self._on_transfer_progress)
        conn.transfer_finalizing.connect(self._on_transfer_finalizing)
        conn.transfer_done.connect(self._on_transfer_done)
        conn.download_progress.connect(self._on_download_progress)
        conn.download_done.connect(self._on_download_done)
        conn.opened_file_changed.connect(self._on_opened_file)
        # Run-state gating (Android FilesTab parity, App.tsx ~5464)
        conn.machine_status_changed.connect(self._on_status_for_gates)
        conn.machine_flags_changed.connect(lambda *_: self._sync_gates())
        conn.serial_state_changed.connect(lambda *_: self._sync_gates())
        conn.link_changed.connect(lambda *_: self._sync_gates())
        # Transfers gate the upload / clear-cache buttons too — including
        # downloads started elsewhere (live preview cache sync).
        conn.transfer_progress.connect(lambda *_: self._sync_gates())
        conn.transfer_done.connect(lambda *_: self._sync_gates())
        conn.download_progress.connect(lambda *_: self._sync_gates())
        conn.download_done.connect(lambda *_: self._sync_gates())
        self._sync_gates()

    # ── Run-state gating ─────────────────────────────────────────────────────

    def _on_status_for_gates(self, status: str) -> None:
        self._machine_status = str(status).split(":")[0].strip().lower()
        self._sync_gates()

    def _machine_locked(self) -> bool:
        """File interaction is blocked while a job runs or holds, while homing,
        and while a guided procedure is busy (homing counts as motion)."""
        flags = self._conn.flags() if self._conn else {}
        return (bool(flags.get("procedureBusy", False))
                or self._machine_status in ("run", "hold", "home"))

    def _menu_locked(self) -> bool:
        """The per-file ⋮ menu additionally stays usable during Hold."""
        flags = self._conn.flags() if self._conn else {}
        return (bool(flags.get("procedureBusy", False))
                or self._machine_status in ("run", "home"))

    def _machine_idle_for_ply(self) -> bool:
        flags = self._conn.flags() if self._conn else {}
        return (not flags.get("procedureBusy", False)
                and self._machine_status not in
                ("run", "hold", "alarm", "home", "jog"))

    def _machine_moving(self) -> bool:
        """The machine is doing something — a job (running/held), manual motion
        (jog), homing, or a guided procedure. Upload / clear-cache are blocked
        then (homing counts as motion)."""
        flags = self._conn.flags() if self._conn else {}
        return (bool(flags.get("procedureBusy", False))
                or bool(flags.get("jobRunning", False))
                or bool(flags.get("jobPaused", False))
                or self._machine_status in ("run", "hold", "jog", "home"))

    def _sync_gates(self) -> None:
        locked = self._machine_locked()
        conn = self._conn
        connected = bool(conn and (conn.is_serial_connected
                                   or conn.is_offline_link))
        transferring = bool(conn and (conn.is_transfer_active
                                      or conn.is_downloading))
        moving = self._machine_moving()
        self._search.setEnabled(not locked)
        self._sort_b.setEnabled(not locked)
        self._sel_b.setEnabled(not locked)
        self._multi_b.setEnabled(not locked)
        # Upload / clear-cache are also blocked during a transfer and while the
        # machine is moving (job or manual jog).
        self._upload_btn.setEnabled(connected and not moving
                                    and not transferring
                                    and self._upload_row is None)
        self._clear_cache_btn.setEnabled(not transferring and not moving)
        # Dim every file row (and swap to a not-allowed cursor) while selection
        # is locked, so it's evident the list isn't interactive. The upload row
        # (an active transfer) is never locked — that only happens at idle.
        for row in self._rows:
            row.set_locked(locked and row is not self._upload_row)
        if locked and self._select_mode:
            self._exit_select_mode()

    def _on_opened_file(self, name) -> None:
        self._opened_name = name
        for row in self._rows:
            row.set_opened(row.name == name)
