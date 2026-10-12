"""Array maths that prepares the depositional-routing inputs. No Qt here.

numpy is imported inside each function: it is not guaranteed in every QGIS
build, and this module is reachable from the plugin's load path.
"""

NODATA = -9999.0

# Step 0 baseline: slope class upper bounds (degrees) and the alpha angle of
# each class. Class 0 (slope < 10°) has no alpha.
SLOPE_CLASS_UPPER_BOUNDS = (10, 12, 14, 16, 18, 20, 25, 30, 35, 40, 45)
ALPHA_BY_SLOPE_CLASS = (
    None, 9.0, 11.0, 13.0, 15.0, 17.0, 19.0, 22.0, 25.0, 28.0, 29.0, 30.0,
)

SOURCE_FALLBACK_PERCENTILE = 95
SOURCE_LAST_RESORT_PERCENTILE = 99


class DepositionInputError(Exception):
    """The PINN output cannot seed depositional routing."""


def clamp_dfi(dfi):
    """Probabilities clamped to [0, 1]; everything else becomes NODATA.

    The C++ engine crashes on values like 1.000001.
    """
    import numpy as np

    clean = dfi.astype(np.float32)
    valid = np.isfinite(clean) & (clean > -1.0)
    clean[valid] = np.clip(clean[valid], 0.0, 1.0)
    clean[~valid] = NODATA
    return clean


def alpha_from_slope(slope_drop, nodata):
    """Baseline alpha angle per cell from a TauDEM slope (drop/distance)."""
    import numpy as np

    bounds = np.asarray(SLOPE_CLASS_UPPER_BOUNDS, dtype=np.float64)
    alphas = np.asarray(
        [np.nan if a is None else a for a in ALPHA_BY_SLOPE_CLASS], dtype=np.float32
    )

    # TauDEM reports slope as a drop/distance ratio, the classes are degrees.
    slope_degrees = np.degrees(np.arctan(slope_drop))

    valid = (slope_drop >= 0) & (slope_drop != nodata)
    alpha = np.full(slope_drop.shape, NODATA, dtype=np.float32)

    classes = np.searchsorted(bounds, slope_degrees[valid], side="right")
    has_alpha = np.isfinite(alphas[classes])

    rows, cols = np.nonzero(valid)
    alpha[rows[has_alpha], cols[has_alpha]] = alphas[classes[has_alpha]]
    return alpha


def source_mask(dfi, nodata, threshold, log):
    """1.0 where debris flow starts, NODATA elsewhere.

    Cells above `threshold` are sources. When none qualify the threshold drops
    to the 95th percentile (99th if that is zero), then to the maximum value.
    Raises DepositionInputError when no cell can be a source.
    """
    import numpy as np

    valid_dfi = dfi[(dfi != nodata) & np.isfinite(dfi)]
    if valid_dfi.size == 0:
        raise DepositionInputError("DFI raster contains no valid probability cells.")

    mask = (dfi > threshold) & (dfi != nodata)
    count = np.count_nonzero(mask)

    if count == 0:
        log(
            f"   > [Notice] No cells found > {threshold}. "
            "Falling back to dynamic 95th percentile..."
        )
        dynamic = float(np.percentile(valid_dfi, SOURCE_FALLBACK_PERCENTILE))
        if dynamic <= 0.0:
            dynamic = float(np.percentile(valid_dfi, SOURCE_LAST_RESORT_PERCENTILE))

        log(f"   > Using dynamic threshold: {dynamic:.4f}")
        mask = (dfi > dynamic) & (dfi != nodata)
        count = np.count_nonzero(mask)

        if count == 0:
            max_val = float(np.max(valid_dfi))
            if max_val > 0.0:
                mask = (dfi >= max_val) & (dfi != nodata)
                count = np.count_nonzero(mask)

    log(f"   > Source cells designated: {count}")
    if count == 0:
        raise DepositionInputError(
            "Zero source cells met the condition. Check PINN output."
        )

    source = np.full(dfi.shape, NODATA, dtype=np.float32)
    source[mask] = 1.0
    return source
