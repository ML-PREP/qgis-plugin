"""argv builders for the native engines the EIL workers launch. No Qt here."""

import os

# Defaults the Go engine falls back to when a parameter is absent.
_ZONAL_DEFAULTS = (
    ("areaMin", 5000),
    ("thresh", 5000),
    ("cvMin", 0.15),
    ("rf", 10),
    ("maxIter", 10),
    ("tileCols", 3),
    ("tileRows", 3),
    ("tileOverlap", 50),
)

# Fixed tuning of the C++/MPI depositional routing model.
ROUTING_THRESHOLD = "0.2"
ROUTING_DFI_MID = "0.69"
ROUTING_ALPHA_GAIN_PER_METER = "0.03333"


def build_zonal_args(
    dem_path, raster_folder, output_dir, python_exe, target_crs, params
):
    """Flags for the Go zonal engine, appended to its launch command."""
    args = [
        "-dem",
        dem_path,
        "-rasterFolder",
        raster_folder,
        "-outputDir",
        output_dir,
        "-pythonExe",
        python_exe,
        "-targetCRS",
        target_crs,
    ]
    for name, default in _ZONAL_DEFAULTS:
        args += [f"-{name}", str(params.get(name, default))]
    return args


def build_routing_command(mpi_exe, cores, engine, inputs, output_dir, deposition_out):
    """argv for the C++/MPI depositional routing engine.

    `inputs` maps fel/ang/source/dfi/alpha to the rasters prepared for it.
    """

    def out(name):
        return os.path.join(output_dir, name)

    return [
        mpi_exe,
        "-n",
        str(cores),
        engine,
        "--fel",
        inputs["fel"],
        "--ang",
        inputs["ang"],
        "--source",
        inputs["source"],
        "--dfi",
        inputs["dfi"],
        "--alpha",
        inputs["alpha"],
        "--out-alpha",
        out("step4_dynamic_alpha.tif"),
        "--out-beta",
        out("step4_beta_angle.tif"),
        "--out-dfs",
        out("step4_dfs.tif"),
        "--out-mask",
        out("step4_runout_mask.tif"),
        "--out-deposition",
        deposition_out,
        "--threshold",
        ROUTING_THRESHOLD,
        "--dfi-mid",
        ROUTING_DFI_MID,
        "--alpha-gain-per-meter",
        ROUTING_ALPHA_GAIN_PER_METER,
    ]
