from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "steps" / "fos_source_refinement" / "fos_source_refinement.py"
SPEC = importlib.util.spec_from_file_location("fos_source_refinement", SCRIPT_PATH)
fsr = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fsr
SPEC.loader.exec_module(fsr)

NODATA = 255


def refine(terrain, fos, fos_valid):
    return fsr.refine_source_mask(
        np.array(terrain),
        np.array(fos, dtype=np.float64),
        np.array(fos_valid),
        fos_threshold=1.5,
        mask_nodata=NODATA,
    )


def test_source_is_one_wherever_fos_is_below_threshold():
    source, diagnostics = refine(
        terrain=[True, True, True, True],
        fos=[0.8, 1.49, 2.0, 1.0],
        fos_valid=[True, True, True, True],
    )

    assert source.tolist() == [1, 1, 0, 1]
    assert diagnostics["retained"].tolist() == [True, True, False, True]
    assert diagnostics["excluded_stable"].tolist() == [False, False, True, False]


def test_fos_exactly_at_threshold_is_not_a_source():
    source, _ = refine(terrain=[True], fos=[1.5], fos_valid=[True])

    assert source.tolist() == [0]


def test_unresolved_fos_and_off_terrain_cells_are_nodata():
    source, diagnostics = refine(
        terrain=[True, False],
        fos=[np.nan, 0.5],
        fos_valid=[False, True],
    )

    assert source.tolist() == [NODATA, NODATA]
    assert diagnostics["unresolved"].tolist() == [True, False]


def write_raster(path, data, nodata=-9999.0):
    data = np.asarray(data, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:32651",
        transform=from_origin(0, 100, 10, 10),
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


def test_run_ignores_susceptibility_when_building_the_source_mask(tmp_path):
    # Arrange: susceptibility is NoData everywhere; FoS alone decides sources.
    shape = (1, 3)
    susceptibility = write_raster(tmp_path / "sus.tif", np.full(shape, -9999.0))
    fos = write_raster(tmp_path / "fos.tif", [[1.0, 1.5, 2.0]])
    params = fsr.SourceRefinementParams(
        susceptibility_raster=susceptibility,
        cohesion_raster=write_raster(tmp_path / "c.tif", np.full(shape, 5.0)),
        friction_angle_raster=write_raster(tmp_path / "phi.tif", np.full(shape, 0.5)),
        saturation_ratio_raster=write_raster(tmp_path / "m.tif", np.full(shape, 0.5)),
        total_unit_weight_raster=write_raster(tmp_path / "g.tif", np.full(shape, 18.0)),
        slope_angle_raster=write_raster(tmp_path / "slope.tif", np.full(shape, 30.0)),
        output_fos_raster=tmp_path / "out" / "fos.tif",
        output_source_mask_raster=tmp_path / "out" / "mask.tif",
        output_summary_json=tmp_path / "out" / "summary.json",
        factor_of_safety_raster=fos,
    )

    # Act
    summary = fsr.run_source_refinement(params)

    # Assert
    with rasterio.open(params.output_source_mask_raster) as src:
        assert src.read(1).tolist() == [[1, 0, 0]]
    counts = json.loads(params.output_summary_json.read_text())["counts"]
    assert counts["retained_source_cells"] == 1
    assert counts["stable_cells_excluded"] == 2
    assert summary["method"]["threshold_inclusive_for_source"] is False


def test_derived_fos_is_computed_outside_susceptible_units(tmp_path):
    # Arrange: no susceptibility anywhere, no precomputed FoS raster.
    shape = (1, 2)
    params = fsr.SourceRefinementParams(
        susceptibility_raster=write_raster(tmp_path / "sus.tif", np.full(shape, -9999.0)),
        cohesion_raster=write_raster(tmp_path / "c.tif", np.zeros(shape)),
        friction_angle_raster=write_raster(tmp_path / "phi.tif", np.full(shape, 0.3)),
        saturation_ratio_raster=write_raster(tmp_path / "m.tif", np.ones(shape)),
        total_unit_weight_raster=write_raster(tmp_path / "g.tif", np.full(shape, 18.0)),
        slope_angle_raster=write_raster(tmp_path / "slope.tif", np.full(shape, 40.0)),
        output_fos_raster=tmp_path / "out" / "fos.tif",
        output_source_mask_raster=tmp_path / "out" / "mask.tif",
        output_summary_json=tmp_path / "out" / "summary.json",
    )

    # Act
    fsr.run_source_refinement(params)

    # Assert: a steep, saturated, cohesionless slope is unstable -> source.
    with rasterio.open(params.output_source_mask_raster) as src:
        assert src.read(1).tolist() == [[1, 1]]
