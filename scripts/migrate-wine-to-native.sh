#!/usr/bin/env bash
set -euo pipefail
umask 077

source_dir=""
destination_dir=""
apply=false
source_stopped=false
pid_file=""
stage=""

usage() {
    cat <<'EOF'
Usage: migrate-wine-to-native.sh --source PATH --destination PATH [--source-stopped | --pid-file PATH] [--apply]

Defaults to dry-run. Before --apply, stop Wine and either pass --source-stopped
as an explicit acknowledgement or provide a stale Wine --pid-file that can be
verified as inactive. The source is never deleted. Apply requires an empty
new destination, creates a mode-0600 Wine rollback archive, and migrates the
world through SQLite's snapshot API. Native LinuxServer INIs are rendered from
your .env on first startup; WindowsServer INIs are never activated as Linux INIs.
EOF
}

cleanup() {
    [ -z "$stage" ] || rm -rf -- "$stage"
}
trap cleanup EXIT

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) source_dir="${2:-}"; shift 2 ;;
        --destination) destination_dir="${2:-}"; shift 2 ;;
        --pid-file) pid_file="${2:-}"; shift 2 ;;
        --source-stopped) source_stopped=true; shift ;;
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
[ -f "$source_db" ] && [ ! -L "$source_db" ] || { printf 'Migration error: source game_0.db is missing or unsafe\n' >&2; exit 1; }

if [ -n "$pid_file" ]; then
    [ -f "$pid_file" ] && [ ! -L "$pid_file" ] && [ -r "$pid_file" ] || {
        printf 'Migration error: pid file is missing or unsafe: %s\n' "$pid_file" >&2
        exit 1
    }
    if pid_status="$(python3 - "$pid_file" <<'PY'
import os
import sys
from pathlib import Path
try:
    pid = int(Path(sys.argv[1]).read_text(encoding="ascii").strip())
    if pid < 1:
        raise ValueError
except (OSError, ValueError):
    print("pid file is invalid", file=sys.stderr)
    raise SystemExit(2)
try:
    os.kill(pid, 0)
except ProcessLookupError:
    print("inactive")
except (PermissionError, OSError) as exc:
    print(f"cannot verify pid {pid}: {exc}", file=sys.stderr)
    raise SystemExit(3)
else:
    print(f"active:{pid}")
    raise SystemExit(4)
PY
)"; then
        [ "$pid_status" = inactive ] || { printf 'Migration error: unexpected pid status\n' >&2; exit 1; }
    else
        printf 'Migration error: source stop evidence failed for %s\n' "$pid_file" >&2
        exit 1
    fi
elif [ "$apply" = true ] && [ "$source_stopped" != true ]; then
    printf 'Migration error: --apply requires --source-stopped or a verifiable inactive --pid-file\n' >&2
    exit 1
fi

if [ -d "$destination_dir" ] && [ -n "$(find "$destination_dir" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    printf 'Migration error: destination is not empty; use a new Native volume/path\n' >&2
    exit 1
fi

sqlite_result="$(sqlite3 -readonly "$source_db" 'PRAGMA integrity_check;')"
[ "$sqlite_result" = ok ] || { printf 'Migration error: source database integrity is %s\n' "$sqlite_result" >&2; exit 1; }
source_hash="$(sha256sum "$source_db" | cut -d' ' -f1)"

printf 'Runtime migration plan (dry-run=%s):\n' "$([ "$apply" = true ] && echo false || echo true)"
printf '  source runtime/path: Wine / %s\n' "$source_dir"
printf '  destination runtime/path: Native Linux / %s\n' "$destination_dir"
printf '  world: game_0.db (integrity ok; SQLite snapshot on apply)\n'
printf '  config: generated as LinuxServer from the selected .env on first Native startup\n'
printf '  WindowsServer INIs: kept only in the unchanged source/rollback archive\n'
printf '  source_sha256: %s\n' "$source_hash"

[ "$apply" = true ] || exit 0
mkdir -p "$destination_parent"
if [ -d "$destination_dir" ]; then rmdir "$destination_dir"; fi

# This archive intentionally preserves the complete Wine Saved tree for rollback
# and may contain credentials in its INIs. umask 077 and chmod 0600 restrict it.
backup_path="$(mktemp "${destination_parent}/wine-pre-native-XXXXXXXX.tar.gz")"
tar -czf "$backup_path" -C "$source_dir" ConanSandbox/Saved
chmod 0600 "$backup_path"

stage="$(mktemp -d "${destination_parent}/.native-migration.XXXXXX")"
saved_destination="${stage}/ConanSandbox/Saved"
mkdir -p "$saved_destination"
python3 - "$source_db" "${saved_destination}/game_0.db" <<'PY'
import sqlite3
import sys
source_path, destination_path = sys.argv[1:]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
destination = sqlite3.connect(destination_path)
try:
    source.backup(destination)
    result = destination.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"destination SQLite integrity failed: {result}")
finally:
    destination.close()
    source.close()
PY

result="$(sqlite3 -readonly "${saved_destination}/game_0.db" 'PRAGMA integrity_check;')"
[ "$result" = ok ] || { printf 'Migration error: destination database integrity is %s\n' "$result" >&2; exit 1; }
destination_hash="$(sha256sum "${saved_destination}/game_0.db" | cut -d' ' -f1)"

mkdir -p "${stage}/.migration"
cat > "${stage}/.migration/README.txt" <<EOF
Wine-to-Native runtime migration
Source: ${source_dir}
Source database SHA-256: ${source_hash}
Native snapshot SHA-256: ${destination_hash}
WindowsServer INIs were not activated. Start Native with your reviewed .env so
it renders Config/LinuxServer, then verify database integrity, A2S, RCON, mods,
and player connectivity before considering cleanup of the original Wine data.
EOF
chmod 0600 "${stage}/.migration/README.txt"

mv -- "$stage" "$destination_dir"
stage=""
printf 'Runtime migration applied successfully. Protected Wine rollback archive: %s\n' "$backup_path"
printf 'Source data was not deleted. Native LinuxServer configuration will be rendered from your .env.\n'
printf 'Rollback: stop Native and restart Wine against the unchanged source path: %s\n' "$source_dir"
printf 'For the previous image scripts, pin ghcr.io/balnaimi/conan-exiles-server:2.6.1.\n'
