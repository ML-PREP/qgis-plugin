# Cross-Platform Audit & Remediation Plan — MLPREP Detection V4

**Audited:** 2026-09-08
**Scope:** `MLPREP_Plugin_v4/` (QGIS plugin, Python + Go + C++/MPI)
**Current state:** macOS/arm64 only. Will not start on Windows or Linux.
**Target:** macOS (arm64 + x86_64), Windows 10/11 x64, Linux x64.

---

## Executive Summary

The **Python code is largely portable already** — it consistently uses `os.path.join`,
`os.path.expanduser`, and `platform.system()` branches. There is no `/`-string path
concatenation anywhere in the plugin source.

The portability failures come from three places:

1. **A machine-specific macOS virtualenv is committed inside the plugin folder.**
2. **External native tooling** (TauDEM, MPI, GRASS, GDAL CLI, Go toolchain, a hand-built
   C++/MPI binary) is discovered via hardcoded Homebrew/`/usr/local` paths, or is
   assumed to be on `PATH` when it will not be.
3. **Executable discovery is duplicated across two languages and five call sites**, so
   each new platform requires the same fix in five places.

Item 3 is the structural problem. Fixing it (Phase 2 below) is the single highest-value
change and makes Phases 3–5 mechanical.

### Severity legend

| Level | Meaning |
|-------|---------|
| **P0** | Plugin does not load or immediately crashes on a target platform |
| **P1** | Plugin loads but a major feature fails on real data |
| **P2** | Works, but degraded UX / fragile / ships junk |

---

## Findings

### P0 — Startup blockers

#### F1. Machine-specific macOS venv inside the plugin directory
**File:** `venv/pyvenv.cfg`

> **Correction (2026-09-08):** the original audit said this venv was *committed*.
> It is not — the parent `mlprep/.gitignore` already ignores `venv/`. The risk is
> real but is a **packaging** risk, not a version-control one: a QGIS plugin zip
> is built from the directory, so it would ship unless excluded. Two further
> facts emerged: the venv is **broken** (`venv/bin/python` does not exist), and
> it is **1.7 GB**.

```
home = /Library/Frameworks/Python.framework/Versions/3.13/bin
command = ... -m venv /Users/ml-prepproject/Library/Application Support/QGIS/.../venv
```

A Python 3.13 macOS framework venv, created under another user's home directory, is
bundled in the plugin. It is dead weight on macOS and non-functional everywhere else.
`SetupWorker` already provisions environments at runtime in `~/.mlprep_envs`, so this
directory serves no purpose.

**Fix:** delete `venv/`; add `venv/`, `__pycache__/`, `.DS_Store` to `.gitignore`.

---

#### F2. Three conflicting virtualenv location conventions
**Files:** `merged_wrapper.py:775-780`, `landslide_wrapper.py:181-187`,
`eil_wrapper.py:120`

> **Correction (2026-09-08):** there were **three**, not two. `eil_wrapper.py:120`
> hardcoded `<plugin_dir>/venv/bin/python` — a POSIX-only path into the broken
> 1.7 GB venv described in F1. Also worth recording: `eil_wrapper.py` and
> `landslide_wrapper.py` are **not imported by anything** (`mainPlugin.py` loads
> only `merged_wrapper`); they are pre-merge standalone modules.

```python
# merged_wrapper.py  -> ~/.mlprep_envs/<venv_name>/{bin|Scripts}/python
envs_dir = os.path.join(os.path.expanduser("~"), ".mlprep_envs")

# landslide_wrapper.py -> <plugin_dir>/ls_detect_env/{bin|Scripts}/python
plugin_dir = os.path.dirname(os.path.abspath(__file__))
```

Two different resolution strategies for the same interpreter. One of them is always
wrong. `SetupWorker` creates the env at `~/.mlprep_envs/ls_detect_env`, so
`landslide_wrapper` is the incorrect one.

**Fix:** single source of truth in `platform_utils.venv_python(name)`; delete both
local copies.

---

#### F3. `get_base_python()` has no Linux branch
**File:** `merged_wrapper.py:492-503`

```python
if platform.system() == 'Windows':   ...
elif platform.system() == 'Darwin':  ...
# no Linux branch
if os.path.basename(sys.executable).lower().startswith('python'):
    return sys.executable
return "python3"          # <- relies entirely on PATH
```

Under QGIS, `sys.executable` is `qgis-bin` / `qgis-bin.exe`, so the `startswith('python')`
guard never matches and the function falls through to the bare string `"python3"`.
On Linux that may resolve to a system Python with no venv module; on Windows it fails
outright.

**Fix:** add a POSIX branch checking `sys.prefix/bin/python3`, then
`shutil.which("python3") or shutil.which("python")`, and raise a clear error if none found.

---

#### F4. TauDEM discovery is POSIX-only and misses the `.exe` suffix
**File:** `merged_wrapper.py:58-67`

