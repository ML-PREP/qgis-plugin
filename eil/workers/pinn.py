"""Stage 2 of the EIL pipeline: PINN susceptibility predictions."""

import json
import subprocess

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ... import platform_utils as pu
from ...common.cancellable import CancellableProcessMixin


def _status_line(line):
    """The parsed dict if `line` is the script's final JSON status, else None."""
    try:
        response = json.loads(line)
    except json.JSONDecodeError:
        return None
    return response if isinstance(response, dict) and "status" in response else None


class PINNPredictionWorker(CancellableProcessMixin, QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    def __init__(
        self, python_exe, script_path, input_gpkg, model_path, output_gpkg, dem_path
    ):
        super().__init__()
        self._init_cancellation()
        self.python_exe = python_exe
        self.script_path = script_path
        self.input_gpkg = input_gpkg
        self.model_path = model_path
        self.output_gpkg = output_gpkg
        self.dem_path = dem_path

    def run(self):
        try:
            self.log.emit("\n--- INITIATING PINN AI MODEL ---")
            self.log.emit("Loading TensorFlow and parsing features...")

            cmd = [
                self.python_exe,
                self.script_path,
                self.input_gpkg,
                self.model_path,
                self.output_gpkg,
                self.dem_path,
            ]
            process = self._track(
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # merge stderr into stdout
                    **pu.subprocess_kwargs(),
                )
            )

            response = None
            for line in iter(process.stdout.readline, ""):
                clean = line.strip()
                if not clean:
                    continue
                status = _status_line(clean)
                if status is not None:
                    response = status
                    continue
                self.log.emit(f"   > PINN: {clean}")

            process.wait()

            if self.is_cancelled:
                self.log.emit("--- AI MODEL STOPPED ---")
                return

            self._report(response)

        except Exception as e:
            if self.is_cancelled:
                return
            self.log.emit(f"Worker Error: {str(e)}")
            self.finished.emit({}, "error")

    def _report(self, response):
        if response is None:
            self.log.emit("❌ AI Script produced no final status line.")
            self.finished.emit({}, "error")
            return

        if response.get("status") == "success":
            self.log.emit("✅ AI Predictions generated successfully!")
            self.finished.emit(response, "success")
            return

        self.log.emit(f"❌ AI Prediction Failed:\n{response.get('message')}")
        self.log.emit(response.get("traceback", ""))
        self.finished.emit({}, "error")
