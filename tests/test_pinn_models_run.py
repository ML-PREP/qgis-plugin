"""Every model in model/pinn_models.json runs through its prediction script.

Runs the script exactly as PINNPredictionWorker does, in the managed EIL
virtualenv: slope-unit models through pinn_inference.py on a synthetic
Zonal_Results/Merged_PINN_Features.gpkg with the Go engine's column names,
grid models through grid_inference.py on synthetic input rasters. A model
whose layer classes cannot be loaded, or whose inputs the pipeline cannot
supply, fails here instead of hours into a run. Needs the provisioned
~/.mlprep_envs/venv (skipped otherwise).
"""

import importlib
import json
import os
import subprocess
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
paths = importlib.import_module("mlprep_plugin.common.paths")
pinn_models = importlib.import_module("mlprep_plugin.eil.pinn_models")

VENV_PYTHON = pu.venv_python("venv")
CATALOG = pinn_models.load_catalog(paths.PINN_MODEL_CONFIG)
SLOPE_UNIT_MODELS = [m for m in CATALOG.models if not m.is_grid]
GRID_MODELS = [m for m in CATALOG.models if m.is_grid]
RUN_TIMEOUT_S = 600

# Built in the venv, which has geopandas/rasterio. Every soil label the Go
# engine can hand over: USDA names, Undifferentiated, nulls.
FIXTURE_SCRIPT = r"""
import os, sys
import numpy as np, geopandas as gpd, rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
out = sys.argv[1]
zonal = os.path.join(out, "Zonal_Results"); os.makedirs(zonal)
rng = np.random.default_rng(0)
n, x0, y0, cell = 60, 600000.0, 780000.0, 100.0
geoms = [box(x0 + (i % 10) * cell, y0 - (i // 10 + 1) * cell,
             x0 + (i % 10 + 1) * cell, y0 - (i // 10) * cell) for i in range(n)]
labels = ["Loam", "Sandy Clay Loam", "Clay", "Undifferentiated", "Silt Loam", None]
gpd.GeoDataFrame({
    "Slope_mean": rng.uniform(12, 45, n), "BUK_mean": rng.uniform(110, 160, n),
    "Prc_mean": rng.uniform(150, 320, n), "ContributingFactor_mean": rng.uniform(1, 500, n),
    "SoilThc_mean": rng.uniform(0.5, 3.0, n), "Elev_mean": rng.uniform(100, 1800, n),
    "PGA2_max": rng.uniform(0.1, 0.6, n), "Clay_mean": rng.uniform(150, 450, n),
    "Sand_mean": rng.uniform(150, 450, n), "Silt_mean": rng.uniform(150, 450, n),
    "type": [labels[i % len(labels)] for i in range(n)],
}, geometry=geoms, crs="EPSG:32651").to_file(
    os.path.join(zonal, "Merged_PINN_Features.gpkg"), driver="GPKG")
with rasterio.open(os.path.join(out, "dem.tif"), "w", driver="GTiff", width=100,
                   height=60, count=1, dtype="float32", crs="EPSG:32651",
                   transform=from_origin(x0, y0, 10, 10)) as dst:
    dst.write(np.ones((1, 60, 100), "float32"))
"""


def _venv_has_tensorflow():
    if not os.path.isfile(VENV_PYTHON):
        return False
    probe = subprocess.run([VENV_PYTHON, "-c", "import tensorflow, geopandas"],
                           capture_output=True)
    return probe.returncode == 0


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _venv_has_tensorflow(),
                       reason="needs the provisioned EIL venv with TensorFlow"),
]


