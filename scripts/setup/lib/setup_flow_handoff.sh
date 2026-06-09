#!/usr/bin/env bash
# 用途：为宿主机准备 / readiness 等部署前阶段提供统一 handoff 提示；禁止脚本硬编码 one_click 主链顺序。

SETUP_FLOW_HANDOFF_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$SETUP_FLOW_HANDOFF_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$SETUP_FLOW_HANDOFF_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
SETUP_FLOW_HANDOFF_ROOT="$(openclaw_repo_root_from "$SETUP_FLOW_HANDOFF_LIB_DIR")"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$SETUP_FLOW_HANDOFF_ROOT/scripts/lib/repo_contracts.sh"
unset SETUP_FLOW_HANDOFF_LIB_DIR
repo_contract_assign_path SETUP_FLOW_HANDOFF_CONFIG_PATH governance.setup_handoff

setup_flow_handoff_config_get() {
  local path_expr="$1"
  local default_value="${2:-}"
  if command -v jq >/dev/null 2>&1 && [[ -f "$SETUP_FLOW_HANDOFF_CONFIG_PATH" ]]; then
    local jq_filter='.' part=''
    IFS='.' read -r -a __setup_handoff_parts <<< "$path_expr"
    for part in "${__setup_handoff_parts[@]}"; do
      jq_filter+="[\"$part\"]"
    done
    local value=''
    value="$(jq -r "$jq_filter // empty" "$SETUP_FLOW_HANDOFF_CONFIG_PATH" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return 0
    fi
  fi
  printf '%s' "$default_value"
}

setup_flow_handoff_reference_lines() {
  if command -v jq >/dev/null 2>&1 && [[ -f "$SETUP_FLOW_HANDOFF_CONFIG_PATH" ]]; then
    jq -r '.references[]? | select((.label // "") != "" and (.path // "") != "") | "\(.label)：\(.path)"' "$SETUP_FLOW_HANDOFF_CONFIG_PATH" 2>/dev/null || true
    return 0
  fi
  printf '%s\n' '统一部署基线与执行位置：docs/getting-started/quickstart.md'
}

setup_flow_print_unified_handoff() {
  local phase_label="$1"
  local status_label="${2:-已完成}"
  local template='' rendered='' line=''
  template="$(setup_flow_handoff_config_get phase_message_template '{phaseLabel}{statusLabel}；后续动作请回到统一部署基线，不在当前脚本中硬编码下一步命令。')"
  rendered="${template//\{phaseLabel\}/$phase_label}"
  rendered="${rendered//\{statusLabel\}/$status_label}"
  echo "[INFO] ${rendered}"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    echo "[INFO] $line"
  done < <(setup_flow_handoff_reference_lines)
}
