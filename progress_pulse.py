# -*- coding: utf-8 -*-
"""A shimmer that sweeps across a QProgressBar while a long task runs.

Pipeline stages can sit on one value for minutes (GRASS tiles, TauDEM), and a
frozen bar reads as a hung plugin. `ProgressPulse` overlays the bar and, once
per period, glides a soft highlight across its filled part -- or across the
whole track while nothing is filled yet. It is click-through and only paints;
the bar's value and text are untouched. Timing lives in common/pulse.py.
"""

from qgis.PyQt import QtCore, QtGui, QtWidgets

from .common import pulse

FRAME_INTERVAL_MS = 33
CORNER_RADIUS_PX = 3
TRACK_INSET_PX = 1

# Over the filled chunk: a white sheen. Over an empty track: a faint accent.
SHEEN_COLOR = QtGui.QColor(255, 255, 255, 120)
TRACK_COLOR = QtGui.QColor(0, 122, 255, 70)


class ProgressPulse(QtWidgets.QWidget):
    """Click-through shimmer pinned to the full rect of a QProgressBar."""

    def __init__(self, bar):
        super(ProgressPulse, self).__init__(bar)
        self._bar = bar
        self._clock = QtCore.QElapsedTimer()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self.update)

        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        bar.installEventFilter(self)
        self.hide()

    def start(self):
        """Begin pulsing. Safe to call when already running."""
        if self._timer.isActive():
            return
        self.setGeometry(self._bar.rect())
        self._clock.start()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def eventFilter(self, source, event):
        if source is self._bar and event.type() == QtCore.QEvent.Resize:
            self.setGeometry(self._bar.rect())
        return False

    def _filled_width(self, track):
        bar = self._bar
        span = bar.maximum() - bar.minimum()
        if span <= 0:
            return 0
        return track.width() * (bar.value() - bar.minimum()) / span

    def paintEvent(self, event):
        fraction = pulse.sweep_fraction(self._clock.elapsed())
        if fraction is None:
            return

        track = QtCore.QRectF(self.rect()).adjusted(
            TRACK_INSET_PX, TRACK_INSET_PX, -TRACK_INSET_PX, -TRACK_INSET_PX
        )
        filled = self._filled_width(track)
        if filled >= pulse.MIN_BAND_PX:
            area = QtCore.QRectF(track.left(), track.top(), filled, track.height())
            color = SHEEN_COLOR
        else:
            area, color = track, TRACK_COLOR

        left, width = pulse.band(fraction, area.width())
        start = area.left() + left
        gradient = QtGui.QLinearGradient(start, 0, start + width, 0)
        clear = QtGui.QColor(color)
        clear.setAlpha(0)
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, color)
        gradient.setColorAt(1.0, clear)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(area, CORNER_RADIUS_PX, CORNER_RADIUS_PX)
        painter.setClipPath(clip)
        painter.fillRect(area, QtGui.QBrush(gradient))
        painter.end()
