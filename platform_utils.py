"""Cross-platform resolution of external executables and managed environments.

Every lookup of an external tool (MPI, TauDEM, GRASS, GDAL, Go) and every path
to a managed virtualenv interpreter goes through this module, so that adding a
platform means editing one table instead of five call sites.

Resolution order for `find_exe` is always:

    1. the directory a user pointed at, stored in QSettings ("mlprep/exe/<key>")
    2. `extra_dirs` supplied by the caller
    3. PATH, via shutil.which
    4. the platform search table below

`extra_dirs` outranks PATH deliberately: GDAL must resolve to the build QGIS
itself ships, not to an unrelated copy that happens to be earlier on PATH.

Returning None means "not installed" -- callers surface that to the user rather
than falling back to a bare command name that will fail later with a cryptic
FileNotFoundError from deep inside a worker thread.
"""

import glob
import os
import platform
import shutil
import stat
import subprocess
import sys
from typing import NamedTuple

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

SETTINGS_PREFIX = "mlprep/exe"

# Root of everything the plugin installs for itself: the two virtualenvs and,
# on macOS, the conda-forge GRASS that scripts/install_grass.sh creates.
ENVS_DIR = os.path.join(os.path.expanduser("~"), ".mlprep_envs")
GRASS_ENV_DIR = os.path.join(ENVS_DIR, "grass")

# Environment variables handed to the bundled Go engine so it reuses the
# executables this module already resolved instead of re-deriving them.
ENV_GRASS_EXE = "MLPREP_GRASS_EXE"
ENV_GDAL_BIN = "MLPREP_GDAL_BIN"


def _platform_key():
    if IS_WINDOWS:
        return "win32"
    if IS_MACOS:
        return "darwin"
    return "linux"


