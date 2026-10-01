"""Step 6: screen landslide damming potential at stream intersections.

This standalone post-processing step evaluates stream cells intersected or
reached by the modeled Step 4 runout zone. It estimates the inflow angle between
the local runout approach direction and the stream-channel orientation, then
classifies stream-blockage potential using transparent geometric rules.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import heapq
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Iterable
import uuid


def configure_geospatial_environment() -> dict[str, str]:
    """Prefer geospatial data files bundled with the active Python environment."""

    site_roots = [
        Path(sys.prefix) / "Lib" / "site-packages",
        Path(sys.base_prefix) / "Lib" / "site-packages",
    ]
    proj_candidates = [
        "rasterio/proj_data",
        "pyogrio/proj_data",
        "fiona/proj_data",
        "pyproj/proj_dir/share/proj",
    ]
    gdal_candidates = [
        "rasterio/gdal_data",
        "fiona/gdal_data",
    ]
    configured: dict[str, str] = {}

    for root in site_roots:
        for relative in proj_candidates:
            candidate = root / relative
            if candidate.exists():
                value = str(candidate)
                os.environ["PROJ_LIB"] = value
                os.environ["PROJ_DATA"] = value
                configured["PROJ"] = value
                break
        if "PROJ" in configured:
            break

    for root in site_roots:
        for relative in gdal_candidates:
            candidate = root / relative
            if candidate.exists():
                value = str(candidate)
                os.environ["GDAL_DATA"] = value
                configured["GDAL"] = value
                break
        if "GDAL" in configured:
            break
    return configured


CONFIGURED_GEOSPATIAL_DIRS = configure_geospatial_environment()

import numpy as np
import rasterio
from rasterio.transform import xy as transform_xy

try:
    from scipy.spatial import cKDTree
except Exception:  # pragma: no cover - exercised only in minimal environments.
    cKDTree = None

try:
    from numba import get_num_threads, njit, prange, set_num_threads
except Exception:  # pragma: no cover - exercised only in minimal environments.
    get_num_threads = None
    njit = None
    prange = range
    set_num_threads = None


FLOAT_NODATA = -9999.0
BYTE_NODATA = 0


@dataclass
class RasterGrid:
    path: str
    array: np.ndarray
    nodata: float | None
    transform: Any
    crs: Any
    width: int
    height: int
    profile: dict[str, Any]


@dataclass
class Step6Params:
    dem_fel_raster: str
    dinf_flow_raster: str
    runout_mask_raster: str
    stream_raster: str
    slope_raster: str
    output_stream_intersection_mask: str
    output_inflow_angle: str
    output_stream_angle: str
    output_runout_approach_angle: str
    output_channel_slope: str
    output_candidate_damming_potential_class: str
    output_damming_potential_class: str
    output_damming_potential_score: str
    output_summary_json: str
    output_height_above_stream_raster: str = ""
    output_valley_floor_mask_raster: str = ""
    output_raw_valley_width_raster: str = ""
    output_corrected_valley_width_raster: str = ""
    output_reference_volume_domain_class: str = ""
    output_reference_volume_combined_class: str = ""
    output_reference_volume_combined_score: str = ""
    stream_vector: str = ""
    output_candidate_points: str = ""
    runout_approach_window_m: float = 25.0
    channel_slope_window_m: float = 25.0
    low_buffer_m: float = 5.0
    moderate_buffer_m: float = 10.0
    high_buffer_m: float = 15.0
    very_high_buffer_m: float = 15.0
    very_high_inflow_angle_deg: float = 75.0
    high_inflow_angle_deg: float = 60.0
    moderate_inflow_angle_deg: float = 30.0
    low_channel_gradient_deg: float = 10.0
    steep_channel_gradient_deg: float = 15.0
    valley_width_enabled: bool = True
    valley_width_elevation_threshold_m: float = 10.0
    valley_width_profile_half_length_m: float = 250.0
    valley_width_profile_sample_spacing_m: float = 0.0
    valley_width_meander_window_radius_m: float = 100.0
    valley_width_local_profile_spacing_m: float = 100.0
    numba_threads: int = 0
    valley_width_assignment_chunk_size: int = 50000
    reference_volume_enabled: bool = True
    reference_area_m2: float = 2500.0
    area_volume_coefficient: float = 0.54
    area_volume_exponent: float = 1.15
    delivery_fraction: float = 0.60
    reference_delivered_deposit_volume_m3: float = 2600.0
    reference_volume_rounding_increment_m3: float = 100.0
    overwrite: bool = False


@dataclass(frozen=True)
class StagedOutputBundle:
    official_params: Step6Params
    staged_params: Step6Params
    staged_to_official: dict[str, str]
    stage_dirs: tuple[Path, ...]


@dataclass
class ReferenceVolumeThresholds:
    reference_area_m2: float
    area_volume_coefficient: float
    area_volume_exponent: float
    source_volume_m3: float
    delivery_fraction: float
    delivered_volume_unrounded_m3: float
    reference_delivered_volume_m3: float
    formation_width_m: float
    possible_width_m: float


@dataclass
class CandidateRecord:
    row: int
    col: int
    x: float
    y: float
    inflow_angle_deg: float
    stream_angle_deg: float
    runout_approach_angle_deg: float
    channel_slope_deg: float | None
    baseline_damming_class: int
    geometry_baseline_class: int
    geometry_baseline_score: float
    damming_class: int
    damming_score: float
    interpretation: str
    candidate_source: str
    raw_valley_width_m: float | None = None
    valley_floor_width_m: float | None = None
    valley_width_censored: bool = False
    valley_width_profile_count: int = 0
    valley_width_method: str = ""
    reference_volume_domain_class: int = 0
    reference_volume_domain_status: str = ""
    reference_delivered_deposit_volume_m3: float | None = None
    reference_volume_formation_width_m: float | None = None
    reference_volume_possible_width_m: float | None = None
    reference_volume_combined_class: int = 0
    reference_volume_combined_score: float | None = None


@dataclass
class Step6Result:
    stream_intersection_mask: np.ndarray
    inflow_angle: np.ndarray
    stream_angle: np.ndarray
    runout_approach_angle: np.ndarray
    channel_slope: np.ndarray
    candidate_damming_class: np.ndarray
    damming_class: np.ndarray
    damming_score: np.ndarray
    height_above_stream: np.ndarray
    valley_floor_mask: np.ndarray
    raw_valley_width: np.ndarray
    corrected_valley_width: np.ndarray
    reference_volume_domain_class: np.ndarray
    reference_volume_combined_class: np.ndarray
    reference_volume_combined_score: np.ndarray
    records: list[CandidateRecord]
    summary: dict[str, Any]


@dataclass
class CoreArrays:
    dem: np.ndarray
    dinf: np.ndarray
    runout_mask: np.ndarray
    stream_mask: np.ndarray
    slope: np.ndarray
    dem_nodata: float | None = None
    dinf_nodata: float | None = None
    runout_nodata: float | None = None
    stream_nodata: float | None = None
    slope_nodata: float | None = None


@dataclass
class StreamSegment:
    x1: float
    y1: float
    x2: float
    y2: float
    angle_deg: float


@dataclass
class ValleyProfileMeasurement:
    raw_width_m: float | None
    censored: bool
    method: str


REQUIRED_CONFIG_FIELDS = {
    "dem_fel_raster",
    "dinf_flow_raster",
    "runout_mask_raster",
    "stream_raster",
    "slope_raster",
    "output_stream_intersection_mask",
    "output_inflow_angle",
    "output_stream_angle",
    "output_runout_approach_angle",
    "output_channel_slope",
    "output_candidate_damming_potential_class",
    "output_damming_potential_class",
    "output_damming_potential_score",
    "output_summary_json",
}

OPTIONAL_CONFIG_FIELDS = {
    "stream_vector",
    "output_candidate_points",
    "runout_approach_window_m",
    "channel_slope_window_m",
    "low_buffer_m",
    "moderate_buffer_m",
    "high_buffer_m",
    "very_high_buffer_m",
    "very_high_inflow_angle_deg",
    "high_inflow_angle_deg",
    "moderate_inflow_angle_deg",
    "low_channel_gradient_deg",
    "steep_channel_gradient_deg",
    "valley_width_enabled",
    "valley_width_elevation_threshold_m",
    "valley_width_profile_half_length_m",
    "valley_width_profile_sample_spacing_m",
    "valley_width_meander_window_radius_m",
    "valley_width_local_profile_spacing_m",
    "numba_threads",
    "valley_width_assignment_chunk_size",
    "reference_volume_enabled",
    "reference_area_m2",
    "area_volume_coefficient",
    "area_volume_exponent",
    "delivery_fraction",
    "reference_delivered_deposit_volume_m3",
    "reference_volume_rounding_increment_m3",
    "output_height_above_stream_raster",
    "output_valley_floor_mask_raster",
    "output_raw_valley_width_raster",
    "output_corrected_valley_width_raster",
    "output_reference_volume_domain_class",
    "output_reference_volume_combined_class",
    "output_reference_volume_combined_score",
}

ACTIVE_CONFIG_FIELDS = REQUIRED_CONFIG_FIELDS | OPTIONAL_CONFIG_FIELDS

REMOVED_CONFIG_FIELDS = {
    "candidate_search_radius_cells": (
        "Step 6 now evaluates direct runout-stream intersections only; "
        "candidate_search_radius_cells has been removed."
    ),
    "source_raster": "source_raster was an unused QA-only input and has been removed.",
    "depositional_mask_raster": (
        "depositional_mask_raster was an unused QA-only input and has been removed."
    ),
    "valley_width_flow_path_method": (
        "valley_width_flow_path_method has been removed because D-infinity primary routing is fixed."
    ),
    "valley_width_min_valid_profiles": (
        "valley_width_min_valid_profiles belonged to the removed quantile-scoring method."
    ),
    "valley_width_score_mode": "Quantile-based valley-width scoring has been removed.",
    "valley_width_narrow_quantile": "Quantile-based valley-width scoring has been removed.",
    "valley_width_wide_quantile": "Quantile-based valley-width scoring has been removed.",
    "valley_width_score_weight": "Quantile-based valley-width scoring has been removed.",
    "apply_valley_width_to_class": "Quantile-based valley-width class adjustment has been removed.",
    "valley_width_promote_threshold": "Quantile-based valley-width class adjustment has been removed.",
    "valley_width_demote_threshold": "Quantile-based valley-width class adjustment has been removed.",
}

INPUT_PARAM_FIELDS = (
    "dem_fel_raster",
    "dinf_flow_raster",
    "runout_mask_raster",
    "stream_raster",
    "slope_raster",
    "stream_vector",
)

CORE_OUTPUT_PARAM_FIELDS = (
    "output_stream_intersection_mask",
    "output_inflow_angle",
    "output_stream_angle",
    "output_runout_approach_angle",
    "output_channel_slope",
    "output_candidate_damming_potential_class",
    "output_damming_potential_class",
    "output_damming_potential_score",
    "output_summary_json",
)

OPTIONAL_OUTPUT_PARAM_FIELDS = (
    "output_height_above_stream_raster",
    "output_valley_floor_mask_raster",
    "output_raw_valley_width_raster",
    "output_corrected_valley_width_raster",
    "output_reference_volume_domain_class",
    "output_reference_volume_combined_class",
    "output_reference_volume_combined_score",
    "output_candidate_points",
)


def _resolve_config_path(config_dir: Path, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = config_dir / path
    return str(path.resolve())


def _require_text(config: dict[str, Any], key: str) -> str:
    value = str(config.get(key, "")).strip()
    if not value:
        raise ValueError(f"Config value '{key}' is required.")
    return value


def _parse_float(config: dict[str, Any], key: str, default: float) -> float:
    value = float(config.get(key, default))
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite.")
    return value


def _parse_int(config: dict[str, Any], key: str, default: int) -> int:
    raw = config.get(key, default)
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be an integer.")
    value = int(raw)
    if float(raw) != float(value):
        raise ValueError(f"{key} must be an integer.")
    if value < 0:
        raise ValueError(f"{key} must be >= 0.")
    return value


def _parse_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise ValueError(f"{key} must be a boolean.")


def calculate_reference_volume_thresholds(
    reference_area_m2: float,
    area_volume_coefficient: float,
    area_volume_exponent: float,
    delivery_fraction: float,
    reference_delivered_deposit_volume_m3: float,
) -> ReferenceVolumeThresholds:
    source_volume_m3 = float(area_volume_coefficient) * (float(reference_area_m2) ** float(area_volume_exponent))
    delivered_volume_unrounded_m3 = source_volume_m3 * float(delivery_fraction)
    reference_volume_m3 = float(reference_delivered_deposit_volume_m3)
    formation_width_m = math.sqrt(reference_volume_m3 / 180.3)
    possible_width_m = (reference_volume_m3 / 1.7) ** (1.0 / 2.5)
    return ReferenceVolumeThresholds(
        reference_area_m2=float(reference_area_m2),
        area_volume_coefficient=float(area_volume_coefficient),
        area_volume_exponent=float(area_volume_exponent),
        source_volume_m3=source_volume_m3,
        delivery_fraction=float(delivery_fraction),
        delivered_volume_unrounded_m3=delivered_volume_unrounded_m3,
        reference_delivered_volume_m3=reference_volume_m3,
        formation_width_m=float(formation_width_m),
        possible_width_m=float(possible_width_m),
    )


def rounded_reference_volume(delivered_volume_m3: float, increment_m3: float) -> float:
    if increment_m3 <= 0:
        return float(delivered_volume_m3)
    return float(round(float(delivered_volume_m3) / float(increment_m3)) * float(increment_m3))


def load_params(config_path: str, overwrite: bool = False) -> Step6Params:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config JSON does not exist: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Step 6 config must be a JSON object.")
    removed = [key for key in sorted(REMOVED_CONFIG_FIELDS) if key in config]
    if removed:
        explanations = " ".join(REMOVED_CONFIG_FIELDS[key] for key in removed)
        raise ValueError(f"Removed Step 6 config field(s): {', '.join(removed)}. {explanations}")
    unknown = sorted(set(config) - ACTIVE_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"Unknown Step 6 config field(s): {', '.join(unknown)}")
    missing = sorted(key for key in REQUIRED_CONFIG_FIELDS if not str(config.get(key, "")).strip())
    if missing:
        raise ValueError(f"Missing required Step 6 config field(s): {', '.join(missing)}")
    config_dir = path.parent

    params = Step6Params(
        dem_fel_raster=_resolve_config_path(config_dir, _require_text(config, "dem_fel_raster")),
        dinf_flow_raster=_resolve_config_path(config_dir, _require_text(config, "dinf_flow_raster")),
        runout_mask_raster=_resolve_config_path(config_dir, _require_text(config, "runout_mask_raster")),
        stream_raster=_resolve_config_path(config_dir, _require_text(config, "stream_raster")),
        slope_raster=_resolve_config_path(config_dir, _require_text(config, "slope_raster")),
        stream_vector=_resolve_config_path(config_dir, config.get("stream_vector", "")),
        runout_approach_window_m=_parse_float(config, "runout_approach_window_m", 25.0),
        channel_slope_window_m=_parse_float(config, "channel_slope_window_m", 25.0),
        low_buffer_m=_parse_float(config, "low_buffer_m", 5.0),
        moderate_buffer_m=_parse_float(config, "moderate_buffer_m", 10.0),
        high_buffer_m=_parse_float(config, "high_buffer_m", 15.0),
        very_high_buffer_m=_parse_float(config, "very_high_buffer_m", 15.0),
        very_high_inflow_angle_deg=_parse_float(config, "very_high_inflow_angle_deg", 75.0),
        high_inflow_angle_deg=_parse_float(config, "high_inflow_angle_deg", 60.0),
        moderate_inflow_angle_deg=_parse_float(config, "moderate_inflow_angle_deg", 30.0),
        low_channel_gradient_deg=_parse_float(config, "low_channel_gradient_deg", 10.0),
        steep_channel_gradient_deg=_parse_float(config, "steep_channel_gradient_deg", 15.0),
        valley_width_enabled=_parse_bool(config, "valley_width_enabled", True),
        valley_width_elevation_threshold_m=_parse_float(config, "valley_width_elevation_threshold_m", 10.0),
        valley_width_profile_half_length_m=_parse_float(config, "valley_width_profile_half_length_m", 250.0),
        valley_width_profile_sample_spacing_m=_parse_float(config, "valley_width_profile_sample_spacing_m", 0.0),
        valley_width_meander_window_radius_m=_parse_float(config, "valley_width_meander_window_radius_m", 100.0),
        valley_width_local_profile_spacing_m=_parse_float(config, "valley_width_local_profile_spacing_m", 100.0),
        numba_threads=_parse_int(config, "numba_threads", 0),
        valley_width_assignment_chunk_size=_parse_int(
            config, "valley_width_assignment_chunk_size", 50000
        ),
        reference_volume_enabled=_parse_bool(config, "reference_volume_enabled", True),
        reference_area_m2=_parse_float(config, "reference_area_m2", 2500.0),
        area_volume_coefficient=_parse_float(config, "area_volume_coefficient", 0.54),
        area_volume_exponent=_parse_float(config, "area_volume_exponent", 1.15),
        delivery_fraction=_parse_float(config, "delivery_fraction", 0.60),
        reference_delivered_deposit_volume_m3=_parse_float(
            config, "reference_delivered_deposit_volume_m3", 2600.0
        ),
        reference_volume_rounding_increment_m3=_parse_float(
            config, "reference_volume_rounding_increment_m3", 100.0
        ),
        output_stream_intersection_mask=_resolve_config_path(
            config_dir, _require_text(config, "output_stream_intersection_mask")
        ),
        output_inflow_angle=_resolve_config_path(config_dir, _require_text(config, "output_inflow_angle")),
        output_stream_angle=_resolve_config_path(config_dir, _require_text(config, "output_stream_angle")),
        output_runout_approach_angle=_resolve_config_path(
            config_dir, _require_text(config, "output_runout_approach_angle")
        ),
        output_channel_slope=_resolve_config_path(config_dir, _require_text(config, "output_channel_slope")),
        output_candidate_damming_potential_class=_resolve_config_path(
            config_dir, _require_text(config, "output_candidate_damming_potential_class")
        ),
        output_damming_potential_class=_resolve_config_path(
            config_dir, _require_text(config, "output_damming_potential_class")
        ),
        output_damming_potential_score=_resolve_config_path(
            config_dir, _require_text(config, "output_damming_potential_score")
        ),
        output_height_above_stream_raster=_resolve_config_path(
            config_dir, config.get("output_height_above_stream_raster", "")
        ),
        output_valley_floor_mask_raster=_resolve_config_path(
            config_dir, config.get("output_valley_floor_mask_raster", "")
        ),
        output_raw_valley_width_raster=_resolve_config_path(
            config_dir, config.get("output_raw_valley_width_raster", "")
        ),
        output_corrected_valley_width_raster=_resolve_config_path(
            config_dir, config.get("output_corrected_valley_width_raster", "")
        ),
        output_reference_volume_domain_class=_resolve_config_path(
            config_dir, config.get("output_reference_volume_domain_class", "")
        ),
        output_reference_volume_combined_class=_resolve_config_path(
            config_dir, config.get("output_reference_volume_combined_class", "")
        ),
        output_reference_volume_combined_score=_resolve_config_path(
            config_dir, config.get("output_reference_volume_combined_score", "")
        ),
        output_candidate_points=_resolve_config_path(config_dir, config.get("output_candidate_points", "")),
        output_summary_json=_resolve_config_path(config_dir, _require_text(config, "output_summary_json")),
        overwrite=overwrite,
    )
    validate_thresholds(params)
    return params


def validate_thresholds(params: Step6Params) -> None:
    numeric_fields = (
        "runout_approach_window_m",
        "channel_slope_window_m",
        "low_buffer_m",
        "moderate_buffer_m",
        "high_buffer_m",
        "very_high_buffer_m",
        "very_high_inflow_angle_deg",
        "high_inflow_angle_deg",
        "moderate_inflow_angle_deg",
        "low_channel_gradient_deg",
        "steep_channel_gradient_deg",
        "valley_width_elevation_threshold_m",
        "valley_width_profile_half_length_m",
        "valley_width_profile_sample_spacing_m",
        "valley_width_meander_window_radius_m",
        "valley_width_local_profile_spacing_m",
        "reference_area_m2",
        "area_volume_coefficient",
        "area_volume_exponent",
        "delivery_fraction",
        "reference_delivered_deposit_volume_m3",
        "reference_volume_rounding_increment_m3",
    )
    for field_name in numeric_fields:
        if not math.isfinite(float(getattr(params, field_name))):
            raise ValueError(f"{field_name} must be finite.")
    if not 0.0 <= params.moderate_inflow_angle_deg <= params.high_inflow_angle_deg <= params.very_high_inflow_angle_deg <= 90.0:
        raise ValueError("Inflow-angle thresholds must satisfy 0 <= moderate <= high <= very_high <= 90.")
    if params.low_channel_gradient_deg < 0 or params.steep_channel_gradient_deg < params.low_channel_gradient_deg:
        raise ValueError("Channel-gradient thresholds must satisfy 0 <= low <= steep.")
    if params.runout_approach_window_m <= 0:
        raise ValueError("runout_approach_window_m must be > 0.")
    if params.channel_slope_window_m <= 0:
        raise ValueError("channel_slope_window_m must be > 0.")
    for field_name in ["low_buffer_m", "moderate_buffer_m", "high_buffer_m", "very_high_buffer_m"]:
        if getattr(params, field_name) < 0:
            raise ValueError(f"{field_name} must be >= 0.")
    if params.valley_width_elevation_threshold_m < 0:
        raise ValueError("valley_width_elevation_threshold_m must be >= 0.")
    if params.valley_width_profile_half_length_m <= 0:
        raise ValueError("valley_width_profile_half_length_m must be > 0.")
    if params.valley_width_profile_sample_spacing_m < 0:
        raise ValueError("valley_width_profile_sample_spacing_m must be >= 0.")
    if params.valley_width_meander_window_radius_m < 0:
        raise ValueError("valley_width_meander_window_radius_m must be >= 0.")
    if params.valley_width_local_profile_spacing_m <= 0:
        raise ValueError("valley_width_local_profile_spacing_m must be > 0.")
    if params.valley_width_assignment_chunk_size < 1:
        raise ValueError("valley_width_assignment_chunk_size must be >= 1.")
    required_width_outputs = {
        "output_height_above_stream_raster": params.output_height_above_stream_raster,
        "output_valley_floor_mask_raster": params.output_valley_floor_mask_raster,
        "output_raw_valley_width_raster": params.output_raw_valley_width_raster,
        "output_corrected_valley_width_raster": params.output_corrected_valley_width_raster,
    }
    if params.valley_width_enabled:
        missing = [name for name, value in required_width_outputs.items() if not value]
        if missing:
            raise ValueError("Valley-width outputs are required when valley_width_enabled=true: " + ", ".join(missing))
    if params.reference_volume_enabled:
        if not params.valley_width_enabled:
            raise ValueError("reference_volume_enabled=true requires valley_width_enabled=true.")
        if params.reference_area_m2 <= 0:
            raise ValueError("reference_area_m2 must be > 0.")
        if params.area_volume_coefficient <= 0:
            raise ValueError("area_volume_coefficient must be > 0.")
        if params.area_volume_exponent <= 0:
            raise ValueError("area_volume_exponent must be > 0.")
        if not 0.0 < params.delivery_fraction <= 1.0:
            raise ValueError("delivery_fraction must satisfy 0 < delivery_fraction <= 1.")
        if params.reference_delivered_deposit_volume_m3 <= 0:
            raise ValueError("reference_delivered_deposit_volume_m3 must be > 0.")
        if params.reference_volume_rounding_increment_m3 <= 0:
            raise ValueError("reference_volume_rounding_increment_m3 must be > 0.")
        reference_outputs = {
            "output_reference_volume_domain_class": params.output_reference_volume_domain_class,
            "output_reference_volume_combined_class": params.output_reference_volume_combined_class,
            "output_reference_volume_combined_score": params.output_reference_volume_combined_score,
        }
        missing = [name for name, value in reference_outputs.items() if not value]
        if missing:
            raise ValueError(
                "Reference-volume outputs are required when reference_volume_enabled=true: " + ", ".join(missing)
            )
        thresholds = calculate_reference_volume_thresholds(
            params.reference_area_m2,
            params.area_volume_coefficient,
            params.area_volume_exponent,
            params.delivery_fraction,
            params.reference_delivered_deposit_volume_m3,
        )
        rounded = rounded_reference_volume(
            thresholds.delivered_volume_unrounded_m3,
            params.reference_volume_rounding_increment_m3,
        )
        if not math.isclose(rounded, params.reference_delivered_deposit_volume_m3, rel_tol=0.0, abs_tol=1.0e-6):
            raise ValueError(
                "reference_delivered_deposit_volume_m3 is inconsistent with the area-volume, "
                f"delivery, and rounding assumptions: derived rounded volume is {rounded:.6f} m3."
            )
    output_paths = step6_output_paths(params)
    normalized_outputs: dict[str, str] = {}
    for field_name, path in output_paths.items():
        normalized = os.path.normcase(str(Path(path).expanduser().resolve()))
        if normalized in normalized_outputs:
            raise ValueError(
                f"{field_name} duplicates output path configured by "
                f"{normalized_outputs[normalized]}: {path}"
            )
        normalized_outputs[normalized] = field_name

    normalized_inputs = {
        os.path.normcase(str(Path(str(getattr(params, field_name))).expanduser().resolve())): field_name
        for field_name in INPUT_PARAM_FIELDS
        if str(getattr(params, field_name)).strip()
    }
    for normalized, output_field in normalized_outputs.items():
        if normalized in normalized_inputs:
            raise ValueError(
                f"{output_field} must not overwrite input {normalized_inputs[normalized]}: "
                f"{output_paths[output_field]}"
            )


def step6_output_paths(params: Step6Params) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for field_name in CORE_OUTPUT_PARAM_FIELDS + OPTIONAL_OUTPUT_PARAM_FIELDS:
        value = str(getattr(params, field_name)).strip()
        if value:
            outputs[field_name] = value
    return outputs


def read_raster(path: str, label: str) -> RasterGrid:
    if not path:
        raise ValueError(f"{label} path is blank.")
    if not Path(path).exists():
        raise FileNotFoundError(f"{label} raster does not exist: {path}")
    with rasterio.open(path) as src:
        array = src.read(1)
        return RasterGrid(
            path=str(Path(path).resolve()),
            array=array,
            nodata=None if src.nodata is None else float(src.nodata),
            transform=src.transform,
            crs=src.crs,
            width=src.width,
            height=src.height,
            profile=src.profile.copy(),
        )


def validate_alignment(reference: RasterGrid, other: RasterGrid, label: str) -> None:
    errors: list[str] = []
    if reference.width != other.width or reference.height != other.height:
        errors.append(f"shape {other.width}x{other.height} != {reference.width}x{reference.height}")
    if reference.transform != other.transform:
        errors.append("transform differs")
    if reference.crs != other.crs:
        errors.append(f"CRS differs ({other.crs} != {reference.crs})")
    if errors:
        raise ValueError(f"{label} raster is not aligned to DEM: " + "; ".join(errors))


def valid_mask(array: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(array)
    if nodata is not None:
        mask &= ~np.isclose(array, nodata, atol=0.0, rtol=0.0)
    return mask


def validate_binary_mask_raster(grid: RasterGrid, label: str) -> None:
    valid = valid_mask(grid.array, grid.nodata)
    invalid = valid & (grid.array != 0) & (grid.array != 1)
    if np.any(invalid):
        examples = np.unique(grid.array[invalid])[:10].tolist()
        raise ValueError(
            f"{label} must be binary where valid cells are only 0 or 1; "
            f"unexpected value example(s): {examples}"
        )


def validate_dinf_raster(grid: RasterGrid) -> None:
    valid = valid_mask(grid.array, grid.nodata)
    if not np.any(valid):
        raise ValueError("dinf_flow_raster contains no valid cells.")
    values = grid.array[valid]
    tolerance = 1.0e-6
    if np.any(values < -tolerance) or np.any(values > 2.0 * math.pi + tolerance):
        raise ValueError(
            "dinf_flow_raster valid values must be D-infinity directions in radians within [0, 2*pi]."
        )


def validate_slope_raster(grid: RasterGrid) -> None:
    valid = valid_mask(grid.array, grid.nodata)
    if not np.any(valid):
        raise ValueError("slope_raster contains no valid cells.")
    values = grid.array[valid]
    tolerance = 1.0e-6
    if np.any(values < -tolerance) or np.any(values > 90.0 + tolerance):
        raise ValueError("slope_raster valid values must be slope angles in degrees within [0, 90].")


def assert_metric_grid(grid: RasterGrid) -> tuple[float, float]:
    dx = float(abs(grid.transform.a))
    dy = float(abs(grid.transform.e))
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("DEM transform must have positive x/y cell sizes.")
    if grid.crs is not None and hasattr(grid.crs, "is_projected") and not grid.crs.is_projected:
        raise ValueError("Step 6 expects a projected metric CRS for slope and distance calculations.")
    return dx, dy


def _slice_for_shift(length: int, shift: int) -> tuple[slice, slice]:
    if shift >= 0:
        return slice(shift, length), slice(0, length - shift)
    return slice(0, length + shift), slice(-shift, length)


def dilate_mask_by_distance(mask: np.ndarray, buffer_m: float, dx: float, dy: float) -> np.ndarray:
    if buffer_m <= 0:
        return mask.copy()
    rows, cols = mask.shape
    row_radius = int(math.ceil(float(buffer_m) / dy))
    col_radius = int(math.ceil(float(buffer_m) / dx))
    out = mask.copy()
    for dr in range(-row_radius, row_radius + 1):
        for dc in range(-col_radius, col_radius + 1):
            if dr == 0 and dc == 0:
                continue
            if abs(dr) >= rows or abs(dc) >= cols:
                continue
            if math.hypot(float(dr) * dy, float(dc) * dx) > float(buffer_m) + 1e-9:
                continue
            dst_r, src_r = _slice_for_shift(rows, dr)
            dst_c, src_c = _slice_for_shift(cols, dc)
            out[dst_r, dst_c] |= mask[src_r, src_c]
    return out


def normalize_axial_angle(angle_deg: float) -> float:
    value = float(angle_deg) % 180.0
    if value < 0.0:
        value += 180.0
    return value


def normalize_direction_angle(angle_deg: float) -> float:
    value = float(angle_deg) % 360.0
    if value < 0.0:
        value += 360.0
    return value


def inflow_angle_deg(runout_angle_deg: float, stream_angle_deg: float) -> float:
    delta = abs(float(runout_angle_deg) - float(stream_angle_deg))
    delta = delta % 180.0
    if delta > 90.0:
        return 180.0 - delta
    return delta


def circular_mean_deg(angles_deg: Iterable[float]) -> float:
    angles = [math.radians(normalize_direction_angle(value)) for value in angles_deg]
    if not angles:
        raise ValueError("Cannot compute circular mean of an empty angle list.")
    sin_sum = float(np.sum(np.sin(angles)))
    cos_sum = float(np.sum(np.cos(angles)))
    return normalize_direction_angle(math.degrees(math.atan2(sin_sum, cos_sum)))


def angular_difference_deg(left: float, right: float) -> float:
    delta = abs(normalize_direction_angle(left) - normalize_direction_angle(right)) % 360.0
    return min(delta, 360.0 - delta)


def direction_from_to(row0: int, col0: int, row1: int, col1: int, dx: float, dy: float) -> float:
    x_delta = float(col1 - col0) * dx
    y_delta = float(row0 - row1) * dy
    return normalize_direction_angle(math.degrees(math.atan2(y_delta, x_delta)))


def dinf_radians_to_degrees(angle_rad: float) -> float:
    return normalize_direction_angle(math.degrees(float(angle_rad)))


def estimate_stream_angle_from_raster(
    stream_mask: np.ndarray,
    row: int,
    col: int,
    dx: float,
    dy: float,
    *,
    window_radius: int = 2,
) -> float | None:
    points: list[tuple[float, float]] = []
    rows, cols = stream_mask.shape
    for rr in range(max(0, row - window_radius), min(rows, row + window_radius + 1)):
        for cc in range(max(0, col - window_radius), min(cols, col + window_radius + 1)):
            if not stream_mask[rr, cc]:
                continue
            x = float(cc - col) * dx
            y = float(row - rr) * dy
            points.append((x, y))
    if len(points) < 2:
        return None
    coords = np.asarray(points, dtype=np.float64)
    coords -= coords.mean(axis=0)
    if float(np.max(np.abs(coords))) == 0.0:
        return None
    cov = coords.T @ coords
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    vector = eigenvectors[:, int(np.argmax(eigenvalues))]
    if float(np.linalg.norm(vector)) == 0.0:
        return None
    return normalize_axial_angle(math.degrees(math.atan2(float(vector[1]), float(vector[0]))))


def estimate_runout_approach_angle(
    runout_mask: np.ndarray,
    dinf: np.ndarray,
    dinf_valid: np.ndarray,
    row: int,
    col: int,
    dx: float,
    dy: float,
    *,
    window_distance_m: float,
) -> tuple[float | None, bool, int]:
    rows, cols = runout_mask.shape
    row_radius = int(math.ceil(float(window_distance_m) / dy))
    col_radius = int(math.ceil(float(window_distance_m) / dx))
    upstream_angles: list[float] = []
    fallback_candidates: list[tuple[float, float, float]] = []

    for rr in range(max(0, row - row_radius), min(rows, row + row_radius + 1)):
        for cc in range(max(0, col - col_radius), min(cols, col + col_radius + 1)):
            if not runout_mask[rr, cc] or not dinf_valid[rr, cc]:
                continue
            if rr == row and cc == col:
                continue
            distance_m = math.hypot(float(rr - row) * dy, float(cc - col) * dx)
            if distance_m <= 0.0 or distance_m > float(window_distance_m) + 1e-9:
                continue

            flow_angle = dinf_radians_to_degrees(float(dinf[rr, cc]))
            target_angle = direction_from_to(rr, cc, row, col, dx, dy)
            connection_diff = angular_difference_deg(flow_angle, target_angle)
            fallback_candidates.append((connection_diff, distance_m, flow_angle))
            if connection_diff <= 45.0:
                upstream_angles.append(flow_angle)

    if upstream_angles:
        return circular_mean_deg(upstream_angles), len(upstream_angles) > 1, len(upstream_angles)
    if not fallback_candidates:
        return None, True, 0

    fallback_candidates.sort(key=lambda item: (item[0], item[1]))
    best_diff = fallback_candidates[0][0]
    tied = [item[2] for item in fallback_candidates if abs(item[0] - best_diff) <= 5.0]
    if len(tied) > 1:
        return circular_mean_deg(tied), True, len(fallback_candidates)
    return fallback_candidates[0][2], False, len(fallback_candidates)


def estimate_mean_channel_slope_from_raster(
    slope: np.ndarray,
    slope_valid: np.ndarray,
    row: int,
    col: int,
    stream_angle_deg: float,
    dx: float,
    dy: float,
    window_distance_m: float,
) -> float | None:
    angle_rad = math.radians(normalize_axial_angle(stream_angle_deg))
    unit_col = math.cos(angle_rad)
    unit_row = -math.sin(angle_rad)
    rows, cols = slope.shape
    samples: list[float] = []
    visited: set[tuple[int, int]] = set()
    max_steps = max(1, int(math.ceil(float(window_distance_m) / min(dx, dy))))

    for sign in [-1, 1]:
        for step in range(1, max_steps + 1):
            dcol = int(round(sign * unit_col * step))
            drow = int(round(sign * unit_row * step))
            if drow == 0 and dcol == 0:
                continue
            distance = math.hypot(float(drow) * dy, float(dcol) * dx)
            if distance > float(window_distance_m) + 1e-9:
                continue
            rr = row + drow
            cc = col + dcol
            if not (0 <= rr < rows and 0 <= cc < cols):
                continue
            key = (rr, cc)
            if key in visited:
                continue
            visited.add(key)
            if slope_valid[rr, cc]:
                samples.append(float(slope[rr, cc]))

    if not samples:
        return None
    return float(np.mean(samples))


def classify_candidate(inflow: float, channel_slope: float | None, params: Step6Params) -> tuple[int, str]:
    if channel_slope is None or not math.isfinite(float(channel_slope)):
        if inflow >= params.very_high_inflow_angle_deg:
            return 4, "very high damming potential; slope unavailable"
        if inflow >= params.high_inflow_angle_deg:
            return 3, "high damming potential; slope unavailable"
        if inflow >= params.moderate_inflow_angle_deg:
            return 2, "moderate damming potential; slope unavailable"
        return 1, "low damming potential; slope unavailable"

    slope = float(channel_slope)
    if inflow >= params.very_high_inflow_angle_deg and slope < params.low_channel_gradient_deg:
        return 4, "very high damming potential"
    if inflow >= params.high_inflow_angle_deg and slope < params.low_channel_gradient_deg:
        return 3, "high damming potential"
    if inflow >= params.high_inflow_angle_deg and slope >= params.low_channel_gradient_deg:
        return 2, "moderate damming potential"
    if inflow >= params.moderate_inflow_angle_deg and inflow < params.high_inflow_angle_deg and slope < params.low_channel_gradient_deg:
        return 2, "moderate damming potential"
    if inflow < params.high_inflow_angle_deg and slope >= params.low_channel_gradient_deg:
        return 5, "debris-flow connectivity favored"
    if inflow < params.moderate_inflow_angle_deg and slope < params.low_channel_gradient_deg:
        return 1, "low damming potential"
    return 1, "low damming potential"


def damming_score(inflow: float, channel_slope: float | None, params: Step6Params) -> float:
    angle_score = float(np.clip((float(inflow) - params.moderate_inflow_angle_deg) / 60.0, 0.0, 1.0))
    if channel_slope is None or not math.isfinite(float(channel_slope)):
        slope_modifier = 1.0
    elif float(channel_slope) < params.low_channel_gradient_deg:
        slope_modifier = 1.0
    elif float(channel_slope) < params.steep_channel_gradient_deg:
        slope_modifier = 0.75
    else:
        slope_modifier = 0.50
    return float(np.clip(angle_score * slope_modifier, 0.0, 1.0))


if njit is not None:

    @njit(cache=True)
    def _normalize_direction_angle_numba(angle_deg: float) -> float:
        value = angle_deg % 360.0
        if value < 0.0:
            value += 360.0
        return value


    @njit(cache=True)
    def _normalize_axial_angle_numba(angle_deg: float) -> float:
        value = angle_deg % 180.0
        if value < 0.0:
            value += 180.0
        return value


    @njit(cache=True)
    def _angular_difference_deg_numba(left: float, right: float) -> float:
        delta = abs(_normalize_direction_angle_numba(left) - _normalize_direction_angle_numba(right)) % 360.0
        if delta < 360.0 - delta:
            return delta
        return 360.0 - delta


    @njit(cache=True)
    def _direction_from_to_numba(row0: int, col0: int, row1: int, col1: int, dx: float, dy: float) -> float:
        x_delta = float(col1 - col0) * dx
        y_delta = float(row0 - row1) * dy
        return _normalize_direction_angle_numba(math.degrees(math.atan2(y_delta, x_delta)))


    @njit(cache=True)
    def _inflow_angle_deg_numba(runout_angle_deg: float, stream_angle_deg: float) -> float:
        delta = abs(runout_angle_deg - stream_angle_deg) % 180.0
        if delta > 90.0:
            return 180.0 - delta
        return delta


    @njit(cache=True)
    def _estimate_stream_angle_numba(stream_mask: np.ndarray, row: int, col: int, dx: float, dy: float) -> float:
        rows, cols = stream_mask.shape
        count = 0
        sum_x = 0.0
        sum_y = 0.0
        for rr in range(max(0, row - 2), min(rows, row + 3)):
            for cc in range(max(0, col - 2), min(cols, col + 3)):
                if not stream_mask[rr, cc]:
                    continue
                x = float(cc - col) * dx
                y = float(row - rr) * dy
                sum_x += x
                sum_y += y
                count += 1
        if count < 2:
            return -9999.0
        mean_x = sum_x / float(count)
        mean_y = sum_y / float(count)
        cov_xx = 0.0
        cov_xy = 0.0
        cov_yy = 0.0
        max_abs = 0.0
        for rr in range(max(0, row - 2), min(rows, row + 3)):
            for cc in range(max(0, col - 2), min(cols, col + 3)):
                if not stream_mask[rr, cc]:
                    continue
                x = float(cc - col) * dx - mean_x
                y = float(row - rr) * dy - mean_y
                cov_xx += x * x
                cov_xy += x * y
                cov_yy += y * y
                ax = abs(x)
                ay = abs(y)
                if ax > max_abs:
                    max_abs = ax
                if ay > max_abs:
                    max_abs = ay
        if max_abs == 0.0:
            return -9999.0
        principal_angle = 0.5 * math.degrees(math.atan2(2.0 * cov_xy, cov_xx - cov_yy))
        return _normalize_axial_angle_numba(principal_angle)


    @njit(cache=True)
    def _classify_candidate_numba(
        inflow: float,
        channel_slope: float,
        has_channel_slope: bool,
        very_high_inflow_angle_deg: float,
        high_inflow_angle_deg: float,
        moderate_inflow_angle_deg: float,
        low_channel_gradient_deg: float,
    ) -> int:
        if not has_channel_slope:
            if inflow >= very_high_inflow_angle_deg:
                return 4
            if inflow >= high_inflow_angle_deg:
                return 3
            if inflow >= moderate_inflow_angle_deg:
                return 2
            return 1
        if inflow >= very_high_inflow_angle_deg and channel_slope < low_channel_gradient_deg:
            return 4
        if inflow >= high_inflow_angle_deg and channel_slope < low_channel_gradient_deg:
            return 3
        if inflow >= high_inflow_angle_deg and channel_slope >= low_channel_gradient_deg:
            return 2
        if inflow >= moderate_inflow_angle_deg and inflow < high_inflow_angle_deg and channel_slope < low_channel_gradient_deg:
            return 2
        if inflow < high_inflow_angle_deg and channel_slope >= low_channel_gradient_deg:
            return 5
        if inflow < moderate_inflow_angle_deg and channel_slope < low_channel_gradient_deg:
            return 1
        return 1


    @njit(cache=True)
    def _damming_score_numba(
        inflow: float,
        channel_slope: float,
        has_channel_slope: bool,
        moderate_inflow_angle_deg: float,
        low_channel_gradient_deg: float,
        steep_channel_gradient_deg: float,
    ) -> float:
        angle_score = (inflow - moderate_inflow_angle_deg) / 60.0
        if angle_score < 0.0:
            angle_score = 0.0
        elif angle_score > 1.0:
            angle_score = 1.0
        if not has_channel_slope or channel_slope < low_channel_gradient_deg:
            slope_modifier = 1.0
        elif channel_slope < steep_channel_gradient_deg:
            slope_modifier = 0.75
        else:
            slope_modifier = 0.50
        score = angle_score * slope_modifier
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score


    @njit(cache=True, parallel=True)
    def _evaluate_candidate_cells_numba(
        candidate_rows: np.ndarray,
        candidate_cols: np.ndarray,
        runout_mask: np.ndarray,
        direct_candidates: np.ndarray,
        stream_mask: np.ndarray,
        dinf: np.ndarray,
        dinf_valid: np.ndarray,
        slope: np.ndarray,
        slope_valid: np.ndarray,
        dx: float,
        dy: float,
        runout_approach_window_m: float,
        channel_slope_window_m: float,
        very_high_inflow_angle_deg: float,
        high_inflow_angle_deg: float,
        moderate_inflow_angle_deg: float,
        low_channel_gradient_deg: float,
        steep_channel_gradient_deg: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        n = len(candidate_rows)
        valid = np.zeros(n, dtype=np.bool_)
        inflow = np.full(n, -9999.0, dtype=np.float32)
        stream_angle = np.full(n, -9999.0, dtype=np.float32)
        runout_angle = np.full(n, -9999.0, dtype=np.float32)
        channel_slope = np.full(n, -9999.0, dtype=np.float32)
        dam_class = np.zeros(n, dtype=np.uint8)
        dam_score = np.full(n, -9999.0, dtype=np.float32)
        flags = np.zeros((n, 4), dtype=np.uint8)
        candidate_source = np.zeros(n, dtype=np.uint8)
        rows, cols = runout_mask.shape
        row_radius = int(math.ceil(runout_approach_window_m / dy))
        col_radius = int(math.ceil(runout_approach_window_m / dx))
        max_channel_steps = max(1, int(math.ceil(channel_slope_window_m / min(dx, dy))))

        for i in prange(n):
            row = int(candidate_rows[i])
            col = int(candidate_cols[i])
            if direct_candidates[row, col]:
                candidate_source[i] = 1

            local_stream_angle = _estimate_stream_angle_numba(stream_mask, row, col, dx, dy)
            if local_stream_angle <= -9000.0:
                flags[i, 0] = 1
                continue

            upstream_count = 0
            fallback_count = 0
            fallback_best_diff = 1.0e9
            fallback_best_distance = 1.0e9
            fallback_best_angle = -9999.0
            tie_sin = 0.0
            tie_cos = 0.0
            tie_count = 0
            upstream_sin = 0.0
            upstream_cos = 0.0

            r_min = max(0, row - row_radius)
            r_max = min(rows, row + row_radius + 1)
            c_min = max(0, col - col_radius)
            c_max = min(cols, col + col_radius + 1)
            for rr in range(r_min, r_max):
                for cc in range(c_min, c_max):
                    if not runout_mask[rr, cc] or not dinf_valid[rr, cc]:
                        continue
                    if rr == row and cc == col:
                        continue
                    distance_m = math.hypot(float(rr - row) * dy, float(cc - col) * dx)
                    if distance_m <= 0.0 or distance_m > runout_approach_window_m + 1.0e-9:
                        continue
                    flow_angle = _normalize_direction_angle_numba(math.degrees(float(dinf[rr, cc])))
                    target_angle = _direction_from_to_numba(rr, cc, row, col, dx, dy)
                    connection_diff = _angular_difference_deg_numba(flow_angle, target_angle)
                    fallback_count += 1
                    if connection_diff < fallback_best_diff - 1.0e-9 or (
                        abs(connection_diff - fallback_best_diff) <= 1.0e-9 and distance_m < fallback_best_distance
                    ):
                        fallback_best_diff = connection_diff
                        fallback_best_distance = distance_m
                        fallback_best_angle = flow_angle
                    if connection_diff <= 45.0:
                        angle_rad = math.radians(flow_angle)
                        upstream_sin += math.sin(angle_rad)
                        upstream_cos += math.cos(angle_rad)
                        upstream_count += 1

            if upstream_count > 0:
                local_runout_angle = _normalize_direction_angle_numba(math.degrees(math.atan2(upstream_sin, upstream_cos)))
                if upstream_count > 1:
                    flags[i, 3] = 1
            elif fallback_count > 0:
                for rr in range(r_min, r_max):
                    for cc in range(c_min, c_max):
                        if not runout_mask[rr, cc] or not dinf_valid[rr, cc]:
                            continue
                        if rr == row and cc == col:
                            continue
                        distance_m = math.hypot(float(rr - row) * dy, float(cc - col) * dx)
                        if distance_m <= 0.0 or distance_m > runout_approach_window_m + 1.0e-9:
                            continue
                        flow_angle = _normalize_direction_angle_numba(math.degrees(float(dinf[rr, cc])))
                        target_angle = _direction_from_to_numba(rr, cc, row, col, dx, dy)
                        connection_diff = _angular_difference_deg_numba(flow_angle, target_angle)
                        if abs(connection_diff - fallback_best_diff) <= 5.0:
                            angle_rad = math.radians(flow_angle)
                            tie_sin += math.sin(angle_rad)
                            tie_cos += math.cos(angle_rad)
                            tie_count += 1
                if tie_count > 1:
                    local_runout_angle = _normalize_direction_angle_numba(math.degrees(math.atan2(tie_sin, tie_cos)))
                    flags[i, 3] = 1
                else:
                    local_runout_angle = fallback_best_angle
            else:
                flags[i, 1] = 1
                flags[i, 3] = 1
                continue

            channel_sum = 0.0
            channel_count = 0
            angle_rad = math.radians(_normalize_axial_angle_numba(local_stream_angle))
            unit_col = math.cos(angle_rad)
            unit_row = -math.sin(angle_rad)
            last_rr_1 = -999999
            last_cc_1 = -999999
            last_rr_2 = -999999
            last_cc_2 = -999999
            for sign in (-1, 1):
                for step in range(1, max_channel_steps + 1):
                    dcol = int(round(float(sign) * unit_col * float(step)))
                    drow = int(round(float(sign) * unit_row * float(step)))
                    if drow == 0 and dcol == 0:
                        continue
                    distance = math.hypot(float(drow) * dy, float(dcol) * dx)
                    if distance > channel_slope_window_m + 1.0e-9:
                        continue
                    rr = row + drow
                    cc = col + dcol
                    if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                        continue
                    if (rr == last_rr_1 and cc == last_cc_1) or (rr == last_rr_2 and cc == last_cc_2):
                        continue
                    if sign == -1:
                        last_rr_1 = rr
                        last_cc_1 = cc
                    else:
                        last_rr_2 = rr
                        last_cc_2 = cc
                    if slope_valid[rr, cc]:
                        channel_sum += float(slope[rr, cc])
                        channel_count += 1
            has_channel_slope = channel_count > 0
            local_channel_slope = -9999.0
            if has_channel_slope:
                local_channel_slope = channel_sum / float(channel_count)
            else:
                flags[i, 2] = 1

            local_inflow = _inflow_angle_deg_numba(local_runout_angle, local_stream_angle)
            local_class = _classify_candidate_numba(
                local_inflow,
                local_channel_slope,
                has_channel_slope,
                very_high_inflow_angle_deg,
                high_inflow_angle_deg,
                moderate_inflow_angle_deg,
                low_channel_gradient_deg,
            )
            local_score = _damming_score_numba(
                local_inflow,
                local_channel_slope,
                has_channel_slope,
                moderate_inflow_angle_deg,
                low_channel_gradient_deg,
                steep_channel_gradient_deg,
            )

            valid[i] = True
            inflow[i] = np.float32(local_inflow)
            stream_angle[i] = np.float32(local_stream_angle)
            runout_angle[i] = np.float32(local_runout_angle)
            if has_channel_slope:
                channel_slope[i] = np.float32(local_channel_slope)
            dam_class[i] = np.uint8(local_class)
            dam_score[i] = np.float32(local_score)

        return valid, inflow, stream_angle, runout_angle, channel_slope, dam_class, dam_score, flags, candidate_source


    @njit(cache=True)
    def _trace_height_above_stream_numba(
        dem: np.ndarray,
        dem_valid: np.ndarray,
        stream_mask: np.ndarray,
        receiver_row: np.ndarray,
        receiver_col: np.ndarray,
        float_nodata: float,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        rows, cols = dem.shape
        cell_count = rows * cols
        dem_flat = dem.reshape(cell_count)
        dem_valid_flat = dem_valid.reshape(cell_count)
        stream_flat = stream_mask.reshape(cell_count)
        receiver_row_flat = receiver_row.reshape(cell_count)
        receiver_col_flat = receiver_col.reshape(cell_count)
        height = np.full(cell_count, float_nodata, dtype=np.float32)
        associated_stream_elev = np.full(cell_count, np.nan, dtype=np.float32)
        indegree = np.zeros(cell_count, dtype=np.uint8)
        topological_order = np.empty(cell_count, dtype=np.int32)
        negative_height_count = 0

        # Stream cells are terminal roots, so their nominal receiver is ignored.
        for index in range(cell_count):
            if not dem_valid_flat[index] or stream_flat[index]:
                continue
            rr = int(receiver_row_flat[index])
            cc = int(receiver_col_flat[index])
            if 0 <= rr < rows and 0 <= cc < cols:
                receiver_index = rr * cols + cc
                if dem_valid_flat[receiver_index]:
                    indegree[receiver_index] += np.uint8(1)

        queue_tail = 0
        for index in range(cell_count):
            if dem_valid_flat[index] and indegree[index] == 0:
                topological_order[queue_tail] = np.int32(index)
                queue_tail += 1

        queue_head = 0
        while queue_head < queue_tail:
            index = int(topological_order[queue_head])
            queue_head += 1
            if stream_flat[index]:
                continue
            rr = int(receiver_row_flat[index])
            cc = int(receiver_col_flat[index])
            if 0 <= rr < rows and 0 <= cc < cols:
                receiver_index = rr * cols + cc
                if dem_valid_flat[receiver_index]:
                    indegree[receiver_index] -= np.uint8(1)
                    if indegree[receiver_index] == 0:
                        topological_order[queue_tail] = np.int32(receiver_index)
                        queue_tail += 1

        # Reverse topological order guarantees that each receiver is resolved
        # before its upstream source. Cells in receiver cycles never enter the
        # order and therefore remain NoData, matching the reference path walker.
        for order_index in range(queue_tail - 1, -1, -1):
            index = int(topological_order[order_index])
            if stream_flat[index]:
                terminal_elev = float(dem_flat[index])
            else:
                rr = int(receiver_row_flat[index])
                cc = int(receiver_col_flat[index])
                if not (0 <= rr < rows and 0 <= cc < cols):
                    continue
                receiver_index = rr * cols + cc
                terminal_elev = float(associated_stream_elev[receiver_index])
                if not math.isfinite(terminal_elev):
                    continue

            associated_stream_elev[index] = np.float32(terminal_elev)
            local_height = float(dem_flat[index]) - terminal_elev
            if local_height < 0.0:
                local_height = 0.0
                negative_height_count += 1
            height[index] = np.float32(local_height)

        return (
            height.reshape((rows, cols)),
            associated_stream_elev.reshape((rows, cols)),
            negative_height_count,
        )


else:
    _evaluate_candidate_cells_numba = None
    _trace_height_above_stream_numba = None


def load_stream_segments(path: str) -> list[StreamSegment]:
    try:
        from osgeo import ogr
    except Exception as exc:
        raise RuntimeError("stream_vector support requires osgeo/OGR, such as QGIS Python.") from exc

    dataset = ogr.Open(path)
    if dataset is None:
        raise RuntimeError(f"Could not open stream_vector: {path}")
    segments: list[StreamSegment] = []

    def add_line(points: list[tuple[float, float]]) -> None:
        for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
            if x1 == x2 and y1 == y2:
                continue
            angle = normalize_axial_angle(math.degrees(math.atan2(y2 - y1, x2 - x1)))
            segments.append(StreamSegment(float(x1), float(y1), float(x2), float(y2), angle))

    def collect_geometry(geometry: Any) -> None:
        if geometry is None:
            return
        name = geometry.GetGeometryName().upper()
        if name in {"LINESTRING", "LINEARRING"}:
            add_line([(geometry.GetX(i), geometry.GetY(i)) for i in range(geometry.GetPointCount())])
            return
        for index in range(geometry.GetGeometryCount()):
            collect_geometry(geometry.GetGeometryRef(index))

    for layer_index in range(dataset.GetLayerCount()):
        layer = dataset.GetLayerByIndex(layer_index)
        layer.ResetReading()
        for feature in layer:
            collect_geometry(feature.GetGeometryRef())

    if not segments:
        raise ValueError(f"stream_vector contains no line segments: {path}")
    return segments


def point_segment_distance_squared(x: float, y: float, segment: StreamSegment) -> float:
    vx = segment.x2 - segment.x1
    vy = segment.y2 - segment.y1
    wx = x - segment.x1
    wy = y - segment.y1
    denom = vx * vx + vy * vy
    if denom <= 0.0:
        return (x - segment.x1) ** 2 + (y - segment.y1) ** 2
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    px = segment.x1 + t * vx
    py = segment.y1 + t * vy
    return (x - px) ** 2 + (y - py) ** 2


def nearest_stream_vector_angle(x: float, y: float, segments: list[StreamSegment]) -> float | None:
    if not segments:
        return None
    best = min(segments, key=lambda segment: point_segment_distance_squared(x, y, segment))
    return best.angle_deg


def build_primary_dinf_receivers(
    dem: np.ndarray,
    dem_valid: np.ndarray,
    dinf: np.ndarray,
    dinf_valid: np.ndarray,
    dx: float,
    dy: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Map each valid cell to one deterministic primary downstream neighbour."""

    rows, cols = dem.shape
    receiver_row = np.full((rows, cols), -1, dtype=np.int32)
    receiver_col = np.full((rows, cols), -1, dtype=np.int32)
    best_rank = np.full((rows, cols), 3, dtype=np.int8)
    best_diff = np.full((rows, cols), np.inf, dtype=np.float32)
    best_distance = np.full((rows, cols), np.inf, dtype=np.float32)
    flow_angle = np.zeros((rows, cols), dtype=np.float32)
    valid_flow = dem_valid & dinf_valid
    flow_angle[valid_flow] = np.mod(np.degrees(dinf[valid_flow].astype(np.float32, copy=False)), 360.0)

    for dr in [-1, 0, 1]:
        for dc in [-1, 0, 1]:
            if dr == 0 and dc == 0:
                continue
            if dr >= 0:
                src_r = slice(0, rows - dr)
                nbr_r = slice(dr, rows)
                row_offset = dr
            else:
                src_r = slice(-dr, rows)
                nbr_r = slice(0, rows + dr)
                row_offset = dr
            if dc >= 0:
                src_c = slice(0, cols - dc)
                nbr_c = slice(dc, cols)
                col_offset = dc
            else:
                src_c = slice(-dc, cols)
                nbr_c = slice(0, cols + dc)
                col_offset = dc

            source_valid = dem_valid[src_r, src_c] & dinf_valid[src_r, src_c]
            neighbour_valid = dem_valid[nbr_r, nbr_c]
            valid = source_valid & neighbour_valid
            if not np.any(valid):
                continue

            neighbour_angle = direction_from_to(0, 0, dr, dc, dx, dy)
            raw_diff = np.abs(flow_angle[src_r, src_c] - np.float32(neighbour_angle))
            diff = np.minimum(raw_diff, 360.0 - raw_diff).astype(np.float32, copy=False)
            rank = (dem[nbr_r, nbr_c] > dem[src_r, src_c]).astype(np.int8, copy=False)
            distance = np.float32(math.hypot(float(dr) * dy, float(dc) * dx))

            rank_view = best_rank[src_r, src_c]
            diff_view = best_diff[src_r, src_c]
            distance_view = best_distance[src_r, src_c]
            better = valid & (
                (rank < rank_view)
                | ((rank == rank_view) & (diff < diff_view))
                | ((rank == rank_view) & (diff == diff_view) & (distance < distance_view))
            )
            if not np.any(better):
                continue

            rank_view[better] = rank[better]
            diff_view[better] = diff[better]
            distance_view[better] = distance
            receiver_row_view = receiver_row[src_r, src_c]
            receiver_col_view = receiver_col[src_r, src_c]
            source_rows, source_cols = np.where(better)
            receiver_row_view[better] = source_rows.astype(np.int32, copy=False) + int(src_r.start or 0) + row_offset
            receiver_col_view[better] = source_cols.astype(np.int32, copy=False) + int(src_c.start or 0) + col_offset
    return receiver_row, receiver_col


