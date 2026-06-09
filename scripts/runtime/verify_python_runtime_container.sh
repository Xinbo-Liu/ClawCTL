#!/usr/bin/env bash
# 用途：校验仓库中的 Python 执行入口是否统一收口到容器化 Python 3.11。
# 模式：
#   - 默认：先做静态检查；若存在 docker，则继续做容器实跑版本检查。
#   - --static-only：只做静态检查，不要求本机提供 docker。
#   - --runtime-only：跳过静态半程，只做容器内版本检查（供 release gate 在 fast lint 已通过后复用）。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/lib/image_env.sh"
source "$ROOT_DIR/scripts/lib/python_runtime_guard.sh"
image_env_load

STATIC_ONLY=0
RUNTIME_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --static-only) STATIC_ONLY=1 ;;
    --runtime-only) RUNTIME_ONLY=1 ;;
    -h|--help)
      cat <<'USAGE'
用法：
  ./scripts/runtime/verify_python_runtime_container.sh [--static-only|--runtime-only]

说明：
  默认先做静态收口检查；若本机存在 docker，则继续验证容器内 Python 主版本/次版本。
  传入 --static-only 时，仅检查接线是否都已回收到容器入口，不要求 docker 可用。
  传入 --runtime-only 时，跳过静态半程，仅验证容器内 Python 版本（用于 release gate 避免重复跑静态治理）。
USAGE
      exit 0
      ;;
    *) echo "[python_runtime_verify] 未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done

fail() {
  echo "[python_runtime_verify][FAIL] $1" >&2
  exit "${2:-2}"
}

note() {
  echo "[python_runtime_verify] $1"
}

verify_scheduler_source_mount_consistency() {
  local container_name="${OPENCLAW_CONTROL_PLANE_SCHEDULER_CONTAINER_NAME:-openclaw-control-plane-scheduler}"
  local container_tools_root="${OPENCLAW_CONTROL_PLANE_CONTAINER_TOOLS_ROOT:-/opt/openclaw-tools}"
  local running=""
  local host_manifest=""
  local container_manifest=""
  local consistency_roots=(scripts python agent config docs)
  local find_prune=(
    -path '*/__pycache__/*' -o
    -path '*/.pytest_cache/*' -o
    -path '*/.mypy_cache/*' -o
    -path '*/.ruff_cache/*' -o
    -name '*.pyc' -o
    -name '*.pyo'
  )

  running="$(docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null || true)"
  if [[ "$running" != "true" ]]; then
    note "scheduler 容器未运行，跳过容器内外源码一致性校验"
    return 0
  fi

  host_manifest="$(mktemp)"
  container_manifest="$(mktemp)"
  (
    cd "$ROOT_DIR"
    find "${consistency_roots[@]}" \( "${find_prune[@]}" \) -prune -o -type f -print \
      | LC_ALL=C sort \
      | xargs -r sha256sum
  ) >"$host_manifest"
  docker exec "$container_name" sh -lc "
    set -e
    cd '$container_tools_root'
    find scripts python agent config docs \\( -path '*/__pycache__/*' -o -path '*/.pytest_cache/*' -o -path '*/.mypy_cache/*' -o -path '*/.ruff_cache/*' -o -name '*.pyc' -o -name '*.pyo' \\) -prune -o -type f -print \
      | LC_ALL=C sort \
      | xargs -r sha256sum
  " >"$container_manifest" || {
    rm -f "$host_manifest" "$container_manifest"
    fail "无法读取 scheduler 容器内源码视图：$container_name:$container_tools_root" 5
  }

  if ! diff -u "$host_manifest" "$container_manifest" >/tmp/openclaw_scheduler_source_mount_diff.$$; then
    cat /tmp/openclaw_scheduler_source_mount_diff.$$ >&2
    rm -f "$host_manifest" "$container_manifest" /tmp/openclaw_scheduler_source_mount_diff.$$
    fail "scheduler 容器内外源码不一致；请重建 scheduler 容器后重试" 5
  fi
  rm -f "$host_manifest" "$container_manifest" /tmp/openclaw_scheduler_source_mount_diff.$$
  note "scheduler 容器内外源码一致性校验通过"
}

note "检查 Python 镜像来源与容器入口"
[[ -f "$ROOT_DIR/scripts/runtime/run_python_container.sh" ]] || fail "缺少 scripts/runtime/run_python_container.sh" 2
if [[ "$RUNTIME_ONLY" != "1" ]]; then
  RUNTIME_CONTRACT_ERROR=""
  if ! RUNTIME_CONTRACT_ERROR="$(python_runtime_guard_verify_runtime_image_contract "$ROOT_DIR")"; then
    fail "${RUNTIME_CONTRACT_ERROR:-运行时镜像 pin / compose 严格引用校验失败}" 2
  fi
fi
[[ "$OPENCLAW_CONTROL_PLANE_IMAGE" == *'python:3.11.'* || "$OPENCLAW_CONTROL_PLANE_IMAGE" == *'python:3.11-'* ]] || fail "当前加载的 OPENCLAW_CONTROL_PLANE_IMAGE 不是 Python 3.11 系列：$OPENCLAW_CONTROL_PLANE_IMAGE" 2

RUNNER_BINDING_ERROR=""
if ! RUNNER_BINDING_ERROR="$(python_runtime_guard_verify_runner_binding_contract "$ROOT_DIR")"; then
  fail "${RUNNER_BINDING_ERROR:-PYTHON_RUNNER 固定绑定容器入口校验失败}" 3
