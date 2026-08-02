#!/usr/bin/env bash
set -euo pipefail

export GAME_DIR="${GAME_DIR:-/data/server}"
export STEAM_DATA_DIR="${STEAM_DATA_DIR:-/data/steam}"
export BACKUP_DIR="${BACKUP_DIR:-/data/backups}"
export STEAMCMD_BIN="${STEAMCMD_BIN:-${STEAM_DATA_DIR}/steamcmd/steamcmd.sh}"

if [ "${NATIVE_OPERATION_LOCK_HELD:-0}" != 1 ]; then
    exec python3 /scripts/native/runtime_state.py lock-exec \
        --game-dir "$GAME_DIR" -- /scripts/native/entrypoint.sh
fi
python3 /scripts/native/runtime_state.py verify-lock --game-dir "$GAME_DIR"
unset NATIVE_OPERATION_LOCK_HELD

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
# A verified restore records its exact Workshop dependencies here; consume that
# marker once so light/full restores cannot silently start with different mods.
restore_mod_marker="$GAME_DIR/.runtime/restore-required-workshop-ids"
if [ -e "$restore_mod_marker" ] || [ -L "$restore_mod_marker" ]; then
    if [ -L "$restore_mod_marker" ] || [ ! -f "$restore_mod_marker" ]; then
        echo "[CONAN] ERROR: Restore mod dependency marker is unsafe" >&2
        exit 1
    fi
    mapfile -t restored_mod_lines < "$restore_mod_marker"
    restored_mod_list="${restored_mod_lines[0]:-}"
    if [ "${#restored_mod_lines[@]}" -ne 1 ] || {
        [ -n "$restored_mod_list" ] && [[ ! "$restored_mod_list" =~ ^[0-9]+(,[0-9]+)*$ ]];
    }; then
        echo "[CONAN] ERROR: Restore mod dependency marker is malformed" >&2
        exit 1
    fi
    configured_mod_list="${SERVER_MOD_LIST:-}"
    if [ -n "$configured_mod_list" ]; then
        if [[ "$configured_mod_list" =~ [[:space:]] ]] || [[ ! "$configured_mod_list" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
            echo "[CONAN] ERROR: SERVER_MOD_LIST is malformed for Native restore" >&2
            exit 1
        fi
        if [ "$configured_mod_list" != "$restored_mod_list" ]; then
            echo "[CONAN] ERROR: SERVER_MOD_LIST conflicts with restored Workshop dependencies" >&2
            exit 1
        fi
    fi
    export SERVER_MOD_LIST="$restored_mod_list"
fi
# shellcheck source=scripts/runtime/install-mods.sh
source /scripts/runtime/install-mods.sh
install_mods_atomic
if [ -e "$restore_mod_marker" ] || [ -L "$restore_mod_marker" ]; then rm -f -- "$restore_mod_marker"; fi

# FD 9 intentionally remains locked across exec. The supervisor releases it
# only after the Shipping process PID is durably published.

# Keep the game process environment free of resolved plaintext credentials.
# For direct RCON input, retain a mode-0600 runtime file on tmpfs for the
# supervisor; _FILE users continue using their mounted secret file.
RUNTIME_SECRET_DIR="${RUNTIME_SECRET_DIR:-/tmp/conan-runtime-secrets}"
RUNTIME_RCON_SECRET_FILE="${RUNTIME_SECRET_DIR}/rcon-password"
if [ -n "${RCON_PASSWORD:-}" ] && [ -z "${RCON_PASSWORD_FILE:-}" ]; then
    mkdir -p "$RUNTIME_SECRET_DIR"
    chmod 0700 "$RUNTIME_SECRET_DIR"
    printf '%s' "$RCON_PASSWORD" > "$RUNTIME_RCON_SECRET_FILE"
    chmod 0600 "$RUNTIME_RCON_SECRET_FILE"
    export RCON_PASSWORD_FILE="$RUNTIME_RCON_SECRET_FILE"
fi
unset ADMIN_PASSWORD SERVER_PASSWORD RCON_PASSWORD

exec /scripts/native/supervisor.sh
