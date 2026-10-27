"""`soil_type_idx`, input 10 of the rainfall-aware PINN.

Mirrors the training notebook (mlprep/notebooks/cotabato_new_slope_unit_v2-8,
cell 11): the index comes from the soil-map `type` label, the 12 USDA texture
names keep their SOIL_TEXTURE_TO_IDX positions, and everything else --
"Undifferentiated", nulls, unrecognized labels -- is class 12.
"""

import os
import sys

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("seaborn", reason="py_files.helpers imports seaborn")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_files import helpers  # noqa: E402

USDA = [
    "Sand", "Loamy Sand", "Sandy Loam", "Silt Loam", "Loam", "Silt",
    "Sandy Clay Loam", "Clay Loam", "Silty Clay Loam", "Sandy Clay",
    "Silty Clay", "Clay",
]


class TestSoilTypeIndexV3:
    def test_usda_names_keep_their_texture_positions(self):
        # Arrange
        df = pd.DataFrame({"type": USDA})

        # Act
        got = helpers.add_soil_type_index_v3(df)["soil_type_idx"].tolist()

        # Assert
        assert got == list(range(12))
        assert got == [helpers.SOIL_TEXTURE_TO_IDX[name] for name in USDA]

    @pytest.mark.parametrize("label", ["Undifferentiated", None, "Unknown", "45.0"])
    def test_anything_else_is_undifferentiated(self, label):
        # "Unknown" is what pinn_inference writes for numeric raster codes.
        df = pd.DataFrame({"type": [label]})
        assert helpers.add_soil_type_index_v3(df)["soil_type_idx"].tolist() == [12]

    def test_there_are_thirteen_classes(self):
        assert helpers.NUM_SOIL_TYPE_CLASSES_V3 == 13
        assert helpers.UNDIFFERENTIATED_IDX == 12

    def test_missing_type_column_is_an_error(self):
        with pytest.raises(KeyError, match="type"):
            helpers.add_soil_type_index_v3(pd.DataFrame({"Slope_mean": [20.0]}))
