# pinn_inference.py
import sys
import os
import json
import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
os.environ["TF_DETERMINISTIC_OPS"] = "1"
import tensorflow as tf

import rasterio
from rasterio.features import rasterize as rio_rasterize

# Ensure the current directory is in sys.path to access py_files
current_dir = os.path.dirname(os.path.abspath(__file__))

# Model inputs that carry the soil label as a string.
CATEGORICAL_INPUTS = ("type", "LITHO")
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from py_files.helpers import add_soil_texture_index, add_soil_type_index_v3
    from py_files.data import (
        preprocessing_v2,
        apply_log_transform,
        apply_clip_thresholds,
        dataframe_to_dataset,
    )
    from py_files.GallenModel import (
        CriticalAcceleration,
        DisplacementIntermediate,
        FosLayer,
    )
    from py_files.GallenModel_v1 import (
        NewmarkActivation,
        DisplacementLayerRainFall,
        WetnessLayer,
        ClipLayer,
        CohesionLayer,
        InternalFrictionLayer,
        LogitLayer,
    )
    from py_files.GallenModel_v3 import HydraulicConductivityLayerV3
    from py_files.losses import DiceCrossEntropyLoss

    # from py_files.Landslidev2_Old import DiceCrossEntropyLoss
except ImportError as e:
    print(json.dumps({"status": "error", "message": f"Import Error: {e}"}))
    sys.exit(1)


# ---------------------------------------------------------
# LogitLayer definition to fix the deserialization error
# ---------------------------------------------------------
@tf.keras.utils.register_keras_serializable(package="Custom")
class LogitLayer(tf.keras.layers.Layer):
    """Maps probabilities [0, 1] to logits [-inf, inf] using log(p / (1 - p))."""

    def __init__(self, eps=1e-6, **kwargs):
        super(LogitLayer, self).__init__(**kwargs)
        self.eps = eps

    def call(self, inputs):
        # Clip to avoid log(0) or division by zero
        p = tf.clip_by_value(inputs, self.eps, 1.0 - self.eps)
        return tf.math.log(p / (1.0 - p))

    def get_config(self):
        config = super().get_config()
        config.update({"eps": self.eps})
        return config

    @classmethod
    def from_config(cls, config):
        return cls(**config)


# Columns to drop during preprocessing if they exist
COLUMNS_DROP = [
    "Landslide1",
    "descriptio",
    "sus_pinn_ground truth",
    "ds",
    "cohesion",
    "internal_friction",
    "sus_pinn_landslide",
    "confusion",
    "landslide_preds",
    "landslide_probability",
    "Lithology",
    "LITHO",
    "Geomorphology",
    "LITHODESC",
    "LITHO_2",
    "LITHODESC_2",
    "value",
]


# ---------------------------------------------------------
# Depositional-zone hand-off (ported from MLPREP Plugin v4.4)
# ---------------------------------------------------------
# Slope units with Hazard_Susceptibility >= this value are "susceptible" for
# the FoS source refinement; all others are NoData (= non-susceptible) in
# Susceptibility.tif. CONFIRM THIS VALUE with the Objective 2 classification.
SUSCEPTIBILITY_THRESHOLD = 0.5
RASTER_NODATA = -9999.0

# Susceptibility_DFI keeps the PINN susceptibility only where the DFI-specific
# Factor of Safety is below this; stable slopes are zeroed.
FOS_DFI_STABLE = 1.25
FOS_DFI_BOUNDS = (0.01, 10.0)
WATER_UNIT_WEIGHT = 9.81
MIN_PARAMETER = 0.001


def _physics_layers(model):
    """{output name: layer name} of the PINN's physics layers, wetness if present."""
    layers = {
        "fos": "fos_layer",
        "displacement": "displacement_layer",
        "cohesion": "cohesion_layer",
        "internal_friction": "internal_friction",
    }
    for layer in model.layers:
        if "wetness" in layer.name.lower():
            layers["wetness"] = layer.name
            break
    return layers


