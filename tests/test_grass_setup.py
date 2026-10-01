"""The GRASS setup gate: what it waits on, and the installer scripts it runs.

The gate dialog itself needs QGIS, so these tests cover the pieces it is built
from: `grass_setup_status()` (what unlocks Continue), the installer command it
launches, and scripts/install_grass.sh run against a throwaway HOME.
"""

import importlib
import os
import shutil
import subprocess
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
ga = importlib.import_module("mlprep_plugin.grass_addons")

SHELL_SCRIPT = os.path.join(PLUGIN_DIR, "scripts", "install_grass.sh")
ADDON_MODULES = {
    "r.slopeunits.create",
    "r.slopeunits.clean",
    "r.slopeunits.metrics",
    "r.slopeunits.optimize",
}


class TestGrassSetupStatus:
    def test_addon_is_not_ready_without_grass(self, monkeypatch):
        # Arrange -- an addon dir left behind by an uninstalled GRASS.
        monkeypatch.setattr(pu, "find_grass", lambda: None)
        monkeypatch.setattr(ga, "bundled_addon_installed", lambda *a: True)

        # Act
        grass, addon_ready = ga.grass_setup_status()

        # Assert
        assert grass is None
        assert addon_ready is False

    def test_grass_without_addon_is_not_ready(self, monkeypatch):
        monkeypatch.setattr(pu, "find_grass", lambda: "/opt/grass/bin/grass")
        monkeypatch.setattr(ga, "bundled_addon_installed", lambda *a: False)

        assert ga.grass_setup_status() == ("/opt/grass/bin/grass", False)

    def test_grass_and_addon_is_ready(self, monkeypatch):
        monkeypatch.setattr(pu, "find_grass", lambda: "/opt/grass/bin/grass")
        monkeypatch.setattr(ga, "bundled_addon_installed", lambda *a: True)

        assert ga.grass_setup_status() == ("/opt/grass/bin/grass", True)

    def test_addon_check_asks_the_grass_that_was_found(self, monkeypatch):
        # Arrange
        seen = []
        monkeypatch.setattr(pu, "find_grass", lambda: "/found/grass")
        monkeypatch.setattr(
            pu, "grass_addon_installed",
            lambda module, grass_exe=None: seen.append(grass_exe) or "hit",
        )

        # Act
        ga.grass_setup_status()

        # Assert
        assert seen and set(seen) == {"/found/grass"}


class TestInstallerCommand:
    def test_both_installer_scripts_ship_with_the_plugin(self):
        scripts = os.path.join(PLUGIN_DIR, pu.GRASS_INSTALLER_DIR)
        assert os.path.isfile(os.path.join(scripts, "install_grass.sh"))
        assert os.path.isfile(os.path.join(scripts, "install_grass_windows.bat"))

    def test_returns_none_when_script_is_missing(self, tmp_path):
        assert pu.grass_installer_command(str(tmp_path)) is None

    def test_posix_runs_shell_script_through_bash(self, monkeypatch):
        # bash explicitly: a zip-extracted plugin loses the executable bit.
        monkeypatch.setattr(pu, "IS_WINDOWS", False)

        command = pu.grass_installer_command(PLUGIN_DIR)

        assert command == ["/bin/bash", SHELL_SCRIPT]

    def test_windows_runs_batch_file_through_cmd(self, monkeypatch):
        monkeypatch.setattr(pu, "IS_WINDOWS", True)

        command = pu.grass_installer_command(PLUGIN_DIR)

        assert command[:2] == ["cmd.exe", "/c"]
        assert command[2].endswith("install_grass_windows.bat")

    def test_macos_searches_the_managed_grass_env(self):
        assert os.path.join(pu.GRASS_ENV_DIR, "bin") in pu._SEARCH_DIRS["darwin"]
        assert pu.GRASS_ENV_DIR.startswith(pu.ENVS_DIR)


@pytest.mark.skipif(pu.IS_WINDOWS or not shutil.which("bash"),
                    reason="POSIX shell installer")
class TestShellInstaller:
    @pytest.fixture
    def fake_grass_env(self, tmp_path):
        """HOME and PATH with a stub `grass84` first on PATH."""
        home = tmp_path / "home"
        home.mkdir()
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        grass = fake_bin / "grass84"
        grass.write_text('#!/bin/sh\necho "fake grass $*"\n')
        grass.chmod(0o755)
        env = {"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"}
        return home, env

    @staticmethod
    def run_script(env, *args):
        return subprocess.run(
            ["bash", SHELL_SCRIPT, *args],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_script_has_valid_syntax(self):
        result = subprocess.run(["bash", "-n", SHELL_SCRIPT], capture_output=True)
        assert result.returncode == 0, result.stderr

    def test_rejects_unknown_option(self, fake_grass_env):
        _, env = fake_grass_env

        result = self.run_script(env, "--bogus")

        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_check_reports_missing_addon_and_changes_nothing(self, fake_grass_env):
        # Arrange
        home, env = fake_grass_env

        # Act
        result = self.run_script(env, "--check")

        # Assert
        assert result.returncode == 0
        assert "MISSING" in result.stdout
        assert not (home / ".grass8").exists()

    def test_installs_bundled_addon_where_the_plugin_looks(self, fake_grass_env):
        # Arrange
        home, env = fake_grass_env
        scripts = home / ".grass8" / "addons" / "scripts"

        # Act
        result = self.run_script(env, "--addon-only")

        # Assert
        assert result.returncode == 0, result.stdout
        names = set(os.listdir(scripts))
        assert ADDON_MODULES <= names
        assert not [n for n in names if n.endswith(".py")]
        assert os.stat(scripts / "r.slopeunits.create").st_mode & 0o111

    def test_installed_addon_is_detected_by_the_plugin(
            self, fake_grass_env, monkeypatch):
        # Arrange
        home, env = fake_grass_env
        addon_dir = home / ".grass8" / "addons"
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(addon_dir))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)

        # Act
        self.run_script(env, "--addon-only")

        # Assert -- the script and the plugin agree on the layout.
        assert ga.bundled_addon_installed()

    def test_removes_stale_py_copies(self, fake_grass_env):
        # Arrange
        home, env = fake_grass_env
        scripts = home / ".grass8" / "addons" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "r.slopeunits.create.py").write_text("stale\n")

        # Act
        self.run_script(env, "--addon-only")

        # Assert
        assert not (scripts / "r.slopeunits.create.py").exists()
