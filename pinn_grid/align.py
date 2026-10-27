"""Resample the EIL input rasters onto a regular grid snapped to the DEM.

Port of mlprep/scripts/align_rasters_to_dem.py, which built the grid the
grid PINN was trained on, minus its GeoPackage step: the cells are read back
from the gridded rasters directly. The grid shares the DEM's CRS and origin,
with square cells of `cell_size` metres, and the resampling plays the role of
the zonal statistic:

    finer than the cell (DEM, slope ...)   average of the pixels in the cell
    coarser than the cell (SoilGrids, PGA) bilinear
    categorical (soil type)                mode (majority), never interpolated

Only band 1 of each raster is used, as the slope-unit route does.
"""

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

from .inputs import SOIL_TYPE_INPUT

FLOAT_NODATA = -9999.0
WARP_MEM_LIMIT_MB = 512
CATEGORICAL_INPUTS = frozenset({SOIL_TYPE_INPUT})
METRE_UNITS = frozenset({"metre", "meter", "m"})


class GridError(ValueError):
    """The DEM cannot define a grid; the message is shown to the user."""


@dataclass(frozen=True)
class Grid:
    crs: object
    transform: Affine
    width: int
    height: int

    def window_transform(self, window):
        return window_transform(window, self.transform)


def read_grid(dem_path, cell_size):
    """The DEM's CRS and origin with square `cell_size` m cells over its extent."""
    with rasterio.open(dem_path) as dem:
        if dem.crs is None:
            raise GridError(f"The DEM {dem_path} has no CRS.")
        if not dem.crs.is_projected or dem.crs.linear_units not in METRE_UNITS:
            raise GridError(
                f"The grid model needs a DEM in a projected CRS in metres; "
                f"{dem_path} is in {dem.crs.to_string()}."
            )
        width = math.ceil(dem.width * abs(dem.transform.a) / cell_size)
        height = math.ceil(dem.height * abs(dem.transform.e) / cell_size)
        origin = dem.transform
        return Grid(
            crs=dem.crs,
            transform=Affine(cell_size, 0.0, origin.c, 0.0, -cell_size, origin.f),
            width=width,
            height=height,
        )


def resampling_for(name, path, cell_size):
    """Mode for classes, else average (finer than the cell) or bilinear."""
    if name in CATEGORICAL_INPUTS:
        return Resampling.mode
    with rasterio.open(path) as src:
        source_res = max(abs(src.res[0]), abs(src.res[1]))
    return Resampling.average if source_res < cell_size else Resampling.bilinear


def resolve_nodata(src):
    """Source nodata if set; otherwise a sentinel that fits the dtype."""
    if src.nodata is not None:
        return src.nodata
    if np.issubdtype(np.dtype(src.dtypes[0]), np.floating):
        return FLOAT_NODATA
    return np.iinfo(np.dtype(src.dtypes[0])).max


def strip_windows(width, height, chunk_rows):
    """Full-width row strips covering a width x height grid."""
    for row_off in range(0, height, chunk_rows):
        yield Window(0, row_off, width, min(chunk_rows, height - row_off))


def grid_raster(src_path, grid, out_path, resampling, threads, chunk_rows):
    """Warp band 1 of one raster onto the grid strip by strip."""
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise GridError(f"{src_path} has no CRS; cannot grid it.")
        nodata = resolve_nodata(src)
        profile = {
            "driver": "GTiff", "crs": grid.crs, "transform": grid.transform,
            "width": grid.width, "height": grid.height,
            "count": 1, "dtype": src.dtypes[0], "nodata": nodata,
            "compress": "lzw", "tiled": True, "BIGTIFF": "IF_SAFER",
        }
        vrt_options = {
            "crs": grid.crs, "transform": grid.transform,
            "width": grid.width, "height": grid.height,
            "resampling": resampling, "src_nodata": nodata, "nodata": nodata,
            "warp_mem_limit": WARP_MEM_LIMIT_MB, "warp_extras": {"NUM_THREADS": threads},
        }
        with WarpedVRT(src, **vrt_options) as vrt, rasterio.open(out_path, "w", **profile) as dst:
            for window in strip_windows(grid.width, grid.height, chunk_rows):
                dst.write(vrt.read(1, window=window), 1, window=window)


def grid_inputs(rasters, grid, out_dir, workers, chunk_rows, on_done=lambda _name: None):
    """{input name: gridded raster path}; `on_done(name)` after each raster.

    Rasters are warped concurrently on threads (GDAL releases the GIL), so a
    cancelled run leaves no orphaned worker processes behind.
    """
    threads = max(1, workers // max(1, len(rasters)))
    jobs = {
        name: (path, os.path.join(out_dir, f"{name}.tif"), resampling_for(name, path, grid.transform.a))
        for name, path in rasters.items()
    }
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs)))) as pool:
        futures = {
            pool.submit(grid_raster, src, grid, out, resampling, threads, chunk_rows): name
            for name, (src, out, resampling) in jobs.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as exc:
                raise GridError(f"Could not grid the {name} raster: {exc}") from exc
            on_done(name)
    return {name: out for name, (_src, out, _rs) in jobs.items()}
