#!/usr/bin/env bash
# 用途：只读校验宿主机系统时间是否满足证书、镜像拉取与部署日志排序前提。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=../lib/system_time_guard.sh
source "$ROOT_DIR/scripts/lib/system_time_guard.sh"

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/doctor/check_system_time.sh [--offline] [--max-drift-seconds <seconds>] [--min-epoch <epoch>] [--max-epoch <epoch>]

说明：
  - 本脚本只读检查系统时间，不修改宿主机配置。
  - 在线模式会通过多个 HTTPS HTTP Date 参考源形成时间基准，再与本机 UTC 时间比对；TLS 证书校验使用 -k 仅用于取得时间基准，避免错误本机时钟阻断校验。
  - --offline 只校验本机时间位于可信 epoch 窗口内，跳过外部 HTTP Date 基准。
  - 可用 OPENCLAW_SYSTEM_TIME_REFERENCE_URLS 覆盖默认参考端点，多个 URL 用空格分隔。
  - 修复入口统一使用：sudo bash ./scripts/setup/update_system_time.sh。

选项：
  --offline                         跳过外部 HTTP Date 时间基准比对。
  --max-drift-seconds <seconds>     本机时间与外部基准允许的最大漂移秒数，默认 300。
  --min-epoch <epoch>               本机时间最低可信 epoch，默认 2026-01-01T00:00:00Z。
  --max-epoch <epoch>               本机时间最高可信 epoch，默认 2031-01-01T00:00:00Z。
  --max-step-seconds <seconds>      修复入口允许直接校正的最大跳变秒数，默认 94608000（约 3 年）。
  --min-reference-count <count>     在线模式至少需要的 HTTP Date 参考源数量，默认 2。
  --max-reference-skew-seconds <s>  HTTP Date 参考源之间允许的最大偏差秒数，默认 120。
  -h, --help                        显示帮助。
USAGE
}

args=()
args_count=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      args+=("$1")
      args_count=$((args_count + 1))
      shift
      ;;
  esac
done

if (( args_count > 0 )); then
  system_time_guard_check "${args[@]}"
else
  system_time_guard_check
fi
