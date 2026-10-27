"""common/cancellable.py: Cancel must stop a tool's whole process tree.

MPI launchers are the reason: Open MPI gives every rank its own process group,
and killing only the launcher (SIGKILL) leaves the ranks running under PID 1.
"""

import importlib
import os
import shutil
import subprocess
import sys
import textwrap
import time
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

cancellable = importlib.import_module("mlprep_plugin.common.cancellable")

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX process tree")

# A launcher that ignores SIGTERM and starts a child in its own session, the
# way Open MPI's prterun starts ranks; prints the child's pid.
STUBBORN_LAUNCHER = textwrap.dedent("""
    import signal, subprocess, sys, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    print(child.pid, flush=True)
    time.sleep(60)
    """)


def is_alive(pid):
    """True for a live process; zombies (exited, not yet reaped) count as dead."""
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(out) and not out.startswith("Z")


def wait_until_dead(pids, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(is_alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return False


def start_stubborn_launcher():
    launcher = subprocess.Popen(
        [sys.executable, "-c", STUBBORN_LAUNCHER], stdout=subprocess.PIPE, text=True
    )
    child_pid = int(launcher.stdout.readline())
    return launcher, child_pid


class Worker(cancellable.CancellableProcessMixin):
    def __init__(self):
        self._init_cancellation()


@posix_only
class TestDescendants:
    def test_finds_children_and_grandchildren(self):
        # Arrange
        launcher, child_pid = start_stubborn_launcher()
        try:
            # Act
            found = cancellable.descendants(launcher.pid)

            # Assert
            assert child_pid in found
        finally:
            cancellable.stop_process_tree(launcher, grace_seconds=0)

    def test_process_without_children_has_none(self):
        process = subprocess.Popen(["sleep", "60"])
        try:
            assert cancellable.descendants(process.pid) == []
        finally:
            process.kill()
            process.wait()


@posix_only
class TestStopProcessTree:
    def test_kills_a_launcher_that_ignores_sigterm_and_its_detached_child(self):
        # Arrange
        launcher, child_pid = start_stubborn_launcher()

        # Act
        cancellable.stop_process_tree(launcher, grace_seconds=0.5)

        # Assert
        assert launcher.wait(timeout=5) is not None
        assert wait_until_dead([child_pid])

    def test_already_finished_process_is_a_no_op(self):
        process = subprocess.Popen(["true"])
        process.wait()
        cancellable.stop_process_tree(process, grace_seconds=0)

    @pytest.mark.skipif(shutil.which("mpiexec") is None, reason="needs mpiexec")
    def test_stops_every_mpi_rank(self):
        # Arrange
        launcher = subprocess.Popen(
            ["mpiexec", "-n", "2", "sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while len(cancellable.descendants(launcher.pid)) < 2:
            assert time.monotonic() < deadline, "MPI ranks never started"
            time.sleep(0.2)
        tree = cancellable.descendants(launcher.pid)

        # Act
        cancellable.stop_process_tree(launcher, grace_seconds=3)

        # Assert
        assert wait_until_dead(tree)


@posix_only
class TestCancel:
    def test_cancel_stops_the_tracked_tree_in_the_background(self):
        # Arrange
        worker = Worker()
        launcher, child_pid = start_stubborn_launcher()
        worker._track(launcher)

        # Act
        worker.cancel()

        # Assert
        assert worker.is_cancelled
        assert wait_until_dead([launcher.pid, child_pid], timeout=15)
        launcher.wait(timeout=5)

    def test_process_tracked_after_cancel_is_stopped_at_once(self):
        # Arrange: Cancel lands before the next tool has been launched
        worker = Worker()
        worker.cancel()
        launcher, child_pid = start_stubborn_launcher()

        # Act
        worker._track(launcher)

        # Assert
        assert wait_until_dead([launcher.pid, child_pid], timeout=15)
        launcher.wait(timeout=5)