def _column(values, count):
    return np.asarray(values).reshape(count, -1)[:, 0]


def _fos_dfi(cohesion, friction_deg, wetness, soil_thickness, unit_weight, slope_deg):
    """Infinite-slope FoS per slope unit from the PINN's own parameters."""
    c_prime = np.maximum(cohesion, MIN_PARAMETER)
    phi = np.radians(np.maximum(friction_deg, MIN_PARAMETER))
    t = np.maximum(soil_thickness, MIN_PARAMETER)
    gamma_t = np.maximum(unit_weight, MIN_PARAMETER)
    theta = np.radians(np.maximum(slope_deg, MIN_PARAMETER))

    cohesive = (c_prime / t) * (1.0 / (gamma_t * np.sin(theta)))
    frictional = np.tan(phi) / np.tan(theta)
    pore = (wetness * WATER_UNIT_WEIGHT * np.tan(phi)) / (gamma_t * np.tan(theta))
    return np.clip(cohesive + frictional - pore, *FOS_DFI_BOUNDS)


def _dem_grid(dem_path):
    """(rows, cols, transform, valid-terrain mask) of the DEM."""
    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        valid = np.isfinite(dem)
        if src.nodata is not None:
            valid &= ~np.isclose(dem, src.nodata)
        return src.height, src.width, src.transform, valid


def _write_raster(path, data, grid, crs_wkt):
    rows, cols, transform, _ = grid
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype="float32",
        crs=crs_wkt,
        transform=transform,
        nodata=RASTER_NODATA,
        compress="lzw",
        tiled=True,
        BIGTIFF="IF_SAFER",
    ) as dst:
        dst.write(data, 1)
    return path


def _burn(gdf, values, grid, fill):
    """Per-slope-unit values on the DEM grid; NaN units and off-terrain stay NoData."""
    rows, cols, transform, valid = grid
    shapes = [
        (geom, float(v))
        for geom, v in zip(gdf.geometry, values)
        if geom is not None and not geom.is_empty and np.isfinite(v)
    ]
    burned = (
        rio_rasterize(
            shapes, out_shape=(rows, cols), transform=transform, fill=fill,
            dtype="float32",
        )
        if shapes
        else np.full((rows, cols), fill, dtype=np.float32)
    )
    burned[~valid] = RASTER_NODATA
    return burned


def _write_objective2_rasters(out_gdf, folder, grid, crs_wkt):
    """Rasters fos_source_refinement.py reads; returns {result key: path}."""
    os.makedirs(folder, exist_ok=True)
    hazard = np.asarray(out_gdf["Hazard_Susceptibility"], dtype=np.float64)
    susceptible = np.where(hazard >= SUSCEPTIBILITY_THRESHOLD, 1.0, np.nan)
    print(
        f"[INFO] Susceptible slope units (Hazard_Susceptibility >= "
        f"{SUSCEPTIBILITY_THRESHOLD}): {int(np.isfinite(susceptible).sum())} "
        f"of {len(out_gdf)}",
        flush=True,
    )
    layers = {
        "susceptibility_tif": ("Susceptibility.tif", susceptible),
        "cohesion_tif": ("Cohesion.tif", out_gdf["Cohesion"]),
        "friction_angle_tif": (
            "Internal_Friction_Angle.tif", out_gdf["Internal_Friction"]
        ),
        "wetness_tif": ("Saturation_Ratio.tif", out_gdf["Wetness"]),
        # The PINN's own fos_layer: Step 4 refines its sources with it rather
        # than re-deriving a cell-scale FoS that disagrees by about six times.
        "factor_of_safety_tif": ("FactorOfSafety.tif", out_gdf["FactorOfSafety"]),
    }
    paths = {}
    for key, (name, values) in layers.items():
        values = np.asarray(values, dtype=np.float64)
        burned = _burn(out_gdf, values, grid, RASTER_NODATA)
        paths[key] = _write_raster(os.path.join(folder, name), burned, grid, crs_wkt)
        del burned
    return paths


