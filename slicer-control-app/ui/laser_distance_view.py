"""
ui/laser_distance_view.py
The distance sensor's live reading, on its own.

Why
---
The laser is the input to edge finding, centre detection and the height map, and
when one of those comes out wrong the first question is whether the sensor is
reading sensibly at all. Until now the only way to ask was to start a procedure
that uses it and infer the answer from the result — which moves the machine, and
answers a different question.

This asks it directly: `distanceLaserMm`, every 300 ms, with nothing else in the
way. It moves nothing.

Why a trace and not just a number
---------------------------------
A single number tells you the sensor answers. It does not tell you whether the
answer is *steady*, and "the reading wanders by half a millimetre" is the fault
that ruins a height map while looking fine in any one sample. So the last two
minutes are drawn as a strip under the reading, with the spread written next to
it. Noise is a shape, and this is the smallest thing that shows a shape.

*reset graph* throws the history away, because a trace that spans two different
surfaces has a spread describing neither — after moving the head, the old
samples are not evidence about the new spot.

What the errors mean
--------------------
The firmware distinguishes three, and they want different reactions, so they are
not flattened into "sensor error" (README 7.25):

    distanceLaserSensorNotInitialized   the enable pin is high but the warm-up
                                        has not finished — wait, do not act
    distanceLaserReadFailed             the Modbus read itself failed — a wiring
                                        or baud problem, not a surface problem
    distanceLaserOutOfRange             raw 32767: nothing in range, or a
                                        surface the laser cannot see

The last is the interesting one when a scan misbehaves: it is what a hole, a
dark surface or a too-distant bed looks like from here. It is reported as a
reading state rather than a failure, because the sensor is working perfectly and
saying so.

`laserSensorEnabled` is only the enable pin
-------------------------------------------
The button that opens this is gated on that flag, which the firmware is explicit
about: it "confirms only that the enable pin is HIGH", and can be true before
initialization completes. So an opening dialog that reports
`distanceLaserSensorNotInitialized` for a second or two is not a fault, and it
says "warming up" rather than crying error.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QCursor, QPolygonF, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
)

from ui.theme import WHITE, TXT, TXT2, TXT3, BORDER, PANEL, RUN_C, WARN_C, ERR_C
from ui.scale import px, bind, font

#: The poll interval. Fast enough to look live, slow enough that a Modbus read
#: over a 9600-baud soft UART is comfortably finished before the next one.
POLL_MS = 300

#: Samples kept for the trace: two minutes at the poll rate.
HISTORY = 400

#: After this long with no reply of any kind, say so rather than leaving the
#: last number sitting there looking current.
STALE_MS = 2500

#: Plain wording for the three failures the firmware distinguishes.
_ERRORS = {
    "distanceLaserSensorNotInitialized":
        ("Warming up…", "The sensor is enabled but has not finished "
                        "initialising. This clears on its own.", WARN_C),
    "distanceLaserReadFailed":
        ("Read failed", "The sensor did not answer over its serial link. "
                        "Check the wiring rather than the surface.", ERR_C),
    "distanceLaserOutOfRange":
        ("Out of range", "Nothing within range, or a surface the laser cannot "
                         "read — dark, shiny or too far.", WARN_C),
}

_TRACE_BG = QColor("#0B0B0B")
_TRACE_LINE = QColor("#6EC9E2")
_TRACE_GRID = QColor("#26262A")


class _Trace(QWidget):
    """The recent readings, as a strip. Autoscaled to what it holds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vals: list[float] = []
        self.setMinimumHeight(px(90))

    def add(self, mm: float) -> None:
        self._vals.append(float(mm))
        if len(self._vals) > HISTORY:
            del self._vals[:len(self._vals) - HISTORY]
        self.update()

    def clear(self) -> None:
        self._vals.clear()
        self.update()

    @property
    def spread(self) -> tuple[float, float, float] | None:
        """(min, max, max-min) over what is held, or None while empty."""
        if not self._vals:
            return None
        lo, hi = min(self._vals), max(self._vals)
        return lo, hi, hi - lo

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(r, _TRACE_BG)
        p.setPen(QPen(_TRACE_GRID, 1))
        for f in (0.25, 0.5, 0.75):
            y = r.top() + r.height() * f
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if len(self._vals) < 2:
            p.setPen(QPen(QColor("#777")))
            p.setFont(font(10))
            p.drawText(r, Qt.AlignCenter, "Waiting for readings…")
            p.end()
            return
        lo, hi, rng = self.spread
        # A flat trace is the good case; give it a band so it draws as a line
        # through the middle rather than jumping on the last decimal.
        if rng < 1e-6:
            lo, hi = lo - 0.5, hi + 0.5
            rng = hi - lo
        pad = px(6)
        n = len(self._vals)
        step = (r.width() - 2 * pad) / max(n - 1, 1)
        pts = []
        for i, v in enumerate(self._vals):
            t = (v - lo) / rng
            pts.append(QPointF(r.left() + pad + i * step,
                               r.bottom() - pad - t * (r.height() - 2 * pad)))
        p.setPen(QPen(_TRACE_LINE, max(1, px(2))))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        p.setPen(QPen(QColor(TXT3)))
        p.setFont(font(9))
        p.drawText(QRectF(r.left() + pad, r.top() + px(2), r.width(), px(14)),
                   Qt.AlignLeft, "%.2f mm" % hi)
        p.drawText(QRectF(r.left() + pad, r.bottom() - px(16), r.width(), px(14)),
                   Qt.AlignLeft, "%.2f mm" % lo)
        p.end()


