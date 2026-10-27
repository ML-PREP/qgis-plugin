"""Soil indices of the grid PINN (py_files/soil_texture.py).

The vectorised `soil_texture_idx` must give exactly what the per-row
classifier the model was trained with gives, including on class boundaries.
"""

import os
import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pandas")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_files import soil_texture as st  # noqa: E402


def _per_row(clay, silt, sand):
    return [
        st.SOIL_TEXTURE_TO_IDX[
            st.classify_soil_texture(
                st.gkg_to_percent(c), st.gkg_to_percent(si), st.gkg_to_percent(sa)
            )
        ]
        for c, si, sa in zip(clay, silt, sand)
    ]


class TestSoilTextureIdx:
    def test_matches_the_per_row_classifier_on_random_samples(self):
        # Arrange
        rng = np.random.default_rng(7)
        clay, silt, sand = (rng.uniform(0, 1000, 20_000) for _ in range(3))

        # Act
        got = st.soil_texture_idx(clay, silt, sand)

        # Assert
        assert got.tolist() == _per_row(clay, silt, sand)

    def test_matches_on_every_boundary_value(self):
        # Arrange -- whole g/kg steps hit each rule's percentage edge exactly.
        grid = np.arange(0, 1001, 10, dtype=float)
        clay, silt = np.meshgrid(grid, grid)
        clay, silt = clay.ravel(), silt.ravel()
        sand = np.clip(1000.0 - clay - silt, 0, None)

        # Act
        got = st.soil_texture_idx(clay, silt, sand)

        # Assert
        assert got.tolist() == _per_row(clay, silt, sand)

    @pytest.mark.parametrize(
        "clay, silt, sand, expected",
        [
            (450, 450, 100, "Silty Clay"),
            (300, 100, 600, "Sandy Clay Loam"),
            (150, 400, 450, "Loam"),
            (50, 850, 100, "Silt"),
            (50, 50, 900, "Sand"),
        ],
    )
    def test_known_textures(self, clay, silt, sand, expected):
        got = st.soil_texture_idx([clay], [silt], [sand])

        assert got.tolist() == [st.SOIL_TEXTURE_TO_IDX[expected]]


class TestSoilTypeIdx:
    def test_names_keep_their_texture_positions(self):
        got = st.soil_type_idx(["Clay", "Loam", "Undifferentiated"])

        assert got.tolist() == [11, 4, 12]

    def test_unknown_names_and_nulls_are_undifferentiated(self):
        got = st.soil_type_idx(["Mud", None, np.nan])

        assert got.tolist() == [12, 12, 12]
