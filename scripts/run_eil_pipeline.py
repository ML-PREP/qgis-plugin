#!/usr/bin/env python3
"""Run the EIL pipeline headlessly, through the plugin's own code.

Drives eil.pipeline.EilPipeline -- the object the EIL tab drives -- so every
stage runs exactly what the plugin runs, without opening QGIS:

    Go zonal engine -> seam cleanup (QGIS Processing) -> pinn_inference.py
        -> dashboard -> TauDEM pitremove/dinfflowdir -> C++/MPI routing

Only the GUI around the pipeline is replaced:

    the EIL tab          ConsoleView: logs to stdout and a log file
    the dashboard        ConsoleDashboard: tallies hazard classes (the real one
                         ranks municipalities and needs the gitignored roi_data)
    Dependencies panel   HeadlessDependencies: the same check_dependencies()
                         data the panel shows
    InfinitePrintingProbe  disabled unless --with-probe (it only prints)

Built for the Linux container in docker/eil-linux/ (see eil-docker.sh), where
the shipped bin/linux_amd64 TauDEM tools run. It works anywhere QGIS's Python
bindings import.

    run_eil_pipeline.py --dem /data/dem.tif \\
        --raster Slope=/data/slope.tif --raster Clay=/data/clay.tif ...

Raster names are the model inputs (eil.params.MODEL_INPUT_NAMES); the DEM is
always fed in as "Elev", as the tab does.
"""

import argparse
import importlib
import os
import signal
import subprocess
import sys
import time
import types

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A synthetic package, as in the tests: the plugin's __init__ is never run.
PACKAGE = "mlprep_plugin"

# Same as eil.inputs (a Qt module, so not importable before QGIS starts).
RASTER_SUFFIXES = (".tif", ".tiff")
DEFAULT_CRS = "EPSG:4326"

DEFAULT_STALL_GRACE_SECONDS = 30
WATCHDOG_INTERVAL_MS = 1000
PROGRESS_LOG_STEP = 10
LOG_FILE_NAME = "eil_headless_run.log"
ENVIRONMENT_FILE_NAME = "eil_headless_environment.txt"
EIL_VENV = "venv"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_MISSING_DEPENDENCIES = 3
EXIT_CANCELLED = 130


def plugin_module(name):
    """Import `name` (e.g. "eil.params") from the plugin package."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [PLUGIN_DIR]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")


params = plugin_module("eil.params")


# ---------------------------------------------------------------------------
# Inputs (pure)
# ---------------------------------------------------------------------------

def parse_raster_args(items):
    """{model input name: path} from NAME=PATH strings; ValueError if invalid."""
    known = set(params.MODEL_INPUT_NAMES.values())
    rasters = {}
    for item in items:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"Expected NAME=PATH, got '{item}'.")
        if name == params.DEM_INPUT_NAME:
            raise ValueError(
                f"'{name}' is always the DEM; pass it with --dem instead."
            )
        if name not in known:
            raise ValueError(
                f"Unknown raster name '{name}'. Known: {', '.join(sorted(known))}."
            )
        if name in rasters:
            raise ValueError(f"Raster '{name}' was given twice.")
        rasters[name] = path
    return rasters


def select_rasters(dem_path, named, log):
    """What EilInputs.selected_rasters would hand the pipeline.

    The tab silently skips missing or non-GeoTIFF selections; here each skip
    is logged, since a dropped input changes what the model receives.
    """
    selected = {}
    if os.path.exists(dem_path):
        selected[params.DEM_INPUT_NAME] = dem_path
    for name, path in named.items():
        if not os.path.exists(path):
            log(f"[Warning] {name}: {path} does not exist; ignored (as the tab would).")
        elif not path.lower().endswith(RASTER_SUFFIXES):
            log(f"[Warning] {name}: {path} is not a GeoTIFF; ignored (as the tab would).")
        else:
            selected[name] = path
    return selected


def default_output_folder(dem_path):
    """EIL_Results beside the DEM -- the tab's default."""
    return os.path.join(os.path.dirname(dem_path), params.DEFAULT_OUTPUT_SUBDIR)


