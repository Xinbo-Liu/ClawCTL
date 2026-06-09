#!/usr/bin/env bash
# 用途：统一查看 compose 服务状态与运行容器健康，避免文档与脚本散落 docker compose ps / docker inspect 命令。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"

COMPOSE_FILE=""
COMPOSE_FILE_EXPLICIT='0'
ENV_FILE="$ROOT_DIR/deploy/.env"
SKIP_COMPOSE_PS='0'
TARGETS=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/show_runtime_service_status.sh
  bash ./scripts/runtime/show_runtime_service_status.sh --target gateway --target ingress

说明：
  - 默认先输出 `docker compose ps`，再输出仓库约定 target 的容器运行 / 健康状态；
  - `--target` 可重复传入，用于只看指定服务；
  - 当前维护的 target 由 runtime service registry 决定；base 默认包含 `gateway / ingress / internal-api / scheduler`，启用扩展后会追加 extension target。

选项：
  --target <alias>               指定要查看的 target，可重复传入
  --compose-file <path>          覆盖 compose 文件路径（默认：当前运行画像 effective compose，缺失时回退 deploy/docker-compose.yml）
  --env-file <path>              覆盖 env 文件路径（默认：deploy/.env）
  --skip-compose-ps              跳过 compose ps，只输出 target 摘要
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[show_runtime_service_status][FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail '--target 缺少参数'
      TARGETS+=("$2")
      shift 2
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
    --skip-compose-ps)
      SKIP_COMPOSE_PS='1'
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
runtime_container_require_docker >/dev/null
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  mapfile -t TARGETS < <(runtime_known_targets)
fi

if [[ "$SKIP_COMPOSE_PS" != '1' ]]; then
  [[ -f "$COMPOSE_FILE" ]] || fail "compose 文件不存在：$COMPOSE_FILE"
  [[ -f "$ENV_FILE" ]] || fail "env 文件不存在：$ENV_FILE"
  echo '== docker compose ps =='
  runtime_compose_ps "$ENV_FILE" "$COMPOSE_FILE"
  echo
fi

echo '== runtime target status =='
printf '%-12s %-30s %-30s %s\n' 'target' 'service' 'container' 'status'
for target in "${TARGETS[@]}"; do
  service_name="$(runtime_service_name_for_target "$target")" || fail "不支持的 --target：$target"
  container_name="$(runtime_container_name_for_target "$target")" || fail "不支持的 --target：$target"
  status_line="$(runtime_container_status_line "$container_name")"
  printf '%-12s %-30s %-30s %s\n' "$target" "$service_name" "$container_name" "$status_line"
done
