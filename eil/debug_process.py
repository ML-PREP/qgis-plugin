"""Experimental: a QProcess running alongside the EIL pipeline.

Starts scripts/infinite_printing.py, which prints forever, and streams its
output into the EIL log. It is a trial of QProcess as an alternative to the
QThread + subprocess workers, not part of the pipeline: nothing depends on it,
and it stops when the run is cancelled or the next run starts.
"""

from qgis.PyQt import QtCore
from qgis.PyQt.QtCore import QProcess

from .. import platform_utils as pu
from ..common.paths import INFINITE_PRINTING_SCRIPT
from ..log_panel import LEVEL_ERROR as LOG_ERROR
from ..log_panel import LEVEL_INFO as LOG_INFO


class InfinitePrintingProbe(QtCore.QObject):
    def __init__(self, log, parent=None):
        super().__init__(parent)
        self._log = log
        self._process = None

    def start(self):
        # One probe at a time: a previous run's process would otherwise keep
        # printing into the log alongside the new one.
        self.kill()
        self._process = self._new_process()
        try:
            self._process.start(
                pu.venv_python("venv"), ["-u", INFINITE_PRINTING_SCRIPT]
            )
        except Exception as exc:
            self._log(f"Err occured during running infinite printing: {exc}", LOG_ERROR)

    def kill(self):
        if self._process is not None:
            self._process.kill()

    def _new_process(self):
        process = QProcess(self)

        def relay_stdout():
            self._log(bytes(process.readAllStandardOutput()).decode(), LOG_INFO)

        def relay_stderr():
            self._log(bytes(process.readAllStandardError()).decode(), LOG_ERROR)

        def on_error(_error):
            self._log(f"infinite_printing failed: {process.errorString()}", LOG_ERROR)

        process.readyReadStandardOutput.connect(relay_stdout)
        process.readyReadStandardError.connect(relay_stderr)
        process.errorOccurred.connect(on_error)
        return process
