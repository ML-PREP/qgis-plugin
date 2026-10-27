"""What the grid route writes, strip by strip, and the dashboard's tallies.

    Final_Hazard_Grid.tif                    fold-mean susceptibility (the map layer)
    overall_output/Grid_Physics.tif          the notebook's six bands (section 5)
    overall_output/objective2_rasters/*.tif  the depositional zone's FoS inputs

The objective-2 rasters follow pinn_inference.py's hand-off: Susceptibility.tif
is 1 where the susceptibility reaches the threshold and NoData elsewhere (=
not susceptible), the others carry the fold-mean physics. FactorOfSafety.tif
is the model's own fold-mean FoS, which the FoS refinement reads as-is instead
of deriving one.
"""

import os
from contextlib import ExitStack

import numpy as np
import rasterio
from rasterio.features import rasterize

from eil.hazard_stats import HAZARD_CLASSES, HIGH, HIGH_THRESHOLD, LOW, MODERATE, MODERATE_THRESHOLD

NODATA = -9999.0
HAZARD_RASTER = "Final_Hazard_Grid.tif"
OVERALL_DIR = "overall_output"
PHYSICS_RASTER = "Grid_Physics.tif"
OBJECTIVE2_DIR = "objective2_rasters"
FRICTION_ANGLE_UNIT = "radians"

PHYSICS_BANDS = (
    "susceptibility_mean",
    "susceptibility_std",
    "cohesion_kpa",
    "friction_deg",
    "displacement_cm",
    "wetness_m",
)
# result key -> (file name, summary value); names as pinn_inference.py writes them.
OBJECTIVE2_RASTERS = {
    "cohesion_tif": ("Cohesion.tif", "cohesion_kpa"),
    "friction_angle_tif": ("Internal_Friction_Angle.tif", "friction_rad"),
    "wetness_tif": ("Saturation_Ratio.tif", "wetness_m"),
    "factor_of_safety_tif": ("FactorOfSafety.tif", "fos"),
}
SUSCEPTIBILITY_KEY = "susceptibility_tif"
SUSCEPTIBILITY_FILE = "Susceptibility.tif"


def summarize_folds(stacked):
    """Per-cell ensemble values from predict()'s (n_folds, n_cells) arrays."""
    friction_rad = stacked["friction_rad"].mean(axis=0)
    return {
        "susceptibility_mean": stacked["susceptibility"].mean(axis=0),
        "susceptibility_std": stacked["susceptibility"].std(axis=0),
        "cohesion_kpa": stacked["cohesion_kpa"].mean(axis=0),
        "friction_rad": friction_rad,
        "friction_deg": np.degrees(friction_rad),
        "displacement_cm": stacked["displacement_cm"].mean(axis=0),
        "wetness_m": stacked["wetness_m"].mean(axis=0),
        "fos": stacked["fos"].mean(axis=0),
    }


def hazard_classes(susceptibility):
    """Index into HAZARD_CLASSES per cell, by eil.hazard_stats' thresholds."""
    return np.select(
        [susceptibility > HIGH_THRESHOLD, susceptibility >= MODERATE_THRESHOLD],
        [HAZARD_CLASSES.index(HIGH), HAZARD_CLASSES.index(MODERATE)],
        default=HAZARD_CLASSES.index(LOW),
    )


def _on_grid(shape, valid, values):
    """`values` (one per valid cell) spread onto a strip, NoData elsewhere."""
    out = np.full(shape, NODATA, dtype=np.float32)
    out[valid] = values
    return out