```python
candidates = [
    os.path.expanduser("~/.mlprep_envs/venv/bin"),
    "/usr/local/taudem", "/usr/local/bin", "/opt/homebrew/bin",
    os.path.expanduser("~/miniconda3/bin"),
]
if os.path.exists(os.path.join(c, "pitremove")):   # no .exe
```

Windows TauDEM installs to `C:\Program Files\TauDEM\TauDEM5Exe` with `.exe` binaries.
Returns `None` on Windows/most Linux installs, and the worker aborts with
"TauDEM not found."

---

#### F5. MPI discovery is Homebrew-only
**File:** `merged_wrapper.py:70-74`

```python
for candidate in ["/opt/homebrew/bin/mpiexec", "/usr/local/bin/mpiexec",
                  "/opt/homebrew/bin/mpirun"]:
```

Falls back to the bare string `"mpiexec"`. Windows uses MS-MPI at
`C:\Program Files\Microsoft MPI\Bin\mpiexec.exe`, which is not on `PATH` by default.

---

#### F6. Go engine requires the Go compiler at runtime
**File:** `merged_wrapper.py:296-306`, invoked with `go run .`

```python
go_executable = "go"
if sys.platform == "win32":            go_executable = "go.exe"
elif os.path.exists("/opt/homebrew/bin/go"): go_executable = "/opt/homebrew/bin/go"
elif os.path.exists("/usr/local/bin/go"):    go_executable = "/usr/local/bin/go"
```

No end user has a Go toolchain installed. `go run` also recompiles on every invocation
and needs a writable module cache.

**Fix:** cross-compile once at release time and ship the binaries.

```bash
cd ml-prep-scripts
for t in darwin/arm64 darwin/amd64 linux/amd64 windows/amd64; do
  GOOS=${t%/*} GOARCH=${t#*/} go build \
    -o "../bin/${t%/*}_${t#*/}/zonal$([ "${t%/*}" = windows ] && echo .exe)" .
done
```

Keep `go run .` as a dev-only fallback when the bundled binary is absent.

---

#### F7. Go engine drives GRASS through `bash`
**File:** `ml-prep-scripts/main.go:407-424`

```go
script := fmt.Sprintf(`#!/bin/bash
r.in.gdal -o input="%s" output=dem band=1 --overwrite
...`)
tmp, _ := os.CreateTemp("", "slu_*.sh")
os.Chmod(tmp.Name(), 0755)                                   // no-op on Windows
cmd := exec.Command(grassExe, "-c", demPath, grassMapset, "--exec", "bash", tmp.Name())
```

`bash` does not exist on a stock Windows install. `os.Chmod` with a Unix mode is a no-op
there, and the shebang is meaningless.

**Fix:** GRASS bundles Python on all three platforms. Emit a `.py` script and invoke
`grass ... --exec python script.py`, using `grass.script.run_command()` for each step.

---

#### F8. `getGrassExe()` only knows macOS app-bundle paths
**File:** `ml-prep-scripts/main.go:344-359`

```go
if runtime.GOOS == "darwin" {
    paths := []string{"/Applications/GRASS-8.4.app/Contents/Resources/bin/grass", ...}
}
return "grass"     // Windows falls straight through
```

Windows needs `C:\Program Files\GRASS GIS 8.4\grass84.bat` (a `.bat`, which requires
shell resolution or an explicit `cmd /c`). Linux `grass` is usually on `PATH`, but the
versioned name (`grass84`) varies by distro.

---

#### F9. `os.Getenv("HOME")` is empty on Windows
**File:** `ml-prep-scripts/main.go:421`

```go
addonPath := filepath.Join(os.Getenv("HOME"), ".grass8", "addons")
```

Yields `\.grass8\addons` on Windows — a path on the current drive root.
The `r.slopeunits` addon is then never found.

**Fix:** `os.UserHomeDir()`; GRASS addons live under `%APPDATA%\GRASS8\addons` on Windows.

---

#### F10. Bare GDAL CLI invocations
**Files:** `ml-prep-scripts/main.go:503`, `internals/gpkgtogif/gpkgtogif.go:41,74`

```go
exec.Command("gdalwarp", "-t_srs", crs, ...)
exec.Command("ogrinfo", "-al", "-so", gpkgPath)
```

`gdalwarp` / `ogrinfo` / `ogr2ogr` are not on `PATH` on Windows outside the OSGeo4W
shell, and not guaranteed on Linux either.

**Fix:** resolve GDAL's bin directory in Python (from `QgsApplication.prefixPath()` /
`sys.prefix`) and inject it into the Go subprocess `PATH`, or pass explicit binary
paths as flags.

---

#### F11. The macOS C++/MPI binary is arm64-only and Homebrew-linked
**File:** `ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port/build_manual/`

```
step4_deposition_zone_mpi:     Mach-O 64-bit executable arm64
step4_deposition_zone_mpi.exe: PE32+ executable (console) x86-64
```

```
otool -L step4_deposition_zone_mpi
  /opt/homebrew/opt/gdal/lib/libgdal.39.dylib
  /opt/homebrew/opt/open-mpi/lib/libmpi.40.dylib
```

