"""eil/workers/depzone_commands.py: the v4.4 depositional-zone recipe, without Qt."""

import importlib
import json
import os
import sys
import types

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

dc = importlib.import_module("mlprep_plugin.eil.workers.depzone_commands")
commands = importlib.import_module("mlprep_plugin.eil.workers.commands")

OUT = os.path.join("run", "deposition_zone_output")


def flags(argv):
    """{--flag: value} of an argv, for flags followed by a value."""
    return {
        argv[i]: argv[i + 1]
        for i in range(len(argv) - 1)
        if argv[i].startswith("--") and not argv[i + 1].startswith("--")
    }


class TestPaths:
    def test_every_file_lives_in_the_output_folder(self):
        paths = dc.DepzonePaths(OUT)
        assert paths.fel == os.path.join(OUT, "fel.tif")
        assert paths.initial_depzone == os.path.join(OUT, "Initial_DepZone.tif")
        assert paths.refined_mask == os.path.join(
            OUT, "fos_source_refinement", "refined_source_mask.tif"
        )
        assert paths.step5_combined == os.path.join(
            OUT, "step5_cleanup_output", "post_depositional_combined_mask.tif"
        )


class TestTaudem:
    def test_runs_the_tool_under_mpi(self):
        argv = dc.taudem("mpiexec", 8, "/bin/areadinf", "-ang", "a.tif", "-nc")
        assert argv == ["mpiexec", "-n", "8", "/bin/areadinf", "-ang", "a.tif", "-nc"]


class TestPrep:
    def test_builds_a_prep_subcommand(self):
        argv = dc.prep("py", "prep.py", "twi", sca="s.tif", slope="p.tif", out="t.tif")
        assert argv[:3] == ["py", "prep.py", "twi"]
        assert flags(argv) == {"--sca": "s.tif", "--slope": "p.tif", "--out": "t.tif"}

    def test_underscored_names_become_dashed_flags(self):
        argv = dc.prep("py", "prep.py", "engine-inputs", source_mask="m.tif")
        assert "--source-mask" in argv


class TestPredictorConfig:
    def test_matches_the_shipped_models_predictors(self):
        # Arrange
        with open(os.path.join(PLUGIN_DIR, "model_dfi", "model_metadata.json")) as f:
            expected = set(json.load(f)["predictor_columns"])
        paths = dc.DepzonePaths(OUT)

        # Act
        config = dc.predictor_config(paths)

        # Assert
        assert set(config["predictor_rasters"]) == expected

    def test_continuous_predictors_are_low_pass_filtered(self):
        rasters = dc.predictor_config(dc.DepzonePaths(OUT))["predictor_rasters"]
        assert rasters["twi"].endswith("twi_lpf.tif")
        # Geomorphology is categorical and the one predictor used raw.
        assert rasters["geomorphology"].endswith("Geomorphology.tif")


class TestFosConfig:
    def fos_inputs(self, fos="fos.tif"):
        return dc.FosInputs(
            susceptibility="sus.tif",
            factor_of_safety=fos,
            cohesion="c.tif",
            friction_angle="phi.tif",
            wetness="m.tif",
            friction_angle_unit="radians",
            bulk_density="buk.tif",
            slope="slope.tif",
        )

    def test_uses_the_pinns_own_fos_when_available(self):
        config = dc.fos_config(
            dc.DepzonePaths(OUT), self.fos_inputs(), "buk_masked.tif", 0.0981
        )
        assert config["factor_of_safety_raster"] == "fos.tif"
        assert config["fos_threshold"] == 1.5
        assert config["total_unit_weight_raster"] == "buk_masked.tif"
        assert config["total_unit_weight_scale_to_kn_m3"] == 0.0981
        assert config["total_unit_weight_resampling"] == "nearest"
        assert config["friction_angle_unit"] == "radians"

    def test_falls_back_to_deriving_fos_without_the_pinn_raster(self):
        config = dc.fos_config(
            dc.DepzonePaths(OUT), self.fos_inputs(fos=None), "b.tif", 1.0
        )
        assert config["factor_of_safety_raster"] is None

    def test_missing_inputs_are_named(self):
        inputs = self.fos_inputs()
        assert dc.missing_fos_inputs(inputs, exists=lambda p: p != "c.tif") == [
            "PINN cohesion raster"
        ]


class TestStep5Config:
    def test_cleans_the_initial_runout_with_v44_parameters(self):
        paths = dc.DepzonePaths(OUT)
        section = dc.step5_config(paths)["runout_cleanup_only"]
        assert section["runout_mask_raster"] == paths.initial_depzone
        assert section["source_mask_raster"] == paths.source_final
        assert section["min_runout_source_area_m2"] == 200.0
        assert section["edge_seed_buffer_m"] == 5.0


class TestPublishedRasters:
    def test_copies_the_three_v44_results_to_the_output_root(self):
        paths = dc.DepzonePaths(OUT)
        assert dc.published_rasters(paths) == {
            "ML_DFI_Depositional_Zone.tif": paths.deposition,
            "Initial_DepZone.tif": paths.initial_depzone,
            "PostDep_DepZone.tif": paths.step5_combined,
        }

    def test_final_layer_is_the_cleaned_zone(self):
        assert dc.FINAL_RASTER == "PostDep_DepZone.tif"


class TestRoutingCommand:
    def test_uses_v44_threshold_and_initial_depzone_mask(self):
        paths = dc.DepzonePaths(OUT)
        argv = commands.build_routing_command(
            "mpiexec", 4, "engine", dc.routing_inputs(paths), OUT, paths.deposition
        )
        got = flags(argv)
        assert got["--threshold"] == "0.01"
        assert got["--out-mask"] == paths.initial_depzone
        assert got["--source"] == paths.source_final
        assert got["--dfi"] == paths.dfi_final
        assert got["--alpha"] == paths.alpha_final


class TestStageInputs:
    RESULT = {
        "susceptibility_tif": "sus.tif",
        "factor_of_safety_tif": "fos.tif",
        "cohesion_tif": "c.tif",
        "friction_angle_tif": "phi.tif",
        "wetness_tif": "m.tif",
        "friction_angle_unit": "radians",
    }

    def test_fos_inputs_combine_pinn_rasters_and_eil_inputs(self):
        # Act
        inputs = dc.fos_inputs_from(self.RESULT, {"BUK": "buk.tif", "Slope": "s.tif"})

        # Assert
        assert inputs.susceptibility == "sus.tif"
        assert inputs.factor_of_safety == "fos.tif"
        assert inputs.bulk_density == "buk.tif"
        assert inputs.slope == "s.tif"
        assert inputs.friction_angle_unit == "radians"

    def test_unselected_eil_inputs_are_empty(self):
        inputs = dc.fos_inputs_from(self.RESULT, {})
        assert inputs.bulk_density == "" and inputs.slope == ""

    def test_no_skip_reason_when_everything_is_there(self):
        inputs = dc.fos_inputs_from(self.RESULT, {"BUK": "b.tif", "Slope": "s.tif"})
        assert dc.skip_reason("ndvi.tif", inputs, exists=lambda p: True) is None

    def test_skips_without_ndvi(self):
        inputs = dc.fos_inputs_from(self.RESULT, {"BUK": "b.tif", "Slope": "s.tif"})
        assert "NDVI" in dc.skip_reason("", inputs, exists=lambda p: True)

    def test_skips_and_names_missing_fos_inputs(self):
        inputs = dc.fos_inputs_from(self.RESULT, {"Slope": "s.tif"})
        reason = dc.skip_reason("ndvi.tif", inputs, exists=lambda p: True)
        assert "Bulk Density raster" in reason
