"""Three resizable columns that reflow to two on narrow screens.

    wide     | primary | secondary | tertiary |
    stacked  | primary | secondary |
             |         | tertiary  |

Columns sit in splitters, so the user can still drag the dividers. Each
column is a QScrollArea that never scrolls sideways: its minimum width tracks
its content's, and the layout switches to the stacked form whenever three
columns would not fit side by side.
"""

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QComboBox, QHBoxLayout, QScrollArea, QSplitter, QWidget

MODE_WIDE = "wide"
MODE_STACKED = "stacked"

# Relative column widths in the wide form: inputs, results, details + log.
WIDE_PROPORTIONS = (4, 3, 3)
STACKED_PROPORTIONS = (1, 1)

# Below this width three columns technically fit but leave the table, chart
# and log too cramped to read, so the narrower form is used.
WIDE_BREAKPOINT = 1200

# Characters a compact combo box reserves; longer entries are elided.
COMPACT_COMBO_CHARS = 12


def compact_combo(combo: QComboBox, min_chars=COMPACT_COMBO_CHARS):
    """Stop `combo` from widening to its longest entry (e.g. a full path).

    Qt sizes a combo box to its contents by default, so one long raster path
    made the whole column scroll sideways. The full text stays reachable: the
    popup elides in the middle (keeping the filename) and the tooltip shows it.
    """
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(min_chars)
    combo.view().setTextElideMode(Qt.ElideMiddle)
    combo.currentTextChanged.connect(combo.setToolTip)
    return combo


class _ContentWidthTracker(QObject):
    """Keeps a scroll area at least as wide as its content's minimum."""

    def __init__(self, area):
        super().__init__(area)
        self._area = area
        area.widget().installEventFilter(self)
        self.update()

    def eventFilter(self, watched, event):
        # Fired whenever the content's layout changes, e.g. when RoiSelector
        # inserts its group box after the form is built.
        if event.type() == QEvent.LayoutRequest:
            self.update()
        return False

    def update(self):
        area = self._area
        content = area.widget().minimumSizeHint().width()
        scrollbar = area.verticalScrollBar().sizeHint().width()
        frame = 2 * area.frameWidth()
        area.setMinimumWidth(content + scrollbar + frame)


def no_horizontal_scroll(area: QScrollArea):
    """Never scroll `area` sideways; widen it to fit its content instead."""
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return _ContentWidthTracker(area)


class ResponsiveColumns(QWidget):
    def __init__(self, primary, secondary, tertiary, parent=None,
                 breakpoint=WIDE_BREAKPOINT):
        super().__init__(parent)
        self._panels = (primary, secondary, tertiary)
        self._breakpoint = breakpoint
        self.mode = None

        self._outer = QSplitter(Qt.Horizontal, self)
        self._stack = QSplitter(Qt.Vertical)
        for splitter in (self._outer, self._stack):
            splitter.setChildrenCollapsible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._outer)

        self._apply(MODE_STACKED)

    def _wide_minimum(self):
        handles = 2 * self._outer.handleWidth()
        return sum(_minimum_width(panel) for panel in self._panels) + handles

    def resizeEvent(self, event):
        super().resizeEvent(event)
        needed = max(self._breakpoint, self._wide_minimum())
        mode = MODE_WIDE if event.size().width() >= needed else MODE_STACKED
        if mode != self.mode:
            self._apply(mode)

    def _apply(self, mode):
        primary, secondary, tertiary = self._panels
        width = max(self.width(), 1)
        self._outer.insertWidget(0, primary)

        if mode == MODE_WIDE:
            self._outer.insertWidget(1, secondary)
            self._outer.insertWidget(2, tertiary)
            self._stack.hide()
            self._outer.setSizes(_split(width, WIDE_PROPORTIONS) + [0])
        else:
            self._stack.addWidget(secondary)
            self._stack.addWidget(tertiary)
            self._outer.insertWidget(1, self._stack)
            self._stack.show()
            self._outer.setSizes(_split(width, STACKED_PROPORTIONS))
            self._stack.setSizes(_split(max(self.height(), 1), (1, 1)))
        self.mode = mode


def _minimum_width(widget):
    # minimumSizeHint ignores an explicit setMinimumWidth, which is how
    # _ContentWidthTracker reserves room for a column's content.
    return max(widget.minimumWidth(), widget.minimumSizeHint().width())


def _split(total, proportions):
    unit = total / sum(proportions)
    return [int(unit * share) for share in proportions]