def trace_to_stream_and_compute_height_above_stream(
    dem: np.ndarray,
    dem_valid: np.ndarray,
    stream_mask: np.ndarray,
    receiver_row: np.ndarray,
    receiver_col: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Compute DEM elevation above the downstream stream terminal using memoized primary paths."""

    if _trace_height_above_stream_numba is not None:
        return _trace_height_above_stream_numba(
            dem,
            dem_valid,
            stream_mask,
            receiver_row,
            receiver_col,
            float(FLOAT_NODATA),
        )

    rows, cols = dem.shape
    height = np.full((rows, cols), FLOAT_NODATA, dtype=np.float32)
    associated_stream_elev = np.full((rows, cols), np.nan, dtype=np.float32)
    state = np.zeros((rows, cols), dtype=np.uint8)  # 0 unknown, 1 visiting, 2 resolved, 3 invalid

    stream_cells = dem_valid & stream_mask
    height[stream_cells] = 0.0
    associated_stream_elev[stream_cells] = dem[stream_cells].astype(np.float32, copy=False)
    state[stream_cells] = 2
    state[~dem_valid] = 3
    negative_height_count = 0

    for start_row in range(rows):
        for start_col in range(cols):
            if state[start_row, start_col] != 0:
                continue
            path: list[tuple[int, int]] = []
            row = start_row
            col = start_col
            terminal_elev: float | None = None
            valid_terminal = False

            while True:
                if not (0 <= row < rows and 0 <= col < cols) or not dem_valid[row, col]:
                    break
                cell_state = int(state[row, col])
                if cell_state == 2:
                    terminal_elev = float(associated_stream_elev[row, col])
                    valid_terminal = math.isfinite(terminal_elev)
                    break
                if cell_state == 3 or cell_state == 1:
                    break
                state[row, col] = 1
                path.append((row, col))
                rr = int(receiver_row[row, col])
                cc = int(receiver_col[row, col])
                if rr < 0 or cc < 0:
                    break
                row, col = rr, cc

            if valid_terminal and terminal_elev is not None:
                for rr, cc in path:
                    associated_stream_elev[rr, cc] = np.float32(terminal_elev)
                    local_height = float(dem[rr, cc]) - terminal_elev
                    if local_height < 0.0:
                        local_height = 0.0
                        negative_height_count += 1
                    height[rr, cc] = np.float32(local_height)
                    state[rr, cc] = 2
            else:
                for rr, cc in path:
                    state[rr, cc] = 3
    return height, associated_stream_elev, negative_height_count


def create_valley_floor_mask(
    height_above_stream: np.ndarray,
    stream_mask: np.ndarray,
    dem_valid: np.ndarray,
    threshold_m: float,
) -> np.ndarray:
    valid_height = height_above_stream != FLOAT_NODATA
    valley_floor = dem_valid & valid_height & (height_above_stream <= float(threshold_m))
    valley_floor |= dem_valid & stream_mask
    return valley_floor.astype(np.uint8)


def _cell_from_xy(transform: Any, x: float, y: float, shape: tuple[int, int]) -> tuple[int, int] | None:
    col_f, row_f = (~transform) * (float(x), float(y))
    row = int(math.floor(row_f))
    col = int(math.floor(col_f))
    if 0 <= row < shape[0] and 0 <= col < shape[1]:
        return row, col
    return None


def sample_perpendicular_valley_profile(
    valley_floor_mask: np.ndarray,
    transform: Any,
    row: int,
    col: int,
    stream_angle_deg: float,
    dx: float,
    dy: float,
    half_length_m: float,
    sample_spacing_m: float,
) -> ValleyProfileMeasurement:
    if not valley_floor_mask[row, col]:
        return ValleyProfileMeasurement(None, False, "center_not_valley_floor")
    spacing = float(sample_spacing_m) if sample_spacing_m > 0 else min(dx, dy)
    spacing = min(spacing, min(dx, dy))
    if spacing <= 0:
        raise ValueError("Profile sample spacing must be > 0 after automatic resolution selection.")
    center_x, center_y = transform_xy(transform, row, col, offset="center")
    perpendicular = math.radians(normalize_axial_angle(stream_angle_deg) + 90.0)
    unit_x = math.cos(perpendicular)
    unit_y = math.sin(perpendicular)
    shape = valley_floor_mask.shape
    censored = False
    extents: list[float] = []
    for sign in [-1.0, 1.0]:
        last_valid = 0.0
        distance = spacing
        side_censored = True
        while distance <= float(half_length_m) + 1e-9:
            x = float(center_x) + sign * unit_x * distance
            y = float(center_y) + sign * unit_y * distance
            cell = _cell_from_xy(transform, x, y, shape)
            if cell is None:
                side_censored = False
                break
            rr, cc = cell
            if not valley_floor_mask[rr, cc]:
                side_censored = False
                break
            last_valid = distance
            distance += spacing
        if side_censored and last_valid >= float(half_length_m) - spacing * 0.5:
            censored = True
        extents.append(last_valid)
    return ValleyProfileMeasurement(float(extents[0] + extents[1]), censored, "perpendicular_profile")


def stream_network_cells_within_distance(
    stream_mask: np.ndarray,
    start: tuple[int, int],
    radius_m: float,
    dx: float,
    dy: float,
) -> list[tuple[int, int, float]]:
    rows, cols = stream_mask.shape
    if not stream_mask[start]:
        return []
    offsets: list[tuple[int, int, float]] = []
    for dr in [-1, 0, 1]:
        for dc in [-1, 0, 1]:
            if dr == 0 and dc == 0:
                continue
            offsets.append((dr, dc, math.hypot(float(dr) * dy, float(dc) * dx)))
    distances: dict[tuple[int, int], float] = {start: 0.0}
    heap: list[tuple[float, int, int]] = [(0.0, start[0], start[1])]
    while heap:
        distance, row, col = heapq.heappop(heap)
        if distance > float(radius_m) + 1e-9:
            continue
        if distance > distances.get((row, col), math.inf) + 1e-9:
            continue
        for dr, dc, edge_cost in offsets:
            rr = row + dr
            cc = col + dc
            if not (0 <= rr < rows and 0 <= cc < cols) or not stream_mask[rr, cc]:
                continue
            new_distance = distance + edge_cost
            if new_distance > float(radius_m) + 1e-9:
                continue
            key = (rr, cc)
            if new_distance + 1e-9 < distances.get(key, math.inf):
                distances[key] = new_distance
                heapq.heappush(heap, (new_distance, rr, cc))
    return [(row, col, distance) for (row, col), distance in sorted(distances.items(), key=lambda item: item[1])]


def _select_profile_cells_by_spacing(
    network_cells: list[tuple[int, int, float]],
    spacing_m: float,
) -> list[tuple[int, int]]:
    selected: list[tuple[int, int]] = []
    last_distance = -math.inf
    for row, col, distance in network_cells:
        if not selected or distance - last_distance >= float(spacing_m) - 1e-9:
            selected.append((row, col))
            last_distance = distance
    return selected


def measure_corrected_valley_width(
    row: int,
    col: int,
    stream_mask: np.ndarray,
    valley_floor_mask: np.ndarray,
    transform: Any,
    dx: float,
    dy: float,
    params: Step6Params,
    stream_segments: list[StreamSegment] | None,
    raw_cache: dict[tuple[int, int], ValleyProfileMeasurement],
    raw_raster: np.ndarray,
) -> tuple[float | None, float | None, bool, int, str]:
    network_cells = stream_network_cells_within_distance(
        stream_mask,
        (row, col),
        params.valley_width_meander_window_radius_m,
        dx,
        dy,
    )
    profile_cells = _select_profile_cells_by_spacing(network_cells, params.valley_width_local_profile_spacing_m)
    if (row, col) not in profile_cells:
        profile_cells.insert(0, (row, col))

    valid_uncensored: list[float] = []
    censored_count = 0
    candidate_raw: float | None = None
    for rr, cc in profile_cells:
        key = (rr, cc)
        if key not in raw_cache:
            x, y = transform_xy(transform, rr, cc, offset="center")
            if stream_segments is not None:
                local_angle = nearest_stream_vector_angle(float(x), float(y), stream_segments)
            else:
                local_angle = estimate_stream_angle_from_raster(stream_mask, rr, cc, dx, dy)
            if local_angle is None:
                raw_cache[key] = ValleyProfileMeasurement(None, False, "missing_stream_orientation")
            else:
                raw_cache[key] = sample_perpendicular_valley_profile(
                    valley_floor_mask,
                    transform,
                    rr,
                    cc,
                    local_angle,
                    dx,
                    dy,
                    params.valley_width_profile_half_length_m,
                    params.valley_width_profile_sample_spacing_m,
                )
            if raw_cache[key].raw_width_m is not None:
                raw_raster[rr, cc] = np.float32(raw_cache[key].raw_width_m)
        measurement = raw_cache[key]
        if key == (row, col):
            candidate_raw = measurement.raw_width_m
        if measurement.raw_width_m is None:
            continue
        if measurement.censored:
            censored_count += 1
        else:
            valid_uncensored.append(float(measurement.raw_width_m))

    if valid_uncensored:
        return candidate_raw, float(min(valid_uncensored)), False, len(profile_cells), "network_window_minimum"
    if censored_count > 0:
        return candidate_raw, float(2.0 * params.valley_width_profile_half_length_m), True, len(profile_cells), "censored_lower_bound"
    return candidate_raw, None, False, len(profile_cells), "no_valid_profile"


def select_stream_profile_anchors(
    stream_mask: np.ndarray,
    spacing_m: float,
    dx: float,
    dy: float,
) -> list[tuple[int, int]]:
    """Select one representative stream-profile anchor per spacing-sized grid bin."""

    rows, cols = np.where(stream_mask)
    if len(rows) == 0:
        return []
    spacing = float(spacing_m)
    if spacing <= min(dx, dy) + 1e-9:
        return list(zip(rows.astype(int).tolist(), cols.astype(int).tolist()))

    row_stride = max(1, int(round(spacing / float(dy))))
    col_stride = max(1, int(round(spacing / float(dx))))
    bin_cols = int(math.ceil(stream_mask.shape[1] / float(col_stride)))
    linear_bins = (rows // row_stride) * bin_cols + (cols // col_stride)
    _unique_bins, first_indices = np.unique(linear_bins, return_index=True)
    selected_rows = rows[first_indices].astype(int)
    selected_cols = cols[first_indices].astype(int)
    return list(zip(selected_rows.tolist(), selected_cols.tolist()))


def measure_valley_width_at_anchors(
    anchor_cells: list[tuple[int, int]],
    stream_mask: np.ndarray,
    valley_floor_mask: np.ndarray,
    transform: Any,
    dx: float,
    dy: float,
    params: Step6Params,
    stream_segments: list[StreamSegment] | None,
    raw_raster: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Measure raw perpendicular valley width once at each stream-profile anchor."""

    anchor_xy: list[tuple[float, float]] = []
    raw_widths: list[float] = []
    censored_flags: list[bool] = []
    warnings: list[str] = []
    for row, col in anchor_cells:
        x, y = transform_xy(transform, row, col, offset="center")
        if stream_segments is not None:
            local_angle = nearest_stream_vector_angle(float(x), float(y), stream_segments)
        else:
            local_angle = estimate_stream_angle_from_raster(stream_mask, row, col, dx, dy)
        if local_angle is None:
            continue
        measurement = sample_perpendicular_valley_profile(
            valley_floor_mask,
            transform,
            row,
            col,
            local_angle,
            dx,
            dy,
            params.valley_width_profile_half_length_m,
            params.valley_width_profile_sample_spacing_m,
        )
        if measurement.raw_width_m is None:
            continue
        raw_raster[row, col] = np.float32(measurement.raw_width_m)
        anchor_xy.append((float(x), float(y)))
        raw_widths.append(float(measurement.raw_width_m))
        censored_flags.append(bool(measurement.censored))

    if anchor_cells and not raw_widths:
        warnings.append("No valid valley-width anchor profiles were measured.")
    return (
        np.asarray(anchor_xy, dtype=np.float64),
        np.asarray(raw_widths, dtype=np.float64),
        np.asarray(censored_flags, dtype=bool),
        warnings,
    )


def assign_anchor_corrected_valley_widths(
    records: list[CandidateRecord],
    anchor_xy: np.ndarray,
    raw_widths: np.ndarray,
    censored_flags: np.ndarray,
    params: Step6Params,
) -> list[str]:
    """Assign moving-window minimum anchor widths to candidate records."""

    warnings: list[str] = []
    if not records:
        return warnings
    if cKDTree is None:
        raise RuntimeError(
            "scipy.spatial.cKDTree is required for optimized valley-width anchor aggregation."
        )
    if len(anchor_xy) == 0:
        warnings.append("No valley-width anchors were available for candidate width assignment.")
        return warnings

    tree = cKDTree(anchor_xy)
    candidate_xy = np.asarray([(record.x, record.y) for record in records], dtype=np.float64)
    max_censored_width = float(2.0 * params.valley_width_profile_half_length_m)
    query_workers = int(params.numba_threads) if params.numba_threads > 0 else -1
    chunk_size = int(params.valley_width_assignment_chunk_size)
    for chunk_start in range(0, len(records), chunk_size):
        chunk_stop = min(len(records), chunk_start + chunk_size)
        chunk_xy = candidate_xy[chunk_start:chunk_stop]
        try:
            neighborhoods = tree.query_ball_point(
                chunk_xy,
                r=float(params.valley_width_meander_window_radius_m),
                workers=query_workers,
            )
        except TypeError:  # pragma: no cover - compatibility with older SciPy.
            neighborhoods = tree.query_ball_point(
                chunk_xy,
                r=float(params.valley_width_meander_window_radius_m),
            )
        for local_index, indices in enumerate(neighborhoods):
            record = records[chunk_start + local_index]
            record.valley_width_profile_count = int(len(indices))
            record.valley_width_method = "anchor_window_minimum"
            if not indices:
                record.raw_valley_width_m = None
                record.valley_floor_width_m = None
                record.valley_width_censored = False
                record.valley_width_method = "no_anchor_in_window"
                continue

            local_indices = np.asarray(indices, dtype=np.intp)
            local_raw = raw_widths[local_indices]
            local_censored = censored_flags[local_indices]
            distances_sq = np.sum((anchor_xy[local_indices] - chunk_xy[local_index]) ** 2, axis=1)
            nearest_index = int(local_indices[int(np.argmin(distances_sq))])
            record.raw_valley_width_m = float(raw_widths[nearest_index])

            valid_uncensored = local_raw[~local_censored]
            if len(valid_uncensored) > 0:
                record.valley_floor_width_m = float(np.min(valid_uncensored))
                record.valley_width_censored = False
                continue
            if np.any(local_censored):
                record.valley_floor_width_m = max_censored_width
                record.valley_width_censored = True
                record.valley_width_method = "anchor_window_censored_lower_bound"
                continue
            record.valley_floor_width_m = None
            record.valley_width_censored = False
            record.valley_width_method = "no_valid_anchor_profile"
    return warnings


def classify_reference_volume_domain(
    valley_width_m: float | None,
    valley_width_censored: bool,
    thresholds: ReferenceVolumeThresholds,
) -> tuple[int, str]:
    if valley_width_m is None or not math.isfinite(float(valley_width_m)):
        return 0, "indeterminate_missing_valley_width"
    width = float(valley_width_m)
    if valley_width_censored:
        if width > thresholds.possible_width_m:
            return 1, "nonformation_confirmed_from_censored_lower_bound"
        return 0, "indeterminate_censored_valley_width"
    if width <= thresholds.formation_width_m:
        return 3, "formation_domain_condition_met"
    if width <= thresholds.possible_width_m:
        return 2, "possible_or_uncertain_obstruction_domain"
    return 1, "nonformation_domain_complete_blockage_unlikely"


def combine_reference_volume_class(
    geometry_baseline_class: int,
    geometry_baseline_score: float,
    reference_domain_class: int,
) -> tuple[int, float | None]:
    base_class = int(geometry_baseline_class)
    base_score = float(geometry_baseline_score)
    domain = int(reference_domain_class)
    if base_class == 5:
        return 5, 0.0
    if domain == 0:
        return 0, None
    if base_class == 1:
        return 1, 0.0
    if domain == 1:
        return 1, 0.0
    if domain == 2:
        if base_class == 2:
            return 2, base_score * 0.5
        return 3, base_score * 0.5
    if domain == 3:
        return base_class, base_score
    return 0, None


def evaluate_damming_potential(
    arrays: CoreArrays,
    params: Step6Params,
    transform: Any,
    dx: float,
    dy: float,
    *,
    stream_segments: list[StreamSegment] | None = None,
) -> Step6Result:
    eval_start = time.perf_counter()
    phase_start = eval_start
    timings: dict[str, float] = {}

    def mark_phase(name: str) -> None:
        nonlocal phase_start
        now = time.perf_counter()
        timings[name] = round(now - phase_start, 3)
        phase_start = now

    shape = arrays.dem.shape
    if any(source.shape != shape for source in [arrays.dinf, arrays.runout_mask, arrays.stream_mask, arrays.slope]):
        raise ValueError("Core arrays must have identical shapes.")
    if params.numba_threads > 0 and set_num_threads is not None:
        try:
            set_num_threads(params.numba_threads)
        except ValueError as exc:
            raise ValueError(f"numba_threads is invalid for this environment: {exc}") from exc
    active_numba_threads = int(get_num_threads()) if get_num_threads is not None else 1

    dem_valid = valid_mask(arrays.dem, arrays.dem_nodata)
    dinf_valid = valid_mask(arrays.dinf, arrays.dinf_nodata)
    runout_valid = valid_mask(arrays.runout_mask, arrays.runout_nodata)
    stream_valid = valid_mask(arrays.stream_mask, arrays.stream_nodata)
    slope_valid = valid_mask(arrays.slope, arrays.slope_nodata)
    runout = runout_valid & (arrays.runout_mask > 0)
    stream = stream_valid & (arrays.stream_mask > 0)
    terrain_valid = dem_valid & dinf_valid & slope_valid
    mark_phase("prepare_masks")

    height_above_stream = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    valley_floor_mask = np.zeros(shape, dtype=np.uint8)
    raw_valley_width = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    corrected_valley_width = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    reference_volume_domain_class = np.zeros(shape, dtype=np.uint8)
    reference_volume_combined_class = np.zeros(shape, dtype=np.uint8)
    reference_volume_combined_score = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    reference_thresholds: ReferenceVolumeThresholds | None = None
    if params.reference_volume_enabled:
        reference_thresholds = calculate_reference_volume_thresholds(
            params.reference_area_m2,
            params.area_volume_coefficient,
            params.area_volume_exponent,
            params.delivery_fraction,
            params.reference_delivered_deposit_volume_m3,
        )
    valley_width_summary: dict[str, Any] = {
        "valley_width_enabled": bool(params.valley_width_enabled),
        "valley_width_method": "disabled",
        "valley_width_elevation_threshold_m": float(params.valley_width_elevation_threshold_m),
        "valley_width_profile_half_length_m": float(params.valley_width_profile_half_length_m),
        "valley_width_meander_window_radius_m": float(params.valley_width_meander_window_radius_m),
        "stream_profile_anchor_count": 0,
        "valid_stream_profile_anchor_count": 0,
        "censored_stream_profile_anchor_count": 0,
        "valid_raw_valley_width_count": 0,
        "valid_corrected_valley_width_count": 0,
        "censored_valley_width_count": 0,
        "missing_valley_width_count": 0,
        "valley_width_narrow_reference_m": None,
        "valley_width_wide_reference_m": None,
        "valley_width_min_m": None,
        "valley_width_median_m": None,
        "valley_width_max_m": None,
    }
    reference_volume_summary: dict[str, Any] = {
        "enabled": bool(params.reference_volume_enabled),
        "assessment_name": "reference-volume landslide-damming potential",
        "volume_interpretation": "reference delivered-deposit volume at the candidate stream reach",
        "reference_area_m2": float(params.reference_area_m2),
        "area_volume_equation": (
            f"Vsource = {params.area_volume_coefficient:g} * Aref^{params.area_volume_exponent:g}"
        ),
        "source_volume_m3": None,
        "delivery_fraction": float(params.delivery_fraction),
        "delivered_volume_unrounded_m3": None,
        "reference_delivered_deposit_volume_m3": float(params.reference_delivered_deposit_volume_m3),
        "formation_width_m": None,
        "possible_width_m": None,
        "domain_class_distribution": {},
        "combined_class_distribution": {},
        "evaluated_candidate_count": 0,
        "indeterminate_censored_candidate_count": 0,
    }
    if reference_thresholds is not None:
        reference_volume_summary.update(
            {
                "source_volume_m3": float(reference_thresholds.source_volume_m3),
                "delivered_volume_unrounded_m3": float(reference_thresholds.delivered_volume_unrounded_m3),
                "formation_width_m": float(reference_thresholds.formation_width_m),
                "possible_width_m": float(reference_thresholds.possible_width_m),
                "formation_width_m_rounded": round(reference_thresholds.formation_width_m, 2),
                "possible_width_m_rounded": round(reference_thresholds.possible_width_m, 2),
            }
        )
    negative_hans_values_clamped = 0
    if params.valley_width_enabled:
        receiver_row, receiver_col = build_primary_dinf_receivers(arrays.dem, dem_valid, arrays.dinf, dinf_valid, dx, dy)
        mark_phase("build_primary_dinf_receivers")
        (
            height_above_stream,
            _associated_stream_elev,
            negative_hans_values_clamped,
        ) = trace_to_stream_and_compute_height_above_stream(
            arrays.dem,
            dem_valid,
            stream,
            receiver_row,
            receiver_col,
        )
        mark_phase("trace_height_above_stream")
        valley_floor_mask = create_valley_floor_mask(
            height_above_stream,
            stream,
            dem_valid,
            params.valley_width_elevation_threshold_m,
        )
        mark_phase("create_valley_floor_mask")
        valley_width_summary["valley_width_method"] = (
            "threshold_elevation_dinf_primary_path_anchor_profile_window_minimum"
        )

    direct_candidates = runout & stream & terrain_valid
    candidate_mask = direct_candidates.copy()
    candidate_mode = "direct_overlap" if np.any(candidate_mask) else "none"
    mark_phase("create_candidate_mask")

    stream_intersection_mask = np.zeros(shape, dtype=np.uint8)
    stream_intersection_mask[candidate_mask] = 1
    inflow = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    stream_angle = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    runout_angle = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    channel_slope_out = np.full(shape, FLOAT_NODATA, dtype=np.float32)
    dam_class = np.zeros(shape, dtype=np.uint8)
    dam_score = np.full(shape, FLOAT_NODATA, dtype=np.float32)

    warnings: list[str] = []
    if negative_hans_values_clamped > 0:
        warnings.append(
            f"Clamped {negative_hans_values_clamped} negative height-above-stream values to 0 m. "
            "These can occur where the deterministic primary D-infinity receiver uses a small uphill fallback."
        )
    counters = {
        "candidate_cells": int(np.count_nonzero(candidate_mask)),
        "direct_overlap_candidate_cells": int(np.count_nonzero(candidate_mask & direct_candidates)),
        "buffer_derived_candidate_cells": int(np.count_nonzero(candidate_mask & ~direct_candidates)),
        "missing_stream_angle_cells": 0,
        "missing_runout_angle_cells": 0,
        "missing_channel_slope_cells": 0,
        "multi_sample_runout_approach_cells": 0,
    }

    records: list[CandidateRecord] = []
    rows, cols = np.where(candidate_mask)
    if stream_segments is None and _evaluate_candidate_cells_numba is not None and len(rows) > 0:
        (
            valid_records,
            inflow_values,
            stream_angle_values,
            runout_angle_values,
            channel_slope_values,
            class_values,
            score_values,
            candidate_flags,
            candidate_source_values,
        ) = _evaluate_candidate_cells_numba(
            rows.astype(np.int32, copy=False),
            cols.astype(np.int32, copy=False),
            runout,
            direct_candidates,
            stream,
            arrays.dinf.astype(np.float32, copy=False),
            dinf_valid,
            arrays.slope.astype(np.float32, copy=False),
            slope_valid,
            float(dx),
            float(dy),
            float(params.runout_approach_window_m),
            float(params.channel_slope_window_m),
            float(params.very_high_inflow_angle_deg),
            float(params.high_inflow_angle_deg),
            float(params.moderate_inflow_angle_deg),
            float(params.low_channel_gradient_deg),
            float(params.steep_channel_gradient_deg),
        )
        counters["missing_stream_angle_cells"] += int(np.count_nonzero(candidate_flags[:, 0]))
        counters["missing_runout_angle_cells"] += int(np.count_nonzero(candidate_flags[:, 1]))
        counters["missing_channel_slope_cells"] += int(np.count_nonzero(candidate_flags[:, 2]))
        counters["multi_sample_runout_approach_cells"] += int(np.count_nonzero(candidate_flags[:, 3]))
        for index in np.where(valid_records)[0].tolist():
            row = int(rows[index])
            col = int(cols[index])
            local_inflow = float(inflow_values[index])
            local_stream_angle = float(stream_angle_values[index])
            local_runout_angle = float(runout_angle_values[index])
            local_channel_slope = None
            if channel_slope_values[index] != FLOAT_NODATA:
                local_channel_slope = float(channel_slope_values[index])
            local_class = int(class_values[index])
            local_score = float(score_values[index])
            _class_check, interpretation = classify_candidate(local_inflow, local_channel_slope, params)
            x, y = transform_xy(transform, row, col, offset="center")

            inflow[row, col] = np.float32(local_inflow)
            stream_angle[row, col] = np.float32(local_stream_angle)
            runout_angle[row, col] = np.float32(local_runout_angle)
            if local_channel_slope is not None:
                channel_slope_out[row, col] = np.float32(local_channel_slope)
            dam_class[row, col] = np.uint8(local_class)
            dam_score[row, col] = np.float32(local_score)
            records.append(
                CandidateRecord(
                    row=row,
                    col=col,
                    x=float(x),
                    y=float(y),
                    inflow_angle_deg=local_inflow,
                    stream_angle_deg=local_stream_angle,
                    runout_approach_angle_deg=local_runout_angle,
                    channel_slope_deg=local_channel_slope,
                    baseline_damming_class=local_class,
                    geometry_baseline_class=local_class,
                    geometry_baseline_score=local_score,
                    damming_class=local_class,
                    damming_score=local_score,
                    interpretation=interpretation,
                    candidate_source="direct_overlap" if candidate_source_values[index] == 1 else "buffer_derived",
                )
            )
    else:
        for row, col in zip(rows.tolist(), cols.tolist()):
            x, y = transform_xy(transform, row, col, offset="center")
            if stream_segments is not None:
                local_stream_angle = nearest_stream_vector_angle(float(x), float(y), stream_segments)
            else:
                local_stream_angle = estimate_stream_angle_from_raster(stream, row, col, dx, dy)
            if local_stream_angle is None:
                counters["missing_stream_angle_cells"] += 1
                continue

            local_runout_angle, used_multiple_samples, _neighbor_count = estimate_runout_approach_angle(
                runout,
                arrays.dinf,
                dinf_valid,
                row,
                col,
                dx,
                dy,
                window_distance_m=params.runout_approach_window_m,
            )
            if used_multiple_samples:
                counters["multi_sample_runout_approach_cells"] += 1
            if local_runout_angle is None:
                counters["missing_runout_angle_cells"] += 1
                continue

            local_channel_slope = estimate_mean_channel_slope_from_raster(
                arrays.slope,
                slope_valid,
                row,
                col,
                local_stream_angle,
                dx,
                dy,
                params.channel_slope_window_m,
            )
            if local_channel_slope is None or not math.isfinite(float(local_channel_slope)):
                counters["missing_channel_slope_cells"] += 1
                local_channel_slope = None

            local_inflow = inflow_angle_deg(local_runout_angle, local_stream_angle)
            local_class, interpretation = classify_candidate(local_inflow, local_channel_slope, params)
            local_score = damming_score(local_inflow, local_channel_slope, params)

            inflow[row, col] = np.float32(local_inflow)
            stream_angle[row, col] = np.float32(normalize_axial_angle(local_stream_angle))
            runout_angle[row, col] = np.float32(normalize_direction_angle(local_runout_angle))
            if local_channel_slope is not None:
                channel_slope_out[row, col] = np.float32(local_channel_slope)
            dam_class[row, col] = np.uint8(local_class)
            dam_score[row, col] = np.float32(local_score)
            records.append(
                CandidateRecord(
                    row=row,
                    col=col,
                    x=float(x),
                    y=float(y),
                    inflow_angle_deg=float(local_inflow),
                    stream_angle_deg=float(normalize_axial_angle(local_stream_angle)),
                    runout_approach_angle_deg=float(normalize_direction_angle(local_runout_angle)),
                    channel_slope_deg=None if local_channel_slope is None else float(local_channel_slope),
                    baseline_damming_class=int(local_class),
                    geometry_baseline_class=int(local_class),
                    geometry_baseline_score=float(local_score),
                    damming_class=int(local_class),
                    damming_score=float(local_score),
                    interpretation=interpretation,
                    candidate_source="direct_overlap" if direct_candidates[row, col] else "buffer_derived",
                )
            )
    mark_phase("evaluate_candidate_cells")

    if params.valley_width_enabled and records:
        anchor_cells = select_stream_profile_anchors(
            stream,
            params.valley_width_local_profile_spacing_m,
            dx,
            dy,
        )
        mark_phase("select_stream_profile_anchors")
        anchor_xy, anchor_raw_widths, anchor_censored_flags, anchor_warnings = measure_valley_width_at_anchors(
            anchor_cells,
            stream,
            valley_floor_mask.astype(bool),
            transform,
            dx,
            dy,
            params,
            stream_segments,
            raw_valley_width,
        )
        warnings.extend(anchor_warnings)
        mark_phase("measure_anchor_valley_widths")
        assignment_warnings = assign_anchor_corrected_valley_widths(
            records,
            anchor_xy,
            anchor_raw_widths,
            anchor_censored_flags,
            params,
        )
        warnings.extend(assignment_warnings)
        for record in records:
            if record.valley_floor_width_m is not None:
                corrected_valley_width[record.row, record.col] = np.float32(record.valley_floor_width_m)
        mark_phase("assign_candidate_valley_widths")

        valley_width_summary["valley_width_narrow_reference_m"] = None
        valley_width_summary["valley_width_wide_reference_m"] = None
        corrected_values = np.asarray(
            [
                record.valley_floor_width_m
                for record in records
                if record.valley_floor_width_m is not None and not record.valley_width_censored
            ],
            dtype=np.float64,
        )
        valley_width_summary["valid_raw_valley_width_count"] = int(
            sum(record.raw_valley_width_m is not None for record in records)
        )
        valley_width_summary["stream_profile_anchor_count"] = int(len(anchor_cells))
        valley_width_summary["valid_stream_profile_anchor_count"] = int(len(anchor_raw_widths))
        valley_width_summary["censored_stream_profile_anchor_count"] = int(np.count_nonzero(anchor_censored_flags))
        valley_width_summary["valid_corrected_valley_width_count"] = int(len(corrected_values))
        valley_width_summary["censored_valley_width_count"] = int(sum(record.valley_width_censored for record in records))
        valley_width_summary["missing_valley_width_count"] = int(
            sum(record.valley_floor_width_m is None for record in records)
        )
        if len(corrected_values) > 0:
            valley_width_summary["valley_width_min_m"] = float(np.min(corrected_values))
            valley_width_summary["valley_width_median_m"] = float(np.median(corrected_values))
            valley_width_summary["valley_width_max_m"] = float(np.max(corrected_values))

        mark_phase("finalize_valley_width_summary")

    candidate_dam_class = dam_class.copy()
    combined_candidate_class = reference_volume_combined_class.copy()
    if params.reference_volume_enabled and reference_thresholds is not None and records:
        indeterminate_censored = 0
        evaluated_reference = 0
        for record in records:
            domain_class, domain_status = classify_reference_volume_domain(
                record.valley_floor_width_m,
                record.valley_width_censored,
                reference_thresholds,
            )
            combined_class, combined_score = combine_reference_volume_class(
                record.geometry_baseline_class,
                record.geometry_baseline_score,
                domain_class,
            )
            record.reference_volume_domain_class = int(domain_class)
            record.reference_volume_domain_status = domain_status
            record.reference_delivered_deposit_volume_m3 = reference_thresholds.reference_delivered_volume_m3
            record.reference_volume_formation_width_m = reference_thresholds.formation_width_m
            record.reference_volume_possible_width_m = reference_thresholds.possible_width_m
            record.reference_volume_combined_class = int(combined_class)
            record.reference_volume_combined_score = combined_score
            reference_volume_domain_class[record.row, record.col] = np.uint8(domain_class)
            reference_volume_combined_class[record.row, record.col] = np.uint8(combined_class)
            if combined_score is not None:
                reference_volume_combined_score[record.row, record.col] = np.float32(combined_score)
            if domain_class > 0:
                evaluated_reference += 1
            if domain_class == 0 and record.valley_width_censored:
                indeterminate_censored += 1
        combined_candidate_class = reference_volume_combined_class.copy()
        reference_volume_summary["evaluated_candidate_count"] = int(evaluated_reference)
        reference_volume_summary["indeterminate_censored_candidate_count"] = int(indeterminate_censored)
        reference_volume_summary["domain_class_distribution"] = {
            str(class_id): int(np.count_nonzero(reference_volume_domain_class == class_id))
            for class_id in [1, 2, 3]
            if int(np.count_nonzero(reference_volume_domain_class == class_id)) > 0
        }
        reference_volume_summary["combined_class_distribution"] = {
            str(class_id): int(np.count_nonzero(reference_volume_combined_class == class_id))
            for class_id in [1, 2, 3, 4, 5]
            if int(np.count_nonzero(reference_volume_combined_class == class_id)) > 0
        }
        mark_phase("reference_volume_assessment")

    buffered_dam_class = candidate_dam_class.copy()
    buffer_specs = [
        (1, params.low_buffer_m),
        (2, params.moderate_buffer_m),
        (3, params.high_buffer_m),
        (4, params.very_high_buffer_m),
    ]
    for class_id, buffer_m in buffer_specs:
        if buffer_m <= 0:
            continue
        expanded = dilate_mask_by_distance(candidate_dam_class == class_id, buffer_m, dx, dy)
        # Class 5 remains an unbuffered debris-flow-connectivity class.
        buffered_dam_class[expanded & (candidate_dam_class != 5)] = np.uint8(class_id)
    dam_class = buffered_dam_class
    if params.reference_volume_enabled:
        buffered_combined_class = combined_candidate_class.copy()
        for class_id, buffer_m in buffer_specs:
            if buffer_m <= 0:
                continue
            expanded = dilate_mask_by_distance(combined_candidate_class == class_id, buffer_m, dx, dy)
            buffered_combined_class[expanded & (combined_candidate_class != 5)] = np.uint8(class_id)
        reference_volume_combined_class = buffered_combined_class
        reference_volume_summary["combined_class_distribution"] = {
            str(class_id): int(np.count_nonzero(reference_volume_combined_class == class_id))
            for class_id in [1, 2, 3, 4, 5]
            if int(np.count_nonzero(reference_volume_combined_class == class_id)) > 0
        }
    mark_phase("buffer_damming_classes")

    evaluated_mask = dam_class > 0
    class_distribution = {
        str(class_id): int(np.count_nonzero(dam_class == class_id))
        for class_id in [1, 2, 3, 4, 5]
        if int(np.count_nonzero(dam_class == class_id)) > 0
    }
    if counters["missing_channel_slope_cells"] > 0:
        warnings.append(
            f"Channel slope was unavailable or invalid for {counters['missing_channel_slope_cells']} evaluated candidate cells; "
            "angle-only classification was used for those cells."
        )
    if counters["missing_stream_angle_cells"] > 0:
        warnings.append(f"Skipped {counters['missing_stream_angle_cells']} candidates without estimable stream direction.")
    if counters["missing_runout_angle_cells"] > 0:
        warnings.append(f"Skipped {counters['missing_runout_angle_cells']} candidates without estimable runout approach direction.")

    summary = {
        "candidate_mode": candidate_mode,
        "runout_approach_window_m": float(params.runout_approach_window_m),
        "parallel_runtime": {
            "numba_available": bool(njit is not None),
            "configured_numba_threads": int(params.numba_threads),
            "active_numba_threads": active_numba_threads,
            "valley_width_assignment_chunk_size": int(params.valley_width_assignment_chunk_size),
        },
        "buffer_distances_m": {
            "low": float(params.low_buffer_m),
            "moderate": float(params.moderate_buffer_m),
            "high": float(params.high_buffer_m),
            "very_high": float(params.very_high_buffer_m),
            "debris_flow_connectivity_favored": 0.0,
        },
        "counts": {
            **counters,
            "evaluated_candidate_cells": int(np.count_nonzero(candidate_dam_class > 0)),
            "buffered_class_cells": int(np.count_nonzero(evaluated_mask)),
        },
        "candidate_class_distribution": {
            str(class_id): int(np.count_nonzero(candidate_dam_class == class_id))
            for class_id in [1, 2, 3, 4, 5]
            if int(np.count_nonzero(candidate_dam_class == class_id)) > 0
        },
        "class_distribution": class_distribution,
        "valley_width": valley_width_summary,
        "negative_height_above_stream_values_clamped": int(negative_hans_values_clamped),
        "reference_volume_assessment": reference_volume_summary,
        "runtime_seconds": {
            **timings,
            "evaluate_total": round(time.perf_counter() - eval_start, 3),
        },
        "warnings": warnings,
    }
    return Step6Result(
        stream_intersection_mask=stream_intersection_mask,
        inflow_angle=inflow,
        stream_angle=stream_angle,
        runout_approach_angle=runout_angle,
        channel_slope=channel_slope_out,
        candidate_damming_class=candidate_dam_class,
        damming_class=dam_class,
        damming_score=dam_score,
        height_above_stream=height_above_stream,
        valley_floor_mask=valley_floor_mask,
        raw_valley_width=raw_valley_width,
        corrected_valley_width=corrected_valley_width,
        reference_volume_domain_class=reference_volume_domain_class,
        reference_volume_combined_class=reference_volume_combined_class,
        reference_volume_combined_score=reference_volume_combined_score,
        records=records,
        summary=summary,
    )


def ensure_output_allowed(path: str, overwrite: bool) -> None:
    if not path:
        return
    output_path = Path(path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Pass --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)


def write_geotiff(path: str, reference: RasterGrid, array: np.ndarray, dtype: str, nodata: float | int) -> None:
    profile = reference.profile.copy()
    profile.update(
        driver="GTiff",
        count=1,
        dtype=dtype,
        nodata=nodata,
        compress="lzw",
        width=reference.width,
        height=reference.height,
        transform=reference.transform,
        crs=reference.crs,
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype, copy=False), 1)


