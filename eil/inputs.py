"""The EIL input panel: DEM and PINN parameter rasters."""

import os

from qgis.PyQt import QtCore, QtGui
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import QgsProject, QgsRasterLayer

from ..common import paths
from ..common.qt_helpers import (
    add_file_choice,
    disable_wheel_scroll,
    layer_source_path,
    project_raster_layers,
)
from ..log_panel import LEVEL_DETAIL as LOG_DETAIL
from .params import DEM_INPUT_NAME, MODEL_INPUT_NAMES

DEFAULT_CRS = "EPSG:4326"
RASTER_SUFFIXES = (".tif", ".tiff")
NO_DEM = "Select a DEM layer..."
NO_LAYER = "Select a layer..."


class EilInputs(QtCore.QObject):
    def __init__(self, window, ui, log, parent=None):
        super().__init__(parent)
        self._window = window
        self._ui = ui
        self._log = log
        self._dem_icon = (
            QtGui.QIcon(paths.DEM_ICON)
            if os.path.exists(paths.DEM_ICON)
            else QtGui.QIcon()
        )
        self._parameter_boxes = [
            ui.comboBox_BulkDensity,
            ui.comboBox_Clay,
            ui.comboBox_Sand,
            ui.comboBox_Silt,
            ui.comboBox_Slope,
            ui.comboBox_PGA,
            ui.comboBox_PRC,
            ui.comboBox_SoilThickness,
            ui.comboBox_SoilType,
            ui.comboBox_ContributingFactor,
        ]
        self._connect_signals()
        self.refresh_layers()

    def _connect_signals(self):
        ui = self._ui
        browse_buttons = {
            ui.pushButton_BulkDensity: ui.comboBox_BulkDensity,
            ui.pushButton_Clay: ui.comboBox_Clay,
            ui.pushButton_Sand: ui.comboBox_Sand,
            ui.pushButton_Silt: ui.comboBox_Silt,
            ui.pushButton_Slope: ui.comboBox_Slope,
            ui.pushButton_PGA: ui.comboBox_PGA,
            ui.pushButton_PRC: ui.comboBox_PRC,
            ui.pushButton_SoilThickness: ui.comboBox_SoilThickness,
            ui.pushButton_SoilType: ui.comboBox_SoilType,
            ui.pushButton_ContributingFactor: ui.comboBox_ContributingFactor,
        }
        for button, box in browse_buttons.items():
            button.clicked.connect(lambda _checked, b=box: self._browse_raster(b))
        ui.pushButton_DEM.clicked.connect(self._browse_dem)

        disable_wheel_scroll(ui.comboBox_DEM)
        ui.comboBox_DEM.currentTextChanged.connect(
            lambda text: self._log_selection(text, "DEM")
        )
        for box in self._parameter_boxes:
            disable_wheel_scroll(box)
            box.currentTextChanged.connect(
                lambda text, b=box: self._log_selection(
                    text, b.objectName().replace("comboBox_", "")
                )
            )

        QgsProject.instance().layersAdded.connect(self.refresh_layers)
        QgsProject.instance().layersRemoved.connect(self.refresh_layers)

    def _log_selection(self, text, layer_type):
        if text and "Select a" not in text:
            self._log(f"[{layer_type} Selected]: {text}", LOG_DETAIL)

    def _browse_raster(self, combobox):
        filename, _ = QFileDialog.getOpenFileName(
            self._window, "Select Local Raster", "", "GeoTIFF (*.tif *.tiff)"
        )
        if filename:
            add_file_choice(combobox, filename)

    def _browse_dem(self):
        filename, _ = QFileDialog.getOpenFileName(
            self._window,
            "Select DEM Raster",
            "",
            "Tiff Files (*.tif *.tiff);;All Files (*.*)",
        )
        if filename:
            add_file_choice(self._ui.comboBox_DEM, filename, self._dem_icon)

    def refresh_layers(self):
        """Re-list the project's rasters, keeping each box's current choice."""
        rasters = project_raster_layers()
        self._repopulate(self._ui.comboBox_DEM, NO_DEM, rasters)
        for box in self._parameter_boxes:
            self._repopulate(box, NO_LAYER, rasters)

    @staticmethod
    def _repopulate(box, placeholder, rasters):
        previous = box.currentData()
        box.blockSignals(True)
        box.clear()
        box.addItem(placeholder, None)
        for layer in rasters:
            box.addItem(layer.name(), layer.id())
        index = box.findData(previous)
        if index != -1:
            box.setCurrentIndex(index)
        box.blockSignals(False)

    def has_dem(self):
        return bool(self._ui.comboBox_DEM.currentData())

    def resolve_dem(self) -> tuple[str, str]:
        """(path, CRS authid) of the chosen DEM; path is "" if unresolvable."""
        choice = self._ui.comboBox_DEM.currentData()
        if os.path.isabs(str(choice)):
            layer = QgsRasterLayer(str(choice), "temp_dem")
            crs = layer.crs().authid() if layer.isValid() else DEFAULT_CRS
            return str(choice), crs

        layer = QgsProject.instance().mapLayer(choice)
        if not layer:
            return "", DEFAULT_CRS
        return layer.source().split("|")[0], layer.crs().authid()

    def selected_rasters(self):
        """{model input name: raster path} for every usable selection."""
        selected = {}

        dem_path = layer_source_path(self._ui.comboBox_DEM.currentData())
        if dem_path and os.path.exists(dem_path):
            selected[DEM_INPUT_NAME] = dem_path

        for box in self._parameter_boxes:
            name = MODEL_INPUT_NAMES.get(box.objectName())
            path = layer_source_path(box.currentData())
            if (
                name
                and path
                and os.path.exists(path)
                and path.lower().endswith(RASTER_SUFFIXES)
            ):
                selected[name] = path

        return selected
