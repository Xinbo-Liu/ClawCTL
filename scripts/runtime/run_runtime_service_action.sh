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
  - `up` 会先执行 `bootstrap.sh` 刷新 runtime.scheduler.app.env 等运行态派生 env，再执行 `docker compose up -d`；
  - compose 真源已声明 `pull_policy: never`，镜像需由前置镜像阶段准备；
  - 当前维护的 target 由 runtime service registry 决定；base 默认包含 `gateway / ingress / internal-api / scheduler`，启用扩展后会追加 extension target。

动作：
  restart | start | stop | up

选项：
  --target <alias>               仓库约定 target 别名，可重复传入
  --all                          对全部 runtime target 执行动作
  --compose-file <path>          覆盖 compose 文件路径（默认：当前运行画像 effective compose，缺失时回退 deploy/docker-compose.yml）
  --env-file <path>              覆盖 env 文件路径（默认：deploy/.env；up 仅接受默认 env 并刷新运行态派生 env）
  --force-recreate               仅对 up 生效；强制重建目标容器以加载已刷新的 env_file
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[run_runtime_service_action][FAIL] $*" >&2
  exit 2
}

# `up` 会刷新由默认部署配置派生的 runtime env，禁止对临时 env 文件执行该动作。
require_default_env_for_runtime_up() {
  local env_abs="" default_env_abs=""
  env_abs="$(openclaw_repo_abs_path_for_compare "$ROOT_DIR" "$ENV_FILE")" || fail "无法解析 env 文件路径：$ENV_FILE"
  default_env_abs="$(openclaw_repo_abs_path_for_compare "$ROOT_DIR" "$ROOT_DIR/deploy/.env")" || fail "无法解析默认 env 文件路径：$ROOT_DIR/deploy/.env"
  if [[ "$env_abs" != "$default_env_abs" ]]; then
    fail "up 需要默认 deploy/.env 才能刷新 runtime.scheduler.app.env；当前 --env-file=$ENV_FILE。请将配置写入 deploy/site.env、agent/extensions/<extension-id>/deploy/extension.env 或 deploy/targets.d，执行 one_click_config.sh 后去掉 --env-file 重试。"
  fi
}

# 容器启动前重建 runtime app env，确保服务读取的是最新部署配置。
refresh_runtime_derived_env_for_up() {
  echo "[run_runtime_service_action] 刷新运行态派生 env：bash ./scripts/setup/bootstrap.sh" >&2
  bash "$ROOT_DIR/scripts/setup/bootstrap.sh" \
    || fail "bootstrap.sh 执行失败；请先修复 deploy/.env、运行态权限或控制面执行介质后重试。"
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

if [[ "$ACTION" == 'up' ]]; then
  require_default_env_for_runtime_up
fi
[[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
runtime_compose_require_cli >/dev/null || fail '未检测到 docker'
if [[ "$ACTION" == 'up' ]]; then
  refresh_runtime_derived_env_for_up
fi
if [[ "$COMPOSE_FILE_EXPLICIT" != '1' ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE"
runtime_container_load_target_registry_cache || fail '无法加载 runtime target registry'

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
