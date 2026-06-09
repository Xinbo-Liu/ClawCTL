#!/usr/bin/env bash
set -euo pipefail

# 唯一受支持的仓库级 shell 入口是 `scripts/testing/run_repo_unittest.sh`；
# 推荐先执行 `scripts/testing/check_repo_test_readiness.sh` 做只读前置预检；
# Python 真源是 `openclaw.testing.repo_unittest` 模块入口，本脚本只负责 shell 包装与环境引导。

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
RUNNER_ARGS=(--workdir "$ROOT_DIR")

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/testing/run_repo_unittest.sh [selectors...]
  bash ./scripts/testing/run_repo_unittest.sh --help

说明：
  - 当前脚本是唯一正式受支持的仓库级 unittest shell 入口；
  - `--help` 可离线查看；真正执行仍属于 Docker 必需的仓库级测试入口；
  - 建议先执行 bash ./scripts/testing/check_repo_test_readiness.sh，再运行 unittest。
USAGE
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then
  usage
  exit 0
fi

case "${OPENCLAW_ALLOW_PYTEST_PLUGIN_AUTOLOAD:-}" in
  1|true|TRUE|yes|YES|on|ON)
    ;;
  *)
    RUNNER_ARGS+=(--env 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1')
    ;;
esac

OPENCLAW_STATIC_PYTHON_READINESS_LABEL='repo unittest'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
exec bash "$STATIC_PYTHON_RUNNER" "${RUNNER_ARGS[@]}" -- -m openclaw.testing.repo_unittest "$@"
