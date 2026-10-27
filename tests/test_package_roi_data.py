"""scripts/package_roi_data.py builds what the plugin's ROI download accepts."""

import importlib
import importlib.util
import json
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
roi_data = importlib.import_module("mlprep_plugin.common.roi_data")

_spec = importlib.util.spec_from_file_location(
    "package_roi_data", os.path.join(PLUGIN_DIR, "scripts", "package_roi_data.py")
)
packager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packager)


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "roi_data"
    directory.mkdir()
    for name in roi_data.required_files():
        (directory / name).write_bytes(b"boundary")
    (directory / "phl_admbndt_adminUnitLookup.xlsx").write_bytes(b"extra")
    (directory / ".DS_Store").write_bytes(b"junk")
    return directory


class TestPackage:
    def test_writes_a_manifest_the_plugin_accepts(self, source, tmp_path):
        # Act
        packager.package(str(source), str(tmp_path / "out"), "20231106")

        # Assert
        text = (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
        manifest = roi_data.parse_manifest(text)
        assert manifest.archive == "roi_data-20231106.zip"
        roi_data.verify_archive(str(tmp_path / "out" / manifest.archive), manifest)

    def test_archive_installs_every_file_except_os_junk(
        self, source, tmp_path, monkeypatch
    ):
        # Arrange
        monkeypatch.setattr(pu, "ENVS_DIR", str(tmp_path / "envs"))
        manifest = packager.package(str(source), str(tmp_path / "out"), "20231106")

        # Act
        installed = roi_data.install_archive(
            str(tmp_path / "out" / manifest.archive), manifest.version
        )

        # Assert
        assert os.path.isfile(os.path.join(installed, "phl_admbndt_adminUnitLookup.xlsx"))
        assert not os.path.exists(os.path.join(installed, ".DS_Store"))
        assert roi_data.is_complete(installed)

    def test_records_the_unpacked_size_for_the_disk_check(self, source, tmp_path):
        # Act
        manifest = packager.package(str(source), str(tmp_path / "out"), "1")

        # Assert
        expected = sum(
            os.path.getsize(source / name)
            for name in os.listdir(source) if not name.startswith(".")
        )
        assert manifest.unpacked_size == expected

    def test_refuses_an_incomplete_source(self, source, tmp_path):
        # Arrange
        os.remove(source / roi_data.required_files()[0])

        # Act / Assert -- never publish data the plugin would reject.
        with pytest.raises(SystemExit, match="missing"):
            packager.package(str(source), str(tmp_path / "out"), "1")
        assert not (tmp_path / "out" / "manifest.json").exists()
