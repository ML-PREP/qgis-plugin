#!/usr/bin/env python3
"""End-to-end check of the bundled TauDEM tools on the current platform.

Runs pitremove and dinfflowdir from bin/<tag>/ under the host's mpiexec on a
synthetic DEM with a known pit, exactly as the deposition worker does
(platform_utils.find_taudem + bundled_environment), then verifies the output.
Also checks that the bundled routing engine can load its libraries.

Needs GDAL's Python bindings (to write the DEM and read the results) and an
MPI launcher. Used by CI; runs the same on a developer machine.

    python scripts/taudem_smoke_test.py --mpiexec "C:/Program Files/Microsoft MPI/Bin/mpiexec.exe"
"""

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
from osgeo import gdal, osr

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

import platform_utils as pu  # noqa: E402

gdal.UseExceptions()

EPSG = 32651
ROWS, COLS = 150, 200
PIT = (slice(70, 76), slice(90, 96))
PROCESS_COUNTS = (1, 2)
ENGINE_NAME = "step4_deposition_zone_mpi"
# Windows exit codes for "a DLL could not be found / loaded".
DLL_LOAD_FAILURES = {0xC0000135, 0xC000007B, -1073741515, -1073741701}
MASKING_VARS = {"PROJ_DATA", "PROJ_LIB", "GDAL_DATA"}


def make_dem(path):
    """Tilted plane + noise + one 5 m pit; returns the elevation array."""
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:ROWS, 0:COLS]
    dem = (500 - 0.8 * x - 0.3 * y + rng.normal(0, 0.5, (ROWS, COLS))).astype("float32")
    dem[PIT] -= 5.0
    ds = gdal.GetDriverByName("GTiff").Create(path, COLS, ROWS, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((300000, 10, 0, 1700000, 0, -10))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    band.WriteArray(dem)
    ds = None
    return dem


def tool_env(mpiexec):
    """Environment the deposition worker gives bundled tools.

    Anything that could let the tools find data or DLLs outside the bundle is
    removed first, so a gap in the bundle fails here instead of being masked
    by the build environment (conda's activation sets PROJ_DATA, for one).
    On Windows PATH is cut down to the OS and MS-MPI for the same reason.
    """
    clean = {k: v for k, v in os.environ.items() if k not in MASKING_VARS}
    env = pu.bundled_environment(clean, PLUGIN_DIR)
    if pu.IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join(
            [os.path.dirname(mpiexec), os.path.join(system_root, "System32"), system_root]
        )
    return env


def run(cmd, env):
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        sys.exit(f"FAILED ({result.returncode}): {' '.join(cmd)}\n{output}")
    if "proj.db" in output:
        sys.exit(f"PROJ could not use the bundled proj.db:\n{output}")


def run_taudem(mpiexec, env, dem_path, out_dir, processes):
    pitremove = pu.find_taudem("pitremove", PLUGIN_DIR)
    dinfflowdir = pu.find_taudem("dinfflowdir", PLUGIN_DIR)
    for tool in (pitremove, dinfflowdir):
        if not pu.is_bundled(PLUGIN_DIR, tool):
            sys.exit(f"expected the bundled tool, resolved: {tool}")
    os.makedirs(out_dir, exist_ok=True)
    fel, ang, slp = (os.path.join(out_dir, n + ".tif") for n in ("fel", "ang", "slp"))
    base = [mpiexec, "-n", str(processes)]
    run(base + [pitremove, "-z", dem_path, "-fel", fel], env)
    run(base + [dinfflowdir, "-fel", fel, "-ang", ang, "-slp", slp], env)


def read(path):
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    return band.ReadAsArray().astype("float64"), band.GetNoDataValue(), srs.GetAuthorityCode(None)


def remaining_pits(grid):
    """Interior cells strictly lower than all eight neighbours."""
    centre = grid[1:-1, 1:-1]
    neighbours = [grid[1 + dy:grid.shape[0] - 1 + dy, 1 + dx:grid.shape[1] - 1 + dx]
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    return int(np.sum(np.all(np.stack(neighbours) > centre, axis=0)))


def check_outputs(dem, out_dir):
    fel, fel_nd, fel_epsg = read(os.path.join(out_dir, "fel.tif"))
    ang, ang_nd, _ = read(os.path.join(out_dir, "ang.tif"))
    slp, slp_nd, _ = read(os.path.join(out_dir, "slp.tif"))
    valid_ang = ang[ang != ang_nd]
    return {
        "fel >= dem": bool(np.all(fel[fel != fel_nd] >= dem[fel != fel_nd] - 1e-4)),
        "no pits left in fel": remaining_pits(fel) == 0 < remaining_pits(dem),
        f"EPSG:{EPSG} kept": fel_epsg == str(EPSG),
        "ang in [0, 2pi]": bool(np.all((valid_ang >= 0) & (valid_ang <= 2 * np.pi + 1e-6))),
        "ang mostly valid": valid_ang.size > 0.95 * ang.size,
        "slp >= 0": bool(np.all(slp[slp != slp_nd] >= 0)),
    }


def check_runs_agree(work):
    first, second = (os.path.join(work, f"n{n}") for n in PROCESS_COUNTS)
    return {
        f"{name} identical for -n {PROCESS_COUNTS[0]} and -n {PROCESS_COUNTS[1]}":
            np.array_equal(read(os.path.join(first, name + ".tif"))[0],
                           read(os.path.join(second, name + ".tif"))[0])
        for name in ("fel", "ang", "slp")
    }


def check_engine_loads(env):
    """The engine has no --help; it only has to get past DLL loading."""
    engine = pu.bundled_binary(PLUGIN_DIR, ENGINE_NAME)
    if not engine:
        return {"routing engine present": False}
    code = subprocess.run([engine], env=env, capture_output=True).returncode
    return {"routing engine loads its libraries": code not in DLL_LOAD_FAILURES}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mpiexec", default=pu.find_exe("mpiexec", "mpiexec"))
    args = parser.parse_args()
    if not args.mpiexec:
        sys.exit("mpiexec not found; pass --mpiexec")

    env = tool_env(args.mpiexec)
    results = {}
    with tempfile.TemporaryDirectory() as work:
        dem_path = os.path.join(work, "dem.tif")
        dem = make_dem(dem_path)
        for n in PROCESS_COUNTS:
            out_dir = os.path.join(work, f"n{n}")
            run_taudem(args.mpiexec, env, dem_path, out_dir, n)
            results.update({f"-n {n}: {k}": v for k, v in check_outputs(dem, out_dir).items()})
        results.update(check_runs_agree(work))
    results.update(check_engine_loads(env))

    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
