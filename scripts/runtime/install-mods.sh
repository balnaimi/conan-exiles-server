#!/usr/bin/env bash
set -euo pipefail

if ! declare -F log >/dev/null 2>&1; then
    log() { printf '[CONAN] %s\n' "$*"; }
fi
if ! declare -F warn >/dev/null 2>&1; then
    warn() { printf '[CONAN] WARNING: %s\n' "$*" >&2; }
fi
if ! declare -F error >/dev/null 2>&1; then
    error() { printf '[CONAN] ERROR: %s\n' "$*" >&2; }
fi

install_mods_atomic() {
    local game_dir="${GAME_DIR:-/conanexiles}"
    local steam_data_dir="${STEAM_DATA_DIR:-/steamcmd}"
    local steamcmd_bin="${STEAMCMD_BIN:-/steamcmd/steamcmd.sh}"
    local workshop_app_id="${WORKSHOP_APP_ID:-440900}"
    local raw_mod_list="${SERVER_MOD_LIST:-}"
    local compat_mode="${MOD_INSTALL_COMPAT_MODE:-native}"
    local prune="${NATIVE_PRUNE_REMOVED_MODS:-false}"
    local mods_dir="${game_dir}/ConanSandbox/Mods"
    local lock_file="${MOD_INSTALL_LOCK:-${steam_data_dir}/locks/mod-install.lock}"
    local compact mod_id item_dir pak_name old_id old_name still_active
    local stage old_manifest stage_list stage_manifest
    local -a mod_ids=() pak_files=() workshop_roots=()
    local -A seen_names=()

    case "${compat_mode,,}" in
        native|wine) ;;
        *) error "MOD_INSTALL_COMPAT_MODE must be native or wine"; return 2 ;;
    esac
    case "${prune,,}" in
        true|false) ;;
        *) error "NATIVE_PRUNE_REMOVED_MODS must be true or false"; return 2 ;;
    esac
    [ -x "$steamcmd_bin" ] || { error "SteamCMD is not executable at $steamcmd_bin"; return 1; }

    if [ "${compat_mode,,}" = native ]; then
        compact="$raw_mod_list"
        if [ -n "$raw_mod_list" ] && {
            [[ "$raw_mod_list" =~ [[:space:]] ]] ||
            [[ ! "$raw_mod_list" =~ ^[0-9]+(,[0-9]+)*$ ]];
        }; then
            error "Malformed SERVER_MOD_LIST: Native requires exact comma-separated numeric IDs without whitespace or blank entries"
            return 2
        fi
    else
        if [ -z "$raw_mod_list" ]; then
            log "Wine compatibility: empty SERVER_MOD_LIST leaves the existing mod list unchanged"
            return 0
        fi
        IFS= read -r compact <<< "$raw_mod_list" || true
        compact="${compact//[[:space:]]/}"
    fi
    if [ -n "$compact" ]; then
        local -a parsed_ids=()
        IFS=',' read -r -a parsed_ids <<< "$compact"
        mod_ids=()
        for mod_id in "${parsed_ids[@]}"; do
            if [ -z "$mod_id" ]; then
                if [ "${compat_mode,,}" = wine ]; then continue; fi
                error "SERVER_MOD_LIST contains an empty Workshop ID"
                return 2
            fi
            if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
                error "Invalid mod ID entry; SERVER_MOD_LIST must contain comma-separated numeric Workshop IDs"
                return 2
            fi
            mod_ids+=("$mod_id")
        done
    fi

    mkdir -p "$mods_dir" "$(dirname "$lock_file")"
    exec 8>"$lock_file"
    flock -n 8 || { error "Another Workshop mod operation is already running"; return 1; }

    stage="$(mktemp -d "${mods_dir}/.staging.XXXXXX")"
    old_manifest="${stage}/old-managed.tsv"
    stage_list="${stage}/modlist.txt.new"
    stage_manifest="${stage}/managed-mods.tsv.new"
    : > "$stage_list"
    : > "$stage_manifest"
    if [ -r "${mods_dir}/.managed-mods.tsv" ]; then
        cp "${mods_dir}/.managed-mods.tsv" "$old_manifest"
    else
        : > "$old_manifest"
    fi
    trap 'rm -rf -- "${stage:-}"' RETURN

    if [ -n "${STEAM_WORKSHOP_ROOT:-}" ]; then
        workshop_roots+=("$STEAM_WORKSHOP_ROOT")
    fi
    if [ -n "${WORKSHOP_CONTENT_ROOTS:-}" ]; then
        local root
        for root in $WORKSHOP_CONTENT_ROOTS; do
            workshop_roots+=("${root}/steamapps/workshop/content/${workshop_app_id}")
        done
    fi
    workshop_roots+=(
        "${steam_data_dir}/steamcmd/steamapps/workshop/content/${workshop_app_id}"
        "${steam_data_dir}/Steam/steamapps/workshop/content/${workshop_app_id}"
        "${steam_data_dir}/steamapps/workshop/content/${workshop_app_id}"
        "/steamcmd/steamapps/workshop/content/${workshop_app_id}"
        "/root/Steam/steamapps/workshop/content/${workshop_app_id}"
    )

    for mod_id in "${mod_ids[@]}"; do
        log "Downloading Workshop item $mod_id"
        if ! HOME="$steam_data_dir" STEAM_DATA_DIR="$steam_data_dir" \
            "$steamcmd_bin" +login anonymous \
            +workshop_download_item "$workshop_app_id" "$mod_id" validate +quit; then
            error "Workshop item $mod_id failed; preserving the last-known-good active mod list"
            return 1
        fi

        item_dir=""
        local root
        for root in "${workshop_roots[@]}"; do
            if [ -d "${root}/${mod_id}" ]; then
                item_dir="${root}/${mod_id}"
                break
            fi
        done
        if [ -z "$item_dir" ]; then
            error "Workshop item $mod_id downloaded but its content directory was not found; preserving the active list"
            return 1
        fi

        pak_files=()
        while IFS= read -r -d '' pak; do pak_files+=("$pak"); done \
            < <(find "$item_dir" -maxdepth 2 -type f -name '*.pak' -print0 | LC_ALL=C sort -z)
        if [ "${#pak_files[@]}" -eq 0 ]; then
            error "Workshop item $mod_id contains no usable .pak"
            return 1
        fi
        if [ "${#pak_files[@]}" -gt 1 ]; then
            if [ "${compat_mode,,}" = wine ]; then
                warn "Wine compatibility: Workshop item $mod_id contains multiple .pak files; selecting lexical first: ${pak_files[0]}"
            else
                error "Workshop item $mod_id must contain exactly one top-level usable .pak (found ${#pak_files[@]})"
                return 1
            fi
        fi
        if [ ! -s "${pak_files[0]}" ]; then
            if [ "${compat_mode,,}" = wine ]; then
                warn "Wine compatibility: Workshop item $mod_id contains an empty .pak; preserving legacy activation behavior"
            else
                error "Workshop item $mod_id contains an empty .pak"
                return 1
            fi
        fi

        pak_name="$(basename "${pak_files[0]}")"
        if [ -n "${seen_names[$pak_name]+x}" ]; then
            if [ "${compat_mode,,}" = wine ]; then
                warn "Wine compatibility: duplicate package name $pak_name; preserving legacy overwrite/list behavior"
            else
                error "Two Workshop items resolve to the same package name $pak_name"
                return 1
            fi
        else
            printf '%s\t%s\n' "$mod_id" "$pak_name" >> "$stage_manifest"
        fi
        seen_names[$pak_name]=1
        cp -- "${pak_files[0]}" "${stage}/${pak_name}"
        printf '*%s\n' "$pak_name" >> "$stage_list"
    done

    # Package moves and list replacement stay on the same filesystem. The live
    # mod list is replaced only after every download/package check succeeds.
    while IFS=$'\t' read -r mod_id pak_name; do
        [ -n "$pak_name" ] || continue
        mv -f -- "${stage}/${pak_name}" "${mods_dir}/${pak_name}"
    done < "$stage_manifest"
    mv -f -- "$stage_list" "${mods_dir}/modlist.txt"
    mv -f -- "$stage_manifest" "${mods_dir}/.managed-mods.tsv"

    if [ "${prune,,}" = true ]; then
        while IFS=$'\t' read -r old_id old_name; do
            [ -n "$old_name" ] || continue
            case "$old_name" in
                *.pak)
                    [ "$old_name" = "$(basename "$old_name")" ] || continue
                    still_active=false
                    while IFS=$'\t' read -r _ pak_name; do
                        if [ "$pak_name" = "$old_name" ]; then still_active=true; break; fi
                    done < "${mods_dir}/.managed-mods.tsv"
                    if [ "$still_active" = false ]; then
                        rm -f -- "${mods_dir}/${old_name}"
                        log "Pruned removed managed package $old_name"
                    fi
                    ;;
            esac
        done < "$old_manifest"
    fi

    rm -rf -- "$stage"
    trap - RETURN
    log "Activated ${#mod_ids[@]} Workshop mod(s) in requested order"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    install_mods_atomic
fi