The Windows `.exe` exists (good), but the macOS build:
- will not run on Intel Macs;
- will not run on any Mac lacking Homebrew GDAL **major version 39** specifically;
- has no Linux counterpart at all.

`merged_wrapper.py:227-233` selects between exactly two paths and errors out otherwise.

**Fix options** (pick one):
- **A** — Build `universal2` and relink dependencies with `@rpath`, bundling the dylibs
  next to the binary (`install_name_tool` + `codesign`). Add a `linux_x64` build.
- **B** — Link against QGIS's own bundled GDAL rather than Homebrew's.
- **C** — Add a pure-Python/numpy fallback routing path for platforms without a binary
  (slower, but guarantees the feature always works).

---

### P1 — Runs, but fails on real data

#### F12. `open()` calls without an explicit encoding
**Files:** `merged_wrapper.py:468`, `eil_wrapper.py:78`, `landslide_worker.py:117`

```python
with open(self.output_csv, 'r') as f:
```

Python defaults to the locale encoding, i.e. **cp1252 on Windows**.

> **Correction (2026-09-08):** the audit said this "raises `UnicodeDecodeError`".
> Testing showed the usual outcome is worse — **silent corruption**. cp1252 maps
> almost every byte, so `Doña` decodes to `DoÃ±a` with no error at all and the
> wrong name lands in a results table. It only *raises* when a UTF-8
> continuation byte hits one of cp1252's five undefined slots
> (0x81/0x8D/0x8F/0x90/0x9D) — e.g. `Māhina`. Both cases are covered by tests.

**Fix:** `encoding="utf-8"` on every text-mode `open()`.

---

#### F13. `subprocess(..., text=True)` without an encoding
**Files:** `merged_wrapper.py:256,329,401,459,1214`, `landslide_wrapper.py:359`,
`eil_wrapper.py:61`

Same locale-decoding problem applied to subprocess output. GDAL and GRASS routinely
emit non-ASCII in progress and warning messages.

**Fix:** `encoding="utf-8", errors="replace"` on every call.

---

#### F14. macOS `PATH` prepend is mis-gated and leaks onto Linux
**File:** `merged_wrapper.py:326-327`

```python
if sys.platform != "win32":
    env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
```

Uses "not Windows" where it means "macOS". On Linux this prepends two directories that
either do not exist or hold unrelated binaries, ahead of the real system paths.

**Fix:** gate on `sys.platform == "darwin"`.

---

#### F15. `matplotlib.use('Qt5Agg')` is hardcoded
**File:** `merged_wrapper.py:13-15`

Hard-fails at import time on Qt6-based QGIS builds, taking the whole plugin down with it
(it runs at module scope). Not strictly an OS issue, but it varies by how QGIS was
packaged per platform.

**Fix:** try `qtagg` first, fall back to `Qt5Agg`, wrapped in try/except.

---

### P2 — Degraded UX and packaging hygiene

#### F16. Console windows flash on Windows
Every `subprocess.Popen` / `subprocess.run` spawns a visible console window.
With per-tile GDAL calls this means dozens of flashing windows.

**Fix:** `creationflags=subprocess.CREATE_NO_WINDOW` on win32.

---

#### F17. Windows 260-character path limit
QGIS temp directories combined with the nested `tiles_temp/slu_<N>/` layout
(`main.go:440,459`) approach `MAX_PATH`. GDAL fails with opaque errors when exceeded.

**Fix:** shorten intermediate directory names, and document enabling Windows long paths.

---

#### F18. Repository ships junk
- `.DS_Store` files at multiple levels (macOS-only metadata)
- `ml-prep-scripts/.git/` — a nested git repository inside the plugin
- `cpp_mpi_port/build_mac/CMakeFiles/` — CMake compiler-probe artifacts
- `roi_data/` shapefiles (gitignored, but present in the working tree and any zip)
- `eil_mainwindow_backup.py`, `main_backup.go.txt`
- `EIL_Icon.png` is **6.8 MB** for a toolbar icon

**Fix:** a `.qgis-plugin-ignore`-style build script that produces a clean release zip.

---

#### F19. `metadata.txt` is unfinished
**File:** `metadata.txt`

```
author=Your Name
email=your.email@example.com
tracker=
repository=
qgisMinimumVersion=3.0
```

Placeholder author/email, empty tracker and repository (both required by the QGIS plugin
repository), and a `qgisMinimumVersion` of 3.0 that is almost certainly untested.

---

#### F20. Worker requirements files did not list every module the workers import
**Files:** `requirements_eil.txt`, `requirements_ls.txt`, `ml-prep-scripts/main.go`

Reported from Windows: the Go engine finished Phase 1 tiling, then died on

```
File "...\Temp\558756868.py", line 5, in <module>
    import fiona
ModuleNotFoundError: No module named 'fiona'
Slope Unit Generation Failed: Polygonize failed: exit status 1
```

