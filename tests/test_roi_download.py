"""The ROI data download: manifest, resumable transfer, verify and install.

Runs against a local HTTP server that honours Range requests, so resuming is
exercised for real. Needs QGIS's PyQt bindings (skipped otherwise).
"""

import hashlib
import importlib
import json
import os
import sys
import threading
import types
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qgis.PyQt.QtNetwork", reason="needs QGIS's PyQt bindings")

from qgis.PyQt import QtCore, QtWidgets  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
roi_data = importlib.import_module("mlprep_plugin.common.roi_data")
roi_download = importlib.import_module("mlprep_plugin.app.roi_download")

WAIT_MS = 20000
ARCHIVE = "roi_data-test.zip"


class _Handler(BaseHTTPRequestHandler):
    """Serves the fixture's files, honouring single-range requests."""

    def do_GET(self):
        server = self.server
        server.ranges.append(self.headers.get("Range"))
        body = server.files.get(self.path.lstrip("/"))
        if body is None:
            self.send_error(404)
            return
        start = 0
        requested = self.headers.get("Range")
        if requested and server.honour_ranges:
            start = int(requested.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.files, httpd.ranges, httpd.honour_ranges = {}, [], True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def envs(tmp_path, monkeypatch):
    monkeypatch.setattr(roi_data, "BUNDLED_ROI_DIR", str(tmp_path / "bundled"))
    monkeypatch.setattr(pu, "ENVS_DIR", str(tmp_path / "envs"))
    return tmp_path


def publish(server, tmp_path, sha256=None):
    """Put an archive of valid boundary files and its manifest on the server."""
    archive_path = tmp_path / ARCHIVE
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in roi_data.required_files():
            # Incompressible-ish content so the archive spans several reads.
            archive.writestr(name, os.urandom(64 * 1024))
    body = archive_path.read_bytes()
    server.files[ARCHIVE] = body
    server.files["manifest.json"] = json.dumps({
        "version": "test",
        "archive": ARCHIVE,
        "sha256": sha256 or hashlib.sha256(body).hexdigest(),
        "size": len(body),
        "unpacked_size": 0,
    }).encode()
    return body


def manifest_url(server):
    host, port = server.server_address
    return f"http://{host}:{port}/manifest.json"


def run_download(url):
    """Run a RoiDownloader to completion; returns (ok, message, progress)."""
    downloader = roi_download.RoiDownloader(url)
    result, progress = {}, []
    loop = QtCore.QEventLoop()
    downloader.progress.connect(lambda done, total: progress.append((done, total)))
    downloader.finished.connect(lambda ok, message: (result.update(ok=ok, message=message), loop.quit()))
    QtCore.QTimer.singleShot(WAIT_MS, loop.quit)
    downloader.start()
    loop.exec_()
    assert result, "download did not finish in time"
    return result["ok"], result["message"], progress


class TestRoiDownloader:
    def test_downloads_verifies_and_installs_into_the_cache(self, qt_app, server, envs):
        # Arrange
        body = publish(server, envs)

        # Act
        ok, message, progress = run_download(manifest_url(server))

        # Assert
        assert ok, message
        assert roi_data.resolve_roi_dir() == roi_data.cache_dir()
        assert progress[-1] == (len(body), len(body))

    def test_resumes_a_partial_download(self, qt_app, server, envs):
        # Arrange -- half the archive is already on disk from an earlier attempt.
        body = publish(server, envs)
        manifest = roi_data.parse_manifest(server.files["manifest.json"])
        os.makedirs(pu.ENVS_DIR, exist_ok=True)
        half = len(body) // 2
        with open(roi_data.partial_download_path(manifest), "wb") as handle:
            handle.write(body[:half])

        # Act
        ok, message, _ = run_download(manifest_url(server))

        # Assert
        assert ok, message
        assert f"bytes={half}-" in server.ranges

    def test_restarts_when_the_server_ignores_ranges(self, qt_app, server, envs):
        # Arrange -- a stale partial file and a server that always sends it all.
        body = publish(server, envs)
        server.honour_ranges = False
        manifest = roi_data.parse_manifest(server.files["manifest.json"])
        os.makedirs(pu.ENVS_DIR, exist_ok=True)
        with open(roi_data.partial_download_path(manifest), "wb") as handle:
            handle.write(body[:100])

        # Act
        ok, message, _ = run_download(manifest_url(server))

        # Assert -- appending would have corrupted it; the checksum proves not.
        assert ok, message

    def test_corrupt_archive_fails_and_is_discarded(self, qt_app, server, envs):
        # Arrange
        publish(server, envs, sha256="0" * 64)
        manifest = roi_data.parse_manifest(server.files["manifest.json"])

        # Act
        ok, message, _ = run_download(manifest_url(server))

        # Assert -- a retry must start clean rather than resume bad bytes.
        assert not ok
        assert "checksum" in message
        assert not os.path.exists(roi_data.partial_download_path(manifest))
        assert roi_data.resolve_roi_dir() is None

    def test_missing_manifest_reports_the_url(self, qt_app, server, envs):
        # Act -- nothing published.
        ok, message, _ = run_download(manifest_url(server))

        # Assert
        assert not ok
        assert manifest_url(server) in message
