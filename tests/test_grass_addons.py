"""Offline installation of the bundled r.slopeunits addon.

`g.extension` fetches precompiled addons from wingrass.fsv.cvut.cz on Windows,
which fails outright when that host cannot be resolved:

    ERROR: Cannot open URL <...r.slopeunits.zip>:
    <urlopen error [Errno 11001] getaddrinfo failed>

The modules are pure Python, so they ship with the plugin and install by copy.
"""

import importlib
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
ga = importlib.import_module("mlprep_plugin.grass_addons")

REQUIRED = {
    "r.slopeunits.create",
    "r.slopeunits.clean",
    "r.slopeunits.metrics",
    "r.slopeunits.optimize",
}


class TestVendoredCopy:
    def test_all_modules_are_bundled(self):
        names = {
            os.path.splitext(os.path.basename(p))[0]
            for p in ga.vendored_modules(PLUGIN_DIR)
        }
        assert REQUIRED <= names

    def test_the_module_the_engine_calls_is_present(self):
        # main.go invokes r.slopeunits.create specifically.
        target = os.path.join(ga.vendored_dir(PLUGIN_DIR), "r.slopeunits.create.py")
        assert os.path.isfile(target)
        assert os.path.getsize(target) > 1000

    def test_attribution_is_kept(self):
        readme = os.path.join(ga.vendored_dir(PLUGIN_DIR), "README.md")
        text = open(readme, encoding="utf-8").read()
        assert "GRASS Development Team" in text
        assert "GPL" in text


class TestOfflineInstall:
    def test_installs_every_module(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))

        # Act
        installed = ga.install_bundled_addon(PLUGIN_DIR)

        # Assert
        assert REQUIRED <= set(installed)
        scripts = tmp_path / "scripts"
        expected = "r.slopeunits.create" + (".py" if pu.IS_WINDOWS else "")
        assert (scripts / expected).is_file()

    def test_installed_addon_is_then_detected(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)
        pu.clear_module_cache()
        assert not pu.grass_addon_installed("r.slopeunits.create")

        # Act
        ga.install_bundled_addon(PLUGIN_DIR)

        # Assert -- found on the filesystem alone, without invoking GRASS.
        assert pu.grass_addon_installed("r.slopeunits.create")

    @pytest.mark.skipif(pu.IS_WINDOWS, reason="POSIX permission model")
    def test_modules_are_executable_on_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        ga.install_bundled_addon(PLUGIN_DIR)
        target = tmp_path / "scripts" / "r.slopeunits.create"
        assert os.stat(target).st_mode & 0o111

    @pytest.mark.skipif(pu.IS_WINDOWS, reason="POSIX lookup rules")
    def test_scripts_have_no_py_extension_on_posix(self, tmp_path, monkeypatch):
        # GRASS on POSIX execs `r.slopeunits.create` from PATH by exact name;
        # only Windows resolves a .py. g.extension installs them the same way.
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        ga.install_bundled_addon(PLUGIN_DIR)
        names = os.listdir(tmp_path / "scripts")
        assert REQUIRED <= set(names)
        assert not [n for n in names if n.endswith(".py")]

    @pytest.mark.skipif(pu.IS_WINDOWS, reason="POSIX lookup rules")
    def test_py_suffixed_copy_is_not_reported_installed_on_posix(
            self, tmp_path, monkeypatch):
        # Arrange -- what the old installer left behind: GRASS cannot run it.
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)
        pu.clear_module_cache()
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        stale = scripts / "r.slopeunits.create.py"
        stale.write_text("#!/usr/bin/env python3\n")
        stale.chmod(0o755)

        # Act / Assert
        assert not pu.grass_addon_installed("r.slopeunits.create")

    @pytest.mark.skipif(pu.IS_WINDOWS, reason="POSIX lookup rules")
    def test_ensure_repairs_py_suffixed_install_on_posix(
            self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)
        pu.clear_module_cache()
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "r.slopeunits.create.py").write_text("stale\n")

        # Act
        ga.ensure_bundled_addon(PLUGIN_DIR)

        # Assert -- runnable name present, misleading leftover gone.
        assert (scripts / "r.slopeunits.create").is_file()
        assert not (scripts / "r.slopeunits.create.py").exists()

    @pytest.mark.skipif(not pu.IS_WINDOWS, reason="Windows shim")
    def test_bat_shims_are_written_on_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        ga.install_bundled_addon(PLUGIN_DIR)
        assert (tmp_path / "bin" / "r.slopeunits.create.bat").is_file()

    def test_reinstalling_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        first = ga.install_bundled_addon(PLUGIN_DIR)
        second = ga.install_bundled_addon(PLUGIN_DIR)
        assert first == second

    def test_missing_bundle_raises_with_guidance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        with pytest.raises(RuntimeError) as excinfo:
            ga.install_bundled_addon(str(tmp_path))   # no vendor/ here
        assert "g.extension" in str(excinfo.value)

    def test_no_network_is_used(self, tmp_path, monkeypatch):
        # The whole point: installation must not touch the network.
        import urllib.request

        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: pytest.fail("installation attempted a network call"),
        )
        ga.install_bundled_addon(PLUGIN_DIR)


class TestFindGrass:
    def test_searches_qgis_dirs_with_the_grass_override_key(self, monkeypatch):
        # Arrange -- same lookup the Dependencies panel uses for its GRASS row.
        calls = []
        monkeypatch.setattr(pu, "qgis_bin_dirs", lambda: ["/qgis/bin"])
        monkeypatch.setattr(
            pu, "find_exe",
            lambda name, key=None, extra_dirs=None:
                calls.append((name, key, extra_dirs)) or "/usr/bin/grass",
        )

        # Act
        found = pu.find_grass()

        # Assert
        assert found == "/usr/bin/grass"
        assert calls == [("grass", "grass", ["/qgis/bin"])]

    def test_returns_none_when_grass_is_absent(self, monkeypatch):
        monkeypatch.setattr(pu, "qgis_bin_dirs", lambda: [])
        monkeypatch.setattr(pu, "find_exe", lambda *a, **k: None)
        assert pu.find_grass() is None


class TestEnsureBundledAddon:
    @pytest.fixture
    def addon_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)
        pu.clear_module_cache()
        return tmp_path

    def test_reports_missing_before_install(self, addon_dir):
        assert not ga.bundled_addon_installed()

    def test_installs_when_missing(self, addon_dir):
        # Act
        installed = ga.ensure_bundled_addon(PLUGIN_DIR)

        # Assert
        assert REQUIRED <= set(installed)
        assert ga.bundled_addon_installed()

    def test_skips_when_already_installed(self, addon_dir, monkeypatch):
        # Arrange
        ga.install_bundled_addon(PLUGIN_DIR)
        monkeypatch.setattr(
            ga, "install_bundled_addon",
            lambda *a, **k: pytest.fail("reinstalled an installed addon"),
        )
        messages = []

        # Act
        installed = ga.ensure_bundled_addon(PLUGIN_DIR, log=messages.append)

        # Assert
        assert installed == []
        assert any("already installed" in m for m in messages)
