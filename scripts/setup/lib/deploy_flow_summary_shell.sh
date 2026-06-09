#!/usr/bin/env bash
# 用途：为 one_click_deploy 提供摘要写出、终端摘要与错误收尾 helper，避免主脚本继续内联 success/failure summary 逻辑。
set -euo pipefail

DEPLOY_FLOW_SUMMARY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$DEPLOY_FLOW_SUMMARY_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$DEPLOY_FLOW_SUMMARY_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
DEPLOY_FLOW_SUMMARY_LIB_ROOT="$(openclaw_repo_root_from "$DEPLOY_FLOW_SUMMARY_LIB_DIR")"
# shellcheck source=scripts/lib/flow_summary_common_shell.sh
source "$DEPLOY_FLOW_SUMMARY_LIB_ROOT/scripts/lib/flow_summary_common_shell.sh"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$DEPLOY_FLOW_SUMMARY_LIB_ROOT/scripts/lib/repo_contracts.sh"
unset DEPLOY_FLOW_SUMMARY_LIB_DIR

deploy_named_array_reset() {
  local target_name="$1"
  case "$target_name" in
    summary_args)
      summary_args=()
      ;;
    *)
      echo "[deploy_flow_summary][FAIL] 未知摘要参数数组：$target_name" >&2
      return 2
      ;;
  esac
}

deploy_named_array_append() {
  local target_name="$1"
  shift
  local item=''
  case "$target_name" in
    summary_args)
      for item in "$@"; do
        summary_args+=("$item")
      done
      ;;
    *)
      echo "[deploy_flow_summary][FAIL] 未知摘要参数数组：$target_name" >&2
      return 2
      ;;
  esac
}

deploy_build_failure_summary_args() {
  local target_name="$1"
  local include_outputs="$2"
  deploy_named_array_reset "$target_name"
  deploy_named_array_append "$target_name" \
    --flow deploy \
    --stage "$LAST_FAILED_STEP" \
    --status failed \
    --timestamp "$TS" \
    --log-path "$LOG_PATH" \
    --summary-json-path "$SUMMARY_JSON_PATH" \
    --summary-md-path "$SUMMARY_PATH" \
    --exit-code "$LAST_FAILED_CODE" \
    --mode "$DEPLOY_MODE"
  if [[ "$include_outputs" == '1' ]]; then
    deploy_named_array_append "$target_name" \
      --out-json "$SUMMARY_JSON_PATH" \
      --out-md "$SUMMARY_PATH"
  fi
  [[ -n "$RESUME_FROM" ]] && deploy_named_array_append "$target_name" --resume-from "$RESUME_FROM"
  [[ -n "$IMAGE_ARCHIVE_PATH" ]] && deploy_named_array_append "$target_name" --image-archive-path "$IMAGE_ARCHIVE_PATH"
  return 0
}

deploy_build_success_summary_args() {
  local target_name="$1"
  local status="$2"
  local include_outputs="$3"
  deploy_named_array_reset "$target_name"
  deploy_named_array_append "$target_name" \
    --env-file "$ENV_FILE" \
    --mode "$DEPLOY_MODE" \
    --status "$status" \
    --timestamp "$TS" \
    --log-path "$LOG_PATH" \
    --summary-json-path "$SUMMARY_JSON_PATH" \
    --summary-md-path "$SUMMARY_PATH" \
    --start-services "$START_SERVICES" \
    --post-acceptance "$RUN_POST_DEPLOY_ACCEPTANCE"
  if [[ "$include_outputs" == '1' ]]; then
    deploy_named_array_append "$target_name" \
      --out-json "$SUMMARY_JSON_PATH" \
      --out-md "$SUMMARY_PATH"
  fi
  [[ -n "$RESUME_FROM" ]] && deploy_named_array_append "$target_name" --resume-from "$RESUME_FROM"
  [[ -n "$IMAGE_ARCHIVE_PATH" ]] && deploy_named_array_append "$target_name" --image-archive-path "$IMAGE_ARCHIVE_PATH"
  return 0
}

deploy_run_summary_helper_from_array() {
  local helper_prefix_name="$1"
  local command="$2"
  local array_name="$3"
  local __out_var="${4:-}"
  local __status_var="${5:-}"
  local -a resolved_args=()
  local output=''
  local status=0
  case "$array_name" in
    summary_args)
      resolved_args=("${summary_args[@]}")
      ;;
    *)
      echo "[deploy_flow_summary][FAIL] 未知摘要参数数组：$array_name" >&2
      return 2
      ;;
  esac
  set +e
  output="$(summary_run_prefix "$helper_prefix_name" "$command" "${resolved_args[@]}" 2>&1)"
  status=$?
  set -e
  if [[ -n "$__out_var" ]]; then
    printf -v "$__out_var" '%s' "$output"
  elif [[ -n "$output" ]]; then
    printf '%s\n' "$output"
  fi
  if [[ -n "$__status_var" ]]; then
    printf -v "$__status_var" '%s' "$status"
  fi
  return "$status"
}

