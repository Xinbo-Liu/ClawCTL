#!/usr/bin/env bash
# 用途：核对宿主机 / gateway / scheduler 的运行路径口径，快速定位 path-index、runtime.*.env 与平台 dispatch 目录漂移。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
RUNTIME_PATHS_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
RUNTIME_PATHS_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
RUNTIME_CONTAINER_RUNNER="$ROOT_DIR/scripts/runtime/run_runtime_container_command.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/doctor_paths.sh

说明：
  - 对齐宿主机 / gateway / scheduler 的固定路径口径；
  - 默认检查 path-index、runtime.host.env、runtime.gateway.env、runtime.scheduler.env；
  - 若 active profile 提供 dispatch 运行面，会自动追加 dispatch_out / runs / queue / queue_done / audit 目录检查。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[doctor_paths][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

log() { echo "[doctor_paths] $*"; }
section() { printf '\n== %s ==\n' "$*"; }
check_file() {
  local path="$1"
  if [[ -e "$path" ]]; then
    ls -ld "$path"
  else
    echo "MISSING $path"
  fi
}

runtime_paths_env_entry() {
  local view="${1:-host}"
  case "$view" in
    host) printf '%s\n' 'runtime_host_env' ;;
    gateway) printf '%s\n' 'runtime_gateway_env' ;;
    scheduler) printf '%s\n' 'runtime_scheduler_env' ;;
    *) printf 'runtime_%s_env\n' "${view//-/_}" ;;
  esac
}

runtime_paths_env_args() {
  local view="${1:-host}"
  local env_entry=""
  local env_file=""
  env_entry="$(runtime_paths_env_entry "$view")"
  env_file="$(bash "$RUNTIME_PATHS_TOOL" runtime paths resolve "$env_entry" --view host --repo-root "$ROOT_DIR" --config-path "$RUNTIME_PATHS_CONFIG_PATH" --abs-host 2>/dev/null || true)"
  [[ -n "$env_file" && -f "$env_file" ]] || return 0
  printf '%s\0' --env-file "$env_file"
}

runtime_paths_resolve() {
  local entry_id="$1"
  local view="$2"
  shift 2
  local -a args=(
    runtime paths resolve "$entry_id"
    --view "$view"
    --repo-root "$ROOT_DIR"
    --config-path "$RUNTIME_PATHS_CONFIG_PATH"
  )
  while IFS= read -r -d '' item; do
    args+=("$item")
  done < <(runtime_paths_env_args "$view")
  args+=("$@")
  bash "$RUNTIME_PATHS_TOOL" "${args[@]}"
}

runtime_paths_abs_host_path() {
  runtime_paths_resolve "$1" host --abs-host
}

resolve_optional_host_path() {
  local entry_id="$1"
  local output=""
  if output="$(runtime_paths_abs_host_path "$entry_id" 2>/dev/null)"; then
    printf '%s\n' "$output"
    return 0
  fi
  return 1
}

HOST_STATE_DIR="$(runtime_paths_abs_host_path state_root)"
OPENCLAW_CONFIG_HOST="$(runtime_paths_abs_host_path openclaw_config)"
HOST_GATEWAY_DIR="$(runtime_permissions_host_gateway_state_dir "$ROOT_DIR")"
HOST_CONTROL_PLANE_DIR="$(runtime_permissions_host_control_plane_state_dir "$ROOT_DIR")"
GATEWAY_STATE_DIR="$(runtime_paths_resolve state_root gateway)"
SCHEDULER_STATE_DIR="$(runtime_paths_resolve state_root scheduler)"

HOST_DISPATCH_DIR="$(resolve_optional_host_path dispatch_out_dir || true)"
HOST_DISPATCH_RUNS_DIR="$(resolve_optional_host_path dispatch_runs_dir || true)"
HOST_DISPATCH_QUEUE_DIR="$(resolve_optional_host_path dispatch_queue_dir || true)"
HOST_DISPATCH_QUEUE_DONE_DIR="$(resolve_optional_host_path dispatch_queue_done_dir || true)"
HOST_DISPATCH_CFG_DIR="$(resolve_optional_host_path dispatch_config_dir || true)"
HOST_DISPATCH_AUDIT_DIR="$(resolve_optional_host_path dispatch_governance_audit_dir || true)"

