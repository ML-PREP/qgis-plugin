# MLPREP Detection V4 — QGIS Plugin

Landslide detection and Earthquake-Induced Landslide (EIL) hazard mapping in one
QGIS toolbar. The EIL model is a Physics-Informed Neural Network that embeds
Newmark displacement analysis directly in the network architecture.

Supported: **macOS** (Apple Silicon; Intel via CI build), **Windows 10/11 x64**,
**Linux x64**. QGIS 3.22 or newer.

---

## Installing

1. Download `MLPREP_Plugin_v4.zip` from the releases page.
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Open the plugin and go to the **Dependencies** tab *before* running anything.

### The Dependencies tab

The plugin shells out to programs that are installed separately. The tab shows
✓ / ✗ for each, says how to install what is missing, and lets you point at a
copy that lives somewhere unusual (the location is remembered).

| Program | Needed for | macOS | Windows | Linux |
|---|---|---|---|---|
| GRASS GIS 8.x | EIL hazard mapping | `scripts/install_grass.sh` (Homebrew) | `scripts\install_grass_windows.bat` | `scripts/install_grass.sh` |
| GDAL | EIL hazard mapping | supplied by QGIS, or `brew install gdal` | supplied by QGIS/OSGeo4W | `apt install gdal-bin` |
| MPI | Depositional routing | `brew install open-mpi` | [MS-MPI](https://learn.microsoft.com/message-passing-interface/microsoft-mpi) | `apt install openmpi-bin` |
| TauDEM | Depositional routing | build from source | TauDEM 5.3 installer | **bundled** (`pitremove`, `dinfflowdir` in `bin/linux_amd64/`) |

**GRASS and its `r.slopeunits` addon are required before the plugin will
finish setting up.** If either is missing when the plugin opens, a blocking
*GRASS GIS Setup Required* dialog appears with a ✓ / ✗ row for each:

- **Install automatically** runs the platform installer script (below) and
  shows its output in the dialog.
- **Continue** stays disabled until *both* GRASS and the addon are detected.
  The dialog re-checks every few seconds, so installing by hand also unlocks it.
- **Locate GRASS…** accepts a copy installed somewhere unusual.
- **Close Plugin** closes the plugin.

GDAL, MPI and TauDEM only block the pipelines that use them.

Both installer scripts install GRASS if it is missing, then the addon from the
copy bundled in `vendor/grass_addons/` (no network needed), and exit non-zero
unless both end up installed. They can also be run by hand.

**macOS / Linux** — `scripts/install_grass.sh`:

```sh
bash scripts/install_grass.sh --check       # report only, changes nothing
bash scripts/install_grass.sh               # install GRASS + the addon
bash scripts/install_grass.sh --addon-only  # addon only, GRASS already present
```

- *macOS:* Homebrew has no GRASS package, so the script runs
  `brew install micromamba` and then installs GRASS from conda-forge into
  `~/.mlprep_envs/grass`. No administrator password is needed, but
  [Homebrew](https://brew.sh) must already be installed.
- *Linux:* installs the distribution's `grass` package with apt, dnf, pacman or
  zypper. It needs `sudo`, or `pkexec` when run from the plugin without a
  terminal. If neither works, it prints the command to run yourself.

**Windows** — `scripts\install_grass_windows.bat`:

```bat
scripts\install_grass_windows.bat /check       :: report only, changes nothing
scripts\install_grass_windows.bat              :: install GRASS + the addon
scripts\install_grass_windows.bat /addon-only  :: addon only, GRASS already present
```

The full Windows install downloads ~940 MB and may need an Administrator
prompt. If QGIS came from OSGeo4W, adding GRASS through `osgeo4w-setup.exe`
instead keeps one shared GDAL.

To install the addon by hand instead: `g.extension extension=r.slopeunits`

### Python environments

On first run the plugin builds two virtualenvs under `~/.mlprep_envs`
(`ls_detect_env`, `venv`) from `requirements_ls.txt` and `requirements_eil.txt`.
The `r.slopeunits` addon lives in GRASS's user addon directory
(`~/.grass8/addons`, or `%APPDATA%\GRASS8\addons` on Windows), and on macOS
the script-installed GRASS lives in `~/.mlprep_envs/grass`. This takes
several minutes and needs network access for the pip installs. Nothing is
installed into QGIS's own Python.

---

## Windows: enable long paths

Windows limits paths to 260 characters unless long-path support is on. The
pipeline nests intermediate directories under your output folder and `%TEMP%`,
so a deeply-nested output location can exceed the limit and produce confusing
GDAL errors.

Intermediate directory names are kept deliberately short for this reason, but if
you keep project data far down a directory tree, enable long paths:

**Group Policy** — `Computer Configuration → Administrative Templates → System →
Filesystem → Enable Win32 long paths`.

**Or the registry**, as Administrator:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

Reboot afterwards. If you would rather not change this, keep your output folder
short — `C:\mlprep\out` rather than a deep path under `Documents`.

---

## Building a release

Two compiled engines ship with the plugin: a Go slope-unit/zonal engine and a
C++/MPI depositional-routing engine. Neither is committed — `bin/` is generated.

```bash
./build_binaries.sh          # Go engine, cross-compiled for all four targets
./build_native.sh all        # C++/MPI engine: host platform + linux/amd64 (Docker)
./build_release.sh           # staged, verified zip in dist/
./build_release.sh --no-roi  # omit roi_data/ (~1.4 GB); ROI dropdown will be empty
```

`build_release.sh` stages through an explicit deny list and then **fails** if a
virtualenv, `__pycache__`, `.git` or build tree leaks into the package.

### Platform coverage of the routing engine

| Target | How it is produced |
|---|---|
| `darwin_arm64` | `./build_native.sh host` on Apple Silicon |
| `linux_amd64` | `./build_native.sh linux` (Docker, Debian 12) |
| `darwin_amd64` | not built yet (needs an Intel Mac) |
| `windows_amd64` | CI — the `windows-native` job in `.github/workflows/ci.yml` builds the engine **and** `pitremove`/`dinfflowdir`, bundles their DLLs + `proj.db`, smoke-tests them under MS-MPI, and uploads `bin-windows_amd64`. Unzip it into `bin/windows_amd64/` before `build_release.sh`. (Fallback: the old prebuilt engine from `build_manual/`, no TauDEM, no DLLs.) |

macOS and Linux builds bundle GDAL and its dependencies alongside the binary, so
they do not care which GDAL the user has. MPI and the C++ runtime are
deliberately **not** bundled — the binary is launched by the host's `mpiexec`,
and a bundled MPI that disagrees with the host launcher fails at startup.

The CI-built Windows bundle puts its DLLs beside the `.exe` files (Windows has
no rpath; a process searches its own directory first). `msmpi.dll` is
excluded for the same reason as MPI above.

---

## Development

```bash
cd ml-prep-scripts && go test ./...   # engine + executable-resolution tests
python -m pytest tests/               # platform/dependency-resolution tests
```

External programs are resolved in exactly two places — `platform_utils.py`
(Python) and `ml-prep-scripts/toolpath/` (Go). Add a platform by editing those
search tables, not by adding a branch at a call site.

### Known gaps

- **Windows is written but unverified end-to-end.** It compiles and its unit
  tests pass, but the plugin has not been loaded in QGIS on Windows.
- **`roi_data/` is ~1.4 GB** and needed at runtime for the ROI dropdown, which
  makes the full package impractical to distribute as-is.
