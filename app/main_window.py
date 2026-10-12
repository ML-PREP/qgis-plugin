"""The plugin window: hosts the feature tabs and gates first-time setup."""

import os

from qgis.PyQt import QtCore, QtWidgets
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMainWindow, QMessageBox, QProgressDialog, QTabWidget

from .. import grass_addons
from .. import platform_utils as pu
from ..common.paths import PLUGIN_DIR
from ..common.roi import admin_boundaries
from ..dependency_panel import DependencyPanel
from ..eil.tab import EilTab
from ..grass_gate import GrassRequiredDialog
from ..landslide.tab import LandslideTab
from .setup_worker import SetupWorker
from .styles import MASTER_STYLESHEET

# One-shot delay before the setup check, so the window paints before any
# modal prompt appears over it.
SETUP_CHECK_DELAY_MS = 500


class UnifiedPluginDialog(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MLPREP Detection Suite")

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.main_tabs = QTabWidget(central)
        layout.addWidget(self.main_tabs)

        # Resolves TauDEM/MPI/GRASS/GDAL up front so a missing program is
        # reported before a run starts rather than minutes into one.
        self.dependency_panel = DependencyPanel(PLUGIN_DIR, self)

        boundaries = admin_boundaries()
        self.landslide = LandslideTab(self, boundaries)
        self.eil = EilTab(
            self,
            self.dependency_panel,
            self.show_dependencies,
            boundaries["Municipality"],
        )

        self.main_tabs.addTab(self.landslide.widget, "Landslide Detection")
        self.main_tabs.addTab(self.eil.widget, "EIL Detection")
        self.dependency_tab_index = self.main_tabs.addTab(
            self.dependency_panel, "Dependencies"
        )

        self.dependency_panel.statusChanged.connect(self._on_dependencies_changed)

        # The GUI thread should be blocked (No user interaction should be permitted. Should, wait until check_and_setup_environments is finished)
        self.dependency_panel.setupRequested.connect(
            lambda: self.check_and_setup_environments(force=True)
        )
        self._on_dependencies_changed()

        self.setStyleSheet(MASTER_STYLESHEET)

        QtCore.QTimer.singleShot(
            SETUP_CHECK_DELAY_MS, self.check_and_setup_environments
        )

    def show_dependencies(self):
        self.main_tabs.setCurrentIndex(self.dependency_tab_index)

    def _on_dependencies_changed(self):
        """Reflect dependency state in the tab label and the EIL Run button."""
        missing = self.dependency_panel.missing_for()
        self.main_tabs.setTabText(
            self.dependency_tab_index,
            "Dependencies" if not missing else f"⚠ Dependencies ({len(missing)})",
        )
        self.eil.refresh_run_button()

    # ------------------------------------------------------------------
    # First-time setup
    # ------------------------------------------------------------------
    def check_and_setup_environments(self, force=False):
        # The EIL stage runs r.slopeunits inside GRASS: nothing proceeds until
        # both are installed.
        if not self._ensure_grass_ready():
            return

        # A present interpreter is not evidence of a usable environment:
        # OSGeo4W's venv creates a working python.exe and only then fails to
        # bootstrap pip, so readiness is recorded explicitly after the
        # requirements install rather than inferred from what exists on disk.
        venvs_ready = all(
            pu.venv_is_provisioned(name, os.path.join(PLUGIN_DIR, requirements))
            for name, requirements in pu.ENV_REQUIREMENTS.items()
        )
        if venvs_ready and not force:
            return

        reply = QMessageBox.question(
            self,
            "First Time Setup Required",
            "The MLPREP Detection Suite needs two Python environments.\n\n"
            "They are created under:\n"
            f"  {pu.ENVS_DIR}\n\n"
            "This downloads several hundred MB and takes a few minutes. "
            "An internet connection is required.\n\n"
            "Install them now?",
            QMessageBox.Yes | QMessageBox.No,
        )

        # I dont think we need to ask user for this (The plugin wont be usable if venv deps are not installed)
        if reply == QMessageBox.Yes:
            self._start_setup()

    def _ensure_grass_ready(self):
        """Block on a modal prompt until GRASS and r.slopeunits are found.

        The prompt installs them via scripts/install_grass.sh (or the Windows
        .bat). Declining closes the plugin.
        """
        grass, addon_ready = grass_addons.grass_setup_status()
        if grass and addon_ready:
            return True

        gate = GrassRequiredDialog(PLUGIN_DIR, self)
        accepted = gate.exec_() == QtWidgets.QDialog.Accepted
        self.dependency_panel.refresh()
        if not accepted:
            self.close()
        return accepted

    def _start_setup(self):
        self.setup_dialog = QProgressDialog("Initializing Setup...", None, 0, 0, self)
        self.setup_dialog.setWindowTitle("Installing Dependencies")
        self.setup_dialog.setWindowModality(Qt.WindowModal)
        self.setup_dialog.setCancelButton(None)
        self.setup_dialog.show()

        self.setup_worker = SetupWorker(PLUGIN_DIR)
        self.setup_worker.progress.connect(self.setup_dialog.setLabelText)
        self.setup_worker.finished.connect(self._on_setup_finished)
        self.setup_worker.start()

    def _on_setup_finished(self, success, message):
        self.setup_dialog.close()

        # Re-resolve so the Dependencies tab and the Run buttons reflect the
        # new state without needing a restart.
        self.dependency_panel.refresh()

        if success:
            QMessageBox.information(
                self,
                "Setup Complete",
                "Python environments and the GRASS addon are ready.",
            )
        else:
            QMessageBox.critical(self, "Setup Failed", f"An error occurred:\n{message}")
            self.show_dependencies()