section "宿主机关键路径"
for path in \
  "$HOST_STATE_DIR" \
  "$OPENCLAW_CONFIG_HOST" \
  "$HOST_GATEWAY_DIR/exec-approvals.json" \
  "$HOST_CONTROL_PLANE_DIR/path-index.json" \
  "$HOST_CONTROL_PLANE_DIR/runtime.host.env" \
  "$HOST_GATEWAY_DIR/runtime.gateway.env" \
  "$HOST_CONTROL_PLANE_DIR/runtime.scheduler.env" \
  "$HOST_CONTROL_PLANE_DIR/runtime.scheduler.app.env" \
  "$HOST_CONTROL_PLANE_DIR/runtime.internal-api.env" \
  "$HOST_CONTROL_PLANE_DIR/runtime.internal-api.app.env" \
  "$HOST_DISPATCH_DIR" \
  "$HOST_DISPATCH_RUNS_DIR" \
  "$HOST_DISPATCH_QUEUE_DIR" \
  "$HOST_DISPATCH_QUEUE_DONE_DIR" \
  "$HOST_DISPATCH_CFG_DIR" \
  "$HOST_DISPATCH_AUDIT_DIR"; do
  [[ -n "$path" ]] || continue
  check_file "$path"
done

section "统一运行态变量"
printf 'OPENCLAW_STATE_DIR.host=%s\n' "$HOST_STATE_DIR"
printf 'OPENCLAW_STATE_DIR.gateway=%s\n' "$GATEWAY_STATE_DIR"
printf 'OPENCLAW_STATE_DIR.scheduler=%s\n' "$SCHEDULER_STATE_DIR"
[[ -n "$HOST_DISPATCH_DIR" ]] && printf 'DISPATCH_OUT_DIR.host=%s\n' "$HOST_DISPATCH_DIR"
[[ -n "$HOST_DISPATCH_RUNS_DIR" ]] && printf 'DISPATCH_RUNS_DIR.host=%s\n' "$HOST_DISPATCH_RUNS_DIR"
[[ -n "$HOST_DISPATCH_QUEUE_DIR" ]] && printf 'DISPATCH_QUEUE_DIR.host=%s\n' "$HOST_DISPATCH_QUEUE_DIR"
[[ -n "$HOST_DISPATCH_QUEUE_DONE_DIR" ]] && printf 'DISPATCH_QUEUE_DONE_DIR.host=%s\n' "$HOST_DISPATCH_QUEUE_DONE_DIR"
[[ -n "$HOST_DISPATCH_CFG_DIR" ]] && printf 'DISPATCH_CONFIG_DIR.host=%s\n' "$HOST_DISPATCH_CFG_DIR"
[[ -n "$HOST_DISPATCH_AUDIT_DIR" ]] && printf 'DISPATCH_GOVERNANCE_AUDIT_DIR.host=%s\n' "$HOST_DISPATCH_AUDIT_DIR"

section "统一正式入口可调用性"
for path in \
  "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"; do
  if [[ ! -e "$path" ]]; then
    echo "MISSING $path"
  elif [[ -x "$path" ]]; then
    echo "EXEC_OK $path"
  else
    echo "NONEXEC_OK $path"
  fi
done

section "gateway 容器视角"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$(runtime_container_name_for_target gateway)"; then
  bash "$RUNTIME_CONTAINER_RUNNER" --target gateway --shell '
    env | grep -E "^OPENCLAW_HOME|^OPENCLAW_STATE_DIR|^OPENCLAW_CONFIG_PATH|^OPENCLAW_GATEWAY_|^TZ" | sort
    echo
    ls -ld "'"$GATEWAY_STATE_DIR"'" 2>/dev/null || true
  '
else
  log '未检测到运行中的 gateway target，跳过容器视角检查。'
fi

section "scheduler 容器视角"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' | grep -qx "$(runtime_container_name_for_target scheduler)"; then
  bash "$RUNTIME_CONTAINER_RUNNER" --target scheduler --shell '
    printf "OPENCLAW_STATE_DIR=%s\nDISPATCH_OUT_DIR=%s\nDISPATCH_CONFIG_DIR=%s\n" "${OPENCLAW_STATE_DIR:-}" "${DISPATCH_OUT_DIR:-}" "${DISPATCH_CONFIG_DIR:-}"
    ls -ld "${DISPATCH_OUT_DIR:-/missing}" "${DISPATCH_CONFIG_DIR:-/missing}" 2>/dev/null || true
  '
else
  log '未检测到运行中的 scheduler target，跳过容器视角检查。'
fi

log '路径体检完成。'
