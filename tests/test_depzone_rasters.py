"""depzone/rasters.py: the array maths of the v4.4 depositional-zone flow.

Pure numpy, so these run in CI; the rasterio I/O around them is in prep.py.
"""

import importlib.util
import math
import os

import pytest

np = pytest.importorskip("numpy")

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "depzone_rasters", os.path.join(PLUGIN_DIR, "depzone", "rasters.py")
)
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)

ND = r.NODATA


class TestBlocks:
    def test_blocks_cover_every_row_once_with_halo(self):
        # Act
        blocks = list(r.blocks(height=10, block_rows=4, halo=1))

        # Assert
        assert [(b.row0, b.row1) for b in blocks] == [(0, 4), (4, 8), (8, 10)]
        assert [(b.pad0, b.pad1) for b in blocks] == [(0, 5), (3, 9), (7, 10)]

    def test_trim_drops_the_halo_rows(self):
        block = list(r.blocks(height=10, block_rows=4, halo=1))[1]
        padded = np.arange(block.pad1 - block.pad0)
        assert block.trim(padded).tolist() == [1, 2, 3, 4]


class TestSlopeDegrees:
    def test_converts_tangent_to_degrees_and_keeps_nodata(self):
        tangent = np.array([[0.0, 1.0, ND]], dtype=np.float32)
        out = r.slope_degrees(tangent, ND)
        assert out[0, 0] == pytest.approx(0.0)
        assert out[0, 1] == pytest.approx(45.0)
        assert out[0, 2] == ND


class TestClampDfi:
    def test_clamps_into_unit_range_and_nodatas_invalid(self):
        dfi = np.array([[1.000001, -0.5, -9999.0, np.nan]], dtype=np.float32)
        out = r.clamp_dfi(dfi)
        assert out[0, 0] == 1.0
        assert out[0, 1] == 0.0
        assert out[0, 2] == ND and out[0, 3] == ND


class TestCurvatures:
    def test_plane_has_zero_curvature(self):
        # Arrange: a tilted plane
        y, x = np.mgrid[0:6, 0:6]
        z = (2.0 * x + 0.5 * y).astype(np.float64)

        # Act
        prof, plan = r.curvatures(z, cell_size=5.0)

        # Assert: interior only -- the outer ring copies the centre for its
        # missing neighbours, so it is not a plane there by construction
        assert np.allclose(prof[1:-1, 1:-1], 0.0, atol=1e-9)
        assert np.allclose(plan[1:-1, 1:-1], 0.0, atol=1e-9)

    def test_nodata_cells_stay_nodata_and_do_not_spike_neighbours(self):
        y, x = np.mgrid[0:6, 0:6]
        z = (2.0 * x).astype(np.float64)
        z[2, 2] = np.nan

        prof, plan = r.curvatures(z, cell_size=5.0)

        assert prof[2, 2] == ND and plan[2, 2] == ND
        neighbours = np.delete(prof[1:4, 1:4].ravel(), 4)
        assert np.all(np.abs(neighbours) < 1.0)

    def test_valley_and_ridge_have_opposite_profile_sign(self):
        y, x = np.mgrid[0:7, 0:7]
        slope = 3.0 * y
        valley = slope + 0.5 * (x - 3.0) ** 2
        ridge = slope - 0.5 * (x - 3.0) ** 2

        _, plan_valley = r.curvatures(valley.astype(np.float64), cell_size=1.0)
        _, plan_ridge = r.curvatures(ridge.astype(np.float64), cell_size=1.0)

        assert np.sign(plan_valley[3, 3]) == -np.sign(plan_ridge[3, 3])


class TestTwi:
    def test_ln_of_area_over_tangent(self):
        sca = np.array([[100.0, 100.0, ND]], dtype=np.float32)
        tan = np.array([[0.5, 0.0, 0.5]], dtype=np.float32)

        out = r.twi(sca, tan, sca_nodata=ND, slope_nodata=ND)

        assert out[0, 0] == pytest.approx(math.log(100.0 / 0.5), rel=1e-6)
        # Flats are floored at tan = 0.001 instead of dividing by zero.
        assert out[0, 1] == pytest.approx(math.log(100.0 / 0.001), rel=1e-6)
        assert out[0, 2] == ND


class TestWindowMean:
    def test_averages_only_valid_neighbours(self):
        values = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])
        valid = np.ones_like(values, dtype=bool)
        valid[0, 0] = False

        out = r.window_mean(values, valid, nodata=ND, window=3)

        assert out[1, 1] == pytest.approx((45.0 - 1.0) / 8)
        assert out[0, 0] == ND
        assert out[0, 1] == pytest.approx((2 + 3 + 5 + 6 + 4) / 5)


class TestBoundaryRing:
    def test_marks_cells_touching_the_domain_edge(self):
        valid = np.ones((5, 5), dtype=bool)
        valid[2, 2] = False

        ring = r.boundary_ring(valid)

        assert ring[0].all() and ring[-1].all()
        assert ring[1, 1] and ring[1, 2] and ring[3, 3]
        assert not ring[2, 2]

    def test_interior_cells_are_not_ring(self):
        ring = r.boundary_ring(np.ones((5, 5), dtype=bool))
        assert not ring[1:4, 1:4].any()


class TestBulkDensity:
    def test_non_positive_cells_become_nodata(self):
        data = np.array([[0.0, -3.0, 140.0, ND]], dtype=np.float32)

        clean, masked = r.mask_non_positive(data, ND)

        assert clean.tolist() == [[ND, ND, 140.0, ND]]
        assert masked == 2

    @pytest.mark.parametrize(
        "median, scale",
        [(140.0, 0.0981), (1.4, 9.81), (14.0, 1.0)],
    )
    def test_unit_weight_scale_detects_the_encoding(self, median, scale):
        assert r.unit_weight_scale(median)[0] == scale


class TestEngineInputs:
    def test_builds_the_engines_validation_contract(self):
        # Arrange: 1 row, 5 cells; the last is off the terrain
        terrain = np.array([[True, True, True, True, False]])
        alpha = np.array([[20.0, 95.0, 3.0, 20.0, 20.0]], dtype=np.float32)
        dfi = np.array([[0.8, ND, 0.3, 0.4, 0.5]], dtype=np.float32)
        source = np.array([[1, 1, 1, 0, 1]], dtype=np.uint8)

        # Act
        out = r.engine_inputs(terrain, alpha, ND, dfi, ND, source)

        # Assert: alpha outside [1, 90] is neutralised to NoData
        assert out.alpha.tolist() == [[20.0, ND, 3.0, 20.0, ND]]
        # DFI gaps on terrain take the neutral mid value
        assert out.dfi.tolist()[0][:4] == pytest.approx([0.8, r.DFI_MID, 0.3, 0.4])
        assert out.dfi[0, 4] == ND
        # Sources need alpha inside the engine's fixed range [6, 72]
        assert out.source.tolist() == [[1.0, 0.0, 0.0, 0.0, ND]]
        assert out.counts == {
            "terrain": 4,
            "source_candidates": 3,
            "legal_sources": 1,
            "alpha_neutralised": 1,
            "dfi_predicted": 3,
        }
