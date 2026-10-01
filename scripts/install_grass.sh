#!/usr/bin/env bash
# ============================================================================
#  MLPREP Detection V4 - GRASS GIS dependency installer (macOS / Linux)
#
#  Installs what the plugin's EIL slope-unit stage needs:
#     1. GRASS GIS 8.x
#     2. the r.slopeunits addon  (the engine calls r.slopeunits.create)
#
#  Usage:
#     install_grass.sh               install anything missing
#     install_grass.sh --check       report only, change nothing
#     install_grass.sh --addon-only  skip GRASS, install the addon
#
#  macOS: Homebrew has no GRASS package, so Homebrew installs micromamba and
#         micromamba installs GRASS from conda-forge into ~/.mlprep_envs/grass.
#         No administrator password is needed. Homebrew itself must already
#         be installed (https://brew.sh).
#  Linux: the distribution's package manager (apt, dnf, pacman or zypper),
#         through sudo, or pkexec when there is no terminal to ask in.
#
#  The plugin's "GRASS GIS Setup" dialog runs this script; it can also be run
#  by hand from a terminal. Mirror of install_grass_windows.bat.
# ============================================================================

set -uo pipefail

ADDON="r.slopeunits"
ADDON_MODULES=(r.slopeunits.create r.slopeunits.clean r.slopeunits.metrics r.slopeunits.optimize)
PROBE_MODULE="r.slopeunits.create"
# Newest first; matches _ALIASES["grass"] in platform_utils.py.
GRASS_NAMES=(grass86 grass85 grass84 grass83 grass82 grass)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(dirname "$SCRIPT_DIR")"
BUNDLED_ADDON="$PLUGIN_DIR/vendor/grass_addons/$ADDON"

# Keep in sync with platform_utils.ENVS_DIR / GRASS_ENV_DIR / grass_addon_dir().
ENVS_DIR="$HOME/.mlprep_envs"
GRASS_ENV_DIR="$ENVS_DIR/grass"
ADDON_DIR="$HOME/.grass8/addons"

GRASS_EXE=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [--check | --addon-only]
  (no option)    install GRASS and the $ADDON addon if missing
  --check        report what is present, change nothing
  --addon-only   install only the addon (GRASS must already exist)
EOF
}

MODE="install"
case "${1:-}" in
    "")           ;;
    --check)      MODE="check" ;;
    --addon-only) MODE="addon" ;;
    -h|--help)    usage; exit 0 ;;
    *)            usage >&2; exit 2 ;;
esac

log()  { printf '%s\n' "$*"; }
fail() {
    log ""
    log "[ERROR] $*"
    log ""
    log "Setup did not complete. See the messages above."
    exit 1
}

# ------------------------------------------------------------------- lookups

# Mirrors platform_utils.find_grass(): the plugin-managed env first, then PATH,
# then the usual install locations.
find_grass() {
    GRASS_EXE=""
    local name dir candidate
    local dirs=()

    for name in "${GRASS_NAMES[@]}"; do
        [ -x "$GRASS_ENV_DIR/bin/$name" ] && { GRASS_EXE="$GRASS_ENV_DIR/bin/$name"; return 0; }
    done
    for name in "${GRASS_NAMES[@]}"; do
        candidate="$(command -v "$name" 2>/dev/null || true)"
        [ -n "$candidate" ] && { GRASS_EXE="$candidate"; return 0; }
    done

    shopt -s nullglob
    dirs+=(/Applications/GRASS-*.app/Contents/Resources/bin
           /Applications/QGIS*.app/Contents/MacOS/bin
           /usr/lib/grass*/bin /usr/local/bin /usr/bin)
    shopt -u nullglob
    for dir in "${dirs[@]}"; do
        for name in "${GRASS_NAMES[@]}"; do
            [ -x "$dir/$name" ] && { GRASS_EXE="$dir/$name"; return 0; }
        done
    done
    return 1
}

# Mirrors platform_utils.grass_addon_installed() on POSIX: GRASS launches a
# script addon by its exact module name, so a `.py` copy does not count.
addon_installed() {
    [ -x "$ADDON_DIR/scripts/$PROBE_MODULE" ] || [ -x "$ADDON_DIR/bin/$PROBE_MODULE" ]
}

