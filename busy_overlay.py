# -*- coding: utf-8 -*-
"""A "processing" veil for long-running tab operations.

The EIL pipeline runs on worker threads, so the tab stays responsive while it
works and nothing on screen says the plugin is busy -- users re-click Run or
change inputs mid-run. `BusyOverlay` covers a panel with a translucent card,
an animated spinner and a stage message, and swallows the mouse and keyboard
events aimed at whatever sits underneath it.

The overlay is only the visual half: callers also disable the widgets they want
locked, so blocking survives keyboard focus traversal. Parent the overlay to a
widget that stays enabled (a scroll area's viewport) rather than to the one
being disabled -- a disabled parent disables its children, overlay included.
"""

from qgis.PyQt import QtCore, QtGui, QtWidgets

SPINNER_INTERVAL_MS = 45
SPINNER_LINE_COUNT = 12
SPINNER_RADIUS_PX = 20
SPINNER_LINE_LENGTH_PX = 9
SPINNER_LINE_WIDTH_PX = 4

CARD_WIDTH_PX = 320
CARD_HEIGHT_PX = 168
CARD_RADIUS_PX = 8
CARD_PADDING_PX = 18

VEIL_COLOR = QtGui.QColor(247, 247, 247, 218)
CARD_COLOR = QtGui.QColor(255, 255, 255)
CARD_BORDER_COLOR = QtGui.QColor(179, 179, 179)
ACCENT_COLOR = QtGui.QColor(0, 122, 255)
TITLE_COLOR = QtGui.QColor(26, 26, 26)
HINT_COLOR = QtGui.QColor(119, 119, 119)


class BusyOverlay(QtWidgets.QWidget):
    """Translucent spinner veil pinned to the full rect of its parent widget."""

    def __init__(self, host):
        super(BusyOverlay, self).__init__(host)
        self._angle_step = 0
        self._title = ""
        self._hint = ""

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(SPINNER_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_spinner)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.WaitCursor)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)

        host.installEventFilter(self)
        self.hide()

    # -- public API ----------------------------------------------------

    @property
    def is_running(self):
        return self._timer.isActive()

    def start(self, title, hint=""):
        """Show the veil and begin animating. Safe to call when already shown."""
        self._title = title
        self._hint = hint
        self._sync_geometry()
        self.show()
        self.raise_()
        self.setFocus(QtCore.Qt.OtherFocusReason)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def set_stage(self, title, hint=""):
        """Swap the message without interrupting the animation."""
        self._title = title
        self._hint = hint
        self.update()

    def stop(self):
        self._timer.stop()
        self.hide()

    # -- geometry ------------------------------------------------------

    def eventFilter(self, source, event):
        if source is self.parentWidget() and event.type() in (
            QtCore.QEvent.Resize,
            QtCore.QEvent.Show,
            QtCore.QEvent.LayoutRequest,
        ):
            self._sync_geometry()
        return False

    def _sync_geometry(self):
        host = self.parentWidget()
        if host is not None:
            self.setGeometry(host.rect())

    # -- input blocking ------------------------------------------------
    # The veil is opaque to the mouse by default, but wheel and key events
    # propagate to the parent unless they are explicitly accepted here.

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        event.accept()

    # -- painting ------------------------------------------------------

    def _advance_spinner(self):
        self._angle_step = (self._angle_step + 1) % SPINNER_LINE_COUNT
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), VEIL_COLOR)

        card = QtCore.QRect(0, 0, CARD_WIDTH_PX, CARD_HEIGHT_PX)
        card.moveCenter(self.rect().center())
        # A narrow panel would otherwise have the card spill past both edges.
        if card.width() > self.width() - 2 * CARD_PADDING_PX:
            card.setLeft(CARD_PADDING_PX)
            card.setRight(max(CARD_PADDING_PX + 1, self.width() - CARD_PADDING_PX))

        painter.setPen(QtGui.QPen(CARD_BORDER_COLOR, 1))
        painter.setBrush(QtGui.QBrush(CARD_COLOR))
        painter.drawRoundedRect(
            QtCore.QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5),
            CARD_RADIUS_PX, CARD_RADIUS_PX,
        )

        spinner_center = QtCore.QPoint(
            card.center().x(), card.top() + CARD_PADDING_PX + SPINNER_RADIUS_PX
        )
        self._paint_spinner(painter, spinner_center)

        text_top = spinner_center.y() + SPINNER_RADIUS_PX + 12
        text_rect = QtCore.QRect(
            card.left() + CARD_PADDING_PX,
            text_top,
            card.width() - 2 * CARD_PADDING_PX,
            card.bottom() - text_top - CARD_PADDING_PX // 2,
        )
        self._paint_text(painter, text_rect)
        painter.end()

    def _paint_spinner(self, painter, center):
        painter.save()
        painter.translate(center)
        painter.setPen(QtCore.Qt.NoPen)
        for index in range(SPINNER_LINE_COUNT):
            # Trailing segments fade out, which reads as rotation.
            distance = (index - self._angle_step) % SPINNER_LINE_COUNT
            alpha = int(255 * (1.0 - distance / float(SPINNER_LINE_COUNT)) ** 2)
            color = QtGui.QColor(ACCENT_COLOR)
            color.setAlpha(max(28, alpha))

            painter.save()
            painter.rotate(360.0 * index / SPINNER_LINE_COUNT)
            painter.setBrush(QtGui.QBrush(color))
            painter.drawRoundedRect(
                QtCore.QRectF(
                    -SPINNER_LINE_WIDTH_PX / 2.0,
                    -SPINNER_RADIUS_PX,
                    SPINNER_LINE_WIDTH_PX,
                    SPINNER_LINE_LENGTH_PX,
                ),
                SPINNER_LINE_WIDTH_PX / 2.0,
                SPINNER_LINE_WIDTH_PX / 2.0,
            )
            painter.restore()
        painter.restore()

    def _paint_text(self, painter, rect):
        title_font = QtGui.QFont(self.font())
        title_font.setBold(True)
        title_font.setPointSize(max(9, title_font.pointSize() + 1))
        painter.setFont(title_font)
        painter.setPen(QtGui.QPen(TITLE_COLOR))

        metrics = QtGui.QFontMetrics(title_font)
        title_height = metrics.height() + 2
        title_rect = QtCore.QRect(rect.left(), rect.top(), rect.width(), title_height)
        painter.drawText(
            title_rect,
            QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
            metrics.elidedText(self._title, QtCore.Qt.ElideRight, rect.width()),
        )

        if not self._hint:
            return

        hint_font = QtGui.QFont(self.font())
        hint_font.setPointSize(max(8, hint_font.pointSize() - 1))
        painter.setFont(hint_font)
        painter.setPen(QtGui.QPen(HINT_COLOR))
        painter.drawText(
            QtCore.QRect(
                rect.left(),
                rect.top() + title_height + 4,
                rect.width(),
                rect.height() - title_height - 4,
            ),
            QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap,
            self._hint,
        )