def expected_outputs(output_folder):
    """(label, path) of every file the stages write, in pipeline order."""
    deposition = os.path.join(output_folder, params.DEPOSITION_OUTPUT_SUBDIR)

    def out(*parts):
        return os.path.join(output_folder, *parts)

    def dep(name):
        return os.path.join(deposition, name)

    return [
        ("Slope units (Go)", out("Final_SlopeUnits.gpkg")),
        ("Slope units, seams removed", out("Cleaned_SlopeUnits.gpkg")),
        ("PINN features (Go)", out("Merged_PINN_Features.gpkg")),
        ("PINN features, seams removed", out("Cleaned_PINN_Features.gpkg")),
        ("Hazard map (PINN)", out("Final_Hazard_Map.gpkg")),
        ("PINN raster (Overall_Output.tif)", out("overall_output", "Overall_Output.tif")),
        ("TauDEM pit-filled DEM", dep("fel.tif")),
        ("TauDEM D-inf flow angle", dep("ang.tif")),
        ("TauDEM slope", dep("slp.tif")),
        ("Routing source mask", dep("source.tif")),
        ("Depositional mask", dep("step4_pure_depositional_mask.tif")),
    ]


# ---------------------------------------------------------------------------
# Run bookkeeping (pure)
# ---------------------------------------------------------------------------

