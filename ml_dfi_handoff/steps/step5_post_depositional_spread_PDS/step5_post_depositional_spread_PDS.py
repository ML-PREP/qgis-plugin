"""Step 5 post-depositional spread PDS.

Standalone script version for the Codes_V2 workspace. The scientific logic is
consolidated from the copied PDS modules without changing the spread method.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import numpy as np

try:
    from osgeo import gdal, ogr, osr
except ModuleNotFoundError:
    gdal = None
    ogr = None
    osr = None


@dataclass(frozen=True)
class PostDepositionalSpreadParams:
    runout_mask_raster: str
    dfi_raster: str
    dem_raster: str
    source_mask_raster: str
    stream_mask_raster: str
    source_contributing_area_raster: str
    output_spread_mask: str
    output_combined_mask: str
    output_seed_mask: str
    output_cleaned_runout_mask: str = ""
    output_removed_runout_mask: str = ""
    output_spread_mask_shapefile: str = ""
    output_combined_mask_shapefile: str = ""
    output_summary_json: str = ""
    output_min_elev_diff: str = ""
    output_source_row: str = ""
    output_source_col: str = ""
    dfi_threshold: float = 0.69
    max_relative_rise_m: float = 1.0
    min_runout_source_area_m2: float = 200.0
    min_spread_radius_m: float = 5.0
    max_spread_radius_m: float = 30.0
    source_contributing_area_reference: float = 4400.0
    write_debug_rasters: bool = False


@dataclass(frozen=True)
class RasterMetadata:
    path: str
    rows: int
    cols: int
    geotransform: tuple[float, float, float, float, float, float]
    projection_wkt: str
    nodata: float | None
    dtype_name: str


@dataclass(frozen=True)
class RasterSpec(RasterMetadata):
    array: np.ndarray


@dataclass(frozen=True)
class SpreadSummary:
    input_paths: dict[str, str]
    output_paths: dict[str, str]
    rows: int
    cols: int
    pixel_size: tuple[float, float]
    geotransform: tuple[float, float, float, float, float, float]
    crs_authority: str | None
    crs_wkt: str
    dfi_threshold: float
    max_relative_rise_m: float
    min_spread_radius_m: float
    max_spread_radius_m: float
    source_contributing_area_reference: float
    spread_mode: str
    min_runout_source_area_m2: float
    original_runout_cells: int
    area_removed_runout_cells: int
    disconnected_runout_cells: int
    removed_runout_cells: int
    removed_runout_percent: float | None
    cleaned_runout_cells: int
    valid_runout_cells: int
    seed_cells: int
    seeds_with_zero_contributing_source_area: int
    seeds_assigned_min_radius: int
    seeds_assigned_max_or_near_max_radius: int
    spread_cells_added: int
    combined_footprint_cells: int
    source_cells_processed: int
    candidate_evaluations: int
    neighbor_offset_count: int
    neighbor_offsets: list[list[float]]
    dfi_stats: dict[str, float | None] = field(default_factory=lambda: cast(dict[str, float | None], {}))
    source_contributing_area_stats: dict[str, float | None] = field(default_factory=lambda: cast(dict[str, float | None], {}))
    source_contribution_index_stats: dict[str, float | None] = field(default_factory=lambda: cast(dict[str, float | None], {}))
    computed_spread_radius_stats: dict[str, float | None] = field(default_factory=lambda: cast(dict[str, float | None], {}))
    rejection_counts: dict[str, int] = field(default_factory=lambda: cast(dict[str, int], {}))
    warnings: list[str] = field(default_factory=lambda: cast(list[str], []))
    timings_seconds: dict[str, float] = field(default_factory=lambda: cast(dict[str, float], {}))
    notes: list[str] = field(default_factory=lambda: cast(list[str], []))
    metadata: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))


@dataclass(frozen=True)
class NativeSpreadResult:
    spread_mask: np.ndarray
    combined_mask: np.ndarray
    seed_mask: np.ndarray
    cleaned_runout_mask: np.ndarray
    removed_runout_mask: np.ndarray
    min_elev_diff: np.ndarray
    source_index: np.ndarray
    source_contributing_area: np.ndarray | None
    source_contribution_index: np.ndarray | None
    computed_spread_radius: np.ndarray | None
    summary: SpreadSummary


@dataclass(frozen=True)
class StagedOutputBundle:
    official_params: PostDepositionalSpreadParams
    staged_params: PostDepositionalSpreadParams
    staged_to_official: dict[str, str]
    stage_dirs: tuple[Path, ...]


REQUIRED_CONFIG_FIELDS = {
    "runout_mask_raster",
    "dfi_raster",
    "dem_raster",
    "source_mask_raster",
    "stream_mask_raster",
    "source_contributing_area_raster",
    "output_spread_mask",
    "output_combined_mask",
    "output_seed_mask",
    "output_cleaned_runout_mask",
    "output_removed_runout_mask",
    "output_summary_json",
}

OPTIONAL_CONFIG_FIELDS = {
    "output_spread_mask_shapefile",
    "output_combined_mask_shapefile",
    "output_min_elev_diff",
    "output_source_row",
    "output_source_col",
    "dfi_threshold",
    "max_relative_rise_m",
    "min_runout_source_area_m2",
    "min_spread_radius_m",
    "max_spread_radius_m",
    "source_contributing_area_reference",
    "write_debug_rasters",
}

ACTIVE_CONFIG_FIELDS = REQUIRED_CONFIG_FIELDS | OPTIONAL_CONFIG_FIELDS

INPUT_PARAM_FIELDS = (
    "runout_mask_raster",
    "dfi_raster",
    "dem_raster",
    "source_mask_raster",
    "stream_mask_raster",
    "source_contributing_area_raster",
)

CORE_OUTPUT_PARAM_FIELDS = (
    "output_spread_mask",
    "output_combined_mask",
    "output_seed_mask",
    "output_cleaned_runout_mask",
    "output_removed_runout_mask",
    "output_summary_json",
)

OPTIONAL_OUTPUT_PARAM_FIELDS = (
    "output_spread_mask_shapefile",
    "output_combined_mask_shapefile",
    "output_min_elev_diff",
    "output_source_row",
    "output_source_col",
)


def load_json_object(config_path: str) -> tuple[dict[str, object], Path]:
    cfg_path = Path(config_path).expanduser().resolve()
    with cfg_path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Config root must be a JSON object: {cfg_path}")
    return {str(key): value for key, value in payload.items()}, cfg_path.parent


def require_non_empty_string(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return text


def resolve_path(root: Path, value: object) -> str:
    text = require_non_empty_string(value, "path")
    path = Path(text)
    if path.is_absolute():
        return str(path)
    return str((root / path).resolve())


def optional_resolve_path(root: Path, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return str(path)
    return str((root / path).resolve())


def parse_optional_float(value: object, *, field_name: str, default: float) -> float:
    if value is None or str(value).strip() == "":
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric when provided.") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite.")
    return parsed


def parse_optional_bool(value: object, *, field_name: str, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    raise ValueError(f"{field_name} must be a boolean value.")


def _reject_removed_fields(cfg: dict[str, object]) -> None:
    removed_fields = [
        "spread_radius_m",
        "spread_half_angle_deg",
        "allow_recursive_spread",
        "terminal_slope_min_deg",
        "terminal_slope_max_deg",
        "high_vhigh_fraction_weight",
        "source_susceptibility_raster",
        "source_area_reference_m2",
        "source_area_weight",
        "high_fraction_weight",
        "plan_curvature_raster",
        "terminal_influence_length_m",
        "min_valid_terminal_path_length_m",
    ]
    for field_name in removed_fields:
        if field_name in cfg:
            raise ValueError(
                f"{field_name} is no longer supported. "
                "Step 5 spread radius is now controlled by D-infinity-connected source contribution."
            )


def _validate_loaded_values(
    *,
    dfi_threshold: float,
    max_relative_rise_m: float,
    source_contributing_area_reference: float,
    min_runout_source_area_m2: float,
    min_spread_radius_m: float,
    max_spread_radius_m: float,
) -> None:
    values = {
        "dfi_threshold": dfi_threshold,
        "max_relative_rise_m": max_relative_rise_m,
        "source_contributing_area_reference": source_contributing_area_reference,
        "min_runout_source_area_m2": min_runout_source_area_m2,
        "min_spread_radius_m": min_spread_radius_m,
        "max_spread_radius_m": max_spread_radius_m,
    }
    nonfinite = [name for name, value in values.items() if not math.isfinite(float(value))]
    if nonfinite:
        raise ValueError("Step 5 numeric settings must be finite: " + ", ".join(nonfinite))
    if not 0.0 <= dfi_threshold <= 1.0:
        raise ValueError("dfi_threshold must be within [0, 1].")
    if max_relative_rise_m < 0.0:
        raise ValueError("max_relative_rise_m must be non-negative.")
    if source_contributing_area_reference <= 0.0:
        raise ValueError("source_contributing_area_reference must be positive.")
    if min_runout_source_area_m2 < 0.0:
        raise ValueError("min_runout_source_area_m2 must be non-negative.")
    if min_spread_radius_m <= 0.0 or max_spread_radius_m <= 0.0:
        raise ValueError("Spread-radius bounds must be positive.")
    if max_spread_radius_m < min_spread_radius_m:
        raise ValueError("max_spread_radius_m must be greater than or equal to min_spread_radius_m.")


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def step5_input_paths(params: PostDepositionalSpreadParams) -> dict[str, str]:
    return {field_name: str(getattr(params, field_name)) for field_name in INPUT_PARAM_FIELDS}


def step5_output_paths(params: PostDepositionalSpreadParams) -> dict[str, str]:
    outputs = {
        field_name: str(getattr(params, field_name))
        for field_name in CORE_OUTPUT_PARAM_FIELDS
    }
    for field_name in ("output_spread_mask_shapefile", "output_combined_mask_shapefile"):
        value = str(getattr(params, field_name)).strip()
        if value:
            outputs[field_name] = value
    if params.write_debug_rasters:
        for field_name in ("output_min_elev_diff", "output_source_row", "output_source_col"):
            value = str(getattr(params, field_name)).strip()
            if value:
                outputs[field_name] = value
        outputs.update(
            {
                "output_source_elevation": _derive_related_output_path(
                    params.output_spread_mask,
                    "seed_elevation",
                ),
                "output_computed_spread_radius": _derive_related_output_path(
                    params.output_spread_mask,
                    "computed_spread_radius",
                ),
                "output_source_contributing_area": _derive_related_output_path(
                    params.output_spread_mask,
                    "source_contributing_area",
                ),
                "output_source_contribution_index": _derive_related_output_path(
                    params.output_spread_mask,
                    "source_contribution_index",
                ),
            }
        )
    return outputs


def _validate_path_contract(params: PostDepositionalSpreadParams) -> None:
    inputs = step5_input_paths(params)
    outputs = step5_output_paths(params)
    input_by_path = {_normalized_path(path): name for name, path in inputs.items()}
    output_by_path: dict[str, str] = {}
    for name, path in outputs.items():
        normalized = _normalized_path(path)
        if normalized in input_by_path:
            raise ValueError(
                f"{name} must not overwrite input {input_by_path[normalized]}: {path}"
            )
        if normalized in output_by_path:
            raise ValueError(
                f"Step 5 outputs {output_by_path[normalized]} and {name} resolve to the same path: {path}"
            )
        output_by_path[normalized] = name


def load_post_depositional_spread_params(config_path: str) -> PostDepositionalSpreadParams:
    # This loader deliberately rejects old global spread settings because the
    # current method derives spread radius from source contribution.
    cfg, root = load_json_object(config_path)
    _reject_removed_fields(cfg)
    unknown_fields = sorted(set(cfg) - ACTIVE_CONFIG_FIELDS)
    if unknown_fields:
        raise ValueError(
            "Unknown Step 5 config field(s): " + ", ".join(unknown_fields)
        )
    missing_fields = sorted(field_name for field_name in REQUIRED_CONFIG_FIELDS if field_name not in cfg)
    if missing_fields:
        raise ValueError(
            "Missing required Step 5 config field(s): " + ", ".join(missing_fields)
        )

    dfi_threshold = parse_optional_float(
        cfg.get("dfi_threshold"),
        field_name="dfi_threshold",
        default=0.69,
    )
    max_relative_rise_m = parse_optional_float(
        cfg.get("max_relative_rise_m"),
        field_name="max_relative_rise_m",
        default=1.0,
    )
    source_contributing_area_reference = parse_optional_float(
        cfg.get("source_contributing_area_reference"),
        field_name="source_contributing_area_reference",
        default=4400.0,
    )
    min_runout_source_area_m2 = parse_optional_float(
        cfg.get("min_runout_source_area_m2"),
        field_name="min_runout_source_area_m2",
        default=200.0,
    )
    min_spread_radius_m = parse_optional_float(
        cfg.get("min_spread_radius_m"),
        field_name="min_spread_radius_m",
        default=5.0,
    )
    max_spread_radius_m = parse_optional_float(
        cfg.get("max_spread_radius_m"),
        field_name="max_spread_radius_m",
        default=30.0,
    )
    _validate_loaded_values(
        dfi_threshold=dfi_threshold,
        max_relative_rise_m=max_relative_rise_m,
        source_contributing_area_reference=source_contributing_area_reference,
        min_runout_source_area_m2=min_runout_source_area_m2,
        min_spread_radius_m=min_spread_radius_m,
        max_spread_radius_m=max_spread_radius_m,
    )
    params = PostDepositionalSpreadParams(
        runout_mask_raster=resolve_path(root, require_non_empty_string(cfg.get("runout_mask_raster"), "runout_mask_raster")),
        dfi_raster=resolve_path(root, require_non_empty_string(cfg.get("dfi_raster"), "dfi_raster")),
        dem_raster=resolve_path(root, require_non_empty_string(cfg.get("dem_raster"), "dem_raster")),
        source_mask_raster=resolve_path(
            root,
            require_non_empty_string(cfg.get("source_mask_raster"), "source_mask_raster"),
        ),
        stream_mask_raster=resolve_path(
            root,
            require_non_empty_string(cfg.get("stream_mask_raster"), "stream_mask_raster"),
        ),
        source_contributing_area_raster=resolve_path(
            root,
            require_non_empty_string(cfg.get("source_contributing_area_raster"), "source_contributing_area_raster"),
        ),
        output_spread_mask=resolve_path(root, require_non_empty_string(cfg.get("output_spread_mask"), "output_spread_mask")),
        output_combined_mask=resolve_path(
            root,
            require_non_empty_string(cfg.get("output_combined_mask"), "output_combined_mask"),
        ),
        output_seed_mask=resolve_path(root, require_non_empty_string(cfg.get("output_seed_mask"), "output_seed_mask")),
        output_cleaned_runout_mask=resolve_path(
            root,
            require_non_empty_string(cfg.get("output_cleaned_runout_mask"), "output_cleaned_runout_mask"),
        ),
        output_removed_runout_mask=resolve_path(
            root,
            require_non_empty_string(cfg.get("output_removed_runout_mask"), "output_removed_runout_mask"),
        ),
        output_spread_mask_shapefile=optional_resolve_path(root, cfg.get("output_spread_mask_shapefile", "")),
        output_combined_mask_shapefile=optional_resolve_path(root, cfg.get("output_combined_mask_shapefile", "")),
        output_summary_json=resolve_path(
            root,
            require_non_empty_string(cfg.get("output_summary_json"), "output_summary_json"),
        ),
        output_min_elev_diff=optional_resolve_path(root, cfg.get("output_min_elev_diff", "")),
        output_source_row=optional_resolve_path(root, cfg.get("output_source_row", "")),
        output_source_col=optional_resolve_path(root, cfg.get("output_source_col", "")),
        dfi_threshold=dfi_threshold,
        max_relative_rise_m=max_relative_rise_m,
        min_runout_source_area_m2=min_runout_source_area_m2,
        min_spread_radius_m=min_spread_radius_m,
        max_spread_radius_m=max_spread_radius_m,
        source_contributing_area_reference=source_contributing_area_reference,
        write_debug_rasters=parse_optional_bool(
            cfg.get("write_debug_rasters"),
            field_name="write_debug_rasters",
            default=False,
        ),
    )
    _validate_path_contract(params)
    return params


FLOAT32_NODATA = np.float32(-3.402823466e38)
INT32_NODATA = -1
GT_TOLERANCE = 1e-9
LINEAR_UNITS_TOLERANCE = 1e-9
ELEVATION_TIE_TOLERANCE = 1e-12
EIGHT_NEIGHBOR_STEPS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


@dataclass(frozen=True)
class SourceContribution:
    source_contributing_area: float
    source_contribution_index: float
    computed_spread_radius_m: float
    has_zero_source_area: bool


def _require_osgeo() -> tuple[Any, Any, Any]:
    if gdal is None or ogr is None or osr is None:
        raise RuntimeError(
            "Step 5 raster execution requires QGIS/OSGeo GDAL bindings. "
            "Use run_step5.bat or a Python environment with osgeo installed."
        )
    gdal.UseExceptions()
    return gdal, ogr, osr


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _optional_abspath(path: str) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    return os.path.abspath(raw)


def _read_raster_metadata(path: str, label: str) -> RasterMetadata:
    gdal_module, _, _ = _require_osgeo()
    try:
        ds = gdal_module.Open(path, gdal_module.GA_ReadOnly)
    except RuntimeError as exc:
        raise FileNotFoundError(f"Could not open {label} raster: {path}") from exc
    if ds is None:
        raise FileNotFoundError(f"Could not open {label} raster: {path}")

    band = ds.GetRasterBand(1)
    if band is None:
        raise RuntimeError(f"{label} raster has no band 1: {path}")

    gt_raw = ds.GetGeoTransform(can_return_null=True)
    if gt_raw is None or len(gt_raw) != 6:
        raise RuntimeError(f"Invalid geotransform in {label}: {path}")

    metadata = RasterMetadata(
        path=os.path.abspath(path),
        rows=int(ds.RasterYSize),
        cols=int(ds.RasterXSize),
        geotransform=(
            float(gt_raw[0]),
            float(gt_raw[1]),
            float(gt_raw[2]),
            float(gt_raw[3]),
            float(gt_raw[4]),
            float(gt_raw[5]),
        ),
        projection_wkt=ds.GetProjectionRef() or "",
        nodata=None if band.GetNoDataValue() is None else float(band.GetNoDataValue()),
        dtype_name=str(gdal_module.GetDataTypeName(band.DataType)),
    )
    ds = None
    return metadata


def _read_raster(
    path: str,
    label: str,
    metadata: RasterMetadata | None = None,
) -> RasterSpec:
    gdal_module, _, _ = _require_osgeo()
    raster_metadata = metadata or _read_raster_metadata(path, label)
    try:
        ds = gdal_module.Open(path, gdal_module.GA_ReadOnly)
    except RuntimeError as exc:
        raise FileNotFoundError(f"Could not open {label} raster: {path}") from exc
    if ds is None:
        raise FileNotFoundError(f"Could not open {label} raster: {path}")
    band = ds.GetRasterBand(1)
    if band is None:
        raise RuntimeError(f"{label} raster has no band 1: {path}")
    array = band.ReadAsArray()
    if array is None:
        raise RuntimeError(f"Failed reading raster array for {label}: {path}")
    result = RasterSpec(
        path=raster_metadata.path,
        rows=raster_metadata.rows,
        cols=raster_metadata.cols,
        geotransform=raster_metadata.geotransform,
        projection_wkt=raster_metadata.projection_wkt,
        nodata=raster_metadata.nodata,
        dtype_name=raster_metadata.dtype_name,
        array=np.asarray(array),
    )
    ds = None
    return result


def _srs_from_wkt(wkt: str) -> osr.SpatialReference:
    _, _, osr_module = _require_osgeo()
    srs = osr_module.SpatialReference()
    if wkt:
        srs.ImportFromWkt(wkt)
    if hasattr(srs, "SetAxisMappingStrategy"):
        srs.SetAxisMappingStrategy(osr_module.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def _crs_authority_string(srs: osr.SpatialReference) -> str | None:
    authority_name = srs.GetAuthorityName(None)
    authority_code = srs.GetAuthorityCode(None)
    if authority_name and authority_code:
        return f"{authority_name}:{authority_code}"
    return None


def _gt_equal(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
    tol: float = GT_TOLERANCE,
) -> bool:
    return all(abs(left[idx] - right[idx]) <= tol for idx in range(6))


def _validate_alignment(
    reference: RasterSpec | RasterMetadata,
    other: RasterSpec | RasterMetadata,
    label: str,
) -> None:
    if reference.rows != other.rows or reference.cols != other.cols:
        raise ValueError(
            f"{label} size mismatch: expected {reference.cols}x{reference.rows}, "
            f"got {other.cols}x{other.rows} ({other.path})."
        )
    if not _gt_equal(reference.geotransform, other.geotransform):
        raise ValueError(f"{label} geotransform mismatch with reference grid: {other.path}")

    reference_srs = _srs_from_wkt(reference.projection_wkt)
    other_srs = _srs_from_wkt(other.projection_wkt)
    if not bool(reference_srs.IsSame(other_srs)):
        raise ValueError(f"{label} CRS mismatch with reference grid: {other.path}")


def _assert_projected_metric_grid(
    reference: RasterSpec | RasterMetadata,
) -> tuple[float, float, osr.SpatialReference]:
    gt = reference.geotransform
    if abs(gt[2]) > GT_TOLERANCE or abs(gt[4]) > GT_TOLERANCE:
        raise ValueError(
            "Rotated/sheared geotransforms are not supported. "
            "Provide north-up rasters with zero rotation terms."
        )

    dx = abs(float(gt[1]))
    dy = abs(float(gt[5]))
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("Invalid pixel size in reference raster geotransform.")

    srs = _srs_from_wkt(reference.projection_wkt)
    if bool(srs.IsGeographic()):
        raise ValueError("Geographic CRS is not supported. Use projected metric rasters.")
    if not bool(srs.IsProjected()):
        raise ValueError("Projected metric CRS is required for post-depositional spread.")

    linear_units_to_meter = float(srs.GetLinearUnits())
    if abs(linear_units_to_meter - 1.0) > LINEAR_UNITS_TOLERANCE:
        raise ValueError(
            "Projected CRS linear units must be meters for post-depositional spread. "
            f"Found conversion factor to meters: {linear_units_to_meter!r}."
        )

    return dx, dy, srs


def preflight_step5(
    params: PostDepositionalSpreadParams,
) -> tuple[dict[str, RasterMetadata], dict[str, Any]]:
    labels = {
        "runout_mask_raster": "runout mask",
        "dfi_raster": "DFI",
        "dem_raster": "DEM",
        "source_mask_raster": "source mask",
        "stream_mask_raster": "stream mask",
        "source_contributing_area_raster": "source contributing area",
    }
    metadata: dict[str, RasterMetadata] = {}
    for field_name, path in step5_input_paths(params).items():
        metadata[field_name] = _read_raster_metadata(path, labels[field_name])

    reference = metadata["runout_mask_raster"]
    for field_name, label in labels.items():
        if field_name == "runout_mask_raster":
            continue
        _validate_alignment(reference, metadata[field_name], label)
    dx, dy, srs = _assert_projected_metric_grid(reference)

    output_parent_dirs = sorted(
        {
            str(Path(path).expanduser().resolve().parent)
            for path in step5_output_paths(params).values()
        }
    )
    report = {
        "status": "metadata_passed",
        "grid": {
            "rows": int(reference.rows),
            "cols": int(reference.cols),
            "dx": float(dx),
            "dy": float(dy),
            "crs_authority": _crs_authority_string(srs),
        },
        "inputs": {
            field_name: {
                "path": raster.path,
                "dtype": raster.dtype_name,
                "nodata": raster.nodata,
            }
            for field_name, raster in metadata.items()
        },
        "output_parent_dirs": output_parent_dirs,
        "value_validation": "required_before_publication",
    }
    return metadata, report


def _valid_mask(array: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(array)
    if nodata is not None and np.isfinite(nodata):
        mask &= ~np.isclose(array, nodata, atol=0.0, rtol=0.0)
    return mask


def _write_geotiff(
    out_path: str,
    reference: RasterSpec,
    array: np.ndarray,
    dtype: int,
    nodata: float | None,
) -> None:
    gdal_module, _, _ = _require_osgeo()
    _ensure_parent_dir(out_path)
    driver = gdal_module.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GDAL GTiff driver is unavailable.")

    out_ds = driver.Create(
        out_path,
        reference.cols,
        reference.rows,
        1,
        dtype,
        options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
    )
    if out_ds is None:
        raise RuntimeError(f"Could not create output raster: {out_path}")

    out_ds.SetGeoTransform(reference.geotransform)
    out_ds.SetProjection(reference.projection_wkt)
    band = out_ds.GetRasterBand(1)
    band.WriteArray(array)
    if nodata is not None:
        band.SetNoDataValue(float(nodata))
    band.FlushCache()
    out_ds = None


def _derive_related_output_path(base_raster_path: str, replacement: str) -> str:
    directory = os.path.dirname(os.path.abspath(base_raster_path))
    base_name = os.path.basename(base_raster_path)
    stem, ext = os.path.splitext(base_name)
    return os.path.join(directory, f"{stem}_{replacement}{ext or '.tif'}")


def _write_mask_shapefile(mask_raster_path: str, output_shapefile: str, label: str) -> None:
    gdal_module, ogr_module, _ = _require_osgeo()
    _ensure_parent_dir(output_shapefile)
    shp_driver = ogr_module.GetDriverByName("ESRI Shapefile")
    if shp_driver is None:
        raise RuntimeError("OGR ESRI Shapefile driver is unavailable.")

    if os.path.exists(output_shapefile):
        shp_driver.DeleteDataSource(output_shapefile)

    mask_ds = gdal_module.Open(mask_raster_path, gdal_module.GA_ReadOnly)
    if mask_ds is None:
        raise RuntimeError(f"Could not open {label} raster for polygonize: {mask_raster_path}")

    mask_band = mask_ds.GetRasterBand(1)
    if mask_band is None:
        raise RuntimeError(f"{label.capitalize()} raster is missing band 1: {mask_raster_path}")

    shp_ds = shp_driver.CreateDataSource(output_shapefile)
    if shp_ds is None:
        raise RuntimeError(f"Could not create {label} shapefile: {output_shapefile}")

    out_srs = _srs_from_wkt(mask_ds.GetProjectionRef() or "")
    layer_name = os.path.splitext(os.path.basename(output_shapefile))[0]
    layer = shp_ds.CreateLayer(layer_name, out_srs, ogr_module.wkbPolygon)
    if layer is None:
        raise RuntimeError(f"Could not create layer in shapefile: {output_shapefile}")

    if layer.CreateField(ogr_module.FieldDefn("value", ogr_module.OFTInteger)) != 0:
        raise RuntimeError(f"Failed creating 'value' field in {label} shapefile.")

    err = gdal_module.Polygonize(mask_band, None, layer, 0, [], callback=None)
    if err != 0:
        raise RuntimeError(f"gdal.Polygonize failed for {label} shapefile.")

    delete_fids: list[int] = []
    layer.ResetReading()
    feature = layer.GetNextFeature()
    while feature is not None:
        if int(feature.GetField("value") or 0) != 1:
            delete_fids.append(int(feature.GetFID()))
        feature = layer.GetNextFeature()
    for fid in delete_fids:
        layer.DeleteFeature(fid)

    shp_ds = None
    mask_ds = None


def _array_min_max(array: np.ndarray, valid_mask: np.ndarray) -> tuple[float | None, float | None]:
    if int(np.count_nonzero(valid_mask)) == 0:
        return None, None
    values = np.asarray(array[valid_mask], dtype=np.float64)
    return float(np.min(values)), float(np.max(values))


def _stats_from_values(values: Any) -> dict[str, float | None]:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return {"min": None, "max": None, "mean": None}
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def _inside(row: int, col: int, rows: int, cols: int) -> bool:
    return 0 <= row < rows and 0 <= col < cols


def _source_adjacent_runout_anchors(cleaned_runout_mask: np.ndarray, source_mask: np.ndarray) -> np.ndarray:
    cleaned = np.asarray(cleaned_runout_mask, dtype=bool)
    source = np.asarray(source_mask, dtype=bool)
    if cleaned.shape != source.shape:
        raise ValueError("cleaned runout and source mask must share the same shape.")

    anchors = cleaned & source
    rows, cols = cleaned.shape
    for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
        runout_rows = slice(max(0, -row_offset), min(rows, rows - row_offset))
        runout_cols = slice(max(0, -col_offset), min(cols, cols - col_offset))
        source_rows = slice(max(0, row_offset), min(rows, rows + row_offset))
        source_cols = slice(max(0, col_offset), min(cols, cols + col_offset))
        anchors[runout_rows, runout_cols] |= (
            cleaned[runout_rows, runout_cols] & source[source_rows, source_cols]
        )
    return anchors


def _source_connected_runout_mask(cleaned_runout_mask: np.ndarray, source_mask: np.ndarray) -> np.ndarray:
    """Keep only cleaned-runout components that touch or are adjacent to source cells."""
    cleaned = np.asarray(cleaned_runout_mask, dtype=bool)
    anchors = _source_adjacent_runout_anchors(cleaned, source_mask)
    if not bool(np.any(cleaned)) or not bool(np.any(anchors)):
        return np.zeros_like(cleaned, dtype=bool)

    try:
        from scipy import ndimage  # type: ignore

        label_input = np.ascontiguousarray(cleaned, dtype=np.uint8)
        labels, _label_count = ndimage.label(
            label_input,
            structure=np.ones((3, 3), dtype=bool),
        )
        anchor_labels = np.unique(labels[anchors])
        anchor_labels = anchor_labels[anchor_labels != 0]
        if anchor_labels.size == 0:
            return np.zeros_like(cleaned, dtype=bool)
        return np.isin(labels, anchor_labels)
    except ImportError:
        return _source_connected_runout_mask_numpy(cleaned, anchors)
    except ValueError as exc:
        if "Numpy IterAllButAxis requires an ndarray" not in str(exc):
            raise
        # Some QGIS/OSGeo SciPy builds have an ndimage ABI defect even for a
        # contiguous ndarray. Keep the fast array-based fallback deterministic.
        return _source_connected_runout_mask_numpy(cleaned, anchors)


def _source_connected_runout_mask_numpy(cleaned: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    """Vectorized connectivity fallback constrained to the cleaned runout mask."""
    connected = np.asarray(anchors, dtype=bool) & np.asarray(cleaned, dtype=bool)
    rows, cols = connected.shape
    while True:
        expanded = connected.copy()
        for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
            target_rows = slice(max(0, row_offset), min(rows, rows + row_offset))
            target_cols = slice(max(0, col_offset), min(cols, cols + col_offset))
            source_rows = slice(max(0, -row_offset), min(rows, rows - row_offset))
            source_cols = slice(max(0, -col_offset), min(cols, cols - col_offset))
            expanded[target_rows, target_cols] |= connected[source_rows, source_cols]
        expanded &= cleaned
        if bool(np.array_equal(expanded, connected)):
            return connected
        connected = expanded


def _source_connected_runout_mask_deque(cleaned: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    """Reference flood-fill implementation kept for small tests and debugging."""
    if not bool(np.any(cleaned)) or not bool(np.any(anchors)):
        return np.zeros_like(cleaned, dtype=bool)
    cleaned = np.asarray(cleaned, dtype=bool)
    rows, cols = cleaned.shape
    connected = np.zeros_like(cleaned, dtype=bool)
    pending: deque[tuple[int, int]] = deque((int(r), int(c)) for r, c in np.argwhere(anchors))
    while pending:
        row, col = pending.popleft()
        if bool(connected[row, col]) or not bool(cleaned[row, col]):
            continue
        connected[row, col] = True
        for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
            next_row = int(row + row_offset)
            next_col = int(col + col_offset)
            if _inside(next_row, next_col, rows, cols) and not bool(connected[next_row, next_col]):
                if bool(cleaned[next_row, next_col]):
                    pending.append((next_row, next_col))
    return connected


def _build_edge_seed_mask(
    *,
    eligible_seed_mask: np.ndarray,
    original_runout_mask: np.ndarray,
    source_mask: np.ndarray,
    stream_mask: np.ndarray,
    dem_valid: np.ndarray,
) -> np.ndarray:
    """Retain DFI-qualified non-source, non-stream cells on the cleaned runout edge."""
    eligible_seed_mask = np.asarray(eligible_seed_mask, dtype=bool)
    original_runout_mask = np.asarray(original_runout_mask, dtype=bool)
    source_mask = np.asarray(source_mask, dtype=bool)
    stream_mask = np.asarray(stream_mask, dtype=bool)
    dem_valid = np.asarray(dem_valid, dtype=bool)
    seed_mask = np.zeros_like(eligible_seed_mask, dtype=bool)
    rows, cols = eligible_seed_mask.shape

    candidate_seed_mask = eligible_seed_mask & ~source_mask & ~stream_mask & dem_valid
    for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
        source_rows = slice(max(0, -row_offset), min(rows, rows - row_offset))
        source_cols = slice(max(0, -col_offset), min(cols, cols - col_offset))
        neighbor_rows = slice(max(0, row_offset), min(rows, rows + row_offset))
        neighbor_cols = slice(max(0, col_offset), min(cols, cols + col_offset))

        touches_edge = (
            dem_valid[neighbor_rows, neighbor_cols]
            & ~original_runout_mask[neighbor_rows, neighbor_cols]
        )
        seed_mask[source_rows, source_cols] |= (
            candidate_seed_mask[source_rows, source_cols] & touches_edge
        )

    return seed_mask


def _validate_source_mask_values(source_mask_array: np.ndarray, source_mask_valid: np.ndarray) -> None:
    _validate_binary_mask_values(source_mask_array, source_mask_valid, "source_mask_raster")


def _validate_stream_mask_values(stream_mask_array: np.ndarray, stream_mask_valid: np.ndarray) -> None:
    _validate_nonnegative_mask_values(stream_mask_array, stream_mask_valid, "stream_mask_raster")


def _validate_nonnegative_mask_values(mask_array: np.ndarray, mask_valid: np.ndarray, label: str) -> None:
    if not np.any(mask_valid):
        raise ValueError(f"{label} has no valid cells after nodata masking.")
    valid_values = np.asarray(mask_array[mask_valid], dtype=np.float64)
    unexpected = valid_values[valid_values < 0.0]
    if unexpected.size:
        unique = np.unique(unexpected)
        preview = ", ".join(str(float(value)) for value in unique[:10])
        raise ValueError(
            f"{label} must contain non-negative mask values or nodata. "
            f"Unexpected values include: {preview}"
        )


def _validate_binary_mask_values(mask_array: np.ndarray, mask_valid: np.ndarray, label: str) -> None:
    if not np.any(mask_valid):
        raise ValueError(f"{label} has no valid cells after nodata masking.")
    valid_values = np.asarray(mask_array[mask_valid], dtype=np.float64)
    unexpected = valid_values[
        ~(
            np.isclose(valid_values, 0.0, atol=0.0, rtol=0.0)
            | np.isclose(valid_values, 1.0, atol=0.0, rtol=0.0)
        )
    ]
    if unexpected.size:
        unique = np.unique(unexpected)
        preview = ", ".join(str(float(value)) for value in unique[:10])
        raise ValueError(
            f"{label} must contain only binary 0/1 values or nodata. "
            f"Unexpected values include: {preview}"
        )


def _compute_neighbor_offsets(dx: float, dy: float, spread_radius_m: float) -> list[tuple[int, int, float]]:
    if spread_radius_m <= 0.0:
        return []

    max_row_offset = int(math.ceil(float(spread_radius_m) / float(dy)))
    max_col_offset = int(math.ceil(float(spread_radius_m) / float(dx)))
    offsets: list[tuple[int, int, float]] = []
    for row_offset in range(-max_row_offset, max_row_offset + 1):
        for col_offset in range(-max_col_offset, max_col_offset + 1):
            if row_offset == 0 and col_offset == 0:
                continue
            distance = math.sqrt(
                (float(col_offset) * float(dx)) * (float(col_offset) * float(dx))
                + (float(row_offset) * float(dy)) * (float(row_offset) * float(dy))
            )
            if distance <= float(spread_radius_m) + GT_TOLERANCE:
                offsets.append((int(row_offset), int(col_offset), float(distance)))
    offsets.sort(key=lambda item: (item[2], abs(item[0]), abs(item[1]), item[0], item[1]))
    return offsets


def _validate_source_contributing_area_values(
    source_contributing_area_array: np.ndarray,
    source_contributing_area_valid: np.ndarray,
) -> None:
    if not np.any(source_contributing_area_valid):
        raise ValueError("source_contributing_area_raster has no valid cells after nodata masking.")
    valid_values = np.asarray(source_contributing_area_array[source_contributing_area_valid], dtype=np.float64)
    if np.any(valid_values < 0.0):
        bad = valid_values[valid_values < 0.0]
        raise ValueError(
            "source_contributing_area_raster must contain non-negative values or nodata. "
            f"Found negative value {float(bad[0])!r}."
        )


def _validate_dfi_values(dfi_array: np.ndarray, dfi_valid: np.ndarray) -> None:
    if not np.any(dfi_valid):
        raise ValueError("dfi_raster has no valid cells after nodata masking.")
    valid_values = np.asarray(dfi_array[dfi_valid], dtype=np.float64)
    outside = valid_values[(valid_values < 0.0) | (valid_values > 1.0)]
    if outside.size:
        raise ValueError(
            "dfi_raster must contain probabilities within [0, 1] or nodata. "
            f"Found value {float(outside[0])!r}."
        )


def _compute_source_contribution_from_area(
    *,
    source_contributing_area_value: float,
    cell_area_m2: float,
    min_spread_radius_m: float,
    max_spread_radius_m: float,
    source_contributing_area_reference: float,
) -> SourceContribution:
    source_contributing_area = max(float(source_contributing_area_value), 0.0) * float(cell_area_m2)
    if source_contributing_area <= 0.0:
        return SourceContribution(
            source_contributing_area=0.0,
            source_contribution_index=0.0,
            computed_spread_radius_m=float(min_spread_radius_m),
            has_zero_source_area=True,
        )

    source_contribution_index = min(source_contributing_area / float(source_contributing_area_reference), 1.0)
    source_contribution_index = float(min(max(source_contribution_index, 0.0), 1.0))
    computed_spread_radius_m = float(min_spread_radius_m) + source_contribution_index * (
        float(max_spread_radius_m) - float(min_spread_radius_m)
    )
    return SourceContribution(
        source_contributing_area=float(source_contributing_area),
        source_contribution_index=float(source_contribution_index),
        computed_spread_radius_m=float(computed_spread_radius_m),
        has_zero_source_area=False,
    )


def _candidate_is_better(
    *,
    current_min_elev_diff: float,
    current_source_index: int,
    new_min_elev_diff: float,
    new_source_index: int,
) -> bool:
    if int(current_source_index) < 0:
        return True
    if not math.isfinite(float(current_min_elev_diff)):
        return True
    if float(new_min_elev_diff) < float(current_min_elev_diff) - ELEVATION_TIE_TOLERANCE:
        return True
    if abs(float(new_min_elev_diff) - float(current_min_elev_diff)) <= ELEVATION_TIE_TOLERANCE:
        if int(new_source_index) < int(current_source_index):
            return True
    return False


def _collect_connected_local_candidates(
    *,
    source_row: int,
    source_col: int,
    eligible_cells: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    if not eligible_cells:
        return set()

    connected: set[tuple[int, int]] = set()
    pending: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()
    for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
        neighbor = (int(source_row + row_offset), int(source_col + col_offset))
        if neighbor in eligible_cells and neighbor not in visited:
            pending.append(neighbor)
            visited.add(neighbor)

    while pending:
        current = pending.popleft()
        connected.add(current)
        current_row, current_col = current
        for row_offset, col_offset in EIGHT_NEIGHBOR_STEPS:
            neighbor = (int(current_row + row_offset), int(current_col + col_offset))
            if neighbor not in eligible_cells or neighbor in visited:
                continue
            pending.append(neighbor)
            visited.add(neighbor)

    return connected


def run_post_depositional_spread_core(
    *,
    runout_array: np.ndarray,
    runout_nodata: float | None,
    dfi_array: np.ndarray,
    dfi_nodata: float | None,
    dem_array: np.ndarray,
    dem_nodata: float | None,
    source_mask_array: np.ndarray,
    source_mask_nodata: float | None,
    stream_mask_array: np.ndarray,
    stream_mask_nodata: float | None,
    source_contributing_area_array: np.ndarray,
    source_contributing_area_nodata: float | None,
    dx: float,
    dy: float,
    dfi_threshold: float,
    max_relative_rise_m: float,
    min_runout_source_area_m2: float,
    min_spread_radius_m: float,
    max_spread_radius_m: float,
    source_contributing_area_reference: float,
    input_paths: dict[str, str] | None = None,
    output_paths: dict[str, str] | None = None,
    geotransform: tuple[float, float, float, float, float, float] | None = None,
    crs_authority: str | None = None,
    crs_wkt: str = "",
    collect_debug_rasters: bool = False,
) -> NativeSpreadResult:
    """Compute the seed-driven post-depositional spread masks."""

    # The core spread logic expands only from cleaned Step 4 runout seed cells.
    # Accepted spread cells do not become new seeds, so this remains local
    # post-processing rather than a second full routing model.
    runout_array = np.asarray(runout_array)
    dfi_array = np.asarray(dfi_array)
    dem_array = np.asarray(dem_array)
    source_mask_array = np.asarray(source_mask_array)
    stream_mask_array = np.asarray(stream_mask_array)
    source_contributing_area_array = np.asarray(source_contributing_area_array)

    if runout_array.shape != dfi_array.shape or runout_array.shape != dem_array.shape:
        raise ValueError("runout, DFI, and DEM arrays must share the same shape.")
    if runout_array.shape != source_mask_array.shape:
        raise ValueError("source mask array must align with the runout grid.")
    if runout_array.shape != stream_mask_array.shape:
        raise ValueError("stream mask array must align with the runout grid.")
    if runout_array.shape != source_contributing_area_array.shape:
        raise ValueError("source contributing area array must align with the runout grid.")
    if dx <= 0.0 or dy <= 0.0:
        raise ValueError("Pixel sizes dx and dy must be positive.")
    _validate_loaded_values(
        dfi_threshold=float(dfi_threshold),
        max_relative_rise_m=float(max_relative_rise_m),
        source_contributing_area_reference=float(source_contributing_area_reference),
        min_runout_source_area_m2=float(min_runout_source_area_m2),
        min_spread_radius_m=float(min_spread_radius_m),
        max_spread_radius_m=float(max_spread_radius_m),
    )

    rows, cols = runout_array.shape
    runout_valid = _valid_mask(runout_array, runout_nodata)
    dfi_valid = _valid_mask(dfi_array, dfi_nodata)
    dem_valid = _valid_mask(dem_array, dem_nodata)
    source_mask_valid = _valid_mask(source_mask_array, source_mask_nodata)
    stream_mask_valid = _valid_mask(stream_mask_array, stream_mask_nodata)
    source_contributing_area_valid = _valid_mask(source_contributing_area_array, source_contributing_area_nodata)
    _validate_binary_mask_values(runout_array, runout_valid, "runout_mask_raster")
    _validate_source_mask_values(source_mask_array, source_mask_valid)
    _validate_stream_mask_values(stream_mask_array, stream_mask_valid)
    _validate_source_contributing_area_values(source_contributing_area_array, source_contributing_area_valid)
    _validate_dfi_values(dfi_array, dfi_valid)

    cell_area_m2 = float(dx) * float(dy)
    original_runout_mask = runout_valid & (runout_array > 0.0)
    runout_without_source_area = original_runout_mask & ~source_contributing_area_valid
    runout_without_source_area_count = int(np.count_nonzero(runout_without_source_area))
    if runout_without_source_area_count:
        raise ValueError(
            "source_contributing_area_raster has nodata or non-finite values at "
            f"{runout_without_source_area_count} Step 4 runout cells. "
            "Step 5 will not interpret missing contributing area as zero."
        )
    source_mask_bool = source_mask_valid & (source_mask_array > 0.0)
    stream_mask_bool = stream_mask_valid & (stream_mask_array > 0.0)
    raw_source_count_threshold = float(min_runout_source_area_m2) / cell_area_m2
    area_removed_runout_mask_bool = original_runout_mask & (
        source_contributing_area_array < raw_source_count_threshold
    )
    area_cleaned_runout_mask_bool = original_runout_mask & ~area_removed_runout_mask_bool
    source_connected_runout_mask_bool = _source_connected_runout_mask(area_cleaned_runout_mask_bool, source_mask_bool)
    disconnected_runout_mask_bool = area_cleaned_runout_mask_bool & ~source_connected_runout_mask_bool
    removed_runout_mask_bool = area_removed_runout_mask_bool | disconnected_runout_mask_bool
    cleaned_runout_mask_bool = source_connected_runout_mask_bool
    dfi_eligible_runout_mask = cleaned_runout_mask_bool & dfi_valid & dem_valid & (dfi_array > float(dfi_threshold))
    edge_seed_mask = _build_edge_seed_mask(
        eligible_seed_mask=dfi_eligible_runout_mask,
        original_runout_mask=cleaned_runout_mask_bool,
        source_mask=source_mask_bool,
        stream_mask=stream_mask_bool,
        dem_valid=dem_valid,
    )
    max_neighbor_offsets = _compute_neighbor_offsets(dx=dx, dy=dy, spread_radius_m=max_spread_radius_m)
    seed_mask_bool = edge_seed_mask

    spread_mask = np.zeros((rows, cols), dtype=np.uint8)
    seed_mask = np.asarray(seed_mask_bool, dtype=np.uint8)
    cleaned_runout_mask = np.asarray(cleaned_runout_mask_bool, dtype=np.uint8)
    removed_runout_mask = np.asarray(removed_runout_mask_bool, dtype=np.uint8)

    min_elev_diff = np.full((rows, cols), float(FLOAT32_NODATA), dtype=np.float64)
    source_index = np.full((rows, cols), INT32_NODATA, dtype=np.int32)
    source_contributing_area = (
        np.full((rows, cols), FLOAT32_NODATA, dtype=np.float32)
        if collect_debug_rasters
        else None
    )
    source_contribution_index = (
        np.full((rows, cols), FLOAT32_NODATA, dtype=np.float32)
        if collect_debug_rasters
        else None
    )
    computed_spread_radius = (
        np.full((rows, cols), FLOAT32_NODATA, dtype=np.float32)
        if collect_debug_rasters
        else None
    )

    rejection_counts: dict[str, int] = {
        "already_in_runout": 0,
        "invalid_dfi": 0,
        "dfi_not_above_threshold": 0,
        "invalid_dem": 0,
        "too_high_above_seed": 0,
        "out_of_bounds": 0,
        "disconnected_from_source": 0,
    }

    seed_rows, seed_cols = np.nonzero(seed_mask_bool)
    seed_count = int(seed_rows.size)
    seed_raw_source_counts = np.zeros(seed_count, dtype=np.float64)
    if seed_count:
        seed_values_valid = source_contributing_area_valid[seed_rows, seed_cols]
        seed_raw_source_counts[seed_values_valid] = np.maximum(
            np.asarray(
                source_contributing_area_array[seed_rows[seed_values_valid], seed_cols[seed_values_valid]],
                dtype=np.float64,
            ),
            0.0,
        )
    seed_source_contributing_areas = seed_raw_source_counts * cell_area_m2
    seed_source_contribution_indices = np.clip(
        seed_source_contributing_areas / float(source_contributing_area_reference),
        0.0,
        1.0,
    )
    seed_spread_radii = float(min_spread_radius_m) + seed_source_contribution_indices * (
        float(max_spread_radius_m) - float(min_spread_radius_m)
    )
    seeds_with_zero_contributing_source_area = int(
        np.count_nonzero(seed_source_contributing_areas <= 0.0)
    )
    seeds_assigned_min_radius = int(
        np.count_nonzero(
            seed_spread_radii <= float(min_spread_radius_m) + ELEVATION_TIE_TOLERANCE
        )
    )
    near_max_tolerance = 0.01 * (float(max_spread_radius_m) - float(min_spread_radius_m))
    seeds_assigned_max_or_near_max_radius = int(
        np.count_nonzero(
            seed_spread_radii
            >= float(max_spread_radius_m) - near_max_tolerance - ELEVATION_TIE_TOLERANCE
        )
    )
    if (
        seed_count
        and source_contributing_area is not None
        and source_contribution_index is not None
        and computed_spread_radius is not None
    ):
        source_contributing_area[seed_rows, seed_cols] = seed_source_contributing_areas.astype(np.float32)
        source_contribution_index[seed_rows, seed_cols] = seed_source_contribution_indices.astype(np.float32)
        computed_spread_radius[seed_rows, seed_cols] = seed_spread_radii.astype(np.float32)

    candidate_evaluations = 0
    source_cells_processed = seed_count
    offset_rows = np.asarray([item[0] for item in max_neighbor_offsets], dtype=np.int32)
    offset_cols = np.asarray([item[1] for item in max_neighbor_offsets], dtype=np.int32)
    offset_distances = np.asarray([item[2] for item in max_neighbor_offsets], dtype=np.float64)
    max_local_row = int(np.max(np.abs(offset_rows))) if offset_rows.size else 0
    max_local_col = int(np.max(np.abs(offset_cols))) if offset_cols.size else 0
    local_rows = 2 * max_local_row + 1
    local_cols = 2 * max_local_col + 1
    center_local_row = max_local_row
    center_local_col = max_local_col
    eligible_local = np.zeros((local_rows, local_cols), dtype=bool)
    connected_local = np.zeros((local_rows, local_cols), dtype=bool)
    queue_capacity = max(local_rows * local_cols, 1)
    queue_rows = np.empty(queue_capacity, dtype=np.int32)
    queue_cols = np.empty(queue_capacity, dtype=np.int32)

    for seed_index in range(seed_count):
        source_r = int(seed_rows[seed_index])
        source_c = int(seed_cols[seed_index])
        source_flat_index = int(source_r * cols + source_c)
        source_elev = float(dem_array[source_r, source_c])
        offset_count = int(
            np.searchsorted(
                offset_distances,
                float(seed_spread_radii[seed_index]) + GT_TOLERANCE,
                side="right",
            )
        )
        candidate_evaluations += offset_count
        if offset_count == 0:
            continue

        candidate_rows = source_r + offset_rows[:offset_count]
        candidate_cols = source_c + offset_cols[:offset_count]
        in_bounds = (
            (candidate_rows >= 0)
            & (candidate_rows < rows)
            & (candidate_cols >= 0)
            & (candidate_cols < cols)
        )
        rejection_counts["out_of_bounds"] += int(offset_count - np.count_nonzero(in_bounds))
        candidate_rows = candidate_rows[in_bounds]
        candidate_cols = candidate_cols[in_bounds]
        if candidate_rows.size == 0:
            continue

        active = np.ones(candidate_rows.size, dtype=bool)
        rejected = active & cleaned_runout_mask_bool[candidate_rows, candidate_cols]
        rejection_counts["already_in_runout"] += int(np.count_nonzero(rejected))
        active &= ~rejected

        rejected = active & ~dfi_valid[candidate_rows, candidate_cols]
        rejection_counts["invalid_dfi"] += int(np.count_nonzero(rejected))
        active &= ~rejected

        rejected = active & (
            np.asarray(dfi_array[candidate_rows, candidate_cols], dtype=np.float64)
            <= float(dfi_threshold)
        )
        rejection_counts["dfi_not_above_threshold"] += int(np.count_nonzero(rejected))
        active &= ~rejected

        rejected = active & ~dem_valid[candidate_rows, candidate_cols]
        rejection_counts["invalid_dem"] += int(np.count_nonzero(rejected))
        active &= ~rejected

        candidate_elevations = np.asarray(
            dem_array[candidate_rows, candidate_cols],
            dtype=np.float64,
        )
        rejected = active & (
            candidate_elevations
            > source_elev + float(max_relative_rise_m) + ELEVATION_TIE_TOLERANCE
        )
        rejection_counts["too_high_above_seed"] += int(np.count_nonzero(rejected))
        active &= ~rejected

        eligible_rows = candidate_rows[active]
        eligible_cols = candidate_cols[active]
        eligible_count = int(eligible_rows.size)
        if eligible_count == 0:
            continue

        eligible_local.fill(False)
        connected_local.fill(False)
        eligible_local[
            eligible_rows - source_r + center_local_row,
            eligible_cols - source_c + center_local_col,
        ] = True

        queue_head = 0
        queue_tail = 0
        for row_step, col_step in EIGHT_NEIGHBOR_STEPS:
            local_row = center_local_row + row_step
            local_col = center_local_col + col_step
            if (
                0 <= local_row < local_rows
                and 0 <= local_col < local_cols
                and bool(eligible_local[local_row, local_col])
                and not bool(connected_local[local_row, local_col])
            ):
                connected_local[local_row, local_col] = True
                queue_rows[queue_tail] = local_row
                queue_cols[queue_tail] = local_col
                queue_tail += 1

        while queue_head < queue_tail:
            current_local_row = int(queue_rows[queue_head])
            current_local_col = int(queue_cols[queue_head])
            queue_head += 1
            for row_step, col_step in EIGHT_NEIGHBOR_STEPS:
                local_row = current_local_row + row_step
                local_col = current_local_col + col_step
                if (
                    0 <= local_row < local_rows
                    and 0 <= local_col < local_cols
                    and bool(eligible_local[local_row, local_col])
                    and not bool(connected_local[local_row, local_col])
                ):
                    connected_local[local_row, local_col] = True
                    queue_rows[queue_tail] = local_row
                    queue_cols[queue_tail] = local_col
                    queue_tail += 1

        rejection_counts["disconnected_from_source"] += int(eligible_count - queue_tail)
        for queue_index in range(queue_tail):
            candidate_r = int(
                source_r + int(queue_rows[queue_index]) - center_local_row
            )
            candidate_c = int(
                source_c + int(queue_cols[queue_index]) - center_local_col
            )
            elevation_diff = float(dem_array[candidate_r, candidate_c]) - source_elev
            if _candidate_is_better(
                current_min_elev_diff=float(min_elev_diff[candidate_r, candidate_c]),
                current_source_index=int(source_index[candidate_r, candidate_c]),
                new_min_elev_diff=elevation_diff,
                new_source_index=source_flat_index,
            ):
                spread_mask[candidate_r, candidate_c] = 1
                min_elev_diff[candidate_r, candidate_c] = float(elevation_diff)
                source_index[candidate_r, candidate_c] = source_flat_index

    combined_mask = np.asarray(cleaned_runout_mask_bool | (spread_mask > 0), dtype=np.uint8)

    dfi_min, dfi_max = _array_min_max(dfi_array, dfi_valid)
    original_runout_cells = int(np.count_nonzero(original_runout_mask))
    area_removed_runout_cells = int(np.count_nonzero(area_removed_runout_mask_bool))
    disconnected_runout_cells = int(np.count_nonzero(disconnected_runout_mask_bool))
    removed_runout_cells = int(np.count_nonzero(removed_runout_mask_bool))
    cleaned_runout_cells = int(np.count_nonzero(cleaned_runout_mask_bool))
    removed_runout_percent = (
        (100.0 * float(removed_runout_cells) / float(original_runout_cells))
        if original_runout_cells > 0
        else None
    )
    warnings: list[str] = []
    if seeds_with_zero_contributing_source_area:
        warnings.append(
            f"{seeds_with_zero_contributing_source_area} seed cells had zero contributing "
            "source area and were assigned min_spread_radius_m."
        )
    summary = SpreadSummary(
        input_paths=dict(input_paths or {}),
        output_paths=dict(output_paths or {}),
        rows=int(rows),
        cols=int(cols),
        pixel_size=(float(dx), float(dy)),
        geotransform=geotransform if geotransform is not None else (0.0, float(dx), 0.0, 0.0, 0.0, -float(dy)),
        crs_authority=crs_authority,
        crs_wkt=str(crs_wkt or ""),
        dfi_threshold=float(dfi_threshold),
        max_relative_rise_m=float(max_relative_rise_m),
        min_spread_radius_m=float(min_spread_radius_m),
        max_spread_radius_m=float(max_spread_radius_m),
        source_contributing_area_reference=float(source_contributing_area_reference),
        spread_mode="runout_cleanup_edge_seed_source_contribution_relief_connected",
        min_runout_source_area_m2=float(min_runout_source_area_m2),
        original_runout_cells=original_runout_cells,
        area_removed_runout_cells=area_removed_runout_cells,
        disconnected_runout_cells=disconnected_runout_cells,
        removed_runout_cells=removed_runout_cells,
        removed_runout_percent=removed_runout_percent,
        cleaned_runout_cells=cleaned_runout_cells,
        valid_runout_cells=cleaned_runout_cells,
        seed_cells=int(seed_count),
        seeds_with_zero_contributing_source_area=int(seeds_with_zero_contributing_source_area),
        seeds_assigned_min_radius=int(seeds_assigned_min_radius),
        seeds_assigned_max_or_near_max_radius=int(seeds_assigned_max_or_near_max_radius),
        spread_cells_added=int(np.count_nonzero(spread_mask)),
        combined_footprint_cells=int(np.count_nonzero(combined_mask)),
        source_cells_processed=int(source_cells_processed),
        candidate_evaluations=int(candidate_evaluations),
        neighbor_offset_count=int(len(max_neighbor_offsets)),
        neighbor_offsets=[
            [int(row_offset), int(col_offset), float(distance_m)]
            for row_offset, col_offset, distance_m in max_neighbor_offsets
        ],
        dfi_stats={"min": dfi_min, "max": dfi_max},
        source_contributing_area_stats=_stats_from_values(seed_source_contributing_areas),
        source_contribution_index_stats=_stats_from_values(seed_source_contribution_indices),
        computed_spread_radius_stats=_stats_from_values(seed_spread_radii),
        rejection_counts={key: int(value) for key, value in rejection_counts.items()},
        warnings=warnings,
        notes=[
            "Step 5 first removes Step 4 runout cells whose converted source contributing area is less than min_runout_source_area_m2.",
            "Step 5 then removes remaining runout cells that are not 8-connected to the source mask through the retained runout footprint.",
            "The cleaned runout mask is then used for seed selection, candidate exclusion, and the final combined footprint.",
            "This implementation is seed-driven only: seed candidates are DFI-qualified non-source edge cells from the cleaned runout mask.",
            "A cleaned runout cell becomes an edge seed when it touches at least one valid non-runout neighbor.",
            "Runout cells marked by the source mask are excluded from seed selection and therefore do not initiate post-depositional spread.",
            "Runout cells marked by the stream mask are excluded from seed selection to avoid stream-accumulation cells forcing inflated spread radii.",
            "Spread radius is controlled by a precomputed TauDEM weighted D-infinity source contributing-cell-count raster.",
            "The source contributing-area raster stores the D-infinity-weighted number of contributing source cells; Step 5 converts it to square meters by multiplying by dx * dy.",
            "Spread is allowed in all directions within the computed source-contribution radius; there is no plan-curvature confinement or directional fan filter.",
            "Final spread remains constrained by DFI, DEM validity, seed-relative elevation rise, radius, and local connectivity filters.",
            "Candidate connectivity is enforced only after DFI, DEM, and radius eligibility are satisfied.",
            "The relative relief rule is seed-relative: a candidate cell cannot join if it is more than max_relative_rise_m above its edge seed cell.",
            "Seeds with zero source contributing area are assigned min_spread_radius_m and reported as warnings; nodata at any Step 4 runout cell is an input error.",
            "The precomputed neighbor-offset list spans max_spread_radius_m, and each source evaluates only the subset within its own computed radius.",
        ],
        metadata={
            "runout_cleanup_rule": "Remove runout cells when raw_source_contributing_cell_count * dx * dy < min_runout_source_area_m2.",
            "runout_source_connectivity_rule": "After source-area cleanup, keep only retained runout components that touch or are 8-neighbor-adjacent to source-mask cells.",
            "seed_selection": "DFI-qualified non-source, non-stream cleaned-runout cells that touch at least one valid outside-runout neighbor.",
            "radius_conditioning": "source_contribution_index = min((raw_source_contributing_cell_count * dx * dy) / source_contributing_area_reference, 1.0), clamped to [0, 1].",
            "dfi_eligible_runout_cells_before_edge_filter": int(np.count_nonzero(dfi_eligible_runout_mask)),
            "source_runout_cells_excluded_from_seed_selection": int(np.count_nonzero(cleaned_runout_mask_bool & source_mask_bool)),
            "stream_runout_cells_excluded_from_seed_selection": int(np.count_nonzero(cleaned_runout_mask_bool & stream_mask_bool)),
            "edge_seed_rule": "A seed must touch at least one valid non-runout 8-neighbor cell.",
            "debug_rasters_store_values_at": "seed_cells",
            "conditioning_statistics_scope": "edge_seed_cells",
            "source_mask_contract": "Binary source mask only: 1 = source, 0 = non-source.",
            "source_contributing_area_contract": "Non-negative TauDEM D-infinity weighted source-cell-count raster derived from the binary source mask; converted to square meters internally as raw_count * dx * dy.",
            "runout_cells_missing_source_contributing_area": int(runout_without_source_area_count),
            "valid_dfi_cells": int(np.count_nonzero(dfi_valid)),
            "invalid_dfi_cells": int(dfi_valid.size - np.count_nonzero(dfi_valid)),
        },
    )

    return NativeSpreadResult(
        spread_mask=spread_mask,
        combined_mask=combined_mask,
        seed_mask=seed_mask,
        cleaned_runout_mask=cleaned_runout_mask,
        removed_runout_mask=removed_runout_mask,
        min_elev_diff=min_elev_diff,
        source_index=source_index,
        source_contributing_area=source_contributing_area,
        source_contribution_index=source_contribution_index,
        computed_spread_radius=computed_spread_radius,
        summary=summary,
    )


def prepare_staged_output_bundle(
    params: PostDepositionalSpreadParams,
) -> StagedOutputBundle:
    token = uuid.uuid4().hex
    stage_dirs_by_parent: dict[Path, Path] = {}
    replacements: dict[str, str] = {}

    field_names = list(CORE_OUTPUT_PARAM_FIELDS)
    for field_name in ("output_spread_mask_shapefile", "output_combined_mask_shapefile"):
        if str(getattr(params, field_name)).strip():
            field_names.append(field_name)
    if params.write_debug_rasters:
        for field_name in ("output_min_elev_diff", "output_source_row", "output_source_col"):
            if str(getattr(params, field_name)).strip():
                field_names.append(field_name)

    for field_name in field_names:
        official = Path(str(getattr(params, field_name))).expanduser().resolve()
        stage_dir = stage_dirs_by_parent.get(official.parent)
        if stage_dir is None:
            official.parent.mkdir(parents=True, exist_ok=True)
            stage_dir = official.parent / f".step5-stage-{token}"
            stage_dir.mkdir(parents=False, exist_ok=False)
            stage_dirs_by_parent[official.parent] = stage_dir
        replacements[field_name] = str(stage_dir / official.name)

    staged_params = replace(params, **replacements)
    official_outputs = step5_output_paths(params)
    staged_outputs = step5_output_paths(staged_params)
    staged_to_official = {
        str(Path(staged_outputs[key]).resolve()): str(Path(official).resolve())
        for key, official in official_outputs.items()
        if key in staged_outputs
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


def _artifact_members(path: Path) -> list[Path]:
    if path.suffix.lower() == ".shp":
        candidates = [path.with_suffix(extension) for extension in SHAPEFILE_EXTENSIONS]
    else:
        candidates = [path]
        candidates.extend(
            sorted(
                candidate
                for candidate in path.parent.glob(path.name + ".*")
                if candidate.is_file()
            )
        )
    return [candidate for candidate in candidates if candidate.is_file()]


def _official_member_for(staged_base: Path, official_base: Path, member: Path) -> Path:
    if staged_base.suffix.lower() == ".shp":
        return official_base.with_suffix(member.suffix)
    suffix = str(member)[len(str(staged_base)) :]
    return Path(str(official_base) + suffix)


def validate_staged_output_bundle(
    bundle: StagedOutputBundle,
    reference: RasterMetadata,
) -> None:
    missing: list[str] = []
    for staged_raw in bundle.staged_to_official:
        staged = Path(staged_raw)
        if not staged.is_file() or staged.stat().st_size <= 0:
            missing.append(str(staged))
            continue
        if staged.suffix.lower() == ".shp":
            for required_extension in (".shp", ".shx", ".dbf"):
                required = staged.with_suffix(required_extension)
                if not required.is_file() or required.stat().st_size <= 0:
                    missing.append(str(required))
        elif staged.suffix.lower() in {".tif", ".tiff"}:
            metadata = _read_raster_metadata(str(staged), staged.name)
            _validate_alignment(reference, metadata, staged.name)
        elif staged.suffix.lower() == ".json":
            with staged.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise RuntimeError(f"Step 5 summary must be a JSON object: {staged}")
    if missing:
        raise RuntimeError(
            "Step 5 staged output bundle is incomplete:\n  " + "\n  ".join(missing)
        )


def publish_staged_output_bundle(bundle: StagedOutputBundle) -> None:
    pairs = [
        (Path(staged), Path(official))
        for staged, official in bundle.staged_to_official.items()
    ]
    pairs.sort(key=lambda pair: pair[1].suffix.lower() == ".json")
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for pair_index, (staged, official) in enumerate(pairs):
            staged_members = _artifact_members(staged)
            if not staged_members:
                raise RuntimeError(f"Missing staged Step 5 output: {staged}")

            for existing_index, existing in enumerate(_artifact_members(official)):
                backup = staged.parent / (
                    f".backup-{pair_index}-{existing_index}-{existing.name}"
                )
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
                "Step 5 publication failed and rollback was incomplete:\n  "
                + "\n  ".join(rollback_errors)
            )
        raise


def run_post_depositional_spread_native(
    params: PostDepositionalSpreadParams,
    *,
    preflight_metadata: dict[str, RasterMetadata] | None = None,
    preflight_report: dict[str, Any] | None = None,
    reported_params: PostDepositionalSpreadParams | None = None,
) -> dict[str, Any]:
    run_start = time.perf_counter()
    preflight_was_supplied = (
        preflight_metadata is not None and preflight_report is not None
    )
    if preflight_metadata is None or preflight_report is None:
        preflight_start = time.perf_counter()
        preflight_metadata, preflight_report = preflight_step5(params)
        preflight_seconds = time.perf_counter() - preflight_start
    else:
        preflight_seconds = float(
            preflight_report.get("metadata_preflight_seconds", 0.0)
        )

    read_start = time.perf_counter()
    runout = _read_raster(
        params.runout_mask_raster,
        "runout mask",
        preflight_metadata["runout_mask_raster"],
    )
    dfi = _read_raster(params.dfi_raster, "DFI", preflight_metadata["dfi_raster"])
    dem = _read_raster(params.dem_raster, "DEM", preflight_metadata["dem_raster"])
    source_mask = _read_raster(
        params.source_mask_raster,
        "source mask",
        preflight_metadata["source_mask_raster"],
    )
    stream_mask = _read_raster(
        params.stream_mask_raster,
        "stream mask",
        preflight_metadata["stream_mask_raster"],
    )
    source_contributing_area = _read_raster(
        params.source_contributing_area_raster,
        "source contributing area",
        preflight_metadata["source_contributing_area_raster"],
    )
    read_seconds = time.perf_counter() - read_start

    _validate_alignment(runout, dfi, "DFI")
    _validate_alignment(runout, dem, "DEM")
    _validate_alignment(runout, source_mask, "source mask")
    _validate_alignment(runout, stream_mask, "stream mask")
    _validate_alignment(runout, source_contributing_area, "source contributing area")
    dx, dy, srs = _assert_projected_metric_grid(runout)
    crs_authority = _crs_authority_string(srs)

    actual_output_paths = step5_output_paths(params)
    output_paths = {
        key: _optional_abspath(value)
        for key, value in step5_output_paths(reported_params or params).items()
    }
    debug_output_paths = {
        key: value
        for key, value in actual_output_paths.items()
        if key.startswith("output_source_")
        or key == "output_computed_spread_radius"
    }

    core_start = time.perf_counter()
    result = run_post_depositional_spread_core(
        runout_array=runout.array,
        runout_nodata=runout.nodata,
        dfi_array=dfi.array,
        dfi_nodata=dfi.nodata,
        dem_array=dem.array,
        dem_nodata=dem.nodata,
        source_mask_array=source_mask.array,
        source_mask_nodata=source_mask.nodata,
        stream_mask_array=stream_mask.array,
        stream_mask_nodata=stream_mask.nodata,
        source_contributing_area_array=source_contributing_area.array,
        source_contributing_area_nodata=source_contributing_area.nodata,
        dx=dx,
        dy=dy,
        dfi_threshold=params.dfi_threshold,
        max_relative_rise_m=params.max_relative_rise_m,
        min_runout_source_area_m2=params.min_runout_source_area_m2,
        min_spread_radius_m=params.min_spread_radius_m,
        max_spread_radius_m=params.max_spread_radius_m,
        source_contributing_area_reference=params.source_contributing_area_reference,
        input_paths={
            "runout_mask_raster": os.path.abspath(params.runout_mask_raster),
            "dfi_raster": os.path.abspath(params.dfi_raster),
            "dem_raster": os.path.abspath(params.dem_raster),
            "source_mask_raster": os.path.abspath(params.source_mask_raster),
            "stream_mask_raster": os.path.abspath(params.stream_mask_raster),
            "source_contributing_area_raster": os.path.abspath(params.source_contributing_area_raster),
        },
        output_paths=output_paths,
        geotransform=runout.geotransform,
        crs_authority=crs_authority,
        crs_wkt=runout.projection_wkt,
        collect_debug_rasters=params.write_debug_rasters,
    )
    core_seconds = time.perf_counter() - core_start

    write_start = time.perf_counter()
    gdal_module, _, _ = _require_osgeo()
    _write_geotiff(
        params.output_spread_mask,
        runout,
        result.spread_mask,
        gdal_module.GDT_Byte,
        0.0,
    )
    _write_geotiff(
        params.output_combined_mask,
        runout,
        result.combined_mask,
        gdal_module.GDT_Byte,
        0.0,
    )
    _write_geotiff(
        params.output_seed_mask,
        runout,
        result.seed_mask,
        gdal_module.GDT_Byte,
        0.0,
    )
    _write_geotiff(
        params.output_cleaned_runout_mask,
        runout,
        result.cleaned_runout_mask,
        gdal_module.GDT_Byte,
        0.0,
    )
    _write_geotiff(
        params.output_removed_runout_mask,
        runout,
        result.removed_runout_mask,
        gdal_module.GDT_Byte,
        0.0,
    )
    if params.write_debug_rasters and params.output_min_elev_diff:
        _write_geotiff(
            params.output_min_elev_diff,
            runout,
            result.min_elev_diff,
            gdal_module.GDT_Float32,
            float(FLOAT32_NODATA),
        )
    provenance_valid = (
        result.source_index >= 0
        if params.write_debug_rasters
        else None
    )
    if params.write_debug_rasters and params.output_source_row:
        assert provenance_valid is not None
        source_row_array = np.full(
            result.source_index.shape,
            INT32_NODATA,
            dtype=np.int32,
        )
        source_row_array[provenance_valid] = (
            result.source_index[provenance_valid] // runout.cols
        )
        _write_geotiff(
            params.output_source_row,
            runout,
            source_row_array,
            gdal_module.GDT_Int32,
            float(INT32_NODATA),
        )
        del source_row_array
    if params.write_debug_rasters and params.output_source_col:
        assert provenance_valid is not None
        source_col_array = np.full(
            result.source_index.shape,
            INT32_NODATA,
            dtype=np.int32,
        )
        source_col_array[provenance_valid] = (
            result.source_index[provenance_valid] % runout.cols
        )
        _write_geotiff(
            params.output_source_col,
            runout,
            source_col_array,
            gdal_module.GDT_Int32,
            float(INT32_NODATA),
        )
        del source_col_array

    if params.write_debug_rasters:
        assert provenance_valid is not None
        if (
            result.computed_spread_radius is None
            or result.source_contributing_area is None
            or result.source_contribution_index is None
        ):
            raise RuntimeError(
                "Step 5 debug rasters were requested but debug values were not collected."
            )
        source_elevation = np.full(
            result.source_index.shape,
            FLOAT32_NODATA,
            dtype=np.float32,
        )
        if np.any(provenance_valid):
            source_elevation[provenance_valid] = np.asarray(
                dem.array.reshape(-1)[result.source_index[provenance_valid]],
                dtype=np.float32,
            )
        _write_geotiff(
            debug_output_paths["output_source_elevation"],
            runout,
            source_elevation,
            gdal_module.GDT_Float32,
            float(FLOAT32_NODATA),
        )
        del source_elevation
        _write_geotiff(
            debug_output_paths["output_computed_spread_radius"],
            runout,
            result.computed_spread_radius,
            gdal_module.GDT_Float32,
            float(FLOAT32_NODATA),
        )
        _write_geotiff(
            debug_output_paths["output_source_contributing_area"],
            runout,
            result.source_contributing_area,
            gdal_module.GDT_Float32,
            float(FLOAT32_NODATA),
        )
        _write_geotiff(
            debug_output_paths["output_source_contribution_index"],
            runout,
            result.source_contribution_index,
            gdal_module.GDT_Float32,
            float(FLOAT32_NODATA),
        )
    if params.output_spread_mask_shapefile:
        _write_mask_shapefile(params.output_spread_mask, params.output_spread_mask_shapefile, "spread")
    if params.output_combined_mask_shapefile:
        _write_mask_shapefile(params.output_combined_mask, params.output_combined_mask_shapefile, "combined")

    write_seconds = time.perf_counter() - write_start
    total_before_summary_seconds = time.perf_counter() - run_start
    if preflight_was_supplied:
        total_before_summary_seconds += preflight_seconds
    summary_metadata = dict(result.summary.metadata)
    summary_metadata["preflight"] = dict(preflight_report)
    summary = replace(
        result.summary,
        timings_seconds={
            "metadata_preflight": float(preflight_seconds),
            "raster_read": float(read_seconds),
            "core_compute": float(core_seconds),
            "output_write_before_summary": float(write_seconds),
            "total_before_summary": float(total_before_summary_seconds),
        },
        metadata=summary_metadata,
    )
    summary_dict = asdict(summary)
    _ensure_parent_dir(params.output_summary_json)
    with open(params.output_summary_json, "w", encoding="utf-8") as handle:
        json.dump(summary_dict, handle, indent=2)
    return summary_dict


def run_post_depositional_spread_transaction(
    params: PostDepositionalSpreadParams,
) -> dict[str, Any]:
    preflight_start = time.perf_counter()
    metadata, report = preflight_step5(params)
    report = dict(report)
    report["metadata_preflight_seconds"] = float(time.perf_counter() - preflight_start)
    bundle = prepare_staged_output_bundle(params)
    try:
        summary = run_post_depositional_spread_native(
            bundle.staged_params,
            preflight_metadata=metadata,
            preflight_report=report,
            reported_params=params,
        )
        validate_staged_output_bundle(bundle, metadata["runout_mask_raster"])
        publish_staged_output_bundle(bundle)
        return summary
    finally:
        cleanup_staged_output_bundle(bundle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Step 5 post-depositional spread in the QGIS/OSGeo runtime."
    )
    parser.add_argument("--config", help="Path to Step 5 config JSON.")
    parser.add_argument(
        "--check-environment",
        action="store_true",
        help="Report the active Python, NumPy, and GDAL runtime without reading input data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the config, input metadata, alignment, CRS, and output paths without processing pixels.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.check_environment:
        gdal_module, _, _ = _require_osgeo()
        try:
            import scipy

            scipy_version: str | None = scipy.__version__
        except ModuleNotFoundError:
            scipy_version = None
        print(
            json.dumps(
                {
                    "python": sys.executable,
                    "numpy": np.__version__,
                    "gdal": gdal_module.VersionInfo("--version"),
                    "osgeo_available": True,
                    "scipy": scipy_version,
                },
                indent=2,
            )
        )
        return 0
    if not args.config:
        raise SystemExit("--config is required unless --check-environment is used.")
    params = load_post_depositional_spread_params(args.config)
    if args.dry_run:
        _, report = preflight_step5(params)
        print(json.dumps(report, indent=2))
        return 0
    summary = run_post_depositional_spread_transaction(params)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
