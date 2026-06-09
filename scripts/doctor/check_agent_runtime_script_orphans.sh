#!/usr/bin/env bash
# 用途：检查受管显式扩展包 `agent/extensions/*/agent/modules/*/bin/*` 下是否残留未被真源消费的孤儿脚本，可通过 --extension 限定扫描范围。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
cd "$ROOT_DIR"
exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  -- -m openclaw.doctor.agent_modules.runtime_script_orphans "$@"
