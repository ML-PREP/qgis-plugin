"""The depositional-zone recipe of MLPREP Plugin v4.4: files, argv, configs. No Qt.

The deposition worker runs these steps in order; everything it needs to know
about names, flags and tuning lives here so it can be tested without QGIS.
"""

import os
from dataclasses import dataclass

# Stream definition on the D-infinity specific catchment area, calibrated so
# stream density matches the ~1.8% Strahler network the ML-DFI model was
# trained against.
STREAM_SCA_THRESHOLD = 1500

# A FoS-refined source keeps cells whose Factor of Safety is below this.
FOS_THRESHOLD = 1.5
FOS_BLOCK_ROWS = 256
MASK_NODATA = 255

# Step 5 cleanup of the raw runout.
MIN_RUNOUT_SOURCE_AREA_M2 = 200.0
MIN_DINF_FLOW_PROPORTION = 0.01
EDGE_SEED_BUFFER_M = 5.0

NODATA = -9999.0
WEISS_LANDFORM_CLASSES = "6"

FOS_DIR = "fos_source_refinement"
STEP5_DIR = "step5_cleanup_output"


@dataclass(frozen=True)
class DepzonePaths:
    """Every file the depositional-zone stage writes, under one folder."""

    out_dir: str

    def _at(self, *parts):
        return os.path.join(self.out_dir, *parts)

    # TauDEM hydrology
    fel = property(lambda s: s._at("fel.tif"))
    ang = property(lambda s: s._at("ang.tif"))
    slp = property(lambda s: s._at("slp.tif"))
    slp_degrees = property(lambda s: s._at("slp_degrees.tif"))
    sca = property(lambda s: s._at("sca.tif"))
    src = property(lambda s: s._at("src.tif"))
    src_routing = property(lambda s: s._at("src_routing.tif"))
    dstgsurf = property(lambda s: s._at("dstgsurf.tif"))
    dugsurf = property(lambda s: s._at("dugsurf.tif"))

    # ML-DFI predictors and inference
    aligned_ndvi = property(lambda s: s._at("aligned_ndvi.tif"))
    profile_curvature = property(lambda s: s._at("profile_curvature.tif"))
    plan_curvature = property(lambda s: s._at("plan_curvature.tif"))
    slope_deg = property(lambda s: s._at("slope_deg.tif"))
    geomorphology = property(lambda s: s._at("Geomorphology.tif"))
    twi = property(lambda s: s._at("twi.tif"))
    predictor_config = property(lambda s: s._at("predictor_config.json"))
    raw_dfi = property(lambda s: s._at("ml_dfi_probability.tif"))
    dfi_clean = property(lambda s: s._at("dfi_clean.tif"))
    alpha = property(lambda s: s._at("BaselineAlpha.tif"))

    # FoS source refinement
    fos_dir = property(lambda s: s._at(FOS_DIR))
    bulk_masked = property(lambda s: s._at(FOS_DIR, "bulk_density_masked.tif"))
    fos_config = property(lambda s: s._at(FOS_DIR, "fos_config.json"))
    cell_fos = property(lambda s: s._at(FOS_DIR, "cell_scale_factor_of_safety.tif"))
    refined_mask = property(lambda s: s._at(FOS_DIR, "refined_source_mask.tif"))
    fos_summary = property(lambda s: s._at(FOS_DIR, "source_refinement_summary.json"))

    # Routing engine
    alpha_final = property(lambda s: s._at("alpha_final.tif"))
    dfi_final = property(lambda s: s._at("dfi_final.tif"))
    source_final = property(lambda s: s._at("source_final.tif"))
    initial_depzone = property(lambda s: s._at("Initial_DepZone.tif"))
    deposition = property(lambda s: s._at("step4_pure_depositional_mask.tif"))
    source_sca = property(lambda s: s._at("source_contributing_area.tif"))

    # Step 5 cleanup
    step5_dir = property(lambda s: s._at(STEP5_DIR))
    step5_config = property(lambda s: s._at(STEP5_DIR, "step5_config.json"))
    step5_combined = property(
        lambda s: s._at(STEP5_DIR, "post_depositional_combined_mask.tif")
    )


