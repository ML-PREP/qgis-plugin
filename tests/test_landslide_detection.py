"""Command line, environment and result parsing for landslide_worker.py."""

import importlib
import json
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

detection = importlib.import_module("mlprep_plugin.landslide.detection")
paths = importlib.import_module("mlprep_plugin.common.paths")


def make_request():
    return detection.DetectionRequest(
        dem="dem.tif",
        after="post.tif",
        lulc="None",
        roi_path="roi.shp",
        filter_col="ADM3_EN",
        filter_val="Alamada",
    )


class TestBuildDetectionCommand:
    def test_wires_every_input_flag(self):
        # Act
        cmd = detection.build_detection_command("py", make_request(), "out.tif")

        # Assert
        assert cmd[:2] == ["py", paths.LANDSLIDE_WORKER_SCRIPT]
        flags = dict(zip(cmd[2::2], cmd[3::2]))
        assert flags == {
            "--dem": "dem.tif",
            "--after": "post.tif",
            "--lulc": "None",
            "--output": "out.tif",
            "--roi_path": "roi.shp",
            "--filter_col": "ADM3_EN",
            "--filter_val": "Alamada",
            "--model_path": paths.LANDSLIDE_MODEL,
            "--stats_path": paths.LANDSLIDE_NORM_STATS,
        }

    def test_request_is_immutable(self):
        with pytest.raises(Exception):
            make_request().dem = "other.tif"


class TestDetectionEnvironment:
    def test_strips_qgis_python_variables(self):
        # Arrange
        environ = {"PYTHONHOME": "x", "PYTHONPATH": "y", "PATH": "/usr/bin"}

        # Act
        env = detection.detection_environment(environ, platform="darwin")

        # Assert
        assert env == {"PATH": "/usr/bin"}
        assert "PYTHONHOME" in environ  # caller's mapping untouched

    def test_windows_prepends_qgis_bin(self, tmp_path):
        # Arrange: sys.prefix is <root>/apps/Python312, bin is <root>/bin.
        prefix = tmp_path / "apps" / "Python312"
        prefix.mkdir(parents=True)
        (tmp_path / "bin").mkdir()

        # Act
        env = detection.detection_environment(
            {"PATH": "C:\\Windows"}, platform="win32", prefix=str(prefix)
        )

        # Assert
        assert env["PATH"].startswith(str(tmp_path / "bin") + os.pathsep)


class TestParseWorkerResult:
    def test_reads_the_last_line(self):
        stdout = "loading\nworking\n" + json.dumps({"status": "success"}) + "\n"
        assert detection.parse_worker_result(stdout) == {"status": "success"}

    def test_non_json_tail_raises(self):
        with pytest.raises(json.JSONDecodeError):
            detection.parse_worker_result("Traceback ...\nBoom")
