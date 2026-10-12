"""Qt-free pieces of the EIL pipeline: hazard tallies, engine argv, progress.

Loaded through a synthetic package (as in test_grass_addons.py) so the
plugin's __init__ -- which needs QGIS -- is never executed.
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

hazard = importlib.import_module("mlprep_plugin.eil.hazard_stats")
params = importlib.import_module("mlprep_plugin.eil.params")
commands = importlib.import_module("mlprep_plugin.eil.workers.commands")
progress = importlib.import_module("mlprep_plugin.eil.workers.zonal_progress")
roi = importlib.import_module("mlprep_plugin.common.roi")
paths = importlib.import_module("mlprep_plugin.common.paths")


class TestClassifySusceptibility:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (0.9, "High"),
            (0.661, "High"),
            (0.66, "Moderate"),
            (0.33, "Moderate"),
            (0.329, "Low"),
            (0.0, "Low"),
            ("0.8", "High"),
        ],
    )
    def test_thresholds(self, value, expected):
        assert hazard.classify_susceptibility(value) == expected

    @pytest.mark.parametrize("value", [None, "", "n/a", object()])
    def test_unreadable_value_counts_as_low(self, value):
        assert hazard.classify_susceptibility(value) == "Low"


class TestTallies:
    def test_counts_each_class_per_municipality(self):
        # Arrange
        pairs = [("A", 0.9), ("A", 0.5), ("A", 0.1), ("B", 0.9), ("A", 0.95)]

        # Act
        tallies = hazard.tally_by_municipality(pairs)

        # Assert
        assert tallies == {
            "A": {"High": 2, "Moderate": 1, "Low": 1},
            "B": {"High": 1, "Moderate": 0, "Low": 0},
        }

    def test_empty_input_gives_empty_tally(self):
        assert hazard.tally_by_municipality([]) == {}

    def test_most_hazardous_orders_by_high_and_caps(self):
        # Arrange
        tallies = {
            f"M{i}": {"High": i, "Moderate": 0, "Low": 0} for i in range(15)
        }

        # Act
        ranked = hazard.most_hazardous(tallies)

        # Assert
        assert len(ranked) == hazard.TOP_MUNICIPALITIES
        assert [name for name, _ in ranked[:3]] == ["M14", "M13", "M12"]

    def test_most_hazardous_does_not_mutate_input(self):
        tallies = {"A": {"High": 1, "Moderate": 0, "Low": 0}}
        hazard.most_hazardous(tallies)
        assert tallies == {"A": {"High": 1, "Moderate": 0, "Low": 0}}


class TestZonalArgs:
    def test_flags_follow_the_engine_contract(self):
        # Act
        args = commands.build_zonal_args(
            "dem.tif", "rasters", "out", "py", "EPSG:32651", dict(params.ZONAL_PARAMS)
        )

        # Assert
        pairs = dict(zip(args[::2], args[1::2]))
        assert pairs == {
            "-dem": "dem.tif",
            "-rasterFolder": "rasters",
            "-outputDir": "out",
            "-pythonExe": "py",
            "-targetCRS": "EPSG:32651",
            "-areaMin": "5000",
            "-thresh": "5000",
            "-cvMin": "0.15",
            "-rf": "10",
            "-maxIter": "1",
            "-tileCols": "3",
            "-tileRows": "3",
            "-tileOverlap": "500",
        }

    def test_missing_params_use_engine_defaults(self):
        args = commands.build_zonal_args("d", "r", "o", "p", "c", {})
        pairs = dict(zip(args[::2], args[1::2]))
        assert pairs["-maxIter"] == "10"
        assert pairs["-tileOverlap"] == "50"


class TestRoutingCommand:
    def test_inputs_and_outputs_are_wired(self, tmp_path):
        # Arrange
        inputs = {k: f"{k}.tif" for k in ("fel", "ang", "source", "dfi", "alpha")}

        # Act
        cmd = commands.build_routing_command(
            "mpiexec", 4, "engine", inputs, str(tmp_path), "dep.tif"
        )

        # Assert
        assert cmd[:4] == ["mpiexec", "-n", "4", "engine"]
        flags = dict(zip(cmd[4::2], cmd[5::2]))
        assert flags["--fel"] == "fel.tif"
        assert flags["--alpha"] == "alpha.tif"
        assert flags["--out-deposition"] == "dep.tif"
        assert flags["--out-dfs"] == os.path.join(str(tmp_path), "step4_dfs.tif")
        assert flags["--threshold"] == "0.2"


class TestZonalProgress:
    def test_phases_drive_the_bar(self):
        # Arrange
        step = progress.zonal_step(3)
        lines = [
            "[Phase 1] Merging Tiles",
            "noise",
            "[Phase 1] Polygonizing",
            "[RASTER_DONE] a",
            "[RASTER_DONE] b",
            "[RASTER_DONE] c",
            "[MERGE_DONE]",
        ]

        # Act
        shown, current = [], 0.0
        for line in lines:
            current, value = progress.advance(current, line, step)
            if value is not None:
                shown.append(value)

        # Assert
        assert shown == [40, 50, 65, 80, 95, 100]

    def test_zonal_phase_is_capped_below_complete(self):
        current, value = progress.advance(94.0, "[RASTER_DONE]", 45.0)
        assert value == progress.ZONAL_CEILING

    def test_zero_rasters_does_not_divide_by_zero(self):
        assert progress.zonal_step(0) == progress.ZONAL_SHARE


class TestRoi:
    def test_every_level_has_a_shapefile_and_column(self, tmp_path):
        boundaries = roi.admin_boundaries(str(tmp_path))
        assert tuple(boundaries) == roi.ADMIN_LEVELS
        assert boundaries["Municipality"] == {
            "path": os.path.join(
                str(tmp_path), "phl_admbnda_adm3_psa_namria_20231106.shp"
            ),
            "col": "ADM3_EN",
        }


class TestPaths:
    def test_plugin_dir_is_the_repository_root(self):
        # Moving modules into subpackages must not shift bundled-file paths.
        assert paths.PLUGIN_DIR == PLUGIN_DIR
        assert os.path.exists(os.path.join(paths.PLUGIN_DIR, "metadata.txt"))
