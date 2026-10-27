"""Array maths of the depositional-zone flow (ported from MLPREP Plugin v4.4).

Pure numpy, no I/O: prep.py streams rasters through these functions block by
block, so a full-size DEM never has to fit in memory at once.
"""

from dataclasses import dataclass

import numpy as np

NODATA = -9999.0

# The shipped ML-DFI model was fitted on predictors smoothed by exactly a 3x3
# uniform mean over valid cells (verified against the Makilala *_LPF_* rasters:
# correlation 1.0000, max abs difference 3.3e-9).
LPF_WINDOW = 3

# Engine constants, copied from step4_deposition_zone_mpi.cpp so the inputs
# satisfy its validation rules.
ALPHA_VALID_MIN, ALPHA_VALID_MAX = 1.0, 90.0  # kAlphaAngleValidMin/Max
ALPHA_FIXED_MIN, ALPHA_FIXED_MAX = 6.0, 72.0  # kFixedAlphaMin/Max
DFI_MID = 0.69  # the routing engine's --dfi-mid

# TWI floors the slope tangent so flats do not divide by zero.
MIN_TAN_SLOPE = 0.001


@dataclass(frozen=True)
class Block:
    """Rows [row0, row1) of a raster, read with halo rows [pad0, pad1)."""

    row0: int
    row1: int
    pad0: int
    pad1: int

    def trim(self, padded):
        """The block's own rows out of an array read over the halo."""
        top = self.row0 - self.pad0
        return padded[top : top + (self.row1 - self.row0)]


def blocks(height, block_rows, halo=0):
    """Row blocks covering a raster, each with `halo` extra rows either side."""
    for row0 in range(0, height, block_rows):
        row1 = min(row0 + block_rows, height)
        yield Block(row0, row1, max(row0 - halo, 0), min(row1 + halo, height))


def _valid(array, nodata):
    valid = np.isfinite(array)
    if nodata is not None:
        valid &= array != np.float32(nodata)
    return valid


def slope_degrees(tangent, nodata):
    """TauDEM's dinfflowdir slope (rise/run) in degrees, as Step 0 expects."""
    valid = _valid(tangent, nodata)
    out = np.full(tangent.shape, NODATA, dtype=np.float32)
    out[valid] = np.degrees(np.arctan(tangent[valid]))
    return out


def clamp_dfi(dfi):
    """Probabilities clamped to [0, 1]; the engine aborts on e.g. 1.000001."""
    clean = dfi.astype(np.float32)
    valid = np.isfinite(clean) & (clean > -1.0)
    clean[valid] = np.clip(clean[valid], 0.0, 1.0)
    clean[~valid] = NODATA
    return clean


def _shifted(padded, dr, dc, shape):
    return padded[1 + dr : 1 + dr + shape[0], 1 + dc : 1 + dc + shape[1]]


def curvatures(z, cell_size):
    """(profile, plan) curvature in 1/m (Zevenbergen & Thorne) of `z`.

    `z` holds NaN where the DEM has no data. A missing neighbour takes the
    centre cell's elevation, so the domain edge does not produce huge
    artificial curvature. Sign and scale match the rasters the ML-DFI model was
    trained on: profile -2(DG^2+EH^2+FGH)/p, plan -2(DH^2+EG^2-FGH)/p^1.5.
    """
    invalid = np.isnan(z)
    padded = np.pad(z, 1, mode="constant", constant_values=np.nan)

    def nb(dr, dc):
        a = _shifted(padded, dr, dc, z.shape)
        return np.where(np.isnan(a), z, a)

    z1, z2, z3 = nb(-1, -1), nb(-1, 0), nb(-1, 1)
    z4, z6 = nb(0, -1), nb(0, 1)
    z7, z8, z9 = nb(1, -1), nb(1, 0), nb(1, 1)
    length = cell_size

    d = ((z4 + z6) / 2.0 - z) / length**2
    e = ((z2 + z8) / 2.0 - z) / length**2
    f = (-z1 + z3 + z7 - z9) / (4.0 * length**2)
    g = (-z4 + z6) / (2.0 * length)
    h = (z2 - z8) / (2.0 * length)

    p = g**2 + h**2
    ok = ~invalid & (p > 0)
    prof = np.zeros(z.shape, dtype=np.float32)
    plan = np.zeros(z.shape, dtype=np.float32)
    prof[ok] = -2.0 * (d * g**2 + e * h**2 + f * g * h)[ok] / p[ok]
    plan[ok] = -2.0 * (d * h**2 + e * g**2 - f * g * h)[ok] / p[ok] ** 1.5
    prof[invalid] = NODATA
    plan[invalid] = NODATA
    return prof, plan


def twi(sca, tan_slope, sca_nodata, slope_nodata):
    """Topographic Wetness Index ln(SCA / tan(slope)) from TauDEM grids."""
    valid = _valid(sca, sca_nodata) & _valid(tan_slope, slope_nodata)
    valid &= (sca > 0) & (tan_slope >= 0)
    out = np.full(sca.shape, NODATA, dtype=np.float32)
    tan_safe = np.maximum(tan_slope[valid], MIN_TAN_SLOPE)
    out[valid] = np.log(sca[valid] / tan_safe)
    return out


