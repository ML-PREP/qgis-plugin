"""Grid PINN predictions for the EIL pipeline: no slope units, no zonal statistics.

Run in the EIL virtualenv by the pipeline's PINN worker when a "grid" model
(model/pinn_models.json) is chosen. Every input raster is resampled onto a
DEM-snapped grid of the model's cell size (pinn_grid/align.py), then each
strip of cells is predicted by the mean of the model's cross-validation folds
(pinn_grid/ensemble.py), as in mlprep/notebooks/cotabato_grid_v2-9.ipynb.

    python grid_inference.py --dem DEM.tif --model-dir model/grid_v2-9-fixed-recharge \\
        --cell-size 25 --output-dir EIL_Results \\
        --raster Elev=DEM.tif --raster Slope=slope.tif ... \\
        [--municipalities muni.gpkg --municipality-field NAME] [--tally-mask roi.gpkg]

Prints "PROGRESS <0-100>" lines while it works and, last, one JSON status
line like pinn_inference.py's; its keys are what the depositional zone reads
(susceptibility_tif, cohesion_tif, friction_angle_tif, wetness_tif,
factor_of_safety_tif).
"""

import argparse
import json
import os
import sys
import traceback
from contextlib import ExitStack

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
os.environ["TF_DETERMINISTIC_OPS"] = "1"

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

GRID_INPUTS_DIR = "Grid_Inputs"
SOIL_RECODE_FILE = "soil_type_recode.json"
# Same cut as pinn_inference.py: the susceptible cells the FoS refinement keeps.
SUSCEPTIBILITY_THRESHOLD = 0.5
STRIP_ROWS = 128
# Whole-run progress after each phase.
GRIDDED, MODELS_LOADED, PREDICTED = 25, 30, 100


def progress(percent):
    print(f"PROGRESS {int(percent)}", flush=True)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dem", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--cell-size", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--raster", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--municipalities")
    parser.add_argument("--municipality-field")
    parser.add_argument("--tally-mask")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    return parser.parse_args(argv)


def parse_rasters(items):
    """{name: path} from NAME=PATH items (paths may contain '=')."""
    rasters = {}
    for item in items:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"--raster expects NAME=PATH, got {item!r}")
        rasters[name] = path
    return rasters


def read_shapes(path, crs, field=None):
    """[(field value, geometry)] of a vector file, reprojected to `crs`."""
    import geopandas as gpd

    frame = gpd.read_file(path).to_crs(crs)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]
    labels = frame[field].astype(str) if field else [""] * len(frame)
    return [(label, geom) for label, geom in zip(labels, frame.geometry)]


def build_tally(args, grid):
    from pinn_grid.outputs import HazardTally

    municipalities = None
    if args.municipalities and args.municipality_field:
        municipalities = read_shapes(args.municipalities, grid.crs, args.municipality_field)
    mask = None
    if args.tally_mask:
        mask = [geom for _label, geom in read_shapes(args.tally_mask, grid.crs)] or None
    return HazardTally(grid, municipalities, mask)


def predict_strips(args, grid, gridded, tally):
    """Predict every strip; returns (cells predicted, unknown soil codes, paths)."""
    from pinn_grid import align, ensemble, features
    from pinn_grid.outputs import GridWriter, summarize_folds
    import rasterio

    folds = ensemble.load_folds(args.model_dir)
    recode = features.load_soil_recode(os.path.join(args.model_dir, SOIL_RECODE_FILE))
    print(f"[INFO] {len(folds)} folds loaded; grid {grid.width} x {grid.height} cells "
          f"of {args.cell_size:g} m", flush=True)
    progress(MODELS_LOADED)

    windows = list(align.strip_windows(grid.width, grid.height, STRIP_ROWS))
    predicted, unknown_codes = 0, set()
    with ExitStack() as stack, GridWriter(grid, args.output_dir, SUSCEPTIBILITY_THRESHOLD) as writer:
        sources = {name: stack.enter_context(rasterio.open(path)) for name, path in gridded.items()}
        for number, window in enumerate(windows, start=1):
            strip = {
                name: src.read(1, window=window, masked=True).astype("float32").filled(float("nan"))
                for name, src in sources.items()
            }
            valid = features.valid_cells(strip)
            if valid.any():
                frame, unknown = features.model_frame(
                    {name: values[valid] for name, values in strip.items()}, recode
                )
                unknown_codes |= unknown
                summary = summarize_folds(ensemble.predict(folds, frame))
                writer.write(window, valid, summary)
                tally.add(window, valid, summary["susceptibility_mean"])
                predicted += int(valid.sum())
            progress(MODELS_LOADED + (PREDICTED - MODELS_LOADED) * number / len(windows))
    return predicted, unknown_codes, writer.paths


def run(args):
    from pinn_grid import align
    from pinn_grid.inputs import missing_inputs
    from pinn_grid.outputs import FRICTION_ANGLE_UNIT

    rasters = parse_rasters(args.raster)
    missing = missing_inputs(rasters)
    if missing:
        raise ValueError(f"The grid model needs these inputs: {', '.join(missing)}")

    grid = align.read_grid(args.dem, args.cell_size)
    inputs_dir = os.path.join(args.output_dir, GRID_INPUTS_DIR)
    os.makedirs(inputs_dir, exist_ok=True)
    print(f"[INFO] Gridding {len(rasters)} rasters onto {args.cell_size:g} m cells", flush=True)
    done = []

    def on_gridded(name):
        done.append(name)
        print(f"[INFO] Gridded {name}", flush=True)
        progress(GRIDDED * len(done) / len(rasters))

    gridded = align.grid_inputs(rasters, grid, inputs_dir, args.workers, STRIP_ROWS * 4, on_gridded)
    tally = build_tally(args, grid)
    predicted, unknown_codes, paths = predict_strips(args, grid, gridded, tally)
    if not predicted:
        raise ValueError(
            "No cell could be predicted: every cell lacks an input, has a slope "
            "under 10 degrees, or has no bulk density."
        )
    if unknown_codes:
        print(f"[WARN] Soil Type codes with no soil-map name were treated as "
              f"Undifferentiated: {sorted(unknown_codes)}", flush=True)
    print(f"[INFO] Predicted {predicted:,} of {grid.width * grid.height:,} grid cells",
          flush=True)
    tallies = tally.result()
    print(f"[INFO] Hazard classes (cells): {tallies['hazard_counts']}", flush=True)
    return {
        "kind": "grid",
        **paths,
        "friction_angle_unit": FRICTION_ANGLE_UNIT,
        "susceptibility_threshold": SUSCEPTIBILITY_THRESHOLD,
        "cells_predicted": predicted,
        **tallies,
    }


def main(argv=None):
    try:
        result = run(parse_args(argv))
        print(json.dumps({"status": "success", **result}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
