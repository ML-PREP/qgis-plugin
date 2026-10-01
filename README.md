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
| GRASS GIS 8.x | EIL hazard mapping | download to `/Applications` | standalone installer or OSGeo4W | `apt install grass` |
| GDAL | EIL hazard mapping | supplied by QGIS, or `brew install gdal` | supplied by QGIS/OSGeo4W | `apt install gdal-bin` |
| MPI | Depositional routing | `brew install open-mpi` | [MS-MPI](https://learn.microsoft.com/message-passing-interface/microsoft-mpi) | `apt install openmpi-bin` |
| TauDEM | Depositional routing | build from source | TauDEM 5.3 installer | build from source |

**Landslide Detection needs none of these** — it is never blocked by a missing
GRASS or TauDEM. Only the EIL and depositional-routing pipelines are.

The `r.slopeunits` GRASS addon is also required for slope-unit generation:

```
g.extension extension=r.slopeunits
```

**On Windows**, `scripts\install_grass_windows.bat` does both steps:

```bat
scripts\install_grass_windows.bat /check       :: report only, changes nothing
scripts\install_grass_windows.bat              :: install GRASS + the addon
scripts\install_grass_windows.bat /addon-only  :: addon only, GRASS already present
```

Run `/check` first. The full install downloads ~940 MB and may need an
Administrator prompt. If QGIS came from OSGeo4W, adding GRASS through
`osgeo4w-setup.exe` instead keeps one shared GDAL.

### Python environments

On first run the plugin builds two virtualenvs under `~/.mlprep_envs`
(`ls_detect_env`, `venv`) from `requirements_ls.txt` and `requirements_eil.txt`.
This takes several minutes and needs network access. Nothing is installed into
QGIS's own Python.

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
| `darwin_amd64` | CI only — `macos-13` runner, see `.github/workflows/` |
| `windows_amd64` | prebuilt, vendored from `build_manual/`; rebuild with `build_manual_msvc.ps1` (needs MSVC + MS-MPI) |

macOS and Linux builds bundle GDAL and its dependencies alongside the binary, so
they do not care which GDAL the user has. MPI and the C++ runtime are
deliberately **not** bundled — the binary is launched by the host's `mpiexec`,
and a bundled MPI that disagrees with the host launcher fails at startup.

The Windows engine ships **without** bundled DLLs and relies on the host's GDAL
and MS-MPI.

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
