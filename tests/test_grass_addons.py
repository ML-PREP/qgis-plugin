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
        assert (scripts / "r.slopeunits.create.py").is_file()

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
        target = tmp_path / "scripts" / "r.slopeunits.create.py"
        assert os.stat(target).st_mode & 0o111

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
