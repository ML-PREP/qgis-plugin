"""Fixed parameters of the EIL pipeline."""

from types import MappingProxyType

# Slivers smaller than this (map units²) are merged away after tiling, and the
# Go engine discards slope units below it.
SLOPE_UNIT_AREA_MIN = 5000

# Handed to the Go zonal engine. `maxIter` is pinned to 1 for now, so the Max
# Iteration box on the EIL tab is not read; its default there is 10.
ZONAL_PARAMS = MappingProxyType(
    {
        "areaMin": SLOPE_UNIT_AREA_MIN,
        "thresh": 5000,
        "cvMin": 0.15,
        "rf": 10,
        "maxIter": 1,
        "tileCols": 3,
        "tileRows": 3,
        "tileOverlap": 500,
    }
)

DEFAULT_MAX_ITERATIONS = 10

DEPOSITION_CORES = 4
DEPOSITION_OUTPUT_SUBDIR = "deposition_zone_output"
DEFAULT_OUTPUT_SUBDIR = "EIL_Results"

# UI combobox -> base name the Keras model expects; the Go engine appends
# "_mean" (e.g. Slope -> Slope_mean). The DEM is always fed in as "Elev".
DEM_INPUT_NAME = "Elev"
MODEL_INPUT_NAMES = MappingProxyType(
    {
        "comboBox_Slope": "Slope",
        "comboBox_BulkDensity": "BUK",
        "comboBox_Clay": "Clay",
        "comboBox_Sand": "Sand",
        "comboBox_Silt": "Silt",
        "comboBox_PRC": "Prc",
        "comboBox_SoilThickness": "SoilThc",
        "comboBox_ContributingFactor": "ContributingFactor",
        "comboBox_PGA": "PGA2",
        "comboBox_SoilType": "type",
    }
)