The Go engine writes short Python scripts to a temp file and runs them with the
**EIL venv's** interpreter (`merged_wrapper.py` passes `self.eil_venv_python` to
`GoParallelZonalWorker`). That venv is provisioned *only* from
`requirements_eil.txt`; QGIS's own site-packages are not on its path. So an
undeclared import is a guaranteed `ModuleNotFoundError` — surfacing only when
that phase runs, which can be many minutes into a job.

`fiona` was never declared in either requirements file. It used to arrive
transitively via geopandas; **geopandas 1.x reads and writes through `pyogrio`
instead**, and `rasterstats` 0.21 likewise demoted fiona to an optional extra.
Nothing installs it any more. Confirmed absent from *both* environments on the
developer Mac too — this was not a Windows-only drift.

Auditing every worker import against its requirements file found three more:

| Module | Imported by | Was arriving via | Status |
|--------|-------------|------------------|--------|
| `fiona` | `main.go:polygonizeScript` | nothing | **broken** |
| `rasterstats` | `main.go:zonalStatScript` | nothing | **broken** (Phase 2, next to fail) |
| `rasterio` | 3 Go scripts, `pinn_inference.py` | `contextily` | accidental |
| `scipy` | `main.go:mergeSlopeUnitsScript` | `scikit-learn` | accidental |
| `shapely` | `main.go:zonalStatScript` | `geopandas` | accidental |

**Fix:**
1. `polygonizeScript` no longer imports fiona — it collects the sieved shapes and
   writes the GPKG through `geopandas`/`pyogrio`. Verified byte-for-byte
   equivalent output: same layer name (`Final_SlopeUnits`), same `su_id` field,
   same CRS, same geometry type.
2. Both requirements files now declare every import explicitly, including the
   ones that were merely transitive. Transitive availability is not a contract.
3. `tests/test_worker_requirements.py` parses the embedded Python out of
   `main.go` plus the standalone workers, and fails if any third-party import is
   absent from the matching requirements file. This is the guard that makes the
   class of bug non-recurring.

Existing users need no manual step: `READY_STAMP` records a hash of the
requirements file, so changing it invalidates both environments and the
Dependencies panel re-offers setup.

---

## The Structural Fix — `platform_utils.py`

Findings F4–F10 are one bug repeated: executable discovery scattered across two
languages and five call sites. Centralize it in a single new module.

```python
# platform_utils.py
import os, sys, shutil, subprocess
from qgis.PyQt.QtCore import QSettings

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
EXE    = ".exe" if IS_WIN else ""

_SEARCH_DIRS = {
    "win32": [
        r"C:\Program Files\TauDEM\TauDEM5Exe",
        r"C:\Program Files\Microsoft MPI\Bin",
        r"C:\Program Files\GRASS GIS 8.4",
    ],
    "darwin": [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/local/taudem",
        "/Applications/GRASS-8.4.app/Contents/Resources/bin",
    ],
    "linux": ["/usr/bin", "/usr/local/bin", "/usr/lib/taudem"],
}


def find_exe(name, settings_key=None):
    """Resolve an external tool. QSettings override -> PATH -> platform dirs.

    Returns an absolute path, or None if the tool is not installed.
    """
    if settings_key:
        override = QSettings().value(f"mlprep/exe/{settings_key}")
        if override and os.path.exists(override):
            return override

    hit = shutil.which(name)
    if hit:
        return hit

    key = sys.platform if sys.platform in _SEARCH_DIRS else "linux"
    for directory in _SEARCH_DIRS[key]:
        candidate = os.path.join(directory, name + EXE)
        if os.path.exists(candidate):
            return candidate
    return None


def venv_python(venv_name):
    """Single source of truth for the managed virtualenv interpreters."""
    envs_dir = os.path.join(os.path.expanduser("~"), ".mlprep_envs")
    if IS_WIN:
        return os.path.join(envs_dir, venv_name, "Scripts", "python.exe")
    return os.path.join(envs_dir, venv_name, "bin", "python")


def bundled_binary(plugin_dir, name):
    """Path to a cross-compiled binary shipped under bin/<goos>_<goarch>/."""
    import platform as _p
    goos  = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    goarch = {"x86_64": "amd64", "AMD64": "amd64",
              "arm64": "arm64", "aarch64": "arm64"}.get(_p.machine(), "amd64")
    return os.path.join(plugin_dir, "bin", f"{goos}_{goarch}", name + EXE)


def subprocess_kwargs(**extra):
    """Portable defaults for every subprocess call in the plugin."""
    kwargs = {"text": True, "encoding": "utf-8", "errors": "replace"}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    kwargs.update(extra)
    return kwargs
```

### Dependency preflight panel

Add a **Dependencies** tab that runs `find_exe` for `mpiexec`, `pitremove`,
`dinfflowdir`, `grass`, `gdalwarp`, `ogr2ogr`, plus `bundled_binary()` for the Go and
C++ engines. Show ✓/✗ per tool with the resolved path, and let the user browse to
anything missing — persisted under `QSettings("mlprep/exe/<tool>")`.

