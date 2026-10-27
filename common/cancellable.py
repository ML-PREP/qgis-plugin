"""Cancellation support shared by the subprocess-driving worker threads."""

import os
import signal
import subprocess
import threading

IS_WINDOWS = os.name == "nt"

# How long a launcher gets to shut its children down after SIGTERM before
# everything left in its tree is killed.
STOP_GRACE_SECONDS = 5.0


def descendants(pid):
    """Pids of every process below `pid` (children, grandchildren, ...). POSIX."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True
    ).stdout
    children = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 2:
            child, parent = int(fields[0]), int(fields[1])
            children.setdefault(parent, []).append(child)

    found, pending = [], [pid]
    while pending:
        for child in children.get(pending.pop(), []):
            found.append(child)
            pending.append(child)
    return found


def _kill_quietly(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_windows_tree(process):
    # terminate() would end mpiexec.exe alone; /T takes its children with it.
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def stop_process_tree(process, grace_seconds=STOP_GRACE_SECONDS):
    """Stop `process` and everything it started.

    SIGTERM goes to the launcher first: Open MPI's mpiexec passes it on to its
    ranks and exits cleanly. Killing the launcher outright would orphan the
    ranks, which live in their own process groups, so the tree is recorded
    before any signal and whatever outlives the grace period is killed.
    """
    if process.poll() is not None:
        return
    if IS_WINDOWS:
        _stop_windows_tree(process)
        return

    tree = descendants(process.pid)
    try:
        process.terminate()
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        pass
    for pid in [process.pid, *tree]:
        _kill_quietly(pid)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def _stop_in_background(process):
    # The grace period must not freeze the QGIS window that clicked Cancel.
    threading.Thread(target=stop_process_tree, args=(process,), daemon=True).start()


class CancellableProcessMixin(object):
    """Gives a worker thread a subprocess that the UI's Cancel button can stop.

    Each worker records the child process it is currently waiting on; `cancel()`
    stops that process's whole tree and raises `_cancelled`, so the aborted
    stage returns quietly instead of reporting the kill as a crash.
    """

    def _init_cancellation(self):
        self._process = None
        self._cancelled = False

    @property
    def is_cancelled(self):
        return getattr(self, "_cancelled", False)

    def _track(self, process):
        self._process = process
        # Cancel may land between a check and the launch of the next tool.
        if self.is_cancelled:
            _stop_in_background(process)
        return process

    def cancel(self):
        self._cancelled = True
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        _stop_in_background(process)
