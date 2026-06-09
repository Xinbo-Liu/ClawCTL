#!/usr/bin/env bash
# 用途：把目标机首次准备、镜像准备与可选启动收口成一个入口。
# 说明：
# - 默认执行在线全链路，并在最后拉起 runtime 服务；但服务启动不等于 dispatcher 调度已正式上线。
# - 支持离线模式：通过已导出的 deployment_images_*.tar 统一导入部署镜像合同角色，再继续部署。
# - runtime image source 统一查看 docs/operations/runtime-service-reference.md；启用的 provider/API 入口以 active profile 的 deploy env schema 与 extension.env/site.env 输入为准。
set -Eeuo pipefail
export TZ=Asia/Shanghai

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
OPENCLAW_PYTHON_TOOL="$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh"

prevalidate_image_archive_args() {
  local -a args=("$@")
  local has_offline=0
  local has_image_archive=0
  local index=0
  local arg=''
  for (( index = 0; index < ${#args[@]}; index += 1 )); do
    arg="${args[$index]}"
    case "$arg" in
      --offline)
        has_offline=1
        ;;
      --image-archive)
        has_image_archive=1
        if (( index + 1 >= ${#args[@]} )) || [[ "${args[$(( index + 1 ))]}" == --* ]]; then
          echo "[FAIL] --image-archive 缺少路径参数" >&2
          exit 2
        fi
        ;;
      --image-archive=)
        echo "[FAIL] --image-archive 缺少路径参数" >&2
        exit 2
        ;;
      --image-archive=*)
        has_image_archive=1
        ;;
    esac
  done
  if (( has_image_archive == 1 && has_offline == 0 )); then
    echo "[FAIL] --image-archive 仅在 --offline 下有效" >&2
    exit 2
  fi
}
prevalidate_image_archive_args "$@"

# shellcheck source=scripts/lib/flow_entry_surface_shell.sh
source "$ROOT_DIR/scripts/lib/flow_entry_surface_shell.sh"
# shellcheck source=scripts/lib/flow_step_runner.sh
source "$ROOT_DIR/scripts/lib/flow_step_runner.sh"
# shellcheck source=scripts/setup/lib/setup_cli_common.sh
source "$ROOT_DIR/scripts/setup/lib/setup_cli_common.sh"
# shellcheck source=scripts/setup/lib/deploy_flow_control_plane_shell.sh
source "$ROOT_DIR/scripts/setup/lib/deploy_flow_control_plane_shell.sh"
# shellcheck source=scripts/lib/run_summary_shell.sh
source "$ROOT_DIR/scripts/lib/run_summary_shell.sh"
# shellcheck source=scripts/setup/lib/deploy_runtime_context_shell.sh
source "$ROOT_DIR/scripts/setup/lib/deploy_runtime_context_shell.sh"
# shellcheck source=scripts/setup/lib/deploy_flow_summary_shell.sh
source "$ROOT_DIR/scripts/setup/lib/deploy_flow_summary_shell.sh"
# shellcheck source=scripts/setup/lib/extension_env_gate.sh
source "$ROOT_DIR/scripts/setup/lib/extension_env_gate.sh"
source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
source "$ROOT_DIR/scripts/setup/lib/host_install_defaults.sh"
one_click_deploy_cp() {
  bash "$OPENCLAW_PYTHON_TOOL" setup flow one-click-deploy "$@"
}
TS="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR=""
ENV_FILE=""
ENV_FILE_OVERRIDE=""
COMPOSE_FILE=""
DEPLOY_SUCCESS_CMD=()
FLOW_FAILURE_CMD=()
DEPLOY_STAGE_RUNNER_SCRIPT=""
RUN_SUMMARY_SHELL_SCRIPT=""
IMAGE_ENV_SCRIPT=""
DEPLOY_RUNTIME_CONTEXT_SHELL_SCRIPT=""
DEPLOY_FLOW_SUMMARY_SHELL_SCRIPT=""
BASIC_GATE_SCRIPT_REL=""
RUNTIME_HOST_ENV_PATH=""
DEFAULT_LOG_DIR_REL=""
IMAGE_ARCHIVE_PATTERN=""
LOG_DIR=""
LOG_PATH=""
SUMMARY_PATH=""
SUMMARY_JSON_PATH=""
START_SERVICES=1
RUN_BROWSER_VERIFY=1
RUN_RELEASE_CHECK=1
STRICT_RELEASE_CHECK="${OPENCLAW_STRICT_RELEASE_CHECK:-0}"
RUN_BASIC_GATE_REFRESH=1
RUN_POST_DEPLOY_ACCEPTANCE=1
DEPLOY_MODE="online"
IMAGE_ARCHIVE_PATH=""
LAST_FAILED_STEP=""
LAST_FAILED_CODE=0
CURRENT_STAGE_NAME="init"
EXPLAIN_ONLY=0
HELP_ONLY=0
RESUME_FROM=""
DEPLOY_FLOW_ARGS=()
EFFECTIVE_STAGES=()
declare -A EFFECTIVE_STAGE_INDEX=()
log() {
  printf '%s\n' "$*" | flow_redact_sensitive_stream | tee -a "$LOG_PATH"
}

init_runtime_context() {
  deploy_init_runtime_context
}

deploy_prime_control_plane_value() {
  local __out_var="$1"
  shift
  local output=''
  local status=0
  set +e
  output="$(one_click_deploy_cp "$@" 2>&1)"
  status=$?
  set -e
  printf -v "$__out_var" '%s' "$output"
  return "$status"
}

deploy_prime_summary_context_minimal_defaults() {
  local host_state_root=''
  host_state_root="$(host_install_defaults_state_root_default 2>/dev/null || true)"
  [[ -n "$host_state_root" ]] || host_state_root='state/openclaw'
  case "$host_state_root" in
    /*)
      RUNTIME_HOST_ENV_PATH="$host_state_root/control_plane/runtime.host.env"
      DEFAULT_LOG_DIR_REL="$host_state_root/control_plane/logs"
      ;;
    *)
      RUNTIME_HOST_ENV_PATH="$ROOT_DIR/$host_state_root/control_plane/runtime.host.env"
      DEFAULT_LOG_DIR_REL="$host_state_root/control_plane/logs"
      ;;
  esac
  FLOW_FAILURE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-failure)
  deploy_init_summary_paths
  mkdir -p "$LOG_DIR"
  : > "$LOG_PATH"
}

deploy_prime_fail_control_plane_defaults() {
  local output="$1"
  local status="${2:-2}"
  output="${output//$'\r'/}"
  output="${output//$'\n'/；}"
  [[ -n "$output" ]] || output='one-click-deploy 控制面默认值加载失败'
  deploy_prime_summary_context_minimal_defaults
  CURRENT_STAGE_NAME="control_plane_defaults"
  LAST_FAILED_STEP="control_plane_defaults"
  LAST_FAILED_CODE="$status"
  log "[FAIL] one-click-deploy 控制面默认值加载失败：$output"
  if ! deploy_write_summary failed; then
    echo "[one_click_deploy][WARN] 写出失败摘要失败：$SUMMARY_PATH" >&2
  fi
  if ! deploy_emit_terminal_summary failed; then
    echo "[one_click_deploy][WARN] 输出失败终端摘要失败：$SUMMARY_PATH" >&2
  fi
  exit "$status"
}

deploy_prime_summary_context() {
  local runtime_host_env_rel=''
  local default_log_dir_rel=''
  local cp_status=0
  local -a bootstrap_args=(bootstrap-json "${DEPLOY_FLOW_ARGS[@]}")
  [[ -z "${RESUME_FROM:-}" ]] || bootstrap_args+=(--resume-from "$RESUME_FROM")
  deploy_prime_control_plane_value DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON "${bootstrap_args[@]}" || cp_status=$?
  [[ "$cp_status" -eq 0 ]] || deploy_prime_fail_control_plane_defaults "$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON" "$cp_status"
  [[ -n "$DEPLOY_CONTROL_PLANE_BOOTSTRAP_JSON" ]] || deploy_prime_fail_control_plane_defaults 'one-click-deploy 缺少 bootstrap-json 控制面真源' 2
  runtime_host_env_rel="$(deploy_bootstrap_value runtime-host-env-path)" || cp_status=$?
  [[ "$cp_status" -eq 0 ]] || deploy_prime_fail_control_plane_defaults "$runtime_host_env_rel" "$cp_status"
  [[ -n "$runtime_host_env_rel" ]] || deploy_prime_fail_control_plane_defaults 'one-click-deploy 缺少 runtime-host-env-path 控制面真源' 2
  default_log_dir_rel="$(deploy_bootstrap_value default-log-dir)" || cp_status=$?
  [[ "$cp_status" -eq 0 ]] || deploy_prime_fail_control_plane_defaults "$default_log_dir_rel" "$cp_status"
  [[ -n "$default_log_dir_rel" ]] || deploy_prime_fail_control_plane_defaults 'one-click-deploy 缺少 default-log-dir 控制面真源' 2
  case "$runtime_host_env_rel" in
    /*) RUNTIME_HOST_ENV_PATH="$runtime_host_env_rel" ;;
    *) RUNTIME_HOST_ENV_PATH="$ROOT_DIR/$runtime_host_env_rel" ;;
  esac
  DEFAULT_LOG_DIR_REL="$default_log_dir_rel"
  FLOW_FAILURE_CMD=(bash "$OPENCLAW_PYTHON_TOOL" setup flow deploy-failure)
  deploy_init_summary_paths
  mkdir -p "$LOG_DIR"
  : > "$LOG_PATH"
}

deploy_repair_repo_exec_bits() {
  bash "$ROOT_DIR/scripts/setup/fix_permissions.sh"
}

deploy_reject_root_runtime_user() {
  [[ "$(id -u)" != "0" ]] && return 0
  echo "[FAIL] one_click_deploy 拒绝以 root 执行正式部署主链；root 仅用于 prepare_docker_host、prepare_deploy_user、apply_ingress_boundary_rules、fix_permissions 等宿主机步骤。请切换到固定部署用户后重试；若当前保留 root SSH 会话，可执行：runuser -u openclaw -- bash -lc 'cd $ROOT_DIR && bash ./scripts/setup/one_click_deploy.sh'。" >&2
  exit 2
}

deploy_assert_access_mode() {
  local path="$1"
  local mode="$2"
  local label="$3"
  case "$mode" in
    rx)
      [[ -r "$path" && -x "$path" ]] || {
        echo "[FAIL] $label 缺少读取/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限后再继续。" >&2
        exit 2
      }
      ;;
    rw)
      [[ -r "$path" && -w "$path" ]] || {
        echo "[FAIL] $label 缺少读取/写入权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限后再继续。" >&2
        exit 2
      }
      ;;
    rwx)
      [[ -r "$path" && -w "$path" && -x "$path" ]] || {
        echo "[FAIL] $label 缺少读取/写入/执行权限：$path；当前脚本不会自动提权或 chown，请先修正宿主机权限后再继续。" >&2
        exit 2
      }
      ;;
    *)
      echo "[FAIL] 未知权限模式：$mode" >&2
      exit 2
      ;;
  esac
}

deploy_assert_dir_manageable_or_creatable() {
  local dir="$1"
  local label="$2"
  if [[ -d "$dir" ]]; then
    deploy_assert_access_mode "$dir" rwx "$label"
    return 0
  fi
  local parent_dir
  parent_dir="$(dirname "$dir")"
  [[ -d "$parent_dir" ]] || {
    echo "[FAIL] $label 的父目录不存在：$parent_dir；当前脚本不会自动提权或补建越级路径，请先修正宿主机目录布局。" >&2
    exit 2
  }
  deploy_assert_access_mode "$parent_dir" rwx "$label 的父目录"
}

deploy_check_local_permission_prereqs() {
  local args=(
    bash "$ROOT_DIR/scripts/doctor/check_local_runtime_fs_contract.sh"
    --env-file "$ENV_FILE"
    --require-env-file
    --require-current-runtime-user
    --reject-root-runtime-user
  )
  "${args[@]}"
}

deploy_check_deployment_image_readiness_prereqs() {
  local args=(bash "$ROOT_DIR/scripts/doctor/check_deployment_image_readiness.sh" --env-file "$ENV_FILE")
  if [[ "$DEPLOY_MODE" == "offline" ]]; then
    args+=(--offline)
    [[ -n "$IMAGE_ARCHIVE_PATH" ]] && args+=(--image-archive "$IMAGE_ARCHIVE_PATH")
  fi
  "${args[@]}"
}


deploy_check_runtime_bind_user_contract_prereqs() {
  local args=(bash "$ROOT_DIR/scripts/doctor/check_runtime_bind_user_contract.sh" --env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE")
  "${args[@]}"
}

deploy_check_extension_env_prereqs() {
  local config_path=''
  local report_path=''
  config_path="$(deploy_active_control_plane_config_path_for_env "$ENV_FILE")" || return $?
  report_path="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/extension_env_preflight.json)"
  extension_env_gate_ensure_active_profile "$ROOT_DIR" "$config_path" "one_click_deploy" scheduler "$report_path"
}

deploy_render_effective_compose() {
  local config_path=''
  local effective_compose_path=''
  local -a args=()
  config_path="$(deploy_active_control_plane_config_path_for_env "$ENV_FILE")" || return $?
  effective_compose_path="$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/docker-compose.effective.yml)"
  mkdir -p "$(dirname "$effective_compose_path")"
  args=(bash "$OPENCLAW_PYTHON_TOOL" runtime mounts sync-compose --output "$effective_compose_path")
  args+=(--config-path "$config_path")
  "${args[@]}"
  COMPOSE_FILE="$effective_compose_path"
}

deploy_gateway_selection_env_rewritten() {
  local selection_file="$ROOT_DIR/state/image_pull/gateway_source_selection.json"
  [[ -f "$selection_file" && -r "$selection_file" ]] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  [[ "$(jq -r '.envRewritten // false' "$selection_file" 2>/dev/null || printf false)" == 'true' ]]
}

deploy_reload_image_env_after_source_selection() {
  IMAGE_ENV_DEPLOY_ENV_PATH="$ENV_FILE"
  export IMAGE_ENV_DEPLOY_ENV_PATH
  IMAGE_ENV_LOADED=0
  image_env_load
}

deploy_refresh_after_pull_images() {
  if ! deploy_gateway_selection_env_rewritten; then
    return 0
  fi
  log "[INFO] pull_images 已改写当前 deploy env；重新加载镜像 env、重渲染 effective compose 并刷新 basic gate proof。"
  deploy_reload_image_env_after_source_selection
  flow_set_var CURRENT_STAGE_NAME effective_compose_render_after_pull_images
  deploy_render_effective_compose
  if [[ "$RUN_BASIC_GATE_REFRESH" != "1" ]]; then
    echo "[FAIL] pull_images 已改写 deploy env，但当前未允许刷新 basic gate proof；请先执行 bash ./$BASIC_GATE_SCRIPT_REL --env-file '$ENV_FILE' 后再恢复部署。" >&2
    flow_set_var LAST_FAILED_STEP basic_gate_proof_after_image_source_switch
    flow_set_var LAST_FAILED_CODE 2
    return 2
  fi
  flow_set_var CURRENT_STAGE_NAME basic_gate_proof_after_image_source_switch
  deploy_run_basic_gate_refresh
  deploy_verify_basic_gate_proof
}

deploy_validate_cli_options() {
  if [[ -n "$IMAGE_ARCHIVE_PATH" && "$DEPLOY_MODE" != "offline" ]]; then
    echo "[FAIL] --image-archive 仅在 --offline 下有效；在线模式请移除该参数，避免 basic gate proof 与离线镜像归档状态不一致。" >&2
    exit 2
  fi
  case "$RESUME_FROM" in
    post_deploy_acceptance|post_deploy_full_acceptance)
      if [[ "$START_SERVICES" != "1" || "$RUN_POST_DEPLOY_ACCEPTANCE" != "1" ]]; then
        echo "[FAIL] --resume-from $RESUME_FROM 必须执行部署后验收；不能与 --prepare-only 或 --skip-acceptance 同用。" >&2
        exit 2
      fi
      ;;
  esac
}


while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline)
      DEPLOY_MODE="offline"
      RUN_RELEASE_CHECK=0
      ;;
    --image-archive)
      [[ $# -ge 2 ]] || { echo "[FAIL] --image-archive 缺少路径参数" >&2; exit 2; }
      IMAGE_ARCHIVE_PATH="$2"
      shift
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo "[FAIL] --env-file 缺少路径参数" >&2; exit 2; }
      ENV_FILE_OVERRIDE="$2"
      shift
      ;;
    --prepare-only)
      START_SERVICES=0
      RUN_POST_DEPLOY_ACCEPTANCE=0
      ;;
    --resume-from)
      [[ $# -ge 2 ]] || { echo "[FAIL] --resume-from 缺少阶段名" >&2; exit 2; }
      RESUME_FROM="$2"
      shift
      ;;
    --explain)
      EXPLAIN_ONLY=1
      ;;
    --skip-release-check)
      RUN_RELEASE_CHECK=0
      ;;
    --strict-release-check)
      RUN_RELEASE_CHECK=1
      STRICT_RELEASE_CHECK=1
      ;;
    --skip-browser-verify)
      RUN_BROWSER_VERIFY=0
      ;;
    --require-basic-gate-proof)
      RUN_BASIC_GATE_REFRESH=0
      ;;
    --skip-acceptance)
      RUN_POST_DEPLOY_ACCEPTANCE=0
      ;;
    -h|--help)
      HELP_ONLY=1
      ;;
    *)
      deploy_refresh_flow_args
      flow_entry_handle_unknown_arg "one_click_deploy" "$1" deploy_flow_static_help_text \
        "$OPENCLAW_PYTHON_TOOL" setup flow deploy help-text "${DEPLOY_FLOW_ARGS[@]}"
      exit 2
      ;;
  esac
  shift
done

deploy_refresh_flow_args

if [[ "$HELP_ONLY" == "1" ]]; then
  flow_entry_run_dynamic_or_static_surface help deploy_flow_static_help_text deploy_flow_static_explain_text \
    "$OPENCLAW_PYTHON_TOOL" setup flow deploy help-text "${DEPLOY_FLOW_ARGS[@]}"
  exit 0
fi

if [[ "$EXPLAIN_ONLY" == "1" ]]; then
  flow_entry_run_dynamic_or_static_surface explain deploy_flow_static_help_text deploy_flow_static_explain_text \
    "$OPENCLAW_PYTHON_TOOL" setup flow deploy explain "${DEPLOY_FLOW_ARGS[@]}"
  exit 0
fi

deploy_validate_cli_options
deploy_reject_root_runtime_user
CURRENT_STAGE_NAME="repo_exec_permission_repair"
deploy_prime_summary_context_minimal_defaults
trap 'deploy_on_error $?' ERR
deploy_repair_repo_exec_bits
deploy_prime_summary_context

CURRENT_STAGE_NAME="control_plane_defaults"
deploy_load_control_plane_defaults
CURRENT_STAGE_NAME="basic_gate_proof_preflight"
deploy_verify_or_refresh_basic_gate_proof
CURRENT_STAGE_NAME="local_permission_preflight"
deploy_check_local_permission_prereqs
CURRENT_STAGE_NAME="control_plane_preflight"
flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE \
  control_plane_preflight deploy_run_control_plane_preflight_from_bootstrap

init_runtime_context
deploy_build_effective_stage_order
deploy_validate_resume_from
deploy_check_ingress_boundary_resume_guard

deploy_log_run_context

if [[ "$DEPLOY_MODE" == "offline" ]]; then
  deploy_resolve_offline_archives
fi

CURRENT_STAGE_NAME="deployment_image_readiness_preflight"
deploy_check_deployment_image_readiness_prereqs
CURRENT_STAGE_NAME="extension_env_preflight"
flow_run_logged_step "$LOG_PATH" CURRENT_STAGE_NAME LAST_FAILED_STEP LAST_FAILED_CODE \
  extension_env_preflight deploy_check_extension_env_prereqs
CURRENT_STAGE_NAME="effective_compose_render"
deploy_render_effective_compose
CURRENT_STAGE_NAME="runtime_bind_user_contract_preflight"
deploy_check_runtime_bind_user_contract_prereqs
CURRENT_STAGE_NAME="deploy_stage_runner"
deploy_run_effective_stages
CURRENT_STAGE_NAME="post_deploy_acceptance"
deploy_run_post_deploy_acceptance

if [[ "$START_SERVICES" == "1" ]]; then
  log "[OK] 一键部署完成。成功态摘要已写入 state/setup。"
else
  log "[OK] 准备阶段完成；后续动作已写入 state/setup 成功态摘要。"
fi

CURRENT_STAGE_NAME="deploy_success_summary"
deploy_write_summary success
CURRENT_STAGE_NAME="deploy_success_terminal_summary"
deploy_emit_terminal_summary success
