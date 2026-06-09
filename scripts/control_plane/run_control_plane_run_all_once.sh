#!/usr/bin/env bash
# 用途：在 scheduler 容器内受控执行一次 run-all-once，作为唯一人工全链触发入口。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/control_plane/run_control_plane_run_all_once.sh

说明：
  - 该脚本会进入 scheduler 容器，执行一次 control-plane run-all-once；
  - 当前脚本不接受业务参数；如只查看说明，请使用 --help。
USAGE
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[run_control_plane_run_all_once][FAIL] 未知参数：$1" >&2
      exit 2
      ;;
  esac
fi

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
RUNNER="$ROOT_DIR/scripts/runtime/run_runtime_container_command.sh"
[[ -f "$RUNNER" && -r "$RUNNER" ]] || { echo "[run_control_plane_run_all_once][FAIL] 缺少统一容器入口：$RUNNER" >&2; exit 2; }

RUN_ALL_ONCE_MAX_ATTEMPTS="${OPENCLAW_RUN_ALL_ONCE_MAX_ATTEMPTS:-12}"
RUN_ALL_ONCE_RETRY_SLEEP_SECONDS="${OPENCLAW_RUN_ALL_ONCE_RETRY_SLEEP_SECONDS:-10}"
[[ "$RUN_ALL_ONCE_MAX_ATTEMPTS" =~ ^[0-9]+$ && "$RUN_ALL_ONCE_MAX_ATTEMPTS" -ge 1 ]] || {
  echo "[run_control_plane_run_all_once][FAIL] OPENCLAW_RUN_ALL_ONCE_MAX_ATTEMPTS 必须为 >=1 的整数" >&2
  exit 2
}
[[ "$RUN_ALL_ONCE_RETRY_SLEEP_SECONDS" =~ ^[0-9]+$ && "$RUN_ALL_ONCE_RETRY_SLEEP_SECONDS" -ge 1 ]] || {
  echo "[run_control_plane_run_all_once][FAIL] OPENCLAW_RUN_ALL_ONCE_RETRY_SLEEP_SECONDS 必须为 >=1 的整数" >&2
  exit 2
}

attempt=1
while [[ "$attempt" -le "$RUN_ALL_ONCE_MAX_ATTEMPTS" ]]; do
  output_path="$(mktemp)"
  if bash "$RUNNER" --target scheduler -- /opt/openclaw-tools/scripts/runtime/container_openclaw_cli control-plane scheduler-runtime --run-all-once >"$output_path" 2>&1; then
    cat "$output_path"
    rm -f "$output_path"
    exit 0
  fi
  rc=$?
  cat "$output_path" >&2
  if [[ "$rc" -eq 5 ]] && grep -q 'scheduler cycle lock busy' "$output_path" && [[ "$attempt" -lt "$RUN_ALL_ONCE_MAX_ATTEMPTS" ]]; then
    echo "[run_control_plane_run_all_once][INFO] scheduler cycle lock busy，${RUN_ALL_ONCE_RETRY_SLEEP_SECONDS}s 后重试（${attempt}/${RUN_ALL_ONCE_MAX_ATTEMPTS}）" >&2
    rm -f "$output_path"
    sleep "$RUN_ALL_ONCE_RETRY_SLEEP_SECONDS"
    attempt=$((attempt + 1))
    continue
  fi
  rm -f "$output_path"
  exit "$rc"
done
