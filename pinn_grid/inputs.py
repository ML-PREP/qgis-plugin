"""The EIL inputs a grid PINN reads, and the training column each becomes.

Keys are the EIL tab's input names (eil/params.py MODEL_INPUT_NAMES, plus
"Elev" for the DEM); values are the column names of the grid the model was
trained on (mlprep/datasets/combined_mapped.gpkg). No imports: the EIL
pipeline reads this inside QGIS.
"""

SOIL_TYPE_INPUT = "type"
SLOPE_INPUT = "Slope"
BULK_DENSITY_INPUT = "BUK"

MODEL_COLUMNS = {
    "Elev": "Elev_mean",
    SLOPE_INPUT: "Slope_mean",
    BULK_DENSITY_INPUT: "BUK_mean",
    "Clay": "Clay_mean",
    "Sand": "Sand_mean",
    "Silt": "Silt_mean",
    "Prc": "Prc_mean",
    "SoilThc": "SoilThc_mean",
    "ContributingFactor": "ContributingFactor_mean",
    "PGA2": "PGA2_max",
    SOIL_TYPE_INPUT: "type",
}


def missing_inputs(rasters):
    """Input names the grid model needs that `rasters` does not provide."""
    return [name for name in MODEL_COLUMNS if not rasters.get(name)]
