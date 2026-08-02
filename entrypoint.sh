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

# Resolve direct or file-backed secrets before writing configuration.
# shellcheck source=scripts/runtime/secrets.sh
source /scripts/runtime/secrets.sh
resolve_server_secrets

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
# 4. Download / install Steam Workshop mods atomically
# ============================================
# shellcheck source=scripts/runtime/install-mods.sh
source /scripts/runtime/install-mods.sh
install_mods_atomic

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
