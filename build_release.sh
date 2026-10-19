#!/usr/bin/env bash
# Package the plugin into a QGIS-installable zip.
#
# The plugin directory contains things that must never ship: a machine-specific
# virtualenv, Python bytecode, macOS metadata, a nested git repository and CMake
# probe artefacts. Zipping the directory directly would include all of them, so
# the release is staged through an explicit allow/deny list and then verified.
#
#   ./build_release.sh                          # every platform
#   ./build_release.sh --no-roi                 # omit roi_data/ (see below)
#   ./build_release.sh --platform windows_amd64 # only that platform's engines
#
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
DIST_DIR="$PLUGIN_DIR/dist"
STAGE="$DIST_DIR/$PLUGIN_NAME"

INCLUDE_ROI=1
PLATFORM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-roi)   INCLUDE_ROI=0 ;;
    --platform) shift; PLATFORM="${1:-}" ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

# Never ship. venv/ is the important one: it is built for one machine's Python
# and one user's home directory, and is 1.7 GB.
EXCLUDES=(
  "venv/"
  "eil_venv/"
  "ls_detect_env/"
  "dist/"
  "__pycache__/"
  "*.pyc"
  ".pytest_cache/"
  "output/"
  "scratch/"
  # Pipeline run outputs left in the plugin directory (several GB each).
  "/run_*/"
  "/deposition_zone_output/"
  "/EIL_Results/"
  ".coverage"
  ".DS_Store"
  ".git/"
  ".gitignore"
  "tests/"
  "*_backup.py"
  "*_backup.go.txt"
  "*_Old.py"
  "*_old.py"
  "CMakeFiles/"
  "CMakeCache.txt"
  "build_mac/"
  "build-*/"
  "build_manual/"
  "docker/"
  # Credentials must never ship, even if one lands in the working tree again.
  "mlprep-carsu"
  "mlprep-carsu.pub"
  "id_rsa*"
  "id_ed25519*"
  "*.pem"
)
[ "$INCLUDE_ROI" -eq 0 ] && EXCLUDES+=("roi_data/")

# A platform-targeted package drops the other platforms' engines. Only
# bin/<host tag>/ is ever consulted at runtime, so shipping the rest is pure
# weight — 320 MB of macOS and Linux libraries on a Windows install.
if [ -n "$PLATFORM" ]; then
  for other in "$PLUGIN_DIR"/bin/*/; do
    tag="$(basename "$other")"
    [ "$tag" = "$PLATFORM" ] || EXCLUDES+=("bin/$tag/")
  done
fi

# Files without which the plugin cannot load.
REQUIRED=(
  "metadata.txt"
  "__init__.py"
  "mainPlugin.py"
  "app/main_window.py"
  "landslide/tab.py"
  "eil/tab.py"
  "platform_utils.py"
  "dependency_panel.py"
  "busy_overlay.py"
  "grass_addons.py"
  "grass_gate.py"
  "scripts/install_grass.sh"
  "scripts/install_grass_windows.bat"
)

echo "Packaging $PLUGIN_NAME"

# 1. Binaries must exist for every target before packaging.
if [ ! -d "$PLUGIN_DIR/bin" ]; then
  echo "  bin/ missing — running build_binaries.sh"
  "$PLUGIN_DIR/build_binaries.sh"
fi
for target in darwin_arm64 darwin_amd64 linux_amd64 windows_amd64; do
  suffix=""; [ "${target%_*}" = "windows" ] && suffix=".exe"
  [ -f "$PLUGIN_DIR/bin/$target/zonal$suffix" ] || {
    echo "error: missing bin/$target/zonal$suffix — run ./build_binaries.sh" >&2
    exit 1
  }
done

# The depositional-routing engine cannot be cross-compiled the way Go can, so
# it is only present for platforms actually built. Warn rather than fail: the
# plugin degrades to skipping that stage with an explanation.
for target in darwin_arm64 darwin_amd64 linux_amd64 windows_amd64; do
  suffix=""; [ "${target%_*}" = "windows" ] && suffix=".exe"
  [ -f "$PLUGIN_DIR/bin/$target/step4_deposition_zone_mpi$suffix" ] || \
    echo "  warning: no depositional-routing engine for $target (build with ./build_native.sh)"
done

# 2. Stage.
rm -rf "$STAGE"
mkdir -p "$STAGE"

rsync_args=(-a)
for pattern in "${EXCLUDES[@]}"; do rsync_args+=(--exclude "$pattern"); done
rsync "${rsync_args[@]}" "$PLUGIN_DIR/" "$STAGE/"

# 3. Verify. An exclude pattern that silently stops matching would otherwise
#    ship 1.7 GB of someone else's virtualenv.
for required in "${REQUIRED[@]}"; do
  [ -f "$STAGE/$required" ] || { echo "error: $required missing from stage" >&2; exit 1; }
done

# A platform-targeted package is useless without that platform's engines.
if [ -n "$PLATFORM" ]; then
  suffix=""; [ "${PLATFORM%_*}" = "windows" ] && suffix=".exe"
  [ -f "$STAGE/bin/$PLATFORM/zonal$suffix" ] || {
    echo "error: no zonal engine for $PLATFORM in the package" >&2; exit 1; }
  [ -d "$STAGE/bin" ] && [ "$(ls "$STAGE/bin" | wc -l)" -eq 1 ] || {
    echo "error: expected only bin/$PLATFORM in the package" >&2; exit 1; }
fi

leaked=$(find "$STAGE" \( -name "pyvenv.cfg" -o -name "__pycache__" -o -name ".DS_Store" \
                       -o -name "*.pyc" -o -name ".git" -o -name "CMakeCache.txt" \
                       -o -name "build-*" -o -name "build_manual" \) -print -quit)
if [ -n "$leaked" ]; then
  echo "error: excluded content leaked into the package: $leaked" >&2
  exit 1
fi

# Catch credentials by content, not just by name: a renamed key still leaks.
# grep exits 1 on no match, which set -o pipefail would turn into a silent abort.
key_file=$(grep -rlI -e "-----BEGIN [A-Z ]*PRIVATE KEY-----" "$STAGE" 2>/dev/null | head -1 || true)
if [ -n "$key_file" ]; then
  echo "error: private key found in the package: $key_file" >&2
  exit 1
fi

# 4. Zip.
zip_suffix=""
[ -n "$PLATFORM" ] && zip_suffix="-$PLATFORM"
[ "$INCLUDE_ROI" -eq 0 ] && zip_suffix="$zip_suffix-noroi"
zip_path="$DIST_DIR/${PLUGIN_NAME}${zip_suffix}.zip"
rm -f "$zip_path"
( cd "$DIST_DIR" && zip -qr "$(basename "$zip_path")" "$PLUGIN_NAME" )
rm -rf "$STAGE"

echo
echo "Package: $zip_path"
echo "Size:    $(du -h "$zip_path" | cut -f1)"
if [ "$INCLUDE_ROI" -eq 1 ]; then
  echo
  echo "Note: roi_data/ is ~1.4 GB and dominates this archive. Pass --no-roi to"
  echo "      omit it; roi_data/ can then be copied into the installed plugin"
  echo "      directory afterwards, since it is only read at runtime."
fi
