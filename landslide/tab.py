"""The Landslide Detection tab: input pickers, previews, detection and save."""

import os
import shutil
import subprocess
import tempfile

from qgis.PyQt import QtCore, QtWidgets
from qgis.PyQt.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from .. import platform_utils as pu
from ..common import paths
from ..common.qgis_layers import add_raster_layer
from ..common.qt_helpers import render_preview
from ..common.roi_selector import RoiSelector
from ..landslide_mainwindow import Ui_MainWindow as Ui_Landslide
from .detection import (
    DetectionRequest,
    build_detection_command,
    detection_environment,
    parse_worker_result,
)

RESULT_FILENAME = "temp_landslide_result.tif"
ROI_PANEL_POSITION = 2


class LandslideTab(QtCore.QObject):
    def __init__(self, window, admin_config):
        super().__init__(window)
        self._window = window
        self._venv_python = pu.venv_python("ls_detect_env")
        self._result_path = None

        # The Designer form is laid out on a throwaway main window; its panels
        # are then re-parented into this tab's container.
        self.ui = Ui_Landslide()
        self._form_host = QMainWindow()
        self.ui.setupUi(self._form_host)
        self.widget = self._build_container()

        self.ui.Label_ROI.setText("LULC Shapefile:")
        self.ui.GroupBox_ROI.setTitle("LULC Preview")
        self.roi = RoiSelector(
            self.ui,
            admin_config,
            self.ui.verticalLayout_4,
            ROI_PANEL_POSITION,
            parent=self,
        )

        self._connect_signals()

    def _build_container(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.ui.Widget_Map_Area, stretch=3)
        layout.addWidget(self.ui.scrollArea, stretch=1)
        return container

    def _connect_signals(self):
        self.ui.PushButton_ROI.clicked.connect(self._select_lulc_file)
        self.ui.PushButton_DEM.clicked.connect(self._select_dem_file)
        self.ui.PushButton_Sentinel_2_Post_Event.clicked.connect(
            self._select_post_event_file
        )
        self.ui.Button_Detect.clicked.connect(self.run_detection)
        self.ui.Button_Save.clicked.connect(self.save_result)

    # ------------------------------------------------------------------
    # Inputs and previews
    # ------------------------------------------------------------------
    def _open_file(self, title, file_filter):
        filename, _ = QFileDialog.getOpenFileName(self._window, title, "", file_filter)
        return filename

    def _select_lulc_file(self):
        filename = self._open_file("Select LULC Shapefile", "Shapefiles (*.shp)")
        if filename:
            self.ui.PTE_ROI.setPlainText(filename)
            render_preview(filename, self.ui.GraphicsView_ROI, is_raster=False)

    def _select_dem_file(self):
        filename = self._open_file("Select DEM", "Tiff (*.tif)")
        if filename:
            self.ui.PTE_DEM.setPlainText(filename)
            render_preview(filename, self.ui.GraphicsView_DEM_2, is_raster=True)
            self._preview_slope(filename)

    def _select_post_event_file(self):
        filename = self._open_file("Select Post-Event", "Tiff (*.tif)")
        if filename:
            self.ui.PTE_Sentinel_2_Post_Event.setPlainText(filename)
            render_preview(filename, self.ui.GraphicsView_Post_Event, is_raster=True)

    def _preview_slope(self, dem_path):
        """Derive a temporary slope raster with QGIS's native algorithm and show it."""
        import processing

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result = processing.run(
                "native:slope",
                {"INPUT": dem_path, "Z_FACTOR": 1.0, "OUTPUT": "TEMPORARY_OUTPUT"},
            )
            render_preview(result["OUTPUT"], self.ui.GraphicsView_Slope, is_raster=True)
        except Exception as e:
            QMessageBox.warning(
                self._window,
                "Preview Error",
                f"Could not generate Slope preview:\n{str(e)}",
            )
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def _build_request(self):
        """The detection inputs, or None after telling the user what is missing."""
        boundary = self.roi.boundary()
        if not boundary:
            return None

        dem = self.ui.PTE_DEM.toPlainText()
        after = self.ui.PTE_Sentinel_2_Post_Event.toPlainText()
        if not all([dem, after]):
            QMessageBox.warning(
                self._window,
                "Missing Inputs",
                "Please select DEM and Sentinel-2 Post-Event files. LULC is optional.",
            )
            return None

        if not os.path.exists(self._venv_python):
            QMessageBox.critical(
                self._window,
                "Config Error",
                f"Virtual Environment Python not found at:\n{self._venv_python}",
            )
            return None

        if not (
            os.path.exists(paths.LANDSLIDE_MODEL)
            and os.path.exists(paths.LANDSLIDE_NORM_STATS)
        ):
            QMessageBox.critical(
                self._window,
                "Model Missing",
                "Ensure 'best.keras' and 'norm_stats.json' are placed in the "
                "'model' folder.",
            )
            return None

        return DetectionRequest(
            dem=dem,
            after=after,
            lulc=self.ui.PTE_ROI.toPlainText() or "None",
            roi_path=boundary["path"],
            # By PCODE: names repeat across the country.
            filter_col=boundary["pcode"],
            filter_val=self.roi.choice.pcode if self.roi.choice else "",
        )

    def run_detection(self):
        request = self._build_request()
        if request is None:
            return

        output_path = os.path.join(tempfile.gettempdir(), RESULT_FILENAME)
        command = build_detection_command(self._venv_python, request, output_path)

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                env=detection_environment(),
                **pu.subprocess_kwargs(),
            )
            if process.returncode != 0:
                QMessageBox.critical(
                    self._window, "Error", f"Process failed.\n{process.stderr}"
                )
                return
            self._show_result(process.stdout)
        except Exception as e:
            QMessageBox.critical(self._window, "System Error", str(e))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _show_result(self, stdout):
        try:
            result = parse_worker_result(stdout)
        except ValueError:
            QMessageBox.warning(
                self._window,
                "Error",
                f"Could not parse script output.\n\nTerminal Output:\n{stdout}",
            )
            return

        if result.get("status") != "success":
            QMessageBox.critical(
                self._window, "Worker Error", f"Script failed: {result.get('message')}"
            )
            return

        QMessageBox.information(
            self._window, "Success", "Detection Complete! You can now click SAVE."
        )
        mask_path = result.get("output_mask")
        self._result_path = mask_path

        # The prediction goes only to the isolated Results viewer.
        if mask_path and os.path.exists(mask_path):
            render_preview(mask_path, self.ui.GraphicsView_Result, is_raster=True)
            add_raster_layer(mask_path, "Landslide Result (Preview)")

    def save_result(self):
        if not self._result_path or not os.path.exists(self._result_path):
            QMessageBox.warning(
                self._window,
                "Save",
                "No detection result exists yet.\nPlease run 'DETECT' first.",
            )
            return

        safe_name = (self.roi.name or "Landslide_Result").replace(" ", "_")
        default_path = os.path.join(
            QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.DocumentsLocation
            ),
            f"{safe_name}_Landslide.tif",
        )
        filename, _ = QFileDialog.getSaveFileName(
            self._window, "Save Landslide Mask", default_path, "Tiff Files (*.tif)"
        )
        if not filename:
            return
        try:
            shutil.copy2(self._result_path, filename)
            QMessageBox.information(
                self._window, "Saved", f"File successfully saved to:\n{filename}"
            )
        except Exception as e:
            QMessageBox.critical(
                self._window, "Save Error", f"Could not save file:\n{str(e)}"
            )
