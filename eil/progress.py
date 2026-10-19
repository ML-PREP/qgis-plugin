"""Whole-run position of the EIL tab's progress bar. No Qt here.

Each stage reports its own 0-100 progress and owns a fixed slice of the bar,
sized from the full-size run on carsu_vm (Cotabato, 21,086 x 20,119 DEM,
11 rasters, 64 cores; 2026-10-13):

    slope units + zonal stats   11 min 45 s   ->   0-38
    seam cleanup                 7 min 30 s   ->  38-62
    PINN predictions                    45 s  ->  62-65
    depositional routing        10 min 50 s   ->  65-100

Routing is optional: a run that skips it jumps from 65 to 100 when it ends.
"""

from types import MappingProxyType

ZONAL = "zonal"
SEAMS = "seams"
PINN = "pinn"
DEPOSITION = "deposition"

STAGE_SLICES = MappingProxyType(
    {
        ZONAL: (0, 38),
        SEAMS: (38, 62),
        PINN: (62, 65),
        DEPOSITION: (65, 100),
    }
)

COMPLETE = 100


def overall(stage, percent):
    """Bar position for `stage` at `percent` (0-100) of its own work."""
    start, end = STAGE_SLICES[stage]
    fraction = min(max(percent, 0), 100) / 100
    return int(start + (end - start) * fraction)


def advance(shown, stage, percent):
    """Bar position after `stage` reports `percent`; never moves backwards."""
    return max(shown, overall(stage, percent))
