#!/usr/bin/env bash
# 用途：统一校验 generated docs 是否已与现行规格同步。
set -euo pipefail

__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir
STATIC_PYTHON_RUNNER="$ROOT_DIR/scripts/lib/run_static_python.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
REQUESTED_CONFIG_PATH=""
PROFILE_ID="agent_platform"
EXPLICIT_PROFILE="0"
RESOLVED_CONFIG_PATH=""
CANONICAL_RESOLVED_CONFIG_PATH=""

usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/docs/check_generated_docs_sync.sh
  bash ./scripts/docs/check_generated_docs_sync.sh --config-path <control-plane-config-path>

说明：
  - `--help` 可离线查看；真正执行属于 Docker 必需的生成文档同步检查；
  统一执行以下生成文档同步检查：
    1. docs/getting-started/quickstart.md 与 docs/getting-started/environment-setup.md
    2. docs/getting-started/deployment-inputs.md
    3. deploy/site.env.example
    4. docs/operations/runtime-service-reference.md
    5. docs/operations/maintenance-map.md
    6. scripts/README.md
    7. config/workspace_templates/*/USER.md 自动生成段

边界：
  - 只检查仓库生成文档是否已与现行规格同步；
  - runtime-service-reference、getting-started、maintenance-map 与 scripts/README 是 canonical 文档，固定按 agent_platform 检查；
  - deployment-inputs 按当前 active profile 或显式 --config-path 检查，用于覆盖启用扩展带来的输入项；
  - deploy/site.env.example 是平台默认部署输入模板，固定按当前默认 schema 检查；
  - docs_registry、reference layout 与 CentOS 7 实机验收链由各自门禁检查。
USAGE
}

run_python_module_for_config() {
  local config_path="$1"
  local module_name="$2"
  shift 2
  OPENCLAW_STATIC_PYTHON_READINESS_LABEL='generated docs sync' \
    bash "$STATIC_PYTHON_RUNNER" --workdir "$ROOT_DIR" --env "OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH=$config_path" -- -m "$module_name" "$@"
}

run_check() {
  local label="$1"
  local module_name="$2"
  shift 2
  echo "[generated-docs-sync] $label"
  if [[ "$#" -gt 0 ]]; then
    run_python_module_for_config "$RESOLVED_CONFIG_PATH" "$module_name" "$@"
  else
    run_python_module_for_config "$RESOLVED_CONFIG_PATH" "$module_name" --check
  fi
}

run_canonical_check() {
  local label="$1"
  local module_name="$2"
  shift 2
  echo "[generated-docs-sync] $label"
  if [[ "$#" -gt 0 ]]; then
    run_python_module_for_config "$CANONICAL_RESOLVED_CONFIG_PATH" "$module_name" "$@"
  else
    run_python_module_for_config "$CANONICAL_RESOLVED_CONFIG_PATH" "$module_name" --check
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --config-path)
      [[ $# -ge 2 ]] || { echo '[generated-docs-sync][FAIL] --config-path 缺少路径参数' >&2; usage >&2; exit 2; }
      REQUESTED_CONFIG_PATH="$2"
      shift 2
      ;;
    *)
      echo "[generated-docs-sync][FAIL] 未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$REQUESTED_CONFIG_PATH" ]]; then
  openclaw_control_plane_apply_default_selection_from_env_files \
    REQUESTED_CONFIG_PATH \
    PROFILE_ID \
    EXPLICIT_PROFILE \
    "$ROOT_DIR/deploy/.env|deploy/.env" \
    "$ROOT_DIR/deploy/site.env|deploy/site.env"
fi
RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path "$PROFILE_ID" "$REQUESTED_CONFIG_PATH" "$EXPLICIT_PROFILE")"
CANONICAL_RESOLVED_CONFIG_PATH="$(openclaw_control_plane_resolve_config_path agent_platform "" 1)"
CONFIG_ARGS=(--config-path "$RESOLVED_CONFIG_PATH")
CANONICAL_CONFIG_ARGS=(--control-plane-profile agent_platform)

run_canonical_check 'getting_started_reference' 'openclaw.docs.renderers.getting_started' --check "${CANONICAL_CONFIG_ARGS[@]}"
run_check 'deployment_inputs_reference' 'openclaw.setup.deploy_env.control_plane' docs render-deployment-inputs --check "${CONFIG_ARGS[@]}"
run_canonical_check 'site_env_example_reference' 'openclaw.setup.deploy_env.control_plane' docs render-site-env-example --check
run_canonical_check 'runtime_surface_reference' 'openclaw.docs.renderers.runtime_surface' --check "${CANONICAL_CONFIG_ARGS[@]}"
run_canonical_check 'maintenance_map_reference' 'openclaw.docs.renderers.maintenance_map' --check "${CANONICAL_CONFIG_ARGS[@]}"
run_canonical_check 'script_catalog_reference' 'openclaw.docs.renderers.script_catalog' --check "${CANONICAL_CONFIG_ARGS[@]}"
run_canonical_check 'workspace_user_sections' 'openclaw.docs.renderers.workspace_user_sections' --check

echo '[generated-docs-sync] 已通过'
