#!/usr/bin/env bash
# 用途：在单个隔离仓库副本中验证受管 agent 模块的完整生命周期矩阵。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

exec bash "$ROOT_DIR/scripts/lib/run_static_python.sh" --workdir "$ROOT_DIR" -- \
  -m openclaw.doctor.agent_modules.lifecycle_matrix "$@"
