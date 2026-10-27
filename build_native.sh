#!/usr/bin/env bash
# Build the Step 4 C++/MPI depositional-routing engine and install it into
# bin/<goos>_<goarch>/ alongside the Go zonal engine.
#
# The engine links GDAL and MPI. Linking them by absolute path made the binary
# depend on one machine's exact Homebrew layout — the previously shipped macOS
# build wants libgdal.39 and silently fails to launch on a host with libgdal.38.
# Each platform therefore ships its dependency closure in a sibling lib/ dir,
# reached through @rpath (macOS) or $ORIGIN (Linux).
#
#   ./build_native.sh              # host platform: engine + pitremove/dinfflowdir
#   ./build_native.sh linux        # linux/amd64 engine + pitremove/dinfflowdir, via Docker
#   ./build_native.sh all          # host + linux
#
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPP_DIR="$PLUGIN_DIR/ml_dfi_handoff/steps/step4_deposition_zone/cpp_mpi_port"
BIN_ROOT="$PLUGIN_DIR/bin"
ENGINE="step4_deposition_zone_mpi"
# TauDEM tools the deposition worker runs before the engine. Built from the
# vendored TauDEM sources and shipped next to the engine, sharing its lib/.
TAUDEM_TOOLS="pitremove dinfflowdir areadinf threshold dinfdistdown dinfdistup"

target="${1:-host}"

host_tag() {
  local goos goarch
  case "$(uname -s)" in
    Darwin) goos=darwin ;;
    Linux)  goos=linux ;;
    *) echo "unsupported host: $(uname -s)" >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) goarch=arm64 ;;
    x86_64|amd64)  goarch=amd64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
  esac
  echo "${goos}_${goarch}"
}

build_host() {
  local tag out
  tag="$(host_tag)"
  out="$BIN_ROOT/$tag"

  echo "==> Building $ENGINE + TauDEM ($TAUDEM_TOOLS) for $tag"
  command -v cmake >/dev/null || { echo "error: cmake not installed" >&2; exit 1; }

  cmake -S "$CPP_DIR" -B "$CPP_DIR/build-$tag" -DCMAKE_BUILD_TYPE=Release \
    -DMLPREP_BUILD_TAUDEM_TOOLS=ON >/dev/null
  cmake --build "$CPP_DIR/build-$tag" --parallel >/dev/null

  local bin
  rm -rf "$out/lib" "$out/share"
  mkdir -p "$out"
  for bin in $ENGINE $TAUDEM_TOOLS; do
    rm -f "$out/$bin"
    cp "$CPP_DIR/build-$tag/$bin" "$out/$bin"
  done

  echo "==> Bundling dependencies"
  if [ "${tag%%_*}" = "darwin" ]; then
    bundle_macos_local "$out"
  else
    bundle_linux_local "$out"
  fi
  echo "    $(du -sh "$out" | cut -f1) total"
}

bundle_macos_local() {
  local out="$1" bin proj_db
  # All three executables share one lib/; the bundler skips dylibs already there.
  for bin in $ENGINE $TAUDEM_TOOLS; do
    python3 "$PLUGIN_DIR/scripts/bundle_macos_deps.py" "$out/$bin" >/dev/null
  done
  # proj.db matching the bundled libproj (see bundled_environment).
  proj_db="$(brew --prefix proj 2>/dev/null)/share/proj/proj.db"
  [ -f "$proj_db" ] || { echo "error: proj.db not found at $proj_db" >&2; exit 1; }
  mkdir -p "$out/share/proj"
  cp "$proj_db" "$(dirname "$proj_db")/proj.ini" "$out/share/proj/"
}

bundle_linux_local() {
  local out="$1" bin
  command -v patchelf >/dev/null || { echo "error: patchelf not installed" >&2; exit 1; }
  mkdir -p "$out/lib"
  # See docker/Dockerfile.linux for why MPI and libstdc++ are excluded.
  for bin in $ENGINE $TAUDEM_TOOLS; do ldd "$out/$bin"; done \
    | awk '/=> \//{print $3}' | sort -u \
    | grep -vE '/(libc|libm|libpthread|libdl|librt|ld-linux)[.-]' \
    | grep -vE '/(libmpi|libmpi_cxx|libstdc\+\+|libgcc_s|libopen-pal|libopen-rte)[.-]' \
    | xargs -I{} cp -Lf {} "$out/lib/"
  for bin in $ENGINE $TAUDEM_TOOLS; do patchelf --set-rpath '$ORIGIN/lib' "$out/$bin"; done
  # Bundled libraries resolve their own dependencies through their own rpath.
  for lib in "$out"/lib/*.so*; do patchelf --set-rpath '$ORIGIN' "$lib"; done
}

build_linux_docker() {
  local out="$BIN_ROOT/linux_amd64"
  echo "==> Building $ENGINE + TauDEM ($TAUDEM_TOOLS) for linux_amd64 (Docker)"
  command -v docker >/dev/null || { echo "error: docker not installed" >&2; exit 1; }

  docker build --platform linux/amd64 \
    -f "$CPP_DIR/docker/Dockerfile.linux" \
    -t mlprep-step4-linux "$CPP_DIR" >/dev/null

  local bin
  rm -rf "$out/lib" "$out/share"
  for bin in $ENGINE $TAUDEM_TOOLS; do rm -f "$out/$bin"; done
  mkdir -p "$out"
  local cid
  cid="$(docker create --platform linux/amd64 mlprep-step4-linux)"
  for bin in $ENGINE $TAUDEM_TOOLS; do
    docker cp "$cid:/out/$bin" "$out/$bin" >/dev/null
    chmod +x "$out/$bin"
  done
  docker cp "$cid:/out/lib" "$out/lib" >/dev/null
  # proj.db matching the bundled libproj (see Dockerfile.linux).
  docker cp "$cid:/out/share" "$out/share" >/dev/null
  docker rm "$cid" >/dev/null
  echo "    $(du -sh "$out" | cut -f1) total"
}

# The Windows engine cannot be built here (it needs MSVC + MS-MPI, see
# build_manual_msvc.ps1). The prebuilt artefact is archived under build_manual/
# and vendored into bin/ so that bin/ stays fully reproducible from the tree.
vendor_windows() {
  local src="$CPP_DIR/build_manual/$ENGINE.exe"
  local out="$BIN_ROOT/windows_amd64"
  [ -f "$src" ] || { echo "  note: no prebuilt Windows engine to vendor"; return 0; }
  mkdir -p "$out"
  cp "$src" "$out/$ENGINE.exe"
  echo "==> Vendored prebuilt Windows engine (no bundled DLLs — the host must"
  echo "    supply GDAL and MS-MPI; the Dependencies panel checks for them)"
}

case "$target" in
  host)  build_host; vendor_windows ;;
  linux) build_linux_docker; vendor_windows ;;
  all)   build_host; build_linux_docker; vendor_windows ;;
  *) echo "usage: $0 [host|linux|all]" >&2; exit 1 ;;
esac

echo
echo "Installed engines:"
find "$BIN_ROOT" -name "$ENGINE*" -maxdepth 2 -exec sh -c \
  'printf "  %-34s %s\n" "${1#'"$BIN_ROOT"'/}" "$(du -sh "$(dirname "$1")" | cut -f1)"' _ {} \;
