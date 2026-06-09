#!/usr/bin/env bash
# 用途：统一输出当前部署的 compose 渲染结果，避免文档与脚本散写 cd deploy && docker compose config。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_compose_lib.sh"
source "$ROOT_DIR/scripts/lib/flow_step_runner.sh"
COMPOSE_FILE=""
ENV_FILE="$ROOT_DIR/deploy/.env"
SHOW_SECRETS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      [[ $# -ge 2 ]] || { echo "[show_runtime_compose_config][FAIL] --compose-file 缺少路径参数" >&2; exit 2; }
      COMPOSE_FILE="$2"; shift 2 ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo "[show_runtime_compose_config][FAIL] --env-file 缺少路径参数" >&2; exit 2; }
      ENV_FILE="$2"; shift 2 ;;
    --show-secrets)
      SHOW_SECRETS=1; shift ;;
    -h|--help)
      cat <<'USAGE'
用法：
  bash ./scripts/runtime/show_runtime_compose_config.sh
  bash ./scripts/runtime/show_runtime_compose_config.sh --compose-file /path/to/docker-compose.yml --env-file /path/to/.env

说明：
  默认读取当前运行画像 effective compose，缺失时回退 deploy/docker-compose.yml。
  默认会脱敏 token / secret / password / webhook / URL 类变量；确需查看原始值时追加 --show-secrets。
USAGE
      exit 0 ;;
    *) echo "[show_runtime_compose_config][FAIL] 未知参数：$1" >&2; exit 2 ;;
  esac
done
if [[ -z "$COMPOSE_FILE" ]]; then
  COMPOSE_FILE="$(runtime_compose_default_file "$ROOT_DIR" "$ENV_FILE")"
fi
[[ -f "$COMPOSE_FILE" ]] || { echo "[show_runtime_compose_config][FAIL] compose 文件不存在：$COMPOSE_FILE" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "[show_runtime_compose_config][FAIL] env 文件不存在：$ENV_FILE" >&2; exit 2; }
if [[ "$SHOW_SECRETS" == "1" ]]; then
  exec runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" config
fi
runtime_compose_command "$ENV_FILE" "$COMPOSE_FILE" config | flow_redact_sensitive_stream