This turns today's failure mode (a cryptic `FileNotFoundError` fifteen minutes into a
run) into an actionable dialog before any work starts. **It is the single highest-value
change in this document.**

---

## Progress

**2026-09-08 — F4 through F10 complete.** External executable discovery is now
centralized. New/changed files:

| File | Role |
|------|------|
| `platform_utils.py` | **new** — Python-side resolver: `find_exe`, `venv_python`, `bundled_binary`, `subprocess_kwargs`, `tool_environment`, `check_dependencies` |
| `ml-prep-scripts/toolpath/toolpath.go` | **new** — Go-side resolver: `Grass`, `Gdal`, `GrassAddons`, `UserHome`, `GrassPython` |
| `ml-prep-scripts/toolpath/command_windows.go` | **new** — runs GRASS's `.bat` launcher through `cmd.exe` |
| `ml-prep-scripts/toolpath/command_other.go` | **new** — POSIX passthrough |
| `ml-prep-scripts/main.go` | bash heredoc → GRASS Python script; GRASS/GDAL via `toolpath` |
| `ml-prep-scripts/internals/gpkgtogif/gpkgtogif.go` | GDAL tools via `toolpath` |
| `eil/workers/` (was `merged_wrapper.py`) | TauDEM/MPI via `find_exe`; `go run .` → bundled binary; `tool_environment()` |
| `build_binaries.sh` | **new** — cross-compiles the engine for all four targets |
| `bin/<goos>_<goarch>/zonal[.exe]` | **new** — shipped engine binaries |
| `ml-prep-scripts/toolpath/toolpath_test.go` | **new** — 9 tests |
| `tests/test_platform_utils.py` | **new** — 20 tests |

Verification: `go vet ./...` clean; `go build ./...` succeeds for `darwin/arm64`,
`darwin/amd64`, `linux/amd64`, `windows/amd64`; 9 Go tests and 20 Python tests pass.
The Linux binary is statically linked (`CGO_ENABLED=0`), so it carries no glibc
version dependency.

**Behavioural change worth noting:** `find_exe` searches caller-supplied
`extra_dirs` *before* `PATH`. This was found by a failing test — the first
implementation checked `PATH` first, which meant QGIS's own GDAL lost to any
unrelated `gdalwarp` earlier on `PATH`.

**2026-09-08 (later) — Dependencies preflight panel built.**

| File | Role |
|------|------|
| `dependency_panel.py` | **new** — `DependencyPanel`: ✓/✗ table, per-tool Browse… override, Re-check, feature-scoped readiness |
| `platform_utils.py` | tool registry became `ToolSpec`/`ToolStatus` with a `feature` and a per-platform install `hint`; overrides now store a *directory* |
| `app/main_window.py`, `eil/tab.py`, `eil/pipeline.py` (was `merged_wrapper.py`) | "Dependencies" tab; EIL Run disabled when unready; deposition skipped with a logged reason |

Tools are grouped by pipeline (`FEATURE_EIL`, `FEATURE_DEPOSITION`) so a missing
GRASS blocks EIL Hazard Mapping while leaving Landslide Detection — which uses
none of these programs — fully usable. The tab title shows `⚠ Dependencies (n)`
when something is absent.

**Second bug found by a test.** `pitremove` and `dinfflowdir` share the
`taudem` settings key. Storing an override as an *executable path* made
`dinfflowdir` resolve to the `pitremove` binary, and the panel then reported
Depositional Routing as ready. Overrides are now stored as the containing
**directory** and each tool is looked up by its own name inside it.

Verification: 35 Python tests and 9 Go tests pass; the panel was rendered
headless (offscreen Qt) and the full override round-trip exercised — including
persistence across panel instances and correct fallback when an override goes
stale.

**2026-09-08 (later still) — Phase 1 complete.**

`platform_utils.base_python()` replaces `SetupWorker.get_base_python()`: it prefers
QGIS's own interpreter, adds the missing POSIX branch, falls back to `shutil.which`,
and **raises with guidance** instead of returning a bare `"python3"`. All three venv
conventions now resolve through `platform_utils.venv_python()` — verified by grep
that no duplicate resolver remains anywhere in the plugin.

The 1.7 GB `venv/` **stays in place by user decision**. Deletion is therefore
replaced by a packaging guard: `build_release.sh` stages the plugin through an
explicit deny list and then *fails the build* if excluded content is found in the
stage. Verified: the produced zip contains 0 offending entries and all four engine
binaries.

**Known packaging issue (Phase 6):** the release is **97 MB without `roi_data/`
and ~1.5 GB with it**. `roi_data/` is used at runtime (`common/roi.py`)
so it cannot simply be dropped; `--no-roi` exists but leaves the ROI dropdown
empty. This needs a real answer — a separate download, or a trimmed boundary set.

