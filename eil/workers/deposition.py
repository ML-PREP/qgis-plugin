"""Stage 3 of the EIL pipeline: the depositional zone, as in MLPREP Plugin v4.4.

    TauDEM hydrology -> ML-DFI predictors -> ML-DFI model -> Step 0 alpha
      -> FoS source refinement -> engine inputs -> C++/MPI routing
      -> Step 5 runout cleanup -> final rasters copied to the output folder

Every step is a separate process (TauDEM under MPI, or a script in the EIL
venv, which has rasterio), so Cancel can stop it and no full-size raster is
ever loaded into QGIS. File names, argv and configs live in depzone_commands.
"""

import json
import os
import shutil
import subprocess
import traceback

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ... import platform_utils as pu
from ...common import paths as plugin_paths
from ...common.cancellable import CancellableProcessMixin
from ...common.paths import PLUGIN_DIR
from . import depzone_commands as dc
from .commands import build_routing_command

ENGINE_NAME = "step4_deposition_zone_mpi"

# Characters of a failed step's output quoted in the error.
ERROR_TAIL = 2000
# depzone/prep.py engine-inputs exits with this when no cell can seed routing.
EXIT_NO_SOURCES = 3

# This stage's own 0-100 progress after each step. The TauDEM D-infinity
# passes dominate (dinfflowdir alone was 42 of 46 minutes on 4 cores).
STEP_PROGRESS = (
    ("_hydrology", 25),
    ("_predictors", 62),
    ("_ml_dfi", 70),
    ("_alpha", 72),
    ("_fos_sources", 76),
    ("_engine_inputs", 79),
    ("_route", 93),
    ("_cleanup", 99),
    ("_publish", 100),
)


class StepFailed(Exception):
    """A step's process exited non-zero; the message is shown to the user."""


class Cancelled(Exception):
    """Cancel was clicked; the stage stops quietly."""


