"""Locations of files bundled inside the installed plugin.

Every module resolves bundled files from here rather than from its own
`__file__`, so moving a module between packages never changes the path it
computes.
"""

import os

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROI_DIR = os.path.join(PLUGIN_DIR, "roi_data")
DEM_ICON = os.path.join(PLUGIN_DIR, "DEM_icon.svg")

# Landslide detection
LANDSLIDE_WORKER_SCRIPT = os.path.join(PLUGIN_DIR, "landslide_worker.py")
LANDSLIDE_MODEL = os.path.join(PLUGIN_DIR, "model_lsi", "default", "best.keras")
LANDSLIDE_NORM_STATS = os.path.join(
    PLUGIN_DIR, "model_lsi", "default", "norm_stats.json"
)

# EIL pipeline
GO_PROJECT_DIR = os.path.join(PLUGIN_DIR, "ml-prep-scripts")
PINN_SCRIPT = os.path.join(PLUGIN_DIR, "pinn_inference.py")
EIL_MODEL = os.path.join(PLUGIN_DIR, "model", "my_model.keras")
STEP4_WRAPPER = os.path.join(
    PLUGIN_DIR,
    "ml_dfi_handoff",
    "steps",
    "step4_deposition_zone",
    "step4_deposition_zone_mpi_wrapper.py",
)
INFINITE_PRINTING_SCRIPT = os.path.join(PLUGIN_DIR, "scripts", "infinite_printing.py")