fi
RUNNER_SURFACE_ERROR=""
if ! RUNNER_SURFACE_ERROR="$(python_runtime_guard_verify_runner_surface_contract "$ROOT_DIR")"; then
  fail "${RUNNER_SURFACE_ERROR:-run_python_container.sh surface manifest 校验失败}" 3
fi
AGENT_LAUNCHER_ERROR=""
if ! AGENT_LAUNCHER_ERROR="$(python_runtime_guard_verify_agent_launcher_contract "$ROOT_DIR")"; then
  fail "${AGENT_LAUNCHER_ERROR:-agent 薄启动器容器入口校验失败}" 3
fi
AGENT_README_ERROR=""
if ! AGENT_README_ERROR="$(python_runtime_guard_verify_agent_readme_contract "$ROOT_DIR")"; then
  fail "${AGENT_README_ERROR:-agent 文档入口校验失败}" 3
fi
grep -Fq 'runtime paths render-generated' "$ROOT_DIR/scripts/setup/bootstrap.sh" || fail "bootstrap.sh 未通过 runtime paths render-generated 收口路径派生产物" 3

if [[ "$STATIC_ONLY" == "1" && "$RUNTIME_ONLY" == "1" ]]; then
  fail "--static-only 与 --runtime-only 不能同时使用" 2
fi

if [[ "$RUNTIME_ONLY" != "1" ]]; then
  HOST_PY_STATIC_OUTPUT=""
  HOST_PY_STATIC_STATUS=0
  set +e
  HOST_PY_STATIC_OUTPUT="$(bash "$ROOT_DIR/scripts/doctor/check_host_python_governance.sh" 2>&1)"
  HOST_PY_STATIC_STATUS=$?
  set -e
  if [[ "$HOST_PY_STATIC_STATUS" != "0" ]]; then
    [[ -z "$HOST_PY_STATIC_OUTPUT" ]] || echo "$HOST_PY_STATIC_OUTPUT" >&2
    fail "发现 shell 或正式文档仍残留宿主机 Python 执行/暴露面" 4
  fi

  mapfile -t DELETED_STUB_SCAN_ROOTS < <(
    while IFS= read -r rel_root; do
      [[ -n "$rel_root" ]] || continue
      [[ -e "$ROOT_DIR/$rel_root" ]] || continue
      printf '%s\n' "$ROOT_DIR/$rel_root"
    done < <(python_runtime_guard_iter_deleted_stub_ref_scan_roots)
  )
  HOST_STUB_REF="$(python_runtime_guard_search_fixed_in_paths 'python_runner_host_stub.sh' "${DELETED_STUB_SCAN_ROOTS[@]}" | grep -Fv "$ROOT_DIR/scripts/runtime/verify_python_runtime_container.sh:" || true)"
  [[ -z "$HOST_STUB_REF" ]] || { echo "$HOST_STUB_REF" >&2; fail "发现活动真源仍引用不支持的宿主机 Python stub" 4; }

  if [[ "$STATIC_ONLY" == "1" ]]; then
    note "静态检查通过（按要求未进行容器实跑验证）"
    exit 0
  fi
else
  note "跳过静态半程，直接执行容器内 Python 版本验证"
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "未检测到 docker；默认模式必须执行容器内 Python 版本验证。" 5
fi

verify_scheduler_source_mount_consistency

note "验证容器内 Python 版本"
VERSION_OUT="$(bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" --workdir "$ROOT_DIR" -- - <<'PY'
import platform
import sys
print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
print(platform.python_implementation())
PY
)"
PY_VERSION="$(printf '%s\n' "$VERSION_OUT" | sed -n '1p')"
PY_IMPL="$(printf '%s\n' "$VERSION_OUT" | sed -n '2p')"
[[ "$PY_VERSION" == 3.11.* ]] || fail "容器内 Python 版本不是 3.11.x：$PY_VERSION" 5
[[ "$PY_IMPL" == 'CPython' ]] || fail "容器内 Python 实现不是 CPython：$PY_IMPL" 5

note "验证 runner stdin 边界"
STDIN_PROBE_FILE="$(mktemp)"
cleanup_stdin_probe() {
  rm -f "$STDIN_PROBE_FILE"
}
trap cleanup_stdin_probe EXIT
STDIN_TAIL="$(
  printf 'openclaw-stdin-sentinel\n' | {
    bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" --workdir "$ROOT_DIR" -- -c 'print("runner-ok")' >"$STDIN_PROBE_FILE"
    cat
  }
)"
STDIN_PROBE_OUT="$(cat "$STDIN_PROBE_FILE")"
[[ "$STDIN_PROBE_OUT" == 'runner-ok' ]] || fail "runner 普通命令输出异常：$STDIN_PROBE_OUT" 5
[[ "$STDIN_TAIL" == 'openclaw-stdin-sentinel' ]] || fail "runner 普通命令吞掉了调用者 stdin，调用者管道剩余内容不可读" 5

STDIN_DATA_OUT="$(
  printf 'openclaw-stdin-data\n' | bash "$ROOT_DIR/scripts/runtime/run_python_container.sh" --workdir "$ROOT_DIR" --stdin -- -c 'import sys; print(sys.stdin.read().strip())'
)"
[[ "$STDIN_DATA_OUT" == 'openclaw-stdin-data' ]] || fail "runner --stdin 未能把调用者 stdin 传入容器：$STDIN_DATA_OUT" 5
cleanup_stdin_probe
trap - EXIT

note "容器内 Python 版本与 stdin 边界验证通过：$PY_VERSION ($PY_IMPL)"
