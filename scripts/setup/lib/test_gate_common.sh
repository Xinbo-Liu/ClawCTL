#!/usr/bin/env bash
# 用途：为 setup/full/basic 门禁脚本提供共享的只读辅助函数，避免重复维护 env 替换与文本解析逻辑。
set -euo pipefail

SETUP_GATE_LAST_DURATION_SECONDS=""
SETUP_GATE_ENV_CONTEXT_CACHE_KEY=""
SETUP_GATE_ENV_CONTEXT_CACHE_OUTPUT=""
SETUP_GATE_ENV_CONTEXT_OUTPUT=""
SETUP_GATE_ENV_CONTEXT_ERROR=""
SETUP_GATE_COMMON_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$SETUP_GATE_COMMON_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$SETUP_GATE_COMMON_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
SETUP_GATE_COMMON_ROOT="$(openclaw_repo_root_from "$SETUP_GATE_COMMON_LIB_DIR")"

setup_gate_epoch_seconds() {
  date +%s
}

setup_gate_duration_seconds() {
  local started_at="$1" finished_at="$2"
  if [[ "$started_at" =~ ^[0-9]+$ && "$finished_at" =~ ^[0-9]+$ && "$finished_at" -ge "$started_at" ]]; then
    printf '%s' "$((finished_at - started_at))"
  else
    printf ''
  fi
}

setup_gate_run_and_capture() {
  local __outvar="$1"
  shift
  local __captured rc restore_errexit=0 started_at='' finished_at=''
  SETUP_GATE_LAST_DURATION_SECONDS=""
  started_at="$(setup_gate_epoch_seconds 2>/dev/null || true)"
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  __captured="$("$@" 2>&1)"
  rc=$?
  finished_at="$(setup_gate_epoch_seconds 2>/dev/null || true)"
  SETUP_GATE_LAST_DURATION_SECONDS="$(setup_gate_duration_seconds "$started_at" "$finished_at")"
  [[ "$restore_errexit" == "1" ]] && set -e
  printf -v "$__outvar" '%s' "$__captured"
  return "$rc"
}

setup_gate_env_file_cache_key() {
  local env_file="$1"
  local _default_env_file="$2"
  local env_dir='' env_base='' resolved_env='' fingerprint=''
  [[ -f "$env_file" ]] || {
    echo "[test_gate_common][FAIL] env 文件不存在：$env_file" >&2
    return 2
  }
  env_dir="$(cd "$(dirname "$env_file")" && pwd -P)" || return $?
  env_base="$(basename "$env_file")"
  resolved_env="$env_dir/$env_base"
  fingerprint="$(stat -c '%Y:%s' "$resolved_env" 2>/dev/null || true)"
  printf '%s|%s|%s' "$resolved_env" "$_default_env_file" "$fingerprint"
}

setup_gate_load_env_context() {
  local env_file="$1"
  local default_env_file="$2"
  local common_root='' cache_key='' output='' filtered_output='' line='' key='' rc=0
  SETUP_GATE_ENV_CONTEXT_OUTPUT=""
  SETUP_GATE_ENV_CONTEXT_ERROR=""
  cache_key="$(setup_gate_env_file_cache_key "$env_file" "$default_env_file" 2>&1)" || {
    rc=$?
    SETUP_GATE_ENV_CONTEXT_ERROR="$cache_key"
    return "$rc"
  }
  if [[ -n "$SETUP_GATE_ENV_CONTEXT_CACHE_KEY" && "$cache_key" == "$SETUP_GATE_ENV_CONTEXT_CACHE_KEY" ]]; then
    SETUP_GATE_ENV_CONTEXT_OUTPUT="$SETUP_GATE_ENV_CONTEXT_CACHE_OUTPUT"
    return 0
  fi
  common_root="$SETUP_GATE_COMMON_ROOT"
  output="$(bash "$common_root/scripts/runtime/run_openclaw_python_tool.sh" setup env query-env-batch --env-file "$env_file" --format shell --all 2>&1)" || {
    rc=$?
    SETUP_GATE_ENV_CONTEXT_ERROR="$output"
    return "$rc"
  }
  while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    filtered_output+="$line"$'\n'
  done <<< "$output"
  output="${filtered_output%$'\n'}"
  SETUP_GATE_ENV_CONTEXT_CACHE_KEY="$cache_key"
  SETUP_GATE_ENV_CONTEXT_CACHE_OUTPUT="$output"
  SETUP_GATE_ENV_CONTEXT_OUTPUT="$output"
}

