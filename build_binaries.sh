#!/usr/bin/env bash
# Cross-compile the Go zonal engine for every platform the plugin ships to.
#
# End users have no Go toolchain, so the plugin loads bin/<goos>_<goarch>/zonal
# at runtime. Run this before packaging a release.
#
#   ./build_binaries.sh
#
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$PLUGIN_DIR/ml-prep-scripts"
OUT_ROOT="$PLUGIN_DIR/bin"

TARGETS=(
  darwin/arm64
  darwin/amd64
  linux/amd64
  windows/amd64
)

command -v go >/dev/null 2>&1 || {
  echo "error: the Go toolchain is required to build release binaries" >&2
  exit 1
}

echo "Building zonal engine from $SRC_DIR"

# Remove only this script's own outputs. bin/ is shared with build_native.sh,
# which puts the C++/MPI engine and its bundled libraries in the same tree —
# wiping the whole directory here would silently delete them.
find "$OUT_ROOT" -name "zonal" -o -name "zonal.exe" 2>/dev/null | while read -r stale; do
  rm -f "$stale"
done

for target in "${TARGETS[@]}"; do
  goos="${target%/*}"
  goarch="${target#*/}"
  suffix=""
  [ "$goos" = "windows" ] && suffix=".exe"

  out="$OUT_ROOT/${goos}_${goarch}/zonal${suffix}"
  mkdir -p "$(dirname "$out")"

  echo "  -> $goos/$goarch"
  ( cd "$SRC_DIR" && CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
      go build -trimpath -ldflags="-s -w" -o "$out" . )
done

echo
echo "Done. Binaries in $OUT_ROOT:"
find "$OUT_ROOT" -type f -exec ls -lh {} \; | awk '{print "  " $9 "  " $5}'