@dataclass(frozen=True)
class FosInputs:
    """Objective 2 (PINN) rasters and UI inputs for the FoS source refinement."""

    susceptibility: str
    factor_of_safety: str
    cohesion: str
    friction_angle: str
    wetness: str
    friction_angle_unit: str
    bulk_density: str
    slope: str


REQUIRED_FOS_INPUTS = (
    ("susceptibility", "PINN susceptible-unit raster"),
    ("cohesion", "PINN cohesion raster"),
    ("friction_angle", "PINN internal friction angle raster"),
    ("wetness", "PINN wetness (saturation ratio) raster"),
    ("bulk_density", "Bulk Density raster (EIL input)"),
    ("slope", "Slope raster (EIL input)"),
)


def missing_fos_inputs(inputs, exists=os.path.exists):
    """Labels of the required FoS inputs that are absent."""
    return [
        label
        for key, label in REQUIRED_FOS_INPUTS
        if not getattr(inputs, key) or not exists(getattr(inputs, key))
    ]


def taudem(mpi_exe, cores, tool_exe, *args):
    return [mpi_exe, "-n", str(cores), tool_exe, *args]


def prep(python_exe, script, command, *positional, **flags):
    """argv for one depzone/prep.py subcommand; foo_bar=x becomes --foo-bar x."""
    argv = [python_exe, script, command]
    for name, value in flags.items():
        argv += [f"--{name.replace('_', '-')}", value]
    return argv + list(positional)


def weiss(python_exe, script, dem, out_dir):
    """Weiss slope position (6 classes) and Horn slope: slope_deg + Geomorphology."""
    return [
        python_exe,
        script,
        dem,
        "--out-dir",
        out_dir,
        "--landform-classification",
        WEISS_LANDFORM_CLASSES,
    ]


def apply_model(python_exe, script, model, metadata, paths):
    return [
        python_exe,
        script,
        "--model-path",
        model,
        "--metadata-path",
        metadata,
        "--predictor-config-json",
        paths.predictor_config,
        "--reference-raster-path",
        paths.slope_deg,
        "--output-dir",
        paths.out_dir,
        "--output-probability-raster",
        os.path.basename(paths.raw_dfi),
        "--disable-full-domain-shap",
    ]


def step0(python_exe, script, paths):
    return [
        python_exe,
        script,
        "--slope",
        paths.slp_degrees,
        "--output-alpha",
        paths.alpha,
        "--nodata",
        str(NODATA),
        "--overwrite",
    ]


def with_config(python_exe, script, config_path):
    """argv for a handoff step driven by a JSON config."""
    return [python_exe, script, "--config", config_path, "--overwrite"]


def lowpass_sources(paths):
    """{predictor: unfiltered raster} for the continuous ML-DFI predictors."""
    return {
        "twi": paths.twi,
        "slope": paths.slope_deg,
        "profile_curvature": paths.profile_curvature,
        "plan_curvature": paths.plan_curvature,
        "ndvi": paths.aligned_ndvi,
        "dugsurf": paths.dugsurf,
        "dstgsurf": paths.dstgsurf,
    }


def lowpass_path(paths, raster):
    """Where depzone/prep.py lowpass writes the filtered copy of `raster`."""
    stem = os.path.splitext(os.path.basename(raster))[0]
    return os.path.join(paths.out_dir, f"{stem}_lpf.tif")


def predictor_config(paths):
    rasters = {
        name: lowpass_path(paths, raster)
        for name, raster in lowpass_sources(paths).items()
    }
    rasters["geomorphology"] = paths.geomorphology
    return {"predictor_rasters": rasters}


