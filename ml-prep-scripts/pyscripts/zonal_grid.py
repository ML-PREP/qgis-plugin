"""Zonal statistics on a slope-unit label raster. Embedded in the zonal engine.

    zones <units.gpkg> <zones.tif> <grid.tif> [<grid.tif> ...]
    stats <zones.tif> <value.tif> <out.npz> <nodata> <mean|min|majority> <nearest|bilinear>

`zones` burns every slope unit's row_id + 1 into an int32 raster on the finest
of the given grids (the DEM, when there is one). It runs once per pipeline.

`stats` warps one value raster onto that grid and reduces it per unit in row
blocks, so memory stays at a few hundred MB whatever the raster size. It
replaces a rasterstats pass that looped over every polygon in Python and,
because it only counted value pixels whose centre fell inside a polygon, gave
no value at all to units smaller than a coarse pixel. On the label grid every
5 m cell of a unit samples the value raster, so those units get the value of
the coarse pixel(s) they lie in.

Output (stats): <out.npz> with row_id and values, the format the merge step
joins on. numpy is the only module-level import, so the reducers can be tested
without the geospatial stack.
"""

import json
import os
import sys

import numpy as np

ZONE_NODATA = 0
UNIT_COUNT_TAG = "MLPREP_UNIT_COUNT"
BLOCK_ROWS = 512
ZONE_BLOCK_SIZE = 512
GDAL_CACHE_MB = 256
RULES = ("mean", "min", "majority")
RESAMPLINGS = ("nearest", "bilinear")


# ---------------------------------------------------------------------------
# Reducers: fed (unit id, value) pairs block by block; unit ids are row_id + 1
# ---------------------------------------------------------------------------

class _Accumulator:
    def __init__(self, unit_count):
        self.size = unit_count + 1

    def _check(self, zones):
        if zones.size and zones.max() >= self.size:
            raise ValueError(
                f"unit id {int(zones.max())} exceeds the unit count {self.size - 1}; "
                "the zone raster does not match its slope units"
            )


class MeanAccumulator(_Accumulator):
    def __init__(self, unit_count):
        super().__init__(unit_count)
        self.sums = np.zeros(self.size, dtype="float64")
        self.counts = np.zeros(self.size, dtype="int64")

    def add(self, zones, values):
        self._check(zones)
        self.sums += np.bincount(zones, weights=values, minlength=self.size)
        self.counts += np.bincount(zones, minlength=self.size)

    def result(self):
        with np.errstate(invalid="ignore", divide="ignore"):
            means = self.sums / self.counts
        return means[1:]


class MinAccumulator(_Accumulator):
    def __init__(self, unit_count):
        super().__init__(unit_count)
        self.mins = np.full(self.size, np.inf, dtype="float64")

    def add(self, zones, values):
        self._check(zones)
        if not zones.size:
            return
        order = np.argsort(zones, kind="stable")
        ids, values = zones[order], values[order]
        starts = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1]])
        unit_ids = ids[starts]
        self.mins[unit_ids] = np.minimum(
            self.mins[unit_ids], np.minimum.reduceat(values, starts)
        )

    def result(self):
        mins = self.mins[1:].copy()
        mins[np.isinf(mins)] = np.nan
        return mins


class MajorityAccumulator(_Accumulator):
    """Most frequent value per unit; a tie goes to the smallest (as rasterstats)."""

    def __init__(self, unit_count):
        super().__init__(unit_count)
        self.counts = {}

    def add(self, zones, values):
        self._check(zones)
        for value in np.unique(values):
            per_unit = np.bincount(zones[values == value], minlength=self.size)
            self.counts[value] = self.counts.get(value, 0) + per_unit

    def result(self):
        if not self.counts:
            return np.full(self.size - 1, np.nan)
        classes = np.array(sorted(self.counts), dtype="float64")
        table = np.stack([self.counts[c] for c in sorted(self.counts)])
        majority = classes[table.argmax(axis=0)]
        majority[table.sum(axis=0) == 0] = np.nan
        return majority[1:]


ACCUMULATORS = {
    "mean": MeanAccumulator,
    "min": MinAccumulator,
    "majority": MajorityAccumulator,
}


def valid_cells(zones, values, nodata):
    """(unit ids, values) of cells inside a unit holding real data."""
    valid = (zones > ZONE_NODATA) & np.isfinite(values) & (values != nodata)
    return zones[valid].astype("int64"), values[valid]


