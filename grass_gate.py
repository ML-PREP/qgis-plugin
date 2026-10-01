"""Blocking prompt shown while GRASS GIS is not installed.

Environment setup installs the r.slopeunits addon into GRASS, so it cannot run
until GRASS exists. Rather than fail several steps later, the plugin stops here:
Continue stays disabled until GRASS is detected, and the dialog re-checks on a
timer so installing GRASS in the background unlocks it without a restart.
"""

import os

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import QTimer

from . import platform_utils as pu

POLL_INTERVAL_MS = 3000
COLOR_OK = "#1a7f37"
COLOR_MISSING = "#c0392b"


class GrassRequiredDialog(QtWidgets.QDialog):
    """Modal gate: accepted only once GRASS is found, rejected to close."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GRASS GIS Required")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.recheck)
        self._timer.start()

        self.recheck()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        message = QtWidgets.QLabel(
            "<b>GRASS GIS 8.x is not installed on this machine.</b><br><br>"
            "The MLPREP Detection Suite needs GRASS before it can finish "
            "setting up. Install it, then return to this window.<br><br>"
            f"<b>How to install:</b><br>{pu._hint('grass')}<br><br>"
            "This window checks again every few seconds. If GRASS is already "
            "installed somewhere unusual, use <i>Locate GRASS…</i>"
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        self.button_locate = QtWidgets.QPushButton("Locate GRASS…")
        self.button_locate.clicked.connect(self._locate)
        buttons.addWidget(self.button_locate)

        self.button_recheck = QtWidgets.QPushButton("Re-check")
        self.button_recheck.clicked.connect(self.recheck)
        buttons.addWidget(self.button_recheck)
        buttons.addStretch(1)

        self.button_close = QtWidgets.QPushButton("Close Plugin")
        self.button_close.clicked.connect(self.reject)
        buttons.addWidget(self.button_close)

        self.button_continue = QtWidgets.QPushButton("Continue")
        self.button_continue.setDefault(True)
        self.button_continue.clicked.connect(self.accept)
        buttons.addWidget(self.button_continue)
        layout.addLayout(buttons)

    def recheck(self):
        """Look for GRASS again and unlock Continue if it is found."""
        path = pu.find_grass()
        self.button_continue.setEnabled(bool(path))

        if path:
            self.status.setText(f"✓ GRASS found: {path}")
            self.status.setStyleSheet(f"color: {COLOR_OK}; font-weight: bold;")
            return

        self.status.setText("✗ GRASS not found yet.")
        self.status.setStyleSheet(f"color: {COLOR_MISSING}; font-weight: bold;")

    def _locate(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Locate GRASS GIS",
            "",
            "GRASS launcher (*.bat *.exe);;All files (*)" if pu.IS_WINDOWS
            else "All files (*)",
        )
        if not path:
            return

        pu.set_exe_override("grass", path)
        self.recheck()

        if not self.button_continue.isEnabled():
            QtWidgets.QMessageBox.warning(
                self,
                "GRASS not found",
                f"No GRASS launcher was recognised in\n{os.path.dirname(path)}",
            )

    def done(self, result):
        self._timer.stop()
        super().done(result)
