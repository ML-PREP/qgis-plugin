"""Stage 3 of the EIL pipeline: ML-DFI depositional routing (TauDEM + C++/MPI)."""

import os
import subprocess
import traceback

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ... import platform_utils as pu
from ... import raster_io
from ...common.cancellable import CancellableProcessMixin
from ...common.paths import PLUGIN_DIR
from . import deposition_rasters as dr
from .commands import build_routing_command

ENGINE_NAME = "step4_deposition_zone_mpi"
SOURCE_THRESHOLD = 0.50


class MLDFIDepositionWorker(CancellableProcessMixin, QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, dem_path, dfi_tif, output_dir, step4_wrapper_py, cores=4):
        super().__init__()
        self._init_cancellation()
        self.dem_path = dem_path
        self.dfi_tif = dfi_tif  # AI's Hazard_Susceptibility raster
        self.output_dir = output_dir
        self.step4_wrapper_py = step4_wrapper_py
        self.cores = cores

        self.taudem_dir = self._locate_taudem()
        self.mpi_exe = self._locate_mpi()

    def _locate_taudem(self):
        """Directory holding the TauDEM binaries, or None if not installed."""
        pitremove = pu.find_taudem(
            "pitremove",
            PLUGIN_DIR,
            extra_dirs=[
                pu.venv_bin_dir("venv"),
                os.path.expanduser("~/miniconda3/bin"),
            ],
        )
        return os.path.dirname(pitremove) if pitremove else None

    def _locate_mpi(self):
        """Absolute path to the MPI launcher, or None if not installed."""
        return pu.find_exe("mpiexec", "mpiexec")

    def _taudem(self, name):
        """Full path to one TauDEM tool, with the platform executable suffix."""
        return os.path.join(self.taudem_dir, name + pu.EXE_SUFFIX)

    def _out(self, name):
        return os.path.join(self.output_dir, name)

    def _routing_inputs(self):
        """Rasters the routing engine reads, keyed by its flag names."""
        return {
            "fel": self._out("fel.tif"),
            "ang": self._out("ang.tif"),
            "source": self._out("source.tif"),
            "dfi": self._out("dfi_clean.tif"),
            "alpha": self._out("alpha.tif"),
        }

    def _missing_tools_message(self):
        if not self.taudem_dir:
            return (
                "TauDEM not found.\n\nInstall TauDEM, or set its location "
                "in the plugin's Dependencies panel."
            )
        if not self.mpi_exe:
            return (
                "MPI launcher (mpiexec) not found.\n\nInstall OpenMPI "
                "(macOS/Linux) or MS-MPI (Windows), or set its location "
                "in the plugin's Dependencies panel."
            )
        return None

    def _run_taudem(self, env, fel, ang, slp):
        mpi_base = [self.mpi_exe, "-n", str(self.cores)]
        steps = (
            ("Pit-filling DEM", ["pitremove", "-z", self.dem_path, "-fel", fel], 10),
            (
                "Computing Flow Direction & Slope",
                ["dinfflowdir", "-fel", fel, "-ang", ang, "-slp", slp],
                25,
            ),
        )
        for number, (label, (tool, *args), done) in enumerate(steps, start=1):
            self.log.emit(f"\n[TauDEM] Step {number}/{len(steps)}: {label}...")
            subprocess.run(
                mpi_base + [self._taudem(tool)] + args,
                check=True,
                capture_output=True,
                env=env,
                **pu.subprocess_kwargs(),
            )
            self.progress.emit(done)

    def _prepare_inputs(self, slp, dfi_clean, alpha, source):
        """Derive the cleaned DFI, alpha prior and source mask Step 4 needs."""
        self.log.emit("   > Cleaning DFI Raster (Clamping probabilities to [0, 1])...")
        dfi, profile = raster_io.read_band(self.dfi_tif)
        raster_io.write_band(dfi_clean, dr.clamp_dfi(dfi), profile)
        self.progress.emit(30)

        self.log.emit("   > Generating Baseline Alpha Raster (Step 0 Logic)...")
        slope, profile = raster_io.read_band(slp)
        raster_io.write_band(alpha, dr.alpha_from_slope(slope, profile.nodata), profile)
        self.progress.emit(35)

        self.log.emit(
            f"   > Generating Binary Source Raster (Target DFI > {SOURCE_THRESHOLD})..."
        )
        clean, profile = raster_io.read_band(dfi_clean)
        mask = dr.source_mask(clean, profile.nodata, SOURCE_THRESHOLD, self.log.emit)
        raster_io.write_band(source, mask, profile)
        self.progress.emit(45)

    def _routing_engine(self):
        """The C++/MPI engine shipped for this platform, or None after reporting.

        Same bin/<goos>_<goarch>/ layout as the Go zonal engine, so both go
        through one resolver.
        """
        engine = pu.bundled_binary(PLUGIN_DIR, ENGINE_NAME)
        if engine:
            return engine
        expected = os.path.join(
            PLUGIN_DIR, "bin", pu.platform_tag(), ENGINE_NAME + pu.EXE_SUFFIX
        )
        self.error.emit(
            f"Depositional routing engine not available for this "
            f"platform ({pu.platform_tag()}).\n\nExpected:\n{expected}\n\n"
            "Reinstall the plugin, or build it with "
            "./build_native.sh."
        )
        return None

    def _run_routing(self, engine, env, inputs, output_dep):
        self.log.emit("\n--- INITIATING C++/MPI DEPOSITIONAL ROUTING ---")
        cmd = build_routing_command(
            self.mpi_exe, self.cores, engine, inputs, self.output_dir, output_dep
        )
        process = self._track(
            subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                **pu.subprocess_kwargs(),
            )
        )
        for line in iter(process.stdout.readline, ""):
            clean = line.strip()
            if clean:
                self.log.emit(f"   > MPI: {clean}")
        process.wait()
        return process.returncode

    def run(self):
        try:
            missing = self._missing_tools_message()
            if missing:
                self.error.emit(missing)
                return

            os.makedirs(self.output_dir, exist_ok=True)
            inputs = self._routing_inputs()
            slp = self._out("slp.tif")
            output_dep = self._out("step4_pure_depositional_mask.tif")
            env = pu.tool_environment()
            # Only the shipped binaries may see the bundled proj.db; a
            # separately installed TauDEM keeps its own PROJ setup.
            bundled_env = pu.bundled_environment(env, PLUGIN_DIR)
            taudem_env = (bundled_env
                          if pu.is_bundled(PLUGIN_DIR, self._taudem("pitremove"))
                          else env)

            self._run_taudem(taudem_env, inputs["fel"], inputs["ang"], slp)
            self._prepare_inputs(slp, inputs["dfi"], inputs["alpha"], inputs["source"])

            if self.is_cancelled:
                return

            engine = self._routing_engine()
            if not engine:
                return

            returncode = self._run_routing(engine, bundled_env, inputs, output_dep)

            if self.is_cancelled:
                self.log.emit("\n--> Depositional routing stopped.")
                return

            if returncode != 0:
                self.error.emit(f"C++/MPI Step 4 failed with exit code {returncode}.")
                return

            self.progress.emit(100)
            self.log.emit("\n✅ Depositional Routing Complete!")
            self.finished.emit(output_dep, "success")

        except dr.DepositionInputError as e:
            self.error.emit(str(e))
        except Exception as e:
            if self.is_cancelled:
                return
            self.error.emit(
                f"Depositional Worker Error:\n{str(e)}\n{traceback.format_exc()}"
            )
