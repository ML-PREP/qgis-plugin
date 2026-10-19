"""The zonal stage's own 0-100 progress from the Go engine's log lines. No Qt here.

Milestones follow the full-size run's timings (11 min 45 s in all): the GRASS
slope-unit tiles take the first 57%, merging and polygonizing them take it to
86%, rasterizing the zones and the per-raster statistics to 97%, and the final
feature merge to 100%.
"""

from dataclasses import dataclass

TILES_DONE = 57.0
TILES_MERGED = 71.0
ZONES_START = 86.0
RASTERS_DONE = 97.0
COMPLETE = 100

# Each line resets the position to its milestone, whatever came before.
MILESTONES = (
    ("[Phase 1] Merging", TILES_DONE),
    ("[Phase 1] Sieve-Cleaning", TILES_MERGED),
    ("[Phase 2] Rasterizing", ZONES_START),
    ("[Merging]", RASTERS_DONE),
    ("[MERGE_DONE]", COMPLETE),
)


@dataclass(frozen=True)
class ZonalSteps:
    """Progress gained per finished tile and per finished raster."""

    tile: float
    raster: float


def tile_count(params):
    """Number of DEM tiles the engine runs GRASS on."""
    return params.get("tileCols", 1) * params.get("tileRows", 1)


def zonal_steps(total_tiles, total_rasters):
    return ZonalSteps(
        tile=TILES_DONE / max(1, total_tiles),
        raster=(RASTERS_DONE - ZONES_START) / max(1, total_rasters),
    )


def _is_tile_done(line):
    return line.startswith("> Tile ") and " generated in " in line


def advance(current, line, steps):
    """(new running total, value to show or None) after one engine line."""
    for marker, position in MILESTONES:
        if line.startswith(marker):
            return position, int(position)
    if _is_tile_done(line):
        total = min(TILES_DONE, current + steps.tile)
    elif line.startswith("[RASTER_DONE]"):
        total = min(RASTERS_DONE, current + steps.raster)
    else:
        return current, None
    return total, int(total)
