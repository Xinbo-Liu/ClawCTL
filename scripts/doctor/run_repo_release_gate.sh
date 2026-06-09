#!/usr/bin/env bash
# 用途：统一执行仓库静态发布门禁的 shell 包装入口；帮助面可离线查看，执行面固定委托给容器化 Python 真源。
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
  bash ./scripts/doctor/run_repo_release_gate.sh [--with-docker-sock] [--quiet] [--json]
  bash ./scripts/doctor/run_repo_release_gate.sh --help

说明：
  - `--help` 可离线查看；
  - 真正执行 repo release gate 属于 Docker 必需的仓库级静态治理入口；
  - 默认不把 /var/run/docker.sock 挂入检查容器；只有显式传入 --with-docker-sock 才挂载；
  - 无 Docker 时，先执行 bash ./scripts/testing/check_repo_test_readiness.sh 查看缺失的是 Docker 还是控制面执行介质；
  - 可独立于完整 release gate 运行的仓库级治理入口保留为 bash ./scripts/testing/check_repo_test_readiness.sh、bash ./scripts/doctor/check_host_python_governance.sh 与 bash ./scripts/doctor/check_platform_docstring_governance.sh --mode report；除 --help 外，静态 Python 检查仍固定要求 Docker 与控制面执行介质。
USAGE
}

WITH_DOCKER_SOCK=0
ARGS=()
TOOL_OVERLAY_DIR=""

cleanup() {
  [[ -n "$TOOL_OVERLAY_DIR" ]] || return 0
  case "$TOOL_OVERLAY_DIR" in
    "$ROOT_DIR/state/openclaw/control_plane/tmp/release-gate-tools."*)
      rm -rf "$TOOL_OVERLAY_DIR"
      ;;
    *)
      echo "[run_repo_release_gate][WARN] 拒绝清理非 release gate 工具目录：$TOOL_OVERLAY_DIR" >&2
      ;;
  esac
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-docker-sock)
      WITH_DOCKER_SOCK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

OPENCLAW_STATIC_PYTHON_READINESS_LABEL='repo release gate'
export OPENCLAW_STATIC_PYTHON_READINESS_LABEL
STATIC_RUNNER_ARGS=(--workdir "$ROOT_DIR")

ensure_tool_overlay_dir() {
  if [[ -n "$TOOL_OVERLAY_DIR" ]]; then
    return 0
  fi
  mkdir -p "$ROOT_DIR/state/openclaw/control_plane/tmp"
  TOOL_OVERLAY_DIR="$(mktemp -d "$ROOT_DIR/state/openclaw/control_plane/tmp/release-gate-tools.XXXXXX")"
  mkdir -p "$TOOL_OVERLAY_DIR/bin" "$TOOL_OVERLAY_DIR/lib"
  STATIC_RUNNER_ARGS+=(--env "PATH=$TOOL_OVERLAY_DIR/bin:/usr/local/bin:/usr/bin:/bin")
  STATIC_RUNNER_ARGS+=(--env "LD_LIBRARY_PATH=$TOOL_OVERLAY_DIR/lib")
}

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

add_host_tool_overlay() {
  local tool_name="$1"
  local required="${2:-1}"
  local host_tool=""
  host_tool="$(command -v "$tool_name" || true)"
  if [[ -z "$host_tool" || ! -x "$host_tool" ]]; then
    if [[ "$required" == "1" ]]; then
      echo "[run_repo_release_gate][FAIL] 缺少宿主机命令：$tool_name；请先执行 Docker host/base tools 准备。" >&2
      exit 2
    fi
    return 0
  fi
  ensure_tool_overlay_dir
  cp -L "$host_tool" "$TOOL_OVERLAY_DIR/bin/$tool_name"
  chmod 755 "$TOOL_OVERLAY_DIR/bin/$tool_name"
  copy_tool_runtime_deps "$host_tool"
}

add_host_tool_overlay jq 1
if [[ "$WITH_DOCKER_SOCK" == '1' && -S /var/run/docker.sock ]]; then
  add_host_tool_overlay docker 1
  STATIC_RUNNER_ARGS+=(--mount /var/run/docker.sock)
elif [[ "$WITH_DOCKER_SOCK" == '1' ]]; then
  echo '[run_repo_release_gate][FAIL] --with-docker-sock 已指定，但 /var/run/docker.sock 不存在或不是 socket' >&2
  exit 2
fi
if ((${#ARGS[@]})); then
  bash "$STATIC_PYTHON_RUNNER" "${STATIC_RUNNER_ARGS[@]}" -- -m openclaw.doctor.release.repo_release_gate "${ARGS[@]}"
else
  bash "$STATIC_PYTHON_RUNNER" "${STATIC_RUNNER_ARGS[@]}" -- -m openclaw.doctor.release.repo_release_gate
fi
