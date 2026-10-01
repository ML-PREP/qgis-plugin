"""Tests for managed virtualenv creation.

The Windows failure this covers:

    Command '[...python.exe, -m, venv, ...\\ls_detect_env]' returned non-zero
    exit status 1

`check=True` without `capture_output` discards the reason, leaving only the exit
code. Every failure here must instead carry the underlying output.
"""

import importlib
import os
import subprocess
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# env_setup uses `from . import platform_utils`, so it must be imported as part
# of a package -- the same way QGIS loads it. Registering a stand-in package
# keeps the test faithful to how the module is actually used.
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
es = importlib.import_module("mlprep_plugin.env_setup")


@pytest.fixture
def envs(tmp_path, monkeypatch):
    """Redirect the managed-env location away from the real ~/.mlprep_envs."""
    monkeypatch.setattr(pu, "ENVS_DIR", str(tmp_path))
    return tmp_path


class TestCreateVenv:
    def test_creates_a_working_interpreter(self, envs):
        # Act
        exe = es.create_venv(pu.base_python(), str(envs / "fresh"))

        # Assert
        assert es.interpreter_works(exe)
        assert es.has_pip(exe)

    def test_is_idempotent(self, envs):
        # Arrange
        first = es.create_venv(pu.base_python(), str(envs / "again"))

        # Act
        second = es.create_venv(pu.base_python(), str(envs / "again"))

        # Assert
        assert first == second

    def test_rebuilds_a_half_created_environment(self, envs):
        # Arrange -- a directory exists but holds no interpreter. The old code
        # tested for the directory, so this state was never repaired.
        broken = envs / "broken"
        (broken / "bin").mkdir(parents=True)
        (broken / "pyvenv.cfg").write_text("junk\n", encoding="utf-8")

        # Act
        exe = es.create_venv(pu.base_python(), str(broken))

        # Assert
        assert es.interpreter_works(exe)


class TestDiagnostics:
    def test_failure_names_interpreter_target_and_manual_command(self, envs):
        # Act
        with pytest.raises(RuntimeError) as excinfo:
            es.create_venv("/nonexistent/python3", str(envs / "nope"))

        # Assert -- the old error was just "returned non-zero exit status 1".
        message = str(excinfo.value)
        assert "/nonexistent/python3" in message
        assert "nope" in message
        assert "-m venv" in message

    def test_unstartable_command_does_not_leak_oserror(self, envs):
        # A missing executable must surface as the same diagnostic RuntimeError,
        # not as FileNotFoundError escaping the worker thread.
        with pytest.raises(RuntimeError):
            es.create_venv("/definitely/not/here", str(envs / "x"))

    def test_missing_requirements_file_is_reported(self, envs):
        exe = es.create_venv(pu.base_python(), str(envs / "reqs"))
        with pytest.raises(RuntimeError, match="Requirements file not found"):
            es.install_requirements(exe, str(envs / "absent.txt"))

    def test_pip_failure_includes_pip_output(self, envs):
        # Arrange
        exe = es.create_venv(pu.base_python(), str(envs / "pipfail"))
        req = envs / "bad.txt"
        req.write_text("this-package-does-not-exist-mlprep-xyz==9.9.9\n", encoding="utf-8")

        # Act / Assert
        with pytest.raises(RuntimeError) as excinfo:
            es.install_requirements(exe, str(req))
        assert "mlprep-xyz" in str(excinfo.value).lower()


class TestPipBootstrapDetection:
    """Distinguishing 'only pip failed' from 'the environment failed'.

    Only the former is safe to retry with --without-pip.
    """

    @pytest.mark.parametrize("output", [
        "Error: Command '['python.exe', '-Im', 'ensurepip', '--upgrade']' "
        "returned non-zero exit status 1.",
        "ModuleNotFoundError: No module named pip",
    ])
    def test_recognises_pip_bootstrap_failures(self, output):
        assert es._looks_like_pip_bootstrap_failure(output)

    @pytest.mark.parametrize("output", [
        "PermissionError: [WinError 5] Access is denied",
        "OSError: [Errno 28] No space left on device",
    ])
    def test_does_not_misread_real_failures(self, output):
        assert not es._looks_like_pip_bootstrap_failure(output)