setup_gate_apply_env_context() {
  local env_file="$1"
  shift
  local output="$SETUP_GATE_ENV_CONTEXT_OUTPUT" line='' key=''
  local -a export_keys=()
  while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export_keys+=("$key")
  done <<< "$output"
  if [[ -n "$output" ]]; then
    eval "$output"
  fi
  ((${#export_keys[@]} > 0)) && export "${export_keys[@]}"
  IMAGE_ENV_DEPLOY_ENV_PATH="$env_file" ENV_FILE="$env_file" "$@"
}

setup_gate_with_env_context() {
  local env_file="$1"
  local default_env_file="$2"
  local rc=0
  shift 2
  setup_gate_load_env_context "$env_file" "$default_env_file" || {
    rc=$?
    [[ -n "$SETUP_GATE_ENV_CONTEXT_ERROR" ]] && echo "$SETUP_GATE_ENV_CONTEXT_ERROR" >&2
    return "$rc"
  }
  setup_gate_apply_env_context "$env_file" "$@"
}

setup_gate_run_and_capture_with_env() {
  local __outvar="$1"
  local env_file="$2"
  local default_env_file="$3"
  shift 3
  local __captured rc restore_errexit=0 started_at='' finished_at=''
  SETUP_GATE_LAST_DURATION_SECONDS=""
  started_at="$(setup_gate_epoch_seconds 2>/dev/null || true)"
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  setup_gate_load_env_context "$env_file" "$default_env_file"
  rc=$?
  if [[ $rc -eq 0 ]]; then
    __captured="$(setup_gate_apply_env_context "$env_file" "$@" 2>&1)"
    rc=$?
  else
    __captured="$SETUP_GATE_ENV_CONTEXT_ERROR"
  fi
  finished_at="$(setup_gate_epoch_seconds 2>/dev/null || true)"
  SETUP_GATE_LAST_DURATION_SECONDS="$(setup_gate_duration_seconds "$started_at" "$finished_at")"
  [[ "$restore_errexit" == "1" ]] && set -e
  printf -v "$__outvar" '%s' "$__captured"
  return "$rc"
}

setup_gate_trim_space() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

setup_gate_missing_required_keys() {
  local file="$1" raw="" line="" key="" value=""
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    line="$(setup_gate_trim_space "$raw")"
    [[ -n "$line" ]] || continue
    [[ "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *=* ]] || continue
    key="$(setup_gate_trim_space "${line%%=*}")"
    value="$(setup_gate_trim_space "${line#*=}")"
    [[ "$value" == "__REQUIRED__" ]] && printf '%s\n' "$key"
  done < "$file"
  return 0
}

setup_gate_csv_contains() {
  local csv="$1" needle="$2"
  [[ -z "$csv" ]] && return 1
  local IFS=,
  local item
  for item in $csv; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

setup_gate_json_escape() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  printf '%s' "$s"
}

setup_gate_append_unique_action() {
  local array_name="$1" item="$2"
  setup_gate_append_unique_array_item "$array_name" "$item"
}

setup_gate_append_unique_array_item() {
  local array_name="$1" item="$2"
  local existing=''
  local -a current_items=()
  [[ -n "$array_name" && -n "$item" ]] || return 0
  case "$array_name" in
    NEXT_ACTIONS)
      current_items=("${NEXT_ACTIONS[@]+"${NEXT_ACTIONS[@]}"}")
      ;;
    CONFIG_NEXT_ACTIONS)
      current_items=("${CONFIG_NEXT_ACTIONS[@]+"${CONFIG_NEXT_ACTIONS[@]}"}")
      ;;
    *)
      echo "[test_gate_common][FAIL] 未知 action 数组：$array_name" >&2
      return 2
      ;;
  esac
  for existing in "${current_items[@]:-}"; do
    [[ "$existing" == "$item" ]] && return 0
  done
  case "$array_name" in
    NEXT_ACTIONS)
      NEXT_ACTIONS+=("$item")
      ;;
    CONFIG_NEXT_ACTIONS)
      CONFIG_NEXT_ACTIONS+=("$item")
      ;;
  esac
}

setup_gate_collect_scenario_actions() {
  local array_name="$1"
  local entry="$2"
  local scenario="$3"
  local transform_func="${4:-}"
  local line='' transformed=''
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    transformed="$line"
    if [[ -n "$transform_func" ]] && declare -F "$transform_func" >/dev/null 2>&1; then
      transformed="$("$transform_func" "$line")"
    fi
    setup_gate_append_unique_array_item "$array_name" "$transformed"
  done < <(setup_gate_failure_scenario_lines "$entry" "$scenario" commands)
}


# shellcheck source=scripts/setup/lib/setup_failure_surface_shell.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/setup_failure_surface_shell.sh"

setup_gate_read_env_key() {
  local file_path="$1"
  local key="$2"
  local raw_line="" line="" current_key="" value=""

  [[ -f "$file_path" ]] || {
    printf ''
    return 0
  }

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line="$raw_line"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *=* ]] && continue
    current_key="${line%%=*}"
    value="${line#*=}"
    current_key="${current_key#"${current_key%%[![:space:]]*}"}"
    current_key="${current_key%"${current_key##*[![:space:]]}"}"
    [[ "$current_key" == "$key" ]] || continue
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    printf '%s' "$value"
    return 0
  done < "$file_path"

  printf ''
}

setup_gate_default_host_state_root() {
  local common_root=''
  common_root="$SETUP_GATE_COMMON_ROOT"
  # shellcheck source=scripts/lib/repo_contracts.sh
  source "$common_root/scripts/lib/repo_contracts.sh"
  command -v jq >/dev/null 2>&1 || {
    echo '[test_gate_common][FAIL] 缺少 jq；无法读取 host_state_root 默认值。' >&2
    return 97
  }
  jq -r '.defaults.host_state_root // empty' "$common_root/$(repo_contract_relpath governance.install_defaults)"
}

setup_gate_runtime_host_env_path_default() {
  local root_dir="$1"
  local common_root='' host_state_root=''
  common_root="$SETUP_GATE_COMMON_ROOT"
  host_state_root="$(setup_gate_default_host_state_root)" || return $?
  [[ -n "$host_state_root" ]] || return 0
  case "$host_state_root" in
    /*)
      printf '%s' "$host_state_root/control_plane/runtime.host.env"
      ;;
    *)
      printf '%s' "$root_dir/$host_state_root/control_plane/runtime.host.env"
      ;;
  esac
}

setup_gate_failure_doc_path() {
  setup_failure_surface_doc_path
}

setup_gate_failure_scenario_field() {
  setup_failure_surface_field "$@"
}

setup_gate_failure_scenario_lines() {
  setup_failure_surface_lines "$@"
}