class GridWriter:
    """Opens every output raster on the grid; write() fills one strip."""

    def __init__(self, grid, output_folder, susceptibility_threshold):
        self._threshold = susceptibility_threshold
        overall = os.path.join(output_folder, OVERALL_DIR)
        objective2 = os.path.join(overall, OBJECTIVE2_DIR)
        os.makedirs(objective2, exist_ok=True)
        self.paths = {
            "hazard_raster": os.path.join(output_folder, HAZARD_RASTER),
            "physics_raster": os.path.join(overall, PHYSICS_RASTER),
            SUSCEPTIBILITY_KEY: os.path.join(objective2, SUSCEPTIBILITY_FILE),
            **{key: os.path.join(objective2, name)
               for key, (name, _value) in OBJECTIVE2_RASTERS.items()},
        }
        self._profile = {
            "driver": "GTiff", "crs": grid.crs, "transform": grid.transform,
            "width": grid.width, "height": grid.height, "dtype": "float32",
            "nodata": NODATA, "compress": "lzw", "tiled": True, "BIGTIFF": "IF_SAFER",
        }
        self._stack = ExitStack()
        self._datasets = {}

    def __enter__(self):
        for key, path in self.paths.items():
            count = len(PHYSICS_BANDS) if key == "physics_raster" else 1
            self._datasets[key] = self._stack.enter_context(
                rasterio.open(path, "w", count=count, **self._profile)
            )
        for band, name in enumerate(PHYSICS_BANDS, start=1):
            self._datasets["physics_raster"].set_band_description(band, name)
        return self

    def __exit__(self, *exc_info):
        return self._stack.__exit__(*exc_info)

    def write(self, window, valid, summary):
        """Write one strip; `valid` is its 2-D mask of predicted cells."""
        shape = valid.shape
        datasets = self._datasets
        susceptibility = summary["susceptibility_mean"]
        datasets["hazard_raster"].write(_on_grid(shape, valid, susceptibility), 1, window=window)
        for band, name in enumerate(PHYSICS_BANDS, start=1):
            datasets["physics_raster"].write(
                _on_grid(shape, valid, summary[name]), band, window=window
            )
        susceptible = np.where(susceptibility >= self._threshold, 1.0, NODATA)
        datasets[SUSCEPTIBILITY_KEY].write(_on_grid(shape, valid, susceptible), 1, window=window)
        for key, (_name, value) in OBJECTIVE2_RASTERS.items():
            datasets[key].write(_on_grid(shape, valid, summary[value]), 1, window=window)


class HazardTally:
    """Cells per hazard class, overall and per municipality (by cell centre).

    `municipalities` is [(name, geometry)] in the grid CRS, or None; `mask`
    (geometries) limits the count to a region of interest, as the slope-unit
    dashboard ranks only the clipped map.
    """

    def __init__(self, grid, municipalities=None, mask=None):
        self._grid = grid
        self._names = sorted({name for name, _geom in municipalities or ()})
        index = {name: number for number, name in enumerate(self._names, start=1)}
        self._shapes = [(geom, index[name]) for name, geom in municipalities or ()]
        self._mask = list(mask) if mask is not None else None
        self._overall = np.zeros(len(HAZARD_CLASSES), dtype=np.int64)
        self._by_municipality = np.zeros((len(self._names) + 1, len(HAZARD_CLASSES)), np.int64)

    def _burn(self, shapes, window, shape):
        return rasterize(shapes, out_shape=shape, transform=self._grid.window_transform(window),
                         fill=0, dtype="int32")

    def add(self, window, valid, susceptibility):
        counted = valid.copy()
        if self._mask is not None:
            counted &= self._burn([(g, 1) for g in self._mask], window, valid.shape) == 1
        classes = np.full(valid.shape, -1)
        classes[valid] = hazard_classes(susceptibility)
        picked = classes[counted]
        self._overall += np.bincount(picked, minlength=len(HAZARD_CLASSES))
        if self._shapes:
            ids = self._burn(self._shapes, window, valid.shape)[counted]
            np.add.at(self._by_municipality, (ids, picked), 1)

    def result(self):
        def counts(row):
            return {name: int(n) for name, n in zip(HAZARD_CLASSES, row)}

        return {
            "hazard_counts": counts(self._overall),
            "municipality_tallies": {
                name: counts(self._by_municipality[number])
                for number, name in enumerate(self._names, start=1)
                if self._by_municipality[number].any()
            },
        }
