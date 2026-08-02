#!/usr/bin/env bash
set -euo pipefail

export GAME_DIR="${GAME_DIR:-/data/server}"
export STEAM_DATA_DIR="${STEAM_DATA_DIR:-/data/steam}"
export BACKUP_DIR="${BACKUP_DIR:-/data/backups}"
export STEAMCMD_BIN="${STEAMCMD_BIN:-${STEAM_DATA_DIR}/steamcmd/steamcmd.sh}"

mkdir -p "$GAME_DIR" "$STEAM_DATA_DIR" "$BACKUP_DIR" "${STEAM_DATA_DIR}/locks"

# Resolve direct or file-backed secrets before they are rendered or used.
# shellcheck source=scripts/runtime/secrets.sh
source /scripts/runtime/secrets.sh
resolve_server_secrets

if [ ! -x "$STEAMCMD_BIN" ]; then
    mkdir -p "$(dirname "$STEAMCMD_BIN")"
    cp -a /opt/steamcmd-bootstrap/. "$(dirname "$STEAMCMD_BIN")/"
    chmod +x "$STEAMCMD_BIN"
fi

/scripts/native/preflight.sh
/scripts/native/install-server.sh

export CONFIG_PLATFORM=LinuxServer
# shellcheck source=scripts/runtime/configure-server.sh
source /scripts/runtime/configure-server.sh
render_server_config

# Download and activate the complete ordered set only after every item passes.
# shellcheck source=scripts/runtime/install-mods.sh
source /scripts/runtime/install-mods.sh
install_mods_atomic

exec /scripts/native/supervisor.sh
