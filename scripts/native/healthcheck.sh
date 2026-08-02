#!/usr/bin/env bash
set -euo pipefail

QUERY_PORT="${QUERY_PORT:-27015}"
RCON_PORT="${RCON_PORT:-25575}"
RCON_ENABLED="${RCON_ENABLED:-False}"

python3 /scripts/native/a2s-info.py 127.0.0.1 "$QUERY_PORT" --timeout 2 >/dev/null
case "${RCON_ENABLED,,}" in
    true|1|yes|on)
        python3 /scripts/native/rcon.py --host 127.0.0.1 --port "$RCON_PORT" --timeout 2 help >/dev/null
        ;;
esac
