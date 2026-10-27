"""Creation of the plugin's managed virtualenvs.

`python -m venv` is not reliable with the interpreter QGIS ships. In particular
OSGeo4W QGIS on Windows frequently lacks a working `ensurepip`, so venv creation
fails at the "install pip" stage with nothing but:

    returned non-zero exit status 1

Two things are handled here that the plain call did not:

  * **Diagnostics.** Output is captured and included in the raised error, so a
    failure says what actually went wrong instead of just the exit code.
  * **Fallback.** When creation fails only because pip could not be
    bootstrapped, the environment is recreated with --without-pip and pip is
    installed from get-pip.py.

A half-created environment is also removed rather than left behind: the old code
tested for the *directory*, so an aborted attempt made every later run skip
creation and then fail against an interpreter that was never there.
"""

import os
import shutil
import subprocess

from . import platform_utils as pu

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Substrings that mean "the environment is fine, only pip bootstrapping failed".
_PIP_BOOTSTRAP_MARKERS = (
    "ensurepip",
    "returned non-zero exit status",
    "No module named pip",
    "pip is configured with locations",
)


class _Failed:
    """Stands in for a CompletedProcess when the command could not start."""

    returncode = 1

    def __init__(self, message):
        self.stdout = ""
        self.stderr = message


def _run(command, env, timeout=900):
    """Run a command, capturing output.

    Never raises: a missing executable or a timeout comes back as a failed
    result carrying the reason, so every caller reports one diagnostic error
    instead of leaking OSError or TimeoutExpired out of a worker thread.
    """
    try:
        return subprocess.run(
            command, env=env, capture_output=True, timeout=timeout,
            **pu.subprocess_kwargs()
        )
    except FileNotFoundError:
        return _Failed(f"Executable not found: {command[0]}")
    except subprocess.TimeoutExpired:
        return _Failed(f"Timed out after {timeout}s: {' '.join(map(str, command))}")
    except OSError as exc:
        return _Failed(f"Could not run {command[0]}: {exc}")


def _combined(result):
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def interpreter_works(python_exe):
    """True when `python_exe` exists and actually runs."""
    if not python_exe or not os.path.isfile(python_exe):
        return False
    try:
        probe = subprocess.run(
            [python_exe, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True, timeout=60, **pu.subprocess_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and probe.stdout.strip().startswith("3")


def has_pip(python_exe):
    try:
        probe = subprocess.run(
            [python_exe, "-m", "pip", "--version"],
            capture_output=True, timeout=120, **pu.subprocess_kwargs()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _bootstrap_pip(python_exe, env, log):
    """Install pip into an environment created with --without-pip."""
    log(f"   > Bootstrapping pip from {GET_PIP_URL}")

    target = os.path.join(os.path.dirname(os.path.dirname(python_exe)), "get-pip.py")
    fetch = _run([python_exe, "-c",
                  "import urllib.request, sys; "
                  f"urllib.request.urlretrieve({GET_PIP_URL!r}, sys.argv[1])",
                  target], env)
    if fetch.returncode != 0 or not os.path.isfile(target):
        raise RuntimeError(
            "Could not download get-pip.py. The machine may be offline or "
            f"behind a proxy.\n\n{_combined(fetch)}"
        )

    try:
        install = _run([python_exe, target, "--no-warn-script-location"], env)
        if install.returncode != 0:
            raise RuntimeError(f"get-pip.py failed.\n\n{_combined(install)}")
    finally:
        if os.path.isfile(target):
            os.remove(target)


def create_venv(base_python, venv_dir, env=None, log=None):
    """Create (or reuse) the virtualenv at `venv_dir`. Returns its interpreter.

    Raises RuntimeError carrying the underlying output when it cannot.
    """
    log = log or (lambda _message: None)
    env = env if env is not None else pu.tool_environment()

    name = os.path.basename(venv_dir)
    python_exe = pu.venv_python(name)

    version = pu.venv_version(name)
    too_old = version is not None and version < pu.MIN_PYTHON

    if interpreter_works(python_exe) and not too_old:
        log(f"   > Reusing existing environment: {venv_dir}")
        if not has_pip(python_exe):
            _bootstrap_pip(python_exe, env, log)
        return python_exe

    # Anything already there is broken, half-written or built on a Python
    # older than MIN_PYTHON; start clean.
    if os.path.exists(venv_dir):
        log(f"   > Removing incomplete or outdated environment: {venv_dir}")
        shutil.rmtree(venv_dir, ignore_errors=True)

    log(f"   > Creating environment: {venv_dir}")
    clear_provisioned(venv_dir)
    created = _run([base_python, "-m", "venv", venv_dir], env)

    if created.returncode != 0:
        output = _combined(created)
        if not _looks_like_pip_bootstrap_failure(output):
            raise RuntimeError(_creation_error(base_python, venv_dir, output))

        # The environment itself is fine; only pip bootstrapping failed.
        log("   > venv could not bootstrap pip; retrying without it.")
        shutil.rmtree(venv_dir, ignore_errors=True)
        retry = _run([base_python, "-m", "venv", "--without-pip", venv_dir], env)
        if retry.returncode != 0:
            raise RuntimeError(
                _creation_error(base_python, venv_dir, _combined(retry))
            )

    if not interpreter_works(python_exe):
        raise RuntimeError(
            _creation_error(
                base_python, venv_dir,
                f"venv reported success but {python_exe} is missing or will not run.",
            )
        )

    if not has_pip(python_exe):
        _bootstrap_pip(python_exe, env, log)

    return python_exe


def _looks_like_pip_bootstrap_failure(output):
    lowered = output.lower()
    return any(marker.lower() in lowered for marker in _PIP_BOOTSTRAP_MARKERS)


def _creation_error(base_python, venv_dir, output):
    return (
        f"Could not create the Python environment at:\n  {venv_dir}\n\n"
        f"Using interpreter:\n  {base_python}\n\n"
        f"{output or '(the command produced no output)'}\n\n"
        "Common causes on Windows:\n"
        "  - QGIS's Python has no working 'ensurepip'\n"
        "  - antivirus blocking the copy of python.exe\n"
        "  - no write access to your user profile\n\n"
        "To see the raw error, open the OSGeo4W Shell and run:\n"
        f'  "{base_python}" -m venv "{venv_dir}"'
    )


def mark_provisioned(venv_dir, requirements_path):
    """Record that this environment's requirements installed successfully."""
    stamp = os.path.join(venv_dir, pu.READY_STAMP)
    with open(stamp, "w", encoding="utf-8") as handle:
        handle.write(pu.requirements_fingerprint(requirements_path))


def clear_provisioned(venv_dir):
    stamp = os.path.join(venv_dir, pu.READY_STAMP)
    if os.path.isfile(stamp):
        os.remove(stamp)


def install_requirements(python_exe, requirements_path, env=None, log=None):
    """pip install -r, surfacing pip's own output on failure."""
    log = log or (lambda _message: None)
    env = env if env is not None else pu.tool_environment()

    if not os.path.isfile(requirements_path):
        raise RuntimeError(f"Requirements file not found:\n  {requirements_path}")

    log(f"   > Installing {os.path.basename(requirements_path)}")
    result = _run(
        [python_exe, "-m", "pip", "install", "--no-input",
         "-r", requirements_path], env, timeout=3600
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Installing {os.path.basename(requirements_path)} failed.\n\n"
            f"{_combined(result)}"
        )
