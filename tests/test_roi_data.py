"""Locating, verifying and installing the admin-boundary data (Qt-free).

Releases built with `build_release.sh --no-roi` ship without roi_data/; the
plugin then downloads it once into ~/.mlprep_envs/roi_data before any tab that
reads boundaries is built.
"""

import hashlib
import importlib
import json
import os
import sys
import types
import zipfile

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pu = importlib.import_module("mlprep_plugin.platform_utils")
roi = importlib.import_module("mlprep_plugin.common.roi")
roi_data = importlib.import_module("mlprep_plugin.common.roi_data")


def write_boundaries(directory):
    """Every file roi_data.required_files() demands, with dummy content."""
    os.makedirs(directory, exist_ok=True)
    for name in roi_data.required_files():
        with open(os.path.join(directory, name), "wb") as handle:
            handle.write(b"x")


def make_archive(path, source_dir):
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(os.listdir(source_dir)):
            archive.write(os.path.join(source_dir, name), name)
    return path


def manifest_for(archive_path, version="20231106"):
    with open(archive_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return roi_data.RoiManifest(
        version=version,
        archive=os.path.basename(archive_path),
        sha256=digest,
        size=os.path.getsize(archive_path),
    )


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Isolated bundled and cache locations."""
    bundled = tmp_path / "plugin" / "roi_data"
    envs = tmp_path / "envs"
    monkeypatch.setattr(roi_data, "BUNDLED_ROI_DIR", str(bundled))
    monkeypatch.setattr(pu, "ENVS_DIR", str(envs))
    return types.SimpleNamespace(bundled=bundled, cache=envs / "roi_data", root=tmp_path)


class TestRequiredFiles:
    def test_covers_every_admin_level_with_its_sidecars(self):
        # Act
        names = roi_data.required_files()

        # Assert
        for filename, *_ in roi._BOUNDARY_FILES.values():
            stem = os.path.splitext(filename)[0]
            for ext in (".shp", ".shx", ".dbf", ".prj"):
                assert stem + ext in names


class TestResolveRoiDir:
    def test_prefers_data_bundled_with_the_plugin(self, dirs):
        # Arrange
        write_boundaries(dirs.bundled)
        write_boundaries(dirs.cache)
        roi_data.write_stamp(str(dirs.cache), "20231106")

        # Act / Assert
        assert roi_data.resolve_roi_dir() == str(dirs.bundled)

    def test_uses_the_cache_when_the_release_omits_the_data(self, dirs):
        # Arrange -- a --no-roi build: no bundled roi_data/ at all.
        write_boundaries(dirs.cache)
        roi_data.write_stamp(str(dirs.cache), "20231106")

        # Act / Assert
        assert roi_data.resolve_roi_dir() == str(dirs.cache)

    def test_returns_none_when_a_download_is_needed(self, dirs):
        assert roi_data.resolve_roi_dir() is None

    def test_incomplete_bundled_data_is_ignored(self, dirs):
        # Arrange -- one level missing its .dbf.
        write_boundaries(dirs.bundled)
        os.remove(dirs.bundled / roi_data.required_files()[2])

        # Act / Assert
        assert roi_data.resolve_roi_dir() is None

    def test_cache_without_a_stamp_is_not_trusted(self, dirs):
        # Arrange -- files present, but the install never finished.
        write_boundaries(dirs.cache)

        # Act / Assert
        assert roi_data.resolve_roi_dir() is None


class TestParseManifest:
    def test_reads_a_valid_manifest(self):
        # Arrange
        text = json.dumps({
            "version": "20231106", "archive": "roi_data-20231106.zip",
            "sha256": "a" * 64, "size": 123,
        })

        # Act
        manifest = roi_data.parse_manifest(text)

        # Assert
        assert manifest == roi_data.RoiManifest("20231106", "roi_data-20231106.zip", "a" * 64, 123)

    @pytest.mark.parametrize("bad", [
        "not json",
        json.dumps({"version": "1", "archive": "a.zip", "size": 1}),
        json.dumps({"version": "1", "archive": "a.zip", "sha256": "xyz", "size": 1}),
        json.dumps({"version": "1", "archive": "a.zip", "sha256": "a" * 64, "size": 0}),
        json.dumps({"version": "1", "archive": "../evil.zip", "sha256": "a" * 64, "size": 1}),
        json.dumps({"version": "1", "archive": "sub/a.zip", "sha256": "a" * 64, "size": 1}),
    ])
    def test_rejects_malformed_or_unsafe_manifests(self, bad):
        with pytest.raises(roi_data.RoiDataError):
            roi_data.parse_manifest(bad)

    def test_archive_url_is_resolved_next_to_the_manifest(self):
        # Arrange
        manifest = roi_data.RoiManifest("1", "roi_data-1.zip", "a" * 64, 1)

        # Act
        url = roi_data.archive_url("http://host/mlprep/roi/manifest.json", manifest)

        # Assert
        assert url == "http://host/mlprep/roi/roi_data-1.zip"


class TestManifestUrl:
    def test_environment_variable_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv(roi_data.ENV_ROI_URL, "http://localhost:9/manifest.json")
        assert roi_data.manifest_url() == "http://localhost:9/manifest.json"

    def test_defaults_to_the_configured_server(self, monkeypatch):
        monkeypatch.delenv(roi_data.ENV_ROI_URL, raising=False)
        assert roi_data.manifest_url() == roi_data.DEFAULT_MANIFEST_URL


class TestVerifyArchive:
    def test_accepts_a_matching_archive(self, tmp_path):
        # Arrange
        source = tmp_path / "src"
        write_boundaries(source)
        archive = make_archive(tmp_path / "a.zip", source)

        # Act / Assert -- no exception.
        roi_data.verify_archive(str(archive), manifest_for(archive))

    def test_rejects_a_truncated_download(self, tmp_path):
        # Arrange
        source = tmp_path / "src"
        write_boundaries(source)
        archive = make_archive(tmp_path / "a.zip", source)
        manifest = manifest_for(archive)
        with open(archive, "r+b") as handle:
            handle.truncate(manifest.size - 1)

        # Act / Assert
        with pytest.raises(roi_data.RoiDataError, match="size"):
            roi_data.verify_archive(str(archive), manifest)

    def test_rejects_corrupted_content(self, tmp_path):
        # Arrange
        source = tmp_path / "src"
        write_boundaries(source)
        archive = make_archive(tmp_path / "a.zip", source)
        manifest = manifest_for(archive)
        with open(archive, "r+b") as handle:
            handle.seek(10)
            handle.write(b"\xff")

        # Act / Assert
        with pytest.raises(roi_data.RoiDataError, match="checksum"):
            roi_data.verify_archive(str(archive), manifest)


class TestInstallArchive:
    def test_installs_into_the_cache_and_marks_it_ready(self, dirs):
        # Arrange
        source = dirs.root / "src"
        write_boundaries(source)
        archive = make_archive(dirs.root / "a.zip", source)

        # Act
        installed = roi_data.install_archive(str(archive), "20231106")

        # Assert
        assert installed == str(dirs.cache)
        assert roi_data.resolve_roi_dir() == str(dirs.cache)

    def test_replaces_a_previous_partial_install(self, dirs):
        # Arrange -- leftovers from an interrupted extraction.
        write_boundaries(dirs.cache)
        (dirs.cache / "stale.txt").write_text("old")
        source = dirs.root / "src"
        write_boundaries(source)
        archive = make_archive(dirs.root / "a.zip", source)

        # Act
        roi_data.install_archive(str(archive), "20231106")

        # Assert
        assert not (dirs.cache / "stale.txt").exists()

    def test_rejects_an_archive_missing_boundary_files(self, dirs):
        # Arrange
        source = dirs.root / "src"
        write_boundaries(source)
        os.remove(source / roi_data.required_files()[0])
        archive = make_archive(dirs.root / "a.zip", source)

        # Act / Assert -- nothing half-installed is left looking ready.
        with pytest.raises(roi_data.RoiDataError, match="missing"):
            roi_data.install_archive(str(archive), "20231106")
        assert roi_data.resolve_roi_dir() is None

    def test_refuses_entries_that_escape_the_target(self, dirs):
        # Arrange
        archive = dirs.root / "evil.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../outside.txt", "x")

        # Act / Assert
        with pytest.raises(roi_data.RoiDataError, match="unsafe"):
            roi_data.install_archive(str(archive), "20231106")
        assert not (dirs.root / "envs" / "outside.txt").exists()


class TestDiskSpace:
    def test_raises_with_sizes_when_space_is_short(self, tmp_path, monkeypatch):
        # Arrange
        usage = types.SimpleNamespace(total=0, used=0, free=100 * 1024 ** 2)
        monkeypatch.setattr(roi_data.shutil, "disk_usage", lambda path: usage)

        # Act / Assert
        with pytest.raises(roi_data.RoiDataError, match="100 MB"):
            roi_data.ensure_disk_space(str(tmp_path), 1024 ** 3)


class TestFormatBytes:
    @pytest.mark.parametrize("size,text", [
        (0, "0 MB"), (5 * 1024 ** 2, "5 MB"), (int(1.5 * 1024 ** 3), "1.5 GB"),
    ])
    def test_formats_download_sizes(self, size, text):
        assert roi_data.format_bytes(size) == text
