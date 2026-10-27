"""The grid PINN's cross-validation folds, run as one ensemble.

Each fold-<N>-model-v3.keras is fed through its own transform manifest
(v1_cotabato_transforms_fold<N>.json beside it), as run_model_v3.py's
replay_transforms does: missingness indicators, training medians, log1p and
clip, in that order. The ensemble value of every output is the fold mean.
"""

import glob
import json
import os
import re
from dataclasses import dataclass

import numpy as np
import tensorflow as tf

# Importing these registers the custom layers ("Custom>...") Keras needs.
import py_files.GallenModel  # noqa: F401
import py_files.GallenModel_v1  # noqa: F401
import py_files.GallenModel_v3  # noqa: F401
from py_files.data import (
    MISSINGNESS_SUFFIX,
    apply_clip_thresholds,
    apply_imputation_medians,
    apply_log_transform,
    apply_missingness_indicators,
    dataframe_to_dataset,
)

FOLD_PATTERN = re.compile(r"fold-(\d+)-model-v3\.keras$")
MANIFEST_NAME = "v1_cotabato_transforms_fold{fold}.json"
LABEL_COL = "landslide"
BATCH_SIZE = 8192

# Each fold traces its own predict function once; TensorFlow's "retracing"
# warning about that is noise in the run log.
tf.get_logger().setLevel("ERROR")

# output name -> model layer; friction stays in radians, as the layer emits.
# "fos" is the model's own Factor of Safety, which the depositional zone's
# source refinement reads instead of deriving one.
OUTPUT_LAYERS = {
    "susceptibility": "final_head",
    "cohesion_kpa": "cohesion_layer",
    "friction_rad": "internal_friction",
    "displacement_cm": "displacement_layer",
    "wetness_m": "m_clip",
    "fos": "fos_layer",
}


class EnsembleError(ValueError):
    """The model folder cannot be run; the message is shown to the user."""


@dataclass(frozen=True)
class Fold:
    number: int
    extractor: tf.keras.Model
    input_names: tuple
    manifest: dict


def _load_fold(path, number):
    manifest_path = os.path.join(os.path.dirname(path), MANIFEST_NAME.format(fold=number))
    if not os.path.exists(manifest_path):
        raise EnsembleError(f"Fold {number} has no transform manifest at {manifest_path}.")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    model = tf.keras.models.load_model(path, compile=False)
    input_names = tuple(tensor.name for tensor in model.inputs)
    indicators = sorted(n for n in input_names if n.endswith(MISSINGNESS_SUFFIX))
    if sorted(manifest.get("imputed_indicator_cols", [])) != indicators:
        raise EnsembleError(
            f"{manifest_path} lists indicators {manifest.get('imputed_indicator_cols')}, "
            f"but fold {number} declares {indicators}: it belongs to another training run."
        )
    present = {layer.name for layer in model.layers}
    absent = sorted(layer for layer in OUTPUT_LAYERS.values() if layer not in present)
    if absent:
        raise EnsembleError(f"Fold {number} has no {', '.join(absent)} layer: {path}")
    extractor = tf.keras.Model(
        model.inputs,
        {name: model.get_layer(layer).output for name, layer in OUTPUT_LAYERS.items()},
    )
    return Fold(number, extractor, input_names, manifest)


def load_folds(model_dir):
    """Every fold in `model_dir`, in fold order."""
    found = sorted(
        (int(match.group(1)), path)
        for path in glob.glob(os.path.join(model_dir, "fold-*-model-v3.keras"))
        if (match := FOLD_PATTERN.search(os.path.basename(path)))
    )
    if not found:
        raise EnsembleError(f"No fold-<N>-model-v3.keras checkpoints in {model_dir}.")
    return [_load_fold(path, number) for number, path in found]


def replay_transforms(frame, fold):
    """A copy of `frame` transformed as fold `fold` was trained."""
    manifest = fold.manifest
    indicators = [n for n in fold.input_names if n.endswith(MISSINGNESS_SUFFIX)]
    out = frame.copy()
    out = apply_missingness_indicators(out, indicators)
    out = apply_imputation_medians(out, manifest.get("imputation_medians", {}))
    out = apply_log_transform(out, manifest.get("log_transformed_cols", []))
    return apply_clip_thresholds(out, manifest.get("clip_thresholds", {}))


def _fold_outputs(fold, frame):
    feed = replay_transforms(frame, fold)
    missing = [name for name in fold.input_names if name not in feed.columns]
    if missing:
        raise EnsembleError(f"Fold {fold.number} needs inputs the grid lacks: {missing}")
    columns = {
        name: feed[name].astype(str) if name == "type" else feed[name].astype(np.float32)
        for name in fold.input_names
    }
    dataset = dataframe_to_dataset(
        feed.assign(**columns)[list(fold.input_names)].assign(**{LABEL_COL: 0}),
        shuffle=False,
        batch_size=BATCH_SIZE,
    )
    raw = fold.extractor.predict(dataset, verbose=0)
    count = len(feed)
    return {name: np.asarray(raw[name]).reshape(count, -1)[:, 0] for name in OUTPUT_LAYERS}


def predict(folds, frame):
    """{output name: array (n_folds, n_cells)} for the rows of `frame`."""
    per_fold = [_fold_outputs(fold, frame) for fold in folds]
    return {name: np.stack([outputs[name] for outputs in per_fold]) for name in OUTPUT_LAYERS}
