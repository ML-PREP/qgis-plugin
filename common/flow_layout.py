"""A layout that wraps its items onto new rows, like words in a paragraph.

Port of Qt's official "Flow Layout" example (qtbase/examples/widgets/layouts/
flowlayout). Items sit on one row when there is room and wrap when the
container narrows, so a toolbar never forces its parent to scroll sideways.

Unlike the Qt example it does not report heightForWidth: that propagates up
to an enclosing QScrollArea, which then sizes its whole content to preferred
rather than minimum height and scrolls needlessly. The host widget's minimum
height is set to the wrapped height instead.
"""

from qgis.PyQt.QtCore import QPoint, QRect, QSize, Qt
from qgis.PyQt.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def __del__(self):
        while self.takeAt(0) is not None:
            pass

    # -- QLayout interface ------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        height = self._arrange(rect)
        host = self.parentWidget()
        # Converges: the same width yields the same height on the next pass.
        if host is not None and host.minimumHeight() != height:
            host.setMinimumHeight(height)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        """The widest single item: below that, nothing can wrap any further."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # -- placement --------------------------------------------------------
    def _arrange(self, rect):
        """Place items left to right, wrapping; returns the height used."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        gap = self.spacing()

        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and row_height > 0:
                x, y = area.x(), y + row_height + gap
                row_height = 0
            item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            row_height = max(row_height, hint.height())

        return y + row_height - rect.y() + margins.bottom()

