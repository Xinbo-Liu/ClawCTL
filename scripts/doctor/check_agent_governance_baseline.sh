#!/usr/bin/env bash
# 用途：校验 agent/ 统一治理目录、模块桥接页、group 桥接页、工作区模板注册表与 control plane 注册对象是否保持一致，并确保 contract / implementation 绑定继续收口到 module。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_agent_governance_baseline.sh
  bash ./scripts/doctor/check_agent_governance_baseline.sh --config-path <control-plane-config-path>
  bash ./scripts/doctor/check_agent_governance_baseline.sh --control-plane-profile <profile-id>

说明：
  - 校验 agent/ 统一治理目录、模块桥接页、group 桥接页、工作区模板注册表与 control plane 注册对象是否一致；
  - 检查 job.operationRef / groupRef 是否与 module.operations、group 注册对象保持一致；
  - 检查单 agent 合同与 implementation 绑定是否继续由 module 持有；
  - 主仓库正式校验默认走 agent_platform；如需切换到 base 或受管显式扩展 profile，可直接传入对应 profile id；
  - 如只查看说明，请使用 --help。
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
cd "$ROOT_DIR"
FORWARDED_ARGS=()
while IFS= read -r -d '' item; do
  FORWARDED_ARGS+=("$item")
done < <(openclaw_control_plane_wrapper_args agent_platform "$@")
unset OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH
exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  -- -m openclaw.doctor.agent_governance.baseline "${FORWARDED_ARGS[@]}"
