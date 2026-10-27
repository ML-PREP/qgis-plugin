"""Stage 2 of the EIL pipeline: PINN susceptibility predictions.

Runs one prediction script in the EIL venv -- pinn_inference.py for slope-unit
models, grid_inference.py for grid models -- and hands over its final JSON
status line. A "PROGRESS <0-100>" line from the script moves the progress bar.
"""

import json
import subprocess

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ... import platform_utils as pu
from ...common.cancellable import CancellableProcessMixin

PROGRESS_PREFIX = "PROGRESS "


def _status_line(line):
    """The parsed dict if `line` is the script's final JSON status, else None."""
    try:
        response = json.loads(line)
    except json.JSONDecodeError:
        return None
    return response if isinstance(response, dict) and "status" in response else None


def _progress_line(line):
    """The percent if `line` is a "PROGRESS <n>" line, else None."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        return int(line[len(PROGRESS_PREFIX):])
    except ValueError:
        return None


class PINNPredictionWorker(CancellableProcessMixin, QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    def __init__(self, argv, title="PINN AI MODEL"):
        super().__init__()
        self._init_cancellation()
        self.argv = list(argv)
        self.title = title

    def run(self):
        try:
            self.log.emit(f"\n--- INITIATING {self.title} ---")
            self.log.emit("Loading TensorFlow and parsing features...")

            process = self._track(
                subprocess.Popen(
                    self.argv,
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
                percent = _progress_line(clean)
                if percent is not None:
                    self.progress.emit(percent)
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
