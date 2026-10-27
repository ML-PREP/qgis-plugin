"""ml-prep-scripts/pyscripts/zonal_grid.py: label-raster zonal statistics.

The accumulators need only numpy, so they run in CI. The end-to-end tests also
need rasterio/geopandas (the EIL venv) and compare against rasterstats, the
implementation this replaced.
"""

import importlib.util
import os

import pytest

np = pytest.importorskip("numpy")

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(PLUGIN_DIR, "ml-prep-scripts", "pyscripts", "zonal_grid.py")

_spec = importlib.util.spec_from_file_location("zonal_grid", SCRIPT)
zg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zg)

NAN = float("nan")


def feed(accumulator, *blocks):
    for zones, values in blocks:
        accumulator.add(np.asarray(zones), np.asarray(values, dtype="float64"))
    return accumulator.result()


class TestMeanAccumulator:
    def test_means_per_unit_across_blocks(self):
        # Arrange: unit ids are row_id + 1; three units, unit 2 has no cells
        acc = zg.MeanAccumulator(3)

        # Act
        got = feed(acc, ([1, 1, 3], [2.0, 4.0, 10.0]), ([3, 1], [20.0, 6.0]))

        # Assert
        np.testing.assert_array_equal(got, [4.0, NAN, 15.0])

    def test_rejects_ids_beyond_the_unit_count(self):
        acc = zg.MeanAccumulator(2)

        with pytest.raises(ValueError, match="unit id 3"):
            acc.add(np.array([3]), np.array([1.0]))


class TestMinAccumulator:
    def test_minimum_per_unit_across_blocks(self):
        acc = zg.MinAccumulator(3)

        got = feed(acc, ([2, 1, 2], [5.0, 7.0, 3.0]), ([1, 2], [-1.0, 4.0]))

        np.testing.assert_array_equal(got, [-1.0, 3.0, NAN])


class TestMajorityAccumulator:
    def test_most_frequent_class_per_unit_across_blocks(self):
        acc = zg.MajorityAccumulator(2)

        got = feed(acc, ([1, 1, 2], [45.0, 94.0, 94.0]), ([1, 2, 2], [45.0, 94.0, 1.0]))

        np.testing.assert_array_equal(got, [45.0, 94.0])

    def test_tie_goes_to_the_smallest_class_like_rasterstats(self):
        acc = zg.MajorityAccumulator(1)

        got = feed(acc, ([1, 1], [94.0, 45.0]))

        np.testing.assert_array_equal(got, [45.0])

    def test_unit_without_cells_is_nan(self):
        acc = zg.MajorityAccumulator(2)

        got = feed(acc, ([2], [7.0]))

        assert np.isnan(got[0]) and got[1] == 7.0


class TestValidCells:
    def test_drops_outside_units_nodata_and_non_finite(self):
        zones = np.array([[0, 1, 1], [2, 2, 1]], dtype="int32")
        values = np.array([[5.0, -9999.0, np.nan], [3.0, np.inf, 8.0]])

        z, v = zg.valid_cells(zones, values, nodata=-9999.0)

        np.testing.assert_array_equal(z, [2, 1])
        np.testing.assert_array_equal(v, [3.0, 8.0])


# ---------------------------------------------------------------------------
# End to end: zones + stats subcommands vs rasterstats
# ---------------------------------------------------------------------------

def _write_raster(rasterio, path, array, transform, crs, nodata=-9999.0):
    with rasterio.open(path, "w", driver="GTiff", width=array.shape[1],
                       height=array.shape[0], count=1, dtype="float32",
                       crs=crs, transform=transform, nodata=nodata) as dst:
        dst.write(array.astype("float32"), 1)


