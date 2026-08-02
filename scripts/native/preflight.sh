#!/usr/bin/env bash
set -euo pipefail

log() { printf '[NATIVE] %s\n' "$*"; }
warn() { printf '[NATIVE] WARNING: %s\n' "$*" >&2; }
fatal() { printf '[NATIVE] ERROR: %s\n' "$*" >&2; exit 1; }

flags_file="${CPU_FLAGS_FILE:-/proc/cpuinfo}"
[ -r "$flags_file" ] || fatal "Cannot read CPU flags from $flags_file"

flags=""
while IFS= read -r line; do
    if [[ "$line" =~ ^(flags|Features)[[:space:]]*: ]]; then
        flags="${line#*:}"
        break
    fi
done < "$flags_file"
# Unit fixtures may contain only the raw space-separated flag list.
if [ -z "$flags" ] && [ -n "${CPU_FLAGS_FILE:-}" ]; then
    flags="$(tr '\n' ' ' < "$flags_file")"
fi

has_flag() {
    case " $flags " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

sse42=no; avx=no; avx2=no
has_flag sse4_2 && sse42=yes
has_flag avx && avx=yes
has_flag avx2 && avx2=yes

log "cpu_flags sse4_2=$sse42 avx=$avx avx2=$avx2"

if [ "$sse42" != yes ]; then
    fatal "CPU flag sse4_2 is not visible. This guest is below the current UE5 x64 baseline. On a VPS/QEMU host, select a modern or host CPU model."
fi

if [ "$avx2" != yes ]; then
    warn "AVX2 is not visible. AVX2 is not a universal UE5 requirement; this build may still impose additional CPU requirements, so keep this diagnostic with any startup report."
fi

if [ "${NATIVE_PREFLIGHT_SKIP_RESOURCES:-0}" != "1" ]; then
    memory_kib=0
    while read -r key value _; do
        if [ "$key" = "MemTotal:" ]; then memory_kib="$value"; break; fi
    done < /proc/meminfo
    memory_gib=$((memory_kib / 1024 / 1024))
    disk_kib="$(df -Pk "${GAME_DIR:-/data/server}" 2>/dev/null | while read -r filesystem blocks used available capacity mountpoint; do [ "$filesystem" = Filesystem ] || value="$available"; done; printf '%s' "${value:-0}")"
    disk_gib=$((disk_kib / 1024 / 1024))
    log "resources memory_gib=$memory_gib disk_available_gib=$disk_gib"
    [ "$memory_gib" -ge 16 ] || warn "Less than 16 GiB RAM is visible; measured Enhanced tests found 8 GiB insufficient."
    [ "$disk_gib" -ge 70 ] || warn "Less than 70 GiB free is visible; updates, backups, and mods may require more space."
fi