class TestWithoutPipFallback:
    def test_environment_without_pip_is_detected(self, envs):
        # Arrange -- exactly the state the fallback produces.
        target = envs / "nopip"
        subprocess.run([pu.base_python(), "-m", "venv", "--without-pip", str(target)],
                       check=True, capture_output=True)
        exe = pu.venv_python("nopip")

        # Assert
        assert es.interpreter_works(exe)
        assert not es.has_pip(exe)

    @pytest.mark.slow
    def test_pip_can_be_bootstrapped_into_it(self, envs):
        # Network-dependent: this is the actual Windows remedy.
        target = envs / "bootstrap"
        subprocess.run([pu.base_python(), "-m", "venv", "--without-pip", str(target)],
                       check=True, capture_output=True)
        exe = pu.venv_python("bootstrap")

        es._bootstrap_pip(exe, pu.tool_environment(), lambda _m: None)

        assert es.has_pip(exe)


class TestSetupTriggerCondition:
    """The plugin decides whether to offer setup by testing the interpreters.

    Testing for the *directory* meant a run that failed part-way left both
    directories behind, after which the plugin silently never offered setup
    again while every pipeline still failed.
    """

    def test_half_created_environment_still_triggers_setup(self, envs):
        # Arrange -- directories present, no interpreter in either.
        for name in ("ls_detect_env", "venv"):
            (envs / name / ("Scripts" if pu.IS_WINDOWS else "bin")).mkdir(parents=True)
            (envs / name / "pyvenv.cfg").write_text("home = ...\n", encoding="utf-8")

        # Act
        directories_exist = all((envs / n).is_dir() for n in ("ls_detect_env", "venv"))
        interpreters_work = all(
            es.interpreter_works(pu.venv_python(n)) for n in ("ls_detect_env", "venv")
        )

        # Assert -- the old condition says "ready", the new one does not.
        assert directories_exist
        assert not interpreters_work

    def test_complete_environments_do_not_retrigger_setup(self, envs):
        # Arrange
        for name in ("ls_detect_env", "venv"):
            es.create_venv(pu.base_python(), str(envs / name))

        # Act / Assert
        assert all(
            es.interpreter_works(pu.venv_python(n)) for n in ("ls_detect_env", "venv")
        )


class TestProvisioningStamp:
    """A present interpreter is not evidence of a usable environment.

    OSGeo4W's `python -m venv` creates a working python.exe and only then fails
    to bootstrap pip. Readiness was inferred from the interpreter, so the plugin
    reported the environment ready and never offered setup, while nothing was
    actually installed.
    """

    def test_interpreter_without_pip_is_not_provisioned(self, envs, tmp_path):
        # Arrange -- exactly the state the Windows failure leaves behind.
        target = envs / "ls_detect_env"
        subprocess.run([pu.base_python(), "-m", "venv", "--without-pip", str(target)],
                       check=True, capture_output=True)
        requirements = tmp_path / "requirements_ls.txt"
        requirements.write_text("six\n", encoding="utf-8")

        # Act / Assert
        assert es.interpreter_works(pu.venv_python("ls_detect_env"))
        assert not es.has_pip(pu.venv_python("ls_detect_env"))
        assert not pu.venv_is_provisioned("ls_detect_env", str(requirements))

    def test_marking_provisioned_makes_it_ready(self, envs, tmp_path):
        # Arrange
        target = envs / "env"
        es.create_venv(pu.base_python(), str(target))
        requirements = tmp_path / "r.txt"
        requirements.write_text("six\n", encoding="utf-8")

        # Act
        es.mark_provisioned(str(target), str(requirements))

        # Assert
        assert pu.venv_is_provisioned("env", str(requirements))

    def test_changed_requirements_force_reinstall(self, envs, tmp_path):
        # Arrange
        target = envs / "env"
        es.create_venv(pu.base_python(), str(target))
        requirements = tmp_path / "r.txt"
        requirements.write_text("six\n", encoding="utf-8")
        es.mark_provisioned(str(target), str(requirements))

        # Act -- the pinned set changes.
        requirements.write_text("six\nnumpy\n", encoding="utf-8")

        # Assert
        assert not pu.venv_is_provisioned("env", str(requirements))

    def test_recreating_an_environment_clears_the_stamp(self, envs, tmp_path):
        # Arrange
        target = envs / "env"
        es.create_venv(pu.base_python(), str(target))
        requirements = tmp_path / "r.txt"
        requirements.write_text("six\n", encoding="utf-8")
        es.mark_provisioned(str(target), str(requirements))

        # Act -- wipe the interpreter so create_venv rebuilds.
        os.remove(pu.venv_python("env"))
        es.create_venv(pu.base_python(), str(target))

        # Assert -- a rebuilt environment must not inherit the old stamp.
        assert not pu.venv_is_provisioned("env", str(requirements))

    def test_missing_stamp_means_not_provisioned(self, envs):
        es.create_venv(pu.base_python(), str(envs / "env"))
        assert not pu.venv_is_provisioned("env")
