#!/usr/bin/env bash
# 用途：承接 one_click_deploy 的控制面默认值加载、阶段顺序派生与 resume 校验，减少主脚本内联事实表。

__openclaw_deploy_cp_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$__openclaw_deploy_cp_lib_dir/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$__openclaw_deploy_cp_lib_dir" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
__openclaw_deploy_cp_root="$(openclaw_repo_root_from "$__openclaw_deploy_cp_lib_dir")"
# shellcheck source=scripts/lib/flow_sequence_shell.sh
source "$__openclaw_deploy_cp_root/scripts/lib/flow_sequence_shell.sh"
# shellcheck source=scripts/lib/control_plane_config_paths.sh
source "$__openclaw_deploy_cp_root/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=scripts/setup/lib/ingress_boundary_evidence_cache.sh
source "$__openclaw_deploy_cp_root/scripts/setup/lib/ingress_boundary_evidence_cache.sh"
unset __openclaw_deploy_cp_lib_dir __openclaw_deploy_cp_root
DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON=""

deploy_load_control_plane_bootstrap() {
  if [[ -n "$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON" ]]; then
    return 0
  fi
  local -a args=(bootstrap-json "${DEPLOY_FLOW_ARGS[@]}")
  [[ -z "${RESUME_FROM:-}" ]] || args+=(--resume-from "$RESUME_FROM")
  DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON="$(one_click_deploy_cp "${args[@]}")"
}

deploy_bootstrap_value() {
  local key="$1"
  deploy_load_control_plane_bootstrap
  jq -r --arg key "$key" '.paths[$key] // ""' <<<"$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON"
}

deploy_active_control_plane_config_path_for_env() {
  local env_file="${1:-$ENV_FILE}"
  local selected="${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}"
  local profile="${OPENCLAW_CONTROL_PLANE_PROFILE:-agent_platform}"
  local explicit_profile=0
  [[ -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]] && explicit_profile=1
  openclaw_control_plane_apply_env_file_active_selection \
    "$env_file" \
    selected \
    profile \
    explicit_profile \
    0 \
    "$env_file" || return $?
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$selected" \
    OPENCLAW_CONTROL_PLANE_PROFILE="$profile" \
    openclaw_control_plane_resolve_config_path "$profile" "$selected" "$explicit_profile"
}

deploy_run_control_plane_preflight_from_bootstrap() {
  deploy_load_control_plane_bootstrap
  if [[ "$(jq -r '.preflight.status // ""' <<<"$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON")" != "ok" ]]; then
    echo '[one_click_deploy_control_plane][FAIL] bootstrap-json 未返回可用 preflight 状态' >&2
    return 2
  fi
  echo 'one_click_deploy static preflight ok'
}

