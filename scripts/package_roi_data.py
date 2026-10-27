#!/usr/bin/env python3
"""Package roi_data/ for the plugin's first-run download.

Releases built with `build_release.sh --no-roi` download the boundary data
instead of shipping it (see common/roi_data.py). This writes the two files the
server must publish side by side:

    <out>/roi_data-<version>.zip
    <out>/manifest.json

    python3 scripts/package_roi_data.py --version 20231106
    python3 scripts/package_roi_data.py --version 20231106 --source roi_data --out dist/roi

Then copy both files to the directory served at the plugin's manifest URL
(roi_data.DEFAULT_MANIFEST_URL), e.g. with rsync. Publishing a new version means
uploading the new zip first and replacing manifest.json last, so a client never
sees a manifest whose archive is not there yet.
"""

import argparse
import dataclasses
import hashlib
import importlib
import json
import os
import sys
import types
import zipfile

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "mlprep_plugin"
HASH_CHUNK = 1024 * 1024


def plugin_module(name):
    """Import `name` from the plugin without running its QGIS-only __init__."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [PLUGIN_DIR]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")


roi_data = plugin_module("common.roi_data")


def _payload(source):
    """Files to ship: everything in `source` except hidden OS clutter."""
    return sorted(
        name for name in os.listdir(source)
        if not name.startswith(".") and os.path.isfile(os.path.join(source, name))
    )


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(source, out_dir, version):
    """Write the archive and manifest; returns the RoiManifest written."""
    if not roi_data.is_complete(source):
        missing = [
            name for name in roi_data.required_files()
            if not os.path.isfile(os.path.join(source, name))
        ]
        raise SystemExit(f"{source} is missing boundary files: {', '.join(missing)}")

    os.makedirs(out_dir, exist_ok=True)
    names = _payload(source)
    archive_name = f"roi_data-{version}.zip"
    archive_path = os.path.join(out_dir, archive_name)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for name in names:
            print(f"  adding {name}", flush=True)
            archive.write(os.path.join(source, name), name)

    manifest = roi_data.RoiManifest(
        version=version,
        archive=archive_name,
        sha256=_sha256(archive_path),
        size=os.path.getsize(archive_path),
        unpacked_size=sum(os.path.getsize(os.path.join(source, n)) for n in names),
    )
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(dataclasses.asdict(manifest), handle, indent=2)
        handle.write("\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True, help="data version, e.g. 20231106")
    parser.add_argument("--source", default=os.path.join(PLUGIN_DIR, "roi_data"))
    parser.add_argument("--out", default=os.path.join(PLUGIN_DIR, "dist", "roi"))
    args = parser.parse_args(argv)

    manifest = package(args.source, args.out, args.version)
    print(f"\nArchive:  {os.path.join(args.out, manifest.archive)}")
    print(f"Size:     {roi_data.format_bytes(manifest.size)} "
          f"(unpacks to {roi_data.format_bytes(manifest.unpacked_size)})")
    print(f"Manifest: {os.path.join(args.out, 'manifest.json')}")
    print(f"\nPublish both so the manifest is served at:\n  {roi_data.DEFAULT_MANIFEST_URL}")


if __name__ == "__main__":
    main()
