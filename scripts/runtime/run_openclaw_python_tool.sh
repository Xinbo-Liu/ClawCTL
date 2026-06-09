#!/usr/bin/env bash
# 用途：通过共享容器运行器执行仓库 Python CLI 命令。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
PYTHON_RUNNER="$ROOT_DIR/scripts/runtime/run_python_container.sh"
# shellcheck source=../lib/repo_python_env.sh
source "$ROOT_DIR/scripts/lib/repo_python_env.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../lib/control_plane_scheduler_exec.sh
source "$ROOT_DIR/scripts/lib/control_plane_scheduler_exec.sh"

runner_env_args() {
  local -a args=()
  local -A seen=()
  local name=""
  local extra_env_tokens=""
  local token=""
  for name in OPENCLAW_STATE_DIR HOST_STATE_DIR OPENCLAW_RUNTIME_PATH_VIEW; do
    [[ -n "${!name:-}" ]] || continue
    args+=(--env "$name=${!name}")
    seen["$name"]=1
  done

  extra_env_tokens="${OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS:-}"
  extra_env_tokens="${extra_env_tokens//,/ }"
  extra_env_tokens="${extra_env_tokens//;/ }"
  for token in $extra_env_tokens; do
    name="$token"
    [[ "$name" =~ ^[A-Z0-9_]+$ ]] || continue
    [[ -n "${!name:-}" ]] || continue
    [[ -n "${seen[$name]:-}" ]] && continue
    args+=(--env "$name=${!name}")
    seen["$name"]=1
  done

  ((${#args[@]} > 0)) || return 0
  printf '%s\0' "${args[@]}"
}

runner_mount_args() {
  local -a args=()
  local runtime_view="${OPENCLAW_RUNTIME_PATH_VIEW:-}"
  local scheduler_state_host_dir="${OPENCLAW_SCHEDULER_STATE_HOST_DIR:-}"
  local scheduler_state_container_dir="${OPENCLAW_STATE_DIR:-/home/openclaw/.openclaw}"

  if [[ "$runtime_view" == 'scheduler' && -n "$scheduler_state_host_dir" ]]; then
    args+=(--mount-to "$scheduler_state_host_dir" "$scheduler_state_container_dir")
  fi

  ((${#args[@]} > 0)) || return 0
  printf '%s\0' "${args[@]}"
}

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/runtime/run_openclaw_python_tool.sh <cli-subcommand> [args...]

说明：
  - 统一通过容器化 Python 执行仓库内的 openclaw.cli 子命令；
  - scheduler 服务容器语义的正式人工入口统一为：
      bash ./scripts/runtime/run_openclaw_python_tool.sh dispatch ops run-target-operation ...
      bash ./scripts/runtime/run_openclaw_python_tool.sh control-plane runtime scheduler-run-agent-runtime ...
  - 入口不会自动准备控制面镜像，请先显式执行 bash ./scripts/setup/prepare_control_plane_medium.sh；
  - 入口会先注入仓库级 Python 环境，确保容器内优先加载当前仓库源码；
  - 额外自动透传仅保留 OPENCLAW_STATE_DIR、HOST_STATE_DIR、OPENCLAW_RUNTIME_PATH_VIEW；
  - 如需额外透传环境变量，请设置 OPENCLAW_PYTHON_TOOL_EXTRA_ENV_VARS="VAR_A VAR_B"。
USAGE
}

if [[ "${1:-}" == 'dispatch' && "${2:-}" == 'ops' && "${3:-}" == 'run-target-operation' ]]; then
  shift 3
  openclaw_scheduler_run_target_operation "$@"
  exit $?
fi

if [[ "${1:-}" == 'control-plane' && "${2:-}" == 'runtime' && "${3:-}" == 'scheduler-run-agent-runtime' ]]; then
  shift 3
  openclaw_scheduler_run_agent_runtime "$@"
  exit $?
fi

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
  esac
fi

RUNNER_ENV_ARGS=()
while IFS= read -r -d '' item; do
  RUNNER_ENV_ARGS+=("$item")
done < <(runner_env_args)

RUNNER_MOUNT_ARGS=()
while IFS= read -r -d '' item; do
  RUNNER_MOUNT_ARGS+=("$item")
done < <(runner_mount_args)

REPO_PYTHON_ENV_ARGS=()
while IFS= read -r -d '' item; do
  REPO_PYTHON_ENV_ARGS+=("$item")
done < <(openclaw_repo_python_env_args "$ROOT_DIR")

RUNNER_CONTROL_PLANE_CONFIG_PATH=""
RUNNER_CONTROL_PLANE_PROFILE="agent_platform"
RUNNER_CONTROL_PLANE_PROFILE_EXPLICIT=0
# 普通容器化 Python 入口也必须使用当前部署选择；否则 sync-compose 等治理命令
# 会在宿主机与容器内按不同 profile 渲染，误删扩展 runtime service。
openclaw_control_plane_apply_default_selection_from_env_files \
  RUNNER_CONTROL_PLANE_CONFIG_PATH \
  RUNNER_CONTROL_PLANE_PROFILE \
  RUNNER_CONTROL_PLANE_PROFILE_EXPLICIT \
  "$ROOT_DIR/deploy/.env|deploy/.env" \
  "$ROOT_DIR/deploy/site.env|deploy/site.env"
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path "$RUNNER_CONTROL_PLANE_PROFILE" "$RUNNER_CONTROL_PLANE_CONFIG_PATH" "$RUNNER_CONTROL_PLANE_PROFILE_EXPLICIT")"

exec bash "$PYTHON_RUNNER" \
  --workdir "$ROOT_DIR" \
  "${RUNNER_MOUNT_ARGS[@]+"${RUNNER_MOUNT_ARGS[@]}"}" \
  "${REPO_PYTHON_ENV_ARGS[@]+"${REPO_PYTHON_ENV_ARGS[@]}"}" \
  "${RUNNER_ENV_ARGS[@]+"${RUNNER_ENV_ARGS[@]}"}" \
  --env "OPENCLAW_REPO_ROOT=$ROOT_DIR" \
  --env "OPENCLAW_TOOLS_ROOT=$ROOT_DIR" \
  --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" \
  -- -c 'from openclaw import cli; raise SystemExit(cli.main())' "$@"
