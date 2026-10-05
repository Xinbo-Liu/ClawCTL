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
TOOL_OVERLAY_DIR=""

# 职责：清理 unittest 工具覆盖层，避免静态容器验证在仓库 state 下留下临时目录。
cleanup() {
  [[ -n "$TOOL_OVERLAY_DIR" ]] || return 0
  case "$TOOL_OVERLAY_DIR" in
    "$ROOT_DIR/state/openclaw/control_plane/tmp/repo-unittest-tools."*)
      rm -rf "$TOOL_OVERLAY_DIR"
      ;;
    *)
      echo "[run_repo_unittest][WARN] 拒绝清理非 repo unittest 工具目录：$TOOL_OVERLAY_DIR" >&2
      ;;
  esac
}
trap cleanup EXIT

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/testing/run_repo_unittest.sh [selectors...]
  bash ./scripts/testing/run_repo_unittest.sh --report-json <path> [selectors...]
  bash ./scripts/testing/run_repo_unittest.sh --list-tests [--json]
  bash ./scripts/testing/run_repo_unittest.sh --help

说明：
  - 当前脚本是唯一正式受支持的仓库级 unittest shell 入口；
  - `--help` 可离线查看；真正执行仍属于 Docker 必需的仓库级测试入口；
  - 默认发现先枚举测试文件，聚合入口不参与仓库级分桶；worker 负责加载并回传测试 ID；
  - `--report-json` 记录测试 ID、模块、worker、耗时、退出状态和总耗时；
  - worker 与完整 suite 分别受 300 秒和 900 秒内层时限约束；
  - 执行面会把宿主 jq 复制到临时工具覆盖层，确保控制面容器内 shell 子进程可解析 JSON；
  - 建议先执行 bash ./scripts/testing/check_repo_test_readiness.sh，再运行 unittest。
USAGE
}

# 职责：确保 unittest 工具覆盖目录，并把覆盖目录注入静态容器 PATH/LD_LIBRARY_PATH。
ensure_tool_overlay_dir() {
  if [[ -n "$TOOL_OVERLAY_DIR" ]]; then
    return 0
  fi
  mkdir -p "$ROOT_DIR/state/openclaw/control_plane/tmp"
  TOOL_OVERLAY_DIR="$(mktemp -d "$ROOT_DIR/state/openclaw/control_plane/tmp/repo-unittest-tools.XXXXXX")"
  mkdir -p "$TOOL_OVERLAY_DIR/bin" "$TOOL_OVERLAY_DIR/lib"
  cat > "$TOOL_OVERLAY_DIR/bash_env" <<EOF
export PATH="$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin:\${PATH:-}"
export LD_LIBRARY_PATH="$TOOL_OVERLAY_DIR/lib:\${LD_LIBRARY_PATH:-}"
EOF
  RUNNER_ARGS+=(--env "PATH=$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin")
  RUNNER_ARGS+=(--env "LD_LIBRARY_PATH=$TOOL_OVERLAY_DIR/lib")
  RUNNER_ARGS+=(--env "BASH_ENV=$TOOL_OVERLAY_DIR/bash_env")
}

# 职责：复制宿主工具运行时依赖，避免只复制二进制后在 slim 容器中缺共享库。
copy_tool_runtime_deps() {
  local host_tool="$1"
  local ldd_output=""
  command -v ldd >/dev/null 2>&1 || return 0
  ldd_output="$(ldd "$host_tool" 2>/dev/null || true)"
  [[ -n "$ldd_output" ]] || return 0
  printf '%s\n' "$ldd_output" | awk '
    /=>[[:space:]]*\// { print $3; next }
    /^[[:space:]]*\// { print $1; next }
  ' | while IFS= read -r lib_path; do
    [[ -n "$lib_path" && -e "$lib_path" ]] || continue
    case "$(basename "$lib_path")" in
      linux-vdso*|ld-linux*|libc.so*|libm.so*|libpthread.so*|libdl.so*|librt.so*)
        continue
        ;;
    esac
    cp -L "$lib_path" "$TOOL_OVERLAY_DIR/lib/$(basename "$lib_path")"
  done
}

# 职责：把宿主命令复制进静态容器工具覆盖层，缺少必需命令时给出明确失败。
add_host_tool_overlay() {
  local tool_name="$1"
  local required="${2:-1}"
  local host_tool=""
  host_tool="$(command -v "$tool_name" || true)"
  if [[ -z "$host_tool" || ! -x "$host_tool" ]]; then
    if [[ "$required" == "1" ]]; then
      echo "[run_repo_unittest][FAIL] 缺少宿主机命令：$tool_name；请先执行 Docker host/base tools 准备。" >&2
      exit 2
    fi
    return 0
  fi
  ensure_tool_overlay_dir
  cp -L "$host_tool" "$TOOL_OVERLAY_DIR/bin/$tool_name"
  chmod 755 "$TOOL_OVERLAY_DIR/bin/$tool_name"
  copy_tool_runtime_deps "$host_tool"
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
RUNNER_ARGS+=(--env 'OPENCLAW_CONTROL_PLANE_PROFILE=')
RUNNER_ARGS+=(--env 'OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=')
# shellcheck source=../lib/docs_inventory_env.sh
source "$ROOT_DIR/scripts/lib/docs_inventory_env.sh"
openclaw_docs_inventory_prepare_runner_args "$ROOT_DIR"
RUNNER_ARGS+=("${OPENCLAW_DOCS_INVENTORY_RUNNER_ARGS[@]}")
add_host_tool_overlay jq 1
bash "$STATIC_PYTHON_RUNNER" "${RUNNER_ARGS[@]}" -- -m openclaw.testing.repo_unittest "$@"
