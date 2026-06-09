#!/usr/bin/env bash
# 用途：检查带 taskContract 的任务页是否具备固定任务模板区块；帮助面可离线查看，执行面固定走控制面容器。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
OPENCLAW_STATIC_PYTHON_READINESS_LABEL='documentation task structure'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
exec bash "$STATIC_PYTHON_RUNNER" --workdir "$ROOT_DIR" -- -m openclaw.docs.validators.task_structure "$@"