def write_candidate_points(path: str, records: list[CandidateRecord], reference: RasterGrid, overwrite: bool) -> None:
    if not path:
        return
    try:
        from osgeo import ogr, osr
    except Exception as exc:
        raise RuntimeError("output_candidate_points requires osgeo/OGR, such as QGIS Python.") from exc

    output_path = Path(path)
    driver_name = "GPKG" if output_path.suffix.lower() == ".gpkg" else "ESRI Shapefile"
    driver = ogr.GetDriverByName(driver_name)
    if driver is None:
        raise RuntimeError(f"OGR driver is unavailable: {driver_name}")
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"Candidate-point output already exists: {output_path}. Pass --overwrite to replace it.")
        driver.DeleteDataSource(str(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    datasource = driver.CreateDataSource(str(output_path))
    if datasource is None:
        raise RuntimeError(f"Could not create candidate-point output: {output_path}")
    spatial_ref = None
    if reference.crs is not None:
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(reference.crs.to_wkt())
    layer_name = output_path.stem
    layer = datasource.CreateLayer(layer_name, spatial_ref, ogr.wkbPoint)
    if layer is None:
        raise RuntimeError(f"Could not create candidate-point layer: {output_path}")

    shapefile_field_names = {
        "inflow_angle_deg": "inflow_deg",
        "stream_angle_deg": "strm_deg",
        "runout_approach_angle_deg": "run_deg",
        "channel_slope_deg": "chn_slp",
        "baseline_damming_class": "base_cls",
        "geom_base_cls": "geom_cls",
        "geom_base_scr": "geom_scr",
        "damming_class": "dam_cls",
        "damming_score": "dam_scr",
        "raw_valley_width_m": "raw_w_m",
        "valley_floor_width_m": "val_w_m",
        "valley_width_censored": "val_cens",
        "valley_width_profile_count": "val_nprof",
        "valley_width_method": "val_meth",
        "vref_domain": "vref_dom",
        "vref_status": "vref_stat",
        "vref_comb_cls": "vref_cls",
        "vref_comb_scr": "vref_scr",
        "interpretation": "interp",
        "candidate_source": "cand_src",
    }

    def out_field(name: str) -> str:
        if driver_name == "ESRI Shapefile":
            return shapefile_field_names.get(name, name[:10])
        return name

    fields = [
        ("row", ogr.OFTInteger),
        ("col", ogr.OFTInteger),
        ("x", ogr.OFTReal),
        ("y", ogr.OFTReal),
        ("inflow_angle_deg", ogr.OFTReal),
        ("stream_angle_deg", ogr.OFTReal),
        ("runout_approach_angle_deg", ogr.OFTReal),
        ("channel_slope_deg", ogr.OFTReal),
        ("baseline_damming_class", ogr.OFTInteger),
        ("geom_base_cls", ogr.OFTInteger),
        ("geom_base_scr", ogr.OFTReal),
        ("damming_class", ogr.OFTInteger),
        ("damming_score", ogr.OFTReal),
        ("raw_valley_width_m", ogr.OFTReal),
        ("valley_floor_width_m", ogr.OFTReal),
        ("valley_width_censored", ogr.OFTInteger),
        ("valley_width_profile_count", ogr.OFTInteger),
        ("valley_width_method", ogr.OFTString),
        ("vref_m3", ogr.OFTReal),
        ("w_form_m", ogr.OFTReal),
        ("w_poss_m", ogr.OFTReal),
        ("vref_domain", ogr.OFTInteger),
        ("vref_status", ogr.OFTString),
        ("vref_comb_cls", ogr.OFTInteger),
        ("vref_comb_scr", ogr.OFTReal),
        ("interpretation", ogr.OFTString),
        ("candidate_source", ogr.OFTString),
    ]
    for name, field_type in fields:
        field = ogr.FieldDefn(out_field(name), field_type)
        if field_type == ogr.OFTString:
            field.SetWidth(80)
        layer.CreateField(field)

    definition = layer.GetLayerDefn()
    for record in records:
        feature = ogr.Feature(definition)
        feature.SetField("row", int(record.row))
        feature.SetField("col", int(record.col))
        feature.SetField("x", float(record.x))
        feature.SetField("y", float(record.y))
        feature.SetField(out_field("inflow_angle_deg"), float(record.inflow_angle_deg))
        feature.SetField(out_field("stream_angle_deg"), float(record.stream_angle_deg))
        feature.SetField(out_field("runout_approach_angle_deg"), float(record.runout_approach_angle_deg))
        if record.channel_slope_deg is not None:
            feature.SetField(out_field("channel_slope_deg"), float(record.channel_slope_deg))
        feature.SetField(out_field("baseline_damming_class"), int(record.baseline_damming_class))
        feature.SetField(out_field("geom_base_cls"), int(record.geometry_baseline_class))
        feature.SetField(out_field("geom_base_scr"), float(record.geometry_baseline_score))
        feature.SetField(out_field("damming_class"), int(record.damming_class))
        feature.SetField(out_field("damming_score"), float(record.damming_score))
        if record.raw_valley_width_m is not None:
            feature.SetField(out_field("raw_valley_width_m"), float(record.raw_valley_width_m))
        if record.valley_floor_width_m is not None:
            feature.SetField(out_field("valley_floor_width_m"), float(record.valley_floor_width_m))
        feature.SetField(out_field("valley_width_censored"), int(record.valley_width_censored))
        feature.SetField(out_field("valley_width_profile_count"), int(record.valley_width_profile_count))
        feature.SetField(out_field("valley_width_method"), record.valley_width_method)
        if record.reference_delivered_deposit_volume_m3 is not None:
            feature.SetField("vref_m3", float(record.reference_delivered_deposit_volume_m3))
        if record.reference_volume_formation_width_m is not None:
            feature.SetField("w_form_m", float(record.reference_volume_formation_width_m))
        if record.reference_volume_possible_width_m is not None:
            feature.SetField("w_poss_m", float(record.reference_volume_possible_width_m))
        feature.SetField(out_field("vref_domain"), int(record.reference_volume_domain_class))
        feature.SetField(out_field("vref_status"), record.reference_volume_domain_status)
        feature.SetField(out_field("vref_comb_cls"), int(record.reference_volume_combined_class))
        if record.reference_volume_combined_score is not None:
            feature.SetField(out_field("vref_comb_scr"), float(record.reference_volume_combined_score))
        feature.SetField(out_field("interpretation"), record.interpretation)
        feature.SetField(out_field("candidate_source"), record.candidate_source)
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint(float(record.x), float(record.y))
        feature.SetGeometry(point)
        layer.CreateFeature(feature)
        feature = None
    datasource = None


SHAPEFILE_EXTENSIONS = (
    ".shp",
    ".shx",
    ".dbf",
    ".prj",
    ".cpg",
    ".qix",
    ".sbn",
    ".sbx",
)


def prepare_staged_output_bundle(params: Step6Params) -> StagedOutputBundle:
    token = uuid.uuid4().hex
    stage_dirs_by_parent: dict[Path, Path] = {}
    replacements: dict[str, str] = {}
    for field_name, configured_path in step6_output_paths(params).items():
        official = Path(configured_path).expanduser().resolve()
        official.parent.mkdir(parents=True, exist_ok=True)
        stage_dir = stage_dirs_by_parent.get(official.parent)
        if stage_dir is None:
            stage_dir = official.parent / f".step6-stage-{token}"
            stage_dir.mkdir(parents=False, exist_ok=False)
            stage_dirs_by_parent[official.parent] = stage_dir
        replacements[field_name] = str(stage_dir / official.name)

    staged_params = replace(params, **replacements, overwrite=True)
    staged_outputs = step6_output_paths(staged_params)
    staged_to_official = {
        str(Path(staged_outputs[field_name]).resolve()): str(Path(official).resolve())
        for field_name, official in step6_output_paths(params).items()
    }
    return StagedOutputBundle(
        official_params=params,
        staged_params=staged_params,
        staged_to_official=staged_to_official,
        stage_dirs=tuple(stage_dirs_by_parent.values()),
    )


def cleanup_staged_output_bundle(bundle: StagedOutputBundle) -> None:
    for stage_dir in bundle.stage_dirs:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _artifact_members(path: Path) -> list[Path]:
    if path.suffix.lower() == ".shp":
        candidates = [path.with_suffix(extension) for extension in SHAPEFILE_EXTENSIONS]
    else:
        candidates = [path]
        candidates.extend(
            sorted(candidate for candidate in path.parent.glob(path.name + ".*") if candidate.is_file())
        )
    return [candidate for candidate in candidates if candidate.is_file()]


def _official_member_for(staged_base: Path, official_base: Path, member: Path) -> Path:
    if staged_base.suffix.lower() == ".shp":
        return official_base.with_suffix(member.suffix)
    suffix = str(member)[len(str(staged_base)) :]
    return Path(str(official_base) + suffix)


def validate_staged_output_bundle(bundle: StagedOutputBundle) -> None:
    with rasterio.open(bundle.official_params.dem_fel_raster) as reference:
        reference_shape = (reference.height, reference.width)
        reference_transform = reference.transform
        reference_crs = reference.crs

    missing: list[str] = []
    for staged_raw in bundle.staged_to_official:
        staged = Path(staged_raw)
        if not staged.is_file() or staged.stat().st_size <= 0:
            missing.append(str(staged))
            continue
        suffix = staged.suffix.lower()
        if suffix == ".shp":
            for required_extension in (".shp", ".shx", ".dbf"):
                required = staged.with_suffix(required_extension)
                if not required.is_file() or required.stat().st_size <= 0:
                    missing.append(str(required))
        elif suffix in {".tif", ".tiff"}:
            with rasterio.open(staged) as dataset:
                if (dataset.height, dataset.width) != reference_shape:
                    raise RuntimeError(f"Staged Step 6 raster has an invalid shape: {staged}")
                if dataset.transform != reference_transform:
                    raise RuntimeError(f"Staged Step 6 raster has an invalid transform: {staged}")
                if dataset.crs != reference_crs:
                    raise RuntimeError(f"Staged Step 6 raster has an invalid CRS: {staged}")
        elif suffix == ".json":
            payload = json.loads(staged.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"Step 6 summary must be a JSON object: {staged}")
    if missing:
        raise RuntimeError("Step 6 staged output bundle is incomplete:\n  " + "\n  ".join(missing))


def publish_staged_output_bundle(bundle: StagedOutputBundle) -> None:
    pairs = [(Path(staged), Path(official)) for staged, official in bundle.staged_to_official.items()]
    pairs.sort(key=lambda pair: pair[1].suffix.lower() == ".json")
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for pair_index, (staged, official) in enumerate(pairs):
            staged_members = _artifact_members(staged)
            if not staged_members:
                raise RuntimeError(f"Missing staged Step 6 output: {staged}")
            for existing_index, existing in enumerate(_artifact_members(official)):
                backup = staged.parent / f".backup-{pair_index}-{existing_index}-{existing.name}"
                os.replace(existing, backup)
                backups.append((backup, existing))
            for staged_member in staged_members:
                official_member = _official_member_for(staged, official, staged_member)
                os.replace(staged_member, official_member)
                published.append(official_member)
    except Exception:
        for published_path in reversed(published):
            try:
                published_path.unlink(missing_ok=True)
            except OSError:
                pass
        rollback_errors: list[str] = []
        for backup, original in reversed(backups):
            try:
                os.replace(backup, original)
            except OSError as exc:
                rollback_errors.append(f"{original}: {exc}")
        if rollback_errors:
            raise RuntimeError(
                "Step 6 publication failed and rollback was incomplete:\n  "
                + "\n  ".join(rollback_errors)
            )
        raise


def _run_step6_direct(
    params: Step6Params,
    *,
    reported_params: Step6Params | None = None,
) -> dict[str, Any]:
    run_start = time.perf_counter()
    phase_start = run_start
    run_timings: dict[str, float] = {}

    def mark_run_phase(name: str) -> None:
        nonlocal phase_start
        now = time.perf_counter()
        run_timings[name] = round(now - phase_start, 3)
        phase_start = now

    dem = read_raster(params.dem_fel_raster, "dem_fel_raster")
    dinf = read_raster(params.dinf_flow_raster, "dinf_flow_raster")
    runout = read_raster(params.runout_mask_raster, "runout_mask_raster")
    stream = read_raster(params.stream_raster, "stream_raster")
    slope = read_raster(params.slope_raster, "slope_raster")
    mark_run_phase("read_input_rasters")
    validate_alignment(dem, dinf, "dinf_flow_raster")
    validate_alignment(dem, runout, "runout_mask_raster")
    validate_alignment(dem, stream, "stream_raster")
    validate_alignment(dem, slope, "slope_raster")
    dx, dy = assert_metric_grid(dem)
    validate_binary_mask_raster(runout, "runout_mask_raster")
    validate_binary_mask_raster(stream, "stream_raster")
    validate_dinf_raster(dinf)
    validate_slope_raster(slope)
    mark_run_phase("validate_inputs")

    output_paths = [
        params.output_stream_intersection_mask,
        params.output_inflow_angle,
        params.output_stream_angle,
        params.output_runout_approach_angle,
        params.output_channel_slope,
        params.output_candidate_damming_potential_class,
        params.output_damming_potential_class,
        params.output_damming_potential_score,
        params.output_height_above_stream_raster,
        params.output_valley_floor_mask_raster,
        params.output_raw_valley_width_raster,
        params.output_corrected_valley_width_raster,
        params.output_reference_volume_domain_class,
        params.output_reference_volume_combined_class,
        params.output_reference_volume_combined_score,
        params.output_summary_json,
    ]
    if params.output_candidate_points:
        output_paths.append(params.output_candidate_points)
    for output_path in output_paths:
        ensure_output_allowed(output_path, params.overwrite)

    stream_segments = load_stream_segments(params.stream_vector) if params.stream_vector else None
    mark_run_phase("prepare_outputs_and_vectors")
    result = evaluate_damming_potential(
        CoreArrays(
            dem=dem.array,
            dinf=dinf.array,
            runout_mask=runout.array,
            stream_mask=stream.array,
            slope=slope.array,
            dem_nodata=dem.nodata,
            dinf_nodata=dinf.nodata,
            runout_nodata=runout.nodata,
            stream_nodata=stream.nodata,
            slope_nodata=slope.nodata,
        ),
        params,
        dem.transform,
        dx,
        dy,
        stream_segments=stream_segments,
    )
    mark_run_phase("evaluate_damming_potential")

    write_geotiff(params.output_stream_intersection_mask, dem, result.stream_intersection_mask, "uint8", BYTE_NODATA)
    write_geotiff(params.output_inflow_angle, dem, result.inflow_angle, "float32", FLOAT_NODATA)
    write_geotiff(params.output_stream_angle, dem, result.stream_angle, "float32", FLOAT_NODATA)
    write_geotiff(params.output_runout_approach_angle, dem, result.runout_approach_angle, "float32", FLOAT_NODATA)
    write_geotiff(params.output_channel_slope, dem, result.channel_slope, "float32", FLOAT_NODATA)
    write_geotiff(
        params.output_candidate_damming_potential_class,
        dem,
        result.candidate_damming_class,
        "uint8",
        BYTE_NODATA,
    )
    write_geotiff(params.output_damming_potential_class, dem, result.damming_class, "uint8", BYTE_NODATA)
    write_geotiff(params.output_damming_potential_score, dem, result.damming_score, "float32", FLOAT_NODATA)
    if params.output_height_above_stream_raster:
        write_geotiff(
            params.output_height_above_stream_raster,
            dem,
            result.height_above_stream,
            "float32",
            FLOAT_NODATA,
        )
    if params.output_valley_floor_mask_raster:
        write_geotiff(
            params.output_valley_floor_mask_raster,
            dem,
            result.valley_floor_mask,
            "uint8",
            BYTE_NODATA,
        )
    if params.output_raw_valley_width_raster:
        write_geotiff(
            params.output_raw_valley_width_raster,
            dem,
            result.raw_valley_width,
            "float32",
            FLOAT_NODATA,
        )
    if params.output_corrected_valley_width_raster:
        write_geotiff(
            params.output_corrected_valley_width_raster,
            dem,
            result.corrected_valley_width,
            "float32",
            FLOAT_NODATA,
        )
    if params.output_reference_volume_domain_class:
        write_geotiff(
            params.output_reference_volume_domain_class,
            dem,
            result.reference_volume_domain_class,
            "uint8",
            BYTE_NODATA,
        )
    if params.output_reference_volume_combined_class:
        write_geotiff(
            params.output_reference_volume_combined_class,
            dem,
            result.reference_volume_combined_class,
            "uint8",
            BYTE_NODATA,
        )
    if params.output_reference_volume_combined_score:
        write_geotiff(
            params.output_reference_volume_combined_score,
            dem,
            result.reference_volume_combined_score,
            "float32",
            FLOAT_NODATA,
        )
    write_candidate_points(params.output_candidate_points, result.records, dem, params.overwrite)
    mark_run_phase("write_outputs")

    report_params = reported_params or params
    summary = {
        "step": "step6_landslide_damming_potential_LDP",
        "purpose": "screening-level stream blockage / landslide damming potential",
        "inputs": {
            "dem_fel_raster": dem.path,
            "dinf_flow_raster": dinf.path,
            "runout_mask_raster": runout.path,
            "stream_raster": stream.path,
            "slope_raster": slope.path,
            "stream_vector": report_params.stream_vector,
        },
        "parameters": {
            "runout_approach_window_m": float(params.runout_approach_window_m),
            "channel_slope_window_m": float(params.channel_slope_window_m),
            "low_buffer_m": float(params.low_buffer_m),
            "moderate_buffer_m": float(params.moderate_buffer_m),
            "high_buffer_m": float(params.high_buffer_m),
            "very_high_buffer_m": float(params.very_high_buffer_m),
            "very_high_inflow_angle_deg": float(params.very_high_inflow_angle_deg),
            "high_inflow_angle_deg": float(params.high_inflow_angle_deg),
            "moderate_inflow_angle_deg": float(params.moderate_inflow_angle_deg),
            "low_channel_gradient_deg": float(params.low_channel_gradient_deg),
            "steep_channel_gradient_deg": float(params.steep_channel_gradient_deg),
            "valley_width_enabled": bool(params.valley_width_enabled),
            "valley_width_elevation_threshold_m": float(params.valley_width_elevation_threshold_m),
            "valley_width_profile_half_length_m": float(params.valley_width_profile_half_length_m),
            "valley_width_profile_sample_spacing_m": float(params.valley_width_profile_sample_spacing_m),
            "valley_width_meander_window_radius_m": float(params.valley_width_meander_window_radius_m),
            "valley_width_local_profile_spacing_m": float(params.valley_width_local_profile_spacing_m),
            "numba_threads": int(params.numba_threads),
            "valley_width_assignment_chunk_size": int(params.valley_width_assignment_chunk_size),
            "reference_volume_enabled": bool(params.reference_volume_enabled),
            "reference_area_m2": float(params.reference_area_m2),
            "area_volume_coefficient": float(params.area_volume_coefficient),
            "area_volume_exponent": float(params.area_volume_exponent),
            "delivery_fraction": float(params.delivery_fraction),
            "reference_delivered_deposit_volume_m3": float(params.reference_delivered_deposit_volume_m3),
            "reference_volume_rounding_increment_m3": float(params.reference_volume_rounding_increment_m3),
        },
        **result.summary,
        "outputs": {
            "stream_intersection_mask": str(Path(report_params.output_stream_intersection_mask).resolve()),
            "inflow_angle": str(Path(report_params.output_inflow_angle).resolve()),
            "stream_angle": str(Path(report_params.output_stream_angle).resolve()),
            "runout_approach_angle": str(Path(report_params.output_runout_approach_angle).resolve()),
            "channel_slope": str(Path(report_params.output_channel_slope).resolve()),
            "candidate_damming_potential_class": str(
                Path(report_params.output_candidate_damming_potential_class).resolve()
            ),
            "damming_potential_class": str(Path(report_params.output_damming_potential_class).resolve()),
            "damming_potential_score": str(Path(report_params.output_damming_potential_score).resolve()),
            "height_above_stream_raster": str(Path(report_params.output_height_above_stream_raster).resolve())
            if report_params.output_height_above_stream_raster
            else "",
            "valley_floor_mask_raster": str(Path(report_params.output_valley_floor_mask_raster).resolve())
            if report_params.output_valley_floor_mask_raster
            else "",
            "raw_valley_width_raster": str(Path(report_params.output_raw_valley_width_raster).resolve())
            if report_params.output_raw_valley_width_raster
            else "",
            "corrected_valley_width_raster": str(Path(report_params.output_corrected_valley_width_raster).resolve())
            if report_params.output_corrected_valley_width_raster
            else "",
            "reference_volume_domain_class": str(Path(report_params.output_reference_volume_domain_class).resolve())
            if report_params.output_reference_volume_domain_class
            else "",
            "reference_volume_combined_class": str(Path(report_params.output_reference_volume_combined_class).resolve())
            if report_params.output_reference_volume_combined_class
            else "",
            "reference_volume_combined_score": str(Path(report_params.output_reference_volume_combined_score).resolve())
            if report_params.output_reference_volume_combined_score
            else "",
            "candidate_points": str(Path(report_params.output_candidate_points).resolve())
            if report_params.output_candidate_points
            else "",
            "summary_json": str(Path(report_params.output_summary_json).resolve()),
        },
        "limitations": [
            "Screening layer only; not a definitive landslide-dam hazard map.",
            "The workflow estimates valley-floor or obstructable-valley width, not active wetted-channel width.",
            "Valley width is evaluated in the separate reference-volume domain assessment and does not replace landslide volume, deposit thickness, dam height, discharge, breach probability, or outburst-flood modelling.",
            "The 10 m threshold is configurable and should be evaluated against local hillshade, contours, imagery, and known valley geometry.",
            "Stream raster/vector quality, DEM quality, slope raster quality, and flow-direction quality strongly affect the result.",
            "Results are conditional on an adopted 2,600 m3 reference delivered-deposit volume.",
            "The reference volume is not a prediction of actual source or delivered volume for each individual landslide.",
            "The 60% delivery fraction is a moderating scenario assumption and should be tested against local observations where possible.",
            "The analysis does not estimate dam height, dam stability, impounded-water volume, breach probability, or outburst-flood magnitude.",
        ],
    }
    summary["runtime_seconds"] = {
        **summary.get("runtime_seconds", {}),
        "run_read_input_rasters": run_timings.get("read_input_rasters", 0.0),
        "run_validate_inputs": run_timings.get("validate_inputs", 0.0),
        "run_prepare_outputs_and_vectors": run_timings.get("prepare_outputs_and_vectors", 0.0),
        "run_evaluate_damming_potential": run_timings.get("evaluate_damming_potential", 0.0),
        "run_write_outputs": run_timings.get("write_outputs", 0.0),
        "run_total_before_summary_write": round(time.perf_counter() - run_start, 3),
    }
    Path(params.output_summary_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_step6(params: Step6Params) -> dict[str, Any]:
    validate_thresholds(params)
    for output_path in step6_output_paths(params).values():
        ensure_output_allowed(output_path, params.overwrite)
    bundle = prepare_staged_output_bundle(params)
    try:
        summary = _run_step6_direct(bundle.staged_params, reported_params=params)
        validate_staged_output_bundle(bundle)
        publish_staged_output_bundle(bundle)
        return summary
    finally:
        cleanup_staged_output_bundle(bundle)


def _smoke_params() -> Step6Params:
    return Step6Params(
        dem_fel_raster="",
        dinf_flow_raster="",
        runout_mask_raster="",
        stream_raster="",
        slope_raster="",
        output_stream_intersection_mask="",
        output_inflow_angle="",
        output_stream_angle="",
        output_runout_approach_angle="",
        output_channel_slope="",
        output_candidate_damming_potential_class="",
        output_damming_potential_class="",
        output_damming_potential_score="",
        output_summary_json="",
        runout_approach_window_m=2.0,
        channel_slope_window_m=2.0,
        very_high_inflow_angle_deg=75.0,
        high_inflow_angle_deg=60.0,
        moderate_inflow_angle_deg=30.0,
        low_channel_gradient_deg=10.0,
        steep_channel_gradient_deg=15.0,
    )


def run_smoke_test() -> dict[str, Any]:
    params = _smoke_params()
    transform = rasterio.transform.from_origin(0.0, 5.0, 1.0, 1.0)
    dem = np.array(
        [
            [10, 10, 10, 10, 10],
            [9, 9, 9, 9, 9],
            [8, 8, 8, 8, 8],
            [7, 7, 7, 7, 7],
            [6, 6, 6, 6, 6],
        ],
        dtype=np.float32,
    )

    stream = np.zeros((5, 5), dtype=np.uint8)
    stream[2, 1:4] = 1
    runout = np.zeros((5, 5), dtype=np.uint8)
    runout[1, 2] = 1
    runout[2, 2] = 1
    dinf = np.full((5, 5), math.radians(270.0), dtype=np.float32)
    slope = np.full((5, 5), 5.0, dtype=np.float32)
    result = evaluate_damming_potential(
        CoreArrays(dem=dem, dinf=dinf, runout_mask=runout, stream_mask=stream, slope=slope),
        params,
        transform,
        1.0,
        1.0,
    )
    high_class = int(result.damming_class[2, 2])
    high_inflow = float(result.inflow_angle[2, 2])
    if high_class not in {3, 4} or high_inflow < 75.0:
        raise AssertionError("Perpendicular low-gradient intersection should produce high or very high damming potential.")

    disabled_params = _smoke_params()
    disabled_params.valley_width_enabled = False
    disabled_result = evaluate_damming_potential(
        CoreArrays(dem=dem, dinf=dinf, runout_mask=runout, stream_mask=stream, slope=slope),
        disabled_params,
        transform,
        1.0,
        1.0,
    )
    if int(disabled_result.damming_class[2, 2]) != high_class:
        raise AssertionError("Width-disabled mode should preserve the original angle-and-slope class.")

    dinf_parallel = np.full((5, 5), math.radians(0.0), dtype=np.float32)
    runout_parallel = np.zeros((5, 5), dtype=np.uint8)
    runout_parallel[2, 1] = 1
    runout_parallel[2, 2] = 1
    steep_slope = np.full((5, 5), 20.0, dtype=np.float32)
    result_parallel = evaluate_damming_potential(
        CoreArrays(
            dem=dem,
            dinf=dinf_parallel,
            runout_mask=runout_parallel,
            stream_mask=stream,
            slope=steep_slope,
        ),
        params,
        transform,
        1.0,
        1.0,
    )
    if int(result_parallel.damming_class[2, 2]) != 5:
        raise AssertionError("Parallel steep-channel intersection should favor debris-flow connectivity.")

    normalized = [inflow_angle_deg(a, b) for a, b in [(0, 0), (0, 180), (10, 170), (270, 0), (135, 45)]]
    if any(value < 0.0 or value > 90.0 for value in normalized):
        raise AssertionError("Inflow-angle normalization failed to stay within 0..90 degrees.")
    if result.summary["counts"]["candidate_cells"] <= 0:
        raise AssertionError("Smoke test produced no candidate cells.")

    valley_params = _smoke_params()
    valley_params.runout_approach_window_m = 5.0
    valley_params.channel_slope_window_m = 5.0
    valley_params.valley_width_profile_half_length_m = 20.0
    valley_params.valley_width_meander_window_radius_m = 10.0
    valley_params.valley_width_local_profile_spacing_m = 2.0

    valley_shape = (41, 41)
    stream_row = 20
    dem_valley = np.zeros(valley_shape, dtype=np.float32)
    for rr in range(valley_shape[0]):
        dem_valley[rr, :] = abs(rr - stream_row)
    stream_valley = np.zeros(valley_shape, dtype=np.uint8)
    stream_valley[stream_row, :] = 1
    runout_valley = np.zeros(valley_shape, dtype=np.uint8)
    runout_valley[stream_row - 4 : stream_row + 1, 20] = 1
    dinf_valley = np.zeros(valley_shape, dtype=np.float32)
    dinf_valley[:stream_row, :] = math.radians(270.0)
    dinf_valley[stream_row + 1 :, :] = math.radians(90.0)
    slope_valley = np.full(valley_shape, 5.0, dtype=np.float32)
    valley_transform = rasterio.transform.from_origin(0.0, float(valley_shape[0]), 1.0, 1.0)
    receiver_row, receiver_col = build_primary_dinf_receivers(
        dem_valley,
        np.ones(valley_shape, dtype=bool),
        dinf_valley,
        np.ones(valley_shape, dtype=bool),
        1.0,
        1.0,
    )
    height, _stream_elev, negative_height_count = trace_to_stream_and_compute_height_above_stream(
        dem_valley,
        np.ones(valley_shape, dtype=bool),
        stream_valley.astype(bool),
        receiver_row,
        receiver_col,
    )
    if negative_height_count != 0:
        raise AssertionError("Synthetic downhill valley unexpectedly produced negative HANS values.")
    valley_floor = create_valley_floor_mask(height, stream_valley.astype(bool), np.ones(valley_shape, dtype=bool), 10.0)
    if int(valley_floor[10:31, 20].sum()) != 21 or valley_floor[9, 20] or valley_floor[31, 20]:
        raise AssertionError("Valley-floor mask did not match the 10 m height-above-stream threshold.")

    raw_profile = sample_perpendicular_valley_profile(
        valley_floor.astype(bool),
        valley_transform,
        stream_row,
        20,
        0.0,
        1.0,
        1.0,
        20.0,
        1.0,
    )
    if raw_profile.raw_width_m is None or abs(raw_profile.raw_width_m - 20.0) > 1.0:
        raise AssertionError(f"Straight-valley raw width should be about 20 m, got {raw_profile.raw_width_m}.")

    oblique_profile = sample_perpendicular_valley_profile(
        valley_floor.astype(bool),
        valley_transform,
        stream_row,
        20,
        45.0,
        1.0,
        1.0,
        30.0,
        1.0,
    )
    raw_cache = {
        (stream_row, 20): ValleyProfileMeasurement(oblique_profile.raw_width_m, False, "synthetic_oblique"),
        (stream_row, 21): ValleyProfileMeasurement(raw_profile.raw_width_m, False, "synthetic_straight"),
    }
    raw_raster = np.full(valley_shape, FLOAT_NODATA, dtype=np.float32)
    _raw, corrected, corrected_censored, _profile_count, _method = measure_corrected_valley_width(
        stream_row,
        20,
        stream_valley.astype(bool),
        valley_floor.astype(bool),
        valley_transform,
        1.0,
        1.0,
        valley_params,
        None,
        raw_cache,
        raw_raster,
    )
    if oblique_profile.raw_width_m is None or oblique_profile.raw_width_m <= raw_profile.raw_width_m:
        raise AssertionError("Synthetic oblique profile should inflate raw valley width.")
    if corrected is None or abs(corrected - raw_profile.raw_width_m) > 1.0 or corrected_censored:
        raise AssertionError("Moving-window minimum should recover the narrower local valley width.")

    valley_result = evaluate_damming_potential(
        CoreArrays(
            dem=dem_valley,
            dinf=dinf_valley,
            runout_mask=runout_valley,
            stream_mask=stream_valley,
            slope=slope_valley,
        ),
        valley_params,
        valley_transform,
        1.0,
        1.0,
    )
    measured_width = float(valley_result.corrected_valley_width[stream_row, 20])
    if abs(measured_width - 20.0) > 1.0:
        raise AssertionError(f"Corrected valley width should be about 20 m, got {measured_width}.")

    fully_open_valley = np.ones((11, 11), dtype=bool)
    censored_profile = sample_perpendicular_valley_profile(
        fully_open_valley,
        rasterio.transform.from_origin(0.0, 11.0, 1.0, 1.0),
        5,
        5,
        0.0,
        1.0,
        1.0,
        5.0,
        1.0,
    )
    if not censored_profile.censored:
        raise AssertionError("Open profiles that remain valley floor to the maximum span should be censored.")

    thresholds = calculate_reference_volume_thresholds(2500.0, 0.54, 1.15, 0.60, 2600.0)
    if abs(thresholds.source_volume_m3 - 4365.407) > 0.01:
        raise AssertionError("Reference source-volume derivation failed.")
    if abs(thresholds.delivered_volume_unrounded_m3 - 2619.244) > 0.01:
        raise AssertionError("Reference delivered-volume derivation failed.")
    if rounded_reference_volume(thresholds.delivered_volume_unrounded_m3, 100.0) != 2600.0:
        raise AssertionError("Reference delivered-volume rounding failed.")
    if abs(thresholds.formation_width_m - 3.79742155) > 1.0e-6:
        raise AssertionError("Reference formation-width threshold failed.")
    if abs(thresholds.possible_width_m - 18.78493820) > 1.0e-6:
        raise AssertionError("Reference possible-width threshold failed.")

    domain_checks = [
        (3.70, False, 3),
        (thresholds.formation_width_m, False, 3),
        (10.00, False, 2),
        (thresholds.possible_width_m, False, 2),
        (19.00, False, 1),
        (19.00, True, 1),
        (10.00, True, 0),
    ]
    for width, censored, expected_domain in domain_checks:
        domain, _status = classify_reference_volume_domain(width, censored, thresholds)
        if domain != expected_domain:
            raise AssertionError(f"Reference-volume domain check failed for width={width}, censored={censored}.")

    if combine_reference_volume_class(5, 0.5, 3) != (5, 0.0):
        raise AssertionError("Class 5 must remain combined Class 5 for all valley widths.")
    if combine_reference_volume_class(4, 0.8, 3) != (4, 0.8):
        raise AssertionError("Formation-domain geometry Class 4 should retain Class 4 and full geometry score.")
    if combine_reference_volume_class(4, 0.8, 2) != (3, 0.4):
        raise AssertionError("Possible-domain geometry Class 4 should cap to combined Class 3 and half score.")
    if combine_reference_volume_class(4, 0.8, 1) != (1, 0.0):
        raise AssertionError("Non-formation domain should override geometry Class 4 to combined Class 1.")

    invalid_params = _smoke_params()
    invalid_params.reference_volume_enabled = True
    invalid_params.valley_width_enabled = False
    try:
        validate_thresholds(invalid_params)
    except ValueError as exc:
        if "reference_volume_enabled=true requires valley_width_enabled=true" not in str(exc):
            raise
    else:
        raise AssertionError("reference_volume_enabled=true with valley_width_enabled=false should fail validation.")

    return {
        "status": "ok",
        "perpendicular_inflow_angle_deg": high_inflow,
        "perpendicular_class": high_class,
        "parallel_steep_class": int(result_parallel.damming_class[2, 2]),
        "normalized_angle_examples": normalized,
        "candidate_cells": int(result.summary["counts"]["candidate_cells"]),
        "width_disabled_class": int(disabled_result.damming_class[2, 2]),
        "straight_valley_raw_width_m": raw_profile.raw_width_m,
        "straight_valley_corrected_width_m": measured_width,
        "oblique_raw_width_m": oblique_profile.raw_width_m,
        "meander_corrected_width_m": corrected,
        "censored_profile_flag": censored_profile.censored,
        "reference_source_volume_m3": thresholds.source_volume_m3,
        "reference_delivered_volume_unrounded_m3": thresholds.delivered_volume_unrounded_m3,
        "reference_formation_width_m": thresholds.formation_width_m,
        "reference_possible_width_m": thresholds.possible_width_m,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Step 6 landslide damming potential screening from modeled deposition and stream channels."
    )
    parser.add_argument("--config", help="Path to Step 6 JSON config.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs.")
    parser.add_argument("--smoke-test", action="store_true", help="Run synthetic in-memory checks and exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.smoke_test:
        print(json.dumps(run_smoke_test(), indent=2))
        return 0
    if not args.config:
        parser.error("--config is required unless --smoke-test is used.")
    try:
        params = load_params(args.config, overwrite=bool(args.overwrite))
        summary = run_step6(params)
    except Exception as exc:
        print(f"Step 6 landslide damming potential failed: {exc}", file=sys.stderr)
        return 1

    print("Step 6 landslide damming potential completed.")
    print(f"Candidate cells: {summary['counts']['candidate_cells']}")
    print(f"Evaluated candidate cells: {summary['counts']['evaluated_candidate_cells']}")
    print(f"Summary JSON: {summary['outputs']['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
