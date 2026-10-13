"""Sequencing of the EIL pipeline's stages.

    slope units + zonal stats (Go)  ->  seam cleanup  ->  PINN predictions
        ->  dashboard  ->  depositional routing (TauDEM + C++/MPI, optional)

Each stage runs on a worker thread; its completion signal starts the next.
The tab owns the "running" flag: a stage reporting in after a cancel finds it
cleared and is ignored.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass, field

from qgis.PyQt import QtCore, QtWidgets

from .. import platform_utils as pu
from ..common import paths
from ..common.qgis_layers import add_raster_layer, add_vector_layer
from ..log_panel import LEVEL_ERROR as LOG_ERROR
from ..log_panel import LEVEL_SUCCESS as LOG_SUCCESS
from ..log_panel import LEVEL_WARNING as LOG_WARNING
from .debug_process import InfinitePrintingProbe
from .params import (
    DEPOSITION_CORES,
    DEPOSITION_OUTPUT_SUBDIR,
    SLOPE_UNIT_AREA_MIN,
    ZONAL_PARAMS,
)
from .seams import eliminate_seams
from .workers.deposition import MLDFIDepositionWorker
from .workers.pinn import PINNPredictionWorker
from .workers.zonal import GoParallelZonalWorker

SEAM_CLEANUP_PROGRESS = 90


@dataclass(frozen=True)
class EilRunRequest:
    dem_path: str
    dem_crs: str
    rasters: dict = field(default_factory=dict)
    output_folder: str = ""


def _stage_rasters(rasters):
    """Folder of <input name>.tif links (copies where links are unsupported)."""
    folder = tempfile.mkdtemp(prefix="go_rasters_")
    for name, source in rasters.items():
        target = os.path.join(folder, f"{name}.tif")
        try:
            os.symlink(source, target)
        except OSError:
            shutil.copy2(source, target)
    return folder


class EilPipeline(QtCore.QObject):
    def __init__(self, view, dependency_panel, parent=None):
        super().__init__(parent)
        self._view = view
        self._dependency_panel = dependency_panel
        self._venv_python = pu.venv_python("venv")
        self._request = None
        self._temp_raster_dir = None
        self._zonal_worker = None
        self._pinn_worker = None
        self._deposition_worker = None
        self._probe = InfinitePrintingProbe(view.log, parent=self)

    # ------------------------------------------------------------------
    # Start / cancel
    # ------------------------------------------------------------------
    def start(self, request):
        view = self._view
        self._request = request
        self._temp_raster_dir = _stage_rasters(request.rasters)
        params = dict(ZONAL_PARAMS)

        view.log(f"[Input DEM]: {os.path.basename(request.dem_path)}")
        view.log(f"[Slope Params]: {params}")
        view.set_running(
            True,
            "Building slope units",
            "Tiling the DEM, delineating slope units and computing zonal "
            "statistics for each selected parameter.",
        )

        self._zonal_worker = GoParallelZonalWorker(
            paths.GO_PROJECT_DIR,
            request.dem_path,
            self._temp_raster_dir,
            request.output_folder,
            self._venv_python,
            request.dem_crs,
            len(request.rasters),
            params,
        )
        # self._probe.start()

        self._zonal_worker.progress.connect(view.set_progress)
        self._zonal_worker.log.connect(view.log)
        self._zonal_worker.error.connect(self._on_stage_error)
        self._zonal_worker.finished.connect(self._on_zonal_finished)
        self._zonal_worker.start()

    def cancel(self):
        view = self._view
        self._probe.kill()
        view.log("\n--> Cancelling: stopping the running stage…")
        view.set_stage("Cancelling…", "Stopping the current stage.")
        QtWidgets.QApplication.processEvents()

        for worker in (self._zonal_worker, self._pinn_worker, self._deposition_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()

        self._cleanup_temp_rasters()
        view.set_running(False)
        view.log("--> Algorithm Cancelled by User.", LOG_WARNING)
        view.set_progress(0)

    def _cleanup_temp_rasters(self):
        if self._temp_raster_dir and os.path.exists(self._temp_raster_dir):
            shutil.rmtree(self._temp_raster_dir, ignore_errors=True)
        self._temp_raster_dir = None

    def _stop(self):
        self._view.set_running(False)

    def _load_vector(self, path, name):
        if not path or not os.path.exists(path):
            self._view.log(f"[Warning] {name} path not found: {path}")
            return
        if add_vector_layer(path, name):
            self._view.log(f"--> Loaded into map: {name}")

    # ------------------------------------------------------------------
    # Stage 1 done: clean seams, then run the PINN
    # ------------------------------------------------------------------
    def _on_zonal_finished(self, output_folder, status):
        if not self._view.is_running:
            return
        if status == "success":
            self._after_zonal(output_folder)
        else:
            self._stop()
        self._cleanup_temp_rasters()

    def _clean_seams(self, output_folder, raw_name, cleaned_name, label):
        raw = os.path.join(output_folder, raw_name)
        if not os.path.exists(raw):
            return raw
        cleaned = os.path.join(output_folder, cleaned_name)
        return eliminate_seams(raw, cleaned, SLOPE_UNIT_AREA_MIN, self._view.log, label)

    def _after_zonal(self, output_folder):
        view = self._view
        view.log("\n[Data Prep Complete] All features merged!", LOG_SUCCESS)
        view.set_progress(SEAM_CLEANUP_PROGRESS)
        view.set_stage(
            "Cleaning geometry",
            "Erasing tile seams from the slope units and merged features.",
        )
        # Seam removal runs on the GUI thread, so paint the new stage text
        # before it blocks rather than after it finishes.
        QtWidgets.QApplication.processEvents()

        if os.path.exists(os.path.join(output_folder, "Final_SlopeUnits.gpkg")):
            view.log("--> Erasing tile seams on Slope Units...")
        slope_units = self._clean_seams(
            output_folder,
            "Final_SlopeUnits.gpkg",
            "Cleaned_SlopeUnits.gpkg",
            "Slope Units",
        )
        if os.path.exists(slope_units) and view.should_load_results():
            self._load_vector(slope_units, "EIL Slope Units")

        features = self._clean_seams(
            output_folder,
            "Merged_PINN_Features.gpkg",
            "Cleaned_PINN_Features.gpkg",
            "PINN Features",
        )

        # Cancel may have been clicked while seam removal was blocking.
        if not view.is_running:
            return

        if not os.path.exists(features):
            self._stop()
            return

        if not os.path.exists(paths.EIL_MODEL):
            view.log(f"\n[WARNING] AI Model not found at: {paths.EIL_MODEL}")
            if view.should_load_results():
                self._load_vector(features, "Merged PINN Features")
            self._stop()
            return

        self._start_pinn(features, output_folder)

    def _start_pinn(self, features, output_folder):
        self._view.set_stage(
            "Running AI model",
            "Loading TensorFlow and predicting susceptibility for every slope unit.",
        )
        self._pinn_worker = PINNPredictionWorker(
            self._venv_python,
            paths.PINN_SCRIPT,
            features,
            paths.EIL_MODEL,
            os.path.join(output_folder, "Final_Hazard_Map.gpkg"),
            self._request.dem_path,
        )
        self._pinn_worker.log.connect(self._view.log)
        self._pinn_worker.finished.connect(self._on_pinn_finished)
        self._pinn_worker.start()

    # ------------------------------------------------------------------
    # Stage 2 done: dashboard, then depositional routing
    # ------------------------------------------------------------------
    def _on_pinn_finished(self, result, status):
        view = self._view
        if not view.is_running:
            return

        view.set_progress(100)
        if status != "success":
            self._stop()
            return

        view.log("\n✅ AI Predictions generated successfully!", LOG_SUCCESS)
        if view.should_load_results():
            self._load_vector(result.get("output"), "EIL AI Predictions")

        view.log("\n🎉 ENTIRE PIPELINE COMPLETE!", LOG_SUCCESS)
        view.dashboard.update(result.get("output"))

        overall_tif = result.get("overall_tif")
        if not (overall_tif and os.path.exists(overall_tif)):
            self._stop()
            return
        if self._deposition_blocked():
            self._stop()
            return
        self._start_deposition(overall_tif)

    def _deposition_blocked(self):
        """True (after logging why) when routing cannot run.

        Deposition is a bonus stage after the result already exists, so a
        missing piece skips it with an explanation rather than a dialog.
        """
        if not os.path.exists(paths.STEP4_WRAPPER):
            self._view.log(
                "[Warning] Step 4 Wrapper not found. Skipping deposition routing."
            )
            return True

        missing = self._dependency_panel.missing_for(pu.FEATURE_DEPOSITION)
        if missing:
            self._view.log(
                "\n[Skipped] Depositional routing needs "
                + ", ".join(m.spec.label for m in missing)
                + ".\n           See the Dependencies tab to install "
                "or locate them, then re-run."
            )
            return True
        return False

    def _start_deposition(self, overall_tif):
        view = self._view
        view.log("\n--- INITIATING DYNAMIC-ALPHA DEPOSITION ROUTING ---")
        view.set_stage(
            "Routing deposition zones",
            "TauDEM flow routing and MPI runout modelling.",
        )
        self._deposition_worker = MLDFIDepositionWorker(
            dem_path=self._request.dem_path,
            dfi_tif=overall_tif,
            output_dir=os.path.join(
                self._request.output_folder, DEPOSITION_OUTPUT_SUBDIR
            ),
            step4_wrapper_py=paths.STEP4_WRAPPER,
            cores=(
                DEPOSITION_CORES if DEPOSITION_CORES else 0
            ),  # This constant dynamically determine the cpu core count of the machine it was running on
        )
        self._deposition_worker.log.connect(view.log)
        self._deposition_worker.progress.connect(view.set_progress)
        self._deposition_worker.finished.connect(self._on_deposition_finished)
        self._deposition_worker.error.connect(self._on_stage_error)
        self._deposition_worker.start()

    # ------------------------------------------------------------------
    # Stage 3 done / any stage failed
    # ------------------------------------------------------------------
    def _on_deposition_finished(self, mask_path, status):
        view = self._view
        if not view.is_running:
            return
        self._stop()

        if status != "success" or not os.path.exists(mask_path):
            return
        if view.should_load_results() and add_raster_layer(
            mask_path, "ML-DFI Depositional Zone"
        ):
            view.log("--> Loaded into map: ML-DFI Depositional Zone")

    def _on_stage_error(self, message):
        view = self._view
        if not view.is_running:
            return
        self._stop()
        self._cleanup_temp_rasters()
        view.log(f"\n[CRITICAL ERROR] {message}", LOG_ERROR)
        view.set_progress(0)