class LaserDistanceDialog(QDialog):
    """Live `distanceLaserMm`, polled. Read-only: it moves nothing."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._last_mm: float | None = None
        self._samples = 0
        self._errors = 0

        self.setWindowTitle("Laser distance")
        self.setModal(False)
        self.resize(px(420), px(360))

        self._root = QVBoxLayout(self)

        head = QHBoxLayout()
        self._title = QLabel("Laser distance")
        head.addWidget(self._title)
        head.addStretch()
        self._clear_b = QPushButton("Reset graph")
        self._clear_b.setToolTip(
            "Throw away the readings collected so far and start the trace "
            "again. Useful after moving the head: the spread of a trace that "
            "spans two different surfaces describes neither.")
        self._clear_b.clicked.connect(self._reset_graph)
        head.addWidget(self._clear_b)
        self._close_b = QPushButton("Close")
        self._close_b.clicked.connect(self.close)
        head.addWidget(self._close_b)
        self._root.addLayout(head)

        self._value = QLabel("—")
        self._value.setAlignment(Qt.AlignCenter)
        self._root.addWidget(self._value)

        self._state = QLabel("Waiting for the first reading…")
        self._state.setAlignment(Qt.AlignCenter)
        self._state.setWordWrap(True)
        self._root.addWidget(self._state)

        self._trace = _Trace()
        self._root.addWidget(self._trace, 1)

        self._stats = QLabel("")
        self._stats.setAlignment(Qt.AlignCenter)
        self._root.addWidget(self._stats)

        bind(self, self._apply_scale)

        conn.laser_distance_changed.connect(self._on_reading)
        conn.laser_distance_error.connect(self._on_error)

        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._ask)
        self._poll.start()

        self._stale = QTimer(self)
        self._stale.setSingleShot(True)
        self._stale.setInterval(STALE_MS)
        self._stale.timeout.connect(self._on_stale)
        self._ask()

    # ── polling ─────────────────────────────────────────────────────────────

    def _ask(self) -> None:
        self._conn.get("distanceLaserMm")
        self._stale.start()

    def _on_reading(self, mm: float) -> None:
        self._stale.start()
        self._last_mm = float(mm)
        self._samples += 1
        self._trace.add(self._last_mm)
        self._value.setText("%.2f mm" % self._last_mm)
        self._value.setStyleSheet(f"color: {TXT}; background: transparent;")
        self._state.setText("Reading")
        self._state.setStyleSheet(f"color: {RUN_C}; background: transparent;")
        self._update_stats()

    def _on_error(self, error: str) -> None:
        self._stale.start()
        self._errors += 1
        title, why, colour = _ERRORS.get(
            error, ("Sensor error", error, ERR_C))
        # The value is left showing the last good reading, greyed: blanking it
        # would lose the number the operator was looking at the moment a single
        # dropped sample arrived.
        self._value.setStyleSheet(f"color: {TXT3}; background: transparent;")
        self._state.setText("%s — %s" % (title, why))
        self._state.setStyleSheet(f"color: {colour}; background: transparent;")
        self._update_stats()

    def _on_stale(self) -> None:
        self._value.setStyleSheet(f"color: {TXT3}; background: transparent;")
        self._state.setText("No answer from the machine.")
        self._state.setStyleSheet(f"color: {ERR_C}; background: transparent;")

    def _reset_graph(self) -> None:
        """Start the trace over.

        The counters go with it. They describe the trace -- "61 readings,
        spread 0.04 mm" -- so leaving the count running while the graph starts
        empty would put a number on screen that nothing on screen supports.
        The live reading itself is left alone: it is the sensor's answer now,
        not part of the history.
        """
        self._trace.clear()
        self._samples = 0
        self._errors = 0
        self._update_stats()

    def _update_stats(self) -> None:
        sp = self._trace.spread
        bits = ["%d readings" % self._samples]
        if sp is not None and self._samples > 1:
            bits.append("spread %.2f mm (%.2f – %.2f)" % (sp[2], sp[0], sp[1]))
        if self._errors:
            bits.append("%d error%s" % (self._errors,
                                        "" if self._errors == 1 else "s"))
        self._stats.setText("  ·  ".join(bits))

    # ── housekeeping ────────────────────────────────────────────────────────

    def closeEvent(self, e):
        self._poll.stop()
        self._stale.stop()
        for sig, slot in ((self._conn.laser_distance_changed, self._on_reading),
                          (self._conn.laser_distance_error, self._on_error)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(e)

    def _apply_scale(self, *_):
        self.setStyleSheet(f"QDialog {{ background: {PANEL}; }}")
        self._title.setFont(font(15, bold=True))
        self._title.setStyleSheet(f"color: {TXT}; background: transparent;")
        self._value.setFont(font(38, bold=True))
        self._state.setFont(font(11))
        self._stats.setFont(font(10))
        self._stats.setStyleSheet(f"color: {TXT3}; background: transparent;")
        self._root.setContentsMargins(px(16), px(16), px(16), px(16))
        self._root.setSpacing(px(8))
        for b in (self._clear_b, self._close_b):
            b.setFont(font(10))
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {WHITE}; color: {TXT};
                    border: 1px solid {BORDER}; border-radius: {px(6)}px;
                    padding: {px(5)}px {px(12)}px;
                }}
                QPushButton:hover {{ background: #E8E8E8; }}
            """)
