"""
ui/pages/facing_dialog.py
The facing popup: collect a face size, a depth, two step sizes and a tool
diameter, show what that will actually cut, and hand the finished program over
to be uploaded.

Division of labour
------------------
``ui/facing.py`` owns the toolpath and knows nothing about Qt; this file owns
the form and knows nothing about G-code beyond asking for it. The dialog never
writes or sends the file either -- it emits :attr:`FacingDialog.job_ready` with
the program text and a name, and ``MainWindow`` stages it in the CAM export
folder and puts it through the upload queue that CAM exports already use. That
way the facing job gets the same things a CAM export gets for free: the "no
machine / transfer busy" explanation, the name-collision prompt, the progress
row on the Files page, and the staged copy being deleted once it is safely on
the machine.

Nothing is computed twice
-------------------------
The summary line under the form, the warnings, and the program itself all come
from the same ``facing.summary`` / ``facing.validate`` calls on the same
``FacingJob``, so the "18 passes, 2 levels, about 4 min" the operator agreed to
cannot describe a different program from the one that gets sent.

Why the corner buttons are a 2x2 grid
-------------------------------------
The start corner is the one input where the layout *is* the explanation: a
button in the top-left of a square block means the top-left corner of the
stock, and no label has to say which way Y points. A list of four radio
buttons reading "bottom left / bottom right / top left / top right" makes the
operator translate words into a direction while standing at the machine, which
is exactly the moment to not make them do that.

The values are remembered (QSettings, like the Controls page's own
preferences). Facing is a repeated operation -- the same cutter, the same
material, often the same stock -- and retyping six numbers to skim a second
piece is pure friction.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
    QPushButton, QButtonGroup, QSizePolicy,
)

from ui.theme import WHITE, LIGHT, BORDER, TXT, TXT2, TXT3, ERR_C
from ui.widgets import lbl
from ui.scale import fs, px, autoscale
from ui import facing
from ui.pages.dialogs import FlowDialog, _section_label, _warning_row

#: Where the form's values are kept between runs.
_PREFIX = "facing/"

#: The size form, column by column. Each column is one idea -- the rectangle,
#: how deep and in what bites, the cutter and how far it steps -- so a column
#: can be read on its own. Units are stated once in the section header instead
#: of six times in the grid.
_SIZE_COLUMNS = (
    (("width",       "Face X",    "80"),
     ("depth",       "Face Y",    "50")),
    (("total_depth", "Cut depth", "0.5"),
     ("stepdown",    "Stepdown",  "0.3")),
    (("tool_dia",    "Tool \u2300",  "6"),
     ("stepover",    "Stepover",  "3")),
)

#: Feeds carry their units in the label: they differ from each other, so they
#: cannot be hoisted into the section header the way the millimetres are, and
#: three "Plunge [200] mm/min" columns side by side need half again the width
#: of the card.
_FEEDS = (
    ("feed",   "Feed (mm/min)",    "800"),
    ("plunge", "Plunge (mm/min)",  "200"),
    ("rpm",    "Spindle (rpm)",    "12000"),
)

#: Every numeric key, for reading the form and saving it.
_ALL_FIELDS = tuple(f[0] for col in _SIZE_COLUMNS for f in col) \
    + tuple(f[0] for f in _FEEDS)


def _mm(v: float) -> float:
    """*v* for display, without a negative zero.

    A face zeroed on its own top-right corner reaches exactly X0, and the sign
    the arithmetic leaves there is -0.0. Printed as "-0.0" in a line the
    operator is reading to check their zero against, that is a coordinate that
    looks wrong and is not.
    """
    return round(v, 1) + 0.0


def _input(text: str) -> QLineEdit:
    """A form field styled like the ones in the probing flows."""
    e = QLineEdit(text)
    e.setAlignment(Qt.AlignRight)
    autoscale(e, lambda x: (
        x.setFixedHeight(px(34)),
        # Without a floor a tight row shrinks these to a couple of characters
        # and the value in them cannot be read, let alone checked. Kept modest
        # so three label+field pairs still fit one row: these hold "0.4" and
        # "80", and the fields that hold more carry the row's stretch.
        x.setMinimumWidth(px(56)),
        x.setStyleSheet(
            f"background: {LIGHT}; border: 1px solid {BORDER}; "
            f"border-radius: {px(8)}px; padding: {px(2)}px {px(8)}px; "
            f"font-size: {fs(14)}px;"),
    ))
    return e


def _choice_button(text: str) -> QPushButton:
    """A checkable button for the corner / direction pickers."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    autoscale(b, lambda x: (
        x.setFixedHeight(px(34)),
        x.setStyleSheet(
            f"QPushButton {{ background: {WHITE}; color: {TXT2}; "
            f"border: 1px solid {BORDER}; border-radius: {px(8)}px; "
            f"font-size: {fs(12)}px; padding: 0 {px(6)}px; }}"
            f"QPushButton:hover {{ background: {LIGHT}; }}"
            f"QPushButton:checked {{ background: {LIGHT}; color: {TXT}; "
            f"border: {px(1.5)}px solid {TXT}; font-weight: bold; }}"),
    ))
    return b