@pytest.fixture
def scene(tmp_path):
    """A 5 m DEM grid, a 50 m coarse raster, and four units incl. a tiny one."""
    rasterio = pytest.importorskip("rasterio")
    gpd = pytest.importorskip("geopandas")
    from rasterio.transform import from_origin
    from shapely.geometry import box

    crs = "EPSG:32651"
    x0, y0 = 500000.0, 800000.0
    fine_tf = from_origin(x0, y0, 5, 5)
    coarse_tf = from_origin(x0, y0, 50, 50)

    rng = np.random.default_rng(0)
    fine = rng.uniform(0, 100, size=(40, 40))
    fine[0:4, 0:4] = -9999.0  # nodata patch inside unit 0
    coarse = np.arange(16, dtype="float64").reshape(4, 4) * 10.0
    classes = np.where(np.arange(40)[None, :] < 26, 45.0, 94.0) * np.ones((40, 1))

    paths = {name: str(tmp_path / f"{name}.tif") for name in ("dem", "fine", "coarse", "type")}
    _write_raster(rasterio, paths["dem"], fine, fine_tf, crs)
    _write_raster(rasterio, paths["fine"], fine, fine_tf, crs)
    _write_raster(rasterio, paths["coarse"], coarse, coarse_tf, crs)
    _write_raster(rasterio, paths["type"], classes, fine_tf, crs)

    # Coarse pixel centres sit at x0+25+50i, y0-25-50j. Units do not overlap.
    units = gpd.GeoDataFrame(
        {"row_id": [0, 1, 2, 3]},
        geometry=[
            box(x0, y0 - 100, x0 + 100, y0),              # big, all class 45
            box(x0 + 100, y0 - 200, x0 + 200, y0 - 40),   # big, mostly class 94
            box(x0 + 30, y0 - 165, x0 + 45, y0 - 150),    # 15 m: no coarse centre
            box(x0 + 110, y0 - 30, x0 + 160, y0 - 10),    # mixed classes
        ],
        crs=crs,
    )
    paths["units"] = str(tmp_path / "units.gpkg")
    units.to_file(paths["units"], driver="GPKG")
    return paths, tmp_path


def _run(scene_paths, tmp_path, value, rule, resampling):
    zones = str(tmp_path / "Zones.tif")
    if not os.path.exists(zones):
        zg.build_zones(scene_paths["units"], zones, [scene_paths["dem"]])
    out = str(tmp_path / f"{value}_zonal.npz")
    zg.zonal_stats_on_grid(zones, scene_paths[value], out, -9999.0, rule, resampling)
    with np.load(out) as data:
        return data["row_id"], data["values"]


class TestEndToEnd:
    def test_fine_raster_mean_matches_rasterstats(self, scene):
        # Arrange
        rasterstats = pytest.importorskip("rasterstats")
        gpd = pytest.importorskip("geopandas")
        paths, tmp = scene

        # Act
        row_id, got = _run(paths, tmp, "fine", "mean", "bilinear")

        # Assert
        units = gpd.read_file(paths["units"])
        expected = [s["mean"] for s in rasterstats.zonal_stats(
            units, paths["fine"], stats="mean", nodata=-9999.0)]
        np.testing.assert_array_equal(row_id, [0, 1, 2, 3])
        np.testing.assert_allclose(got, np.array(expected, dtype="float64"), rtol=1e-6)

    def test_small_unit_gets_a_value_from_a_coarse_raster(self, scene):
        rasterstats = pytest.importorskip("rasterstats")
        gpd = pytest.importorskip("geopandas")
        paths, tmp = scene

        _row_id, got = _run(paths, tmp, "coarse", "mean", "bilinear")

        units = gpd.read_file(paths["units"])
        old = rasterstats.zonal_stats(units, paths["coarse"], stats="mean", nodata=-9999.0)
        assert old[2]["mean"] is None          # the gap this change closes
        assert np.isfinite(got).all()

    def test_majority_returns_real_class_codes(self, scene):
        paths, tmp = scene

        _row_id, got = _run(paths, tmp, "type", "majority", "nearest")

        assert set(np.unique(got)) <= {45.0, 94.0}
        assert got[0] == 45.0 and got[1] == 94.0

    def test_zone_raster_records_the_unit_count(self, scene):
        rasterio = pytest.importorskip("rasterio")
        paths, tmp = scene
        zones = str(tmp / "Zones.tif")

        result = zg.build_zones(paths["units"], zones, [paths["dem"]])

        with rasterio.open(zones) as src:
            assert src.tags()[zg.UNIT_COUNT_TAG] == "4"
            assert src.profile["tiled"]
        assert result["units"] == 4
