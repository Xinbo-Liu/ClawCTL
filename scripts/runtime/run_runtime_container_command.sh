#!/usr/bin/env bash
# 用途：统一在仓库约定的运行容器内执行命令，避免脚本与文档各自硬编码 docker exec + 容器名。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
# shellcheck source=./runtime_container_lib.sh
source "$ROOT_DIR/scripts/runtime/runtime_container_lib.sh"

TARGET=""
CONTAINER_NAME=""
TTY='0'
SHELL_COMMAND=""
PASSTHROUGH_ARGS=()

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_runtime_container_command.sh --target <target> -- <argv...>
  bash ./scripts/runtime/run_runtime_container_command.sh --target <...> --shell '<shell command>'
  bash ./scripts/runtime/run_runtime_container_command.sh --container <container_name> -- <argv...>

说明：
  - 统一把运行容器选择、存在性检查与 `docker exec` 调用收口到一处；
  - 默认使用 `docker exec -i`；传入 `--tty` 且当前终端可分配 TTY 时，会自动追加 `-t`；
  - `--shell` 会通过 `sh -lc` 执行整段命令，适合带 pipe / grep / sed 的排障语句。

选项：
  --target <alias>               仓库约定容器别名；base 默认 target 为 gateway / ingress / internal-api / scheduler，扩展 profile 可额外追加 target
  --container <container_name>   直接指定容器名
  --shell '<command>'            通过容器内 `sh -lc` 执行
  --tty                          在当前终端支持时追加 `-t`
  -h, --help                     显示帮助
USAGE
}

fail() {
  echo "[run_runtime_container_command][FAIL] $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail '--target 缺少参数'
      TARGET="$2"
      shift 2
      ;;
    --container)
      [[ $# -ge 2 ]] || fail '--container 缺少参数'
      CONTAINER_NAME="$2"
      shift 2
      ;;
    --tty)
      TTY='1'
      shift
      ;;
    --shell)
      [[ $# -ge 2 ]] || fail '--shell 缺少参数'
      SHELL_COMMAND="$2"
      shift 2
      ;;
    --)
      shift
      PASSTHROUGH_ARGS=("$@")
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

if [[ -n "$TARGET" && -n "$CONTAINER_NAME" ]]; then
  fail '--target 与 --container 只能二选一'
fi
if [[ -n "$TARGET" ]]; then
  CONTAINER_NAME="$(runtime_container_name_for_target "$TARGET")" || fail "不支持的 --target：$TARGET"
fi
[[ -n "$CONTAINER_NAME" ]] || fail '必须提供 --target 或 --container'

if [[ -n "$SHELL_COMMAND" ]]; then
  [[ ${#PASSTHROUGH_ARGS[@]} -eq 0 ]] || fail '--shell 与 -- 后命令不能同时使用'
  if [[ "$TTY" == '1' ]]; then
    runtime_container_exec --tty --container "$CONTAINER_NAME" --shell "$SHELL_COMMAND"
    exit $?
  fi
  runtime_container_exec --container "$CONTAINER_NAME" --shell "$SHELL_COMMAND"
  exit $?
fi

[[ ${#PASSTHROUGH_ARGS[@]} -gt 0 ]] || fail '必须提供 --shell 或 -- 后命令'
if [[ "$TTY" == '1' ]]; then
  runtime_container_exec --tty --container "$CONTAINER_NAME" -- "${PASSTHROUGH_ARGS[@]}"
  exit $?
fi
runtime_container_exec --container "$CONTAINER_NAME" -- "${PASSTHROUGH_ARGS[@]}"