def _result(stdout):
    """The JSON status line pinn_inference.py prints last."""
    for line in reversed(stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON result in output:\n{stdout[-2000:]}")


@pytest.mark.parametrize("model", SLOPE_UNIT_MODELS, ids=lambda m: m.id)
def test_model_runs_on_pipeline_output(model, tmp_path):
    # Arrange
    assert model.is_available, f"{model.name}: no file at {model.path}"
    subprocess.run([VENV_PYTHON, "-c", FIXTURE_SCRIPT, str(tmp_path)], check=True)

    # Act
    completed = subprocess.run(
        [VENV_PYTHON, paths.PINN_SCRIPT,
         str(tmp_path / "Zonal_Results" / "Merged_PINN_Features.gpkg"), model.path,
         str(tmp_path / "Final_Hazard_Map.gpkg"), str(tmp_path / "dem.tif")],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=RUN_TIMEOUT_S,
        env=pu.tool_environment(),
    )

    # Assert
    result = _result(completed.stdout)
    assert result["status"] == "success", result.get("message")
    # Every input must come from the data, never from the placeholder fill.
    assert "Imputing missing required input" not in completed.stdout


# One raster per EIL input on a 5 m grid (60 x 60 cells of 25 m), in the units
# the Cotabato inputs come in: SoilGrids g/kg and cg/cm3, soil-map codes.
GRID_FIXTURE_SCRIPT = r"""
import os, sys
import numpy as np, rasterio
from rasterio.transform import from_origin
out = sys.argv[1]
rng = np.random.default_rng(0)
size, x0, y0 = 300, 650000.0, 780000.0
def write(name, data, nodata=-9999.0):
    with rasterio.open(os.path.join(out, name + ".tif"), "w", driver="GTiff",
                       width=size, height=size, count=1, dtype="float32",
                       crs="EPSG:32651", nodata=nodata,
                       transform=from_origin(x0, y0, 5.0, 5.0)) as dst:
        dst.write(data.astype("float32"), 1)
def field(lo, hi):
    return rng.uniform(lo, hi, (size, size))
write("Elev", field(100, 1800))
write("Slope", field(5, 45))
write("BUK", field(80, 140))
write("Clay", field(150, 450)); write("Sand", field(150, 450)); write("Silt", field(150, 450))
write("Prc", field(150, 320)); write("SoilThc", field(0.5, 3.0))
write("ContributingFactor", field(1, 500)); write("PGA2", field(0.1, 0.6))
codes = np.array([45, 451, 583, 448, 7777], "float32")
write("type", codes[rng.integers(0, len(codes), (size, size))], nodata=0.0)
"""


@pytest.mark.parametrize("model", GRID_MODELS, ids=lambda m: m.id)
def test_grid_model_runs_on_eil_inputs(model, tmp_path):
    # Arrange
    assert model.is_available, f"{model.name}: no folds in {model.path}"
    subprocess.run([VENV_PYTHON, "-c", GRID_FIXTURE_SCRIPT, str(tmp_path)], check=True)
    names = ("Elev", "Slope", "BUK", "Clay", "Sand", "Silt", "Prc", "SoilThc",
             "ContributingFactor", "PGA2", "type")
    rasters = [arg for name in names
               for arg in ("--raster", f"{name}={tmp_path / (name + '.tif')}")]

    # Act
    completed = subprocess.run(
        [VENV_PYTHON, paths.GRID_SCRIPT, "--dem", str(tmp_path / "Elev.tif"),
         "--model-dir", model.path, "--cell-size", str(model.cell_size_m),
         "--output-dir", str(tmp_path / "out"), *rasters],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=RUN_TIMEOUT_S,
        env=pu.tool_environment(),
    )

    # Assert
    result = _result(completed.stdout)
    assert result["status"] == "success", result.get("message")
    assert result["cells_predicted"] > 0
    # What the depositional zone's FoS refinement reads.
    for key in ("susceptibility_tif", "cohesion_tif", "friction_angle_tif", "wetness_tif",
                "factor_of_safety_tif"):
        assert os.path.isfile(result[key]), key
    assert result["friction_angle_unit"] == "radians"
    assert "PROGRESS 100" in completed.stdout
    # The unknown soil code 7777 falls back to Undifferentiated, loudly.
    assert "[7777]" in completed.stdout
