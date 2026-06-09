#!/usr/bin/env bash

if [[ -n "${OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_SH_LOADED=1

OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=repo_root.sh
source "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_LIB_DIR/repo_root.sh"
OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT="$(openclaw_repo_root_from "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_LIB_DIR")"
unset OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_LIB_DIR

# shellcheck source=./control_plane_config_paths.sh
source "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../runtime/runtime_target_lib.sh
source "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT/scripts/runtime/runtime_target_lib.sh"
# shellcheck source=../runtime/runtime_compose_lib.sh
source "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT/scripts/runtime/runtime_compose_lib.sh"
# shellcheck source=../runtime/runtime_docker_lib.sh
source "$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT/scripts/runtime/runtime_docker_lib.sh"

openclaw_scheduler_exec_fail() {
  echo "[$1][FAIL] $2" >&2
  exit "${3:-2}"
}

openclaw_scheduler_read_env_key() {
  openclaw_control_plane_read_env_key "$@"
}

openclaw_scheduler_apply_control_plane_selection_from_env_file() {
  local env_file="$1"
  local requested_config_var="$2"
  local profile_var="$3"
  local explicit_profile_var="$4"
  local env_label="${5:-deploy/.env}"
  openclaw_control_plane_apply_selection_from_env_file \
    "$env_file" \
    "$requested_config_var" \
    "$profile_var" \
    "$explicit_profile_var" \
    "$env_label" || openclaw_scheduler_exec_fail "scheduler_service_exec" "控制面配置选择无效：$env_label" 2
}

openclaw_scheduler_resolve_container_control_plane_config_path() {
  local root_dir="$1"
  local control_plane_profile="${2:-agent_platform}"
  local requested_config_path="${3:-}"
  local explicit_profile="${4:-0}"
  local host_config_path=""
  host_config_path="$(openclaw_control_plane_resolve_config_path "$control_plane_profile" "$requested_config_path" "$explicit_profile")" || return 1
  openclaw_control_plane_container_config_path "$control_plane_profile" "$host_config_path" || {
    openclaw_scheduler_exec_fail \
      "scheduler_service_exec" \
      "控制面配置不在仓库挂载内；请检查仓库挂载与正式配置选择（host=$host_config_path，default=$(openclaw_control_plane_container_profile_config_path "$control_plane_profile")）" \
      2
  }
}

openclaw_scheduler_prepare_service_exec() {
  local root_dir="$1"
  local env_file="$2"
  local compose_file="$3"
  local service_name="$4"
  local ensure_running_mode="$5"

  runtime_compose_require_cli >/dev/null || openclaw_scheduler_exec_fail "scheduler_service_exec" '未检测到 docker' 3
  [[ -f "$compose_file" ]] || openclaw_scheduler_exec_fail "scheduler_service_exec" "compose 文件不存在：$compose_file" 2
  [[ -f "$env_file" ]] || openclaw_scheduler_exec_fail "scheduler_service_exec" "环境文件不存在：$env_file" 2

  case "$ensure_running_mode" in
    up|strict) ;;
    *) openclaw_scheduler_exec_fail "scheduler_service_exec" "不支持的 --ensure-running：$ensure_running_mode" 2 ;;
  esac

  if [[ "$ensure_running_mode" == "up" ]]; then
    runtime_compose_up_services "$env_file" "$compose_file" "$service_name" >/dev/null
    return 0
  fi

  local container_id=""
  container_id="$(runtime_compose_service_container_id "$env_file" "$compose_file" "$service_name")"
  [[ -n "$container_id" ]] || openclaw_scheduler_exec_fail \
    "scheduler_service_exec" \
    'scheduler target 尚未创建；请先执行 bash ./scripts/runtime/run_runtime_service_action.sh up --target scheduler' \
    10
  [[ "$(runtime_docker_container_running_bool "$container_id" 2>/dev/null || true)" == "true" ]] || openclaw_scheduler_exec_fail \
    "scheduler_service_exec" \
    "scheduler target 未运行；当前模式 strict 不会自动启动：$service_name" \
    11
}