# ---------------------------------------------------------------------------
# zones: slope units -> label raster
# ---------------------------------------------------------------------------

def _finest(grids):
    """The grid with the smallest pixel area."""
    import rasterio

    def pixel_area(path):
        with rasterio.open(path) as src:
            return abs(src.res[0] * src.res[1])

    return min(grids, key=pixel_area)


def build_zones(units_path, zones_path, grids):
    """Burn row_id + 1 of every unit into a tiled int32 raster. Returns a summary."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize

    units = gpd.read_file(units_path, columns=["row_id"])
    if units.empty:
        raise ValueError(f"No slope units in {units_path}")
    row_ids = units["row_id"].to_numpy(dtype="int64")
    if row_ids.min() < 0:
        raise ValueError("row_id must be non-negative")
    unit_count = int(row_ids.max()) + 1

    grid = _finest(grids)
    with rasterio.open(grid) as src:
        transform, crs, shape = src.transform, src.crs, (src.height, src.width)
    if units.crs is not None and crs is not None and not units.crs.equals(crs):
        sys.stderr.write(
            f"[zonal_grid] Warning: slope units CRS ({units.crs.to_string()}) "
            f"differs from the grid CRS ({crs.to_string()}) of {grid}.\n"
        )

    labels = rasterize(
        zip(units.geometry, row_ids + 1),
        out_shape=shape, transform=transform, fill=ZONE_NODATA, dtype="int32",
    )
    profile = dict(
        driver="GTiff", width=shape[1], height=shape[0], count=1, dtype="int32",
        crs=crs, transform=transform, nodata=ZONE_NODATA, tiled=True,
        blockxsize=ZONE_BLOCK_SIZE, blockysize=ZONE_BLOCK_SIZE,
        compress="deflate", BIGTIFF="IF_SAFER",
    )
    with rasterio.open(zones_path, "w", **profile) as dst:
        dst.write(labels, 1)
        dst.update_tags(**{UNIT_COUNT_TAG: str(unit_count)})

    return {"status": "ok", "units": unit_count, "zones": zones_path, "grid": grid}


# ---------------------------------------------------------------------------
# stats: one value raster -> per-unit statistic
# ---------------------------------------------------------------------------

def zonal_stats_on_grid(zones_path, value_path, out_path, nodata, rule, resampling):
    """Reduce `value_path` per unit on the zone grid; writes row_id/values npz."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window

    if rule not in RULES:
        raise ValueError(f"Unknown rule '{rule}'; expected one of {RULES}")
    if resampling not in RESAMPLINGS:
        raise ValueError(f"Unknown resampling '{resampling}'; expected one of {RESAMPLINGS}")

    # GDAL's block cache defaults to 5% of RAM per process; with one process
    # per raster that alone outgrew the data. Blocks are read once, in order.
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB), rasterio.open(zones_path) as zones_src:
        unit_count = int(zones_src.tags()[UNIT_COUNT_TAG])
        accumulator = ACCUMULATORS[rule](unit_count)
        with rasterio.open(value_path) as value_src, WarpedVRT(
            value_src,
            crs=zones_src.crs, transform=zones_src.transform,
            width=zones_src.width, height=zones_src.height,
            resampling=Resampling[resampling], nodata=nodata, dtype="float64",
        ) as values_on_grid:
            for row in range(0, zones_src.height, BLOCK_ROWS):
                window = Window(0, row, zones_src.width,
                                min(BLOCK_ROWS, zones_src.height - row))
                zones = zones_src.read(1, window=window)
                values = values_on_grid.read(1, window=window)
                accumulator.add(*valid_cells(zones, values, nodata))

    values = accumulator.result()
    np.savez(out_path, row_id=np.arange(unit_count, dtype="int64"), values=values)
    name = os.path.splitext(os.path.basename(value_path))[0].replace(" ", "_")
    return {"status": "ok", "rows": int(values.size), "layer": name,
            "missing": int(np.isnan(values).sum())}


def main(argv):
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    if command == "zones" and len(args) >= 3:
        result = build_zones(args[0], args[1], args[2:])
    elif command == "stats" and len(args) == 6:
        zones, value, out, nodata, rule, resampling = args
        result = zonal_stats_on_grid(zones, value, out, float(nodata), rule, resampling)
    else:
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
