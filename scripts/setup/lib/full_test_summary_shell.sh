#!/usr/bin/env bash
# 用途：为 one_click_test_full 提供结果归并、摘要写出与终端输出 helper，避免主脚本继续内联 record_* / summary 流程。
set -euo pipefail

FULL_TEST_SUMMARY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$FULL_TEST_SUMMARY_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$FULL_TEST_SUMMARY_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
FULL_TEST_SUMMARY_ROOT="$(openclaw_repo_root_from "$FULL_TEST_SUMMARY_LIB_DIR")"
# shellcheck source=scripts/lib/flow_summary_common_shell.sh
source "$FULL_TEST_SUMMARY_ROOT/scripts/lib/flow_summary_common_shell.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$FULL_TEST_SUMMARY_ROOT/scripts/lib/repo_contracts.sh"
unset FULL_TEST_SUMMARY_LIB_DIR FULL_TEST_SUMMARY_ROOT

FULL_TEST_CURRENT_CHECK_ID=""
FULL_TEST_CURRENT_CHECK_STARTED_SECONDS=""
FULL_TEST_DETAIL_INLINE_LIMIT="${FULL_TEST_DETAIL_INLINE_LIMIT:-8000}"
repo_contract_assign_path FULL_TEST_SURFACE_PATH governance.full_test_surface
repo_contract_assign_path FULL_TEST_SUMMARY_OUTPUT_SURFACE_PATH governance.summary_output_surface

full_test_named_array_append() {
  local target_name="$1"
  local value="$2"
  case "$target_name" in
    PASS_IDS)
      PASS_IDS+=("$value")
      ;;
    FAIL_IDS)
      FAIL_IDS+=("$value")
      ;;
    WARN_IDS)
      WARN_IDS+=("$value")
      ;;
    SKIP_IDS)
      SKIP_IDS+=("$value")
      ;;
    RESULT_LINES)
      RESULT_LINES+=("$value")
      ;;
    *)
      echo "[full_test_summary][FAIL] 未知结果数组：$target_name" >&2
      return 2
      ;;
  esac
}

full_test_epoch_seconds() {
  date +%s
}

full_test_mark_check_started() {
  local check_id="$1"
  FULL_TEST_CURRENT_CHECK_ID="$check_id"
  FULL_TEST_CURRENT_CHECK_STARTED_SECONDS="$(full_test_epoch_seconds 2>/dev/null || true)"
}

full_test_duration_from_started() {
  local started_at="$1" finished_at="$2"
  if [[ "$started_at" =~ ^[0-9]+$ && "$finished_at" =~ ^[0-9]+$ && "$finished_at" -ge "$started_at" ]]; then
    printf '%s' "$((finished_at - started_at))"
  else
    printf ''
  fi
}

full_test_consume_duration_seconds() {
  local check_id="$1" finished_at='' duration=''
  finished_at="$(full_test_epoch_seconds 2>/dev/null || true)"
  if [[ "$FULL_TEST_CURRENT_CHECK_ID" == "$check_id" ]]; then
    duration="$(full_test_duration_from_started "$FULL_TEST_CURRENT_CHECK_STARTED_SECONDS" "$finished_at")"
    FULL_TEST_CURRENT_CHECK_ID=""
    FULL_TEST_CURRENT_CHECK_STARTED_SECONDS=""
  fi
  if [[ -z "$duration" && "${SETUP_GATE_LAST_DURATION_SECONDS:-}" =~ ^[0-9]+$ ]]; then
    duration="$SETUP_GATE_LAST_DURATION_SECONDS"
  fi
  SETUP_GATE_LAST_DURATION_SECONDS=""
  printf '%s' "$duration"
}