openclaw_scheduler_run_target_operation() {
  local root_dir="$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT"
  local compose_file=""
  local env_file=""
  local scheduler_target="scheduler"
  local target_binding_ref=""
  local dispatch_target_id=""
  local operation=""
  local ensure_running_mode="up"
  local control_plane_profile="agent_platform"
  local control_plane_profile_explicit=0
  local requested_config_path=""
  local passthrough_args=()
  local target_inline=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root-dir)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--root-dir 缺少参数' 2
        root_dir="$2"
        shift 2
        ;;
      --compose-file)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--compose-file 缺少参数' 2
        compose_file="$2"
        shift 2
        ;;
      --env-file)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--env-file 缺少参数' 2
        env_file="$2"
        shift 2
        ;;
      --ensure-running)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--ensure-running 缺少参数' 2
        ensure_running_mode="$2"
        shift 2
        ;;
      --config-path)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--config-path 缺少参数' 2
        requested_config_path="$2"
        shift 2
        ;;
      --control-plane-profile)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--control-plane-profile 缺少参数' 2
        control_plane_profile="$2"
        control_plane_profile_explicit=1
        shift 2
        ;;
      --target-binding-ref)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--target-binding-ref 缺少参数' 2
        target_binding_ref="$2"
        shift 2
        ;;
      --target)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--target 缺少参数' 2
        [[ -z "$dispatch_target_id" || "$dispatch_target_id" == "$2" ]] || openclaw_scheduler_exec_fail "run_target_operation" '--target 只能指定一次' 2
        dispatch_target_id="$2"
        shift 2
        ;;
      --target=*)
        target_inline="${1#--target=}"
        [[ -n "$target_inline" ]] || openclaw_scheduler_exec_fail "run_target_operation" '--target 缺少参数' 2
        [[ -z "$dispatch_target_id" || "$dispatch_target_id" == "$target_inline" ]] || openclaw_scheduler_exec_fail "run_target_operation" '--target 只能指定一次' 2
        dispatch_target_id="$target_inline"
        shift
        ;;
      --operation)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "run_target_operation" '--operation 缺少参数' 2
        operation="$2"
        shift 2
        ;;
      --)
        shift
        passthrough_args=("$@")
        break
        ;;
      -h|--help)
        cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation \
    [--config-path <path>] [--control-plane-profile <profile_id>] \
    [--target-binding-ref <target_binding_ref>] [--target <target_id>] \
    --operation <operation> \
    [--root-dir <path>] [--compose-file <path>] [--env-file <path>] [--ensure-running <up|strict>] [-- <extra args...>]

说明：
  - 正式人工入口：统一经 scheduler 服务容器执行 target contract 中已注册的 operation。
  - `--target-binding-ref` 显式指定 control plane registry 里的 targetBindingRef。
  - `--target <target_id>` 作为 dispatch 场景短写：会同时透传给真实 dispatcher/runtime 的 `--target`，
    并在未显式指定 `--target-binding-ref` 时作为 `--dispatch-target-id` 交给 scheduler 容器内 control plane CLI 解析。
  - 未显式传入 `--config-path` / `--control-plane-profile` 时，入口从 `--env-file` 指向的 deploy env 读取 active profile 与配置路径。
  - `--ensure-running strict` 只复用当前已运行容器，不会隐式启动。