**2026-09-08 — Phase 3 complete.** Every text-mode `open()` carries
`encoding="utf-8"`; every `subprocess.run`/`Popen` goes through
`subprocess_kwargs()` (UTF-8 + `errors="replace"` + `CREATE_NO_WINDOW`); the
matplotlib backend tries `qtagg` before `Qt5Agg` so a Qt6 QGIS no longer kills
the plugin at import.

**2026-09-08 — Phase 5 partially complete (strategy A).**

| Platform | Zonal | Routing engine | Bundled libs | Verified |
|----------|-------|----------------|--------------|----------|
| `darwin_arm64` | ✅ | ✅ built | 186 dylibs (162 MB) | runs; no absolute deps remain |
| `linux_amd64` | ✅ | ✅ built (Docker) | 113 libs (157 MB) | **runs with no system GDAL**, incl. under `mpiexec -n 2` |
| `windows_amd64` | ✅ | ⚠️ prebuilt, vendored | none | **unverified — needs Windows** |
| `darwin_amd64` | ✅ | ❌ not built | — | needs an Intel Mac |

**F11 confirmed on the developer's own machine.** The previously shipped macOS
binary links `libgdal.39`; this host has `libgdal.38`, and that binary produces
*no output at all* — dyld fails silently. The rebuilt, bundled binary works.

**Three bugs the clean-room container test caught**, none of which would have
shown up on the build host:
1. `$ORIGIN` was set on the Linux executable but **not on the bundled libraries**,
   so `libgdal` could not find its own siblings.
2. Bundling `libstdc++`/`libgcc_s` from Debian 12 **broke older hosts**
   (`GLIBC_2.36 not found` on Ubuntu 22.04) — bundling those actively hurts.
3. Bundling `libmpi` conflicted with the host's `mpiexec`, which is what actually
   launches the binary — it died inside `opal_init`. MPI and the C++ runtime are
   now deliberately taken from the host, which the Dependencies panel requires.

**Cost of strategy A:** `bin/` is 323 MB and the release zip is **209 MB**
(without `roi_data/`). `bin/` is gitignored and rebuilt at release time by
`build_binaries.sh` + `build_native.sh all`.

**2026-09-08 — Phase 6 complete; the two blocked rows moved into CI.**

`metadata.txt` now carries real author/email/repository/tracker/homepage/icon/tags.
`EIL_Icon.png` went from 6.5 MB to 412 KB — and turns out to be **referenced
nowhere**; only `mlprep-icon.jpg` and `DEM_icon.svg` are used. Backups removed.
`README.md` documents installation, the dependency matrix, the release process,
Windows long-path enablement, and the known gaps.

**F17 (MAX_PATH).** Intermediate names shortened: the GRASS database is now
`g<8-char hash>` instead of `grass_db_<full DEM basename>`, `tiles_temp` → `tiles`,
`slu_<n>` → `s<n>`. Worst-case GRASS mapset path drops from 128 to 84 characters
— the one that matters, since GRASS nests deeply beneath it under `%TEMP%`.

**The two hardware-blocked rows are now CI jobs**
(`.github/workflows/plugin-cross-platform.yml`):

> **Correction (2026-10-12):** that workflow file was never committed — it is
> absent from the tree and from git history, so none of the jobs below ever
> ran. It is replaced by `.github/workflows/ci.yml` (Python tests on all three
> OSes + a Windows build of the engine and bundled TauDEM tools).

| Job | Runner | Covers |
|---|---|---|
| `tests` | windows / ubuntu / macos | Go + Python suites on real Windows — the 11 Windows-only branches finally execute |
| `engines` | windows / ubuntu / macos | the zonal engine *runs*, not just links |
| `native-macos-intel` | `macos-13` | builds + bundles the routing engine for `darwin_amd64`, and fails if any absolute path remains |
| `windows-engine-deps` | windows | `dumpbin /dependents` on the prebuilt engine |

**Windows risk reduced today, not just deferred.** The `cmd.exe` command-line
builder — the piece most likely to be wrong — was pure string logic trapped
behind `//go:build windows`, so it could never be tested here. It now lives in
`toolpath/cmdline.go` with no build tag, covered by **8 tests that run on every
platform**, including the `C:\Program Files\GRASS GIS 8.4` quoting case.

**A bug introduced and caught in this phase:** `build_binaries.sh` began with
`rm -rf bin/`, which silently deleted the C++ engines and their 300 MB of
bundled libraries that `build_native.sh` writes to the same tree. It now removes
only its own outputs.

**Still genuinely unverified:** the plugin has never been loaded in QGIS on
Windows. CI covers the unit level and that the engines start; it has no QGIS,
GRASS or TauDEM, so the end-to-end Windows pipeline remains untested.

**2026-10-12 — TauDEM bundled on Linux (prototype); proj.db shipped.**

`pitremove` and `dinfflowdir` are built from vendored TauDEM 5.5.0 sources by
`build_native.sh linux` and share the engine's `lib/` (no new libraries, ~175 KB
each). `find_taudem()` prefers a user override, then the bundle, then a system
install. Verified on clean Ubuntu 24.04 (OpenMPI only, no GDAL): pits filled,
`-n 1` and `-n 2` bit-identical.

