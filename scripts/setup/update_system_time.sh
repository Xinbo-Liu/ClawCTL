#!/usr/bin/env bash
# 用途：以 root 收口宿主机时区、NTP 与系统时间漂移，供宿主机准备与故障修复复用。
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
  sudo bash ./scripts/setup/update_system_time.sh [--offline] [--timezone <zone>] [--max-drift-seconds <seconds>] [--min-epoch <epoch>] [--max-epoch <epoch>]

说明：
  - 本脚本需要 root 权限，会设置时区、启用 NTP/chronyd，并在漂移超过阈值时按多源 HTTPS HTTP Date 基准直接校正系统时间。
  - 在线模式必须能访问满足 quorum 的时间参考 URL；可用 OPENCLAW_SYSTEM_TIME_REFERENCE_URLS 覆盖默认参考端点，多个 URL 用空格分隔。
  - --offline 只设置时区和本机 NTP 服务，并执行本机可信时间窗口校验，不做外部 HTTP Date 校时。

选项：
  --timezone <zone>                 设置时区，默认 Asia/Shanghai。
  --offline                         跳过外部 HTTP Date 时间基准比对与直接校时。
  --max-drift-seconds <seconds>     本机时间与外部基准允许的最大漂移秒数，默认 300。
  --min-epoch <epoch>               本机时间最低可信 epoch，默认 2026-01-01T00:00:00Z。
  --max-epoch <epoch>               本机时间最高可信 epoch，默认 2031-01-01T00:00:00Z。
  --max-step-seconds <seconds>      允许直接校正的最大跳变秒数，默认 94608000（约 3 年）。
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
  system_time_guard_update "${args[@]}"
else
  system_time_guard_update
fi
