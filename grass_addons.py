"""Offline installation of the GRASS addons the plugin needs.

`g.extension extension=r.slopeunits` is the normal route, but on Windows it
downloads a precompiled archive from wingrass.fsv.cvut.cz, which fails on any
network that cannot resolve that host:

    ERROR: Cannot open URL <http://wingrass.fsv.cvut.cz/grass84/addons/
    grass-8.4.2/r.slopeunits.zip>: <urlopen error [Errno 11001] getaddrinfo failed>

All four r.slopeunits modules are pure Python, so a copy into the GRASS addon
directory is a complete installation. The modules ship in vendor/grass_addons/,
which removes the network dependency entirely.
"""

import os
import shutil
import stat

from . import platform_utils as pu

VENDOR_SUBDIR = os.path.join("vendor", "grass_addons")

# GRASS launches script addons on Windows through a .bat shim next to them.
_WINDOWS_SHIM = '@"%GRASS_PYTHON%" "%~dp0..\\scripts\\{module}.py" %*\r\n'


def vendored_dir(plugin_dir, addon="r.slopeunits"):
    return os.path.join(plugin_dir, VENDOR_SUBDIR, addon)


def vendored_modules(plugin_dir, addon="r.slopeunits"):
    """The .py modules bundled for `addon`, newest listing order."""
    source = vendored_dir(plugin_dir, addon)
    if not os.path.isdir(source):
        return []
    return sorted(
        os.path.join(source, name)
        for name in os.listdir(source)
        if name.endswith(".py")
    )


def install_bundled_addon(plugin_dir, addon="r.slopeunits", log=None):
    """Copy a bundled addon into the user's GRASS addon directory.

    Returns the list of installed module names. Raises RuntimeError with a
    usable message rather than leaving a half-installed addon behind.
    """
    log = log or (lambda _message: None)

    modules = vendored_modules(plugin_dir, addon)
    if not modules:
        raise RuntimeError(
            f"No bundled copy of {addon} found at:\n"
            f"  {vendored_dir(plugin_dir, addon)}\n\n"
            "Reinstall the plugin, or install the addon with:\n"
            "  g.extension extension=" + addon
        )

    addons_root = pu.grass_addon_dir()
    scripts_dir = os.path.join(addons_root, "scripts")
    bin_dir = os.path.join(addons_root, "bin")

    try:
        os.makedirs(scripts_dir, exist_ok=True)
        os.makedirs(bin_dir, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create the GRASS addon directory:\n  {addons_root}\n\n{exc}"
        )

    installed = []
    for source in modules:
        module = os.path.splitext(os.path.basename(source))[0]
        # GRASS on POSIX execs a script addon from PATH by its exact module
        # name, so `r.slopeunits.create.py` is never found there. Only Windows
        # resolves the .py (through the .bat shim). g.extension does the same.
        target = os.path.join(
            scripts_dir, os.path.basename(source) if pu.IS_WINDOWS else module
        )

        try:
            shutil.copy2(source, target)
            if not pu.IS_WINDOWS:
                _remove_stale_py_copy(scripts_dir, module)
                current = os.stat(target).st_mode
                os.chmod(target, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            else:
                shim = os.path.join(bin_dir, module + ".bat")
                with open(shim, "w", encoding="ascii", newline="") as handle:
                    handle.write(_WINDOWS_SHIM.format(module=module))
        except OSError as exc:
            raise RuntimeError(f"Could not install {module}:\n{exc}")

        log(f"   > Installed {module}")
        installed.append(module)

    return installed


def _remove_stale_py_copy(scripts_dir, module):
    """Delete a `<module>.py` left by older installs: GRASS cannot run it on
    POSIX, and its presence made the addon look installed when it was not."""
    stale = os.path.join(scripts_dir, module + ".py")
    if os.path.isfile(stale):
        os.remove(stale)


def bundled_addon_installed(grass_exe=None):
    """True when GRASS can find every addon module the engine calls."""
    return all(
        pu.grass_addon_installed(m, grass_exe) for m in pu.GRASS_ADDON_MODULES
    )


def ensure_bundled_addon(plugin_dir, addon="r.slopeunits", log=None):
    """Install the bundled addon unless it is already present.

    Returns the installed module names, or [] when nothing needed doing.
    """
    log = log or (lambda _message: None)

    if bundled_addon_installed():
        log(f"   > GRASS addon {addon} already installed")
        return []

    log(f"   > Installing GRASS addon {addon} into {pu.grass_addon_dir()}")
    installed = install_bundled_addon(plugin_dir, addon, log=log)
    pu.clear_module_cache()
    return installed


def grass_setup_status():
    """`(grass_path, addon_ready)` -- what the GRASS setup gate waits on.

    The addon only counts once GRASS itself is found: an addon directory left
    behind by an uninstalled GRASS cannot run anything.
    """
    grass = pu.find_grass()
    return grass, bool(grass) and bundled_addon_installed(grass)