def _friction_unit(values):
    """A physical friction angle is under 90 degrees, i.e. under pi/2 radians."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    largest = float(finite.max()) if finite.size else 0.0
    unit = "radians" if largest <= np.pi / 2.0 else "degrees"
    print(
        f"[INFO] Internal friction max {largest:.4f} -> treated as {unit}",
        flush=True,
    )
    return unit


def run_prediction():
    try:
        # 1. Parse Arguments from the QGIS Wrapper
        raw_gpkg_path = sys.argv[1]
        clean_gpkg_path = raw_gpkg_path.split("|")[0]
        model_path = sys.argv[2]
        output_gpkg = sys.argv[3]
        dem_template_path = sys.argv[4]

        # Locate the manifest json
        manifest_path = os.path.join(
            current_dir, "feature_manifests", "v1_cotabato_transforms_production.json"
        )
        if not os.path.exists(manifest_path):
            manifest_path = os.path.join(
                os.path.dirname(model_path), "v1_cotabato_transforms_production.json"
            )

        base_dir = os.path.dirname(clean_gpkg_path)

        # Determine the Zonal stats GPKG path (from Go pipeline)
        if base_dir.endswith("Zonal_Results"):
            zonal_path = os.path.join(base_dir, "Merged_PINN_Features.gpkg")
        else:
            zonal_path = os.path.join(
                base_dir, "Zonal_Results", "Merged_PINN_Features.gpkg"
            )
        if not os.path.exists(zonal_path):
            zonal_path = os.path.join(base_dir, "Merged_PINN_Features.gpkg")

        # 2. Setup Custom Objects and Load Model
        custom_objects = {
            "NewmarkActivation": NewmarkActivation,
            "DisplacementLayerRainFall": DisplacementLayerRainFall,
            "WetnessLayer": WetnessLayer,
            "ClipLayer": ClipLayer,
            "CohesionLayer": CohesionLayer,
            "InternalFrictionLayer": InternalFrictionLayer,
            "CriticalAcceleration": CriticalAcceleration,
            "DisplacementIntermediate": DisplacementIntermediate,
            "FosLayer": FosLayer,
            "HydraulicConductivityLayerV3": HydraulicConductivityLayerV3,
            "DiceCrossEntropyLoss": DiceCrossEntropyLoss,
            "Custom>LogitLayer": LogitLayer,
        }

        for name, cls in custom_objects.items():
            tf.keras.utils.get_custom_objects()[name] = cls
            tf.keras.utils.get_custom_objects()[f"Custom>{name}"] = cls

        model = tf.keras.models.load_model(
            model_path, compile=False, custom_objects=custom_objects
        )
        input_cols = [inp.name.split(":")[0] for inp in model.inputs]

        # 3. Load GPKG
        gdf_raw = gpd.read_file(zonal_path)

        # Safely rename Go Engine outputs to Keras expected inputs
        rename_dict = {}
        for col in gdf_raw.columns:
            cl = col.lower()
            if "slope" in cl:
                rename_dict[col] = "Slope_mean"
            elif "clay" in cl:
                rename_dict[col] = "Clay_mean"
            elif "sand" in cl:
                rename_dict[col] = "Sand_mean"
            elif "silt" in cl:
                rename_dict[col] = "Silt_mean"
            elif "elev" in cl:
                rename_dict[col] = "Elev_mean"
            elif "soilth" in cl:
                rename_dict[col] = "SoilThc_mean"
            elif "prc" in cl:
                rename_dict[col] = "Prc_mean"
            elif "pga" in cl:
                rename_dict[col] = "PGA2_max"
            elif "contributing" in cl:
                rename_dict[col] = "ContributingFactor_mean"
            elif "buk" in cl or "bulk" in cl:
                rename_dict[col] = "BUK_mean"
            elif "type" in cl:
                rename_dict[col] = "type"

        gdf_raw.rename(columns=rename_dict, inplace=True)

        # ---------------------------------------------------------
        # FIX 1: Bulk Density Unit Conversion
        # If values are > 50, they are likely in cg/cm^3.
        # Multiply by 0.0981 to convert to kN/m^3 for the physics engine.
        # ---------------------------------------------------------
        if "BUK_mean" in gdf_raw.columns:
            gdf_raw["BUK_mean"] = pd.to_numeric(gdf_raw["BUK_mean"], errors="coerce")
            gdf_raw.loc[gdf_raw["BUK_mean"] > 50, "BUK_mean"] = (
                gdf_raw["BUK_mean"] * 0.0981
            )

        # ---------------------------------------------------------
        # FIX 2: Soil Type / Lithology Catcher
        # If the raster outputs numerical codes (like 45.0) instead of strings,
        # override it with "Unknown" so the network relies on the Sand/Silt/Clay percentages instead.
        # ---------------------------------------------------------
        if "type" in gdf_raw.columns:
            gdf_raw["type"] = gdf_raw["type"].astype(str)
            # If the string is just a number (e.g., "45.0"), mark it unknown
            gdf_raw.loc[
                gdf_raw["type"].str.replace(".", "", 1).str.isnumeric(), "type"
            ] = "Unknown"

        # Absolute safety net for the preprocessor
        if "Slope_mean" not in gdf_raw.columns:
            gdf_raw["Slope_mean"] = 15.0
        if "type" not in gdf_raw.columns:
            gdf_raw["type"] = "Unknown"

        # Safely run preprocessing_v2
        valid_drop = [c for c in COLUMNS_DROP if c in gdf_raw.columns]
        df, columns, numeric_cols, _, _ = preprocessing_v2(
            gdf_raw, columns_drop=valid_drop, track_imputation=True
        )

        # Preserve Geometry for the final GPKG output
        geometry = df.geometry.copy()
        crs = df.crs

        # Add indexing and metadata
        try:
            df = add_soil_texture_index(df[columns].copy())
        except Exception as e:
            print(f"[WARN] add_soil_texture_index failed: {e}", flush=True)

        # Input 10 of the rainfall-aware model: the soil-map class of `type`
        # (13 classes; anything unrecognized is Undifferentiated), derived as
        # in training -- mlprep/notebooks/cotabato_new_slope_unit_v2-8, cell 11.
        if "soil_type_idx" in input_cols:
            df = add_soil_type_index_v3(df)

        # 4. Replay Production Manifest Transforms (Safe Fallback)
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                transform_meta = json.load(f)

            log_cols = transform_meta.get("log_transformed_cols", [])
            clip_thresh = transform_meta.get("clip_thresholds", {})

            if log_cols:
                df = apply_log_transform(df, log_cols)
            if clip_thresh:
                df = apply_clip_thresholds(df, clip_thresh)
        else:
            print(
                f"[WARN] Manifest not found at {manifest_path}. Skipping data scaling.",
                flush=True,
            )

        # ---------------------------------------------------------
        # NEW: Explicit Type-Casting to Prevent TensorFlow Crashes
        # ---------------------------------------------------------
        for exp_col in input_cols:
            # Matched exactly: "soil_type_idx" contains "type" but is a
            # numeric class index, not the categorical soil label.
            is_label = exp_col in CATEGORICAL_INPUTS
            is_index = exp_col.endswith("_idx")
            if exp_col not in df.columns:
                print(f"[WARN] Imputing missing required input: {exp_col}", flush=True)
                if is_label:
                    df[exp_col] = "Unknown"
                elif is_index:
                    df[exp_col] = 4
                else:
                    df[exp_col] = 0.001

            # Force Data Types
            if is_label:
                df[exp_col] = df[exp_col].astype(str)
            elif is_index:
                df[exp_col] = (
                    pd.to_numeric(df[exp_col], errors="coerce")
                    .fillna(4)
                    .astype("int32")
                )
            else:
                df[exp_col] = (
                    pd.to_numeric(df[exp_col], errors="coerce")
                    .fillna(0.001)
                    .astype("float32")
                )

        # 5. Predict
        # Inject dummy target column for compatibility with dataframe_to_dataset
        df["landslide"] = 0
        ds = dataframe_to_dataset(
            df[input_cols + ["landslide"]].copy(), shuffle=False, batch_size=128
        )

        out = model.predict(ds, verbose=0)
        susceptibility = (
            out["final_head"].flatten() if isinstance(out, dict) else out.flatten()
        )

        # 6. Extract Intermediate Physics
        layers = _physics_layers(model)
        physics_extractor = tf.keras.Model(
            inputs=model.inputs,
            outputs={k: model.get_layer(v).output for k, v in layers.items()},
        )
        phys = physics_extractor.predict(ds, verbose=0)

        # 7. Assemble Output GeoDataFrame
        out_gdf = gpd.GeoDataFrame(df.copy(), geometry=geometry, crs=crs)
        count = len(out_gdf)

        # The QGIS plugin expects exactly 'Hazard_Susceptibility' instead of 'susceptibility'
        out_gdf["Hazard_Susceptibility"] = susceptibility
        out_gdf["FactorOfSafety"] = _column(phys["fos"], count)
        out_gdf["Displacement"] = _column(phys["displacement"], count)
        out_gdf["Cohesion"] = _column(phys["cohesion"], count)
        out_gdf["Internal_Friction"] = _column(phys["internal_friction"], count)
        if "wetness" in phys:
            out_gdf["Wetness"] = _column(phys["wetness"], count)
        else:
            print(
                "[WARN] Could not find wetness layer, using m=1.0 (fully saturated) "
                "for DFI FoS.",
                flush=True,
            )
            out_gdf["Wetness"] = np.ones(count)

        # DFI-specific Factor of Safety; Susceptibility_DFI drops stable units.
        # (Slope < 10 is already filtered out by preprocessing.)
        out_gdf["FoS_DFI"] = _fos_dfi(
            out_gdf["Cohesion"],
            out_gdf["Internal_Friction"],
            out_gdf["Wetness"],
            df["SoilThc_mean"],
            df["BUK_mean"],  # already converted to kN/m3
            df["Slope_mean"],
        )
        out_gdf["Susceptibility_DFI"] = np.where(
            out_gdf["FoS_DFI"] < FOS_DFI_STABLE, susceptibility, 0.0
        )

        # Save to Output GPKG
        main_output_path = output_gpkg
        if os.path.exists(main_output_path):
            os.remove(main_output_path)

        out_gdf.to_file(main_output_path, driver="GPKG")

        # 8. OVERALL OUTPUT: DFI susceptibility on the DEM grid. Valid terrain
        # outside any unit is 0 (low hazard, still routable), not NoData.
        overall_dir = os.path.join(base_dir, "overall_output")
        os.makedirs(overall_dir, exist_ok=True)
        overall_tif = os.path.join(overall_dir, "Overall_Output.tif")
        grid = _dem_grid(dem_template_path)
        crs_wkt = out_gdf.crs.to_wkt() if out_gdf.crs else None
        burned = _burn(out_gdf, np.asarray(out_gdf["Susceptibility_DFI"]), grid, 0.0)
        _write_raster(overall_tif, burned, grid, crs_wkt)
        del burned

        # 9. Objective 2 rasters for the depositional-zone FoS source refinement
        objective2 = _write_objective2_rasters(
            out_gdf, os.path.join(overall_dir, "objective2_rasters"), grid, crs_wkt
        )

        # 10. Return paths to UI Wrapper
        output_json = {
            "status": "success",
            "output": main_output_path,
            "overall_tif": overall_tif,
            **objective2,
            "friction_angle_unit": _friction_unit(out_gdf["Internal_Friction"]),
            "susceptibility_threshold": SUSCEPTIBILITY_THRESHOLD,
        }
        print(json.dumps(output_json))

    except Exception as e:
        import traceback

        # Error message exception
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
        )


if __name__ == "__main__":
    run_prediction()