class StageClock:
    """How long each pipeline stage (as named by set_stage) ran."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._finished = []
        self._current = None
        self._started = None

    def enter(self, stage):
        if stage == self._current:
            return
        self.close()
        self._current = stage
        self._started = self._clock()

    def close(self):
        if self._current is not None:
            self._finished.append((self._current, self._clock() - self._started))
        self._current = None

    def durations(self):
        return list(self._finished)


class StallDetector:
    """True once the pipeline claims to run but no worker has lived for a while.

    A stage that returns without emitting finished/error leaves the tab busy
    forever; in the plugin that looks like a hang.
    """

    def __init__(self, grace_seconds, clock=time.monotonic):
        self._grace = grace_seconds
        self._clock = clock
        self._idle_since = None

    def observe(self, is_running, worker_alive):
        if not is_running or worker_alive:
            self._idle_since = None
            return False
        if self._idle_since is None:
            self._idle_since = self._clock()
        return self._clock() - self._idle_since >= self._grace


# ---------------------------------------------------------------------------
# Stand-ins for the GUI around EilPipeline
# ---------------------------------------------------------------------------

class ConsoleView:
    """The surface of eil.tab.EilTab that EilPipeline uses, on a console."""

    def __init__(self, log_path, error_level, clock=time.monotonic):
        self._clock = clock
        self._t0 = clock()
        self._log_file = open(log_path, "a", encoding="utf-8")
        self._error_level = error_level
        self._running = False
        self._progress_bucket = -1
        self.stages = StageClock(clock)
        self.errors = []
        self.dashboard = None
        self.on_stopped = None

    @property
    def is_running(self):
        return self._running

    def log(self, message, level=None):
        text = str(message)
        stamp = time.strftime("%H:%M:%S", time.gmtime(self._clock() - self._t0))
        for line in text.splitlines() or [""]:
            rendered = f"[{stamp}] {line}"
            print(rendered, flush=True)
            self._log_file.write(rendered + "\n")
        self._log_file.flush()
        if level == self._error_level or "[CRITICAL ERROR]" in text:
            self.errors.append(text.strip())

    def record(self, lines):
        """Write already-printed lines to the log file only."""
        for line in lines:
            self._log_file.write(f"[setup] {line}\n")
        self._log_file.flush()

    def set_progress(self, value):
        bucket = int(value) // PROGRESS_LOG_STEP
        if bucket != self._progress_bucket:
            self._progress_bucket = bucket
            self.log(f"   [progress {int(value)}%]")

    def should_load_results(self):
        return False

    def set_running(self, running, stage="", hint=""):
        self._running = running
        if running:
            self._enter(stage, hint)
            return
        self.stages.close()
        if self.on_stopped:
            self.on_stopped()

    def set_stage(self, stage, hint=""):
        if self._running:
            self._enter(stage, hint)

    def _enter(self, stage, hint):
        self.stages.enter(stage)
        self.log(f"\n===== STAGE: {stage} =====")
        if hint:
            self.log(f"      {hint}")

    def close(self):
        self._log_file.close()


class ConsoleDashboard:
    """Replaces eil.dashboard: hazard-class tally of the PINN output."""

    def __init__(self, log):
        self._log = log

    def update(self, hazard_gpkg):
        from qgis.core import QgsVectorLayer

        hazard_stats = plugin_module("eil.hazard_stats")
        layer = QgsVectorLayer(hazard_gpkg or "", "hazard", "ogr")
        if not layer.isValid():
            self._log(f"[Dashboard] Cannot open hazard map: {hazard_gpkg}")
            return
        if layer.fields().indexOf("Hazard_Susceptibility") == -1:
            self._log("[Dashboard] Hazard map has no Hazard_Susceptibility field.")
            return

        counts = {name: 0 for name in hazard_stats.HAZARD_CLASSES}
        unreadable = out_of_range = 0
        for feature in layer.getFeatures():
            value = feature["Hazard_Susceptibility"]
            counts[hazard_stats.classify_susceptibility(value)] += 1
            score = _as_float(value)
            if score is None:
                unreadable += 1
            elif not 0.0 <= score <= 1.0:
                out_of_range += 1

        tally = ", ".join(f"{name}: {count}" for name, count in counts.items())
        self._log(f"[Dashboard] {layer.featureCount()} slope units -> {tally}")
        if unreadable:
            self._log(f"[Dashboard] {unreadable} NULL/NaN scores (counted as Low).")
        if out_of_range:
            self._log(f"[Dashboard] {out_of_range} scores outside [0, 1].")


def _as_float(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return None if score != score else score  # NaN


class HeadlessDependencies:
    """The parts of dependency_panel.DependencyPanel the EIL code calls."""

    def __init__(self, pu):
        self._pu = pu
        self._statuses = []

    def refresh(self):
        self._statuses = self._pu.check_dependencies(PLUGIN_DIR)

    def missing_for(self, feature=None):
        return [
            status for status in self._statuses
            if not status.found
            and (feature is None or status.spec.feature == feature)
        ]

    def report(self, log):
        log("Dependencies (same lookup as the plugin's Dependencies panel):")
        for status in self._statuses:
            mark = "OK " if status.found else "MISSING"
            log(f"   [{mark}] {status.spec.label:<38} {status.path or status.spec.hint}")


class _NoProbe:
    def start(self):
        pass

    def kill(self):
        pass


# ---------------------------------------------------------------------------
# QGIS, setup and the run itself
# ---------------------------------------------------------------------------

def start_qgis():
    """A QgsApplication with Processing loaded, as inside the QGIS desktop."""
    from qgis.core import QgsApplication

    QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
    app = QgsApplication([], True)
    app.initQgis()

    sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
    from processing.core.Processing import Processing

    Processing.initialize()
    registry = QgsApplication.processingRegistry()
    if registry.providerById("native") is None:
        from qgis.analysis import QgsNativeAlgorithms

        registry.addProvider(QgsNativeAlgorithms())
    return app


def provision(log):
    """The EIL half of the plugin's first-run setup (app.setup_worker)."""
    pu = plugin_module("platform_utils")
    setup_worker = plugin_module("app.setup_worker")
    grass_addons = plugin_module("grass_addons")

    venv_name, requirements, label = next(
        env for env in setup_worker.ENVIRONMENTS if env[0] == EIL_VENV
    )
    worker = setup_worker.SetupWorker(PLUGIN_DIR)
    worker.progress.connect(log)
    log(f"Setup: {label} environment in {pu.venv_bin_dir(venv_name)} "
        "(first run downloads TensorFlow; pip is silent for several minutes)")
    worker._provision(
        pu.base_python(), pu.tool_environment(), venv_name, requirements, label
    )
    grass_addons.ensure_bundled_addon(PLUGIN_DIR, log=log)


