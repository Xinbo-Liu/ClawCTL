#!/usr/bin/env bash
# 用途：检查控制平面 job manifest 是否仍残留可推导的默认字段与冗余面。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
cd "$ROOT_DIR"
FORWARDED_ARGS=()
while IFS= read -r -d '' item; do
  FORWARDED_ARGS+=("$item")
done < <(openclaw_control_plane_wrapper_args agent_platform "$@")
unset OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  -- -m openclaw.doctor.agent_modules.job_surface "${FORWARDED_ARGS[@]}"
