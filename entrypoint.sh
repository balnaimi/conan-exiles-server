#!/bin/bash
set -e

# ============================================
# Conan Exiles Dedicated Server - Entrypoint
# ============================================

GAME_DIR="/conanexiles"
CONFIG_DIR="${GAME_DIR}/ConanSandbox/Saved/Config/WindowsServer"
SERVER_EXE="${GAME_DIR}/ConanSandboxServer.exe"
STEAM_APP_ID=443030

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[CONAN]${NC} $1"; }
warn() { echo -e "${YELLOW}[CONAN]${NC} $1"; }
error() { echo -e "${RED}[CONAN]${NC} $1"; }

# ============================================
# 1. Virtual display handled by xvfb-run
# ============================================

# ============================================
# 2. Download / Update game
# ============================================
if [ ! -f "$SERVER_EXE" ]; then
    log "Game not found. Downloading Conan Exiles Dedicated Server (~4.5GB)..."
    log "This may take 10-30 minutes on first run."
else
    log "Game found. Checking for updates..."
fi

/steamcmd/steamcmd.sh \
    +@sSteamCmdForcePlatformType windows \
    +force_install_dir "$GAME_DIR" \
    +login anonymous \
    +app_update $STEAM_APP_ID validate \
    +quit

if [ ! -f "$SERVER_EXE" ]; then
    error "Download failed! Server executable not found."
    error "Retrying in 10 seconds..."
    sleep 10
    /steamcmd/steamcmd.sh \
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
# 3. Initialize Wine prefix
# ============================================
if [ ! -d "$WINEPREFIX/drive_c" ]; then
    log "Initializing Wine prefix..."
    wineboot --init 2>/dev/null || true
    sleep 5
fi

# ============================================
# 4. Configure server settings
# ============================================
log "Applying server configuration..."
mkdir -p "$CONFIG_DIR"

# --- Engine.ini ---
cat > "$CONFIG_DIR/Engine.ini" << EOF
[URL]
Port=${SERVER_PORT:-7777}

[OnlineSubsystemSteam]
ServerName=${SERVER_NAME:-Conan Exiles Server}
ServerPassword=${SERVER_PASSWORD:-}

[/Script/OnlineSubsystemUtils.IpNetDriver]
MaxClientRate=${MAX_CLIENT_RATE:-15000}
MaxInternetClientRate=${MAX_CLIENT_RATE:-15000}
EOF

# --- ServerSettings.ini ---
cat > "$CONFIG_DIR/ServerSettings.ini" << EOF
[ServerSettings]
AdminPassword=${ADMIN_PASSWORD:-}
MaxNudity=${MAX_NUDITY:-2}
IsBattlEyeEnabled=${BATTLEYE_ENABLED:-False}
IsVACEnabled=False
serverRegion=${SERVER_REGION:-1}
ServerCommunity=${SERVER_COMMUNITY:-0}
NPCMindReadingMode=0
MaxAggroRange=9000
ClampBuildingDamageToResources=True
LogoutCharactersRemainInTheWorld=${LOGOUT_REMAIN:-True}
EverybodyCanLootCorpse=${EVERYBODY_LOOT_CORPSE:-True}

[RCON]
RconEnabled=${RCON_ENABLED:-False}
RconPassword=${RCON_PASSWORD:-}
RconPort=${RCON_PORT:-25575}
RconMaxKarma=60

[/Script/Engine.GameSession]
MaxPlayers=${MAX_PLAYERS:-40}
EOF

# --- Game.ini (PvE/PvP settings) ---
SERVER_TYPE="${SERVER_TYPE:-pve}"
case "$SERVER_TYPE" in
    pvp)
        PVP_ENABLED="True"
        ;;
    pve-c)
        PVP_ENABLED="True"
        ;;
    *)
        PVP_ENABLED="False"
        ;;
esac

cat > "$CONFIG_DIR/Game.ini" << EOF
[/Script/ConanSandbox.SystemSettings]
PVPEnabled=${PVP_ENABLED}
EOF

log "Configuration applied:"
log "  Server Name: ${SERVER_NAME:-Conan Exiles Server}"
log "  Server Type: ${SERVER_TYPE:-pve}"
log "  Max Players: ${MAX_PLAYERS:-40}"
log "  RCON: ${RCON_ENABLED:-False}"
[ -n "$SERVER_PASSWORD" ] && log "  Password: Protected" || log "  Password: None (public)"

# ============================================
# 5. Start server
# ============================================
log "Starting Conan Exiles Dedicated Server..."
log "============================================"

cd "$GAME_DIR"
xvfb-run --auto-servernum wine ConanSandboxServer.exe -log \
    -Port=${SERVER_PORT:-7777} \
    -QueryPort=${QUERY_PORT:-27015} \
    -MaxPlayers=${MAX_PLAYERS:-40}
