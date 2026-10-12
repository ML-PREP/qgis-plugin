"""Guard the plugin's load path against unbundled third-party imports.

QGIS calls classFactory() at startup, which imports mainPlugin ->
app.main_window and, through it, every module in the feature packages. Any
module-scope import there must be satisfiable by QGIS's own
interpreter. rasterio is bundled with QGIS on macOS but NOT with OSGeo4W QGIS on
Windows, so `import rasterio` at module scope made the plugin fail to load
there entirely:

    ModuleNotFoundError: No module named 'rasterio'

These tests parse the load-path modules rather than importing them, so they run
without QGIS.
"""

import ast
import glob
import os

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Packages imported wholesale by the plugin window; every module in them is
# on the load path, including ones added later.
LOAD_PATH_PACKAGES = ("app", "common", "landslide", "eil")


def _package_modules():
    found = []
    for package in LOAD_PATH_PACKAGES:
        pattern = os.path.join(PLUGIN_DIR, package, "**", "*.py")
        found += [
            os.path.relpath(path, PLUGIN_DIR)
            for path in glob.glob(pattern, recursive=True)
        ]
    return sorted(found)


# Imported by classFactory, directly or transitively, inside QGIS's interpreter.
LOAD_PATH = _package_modules() + [
    "mainPlugin.py",
    "platform_utils.py",
    "dependency_panel.py",
    "raster_io.py",
    "landslide_mainwindow.py",
    "eil_mainwindow.py",
    "busy_overlay.py",
    "log_panel.py",
    "grass_addons.py",
    "grass_gate.py",
]

# Third-party packages QGIS does NOT guarantee on every platform.
# matplotlib and numpy were assumed safe because macOS QGIS 3.42 bundles them.
# A different macOS QGIS reported "No module named matplotlib" at classFactory,
# so neither is guaranteed either.
NOT_BUNDLED = {
    "rasterio", "geopandas", "shapely", "fiona", "pyproj",
    "tensorflow", "keras", "sklearn", "scipy", "pandas",
    "seaborn", "contextily", "torch",
    "matplotlib", "numpy",
}

# Direct Qt bindings: qgis.PyQt maps to whichever Qt this QGIS was built with.
DIRECT_QT = {"PyQt5", "PyQt6", "PySide2", "PySide6"}


def module_scope_imports(path):
    """Top-level import names only -- imports inside functions are deferred."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    names = set()
    for node in tree.body:                      # body only == module scope
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("filename", LOAD_PATH)
def test_no_unbundled_third_party_import_at_module_scope(filename):
    # Arrange
    path = os.path.join(PLUGIN_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present")

    # Act
    offenders = module_scope_imports(path) & NOT_BUNDLED

    # Assert
    assert not offenders, (
        f"{filename} imports {sorted(offenders)} at module scope. QGIS does not "
        "ship these on every platform, so the plugin will fail to load. Import "
        "inside the function that needs it, or use osgeo.gdal (see raster_io.py)."
    )


@pytest.mark.parametrize("filename", LOAD_PATH)
def test_qt_is_imported_through_qgis(filename):
    # Arrange
    path = os.path.join(PLUGIN_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present")

    # Act
    offenders = module_scope_imports(path) & DIRECT_QT

    # Assert
    assert not offenders, (
        f"{filename} imports {sorted(offenders)} directly. Use qgis.PyQt so the "
        "plugin works on both Qt5 and Qt6 builds of QGIS."
    )


def test_raster_io_uses_gdal_not_rasterio():
    # raster_io exists specifically to avoid the rasterio dependency.
    names = module_scope_imports(os.path.join(PLUGIN_DIR, "raster_io.py"))
    assert "osgeo" in names
    assert "rasterio" not in names


def test_subprocess_workers_may_still_use_rasterio():
    # These run inside the managed virtualenvs, where rasterio IS installed,
    # so they are deliberately exempt from the rule above.
    for worker in ("landslide_worker.py", "pinn_inference.py"):
        path = os.path.join(PLUGIN_DIR, worker)
        if os.path.exists(path):
            assert "rasterio" in module_scope_imports(path)


def _plugin_local_names():
    """Top-level modules and packages that live inside the plugin directory."""
    names = set()
    for entry in os.listdir(PLUGIN_DIR):
        full = os.path.join(PLUGIN_DIR, entry)
        if entry.endswith(".py") and entry != "__init__.py":
            names.add(entry[:-3])
        elif os.path.isfile(os.path.join(full, "__init__.py")):
            names.add(entry)
    return names


@pytest.mark.parametrize("filename", LOAD_PATH)
def test_plugin_modules_are_imported_relatively(filename):
    # Installed, the plugin is a package under QGIS's plugins directory, not on
    # sys.path, so `from log_panel import ...` fails at classFactory with
    # "No module named 'log_panel'" even though it works from a dev checkout.
    # Arrange
    path = os.path.join(PLUGIN_DIR, filename)
    if not os.path.exists(path):
        pytest.skip(f"{filename} not present")

    # Act
    offenders = module_scope_imports(path) & _plugin_local_names()

    # Assert
    assert not offenders, (
        f"{filename} imports plugin module(s) {sorted(offenders)} absolutely. "
        "Use a relative import (e.g. `from ..log_panel import ...`)."
    )
