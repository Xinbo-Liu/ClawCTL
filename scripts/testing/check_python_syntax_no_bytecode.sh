#!/usr/bin/env bash
# 用途：无字节码产物地检查仓库 Python 语法，入口为 openclaw.testing.syntax_check。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
export PYTHONDONTWRITEBYTECODE=1
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

cd "$ROOT_DIR"
exec bash "$ROOT_DIR/scripts/lib/run_static_python.sh" \
  --env PYTHONDONTWRITEBYTECODE=1 \
  -- -m openclaw.testing.syntax_check "$@"
