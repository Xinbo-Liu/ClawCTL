#!/usr/bin/env bash
# 用途：检查仓库生产 Python 中文语义 docstring，enforce 模式要求生产面零缺口。
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
  bash ./scripts/doctor/check_repo_prod_docstring_governance.sh [--json] [--scope platform|extensions|repo-prod] [--mode report|ratchet|enforce]

说明：
  - 扫描 python/openclaw 平台生产代码与 agent/extensions/*/python 受管扩展生产代码；
  - enforce 模式要求模块、类、公共函数、公共方法和高风险私有函数中文语义 docstring 零缺口；
  - 语义缺口包括缺少中文说明、逐参数含义、已标注参数类型、返回含义、返回类型、异常和副作用说明；
  - ratchet 模式读取 config/governance/validation/repo_prod_semantic_docstring_baseline/ 分片基线；
  - `--help` 可离线查看；真正执行属于 Docker 必需的静态 Python 检查，统一通过控制面容器运行；
  - 生成或更新基线使用内部参数：--write-baseline <path> [--write-baseline-format auto|monolithic|sharded]。
USAGE
}
fail() {
  echo "[check_repo_prod_docstring_governance][FAIL] $*" >&2
  exit "${2:-2}"
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --repo-root|--baseline|--write-baseline|--write-baseline-format|--mode|--scope)
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
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL='repo production semantic docstring governance'

exec bash "$STATIC_PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  --env "OPENCLAW_REPO_ROOT=$OPENCLAW_REPO_ROOT" \
  -- -m openclaw.doctor.platform.semantic_docstring_governance "${ARGS[@]+"${ARGS[@]}"}"
