#!/usr/bin/env bash
# 用途：统一处理主入口脚本的 help/explain/未知参数帮助面切换，避免重复维护 Docker 检测与静态帮助分流。
set -euo pipefail

flow_entry_has_docker() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1
}

flow_entry_run_static_surface() {
  local mode="$1" help_func="$2" explain_func="$3"
  case "$mode" in
    help) "$help_func" ;;
    explain) "$explain_func" ;;
    *)
      echo "[flow_entry_surface][FAIL] 未知帮助面模式：$mode" >&2
      return 2
      ;;
  esac
}

flow_entry_exec_dynamic_command() {
  local command_path="$1"
  shift
  if [[ "$command_path" == *.sh && -f "$command_path" ]]; then
    OPENCLAW_PYTHON_TOOL_NO_PULL=1 bash "$command_path" "$@"
    return $?
  fi
  OPENCLAW_PYTHON_TOOL_NO_PULL=1 "$command_path" "$@"
}

flow_entry_run_dynamic_or_static_surface() {
  local mode="$1" help_func="$2" explain_func="$3"
  shift 3
  if flow_entry_has_docker; then
    if flow_entry_exec_dynamic_command "$@"; then
      return 0
    fi
    echo "[flow_entry_surface][WARN] 动态帮助面当前不可用，使用静态说明作为后备；帮助面与执行面边界统一查看 docs/getting-started/quickstart.md。" >&2
  fi
  flow_entry_run_static_surface "$mode" "$help_func" "$explain_func"
}

flow_entry_maybe_render_static_surface() {
  local help_only="$1" explain_only="$2" help_func="$3" explain_func="$4"
  if [[ "$help_only" == "1" ]]; then
    "$help_func"
    return 0
  fi
  if [[ "$explain_only" == "1" ]]; then
    "$explain_func"
    return 0
  fi
  return 1
}

flow_entry_handle_unknown_arg() {
  local prefix="$1" arg="$2" help_func="$3"
  shift 3
  echo "[$prefix] 未知参数：$arg" >&2
  if (($# > 0)) && flow_entry_has_docker; then
    if flow_entry_exec_dynamic_command "$@" >&2; then
      return 2
    fi
    echo "[flow_entry_surface][WARN] 动态帮助面当前不可用，使用静态说明作为后备；帮助面与执行面边界统一查看 docs/getting-started/quickstart.md。" >&2
  fi
  "$help_func" >&2
  return 2
}
