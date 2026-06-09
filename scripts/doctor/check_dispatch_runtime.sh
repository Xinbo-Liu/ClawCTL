#!/usr/bin/env bash
# 用途：检查 delivery_adapter 由 scheduler 唯一承载，并补做 preflight/status 探针。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../runtime/runtime_target_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_target_lib.sh"
# shellcheck source=../runtime/runtime_compose_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
# shellcheck source=../runtime/runtime_docker_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_docker_lib.sh"
# shellcheck source=../setup/lib/deploy_env_shell.sh
source "$ROOT_DIR/scripts/setup/lib/deploy_env_shell.sh"
COMPOSE_FILE=""
ENV_FILE=""
OUTPUT_JSON=0
SCHEDULER_TARGET="scheduler"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_dispatch_runtime.sh [--root-dir <path>] [--compose-file <path>] [--env-file <path>] [--json]

说明：
  - 检查 scheduler target 是否已经创建、处于 running、并通过 Docker healthcheck；
  - 从 scheduler 容器直接执行 target adapter 已注册的 preflight/status operation，确认 delivery_adapter 执行能力可用。
  - 默认读取当前运行画像 effective compose，缺失时回退 deploy/docker-compose.yml。
USAGE
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

fail() {
  local msg="$1"
  local code="${2:-2}"
  if [[ "$OUTPUT_JSON" == "1" ]]; then
    local escaped_msg=""
    escaped_msg="$(json_escape "$msg")"
    printf '{\n  "ok": false,\n  "service": "dispatch",\n  "execution_surface": "scheduler",\n  "message": "%s"\n}\n' "$escaped_msg"
  else
    echo "[check_dispatch_runtime][FAIL] $msg" >&2
  fi
  exit "$code"
}

note() {
  [[ "$OUTPUT_JSON" == "1" ]] || echo "[check_dispatch_runtime] $*"
}

emit_no_dispatch_targets() {
  if [[ "$OUTPUT_JSON" == "1" ]]; then
    jq -n '{
      ok: true,
      service: "dispatch",
      execution_surface: "scheduler",
      skipped: true,
      reason: "no dispatch targets configured"
    }'
  else
    note "当前控制面未配置 dispatch target，跳过 delivery_adapter preflight/status 探针"
  fi
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root-dir)
      [[ $# -ge 2 ]] || fail "--root-dir 缺少参数" 2
      ROOT_DIR="$2"
      shift 2
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || fail "--compose-file 缺少参数" 2
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || fail "--env-file 缺少参数" 2
      ENV_FILE="$2"
      shift 2
      ;;
    --json)
      OUTPUT_JSON=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "未知参数：$1" 2 ;;
  esac
done

ROOT_DIR="$(cd "$ROOT_DIR" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deploy/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")}"
[[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE" 2
[[ -f "$ENV_FILE" ]] || fail "环境文件不存在：$ENV_FILE" 2
if [[ -z "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" ]]; then
  deploy_env_shell_load_keys "$ENV_FILE" OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH || fail "无法从 env 读取 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH：$ENV_FILE" 2
  export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
fi
runtime_compose_require_cli >/dev/null || fail "未检测到 docker" 3

check_target() {
  local target="$1"
  local service_name=""
  local container_id=""
  local status=""
  local health=""
  service_name="$(runtime_target_service_name_for_target "$target")" || fail "未定义 $target target 对应 compose service" 2
  container_id="$(runtime_compose_service_container_id "$ENV_FILE" "$COMPOSE_FILE" "$service_name")"
  [[ -n "$container_id" ]] || fail "未找到 $target target 对应容器；请先执行 bash ./scripts/runtime/run_runtime_service_action.sh up --target $target" 10
  status="$(runtime_docker_container_status "$container_id" 2>/dev/null || true)"
  health="$(runtime_docker_container_health "$container_id" 2>/dev/null || true)"
  [[ "$status" == "running" ]] || fail "$target 未处于 running 状态" 11
  [[ "$health" == "healthy" ]] || fail "$target healthcheck 未通过" 12
}

check_target "$SCHEDULER_TARGET"
control_plane_summary="$(bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane summary overview)" || fail "读取 control-plane summary 失败" 13
target_count="$(printf '%s\n' "$control_plane_summary" | jq -r '.counts.targets // 0' 2>/dev/null || printf '0')"
[[ "$target_count" =~ ^[0-9]+$ ]] || target_count=0
if [[ "$target_count" == "0" ]]; then
  emit_no_dispatch_targets
fi
tmp_root="$ROOT_DIR/state/openclaw/control_plane/tmp"
mkdir -p "$tmp_root"
tmp_dir="$(mktemp -d "$tmp_root/check-dispatch-runtime.XXXXXX")"
preflight_json="$tmp_dir/preflight.json"
status_json="$tmp_dir/status.json"
trap 'rm -rf "$tmp_dir"' EXIT
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" dispatch ops run-target-operation --root-dir "$ROOT_DIR" --compose-file "$COMPOSE_FILE" --env-file "$ENV_FILE" --ensure-running strict --operation preflight >"$preflight_json" || fail "dispatch preflight operation 执行失败" 14
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" dispatch ops run-target-operation --root-dir "$ROOT_DIR" --compose-file "$COMPOSE_FILE" --env-file "$ENV_FILE" --ensure-running strict --operation status >"$status_json" || fail "dispatch status operation 执行失败" 15
summary="$(bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" runtime healthcheck dispatch-summary --preflight "$preflight_json" --status "$status_json")" || fail "dispatch 探针输出结构异常" 16

if [[ "$OUTPUT_JSON" == "1" ]]; then
  jq -n --argjson summary "$summary" '{ok: true, service: "dispatch", execution_surface: "scheduler", dispatch_summary: $summary}'
else
  note "delivery_adapter 执行能力检查通过（scheduler + preflight/status 探针）"
  note "$summary"
fi