# Directories searched when a tool is not on PATH. Globs are expanded so that
# versioned install directories (GRASS 8.3 vs 8.4) do not need to be enumerated.
_SEARCH_DIRS = {
    "win32": [
        r"C:\Program Files\TauDEM\TauDEM5Exe",
        r"C:\Program Files\Microsoft MPI\Bin",
        r"C:\Program Files\GRASS GIS *",
        r"C:\OSGeo4W\bin",
        r"C:\OSGeo4W64\bin",
        # QGIS ships its own GRASS; on a standalone QGIS install this is the
        # only GRASS present.
        r"C:\Program Files\QGIS *\bin",
        r"C:\Program Files\QGIS *\apps\grass\grass*\bin",
    ],
    "darwin": [
        # Created by scripts/install_grass.sh (Homebrew micromamba + conda-forge).
        os.path.join(GRASS_ENV_DIR, "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/local/taudem",
        "/Applications/GRASS-*.app/Contents/Resources/bin",
        "/Applications/QGIS*.app/Contents/MacOS/bin",
    ],
    "linux": [
        "/usr/bin",
        "/usr/local/bin",
        "/usr/lib/taudem",
        "/usr/lib/grass*/bin",
    ],
}

# Alternative basenames to try for a tool, in order. GRASS in particular ships
# under a version-suffixed name on most Linux distributions and as a batch
# launcher on Windows.
_ALIASES = {
    # QGIS bundles its own GRASS: QGIS 3.44 ships grass85. Newest first.
    "grass": ["grass86", "grass85", "grass84", "grass83", "grass82", "grass"],
    "mpiexec": ["mpiexec", "mpirun"],
}

# Extensions to try in addition to the platform default. GRASS on Windows is a
# .bat launcher, not a .exe.
_EXTRA_EXTENSIONS = {
    "grass": [".bat", ".cmd"] if IS_WINDOWS else [],
}


def _expand(patterns):
    """Expand glob patterns into existing directories, preserving order."""
    dirs = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            dirs.extend(sorted(glob.glob(pattern), reverse=True))
        elif os.path.isdir(pattern):
            dirs.append(pattern)
    return dirs


def _candidate_names(name):
    """Every (basename + extension) combination worth probing for `name`."""
    extensions = [EXE_SUFFIX] + _EXTRA_EXTENSIONS.get(name, [])
    return [
        alias + ext
        for alias in _ALIASES.get(name, [name])
        for ext in extensions
    ]


def _override_dir(key):
    """Directory a user pointed at for `key`, or None.

    Overrides are stored as a *directory*, not an executable path, because one
    key can cover several tools that live together (pitremove and dinfflowdir
    share the "taudem" key). Storing one executable would otherwise make the
    sibling tool resolve to the wrong binary.
    """
    if not key:
        return None
    try:
        from qgis.PyQt.QtCore import QSettings
    except ImportError:
        return None
    value = QSettings().value(f"{SETTINGS_PREFIX}/{key}")
    return value if value and os.path.isdir(value) else None


def find_exe(name, settings_key=None, extra_dirs=None):
    """Resolve an external tool to an absolute path, or None if not installed.

    `settings_key` names the QSettings entry holding the directory a user
    pointed at (set from the plugin's Dependencies panel), searched first.
    `extra_dirs`
    is searched before PATH so a caller-supplied location (QGIS's own bin
    directory, a managed virtualenv) beats an unrelated copy on PATH.
    """
    def probe(directories):
        for directory in _expand(directories):
            for candidate in _candidate_names(name):
                path = os.path.join(directory, candidate)
                if os.path.isfile(path):
                    return path
        return None

    override = _override_dir(settings_key)
    hit = probe(([override] if override else []) + list(extra_dirs or []))
    if hit:
        return hit

    for candidate in _ALIASES.get(name, [name]):
        hit = shutil.which(candidate)
        if hit:
            return hit

    return probe(_SEARCH_DIRS[_platform_key()])


def set_exe_override(settings_key, path):
    """Persist where a user located a tool.

    `path` may be the executable itself or the directory holding it; the
    directory is what gets stored, so sibling tools sharing the key resolve
    correctly too.
    """
    from qgis.PyQt.QtCore import QSettings

    directory = path if os.path.isdir(path) else os.path.dirname(path)
    QSettings().setValue(f"{SETTINGS_PREFIX}/{settings_key}", directory)


# ---------------------------------------------------------------------------
# GDAL — prefer the copy QGIS already ships over whatever is on PATH
# ---------------------------------------------------------------------------

def qgis_bin_dirs():
    """Directories inside the running QGIS install that may hold GDAL tools."""
    dirs = []
    try:
        from qgis.core import QgsApplication

        prefix = QgsApplication.prefixPath()
        if prefix:
            dirs.extend([os.path.join(prefix, "bin"), prefix])
    except Exception:
        pass

    if IS_WINDOWS:
        # OSGeo4W layout: <root>/apps/qgis/python -> <root>/bin
        dirs.append(os.path.join(os.path.dirname(os.path.dirname(sys.prefix)), "bin"))
    return [d for d in dirs if os.path.isdir(d)]


def gdal_bin_dir():
    """Directory containing gdalwarp/ogrinfo, or None if GDAL CLI is absent."""
    gdalwarp = find_exe("gdalwarp", "gdalwarp", extra_dirs=qgis_bin_dirs())
    return os.path.dirname(gdalwarp) if gdalwarp else None


# ---------------------------------------------------------------------------
# Managed virtualenvs — single source of truth
# ---------------------------------------------------------------------------


def base_python():
    """Interpreter used to create the managed virtualenvs.

    Prefers the Python that QGIS itself ships so the created environments match
    QGIS's version, then falls back to PATH. Raises rather than returning a bare
    "python3" that would fail later inside a worker thread with no explanation.

    Note that under QGIS `sys.executable` is the QGIS binary (qgis-bin), not an
    interpreter, so it is only usable when the host really is Python.
    """
    candidates = []
    if IS_WINDOWS:
        candidates += [
            os.path.join(sys.prefix, "python.exe"),
            os.path.join(sys.prefix, "Scripts", "python.exe"),
        ]
    else:
        candidates += [
            os.path.join(sys.prefix, "bin", "python3"),
            os.path.join(sys.prefix, "bin", "python"),
        ]

    if os.path.basename(sys.executable).lower().startswith("python"):
        candidates.append(sys.executable)

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    for name in ("python3", "python"):
        hit = shutil.which(name)
        if hit:
            return hit

    raise RuntimeError(
        "No Python interpreter was found to build the plugin's environments.\n\n"
        f"Checked QGIS's own location ({sys.prefix}) and PATH.\n"
        "Install Python 3, or make it available on PATH, and try again."
    )


# Written into a virtualenv once its requirements have installed successfully.
# The interpreter existing is NOT evidence the environment is usable: OSGeo4W's
# `python -m venv` creates a working python.exe and only then fails to bootstrap
# pip, leaving an interpreter that runs but has no packages.
READY_STAMP = ".mlprep_ready"


def requirements_fingerprint(requirements_path):
    """Hash of a requirements file, so a changed file forces a reinstall."""
    import hashlib

    with open(requirements_path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def venv_is_provisioned(venv_name, requirements_path=None):
    """True when the environment has an interpreter AND its packages installed.

    Deliberately file-based so it is cheap enough to call on every panel
    refresh; env_setup performs the deeper runtime checks during setup.
    """
    if not os.path.isfile(venv_python(venv_name)):
        return False

    stamp = os.path.join(ENVS_DIR, venv_name, READY_STAMP)
    if not os.path.isfile(stamp):
        return False

    if requirements_path is None or not os.path.isfile(requirements_path):
        return True

    try:
        with open(stamp, encoding="utf-8") as handle:
            recorded = handle.read().strip()
    except OSError:
        return False
    return recorded == requirements_fingerprint(requirements_path)


def venv_bin_dir(venv_name):
    """Directory holding a managed virtualenv's executables."""
    return os.path.join(ENVS_DIR, venv_name, "Scripts" if IS_WINDOWS else "bin")


def venv_python(venv_name):
    """Absolute path to the interpreter of a managed virtualenv."""
    name = "python.exe" if IS_WINDOWS else "python"
    return os.path.join(venv_bin_dir(venv_name), name)


# ---------------------------------------------------------------------------
# Bundled binaries — cross-compiled at release time, shipped under bin/
# ---------------------------------------------------------------------------

_ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "AMD64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


def _rosetta_translated():
    """True when this process is an x86_64 build running on Apple Silicon."""
    if not IS_MACOS:
        return False
    try:
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        rc = libc.sysctlbyname(
            b"sysctl.proc_translated",
            ctypes.byref(value), ctypes.byref(size), None, 0,
        )
    except Exception:
        # The sysctl is absent on Intel Macs and on older macOS; either way
        # the process is not translated.
        return False
    return rc == 0 and value.value == 1


def host_arch():
    """The machine's real architecture, not the one this process reports.

    QGIS ships x86_64 macOS builds, so on an Apple Silicon Mac `platform
    .machine()` inside QGIS returns "x86_64" even though the hardware is
    arm64. Keying the bundled engines off that reports every arm64 binary as
    missing. A translated process can exec a native arm64 binary without
    trouble, so the hardware architecture is the correct key.
    """
    if _rosetta_translated():
        return "arm64"
    return platform.machine()


def platform_tag():
    """Directory name under bin/ for this host, e.g. "darwin_arm64"."""
    goos = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    goarch = _ARCH_ALIASES.get(host_arch(), "amd64")
    return f"{goos}_{goarch}"


def bundled_binary(plugin_dir, name):
    """Path to a shipped cross-compiled binary, or None if unusable on this host.

    "Unusable" covers more than "absent". QGIS installs plugins with
    `zipfile.extractall`, which discards Unix permission bits, so a binary
    shipped as 0755 lands on disk as 0644 and every attempt to run it fails
    with "Permission denied" minutes into a pipeline. The bit is restored here
    -- the same repair `grass_addons` performs on addon scripts -- and a copy
    that cannot be repaired is reported as missing rather than handed to a
    worker that will only fail later.
    """
    path = os.path.join(bundled_dir(plugin_dir), name + EXE_SUFFIX)
    if not os.path.isfile(path):
        return None
    return path if ensure_executable(path) else None


def bundled_dir(plugin_dir):
    """bin/<tag>/ -- where this host's shipped binaries live."""
    return os.path.join(plugin_dir, "bin", platform_tag())


def is_bundled(plugin_dir, path):
    """True when `path` is one of the binaries shipped in bin/<tag>/."""
    if not path:
        return False
    return (os.path.dirname(os.path.abspath(path))
            == os.path.abspath(bundled_dir(plugin_dir)))


# The bundle's libproj is a fixed release and needs the proj.db written by
# that same release; build_native.sh ships it here.
BUNDLED_PROJ_SUBDIR = os.path.join("share", "proj")


def bundled_environment(base, plugin_dir):
    """A copy of `base` for running bundled binaries only.

    Points PROJ_DATA at the shipped proj.db so the bundled libproj finds its
    own database. Never use it for GRASS, system GDAL or a separately
    installed TauDEM: their PROJ would reject a database from another release
    and lose all CRS support. `base` itself is left untouched.
    """
    env = dict(base)
    proj_dir = os.path.join(bundled_dir(plugin_dir), BUNDLED_PROJ_SUBDIR)
    if os.path.isfile(os.path.join(proj_dir, "proj.db")):
        env["PROJ_DATA"] = proj_dir
    return env


TAUDEM_KEY = "taudem"
# Built from the vendored TauDEM sources by build_native.sh and shipped in
# bin/<tag>/ next to the routing engine (linux_amd64 and darwin_arm64).
BUNDLED_TAUDEM_TOOLS = ("pitremove", "dinfflowdir")


def find_taudem(name, plugin_dir=None, extra_dirs=None):
    """Resolve a TauDEM tool: user override, then bundled copy, then system.

    A directory the user picked in the Dependencies panel is an explicit
    choice and wins. Otherwise the bundled build -- the one tested with the
    routing engine -- beats whatever TauDEM happens to be on PATH.
    """
    if plugin_dir and not _override_dir(TAUDEM_KEY):
        bundled = bundled_binary(plugin_dir, name)
        if bundled:
            return bundled
    return find_exe(name, TAUDEM_KEY, extra_dirs=extra_dirs)


def ensure_executable(path):
    """Add the execute bit to `path` if it is missing. True when runnable."""
    if IS_WINDOWS:
        return True
    if os.access(path, os.X_OK):
        return True

    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        # A read-only install directory, or a file owned by another user.
        return False
    return os.access(path, os.X_OK)


# ---------------------------------------------------------------------------
# Subprocess defaults
# ---------------------------------------------------------------------------

def subprocess_kwargs(**extra):
    """Portable defaults for every subprocess call in the plugin.

    Forces UTF-8 decoding (the Windows default is cp1252, which raises on
    non-ASCII paths and on GDAL's progress output) and suppresses the console
    window that would otherwise flash for each child process on Windows.
    """
    kwargs = {"text": True, "encoding": "utf-8", "errors": "replace"}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    kwargs.update(extra)
    return kwargs


def tool_environment(base=None):
    """Environment for child processes: QGIS-clean, with resolved tools exported.

    PYTHONHOME/PYTHONPATH are dropped so a child interpreter does not inherit
    QGIS's, and the resolved GRASS/GDAL locations are exported so the bundled
    Go engine reuses them instead of guessing.
    """
    env = dict(base if base is not None else os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)

    # The same lookup as the setup gate, so the engine and the installer
    # scripts use the very GRASS the gate approved (including QGIS's own copy).
    grass = find_grass()
    if grass:
        env[ENV_GRASS_EXE] = grass

    # Every GRASS the plugin starts must search the directory the plugin
    # installs addons into. GRASS's own default differs on macOS
    # (~/Library/GRASS/<ver>/Addons), where r.slopeunits was then "not found".
    env["GRASS_ADDON_BASE"] = grass_addon_dir()

    gdal_dir = gdal_bin_dir()
    if gdal_dir:
        env[ENV_GDAL_BIN] = gdal_dir
        env["PATH"] = gdal_dir + os.pathsep + env.get("PATH", "")

    if IS_WINDOWS:
        qgis_bin = os.path.join(os.path.dirname(os.path.dirname(sys.prefix)), "bin")
        if os.path.isdir(qgis_bin):
            env["PATH"] = qgis_bin + os.pathsep + env.get("PATH", "")

    return env


# ---------------------------------------------------------------------------
# Dependency reporting (backs the Dependencies preflight panel)
# ---------------------------------------------------------------------------

# Which pipeline each external tool belongs to. Landslide Detection needs none
# of them, so a missing GRASS must never block that tab.
FEATURE_EIL = "eil"
FEATURE_DEPOSITION = "deposition"
FEATURE_LANDSLIDE = "landslide"

FEATURE_LABELS = {
    FEATURE_EIL: "EIL Hazard Mapping",
    FEATURE_DEPOSITION: "Depositional Routing (ML-DFI Step 4)",
    FEATURE_LANDSLIDE: "Landslide Detection",
}

_HINTS = {
    "mpiexec": {
        "win32": "Install Microsoft MPI (msmpisetup.exe) from the Microsoft Download Center.",
        "darwin": "brew install open-mpi",
        "linux": "sudo apt install openmpi-bin   (or your distribution's equivalent)",
    },
    "taudem": {
        "win32": r"Install TauDEM 5.3; the default location is C:\Program Files\TauDEM.",
        "darwin": "Build TauDEM from source, or install it into ~/.mlprep_envs/venv/bin.",
        "linux": "Bundled with the plugin. Reinstall it, or run ./build_native.sh linux.",
    },
    "grass": {
        "win32": r"Run scripts\install_grass_windows.bat, or install GRASS GIS 8.x "
                 "(standalone installer or OSGeo4W).",
        "darwin": "Run scripts/install_grass.sh (installs GRASS through Homebrew "
                  "micromamba into ~/.mlprep_envs/grass).",
        "linux": "Run scripts/install_grass.sh, or: sudo apt install grass",
    },
    "gdal": {
        "win32": "Normally supplied by QGIS/OSGeo4W. Reinstall QGIS if it is absent.",
        "darwin": "Normally supplied by QGIS. Otherwise: brew install gdal",
        "linux": "sudo apt install gdal-bin",
    },
}


def _hint(topic):
    return _HINTS.get(topic, {}).get(_platform_key(), "")


class ToolSpec(NamedTuple):
    """One external program the plugin shells out to."""

    key: str        # QSettings override key
    name: str       # executable base name passed to find_exe
    label: str      # shown to the user
    feature: str    # pipeline that needs it
    hint: str       # how to install it on this platform


class ToolStatus(NamedTuple):
    spec: ToolSpec
    path: object    # str when resolved, None when missing

    @property
    def found(self):
        return self.path is not None


REQUIRED_TOOLS = [
    ToolSpec("grass", "grass", "GRASS GIS 8.x",
             FEATURE_EIL, _hint("grass")),
    ToolSpec("gdalwarp", "gdalwarp", "GDAL (gdalwarp)",
             FEATURE_EIL, _hint("gdal")),
    ToolSpec("ogrinfo", "ogrinfo", "OGR (ogrinfo)",
             FEATURE_EIL, _hint("gdal")),
    ToolSpec("mpiexec", "mpiexec", "MPI launcher",
             FEATURE_DEPOSITION, _hint("mpiexec")),
    ToolSpec(TAUDEM_KEY, "pitremove", "TauDEM (pitremove)",
             FEATURE_DEPOSITION, _hint("taudem")),
    ToolSpec(TAUDEM_KEY, "dinfflowdir", "TauDEM (dinfflowdir)",
             FEATURE_DEPOSITION, _hint("taudem")),
]

# The zonal engine ships with the plugin rather than being installed, so it is
# resolved by platform tag instead of by searching the filesystem.
ENGINE_SPEC = ToolSpec(
    "zonal", "zonal", "Zonal engine (bundled)", FEATURE_EIL,
    "Reinstall the plugin, or run build_binaries.sh with the Go toolchain.",
)

ROUTING_ENGINE_SPEC = ToolSpec(
    "step4", "step4_deposition_zone_mpi", "Routing engine (bundled)",
    FEATURE_DEPOSITION,
    "Not built for this platform. Reinstall, or run build_native.sh.",
)


# The managed virtualenvs are not executables to be found on PATH, but their
# absence breaks a pipeline just as completely, so the panel reports them the
# same way. Landslide Detection needs no external tool at all -- only this.
# Python packages the plugin uses inside the QGIS process. Which of these a
# QGIS build ships varies -- one macOS QGIS reported "No module named
# matplotlib" at plugin load -- so they are reported rather than assumed.
IN_PROCESS_MODULES = [
    ToolSpec("mod_numpy", "numpy", "numpy (Python module)", FEATURE_EIL,
             "Install into QGIS's Python, or use a QGIS build that bundles it."),
    ToolSpec("mod_matplotlib", "matplotlib", "matplotlib (Python module)",
             FEATURE_EIL,
             "Optional: only the hazard chart needs it. Everything else works."),
]


# GRASS addons install as scripts under the user's addon directory. Checking
# the filesystem keeps the panel refresh fast; running GRASS to ask would take
# seconds. Mirrors toolpath.GrassAddons() in the Go engine.
GRASS_ADDON_MODULES = ["r.slopeunits.create"]


def find_grass():
    """Absolute path to the GRASS launcher, or None if GRASS is not installed.

    Same lookup as the Dependencies panel's GRASS row, so the setup gate and
    the panel never disagree about whether GRASS is present.
    """
    return find_exe("grass", "grass", extra_dirs=qgis_bin_dirs())


def grass_addon_dir():
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return os.path.join(appdata, "GRASS8", "addons")
    return os.path.join(os.path.expanduser("~"), ".grass8", "addons")


# One installer per platform family, both under scripts/. Each installs GRASS
# when missing and then the r.slopeunits addon, and exits 0 only when both are
# present -- the GRASS setup dialog runs them and re-checks afterwards.
GRASS_INSTALLER_DIR = "scripts"
GRASS_ADDON_ONLY_FLAG = "/addon-only" if IS_WINDOWS else "--addon-only"


def grass_installer_script_name():
    return "install_grass_windows.bat" if IS_WINDOWS else "install_grass.sh"


def grass_installer_command(plugin_dir):
    """`[program, *args]` that runs this platform's GRASS installer.

    Returns None when the script is missing from the plugin directory, so the
    caller can say so instead of failing to launch it.
    """
    script = os.path.join(plugin_dir, GRASS_INSTALLER_DIR, grass_installer_script_name())
    if not os.path.isfile(script):
        return None

    # Run through the interpreter explicitly: a zip-extracted plugin loses the
    # executable bit, and a .bat is not directly executable without cmd.
    return ["cmd.exe", "/c", script] if IS_WINDOWS else ["/bin/bash", script]


def grass_addon_installed(module, grass_exe=None):
    """Where a GRASS addon module lives, or None.

    Two tiers, because neither alone is correct:

      * the user addon directory, checked on the filesystem -- fast, and the
        location g.extension uses by default;
      * failing that, GRASS itself is asked. `g.extension -s` installs
        system-wide instead, so a filesystem-only check reports a perfectly
        good installation as missing.
    """
    addons = grass_addon_dir()
    # Only names GRASS can actually launch count. A `<module>.py` is runnable
    # on Windows (via the .bat shim) but invisible to GRASS on POSIX.
    if IS_WINDOWS:
        candidates = [
            os.path.join(addons, "scripts", module + ".py"),
            os.path.join(addons, "bin", module + EXE_SUFFIX),
            os.path.join(addons, "bin", module + ".bat"),
        ]
    else:
        candidates = [
            os.path.join(addons, "scripts", module),
            os.path.join(addons, "bin", module),
        ]
    hit = next((c for c in candidates if os.path.isfile(c)), None)
    if hit:
        return hit

    return _ask_grass_for_module(module, grass_exe)


_MODULE_CACHE = {}


def _ask_grass_for_module(module, grass_exe=None):
    """Ask GRASS whether it can run `module`. Cached: this costs a second."""
    if module in _MODULE_CACHE:
        return _MODULE_CACHE[module]

    grass_exe = grass_exe or find_exe("grass", "grass")
    if not grass_exe:
        _MODULE_CACHE[module] = None
        return None

    probe = (
        "import sys, grass.script as gs; "
        f"sys.exit(0 if gs.find_program({module!r}, '--help') else 1)"
    )
    try:
        result = subprocess.run(
            # --tmp-location, not --tmp-project: the latter is GRASS 8.4+ only,
            # and Ubuntu 24.04 still ships 8.3 (where it exits 2).
            # python3 off Windows: Ubuntu and macOS have no bare `python`.
            # Same choice as toolpath.GrassPython() in the Go engine.
            [grass_exe, "--tmp-location", "EPSG:4326", "--exec",
             "python" if IS_WINDOWS else "python3", "-c", probe],
            capture_output=True, timeout=120, env=tool_environment(),
            **subprocess_kwargs()
        )
        found = f"{module} (installed in GRASS)" if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        found = None

    _MODULE_CACHE[module] = found
    return found


def clear_module_cache():
    """Forget cached GRASS module lookups, so Re-check really re-checks."""
    _MODULE_CACHE.clear()


def module_available(name):
    """True when `name` can be imported by the interpreter running QGIS."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


ENV_REQUIREMENTS = {
    "ls_detect_env": "requirements_ls.txt",
    "venv": "requirements_eil.txt",
}

PYTHON_ENVS = [
    ToolSpec("env_ls", "ls_detect_env", "Python environment (detection)",
             FEATURE_LANDSLIDE,
             "Created automatically on first run; needs internet access."),
    ToolSpec("env_eil", "venv", "Python environment (EIL)",
             FEATURE_EIL,
             "Created automatically on first run; needs internet access."),
]


def _resolve_tool(spec, plugin_dir, extra_dirs):
    if spec.key == TAUDEM_KEY:
        return find_taudem(spec.name, plugin_dir, extra_dirs=extra_dirs)
    return find_exe(spec.name, spec.key, extra_dirs=extra_dirs)


def check_dependencies(plugin_dir=None):
    """Resolve every external dependency. Returns a list of ToolStatus.

    Pass `plugin_dir` to also report the bundled engines.
    """
    extra = qgis_bin_dirs()
    statuses = [ToolStatus(spec, _resolve_tool(spec, plugin_dir, extra))
                for spec in REQUIRED_TOOLS]

    for module in GRASS_ADDON_MODULES:
        spec = ToolSpec(
            "grass_addon", module, f"GRASS addon: {module}", FEATURE_EIL,
            "Install with:  g.extension extension=r.slopeunits\n"
            f"Or run:  {grass_installer_script_name()} {GRASS_ADDON_ONLY_FLAG}",
        )
        statuses.append(ToolStatus(spec, grass_addon_installed(module)))

    for spec in IN_PROCESS_MODULES:
        statuses.append(
            ToolStatus(spec, spec.name if module_available(spec.name) else None)
        )

    for spec in PYTHON_ENVS:
        requirements = (os.path.join(plugin_dir, ENV_REQUIREMENTS[spec.name])
                        if plugin_dir else None)
        provisioned = venv_is_provisioned(spec.name, requirements)
        statuses.append(
            ToolStatus(spec, venv_python(spec.name) if provisioned else None)
        )

    if plugin_dir:
        statuses.append(
            ToolStatus(ENGINE_SPEC, bundled_binary(plugin_dir, ENGINE_SPEC.name))
        )
        statuses.append(
            ToolStatus(ROUTING_ENGINE_SPEC,
                       bundled_binary(plugin_dir, ROUTING_ENGINE_SPEC.name))
        )
    return statuses


def missing_dependencies(plugin_dir=None, feature=None):
    """Unresolved tools, optionally narrowed to one pipeline."""
    return [
        status for status in check_dependencies(plugin_dir)
        if not status.found
        and (feature is None or status.spec.feature == feature)
    ]


def is_plausible_exe(name, path):
    """True when `path`'s filename looks like the tool `name`.

    Used to warn -- never to block -- when a user browses to an override.
    """
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return stem in {alias.lower() for alias in _ALIASES.get(name, [name])}
