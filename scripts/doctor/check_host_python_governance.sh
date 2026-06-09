#!/usr/bin/env bash
# 用途：host python governance 的正式 shell 包装入口；扫描真源在 Python 模块中维护。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_host_python_governance.sh [--json]

说明：
  - 聚合检查 repo-tracked shell、正式文档、生成文档与 extension 文档中的宿主机 Python 暴露面；
  - 当前仓库真源固定为 enforce；任何命中都失败；
  - observe 模式仅用于内部诊断：当前命中必须是已登记基线子集，任何新增路径 / 新命令形态 / 新类别直接失败；
  - enforce 模式下任何命中都失败；
  - 固定类别：shell_exec / shell_indirect_exec / doc_example / generated_doc_example / extension_doc_example。

内部诊断参数：
  --repo-root <path>
  --config <path>
  --baseline <path>
  --mode <observe|enforce>
USAGE
}

fail() {
  echo "[check_host_python_governance][FAIL] $*" >&2
  exit "${2:-2}"
}

python_env_path() {
  local raw="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$raw"
    return 0
  fi
  printf '%s\n' "$raw"
}

python_arg_path() {
  local raw="${1:-}"
  [[ -n "$raw" ]] || return 0
  if command -v cygpath >/dev/null 2>&1 && [[ "$raw" == /* ]]; then
    cygpath -w "$raw"
    return 0
  fi
  printf '%s\n' "$raw"
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --repo-root|--config|--baseline)
      [[ $# -ge 2 ]] || fail "$1 缺少路径参数"
      ARGS+=("$1" "$(python_arg_path "$2")")
      shift 2
      ;;
    --json|--mode)
      if [[ "$1" == "--mode" ]]; then
        [[ $# -ge 2 ]] || fail '--mode 缺少参数'
        ARGS+=("$1" "$2")
        shift 2
      else
        ARGS+=("$1")
        shift
      fi
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

export OPENCLAW_REPO_ROOT="${OPENCLAW_REPO_ROOT:-$ROOT_DIR}"

if [[ -n "${OPENCLAW_HOST_PYTHON_GOVERNANCE_PYTHON:-}" ]]; then
  PYTHON_BIN="$OPENCLAW_HOST_PYTHON_GOVERNANCE_PYTHON"
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "找不到 Python 解释器：$PYTHON_BIN" 127
  PYTHON_ROOT="$(python_env_path "$ROOT_DIR/python")"
  case "$(uname -s 2>/dev/null || printf unknown)" in
    MINGW*|MSYS*|CYGWIN*) PYTHONPATH_SEP=';' ;;
    *) PYTHONPATH_SEP=':' ;;
  esac
  export PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+$PYTHONPATH_SEP$PYTHONPATH}"
  exec "$PYTHON_BIN" -B -m openclaw.doctor.platform.host_python_governance "${ARGS[@]+"${ARGS[@]}"}"
fi

exec bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" \
  --workdir "$ROOT_DIR" \
  --env "OPENCLAW_REPO_ROOT=$OPENCLAW_REPO_ROOT" \
  -- -m openclaw.doctor.platform.host_python_governance "${ARGS[@]+"${ARGS[@]}"}"
