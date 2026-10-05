#!/usr/bin/env bash
# 用途：通过 Python 真源检查 entrypointContract 必需文本与禁止文本。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
OPENCLAW_STATIC_PYTHON_READINESS_LABEL='documentation entrypoints'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL

exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  -- -m openclaw.docs.validators.entrypoints "$@"
