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


class TestGrassAddonBase:
    def test_child_processes_search_the_plugin_addon_dir(self, tmp_path, monkeypatch):
        # Regression: on macOS GRASS defaults to ~/Library/GRASS/<ver>/Addons,
        # so r.slopeunits installed by the plugin was "not found" at run time.
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))

        env = pu.tool_environment(base={})

        assert env["GRASS_ADDON_BASE"] == str(tmp_path)

    def test_overrides_an_inherited_addon_base(self, tmp_path, monkeypatch):
        # The plugin's checks and installs use grass_addon_dir(); GRASS must agree.
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))

        env = pu.tool_environment(base={"GRASS_ADDON_BASE": "/elsewhere"})

        assert env["GRASS_ADDON_BASE"] == str(tmp_path)


class TestScriptsUsePluginResolvedGrass:
    def test_tool_environment_exports_the_gate_s_grass(self, monkeypatch):
        monkeypatch.setattr(pu, "find_grass", lambda: "/qgis/bin/grass85")

        env = pu.tool_environment(base={})

        assert env[pu.ENV_GRASS_EXE] == "/qgis/bin/grass85"

    @pytest.mark.skipif(pu.IS_WINDOWS or not shutil.which("bash"),
                        reason="POSIX shell installer")
    def test_shell_script_prefers_mlprep_grass_exe(self, tmp_path):
        # Arrange -- a GRASS only the plugin knows about, nothing on PATH.
        grass = tmp_path / "custom" / "grass85"
        grass.parent.mkdir()
        grass.write_text("#!/bin/sh\n")
        grass.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin",
               "MLPREP_GRASS_EXE": str(grass)}

        # Act
        result = subprocess.run(["bash", SHELL_SCRIPT, "--check"],
                                capture_output=True, text=True, env=env, timeout=60)

        # Assert
        assert f"GRASS found:  {grass}" in result.stdout

    @pytest.mark.skipif(pu.IS_WINDOWS or not shutil.which("bash"),
                        reason="POSIX shell installer")
    def test_shell_script_installs_into_grass_addon_base(self, tmp_path):
        # Arrange
        grass = tmp_path / "grass84"
        grass.write_text("#!/bin/sh\n")
        grass.chmod(0o755)
        addon_base = tmp_path / "addons"
        home = tmp_path / "home"
        home.mkdir()
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin",
               "MLPREP_GRASS_EXE": str(grass),
               "GRASS_ADDON_BASE": str(addon_base)}

        # Act
        result = subprocess.run(["bash", SHELL_SCRIPT, "--addon-only"],
                                capture_output=True, text=True, env=env, timeout=60)

        # Assert
        assert result.returncode == 0, result.stdout
        assert (addon_base / "scripts" / "r.slopeunits.create").is_file()
        assert not (home / ".grass8").exists()


class TestWindowsBatchScript:
    """Static checks: the .bat cannot run here, so guard what broke before."""

    @pytest.fixture
    def bat(self):
        path = os.path.join(PLUGIN_DIR, "scripts", "install_grass_windows.bat")
        with open(path, newline="", encoding="ascii") as handle:
            return handle.read()

    def test_keeps_crlf_line_endings(self, bat):
        # cmd.exe mis-parses labels and blocks in LF-only batch files.
        assert "\n" not in bat.replace("\r\n", "")

    def test_knows_the_grass_qgis_bundles(self, bat):
        assert "grass85.bat" in bat and "grass86.bat" in bat

    def test_searches_qgis_bundled_grass(self, bat):
        assert "QGIS *" in bat and "apps\\grass\\grass*" in bat

    def test_prefers_plugin_resolved_grass(self, bat):
        assert "MLPREP_GRASS_EXE" in bat

    def test_every_call_and_goto_target_exists(self, bat):
        # A bad rewrite once deleted the script's main body; labels it still
        # jumped to (:addon_stage, :report) vanished with it.
        import re
        labels = set(re.findall(r"^:(\w+)", bat, re.MULTILINE))
        targets = set(re.findall(r"(?:call|goto)\s+:(\w+)", bat, re.IGNORECASE))
        assert targets - {"eof"} <= labels, targets - labels

    def test_main_flow_is_intact(self, bat):
        for step in ("curl.exe -L", '"%SETUP_PATH%" /S', ":addon_stage",
                     "g.extension extension=%ADDON%", ":manual_addon_install",
                     ":report", ":fail", ":usage"):
            assert step in bat, f"missing {step!r}"

    def test_main_flow_precedes_subroutines(self, bat):
        # The body must run before control reaches the subroutine labels.
        assert bat.index(":addon_stage") < bat.index("\r\n:find_grass\r\n")

    def test_addon_dir_follows_grass_addon_base(self, bat):
        # Only the fallback default may name the APPDATA path directly.
        assert bat.count("%APPDATA%\\GRASS8\\addons") == 1
        assert 'if not defined GRASS_ADDON_BASE set "GRASS_ADDON_BASE=' in bat


class TestAskGrassProbe:
    def run_probe(self, monkeypatch, is_windows):
        seen = {}

        def fake_run(command, **_kwargs):
            seen["command"] = command
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(pu, "IS_WINDOWS", is_windows)
        monkeypatch.setattr(pu.subprocess, "run", fake_run)
        monkeypatch.setattr(pu, "tool_environment", lambda: {})
        # The real kwargs use subprocess.CREATE_NO_WINDOW, absent off Windows.
        monkeypatch.setattr(pu, "subprocess_kwargs", lambda **_k: {})
        pu.clear_module_cache()
        pu._ask_grass_for_module("r.slopeunits.create", grass_exe="/usr/bin/grass")
        pu.clear_module_cache()
        return seen["command"]

    def test_uses_tmp_location_which_grass_8_3_supports(self, monkeypatch):
        # --tmp-project is GRASS 8.4+; Ubuntu 24.04 ships 8.3 and exits 2.
        command = self.run_probe(monkeypatch, is_windows=False)

        assert "--tmp-location" in command
        assert "--tmp-project" not in command

    def test_uses_python3_off_windows(self, monkeypatch):
        # Ubuntu and macOS have no bare `python`.
        command = self.run_probe(monkeypatch, is_windows=False)

        assert command[command.index("--exec") + 1] == "python3"

    def test_uses_python_on_windows(self, monkeypatch):
        command = self.run_probe(monkeypatch, is_windows=True)

        assert command[command.index("--exec") + 1] == "python"
