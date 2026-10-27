"""The grid PINN route's Qt-free pieces (pinn_grid/).

Gridding and tallies need rasterio and are skipped without it; the
end-to-end run with the real model is in test_pinn_models_run.py.
"""

import os
import sys

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

from eil import hazard_stats  # noqa: E402
from pinn_grid import features  # noqa: E402
from pinn_grid.inputs import MODEL_COLUMNS, missing_inputs  # noqa: E402

RECODE = {45: "Undifferentiated", 451: "Sandy Clay Loam", 448: "Clay"}


def _columns(n, **overrides):
    """One value per input for `n` cells, every cell predictable."""
    base = {
        "Elev": 500.0, "Slope": 25.0, "BUK": 112.0, "Clay": 300.0, "Sand": 400.0,
        "Silt": 300.0, "Prc": 150.0, "SoilThc": 10.0, "ContributingFactor": 400.0,
        "PGA2": 0.3, "type": 451.0,
    }
    return {name: np.full(n, overrides.get(name, value), dtype=float)
            for name, value in base.items()}


class TestInputs:
    def test_every_input_is_required(self):
        assert missing_inputs({name: "x.tif" for name in MODEL_COLUMNS}) == []

    def test_names_the_missing_inputs(self):
        rasters = {name: "x.tif" for name in MODEL_COLUMNS if name not in ("Prc", "type")}

        assert missing_inputs(rasters) == ["Prc", "type"]


class TestValidCells:
    def test_drops_gentle_slopes_missing_inputs_and_no_bulk_density(self):
        # Arrange
        columns = _columns(5)
        columns["Slope"][0] = 9.9
        columns["Slope"][1] = 10.0
        columns["PGA2"][2] = np.nan
        columns["BUK"][3] = 0.0

        # Act
        got = features.valid_cells(columns)

        # Assert
        assert got.tolist() == [False, True, False, False, True]


class TestModelFrame:
    def test_soil_codes_become_the_training_names(self):
        names, unknown = features.recode_soil_types([451.0, 45.0, 448.2], RECODE)

        assert names.tolist() == ["Sandy Clay Loam", "Undifferentiated", "Clay"]
        assert unknown == set()

    def test_unknown_codes_are_undifferentiated_and_reported(self):
        names, unknown = features.recode_soil_types([999.0], RECODE)

        assert names.tolist() == ["Undifferentiated"]
        assert unknown == {999}

    def test_has_every_training_column_and_both_soil_indices(self):
        # Act
        frame, _unknown = features.model_frame(_columns(3), RECODE)

        # Assert
        expected = set(MODEL_COLUMNS.values()) | {"soil_texture_idx", "soil_type_idx"}
        assert set(frame.columns) == expected
        assert frame["type"].tolist() == ["Sandy Clay Loam"] * 3
        assert frame["soil_type_idx"].tolist() == [6.0] * 3
        # Bulk density stays in cg/cm3: the model converts it itself.
        assert frame["BUK_mean"].tolist() == [112.0] * 3


class TestHazardClasses:
    def test_match_the_slope_unit_dashboard(self):
        outputs = pytest.importorskip("pinn_grid.outputs")
        values = np.array([0.0, 0.32, 0.33, 0.5, 0.66, 0.661, 1.0])

        got = [hazard_stats.HAZARD_CLASSES[i] for i in outputs.hazard_classes(values)]

        assert got == [hazard_stats.classify_susceptibility(v) for v in values]


class TestSummarizeFolds:
    def test_factor_of_safety_is_the_fold_mean(self):
        outputs = pytest.importorskip("pinn_grid.outputs")
        stacked = {name: np.ones((2, 3)) for name in (
            "susceptibility", "cohesion_kpa", "friction_rad", "displacement_cm", "wetness_m")}
        stacked["fos"] = np.array([[1.0, 2.0, 0.5], [2.0, 1.0, 1.5]])

        summary = outputs.summarize_folds(stacked)

        assert summary["fos"].tolist() == [1.5, 1.5, 1.0]


@pytest.fixture
def rio():
    return pytest.importorskip("rasterio")


