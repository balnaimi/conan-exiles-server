#!/usr/bin/env bash
set -euo pipefail

source_dir=""
destination_dir=""
apply=false
pid_file=""

usage() {
    cat <<'EOF'
Usage: migrate-wine-to-native.sh --source PATH --destination PATH [--pid-file PATH] [--apply]

Defaults to dry-run. The source is never deleted. --apply requires an empty
new destination and creates a timestamped source backup before copying.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) source_dir="${2:-}"; shift 2 ;;
        --destination) destination_dir="${2:-}"; shift 2 ;;
        --pid-file) pid_file="${2:-}"; shift 2 ;;
        --apply) apply=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Migration error: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

[ -n "$source_dir" ] && [ -n "$destination_dir" ] || { usage >&2; exit 2; }
source_dir="$(realpath -e "$source_dir")"
destination_parent="$(realpath -m "$(dirname "$destination_dir")")"
destination_dir="${destination_parent}/$(basename "$destination_dir")"
[ "$source_dir" != "$destination_dir" ] || { printf 'Migration error: source and destination are identical\n' >&2; exit 2; }

saved_source="${source_dir}/ConanSandbox/Saved"
source_db="${saved_source}/game_0.db"
config_source="${saved_source}/Config/WindowsServer"
[ -f "$source_db" ] || { printf 'Migration error: source game_0.db is missing\n' >&2; exit 1; }
[ -d "$config_source" ] || { printf 'Migration error: source WindowsServer config is missing\n' >&2; exit 1; }

if [ -z "$pid_file" ]; then pid_file="${source_dir}/.runtime/server.pid"; fi
if [ -r "$pid_file" ]; then
    pid="$(tr -cd '0-9' < "$pid_file")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        printf 'Migration error: source server process is still active (pid file %s)\n' "$pid_file" >&2
        exit 1
    fi
fi

if [ -d "$destination_dir" ] && [ -n "$(find "$destination_dir" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    printf 'Migration error: destination is not empty; use a new Native volume/path\n' >&2
    exit 1
fi

sqlite_result="$(sqlite3 -readonly "$source_db" 'PRAGMA integrity_check;')"
[ "$sqlite_result" = ok ] || { printf 'Migration error: source database integrity is %s\n' "$sqlite_result" >&2; exit 1; }

printf 'Migration plan (dry-run=%s):\n' "$([ "$apply" = true ] && echo false || echo true)"
printf '  source: %s\n  destination: %s\n' "$source_dir" "$destination_dir"
printf '  world: game_0.db (integrity ok)\n  config: WindowsServer -> LinuxServer\n'
printf '  stale BuildIdOverride keys: removed from migrated Engine.ini\n'

[ "$apply" = true ] || exit 0

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="${destination_parent}/wine-pre-native-${timestamp}.tar.gz"
tar -czf "$backup_path" -C "$source_dir" ConanSandbox/Saved

saved_destination="${destination_dir}/ConanSandbox/Saved"
config_destination="${saved_destination}/Config/LinuxServer"
mkdir -p "$config_destination"
cp -- "$source_db" "${saved_destination}/game_0.db"
for name in Engine.ini Game.ini ServerSettings.ini Input.ini; do
    [ -f "${config_source}/${name}" ] && cp -- "${config_source}/${name}" "${config_destination}/${name}"
done

if [ -f "${config_destination}/Engine.ini" ]; then
    python3 - "${config_destination}/Engine.ini" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
blocked = {"bUseBuildIdOverride", "BuildIdOverride"}
filtered = [line for line in lines if line.split("=", 1)[0].strip() not in blocked]
path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
fi

result="$(sqlite3 -readonly "${saved_destination}/game_0.db" 'PRAGMA integrity_check;')"
[ "$result" = ok ] || { printf 'Migration error: destination database integrity is %s\n' "$result" >&2; exit 1; }
printf 'Migration applied successfully. Pre-copy backup: %s\n' "$backup_path"
printf 'Source data was not deleted. Test the Native destination before any cleanup.\n'
