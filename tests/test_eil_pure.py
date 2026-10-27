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
run_progress = importlib.import_module("mlprep_plugin.eil.progress")
completion = importlib.import_module("mlprep_plugin.eil.completion")
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
        assert flags["--threshold"] == "0.01"
        assert flags["--out-mask"] == os.path.join(str(tmp_path), "Initial_DepZone.tif")


def feed_zonal(lines, steps):
    shown, current = [], 0.0
    for line in lines:
        current, value = progress.advance(current, line, steps)
        if value is not None:
            shown.append(value)
    return shown


class TestZonalProgress:
    def test_engine_lines_drive_the_stage(self):
        # Arrange: the engine's real output, 2 tiles and 2 rasters
        steps = progress.zonal_steps(total_tiles=2, total_rasters=2)
        lines = [
            "[Phase 1] Tiling DEM for Parallel Slope Unit Generation...",
            "> Tile 1 generated in 3m2s.",
            "noise",
            "> Tile 0 generated in 3m9s.",
            "[Phase 1] Merging Interlocking Tiles...",
            "[Phase 1] Sieve-Cleaning Seams and Polygonizing to GPKG...",
            "[Phase 2] Rasterizing slope units onto the zone grid...",
            "[RASTER_DONE] Slope",
            "[RASTER_DONE] Elev",
            "[Merging] Combining all features...",
            "[MERGE_DONE] /out/Merged_PINN_Features.gpkg",
        ]

        # Act
        shown = feed_zonal(lines, steps)

        # Assert
        assert shown == [28, 57, 57, 71, 86, 91, 97, 97, 100]

    def test_tiles_never_pass_the_merge_milestone(self):
        steps = progress.zonal_steps(total_tiles=1, total_rasters=1)
        assert feed_zonal(["> Tile 0 generated in 1s."] * 3, steps) == [57, 57, 57]

    def test_rasters_never_pass_the_feature_merge(self):
        steps = progress.zonal_steps(total_tiles=1, total_rasters=1)
        current, value = progress.advance(96.0, "[RASTER_DONE] x", steps)
        assert value == int(progress.RASTERS_DONE)

    def test_zero_counts_do_not_divide_by_zero(self):
        steps = progress.zonal_steps(total_tiles=0, total_rasters=0)
        assert steps.tile == progress.TILES_DONE

    def test_tile_count_comes_from_the_grid_params(self):
        assert progress.tile_count({"tileCols": 3, "tileRows": 4}) == 12
        assert progress.tile_count({}) == 1


class TestRunProgress:
    def test_each_stage_fills_its_own_slice(self):
        for stage, (start, end) in run_progress.STAGE_SLICES.items():
            assert run_progress.overall(stage, 0) == start
            assert run_progress.overall(stage, 100) == end

    @pytest.mark.parametrize("route", ["STAGE_SLICES", "GRID_STAGE_SLICES"])
    def test_slices_tile_the_whole_bar_in_pipeline_order(self, route):
        slices = list(getattr(run_progress, route).values())
        assert slices[0][0] == 0
        assert slices[-1][1] == run_progress.COMPLETE
        assert all(a[1] == b[0] for a, b in zip(slices, slices[1:]))

    def test_the_grid_stage_hands_over_to_routing(self):
        assert run_progress.overall(run_progress.GRID, 0) == 0
        assert run_progress.overall(run_progress.GRID, 100) == 65

    def test_stage_percent_is_clamped(self):
        assert run_progress.overall(run_progress.PINN, 250) == 65
        assert run_progress.overall(run_progress.PINN, -5) == 62

    def test_bar_never_moves_backwards(self):
        shown = run_progress.advance(50, run_progress.ZONAL, 100)
        assert shown == 50

    def test_bar_moves_forward_into_the_next_stage(self):
        shown = run_progress.advance(38, run_progress.SEAMS, 50)
        assert shown == 50


