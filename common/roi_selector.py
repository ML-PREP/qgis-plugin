"""Region-of-interest picker shared by the Landslide and EIL tabs.

Each entry is an admin unit keyed by its PCODE and labelled with its parents
("Poblacion, Makilala, Cotabato"), so same-named units never get confused.
"""

import os
from functools import lru_cache

from qgis.PyQt import QtCore, QtWidgets
from qgis.core import QgsFeatureRequest, QgsVectorLayer

from .qt_helpers import disable_wheel_scroll
from .roi import ADMIN_LEVELS, RoiSelection, label_choices

DEFAULT_LEVEL = "Municipality"
SEARCH_INDEX_DELAY_MS = 100


# Both tabs list the same boundary files; read each one once per session.
@lru_cache(maxsize=None)
def _read_choices(level, path, name_column, pcode_column, parents):
    """RoiChoices for every unit in a boundary shapefile, or () if unreadable."""
    if not os.path.exists(path):
        return ()
    layer = QgsVectorLayer(path, "roi_temp", "ogr")
    if not layer.isValid():
        return ()
    fields = layer.fields()
    columns = (pcode_column, name_column, *parents)
    if any(fields.indexOf(column) == -1 for column in columns):
        return ()

    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.NoGeometry)
    request.setSubsetOfAttributes(list(columns), fields)
    rows = (
        (
            feature[pcode_column],
            feature[name_column],
            tuple(feature[parent] for parent in parents),
        )
        for feature in layer.getFeatures(request)
    )
    return label_choices(level, rows)


def _choices_for(level, config):
    """RoiChoices of `level`; () when its file is missing or unreadable."""
    if not config:
        return ()
    try:
        return _read_choices(
            level, config["path"], config["col"], config["pcode"], config["parents"]
        )
    except Exception:
        return ()


class RoiSelector(QtCore.QObject):
    """Builds the ROI group box into a host layout and tracks the choice.

    The widgets are stored on `ui` under the names the Landslide stylesheet
    uses. A `checkable` box doubles as an on/off switch for the ROI.
    """

    def __init__(
        self,
        ui,
        admin_config,
        host_layout,
        position,
        title="Region of Interest",
        checkable=False,
        parent=None,
    ):
        super().__init__(parent)
        self._ui = ui
        self._admin_config = admin_config
        self._search_index = {}

        self._build_widgets(host_layout, position, title, checkable)
        ui.ComboBox_ROI_Level.currentTextChanged.connect(self.populate_names)
        self.populate_names()
        # Reading every boundary file is slow; let the window paint first.
        QtCore.QTimer.singleShot(SEARCH_INDEX_DELAY_MS, self.build_search_index)

    def _build_widgets(self, host_layout, position, title, checkable):
        ui = self._ui
        box = QtWidgets.QGroupBox(title, host_layout.parentWidget())
        box.setObjectName("GroupBox_ROI_Selection")
        box.setCheckable(checkable)
        if checkable:
            box.setChecked(False)
        ui.GroupBox_ROI_Selection = box

        layout = QtWidgets.QGridLayout(box)
        layout.setContentsMargins(10, 15, 10, 15)
        layout.setVerticalSpacing(15)
        layout.setHorizontalSpacing(10)
        layout.setColumnStretch(1, 1)
        ui.layout_roi_select = layout

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

        host_layout.insertWidget(position, box)

    # ------------------------------------------------------------------
    # The current choice
    # ------------------------------------------------------------------
    @property
    def is_enabled(self):
        """False only when the box is checkable and unchecked."""
        box = self._ui.GroupBox_ROI_Selection
        return not box.isCheckable() or box.isChecked()

    @property
    def level(self):
        return self._ui.ComboBox_ROI_Level.currentText()

    @property
    def choice(self):
        """The selected RoiChoice, or None."""
        return self._ui.ComboBox_ROI_Name.currentData()

    @property
    def name(self):
        """The bare name of the selected unit ("" if none)."""
        return self.choice.name if self.choice else ""

    def boundary(self):
        """{"path", "col", "pcode", "parents"} of the selected level, or None."""
        return self._admin_config.get(self.level)

    def selection(self):
        """RoiSelection for the chosen unit, or None if nothing is chosen."""
        config = self.boundary()
        if not self.choice or not config:
            return None
        return RoiSelection(self.choice, config["path"], config["pcode"])

    # ------------------------------------------------------------------
    # Populating
    # ------------------------------------------------------------------
    def populate_names(self):
        combo = self._ui.ComboBox_ROI_Name
        combo.clear()
        config = self.boundary()
        if not config:
            return
        if not os.path.exists(config["path"]):
            combo.addItem("Error: missing file", None)
            return
        for choice in _choices_for(self.level, config):
            combo.addItem(choice.label, choice)

    def build_search_index(self):
        """Autocomplete over every admin unit's label, across all levels."""
        index = {}
        for level, config in self._admin_config.items():
            for choice in _choices_for(level, config):
                index.setdefault(choice.label, choice)
        self._search_index = index

        completer = QtWidgets.QCompleter(list(index), self._ui.LineEdit_Search)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        self._ui.LineEdit_Search.setCompleter(completer)
        completer.activated.connect(self._on_search_selected)

    def _on_search_selected(self, text):
        choice = self._search_index.get(text)
        if not choice:
            return
        self._ui.ComboBox_ROI_Level.setCurrentText(choice.level)
        combo = self._ui.ComboBox_ROI_Name
        index = combo.findText(choice.label)
        if index != -1:
            combo.setCurrentIndex(index)