The same test showed every bundled output **lost its EPSG code**: the bundled
libproj had no `proj.db` ("Cannot find proj.db"). The Linux bundle now ships
`share/proj/proj.db` (PROJ 9.1, 8 MB) and `bundled_environment()` sets
`PROJ_DATA` for bundled binaries **only**. Handing it to anything else is
actively harmful — confirmed: Ubuntu 24.04's PROJ 9.4 given that database
fails with "DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 3 is
expected", so GRASS and system GDAL must keep `tool_environment()` unchanged.

**Open, found by the same test:**
- The Linux bundle does **not** start on glibc < 2.36 (Ubuntu 22.04): the
  bundled `libexpat`/`libresolv` from Debian 12 require it. Pre-existing — the
  engine build from 2026-09-08 fails identically. The "older distributions"
  claim in `Dockerfile.linux` is wrong until this is fixed.
- The `darwin_arm64` bundle ships `libproj.25.dylib` without `proj.db`, so the
  macOS engine has the same EPSG loss. `bundled_environment()` already handles
  it once `build_host` copies `share/proj`.

---

## TODO

### Phase 1 — Unblock startup (est. 0.5 day, no Windows machine required)

- [x] ~~Delete the `venv/` directory~~ — **kept in place by user decision**; a
      packaging guard replaces deletion (see `build_release.sh`)
- [x] Add `venv/`, `ls_detect_env/`, `__pycache__/`, `.pytest_cache/`, `dist/`,
      `.DS_Store`, `*.py[cod]` to the plugin's own `.gitignore`
- [x] Add `build_release.sh`: stages through an explicit deny list, then **fails
      the build** if `pyvenv.cfg`/`__pycache__`/`.git`/`.DS_Store` leak in
- [x] Create `platform_utils.py` with `find_exe`, `venv_python`, `venv_bin_dir`, `bundled_binary`, `subprocess_kwargs`, `tool_environment`
- [x] Replace `merged_wrapper.get_venv_python_path()` with `platform_utils.venv_python`
- [x] Replace `landslide_wrapper.get_venv_python_path()` with `platform_utils.venv_python` (F2)
- [x] Replace `eil_wrapper.py`'s hardcoded `<plugin_dir>/venv/bin/python` (F2 — the third convention)
- [x] Replace `SetupWorker.get_base_python()` with `platform_utils.base_python()`: POSIX branch, `shutil.which` fallback (F3)
- [x] Raise an explicit, user-facing error when no base Python can be found

### Phase 2 — Centralize executable discovery (est. 1 day)

- [x] Rewrite `MLDFIDepositionWorker._locate_taudem()` to use `find_exe("pitremove", "taudem")` (F4)
- [x] Rewrite `MLDFIDepositionWorker._locate_mpi()` to use `find_exe("mpiexec", "mpiexec")` (F5)
- [x] Route the Go executable lookup through `bundled_binary()` with a `find_exe("go")` dev fallback (F6)
- [x] Replace the Homebrew `PATH` prepend with `tool_environment()` (F14 — the hardcoded prepend is gone entirely)
- [x] Surface actionable errors when TauDEM, MPI, or the zonal engine is missing
- [x] Build the **Dependencies preflight panel** (✓/✗ per tool, browse-to-override, `QSettings` persistence)
- [x] Block "Run" and surface the panel when a required tool is missing
- [x] Group tools by pipeline so Landslide Detection is never blocked by GRASS/TauDEM
- [x] Skip depositional routing with a logged explanation instead of a mid-pipeline dialog

### Phase 3 — Encoding and subprocess hygiene (est. 0.5 day)

- [x] Add `encoding="utf-8"` to all text-mode `open()` calls (F12) — also found two more in `py_files/GallenModel{,_v1}.py`
- [x] Route every `subprocess.run` / `Popen` through `subprocess_kwargs()` (F13, F16)
- [x] Verify non-ASCII handling — 4 tests covering silent corruption, the raising case, undecodable bytes, and non-ASCII output paths
- [x] Make the matplotlib backend selection resilient (`qtagg` → `Qt5Agg` fallback) (F15)

### Phase 4 — Go engine portability (est. 2 days, needs Windows for verification)

- [x] Replace `os.Getenv("HOME")` with `os.UserHomeDir()` (F9)
- [x] Resolve the GRASS addon path per-OS (`%APPDATA%\GRASS8\addons` on Windows)
- [x] Extend GRASS discovery for Windows (`grass84.bat`) and Linux (F8) — moved into `toolpath.Grass()`
- [x] Route `.bat` launchers through `cmd.exe` with a hand-built command line (`toolpath/command_windows.go`)
- [x] Replace the `#!/bin/bash` heredoc with a GRASS Python script via `--exec <python>` (F7)
- [x] Remove the now-dead `os.Chmod(0755)` call
- [x] Accept explicit GDAL binary paths instead of bare `gdalwarp` / `ogrinfo` / `gdal_rasterize` / `gdaladdo` (F10)
- [x] Pass the resolved GDAL bin dir from Python into the Go subprocess env (`MLPREP_GDAL_BIN`)
- [x] Add a cross-compile script for `darwin/{arm64,amd64}`, `linux/amd64`, `windows/amd64` (`build_binaries.sh`)
- [x] Commit the resulting binaries under `bin/<goos>_<goarch>/`
- [ ] Remove the nested `ml-prep-scripts/.git` repository or convert it to a submodule

