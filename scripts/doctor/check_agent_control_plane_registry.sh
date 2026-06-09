#!/usr/bin/env bash
# 用途：校验 control plane 中 agent / implementation 正式注册对象是否由 agent module manifest 派生且已同步。
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
cd "$ROOT_DIR"
exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" \
  -- -m openclaw.control_plane.cli validate agent-control-plane "$@"