deploy_load_control_plane_defaults() {
  ARTIFACT_DIR="$ROOT_DIR/$(deploy_bootstrap_value artifact-dir)"
  ENV_FILE="$ROOT_DIR/$(deploy_bootstrap_value env-file-path)"
  if [[ -n "${ENV_FILE_OVERRIDE:-}" ]]; then
    case "$ENV_FILE_OVERRIDE" in
      /*) ENV_FILE="$ENV_FILE_OVERRIDE" ;;
      *) ENV_FILE="$ROOT_DIR/$ENV_FILE_OVERRIDE" ;;
    esac
  fi
  COMPOSE_FILE="$ROOT_DIR/$(deploy_bootstrap_value compose-file-path)"
  DEPLOY_SUCCESS_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-success)
  FLOW_FAILURE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-failure)
  DEPLOY_STAGE_RUNNER_SCRIPT="$ROOT_DIR/$(deploy_bootstrap_value deploy-stage-runner-script-path)"
  RUN_SUMMARY_SHELL_SCRIPT="$ROOT_DIR/$(deploy_bootstrap_value run-summary-shell-script-path)"
  IMAGE_ENV_SCRIPT="$ROOT_DIR/$(deploy_bootstrap_value image-env-script-path)"
  DEPLOY_RUNTIME_CONTEXT_SHELL_SCRIPT="$ROOT_DIR/$(deploy_bootstrap_value deploy-runtime-context-shell-script-path)"
  DEPLOY_FLOW_SUMMARY_SHELL_SCRIPT="$ROOT_DIR/$(deploy_bootstrap_value deploy-flow-summary-shell-script-path)"
  BASIC_GATE_SCRIPT_REL="$(deploy_bootstrap_value basic-gate-script-path)"
  RUNTIME_HOST_ENV_PATH="$ROOT_DIR/$(deploy_bootstrap_value runtime-host-env-path)"
  DEFAULT_LOG_DIR_REL="$(deploy_bootstrap_value default-log-dir)"
  IMAGE_ARCHIVE_PATTERN="$ROOT_DIR/$(deploy_bootstrap_value image-archive-pattern)"

  # shellcheck source=scripts/setup/lib/deploy_stage_runner.sh
  source "$DEPLOY_STAGE_RUNNER_SCRIPT"
  # shellcheck source=scripts/lib/run_summary_shell.sh
  source "$RUN_SUMMARY_SHELL_SCRIPT"
  # shellcheck source=scripts/setup/lib/deploy_runtime_context_shell.sh
  source "$DEPLOY_RUNTIME_CONTEXT_SHELL_SCRIPT"
  # shellcheck source=scripts/setup/lib/deploy_flow_summary_shell.sh
  source "$DEPLOY_FLOW_SUMMARY_SHELL_SCRIPT"
}

deploy_verify_basic_gate_proof() {
  local offline_flag=0
  [[ "$DEPLOY_MODE" == "offline" ]] && offline_flag=1
  local -a args=(
    setup flow basic-summary verify-proof
    --env-file "$ENV_FILE"
    --offline "$offline_flag"
    --return-code 0
    --release-check "$RUN_RELEASE_CHECK"
    --release-policy "$(deploy_release_policy)"
  )
  [[ -n "$IMAGE_ARCHIVE_PATH" ]] && args+=(--image-archive-path "$IMAGE_ARCHIVE_PATH")
  bash "$OPENCLAW_PYTHON_TOOL" "${args[@]}"
}

deploy_run_basic_gate_refresh() {
  local -a args=(bash "$ROOT_DIR/$BASIC_GATE_SCRIPT_REL" --env-file "$ENV_FILE")
  if [[ "$DEPLOY_MODE" == "offline" ]]; then
    args+=(--offline)
    [[ -n "$IMAGE_ARCHIVE_PATH" ]] && args+=(--image-archive "$IMAGE_ARCHIVE_PATH")
  fi
  if [[ "$RUN_RELEASE_CHECK" != "1" ]]; then
    args+=(--skip-release-check)
  fi
  if [[ "${STRICT_RELEASE_CHECK:-0}" == "1" ]]; then
    args+=(--strict-release-check)
  fi
  log "[INFO] latest basic gate proof 缺失或已过期；自动执行：${args[*]}"
  "${args[@]}"
}

deploy_release_policy() {
  if [[ "$RUN_RELEASE_CHECK" != "1" ]]; then
    printf 'skipped\n'
    return 0
  fi
  if [[ "${STRICT_RELEASE_CHECK:-0}" == "1" ]]; then
    printf 'strict_release\n'
    return 0
  fi
  printf 'relaxed_install\n'
}

deploy_verify_or_refresh_basic_gate_proof() {
  local verify_output=''
  local verify_status=0
  if verify_output="$(deploy_verify_basic_gate_proof 2>&1)"; then
    [[ -n "$verify_output" ]] && printf '%s\n' "$verify_output"
    return 0
  else
    verify_status=$?
  fi
  if [[ "$RUN_BASIC_GATE_REFRESH" != "1" ]]; then
    printf '%s\n' "$verify_output" >&2
    return "$verify_status"
  fi
  [[ -n "$verify_output" ]] && log "[INFO] basic gate proof 校验未通过，准备自动刷新。原始输出：$verify_output"
  deploy_run_basic_gate_refresh
  deploy_verify_basic_gate_proof
}

deploy_run_post_deploy_acceptance() {
  if [[ "$START_SERVICES" != "1" || "$RUN_POST_DEPLOY_ACCEPTANCE" != "1" ]]; then
    return 0
  fi
  if [[ "${RESUME_FROM:-}" == "post_deploy_full_acceptance" ]]; then
    log "[INFO] --resume-from post_deploy_full_acceptance：执行范围限定为 full test 与 runtime evidence，跳过 control_plane_run_all_once。"
  else
    if [[ "${RESUME_FROM:-}" == "post_deploy_acceptance" ]]; then
      log "[INFO] --resume-from post_deploy_acceptance：执行 required run ledger jobs、full test 与 runtime evidence；发送动作按当前 target 配置执行。"
    fi
    deploy_run_control_plane_once_if_required
  fi
  flow_set_var CURRENT_STAGE_NAME post_deploy_full_acceptance
  log "[STEP] post_deploy_full_acceptance"
  deploy_run_post_deploy_full_acceptance_with_retry
  log "[OK] post_deploy_full_acceptance"
  flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE \
    deployment_acceptance_export_guard deploy_assert_deployment_acceptance_ready_for_evidence
  flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE \
    export_runtime_acceptance_evidence bash "$ROOT_DIR/scripts/runtime/export_runtime_acceptance_evidence.sh"
}

deploy_one_click_test_path() {
  local key="$1"
  local path=''
  path="$(bash "$OPENCLAW_PYTHON_TOOL" setup flow one-click-test "$key")" || return $?
  path="${path//\\//}"
  case "$path" in
    /*|[A-Za-z]:/*) printf '%s\n' "$path" ;;
    *) printf '%s/%s\n' "$ROOT_DIR" "$path" ;;
  esac
}

deploy_assert_deployment_acceptance_ready_for_evidence() {
  local latest_summary_path='' acceptance_path=''
  latest_summary_path="$(deploy_one_click_test_path full-latest-summary-json-path)"
  acceptance_path="$(deploy_one_click_test_path full-acceptance-state-path)"
  if [[ ! -f "$latest_summary_path" ]]; then
    echo "[FAIL] full test latest summary 缺失：$latest_summary_path" >&2
    return 2
  fi
  if [[ ! -f "$acceptance_path" ]]; then
    echo "[FAIL] deployment acceptance 状态文件缺失：$acceptance_path" >&2
    return 2
  fi
  if ! jq -e '.checks[]? | select(.id == "deployment_acceptance_contract" and .status == "PASS")' "$latest_summary_path" >/dev/null; then
    echo "[FAIL] full test latest summary 未记录 deployment_acceptance_contract=PASS：$latest_summary_path" >&2
    return 2
  fi
  if [[ "$(jq -r '.accepted // false' "$acceptance_path")" != "true" ]]; then
    echo "[FAIL] deployment acceptance 未 accepted：$acceptance_path" >&2
    return 2
  fi
}

deploy_testing_manifest_json_for_env() {
  local config_path=''
  config_path="$(deploy_active_control_plane_config_path_for_env "$ENV_FILE")" || return $?
  OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$config_path" \
    bash "$OPENCLAW_PYTHON_TOOL" setup flow full-test-surface json
}

deploy_required_run_ledger_jobs() {
  local manifest_json=''
  if ! manifest_json="$(deploy_testing_manifest_json_for_env)"; then
    echo '[deploy_flow_control_plane][FAIL] 无法渲染当前 active profile 的 full-test manifest，不能判断 required run ledger jobs' >&2
    return 2
  fi
  jq -r '.acceptance_reference.required_run_ledger_jobs[]? // empty' <<<"$manifest_json" 2>/dev/null || true
}

deploy_run_control_plane_once_if_required() {
  local required_jobs=''
  required_jobs="$(deploy_required_run_ledger_jobs)" || return $?
  [[ -n "$required_jobs" ]] || return 0
  flow_set_var CURRENT_STAGE_NAME control_plane_run_all_once
  log "[STEP] control_plane_run_all_once"
  log "[INFO] 当前 deployment acceptance 声明 required run ledger jobs: $(printf '%s' "$required_jobs" | paste -sd, -)"
  log "[INFO] 执行 scheduler run-all-once 生成本机真实 run ledger；发送动作按当前 target 配置执行。若该环境不允许发送，请使用 --skip-acceptance 启动服务，并在允许执行 required jobs 后闭合 deployment acceptance。"
  bash "$ROOT_DIR/scripts/control_plane/run_control_plane_run_all_once.sh"
  log "[OK] control_plane_run_all_once"
}

deploy_run_post_deploy_full_acceptance_with_retry() {
  local attempt=1
  local max_attempts=3
  local exit_code=0
  while (( attempt <= max_attempts )); do
    if bash "$ROOT_DIR/scripts/setup/one_click_test_full.sh" --env-file "$ENV_FILE"; then
      return 0
    fi
    exit_code=$?
    if (( attempt >= max_attempts )); then
      return "$exit_code"
    fi
    log "[WARN] post_deploy_full_acceptance 第 $attempt 次未通过，等待 runtime 健康检查收敛后重试。"
    sleep 15
    attempt=$((attempt + 1))
  done
  return "$exit_code"
}

deploy_refresh_flow_args() {
  DEPLOY_FLOW_ARGS=(
    --mode "$DEPLOY_MODE"
    --release-check "$RUN_RELEASE_CHECK"
    --browser-verify "$RUN_BROWSER_VERIFY"
    --start-services "$START_SERVICES"
  )
}

deploy_build_effective_stage_order() {
  deploy_load_control_plane_bootstrap
  EFFECTIVE_STAGES=()
  while IFS= read -r stage_name; do
    [[ -n "$stage_name" ]] || continue
    EFFECTIVE_STAGES+=("$stage_name")
  done < <(jq -r '.effectiveStages[]? // empty' <<<"$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON")
  flow_sequence_build_index EFFECTIVE_STAGE_INDEX EFFECTIVE_STAGES
}

deploy_validate_resume_from() {
  [[ -n "$RESUME_FROM" ]] || return 0
  deploy_load_control_plane_bootstrap
  if [[ "$(jq -r '.resume.status // ""' <<<"$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON")" != "ok" ]]; then
    echo "[deploy_flow_control_plane] --resume-from 指定的阶段无效：$RESUME_FROM" >&2
    return 2
  fi
}

deploy_check_ingress_boundary_resume_guard() {
  [[ -n "$RESUME_FROM" ]] || return 0
  if deploy_should_run_stage check_ingress_boundary_evidence; then
    return 0
  fi
  flow_log_line "$LOG_PATH" "[STEP] check_ingress_boundary_evidence_resume_guard"
  flow_set_var CURRENT_STAGE_NAME check_ingress_boundary_evidence_resume_guard
  local check_output='' exit_code=0
  if ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1; then
    flow_log_line "$LOG_PATH" "[OK] check_ingress_boundary_evidence_resume_guard (cached evidence)"
    flow_log_line "$LOG_PATH" "[INFO] 复用已落盘且与当前 deploy env 一致的 root 侧 ingress 边界证据，并已对当前 Nginx allowlist 做本地校验；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 或 root 侧 check_ingress_boundary_evidence.sh 写出基础证据。"
    return 0
  fi
  if check_output="$(bash "$ROOT_DIR/scripts/doctor/check_ingress_boundary_evidence.sh" \
    --env-file "$ENV_FILE" \
    --require-nginx-policy \
    --no-write 2>&1)"; then
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output" | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
    flow_log_line "$LOG_PATH" "[OK] check_ingress_boundary_evidence_resume_guard"
    return 0
  else
    exit_code=$?
    [[ -n "$check_output" ]] && printf '%s\n' "$check_output" | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
  fi
  if [[ "$exit_code" -eq 0 ]]; then
    flow_log_line "$LOG_PATH" "[OK] check_ingress_boundary_evidence_resume_guard"
    return 0
  fi
  flow_set_var LAST_FAILED_STEP check_ingress_boundary_evidence_resume_guard
  flow_set_var LAST_FAILED_CODE "$exit_code"
  flow_log_line "$LOG_PATH" "[FAIL] check_ingress_boundary_evidence_resume_guard (exit=$exit_code)"
  return "$exit_code"
}

deploy_should_run_stage() {
  local stage_name="$1"
  flow_sequence_should_run_from EFFECTIVE_STAGE_INDEX "$stage_name" "$RESUME_FROM"
}

deploy_run_effective_stages() {
  local stage_name=''
  for stage_name in "${EFFECTIVE_STAGES[@]}"; do
    if deploy_should_run_stage "$stage_name"; then
      deploy_execute_stage "$stage_name"
    fi
  done
}
