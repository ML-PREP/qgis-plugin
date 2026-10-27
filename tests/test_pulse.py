"""common/pulse.py: timing of the progress bar's shimmer, without Qt."""

import importlib
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pulse = importlib.import_module("mlprep_plugin.common.pulse")


class TestSweepFraction:
    def test_starts_at_the_left_edge(self):
        assert pulse.sweep_fraction(0) == 0.0

    def test_reaches_the_right_edge_when_the_sweep_ends(self):
        assert pulse.sweep_fraction(pulse.SWEEP_MS) == pytest.approx(1.0)

    def test_moves_forward_through_the_sweep(self):
        # Arrange
        times = range(0, pulse.SWEEP_MS + 1, 50)

        # Act
        positions = [pulse.sweep_fraction(t) for t in times]

        # Assert
        assert positions == sorted(positions)

    def test_rests_between_sweeps(self):
        assert pulse.sweep_fraction(pulse.SWEEP_MS + 1) is None
        assert pulse.sweep_fraction(pulse.PERIOD_MS - 1) is None

    def test_repeats_every_period(self):
        assert pulse.sweep_fraction(pulse.PERIOD_MS + 300) == pulse.sweep_fraction(300)


class TestBand:
    def test_band_is_centered_on_the_sweep_position(self):
        # Act
        left, width = pulse.band(fraction=0.5, span=400)

        # Assert
        assert left + width / 2 == pytest.approx(200)

    def test_band_enters_and_leaves_fully_outside_the_span(self):
        left_start, width = pulse.band(fraction=0.0, span=400)
        left_end, _ = pulse.band(fraction=1.0, span=400)
        assert left_start + width <= 0
        assert left_end >= 400

    def test_band_has_a_minimum_width_on_a_short_span(self):
        _, width = pulse.band(fraction=0.5, span=10)
        assert width == pulse.MIN_BAND_PX
