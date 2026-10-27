"""Sequencing of the EIL pipeline's stages.

    slope units + zonal stats (Go)  ->  seam cleanup  ->  PINN predictions
        ->  dashboard  ->  depositional routing (TauDEM + C++/MPI, optional)

A grid model (model/pinn_models.json "kind": "grid") skips slope units and
zonal statistics: grid_inference.py resamples the inputs onto the model's
grid and predicts every cell, then the dashboard and routing run as above.

Each stage runs on a worker thread; its completion signal starts the next.
The tab owns the "running" flag: a stage reporting in after a cancel finds it
cleared and is ignored.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from qgis.PyQt import QtCore, QtWidgets

from .. import platform_utils as pu
from ..common import paths
from ..common.qgis_layers import add_raster_layer, add_vector_layer
from ..common.roi import RoiSelection
from ..log_panel import LEVEL_ERROR as LOG_ERROR
from ..log_panel import LEVEL_SUCCESS as LOG_SUCCESS
from ..log_panel import LEVEL_WARNING as LOG_WARNING
from ..pinn_grid.inputs import missing_inputs as missing_grid_inputs
from . import progress, roi_clip
from .completion import completion_message
from .debug_process import InfinitePrintingProbe
from .params import (
    DEPOSITION_CORES,
    DEPOSITION_OUTPUT_SUBDIR,
    SLOPE_UNIT_AREA_MIN,
    ZONAL_PARAMS,
)
from .pinn_models import KIND_GRID, KIND_SLOPE_UNIT
from .seams import eliminate_seams
from .workers import depzone_commands as depzone
from .workers.deposition import MLDFIDepositionWorker
from .workers.pinn import PINNPredictionWorker
from .workers.zonal import GoParallelZonalWorker


@dataclass(frozen=True)
class EilRunRequest:
    dem_path: str
    dem_crs: str
    rasters: dict = field(default_factory=dict)
    output_folder: str = ""
    # Not a PINN input: only the depositional zone's ML-DFI model reads it.
    ndvi_path: str = ""
    # When set, the layers shown on the map are clipped to this admin unit.
    roi: Optional[RoiSelection] = None
    # The PINN model chosen from model/pinn_models.json: a .keras file, or
    # for a grid model its folder of folds and the grid cell size.
    model_path: str = ""
    model_kind: str = KIND_SLOPE_UNIT
    cell_size_m: float = 0.0

    @property
    def is_grid(self):
        return self.model_kind == KIND_GRID


SLOPE_UNIT_STAGE = (
    "Building slope units",
    "Tiling the DEM, delineating slope units and computing zonal "
    "statistics for each selected parameter.",
)
GRID_STAGE = (
    "Rasterizing Inputs",
    "Resampling every input" "Uses grid based dataset instead of Slope Unit (SU)",
)


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
        self._roi_boundary = None
        self._shown_progress = 0
        self._probe = InfinitePrintingProbe(view.log, parent=self)

    # ------------------------------------------------------------------
    # Start / cancel
    # ------------------------------------------------------------------
    def start(self, request):
        view = self._view
        self._request = request
        self._reset_progress()
        stage = GRID_STAGE if request.is_grid else SLOPE_UNIT_STAGE

        view.log(f"[Input DEM]: {os.path.basename(request.dem_path)}")
        view.set_running(True, *stage)
        if not self._prepare_roi(request, stage):
            self._stop()
            return
        if request.is_grid:
            self._start_grid(request)
        else:
            self._start_zonal(request)

    def _start_zonal(self, request):
        view = self._view
        params = dict(ZONAL_PARAMS)
        view.log(f"[Slope Params]: {params}")
        self._temp_raster_dir = _stage_rasters(request.rasters)

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

        self._zonal_worker.progress.connect(self._on_zonal_progress)
        self._zonal_worker.log.connect(view.log)
        self._zonal_worker.error.connect(self._on_stage_error)
        self._zonal_worker.finished.connect(self._on_zonal_finished)
        self._zonal_worker.start()

    def _prepare_roi(self, request, stage):
        """Extract the clipping boundary; False (after logging) if it is unusable.

        Checked before the slow stages so a region outside the DEM fails in
        seconds rather than after the whole run.
        """
        self._roi_boundary = None
        if request.roi is None:
            return True

        view = self._view
        label = request.roi.choice.label
        view.set_stage("Preparing region of interest", f"Extracting {label}.")
        QtWidgets.QApplication.processEvents()

        boundary = roi_clip.extract_boundary(
            request.roi, request.dem_crs, request.output_folder, view.log
        )
        if boundary is None:
            return False
        if not roi_clip.overlaps_raster(boundary, request.dem_path):
            view.log(
                f"[ERROR] {label} does not overlap the DEM; pick a region inside it.",
                LOG_ERROR,
            )
            return False

        view.log(f"[ROI]: results will be clipped to {label}")
        view.set_stage(*stage)
        self._roi_boundary = boundary
        return True

    def _clip_vector(self, path, label):
        """`path` clipped to the ROI, or `path` itself when there is no ROI."""
        if not self._roi_boundary or not path or not os.path.exists(path):
            return path
        return roi_clip.clip_vector(path, self._roi_boundary, self._view.log, label)

    def _clip_raster(self, path, label):
        if not self._roi_boundary or not os.path.exists(path):
            return path
        return roi_clip.clip_raster(path, self._roi_boundary, self._view.log, label)

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
        self._reset_progress()

    def _cleanup_temp_rasters(self):
        if self._temp_raster_dir and os.path.exists(self._temp_raster_dir):
            shutil.rmtree(self._temp_raster_dir, ignore_errors=True)
        self._temp_raster_dir = None

    def _stop(self):
        self._view.set_running(False)

    # ------------------------------------------------------------------
    # Progress: each stage reports 0-100 of its own work (see progress.py)
    # ------------------------------------------------------------------
    def _report(self, stage, percent):
        self._shown_progress = progress.advance(self._shown_progress, stage, percent)
        self._view.set_progress(self._shown_progress)

    def _reset_progress(self):
        self._shown_progress = 0
        self._view.set_progress(0)

    def _finish(self, routing_ran):
        """End a successful run: full bar, idle tab, then tell the user."""
        self._shown_progress = progress.COMPLETE
        self._view.set_progress(progress.COMPLETE)
        self._stop()
        roi = self._request.roi
        self._view.show_completion(
            *completion_message(
                self._request.output_folder,
                routing_ran,
                roi.choice.label if roi and self._roi_boundary else None,
            )
        )

    # Declared slots, not lambdas: a slot runs on this object's (GUI) thread,
    # while a lambda could touch the widgets from the worker thread.
    @QtCore.pyqtSlot(int)
    def _on_zonal_progress(self, percent):
        self._report(progress.ZONAL, percent)

    @QtCore.pyqtSlot(int)
    def _on_grid_progress(self, percent):
        self._report(progress.GRID, percent)

    @QtCore.pyqtSlot(int)
    def _on_deposition_progress(self, percent):
        self._report(progress.DEPOSITION, percent)

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
        self._report(progress.SEAMS, 0)
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
        slope_units = self._clip_vector(slope_units, "Slope Units")
        if os.path.exists(slope_units) and view.should_load_results():
            if self._roi_boundary:
                self._load_vector(self._roi_boundary, "EIL Region of Interest")
            self._load_vector(slope_units, "EIL Slope Units")
        # The two layers take about equally long.
        self._report(progress.SEAMS, 50)
        QtWidgets.QApplication.processEvents()

        features = self._clean_seams(
            output_folder,
            "Merged_PINN_Features.gpkg",
            "Cleaned_PINN_Features.gpkg",
            "PINN Features",
        )
        self._report(progress.SEAMS, 100)

        # Cancel may have been clicked while seam removal was blocking.
        if not view.is_running:
            return

        if not os.path.exists(features):
            self._stop()
            return

        if not os.path.exists(self._request.model_path):
            view.log(f"\n[WARNING] AI Model not found at: {self._request.model_path}")
            if view.should_load_results():
                self._load_vector(features, "Merged PINN Features")
            self._stop()
            return

        self._start_pinn(features, output_folder)

    def _start_pinn(self, features, output_folder):
        self._report(progress.PINN, 0)
        self._view.set_stage(
            "Running AI model",
            "Loading TensorFlow and predicting susceptibility for every slope unit.",
        )
        self._pinn_worker = PINNPredictionWorker(
            [
                self._venv_python,
                paths.PINN_SCRIPT,
                features,
                self._request.model_path,
                os.path.join(output_folder, "Final_Hazard_Map.gpkg"),
                self._request.dem_path,
            ]
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

        if status != "success":
            self._stop()
            return
        self._report(progress.PINN, 100)

        view.log("\n✅ AI Predictions generated successfully!", LOG_SUCCESS)
        # The dashboard ranks only what the map shows; routing below keeps
        # reading the PINN's full-extent rasters.
        hazard_map = self._clip_vector(result.get("output"), "AI Predictions")
        if view.should_load_results():
            self._load_vector(hazard_map, "EIL AI Predictions")

        view.dashboard.update(hazard_map)
        self._continue_to_deposition(result)

    # ------------------------------------------------------------------
    # Grid models: one stage instead of slope units + seams + PINN
    # ------------------------------------------------------------------
    def _grid_argv(self, request):
        argv = [
            self._venv_python,
            paths.GRID_SCRIPT,
            "--dem",
            request.dem_path,
            "--model-dir",
            request.model_path,
            "--cell-size",
            str(request.cell_size_m),
            "--output-dir",
            request.output_folder,
        ]
        for name, path in request.rasters.items():
            argv += ["--raster", f"{name}={path}"]
        boundary = self._view.dashboard.municipality_boundary
        if boundary:
            argv += [
                "--municipalities",
                boundary["path"],
                "--municipality-field",
                boundary["col"],
            ]
        if self._roi_boundary:
            argv += ["--tally-mask", self._roi_boundary]
        return argv

    def _start_grid(self, request):
        view = self._view
        missing = missing_grid_inputs(request.rasters)
        if missing:
            view.log(
                "[ERROR] The grid model needs every EIL input; missing: "
                + ", ".join(missing),
                LOG_ERROR,
            )
            self._stop()
            return
        view.log(f"[Grid]: {request.cell_size_m:g} m cells, no slope units")
        self._report(progress.GRID, 0)
        self._pinn_worker = PINNPredictionWorker(
            self._grid_argv(request), title="GRID PINN AI MODEL"
        )
        self._pinn_worker.log.connect(view.log)
        self._pinn_worker.progress.connect(self._on_grid_progress)
        self._pinn_worker.finished.connect(self._on_grid_finished)
        self._pinn_worker.start()

    def _on_grid_finished(self, result, status):
        view = self._view
        if not view.is_running:
            return
        if status != "success":
            self._stop()
            return
        self._report(progress.GRID, 100)

        view.log("\n✅ Grid AI Predictions generated successfully!", LOG_SUCCESS)
        hazard = self._clip_raster(result.get("hazard_raster", ""), "AI Predictions")
        if view.should_load_results():
            if self._roi_boundary:
                self._load_vector(self._roi_boundary, "EIL Region of Interest")
            self._load_raster(hazard, "EIL AI Predictions (grid)")
        view.dashboard.show_tallies(result.get("municipality_tallies"))
        self._continue_to_deposition(result)

    # ------------------------------------------------------------------
    # After either route: depositional routing
    # ------------------------------------------------------------------
    def _continue_to_deposition(self, result):
        fos_inputs = depzone.fos_inputs_from(result, self._request.rasters)
        if self._deposition_blocked(fos_inputs):
            self._finish(routing_ran=False)
            return
        self._start_deposition(fos_inputs)

    def _deposition_blocked(self, fos_inputs):
        """True (after logging why) when routing cannot run.

        Deposition is a bonus stage after the result already exists, so a
        missing piece skips it with an explanation rather than a dialog.
        """
        reason = depzone.skip_reason(self._request.ndvi_path, fos_inputs)
        if reason:
            self._view.log(
                f"\n[Skipped] The depositional zone needs {reason}.", LOG_WARNING
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

    def _start_deposition(self, fos_inputs):
        view = self._view
        request = self._request
        view.log("\n--- INITIATING DYNAMIC-ALPHA DEPOSITION ROUTING ---")
        view.set_stage(
            "Routing deposition zones",
            "TauDEM hydrology, ML-DFI model, FoS sources, MPI runout and cleanup.",
        )
        self._deposition_worker = MLDFIDepositionWorker(
            dem_path=request.dem_path,
            ndvi_path=request.ndvi_path,
            fos_inputs=fos_inputs,
            python_exe=self._venv_python,
            output_dir=os.path.join(request.output_folder, DEPOSITION_OUTPUT_SUBDIR),
            publish_dir=request.output_folder,
            cores=DEPOSITION_CORES,
        )
        self._deposition_worker.log.connect(view.log)
        self._deposition_worker.progress.connect(self._on_deposition_progress)
        self._deposition_worker.finished.connect(self._on_deposition_finished)
        self._deposition_worker.error.connect(self._on_stage_error)
        self._deposition_worker.start()

    # ------------------------------------------------------------------
    # Stage 3 done / any stage failed
    # ------------------------------------------------------------------
    def _on_deposition_finished(self, publish_dir, status):
        view = self._view
        if not view.is_running:
            return
        final = os.path.join(publish_dir, depzone.FINAL_RASTER)
        if status != "success" or not os.path.exists(final):
            self._stop()
            return
        shown = (
            ("ML_DFI_Depositional_Zone.tif", "ML-DFI Depositional Zone"),
            (depzone.FINAL_RASTER, "PostDep_DepZone"),
        )
        for file_name, layer_name in shown:
            path = self._clip_raster(os.path.join(publish_dir, file_name), layer_name)
            if view.should_load_results():
                self._load_raster(path, layer_name)
        self._finish(routing_ran=True)

    def _load_raster(self, path, layer_name):
        if os.path.exists(path) and add_raster_layer(path, layer_name):
            self._view.log(f"--> Loaded into map: {layer_name}")

    def _on_stage_error(self, message):
        view = self._view
        if not view.is_running:
            return
        self._stop()
        self._cleanup_temp_rasters()
        view.log(f"\n[CRITICAL ERROR] {message}", LOG_ERROR)
        self._reset_progress()
