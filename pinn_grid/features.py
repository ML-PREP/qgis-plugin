"""Model-ready rows from gridded inputs, as the grid PINN was trained and run.

Mirrors the notebook's inference (cotabato_grid_v2-9, section 5): cells with
a missing input, a slope under 10 degrees or no bulk density are not
predicted, the soil-map codes are renamed to the names the model's
StringLookup knows, and both soil indices are derived. Bulk density stays in
cg/cm3: the v2-9 physics converts it itself (bulk_density_unit="cg/cm3").
"""

import json

import numpy as np
import pandas as pd

from py_files.soil_texture import (
    UNDIFFERENTIATED_NAME,
    soil_texture_idx,
    soil_type_idx,
)

from .inputs import BULK_DENSITY_INPUT, MODEL_COLUMNS, SLOPE_INPUT, SOIL_TYPE_INPUT

# preprocessing() drops gentler cells, in training and in the notebook.
MIN_SLOPE_DEG = 10.0


def load_soil_recode(path):
    """{soil code: soil-map name} from a model's soil_type_recode.json."""
    with open(path, encoding="utf-8") as handle:
        codes = json.load(handle)["codes"]
    return {int(float(code)): name for code, name in codes.items()}


def valid_cells(columns):
    """Cells that can be predicted: every input present, steep, with soil."""
    valid = np.logical_and.reduce([np.isfinite(values) for values in columns.values()])
    with np.errstate(invalid="ignore"):
        valid &= columns[SLOPE_INPUT] >= MIN_SLOPE_DEG
        valid &= columns[BULK_DENSITY_INPUT] > 0
    return valid


def recode_soil_types(codes, recode):
    """(soil-map names, codes not in `recode`); unknown codes are Undifferentiated."""
    rounded = np.rint(np.asarray(codes, dtype=float)).astype(np.int64)
    names = np.array([recode.get(code, UNDIFFERENTIATED_NAME) for code in rounded], dtype=object)
    unknown = set(np.unique(rounded).tolist()) - set(recode)
    return names, unknown


def model_frame(columns, recode):
    """(DataFrame in the model's column names, unknown soil codes).

    `columns` holds one array per input name, for the valid cells only.
    """
    names, unknown = recode_soil_types(columns[SOIL_TYPE_INPUT], recode)
    frame = pd.DataFrame({
        MODEL_COLUMNS[name]: np.asarray(values, dtype=np.float32)
        for name, values in columns.items()
        if name != SOIL_TYPE_INPUT
    })
    frame["type"] = names
    frame["soil_texture_idx"] = soil_texture_idx(
        frame["Clay_mean"], frame["Silt_mean"], frame["Sand_mean"]
    ).astype(np.float32)
    frame["soil_type_idx"] = soil_type_idx(names).astype(np.float32)
    return frame, unknown
