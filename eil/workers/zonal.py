"""Stage 1 of the EIL pipeline: slope units and zonal statistics (Go engine)."""

import os
import subprocess
import traceback

from ...log_panel import LEVEL_INFO, LEVEL_WARNING
from qgis.PyQt.QtCore import QThread, pyqtSignal

from ... import platform_utils as pu
from ...common.cancellable import CancellableProcessMixin
from ...common.paths import PLUGIN_DIR
from . import zonal_progress
from .commands import build_zonal_args

ENGINE_NAME = "zonal"


class GoParallelZonalWorker(CancellableProcessMixin, QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        go_project_dir,
        dem_path,
        input_raster_folder,
        output_folder,
        venv_python,
        target_crs,
        total_rasters,
        params,
    ):
        super().__init__()
        self._init_cancellation()
        self.go_project_dir = go_project_dir
        self.dem_path = dem_path
        self.input_raster_folder = input_raster_folder
        self.output_folder = output_folder
        self.venv_python = venv_python
        self.target_crs = target_crs
        self.total_rasters = total_rasters
        self.params = params

    def _engine_command(self):
        """Launch command for the engine, or None after reporting why not.

        Prefers the cross-compiled engine shipped under bin/<goos>_<goarch>/.
        Falling back to "go run ." keeps the source tree usable for
        development; end users never have a Go toolchain installed.
        """
        engine = pu.bundled_binary(PLUGIN_DIR, ENGINE_NAME)
        if engine:
            return [engine]

        go_exe = pu.find_exe("go", "go")
        if not go_exe:
            expected = os.path.join(
                PLUGIN_DIR, "bin", pu.platform_tag(), ENGINE_NAME + pu.EXE_SUFFIX
            )
            self.error.emit(
                f"Zonal engine not available for this platform "
                f"({pu.platform_tag()}).\n\nExpected:\n{expected}\n\n"
                "Reinstall the plugin, or install the Go toolchain to "
                "build it from source.",
                LEVEL_INFO,
            )
            return None

        self.log.emit(
            "   > No bundled engine for this platform; "
            "building from source with the Go toolchain.",
            LEVEL_WARNING,
        )
        return [go_exe, "run", "."]

    def _stream_output(self, process):
        """Relay engine output to the log and progress bar until it exits."""
        step = zonal_progress.zonal_step(self.total_rasters)
        current = 0.0
        try:
            for line in iter(process.stdout.readline, ""):
                clean_line = line.strip()
                if not clean_line:
                    continue
                self.log.emit(f"   > GO: {clean_line}")
                current, value = zonal_progress.advance(current, clean_line, step)
                if value is not None:
                    self.progress.emit(value)
        except Exception as exc:
            # The engine keeps running; its exit code still decides the outcome.
            self.log.emit(f"[Warning] Lost the Go engine's output stream: {exc}")

    def run(self):
        try:
            self.log.emit("\n--- PASSING BATON TO GO ENGINE ---")

            cmd = self._engine_command()
            if cmd is None:
                return
            cmd += build_zonal_args(
                self.dem_path,
                self.input_raster_folder,
                self.output_folder,
                self.venv_python,
                self.target_crs,
                self.params,
            )
            self.log.emit(f"Executing: {' '.join(cmd)}")

            # Exports MLPREP_GRASS_EXE / MLPREP_GDAL_BIN so the engine reuses
            # the executables already resolved here instead of re-deriving them.
            process = self._track(
                subprocess.Popen(
                    cmd,
                    cwd=self.go_project_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=pu.tool_environment(),
                    **pu.subprocess_kwargs(),
                )
            )
            self._stream_output(process)
            process.wait()

            if self.is_cancelled:
                self.log.emit("--- GO ENGINE STOPPED ---")
                return

            if process.returncode == 0:
                self.log.emit("--- GO PARALLEL PIPELINE COMPLETE ---")
                self.progress.emit(zonal_progress.COMPLETE)
                self.finished.emit(self.output_folder, "success")
            else:
                self.error.emit(
                    f"Go engine crashed with exit code {process.returncode}"
                )

        except Exception as e:
            if self.is_cancelled:
                return
            self.error.emit(f"Go Subprocess Error:\n{str(e)}\n{traceback.format_exc()}")
