"""Where the admin-boundary data (roi_data/) lives, and installing it.

roi_data/ is ~1.4 GB, so releases built with `build_release.sh --no-roi` leave
it out. The plugin then downloads it once from the ML-PREP server into the
managed directory (~/.mlprep_envs/roi_data) before any tab that reads
boundaries is built. A release that still bundles it never touches the network.

The server publishes two files side by side (see scripts/package_roi_data.py):

    manifest.json            {"version", "archive", "sha256", "size", "unpacked_size"}
    roi_data-<version>.zip   every file of roi_data/, at the archive root

This module is Qt-free; app/roi_download.py does the network transfer.
"""

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from urllib.parse import urljoin

from .. import platform_utils as pu
from .paths import ROI_DIR as BUNDLED_ROI_DIR
from .roi import _BOUNDARY_FILES

# carsu_vm. Only reachable from its network (LAN/VPN); point it, or the
# environment variable below, at a public host before distributing widely.
DEFAULT_MANIFEST_URL = "http://192.168.128.142/mlprep/roi/manifest.json"
ENV_ROI_URL = "MLPREP_ROI_URL"

CACHE_DIRNAME = "roi_data"
# Written last, so a directory without it is an interrupted install.
READY_STAMP = ".mlprep_ready"

# Shapefile parts the plugin cannot read a boundary layer without.
_SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj")

_HASH_CHUNK = 1024 * 1024
_MB = 1024 ** 2
_GB = 1024 ** 3


class RoiDataError(RuntimeError):
    """The boundary data could not be fetched, verified or installed."""


@dataclass(frozen=True)
class RoiManifest:
    version: str
    archive: str
    sha256: str
    size: int
    unpacked_size: int = 0


def required_files():
    """Every file that must exist for each admin level to load."""
    return tuple(
        os.path.splitext(filename)[0] + ext
        for filename, *_ in _BOUNDARY_FILES.values()
        for ext in _SHAPEFILE_PARTS
    )


def cache_dir():
    """Where downloaded boundary data is installed."""
    return os.path.join(pu.ENVS_DIR, CACHE_DIRNAME)


def manifest_url():
    return os.environ.get(ENV_ROI_URL) or DEFAULT_MANIFEST_URL


def is_complete(directory):
    """True when every required boundary file exists and is non-empty."""
    return all(_has_file(directory, name) for name in required_files())


def _has_file(directory, name):
    path = os.path.join(directory, name)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def write_stamp(directory, version):
    with open(os.path.join(directory, READY_STAMP), "w", encoding="utf-8") as handle:
        handle.write(version)


def resolve_roi_dir():
    """The directory to read boundaries from, or None when a download is needed.

    Bundled data wins, so a full release never downloads anything.
    """
    if is_complete(BUNDLED_ROI_DIR):
        return BUNDLED_ROI_DIR
    cached = cache_dir()
    if os.path.isfile(os.path.join(cached, READY_STAMP)) and is_complete(cached):
        return cached
    return None


def parse_manifest(text):
    """Validated RoiManifest from the server's manifest.json text."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RoiDataError(f"The ROI manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RoiDataError("The ROI manifest must be a JSON object.")

    version, archive = data.get("version"), data.get("archive")
    sha256, size = data.get("sha256"), data.get("size")
    unpacked_size = data.get("unpacked_size", 0)

    problems = []
    if not isinstance(version, str) or not version:
        problems.append("version")
    # A bare filename: it is joined onto the manifest URL and the cache dir.
    if (
        not isinstance(archive, str)
        or not archive
        or os.path.basename(archive) != archive
        or archive in (".", "..")
    ):
        problems.append("archive")
    if not isinstance(sha256, str) or len(sha256) != 64 or not _is_hex(sha256):
        problems.append("sha256")
    if not isinstance(size, int) or size <= 0:
        problems.append("size")
    if not isinstance(unpacked_size, int) or unpacked_size < 0:
        problems.append("unpacked_size")
    if problems:
        raise RoiDataError(f"The ROI manifest has invalid fields: {', '.join(problems)}.")

    return RoiManifest(version, archive, sha256.lower(), size, unpacked_size)


def _is_hex(text):
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def archive_url(manifest_location, manifest):
    """The archive's URL: a sibling of manifest.json on the server."""
    return urljoin(manifest_location, manifest.archive)


def partial_download_path(manifest):
    """Where the archive is downloaded to; kept across attempts to resume."""
    return os.path.join(pu.ENVS_DIR, manifest.archive + ".part")


def verify_archive(path, manifest):
    """Raise RoiDataError unless `path` matches the manifest's size and sha256."""
    actual_size = os.path.getsize(path)
    if actual_size != manifest.size:
        raise RoiDataError(
            f"Downloaded ROI archive has the wrong size: {actual_size} bytes, "
            f"expected {manifest.size}."
        )
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    if digest.hexdigest() != manifest.sha256:
        raise RoiDataError(
            "Downloaded ROI archive failed its checksum; it is corrupted or "
            "was changed on the server."
        )


def install_archive(archive_path, version):
    """Extract `archive_path` into the cache and mark it ready. Returns the dir.

    Extraction goes to a staging directory that replaces the cache only once
    complete, so an interruption never leaves a directory that looks ready.
    """
    target = cache_dir()
    staging = target + ".partial"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)

    try:
        _extract_safely(archive_path, staging)
        missing = [name for name in required_files() if not _has_file(staging, name)]
        if missing:
            raise RoiDataError(
                "The ROI archive is missing boundary files: " + ", ".join(missing)
            )
        write_stamp(staging, version)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    return target


def _extract_safely(archive_path, destination):
    root = os.path.realpath(destination)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                resolved = os.path.realpath(os.path.join(root, member))
                if not resolved.startswith(root + os.sep):
                    raise RoiDataError(f"The ROI archive has an unsafe entry: {member}")
            archive.extractall(root)
    except zipfile.BadZipFile as exc:
        raise RoiDataError(f"The ROI archive is not a valid zip file: {exc}") from exc


def ensure_disk_space(directory, needed):
    """Raise RoiDataError when `directory`'s volume has under `needed` bytes free."""
    existing = directory
    while not os.path.isdir(existing) and os.path.dirname(existing) != existing:
        existing = os.path.dirname(existing)
    free = shutil.disk_usage(existing).free
    if free < needed:
        raise RoiDataError(
            f"Not enough free disk space in {directory}: {format_bytes(needed)} "
            f"needed, {format_bytes(free)} available."
        )


def format_bytes(size):
    if size >= _GB:
        return f"{size / _GB:.1f} GB"
    return f"{round(size / _MB)} MB"