class FacingDialog(FlowDialog):
    """Facing setup, then hand the program over for upload.

    Emits :attr:`job_ready` (program text, file name) and accepts. The caller
    stages and sends it -- see the module docstring.
    """

    #: (gcode text, suggested file name)
    job_ready = Signal(str, str)

    def __init__(self, conn, parent=None):
        super().__init__(parent, width=700)
        self._conn = conn
        self._prefs = QSettings("InMachines", "MiniCNC2")
        self._inputs: dict[str, QLineEdit] = {}
        self._reserve = 0            # set in _build, once a label's font exists
        self._build()

    # ── form ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # The width the wrapped labels below are pinned to. They must be, or
        # they are measured short and drawn clipped -- the same reason
        # _warning_row does it.
        self._text_w = px(self._width) - 2 * px(36) - 2 * px(40)

        # The cap alone cannot keep a program inside the machine: the operator
        # sets zero by hand at the corner of the stock, and every coordinate in
        # the file is measured from there. Saying so here, with the reach the
        # summary prints below, is the difference between an alarm before the
        # job and an alarm half-way through a raster with the tool down.
        warn_row = _warning_row(
            "Zero X, Y and Z at the corner you pick below, with the end mill "
            "on top of the stock \u2014 then check where that zero sits. The "
            "passes reach half the tool past each edge, and a zero too near "
            "the end of the travel trips a hard or soft limit mid-job.",
            dialog_w=self._width)

        # Sizes: three columns of two, so six numbers take two rows.
        size_grid = QWidget()
        g = QGridLayout(size_grid)
        g.setContentsMargins(px(40), 0, px(40), 0)
        g.setHorizontalSpacing(px(10))
        g.setVerticalSpacing(px(8))
        size_labels = []
        for col, fields in enumerate(_SIZE_COLUMNS):
            for row, (key, label, default) in enumerate(fields):
                e = _input(str(self._prefs.value(_PREFIX + key, default)))
                e.textChanged.connect(self._refresh)
                self._inputs[key] = e
                name = lbl(label, sz=13, color=TXT2)
                size_labels.append(name)
                g.addWidget(name, row, col * 2)
                g.addWidget(e, row, col * 2 + 1)
            g.setColumnStretch(col * 2 + 1, 1)
        # Every label column is floored at the widest label actually measured,
        # plus a little slack. Leaving it to the layout clips them: the inputs
        # carry the stretch, so any shortfall comes out of the label, and
        # "Stepdown" and "Cut depth" lost their last letter. Measuring beats a
        # hard-coded width because the font is whatever the platform
        # substitutes, and it squares the three columns up as a bonus.
        label_w = max(w.sizeHint().width() for w in size_labels) + px(6)
        for col in range(len(_SIZE_COLUMNS)):
            g.setColumnMinimumWidth(col * 2, label_w)

        # Start corner: the 2x2 block *is* the label (see module docstring).
        self._corner_group = QButtonGroup(self)
        self._corner_group.setExclusive(True)
        corner_w = QWidget()
        cg = QGridLayout(corner_w)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(px(6))
        saved_corner = str(self._prefs.value(_PREFIX + "corner", "bottom left"))
        # Grid position mirrors the machine: +Y is away from the operator, so
        # the "top" corners go in the grid's top row.
        for idx, (name, row, col) in enumerate((
                ("top left", 0, 0), ("top right", 0, 1),
                ("bottom left", 1, 0), ("bottom right", 1, 1))):
            b = _choice_button(name)
            b.setProperty("corner", name)
            b.setChecked(name == saved_corner)
            self._corner_group.addButton(b, idx)
            cg.addWidget(b, row, col)
        if self._corner_group.checkedButton() is None:
            self._corner_group.button(2).setChecked(True)   # bottom left
        self._corner_group.idToggled.connect(lambda *_: self._refresh())

        # Pass direction, stacked to the same height as the corner block.
        self._dir_group = QButtonGroup(self)
        self._dir_group.setExclusive(True)
        dir_w = QWidget()
        dv = QVBoxLayout(dir_w)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(px(6))
        saved_dir = str(self._prefs.value(_PREFIX + "direction", "horizontal"))
        for idx, (name, label) in enumerate((("horizontal", "Horizontal \u2194"),
                                             ("vertical", "Vertical \u2195"))):
            b = _choice_button(label)
            b.setProperty("direction", name)
            b.setChecked(name == saved_dir)
            self._dir_group.addButton(b, idx)
            dv.addWidget(b)
        if self._dir_group.checkedButton() is None:
            self._dir_group.button(0).setChecked(True)
        self._dir_group.idToggled.connect(lambda *_: self._refresh())

        # Rows of fixed-height controls have nothing to give, so they are held
        # at their hint rather than left shrinkable. Otherwise any shortfall in
        # the card's height arithmetic is taken out of whichever row is most
        # elastic -- here the corner picker, which quietly lost its bottom row
        # while every other row kept its size.
        for fixed in (size_grid, corner_w, dir_w):
            fixed.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        picker = QWidget()
        picker.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        ph = QHBoxLayout(picker)
        ph.setContentsMargins(px(40), 0, px(40), 0)
        ph.setSpacing(px(16))
        for header, body in (("Start corner", corner_w), ("Passes", dir_w)):
            col_lay = QVBoxLayout()
            col_lay.setContentsMargins(0, 0, 0, 0)
            col_lay.setSpacing(px(5))
            col_lay.addWidget(lbl(header, sz=13, color=TXT2))
            col_lay.addWidget(body)
            ph.addLayout(col_lay, 1)

        # Feeds and speed: three across, each value under its labelled unit.
        feed_w = QWidget()
        fg = QGridLayout(feed_w)
        fg.setContentsMargins(px(40), 0, px(40), 0)
        fg.setHorizontalSpacing(px(8))
        fg.setVerticalSpacing(px(8))
        for col, (key, label, default) in enumerate(_FEEDS):
            e = _input(str(self._prefs.value(_PREFIX + key, default)))
            e.textChanged.connect(self._refresh)
            self._inputs[key] = e
            fg.addWidget(lbl(label, sz=12, color=TXT2), 0, col)
            fg.addWidget(e, 1, col)
            fg.setColumnStretch(col, 1)

        # File name, on the same footing as the feeds -- it needs no heading of
        # its own to be understood.
        name_w = QWidget()
        nh = QHBoxLayout(name_w)
        nh.setContentsMargins(px(40), 0, px(40), 0)
        nh.setSpacing(px(8))
        nh.addWidget(lbl("Save as", sz=13, color=TXT2))
        self._name = _input(str(self._prefs.value(_PREFIX + "name", "facing")))
        self._name.setAlignment(Qt.AlignLeft)
        nh.addWidget(self._name, 1)
        nh.addWidget(lbl(".nc", sz=11, color=TXT3))
        for fixed in (feed_w, name_w):
            fixed.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # What this will actually do, and why it cannot be sent if it cannot.
        self._summary_lbl = lbl("", sz=13, color=TXT)
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setFixedWidth(self._text_w)
        summary_row = self._indented(self._summary_lbl)

        self._problem_lbl = lbl("", sz=13, color=ERR_C)
        self._problem_lbl.setWordWrap(True)
        self._problem_lbl.setFixedWidth(self._text_w)
        self._problem_row = self._indented(self._problem_lbl)

        # The summary and the problem list never appear together, and both are
        # written to fit two lines. Reserving two keeps the card a constant
        # height while the operator types: re-measuring on every keystroke
        # makes the dialog jump under the cursor, and leaving it alone strands
        # a band of white where a longer message used to be. A rarer, longer
        # message still grows the card -- it is clipping that would matter.
        # The reserve goes on the labels, never on the block around them: an
        # explicit minimumHeight on a container *replaces* the minimum its
        # layout derived from its contents, so it reads as "you may shrink me
        # to two lines" rather than "keep two lines free", and the card then
        # had 22 px to find somewhere else -- it took them from the corner
        # picker's bottom row.
        self._reserve = QFontMetrics(self._summary_lbl.font()).lineSpacing() * 2
        report = self._stack(summary_row, self._problem_row, spacing=px(8))

        # No big icon: this phase is a form, and the 48 px glyph the procedure
        # flows open with is exactly what pushed the tick button off the bottom
        # of a 1280x800 screen. The title says what the dialog is.
        phase = self.build_phase(
            None, "Facing",
            [warn_row,
             self._stack(_section_label(
                 f"Face and steps — mm, face up to "
                 f"{facing.MAX_FACE_MM:.0f} × {facing.MAX_FACE_MM:.0f}, "
                 f"cut depth up to {facing.MAX_DEPTH_MM:g}"), size_grid,
                         picker),
             self._stack(_section_label("Feeds and speed"), feed_w, name_w),
             report],
            [("✓", self._send, True), ("✕", self.reject, True)],
        )
        # build_phase records the buttons it made, in the order given, so the
        # ✓ is phase_buttons[0]. Searching the widget tree by type would not
        # work here: the corner and direction pickers are QPushButtons too.
        self._ok_btn = self.phase_buttons[0] if self.phase_buttons else None
        # Fill the summary before handing the phase over, not after. set_phase
        # measures the card and sizes the window to it, and an empty summary
        # label measures zero -- so the text arriving afterwards found the
        # window already sized and was taken out of the tallest flexible row
        # instead, which is how the corner picker lost its bottom row.
        self._refresh()
        self.set_phase(phase)

    @staticmethod
    def _stack(*rows: QWidget, spacing: int | None = None) -> QWidget:
        """Several form rows presented to build_phase as one.

        FlowDialog puts px(18) between the rows it is handed, which is right
        for a phase of three or four and comes to most of a screen for a form
        of eleven -- enough that the tick button fell past the bottom of a
        1280x800 display and the card grew a scrollbar. Grouping keeps that
        generous gap *between* sections and a tighter one inside each.
        """
        w = QWidget()
        # Minimum, not the default Preferred: a group of fixed-height rows has
        # nothing to give, and left shrinkable it is where the card takes any
        # shortfall out of -- which is how the corner picker kept losing its
        # bottom row while every other row stayed the size it asked for.
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(px(10) if spacing is None else spacing)
        for r in rows:
            v.addWidget(r)
        return w

    def _indented(self, inner: QWidget) -> QWidget:
        """*inner* in a row with the px(40) side margin the form uses."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(px(40), 0, px(40), 0)
        h.setSpacing(0)
        h.addWidget(inner, 1)
        return w

    # ── reading the form ─────────────────────────────────────────────────

    @staticmethod
    def _num(e: QLineEdit):
        """The field's value, or None if it is not a usable number.

        The finite check is not pedantry: float() accepts "nan" and "inf", and
        a NaN fails every ``> 0`` test in facing.validate() rather than
        tripping it, so without this it would pass validation and be written
        into a program for a machine to run.
        """
        txt = e.text().strip().replace(",", ".")
        try:
            v = float(txt)
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def _corner(self) -> str:
        b = self._corner_group.checkedButton()
        return str(b.property("corner")) if b else "bottom left"

    def _direction(self) -> str:
        b = self._dir_group.checkedButton()
        return str(b.property("direction")) if b else "horizontal"

    def _job(self) -> facing.FacingJob | None:
        """The job the form currently describes, or None if a field is unusable."""
        vals = {}
        for key in _ALL_FIELDS:
            v = self._num(self._inputs[key])
            if v is None:
                return None
            vals[key] = v
        return facing.FacingJob(
            width=vals["width"], depth=vals["depth"],
            total_depth=vals["total_depth"], stepdown=vals["stepdown"],
            stepover=vals["stepover"], tool_dia=vals["tool_dia"],
            corner=self._corner(), direction=self._direction(),
            feed=vals["feed"], plunge=vals["plunge"], rpm=int(vals["rpm"]),
        )

    # ── live feedback ────────────────────────────────────────────────────

    def _refresh(self, *_) -> None:
        job = self._job()

        if job is None:
            self._set_problems(["Every field needs a number."])
            self._wrapped(self._summary_lbl, "")
            return

        problems = facing.validate(job, self._conn.work_area_mm())
        self._set_problems(problems)
        if problems:
            self._wrapped(self._summary_lbl, "")
            return

        s = facing.summary(job)
        axis = "X" if job.direction == "horizontal" else "Y"
        pct = s["stepover"] / job.tool_dia * 100.0
        x0, x1, y0, y1 = s["extents"]
        self._wrapped(
            self._summary_lbl,
            # Non-breaking spaces between a number and its unit: the line
            # wraps, and "0.23" ending a line with "m" starting the next reads
            # as a different number entirely.
            f"{s['passes']} passes along {axis} at {s['stepover']:.2f}\u00a0mm "
            f"({pct:.0f}% of tool), {s['levels']} level(s) at "
            f"{s['stepdown']:.2f}\u00a0mm, {s['cut_mm'] / 1000.0:.2f}\u00a0m, "
            f"{facing.fmt_minutes(s['minutes'])}.\n"
            f"From zero: X {_mm(x0):+.1f} to {_mm(x1):+.1f}, "
            f"Y {_mm(y0):+.1f} to {_mm(y1):+.1f}, down to "
            f"Z {_mm(s['z_min']):+.1f}.")

    def _wrapped(self, label, text: str) -> None:
        """Set a wrapped label's text and re-measure its height.

        A word-wrapped QLabel reports a sizeHint that is a guess at a pleasing
        box, not its height at the width it has been pinned to, so a label
        whose text grows to a second line is measured one line short. These two
        labels are rewritten on every keystroke, and without the re-measure the
        missing line is taken out of whatever sits above them -- it was the
        corner picker, which lost its bottom row.
        """
        label.setText(text)
        # Exactly one of the two is ever non-empty, so flooring the visible one
        # keeps the block a constant height without reserving twice.
        label.setMinimumHeight(
            max(label.heightForWidth(self._text_w), self._reserve) if text else 0)
        # An empty row still costs the layout's spacing on both sides, which
        # left a band of nothing where the summary had been.
        row = label.parentWidget()
        if row is not None:
            row.setVisible(bool(text))

    def _set_problems(self, problems: list) -> None:
        """Show the first problem, and disable sending until there are none.

        Only the first: each message explains *why* the number is wrong, which
        makes it two or three lines, and three fields wrong at once became a
        wall of red tall enough to push the buttons off the bottom of the card.
        The card only gains a scrollbar when it is first shown, so that text
        would have been clipped rather than scrolled -- and clipped is how an
        operator comes to believe the dialog is broken rather than their
        numbers. They are checked in form order, so fixing the one shown brings
        up the next field down.
        """
        self._wrapped(self._problem_lbl, problems[0] if problems else "")
        if self._ok_btn is not None:
            self._ok_btn.setEnabled(not problems)

    # ── sending ──────────────────────────────────────────────────────────

    def _send(self) -> None:
        job = self._job()
        if job is None:
            return
        problems = facing.validate(job, self._conn.work_area_mm())
        if problems:
            self._set_problems(problems)
            return

        self._remember(job)
        name = (self._name.text().strip() or "facing")
        if not name.lower().endswith(".nc"):
            name += ".nc"
        self.job_ready.emit(facing.gcode(job), name)
        self.accept()

    def _remember(self, job: facing.FacingJob) -> None:
        for key in _ALL_FIELDS:
            self._prefs.setValue(_PREFIX + key, self._inputs[key].text().strip())
        self._prefs.setValue(_PREFIX + "corner", job.corner)
        self._prefs.setValue(_PREFIX + "direction", job.direction)
        self._prefs.setValue(_PREFIX + "name", self._name.text().strip() or "facing")