deploy_note_summary_unavailable() {
  local output="$1"
  if declare -F log >/dev/null 2>&1 && [[ -n "${LOG_PATH:-}" ]]; then
    log "[WARN] deploy-failure 控制面摘要不可用；请先恢复控制面摘要入口。"
    [[ -n "$output" ]] && log "$output"
  else
    echo "[WARN] deploy-failure 控制面摘要不可用；请先恢复控制面摘要入口。" >&2
    [[ -n "$output" ]] && printf '%s\n' "$output" >&2
  fi
}

deploy_write_summary() {
  local status="$1"
  mkdir -p "$LOG_DIR"
  local -a summary_args=()
  local helper_output=''
  local helper_status=0
  if [[ "$status" == 'failed' ]]; then
    deploy_build_failure_summary_args summary_args 1
    if deploy_run_summary_helper_from_array FLOW_FAILURE_CMD write-failure-summary summary_args helper_output helper_status; then
      return 0
    fi
    deploy_note_summary_unavailable "$helper_output"
    return "$helper_status"
  fi
  deploy_build_success_summary_args summary_args "$status" 1
  flow_summary_write_from_array DEPLOY_SUCCESS_CMD write-success-summary summary_args
}

deploy_emit_terminal_summary() {
  local status="$1"
  local -a summary_args=()
  local helper_output=''
  local helper_status=0
  flow_summary_note_paths log "$SUMMARY_PATH" "$SUMMARY_JSON_PATH"
  if [[ "$status" == 'failed' ]]; then
    deploy_build_failure_summary_args summary_args 0
    if deploy_run_summary_helper_from_array FLOW_FAILURE_CMD failure-summary summary_args helper_output helper_status; then
      while IFS= read -r line; do
        if [[ -z "$line" ]]; then
          log ""
          continue
        fi
        log "[SUMMARY] $line"
      done <<< "$helper_output"
      return 0
    fi
    deploy_note_summary_unavailable "$helper_output"
    return "$helper_status"
  fi
  deploy_build_success_summary_args summary_args "$status" 0
  flow_summary_emit_from_array log DEPLOY_SUCCESS_CMD success-summary summary_args
}

deploy_log_run_context() {
  local offline_hint=''
  log "[INFO] 一键部署开始：$TS"
  log "[INFO] 日志位置：$LOG_PATH"
  log "[INFO] 摘要位置：$SUMMARY_PATH"
  log "[INFO] 机器摘要位置：$SUMMARY_JSON_PATH"
  log "[INFO] 当前模式：$DEPLOY_MODE"
  if ((${#EFFECTIVE_STAGES[@]} > 0)); then
    log "[INFO] 当前部署阶段计划：${EFFECTIVE_STAGES[*]}"
  fi
  if [[ -n "$RESUME_FROM" ]]; then
    log "[INFO] 从阶段继续执行：$RESUME_FROM"
  fi
  if [[ "$DEPLOY_MODE" == 'online' && "$RUN_RELEASE_CHECK" != '1' ]]; then
    log "[WARN] 已跳过 OpenClaw release 对齐检查；仅建议用于已经完成外部版本校验的恢复场景。"
  fi
  if [[ "$RUN_BROWSER_VERIFY" != '1' ]]; then
    log "[WARN] 已跳过浏览器能力校验；若部署后 Gateway 页面异常，请补跑默认部署链或 full test。"
  fi
  if [[ "$RUN_BASIC_GATE_REFRESH" != '1' ]]; then
    log "[WARN] basic gate proof 自动刷新未启用；latest proof 不匹配时部署会直接失败。"
  fi
  if [[ "$START_SERVICES" == '1' && "$RUN_POST_DEPLOY_ACCEPTANCE" != '1' ]]; then
    log "[WARN] 已跳过部署后 full test 与 runtime acceptance evidence 导出；服务启动成功不等于验收闭环通过。"
  fi
  log "[INFO] runtime image source 统一查看 docs/operations/runtime-service-reference.md；provider/API 入口以 active profile 的 deploy env schema 与 extension.env/site.env 输入为准"
  if [[ "$DEPLOY_MODE" == 'offline' ]]; then
    offline_hint=' --offline'
  fi
  log "[INFO] 前置门禁：latest basic gate proof 已校验；缺失或过期时入口会自动补跑 bash ./$BASIC_GATE_SCRIPT_REL${offline_hint}。"
}

deploy_on_error() {
  local exit_code="$1"
  if [[ "$LAST_FAILED_CODE" -eq 0 ]]; then
    LAST_FAILED_CODE="$exit_code"
  fi
  if [[ -z "$LAST_FAILED_STEP" ]]; then
    LAST_FAILED_STEP="$CURRENT_STAGE_NAME"
  fi
  if [[ -z "$LOG_DIR" || -z "$LOG_PATH" || -z "$SUMMARY_PATH" || -z "$SUMMARY_JSON_PATH" ]]; then
    exit "$exit_code"
  fi
  if ! deploy_write_summary failed; then
    echo "[one_click_deploy][WARN] 写出失败摘要失败：$SUMMARY_PATH" >&2
  fi
  if ! deploy_emit_terminal_summary failed; then
    echo "[one_click_deploy][WARN] 输出失败终端摘要失败：$SUMMARY_PATH" >&2
  fi
  exit "$exit_code"
}