### Phase 5 — Native C++/MPI engine (est. 2–3 days, needs a Linux box + an Intel Mac or CI)

- [x] Decide on strategy — **A (bundle dependencies)**, chosen by the user with measured sizes in hand
- [x] Build `darwin/amd64` — **wired up in CI** (`macos-13` runner); cannot run on this machine
- [x] Relink Homebrew dylibs to `@rpath` and bundle them beside the binary (`scripts/bundle_macos_deps.py`, recursive)
- [x] Ad-hoc-codesign the macOS binary and every bundled dylib (arm64 rejects unsigned code after `install_name_tool`)
- [x] Build `linux/amd64` via Docker, with `$ORIGIN` rpath on the binary **and** each bundled library
- [x] Verify the Linux engine runs on a host with **no GDAL installed**, standalone and under `mpiexec -n 2`
- [x] Windows verification — **wired up in CI** (`windows-latest`); the `cmd.exe` command-line builder was extracted from the build-tagged file so its 8 tests now run on every platform
- [x] Move both engines into `bin/<goos>_<goarch>/`; route selection through `bundled_binary()`
- [x] Delete `build_mac/` probe artifacts and exclude all build trees from the package

### Phase 6 — Packaging and release (est. 1 day)

- [x] Fill in `metadata.txt`: `author`, `email`, `tracker`, `repository`, `homepage`, `icon`, `tags` (F19)
- [x] Set `qgisMinimumVersion` — **3.22**, a judgement from the APIs used, not a test result (only 3.42.1 has been run)
- [x] Downsize `EIL_Icon.png` — 6.5 MB / 2048px → 412 KB / 512px (it is referenced nowhere; downsized rather than deleted)
- [x] Remove `eil_mainwindow_backup.py` and `main_backup.go.txt`
- [x] Write `build_release.sh` producing a clean, *verified* zip
- [x] Shorten intermediate temp directory names (F17) — GRASS mapset path 128 → 84 chars
- [x] Document Windows long-path enablement in `README.md`
- [x] Write `README.md`: install, dependency matrix, release process, known gaps

### Phase 7 — Verification

- [ ] Smoke test: plugin loads in QGIS on Windows 11
- [ ] Smoke test: plugin loads in QGIS on Ubuntu 22.04
- [ ] Smoke test: plugin loads on an Intel Mac (or Rosetta-disabled arm64)
- [ ] End-to-end EIL run on each platform against the same DEM; compare outputs
- [ ] End-to-end landslide-detection run on each platform
- [ ] Preflight panel correctly reports every missing dependency on a clean VM
- [ ] Run with a non-ASCII output path on Windows (encoding regression check)

---

## Dependency Matrix

External tools the plugin requires, and where they come from per platform.

| Tool | macOS | Windows | Linux | Bundled? |
|------|-------|---------|-------|----------|
| Python 3 | QGIS-bundled | QGIS-bundled | system | no (venv created at runtime) |
| MPI | Homebrew `open-mpi` | MS-MPI | `openmpi-bin` | no |
| TauDEM | bundled (`pitremove`, `dinfflowdir`; arm64) | bundled (`.exe`) | bundled (`pitremove`, `dinfflowdir`) | **yes** (darwin_arm64, windows_amd64, linux_amd64) |
| GRASS GIS 8.x | `.app` bundle | OSGeo4W / standalone | distro package | no |
| GDAL CLI | Homebrew / QGIS | OSGeo4W / QGIS | distro / QGIS | no (use QGIS's) |
| `r.slopeunits` addon | `g.extension` | `g.extension` | `g.extension` | no |
| Go zonal engine | — | — | — | **yes** (after Phase 4) |
| C++/MPI step4 | — | — | — | **yes** (after Phase 5) |

Everything in the "no" rows must be surfaced by the preflight panel.

---

## Effort Summary

| Phase | Description | Estimate | Blocking dependency |
|-------|-------------|----------|---------------------|
| 1 | Unblock startup | 0.5 day | none |
| 2 | Centralize discovery + preflight | 1 day | Phase 1 |
| 3 | Encoding / subprocess hygiene | 0.5 day | none (parallelizable) |
| 4 | Go engine portability | 2 days | Windows VM to verify |
| 5 | Native C++/MPI engine | 2–3 days | Linux box + Intel Mac / CI |
| 6 | Packaging & release | 1 day | Phases 4–5 |
| 7 | Verification | 1–2 days | all |
| | **Total** | **8–10 days** | |

Phases 1–3 are self-contained and require no additional hardware.
