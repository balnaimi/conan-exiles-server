#!/usr/bin/env bash
set -euo pipefail

log() { printf '[NATIVE] %s\n' "$*"; }
warn() { printf '[NATIVE] WARNING: %s\n' "$*" >&2; }
fatal() { printf '[NATIVE] ERROR: %s\n' "$*" >&2; exit 1; }

GAME_DIR="${GAME_DIR:-/data/server}"
STEAM_DATA_DIR="${STEAM_DATA_DIR:-/data/steam}"
STEAMCMD_BIN="${STEAMCMD_BIN:-${STEAM_DATA_DIR}/steamcmd/steamcmd.sh}"
STEAM_INSTALL_LOCK="${STEAM_INSTALL_LOCK:-${STEAM_DATA_DIR}/locks/server-install.lock}"
STEAM_APP_ID="${STEAM_APP_ID:-443030}"
NATIVE_VALIDATE_SERVER="${NATIVE_VALIDATE_SERVER:-false}"
SHIPPING_BINARY="${GAME_DIR}/ConanSandbox/Binaries/Linux/ConanSandboxServer-Linux-Shipping"

case "${NATIVE_VALIDATE_SERVER,,}" in
    true|false) ;;
    *) fatal "NATIVE_VALIDATE_SERVER must be true or false" ;;
esac

[ -x "$STEAMCMD_BIN" ] || fatal "SteamCMD is not executable at $STEAMCMD_BIN"
mkdir -p "$GAME_DIR" "$STEAM_DATA_DIR" "$(dirname "$STEAM_INSTALL_LOCK")"

exec 9>"$STEAM_INSTALL_LOCK"
flock -n 9 || fatal "Another SteamCMD server install/update is already running"

steam_args=(
    +force_install_dir "$GAME_DIR"
    +login anonymous
    +app_update "$STEAM_APP_ID"
)
if [ "${NATIVE_VALIDATE_SERVER,,}" = true ]; then
    steam_args+=(validate)
fi
steam_args+=(+quit)

log "Installing/updating Conan Exiles Enhanced native server (app $STEAM_APP_ID)"
if ! HOME="$STEAM_DATA_DIR" STEAM_DATA_DIR="$STEAM_DATA_DIR" "$STEAMCMD_BIN" "${steam_args[@]}"; then
    warn "SteamCMD failed; the in-place game installation may be partially updated. The server will not launch. Retry startup, optionally with NATIVE_VALIDATE_SERVER=true."
    exit 1
fi

[ -x "${GAME_DIR}/ConanSandboxServer.sh" ] || fatal "SteamCMD completed but ConanSandboxServer.sh is missing or not executable"
[ -x "$SHIPPING_BINARY" ] || fatal "SteamCMD completed but ConanSandboxServer-Linux-Shipping is missing or not executable"

manifest=""
for candidate in \
    "${STEAM_DATA_DIR}/steamapps/appmanifest_${STEAM_APP_ID}.acf" \
    "${STEAM_DATA_DIR}/steamcmd/steamapps/appmanifest_${STEAM_APP_ID}.acf" \
    "${GAME_DIR}/steamapps/appmanifest_${STEAM_APP_ID}.acf"; do
    if [ -r "$candidate" ]; then manifest="$candidate"; break; fi
done

build_id=unknown
if [ -n "$manifest" ]; then
    build_id="$(python3 - "$manifest" <<'PY'
import re
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
match = re.search(r'"buildid"\s+"([0-9]+)"', text)
print(match.group(1) if match else "unknown")
PY
)"
fi
log "installed_build_id=$build_id"
