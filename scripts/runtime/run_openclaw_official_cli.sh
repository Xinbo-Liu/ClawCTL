#!/usr/bin/env bash
# 用途：统一在官方 Gateway 运行容器内执行 OpenClaw 官方 CLI。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"

TARGET=""
CONTAINER_NAME=""

# 输出当前脚本的用法说明。
usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_openclaw_official_cli.sh --target gateway -- <openclaw 子命令参数>
  bash ./scripts/runtime/run_openclaw_official_cli.sh --container <container_name> -- <openclaw 子命令参数>
USAGE
}

# 统一输出失败信息并以指定状态码退出。
fail() {
  echo "[run_openclaw_official_cli][FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail "--target 缺少参数"
      TARGET="$2"
      shift 2
      ;;
    --container)
      [[ $# -ge 2 ]] || fail "--container 缺少参数"
      CONTAINER_NAME="$2"
      shift 2
      ;;
    --)
      shift
      break
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

[[ $# -gt 0 ]] || fail "必须在 -- 后提供 openclaw 子命令参数"
[[ -z "$TARGET" || -z "$CONTAINER_NAME" ]] || fail "--target 与 --container 只能二选一"
if [[ -n "$TARGET" ]]; then
  CONTAINER_NAME="$(runtime_container_name_for_target "$TARGET")" || fail "不支持的 --target：$TARGET"
fi
[[ -n "$CONTAINER_NAME" ]] || fail "必须提供 --target 或 --container"

runtime_container_require_docker >/dev/null
runtime_container_exec --container "$CONTAINER_NAME" -- openclaw "$@"
