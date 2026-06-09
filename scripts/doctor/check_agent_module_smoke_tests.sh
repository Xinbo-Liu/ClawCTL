#!/usr/bin/env bash
# 用途：执行 agent 模块 smoke / regression 测试，可通过 --extension 限定单个受管显式扩展包。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
cd "$ROOT_DIR"
exec bash "$STATIC_PYTHON_RUNNER" --workdir "$ROOT_DIR" -- -m openclaw.doctor.agent_modules.smoke_tests "$@"