def write_environment_report(path, pu):
    """Platform tag and the EIL venv's package versions, for later comparison."""
    python = pu.venv_python(EIL_VENV)
    lines = [f"platform_tag: {pu.platform_tag()}", f"venv_python: {python}"]
    try:
        freeze = subprocess.run(
            [python, "-m", "pip", "freeze"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        freeze = f"pip freeze failed: {exc}\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n" + freeze)


def build_request(args, log):
    """EilRunRequest as EilTab._build_request builds it, or None."""
    from qgis.core import QgsRasterLayer

    pipeline_module = plugin_module("eil.pipeline")
    dem_path = os.path.abspath(args.dem)
    if not os.path.exists(dem_path):
        log(f"[ERROR] Could not locate DEM file:\n{dem_path}")
        return None

    layer = QgsRasterLayer(dem_path, "temp_dem")
    dem_crs = layer.crs().authid() if layer.isValid() else DEFAULT_CRS
    if not dem_crs:
        log("[Warning] The DEM's CRS has no authority id; the Go engine gets "
            "an empty -targetCRS (the plugin would do the same).")

    rasters = select_rasters(dem_path, parse_raster_args(args.raster), log)
    output_folder = os.path.abspath(args.output or default_output_folder(dem_path))
    os.makedirs(output_folder, exist_ok=True)
    return pipeline_module.EilRunRequest(
        dem_path=dem_path, dem_crs=dem_crs, rasters=rasters,
        output_folder=output_folder,
    )


def _workers(pipeline):
    return [w for w in (pipeline._zonal_worker, pipeline._pinn_worker,
                        pipeline._deposition_worker) if w is not None]


def drive(app, pipeline, view, request, stall_grace):
    """Start the pipeline and spin the Qt loop until it stops. Returns outcome."""
    from qgis.PyQt.QtCore import QTimer

    outcome = {"stalled": False, "cancelled": False}
    detector = StallDetector(stall_grace)

    def watchdog():
        alive = any(w.isRunning() for w in _workers(pipeline))
        if detector.observe(view.is_running, alive):
            view.log(f"\n[STALL] Pipeline still marked running, but no stage has "
                     f"been active for {stall_grace}s: a stage ended without "
                     "signalling. The plugin would look hung here.")
            outcome["stalled"] = True
            app.quit()

    def on_sigint(_signum, _frame):
        outcome["cancelled"] = True
        pipeline.cancel()

    # The timer also hands control back to Python so Ctrl+C is noticed.
    timer = QTimer()
    timer.timeout.connect(watchdog)
    timer.start(WATCHDOG_INTERVAL_MS)
    signal.signal(signal.SIGINT, on_sigint)
    view.on_stopped = lambda: QTimer.singleShot(0, app.quit)

    pipeline.start(request)
    if view.is_running:
        app.exec_()
    timer.stop()
    for worker in _workers(pipeline):
        worker.wait()
    return outcome


def summarize(view, output_folder, outcome):
    """Print stage timings and outputs; returns the exit code."""
    view.log("\n===== SUMMARY =====")
    for stage, seconds in view.stages.durations():
        view.log(f"   {stage:<28} {seconds:8.1f} s")
    view.log("Outputs (seam-cleaned files appear only when slivers were merged):")
    for label, path in expected_outputs(output_folder):
        size = f"{os.path.getsize(path) / 1e6:9.2f} MB" if os.path.exists(path) else "      --   "
        view.log(f"   {size}  {label:<34} {os.path.relpath(path, output_folder)}")
    for error in view.errors:
        view.log(f"[error seen] {error.splitlines()[0]}")

    if outcome["cancelled"]:
        return EXIT_CANCELLED
    final_mask = expected_outputs(output_folder)[-1][1]
    if outcome["stalled"] or view.errors or not os.path.exists(final_mask):
        view.log("RESULT: pipeline did NOT reach the depositional mask.")
        return EXIT_FAILED
    view.log("RESULT: pipeline completed through depositional routing.")
    return EXIT_OK


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dem", help="DEM GeoTIFF (also fed in as 'Elev')")
    parser.add_argument("--raster", action="append", default=[], metavar="NAME=PATH",
                        help="model input raster; repeat for each "
                             f"({', '.join(params.MODEL_INPUT_NAMES.values())})")
    parser.add_argument("--output", help="output folder (default: EIL_Results beside the DEM)")
    parser.add_argument("--setup-only", action="store_true",
                        help="provision the EIL venv + GRASS addon, then exit")
    parser.add_argument("--skip-setup", action="store_true",
                        help="do not run the first-run setup step")
    parser.add_argument("--with-probe", action="store_true",
                        help="also start the experimental InfinitePrintingProbe")
    parser.add_argument("--stall-grace", type=int, default=DEFAULT_STALL_GRACE_SECONDS,
                        help="seconds without a live stage before reporting a stall")
    args = parser.parse_args(argv)
    if not args.setup_only and not args.dem:
        parser.error("--dem is required (unless --setup-only)")
    return args


def run(args, app):
    pu = plugin_module("platform_utils")
    log_panel = plugin_module("log_panel")
    # Setup and input selection run before the log file exists; their lines
    # (dropped-input warnings above all) are copied into it once it does.
    early_lines = []

    def early_log(message, _level=None):
        print(message, flush=True)
        early_lines.append(str(message))

    if not args.skip_setup:
        provision(early_log)
    if args.setup_only:
        return EXIT_OK

    request = build_request(args, early_log)
    if request is None:
        return EXIT_USAGE
    view = ConsoleView(os.path.join(request.output_folder, LOG_FILE_NAME),
                       log_panel.LEVEL_ERROR)
    view.record(early_lines)
    try:
        return _run_with_view(args, app, pu, request, view)
    finally:
        view.close()


def _run_with_view(args, app, pu, request, view):
    view.dashboard = ConsoleDashboard(view.log)
    dependencies = HeadlessDependencies(pu)
    dependencies.refresh()
    dependencies.report(view.log)
    if dependencies.missing_for(pu.FEATURE_EIL):
        view.log("[ERROR] EIL cannot start: dependencies missing (see above).")
        return EXIT_MISSING_DEPENDENCIES

    write_environment_report(
        os.path.join(request.output_folder, ENVIRONMENT_FILE_NAME), pu)
    view.log(f"\nPlatform: {pu.platform_tag()}  DEM CRS: {request.dem_crs}")
    view.log(f"Inputs: {', '.join(sorted(request.rasters))}")
    view.log(f"Output: {request.output_folder}")
    view.log("\n--- STARTING FULL EIL PIPELINE ---")

    pipeline = plugin_module("eil.pipeline").EilPipeline(view, dependencies)
    if not args.with_probe:
        pipeline._probe = _NoProbe()
    try:
        outcome = drive(app, pipeline, view, request, args.stall_grace)
    finally:
        # The plugin never stops the probe after a normal finish; do it here.
        pipeline._probe.kill()
    return summarize(view, request.output_folder, outcome)


def main(argv=None):
    args = parse_args(argv)
    try:
        parse_raster_args(args.raster)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    app = start_qgis()
    try:
        return run(args, app)
    finally:
        app.exitQgis()


if __name__ == "__main__":
    sys.exit(main())