def window_mean(values, valid, nodata, window=LPF_WINDOW):
    """Mean over each window of the valid cells only; invalid cells stay nodata."""
    pad = window // 2
    shape = values.shape
    sums = np.zeros((shape[0] + 2 * pad, shape[1] + 2 * pad))
    counts = np.zeros_like(sums)
    sums[pad : pad + shape[0], pad : pad + shape[1]] = np.where(valid, values, 0.0)
    counts[pad : pad + shape[0], pad : pad + shape[1]] = valid

    total = np.zeros(shape)
    count = np.zeros(shape)
    for dr in range(-pad, pad + 1):
        for dc in range(-pad, pad + 1):
            total += sums[
                pad + dr : pad + dr + shape[0], pad + dc : pad + dc + shape[1]
            ]
            count += counts[
                pad + dr : pad + dr + shape[0], pad + dc : pad + dc + shape[1]
            ]

    out = np.full(shape, nodata, dtype=np.float32)
    ok = valid & (count > 0)
    out[ok] = total[ok] / count[ok]
    return out


def boundary_ring(valid):
    """Valid cells with at least one of their eight neighbours outside the domain.

    dinfdistdown NoDatas any cell whose flow leaves the domain before reaching
    a stream, and the NoData then spreads upstream. Marking this ring as
    outlets lets every flow path end somewhere.
    """
    padded = np.zeros((valid.shape[0] + 2, valid.shape[1] + 2), dtype=bool)
    padded[1:-1, 1:-1] = valid
    interior = np.ones_like(valid)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                interior &= _shifted(padded, dr, dc, valid.shape)
    return valid & ~interior


def mask_non_positive(data, nodata):
    """(copy with non-positive cells as nodata, how many were masked).

    A unit weight of 0 divides the FoS pore-pressure term by zero; bulk-density
    rasters often use 0 as background while declaring another NoData value.
    """
    clean = data.astype(np.float32)
    bad = ~np.isfinite(clean) | (clean <= 0)
    masked = int((bad & (clean != np.float32(nodata))).sum())
    clean[bad] = nodata
    return clean, masked


def unit_weight_scale(median_bulk_density):
    """(factor to kN/m3, description) for a bulk-density raster's encoding."""
    if median_bulk_density > 50:
        return 0.0981, "cg/cm3 (g/cm3 x 100)"
    if 0.5 <= median_bulk_density <= 3.0:
        return 9.81, "g/cm3"
    return 1.0, "already kN/m3"


@dataclass(frozen=True)
class EngineInputs:
    alpha: np.ndarray
    dfi: np.ndarray
    source: np.ndarray
    counts: dict


def engine_inputs(terrain, alpha, alpha_nodata, dfi, dfi_nodata, source_mask):
    """Alpha, DFI and source rasters the routing engine accepts as-is.

    - alpha outside [1, 90] is neutralised to NoData (the engine aborts on it);
    - DFI gaps on terrain take DFI_MID, inside the engine's deadband, so cells
      without a prediction neither speed up nor slow down the flow;
    - a source is a FoS-refined source cell (mask value 1) whose alpha lies in
      the engine's fixed range [6, 72]; the engine silently drops any other.
    """
    alpha_present = terrain & _valid(alpha, alpha_nodata)
    alpha_bad = alpha_present & ((alpha < ALPHA_VALID_MIN) | (alpha > ALPHA_VALID_MAX))
    alpha_present &= ~alpha_bad
    alpha_out = np.full(alpha.shape, NODATA, dtype=np.float32)
    alpha_out[alpha_present] = alpha[alpha_present]

    dfi_ok = terrain & _valid(dfi, dfi_nodata) & (dfi >= 0.0) & (dfi <= 1.0)
    dfi_out = np.full(dfi.shape, NODATA, dtype=np.float32)
    dfi_out[terrain] = DFI_MID
    dfi_out[dfi_ok] = dfi[dfi_ok]

    candidates = terrain & (source_mask == 1)
    in_fixed_range = (alpha >= ALPHA_FIXED_MIN) & (alpha <= ALPHA_FIXED_MAX)
    alpha_usable = alpha_present & in_fixed_range
    legal = candidates & alpha_usable
    source_out = np.full(source_mask.shape, NODATA, dtype=np.float32)
    source_out[terrain] = 0.0
    source_out[legal] = 1.0

    counts = {
        "terrain": int(terrain.sum()),
        "source_candidates": int(candidates.sum()),
        "legal_sources": int(legal.sum()),
        "alpha_neutralised": int(alpha_bad.sum()),
        "dfi_predicted": int(dfi_ok.sum()),
    }
    return EngineInputs(alpha_out, dfi_out, source_out, counts)
