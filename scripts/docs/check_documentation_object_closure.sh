#!/usr/bin/env bash
# 用途：校验活动文档 surface 中的路径对象、runtime service 对象与正式命令入口是否仍与统一规格闭环；帮助面可离线查看，执行面固定走控制面容器。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform)"
OPENCLAW_STATIC_PYTHON_READINESS_LABEL='documentation object closure'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" \
  -- -m openclaw.docs.validators.object_closure "$@"
