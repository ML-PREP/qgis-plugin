"""Guard the worker modules against module-scope imports that only exist on Unix.

`from sys import setdlopenflags` was an unused import in py_files/GallenModel.py.
sys.setdlopenflags exists on Linux and macOS only, so every AI prediction on
Windows failed as soon as the model module loaded:

    ImportError: cannot import name 'setdlopenflags' from 'sys'

The modules need TensorFlow to import for real, which CI does not install, so
these tests parse them instead. Imports inside a function or an `if`/`try`
block are left alone: those can be guarded by a platform check.
"""

import ast
import glob
import os

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules the prediction and pipeline workers import on every platform.
WORKER_PATTERNS = ("*.py", "py_files/*.py", "pinn_grid/**/*.py", "common/**/*.py")

UNIX_ONLY_SYS = {"setdlopenflags", "getdlopenflags", "abiflags"}
UNIX_ONLY_MODULES = {"fcntl", "termios", "tty", "pty", "pwd", "grp", "resource", "posix"}


def _worker_modules():
    found = set()
    for pattern in WORKER_PATTERNS:
        found.update(glob.glob(os.path.join(PLUGIN_DIR, pattern), recursive=True))
    return sorted(os.path.relpath(path, PLUGIN_DIR) for path in found)


def _unix_only_imports(tree):
    """Module-scope imports that raise on Windows, as readable strings."""
    bad = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "sys":
            bad += [f"from sys import {a.name}" for a in node.names if a.name in UNIX_ONLY_SYS]
        elif isinstance(node, ast.ImportFrom) and node.module in UNIX_ONLY_MODULES:
            bad.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            bad += [f"import {a.name}" for a in node.names if a.name in UNIX_ONLY_MODULES]
    return bad


@pytest.mark.parametrize("module", _worker_modules())
def test_no_unix_only_import_at_module_scope(module):
    with open(os.path.join(PLUGIN_DIR, module), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=module)

    assert _unix_only_imports(tree) == [], f"{module} cannot be imported on Windows"


def test_detects_the_original_failure():
    tree = ast.parse("from sys import setdlopenflags\nimport os\n")

    assert _unix_only_imports(tree) == ["from sys import setdlopenflags"]


def test_ignores_guarded_imports():
    tree = ast.parse("import sys\nif sys.platform != 'win32':\n    import fcntl\n")

    assert _unix_only_imports(tree) == []
