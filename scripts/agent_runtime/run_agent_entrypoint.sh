#!/usr/bin/env bash
set -euo pipefail

# 用途：统一收口 managed explicit extension `agent/extensions/*/agent/modules/*/bin/*`
# 的宿主机/容器执行入口，并固定通过 control-plane runtime 统一执行。

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
CONTAINER_CLI_PATH="/opt/openclaw-tools/scripts/runtime/container_openclaw_cli"
CONTAINER_PYTHON_PATH="/opt/openclaw-tools/scripts/runtime/container_python"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"

AGENT_REF="${1:-}"
if [[ -z "$AGENT_REF" ]]; then
  echo "[run_agent_entrypoint][FAIL] 缺少 agent 标识" >&2
  exit 2
fi
shift || true

resolve_host_agent_config_path() {
  if [[ -n "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" || -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]]; then
    openclaw_control_plane_resolve_config_path agent_platform
    return 0
  fi
  openclaw_control_plane_agent_config_path "$AGENT_REF"
}

resolve_container_agent_config_path() {
  if [[ -n "${OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH:-}" || -n "${OPENCLAW_CONTROL_PLANE_PROFILE:-}" ]]; then
    openclaw_control_plane_container_config_path agent_platform
    return 0
  fi
  "$CONTAINER_PYTHON_PATH" -m openclaw.lib.repo.control_plane_config_surface \
    agent-host-path \
    --agent-ref "$AGENT_REF" \
    --repo-root /opt/openclaw-tools
}

if [[ -x "$CONTAINER_CLI_PATH" ]]; then
  RESOLVED_CONFIG_PATH="$(resolve_container_agent_config_path)"
  exec env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" \
    "$CONTAINER_CLI_PATH" control-plane runtime run-agent-runtime --agent-ref "$AGENT_REF" -- "$@"
fi

RESOLVED_CONFIG_PATH="$(resolve_host_agent_config_path)"
exec env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$RESOLVED_CONFIG_PATH" \
  bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" control-plane runtime scheduler-run-agent-runtime --config-path "$RESOLVED_CONFIG_PATH" --agent-ref "$AGENT_REF" -- "$@"
