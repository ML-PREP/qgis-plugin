"""First-time installation of the managed Python environments."""

import os

from qgis.PyQt.QtCore import QThread, pyqtSignal

from .. import env_setup, grass_addons
from .. import platform_utils as pu

# (virtualenv, requirements file, label shown to the user)
ENVIRONMENTS = (
    ("ls_detect_env", "requirements_ls.txt", "Landslide"),
    ("venv", "requirements_eil.txt", "EIL"),
)


class SetupWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, base_dir):
        super().__init__()
        self.base_dir = base_dir
        self.envs_dir = pu.ENVS_DIR
        os.makedirs(self.envs_dir, exist_ok=True)

    def _provision(self, base_python, setup_env, venv_name, requirements, label):
        venv_dir = os.path.join(self.envs_dir, venv_name)
        requirements_path = os.path.join(self.base_dir, requirements)

        # Re-running pip on a ready environment costs minutes for nothing;
        # setup may run just to install the GRASS addon.
        if pu.venv_is_provisioned(venv_name, requirements_path):
            self.progress.emit(f"{label} environment already set up.")
            return

        self.progress.emit(f"Preparing {label} environment...")
        python_exe = env_setup.create_venv(
            base_python, venv_dir, env=setup_env, log=self.progress.emit
        )

        self.progress.emit(
            f"Installing {label} dependencies (this may take several minutes)..."
        )
        env_setup.install_requirements(
            python_exe, requirements_path, env=setup_env, log=self.progress.emit
        )
        env_setup.mark_provisioned(venv_dir, requirements_path)

    def run(self):
        try:
            base_python = pu.base_python()
        except RuntimeError as exc:
            self.finished.emit(False, str(exc))
            return

        self.progress.emit(f"Using interpreter: {base_python}")
        setup_env = pu.tool_environment()

        try:
            for venv_name, requirements, label in ENVIRONMENTS:
                self._provision(base_python, setup_env, venv_name, requirements, label)

            # The GRASS gate already ensured GRASS and the addon; this offline
            # copy only repairs an addon removed since.
            self.progress.emit("Installing GRASS addon r.slopeunits...")
            grass_addons.ensure_bundled_addon(self.base_dir, log=self.progress.emit)

            self.finished.emit(True, "Setup Complete!")
        except Exception as exc:
            self.finished.emit(False, str(exc))
