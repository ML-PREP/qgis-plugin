#!/usr/bin/env bash
# Run the headless EIL pipeline (scripts/run_eil_pipeline.py) in a Linux
# container, so the shipped bin/linux_amd64 TauDEM tools can be used from macOS.
#
#   eil-docker.sh build                     build the image
#   eil-docker.sh setup                     create the EIL venv + GRASS addon
#   eil-docker.sh run DATA_DIR [ARGS...]    run the pipeline; DATA_DIR is /data
#   eil-docker.sh shell [DATA_DIR]          bash inside the container
#
# Example:
#   eil-docker.sh run ~/gis/cotabato \
#       --dem /data/dem.tif --raster Slope=/data/slope.tif --raster Clay=/data/clay.tif
#
# The plugin repo is mounted at /plugin (your working tree, no rebuild needed
# after code changes). The venv and the GRASS addon live in the volumes
# "mlprep-eil-envs" and "mlprep-eil-grass8"; `docker volume rm` them to force
# a fresh setup.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$HERE/../.." && pwd)"
IMAGE="mlprep-eil-linux"
ENVS_VOLUME="mlprep-eil-envs"
# GRASS addons go to ~/.grass8/addons (platform_utils.grass_addon_dir).
ADDONS_VOLUME="mlprep-eil-grass8"
PLATFORM="linux/amd64"
# MPI uses shared memory between its 4 ranks; Docker's 64 MB default is tight.
SHM_SIZE="1g"

usage() {
  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() { echo "error: $*" >&2; exit 2; }

require_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || die "image $IMAGE not built yet; run: $0 build"
  [ -x "$PLUGIN_DIR/bin/linux_amd64/pitremove" ] \
    || die "bin/linux_amd64 is missing the TauDEM tools; run: ./build_native.sh linux"
}

docker_run() {
  # -t only with a terminal, so the script also works when piped or in CI.
  local tty=()
  [ -t 0 ] && [ -t 1 ] && tty=(-it)
  docker run --rm ${tty[@]+"${tty[@]}"} --platform "$PLATFORM" --shm-size "$SHM_SIZE" \
    -v "$PLUGIN_DIR:/plugin" \
    -v "$ENVS_VOLUME:/root/.mlprep_envs" \
    -v "$ADDONS_VOLUME:/root/.grass8" \
    "$@"
}

data_mount() {
  local data_dir="$1"
  [ -d "$data_dir" ] || die "data directory not found: $data_dir"
  echo "$(cd "$data_dir" && pwd):/data"
}

cmd="${1:-}"
[ $# -gt 0 ] && shift

case "$cmd" in
  build)
    docker build --platform "$PLATFORM" -t "$IMAGE" "$HERE"
    ;;
  setup)
    require_image
    docker_run "$IMAGE" --setup-only
    ;;
  run)
    [ $# -ge 1 ] || die "usage: $0 run DATA_DIR [ARGS...]"
    require_image
    mount="$(data_mount "$1")"; shift
    docker_run -v "$mount" "$IMAGE" "$@"
    ;;
  shell)
    require_image
    extra=()
    if [ $# -ge 1 ]; then extra=(-v "$(data_mount "$1")"); fi
    docker_run ${extra[@]+"${extra[@]}"} --entrypoint bash "$IMAGE"
    ;;
  -h|--help|help|"")
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
