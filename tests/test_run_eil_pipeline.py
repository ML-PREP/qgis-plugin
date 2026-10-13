"""Qt-free helpers of scripts/run_eil_pipeline.py (the headless EIL runner)."""

import importlib.util
import os

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "run_eil_pipeline", os.path.join(PLUGIN_DIR, "scripts", "run_eil_pipeline.py")
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def touch(path):
    path.write_bytes(b"")
    return str(path)


class TestParseRasterArgs:
    def test_maps_model_input_names_to_paths(self):
        # Act
        got = runner.parse_raster_args(["Slope=/d/slope.tif", "PGA2=/d/pga.tif"])

        # Assert
        assert got == {"Slope": "/d/slope.tif", "PGA2": "/d/pga.tif"}

    def test_rejects_an_item_without_equals(self):
        with pytest.raises(ValueError, match="NAME=PATH"):
            runner.parse_raster_args(["slope.tif"])

    def test_rejects_a_name_the_model_does_not_know(self):
        with pytest.raises(ValueError, match="Unknown raster name 'Slop'"):
            runner.parse_raster_args(["Slop=/d/slope.tif"])

    def test_rejects_elev_because_the_dem_supplies_it(self):
        with pytest.raises(ValueError, match="--dem"):
            runner.parse_raster_args(["Elev=/d/dem.tif"])

    def test_rejects_a_name_given_twice(self):
        with pytest.raises(ValueError, match="twice"):
            runner.parse_raster_args(["Clay=/a.tif", "Clay=/b.tif"])


class TestSelectRasters:
    """Mirrors EilInputs.selected_rasters: DEM as Elev, unusable files dropped."""

    def test_adds_the_dem_as_elev(self, tmp_path):
        # Arrange
        dem = touch(tmp_path / "dem.tif")
        slope = touch(tmp_path / "slope.tif")

        # Act
        got = runner.select_rasters(dem, {"Slope": slope}, log=lambda _m: None)

        # Assert
        assert got == {"Elev": dem, "Slope": slope}

    def test_drops_missing_and_non_geotiff_inputs_with_a_warning(self, tmp_path):
        # Arrange
        dem = touch(tmp_path / "dem.tif")
        asc = touch(tmp_path / "clay.asc")
        messages = []

        # Act
        got = runner.select_rasters(
            dem,
            {"Clay": asc, "Sand": str(tmp_path / "missing.tif")},
            log=messages.append,
        )

        # Assert
        assert got == {"Elev": dem}
        assert len(messages) == 2
        assert all("ignored" in m for m in messages)

    def test_accepts_tiff_suffix_in_any_case(self, tmp_path):
        dem = touch(tmp_path / "dem.tif")
        silt = touch(tmp_path / "silt.TIFF")

        got = runner.select_rasters(dem, {"Silt": silt}, log=lambda _m: None)

        assert got["Silt"] == silt


class TestOutputs:
    def test_default_output_folder_sits_beside_the_dem(self):
        got = runner.default_output_folder("/data/area/dem.tif")

        assert got == os.path.join("/data/area", "EIL_Results")

    def test_expected_outputs_end_with_the_depositional_mask(self):
        got = runner.expected_outputs("/out")

        labels = [label for label, _path in got]
        assert labels[0] == "Slope units (Go)"
        assert got[-1][1] == os.path.join(
            "/out", "deposition_zone_output", "step4_pure_depositional_mask.tif"
        )
        assert ("PINN raster (Overall_Output.tif)",
                os.path.join("/out", "overall_output", "Overall_Output.tif")) in got


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestStageClock:
    def test_records_how_long_each_stage_ran(self):
        # Arrange
        clock = FakeClock()
        stages = runner.StageClock(clock)

        # Act
        stages.enter("Building slope units")
        clock.now = 30.0
        stages.enter("Running AI model")
        clock.now = 45.0
        stages.close()

        # Assert
        assert stages.durations() == [
            ("Building slope units", 30.0),
            ("Running AI model", 15.0),
        ]

    def test_re_entering_the_current_stage_does_not_split_it(self):
        clock = FakeClock()
        stages = runner.StageClock(clock)

        stages.enter("A")
        clock.now = 5.0
        stages.enter("A")
        clock.now = 9.0
        stages.close()

        assert stages.durations() == [("A", 9.0)]


class TestStallDetector:
    """A stage that ends without signalling leaves the plugin 'running' forever."""

    def test_flags_running_pipeline_with_no_live_worker_after_grace(self):
        # Arrange
        clock = FakeClock()
        detector = runner.StallDetector(grace_seconds=10, clock=clock)

        # Act / Assert
        assert not detector.observe(is_running=True, worker_alive=False)
        clock.now = 9.0
        assert not detector.observe(is_running=True, worker_alive=False)
        clock.now = 10.0
        assert detector.observe(is_running=True, worker_alive=False)

    def test_a_live_worker_resets_the_grace_period(self):
        clock = FakeClock()
        detector = runner.StallDetector(grace_seconds=10, clock=clock)

        detector.observe(is_running=True, worker_alive=False)
        clock.now = 8.0
        detector.observe(is_running=True, worker_alive=True)
        clock.now = 15.0

        assert not detector.observe(is_running=True, worker_alive=False)

    def test_never_flags_a_stopped_pipeline(self):
        clock = FakeClock()
        detector = runner.StallDetector(grace_seconds=1, clock=clock)

        detector.observe(is_running=False, worker_alive=False)
        clock.now = 100.0

        assert not detector.observe(is_running=False, worker_alive=False)


class TestConsoleViewLogFile:
    def test_records_pre_log_lines_to_file_without_reprinting(self, tmp_path, capsys):
        # Arrange
        log_path = tmp_path / "run.log"
        view = runner.ConsoleView(str(log_path), error_level="error")

        # Act
        view.record(["[Warning] Clay: /d/clay.asc is not a GeoTIFF; ignored"])
        view.log("--- STARTING ---")
        view.close()

        # Assert
        lines = log_path.read_text().splitlines()
        assert lines[0] == "[setup] [Warning] Clay: /d/clay.asc is not a GeoTIFF; ignored"
        assert lines[1].endswith("--- STARTING ---")
        assert "ignored" not in capsys.readouterr().out
