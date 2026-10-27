"""depzone/prep.py: block-wise raster I/O must equal whole-array maths.

Needs rasterio (the EIL venv); skipped where it is missing, as in CI.
"""

import importlib.util
import json
import os

import pytest

np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"depzone_{name}", os.path.join(PLUGIN_DIR, "depzone", f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = _load("prep")
dz = _load("rasters")

ROWS, COLS = 30, 24
# Small blocks so every kernel crosses several block seams.
SMALL_BLOCK = 7
CELL = 5.0
ND = dz.NODATA


@pytest.fixture(autouse=True)
def small_blocks(monkeypatch):
    monkeypatch.setattr(prep, "BLOCK_ROWS", SMALL_BLOCK)


def write(path, data, nodata=ND, cell=CELL, dtype="float32"):
    profile = dict(
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=dtype,
        crs="EPSG:32651",
        transform=from_origin(300000, 1700000, cell, cell),
        nodata=nodata,
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)
    return str(path)


def read(path):
    with rasterio.open(path) as src:
        return src.read(1)


def dem_array():
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:ROWS, 0:COLS]
    dem = 500 - 0.8 * x - 0.3 * y + rng.normal(0, 0.5, (ROWS, COLS))
    dem[0:3, 0:4] = ND  # an irregular no-data corner
    return dem.astype(np.float32)


def test_curvatures_match_the_whole_array(tmp_path):
    # Arrange
    dem = dem_array()
    src = write(tmp_path / "dem.tif", dem)
    z = np.where(dem == ND, np.nan, dem).astype(np.float64)
    expected_prof, expected_plan = dz.curvatures(z, CELL)

    # Act
    prep.main(
        [
            "curvatures",
            "--dem",
            src,
            "--profile",
            str(tmp_path / "p.tif"),
            "--plan",
            str(tmp_path / "q.tif"),
        ]
    )

    # Assert
    np.testing.assert_allclose(read(tmp_path / "p.tif"), expected_prof, rtol=1e-6)
    np.testing.assert_allclose(read(tmp_path / "q.tif"), expected_plan, rtol=1e-6)


def test_lowpass_matches_the_whole_array(tmp_path):
    dem = dem_array()
    src = write(tmp_path / "twi.tif", dem)
    valid = dem != ND
    expected = dz.window_mean(np.where(valid, dem, 0.0), valid, ND)

    prep.main(["lowpass", "--out-dir", str(tmp_path), src])

    out = read(prep.lowpass_path(str(tmp_path), src))
    np.testing.assert_allclose(out, expected, rtol=1e-6)


def test_seed_outlets_marks_the_whole_ring(tmp_path, capsys):
    # Arrange: ang valid everywhere but the no-data corner; no streams yet
    dem = dem_array()
    ang = write(tmp_path / "ang.tif", np.where(dem == ND, ND, 1.0))
    streams = write(
        tmp_path / "src.tif", np.zeros((ROWS, COLS)), nodata=255, dtype="uint8"
    )
    expected = dz.boundary_ring(dem != ND)

    # Act
    prep.main(
        [
            "seed-outlets",
            "--src",
            streams,
            "--ang",
            ang,
            "--out",
            str(tmp_path / "routing.tif"),
        ]
    )

    # Assert
    np.testing.assert_array_equal(read(tmp_path / "routing.tif") == 1, expected)
    assert f"Seeded {int(expected.sum())}" in capsys.readouterr().out


def test_align_puts_a_coarse_raster_on_the_reference_grid(tmp_path):
    ref = write(tmp_path / "dem.tif", dem_array())
    coarse = write(tmp_path / "ndvi.tif", np.full((6, 5), 0.4), cell=CELL * 5)

    prep.main(
        ["align", "--src", coarse, "--ref", ref, "--out", str(tmp_path / "a.tif")]
    )

    out = read(tmp_path / "a.tif")
    assert out.shape == (ROWS, COLS)
    assert np.allclose(out[out != ND], 0.4)


def test_sanitize_bulk_reports_masked_cells_and_scale(tmp_path, capsys):
    bulk = np.full((ROWS, COLS), 140.0)
    bulk[0, :5] = 0.0
    src = write(tmp_path / "buk.tif", bulk, nodata=32767)

    prep.main(["sanitize-bulk", "--src", src, "--out", str(tmp_path / "b.tif")])

    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["masked"] == 5
    assert summary["scale"] == 0.0981
    assert (read(tmp_path / "b.tif")[0, :5] == ND).all()


def engine_case(tmp_path, alpha_value):
    terrain = np.ones((ROWS, COLS))
    paths = {
        "ang": write(tmp_path / "ang.tif", terrain),
        "fel": write(tmp_path / "fel.tif", terrain * 100),
        "alpha": write(tmp_path / "alpha.tif", terrain * alpha_value),
        "dfi": write(tmp_path / "dfi.tif", terrain * 0.5),
        "source-mask": write(
            tmp_path / "mask.tif", np.eye(ROWS, COLS), nodata=255, dtype="uint8"
        ),
    }
    argv = ["engine-inputs"]
    for flag, path in paths.items():
        argv += [f"--{flag}", path]
    return argv + ["--out-dir", str(tmp_path)]


def test_engine_inputs_counts_legal_sources(tmp_path, capsys):
    prep.main(engine_case(tmp_path, alpha_value=20.0))

    counts = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert counts["legal_sources"] == min(ROWS, COLS)
    assert read(tmp_path / "source_final.tif").sum() == min(ROWS, COLS)


def test_engine_inputs_fails_when_no_source_is_legal(tmp_path):
    # alpha 3 is valid but outside the engine's fixed range [6, 72]
    with pytest.raises(SystemExit) as stop:
        prep.main(engine_case(tmp_path, alpha_value=3.0))
    assert stop.value.code == prep.EXIT_NO_SOURCES