class TestCompletionMessage:
    def test_names_the_output_folder(self):
        # Act
        title, text = completion.completion_message("/out/EIL_Results", routing_ran=True)

        # Assert
        assert title == completion.TITLE
        assert "/out/EIL_Results" in text
        assert "skipped" not in text

    def test_says_when_routing_was_skipped(self):
        title, text = completion.completion_message("/out", routing_ran=False)
        assert "Depositional routing was skipped" in text

    def test_names_the_clipping_region(self):
        _, text = completion.completion_message(
            "/out", routing_ran=True, roi_label="Makilala, Cotabato"
        )
        assert "clipped to Makilala, Cotabato" in text

    def test_says_nothing_about_clipping_without_a_region(self):
        _, text = completion.completion_message("/out", routing_ran=True)
        assert "clipped" not in text


class TestRoi:
    def test_every_level_has_a_shapefile_and_columns(self, tmp_path):
        boundaries = roi.admin_boundaries(str(tmp_path))
        assert tuple(boundaries) == roi.ADMIN_LEVELS
        assert boundaries["Municipality"] == {
            "path": os.path.join(
                str(tmp_path), "phl_admbnda_adm3_psa_namria_20231106.shp"
            ),
            "col": "ADM3_EN",
            "pcode": "ADM3_PCODE",
            "parents": ("ADM2_EN",),
        }

    def test_region_has_no_parent_columns(self, tmp_path):
        assert roi.admin_boundaries(str(tmp_path))["Region"]["parents"] == ()

    def test_barangay_labels_name_municipality_and_province(self, tmp_path):
        assert roi.admin_boundaries(str(tmp_path))["Barangay"]["parents"] == (
            "ADM3_EN",
            "ADM2_EN",
        )


class TestRoiChoices:
    def test_label_joins_name_and_parents(self):
        # Act
        choices = roi.label_choices(
            "Municipality", [("PH1247", "Makilala", ("Cotabato",))]
        )

        # Assert
        assert choices == (
            roi.RoiChoice("Municipality", "PH1247", "Makilala", "Makilala, Cotabato"),
        )

    def test_same_name_in_different_parents_stays_distinct(self):
        choices = roi.label_choices(
            "Municipality",
            [("PH1", "San Jose", ("Tarlac",)), ("PH2", "San Jose", ("Batangas",))],
        )
        assert [c.label for c in choices] == ["San Jose, Batangas", "San Jose, Tarlac"]

    def test_colliding_labels_are_suffixed_with_the_pcode(self):
        choices = roi.label_choices(
            "Barangay",
            [("PH2", "Poblacion", ("Alpha", "Beta")), ("PH1", "Poblacion", ("Alpha", "Beta"))],
        )
        assert [c.label for c in choices] == [
            "Poblacion, Alpha, Beta [PH1]",
            "Poblacion, Alpha, Beta [PH2]",
        ]

    def test_rows_without_a_name_or_pcode_are_dropped(self):
        choices = roi.label_choices(
            "Province", [("", "Nowhere", ()), ("PH9", "", ()), ("PH3", "Bukidnon", ())]
        )
        assert [c.pcode for c in choices] == ["PH3"]

    def test_blank_parents_are_skipped_in_the_label(self):
        (choice,) = roi.label_choices("Province", [("PH3", "Bukidnon", ("", None))])
        assert choice.label == "Bukidnon"


class TestRoiHelpers:
    def test_attribute_filter_quotes_column_and_value(self):
        assert roi.attribute_filter("ADM3_PCODE", "PH1247") == "\"ADM3_PCODE\" = 'PH1247'"

    def test_attribute_filter_escapes_single_quotes(self):
        assert roi.attribute_filter("ADM4_EN", "O'Donnell") == "\"ADM4_EN\" = 'O''Donnell'"

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("/out/Final_Hazard_Map.gpkg", "/out/Final_Hazard_Map_ROI.gpkg"),
            ("/out/PostDep_DepZone.tif", "/out/PostDep_DepZone_ROI.tif"),
        ],
    )
    def test_clipped_path_sits_beside_the_source(self, path, expected):
        assert roi.clipped_path(path) == expected


class TestPaths:
    def test_plugin_dir_is_the_repository_root(self):
        # Moving modules into subpackages must not shift bundled-file paths.
        assert paths.PLUGIN_DIR == PLUGIN_DIR
        assert os.path.exists(os.path.join(paths.PLUGIN_DIR, "metadata.txt"))