USAGE
        return 0
        ;;
      *)
        passthrough_args=("$@")
        break
        ;;
    esac
  done

  root_dir="$(cd "$root_dir" && pwd)"
  compose_file="${compose_file:-$root_dir/deploy/docker-compose.yml}"
  env_file="${env_file:-$root_dir/deploy/.env}"
  [[ -n "$operation" ]] || openclaw_scheduler_exec_fail "run_target_operation" '缺少 --operation' 2
  openclaw_scheduler_apply_control_plane_selection_from_env_file \
    "$env_file" \
    requested_config_path \
    control_plane_profile \
    control_plane_profile_explicit

  if [[ -n "$dispatch_target_id" ]]; then
    if ((${#passthrough_args[@]} > 0)); then
      passthrough_args=(--target "$dispatch_target_id" "${passthrough_args[@]}")
    else
      passthrough_args=(--target "$dispatch_target_id")
    fi
  fi

  local service_name=""
  service_name="$(runtime_target_service_name_for_target "$scheduler_target")" || openclaw_scheduler_exec_fail "run_target_operation" '未定义 scheduler target 对应 compose service' 2
  local container_config_path=""
  container_config_path="$(openclaw_scheduler_resolve_container_control_plane_config_path "$root_dir" "$control_plane_profile" "$requested_config_path" "$control_plane_profile_explicit")"
  openclaw_scheduler_prepare_service_exec "$root_dir" "$env_file" "$compose_file" "$service_name" "$ensure_running_mode"

  local -a cli_args=(
    control-plane
    runtime
    run-target-operation
    --operation "$operation"
  )
  if [[ -n "$target_binding_ref" ]]; then
    cli_args+=(--target-binding-ref "$target_binding_ref")
  elif [[ -n "$dispatch_target_id" ]]; then
    cli_args+=(--dispatch-target-id "$dispatch_target_id")
  fi
  if ((${#passthrough_args[@]} > 0)); then
    cli_args+=(-- "${passthrough_args[@]}")
  fi

  runtime_compose_exec_service \
    "$env_file" \
    "$compose_file" \
    "$service_name" \
    env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$container_config_path" \
    "OPENCLAW_RUNTIME_PATH_VIEW=scheduler" \
    /opt/openclaw-tools/scripts/runtime/container_openclaw_cli \
    "${cli_args[@]}"
  return $?
}

openclaw_scheduler_run_agent_runtime() {
  local root_dir="$OPENCLAW_CONTROL_PLANE_SCHEDULER_EXEC_ROOT"
  local compose_file=""
  local env_file=""
  local scheduler_target="scheduler"
  local agent_ref=""
  local ensure_running_mode="up"
  local control_plane_profile="agent_platform"
  local control_plane_profile_explicit=0
  local requested_config_path=""
  local passthrough_args=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root-dir)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--root-dir 缺少参数' 2
        root_dir="$2"
        shift 2
        ;;
      --compose-file)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--compose-file 缺少参数' 2
        compose_file="$2"
        shift 2
        ;;
      --env-file)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--env-file 缺少参数' 2
        env_file="$2"
        shift 2
        ;;
      --ensure-running)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--ensure-running 缺少参数' 2
        ensure_running_mode="$2"
        shift 2
        ;;
      --config-path)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--config-path 缺少参数' 2
        requested_config_path="$2"
        shift 2
        ;;
      --control-plane-profile)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--control-plane-profile 缺少参数' 2
        control_plane_profile="$2"
        control_plane_profile_explicit=1
        shift 2
        ;;
      --agent-ref)
        [[ $# -ge 2 ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '--agent-ref 缺少参数' 2
        agent_ref="$2"
        shift 2
        ;;
      --)
        shift
        passthrough_args=("$@")
        break
        ;;
      -h|--help)
        cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime scheduler-run-agent-runtime \
    --agent-ref <agent_ref> \
    [--config-path <path>] [--control-plane-profile <profile_id>] \
    [--root-dir <path>] [--compose-file <path>] [--env-file <path>] [--ensure-running <up|strict>] [-- <extra args...>]

说明：
  - 正式人工入口：统一经 scheduler 服务容器执行 runtime adapter 注册表与 implementation runtime 绑定解析出的 agent runtime。
  - 未显式传入 `--config-path` / `--control-plane-profile` 时，入口从 `--env-file` 指向的 deploy env 读取 active profile 与配置路径。
  - `--ensure-running strict` 只允许复用当前已运行容器，不会隐式启动。
  - 额外参数会原样透传给 agent runtime。
USAGE
        return 0
        ;;
      *)
        passthrough_args=("$@")
        break
        ;;
    esac
  done

  root_dir="$(cd "$root_dir" && pwd)"
  compose_file="${compose_file:-$root_dir/deploy/docker-compose.yml}"
  env_file="${env_file:-$root_dir/deploy/.env}"
  [[ -n "$agent_ref" ]] || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '缺少 --agent-ref' 2
  openclaw_scheduler_apply_control_plane_selection_from_env_file \
    "$env_file" \
    requested_config_path \
    control_plane_profile \
    control_plane_profile_explicit

  local service_name=""
  service_name="$(runtime_target_service_name_for_target "$scheduler_target")" || openclaw_scheduler_exec_fail "scheduler_run_agent_runtime" '未定义 scheduler target 对应 compose service' 2
  local container_config_path=""
  container_config_path="$(openclaw_scheduler_resolve_container_control_plane_config_path "$root_dir" "$control_plane_profile" "$requested_config_path" "$control_plane_profile_explicit")"
  openclaw_scheduler_prepare_service_exec "$root_dir" "$env_file" "$compose_file" "$service_name" "$ensure_running_mode"

  local -a cli_args=(
    control-plane
    runtime
    run-agent-runtime
    --agent-ref "$agent_ref"
  )
  if ((${#passthrough_args[@]} > 0)); then
    cli_args+=(-- "${passthrough_args[@]}")
  fi

  runtime_compose_exec_service \
    "$env_file" \
    "$compose_file" \
    "$service_name" \
    env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$container_config_path" \
    "OPENCLAW_RUNTIME_PATH_VIEW=scheduler" \
    /opt/openclaw-tools/scripts/runtime/container_openclaw_cli \
    "${cli_args[@]}"
  return $?
}