def fos_config(paths, inputs, bulk_density, unit_weight_scale):
    return {
        "susceptibility_raster": inputs.susceptibility,
        "cohesion_raster": inputs.cohesion,
        "friction_angle_raster": inputs.friction_angle,
        "saturation_ratio_raster": inputs.wetness,
        "total_unit_weight_raster": bulk_density,
        "slope_angle_raster": inputs.slope,
        "friction_angle_unit": inputs.friction_angle_unit or "degrees",
        "total_unit_weight_band": 1,
        "total_unit_weight_scale_to_kn_m3": unit_weight_scale,
        # Nearest, not bilinear: bulk density is far coarser than the slope
        # grid, and interpolating across its units invents unit weights.
        "total_unit_weight_resampling": "nearest",
        "fos_threshold": FOS_THRESHOLD,
        "output_fos_raster": paths.cell_fos,
        "output_source_mask_raster": paths.refined_mask,
        "output_summary_json": paths.fos_summary,
        # The PINN's own FoS when available; otherwise the step derives one.
        "factor_of_safety_raster": inputs.factor_of_safety or None,
        "float_nodata": NODATA,
        "mask_nodata": MASK_NODATA,
        "block_rows": FOS_BLOCK_ROWS,
        "overwrite": True,
    }


def routing_inputs(paths):
    """Rasters the routing engine reads, keyed as build_routing_command expects."""
    return {
        "fel": paths.fel,
        "ang": paths.ang,
        "source": paths.source_final,
        "dfi": paths.dfi_final,
        "alpha": paths.alpha_final,
    }


def step5_config(paths):
    return {
        "runout_cleanup_only": {
            "runout_mask_raster": paths.initial_depzone,
            "source_mask_raster": paths.source_final,
            "source_contributing_area_raster": paths.source_sca,
            "dinf_flow_raster": paths.ang,
            "output_dir": paths.step5_dir,
            "min_runout_source_area_m2": MIN_RUNOUT_SOURCE_AREA_M2,
            "min_dinf_flow_proportion": MIN_DINF_FLOW_PROPORTION,
            "edge_seed_buffer_m": EDGE_SEED_BUFFER_M,
            "overwrite": True,
        }
    }


# Results copied to the run's output folder, as v4.4 does: the engine's pure
# depositional mask, the raw runout, and the Step 5-cleaned final zone.
FINAL_RASTER = "PostDep_DepZone.tif"


def published_rasters(paths):
    """{name in the output folder: source raster}."""
    return {
        "ML_DFI_Depositional_Zone.tif": paths.deposition,
        "Initial_DepZone.tif": paths.initial_depzone,
        FINAL_RASTER: paths.step5_combined,
    }


def fos_inputs_from(pinn_result, eil_rasters):
    """FoS refinement inputs: the PINN's rasters plus the run's own inputs.

    `eil_rasters` is the run's {model input name: path}; Bulk Density and
    Slope are the same rasters the slope units were built from.
    """
    return FosInputs(
        susceptibility=pinn_result.get("susceptibility_tif") or "",
        factor_of_safety=pinn_result.get("factor_of_safety_tif") or "",
        cohesion=pinn_result.get("cohesion_tif") or "",
        friction_angle=pinn_result.get("friction_angle_tif") or "",
        wetness=pinn_result.get("wetness_tif") or "",
        friction_angle_unit=pinn_result.get("friction_angle_unit") or "degrees",
        bulk_density=eil_rasters.get("BUK", ""),
        slope=eil_rasters.get("Slope", ""),
    )


def skip_reason(ndvi_path, fos_inputs, exists=os.path.exists):
    """Why the depositional zone cannot run with these inputs, or None."""
    if not ndvi_path or not exists(ndvi_path):
        return "an NDVI raster (select one in the EIL inputs) for the ML-DFI model"
    missing = missing_fos_inputs(fos_inputs, exists)
    if missing:
        return "the FoS source refinement's " + ", ".join(missing)
    return None
