#!/usr/bin/env bash
# 用途：delivery_cleanliness 检查的 shell 包装入口；帮助面可离线查看，执行面固定通过容器化静态 Python 入口调用内部真源。
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
  bash ./scripts/doctor/check_delivery_cleanliness.sh [--json]

说明：
  - `--help` 可离线查看；
  - 真正执行属于 Docker 必需的交付说明洁净度检查；
  - 若当前只是在区分前置条件，先执行 bash ./scripts/testing/check_repo_test_readiness.sh。
USAGE
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
  usage
  exit 0
fi

OPENCLAW_STATIC_PYTHON_READINESS_LABEL='delivery cleanliness'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
exec bash "$STATIC_PYTHON_RUNNER" --workdir "$ROOT_DIR" -- -m openclaw.doctor.release.delivery_cleanliness "$@"
