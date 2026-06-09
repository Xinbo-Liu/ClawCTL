#!/usr/bin/env bash
# 用途：从 deploy stage flow 真源读取 one_click_deploy 阶段执行映射；主 runner 只负责调度与日志执行。
set -euo pipefail

DEPLOY_STAGE_REGISTRY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root_bootstrap.sh
source "$DEPLOY_STAGE_REGISTRY_LIB_DIR/repo_root_bootstrap.sh"
openclaw_setup_lib_source_repo_root "$DEPLOY_STAGE_REGISTRY_LIB_DIR" || return 2 2>/dev/null || exit 2
unset -f openclaw_setup_lib_source_repo_root
DEPLOY_STAGE_REGISTRY_ROOT="$(openclaw_repo_root_from "$DEPLOY_STAGE_REGISTRY_LIB_DIR")"
# shellcheck source=scripts/lib/repo_contracts.sh
source "$DEPLOY_STAGE_REGISTRY_ROOT/scripts/lib/repo_contracts.sh"
unset DEPLOY_STAGE_REGISTRY_LIB_DIR
repo_contract_assign_path DEPLOY_STAGE_REGISTRY_PATH governance.deploy_stage_flow

declare -gA DEPLOY_STAGE_SCRIPT_MAP=()
declare -gA DEPLOY_STAGE_ARG_MODE_MAP=()
declare -gA DEPLOY_STAGE_CUSTOM_HANDLER_MAP=()
DEPLOY_STAGE_REGISTRY_INITIALIZED=0

# 检测当前环境是否提供 jq。
deploy_stage_registry_jq_available() {
  command -v jq >/dev/null 2>&1
}

