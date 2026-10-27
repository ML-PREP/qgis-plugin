"""Every third-party module a worker imports must be declared in its requirements file.

Workers run in ~/.mlprep_envs/<name>, provisioned only from the matching
requirements_*.txt.  QGIS's own site-packages are NOT on their path, so an
undeclared import is a hard ModuleNotFoundError at run time -- and only in the
phase that imports it, which can be many minutes into a run.

This bit us with `fiona`: the Go polygonize step imported it, no requirements
file declared it, and geopandas 1.x no longer pulls it in transitively.
"""

import ast
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_GO = os.path.join(REPO, "ml-prep-scripts", "main.go")

# import name -> distribution name on PyPI
DISTRIBUTION = {
    "sklearn": "scikit-learn",
    "imblearn": "imbalanced-learn",
    "keras_tuner": "keras-tuner",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
}

# Modules supplied by the plugin itself or by the interpreter, not by pip.
# "rasters" is depzone/rasters.py, imported by its sibling prep.py.
LOCAL_MODULES = {"py_files", "MLPREP_Plugin_v3_Final", "rasters"}


def third_party(names):
    return {
        n for n in names
        if n not in sys.stdlib_module_names and n not in LOCAL_MODULES
    }


def declared(requirements_name):
    """Distribution names declared in a requirements file, normalised."""
    path = os.path.join(REPO, requirements_name)
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    names = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[ ]", line, 1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def imports_of_source(source, filename="<embedded>"):
    """Top-level package names imported anywhere in a source string."""
    tree = ast.parse(source, filename=filename)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def embedded_go_scripts():
    """The Python scripts main.go writes to a temp file and runs.

    Returns {go_var_name: source}.
    """
    with open(MAIN_GO, encoding="utf-8") as handle:
        go = handle.read()
    found = dict(re.findall(r"var (\w*[Ss]cript) = `(.*?)`", go, re.S))
    assert found, "no embedded Python scripts found in main.go -- parser is stale"
    return found


def assert_covered(modules, requirements_name, origin):
    have = declared(requirements_name)
    missing = sorted(
        DISTRIBUTION.get(m, m) for m in modules
        if DISTRIBUTION.get(m, m).lower().replace("_", "-") not in have
    )
    assert not missing, (
        f"{origin} imports {missing}, not declared in {requirements_name}. "
        f"Transitive availability does not count -- declare it explicitly."
    )


# The Go engine is launched with the EIL venv's interpreter
# (merged_wrapper.py: GoParallelZonalWorker(..., self.eil_venv_python, ...)).
@pytest.mark.parametrize("var_name", sorted(embedded_go_scripts()))
def test_go_embedded_script_imports_are_declared(var_name):
    source = embedded_go_scripts()[var_name]
    modules = third_party(imports_of_source(source, var_name))
    assert_covered(modules, "requirements_eil.txt", f"main.go:{var_name}")


@pytest.mark.parametrize(
    "script,requirements",
    [
        ("landslide_worker.py", "requirements_ls.txt"),
        ("pinn_inference.py", "requirements_eil.txt"),
        ("headless_model.py", "requirements_eil.txt"),
        # Depositional-zone stage: every script the deposition worker runs.
        ("depzone/prep.py", "requirements_eil.txt"),
        ("depzone/rasters.py", "requirements_eil.txt"),
        ("depzone/weiss_tpi_landform.py", "requirements_eil.txt"),
        (
            "ml_dfi_handoff/steps/step0_baseline_alpha_angle/"
            "step0_baseline_alpha_angle.py",
            "requirements_eil.txt",
        ),
        (
            "ml_dfi_handoff/steps/step3_train_ml_dfi_model/"
            "apply_ml_dfi_model_to_rasters.py",
            "requirements_eil.txt",
        ),
        (
            "ml_dfi_handoff/steps/fos_source_refinement/fos_source_refinement.py",
            "requirements_eil.txt",
        ),
        (
            "ml_dfi_handoff/steps/step5_cleanup_only/step5_cleanup_only.py",
            "requirements_eil.txt",
        ),
    ],
)
def test_standalone_worker_imports_are_declared(script, requirements):
    path = os.path.join(REPO, script)
    with open(path, encoding="utf-8-sig") as handle:
        source = handle.read()
    modules = third_party(imports_of_source(source, script))
    assert_covered(modules, requirements, script)


def test_ml_dfi_model_library_is_declared():
    """The pickled ML-DFI model needs its library at unpickle time.

    No script imports xgboost by name -- joblib.load pulls it in -- so the
    import scan above cannot see it.
    """
    import json

    metadata = os.path.join(REPO, "model_dfi", "model_metadata.json")
    with open(metadata, encoding="utf-8") as f:
        model_type = json.load(f)["model_type"]
    assert_covered({model_type}, "requirements_eil.txt", "model_dfi/trained_ml_dfi_model.pkl")


def test_no_worker_imports_fiona():
    """fiona is not in the dependency graph any more.

    geopandas 1.x reads/writes via pyogrio and rasterstats 0.21 dropped fiona to
    an optional extra, so nothing installs it transitively.  Anything importing
    it is a latent ModuleNotFoundError.
    """
    offenders = []
    for name, source in embedded_go_scripts().items():
        if "fiona" in imports_of_source(source, name):
            offenders.append(f"main.go:{name}")
    for script in ("landslide_worker.py", "pinn_inference.py", "headless_model.py"):
        with open(os.path.join(REPO, script), encoding="utf-8") as handle:
            if "fiona" in imports_of_source(handle.read(), script):
                offenders.append(script)
    assert not offenders, f"fiona imported by {offenders}; use geopandas/pyogrio instead"
