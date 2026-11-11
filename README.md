# MLPREP Detection V4 — QGIS Plugin

Landslide detection and Earthquake-Induced Landslide (EIL) hazard mapping in one
QGIS toolbar. The EIL model is a Physics-Informed Neural Network (PINN) that
embeds Newmark displacement analysis directly in the network architecture, with
an optional depositional-routing stage (TauDEM + a C++/MPI engine + the ML-DFI
model) that maps where failed material ends up.

| | |
|---|---|
| **QGIS** | 3.22 – 3.x (`metadata.txt`) |
| **macOS** | Apple Silicon (full); Intel (no depositional routing yet, see [Platform coverage](#platform-coverage-of-the-native-binaries)) |
| **Windows** | 10/11 x64 |
| **Linux** | x64, glibc ≥ 2.36 (Debian 12+, Ubuntu 23.04+/24.04, Fedora 37+) |

## Contents

- [The plugin at a glance](#the-plugin-at-a-glance)
  - [Tabs](#tabs)
  - [What happens on first launch](#what-happens-on-first-launch)
- [Repository layout](#repository-layout)
- [Dependencies](#dependencies)
  - [Bundled with the plugin](#bundled-with-the-plugin)
  - [System dependencies (not bundled)](#system-dependencies-not-bundled)
  - [Python packages](#python-packages)
- [Installing](#installing)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Windows](#windows)
  - [Installing the plugin itself](#installing-the-plugin-itself)
- [Building binaries, bundling and packaging](#building-binaries-bundling-and-packaging)
- [Development](#development)
- [Troubleshooting and known gaps](#troubleshooting-and-known-gaps)

---

## The plugin at a glance

The plugin adds one toolbar button and a **Plugins → MLPREP Suite → MLPREP
Detection Suite** menu entry (`mainPlugin.py`). Both open a single maximised
window (`app/main_window.py`) with three tabs.

### Tabs

#### 1. Landslide Detection — `landslide/`

Detects landslide scars from post-event imagery with a Keras segmentation model.
It needs **no external programs**, only its Python environment.

| Panel | Contents |
|---|---|
| **File Inputs** | DEM (`.tif`), LULC shapefile (`.shp`), post-event Sentinel-2 image (`.tif`) |
| **Region of Interest** | Admin-boundary picker (province / municipality) from `roi_data/` |
| **Input Previews** | Post-event image, DEM, slope (computed with QGIS Processing) and LULC |
| **Final Landslide Result** | The detected mask, with **DETECT** and **SAVE** buttons |

**DETECT** runs `landslide_worker.py` in the `ls_detect_env` virtualenv with
the model in `model_lsi/default/` (`best.keras` + `norm_stats.json`).
**SAVE** writes the mask as a GeoTIFF.

#### 2. EIL Detection — `eil/`

Maps earthquake-induced landslide hazard, then optionally routes the debris.

**Left panel (inputs)**

| Group | Contents |
|---|---|
| **Parameters** | DEM plus one raster each for Bulk Density, Clay, Sand, Silt, Slope, Peak Ground Acceleration (PGA), Precipitation, Soil Thickness, Soil Type / Lithology and Contributing Factor. **NDVI** is optional and is read only by the depositional-zone model. Each can be a loaded map layer or a file picked with `…`. |
| **PINN Model** | Choice of model from `model/pinn_models.json`: *Earthquake Induced*, *Rainfall-aware EIL*, or *Grid Model v2-9 (25 m cells)* |
| **Clip Results to Region of Interest** | Optional: clips the layers added to the map to one admin unit (`*_ROI` files) |
| **Slope Unit Tuning** | Max Iteration (currently not read: the engine is pinned to 1 iteration in `eil/params.py`) |
| **Output Setup** | Output folder (defaults to the DEM's folder) and *Automatically load results into map* |
| **Run Pipeline / Cancel** | |

**Right panel (results)**: a hazard dashboard with *Top 10 Most Landslide
Susceptible Municipalities* (ranked by number of High-hazard units), a *Top 10
Hazard Distribution* stacked chart, and *Feature Details* for the unit under
the cursor.

**Pipeline** (`eil/pipeline.py`). Each stage runs on a worker thread:

```
slope units + zonal statistics   Go engine (bin/<platform>/zonal) driving GRASS r.slopeunits + GDAL
        → seam cleanup           QGIS Processing
        → PINN predictions       pinn_inference.py in ~/.mlprep_envs/venv
        → hazard dashboard
        → depositional routing   TauDEM tools + C++/MPI engine under mpiexec, then the ML-DFI
                                 model (model_dfi/) and the ml_dfi_handoff/ step scripts
```

A grid model (`"kind": "grid"` in `pinn_models.json`) skips slope units and
zonal statistics. `grid_inference.py` resamples every input onto the model's
grid and predicts each cell. Depositional routing is skipped, with an
explanation in the log, when MPI or the routing engine is missing. The hazard
map still completes.

#### 3. Dependencies — `dependency_panel.py`

A preflight table of every external program and Python environment. Each row
shows ✓/✗, the pipeline that needs it, and either where it was found or how to
install it on the current platform.

- **Browse…** points a row at a copy installed somewhere unusual. The choice is
  stored in QSettings under `mlprep/exe/*` and wins over every other lookup.
- **Install** (GRASS addon row) installs the bundled `r.slopeunits` copy.
- **Set up environments** creates or repairs the virtualenvs.
- **Re-check** searches again without restarting QGIS.

The tab's label shows how many items are missing. The EIL **Run** button stays
disabled until that pipeline's requirements are met.

### What happens on first launch

The window runs three gates in order. Each blocks until it is satisfied:

1. **Boundary data**: only for a release built with `--no-roi`. A modal dialog
   downloads `roi_data/` (~1.4 GB) into `~/.mlprep_envs/roi_data`. Cancelling
   closes the plugin.
2. **GRASS GIS Setup Required**: appears if GRASS or its `r.slopeunits` addon
   is missing.
   - **Install automatically** runs the platform installer script.
   - **Continue** stays disabled until *both* are detected (it re-checks every
     few seconds, so a manual install also unlocks it).
   - **Locate GRASS…** accepts a non-standard install.
   - **Close Plugin** closes the plugin.
3. **First Time Setup**: creates the two Python virtualenvs under
   `~/.mlprep_envs`. This downloads several hundred MB from PyPI and takes a
   few minutes.

GDAL, MPI and TauDEM never block startup. They only block the pipeline that
uses them.

---

## Repository layout

```
MLPREP_Plugin_v4/
├── __init__.py, mainPlugin.py, metadata.txt   QGIS entry point and plugin metadata
├── app/                     Main window, first-run gates, ROI download dialog, setup worker
├── landslide/               Landslide Detection tab
├── eil/                     EIL Detection tab: inputs, model picker, pipeline, dashboard
│   └── workers/             QThread workers: zonal (Go), PINN, deposition (TauDEM + MPI)
├── common/                  Shared Qt/QGIS helpers, paths, ROI selector, ROI data cache
├── depzone/                 Raster preparation for the depositional-zone stage
├── pinn_grid/               Grid-model inference (alignment, features, fold ensemble)
├── py_files/                Model definitions and custom Keras layers the PINN models load
├── dependency_panel.py      Dependencies tab
├── platform_utils.py        The ONE place external programs and paths are resolved (Python side)
├── env_setup.py             Virtualenv creation and pip install
├── grass_gate.py, grass_addons.py   GRASS setup dialog and addon install/check
├── landslide_worker.py      Detection worker (runs in ls_detect_env)
├── pinn_inference.py, grid_inference.py   PINN workers (run in venv)
├── requirements_ls.txt      → ~/.mlprep_envs/ls_detect_env
├── requirements_eil.txt     → ~/.mlprep_envs/venv
│
├── model/                   PINN models + pinn_models.json (the model picker's list)
├── model_lsi/               Landslide detection model
├── model_dfi/               ML-DFI depositional favourability model (xgboost, joblib)
├── roi_data/                Philippine admin boundaries (~1.4 GB, shapefiles)
├── vendor/grass_addons/     Bundled copy of the r.slopeunits GRASS addon (offline install)
├── bin/<platform>/          Built binaries: NOT committed, produced by the build scripts
│
├── ml-prep-scripts/         Go slope-unit / zonal-statistics engine
├── ml_dfi_handoff/          ML-DFI workflow steps 0–6; step4 holds the C++/MPI routing
│   └── steps/step4_deposition_zone/cpp_mpi_port/   engine and the vendored TauDEM sources
│
├── scripts/                 GRASS installers, dylib/DLL bundlers, ROI packager, smoke tests,
│                            headless runner (run_eil_pipeline.py)
├── build_binaries.sh        Builds the Go engine for all platforms
├── build_native.sh          Builds the C++/MPI engine + TauDEM tools and bundles their libs
├── build_release.sh         Stages, verifies and zips the plugin
├── docker/eil-linux/        Linux container that runs the EIL pipeline headless
├── tests/                   pytest suite (runs without QGIS)
└── CROSS_PLATFORM.md        Cross-platform audit and remediation log
```

`eil_wrapper.py`, `landslide_wrapper.py`, `headless_model.py` and the
`*_mainwindow.py` Designer files are carried over from V3. Only the two
`*_mainwindow.py` UI files are still used.

---

## Dependencies

### Bundled with the plugin

These ship inside the release zip. Users install nothing for them.

| Component | Location | Used by | Notes |
|---|---|---|---|
| Go zonal / slope-unit engine | `bin/<platform>/zonal[.exe]` | EIL | Static Go binary (`CGO_ENABLED=0`), built for all four platforms |
| C++/MPI depositional-routing engine | `bin/<platform>/step4_deposition_zone_mpi[.exe]` | Depositional routing | Not built for `darwin_amd64` yet |
| TauDEM tools: `pitremove`, `dinfflowdir`, `areadinf`, `threshold`, `dinfdistdown`, `dinfdistup` | `bin/<platform>/` | Depositional routing | Built from the vendored TauDEM sources. Preferred over any system TauDEM unless the user picks one in the Dependencies tab |
| GDAL, PROJ and their dependency closure for the native binaries | `bin/<platform>/lib/` (macOS/Linux), DLLs beside the `.exe` (Windows), plus `share/proj/proj.db` | The native binaries only | Linked through `@rpath` / `$ORIGIN`, so they do not depend on the user's GDAL version |
| `r.slopeunits` GRASS addon | `vendor/grass_addons/r.slopeunits` | EIL | Copied into the user's GRASS addon directory by the installer scripts. No network needed |
| Models | `model/`, `model_lsi/`, `model_dfi/` | All pipelines | |
| Admin boundaries | `roi_data/` | All tabs | Omitted by `--no-roi` builds and downloaded on first run instead |

### System dependencies (not bundled)

These must be on the user's machine. The Dependencies tab checks every one.

| Dependency | Needed for | Why it is not bundled |
|---|---|---|
| **QGIS 3.22+** | Everything | Host application. Supplies Qt/PyQt, Processing and the in-process Python |
| **Python ≥ 3.10** with `venv` | Creating the virtualenvs | QGIS's own Python is used when it qualifies (it usually does). On Debian/Ubuntu `python3-venv` must be installed |
| **GRASS GIS 8.x** + `r.slopeunits` addon | EIL slope units (**startup gate**) | ~1 GB, platform-specific. Installed by `scripts/install_grass.sh` / `scripts\install_grass_windows.bat` |
| **GDAL command-line tools** (`gdalwarp`, `ogrinfo`) | EIL (the Go engine shells out to them) | Normally supplied by QGIS |
| **MPI runtime** (`mpiexec`): Open MPI on macOS/Linux, MS-MPI on Windows | Depositional routing | The binaries must be launched by the host's own `mpiexec`. A bundled MPI that disagrees with the launcher fails at startup |
| **C/C++ runtime**: `libstdc++`/glibc on Linux | Native binaries | Tied to the host's glibc. Bundling a newer one breaks older hosts. The Windows bundle *does* ship the MSVC runtime DLLs, and macOS uses the system `libc++` |
| **Internet access** on first run | pip installs, and the ROI download on `--no-roi` builds | |

Optional overrides via environment variables:

| Variable | Effect |
|---|---|
| `MLPREP_GRASS_EXE` | Path to the GRASS launcher |
| `MLPREP_GDAL_BIN` | Directory containing `gdalwarp` / `ogrinfo` |
| `MLPREP_ROI_URL` | Manifest URL for the boundary-data download (see `common/roi_data.py`) |

### Python packages

Nothing is installed into QGIS's own Python. Two managed virtualenvs are
created under `~/.mlprep_envs` (`%USERPROFILE%\.mlprep_envs` on Windows). A
stamp file records each one's requirements fingerprint, so editing a
requirements file triggers a reinstall.

| Environment | Requirements file | Runs | Packages |
|---|---|---|---|
| `ls_detect_env` | `requirements_ls.txt` | `landslide_worker.py` | numpy, rasterio, geopandas, pyogrio, shapely, tensorflow |
| `venv` | `requirements_eil.txt` | Go engine's embedded `zonal_grid.py`, `pinn_inference.py`, `grid_inference.py`, ML-DFI steps | rasterio, geopandas, pyogrio, shapely, rasterstats, numpy, scipy, pandas, tensorflow, scikit-learn, xgboost, joblib, keras-tuner, imbalanced-learn, scikeras, matplotlib, seaborn, contextily |

Inside the QGIS process the plugin uses `qgis.core`, `qgis.PyQt`,
`processing`, **numpy** (required) and **matplotlib** (optional: only the
hazard chart needs it). Most QGIS builds ship both. The Dependencies tab
reports them if they are missing.

Every module a worker imports must be listed explicitly in its requirements
file, because the venvs cannot see QGIS's site-packages.
`tests/test_worker_requirements.py` enforces this.

---

## Installing

Install the system dependencies for your platform first, then the plugin. In
every case, open the plugin's **Dependencies** tab afterwards and check that
nothing is ✗.

### macOS

Prerequisite: [Homebrew](https://brew.sh).

```sh
# 1. QGIS: the official installer from https://qgis.org (or: brew install --cask qgis)

# 2. MPI, for depositional routing
brew install open-mpi

# 3. GDAL CLI: normally inside QGIS.app already. Only if the Dependencies tab shows ✗:
brew install gdal

# 4. Python 3.10+: only needed if QGIS's Python is older (rare)
brew install python@3.12

# 5. GRASS + r.slopeunits (or let the plugin's setup dialog run this for you)
bash scripts/install_grass.sh
```

`install_grass.sh` on macOS: Homebrew has no GRASS formula, so the script
installs `micromamba` with Homebrew and then installs GRASS from conda-forge
into `~/.mlprep_envs/grass`. No administrator password is needed.

TauDEM needs no install on Apple Silicon because it is bundled. On an **Intel
Mac** the release has no routing engine or TauDEM build, so depositional
routing is skipped. Build them yourself with `./build_native.sh host` on that
Mac (see [Building](#building-binaries-bundling-and-packaging)).

### Linux

**Debian / Ubuntu**

```sh
sudo apt update
sudo apt install qgis python3-qgis gdal-bin openmpi-bin python3-venv
bash scripts/install_grass.sh        # installs the `grass` package + the addon
```

**Fedora**

```sh
sudo dnf install qgis python3-qgis gdal openmpi
bash scripts/install_grass.sh
```

Fedora installs Open MPI's `mpiexec` under `/usr/lib64/openmpi/bin`, which is
not on `PATH` by default. Either `module load mpi/openmpi-x86_64` before
launching QGIS, or point the *MPI launcher* row at it with **Browse…**.

**Arch**

```sh
sudo pacman -S qgis gdal openmpi python
bash scripts/install_grass.sh
```

Notes:

- `install_grass.sh` uses apt, dnf, pacman or zypper through `sudo`, or
  `pkexec` when the plugin runs it without a terminal. If neither works it
  prints the command to run yourself.
- The bundled Linux binaries are built on Debian 12 and need **glibc ≥ 2.36**
  and **Open MPI** (4.x or 5.x). They link `libmpi.so.40`, so an MPICH-only
  host cannot run them.
- TauDEM is bundled. Nothing to install.

### Windows

1. **QGIS**: the standalone installer or OSGeo4W from <https://qgis.org>. Both
   supply GDAL's command-line tools and a suitable Python.
2. **MS-MPI runtime**: install `msmpisetup.exe` from
   [Microsoft MPI](https://learn.microsoft.com/message-passing-interface/microsoft-mpi).
   The runtime is enough; the SDK is only needed for building.
3. **GRASS + r.slopeunits**: let the plugin's setup dialog run it, or from a
   command prompt in the plugin folder:

   ```bat
   scripts\install_grass_windows.bat             :: install GRASS 8.4 + the addon
   scripts\install_grass_windows.bat /check      :: report only
   scripts\install_grass_windows.bat /addon-only :: addon only
   ```

   The full GRASS download is ~940 MB and may need an Administrator prompt.
   If QGIS came from OSGeo4W, adding GRASS through `osgeo4w-setup.exe` instead
   keeps a single shared GDAL. A standalone QGIS that bundles GRASS (for
   example `grass85`) is detected automatically; only the addon is then needed.
4. **Long paths**: strongly recommended. See
   [Windows: enable long paths](#windows-enable-long-paths).

TauDEM and the GDAL/PROJ/MSVC runtime DLLs for the native binaries are bundled.
No TauDEM install or Visual C++ redistributable is needed.

### Installing the plugin itself

1. Download the zip for your platform (or the all-platform zip) from the
   releases page.
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Open **MLPREP Detection Suite** and follow the first-launch gates
   ([above](#what-happens-on-first-launch)).

To install the addon by hand inside an existing GRASS:
`g.extension extension=r.slopeunits`. The addon goes in `~/.grass8/addons`, or
`%APPDATA%\GRASS8\addons` on Windows.

---

## Building binaries, bundling and packaging

`bin/` is generated and not committed. A release needs three kinds of build
output in `bin/<platform>/`, then a packaging step.

### 0. Get the sources

```sh
git clone <repo-url> MLPREP_Plugin_v4
```

A plain clone contains every source the build needs. There are no submodules
or other repositories to fetch:

| Source | Location |
|---|---|
| Go zonal / slope-unit engine | `ml-prep-scripts/` |
| C++/MPI routing engine | `ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port/` |
| TauDEM sources (subset) | `ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port/third_party/taudem/` |

Keep the checkout directory named **`MLPREP_Plugin_v4`**. `build_release.sh`
uses the directory name as the plugin's folder name inside the zip.

### Build prerequisites

| Build step | macOS (Apple Silicon) | Linux | Windows |
|---|---|---|---|
| Go engine (`build_binaries.sh`) | `brew install go` (Go ≥ 1.25) | Go ≥ 1.25 from <https://go.dev/dl> | Not needed: cross-compiled from macOS/Linux |
| C++/MPI engine + TauDEM, host build | `xcode-select --install`; `brew install cmake gdal open-mpi` | `sudo apt install build-essential cmake libgdal-dev libopenmpi-dev patchelf` | Visual Studio Build Tools (MSVC x64), MS-MPI runtime **and SDK**, micromamba |
| C++/MPI engine, Linux build via Docker | Docker Desktop | Docker | — |
| Packaging (`build_release.sh`) | bash, rsync, zip (preinstalled) | `sudo apt install rsync zip` | Run from macOS/Linux or WSL |

### 1. Go zonal engine: all platforms, from one machine

```sh
./build_binaries.sh
```

Cross-compiles the Go engine in `ml-prep-scripts/` (part of this repository) for `darwin/arm64`, `darwin/amd64`,
`linux/amd64` and `windows/amd64` into `bin/<goos>_<goarch>/zonal[.exe]`. It
only replaces `zonal` binaries, never the native engines that share `bin/`.

### 2. C++/MPI routing engine + TauDEM tools: per platform

The engine and the six TauDEM tools link GDAL and MPI, so they cannot be
cross-compiled the way Go can.

```sh
./build_native.sh          # host platform (e.g. darwin_arm64)
./build_native.sh linux    # linux_amd64, built inside Docker (Debian 12)
./build_native.sh all      # host + linux
```

What the script does:

- Configures `ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port` with
  CMake and `-DMLPREP_BUILD_TAUDEM_TOOLS=ON`, builds, and copies the seven
  executables into `bin/<platform>/`.
- **Bundles the dependency closure** so the binaries run on machines with a
  different GDAL:
  - *macOS*: `scripts/bundle_macos_deps.py` copies non-system dylibs into
    `bin/darwin_arm64/lib/` and rewrites install names to `@rpath`. Homebrew's
    `proj.db`/`proj.ini` go into `share/proj/`. Unlike Linux and Windows,
    this currently bundles Open MPI's `libmpi`/`libopen-pal` too, so the host's
    Homebrew `open-mpi` should be the same major version as the build machine's.
  - *Linux*: copies the `ldd` closure into `lib/` and sets `$ORIGIN` rpaths
    with `patchelf`. It deliberately **excludes** libc, `libstdc++`/`libgcc_s`
    and MPI. `proj.db` comes from the build image.
  - The build fails if a binary links `libmpi_cxx`, because Open MPI 5 hosts
    lack it.
- Leaves `bin/windows_amd64/` alone if it already has an engine (see step 3).

The plugin points `PROJ_DATA` at the bundled `share/proj` only for these
binaries (`platform_utils.bundled_environment`).

### 3. Windows engine + TauDEM: built in CI

The Windows build needs MSVC and MS-MPI, so it runs in GitHub Actions. The
`windows-native` job in `.github/workflows/ci.yml`:

1. installs MS-MPI, MSVC and a conda-forge GDAL/PROJ via micromamba
2. builds the engine and the TauDEM tools with CMake + Ninja
3. bundles every needed DLL and `proj.db` beside the `.exe` files
   (`scripts/bundle_windows_deps.py`; `msmpi.dll` is excluded)
4. smoke-tests the tools with `PATH` and `PROJ_DATA` stripped
   (`scripts/taudem_smoke_test.py`)
5. uploads the artifact **`bin-windows_amd64`**

Fetch it into the tree before packaging:

```sh
gh run download <run-id> -n bin-windows_amd64 -D bin/windows_amd64
```

To build on a Windows machine instead, run the same steps from an *x64 Native
Tools PowerShell* with the MS-MPI runtime and SDK installed:

```powershell
micromamba create -n build -c conda-forge python=3.12 libgdal-core gdal numpy pefile cmake ninja
micromamba activate build
$src = "ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port"
cmake -S $src -B build -G Ninja -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl `
  -DMLPREP_BUILD_TAUDEM_TOOLS=ON -DCMAKE_PREFIX_PATH="$env:CONDA_PREFIX\Library"
cmake --build build --parallel

$out = "bin/windows_amd64"; New-Item -ItemType Directory -Force $out | Out-Null
foreach ($exe in "step4_deposition_zone_mpi","pitremove","dinfflowdir","areadinf","threshold","dinfdistdown","dinfdistup") {
  Copy-Item "build/$exe.exe" $out
}
python scripts/bundle_windows_deps.py --search-dir "$env:CONDA_PREFIX\Library\bin" `
  --proj-dir "$env:CONDA_PREFIX\Library\share\proj" --out $out (Get-ChildItem "$out\*.exe").FullName
python scripts/taudem_smoke_test.py
```

Fallback: if `bin/windows_amd64/` has no engine at all, `build_native.sh`
copies in an old prebuilt one from `cpp_mpi_port/build_manual/`. That build has
no TauDEM tools and no bundled DLLs, so the host must supply GDAL. It never
overwrites an existing engine.

### Platform coverage of the native binaries

| Target | Go `zonal` | Routing engine + TauDEM | How it is produced |
|---|---|---|---|
| `darwin_arm64` | ✓ | ✓ | `./build_native.sh host` on Apple Silicon |
| `darwin_amd64` | ✓ | ✗ | Needs `./build_native.sh host` on an Intel Mac |
| `linux_amd64` | ✓ | ✓ | `./build_native.sh linux` (Docker) |
| `windows_amd64` | ✓ | ✓ | CI artifact `bin-windows_amd64` |

### 4. Package the zip

```sh
./build_release.sh                              # all platforms, with roi_data/
./build_release.sh --platform windows_amd64     # only that platform's bin/
./build_release.sh --no-roi                     # omit roi_data/ (downloaded on first run)
./build_release.sh --platform linux_amd64 --no-roi
```

Output: `dist/MLPREP_Plugin_v4[-<platform>][-noroi].zip`.

The script:

1. fails if any platform's `zonal` is missing (it runs `build_binaries.sh` if
   `bin/` is absent entirely), and warns for each platform without a routing
   engine
2. stages the tree through an explicit **deny list**: virtualenvs, `dist/`,
   bytecode, `.git`, `tests/`, build trees, `docker/`, `*_old.py` /
   `*_backup.py`, pipeline outputs and key files
3. **verifies** the stage: required files present, nothing excluded leaked
   (`pyvenv.cfg`, `__pycache__`, `.git`, `CMakeCache.txt`, `build-*`), no
   private key by content, and for `--platform` exactly one `bin/` subfolder
4. zips the stage

A per-platform zip is much smaller, because each other platform's `bin/`
contributes 100–170 MB.

### 5. Publishing the boundary data (`--no-roi` releases only)

```sh
python3 scripts/package_roi_data.py --version <data version>
```

This writes `roi_data-<version>.zip` and `manifest.json`. Upload the zip first
and the manifest last, to the directory the plugin's manifest URL points at
(`DEFAULT_MANIFEST_URL` in `common/roi_data.py`, overridable with
`MLPREP_ROI_URL`). The default URL is only reachable on the project network.
Point it at a public host before distributing widely.

### Release checklist

```sh
./build_binaries.sh
./build_native.sh all                                   # on an Apple Silicon Mac with Docker
gh run download <run-id> -n bin-windows_amd64 -D bin/windows_amd64
python -m pytest tests -m "not slow" -q
./build_release.sh --platform <tag> [--no-roi]          # once per target, or once without --platform
```

---

## Development

```sh
python -m pip install pytest numpy
python -m pytest tests -m "not slow" -q   # runs without QGIS; CI runs it on all three OSes
cd ml-prep-scripts && go test ./...       # Go engine + executable resolution
```

- External programs are resolved in exactly two places: `platform_utils.py`
  (Python) and `ml-prep-scripts/toolpath/` (Go). To support a new platform or
  install location, edit those search tables rather than adding a branch at a
  call site.
- PINN models are listed in `model/pinn_models.json`. Add an entry there to
  make a model appear in the picker.
- **Headless runs**: `scripts/run_eil_pipeline.py` runs the EIL pipeline
  without the GUI. `docker/eil-linux/eil-docker.sh` runs it in an Ubuntu 24.04
  container against the bind-mounted working tree and the `linux_amd64`
  binaries.

---

## Troubleshooting and known gaps

### Windows: enable long paths

Windows limits paths to 260 characters unless long-path support is on. The
pipeline nests intermediate directories under the output folder and `%TEMP%`,
so a deep output location can exceed the limit and produce confusing GDAL
errors.

**Group Policy**: `Computer Configuration → Administrative Templates → System →
Filesystem → Enable Win32 long paths`.

**Or the registry**, as Administrator:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

Reboot afterwards. Alternatively, keep the output folder short
(`C:\mlprep\out` rather than a deep path under `Documents`).

### Virtualenv creation fails on Windows

OSGeo4W's Python sometimes lacks a working `ensurepip`. The plugin then
recreates the env with `--without-pip` and bootstraps pip from
`get-pip.py`, which needs network access. The error dialog shows the exact
command to reproduce the failure in the OSGeo4W Shell.

### Known gaps

- **Intel macOS** has no routing engine or TauDEM build, so depositional
  routing is skipped there.
- **Windows** compiles, passes unit tests and has a CI-smoke-tested native
  bundle, but has not been verified end-to-end inside QGIS.
- **`roi_data/` is ~1.4 GB.** Use `--no-roi` builds plus the first-run download
  for distribution.
- The Dependencies tab's TauDEM hints for macOS and Windows still describe a
  manual TauDEM install, even though both now prefer the bundled tools.
