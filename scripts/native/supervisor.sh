#!/usr/bin/env bash
set -euo pipefail

GAME_DIR="${GAME_DIR:-/data/server}"
RCON_ENABLED="${RCON_ENABLED:-False}"
RCON_PORT="${RCON_PORT:-25575}"
NATIVE_STOP_GRACE_SECONDS="${NATIVE_STOP_GRACE_SECONDS:-30}"
NATIVE_TERM_GRACE_SECONDS="${NATIVE_TERM_GRACE_SECONDS:-10}"
server_pid=""
stop_requested=false

log() { printf '[NATIVE] %s\n' "$*"; }
warn() { printf '[NATIVE] WARNING: %s\n' "$*" >&2; }

is_true() {
    case "${1,,}" in true|1|yes|on) return 0;; *) return 1;; esac
}

valid_seconds() {
    [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 600 ]
}
valid_seconds "$NATIVE_STOP_GRACE_SECONDS" || { warn "NATIVE_STOP_GRACE_SECONDS must be 1-600"; exit 2; }
valid_seconds "$NATIVE_TERM_GRACE_SECONDS" || { warn "NATIVE_TERM_GRACE_SECONDS must be 1-600"; exit 2; }

server_exited() {
    [ -n "$server_pid" ] || return 0
    if ! kill -0 "$server_pid" 2>/dev/null; then return 0; fi
    state="$(ps -o stat= -p "$server_pid" 2>/dev/null || true)"
    [[ -z "$state" || "$state" == Z* ]]
}

wait_for_exit() {
    local seconds="$1" ticks=$((seconds * 4))
    local tick
    for ((tick=0; tick<ticks; tick++)); do
        server_exited && return 0
        sleep 0.25
    done
    server_exited
}

graceful_stop() {
    [ "$stop_requested" = true ] && return 0
    stop_requested=true
    log "Stop requested for native Shipping process pid=$server_pid"

    if is_true "$RCON_ENABLED" && [ -n "${RCON_PASSWORD:-}" ]; then
        if python3 /scripts/native/rcon.py --host 127.0.0.1 --port "$RCON_PORT" --timeout 5 shutdown >/dev/null; then
            log "RCON stop command accepted"
        else
            warn "RCON stop command failed; waiting briefly before TERM fallback"
        fi
    fi

    if wait_for_exit "$NATIVE_STOP_GRACE_SECONDS"; then
        log "Native Shipping process exited during graceful window"
        return 0
    fi

    warn "Native Shipping process still active; sending TERM to its process group"
    kill -TERM -- "-$server_pid" 2>/dev/null || kill -TERM "$server_pid" 2>/dev/null || true
    if wait_for_exit "$NATIVE_TERM_GRACE_SECONDS"; then return 0; fi

    warn "Native Shipping process ignored TERM; sending KILL"
    kill -KILL -- "-$server_pid" 2>/dev/null || kill -KILL "$server_pid" 2>/dev/null || true
}

handle_signal() {
    graceful_stop
}
trap handle_signal TERM INT

cd "$GAME_DIR"
args=(-log "-Port=${SERVER_PORT:-7777}" "-QueryPort=${QUERY_PORT:-27015}" "-MaxPlayers=${MAX_PLAYERS:-40}")
if [ -n "${MULTIHOME:-}" ]; then
    args+=("-MULTIHOME=${MULTIHOME}" "-MULTIHOMEHTTP=${MULTIHOMEHTTP:-$MULTIHOME}")
fi

setsid "$GAME_DIR/ConanSandboxServer.sh" "${args[@]}" &
server_pid=$!
log "Started native Shipping process group pid=$server_pid"

set +e
wait "$server_pid"
status=$?
set -e
if [ "$stop_requested" = true ]; then
    wait "$server_pid" 2>/dev/null || true
    exit 0
fi
exit "$status"
