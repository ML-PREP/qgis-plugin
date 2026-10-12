"""Progress-bar position from the Go zonal engine's log lines. No Qt here.

Phase 1 (slope units) fills the bar to 50%, phase 2 (zonal statistics) shares
45% across the input rasters, and the final merge takes it to 100%.
"""

PHASE1_MERGED = 40.0
PHASE1_POLYGONIZED = 50.0
ZONAL_SHARE = 45.0
ZONAL_CEILING = 95
COMPLETE = 100


def zonal_step(total_rasters):
    """Progress gained per raster finished in phase 2."""
    return ZONAL_SHARE / max(1, total_rasters)


def advance(current, line, step):
    """(new running total, value to show or None) after one engine line."""
    if "[Phase 1]" in line and "Merging Tiles" in line:
        return PHASE1_MERGED, int(PHASE1_MERGED)
    if "[Phase 1]" in line and "Polygonizing" in line:
        return PHASE1_POLYGONIZED, int(PHASE1_POLYGONIZED)
    if "[RASTER_DONE]" in line:
        total = current + step
        return total, int(min(ZONAL_CEILING, total))
    if "[MERGE_DONE]" in line:
        return current, COMPLETE
    return current, None