class MLDFIDepositionWorker(CancellableProcessMixin, QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        dem_path,
        ndvi_path,
        fos_inputs,
        python_exe,
        output_dir,
        publish_dir,
        cores=4,
    ):
        super().__init__()
        self._init_cancellation()
        self.dem_path = dem_path
        self.ndvi_path = ndvi_path
        self.fos_inputs = fos_inputs
        self.python_exe = python_exe
        self.paths = dc.DepzonePaths(output_dir)
        self.publish_dir = publish_dir
        self.cores = cores

        self.taudem_dir = self._locate_taudem()
        self.mpi_exe = pu.find_exe("mpiexec", "mpiexec")

    # ------------------------------------------------------------------
    # Tools and environments
    # ------------------------------------------------------------------
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
        missing = [p for p in plugin_paths.DEPZONE_SCRIPTS if not os.path.exists(p)]
        if missing:
            return "Depositional-zone files missing from the plugin:\n" + "\n".join(
                missing
            )
        return None

    def _environments(self):
        env = pu.tool_environment()
        # Only the shipped binaries may see the bundled proj.db; a separately
        # installed TauDEM keeps its own PROJ setup.
        self._script_env = env
        self._bundled_env = pu.bundled_environment(env, PLUGIN_DIR)
        bundled_taudem = pu.is_bundled(PLUGIN_DIR, self._tool("pitremove"))
        self._taudem_env = self._bundled_env if bundled_taudem else env

    def _tool(self, name):
        return os.path.join(self.taudem_dir, name + pu.EXE_SUFFIX)

    # ------------------------------------------------------------------
    # Running one step
    # ------------------------------------------------------------------
    def _run(self, label, argv, env, prefix=None):
        """Run `argv` where Cancel can stop it; returns its output lines."""
        if self.is_cancelled:
            raise Cancelled()
        self.log.emit(f"   > {label}...")
        process = self._track(
            subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                **pu.subprocess_kwargs(),
            )
        )
        lines = []
        for line in iter(process.stdout.readline, ""):
            clean = line.strip()
            if clean:
                lines.append(clean)
                if prefix:
                    self.log.emit(f"   > {prefix}: {clean}")
        process.wait()
        if self.is_cancelled:
            raise Cancelled()
        if process.returncode != 0:
            raise StepFailed(
                f"{label} failed with exit code {process.returncode}:\n"
                + "\n".join(lines)[-ERROR_TAIL:]
            )
        return lines

    def _taudem(self, label, tool, *args):
        argv = dc.taudem(self.mpi_exe, self.cores, self._tool(tool), *args)
        return self._run(f"TauDEM {tool}: {label}", argv, self._taudem_env)

    def _prep(self, label, command, *positional, **flags):
        argv = dc.prep(
            self.python_exe,
            plugin_paths.DEPZONE_PREP_SCRIPT,
            command,
            *positional,
            **flags,
        )
        return self._run(label, argv, self._script_env, prefix="Prep")

    def _script(self, label, argv, prefix):
        return self._run(label, argv, self._script_env, prefix=prefix)

    @staticmethod
    def _json_tail(lines):
        """The JSON object a prep subcommand prints as its last line."""
        return json.loads(lines[-1]) if lines else {}

    # ------------------------------------------------------------------
    # The steps, in order (see STEP_PROGRESS)
    # ------------------------------------------------------------------
    def _hydrology(self):
        p = self.paths
        self.log.emit("\n[TauDEM] Pit filling and D-infinity flow directions")
        self._taudem("pit filling", "pitremove", "-z", self.dem_path, "-fel", p.fel)
        self._taudem(
            "flow direction & slope",
            "dinfflowdir",
            "-fel",
            p.fel,
            "-ang",
            p.ang,
            "-slp",
            p.slp,
        )
        self._prep(
            "Slope tangent to degrees",
            "slope-degrees",
            tangent=p.slp,
            out=p.slp_degrees,
        )

    def _predictors(self):
        p = self.paths
        self.log.emit("\n[Predictors] Generating the ML-DFI predictors")
        self._prep(
            "Aligning NDVI to the DEM grid",
            "align",
            src=self.ndvi_path,
            ref=self.dem_path,
            out=p.aligned_ndvi,
        )
        self._prep(
            "Plan & profile curvature",
            "curvatures",
            dem=self.dem_path,
            profile=p.profile_curvature,
            plan=p.plan_curvature,
        )
        self._script(
            "Weiss slope position & Horn slope",
            dc.weiss(
                self.python_exe, plugin_paths.WEISS_SCRIPT, self.dem_path, p.out_dir
            ),
            prefix="Weiss",
        )
        # -nc: without it TauDEM NoDatas every cell whose upslope area touches
        # the DEM edge (the reference lineage ran AreaDinf with it).
        self._taudem(
            "specific catchment area", "areadinf", "-ang", p.ang, "-sca", p.sca, "-nc"
        )
        self._taudem(
            "stream definition",
            "threshold",
            "-ssa",
            p.sca,
            "-src",
            p.src,
            "-thresh",
            str(dc.STREAM_SCA_THRESHOLD),
        )
        self._prep(
            "Seeding domain-boundary outlets",
            "seed-outlets",
            src=p.src,
            ang=p.ang,
            out=p.src_routing,
        )
        self._taudem(
            "distance down to stream",
            "dinfdistdown",
            "-ang",
            p.ang,
            "-fel",
            p.fel,
            "-src",
            p.src_routing,
            "-dd",
            p.dstgsurf,
            "-m",
            "ave",
            "s",
            "-nc",
        )
        # -thresh 0 per the reference lineage; TauDEM's 0.5 default truncates
        # most flow paths and collapses the distances.
        self._taudem(
            "distance up to ridge",
            "dinfdistup",
            "-ang",
            p.ang,
            "-fel",
            p.fel,
            "-slp",
            p.slp,
            "-du",
            p.dugsurf,
            "-m",
            "ave",
            "s",
            "-thresh",
            "0",
            "-nc",
        )
        self._prep(
            "Topographic Wetness Index", "twi", sca=p.sca, slope=p.slp, out=p.twi
        )
        self._prep(
            "Low-pass filtering the predictors",
            "lowpass",
            *dc.lowpass_sources(p).values(),
            out_dir=p.out_dir,
        )

    def _ml_dfi(self):
        p = self.paths
        self.log.emit("\n[ML-DFI] Running the depositional favorability model")
        with open(p.predictor_config, "w", encoding="utf-8") as handle:
            json.dump(dc.predictor_config(p), handle, indent=4)
        self._script(
            "ML-DFI inference",
            dc.apply_model(
                self.python_exe,
                plugin_paths.APPLY_DFI_SCRIPT,
                plugin_paths.DFI_MODEL,
                plugin_paths.DFI_MODEL_METADATA,
                p,
            ),
            prefix="ML-DFI",
        )
        self._prep(
            "Clamping DFI to [0, 1]", "clean-dfi", src=p.raw_dfi, out=p.dfi_clean
        )

    def _alpha(self):
        self.log.emit("\n[Step 0] Baseline alpha angle")
        argv = dc.step0(self.python_exe, plugin_paths.STEP0_SCRIPT, self.paths)
        self._script("Baseline alpha", argv, prefix="Step 0")

    def _fos_sources(self):
        p, inputs = self.paths, self.fos_inputs
        self.log.emit(
            "\n[FoS] Refining PINN susceptibility into a cell-scale source mask"
        )
        os.makedirs(p.fos_dir, exist_ok=True)
        bulk = self._json_tail(
            self._prep(
                "Masking non-positive bulk density",
                "sanitize-bulk",
                src=inputs.bulk_density,
                out=p.bulk_masked,
            )
        )
        self.log.emit(
            f"   > Bulk density median {bulk['median']:.3f} read as {bulk['note']}; "
            f"{bulk['masked']} non-positive cells masked"
        )
        if inputs.factor_of_safety:
            self.log.emit("   > Using the PINN's own FactorOfSafety raster")
        else:
            self.log.emit("   > No PINN FoS raster; deriving cell-scale FoS instead")
        with open(p.fos_config, "w", encoding="utf-8") as handle:
            json.dump(
                dc.fos_config(p, inputs, p.bulk_masked, bulk["scale"]), handle, indent=2
            )
        argv = dc.with_config(
            self.python_exe, plugin_paths.FOS_REFINEMENT_SCRIPT, p.fos_config
        )
        self._script("FoS source refinement", argv, prefix="FoS")
        self._log_fos_counts()

    def _log_fos_counts(self):
        try:
            with open(self.paths.fos_summary, encoding="utf-8") as handle:
                counts = json.load(handle).get("counts", {})
        except (OSError, ValueError):
            return
        self.log.emit(
            f"   > FoS: {counts.get('retained_source_cells', '?')} source cells "
            f"(FoS < {dc.FOS_THRESHOLD}) | "
            f"{counts.get('stable_cells_excluded', '?')} stable | "
            f"{counts.get('unresolved_terrain_cells', '?')} unresolved"
        )

    def _engine_inputs(self):
        p = self.paths
        try:
            lines = self._prep(
                "Building the routing engine's inputs",
                "engine-inputs",
                ang=p.ang,
                fel=p.fel,
                alpha=p.alpha,
                dfi=p.dfi_clean,
                source_mask=p.refined_mask,
                out_dir=p.out_dir,
            )
        except StepFailed as failure:
            if f"exit code {EXIT_NO_SOURCES}" in str(failure):
                raise StepFailed(
                    "No legal source cell for depositional routing: every "
                    "FoS-refined source lacks an alpha angle in [6, 72] degrees. "
                    "Check Step 0's alpha raster (BaselineAlpha.tif)."
                ) from failure
            raise
        counts = self._json_tail(lines)
        self.log.emit(
            f"   > Sources: {counts['source_candidates']} FoS-refined | "
            f"{counts['legal_sources']} accepted by the engine's alpha rule | "
            f"DFI predicted on {counts['dfi_predicted']} of {counts['terrain']} cells"
        )

    def _route(self):
        p = self.paths
        engine = pu.bundled_binary(PLUGIN_DIR, ENGINE_NAME)
        if not engine:
            raise StepFailed(
                f"Depositional routing engine not available for this platform "
                f"({pu.platform_tag()}). Reinstall the plugin, or build it with "
                "./build_native.sh."
            )
        self.log.emit("\n--- INITIATING C++/MPI DEPOSITIONAL ROUTING ---")
        argv = build_routing_command(
            self.mpi_exe,
            self.cores,
            engine,
            dc.routing_inputs(p),
            p.out_dir,
            p.deposition,
        )
        self._run("C++/MPI routing", argv, self._bundled_env, prefix="MPI")

    def _cleanup(self):
        p = self.paths
        self.log.emit("\n[Step 5] Cleaning the runout")
        self._taudem(
            "source-weighted contributing area",
            "areadinf",
            "-ang",
            p.ang,
            "-sca",
            p.source_sca,
            "-wg",
            p.source_final,
            "-nc",
        )
        os.makedirs(p.step5_dir, exist_ok=True)
        with open(p.step5_config, "w", encoding="utf-8") as handle:
            json.dump(dc.step5_config(p), handle, indent=2)
        argv = dc.with_config(
            self.python_exe, plugin_paths.STEP5_CLEANUP_SCRIPT, p.step5_config
        )
        self._script("Step 5 runout cleanup", argv, prefix="Step 5")

    def _publish(self):
        for name, source in dc.published_rasters(self.paths).items():
            if os.path.exists(source):
                shutil.copy2(source, os.path.join(self.publish_dir, name))

    # ------------------------------------------------------------------
    def run(self):
        try:
            missing = self._missing_tools_message()
            if missing:
                self.error.emit(missing)
                return
            os.makedirs(self.paths.out_dir, exist_ok=True)
            self._environments()
            for step, done in STEP_PROGRESS:
                getattr(self, step)()
                self.progress.emit(done)
            self.log.emit("\n✅ Depositional zone complete (Steps 0-5)!")
            self.finished.emit(self.publish_dir, "success")
        except Cancelled:
            self.log.emit("\n--> Depositional routing stopped.")
        except StepFailed as failure:
            self.error.emit(str(failure))
        except Exception as exc:
            if self.is_cancelled:
                return
            self.error.emit(
                f"Depositional Worker Error:\n{exc}\n{traceback.format_exc()}"
            )
