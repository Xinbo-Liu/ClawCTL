#!/usr/bin/env bash
set -euo pipefail

HOST_INSTALL_DEFAULTS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$HOST_INSTALL_DEFAULTS_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$HOST_INSTALL_DEFAULTS_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
HOST_INSTALL_DEFAULTS_ROOT="$(openclaw_repo_root_from "$HOST_INSTALL_DEFAULTS_LIB_DIR")"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$HOST_INSTALL_DEFAULTS_ROOT/scripts/lib/repo_contracts.sh"
unset HOST_INSTALL_DEFAULTS_LIB_DIR

host_install_defaults_repo_root() {
  printf '%s\n' "$HOST_INSTALL_DEFAULTS_ROOT"
}

host_install_defaults_truth_file() {
  printf '%s/%s\n' "$(host_install_defaults_repo_root)" "$(repo_contract_relpath governance.install_defaults)"
}

host_install_defaults_value_default() {
  local key="$1"
  local fallback="$2"
  local truth_file=""
  local value=""
  truth_file="$(host_install_defaults_truth_file)"
  if [[ -f "$truth_file" ]]; then
    value="$(awk -v key="$key" '
      BEGIN {in_defaults=0; quote=sprintf("%c", 34)}
      /"defaults"[[:space:]]*:[[:space:]]*\{/ {in_defaults=1; next}
      in_defaults && $0 ~ /^[[:space:]]*}/ {in_defaults=0}
      in_defaults {
        pattern = "^[[:space:]]*" quote key quote "[[:space:]]*:[[:space:]]*" quote
        if ($0 ~ pattern) {
          line = $0
          sub(/^.*:[[:space:]]*"/, "", line)
          sub(/"[[:space:]]*,?[[:space:]]*$/, "", line)
          print line
          exit
        }
      }
    ' "$truth_file")"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  fi
  printf '%s\n' "$fallback"
}

host_install_defaults_required_value() {
  local key="$1"
  local value=""
  value="$(host_install_defaults_value_default "$key" "")"
  if [[ -z "$value" ]]; then
    echo "[host_install_defaults] 缺少 install_defaults 真源：$key" >&2
    return 2
  fi
  printf '%s\n' "$value"
}

host_install_defaults_state_root_default() {
  host_install_defaults_required_value host_state_root
}
