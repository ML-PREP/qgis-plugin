"""Blocking prompt shown until GRASS GIS and its r.slopeunits addon are ready.

The EIL slope-unit stage calls r.slopeunits.create inside GRASS, so the plugin
stops here rather than failing several steps later. Continue stays disabled
until both are detected. *Install automatically* runs the platform installer
script (scripts/install_grass.sh, or install_grass_windows.bat on Windows) and
streams its output; the dialog also re-checks on a timer, so installing by hand
in the background unlocks it without a restart.
"""

import os

from qgis.PyQt import QtGui, QtWidgets
from qgis.PyQt.QtCore import QProcess, QProcessEnvironment, QTimer

from . import grass_addons
from . import platform_utils as pu

POLL_INTERVAL_MS = 3000
COLOR_OK = "#1a7f37"
COLOR_MISSING = "#c0392b"
LOG_MIN_HEIGHT = 180


class GrassRequiredDialog(QtWidgets.QDialog):
    """Modal gate: accepted only once GRASS and the addon are found."""

    def __init__(self, plugin_dir, parent=None):
        super().__init__(parent)
        self._plugin_dir = plugin_dir
        self._grass_path = None
        self._process = None

        self.setWindowTitle("GRASS GIS Setup Required")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.recheck)
        self._timer.start()

        self.recheck()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        script = os.path.join(pu.GRASS_INSTALLER_DIR, pu.grass_installer_script_name())
        message = QtWidgets.QLabel(
            "<b>GRASS GIS 8.x and the r.slopeunits addon are required.</b><br><br>"
            "The MLPREP Detection Suite cannot finish setting up until both are "
            "installed on this machine.<br><br>"
            "<b>Install automatically</b> runs the bundled installer "
            f"(<code>{script}</code>), which installs whatever is missing. "
            f"{pu._hint('grass')}<br><br>"
            "This window checks again every few seconds. If GRASS is already "
            "installed somewhere unusual, use <i>Locate GRASS…</i>"
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self.status_grass = QtWidgets.QLabel()
        self.status_addon = QtWidgets.QLabel()
        for label in (self.status_grass, self.status_addon):
            label.setWordWrap(True)
            layout.addWidget(label)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(LOG_MIN_HEIGHT)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        layout.addLayout(self._build_buttons())

    def _build_buttons(self):
        buttons = QtWidgets.QHBoxLayout()

        self.button_install = QtWidgets.QPushButton("Install automatically")
        self.button_install.clicked.connect(self._run_installer)
        buttons.addWidget(self.button_install)

        self.button_locate = QtWidgets.QPushButton("Locate GRASS…")
        self.button_locate.clicked.connect(self._locate)
        buttons.addWidget(self.button_locate)

        self.button_recheck = QtWidgets.QPushButton("Re-check")
        self.button_recheck.clicked.connect(self._full_recheck)
        buttons.addWidget(self.button_recheck)
        buttons.addStretch(1)

        self.button_close = QtWidgets.QPushButton("Close Plugin")
        self.button_close.clicked.connect(self.reject)
        buttons.addWidget(self.button_close)

        self.button_continue = QtWidgets.QPushButton("Continue")
        self.button_continue.setDefault(True)
        self.button_continue.clicked.connect(self.accept)
        buttons.addWidget(self.button_continue)
        return buttons

    # -------------------------------------------------------------- checks

    def recheck(self):
        """Look for GRASS and the addon again; unlock Continue when both exist."""
        grass, addon_ready = grass_addons.grass_setup_status()

        # GRASS answers "is the addon installed?" from a cache. An answer given
        # before GRASS appeared (or from a different GRASS) is stale.
        if grass != self._grass_path:
            self._grass_path = grass
            pu.clear_module_cache()
            grass, addon_ready = grass_addons.grass_setup_status()

        self._show_status(self.status_grass, grass,
                          f"GRASS found: {grass}", "GRASS GIS not found yet.")
        self._show_status(self.status_addon, addon_ready,
                          "r.slopeunits addon installed.",
                          "r.slopeunits addon not installed yet.")

        installer_running = self._process is not None
        self.button_continue.setEnabled(bool(grass) and addon_ready
                                        and not installer_running)
        self.button_install.setEnabled(not (grass and addon_ready)
                                       and not installer_running)

    def _full_recheck(self):
        """Re-check, discarding cached answers from GRASS."""
        pu.clear_module_cache()
        self.recheck()

    @staticmethod
    def _show_status(label, is_ok, ok_text, missing_text):
        label.setText(f"✓ {ok_text}" if is_ok else f"✗ {missing_text}")
        color = COLOR_OK if is_ok else COLOR_MISSING
        label.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ----------------------------------------------------------- installer

    def _run_installer(self):
        command = pu.grass_installer_command(self._plugin_dir)
        if command is None:
            QtWidgets.QMessageBox.critical(
                self,
                "Installer missing",
                "The GRASS installer script is missing from the plugin:\n"
                f"  {os.path.join(self._plugin_dir, pu.GRASS_INSTALLER_DIR)}\n\n"
                "Reinstall the plugin, or install GRASS GIS 8.x by hand.",
            )
            return

        self.log_view.clear()
        self.log_view.setVisible(True)
        self._append_log("$ " + " ".join(command) + "\n")

        process = QProcess(self)
        process.setProcessEnvironment(self._installer_environment())
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_installer_output)
        process.finished.connect(self._installer_finished)
        process.errorOccurred.connect(self._installer_error)

        self._process = process
        self.recheck()
        process.start(command[0], command[1:])

    @staticmethod
    def _installer_environment():
        environment = QProcessEnvironment()
        for key, value in pu.tool_environment().items():
            environment.insert(key, value)
        return environment

    def _read_installer_output(self):
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardOutput())
        self._append_log(data.decode("utf-8", errors="replace"))

    def _append_log(self, text):
        self.log_view.moveCursor(QtGui.QTextCursor.End)
        self.log_view.insertPlainText(text)
        self.log_view.ensureCursorVisible()

    def _installer_finished(self, exit_code, _exit_status):
        self._read_installer_output()
        self._process = None
        self._append_log(
            "\nInstaller finished successfully.\n" if exit_code == 0
            else f"\nInstaller exited with code {exit_code}. "
                 "See the messages above.\n"
        )
        self._full_recheck()

    def _installer_error(self, error):
        # FailedToStart never emits finished(); every other error is followed
        # by finished(), which does the cleanup.
        if error != QProcess.FailedToStart or self._process is None:
            return
        self._append_log(f"\nCould not start the installer: "
                         f"{self._process.errorString()}\n")
        self._process = None
        self.recheck()

    # ------------------------------------------------------------- locate

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
        self._full_recheck()

        if not self._grass_path:
            QtWidgets.QMessageBox.warning(
                self,
                "GRASS not found",
                f"No GRASS launcher was recognised in\n{os.path.dirname(path)}",
            )

    def done(self, result):
        self._timer.stop()
        if self._process is not None:
            process, self._process = self._process, None
            process.finished.disconnect(self._installer_finished)
            process.kill()
            process.waitForFinished(1000)
        super().done(result)
