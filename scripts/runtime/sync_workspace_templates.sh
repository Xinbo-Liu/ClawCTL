#!/usr/bin/env bash
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"

usage() {
  cat <<'USAGE'
用法：
  ./scripts/runtime/sync_workspace_templates.sh
  ./scripts/runtime/sync_workspace_templates.sh --check
  ./scripts/runtime/sync_workspace_templates.sh --config-path <control-plane-config-path>

说明：
  统一由 workspace template base manifest + enabled extension fragment 声明模板目录与运行态目标副本；
  默认执行同步；--check 通过容器化 Python 校验运行态副本是否漂移。
USAGE
}

MODE="sync"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      [[ "$MODE" == "sync" ]] || { echo "[workspace_sync] --check 不能重复指定" >&2; exit 2; }
      MODE="check"
      ;;
    --config-path)
      [[ $# -ge 2 ]] || { echo "[workspace_sync] --config-path 缺少路径参数" >&2; exit 2; }
      RESOLVED_CONFIG_PATH="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[workspace_sync] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform "$RESOLVED_CONFIG_PATH")"

ARGS=(--config-path "$RESOLVED_CONFIG_PATH")
if [[ "$MODE" == "check" ]]; then
  ARGS+=(--check)
fi

COMMON_ENVS=(
  --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH"
)
REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

bash "$PYTHON_RUNNER" --workdir "$ROOT_DIR" "${REPO_PYTHON_ENV_ARGS[@]}" "${COMMON_ENVS[@]}" -- \
  -m openclaw.cli runtime workspace \
  --repo-root "$ROOT_DIR" \
  "${ARGS[@]}"
