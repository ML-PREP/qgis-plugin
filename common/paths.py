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
# Grid models (no slope units); see pinn_grid/.
GRID_SCRIPT = os.path.join(PLUGIN_DIR, "grid_inference.py")
# Which PINN models the EIL tab offers; see eil/pinn_models.py.
PINN_MODEL_CONFIG = os.path.join(PLUGIN_DIR, "model", "pinn_models.json")
STEP4_WRAPPER = os.path.join(
    PLUGIN_DIR,
    "ml_dfi_handoff",
    "steps",
    "step4_deposition_zone",
    "step4_deposition_zone_mpi_wrapper.py",
)

# Depositional zone (the MLPREP v4.4 flow)
_HANDOFF_STEPS = os.path.join(PLUGIN_DIR, "ml_dfi_handoff", "steps")
DEPZONE_PREP_SCRIPT = os.path.join(PLUGIN_DIR, "depzone", "prep.py")
WEISS_SCRIPT = os.path.join(PLUGIN_DIR, "depzone", "weiss_tpi_landform.py")
DFI_MODEL = os.path.join(PLUGIN_DIR, "model_dfi", "trained_ml_dfi_model.pkl")
DFI_MODEL_METADATA = os.path.join(PLUGIN_DIR, "model_dfi", "model_metadata.json")
APPLY_DFI_SCRIPT = os.path.join(
    _HANDOFF_STEPS, "step3_train_ml_dfi_model", "apply_ml_dfi_model_to_rasters.py"
)
STEP0_SCRIPT = os.path.join(
    _HANDOFF_STEPS, "step0_baseline_alpha_angle", "step0_baseline_alpha_angle.py"
)
FOS_REFINEMENT_SCRIPT = os.path.join(
    _HANDOFF_STEPS, "fos_source_refinement", "fos_source_refinement.py"
)
STEP5_CLEANUP_SCRIPT = os.path.join(
    _HANDOFF_STEPS, "step5_cleanup_only", "step5_cleanup_only.py"
)
DEPZONE_SCRIPTS = (
    DEPZONE_PREP_SCRIPT, WEISS_SCRIPT, DFI_MODEL, DFI_MODEL_METADATA,
    APPLY_DFI_SCRIPT, STEP0_SCRIPT, FOS_REFINEMENT_SCRIPT, STEP5_CLEANUP_SCRIPT,
)

INFINITE_PRINTING_SCRIPT = os.path.join(PLUGIN_DIR, "scripts", "infinite_printing.py")
