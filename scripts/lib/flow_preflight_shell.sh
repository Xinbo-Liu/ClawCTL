#!/usr/bin/env bash
# 用途：统一承接 deploy/release/test 主入口的静态 preflight 调用与 control plane 默认值加载，避免重复维护“先 preflight 再 load defaults”模式。
set -euo pipefail

flow_preflight_run() {
  local python_tool="$1"
  shift
  bash "$python_tool" "$@" >/dev/null
}

flow_preflight_run_and_load() {
  local loader="$1"
  local python_tool="$2"
  shift 2
  flow_preflight_run "$python_tool" "$@"
  "$loader"
}
