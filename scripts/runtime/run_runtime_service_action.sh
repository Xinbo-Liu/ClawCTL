#!/usr/bin/env bash
# 用途：统一执行运行服务的 start/stop/restart/up，避免文档与脚本散落 docker compose restart + 服务名。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"

ACTION="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi
COMPOSE_FILE=""
COMPOSE_FILE_EXPLICIT='0'
ENV_FILE="$ROOT_DIR/deploy/.env"
TARGETS=()
SERVICES=()
USE_ALL='0'
FORCE_RECREATE='0'

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_runtime_service_action.sh restart --target gateway --target ingress
  bash ./scripts/runtime/run_runtime_service_action.sh up --target scheduler
  bash ./scripts/runtime/run_runtime_service_action.sh up --target scheduler --force-recreate
  bash ./scripts/runtime/run_runtime_service_action.sh stop --all

说明：
  - 统一执行运行服务的 compose 动作；
  - `restart/start/stop` 直接映射 compose 子命令；
  - `up` 固定执行 `docker compose up -d`；compose 真源已声明 `pull_policy: never`，镜像需由前置镜像阶段准备；
  - 当前维护的 target 由 runtime service registry 决定；base 默认包含 `gateway / ingress / internal-api / scheduler`，启用扩展后会追加 extension target。

动作：
  restart | start | stop | up

选项：
  --target <alias>               仓库约定 target 别名，可重复传入
  --all                          对全部 runtime target 执行动作
  --compose-file <path>          覆盖 compose 文件路径（默认：当前运行画像 effective compose，缺失时回退 deploy/docker-compose.yml）
  --env-file <path>              覆盖 env 文件路径（默认：deploy/.env）
  --force-recreate               仅对 up 生效；强制重建目标容器以刷新 env_file 派生环境
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[run_runtime_service_action][FAIL] $*" >&2
  exit 2
}

case "$ACTION" in
  restart|start|stop|up) ;;
  -h|--help|'') usage; exit 0 ;;
  *) fail "不支持的动作：$ACTION" ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail '--target 缺少参数'
      TARGETS+=("$2")
      shift 2
      ;;
    --all)
      USE_ALL='1'
      shift
      ;;
    --compose-file)
      [[ $# -ge 2 ]] || fail '--compose-file 缺少参数'
      COMPOSE_FILE="$2"
      COMPOSE_FILE_EXPLICIT='1'
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || fail '--env-file 缺少参数'
      ENV_FILE="$2"
      shift 2
      ;;
    --force-recreate)
      FORCE_RECREATE='1'
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

if [[ "$COMPOSE_FILE_EXPLICIT" != '1' ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
runtime_compose_require_cli >/dev/null || fail '未检测到 docker'

if [[ "$USE_ALL" == '1' ]]; then
  mapfile -t TARGETS < <(runtime_known_targets)
fi
for target in "${TARGETS[@]+"${TARGETS[@]}"}"; do
  service_name="$(runtime_service_name_for_target "$target")" || fail "不支持的 --target：$target"
  SERVICES+=("$service_name")
done
if [[ -z "${SERVICES+x}" ]] || [[ ${#SERVICES[@]} -eq 0 ]]; then
  fail '必须至少提供一个 --target，或使用 --all'
fi
mapfile -t SERVICES < <(printf '%s\n' "${SERVICES[@]+"${SERVICES[@]}"}" | runtime_target_dedupe_lines)

if [[ "$ACTION" == 'up' ]]; then
  UP_ARGS=()
  if [[ "$FORCE_RECREATE" == '1' ]]; then
    UP_ARGS+=(--force-recreate)
  fi
  runtime_compose_up_services "$ENV_FILE" "$COMPOSE_FILE" "${UP_ARGS[@]}" "${SERVICES[@]}"
  ingress_service="$(runtime_service_name_for_target ingress 2>/dev/null || true)"
  if [[ -n "$ingress_service" ]] && printf '%s\n' "${SERVICES[@]}" | grep -Fxq "$ingress_service"; then
    runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" restart "$ingress_service"
  fi
  exit $?
fi
runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" "$ACTION" "${SERVICES[@]}"
