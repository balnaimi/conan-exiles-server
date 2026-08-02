#!/bin/bash
set -e

# ============================================
# Conan Exiles Enhanced Dedicated Server - Entrypoint
# ============================================

GAME_DIR="/conanexiles"
CONFIG_DIR="${GAME_DIR}/ConanSandbox/Saved/Config/WindowsServer"
SERVER_EXE="${GAME_DIR}/ConanSandboxServer.exe"
STEAM_APP_ID=443030
WORKSHOP_APP_ID=440900
STEAMCMD_BIN="${STEAMCMD_BIN:-/steamcmd/steamcmd.sh}"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[CONAN]${NC} $1"; }
warn() { echo -e "${YELLOW}[CONAN]${NC} $1"; }
error() { echo -e "${RED}[CONAN]${NC} $1"; }

# ============================================
# 1. Download / Update game
# ============================================
if [ ! -f "$SERVER_EXE" ]; then
    log "Game not found. Downloading Conan Exiles Enhanced Dedicated Server (~4.5GB)..."
    log "This may take 10-30 minutes on first run."
else
    log "Game found. Checking for updates..."
fi

"$STEAMCMD_BIN" \
    +@sSteamCmdForcePlatformType windows \
    +force_install_dir "$GAME_DIR" \
    +login anonymous \
    +app_update $STEAM_APP_ID validate \
    +quit

if [ ! -f "$SERVER_EXE" ]; then
    error "Download failed! Retrying in 10 seconds..."
    sleep 10
    "$STEAMCMD_BIN" \
        +@sSteamCmdForcePlatformType windows \
        +force_install_dir "$GAME_DIR" \
        +login anonymous \
        +app_update $STEAM_APP_ID validate \
        +quit
fi

if [ ! -f "$SERVER_EXE" ]; then
    error "Download failed after retry. Exiting."
    exit 1
fi

log "Game files ready!"

# ============================================
# 2. Initialize Wine prefix
# ============================================
if [ ! -d "$WINEPREFIX/drive_c" ]; then
    log "Initializing Wine prefix..."
    wineboot --init 2>/dev/null || true
    sleep 5
fi

# ============================================
# 3. Configure server settings
# ============================================
CONFIG_PLATFORM=WindowsServer
# shellcheck source=scripts/runtime/configure-server.sh
source /scripts/runtime/configure-server.sh
render_server_config

# ============================================
# 4. Download / install Steam Workshop mods
# ============================================
install_mods() {
    local raw_mod_list="${SERVER_MOD_LIST:-}"
    local mods_dir="${GAME_DIR}/ConanSandbox/Mods"
    local modlist_file="${mods_dir}/modlist.txt"
    local steam_root workshop_root mod_id mod_dir pak_file pak_name

    if [ -z "$raw_mod_list" ]; then
        log "  Mods: None"
        return 0
    fi

    log "Installing Steam Workshop mods..."
    mkdir -p "$mods_dir"
    : > "$modlist_file"

    IFS=',' read -ra MOD_IDS <<< "$raw_mod_list"
    for mod_id in "${MOD_IDS[@]}"; do
        mod_id="${mod_id//[[:space:]]/}"
        [ -z "$mod_id" ] && continue

        if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
            error "Invalid mod ID '$mod_id'. SERVER_MOD_LIST must contain comma-separated numeric Steam Workshop IDs."
            exit 1
        fi

        log "  Downloading workshop mod $mod_id..."
        "$STEAMCMD_BIN" \
            +login anonymous \
            +workshop_download_item "$WORKSHOP_APP_ID" "$mod_id" validate \
            +quit

        mod_dir=""
        for steam_root in ${WORKSHOP_CONTENT_ROOTS:-/root/Steam /home/steam/Steam /steamcmd /root/.steam/steam}; do
            workshop_root="${steam_root}/steamapps/workshop/content/${WORKSHOP_APP_ID}/${mod_id}"
            if [ -d "$workshop_root" ]; then
                mod_dir="$workshop_root"
                break
            fi
        done

        if [ -z "$mod_dir" ]; then
            mod_dir="$(find / -path "*/steamapps/workshop/content/${WORKSHOP_APP_ID}/${mod_id}" -type d -print -quit 2>/dev/null || true)"
        fi

        if [ -z "$mod_dir" ]; then
            error "Workshop mod $mod_id was not found after download."
            exit 1
        fi

        pak_file="$(find "$mod_dir" -maxdepth 2 -type f -name '*.pak' -print -quit)"
        if [ -z "$pak_file" ]; then
            error "Workshop mod $mod_id downloaded, but no .pak file was found in $mod_dir."
            exit 1
        fi

        pak_name="$(basename "$pak_file")"
        cp -f "$pak_file" "${mods_dir}/${pak_name}"
        printf '*%s\n' "$pak_name" >> "$modlist_file"
        log "  Installed $mod_id as $pak_name"
    done

    log "Mods installed. modlist.txt:"
    sed 's/^/[CONAN]   /' "$modlist_file"
}

install_mods

# ============================================
# 5. Start server
# ============================================
log "Starting Conan Exiles Enhanced Dedicated Server..."
log "============================================"

cd "$GAME_DIR"
server_args=(
    -log
    "-Port=${SERVER_PORT:-7777}"
    "-QueryPort=${QUERY_PORT:-27015}"
    "-MaxPlayers=${MAX_PLAYERS:-40}"
)

if [ -n "${MULTIHOME:-}" ]; then
    multihome_http="${MULTIHOMEHTTP:-$MULTIHOME}"
    server_args+=("-MULTIHOME=${MULTIHOME}")
    server_args+=("-MULTIHOMEHTTP=${multihome_http}")
    log "MULTIHOME enabled: ${MULTIHOME}"
    log "MULTIHOMEHTTP enabled: ${multihome_http}"
fi

xvfb-run --auto-servernum wine ConanSandboxServer.exe "${server_args[@]}"
