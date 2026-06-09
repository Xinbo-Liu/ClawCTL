#!/usr/bin/env bash
# 用途：统一查看运行容器日志，避免文档与脚本各自硬编码 docker logs + 容器名。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"
source "$ROOT_DIR/scripts/runtime/runtime_docker_lib.sh"

TARGETS=()
CONTAINERS=()
LINES='200'
FOLLOW='0'

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway
  bash ./scripts/runtime/show_runtime_container_logs.sh --target gateway --lines 80
  bash ./scripts/runtime/show_runtime_container_logs.sh --target ingress --follow

说明：
  - 默认输出最近 200 行日志；
  - `--target` 与 `--container` 可二选一并可重复；
  - `--follow` 仅支持单个 target / container；
  - 当前维护的 target 由 runtime service registry 决定；base 默认包含 `gateway / ingress / internal-api / scheduler`，启用扩展后会追加 extension target。

选项：
  --target <alias>               仓库约定 target 别名，可重复传入
  --container <name>             直接指定容器名，可重复传入
  --lines <n>                    日志行数，默认 200
  --follow                       持续跟踪日志
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[show_runtime_container_logs][FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail '--target 缺少参数'
      TARGETS+=("$2")
      shift 2
      ;;
    --container)
      [[ $# -ge 2 ]] || fail '--container 缺少参数'
      CONTAINERS+=("$2")
      shift 2
      ;;
    --lines)
      [[ $# -ge 2 ]] || fail '--lines 缺少参数'
      LINES="$2"
      shift 2
      ;;
    --follow)
      FOLLOW='1'
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数：$1"
      ;;
  esac
done

runtime_container_require_docker >/dev/null
[[ "$LINES" =~ ^[0-9]+$ ]] || fail '--lines 必须为非负整数'
if [[ ${#TARGETS[@]} -gt 0 && ${#CONTAINERS[@]} -gt 0 ]]; then
  fail '--target 与 --container 只能二选一'
fi
if [[ ${#TARGETS[@]} -eq 0 && ${#CONTAINERS[@]} -eq 0 ]]; then
  fail '必须至少提供一个 --target 或 --container'
fi

if [[ ${#TARGETS[@]} -gt 0 ]]; then
  for target in "${TARGETS[@]}"; do
    CONTAINERS+=("$(runtime_container_name_for_target "$target")") || fail "不支持的 --target：$target"
  done
fi
mapfile -t CONTAINERS < <(printf '%s\n' "${CONTAINERS[@]}" | runtime_target_dedupe_lines)

if [[ "$FOLLOW" == '1' && ${#CONTAINERS[@]} -ne 1 ]]; then
  fail '--follow 仅支持单个 target / container'
fi

for container_name in "${CONTAINERS[@]}"; do
  runtime_container_require_exists "$container_name" >/dev/null
  echo "== logs: $container_name =="
  if [[ "$FOLLOW" == '1' ]]; then
    runtime_docker_logs --target "$container_name" --lines "$LINES" --follow
    exit $?
  fi
  runtime_docker_logs --target "$container_name" --lines "$LINES"
  if [[ ${#CONTAINERS[@]} -gt 1 ]]; then
    echo
  fi
done
