#!/usr/bin/env bash
# 用途：初始化当前唯一运行路径的运行态目录、渲染路径索引并收口权限。
set -euo pipefail
__openclaw_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=../lib/repo_root.sh
source "$__openclaw_script_dir/../lib/repo_root.sh"
ROOT_DIR="$(openclaw_repo_root_from "$__openclaw_script_dir")"
unset __openclaw_script_dir

bootstrap_fail() {
  echo "[bootstrap][FAIL] $*" >&2
  exit 2
}

bootstrap_usage() {
  cat <<'USAGE'
用法：
  bash ./scripts/setup/bootstrap.sh

说明：
  bootstrap 负责生成当前唯一实现路径的运行态目录，并完成以下动作：
  1) 按 runtime_paths 真源与 compose 挂载自动预创建宿主机运行态目录
  2) 渲染 runtime_paths 路径索引
  3) 校验 dispatch target 注册表
  4) 渲染 dispatch runtime targets.json
  5) 生成 gateway local_ro 最小镜像目录
  6) 收口当前运行态生成产物、脚本 shebang 与证书权限

前提：
  - deploy/.env 已生成，且当前部署用户可读
  - 当前 host state 目录由当前部署用户可管理
  - 当前脚本不会自动 sudo；以 root 执行时必须能解析 OPENCLAW_RUNTIME_UID/GID，解析失败会中止
USAGE
}

source "$ROOT_DIR/scripts/setup/lib/runtime_permissions.sh"
# shellcheck source=../lib/control_plane_config_paths.sh
source "$ROOT_DIR/scripts/lib/control_plane_config_paths.sh"
# shellcheck source=../lib/repo_contracts.sh
source "$ROOT_DIR/scripts/lib/repo_contracts.sh"
repo_contract_assign_relpath GATEWAY_READONLY_MANIFEST_REL_PATH gateway.readonly_manifest

bootstrap_deploy_env_value() {
  local key="$1"
  awk -F= -v expected="$key" '
    $0 ~ /^[[:space:]]*#/ { next }
    $0 !~ /=/ { next }
    {
      name = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == expected) {
        value = substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
      }
    }
  ' "$ROOT_DIR/deploy/.env" | tail -n 1
}

bootstrap_export_runtime_config_selection() {
  local selected_config_path=""
  local selected_profile=""
  selected_profile="$(bootstrap_deploy_env_value OPENCLAW_CONTROL_PLANE_PROFILE)"
  selected_config_path="$(bootstrap_deploy_env_value OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH)"
  [[ -n "$selected_profile" ]] || bootstrap_fail "deploy/.env 缺少 OPENCLAW_CONTROL_PLANE_PROFILE；请先重新执行 one_click_config.sh"
  [[ -n "$selected_config_path" ]] || bootstrap_fail "deploy/.env 缺少 OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH；请先重新执行 one_click_config.sh"
  selected_config_path="$(openclaw_control_plane_resolve_config_path "$selected_profile" "$selected_config_path" 1)" \
    || bootstrap_fail "无法解析控制面配置路径：profile=$selected_profile path=$selected_config_path"
  export OPENCLAW_CONTROL_PLANE_PROFILE="$selected_profile"
  export OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH="$selected_config_path"
}

bootstrap_check_local_permission_prereqs() {
  local host_state_dir=""
  host_state_dir="$(runtime_permissions_host_state_root "$ROOT_DIR")"
  runtime_permissions_assert_access_mode "$ROOT_DIR" rx "仓库根目录" || bootstrap_fail "仓库根目录缺少读取/执行权限：$ROOT_DIR"
  [[ -f "$ROOT_DIR/deploy/.env" ]] || bootstrap_fail "缺少 deploy/.env；请先执行 bash ./scripts/setup/one_click_config.sh"
  runtime_permissions_assert_access_mode "$ROOT_DIR/deploy/.env" r "deploy/.env" || bootstrap_fail "deploy/.env 不可读：$ROOT_DIR/deploy/.env"
  runtime_permissions_assert_dir_manageable_or_creatable "$ROOT_DIR/state" "state 目录" || bootstrap_fail "state 目录不可管理：$ROOT_DIR/state"
  runtime_permissions_assert_dir_manageable_or_creatable "$host_state_dir" "运行态 state 目录" || bootstrap_fail "运行态 state 目录不可管理：$host_state_dir"
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      bootstrap_usage
      exit 0
      ;;
    *)
      echo "[bootstrap][FAIL] 当前脚本不接受参数：$1" >&2
      echo "请执行：bash ./scripts/setup/bootstrap.sh --help" >&2
      exit 2
      ;;
  esac
fi

bootstrap_check_local_permission_prereqs
runtime_permissions_assert_root_runtime_uid_gid_resolvable "$ROOT_DIR"
bootstrap_export_runtime_config_selection
runtime_permissions_prepare_repo_support_dirs "$ROOT_DIR"
runtime_permissions_prepare_openclaw_state_layout "$ROOT_DIR"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" runtime paths render-generated \
  --repo-root "$ROOT_DIR" \
  --config-path "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH"
bash "$ROOT_DIR/scripts/runtime/run_openclaw_python_tool.sh" setup env bootstrap-runtime \
  --repo-root "$ROOT_DIR" \
  --env-file "$ROOT_DIR/deploy/.env" \
  --config-path "$OPENCLAW_CONTROL_PLANE_SERVICE_CONFIG_PATH" \
  --dispatch-output "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" dispatch/targets.json)" \
  --dispatch-registry-summary-json "$(runtime_permissions_host_state_file "$ROOT_DIR" setup/dispatch_target_registry_summary.json)" \
  --dispatch-summary-json "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" setup/dispatch_runtime_summary.json)" \
  --scheduler-output "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" runtime.scheduler.app.env)" \
  --internal-api-output "$(runtime_permissions_host_control_plane_file "$ROOT_DIR" runtime.internal-api.app.env)" \
  --internal-api-bind 0.0.0.0 \
  --gateway-readonly-manifest "$ROOT_DIR/$GATEWAY_READONLY_MANIFEST_REL_PATH" \
  --gateway-local-ro-output "$(runtime_permissions_host_gateway_file "$ROOT_DIR" local_ro_gateway)"
runtime_permissions_harden_bootstrap_outputs "$ROOT_DIR"
runtime_permissions_harden_deploy_inputs "$ROOT_DIR"
runtime_permissions_mark_shebang_executable "$ROOT_DIR"
runtime_permissions_harden_certs "$ROOT_DIR"
runtime_permissions_prepare_ingress_cap_drop_mount_access "$ROOT_DIR"
