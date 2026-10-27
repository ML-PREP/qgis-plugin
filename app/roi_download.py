"""Downloading the admin-boundary data on first use.

Used only when the release was built with `--no-roi` and no cached copy exists
(see common/roi_data.py). The transfer goes through QGIS's network manager so
the user's QGIS proxy and SSL settings apply, streams to a `.part` file that
later attempts resume with an HTTP Range request, and is verified against the
manifest's sha256 before it is unpacked into ~/.mlprep_envs/roi_data.
"""

import os

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import platform_utils as pu
from ..common import roi_data

# A stalled connection (e.g. the server is off the current network) fails
# after this long instead of hanging the gate indefinitely.
TRANSFER_TIMEOUT_MS = 60_000
HTTP_PARTIAL_CONTENT = 206
DIALOG_WIDTH = 520
COLOR_ERROR = "#c0392b"


def _network_manager(parent):
    try:
        from qgis.core import QgsNetworkAccessManager
    except ImportError:
        return QNetworkAccessManager(parent)
    return QgsNetworkAccessManager.instance()


def _request(url, offset=0):
    request = QNetworkRequest(QUrl(url))
    if hasattr(QNetworkRequest, "FollowRedirectsAttribute"):
        request.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
    if hasattr(request, "setTransferTimeout"):
        request.setTransferTimeout(TRANSFER_TIMEOUT_MS)
    if offset:
        request.setRawHeader(b"Range", f"bytes={offset}-".encode())
    return request


class _InstallThread(QThread):
    """Hashing and unpacking ~1 GB would freeze the dialog on the GUI thread."""

    done = pyqtSignal(bool, str)

    def __init__(self, archive_path, manifest):
        super().__init__()
        self._archive_path = archive_path
        self._manifest = manifest

    def run(self):
        try:
            roi_data.verify_archive(self._archive_path, self._manifest)
        except roi_data.RoiDataError as exc:
            # Corrupt bytes must not be resumed from on the next attempt.
            _remove_quietly(self._archive_path)
            self.done.emit(False, str(exc))
            return
        try:
            roi_data.install_archive(self._archive_path, self._manifest.version)
        except (roi_data.RoiDataError, OSError) as exc:
            self.done.emit(False, f"Could not unpack the boundary data: {exc}")
            return
        _remove_quietly(self._archive_path)
        self.done.emit(True, roi_data.cache_dir())


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


class RoiDownloader(QObject):
    """Manifest -> archive (resumable) -> verify -> install, without blocking Qt."""

    status = pyqtSignal(str)
    # Byte counts exceed 2**31 for large archives, hence `object`.
    progress = pyqtSignal(object, object)
    finished = pyqtSignal(bool, str)

    def __init__(self, manifest_url, parent=None):
        super().__init__(parent)
        self._manifest_url = manifest_url
        self._network = _network_manager(self)
        self._reply = None
        self._file = None
        self._offset = 0
        self._manifest = None
        self._installer = None
        self._aborted = False

    # ------------------------------------------------------------ control

    def start(self):
        self._aborted = False
        self.status.emit("Contacting the ML-PREP data server…")
        self._reply = self._network.get(_request(self._manifest_url))
        self._reply.finished.connect(self._on_manifest_finished)

    def abort(self):
        """Stop the transfer; the partial file is kept so a retry resumes."""
        self._aborted = True
        if self._reply is not None:
            self._reply.abort()
        self._close_file()

    # ------------------------------------------------------------ manifest

    def _on_manifest_finished(self):
        reply, self._reply = self._reply, None
        reply.deleteLater()
        if self._aborted:
            return
        if reply.error() != QNetworkReply.NoError:
            self._fail(self._unreachable_message(self._manifest_url, reply.errorString()))
            return
        try:
            self._manifest = roi_data.parse_manifest(bytes(reply.readAll()).decode("utf-8"))
        except (roi_data.RoiDataError, UnicodeDecodeError) as exc:
            self._fail(str(exc))
            return
        self._start_archive()

    # ------------------------------------------------------------ archive

    def _start_archive(self):
        manifest = self._manifest
        part = roi_data.partial_download_path(manifest)
        os.makedirs(pu.ENVS_DIR, exist_ok=True)

        self._offset = os.path.getsize(part) if os.path.isfile(part) else 0
        if self._offset > manifest.size:
            _remove_quietly(part)
            self._offset = 0
        try:
            roi_data.ensure_disk_space(
                pu.ENVS_DIR, manifest.size - self._offset + manifest.unpacked_size
            )
        except roi_data.RoiDataError as exc:
            self._fail(str(exc))
            return

        if self._offset == manifest.size:
            self._install(part)
            return

        self._file = open(part, "ab")
        verb = "Resuming" if self._offset else "Downloading"
        self.status.emit(
            f"{verb} administrative boundary data ({roi_data.format_bytes(manifest.size)})…"
        )
        url = roi_data.archive_url(self._manifest_url, manifest)
        self._reply = self._network.get(_request(url, self._offset))
        self._reply.metaDataChanged.connect(self._on_archive_headers)
        self._reply.readyRead.connect(self._on_archive_data)
        self._reply.downloadProgress.connect(self._on_archive_progress)
        self._reply.finished.connect(self._on_archive_finished)

    def _on_archive_headers(self):
        status = self._reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if self._offset and status is not None and status != HTTP_PARTIAL_CONTENT:
            # The server ignored the Range header and is sending everything:
            # appending would corrupt the file, so start it over.
            self._file.seek(0)
            self._file.truncate()
            self._offset = 0

    def _on_archive_data(self):
        if self._file is not None:
            self._file.write(bytes(self._reply.readAll()))

    def _on_archive_progress(self, received, _total):
        self.progress.emit(self._offset + received, self._manifest.size)

    def _on_archive_finished(self):
        reply, self._reply = self._reply, None
        reply.deleteLater()
        if self._aborted:
            return
        if reply.error() == QNetworkReply.NoError:
            # Anything still buffered after the last readyRead.
            self._file.write(bytes(reply.readAll()))
        self._close_file()
        if reply.error() != QNetworkReply.NoError:
            url = roi_data.archive_url(self._manifest_url, self._manifest)
            self._fail(self._unreachable_message(url, reply.errorString()))
            return
        self._install(roi_data.partial_download_path(self._manifest))

    # ------------------------------------------------------------ install

    def _install(self, archive_path):
        self.status.emit("Verifying and unpacking the boundary data…")
        self._installer = _InstallThread(archive_path, self._manifest)
        self._installer.done.connect(self._on_installed)
        self._installer.start()

    def _on_installed(self, ok, message):
        self._installer.wait()
        self._installer = None
        if ok:
            self.finished.emit(True, message)
        else:
            self._fail(message)

    # ------------------------------------------------------------ helpers

    def _close_file(self):
        if self._file is not None:
            self._file.close()
            self._file = None

    def _fail(self, message):
        self._close_file()
        self.finished.emit(False, message)

    @staticmethod
    def _unreachable_message(url, reason):
        return (
            f"Could not download from:\n  {url}\n\nReason: {reason}\n\n"
            "Check that this computer can reach the ML-PREP data server "
            "(it may require the office network or VPN), then press Retry."
        )


