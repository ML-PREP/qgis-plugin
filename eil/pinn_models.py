"""The PINN models the EIL tab can run, read from model/pinn_models.json.

    {
      "default": "earthquake",
      "models": [
        {"id": "earthquake", "name": "Earthquake Induced Model",
         "path": "my_model.keras", "description": "optional"}
      ]
    }

`id` is what the tab remembers and what `run_eil_pipeline.py --model` takes;
`name` is what the user sees. A relative `path` is resolved beside the config
file. Every slope-unit model must take the same inputs: pinn_inference.py
fills any input the pipeline did not produce with a placeholder value.

`"kind": "grid"` marks a model trained on a regular grid instead of slope
units (no slope units, no zonal statistics; see grid_inference.py). Its `path`
is a folder of fold-<N>-model-v3.keras checkpoints and their transform
manifests, and `cell_size_m` is the grid cell it was trained on:

    {"id": "grid-v2-9", "name": "...", "kind": "grid",
     "path": "grid_v2-9", "cell_size_m": 25}

Qt-free so the headless runner and tests can use it.
"""

import glob
import json
import os
from dataclasses import dataclass
from typing import Optional, Tuple


KIND_SLOPE_UNIT = "slope_unit"
KIND_GRID = "grid"
KINDS = (KIND_SLOPE_UNIT, KIND_GRID)
# The checkpoints of a grid model's cross-validation folds.
GRID_FOLD_PATTERN = "fold-*-model-v3.keras"


class PinnModelConfigError(ValueError):
    """model/pinn_models.json is missing or malformed."""


@dataclass(frozen=True)
class PinnModel:
    id: str
    name: str
    path: str
    description: str = ""
    kind: str = KIND_SLOPE_UNIT
    cell_size_m: float = 0.0

    @property
    def is_grid(self):
        return self.kind == KIND_GRID

    @property
    def is_available(self):
        if self.is_grid:
            return bool(glob.glob(os.path.join(self.path, GRID_FOLD_PATTERN)))
        return os.path.isfile(self.path)


@dataclass(frozen=True)
class PinnCatalog:
    models: Tuple[PinnModel, ...]
    default_id: str

    def find(self, model_id) -> Optional[PinnModel]:
        return next((m for m in self.models if m.id == model_id), None)


def load_catalog(config_path) -> PinnCatalog:
    """Validated catalog; PinnModelConfigError explains what is wrong."""
    try:
        with open(config_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise PinnModelConfigError(
            f"Cannot read the PINN model list {config_path}: {exc.strerror}"
        ) from exc
    except ValueError as exc:
        raise PinnModelConfigError(
            f"{config_path} is not valid JSON: {exc}"
        ) from exc

    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise PinnModelConfigError(f"{config_path} must list at least one entry in \"models\".")

    base_dir = os.path.dirname(os.path.abspath(config_path))
    models = tuple(_parse_entry(item, index, base_dir, config_path)
                   for index, item in enumerate(entries))

    ids = [model.id for model in models]
    duplicates = sorted({model_id for model_id in ids if ids.count(model_id) > 1})
    if duplicates:
        raise PinnModelConfigError(
            f"{config_path} has duplicate model ids: {', '.join(duplicates)}"
        )

    default_id = data.get("default", ids[0])
    if default_id not in ids:
        raise PinnModelConfigError(
            f"{config_path}: \"default\" is {default_id!r}, which is not one of "
            f"the model ids ({', '.join(ids)})."
        )
    return PinnCatalog(models, default_id)


def _parse_entry(item, index, base_dir, config_path):
    if not isinstance(item, dict):
        raise PinnModelConfigError(f"{config_path}: models[{index}] must be an object.")
    for key in ("id", "name", "path"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PinnModelConfigError(
                f"{config_path}: models[{index}] needs a non-empty \"{key}\"."
            )
    description = item.get("description", "")
    kind = item.get("kind", KIND_SLOPE_UNIT)
    if kind not in KINDS:
        raise PinnModelConfigError(
            f"{config_path}: models[{index}] has \"kind\" {kind!r}; "
            f"use one of {', '.join(KINDS)}."
        )
    path = os.path.expanduser(item["path"])
    return PinnModel(
        id=item["id"].strip(),
        name=item["name"].strip(),
        path=path if os.path.isabs(path) else os.path.join(base_dir, path),
        description=description if isinstance(description, str) else "",
        kind=kind,
        cell_size_m=_grid_cell_size(item, index, config_path) if kind == KIND_GRID else 0.0,
    )


def _grid_cell_size(item, index, config_path):
    value = item.get("cell_size_m")
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not is_number or value <= 0:
        raise PinnModelConfigError(
            f"{config_path}: models[{index}] is a grid model and needs a positive "
            "\"cell_size_m\" (the grid cell it was trained on, in metres)."
        )
    return float(value)


def choose(catalog, saved_id) -> Optional[PinnModel]:
    """The model to preselect: the saved one, else the default, else any.

    Only models whose file exists qualify; None when there are none.
    """
    for model_id in (saved_id, catalog.default_id):
        model = catalog.find(model_id)
        if model is not None and model.is_available:
            return model
    return next((m for m in catalog.models if m.is_available), None)
