#!/usr/bin/env bash
# 用途：为 one_click_test_full 提供 control plane 默认值、检查组调度与 acceptance 写状态 helper，避免主脚本继续内联 acceptance 前流程。
set -euo pipefail

FULL_TEST_ACCEPTANCE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$FULL_TEST_ACCEPTANCE_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$FULL_TEST_ACCEPTANCE_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
FULL_TEST_ACCEPTANCE_ROOT="$(openclaw_repo_root_from "$FULL_TEST_ACCEPTANCE_LIB_DIR")"
# shellcheck source=scripts/lib/flow_sequence_shell.sh
source "$FULL_TEST_ACCEPTANCE_ROOT/scripts/lib/flow_sequence_shell.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$FULL_TEST_ACCEPTANCE_ROOT/scripts/lib/repo_contracts.sh"
# shellcheck source=host_install_defaults.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/host_install_defaults.sh"
unset FULL_TEST_ACCEPTANCE_LIB_DIR FULL_TEST_ACCEPTANCE_ROOT

full_test_host_state_root_default_abs() {
  local state_root=''
  state_root="$(host_install_defaults_state_root_default)" || return $?
  case "$state_root" in
    /*) printf '%s\n' "$state_root" ;;
    *) printf '%s\n' "$ROOT_DIR/$state_root" ;;
  esac
}

full_test_runtime_path_default() {
  local entry_id="$1"
  local state_root='' control_plane_root='' setup_root=''
  state_root="$(full_test_host_state_root_default_abs)" || return $?
  control_plane_root="$state_root/control_plane"
  setup_root="$control_plane_root/setup"
  case "$entry_id" in
    runtime_host_env) printf '%s\n' "$control_plane_root/runtime.host.env" ;;
    logs_dir) printf '%s\n' "$control_plane_root/logs" ;;
    deployment_acceptance_state|deployment_acceptance_setup_json) printf '%s\n' "$setup_root/deployment_acceptance.json" ;;
    one_click_test_full_latest_summary_json) printf '%s\n' "$setup_root/one_click_test_full.latest.summary.json" ;;
    one_click_test_full_latest_summary_markdown) printf '%s\n' "$setup_root/one_click_test_full.latest.summary.md" ;;
    *) return 1 ;;
  esac
}

full_test_static_preflight() {
  local rel=''
  local required_files=(
    'scripts/runtime/run_openclaw_python_tool.sh'
    'scripts/lib/flow_entry_surface_shell.sh'
    'scripts/lib/flow_preflight_shell.sh'
    'scripts/lib/flow_sequence_shell.sh'
    'scripts/setup/lib/setup_cli_common.sh'
    'scripts/setup/lib/test_gate_common.sh'
    'scripts/setup/lib/full_test_env_shell.sh'
    'scripts/setup/lib/full_test_group_registry.sh'
    "$(repo_contract_relpath governance.full_test_group_registry)"
    'scripts/setup/lib/full_test_summary_shell.sh'
    'scripts/setup/lib/full_test_acceptance_shell.sh'
    'scripts/setup/lib/full_test_group_runner.sh'
    'scripts/runtime/runtime_container_lib.sh'
    'scripts/lib/run_summary_shell.sh'
    'scripts/lib/flow_summary_common_shell.sh'
    "$(repo_contract_relpath runtime.testing_manifest)"
    "$(repo_contract_relpath governance.summary_manifest)"
    "$(repo_contract_relpath governance.setup_entrypoints)"
  )
  for rel in "${required_files[@]}"; do
    [[ -f "$ROOT_DIR/$rel" ]] || die "缺少必要文件：$rel" 3
  done
}

full_test_load_control_plane_defaults() {
  DEFAULT_ENV_FILE="$ROOT_DIR/deploy/.env"
  [[ -n "$ENV_FILE" ]] || ENV_FILE="$DEFAULT_ENV_FILE"
  DEPLOYMENT_ACCEPTANCE_STATE_PATH="$(full_test_runtime_path_default deployment_acceptance_state)"
  FULL_TEST_SURFACE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow full-test-surface)
  FULL_TEST_LOG_DIR="$(full_test_runtime_path_default logs_dir)"
  FULL_TEST_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
  FULL_TEST_SUMMARY_JSON_PATH="$FULL_TEST_LOG_DIR/one_click_test_full-${FULL_TEST_RUN_ID}.summary.json"
  FULL_TEST_SUMMARY_MD_PATH="$FULL_TEST_LOG_DIR/one_click_test_full-${FULL_TEST_RUN_ID}.summary.md"
}

full_test_preflight_and_load_control_plane_defaults() {
  full_test_static_preflight
  full_test_load_control_plane_defaults
}

full_test_run_selected_groups() {
  local ordered_groups=()
  local manifest_json=''
  if declare -F full_test_testing_manifest_json >/dev/null 2>&1 && manifest_json="$(full_test_testing_manifest_json 2>/dev/null)"; then
    if [[ "$GROUP" == 'all' ]]; then
      mapfile -t ordered_groups < <(jq -r '.execution_order[]? // empty' <<<"$manifest_json")
    else
      ordered_groups=("$GROUP")
    fi
  fi
  if ((${#ordered_groups[@]} == 0)); then
    flow_sequence_load_lines ordered_groups "${FULL_TEST_SURFACE_CMD[@]}" group-order --group-name "$GROUP" --format lines
  fi
  flow_sequence_run_array ordered_groups full_test_run_group_by_name
}

full_test_evaluate_deployment_acceptance() {
  : > "$RESULT_LINES_FILE"
  if ((${#RESULT_LINES[@]} > 0)); then
    printf '%s\n' "${RESULT_LINES[@]+"${RESULT_LINES[@]}"}" > "$RESULT_LINES_FILE"
  fi
  local acceptance_kv=''
  acceptance_kv="$("${FULL_TEST_SURFACE_CMD[@]}" acceptance-status --group "$GROUP" --only "$ONLY_RAW" --skip "$SKIP_RAW" --result-lines-file "$RESULT_LINES_FILE" --format kv-lines)"
  local kv_line='' kv_key='' kv_value=''
  while IFS= read -r kv_line; do
    [[ -n "$kv_line" ]] || continue
    kv_key="${kv_line%%=*}"
    kv_value="${kv_line#*=}"
    case "$kv_key" in
      FULL_TEST_ACCEPTANCE_ELIGIBLE) FULL_TEST_ACCEPTANCE_ELIGIBLE="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_ACCEPTED) FULL_TEST_ACCEPTANCE_ACCEPTED="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_CONTRACT_ID) FULL_TEST_ACCEPTANCE_CONTRACT_ID="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_CONTRACT_GROUP) FULL_TEST_ACCEPTANCE_CONTRACT_GROUP="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_CONTRACT_STATUS) FULL_TEST_ACCEPTANCE_CONTRACT_STATUS="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL) FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_CONTRACT_ACTION) FULL_TEST_ACCEPTANCE_CONTRACT_ACTION="$kv_value" ;;
      FULL_TEST_ACCEPTANCE_REQUIRED_CHECKS) FULL_TEST_ACCEPTANCE_REQUIRED_CHECKS="$kv_value" ;;
    esac
  done <<< "$acceptance_kv"

  case "$FULL_TEST_ACCEPTANCE_CONTRACT_STATUS" in
    PASS)
      record_pass "$FULL_TEST_ACCEPTANCE_CONTRACT_ID" "$FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL" "$FULL_TEST_ACCEPTANCE_CONTRACT_GROUP"
      ;;
    FAIL)
      record_fail "$FULL_TEST_ACCEPTANCE_CONTRACT_ID" "$FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL" "$FULL_TEST_ACCEPTANCE_CONTRACT_GROUP"
      [[ -n "$FULL_TEST_ACCEPTANCE_CONTRACT_ACTION" ]] && append_action "$FULL_TEST_ACCEPTANCE_CONTRACT_ACTION"
      ;;
    *)
      record_skip "$FULL_TEST_ACCEPTANCE_CONTRACT_ID" "$FULL_TEST_ACCEPTANCE_CONTRACT_DETAIL" "$FULL_TEST_ACCEPTANCE_CONTRACT_GROUP"
      ;;
  esac

  "${FULL_TEST_SURFACE_CMD[@]}" write-acceptance-state \
    --out-json "$DEPLOYMENT_ACCEPTANCE_STATE_PATH" \
    --generated-at "$GENERATED_AT" \
    --env-file "$ENV_FILE" \
    --group "$GROUP" \
    --only "$ONLY_RAW" \
    --skip "$SKIP_RAW" \
    --result-lines-file "$RESULT_LINES_FILE"
}
