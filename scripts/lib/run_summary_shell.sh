#!/usr/bin/env bash
# 用途：为 deploy / release / full test 主链提供统一的摘要写出与终端摘要输出桥接，避免各自维护同类 shell 循环。
set -euo pipefail

summary_run_prefix() {
  local prefix_name="$1"
  shift
  case "$prefix_name" in
    DEPLOY_SUCCESS_CMD)
      "${DEPLOY_SUCCESS_CMD[@]}" "$@"
      ;;
    FLOW_FAILURE_CMD)
      "${FLOW_FAILURE_CMD[@]}" "$@"
      ;;
    *)
      echo "[run_summary_shell][FAIL] 未知 summary helper 前缀：$prefix_name" >&2
      return 2
      ;;
  esac
}

summary_write() {
  local prefix_name="$1"
  local command="$2"
  shift 2
  summary_run_prefix "$prefix_name" "$command" "$@"
}

summary_note_paths() {
  local log_func="$1"
  local summary_md_path="$2"
  local summary_json_path="$3"
  "$log_func" "[INFO] 摘要文件：$summary_md_path"
  "$log_func" "[INFO] 机器摘要：$summary_json_path"
}

summary_emit() {
  local log_func="$1"
  local helper="$2"
  local command="$3"
  shift 3
  local line=''
  while IFS= read -r line; do
    if [[ -z "$line" ]]; then
      "$log_func" ""
      continue
    fi
    "$log_func" "[SUMMARY] $line"
  done < <(summary_run_prefix "$helper" "$command" "$@")
}