full_test_record_result() {
  local bucket_name="$1"
  local lines_name="$2"
  local status="$3"
  local check_id="$4"
  local detail="${5:-}"
  local group="${6:-}"
  local duration_seconds='' detail_path='' omitted=0 detail_dir=''
  duration_seconds="$(full_test_consume_duration_seconds "$check_id")"
  if [[ -n "$duration_seconds" ]]; then
    if [[ -n "$detail" ]]; then
      detail="[full_test_duration_seconds=${duration_seconds}] $detail"
    else
      detail="[full_test_duration_seconds=${duration_seconds}]"
    fi
  fi
  if [[ "$FULL_TEST_DETAIL_INLINE_LIMIT" =~ ^[1-9][0-9]*$ && ${#detail} -gt "$FULL_TEST_DETAIL_INLINE_LIMIT" ]]; then
    detail_dir="$FULL_TEST_LOG_DIR/check-details/$FULL_TEST_RUN_ID"
    mkdir -p "$detail_dir"
    detail_path="$detail_dir/${check_id}.log"
    printf '%s\n' "$detail" > "$detail_path"
    omitted="$((${#detail} - FULL_TEST_DETAIL_INLINE_LIMIT))"
    detail="${detail:0:FULL_TEST_DETAIL_INLINE_LIMIT}... [detail 已截断 ${omitted} 字符；完整输出见 ${detail_path#"$ROOT_DIR"/}]"
  fi
  detail="${detail//$'\r'/ }"
  detail="${detail//$'\n'/ | }"
  detail="${detail//|/\/}"
  group="${group//$'\r'/ }"
  group="${group//$'\n'/ }"
  group="${group//|/\/}"
  full_test_named_array_append "$bucket_name" "$check_id"
  full_test_named_array_append "$lines_name" "$status|$check_id|$detail|$group"
}

full_test_record_pass() {
  full_test_record_result PASS_IDS RESULT_LINES PASS "$@"
}

full_test_record_fail() {
  full_test_record_result FAIL_IDS RESULT_LINES FAIL "$@"
}

full_test_record_warn() {
  full_test_record_result WARN_IDS RESULT_LINES WARN "$@"
}

full_test_record_skip() {
  full_test_record_result SKIP_IDS RESULT_LINES SKIP "$@"
}

full_test_check_status_by_id() {
  local target_id="$1"
  local line='' status='' id='' detail='' group=''
  for line in "${RESULT_LINES[@]+"${RESULT_LINES[@]}"}"; do
    IFS='|' read -r status id detail group <<<"$line"
    if [[ "$id" == "$target_id" ]]; then
      printf '%s\n' "$status"
      return 0
    fi
  done
  printf 'NOT_RUN\n'
  return 0
}

full_test_runtime_host_env_path() {
  local resolved=''
  if declare -F full_test_runtime_path_default >/dev/null 2>&1; then
    full_test_runtime_path_default runtime_host_env
    return $?
  fi
  if declare -F one_click_test_cp >/dev/null 2>&1; then
    resolved="$(one_click_test_cp runtime-host-env-path 2>/dev/null || true)"
  fi
  [[ -n "$resolved" ]] || {
    echo "[full_test_summary][FAIL] 缺少 runtime-host-env-path 控制面真源" >&2
    return 2
  }
  if [[ "$resolved" = /* ]]; then
    printf '%s' "$resolved"
  else
    printf '%s' "$ROOT_DIR/$resolved"
  fi
}

full_test_summary_output_get() {
  local field="$1" fallback="${2:-}" value=''
  if [[ -f "$FULL_TEST_SUMMARY_OUTPUT_SURFACE_PATH" ]]; then
    value="$(flow_summary_json_get "$FULL_TEST_SUMMARY_OUTPUT_SURFACE_PATH" "profiles.full_test.$field" 2>/dev/null || true)"
  fi
  if [[ -z "$value" && -n "$fallback" ]]; then
    value="$fallback"
  fi
  printf '%s' "$value"
}

full_test_default_log_dir() {
  local resolved=''
  if declare -F full_test_runtime_path_default >/dev/null 2>&1; then
    full_test_runtime_path_default logs_dir
    return $?
  fi
  if declare -F one_click_test_cp >/dev/null 2>&1; then
    resolved="$(one_click_test_cp full-log-dir 2>/dev/null || true)"
  fi
  [[ -n "$resolved" ]] || {
    echo "[full_test_summary][FAIL] 缺少 full-log-dir 控制面真源" >&2
    return 2
  }
  if [[ "$resolved" = /* ]]; then
    printf '%s' "$resolved"
  else
    printf '%s' "$ROOT_DIR/$resolved"
  fi
}

full_test_default_latest_summary_path() {
  local kind="$1"
  local resolved=''
  if declare -F full_test_runtime_path_default >/dev/null 2>&1; then
    if [[ "$kind" == 'json' ]]; then
      full_test_runtime_path_default one_click_test_full_latest_summary_json
    else
      full_test_runtime_path_default one_click_test_full_latest_summary_markdown
    fi
    return $?
  fi
  if declare -F one_click_test_cp >/dev/null 2>&1; then
    if [[ "$kind" == 'json' ]]; then
      resolved="$(one_click_test_cp full-latest-summary-json-path 2>/dev/null || true)"
    else
      resolved="$(one_click_test_cp full-latest-summary-markdown-path 2>/dev/null || true)"
    fi
  fi
  [[ -n "$resolved" ]] || {
    echo "[full_test_summary][FAIL] 缺少 one_click_test_full 最新摘要路径真源：$kind" >&2
    return 2
  }
  if [[ "$resolved" = /* ]]; then
    printf '%s' "$resolved"
  else
    printf '%s' "$ROOT_DIR/$resolved"
  fi
}

full_test_default_acceptance_state_path() {
  local resolved=''
  if declare -F full_test_runtime_path_default >/dev/null 2>&1; then
    full_test_runtime_path_default deployment_acceptance_state
    return $?
  fi
  if declare -F one_click_test_cp >/dev/null 2>&1; then
    resolved="$(one_click_test_cp full-acceptance-state-path 2>/dev/null || true)"
  fi
  [[ -n "$resolved" ]] || {
    echo "[full_test_summary][FAIL] 缺少 deployment_acceptance_state 控制面真源" >&2
    return 2
  }
  if [[ "$resolved" = /* ]]; then
    printf '%s' "$resolved"
  else
    printf '%s' "$ROOT_DIR/$resolved"
  fi
}

full_test_failure_scenario_for_stage() {
  case "$1" in
    control_plane_defaults|cli_filter_validation|prereqs|summary_write)
      printf '%s\n' 'preflight_failed'
      ;;
    *)
      printf '%s\n' 'preflight_failed'
      ;;
  esac
}

full_test_apply_next_action_command() {
  local line="$1"
  local selected_group="${GROUP:-all}"
  local only_raw="${ONLY_RAW:-}"
  local skip_raw="${SKIP_RAW:-}"
  local strict_flag="${STRICT:-0}"
  if [[ "$line" == 'bash ./scripts/setup/one_click_test_full.sh' ]]; then
    line='bash ./scripts/setup/one_click_test_full.sh'
    [[ "$selected_group" != 'all' ]] && line+=" --group $selected_group"
    [[ -n "$only_raw" ]] && line+=" --only $only_raw"
    [[ -n "$skip_raw" ]] && line+=" --skip $skip_raw"
    [[ "$strict_flag" == '1' ]] && line+=" --strict"
  fi
  printf '%s\n' "$line"
}

full_test_surface_config_get() {
  local path_expr="$1"
  local default_value="${2:-}"
  flow_summary_json_get "$FULL_TEST_SURFACE_PATH" "$path_expr" "$default_value"
}

full_test_preflight_failure_detail() {
  if [[ -n "${FULL_TEST_LAST_ERROR_MESSAGE:-}" ]]; then
    printf '%s' "$FULL_TEST_LAST_ERROR_MESSAGE"
    return 0
  fi
  full_test_surface_config_get preflight_failure_detail 'full test 在正式检查组执行前提前失败；请先修复 Docker / 控制面介质 / latest summary 写出路径或 deploy env prerequisites。'
}

full_test_append_preflight_actions() {
  setup_gate_collect_scenario_actions NEXT_ACTIONS one_click_test_full preflight_failed full_test_apply_next_action_command
}

full_test_build_check_json_rows() {
  local first=1 line='' status='' check_id='' detail='' group=''
  printf '['
  if ((${#RESULT_LINES[@]} == 0)); then
    printf '{"id":"%s","group":"preflight","status":"FAIL","detail":"%s"}' \
      "$(setup_gate_json_escape "$FULL_TEST_CURRENT_STAGE")" \
      "$(setup_gate_json_escape "$(full_test_preflight_failure_detail)")"
    printf ']'
    return 0
  fi
  for line in "${RESULT_LINES[@]+"${RESULT_LINES[@]}"}"; do
    IFS='|' read -r status check_id detail group <<<"$line"
    [[ $first -eq 1 ]] || printf ','
    first=0
    printf '{"id":"%s","group":"%s","status":"%s","detail":"%s"}' \
      "$(setup_gate_json_escape "$check_id")" \
      "$(setup_gate_json_escape "$group")" \
      "$(setup_gate_json_escape "$status")" \
      "$(setup_gate_json_escape "$detail")"
  done
  printf ']'
}

full_test_write_result_artifacts() {
  : > "$RESULT_LINES_FILE"
  : > "$NEXT_ACTIONS_FILE"
  if ((${#RESULT_LINES[@]} > 0)); then
    printf '%s\n' "${RESULT_LINES[@]+"${RESULT_LINES[@]}"}" > "$RESULT_LINES_FILE"
  fi
  if ((${#NEXT_ACTIONS[@]} > 0)); then
    printf '%s\n' "${NEXT_ACTIONS[@]+"${NEXT_ACTIONS[@]}"}" > "$NEXT_ACTIONS_FILE"
  fi
}

full_test_write_summary() {
  local helper_output='' helper_status=0
  full_test_write_result_artifacts
  set +e
  helper_output="$("${FULL_TEST_SURFACE_CMD[@]}" write-summary \
    --out-json "$FULL_TEST_SUMMARY_JSON_PATH" \
    --out-md "$FULL_TEST_SUMMARY_MD_PATH" \
    --generated-at "$GENERATED_AT" \
    --env-file "$ENV_FILE" \
    --group "$GROUP" \
    --only "$ONLY_RAW" \
    --skip "$SKIP_RAW" \
    --strict "$STRICT" \
    --quiet "$QUIET" \
    --json-stdout "$JSON_STDOUT" \
    --return-code "$FINAL_EXIT_CODE" \
    --result-lines-file "$RESULT_LINES_FILE" \
    --next-actions-file "$NEXT_ACTIONS_FILE" \
    --acceptance-state "$DEPLOYMENT_ACCEPTANCE_STATE_PATH" 2>&1)"
  helper_status=$?
  set -e
  if [[ $helper_status -eq 0 ]]; then
    return 0
  fi
  if [[ -n "$helper_output" ]]; then
    printf '%s\n' "$helper_output" >&2
  fi
  return "$helper_status"
}

full_test_after_run() {
  local print_output='' print_status=0 write_status=0
  FULL_TEST_SUMMARY_EMITTED=1
  set +e
  full_test_write_summary
  write_status=$?
  set -e
  if [[ $write_status -ne 0 ]]; then
    return "$write_status"
  fi
  if [[ "$QUIET" != "1" ]]; then
    set +e
    print_output="$("${FULL_TEST_SURFACE_CMD[@]}" print-summary --summary-json "$FULL_TEST_SUMMARY_JSON_PATH" 2>&1)"
    print_status=$?
    set -e
    if [[ $print_status -eq 0 ]]; then
      printf '%s\n' "$print_output"
    else
      if [[ -n "$print_output" ]]; then
        printf '%s\n' "$print_output" >&2
      fi
      return "$print_status"
    fi
    flow_summary_emit_prefixed_note_paths "[one_click_test_full]" "${FULL_TEST_SUMMARY_MD_PATH#"$ROOT_DIR"/}" "${FULL_TEST_SUMMARY_JSON_PATH#"$ROOT_DIR"/}"
  fi
  if [[ "$JSON_STDOUT" == "1" ]]; then
    set +e
    print_output="$("${FULL_TEST_SURFACE_CMD[@]}" print-summary --summary-json "$FULL_TEST_SUMMARY_JSON_PATH" --format json 2>&1)"
    print_status=$?
    set -e
    if [[ $print_status -eq 0 ]]; then
      printf '%s\n' "$print_output"
    else
      cat "$FULL_TEST_SUMMARY_JSON_PATH"
    fi
  fi
}


full_test_calculate_exit_code() {
  local strict_flag="$1"
  if ((${#FAIL_IDS[@]} > 0)); then
    printf '1\n'
    return 0
  fi
  if [[ "$strict_flag" == "1" && ${#WARN_IDS[@]} -gt 0 ]]; then
    printf '1\n'
    return 0
  fi
  printf '0\n'
}
