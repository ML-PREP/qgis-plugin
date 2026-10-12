"""Array preparation for depositional routing (clean DFI, alpha, source)."""

import importlib
import os
import sys
import types

import pytest

np = pytest.importorskip("numpy")

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

dr = importlib.import_module("mlprep_plugin.eil.workers.deposition_rasters")

NODATA = dr.NODATA


class TestClampDfi:
    def test_clamps_probabilities_and_marks_invalid_as_nodata(self):
        # Arrange
        dfi = np.array([[1.000001, 0.5, -0.0], [np.nan, -9999.0, np.inf]])

        # Act
        clean = dr.clamp_dfi(dfi)

        # Assert
        assert clean.dtype == np.float32
        np.testing.assert_array_equal(
            clean, np.array([[1.0, 0.5, 0.0], [NODATA, NODATA, NODATA]])
        )

    def test_does_not_mutate_input(self):
        dfi = np.array([[2.0]])
        dr.clamp_dfi(dfi)
        assert dfi[0, 0] == 2.0


class TestAlphaFromSlope:
    def test_slope_classes_map_to_alpha(self):
        # Arrange: drop ratios for 5°, 11°, 50° and a nodata cell.
        degrees = np.array([5.0, 11.0, 50.0])
        drops = np.tan(np.radians(degrees))
        slope = np.array([[drops[0], drops[1]], [drops[2], NODATA]])

        # Act
        alpha = dr.alpha_from_slope(slope, NODATA)

        # Assert: < 10° has no alpha, 10-12° -> 9, > 45° -> 30.
        np.testing.assert_array_equal(
            alpha, np.array([[NODATA, 9.0], [30.0, NODATA]], dtype=np.float32)
        )


class TestSourceMask:
    def test_cells_above_threshold_are_sources(self):
        dfi = np.array([[0.9, 0.2], [0.6, NODATA]], dtype=np.float32)
        source = dr.source_mask(dfi, NODATA, 0.5, log=lambda _: None)
        np.testing.assert_array_equal(
            source, np.array([[1.0, NODATA], [1.0, NODATA]], dtype=np.float32)
        )

    def test_falls_back_to_percentile_when_nothing_passes(self):
        # Arrange
        dfi = np.linspace(0.0, 0.4, 100, dtype=np.float32).reshape(10, 10)
        messages = []

        # Act
        source = dr.source_mask(dfi, NODATA, 0.5, log=messages.append)

        # Assert
        assert 0 < np.count_nonzero(source == 1.0) <= 5
        assert any("dynamic threshold" in m for m in messages)

    def test_uniform_raster_falls_back_to_its_maximum(self):
        dfi = np.full((3, 3), 0.2, dtype=np.float32)
        source = dr.source_mask(dfi, NODATA, 0.5, log=lambda _: None)
        assert np.count_nonzero(source == 1.0) == 9

    def test_no_valid_cells_raises(self):
        dfi = np.full((2, 2), NODATA, dtype=np.float32)
        with pytest.raises(dr.DepositionInputError, match="no valid"):
            dr.source_mask(dfi, NODATA, 0.5, log=lambda _: None)

    def test_all_zero_raster_raises(self):
        dfi = np.zeros((2, 2), dtype=np.float32)
        with pytest.raises(dr.DepositionInputError, match="Zero source cells"):
            dr.source_mask(dfi, NODATA, 0.5, log=lambda _: None)
