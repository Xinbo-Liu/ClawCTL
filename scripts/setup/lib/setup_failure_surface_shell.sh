#!/usr/bin/env bash
# 用途：统一从 setup_failures 真源读取 doc path、场景标题、命令与说明，避免多处重复维护第二份失败分流口径。
set -euo pipefail

SETUP_FAILURE_SURFACE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$SETUP_FAILURE_SURFACE_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$SETUP_FAILURE_SURFACE_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
SETUP_FAILURE_SURFACE_LIB_ROOT="$(openclaw_repo_root_from "$SETUP_FAILURE_SURFACE_LIB_DIR")"
# shellcheck source=scripts/lib/repo_python_env.sh
source "$SETUP_FAILURE_SURFACE_LIB_ROOT/scripts/lib/repo_python_env.sh"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$SETUP_FAILURE_SURFACE_LIB_ROOT/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$SETUP_FAILURE_SURFACE_LIB_ROOT/scripts/lib/repo_contracts.sh"
unset SETUP_FAILURE_SURFACE_LIB_DIR
repo_contract_assign_path SETUP_FAILURE_SURFACE_PATH governance.setup_failures
SETUP_FAILURE_SURFACE_CACHE_PATH="${SETUP_FAILURE_SURFACE_CACHE_PATH:-}"

setup_failure_surface_jq_available() {
  command -v jq >/dev/null 2>&1
}

setup_failure_surface_python_runner() {
  printf '%s\n' "${PYTHON_RUNNER:-$SETUP_FAILURE_SURFACE_LIB_ROOT/scripts/runtime/run_python_container.sh}"
}

setup_failure_surface_config_path() {
  openclaw_control_plane_resolve_config_path agent_platform
}

setup_failure_surface_cleanup() {
  if [[ -n "$SETUP_FAILURE_SURFACE_CACHE_PATH" ]] && [[ -f "$SETUP_FAILURE_SURFACE_CACHE_PATH" ]]; then
    rm -f "$SETUP_FAILURE_SURFACE_CACHE_PATH"
  fi
}
trap setup_failure_surface_cleanup EXIT

setup_failure_surface_render_json() {
  local config_path runner tmp_file item=''
  local -a repo_python_env_args=()
  setup_failure_surface_jq_available || return 1
  config_path="$(setup_failure_surface_config_path)"
  runner="$(setup_failure_surface_python_runner)"
  [[ -f "$runner" ]] || return 1
  while IFS= read -r -d '' item; do
    repo_python_env_args+=("$item")
  done < <(openclaw_repo_python_env_args "$SETUP_FAILURE_SURFACE_LIB_ROOT")
  tmp_file="$(mktemp "${TMPDIR:-/tmp}/openclaw-setup-failures.XXXXXX.json")"
  if OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$config_path" \
    bash "$runner" \
      --workdir "$SETUP_FAILURE_SURFACE_LIB_ROOT" \
      "${repo_python_env_args[@]}" \
      --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$config_path" \
      -- -m openclaw.setup.surface.governance_cli setup-failures-surface --config-path "$config_path" >"$tmp_file" 2>/dev/null; then
    SETUP_FAILURE_SURFACE_CACHE_PATH="$tmp_file"
    printf '%s' "$SETUP_FAILURE_SURFACE_CACHE_PATH"
    return 0
  fi
  rm -f "$tmp_file"
  return 1
}

setup_failure_surface_materialized_path() {
  if [[ -n "$SETUP_FAILURE_SURFACE_CACHE_PATH" ]] && [[ -f "$SETUP_FAILURE_SURFACE_CACHE_PATH" ]]; then
    printf '%s' "$SETUP_FAILURE_SURFACE_CACHE_PATH"
    return 0
  fi
  if setup_failure_surface_render_json >/dev/null; then
    printf '%s' "$SETUP_FAILURE_SURFACE_CACHE_PATH"
    return 0
  fi
  printf '%s' "$SETUP_FAILURE_SURFACE_PATH"
}

setup_failure_surface_doc_path() {
  setup_failure_surface_field "" "" doc_path
}

setup_failure_surface_field() {
  local entry="$1"
  local scenario="$2"
  local field="$3"
  local surface_path
  surface_path="$(setup_failure_surface_materialized_path)"
  if [[ -f "$surface_path" ]] && setup_failure_surface_jq_available; then
    jq -r --arg entry "$entry" --arg scenario "$scenario" --arg field "$field" '
      (.entries[$entry].scenarios[$scenario] // {}) as $node |
      if $field == "doc_path" then (.generated_artifacts.setup_failure_doc // "")
      elif $field == "entry_title" then (.entries[$entry].title // $entry)
      elif $field == "scenario_title" then ($node.title // $scenario)
      elif $field == "when" then ($node.when // "")
      else empty end
    ' "$surface_path" 2>/dev/null || true
    return 0
  fi
  printf ''
}

setup_failure_surface_lines() {
  local entry="$1"
  local scenario="$2"
  local field="$3"
  local surface_path
  surface_path="$(setup_failure_surface_materialized_path)"
  if [[ -f "$surface_path" ]] && setup_failure_surface_jq_available; then
    jq -r --arg entry "$entry" --arg scenario "$scenario" --arg field "$field" '
      (.entries[$entry].scenarios[$scenario] // {}) as $node |
      if $field == "commands" then ($node.commands // [])[]?
      elif $field == "notes" then ($node.notes // [])[]?
      elif $field == "references" then ($node.references // [])[]?
      else empty end
    ' "$surface_path" 2>/dev/null || true
    return 0
  fi
}
