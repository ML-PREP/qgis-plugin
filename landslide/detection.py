"""Building and interpreting a landslide_worker.py run. No Qt or QGIS here."""

import json
import os
import sys
from dataclasses import dataclass

from ..common import paths


@dataclass(frozen=True)
class DetectionRequest:
    dem: str
    after: str
    lulc: str
    roi_path: str
    filter_col: str
    filter_val: str


def build_detection_command(python_exe, request, output_path):
    """argv for landslide_worker.py inside the detection virtualenv."""
    return [
        python_exe,
        paths.LANDSLIDE_WORKER_SCRIPT,
        "--dem",
        request.dem,
        "--after",
        request.after,
        "--lulc",
        request.lulc,
        "--output",
        output_path,
        "--roi_path",
        request.roi_path,
        "--filter_col",
        request.filter_col,
        "--filter_val",
        request.filter_val,
        "--model_path",
        paths.LANDSLIDE_MODEL,
        "--stats_path",
        paths.LANDSLIDE_NORM_STATS,
    ]


def detection_environment(environ=None, platform=sys.platform, prefix=sys.prefix):
    """Environment for the worker: QGIS's Python variables stripped.

    On Windows QGIS's bin/ is put first on PATH so the worker finds the GDAL
    DLLs that live there.
    """
    env = dict(os.environ if environ is None else environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    if platform == "win32":
        qgis_bin = os.path.join(os.path.dirname(os.path.dirname(prefix)), "bin")
        if os.path.exists(qgis_bin):
            env["PATH"] = qgis_bin + os.pathsep + env.get("PATH", "")
    return env


def parse_worker_result(stdout):
    """The worker's final JSON status line. Raises json.JSONDecodeError."""
    lines = stdout.strip().split("\n")
    return json.loads(lines[-1] if lines else "")
