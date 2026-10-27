"""The EIL Detection tab: wiring, run state and the Run/Cancel entry points."""

import os
from typing import Optional

from qgis.PyQt import QtCore
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from .. import platform_utils as pu
from ..busy_overlay import BusyOverlay
from ..common import paths
from ..common.roi_selector import RoiSelector
from ..eil_mainwindow import Ui_MainWindow as Ui_EIL
from ..log_panel import LEVEL_ERROR
from ..log_panel import LEVEL_SUCCESS as LOG_SUCCESS
from ..log_panel import LEVEL_WARNING as LOG_WARNING
from ..progress_pulse import ProgressPulse
from .dashboard import HazardDashboard
from .feature_info import FeatureHoverFilter
from .inputs import EilInputs
from .model_picker import PinnModelPicker
from .params import DEFAULT_MAX_ITERATIONS, DEFAULT_OUTPUT_SUBDIR
from .pipeline import EilPipeline, EilRunRequest

# Between PINN Model and Output Setup in the left panel.
ROI_PANEL_POSITION = 3


class EilTab(QtCore.QObject):
    # Every stage reports through the same three surfaces -- the veil, the
    # progress bar format and the Run button label -- so the tab can never
    # show a half-updated "is it running?" state.
    RUN_LABEL = "Run Pipeline"
    BUSY_LABEL = "Running…"

    def __init__(self, window, dependency_panel, show_dependencies, admin_config):
        super().__init__(window)
        self._window = window
        self._dependency_panel = dependency_panel
        self._show_dependencies = show_dependencies
        self._running = False

        # The Designer form is laid out on a throwaway main window; its central
        # widget becomes this tab.
        self.ui = Ui_EIL()
        self._form_host = QMainWindow()
        self.ui.setupUi(self._form_host)
        self.widget = self.ui.centralwidget

        self.dashboard = HazardDashboard(
            self.ui, self.log, admin_config["Municipality"]
        )
        self.roi = RoiSelector(
            self.ui,
            admin_config,
            self.ui.verticalLayout,
            ROI_PANEL_POSITION,
            title="Clip Results to Region of Interest",
            checkable=True,
            parent=self,
        )

        # Parented to the scroll area's viewport, not the contents widget: the
        # contents get disabled during a run, and a disabled parent would
        # disable the veil with it.
        self._overlay = BusyOverlay(self.ui.scrollAreaLeft.viewport())
        self._pulse = ProgressPulse(self.ui.progressBar)

        self.ui.plainTextEdit_Details.setReadOnly(True)
        self._hover = FeatureHoverFilter(self.ui.plainTextEdit_Details, parent=self)
        self._hover.watch(self.ui)

        self.inputs = EilInputs(window, self.ui, self.log, parent=self)
        self.model_picker = PinnModelPicker(
            self.ui.comboBox_Model,
            self.ui.label_ModelDescription,
            paths.PINN_MODEL_CONFIG,
            QSettings(),
            self.log,
            parent=self,
        )
        self.pipeline = EilPipeline(
            self, dependency_panel, parent=self
        )  # NOTE:: A worker

        self._reset_panels()
        self.ui.pushButton_OutputFolder.clicked.connect(self._select_output_folder)
        self.ui.pushButton_Run.clicked.connect(self._on_run_clicked)
        self.ui.pushButton_Cancel.clicked.connect(self._on_cancel_clicked)

    def _reset_panels(self):
        self.ui.plainTextEdit_Logs.clear()
        self.ui.plainTextEdit_Details.clear()
        self.ui.progressBar.setValue(0)

        box = self.ui.plainTextEdit_MaxIteration
        box.setStyleSheet("QPlainTextEdit { color: black; }")
        box.setPlaceholderText(f"[Default: {DEFAULT_MAX_ITERATIONS}]")
        box.setPlainText(str(DEFAULT_MAX_ITERATIONS))

    # ------------------------------------------------------------------
    # Surfaces the pipeline reports through
    # ------------------------------------------------------------------
    def log(self, message, level=None):
        """Single entry point for EIL tab logging.

        `level` is optional: worker threads emit bare strings, and the panel
        infers the severity from the line itself when none is given.
        """
        self.ui.plainTextEdit_Logs.append_message(message, level)

    @property
    def is_running(self):
        return self._running

    def set_progress(self, value):
        self.ui.progressBar.setValue(value)

    def should_load_results(self):
        return self.ui.checkBox_LoadResults.isChecked()

    def show_completion(self, title, text):
        """Tell the user the whole pipeline has finished."""
        self.log(f"\n{title}", LOG_SUCCESS)
        QMessageBox.information(self._window, title, text)

    def set_running(self, running, stage="", hint=""):
        """Enter or leave the busy state, in one place, for every exit path."""
        self._running = running

        # Disabling the real widgets (not just veiling them) keeps keyboard
        # focus traversal out of the inputs. Cancel is deliberately absent.
        for widget in (self.ui.scrollAreaWidgetContentsLeft, self.ui.pushButton_Run):
            widget.setEnabled(not running)
        self.ui.pushButton_Cancel.setEnabled(True)

        if running:
            self.ui.pushButton_Run.setText(self.BUSY_LABEL)
            self._overlay.start(stage, hint)
            self._pulse.start()
            self.ui.progressBar.setFormat(f"{stage}  %p%")
            return

        self.ui.pushButton_Run.setText(self.RUN_LABEL)
        self._overlay.stop()
        self._pulse.stop()
        self.ui.progressBar.setFormat("%p%")
        self.refresh_run_button()

    def set_stage(self, stage, hint=""):
        """Relabel the running pipeline as it moves between stages."""
        if not self._running:
            return
        self._overlay.set_stage(stage, hint)
        self.ui.progressBar.setFormat(f"{stage}  %p%")

    def refresh_run_button(self):
        """Enable Run only when dependencies are met and nothing is running."""
        ready = self._dependency_panel.is_ready(pu.FEATURE_EIL)
        self.ui.pushButton_Run.setEnabled(ready and not self._running)
        self.ui.pushButton_Run.setToolTip(
            "" if ready else "Missing dependencies — see the Dependencies tab."
        )

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def _select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self._window, "Select Output Directory"
        )
        if folder:
            self.ui.plainTextEdit_OutputFolder.setPlainText(folder)
            self.log(f"[Output] Results will be saved to: {folder}")

    def _dependencies_met(self):
        """True when EIL can run; otherwise explain and show the panel."""
        message = self._dependency_panel.blocking_message(pu.FEATURE_EIL)
        if message is None:
            return True
        QMessageBox.warning(self._window, "Missing dependencies", message)
        self._show_dependencies()
        return False

    def _output_folder(self, dem_path):
        """The chosen output folder, or EIL_Results beside the DEM; created."""
        chosen = self.ui.plainTextEdit_OutputFolder.toPlainText().strip()
        if chosen and os.path.isdir(chosen):
            folder = chosen
        else:
            folder = os.path.join(os.path.dirname(dem_path), DEFAULT_OUTPUT_SUBDIR)
            self.ui.plainTextEdit_OutputFolder.setPlainText(folder)
        os.makedirs(folder, exist_ok=True)
        return folder

    def _build_request(self) -> Optional[EilRunRequest]:
        """The run's inputs, or None after reporting what is missing."""
        if not self.inputs.has_dem():
            QMessageBox.warning(self._window, "Error", "Please select a DEM.")
            self.log("[ERROR] No DEM selected.")
            return None

        dem_path, dem_crs = self.inputs.resolve_dem()
        if not dem_path or not os.path.exists(dem_path):
            self.log(f"[ERROR] Could not locate DEM file:\n{dem_path}", LEVEL_ERROR)
            return None

        rasters = self.inputs.selected_rasters()
        if not rasters:
            self.log("No parameters selected. Pipeline finished early.", LOG_WARNING)
            return None

        # Checked now rather than after the zonal-statistics stage, which can
        # take hours before the model is first needed.
        model = self.model_picker.selected()
        if model is None:
            QMessageBox.warning(
                self._window,
                "No PINN model",
                "No PINN model file is available. Choose one under "
                f'"PINN Model", or add one in:\n{paths.PINN_MODEL_CONFIG}',
            )
            self.log("[ERROR] No PINN model file is available.", LEVEL_ERROR)
            return None

        roi = None
        if self.roi.is_enabled:
            roi = self.roi.selection()
            if roi is None:
                QMessageBox.warning(
                    self._window,
                    "Error",
                    "Select a region of interest to clip to, or untick "
                    '"Clip Results to Region of Interest".',
                )
                self.log("[ERROR] Clipping is on but no region is selected.")
                return None

        return EilRunRequest(
            dem_path=dem_path,
            dem_crs=dem_crs,
            rasters=rasters,
            output_folder=self._output_folder(dem_path),
            ndvi_path=self.inputs.ndvi_path(),
            roi=roi,
            model_path=model.path,
            model_kind=model.kind,
            cell_size_m=model.cell_size_m,
        )

    def _on_run_clicked(self):
        if self._running:
            return

        # Dependencies not being setup
        try:
            self.refresh_and_check_dependencies()
        except Exception as exc:
            return

        self.log("\n--- STARTING FULL EIL PIPELINE ---")
        self.ui.progressBar.setValue(0)

        request = self._build_request()
        if request is not None:
            self.log(f"[Model] PINN model: {self.model_picker.selected().name}")
            self.pipeline.start(request)

    def refresh_and_check_dependencies(self):
        self._dependency_panel.refresh()
        if not self._dependencies_met():
            raise Exception("Dependencies are not installed")

    def _on_cancel_clicked(self):
        if not self._running:
            self.ui.progressBar.setValue(0)
            self.ui.progressBar.setFormat("%p%")
            return
        self.pipeline.cancel()