def _write(rio, path, data, crs="EPSG:32651", res=5.0, nodata=-9999.0):
    from rasterio.transform import from_origin

    with rio.open(path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0],
                  count=1, dtype=data.dtype, crs=crs, nodata=nodata,
                  transform=from_origin(600000.0, 800000.0, res, res)) as dst:
        dst.write(data, 1)
    return str(path)


class TestAlign:
    def test_grid_shares_the_dem_origin_with_coarser_cells(self, rio, tmp_path):
        from pinn_grid import align

        dem = _write(rio, tmp_path / "dem.tif", np.ones((12, 13), "float32"))

        grid = align.read_grid(dem, 25.0)

        assert (grid.width, grid.height) == (3, 3)
        assert (grid.transform.c, grid.transform.f, grid.transform.a) == (600000.0, 800000.0, 25.0)

    def test_refuses_a_dem_in_degrees(self, rio, tmp_path):
        from pinn_grid import align

        dem = _write(rio, tmp_path / "dem.tif", np.ones((4, 4), "float32"),
                     crs="EPSG:4326", res=0.001)

        with pytest.raises(align.GridError, match="projected"):
            align.read_grid(dem, 25.0)

    def test_fine_rasters_average_and_soil_type_takes_the_majority(self, rio, tmp_path):
        # Arrange -- one 10 m cell = 2 x 2 pixels of 5 m.
        from pinn_grid import align

        dem = _write(rio, tmp_path / "dem.tif", np.ones((2, 2), "float32"))
        slope = _write(rio, tmp_path / "slope.tif", np.array([[10, 20], [30, 40]], "float32"))
        soil = _write(rio, tmp_path / "soil.tif", np.array([[451, 451], [451, 45]], "float32"))
        grid = align.read_grid(dem, 10.0)

        # Act
        out = align.grid_inputs({"Slope": slope, "type": soil}, grid, str(tmp_path), 2, 16)

        # Assert
        with rio.open(out["Slope"]) as src:
            assert src.read(1).tolist() == [[25.0]]
        with rio.open(out["type"]) as src:
            assert src.read(1).tolist() == [[451.0]]


class TestHazardTally:
    def test_counts_cells_per_municipality_by_centre(self, rio):
        # Arrange -- a 2 x 2 grid split into West and East columns.
        from affine import Affine
        from shapely.geometry import box

        from pinn_grid.align import Grid
        from pinn_grid.outputs import HazardTally

        grid = Grid("EPSG:32651", Affine(25, 0, 0, 0, -25, 50), 2, 2)
        tally = HazardTally(grid, [("West", box(0, 0, 25, 50)), ("East", box(25, 0, 50, 50))])
        valid = np.array([[True, True], [True, False]])
        window = rio.windows.Window(0, 0, 2, 2)

        # Act
        tally.add(window, valid, np.array([0.9, 0.5, 0.1]))

        # Assert
        result = tally.result()
        assert result["hazard_counts"] == {"High": 1, "Moderate": 1, "Low": 1}
        assert result["municipality_tallies"] == {
            "West": {"High": 1, "Moderate": 0, "Low": 1},
            "East": {"High": 0, "Moderate": 1, "Low": 0},
        }


class TestGridWriter:
    def test_writes_the_models_factor_of_safety_for_the_depositional_zone(self, rio, tmp_path):
        # Arrange -- a 1 x 2 grid, one predicted cell.
        from affine import Affine

        from pinn_grid.align import Grid
        from pinn_grid.outputs import NODATA, GridWriter

        grid = Grid("EPSG:32651", Affine(25, 0, 0, 0, -25, 25), 2, 1)
        valid = np.array([[True, False]])
        summary = {name: np.array([0.7]) for name in (
            "susceptibility_mean", "susceptibility_std", "cohesion_kpa", "friction_deg",
            "friction_rad", "displacement_cm", "wetness_m")}
        summary["fos"] = np.array([1.2])

        # Act
        with GridWriter(grid, str(tmp_path), 0.5) as writer:
            writer.write(rio.windows.Window(0, 0, 2, 1), valid, summary)

        # Assert
        path = writer.paths["factor_of_safety_tif"]
        assert os.path.basename(path) == "FactorOfSafety.tif"
        with rio.open(path) as src:
            assert src.read(1).tolist() == [[pytest.approx(1.2), NODATA]]
