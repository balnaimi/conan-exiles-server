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
    decimal_less_than() {
        local left="$1" right="$2"
        while [ "${left#0}" != "$left" ]; do left="${left#0}"; done
        while [ "${right#0}" != "$right" ]; do right="${right#0}"; done
        [ -n "$left" ] || left=0
        [ -n "$right" ] || right=0
        if [ "${#left}" -ne "${#right}" ]; then
            [ "${#left}" -lt "${#right}" ]
        else
            # Equal-length decimal strings compare lexically; -lt can overflow on cgroup v1's unlimited sentinel.
            # shellcheck disable=SC2071
            [[ "$left" < "$right" ]]
        fi
    }

    memory_kib=0
    meminfo_file="${MEMINFO_FILE:-/proc/meminfo}"
    while read -r key value _; do
        if [ "$key" = "MemTotal:" ]; then memory_kib="$value"; break; fi
    done < "$meminfo_file"
    case "$memory_kib" in ''|*[!0-9]*) memory_kib=0 ;; esac
    while [ "${memory_kib#0}" != "$memory_kib" ]; do memory_kib="${memory_kib#0}"; done
    [ -n "$memory_kib" ] || memory_kib=0
    # Keep multiplication within signed Bash arithmetic; 1 EiB is beyond a realistic host total.
    decimal_less_than "$memory_kib" 1125899906842624 || memory_kib=0
    memory_bytes=$((memory_kib * 1024))
    memory_source=meminfo
    for limit_file in \
        "${CGROUP_MEMORY_MAX_FILE:-/sys/fs/cgroup/memory.max}" \
        "${CGROUP_MEMORY_LIMIT_FILE:-/sys/fs/cgroup/memory/memory.limit_in_bytes}"; do
        [ -r "$limit_file" ] || continue
        cgroup_limit=""
        IFS= read -r cgroup_limit < "$limit_file" || true
        case "$cgroup_limit" in
            ''|max|*[!0-9]*) continue ;;
        esac
        while [ "${cgroup_limit#0}" != "$cgroup_limit" ]; do cgroup_limit="${cgroup_limit#0}"; done
        [ -n "$cgroup_limit" ] || cgroup_limit=0
        # cgroup v1 represents "unlimited" with huge near-LONG_MAX sentinels.
        decimal_less_than "$cgroup_limit" 1152921504606846976 || continue
        if [ "$memory_bytes" -eq 0 ] || decimal_less_than "$cgroup_limit" "$memory_bytes"; then
            memory_bytes="$cgroup_limit"
            memory_source=cgroup
        fi
    done
    memory_kib=$((memory_bytes / 1024))
    memory_gib=$((memory_kib / 1024 / 1024))
    disk_kib=0
    while read -r filesystem blocks used available capacity mountpoint; do
        [ "$filesystem" = Filesystem ] && continue
        disk_kib="$available"
        break
    done < <(df -Pk "${GAME_DIR:-/data/server}" 2>/dev/null)
    case "$disk_kib" in ''|*[!0-9]*) disk_kib=0 ;; esac
    disk_gib=$((disk_kib / 1024 / 1024))
    log "resources memory_gib=$memory_gib memory_source=$memory_source disk_available_gib=$disk_gib"
    if [ "$memory_gib" -lt 10 ]; then
        warn "Less than 10 GiB RAM is visible; measured Enhanced tests found 8 GiB insufficient."
    elif [ "$memory_gib" -lt 16 ]; then
        warn "10–15 GiB RAM is visible; the measured small vanilla runtime may fit, but memory headroom is limited and 16 GiB is recommended for typical use."
    fi
    if [ "$disk_gib" -lt 10 ]; then
        warn "Less than 10 GiB free is visible; a Native installation or update may not fit once staging and existing data are included."
    elif [ "$disk_gib" -lt 20 ]; then
        warn "Less than 20 GiB free is visible; the measured basic runtime may fit, but update, backup, and migration headroom is limited."
    fi
fi
