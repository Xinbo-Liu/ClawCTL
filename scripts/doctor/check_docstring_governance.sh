#!/usr/bin/env bash
# 用途：一次遍历生产 Python 文件并执行平台覆盖与仓库语义 docstring 递进检查。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

exec bash "$ROOT_DIR/scripts/lib/run_static_python.sh" --workdir "$ROOT_DIR" -- \
  -m openclaw.doctor.platform.docstring_governance_batch "$@"
