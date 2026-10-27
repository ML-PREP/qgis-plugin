"""Soil indices for the grid PINN (v2-9), as it was trained.

`classify_soil_texture` is a verbatim copy of mlprep/py_files/helpers.py, the
classifier the grid model's `soil_texture_idx` input was built with. The
plugin's py_files/helpers.py keeps the older classifier the slope-unit models
are served with, and is left alone so their predictions do not shift.

`soil_texture_idx` applies the same rules to whole arrays at once (a 25 m grid
of a province is ~17 M cells; the per-row classifier takes minutes there), and
tests/test_soil_texture.py holds it equal to the per-row version.
"""

import numpy as np
import pandas as pd

SOIL_TEXTURE_TO_IDX = {
    'Sand': 0, 'Loamy Sand': 1, 'Sandy Loam': 2, 'Silt Loam': 3,
    'Loam': 4, 'Silt': 5, 'Sandy Clay Loam': 6, 'Clay Loam': 7,
    'Silty Clay Loam': 8, 'Sandy Clay': 9, 'Silty Clay': 10, 'Clay': 11,
}

UNDIFFERENTIATED_NAME = 'Undifferentiated'
UNDIFFERENTIATED_IDX = len(SOIL_TEXTURE_TO_IDX)  # 12
SOIL_TYPE_TO_IDX_V3 = {**SOIL_TEXTURE_TO_IDX, UNDIFFERENTIATED_NAME: UNDIFFERENTIATED_IDX}


def classify_soil_texture(clay_pct, silt_pct, sand_pct):
    """USDA texture class of one sample; percentages 0-100."""
    c, si, sa = clay_pct, silt_pct, sand_pct

    # High clay classes (check first)
    if c >= 40 and si >= 40:
        return 'Silty Clay'
    if c >= 35 and sa >= 45:
        return 'Sandy Clay'
    if c >= 40:
        return 'Clay'

    # Medium-high clay (27-40%) — Sandy Clay Loam takes precedence over
    # Clay Loam in the high-sand corner.
    if 27 <= c < 40 and sa >= 45:
        return 'Sandy Clay Loam'
    if 27 <= c < 40 and sa < 20:
        return 'Silty Clay Loam'
    if 27 <= c < 40:
        return 'Clay Loam'

    # Sandy Clay Loam: 20-35% clay, <28% silt, >=45% sand
    if 20 <= c < 35 and si < 28 and sa >= 45:
        return 'Sandy Clay Loam'

    # High silt classes
    if si >= 80 and c < 12:
        return 'Silt'
    if si >= 50 and c < 27:
        return 'Silt Loam'

    # Sandy classes
    if sa >= 85 and c < 10:
        return 'Sand'
    if 70 <= sa < 90 and c < 15:
        return 'Loamy Sand'

    # Loam: 7-27% clay, 28-50% silt, 23-52% sand. Checked before Sandy Loam
    # because the two overlap in the c<20, 43<=sa<=52 corner.
    if 7 <= c < 27 and 28 <= si < 50 and 23 <= sa <= 52:
        return 'Loam'

    # Sandy Loam (broader): 0-20% clay, 0-50% silt, 43-85% sand
    if c < 20 and si < 50 and sa >= 43:
        return 'Sandy Loam'

    # Fallback by dominant fraction
    if sa >= si and sa >= c:
        return 'Sandy Loam'
    if si >= sa and si >= c:
        return 'Silt Loam'
    return 'Loam'


def gkg_to_percent(value_gkg):
    """g/kg to percent, with the same float arithmetic as training."""
    return (value_gkg / 1000.0) * 100.0


def soil_texture_idx(clay_gkg, silt_gkg, sand_gkg):
    """`soil_texture_idx` per sample from clay/silt/sand in g/kg (int array).

    The rules of `classify_soil_texture`, first match wins, in its order.
    """
    c = gkg_to_percent(np.asarray(clay_gkg, dtype=float))
    si = gkg_to_percent(np.asarray(silt_gkg, dtype=float))
    sa = gkg_to_percent(np.asarray(sand_gkg, dtype=float))
    medium_clay = (c >= 27) & (c < 40)
    rules = (
        (c >= 40) & (si >= 40), 'Silty Clay',
        (c >= 35) & (sa >= 45), 'Sandy Clay',
        c >= 40, 'Clay',
        medium_clay & (sa >= 45), 'Sandy Clay Loam',
        medium_clay & (sa < 20), 'Silty Clay Loam',
        medium_clay, 'Clay Loam',
        (c >= 20) & (c < 35) & (si < 28) & (sa >= 45), 'Sandy Clay Loam',
        (si >= 80) & (c < 12), 'Silt',
        (si >= 50) & (c < 27), 'Silt Loam',
        (sa >= 85) & (c < 10), 'Sand',
        (sa >= 70) & (sa < 90) & (c < 15), 'Loamy Sand',
        (c >= 7) & (c < 27) & (si >= 28) & (si < 50) & (sa >= 23) & (sa <= 52), 'Loam',
        (c < 20) & (si < 50) & (sa >= 43), 'Sandy Loam',
        (sa >= si) & (sa >= c), 'Sandy Loam',
        (si >= sa) & (si >= c), 'Silt Loam',
    )
    conditions = list(rules[0::2])
    choices = [SOIL_TEXTURE_TO_IDX[name] for name in rules[1::2]]
    return np.select(conditions, choices, default=SOIL_TEXTURE_TO_IDX['Loam'])


def soil_type_idx(type_names):
    """`soil_type_idx` from soil-map names; unknown names and nulls are 12."""
    mapped = pd.Series(type_names, dtype=object).map(SOIL_TYPE_TO_IDX_V3)
    return mapped.fillna(UNDIFFERENTIATED_IDX).astype(int).to_numpy()
