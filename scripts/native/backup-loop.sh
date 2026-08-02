#!/usr/bin/env bash
set -euo pipefail

enabled="${NATIVE_BACKUP_ENABLED:-false}"
interval="${NATIVE_BACKUP_INTERVAL_MINUTES:-60}"
tool="${NATIVE_BACKUP_TOOL:-/scripts/native/backup.sh}"

case "${enabled,,}" in
    false|0|no|off) exit 0 ;;
    true|1|yes|on) ;;
    *) printf '[NATIVE] ERROR: NATIVE_BACKUP_ENABLED must be true or false\n' >&2; exit 2 ;;
esac
if [[ ! "$interval" =~ ^[0-9]+$ ]] || [ "$interval" -lt 1 ] || [ "$interval" -gt 10080 ]; then
    printf '[NATIVE] ERROR: NATIVE_BACKUP_INTERVAL_MINUTES must be 1-10080\n' >&2
    exit 2
fi
[ -x "$tool" ] || { printf '[NATIVE] ERROR: backup tool is not executable: %s\n' "$tool" >&2; exit 2; }

run_backup() {
    if ! "$tool" --reason scheduled; then
        printf '[NATIVE] WARNING: scheduled backup failed; the server will keep running\n' >&2
        return 1
    fi
}

if [ "${NATIVE_BACKUP_RUN_ONCE:-0}" = "1" ]; then
    run_backup
    exit $?
fi

printf '[NATIVE] Scheduled backups enabled every %s minute(s)\n' "$interval"
while sleep "$((interval * 60))"; do
    run_backup || true
done
