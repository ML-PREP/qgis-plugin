"""Cancellation support shared by the subprocess-driving worker threads."""


class CancellableProcessMixin(object):
    """Gives a worker thread a subprocess that the UI's Cancel button can kill.

    Each worker records the child process it is currently waiting on; `cancel()`
    terminates it and raises `_cancelled`, so the aborted stage returns quietly
    instead of reporting the kill as a crash.
    """

    def _init_cancellation(self):
        self._process = None
        self._cancelled = False

    @property
    def is_cancelled(self):
        return getattr(self, "_cancelled", False)

    def _track(self, process):
        self._process = process
        return process

    def cancel(self):
        self._cancelled = True
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            pass
