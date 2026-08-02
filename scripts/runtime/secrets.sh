#!/usr/bin/env bash
# Source this file, then call resolve_secret VARIABLE for VARIABLE/VARIABLE_FILE.

secret_error() { printf '[CONAN] ERROR: %s\n' "$*" >&2; }

resolve_secret() {
    local name="${1:-}"
    if [[ ! "$name" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
        secret_error "Invalid secret variable name"
        return 2
    fi

    local file_name="${name}_FILE"
    local direct_value="${!name-}"
    local file_path="${!file_name-}"

    if [ -n "$direct_value" ] && [ -n "$file_path" ]; then
        secret_error "$name and $file_name are both set; configure only one"
        return 2
    fi

    if [ -z "$file_path" ]; then
        return 0
    fi
    if [ ! -r "$file_path" ]; then
        secret_error "$file_name does not point to a readable file"
        return 2
    fi

    local -a lines=()
    mapfile -t lines < "$file_path"
    if [ "${#lines[@]}" -ne 1 ] || [ -z "${lines[0]}" ]; then
        secret_error "$file_name must contain exactly one logical line"
        return 2
    fi
    if [[ "${lines[0]}" == *$'\r'* ]]; then
        secret_error "$file_name contains an unsupported carriage return"
        return 2
    fi

    printf -v "$name" '%s' "${lines[0]}"
    export "$name"
}

resolve_server_secrets() {
    resolve_secret ADMIN_PASSWORD
    resolve_secret SERVER_PASSWORD
    resolve_secret RCON_PASSWORD
}