class RoiDownloadDialog(QtWidgets.QDialog):
    """Modal gate: accepted only once the boundary data is installed.

    Starts downloading as soon as it opens. Cancelling closes the plugin, since
    every tab reads these boundaries; a later attempt resumes the transfer.
    """

    def __init__(self, manifest_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloading Boundary Data")
        self.setModal(True)
        # A fixed width lets the wrapped labels report a definite height.
        self.setFixedWidth(DIALOG_WIDTH)

        self._downloader = RoiDownloader(manifest_url, self)
        self._downloader.status.connect(self._show_status)
        self._downloader.progress.connect(self._show_progress)
        self._downloader.finished.connect(self._on_finished)

        self._build_ui(manifest_url)
        self._fit_height()
        QTimer.singleShot(0, self._start)

    def _build_ui(self, manifest_url):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            "<b>The Philippine administrative boundaries are required.</b><br><br>"
            "This copy of the plugin was installed without them, so they are "
            "downloaded once and kept in:<br>"
            f"<code>{roi_data.cache_dir()}</code><br><br>"
            "The plugin opens when the download finishes. If it is interrupted, "
            "the next attempt continues where it stopped."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._status)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        self._source = QtWidgets.QLabel(f"Source: {manifest_url}")
        self._source.setStyleSheet("color: #666666;")
        self._source.setWordWrap(True)
        layout.addWidget(self._source)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self._retry = QtWidgets.QPushButton("Retry")
        self._retry.clicked.connect(self._start)
        self._retry.setVisible(False)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self._retry)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _start(self):
        self._retry.setVisible(False)
        self._status.setStyleSheet("")
        self._bar.setRange(0, 0)
        self._downloader.start()

    def _show_status(self, text):
        self._status.setText(text)
        self._fit_height()

    def _fit_height(self):
        """Resize to the wrapped text, which grows from a status to an error."""
        self.resize(DIALOG_WIDTH, self.layout().heightForWidth(DIALOG_WIDTH))

    def _show_progress(self, done, total):
        # QProgressBar takes an int; per-mille avoids overflow on GB sizes.
        self._bar.setRange(0, 1000)
        self._bar.setValue(int(done * 1000 / total) if total else 0)
        self._bar.setFormat(
            f"{roi_data.format_bytes(done)} of {roi_data.format_bytes(total)}  (%p%)"
        )

    def _on_finished(self, ok, message):
        if ok:
            self.accept()
            return
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._bar.setFormat("")
        self._status.setStyleSheet(f"color: {COLOR_ERROR};")
        self._retry.setVisible(True)
        self._show_status(message)

    def reject(self):
        self._downloader.abort()
        super().reject()
