"""Region-of-interest picker on the Landslide tab: search, level and name."""

import os

from qgis.PyQt import QtCore, QtWidgets
from qgis.core import QgsVectorLayer

from ..common.qt_helpers import disable_wheel_scroll
from ..common.roi import ADMIN_LEVELS

DEFAULT_LEVEL = "Municipality"
SEARCH_INDEX_DELAY_MS = 100
INPUTS_PANEL_POSITION = 2


def _unique_names(path, column):
    """Non-empty values of `column` in a boundary shapefile, or [] if unreadable."""
    layer = QgsVectorLayer(path, "roi_temp", "ogr")
    if not layer.isValid():
        return []
    index = layer.fields().indexOf(column)
    if index == -1:
        return []
    return [str(value) for value in layer.uniqueValues(index) if value]


class RoiSelector(QtCore.QObject):
    """Builds the ROI group box into the inputs panel and tracks the choice.

    The widgets are stored on `ui` under the names the master stylesheet uses.
    """

    def __init__(self, ui, admin_config, parent=None):
        super().__init__(parent)
        self._ui = ui
        self._admin_config = admin_config
        self._location_index = {}

        self._build_widgets()
        ui.ComboBox_ROI_Level.currentTextChanged.connect(self.populate_names)
        self.populate_names()
        # Reading every boundary file is slow; let the window paint first.
        QtCore.QTimer.singleShot(SEARCH_INDEX_DELAY_MS, self.build_search_index)

    def _build_widgets(self):
        ui = self._ui
        ui.GroupBox_ROI_Selection = QtWidgets.QGroupBox(ui.Widget_Inputs)
        ui.GroupBox_ROI_Selection.setTitle("Region of Interest")

        layout = QtWidgets.QGridLayout(ui.GroupBox_ROI_Selection)
        layout.setContentsMargins(10, 15, 10, 15)
        layout.setVerticalSpacing(15)
        layout.setHorizontalSpacing(10)
        layout.setColumnStretch(1, 1)
        ui.layout_roi_select = layout

        box = ui.GroupBox_ROI_Selection
        ui.Label_Search = QtWidgets.QLabel("Search Location:", box)
        ui.LineEdit_Search = QtWidgets.QLineEdit(box)
        ui.LineEdit_Search.setPlaceholderText("Type to search (e.g. Alamada)...")

        ui.Label_Level = QtWidgets.QLabel("Select Level:", box)
        ui.ComboBox_ROI_Level = QtWidgets.QComboBox(box)
        ui.ComboBox_ROI_Level.addItems(list(ADMIN_LEVELS))
        ui.ComboBox_ROI_Level.setCurrentText(DEFAULT_LEVEL)

        ui.Label_Name = QtWidgets.QLabel("Select Name:", box)
        ui.ComboBox_ROI_Name = QtWidgets.QComboBox(box)
        ui.ComboBox_ROI_Name.setEditable(False)

        disable_wheel_scroll(ui.ComboBox_ROI_Level)
        disable_wheel_scroll(ui.ComboBox_ROI_Name)

        rows = (
            (ui.Label_Search, ui.LineEdit_Search),
            (ui.Label_Level, ui.ComboBox_ROI_Level),
            (ui.Label_Name, ui.ComboBox_ROI_Name),
        )
        for row, (label, field) in enumerate(rows):
            layout.addWidget(label, row, 0)
            layout.addWidget(field, row, 1)

        ui.verticalLayout_4.insertWidget(INPUTS_PANEL_POSITION, box)

    @property
    def level(self):
        return self._ui.ComboBox_ROI_Level.currentText()

    @property
    def name(self):
        return self._ui.ComboBox_ROI_Name.currentText()

    def boundary(self):
        """{"path", "col"} of the selected admin level, or None."""
        return self._admin_config.get(self.level)

    def populate_names(self):
        combo = self._ui.ComboBox_ROI_Name
        combo.clear()
        config = self.boundary()
        if not config:
            return
        if not os.path.exists(config["path"]):
            combo.addItem("Error: missing file")
            return
        try:
            combo.addItems(sorted(_unique_names(config["path"], config["col"])))
        except Exception:
            pass

    def build_search_index(self):
        """Autocomplete over every admin name; the first level to list a name wins."""
        index = {}
        for level, config in self._admin_config.items():
            if not os.path.exists(config["path"]):
                continue
            try:
                names = _unique_names(config["path"], config["col"])
            except Exception:
                continue
            for name in names:
                index.setdefault(name, level)
        self._location_index = index

        completer = QtWidgets.QCompleter(list(index), self._ui.LineEdit_Search)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        self._ui.LineEdit_Search.setCompleter(completer)
        completer.activated.connect(self._on_search_selected)

    def _on_search_selected(self, text):
        level = self._location_index.get(text)
        if not level:
            return
        self._ui.ComboBox_ROI_Level.setCurrentText(level)
        combo = self._ui.ComboBox_ROI_Name
        index = combo.findText(text)
        if index != -1:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentText(text)
