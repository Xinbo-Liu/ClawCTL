#!/usr/bin/env bash
# 用途：校验文档职责边界；帮助面可离线查看，执行面固定走容器化静态 Python。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/docs/check_documentation_boundaries.sh
  bash ./scripts/docs/check_documentation_boundaries.sh --stdout

说明：
  - `--help` 可离线查看；
  - 真正执行属于 Docker 必需的 docs 静态治理检查；
  - 若当前只是确认前置条件，先执行 bash ./scripts/testing/check_repo_test_readiness.sh。
USAGE
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
  usage
  exit 0
fi

OPENCLAW_STATIC_PYTHON_READINESS_LABEL='documentation boundaries'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
exec bash "$STATIC_PYTHON_RUNNER" --workdir "$ROOT_DIR" -- -m openclaw.docs.validators.boundaries "$@"
