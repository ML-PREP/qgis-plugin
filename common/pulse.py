"""Timing and geometry of the progress bar's shimmer. No Qt here.

A soft highlight sweeps left to right once per period, then rests, so a bar
that has not moved for minutes still shows the pipeline is alive.
"""

import math

SWEEP_MS = 1400
REST_MS = 1000
PERIOD_MS = SWEEP_MS + REST_MS

# The highlight band's width as a share of the span it crosses, never narrower
# than MIN_BAND_PX.
BAND_SHARE = 0.3
MIN_BAND_PX = 40


def sweep_fraction(elapsed_ms):
    """Band position across the span (0-1) at `elapsed_ms`, or None while resting."""
    phase = elapsed_ms % PERIOD_MS
    if phase > SWEEP_MS:
        return None
    # Ease in and out, so the band glides rather than jerks at the edges.
    return (1 - math.cos(math.pi * phase / SWEEP_MS)) / 2


def band(fraction, span):
    """(left, width) of the band; it enters and leaves fully outside the span."""
    width = max(MIN_BAND_PX, span * BAND_SHARE)
    center = -width / 2 + fraction * (span + width)
    return center - width / 2, width