# 从 deploy_stage_flow 真源加载阶段执行映射。
deploy_stage_registry_init() {
  if [[ "$DEPLOY_STAGE_REGISTRY_INITIALIZED" == "1" ]]; then
    return 0
  fi
  DEPLOY_STAGE_SCRIPT_MAP=()
  DEPLOY_STAGE_ARG_MODE_MAP=()
  DEPLOY_STAGE_CUSTOM_HANDLER_MAP=()
  if ! deploy_stage_registry_jq_available || [[ ! -f "$DEPLOY_STAGE_REGISTRY_PATH" ]]; then
    echo '[deploy_stage_registry] 缺少 jq 或 deploy_stage_flow 真源' >&2
    return 2
  fi

  local spec='' stage='' kind='' script_rel_path='' arg_mode='' handler=''
  while IFS= read -r spec; do
    [[ -n "$spec" ]] || continue
    stage="$(jq -r '.stage' <<<"$spec")"
    kind="$(jq -r '.kind // ""' <<<"$spec")"
    script_rel_path="$(jq -r '.script_rel_path // ""' <<<"$spec")"
    arg_mode="$(jq -r '.arg_mode // ""' <<<"$spec")"
    handler="$(jq -r '.handler // ""' <<<"$spec")"
    case "$kind" in
      '')
        continue
        ;;
      script)
        [[ -n "$script_rel_path" ]] || {
          echo "[deploy_stage_registry] 阶段 $stage 缺少 execution.script_rel_path" >&2
          return 2
        }
        DEPLOY_STAGE_SCRIPT_MAP["$stage"]="$ROOT_DIR/$script_rel_path"
        ;;
      arg_mode_only)
        ;;
      custom_handler)
        [[ -n "$handler" ]] || {
          echo "[deploy_stage_registry] 阶段 $stage 缺少 execution.handler" >&2
          return 2
        }
        DEPLOY_STAGE_CUSTOM_HANDLER_MAP["$stage"]="$handler"
        ;;
      *)
        echo "[deploy_stage_registry] 阶段 $stage 存在未知 execution.kind：$kind" >&2
        return 2
        ;;
    esac
    [[ -n "$arg_mode" ]] && DEPLOY_STAGE_ARG_MODE_MAP["$stage"]="$arg_mode"
  done < <(
    jq -c '.stages | to_entries[] | {
      stage: .key,
      kind: (.value.execution.kind // ""),
      script_rel_path: (.value.execution.script_rel_path // ""),
      arg_mode: (.value.execution.arg_mode // ""),
      handler: (.value.execution.handler // "")
    }' "$DEPLOY_STAGE_REGISTRY_PATH"
  )
  DEPLOY_STAGE_REGISTRY_INITIALIZED=1
}

# 确认阶段注册表已经完成初始化。
deploy_stage_assert_registry_ready() {
  [[ "${#DEPLOY_STAGE_SCRIPT_MAP[@]}" -gt 0 || "${#DEPLOY_STAGE_CUSTOM_HANDLER_MAP[@]}" -gt 0 ]] || {
    echo '[deploy_stage_registry] 阶段真源尚未初始化' >&2
    return 2
  }
}

# 按阶段参数模式补齐命令参数。
deploy_stage_append_mode_args() {
  local stage="$1"
  local mode="${DEPLOY_STAGE_ARG_MODE_MAP[$stage]:-}"
  case "$mode" in
    '') ;;
    env-file)
      DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE")
      ;;
    env-file-compose-file)
      DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE")
      ;;
    env-file-compose-file-require-local)
      DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE" --require-local)
      ;;
    env-file-required-nginx-policy)
      DEPLOY_STAGE_COMMAND+=(--env-file "$ENV_FILE" --compose-file "$COMPOSE_FILE" --require-nginx-policy --no-write)
      ;;
    gateway-ingress-render)
      DEPLOY_STAGE_COMMAND+=(setup ingress render-nginx --env-file "$ENV_FILE")
      ;;
    image-archive)
      DEPLOY_STAGE_COMMAND+=("$IMAGE_ARCHIVE_PATH")
      ;;
    compose-config)
      DEPLOY_STAGE_COMMAND=(bash "$ROOT_DIR/scripts/runtime/show_runtime_compose_config.sh" --compose-file "$COMPOSE_FILE" --env-file "$ENV_FILE")
      ;;
    runtime-up)
      DEPLOY_STAGE_COMMAND=(bash "$ROOT_DIR/scripts/runtime/run_runtime_service_action.sh" up --all --force-recreate --compose-file "$COMPOSE_FILE" --env-file "$ENV_FILE")
      ;;
    runtime-ps)
      DEPLOY_STAGE_COMMAND=(bash "$ROOT_DIR/scripts/runtime/show_runtime_service_status.sh" --compose-file "$COMPOSE_FILE" --env-file "$ENV_FILE")
      ;;
    *)
      echo "[deploy_stage_registry] 未知参数模式：$mode" >&2
      return 2
      ;;
  esac
}

declare -ga DEPLOY_STAGE_COMMAND=()

# 根据阶段定义组装最终可执行命令数组。
deploy_stage_prepare_command() {
  local stage="$1"
  deploy_stage_assert_registry_ready
  local script_path="${DEPLOY_STAGE_SCRIPT_MAP[$stage]:-}"
  if [[ -n "$script_path" ]]; then
    DEPLOY_STAGE_COMMAND=(bash "$script_path")
    deploy_stage_append_mode_args "$stage"
    return 0
  fi
  local handler="${DEPLOY_STAGE_CUSTOM_HANDLER_MAP[$stage]:-}"
  if [[ -n "$handler" ]]; then
    DEPLOY_STAGE_COMMAND=("$handler")
    return 0
  fi
  if [[ -n "${DEPLOY_STAGE_ARG_MODE_MAP[$stage]:-}" ]]; then
    DEPLOY_STAGE_COMMAND=()
    deploy_stage_append_mode_args "$stage"
    return 0
  fi
  echo "[deploy_stage_registry] 未知阶段：$stage" >&2
  return 2
}
