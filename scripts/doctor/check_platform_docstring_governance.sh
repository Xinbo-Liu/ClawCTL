#!/usr/bin/env bash
# 用途：检查平台 Python 公共接口中文 docstring 基线，默认只阻断新增退化。
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
  bash ./scripts/doctor/check_platform_docstring_governance.sh [--json] [--mode report|ratchet]

说明：
  - 扫描 python/openclaw 平台生产代码中的模块、类、公共函数和公共方法 docstring；
  - 默认 ratchet 模式读取 config/governance/validation/platform_python_docstring_baseline/ 分片基线，只阻断新增退化；
  - report 模式只输出当前缺口统计，并按相对基线新增退化缺口和高优先模块缺口分组；
  - `--help` 可离线查看；真正执行属于 Docker 必需的静态 Python 检查，统一通过控制面容器运行；
  - 生成或更新基线使用内部参数：--write-baseline <path> [--write-baseline-format auto|monolithic|sharded]。
USAGE
}

fail() {
  echo "[check_platform_docstring_governance][FAIL] $*" >&2
  exit "${2:-2}"
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --repo-root|--baseline|--write-baseline|--write-baseline-format|--mode)
      [[ $# -ge 2 ]] || fail "$1 缺少参数"
      ARGS+=("$1" "$2")
      shift 2
      ;;
    --json)
      ARGS+=("$1")
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

export OPENCLAW_REPO_ROOT="${OPENCLAW_REPO_ROOT:-$ROOT_DIR}"
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL='platform docstring governance'

exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  --env "OPENCLAW_REPO_ROOT=$OPENCLAW_REPO_ROOT" \
  -- -m openclaw.doctor.platform.docstring_governance "${ARGS[@]+"${ARGS[@]}"}"
