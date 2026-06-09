#!/usr/bin/env bash
# 用途：部署/验收阶段统一校验 lock，并按 active profile 准备扩展离线 wheelhouse 与 venv。
set -euo pipefail

EXTENSION_ENV_GATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
EXTENSION_ENV_GATE_REPO_ROOT="$(cd "$EXTENSION_ENV_GATE_LIB_DIR/.." && cd .. && cd .. && pwd -P)"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$EXTENSION_ENV_GATE_REPO_ROOT/scripts/lib/control_plane_config_paths.sh"
unset EXTENSION_ENV_GATE_LIB_DIR EXTENSION_ENV_GATE_REPO_ROOT

extension_env_gate_deploy_env_value() {
  local root_dir="$1"
  local key="$2"
  local env_file="$root_dir/deploy/.env"
  [[ -f "$env_file" ]] || return 1
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^'\''|'\''$/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    }
  ' "$env_file"
}

extension_env_gate_scheduler_state_host_dir() {
  local root_dir="$1"
  local host_state_root="${HOST_STATE_DIR:-}"
  if [[ -z "$host_state_root" ]]; then
    host_state_root="$(extension_env_gate_deploy_env_value "$root_dir" HOST_STATE_ROOT 2>/dev/null || true)"
  fi
  [[ -n "$host_state_root" ]] || host_state_root="state/openclaw"
  case "$host_state_root" in
    /*) printf '%s/control_plane\n' "${host_state_root%/}" ;;
    *) printf '%s/%s/control_plane\n' "$root_dir" "${host_state_root%/}" ;;
  esac
}

extension_env_gate_resolve_host_config_path() {
  local root_dir="$1"
  local config_path="$2"
  local label="${3:-extension_env}"
  local resolved='' status=0
  [[ -n "$config_path" ]] || return 1
  set +e
  resolved="$(openclaw_control_plane_normalize_host_config_path "$config_path" 2>&1)"
  status=$?
  set -e
  if [[ "$status" -ne 0 || -z "$resolved" ]]; then
    echo "[$label][FAIL] OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH 无法解析为 host 配置路径：$config_path" >&2
    [[ -z "$resolved" ]] || echo "$resolved" >&2
    return 2
  fi
  case "$resolved" in
    "$root_dir"/*) ;;
    *)
      echo "[$label][FAIL] control-plane 配置路径不在当前仓库内：$resolved" >&2
      return 2
      ;;
  esac
  if [[ ! -f "$resolved" ]]; then
    echo "[$label][FAIL] control-plane 配置文件不存在：$resolved" >&2
    return 2
  fi
  printf '%s\n' "$resolved"
}

extension_env_gate_verify_lifecycle_lock() {
  local root_dir="$1"
  local label="${2:-extension_env}"
  local output=''
  # 扩展源码、模块声明与 lifecycle lock 必须先闭合，避免准备 venv 后才在 release gate 暴露漂移。
  if ! output="$(bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" control-plane extensions doctor 2>&1)"; then
    echo "[$label][FAIL] managed extension lifecycle lock 未同步；请先更新并复核扩展 lock：" >&2
    echo "  bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane extensions lock" >&2
    [[ -z "$output" ]] || echo "$output" >&2
    return 2
  fi
}

extension_env_gate_verify_active_profile() {
  local root_dir="$1"
  local config_path="$2"
  local label="${3:-extension_env}"
  local runtime_view="${4:-scheduler}"
  local scheduler_state_host_dir=""
  local output=''
  [[ -n "$config_path" ]] || return 0
  config_path="$(extension_env_gate_resolve_host_config_path "$root_dir" "$config_path" "$label")" || return $?
  if [[ "$runtime_view" == "scheduler" ]]; then
    scheduler_state_host_dir="$(extension_env_gate_scheduler_state_host_dir "$root_dir")"
  fi
  if ! output="$(
    OPENCLAW_RUNTIME_PATH_VIEW="$runtime_view" \
      OPENCLAW_SCHEDULER_STATE_HOST_DIR="$scheduler_state_host_dir" \
      bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" \
        control-plane runtime --config-path "$config_path" extension-env verify --enabled --json 2>&1
  )"; then
    echo "[$label][FAIL] active profile 启用的 managed extension runtime venv 未准备或已失效；请使用唯一扩展环境入口自动同步离线 wheelhouse 并准备 venv：" >&2
    echo "  OPENCLAW_RUNTIME_PATH_VIEW=\"$runtime_view\" bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime --config-path \"$config_path\" extension-env ensure --enabled --offline --json" >&2
    [[ -z "$output" ]] || echo "$output" >&2
    return 2
  fi
}

extension_env_gate_ensure_active_profile() {
  local root_dir="$1"
  local config_path="$2"
  local label="${3:-extension_env}"
  local runtime_view="${4:-scheduler}"
  local report_path="${5:-}"
  local scheduler_state_host_dir=""
  local output=''
  [[ -n "$config_path" ]] || return 0
  config_path="$(extension_env_gate_resolve_host_config_path "$root_dir" "$config_path" "$label")" || return $?
  extension_env_gate_verify_lifecycle_lock "$root_dir" "$label"
  if [[ "$runtime_view" == "scheduler" ]]; then
    scheduler_state_host_dir="$(extension_env_gate_scheduler_state_host_dir "$root_dir")"
  fi
  if ! output="$(
    OPENCLAW_RUNTIME_PATH_VIEW="$runtime_view" \
      OPENCLAW_SCHEDULER_STATE_HOST_DIR="$scheduler_state_host_dir" \
      bash "$root_dir/scripts/runtime/run_openclaw_python_tool.sh" \
        control-plane runtime --config-path "$config_path" extension-env ensure --enabled --offline --json 2>&1
  )"; then
    if [[ -n "$report_path" ]]; then
      mkdir -p "$(dirname "$report_path")"
      printf '%s\n' "$output" >"$report_path"
    fi
    echo "[$label][FAIL] active profile 启用的 managed extension runtime venv 自动准备失败；请复核 lifecycle lock、requirements.lock 与离线 wheelhouse：" >&2
    echo "  bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane extensions doctor" >&2
    echo "  OPENCLAW_RUNTIME_PATH_VIEW=\"$runtime_view\" bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime --config-path \"$config_path\" extension-env ensure --enabled --offline --json" >&2
    [[ -z "$output" ]] || echo "$output" >&2
    return 2
  fi
  if [[ -n "$report_path" ]]; then
    mkdir -p "$(dirname "$report_path")"
    printf '%s\n' "$output" >"$report_path"
  fi
}