# QGIS launched from the Dock or a desktop menu gets a minimal PATH, so look in
# Homebrew's default prefixes as well.
find_brew() {
    local candidate
    for candidate in "$(command -v brew 2>/dev/null || true)" \
                     /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

find_micromamba() {
    local brew_bin="$1" candidate
    for candidate in "$(command -v micromamba 2>/dev/null || true)" \
                     "$("$brew_bin" --prefix 2>/dev/null)/bin/micromamba"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

# ------------------------------------------------------------ install GRASS

install_grass_macos() {
    local brew_bin micromamba
    brew_bin="$(find_brew)" || fail "Homebrew is not installed.
        Install it from https://brew.sh (it needs an administrator password),
        then run this installer again."
    log "Using Homebrew: $brew_bin"

    # NONINTERACTIVE stops brew waiting on a prompt nobody can answer when the
    # plugin runs this script without a terminal.
    export NONINTERACTIVE=1 HOMEBREW_NO_ENV_HINTS=1
    if ! micromamba="$(find_micromamba "$brew_bin")"; then
        log "Installing micromamba with Homebrew..."
        "$brew_bin" install micromamba || fail "brew install micromamba failed."
        micromamba="$(find_micromamba "$brew_bin")" \
            || fail "micromamba was installed but cannot be found."
    fi
    log "Using micromamba: $micromamba"

    log ""
    log "Installing GRASS GIS from conda-forge into $GRASS_ENV_DIR"
    log "(downloads several hundred MB; this takes a few minutes)..."
    mkdir -p "$ENVS_DIR" || fail "Cannot create $ENVS_DIR"
    # A private root prefix keeps micromamba's package cache with the plugin's
    # other environments instead of touching any conda setup the user has.
    MAMBA_ROOT_PREFIX="$ENVS_DIR/.micromamba" "$micromamba" create --yes \
        --prefix "$GRASS_ENV_DIR" --override-channels --channel conda-forge grass \
        || fail "micromamba could not install GRASS."
}

# Prints the command prefix that runs a command as root, or fails.
root_prefix() {
    if [ "$(id -u)" -eq 0 ]; then
        printf ''
    elif sudo -n true 2>/dev/null; then
        printf 'sudo -n'
    elif [ -t 0 ] && command -v sudo >/dev/null 2>&1; then
        printf 'sudo'
    elif command -v pkexec >/dev/null 2>&1 && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
        # Graphical password prompt: the plugin runs this without a terminal.
        printf 'pkexec'
    else
        return 1
    fi
}

install_grass_linux() {
    local manager install_cmd prefix
    if command -v apt-get >/dev/null 2>&1; then
        manager="apt-get"; install_cmd="apt-get install -y grass"
    elif command -v dnf >/dev/null 2>&1; then
        manager="dnf"; install_cmd="dnf install -y grass"
    elif command -v pacman >/dev/null 2>&1; then
        manager="pacman"; install_cmd="pacman -S --noconfirm grass"
    elif command -v zypper >/dev/null 2>&1; then
        manager="zypper"; install_cmd="zypper --non-interactive install grass"
    else
        fail "No supported package manager found (apt-get, dnf, pacman, zypper).
        Install GRASS GIS 8.x with your distribution's tools, then re-run this
        script with --addon-only."
    fi

    prefix="$(root_prefix)" || fail "Installing GRASS needs administrator rights.
        Run this in a terminal, then press Re-check in the plugin:
          sudo $install_cmd"

    log "Installing GRASS GIS with $manager..."
    if [ "$manager" = "apt-get" ]; then
        $prefix apt-get update || fail "apt-get update failed."
    fi
    # shellcheck disable=SC2086  # prefix and install_cmd are deliberately word-split
    $prefix $install_cmd || fail "$manager could not install GRASS."
}

# ------------------------------------------------------------ install addon

# GRASS 8.4 renamed --tmp-location to --tmp-project; distributions still ship
# 8.3, so try the new spelling first and fall back.
grass_exec() {
    "$GRASS_EXE" --tmp-project EPSG:4326 --exec "$@" 2>/dev/null \
        || "$GRASS_EXE" --tmp-location EPSG:4326 --exec "$@"
}

install_addon_from_bundle() {
    local module
    mkdir -p "$ADDON_DIR/scripts" "$ADDON_DIR/bin" \
        || fail "Cannot create the GRASS addon directory $ADDON_DIR"
    for module in "${ADDON_MODULES[@]}"; do
        [ -f "$BUNDLED_ADDON/$module.py" ] || { log "   [WARN] bundled $module.py missing"; continue; }
        # Same layout as grass_addons.install_bundled_addon(): no .py suffix,
        # executable, and any stale .py copy removed.
        cp "$BUNDLED_ADDON/$module.py" "$ADDON_DIR/scripts/$module" \
            && chmod +x "$ADDON_DIR/scripts/$module" \
            && rm -f "$ADDON_DIR/scripts/$module.py" \
            && log "     installed $module"
    done
}

install_addon() {
    [ -n "$GRASS_EXE" ] || fail "Cannot install the addon without GRASS."

    log ""
    log "Installing the $ADDON addon..."
    log "  (into $ADDON_DIR - where the plugin looks)"

    # The bundled copy needs no network and no compiler: r.slopeunits is pure
    # Python. g.extension is the fallback for a plugin copy without vendor/.
    if [ -d "$BUNDLED_ADDON" ]; then
        log "   Installing from the copy bundled with the plugin (no network needed)"
        install_addon_from_bundle
    fi
    if ! addon_installed; then
        log "   Trying g.extension..."
        GRASS_ADDON_BASE="$ADDON_DIR" grass_exec g.extension extension="$ADDON" \
            || log "   [WARN] g.extension did not install $ADDON."
    fi
    addon_installed || fail "Could not install $ADDON automatically.
        Install it by hand:  g.extension extension=$ADDON"
}

# ------------------------------------------------------------------- report

report() {
    log ""
    log "============================================================"
    log "  Result"
    log "============================================================"
    if [ -n "$GRASS_EXE" ]; then
        log "  GRASS GIS        : OK   $GRASS_EXE"
    else
        log "  GRASS GIS        : MISSING"
    fi
    if [ -n "$GRASS_EXE" ] && addon_installed; then
        log "  $ADDON     : OK"
    else
        log "  $ADDON     : MISSING"
    fi
    log ""
}

# --------------------------------------------------------------------- main

log ""
log "============================================================"
log "  MLPREP - GRASS GIS dependency setup"
log "============================================================"
log ""

if find_grass; then
    log "[OK]      GRASS found:  $GRASS_EXE"
else
    log "[MISSING] GRASS GIS is not installed."
fi

# --check is a report: it always succeeds, so it is safe in CI and scripts.
if [ "$MODE" = "check" ]; then
    report
    exit 0
fi

if [ -z "$GRASS_EXE" ] && [ "$MODE" = "install" ]; then
    case "$(uname -s)" in
        Darwin) install_grass_macos ;;
        Linux)  install_grass_linux ;;
        *)      fail "Unsupported system: $(uname -s). Use install_grass_windows.bat on Windows." ;;
    esac
    find_grass || fail "GRASS still not found after installation."
    log "[OK]      GRASS installed: $GRASS_EXE"
fi

install_addon
report
exit 0
