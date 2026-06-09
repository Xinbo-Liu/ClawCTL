#!/usr/bin/env bash
# 用途：收口 one_click_deploy 的阶段执行映射与构建/加载后续动作，避免主脚本继续维护大段 case 分支。
set -euo pipefail

DEPLOY_STAGE_RUNNER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$DEPLOY_STAGE_RUNNER_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$DEPLOY_STAGE_RUNNER_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
DEPLOY_STAGE_RUNNER_ROOT="$(openclaw_repo_root_from "$DEPLOY_STAGE_RUNNER_LIB_DIR")"
# shellcheck source=scripts/lib/flow_step_runner.sh
source "$DEPLOY_STAGE_RUNNER_ROOT/scripts/lib/flow_step_runner.sh"
# shellcheck source=scripts/setup/lib/deploy_stage_registry.sh
source "$DEPLOY_STAGE_RUNNER_ROOT/scripts/setup/lib/deploy_stage_registry.sh"
# shellcheck source=scripts/setup/lib/ingress_boundary_evidence_cache.sh
source "$DEPLOY_STAGE_RUNNER_ROOT/scripts/setup/lib/ingress_boundary_evidence_cache.sh"
unset DEPLOY_STAGE_RUNNER_LIB_DIR DEPLOY_STAGE_RUNNER_ROOT

# 包装为统一的带日志阶段执行入口。
_deploy_run_step() {
  flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE "$@"
}

# 执行 release 对齐检查，并将失败语义写入部署日志。
deploy_run_release_check() {
  flow_log_line "$LOG_PATH" "[STEP] check_openclaw_release"
  flow_set_var CURRENT_STAGE_NAME check_openclaw_release
  set +e
  IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE" bash "$ROOT_DIR/scripts/images/check_openclaw_release.sh" 2>&1 | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
  local exit_code=${PIPESTATUS[0]}
  set -e
  if [[ "$exit_code" -eq 0 ]]; then
    flow_log_line "$LOG_PATH" "[OK] check_openclaw_release"
    return 0
  fi
  case "$exit_code" in
    10)
      if [[ "${STRICT_RELEASE_CHECK:-0}" != "1" ]]; then
        flow_log_line "$LOG_PATH" '[WARN] 当前 pin 可验证但上游存在更高 base release；默认首装策略 relaxed_install 仅记录 WARN，发布门禁请使用 --strict-release-check。'
        flow_log_line "$LOG_PATH" "[OK] check_openclaw_release (relaxed_install warn exit=$exit_code)"
        return 0
      fi
      flow_log_line "$LOG_PATH" '[FAIL] 发现 OpenClaw 新的 base release；strict_release 策略阻断，需先完成 candidate 验证再继续。'
      ;;
    11)
      flow_log_line "$LOG_PATH" '[FAIL] Release 检查无法联网；在线模式要求完成版本对齐检查。'
      ;;
    12)
      if [[ "${STRICT_RELEASE_CHECK:-0}" != "1" ]]; then
        flow_log_line "$LOG_PATH" '[WARN] 当前 pin 可验证但上游存在更高 correction tag；默认首装策略 relaxed_install 仅记录 WARN，发布门禁请使用 --strict-release-check。'
        flow_log_line "$LOG_PATH" "[OK] check_openclaw_release (relaxed_install warn exit=$exit_code)"
        return 0
      fi
      flow_log_line "$LOG_PATH" '[FAIL] 发现 OpenClaw 新的 correction tag；strict_release 策略阻断，需先完成 candidate 验证再继续。'
      ;;
    13)
      flow_log_line "$LOG_PATH" '[FAIL] 当前实际镜像源尚未提供 latest tag 对应 digest；必须先修复镜像源可用性或显式切换 candidate 仓库。'
      ;;
    14)
      flow_log_line "$LOG_PATH" '[FAIL] 当前 pin 与 selected runtime source digest 不一致；必须先修正默认 pin。'
      ;;
  esac
  flow_set_var LAST_FAILED_STEP check_openclaw_release
  flow_set_var LAST_FAILED_CODE "$exit_code"
  flow_log_line "$LOG_PATH" "[FAIL] check_openclaw_release (exit=$exit_code)"
  return "$exit_code"
}

# 执行 ingress 边界证据阶段。
#
# 该阶段在非 root 部署用户下可能无法直接读取 iptables / firewalld 语义。
# 先尝试标准 doctor；若失败，再复用 root 侧已写出且与当前 env / nginx policy
# 对齐的 evidence。只有两者都不可用时才把阶段标记为 FAIL。
deploy_run_ingress_boundary_evidence_stage() {
  local stage='check_ingress_boundary_evidence'
  local exit_code=0

  flow_set_var CURRENT_STAGE_NAME "$stage"
  flow_log_line "$LOG_PATH" "[STEP] $stage"

  if ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1; then
    flow_set_var LAST_FAILED_STEP ''
    flow_set_var LAST_FAILED_CODE 0
    flow_log_line "$LOG_PATH" "[OK] $stage (cached evidence)"
    flow_log_line "$LOG_PATH" "[INFO] 复用已落盘且与当前 deploy env 一致的 root 侧 ingress 边界证据，并已对当前 Nginx allowlist 做本地校验；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 或 root 侧 check_ingress_boundary_evidence.sh 写出基础证据。"
    return 0
  fi

  set +e
  "${DEPLOY_STAGE_COMMAND[@]}" 2>&1 | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
  exit_code=${PIPESTATUS[0]}
  set -e

  if [[ "$exit_code" -eq 0 ]]; then
    flow_log_line "$LOG_PATH" "[OK] $stage"
    return 0
  fi

  if ingress_boundary_cached_evidence_ok "$ROOT_DIR" "$ENV_FILE" 1; then
    flow_set_var LAST_FAILED_STEP ''
    flow_set_var LAST_FAILED_CODE 0
    flow_log_line "$LOG_PATH" "[OK] $stage (cached evidence)"
    flow_log_line "$LOG_PATH" "[INFO] 复用已落盘且与当前 deploy env 一致的 root 侧 ingress 边界证据，并已对当前 Nginx allowlist 做本地校验；当前用户无法直接读取宿主机防火墙语义时，必须先由 apply_ingress_boundary_rules.sh 或 root 侧 check_ingress_boundary_evidence.sh 写出基础证据。"
    return 0
  fi

  flow_set_var LAST_FAILED_STEP "$stage"
  flow_set_var LAST_FAILED_CODE "$exit_code"
  flow_log_line "$LOG_PATH" "[FAIL] $stage (exit=$exit_code)"
  return "$exit_code"
}


# 按阶段注册表执行指定阶段命令。
deploy_run_registered_stage() {
  local stage="$1"
  deploy_stage_prepare_command "$stage"
  if [[ "$stage" == "check_ingress_boundary_evidence" ]]; then
    deploy_run_ingress_boundary_evidence_stage
    return $?
  fi
  _deploy_run_step "$stage" "${DEPLOY_STAGE_COMMAND[@]}"
  if [[ "$stage" == "pull_images" ]] && declare -F deploy_refresh_after_pull_images >/dev/null 2>&1; then
    deploy_refresh_after_pull_images
  fi
}

# 初始化阶段注册表后执行指定阶段。
deploy_execute_stage() {
  local stage="$1"
  deploy_stage_registry_init
  deploy_run_registered_stage "$stage"
}
